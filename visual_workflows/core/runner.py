"""Running a project's steps on its cluster: upload, submit, follow, cancel."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath

from . import jobstatus
from .profile import ClusterProfile
from .project import JobRecord, Project, ProjectError, Step, done
from .scheduler import Job, JobState, Scheduler
from .transport import Transport, TransportError


async def expand_remote_path(transport: Transport, path: str) -> str:
    """A profile path with $HOME, ~ or other variables, as the cluster sees it."""
    if path.startswith("~/") or path == "~":
        path = "$HOME" + path[1:]
    if "$" not in path:
        return path
    escaped = path.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")
    r = await transport.run(f'printf "%s" "{escaped}"')
    r.check()
    return r.stdout


class Runner:
    def __init__(self, project: Project, profile: ClusterProfile, transport: Transport,
                 scheduler: Scheduler):
        self.project, self.profile = project, profile
        self.transport, self.scheduler = transport, scheduler
        self._root: str | None = None

    async def remote_root(self) -> str:
        if self._root is None:
            rd = self.project.remote_dir
            if not rd.startswith(("/", "~", "$")):
                rd = str(PurePosixPath(self.profile.work_root) / rd)
            self._root = await expand_remote_path(self.transport, rd)
        return self._root

    async def remote_dir(self, step: Step) -> str:
        return str(PurePosixPath(await self.remote_root()) / step.id)

    # -- upload and submit ----------------------------------------------------------

    async def upload(self, step: Step) -> str:
        """Copy the step's inputs, and its job script with the status lines."""
        remote = await self.remote_dir(step)
        await self.transport.makedirs(remote)
        for rel in step.inputs():
            if rel.as_posix() == step.script:
                continue
            await self.transport.put(step.folder / rel, f"{remote}/{rel.as_posix()}")
        await self.transport.write_text(f"{remote}/{step.script}",
                                        jobstatus.add_status_lines(step.script_text()))
        return remote

    async def submit(self, steps: list[Step] | None = None) -> list[tuple[Step, str]]:
        """Submit steps in project order, each after the steps it depends on
        (with Slurm dependencies for those still queued or running). Without
        a list: every step that has not completed and is not running."""
        await self.refresh()
        if steps is None:
            steps = [s for s in self.project.steps if not done(s) and not s.active]
        order = {s.id: i for i, s in enumerate(self.project.steps)}
        steps = sorted(steps, key=lambda s: order[s.id])
        for s in steps:
            if s.active:
                raise ProjectError(f"Step {s.id} is already queued or running "
                                   f"(job {s.latest.id})")
        submitted = []
        for step in steps:
            after = []
            for dep in self.project.depends_on(step):
                if done(dep):
                    continue
                if dep.active:
                    after.append(dep.latest.id)
                elif dep.latest is None:
                    raise ProjectError(f"Step {step.id} needs {dep.id}, which has not run: "
                                       "submit it too")
                else:
                    raise ProjectError(f"Step {step.id} needs {dep.id}, whose last job ended "
                                       f"{dep.latest.state.value}: run it again first")
            remote = await self.upload(step)
            job_id = await self.scheduler.submit(step.script, remote, after_ok=after)
            text = step.script_text()
            name = jobstatus.job_name(text, step.script)
            step.jobs.append(JobRecord(
                id=job_id, submitted=datetime.now(timezone.utc).replace(microsecond=0),
                output=jobstatus.output_file(text, job_id, name)))
            step.save()
            submitted.append((step, job_id))
        return submitted

    async def cancel(self, steps: list[Step] | None = None) -> list[str]:
        steps = steps if steps is not None else [s for s in self.project.steps if s.active]
        ids = [s.latest.id for s in steps if s.active]
        await self.scheduler.cancel(ids)
        return ids

    # -- following ------------------------------------------------------------------

    async def refresh(self) -> list[Step]:
        """Update the steps whose last job has not finished; returns those that changed."""
        active = [s for s in self.project.steps if s.active]
        if not active:
            return []
        known = await self.scheduler.jobs()
        changed = []
        gone = [s for s in active if s.latest.id not in known]
        history = await self.scheduler.history([s.latest.id for s in gone]) if gone else {}
        for step in active:
            record = step.latest
            job = known.get(record.id)
            before = (record.state, record.final)
            if job is not None:
                _update(record, job)
                # Slurm's word is final once the job ended: it has the exit code
                record.final = job.state.ended
                if record.final:
                    record.source = "slurm"
                    await self._read_modules(step, record)
            else:
                outcome = await self._outcome(step, record, history.get(record.id))
                _apply(record, outcome)
            if (record.state, record.final) != before:
                changed.append(step)
            step.save()
        return changed

    async def _read(self, path: str) -> str | None:
        try:
            return await self.transport.read_text(path)
        except TransportError:
            return None

    async def _status(self, step: Step, record) -> jobstatus.StatusFile | None:
        text = await self._read(f"{await self.remote_dir(step)}/{jobstatus.status_path(record.id)}")
        return jobstatus.parse_status(text) if text is not None else None

    async def _read_modules(self, step: Step, record):
        status = await self._status(step, record)
        if status:
            record.modules = status.modules

    async def _outcome(self, step: Step, record, accounting: Job | None):
        status = await self._status(step, record)
        output = None
        if record.output:
            output = await self._read(f"{await self.remote_dir(step)}/{record.output}")
        return jobstatus.final_state(None, accounting, status, output)


def _update(record, job: Job):
    record.state = job.state
    record.scheduler_state = job.scheduler_state
    record.reason = job.reason
    record.exit_code, record.signal = job.exit_code, job.signal
    record.started = _second(job.start_time) or record.started
    record.ended = _second(job.end_time) or record.ended
    record.nodes = job.nodes or record.nodes


def _apply(record, outcome: jobstatus.Outcome):
    record.state, record.reason = outcome.state, outcome.reason
    record.exit_code, record.signal = outcome.exit_code, outcome.signal
    record.started = _second(outcome.start_time) or record.started
    record.ended = _second(outcome.end_time) or record.ended
    record.nodes = outcome.nodes or record.nodes
    record.modules = outcome.modules or record.modules
    record.source = outcome.source
    record.final = True


def _second(t: datetime | None) -> datetime | None:
    """Whole seconds, as TOML keeps them; naive times (Slurm's text output) as they are."""
    return t.replace(microsecond=0) if t else None
