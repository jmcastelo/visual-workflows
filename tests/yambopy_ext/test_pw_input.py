"""The pw.x input reader, on what YamboPy's PwIn gets wrong among others."""

import pytest

from visual_workflows.yambopy_ext.qepy.pw_input import PwInputError, read_pw_input

NSCF = """ &control
    calculation = 'nscf', prefix='WSe2'   ! two on a line
    pseudo_dir = '../pseudos/'            ! a '/' in a string: not the end
    outdir="./out/", tstress=.true., max_seconds=4.32d4
 /
 &SYSTEM
    ibrav=4, celldm(1)=6.2, nat=3, ntyp=2, ecutwfc=60
    nbnd = 40, nspin=2, starting_magnetization(1)=0.5
    starting_ns_eigenvalue(1, 2, 1) = 0.0
    input_dft = "PBE0", lspinorb = F, noncolin=.false.
 /
 &electrons
 /
ATOMIC_SPECIES
 W 183.84 W.upf   # comment
 Se 78.97 Se.upf
K_POINTS {automatic}
 12 12 1 0 0 0
"""


def test_namelists():
    pw = read_pw_input(NSCF)
    assert pw.get("control", "pseudo_dir") == "../pseudos/"      # PwIn: '..
    assert pw.get("CONTROL", "outdir") == "./out/" and pw.get("control", "tstress") is True
    assert pw.get("control", "max_seconds") == 43200.0
    assert pw.get("&system", "nbnd") == 40 and pw.get("system", "celldm(1)") == 6.2
    assert pw.get("system", "starting_ns_eigenvalue(1,2,1)") == 0.0
    assert pw.get("system", "input_dft") == "PBE0" and pw.get("system", "lspinorb") is False
    assert pw.namelists["electrons"] == {}
    assert pw.get("system", "ecutrho") is None                    # nothing added (PwIn adds defaults)


def test_cards():
    pw = read_pw_input(NSCF)
    assert pw.cards["ATOMIC_SPECIES"].lines == ["W 183.84 W.upf", "Se 78.97 Se.upf"]
    k = pw.k_points()
    assert (k.kind, k.grid, k.shift, k.count, k.upper_bound) == ("automatic", (12, 12, 1), (0, 0, 0),
                                                                 None, 144)


@pytest.mark.parametrize("card, kind, count", [
    ("K_POINTS gamma", "gamma", 1),                               # PwIn: StopIteration
    ("K_POINTS (crystal)\n2\n0 0 0 1\n0.5 0 0 1", "list", 2),
    ("K_POINTS\n1\n0 0 0 1", "list", 1),                          # tpiba, the default
    ("K_POINTS crystal_b\n2\n0 0 0 10\n0.5 0 0 1", "list", None),  # a path
])
def test_k_points(card, kind, count):
    k = read_pw_input("&control\n/\n" + card + "\n").k_points()
    assert (k.kind, k.count) == (kind, count)


@pytest.mark.parametrize("text, message", [
    ("&control\n calculation='scf'\n", "not closed"),
    ("&control\n 'scf'\n/\n", "without a name"),
    ("&control\n = 3\n/\n", "without a name"),
    ("&control\n nbnd =\n/\n", "has no value"),
    ("&control\n/\nK_POINTS automatic\n4 4\n", "6 integers"),
    ("&control\n/\nK_POINTS crystal\nfour\n", "number of k-points"),
])
def test_errors(text, message):
    with pytest.raises(PwInputError, match=message):
        read_pw_input(text).k_points()


def test_end_after_the_slash():
    """Fortran ignores the rest of the line after '/': old inputs end with '/&end'."""
    pw = read_pw_input("&control\n calculation='scf'\n /&end\n&system\n nat=1\n /&end\n"
                       "K_POINTS gamma\n")
    assert pw.namelist_order == ["control", "system"] and pw.get("system", "nat") == 1
