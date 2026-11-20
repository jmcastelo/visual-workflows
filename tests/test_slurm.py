"""Slurm parsing and commands, on outputs recorded on Lynx (Slurm 26.05)."""

import json
from datetime import datetime, timezone

import pytest

from fakes import FakeTransport, recorded
from visual_workflows.core.scheduler import JobState, SchedulerError, Slurm
from visual_workflows.core.scheduler.slurm import (end_reason, expand_hostlist, parse_duration,
                                                   parse_job_text, parse_jobs_json,
                                                   parse_jobs_text, parse_key_values,
                                                   parse_nodes, parse_partitions)


def lynx(name):
    return recorded("lynx", name)


IDS = dict(line.split("=") for line in lynx("job-ids.txt").split())   # failed=..., etc.


# -- small parsers ---------------------------------------------------------------

@pytest.mark.parametrize("text, seconds", [
    ("60", 3600), ("5:00", 300), ("0:19", 19), ("1:00:00", 3600), ("00:05:00", 300),
    ("2-12", 216000), ("2-12:30", 217800), ("3-00:00:00", 259200),
    ("UNLIMITED", None), ("infinite", None), ("NONE", None), ("N/A", None), ("", None),
])
def test_durations(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text, hosts", [
    ("compute[01-03]", ["compute01", "compute02", "compute03"]),
    ("compute[02,05]", ["compute02", "compute05"]),
    ("compute[03-04,06],novas04", ["compute03", "compute04", "compute06", "novas04"]),
    ("n[8-10]", ["n8", "n9", "n10"]),
    ("rack[1-2]-n[1-2]", ["rack1-n1", "rack1-n2", "rack2-n1", "rack2-n2"]),
    ("single", ["single"]),
    ("", []),
])
def test_hostlists(text, hosts):
    assert expand_hostlist(text) == hosts


def test_key_values_with_spaces_in_values():
    kv = parse_key_values("NodeName=c1 OS=Linux 7.2.2-arch1-1 #1 SMP PREEMPT_DYNAMIC Fri, "
                          "28 Aug 2026 RealMemory=182457 AllocNode:Sid=localhost:61688 CPUs/Task=1")
    assert kv["OS"] == "Linux 7.2.2-arch1-1 #1 SMP PREEMPT_DYNAMIC Fri, 28 Aug 2026"
    assert kv["RealMemory"] == "182457"
    assert kv["AllocNode:Sid"] == "localhost:61688" and kv["CPUs/Task"] == "1"


# -- partitions and nodes ----------------------------------------------------------

def test_partitions():
    parts = {p.name: p for p in parse_partitions(lynx("scontrol-partitions.txt"))}
    assert list(parts) == ["total", "arqus", "magma", "novas"]
    total = parts["total"]
    assert total.default and total.up and not parts["magma"].default
    assert total.nodes == [f"compute0{i}" for i in range(1, 7)]
    assert total.total_cpus == 576
    assert total.max_time_min is None and total.default_time_min is None
    # Lynx gives 5 GB to a job that does not ask for memory
    assert total.default_mem_per_node_mb == 5000
    assert total.max_mem_per_node_mb is None
    assert total.allowed_groups == ["magma", "magma-guest", "arqus"]
    assert parts["arqus"].nodes == ["novas04"]


def test_partition_access_by_group():
    parts = {p.name: p for p in parse_partitions(lynx("scontrol-partitions.txt"))}
    groups = lynx("id-groups.txt").split()
    assert all(p.allows(groups) for p in parts.values())
    assert not parts["arqus"].allows(["alice", "magma"])
    assert parts["novas"].allows(["magma"])


def test_nodes():
    nodes = {n.name: n for n in parse_nodes(lynx("sinfo-nodes.txt"))}
    assert sorted(nodes) == [f"compute0{i}" for i in range(1, 7)] + [f"novas0{i}" for i in range(1, 5)]
    c1 = nodes["compute01"]
    assert c1.partitions == ["total", "magma"]
    assert (c1.cpus, c1.cpus_allocated, c1.cpus_idle) == (96, 32, 64)
    assert c1.memory_mb == 182457 and c1.free_memory_mb == 126425
    assert c1.state == "mix" and c1.gres == "" and c1.cpu_load == pytest.approx(32.03)
    assert nodes["novas04"].partitions == ["arqus", "novas"]


