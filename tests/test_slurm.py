"""Slurm parsing and commands, on outputs recorded on Lynx (Slurm 26.05)."""

import json
from datetime import datetime, timezone

import pytest

from fakes import FakeTransport, recorded
from visual_workflows.core.scheduler import JobState, SchedulerError, Slurm, access
from visual_workflows.core.scheduler.slurm import (end_reason, expand_hostlist,
                                                   parse_associations, parse_duration,
                                                   parse_job_text, parse_jobs_json,
                                                   parse_jobs_text, parse_key_values,
                                                   parse_nodes, parse_partitions, parse_qos,
                                                   parse_sacct)


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
    # Recorded before %z was asked for: the layout is unknown
    assert (c1.sockets, c1.cores_per_socket, c1.threads_per_core, c1.cores) == (0, 0, 0, 0)


def test_nodes_with_layout():
    nodes = {n.name: n for n in parse_nodes(recorded("tirant", "sinfo-nodes.txt"))}
    assert len(nodes) == 290
    s = nodes["s01r1b01"]
    assert (s.sockets, s.cores_per_socket, s.threads_per_core, s.cores) == (2, 24, 1, 48)
    assert s.cpus == 48 and s.memory_mb == 96310 and s.state == "mix-"
    assert nodes["s01r1b22"].free_memory_mb is None       # N/A on a node that is down
    lynx_like = parse_nodes("compute01|total*|96|182457|(null)|mix|32/64/0/96|32.03|126425|2:24:2\n")
    assert lynx_like[0].cores == 48 and lynx_like[0].threads_per_core == 2


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


# -- accounting (sacct, sacctmgr) -------------------------------------------------------

def synthetic(name):
    return recorded("synthetic", name)


def test_sacct():
    jobs = parse_sacct(synthetic("sacct.txt"))
    assert sorted(jobs) == ["393491", "393492", "393500", "393501", "393502"]
    failed = jobs["393491"]
    assert (failed.state, failed.exit_code, failed.signal) == (JobState.FAILED, 3, None)
    assert failed.elapsed_s == 61 and failed.max_rss_mb == 2
    assert failed.end_time == datetime(2026, 11, 20, 13, 44, 1)
    assert jobs["393492"].state is JobState.TIMEOUT
    pw = jobs["393500"]
    assert pw.state is JobState.COMPLETED and pw.exit_code == 0
    assert pw.max_rss_mb == 25 * 1024          # the largest step, not the batch one
    assert pw.time_limit_min == 720 and pw.num_cpus == 48
    cancelled = jobs["393501"]
    assert cancelled.state is JobState.CANCELLED and cancelled.scheduler_state == "CANCELLED"
    pending = jobs["393502"]
    assert pending.state is JobState.PENDING and pending.nodes == ""
    assert pending.start_time is None and pending.exit_code is None
    assert pending.time_limit_min == 1440


def test_associations_and_qos():
    assoc = parse_associations(synthetic("sacctmgr-assoc.txt"))
    assert [(a.account, a.partition) for a in assoc] == [("physics", ""), ("physics", "gpu")]
    assert assoc[0].qos == ["normal", "long"] and assoc[0].default_qos == "normal"
    assert assoc[0].max_wall_min == 3 * 1440 and assoc[0].max_jobs is None
    assert assoc[0].grp_tres == "cpu=512"
    assert (assoc[1].max_jobs, assoc[1].max_submit, assoc[1].max_tres) == (4, 10, "gres/gpu=4")
    qos = {q.name: q for q in parse_qos(synthetic("sacctmgr-qos.txt"))}
    assert qos["long"].max_wall_min == 7 * 1440 and qos["long"].priority == 10
    assert qos["normal"].max_jobs_per_user == 50 and qos["normal"].max_tres_per_user == "cpu=1024"
    assert qos["gpu"].max_submit_per_user is None


