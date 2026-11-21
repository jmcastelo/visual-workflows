"""Checks across a chain of steps: scf → nscf → p2y → yambo (GW, BSE).

Each step with a job description is read (its pw.x inputs, p2y's -I and -O,
its yambo inputs and -J names); what each run writes and reads becomes a path
in the project ("01-scf/out/si.save"), following the steps' links (a step's
SAVE linked to "../03-p2y/SAVE"). A run reading a path is matched with the
last run writing it before it: an earlier run of the same step, or a run of a
step it comes after (`after`, directly or not). Then the rules of the chain
(docs/quantum-espresso.md §4, docs/yambo.md §1, §4, §6) are checked on the
pairs, and, for BSE runs, what Visual Excitons needs (docs/design.md, Phase 2
plan, task 5). What needs the SAVE itself (bands, k-points) is Phase 3.

Paths outside the project, or not written by any of its steps, are unknown:
no message is given about them.
"""

from __future__ import annotations

import posixpath
import shlex
from dataclasses import dataclass, field

from ..yambopy_ext.io.yambo_input import YamboInput, YamboInputError, read_yambo_input
from ..yambopy_ext.qepy.pw_input import PwInput, PwInputError, read_pw_input
from . import pseudos
from .jobspec import QE, YAMBO
from .problems import Problem

SCF_TYPES = ("scf", "relax", "md", "vc-relax", "vc-md", "ensemble")
P2Y_HYBRIDS = ("hf", "pbe0", "b3lyp", "gaupbe")      # the only hybrids p2y takes
NO_DENSITY = ("minimal", "none")                       # disk_io: no charge density written
NO_WAVEFUNCTIONS = ("nowf", "minimal", "none")         # disk_io: no wave functions written


@dataclass
class ChainRun:
    """One run of a step, with what it writes and reads in project paths."""
    step: str                        # the step's id (its folder)
    index: int                       # 1-based, in the step's job
    kind: str                        # "pw", "p2y", "yambo"
    input: str = ""
    pw: PwInput | None = None
    yambo: YamboInput | None = None
    writes: str = ""                 # what it makes: prefix.save, SAVE, the first -J folder
    reads: list[str] = field(default_factory=list)   # -I for p2y; for yambo: SAVE and -J
    jobs: list[str] = field(default_factory=list)
    outside: int = 0                 # read paths outside the project (unknown)

    @property
    def label(self) -> str:
        return f"{self.step}, run {self.index} ({self.input or self.kind})"


class Chain:
    """The runs of a project's steps with job descriptions, and how they connect."""

    def __init__(self, project):
        self.project = project
        self.steps = {s.id: s for s in project.steps}
        self.order = {s.id: i for i, s in enumerate(project.steps)}
        self.runs: list[ChainRun] = []
        for step in project.steps:
            if step.described:
                self.runs += self._read(step)

    # -- paths -----------------------------------------------------------------------

    def resolve(self, step: str, path: str) -> str | None:
        """A path as seen from a step's folder on the cluster, as a project path
        (links followed); None outside the project."""
        if not path or path.startswith(("/", "$", "~")):
            return None
        resolved = posixpath.normpath(posixpath.join(step, path))
        for _ in range(8):                       # links to links
            parts = resolved.split("/")
            if parts[0] in ("..", ".") or not parts[0]:
                return None
            owner = self.steps.get(parts[0])
            links = owner.job.links if owner is not None and owner.described else {}
            if len(parts) > 1 and parts[1] in links:
                target = posixpath.normpath(posixpath.join(parts[0], links[parts[1]]))
                resolved = posixpath.normpath("/".join([target] + parts[2:]))
                continue
            return resolved
        return resolved

    def runs_before(self, run: ChainRun) -> list[ChainRun]:
        """Runs that come before `run`: earlier ones of its step, and those of
        the steps it comes after, directly or not (in project order)."""
        before = self._ancestors(run.step)
        return [r for r in self.runs if (r.step == run.step and r.index < run.index)
                or r.step in before]

    def writer(self, run: ChainRun, path: str | None) -> tuple[ChainRun | None, bool]:
        """The run that writes `path` for `run`: the last one before it, or else
        any (then the second value is False: `run` does not come after it)."""
        if path is None:
            return None, True
        before = [r for r in self.runs_before(run) if r.writes == path]
        if before:
            return max(before, key=lambda r: (self.order[r.step], r.index)), True
        others = [r for r in self.runs if r.writes == path and r is not run
                  and not (r.step == run.step and r.index > run.index)]
        return (others[-1], False) if others else (None, True)

    def _ancestors(self, step: str) -> set[str]:
        seen, todo = set(), list(self.steps[step].after) if step in self.steps else []
        while todo:
            s = todo.pop()
            if s in seen or s not in self.steps:
                continue
            seen.add(s)
            todo += self.steps[s].after
        return seen

    # -- reading the runs ------------------------------------------------------------

    def _read(self, step) -> list[ChainRun]:
        runs = []
        for i, run in enumerate(step.job.runs, 1):
            text = None
            if run.input:
                try:
                    text = (step.folder / run.input).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    text = None
            if run.program == QE and run.executable == "pw" and text is not None:
                try:
                    pw = read_pw_input(text)
                except PwInputError:
                    continue
                outdir = pw.get("control", "outdir", "./")
                prefix = pw.get("control", "prefix", "pwscf")
                writes = self.resolve(step.id, posixpath.join(str(outdir), f"{prefix}.save"))
                runs.append(ChainRun(step.id, i, "pw", run.input, pw=pw, writes=writes or ""))
            elif run.program == YAMBO and run.executable == "p2y":
                options = _options(run.args)
                source = self.resolve(step.id, options.get("I", "."))
                target = self.resolve(step.id, posixpath.join(options.get("O", "."), "SAVE"))
                runs.append(ChainRun(step.id, i, "p2y", writes=target or "",
                                     reads=[source] if source else []))
            elif run.program == YAMBO and run.executable == "yambo" and text is not None:
                try:
                    inp = read_yambo_input(text)
                except YamboInputError:
                    continue
                options = _options(run.args)
                base = options.get("O", ".")
                save = self.resolve(step.id, posixpath.join(options.get("I", "."), "SAVE"))
                jobs = [self.resolve(step.id, posixpath.join(base, j)) for j in run.jobs]
                writes = jobs[0] if jobs else save
                reads = ([jobs[0]] if jobs else []) + [save] + jobs[1:]
                runs.append(ChainRun(step.id, i, "yambo", run.input, yambo=inp,
                                     writes=writes or "", reads=[r for r in reads if r],
                                     jobs=list(run.jobs), outside=reads.count(None)))
        return runs


