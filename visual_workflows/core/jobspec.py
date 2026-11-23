"""Job descriptions: the resources and runs of a step, from which its job
script is written.

A step's `step.toml` may hold a `[job]` table (docs/design.md, Phase 2 plan):

    [job]
    partition = "magma"
    nodes = 1
    tasks_per_node = 48          # MPI tasks
    cpus_per_task = 1            # OpenMP threads
    memory = "60G"               # per node, as sbatch --mem
    time = "12:00:00"
    links = { SAVE = "../03-p2y/SAVE" }
    before = []
    after = []
    [[job.runs]]
    program = "qe"
    executable = "pw"
    input = "pw.scf.in"
    output = "pw.scf.out"
    args = "-nk 4"

check() compares it with the cluster profile (partition limits, programs and
their executables); render() writes the script. Both need nothing but the
profile: no connection to the cluster.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..identity import APP_NAME
from .problems import Problem
from .profile import ClusterProfile, Partition, Program, TableReader
from .scheduler.slurm import parse_duration
from .shell import quote

# Programs whose command lines the script writes itself; others run as
# `executable args`
QE, YAMBO = "qe", "yambo"
# Executables' file names when the profile does not give them
DEFAULT_EXECUTABLES = {"pw": "pw.x", "ph": "ph.x", "dos": "dos.x", "projwfc": "projwfc.x",
                       "bands": "bands.x", "pp": "pp.x", "yambo": "yambo", "p2y": "p2y",
                       "ypp": "ypp"}
# Yambo executables that take -F input and -J jobs
YAMBO_STYLE = ("yambo", "ypp")
# Yambo's -walltime (checked in its source, 5.1.1 and 5.3.0): only the Haydock
# and inversion BSE solvers look at it, once per iteration or frequency; then
# Yambo writes its report and stops (MPI_Abort). It compares the elapsed days,
# hours (0-23) and minutes (0-59) each with its own limit, a 0 meaning "any",
# so "11h50m" only matches in minutes 50-59 of each hour. The script gives
# whole hours ("11h", "1d5h", "2d"), rounded down, and minutes only in the
# last hour; the margin covers the shutdown and the minute steps of the check.
WALLTIME_MARGIN = 5 * 60

# How deep Visual Excitons looks into each step's outputs/ folder (its
# SEARCH_DEPTH) for the databases it reads
VISUAL_EXCITONS_DEPTH = 3

# time = "UNLIMITED": no --time, where the partition and QOS allow it
UNLIMITED = ("UNLIMITED", "INFINITE")

_MEMORY = re.compile(r"^(\d+(?:\.\d+)?)\s*([KMGT]?)B?$", re.I)
_LINK_NAME = re.compile(r"^[^/\0]+$")


class JobSpecError(Exception):
    """The [job] table cannot be read."""


@dataclass
class Run:
    program: str = QE               # a program of the profile: "qe", "yambo", ...
    executable: str = "pw"          # a key of that program's executables
    input: str = ""
    output: str = ""                # QE: where its output goes (default: from the input)
    args: str = ""                  # shell words, as written (e.g. "-nk 4")
    jobs: list[str] = field(default_factory=list)   # Yambo's -J
    mpi: bool = True                # run through the program's MPI launcher

    @property
    def qe_style(self) -> bool:
        return self.program == QE

    @property
    def yambo_style(self) -> bool:
        return self.program == YAMBO and self.executable in YAMBO_STYLE

    @property
    def output_file(self) -> str:
        """Where the run's output goes ("" for the job's own output)."""
        if self.output or not self.qe_style or not self.input:
            return self.output
        stem = self.input[:-3] if self.input.endswith(".in") else self.input
        return stem + ".out"

    def to_table(self) -> dict:
        t = {"program": self.program, "executable": self.executable}
        for key in ("input", "output", "args"):
            if getattr(self, key):
                t[key] = getattr(self, key)
        if self.jobs:
            t["jobs"] = list(self.jobs)
        if not self.mpi:
            t["mpi"] = False
        return t


@dataclass
class JobSpec:
    partition: str = ""
    nodes: int = 1
    tasks_per_node: int = 1
    cpus_per_task: int = 1
    memory: str = ""
    time: str = ""
    account: str = ""               # "" for the profile's
    qos: str = ""                   # "" for the profile's (e.g. "interactive" for a test)
    links: dict[str, str] = field(default_factory=dict)   # name -> target
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)

    @property
    def tasks(self) -> int:
        return self.nodes * self.tasks_per_node

    @property
    def memory_mb(self) -> int | None:
        return memory_mb(self.memory)

    @property
    def unlimited(self) -> bool:
        """No time limit, chosen on purpose (time = "UNLIMITED")."""
        return self.time.strip().upper() in UNLIMITED

    @property
    def time_seconds(self) -> int | None:
        """The time limit in seconds; None without one, or when unreadable."""
        if self.unlimited:
            return None
        try:
            return parse_duration(self.time) if self.time.strip() else None
        except ValueError:
            return None

    # -- the [job] table ------------------------------------------------------------

    @classmethod
    def from_table(cls, table: dict, where: str = "step.toml") -> "JobSpec":
        r = TableReader({"job": table}, where, JobSpecError)
        t = r.table("job")
        runs = t.get("runs", [])
        if not isinstance(runs, list):
            raise JobSpecError(f"{where}: job.runs must be a list of tables ([[job.runs]]).")
        spec = cls(
            partition=r.get(t, "job.partition", str, ""),
            nodes=r.get(t, "job.nodes", int, 1),
            tasks_per_node=r.get(t, "job.tasks_per_node", int, 1),
            cpus_per_task=r.get(t, "job.cpus_per_task", int, 1),
            memory=_text(r.get(t, "job.memory", (str, int), "")),
            time=_text(r.get(t, "job.time", (str, int), "")),
            account=r.get(t, "job.account", str, ""),
            qos=r.get(t, "job.qos", str, ""),
            links=r.string_table(t, "job.links"),
            before=r.strings(t, "job.before"),
            after=r.strings(t, "job.after"))
        for i, run in enumerate(runs, 1):
            key = f"job.runs[{i}]"
            run = r.as_table(run, key)
            spec.runs.append(Run(
                program=r.get(run, f"{key}.program", str, QE),
                executable=r.get(run, f"{key}.executable", str, "pw"),
                input=r.get(run, f"{key}.input", str, ""),
                output=r.get(run, f"{key}.output", str, ""),
                args=r.get(run, f"{key}.args", str, ""),
                jobs=r.strings(run, f"{key}.jobs"),
                mpi=r.get(run, f"{key}.mpi", bool, True)))
        return spec

    def to_table(self) -> dict:
        t = {"partition": self.partition, "nodes": self.nodes,
             "tasks_per_node": self.tasks_per_node, "cpus_per_task": self.cpus_per_task,
             "memory": self.memory, "time": self.time}
        if self.account:
            t["account"] = self.account
        if self.qos:
            t["qos"] = self.qos
        if self.links:
            t["links"] = dict(self.links)
        if self.before:
            t["before"] = list(self.before)
        if self.after:
            t["after"] = list(self.after)
        t["runs"] = [run.to_table() for run in self.runs]
        return t


def _text(value) -> str:
    return str(value).strip()


def memory_mb(text: str) -> int | None:
    """Megabytes from sbatch's --mem: "60G", "60000", "60000M", "1.5T" (a bare
    number is megabytes). None when empty or unreadable."""
    m = _MEMORY.match(str(text).strip())
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2).upper()
    factor = {"K": 1 / 1024, "": 1, "M": 1, "G": 1024, "T": 1024 * 1024}[unit]
    return int(value * factor)


def yambo_walltime(seconds: int) -> str:
    """Yambo's -walltime for a stop after this many seconds, rounded down in a
    form its unit-by-unit check matches from then on (see WALLTIME_MARGIN):
    "2d", "1d5h", "11h", or minutes below an hour ("45m", at least "1m")."""
    hours = seconds // 3600
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d{hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h"
    return f"{max(1, seconds // 60)}m"


# -- checks ------------------------------------------------------------------------

def check(spec: JobSpec, profile: ClusterProfile, folder: Path | None = None,
          remote_headers: dict | None = None, inputs: dict | None = None) -> list[Problem]:
    """What is wrong with the job on this cluster, as far as the profile tells.
    Errors make sbatch refuse the job, or make it wait forever or fail at
    once; warnings are worth a look. With the step's `folder`, the inputs of
    pw.x and yambo runs are read there and checked, with the pseudopotentials
    in the project; `remote_headers` gives those on the cluster ({path as in
    pseudos.PseudoFile.remote: header, message or None}), read at submission.
    `inputs` ({file name: path}) gives input files not in the folder yet (a
    step being made: its folder may not exist)."""
    problems: list[Problem] = []

    def error(message, where=""):
        problems.append(Problem(message, where))

    def warning(message, where=""):
        problems.append(Problem(message, where, error=False))

    for key, label in (("nodes", "nodes"), ("tasks_per_node", "tasks per node"),
                       ("cpus_per_task", "CPUs per task")):
        if getattr(spec, key) < 1:
            error(f"The number of {label} must be at least 1.", key)

    partition = _check_partition(spec, profile, error, warning)

    seconds = spec.time_seconds
    limit = _limit_seconds(partition.max_walltime) if partition is not None else None
    qos = spec.qos or profile.qos
    if partition is not None and partition.qos and qos:
        # The QOS the partition takes from you, each with its own time limit
        if qos not in partition.qos:
            error(f"QOS {qos!r}" + ("" if spec.qos else " (the profile's)")
                  + f" is not one {spec.partition} accepts for you (it takes "
                  f"{', '.join(partition.qos)}): Slurm refuses the job.", "qos")
        else:
            limit = _limit_seconds(partition.qos[qos])
    if not spec.time.strip():
        error("No time limit: give one (e.g. 12:00:00), or UNLIMITED where the partition "
              "allows jobs without one.", "time")
    elif spec.unlimited:
        if limit is not None:
            error(f"Jobs on {spec.partition}" + (f" with QOS {qos}" if qos and partition.qos else "")
                  + f" are limited to {_walltime_text(limit)}: give a time within it.", "time")
        else:
            warning("No time limit. Slurm starts jobs early in the gaps left by larger "
                    "ones (backfill) only when it knows when they end, so this job may wait "
                    "longer to start; a run that hangs keeps its nodes until cancelled; and "
                    "Yambo's Haydock and inversion solvers get no -walltime to stop "
                    "cleanly. A generous limit avoids all three.", "time")
    elif seconds is None:
        error(f"{spec.time!r} is not a Slurm time (e.g. 12:00:00 or 2-00:00:00).", "time")
    elif seconds <= 0:
        error("The time limit must be positive.", "time")
    elif limit is not None and seconds > limit:
        error(f"{spec.time} is above the limit for you on {spec.partition}"
              + (f" with QOS {qos}" if qos and partition.qos else "")
              + f" ({_walltime_text(limit)}).", "time")

    mb = spec.memory_mb
    if not spec.memory.strip():
        error("No memory: give the memory per node (e.g. 60G); without it Slurm gives the "
              "partition's default, which may be small.", "memory")
    elif mb is None or mb <= 0:
        error(f"{spec.memory!r} is not a memory size (e.g. 60G or 60000M).", "memory")
    elif partition is not None:
        _check_memory(spec, partition, mb, error, warning)

    if partition is not None and min(spec.tasks_per_node, spec.cpus_per_task) >= 1:
        _check_cores(spec, partition, error, warning)

    for name, target in spec.links.items():
        if not _LINK_NAME.match(name) or name in (".", ".."):
            error(f"Link {name!r}: a link is made in the step's folder, so its name has no "
                  "'/'.", "links")
        if not target.strip():
            error(f"Link {name!r} has no target.", "links")

    if not spec.runs:
        error("No runs: add at least one program run.", "runs")
    for i, run in enumerate(spec.runs, 1):
        _check_run(i, run, spec, profile, error, warning)
        problems += _check_inputs(i, run, spec, profile, folder, remote_headers or {},
                                  inputs or {})
    return problems


def _check_partition(spec, profile, error, warning) -> Partition | None:
    if not spec.partition.strip():
        error("No partition.", "partition")
        return None
    partition = profile.partitions.get(spec.partition)
    if partition is None:
        if profile.partitions:
            error(f"The {profile.name} profile has no partition {spec.partition!r} "
                  f"(it has {', '.join(profile.partitions)}).", "partition")
        else:
            warning(f"The {profile.name} profile lists no partitions, so the job cannot be "
                    "checked against them: Discover fills them in.", "partition")
    return partition


def _walltime_text(seconds: int) -> str:
    from .profile import walltime
    return walltime(seconds // 60)


def _limit_seconds(walltime: str) -> int | None:
    try:
        return parse_duration(walltime) if walltime.strip() else None
    except ValueError:
        return None


def _check_memory(spec, partition: Partition, mb: int, error, warning):
    gb = mb / 1024
    limit = partition.memory_limit_per_node_gb
    if limit and gb > limit:
        error(f"{spec.memory} per node is above what a job may ask on {spec.partition} "
              f"({limit:g} GB): Slurm refuses the job.", "memory")
        return
    smallest = partition.min_memory_per_node_gb or partition.memory_per_node_gb
    largest = partition.memory_per_node_gb
    if largest and gb > largest:
        error(f"{spec.memory} per node is more than any {spec.partition} node has "
              f"({largest:g} GB).", "memory")
    elif smallest and gb > smallest:
        # Not an error: Slurm picks nodes with enough memory
        warning(f"{spec.memory} per node is more than the smallest {spec.partition} nodes have "
                f"({smallest:g} GB): the job waits for the larger ones.", "memory")


def _check_cores(spec, partition: Partition, error, warning):
    per_node = spec.tasks_per_node * spec.cpus_per_task
    threads = partition.threads_per_core
    cores = partition.physical_cores_per_node
    if not cores:
        return
    if per_node > cores:
        what = (f"{cores} cores ({partition.cores_per_node} CPUs counting their "
                f"{threads} threads each, which job scripts leave unused)" if threads > 1
                else f"{cores} cores" + ("" if threads else
                                         " (or fewer: whether cores have several threads is "
                                         "not known; Discover finds out)"))
        error(f"{spec.tasks_per_node} tasks × {spec.cpus_per_task} CPUs = {per_node} per node, "
              f"but {spec.partition} nodes have {what}.", "tasks_per_node")
    elif spec.nodes > 1 and per_node < cores:
        warning(f"{per_node} of the {cores} cores of each node used, on {spec.nodes} nodes: "
                "fewer nodes with more tasks each is usually faster.", "tasks_per_node")


def _check_inputs(i, run: Run, spec: JobSpec, profile: ClusterProfile,
                  folder: Path | None, remote_headers: dict, inputs: dict) -> list[Problem]:
    """The input and command line of a pw.x, yambo, ypp or p2y run
    (input_checks.py) and their parallelisation (parallel.py), with the input
    read from the step's folder when given."""
    from . import input_checks, parallel
    from ..yambopy_ext.io.yambo_input import YamboInputError, read_yambo_input
    from ..yambopy_ext.qepy.pw_input import PwInputError, read_pw_input
    where = f"runs[{i}]"
    tasks = spec.tasks if run.mpi else 1
    pw_run = run.program == QE and run.executable == "pw"
    yambo_family = run.program == YAMBO and run.executable in ("yambo", "ypp", "p2y")
    if not (pw_run or yambo_family) or min(spec.nodes, spec.tasks_per_node) < 1:
        return []
    program = profile.programs.get(run.program)
    modules = program.modules if program else []
    found = []
    if yambo_family:
        found += input_checks.check_yambo_args(run.executable, run.args,
                                               parallel.yambo_version(modules), f"{where}.args")
    text = None
    if (folder is not None or run.input in inputs) and run.input and \
            (pw_run or run.executable in ("yambo", "ypp")):
        path = Path(inputs[run.input]) if run.input in inputs else Path(folder) / run.input
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            found.append(Problem(f"{run.input} is not in the step's folder, so it was not "
                                 "checked (fine if a line before the runs makes it).",
                                 f"{where}.input", error=False))
        except (OSError, UnicodeDecodeError) as e:
            found.append(Problem(f"{run.input} cannot be read ({e}).", f"{where}.input"))
    try:
        if pw_run:
            pw = read_pw_input(text) if text is not None else None
            found += parallel.check_pw(run.args, tasks, pw, spec.time_seconds, f"{where}.args")
            if pw is not None:
                version = parallel.qe_version(modules)
                found += input_checks.check_pw_input(text, pw, version, f"{where}.input")
                from . import pseudos
                files = pseudos.pseudo_files(pw, folder, profile.pseudos)
                headers = {f.remote: pseudos.read_local_header(f.local) for f in files
                           if f.local is not None}
                headers.update({f.remote: remote_headers[f.remote] for f in files
                                if f.local is None and f.remote in remote_headers})
                found += input_checks.check_pseudopotentials(
                    pw, files, headers, version, f"{where}.input",
                    pseudos.known_hints(files, folder) if folder is not None else None)
        elif run.executable == "ypp" and text is not None:
            found += input_checks.check_yambo_input(read_yambo_input(text),
                                                    parallel.yambo_version(modules),
                                                    f"{where}.input", executable="ypp")
        elif run.executable == "yambo" and text is not None:
            inp = read_yambo_input(text)
            version = parallel.yambo_version(modules)
            found += parallel.check_yambo(inp, tasks, spec.cpus_per_task, version, f"{where}.input")
            found += input_checks.check_yambo_input(inp, version, f"{where}.input")
            found += input_checks.check_for_visual_excitons(inp, f"{where}.input")
    except (PwInputError, YamboInputError) as e:
        found.append(Problem(f"{run.input} cannot be read as the program would: {e}.",
                             f"{where}.input"))
    for problem in found:
        problem.message = f"Run {i} ({run.input or run.executable}): {problem.message}"
    return found


