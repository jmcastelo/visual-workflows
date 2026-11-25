"""GW and BSE inputs made from what the SAVE will hold (the nscf), checked as
the Phase 2 and 3 checks check yambo inputs."""

import pytest

from fakes import pw_input, upf
from visual_workflows.core.input_checks import check_for_visual_excitons, check_yambo_input
from visual_workflows.core.parallel import check_yambo, role_size_problems
from visual_workflows.core.yambo_inputs import SaveInfo, _split, band_window, bse_input, gw_input
from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input

SI = SaveInfo(nbnd=24, occupied=4, nk_ibz=16, nk_bz=216)        # the Si chain run on Tirant


def problems(made, tasks=1):
    inp = read_yambo_input(made.text("5.3"))
    return [p.message for p in check_yambo_input(inp, (5, 3)) + check_yambo(inp, tasks, 1, (5, 3))
            + check_for_visual_excitons(inp)]


def test_save_from_the_nscf(tmp_path):
    (tmp_path / "pseudos").mkdir()
    (tmp_path / "pseudos" / "Si.upf").write_text(upf())
    nscf = read_pw_input(pw_input(calculation="nscf", system="force_symmorphic = .true.")
                         .replace("nbnd = 8", "nbnd = 24"))
    save = SaveInfo.from_nscf(nscf, tmp_path / "02-nscf")
    assert (save.nbnd, save.occupied, save.nk_ibz, save.nk_bz) == (24, 4, 8, 64)
    with pytest.raises(ValueError, match="no nbnd"):
        SaveInfo.from_nscf(read_pw_input(pw_input().replace(", nbnd = 8", "")), tmp_path / "x")


def test_gw():
    made = gw_input(SI)
    inp = made.inp
    assert inp.flags == ["HF_and_locXC", "gw0", "ppa", "el_el_corr", "dyson", "em1d"]
    assert inp.block("BndsRnXp").rows == [[1, 24]] and inp.block("GbndRnge").rows == [[1, 24]]
    assert (inp.get("NGsBlkXp"), inp.variable("NGsBlkXp").unit) == (3.0, "Ry")
    assert inp.block("QPkrange").rows == [[1, 16, 3, 6]]             # 2 occupied, 2 empty bands
    assert any("first value to converge" in n for n in made.notes)
    assert problems(made) == []
    text = made.text("5.3")
    assert text.splitlines()[0] == "HF_and_locXC" and "# [Xp] Response block size" in text


@pytest.mark.parametrize("kw", [{"qp_db": "../04-gw/gw/ndb.QP"}, {"scissor_ev": 0.7}])
def test_bse_for_visual_excitons(kw):
    made = bse_input(SI, **kw)
    inp = made.inp
    assert {"optics", "bse", "bss", "em1s", "WRbsWF"} <= set(inp.flags)
    assert (inp.get("BSKmod"), inp.get("BSSmod")) == ("SEX", "d")
    assert inp.block("BSEBands").rows == [[3, 6]]
    if "qp_db" in kw:
        assert inp.get("KfnQPdb") == "E < ../04-gw/gw/ndb.QP"
    else:
        assert inp.block("KfnQP_E").rows == [[0.7, 1.0, 1.0]] and inp.get("KfnQPdb") is None
    assert problems(made) == []                                      # Visual Excitons' checks too


def test_bse_needs_one_source_of_qp_energies():
    with pytest.raises(ValueError, match="one of them"):
        bse_input(SI)
    with pytest.raises(ValueError, match="one of them"):
        bse_input(SI, qp_db="x", scissor_ev=0.5)


def test_bands_given_or_not_known():
    assert gw_input(SI, qp_bands=(1, 10)).inp.block("QPkrange").rows == [[1, 16, 1, 10]]
    metal = SaveInfo(nbnd=40, occupied=None, nk_ibz=10)
    with pytest.raises(ValueError, match="give the bands yourself"):
        gw_input(metal)
    assert bse_input(metal, scissor_ev=0.5, bands=(8, 12)).inp.block("BSEBands").rows == [[8, 12]]
    assert band_window(SaveInfo(nbnd=5, occupied=4, nk_ibz=1), 2, 3) == (3, 5)   # within the SAVE