# -- jobs ----------------------------------------------------------------------------

def test_active_jobs_json():
    jobs = parse_jobs_json(lynx("squeue-active.json"))
    held = jobs[IDS["held"]]
    assert held.state is JobState.PENDING and held.held
    assert held.reason == "JobHeldUser" and held.start_time is None
    running = jobs[IDS["failed"]]
    assert running.state is JobState.RUNNING and running.reason == ""
    assert running.name == "vw-failed" and running.partition == "magma"
    assert running.nodes == "compute01" and running.num_cpus == 1 and running.num_nodes == 1
    assert running.time_limit_min == 5
    assert running.start_time.tzinfo is timezone.utc
    assert running.exit_code is None and running.end_time is None
    assert running.stdout == f"/home/alice/cluster-survey-jobs-lynx/failed-{IDS['failed']}.out"
    assert running.workdir == "/home/alice/cluster-survey-jobs-lynx"


def test_ended_jobs_json():
    jobs = parse_jobs_json(lynx("squeue-ended.json"))
    failed, timeout = jobs[IDS["failed"]], jobs[IDS["timeout"]]
    held, cancelled = jobs[IDS["held"]], jobs[IDS["cancelled"]]
    assert (failed.state, failed.reason, failed.exit_code, failed.signal) == \
        (JobState.FAILED, "NonZeroExitCode", 3, None)
    assert (timeout.state, timeout.reason, timeout.exit_code, timeout.signal) == \
        (JobState.TIMEOUT, "TimeLimit", None, 9)
    assert (cancelled.state, cancelled.exit_code, cancelled.signal) == (JobState.CANCELLED, None, 15)
    assert held.state is JobState.CANCELLED and held.reason == "JobHeldUser"
    assert all(j.end_time for j in jobs.values())
    assert (failed.end_time - failed.start_time).total_seconds() == pytest.approx(61, abs=2)


def test_scontrol_json_one_document_per_job():
    text = lynx("scontrol-jobs-ended.jsons")
    decoder, pos, jobs = json.JSONDecoder(), 0, {}
    while pos < len(text.rstrip()):
        doc, end = decoder.raw_decode(text, pos)
        jobs.update(parse_jobs_json(json.dumps(doc)))
        pos = end + (len(text[end:]) - len(text[end:].lstrip()))
    assert {j: s.state for j, s in jobs.items()} == {
        IDS["failed"]: JobState.FAILED, IDS["timeout"]: JobState.TIMEOUT,
        IDS["held"]: JobState.CANCELLED, IDS["cancelled"]: JobState.CANCELLED}
    # Same answers as squeue's JSON
    squeue = parse_jobs_json(lynx("squeue-ended.json"))
    for j in jobs.values():
        assert (j.exit_code, j.signal, j.end_time) == \
            (squeue[j.id].exit_code, squeue[j.id].signal, squeue[j.id].end_time)


def test_jobs_text_matches_json():
    for name in ("squeue-active", "squeue-ended"):
        text = parse_jobs_text(lynx(name + ".txt"))
        js = parse_jobs_json(lynx(name + ".json"))
        assert text.keys() == js.keys()
        for j in text.values():
            k = js[j.id]
            assert (j.state, j.reason, j.partition, j.time_limit_min, j.num_cpus, j.workdir) == \
                (k.state, k.reason, k.partition, k.time_limit_min, k.num_cpus, k.workdir)


def test_scontrol_text():
    jobs = {j.id: j for line in lynx("scontrol-jobs-ended.txt").splitlines()
            if (j := parse_job_text(line))}
    failed, timeout = jobs[IDS["failed"]], jobs[IDS["timeout"]]
    assert (failed.state, failed.exit_code, failed.signal) == (JobState.FAILED, 3, None)
    assert (timeout.state, timeout.exit_code, timeout.signal) == (JobState.TIMEOUT, None, 9)
    assert jobs[IDS["cancelled"]].signal == 15
    assert failed.time_limit_min == 5 and failed.nodes == "compute01"
    assert failed.start_time == datetime(2026, 11, 20, 13, 43, 0)
    assert jobs[IDS["held"]].nodes == ""
    active = [parse_job_text(line) for line in lynx("scontrol-jobs-active.txt").splitlines()]
    assert {j.state for j in active} == {JobState.RUNNING, JobState.PENDING}
    assert all(j.exit_code is None for j in active)


