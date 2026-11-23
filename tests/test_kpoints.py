"""k-points: grids, pw.x's irreducible count, band paths. Expected counts are
pw.x's ("number of k points="), from runs on Tirant and QE 7.5's test-suite
benchmarks."""

import warnings

import numpy as np
import pytest
from ase.build import bulk, mx2

from fakes import pw_input
from visual_workflows.core.kpoints import (Grid, KPointsError, band_path, for_pools, for_yambo,
                                           grid_from_spacing, irreducible_count)
from visual_workflows.core.structure import Structure
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input

warnings.filterwarnings("ignore", category=DeprecationWarning)


def si(z=0.25):
    return Structure.from_pw_input(
        "&control\n/\n&system\n ibrav=2, celldm(1)=10.20, nat=2, ntyp=1\n/\n"
        "ATOMIC_SPECIES\n Si 28.086 Si.upf\nATOMIC_POSITIONS alat\n Si 0 0 0\n"
        f" Si {z} {z} {z}\n")


@pytest.mark.parametrize("structure, grid, options, expected", [
    (si(), Grid((4, 4, 4)), {}, 8),                                  # Tirant, pw.x 7.x
    (si(), Grid((4, 4, 4)), {"force_symmorphic": True}, 8),
    (si(), Grid((4, 4, 4)), {"nosym": True}, 36),                    # time reversal only
    (si(), Grid((4, 4, 4)), {"nosym": True, "noinv": True}, 64),
    (si(), Grid((4, 4, 4), (1, 1, 1)), {}, 10),                      # pw_scf
    (si(), Grid((2, 2, 2), (1, 1, 1)), {}, 2),                       # pw_scf/scf-kauto
    # pw_twochem: 6 operations, a shifted grid the lattice's rotations do not all keep
    (si(0.26), Grid((4, 4, 4), (1, 1, 1)), {}, 30),
])
def test_count_as_pw_x(structure, grid, options, expected):
    assert irreducible_count(structure, grid, **options) == expected


def test_turned_cell():
    # pw_vc-relax/vc-relax1: rhombohedral As as ibrav = 14, 2 operations only
    text = ("&control\n/\n&system\n ibrav=14, A=3.70971016, B=3.70971016, C=3.70971016,"
            " cosAB=0.49517470, cosAC=0.49517470, cosBC=0.49517470, nat=2, ntyp=1\n/\n"
            "ATOMIC_SPECIES\n As 74.9 As.upf\nATOMIC_POSITIONS crystal\n"
            " As 0.290010 0.290010 0.290010\n As -0.290010 -0.290010 -0.290010\n")
    assert irreducible_count(Structure.from_pw_input(text), Grid((4, 4, 4), (1, 1, 1))) == 32


def test_magnetic_noncollinear_is_unknown():
    assert irreducible_count(si(), Grid((4, 4, 4)), magnetic_noncollinear=True) is None


def test_pools():
    assert for_pools(8) == 8 and for_pools(8, nspin=2) == 16 and for_pools(8, nspin=4) == 8


def test_grid_from_spacing():
    s = Structure.from_atoms(bulk("Si", "diamond", a=5.431))
    b = 2 * np.pi * np.sqrt(3) / 5.431                               # |b_i| of FCC, with 2π
    assert grid_from_spacing(s, 0.2).n == (int(np.ceil(b / 0.2)),) * 3
    layer = Structure.from_atoms(mx2("MoS2", "2H", a=3.18, vacuum=10))
    assert grid_from_spacing(layer, 0.2).n[2] == 1 and grid_from_spacing(layer, 0.2).n[0] > 1
    assert Grid((6, 6, 1), (1, 1, 0)).card() == "K_POINTS automatic\n  6 6 1 1 1 0\n"
    with pytest.raises(KPointsError):
        grid_from_spacing(s, 0)
    assert for_yambo(Grid((6, 6, 1), (1, 1, 0))) == Grid((6, 6, 1), (0, 0, 0))


def test_band_path_fcc():
    path = band_path(Structure.from_atoms(bulk("Si", "diamond", a=5.431)))
    assert path.labels[:3] == ["G", "X", "W"]
    # Γ–X is 2π/a long: 20 points per Å⁻¹
    assert path.points[0][2] == round(20 * 2 * np.pi / 5.431)
    assert path.points[-1][2] == 1
    # pw.x reads the card back: its points, and as many k-points as `size`
    pw = read_pw_input(pw_input().split("K_POINTS")[0] + path.card())
    card = pw.cards["K_POINTS"]
    assert card.option == "crystal_b" and int(card.lines[0]) == len(path.points)
    assert path.size == sum(int(line.split()[3]) for line in card.lines[1:-1]) + 1


def test_band_path_given_and_sections():
    layer = Structure.from_atoms(mx2("MoS2", "2H", a=3.18, vacuum=10))
    path = band_path(layer, "GMKG", npoints=61)
    assert path.labels == ["G", "M", "K", "G"] and path.size == 61
    assert np.allclose(path.points[1][1], (0.5, 0, 0))
    two = band_path(layer, "GM,KG", npoints=40)
    assert [n for _, _, n in two.points][1] == 1                     # M: a jump to K


def test_no_band_path():
    monoclinic = Structure(np.array([[5.0, 0, 0], [0, 6.0, 0], [1.0, 0, 7.0]]), ["Si"],
                           np.zeros((1, 3)))
    with pytest.raises(KPointsError, match="no band path"):
        band_path(monoclinic)
    with pytest.raises(KPointsError, match="no band path"):
        band_path(si(), "GQ")
