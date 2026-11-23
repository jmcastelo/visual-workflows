"""The yambo input reader, on what YamboPy's YamboIn gets wrong among others."""

import pytest

from visual_workflows.yambopy_ext.io.yambo_input import YamboInputError, read_yambo_input

BSE = '''#
#  Version 5.3.0 Revision 23959 Hash (prev commit) 19c1241
optics                           # [R] Linear Response optical properties
bss                              # [R] BSE solver
bse                              # [R][BSE] Bethe Salpeter Equation.
#dipoles                         # commented out: off
BS_CPU= "2.1.1"                  # [PARALLEL] CPUs for each role
BS_ROLEs= "k.eh.t"
BS_nCPU_LinAlg_DIAGO= 4
K_Threads=0
BSENGexx=  30          Ry
BSSmod= "d"
KfnQPdb= "E < ./gw/ndb.QP"
DIP_CPU= ""
% BSEBands
   20 | 35 |                     # [BSK] Bands range
%
% BEnRange
  0.00000 | 10.00000 |         eV
%
% QPkrange
  1| 19| 25| 32|
  20| 20| 1| 40|
%
WRbsWF
'''


def test_flags_variables_blocks():
    y = read_yambo_input(BSE)
    assert y.flags == ["optics", "bss", "bse", "WRbsWF"]          # YamboIn: dipoles too
    assert y.has_flag("wrbswf") and not y.has_flag("dipoles")
    assert y.get("BS_CPU") == "2.1.1" and y.get("bs_roles") == "k.eh.t"   # YamboIn drops them
    assert y.get("KfnQPdb") == "E < ./gw/ndb.QP" and y.get("DIP_CPU") == ""
    assert y.get("BS_nCPU_LinAlg_DIAGO") == 4 and isinstance(y.get("K_Threads"), int)
    assert y.variable("BSENGexx").unit == "Ry" and y.get("BSENGexx") == 30
    assert y.block("BSEBands").rows == [[20, 35]]
    assert (y.block("benrange").rows, y.block("BEnRange").unit) == ([[0.0, 10.0]], "eV")
    assert y.block("QPkrange").rows == [[1, 19, 25, 32], [20, 20, 1, 40]]
    assert y.get("missing", 7) == 7 and y.block("missing") is None


def test_read_as_yambo_reads():
    """'#' is a comment even inside quotes; single quotes; the first block, the last variable."""
    y = read_yambo_input("BSKmod= \"SEX\"\nBSKmod= \"IP\"\n"
                         "% BSEBands\n 1 | 2 |\n%\n% bsebands\n 3 | 4 |\n%\n")
    assert y.get("BSKmod") == "IP" and y.block("BSEBands").rows == [[1, 2]]
    assert read_yambo_input("BSKmod= 'SEX'   # comment\n").get("bskmod") == "SEX"
    # The '#' cuts the string: yambo cannot read the value either
    with pytest.raises(YamboInputError, match="closing quote"):
        read_yambo_input("KfnQPdb= 'E < gw#1/ndb.QP'\n")


@pytest.mark.parametrize("text, message", [
    ("% BSEBands\n 1 | 2 |\n", "not closed"),
    ("%\n 1 | 2 |\n%\n", "without a name"),
    ('KfnQPdb= "E < gw\n', "closing quote"),
    ("BSENGexx=\n", "no value"),
    ("two words\n", "not a runlevel"),
])
def test_errors(text, message):
    with pytest.raises(YamboInputError, match=message):
        read_yambo_input(text)


def test_complex_values_and_units_stuck_to_numbers():
    """As yambo writes them in generated inputs."""
    y = read_yambo_input("XfnQP_Z= ( 1.000000 , 0.000000 )\nDrudeWXs= (0,0) eV\n"
                         "Field1_Tstart= 0.000000fs\n")
    assert y.get("XfnQP_Z") == complex(1, 0) and y.variable("DrudeWXs").unit == "eV"
    assert (y.get("Field1_Tstart"), y.variable("Field1_Tstart").unit) == (0.0, "fs")


# -- writing ----------------------------------------------------------------------------

import glob  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

from visual_workflows.yambopy_ext.io.yambo_input import YamboBlock, YamboVariable, write_yambo_input  # noqa: E402


def same(a, b):
    return a.flags == b.flags and a.variables == b.variables and a.blocks == b.blocks


