"""The command line for QE inputs: `vwf structure`, `vwf pseudos get|list`,
`vwf project add-qe-chain`."""

import ase.io
import pytest
from ase.build import bulk, mx2

from fakes import pw_input, upf
from test_pseudodojo import FakeWeb
from visual_workflows import cli
from visual_workflows.core import pseudodojo
from visual_workflows.core.chain import check_chain
from visual_workflows.core.profile import ClusterProfile, Partition, Program
from visual_workflows.core.project import Project
from visual_workflows.core.ssh import ConnectionSettings
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def vwf(tmp_path, monkeypatch, capsys):
    profiles = tmp_path / "clusters"
    profiles.mkdir()
    ClusterProfile(
        name="Lynx", connection=ConnectionSettings(host="lynx.example.org"),
        partitions={"magma": Partition("", 96, 178.1, threads_per_core=2)},
        programs={"qe": Program([], executables={"pw": "pw.x"}),
                  "yambo": Program([], executables={"p2y": "p2y", "yambo": "yambo"})},
    ).save(profiles / "lynx.cluster.toml")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    web = FakeWeb()
    monkeypatch.setattr(pseudodojo, "_download", web)
    monkeypatch.setattr(pseudodojo.Library.__init__, "__defaults__", (None, web))

    def run(*argv):
        code = cli.main(["--profiles", str(profiles), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    run.web = web
    run("project", "new", "Si", "--cluster", "lynx")
    return run


def test_structure(vwf, tmp_path):
    ase.io.write(tmp_path / "MoS2.cif", mx2("MoS2", "2H", a=3.18, vacuum=10))
    code, out, _ = vwf("structure", "MoS2.cif", "--path", "GMKG")
    assert code == 0
    assert "Space group P-6m2 (187)" in out and "pw.x finds 12 in this cell" in out
    assert "Vacuum along c: one k-point there" in out
    assert "ibrav 4: celldm(1) = 6.00932" in out
    assert "k-points for 0.2 Å⁻¹: 12 12 1 0 0 0 (pw.x computes 19)" in out
    assert "Band path: G M K G" in out
    # --input: the input alone on stdout (to redirect into a file), the rest on stderr
    code, out, err = vwf("structure", "MoS2.cif", "--path", "GMKG", "--input")
    assert out.startswith("&control") and "Space group" in err
    pw = read_pw_input(out)
    assert pw.get("system", "ibrav") == 4 and pw.cards["K_POINTS"].option == "crystal_b"
    # a pw.x input with an ibrav, read as pw.x does (ASE would not)
    (tmp_path / "si.in").write_text(pw_input())
    code, out, _ = vwf("structure", "si.in", "--ibrav", "0", "--spacing", "0.3")
    assert "Fd-3m (227)" in out and "24 with fractional translations" in out
    assert "an nscf for Yambo needs force_symmorphic" in out and "ibrav 2" not in out
    # turned for pw.x
    ase.io.write(tmp_path / "Bi.cif", bulk("Bi", "rhombohedral", a=4.75, alpha=57.2))
    code, out, _ = vwf("structure", "Bi.cif")
    assert "--turn does it" in out
    code, out, _ = vwf("structure", "Bi.cif", "--turn")
    assert "pw.x finds 12 in this cell" in out and "--turn does it" not in out
    code, _, err = vwf("structure", "Bi.cif", "--ibrav", "2")
    assert code == 1 and "not this lattice's" in err


def test_pseudos(vwf, tmp_path):
    ase.io.write(tmp_path / "MoSi.cif", bulk("Mo", "bcc", a=3.15))
    code, out, _ = vwf("pseudos", "get", "Si", "--from", "MoSi.cif", "-C", "Si")
    assert code == 0
    assert "Mo       Mo.nc-sr-04_pbe_standard.upf  72   80      92" in out
    assert "Suggested: ecutwfc = 80, ecutrho = 320 (Ry)" in out and "van Setten" in out
    assert (tmp_path / "Si" / "pseudos" / "Si.nc-sr-04_pbe_standard.upf").exists()
    code, out, _ = vwf("pseudos", "get", "Ge", "--stringent", "-C", "Si")   # no hints in its table
    assert code == 0 and "Ge       Ge.nc-sr-04_pbe_stringent.upf  —    —       —" in out
    assert "Suggested" not in out
    code, out, _ = vwf("pseudos", "list", "-C", "Si")
    assert ["Si.nc-sr-04_pbe_standard.upf", "nc-sr-04_pbe_standard", "28"] in [
        line.split() for line in out.splitlines()]
    code, _, err = vwf("pseudos", "get", "Og", "-C", "Si")
    assert code == 1 and "Og is not in" in err
    code, _, err = vwf("pseudos", "get", "-C", "Si")
    assert code == 1 and "name the elements" in err


def test_add_qe_chain(vwf, tmp_path):
    (tmp_path / "Si" / "pseudos").mkdir()
    (tmp_path / "Si" / "pseudos" / "Si.upf").write_text(upf())
    (tmp_path / "scf.in").write_text(pw_input(control=""))
    code, out, err = vwf("project", "add-qe-chain", "scf.in", "--partition", "magma",
                         "--tasks-per-node", "48", "--mem", "100G", "--time", "2:00:00",
                         "--bands-path", "GXL", "-C", "Si")
    assert code == 0, err
    assert "Added step 03-p2y: p2y -I ../01-scf/out/si.save -O .; after 02-nscf" in out
    assert "Added step 04-bands: pw bands.in; after 03-p2y" in out
    assert "Error" not in err and "max_seconds" not in err     # set from the job's time
    p = Project.load(tmp_path / "Si")
    nscf = read_pw_input((p.steps[1].folder / "nscf.in").read_text())
    assert nscf.get("system", "nbnd") == 24 and nscf.k_points().grid == (4, 4, 4)   # the scf's
    assert nscf.get("control", "max_seconds") == 7200 - 600
    assert check_chain(p) == {}
    # without known occupied bands (no pseudopotentials there): --nscf-nbnd is asked for
    vwf("project", "new", "Bare", "--cluster", "lynx")
    code, _, err = vwf("project", "add-qe-chain", "scf.in", "-C", "Bare")
    assert code == 1 and "--nscf-nbnd" in err
    code, out, _ = vwf("project", "add-qe-chain", "scf.in", "--no-nscf", "--no-bands", "-C", "Bare")
    assert code == 0 and [s.id for s in Project.load(tmp_path / "Bare").steps] == ["01-scf"]
