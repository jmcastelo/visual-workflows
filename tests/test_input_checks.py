"""Checks of pw.x and yambo inputs and command lines (core/input_checks.py),
against what the programs' sources do (docs/quantum-espresso.md §3,
docs/yambo.md §2 and §4)."""

import pytest

from fakes import pw_input
from visual_workflows.core.input_checks import (check_pw_input, check_yambo_args,
                                                check_yambo_input)
from visual_workflows.core.parallel import qe_version, yambo_version
from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input


def errors(problems):
    return "\n".join(p.message for p in problems if p.error)


def warnings(problems):
    return "\n".join(p.message for p in problems if not p.error)


def pw(text, version=(7, 5)):
    return check_pw_input(text, read_pw_input(text), version)


# -- pw.x -----------------------------------------------------------------------------

def test_a_good_input_passes():
    assert pw(pw_input()) == []
    assert pw(pw_input(calculation="nscf", system="nosym = .true.")) == []


@pytest.mark.parametrize("change, message", [
    (dict(control="ecutwfx = 30"), "ecutwfx is not a variable of &CONTROL in QE 7.5"),
    (dict(system="lda_plus_u = .true."), "HUBBARD card since QE 7.1"),
    (dict(control="lkpoint_dir = .false."), "lkpoint_dir is not a variable"),
    (dict(calculation="SCF"), "compares values with their case: 'scf'"),
    (dict(system="occupations = 'smearing'"), "smearing without degauss"),
    (dict(system="occupations = 'smearing', smearing = 'cool', degauss = 0.01"),
     "smearing = 'cool'"),
    (dict(control="max_seconds = -1"), "max_seconds is negative"),
    (dict(control="pseudo_dir = '$HOME/pp'"), "pw.x does not expand $ or ~"),
    (dict(control="outdir = '~/scratch'"), "pw.x does not expand $ or ~"),
    (dict(system="ecutrho = 25"), "not above ecutwfc"),
    (dict(system="nspin = 3"), "nspin = 3"),
    (dict(system="nspin = 2"), "needs tot_magnetization"),
    (dict(system="nspin = 2, occupations = 'smearing', degauss = 0.01"),
     "needs some starting_magnetization"),
    (dict(system="noncolin = .true., nspin = 2"), "conflicting flags"),
    (dict(system="lspinorb = .true."), "needs a noncollinear"),
    (dict(system="tot_magnetization = 1"), "tot_magnetization without nspin = 2"),
    (dict(system="A = 5.43"), "both celldm(1) and A"),
    (dict(kpoints="K_POINTS automatic\n4 4 4 2 0 0"), "shifts must be 0 or 1"),
    (dict(kpoints="K_POINTS crystal\n1\n0 0 0 1", system="occupations = 'tetrahedra'"),
     "tetrahedra need an automatic"),
])
def test_pw_errors(change, message):
    assert message in errors(pw(pw_input(**change)))


def test_version_dependent_names():
    # symmetry_with_labels arrived after 7.3.1; exx_type in 7.6; ppcg left in 7.5
    text = pw_input(control="symmetry_with_labels = .true.")
    assert "not a variable" in errors(pw(text, (7, 3))) and pw(text, (7, 5)) == []
    assert "exx_type" in errors(pw(pw_input(system="exx_type = 'bands'"), (7, 5)))
    assert pw(pw_input(system="exx_type = 'bands'"), (7, 6)) == []
    ppcg = pw_input().replace("&electrons\n", "&electrons\n diagonalization = 'ppcg'\n")
    assert pw(ppcg, (7, 3)) == [] and "diagonalization = 'ppcg'" in errors(pw(ppcg, (7, 5)))


def test_namelists():
    text = ("&control\n/\n&electrons\n/\n&system\n ibrav=2, celldm(1)=10, nat=1, ntyp=1, "
            "ecutwfc=20\n/\nATOMIC_SPECIES\nSi 28 Si.upf\nATOMIC_POSITIONS alat\nSi 0 0 0\n")
    assert "namelists out of order" in errors(pw(text))
    assert "no &IONS" in errors(pw(pw_input(calculation="relax")))
    relax = pw_input(calculation="vc-relax").replace("&electrons\n/\n", "&electrons\n/\n&ions\n/\n")
    assert "no &CELL" in errors(pw(relax)) and "no &IONS" not in errors(pw(relax))
    nscf = pw_input(calculation="nscf").replace("&electrons\n/\n", "&electrons\n/\n&ions\n/\n")
    assert "&IONS is not read in a nscf run" in warnings(pw(nscf))
    assert "XML input" in errors(pw("<?xml version='1.0'?>\n<qes/>\n"))


