"""Projects and steps: local folders, mirrored on a cluster.

    WSe2/                         the project folder
      project.toml                name, cluster, remote folder, step order
      01-scf/                     one folder per step
        step.toml                 script, dependencies, the step's jobs, and
                                  optionally a job description ([job], jobspec.py)
        job.sh  pw.scf.in ...     uploaded as they are (the job script gets
                                  the status lines of jobstatus.py on the way)
        outputs/                  downloads (never uploaded)

On the cluster the project lives in <work_root>/<remote_dir>/, one folder per
step with the same name. A step can be run several times: each run is a
JobRecord, the last one is the step's current state.

A step with a job description has its job.sh written from it (write_script),
with the script's hash kept in step.toml, so that hand edits are noticed
(script_edited) and the user decides: keep them (keep_script: the step then
has a script of its own) or write the script again. Older versions, which
know nothing of [job], keep the table and run job.sh as it is.

Files carry `format` keys and keep keys this version does not know, as
cluster profiles do (profile.py).
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import tomli_w

from ..identity import FORMAT_PROJECT, PROJECT_FILE
from .jobspec import JobSpec, JobSpecError, render
from .profile import ClusterProfile
from .scheduler import JobState

STEP_FILE = "step.toml"
JOB_SCRIPT = "job.sh"           # the script written from a job description
OUTPUTS_DIR = "outputs"
# Never uploaded: the program's own files and downloads
_NOT_INPUTS = {STEP_FILE, OUTPUTS_DIR, ".vwf"}


class ProjectError(Exception):
    """A project or step cannot be read, created or used as asked."""


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]+", "-", name.strip()).strip("-") or "step"


def folder_name(name: str) -> str:
    """A project's folder name, here and on the cluster: "WSe2 bulk" -> "WSe2-bulk"."""
    return _slug(name)


