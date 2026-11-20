"""Bringing a step's results from the cluster: scan, choose, download.

The cluster folder is listed in one command (`find -printf`); files are
chosen by mode and patterns, compared with the step's local outputs/ folder
(same size and time: already there), and downloaded into a ".vwf-part" file,
renamed when complete, so an interrupted download resumes and a partial file
is never taken for a finished one.

Modes:
  default    everything except wave functions and files over 1 GB
  all        everything
  excitons   what Visual Excitons reads: SAVE/ns.db1 and SAVE/ns.wf* (for the
             real-space views), ndb.BS_diago_Q*, every ndb.QP* (QP databases
             are recognised by content and may be renamed)
--include adds files (also over the size limit), --exclude removes them.
Patterns without "/" match file names; with "/", paths from the step folder.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .shell import quote
from .transport import Transport, TransportError

PART_SUFFIX = ".vwf-part"
DEFAULT_MAX_SIZE = 1 << 30            # 1 GB

# Kohn-Sham wave functions: QE's outdir and Yambo's SAVE
WAVEFUNCTIONS = ("*.save/wfc*", "*.wfc[0-9]*", "SAVE/ns.wf", "SAVE/ns.wf_*",
                 "*/SAVE/ns.wf", "*/SAVE/ns.wf_*")
FOR_EXCITONS = ("SAVE/ns.db1", "*/SAVE/ns.db1", "SAVE/ns.wf", "SAVE/ns.wf_*",
                "*/SAVE/ns.wf", "*/SAVE/ns.wf_*", "ndb.BS_diago_Q*", "ndb.QP*")
# Never brought back: the program's own status files
_OWN = (".vwf/*",)

MODES = ("default", "all", "excitons")


@dataclass(frozen=True)
class RemoteFile:
    path: str          # relative to the scanned folder, POSIX
    size: int
    mtime: float


async def scan(transport: Transport, folder: str) -> list[RemoteFile]:
    """Every file under folder, in one command (GNU find, as on Linux clusters)."""
    r = await transport.run(f"find {quote(folder)} -type f -printf '%s %T@ %P\\0'",
                            timeout=600)
    if not r.ok:
        raise TransportError(f"Cannot list {folder}: {r.stderr.strip() or r.stdout.strip()}")
    files = []
    for entry in r.stdout.split("\0"):
        if not entry:
            continue
        size, mtime, path = entry.split(" ", 2)
        files.append(RemoteFile(path, int(size), float(mtime)))
    return sorted(files, key=lambda f: f.path)


def matches(path: str, patterns) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch.fnmatchcase(path if "/" in p else name, p) for p in patterns)


@dataclass
class Choice:
    file: RemoteFile
    reason: str = ""             # why it is skipped; empty when chosen


def choose(files: list[RemoteFile], mode: str = "default", include=(), exclude=(),
           max_size: int | None = DEFAULT_MAX_SIZE) -> list[Choice]:
    if mode not in MODES:
        raise ValueError(f"Unknown download mode {mode!r} (one of {', '.join(MODES)})")
    out = []
    for f in files:
        reason = ""
        if matches(f.path, _OWN):
            continue
        if matches(f.path, exclude):
            reason = "excluded"
        elif matches(f.path, include):
            reason = ""
        elif mode == "excitons":
            reason = "" if matches(f.path, FOR_EXCITONS) else "not for Visual Excitons"
        elif mode == "default":
            if matches(f.path, WAVEFUNCTIONS):
                reason = "wave functions"
            elif max_size is not None and f.size > max_size:
                reason = f"over {human(max_size)}"
        out.append(Choice(f, reason))
    return out


@dataclass
class Item:
    file: RemoteFile
    local: Path
    offset: int = 0              # bytes already downloaded (resuming)
    have: bool = False           # already there, unchanged


def plan(choices: list[Choice], local_root: Path) -> list[Item]:
    items = []
    for c in choices:
        if c.reason:
            continue
        local = local_root / c.file.path
        item = Item(c.file, local)
        if local.is_file():
            st = local.stat()
            item.have = st.st_size == c.file.size and abs(st.st_mtime - c.file.mtime) < 2
        part = _part(local)
        if not item.have and part.is_file() and part.stat().st_size < c.file.size:
            item.offset = part.stat().st_size
        items.append(item)
    return items


def _part(local: Path) -> Path:
    return local.with_name(local.name + PART_SUFFIX)


async def fetch(transport: Transport, remote_root: str, item: Item, progress=None) -> None:
    """Download one file (resuming a partial one), then give it the remote time."""
    part = _part(item.local)
    await transport.download(f"{remote_root}/{item.file.path}", part, item.offset, progress)
    if part.stat().st_size != item.file.size:
        raise TransportError(f"{item.file.path}: got {part.stat().st_size} bytes of "
                             f"{item.file.size} (did it change on the cluster?)")
    os.utime(part, (item.file.mtime, item.file.mtime))
    os.replace(part, item.local)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def parse_size(text: str) -> int:
    """ "1G", "500M", "2.5GB", "1024" (bytes) -> bytes."""
    t = text.strip().upper().removesuffix("B")
    factor = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}.get(t[-1:], 1)
    number = t[:-1] if t[-1:] in "KMGT" else t
    try:
        return int(float(number) * factor)
    except ValueError:
        raise ValueError(f"Not a size: {text!r} (e.g. 500M, 2G)") from None
