"""Parallelisation checks of pw.x and yambo runs, before they are submitted.

The rules are those of the programs' source code, read at QE 7.3.1 and 7.5
and Yambo 5.1.1 and 5.3.0 (docs/quantum-espresso.md §2, docs/yambo.md §3):
errors are what makes the program stop at startup, or ignore what was asked;
warnings are legal but wasteful, or ignored silently. The inputs are read with
yambopy_ext's readers; what needs the crystal's symmetry (the exact number of
k-points) waits for Phase 3, so automatic grids give upper bounds only.
"""

from __future__ import annotations

import math
import re
import shlex

from ..yambopy_ext.io.yambo_input import YamboInput
from ..yambopy_ext.qepy.pw_input import PwInput, PwInputError
from .problems import Problem

# -- pw.x -----------------------------------------------------------------------------

# Every spelling pw.x accepts (Modules/command_line_options.f90), and what it means
QE_OPTIONS = {
    **dict.fromkeys(("-i", "-in", "-inp", "-input"), "input"),
    **dict.fromkeys(("-ni", "-nimage", "-nimages", "-npot"), "ni"),
    **dict.fromkeys(("-nk", "-npool", "-npools"), "nk"),
    **dict.fromkeys(("-nt", "-ntg", "-ntask_groups", "-nyfft"), "nt"),
    **dict.fromkeys(("-nb", "-nband", "-nbgrp", "-nband_group"), "nb"),
    **dict.fromkeys(("-nd", "-ndiag", "-northo", "-nproc_diag", "-nproc_ortho"), "nd"),
    **dict.fromkeys(("-nh", "-nhw", "-n_howmany", "-howmany"), "nh"),
    **dict.fromkeys(("-pd", "-use_pd", "-pencil_decomposition", "-use_pencil_decomposition"), "pd"),
}
# Exchange-correlation functionals with exact exchange (input_dft), for the rules
# on pools with hybrid functionals; others are taken as not hybrid
HYBRIDS = {"pbe0", "b3lyp", "b3lyp-v1r", "x3lyp", "hse", "gaupbe", "hf", "pbesol0", "b86bx",
           "bhahlyp", "scan0", "rpbe0", "sla+pw+hse"}
_LOGICAL = re.compile(r"^\.?(t|true|f|false)\.?$", re.I)


def parse_qe_args(args: str) -> tuple[dict, list[str]]:
    """The parallel options in pw.x's command-line arguments, as pw.x reads
    them ({"nk": 4, ...}), and what pw.x would ignore (with why)."""
    try:
        words = shlex.split(args)
    except ValueError as e:
        return {}, [f"the arguments cannot be read ({e})"]
    options, ignored = {}, []
    i = 0
    while i < len(words):
        word = words[i]
        key = QE_OPTIONS.get(word)
        if key is None:
            if re.match(r"^-+[a-z_]+=", word, re.I):
                ignored.append(f"{word!r}: pw.x takes '{word.split('=')[0]} value', not "
                               "'=', and ignores it")
            else:
                ignored.append(f"{word!r} is not a pw.x option: pw.x ignores it")
            i += 1
            continue
        if i + 1 >= len(words):
            ignored.append(f"{word} has no value: pw.x ignores it")
            break
        value = words[i + 1]
        if key == "input":
            options[key] = value
        elif key == "pd":
            if not _LOGICAL.match(value):
                ignored.append(f"{word} {value}: pw.x takes a logical (.true. or .false.)")
            else:
                options[key] = value.strip(".").lower() in ("t", "true")
        else:
            try:
                options[key] = int(value)
            except ValueError:
                ignored.append(f"{word} {value}: not a whole number, pw.x ignores it")
        i += 2
    return options, ignored


def check_pw(args: str, tasks: int, pw: PwInput | None = None,
             job_seconds: int | None = None, where: str = "") -> list[Problem]:
    """Problems of a pw.x run with `tasks` MPI processes, its command-line
    arguments and, when read, its input."""
    problems: list[Problem] = []

    def error(message):
        problems.append(Problem(message, where))

    def warning(message):
        problems.append(Problem(message, where, error=False))

    options, ignored = parse_qe_args(args)
    for message in ignored:
        error(message)

    # Images: pw.x stops on any -ni above 1 (pwscf.f90: image parallelization not allowed)
    ni = options.get("ni", 1)
    if ni != 1:
        error(f"-ni {ni}: pw.x runs one image only (images are for NEB, PHonon, manypw.x).")
        return problems
    left = tasks
    nk, nb, nt, nd = (options.get(k) for k in ("nk", "nb", "nt", "nd"))
    for key, value, what in (("nk", nk, "pools"), ("nb", nb, "band groups")):
        if value is None:
            continue
        if value < 1 or value > left:
            error(f"-{key} {value}: between 1 and {left} {what} with {tasks} MPI tasks.")
            return problems
        if left % value:
            error(f"-{key} {value} does not divide the {left} MPI tasks "
                  + ("" if key == "nk" else "of a pool ") + f"into equal {what}.")
            return problems
        left //= value
    if nt is not None and (nt < 1 or left % nt):
        error(f"-nt {nt} does not divide the {left} MPI tasks of a band group.")
    nbnd = pw.get("system", "nbnd") if pw is not None else None
    if nd is not None and nd >= 1:
        side = math.isqrt(min(nd, left))
        if isinstance(nbnd, int) and side > nbnd:
            error(f"-nd {nd}: a {side}×{side} linear-algebra grid for {nbnd} bands (nbnd): "
                  "pw.x stops (Too few bands for required ndiag).")
        elif side * side != nd:
            warning(f"-nd {nd}: pw.x uses a square grid of at most the {left} MPI tasks of a "
                    f"band group, {side}×{side}" + (" (serial)" if side == 1 else "") + ".")
    if pw is not None:
        _check_pw_input(pw, nk, tasks, job_seconds, error, warning)
    return problems


