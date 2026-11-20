# Visual Workflows — design

Status: draft, 2026-11-20. Decisions taken so far are marked **Decided**; the
rest are proposals open to change.

## 1. Goal and scope

A desktop program that makes running Quantum ESPRESSO and Yambo on clusters
easier: build the inputs with graphical tools, build the Slurm job scripts,
submit and follow the jobs, and bring the results back.

- **Decided:** QE and Yambo only for now; the design must not prevent adding
  other codes later.
- **Decided:** Slurm only for now, behind a scheduler interface so PBS or others
  can be added.
- **Decided:** our own core, not AiiDA (borrowing its ideas: separate transport,
  scheduler and calculation layers; recorded provenance).
- **Decided:** used by one researcher at a time. Cluster profiles are files that
  people share by email or otherwise.
- **Decided:** a new repository, `~/Code/visual-workflows`, part of a suite with
  Visual Excitons. The suite's name is open; see [Naming](#9-naming).
- **Decided:** Python ≥ 3.11 on the PC (`tomllib`); the cluster-side helper
  stays at Python ≥ 3.6.
- **Decided:** projects are folders of TOML files, not a database (§8).

Target clusters: **Lynx** (the group cluster: SSH with password or key,
Python 3 on the login node) first, **Tirant** later.

Not goals: a web portal; anything that needs administrator rights on a cluster;
a server running on a cluster.

## 2. Architecture

```
PC                                          Cluster (login node)
┌────────────────────────────┐   SSH   ┌───────────────────────────────────┐
│ gui      PySide6 windows   │◄───────►│ sbatch, squeue, sacct, sinfo,     │
│ cli      `vwf` commands    │ one     │ scancel, module, files (SFTP)     │
│ core     no Qt: profiles,  │ open    │ ~/.visual-workflows/helper.py:    │
│          projects, jobs,   │ session │  stdlib only, run per command,    │
│          scheduler, files  │         │  prints JSON and exits            │
└────────────────────────────┘         └───────────────────────────────────┘
```

**No service on the cluster.** Login nodes usually forbid long-running
processes and open ports, and some have no internet to install packages. Every
remote action is one command over a single SSH connection that stays open for
the whole session (one login, also when a cluster asks for a one-time code).

**Three layers.** `core` has no Qt and is fully usable from Python and tests;
`cli` is a thin command-line tool on `core` (debugging, scripting, testing on a
real cluster before any GUI exists); `gui` is PySide6 on `core`. Remote calls
are `async` (asyncssh); the GUI runs them on Qt's event loop through qasync, so
the window never freezes and a slow cluster never blocks it.

### Package layout

```
visual_workflows/
  identity.py          names, settings keys, file formats (see Naming)
  core/
    transport.py       Transport interface, errors; LocalTransport (tests, local runs)
    ssh.py             SSHTransport (asyncssh): logins, ~/.ssh/config, host keys, SFTP
    credentials.py     keyring access ("remember"), in-memory keyring for tests
    shell.py           login-shell wrapping, quoting
    paths.py           the program's folders on the PC
    helper.py          uploading and calling the remote helper
    modules.py         Environment Modules: detect Lmod/Tcl, list, test a load
    scheduler/
      base.py          Scheduler interface, Job, JobState, Partition, Node
      slurm.py         Slurm: JSON job parsing with text fallback; partitions
                       (`scontrol show partition -o`) and nodes (`sinfo -N`)
                       from text formats stable across versions
    profile.py         ClusterProfile: load, save, validate, discover
    project.py         Project, Step, Job records and their files
    jobscript.py       job script rendering from JobSpec + modules
    transfer.py        upload/download, selective, resumable
    monitor.py         polling jobs, state changes, log following
  remote/
    helper.py          the script copied to the cluster (stdlib, Python ≥ 3.6)
  cli.py
  gui/
    main.py
    ...
```

## 3. Connection

