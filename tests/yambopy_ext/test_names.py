"""The lists of names pw.x and yambo read, per version (extracted from their
sources by scripts/extract-input-names.py)."""

from visual_workflows.yambopy_ext.io.yambo_variables import (canonical_name, yambo_names,
                                                              yambo_options)
from visual_workflows.yambopy_ext.qepy.pw_namelists import pw_namelists


def test_pw_namelists_per_version():
    assert "calculation" in pw_namelists((7, 3))["control"]
    assert "symmetry_with_labels" not in pw_namelists((7, 3))["control"]
    assert "symmetry_with_labels" in pw_namelists((7, 5))["control"]
    assert "exx_type" in pw_namelists((7, 6))["system"] and "exx_type" in pw_namelists()["system"]
    assert "lkpoint_dir" not in pw_namelists()["control"] and "wf_collect" in pw_namelists()["control"]
    # Versions between or outside the known ones: the closest below, or the oldest
    assert pw_namelists((7, 4)) == pw_namelists((7, 3)) and pw_namelists((6, 8)) == pw_namelists((7, 3))
    assert pw_namelists((8, 0)) == pw_namelists((7, 6))


def test_yambo_names_per_version():
    v53 = yambo_names((5, 3))
    for name in ("optics", "bse", "bss", "dipoles", "gw0", "HF_and_locXC", "rim_cut", "em1s"):
        assert name in v53["runlevels"], name
    assert "WRbsWF" in v53["flags"]
    for name in ("BSEBands", "BSSmod", "KfnQPdb", "QPkrange", "GbndRnge", "BndsRnXp", "NGsBlkXs",
                 "BS_CPU", "X_and_IO_nCPU_LinAlg_INV", "K_Threads", "OSCLL_CPU"):
        assert name in v53["variables"], name
    assert "OSCLL_CPU" not in yambo_names((5, 1))["variables"]          # new in 5.3
    assert "SX_CPU" in yambo_names((5, 4))["variables"] and "SX_CPU" not in v53["variables"]
    assert v53["units"]["BSENGexx"] == "G_unit" and v53["units"]["BEnRange"] == "E_unit"


def test_canonical_names():
    assert canonical_name("bsengexx") == "BSENGexx"
    assert canonical_name("X_all_q_CPU") is None                       # Yambo 4
    assert canonical_name("Field2_Freq", (5, 3)) == "Field2_Freq"
    assert canonical_name("SX_CPU", (5, 3)) is None


def test_yambo_options():
    kinds = {o["short"] or o["long"]: o["kind"] for o in yambo_options("yambo", (5, 3))}
    assert kinds["F"] == kinds["J"] == kinds["walltime"] == kinds["nompi"] == "job"
    assert kinds["o"] == kinds["k"] == kinds["y"] == kinds["V"] == "editing"
    assert kinds["h"] == kinds["version"] == "exit"
    p2y = {o["short"] or o["long"] for o in yambo_options("p2y", (5, 3))}
    assert {"I", "O", "U", "b"} <= p2y and "F" not in p2y and "walltime" not in p2y
