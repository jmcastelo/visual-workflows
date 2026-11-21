"""A transport that answers commands from recorded outputs."""

from __future__ import annotations

import asyncio
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

from visual_workflows.core.scheduler import Job, JobState, Scheduler
from visual_workflows.core.transport import CommandResult, Transport

DATA = Path(__file__).parent / "data"


def recorded(cluster: str, name: str) -> str:
    return (DATA / cluster / name).read_text()


class FakeTransport(Transport):
    """`answers` maps a command prefix to (exit code, stdout[, stderr]);
    the longest matching prefix wins. Commands run are kept in `commands`."""

    def __init__(self, answers: dict[str, tuple]):
        self.answers = answers
        self.commands: list[str] = []

    async def run(self, command, *, timeout=60, login=True):
        self.commands.append(command)
        matches = [p for p in self.answers if command.startswith(p)]
        if not matches:
            return CommandResult(command, 127, "", f"{command.split()[0]}: command not found")
        code, out, *err = self.answers[max(matches, key=len)]
        return CommandResult(command, code, out, err[0] if err else "")

    async def connect(self):
        pass

    async def close(self):
        pass

    @property
    def is_connected(self):
        return True

    async def home(self):
        return "/home/alice"

    async def _no_files(self, *args, **kwargs):
        raise NotImplementedError("FakeTransport has no files")

    stat = listdir = makedirs = put = get = write_text = read_text = _no_files
    read_range = download = _no_files


# -- a scheduler that runs job scripts on this PC ------------------------------------


class LocalSlurm(Scheduler):
    """Runs job scripts with bash, with the environment Slurm gives them.

    forget=True: ended jobs vanish from jobs() at once, as on Lynx after
    MinJobAge, so their state must come from the status files."""

    def __init__(self, home, forget=True, blocking=False):
        """blocking: run each job to its end inside submit(), for callers that
        start a new event loop per command (the CLI tests)."""
        self.home = str(home)
        self.forget = forget
        self.blocking = blocking
        self._next = 1000
        self._jobs: dict[str, dict] = {}

    def _path(self, workdir):
        return workdir if workdir.startswith("/") else os.path.join(self.home, workdir)

    async def submit(self, script, workdir, *, after_ok=(), options=()):
        self._next += 1
        job_id = str(self._next)
        workdir = self._path(workdir)
        entry = {"id": job_id, "state": JobState.PENDING, "proc": None, "workdir": workdir,
                 "exit": None, "output": os.path.join(workdir, f"slurm-{job_id}.out")}
        self._jobs[job_id] = entry
        entry["task"] = asyncio.create_task(self._run(entry, script, list(after_ok)))
        if self.blocking:
            await entry["task"]
        return job_id

    async def _run(self, entry, script, after):
        for dep in after:
            await self._jobs[dep]["task"]
            if self._jobs[dep]["state"] is not JobState.COMPLETED:
                entry["state"] = JobState.CANCELLED      # DependencyNeverSatisfied
                return
        env = dict(os.environ, HOME=self.home, SLURM_JOB_ID=entry["id"],
                   SLURM_SUBMIT_DIR=entry["workdir"], SLURM_JOB_NODELIST="local01")
        with open(entry["output"], "w") as out:
            entry["proc"] = await asyncio.create_subprocess_exec(
                "bash", script, cwd=entry["workdir"], env=env, stdout=out,
                stderr=asyncio.subprocess.STDOUT, start_new_session=True)
            entry["state"] = JobState.RUNNING
            code = await entry["proc"].wait()
        entry["exit"] = code
        if entry["state"] is JobState.RUNNING:
            entry["state"] = JobState.COMPLETED if code == 0 else JobState.FAILED

    async def _stop(self, job_id, message, state):
        entry = self._jobs[job_id]
        while entry["proc"] is None and entry["state"] is JobState.PENDING:
            await asyncio.sleep(0.01)
        if entry["proc"] is not None and entry["proc"].returncode is None:
            with open(entry["output"], "a") as out:
                now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                out.write(f"slurmstepd: error: *** JOB {job_id} ON local01 CANCELLED AT {now} "
                          f"{message} ***\n")
            entry["state"] = state
            # Slurm signals every process of the job, not only the script
            os.killpg(entry["proc"].pid, signal.SIGTERM)
        await entry["task"]

    async def cancel(self, job_ids):
        for j in job_ids:
            await self._stop(j, "DUE to SIGNAL Terminated", JobState.CANCELLED)

    async def time_out(self, job_id):
        await self._stop(job_id, "DUE TO TIME LIMIT", JobState.TIMEOUT)

    async def wait(self, *job_ids):
        for j in job_ids:
            await self._jobs[j]["task"]

    async def jobs(self):
        result = {}
        for j, e in self._jobs.items():
            if e["state"].ended and self.forget:
                continue
            result[j] = Job(id=j, state=e["state"], nodes="local01",
                            exit_code=e["exit"] if e["state"].ended else None,
                            end_time=datetime.now(timezone.utc) if e["state"].ended else None)
        return result

    async def job(self, job_id):
        return (await self.jobs()).get(job_id)

    async def history(self, job_ids):
        return {}

    async def accounting_available(self):
        return False

    async def associations(self):
        return []

    async def qos(self):
        return []

    async def version(self):
        return "local"

    async def partitions(self):
        return []

    async def nodes(self):
        return []

    async def user_groups(self):
        return []


def pw_input(kpoints="K_POINTS automatic\n4 4 4 0 0 0", control="max_seconds = 3000",
             system="", calculation="scf"):
    """A complete, valid pw.x input (bulk silicon), with parts to change."""
    return (f"&control\n  calculation = '{calculation}', prefix = 'si'\n  {control}\n/\n"
            "&system\n  ibrav = 2, celldm(1) = 10.26, nat = 2, ntyp = 1, ecutwfc = 30, nbnd = 8"
            f"\n  {system}\n/\n&electrons\n/\n"
            "ATOMIC_SPECIES\n  Si 28.086 Si.upf\n"
            "ATOMIC_POSITIONS alat\n  Si 0.00 0.00 0.00\n  Si 0.25 0.25 0.25\n"
            f"{kpoints}\n")
