# Visual Workflows — design

Status: Phase 1 complete (2026-11-20). Decisions are marked **Decided**; the
rest are proposals open to change. How the code is organised is in
[architecture.md](architecture.md); using it, in the other
[documents](../README.md#documentation).

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
- **Decided:** Python ≥ 3.11 on the PC (`tomllib`).
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
│ cli      `vwf` commands    │ one     │ scancel, module, find, df,        │
│ core     no Qt: profiles,  │ open    │ files (SFTP)                      │
│          projects, jobs,   │ session │                                   │
│          scheduler, files  │         │ nothing installed there           │
└────────────────────────────┘         └───────────────────────────────────┘
```

**No service on the cluster, nothing installed there.** Login nodes usually forbid long-running
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
    modules.py         Environment Modules/Lmod: detect, list (JSON or terse),
                       module show, readable names (Spack), load test
    scheduler/
      base.py          Scheduler interface, Job, JobState, Partition, Node
      slurm.py         Slurm: JSON job parsing with text fallback; partitions
                       (`scontrol show partition -o`) and nodes (`sinfo -N`)
                       from text formats stable across versions
    profile.py         ClusterProfile: load, save, validate, export; ProfileStore
    project.py         Project, Step, JobRecord and their TOML files
    jobstatus.py       status lines added to job scripts (§5.1), status files,
                       how a job ended (final_state)
    runner.py          upload, submit with dependencies, refresh, cancel
    jobspec.py         JobSpec: a step's resources and runs; checks against the
                       partition; job script rendering (Phase 2)
    parallel.py        parallelisation checks of a step's runs, and BSE
                       settings Visual Excitons needs (Phase 2)
    transfer.py        downloads: scan (find -printf), choose (modes, patterns),
                       plan against outputs/, resumable fetch (.vwf-part)
    monitor.py         watch (polling ≥ 30 s), LogFollower (reads from an offset)
  cli.py               `vwf`
  gui/
    main.py            QApplication + qasync loop
    asyncqt.py         launch(), ask(), error boxes, GuiPrompter
    session.py         one connection per cluster
    main_window.py     cluster selector, menus, tabs
    cluster_editor.py  jobs_view.py  files_view.py  project_view.py
  yambopy_ext/         code meant for YamboPy (Phase 2 on), as in Visual Excitons
    qepy/pw_input.py   pw.x input reader: namelists and cards (qepy is a
                       package of its own in YamboPy's repository)
    io/yambo_input.py  Yambo input reader: variables, % blocks, runlevel flags
```

**Code meant for YamboPy (`yambopy_ext/`).** Modules that would belong in
YamboPy are written to move there unchanged, as Visual Excitons does: YamboPy's
layout (`qepy`, `io`), its conventions (snake_case, NumPy-style docstrings,
1-based bands), no Qt, no asyncio and nothing from the rest of the program,
and tests of their own in `tests/yambopy_ext`. Each module's docstring says
what in YamboPy it would replace. YamboPy is not a dependency yet (importing
`qepy` or `yambopy` pulls in netCDF4, numba and the rest), so these use the
standard library only. YamboPy itself is not changed from this project while
the fork's `ase-bz` branch has a pull request pending.

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

## 4. No helper on the cluster

**Decided (2026-11-20):** no script of our own on the cluster, for now.
Everything planned for one is done with commands every Linux cluster has:
folder scans with `find -printf` (one call for a whole tree), log following
with SFTP reads from an offset, disk space with `df -P`, modules with the
`module` command itself (§6). Nothing to upload, version or keep compatible
with old Pythons. A helper can come back if something needs it (e.g. heavy
output parsing next to the data).

## 5. Scheduler

`Scheduler` interface in `core/scheduler/base.py`:

```python
class Scheduler(ABC):
    async def version(self) -> str
    async def partitions(self) -> list[Partition]       # limits, nodes, allowed groups
    async def nodes(self) -> list[Node]
    async def user_groups(self) -> list[str]
    async def submit(self, script, workdir, *, after_ok=(), options=()) -> str  # job id
    async def cancel(self, job_ids) -> None
    async def jobs(self) -> dict[str, Job]               # queued, running, recently ended
    async def job(self, job_id) -> Job | None
    # Accounting, where the cluster keeps it (empty results otherwise)
    async def accounting_available(self) -> bool
    async def history(self, job_ids) -> dict[str, Job]   # with elapsed time, peak memory
    async def associations(self) -> list[Association]
    async def qos(self) -> list[Qos]
```

Rendering `#SBATCH` headers comes with the job script builder (Phase 2).

Slurm implementation:

| Need | Command | Notes |
|---|---|---|
| Version | `sinfo --version` | decides whether `--json` is available (Slurm ≥ 21.08) |
| Partitions | `scontrol show partition -o` | limits, default memory, `AllowGroups`; a text format stable across versions |
| Nodes | `sinfo -h -N -o "%N\|%P\|%c\|%m\|%G\|%t\|%C\|%O\|%e\|%z"` | one line per node and partition; `%z` (sockets:cores:threads) last, so older recordings without it still read |
| Allowed partitions | `id -Gn` against each partition's `AllowGroups` | Lynx limits partitions by Unix group |
| Accounting? | `sacct -n -X -o JobID -S now` | succeeds only where the cluster keeps accounting (not Lynx); found out once per session |
| Accounts, QOS | `sacctmgr -nP show assoc where user=$USER …`, `sacctmgr -nP show qos …` | with accounting, if users may read it; else from the profile |
| Submit | `sbatch --parsable [--dependency=afterok:…] script` | the id is the first field |
| Queued, running and recently ended jobs | `squeue --json --user="$USER" --states=all`, else `squeue -h … -o <fields>` | one call for all; ended jobs are kept only `MinJobAge` (300 s on Lynx) and listed only with `--states=all` |
| One job in detail | `scontrol show job --json <id>` | one id per call: a comma list is refused |
| Finished jobs | `sacct -nP -j <ids> …` where accounting exists; else the job's status file (§5.1) | `sacct` adds elapsed time and peak memory (MaxRSS, largest step) |
| Cancel | `scancel id…` | |

Both parsers (JSON and text) are implemented; the text one is the fallback for
old Slurm versions and is tested against recorded outputs. `sacct` and
`sacctmgr` are read in their `-nP` text form only; until a cluster with
accounting (Tirant?) is recorded, their tests use samples written from
Slurm's documentation (`tests/data/synthetic/`).

**Polling:** one `squeue` call for all of the user's active jobs, at most every
60 s (configurable, minimum 30 s); when a job leaves the queue, its status
file (§5.1), and accounting where kept. Polling stops when no job is active.
Admins notice heavy polling: automatic refreshes are never under 30 s (manual
ones are not limited).

**Job states:** `pending → running → completed | failed | cancelled | timeout |
out_of_memory`, or `unknown`. Slurm's states map onto these (e.g. COMPLETING
→ running, NODE_FAIL → failed, DEADLINE → timeout); Slurm's own word and the
reason are kept for display.

