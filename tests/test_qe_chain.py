"""The QE chain made in one go: scf, nscf for Yambo, p2y, bands."""

import warnings

from fakes import pw_input, upf
from visual_workflows.core.chain import check_chain
from visual_workflows.core.jobspec import JobSpec, Run
from visual_workflows.core.kpoints import Grid, band_path
from visual_workflows.core.project import Project
from visual_workflows.core.qe_chain import QeChain, add_qe_chain, chain_inputs
from visual_workflows.core.structure import Structure
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input

warnings.filterwarnings("ignore", category=DeprecationWarning)

JOB = JobSpec("cpu", 1, 4, 1, "4G", "01:00:00")


def si_chain(**kw):
    scf = pw_input(control="max_seconds = 3000")
    return QeChain(scf, JOB, nscf_grid=Grid((6, 6, 6), (1, 1, 1)), nscf_nbnd=40,
                   bands_path=band_path(Structure.from_pw_input(scf), "GXL"), bands_nbnd=12, **kw)


def test_inputs():
    inputs = chain_inputs(si_chain(), "01-scf")
    scf, nscf, bands = (read_pw_input(inputs[k]) for k in ("scf", "nscf", "bands"))
    assert scf.get("control", "outdir") == "./out" and scf.get("control", "calculation") == "scf"
    assert nscf.get("control", "outdir") == bands.get("control", "outdir") == "../01-scf/out"
    assert {p.get("control", "prefix") for p in (scf, nscf, bands)} == {"si"}
    assert nscf.get("system", "nbnd") == 40 and bands.get("system", "nbnd") == 12
    assert nscf.k_points().grid == (6, 6, 6) and nscf.k_points().shift == (0, 0, 0)   # unshifted
    assert nscf.get("system", "force_symmorphic") is True          # diamond: fractional translations
    assert scf.get("system", "force_symmorphic") is None
    assert bands.cards["K_POINTS"].option == "crystal_b"
    # Si at 0.26: no fractional translations pw.x takes, no force_symmorphic
    plain = pw_input().replace("Si 0.25 0.25 0.25", "Si 0.26 0.26 0.26")
    nscf = read_pw_input(chain_inputs(QeChain(plain, JOB, nscf_grid=Grid((4, 4, 4))), "01-scf")["nscf"])
    assert nscf.get("system", "force_symmorphic") is None


def test_steps_and_their_checks(tmp_path):
    p = Project.create(tmp_path / "P", cluster="Local")
    (p.path / "pseudos").mkdir()
    (p.path / "pseudos" / "Si.upf").write_text(upf())
    steps = add_qe_chain(p, si_chain())
    assert [s.id for s in steps] == ["01-scf", "02-nscf", "03-p2y", "04-bands"]
    assert [s.after for s in steps] == [[], ["01-scf"], ["02-nscf"], ["03-p2y"]]   # bands after p2y
    p2y = steps[2].job.runs[0]
    assert (p2y.executable, p2y.args, p2y.mpi) == ("p2y", "-I ../01-scf/out/si.save -O .", False)
    assert steps[2].job.tasks == 1
    assert (steps[1].folder / "nscf.in").exists() and steps[1].job.runs[0].input == "nscf.in"
    assert check_chain(p) == {}                                    # consistent by construction


def test_without_p2y_or_bands(tmp_path):
    p = Project.create(tmp_path / "P", cluster="Local")
    steps = add_qe_chain(p, si_chain(p2y=False))
    assert [s.id for s in steps] == ["01-scf", "02-nscf", "03-bands"]
    assert steps[2].after == ["02-nscf"]
    q = Project.create(tmp_path / "Q", cluster="Local")
    scf = pw_input()
    only = add_qe_chain(q, QeChain(scf, JOB, bands_path=band_path(Structure.from_pw_input(scf))))
    assert [s.id for s in only] == ["01-scf", "02-bands"] and only[1].after == ["01-scf"]


def test_p2y_after_bands_is_an_error(tmp_path):
    """The order the chain avoids: bands between the nscf and p2y."""
    p = Project.create(tmp_path / "P", cluster="Local")
    steps = add_qe_chain(p, si_chain(p2y=False))
    p.add_job_step("p2y", JobSpec("cpu", 1, 1, 1, "4G", "01:00:00", runs=[
        Run("yambo", "p2y", args="-I ../01-scf/out/si.save -O .", mpi=False)]), after=[steps[2].id])
    text = "\n".join(x.message for ps in check_chain(p).values() for x in ps if x.error)
    assert "a bands run: its wave functions" in text
