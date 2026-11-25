"""Why a job ended badly, from how it ended (Slurm's state, the exit code)
and the messages in its log and its runs' own outputs.

Two kinds of message are read:

- the programs' own stops, quoted whatever they say: pw.x's (`Error in
  routine <name> (<n>):` and the message on the next line, between lines of
  `%`, as QE's `errore` writes them) and yambo's and p2y's (`[ERROR] <message>`,
  after `[ERROR] STOP signal received while in <section>`, as Yambo's
  `error()` writes them, in its log and on the job's output);
- known messages of Slurm, MPI, the shell, the modules and the programs,
  each with its cause and what to change.

Messages checked in the sources: QE 7.5 (UtilXlib/error_handler.f90,
PW/src, LAXlib, Modules), Yambo 5.3.0 (src/communicate, src/memory,
src/parallel, src/common, src/stop_and_restart). Slurm's and the MPI
libraries' as they print them (the `srun` one recorded on Lynx).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .scheduler import JobState


@dataclass(frozen=True)
class Known:
    pattern: re.Pattern
    source: str                      # who writes it: slurm, mpi, shell, modules, qe, yambo, system
    cause: str
    fix: str
    rank: int = 1                    # 0: the most telling; 2: only a consequence of something else


def _k(regex, source, cause, fix, rank=1, flags=re.I):
    return Known(re.compile(regex, flags), source, cause, fix, rank)


KNOWN = [
    # -- Slurm and MPI -----------------------------------------------------------------
    _k(r"More processors requested than permitted", "slurm",
       "srun asked for more CPUs than the job was given: its tasks × CPUs per task do not fit what "
       "Slurm allocated (on nodes with 2 threads per core, Slurm may count threads as CPUs).",
       "Write the job script again (Edit step…, or vwf project submit regenerates it): since Phase 4 "
       "the program asks for whole cores. For your own script, set --cpus-per-task to threads × "
       "threads per core and export SRUN_CPUS_PER_TASK.", 0),
    _k(r"PMI server not found|PMI2?_Init.*fail|not built with SLURM'?s PMI support|"
       r"PMIx? (?:client )?(?:init|initialization) failed", "mpi",
       "The MPI library could not talk to Slurm's process manager (PMI): srun and the library "
       "disagree on the PMI flavour.",
       "Set the launcher options of the program in the cluster's profile: --mpi=pmi2 or --mpi=pmix "
       "(srun --mpi=list on the cluster shows which there are). Intel MPI may also need "
       "I_MPI_PMI_LIBRARY.", 0),
    _k(r"Unable to create step for job", "slurm",
       "srun could not start the program's tasks inside the job.",
       "The line after it says why; often the tasks, CPUs or memory asked by srun exceed the job's.", 1),
    _k(r"oom[-_]kill event|Out Of Memory|Exceeded (?:job|step) memory limit", "slurm",
       "A task used more memory than the job was given, and was killed.",
       "Ask for more memory (--mem), or spread the work: more nodes, fewer tasks per node. For "
       "yambo, its parallel roles can split the memory too.", 0),
    _k(r"Disk quota exceeded|No space left on device", "system",
       "The cluster's disk (your quota, or the file system) is full.",
       "Free space in the project's folder on the cluster (wave functions and yambo's databases are "
       "large), or ask for a larger quota.", 0),
    _k(r"error while loading shared libraries: (\S+): cannot open shared object file", "shell",
       "The program needs a library ({0}) that the loaded modules do not provide.",
       "Check the program's modules: vwf modules --load MODULES --exe PROGRAM tests them, and "
       "vwf project check --on-cluster too.", 0),
    _k(r"(?:^|\s)([\w.+-]+): command not found", "shell",
       "{0} is not on the PATH once the modules are loaded.",
       "Check the module that provides it in the cluster's profile (Clusters ▸ Programs, or "
       "vwf clusters program), and the executable's name there.", 0, re.I | re.M),
    _k(r"Unable to locate a modulefile for '([^']+)'|The following module\(s\) are unknown:\s*\"?([^\"\n]+)",
       "modules",
       "The module {0} does not exist on the cluster (any more).",
       "vwf modules lists them; set the program's modules in the cluster's profile again.", 0),
    _k(r"forrtl: severe \(174\): SIGSEGV|Segmentation fault|signal 11 \(Segmentation", "system",
       "A program crashed (segmentation fault).",
       "Often the stack: OpenMP threads need a larger OMP_STACKSIZE (e.g. export OMP_STACKSIZE=512M "
       "in the step's lines before the runs), or ulimit -s unlimited; or not enough memory. If it "
       "persists, the program's build.", 1),
    _k(r"MPI_Abort\(|MPI_ABORT was invoked|BAD TERMINATION OF ONE OF YOUR APPLICATION PROCESSES",
       "mpi",
       "A program stopped all its MPI tasks.",
       "The program's own message, before this line or in its output, says why.", 2),
    # -- pw.x ------------------------------------------------------------------------------
    _k(r"convergence NOT achieved after\s+(\d+)\s+iterations: stopping", "qe",
       "The scf did not converge in {0} iterations.",
       "Lower mixing_beta (e.g. 0.3) and raise electron_maxstep; check the structure (atoms too "
       "close), the cutoffs, and for metals the smearing.", 0),
    _k(r"Maximum CPU time exceeded", "qe",
       "pw.x reached its max_seconds and stopped cleanly before the time limit: the run is not "
       "finished.",
       "Submit again with more --time, and restart_mode = 'restart' in the input to go on from where "
       "it stopped.", 0),
    _k(r"some nodes have no k-points", "qe",
       "More k-point pools (-nk) than k-points: some tasks had nothing to do.",
       "Lower -nk to at most the irreducible k-points (vwf project check says how many).", 1),
    # -- yambo -----------------------------------------------------------------------------
    _k(r"USER STOP signal", "yambo",
       "yambo stopped at its own time limit (or a stop file): the run is not finished.",
       "Submit again with more --time: yambo reuses the databases already written.", 0),
]

# A pw.x stop, by routine (and message): the cause and what to change.
QE_ERRORS = [
    ("setup", r"too few bands", "nbnd is below the occupied bands.",
     "Raise nbnd (at least the occupied bands; more for an nscf)."),
    ("readpp", r"not found", "A pseudopotential file was not found.",
     "Check pseudo_dir (or ESPRESSO_PSEUDO) and the file names in ATOMIC_SPECIES; the project's "
     "pseudopotentials go up with vwf project submit."),
    ("iosys", r"needed files are missing", "An nscf or bands run found no scf data to start from.",
     "Run the scf first, with the same prefix and outdir (the chain links them)."),
    ("read_xml_file", r"", "pw.x could not read the scf's data-file-schema.xml.",
     "The scf did not finish, or prefix and outdir differ from the scf's."),
    ("c_bands", r"too many bands are not converged", "The diagonalisation failed for many bands.",
     "Try diagonalization = 'cg' (or 'ppcg'), and check the structure and cutoffs."),
    ("cdiaghg", r"cholesky", "The overlap matrix is not positive definite.",
     "Usually atoms too close or a wrong structure; else diagonalization = 'cg'."),
    ("pdpotrf", r"cholesky", "The overlap matrix is not positive definite.",
     "Usually atoms too close or a wrong structure; else diagonalization = 'cg'."),
    ("pzpotrf", r"cholesky", "The overlap matrix is not positive definite.",
     "Usually atoms too close or a wrong structure; else diagonalization = 'cg'."),
]

# A yambo stop, by message: the cause and what to change.
YAMBO_ERRORS = [
    (r"Impossible to define an appropriate parallel structure",
     "The parallel variables (*_CPU, *_ROLEs) do not fit the MPI tasks.",
     "Make each *_CPU product equal the tasks (vwf project check says so), or remove them and "
     "let yambo choose."),
    (r"Not enough states to (?:accomodate all electrons|converge the Fermi Level)",
     "The SAVE has too few bands.", "Raise nbnd in the nscf and run nscf and p2y again."),
    (r"Trying to overwrite variable \S+ in \S+ with wrong dimensions",
     "A database an earlier run left in the step's -J folder has other sizes than this run's "
     "(a cutoff or bands changed): yambo cannot overwrite it.",
     "Delete that database on the cluster, with its fragments (e.g. vwf run CLUSTER 'rm "
     "<remote step folder>/gw/ndb.pp*'), then submit the step again. The other databases are reused."),
    (r"Allocation of .* (?:failed|leads total memory above)",
     "yambo ran out of memory.",
     "More memory or nodes, fewer tasks per node; or smaller screening and bands (NGsBlkX*, "
     "BndsRnX*); the parallel roles can spread the memory."),
]

# Exit codes that say something by themselves (128 + a signal)
EXIT_CODES = {
    126: "a command could not be executed (permissions)",
    127: "a command was not found",
    134: "a program aborted (SIGABRT)",
    137: "a program was killed (SIGKILL: often memory, or Slurm at the time limit)",
    139: "a program crashed (SIGSEGV)",
    143: "the job was stopped (SIGTERM: the time limit or a cancel)",
}


@dataclass
class Diagnosis:
    cause: str
    fix: str
    where: str = ""                  # "job log", "run 2 (yambo)", "Slurm"
    evidence: str = ""               # the line it was read from
    rank: int = 1

    def lines(self) -> list[str]:
        out = [self.cause + (f" [{self.where}]" if self.where else "")]
        if self.evidence:
            out.append(f"  read: {self.evidence}")
        if self.fix:
            out.append(f"  what to do: {self.fix}")
        return out


def _line_of(text: str, start: int) -> str:
    begin = text.rfind("\n", 0, start) + 1
    end = text.find("\n", start)
    return text[begin:end if end >= 0 else len(text)].strip()[:200]


def qe_errors(text: str, where: str = "") -> list[Diagnosis]:
    """pw.x's (and the other QE programs') own stops."""
    out = []
    for m in re.finditer(r"Error in routine\s+(\S+)\s*\(\s*(-?\d+)\s*\):\s*\n\s*(.*)", text):
        routine, message = m.group(1), m.group(3).strip()
        cause, fix = f"QE stopped in {routine}: {message}", "QE's message says what it found wrong."
        for name, pattern, c, f in QE_ERRORS:
            if routine == name and re.search(pattern, message, re.I):
                cause, fix = f"{c} (QE, {routine}: {message})", f
                break
        out.append(Diagnosis(cause, fix, where, f"Error in routine {routine} ({m.group(2)}): {message}", 0))
    return out


def yambo_errors(text: str, where: str = "") -> list[Diagnosis]:
    """yambo's, p2y's and ypp's own stops (`[ERROR] <message>`)."""
    out, seen = [], set()
    section = ""
    for m in re.finditer(r"\[ERROR\]\s*(.*)", text):
        message = m.group(1).strip()
        s = re.match(r"STOP signal received while in\s*:?\s*(.*)", message)
        if s:
            section = s.group(1).strip()
            continue
        if not message or message in seen:
            continue
        seen.add(message)
        cause, fix = f"Yambo stopped: {message}", "Yambo's message says what it found wrong."
        for pattern, c, f in YAMBO_ERRORS:
            if re.search(pattern, message, re.I):
                cause, fix = f"{c} (Yambo: {message})", f
                break
        if section:
            cause += f", while in {section}"
        out.append(Diagnosis(cause, fix, where, f"[ERROR] {message}", 0))
    return out