def _check_run(i, run: Run, spec: JobSpec, profile: ClusterProfile, error, warning):
    where = f"runs[{i}]"
    name = f"Run {i}"
    program = profile.programs.get(run.program)
    if program is None:
        error(f"{name}: the {profile.name} profile has no program {run.program!r}: give its "
              "modules in the cluster editor (Programs); vwf modules "
              f"{profile.name} {run.program} lists the candidates.", f"{where}.program")
    elif not program.modules:
        warning(f"{name}: the {profile.name} profile gives no modules for {run.program}; "
                "the run uses whatever the login environment has.", f"{where}.program")
    if program is not None and run.executable not in program.executables:
        known = ", ".join(program.executables) or "none"
        error(f"{name}: {run.program} has no executable {run.executable!r} in the "
              f"{profile.name} profile (it has {known}).", f"{where}.executable")
    if run.qe_style and not run.input:
        error(f"{name}: no input file.", f"{where}.input")
    if run.yambo_style and not run.input:
        error(f"{name}: no input file (yambo -F).", f"{where}.input")
    if run.qe_style and run.input and run.output_file == run.input:
        error(f"{name}: the output would overwrite the input.", f"{where}.output")
    for k, job in enumerate(run.jobs):
        if not job.strip() or "," in job:
            error(f"{name}: job name {job!r}: empty, or with ',' (which separates names).",
                  f"{where}.jobs")
            continue
        if k > 0:
            continue                 # read only: may be anywhere (an earlier step)
        # The first is where Yambo writes: inside the step, so that its results
        # are downloaded with it, and where Visual Excitons looks
        parts = [part for part in job.split("/") if part not in ("", ".")]
        if job.startswith("/") or ".." in parts:
            error(f"{name}: job name {job!r}: Yambo writes in the first job's folder, which "
                  "must be inside the step's folder (no '..', not absolute).", f"{where}.jobs")
        elif len(parts) > VISUAL_EXCITONS_DEPTH:
            warning(f"{name}: job folder {job!r} is {len(parts)} levels deep: Visual Excitons "
                    f"looks only {VISUAL_EXCITONS_DEPTH} levels into each step's results.",
                    f"{where}.jobs")
    if run.args and re.search(r"(^|\s)(<|-inp?\b|-input\b|-F\b|-J\b)", run.args):
        warning(f"{name}: the input, Yambo's -F and -J are written by the script; give them "
                "in their fields, not in the arguments.", f"{where}.args")
    if not run.mpi and spec.tasks > 1:
        warning(f"{name} runs without MPI, on one task of the {spec.tasks}.", f"{where}.mpi")


