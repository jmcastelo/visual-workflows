"""Convergence studies: one yambo variable over several values, a step for
each value (a copy of a base step, its input changed in that one place), and
a result read back from each (task 1's reader) against the value.

The order to study the variables in and the tolerances are decision 7
(docs/expert-questions.md): Yambo's tutorials' order, and 0.05 eV on gaps
and exciton energies, 0.1 eV on peak positions. Studies are yambo's for
now (GW and BSE steps, which share a SAVE): a study of the k-grid or the
nscf's bands would copy the whole chain below its value.

A study is kept in project.toml (`[[studies]]`: name, base step, variable,
values, unit, steps), its steps as any other: submitted, followed and
downloaded with the rest (`vwf project submit`, `get --for-excitons`).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .project import ProjectError
from .results import ResultsError, project_results

# Band ranges given as their last band ("30") or both ("1:30")
BAND_BLOCKS = {"bndsrnxp": "BndsRnXp", "gbndrnge": "GbndRnge", "bndsrnxs": "BndsRnXs",
               "bsebands": "BSEBands", "bndsrnxd": "BndsRnXd"}
# Variables worth scanning, with the unit a value takes when the input has none
SUGGESTED = {"NGsBlkXp": "Ry", "NGsBlkXs": "Ry", "BSENGexx": "Ry", "BSENGBlk": "Ry",
             "EXXRLvcs": "Ry", "BndsRnXp": "", "GbndRnge": "", "BndsRnXs": "", "BSEBands": ""}

# What is read back from each step: (description, from a StepResults)
QUANTITIES = {
    "gw-gap": ("the GW gap on the grid (eV)",
               lambda r: r.qp.indirect[1].value if r.qp and r.qp.indirect else None),
    "gw-direct": ("the GW lowest direct transition (eV)",
                  lambda r: r.qp.direct[1].value if r.qp and r.qp.direct else None),
    "bse-lowest": ("the lowest exciton (eV)", lambda r: r.bse[0].lowest if r.bse else None),
    "bse-bright": ("the lowest bright exciton (eV)", lambda r: r.bse[0].lowest_bright if r.bse else None),
    "bse-peak": ("the strongest absorption peak (eV)",
                 lambda r: max(r.bse[0].peaks, key=lambda p: p.height).energy
                 if r.bse and r.bse[0].peaks else None),
}
# Changes this small count as converged, by quantity (decision 7): energies to
# 0.05 eV, a peak's position, which its broadening blurs, to 0.1 eV
TOLERANCES_EV = {"gw-gap": 0.05, "gw-direct": 0.05, "bse-lowest": 0.05, "bse-bright": 0.05, "bse-peak": 0.1}
TOLERANCE_EV = 0.05                  # for a quantity not listed

# The order to converge the variables in (decision 7, Yambo's tutorials): each
# study made with the variables before it already converged. (variable, why)
ORDER = {
    "gw": [
        ("EXXRLvcs", "the exchange's G-vectors (Hartree–Fock): cheap, converged first"),
        ("NGsBlkXp", "the screening's size; its bands (BndsRnXp) go with it: raise one, "
                     "then check the other again"),
        ("BndsRnXp", "the screening's bands, with NGsBlkXp"),
        ("GbndRnge", "the self-energy's bands: the slowest to converge; the terminator "
                     "(GTermKind = \"BG\") makes it faster"),
    ],
    "bse": [
        ("k-grid", "the k-grid first, the BSE's largest effect: the nscf's grid, a new chain per "
                   "grid (not a study)"),
        ("BSENGexx", "the exchange's G-vectors"),
        ("NGsBlkXs", "the static screening's size (BSENGBlk follows it); its bands BndsRnXs with it"),
        ("BSEBands", "the bands: enough for the excitons you look at, whole degenerate sets at "
                     "the gap's edges (vwf project bands --fit)"),
    ],
}


class StudyError(ProjectError):
    pass


@dataclass
class Study:
    name: str
    base: str                        # the step copied
    variable: str                    # as yambo spells it
    values: list                     # as given: "3", "1:30"
    unit: str = ""
    steps: list = field(default_factory=list)

    def to_table(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v != ""}


def tolerance_of(quantity: str) -> float:
    return TOLERANCES_EV.get(quantity, TOLERANCE_EV)


def kind_of(step) -> str | None:
    """"gw" or "bse" for a GW or BSE step (by its yambo run's runlevels)."""
    from ..yambopy_ext.io.yambo_input import read_yambo_input
    try:
        _, run = _yambo_run(step)
        flags = {f.lower() for f in read_yambo_input(
            (step.folder / run.input).read_text(encoding="utf-8")).flags}
    except (StudyError, OSError, ValueError):
        return None
    if "gw0" in flags:
        return "gw"
    if flags & {"bse", "bss"}:
        return "bse"
    return None


def next_in_order(project, base: str) -> tuple[str, str] | None:
    """The first variable of the recommended order not studied yet from
    `base` (or a copy of it), as (variable, why); None when all are."""
    step = project.step(base)
    kind = kind_of(step)
    if kind is None:
        return None
    done = {s.variable.lower() for s in studies(project) if s.base == base or base in s.steps}
    for variable, why in ORDER[kind]:
        if variable != "k-grid" and variable.lower() not in done:
            return variable, why
    return None


def order_lines(kind: str) -> list[str]:
    return [f"  {i}. {v}: {why}" for i, (v, why) in enumerate(ORDER[kind], 1)]


def studies(project) -> list[Study]:
    return [Study(**{k: d[k] for k in ("name", "base", "variable", "values", "unit", "steps") if k in d})
            for d in project._raw.get("studies", [])]


def study(project, name: str) -> Study:
    for s in studies(project):
        if s.name == name:
            return s
    raise StudyError(f"No study {name!r} in {project.name} (studies: "
                     f"{', '.join(s.name for s in studies(project)) or 'none'})")


# -- changing an input in one place ------------------------------------------------------

def _number(text: str):
    value = float(text)
    return int(value) if value.is_integer() and "." not in text else value


def set_in_text(text: str, name: str, value: str, unit: str = "") -> str:
    """The input `text` with `name` set to `value`, its other lines (yambo's
    descriptions) as they were. A band range block takes "LAST" (its first
    row's last band) or "FIRST:LAST"; a variable a number, its unit kept
    (else `unit`). Added at the end when the input lacks it."""
    lines = text.splitlines()
    lower = name.lower()
    if lower in BAND_BLOCKS:
        parts = re.split(r"[:\-]", value.strip()) if re.fullmatch(r"\s*\d+\s*[:\-]\s*\d+\s*", value) else [value]
        for i, line in enumerate(lines):
            m = re.match(r"\s*%\s*(\w+)", line)
            if m and m.group(1).lower() == lower and i + 1 < len(lines):
                row = lines[i + 1]
                cells = [c.strip() for c in row.split("#", 1)[0].split("|")]
                cells = [c for c in cells if c]
                if len(parts) == 2:
                    cells[:2] = [str(int(parts[0])), str(int(parts[1]))]
                else:
                    cells[-1] = str(int(parts[0]))
                lines[i + 1] = " " + " | ".join(cells) + " |"
                return "\n".join(lines) + "\n"
        first, last = (parts if len(parts) == 2 else ("1", parts[0]))
        return "\n".join(lines + [f"% {BAND_BLOCKS[lower]}", f" {int(first)} | {int(last)} |", "%"]) + "\n"
    number = _number(value)
    for i, line in enumerate(lines):
        m = re.match(r"(\s*)(\w+)(\s*=\s*)([^#]*?)(\s*)(#.*)?$", line)
        if m and m.group(2).lower() == lower:
            old = m.group(4).split()
            kept = old[1] if len(old) > 1 else unit
            new = f"{m.group(1)}{m.group(2)}{m.group(3)}{f'{number} {kept}'.strip()}"
            if m.group(6):                           # yambo's description, kept in its column
                column = len(line) - len(m.group(6))
                new = new + " " * max(1, column - len(new)) + m.group(6)
            lines[i] = new
            return "\n".join(lines) + "\n"
    return "\n".join(lines + [f"{name}= {f'{number} {unit}'.strip()}"]) + "\n"


# -- making a study ----------------------------------------------------------------------

def _yambo_run(step):
    for i, run in enumerate(step.job.runs if step.job else [], 1):
        if run.program == "yambo" and run.executable == "yambo" and run.input:
            return i, run
    raise StudyError(f"{step.id} has no yambo run with an input to change (a GW or BSE step)")


def _label(value: str) -> str:
    return re.sub(r"[^\w.]+", "-", value.strip()).strip("-")


def add_study(project, base: str, variable: str, values: list[str], name: str | None = None,
              unit: str | None = None, profile=None, version=None) -> Study:
    """A study: a step per value, copied from `base` with `variable` set."""
    from ..yambopy_ext.io.yambo_input import read_yambo_input
    from ..yambopy_ext.io.yambo_variables import canonical_name
    step = project.step(base)
    if step.job is None:
        raise StudyError(f"{step.id} runs its own script: a study copies a job description")
    _, run = _yambo_run(step)
    spelled = canonical_name(variable, version)
    if spelled is None:
        raise StudyError(f"yambo{f' {version}' if version else ''} reads no variable {variable!r}")
    values = [str(v).strip() for v in values if str(v).strip()]
    if len(values) < 2:
        raise StudyError("a study needs at least two values")
    if len(set(values)) != len(values):
        raise StudyError("the values repeat")
    is_block = spelled.lower() in BAND_BLOCKS
    for v in values:
        ok = re.fullmatch(r"\d+(\s*[:\-]\s*\d+)?", v) if is_block else re.fullmatch(r"[0-9.eE+-]+", v)
        if not ok:
            raise StudyError(f"{v!r} is not a value for {spelled}"
                             + (" (a last band, or FIRST:LAST)" if is_block else " (a number)"))
        if not is_block:
            try:
                float(v)
            except ValueError:
                raise StudyError(f"{v!r} is not a number") from None
    text = (step.folder / run.input).read_text(encoding="utf-8")
    inp = read_yambo_input(text)
    if not is_block:
        old = inp.variable(spelled)
        unit = unit if unit is not None else (old.unit if old is not None and old.unit else
                                              SUGGESTED.get(spelled, ""))
    name = name or f"{step.name}-{spelled}"
    if any(s.name == name for s in studies(project)):
        raise StudyError(f"a study {name!r} exists already (--name)")
    made = []
    try:
        for v in values:
            copy = project.add_job_step(f"{name}-{_label(v)}", step.job, [step.folder / f for f in step.inputs()],
                                        after=list(step.after))
            path = copy.folder / run.input
            path.write_text(set_in_text(text, spelled, v, unit or ""), encoding="utf-8")
            if profile is not None:
                copy.write_script(profile)
            made.append(copy.id)
    except Exception:
        _remove_steps(project, made)
        raise
    s = Study(name, step.id, spelled, values, unit or "", made)
    project._raw.setdefault("studies", []).append(s.to_table())
    project.save()
    return s


def _remove_steps(project, ids):
    import shutil
    for sid in ids:
        st = project.step(sid)
        shutil.rmtree(st.folder, ignore_errors=True)
        project.steps.remove(st)
    project.save()


# -- reading it back ---------------------------------------------------------------------

@dataclass
class Point:
    value: str                       # as given
    x: float                         # for plotting: the number, or a band range's last band
    step: str
    state: str                       # its latest job's state, or "not run"
    result: float | None             # None: not downloaded, or not in its results
    change: float | None = None      # from the previous point with a result


@dataclass
class StudyResults:
    study: Study
    quantity: str
    points: list
    converged_at: str | None         # the first value after which every change is within tolerance
    tolerance: float
    errors: list


def default_quantity(results_by_step: dict) -> str:
    if any(r.bse for r in results_by_step.values()):
        return "bse-bright"
    return "gw-gap"


def _x(value: str) -> float:
    numbers = re.findall(r"[0-9.eE+-]+", value)
    try:
        return float(numbers[-1]) if numbers else float("nan")
    except ValueError:
        return float("nan")


def study_results(project, s: Study, quantity: str | None = None,
                  tolerance: float | None = None) -> StudyResults:
    """The quantity read back from each step, against the values; converged
    once every later change is within `tolerance` (default: the quantity's,
    TOLERANCES_EV)."""
    steps = [project.step(i) for i in s.steps]
    try:
        found = {r.step: r for r in project_results(project, steps)}
    except ResultsError as e:
        found, errors = {}, [str(e)]
    else:
        errors = [e for r in found.values() for e in r.errors]
    quantity = quantity or default_quantity(found)
    if quantity not in QUANTITIES:
        raise StudyError(f"no quantity {quantity!r} (one of {', '.join(QUANTITIES)})")
    read = QUANTITIES[quantity][1]
    if tolerance is None:
        tolerance = tolerance_of(quantity)
    points, previous = [], None
    for value, step in zip(s.values, steps):
        r = found.get(step.id)
        y = read(r) if r is not None else None
        state = step.latest.state.value if step.latest else "not run"
        p = Point(value, _x(value), step.id, state, y)
        if y is not None and previous is not None:
            p.change = y - previous
        if y is not None:
            previous = y
        points.append(p)
    with_result = [p for p in points if p.result is not None]
    converged = None
    for i, p in enumerate(with_result[:-1]):
        if all(abs(q.change) <= tolerance for q in with_result[i + 1:]):
            converged = p.value
            break
    return StudyResults(s, quantity, points, converged, tolerance, errors)


def report(sr: StudyResults) -> list[str]:
    s = sr.study
    unit = f" ({s.unit})" if s.unit else ""
    lines = [f"{s.name}: {s.variable}{unit} over {', '.join(s.values)}, from {s.base}; "
             f"{QUANTITIES[sr.quantity][0]}"]
    lines.append(f"  {'value':>8}  {'step':<28} {'state':<12} {'result':>9} {'change':>8}")
    for p in sr.points:
        result = f"{p.result:9.3f}" if p.result is not None else "        -"
        change = f"{p.change:+8.3f}" if p.change is not None else ""
        lines.append(f"  {p.value:>8}  {p.step:<28} {p.state:<12} {result} {change}")
    have = sum(p.result is not None for p in sr.points)
    if have < 2:
        lines.append("  Not enough results yet: submit the steps, then vwf project get --for-excitons.")
    elif sr.converged_at is not None:
        lines.append(f"  Changes within {sr.tolerance:g} eV from {s.variable} = {sr.converged_at} on: "
                     "converged there.")
    else:
        lines.append(f"  The changes stay above {sr.tolerance:g} eV: not converged over these values.")
    lines += [f"  Not read: {e}" for e in sr.errors]
    return lines
