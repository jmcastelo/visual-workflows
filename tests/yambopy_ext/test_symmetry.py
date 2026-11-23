"""pw.x's symmetry search, ported: the counts are those pw.x prints ("N Sym. Ops.")
in QE 7.5's test-suite benchmarks."""

import numpy as np
import pytest

from visual_workflows.core.structure import Structure
from visual_workflows.yambopy_ext.qepy.symmetry import (is_group, is_supercell, lattice_rotations,
                                                        pw_operations)


def si(z=0.25, **system):
    extra = "".join(f", {k}={v}" for k, v in system.items())
    return Structure.from_pw_input(
        "&control\n/\n&system\n ibrav=2, celldm(1)=10.20, nat=2, ntyp=1, ecutwfc=12" + extra
        + "\n/\nATOMIC_SPECIES\n Si 28.086 Si.upf\nATOMIC_POSITIONS alat\n Si 0 0 0\n"
        + f" Si {z} {z} {z}\n")


AS_TRICLINIC = ("&control\n/\n&system\n ibrav=14, A=3.70971016, B=3.70971016, C=3.70971016,"
                " cosAB=0.49517470, cosAC=0.49517470, cosBC=0.49517470, nat=2, ntyp=1\n/\n"
                "ATOMIC_SPECIES\n As 74.9 As.upf\nATOMIC_POSITIONS crystal\n"
                " As 0.290010 0.290010 0.290010\n As -0.290010 -0.290010 -0.290010\n")


def count(structure, **kw):
    return len(structure.pw_operations(**kw))


def fractional(structure):
    return sum(1 for _, t in structure.pw_operations() if np.any(np.abs(t) > 1e-6))


def test_lattices():
    a = 5.0
    assert len(lattice_rotations([[a, 0, 0], [0, a, 0], [0, 0, a]])) == 48
    assert len(lattice_rotations([[a, 0, 0], [-a / 2, a * np.sqrt(3) / 2, 0], [0, 0, 8.0]])) == 24
    assert len(lattice_rotations([[a, 0, 0], [0, 6.0, 0], [0, 0, 7.0]])) == 8
    # The same cubic lattice turned by 10° about z: pw.x's fixed rotations miss most
    t = np.radians(10)
    turned = np.array([[a, 0, 0], [0, a, 0], [0, 0, a]]) @ np.array(
        [[np.cos(t), np.sin(t), 0], [-np.sin(t), np.cos(t), 0], [0, 0, 1]])
    assert len(lattice_rotations(turned.tolist())) == 8           # 4/m: rotations about z, and inversion


def test_diamond_needs_fractional_translations():
    s = si()
    assert count(s) == 48 and fractional(s) == 24                 # Fd-3m: inversion with t
    assert count(s, nofrac=True) == 24                            # force_symmorphic
    assert is_group(s.pw_operations())


def test_translations_must_be_one_over_n():
    # Si at 0.26 (pw_twochem): spglib finds the inversion through the bond's centre
    # (t = 0.26 + ...), pw.x does not (t is not 1/n): 6 Sym. Ops., no inversion
    s = si(0.26)
    assert s.symmetry().operations == 12 and count(s) == 6


def test_orientation_matters():
    # pw_vc-relax/vc-relax1: a rhombohedral cell given as ibrav = 14 keeps only
    # the inversion (2 Sym. Ops.); spglib sees R-3m
    s = Structure.from_pw_input(AS_TRICLINIC)
    assert s.symmetry().operations == 12 and count(s) == 2


def test_supercell_has_no_fractional_translations():
    from ase.build import bulk
    s = Structure.from_atoms(bulk("Si", "diamond", a=5.431, cubic=True))     # 8 atoms
    assert is_supercell(s.positions.tolist(), s.kinds)
    assert fractional(s) == 0 and count(s) == 24


def test_kinds_are_told_apart():
    text = ("&control\n/\n&system\n ibrav=1, celldm(1)=6, nat=2, ntyp=2\n/\n"
            "ATOMIC_SPECIES\n Fe1 55.8 Fe.upf\n Fe2 55.8 Fe.upf\n"
            "ATOMIC_POSITIONS crystal\n Fe1 0 0 0\n Fe2 0.5 0.5 0.5\n")
    s = Structure.from_pw_input(text)
    assert count(s) == 48 and fractional(s) == 0                  # CsCl-like, not bcc
    assert not is_supercell(s.positions.tolist(), s.kinds)
    assert is_supercell(s.positions.tolist(), s.symbols)          # as one species: bcc in a cube
    assert len(pw_operations(s.cell.tolist(), s.positions.tolist(), s.symbols)) == 48


@pytest.mark.parametrize("ops", [
    [(np.eye(3, dtype=int).tolist(), [0.0, 0.0, 0.0]), ((-np.eye(3, dtype=int)).tolist(), [0.0, 0.0, 0.0])],
])
def test_is_group(ops):
    assert is_group(ops)
    assert not is_group(ops[1:])
