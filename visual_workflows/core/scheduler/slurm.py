"""Slurm, through its command-line tools run over a Transport.

Job information comes as JSON (`squeue --json`, `scontrol show job --json`,
Slurm >= 21.08) with a text fallback for older versions or when the JSON
cannot be read. Partitions and nodes come from text formats that have been
stable for many Slurm versions (`scontrol show partition -o`, `sinfo -N -o`).

Recorded outputs from Lynx (Slurm 26.05) are in tests/data/lynx/.

Accounting (sacct, sacctmgr) is used where the cluster keeps it, and found
out at run time: Lynx has none. Without it, Slurm forgets a job MinJobAge
seconds after it ends (5 minutes by default), and how jobs that ended earlier
finished is read from the files the jobs leave behind (see end_reason() and
docs/design.md §5.1). sacct and sacctmgr are read in their `-nP` text form
(stable across Slurm versions); the parsers are tested on samples written from
Slurm's documentation until a cluster with accounting is recorded.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ..shell import quote
from ..transport import Transport
from .base import Association, Job, JobState, Node, Partition, Qos, Scheduler, SchedulerError

# Slurm states -> JobState. Anything else is UNKNOWN (scheduler_state keeps it).
_STATES = {
    "PENDING": JobState.PENDING, "REQUEUED": JobState.PENDING,
    "REQUEUE_FED": JobState.PENDING, "REQUEUE_HOLD": JobState.PENDING,
    "RESV_DEL_HOLD": JobState.PENDING,
    "RUNNING": JobState.RUNNING, "CONFIGURING": JobState.RUNNING,
    "COMPLETING": JobState.RUNNING, "SUSPENDED": JobState.RUNNING,
    "STAGE_OUT": JobState.RUNNING, "SIGNALING": JobState.RUNNING,
    "RESIZING": JobState.RUNNING,
    "COMPLETED": JobState.COMPLETED,
    "FAILED": JobState.FAILED, "NODE_FAIL": JobState.FAILED,
    "BOOT_FAIL": JobState.FAILED, "SPECIAL_EXIT": JobState.FAILED,
    "CANCELLED": JobState.CANCELLED, "PREEMPTED": JobState.CANCELLED,
    "REVOKED": JobState.CANCELLED,
    "TIMEOUT": JobState.TIMEOUT, "DEADLINE": JobState.TIMEOUT,
    "OUT_OF_MEMORY": JobState.OUT_OF_MEMORY,
}

# squeue text format (the one recorded on Lynx): id, name, partition, state,
# reason, elapsed, time limit, nodes, CPUs, submit, start, working folder
_SQUEUE_FIELDS = ("%i", "%j", "%P", "%T", "%r", "%M", "%l", "%D", "%C", "%V", "%S", "%Z")
_SQUEUE_FORMAT = "|".join(_SQUEUE_FIELDS)

# sinfo per-node text format; %z (sockets:cores:threads) last, so that
# recordings without it still read
_SINFO_FORMAT = "%N|%P|%c|%m|%G|%t|%C|%O|%e|%z"

# sacct fields: one line per job and one per job step (123.batch, 123.0 ...)
SACCT_FIELDS = ("JobID,JobName,Partition,State,ExitCode,Elapsed,Timelimit,NCPUS,NNodes,"
                "NodeList,Submit,Start,End,WorkDir,MaxRSS")
ASSOC_FIELDS = "Account,Partition,QOS,DefaultQOS,MaxWall,MaxJobs,MaxSubmit,GrpTRES,MaxTRES"
QOS_FIELDS = "Name,Priority,MaxWall,MaxTRESPU,MaxJobsPU,MaxSubmitPU,MaxTRES"

_JOB_ID = re.compile(r"^\d+(_\d+)?$")


def _state(words) -> tuple[JobState, str]:
    """JobState and Slurm's word, from a state string or JSON list of words."""
    if isinstance(words, str):
        words = [words]
    for w in words or ():
        base = w.split()[0].rstrip("+").upper()   # "CANCELLED by 1004", "COMPLETING+"
        if base in _STATES:
            return _STATES[base], base
    return JobState.UNKNOWN, " ".join(words or ())


# -- durations and times ------------------------------------------------------

