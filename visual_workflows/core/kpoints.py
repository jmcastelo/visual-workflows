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
    lattice = [np.array(m).T for m in (lattice_rotations(structure.cell.tolist())
                                       if not nosym else [np.eye(3, dtype=int)])]
    crystal = [m.T for m in rotations(structure, force_symmorphic=force_symmorphic, nosym=nosym)]
    points = _kpoint_grid(grid, lattice, time_reversal=not noinv)
    return sum(_stars(k, lattice, crystal, time_reversal=not noinv) for k in points)


def _kpoint_grid(grid: Grid, lattice, *, time_reversal: bool) -> list:
    """pw.x's kpoint_grid: the grid, reduced by the lattice's rotations (a
    rotated point off the grid is left alone), in crystal coordinates."""
    n, shift = np.array(grid.n), np.array(grid.shift)
    total = int(np.prod(n))
    index = lambda i, j, k: (i * n[1] + j) * n[2] + k          # noqa: E731
    points = np.array([((i + shift[0] / 2) / n[0], (j + shift[1] / 2) / n[1], (k + shift[2] / 2) / n[2])
                       for i in range(n[0]) for j in range(n[1]) for k in range(n[2])])
    equiv = list(range(total))
    for nk in range(total):
        if equiv[nk] != nk:
            continue
        for s in lattice:
            xkr = s @ points[nk]
            xkr = xkr - np.round(xkr)
            for sign in ((1, -1) if time_reversal else (1,)):
                x = sign * xkr * n - 0.5 * shift
                if np.all(np.abs(x - np.round(x)) <= 1e-5):
                    i, j, k = (np.round(x + 2 * n).astype(int) % n)
                    m = index(i, j, k)
                    if m > nk and equiv[m] == m:
                        equiv[m] = nk
    return [points[nk] for nk in range(total) if equiv[nk] == nk]


def _stars(k, lattice, crystal, *, time_reversal: bool) -> int:
    """pw.x's irreducible_BZ: the points the lattice's star of k splits into
    under the crystal's (smaller) group."""
    def key(v):
        return tuple(int(x) for x in np.round(v * 1e5).astype(np.int64) % 100000)
    def orbit(v):
        images = {key(g @ v) for g in crystal}
        if time_reversal:
            images |= {key(-(g @ v)) for g in crystal}
        return min(images)
    return len({orbit(s @ k) for s in lattice})


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
