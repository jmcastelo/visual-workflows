"""The band structure along a path, as the QE chain's bands step computes it
(pw.x `calculation = 'bands'` on a `K_POINTS crystal_b` path).

The energies come from QE's XML (`data-file-schema.xml`): the copy the bands
step keeps in its folder (`bands.xml`, qe_chain.BANDS_XML), downloaded into
its `outputs/`; for steps made before that copy, the scf step's downloaded
XML in the outdir they share, when its last run was this bands run. The path
comes from the step's input: its special points, named again by matching
them with the lattice's (QE's writer keeps no comments), and its sections
(a point followed by 1 k-point jumps to the next).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .results import HARTREE_EV

BOHR_ANGSTROM = 0.529177210903
# Special points match the lattice's within this (reduced coordinates)
LABEL_TOLERANCE = 1e-4


class BandStructureError(Exception):
    """The band structure cannot be read."""


@dataclass
class QeBands:
    """What QE's XML holds about the bands."""
    calculation: str
    kpoints: np.ndarray              # (nk, 3), cartesian, 2π/alat
    energies: np.ndarray             # (spin, nk, nbnd), eV, as computed
    alat: float                      # bohr
    electrons: float
    highest_occupied: float | None   # eV (insulators)
    fermi: float | None              # eV (metals, or with smearing)
    noncolin: bool = False


def read_qe_xml(path) -> QeBands:
    """QE's data-file-schema.xml: its k-points and energies."""
    path = Path(path)
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        raise BandStructureError(f"{path}: {e}") from None
    out = root.find("output")
    bs = out.find("band_structure") if out is not None else None
    if bs is None:
        raise BandStructureError(f"{path}: no band structure in it")

    def number(element, name):
        e = element.find(name)
        return float(e.text) if e is not None and e.text else None

    lsda = (bs.findtext("lsda") or "false").strip() == "true"
    noncolin = (bs.findtext("noncolin") or "false").strip() == "true"
    kpoints, energies = [], []
    for ks in bs.findall("ks_energies"):
        kpoints.append([float(x) for x in ks.find("k_point").text.split()])
        energies.append([float(x) for x in ks.find("eigenvalues").text.split()])
    if not kpoints:
        raise BandStructureError(f"{path}: no k-points in it")
    e = np.array(energies) * HARTREE_EV
    if lsda:                         # up then down at each k-point
        nb = e.shape[1] // 2
        e = np.stack([e[:, :nb], e[:, nb:]])
    else:
        e = e[None]
    structure = out.find("atomic_structure")
    alat = float(structure.get("alat")) if structure is not None and structure.get("alat") else 0.0
    calculation = (root.findtext("input/control_variables/calculation") or "").strip()
    homo, fermi = number(bs, "highestOccupiedLevel"), number(bs, "fermi_energy")
    return QeBands(calculation, np.array(kpoints), e, alat, number(bs, "nelec") or 0.0,
                   homo * HARTREE_EV if homo is not None else None,
                   fermi * HARTREE_EV if fermi is not None else None, noncolin)


@dataclass
class BandStructure:
    x: np.ndarray                    # distance along the path, Å⁻¹ (2π included)
    energies: np.ndarray             # (spin, nk, nbnd), eV, zero at the valence top
    ticks: list                      # [(x, label)]: the special points
    occupied: int | None             # bands below the gap (None: a metal, or not known)
    source: Path
    breaks: list = field(default_factory=list)   # x where one section ends and the next starts
    jumps: list = field(default_factory=list)    # k-point indices (from 0) starting a new section
    zero: str = "the valence top"    # what energy 0 is

    @property
    def gap(self) -> tuple[float, float, float] | None:
        """(gap, x of the valence top, x of the conduction bottom) on the path."""
        if not self.occupied or self.occupied >= self.energies.shape[2]:
            return None
        e = self.energies
        top = e[:, :, :self.occupied].max(axis=(0, 2))
        bottom = e[:, :, self.occupied:].min(axis=(0, 2))
        i, j = int(top.argmax()), int(bottom.argmin())
        return float(bottom[j] - top[i]), float(self.x[i]), float(self.x[j])


