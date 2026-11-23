"""QE's latgen, ported: every ibrav, against YamboPy's cells where it has them,
and against geometry where it does not."""

import glob
import math
import re
from pathlib import Path

import numpy as np
import pytest

from visual_workflows.yambopy_ext.qepy.lattice import (BOHR_ANGSTROM, LatticeError, abc2celldm,
                                                       latgen, pw_cell, pw_positions)
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input

A, B, C = 6.0, 7.5, 9.0                      # a < b < c (bohr), for the orthorhombic ones


@pytest.mark.parametrize("ibrav, celldm, parameters", [
    (1, [A], {"a": A}),
    (2, [A], {"a": A}),
    (3, [A], {"a": A}),
    (-3, [A], {"a": A}),
    (4, [A, 0, C / A], {"a": A, "c": C}),
    (5, [A, 0, 0, math.cos(math.radians(70))], {"a": A, "gamma": 70.0}),
    (-5, [A, 0, 0, math.cos(math.radians(70))], {"a": A, "gamma": 70.0}),
    (6, [A, 0, C / A], {"a": A, "c": C}),
    (7, [A, 0, C / A], {"a": A, "c": C}),
    (8, [A, B / A, C / A], {"a": A, "b": B, "c": C}),
    (9, [A, B / A, C / A], {"a": A, "b": B, "c": C}),
    (-9, [A, B / A, C / A], {"a": A, "b": B, "c": C}),
    (10, [A, B / A, C / A], {"a": A, "b": B, "c": C}),
    (11, [A, B / A, C / A], {"a": A, "b": B, "c": C}),
])
def test_same_cells_as_yambopy(ibrav, celldm, parameters):
    from yambopy.bz.lattice_data import get_lattice_data
    expected = get_lattice_data(ibrav, parameters)[0]
    assert np.allclose(latgen(ibrav, celldm), expected, atol=1e-9)


@pytest.mark.parametrize("ibrav, celldm, lengths, cosines", [
    # (|a1|, |a2|, |a3|) and (cos a2^a3, cos a1^a3, cos a1^a2) of the cell's vectors
    (12, [A, B / A, C / A, 0.3], (A, B, C), (0, 0, 0.3)),
    (-12, [A, B / A, C / A, 0, -0.2], (A, B, C), (0, -0.2, 0)),
    (14, [A, B / A, C / A, 0.1, -0.2, 0.3], (A, B, C), (0.1, -0.2, 0.3)),
])
def test_monoclinic_and_triclinic(ibrav, celldm, lengths, cosines):
    a1, a2, a3 = (np.array(v) for v in latgen(ibrav, celldm))
    assert np.allclose([np.linalg.norm(v) for v in (a1, a2, a3)], lengths)
    cos = lambda u, v: u @ v / np.linalg.norm(u) / np.linalg.norm(v)   # noqa: E731
    assert np.allclose([cos(a2, a3), cos(a1, a3), cos(a1, a2)], cosines, atol=1e-9)


@pytest.mark.parametrize("ibrav, celldm", [(13, [A, B / A, C / A, 0.3]), (-13, [A, B / A, C / A, 0, 0.3]),
                                           (91, [A, B / A, C / A])])
def test_centred_ones_have_half_the_volume(ibrav, celldm):
    conventional = {13: 12, -13: -12, 91: 8}[ibrav]
    full = abs(np.linalg.det(latgen(conventional, celldm)))
    assert abs(abs(np.linalg.det(latgen(ibrav, celldm))) - full / 2) < 1e-9


@pytest.mark.parametrize("ibrav, celldm, message", [
    (0, [A], "nonexistent"), (4, [A], "celldm(3)"), (5, [A, 0, 0, 1.0], "celldm(4)"),
    (14, [A, 1, 1, 0.9, 0.9, -0.9], "do not make sense"), (2, [0], "celldm(1)"),
])
def test_what_pw_refuses(ibrav, celldm, message):
    with pytest.raises(LatticeError, match=re.escape(message)):
        latgen(ibrav, celldm)


def test_abc_and_units():
    assert abc2celldm(4, 3.18, 3.18, 20.0, -0.5, 0, 0)[:3] == pytest.approx(
        [3.18 / BOHR_ANGSTROM, 1.0, 20.0 / 3.18])
    pw = read_pw_input("&control\n/\n&system\n ibrav=4, A=3.18, C=20.0, nat=1, ntyp=1\n/\n"
                       "ATOMIC_POSITIONS angstrom\nMo 1.0 0.0 0.0\n")
    cell, alat = pw_cell(pw)
    assert alat == pytest.approx(3.18 / BOHR_ANGSTROM)
    assert cell[2][2] == pytest.approx(20.0 / BOHR_ANGSTROM)
    symbols, positions = pw_positions(pw)
    assert symbols == ["Mo"] and positions[0][0] == pytest.approx(1.0 / BOHR_ANGSTROM)
    with pytest.raises(LatticeError, match="both celldm"):
        pw_cell(read_pw_input("&control\n/\n&system\n ibrav=1, A=3, celldm(1)=6\n/\n"))
    crystal = read_pw_input("&control\n/\n&system\n ibrav=1, celldm(1)=10, nat=1\n/\n"
                            "ATOMIC_POSITIONS crystal\nSi 1/2 0.25d0 0\n")
    assert pw_positions(crystal)[1][0] == pytest.approx([5.0, 2.5, 0.0])


def _yambopy_checkout():
    """The YamboPy fork's source tree, where it is installed editable (None otherwise)."""
    try:
        import yambopy
    except ImportError:
        return None
    root = Path(yambopy.__file__).resolve().parent.parent
    return root if (root / ".git").exists() else None


# YamboPy's tutorial and reference inputs (with the fork's checkout): every structure reads
_ROOT = _yambopy_checkout()
INPUTS = sorted(p for p in (glob.glob(str(_ROOT / "**/*"), recursive=True) if _ROOT else [])
                if re.search(r"\.(scf|nscf|in)$", p) and "&system" in Path(p).read_text(errors="replace").lower()
                and "ATOMIC_POSITIONS" in Path(p).read_text(errors="replace").upper())


@pytest.mark.skipif(not INPUTS, reason="YamboPy's inputs are not on this PC")
def test_yambopy_inputs():
    from visual_workflows.core.structure import Structure
    groups = {}
    for path in INPUTS:
        s = Structure.from_pw_input(Path(path).read_text(), Path(path).name)
        groups[Path(path).name] = s.symmetry().number
    assert groups, "no input read"
    assert all(1 <= n <= 230 for n in groups.values())
