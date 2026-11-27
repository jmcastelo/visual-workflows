"""Opening a project's results in Visual Excitons.

Visual Excitons opens a Visual Workflows project folder by itself: it reads
`project.toml`, searches each step's `outputs/` and asks which to open when
several steps have a SAVE, a BSE or a GW. From a step, the program says
which: `visual-excitons <project> --save DIR --bse DIR --qp PATH`, with the
directories that step's results came from (a BSE step's SAVE through its
`SAVE` link, its QP database through its input's `KfnQPdb`), and
`--compute` for a BSE step, so that its dispersion and its absorption at
Q = 0 are computed as it opens. Visual
Excitons checks them again and exits with 2 and a message on stderr when a
path holds the wrong thing.

It runs as a program of its own: the command `visual-excitons` on the PATH,
else `python -m visual_excitons.main` with this Python when the package is
installed beside this one (the suite's venv).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .project import OUTPUTS_DIR
from .results import bse_energies, local_path

COMMAND = "visual-excitons"


class ExcitonsError(RuntimeError):
    """Visual Excitons cannot be started, or there is nothing for it to open."""


def command() -> list[str]:
    """How Visual Excitons is started here."""
    found = shutil.which(COMMAND)
    if found:
        return [found]
    if importlib.util.find_spec("visual_excitons") is not None:
        return [sys.executable, "-m", "visual_excitons.main"]
    raise ExcitonsError("Visual Excitons is not installed here: pip install it into this "
                        "environment, or put visual-excitons on the PATH.")


@dataclass
class Opening:
    """What Visual Excitons is given, and what it will lack."""
    project: Path
    save: Path | None = None
    bse: Path | None = None
    qp: Path | None = None
    notes: list = field(default_factory=list)

    @property
    def lacks_wavefunctions(self) -> bool:
        """A BSE opened on a SAVE without its wave functions (ns.wf*): the
        real-space views need them."""
        return self.bse is not None and self.save is not None and not any(Path(self.save).glob("ns.wf*"))

    def save_step(self, project):
        """The step whose downloaded outputs hold the SAVE (None if none does)."""
        if self.save is None:
            return None
        save = Path(self.save).resolve()
        for step in project.steps:
            if save.is_relative_to((step.folder / OUTPUTS_DIR).resolve()):
                return step
        return None

    def arguments(self) -> list[str]:
        args = [str(Path(self.project).resolve())]
        for option, path in (("--save", self.save), ("--bse", self.bse), ("--qp", self.qp)):
            if path is not None:
                args += [option, str(Path(path).resolve())]
        if self.bse is not None:
            # a BSE step: its dispersion and absorption at Q = 0 shown at once
            # (Visual Excitons computes them on opening; Visual Excitons 2026-11-27+)
            args.append("--compute")
        return args


def _folders_with(outputs: Path, pattern: str) -> list[Path]:
    return sorted({p.parent for p in outputs.rglob(pattern)}) if outputs.is_dir() else []


def _save_of(project, step) -> Path | None:
    """The SAVE a step's results came from: its own (p2y), or the one it links."""
    own = _folders_with(step.folder / OUTPUTS_DIR, "ns.db1")
    if own:
        return own[0]
    target = (step.job.links if step.job else {}).get("SAVE")
    if target:
        local = local_path(project, step, target)
        if local is not None and (local / "ns.db1").is_file():
            return local
    return None


def opening(project, step=None) -> Opening:
    """What to give Visual Excitons for a project, or for one of its steps."""
    o = Opening(project.path)
    has_any = any(_folders_with(s.folder / OUTPUTS_DIR, "ns.db1")
                  or _folders_with(s.folder / OUTPUTS_DIR, "ndb.BS_diago_Q*") for s in project.steps)
    if step is None:
        if not has_any:
            raise ExcitonsError("Nothing downloaded for Visual Excitons yet: "
                                "vwf project get --for-excitons brings back what it reads.")
        return o
    outputs = step.folder / OUTPUTS_DIR
    o.save = _save_of(project, step)
    bse = _folders_with(outputs, "ndb.BS_diago_Q*")
    qp = sorted(outputs.rglob("ndb.QP")) if outputs.is_dir() else []
    if bse:
        o.bse = bse[0]
        if len(bse) > 1:
            o.notes.append(f"{step.id} has BSE results in {len(bse)} folders: opening "
                           f"{o.bse.relative_to(outputs)}.")
        energies, _, qp_path = bse_energies(project, step)
        if energies == "QP":
            if qp_path is not None and qp_path.is_file():
                o.qp = qp_path
            else:
                o.notes.append("The BSE read QP energies whose ndb.QP was not downloaded: "
                               "Visual Excitons opens it without them.")
    elif qp:
        o.qp = qp[0]
    if o.save is None:
        if not (o.bse or o.qp):
            raise ExcitonsError(f"{step.id} has nothing downloaded for Visual Excitons: "
                                "vwf project get --for-excitons brings back what it reads.")
        raise ExcitonsError(f"The SAVE of {step.id}'s results was not downloaded: "
                            "vwf project get --for-excitons.")
    if not any(o.save.glob("ns.wf*")):
        o.notes.append("The SAVE has no wave functions (ns.wf*): spectra and composition open, "
                       "the real-space views do not.")
    return o


@dataclass
class Started:
    pid: int | None
    message: str = ""                # what Visual Excitons said, when it stopped at once


def start(arguments: list[str], wait: float = 3.0) -> Started:
    """Visual Excitons started on its own (it outlives this program). Waits
    `wait` seconds for an early stop (a wrong path: exit code 2 and its
    message); after that it is left running."""
    argv = command() + arguments
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                                    start_new_session=True, close_fds=True, env=dict(os.environ))
        except OSError as e:
            raise ExcitonsError(f"Visual Excitons could not be started ({e})") from None
        end = time.monotonic() + wait
        while time.monotonic() < end:
            code = proc.poll()
            if code is not None:
                err.seek(0)
                text = err.read().decode("utf-8", "replace").strip()
                if code != 0:
                    raise ExcitonsError(text.splitlines()[-1] if text else f"Visual Excitons stopped ({code})")
                return Started(None, text)
            time.sleep(0.1)
    return Started(proc.pid)


def shell_line(arguments: list[str]) -> str:
    """The command, as one would type it."""
    from .shell import quote
    try:
        argv = command()
    except ExcitonsError:
        argv = [COMMAND]
    if Path(argv[0]).name == COMMAND:
        argv = [COMMAND]
    return " ".join(quote(a) for a in argv + arguments)
