"""Checks across a chain of steps (core/chain.py): scf → nscf → p2y → GW → BSE,
on a project laid out as Visual Workflows makes them."""

import pytest

from fakes import pw_input, upf
from visual_workflows.core.chain import Chain, check_chain
from visual_workflows.core.jobspec import JobSpec, Run
from visual_workflows.core.project import Project

GW = """HF_and_locXC
gw0
ppa
dyson
% GbndRnge
  1 | 40 |
%
% BndsRnXp
  1 | 40 |
%
% QPkrange
  1| 8| 3| 6|
%
"""

BSE = """optics
bss
bse
BSKmod= "SEX"
BSSmod= "d"
KfnQPdb= "E < ../04-gw/gw/ndb.QP"
% BSEBands
  3 | 6 |
%
WRbsWF
"""


def job(*runs, links=None):
    return JobSpec("cpu", 1, 4, 1, "4G", "01:00:00", links=links or {}, runs=list(runs))


def make(tmp_path, nscf=None, scf=None, gw=GW, bse=BSE, p2y_args="-I ../01-scf/out/si.save -O .",
         nscf_after=("scf",), bse_jobs=("diago", "../04-gw/gw"), gw_after=("p2y",)):
    files = tmp_path / "files"
    files.mkdir(parents=True, exist_ok=True)
    (files / "scf.in").write_text(scf or pw_input(control="outdir = './out', max_seconds = 300"))
    (files / "nscf.in").write_text(nscf or pw_input(
        calculation="nscf", control="outdir = '../01-scf/out', max_seconds = 300",
        system="force_symmorphic = .true.").replace("nbnd = 8", "nbnd = 40"))
    (files / "gw.in").write_text(gw)
    (files / "bse.in").write_text(bse)
    p = Project.create(tmp_path / "proj", cluster="Local")
    (p.path / "pseudos").mkdir()
    (p.path / "pseudos" / "Si.upf").write_text(upf())
    save = {"SAVE": "../03-p2y/SAVE"}
    p.add_job_step("scf", job(Run("qe", "pw", "scf.in")), [files / "scf.in"])
    p.add_job_step("nscf", job(Run("qe", "pw", "nscf.in")), [files / "nscf.in"], after=list(nscf_after))
    p.add_job_step("p2y", job(Run("yambo", "p2y", args=p2y_args, mpi=False)), after=["nscf"])
    p.add_job_step("gw", job(Run("yambo", "yambo", "gw.in", jobs=["gw"]), links=save),
                   [files / "gw.in"], after=list(gw_after))
    p.add_job_step("bse", job(Run("yambo", "yambo", "bse.in", jobs=list(bse_jobs)), links=save),
                   [files / "bse.in"], after=["gw"])
    return p


def messages(found, step=None, errors=True):
    return "\n".join(p.message for s, ps in found.items() if step in (None, s)
                     for p in ps if p.error == errors)


def test_a_good_chain(tmp_path):
    p = make(tmp_path)
    chain = Chain(p)
    writes = {r.step: r.writes for r in chain.runs}
    assert writes == {"01-scf": "01-scf/out/si.save", "02-nscf": "01-scf/out/si.save",
                      "03-p2y": "03-p2y/SAVE", "04-gw": "04-gw/gw", "05-bse": "05-bse/diago"}
    bse = chain.runs[-1]
    assert bse.reads == ["05-bse/diago", "03-p2y/SAVE", "04-gw/gw"]     # SAVE through the link
    assert check_chain(p) == {}


def test_scf_and_nscf(tmp_path):
    found = check_chain(make(tmp_path, nscf_after=()))
    assert "02-nscf does not come after 01-scf" in messages(found, "02-nscf", errors=False)
    found = check_chain(make(tmp_path / "a", scf=pw_input(control="outdir = './out', disk_io = 'none'")))
    assert "writes no charge density" in messages(found, "02-nscf")
    nscf = pw_input(calculation="nscf", control="outdir = '../01-scf/out'",
                    system="force_symmorphic = .true., nspin = 2, tot_magnetization = 0")
    assert "nspin = 2, but 01-scf" in messages(check_chain(make(tmp_path / "b", nscf=nscf)), "02-nscf")
    lost = pw_input(calculation="nscf", control="outdir = './elsewhere'")
    assert "no step makes" in messages(check_chain(make(tmp_path / "c", nscf=lost)), "02-nscf", errors=False)