def parse_duration(text: str) -> int | None:
    """Seconds from a Slurm time: "60" (minutes), "5:00", "1:00:00", "2-12",
    "2-12:30", "3-00:00:00". None for unlimited or unknown."""
    text = text.strip()
    if not text or text.upper() in ("UNLIMITED", "INFINITE", "NONE", "N/A", "INVALID", "NOT_SET"):
        return None
    days = 0
    if "-" in text:
        d, _, text = text.partition("-")
        days = int(d)
        parts = [int(p) for p in text.split(":")]
        parts += [0] * (3 - len(parts))           # D-HH, D-HH:MM, D-HH:MM:SS
        h, m, s = parts
    else:
        parts = [int(p) for p in text.split(":")]
        if len(parts) == 1:
            h, m, s = 0, parts[0], 0              # minutes
        elif len(parts) == 2:
            h, (m, s) = 0, parts                  # MM:SS
        else:
            h, m, s = parts
    return ((days * 24 + h) * 60 + m) * 60 + s


def _minutes(text: str) -> int | None:
    seconds = parse_duration(text)
    return None if seconds is None else seconds // 60


def _text_time(text: str) -> datetime | None:
    """A time from Slurm's text output (the cluster's local time, naive)."""
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError:
        return None          # "N/A", "Unknown", "None"


def _number(value):
    """A JSON number, which Slurm >= 23 wraps as {"set", "infinite", "number"}."""
    if isinstance(value, dict):
        if not value.get("set", True) or value.get("infinite"):
            return None
        value = value.get("number")
    return value


def _json_time(value) -> datetime | None:
    n = _number(value)
    return datetime.fromtimestamp(n, timezone.utc) if n else None


def _int(text) -> int | None:
    try:
        return int(str(text).strip())
    except ValueError:
        return None


def _mb(text: str) -> int | None:
    """Megabytes from "5000", "122080+", "100M", "2G"; None for UNLIMITED/0."""
    text = text.strip().rstrip("+").upper()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGT]?)", text)
    if not m:
        return None
    factor = {"K": 1 / 1024, "": 1, "M": 1, "G": 1024, "T": 1024 ** 2}[m.group(2)]
    value = int(float(m.group(1)) * factor)
    return value or None


# -- host lists ----------------------------------------------------------------

def _split_top(text: str, sep: str = ",") -> list[str]:
    """Split on sep outside brackets."""
    parts, depth, start = [], 0, 0
    for i, c in enumerate(text):
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p for p in parts if p]


def expand_hostlist(text: str) -> list[str]:
    """ "compute[01-03,06],novas04" -> compute01 compute02 compute03 compute06 novas04"""
    hosts = []
    for item in _split_top(text.strip()):
        m = re.match(r"^([^\[]*)\[([^\]]*)\](.*)$", item)
        if not m:
            hosts.append(item)
            continue
        prefix, ranges, rest = m.groups()
        for r in ranges.split(","):
            lo, _, hi = r.partition("-")
            if hi:
                width = len(lo)
                names = [f"{prefix}{i:0{width}d}" for i in range(int(lo), int(hi) + 1)]
            else:
                names = [prefix + lo]
            for name in names:
                hosts.extend(expand_hostlist(name + rest) if "[" in rest else [name + rest])
    return hosts


# -- key=value text (scontrol -o) -------------------------------------------------

_KEY = re.compile(r"(?:^|\s)([A-Za-z][\w/:.]*)=")


def parse_key_values(line: str) -> dict[str, str]:
    """One `scontrol show ... -o` line. Values may contain spaces (OS=Linux 7.2 ...):
    a value runs until the next " Key=" ."""
    matches = list(_KEY.finditer(line))
    values = {}
    for m, nxt in zip(matches, matches[1:] + [None]):
        end = nxt.start() if nxt else len(line)
        values[m.group(1)] = line[m.end():end].strip()
    return values


# -- parsers --------------------------------------------------------------------

