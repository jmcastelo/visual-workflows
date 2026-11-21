"""Steps with a job description: job.sh written from [job], hand edits
noticed, and the checks and module tests run at submission, with real bash
jobs (LocalSlurm) and stand-ins for module, srun and pw.x on the PATH."""

import tomllib

import pytest

from fakes import LocalSlurm
from visual_workflows import cli
from visual_workflows.core.jobspec import JobSpec, Run
from visual_workflows.core.modules import Modules
from visual_workflows.core.profile import ClusterProfile, Partition, Program
from visual_workflows.core.project import JOB_SCRIPT, STEP_FILE, Project, ProjectError
from visual_workflows.core.runner import Runner, ScriptEdited, SubmitRefused
from visual_workflows.core.scheduler import JobState
from visual_workflows.core.ssh import ConnectionSettings
from visual_workflows.core.transport import LocalTransport

# `module` as Environment Modules answers the load test; it counts the
# loads, so that tests see whether a passed load test was run again
MODULE = """#!/bin/bash
case "$1" in
  load) echo load >> "$TOOLS_LOG"
        for m in "${@:2}"; do
          case "$m" in qe/*) ;; *) echo "ERROR: Unable to locate a modulefile for '$m'"; exit 1;; esac
        done ;;
  -t) echo qe/7.3 ;;
esac
exit 0
"""
SRUN = '#!/bin/bash\necho "srun $*" >> "$TOOLS_LOG"\nexec "$@"\n'
PW = '#!/bin/bash\necho "pw.x $*" >> "$TOOLS_LOG"\necho "JOB DONE."\n'


