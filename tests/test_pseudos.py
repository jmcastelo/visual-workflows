"""Pseudopotentials: where pw.x finds them, their checks, the project's
pseudos/ folder and the profile's library on the cluster."""

import pytest

from fakes import LocalSlurm, pw_input, upf
from visual_workflows.core import pseudos
from visual_workflows.core.input_checks import check_pseudopotentials
from visual_workflows.core.jobspec import JobSpec, Run, check
from visual_workflows.core.profile import ClusterProfile, Partition, Program
from visual_workflows.core.project import JOB_SCRIPT, Project
from visual_workflows.core.runner import Runner, SubmitRefused
from visual_workflows.core.ssh import ConnectionSettings
from visual_workflows.core.transport import LocalTransport
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input


def errors(problems):
    return "\n".join(p.message for p in problems if p.error)


def warnings(problems):
    return "\n".join(p.message for p in problems if not p.error)


@pytest.fixture
def project(tmp_path):
    return Project.create(tmp_path / "WSe2", cluster="Local")


def files_for(text, step_folder, library=""):
    return pseudos.pseudo_files(read_pw_input(text), step_folder, library)


# -- where pw.x looks ----------------------------------------------------------------

def test_locations(project):
    step = project.path / "01-scf"
    # pw.x's default, on the cluster
    [f] = files_for(pw_input(), step)
    assert (f.species, f.name, f.local, f.remote, f.default_dir) == \
        ("Si", "Si.upf", None, "~/espresso/pseudo/Si.upf", True)
    # The profile's library
    [f] = files_for(pw_input(), step, "/apps/pseudo/sssp")
    assert (f.local, f.remote, f.default_dir) == (None, "/apps/pseudo/sssp/Si.upf", False)
    # The project's folder comes first
    (project.path / "pseudos").mkdir()
    [f] = files_for(pw_input(), step, "/apps/pseudo/sssp")
    assert f.local == (project.path / "pseudos" / "Si.upf").resolve() and f.remote == "../pseudos/Si.upf"
    # pseudo_dir in the input wins: in the step, or anywhere on the cluster
    [f] = files_for(pw_input(control="pseudo_dir = './pp'"), step)
    assert f.local == (step / "pp" / "Si.upf").resolve()
    [f] = files_for(pw_input(control="pseudo_dir = '/home/alice/pp'"), step)
    assert (f.local, f.remote) == (None, "/home/alice/pp/Si.upf")
    # pw.x does not expand $ or ~ in pseudo_dir (check_pw_input reports it)
    assert files_for(pw_input(control="pseudo_dir = '$HOME/pp'"), step) == []
    # A relative folder outside the project is only on the cluster
    [f] = files_for(pw_input(control="pseudo_dir = '../../shared'"), step)
    assert f.local is None and f.remote == "../../shared/Si.upf"


def test_espresso_pseudo(project):
    step = project.path / "01-scf"
    assert pseudos.espresso_pseudo(step, "") == ""
    assert pseudos.espresso_pseudo(step, "/apps/pp") == "/apps/pp"
    (project.path / "pseudos").mkdir()
    assert pseudos.espresso_pseudo(step, "/apps/pp") == "$SLURM_SUBMIT_DIR/../pseudos"


# -- checks ---------------------------------------------------------------------------

def check_with(text, headers, step):
    pw = read_pw_input(text)
    files = pseudos.pseudo_files(pw, step)
    return check_pseudopotentials(pw, files, {f.remote: headers.get(f.species) for f in files},
                                  (7, 5))


def test_pseudopotential_checks(project):
    step = project.path / "01-scf"
    si = pseudos.parse_header(upf().encode())
    assert check_with(pw_input(), {"Si": si}, step) == []
    assert "not found at ~/espresso/pseudo/Si.upf" not in errors(check_with(pw_input(), {}, step))
    assert "pw.x's default folder" in warnings(check_with(pw_input(), {"Si": None}, step))
    text = pw_input(control="pseudo_dir = '/apps/pp'")
    assert "not found at /apps/pp/Si.upf: pw.x stops" in errors(check_with(text, {"Si": None}, step))
    nscf = pw_input(control="pseudo_dir = '/apps/pp'", calculation="nscf")
    assert "prefix.save" in warnings(check_with(nscf, {"Si": None}, step))
    assert "is not a pseudopotential" in errors(check_with(text, {"Si": "is not a pseudopotential pw.x reads (x)"}, step))
    # nbnd against the occupied bands: 2 Si atoms × 4 electrons = 4 bands
    few = pw_input().replace("nbnd = 8", "nbnd = 3")
    assert "below the 4 occupied bands (8 valence electrons)" in errors(check_with(few, {"Si": si}, step))
    assert "pw.x does not check it here" in warnings(check_with(few.replace("'scf'", "'nscf'"), {"Si": si}, step))
    assert check_with(pw_input().replace("nbnd = 8", "nbnd = 4"), {"Si": si}, step) == []
    # Spin–orbit, functionals, cutoffs
    soc = pw_input(system="noncolin = .true., lspinorb = .true.")
    assert "no spin–orbit" in errors(check_with(soc, {"Si": si}, step))
    assert check_with(soc, {"Si": pseudos.parse_header(upf(has_so=True).encode())}, step) == []
    no_cut = pw_input().replace("ecutwfc = 30, ", "")
    assert "ecutwfc not set" in errors(check_with(no_cut, {"Si": si}, step))
    assert check_with(no_cut, {"Si": pseudos.parse_header(upf(wfc_cutoff=40).encode())}, step) == []
    ge = pseudos.parse_header(upf("Ge", functional="SLA PZ NOGX NOGC").encode())
    assert "is a pseudopotential for Ge" in warnings(check_with(pw_input(), {"Si": ge}, step))


