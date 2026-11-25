"""Yambo inputs made by the program: GW (plasmon pole) and BSE (with the GW's
QP energies, or a scissor shift on DFT energies), from what the SAVE will
hold, known from the nscf that makes it.

The values are starting points for a convergence study, each with a note
saying what it is and why; yambo's own defaults are kept where they are safe
(exact, if costly). A GW input has the runlevels yambo writes for
``yambo -x -p p -g n``; a BSE input those of ``yambo -d s -o b -k sex -y d``:
it computes its own static screening, and reads only the QP energies of the
GW step (``KfnQPdb``), never its screening, which a ``-J`` link to the GW's
folder would make it use instead (INIT.F: a plasmon-pole database found and
no static one). What Visual Excitons reads is always there: the
diagonalisation solver and the excitons' wave functions (``WRbsWF``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from ..yambopy_ext.io.yambo_input import YamboInput, write_yambo_input
from ..yambopy_ext.io.yambo_runlevels import input_names
from ..yambopy_ext.qepy.pw_input import PwInput

GW_RUNLEVELS = ["HF_and_locXC", "gw0", "ppa", "el_el_corr", "dyson", "em1d"]
BSE_RUNLEVELS = ["em1s", "optics", "bss", "bse", "dipoles"]

# Starting points (YamboPy's tutorials; to be converged)
SCREENING_BLOCK_RY = 3.0             # NGsBlkXp, NGsBlkXs
BSE_EXCHANGE_RY = 10.0               # BSENGexx


@dataclass
class SaveInfo:
    """What the SAVE will hold, from the nscf that makes it."""
    nbnd: int                        # bands
    occupied: int | None             # occupied bands (insulators), None when not known
    nk_ibz: int | None               # irreducible k-points
    spin_polarized: bool = False
    nk_bz: int | None = None         # k-points of the whole zone (unshifted grids)
    spinors: bool = False            # noncollinear (spin–orbit): one electron per band
    slab: str | None = None          # a layer's cutoff direction (x, y, z), from its structure
    vacuum_note: str = ""            # why not, when there is vacuum
    rim_w_ry: float | None = None    # a layer's: the screening RIM-W needs (Ry), from its structure

    @classmethod
    def from_nscf(cls, pw: PwInput, step_folder, pseudos_dir=None) -> "SaveInfo":
        from .chain import occupied_bands_of
        from .kpoints import full_grid_count, pw_kpoint_count
        nbnd = pw.get("system", "nbnd")
        if not isinstance(nbnd, int):
            raise ValueError("the nscf sets no nbnd: Yambo needs its empty bands")
        noncolin = pw.get("system", "noncolin", False) is True
        slab, note, rim_w_ry = None, "", None
        try:
            from .structure import Structure
            structure = Structure.from_pw(pw)
            slab, note = structure.slab_direction()
            if slab:
                rim_w_ry = structure.rim_w_screening(RIM_W_GVECTORS)[1]
        except Exception:                            # noqa: BLE001 - no structure: no cutoff
            pass
        return cls(nbnd, occupied_bands_of(pw, Path(step_folder), pseudos_dir), pw_kpoint_count(pw),
                   pw.get("system", "nspin", 1) == 2 and not noncolin, full_grid_count(pw), noncolin,
                   slab, "" if slab else note, rim_w_ry)


@dataclass
class Made:
    """An input made, with notes on its values."""
    inp: YamboInput
    runlevels: list
    notes: list = field(default_factory=list)

    def text(self, version=None, mpi=True) -> str:
        """In yambo's order, with its descriptions."""
        values = {k: v.value for k, v in self.inp.variables.items()}
        names = input_names(self.inp.flags, values, version, mpi=mpi, new_input=True,
                            databases=["QINDX"])
        info = {str(n).lower(): n.text for n in names}
        return write_yambo_input(self.inp, order=[str(n) for n in names],
                                 describe=lambda n: info.get(n.lower(), ""))


# -- the Coulomb cutoff of a layer --------------------------------------------------------

# Yambo's 2D tutorials (hBN, MoS2): random points for the Coulomb integrals
# near q = 0 (RIM) and the G-vectors they cover (decision 6, expert-questions.md)
RIM_POINTS = 1_000_000
RIM_GVECTORS = 100


