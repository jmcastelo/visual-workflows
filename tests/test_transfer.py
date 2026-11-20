"""Downloads (scan, choose, plan, resume) and following jobs and logs."""

import os

import pytest

from fakes import LocalSlurm
from visual_workflows.core.monitor import LogFollower, watch
from visual_workflows.core.profile import ClusterProfile
from visual_workflows.core.project import Project
from visual_workflows.core.runner import Runner
from visual_workflows.core.scheduler import JobState
from visual_workflows.core.ssh import ConnectionSettings
from visual_workflows.core.transfer import (PART_SUFFIX, RemoteFile, choose, fetch, human,
                                            parse_size, plan, scan)
from visual_workflows.core.transport import LocalTransport

MB = 1 << 20


def f(path, size=100):
    return RemoteFile(path, size, 1_700_000_000.0)


# A step folder after QE and Yambo (sizes in bytes)
FILES = [
    f("pw.scf.out"), f("slurm-7.out"), f(".vwf/job-7.status"),
    f("WSe2.save/data-file-schema.xml"), f("WSe2.save/charge-density.dat", 50 * MB),
    f("WSe2.save/wfc1.dat", 200 * MB), f("WSe2.wfc1", 300 * MB),
    f("SAVE/ns.db1", 5 * MB), f("SAVE/ns.wf", MB), f("SAVE/ns.wf_fragments_1_1", 400 * MB),
    f("bse/ndb.BS_diago_Q1", 80 * MB), f("bse/ndb.BS_PAR_Q1", 3 << 30),
    f("gw/ndb.QP", MB), f("gw/ndb.QP_140_146", MB), f("gw/ndb.em1s_fragment_1", 20 * MB),
]


def chosen(choices):
    return [c.file.path for c in choices if not c.reason]


def test_default_skips_wave_functions_and_big_files():
    choices = {c.file.path: c.reason for c in choose(FILES)}
    assert ".vwf/job-7.status" not in choices                # never offered
    for wf in ("WSe2.save/wfc1.dat", "WSe2.wfc1", "SAVE/ns.wf", "SAVE/ns.wf_fragments_1_1"):
        assert choices[wf] == "wave functions"
    assert choices["bse/ndb.BS_PAR_Q1"] == "over 1.0 GB"
    for kept in ("pw.scf.out", "SAVE/ns.db1", "bse/ndb.BS_diago_Q1", "gw/ndb.QP_140_146",
                 "WSe2.save/charge-density.dat"):
        assert choices[kept] == ""


def test_all_and_excitons_modes():
    assert len(chosen(choose(FILES, "all"))) == len(FILES) - 1
    assert chosen(choose(FILES, "excitons")) == [
        "SAVE/ns.db1", "SAVE/ns.wf", "SAVE/ns.wf_fragments_1_1",
        "bse/ndb.BS_diago_Q1", "gw/ndb.QP", "gw/ndb.QP_140_146"]
    # Inside a subfolder too
    nested = [f("run/SAVE/ns.db1"), f("run/SAVE/ns.wf_fragments_1_1")]
    assert chosen(choose(nested, "excitons")) == ["run/SAVE/ns.db1", "run/SAVE/ns.wf_fragments_1_1"]


def test_include_and_exclude():
    c = choose(FILES, include=["ndb.BS_PAR_Q*"], exclude=["*.out", "gw/*"])
    paths = chosen(c)
    assert "bse/ndb.BS_PAR_Q1" in paths                       # over 1 GB, but asked for
    assert not any(p.endswith(".out") or p.startswith("gw/") for p in paths)
    assert chosen(choose(FILES, "excitons", exclude=["ns.wf*"])) == [
        "SAVE/ns.db1", "bse/ndb.BS_diago_Q1", "gw/ndb.QP", "gw/ndb.QP_140_146"]
    with pytest.raises(ValueError):
        choose(FILES, "some")


def test_sizes():
    assert parse_size("1G") == 1 << 30 and parse_size("500M") == 500 * MB
    assert parse_size("2.5GB") == int(2.5 * (1 << 30)) and parse_size("1024") == 1024
    with pytest.raises(ValueError):
        parse_size("lots")
    assert human(512) == "512 B" and human(3 * MB) == "3.0 MB"