def test_different_functionals(project):
    step = project.path / "01-scf"
    text = (pw_input().replace("ntyp = 1", "ntyp = 2")
            .replace("Si 28.086 Si.upf", "Si 28.086 Si.upf\n  O 16.0 O.upf")
            .replace("  Si 0.25 0.25 0.25", "  O 0.25 0.25 0.25"))
    si = pseudos.parse_header(upf().encode())
    o_pbe = pseudos.parse_header(upf("O", 6.0, functional="SLA PW PBX PBC").encode())
    o_lda = pseudos.parse_header(upf("O", 6.0, functional="SLA PZ NOGX NOGC").encode())
    assert check_with(text, {"Si": si, "O": o_pbe}, step) == []          # both PBE
    assert "different functionals" in warnings(check_with(text, {"Si": si, "O": o_lda}, step))


# -- from the job description, and at submission ----------------------------------------

def profile(library=""):
    return ClusterProfile(
        name="Local", connection=ConnectionSettings(host="localhost"), pseudos=library,
        partitions={"cpu": Partition("1-00:00:00", 8, 16, threads_per_core=1)},
        programs={"qe": Program([], executables={"pw": "pw.x"})})


def job():
    return JobSpec("cpu", 1, 1, 1, "1G", "00:10:00", runs=[Run("qe", "pw", "pw.in")])


def test_project_folder_in_checks_and_scripts(project):
    step_folder = project.path / "01-scf"
    step_folder.mkdir()
    (step_folder / "pw.in").write_text(pw_input(control="max_seconds = 300"))
    (project.path / "pseudos").mkdir()
    problems = check(job(), profile(), step_folder)
    assert "Si.upf not found at" in errors(problems)                    # in the project: an error
    (project.path / "pseudos" / "Si.upf").write_text(upf())
    assert not errors(check(job(), profile(), step_folder))
    step = project.add_job_step("bse", job(), [step_folder / "pw.in"], profile=profile())
    assert 'export ESPRESSO_PSEUDO="$SLURM_SUBMIT_DIR/../pseudos"' in \
        (step.folder / JOB_SCRIPT).read_text()


@pytest.fixture
async def cluster(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    async with LocalTransport(home) as t:
        yield t, home


async def test_library_on_the_cluster_is_read_at_submission(project, cluster, tmp_path):
    t, home = cluster
    (tmp_path / "pw.in").write_text(pw_input(control="max_seconds = 300"))
    project.add_job_step("scf", job(), [tmp_path / "pw.in"])
    runner = Runner(project, profile("$HOME/pp"), t, LocalSlurm(home, forget=True))
    with pytest.raises(SubmitRefused, match=r"Si.upf not found at \$HOME/pp/Si.upf"):
        await runner.submit()
    (home / "pp").mkdir()
    (home / "pp" / "Si.upf").write_text(upf(z_valence=2.0))      # 4 electrons: 2 bands
    [(step, _)] = await runner.submit()
    assert 'export ESPRESSO_PSEUDO="$HOME/pp"' in (step.folder / JOB_SCRIPT).read_text()
    # A wrong file on the cluster is read there too
    step.jobs.clear()
    (home / "pp" / "Si.upf").write_text("not a pseudopotential")
    with pytest.raises(SubmitRefused, match="is not a pseudopotential"):
        await runner.submit()


async def test_project_pseudos_are_uploaded_once(project, cluster, tmp_path):
    t, home = cluster
    (project.path / "pseudos").mkdir()
    (project.path / "pseudos" / "Si.upf").write_text(upf())
    (tmp_path / "pw.in").write_text(pw_input(control="max_seconds = 300"))
    project.add_job_step("scf", job(), [tmp_path / "pw.in"])
    runner = Runner(project, profile(), t, LocalSlurm(home, forget=True))
    await runner.submit()
    remote = home / "visual-workflows" / "WSe2" / "pseudos" / "Si.upf"
    assert remote.read_text() == upf()
    assert await runner.upload_pseudos() == []                     # already there
    (project.path / "pseudos" / "Si.upf").write_text(upf(z_valence=4.5))
    assert await runner.upload_pseudos() == ["Si.upf"]             # changed: copied again