def test_end_reasons_from_output_files():
    assert end_reason(lynx(f"job-output-timeout-{IDS['timeout']}.out")) is JobState.TIMEOUT
    assert end_reason(lynx(f"job-output-cancelled-{IDS['cancelled']}.out")) is JobState.CANCELLED
    assert end_reason(lynx(f"job-output-failed-{IDS['failed']}.out")) is None
    assert end_reason("slurmstepd: error: Detected 1 oom_kill event in StepId=12.0. "
                      "Some of the step tasks have been OOM Killed.") is JobState.OUT_OF_MEMORY
    assert end_reason("SLURM_OOM_KILL_STEP=0\n") is None
    assert end_reason("*** JOB 7 ON n1 CANCELLED AT 2026-01-01T00:00:00 DUE TO NODE FAILURE ***") \
        is JobState.FAILED


# -- commands ----------------------------------------------------------------------

def slurm(**answers):
    base = {"sinfo --version": (0, lynx("sinfo-version.txt"))}
    t = FakeTransport({**base, **answers})
    return Slurm(t), t


async def test_version_and_json():
    s, _ = slurm()
    assert await s.version() == "26.05.4"
    assert await s.use_json()
    old, _ = slurm(**{"sinfo --version": (0, "slurm 20.11.9\n")})
    assert not await old.use_json()


async def test_jobs_uses_json_with_ended_jobs():
    s, t = slurm(**{"squeue --json": (0, lynx("squeue-ended.json"))})
    jobs = await s.jobs()
    assert jobs[IDS["failed"]].exit_code == 3
    assert "--states=all" in t.commands[-1]


async def test_jobs_falls_back_to_text():
    s, t = slurm(**{"squeue --json": (0, "squeue: unrecognized option '--json'\n"),
                    "squeue -h": (0, lynx("squeue-active.txt"))})
    assert len(await s.jobs()) == 4
    assert not await s.use_json()        # remembered
    await s.jobs()
    assert not any("--json" in c for c in t.commands[-1:])


async def test_job_detail_and_unknown_job():
    one = json.dumps({"jobs": [j for j in json.loads(lynx("squeue-ended.json"))["jobs"]
                               if str(j["job_id"]) == IDS["timeout"]]})
    s, _ = slurm(**{f"scontrol show job --json {IDS['timeout']}": (0, one),
                    "scontrol show job --json 1": (1, "", "scontrol_print_job error: Invalid job id specified\n")})
    assert (await s.job(IDS["timeout"])).state is JobState.TIMEOUT
    assert await s.job("1") is None
    with pytest.raises(ValueError):
        await s.job("1; rm -rf ~")


async def test_submit():
    s, t = slurm(sbatch=(1, "", "never matched"),
                 **{"cd /home/alice/p && sbatch": (0, "393500;lynx\n")})
    job = await s.submit("job.sh", "/home/alice/p", after_ok=["393498", "393499"],
                         options=["--partition=magma"])
    assert job == "393500"
    assert t.commands[-1] == ("cd /home/alice/p && sbatch --parsable "
                              "--dependency=afterok:393498:393499 --partition=magma job.sh")


async def test_submit_error_is_reported():
    s, _ = slurm(**{"cd ": (1, "", "sbatch: error: Batch job submission failed: "
                                   "Invalid partition name specified\n")})
    with pytest.raises(SchedulerError, match="Invalid partition name"):
        await s.submit("job.sh", "/w")


async def test_cancel_and_groups_and_cluster_info():
    s, t = slurm(scancel=(0, ""), **{"id -Gn": (0, lynx("id-groups.txt")),
                                     "scontrol show partition": (0, lynx("scontrol-partitions.txt")),
                                     "sinfo -h -N": (0, lynx("sinfo-nodes.txt"))})
    await s.cancel([IDS["held"], IDS["cancelled"]])
    assert t.commands[-1] == f"scancel {IDS['held']} {IDS['cancelled']}"
    assert "magma" in await s.user_groups()
    assert len(await s.partitions()) == 4 and len(await s.nodes()) == 10
