"""Results read back from downloaded Yambo databases, and their checks. The
databases are written here in the layout yambo 5.2 wrote on Lynx for bulk
silicon (variables, shapes, units: Ha; ndb.QP's zero at the valence top)."""

import numpy as np
import pytest

from fakes import pw_input, upf
from visual_workflows import cli
from visual_workflows.core.jobspec import JobSpec
from visual_workflows.core.kpoints import Grid
from visual_workflows.core.project import Project
from visual_workflows.core.qe_chain import QeChain, add_qe_chain
from visual_workflows.core.results import (HARTREE_EV, ResultsError, absorption, check_bse, find_peaks,
                                           project_results, read_bse, read_qp, read_save, report)

netCDF4 = pytest.importorskip("netCDF4")
EV = 1 / HARTREE_EV


def write_save(path, valence, conduction, electrons=8, spinors=1, spin=1):
    """An ns.db1: bands 1..n per k-point, `valence` and `conduction` (eV) the
    top valence and bottom conduction band per k (the others below and above)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    nk = len(valence)
    occupied = electrons // (1 if spinors == 2 else 2)
    nbnd = occupied + 4
    e = np.zeros((1, nk, nbnd))
    for k in range(nk):
        e[0, k, :occupied] = valence[k] - np.arange(occupied)[::-1] * 1.0
        e[0, k, occupied:] = conduction[k] + np.arange(nbnd - occupied) * 1.0
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("d", 18)
        ds.createDimension("s", 1)
        ds.createDimension("k", nk)
        ds.createDimension("b", nbnd)
        dims = np.zeros(18)
        dims[11], dims[12], dims[14] = spinors, spin, electrons
        ds.createVariable("DIMENSIONS", "f4", ("d",))[:] = dims
        ds.createVariable("EIGENVALUES", "f4", ("s", "k", "b"))[:] = (e + 5.0) * EV     # absolute: not at 0


def write_qp(path, states, z=0.8, approximation="PPA"):
    """An ndb.QP: `states` = [(band, k, E_DFT, E_QP)] in eV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(states)
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("three", 3)
        ds.createDimension("n", n)
        ds.createDimension("c", 2)
        ds.createDimension("l", len(approximation))
        table = np.array([[b, b, k] for b, k, _, _ in states], float).T
        ds.createVariable("QP_table", "f4", ("three", "n"))[:] = table
        ds.createVariable("QP_Eo", "f4", ("n",))[:] = [d * EV for _, _, d, _ in states]
        ds.createVariable("QP_E", "f4", ("n", "c"))[:] = [[q * EV, -0.01 * EV] for _, _, _, q in states]
        ds.createVariable("QP_Z", "f4", ("n", "c"))[:] = [[z, 0.0]] * n
        ds.createVariable("QP_GW_approximation", "S1", ("l",))[:] = np.array(list(approximation), "S1")


