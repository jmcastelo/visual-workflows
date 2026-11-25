"""Running a project's steps on its cluster: upload, submit, follow, cancel."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import PurePosixPath

from . import jobspec, jobstatus, pseudos
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

    async def run_output(self, step: Step, index: int) -> str | None:
        """Where run `index` (1-based) of a step with a job description writes
        its own output on the cluster: a QE run's output file (the script
        redirects it), or for p2y, yambo and ypp the newest log Yambo wrote in
        the run's -O folder (l_<runlevels>, l-<job>_<runlevels> with -J; with
        several MPI tasks LOG/..._CPU_1; run again, with _01, _02... added). None when it writes to the job's log,
        or nothing is there yet."""
        from .chain import _options
        from .shell import quote
        if step.job is None or not 1 <= index <= len(step.job.runs):
            raise ProjectError(f"{step.id} has no run {index}"
                               + (f" (it has {len(step.job.runs)})" if step.job else
                                  " (its job script is its own)"))
        run = step.job.runs[index - 1]
        folder = PurePosixPath(await self.remote_dir(step))
        if run.output_file:
            return str(folder / run.output_file)
        if run.program != jobspec.YAMBO:     # p2y, yambo, ypp: their own l_* logs
            return None
        where = folder / _options(run.args).get("O", ".")
        stem = f"l-{PurePosixPath(run.jobs[0]).name}_" if run.jobs else "l_"
        r = await self.transport.run(
            # a run again in the same folder: yambo adds _01, _02... (LOG/..._CPU_1_01)
            f"cd {quote(str(where))} 2>/dev/null && ls -1t -- {stem}* LOG/{stem}*_CPU_1 "
            f"LOG/{stem}*_CPU_1_[0-9]* 2>/dev/null | head -n 1", timeout=60)
        name = r.stdout.strip() if r.ok else ""
        return str(where / name) if name else None

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
        if steps:
            await self.upload_pseudos()
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
        for step in described:
            step.write_script(self.profile)
        errors, self.warnings = await self.check(described)
        if errors and check:
            raise SubmitRefused(errors)
        for step_id, messages in errors.items():      # submitted anyway: still worth showing
            self.warnings.setdefault(step_id, []).extend(messages)

    async def check(self, steps: list[Step], *, on_cluster: bool = True
                    ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """Every check of the steps with a job description, as (errors,
        warnings) by step id: the description and inputs (jobspec.check), the
        chain, and, `on_cluster`, the pseudopotentials kept there and the
        module tests."""
        described = [s for s in steps if s.described]
        errors: dict[str, list[str]] = {}
        warnings: dict[str, list[str]] = {}

        def add(step_id, problem):
            (errors if problem.error else warnings).setdefault(step_id, []).append(problem.message)

        for step in described:
            remote = await self._remote_pseudo_headers(step) if on_cluster else {}
            for problem in jobspec.check(step.job, self.profile, step.folder, remote):
                add(step.id, problem)
        from .chain import check_chain
        for step_id, problems in check_chain(self.project, described).items():
            for problem in problems:
                add(step_id, problem)
        if not on_cluster:
            return errors, warnings
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
        return errors, warnings

    async def _remote_pseudo_headers(self, step: Step) -> dict:
        """The headers of the pseudopotentials pw.x runs of the step will read on
        the cluster (not in the project): {path: header, message or None}."""
        from ..yambopy_ext.qepy.pw_input import PwInputError, read_pw_input
        headers = {}
        for run in step.job.runs:
            if run.program != jobspec.QE or run.executable != "pw" or not run.input:
                continue
            try:
                pw = read_pw_input((step.folder / run.input).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, PwInputError):
                continue             # jobspec.check() reports it
            for f in pseudos.pseudo_files(pw, step.folder, self.profile.pseudos):
                if f.local is not None or f.remote in headers:
                    continue
                headers[f.remote] = await self._read_remote_header(step, f.remote)
        return headers

    async def _read_remote_header(self, step: Step, path: str):
        if path.startswith("~/"):
            path = path[2:]          # relative paths are taken from the home folder
        elif path.startswith("$"):
            path = await expand_remote_path(self.transport, path)
        elif not path.startswith("/"):
            path = str(PurePosixPath(await self.remote_dir(step)) / path)
        try:
            if await self.transport.stat(path) is None:
                return None
            data = await self.transport.read_range(path, 0, pseudos.HEADER_BYTES)
        except TransportError as e:
            return f"cannot be read on the cluster ({e})"
        return pseudos.parse_header(data)

    async def upload_pseudos(self) -> list[str]:
        """Copy the project's pseudos/ folder to the cluster, next to the steps
        (files there with the same size and not changed here since are kept).
        Returns those copied."""
        local = self.project.path / pseudos.PROJECT_PSEUDOS
        if not local.is_dir():
            return []
        remote = str(PurePosixPath(await self.remote_root()) / pseudos.PROJECT_PSEUDOS)
        copied = []
        for path in sorted(local.rglob("*")):
            if not path.is_file() or any(part.startswith(".") for part in path.relative_to(local).parts):
                continue
            target = f"{remote}/{path.relative_to(local).as_posix()}"
            info, local_info = await self.transport.stat(target), path.stat()
            # The same size and not changed here since: already there (whether the
            # upload kept the file's time or not)
            if info is not None and info.size == local_info.st_size \
                    and local_info.st_mtime <= info.mtime:
                continue
            await self.transport.makedirs(str(PurePosixPath(target).parent))
            await self.transport.put(path, target)
            copied.append(path.relative_to(local).as_posix())
        return copied

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
            # What a user watching sees: the state, and why it waits
            before = (record.state, record.final, record.reason, record.started)
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
            if (record.state, record.final, record.reason, record.started) != before:
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