### 5.1 Job status without accounting

Lynx has no Slurm accounting: `sacct` and `sacctmgr` fail, and Slurm forgets
an ended job after `MinJobAge` (5 minutes). A job that ends while the PC is
off would leave no trace of how it ended. So **every job records its own
status**, which works on any cluster:

- Lines added after the `#SBATCH` header of the uploaded copy (the local
  script stays as written; also job scripts the user brings)
  append to `.vwf/job-$SLURM_JOB_ID.status` in the job's folder: `started`
  with time and node list, `modules` (`module -t list`), and on exit (a bash
  `trap … EXIT`, plus `TERM` for Slurm's warning before a kill) `ended` with
  the exit code or signal. Tested on Lynx: at the time limit the batch
  script receives SIGTERM (then SIGKILL after `KillWait`, 30 s), so the trap
  runs. The TERM trap must exit (with 143): a trap that only records the
  signal lets the script go on to its next command until the SIGKILL.
- Final state, in this order: Slurm's own (`squeue`, `scontrol show job`)
  while it remembers the job; then accounting (`sacct`) where the cluster
  keeps it; else the status file: `ended` with exit 0 →
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
  avail` (terse, both systems); Lmod also `module spider`.
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
  listing modules and checking loads; the job script builder (Phase 2) will
  write them into job scripts before the loads. Spack
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
- The modules actually loaded (`module -t list` at the job's exit) are
  recorded in its status file (§5.1) and its step record, for reproducibility.

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
python = "python3"             # the cluster's Python (reported by `vwf test`)

[scheduler]
type = "slurm"
account = ""                   # default --account, if the site needs one
qos = ""

[paths]
work_root = "$HOME/visual-workflows"      # where project folders go
scratch = ""                               # e.g. "/scratch/$USER" if separate

[partitions.cpu]                # discovered, then editable
max_walltime = "3-00:00:00"
cores_per_node = 64             # CPUs as Slurm counts them (hardware threads)
memory_per_node_gb = 256        # the largest node's
threads_per_core = 2            # 0: unknown (profiles from before Phase 2)
min_memory_per_node_gb = 192    # the smallest node's
memory_limit_per_node_gb = 0    # MaxMemPerNode; 0: no limit

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

A **"Discover"** button fills in the partitions the user may use, with their
limits, and corrects a Spack module folder given one level too high; modules
are chosen per program from the cluster's list, and checked. Unknown keys are kept when
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
- `step.toml` records: name, job script, the steps it runs after, and each
  run's job: id, submission, state, Slurm's state and reason, exit code or
  signal, start and end, nodes, output file, modules loaded, whether final,
  and where the final state came from.
- Dependencies between steps (nscf needs scf's `.save`) are explicit in each
  `step.toml`; chains are submitted with `--dependency=afterok` for steps
  still queued or running, none for completed ones, and refused for steps
  that never ran or failed.
- **Decided:** TOML files, human-readable and versioning well under git. If job tracking across
  many projects needs fast queries later, a SQLite index can be added on top,
  rebuilt from the project files.

**Downloads** go into each step's `outputs/`, skipping files already there
(same size and time) and resuming interrupted ones (`.vwf-part` files, renamed
when complete). **Decided:** by default everything except wave functions
(QE `*.save/wfc*`, `*.wfc<n>`; Yambo `SAVE/ns.wf*`) and files over 1 GB;
`--all` for everything; `--for-excitons` for what Visual Excitons reads
(`SAVE/ns.db1`, `SAVE/ns.wf*` for its real-space views, `ndb.BS_diago_Q*`,
every `ndb.QP*`); `--include`/`--exclude` patterns on top.

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

- No cluster, Docker or network needed: an asyncssh server inside the test
  process (every login method, host keys, SFTP), `FakeTransport` answering
  from recorded outputs (`tests/data/lynx/`), `LocalTransport` on the PC, and
  `LocalSlurm` running job scripts with bash (status lines, dependencies,
  cancel, time limits). Details in [development.md](development.md#tests).
- Proposed: no Docker Slurm cluster for now: the fakes plus recorded outputs
  cover the code, and real clusters are tried by hand.
- A manual checklist on each cluster before a release
  ([development.md](development.md#trying-it-on-a-real-cluster)).
- Tests never touch the user's keyring, configuration, ssh-agent or
  QSettings.

## 11. Phases

| Phase | Delivers |
|---|---|
| 0 | Survey Lynx (below). Record real command outputs as test data. |
| **1 (done)** | Connection, cluster profiles, remote file browser, submit/cancel/follow an existing job script, downloads. CLI first, then GUI. |
| 2 | Job script builder: partitions, modules (with load test), resources; QE/Yambo parallelisation checks; BSE settings Visual Excitons needs. Plan below. |
| 3 | QE inputs: structure import (ASE), symmetry (spglib), k grids, Brillouin-zone paths (YamboPy fork's `BrillouinZone`), pseudopotential library; scf/nscf/bands. |
| 4 | Yambo inputs: templates from the cluster's own `yambo -F`, edited in the GUI (the Phase 2 reader in `yambopy_ext/io`, extended to write; YamboPy's `YamboIn` loses values, below); chains scf → nscf → p2y → GW → BSE. |
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

**Tirant (2026-11-20):**

- Rocky Linux 8.10; Slurm 24.11.5, JSON `data_parser/v0.0.42` (read by the
  same parser as Lynx's v0.0.45).
- **Accounting** (slurmdbd): `sacct`/`sacctmgr` work; `MinJobAge` 300 s.
- Partitions `main` (default, 288 nodes) and `interactive` (2), open to all
  groups; access by **QOS**: `main` accepts `class_a…class_v`, `interactive`
  only `interactive`. The user's account `lv…` gives `class_t` (default, 3
  days, 4608 CPUs per job) and `interactive` (1 h, 96 CPUs). Partitions have
  no time limit: the QOS's applies → `access()` (Phase 1 fix).
- 48 cores per node, one thread per core (`CR_CORE_MEMORY`); 94 GB per node,
  at most 92000 MB per job and node; **1916 MB per CPU by default**.
- `MpiDefault=pmix`.
- **Lmod 8.7.55:** no JSON listing; the terse listing names families
  (`yambo/`), which are not modules; `module show` in Lua (`whatis("Key:
  value")`, `load(...)`, `prepend_path(...)`) → parsed (Phase 1 fix). The
  group's own modules (in `/storage/projects/<group>/modules`, already in
  `MODULEPATH`) load their libraries themselves. No Spack.
- Home `/home/<group>/<user>` (seen by Slurm as `/storage/home/…`), scratch
  `/storage/scratch/<group>/<user>` (no variable for it), all on Lustre
  (`/storage`, 1.3 PB); `/scratch` is local to each node.
- System Python 3.6.
- Busy queue: the test jobs waited (`Priority`);
  `scripts/cluster-survey-jobs-finish.sh` records them once they ran.

### Phase 1 tasks (all done)

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
5. `core/modules.py`: module system, listing, details, load test with
   `which`/`ldd` (no helper on the cluster, §4).
6. `core/project.py`: projects and steps with an existing job script;
   upload, submit, state updates.
7. `core/monitor.py`: polling with the limits of §5; log following.
8. `core/transfer.py`: selective, resumable downloads (skip files already
   present with the same size and time).
9. `cli.py`: `vwf clusters …`, `test`, `partitions`, `jobs`, `run`, `submit`,
   `cancel`, `modules`, `project …` — tried on Lynx before any GUI.
10. GUI (`gui/`): cluster selector and connection; Cluster tab (profile
    editor, Test connection, Discover, module picking and load checks per
    program); Jobs tab; Files tab (browse, preview, follow, download);
    Project tab (steps, submit/cancel/refresh/watch, step log, downloads
    with a summary first). Async code runs on Qt's loop through qasync;
    login questions are non-blocking dialogs (`asyncqt.py`).

### Phase 2 plan

A step may carry a job description, a `[job]` table in its `step.toml`, from
which the program writes the step's `job.sh`. Steps with a script of their
own work as in Phase 1. The project format stays `workflow-project/1`:
`job.sh` is still an ordinary script, and older versions run it.

```toml
[job]
partition = "magma"
nodes = 1
tasks_per_node = 48          # MPI tasks
cpus_per_task = 1            # OpenMP threads
memory = "60G"
time = "12:00:00"
links = { SAVE = "../03-p2y/SAVE" }   # symlinks made before the runs
before = []                  # extra lines before the runs
after = []                   # and after them
[[job.runs]]
program = "qe"; executable = "pw"
input = "pw.scf.in"; output = "pw.scf.out"; args = "-nk 4"
[[job.runs]]
program = "yambo"; executable = "yambo"
input = "bse.in"; jobs = ["diago", "bse", "gw"]   # yambo -F bse.in -J diago,bse,gw
```

The script: `#SBATCH` lines (job name, partition, nodes, tasks per node, CPUs
per task, `--hint=nomultithread` where nodes have 2 threads per core, memory
and time always, account and QOS from the profile), `module purge`, the
profile's `module use` folders, the modules of every program the step runs,
the programs' preambles, `OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK`, the links,
`before`, the runs through the program's launcher (QE: `-inp in > out`, never
`< in`, which some MPI setups do not pass on; Yambo: `-F in -J jobs`, in the
folder that holds `SAVE` or a link to it, writing its own `o-*`, `r-*` and
`l-*` files), `after`. Yambo runs get `-walltime` from the time left when they
start, less 5 minutes, in whole hours (Yambo compares days, hours and minutes
one by one; only its Haydock and inversion solvers check it); for pw.x, whose
`max_seconds` is set in its input, the checks suggest one when it is missing
or above the job's time.

