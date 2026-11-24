"""A scissor fitted to a GW's ndb.QP: the shift and stretches yambo's
KfnQP_E applies (5.2.4: a conduction state E gets shift + (c − 1)(E − CBM),
a valence state (v − 1)(E − VBM)), and where it is set."""

import pytest

from test_results import CONDUCTION, VALENCE, chain, outputs, write_qp, write_save
from visual_workflows import cli
from visual_workflows.core.project import Project
from visual_workflows.core.results import read_qp
from visual_workflows.core.scissor import (ScissorError, fit_scissor, scissor_from, set_scissor_in_text,
                                           set_step_scissor)
from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input

# DFT levels (eV, valence top 0): bands 3–4 valence, 5–6 conduction, at 3 k-points
LEVELS = {3: [-1.0, -1.5, -2.0], 4: [0.0, -0.5, -0.8], 5: [2.55, 1.2, 0.7], 6: [3.0, 2.0, 1.5]}
# GW corrections exactly of the scissor's form: offsets 0.1 (valence) and 0.7 (conduction)
V_OFF, C_OFF, V_STRETCH, C_STRETCH = 0.1, 0.7, 1.02, 1.05


def corrected(band, e):
    if band <= 4:
        return e + V_OFF + (V_STRETCH - 1) * (e - 0.0)
    return e + C_OFF + (C_STRETCH - 1) * (e - 0.7)


def write_exact(path):
    write_qp(path, [(b, k, e, corrected(b, e)) for b, es in LEVELS.items() for k, e in enumerate(es, 1)])


def test_fit_recovers_the_scissor(tmp_path):
    write_exact(tmp_path / "ndb.QP")
    sc = fit_scissor(read_qp(tmp_path / "ndb.QP", 4), 4)
    assert sc.shift == pytest.approx(C_OFF - V_OFF, abs=1e-5)
    assert sc.conduction.stretch == pytest.approx(C_STRETCH, abs=1e-5)
    assert sc.valence.stretch == pytest.approx(V_STRETCH, abs=1e-5)
    assert sc.conduction.rms < 1e-5 and sc.valence.worst < 1e-5 and sc.bands == (3, 6)
    assert sc.row == [0.6, 1.05, 1.02]
    dft, gw, with_scissor = sc.gaps["gap (on the grid)"]
    assert gw - dft == pytest.approx(C_OFF - V_OFF, abs=1e-4)          # relative to the valence top
    assert with_scissor == pytest.approx(gw, abs=1e-4)                  # the same gap as GW's
    assert any(line.startswith("KfnQP_E = 0.6 | 1.05 | 1.02 |") for line in sc.lines())


def test_fit_on_some_bands_and_its_limits(tmp_path):
    write_exact(tmp_path / "ndb.QP")
    assert scissor_from(tmp_path, bands=(4, 5)).bands == (4, 5)          # occupied found: bands at ≤ 0
    with pytest.raises(ScissorError, match="no state in bands 7–8"):
        scissor_from(tmp_path / "ndb.QP", 4, (7, 8))
    with pytest.raises(ScissorError, match="one side of the gap"):
        scissor_from(tmp_path / "ndb.QP", 4, (5, 6))


INPUT = """optics
KfnQPdb= "E < ../04-gw/gw/ndb.QP"   # [EXTQP BSK BSS] Database action
% BSEBands
 3 | 6 |                             # [BSK] Bands range
%
"""


def test_set_in_the_input():
    out = set_scissor_in_text(INPUT, [0.5, 1.03, 1.01])
    inp = read_yambo_input(out)
    assert inp.get("KfnQPdb") is None and inp.block("KfnQP_E").rows == [[0.5, 1.03, 1.01]]
    assert "# [BSK] Bands range" in out                                  # the rest as it was
    again = read_yambo_input(set_scissor_in_text(out, [0.4, 1.0, 1.0]))
    assert again.block("KfnQP_E").rows == [[0.4, 1.0, 1.0]] and out.count("KfnQP_E") == 1


def test_in_a_project(tmp_path, capsys):
    p = chain(tmp_path, gw=True, bse="gw")
    write_save(outputs(p, "03-p2y") / "SAVE" / "ns.db1", VALENCE, CONDUCTION)
    with pytest.raises(ScissorError, match="no ndb.QP downloaded"):
        set_step_scissor(p, p.step("05-bse"), "04-gw")
    write_exact(outputs(p, "04-gw") / "gw" / "ndb.QP")
    assert cli.main(["project", "scissor", "05-bse", "04-gw", "-C", str(p.path)]) == 0
    out = capsys.readouterr().out
    assert "05-bse/bse.in: the scissor fitted to 04-gw" in out and "fitted to bands 3–6" in out
    inp = read_yambo_input((Project.load(p.path).step("05-bse").folder / "bse.in").read_text())
    assert inp.block("KfnQP_E").rows == [[0.6, 1.05, 1.02]] and inp.get("KfnQPdb") is None
    assert cli.main(["project", "scissor", "05-bse", "nowhere", "-C", str(p.path)]) == 1
    assert "neither a step" in capsys.readouterr().err


def test_made_inputs(tmp_path, capsys):
    from fakes import pw_input
    from visual_workflows.core.yambo_inputs import SaveInfo, bse_input
    write_exact(tmp_path / "ndb.QP")
    made = bse_input(SaveInfo(nbnd=24, occupied=4, nk_ibz=3, nk_bz=8), scissor_from=tmp_path / "ndb.QP")
    assert made.inp.block("KfnQP_E").rows == [[0.6, 1.05, 1.02]]
    assert any(n.startswith(f"Scissor fitted to {tmp_path / 'ndb.QP'}") for n in made.notes)
    with pytest.raises(ValueError, match="one of them"):
        bse_input(SaveInfo(nbnd=24, occupied=4, nk_ibz=3), scissor_ev=0.5, scissor_from=tmp_path / "ndb.QP")
    nscf = tmp_path / "nscf.in"
    nscf.write_text(pw_input(control="calculation='nscf'", system="nbnd=24"))
    code = cli.main(["yambo-input", "bse", str(nscf), "--pseudos", str(tmp_path), "--nbnd", "24",
                     "--occupied", "4", "--nk", "3", "--scissor-from", str(tmp_path / "ndb.QP")])
    out, err = capsys.readouterr()
    assert code == 0 and "KfnQP_E" in out and "0.6 | 1.05 | 1.02" in out.replace("  ", " ")
    assert cli.main(["yambo-input", "bse", "--nbnd", "24", "--occupied", "4", "--nk", "3",
                     "--scissor-from", str(tmp_path / "missing" / "ndb.QP")]) == 1
    assert "no " in capsys.readouterr().err


def test_chain_with_a_fitted_scissor(tmp_path, capsys):
    write_exact(tmp_path / "ndb.QP")
    p = chain(tmp_path / "a", bse="scissor", scissor_from=str(tmp_path / "ndb.QP"))
    inp = read_yambo_input((p.step("04-bse").folder / "bse.in").read_text())
    assert inp.block("KfnQP_E").rows[0][0] == pytest.approx(0.6, abs=1e-3)