def parse_partitions(text: str) -> list[Partition]:
    """`scontrol show partition -o`."""
    partitions = []
    for line in text.splitlines():
        if not line.strip():
            continue
        kv = parse_key_values(line)
        if "PartitionName" not in kv:
            continue
        groups = kv.get("AllowGroups", "ALL")
        accounts = kv.get("AllowAccounts", "ALL")
        qos = kv.get("AllowQos", "ALL")
        partitions.append(Partition(
            name=kv["PartitionName"],
            default=kv.get("Default") == "YES",
            up=kv.get("State", "UP") == "UP",
            nodes=expand_hostlist(kv.get("Nodes", "")) if kv.get("Nodes") not in (None, "(null)") else [],
            total_cpus=_int(kv.get("TotalCPUs", "0")) or 0,
            max_time_min=_minutes(kv.get("MaxTime", "UNLIMITED")),
            default_time_min=_minutes(kv.get("DefaultTime", "NONE")),
            default_mem_per_node_mb=_mb(kv.get("DefMemPerNode", "UNLIMITED")),
            default_mem_per_cpu_mb=_mb(kv.get("DefMemPerCPU", "UNLIMITED")),
            max_mem_per_node_mb=_mb(kv.get("MaxMemPerNode", "UNLIMITED")),
            allowed_groups=None if groups == "ALL" else groups.split(","),
            allowed_accounts=None if accounts == "ALL" else accounts.split(","),
            allowed_qos=None if qos == "ALL" else qos.split(","),
        ))
    return partitions


def parse_nodes(text: str) -> list[Node]:
    """`sinfo -h -N -o "%N|%P|%c|%m|%G|%t|%C|%O|%e|%z"`: one line per node and
    partition. Lines without the last field (%z, the node layout) still read."""
    nodes: dict[str, Node] = {}
    for line in text.splitlines():
        f = line.split("|")
        if len(f) == 9:
            f.append("")
        if len(f) != 10:
            continue
        name, partition, cpus, memory, gres, state, aiot, load, free, layout = (x.strip() for x in f)
        node = nodes.get(name)
        if node is None:
            alloc, idle, *_ = (aiot.split("/") + ["0", "0"])[:4]
            try:
                cpu_load = float(load)
            except ValueError:
                cpu_load = None
            node = nodes[name] = Node(
                name=name, state=state, cpus=_int(cpus) or 0,
                cpus_allocated=_int(alloc) or 0, cpus_idle=_int(idle) or 0,
                memory_mb=_mb(memory) or 0, free_memory_mb=_mb(free),
                cpu_load=cpu_load, gres="" if gres == "(null)" else gres)
            parts = [_int(x) or 0 for x in layout.split(":")]
            if len(parts) == 3:
                node.sockets, node.cores_per_socket, node.threads_per_core = parts
        node.partitions.append(partition.rstrip("*"))
    return list(nodes.values())


def _exit_from_json(value) -> tuple[int | None, int | None]:
    """(exit code, signal) from Slurm's JSON exit_code, in its several forms."""
    if value is None:
        return None, None
    if isinstance(value, int):
        return value, None
    code = _number(value.get("return_code"))
    sig = value.get("signal", {})
    signal = _number(sig.get("id")) if isinstance(sig, dict) else None
    status = value.get("status", [])
    if "SIGNALED" in status:
        return None, signal
    return code, signal or None


def _no_result_if_cancelled_quietly(state: JobState, code, signal):
    """A job cancelled before it ran reports exit code 0: no result, not "exit 0"."""
    if state is JobState.CANCELLED and code == 0 and not signal:
        return None, None
    return code, signal


def parse_jobs_json(text: str) -> dict[str, Job]:
    """`squeue --json` or `scontrol show job --json`."""
    data = json.loads(text)
    jobs = {}
    for j in data.get("jobs", []):
        state, word = _state(j.get("job_state"))
        code, signal = _exit_from_json(j.get("exit_code"))
        if not state.ended:
            code, signal = None, None
        code, signal = _no_result_if_cancelled_quietly(state, code, signal)
        job_id = str(j["job_id"])
        if _number(j.get("array_task_id")) is not None:
            job_id = f"{_number(j.get('array_job_id'))}_{_number(j['array_task_id'])}"
        stdout = j.get("stdout_expanded") or j.get("standard_output") or ""
        jobs[job_id] = Job(
            id=job_id, name=j.get("name", ""), state=state, scheduler_state=word,
            reason=j.get("state_reason", "") if j.get("state_reason") != "None" else "",
            partition=j.get("partition", ""), nodes=j.get("nodes", ""),
            num_nodes=_number(j.get("node_count")), num_cpus=_number(j.get("cpus")),
            time_limit_min=_number(j.get("time_limit")),
            submit_time=_json_time(j.get("submit_time")),
            start_time=_json_time(j.get("start_time")) if state is not JobState.PENDING else None,
            end_time=_json_time(j.get("end_time")) if state.ended else None,
            workdir=j.get("current_working_directory", ""),
            stdout=stdout.replace("%j", job_id),
            exit_code=code, signal=signal)
    return jobs


