from .base import ENDED_STATES, Job, JobState, Node, Partition, Scheduler, SchedulerError
from .slurm import Slurm

__all__ = ["ENDED_STATES", "Job", "JobState", "Node", "Partition", "Scheduler",
           "SchedulerError", "Slurm"]


def scheduler_for(kind: str, transport) -> Scheduler:
    """The scheduler named in a cluster profile."""
    if kind == "slurm":
        return Slurm(transport)
    raise ValueError(f"Unsupported scheduler {kind!r}")