def set_slab_cutoff(inp: YamboInput, direction: str, notes: list):
    """A layer's Coulomb cutoff: rim_cut, CUTGeo "slab <direction>", RIM."""
    inp.set_flag("rim_cut")
    inp.set("CUTGeo", f"slab {direction}")
    inp.set("RandQpts", RIM_POINTS)
    inp.set("RandGvec", RIM_GVECTORS, "RL")
    notes.append(f"Coulomb cutoff for a layer: CUTGeo = \"slab {direction}\", with RIM (RandQpts = {RIM_POINTS}, "
                 f"RandGvec = {RIM_GVECTORS} RL, Yambo's 2D tutorials): without it the layer's electrons and holes "
                 "feel its copies across the vacuum. The vacuum and the RIM values are still to converge.")


# RIM-W (yambo's rim_w runlevel, from 5.2; 5.1 had only an older flag): the
# screened interaction W averaged around each q-point too, as RIM does the bare
# one. In a layer, screening fades as q -> 0, which a coarse k-grid misses: on
# Yambo's monolayer MoS2 GW tutorial the gap is 4.12 eV with the cutoff alone
# and 2.81 eV with RIM-W (converged values about 2.5-2.8 eV). Slab cutoffs and
# semiconductors only (rim_integrate_w.F, QP_interpolate_W.F); rimw_type is
# read but not used by 5.2-5.4, written as the tutorial does. Decision 6.
RIM_W_GVECTORS = 15
RIM_W_SINCE = (5, 2)

# The light's direction in a layer: an in-plane cartesian axis (yambo's default,
# x, lies across a layer whose vacuum is along x)
IN_PLANE = {"x": [0.0, 1.0, 0.0], "y": [1.0, 0.0, 0.0], "z": [1.0, 0.0, 0.0]}


def rim_w_supported(version) -> bool | None:
    """Whether a yambo version has the rim_w runlevel (None: version not known)."""
    if not version:
        return None
    from ..yambopy_ext.io.yambo_variables import _major_minor
    try:
        return _major_minor(version) >= RIM_W_SINCE
    except (TypeError, ValueError):
        return None


def set_rim_w(inp: YamboInput, notes: list, version=None, *, block: str | None = None,
              needs_ry: float | None = None):
    """RIM-W on a layer's cutoff: rim_w, RandGvecW, rimw_type; the screening
    (`block`, NGsBlkXp or NGsBlkXs) raised to what RIM-W needs (`needs_ry`)."""
    if rim_w_supported(version) is False:
        notes.append(f"No RIM-W: yambo {version} lacks its rim_w runlevel (5.2 and later). On a coarse "
                     "k-grid a layer's GW gap comes out too large with the cutoff alone (Yambo's MoS2 "
                     "tutorial: 4.12 eV against 2.81 eV with RIM-W): converge the k-grid with care.")
        return
    inp.set_flag("rim_w")
    inp.set("RandGvecW", RIM_W_GVECTORS, "RL")
    inp.set("rimw_type", "semiconductor")
    notes.append(f"RIM-W (rim_w, RandGvecW = {RIM_W_GVECTORS} RL, Yambo's 2D GW tutorial): the screened "
                 "interaction averaged around each q-point, which a layer's GW needs on a coarse k-grid "
                 "(MoS2: gap 4.12 eV with the cutoff alone, 2.81 eV with RIM-W). Yambo 5.2 or later, "
                 "semiconductors only." + ("" if version else " Needs yambo 5.2 or later."))
    if block and needs_ry:
        variable = inp.variable(block)
        if variable is not None and str(variable.unit).lower() == "ry" and float(variable.value) < needs_ry:
            raised = math.ceil(needs_ry * 10 - 1e-6) / 10
            inp.set(block, raised, "Ry")
            notes.append(f"{block} = {raised:g} Ry, raised from {float(variable.value):g} Ry: RIM-W reaches "
                         f"G-vectors up to {needs_ry:.3f} Ry (its {RIM_W_GVECTORS} shifted to a neighbouring "
                         "q-point), which the screening must hold or yambo stops (\"G_max ... for RIM-W is "
                         "higher than G_max ... of vX\").")


def set_in_plane_field(inp: YamboInput, direction: str, names: list, notes: list):
    """The q -> 0 / light direction along an axis in the layer's plane."""
    axis = IN_PLANE[direction]
    for name in names:
        inp.set_block(name, [axis], "")
    notes.append(f"{', '.join(names)} = {' | '.join(f'{x:g}' for x in axis)}: along the layer's plane "
                 "(light across a layer barely excites it).")


