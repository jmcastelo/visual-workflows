"""Convergence studies: a step per value copied from a GW or BSE step with
one yambo variable changed in place, and a result read back from each."""

import pytest

from test_results import CONDUCTION, VALENCE, chain, outputs, si_qp, write_bse, write_save
from visual_workflows import cli
from visual_workflows.core.project import Project
from visual_workflows.core.studies import (StudyError, add_study, report, set_in_text, studies, study,
                                           study_results)
from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input

INPUT = """#  Yambo's header
optics                           # [R] Linear Response optical properties
NGsBlkXs= 3.000000         Ry    # [Xs] Response block size
% BSEBands
 3 | 6 |                             # [BSK] Bands range
%
"""


def test_one_line_changed():
    out = set_in_text(INPUT, "NGsBlkXs", "5")
    assert "NGsBlkXs= 5 Ry" in out and "# [Xs] Response block size" in out
    assert out.count("\n") == INPUT.count("\n")
    assert out.index("# [Xs]") - out.rindex("\n", 0, out.index("# [Xs]")) == \
        INPUT.index("# [Xs]") - INPUT.rindex("\n", 0, INPUT.index("# [Xs]"))   # its column kept
    assert read_yambo_input(set_in_text(INPUT, "BSEBands", "8")).block("BSEBands").rows == [[3, 8]]
    assert read_yambo_input(set_in_text(INPUT, "BSEBands", "2:7")).block("BSEBands").rows == [[2, 7]]
    added = read_yambo_input(set_in_text(INPUT, "BSENGexx", "15", "Ry"))
    assert added.variable("BSENGexx").value == 15 and added.variable("BSENGexx").unit == "Ry"
    assert read_yambo_input(set_in_text(INPUT, "BndsRnXs", "40")).block("BndsRnXs").rows == [[1, 40]]


def test_add_study(tmp_path):
    p = chain(tmp_path, gw=True, bse="gw")
    s = add_study(p, "05-bse", "ngsblkxs", ["2", "3", "4"])
    assert s.variable == "NGsBlkXs" and s.unit == "Ry" and s.name == "bse-NGsBlkXs"
    n = len(p.steps) - 3
    assert s.steps == [f"{n + i:02d}-bse-NGsBlkXs-{v}" for i, v in enumerate((2, 3, 4), 1)]
    again = Project.load(p.path)
    assert study(again, "bse-NGsBlkXs").steps == s.steps
    copy = again.step(s.steps[2])
    assert copy.after == again.step("05-bse").after and copy.job.links == again.step("05-bse").job.links
    inp = read_yambo_input((copy.folder / "bse.in").read_text())
    assert inp.get("NGsBlkXs") == 4 and inp.get("KfnQPdb") == read_yambo_input(
        (again.step("05-bse").folder / "bse.in").read_text()).get("KfnQPdb")
    b = add_study(again, "04-gw", "BndsRnXp", ["20", "1:24"], name="gw-bands")
    assert read_yambo_input((again.step(b.steps[1]).folder / "gw.in").read_text()).block("BndsRnXp").rows == [[1, 24]]
    assert [x.name for x in studies(again)] == ["bse-NGsBlkXs", "gw-bands"]
    for args, message in [(("01-scf", "NGsBlkXs", ["2", "3"]), "no yambo run"),
                          (("05-bse", "NoSuchVar", ["2", "3"]), "reads no variable"),
                          (("05-bse", "NGsBlkXs", ["2"]), "at least two"),
                          (("05-bse", "NGsBlkXs", ["2", "2"]), "repeat"),
                          (("05-bse", "BSEBands", ["2", "x"]), "not a value"),
                          (("05-bse", "NGsBlkXs", ["2", "3"]), "exists already")]:
        with pytest.raises(StudyError, match=message):
            add_study(again, *args)
    assert len(Project.load(p.path).steps) == len(again.steps)          # nothing half made


def test_results(tmp_path):
    p = chain(tmp_path, gw=True, bse="gw")
    s = add_study(p, "05-bse", "NGsBlkXs", ["1", "2", "3", "4"])
    write_save(outputs(p, "03-p2y") / "SAVE" / "ns.db1", VALENCE, CONDUCTION)
    si_qp(outputs(p, "04-gw") / "gw" / "ndb.QP")
    for step_id, bright in zip(s.steps[:3], (2.0, 2.2, 2.23)):
        write_bse(outputs(p, step_id) / "bse" / "ndb.BS_diago_Q1", [(bright - 0.1, 0.0), (bright, 1.0)])
    sr = study_results(p, s)
    assert sr.quantity == "bse-bright"
    assert [round(x.result, 2) if x.result else None for x in sr.points] == [2.0, 2.2, 2.23, None]
    assert [round(x.change, 2) if x.change is not None else None for x in sr.points] == [None, 0.2, 0.03, None]
    assert sr.converged_at == "2" and sr.points[3].state == "not run"
    text = "\n".join(report(sr))
    assert "Changes within 0.05 eV from NGsBlkXs = 2 on" in text
    assert study_results(p, s, tolerance=0.01).converged_at is None
    assert study_results(p, s, "bse-lowest").points[0].result == pytest.approx(1.9, abs=1e-4)
    with pytest.raises(StudyError, match="no quantity"):
        study_results(p, s, "nope")


def test_command_line(tmp_path, capsys):
    p = chain(tmp_path, gw=True, bse="gw")
    assert cli.main(["project", "studies", "-C", str(p.path)]) == 0
    assert "No study yet" in capsys.readouterr().out
    assert cli.main(["project", "add-study", "04-gw", "NGsBlkXp", "2", "4", "--name", "screening",
                     "-C", str(p.path)]) == 0
    out = capsys.readouterr().out
    assert "Study screening: NGsBlkXp = 2, 4 Ry, copied from 04-gw" in out and "vwf project submit 06-screening-2 07-screening-4" in out
    assert cli.main(["project", "studies", "-C", str(p.path)]) == 0
    assert "screening: NGsBlkXp over 2, 4 Ry, from 04-gw (2 steps)" in capsys.readouterr().out
    csv = tmp_path / "s.csv"
    assert cli.main(["project", "studies", "screening", "--csv", str(csv), "-C", str(p.path)]) == 0
    out = capsys.readouterr().out
    assert "the GW gap on the grid" in out and "Not enough results yet" in out
    assert csv.read_text().splitlines()[0] == "screening_value,step,state,gw-gap"
    assert cli.main(["project", "add-study", "04-gw", "Nope", "2", "4", "-C", str(p.path)]) == 1