def known_messages(text: str, where: str = "") -> list[Diagnosis]:
    out = []
    for k in KNOWN:
        m = k.pattern.search(text)
        if m:
            groups = [g for g in m.groups() if g] or [""]
            out.append(Diagnosis(k.cause.format(*groups), k.fix, where, _line_of(text, m.start()), k.rank))
    return out


def from_text(text: str, where: str = "") -> list[Diagnosis]:
    """Every message read in one log or output."""
    return qe_errors(text, where) + yambo_errors(text, where) + known_messages(text, where)


def from_state(state: JobState, reason: str = "", exit_code: int | None = None,
               signal: str = "") -> list[Diagnosis]:
    """What the way a job ended says by itself."""
    if state is JobState.TIMEOUT:
        return [Diagnosis("The job reached its time limit and Slurm stopped it.",
                          "Ask for more --time (within the partition's limit). pw.x runs get max_seconds "
                          "from --time so they stop cleanly and can restart (restart_mode = 'restart'); "
                          "yambo reuses the databases it wrote.", "Slurm", rank=0)]
    if state is JobState.OUT_OF_MEMORY:
        return [Diagnosis("The job used more memory than it was given, and Slurm killed it.",
                          "Ask for more memory (--mem), or spread the work over more nodes with fewer "
                          "tasks per node.", "Slurm", rank=0)]
    if state is JobState.CANCELLED:
        return [Diagnosis("The job was cancelled (by you, an administrator, or preemption).",
                          "Nothing to change in the job; submit it again.", "Slurm", rank=1)]
    if state is JobState.FAILED and "node" in reason.lower():
        return [Diagnosis(f"A node of the job failed ({reason}).",
                          "Not the job's fault: submit it again.", "Slurm", rank=0)]
    return []


