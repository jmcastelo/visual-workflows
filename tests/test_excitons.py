"""Opening results in Visual Excitons: what a project or a step gives it
(the SAVE, BSE and QP that step used), and starting it. Visual Excitons is
a script here that records its arguments, and stops with code 2 and a
message as it does when a path holds the wrong thing."""

import os
import time

import pytest

from test_results import CONDUCTION, VALENCE, chain, outputs, write_bse, write_save
from visual_workflows import cli
from visual_workflows.core import excitons
from visual_workflows.core.excitons import ExcitonsError, opening, shell_line, start

FAKE = """#!/bin/bash
echo "$@" > "$EXCITONS_ARGS"
for a in "$@"; do
  if [ "$a" = "--bad" ]; then echo "visual-excitons: error: No SAVE in /x" >&2; exit 2; fi
done
sleep 30
"""


@pytest.fixture
def fake_excitons(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "visual-excitons"
    exe.write_text(FAKE)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    args = tmp_path / "args.txt"
    monkeypatch.setenv("EXCITONS_ARGS", str(args))
    return args


def downloaded(tmp_path, wavefunctions=True, qp=True):
    p = chain(tmp_path, gw=True, bse="gw")
    save = outputs(p, "03-p2y") / "SAVE"
    write_save(save / "ns.db1", VALENCE, CONDUCTION)
    if wavefunctions:
        (save / "ns.wf").write_text("wf")
    if qp:
        (outputs(p, "04-gw") / "gw").mkdir(parents=True)
        (outputs(p, "04-gw") / "gw" / "ndb.QP").write_text("qp")
    write_bse(outputs(p, "05-bse") / "bse" / "ndb.BS_diago_Q1", [(2.9, 1.0)])
    return p


def test_what_each_step_gives(tmp_path):
    p = chain(tmp_path / "empty", gw=True, bse="gw")
    with pytest.raises(ExcitonsError, match="get --for-excitons"):
        opening(p)
    p = downloaded(tmp_path)
    root = p.path.resolve()
    assert opening(p).arguments() == [str(root)]                   # Visual Excitons asks
    o = opening(p, p.step("05-bse"))
    assert o.arguments() == [str(root), "--save", str(root / "03-p2y/outputs/SAVE"),
                             "--bse", str(root / "05-bse/outputs/bse"),
                             "--qp", str(root / "04-gw/outputs/gw/ndb.QP"),
                             "--compute"] and o.notes == []               # a BSE: computed on opening
    o = opening(p, p.step("04-gw"))
    assert o.bse is None and o.qp.name == "ndb.QP" and o.save.name == "SAVE"
    assert opening(p, p.step("03-p2y")).arguments()[1:] == ["--save", str(root / "03-p2y/outputs/SAVE")]
    with pytest.raises(ExcitonsError, match="01-scf has nothing downloaded"):
        opening(p, p.step("01-scf"))


def test_what_is_missing(tmp_path):
    p = downloaded(tmp_path, wavefunctions=False, qp=False)
    o = opening(p, p.step("05-bse"))
    assert o.qp is None and any("not downloaded" in n for n in o.notes)
    assert any("real-space views do not" in n for n in o.notes)
    p2 = chain(tmp_path / "b", gw=True, bse="gw")
    write_bse(outputs(p2, "05-bse") / "bse" / "ndb.BS_diago_Q1", [(2.9, 1.0)])
    with pytest.raises(ExcitonsError, match="SAVE of 05-bse's results was not downloaded"):
        opening(p2, p2.step("05-bse"))


def test_start(fake_excitons, monkeypatch):
    s = start(["/p", "--save", "/p/SAVE"], wait=0.5)               # still running: left so
    assert s.pid
    os.killpg(s.pid, 15)
    for _ in range(50):
        if fake_excitons.exists():
            break
        time.sleep(0.05)
    assert fake_excitons.read_text().split() == ["/p", "--save", "/p/SAVE"]
    with pytest.raises(ExcitonsError, match="No SAVE in /x"):
        start(["/p", "--bad"], wait=5)
    assert shell_line(["/a b"]) == "visual-excitons '/a b'"
    monkeypatch.setenv("PATH", "/nowhere")
    monkeypatch.setattr(excitons.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(ExcitonsError, match="not installed"):
        excitons.command()


def test_command_line(tmp_path, fake_excitons, monkeypatch, capsys):
    p = downloaded(tmp_path)
    started = []
    monkeypatch.setattr(excitons, "start", lambda args, wait=3.0: started.append(args))
    assert cli.main(["project", "excitons", "05-bse", "--dry-run", "-C", str(p.path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("visual-excitons ") and "--bse" in out and not started
    assert cli.main(["project", "excitons", "05-bse", "-C", str(p.path)]) == 0
    assert "Visual Excitons started" in capsys.readouterr().out and started[0][2].endswith("SAVE")
    assert cli.main(["project", "excitons", "01-scf", "-C", str(p.path)]) == 1
    assert "nothing downloaded" in capsys.readouterr().err