def _options(args: str) -> dict[str, str]:
    """-I, -O (and their long forms) from p2y's or yambo's arguments."""
    try:
        words = shlex.split(args)
    except ValueError:
        return {}
    names = {"-I": "I", "-Idir": "I", "-O": "O", "-Odir": "O"}
    return {names[w]: words[i + 1] for i, w in enumerate(words[:-1]) if w in names}


# -- the checks -------------------------------------------------------------------------

def check_chain(project, steps=None) -> dict[str, list[Problem]]:
    """Problems across the project's chain, for the given steps (default: all),
    by step id."""
    chain = Chain(project)
    wanted = {s.id for s in steps} if steps is not None else set(chain.steps)
    found: dict[str, list[Problem]] = {}
    for run in chain.runs:
        if run.step not in wanted:
            continue
        problems = []
        if run.kind == "pw":
            problems = _check_nscf(chain, run)
        elif run.kind == "p2y":
            problems = _check_p2y(chain, run)
        elif run.kind == "yambo":
            problems = _check_yambo(chain, run)
        for p in problems:
            p.message = f"Run {run.index} ({run.input or run.kind}): {p.message}"
            p.field = p.field or f"runs[{run.index}]"
        if problems:
            found.setdefault(run.step, []).extend(problems)
    return found


def _order_problem(run: ChainRun, writer: ChainRun, what: str) -> Problem:
    return Problem(f"reads {what} from {writer.label}, but {run.step} does not come after "
                   f"{writer.step}: add it to the steps it runs after, or it may start first.",
                   error=False)


def _check_nscf(chain: Chain, run: ChainRun) -> list[Problem]:
    pw = run.pw
    calculation = pw.get("control", "calculation", "scf")
    if calculation not in ("nscf", "bands") or not run.writes:
        return []                    # outdir outside the project: unknown
    problems = []
    candidates = [r for r in chain.runs_before(run) if r.writes == run.writes and r.kind == "pw"
                  and r.pw.get("control", "calculation", "scf") in SCF_TYPES]
    others = [r for r in chain.runs if r.writes == run.writes and r.kind == "pw" and r is not run
              and r.pw.get("control", "calculation", "scf") in SCF_TYPES and r not in candidates]
    if candidates:
        scf = candidates[-1]
    elif others:
        scf = others[-1]
        problems.append(_order_problem(run, scf, "the scf's density"))
    else:
        return [Problem(f"no step makes {run.writes or 'the scf'} (same prefix and outdir) "
                        f"for this {calculation}: pw.x stops if it is missing.", error=False)]
    disk_io = scf.pw.get("control", "disk_io", "low")
    if disk_io in NO_DENSITY:
        problems.append(Problem(f"{scf.label} has disk_io = '{disk_io}', which writes no charge "
                                f"density: this {calculation} would start from atomic charges, "
                                "without a word."))
    for name, default in (("nspin", 1), ("noncolin", False)):
        a, b = scf.pw.get("system", name, default), pw.get("system", name, default)
        if a != b:
            problems.append(Problem(f"{name} = {b}, but {scf.label} has {a}: pw.x only says "
                                    "\"some spin components not found\"."))
    return problems