**Decided:**

- A hand-edited generated `job.sh` is noticed at Submit (its hash differs from
  the one recorded when it was written); the user keeps the edits (the step
  becomes one with its own script) or writes it again from the description.
  `before`/`after` make hand edits rarely needed.
- Parallelisation checks read the inputs lightly; exact k-point counts after
  symmetry (spglib, the full QE input) wait for Phase 3.
- The module load test runs at Submit, its result kept per cluster and module
  list for the session; a failed test refuses the submission, with a "submit
  anyway" override (`--no-check` in the CLI).
- Saved job presets per cluster: later.
- BSE steps are checked for what Visual Excitons reads: Phase 2, after the
  parallelisation checks.
- Inputs are read by our own readers (`yambopy_ext/`), not YamboPy's: `PwIn`
  cuts values at `/` (`pseudo_dir='../pseudos/'` becomes `'..`), fails on
  `K_POINTS gamma` and merges its defaults into files it reads; `YamboIn`
  drops strings with dots, `<` or `/` (`BS_CPU="2.1.1"`,
  `KfnQPdb="E < ./gw/ndb.QP"`) and empty ones, turns integers into reals and
  switches on runlevels that are commented out. YamboPy's `schedulerpy` has
  nothing to reuse (only `mpirun -np`, no `module use`, memory or account).
  Every problem found in YamboPy goes into
  [yambopy-issues.md](yambopy-issues.md), ready to be filed on GitHub.