def _cutoff(inp: YamboInput, save: SaveInfo, cutoff, notes: list, *, rim_w=False, version=None,
            fields=(), block=None):
    """`cutoff`: True (the SAVE's, if a layer), False (none), or a direction;
    with RIM-W if `rim_w`, and `fields` (LongDrX*, BLongDir) in the plane."""
    direction = save.slab if cutoff is True else (cutoff or None)
    if direction:
        set_slab_cutoff(inp, direction, notes)
        if rim_w:
            set_rim_w(inp, notes, version, block=block,
                      needs_ry=save.rim_w_ry if direction == save.slab else None)
        if fields:
            set_in_plane_field(inp, direction, list(fields), notes)
    elif cutoff is True and save.vacuum_note:
        notes.append(f"No Coulomb cutoff: {save.vacuum_note}.")


# -- the BSE's size ----------------------------------------------------------------------

# Above this many transitions, full diagonalisation gets heavy: time grows as
# the size cubed, memory as its square (decision 5, expert-questions.md)
LARGE_BSE = 20_000


def bse_size(first: int, last: int, occupied: int, nk_bz: int) -> int | None:
    """Transitions, the BSE's rows: valence × conduction bands × k-points of
    the whole zone (Yambo's BSE runs on the full grid)."""
    valence, conduction = occupied - first + 1, last - occupied
    if valence < 1 or conduction < 1 or not nk_bz:
        return None
    return valence * conduction * nk_bz


def _bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def large_bse_problems(inp: YamboInput, occupied: int | None, nk_bz: int | None) -> list:
    """Warnings on a BSE's solver for its size: a full diagonalisation above
    LARGE_BSE transitions (SLEPc suggested), and SLEPc without BSSNEig (yambo
    5.2's K_diago_driver.F then computes 1% of the excitons)."""
    from .problems import Problem
    flags = {f.lower() for f in inp.flags}
    block = inp.block("BSEBands")
    if "bss" not in flags or not occupied or not nk_bz or block is None or not block.rows:
        return []
    first, last = (int(x) for x in block.rows[0][:2])
    size = bse_size(first, last, occupied, nk_bz)
    if size is None:
        return []
    mode = str(inp.get("BSSmod") or "").lower()
    shape = f"{occupied - first + 1} × {last - occupied} × {nk_bz}"
    out = []
    if "d" in mode and size > LARGE_BSE:
        out.append(Problem(
            f"a BSE of {size} transitions ({shape}) diagonalised in full: the matrix alone takes "
            f"{_bytes(size * size * 8)} in single precision (twice in double), and the time grows as the "
            "size cubed. SLEPc (BSSmod = \"s\", in yambo builds with it) finds only the lowest excitons, "
            "with the wave functions Visual Excitons needs; BSSNEig says how many. Its spectrum then "
            "covers only the energies of those excitons.", "BSSmod", error=False))
    if "s" in mode:
        wanted = inp.get("BSSNEig")
        wanted = int(wanted) if isinstance(wanted, (int, float)) else 0
        if wanted <= 0 or wanted > size:
            out.append(Problem(
                f"BSSmod = \"s\" (SLEPc) " + ("without BSSNEig" if wanted <= 0 else
                                               f"with BSSNEig = {wanted}, more than the BSE's {size}")
                + f": yambo computes 1% of the excitons ({max(1, round(size * 0.01))} of {size}). Set "
                "BSSNEig to the number wanted.", "BSSNEig", error=False))
    return out


# Bands each side of the gap by default; twice with spinors, whose bands hold
# one electron each (decision 1, expert-questions.md)
WINDOW_BANDS = 2


def band_window(save: SaveInfo, valence: int | None = None, conduction: int | None = None) -> tuple[int, int]:
    """Bands around the gap: `valence` highest occupied ones and `conduction`
    lowest empty ones (within the SAVE's bands); by default 2 and 2, or 4
    and 4 with spinors. Grown to whole degenerate sets once the SAVE exists
    (core/bands.py, `vwf project bands --fit`)."""
    default = WINDOW_BANDS * (2 if save.spinors else 1)
    valence = default if valence is None else valence
    conduction = default if conduction is None else conduction
    if not save.occupied:
        raise ValueError("the occupied bands are not known (a metal, nspin = 2, or "
                         "pseudopotentials not in the project): give the bands yourself")
    return max(1, save.occupied - valence + 1), min(save.nbnd, save.occupied + conduction)