def _check_pw_input(pw: PwInput, nk, tasks, job_seconds, error, warning):
    nspin = pw.get("system", "nspin", 1)
    spin = 2 if nspin == 2 and not pw.get("system", "noncolin", False) else 1
    try:
        kpoints = pw.k_points()
    except PwInputError as e:
        error(str(e))
        kpoints = None
    pools = nk or 1
    if pools > 1:
        if kpoints is not None and kpoints.kind == "gamma" and spin == 1:
            error(f"-nk {pools} with K_POINTS gamma: one k-point, no pools "
                  "(Gamma-only calculations not allowed with pools).")
        for flag in ("lberry", "lelfield", "lorbm"):
            if pw.get("control", flag, False) is True:
                error(f"-nk {pools} with {flag}: Berry phase and electric fields do not "
                      "work with pools.")
        dft = str(pw.get("system", "input_dft", "")).lower()
        if dft in HYBRIDS and pw.get("control", "tstress", False) is True:
            error(f"-nk {pools} with a hybrid functional ({dft}) and tstress: no stress with "
                  "pools for hybrid functionals.")
        if kpoints is not None and kpoints.upper_bound is not None:
            count = kpoints.upper_bound * spin
            exact = kpoints.count is not None
            if pools > count:
                if dft in HYBRIDS and exact:
                    error(f"-nk {pools} with {count} k-points: pools without k-points are not "
                          "allowed with hybrid functionals.")
                else:
                    warning(f"-nk {pools}: more pools than the {'' if exact else 'at most '}"
                            f"{count} k-points" + (" (spin up and down counted)" if spin == 2
                                                   else "")
                            + ": some pools idle, and max_seconds is not checked while the "
                            "k-points are computed.")
            elif exact and count % pools:
                warning(f"-nk {pools}: {count} k-points do not split evenly into pools "
                        "(some pools work longer).")
    if job_seconds:
        seconds = pw.get("control", "max_seconds")
        suggestion = job_seconds - max(600, job_seconds // 20)
        if seconds is None:
            warning("No max_seconds in &CONTROL: when the job's time ends, Slurm kills pw.x "
                    f"instead of letting it stop and save; about {suggestion} for a run that "
                    "starts with the job.")
        elif isinstance(seconds, (int, float)) and seconds >= job_seconds:
            warning(f"max_seconds = {seconds:g} is not below the job's time ({job_seconds} s): "
                    f"Slurm kills pw.x first; about {suggestion} for a run that starts with "
                    "the job.")


# -- yambo ----------------------------------------------------------------------------

# Roles of each section (src/interface/INIT_load.F); "g" in SE from 5.3
YAMBO_ROLES = {
    "DIP": ("k", "c", "v"), "X_and_IO": ("q", "g", "k", "c", "v"), "X": ("q", "g", "k", "c", "v"),
    "BS": ("k", "eh", "t"), "SE": ("q", "g", "qp", "b"), "RT": ("k", "b", "q", "qp"),
    "SLK": ("p", "d"), "NL": ("w", "k"), "BZINDX": ("k",), "PH_SE": ("q", "k", "b"),
    "OSCLL": ("k", "b"),
}
# Linear-algebra counts yambo reads; the others are never used
YAMBO_LINALG_READ = {"X_and_IO_nCPU_LinAlg_INV", "X_nCPU_LinAlg_INV", "BS_nCPU_LinAlg_DIAGO",
                     "BS_nCPU_LinAlg_INV", "SLK_nCPU_LinAlg_INV", "SE_nCPU_LinAlg_DIAGO"}
YAMBO_THREADS = ("K_Threads", "X_Threads", "DIP_Threads", "SE_Threads", "RT_Threads",
                 "NL_Threads", "OSCLL_Threads")
_YAMBO4 = re.compile(r"^(X_all_q|X_finite_q|X_q_0)_(CPU|ROLEs|nCPU_LinAlg_\w+)$", re.I)
_LINALG = re.compile(r"^(\w+)_nCPU_LinAlg_(INV|DIAGO)$", re.I)


def yambo_version(modules: list[str]) -> tuple[int, int] | None:
    """Yambo's version from the names of its modules ("yambo/5.3-double" ->
    (5, 3)); None when no name tells."""
    for module in modules:
        if "yambo" in module.lower():
            m = re.search(r"(?<![\d.])(\d+)\.(\d+)", module.split("yambo", 1)[-1])
            if m:
                return int(m.group(1)), int(m.group(2))
    return None


def _split(value) -> list[str]:
    return [w for w in re.split(r"[\s.,]+", str(value).strip()) if w]


def check_yambo(inp: YamboInput, tasks: int, threads: int,
                version: tuple[int, int] | None = None, where: str = "") -> list[Problem]:
    """Problems of a yambo run's parallel variables, with `tasks` MPI
    processes of `threads` OpenMP threads each."""
    problems: list[Problem] = []

    def error(message):
        problems.append(Problem(message, where))

    def warning(message):
        problems.append(Problem(message, where, error=False))

    for name in inp.names():
        if _YAMBO4.match(name):
            warning(f"{name} is a Yambo 4 name: Yambo 5 ignores it (X_and_IO_*, X_* now).")

    sizes = _role_sizes(inp)
    for section, valid in YAMBO_ROLES.items():
        cpu_var, roles_var = inp.variable(f"{section}_CPU"), inp.variable(f"{section}_ROLEs")
        cpus = _split(cpu_var.value) if cpu_var is not None else []
        roles = _split(roles_var.value) if roles_var is not None else []
        if not cpus and not roles:
            continue
        if tasks == 1:
            continue                 # yambo ignores them with one process
        try:
            counts = [int(c) for c in cpus]
        except ValueError:
            error(f"{section}_CPU = {cpu_var.value!r}: not whole numbers.")
            continue
        if len(counts) != len(roles):
            error(f"{section}_CPU has {len(counts)} numbers but {section}_ROLEs {len(roles)} "
                  "roles: one number per role.")
            continue
        for count in counts:
            if count > 999:
                error(f"{section}_CPU: {count} processes for a role; yambo reads at most 999.")
        for role in roles:
            if role not in valid:
                error(f"{section}_ROLEs: {role!r} is not a role of {section} (one of "
                      f"{', '.join(valid)}); yambo would give its processes no work.")
        if section == "SE" and "g" in roles:
            if version is not None and version < (5, 3):
                error(f"SE_ROLEs: 'g' needs Yambo 5.3 (the modules give {version[0]}."
                      f"{version[1]}).")
            elif version is None:
                warning("SE_ROLEs: 'g' needs Yambo 5.3 or later.")
        product = math.prod(counts)
        if product != tasks:
            warning(f"{section}_CPU multiplies to {product}, not the {tasks} MPI tasks: yambo "
                    "ignores it and uses its own distribution (saying so only in its log).")
        for role, count in zip(roles, counts):
            size = sizes.get((section, role))
            if size is not None and count > size and role != "eh":
                error(f"{section}: {count} processes for role {role!r}, which has only "
                      f"{size} elements; yambo stops (USER parallel structure does not fit).")

    for name in YAMBO_THREADS:
        value = inp.get(name)
        if isinstance(value, int) and value > 0 and value != threads:
            warning(f"{name} = {value}, but each MPI task has {threads} CPU"
                    f"{'s' if threads != 1 else ''} (0 uses them all).")

    for name in inp.names():
        m = _LINALG.match(name)
        if not m:
            continue
        canonical = next((n for n in YAMBO_LINALG_READ if n.lower() == name.lower()), None)
        if canonical is None:
            warning(f"{name}: yambo never reads it.")
            continue
        value = inp.get(name)
        if not isinstance(value, int) or value <= 0:
            continue                 # -1: automatic; 0: serial
        side = math.isqrt(value)
        if value < 4:
            warning(f"{name} = {value}: below 4 the linear algebra is serial.")
        elif side * side != value:
            warning(f"{name} = {value}: yambo uses a square grid, {side}×{side} = "
                    f"{side * side} processes.")
    return problems


def _role_sizes(inp: YamboInput) -> dict[tuple[str, str], int]:
    """Elements per role that the input itself gives: self-energy bands
    (GbndRnge) and QP states (QPkrange). Others need the SAVE (Phase 3)."""
    sizes = {}
    gbnd = inp.block("GbndRnge")
    if gbnd and gbnd.rows and len(gbnd.rows[0]) >= 2:
        first, last = gbnd.rows[0][:2]
        if isinstance(first, int) and isinstance(last, int):
            sizes[("SE", "b")] = last - first + 1
    qp = inp.block("QPkrange")
    if qp and qp.rows:
        total = 0
        for row in qp.rows:
            if len(row) >= 4 and all(isinstance(x, int) for x in row[:4]):
                k1, k2, b1, b2 = row[:4]
                total += (k2 - k1 + 1) * (b2 - b1 + 1)
        if total:
            sizes[("SE", "qp")] = total
    return sizes