**Tasks** (about one commit each):

1. Node layout and partition memory: `%z` (sockets:cores:threads) in the
   `sinfo -N` format; profile partitions gain `threads_per_core`,
   `min_memory_per_node_gb` and `memory_limit_per_node_gb` (new keys, same
   format), memory rounded down to 0.1 GB; Discover fills them and
   `vwf partitions` shows cores and the job memory limit. **Done**
   (2026-11-21): Tirant recorded again (2:24:1, `MaxMemPerNode` 92000 MB on
   96310 MB nodes); Lynx's recording is still the old one (its layout,
   2:24:2, is in its `sinfo.json`), to be recorded again with `%z`.
2. `core/jobspec.py`: `JobSpec` and runs; checks against the partition and
   `access()` (time against the partition's or QOS's limit, tasks × threads
   within the physical cores, memory within the node and `MaxMemPerNode`);
   rendering. Tested with the Lynx and Tirant profiles. **Done**
   (2026-11-21). The checks use the profile only (its time limit is the
   QOS's, from Discover), so they work offline. Decided on the way: runs
   stop the job when they fail (`|| exit $?`); runs of programs with
   different modules each get a clean environment (`module purge`, `use`,
   `load`), runs of one program share one; `SRUN_CPUS_PER_TASK` is exported
   (srun stopped taking `--cpus-per-task` from the job in Slurm 22.05);
   threads are placed (`OMP_PLACES=cores`, `OMP_PROC_BIND=close`) when a task
   has more than one; `-walltime` is computed when each Yambo run starts
   (Yambo counts from its own start, and earlier runs used part of the job),
   5 minutes before the job's end, rounded down to whole hours (`11h`,
   `1d2h`; minutes in the last hour only), since Yambo's source (5.1.1,
   5.3.0) compares days, hours and minutes each with its own limit, so
   `11h50m` would match only in minutes 50–59 of each hour; only the Haydock
   and inversion solvers check it;
   above the smallest nodes' memory is a warning (the job waits for the
   larger ones); `[job]` without time or memory is an error. `time =
   "UNLIMITED"` is a choice the user makes where the partition and QOS set
   no limit (Lynx), with a warning saying what it costs: no backfill (the
   job may wait longer), a hung run keeps its nodes, no `-walltime` for
   Yambo's Haydock and inversion solvers; the script then has `--time=0`, sbatch's "no limit", since a job
   without `--time` gets the partition's `DefaultTime`, which may be short.
