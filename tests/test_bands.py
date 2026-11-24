"""A downloaded SAVE's bands: energy ranges, the bands of the transitions up
to an energy, the BSE's size, split degeneracies, and the density of states."""

import numpy as np
import pytest

from test_results import EV, chain, outputs
from visual_workflows import cli
from visual_workflows.core import bands as bands_module
from visual_workflows.core.bands import find_save, save_bands, used_windows
from visual_workflows.core.results import ResultsError

netCDF4 = pytest.importorskip("netCDF4")

# (k, band) eV, valence top 0 (bands 1–4 occupied, 8 electrons). At k1 bands
# 2–4 are degenerate (the top) and so are 5–6; k2 has the smallest gap.
LEVELS = np.array([[-8.0, 0.0, 0.0, 0.0, 3.0, 3.0, 4.0, 9.0],
                   [-7.0, -3.0, -2.0, -0.5, 1.0, 2.5, 5.0, 8.0]])


def write(path, levels=LEVELS, electrons=8):
    path.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("d", 18)
        ds.createDimension("s", 1)
        ds.createDimension("k", levels.shape[0])
        ds.createDimension("b", levels.shape[1])
        dims = np.zeros(18)
        dims[11], dims[12], dims[14] = 1, 1, electrons
        ds.createVariable("DIMENSIONS", "f4", ("d",))[:] = dims
        ds.createVariable("EIGENVALUES", "f4", ("s", "k", "b"))[:] = (levels[None] + 2.0) * EV


@pytest.fixture
def sb(tmp_path, monkeypatch):
    # YamboPy cannot expand this SAVE (no symmetries): weights given, 1 and 3 of 4 points
    monkeypatch.setattr(bands_module, "_weights", lambda path, nk: (np.array([0.25, 0.75]), 4, []))
    write(tmp_path / "SAVE" / "ns.db1")
    return save_bands(tmp_path / "SAVE")


def test_bands_and_windows(sb):
    assert [(b.index, round(b.low, 2), round(b.high, 2), b.occupied) for b in sb.bands[3:5]] == \
        [(4, -0.5, 0.0, True), (5, 1.0, 3.0, False)]
    assert sb.save.indirect.value == pytest.approx(1.0, abs=1e-4) and sb.save.direct.value == pytest.approx(1.5, abs=1e-4)
    assert sb.window(1.0) is None
    w = sb.window(1.5)
    assert (w.first, w.last, w.valence, w.conduction, w.bse_size) == (4, 5, 1, 1, 4)
    w = sb.window(3.0)                                # k1: 2–4 → 5–6 (3 eV); k2: 4 → 6, 3 → 5
    assert (w.first, w.last, w.bse_size) == (2, 6, 3 * 2 * 4)
    assert sb.reach(3.0) == pytest.approx((-2.0, 3.0), abs=1e-4)


def test_split_degeneracies(sb):
    splits = sb.splits(3, 5)
    assert len(splits) == 2 and "bands 2 and 3 are degenerate at k1 (0.00 eV)" in splits[0]
    assert "bands 5 and 6" in splits[1] and "leaves out its partner above" in splits[1]
    assert sb.splits(2, 6) == []


def test_dos_counts_the_electrons(sb):
    w, d = sb.dos(0.05)
    below = w <= 0.25
    assert np.trapezoid(d[below], w[below]) == pytest.approx(8.0, abs=0.05)


def test_equal_weights_when_yambopy_cannot(tmp_path):
    write(tmp_path / "SAVE" / "ns.db1")
    sb = save_bands(tmp_path / "SAVE")
    assert list(sb.weights) == [0.5, 0.5] and sb.nk_bz is None and "equal weights" in sb.notes[0]
    assert sb.window(3.0).bse_size is None


