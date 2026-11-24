"""A scissor taken from a GW: the shift and stretching that yambo's
`KfnQP_E` applies, fitted to the corrections in an `ndb.QP`.

What yambo does with `KfnQP_E = shift | c-stretch | v-stretch` (5.2.4:
`mod_QP_CTL.F`, `QP_eval_the_FIT.F`): a conduction state's energy E gets
shift + (c-stretch − 1)(E − CBM), a valence state's (v-stretch − 1)(E − VBM),
CBM and VBM the DFT band edges. The valence top never moves: the shift is how
much the gap opens.

So the GW's corrections are fitted with two straight lines, one for the
conduction states against E − CBM and one for the valence states against
E − VBM (least squares, as yambo's own `QP_fit_energies.F` fits a database
for the states it lacks), and the shift is the difference of their values
at the edges. A common offset of every energy changes nothing in a BSE.

A scissor is an average: on bulk Si (bands 2–8) its errors are about 0.1 eV,
up to 0.5 eV for single states, and it opens the lowest direct transition as
GW does (+0.50 eV against +0.48) but not the indirect gap (+0.27). The fit
says how good it is, and which gaps it gives against the GW's.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .results import Gap, QpSummary, _gaps


class ScissorError(ValueError):
    pass


@dataclass
class Side:
    offset: float                    # eV, the correction at the band edge
    stretch: float
    rms: float                       # eV, the fit's typical error
    worst: float                     # eV, its largest error
    states: int


@dataclass
class Scissor:
    shift: float                     # eV
    conduction: Side
    valence: Side
    bands: tuple[int, int]           # the states fitted
    gaps: dict                       # name -> (DFT, GW, scissor), eV

    @property
    def row(self) -> list[float]:
        """KfnQP_E's row: shift | c-stretch | v-stretch."""
        return [round(self.shift, 4), round(self.conduction.stretch, 4), round(self.valence.stretch, 4)]

    def apply(self, energy: float, valence: bool, vbm: float, cbm: float) -> float:
        """A DFT energy as yambo corrects it with this scissor."""
        if valence:
            return energy + (self.valence.stretch - 1) * (energy - vbm)
        return energy + self.shift + (self.conduction.stretch - 1) * (energy - cbm)

    def lines(self) -> list[str]:
        c, v = self.conduction, self.valence
        out = [f"KfnQP_E = {self.row[0]:g} | {self.row[1]:g} | {self.row[2]:g} |   (shift eV | c-stretch | v-stretch)",
               f"  fitted to bands {self.bands[0]}–{self.bands[1]}: {c.states} conduction states "
               f"(offset {c.offset:+.3f} eV, stretch {c.stretch:.4f}; error {c.rms:.3f} eV typical, "
               f"{c.worst:.3f} eV worst), {v.states} valence states (offset {v.offset:+.3f} eV, "
               f"stretch {v.stretch:.4f}; error {v.rms:.3f} eV typical, {v.worst:.3f} eV worst)"]
        for name, (dft, gw, sc) in self.gaps.items():
            out.append(f"  {name}: DFT {dft:.2f} eV, GW {gw:.2f} eV, with the scissor {sc:.2f} eV")
        return out


def _side(x, y) -> Side:
    if len(x) == 0:
        raise ScissorError("no state on one side of the gap")
    if len(x) == 1 or np.ptp(x) < 1e-9:
        offset, slope = float(np.mean(y)), 0.0
    else:
        a = np.vstack([np.ones_like(x), x]).T
        (offset, slope), *_ = np.linalg.lstsq(a, y, rcond=None)
    r = y - (offset + slope * x)
    return Side(float(offset), float(1 + slope), float(np.sqrt(np.mean(r ** 2))), float(np.abs(r).max()), len(x))


def fit_scissor(qp: QpSummary, occupied: int, bands: tuple[int, int] | None = None) -> Scissor:
    """The scissor closest to the GW's corrections of `qp` (the states of
    `bands`, by default all), for a SAVE with `occupied` valence bands."""
    keep = np.ones(len(qp.band_of), bool)
    if bands is not None:
        keep = (qp.band_of >= bands[0]) & (qp.band_of <= bands[1])
    b, dft, gw = qp.band_of[keep], qp.dft[keep], qp.qp[keep]
    if not len(b):
        raise ScissorError(f"the QP database has no state in bands {bands[0]}–{bands[1]} "
                           f"(it holds {qp.bands[0]}–{qp.bands[1]})")
    valence, conduction = b <= occupied, b > occupied
    if not valence.any() or not conduction.any():
        raise ScissorError(f"the QP database's bands {int(b.min())}–{int(b.max())} lie on one side of the gap "
                           f"({occupied} occupied): a scissor needs both")
    vbm, cbm = float(dft[valence].max()), float(dft[conduction].min())
    c = _side(dft[conduction] - cbm, (gw - dft)[conduction])
    v = _side(dft[valence] - vbm, (gw - dft)[valence])
    sc = Scissor(c.offset - v.offset, c, v, (int(b.min()), int(b.max())), {})
    # the gaps it gives, against GW's, on the database's states
    corrected = np.array([sc.apply(e, val, vbm, cbm) for e, val in zip(dft, valence)])
    table = {"dft": {}, "gw": {}, "scissor": {}}
    for (bb, k, e_dft, e_gw, e_sc) in zip(b, qp.k_of[keep], dft, gw, corrected):
        table["dft"][(int(bb), int(k))] = float(e_dft)
        table["gw"][(int(bb), int(k))] = float(e_gw)
        table["scissor"][(int(bb), int(k))] = float(e_sc)
    found = {kind: _gaps(table[kind], occupied) for kind in table}
    if found["dft"][0] is not None:
        for i, name in ((0, "gap (on the grid)"), (1, "lowest direct transition")):
            g: list[Gap] = [found[kind][i] for kind in ("dft", "gw", "scissor")]
            sc.gaps[name] = tuple(x.value for x in g)
    return sc