# -- the script --------------------------------------------------------------------

HEADER = (f"# Written by {APP_NAME} from the [job] table in step.toml: change that\n"
          "# instead, or this script becomes the step's own (edits are noticed).\n")


def render(spec: JobSpec, profile: ClusterProfile, name: str, espresso_pseudo: str = "") -> str:
    """The job script. Call check() first: render() writes whatever it is given.
    `espresso_pseudo`: where pw.x looks for pseudopotentials without pseudo_dir
    (pseudos.espresso_pseudo: the project's folder or the profile's library)."""
    lines = ["#!/bin/bash", HEADER.rstrip("\n")]
    sbatch = [f"--job-name={name}"]
    if spec.partition:
        sbatch.append(f"--partition={spec.partition}")
    sbatch += [f"--nodes={spec.nodes}", f"--ntasks-per-node={spec.tasks_per_node}",
               f"--cpus-per-task={spec.cpus_per_task}"]
    partition = profile.partitions.get(spec.partition)
    if partition is not None and partition.threads_per_core > 1:
        sbatch.append("--hint=nomultithread")
    if spec.memory:
        sbatch.append(f"--mem={_sbatch_memory(spec.memory)}")
    if spec.unlimited:
        # Without --time a job gets the partition's DefaultTime, which may be short
        # even where MaxTime is unlimited; sbatch reads 0 as "no limit"
        sbatch.append("--time=0")
    elif spec.time:
        sbatch.append(f"--time={spec.time}")
    account, qos = spec.account or profile.account, spec.qos or profile.qos
    if account:
        sbatch.append(f"--account={account}")
    if qos:
        sbatch.append(f"--qos={qos}")
    lines += [f"#SBATCH {option}" for option in sbatch]
    lines.append("")

    programs = _programs(spec, profile)
    walltime = bool(spec.time_seconds) and any(_walltime(run) for run in spec.runs)
    if walltime:
        seconds = spec.time_seconds
        lines += [
            "# Yambo's -walltime from the time left when each run starts, rounded down to",
            "# hours (Yambo compares days, hours and minutes one by one); only its Haydock",
            "# and inversion solvers check it",
            f"_vwf_end=$(( $(date +%s) + {seconds - WALLTIME_MARGIN} ))",
            "_vwf_walltime() {",
            "    local s=$(( _vwf_end - $(date +%s) )) h",
            "    h=$(( s / 3600 ))",
            "    if (( h >= 24 && h % 24 )); then printf '%dd%dh' $(( h / 24 )) $(( h % 24 ))",
            "    elif (( h >= 24 )); then printf '%dd' $(( h / 24 ))",
            "    elif (( h >= 1 )); then printf '%dh' $h",
            "    elif (( s >= 120 )); then printf '%dm' $(( s / 60 ))",
            "    else printf '1m'",
            "    fi",
            "}", ""]
    lines += ["export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK"]
    if spec.cpus_per_task > 1:
        # Threads kept on their task's cores, next to each other
        lines.append("export OMP_PLACES=cores OMP_PROC_BIND=close")
    if espresso_pseudo and any(run.program == QE for run in spec.runs):
        lines.append(f'export ESPRESSO_PSEUDO="{_double_quoted(espresso_pseudo)}"')
    if any(p.mpi_launcher == "srun" for p in programs.values()):
        # srun no longer takes --cpus-per-task from the job (Slurm 22.05 and later)
        lines.append("export SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK")
    lines.append("")

    # One environment for all runs when they share it; otherwise a clean one
    # before each program, since two programs' modules may conflict
    environments = {tuple(p.modules) + tuple(p.preamble) for p in programs.values()}
    shared = len(environments) <= 1
    if shared and programs:
        lines += _environment(profile, list(programs.values())[0]) + [""]

    for link, target in spec.links.items():
        lines.append(f"ln -sfn {quote(target)} {quote(link)}")
    if spec.links:
        lines.append("")
    lines += spec.before
    if spec.before:
        lines.append("")

    current = None
    for run in spec.runs:
        program = profile.programs.get(run.program, Program())
        if not shared and run.program != current:
            if current is not None:
                lines.append("")
            lines += _environment(profile, program)
            current = run.program
        lines.append(_run_line(run, program, walltime) + " || exit $?")
    lines.append("")
    lines += spec.after
    return "\n".join(lines).rstrip("\n") + "\n"