def test_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bands_module, "_weights", lambda path, nk: (np.array([0.25, 0.75]), 4, []))
    p = chain(tmp_path, gw=True, bse="gw")
    with pytest.raises(ResultsError, match="No step has a downloaded SAVE"):
        find_save(p)
    write(outputs(p, "03-p2y") / "SAVE" / "ns.db1")
    assert find_save(p, p.step("05-bse"))[0].id == "05-bse"            # through its SAVE link
    assert {(s, n) for s, n, _, _ in used_windows(p)} == {("04-gw", "QPkrange"), ("05-bse", "BSEBands")}
    dos = tmp_path / "dos.txt"
    assert cli.main(["project", "bands", "--window", "3", "--dos", str(dos), "-C", str(p.path)]) == 0
    out = capsys.readouterr().out
    assert "Transitions up to 3 eV: bands 2–6 (3 valence, 2 conduction); BSE size 3 × 2 × 4 = 24" in out
    assert "05-bse BSEBands bands" in out and dos.read_text().startswith("# energy")
    assert cli.main(["project", "bands", "01-scf", "-C", str(p.path)]) == 1


# -- the windows fitted (decision 2) -------------------------------------------------------

def test_edge_sets(sb):
    # valence top and lowest direct transition: k1 and k2; at k1, bands 2–4 and 5–6 degenerate
    assert sb.edge_sets() == (2, 6)
    assert sb.fitted(3, 5) == (2, 6) and sb.fitted(1, 8) == (1, 8)          # grown, never shrunk


def test_fit_the_projects_windows(tmp_path, monkeypatch, capsys):
    from visual_workflows.core.chain import check_chain
    from visual_workflows.core.project import Project
    from visual_workflows.core.studies import add_study
    from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input
    monkeypatch.setattr(bands_module, "_weights", lambda path, nk: (np.array([0.25, 0.75]), 4, []))
    p = chain(tmp_path, gw=True, bse="gw")
    study = add_study(p, "05-bse", "BSEBands", ["3:6", "2:7"])
    write(outputs(p, "03-p2y") / "SAVE" / "ns.db1")
    bse_text = (p.step("05-bse").folder / "bse.in").read_text()
    k_range = read_yambo_input((p.step("04-gw").folder / "gw.in").read_text()).block("QPkrange").rows[0][:2]
    assert read_yambo_input(bse_text).block("BSEBands").rows == [[3, 6]]
    found = check_chain(Project.load(p.path))
    assert "cuts the degenerate states at the gap's edges: 2–6 holds them whole" in \
        " ".join(str(x) for x in found["05-bse"]) and study.steps[0] not in found
    assert cli.main(["project", "bands", "--fit", "-C", str(p.path)]) == 0
    out = capsys.readouterr().out
    assert "Degenerate sets at the gap's edges: bands 2–6 hold them whole" in out
    assert "05-bse BSEBands 3–6 → 2–6 (bse.in)" in out and "04-gw QPkrange 3–6 → 2–6 (gw.in)" in out
    assert f"not changed (set by the study {study.name})" in out and "Fitted 2 windows." in out
    p = Project.load(p.path)
    bse = read_yambo_input((p.step("05-bse").folder / "bse.in").read_text())
    gw = read_yambo_input((p.step("04-gw").folder / "gw.in").read_text())
    assert bse.block("BSEBands").rows == [[2, 6]] and gw.block("QPkrange").rows[0][2:] == [2, 6]
    assert gw.block("QPkrange").rows[0][:2] == k_range                       # its k-points kept
    assert (p.step("05-bse").folder / "bse.in").read_text().count("\n") == bse_text.count("\n")
    assert "cuts the degenerate" not in " ".join(str(x) for v in check_chain(p).values() for x in v)
    assert cli.main(["project", "bands", "--fit", "-C", str(p.path)]) == 0
    assert "No window to fit" in capsys.readouterr().out


def test_a_step_that_ran_keeps_its_window(sb, tmp_path):
    from datetime import datetime, timezone

    from visual_workflows.core.bands import fit_windows
    from visual_workflows.core.project import JobRecord
    from visual_workflows.core.scheduler import JobState
    p = chain(tmp_path, gw=True, bse="gw")
    step = p.step("05-bse")
    step.jobs.append(JobRecord(id="1", submitted=datetime.now(timezone.utc), state=JobState.COMPLETED))
    step.save()
    fits = {f.step: f for f in fit_windows(p, sb)}
    assert fits["05-bse"].skipped == "05-bse has run with 3–6" and not fits["04-gw"].skipped
