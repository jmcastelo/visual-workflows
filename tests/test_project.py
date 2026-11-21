"""Projects, steps, job status lines, and running steps end to end with real
bash jobs (LocalSlurm) through a LocalTransport."""

import asyncio
from datetime import datetime, timezone

import pytest

from fakes import LocalSlurm
from visual_workflows.core import jobstatus
from visual_workflows.core.jobstatus import (StatusFile, add_status_lines, final_state,
                                             output_file, parse_status, remove_status_lines)
from visual_workflows.core.profile import ClusterProfile
from visual_workflows.core.project import Project, ProjectError
from visual_workflows.core.runner import Runner, expand_remote_path
from visual_workflows.core.scheduler import Job, JobState
from visual_workflows.core.ssh import ConnectionSettings
from visual_workflows.core.transport import LocalTransport

# The group's Yambo job script on Lynx (scratch/jobfiles/runpYAMBO.sh), shortened
YAMBO_SCRIPT = """#!/bin/bash -l
#SBATCH --job-name=gw
#SBATCH --nodes=1
#SBATCH --tasks-per-node=2
#SBATCH --output=%x_%j.sout

nodes=1
#commands
module purge
srun --mpi=pmi2 yambo -F gw.in -J gw
"""


# -- status lines ---------------------------------------------------------------------

def test_status_lines_go_after_the_sbatch_header():
    out = add_status_lines(YAMBO_SCRIPT)
    lines = out.splitlines()
    begin = lines.index(jobstatus.BEGIN)
    assert lines[begin - 1] == "#SBATCH --output=%x_%j.sout"
    assert lines[lines.index(jobstatus.END) + 1] == ""       # then the script goes on
    assert out.count(jobstatus.BEGIN) == 1
    assert add_status_lines(out) == out                      # not added twice
    assert remove_status_lines(out) == YAMBO_SCRIPT


def test_status_lines_without_header_or_shebang():
    out = add_status_lines("pw.x < scf.in > scf.out")
    assert out.startswith("#!/bin/bash\n" + jobstatus.BEGIN)
    assert out.endswith("pw.x < scf.in > scf.out")
    out = add_status_lines("#!/bin/sh\n# comment\npw.x\n")
    assert out.startswith("#!/bin/sh\n" + jobstatus.BEGIN)


def test_output_file_names():
    assert output_file("#!/bin/bash\n", "7", "job.sh") == "slurm-7.out"
    name = jobstatus.job_name(YAMBO_SCRIPT, "job.sh")
    assert name == "gw" and output_file(YAMBO_SCRIPT, "7", name) == "gw_7.sout"
    assert output_file("#SBATCH -o run.%j.log\n", "7", "x") == "run.7.log"


def test_parse_status():
    s = parse_status("started 1795178580 compute01\nsignal 1795178640 TERM\n"
                     "modules intel/mpi/2021.5.0 qe/7.1-intelmpi\nended 1795178641 143\n")
    assert s.started == datetime(2026, 11, 20, 12, 43, tzinfo=timezone.utc)   # 13:43 in Valencia
    assert (s.nodes, s.signal, s.exit_code) == ("compute01", "TERM", 143)
    assert s.modules == ["intel/mpi/2021.5.0", "qe/7.1-intelmpi"]
    # Requeued: the last run counts
    s = parse_status("started 1 n1\nended 2 1\nstarted 3 n2\n")
    assert (s.nodes, s.ended) == ("n2", None)


T = datetime(2026, 11, 20, tzinfo=timezone.utc)
TIMEOUT_LINE = "error: *** JOB 7 ON n1 CANCELLED AT 2026-11-20T13:44:02 DUE TO TIME LIMIT ***"


