"""The refresh from the cluster's yambo: the build that will run a step writes
its own input for the same runs against the step's SAVE (``yambo -F file
<options> -V all``: it writes the input and stops), and it is compared with
the step's input.

What it tells: names that build does not read (yambo ignores them without a
word), variables it writes that the input lacks (a build's own: SLEPc's, the
parallel ones of an MPI build), and the values it computes from the SAVE for
what the input leaves to it (all its G-vectors, its bands). `complete()` adds
those basic variables with yambo's values, keeping the input's own.

Where it runs is the profile's choice for yambo (`refresh`): on the login node
(seconds, no calculation; Tirant's manual sends only processes over 10
minutes of CPU to an interactive session), as a short job (`sbatch --wait`)
on the step's partition, or not at all. A SAVE not indexed yet (no
ndb.kindx) gets yambo's setup first: without it yambo writes only the setup
input.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from ..yambopy_ext.io.yambo_input import YamboInput, read_yambo_input
from ..yambopy_ext.io.yambo_runlevels import input_names
from .shell import quote

REFRESH_MODES = ("login", "job", "off")
FOLDER = ".vwf/refresh"


class RefreshError(RuntimeError):
    """The refresh cannot be done (no SAVE yet, refresh switched off, yambo failed)."""


def options_for(inp: YamboInput) -> str:
    """The yambo options that ask for the same runs as `inp`'s runlevels."""
    on = {f.lower() for f in inp.flags}
    words = []
    if "hf_and_locxc" in on:
        words.append("-x")
    if "gw0" in on:
        kind = "p" if "ppa" in on else "m" if "mpa" in on else "c" if "cohsex" in on else "r"
        words.append(f"-p {kind}")
    if "dyson" in on or "gw0" in on:
        solver = str(inp.get("DysSolver") or "n").strip()[:1].lower() or "n"
        words.append(f"-g {solver}")
    if "life" in on:
        words.append("-l")
    if "em1s" in on:
        words.append("-d s")
    elif "em1d" in on and "gw0" not in on:
        words.append("-d p" if "ppa" in on else "-d d")
    if "optics" in on:
        words.append("-o b" if "bse" in on or "bss" in on else "-o c")
    if "bse" in on or "bss" in on:
        kernel = str(inp.get("BSKmod") or "sex").strip().lower()
        words.append(f"-k {kernel}")
    if "bss" in on:
        solver = str(inp.get("BSSmod") or "h").strip()[:1].lower() or "h"
        words.append(f"-y {solver}")
    if "rim_cut" in on:
        words.append("-r")
    if not words:
        raise RefreshError("the input has no runlevel yambo would be asked for (optics, gw0...)")
    return " ".join(words)


def refresh_script(step_dir: str, save: str, environment: list[str], options: str) -> str:
    """The shell lines run on the cluster: the input yambo writes, on stdout."""
    lines = [f"mkdir -p {quote(step_dir)}/{FOLDER} && cd {quote(step_dir)}/{FOLDER} || exit 1",
             f"[ -e {quote(save)}/ns.db1 ] || {{ echo 'no SAVE at {save} (has p2y run?)' >&2; exit 3; }}",
             "rm -f ref.in r_* l_* r-* l-*",
             f"ln -sfn {quote(save)} SAVE",
             *environment,
             # the SAVE indexed once (yambo's setup) when it is not yet
             "[ -e SAVE/ndb.kindx ] || yambo >/dev/null 2>&1",
             f"yambo -F ref.in {options} -V all >/dev/null 2>&1",
             "[ -s ref.in ] || { echo 'yambo wrote no input' >&2; exit 4; }",
             "cat ref.in"]
    return "\n".join(lines)


def job_command(script: str, step_dir: str, partition: str, qos: str = "", account: str = "") -> str:
    """The script as a short job that the command waits for (`sbatch --wait`),
    its output then printed."""
    out = f"{step_dir}/{FOLDER}/job.out"
    options = [f"--partition={quote(partition)}"] if partition else []
    options += [f"--qos={quote(qos)}"] if qos else []
    options += [f"--account={quote(account)}"] if account else []
    return (f"mkdir -p {quote(step_dir)}/{FOLDER} && sbatch --wait --quiet {' '.join(options)} "
            f"--nodes=1 --ntasks=1 --time=00:10:00 --job-name=vwf-refresh "
            f"--output={quote(out)} --wrap={quote(script)}; rc=$?; cat {quote(out)}; exit $rc")


# -- comparing -------------------------------------------------------------------------

def written_names(text: str) -> list[str]:
    """The names an input yambo wrote holds, flags written commented out (off)
    included, in order."""
    names, in_block = [], False
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if in_block:
            in_block = not s.startswith("%")
            continue
        if s.startswith("#") and not re.match(r"#\w", s):
            continue
        m = re.match(r"#?(%?)\s*(\w+)", s)
        if m:
            in_block = bool(m.group(1))
            if m.group(2) not in names:
                names.append(m.group(2))
    return names


