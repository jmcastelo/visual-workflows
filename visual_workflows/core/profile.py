"""Cluster profiles: everything needed to use one cluster, in a TOML file.

A profile holds no secrets. It can be sent to colleagues: export it without
the personal fields (username, key file) and they import it and fill in
their own. See docs/design.md §7 for the format.

Rules that keep shared files working across versions:
- the file says what it is in its `format` key ("cluster-profile/<n>"),
  never with the program's name;
- keys this version does not know are kept when the profile is saved again;
- a newer format version still loads, with a warning.
"""

from __future__ import annotations

import copy
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

from ..identity import CLUSTER_PROFILE_SUFFIX, DEFAULT_REMOTE_WORK_ROOT, FORMAT_CLUSTER_PROFILE
from .ssh import AUTH_METHODS, ConnectionSettings

FORMAT_KIND, _, _version = FORMAT_CLUSTER_PROFILE.partition("/")
FORMAT_VERSION = int(_version)

SCHEDULERS = ("slurm",)
MPI_LAUNCHERS = ("srun", "mpirun", "mpiexec")

# Slurm time limits: minutes, MM:SS, HH:MM:SS, D-HH, D-HH:MM, D-HH:MM:SS
_WALLTIME = re.compile(r"^(\d+-\d+(:\d+){0,2}|\d+(:\d+){0,2})$")
_UNLIMITED = ("UNLIMITED", "INFINITE")


class ProfileError(Exception):
    """The file is not a readable cluster profile."""


@dataclass
class Partition:
    """What a job script needs to know about a partition. Numbers: 0 is unknown
    (or, for the memory limit, none)."""
    max_walltime: str = ""          # Slurm format; empty: unknown
    cores_per_node: int = 0         # CPUs as Slurm counts them (hardware threads)
    memory_per_node_gb: float = 0   # the largest node's
    gpus_per_node: int = 0
    threads_per_core: int = 0       # 2: cores_per_node counts each core twice
    min_memory_per_node_gb: float = 0   # the smallest node's
    memory_limit_per_node_gb: float = 0  # the most a job may ask per node (MaxMemPerNode)
    # The QOS you may use here, with the time limit of each ("" for none);
    # empty where the cluster keeps no accounting (or before Discover)
    qos: dict = field(default_factory=dict)
    nodes: list = field(default_factory=list)   # its nodes' names (Discover); empty: unknown

    @property
    def physical_cores_per_node(self) -> int:
        """Cores without counting hardware threads (0: unknown)."""
        return self.cores_per_node // (self.threads_per_core or 1) if self.cores_per_node else 0

    @property
    def usable_memory_per_node_gb(self) -> float:
        """The most a job can ask per node on any of the partition's nodes (0: unknown)."""
        node = self.min_memory_per_node_gb or self.memory_per_node_gb
        if self.memory_limit_per_node_gb and node:
            return min(node, self.memory_limit_per_node_gb)
        return self.memory_limit_per_node_gb or node


def walltime(minutes: int | None) -> str:
    """A Slurm time limit from minutes ("" for unlimited or unknown)."""
    if minutes is None:
        return ""
    d, rest = divmod(minutes, 1440)
    h, m = divmod(rest, 60)
    return f"{d}-{h:02d}:{m:02d}:00" if d else f"{h:02d}:{m:02d}:00"