def exit_code_hint(exit_code: int | None) -> Diagnosis | None:
    if exit_code in EXIT_CODES:
        return Diagnosis(f"Exit code {exit_code}: {EXIT_CODES[exit_code]}.", "", "job", rank=2)
    return None


def combine(state: JobState, reason: str, exit_code: int | None, signal: str,
            texts: list[tuple[str, str]]) -> list[Diagnosis]:
    """Everything known about how a job ended: its state, then the messages
    of its log and outputs (`texts`: (where, text)), the most telling first;
    consequences (an MPI abort) only when nothing says more."""
    found = from_state(state, reason, exit_code, signal)
    for where, text in texts:
        found += from_text(text or "", where)
    unique, seen = [], set()
    for d in found:
        key = (d.cause, d.evidence)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    # a general message on a line a more telling one already explains
    best = {}
    for d in unique:
        if d.evidence:
            best[d.evidence] = min(best.get(d.evidence, d.rank), d.rank)
    unique = [d for d in unique if not d.evidence or d.rank == best[d.evidence]]
    telling = [d for d in unique if d.rank < 2]
    if telling:
        unique = telling
    if not any(d.rank == 0 for d in unique):
        hint = exit_code_hint(exit_code)
        if hint:
            unique.append(hint)
    return sorted(unique, key=lambda d: d.rank)


