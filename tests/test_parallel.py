"""Parallelisation checks of pw.x and yambo runs, against the rules read in
QE 7.3.1/7.5 and Yambo 5.1.1/5.3.0 (docs/quantum-espresso.md §2, docs/yambo.md §3)."""

import pytest

from visual_workflows.core import parallel
from visual_workflows.core.jobspec import JobSpec, Run, check
from visual_workflows.core.profile import ClusterProfile, Partition, Program
from visual_workflows.core.ssh import ConnectionSettings
from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input


def errors(problems):
    return "\n".join(p.message for p in problems if p.error)


def warnings(problems):
    return "\n".join(p.message for p in problems if not p.error)


def pw(kpoints="K_POINTS automatic\n4 4 1 0 0 0", control="max_seconds=3000", system="nbnd=20"):
    return read_pw_input(f"&control\n{control}\n/\n&system\n{system}\n/\n{kpoints}\n")


# -- pw.x -----------------------------------------------------------------------------

def test_qe_option_spellings():
    options, ignored = parallel.parse_qe_args("-npools 4 -ndiag 16 -ntg 2 -nbgrp 1 -pd .true.")
    assert options == {"nk": 4, "nd": 16, "nt": 2, "nb": 1, "pd": True} and ignored == []


@pytest.mark.parametrize("args, message", [
    ("--nk 4", "not a pw.x option"),
    ("-nk=4", "not '='"),
    ("-nk four", "not a whole number"),
    ("-nk", "has no value"),
    ("-pd yes", "logical"),
    ("-nk 4 extra", "not a pw.x option"),
])
def test_options_pw_ignores(args, message):
    assert message in errors(parallel.check_pw(args, 8))


@pytest.mark.parametrize("args, tasks, message", [
    ("-ni 2", 8, "one image only"),
    ("-nk 3", 8, "does not divide the 8 MPI tasks"),
    ("-nk 16", 8, "between 1 and 8 pools"),
    ("-nk 2 -nb 3", 8, "does not divide the 4 MPI tasks of a pool"),
    ("-nk 2 -nt 3", 8, "does not divide the 4 MPI tasks of a band group"),
])
def test_qe_divisibility(args, tasks, message):
    assert message in errors(parallel.check_pw(args, tasks))


def test_qe_good_layouts_pass():
    for args, tasks in (("-nk 4", 48), ("-nk 2 -nb 2 -nt 2", 48), ("-nd 16", 64), ("", 48)):
        assert parallel.check_pw(args, tasks, pw(), job_seconds=3600) == [], args


def test_linear_algebra_grid():
    # QE: n = floor(sqrt(min(nd, tasks per band group))), an error above nbnd
    assert "4×4 linear-algebra grid for 3 bands" in errors(
        parallel.check_pw("-nd 16", 64, pw(system="nbnd=3")))
    problems = parallel.check_pw("-nd 20", 64, pw())
    assert not errors(problems) and "4×4" in warnings(problems)
    problems = parallel.check_pw("-nk 8 -nd 16", 64, pw(kpoints="K_POINTS automatic\n8 8 1 0 0 0"))
    assert "of at most the 8 MPI tasks of a band group, 2×2" in warnings(problems)


def test_pools_and_k_points():
    gamma = "K_POINTS gamma"
    assert "Gamma-only" in errors(parallel.check_pw("-nk 2", 8, pw(gamma)))
    # nspin=2 doubles the k-points: 2 pools are allowed at Gamma
    assert not errors(parallel.check_pw("-nk 2", 8, pw(gamma, system="nbnd=20, nspin=2")))
    problems = parallel.check_pw("-nk 8", 8, pw("K_POINTS crystal\n3\n0 0 0 1\n.5 0 0 1\n0 .5 0 1"))
    assert "more pools than the 3 k-points" in warnings(problems)
    assert "max_seconds is not checked" in warnings(problems)
    assert "do not split evenly" in warnings(
        parallel.check_pw("-nk 2", 8, pw("K_POINTS crystal\n3\n0 0 0 1\n.5 0 0 1\n0 .5 0 1")))
    # An automatic grid: an upper bound only
    assert "at most 16 k-points" in warnings(parallel.check_pw("-nk 32", 32, pw()))
    assert "Berry phase" in errors(parallel.check_pw("-nk 2", 8, pw(control="lberry=.true.")))
    hybrid = pw("K_POINTS crystal\n1\n0 0 0 1", control="tstress=.true., max_seconds=3000",
                system="nbnd=20, input_dft='pbe0'")
    assert "no stress with pools" in errors(parallel.check_pw("-nk 2", 8, hybrid))
    assert "pools without k-points" in errors(parallel.check_pw("-nk 2", 8, pw(
        "K_POINTS crystal\n1\n0 0 0 1", system="nbnd=20, input_dft='hse'")))


def test_max_seconds():
    assert "No max_seconds" in warnings(parallel.check_pw("", 8, pw(control="calculation='scf'"),
                                                          job_seconds=7200))
    assert "not below the job's time" in warnings(
        parallel.check_pw("", 8, pw(control="max_seconds=8000"), job_seconds=7200))
    assert parallel.check_pw("", 8, pw(control="calculation='scf'")) == []     # no time known


# -- yambo ----------------------------------------------------------------------------

def yambo(text):
    return read_yambo_input(text)


