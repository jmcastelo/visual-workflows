from .base import (ENDED_STATES, Association, Job, JobState, Node, Partition, Qos, Scheduler,
                   SchedulerError)
from .slurm import Slurm

__all__ = ["ENDED_STATES", "Association", "Job", "Qos", "JobState", "Node", "Partition", "Scheduler",
           "SchedulerError", "Slurm"]


def scheduler_for(kind: str, transport) -> Scheduler:
    """The scheduler named in a cluster profile."""
    if kind == "slurm":
        return Slurm(transport)
    raise ValueError(f"Unsupported scheduler {kind!r}")
