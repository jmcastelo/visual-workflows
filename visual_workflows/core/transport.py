"""Running commands and moving files on a cluster.

`Transport` is what the rest of the core uses: SSHTransport (in ssh.py) for
real clusters, LocalTransport for tests and for running on the PC itself.
Everything is async, so the GUI never waits on a slow cluster.

Remote paths are POSIX strings; relative ones are relative to the remote home.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat as statmod
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .shell import login_shell


# Download chunk size
CHUNK = 1 << 20


class TransportError(Exception):
    """The cluster could not be reached, or an operation on it failed."""


class AuthenticationError(TransportError):
    """Login refused, cancelled, or a key could not be decrypted."""


class HostKeyError(TransportError):
    """The server's host key is unknown and was not accepted, or has changed."""

    def __init__(self, message: str, *, changed: bool = False):
        super().__init__(message)
        self.changed = changed


class CommandTimeout(TransportError):
    """A command did not finish in time."""


@dataclass(frozen=True)
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def check(self) -> "CommandResult":
        """Return self, or raise TransportError if the command failed."""
        if not self.ok:
            detail = self.stderr.strip() or self.stdout.strip()
            raise TransportError(
                f"Command failed (exit code {self.exit_code}): {self.command}"
                + (f"\n{detail}" if detail else "")
            )
        return self


@dataclass(frozen=True)
class FileInfo:
    path: str          # as given, joined with the name for directory listings
    name: str
    size: int
    mtime: float       # seconds since the epoch
    is_dir: bool
    owner: str = ""    # the owner's user name ("" when not known)


class Transport(ABC):
    """Commands and files on one cluster, through one connection."""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    async def run(self, command: str, *, timeout: float | None = 60,
                  login: bool = True) -> CommandResult:
        """Run a command; with login=True, in a login shell (see shell.py).

        A non-zero exit code is not an error: check result.ok, or call
        result.check().
        """

    @abstractmethod
    async def home(self) -> str: ...

    @abstractmethod
    async def stat(self, path: str) -> FileInfo | None:
        """File information, or None if the path does not exist."""

    @abstractmethod
    async def listdir(self, path: str) -> list[FileInfo]: ...

    @abstractmethod
    async def makedirs(self, path: str) -> None:
        """Create a directory and its parents; no error if it exists."""

    @abstractmethod
    async def put(self, local: str | os.PathLike, remote: str) -> None:
        """Upload one file (not a directory)."""

    @abstractmethod
    async def get(self, remote: str, local: str | os.PathLike) -> None:
        """Download one file (not a directory); local parents are created."""

    @abstractmethod
    async def read_range(self, remote: str, offset: int, size: int) -> bytes:
        """Up to `size` bytes from `offset` (fewer at the end of the file)."""

    @abstractmethod
    async def download(self, remote: str, local: str | os.PathLike, offset: int = 0,
                       progress=None) -> None:
        """Append the remote file from `offset` on to `local` (which must then
        hold exactly `offset` bytes). progress(n) is called with each chunk's size."""

    @abstractmethod
    async def write_text(self, remote: str, text: str) -> None:
        """Write a small text file (job scripts, inputs); parents are created."""

    @abstractmethod
    async def read_text(self, remote: str) -> str:
        """Read a small text file (logs, outputs)."""

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()


class LocalTransport(Transport):
    """A "cluster" on this PC: for tests, and for running locally.

    `home` plays the remote home: relative paths resolve against it, and
    commands run with HOME set to it, so a login shell does not read the
    user's real shell setup.
    """

    def __init__(self, home: str | os.PathLike):
        self._home = Path(home).resolve()
        self._connected = False

    async def connect(self) -> None:
        if not self._home.is_dir():
            raise TransportError(f"Not a directory: {self._home}")
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _path(self, path: str) -> Path:
        p = PurePosixPath(path)
        return Path(p) if p.is_absolute() else self._home / p

    async def run(self, command, *, timeout=60, login=True):
        self._require()
        line = login_shell(command) if login else command
        env = dict(os.environ, HOME=str(self._home))
        proc = await asyncio.create_subprocess_shell(
            line, cwd=self._home, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise CommandTimeout(f"Timed out after {timeout} s: {command}") from None
        return CommandResult(command, proc.returncode,
                             out.decode(errors="replace"), err.decode(errors="replace"))

    async def home(self):
        return str(self._home)

    def _info(self, given: str, p: Path, st: os.stat_result) -> FileInfo:
        try:
            import pwd
            owner = pwd.getpwuid(st.st_uid).pw_name
        except (ImportError, KeyError):
            owner = ""
        return FileInfo(given, p.name, st.st_size, st.st_mtime, statmod.S_ISDIR(st.st_mode), owner)

    async def stat(self, path):
        self._require()
        p = self._path(path)
        try:
            return self._info(path, p, p.stat())
        except FileNotFoundError:
            return None

    async def listdir(self, path):
        self._require()
        base = self._path(path)
        try:
            entries = sorted(base.iterdir())
        except FileNotFoundError:
            raise TransportError(f"No such directory: {path}") from None
        return [self._info(str(PurePosixPath(path) / e.name), e, e.stat()) for e in entries]

    async def makedirs(self, path):
        self._require()
        self._path(path).mkdir(parents=True, exist_ok=True)

    async def put(self, local, remote):
        self._require()
        dest = self._path(remote)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, dest)

    async def get(self, remote, local):
        self._require()
        src = self._path(remote)
        if not src.is_file():
            raise TransportError(f"No such file: {remote}")
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, local)

    async def read_range(self, remote, offset, size):
        self._require()
        try:
            with open(self._path(remote), "rb") as f:
                f.seek(offset)
                return f.read(size)
        except FileNotFoundError:
            raise TransportError(f"No such file: {remote}") from None

    async def download(self, remote, local, offset=0, progress=None):
        self._require()
        try:
            src = open(self._path(remote), "rb")
        except FileNotFoundError:
            raise TransportError(f"No such file: {remote}") from None
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        with src, open(local, "r+b" if offset else "wb") as dst:
            src.seek(offset)
            dst.seek(offset)
            dst.truncate()
            while chunk := src.read(CHUNK):
                dst.write(chunk)
                if progress:
                    progress(len(chunk))

    async def write_text(self, remote, text):
        self._require()
        dest = self._path(remote)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)

    async def read_text(self, remote):
        self._require()
        try:
            return self._path(remote).read_text(errors="replace")
        except FileNotFoundError:
            raise TransportError(f"No such file: {remote}") from None

    def _require(self):
        if not self._connected:
            raise TransportError("Not connected")
