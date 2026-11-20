"""`vwf`: the command-line tool, on the same core as the GUI.

    vwf clusters add lynx --host lynx.example.org --user alice --key ~/.ssh/id_ed25519
    vwf test lynx
    vwf partitions lynx
    vwf jobs lynx
    vwf run lynx -- module -t avail
    vwf modules lynx qe --describe
    vwf modules lynx --check yambo/intel-libs yambo/gpl/5.1.1-intelmpi --exe yambo p2y
    vwf submit lynx job.sh --to calcs/WSe2      (uploads job.sh, then submits)
    vwf cancel lynx 393491

    vwf project new WSe2 --cluster lynx
    vwf project add-step scf --script scf.sh --input pw.scf.in pseudo/ -C WSe2
    vwf project add-step nscf --script nscf.sh --input pw.nscf.in --after scf -C WSe2
    vwf project submit -C WSe2            (every step not done, chained)
    vwf project status -C WSe2
    vwf project watch -C WSe2             (until every step has ended)
    vwf project log scf --follow -C WSe2
    vwf project get --for-excitons -C WSe2  (or --all, --include, --dry-run)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import shlex
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import __version__
from .core.credentials import CredentialStore
from .core.modules import PROGRAM_ALIASES, Modules, for_program, labels
from .core.paths import cluster_profiles_dir
from .core.profile import ClusterProfile, ProfileError, ProfileStore
from .core.monitor import DEFAULT_INTERVAL, LogFollower, watch
from .core.project import OUTPUTS_DIR, Project, ProjectError
from .core.transfer import choose, fetch, human, parse_size, plan, scan
from .core.runner import Runner
from .core.scheduler import JobState, SchedulerError, scheduler_for
from .core.ssh import AUTH_METHODS, ConnectionSettings, Secret, SSHTransport
from .core.transport import Transport, TransportError


class UserError(Exception):
    """A mistake in the command line: shown without a traceback."""


# -- talking to the user -------------------------------------------------------------

def _ask(prompt: str, hidden: bool = False) -> str:
    return getpass.getpass(prompt) if hidden else input(prompt)


def _yes(prompt: str) -> bool:
    return _ask(prompt + " [y/N] ").strip().lower() in ("y", "yes")


class TerminalPrompter:
    """Asks for passwords, passphrases, codes and host keys in the terminal."""

    def __init__(self, store: CredentialStore):
        self.can_remember = store.available

    async def _secret(self, prompt: str) -> Secret | None:
        try:
            value = await asyncio.to_thread(_ask, prompt, True)
        except EOFError:
            return None
        remember = self.can_remember and await asyncio.to_thread(
            _yes, "Remember it in the system keyring?")
        return Secret(value, remember)

    async def password(self, username, host, *, retry):
        return await self._secret(("Wrong password. " if retry else "")
                                  + f"Password for {username}@{host}: ")

    async def passphrase(self, key_file, *, retry):
        return await self._secret(("Wrong passphrase. " if retry else "")
                                  + f"Passphrase for {key_file}: ")

    async def keyboard_interactive(self, name, instructions, prompts):
        if name or instructions:
            print("\n".join(x for x in (name, instructions) if x))
        try:
            return [await asyncio.to_thread(_ask, text, not echo) for text, echo in prompts]
        except EOFError:
            return None

    async def accept_host_key(self, host, port, algorithm, fingerprint):
        print(f"The host key of {host} (port {port}) is not known yet:\n"
              f"  {algorithm} {fingerprint}\n"
              "Check it with the cluster's documentation or administrators.")
        return await asyncio.to_thread(_yes, "Trust it and connect?")


# -- formatting -------------------------------------------------------------------------

def table(headers: list[str], rows: list[list], out=None) -> None:
    out = out or sys.stdout
    rows = [[("" if c is None else str(c)) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    for r in [headers] + rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip(), file=out)


def minutes(m: int | None) -> str:
    if m is None:
        return "unlimited"
    d, rest = divmod(m, 1440)
    h, mm = divmod(rest, 60)
    return f"{d}-{h:02d}:{mm:02d}" if d else f"{h}:{mm:02d}"


def megabytes(mb: int | None) -> str:
    if mb is None:
        return "-"
    if mb % 1024 == 0:
        return f"{mb // 1024} GB"
    # Below 10 GB, exactly as Slurm writes it: 5000 MB is not 5 GB
    return f"{mb / 1024:.0f} GB" if mb >= 10240 else f"{mb} MB"


def when(t: datetime | None) -> str:
    if t is None:
        return "-"
    if t.tzinfo is not None:
        t = t.astimezone()          # the PC's local time
    return t.strftime("%Y-%m-%d %H:%M")


# -- profiles and connections ---------------------------------------------------------

def store(args) -> ProfileStore:
    return ProfileStore(args.profiles or cluster_profiles_dir())


def find_profile(args, cluster: str | None = None) -> ClusterProfile:
    cluster = cluster or args.cluster
    profiles, _ = store(args).load_all()
    name = cluster.lower()
    for p in profiles:
        if p.name.lower() == name or (p.path and p.path.name.split(".")[0] == name):
            return p
    known = ", ".join(p.name for p in profiles) or "none yet (vwf clusters add)"
    raise UserError(f"No cluster named {cluster!r}. Known: {known}")


def make_transport(profile: ClusterProfile) -> Transport:
    """The connection for a profile (tests replace this function)."""
    credentials = CredentialStore()
    return SSHTransport(profile.connection, TerminalPrompter(credentials), credentials)


async def _connected(profile: ClusterProfile):
    transport = make_transport(profile)
    await transport.connect()
    return transport, scheduler_for(profile.scheduler, transport)


# -- commands ---------------------------------------------------------------------------

def cmd_clusters_list(args):
    profiles, errors = store(args).load_all()
    if profiles:
        table(["NAME", "HOST", "USER", "LOGIN", "FILE"],
              [[p.name, p.connection.host, p.connection.username or "-", p.connection.auth,
                p.path.name if p.path else ""] for p in profiles])
    else:
        print("No clusters yet: add one with `vwf clusters add`.")
    for e in errors:
        print("warning:", e, file=sys.stderr)


def cmd_clusters_add(args):
    st = store(args)
    if any(p.name.lower() == args.name.lower() for p in st.load_all()[0]):
        raise UserError(f"A cluster named {args.name!r} exists already.")
    profile = ClusterProfile(
        name=args.name,
        connection=ConnectionSettings(host=args.host, port=args.port, username=args.user or "",
                                      auth=args.auth, key_file=args.key or ""))
    problems = profile.validate()
    if problems:
        raise UserError("\n".join(problems))
    path = st.add(profile)
    print(f"Added {profile.name} ({path}).\nTry it: vwf test {profile.name}")


def cmd_clusters_show(args):
    print(find_profile(args).to_toml(), end="")


def cmd_clusters_remove(args):
    profile = find_profile(args)
    store(args).delete(profile)
    print(f"Removed {profile.name}.")


def cmd_clusters_import(args):
    profile = store(args).import_file(args.file)
    for w in profile.warnings:
        print("warning:", w, file=sys.stderr)
    print(f"Imported {profile.name} ({profile.path}).")
    if not profile.connection.username:
        print("It has no username: set yours with an editor, or ~/.ssh/config.")


def cmd_clusters_export(args):
    path = find_profile(args).export(args.file)
    print(f"Exported to {path}, without your username and key file.")


async def cmd_test(args):
    profile = find_profile(args)
    transport, slurm = await _connected(profile)
    try:
        host, port, user = transport.resolved() if hasattr(transport, "resolved") else \
            (profile.connection.host, profile.connection.port, profile.connection.username)
        print(f"Connected to {host} as {user}.")
        print("Home:", await transport.home())
        version = await slurm.version()
        json_output = hasattr(slurm, "use_json") and await slurm.use_json()
        print(f"Slurm {version}" + (" (JSON output)" if json_output else ""))
        accounting = await slurm.accounting_available()
        note = getattr(slurm, "accounting_note", "")
        print("Accounting:", "yes" if accounting else "no" + (f" ({note})" if note else ""))
        print("Your groups:", " ".join(await slurm.user_groups()))
        r = await transport.run(f"{shlex.quote(profile.python)} --version")
        print("Python:", (r.stdout or r.stderr).strip() if r.ok else f"not found ({profile.python})")
        r = await transport.run("module --version 2>&1 | head -1")
        print("Modules:", r.stdout.strip() or "not found")
    finally:
        await transport.close()


async def cmd_partitions(args):
    profile = find_profile(args)
    transport, slurm = await _connected(profile)
    try:
        partitions, nodes, groups = await asyncio.gather(
            slurm.partitions(), slurm.nodes(), slurm.user_groups())
    finally:
        await transport.close()
    rows = []
    for p in partitions:
        allowed = p.allows(groups)
        if not allowed and not args.all:
            continue
        mine = [n for n in nodes if p.name in n.partitions]
        idle = sum(n.cpus_idle for n in mine)
        total = sum(n.cpus for n in mine)
        mem = sorted({n.memory_mb for n in mine})
        mem_range = (megabytes(mem[0]) if len(mem) == 1 else
                     f"{megabytes(mem[0])}-{megabytes(mem[-1])}" if mem else "-")
        default_mem = (f"{megabytes(p.default_mem_per_node_mb)}/node" if p.default_mem_per_node_mb
                       else f"{megabytes(p.default_mem_per_cpu_mb)}/cpu" if p.default_mem_per_cpu_mb
                       else "all")
        row = [p.name + ("*" if p.default else ""), len(p.nodes), f"{idle}/{total}",
               mem_range, default_mem, minutes(p.max_time_min), "up" if p.up else "DOWN"]
        if args.all:
            row.append("yes" if allowed else "no")
        rows.append(row)
    headers = ["PARTITION", "NODES", "CPUS FREE", "MEMORY/NODE", "DEFAULT MEMORY",
               "MAX TIME", "STATE"] + (["YOU"] if args.all else [])
    table(headers, rows)
    print("* default partition. CPUs as Slurm counts them (hardware threads). "
          "Default memory: what a job gets without --mem.")


async def cmd_jobs(args):
    profile = find_profile(args)
    transport, slurm = await _connected(profile)
    try:
        jobs = await slurm.jobs()
    finally:
        await transport.close()
    if not jobs:
        print("No jobs in the queue or ended in the last few minutes.")
        return
    rows = []
    for j in sorted(jobs.values(), key=lambda j: [int(x) for x in j.id.split("_")]):
        if j.exit_code is not None:
            result = f"exit {j.exit_code}"
        elif j.signal is not None:
            result = f"signal {j.signal}"
        else:
            result = ""
        rows.append([j.id, j.name, j.partition, j.state.value, j.reason, j.nodes or "-",
                     j.num_cpus, minutes(j.time_limit_min), when(j.start_time), result])
    table(["JOB", "NAME", "PARTITION", "STATE", "REASON", "NODES", "CPUS", "LIMIT",
           "STARTED", "RESULT"], rows)


async def cmd_modules(args):
    profile = find_profile(args)
    transport = make_transport(profile)
    await transport.connect()
    modules = Modules(transport, profile.module_use)
    try:
        if args.check:
            return await _check_modules(modules, args)
        system = await modules.system()
        if not system.kind:
            raise UserError("No module system found on the cluster (no `module` command).")
        available = await modules.available()
        if args.filter:
            if args.filter.lower() in PROGRAM_ALIASES:
                available = for_program(available, args.filter.lower())
            else:
                available = [m for m in available if args.filter.lower() in m.full.lower()]
        details = await modules.details([m.full for m in available]) if args.describe else {}
    finally:
        await transport.close()
    names = labels(available)
    rows = []
    for m in available:
        row = [m.program, names[m.full], "spack" if m.from_spack else "", m.full]
        if args.describe:
            d = details.get(m.full)
            row.append(d.description[:60] if d else "")
        rows.append(row)
    table(["PROGRAM", "VERSION AND BUILD", "FROM", "MODULE"]
          + (["DESCRIPTION"] if args.describe else []), rows)
    print(f"{len(rows)} modules ({system.kind} {system.version})")


async def _check_modules(modules: Modules, args) -> int:
    check = await modules.check(args.check, args.exe or [])
    print("Loaded:", " ".join(check.loaded) or "-")
    for exe, path in check.executables.items():
        print(f"  {exe}: {path or 'NOT FOUND'}")
        if check.missing_libraries.get(exe):
            print(f"    missing libraries: {', '.join(check.missing_libraries[exe])}")
        for problem in check.wrong_versions.get(exe, []):
            print(f"    wrong version: {problem}")
    if check.ok:
        print("OK: the modules load and the executables run.")
        return 0
    print("Not OK:")
    for problem in check.problems():
        print("  " + problem)
    return 1


async def cmd_run(args):
    if not args.command:
        raise UserError("Nothing to run: vwf run CLUSTER -- COMMAND")
    profile = find_profile(args)
    transport = make_transport(profile)
    await transport.connect()
    try:
        # One argument is a shell line ("module avail | grep qe"); several are
        # words, each quoted
        command = shlex.join(args.command) if len(args.command) > 1 else args.command[0]
        r = await transport.run(command, timeout=args.timeout)
    finally:
        await transport.close()
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.exit_code


async def cmd_submit(args):
    profile = find_profile(args)
    transport, slurm = await _connected(profile)
    try:
        if args.to:
            local = Path(args.script)
            if not local.is_file():
                raise UserError(f"No such file: {local}")
            remote = str(PurePosixPath(args.to) / local.name)
            await transport.put(local, remote)
            workdir, script = args.to, local.name
            print(f"Uploaded {local} to {remote}")
        else:
            p = PurePosixPath(args.script)
            workdir, script = str(p.parent), p.name
        job = await slurm.submit(script, workdir, after_ok=args.after or [])
    finally:
        await transport.close()
    print(f"Submitted job {job}")


async def cmd_cancel(args):
    profile = find_profile(args)
    transport, slurm = await _connected(profile)
    try:
        await slurm.cancel(args.job_ids)
    finally:
        await transport.close()
    print("Cancelled", " ".join(args.job_ids))


# -- projects -------------------------------------------------------------------------------

def cmd_project_new(args):
    p = Project.create(args.dir, cluster=find_profile(args, args.cluster).name, name=args.name,
                       remote_dir=args.remote_dir)
    print(f"Created project {p.name} in {p.path}, for {p.cluster} (cluster folder: {p.remote_dir})")


def cmd_project_add_step(args):
    p = Project.load(args.project)
    step = p.add_step(args.name, args.script, args.input or [], args.after or [])
    files = ", ".join(str(f) for f in step.inputs())
    print(f"Added step {step.id}: {files}"
          + (f"; after {', '.join(step.after)}" if step.after else ""))


async def _project_runner(args):
    project = Project.load(args.project)
    profile = find_profile(args, project.cluster)
    transport, scheduler = await _connected(profile)
    return project, Runner(project, profile, transport, scheduler), transport


async def cmd_project_submit(args):
    project, runner, transport = await _project_runner(args)
    try:
        steps = [project.step(k) for k in args.steps] if args.steps else None
        submitted = await runner.submit(steps)
        root = await runner.remote_root()
    finally:
        await transport.close()
    if not submitted:
        print("Nothing to submit: every step has completed or is queued.")
    for step, job_id in submitted:
        after = [d.latest.id for d in project.depends_on(step) if d.active]
        print(f"{step.id}: job {job_id}" + (f" (after {', '.join(after)})" if after else ""))
    if submitted:
        print(f"On the cluster in {root}")


async def cmd_project_status(args):
    project, runner, transport = await _project_runner(args)
    try:
        await runner.refresh()
    finally:
        await transport.close()
    table(["STEP", "JOB", "STATE", "REASON", "RESULT", "STARTED", "ENDED"],
          _status_rows(project.steps))


def _status_rows(steps) -> list[list]:
    rows = []
    for step in steps:
        j = step.latest
        if j is None:
            rows.append([step.id, "-", "not run", "", "", "", ""])
            continue
        result = (f"exit {j.exit_code}" if j.exit_code is not None
                  else f"signal {j.signal}" if j.signal is not None else "")
        rows.append([step.id, j.id, j.state.value, j.reason, result, when(j.started),
                     when(j.ended)])
    return rows


async def cmd_project_watch(args):
    project, runner, transport = await _project_runner(args)

    def show(steps):
        print(datetime.now().strftime("%H:%M:%S"))
        table(["  STEP", "JOB", "STATE", "REASON", "RESULT", "STARTED", "ENDED"],
              [["  " + r[0]] + r[1:] for r in _status_rows(steps)])
        sys.stdout.flush()

    try:
        await watch(runner, show, interval=args.interval)
    finally:
        await transport.close()
    print("Every step has ended.")


async def cmd_project_log(args):
    project, runner, transport = await _project_runner(args)
    try:
        step = project.step(args.step)
        job = step.latest
        if job is None or not job.output:
            raise UserError(f"Step {step.id} has not run yet.")
        path = f"{await runner.remote_dir(step)}/{job.output}"
        follower = LogFollower(transport, path, tail=args.bytes)
        sys.stdout.write(await follower.read_new())
        polls = 0
        while args.follow:
            await asyncio.sleep(max(args.interval, 2))
            sys.stdout.write(await follower.read_new())
            sys.stdout.flush()
            polls += 1
            # Ask Slurm about the job now and then, not at every read
            if polls * args.interval >= DEFAULT_INTERVAL:
                polls = 0
                await runner.refresh()
                if not step.active:
                    sys.stdout.write(await follower.read_new())
                    print(f"[job {job.id} {step.latest.state.value}]")
                    break
    finally:
        await transport.close()


async def cmd_project_get(args):
    project, runner, transport = await _project_runner(args)
    mode = "all" if args.all else "excitons" if args.for_excitons else "default"
    max_size = None if args.all else parse_size(args.max_size)
    try:
        steps = ([project.step(k) for k in args.steps] if args.steps
                 else [s for s in project.steps if s.jobs])
        if not steps:
            print("No step has run yet.")
        any_skipped = False
        for step in steps:
            remote = await runner.remote_dir(step)
            choices = choose(await scan(transport, remote), mode, args.include or [],
                             args.exclude or [], max_size)
            items = plan(choices, step.folder / OUTPUTS_DIR)
            todo = [i for i in items if not i.have]
            size = sum(i.file.size - i.offset for i in todo)
            print(f"{step.id}: {len(todo)} files to get ({human(size)}), "
                  f"{len(items) - len(todo)} up to date")
            skipped: dict[str, list] = {}
            for c in choices:
                if c.reason:
                    skipped.setdefault(c.reason, []).append(c.file.size)
            any_skipped = any_skipped or bool(skipped)
            for reason, sizes in skipped.items():
                print(f"  skipped, {reason}: {len(sizes)} files ({human(sum(sizes))})")
            for item in todo:
                if args.dry_run:
                    print(f"  {item.file.path} ({human(item.file.size)})"
                          + (f", resuming at {human(item.offset)}" if item.offset else ""))
                    continue
                await fetch(transport, remote, item)
                print(f"  {item.file.path} ({human(item.file.size)})")
        if steps and not args.dry_run:
            print(f"Into each step's {OUTPUTS_DIR}/ folder.")
        if any_skipped:
            print("To get skipped files: --all for everything, --include PATTERN for some.")
    finally:
        await transport.close()


async def cmd_project_cancel(args):
    project, runner, transport = await _project_runner(args)
    try:
        steps = [project.step(k) for k in args.steps] if args.steps else None
        ids = await runner.cancel(steps)
    finally:
        await transport.close()
    print("Cancelled " + " ".join(ids) if ids else "Nothing running to cancel.")


# -- argument parsing -----------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="vwf", description="Run QE and Yambo on Slurm clusters.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--profiles", type=Path, help=argparse.SUPPRESS)   # tests
    sub = ap.add_subparsers(dest="command_name", required=True)

    clusters = sub.add_parser("clusters", help="manage cluster profiles")
    csub = clusters.add_subparsers(dest="action", required=True)
    csub.add_parser("list", help="list clusters").set_defaults(func=cmd_clusters_list)
    add = csub.add_parser("add", help="add a cluster")
    add.add_argument("name")
    add.add_argument("--host", required=True, help="host name or ~/.ssh/config alias")
    add.add_argument("--port", type=int)
    add.add_argument("--user", help="username on the cluster")
    add.add_argument("--auth", choices=AUTH_METHODS, default="key", help="login method")
    add.add_argument("--key", help="private key file (default: ssh-agent and default keys)")
    add.set_defaults(func=cmd_clusters_add)
    for name, func, text in (("show", cmd_clusters_show, "show a cluster's profile"),
                             ("remove", cmd_clusters_remove, "remove a cluster")):
        c = csub.add_parser(name, help=text)
        c.add_argument("cluster")
        c.set_defaults(func=func)
    imp = csub.add_parser("import", help="import a profile file")
    imp.add_argument("file", type=Path)
    imp.set_defaults(func=cmd_clusters_import)
    exp = csub.add_parser("export", help="export a profile to share (without personal fields)")
    exp.add_argument("cluster")
    exp.add_argument("file", type=Path)
    exp.set_defaults(func=cmd_clusters_export)

    def cluster_command(name, func, text):
        c = sub.add_parser(name, help=text)
        c.add_argument("cluster")
        c.set_defaults(func=func)
        return c

    cluster_command("test", cmd_test, "connect and report what the cluster offers")
    cluster_command("partitions", cmd_partitions, "partitions you may use").add_argument(
        "--all", action="store_true", help="also partitions your groups may not use")
    cluster_command("jobs", cmd_jobs, "your jobs: queued, running, recently ended")
    run = cluster_command("run", cmd_run, "run a command on the cluster (in a login shell)")
    run.add_argument("--timeout", type=float, default=300)
    run.add_argument("command", nargs=argparse.REMAINDER)
    submit = cluster_command("submit", cmd_submit, "submit a job script")
    submit.add_argument("script", help="path on the cluster, or a local file with --to")
    submit.add_argument("--to", metavar="DIR", help="upload the local script to DIR and submit there")
    submit.add_argument("--after", nargs="+", metavar="JOB", help="start after these jobs succeed")
    mods = cluster_command("modules", cmd_modules, "list modules, or check that some load")
    mods.add_argument("filter", nargs="?", help="a program (qe, yambo) or part of a name")
    mods.add_argument("--describe", action="store_true", help="add each module's description")
    mods.add_argument("--check", nargs="+", metavar="MODULE",
                      help="load these modules (from a clean slate) and check --exe")
    mods.add_argument("--exe", nargs="+", metavar="EXECUTABLE",
                      help="executables that must be found and have all their libraries")
    project = sub.add_parser("project", help="projects: steps run in order on a cluster")
    psub = project.add_subparsers(dest="action", required=True)
    new = psub.add_parser("new", help="create a project folder")
    new.add_argument("dir", type=Path)
    new.add_argument("--cluster", required=True)
    new.add_argument("--name")
    new.add_argument("--remote-dir", help="folder on the cluster (default: the folder's name, "
                                          "under the cluster's work_root)")
    new.set_defaults(func=cmd_project_new)

    def project_command(name, func, text):
        c = psub.add_parser(name, help=text)
        c.add_argument("-C", "--project", type=Path, default=Path("."),
                       help="project folder (default: here)")
        c.set_defaults(func=func)
        return c

    add_step = project_command("add-step", cmd_project_add_step, "add a step")
    add_step.add_argument("name")
    add_step.add_argument("--script", required=True, help="the step's job script")
    add_step.add_argument("--input", nargs="+", metavar="FILE", help="input files or folders")
    add_step.add_argument("--after", nargs="+", metavar="STEP", help="steps it needs first")
    for name, func, text in (
            ("submit", cmd_project_submit, "upload and submit steps (default: all not done)"),
            ("cancel", cmd_project_cancel, "cancel steps' jobs (default: all running)")):
        project_command(name, func, text).add_argument("steps", nargs="*", metavar="STEP")
    project_command("status", cmd_project_status, "update and show the steps' jobs")
    w = project_command("watch", cmd_project_watch, "follow the steps until all have ended")
    w.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                   help="seconds between checks (at least 30)")
    log = project_command("log", cmd_project_log, "show the end of a step's output")
    log.add_argument("step")
    log.add_argument("-f", "--follow", action="store_true", help="keep showing what is added")
    log.add_argument("--bytes", type=int, default=4096, help="how much of the end to show first")
    log.add_argument("--interval", type=float, default=10, help="seconds between reads")
    get = project_command("get", cmd_project_get, "download steps' results into outputs/")
    get.add_argument("steps", nargs="*", metavar="STEP")
    which = get.add_mutually_exclusive_group()
    which.add_argument("--all", action="store_true", help="everything, wave functions included")
    which.add_argument("--for-excitons", action="store_true",
                       help="what Visual Excitons reads (SAVE/ns.db1, SAVE/ns.wf*, "
                            "ndb.BS_diago_Q*, ndb.QP*)")
    get.add_argument("--include", nargs="+", metavar="PATTERN", help="also these files")
    get.add_argument("--exclude", nargs="+", metavar="PATTERN", help="not these files")
    get.add_argument("--max-size", default="1G", help="skip larger files (default 1G)")
    get.add_argument("--dry-run", action="store_true", help="only show what would be downloaded")

    cancel = cluster_command("cancel", cmd_cancel, "cancel jobs")
    cancel.add_argument("job_ids", nargs="+", metavar="JOB")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "command", None) and args.command[:1] == ["--"]:
        args.command = args.command[1:]
    try:
        result = args.func(args)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        return result or 0
    except (UserError, ProfileError, ProjectError, TransportError, SchedulerError,
            ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
