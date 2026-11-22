"""
lattice.py
==========
The cell and atomic positions of a pw.x input, as pw.x builds them: QE's
``latgen`` (every ``ibrav``, ``Modules/latgen.f90``) and ``abc2celldm``
(``A``, ``B``, ``C`` and cosines), ported, and the units of
``CELL_PARAMETERS`` and ``ATOMIC_POSITIONS``.

ASE reads pw.x inputs only with ``ibrav = 0``; ``yambopy.bz.lattice_data``
builds QE's cells for ibrav 1-11, -3, -5, -9. This covers all of them,
monoclinic, triclinic and 91 included.

* :func:`latgen` — the lattice vectors (bohr) of an ``ibrav`` and ``celldm``.
* :func:`pw_cell` — a :class:`~.pw_input.PwInput`'s cell (bohr) and alat.
* :func:`pw_positions` — its atoms: symbols and Cartesian positions (bohr).

Meant for ``yambopy.qepy``. Standard library only: vectors are lists.
"""

import math

__all__ = ['BOHR_ANGSTROM', 'latgen', 'abc2celldm', 'pw_cell', 'pw_positions', 'LatticeError']

BOHR_ANGSTROM = 0.529177210903       # QE's BOHR_RADIUS_ANGS (Modules/constants.f90)
_SR2, _SR3 = 1.414213562373, 1.732050807569   # as latgen.f90 writes them


class LatticeError(ValueError):
    """Lattice data pw.x would refuse (its message)."""


