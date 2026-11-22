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
