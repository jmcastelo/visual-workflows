"""Projects and steps: local folders, mirrored on a cluster.

    WSe2/                         the project folder
      project.toml                name, cluster, remote folder, step order
      01-scf/                     one folder per step
        step.toml                 script, dependencies, the step's jobs
        job.sh  pw.scf.in ...     uploaded as they are (the job script gets
                                  the status lines of jobstatus.py on the way)
        outputs/                  downloads (never uploaded)

On the cluster the project lives in <work_root>/<remote_dir>/, one folder per
step with the same name. A step can be run several times: each run is a
JobRecord, the last one is the step's current state.

Files carry `format` keys and keep keys this version does not know, as
cluster profiles do (profile.py).
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import tomli_w

from ..identity import FORMAT_PROJECT, PROJECT_FILE
from .scheduler import JobState

STEP_FILE = "step.toml"
OUTPUTS_DIR = "outputs"
# Never uploaded: the program's own files and downloads
_NOT_INPUTS = {STEP_FILE, OUTPUTS_DIR, ".vwf"}


class ProjectError(Exception):
    """A project or step cannot be read, created or used as asked."""


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]+", "-", name.strip()).strip("-") or "step"


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
    _raw: dict = field(default_factory=dict, repr=False)

    @property
    def id(self) -> str:
        return self.folder.name

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
        _write_toml(self.folder / STEP_FILE, data)

    @classmethod
    def load(cls, folder: Path) -> "Step":
        d = _read_toml(folder / STEP_FILE)
        if "script" not in d:
            raise ProjectError(f"{folder / STEP_FILE} names no job script")
        try:
            jobs = [JobRecord.from_dict(j) for j in d.get("jobs", [])]
        except (KeyError, TypeError) as e:
            raise ProjectError(f"{folder / STEP_FILE}: unreadable job record ({e})") from None
        return cls(folder, d.get("name", folder.name), d["script"], list(d.get("after", [])),
                   jobs, d)


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
        folder = self.path / f"{len(self.steps) + 1:02d}-{_slug(name)}"
        if folder.exists():
            raise ProjectError(f"{folder} exists already")
        folder.mkdir()
        try:
            shutil.copy2(script, folder / script.name)
            for f in inputs:
                f = Path(f)
                if f.is_dir():
                    shutil.copytree(f, folder / f.name)
                elif f.is_file():
                    shutil.copy2(f, folder / f.name)
                else:
                    raise ProjectError(f"No such input: {f}")
        except ProjectError:
            shutil.rmtree(folder)
            raise
        step = Step(folder, name, script.name, deps)
        step.save()
        self.steps.append(step)
        self.save()
        return step

    def depends_on(self, step: Step) -> list[Step]:
        return [self.step(a) for a in step.after]


def done(step: Step) -> bool:
    """The step's last job completed successfully."""
    j = step.latest
    return j is not None and j.final and j.state is JobState.COMPLETED