def write_bse(path, excitons, bands=(3, 6)):
    """An ndb.BS_diago_Q1: `excitons` = [(energy eV, strength)], in any order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("n", len(excitons))
        ds.createDimension("c", 2)
        ds.createDimension("two", 2)
        ds.createVariable("BS_Energies", "f4", ("n", "c"))[:] = [[e * EV, 0] for e, _ in excitons]
        root = [[np.sqrt(s), 0] for _, s in excitons]
        ds.createVariable("BS_left_Residuals", "f4", ("n", "c"))[:] = root
        ds.createVariable("BS_right_Residuals", "f4", ("n", "c"))[:] = root
        ds.createVariable("Bands", "i4", ("two",))[:] = bands


# Valence top at k1, conduction bottom at k3; the smallest direct transition at k3 (1.7 eV)
VALENCE, CONDUCTION = [0.0, -0.5, -1.0], [2.55, 1.2, 0.7]


def si_qp(path, shift_v=0.05, shift_c=0.6, **kw):
    states = []
    for k, (v, c) in enumerate(zip(VALENCE, CONDUCTION), 1):
        states += [(4, k, v, v + shift_v), (5, k, c, c + shift_c)]
    write_qp(path, states, **kw)


def test_save(tmp_path):
    write_save(tmp_path / "SAVE" / "ns.db1", VALENCE, CONDUCTION)
    s = read_save(tmp_path / "SAVE")
    assert (s.occupied, s.nbnd, s.nk_ibz) == (4, 8, 3)
    assert s.indirect.value == pytest.approx(0.7, abs=1e-4)
    assert (s.indirect.k_valence, s.indirect.k_conduction) == (1, 3)
    assert s.direct.value == pytest.approx(min(c - v for v, c in zip(VALENCE, CONDUCTION)), abs=1e-4)
    assert abs(s.energies[0, 0, 3]) < 1e-4                           # zero at the valence top
    write_save(tmp_path / "S2" / "ns.db1", VALENCE, CONDUCTION, electrons=4, spinors=2)
    assert read_save(tmp_path / "S2").occupied == 4                  # spinor bands hold one electron
    write_save(tmp_path / "metal" / "ns.db1", [0.0, 0.3], [0.2, 1.0])
    assert read_save(tmp_path / "metal").metal
    with pytest.raises(ResultsError, match="no "):
        read_save(tmp_path / "nowhere")


def test_qp(tmp_path):
    si_qp(tmp_path / "ndb.QP")
    q = read_qp(tmp_path / "ndb.QP", occupied=4)
    assert q.approximation == "PPA" and q.bands == (4, 5) and q.nk == 3
    assert [round(b.mean, 3) for b in q.shifts] == [0.05, 0.6]
    (dft, gw) = q.indirect
    assert dft.value == pytest.approx(0.7, abs=1e-4) and gw.value == pytest.approx(1.25, abs=1e-4)
    assert q.direct[1].value == pytest.approx(min(CONDUCTION[k] - VALENCE[k] for k in range(3)) + 0.55, abs=1e-4)
    assert read_qp(tmp_path / "ndb.QP").indirect[0].value == pytest.approx(0.7, abs=1e-4)   # valence: at or below 0


def test_bse_and_absorption(tmp_path):
    write_bse(tmp_path / "ndb.BS_diago_Q1", [(3.9, 0.8), (2.9, 0.0), (3.1, 1.0), (3.0, 0.01)])
    b = read_bse(tmp_path / "ndb.BS_diago_Q1")
    assert list(np.round(b.energies, 3)) == [2.9, 3.0, 3.1, 3.9]
    assert b.lowest == pytest.approx(2.9, abs=1e-4) and b.lowest_bright == pytest.approx(3.1, abs=1e-4)
    assert b.dark == 2 and b.bands == (3, 6)
    assert [round(p.energy, 1) for p in b.peaks] == [3.1, 3.9]
    w, s = absorption([1.0], [1.0], 0.1, np.linspace(0, 2, 201))
    assert s.max() == pytest.approx(1.0) and w[s.argmax()] == pytest.approx(1.0)
    assert find_peaks(w, s)[0].energy == pytest.approx(1.0)


def test_bse_checks():
    class B:
        lowest, lowest_bright = 2.4, 2.5
    texts = lambda f: " ".join(x.text for x in f)
    assert check_bse(B, 3.03, "QP", 2.55)                             # below the DFT transition
    assert "did not apply the QP" in texts(check_bse(B, 3.03, "QP", 2.55))
    assert not check_bse(B, 2.5, "DFT")                               # 0.1 eV of binding
    assert "above the lowest" in texts(check_bse(B, 2.0, "DFT"))
    assert "unusually strong" in texts(check_bse(B, 5.0, "DFT"))
    B.lowest_bright = None
    assert "No exciton absorbs" in texts(check_bse(B, 2.5, "DFT"))


# -- a project -------------------------------------------------------------------------

def chain(tmp_path, **kw):
    p = Project.create(tmp_path / "Si", cluster="Local")
    (p.path / "pseudos").mkdir()
    (p.path / "pseudos" / "Si.upf").write_text(upf())
    add_qe_chain(p, QeChain(pw_input(control=""), JobSpec("cpu", 1, 4, 1, "8G", "01:00:00"),
                            nscf_grid=Grid((6, 6, 6)), nscf_nbnd=24, **kw), yambo_version=(5, 3))
    return p


def outputs(p, step):
    return p.step(step).folder / "outputs"


def test_project_results(tmp_path):
    p = chain(tmp_path, gw=True, bse="gw")
    assert project_results(p) == []                                    # nothing downloaded
    write_save(outputs(p, "03-p2y") / "SAVE" / "ns.db1", VALENCE, CONDUCTION)
    si_qp(outputs(p, "04-gw") / "gw" / "ndb.QP")
    write_bse(outputs(p, "05-bse") / "bse" / "ndb.BS_diago_Q1", [(1.9, 0.05), (2.1, 1.0), (2.8, 0.9)])
    results = {r.step: r for r in project_results(p)}
    assert set(results) == {"03-p2y", "04-gw", "05-bse"}
    bse = results["05-bse"]
    assert bse.energies_from == "QP" and bse.transition == pytest.approx(2.25, abs=1e-4)   # 1.7 + 0.55
    assert bse.findings == []                                          # consistent
    text = "\n".join(report(project_results(p)))
    assert "DFT gap 0.70 eV (k1 → k3)" in text and "gap 0.70 → 1.25 eV" in text
    assert "on QP energies" in text and "0.35 eV below the lowest direct transition (2.25 eV)" in text
    # the GW closing the gap, and Z out of range
    si_qp(outputs(p, "04-gw") / "gw" / "ndb.QP", shift_v=0.5, shift_c=0.1, z=0.3)
    gw = {r.step: r for r in project_results(p)}["04-gw"]
    texts = " ".join(f.text for f in gw.findings)
    assert "smaller than the DFT" in texts and "outside 0.5–1" in texts


def test_project_results_scissor_and_missing_qp(tmp_path):
    p = chain(tmp_path, bse="scissor", scissor_ev=0.5)
    write_save(outputs(p, "03-p2y") / "SAVE" / "ns.db1", VALENCE, CONDUCTION)
    step = p.step("04-bse")
    write_bse(outputs(p, step.id) / "bse" / "ndb.BS_diago_Q1", [(2.9, 1.0)])
    r = {r.step: r for r in project_results(p)}[step.id]
    assert r.energies_from == "DFT + scissor" and r.scissor == 0.5
    assert r.transition == pytest.approx(min(c - v for v, c in zip(VALENCE, CONDUCTION)) + 0.5, abs=1e-4)
    # a BSE on QP energies whose ndb.QP was not downloaded
    p2 = chain(tmp_path / "b", gw=True, bse="gw")
    write_bse(outputs(p2, "05-bse") / "bse" / "ndb.BS_diago_Q1", [(2.9, 1.0)])
    r2 = project_results(p2)[0]
    assert "was not downloaded" in r2.findings[0].text and r2.transition is None


def test_command_line(tmp_path, capsys):
    p = chain(tmp_path, gw=True, bse="gw")
    assert cli.main(["project", "results", "-C", str(p.path)]) == 0
    assert "No results downloaded yet" in capsys.readouterr().out
    write_save(outputs(p, "03-p2y") / "SAVE" / "ns.db1", VALENCE, CONDUCTION)
    (outputs(p, "04-gw") / "gw").mkdir(parents=True)
    (outputs(p, "04-gw") / "gw" / "ndb.QP").write_text("not netCDF")
    assert cli.main(["project", "results", "-C", str(p.path)]) == 1
    out = capsys.readouterr().out
    assert "DFT gap 0.70 eV" in out and "Not read:" in out
    assert cli.main(["project", "results", "03-p2y", "-C", str(p.path)]) == 0
    assert "04-gw" not in capsys.readouterr().out
