"""Structures and k-points put into pw.x inputs, written and read back."""

import warnings

import numpy as np
from ase.build import bulk, mx2

from visual_workflows.core.kpoints import Grid, band_path
from visual_workflows.core.qe_inputs import orientation_notes, set_kpoints, set_structure
from visual_workflows.core.structure import Structure
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input, write_pw_input

warnings.filterwarnings("ignore", category=DeprecationWarning)

SCF = """&control
 calculation='scf', prefix='si'
/
&system
 ibrav=2, celldm(1)=10.26, nat=2, ntyp=1, ecutwfc=30
 nspin=2, starting_magnetization(1)=0.2
/
&electrons
/
ATOMIC_SPECIES
 Si 28.086 Si.pz-vbc.UPF
ATOMIC_POSITIONS alat
 Si 0.00 0.00 0.00
 Si 0.25 0.25 0.25
K_POINTS automatic
 4 4 4 1 1 1
"""


def round_trip(pw):
    return read_pw_input(write_pw_input(pw))


def test_new_structure_in_an_input():
    pw = read_pw_input(SCF)
    layer = Structure.from_atoms(mx2("MoS2", "2H", a=3.18, vacuum=10))
    notes = set_structure(pw, layer, pseudos={"Mo": "Mo.upf"})
    pw = round_trip(pw)
    assert pw.get("system", "ibrav") == 0 and pw.get("system", "celldm(1)") is None
    assert (pw.get("system", "nat"), pw.get("system", "ntyp")) == (3, 2)
    assert [line.split()[0] for line in pw.cards["ATOMIC_SPECIES"].lines] == ["Mo", "S"]
    assert pw.cards["ATOMIC_SPECIES"].lines[0].split()[2] == "Mo.upf"
    assert pw.get("control", "prefix") == "si" and pw.get("system", "ecutwfc") == 30   # kept
    assert pw.get("system", "starting_magnetization(1)") is None                      # Si's: gone
    assert any("celldm(1)" in n for n in notes) and any("S: no pseudopotential" in n for n in notes)
    assert any("starting_magnetization(1)" in n for n in notes)
    again = Structure.from_pw_input(write_pw_input(pw))
    assert np.allclose(again.cell, layer.cell) and again.symmetry().number == 187


def test_species_keep_their_order_and_values():
    pw = read_pw_input(SCF.replace("ntyp=1", "ntyp=2").replace(
        "starting_magnetization(1)=0.2", "starting_magnetization(1)=0.2, starting_magnetization(2)=-0.4")
        .replace(" Si 28.086 Si.pz-vbc.UPF\n", " Si 28.086 Si.pz-vbc.UPF\n C 12.011 C.upf\n"))
    sic = Structure.from_atoms(bulk("SiC", "zincblende", a=4.36))
    sic.symbols = ["C", "Si"]                                    # C first in the structure
    set_structure(pw, sic)
    pw = round_trip(pw)
    assert [line.split()[0] for line in pw.cards["ATOMIC_SPECIES"].lines] == ["Si", "C"]
    assert pw.get("system", "starting_magnetization(1)") == 0.2       # still Si's
    assert pw.get("system", "starting_magnetization(2)") == -0.4      # still C's


def test_new_species_after_and_renumbered():
    pw = read_pw_input(SCF)
    s = Structure.from_atoms(bulk("Si", "diamond", a=5.43))
    s.labels = ["Si1", "Si2"]                                     # two kinds now
    set_structure(pw, s)
    lines = pw.cards["ATOMIC_SPECIES"].lines
    assert [line.split()[0] for line in lines] == ["Si1", "Si2"]
    assert lines[0].split()[1] == "28.0850"
    assert pw.cards["ATOMIC_POSITIONS"].lines[1].split()[0] == "Si2"


def test_fixed_atoms_kept_when_the_atoms_are_the_same():
    relax = SCF.replace(" Si 0.25 0.25 0.25", " Si 0.25 0.25 0.25 0 0 1")
    pw = read_pw_input(relax)
    si = Structure.from_pw_input(relax)
    notes = set_structure(pw, si)
    assert not any("flags" in n for n in notes)
    assert pw.cards["ATOMIC_POSITIONS"].lines[1].endswith("0 0 1")
    pw = read_pw_input(relax)
    notes = set_structure(pw, Structure.from_atoms(bulk("Si", "diamond", a=5.43, cubic=True)))
    assert any("flags" in n for n in notes)


def test_kpoints():
    pw = read_pw_input(SCF)
    set_kpoints(pw, Grid((6, 6, 6)))
    assert round_trip(pw).k_points().grid == (6, 6, 6)
    path = band_path(Structure.from_pw_input(SCF), "GXL")
    set_kpoints(pw, path)
    card = round_trip(pw).cards["K_POINTS"]
    assert card.option == "crystal_b" and int(card.lines[0]) == 3 and "!" not in "".join(card.lines)


def test_orientation():
    assert orientation_notes(Structure.from_atoms(bulk("Si", "diamond", a=5.43))) == []
    bi = Structure.from_atoms(bulk("Bi", "rhombohedral", a=4.75, alpha=57.2))   # ASE's orientation
    notes = orientation_notes(bi)
    assert notes and "4 of the crystal's 12" in notes[0] and "finds 12" in notes[0]
    turned = bi.oriented_for_pw()
    assert len(turned.pw_operations()) == 12 and orientation_notes(turned) == []
    assert np.allclose(turned.positions, bi.positions)
    assert np.isclose(turned.volume, bi.volume)