def test_lattice():
    assert "ibrav = 0 without CELL_PARAMETERS" in errors(pw(pw_input().replace("ibrav = 2", "ibrav = 0")))
    assert "redundant" in errors(pw(pw_input() + "CELL_PARAMETERS alat\n1 0 0\n0 1 0\n0 0 1\n"))
    assert "without celldm(1) or A" in errors(pw(pw_input().replace("celldm(1) = 10.26, ", "")))
    assert "not a Bravais lattice" in errors(pw(pw_input().replace("ibrav = 2", "ibrav = 15")))
    cubic = pw_input().replace("ibrav = 2", "ibrav = 0") + "CELL_PARAMETERS bohr\n10 0 0\n0 10 0\n0 0 10\n"
    assert "given twice" in errors(pw(cubic))
    assert pw(cubic.replace("celldm(1) = 10.26, ", "")) == []


def test_cards():
    text = pw_input()
    assert "ATOMIC_POSITIONS has 1 atoms for nat = 2" in errors(pw(text.replace("  Si 0.25 0.25 0.25\n", "")))
    assert "species Ge is not in ATOMIC_SPECIES" in errors(pw(text.replace("Si 0.25", "Ge 0.25")))
    assert "does not have 4 or 7 columns" in errors(pw(text.replace("Si 0.25 0.25 0.25", "Si 0.25 0.25")))
    assert "ATOMIC_POSITIONS cartesian" in errors(pw(text.replace("ATOMIC_POSITIONS alat", "ATOMIC_POSITIONS cartesian")))
    assert "without units" in warnings(pw(text.replace("ATOMIC_POSITIONS alat", "ATOMIC_POSITIONS")))
    extra = text.replace("Si 28.086 Si.upf", "Si 28.086 Si.upf\n  Ge 72.6 Ge.upf")
    assert "the extra ones are ignored" in warnings(pw(extra))
    # A whole-line '!' comment inside a card is data for pw.x
    commented = text.replace("  Si 0.25 0.25 0.25", "  ! the second atom\n  Si 0.25 0.25 0.25")
    assert "reads it as data" in errors(pw(commented))
    assert "no K_POINTS card" in warnings(pw(text.replace("K_POINTS automatic\n4 4 4 0 0 0\n", "")))
    assert "silently takes tpiba" in warnings(pw(text.replace("K_POINTS automatic", "K_POINTS {cristal}")
                                                 .replace("4 4 4 0 0 0", "1\n0 0 0 1")))


def test_versions_from_module_names():
    assert qe_version(["qe/oneapi-2024.2.1/7.3.1-omp"]) == (7, 3)     # Tirant: not 2024.2
    assert qe_version(["qe/7.3.1b-intelmpi-omp"]) == (7, 3)
    assert yambo_version(["yambo/5.3-double-gcc-12.2"]) == (5, 3)
    assert qe_version(["intel/2023"]) is None


# -- yambo ----------------------------------------------------------------------------

GOOD_BSE = '''optics
bss
bse
dipoles
BSKmod= "SEX"
BSSmod= "d"
BSENGexx= 30 Ry
BSENGBlk= 2 Ry
KfnQPdb= "E < ../04-gw/outputs/gw/ndb.QP"
% BSEBands
  20 | 35 |
%
% BEnRange
  0.0 | 10.0 | eV
%
WRbsWF
'''


def yambo(text, version=(5, 3)):
    return check_yambo_input(read_yambo_input(text), version)


def test_a_good_yambo_input_passes():
    assert yambo(GOOD_BSE) == []
    assert yambo(GOOD_BSE, (5, 1)) == []


