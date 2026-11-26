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


def test_structure_input_ready(vwf, tmp_path, monkeypatch):
    """--input with the project's pseudopotentials: their files, the cutoffs
    their hints suggest, and a prefix from the file's name."""
    ase.io.write(tmp_path / "MoS2.cif", mx2("MoS2", "2H", a=3.18, vacuum=10))
    vwf("pseudos", "get", "Mo", "-C", "Si")
    monkeypatch.chdir(tmp_path / "Si")
    code, out, err = vwf("structure", "../MoS2.cif", "--input")
    pw = read_pw_input(out)
    assert pw.get("control", "prefix") == "mos2"
    assert pw.get("system", "ecutwfc") == 80 and pw.get("system", "ecutrho") == 320
    assert "Mo.nc-sr-04_pbe_standard.upf" in out and "S.upf" in out
    assert "No pseudopotential for S" in err and "PseudoDojo's normal hint" in err
    code, out, _ = vwf("structure", "../MoS2.cif", "--input", "--prefix", "m", "--ecutwfc", "50",
                       "--level", "high")
    pw = read_pw_input(out)
    assert pw.get("control", "prefix") == "m" and pw.get("system", "ecutwfc") == 50
    assert pw.get("system", "ecutrho") == 200
    monkeypatch.chdir(tmp_path)
    code, out, err = vwf("structure", "MoS2.cif", "--input")
    assert pw_has_no_cutoff(out) and "no cutoffs (vwf pseudos get first" in err


def pw_has_no_cutoff(text):
    return read_pw_input(text).get("system", "ecutwfc") is None


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
    assert all(s.job.cpus_per_task == 1 for s in p.steps)
    # without known occupied bands (no pseudopotentials there): --nscf-nbnd is asked for
    vwf("project", "new", "Bare", "--cluster", "lynx")
    code, _, err = vwf("project", "add-qe-chain", "scf.in", "-C", "Bare")
    assert code == 1 and "--nscf-nbnd" in err
    code, out, _ = vwf("project", "add-qe-chain", "scf.in", "--no-nscf", "--no-bands", "-C", "Bare")
    assert code == 0 and [s.id for s in Project.load(tmp_path / "Bare").steps] == ["01-scf"]


def test_add_qe_chain_to_the_bse(vwf, tmp_path):
    from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input
    (tmp_path / "Si" / "pseudos").mkdir()
    (tmp_path / "Si" / "pseudos" / "Si.upf").write_text(upf())
    (tmp_path / "scf.in").write_text(pw_input(control=""))
    resources = ["--partition", "magma", "--tasks-per-node", "48", "--mem", "100G", "--time", "2:00:00"]
    code, out, err = vwf("project", "add-qe-chain", "scf.in", *resources, "--no-bands",
                         "--gw", "--bse", "gw", "-C", "Si")
    assert code == 0, err
    assert "Added step 03-p2y: p2y -I ../01-scf/out/si.save -O .; then yambo setup.in" in out
    assert "Added step 04-gw: yambo gw.in; after 03-p2y" in out
    assert "Added step 05-bse: yambo bse.in; after 04-gw" in out
    bse = read_yambo_input((tmp_path / "Si" / "05-bse" / "bse.in").read_text())
    assert bse.get("KfnQPdb") == "E < ../04-gw/gw/ndb.QP"
    assert check_chain(Project.load(tmp_path / "Si")) == {}


@pytest.mark.parametrize("extra, message", [
    (["--bse", "scissor"], "needs the shift"), (["--scissor", "0.5"], "go with --bse scissor"),
    (["--bse", "gw"], "needs the GW step"),
])
def test_add_qe_chain_yambo_refused(vwf, tmp_path, extra, message):
    (tmp_path / "scf.in").write_text(pw_input(control=""))
    code, _, err = vwf("project", "add-qe-chain", "scf.in", "--no-bands", *extra, "-C", "Si")
    assert code == 1 and message in err


def test_add_qe_chain_yambo_bands(vwf, tmp_path):
    (tmp_path / "scf.in").write_text(pw_input(control=""))          # no pseudopotentials here
    code, _, err = vwf("project", "add-qe-chain", "scf.in", "--no-bands", "--nscf-nbnd", "24",
                       "--bse", "scissor", "--scissor", "0.5", "-C", "Si")
    assert code == 1 and "--yambo-bands FIRST LAST" in err
    code, out, err = vwf("project", "add-qe-chain", "scf.in", "--no-bands", "--nscf-nbnd", "24",
                         "--bse", "scissor", "--scissor", "0.5", "--yambo-bands", "3", "6", "-C", "Si")
    assert code == 0 and "Added step 04-bse" in out


def test_yambo_input(vwf, tmp_path):
    from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input
    (tmp_path / "pp").mkdir()
    (tmp_path / "pp" / "Si.upf").write_text(upf())
    (tmp_path / "nscf.in").write_text(pw_input(calculation="nscf", system="force_symmorphic = .true.")
                                      .replace("nbnd = 8", "nbnd = 24"))
    code, out, err = vwf("yambo-input", "gw", "nscf.in", "--pseudos", "pp", "--tasks", "4",
                         "--yambo-version", "5.3")
    assert code == 0
    gw = read_yambo_input(out)                                      # stdout: the input alone
    assert gw.block("QPkrange").rows == [[1, 8, 3, 6]] and gw.get("X_and_IO_CPU")
    assert "note: NGsBlkXp = 3 Ry" in err
    code, out, _ = vwf("yambo-input", "bse", "--nbnd", "24", "--occupied", "4", "--nk", "16",
                       "--scissor", "0.6")
    assert read_yambo_input(out).block("KfnQP_E").rows == [[0.6, 1.0, 1.0]]
    code, _, err = vwf("yambo-input", "bse", "nscf.in", "--pseudos", "pp")
    assert code == 1 and "--qp-db PATH" in err and "--scissor EV" in err
    code, _, err = vwf("yambo-input", "gw", "--nbnd", "24")         # occupied bands not known
    assert code == 1 and "--bands FIRST LAST" in err
    code, _, err = vwf("yambo-input", "gw")
    assert code == 1 and "give the nscf's input" in err


def test_add_qe_chain_yambo_resources(vwf, tmp_path):
    """The GW and BSE steps' own tasks and threads; the pw.x steps keep theirs."""
    (tmp_path / "Si" / "pseudos").mkdir()
    (tmp_path / "Si" / "pseudos" / "Si.upf").write_text(upf())
    (tmp_path / "scf.in").write_text(pw_input(control=""))
    code, out, err = vwf("project", "add-qe-chain", "scf.in", "--partition", "magma",
                         "--tasks-per-node", "8", "--mem", "16G", "--time", "1:00:00",
                         "--no-bands", "--gw", "--bse", "gw", "--yambo-tasks-per-node", "1",
                         "--yambo-cpus-per-task", "16", "-C", "Si")
    assert code == 0, err
    steps = {s.id: s.job for s in Project.load(tmp_path / "Si").steps}
    assert (steps["01-scf"].tasks_per_node, steps["01-scf"].cpus_per_task) == (8, 1)
    assert (steps["04-gw"].tasks_per_node, steps["04-gw"].cpus_per_task) == (1, 16)
    assert (steps["05-bse"].tasks_per_node, steps["05-bse"].cpus_per_task) == (1, 16)
    assert "Error" not in err
