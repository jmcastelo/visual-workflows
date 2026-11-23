"""The symmetry operations pw.x finds, ported from QE (PW/src/symm_base.f90,
qe-7.5: set_sym_bl, sgam_at, checksym, is_group, find_sym).

pw.x does not look for every symmetry of the crystal. It tries only 32 fixed
rotations in Cartesian axes (the cube's 24, and 8 more about z for hexagonal
lattices), with and without inversion, and keeps those that map the lattice
onto itself: a cell turned away from QE's standard orientation loses them
(an ibrav = 14 rhombohedral cell keeps only the inversion). Of those, it keeps
the ones the atoms allow, with a fractional translation only if each of its
components is 0 or ±1/n, n = 2, 3, 4, 6, and none at all in a supercell.

Coordinates: cells as rows (any length unit), positions in crystal (fractional)
coordinates. A rotation is the integer matrix M with x' = M x in crystal
coordinates (spglib's convention), and an operation is x -> M x + t.
Standard library only, as YamboPy's own modules.
"""

from __future__ import annotations

import math

EPS1 = 1e-6          # a rotation maps the lattice onto itself (set_sym_bl)
EPS2 = 1e-5          # fractional translations, and is_group
ACCEP = 1e-5         # atoms coincide (sgam_at's initial acceptance)

_S3, _C3 = math.sqrt(3) / 2, 0.5

