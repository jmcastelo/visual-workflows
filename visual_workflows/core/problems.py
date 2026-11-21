"""What the checks of a job find: errors (the job would be refused, or fail)
and warnings (worth a look). Shared by jobspec.py and parallel.py."""

from dataclasses import dataclass


@dataclass
class Problem:
    message: str
    field: str = ""                 # "partition", "time", "runs[2].args" ...
    error: bool = True              # False: a warning, the job can still be submitted

    def __str__(self) -> str:
        return ("Error: " if self.error else "Warning: ") + self.message