@pytest.mark.parametrize("status, output, state, code", [
    (StatusFile(T, "n1", T, 0), "", JobState.COMPLETED, 0),
    (StatusFile(T, "n1", T, 3), "", JobState.FAILED, 3),
    (StatusFile(T, "n1", T, 143, "TERM"), TIMEOUT_LINE, JobState.TIMEOUT, None),
    (StatusFile(T, "n1", T, 143, "TERM"), "*** JOB 7 ON n1 CANCELLED AT x DUE to SIGNAL Terminated ***",
     JobState.CANCELLED, None),
    # Killed without a word (SIGKILL): the output says why, or it failed
    (StatusFile(T, "n1"), "Detected 1 oom_kill event in StepId=7.0", JobState.OUT_OF_MEMORY, None),
    (StatusFile(T, "n1"), "", JobState.FAILED, None),
    (None, None, JobState.CANCELLED, None),          # never ran
])
def test_final_state_from_status_file(status, output, state, code):
    outcome = final_state(None, None, status, output)
    assert (outcome.state, outcome.exit_code) == (state, code)


def test_final_state_prefers_slurm_then_accounting():
    status = StatusFile(T, "n1", T, 0, modules=["qe/7.1"])
    slurm = Job("7", state=JobState.TIMEOUT, signal=9)
    acct = Job("7", state=JobState.FAILED, exit_code=2)
    assert final_state(slurm, acct, status, "").source == "slurm"
    o = final_state(None, acct, status, "")
    assert (o.state, o.exit_code, o.source, o.modules) == (JobState.FAILED, 2, "accounting", ["qe/7.1"])


# -- project files ---------------------------------------------------------------------

@pytest.fixture
def inputs(tmp_path):
    d = tmp_path / "inputs"
    d.mkdir()
    (d / "scf.sh").write_text("#!/bin/bash\n#SBATCH --mem=1G\necho scf > scf.out\n")
    (d / "nscf.sh").write_text("#!/bin/bash\n#SBATCH --mem=1G\ncat ../01-scf/scf.out > nscf.out\n")
    (d / "pw.scf.in").write_text("&control /\n")
    (d / "pseudo").mkdir()
    (d / "pseudo" / "W.upf").write_text("upf")
    return d


def test_project_files(tmp_path, inputs):
    p = Project.create(tmp_path / "WSe2 bulk", cluster="Lynx")
    assert p.remote_dir == "WSe2-bulk"
    scf = p.add_step("scf", inputs / "scf.sh", [inputs / "pw.scf.in", inputs / "pseudo"])
    nscf = p.add_step("nscf", inputs / "nscf.sh", after=["scf"])
    assert (scf.id, nscf.id, nscf.after) == ("01-scf", "02-nscf", ["01-scf"])
    (scf.folder / "outputs").mkdir()
    (scf.folder / "outputs" / "big.save").write_text("x")
    (scf.folder / ".hidden").write_text("x")
    assert [str(f) for f in scf.inputs()] == ["pseudo/W.upf", "pw.scf.in", "scf.sh"]
    again = Project.load(tmp_path / "WSe2 bulk")
    assert [s.id for s in again.steps] == ["01-scf", "02-nscf"]
    assert again.step("nscf").after == ["01-scf"] and again.cluster == "Lynx"
    assert (tmp_path / "WSe2 bulk" / "project.toml").read_text().startswith(
        'format = "workflow-project/1"')


def test_project_errors(tmp_path, inputs):
    p = Project.create(tmp_path / "p", cluster="Lynx")
    with pytest.raises(ProjectError, match="No such job script"):
        p.add_step("scf", inputs / "missing.sh")
    with pytest.raises(ProjectError, match="No step 'scf'"):
        p.add_step("nscf", inputs / "nscf.sh", after=["scf"])
    with pytest.raises(ProjectError, match="No such input"):
        p.add_step("scf", inputs / "scf.sh", [inputs / "missing.in"])
    assert not (tmp_path / "p" / "01-scf").exists()        # nothing left behind
    with pytest.raises(ProjectError, match="not empty"):
        Project.create(tmp_path / "p", cluster="Lynx")