# set_sym_bl's s0, as rows (QE's DATA fills Fortran columns: these are their
# transposes, the inverse rotations; the set is closed under inversion, so it
# is the same set)
_S0 = [
    ((1, 0, 0), (0, 1, 0), (0, 0, 1)), ((-1, 0, 0), (0, -1, 0), (0, 0, 1)),
    ((-1, 0, 0), (0, 1, 0), (0, 0, -1)), ((1, 0, 0), (0, -1, 0), (0, 0, -1)),
    ((0, 1, 0), (1, 0, 0), (0, 0, -1)), ((0, -1, 0), (-1, 0, 0), (0, 0, -1)),
    ((0, -1, 0), (1, 0, 0), (0, 0, 1)), ((0, 1, 0), (-1, 0, 0), (0, 0, 1)),
    ((0, 0, 1), (0, -1, 0), (1, 0, 0)), ((0, 0, -1), (0, -1, 0), (-1, 0, 0)),
    ((0, 0, -1), (0, 1, 0), (1, 0, 0)), ((0, 0, 1), (0, 1, 0), (-1, 0, 0)),
    ((-1, 0, 0), (0, 0, 1), (0, 1, 0)), ((-1, 0, 0), (0, 0, -1), (0, -1, 0)),
    ((1, 0, 0), (0, 0, -1), (0, 1, 0)), ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    ((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (-1, 0, 0), (0, 1, 0)),
    ((0, 0, -1), (1, 0, 0), (0, -1, 0)), ((0, 0, 1), (-1, 0, 0), (0, -1, 0)),
    ((0, 1, 0), (0, 0, 1), (1, 0, 0)), ((0, -1, 0), (0, 0, -1), (1, 0, 0)),
    ((0, -1, 0), (0, 0, 1), (-1, 0, 0)), ((0, 1, 0), (0, 0, -1), (-1, 0, 0)),
    ((_C3, _S3, 0), (-_S3, _C3, 0), (0, 0, 1)), ((_C3, -_S3, 0), (_S3, _C3, 0), (0, 0, 1)),
    ((-_C3, _S3, 0), (-_S3, -_C3, 0), (0, 0, 1)), ((-_C3, -_S3, 0), (_S3, -_C3, 0), (0, 0, 1)),
    ((_C3, -_S3, 0), (-_S3, -_C3, 0), (0, 0, -1)), ((_C3, _S3, 0), (_S3, -_C3, 0), (0, 0, -1)),
    ((-_C3, -_S3, 0), (-_S3, _C3, 0), (0, 0, -1)), ((-_C3, _S3, 0), (_S3, _C3, 0), (0, 0, -1)),
]


# -- 3x3 helpers ------------------------------------------------------------------------

def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _t(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def _inv(a):
    (a11, a12, a13), (a21, a22, a23), (a31, a32, a33) = a
    det = (a11 * (a22 * a33 - a23 * a32) - a12 * (a21 * a33 - a23 * a31)
           + a13 * (a21 * a32 - a22 * a31))
    cof = [[a22 * a33 - a23 * a32, a13 * a32 - a12 * a33, a12 * a23 - a13 * a22],
           [a23 * a31 - a21 * a33, a11 * a33 - a13 * a31, a13 * a21 - a11 * a23],
           [a21 * a32 - a22 * a31, a12 * a31 - a11 * a32, a11 * a22 - a12 * a21]]
    return [[x / det for x in row] for row in cof]


def _apply(m, x):
    return [sum(m[i][k] * x[k] for k in range(3)) for i in range(3)]


def _wrap(v):
    return [x - round(x) for x in v]


def _key(m):
    return tuple(tuple(row) for row in m)


# -- set_sym_bl ---------------------------------------------------------------------------

def lattice_rotations(cell) -> list:
    """The rotations of the lattice pw.x finds (set_sym_bl): of its 32 Cartesian
    rotations, those that are integer matrices in the cell's crystal axes, and
    their products with inversion. Only the identity when they are not a
    lattice's point group (pw.x: "symmetries are disabled")."""
    lt = _t([list(map(float, row)) for row in cell])        # columns: a1, a2, a3
    lt_inv = _inv(lt)
    found = []
    for r in _S0:
        m = _mul(_mul(lt_inv, [list(row) for row in r]), lt)    # x' = M x
        if all(abs(round(v) - v) <= EPS1 for row in m for v in row):
            found.append([[int(round(v)) for v in row] for row in m])
    if len(found) not in (1, 2, 4, 6, 8, 12, 24):
        found = found[:1]
    rotations = found + [[[-v for v in row] for row in m] for m in found]
    zero = [0.0, 0.0, 0.0]
    if not is_group([(m, zero) for m in rotations]):
        return rotations[:1]
    return rotations


# -- sgam_at, checksym ------------------------------------------------------------------------

def _eqvect(a, b, f, accep):
    return all(abs(a[i] - b[i] - f[i] - round(a[i] - b[i] - f[i])) < accep for i in range(3))


def _checksym(kinds, xau, rau, ft, accep):
    return all(any(kinds[nb] == kinds[na] and _eqvect(rau[na], xau[nb], ft, accep)
                   for nb in range(len(xau))) for na in range(len(xau)))


def _allowed_translation(ft):
    """0 or ±1/n with n = 2, 3, 4, 6, component by component."""
    for x in ft:
        if abs(x) > EPS2:
            if abs(1 / x - round(1 / x)) >= EPS2 or round(1 / abs(x)) not in (2, 3, 4, 6):
                return False
    return True


def is_supercell(positions, kinds, accep: float = ACCEP) -> bool:
    """Whether a pure translation maps the crystal onto itself (pw.x then uses
    no fractional translations)."""
    xau = [list(map(float, p)) for p in positions]
    return any(kinds[na] == kinds[0]
               and _checksym(kinds, xau, xau, _wrap([xau[na][i] - xau[0][i] for i in range(3)]), accep)
               for na in range(1, len(xau)))


def _sgam_at(rotations, xau, kinds, fractional, accep):
    operations = []
    for m in rotations:
        rau = [_apply(m, x) for x in xau]
        zero = [0.0, 0.0, 0.0]
        if _checksym(kinds, xau, rau, zero, accep):
            operations.append((m, zero))
            continue
        if not fractional:
            continue
        for na in range(len(xau)):
            if kinds[na] != kinds[0]:
                continue
            ft = _wrap([rau[na][i] - xau[0][i] for i in range(3)])
            if _allowed_translation(ft) and _checksym(kinds, xau, rau, ft, accep):
                operations.append((m, _wrap([-x for x in ft])))     # QE's ft: M x - ft
                break
    return operations


def is_group(operations) -> bool:
    """Whether the operations (M, t) form a group (QE's is_group: each product
    once, translations modulo the lattice)."""
    for mi, ti in operations:
        for mj, tj in operations:
            product = _mul(mi, mj)            # x -> Mi (Mj x + tj) + ti
            t = [ti[k] + sum(mi[k][l] * tj[l] for l in range(3)) for k in range(3)]
            matches = sum(1 for mk, tk in operations if mk == product
                          and all(abs(d) < EPS2 for d in _wrap([tk[k] - t[k] for k in range(3)])))
            if matches != 1:
                return False
    return True


def pw_operations(cell, positions, kinds, *, nofrac: bool = False) -> list:
    """The symmetry operations pw.x uses (find_sym, without magnetization):
    [(M, t)], the identity first. `kinds` tells atoms apart as pw.x's species
    do (Fe1 and Fe2 are two)."""
    xau = [list(map(float, p)) for p in positions]
    kinds = list(kinds)
    rotations = lattice_rotations(cell)
    fractional = not nofrac and not is_supercell(xau, kinds)
    accep = ACCEP
    for _ in range(3):
        operations = _sgam_at(rotations, xau, kinds, fractional, accep)
        if is_group(operations):
            return operations
        accep *= 0.5
    return [(rotations[0], [0.0, 0.0, 0.0])]
