"""Results read back from what was downloaded (each step's `outputs/`), and
judged for consistency.

Three Yambo databases, netCDF files read with netCDF4 (YamboPy's own
dependency), with no yambo or ypp run:

- the SAVE's `ns.db1` (p2y): electrons, bands, k-points and the DFT
  eigenvalues (Ha, absolute), so the occupied bands and the gaps on the grid;
- `ndb.QP` (a GW run): for each state of its table (band, k-point), the DFT
  energy `QP_Eo` (Ha, zero at the valence-band top), the QP energy `QP_E`
  (complex: the imaginary part is the width) and the renormalisation `QP_Z`;
- `ndb.BS_diago_Q<n>` (a BSE solved by diagonalisation): the excitons'
  energies `BS_Energies` (Ha) and their left and right residuals, whose
  product gives each one's strength in absorption (as YamboPy's
  `YamboExcitonDB`).

The checks say what looks inconsistent and why. They never judge
convergence: a converged result and an unconverged one look alike from the
databases (see the design, Phase 5, decision 2).
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

HARTREE_EV = 27.211386245988

# An exciton counts as bright above this share of the strongest one
BRIGHT_SHARE = 0.05
# Broadening of the absorption rebuilt from the residuals (yambo's BDmRange default)
BROADENING_EV = 0.1
# Two energies closer than this are one level (degenerate states)
SAME_EV = 0.005


class ResultsError(RuntimeError):
    """A database that cannot be read (missing, not a netCDF file, not what its name says)."""


def _dataset(path):
    try:
        from netCDF4 import Dataset
    except ImportError as e:                       # netCDF4 comes with YamboPy
        raise ResultsError(f"reading Yambo databases needs netCDF4 ({e})") from None
    try:
        return Dataset(str(path))
    except (OSError, RuntimeError) as e:
        raise ResultsError(f"{path}: not a readable netCDF file ({e})") from None


def _array(ds, name, path):
    if name not in ds.variables:
        raise ResultsError(f"{path}: no {name} (not the database its name says?)")
    return np.asarray(ds[name][:])


def _complex(a: np.ndarray) -> np.ndarray:
    """A netCDF complex array (a last axis of 2: real, imaginary) as complex."""
    a = np.asarray(a, dtype=float)
    return a[..., 0] + 1j * a[..., 1] if a.ndim > 1 and a.shape[-1] == 2 else a.astype(complex)


# -- the SAVE --------------------------------------------------------------------------

@dataclass
class Gap:
    """A gap on the k-points at hand (eV): the valence top at `k_valence`,
    the conduction bottom at `k_conduction` (k-point indices from 1)."""
    value: float
    k_valence: int
    k_conduction: int

    @property
    def direct(self) -> bool:
        return self.k_valence == self.k_conduction


@dataclass
class SaveSummary:
    path: Path
    electrons: float
    nbnd: int
    nk_ibz: int
    spin_channels: int               # 2 for nspin = 2
    spinors: int                     # 2 for noncollinear runs
    occupied: int | None             # bands below the gap (None: spin-polarised, or a metal)
    energies: np.ndarray             # (spin, k, band), eV, zero at the valence top when known
    indirect: Gap | None = None
    direct: Gap | None = None
    metal: bool = False


def read_save(path) -> SaveSummary:
    """The SAVE's `ns.db1`: `path` is the file or the SAVE folder."""
    path = Path(path)
    if path.is_dir():
        path = path / "ns.db1"
    if not path.is_file():
        raise ResultsError(f"no {path}")
    with _dataset(path) as ds:
        dims = _array(ds, "DIMENSIONS", path)
        energies = _array(ds, "EIGENVALUES", path).astype(float) * HARTREE_EV
    # As YamboPy's YamboLatticeDB reads them
    spinors, spin, electrons = int(dims[11]), int(dims[12]), float(dims[14])
    if energies.ndim == 2:
        energies = energies[None]
    nspin, nk, nbnd = energies.shape
    occupied = None
    if spin == 1 and abs(electrons - round(electrons)) < 1e-6:
        per_band = 1 if spinors == 2 else 2
        if round(electrons) % per_band == 0:
            occupied = int(round(electrons)) // per_band
    save = SaveSummary(path, electrons, nbnd, nk, spin, spinors, occupied, energies)
    if occupied is not None and 0 < occupied < nbnd:
        e = energies[0]
        valence, conduction = e[:, occupied - 1], e[:, occupied]
        top = float(valence.max())
        save.energies = energies - top
        kv, kc = int(valence.argmax()), int(conduction.argmin())
        save.indirect = Gap(float(conduction[kc] - top), kv + 1, kc + 1)
        jumps = conduction - valence
        kd = int(jumps.argmin())
        save.direct = Gap(float(jumps[kd]), kd + 1, kd + 1)
        save.metal = save.indirect.value <= 0
    return save