# -- running steps -------------------------------------------------------------------------

@pytest.fixture
async def cluster(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    profile = ClusterProfile(name="Local", connection=ConnectionSettings(host="localhost"))
    async with LocalTransport(home) as t:
        yield profile, t, home


async def test_expand_remote_path(cluster):
    _, t, home = cluster
    assert await expand_remote_path(t, "$HOME/vw") == f"{home}/vw"
    assert await expand_remote_path(t, "~/vw") == f"{home}/vw"
    assert await expand_remote_path(t, "/abs/path") == "/abs/path"


async def test_chain_completes_and_is_known_from_status_files(tmp_path, inputs, cluster):
    profile, t, home = cluster
    p = Project.create(tmp_path / "proj", cluster="Local")
    p.add_step("scf", inputs / "scf.sh", [inputs / "pw.scf.in", inputs / "pseudo"])
    p.add_step("nscf", inputs / "nscf.sh", after=["scf"])
    slurm = LocalSlurm(home, forget=True)
    runner = Runner(p, profile, t, slurm)
    submitted = await runner.submit()
    (scf, scf_id), (nscf, nscf_id) = submitted
    assert slurm._jobs[nscf_id]["state"] is JobState.PENDING     # waits for scf
    await slurm.wait(scf_id, nscf_id)
    changed = await runner.refresh()
    assert {s.id for s in changed} == {"01-scf", "02-nscf"}
    for step in (scf, nscf):
        job = step.latest
        assert (job.state, job.exit_code, job.final, job.source) == \
            (JobState.COMPLETED, 0, True, "status file")
        assert job.nodes == "local01" and job.started and job.ended
    remote = home / "visual-workflows" / "proj"
    assert (remote / "02-nscf" / "nscf.out").read_text() == "scf\n"
    assert (remote / "01-scf" / "pseudo" / "W.upf").exists()
    # The uploaded script has the status lines; the local one is untouched
    assert jobstatus.BEGIN in (remote / "01-scf" / "scf.sh").read_text()
    assert jobstatus.BEGIN not in (scf.folder / "scf.sh").read_text()
    # Saved: a fresh load knows the outcome
    again = Project.load(tmp_path / "proj")
    assert again.step("nscf").latest.state is JobState.COMPLETED
    assert await runner.submit() == []          # nothing left to run


async def test_failed_step_and_its_dependants(tmp_path, cluster):
    profile, t, home = cluster
    (tmp_path / "fail.sh").write_text("#!/bin/bash\necho bad input >&2\nexit 3\n")
    (tmp_path / "next.sh").write_text("#!/bin/bash\ntrue\n")
    p = Project.create(tmp_path / "proj", cluster="Local")
    p.add_step("scf", tmp_path / "fail.sh")
    p.add_step("nscf", tmp_path / "next.sh", after=["scf"])
    slurm = LocalSlurm(home)
    runner = Runner(p, profile, t, slurm)
    [(scf, job_id)] = await runner.submit([p.step("scf")])
    await slurm.wait(job_id)
    await runner.refresh()
    assert (scf.latest.state, scf.latest.exit_code) == (JobState.FAILED, 3)
    with pytest.raises(ProjectError, match="ended failed: run it again first"):
        await runner.submit([p.step("nscf")])


async def test_dependency_that_never_ran(tmp_path, inputs, cluster):
    profile, t, home = cluster
    p = Project.create(tmp_path / "proj", cluster="Local")
    p.add_step("scf", inputs / "scf.sh")
    p.add_step("nscf", inputs / "nscf.sh", after=["scf"])
    with pytest.raises(ProjectError, match="has not run: submit it too"):
        await Runner(p, profile, t, LocalSlurm(home)).submit([p.step("nscf")])


async def test_cancel_and_time_limit(tmp_path, cluster):
    profile, t, home = cluster
    (tmp_path / "long.sh").write_text("#!/bin/bash\nsleep 30\necho never\n")
    p = Project.create(tmp_path / "proj", cluster="Local")
    p.add_step("a", tmp_path / "long.sh")
    p.add_step("b", tmp_path / "long.sh")
    slurm = LocalSlurm(home)
    runner = Runner(p, profile, t, slurm)
    (a, a_id), (b, b_id) = await runner.submit()
    with pytest.raises(ProjectError, match="already queued or running"):
        await runner.submit([a])
    while slurm._jobs[b_id]["proc"] is None:
        await asyncio.sleep(0.01)
    assert await runner.cancel([a]) == [a_id]
    await asyncio.wait_for(slurm.time_out(b_id), 10)     # the TERM trap exits at once
    await runner.refresh()
    assert a.latest.state is JobState.CANCELLED and "SIGTERM" in a.latest.reason
    assert b.latest.state is JobState.TIMEOUT
    status = parse_status((home / "visual-workflows/proj/02-b" / f".vwf/job-{b_id}.status").read_text())
    assert (status.signal, status.exit_code) == ("TERM", 143)
    assert "never" not in (home / "visual-workflows/proj/02-b" / f"slurm-{b_id}.out").read_text()


async def test_state_from_slurm_while_it_remembers(tmp_path, inputs, cluster):
    profile, t, home = cluster
    p = Project.create(tmp_path / "proj", cluster="Local")
    p.add_step("scf", inputs / "scf.sh")
    slurm = LocalSlurm(home, forget=False)
    runner = Runner(p, profile, t, slurm)
    [(scf, job_id)] = await runner.submit()
    await slurm.wait(job_id)
    await runner.refresh()
    assert (scf.latest.state, scf.latest.source, scf.latest.final) == \
        (JobState.COMPLETED, "slurm", True)


def test_profile_account_and_qos_only_where_the_script_has_none():
    from visual_workflows.core.runner import profile_options
    p = ClusterProfile(name="Tirant", connection=ConnectionSettings(host="h"),
                       account="lv00", qos="class_t")
    assert profile_options(p, "#!/bin/bash\n#SBATCH --ntasks=48\n") == \
        ["--account=lv00", "--qos=class_t"]
    assert profile_options(p, "#SBATCH --account=other\n#SBATCH -q interactive\n") == []
    assert profile_options(p, "#SBATCH -A other\n") == ["--qos=class_t"]
    assert profile_options(p, "# --qos=x in a comment\n") == ["--account=lv00", "--qos=class_t"]
    p.account = p.qos = ""
    assert profile_options(p, "") == []


async def test_submit_adds_the_profile_account(tmp_path, inputs, cluster):
    profile, t, home = cluster
    profile.account = "lv00"
    p = Project.create(tmp_path / "proj", cluster="Local")
    p.add_step("scf", inputs / "scf.sh")
    seen = {}

    class Recording(LocalSlurm):
        async def submit(self, script, workdir, *, after_ok=(), options=()):
            seen["options"] = list(options)
            return await super().submit(script, workdir, after_ok=after_ok, options=options)

    slurm = Recording(home)
    [(_, job_id)] = await Runner(p, profile, t, slurm).submit()
    await slurm.wait(job_id)
    assert seen["options"] == ["--account=lv00"]


def test_a_project_knows_when_its_files_changed(tmp_path, inputs):
    import os
    p = Project.create(tmp_path / "proj", cluster="Lynx")
    p.add_step("scf", inputs / "scf.sh")
    mine = Project.load(p.path)
    assert not mine.stale()
    other = Project.load(p.path)                       # the command line, say
    step_file = other.step("scf").folder / "step.toml"
    before = step_file.stat().st_mtime_ns
    other.step("scf").save()
    os.utime(step_file, ns=(before + 10**9, before + 10**9))   # surely another time
    assert mine.stale() and not Project.load(p.path).stale()