async def test_accounting_used_where_available():
    s, t = slurm(**{"sacct -n -X": (0, ""),
                    "sacct -nP -j": (0, synthetic("sacct.txt")),
                    "sacctmgr -nP show assoc": (0, synthetic("sacctmgr-assoc.txt")),
                    "sacctmgr -nP show qos": (0, synthetic("sacctmgr-qos.txt"))})
    assert await s.accounting_available()
    history = await s.history(["393491", "393500"])
    assert history["393500"].max_rss_mb == 25 * 1024
    assert "-j 393491,393500" in t.commands[-1]
    assert [a.account for a in await s.associations()] == ["physics", "physics"]
    assert len(await s.qos()) == 3
    with pytest.raises(ValueError):
        await s.history(["393491;id"])


async def test_no_accounting_on_lynx():
    s, t = slurm(**{"sacct": (1, "", lynx("sacct-disabled.txt")),
                    "sacctmgr": (1, "", lynx("sacctmgr-unsupported.txt"))})
    assert not await s.accounting_available()
    assert "disabled" in s.accounting_note
    assert await s.history(["393491"]) == {}
    assert await s.associations() == [] and await s.qos() == []
    # Found out once, not asked again
    assert sum(c.startswith("sacct") for c in t.commands) == 1


async def test_sacctmgr_refused_to_users():
    s, _ = slurm(**{"sacct -n -X": (0, ""),
                    "sacctmgr": (1, "", "sacctmgr: error: Access/permission denied\n")})
    assert await s.accounting_available()
    assert await s.associations() == []


# -- Tirant: Slurm 24.11 with accounting, QOS ------------------------------------------------


def tirant(name):
    return recorded("tirant", name)


TIRANT_IDS = dict(line.split("=") for line in tirant("job-ids.txt").split())


def test_tirant_partitions_accept_qos():
    parts = {p.name: p for p in parse_partitions(tirant("scontrol-partitions.txt"))}
    main, inter = parts["main"], parts["interactive"]
    assert main.default and len(main.nodes) == 288 and main.total_cpus == 13824
    assert main.allowed_groups is None and main.allowed_accounts is None
    assert main.allowed_qos == ["class_a", "class_b", "class_c", "class_t", "class_u", "class_v"]
    assert inter.allowed_qos == ["interactive"]
    assert main.default_mem_per_cpu_mb == 1916 and main.max_mem_per_node_mb == 92000
    assert main.max_time_min is None


def test_tirant_access_through_qos():
    parts = {p.name: p for p in parse_partitions(tirant("scontrol-partitions.txt"))}
    assoc = parse_associations(tirant("sacctmgr-assoc.txt"))
    qos = parse_qos(tirant("sacctmgr-qos.txt"))
    groups = tirant("id-groups.txt").split()
    main = access(parts["main"], groups, assoc, qos)
    assert (main.allowed, main.accounts, main.qos, main.default_qos) == \
        (True, ["lv00"], ["class_t"], "class_t")
    assert main.max_time_min == 3 * 1440           # class_t's limit, not "unlimited"
    inter = access(parts["interactive"], groups, assoc, qos)
    assert (inter.qos, inter.default_qos, inter.max_time_min) == (["interactive"], "interactive", 60)
    # Without the interactive QOS, that partition is not for us
    only_t = [a.__class__(**{**a.__dict__, "qos": ["class_t"]}) for a in assoc]
    assert not access(parts["interactive"], groups, only_t, qos).allowed
    # No accounting (Lynx): groups only
    lynx_part = parse_partitions(lynx("scontrol-partitions.txt"))[0]
    assert access(lynx_part, ["magma"], [], []).allowed



