"""The bands of a downloaded SAVE, for choosing GW and BSE bands by sight:
each band's energy range (zero at the valence top), the gaps, the density
of states, and the bands that hold the transitions up to an energy.

The BSE's size follows from its bands: one row per transition, valence
bands × conduction bands × k-points of the whole zone (Yambo's BSE runs on
the full grid). For the silicon of Phase 4 (bands 3–6, 216 k-points):
2 × 2 × 216 = 864, the excitons yambo wrote.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .results import ResultsError, SaveSummary, read_save


# Two levels closer than this are one degenerate level (eV)
DEGENERATE_EV = 0.01
# ns.db1's single-precision energies: a transition at a window's energy is in it
ROUNDING_EV = 1e-4


@dataclass
class Band:
    index: int                       # from 1
    low: float                       # eV, zero at the valence top
    high: float
    occupied: bool


@dataclass
class Window:
    """The bands holding every direct transition up to `energy` (eV)."""
    energy: float
    first: int                       # band indices, from 1
    last: int
    valence: int                     # how many valence and conduction bands
    conduction: int
    nk_bz: int | None

    @property
    def bse_size(self) -> int | None:
        """Transitions, the BSE's rows (valence × conduction × k-points of the zone)."""
        return self.valence * self.conduction * self.nk_bz if self.nk_bz else None


@dataclass
class SaveBands:
    save: SaveSummary
    weights: np.ndarray              # per irreducible k-point, summing to 1
    nk_bz: int | None                # k-points of the whole zone (None: not known)
    notes: list

    @property
    def bands(self) -> list[Band]:
        e = self.save.energies[0]
        occ = self.save.occupied or 0
        return [Band(b + 1, float(e[:, b].min()), float(e[:, b].max()), b < occ) for b in range(e.shape[1])]

    def window(self, energy: float) -> Window | None:
        """The bands with a direct transition (same k) up to `energy`; None
        when the occupied bands are not known, or no transition is that low."""
        occ = self.save.occupied
        if not occ or occ >= self.save.nbnd:
            return None
        e = self.save.energies[0]                    # (k, band)
        jumps = e[:, None, occ:] - e[:, :occ, None]  # (k, valence, conduction)
        within = jumps <= energy + ROUNDING_EV
        if not within.any():
            return None
        valence = np.flatnonzero(within.any(axis=(0, 2)))
        conduction = np.flatnonzero(within.any(axis=(0, 1)))
        first, last = int(valence.min()) + 1, occ + int(conduction.max()) + 1
        return Window(energy, first, last, occ - first + 1, last - occ, self.nk_bz)

    def reach(self, energy: float) -> tuple[float, float] | None:
        """The energies the states of the transitions up to `energy` span:
        (the lowest valence state, the highest conduction state)."""
        occ = self.save.occupied
        if not occ or occ >= self.save.nbnd:
            return None
        e = self.save.energies[0]
        valence, conduction = e[:, :occ], e[:, occ:]
        within = (conduction[:, None, :] - valence[:, :, None]) <= energy + ROUNDING_EV
        if not within.any():
            return None
        return float(valence[within.any(axis=2)].min()), float(conduction[within.any(axis=1)].max())

    def splits(self, first: int, last: int, tolerance: float = DEGENERATE_EV) -> list[str]:
        """Where bands `first`–`last` cut a set of degenerate states: at a
        k-point, band first−1 level with band first, or band last with last+1."""
        e = self.save.energies[0]
        out = []
        for edge_low, edge_high, side in ((first - 1, first, "below"), (last, last + 1, "above")):
            if edge_low < 1 or edge_high > e.shape[1]:
                continue
            same = np.flatnonzero(np.abs(e[:, edge_high - 1] - e[:, edge_low - 1]) < tolerance)
            if len(same):
                ks = ", ".join(f"k{k + 1} ({e[k, edge_low - 1]:.2f} eV)" for k in same[:3]) \
                    + (f" and {len(same) - 3} more" if len(same) > 3 else "")
                out.append(f"bands {edge_low} and {edge_high} are degenerate at {ks}: the window "
                           f"keeps band {edge_high if side == 'below' else edge_low} and leaves out its partner {side}")
        return out

    def edge_sets(self) -> tuple[int, int] | None:
        """The bands a window must hold whole: at the k-points of the valence
        top, the conduction bottom and the lowest direct transition, the top
        valence level's degenerate set and the bottom conduction level's.
        (first, last), or None without a gap. Si: (2, 7), the triply
        degenerate states at Γ."""
        s = self.save
        if not s.occupied or s.indirect is None or s.metal:
            return None
        e, occ = s.energies[0], s.occupied
        ks = {s.indirect.k_valence - 1, s.indirect.k_conduction - 1, s.direct.k_valence - 1}
        first, last = occ, occ + 1
        for k in ks:
            top, bottom = e[k, occ - 1], e[k, occ]
            for b in range(occ - 1, -1, -1):             # down from the top valence band
                if abs(e[k, b] - top) < DEGENERATE_EV:
                    first = min(first, b + 1)
                else:
                    break
            for b in range(occ, e.shape[1]):              # up from the bottom conduction band
                if abs(e[k, b] - bottom) < DEGENERATE_EV:
                    last = max(last, b + 1)
                else:
                    break
        return first, last

    def fitted(self, first: int, last: int) -> tuple[int, int]:
        """A window grown (never shrunk) to hold the edge sets whole."""
        need = self.edge_sets()
        if need is None:
            return first, last
        return min(first, need[0]), max(last, need[1])

    def states_per_band(self) -> float:
        """Electrons a band holds per k-point: 2 (spin-degenerate), 1 (spinors, or each spin channel)."""
        return 1.0 if self.save.spinors == 2 or self.save.spin_channels == 2 else 2.0

    def dos(self, broadening: float = 0.1, grid=None):
        """The density of states (states/eV per cell, Gaussians of width
        `broadening` eV): (energies, dos)."""
        e = self.save.energies                       # (spin, k, band)
        if grid is None:
            grid = np.arange(e.min() - 5 * broadening, e.max() + 5 * broadening, broadening / 5)
        w = np.asarray(grid, float)
        out = np.zeros_like(w)
        norm = 1.0 / (broadening * np.sqrt(2 * np.pi))
        for spin in range(e.shape[0]):
            for k in range(e.shape[1]):
                levels = e[spin, k]
                out += self.weights[k] * norm * np.exp(-0.5 * ((w[:, None] - levels[None, :]) / broadening) ** 2).sum(1)
        return w, out * self.states_per_band()


