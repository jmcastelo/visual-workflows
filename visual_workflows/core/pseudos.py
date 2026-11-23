"""Where pw.x will find a run's pseudopotentials, and their headers.

pw.x looks for each file of ATOMIC_SPECIES in `pseudo_dir` (input), else in
$ESPRESSO_PSEUDO, else in ~/espresso/pseudo (Modules/read_pseudo.f90); nscf
and bands runs look in outdir/prefix.save first, where the scf copied them.
Pseudopotentials can be kept:

- in the project's `pseudos/` folder: uploaded once to the project's folder on
  the cluster, next to the steps, and given to pw.x as $ESPRESSO_PSEUDO;
- in a library on the cluster named in the profile (`[paths] pseudos`): given
  as $ESPRESSO_PSEUDO when the project has no folder of its own;
- anywhere else through `pseudo_dir` in the input.

Files inside the project are read here; files on the cluster are read at
submission, over the connection (their first HEADER_BYTES only).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..yambopy_ext.qepy.pw_input import PwInput
from ..yambopy_ext.qepy.upf import HEADER_BYTES, UpfError, UpfHeader, read_upf_header

__all__ = ["HEADER_BYTES", "PROJECT_PSEUDOS", "PseudoFile", "espresso_pseudo", "pseudo_files",
           "read_local_header", "parse_header", "known_hints"]

PROJECT_PSEUDOS = "pseudos"          # the project's folder, next to the steps
DEFAULT_DIR = "espresso/pseudo"      # pw.x's default, in the home folder


@dataclass
class PseudoFile:
    """Where pw.x will look for one species' file."""
    species: str
    name: str                        # as in ATOMIC_SPECIES
    local: Path | None               # the file on this PC, when it is inside the project
    remote: str                      # where pw.x looks: relative to the step's folder on
                                     # the cluster, or absolute (may start with $ or ~)
    default_dir: bool = False        # pw.x's own default: the cluster may set another


def project_pseudos(step_folder: Path) -> Path:
    return Path(step_folder).parent / PROJECT_PSEUDOS


def espresso_pseudo(step_folder: Path, library: str) -> str:
    """What a step's job script sets $ESPRESSO_PSEUDO to ("" for nothing): the
    project's folder if it has one, else the profile's library."""
    if project_pseudos(step_folder).is_dir():
        return f"$SLURM_SUBMIT_DIR/../{PROJECT_PSEUDOS}"
    return library.strip()


def pseudo_files(pw: PwInput, step_folder: Path, library: str = "") -> list[PseudoFile]:
    """Where pw.x will look for each file named in ATOMIC_SPECIES."""
    species = pw.cards.get("ATOMIC_SPECIES")
    if species is None:
        return []
    step_folder = Path(step_folder)
    project = step_folder.parent
    pseudo_dir = pw.get("control", "pseudo_dir")
    default = False
    if isinstance(pseudo_dir, str) and pseudo_dir.strip():
        directory = pseudo_dir.strip()
        if "$" in directory or directory.startswith("~"):
            return []                # pw.x does not expand them: check_pw_input says so
    elif project_pseudos(step_folder).is_dir():
        directory = f"../{PROJECT_PSEUDOS}"
    elif library.strip():
        directory = library.strip()
    else:
        directory, default = "~/" + DEFAULT_DIR, True
    files = []
    for line in species.lines:
        words = line.split()
        if len(words) < 3:
            continue
        name = words[2]
        remote = str(PurePosixPath(directory) / name)
        local = None
        if not directory.startswith(("/", "$", "~")):
            candidate = (step_folder / directory / name).resolve()
            if candidate.is_relative_to(project.resolve()):
                local = candidate
        files.append(PseudoFile(words[0], name, local, remote, default))
    return files


def read_local_header(path: Path) -> UpfHeader | str | None:
    """The header of a file on this PC; a message if unreadable; None if missing."""
    try:
        with open(path, "rb") as f:
            data = f.read(HEADER_BYTES)
            if b"<PP_HEADER>" in data and b"</PP_NONLOCAL>" not in data:
                data += f.read()     # UPF 1: spin–orbit data come at the end
    except FileNotFoundError:
        return None
    except OSError as e:
        return f"cannot be read ({e.strerror or e})"
    return parse_header(data)


def parse_header(data: bytes) -> UpfHeader | str:
    try:
        return read_upf_header(data.decode("utf-8", errors="replace"))
    except UpfError as e:
        return f"is not a pseudopotential pw.x reads ({e})"


def known_hints(files: list[PseudoFile], step_folder: Path) -> dict:
    """{species: Hints} for the files in the project's pseudos/ folder whose
    source is recorded there (sources.toml) and that are still that file."""
    from .pseudodojo import read_sources
    folder = project_pseudos(step_folder)
    sources = read_sources(folder) if folder.is_dir() else {}
    return {f.species: sources[f.name].hints for f in files
            if f.local is not None and f.name in sources
            and f.local.resolve() == (folder / f.name).resolve()}