def last_lines(text: str, n: int = 8) -> list[str]:
    """The last non-empty lines of a log, for when no message is known."""
    return [line.rstrip() for line in (text or "").splitlines() if line.strip()][-n:]


# -- on the cluster ----------------------------------------------------------------------

TAIL_BYTES = 64 * 1024


async def read_tail(transport, path: str, size: int = TAIL_BYTES) -> str | None:
    from .monitor import LogFollower
    try:
        return await LogFollower(transport, path, tail=size).read_new()
    except Exception:                                # noqa: BLE001 - a missing file says nothing
        return None


@dataclass
class StepDiagnosis:
    step: str
    job: str
    state: JobState
    exit_code: int | None
    findings: list
    tail: list                       # the job log's last lines, when nothing is known
    read: list                       # what was read: "job log", "run 1 (pw)"...


async def diagnose_step(runner, step) -> StepDiagnosis:
    """Read a step's latest job: its log and its runs' outputs (their ends)."""
    job = step.latest
    if job is None:
        raise ValueError(f"{step.id} has not run yet")
    folder = await runner.remote_dir(step)
    texts, read = [], []
    log = await read_tail(runner.transport, f"{folder}/{job.output}") if job.output else None
    if log is not None:
        texts.append(("job log", log))
        read.append(f"job log ({job.output})")
    silent = []                                      # yambo runs that wrote no log in this job
    started = getattr(job, "started", None)
    for i, run in enumerate(step.job.runs if step.job else [], 1):
        try:
            path = await runner.run_output(step, i)
        except Exception:                            # noqa: BLE001
            path = None
        yambo = getattr(run, "program", "") == "yambo"
        if path and yambo and not await _written_since(runner.transport, path, started):
            silent.append((i, run))                  # the newest log is an earlier job's
            continue
        if not path:
            if yambo and started is not None:
                silent.append((i, run))
            continue
        text = await read_tail(runner.transport, path)
        if text is not None:
            where = f"run {i} ({run.executable}, {path.rsplit('/', 1)[-1]})"
            texts.append((where, text))
            read.append(where)
    findings = combine(job.state, job.reason, job.exit_code, job.signal, texts)
    if silent and job.state.ended and job.state is not JobState.COMPLETED:
        findings = [await _silent_yambo(runner, step, folder, silent)] + findings
    return StepDiagnosis(step.id, job.id, job.state, job.exit_code, findings,
                         [] if any(d.rank < 2 for d in findings) else last_lines(log or ""), read)