def test_discovered_partitions():
    from visual_workflows.core.profile import discovered_partition
    parts = {p.name: p for p in parse_partitions(tirant("scontrol-partitions.txt"))}
    assoc = parse_associations(tirant("sacctmgr-assoc.txt"))
    qos = parse_qos(tirant("sacctmgr-qos.txt"))
    nodes = parse_nodes(tirant("sinfo-nodes.txt"))
    main = discovered_partition(parts["main"], access(parts["main"], ["lv00"], assoc, qos), nodes)
    assert (main.max_walltime, main.cores_per_node, main.threads_per_core) == ("3-00:00:00", 48, 1)
    assert main.physical_cores_per_node == 48
    assert main.memory_per_node_gb == main.min_memory_per_node_gb == 94.0
    assert main.memory_limit_per_node_gb == 89.8          # MaxMemPerNode=92000, below the nodes'
    assert main.usable_memory_per_node_gb == 89.8
    # Lynx: nodes of different memory, no limit, layout unknown in the old recording
    lparts = {p.name: p for p in parse_partitions(lynx("scontrol-partitions.txt"))}
    total = discovered_partition(lparts["total"], access(lparts["total"], ["magma"], [], []),
                                 parse_nodes(lynx("sinfo-nodes.txt")))
    assert (total.memory_per_node_gb, total.min_memory_per_node_gb) == (178.1, 119.2)
    assert total.memory_limit_per_node_gb == 0 and total.threads_per_core == 0
    assert total.usable_memory_per_node_gb == 119.2 and total.physical_cores_per_node == 96
    magma = discovered_partition(lparts["magma"], access(lparts["magma"], ["magma"], [], []),
                                 parse_nodes(lynx("sinfo-nodes.txt")))
    assert magma.nodes[:2] == ["compute01", "compute02"] and len(set(magma.nodes)) == len(magma.nodes)
    assoc = parse_associations(tirant("sacctmgr-assoc.txt"))
    assert [(a.account, a.qos, a.default_qos) for a in assoc] == \
        [("lv00", ["class_t", "interactive"], "class_t")]
    qos = {q.name: q for q in parse_qos(tirant("sacctmgr-qos.txt"))}
    assert (qos["class_t"].max_wall_min, qos["class_t"].max_tres) == (4320, "cpu=4608")
    assert qos["normal"].max_wall_min is None


def test_tirant_json_v0_0_42():
    pending = parse_jobs_json(tirant("squeue-pending.json"))
    assert {j.reason for j in pending.values()} == {"Priority", "JobHeldUser"}
    assert all(j.state is JobState.PENDING and j.start_time is None for j in pending.values())
    later = parse_jobs_json(tirant("squeue-cancelled.json"))
    cancelled = later[TIRANT_IDS["cancelled"]]
    # Cancelled before it ran: no result (Slurm says exit code 0)
    assert (cancelled.state, cancelled.exit_code, cancelled.signal) == (JobState.CANCELLED, None, None)
    assert later[TIRANT_IDS["failed"]].state is JobState.PENDING
    text = parse_jobs_text(tirant("squeue-cancelled.txt"))
    assert {k: v.state for k, v in text.items()} == {k: v.state for k, v in later.items()}


def test_tirant_sacct_and_scontrol():
    jobs = parse_sacct(tirant("sacct-pending-cancelled.txt"))
    held = jobs[TIRANT_IDS["held"]]
    assert (held.state, held.scheduler_state, held.exit_code, held.nodes) == \
        (JobState.CANCELLED, "CANCELLED", None, "")
    assert jobs[TIRANT_IDS["failed"]].state is JobState.PENDING
    assert jobs[TIRANT_IDS["failed"]].workdir.startswith("/storage/home/lv00/alice/")
    text = [parse_job_text(line) for line in tirant("scontrol-jobs-cancelled.txt").splitlines()]
    assert {j.id: j.state for j in text if j} == {k: v.state for k, v in jobs.items()}


def test_jobs_cancelled_before_running_have_no_start():
    """Tirant: squeue --json gives them their end time as start time, and no nodes."""
    jobs = parse_jobs_json(tirant("squeue-cancelled.json"))
    for job_id in (TIRANT_IDS["held"], TIRANT_IDS["cancelled"]):
        job = jobs[job_id]
        assert job.state is JobState.CANCELLED and job.nodes == ""
        assert job.start_time is None and job.end_time is not None
