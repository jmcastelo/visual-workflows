"""The band structure along a path (core/band_structure.py), and the names of
a SAVE's k-points (core/bands.py)."""

import itertools
import warnings

import netCDF4
import numpy as np
import pytest

from fakes import pw_input, upf
from visual_workflows.core.band_structure import (BandStructureError, band_structure, find_bands_xml,
                                                  is_bands_step, step_band_structure)
from visual_workflows.core.bands import k_point_names
from visual_workflows.core.jobspec import JobSpec
from visual_workflows.core.kpoints import band_path
from visual_workflows.core.project import Project
from visual_workflows.core.qe_chain import QeChain, add_qe_chain, chain_inputs
from visual_workflows.core.structure import Structure
from visual_workflows.core.kpoints import Grid

warnings.filterwarnings("ignore", category=DeprecationWarning)

HARTREE_EV = 27.211386245988
A = 10.26                                     # silicon's celldm(1), bohr


def bands_input(path="GX,LG"):
    """A bands input with a crystal_b path, as the QE chain writes it."""
    scf = pw_input(control="")
    bp = band_path(Structure.from_pw_input(scf), path, npoints=12)
    return chain_inputs(QeChain(scf, JobSpec(), bands_path=bp, bands_nbnd=8), "01-scf")["bands"], bp


def qe_xml(path, kpoints, energies_ev, calculation="bands", homo_ev=0.5, electrons=8, lsda=False):
    """A data-file-schema.xml with what the reader takes."""
    ks = "".join(
        f"<ks_energies><k_point weight=\"1\">{k[0]} {k[1]} {k[2]}</k_point>"
        f"<eigenvalues size=\"{len(e)}\">{' '.join(str(x / HARTREE_EV) for x in e)}</eigenvalues>"
        "</ks_energies>" for k, e in zip(kpoints, energies_ev))
    homo = f"<highestOccupiedLevel>{homo_ev / HARTREE_EV}</highestOccupiedLevel>" if homo_ev is not None else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<qes:espresso xmlns:qes=\"http://www.quantum-espresso.org/ns/qes/qes-1.0\">"
        f"<input><control_variables><calculation>{calculation}</calculation></control_variables></input>"
        f"<output><atomic_structure nat=\"2\" alat=\"{A}\"/><band_structure>"
        f"<lsda>{'true' if lsda else 'false'}</lsda><noncolin>false</noncolin>"
        f"<nelec>{electrons}</nelec>{homo}{ks}</band_structure></output></qes:espresso>")


def path_kpoints(bp):
    """The path's k-points in cartesian 2π/alat, as pw.x lists them."""
    structure = Structure.from_pw_input(pw_input(control=""))
    cell = np.asarray(structure.cell) / (A * 0.529177210903)       # in alat
    reciprocal = np.linalg.inv(cell).T                             # rows, 2π/alat
    points = []
    for i, (label, k, n) in enumerate(bp.points):
        if i == len(bp.points) - 1:
            points.append(np.asarray(k))
            break
        nxt = np.asarray(bp.points[i + 1][1])
        for j in range(n):
            points.append(np.asarray(k) + (nxt - np.asarray(k)) * j / n)
    return [p @ reciprocal for p in points]


def test_band_structure_along_a_path(tmp_path):
    text, bp = bands_input("GX,LG")
    kpts = path_kpoints(bp)
    nk = len(kpts)
    assert nk == bp.size
    # 4 valence bands up to 0.5 eV (the HOMO), 4 conduction bands from 1.6 eV
    # the conduction bottom at the 6th k-point only
    energies = [[-5, -2, -1, 0.5, 1.6 + 0.1 * (i != 5), 3, 4, 5] for i in range(nk)]
    qe_xml(tmp_path / "bands.xml", kpts, energies)
    bs = band_structure(tmp_path / "bands.xml", text)
    assert [label for _, label in bs.ticks] == ["Γ", "X|L", "Γ"]
    assert bs.energies.shape == (1, nk, 8) and bs.occupied == 4
    assert bs.energies[0, 0, 3] == pytest.approx(0.0, abs=1e-6)          # zero at the valence top
    assert len(bs.jumps) == 1 and len(bs.breaks) == 1
    jump = bs.jumps[0]
    assert bs.x[jump] == pytest.approx(bs.x[jump - 1])                # no distance across the jump
    assert bs.x[jump - 1] == pytest.approx(2 * np.pi / (A * 0.529177210903), rel=1e-6)   # |ΓX| = 2π/a
    gap, x_top, x_bottom = bs.gap
    assert gap == pytest.approx(1.1, abs=1e-6) and x_bottom != x_top
    # another run's XML, or another path's
    qe_xml(tmp_path / "scf.xml", kpts, energies, calculation="scf")
    with pytest.raises(BandStructureError, match="not the bands"):
        band_structure(tmp_path / "scf.xml", text)
    qe_xml(tmp_path / "short.xml", kpts[:5], energies[:5])
    with pytest.raises(BandStructureError, match="another run's"):
        band_structure(tmp_path / "short.xml", text)


