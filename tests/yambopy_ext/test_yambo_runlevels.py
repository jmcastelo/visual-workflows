"""What yambo puts in its input for given runlevels: the rules extracted from
its source (INIT_activate.F, INIT.F, INIT_QP.F, INIT_load.F), evaluated."""

import pytest

from visual_workflows.yambopy_ext.io.yambo_runlevels import YAMBO_RUNLEVELS, evaluate, input_names

GW = ["HF_and_locXC", "ppa", "dyson", "gw0", "em1d", "el_el_corr"]
BSE = ["optics", "bse", "bss"]


def names(runlevels, values=None, version="5.3", basic=True, **kw):
    return [str(n) for n in input_names(runlevels, values, version, **kw)
            if not basic or not n.verbosity]


def test_versions():
    assert set(YAMBO_RUNLEVELS) == {"5.1.1", "5.2.4", "5.3.0", "5.4"}
    assert names(GW, version="5.2.1") == names(GW, version="5.2")        # the nearest at or below
    # 5.2 (Lynx's yambo/gpl/5.2.4): XTermKind verbose since; DegFix only from 5.3
    assert "XTermKind" in names(GW, version="5.1") and "XTermKind" not in names(GW, version="5.2")
    assert "DegFix" not in names(GW, version="5.2") and "DegFix" in names(GW, version="5.3")
    assert "SX_CPU" in names(GW, version="5.4", basic=False)
    assert "SX_CPU" not in names(GW, version="5.3", basic=False)


def test_gw_with_plasmon_pole():
    found = names(GW)
    for name in ("BndsRnXp", "NGsBlkXp", "PPAPntXp", "GbndRnge", "DysSolver", "QPkrange",
                 "EXXRLvcs", "VXCRLvcs", "dipoles"):
        assert name in found, name
    assert "X_and_IO_CPU" in names(GW, basic=False) and "X_and_IO_CPU" not in found   # -V par
    assert "BSEBands" not in found and "BndsRnXd" not in found
    verbose = names(GW, basic=False)
    assert "GDamping" in verbose and "QPerange" in verbose and "GDamping" not in found


def test_bse_solver_decides():
    diago = names(BSE, {"BSSmod": "d", "BSKmod": "SEX"})
    assert "WRbsWF" in diago and "BSHayTrs" not in diago
    assert {"BSEBands", "BSENGexx", "BSENGBlk", "BSEQptR"} <= set(diago)
    assert {"BS_CPU", "KfnQPdb"} <= set(names(BSE, {"BSSmod": "d"}, basic=False))
    haydock = names(BSE, {"BSSmod": "h", "BSKmod": "SEX"})
    assert "WRbsWF" not in haydock and "BSHayTrs" in haydock
    slepc = names(BSE, {"bssmod": "s"}, basic=False)                    # names: any case
    assert "BSSNEig" in slepc and "WRbsWF" in slepc
    hartree = names(BSE, {"BSSmod": "d", "BSKmod": "hartree"})
    assert "BSENGBlk" not in hartree                                    # no screened kernel


def test_build_and_spin():
    assert not any(n.endswith(("_CPU", "_ROLEs")) for n in names(GW, mpi=False, basic=False))
    assert "XfnQP_E" in names(GW, basic=False) and "XfnQP_up_E" not in names(GW, basic=False)
    spin = names(GW, spin_polarized=True, basic=False)
    assert "XfnQP_up_E" in spin and "XfnQP_dn_E" in spin and "XfnQP_E" not in spin


def test_runlevels_bring_others():
    found = names(["gw0", "ppa", "el_el_corr"])
    assert found[:3] == ["gw0", "ppa", "el_el_corr"]
    assert {"HF_and_locXC", "em1d", "dipoles"} <= set(found)
    assert "dipoles" in names(["optics", "chi"])


def test_descriptions_units_verbosity():
    by_name = {str(n): n for n in input_names(GW, version="5.3")}
    assert by_name["BndsRnXp"].text == "[Xp] Polarization function bands"
    assert by_name["NGsBlkXp"].unit == "G" and by_name["PPAPntXp"].unit == "E"
    assert by_name["GbndRnge"].text == "[GW] G[W] bands range" and by_name["GbndRnge"].verbosity == ""
    assert by_name["QPkrange"].text.startswith("[GW] QP")
    assert by_name["GDamping"].verbosity == "qp"


@pytest.mark.parametrize("expression", [
    "__import__('os')", "l_bse.real", "[1, 2]", "open('x')", "lambda: 1", "x + 1"])