# -- GW --------------------------------------------------------------------------------

@dataclass
class BandShift:
    band: int
    mean: float                      # QP − DFT, eV, over the band's k-points
    low: float
    high: float


@dataclass
class QpSummary:
    path: Path
    bands: tuple[int, int]
    nk: int
    approximation: str               # e.g. PPA
    solver: str                      # e.g. Newton
    shifts: list[BandShift]
    z_range: tuple[float, float]
    width_max: float                 # the largest |Im E|, eV
    dft: np.ndarray                  # (state,) eV
    qp: np.ndarray                   # (state,) eV, real part
    band_of: np.ndarray              # (state,)
    k_of: np.ndarray                 # (state,), from 1
    indirect: tuple[Gap, Gap] | None = None     # DFT, QP on the table's k-points
    direct: tuple[Gap, Gap] | None = None

    def energies(self, which: str = "qp") -> dict:
        """{(band, k): energy} of the table, DFT ("dft") or QP ("qp")."""
        values = self.qp if which == "qp" else self.dft
        return {(int(b), int(k)): float(e) for b, k, e in zip(self.band_of, self.k_of, values)}


def _text(ds, name) -> str:
    if name not in ds.variables:
        return ""
    raw = np.ravel(ds[name][:])
    out = b"".join(x if isinstance(x, bytes) else bytes(x) for x in raw if x is not np.ma.masked)
    return out.decode("ascii", "replace").strip().strip("\x00").strip()


def _gaps(energy: dict, occupied: int):
    """The indirect and direct gaps of {(band, k): E} (None without both bands)."""
    ks = sorted({k for (b, k) in energy if b == occupied} & {k for (b, k) in energy if b == occupied + 1})
    if not ks:
        return None, None
    valence = {k: energy[(occupied, k)] for k in ks}
    conduction = {k: energy[(occupied + 1, k)] for k in ks}
    kv = max(valence, key=valence.get)
    kc = min(conduction, key=conduction.get)
    kd = min(ks, key=lambda k: conduction[k] - valence[k])
    return (Gap(conduction[kc] - valence[kv], kv, kc),
            Gap(conduction[kd] - valence[kd], kd, kd))


