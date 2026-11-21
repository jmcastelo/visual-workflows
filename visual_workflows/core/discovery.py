"""What Discover finds on a cluster for its profile: the partitions the user
may use (with their limits, see profile.discovered_partition), and, on
clusters with accounting, the accounts and QOS that give access to them.
Shared by the GUI's Discover and `vwf clusters discover`."""

from __future__ import annotations

from dataclasses import dataclass, field

from .profile import Partition, discovered_partition
from .scheduler import Scheduler, access


@dataclass
class Discovery:
    partitions: dict[str, Partition] = field(default_factory=dict)   # those the user may use
    accounts: list[str] = field(default_factory=list)                # with accounting only
    default_qos: str = ""            # the default partition's default QOS
    qos: dict[str, list[str]] = field(default_factory=dict)          # partition -> its QOS

    @property
    def account(self) -> str:
        """The account to use when there is only one ("" otherwise)."""
        return self.accounts[0] if len(self.accounts) == 1 else ""


async def discover(scheduler: Scheduler) -> Discovery:
    partitions, nodes = await scheduler.partitions(), await scheduler.nodes()
    groups = await scheduler.user_groups()
    associations, qos = [], []
    if await scheduler.accounting_available():
        associations, qos = await scheduler.associations(), await scheduler.qos()
    found, accesses = Discovery(), {}
    for p in partitions:
        acc = access(p, groups, associations, qos)
        if acc.allowed:
            accesses[p.name] = (p, acc)
            found.partitions[p.name] = discovered_partition(p, acc, nodes)
    if associations:
        found.accounts = sorted({a for _, acc in accesses.values() for a in acc.accounts})
        default = next((acc for p, acc in accesses.values() if p.default),
                       next(iter(accesses.values()), (None, None))[1])
        found.default_qos = (default.default_qos or "") if default is not None else ""
        found.qos = {name: list(acc.qos) for name, (_, acc) in accesses.items()}
    return found
