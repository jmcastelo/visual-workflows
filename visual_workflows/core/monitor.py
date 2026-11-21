"""Following jobs and their output.

watch() refreshes a project's steps until none is queued or running. Admins
notice heavy polling: the interval is never under MIN_INTERVAL, and each
refresh is one squeue call for all steps (plus a few file reads for jobs
Slurm has forgotten).

LogFollower reads a growing file on the cluster from where it last stopped.
"""

from __future__ import annotations

import asyncio

from .runner import Runner
from .transport import Transport, TransportError

MIN_INTERVAL = 30.0
DEFAULT_INTERVAL = 60.0


async def watch(runner: Runner, on_change, interval: float = DEFAULT_INTERVAL,
                min_interval: float = MIN_INTERVAL, on_quiet=None) -> None:
    """Refresh until no step is queued or running; on_change(steps) after each
    refresh that changed something (a state, a reason, a start: and once at
    the start); on_quiet(active steps) after one that changed nothing, so that
    a long wait still shows the watch is alive."""
    interval = max(interval, min_interval)
    first = True
    while True:
        changed = await runner.refresh()
        if changed or first:
            on_change(changed if changed else [s for s in runner.project.steps if s.jobs])
        elif on_quiet is not None:
            on_quiet([s for s in runner.project.steps if s.active])
        first = False
        if not any(s.active for s in runner.project.steps):
            return
        await asyncio.sleep(interval)


class LogFollower:
    def __init__(self, transport: Transport, path: str, tail: int = 4096):
        self.transport, self.path = transport, path
        self.tail = tail                 # bytes shown from the end at the start
        self.offset: int | None = None

    async def read_new(self) -> str:
        """What was added since the last call ("" if nothing; the last `tail`
        bytes on the first call). A file that shrank is read from its start."""
        info = await self.transport.stat(self.path)
        if info is None:
            return ""
        if self.offset is None:
            self.offset = max(0, info.size - self.tail)
        elif info.size < self.offset:
            self.offset = 0              # rewritten
        if info.size == self.offset:
            return ""
        try:
            data = await self.transport.read_range(self.path, self.offset, info.size - self.offset)
        except TransportError:
            return ""
        self.offset += len(data)
        return data.decode(errors="replace")