def test_yambo_good_settings_pass():
    inp = yambo('BS_CPU= "2.4.6"\nBS_ROLEs= "k.eh.t"\nX_and_IO_CPU= "1 1 2 4 6"\n'
                'X_and_IO_ROLEs= "q,g,k,c,v"\nK_Threads= 2\nBS_nCPU_LinAlg_DIAGO= 16\n')
    assert parallel.check_yambo(inp, 48, 2, (5, 3)) == []


def test_yambo_errors():
    problems = parallel.check_yambo(yambo('BS_CPU= "2 24"\nBS_ROLEs= "k c"\n'), 48, 1)
    assert "'c' is not a role of BS" in errors(problems)
    assert "one number per role" in errors(parallel.check_yambo(
        yambo('SE_CPU= "2 24"\nSE_ROLEs= "q qp b"\n'), 48, 1))
    assert "at most 999" in errors(parallel.check_yambo(
        yambo('SE_CPU= "1000"\nSE_ROLEs= "b"\n'), 1000, 1))
    assert "not whole numbers" in errors(parallel.check_yambo(
        yambo('SE_CPU= "two"\nSE_ROLEs= "b"\n'), 2, 1))
    # Self-energy bands and QP states from the input: more processes than elements
    inp = yambo('SE_CPU= "1 8 6"\nSE_ROLEs= "q qp b"\n% GbndRnge\n 1 | 4 |\n%\n'
                '% QPkrange\n 1| 2| 25| 26|\n%\n')
    text = errors(parallel.check_yambo(inp, 48, 1))
    assert "8 processes for role 'qp', which has only 4 elements" in text
    assert "6 processes for role 'b', which has only 4 elements" in text


def test_yambo_versions():
    inp = yambo('SE_CPU= "2 2 2 6"\nSE_ROLEs= "q g qp b"\n')
    assert "needs Yambo 5.3 (the modules give 5.1)" in errors(parallel.check_yambo(inp, 48, 1, (5, 1)))
    assert parallel.check_yambo(inp, 48, 1, (5, 3)) == []
    assert "5.3 or later" in warnings(parallel.check_yambo(inp, 48, 1))
    assert parallel.yambo_version(["yambo/intel-libs", "yambo/gpl/5.1.1-intelmpi"]) == (5, 1)
    assert parallel.yambo_version(["yambo/5.3-double-omp"]) == (5, 3)
    assert parallel.yambo_version(["qe/7.3.1"]) is None


def test_yambo_warnings():
    text = warnings(parallel.check_yambo(yambo(
        'BS_CPU= "2 2 2"\nBS_ROLEs= "k eh t"\nK_Threads= 4\nX_all_q_CPU= "1 2"\n'
        'DIP_nCPU_LinAlg_INV= 4\nX_and_IO_nCPU_LinAlg_INV= 12\nBS_nCPU_LinAlg_DIAGO= 2\n'), 48, 2))
    assert "multiplies to 8, not the 48 MPI tasks" in text
    assert "K_Threads = 4, but each MPI task has 2 CPUs" in text
    assert "Yambo 4 name" in text
    assert "DIP_nCPU_LinAlg_INV: yambo never reads it" in text
    assert "3×3 = 9 processes" in text and "below 4 the linear algebra is serial" in text
    # eh may have more processes than its elements; one task ignores the settings
    assert parallel.check_yambo(yambo('BS_CPU= "1 2 1"\nBS_ROLEs= "k eh t"\n'), 2, 1) == []
    assert parallel.check_yambo(yambo('BS_CPU= "7"\nBS_ROLEs= "c"\n'), 1, 1) == []


# -- from the job description -------------------------------------------------------

def profile():
    return ClusterProfile(
        name="Lynx", connection=ConnectionSettings(host="h"),
        partitions={"cpu": Partition("1-00:00:00", 48, 100, threads_per_core=1)},
        programs={"qe": Program(["qe/7.3"], executables={"pw": "pw.x"}),
                  "yambo": Program(["yambo/5.1.1"], executables={"yambo": "yambo"})})


def test_inputs_read_from_the_step_folder(tmp_path):
    (tmp_path / "pw.in").write_text("&control\nmax_seconds=3000\n/\nK_POINTS gamma\n")
    (tmp_path / "gw.in").write_text('SE_CPU= "2 2 12"\nSE_ROLEs= "g qp b"\n')
    spec = JobSpec("cpu", 1, 48, 1, "50G", "01:00:00",
                   runs=[Run("qe", "pw", "pw.in", args="-nk 2"), Run("yambo", "yambo", "gw.in"),
                         Run("yambo", "yambo", "missing.in")])
    problems = check(spec, profile(), tmp_path)
    text = errors(problems)
    assert "Run 1 (pw.in): -nk 2 with K_POINTS gamma" in text
    assert "Run 2 (gw.in): SE_ROLEs: 'g' needs Yambo 5.3 (the modules give 5.1)" in text
    assert "missing.in is not in the step's folder" in warnings(problems)
    assert [p.field for p in problems if p.error] == ["runs[1].args", "runs[2].input"]
    # Unreadable: the program would not read it either
    (tmp_path / "pw.in").write_text("&control\nnbnd = \n/\n")
    assert "cannot be read as the program would" in errors(check(spec, profile(), tmp_path))
    # Without the folder: the arguments only
    assert "Gamma" not in errors(check(spec, profile()))
