"""What a cluster offers, in a few lines: the connection's report (`vwf test`,
Test connection in the cluster editor). One implementation for both, so the
command line and the window always say the same."""

from __future__ import annotations

from dataclasses import dataclass, field

from .modules import Modules, ModuleSystem
from .profile import ClusterProfile
from .scheduler import Scheduler
from .shell import quote
from .transport import Transport


@dataclass
class ClusterReport:
    home: str
    slurm_version: str
    json_output: bool = False
    accounting: bool = False
    accounting_note: str = ""        # why there is none ("storage is disabled")
    groups: list[str] = field(default_factory=list)
    python: str = ""                 # "Python 3.11.5"; "" when not found
    modules: ModuleSystem | None = None

    @property
    def modules_text(self) -> str:
        if self.modules is None or not self.modules.kind:
            return "none"
        return f"{self.modules.kind} {self.modules.version}".strip()

    def lines(self, python_command: str = "python3") -> list[str]:
        return [f"Home: {self.home}",
                f"Slurm {self.slurm_version}" + (" (JSON output)" if self.json_output else ""),
                "Accounting: " + ("yes" if self.accounting else
                                  "no" + (f" ({self.accounting_note})" if self.accounting_note else "")),
                "Your groups: " + " ".join(self.groups),
                "Python: " + (self.python or f"not found ({python_command})"),
                "Modules: " + self.modules_text]


async def cluster_report(transport: Transport, scheduler: Scheduler, profile: ClusterProfile,
                         modules: Modules | None = None) -> ClusterReport:
    """Ask the cluster: its home folder, Slurm, accounting, the user's groups,
    Python and the module system."""
    use_json = getattr(scheduler, "use_json", None)
    report = ClusterReport(home=await transport.home(), slurm_version=await scheduler.version(),
                           json_output=bool(use_json and await use_json()))
    report.accounting = await scheduler.accounting_available()
    report.accounting_note = getattr(scheduler, "accounting_note", "")
    report.groups = await scheduler.user_groups()
    r = await transport.run(f"{quote(profile.python)} --version")
    if r.ok:
        report.python = (r.stdout or r.stderr).strip()
    modules = modules or Modules(transport, profile.module_use)
    report.modules = await modules.system()
    return report