3. Steps with a job description: `[job]` in `step.toml`, `job.sh` written on
   every save with its hash recorded, hand edits noticed; the module test at
   Submit. **Done** (2026-11-21): `Project.add_job_step`, `Step.write_script`,
   `script_edited`, `keep_script`; `step.toml` keeps `script = "job.sh"`, so
   older versions run the script as written. At submission, before anything
   is uploaded, the steps with a description are checked together: a
   hand-edited script stops it (`ScriptEdited`: the GUI asks keep or write
   again; the CLI takes `--keep-edits` or `--rewrite`); the scripts are
   written again from the current profile; the descriptions are checked; and
   each program's modules are loaded and its executables found once
   (`Modules` remembers the tests that passed, for the connection in the
   GUI; failed ones are tried again). Errors stop it (`SubmitRefused`), with
   "Submit anyway" in the GUI and `--no-check` in the CLI; warnings are shown
   after submitting.
4. `yambopy_ext/qepy/pw_input.py`, `yambopy_ext/io/yambo_input.py` and
   `core/parallel.py`, following [Quantum ESPRESSO](quantum-espresso.md) §2
   and [Yambo](yambo.md) §3:
   - QE errors (pw.x stops at startup, or ignores the option; checked in the
     source of 7.3.1 and 7.5): options it would ignore (misspelled, `-nk=4`,
     not a number); `-ni` other than 1 (pw.x refuses images); `-nk`, then
     `-nb`, then `-nt` not dividing what is left of the tasks, or out of
     range; ⌊√min(nd, tasks per band group)⌋ above `nbnd`; pools with
     `K_POINTS gamma` and `nspin=1`; pools with Berry phase, or with hybrid
     functionals and `tstress`. Warnings: `-nd` not a square or above a band
     group (QE rounds it down); more pools than k-points (a list's count, an
     automatic grid's product as an upper bound, twice with `nspin=2`; idle
     pools, and `max_seconds` not checked inside the k-point loop); k-points
     not a multiple of the pools; `max_seconds` missing or above the job's
     time.
   - Yambo errors: a role not valid for the section (`DIP`: k c v;
     `X_and_IO`, `X`: q g k c v; `SE`: q g qp b, g only in 5.3; `BS`: k eh t),
     factors and roles of different counts, a count above 999, a role other
     than `eh` with more processes than its size where the input gives it
     (bands, QP states; k and q in Phase 3), which Yambo stops on; unknown
     roles, which Yambo accepts and gives no work (checked in the source of
     5.1.1 and 5.3.0). Warnings: a product different from the MPI tasks
     (Yambo uses its defaults, saying so only in the log); `*_Threads`
     different from the CPUs per task; a linear-algebra count not a square or
     below 4, or one Yambo never reads (`DIP_*`, `X*_DIAGO`, `SE_*_INV`);
     Yambo 4 names (`X_all_q_*`), which Yambo 5 ignores like any unknown
     variable. Dots, commas and spaces all separate.