def _weights(path, nk: int) -> tuple[np.ndarray, int | None, list]:
    """The irreducible k-points' weights and the whole zone's count, through
    YamboPy (the symmetries of ns.db1); equal weights if that fails."""
    try:
        from yambopy import YamboLatticeDB
        lattice = YamboLatticeDB.from_db(str(path))
        star = np.asarray(lattice.kpoints_indexes, int)     # the irreducible point of each in the zone
        weights = np.bincount(star, minlength=nk).astype(float)
        if len(weights) == nk and weights.all():
            return weights / weights.sum(), len(star), []
    except Exception as e:                           # noqa: BLE001 - a fallback, said
        return np.full(nk, 1.0 / nk), None, [f"The k-points' weights could not be read ({e}): "
                                             "equal weights, and no BSE size."]
    return np.full(nk, 1.0 / nk), None, ["The k-points' weights did not match: equal weights, and no BSE size."]


def save_bands(path) -> SaveBands:
    """A SAVE's bands (`path`: the SAVE folder or its ns.db1)."""
    save = read_save(path)
    weights, nk_bz, notes = _weights(save.path, save.nk_ibz)
    if save.occupied is None:
        notes.append("The occupied bands are not known (spin-polarised, or a fractional number of "
                     "electrons): energies are absolute, and no windows.")
    elif save.metal:
        notes.append("No gap on the grid: a metal, or a wrong occupation.")
    return SaveBands(save, weights, nk_bz, notes)