@pytest.mark.parametrize("tasks", [2, 4, 8, 16, 48])
def test_parallel_roles_within_their_sizes(tasks):
    for made in (gw_input(SI, tasks=tasks), bse_input(SI, scissor_ev=0.5, tasks=tasks)):
        inp = read_yambo_input(made.text("5.3"))
        assert problems(made, tasks) == [], tasks
        for section in ("X_and_IO", "DIP", "SE", "BS"):
            cpu = inp.get(f"{section}_CPU")
            if cpu:
                counts = [int(x) for x in cpu.split(".")]
                assert __import__("math").prod(counts) == tasks
    # Sizes from the chain checks (Phase 3): no role above its elements
    sizes = {("DIP", "k"): 16, ("DIP", "c"): 2, ("DIP", "v"): 2, ("BS", "k"): 16}
    inp = read_yambo_input(bse_input(SI, scissor_ev=0.5, tasks=48).text("5.3"))
    assert role_size_problems(inp, 48, sizes) == []


def test_roles_that_do_not_fit_are_left_to_yambo():
    tiny = SaveInfo(nbnd=6, occupied=4, nk_ibz=1, nk_bz=1)
    made = bse_input(tiny, scissor_ev=0.5, tasks=7)                  # 7 processes: 1 k-point, 2 + 2 bands
    assert made.inp.get("DIP_CPU") is None
    assert any(n.startswith("DIP: 7 processes") and "left to yambo" in n for n in made.notes)
    assert made.inp.get("BS_CPU") == "1.7.1"                         # transitions: no limit (yambo's too)
    assert _split(12, ["k", "c", "v"], {"k": 3, "c": 2, "v": 2}, ["k", "c", "v"]) == {"k": 3, "c": 2, "v": 2}
    assert _split(13, ["k"], {"k": 4}, ["k"]) is None
    assert gw_input(SI, tasks=1).inp.get("SE_CPU") is None           # one process: nothing to set


def test_spinors_double_the_window():
    # spinor bands hold one electron: 4 each side by default (decision 1)
    from visual_workflows.core.yambo_inputs import band_window
    assert band_window(SaveInfo(nbnd=40, occupied=8, nk_ibz=16)) == (7, 10)
    assert band_window(SaveInfo(nbnd=40, occupied=8, nk_ibz=16, spinors=True)) == (5, 12)
    assert band_window(SaveInfo(nbnd=40, occupied=8, nk_ibz=16, spinors=True), 1, 1) == (8, 9)
    made = bse_input(SaveInfo(nbnd=40, occupied=8, nk_ibz=16, spinors=True), scissor_ev=1.0)
    assert made.inp.block("BSEBands").rows == [[5, 12]]
    assert any("spinors: 4 each side" in n and "--fit" in n for n in made.notes)


def test_the_bses_size():
    from visual_workflows.core.yambo_inputs import LARGE_BSE, bse_size, large_bse_problems
    from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input
    assert bse_size(3, 6, 4, 216) == 864 and bse_size(2, 7, 4, 216) == 1944      # Si on Lynx
    assert bse_size(5, 4, 4, 216) is None
    big = "optics\nbse\nbss\nBSSmod= \"d\"\n% BSEBands\n 1 | 24 |\n%\n"
    [p] = large_bse_problems(read_yambo_input(big), 4, 512)               # 4 × 20 × 512 = 40960
    assert "40960 transitions (4 × 20 × 512)" in p.message and "12.5 GB" in p.message and not p.error
    assert large_bse_problems(read_yambo_input(big), 4, 216) == []        # 17280: fine
    assert large_bse_problems(read_yambo_input(big), None, 512) == []     # size not known
    slepc = big.replace('"d"', '"s"')
    [p] = large_bse_problems(read_yambo_input(slepc), 4, 512)
    assert "without BSSNEig" in p.message and "(410 of 40960)" in p.message
    assert large_bse_problems(read_yambo_input(slepc + "BSSNEig= 200\n"), 4, 512) == []
    [p] = large_bse_problems(read_yambo_input(slepc + "BSSNEig= 99999\n"), 4, 512)
    assert "more than the BSE's 40960" in p.message
    made = bse_input(SaveInfo(nbnd=24, occupied=4, nk_ibz=16, nk_bz=216), scissor_ev=1.0)
    assert any(n.startswith("BSE size: 864 transitions (2 valence × 2 conduction bands × 216") for n in made.notes)
    assert LARGE_BSE == 20000


def test_terminator():
    """Decision 9: the Bruneval–Gonze terminator, off by default."""
    save = SaveInfo(nbnd=24, occupied=4, nk_ibz=16)
    assert read_yambo_input(gw_input(save).text("5.2")).get("GTermKind") is None
    made = gw_input(save, terminator=True)
    assert read_yambo_input(made.text("5.2")).get("GTermKind") == "BG"
    assert any(n.startswith('GTermKind = "BG"') for n in made.notes)