def _gb(mb: int | None) -> float:
    """Gigabytes to 0.1, rounded down: a job asking for that much still fits."""
    return (mb * 10 // 1024) / 10 if mb else 0


def discovered_partition(partition, access, nodes) -> Partition:
    """A profile partition from what the scheduler says: the scheduler's
    partition, the user's access to it (time limit from their QOS) and the
    cluster's nodes (scheduler.Partition, scheduler.Access, [scheduler.Node])."""
    mine = [n for n in nodes if partition.name in n.partitions]
    gpus = [int(m.group(1)) for n in mine
            for m in [re.search(r"gpu[^:]*:(?:[^:]*:)?(\d+)", n.gres)] if m]
    memory = [n.memory_mb for n in mine if n.memory_mb]
    return Partition(
        max_walltime=walltime(access.max_time_min),
        cores_per_node=max((n.cpus for n in mine), default=0),
        memory_per_node_gb=_gb(max(memory, default=0)),
        gpus_per_node=max(gpus, default=0),
        # the most threads any node has: job scripts then ask for whole cores
        threads_per_core=max((n.threads_per_core for n in mine), default=0),
        min_memory_per_node_gb=_gb(min(memory, default=0)),
        memory_limit_per_node_gb=_gb(partition.max_mem_per_node_mb),
        qos={name: walltime(minutes) for name, minutes in access.qos_limits.items()},
        nodes=list(dict.fromkeys(getattr(partition, "nodes", None) or [n.name for n in mine])))


@dataclass
class Program:
    modules: list[str] = field(default_factory=list)
    preamble: list[str] = field(default_factory=list)   # shell lines after the modules
    mpi_launcher: str = "srun"
    launcher_options: str = ""      # e.g. "--mpi=pmi2" (Lynx, Intel MPI with srun)
    executables: dict[str, str] = field(default_factory=dict)
    # yambo only: where its inputs are refreshed from the cluster's own yambo
    # ("login" node, a short "job", or "off"; core/yambo_refresh.py)
    refresh: str = "login"


@dataclass
class ClusterProfile:
    name: str
    connection: ConnectionSettings
    description: str = ""
    python: str = "python3"         # the cluster's Python (for scripts run there)
    scheduler: str = "slurm"
    account: str = ""
    qos: str = ""
    work_root: str = DEFAULT_REMOTE_WORK_ROOT
    scratch: str = ""
    pseudos: str = ""               # a library of pseudopotentials on the cluster
    # Module directories to add (`module use`) before listing or loading
    # modules, e.g. Spack's; written into every job script
    module_use: list[str] = field(default_factory=list)
    partitions: dict[str, Partition] = field(default_factory=dict)
    programs: dict[str, Program] = field(default_factory=dict)
    # Where it was loaded from or last saved to
    path: Path | None = field(default=None, compare=False)
    # Warnings from loading (e.g. a newer format version)
    warnings: list[str] = field(default_factory=list, compare=False)
    # The file as read, so unknown keys survive a save
    _raw: dict = field(default_factory=dict, compare=False, repr=False)

    # -- checks -------------------------------------------------------------

    def validate(self) -> list[str]:
        """Problems that would stop the profile from working; empty if none."""
        problems = []
        c = self.connection
        if not self.name.strip():
            problems.append("The cluster has no name.")
        if not c.host.strip():
            problems.append("No host to connect to.")
        if c.port is not None and not 1 <= c.port <= 65535:
            problems.append(f"Port {c.port} is not between 1 and 65535.")
        if c.auth not in AUTH_METHODS:
            problems.append(f"Unknown login method {c.auth!r} (one of {', '.join(AUTH_METHODS)}).")
        if c.connect_timeout <= 0:
            problems.append("The connection timeout must be positive.")
        if self.scheduler not in SCHEDULERS:
            problems.append(f"Unsupported scheduler {self.scheduler!r} (supported: {', '.join(SCHEDULERS)}).")
        if not self.work_root.strip():
            problems.append("No folder for projects on the cluster (work_root).")
        for d in self.module_use:
            if not d.strip():
                problems.append("An empty module directory in modules.use.")
        for name, p in self.partitions.items():
            w = p.max_walltime.strip()
            if w and w.upper() not in _UNLIMITED and not _WALLTIME.match(w):
                problems.append(f"Partition {name}: {w!r} is not a Slurm time limit "
                                "(e.g. 3-00:00:00).")
            for label, value in (("cores per node", p.cores_per_node),
                                 ("memory per node", p.memory_per_node_gb),
                                 ("GPUs per node", p.gpus_per_node),
                                 ("threads per core", p.threads_per_core),
                                 ("smallest node's memory", p.min_memory_per_node_gb),
                                 ("memory limit per node", p.memory_limit_per_node_gb)):
                if value < 0:
                    problems.append(f"Partition {name}: negative {label}.")
        for name, prog in self.programs.items():
            if prog.mpi_launcher not in MPI_LAUNCHERS:
                problems.append(f"Program {name}: unknown MPI launcher {prog.mpi_launcher!r} "
                                f"(one of {', '.join(MPI_LAUNCHERS)}).")
            for key, exe in prog.executables.items():
                if not exe.strip():
                    problems.append(f"Program {name}: no executable given for {key}.")
            if prog.refresh not in ("login", "job", "off"):
                problems.append(f"Program {name}: refresh {prog.refresh!r} is not login, job or off.")
        return problems

    # -- TOML ---------------------------------------------------------------

    @classmethod
    def from_toml(cls, text: str, path: Path | None = None) -> "ClusterProfile":
        where = str(path) if path else "the profile"
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise ProfileError(f"{where} is not valid TOML: {e}") from None
        return cls.from_dict(data, path)

    @classmethod
    def from_dict(cls, data: dict, path: Path | None = None) -> "ClusterProfile":
        where = str(path) if path else "the profile"
        warnings = []
        fmt = data.get("format")
        if not isinstance(fmt, str) or "/" not in fmt:
            raise ProfileError(f"{where} is not a cluster profile (no format key).")
        kind, _, version = fmt.partition("/")
        if kind != FORMAT_KIND:
            raise ProfileError(f"{where} is a {kind!r} file, not a cluster profile.")
        if not version.isdigit():
            raise ProfileError(f"{where}: unreadable format version {version!r}.")
        if int(version) > FORMAT_VERSION:
            warnings.append(f"{where} was written by a newer version (format {fmt}); "
                            "settings this version does not know are kept but not used.")

        r = TableReader(data, where)
        cluster = r.table("cluster")
        conn = r.table("connection")
        sched = r.table("scheduler")
        paths = r.table("paths")
        modules = r.table("modules")
        port = r.get(conn, "connection.port", int, None)
        connection = ConnectionSettings(
            host=r.get(conn, "connection.host", str, ""),
            port=port if port else None,
            username=r.get(conn, "connection.username", str, ""),
            auth=r.get(conn, "connection.auth", str, "key"),
            key_file=r.get(conn, "connection.key_file", str, ""),
            connect_timeout=float(r.get(conn, "connection.connect_timeout", (int, float), 30)),
        )
        partitions = {}
        for name, t in r.table("partitions").items():
            t = r.as_table(t, f"partitions.{name}")
            partitions[name] = Partition(
                max_walltime=r.get(t, f"partitions.{name}.max_walltime", str, ""),
                cores_per_node=r.get(t, f"partitions.{name}.cores_per_node", int, 0),
                memory_per_node_gb=float(r.get(t, f"partitions.{name}.memory_per_node_gb",
                                               (int, float), 0)),
                gpus_per_node=r.get(t, f"partitions.{name}.gpus_per_node", int, 0),
                threads_per_core=r.get(t, f"partitions.{name}.threads_per_core", int, 0),
                min_memory_per_node_gb=float(r.get(
                    t, f"partitions.{name}.min_memory_per_node_gb", (int, float), 0)),
                memory_limit_per_node_gb=float(r.get(
                    t, f"partitions.{name}.memory_limit_per_node_gb", (int, float), 0)),
                qos=r.string_table(t, f"partitions.{name}.qos"),
                nodes=r.strings(t, f"partitions.{name}.nodes"),
            )
        programs = {}
        for name, t in r.table("programs").items():
            t = r.as_table(t, f"programs.{name}")
            programs[name] = Program(
                modules=r.strings(t, f"programs.{name}.modules"),
                preamble=r.strings(t, f"programs.{name}.preamble"),
                mpi_launcher=r.get(t, f"programs.{name}.mpi_launcher", str, "srun"),
                launcher_options=r.get(t, f"programs.{name}.launcher_options", str, ""),
                executables=r.string_table(t, f"programs.{name}.executables"),
                refresh=r.get(t, f"programs.{name}.refresh", str, "login"),
            )
        return cls(
            name=r.get(cluster, "cluster.name", str, ""),
            description=r.get(cluster, "cluster.description", str, ""),
            connection=connection,
            python=r.get(conn, "connection.python", str, "python3"),
            scheduler=r.get(sched, "scheduler.type", str, "slurm"),
            account=r.get(sched, "scheduler.account", str, ""),
            qos=r.get(sched, "scheduler.qos", str, ""),
            work_root=r.get(paths, "paths.work_root", str, DEFAULT_REMOTE_WORK_ROOT),
            scratch=r.get(paths, "paths.scratch", str, ""),
            pseudos=r.get(paths, "paths.pseudos", str, ""),
            module_use=r.strings(modules, "modules.use"),
            partitions=partitions,
            programs=programs,
            path=path,
            warnings=warnings,
            _raw=copy.deepcopy(data),
        )

    def to_dict(self, *, personal: bool = True) -> dict:
        """The profile as TOML data; personal=False leaves out username and key file."""
        data = copy.deepcopy(self._raw)
        # A newer format is kept as it was: this version only adds what it knows
        if not _newer(data.get("format")):
            data["format"] = FORMAT_CLUSTER_PROFILE
        c = self.connection
        _update(data, "cluster", name=self.name, description=self.description)
        conn = _update(data, "connection", host=c.host, port=c.port, auth=c.auth,
                       username=c.username if personal else "",
                       key_file=c.key_file if personal else "",
                       connect_timeout=c.connect_timeout, python=self.python)
        if c.port is None:
            conn.pop("port", None)
        _update(data, "scheduler", type=self.scheduler, account=self.account, qos=self.qos)
        _update(data, "paths", work_root=self.work_root, scratch=self.scratch, pseudos=self.pseudos)
        _update(data, "modules", use=list(self.module_use))
        data["partitions"] = {
            name: {**_old(self._raw, "partitions", name),
                   "max_walltime": p.max_walltime, "cores_per_node": p.cores_per_node,
                   "memory_per_node_gb": p.memory_per_node_gb, "gpus_per_node": p.gpus_per_node,
                   "threads_per_core": p.threads_per_core,
                   "min_memory_per_node_gb": p.min_memory_per_node_gb,
                   "memory_limit_per_node_gb": p.memory_limit_per_node_gb,
                   "qos": dict(p.qos), "nodes": list(p.nodes)}
            for name, p in self.partitions.items()}
        data["programs"] = {
            name: {**_old(self._raw, "programs", name),
                   "modules": list(p.modules), "preamble": list(p.preamble),
                   "mpi_launcher": p.mpi_launcher, "launcher_options": p.launcher_options,
                   "executables": dict(p.executables),
                   **({"refresh": p.refresh} if p.refresh != "login" else {})}
            for name, p in self.programs.items()}
        # format first, for whoever opens the file
        return {"format": data.pop("format"), **data}

    def to_toml(self, *, personal: bool = True) -> str:
        return tomli_w.dumps(self.to_dict(personal=personal))

    @classmethod
    def load(cls, path: str | os.PathLike) -> "ClusterProfile":
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ProfileError(f"Cannot read {path}: {e.strerror or e}") from None
        return cls.from_toml(text, path)

    def save(self, path: str | os.PathLike | None = None, *, personal: bool = True) -> Path:
        """Write the profile (atomically) and remember where."""
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("No path to save the profile to")
        _write_atomic(target, self.to_toml(personal=personal))
        if personal:
            self.path = target
        return target

    def export(self, path: str | os.PathLike) -> Path:
        """Save a copy to share: without username and key file."""
        return self.save(path, personal=False)


class TableReader:
    """Typed access to TOML data, with error messages naming the key (raised
    as `error`: ProfileError here, other file kinds pass their own)."""

    def __init__(self, data: dict, where: str, error: type[Exception] = ProfileError):
        self.data, self.where, self.error = data, where, error

    def as_table(self, value, key) -> dict:
        if not isinstance(value, dict):
            raise self.error(f"{self.where}: {key} must be a table.")
        return value

    def table(self, key) -> dict:
        return self.as_table(self.data.get(key, {}), key)

    def get(self, table, key, types, default):
        value = table.get(key.rsplit(".", 1)[-1], default)
        if value is default:
            return value
        # bool is an int in Python: accept it only where a bool is asked for
        wanted = types if isinstance(types, tuple) else (types,)
        if (isinstance(value, bool) and bool not in wanted) or not isinstance(value, types):
            raise self.error(f"{self.where}: {key} has the wrong type ({value!r}).")
        return value

    def strings(self, table, key) -> list[str]:
        value = table.get(key.rsplit(".", 1)[-1], [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise self.error(f"{self.where}: {key} must be a list of strings.")
        return list(value)

    def string_table(self, table, key) -> dict[str, str]:
        value = table.get(key.rsplit(".", 1)[-1], {})
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise self.error(f"{self.where}: {key} must be a table of strings.")
        return dict(value)


def _newer(fmt) -> bool:
    if not isinstance(fmt, str):
        return False
    kind, _, version = fmt.partition("/")
    return kind == FORMAT_KIND and version.isdigit() and int(version) > FORMAT_VERSION


def _update(data: dict, table: str, **values) -> dict:
    t = data.setdefault(table, {})
    t.update({k: v for k, v in values.items() if v is not None})
    return t


def _old(raw: dict, table: str, name: str) -> dict:
    t = raw.get(table, {}).get(name, {})
    return copy.deepcopy(t) if isinstance(t, dict) else {}


def _write_atomic(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def file_name(cluster_name: str) -> str:
    """A file name for a cluster: "Lynx (GPU)" -> "lynx-gpu.cluster.toml"."""
    slug = re.sub(r"[^a-z0-9]+", "-", cluster_name.lower()).strip("-") or "cluster"
    return slug + CLUSTER_PROFILE_SUFFIX


class ProfileStore:
    """The user's cluster profiles: one file each in a folder."""

    def __init__(self, directory: str | os.PathLike):
        self.directory = Path(directory)

    def paths(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(self.directory.glob("*" + CLUSTER_PROFILE_SUFFIX))

    def load_all(self) -> tuple[list[ClusterProfile], list[str]]:
        """All readable profiles, and an error message for each unreadable file."""
        profiles, errors = [], []
        for p in self.paths():
            try:
                profiles.append(ClusterProfile.load(p))
            except ProfileError as e:
                errors.append(str(e))
        return profiles, errors

    def new_path(self, cluster_name: str) -> Path:
        """A path for a new profile that does not overwrite another one."""
        base = file_name(cluster_name)[: -len(CLUSTER_PROFILE_SUFFIX)]
        path = self.directory / (base + CLUSTER_PROFILE_SUFFIX)
        n = 2
        while path.exists():
            path = self.directory / f"{base}-{n}{CLUSTER_PROFILE_SUFFIX}"
            n += 1
        return path

    def add(self, profile: ClusterProfile) -> Path:
        """Save a profile that is not in the store yet."""
        return profile.save(self.new_path(profile.name))

    def import_file(self, source: str | os.PathLike) -> ClusterProfile:
        """Copy a shared profile into the store; it must load to be accepted."""
        profile = ClusterProfile.load(source)
        profile.path = None
        self.add(profile)
        return profile

    def delete(self, profile: ClusterProfile) -> None:
        if profile.path and profile.path.parent == self.directory:
            profile.path.unlink(missing_ok=True)