@pytest.mark.parametrize("old, new, message", [
    ('BSKmod= "SEX"', "BSKmod= SEX", "a string without quotes"),
    ('KfnQPdb= "E < ../04-gw/outputs/gw/ndb.QP"', "KfnQPdb= E < gw/ndb.QP", "without quotes"),
    ("BSENGexx= 30 Ry", "bsengexx= 30 Ry", "spelt BSENGexx"),
    ("BSENGexx= 30 Ry", "BSENGexx= 30 RY", "unit 'RY' is not one yambo knows"),
    ("  20 | 35 |", "  20 |", "1 of its 2 columns"),
    ("optics\nbss\n", "gw0\nppa\noptics\nbss\n", "yambo drops the BSE ones"),
    ("optics\nbss\nbse\n", "bse\n", "bse without optics or bss"),
    ('BSSmod= "d"', 'BSSmod= "q"', "none of yambo's solver letters"),
    ('BSSmod= "d"', 'BSSmod= ""', "runs no solver"),
    ('BSKmod= "SEX"', 'BSKmod= "SEXY"', "IP-like kernel"),
    ('BSKmod= "SEX"', 'BSKmod= "SEX"\nLkind= "tilde"', "tilde stops it"),
    ('KfnQPdb= "E < ../04-gw/outputs/gw/ndb.QP"', 'KfnQPdb= "E <gw/ndb.QP"', "'<' must stand apart"),
    ('KfnQPdb= "E < ../04-gw/outputs/gw/ndb.QP"', 'KfnQPdb= "< gw/ndb.QP"', "no action"),
    ("WRbsWF", 'WRbsWF\nCUTGeo= "Box z"', "in lowercase"),
    ("WRbsWF", 'WRbsWF\nCUTGeo= "box"', "no direction"),
    ("WRbsWF", 'WRbsWF\nCUTGeo= "cylinder z"', "without CUTRadius"),
])
def test_yambo_errors(old, new, message):
    assert old in GOOD_BSE
    assert message in errors(yambo(GOOD_BSE.replace(old, new)))


def test_yambo_warnings():
    text = warnings(yambo(GOOD_BSE.replace("WRbsWF", "WRbsWF\nBSENGexx= 40 Ry\nBSSmodd= \"d\"\n"
                                           "FFTGvecs= 2000\nGDamping= 0.1")))
    assert "BSENGexx is given more than once: yambo takes the last value" in text
    assert "BSSmodd is not read by Yambo 5.3" in text
    assert "FFTGvecs has no unit: yambo reads it as a number of G vectors" in text
    assert "GDamping has no unit: yambo reads it in Ha" in text
    assert "runs Haydock and diagonalisation" in warnings(yambo(GOOD_BSE.replace('"d"', '"haydock"')))
    # Hartree is read as Ha (substring), as yambo does
    assert yambo(GOOD_BSE.replace("WRbsWF", "WRbsWF\nGDamping= 0.1 Hartree")) == []


def test_yambo_versions():
    # 5.1 reads BSSmod letters with their case; Lkind exactly Lbar or full
    assert "only lowercase" in errors(yambo(GOOD_BSE.replace('"d"', '"D"'), (5, 1)))
    assert yambo(GOOD_BSE.replace('"d"', '"D"'), (5, 3)) == []
    assert "takes Lbar or full" in errors(yambo(GOOD_BSE + 'Lkind= "bar"\n', (5, 1)))
    assert yambo(GOOD_BSE + 'Lkind= "bar"\n', (5, 3)) == []
    # 5.4: the exchange part has its own parallel section
    gw = 'HF_and_locXC\ngw0\nppa\nSE_CPU= "1 2 4"\nSE_ROLEs= "q qp b"\n'
    assert "SX_CPU" in warnings(yambo(gw, (5, 4))) and yambo(gw, (5, 3)) == []
    assert "SX_CPU is not read by Yambo 5.3" in warnings(yambo('SX_CPU= "2"\n', (5, 3)))


def test_hash_in_a_string_is_explained():
    with pytest.raises(Exception, match="starts a comment for yambo"):
        read_yambo_input('KfnQPdb= "E < gw#1/ndb.QP"\n')


# -- command lines ----------------------------------------------------------------------

@pytest.mark.parametrize("executable, args, message", [
    ("yambo", "-o b -k sex -y d", "writes its input file and exits"),
    ("yambo", "-V par", "writes its input file and exits"),
    ("yambo", "-x", "writes its input file and exits"),
    ("yambo", "-help", "prints and exits"),
    ("yambo", "-unknown", "has no such option"),
    ("yambo", "extra", "not an option"),
    ("p2y", "-F p2y.in", "has no such option"),
    ("p2y", "-walltime 1h", "has no such option"),
    ("ypp", "-e s", "writes its input file and exits"),
])
def test_bad_arguments(executable, args, message):
    assert message in errors(check_yambo_args(executable, args, (5, 3)))