def _write_toml(path: Path, data: dict):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(tomli_w.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProjectError(f"Missing {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ProjectError(f"{path} is not valid TOML: {e}") from None


def _clean(d: dict) -> dict:
    """TOML has no None: leave such keys out."""
    return {k: v for k, v in d.items() if v is not None and v != []}


@dataclass
class JobRecord:
    id: str
    submitted: datetime
    state: JobState = JobState.PENDING
    scheduler_state: str = ""
    reason: str = ""
    exit_code: int | None = None
    signal: int | None = None
    started: datetime | None = None
    ended: datetime | None = None
    nodes: str = ""
    output: str = ""            # Slurm's output file, relative to the step folder
    modules: list[str] = field(default_factory=list)
    final: bool = False         # nothing more will change
    source: str = ""            # where the final state came from

    def to_dict(self) -> dict:
        return _clean({
            "id": self.id, "submitted": self.submitted, "state": self.state.value,
            "scheduler_state": self.scheduler_state or None, "reason": self.reason or None,
            "exit_code": self.exit_code, "signal": self.signal, "started": self.started,
            "ended": self.ended, "nodes": self.nodes or None, "output": self.output or None,
            "modules": self.modules, "final": self.final, "source": self.source or None})

    @classmethod
    def from_dict(cls, d: dict) -> "JobRecord":
        try:
            state = JobState(d.get("state", "unknown"))
        except ValueError:
            state = JobState.UNKNOWN
        return cls(id=str(d["id"]), submitted=d["submitted"], state=state,
                   scheduler_state=d.get("scheduler_state", ""), reason=d.get("reason", ""),
                   exit_code=d.get("exit_code"), signal=d.get("signal"),
                   started=d.get("started"), ended=d.get("ended"), nodes=d.get("nodes", ""),
                   output=d.get("output", ""), modules=list(d.get("modules", [])),
                   final=bool(d.get("final", False)), source=d.get("source", ""))


@dataclass
class Step:
    folder: Path                # local folder; its name is the step's id
    name: str
    script: str                 # file name of the job script, in the folder
    after: list[str] = field(default_factory=list)   # ids (folder names) of earlier steps
    jobs: list[JobRecord] = field(default_factory=list)
    job: JobSpec | None = None  # a job description: the script is written from it
    script_hash: str = ""       # of the script as last written from the description
    _raw: dict = field(default_factory=dict, repr=False)

    @property
    def id(self) -> str:
        return self.folder.name

    @property
    def described(self) -> bool:
        """The step's script is written from its job description."""
        return self.job is not None

    def write_script(self, profile: ClusterProfile) -> str:
        """Write the script from the job description (for this cluster) and
        remember it, so that later hand edits are noticed. Returns the text."""
        if self.job is None:
            raise ProjectError(f"Step {self.id} has no job description")
        from .pseudos import espresso_pseudo
        text = render(self.job, profile, self.id, espresso_pseudo(self.folder, profile.pseudos))
        (self.folder / self.script).write_text(text, encoding="utf-8")
        self.script_hash = _hash(text)
        self.save()
        return text

    @property
    def script_edited(self) -> bool:
        """The written script was changed by hand since it was written."""
        if self.job is None or not self.script_hash:
            return False
        try:
            text = (self.folder / self.script).read_text(encoding="utf-8")
        except FileNotFoundError:
            return False             # written again at the next submission
        return _hash(text) != self.script_hash

    def keep_script(self):
        """Keep the hand-edited script: the step becomes one with a script of
        its own, and its job description is dropped."""
        self.job, self.script_hash = None, ""
        self.save()

    @property
    def latest(self) -> JobRecord | None:
        return self.jobs[-1] if self.jobs else None

    @property
    def active(self) -> bool:
        return self.latest is not None and not self.latest.final

    def script_text(self) -> str:
        return (self.folder / self.script).read_text()

    def inputs(self) -> list[Path]:
        """Files to upload, relative to the folder: everything but the
        program's own files, downloads and hidden files."""
        files = []
        for p in sorted(self.folder.rglob("*")):
            rel = p.relative_to(self.folder)
            if p.is_file() and rel.parts[0] not in _NOT_INPUTS \
                    and not any(part.startswith(".") for part in rel.parts):
                files.append(rel)
        return files

    def save(self):
        data = copy.deepcopy(self._raw)
        data.update(name=self.name, script=self.script, after=list(self.after),
                    jobs=[j.to_dict() for j in self.jobs])
        data.pop("job", None)
        data.pop("script_hash", None)
        if self.job is not None:
            data["job"] = self.job.to_table()
            if self.script_hash:
                data["script_hash"] = self.script_hash
        # The step's own keys first, the job records last: easier to read
        jobs = data.pop("jobs")
        data["jobs"] = jobs
        _write_toml(self.folder / STEP_FILE, data)
        self._raw = data

    @classmethod
    def load(cls, folder: Path) -> "Step":
        path = folder / STEP_FILE
        d = _read_toml(path)
        job = None
        if "job" in d:
            try:
                job = JobSpec.from_table(d["job"], str(path))
            except JobSpecError as e:
                raise ProjectError(str(e)) from None
        if "script" not in d and job is None:
            raise ProjectError(f"{path} names no job script")
        try:
            jobs = [JobRecord.from_dict(j) for j in d.get("jobs", [])]
        except (KeyError, TypeError) as e:
            raise ProjectError(f"{path}: unreadable job record ({e})") from None
        hash_ = d.get("script_hash", "")
        return cls(folder, d.get("name", folder.name), d.get("script", JOB_SCRIPT),
                   list(d.get("after", [])), jobs, job,
                   hash_ if isinstance(hash_, str) else "", d)


@dataclass
class Project:
    path: Path
    name: str
    cluster: str                # a cluster profile's name
    remote_dir: str             # relative to the cluster's work_root, or absolute
    steps: list[Step] = field(default_factory=list)
    _raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def create(cls, path: str | os.PathLike, cluster: str, name: str | None = None,
               remote_dir: str | None = None) -> "Project":
        path = Path(path)
        if path.exists() and any(path.iterdir()):
            raise ProjectError(f"{path} exists and is not empty")
        path.mkdir(parents=True, exist_ok=True)
        project = cls(path, name or path.name, cluster, remote_dir or _slug(path.name))
        project.save()
        return project

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Project":
        path = Path(path)
        d = _read_toml(path / PROJECT_FILE)
        fmt = d.get("format", "")
        if not isinstance(fmt, str) or fmt.partition("/")[0] != FORMAT_PROJECT.partition("/")[0]:
            raise ProjectError(f"{path / PROJECT_FILE} is not a project file")
        steps = [Step.load(path / s) for s in d.get("steps", [])]
        return cls(path, d.get("name", path.name), d.get("cluster", ""),
                   d.get("remote_dir", _slug(path.name)), steps, d)

    def save(self):
        data = copy.deepcopy(self._raw)
        data.update(format=FORMAT_PROJECT, name=self.name, cluster=self.cluster,
                    remote_dir=self.remote_dir, steps=[s.id for s in self.steps])
        _write_toml(self.path / PROJECT_FILE, {"format": data.pop("format"), **data})

    def step(self, key: str) -> Step:
        """A step by id ("01-scf") or name ("scf")."""
        for s in self.steps:
            if key in (s.id, s.name):
                return s
        raise ProjectError(f"No step {key!r} in {self.name} "
                           f"(steps: {', '.join(s.id for s in self.steps) or 'none'})")

    def add_step(self, name: str, script: str | os.PathLike,
                 inputs: list[str | os.PathLike] = (), after: list[str] = ()) -> Step:
        """A new step: its folder, with copies of the script and the inputs."""
        script = Path(script)
        if not script.is_file():
            raise ProjectError(f"No such job script: {script}")
        deps = [self.step(a).id for a in after]
        folder = self._new_folder(name, inputs, extra=[script])
        step = Step(folder, name, script.name, deps)
        step.save()
        self.steps.append(step)
        self.save()
        return step

    def _new_folder(self, name: str, inputs, extra=()) -> Path:
        """The next step's folder, with copies of the files given."""
        folder = self.path / f"{len(self.steps) + 1:02d}-{_slug(name)}"
        if folder.exists():
            raise ProjectError(f"{folder} exists already")
        folder.mkdir()
        try:
            for f in extra:
                shutil.copy2(f, folder / Path(f).name)
            _copy_inputs(folder, inputs)
        except ProjectError:
            shutil.rmtree(folder)
            raise
        return folder

    def update_job_step(self, step: Step, job: JobSpec, after: list[str] | None = None,
                        inputs: list[str | os.PathLike] = (),
                        profile: ClusterProfile | None = None) -> Step:
        """A step's job description changed (and the steps it runs after, and
        new input files copied in, replacing files of the same name); its script
        is written again if the profile is given (hand edits are lost)."""
        if after is not None:
            step.after = [self.step(a).id for a in after if self.step(a).id != step.id]
        _copy_inputs(step.folder, inputs, replace=True)
        step.job = job
        step.save()
        if profile is not None:
            step.write_script(profile)
        return step

    def add_job_step(self, name: str, job: JobSpec, inputs: list[str | os.PathLike] = (),
                     after: list[str] = (), profile: ClusterProfile | None = None) -> Step:
        """A new step with a job description: its folder, with copies of the
        inputs; its script is written now if the profile is given, else at
        the first submission."""
        deps = [self.step(a).id for a in after]
        folder = self._new_folder(name, inputs)
        step = Step(folder, name, JOB_SCRIPT, deps, job=job)
        step.save()
        if profile is not None:
            step.write_script(profile)
        self.steps.append(step)
        self.save()
        return step

    def depends_on(self, step: Step) -> list[Step]:
        return [self.step(a) for a in step.after]


def _copy_inputs(folder: Path, inputs, replace: bool = False):
    for f in inputs:
        f = Path(f)
        target = folder / f.name
        if f.resolve() == target.resolve():
            continue                 # already there
        if replace and target.is_dir() and f.is_dir():
            shutil.rmtree(target)
        if f.is_dir():
            shutil.copytree(f, target)
        elif f.is_file():
            shutil.copy2(f, target)
        else:
            raise ProjectError(f"No such input: {f}")


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def done(step: Step) -> bool:
    """The step's last job completed successfully."""
    j = step.latest
    return j is not None and j.final and j.state is JobState.COMPLETED