@pytest.fixture
def tools(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("module", MODULE), ("srun", SRUN), ("pw.x", PW)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "tools.log"
    log.touch()
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("TOOLS_LOG", str(log))
    return log


def profile() -> ClusterProfile:
    return ClusterProfile(
        name="Local", connection=ConnectionSettings(host="localhost"),
        partitions={"cpu": Partition("1-00:00:00", cores_per_node=8, memory_per_node_gb=16,
                                     threads_per_core=1)},
        programs={"qe": Program(["qe/7.3"], executables={"pw": "pw.x"})})


def scf_job(**kw) -> JobSpec:
    values = dict(partition="cpu", nodes=1, tasks_per_node=1, cpus_per_task=1, memory="1G",
                  time="00:10:00", runs=[Run("qe", "pw", "pw.scf.in")])
    values.update(kw)
    return JobSpec(**values)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pw.scf.in").write_text("&control\n  max_seconds = 300\n/\n")
    p = Project.create(tmp_path / "proj", cluster="Local")
    p.add_job_step("scf", scf_job(), [tmp_path / "pw.scf.in"], profile=profile())
    return p


@pytest.fixture
async def cluster(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    async with LocalTransport(home) as t:
        yield t, home


# -- step files -------------------------------------------------------------------------

def test_step_with_a_job_description(project):
    step = project.step("scf")
    assert step.described and step.script == JOB_SCRIPT
    text = (step.folder / JOB_SCRIPT).read_text()
    assert "#SBATCH --partition=cpu" in text and "srun pw.x -inp pw.scf.in > pw.scf.out" in text
    data = tomllib.loads((step.folder / STEP_FILE).read_text())
    # Older versions read "script" and run job.sh as it is
    assert data["script"] == "job.sh" and data["job"]["runs"][0]["input"] == "pw.scf.in"
    assert data["script_hash"].startswith("sha256:")
    again = Project.load(project.path).step("scf")
    assert again.job == step.job and again.script_hash == step.script_hash
    assert not again.script_edited
    assert [str(f) for f in again.inputs()] == ["job.sh", "pw.scf.in"]


def test_hand_edits_are_noticed(project):
    step = project.step("scf")
    path = step.folder / JOB_SCRIPT
    path.write_text(path.read_text() + "echo mine\n")
    assert step.script_edited
    step.write_script(profile())                     # written again: the edit is gone
    assert not step.script_edited and "echo mine" not in path.read_text()
    path.write_text(path.read_text() + "echo mine\n")
    step.keep_script()                               # kept: the step has a script of its own
    again = Project.load(project.path).step("scf")
    assert not again.described and not again.script_edited
    assert "job" not in tomllib.loads((step.folder / STEP_FILE).read_text())
    assert "echo mine" in (step.folder / JOB_SCRIPT).read_text()


def test_script_written_at_first_submission_if_no_profile(tmp_path):
    p = Project.create(tmp_path / "p", cluster="Local")
    step = p.add_job_step("scf", scf_job())
    assert not (step.folder / JOB_SCRIPT).exists() and not step.script_edited


def test_unreadable_job_table(project):
    path = project.step("scf").folder / STEP_FILE
    path.write_text(path.read_text().replace("nodes = 1", 'nodes = "one"'))
    with pytest.raises(ProjectError, match="job.nodes"):
        Project.load(project.path)


# -- submitting -------------------------------------------------------------------------

async def test_submit_writes_checks_and_runs(project, cluster, tools):
    t, home = cluster
    slurm = LocalSlurm(home, forget=True)
    modules = Modules(t)
    runner = Runner(project, profile(), t, slurm, modules)
    [(step, job_id)] = await runner.submit()
    await slurm.wait(job_id)
    await runner.refresh()
    assert step.latest.state is JobState.COMPLETED and runner.warnings == {}
    remote = home / "visual-workflows" / "proj" / "01-scf"
    assert (remote / "pw.scf.out").read_text() == "JOB DONE.\n"
    log = tools.read_text().splitlines()
    assert log.count("load") == 2               # the load test, then the job
    assert "pw.x -inp pw.scf.in" in log
    assert modules.passed(["qe/7.3"], ["pw.x"])
    # Submitted again with the same Modules: the passed test is not run again
    step.jobs.clear()
    await runner.submit()
    await slurm.wait(step.latest.id)
    assert tools.read_text().splitlines().count("load") == 3      # only the job's


async def test_edited_script_stops_the_submission(project, cluster, tools):
    t, home = cluster
    step = project.step("scf")
    (step.folder / JOB_SCRIPT).write_text("#!/bin/bash\necho mine\n")
    slurm = LocalSlurm(home)
    with pytest.raises(ScriptEdited) as e:
        await Runner(project, profile(), t, slurm).submit()
    assert e.value.steps == [step] and "01-scf" in str(e.value)
    assert slurm._jobs == {} and step.jobs == []


async def test_failed_module_test_refuses_then_submits_anyway(project, cluster, tools):
    t, home = cluster
    bad = profile()
    bad.programs["qe"].modules = ["vasp/6"]
    slurm = LocalSlurm(home, forget=True)
    runner = Runner(project, bad, t, slurm)
    with pytest.raises(SubmitRefused) as e:
        await runner.submit()
    [message] = e.value.problems["01-scf"]
    assert "the qe modules (vasp/6) do not work" in message and "Unable to locate" in message
    assert slurm._jobs == {}
    [(step, job_id)] = await runner.submit(check=False)
    assert "do not work" in runner.warnings["01-scf"][0]


async def test_job_description_errors_refuse(project, cluster, tools):
    t, home = cluster
    step = project.step("scf")
    step.job.memory = "64G"                         # nodes have 16 GB
    step.job.time = ""
    slurm = LocalSlurm(home)
    with pytest.raises(SubmitRefused) as e:
        await Runner(project, profile(), t, slurm).submit()
    text = "\n".join(e.value.problems["01-scf"])
    assert "No time limit" in text and "more than any cpu node has" in text
    assert slurm._jobs == {}


async def test_warnings_are_kept(project, cluster, tools):
    t, home = cluster
    step = project.step("scf")
    step.job.nodes, step.job.tasks_per_node = 2, 2           # half-empty nodes
    slurm = LocalSlurm(home, forget=True)
    runner = Runner(project, profile(), t, slurm)
    await runner.submit()
    assert "2 of the 8 cores" in runner.warnings["01-scf"][0]


async def test_profile_changes_reach_the_script(project, cluster, tools):
    t, home = cluster
    changed = profile()
    changed.account = "lv00"
    runner = Runner(project, changed, t, LocalSlurm(home, forget=True))
    await runner.submit()
    assert "#SBATCH --account=lv00" in (project.step("scf").folder / JOB_SCRIPT).read_text()


# -- the command line -------------------------------------------------------------------

def test_cli_edited_scripts_and_no_check(tmp_path, monkeypatch, capsys, tools):
    home = tmp_path / "home"
    home.mkdir()
    slurm = LocalSlurm(home, forget=True, blocking=True)
    monkeypatch.setattr(cli, "make_transport", lambda p: LocalTransport(home))
    monkeypatch.setattr(cli, "scheduler_for", lambda kind, transport: slurm)
    profiles = tmp_path / "clusters"
    profiles.mkdir()
    local = profile()
    local.save(profiles / "local.cluster.toml")

    def vwf(*argv):
        code = cli.main(["--profiles", str(profiles), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    (tmp_path / "pw.scf.in").write_text("&control\n  max_seconds = 300\n/\n")
    p = Project.create(tmp_path / "proj", cluster="Local")
    step = p.add_job_step("scf", scf_job(), [tmp_path / "pw.scf.in"], profile=local)
    (step.folder / JOB_SCRIPT).write_text("#!/bin/bash\necho mine > mine.txt\n")
    proj = str(p.path)
    code, _, err = vwf("project", "submit", "-C", proj)
    assert code == 1 and "--keep-edits or --rewrite" in err
    code, out, _ = vwf("project", "submit", "-C", proj, "--keep-edits")
    assert code == 0 and "01-scf: job" in out
    assert (home / "visual-workflows" / "proj" / "01-scf" / "mine.txt").exists()
    assert not Project.load(proj).step("scf").described

    # A failing module test: refused, then submitted with --no-check
    q = Project.create(tmp_path / "proj2", cluster="Local")
    q.add_job_step("scf", scf_job(runs=[Run("qe", "pw", "pw.scf.in")]), [tmp_path / "pw.scf.in"])
    local.programs["qe"].modules = ["vasp/6"]
    local.save(profiles / "local.cluster.toml")
    code, _, err = vwf("project", "submit", "-C", str(q.path))
    assert code == 1 and "do not work" in err and "--no-check" in err
    code, out, err = vwf("project", "submit", "-C", str(q.path), "--no-check")
    assert code == 0 and "01-scf: job" in out and "Warning: 01-scf:" in err
