"""Structures for pw.x inputs: reading (ASE), symmetry (spglib), cards."""

import warnings

import numpy as np
import pytest
from ase import Atoms
from ase.build import bulk, graphene, mx2

from fakes import pw_input
from visual_workflows.core.structure import Structure, StructureError
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input

warnings.filterwarnings("ignore", category=DeprecationWarning)


def wse2_bulk(a=3.28, c=12.96, z=0.621):
    """2H-WSe2 (P6_3/mmc): W at 2c, Se at 4f."""
    cell = [[a, 0, 0], [-a / 2, a * np.sqrt(3) / 2, 0], [0, 0, c]]
    pos = [(1 / 3, 2 / 3, 1 / 4), (2 / 3, 1 / 3, 3 / 4), (1 / 3, 2 / 3, z), (2 / 3, 1 / 3, z - 1 / 2),
           (2 / 3, 1 / 3, 1 - z), (1 / 3, 2 / 3, 3 / 2 - z)]
    return Atoms("W2Se4", scaled_positions=np.array(pos) % 1, cell=cell, pbc=True)


@pytest.mark.parametrize("atoms, number, operations, fractional, lattice", [
    (bulk("Si", "diamond", a=5.431), 227, 48, 24, "FCC"),     # inversion needs a translation
    (mx2("MoS2", "2H", a=3.18, vacuum=10), 187, 12, 0, "HEX"),
    (graphene("BN", a=2.504, vacuum=10), 187, 12, 0, "HEX"),
    (wse2_bulk(), 194, 24, 12, "HEX"),                       # non-symmorphic: P6_3/mmc
])
def test_symmetry(atoms, number, operations, fractional, lattice):
    s = Structure.from_atoms(atoms).symmetry()
    assert (s.number, s.operations, s.fractional, s.lattice) == (number, operations, fractional, lattice)
    assert s.symmorphic == (fractional == 0)


def test_primitive_and_idealised():
    conventional = Structure.from_atoms(bulk("Si", "diamond", a=5.431, cubic=True))
    assert len(conventional.symbols) == 8
    primitive = conventional.primitive()
    assert len(primitive.symbols) == 2 and abs(primitive.volume - 5.431 ** 3 / 4) < 1e-6
    # Noise (as in an experimental CIF) hides symmetry only at tight tolerances; the
    # idealised cell has it exactly
    noisy = Structure.from_atoms(bulk("Si", "diamond", a=5.431))
    noisy.positions = noisy.positions + np.array([[0, 0, 0], [1e-4, -1e-4, 0]])
    assert noisy.symmetry(symprec=1e-6).number != 227
    assert noisy.primitive(symprec=1e-3).symmetry(symprec=1e-6).number == 227


def test_reading_files(tmp_path):
    import ase.io
    ase.io.write(tmp_path / "WSe2.cif", wse2_bulk())
    ase.io.write(tmp_path / "POSCAR", mx2("MoS2", "2H", a=3.18, vacuum=10), format="vasp")
    cif = Structure.read(tmp_path / "WSe2.cif")
    assert cif.name == "WSe2" and sorted(cif.species) == ["Se", "W"] and cif.symmetry().number == 194
    assert Structure.read(tmp_path / "POSCAR").symmetry().number == 187
    (tmp_path / "junk.cif").write_text("not a cif\n")
    with pytest.raises(StructureError, match="not a structure ASE can read"):
        Structure.read(tmp_path / "junk.cif")


def test_from_a_pw_input_with_ibrav():
    s = Structure.from_pw_input(pw_input())                 # ibrav = 2, celldm(1) = 10.26
    assert s.symbols == ["Si", "Si"] and s.symmetry().number == 227
    assert abs(s.volume - (10.26 * 0.529177210903) ** 3 / 4) < 1e-3


def test_cards_read_back_the_same():
    s = Structure.from_atoms(wse2_bulk())
    text = ("&control\n/\n&system\n ibrav = 0, nat = 6, ntyp = 2, ecutwfc = 40\n/\n&electrons\n/\n"
            "ATOMIC_SPECIES\n W 183.84 W.upf\n Se 78.97 Se.upf\n" + s.pw_cards()
            + "K_POINTS gamma\n")
    pw = read_pw_input(text)
    assert pw.cards["CELL_PARAMETERS"].option == "angstrom" and len(pw.cards["ATOMIC_POSITIONS"].lines) == 6
    again = Structure.from_pw_input(text)
    assert np.allclose(again.cell, s.cell) and np.allclose(again.positions, s.positions)
    assert again.symmetry().number == 194


def test_no_cell():
    with pytest.raises(StructureError, match="no cell"):
        Structure.from_atoms(Atoms("H2", positions=[[0, 0, 0], [0, 0, 0.74]]))


@pytest.mark.parametrize("atoms, ibrav, celldm", [
    (bulk("Si", "diamond", a=5.431), 2, [5.431 / 0.529177210903]),
    (bulk("Fe", "bcc", a=2.87), -3, [2.87 / 0.529177210903]),        # ASE's vectors are QE's -3
    (mx2("MoS2", "2H", a=3.18, vacuum=10), 4, None),
    (bulk("Bi", "rhombohedral", a=4.75, alpha=57.2), 5, [4.75 / 0.529177210903, 0, 0,
                                                       np.cos(np.radians(57.2))]),
    (bulk("Sn", "bct", a=5.8, c=3.2), 7, [5.8 / 0.529177210903, 0, 3.2 / 5.8]),
    (bulk("Ga", "orthorhombic", a=5.0, b=3.0, c=4.0), 8, [5.0 / 0.529177210903, 0.6, 0.8]),   # a, b, c kept
])
def test_qe_lattice(atoms, ibrav, celldm):
    s = Structure.from_atoms(atoms)
    moved, lattice = s.qe_lattice()
    assert lattice.ibrav == ibrav
    if celldm:
        assert np.allclose([x for x in lattice.celldm[:len(celldm)]], celldm, atol=1e-6)
    assert np.allclose(moved.cell, lattice.cell()) and abs(moved.volume - s.volume) < 1e-6
    assert moved.symmetry().number == s.symmetry().number
    assert len(moved.pw_operations()) == moved.symmetry().operations       # QE's orientation


def test_qe_lattice_settings_and_refusals():
    s = Structure.from_atoms(bulk("Fe", "bcc", a=2.87))
    assert s.qe_lattice(3)[1].ibrav == 3
    with pytest.raises(StructureError, match="not this lattice's"):
        s.qe_lattice(2)
    monoclinic = Structure(np.array([[5.0, 0, 0], [0, 6.0, 0], [1.0, 0, 7.0]]), ["Si"], np.zeros((1, 3)))
    with pytest.raises(StructureError, match="monoclinic or triclinic"):
        monoclinic.qe_lattice()


def test_qe_lattice_keeps_handedness():
    # A left-handed cell: QE's cell is right-handed, and the crystal must not
    # become its mirror image (P3_1 21 would turn into P3_2 21)
    quartz = Atoms("Si3", cell=[[4.9, 0, 0], [-2.45, 4.9 * np.sqrt(3) / 2, 0], [0, 0, 5.4]], pbc=True,
                   scaled_positions=[(0.47, 0, 1 / 3), (0, 0.47, 2 / 3), (0.53, 0.53, 0)])
    s = Structure.from_atoms(quartz)
    left = Structure(s.cell * np.array([[1], [1], [-1]]), s.symbols, s.positions)
    assert np.linalg.det(left.cell) < 0
    for structure in (s, left):
        moved, lattice = structure.qe_lattice()
        assert lattice.ibrav == 4 and moved.symmetry().number == structure.symmetry().number
