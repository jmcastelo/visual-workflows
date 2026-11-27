"""The QE chain made in one go: scf, nscf for Yambo, p2y, bands."""

import warnings

import pytest

from fakes import pw_input, upf
from visual_workflows.core.chain import check_chain
from visual_workflows.core.jobspec import JobSpec, Run
from visual_workflows.core.kpoints import Grid, band_path
from visual_workflows.core.project import Project
from visual_workflows.core.qe_chain import QeChain, add_qe_chain, chain_inputs
from visual_workflows.core.structure import Structure
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input

warnings.filterwarnings("ignore", category=DeprecationWarning)

JOB = JobSpec("cpu", 1, 4, 1, "4G", "01:00:00")


def si_chain(**kw):
    scf = pw_input(control="max_seconds = 3000")
    return QeChain(scf, JOB, nscf_grid=Grid((6, 6, 6), (1, 1, 1)), nscf_nbnd=40,
                   bands_path=band_path(Structure.from_pw_input(scf), "GXL"), bands_nbnd=12, **kw)


def test_inputs():
    inputs = chain_inputs(si_chain(), "01-scf")
    scf, nscf, bands = (read_pw_input(inputs[k]) for k in ("scf", "nscf", "bands"))
    assert scf.get("control", "outdir") == "./out" and scf.get("control", "calculation") == "scf"
    assert nscf.get("control", "outdir") == bands.get("control", "outdir") == "../01-scf/out"
    assert {p.get("control", "prefix") for p in (scf, nscf, bands)} == {"si"}
    assert nscf.get("system", "nbnd") == 40 and bands.get("system", "nbnd") == 12
    assert nscf.k_points().grid == (6, 6, 6) and nscf.k_points().shift == (0, 0, 0)   # unshifted
    assert nscf.get("system", "force_symmorphic") is True          # diamond: fractional translations
    assert scf.get("system", "force_symmorphic") is None
    assert nscf.get("electrons", "diago_full_acc") is True and scf.get("electrons", "diago_full_acc") is None
    assert bands.cards["K_POINTS"].option == "crystal_b"
    # Si at 0.26: no fractional translations pw.x takes, no force_symmorphic
    plain = pw_input().replace("Si 0.25 0.25 0.25", "Si 0.26 0.26 0.26")
    nscf = read_pw_input(chain_inputs(QeChain(plain, JOB, nscf_grid=Grid((4, 4, 4))), "01-scf")["nscf"])
    assert nscf.get("system", "force_symmorphic") is None


def test_steps_and_their_checks(tmp_path):
    p = Project.create(tmp_path / "P", cluster="Local")
    (p.path / "pseudos").mkdir()
    (p.path / "pseudos" / "Si.upf").write_text(upf())
    steps = add_qe_chain(p, si_chain())
    assert [s.id for s in steps] == ["01-scf", "02-nscf", "03-p2y", "04-bands"]
    assert [s.after for s in steps] == [[], ["01-scf"], ["02-nscf"], ["03-p2y"]]   # bands after p2y
    # The band structure's energies copied into its own folder, after pw.x
    assert steps[3].job.after == ["cp ../01-scf/out/si.save/data-file-schema.xml bands.xml"]
    assert steps[0].job.after == []
    p2y = steps[2].job.runs[0]
    assert (p2y.executable, p2y.args, p2y.mpi) == ("p2y", "-I ../01-scf/out/si.save -O .", False)
    assert steps[2].job.tasks == 1
    assert (steps[1].folder / "nscf.in").exists() and steps[1].job.runs[0].input == "nscf.in"
    assert check_chain(p) == {}                                    # consistent by construction


def test_without_p2y_or_bands(tmp_path):
    p = Project.create(tmp_path / "P", cluster="Local")
    steps = add_qe_chain(p, si_chain(p2y=False))
    assert [s.id for s in steps] == ["01-scf", "02-nscf", "03-bands"]
    assert steps[2].after == ["02-nscf"]
    q = Project.create(tmp_path / "Q", cluster="Local")
    scf = pw_input()
    only = add_qe_chain(q, QeChain(scf, JOB, bands_path=band_path(Structure.from_pw_input(scf))))
    assert [s.id for s in only] == ["01-scf", "02-bands"] and only[1].after == ["01-scf"]


def test_p2y_after_bands_is_an_error(tmp_path):
    """The order the chain avoids: bands between the nscf and p2y."""
    p = Project.create(tmp_path / "P", cluster="Local")
    steps = add_qe_chain(p, si_chain(p2y=False))
    p.add_job_step("p2y", JobSpec("cpu", 1, 1, 1, "4G", "01:00:00", runs=[
        Run("yambo", "p2y", args="-I ../01-scf/out/si.save -O .", mpi=False)]), after=[steps[2].id])
    text = "\n".join(x.message for ps in check_chain(p).values() for x in ps if x.error)
    assert "a bands run: its wave functions" in text


# -- continued to Yambo ---------------------------------------------------------------------