def _check_p2y(chain: Chain, run: ChainRun) -> list[Problem]:
    if not run.reads:
        return []
    source, ordered = chain.writer(run, run.reads[0])
    if source is None or source.kind != "pw":
        return []
    problems = [] if ordered else [_order_problem(run, source, source.writes)]
    pw = source.pw
    calculation = pw.get("control", "calculation", "scf")
    if calculation in SCF_TYPES:
        problems.append(Problem(f"p2y reads {source.label}, an {calculation}: Yambo usually "
                                "needs an nscf with empty bands (nbnd) there.", error=False))
    disk_io = pw.get("control", "disk_io", "low" if calculation in SCF_TYPES else "medium")
    if disk_io in NO_WAVEFUNCTIONS:
        problems.append(Problem(f"{source.label} has disk_io = '{disk_io}', which writes no "
                                "wave functions: p2y stops (QE format not recognised)."))
    if pw.get("system", "force_symmorphic", False) is not True and pw.get("system", "nosym", False) is not True:
        problems.append(Problem(f"{source.label} has no force_symmorphic = .true. (nor nosym): "
                                "p2y stops if the crystal has symmetries with fractional "
                                "translations. The nscf finds its own symmetries, so set it "
                                "there.", error=False))
    if "HUBBARD" in pw.cards:
        problems.append(Problem(f"{source.label} uses DFT+U (HUBBARD): p2y stops (Hubbard "
                                "correction is not considered in yambo)."))
    dft = str(pw.get("system", "input_dft", "")).lower()
    if dft and dft not in P2Y_HYBRIDS and any(h in dft for h in ("hse", "pbe0", "b3lyp", "gau", "x3lyp", "scan0", "hf")):
        problems.append(Problem(f"{source.label} uses the hybrid {dft}: p2y takes only HF, PBE0, "
                                "B3LYP and GAUPBE."))
    if not isinstance(pw.get("system", "nbnd"), int):
        problems.append(Problem(f"{source.label} does not set nbnd: an insulator then has no "
                                "empty bands for Yambo.", error=False))
    step = chain.steps[source.step]
    for f in pseudos.pseudo_files(pw, step.folder):
        if f.local is not None:
            header = pseudos.read_local_header(f.local)
            if hasattr(header, "norm_conserving") and not header.norm_conserving:
                problems.append(Problem(f"{f.name} is {header.pseudo_type}, not norm-conserving: "
                                        "p2y refuses ultrasoft and PAW pseudopotentials."))
    return problems


def _nscf_of(chain: Chain, run: ChainRun) -> tuple[ChainRun | None, ChainRun | None]:
    """The p2y run making a yambo run's SAVE, and the pw run p2y read."""
    save = run.reads[1] if run.jobs else (run.reads[0] if run.reads else None)
    p2y, _ = chain.writer(run, save)
    if p2y is None or p2y.kind != "p2y" or not p2y.reads:
        return p2y, None
    source, _ = chain.writer(p2y, p2y.reads[0])
    return p2y, (source if source is not None and source.kind == "pw" else None)


def _top_band(inp: YamboInput, name: str, column: int = 1) -> int | None:
    block = inp.block(name)
    if block is None:
        return None
    values = [row[column] for row in block.rows if len(row) > column and isinstance(row[column], int)]
    return max(values) if values else None


def _check_yambo(chain: Chain, run: ChainRun) -> list[Problem]:
    inp, problems = run.yambo, []
    save = run.reads[1] if run.jobs else (run.reads[0] if run.reads else None)
    p2y, ordered = chain.writer(run, save)
    if p2y is not None and not ordered:
        problems.append(_order_problem(run, p2y, "SAVE"))
    _, nscf = _nscf_of(chain, run)
    if nscf is not None:
        nbnd = nscf.pw.get("system", "nbnd")
        if isinstance(nbnd, int):
            for name, column, fatal, effect in (
                    ("GbndRnge", 1, True, "yambo stops, after dipoles, screening and HF "
                                           "(Too many bands required)"),
                    ("BSEBands", 1, True, "yambo does not check it"),
                    ("QPkrange", 3, False, "yambo lowers it silently"),
                    *((f"BndsRn{x}", 1, False, "yambo lowers it silently")
                      for x in ("Xx", "Xs", "Xd", "Xp", "Xm"))):
                top = _top_band(inp, name, column)
                if top is not None and top > nbnd:
                    problems.append(Problem(f"{name} reaches band {top}, but {nscf.label} has "
                                            f"nbnd = {nbnd}: {effect}.", error=fatal))
    # Steps using one SAVE at the same time may rebuild ndb.kindx together
    if save is not None:
        mine = chain._ancestors(run.step) | {run.step}
        for other in chain.runs:
            if other.kind != "yambo" or other.step == run.step:
                continue
            other_save = other.reads[1] if other.jobs else (other.reads[0] if other.reads else None)
            if other_save == save and other.step not in mine and run.step not in chain._ancestors(other.step):
                problems.append(Problem(f"{other.step} uses the same SAVE and neither step comes "
                                        "after the other: running at once, they may both rebuild "
                                        "SAVE/ndb.kindx.", error=False))
                break
    problems += _check_kernel(chain, run)
    problems += _check_qp(chain, run)
    problems += _check_for_visual_excitons(chain, run)
    return problems


