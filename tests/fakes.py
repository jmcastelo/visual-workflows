"""A transport that answers commands from recorded outputs."""

from __future__ import annotations

from pathlib import Path

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