def read_qp(path, occupied: int | None = None) -> QpSummary:
    """A GW run's `ndb.QP`; `occupied` (the SAVE's) gives the gaps. Without
    it, the valence bands are those at or below yambo's zero."""
    path = Path(path)
    if not path.is_file():
        raise ResultsError(f"no {path}")
    with _dataset(path) as ds:
        table = _array(ds, "QP_table", path)
        e = _complex(_array(ds, "QP_E", path)) * HARTREE_EV
        eo = _complex(_array(ds, "QP_Eo", path)).real * HARTREE_EV
        z = _complex(_array(ds, "QP_Z", path)).real if "QP_Z" in ds.variables else np.ones(len(eo))
        approximation = _text(ds, "QP_GW_approximation")
        solver = _text(ds, "QP_GW_solver")
    if table.shape[0] != len(eo) and table.shape[-1] == len(eo):
        table = table.T                              # (3, n) as written: (n, 3), band band k
    band_of, k_of = table[:, 0].astype(int), table[:, -1].astype(int)
    shifts = []
    for b in sorted(set(band_of)):
        d = (e.real - eo)[band_of == b]
        shifts.append(BandShift(int(b), float(d.mean()), float(d.min()), float(d.max())))
    qp = QpSummary(path, (int(band_of.min()), int(band_of.max())), len(set(k_of)), approximation, solver,
                   shifts, (float(z.min()), float(z.max())), float(np.abs(e.imag).max()),
                   eo, e.real, band_of, k_of)
    if occupied is None:
        below = [b for b in sorted(set(band_of)) if eo[band_of == b].max() <= 1e-3]
        occupied = max(below) if below else None
    if occupied is not None:
        dft_i, dft_d = _gaps(qp.energies("dft"), occupied)
        qp_i, qp_d = _gaps(qp.energies("qp"), occupied)
        if dft_i is not None:
            qp.indirect, qp.direct = (dft_i, qp_i), (dft_d, qp_d)
    return qp


# -- BSE -------------------------------------------------------------------------------

@dataclass
class Peak:
    energy: float                    # eV
    height: float                    # relative to the highest peak


@dataclass
class BseSummary:
    path: Path
    q_index: int                     # from 1 (ndb.BS_diago_Q1: Q = 0)
    bands: tuple[int, int] | None
    energies: np.ndarray             # eV, ascending
    strengths: np.ndarray            # relative to the strongest, same order
    peaks: list[Peak] = field(default_factory=list)
    broadening: float = BROADENING_EV

    @property
    def lowest(self) -> float:
        return float(self.energies[0])

    @property
    def bright(self) -> np.ndarray:
        return self.strengths >= BRIGHT_SHARE

    @property
    def lowest_bright(self) -> float | None:
        bright = self.energies[self.bright]
        return float(bright[0]) if len(bright) else None

    @property
    def dark(self) -> int:
        return int((~self.bright).sum())


def absorption(energies, strengths, broadening: float = BROADENING_EV, grid=None):
    """The absorption rebuilt from the excitons (each a Lorentzian of
    half-width `broadening`, weighted by its strength): (ω, spectrum), the
    spectrum's highest point 1. A shape for finding peaks, not yambo's ε₂."""
    energies, strengths = np.asarray(energies, float), np.asarray(strengths, float)
    if grid is None:
        low = max(0.0, energies.min() - 5 * broadening)
        grid = np.linspace(low, energies.max() + 5 * broadening, 2001)
    w = np.asarray(grid, float)
    spectrum = (strengths[None, :] * broadening / ((w[:, None] - energies[None, :]) ** 2 + broadening ** 2)).sum(1)
    top = spectrum.max()
    return w, spectrum / top if top > 0 else spectrum


def find_peaks(w, spectrum, least: float = 0.02) -> list[Peak]:
    """The local maxima of a spectrum above `least` of the highest."""
    s = np.asarray(spectrum)
    top = s.max() if len(s) else 0
    found = [Peak(float(w[i]), float(s[i] / top)) for i in range(1, len(s) - 1)
             if s[i] > s[i - 1] and s[i] >= s[i + 1] and top > 0 and s[i] >= least * top]
    return found


def read_bse(path, broadening: float = BROADENING_EV) -> BseSummary:
    """A BSE's `ndb.BS_diago_Q<n>`, its excitons and the peaks of the
    absorption rebuilt from them."""
    path = Path(path)
    if not path.is_file():
        raise ResultsError(f"no {path}")
    m = re.search(r"_Q(\d+)$", path.name)
    with _dataset(path) as ds:
        energies = _complex(_array(ds, "BS_Energies", path)).real * HARTREE_EV
        left = _complex(_array(ds, "BS_left_Residuals", path))
        right = _complex(_array(ds, "BS_right_Residuals", path))
        bands = tuple(int(b) for b in np.ravel(ds["Bands"][:])[:2]) if "Bands" in ds.variables else None
    strength = np.abs(left * right)
    order = np.argsort(energies)
    energies, strength = energies[order], strength[order]
    top = strength.max()
    strengths = strength / top if top > 0 else strength
    bse = BseSummary(path, int(m.group(1)) if m else 1, bands, energies, strengths, broadening=broadening)
    if top > 0:
        bse.peaks = find_peaks(*absorption(energies, strengths, broadening))
    return bse