5. BSE settings for Visual Excitons (warnings): a diagonalising solver
   (`BSSmod` d or s; Haydock gives no `ndb.BS_diago_Q*`), eigenvectors
   written (`WRbsWF`), a QP database covering the BSE bands.
6. CLI: `vwf project add-step` with resources and runs, `vwf project script
   STEP` (the rendered script), `vwf project check`.
7. GUI: a step editor (resources with the partition's limits as you type,
   the runs, links, a live preview of the script, the checks' results) and an
   Edit step action.
8. Docs and design brought up to date; a real run on Tirant to settle
   `--mpi=pmi2` under `pmix`.

## 12. Open questions

- Lynx: `/scratch` seems to be local to each compute node (only `compute03`
  there), so not usable from the login node. (Its Spack modules are in
  `/apps/spack/share/spack/modules/linux-arch-x86_64`, named
  `{name}/{version}-{compiler}-{compiler version}-{hash:7}`.)
- A Docker Slurm cluster for end-to-end tests, if the fakes stop being enough.
- Tirant: whether Intel MPI builds start with plain `srun` under
  `MpiDefault=pmix`, or need `--mpi=pmi2` (plain `srun hostname` with 2
  tasks worked on `interactive`, 2026-11-20; an MPI program is still to be
  tried); its scratch purge policy.
- Tirant: authentication (one-time codes?), and whether outbound connections
  or `python3` are restricted on its login nodes.
- Pseudopotential sets to ship or download (SSSP, PseudoDojo; Yambo needs
  norm-conserving).
- Hand-off to Visual Excitons (Phase 5). Visual Excitons (2026-11-21) opens
  a project folder: with a single SAVE in it, it finds the BSE and QP
  databases in other steps' `outputs/`, up to 3 levels down
  (`<project>/<step>/outputs/<job>/`, so `-J` names without folders, and
  the p2y step runs `p2y -I <nscf's prefix.save> -O .` so that `SAVE` is at
  the step's top, not inside `prefix.save`, as checked in Yambo's source,
  5.1.1 and 5.3.0); and
  `visual-excitons <project> [--save DIR] [--bse DIR] [--qp PATH]` opens one
  from the command line (exit code 2 and a message on stderr when a path
  holds the wrong thing). The hand-off is then a "Open in Visual Excitons"
  action running that command; which BSE step to pass when there are
  several is still to decide. A lighter `excitons` download without `ns.wf*`
  (gigabytes) would do for spectra and composition.
- Whether the suite becomes one program with modules or stays separate
  programs sharing a library — affects how much of the GUI framework is
  shared with Visual Excitons.