def test_spin_polarised_and_metallic(tmp_path):
    text, bp = bands_input("GX")
    kpts = path_kpoints(bp)
    energies = [[-1, 0, 1, 2, -0.9, 0.1, 1.1, 2.1] for _ in kpts]      # up, then down
    qe_xml(tmp_path / "bands.xml", kpts, energies, homo_ev=None, lsda=True)
    bs = band_structure(tmp_path / "bands.xml", text)
    assert bs.energies.shape == (2, len(kpts), 4) and bs.occupied is None and bs.gap is None


def test_the_chain_keeps_the_energies_and_older_steps_are_read(tmp_path):
    p = Project.create(tmp_path / "Si", cluster="Local")
    (p.path / "pseudos").mkdir()
    (p.path / "pseudos" / "Si.upf").write_text(upf())
    scf = pw_input(control="")
    bp = band_path(Structure.from_pw_input(scf), "GX", npoints=12)
    steps = {s.id: s for s in add_qe_chain(p, QeChain(scf, JobSpec("cpu", 1, 4, 1, "8G", "01:00:00"),
                                                      nscf_grid=Grid((4, 4, 4)), nscf_nbnd=16,
                                                      bands_path=bp, bands_nbnd=8))}
    bands = steps["04-bands"]
    assert is_bands_step(bands) and not is_bands_step(steps["01-scf"])
    assert find_bands_xml(p, bands) is None
    with pytest.raises(BandStructureError, match="no band structure downloaded"):
        step_band_structure(p, bands)
    kpts = path_kpoints(bp)
    energies = [[-5, -2, -1, 0.5, 1.6, 3, 4, 5]] * len(kpts)
    # a step made before the copy: the scf step's XML, the bands run's
    shared = steps["01-scf"].folder / "outputs" / "out" / "si.save" / "data-file-schema.xml"
    qe_xml(shared, kpts, energies)
    assert find_bands_xml(p, bands) == shared
    # its own copy first
    qe_xml(bands.folder / "outputs" / "bands.xml", kpts, energies)
    assert find_bands_xml(p, bands) == bands.folder / "outputs" / "bands.xml"
    assert step_band_structure(p, bands).occupied == 4


def cubic_symmetries():
    """The 48 signed permutation matrices: the cubic group (cartesian)."""
    out = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            m = np.zeros((3, 3))
            for row, (col, s) in enumerate(zip(perm, signs)):
                m[row, col] = s
            out.append(m)
    return np.array(out)


def write_save_geometry(path, kpoints_cart):
    """An ns.db1 with fcc silicon's lattice, k-points (yambo's units) and symmetries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lattice = A / 2 * np.array([[-1, 0, 1], [0, 1, 1], [-1, 1, 0]], float)
    nk = len(kpoints_cart)
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("d", 18)
        ds.createDimension("three", 3)
        ds.createDimension("k", nk)
        ds.createDimension("b", 8)
        ds.createDimension("s", 1)
        ds.createDimension("sym", 48)
        dims = np.zeros(18)
        dims[11], dims[12], dims[14] = 1, 1, 8
        ds.createVariable("DIMENSIONS", "f4", ("d",))[:] = dims
        ds.createVariable("EIGENVALUES", "f4", ("s", "k", "b"))[:] = np.tile(np.arange(8.0), (1, nk, 1)) / HARTREE_EV
        ds.createVariable("LATTICE_VECTORS", "f8", ("three", "three"))[:] = lattice.T
        ds.createVariable("LATTICE_PARAMETER", "f8", ("three",))[:] = [A, A, A]
        # yambo's k-points: cartesian, in 2π/alat per axis
        ds.createVariable("K-POINTS", "f8", ("three", "k"))[:] = np.array(kpoints_cart).T
        ds.createVariable("SYMMETRY", "f8", ("sym", "three", "three"))[:] = cubic_symmetries()


def test_k_point_names(tmp_path):
    # Γ, an X as an irreducible point (0, -1, 0)·2π/a, an L, and a point that is none
    write_save_geometry(tmp_path / "SAVE" / "ns.db1",
                        [(0, 0, 0), (0, -1, 0), (0.5, -0.5, 0.5), (0.2, 0.1, 0)])
    assert k_point_names(tmp_path / "SAVE") == ["Γ", "X", "L", "k4"]


def test_k_point_names_without_geometry(tmp_path):
    from test_bands import write
    write(tmp_path / "SAVE" / "ns.db1")
    assert k_point_names(tmp_path / "SAVE") == ["k1", "k2"]