# -- checks ----------------------------------------------------------------------------

@dataclass
class Finding:
    level: str                       # "warning" or "note"
    text: str


def check_save(save: SaveSummary) -> list[Finding]:
    out = []
    if save.occupied is None:
        out.append(Finding("note", "The occupied bands are not counted (spin-polarised, or a fractional "
                                   "number of electrons): no gaps."))
    elif save.metal:
        out.append(Finding("warning", f"No gap on the grid ({save.indirect.value:.2f} eV): a metal, or a "
                                      "wrong occupation. The chain's GW and BSE band windows assume an insulator."))
    elif save.occupied >= save.nbnd:
        out.append(Finding("warning", "The SAVE has no empty band: GW and BSE need them (nbnd in the nscf)."))
    return out


def check_qp(qp: QpSummary, occupied: int | None) -> list[Finding]:
    out = []
    low, high = qp.z_range
    if low < 0.5 or high > 1.0:
        out.append(Finding("warning", f"Renormalisation factors Z from {low:.2f} to {high:.2f}: outside 0.5–1, "
                                      "a state sits near a pole of the self-energy (with the plasmon pole, often an "
                                      "artefact). Its QP energy is not reliable."))
    if qp.indirect is not None:
        (dft, gw) = qp.indirect
        if gw.value < dft.value - 0.01:
            out.append(Finding("warning", f"The GW gap ({gw.value:.2f} eV) is smaller than the DFT one "
                                          f"({dft.value:.2f} eV): GW opens the gaps of semiconductors and insulators. "
                                          "Check the screening (NGsBlkXp, BndsRnXp) and the bands summed (GbndRnge)."))
    elif occupied is not None:
        out.append(Finding("note", f"The QP table (bands {qp.bands[0]}–{qp.bands[1]}) does not hold both bands "
                                   f"around the gap ({occupied} and {occupied + 1}): no QP gap."))
    return out


def binding(bse: BseSummary, transition: float | None) -> float | None:
    """How far the lowest exciton lies below the lowest direct transition."""
    return None if transition is None else transition - bse.lowest


# Above this binding (eV) the check asks whether the energies were QP: bulk
# crystals bind by meV to tenths of an eV; layers, with little screening, far
# more (hBN about 2 eV, transition-metal dichalcogenides 0.3-1 eV)
STRONG_BINDING_EV = {False: 2.0, True: 3.5}


