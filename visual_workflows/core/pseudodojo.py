"""PseudoDojo's norm-conserving pseudopotentials: a library on this PC, filled
from PseudoDojo's tables as needed, and copied into a project's pseudos/
folder with where each file came from and its cutoff hints.

The tables are PseudoDojo's "latest version" repositories on GitHub (ONCVPSP
v0.4.1; https://github.com/PseudoDojo), each pinned here to a commit so a
download always gives the same file: PBE and PBEsol, scalar relativistic (SR)
or fully relativistic (FR, spin–orbit), each in two accuracies, standard and
stringent. Per element they hold `X_std.upf` and `X_str.upf` (the tables'
two choices: the md5 sums of their psp8 twins are the tables'), and per
table a `standard.djson` / `stringent.djson` with each element's valence and
cutoff hints (low, normal, high ecut, in Ha: twice that in Ry for pw.x). LDA
is not offered: its tables have UPF files for few elements.

Licence: CC BY 4.0 (PseudoDojo's website). Cite M. J. van Setten et al.,
Comput. Phys. Commun. 226, 39 (2018).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import tomli_w

from ..yambopy_ext.qepy.upf import UpfError, read_upf_header
from .paths import pseudo_library_dir

LICENSE = "CC BY 4.0"
CITATION = "M. J. van Setten et al., Comput. Phys. Commun. 226, 39 (2018)"
SOURCES_FILE = "sources.toml"        # in a project's pseudos/ folder
LEVELS = ("low", "normal", "high")
_RAW = "https://raw.githubusercontent.com/PseudoDojo"


class PseudoDojoError(RuntimeError):
    """A table or file that cannot be had (no network, no such element...)."""


@dataclass(frozen=True)
class Table:
    xc: str                          # "PBE", "PBEsol"
    relativistic: str                # "SR", "FR"
    accuracy: str                    # "standard", "stringent"
    repo: str
    commit: str

    @property
    def id(self) -> str:
        """PseudoDojo's own name for the table, e.g. nc-sr-04_pbe_standard."""
        return f"nc-{self.relativistic.lower()}-04_{self.xc.lower()}_{self.accuracy}"

    @property
    def title(self) -> str:
        kind = "fully relativistic" if self.relativistic == "FR" else "scalar relativistic"
        return f"PseudoDojo {self.xc}, {kind}, {self.accuracy}"

    def url(self, path: str) -> str:
        return f"{_RAW}/{self.repo}/{self.commit}/{path}"

    def upf_path(self, element: str) -> str:
        tag = "std" if self.accuracy == "standard" else "str"
        return f"{element}/{element}_{tag}.upf"

    def file_name(self, element: str) -> str:
        """The file's name in the library and in a project."""
        return f"{element}.{self.id}.upf"


_COMMITS = {("PBE", "SR"): ("ONCVPSP-PBE-SR", "823b18f7701b6303e0e69114eaa645173f706600"),
            ("PBE", "FR"): ("ONCVPSP-PBE-FR", "777dce9786644b83e6c3686f1a24e06c21f141d5"),
            ("PBEsol", "SR"): ("ONCVPSP-PBEsol-SR", "a548e87ee70237ee42caa602abef395edead1d8b"),
            ("PBEsol", "FR"): ("ONCVPSP-PBEsol-FR", "5fe7c8b2d4e5824053a35f1e20b3de2982474354")}

TABLES = [Table(xc, rel, accuracy, repo, commit)
          for (xc, rel), (repo, commit) in _COMMITS.items() for accuracy in ("standard", "stringent")]


def table(xc: str = "PBE", relativistic: str = "SR", accuracy: str = "standard") -> Table:
    for t in TABLES:
        if (t.xc.lower(), t.relativistic.lower(), t.accuracy) == (xc.lower(), relativistic.lower(),
                                                                  accuracy.lower()):
            return t
    raise PseudoDojoError(f"no PseudoDojo table {xc} {relativistic} {accuracy} here (there are "
                          "PBE and PBEsol, SR and FR, standard and stringent)")


def table_by_id(table_id: str) -> Table:
    for t in TABLES:
        if t.id == table_id:
            return t
    raise PseudoDojoError(f"no PseudoDojo table {table_id!r}")


@dataclass
class Hints:
    """An element's cutoff hints (ecutwfc, Ry; None where its table gives none:
    PBE FR stringent for Li, Ge, Sb, Te) and valence, from its table."""
    low: float | None
    normal: float | None
    high: float | None
    z_valence: float

    def cutoff(self, level: str = "normal") -> float | None:
        return getattr(self, level)


@dataclass
class Entry:
    """One element's file of a table, in the library."""
    element: str
    table: Table
    path: Path
    hints: Hints
    sha256: str

    @property
    def url(self) -> str:
        return self.table.url(self.table.upf_path(self.element))


def _download(url: str) -> bytes:
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": "visual-workflows"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