def test_what_p2y_needs(tmp_path):
    base = dict(calculation="nscf", control="outdir = '../01-scf/out'")
    nowf = pw_input(calculation="nscf", control="outdir = '../01-scf/out', disk_io = 'nowf'",
                    system="force_symmorphic = .true.")
    assert "writes no wave functions: p2y stops" in messages(check_chain(make(tmp_path, nscf=nowf)), "03-p2y")
    # Diamond: pw.x uses 24 operations with fractional translations, p2y stops
    found = check_chain(make(tmp_path / "a", nscf=pw_input(**base)))
    assert ("will use 24 of its 48 symmetry operations with fractional translations, and p2y "
            "stops" in messages(found, "03-p2y"))
    # Si at 0.26: the inversion would need t = 0.26..., which pw.x does not take: nothing to say
    symmorphic = pw_input(**base).replace("Si 0.25 0.25 0.25", "Si 0.26 0.26 0.26")
    assert "force_symmorphic" not in messages(check_chain(make(tmp_path / "s", nscf=symmorphic)), None)
    assert "force_symmorphic" not in messages(check_chain(make(tmp_path / "s2", nscf=symmorphic)), None,
                                              errors=False)
    hubbard = pw_input(calculation="nscf", control="outdir = '../01-scf/out'",
                       system="force_symmorphic = .true.") + "HUBBARD {ortho-atomic}\nU Si-3p 1.0\n"
    assert "DFT+U" in messages(check_chain(make(tmp_path / "b", nscf=hubbard)), "03-p2y")
    # p2y reading the scf (no nscf in between)
    found = check_chain(make(tmp_path / "c", p2y_args="-I ../01-scf/out/si.save -O .",
                             nscf=pw_input(calculation="nscf", control="outdir = './nscf'")))
    assert "an scf: Yambo usually needs an nscf" in messages(found, "03-p2y", errors=False)


def test_ultrasoft_pseudopotentials_for_p2y(tmp_path):
    p = make(tmp_path)
    (p.path / "pseudos" / "Si.upf").write_text(upf(pseudo_type="US"))
    assert "not norm-conserving: p2y refuses" in messages(check_chain(p), "03-p2y")


def test_bands_against_the_nscf(tmp_path):
    gw = GW.replace("  1 | 40 |\n%\n% BndsRnXp", "  1 | 60 |\n%\n% BndsRnXp").replace(
        "% BndsRnXp\n  1 | 40 |", "% BndsRnXp\n  1 | 50 |")
    found = check_chain(make(tmp_path, gw=gw))
    assert "GbndRnge reaches band 60, but 02-nscf, run 1 (nscf.in) has nbnd = 40" in messages(found, "04-gw")
    assert "BndsRnXp reaches band 50" in messages(found, "04-gw", errors=False)


def test_screening_for_the_kernel(tmp_path):
    no_ppa = GW.replace("ppa\n", "")
    found = check_chain(make(tmp_path, gw=no_ppa.replace("gw0\n", "gw0\nem1d\n")))
    assert found == {}                                   # em1d in the GW step: screening there
    found = check_chain(make(tmp_path / "a", gw=no_ppa))
    assert "needs the screening" in messages(found, "05-bse")
    # A -J folder outside the project may have it: no message
    found = check_chain(make(tmp_path / "b", gw=no_ppa, bse_jobs=("diago", "../04-gw/gw", "/scratch/em1s")))
    assert "needs the screening" not in messages(found, "05-bse")


def test_qp_database(tmp_path):
    found = check_chain(make(tmp_path, bse=BSE.replace("../04-gw/gw/ndb.QP", "../04-gw/qp/ndb.QP")))
    assert "no step makes it: yambo only warns" in messages(found, "05-bse")
    found = check_chain(make(tmp_path / "a", bse=BSE.replace("  3 | 6 |", "  2 | 7 |")))
    assert "BSEBands 2–7, but 04-gw, run 1 (gw.in) computes QP corrections for bands 3–6" in \
        messages(found, "05-bse", errors=False)