def gw_input(save: SaveInfo, *, qp_bands: tuple[int, int] | None = None,
             block_ry: float = SCREENING_BLOCK_RY, tasks: int = 1, cutoff=True, rim_w=True,
             version=None) -> Made:
    """A GW input: Hartree–Fock, plasmon-pole screening, the Dyson equation
    (Newton), for every irreducible k-point and the bands `qp_bands` (default:
    2 occupied and 2 empty bands around the gap, enough for a BSE on them).
    `cutoff`: a layer's Coulomb cutoff (True: when the SAVE is one; False:
    none; or a direction), with RIM-W (`rim_w`, unless `version`, the
    cluster's yambo, lacks it)."""
    inp, notes = YamboInput(), []
    for r in GW_RUNLEVELS:
        inp.set_flag(r)
    first, last = qp_bands or band_window(save)
    inp.set_block("BndsRnXp", [[1, save.nbnd]])
    inp.set_block("GbndRnge", [[1, save.nbnd]])
    notes.append(f"BndsRnXp, GbndRnge: bands 1–{save.nbnd}, all the SAVE has (yambo's default): "
                 "converge them with more bands in the nscf.")
    inp.set("NGsBlkXp", block_ry, "Ry")
    notes.append(f"NGsBlkXp = {block_ry:g} Ry: the screening's size, the first value to converge "
                 "(yambo's default, 1 G-vector, gives an unconverged GW without a word).")
    notes.append("EXXRLvcs, VXCRLvcs: left to yambo, all the G-vectors (exact).")
    nk = save.nk_ibz
    inp.set_block("QPkrange", [[1, nk or 1, first, last]])
    notes.append(f"QPkrange: k-points 1–{nk or 1}" + ("" if nk else " (the count is not known: check it)")
                 + f", bands {first}–{last}"
                 + (" (spinors: 4 each side)" if save.spinors and not qp_bands else "") + "."
                 + " Once p2y has run, vwf project bands --fit grows it to whole degenerate sets "
                   "at the gap's edges.")
    inp.set("DysSolver", "n")
    _cutoff(inp, save, cutoff, notes, rim_w=rim_w, version=version, fields=("LongDrXp",),
            block="NGsBlkXp")
    notes += _roles(inp, save, tasks, "gw", (first, last))
    return Made(inp, list(GW_RUNLEVELS), notes)


def bse_input(save: SaveInfo, *, qp_db: str | None = None, scissor_ev: float | None = None,
              bands: tuple[int, int] | None = None, block_ry: float = SCREENING_BLOCK_RY,
              exchange_ry: float = BSE_EXCHANGE_RY, tasks: int = 1, scissor_from=None,
              cutoff=True, rim_w=False, version=None) -> Made:
    """A BSE input for Visual Excitons: optics, the SEX kernel with its own
    static screening, diagonalisation, the excitons' wave functions written;
    QP energies from a GW (`qp_db`: the path of its ndb.QP, relative to where
    the BSE runs) or a scissor of the DFT energies: a shift (`scissor_ev`),
    or one fitted to a GW's ndb.QP on the local disk (`scissor_from`, a path:
    shift and stretches, fitted to the BSE's bands). A layer gets its
    Coulomb cutoff (`cutoff`), and RIM-W in the kernel if `rim_w` (off by
    default: yambo supports it, its tutorials' BSEs do not use it)."""
    if sum(x is not None for x in (qp_db, scissor_ev, scissor_from)) != 1:
        raise ValueError("give the GW's QP database, a scissor shift, or an ndb.QP to fit one to "
                         "(one of them)")
    inp, notes = YamboInput(), []
    for r in BSE_RUNLEVELS:
        inp.set_flag(r)
    first, last = bands or band_window(save)
    inp.set("BSKmod", "SEX")
    inp.set("BSSmod", "d")
    inp.set_flag("WRbsWF")
    notes.append("BSSmod = d, WRbsWF: the exciton energies and wave functions Visual Excitons reads.")
    inp.set_block("BSEBands", [[first, last]])
    size = bse_size(first, last, save.occupied, save.nk_bz) if save.occupied else None
    if size:
        notes.append(f"BSE size: {size} transitions ({save.occupied - first + 1} valence × "
                     f"{last - save.occupied} conduction bands × {save.nk_bz} k-points of the zone)"
                     + (f"; above {LARGE_BSE}, consider SLEPc (BSSmod = \"s\", BSSNEig)" if size > LARGE_BSE else "")
                     + ".")
    notes.append(f"BSEBands: {first}–{last}, around the gap"
                 + (" (spinors: 4 each side)" if save.spinors and not bands else "")
                 + "; more bands for excitons higher in energy. Once p2y has run, vwf project bands "
                   "--fit grows it to whole degenerate sets at the gap's edges.")
    inp.set("BSENGexx", exchange_ry, "Ry")
    notes.append(f"BSENGexx = {exchange_ry:g} Ry: the exchange's size, to converge.")
    inp.set_block("BndsRnXs", [[1, save.nbnd]])
    inp.set("NGsBlkXs", block_ry, "Ry")
    notes.append(f"Static screening in this run: BndsRnXs 1–{save.nbnd}, NGsBlkXs = {block_ry:g} Ry "
                 "(as the GW's: the screened interaction's size, BSENGBlk, follows it).")
    if qp_db is not None:
        inp.set("KfnQPdb", f"E < {qp_db}")
        notes.append(f"QP energies from the GW: {qp_db} (it must cover BSEBands at every k-point).")
    elif scissor_from is not None:
        from .scissor import scissor_from as fit
        sc = fit(scissor_from, save.occupied, (first, last))
        inp.set_block("KfnQP_E", [sc.row], "")
        notes.append(f"Scissor fitted to {scissor_from} (no GW in this chain): " + " ".join(
            line.strip() for line in sc.lines()))
    else:
        inp.set_block("KfnQP_E", [[float(scissor_ev), 1.0, 1.0]], "")
        notes.append(f"Scissor: the empty bands raised by {scissor_ev:g} eV (no GW): a cheaper, "
                     "rougher BSE; the shift is yours to choose (from a GW, experiment, or fitted "
                     "to a GW's ndb.QP: --scissor-from).")
    _cutoff(inp, save, cutoff, notes, rim_w=rim_w, version=version, fields=("LongDrXs", "BLongDir"), block="NGsBlkXs")
    notes += _roles(inp, save, tasks, "bse", (first, last))
    return Made(inp, list(BSE_RUNLEVELS), notes)