def parse_jobs_text(text: str) -> dict[str, Job]:
    """`squeue -h -o <_SQUEUE_FORMAT>`: no node list, end time or exit code
    (scontrol has them)."""
    jobs = {}
    for line in text.splitlines():
        f = line.split("|")
        if len(f) != len(_SQUEUE_FIELDS):
            continue
        (job_id, name, partition, word, reason, _elapsed, limit, num_nodes, cpus,
         submit, start, workdir) = (x.strip() for x in f)
        state, word = _state(word)
        jobs[job_id] = Job(
            id=job_id, name=name, state=state, scheduler_state=word,
            reason="" if reason == "None" else reason, partition=partition,
            num_nodes=_int(num_nodes), num_cpus=_int(cpus),
            time_limit_min=_minutes(limit), submit_time=_text_time(submit),
            start_time=_text_time(start) if state is not JobState.PENDING else None,
            workdir=workdir)
    return jobs


def parse_job_text(line: str) -> Job | None:
    """One `scontrol show job -o` line."""
    kv = parse_key_values(line)
    if "JobId" not in kv:
        return None
    state, word = _state(kv.get("JobState", ""))
    code, signal = _exit_text(state, kv.get("ExitCode", ""))
    nodes = kv.get("NodeList", "")
    return Job(
        id=kv["JobId"], name=kv.get("JobName", ""), state=state, scheduler_state=word,
        reason="" if kv.get("Reason") == "None" else kv.get("Reason", ""),
        partition=kv.get("Partition", ""), nodes="" if nodes == "(null)" else nodes,
        num_nodes=_int(kv.get("NumNodes", "")), num_cpus=_int(kv.get("NumCPUs", "")),
        time_limit_min=_minutes(kv.get("TimeLimit", "")),
        submit_time=_text_time(kv.get("SubmitTime", "")),
        start_time=_text_time(kv.get("StartTime", "")) if state is not JobState.PENDING else None,
        end_time=_text_time(kv.get("EndTime", "")) if state.ended else None,
        workdir=kv.get("WorkDir", ""), stdout=kv.get("StdOut", ""),
        exit_code=code, signal=signal)


def _exit_text(state: JobState, text: str) -> tuple[int | None, int | None]:
    """(exit code, signal) from "code:signal", for ended jobs."""
    if not state.ended or ":" not in text:
        return None, None
    c, s = text.split(":", 1)
    code, signal = _int(c), _int(s) or None
    return _no_result_if_cancelled_quietly(state, None if signal else code, signal)


def parse_sacct(text: str) -> dict[str, Job]:
    """`sacct -nP -o <SACCT_FIELDS>`: jobs with their steps folded in (peak
    memory is the largest step's)."""
    jobs: dict[str, Job] = {}
    rss: dict[str, int] = {}
    n = len(SACCT_FIELDS.split(","))
    for line in text.splitlines():
        f = line.split("|")
        if len(f) != n:
            continue
        (job_id, name, partition, word, exit_code, elapsed, limit, cpus, num_nodes,
         nodes, submit, start, end, workdir, max_rss) = (x.strip() for x in f)
        base, _, step = job_id.partition(".")
        if step:
            mb = _mb(max_rss)
            if mb:
                rss[base] = max(rss.get(base, 0), mb)
            continue
        state, word = _state(word)
        code, signal = _exit_text(state, exit_code)
        jobs[base] = Job(
            id=base, name=name, state=state, scheduler_state=word, partition=partition,
            nodes="" if nodes in ("None assigned", "(null)") else nodes,
            num_nodes=_int(num_nodes), num_cpus=_int(cpus), time_limit_min=_minutes(limit),
            submit_time=_text_time(submit),
            start_time=_text_time(start) if state is not JobState.PENDING else None,
            end_time=_text_time(end) if state.ended else None,
            workdir=workdir, exit_code=code, signal=signal, elapsed_s=parse_duration(elapsed))
    for base, mb in rss.items():
        if base in jobs:
            jobs[base].max_rss_mb = mb
    return jobs