@pytest.fixture
async def remote(tmp_path):
    home = tmp_path / "home"
    step = home / "proj" / "01-scf"
    (step / "SAVE").mkdir(parents=True)
    (step / ".vwf").mkdir()
    (step / "scf.out").write_text("JOB DONE.\n")
    (step / "SAVE" / "ns.db1").write_bytes(os.urandom(3 * MB + 17))
    (step / "name with spaces.txt").write_text("x")
    (step / ".vwf" / "job-1.status").write_text("started 1 n\n")
    async with LocalTransport(home) as t:
        yield t, step


async def test_scan_plan_fetch_and_resume(remote, tmp_path):
    t, step = remote
    files = await scan(t, "proj/01-scf")
    assert [x.path for x in files] == [".vwf/job-1.status", "SAVE/ns.db1",
                                       "name with spaces.txt", "scf.out"]
    assert files[1].size == 3 * MB + 17
    local = tmp_path / "outputs"
    items = plan(choose(files), local)
    assert [i.file.path for i in items] == ["SAVE/ns.db1", "name with spaces.txt", "scf.out"]
    # An interrupted download of ns.db1
    big = items[0]
    part = big.local.with_name(big.local.name + PART_SUFFIX)
    part.parent.mkdir(parents=True)
    part.write_bytes((step / "SAVE" / "ns.db1").read_bytes()[:MB])
    items = plan(choose(files), local)
    assert items[0].offset == MB
    for item in items:
        await fetch(t, str(step), item)
    assert (local / "SAVE" / "ns.db1").read_bytes() == (step / "SAVE" / "ns.db1").read_bytes()
    assert not part.exists()
    assert abs((local / "scf.out").stat().st_mtime - (step / "scf.out").stat().st_mtime) < 1
    # Nothing new the second time
    assert all(i.have for i in plan(choose(await scan(t, "proj/01-scf")), local))
    (step / "scf.out").write_text("JOB DONE.\nmore\n")
    again = plan(choose(await scan(t, "proj/01-scf")), local)
    assert [i.file.path for i in again if not i.have] == ["scf.out"]


async def test_scan_missing_folder(remote):
    t, _ = remote
    with pytest.raises(Exception, match="Cannot list"):
        await scan(t, "proj/none")


async def test_log_follower(remote):
    t, step = remote
    log = step / "run.log"
    log.write_text("x" * 5000 + "END1\n")
    follow = LogFollower(t, "proj/01-scf/run.log", tail=10)
    assert await follow.read_new() == "xxxxxEND1\n"           # the last 10 bytes
    assert await follow.read_new() == ""
    with open(log, "a") as fh:
        fh.write("iteration 2\n")
    assert await follow.read_new() == "iteration 2\n"
    log.write_text("restarted\n")                               # rewritten: shorter
    assert await follow.read_new() == "restarted\n"
    assert await LogFollower(t, "proj/01-scf/none.log").read_new() == ""


async def test_watch_until_done(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "a.sh").write_text("#!/bin/bash\nsleep 0.3\n")
    (tmp_path / "b.sh").write_text("#!/bin/bash\nexit 2\n")
    p = Project.create(tmp_path / "proj", cluster="Local")
    p.add_step("a", tmp_path / "a.sh")
    p.add_step("b", tmp_path / "b.sh", after=["a"])
    profile = ClusterProfile(name="Local", connection=ConnectionSettings(host="localhost"))
    async with LocalTransport(home) as t:
        runner = Runner(p, profile, t, LocalSlurm(home))
        await runner.submit()
        seen = []
        await watch(runner, lambda steps: seen.append([(s.id, s.latest.state) for s in steps]),
                    interval=0.05, min_interval=0)
    assert seen[-1][-1] == ("02-b", JobState.FAILED)
    assert p.step("a").latest.state is JobState.COMPLETED


async def test_watch_never_polls_faster_than_the_minimum(monkeypatch):
    import visual_workflows.core.monitor as monitor
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
        raise StopAsyncIteration

    class Busy:
        project = type("P", (), {"steps": [type("S", (), {"active": True, "jobs": [1]})()]})()

        async def refresh(self):
            return []

    monkeypatch.setattr(monitor.asyncio, "sleep", fake_sleep)
    with pytest.raises(StopAsyncIteration):
        await watch(Busy(), lambda s: None, interval=1)
    assert sleeps == [30.0]
