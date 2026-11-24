"""Why a job ended badly: the programs' own stops (in the formats QE 7.5's
errore and Yambo 5.3's error() write), known Slurm, MPI, shell and module
messages, and how a job ended."""

import asyncio
from types import SimpleNamespace

from visual_workflows import cli
from visual_workflows.core.diagnosis import (combine, diagnose_step, from_state, from_text,
                                             qe_errors, report, yambo_errors)
from visual_workflows.core.scheduler import JobState
from visual_workflows.core.transport import LocalTransport

# As QE's errore writes it (UtilXlib/error_handler.f90)
QE_STOP = """
     Reading input from scf.in

 %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
     Error in routine readpp (2):
     file /home/alice/pseudos/Si.upf not found
 %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

     stopping ...
"""

# As Yambo's error() writes it to its log (src/communicate)
YAMBO_STOP = """
 <01s> P1-compute05: [02] CPU structure, Files & I/O Directories
 <02s> P1-compute05: [ERROR] STOP signal received while in[02] CPU structure, Files & I/O Directories
 <02s> P1-compute05: [ERROR] Impossible to define an appropriate parallel structure
"""

SRUN = ("srun: error: Unable to create step for job 393617: More processors requested than permitted\n")


def texts(found):
    return " | ".join(d.cause for d in found)


def test_programs_own_stops():
    [d] = qe_errors(QE_STOP, "run 1 (pw)")
    assert d.cause.startswith("A pseudopotential file was not found.") and "readpp" in d.cause
    assert "pseudo_dir" in d.fix and d.where == "run 1 (pw)" and d.rank == 0
    [d] = qe_errors(QE_STOP.replace("readpp (2)", "cdiaghg (12)").replace(
        "file /home/alice/pseudos/Si.upf not found", "problems computing cholesky"))
    assert "not positive definite" in d.cause
    [d] = qe_errors(QE_STOP.replace("readpp", "some_routine"))
    assert d.cause == "QE stopped in some_routine: file /home/alice/pseudos/Si.upf not found"
    [d] = yambo_errors(YAMBO_STOP)
    assert "parallel variables" in d.cause and "while in [02] CPU structure" in d.cause
    [d] = yambo_errors("[ERROR] Allocation of X_mat failed with code 1\n")
    assert "out of memory" in d.cause
    [d] = yambo_errors("[ERROR] something new\n")
    assert d.cause == "Yambo stopped: something new"


def test_known_messages():
    assert "whole cores" in from_text(SRUN)[0].fix
    cases = {
        "MPI startup(): PMI server not found. Please set I_MPI_PMI_LIBRARY": "process manager",
        "pw.x: error while loading shared libraries: libmkl_core.so.2: cannot open shared object "
        "file: No such file or directory": "libmkl_core.so.2",
        "/var/spool/slurmd/job01/slurm_script: line 30: pw.x: command not found": "pw.x is not on the PATH",
        "ERROR: Unable to locate a modulefile for 'qe/7.9'": "module qe/7.9",
        "slurmstepd: error: Detected 1 oom_kill event in StepId=1.0": "more memory",
        "     convergence NOT achieved after 100 iterations: stopping": "100 iterations",
        "     Maximum CPU time exceeded": "max_seconds",
        "write error: Disk quota exceeded": "disk",
        "forrtl: severe (174): SIGSEGV, segmentation fault occurred": "segmentation fault",
    }
    for text, expected in cases.items():
        assert expected in texts(from_text(text)), text