def scissor_from(path, occupied: int | None = None, bands: tuple[int, int] | None = None) -> Scissor:
    """The scissor fitted to an `ndb.QP` file (or a folder holding one)."""
    from pathlib import Path

    from .results import read_qp
    path = Path(path)
    if path.is_dir():
        path = path / "ndb.QP"
    qp = read_qp(path, occupied)
    if occupied is None:
        below = [int(x) for x in sorted(set(qp.band_of)) if qp.dft[qp.band_of == x].max() <= 1e-3]
        if not below:
            raise ScissorError(f"{path}: no valence state in the database (bands {qp.bands[0]}–{qp.bands[1]}); "
                               "a scissor needs both sides of the gap")
        occupied = max(below)
    return fit_scissor(qp, occupied, bands)


# -- a BSE step's scissor ----------------------------------------------------------------

def set_scissor_in_text(text: str, row: list[float]) -> str:
    """The input with KfnQP_E's row set (the block added when missing) and
    KfnQPdb removed (the scissor replaces the GW's energies); the other lines
    as they were."""
    import re
    lines, out, inside, done = text.splitlines(), [], False, False
    for line in lines:
        if re.match(r"\s*KfnQPdb\s*=", line, re.I):
            continue
        m = re.match(r"\s*%\s*(\w*)", line)
        if m:
            inside = (m.group(1).lower() == "kfnqp_e") if not inside else False
            out.append(line)
            continue
        if inside and not done:
            body, hash_, comment = line.partition("#")
            new = " " + " | ".join(f"{x:g}" for x in row) + " |"
            out.append(new + ((" " * max(1, len(body) - len(new)) + hash_ + comment) if hash_ else ""))
            done = True
            continue
        out.append(line)
    if not done:
        out += ["% KfnQP_E", " " + " | ".join(f"{x:g}" for x in row) + " |", "%"]
    return "\n".join(out) + "\n"


def source_qp(project, source: str):
    """An ndb.QP to fit to: a step's (downloaded into its outputs/) or a path."""
    from pathlib import Path

    from .project import OUTPUTS_DIR, ProjectError
    try:
        step = project.step(source)
    except ProjectError:
        path = Path(source)
        if path.is_dir():
            path = path / "ndb.QP"
        if not path.is_file():
            raise ScissorError(f"{source}: neither a step of {project.name} nor an ndb.QP") from None
        return path
    found = sorted((step.folder / OUTPUTS_DIR).rglob("ndb.QP"))
    if not found:
        raise ScissorError(f"{step.id} has no ndb.QP downloaded: vwf project get --for-excitons {step.id}")
    return found[0]


def set_step_scissor(project, step, source: str) -> tuple[Scissor, str]:
    """A BSE step's input given the scissor fitted to `source`'s ndb.QP, on
    its BSEBands; returns the fit and the input's file name."""
    from ..yambopy_ext.io.yambo_input import read_yambo_input
    from .chain import bse_numbers
    from .results import read_qp
    for run in step.job.runs if step.job else []:
        if run.program != "yambo" or not run.input or not (step.folder / run.input).is_file():
            continue
        text = (step.folder / run.input).read_text(encoding="utf-8")
        inp = read_yambo_input(text)
        if "bss" not in {f.lower() for f in inp.flags} and "bse" not in {f.lower() for f in inp.flags}:
            continue
        block = inp.block("BSEBands")
        bands = tuple(int(x) for x in block.rows[0][:2]) if block is not None and block.rows else None
        occupied, _ = bse_numbers(project, step)
        path = source_qp(project, source)
        qp = read_qp(path, occupied)
        if occupied is None:
            below = [int(x) for x in sorted(set(qp.band_of)) if qp.dft[qp.band_of == x].max() <= 1e-3]
            occupied = max(below) if below else None
        if occupied is None:
            raise ScissorError(f"the occupied bands are not known for {step.id}")
        sc = fit_scissor(qp, occupied, bands)
        (step.folder / run.input).write_text(set_scissor_in_text(text, sc.row), encoding="utf-8")
        return sc, run.input
    raise ScissorError(f"{step.id} has no BSE run with an input")
