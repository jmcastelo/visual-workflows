"""Recording a cluster for the tests: the program asks what it always asks
(the connection report, Discover, the modules, the jobs) through a transport
that keeps every command and its answer, and writes them with a manifest
(cluster.toml) that the tests' fake cluster replays (tests/fakes.py).

Because the program records its own commands, a recording always has exactly
what the code sends; and since the fake cluster refuses unrecorded commands,
a change in what the code sends shows as failing tests until the clusters are
recorded again. See docs/development.md, "Recording a cluster".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from .discovery import discover
from .modules import Modules, for_program
from .profile import ClusterProfile
from .report import cluster_report
from .transport import CommandResult, Transport


class RecordingTransport(Transport):
    """A transport that keeps each command run and its result."""

    def __init__(self, inner: Transport):
        self.inner = inner
        self.records: list[CommandResult] = []

    async def run(self, command, *, timeout=60, login=True):
        result = await self.inner.run(command, timeout=timeout, login=login)
        if not any(r.command == command for r in self.records):
            self.records.append(result)
        return result

    def __getattr__(self, name):
        return getattr(self.inner, name)

    # The abstract methods, passed on
    async def connect(self):
        await self.inner.connect()

    async def close(self):
        await self.inner.close()

    @property
    def is_connected(self):
        return self.inner.is_connected

    async def home(self):
        return await self.inner.home()

    async def stat(self, path):
        return await self.inner.stat(path)

    async def listdir(self, path):
        return await self.inner.listdir(path)

    async def makedirs(self, path):
        return await self.inner.makedirs(path)

    async def put(self, local, remote):
        return await self.inner.put(local, remote)

    async def get(self, remote, local):
        return await self.inner.get(remote, local)

    async def read_range(self, remote, offset, size):
        return await self.inner.read_range(remote, offset, size)

    async def download(self, remote, local, offset=0, **kwargs):
        return await self.inner.download(remote, local, offset, **kwargs)

    async def write_text(self, remote, text):
        return await self.inner.write_text(remote, text)

    async def read_text(self, remote):
        return await self.inner.read_text(remote)


@dataclass
class Recording:
    records: list[CommandResult]
    expect: dict


async def record_cluster(transport: Transport, scheduler_for, profile: ClusterProfile) -> Recording:
    """Ask the cluster what the program asks, keeping every answer.
    `scheduler_for(transport)` makes the scheduler on the recording transport."""
    rec = RecordingTransport(transport)
    scheduler = scheduler_for(rec)
    modules = Modules(rec, profile.module_use)
    report = await cluster_report(rec, scheduler, profile, modules)
    found = await discover(scheduler)
    available = await modules.available()
    shown = [m.full for program in ("qe", "yambo") for m in for_program(available, program)[:2]]
    if shown:
        await modules.details(shown)
    await scheduler.jobs()
    expect = {
        "slurm": report.slurm_version, "json_output": report.json_output,
        "accounting": report.accounting, "modules": report.modules_text,
        "python": report.python, "partitions": list(found.partitions),
        "threads_per_core": max((p.threads_per_core for p in found.partitions.values()), default=0),
        "qe_modules": bool(for_program(available, "qe")),
    }
    return Recording(rec.records, expect)


# Commands whose text depends on what was asked (the modules shown): kept by
# the start that does not, so that other questions find the same answer
VARIABLE_COMMANDS = ("for m in",)


def _stable_prefix(command: str) -> str:
    return next((p for p in VARIABLE_COMMANDS if command.startswith(p)), command)


def _file_name(command: str, used: set[str]) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z0-9]*", command)[:3]
    stem = "-".join(words).lower() or "command"
    name, n = f"{stem}.txt", 2
    while name in used:
        name, n = f"{stem}-{n}.txt", n + 1
    used.add(name)
    return name


def write_recording(folder: Path, recording: Recording, replacements: dict[str, str]) -> Path:
    """Write each answer to a file and the manifest, after replacing personal
    strings (the user name, the home folder) in them. Returns the manifest."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    def anonymous(text: str) -> str:
        for old, new in sorted(replacements.items(), key=lambda kv: -len(kv[0])):
            if old:
                text = text.replace(old, new)
        return text

    used: set[str] = set()
    answers = []
    for r in recording.records:
        if r.exit_code == 127 and not r.stdout:
            answers.append({"command": r.command, "missing": True,
                            "note": "not found on the cluster when recorded"})
            continue
        answer = {"command": _stable_prefix(r.command)}
        if r.exit_code:
            answer["exit"] = r.exit_code
        if r.stdout:
            answer["file"] = _file_name(r.command, used)
            (folder / answer["file"]).write_text(anonymous(r.stdout))
        if r.stderr:
            answer["stderr"] = _file_name(r.command + " stderr", used)
            (folder / answer["stderr"]).write_text(anonymous(r.stderr))
        answers.append(answer)
    expect = {k: anonymous(v) if isinstance(v, str) else v for k, v in recording.expect.items()}
    manifest = folder / "cluster.toml"
    manifest.write_text(
        "# Recorded by `vwf clusters record`: the program's commands and the cluster's\n"
        "# answers, replayed by the tests' fake cluster (tests/fakes.py). Check the files\n"
        "# for anything personal (account and group names, paths) before committing.\n\n"
        + tomli_w.dumps({"expect": expect, "answer": answers}))
    return manifest