def parse_associations(text: str) -> list[Association]:
    """`sacctmgr -nP show assoc where user=... format=<ASSOC_FIELDS>`."""
    result = []
    for line in text.splitlines():
        f = line.split("|")
        if len(f) != len(ASSOC_FIELDS.split(",")):
            continue
        account, partition, qos, default_qos, max_wall, max_jobs, max_submit, grp, mx = \
            (x.strip() for x in f)
        if not account:
            continue
        result.append(Association(
            account=account, partition=partition,
            qos=[q for q in qos.split(",") if q], default_qos=default_qos,
            max_wall_min=_minutes(max_wall), max_jobs=_int(max_jobs) if max_jobs else None,
            max_submit=_int(max_submit) if max_submit else None, grp_tres=grp, max_tres=mx))
    return result


def parse_qos(text: str) -> list[Qos]:
    """`sacctmgr -nP show qos format=<QOS_FIELDS>`."""
    result = []
    for line in text.splitlines():
        f = line.split("|")
        if len(f) != len(QOS_FIELDS.split(",")):
            continue
        name, priority, max_wall, tres_pu, jobs_pu, submit_pu, mx = (x.strip() for x in f)
        if not name:
            continue
        result.append(Qos(
            name=name, priority=_int(priority) if priority else None,
            max_wall_min=_minutes(max_wall), max_tres_per_user=tres_pu,
            max_jobs_per_user=_int(jobs_pu) if jobs_pu else None,
            max_submit_per_user=_int(submit_pu) if submit_pu else None, max_tres=mx))
    return result


_END_REASONS = [
    (re.compile(r"CANCELLED AT \S+ DUE TO TIME LIMIT", re.I), JobState.TIMEOUT),
    # slurmstepd's messages only: a job's environment has SLURM_OOM_KILL_STEP=0
    (re.compile(r"oom[-_]kill event|OOM Killed|Exceeded (job|step) memory limit", re.I),
     JobState.OUT_OF_MEMORY),
    (re.compile(r"CANCELLED AT \S+ DUE TO NODE FAIL", re.I), JobState.FAILED),
    (re.compile(r"CANCELLED AT \S+ DUE TO PREEMPTION", re.I), JobState.CANCELLED),
    (re.compile(r"\*\*\* JOB \S+ ON \S+ CANCELLED AT", re.I), JobState.CANCELLED),
]


def end_reason(output: str) -> JobState | None:
    """Why a job was killed, from the messages Slurm writes into its output
    file; None if there are none (the job ended by itself)."""
    for pattern, state in _END_REASONS:
        if pattern.search(output):
            return state
    return None


# -- the scheduler ---------------------------------------------------------------

