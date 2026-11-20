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


def find_profile(args) -> ClusterProfile:
    profiles, _ = store(args).load_all()
    name = args.cluster.lower()
    for p in profiles:
        if p.name.lower() == name or (p.path and p.path.name.split(".")[0] == name):
            return p
    known = ", ".join(p.name for p in profiles) or "none yet (vwf clusters add)"
    raise UserError(f"No cluster named {args.cluster!r}. Known: {known}")


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
        libs = check.missing_libraries.get(exe)
        print(f"  {exe}: {path or 'NOT FOUND'}"
              + (f"  (missing: {', '.join(libs)})" if libs else ""))
    if check.ok:
        print("OK: the modules load and the executables run.")
        return 0
    print("Problems:")
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
    except (UserError, ProfileError, TransportError, SchedulerError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