def check_bse(bse: BseSummary, transition: float | None, energies_from: str,
              dft_transition: float | None = None, layer: bool = False) -> list[Finding]:
    """`transition`: the lowest direct transition (eV) on the energies the
    BSE was given (`energies_from`: "QP", "DFT + scissor", "DFT");
    `dft_transition`, on DFT energies, when they were QP; `layer`: a 2D
    material's BSE (with a slab cutoff), whose excitons bind far more."""
    out = []
    if bse.lowest < 0:
        out.append(Finding("warning", f"An exciton at {bse.lowest:.2f} eV, below zero: the BSE is unstable "
                                      "(often too few bands or a wrong screening)."))
    if bse.lowest_bright is None:
        out.append(Finding("warning", "No exciton absorbs light: check the BSE's bands and the light's "
                                      "direction (BLongDir)."))
    b = binding(bse, transition)
    if b is not None:
        if b < -SAME_EV * 4:
            out.append(Finding("warning", f"The lowest exciton lies {-b:.2f} eV above the lowest {energies_from} "
                                          "transition: an attractive electron–hole interaction puts it below. "
                                          f"Were the BSE's energies really {energies_from}?"))
        elif b > STRONG_BINDING_EV[layer]:
            out.append(Finding("warning", f"The lowest exciton lies {b:.2f} eV below the lowest {energies_from} "
                                          "transition: an unusually strong binding ("
                                          + ("layers: up to about 2 eV, hBN's" if layer else
                                             "bulk crystals: meV to tenths of an eV")
                                          + f"). Were the BSE's energies really {energies_from}?"))
    if energies_from == "QP" and dft_transition is not None and transition is not None \
            and bse.lowest < dft_transition - SAME_EV and transition > dft_transition:
        out.append(Finding("warning", f"The lowest exciton ({bse.lowest:.2f} eV) lies below the lowest DFT "
                                      f"transition ({dft_transition:.2f} eV): either it is bound by more than GW "
                                      "opened the gap (seen in 2D materials and wide-gap insulators, rare in bulk "
                                      "semiconductors), or the BSE did not apply the QP energies. Yambo's report "
                                      "(r-* file) says which energies it read."))
    return out


# -- a project's results ---------------------------------------------------------------

@dataclass
class StepResults:
    step: str
    save: SaveSummary | None = None
    qp: QpSummary | None = None
    bse: list = field(default_factory=list)          # BseSummary
    energies_from: str | None = None                 # the BSE's: "QP", "DFT + scissor", "DFT"
    scissor: float | None = None
    transition: float | None = None                  # its lowest direct transition, eV
    findings: list = field(default_factory=list)
    errors: list = field(default_factory=list)       # databases that could not be read


def local_path(project, step, remote_relative: str) -> Path | None:
    """Where a path a step's run reads (relative to the step's folder on the
    cluster, e.g. ../04-gw/gw/ndb.QP) was downloaded: that step's outputs."""
    from .project import OUTPUTS_DIR
    path = posixpath.normpath(posixpath.join(step.id, remote_relative))
    parts = path.split("/")
    if not parts or parts[0] in ("..", ""):
        return None
    try:
        other = project.step(parts[0])
    except Exception:                                # noqa: BLE001 - not a step of this project
        return None
    return other.folder / OUTPUTS_DIR / Path(*parts[1:]) if len(parts) > 1 else None


def bse_energies(project, step) -> tuple[str, float | None, Path | None]:
    """What energies a BSE step's input gave it: ("QP", None, the local
    ndb.QP), ("DFT + scissor", shift, None) or ("DFT", None, None)."""
    from ..yambopy_ext.io.yambo_input import read_yambo_input
    for run in step.job.runs if step.job else []:
        if run.program != "yambo" or not run.input:
            continue
        path = step.folder / run.input
        if not path.is_file():
            continue
        inp = read_yambo_input(path.read_text(encoding="utf-8"))
        if not {f.lower() for f in inp.flags} & {"bse", "bss", "optics"}:
            continue                                 # not the BSE (the setup, a GW)
        db = inp.get("KfnQPdb")
        if db:
            m = re.match(r"\s*E\s*<\s*(\S+)", str(db))
            return "QP", None, local_path(project, step, m.group(1)) if m else None
        block = inp.block("KfnQP_E")
        if block is not None and block.rows and float(block.rows[0][0]) != 0:
            return "DFT + scissor", float(block.rows[0][0]), None
        return "DFT", None, None
    return "DFT", None, None


def _q_number(path: Path) -> tuple:
    """ndb.BS_diago_Q<n> in Q's order (Q2 before Q10)."""
    m = re.search(r"_Q(\d+)$", path.name)
    return (str(path.parent), int(m.group(1)) if m else 0)


def _found(folder: Path, pattern: str) -> list[Path]:
    return sorted(folder.rglob(pattern)) if folder.is_dir() else []


