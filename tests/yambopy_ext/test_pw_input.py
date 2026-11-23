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


def test_text_before_the_first_namelist():
    # Fortran skips what precedes a namelist: QE's own inputs open with '#' banners
    pw = read_pw_input("####\n## Succinic acid\n####\n&CONTROL\n calculation='scf'\n/\n\n"
                       "&SYSTEM\n ibrav = 0, nat = 1\n/\nK_POINTS gamma\n")
    assert pw.namelist_order == ["control", "system"] and pw.get("system", "ibrav") == 0


# -- writing ----------------------------------------------------------------------------

from visual_workflows.yambopy_ext.qepy.pw_input import write_pw_input  # noqa: E402


def same(a, b):
    cards = lambda p: {k: (c.option, c.lines) for k, c in p.cards.items()}   # noqa: E731
    return a.namelists == b.namelists and cards(a) == cards(b)


def test_written_back_the_same():
    pw = read_pw_input(NSCF)
    text = write_pw_input(pw)
    assert same(read_pw_input(text), pw)
    assert "pseudo_dir  = '../pseudos/'" in text and "tstress     = .true." in text
    assert "starting_ns_eigenvalue(1,2,1) = 0.0" in text and "max_seconds = 43200.0" in text


def test_pw_x_order():
    pw = read_pw_input("&electrons\n conv_thr=1d-8\n/\n&system\n ibrav=2\n/\n&control\n/\n"
                       "K_POINTS gamma\nATOMIC_SPECIES\n Si 28.1 Si.upf\n")
    text = write_pw_input(pw)
    assert text.index("&control") < text.index("&system") < text.index("&electrons")
    assert text.index("ATOMIC_SPECIES") < text.index("K_POINTS gamma")


def test_editing():
    pw = read_pw_input(NSCF)
    pw.set("system", "nbnd", 80)
    pw.set("&ELECTRONS", "conv_thr", 1e-10)
    pw.set("ions", "ion_dynamics", "bfgs")              # a new namelist
    pw.set("system", "nspin", None)                     # None unsets
    pw.unset("system", "starting_magnetization(1)")
    pw.set_card("k_points", "automatic", ["6 6 1 0 0 0"])
    again = read_pw_input(write_pw_input(pw))
    assert again.get("system", "nbnd") == 80 and again.get("electrons", "conv_thr") == 1e-10
    assert again.get("ions", "ion_dynamics") == "bfgs" and again.get("system", "nspin") is None
    assert again.k_points().grid == (6, 6, 1)
    with pytest.raises(PwInputError, match="not a card"):
        pw.set_card("KPOINTS")


@pytest.mark.parametrize("value, written", [
    (True, ".true."), (3, "3"), (1e-10, "1e-10"), (0.5, "0.5"), ("it's", '"it\'s"'),
    ([1.0, 2.0], "1.0, 2.0"),
])
def test_values(value, written):
    pw = read_pw_input("&control\n/\n")
    pw.set("control", "x", value)
    assert f"x = {written}\n" in write_pw_input(pw)
    assert read_pw_input(write_pw_input(pw)).get("control", "x") == value


@pytest.mark.parametrize("value", [float("nan"), "a'b\"c", []])
def test_values_pw_x_cannot_read(value):
    pw = read_pw_input("&control\n/\n")
    pw.set("control", "x", value)
    with pytest.raises(PwInputError):
        write_pw_input(pw)


def test_misread_lines_not_written():
    pw = read_pw_input("&control\n/\nATOMIC_SPECIES\n ! Mo below\n Mo 95.95 Mo.upf\n")
    assert pw.cards["ATOMIC_SPECIES"].misread and "!" not in write_pw_input(pw)