class Slurm(Scheduler):
    def __init__(self, transport: Transport, *, use_json: bool | None = None):
        """use_json None: decided from the Slurm version (>= 21.08)."""
        self.transport = transport
        self._version: str | None = None
        self._use_json = use_json
        self._accounting: bool | None = None
        # Why accounting is unavailable, as Slurm said it (for the GUI)
        self.accounting_note = ""

    async def _run(self, command: str, what: str) -> str:
        r = await self.transport.run(command)
        if not r.ok:
            detail = (r.stderr.strip() or r.stdout.strip()).splitlines()
            raise SchedulerError(f"{what} failed: " + ("\n".join(detail) or f"exit code {r.exit_code}"))
        return r.stdout

    async def version(self) -> str:
        if self._version is None:
            out = await self._run("sinfo --version", "Reading the Slurm version")
            m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out)
            if not m:
                raise SchedulerError(f"Unrecognised Slurm version: {out.strip()!r}")
            self._version = m.group(0)
        return self._version

    async def use_json(self) -> bool:
        if self._use_json is None:
            major, minor = (int(x) for x in (await self.version()).split(".")[:2])
            self._use_json = (major, minor) >= (21, 8)
        return self._use_json

    async def partitions(self):
        return parse_partitions(await self._run("scontrol show partition -o", "Listing partitions"))

    async def nodes(self):
        return parse_nodes(await self._run(f"sinfo -h -N -o {quote(_SINFO_FORMAT)}", "Listing nodes"))

    async def user_groups(self):
        return (await self._run("id -Gn", "Reading your groups")).split()

    async def submit(self, script, workdir, *, after_ok=(), options=()):
        args = ["sbatch", "--parsable"]
        if after_ok:
            for j in after_ok:
                self._check_id(j)
            args.append("--dependency=afterok:" + ":".join(after_ok))
        args += list(options) + [script]
        command = f"cd {quote(workdir)} && " + " ".join(quote(a) for a in args)
        out = await self._run(command, "Submitting the job")
        job_id = out.strip().splitlines()[-1].split(";")[0].strip() if out.strip() else ""
        if not _JOB_ID.match(job_id):
            raise SchedulerError(f"sbatch gave no job id: {out.strip()!r}")
        return job_id

    async def cancel(self, job_ids):
        if not job_ids:
            return
        for j in job_ids:
            self._check_id(j)
        await self._run("scancel " + " ".join(job_ids), "Cancelling")

    async def jobs(self):
        # --states=all: also the jobs that ended in the last MinJobAge seconds
        if await self.use_json():
            out = await self._run('squeue --json --user="$USER" --states=all', "Listing jobs")
            try:
                return parse_jobs_json(out)
            except (ValueError, KeyError, TypeError, AttributeError):
                self._use_json = False      # unreadable JSON: use text from now on
        out = await self._run(f'squeue -h --user="$USER" --states=all -o {quote(_SQUEUE_FORMAT)}',
                              "Listing jobs")
        return parse_jobs_text(out)

    async def job(self, job_id):
        self._check_id(job_id)
        json_mode = await self.use_json()
        command = f"scontrol show job {'--json' if json_mode else '-o'} {job_id}"
        r = await self.transport.run(command)
        if not r.ok:
            if "invalid job id" in (r.stderr + r.stdout).lower():
                return None
            raise SchedulerError(f"Reading job {job_id} failed: {(r.stderr or r.stdout).strip()}")
        if json_mode:
            try:
                return parse_jobs_json(r.stdout).get(job_id)
            except (ValueError, KeyError, TypeError, AttributeError):
                self._use_json = False
                return await self.job(job_id)
        return next((j for line in r.stdout.splitlines() if (j := parse_job_text(line))), None)

    # -- accounting --------------------------------------------------------------

    async def accounting_available(self):
        if self._accounting is None:
            r = await self.transport.run("sacct -n -X -o JobID -S now")
            self._accounting = r.ok
            if not r.ok:
                self.accounting_note = (r.stderr.strip() or r.stdout.strip()
                                        or f"sacct exit code {r.exit_code}")
        return self._accounting

    async def history(self, job_ids):
        if not job_ids or not await self.accounting_available():
            return {}
        for j in job_ids:
            self._check_id(j)
        # With -j, sacct searches from the beginning, not only today
        out = await self._run(f"sacct -nP -j {','.join(job_ids)} -o {SACCT_FIELDS}",
                              "Reading job accounting")
        return parse_sacct(out)

    async def _sacctmgr(self, command: str):
        """sacctmgr output, or None where users may not read it."""
        if not await self.accounting_available():
            return None
        r = await self.transport.run(command)
        return r.stdout if r.ok else None

    async def associations(self):
        out = await self._sacctmgr(
            f'sacctmgr -nP show assoc where user="$USER" format={ASSOC_FIELDS}')
        return parse_associations(out) if out else []

    async def qos(self):
        out = await self._sacctmgr(f"sacctmgr -nP show qos format={QOS_FIELDS}")
        return parse_qos(out) if out else []

    @staticmethod
    def _check_id(job_id: str):
        if not _JOB_ID.match(job_id):
            raise ValueError(f"Not a Slurm job id: {job_id!r}")