def latgen(ibrav, celldm):
    """
    The lattice vectors of a QE Bravais lattice, as rows, in bohr.

    Parameters
    ----------
    ibrav : int
        QE's index (1-14, -3, -5, -9, 91, -12, -13); not 0.
    celldm : sequence of 6 floats
        ``celldm(1..6)``: alat in bohr, b/a, c/a and the cosines.
    """
    c = [float(x) for x in list(celldm) + [0.0] * (6 - len(celldm))]
    a1, a2, a3 = [0.0] * 3, [0.0] * 3, [0.0] * 3
    if c[0] <= 0:
        raise LatticeError('wrong celldm(1)')

    def need(index, test, what=None):
        if not test(c[index - 1]):
            raise LatticeError(what or f'wrong celldm({index})')

    if ibrav == 1:
        a1[0] = a2[1] = a3[2] = c[0]
    elif ibrav == 2:
        t = c[0] / 2
        a1[:] = [-t, 0, t]
        a2[:] = [0, t, t]
        a3[:] = [-t, t, 0]
    elif abs(ibrav) == 3:
        t = c[0] / 2
        a1[:], a2[:], a3[:] = [t, t, t], [t, t, t], [t, t, t]
        if ibrav < 0:
            a1[0], a2[1], a3[2] = -t, -t, -t
        else:
            a2[0], a3[0], a3[1] = -t, -t, -t
    elif ibrav == 4:
        need(3, lambda x: x > 0)
        a1[0] = c[0]
        a2[0], a2[1] = -c[0] / 2, c[0] * _SR3 / 2
        a3[2] = c[0] * c[2]
    elif abs(ibrav) == 5:
        need(4, lambda x: -0.5 < x < 1.0)
        term1, term2 = math.sqrt(1 + 2 * c[3]), math.sqrt(1 - c[3])
        if ibrav == 5:
            a2[1] = _SR2 * c[0] * term2 / _SR3
            a2[2] = c[0] * term1 / _SR3
            a1[0] = c[0] * term2 / _SR2
            a1[1] = -a1[0] / _SR3
            a1[2] = a2[2]
            a3[0], a3[1], a3[2] = -a1[0], a1[1], a2[2]
        else:
            a1[0] = c[0] * (term1 - 2 * term2) / 3
            a1[1] = c[0] * (term1 + term2) / 3
            a1[2] = a1[1]
            a2[:] = [a1[2], a1[0], a1[1]]
            a3[:] = [a1[1], a1[2], a1[0]]
    elif ibrav == 6:
        need(3, lambda x: x > 0)
        a1[0] = a2[1] = c[0]
        a3[2] = c[0] * c[2]
    elif ibrav == 7:
        need(3, lambda x: x > 0)
        a2[:] = [c[0] / 2, c[0] / 2, c[2] * c[0] / 2]
        a1[:] = [a2[0], -a2[0], a2[2]]
        a3[:] = [-a2[0], -a2[0], a2[2]]
    elif ibrav == 8:
        need(2, lambda x: x > 0)
        need(3, lambda x: x > 0)
        a1[0], a2[1], a3[2] = c[0], c[0] * c[1], c[0] * c[2]
    elif abs(ibrav) == 9:
        need(2, lambda x: x > 0)
        need(3, lambda x: x > 0)
        if ibrav == 9:
            a1[0] = 0.5 * c[0]
            a1[1] = a1[0] * c[1]
            a2[0], a2[1] = -a1[0], a1[1]
        else:
            a1[0] = 0.5 * c[0]
            a1[1] = -a1[0] * c[1]
            a2[0], a2[1] = a1[0], -a1[1]
        a3[2] = c[0] * c[2]
    elif ibrav == 91:
        need(2, lambda x: x > 0)
        need(3, lambda x: x > 0)
        a1[0] = c[0]
        a2[1] = c[0] * c[1] * 0.5
        a2[2] = -c[0] * c[2] * 0.5
        a3[1], a3[2] = a2[1], -a2[2]
    elif ibrav == 10:
        need(2, lambda x: x > 0)
        need(3, lambda x: x > 0)
        a2[0] = 0.5 * c[0]
        a2[1] = a2[0] * c[1]
        a1[0] = a2[0]
        a1[2] = a2[0] * c[2]
        a3[1] = a2[0] * c[1]
        a3[2] = a1[2]
    elif ibrav == 11:
        need(2, lambda x: x > 0)
        need(3, lambda x: x > 0)
        a1[:] = [0.5 * c[0], 0.5 * c[0] * c[1], 0.5 * c[0] * c[2]]
        a2[:] = [-a1[0], a1[1], a1[2]]
        a3[:] = [-a1[0], -a1[1], a1[2]]
    elif ibrav == 12:
        need(2, lambda x: x > 0)
        need(3, lambda x: x > 0)
        need(4, lambda x: abs(x) < 1)
        sen = math.sqrt(1 - c[3] ** 2)
        a1[0] = c[0]
        a2[0], a2[1] = c[0] * c[1] * c[3], c[0] * c[1] * sen
        a3[2] = c[0] * c[2]
    elif ibrav == -12:
        need(2, lambda x: x > 0)
        need(3, lambda x: x > 0)
        need(5, lambda x: abs(x) < 1)
        sen = math.sqrt(1 - c[4] ** 2)
        a1[0] = c[0]
        a2[1] = c[0] * c[1]
        a3[0], a3[2] = c[0] * c[2] * c[4], c[0] * c[2] * sen
    elif ibrav == 13:
        need(2, lambda x: x > 0)
        need(3, lambda x: x > 0)
        need(4, lambda x: abs(x) < 1)
        sen = math.sqrt(1 - c[3] ** 2)
        a1[0] = 0.5 * c[0]
        a1[2] = -a1[0] * c[2]
        a2[0], a2[1] = c[0] * c[1] * c[3], c[0] * c[1] * sen
        a3[0], a3[2] = a1[0], -a1[2]
    elif ibrav == -13:
        need(2, lambda x: x > 0)
        need(3, lambda x: x > 0)
        need(5, lambda x: abs(x) < 1)
        sen = math.sqrt(1 - c[4] ** 2)
        a1[0] = 0.5 * c[0]
        a1[1] = a1[0] * c[1]
        a2[0], a2[1] = -a1[0], a1[1]
        a3[0], a3[2] = c[0] * c[2] * c[4], c[0] * c[2] * sen
    elif ibrav == 14:
        need(2, lambda x: x > 0)
        need(3, lambda x: x > 0)
        for i in (4, 5, 6):
            need(i, lambda x: abs(x) < 1)
        singam = math.sqrt(1 - c[5] ** 2)
        term = 1 + 2 * c[3] * c[4] * c[5] - c[3] ** 2 - c[4] ** 2 - c[5] ** 2
        if term < 0:
            raise LatticeError('celldm do not make sense, check your data')
        term = math.sqrt(term / (1 - c[5] ** 2))
        a1[0] = c[0]
        a2[0], a2[1] = c[0] * c[1] * c[5], c[0] * c[1] * singam
        a3[0] = c[0] * c[2] * c[4]
        a3[1] = c[0] * c[2] * (c[3] - c[4] * c[5]) / singam
        a3[2] = c[0] * c[2] * term
    else:
        raise LatticeError('nonexistent bravais lattice')
    return [a1, a2, a3]