class Library:
    """The tables and files downloaded so far, in `root` (by default the
    program's data folder): root/<table id>/table.djson and <element>.<table id>.upf."""

    def __init__(self, root: Path | None = None, download: Callable[[str], bytes] = _download):
        self.root = Path(root) if root is not None else pseudo_library_dir()
        self.download = download

    def _get(self, url: str) -> bytes:
        try:
            return self.download(url)
        except OSError as e:     # urllib's errors are OSErrors
            raise PseudoDojoError(f"could not download {url} ({getattr(e, 'reason', e)})") from None

    def hints(self, table: Table) -> dict[str, Hints]:
        """Every element of the table with its hints (the table's file, downloaded once)."""
        path = self.root / table.id / "table.djson"
        if not path.exists():
            data = self._get(table.url(f"{table.accuracy}.djson"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))["pseudos_metadata"]
            return {element: Hints(*(_ry(m.get("hints", {}).get(level)) for level in LEVELS),
                                   float(m["Z_val"]))
                    for element, m in metadata.items()}
        except (ValueError, KeyError, TypeError) as e:
            path.unlink(missing_ok=True)
            raise PseudoDojoError(f"{table.title}: its table could not be read ({e})") from None

    def get(self, table: Table, elements) -> list[Entry]:
        """The elements' files, downloaded into the library when not there yet,
        each checked: a UPF file for that element, with the table's valence,
        fully relativistic exactly when the table is."""
        hints = self.hints(table)
        entries = []
        for element in dict.fromkeys(elements):
            if element not in hints:
                raise PseudoDojoError(f"{element} is not in {table.title}")
            path = self.root / table.id / table.file_name(element)
            if path.exists():
                data = path.read_bytes()
            else:
                data = self._get(table.url(table.upf_path(element)))
                _check_file(data, element, hints[element], table)
                path.write_bytes(data)
            entries.append(Entry(element, table, path, hints[element], hashlib.sha256(data).hexdigest()))
        return entries


def _ry(hint) -> float | None:
    """A hint's ecut (Ha) in Ry."""
    return 2.0 * float(hint["ecut"]) if isinstance(hint, dict) and "ecut" in hint else None


def _check_file(data: bytes, element: str, hints: Hints, table: Table) -> None:
    try:
        header = read_upf_header(data.decode("utf-8", errors="replace"))
    except UpfError as e:
        raise PseudoDojoError(f"{element} of {table.title}: not a UPF file ({e})") from None
    if header.element.strip() != element:
        raise PseudoDojoError(f"{element} of {table.title}: the file is for {header.element.strip()}")
    if abs(header.z_valence - hints.z_valence) > 1e-6:
        raise PseudoDojoError(f"{element} of {table.title}: valence {header.z_valence:g}, the table "
                              f"says {hints.z_valence:g}")
    if bool(header.has_so) != (table.relativistic == "FR"):
        raise PseudoDojoError(f"{element} of {table.title}: the file is "
                              f"{'' if header.has_so else 'not '}fully relativistic")


# -- a project's pseudos/ folder ------------------------------------------------------------

@dataclass
class Source:
    """Where a project's file came from, as sources.toml keeps it."""
    file: str
    element: str
    table: str
    url: str
    sha256: str
    hints: Hints
    license: str = LICENSE
    citation: str = CITATION
    extra: dict = field(default_factory=dict)


def add_to_project(entries: list[Entry], folder: Path) -> list[str]:
    """Copy the files into a project's pseudos/ folder (made if needed) and
    record them in its sources.toml. Returns the files' names (for
    ATOMIC_SPECIES)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    sources = read_sources(folder, verify=False)
    names = []
    for entry in entries:
        name = entry.table.file_name(entry.element)
        shutil.copyfile(entry.path, folder / name)
        sources[name] = Source(name, entry.element, entry.table.id, entry.url, entry.sha256, entry.hints)
        names.append(name)
    _write_sources(folder, sources)
    return names


def read_sources(folder: Path, verify: bool = True) -> dict[str, Source]:
    """The files sources.toml records ({name: Source}); with `verify`, only
    those still the same file (an edited or replaced one loses its hints)."""
    path = Path(folder) / SOURCES_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}
    sources = {}
    for name, s in data.get("files", {}).items():
        try:
            h = s.get("ecutwfc_hints_ry", {})
            source = Source(name, s["element"], s["table"], s["url"], s["sha256"],
                            Hints(*(float(h[level]) if level in h else None for level in LEVELS),
                                  float(s["z_valence"])),
                            s.get("license", LICENSE), s.get("citation", CITATION))
        except (KeyError, TypeError, ValueError):
            continue
        if verify:
            try:
                if hashlib.sha256((Path(folder) / name).read_bytes()).hexdigest() != source.sha256:
                    continue
            except OSError:
                continue
        sources[name] = source
    return sources


def _write_sources(folder: Path, sources: dict[str, Source]) -> None:
    files = {}
    for name, s in sorted(sources.items()):
        files[name] = {"element": s.element, "table": s.table, "url": s.url, "sha256": s.sha256,
                       "z_valence": s.hints.z_valence,
                       "ecutwfc_hints_ry": {level: s.hints.cutoff(level) for level in LEVELS
                                            if s.hints.cutoff(level) is not None},
                       "license": s.license, "citation": s.citation}
    text = ("# Where these pseudopotentials came from (written by Visual Workflows).\n"
            "# Cutoff hints are PseudoDojo's, in Ry (its tables give Ha).\n"
            + tomli_w.dumps({"files": files}))
    (Path(folder) / SOURCES_FILE).write_text(text, encoding="utf-8")


@dataclass
class Cutoffs:
    ecutwfc: float | None            # Ry; None when no species has a hint
    ecutrho: float | None
    without_hints: list              # species whose table gives no hint


def suggested_cutoffs(hints: dict, level: str = "normal") -> Cutoffs:
    """ecutwfc and ecutrho (Ry) for the species ({species: Hints}): the
    highest hint at `level` among them, and 4 × it (norm-conserving)."""
    if level not in LEVELS:
        raise PseudoDojoError(f"level {level!r}: low, normal or high")
    known = [h.cutoff(level) for h in hints.values() if h.cutoff(level) is not None]
    without = [species for species, h in hints.items() if h.cutoff(level) is None]
    if not known:
        return Cutoffs(None, None, without)
    return Cutoffs(max(known), 4 * max(known), without)
