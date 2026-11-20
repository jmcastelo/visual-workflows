"""Jobs that record their own status, and how a job ended.

Not every cluster keeps accounting (Lynx does not), and Slurm forgets a job a
few minutes after it ends. So every job script gets a few lines, right after
its #SBATCH header, that append to .vwf/job-<id>.status in the folder it was
submitted from:

    started <epoch> <nodes>
    signal <epoch> TERM          when Slurm stops it (time limit, scancel)
    modules <loaded modules>     at exit
    ended <epoch> <exit code>

final_state() then decides how a job ended from what is available, in this
order: Slurm (while it remembers the job), accounting (sacct, where kept),
the status file with the job's output (Slurm's "CANCELLED ... DUE TO TIME
LIMIT" lines). See docs/design.md §5.1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .scheduler import Job, JobState
from .scheduler.slurm import end_reason

STATUS_DIR = ".vwf"
BEGIN = "# >>> Visual Workflows job status: do not edit >>>"
END = "# <<< Visual Workflows job status <<<"

# Recorded on Lynx: at the time limit Slurm sends SIGTERM to the batch script,
# then SIGKILL after KillWait (30 s). The TERM trap must exit, or the script
# would go on with its next command until the kill.
STATUS_LINES = f"""{BEGIN}
_vwf_status="${{SLURM_SUBMIT_DIR:-$PWD}}/{STATUS_DIR}/job-${{SLURM_JOB_ID:-0}}.status"
mkdir -p "${{_vwf_status%/*}}"
echo "started $(date +%s) ${{SLURM_JOB_NODELIST:-$(hostname)}}" >> "$_vwf_status"
_vwf_exit() {{
    _vwf_rc=$?
    if type module >/dev/null 2>&1; then
        echo "modules $(module -t list 2>&1 | grep -v -e ':$' -e 'No Modulefiles' | tr '\\n' ' ')" >> "$_vwf_status"
    fi
    echo "ended $(date +%s) $_vwf_rc" >> "$_vwf_status"
}}
trap _vwf_exit EXIT
trap 'echo "signal $(date +%s) TERM" >> "$_vwf_status"; exit 143' TERM
{END}
"""


def status_path(job_id: str) -> str:
    """Relative to the folder the job was submitted from."""
    return f"{STATUS_DIR}/job-{job_id}.status"


def add_status_lines(script: str) -> str:
    """The script with the status lines after its #SBATCH header (Slurm reads
    #SBATCH lines only before the first command, so they go after them).
    Lines from an earlier call are replaced, not repeated."""
    script = remove_status_lines(script)
    lines = script.splitlines(keepends=True)
    if not lines or not lines[0].startswith("#!"):
        lines.insert(0, "#!/bin/bash\n")
    insert = 1
    for i in range(1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("#SBATCH"):
            insert = i + 1
        elif stripped and not stripped.startswith("#"):
            break                    # the first command ends the header
    if not lines[insert - 1].endswith("\n"):
        lines[insert - 1] += "\n"
    return "".join(lines[:insert]) + STATUS_LINES + "".join(lines[insert:])


def remove_status_lines(script: str) -> str:
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)
    return pattern.sub("", script)


@dataclass
class StatusFile:
    started: datetime | None = None
    nodes: str = ""
    ended: datetime | None = None
    exit_code: int | None = None
    signal: str = ""                 # "TERM" when Slurm stopped it
    modules: list[str] = field(default_factory=list)


def _epoch(text: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(text), timezone.utc)
    except ValueError:
        return None


def parse_status(text: str) -> StatusFile:
    """A job's status file. A requeued job appends again: the last run counts."""
    s = StatusFile()
    for line in text.splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == "started" and len(words) >= 2:
            s = StatusFile(started=_epoch(words[1]), nodes=" ".join(words[2:]))
        elif words[0] == "signal" and len(words) >= 3:
            s.signal = words[2]
        elif words[0] == "modules":
            s.modules = words[1:]
        elif words[0] == "ended" and len(words) >= 3:
            s.ended = _epoch(words[1])
            try:
                s.exit_code = int(words[2])
            except ValueError:
                pass
    return s


@dataclass
class Outcome:
    state: JobState
    reason: str = ""
    exit_code: int | None = None
    signal: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    nodes: str = ""
    modules: list[str] = field(default_factory=list)
    source: str = ""          # "slurm", "accounting", "status file", "none"


def final_state(slurm: Job | None, accounting: Job | None, status: StatusFile | None,
                output: str | None) -> Outcome:
    """How a job stands, from the best information there is."""
    for job, source in ((slurm, "slurm"), (accounting, "accounting")):
        if job is not None and job.state is not JobState.UNKNOWN:
            return Outcome(job.state, job.reason, job.exit_code, job.signal, job.start_time,
                           job.end_time, job.nodes,
                           status.modules if status else [], source)
    if status is None or status.started is None:
        # Never started (or its status file is gone): cancelled while pending
        return Outcome(JobState.CANCELLED if status is None else JobState.UNKNOWN,
                       "no record of the job running", source="none")
    reason_from_output = end_reason(output) if output else None
    base = dict(start_time=status.started, end_time=status.ended, nodes=status.nodes,
                modules=status.modules, source="status file")
    if status.ended is None:
        # Killed without a chance to write (SIGKILL, node failure, out of memory)
        state = reason_from_output or JobState.FAILED
        return Outcome(state, "killed" if state is JobState.FAILED else "", **base)
    if status.signal:
        state = reason_from_output or JobState.CANCELLED
        return Outcome(state, f"stopped by SIG{status.signal}", signal=15, **base)
    if status.exit_code == 0:
        return Outcome(JobState.COMPLETED, exit_code=0, **base)
    state = reason_from_output or JobState.FAILED
    return Outcome(state, "NonZeroExitCode" if state is JobState.FAILED else "",
                   exit_code=status.exit_code, **base)


def output_file(script: str, job_id: str, job_name: str) -> str:
    """Slurm's output file for a job, from the script's --output (default
    slurm-%j.out), with %j and %x filled in."""
    pattern = "slurm-%j.out"
    for line in script.splitlines():
        m = re.match(r"\s*#SBATCH\s+(?:--output[= ]|-o\s*)(\S+)", line)
        if m:
            pattern = m.group(1)
    return pattern.replace("%j", job_id).replace("%x", job_name).replace("%%", "%")


def job_name(script: str, default: str) -> str:
    for line in script.splitlines():
        m = re.match(r"\s*#SBATCH\s+(?:--job-name[= ]|-J\s*)(\S+)", line)
        if m:
            return m.group(1)
    return default