@dataclass
class Refresh:
    """What the cluster's yambo says of an input."""
    theirs: YamboInput               # the input it wrote (-V all)
    names: list                      # every name it wrote
    unknown: list = field(default_factory=list)     # ours, not read by that build
    added: list = field(default_factory=list)       # basic names it writes, not in ours
    defaults: dict = field(default_factory=dict)    # name -> its value (as written), not in ours

    def report(self) -> list[str]:
        lines = []
        if self.unknown:
            lines.append("Not read by this yambo (it ignores them without a word): "
                         + ", ".join(self.unknown) + ".")
        if self.added:
            lines.append("Written by this yambo, not in the input (its defaults apply): "
                         + ", ".join(f"{n} = {self.defaults.get(n, '…')}" for n in self.added) + ".")
        if not lines:
            lines.append("The input agrees with this yambo: every name read, nothing basic missing.")
        return lines


def compare(ours: YamboInput, text: str, version=None) -> Refresh:
    """The input `text` the cluster's yambo wrote (-V all) against `ours`."""
    theirs = read_yambo_input(text)
    written = written_names(text)
    known = {n.lower() for n in written}
    mine = list(ours.flags) + list(ours.variables) + list(ours.blocks)
    mine_lower = {n.lower() for n in mine}
    unknown = [n for n in mine if n.lower() not in known]
    basic = {str(n).lower() for n in input_names(ours.flags, {k: v.value for k, v in ours.variables.items()},
                                                 version, new_input=True, databases=["QINDX"])
             if not n.verbosity}
    added, defaults = [], {}
    for name in written:
        if name.lower() in mine_lower:
            continue
        variable, block = theirs.variable(name), theirs.block(name)
        if variable is not None:
            defaults[name] = f"{variable.value} {variable.unit}".strip()
        elif block is not None:
            defaults[name] = " | ".join(" ".join(str(x) for x in row) for row in block.rows) + \
                (f" {block.unit}" if block.unit else "")
        # basic: what yambo writes without -V (as we know it), or what we do not know at all
        if name.lower() in basic and (variable is not None or block is not None):
            added.append(name)
    return Refresh(theirs, written, unknown, added, defaults)


def complete(ours: YamboInput, refresh: Refresh) -> YamboInput:
    """`ours` with the basic variables it lacks, at the values yambo computed."""
    out = YamboInput(list(ours.flags), dict(ours.variables), dict(ours.blocks))
    for name in refresh.added:
        variable, block = refresh.theirs.variable(name), refresh.theirs.block(name)
        if variable is not None:
            out.variables[name] = variable
        elif block is not None:
            out.blocks[name] = block
    return out


# -- on the cluster ---------------------------------------------------------------------------

async def refresh_step(runner, step, run_index: int | None = None):
    """The refresh for one of a step's yambo runs (the first with an input,
    by default): (the run, our input, Refresh)."""
    from pathlib import Path
    profile = runner.profile
    program = profile.programs.get("yambo")
    if program is None:
        raise RefreshError(f"the {profile.name} profile has no program 'yambo' (its modules)")
    mode = getattr(program, "refresh", "login") or "login"
    if mode == "off":
        raise RefreshError(f"refreshing from yambo is switched off for {profile.name} "
                           "(programs.yambo.refresh)")
    runs = [(i, r) for i, r in enumerate(step.job.runs if step.job else [], 1)
            if r.program == "yambo" and r.executable == "yambo" and r.input]
    if run_index is not None:
        runs = [(i, r) for i, r in runs if i == run_index]
    if not runs:
        raise RefreshError(f"{step.id} has no yambo run with an input"
                           + (f" as run {run_index}" if run_index else ""))
    index, run = runs[0]
    text = (Path(step.folder) / run.input).read_text(encoding="utf-8")
    ours = read_yambo_input(text)
    step_dir = await runner.remote_dir(step)
    target = (step.job.links or {}).get("SAVE")
    save = posixpath.normpath(posixpath.join(step_dir, target)) if target else f"{step_dir}/SAVE"
    from .jobspec import _environment
    script = refresh_script(step_dir, save, _environment(profile, program), options_for(ours))
    if mode == "job":
        job = step.job
        command = job_command(script, step_dir, job.partition, job.qos or profile.qos,
                              job.account or profile.account)
        timeout = 3600
    else:
        command, timeout = script, 600
    r = await runner.transport.run(command, timeout=timeout)
    if not r.ok:
        raise RefreshError(f"the refresh on {profile.name} failed: "
                           f"{(r.stderr or r.stdout).strip() or f'exit {r.exit_code}'}")
    from .parallel import yambo_version
    version = yambo_version(program.modules)
    version = ".".join(str(x) for x in version) if version else None
    return run, ours, compare(ours, r.stdout, version)
