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
    found = check_chain(make(tmp_path / "a", nscf=pw_input(**base)))
    text = messages(found, "03-p2y", errors=False)
    assert "no force_symmorphic" in text
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