def _yambo_inputs():
    """YamboPy's yambo inputs (where its checkout is) and what yambo 5.3 wrote on Tirant."""
    found = []
    try:
        import yambopy
        root = Path(yambopy.__file__).resolve().parent.parent
        for path in sorted(glob.glob(str(root / "**" / "*"), recursive=True)):
            p = Path(path)
            if p.is_file() and p.suffix in (".in", "") and p.stat().st_size < 200_000:
                text = p.read_text(errors="replace")
                if "&control" not in text.lower() and re.search(r"^\s*(%\s*\w+|\w+\s*=)", text, re.M):
                    found.append((p.name, text))
    except ImportError:
        pass
    data = Path(__file__).parent.parent / "data" / "yambo" / "tirant-5.3-inputs.txt"
    for name, block in re.findall(r"=== (\w+): [^\n]*\n(.*?)(?=\n=== |\Z)", data.read_text(), re.S):
        found.append((f"tirant-{name}", block))
    return found


def test_every_input_written_back_the_same():
    count = 0
    for name, text in _yambo_inputs():
        try:
            inp = read_yambo_input(text)
        except YamboInputError:
            continue                                 # not an input yambo would read
        if not (inp.flags or inp.variables or inp.blocks):
            continue
        assert same(read_yambo_input(write_yambo_input(inp)), inp), name
        count += 1
    assert count >= 7                                # Tirant's, at least


def test_layout_and_values():
    inp = read_yambo_input(
        "optics\nbse\nBSKmod= \"SEX\"\nBSENGexx= 30 Ry\nWRbsWF\nKfnQPdb= 'E < ../gw/ndb.QP'\n"
        "% BSEBands\n 3 | 6 |\n%\n% BEnRange\n 0.0 | 4.5 | eV\n%\nXfnQP_Z= ( 1.0 , 0.0 )\n"
        "Lkind= full\nNGsBlkXs= 1 RL\n")
    text = write_yambo_input(inp)
    assert "BSENGexx= 30 Ry\n" in text and "% BSEBands\n 3 | 6 |\n%\n" in text
    assert "% BEnRange\n 0.0 | 4.5 | eV\n%" in text and "XfnQP_Z= ( 1.0 , 0.0 )" in text
    assert "Lkind= full\n" in text                   # unquoted, as it was (yambo keeps the default)
    assert text.splitlines()[:3] == ["optics", "bse", "WRbsWF"]
    assert same(read_yambo_input(text), inp)


def test_order_and_descriptions():
    inp = read_yambo_input("BSENGexx= 30 Ry\nbse\n% BSEBands\n 3 | 6 |\n%\noptics\n")
    text = write_yambo_input(inp, order=["optics", "bse", "BSEBands", "BSENGexx"],
                             describe=lambda n: {"BSEBands": "[BSK] Bands range"}.get(n, ""))
    lines = text.splitlines()
    assert lines[0] == "optics" and lines[1] == "bse"
    assert lines[2].startswith("% BSEBands") and lines[2].endswith("# [BSK] Bands range")
    assert lines[-1] == "BSENGexx= 30 Ry"


def test_editing():
    inp = read_yambo_input("optics\nbsengexx= 30 Ry\n")
    inp.set("BSENGexx", 40)                          # the unit kept; yambo's spelling
    inp.set("BSKmod", "SEX")
    inp.set_block("bsebands", [[3, 6]])
    inp.set_flag("bse")
    inp.set_flag("WRbsWF")
    inp.set_flag("optics", False)
    again = read_yambo_input(write_yambo_input(inp))
    assert again.variables["BSENGexx"] == YamboVariable(40, "Ry", False)
    assert again.variables["BSKmod"] == YamboVariable("SEX", "", True)
    assert again.blocks["BSEBands"] == YamboBlock([[3, 6]], "")
    assert again.flags == ["bse", "WRbsWF"]
    inp.set("BSKmod", None)
    inp.unset("bsebands")
    assert inp.variable("BSKmod") is None and inp.block("BSEBands") is None


@pytest.mark.parametrize("value, quoted, message", [
    ("E < ndb.QP # old", True, "where yambo would cut it"),
    ("a'b\"c", True, "both kinds of quotes"),
    ("two words", False, "cannot be written unquoted"),
    (float("nan"), False, "not a number"),
    (True, False, "no logical values"),
])
def test_values_yambo_would_misread(value, quoted, message):
    inp = read_yambo_input("optics\n")
    inp.variables["X"] = YamboVariable(value, "", quoted)
    with pytest.raises(YamboInputError, match=message):
        write_yambo_input(inp)