def _makes_screening(inp: YamboInput) -> bool:
    return inp.has_flag("em1s") or inp.has_flag("ppa") or inp.has_flag("em1d") or inp.has_flag("mpa")


def _check_kernel(chain: Chain, run: ChainRun) -> list[Problem]:
    inp = run.yambo
    if not inp.has_flag("bse") or _makes_screening(inp):
        return []
    kernel = str(inp.get("BSKmod", "")).upper()
    if kernel not in ("SEX", "HF"):
        return []
    sources, unknown = [], run.outside > 0
    for path in run.reads:
        writer, _ = chain.writer(run, path)
        if writer is None:
            # Its own job folder may be empty yet; any other folder is unknown
            unknown = unknown or path != run.writes
        elif writer.kind == "yambo" and _makes_screening(writer.yambo):
            sources.append(writer)
    if sources or unknown:
        return []
    return [Problem(f"a {kernel} kernel needs the screening (ndb.em1s or ndb.pp), but this input "
                    "does not compute it and no step it reads (-J, SAVE) does: yambo skips the "
                    "BSE (Bethe Salpeter section skipped) and still ends normally.")]


def _qp_path(inp: YamboInput) -> str | None:
    action = inp.get("KfnQPdb")
    if not isinstance(action, str) or "<" not in action.split():
        return None
    words = action.split()
    return words[words.index("<") + 1] if words.index("<") + 1 < len(words) else None


def _check_qp(chain: Chain, run: ChainRun) -> list[Problem]:
    inp = run.yambo
    path = _qp_path(inp)
    if path is None:
        return []
    resolved = chain.resolve(run.step, path)
    if resolved is None:
        return []
    folder = posixpath.dirname(resolved)
    writer, ordered = chain.writer(run, folder)
    if writer is None:
        if chain.steps.get(resolved.split("/")[0]) is not None:
            return [Problem(f"KfnQPdb reads {path}, but no step makes it: yambo only warns and "
                            "runs the BSE on DFT energies.")]
        return []
    problems = [] if ordered else [_order_problem(run, writer, path)]
    gw = writer.yambo
    if writer.kind != "yambo" or not gw.has_flag("gw0"):
        problems.append(Problem(f"KfnQPdb reads {path}, but {writer.label} is not a GW run.",
                                error=False))
        return problems
    bse = inp.block("BSEBands")
    qp = gw.block("QPkrange")
    if bse is not None and bse.rows and qp is not None and qp.rows:
        first, last = bse.rows[0][:2]
        covered = [(row[2], row[3]) for row in qp.rows if len(row) >= 4]
        if covered and not any(lo <= first and last <= hi for lo, hi in covered):
            problems.append(Problem(f"BSEBands {first}–{last}, but {writer.label} computes QP "
                                    "corrections for bands " + ", ".join(f"{lo}–{hi}" for lo, hi in covered)
                                    + ": yambo fits the others.", error=False))
    return problems


def _check_for_visual_excitons(chain: Chain, run: ChainRun) -> list[Problem]:
    """What Visual Excitons reads from a BSE run: ndb.BS_diago_Q* with the
    excitons' eigenvectors (a diagonalising solver and WRbsWF)."""
    inp = run.yambo
    if not inp.has_flag("bss"):
        return []
    problems = []
    mode = str(inp.get("BSSmod", "")).lower()
    if mode and not any(letter in mode for letter in "ds"):
        problems.append(Problem(f"BSSmod = {inp.get('BSSmod')!r} writes no ndb.BS_diago_Q*: "
                                "Visual Excitons needs a diagonalising solver (d, or s for "
                                "SLEPc).", error=False))
    elif not inp.has_flag("WRbsWF"):
        problems.append(Problem("no WRbsWF: the excitons' eigenvectors are not written, and "
                                "Visual Excitons needs them.", error=False))
    return problems