def _profile():
    from visual_workflows.core.profile import ClusterProfile, Partition, Program
    from visual_workflows.core.ssh import ConnectionSettings
    return ClusterProfile(
        name="T", connection=ConnectionSettings(host="h"),
        partitions={"cpu": Partition("1-00:00:00", 48, 94.0, threads_per_core=1)},
        programs={"qe": Program(["qe/7.5"], executables={"pw": "pw.x"}),
                  "yambo": Program(["yambo/5.3-single-omp"], executables={"p2y": "p2y", "yambo": "yambo"})})


def _project(tmp_path):
    p = Project.create(tmp_path / "P", cluster="T")
    (p.path / "pseudos").mkdir()
    (p.path / "pseudos" / "Si.upf").write_text(upf())
    return p


def yambo_chain(**kw):
    return QeChain(pw_input(control=""), JobSpec("cpu", 1, 8, 1, "16G", "01:00:00"),
                   nscf_grid=Grid((6, 6, 6)), nscf_nbnd=24, **kw)


def test_to_the_bse_on_gw_energies(tmp_path):
    from visual_workflows.core import jobspec
    from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input
    p = _project(tmp_path)
    steps = add_qe_chain(p, yambo_chain(gw=True, bse="gw"), profile=_profile())
    assert [s.id for s in steps] == ["01-scf", "02-nscf", "03-p2y", "04-gw", "05-bse"]
    p2y = steps[2].job.runs
    assert [(r.executable, r.input, r.mpi) for r in p2y] == [("p2y", "", False), ("yambo", "setup.in", False)]
    assert (steps[2].folder / "setup.in").read_text() == "setup\n"           # the SAVE indexed once
    gw, bse = steps[3], steps[4]
    assert gw.job.links == bse.job.links == {"SAVE": "../03-p2y/SAVE"}
    assert gw.job.runs[0].jobs == ["gw"] and bse.job.runs[0].jobs == ["bse"]   # no -J to the GW's folder
    assert gw.after == ["03-p2y"] and bse.after == ["04-gw"]
    inp = read_yambo_input((bse.folder / "bse.in").read_text())
    assert inp.get("KfnQPdb") == "E < ../04-gw/gw/ndb.QP" and inp.has_flag("WRbsWF")
    gw_in = read_yambo_input((gw.folder / "gw.in").read_text())
    assert gw_in.block("QPkrange").rows == [[1, 16, 3, 6]] and inp.block("BSEBands").rows == [[3, 6]]
    assert gw_in.get("X_and_IO_CPU") is not None                              # 8 tasks: roles set
    assert check_chain(p) == {}
    for step in steps:
        assert jobspec.check(step.job, _profile(), step.folder) == [], step.id


def test_bse_with_a_scissor_and_bands_last(tmp_path):
    from visual_workflows.core.kpoints import band_path
    from visual_workflows.core.structure import Structure
    from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input
    p = _project(tmp_path)
    scf = pw_input(control="")
    chain = yambo_chain(bse="scissor", scissor_ev=0.6, yambo_bands=(2, 7),
                        bands_path=band_path(Structure.from_pw_input(scf), "GXL"))
    steps = add_qe_chain(p, chain, profile=_profile())
    assert [s.id for s in steps] == ["01-scf", "02-nscf", "03-p2y", "04-bse", "05-bands"]
    assert steps[3].after == ["03-p2y"] and steps[4].after == ["03-p2y"]
    inp = read_yambo_input((steps[3].folder / "bse.in").read_text())
    assert inp.block("KfnQP_E").rows == [[0.6, 1.0, 1.0]] and inp.block("BSEBands").rows == [[2, 7]]
    assert check_chain(p) == {}


@pytest.mark.parametrize("kw, message", [
    (dict(gw=True, p2y=False), "need the nscf and p2y"),
    (dict(bse="gw"), "needs the GW step"),
    (dict(bse="scissor"), "needs the shift"),
])
def test_yambo_steps_refused(tmp_path, kw, message):
    with pytest.raises(ValueError, match=message):
        add_qe_chain(_project(tmp_path), yambo_chain(**kw))


def test_yambo_bands_not_known(tmp_path):
    p = Project.create(tmp_path / "P", cluster="T")                        # no pseudopotentials here
    with pytest.raises(ValueError, match="give the bands yourself"):
        add_qe_chain(p, yambo_chain(gw=True))
    assert len(add_qe_chain(p, yambo_chain(gw=True, yambo_bands=(3, 6)))) == 4


def test_nodes_to_use(tmp_path):
    """The chain's node list for every step; p2y, on one node, gets its first."""
    from dataclasses import replace
    p = Project.create(tmp_path / "P", cluster="Local")
    scf = pw_input(control="max_seconds = 3000")
    chain = QeChain(scf, replace(JOB, nodes=2, nodelist=["node[1-2]"]), nscf_grid=Grid((4, 4, 4)),
                    nscf_nbnd=40)
    steps = {s.id: s for s in add_qe_chain(p, chain)}
    assert steps["01-scf"].job.nodelist == ["node[1-2]"]
    assert (steps["03-p2y"].job.nodes, steps["03-p2y"].job.nodelist) == (1, ["node1"])