def test_what_visual_excitons_needs():
    from visual_workflows.core.input_checks import check_for_visual_excitons
    from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input

    def warnings(text):
        return " ".join(p.message for p in check_for_visual_excitons(read_yambo_input(text)))
    assert warnings(BSE) == ""
    assert "Visual Excitons needs a diagonalising solver" in warnings(BSE.replace('"d"', '"h"'))
    assert "Visual Excitons needs them" in warnings(BSE.replace("WRbsWF\n", ""))
    assert warnings("optics\nbse\nbsk\n") == ""          # no solver run: not for it


def test_steps_sharing_a_save(tmp_path):
    # GW not after p2y but BSE after GW: GW and BSE are ordered, so no race between them;
    # GW is not after p2y, which is said
    found = check_chain(make(tmp_path, gw_after=()))
    assert "04-gw does not come after 03-p2y" in messages(found, "04-gw", errors=False)
    p = make(tmp_path / "a")
    p.step("bse").after = ["p2y"]                         # BSE and GW now unordered
    p.step("bse").save()
    found = check_chain(Project.load(p.path))
    assert "may both rebuild SAVE/ndb.kindx" in messages(found, "05-bse", errors=False)


async def test_chain_errors_stop_the_submission(tmp_path):
    from fakes import LocalSlurm
    from visual_workflows.core.profile import ClusterProfile, Partition, Program
    from visual_workflows.core.runner import Runner, SubmitRefused
    from visual_workflows.core.ssh import ConnectionSettings
    from visual_workflows.core.transport import LocalTransport
    nowf = pw_input(calculation="nscf", control="outdir = '../01-scf/out', disk_io = 'nowf'",
                    system="force_symmorphic = .true.")
    p = make(tmp_path, nscf=nowf)
    profile = ClusterProfile(
        name="Local", connection=ConnectionSettings(host="localhost"),
        partitions={"cpu": Partition("1-00:00:00", 8, 16, threads_per_core=1)},
        programs={"qe": Program([], executables={"pw": "pw.x"}),
                  "yambo": Program([], executables={"p2y": "p2y", "yambo": "yambo"})})
    home = tmp_path / "home"
    home.mkdir()
    async with LocalTransport(home) as t:
        slurm = LocalSlurm(home)
        with pytest.raises(SubmitRefused) as e:
            await Runner(p, profile, t, slurm).submit()
    assert "writes no wave functions: p2y stops" in "\n".join(e.value.problems["03-p2y"])
    assert slurm._jobs == {}


def nscf_with(kpoints="K_POINTS automatic\n4 4 4 0 0 0", system="force_symmorphic = .true."):
    return pw_input(kpoints=kpoints, calculation="nscf", control="outdir = '../01-scf/out'",
                    system=system).replace("nbnd = 8", "nbnd = 40")