def test_combined():
    # the program's message wins over the MPI abort it caused
    found = combine(JobState.FAILED, "NonZeroExitCode", 1, None,
                    [("job log", "application called MPI_Abort(MPI_COMM_WORLD, 1) - process 0"),
                     ("run 1 (yambo)", YAMBO_STOP)])
    assert len(found) == 1 and "parallel variables" in found[0].cause
    # nothing telling: the abort, and what the exit code says
    found = combine(JobState.FAILED, "", 139, None, [("job log", "MPI_ABORT was invoked on rank 0")])
    assert [d.rank for d in found] == [2, 2] and "SIGSEGV" in texts(found)
    assert "time limit" in texts(from_state(JobState.TIMEOUT))
    assert "node" in texts(from_state(JobState.FAILED, "NodeFail"))
    assert from_state(JobState.FAILED, "NonZeroExitCode") == []
    # the same message in the job log and a run's output: once
    assert len(combine(JobState.FAILED, "", 1, None, [("a", SRUN), ("b", SRUN)])) == 1


def test_diagnose_step_reads_the_runs_outputs(tmp_path):
    folder = tmp_path / "w" / "01-scf"
    folder.mkdir(parents=True)
    (folder / "slurm-7.out").write_text("application called MPI_Abort(MPI_COMM_WORLD, 1) - process 0\n")
    (folder / "scf.out").write_text(QE_STOP)
    job = SimpleNamespace(id="7", output="slurm-7.out", state=JobState.FAILED, reason="NonZeroExitCode",
                          exit_code=1, signal=None)
    run = SimpleNamespace(executable="pw")
    step = SimpleNamespace(id="01-scf", latest=job, job=SimpleNamespace(runs=[run]))

    async def go():
        async with LocalTransport(tmp_path) as t:
            async def remote_dir(s):
                return str(folder)

            async def run_output(s, i):
                return str(folder / "scf.out")
            runner = SimpleNamespace(transport=t, remote_dir=remote_dir, run_output=run_output)
            return await diagnose_step(runner, step)
    d = asyncio.run(go())
    text = "\n".join(report(d))
    assert "01-scf: job 7 failed, exit code 1" in text
    assert "A pseudopotential file was not found" in text and "[run 1 (pw, scf.out)]" in text
    assert "MPI_Abort" not in text and d.tail == []


def test_command_line(tmp_path, monkeypatch, capsys):
    from fakes import LocalSlurm

    def vwf(*argv):
        code = cli.main(["--profiles", str(tmp_path / "clusters"), *argv])
        out = capsys.readouterr().out
        return code, out, None
    vwf("clusters", "add", "Lynx", "--host", "lynx.example.org", "--user", "alice")
    home = tmp_path / "home"
    home.mkdir()
    slurm = LocalSlurm(home, forget=True, blocking=True)
    monkeypatch.setattr(cli, "make_transport", lambda profile: LocalTransport(home))
    monkeypatch.setattr(cli, "scheduler_for", lambda kind, transport: slurm)
    (tmp_path / "scf.sh").write_text("#!/bin/bash\ncat <<'EOF'\n" + QE_STOP + "EOF\nexit 1\n")
    (tmp_path / "ok.sh").write_text("#!/bin/bash\necho fine\n")
    proj = str(tmp_path / "p")
    vwf("project", "new", proj, "--cluster", "lynx")
    vwf("project", "add-step", "scf", "--script", str(tmp_path / "scf.sh"), "-C", proj)
    vwf("project", "add-step", "ok", "--script", str(tmp_path / "ok.sh"), "-C", proj)
    code, out, _ = vwf("project", "diagnose", "-C", proj)
    assert code == 0 and "No step's latest job ended badly" in out
    vwf("project", "submit", "01-scf", "02-ok", "--no-check", "-C", proj)
    code, out, _ = vwf("project", "status", "-C", proj)
    assert "vwf project diagnose 01-scf" in out and "02-ok" not in out.split("diagnose")[-1]
    code, out, _ = vwf("project", "diagnose", "-C", proj)
    assert code == 0 and "01-scf: job" in out and "A pseudopotential file was not found" in out
    assert "02-ok" not in out
    code, out, _ = vwf("project", "diagnose", "02-ok", "-C", proj)
    assert "No known message" in out and "The job log ends with:" in out and "fine" in out