# -- parallel roles ---------------------------------------------------------------------------

# Roles filled first, per section (the others keep 1)
_ORDER = {"X_and_IO": ["c", "v", "k", "q"], "DIP": ["k", "c", "v"], "SE": ["qp", "b", "q"],
          "BS": ["k", "eh", "t"]}


def _roles(inp: YamboInput, save: SaveInfo, tasks: int, kind: str, window) -> list[str]:
    """`<section>_CPU` and `_ROLEs` for `tasks` MPI processes, each role within
    its size (more processes than elements stop yambo: Phase 3's check)."""
    if tasks <= 1:
        return []
    occupied = save.occupied or 0
    nbz = save.nk_bz
    sizes = {"DIP": {"k": save.nk_ibz, "c": save.nbnd - occupied if occupied else None,
                     "v": occupied or None}}
    if kind == "gw":
        sections = {"X_and_IO": ("q.g.k.c.v", {"q": save.nk_ibz, "g": None, "k": nbz,
                                                "c": save.nbnd - occupied if occupied else None,
                                                "v": occupied or None}),
                    "DIP": ("k.c.v", sizes["DIP"]),
                    "SE": ("q.qp.b", {"q": save.nk_ibz, "b": save.nbnd,
                                      "qp": (save.nk_ibz or 0) * (window[1] - window[0] + 1) or None})}
    else:
        first, last = window
        sections = {"DIP": ("k.c.v", {"k": save.nk_ibz,
                                      "c": min(save.nbnd, last) - occupied if occupied else None,
                                      "v": occupied - first + 1 if occupied else None}),
                    "BS": ("k.eh.t", {"k": save.nk_ibz, "eh": None, "t": None})}
    notes = []
    for section, (roles, size) in sections.items():
        counts = _split(tasks, roles.split("."), size, _ORDER[section])
        if counts is None:
            notes.append(f"{section}: {tasks} processes cannot be split within its roles' sizes; "
                         "left to yambo's own distribution.")
            continue
        inp.set(f"{section}_CPU", ".".join(str(counts[r]) for r in roles.split(".")))
        inp.set(f"{section}_ROLEs", roles)
    if any(f"{s}_CPU" in inp.variables for s in sections):
        notes.append(f"Parallel roles for {tasks} MPI processes, each within its size.")
    return notes


def _split(tasks: int, roles: list[str], size: dict, order: list[str]) -> dict | None:
    """Processes per role multiplying to `tasks`, each at most its size (None:
    unknown, no limit), filled in `order`; None when that cannot be done."""
    counts = {r: 1 for r in roles}
    for p in _factors(tasks):
        for r in order:
            limit = size.get(r)
            if r in counts and (limit is None or counts[r] * p <= limit):
                counts[r] *= p
                break
        else:
            return None
    return counts if math.prod(counts.values()) == tasks else None


def _factors(n: int) -> list[int]:
    out, d = [], 2
    while d * d <= n:
        while n % d == 0:
            out.append(d)
            n //= d
        d += 1
    if n > 1:
        out.append(n)
    return sorted(out, reverse=True)