def test_only_the_small_language(expression):
    with pytest.raises((ValueError, SyntaxError)):
        evaluate(expression, {})


def test_conditions():
    env = {"l_bse": True, "BSS_mode": '"d"', "WF_exx_fraction": None}
    assert evaluate('l_bse and has(BSS_mode, "d")', env)
    assert not evaluate('has(BSS_mode, "h")', env)
    assert not evaluate("WF_exx_fraction > 0.0", env)                  # unset: 0
    assert evaluate("not l_unknown", env)                               # unknown switches: off
    assert evaluate('runlevel("optics")', env, ["optics"])
    assert evaluate("any_(l_unknown, l_bse) and all_(l_bse, True)", env)


# What Tirant's yambo 5.3.0 (MPI+OpenMP+SLK+SLEPC+HDF5_MPI_IO build) wrote for a
# fresh SAVE: only the setup input, whatever runlevels were asked for
TIRANT_SETUP = ["setup", "DegFix"]
TIRANT_SETUP_ALL = ["setup", "StdoHash", "Nelectro", "ElecTemp", "BoseTemp", "OccTresh", "NLogCPUs",
                    "DBsIOoff", "DBsFRAGpm", "MEM_tresh", "DegFix", "MaxGvecs", "Gthresh", "K_grids",
                    "IkSigLim", "IkXLim", "NoDiagSC", "QptCoord"]


def test_setup_as_yambo_5_3_writes_it():
    assert names(["setup"]) == TIRANT_SETUP
    assert names(["setup"], basic=False) == TIRANT_SETUP_ALL           # yambo -V all
    # EvalMagn is activated but registered by no executable of 5.3: not written
    assert "EvalMagn" not in names(["setup"], basic=False)
    # QptCoord only while the SAVE has no indices yet
    assert "QptCoord" not in names(["setup"], basic=False, databases=["QINDX"])



# -- against what yambo 5.3 wrote on Tirant -----------------------------------------------------

def _written(block):
    """The names of an input yambo wrote: runlevels, flags (also commented
    out, as yambo writes those off), variables and blocks, in order; its
    runlevels and values."""
    import re
    names, runlevels, values, in_block = [], [], {}, False
    for line in block.splitlines():
        s = line.strip()
        if not s:
            continue
        if in_block:
            in_block = not s.startswith("%")
            continue
        if s.startswith("#") and not re.match(r"#\w", s):
            continue
        m = re.match(r"(#?)(%?)\s*(\w+)\s*(=\s*(.*?))?\s*(#.*)?$", s)
        if not m:
            continue
        in_block = bool(m.group(2))
        names.append(m.group(3))
        if (m.group(6) or "").startswith("# [R") and not m.group(1):
            runlevels.append(m.group(3))
        if m.group(5):
            values[m.group(3)] = m.group(5).split("#")[0].strip()
    return names, runlevels, values


def tirant_inputs():
    import re
    from pathlib import Path
    text = (Path(__file__).parent.parent / "data" / "yambo" / "tirant-5.3-inputs.txt").read_text()
    return re.findall(r"=== (\w+): yambo ([^\n]*)\n(.*?)(?=\n=== |\Z)", text, re.S)


@pytest.mark.parametrize("name, options, block", tirant_inputs(), ids=lambda x: x if " " not in x else "")
def test_as_yambo_5_3_writes_it(name, options, block):
    """GW (plasmon pole), BSE (static screening; diagonalisation, Haydock),
    static screening, optics: the names yambo wrote, in its order, basic and
    with -V all (the build: MPI and ScaLAPACK; the SAVE indexed)."""
    written, runlevels, values = _written(block)
    predicted = [str(n) for n in input_names(runlevels, values, "5.3", mpi=True, scalapack=True,
                                              databases=["QINDX"], new_input=True)
                 if "-V all" in options or not n.verbosity]
    assert predicted == written


def test_versions_as_text_or_numbers():
    # a version reaches these as yambo's module says it ("5.2.4") or as numbers ((5, 2))
    from visual_workflows.yambopy_ext.io.yambo_runlevels import input_names
    from visual_workflows.yambopy_ext.io.yambo_variables import canonical_name, yambo_names
    for version in ("5.2.4", "5.2", (5, 2), (5, 2, 4)):
        assert canonical_name("ngsblkxs", version) == "NGsBlkXs"
        assert yambo_names(version) == yambo_names("5.2")
        assert [n.text for n in input_names(["optics", "bse", "bss"], version=version)] == \
            [n.text for n in input_names(["optics", "bse", "bss"], version="5.2")]