def abc2celldm(ibrav, a, b, c, cosab, cosac, cosbc):
    """``celldm`` from ``A``, ``B``, ``C`` (Å) and the cosines, as QE does."""
    if a <= 0:
        raise LatticeError('incorrect lattice parameter (a)')
    celldm = [a / BOHR_ANGSTROM, b / a, c / a, 0.0, 0.0, 0.0]
    if ibrav in (14, 0):
        celldm[3:] = [cosbc, cosac, cosab]
    elif ibrav in (-12, -13):
        celldm[4] = cosac
    elif ibrav in (-5, 5, 12, 13):
        celldm[3] = cosab
    return celldm


def _celldm(pw, ibrav):
    celldm = [pw.get('system', f'celldm({i})', 0.0) for i in range(1, 7)]
    a = pw.get('system', 'a')
    if a is not None:
        if any(celldm):
            raise LatticeError('do not specify both celldm and a,b,c!')
        celldm = abc2celldm(ibrav, a, pw.get('system', 'b', 0.0), pw.get('system', 'c', 0.0),
                            pw.get('system', 'cosab', 0.0), pw.get('system', 'cosac', 0.0),
                            pw.get('system', 'cosbc', 0.0))
    return [float(x) for x in celldm]


def pw_cell(pw):
    """
    The cell of a pw.x input.

    Returns
    -------
    cell : list of 3 lists
        Lattice vectors as rows, in bohr.
    alat : float
        The length unit ``alat`` stands for, in bohr.
    """
    ibrav = pw.get('system', 'ibrav')
    if ibrav is None:
        raise LatticeError('no ibrav')
    celldm = _celldm(pw, ibrav)
    if ibrav != 0:
        cell = latgen(ibrav, celldm)
        return cell, celldm[0]
    card = pw.cards.get('CELL_PARAMETERS')
    if card is None:
        raise LatticeError('ibrav=0: must read cell parameters')
    rows = [[float(x.replace('d', 'e').replace('D', 'e')) for x in line.split()[:3]]
            for line in card.lines[:3]]
    if len(rows) != 3 or any(len(r) != 3 for r in rows):
        raise LatticeError('CELL_PARAMETERS: three rows of three numbers')
    option = card.option or ('alat' if celldm[0] else 'bohr')
    if option == 'bohr':
        if celldm[0]:
            raise LatticeError('lattice parameter specified twice')
        scale = 1.0
    elif option == 'angstrom':
        if celldm[0]:
            raise LatticeError('lattice parameter specified twice')
        scale = 1 / BOHR_ANGSTROM
    elif option == 'alat':
        if not celldm[0]:
            raise LatticeError('lattice parameter not specified')
        scale = celldm[0]
    else:
        raise LatticeError(f'unknown CELL_PARAMETERS option {option}')
    cell = [[x * scale for x in row] for row in rows]
    alat = celldm[0] or math.sqrt(sum(x * x for x in cell[0]))
    return cell, alat


def pw_positions(pw):
    """
    The atoms of a pw.x input: their symbols (the labels of ATOMIC_POSITIONS)
    and Cartesian positions in bohr.
    """
    cell, alat = pw_cell(pw)
    card = pw.cards.get('ATOMIC_POSITIONS')
    if card is None:
        raise LatticeError('atomic position info missing')
    option = card.option or 'alat'
    if option == 'crystal_sg':
        raise LatticeError('ATOMIC_POSITIONS crystal_sg (space-group positions) is not read here')
    nat = pw.get('system', 'nat')
    lines = card.lines[:nat] if isinstance(nat, int) else card.lines
    symbols, positions = [], []
    for line in lines:
        words = line.split()
        symbols.append(words[0])
        x = [_number(w) for w in words[1:4]]
        if option == 'crystal':
            x = [sum(x[i] * cell[i][j] for i in range(3)) for j in range(3)]
        elif option == 'alat':
            x = [v * alat for v in x]
        elif option == 'angstrom':
            x = [v / BOHR_ANGSTROM for v in x]
        elif option != 'bohr':
            raise LatticeError(f'unknown option for ATOMIC_POSITIONS: {option}')
        positions.append(x)
    return symbols, positions


def _number(word):
    """A coordinate as pw.x reads it: Fortran reals and simple fractions (1/3)."""
    word = word.replace('d', 'e').replace('D', 'e')
    if '/' in word:
        num, den = word.split('/', 1)
        return float(num) / float(den)
    return float(word)
