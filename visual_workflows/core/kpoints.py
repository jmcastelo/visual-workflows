"""k-points for pw.x: Monkhorst–Pack grids, their irreducible count as pw.x
finds it, and band paths.

The count is pw.x's own (qe-7.5, ported): kpoint_grid reduces the grid by
the rotations of the lattice, as pw.x finds them (yambopy_ext.qepy.symmetry),
and by time reversal (k ≡ -k) unless noinv; irreducible_BZ then splits each
point's star into the stars of the crystal's smaller group (without the
operations needing fractional translations when force_symmorphic, none with
nosym). Not for noncollinear magnetic calculations, whose symmetry depends on
the magnetization. nspin = 2 doubles pw.x's list (spin up and down): counted
apart (`for_pools`). Checked against every pw.x test-suite benchmark with an
automatic grid it can read (114 inputs).

Band paths come from the YamboPy fork's BrillouinZone (Setyawan–Curtarolo;
no monoclinic or triclinic lattices), as `K_POINTS crystal_b`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .structure import Structure


class KPointsError(ValueError):
    """k-points that cannot be made for this structure (no band path for it, say)."""


@dataclass
class Grid:
    """A Monkhorst–Pack grid, as `K_POINTS automatic` writes it."""
    n: tuple                         # (n1, n2, n3)
    shift: tuple = (0, 0, 0)         # 0 or 1: half a step

    def card(self) -> str:
        return ("K_POINTS automatic\n  " + " ".join(str(x) for x in self.n) + " "
                + " ".join(str(x) for x in self.shift) + "\n")

    @property
    def size(self) -> int:
        return self.n[0] * self.n[1] * self.n[2]


def grid_from_spacing(structure: Structure, spacing: float, shift=(0, 0, 0)) -> Grid:
    """The grid whose steps are at most `spacing` (Å⁻¹, with the 2π, as ASE's
    kspacing) along each periodic direction; 1 along the others (a layer's
    vacuum)."""
    if spacing <= 0:
        raise KPointsError("the spacing must be positive")
    reciprocal = 2 * np.pi * np.linalg.inv(structure.cell).T      # rows: b1, b2, b3 (with 2π)
    n = tuple(max(1, math.ceil(np.linalg.norm(b) / spacing - 1e-9)) if periodic else 1
              for b, periodic in zip(reciprocal, structure.periodic))
    return Grid(n, tuple(shift))


def for_yambo(grid: Grid) -> Grid:
    """The nscf grid for Yambo: the same, unshifted (Γ-centred). Yambo's
    transferred momenta q = k - k' must fall on the grid itself, which a
    shifted grid does not give."""
    return Grid(tuple(grid.n), (0, 0, 0))


def rotations(structure: Structure, *, force_symmorphic: bool = False,
              nosym: bool = False) -> np.ndarray:
    """The rotations pw.x reduces k-points with (in the cell's reduced coordinates)."""
    if nosym:
        return np.array([np.eye(3, dtype=int)])
    operations = structure.pw_operations(nofrac=force_symmorphic)
    return np.array([m for m, _ in operations], dtype=int)


def irreducible_count(structure: Structure, grid: Grid, *, force_symmorphic: bool = False,
                      nosym: bool = False, noinv: bool = False,
                      magnetic_noncollinear: bool = False) -> int | None:
    """How many k-points pw.x keeps of the grid (its "number of k points=",
    before nspin = 2 doubles the list); None for a noncollinear magnetic
    calculation (its magnetic symmetry depends on the magnetization)."""
    if magnetic_noncollinear:
        return None
    from ..yambopy_ext.qepy.symmetry import lattice_rotations
    key = (structure.cell.tobytes(), structure.positions.tobytes(), tuple(structure.kinds),
           tuple(grid.n), tuple(grid.shift), force_symmorphic, nosym, noinv)
    if key not in _COUNTS:
        lattice = [np.array(m).T for m in (lattice_rotations(structure.cell.tolist())
                                           if not nosym else [np.eye(3, dtype=int)])]
        crystal = [m.T for m in rotations(structure, force_symmorphic=force_symmorphic, nosym=nosym)]
        points = _kpoint_grid(grid, lattice, time_reversal=not noinv)
        if len(_COUNTS) > 256:
            _COUNTS.clear()
        _COUNTS[key] = sum(_stars(k, lattice, crystal, time_reversal=not noinv) for k in points)
    return _COUNTS[key]


_COUNTS: dict = {}                   # checks run as one types: the same input counted once


def _kpoint_grid(grid: Grid, lattice, *, time_reversal: bool) -> np.ndarray:
    """pw.x's kpoint_grid: the grid, reduced by the lattice's rotations (a
    rotated point off the grid is left alone), in crystal coordinates."""
    n, shift = np.array(grid.n), np.array(grid.shift)
    i, j, k = np.meshgrid(*(np.arange(x) for x in n), indexing="ij")
    points = (np.stack([i.ravel(), j.ravel(), k.ravel()], axis=1) + shift / 2) / n
    total = len(points)
    images = []                      # for each point, the grid index of each image (-1: off the grid)
    for s in lattice:
        xkr = points @ np.asarray(s).T
        xkr = xkr - np.round(xkr)
        for sign in ((1, -1) if time_reversal else (1,)):
            x = sign * xkr * n - 0.5 * shift
            on = np.all(np.abs(x - np.round(x)) <= 1e-5, axis=1)
            ijk = np.round(x + 2 * n).astype(int) % n
            index = (ijk[:, 0] * n[1] + ijk[:, 1]) * n[2] + ijk[:, 2]
            images.append(np.where(on, index, -1))
    images = np.stack(images, axis=1).tolist()
    equiv = list(range(total))
    for nk in range(total):
        if equiv[nk] != nk:
            continue
        for m in images[nk]:
            if m > nk and equiv[m] == m:
                equiv[m] = nk
    return points[[nk for nk in range(total) if equiv[nk] == nk]]


def _stars(k, lattice, crystal, *, time_reversal: bool) -> int:
    """pw.x's irreducible_BZ: the points the lattice's star of k splits into
    under the crystal's (smaller) group."""
    star = np.asarray(lattice) @ k                                  # (L, 3)
    images = np.einsum("cij,lj->lci", np.asarray(crystal), star)   # (L, C, 3)
    if time_reversal:
        images = np.concatenate([images, -images], axis=1)
    q = np.round(images * 1e5).astype(np.int64) % 100000
    keys = (q[..., 0] * 100000 + q[..., 1]) * 100000 + q[..., 2]
    return len(np.unique(keys.min(axis=1)))


def _true(value) -> bool:
    return value is True


def magnetic_noncollinear(pw) -> bool:
    """A noncollinear run with a magnetization (pw.x's domag): its symmetry
    depends on the magnetization's directions."""
    if not _true(pw.get("system", "noncolin", False)):
        return False
    system = pw.namelists.get("system", {})
    return any(key.startswith("starting_magnetization") and isinstance(value, (int, float))
               and not isinstance(value, bool) and value != 0 for key, value in system.items())


def pw_kpoint_count(pw) -> int | None:
    """The k-points of a pw.x input as pw.x prints them ("number of k points=",
    before nspin = 2 doubles the list): a grid reduced as pw.x does, a list's
    count, a path's points; None when not known (Berry phase or electric
    fields, a noncollinear magnetic run, a structure that cannot be read)."""
    from ..yambopy_ext.qepy.pw_input import PwInputError
    from .structure import StructureError
    if any(_true(pw.get("control", flag, False)) for flag in ("lberry", "lelfield", "lorbm")):
        return None
    try:
        kpoints = pw.k_points()
    except PwInputError:
        return None
    if kpoints is None:
        return None
    if kpoints.kind == "gamma":
        return 1
    if kpoints.kind == "list" and kpoints.count is not None:
        return kpoints.count
    card = pw.cards.get("K_POINTS")
    if kpoints.kind == "list":       # a path (*_b): each segment's points, and the last one
        try:
            n = int(card.lines[0].split()[0])
            weights = [int(float(line.split()[3])) for line in card.lines[1:n + 1]]
        except (IndexError, ValueError):
            return None
        return sum(weights[:-1]) + 1 if len(weights) == n and n else None
    try:
        structure = Structure.from_pw(pw)
    except StructureError:
        return None
    system = lambda name: _true(pw.get("system", name, False))   # noqa: E731
    return irreducible_count(structure, Grid(tuple(kpoints.grid), tuple(kpoints.shift)),
                             force_symmorphic=system("force_symmorphic"), nosym=system("nosym"),
                             noinv=system("noinv"), magnetic_noncollinear=magnetic_noncollinear(pw))


def full_grid_count(pw) -> int | None:
    """The k-points of the whole Brillouin zone for an unshifted automatic grid
    (Yambo's nbz); None otherwise."""
    try:
        kpoints = pw.k_points()
    except Exception:
        return None
    if kpoints is None or kpoints.kind != "automatic" or any(kpoints.shift):
        return None
    n1, n2, n3 = kpoints.grid
    return n1 * n2 * n3


def for_pools(count: int, nspin: int = 1) -> int:
    """The k-points pools share: pw.x's list, doubled with nspin = 2."""
    return count * (2 if nspin == 2 else 1)


@dataclass
class BandPath:
    """A band path as pw.x's `K_POINTS crystal_b` takes it: special points in
    the cell's reduced coordinates, each with the points pw.x puts from it to
    the next (1 at the end of a section: a jump to the next section)."""
    points: list                     # [(label, (k1, k2, k3), n)]

    @property
    def labels(self) -> list[str]:
        return [label for label, _, _ in self.points]

    @property
    def size(self) -> int:
        """How many k-points pw.x makes of it."""
        return sum(n for _, _, n in self.points[:-1]) + 1

    def card(self) -> str:
        lines = [f"K_POINTS crystal_b\n  {len(self.points)}"]
        for label, k, n in self.points:
            lines.append("  " + " ".join(f"{x:14.10f}" for x in k) + f" {n:4d}  ! {label}")
        return "\n".join(lines) + "\n"


def band_path(structure: Structure, path: str | None = None, density: float = 20.0,
              npoints: int | None = None) -> BandPath:
    """The band path through the special points of the structure's Brillouin
    zone: the lattice's standard one (Setyawan–Curtarolo), or `path` (e.g.
    "GMKG", sections split by commas: "GXWKG,LUWLK"). About `density` points
    per Å⁻¹ (2π included), or `npoints` in all, shared by length."""
    try:
        from yambopy.bz.brillouin import BrillouinZone
    except ImportError as e:
        raise KPointsError(f"band paths need the YamboPy fork ({e})") from None
    sampling = {"npoints": npoints} if npoints else {"density": density * 2 * np.pi}   # the fork: no 2π
    try:
        bz = BrillouinZone(cell=structure.cell, path_string=path, **sampling)
    except ValueError as e:
        raise KPointsError(f"no band path: {e}") from None
    points, segment = [], 0
    sections = bz.special_kpoints("red")
    for s, (labels, kpoints) in enumerate(zip(bz.path_sections, sections)):
        for j, (label, k) in enumerate(zip(labels, kpoints)):
            if j < len(labels) - 1:
                n = int(bz.intervals[segment])
                segment += 1
            else:
                n = 1                    # a section's end: straight to the next one
            points.append((label, tuple(float(x) for x in k), n))
    return BandPath(points)