def path_points(text: str) -> list[tuple[tuple, int]]:
    """The K_POINTS crystal_b card of a pw.x input: [((k1, k2, k3), n)]."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"\s*K_POINTS\b", line, re.I):
            if "crystal_b" not in line.lower():
                raise BandStructureError("the input's K_POINTS is not a crystal_b path")
            count = int(lines[i + 1].split()[0])
            points = []
            for row in lines[i + 2:i + 2 + count]:
                words = row.split("!")[0].split()
                points.append((tuple(float(x) for x in words[:3]), int(float(words[3]))))
            return points
    raise BandStructureError("the input has no K_POINTS")


def special_labels(structure, points) -> list[str]:
    """Each path point's name among the lattice's special points (Γ for G),
    or its coordinates."""
    try:
        from yambopy.bz.brillouin import BrillouinZone
        named = BrillouinZone(cell=structure.cell).high_symmetry_points
    except Exception:                                   # noqa: BLE001 - no names, coordinates then
        named = {}
    labels = []
    for k, _ in points:
        name = next((label for label, q in named.items()
                     if np.allclose(np.asarray(q, float), k, atol=LABEL_TOLERANCE)), None)
        if name is None:
            labels.append("(" + " ".join(f"{x:g}" for x in k) + ")")
        else:
            labels.append("Γ" if name in ("G", "Gamma", "GAMMA") else name)
    return labels


def _occupied(qe: QeBands) -> int | None:
    if qe.highest_occupied is None or not qe.electrons:
        return None
    per_band = 1 if qe.noncolin else 2
    n = qe.electrons / per_band
    return int(round(n)) if abs(n - round(n)) < 1e-6 else None


def band_structure(xml_path, input_text: str) -> BandStructure:
    """The band structure of a bands run: its XML and its pw.x input."""
    from ..yambopy_ext.qepy.pw_input import read_pw_input
    from .structure import Structure
    qe = read_qe_xml(xml_path)
    points = path_points(input_text)
    expected = sum(n for _, n in points[:-1]) + 1
    if qe.calculation and qe.calculation != "bands":
        raise BandStructureError(f"{xml_path}: its last run was a {qe.calculation}, not the bands")
    if len(qe.kpoints) != expected:
        raise BandStructureError(f"{xml_path}: {len(qe.kpoints)} k-points, but the path has "
                                 f"{expected}: another run's")
    # distances: |Δk| in Å⁻¹ (2π/alat units), none across a jump between sections
    scale = 2 * np.pi / (qe.alat * BOHR_ANGSTROM) if qe.alat else 1.0
    step = np.linalg.norm(np.diff(qe.kpoints, axis=0), axis=1) * scale
    starts, index = [], 0
    for _, n in points[:-1]:
        index += n
        starts.append((index, n == 1))
    jumps = {i for i, jump in starts if jump}           # k-point i starts a new section
    for i in jumps:
        step[i - 1] = 0.0
    x = np.concatenate([[0.0], np.cumsum(step)])
    try:
        names = special_labels(Structure.from_pw(read_pw_input(input_text)), points)
    except Exception:                                   # noqa: BLE001 - coordinates then
        names = ["(" + " ".join(f"{v:g}" for v in k) + ")" for k, _ in points]
    ticks, breaks, index = [], [], 0
    for i, ((_, n), name) in enumerate(zip(points, names)):
        if ticks and abs(ticks[-1][0] - x[index]) < 1e-9:      # a jump: "U|K"
            ticks[-1] = (ticks[-1][0], f"{ticks[-1][1]}|{name}")
            breaks.append(float(x[index]))
        else:
            ticks.append((float(x[index]), name))
        if i < len(points) - 1:
            index += n
    occupied = _occupied(qe)
    zero = qe.highest_occupied if qe.highest_occupied is not None else qe.fermi
    energies = qe.energies - (zero or 0.0)
    return BandStructure(x, energies, ticks, occupied, Path(xml_path), breaks, sorted(jumps),
                         "the valence top" if qe.highest_occupied is not None else
                         ("the Fermi energy" if qe.fermi is not None else "as computed"))


def find_bands_xml(project, step) -> Path | None:
    """The downloaded XML of a bands step's run: its own copy, or the scf
    step's when that holds this run (steps made before the copy)."""
    from ..yambopy_ext.qepy.pw_input import read_pw_input
    from .qe_chain import BANDS_XML
    from .project import OUTPUTS_DIR
    own = step.folder / OUTPUTS_DIR / BANDS_XML
    if own.is_file():
        return own
    run = next((r for r in (step.job.runs if step.job else []) if r.program == "qe" and r.input), None)
    if run is None or not (step.folder / run.input).is_file():
        return None
    pw = read_pw_input((step.folder / run.input).read_text(encoding="utf-8", errors="replace"))
    outdir = str(pw.get("control", "outdir", "./"))
    prefix = pw.get("control", "prefix", "pwscf")
    m = re.match(r"\.\./([^/]+)/(.+)$", outdir.rstrip("/"))
    if not m:
        return None
    shared = project.path / m.group(1) / OUTPUTS_DIR / m.group(2) / f"{prefix}.save" / "data-file-schema.xml"
    return shared if shared.is_file() else None


def step_band_structure(project, step) -> BandStructure:
    """A bands step's downloaded band structure."""
    path = find_bands_xml(project, step)
    if path is None:
        raise BandStructureError(f"{step.id}: no band structure downloaded (Get results, once "
                                 "the step has run)")
    run = next(r for r in step.job.runs if r.program == "qe" and r.input)
    return band_structure(path, (step.folder / run.input).read_text(encoding="utf-8", errors="replace"))


def is_bands_step(step) -> bool:
    """A step whose pw.x run computes bands along a path."""
    from ..yambopy_ext.qepy.pw_input import read_pw_input
    if step.job is None:
        return False
    for run in step.job.runs:
        if run.program == "qe" and run.input and (step.folder / run.input).is_file():
            try:
                pw = read_pw_input((step.folder / run.input).read_text(encoding="utf-8",
                                                                      errors="replace"))
            except Exception:                           # noqa: BLE001 - not a pw.x input
                continue
            if pw.get("control", "calculation") == "bands":
                return True
    return False