def test_yambo_roles_against_the_nscf(tmp_path):
    """Roles with more processes than elements: k-points from the nscf, bands
    split at the occupied ones (Si: 8 electrons, 4 occupied bands); 4 tasks."""
    def found(bse_vars="", gw_vars="", nscf=None, where="x"):
        p = make(tmp_path / where, nscf=nscf or nscf_with(), bse=BSE + bse_vars, gw=GW + gw_vars)
        return check_chain(p)
    # BSEBands 3-6: 2 occupied (3, 4) and 2 empty (5, 6) bands for the dipoles
    text = messages(found('DIP_CPU= "1 4 1"\nDIP_ROLEs= "k c v"\n', where="a"), "05-bse")
    assert "DIP: 4 processes for role 'c', which has only 2 elements (empty bands 5–6)" in text
    assert "USER parallel structure does not fit" in text
    assert messages(found('DIP_CPU= "2 2 1"\nDIP_ROLEs= "k c v"\n', where="b"), "05-bse") == ""
    terminated = 'DIP_CPU= "1 4 1"\nDIP_ROLEs= "k c v"\nXTermKind= "BG"\n'
    assert "role 'c'" not in messages(found(terminated, where="t"), "05-bse")
    # BndsRnXp 1-40 in the GW: 4 occupied, 36 empty; 64 k-points in the whole zone
    assert messages(found(gw_vars='X_and_IO_CPU= "1 1 1 1 4"\nX_and_IO_ROLEs= "q g k c v"\n',
                          where="c"), "04-gw") == ""
    gw = GW.replace("% BndsRnXp\n  1 | 40 |", "% BndsRnXp\n  3 | 40 |")
    p = make(tmp_path / "d", nscf=nscf_with(), gw=gw + 'X_and_IO_CPU= "1 1 1 1 4"\nX_and_IO_ROLEs= "q g k c v"\n')
    assert "role 'v', which has only 2 elements (occupied bands 3–4)" in messages(check_chain(p), "04-gw")
    # 2×2×2: 3 irreducible k-points (and q-points)
    small = nscf_with("K_POINTS automatic\n2 2 2 0 0 0")
    text = messages(found('DIP_CPU= "4 1 1"\nDIP_ROLEs= "k c v"\n', nscf=small, where="e"), "05-bse")
    assert "role 'k', which has only 3 elements (the irreducible k-points of 02-nscf" in text
    text = messages(found(gw_vars='SE_CPU= "4 1 1"\nSE_ROLEs= "q qp b"\n', nscf=small, where="f"), "04-gw")
    assert "SE: 4 processes for role 'q', which has only 3 elements" in text


def test_yambo_roles_only_when_certain(tmp_path):
    many = 'DIP_CPU= "1 4 1"\nDIP_ROLEs= "k c v"\n'
    # a metal: occupied bands not known
    metal = nscf_with(system="force_symmorphic = .true., occupations = 'smearing', degauss = 0.01")
    p = make(tmp_path / "a", nscf=metal, bse=BSE + many)
    assert "role 'c'" not in messages(check_chain(p), "05-bse")
    # a shifted grid: no whole-zone count for the response function's k
    shifted = nscf_with("K_POINTS automatic\n2 2 2 1 1 1")
    p = make(tmp_path / "b", nscf=shifted, gw=GW + 'X_and_IO_CPU= "1 1 4 1 1"\nX_and_IO_ROLEs= "q g k c v"\n')
    assert "role 'k'" not in messages(check_chain(p), "04-gw")
    # one process: yambo ignores the roles
    p = make(tmp_path / "c", bse=BSE + many)
    bse = p.step("05-bse")
    bse.job.runs[0].mpi = False
    bse.save()
    assert "role 'c'" not in messages(check_chain(p), "05-bse")


def test_qp_kpoints_in_range(tmp_path):
    # 4×4×4: 8 irreducible k-points
    assert "QPkrange" not in messages(check_chain(make(tmp_path / "a", nscf=nscf_with())), "04-gw",
                                      errors=False)
    gw = GW.replace("  1| 8| 3| 6|", "  1| 12| 3| 6|\n  10| 12| 4| 5|")
    text = messages(check_chain(make(tmp_path / "b", nscf=nscf_with(), gw=gw)), "04-gw", errors=False)
    assert "k-points 1–12, but 02-nscf, run 1 (nscf.in) has 8: yambo computes 1–8 instead" in text
    assert "k-points 10–12" in text and "computes 1–8" in text.split("10–12")[1]


def test_steps_sharing_an_indexed_save(tmp_path):
    # The QE chain's p2y step indexes the SAVE (yambo's setup after p2y): steps after it may
    # share it at once, as a convergence study's copies do
    from test_results import chain
    from visual_workflows.core.studies import add_study
    p = chain(tmp_path, gw=True, bse="gw")
    add_study(p, "05-bse", "NGsBlkXs", ["2", "4"])
    found = check_chain(Project.load(p.path))
    assert not any("rebuild SAVE/ndb.kindx" in str(x) for v in found.values() for x in v)