def project_results(project, steps=None, broadening: float = BROADENING_EV) -> list[StepResults]:
    """What each step's downloaded outputs hold (steps with none are left
    out): its SAVE, GW and BSE databases, read and checked."""
    from .project import OUTPUTS_DIR
    out = []
    saves: dict[str, SaveSummary] = {}
    for step in steps or project.steps:
        outputs = step.folder / OUTPUTS_DIR
        r = StepResults(step.id)
        for db in _found(outputs, "ns.db1")[:1]:
            try:
                r.save = saves[step.id] = read_save(db)
                r.findings += check_save(r.save)
            except ResultsError as e:
                r.errors.append(str(e))
        save = r.save or _save_of(project, step, saves)
        occupied = save.occupied if save else None
        for db in _found(outputs, "ndb.QP")[:1]:
            try:
                r.qp = read_qp(db, occupied)
                r.findings += check_qp(r.qp, occupied)
            except ResultsError as e:
                r.errors.append(str(e))
        bse_dbs = sorted(_found(outputs, "ndb.BS_diago_Q*"), key=_q_number)
        if bse_dbs:
            r.energies_from, r.scissor, qp_path = bse_energies(project, step)
            qp = None
            if r.energies_from == "QP":
                try:
                    qp = read_qp(qp_path, occupied) if qp_path and qp_path.is_file() else None
                except ResultsError:
                    qp = None
                if qp is None:
                    r.findings.append(Finding("note", "The BSE read QP energies whose ndb.QP was not downloaded: "
                                                      "its excitons are not compared with them."))
            dft_transition = None
            if qp is not None and qp.direct is not None:
                r.transition, dft_transition = qp.direct[1].value, qp.direct[0].value
            elif r.energies_from != "QP" and save is not None and save.direct is not None:
                r.transition = save.direct.value + (r.scissor or 0.0)
            for db in bse_dbs:
                try:
                    bse = read_bse(db, broadening)
                except ResultsError as e:
                    r.errors.append(str(e))
                    continue
                r.bse.append(bse)
                if bse.q_index == 1:
                    r.findings += check_bse(bse, r.transition, r.energies_from, dft_transition,
                                            _with_slab_cutoff(step))
        if r.save or r.qp or r.bse or r.errors:
            out.append(r)
    return out


def _with_slab_cutoff(step) -> bool:
    """Whether a step's yambo runs use a layer's Coulomb cutoff (rim_cut,
    CUTGeo "slab ...")."""
    from ..yambopy_ext.io.yambo_input import read_yambo_input
    for run in step.job.runs if step.job else []:
        path = step.folder / run.input if run.program == "yambo" and run.input else None
        if path is None or not path.is_file():
            continue
        try:
            inp = read_yambo_input(path.read_text(encoding="utf-8"))
        except Exception:                            # noqa: BLE001 - unreadable: not known
            continue
        if inp.has_flag("rim_cut") and str(inp.get("CUTGeo") or "").strip().lower().startswith("slab"):
            return True
    return False


def _save_of(project, step, saves) -> SaveSummary | None:
    """The SAVE a yambo step links (its `SAVE` link), if downloaded."""
    target = (step.job.links if step.job else {}).get("SAVE")
    if not target:
        return None
    local = local_path(project, step, target)
    if local is None:
        return None
    owner = posixpath.normpath(posixpath.join(step.id, target)).split("/")[0]
    if owner in saves:
        return saves[owner]
    try:
        return read_save(local)
    except ResultsError:
        return None


# -- the report ------------------------------------------------------------------------

def _gap_text(gap: Gap) -> str:
    where = f"at k{gap.k_valence}" if gap.direct else f"k{gap.k_valence} → k{gap.k_conduction}"
    return f"{gap.value:.2f} eV ({where})"