def test_good_arguments():
    assert check_yambo_args("yambo", "-nompi -C reports -I ../SAVE_dir -memory 4", (5, 3)) == []
    assert check_yambo_args("p2y", "-I ../02-nscf/si.save -O . -nompi", (5, 3)) == []
    assert check_yambo_args("yambo", "-wall 2h", (5, 3)) == []      # a unique prefix
    assert check_yambo_args("ypp", "-J diago", (5, 1)) == []


# -- ypp ----------------------------------------------------------------------------------

def ypp(text, version=(5, 3)):
    from visual_workflows.core.input_checks import check_yambo_input as check
    return [(p.error, p.message) for p in check(read_yambo_input(text), version, executable="ypp")]


def test_ypp_names():
    from visual_workflows.yambopy_ext.io.yambo_variables import canonical_name, ypp_names
    names = ypp_names((5, 3))
    assert {"excitons", "wavefunction", "amplitude", "plot", "bnds", "electrons"} <= set(names["runlevels"])
    assert {"States", "Degen_Step", "Format", "Direction", "BSQindex", "FFTGvecs"} <= set(names["variables"])
    assert "GfnQPdb" in names["variables"]                     # the QP controls ypp loads (G)
    assert canonical_name("states", (5, 3), "ypp") == "States"
    assert canonical_name("BSENGexx", (5, 3), "ypp") is None   # yambo's, not ypp's


def test_ypp_input_checked_with_its_own_names():
    good = 'excitons\nwavefunction\nStates= "1 - 4"\nFormat= "x"\nDirection= "123"\nFFTGvecs= 20 Ry\n'
    assert ypp(good) == []
    found = ypp(good + "BSENGexx= 10 Ry\nStatez= \"1 - 2\"\n")
    assert (False, "BSENGexx is not read by ypp 5.3: ignored without a message.") in found
    assert (False, "Statez is not read by ypp 5.3: ignored without a message.") in found
    assert any(e and "without quotes" in m for e, m in ypp('excitons\nFormat= x\n'))
    # yambo's runlevel rules are not ypp's (no "bse without optics" here)
    assert not any("optics" in m for _, m in ypp(good))


def test_ypp_runs_checked_at_submission(tmp_path):
    from visual_workflows.core import jobspec
    from visual_workflows.core.jobspec import JobSpec, Run
    from visual_workflows.core.profile import ClusterProfile, Partition, Program
    from visual_workflows.core.ssh import ConnectionSettings
    profile = ClusterProfile(name="T", connection=ConnectionSettings(host="h"),
                             partitions={"cpu": Partition("1-00:00:00", 8, 16.0, threads_per_core=1)},
                             programs={"yambo": Program(["yambo/5.3-single-omp"],
                                                        executables={"ypp": "ypp", "yambo": "yambo"})})
    (tmp_path / "wf.in").write_text('excitons\nwavefunction\nStatez= "1 - 4"\n')
    job = JobSpec("cpu", 1, 1, 1, "4G", "00:30:00", runs=[Run("yambo", "ypp", "wf.in", mpi=False)])
    found = [p.message for p in jobspec.check(job, profile, tmp_path)]
    assert any("Statez is not read by ypp 5.3" in m for m in found)


def _tirant_ypp():
    import re
    from pathlib import Path
    text = (Path(__file__).parent / "data" / "yambo" / "tirant-5.3-ypp-inputs.txt").read_text()
    return re.findall(r"=== (\w+): ypp ([^\n]*)\n(.*?)(?=\n=== |\Z)", text, re.S)


@pytest.mark.parametrize("name, options, block", _tirant_ypp(), ids=lambda x: x if " " not in x else "")
def test_as_ypp_5_3_writes_it(name, options, block):
    """What ypp 5.3 wrote on Tirant: every name in ypp's lists, the checks
    clean, and read back unchanged after writing."""
    from visual_workflows.core.yambo_refresh import written_names
    from visual_workflows.yambopy_ext.io.yambo_input import write_yambo_input
    from visual_workflows.yambopy_ext.io.yambo_variables import canonical_name
    if block.startswith("(no input written)"):
        assert "-e s" in options                              # sorting runs at once, with no input
        return
    assert [n for n in written_names(block) if canonical_name(n, (5, 3), "ypp") is None] == []
    assert ypp(block) == []
    inp = read_yambo_input(block)
    again = read_yambo_input(write_yambo_input(inp))
    assert (again.flags, again.variables, again.blocks) == (inp.flags, inp.variables, inp.blocks)