def default_windows(sb: SaveBands) -> list[float]:
    """Energies worth showing: the lowest direct transition plus 1, 2 and 4 eV."""
    if sb.save.direct is None:
        return []
    base = sb.save.direct.value
    return [round(base + extra, 1) for extra in (1.0, 2.0, 4.0)]


def report(sb: SaveBands, windows=None, around: int = 6) -> list[str]:
    s = sb.save
    lines = [f"{s.electrons:g} electrons, {s.nbnd} bands"
             + (f" ({s.occupied} occupied)" if s.occupied else "")
             + f", {s.nk_ibz} k-points" + (f" ({sb.nk_bz} in the whole zone)" if sb.nk_bz else "")]
    bands = sb.bands
    occ = s.occupied or 0
    shown = [b for b in bands if occ - around < b.index <= occ + around] if occ else bands
    zero = "zero at the valence top" if occ else "absolute"
    lines.append(f"  band   lowest  highest   (eV, {zero})")
    if shown and shown[0].index > 1:
        lines.append(f"  … {shown[0].index - 1} bands below")
    for b in shown:
        mark = "  top valence" if b.index == occ else "  bottom conduction" if b.index == occ + 1 else ""
        lines.append(f"  {b.index:4d} {b.low:8.2f} {b.high:8.2f}{mark}")
    if shown and shown[-1].index < len(bands):
        lines.append(f"  … {len(bands) - shown[-1].index} bands above (up to {bands[-1].high:.2f} eV)")
    if s.indirect and not s.metal:
        lines.append(f"  Gap {s.indirect.value:.2f} eV ("
                     + (f"direct, at k{s.indirect.k_valence}" if s.indirect.direct else
                        f"indirect, k{s.indirect.k_valence} → k{s.indirect.k_conduction}")
                     + f"); lowest direct transition {s.direct.value:.2f} eV at k{s.direct.k_valence}")
    for energy in (windows if windows is not None else default_windows(sb)):
        w = sb.window(energy)
        if w is None:
            lines.append(f"  Transitions up to {energy:g} eV: none")
            continue
        size = f"; BSE size {w.valence} × {w.conduction} × {w.nk_bz} = {w.bse_size}" if w.bse_size else ""
        lines.append(f"  Transitions up to {energy:g} eV: bands {w.first}–{w.last} "
                     f"({w.valence} valence, {w.conduction} conduction){size}")
    lines += [f"  Note: {n}" for n in sb.notes]
    return lines


def used_windows(project) -> list[tuple[str, str, int, int]]:
    """The band windows the project's yambo inputs use: (step, name, first, last)."""
    from ..yambopy_ext.io.yambo_input import read_yambo_input
    out = []
    for step in project.steps:
        for run in step.job.runs if step.job else []:
            path = step.folder / run.input if run.input else None
            if run.program != "yambo" or path is None or not path.is_file():
                continue
            inp = read_yambo_input(path.read_text(encoding="utf-8"))
            for name, columns in (("BSEBands", (0, 1)), ("QPkrange", (2, 3))):
                block = inp.block(name)
                if block is not None and block.rows and len(block.rows[0]) > max(columns):
                    row = block.rows[0]
                    out.append((step.id, name, int(row[columns[0]]), int(row[columns[1]])))
    return out


# -- the project's windows fitted -------------------------------------------------------

# Where each block keeps its bands: (first, last) columns of its rows
WINDOW_BLOCKS = {"BSEBands": (0, 1), "QPkrange": (2, 3)}


def set_block_columns(text: str, name: str, columns: tuple[int, int], first: int, last: int) -> str:
    """The input with every row of block `name` given `first` and `last` in
    those columns, its other lines (yambo's descriptions) as they were."""
    import re
    lines, inside = text.splitlines(), False
    for i, line in enumerate(lines):
        m = re.match(r"\s*%\s*(\w*)", line)
        if m:
            inside = m.group(1).lower() == name.lower() if not inside else False
            continue
        if inside:
            body, hash_, comment = line.partition("#")
            cells = [c.strip() for c in body.split("|")]
            cells = [c for c in cells if c]
            if len(cells) > max(columns):
                cells[columns[0]], cells[columns[1]] = str(first), str(last)
                new = " " + " | ".join(cells) + " |"
                if hash_:
                    new += " " * max(1, len(line) - len(hash_ + comment) - len(new)) + hash_ + comment
                lines[i] = new
    return "\n".join(lines) + "\n"