def dispersion_lines(bses: list) -> list[str]:
    """The lowest exciton at each Q-point (yambo's list of irreducible
    q-points, which its report gives), and the lowest of all."""
    lines = [f"  Exciton dispersion ({len(bses)} Q-points; the lowest exciton at each, eV, "
             "in yambo's q-point list):"]
    row = []
    for b in bses:
        row.append(f"Q{b.q_index} {b.lowest:.2f}")
        if len(row) == 6:
            lines.append("    " + ", ".join(row))
            row = []
    if row:
        lines.append("    " + ", ".join(row))
    low = min(bses, key=lambda b: b.lowest)
    at_zero = next((b for b in bses if b.q_index == 1), None)
    text = f"    lowest of all: {low.lowest:.2f} eV at Q{low.q_index}"
    if at_zero is not None and low.q_index != 1:
        text += (f", {at_zero.lowest - low.lowest:.2f} eV below Q = 0's: an indirect exciton (light alone "
                 "cannot make it; with a phonon it can)")
    lines.append(text)
    return lines


def report(results: list[StepResults], peaks: int = 6) -> list[str]:
    """The results as text lines, for the command line and the GUI."""
    lines = []
    for r in results:
        lines.append(f"{r.step}:")
        if r.save:
            s = r.save
            lines.append(f"  SAVE: {s.electrons:g} electrons, {s.nbnd} bands, {s.nk_ibz} k-points"
                         + (f", {s.occupied} occupied" if s.occupied else "")
                         + (", spinors" if s.spinors == 2 else "") + (", 2 spin channels" if s.spin_channels == 2 else ""))
            if s.indirect and not s.metal:
                lines.append(f"    DFT gap {_gap_text(s.indirect)}"
                             + ("" if s.indirect.direct else f"; direct {_gap_text(s.direct)}"))
        if r.qp:
            q = r.qp
            lines.append(f"  GW ({q.approximation or '?'}, {q.solver or '?'} solver): bands {q.bands[0]}–{q.bands[1]} "
                         f"at {q.nk} k-points; Z {q.z_range[0]:.2f}–{q.z_range[1]:.2f}")
            for b in q.shifts:
                lines.append(f"    band {b.band}: {b.mean:+.2f} eV ({b.low:+.2f} to {b.high:+.2f})")
            if q.indirect:
                (dft, gw), (dft_d, gw_d) = q.indirect, q.direct
                lines.append(f"    gap {dft.value:.2f} → {gw.value:.2f} eV; lowest direct transition "
                             f"{dft_d.value:.2f} → {gw_d.value:.2f} eV")
        for bse in [b for b in r.bse if b.q_index == 1] or r.bse[:1]:
            q = "Q = 0" if bse.q_index == 1 else f"Q-point {bse.q_index}"
            bands = f", bands {bse.bands[0]}–{bse.bands[1]}" if bse.bands else ""
            energies = f" on {r.energies_from} energies" + (f" ({r.scissor:+g} eV)" if r.scissor else "")
            lines.append(f"  BSE ({q}{bands}{energies}): {len(bse.energies)} excitons, {bse.dark} weak or dark "
                         f"(under {BRIGHT_SHARE:.0%} of the strongest)")
            lines.append(f"    lowest {bse.lowest:.2f} eV"
                         + (f", lowest bright {bse.lowest_bright:.2f} eV" if bse.lowest_bright is not None else ""))
            b = binding(bse, r.transition) if bse.q_index == 1 else None
            if b is not None:
                lines.append(f"    {b:.2f} eV below the lowest direct transition ({r.transition:.2f} eV)")
            if bse.peaks:
                shown = sorted(sorted(bse.peaks, key=lambda p: -p.height)[:peaks], key=lambda p: p.energy)
                lines.append("    absorption peaks: " + ", ".join(f"{p.energy:.2f} eV ({p.height:.2f})" for p in shown)
                             + f" (rebuilt from the excitons, {bse.broadening:g} eV broadening)")
        if len(r.bse) > 1:
            lines += dispersion_lines(r.bse)
        for f in r.findings:
            lines.append(f"  {'Warning' if f.level == 'warning' else 'Note'}: {f.text}")
        for e in r.errors:
            lines.append(f"  Not read: {e}")
    return lines
