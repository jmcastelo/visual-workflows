"""What the rest of the program knows about a batch scheduler.

Slurm is the only implementation (slurm.py); the interface keeps the rest of
the code free of Slurm's commands and formats, so others can be added.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SchedulerError(Exception):
    """A scheduler command failed; the message includes the scheduler's own."""


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    OUT_OF_MEMORY = "out_of_memory"
    UNKNOWN = "unknown"

    @property
    def ended(self) -> bool:
        return self in ENDED_STATES


ENDED_STATES = frozenset({JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED,
                          JobState.TIMEOUT, JobState.OUT_OF_MEMORY})


@dataclass
class Job:
    id: str
    name: str = ""
    state: JobState = JobState.UNKNOWN
    scheduler_state: str = ""          # the scheduler's own word, e.g. "COMPLETING"
    reason: str = ""                   # why pending or why ended, e.g. "JobHeldUser"
    partition: str = ""
    nodes: str = ""                    # node list, e.g. "compute[01-02]"
    num_nodes: int | None = None
    num_cpus: int | None = None
    time_limit_min: int | None = None  # None: unlimited or unknown
    submit_time: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    workdir: str = ""
    stdout: str = ""                   # output file, with %j etc. expanded when known
    exit_code: int | None = None       # of the batch script, when it ended normally
    signal: int | None = None          # when it was killed by a signal
    # From accounting only (sacct), where the cluster keeps it
    elapsed_s: int | None = None
    max_rss_mb: int | None = None      # peak memory of the largest job step

    @property
    def held(self) -> bool:
        return self.state is JobState.PENDING and "held" in self.reason.lower()


@dataclass
class Partition:
    name: str
    default: bool = False
    up: bool = True
    nodes: list[str] = field(default_factory=list)
    total_cpus: int = 0
    max_time_min: int | None = None            # None: unlimited
    default_time_min: int | None = None
    default_mem_per_node_mb: int | None = None  # what a job gets without --mem
    default_mem_per_cpu_mb: int | None = None
    max_mem_per_node_mb: int | None = None      # None: unlimited
    allowed_groups: list[str] | None = None     # None: everyone
    allowed_accounts: list[str] | None = None   # None: any account
    allowed_qos: list[str] | None = None        # None: any QOS

    def allows(self, groups: list[str]) -> bool:
        """By Unix group only; access() also weighs accounts and QOS."""
        return self.allowed_groups is None or bool(set(self.allowed_groups) & set(groups))


@dataclass
class Node:
    name: str
    partitions: list[str] = field(default_factory=list)
    state: str = ""                  # e.g. "idle", "mix", "alloc", "down"
    cpus: int = 0                    # as the scheduler counts them (hardware threads)
    cpus_allocated: int = 0
    cpus_idle: int = 0
    memory_mb: int = 0
    free_memory_mb: int | None = None
    cpu_load: float | None = None
    gres: str = ""                   # GPUs and other generic resources
    sockets: int = 0                 # node layout (sinfo %z); 0: unknown
    cores_per_socket: int = 0
    threads_per_core: int = 0        # 2 or more: cpus counts hardware threads

    @property
    def cores(self) -> int:
        """Physical cores (0: unknown)."""
        return self.sockets * self.cores_per_socket


@dataclass
class Association:
    """What the user may charge: an account, possibly per partition, with its
    QOS and limits. Only on clusters with accounting (sacctmgr)."""
    account: str
    partition: str = ""                # empty: any partition
    qos: list[str] = field(default_factory=list)
    default_qos: str = ""
    max_wall_min: int | None = None
    max_jobs: int | None = None        # running at once
    max_submit: int | None = None      # queued at once
    grp_tres: str = ""                 # e.g. "cpu=512,mem=2T", as Slurm writes it
    max_tres: str = ""                 # per job


@dataclass
class Qos:
    name: str
    priority: int | None = None
    max_wall_min: int | None = None    # per job
    max_tres_per_user: str = ""
    max_jobs_per_user: int | None = None
    max_submit_per_user: int | None = None
    max_tres: str = ""                 # per job


@dataclass
class Access:
    """How the user may use a partition: through which accounts and QOS, and
    the longest time a job can ask for there."""
    partition: str
    allowed: bool
    accounts: list[str] = field(default_factory=list)   # empty: no accounting known
    qos: list[str] = field(default_factory=list)
    default_qos: str = ""
    max_time_min: int | None = None                     # None: unlimited
    # The time limit with each QOS (the QOS's, lowered by the partition's)
    qos_limits: dict = field(default_factory=dict)      # QOS -> minutes or None


def access(partition: Partition, groups: list[str], associations: list[Association],
           qos: list[Qos]) -> Access:
    """Whether the user may submit to a partition. Unix groups first; then,
    where accounting is known (associations), the accounts and QOS the
    partition accepts among the user's. Time limit: the partition's, lowered
    by the best QOS the user may use there."""
    if not partition.allows(groups):
        return Access(partition.name, False)
    if not associations:
        return Access(partition.name, True, max_time_min=partition.max_time_min)
    accounts, usable, default = [], [], ""
    for a in associations:
        if a.partition and a.partition != partition.name:
            continue
        if partition.allowed_accounts is not None and a.account not in partition.allowed_accounts:
            continue
        mine = [q for q in a.qos if partition.allowed_qos is None or q in partition.allowed_qos]
        if not mine and a.qos:
            continue
        accounts.append(a.account)
        usable += [q for q in mine if q not in usable]
        if a.default_qos in mine and not default:
            default = a.default_qos
    if not accounts:
        return Access(partition.name, False)
    limits = {q.name: q.max_wall_min for q in qos}

    def lowered(wall):
        if partition.max_time_min is None:
            return wall
        return partition.max_time_min if wall is None else min(wall, partition.max_time_min)

    walls = [limits.get(q) for q in usable] or [None]
    best = None if any(w is None for w in walls) else max(walls)
    return Access(partition.name, True, accounts, usable, default or (usable[0] if usable else ""),
                  lowered(best), {q: lowered(limits.get(q)) for q in usable})


class Scheduler(ABC):
    @abstractmethod
    async def version(self) -> str: ...

    @abstractmethod
    async def partitions(self) -> list[Partition]: ...

    @abstractmethod
    async def nodes(self) -> list[Node]: ...

    @abstractmethod
    async def user_groups(self) -> list[str]: ...

    @abstractmethod
    async def submit(self, script: str, workdir: str, *,
                     after_ok: list[str] = (), options: list[str] = ()) -> str:
        """Submit a job script from workdir; returns the job id.

        after_ok: jobs that must complete successfully first.
        """

    @abstractmethod
    async def cancel(self, job_ids: list[str]) -> None: ...

    @abstractmethod
    async def jobs(self) -> dict[str, Job]:
        """The user's jobs the scheduler knows: queued, running and recently ended."""

    @abstractmethod
    async def job(self, job_id: str) -> Job | None:
        """One job in detail, or None if the scheduler no longer knows it."""

    # Accounting: not every cluster keeps it. These return empty results
    # where it is missing; accounting_available() says which is the case.

    @abstractmethod
    async def accounting_available(self) -> bool: ...

    @abstractmethod
    async def history(self, job_ids: list[str]) -> dict[str, Job]:
        """Ended (or running) jobs from accounting, with elapsed time and peak
        memory; {} without accounting."""

    @abstractmethod
    async def associations(self) -> list[Association]:
        """Accounts the user may charge; [] without accounting."""

    @abstractmethod
    async def qos(self) -> list[Qos]:
        """Quality-of-service levels and their limits; [] without accounting."""