@dataclass
class Fit:
    step: str
    input: str                       # the input file, in the step's folder
    name: str                        # BSEBands, QPkrange
    old: tuple[int, int]
    new: tuple[int, int]
    skipped: str = ""                # why it was not changed (the step has run)


def fit_windows(project, sb: SaveBands, write: bool = True, ran_too: bool = False) -> list[Fit]:
    """The project's yambo windows (QPkrange, BSEBands) grown to whole
    degenerate sets at the gap's edges; steps that ran are left as they are
    (their results came from the old window) unless `ran_too`."""
    from ..yambopy_ext.io.yambo_input import read_yambo_input
    from .studies import studies
    chosen = {(sid, st.variable.lower()): st.name for st in studies(project) for sid in st.steps}
    out = []
    for step in project.steps:
        ran = step.latest is not None
        for run in step.job.runs if step.job else []:
            path = step.folder / run.input if run.input else None
            if run.program != "yambo" or path is None or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            inp = read_yambo_input(text)
            changed = text
            for name, columns in WINDOW_BLOCKS.items():
                block = inp.block(name)
                if block is None or not block.rows or len(block.rows[0]) <= max(columns):
                    continue
                old = (int(block.rows[0][columns[0]]), int(block.rows[0][columns[1]]))
                new = sb.fitted(*old)
                if new == old:
                    continue
                fit = Fit(step.id, run.input, name, old, new)
                if (step.id, name.lower()) in chosen:
                    fit.skipped = f"set by the study {chosen[(step.id, name.lower())]}"
                elif ran and not ran_too:
                    fit.skipped = f"{step.id} has run with {old[0]}–{old[1]}"
                else:
                    changed = set_block_columns(changed, name, columns, *new)
                out.append(fit)
            if write and changed != text:
                path.write_text(changed, encoding="utf-8")
    return out


def fit_report(fits: list[Fit]) -> list[str]:
    lines = [f"  {f.step} {f.name} {f.old[0]}–{f.old[1]} → {f.new[0]}–{f.new[1]}"
             + (f": not changed ({f.skipped})" if f.skipped else f" ({f.input})") for f in fits]
    done = sum(1 for f in fits if not f.skipped)
    return lines + [f"Fitted {done} window{'s' if done != 1 else ''}." if done else
                    "No window to fit: every one holds the gap's degenerate sets whole "
                    "(or its step has run, or a study set it)."]


def window_problems(project, steps=None) -> dict:
    """{step id: [Problem]}: windows that cut the degenerate sets at the
    gap's edges, when the project's SAVE has been downloaded."""
    from .problems import Problem
    try:
        _, path = find_save(project)
        sb = save_bands(path)
    except ResultsError:
        return {}
    wanted = {s.id for s in steps} if steps else None
    found = {}
    for f in fit_windows(project, sb, write=False, ran_too=True):
        if wanted is not None and f.step not in wanted:
            continue
        if project.step(f.step).latest is not None or f.skipped.startswith("set by the study"):
            continue                                 # it ran, or the window is the study's own
        found.setdefault(f.step, []).append(Problem(
            f"{f.name} {f.old[0]}–{f.old[1]} ({f.input}) cuts the degenerate states at the gap's edges: "
            f"{f.new[0]}–{f.new[1]} holds them whole (vwf project bands --fit).", error=False))
    return found


def find_save(project, step=None):
    """The downloaded SAVE of a step (its own, or the one it links), or the
    project's first."""
    from .excitons import _save_of
    steps = [step] if step is not None else list(project.steps)
    for s in steps:
        found = _save_of(project, s)
        if found is not None:
            return s, found
    raise ResultsError((f"{step.id} has" if step is not None else "No step has")
                       + " a downloaded SAVE: vwf project get --for-excitons brings it back.")