def _double_quoted(path: str) -> str:
    """A path for "...": variables expand, ~ is written $HOME."""
    if path == "~" or path.startswith("~/"):
        path = "$HOME" + path[1:]
    return path.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")


def _sbatch_memory(text: str) -> str:
    """--mem as sbatch takes it: "60G", "60000M"; "60 GB" -> "60G"."""
    m = _MEMORY.match(text.strip())
    if not m:
        return text.strip()
    return m.group(1) + m.group(2).upper()


def _programs(spec: JobSpec, profile: ClusterProfile) -> dict[str, Program]:
    """The profile programs the runs use, in order of first use."""
    found = {}
    for run in spec.runs:
        if run.program not in found and run.program in profile.programs:
            found[run.program] = profile.programs[run.program]
    return found


def _environment(profile: ClusterProfile, program: Program) -> list[str]:
    """The lines that load a program's modules, as the module check does."""
    if not program.modules and not program.preamble:
        return []
    lines = []
    if program.modules:
        lines.append("module purge")
        lines += [f"module use {quote(d)}" for d in profile.module_use]
        lines.append("module load " + " ".join(quote(m) for m in program.modules))
    lines += program.preamble
    return lines


def _launcher(program: Program) -> str:
    options = f" {program.launcher_options}" if program.launcher_options else ""
    if program.mpi_launcher == "srun":
        return f"srun{options} "
    count = "-n" if program.mpi_launcher == "mpiexec" else "-np"
    return f"{program.mpi_launcher} {count} $SLURM_NTASKS{options} "


def _walltime(run: Run) -> bool:
    """Whether the run takes -walltime (yambo itself; ypp has no such option)."""
    return run.yambo_style and run.executable == "yambo"


def _run_line(run: Run, program: Program, walltime: bool = False) -> str:
    exe = program.executables.get(run.executable,
                                  DEFAULT_EXECUTABLES.get(run.executable, run.executable))
    words = [(_launcher(program) if run.mpi else "") + quote(exe)]
    if run.args:
        words.append(run.args)
    if run.qe_style:
        # -inp, never "< input": some MPI setups do not pass stdin on
        words.append(f"-inp {quote(run.input)}")
    elif run.yambo_style:
        words.append(f"-F {quote(run.input)}")
        if run.jobs:
            words.append("-J " + quote(",".join(run.jobs)))
        if walltime and _walltime(run):
            words.append('-walltime "$(_vwf_walltime)"')
    if run.output_file:
        words.append(f"> {quote(run.output_file)}")
    return " ".join(words)