- **Library:** asyncssh (async, SFTP, keyboard-interactive, agent, jump hosts).
- **Authentication:** SSH key (with ssh-agent or a passphrase), or password.
  A key file is read by the program itself, so its passphrase is asked only if
  it is encrypted (asyncssh would ask for every key). Encrypted keys in the
  OpenSSH format (ssh-keygen's default) need `bcrypt`: asyncssh's `bcrypt`
  extra is a dependency.
  Keyboard-interactive is supported from the start so clusters with one-time
  codes (likely Tirant) work: the GUI shows the server's prompts.
- **Secrets:** never written to files. Passwords and key passphrases go to the
  OS keyring (`keyring`) only if the user ticks "remember"; otherwise they are
  asked at each connection.
- **`~/.ssh/config`:** read for host aliases, user, port, identity files and
  `ProxyJump`, so what works in a terminal works here.
- **Host keys:** checked against `~/.ssh/known_hosts`. An unknown key is shown
  with its fingerprint for the user to accept; a changed key refuses to
  connect.
- **Login shell:** commands run as `bash -lc '<command>'`, so `module`, the
  site's `PATH` and the user's `.bashrc` are set up as in a terminal.
- **Reconnection:** a dropped connection is reopened on the next action; jobs
  are unaffected (they live in Slurm, not in the connection). Commands are
  never retried automatically: a repeated `sbatch` would submit twice.
- **Tests:** an asyncssh server inside the test process (tests/sshserver.py)
  exercises every login method, host keys and SFTP for real, without Docker.

## 4. The remote helper

A single file, `visual_workflows/remote/helper.py`, copied to
`~/.visual-workflows/helper.py` on the cluster and run as
`python3 ~/.visual-workflows/helper.py <command> [args]`. It prints one JSON
object to stdout and exits.

- Standard library only, Python ≥ 3.6, no imports from the package.
- Carries a version; the GUI calls `helper.py version` and uploads the file
  again when the versions differ.
- Used only for what is awkward to do with plain commands:
  `modules` (list and parse available modules for Lmod and Tcl),
  `scan <dir>` (files with sizes and times, to plan downloads),
  `tail <file> <bytes>` (log following), `diskinfo` (quotas and free space
  when the site's tools are known).
- Every feature has a fallback without the helper where practical, so a cluster
  without a usable `python3` still works for the basics.

## 5. Scheduler

`Scheduler` interface in `core/scheduler/base.py`:

```python
class Scheduler(Protocol):
    async def partitions(self) -> list[Partition]       # limits, nodes, state
    async def nodes(self, partition=None) -> list[Node]
    async def submit(self, script_path: str, workdir: str,
                     dependency: list[str] = ()) -> str  # job id
    async def cancel(self, job_ids: list[str]) -> None
    async def states(self, job_ids: list[str]) -> dict[str, JobState]
    async def ended(self, job_ids: list[str]) -> dict[str, JobRecord]  # see §5.1
    def render(self, spec: JobSpec) -> str              # #SBATCH header
```

Slurm implementation:

| Need | Command | Notes |
|---|---|---|
| Version | `sinfo --version` | decides whether `--json` is available (Slurm ≥ 21.08) |
| Partitions, nodes | `sinfo --json`, else `sinfo -h -o <fields>` | limits: `scontrol show partition -o` |
| Allowed partitions | `id -Gn` against each partition's `AllowGroups` | Lynx limits partitions by Unix group |
| Accounts, QOS | `sacctmgr -nP show assoc user=$USER format=...` | needs accounting (not on Lynx); else from the profile |
| Submit | `sbatch --parsable [--dependency=afterok:…] script` | the id is the first field |
| Running/queued jobs | `squeue --json --jobs=…`, else `squeue -h -o` | |
| Recently ended jobs | `squeue --json --me --states=all` | kept only `MinJobAge` (300 s on Lynx); without `--states=all` ended jobs are not listed |
| One job in detail | `scontrol show job --json <id>` | one id per call: a comma list is refused |
| Finished jobs | the job's status file (§5.1); `sacct` where accounting exists | exit code, times; MaxRSS only from `sacct` |
| Cancel | `scancel id…` | |

Both parsers (JSON and text) are implemented; the text one is the fallback for
old Slurm versions and is tested against recorded outputs.

**Polling:** one `squeue` call for all of the user's active jobs, at most every
60 s (configurable, minimum 30 s); when a job leaves the queue, its status
file (§5.1) and, if still there, `scontrol show job`. Polling stops when no job is active. Admins notice heavy polling, so
manual refresh is rate-limited too.

**Job states:** `draft → submitted → pending → running → completed | failed |
cancelled | timeout | out_of_memory | unknown`. Slurm states map onto these;
the raw Slurm state and reason are kept for display.

### 5.1 Job status without accounting

Lynx has no Slurm accounting: `sacct` and `sacctmgr` fail, and Slurm forgets
an ended job after `MinJobAge` (5 minutes). A job that ends while the PC is
off would leave no trace of how it ended. So **every job records its own
status**, which works on any cluster:

- Lines added after the `#SBATCH` header (also to job scripts the user brings)
  append to `.vwf/job-$SLURM_JOB_ID.status` in the job's folder: `started`
  with time and node list, `modules` (`module -t list`), and on exit (a bash
  `trap … EXIT`, plus `TERM` for Slurm's warning before a kill) `ended` with
  the exit code or signal. Tested on Lynx: at the time limit the batch
  script receives SIGTERM (then SIGKILL after `KillWait`, 30 s), so the trap
  runs.
- Final state, in this order: Slurm's own (`squeue`, `scontrol show job`)
  while it remembers the job; else the status file: `ended` with exit 0 →
  completed, other codes → failed; `started` without `ended` → killed
  (time limit, memory, node failure; the reason is looked for in Slurm's
  output file: `*** JOB <id> ON <node> CANCELLED AT <time> DUE TO TIME
  LIMIT ***`, `… DUE to SIGNAL Terminated ***` for scancel — note the case
  — and `oom-kill`); no file → cancelled
  while pending, or unknown.
- `scripts/cluster-survey-jobs.sh` records how Slurm reports held, running,
  failed, timed-out and cancelled jobs, to test this on real outputs.

## 6. Environment Modules

- Detect the system: `module --version` / `$LMOD_VERSION` (Lmod) or
  Environment Modules (`$MODULESHOME`; Lynx: Environment Modules 5.3, no Lmod).
- List: `module -j avail` (JSON, Environment Modules ≥ 4.5) or `module -t
  avail` (terse, both systems); Lmod also `module spider`. Parsed by the
  helper, with a fallback in `core`.
- Lynx names modules by path with the build in the name
  (`qe/7.3.1-intelmpi-omp`, `yambo/gpl/5.2.4-single-omp`): the GUI groups
  them by program and shows version and build (MPI, OpenMP, precision)
  separately.
- **Test a load before submitting:** run `module purge; module load …;
  which pw.x yambo` on the login node, and report missing executables. This
  catches most "module not found" job failures in seconds instead of after
  queueing.
- **Extra module directories** (`module use`), e.g. Spack's generated
  modules on Lynx: listed in the profile (`[modules] use`), added before
  listing modules and written into every job script before the loads. Spack
  module names carry compiler and hash (`quantum-espresso/7.3.1-gcc-13.2.0-
  abc1234`, depending on the site's naming): shown as version and build,
  hash hidden. Spack's own `spack load` works through the preamble.
- **Labels:** `module show` gives each module's `module-whatis` line
  ("Quantum Espresso 7.3.1 INTELMPI OMP"), used as its description.
- **Missing libraries:** on Lynx, Yambo modules load only MKL and MPI; the
  HDF5/netCDF/PETSc/SLEPc they need come from a separate module
  (`yambo/intel-libs`). The load test also runs `ldd` on each executable
  and reports "not found" libraries, which catches a forgotten library
  module before the job runs.
- Per program, the profile stores the module lines and the executable names
  (`pw.x`, `ph.x`, `p2y`, `yambo`, `ypp`), plus an optional preamble (extra
  `export` lines, `source` of a site script) for builds that are not modules.
- Containers (Apptainer) and self-compiled binaries are expressed the same way:
  a preamble and an executable path. Not in Phase 1.
- The modules actually loaded (`module list` at job start) are written into
  the job's output, for reproducibility.

## 7. Cluster profiles

One TOML file per cluster, `<name>.cluster.toml`, kept in the user's config
directory and importable/exportable for sharing. It holds **no secrets** and
no personal data beyond what the user chooses (the username can be left empty
so the shared file is generic).

```toml
format = "cluster-profile/1"

[cluster]
name = "Lynx"
description = "Group cluster"

[connection]
host = "lynx.example.org"      # or an alias from ~/.ssh/config
port = 22
username = ""                  # empty: asked, or taken from ~/.ssh/config
auth = "key"                   # "key" | "password" | "agent" | "interactive"
key_file = "~/.ssh/id_ed25519"
python = "python3"             # for the helper

[scheduler]
type = "slurm"
account = ""                   # default --account, if the site needs one
qos = ""

[paths]
work_root = "$HOME/visual-workflows"      # where project folders go
scratch = ""                               # e.g. "/scratch/$USER" if separate

[partitions.cpu]                # discovered, then editable
max_walltime = "3-00:00:00"
cores_per_node = 64
memory_per_node_gb = 256

[modules]
use = []                        # extra module directories, e.g. Spack's

[programs.qe]
modules = ["qe/7.3.1b-intelmpi-omp"]
preamble = []
mpi_launcher = "srun"           # "srun" | "mpirun" | "mpiexec"
launcher_options = "--mpi=pmi2" # what Lynx's Intel MPI builds need with srun
executables = { pw = "pw.x", ph = "ph.x", dos = "dos.x" }

[programs.yambo]
modules = ["yambo/intel-libs", "yambo/gpl/5.1.1-intelmpi"]
mpi_launcher = "srun"
launcher_options = "--mpi=pmi2"
executables = { p2y = "p2y", yambo = "yambo", ypp = "ypp" }
```

A **"Discover"** button fills in partitions, limits and available modules
from the cluster; the user then picks and edits. Unknown keys are kept when
saving, so newer profiles still load in older versions.

## 8. Projects, steps and jobs

A **project** is a local folder with a `project.toml` and one subfolder per
**step**. A step is one calculation (scf, nscf, p2y, a Yambo run…) with its
inputs, job script, and the record of its jobs (a step can be run again).

```
LocalProjects/MoS2-bse/
  project.toml                  format, cluster, remote folder, steps and their order
  01-scf/   pw.scf.in  job.sh  step.toml  outputs/ (downloaded)
  02-nscf/  ...
```

- The remote folder mirrors the local one under the profile's `work_root`.
- `step.toml` records: inputs' hash, job script, job id(s), state, submit and
  end times, modules loaded, exit code, and which files were downloaded.
- Dependencies between steps (nscf needs scf's `.save`) are explicit in
  `project.toml`; chains are submitted with `--dependency=afterok`, or step by
  step with a check in between.
- **Decided:** TOML files, human-readable and versioning well under git. If job tracking across
  many projects needs fast queries later, a SQLite index can be added on top,
  rebuilt from the project files.

**Phase 1 has no input builders**: a step can be any job script plus input
files the user brings, which already makes the tool useful.

## 9. Naming

**Decided:** "Visual Workflows" is a working name; with Visual Excitons it
forms a suite whose umbrella name may change, and both may be renamed. To keep
that cheap:

- All user-visible names, settings keys and the keyring service are in
  `visual_workflows/identity.py`.
- Shared files carry a `format = "<kind>/<version>"` key, never the program's
  name, so profiles sent to colleagues keep loading after a rename.
- The package is laid out so it could become `<suite>.workflows`, alongside
  `<suite>.excitons`.

## 10. Testing

- `core` is tested without a cluster: a `LocalTransport` runs commands on the
  PC, and the Slurm parsers are tested on recorded `sinfo`/`squeue`/`sacct`
  outputs (JSON and text) kept in `tests/data/`.
- Integration tests run against a Slurm cluster in Docker
  (slurm-docker-cluster) with an SSH server, marked `cluster` and skipped when
  it is not running. Container memory kept small (the PC has 16 GB).
- A manual checklist for Lynx before each release.
- Tests that touch QSettings or the keyring use a temporary settings path and
  a fake keyring backend, never the user's real ones.

## 11. Phases

| Phase | Delivers |
|---|---|
| 0 | Survey Lynx (below). Record real command outputs as test data. |
| **1** | Connection, cluster profiles, remote file browser, submit/cancel/follow an existing job script, downloads. CLI first, then GUI. |
| 2 | Job script builder: partitions, modules (with load test), resources; QE/Yambo parallelisation checks. |
| 3 | QE inputs: structure import (ASE), symmetry (spglib), k grids, Brillouin-zone paths (YamboPy fork's `BrillouinZone`), pseudopotential library; scf/nscf/bands. |
| 4 | Yambo inputs: templates from the cluster's own `yambo -F`, edited in the GUI (YamboPy `YamboIn`); chains scf → nscf → p2y → GW → BSE. |
| 5 | Convergence sweeps and plots, energy-range tools (gap, DOS, band indices), error detection in outputs, hand-off to Visual Excitons. |

### Phase 0: cluster surveys

`scripts/cluster-survey.sh` (static facts) and `scripts/cluster-survey-jobs.sh`
(four tiny test jobs) are run on a cluster's login node; their outputs, made
anonymous, become test data in `tests/data/<cluster>/`.

**Lynx (2026-11-20):**

- Arch Linux; Slurm 26.05.4, JSON output works (`data_parser/v0.0.45`).
- **No accounting:** `sacct`/`sacctmgr` unavailable; `MinJobAge` 300 s → §5.1.
- Partitions `total` (default), `magma`, `novas`, `arqus`: no time limits,
  access by Unix group (`AllowGroups`), no GPUs.
- Nodes: 96 CPUs = 2 sockets × 24 cores × **2 threads**; Slurm counts
  hardware threads (`CR_CPU_MEMORY`), so a whole node is 48 physical cores.
  Memory 122–380 GB per node.
- **Default memory 5000 MB per node** (`DefMemPerNode`): a job that does not
  ask for memory gets 5 GB, too little for QE/Yambo. The job script builder
  always writes `--mem` (or `--mem-per-cpu`).
- `MpiDefault` unset: the group's job scripts launch with `srun --mpi=pmi2`.
- QE modules load their own libraries; Yambo modules need `yambo/intel-libs`
  (or `yambo/gnu-libs`) as well. Spack modules exist after `module use`.
- Environment Modules 5.3.1 (not Lmod), 14 QE and 31 Yambo modules; QE and
  Yambo are not in PATH without them.
- Python 3.14; rsync, tar, zstd available; no `quota`; home on a 2 TB
  ZFS volume; `/scratch` exists.

### Phase 1 tasks

1. `identity.py`, `core/transport.py`: `Transport` interface; `SSHTransport`
   (connect with key, agent or password; `~/.ssh/config`; known hosts;
   `run(cmd) -> (code, out, err)` in a login shell; SFTP get/put/listdir/stat);
   `LocalTransport` for tests.
2. `core/credentials.py`: keyring with a "remember" option.
3. `core/profile.py`: load/save/validate cluster profiles (TOML, `format` key,
   unknown keys kept).
4. `core/scheduler/slurm.py`: version detection; partitions (with group
   access), submit, cancel, states, ended jobs (status files, §5.1), with JSON
   and text parsers; tests on Lynx outputs.
5. `remote/helper.py` + `core/helper.py`: version check and upload; `scan`,
   `tail`, `modules`.
6. `core/project.py`: projects and steps with an existing job script;
   upload, submit, state updates.
7. `core/monitor.py`: polling with the limits of §5; log following.
8. `core/transfer.py`: selective, resumable downloads (skip files already
   present with the same size and time).
9. `cli.py`: `vwf connect`, `vwf partitions`, `vwf submit`, `vwf jobs`,
   `vwf cancel`, `vwf get` — tried on Lynx before any GUI.
10. GUI: cluster profile editor with "Discover" and "Test connection"; job
    list; remote file browser with download; log viewer.

## 12. Open questions

- Lynx: the Spack module directory and its naming; `/scratch` seems to be
  local to each compute node (only `compute03` there), so not usable from the
  login node.
- Tirant: authentication (one-time codes?), and whether outbound connections
  or `python3` are restricted on its login nodes.
- Pseudopotential sets to ship or download (SSSP, PseudoDojo; Yambo needs
  norm-conserving).
- Whether the suite becomes one program with modules or stays separate
  programs sharing a library — affects how much of the GUI framework is
  shared with Visual Excitons.
