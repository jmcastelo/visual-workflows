"""Running a project's steps on its cluster: upload, submit, follow, cancel."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import PurePosixPath

from . import jobspec, jobstatus
from .modules import Modules
from .profile import ClusterProfile
from .project import JobRecord, Project, ProjectError, Step, done
from .scheduler import Job, JobState, Scheduler
from .transport import Transport, TransportError


class ScriptEdited(ProjectError):
    """Steps whose job script, written from their job description, was edited
    by hand: the user keeps the edits (Step.keep_script) or has the script
    written again (Step.write_script), then submits again."""

    def __init__(self, steps: list[Step]):
        self.steps = steps
        names = ", ".join(s.id for s in steps)
        super().__init__(f"The job script of {names} was edited by hand since it was written "
                         "from the job description: keep the edits (the step then has a "
                         "script of its own) or write the script again.")


class SubmitRefused(ProjectError):
    """The checks found errors: nothing was submitted. Submitting again with
    check=False submits anyway."""

    def __init__(self, problems: dict[str, list[str]]):
        self.problems = problems
        lines = [f"{step}: {p}" for step, ps in problems.items() for p in ps]
        super().__init__("Not submitted:\n" + "\n".join(lines))


def _sets(script: str, long: str, short: str) -> bool:
    pattern = re.compile(rf"^\s*#SBATCH\s+(?:{long}(?:=|\s)|{short}(?:\s|\S))")
    return any(pattern.match(line) for line in script.splitlines())


def profile_options(profile: ClusterProfile, script: str) -> list[str]:
    """The profile's account and QOS as sbatch options, for scripts that do
    not set their own (options on the command line would override them)."""
    options = []
    if profile.account and not _sets(script, "--account", "-A"):
        options.append(f"--account={profile.account}")
    if profile.qos and not _sets(script, "--qos", "-q"):
        options.append(f"--qos={profile.qos}")
    return options


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
                 scheduler: Scheduler, modules: Modules | None = None):
        self.project, self.profile = project, profile
        self.transport, self.scheduler = transport, scheduler
        # Pass the session's, so that passed load tests are not run again
        self.modules = modules or Modules(transport, profile.module_use)
        self._root: str | None = None
        self.warnings: dict[str, list[str]] = {}    # from the last submit(), per step

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

    async def submit(self, steps: list[Step] | None = None, *,
                     check: bool = True) -> list[tuple[Step, str]]:
        """Submit steps in project order, each after the steps it depends on
        (with Slurm dependencies for those still queued or running). Without
        a list: every step that has not completed and is not running.

        Steps with a job description are checked first (prepare()): nothing
        is submitted if one has a hand-edited script (ScriptEdited) or, with
        check=True, errors (SubmitRefused). Warnings end up in self.warnings."""
        await self.refresh()
        if steps is None:
            steps = [s for s in self.project.steps if not done(s) and not s.active]
        order = {s.id: i for i, s in enumerate(self.project.steps)}
        steps = sorted(steps, key=lambda s: order[s.id])
        for s in steps:
            if s.active:
                raise ProjectError(f"Step {s.id} is already queued or running "
                                   f"(job {s.latest.id})")
        await self.prepare(steps, check=check)
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
            text = step.script_text()
            job_id = await self.scheduler.submit(step.script, remote, after_ok=after,
                                                 options=profile_options(self.profile, text))
            name = jobstatus.job_name(text, step.script)
            step.jobs.append(JobRecord(
                id=job_id, submitted=datetime.now(timezone.utc).replace(microsecond=0),
                output=jobstatus.output_file(text, job_id, name)))
            step.save()
            submitted.append((step, job_id))
        return submitted

    async def prepare(self, steps: list[Step], *, check: bool = True):
        """For the steps with a job description: refuse hand-edited scripts,
        write the scripts again (the profile may have changed), check the
        descriptions and test that their programs' modules load. Raises
        before anything is uploaded, so that no step goes without the others."""
        described = [s for s in steps if s.described]
        edited = [s for s in described if s.script_edited]
        if edited:
            raise ScriptEdited(edited)
        self.warnings = {}
        errors: dict[str, list[str]] = {}
        for step in described:
            step.write_script(self.profile)
            for problem in jobspec.check(step.job, self.profile):
                (errors if problem.error else self.warnings).setdefault(step.id, []).append(
                    problem.message)
        for (program, modules, executables), step_ids in self._load_tests(described).items():
            modules, executables = list(modules), list(executables)
            if self.modules.passed(modules, executables):
                continue
            result = await self.modules.check(modules, executables)
            if not result.ok:
                for step_id in step_ids:
                    errors.setdefault(step_id, []).append(
                        f"the {program} modules ({' '.join(modules)}) do not work: "
                        + "; ".join(result.problems()))
        if errors and check:
            raise SubmitRefused(errors)
        for step_id, messages in errors.items():      # submitted anyway: still worth showing
            self.warnings.setdefault(step_id, []).extend(messages)

    def _load_tests(self, steps: list[Step]) -> dict[tuple, list[str]]:
        """(program, modules, executables) to test, and the steps needing them."""
        wanted: dict[str, tuple[set, list[str]]] = {}
        for step in steps:
            for run in step.job.runs:
                program = self.profile.programs.get(run.program)
                if program is None or not program.modules:
                    continue                # check() reports those
                exes, ids = wanted.setdefault(run.program, (set(), []))
                if run.executable in program.executables:
                    exes.add(program.executables[run.executable])
                if step.id not in ids:
                    ids.append(step.id)
        return {(name, tuple(self.profile.programs[name].modules), tuple(sorted(exes))): ids
                for name, (exes, ids) in wanted.items()}

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
