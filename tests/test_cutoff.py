"""A layer's Coulomb cutoff (decision 6): the cartesian direction Yambo's
slab cutoff takes, set in the chain's GW and BSE inputs, and checked."""

import pytest

from fakes import upf
from visual_workflows.core.chain import check_chain
from visual_workflows.core.jobspec import JobSpec
from visual_workflows.core.kpoints import Grid
from visual_workflows.core.project import Project
from visual_workflows.core.qe_chain import QeChain, add_qe_chain
from visual_workflows.core.structure import Structure
from visual_workflows.core.yambo_inputs import SaveInfo, bse_input, gw_input
from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input

ase_build = pytest.importorskip("ase.build")

# An hBN layer: hexagonal (ibrav 4), a = 2.5 Å, 20 Å of cell along z
HBN = """&control
  calculation = 'scf', prefix = 'hbn', max_seconds = 3000
/
&system
  ibrav = 4, celldm(1) = 4.7243, celldm(3) = 8.0, nat = 2, ntyp = 2, ecutwfc = 40, nbnd = 8
/
&electrons
/
ATOMIC_SPECIES
  B 10.81 B.upf
  N 14.007 N.upf
ATOMIC_POSITIONS crystal
  B 0.0 0.0 0.5
  N 0.333333333 0.666666667 0.5
K_POINTS automatic
6 6 1 0 0 0
"""


def test_slab_direction():
    layer = ase_build.mx2("MoS2", vacuum=8.0)
    assert Structure.from_atoms(layer).slab_direction()[0] == "z"
    tilted = layer.copy()
    tilted.rotate(30, "x", rotate_cell=True)
    direction, note = Structure.from_atoms(tilted).slab_direction()
    assert direction is None and "--turn" in note
    assert Structure.from_atoms(tilted).oriented_for_pw().slab_direction()[0] == "z"
    isolated = layer.copy()
    isolated.center(vacuum=8.0, axis=(0, 1, 2))
    assert "a molecule" in Structure.from_atoms(isolated).slab_direction()[1]
    assert Structure.from_atoms(ase_build.bulk("Si")).slab_direction() == (None, "")
    assert Structure.from_pw_input(HBN).slab_direction()[0] == "z"


def test_inputs_for_a_layer():
    save = SaveInfo(nbnd=40, occupied=4, nk_ibz=7, nk_bz=36, slab="z")
    for made in (gw_input(save), bse_input(save, scissor_ev=1.0)):
        inp = read_yambo_input(made.text("5.2"))
        assert inp.has_flag("rim_cut") and inp.get("CUTGeo") == "slab z"
        assert inp.get("RandQpts") == 1000000 and inp.variable("RandGvec").unit == "RL"
        assert any(n.startswith("Coulomb cutoff for a layer") for n in made.notes)
    assert not read_yambo_input(gw_input(save, cutoff=False).text("5.2")).has_flag("rim_cut")
    bulk = read_yambo_input(gw_input(SaveInfo(nbnd=24, occupied=4, nk_ibz=16)).text("5.2"))
    assert not bulk.has_flag("rim_cut") and bulk.get("CUTGeo") is None
    tilted = SaveInfo(nbnd=24, occupied=4, nk_ibz=16, vacuum_note="vacuum along c, which does not lie …")
    assert any(n.startswith("No Coulomb cutoff: vacuum along c") for n in gw_input(tilted).notes)


def project(tmp_path):
    p = Project.create(tmp_path / "hBN", cluster="Local")
    (p.path / "pseudos").mkdir()
    (p.path / "pseudos" / "B.upf").write_text(upf("B", 3.0))
    (p.path / "pseudos" / "N.upf").write_text(upf("N", 5.0))
    add_qe_chain(p, QeChain(HBN, JobSpec("cpu", 1, 4, 1, "8G", "01:00:00"), nscf_grid=Grid((6, 6, 1)),
                            nscf_nbnd=40, gw=True, bse="gw"), yambo_version=(5, 3))
    return Project.load(p.path)


def test_the_chain_of_a_layer(tmp_path):
    p = project(tmp_path)
    for step, name in (("04-gw", "gw.in"), ("05-bse", "bse.in")):
        inp = read_yambo_input((p.step(step).folder / name).read_text())
        assert inp.get("CUTGeo") == "slab z" and inp.has_flag("rim_cut")
    assert not any("Coulomb cutoff" in str(x) or "CUTGeo" in str(x) for v in check_chain(p).values() for x in v)


def test_checked(tmp_path):
    p = project(tmp_path)
    gw = p.step("04-gw").folder / "gw.in"
    text = gw.read_text()
    gw.write_text("\n".join(line for line in text.splitlines()
                            if not line.startswith(("rim_cut", "CUTGeo", "RandQpts", "RandGvec"))) + "\n")
    found = " ".join(str(x) for x in check_chain(Project.load(p.path)).get("04-gw", []))
    assert "a layer, vacuum along c (z), but this run has no Coulomb cutoff" in found
    gw.write_text(text.replace('"slab z"', '"slab x"'))
    found = " ".join(str(x) for x in check_chain(Project.load(p.path)).get("04-gw", []))
    assert 'vacuum is along z: "slab z"' in found