def report(d: StepDiagnosis) -> list[str]:
    head = f"{d.step}: job {d.job} {d.state.value}" + (f", exit code {d.exit_code}" if d.exit_code else "")
    lines = [head]
    if not d.findings:
        lines.append("  No known message in what was read (" + ", ".join(d.read or ["nothing"]) + ").")
    for f in d.findings:
        lines += ["  " + x for x in f.lines()]
    if d.tail:
        lines.append("  The job log ends with:")
        lines += ["    " + x for x in d.tail]
    return lines


# -- a yambo that wrote nothing ------------------------------------------------------------

# A job stopped while yambo writes a database leaves it half-written; yambo
# (5.2.4) reads the databases of its -J folder at start-up and, on such a file,
# stops every task before writing its log or report, without a word. Files
# written within this many seconds of the stop are suspects.
HALF_WRITTEN_S = 120
LAST_WRITTEN_S = 10                  # the last files written: within this of the newest


async def _written_since(transport, path: str, since) -> bool:
    """Whether a file was written since `since` (a datetime; None: assume so)."""
    if since is None:
        return True
    try:
        info = await transport.stat(path)
    except Exception:                                # noqa: BLE001 - not known: assume so
        return True
    return info is None or info.mtime >= since.timestamp() - 60


async def _silent_yambo(runner, step, folder: str, runs) -> Diagnosis:
    """Why a yambo run wrote no log: the databases a killed earlier job of the
    step was writing when it stopped, if any (the last ones written in its -J
    folder, near the job's end)."""
    import posixpath
    from .chain import _options
    killed = [j for j in step.jobs[:-1] if j.ended is not None and j.state in
              (JobState.TIMEOUT, JobState.CANCELLED, JobState.FAILED, JobState.OUT_OF_MEMORY)]
    suspects = []
    for _, run in runs:
        if not run.jobs:
            continue
        where = posixpath.normpath(posixpath.join(folder, _options(run.args).get("O", "."), run.jobs[0]))
        try:
            files = await runner.transport.listdir(where)
        except Exception:                            # noqa: BLE001 - no folder: nothing left there
            continue
        databases = [f for f in files if not f.is_dir and f.name.startswith("ndb.")]
        if not databases:
            continue
        # the last ones written (the others were finished before them)
        newest = max(f.mtime for f in databases)
        last = [f for f in databases if f.mtime >= newest - LAST_WRITTEN_S]
        for j in killed:
            end = j.ended.timestamp()
            for f in last:
                if end - HALF_WRITTEN_S <= f.mtime <= end + 60:
                    suspects.append((j, posixpath.relpath(f.path, folder)))
    if suspects:
        j = suspects[-1][0]
        names = sorted({name for _, name in suspects})
        return Diagnosis(
            f"Yambo stopped before writing its log or report: it reads the databases in its -J folder "
            f"at start-up, and {', '.join(names)} {'was' if len(names) == 1 else 'were'} being written "
            f"when job {j.id} was stopped ({j.state.value}): half-written, "
            f"{'it stops' if len(names) == 1 else 'they stop'} yambo without a message.",
            f"Delete {'it' if len(names) == 1 else 'them'} on the cluster (vwf run {runner.profile.name} "
            f"'cd {folder} && rm -v {' '.join(names)}'), then submit the step again; the databases "
            "finished before are reused.", "yambo's -J folder", ", ".join(names), rank=0)
    return Diagnosis("Yambo stopped before writing its log or report (no message): the input, the SAVE, "
                     "or a database in its -J folder it could not read.",
                     "Run it once by hand in a copy of the step's folder to see its message; a database "
                     "left by an earlier, stopped job is the usual cause.", "yambo", rank=1)
