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
