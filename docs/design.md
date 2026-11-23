# Visual Workflows — design

Status: Phases 1 to 3 complete (2026-11-23); Phase 4 (Yambo inputs) planned. Decisions are marked **Decided**; the
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
- **Decided:** a new repository, `visual-workflows`, part of a suite with
  Visual Excitons. The suite's name is open; see [Naming](#9-naming).
- **Decided:** Python ≥ 3.11 on the PC (`tomllib`).
- **Decided:** projects are folders of TOML files, not a database (§8).

Target clusters: **Lynx** (the group cluster: SSH with password or key,
Python 3 on the login node) first, **Tirant** later (recorded and used since
Phase 2).

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
    paths.py           the program's folders on the PC (settings, data)
    modules.py         Environment Modules/Lmod: detect, list (JSON or terse),
                       module show, readable names (Spack), load test
    scheduler/
      base.py          Scheduler interface, Job, JobState, Partition, Node
      slurm.py         Slurm: JSON job parsing with text fallback; partitions
                       (`scontrol show partition -o`) and nodes (`sinfo -N`)
                       from text formats stable across versions
    profile.py         ClusterProfile: load, save, validate, export; ProfileStore
    discovery.py       Discover: partitions, QOS and account a user may use
    report.py          the connection report (`vwf test`, Test connection)
    recording.py       recording what the program asks a cluster (`vwf clusters record`)
    project.py         Project, Step, JobRecord and their TOML files
    jobstatus.py       status lines added to job scripts (§5.1), status files,
                       how a job ended (final_state)
    runner.py          upload, check, submit with dependencies, refresh, cancel;
                       where a run writes its own output
    jobspec.py         JobSpec: a step's resources and runs; checks against the
                       partition; job script rendering (Phase 2)
    problems.py        Problem: an error or warning, with where it is
    parallel.py        parallelisation checks of pw.x and yambo runs (Phase 2)
    input_checks.py    pw.x and yambo inputs, arguments, pseudopotentials, and
                       BSE settings Visual Excitons needs (Phase 2)
    chain.py           checks across the chain of steps (Phase 2, exact in 3)
    pseudos.py         where pw.x finds a run's pseudopotentials (Phase 2)
    structure.py       Structure: read (ASE, pw.x inputs), symmetry (spglib and
                       pw.x's own), ibrav, orientation, vacuum (Phase 3)
    kpoints.py         grids, pw.x's k-point count, band paths (Phase 3)
    qe_inputs.py       a structure and k-points put into a pw.x input (Phase 3)
    qe_chain.py        the scf → nscf → p2y → bands chain (Phase 3)
    pseudodojo.py      PseudoDojo's tables, the library, project copies (Phase 3)
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
    step_editor.py     a step's job description, script and checks (Phase 2)
    pw_editor.py       pseudo_picker.py  qe_chain_dialog.py   (Phase 3)
  yambopy_ext/         code meant for YamboPy (Phase 2 on), as in Visual Excitons
    qepy/pw_input.py   pw.x input reader and writer: namelists and cards (qepy
                       is a package of its own in YamboPy's repository)
    qepy/pw_namelists.py   the variables pw.x accepts, per QE version
    qepy/upf.py        UPF header reader
    qepy/lattice.py    QE's latgen and abc2celldm, ported (Phase 3)
    qepy/symmetry.py   pw.x's symmetry search, ported (Phase 3)
    io/yambo_input.py  Yambo input reader: variables, % blocks, runlevel flags
    io/yambo_variables.py  names, units and options yambo knows, per version
```

**Code meant for YamboPy (`yambopy_ext/`).** Modules that would belong in
YamboPy are written to move there unchanged, as Visual Excitons does: YamboPy's
layout (`qepy`, `io`), its conventions (snake_case, NumPy-style docstrings,
1-based bands), no Qt, no asyncio and nothing from the rest of the program,
and tests of their own in `tests/yambopy_ext`. Each module's docstring says
what in YamboPy it would replace. They use the standard library only, as
they were written when YamboPy was not a dependency (importing `qepy` or
`yambopy` pulls in netCDF4, numba and the rest). Since Phase 3 the YamboPy
fork is a dependency of the program itself (its `BrillouinZone` and
`lattice_data_for_cell`, used by `core/`, as in Visual Excitons). YamboPy
itself is not changed from this project while the fork's `ase-bz` branch has
a pull request pending.

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

Rendering `#SBATCH` headers is the job script builder's (`jobspec.render`, Phase 2).

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
`sacctmgr` are read in their `-nP` text form only. Tirant keeps accounting,
and its recording (`tests/data/tirant/`) has their answers; samples written
from Slurm's documentation (`tests/data/synthetic/`) cover what it does not.

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
  listing modules and checking loads, and written into job scripts before
  the loads. Spack
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
pseudos = ""                               # a library of pseudopotentials (Phase 2)

[partitions.cpu]                # discovered, then editable
max_walltime = "3-00:00:00"
cores_per_node = 64             # CPUs as Slurm counts them (hardware threads)
memory_per_node_gb = 256        # the largest node's
threads_per_core = 2            # 0: unknown (profiles from before Phase 2)
min_memory_per_node_gb = 192    # the smallest node's
memory_limit_per_node_gb = 0    # MaxMemPerNode; 0: no limit
qos = { class_t = "3-00:00:00" } # the QOS you may use here, each with its
                                 # time limit (Discover; Phase 2)

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

A step can be any job script plus input files the user brings (Phase 1);
since Phase 2 a step may instead carry a job description its script is
written from, and since Phase 3 the program builds pw.x inputs and the QE
chain for Yambo itself.

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

Since 2026-11-21: each recorded cluster has a manifest (`cluster.toml`: the
program's commands, their recorded answers, and what the program must find
there), and the same contract tests run on every recorded cluster; the fake
cluster refuses unrecorded commands, so a change in what the code sends
shows up; `vwf clusters record` records what the program asks, so recordings
follow the code; live, read-only tests (`pytest -m live --cluster NAME`)
check real clusters now. The connection report is one implementation for
the command line and the window (`core/report.py`), as Discover
(`core/discovery.py`): the CLI's own `module --version | head -1` had
reported Lmod as missing on Tirant.

## 11. Phases

| Phase | Delivers |
|---|---|
| 0 | Survey Lynx (below). Record real command outputs as test data. |
| **1 (done)** | Connection, cluster profiles, remote file browser, submit/cancel/follow an existing job script, downloads. CLI first, then GUI. |
| **2 (done)** | Job script builder: partitions, modules (with load test), resources; QE/Yambo parallelisation checks; BSE settings Visual Excitons needs. Plan below. **Done** (2026-11-21), with input, pseudopotential and chain checks, and tried on Tirant. |
| **3 (done)** | QE inputs: structure import (ASE), symmetry (spglib), k grids, Brillouin-zone paths (YamboPy fork's `BrillouinZone`), pseudopotential library; scf/nscf/bands. Plan below. **Done** (2026-11-23), with pw.x's own symmetry search and k-point count ported, and a chain run on Tirant. |
| 4 | Yambo inputs: made by the program and checked against the cluster's own `yambo -F`, edited in the GUI (the Phase 2 reader in `yambopy_ext/io`, extended to write; YamboPy's `YamboIn` loses values, above); the Phase 3 chain extended to GW and BSE; ypp inputs. Plan below. |
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
- `MpiDefault=pmix`. Intel MPI builds start with plain `srun` (and with
  `--mpi=pmi2` too): settled in Phase 2's test, so no launcher options.
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
  symmetry came in Phase 3 (task 5).
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
   96310 MB nodes); Lynx recorded again on 2026-11-23 (2:24:2: 2 threads
   per core, now checked by the contract tests), its manifest merged with
   the survey's (the ended test jobs, the module listings).
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
     automatic grid's product as an upper bound, exact since Phase 3; twice
     with `nspin=2`; idle
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

   **Done** (2026-11-21): `read_pw_input` and `read_yambo_input` (stdlib
   only, YamboPy's conventions) take the files as the programs do; the
   checks run from `jobspec.check()` with the step's folder (at submission,
   from `Runner.prepare`), messages naming the run and its input. Role sizes
   known from the input alone: the self-energy's bands (`GbndRnge`) and QP
   states (`QPkrange`); c and v need the occupied bands (done in Phase 3,
   task 5). Yambo's
   version, for `g` in `SE_ROLEs`, is read from its module names
   (`yambo/5.3-double` → 5.3). Hybrid functionals are recognised by
   `input_dft` names only. `max_seconds` is suggested as the job's time less
   5 % (at least 10 minutes), for a run that starts with the job. An input
   missing from the step's folder is a warning (a line before the runs may
   make it).
4b. **Proposed (2026-11-21, from a second source reading:** QE 7.5/7.6 and
   Yambo 5.3.0/5.4-beta, see the references; to be agreed before writing).
   Checks of inputs that the programs only report on the cluster, or worse,
   silently change. Each needs the input alone unless said.
   - **pw.x, the input alone** (errors, pw.x stops): a variable not in the
     version's namelists (`yambopy_ext/qepy/pw_namelists.py`, the version
     from the module names as for Yambo; misspellings, `lkpoint_dir`, the
     pre-7.1 Hubbard variables); namelists out of order or missing
     (`&IONS` for relax and md, `&CELL` for vc-relax and vc-md); a value not
     among those allowed (`calculation`, case-sensitive; `occupations`,
     `smearing`, `diagonalization` per version, `restart_mode`, `nspin`);
     the lattice (`ibrav` with `celldm(1)` or `A`, not both; `ibrav=0` and
     `CELL_PARAMETERS`); `ecutrho` ≤ `ecutwfc`; smearing without `degauss`;
     the magnetisation rules (`tot_magnetization`, `starting_magnetization`
     with `nspin=2`; `noncolin` with `nspin=2`; `lspinorb` without
     `noncolin`; `noncolin` with Γ); the cards (positions before species, an
     unknown option, fewer positions than `nat`, a label not among the
     species, a repeated label, automatic grids with shifts other than 0/1,
     tetrahedra without an automatic grid; an XML input). Warnings for what
     pw.x silently changes: no `K_POINTS` card (Γ with gamma tricks), an
     unknown `K_POINTS` option or `disk_io`, extra species or position
     lines (ignored), a whole-line `!` comment inside a card (read as
     data), `ecutrho` below 4 × `ecutwfc`.
   - **pw.x with the pseudopotentials**, when they are in the step's folder
     or a local `pseudo_dir` (those on the cluster: read over the
     connection at submission, as done below): files missing or names over 80
     characters; `nbnd` below the occupied bands (Σ `z_valence` −
     `tot_charge`); spin–orbit without a spin–orbit pseudopotential;
     functionals that differ between files (warning). A UPF reader in
     `yambopy_ext/qepy/` (`z_valence`, `pseudo_type`, `is_ultrasoft`,
     `is_paw`, `has_so`, `functional`, cutoffs).
   - **yambo, the input and arguments** (errors where yambo would do
     something else than asked, silently): runlevel options in the
     arguments (`-o b`, `-k sex`…: yambo rewrites the input and exits), an
     unknown option, `p2y -F` or `-walltime`; strings without quotes
     (`BSKmod= SEX`: the default is kept); a name with a unit spelt in
     another case than Yambo's (`bsengexx= 30 Ry`: 30 G vectors); unit
     spellings yambo does not know, and G cutoffs without a unit; blocks
     with missing columns; GW and BSE runlevels in one input (the BSE is
     dropped); `bse` without `optics` or `bss`; `bss` without a valid
     `BSSmod` (letters h d s i t, 5.4 x c; matched as substrings, so
     `"haydock"` also diagonalises); `Lkind` per version; an unknown
     `BSKmod`; `CUTGeo` settings that switch the cutoff off;
     `KfnQPdb` without `<` as a word. Warnings: names yambo does not know
     (a list per version extracted from `INIT_load.F`, as for QE), a scalar
     given twice (the last counts), `#` inside a string (a comment), the
     5.4 `SX` section (`SE_*` no longer applies to HF).
   - **Order:** pw.x input alone, then yambo input and arguments, then the
     pseudopotentials (the UPF reader). The variable lists per version are
     data in `yambopy_ext/` with how they were extracted.

   **Agreed and in progress** (2026-11-21). Done: the name lists
   (`yambopy_ext/qepy/pw_namelists.py`; `yambopy_ext/io/yambo_variables.py`
   with runlevels, flags, variables, unit kinds and the command-line options
   of yambo, ypp and p2y), regenerated by `scripts/extract-input-names.py`
   from clones of QE and Yambo; the versions are read from the module names
   (the first plausible major.minor, so `qe/oneapi-2024.2.1/7.3.1-omp` is
   7.3); the pw.x checks from the input alone and the yambo checks of inputs
   and arguments (`core/input_checks.py`), run from `jobspec.check()` with
   the parallel ones. Tried on the 9 pw.x and 34 yambo inputs of YamboPy's
   tutorials and references: no errors; the warnings are Yambo 4 names
   (filed as YamboPy issue 14). The readers now take `/&end` (Fortran ignores
   the rest of the line after `/`), complex values `( 1 , 0 )` and units
   written against numbers (`0.000000fs`), as yambo writes them.

   **Pseudopotentials, done** (2026-11-21), as agreed: a project may keep
   them in `pseudos/` next to the steps (uploaded at Submit when new or
   changed: same size and not newer here means already there), a profile
   may name a library on the cluster (`[paths] pseudos`); written job
   scripts set `ESPRESSO_PSEUDO` to the first of the two, so inputs need no
   `pseudo_dir`. `core/pseudos.py` finds each file as pw.x does; headers
   are read here for files in the project and over the connection at
   submission for the others (the first 64 KB: UPF 1 spin–orbit, at the
   file's end, is then unknown). The UPF reader
   (`yambopy_ext/qepy/upf.py`) takes UPF 1, UPF 2 and the schema form; it
   read the 116 UPF files on the development PC (norm-conserving,
   ultrasoft, PAW, scalar and fully relativistic) the same in full and from
   64 KB. PseudoDojo's cutoff hints waited for the Phase 3 picker (its tables
   give md5 sums of the psp8 files, not of the UPF ones, so a UPF cannot be
   matched to its hints): since Phase 3, task 4, files from the picker carry
   their hints in the project's `sources.toml`.
5. BSE settings for Visual Excitons (warnings): a diagonalising solver
   (`BSSmod` d or s; Haydock gives no `ndb.BS_diago_Q*`), eigenvectors
   written (`WRbsWF`), a QP database covering the BSE bands.
   **Proposed addition (2026-11-21): checks across a chain of steps**, from
   the steps' inputs and how they are linked (`after`, `links`, `-J`): the
   scf writes its density (`disk_io` not `minimal` or `none`) and the nscf
   its wave functions (not `nowf`, `minimal`, `none`); the nscf sets `nbnd`
   (at least the bands its Yambo steps use: `BSEBands`, `GbndRnge`,
   `BndsRnX*`) and `force_symmorphic` (or `nosym`): symmetries come from
   the nscf's own input; the same `prefix`, `outdir`, `nspin`, `noncolin`
   in scf and nscf (a missing density or another spin setting does not
   stop the nscf); norm-conserving pseudopotentials, no DFT+U, only HF,
   PBE0, B3LYP or GAUPBE hybrids for a chain to p2y; a SEX or HF kernel
   with the screening computed in the run or found through `-J`; the QP
   database named in `KfnQPdb` made by a step it follows, covering
   `BSEBands` (a missing file only warns in yambo, the BSE then runs on DFT
   energies); steps running at once on one linked SAVE (they may rebuild
   `ndb.kindx` together). What needs the SAVE (bands, k-points, electrons
   from `ns.db1`: `BSEBands`, `GbndRnge`, `QPkrange` in range, c and v
   roles, `BSSNEig`) is Phase 3: done in task 5 from the nscf, except
   `BSSNEig`, which Yambo resizes itself.

   **Done** (2026-11-21), with the chain checks as proposed, in
   `core/chain.py`, run at submission for the steps submitted (with every
   step of the project as context). What each run writes and reads becomes a
   project path ("01-scf/out/si.save"), links followed; a reader is matched
   with the last writer before it (an earlier run of its step, or a step it
   comes after, directly or not); a writer found only in a step it does not
   come after is a warning (add it to `after`). Paths outside the project,
   or that no step writes, give no message (a `-J` folder elsewhere may hold
   the screening). Errors: an scf writing no density for an nscf; another
   nspin or noncolin than the scf's; an nscf writing no wave functions,
   DFT+U, a hybrid p2y refuses, ultrasoft or PAW pseudopotentials (in the
   project) for p2y; `GbndRnge` or `BSEBands` above the nscf's `nbnd`; a SEX
   or HF kernel with no screening computed or found; a `KfnQPdb` file in a
   step that makes none. Warnings: no step making the scf for an nscf; p2y
   reading an scf; no `force_symmorphic` (or `nosym`) in the nscf (an
   error since Phase 3, task 5, when pw.x would use fractional
   translations; otherwise nothing); `nbnd` unset there;
   `BndsRnX*` or `QPkrange` above it; QP bands not covering `BSEBands`;
   `KfnQPdb` from a run that is not GW; steps on one SAVE that may run at
   once; for Visual Excitons, a solver other than d or s, or no `WRbsWF`.
   Found on the way: the TOML reader refused `mpi = false` (booleans were
   refused everywhere, to keep them out of numbers); fixed.
6. CLI: `vwf project add-step` with resources and runs, `vwf project script
   STEP` (the rendered script), `vwf project check`. **Done** (2026-11-21):
   a run is one string, `"[PROGRAM:]EXECUTABLE [INPUT] [ARGS]"`, the program
   found from the profile's executables (else pw, ph, dos, projwfc, bands,
   pp → qe; yambo, p2y, ypp → yambo), `-J a,b` and `--serial` taken out of the
   arguments, an input given as a path copied into the step; resources as
   options; the step is checked as it is added. `vwf project check` runs
   `Runner.check()` (split from `prepare()`): offline by default, with
   `--on-cluster` the module tests and the pseudopotentials kept on the
   cluster; exit code 1 on errors. Executables missing from a profile take
   their usual names (pw.x). Found on the way: pw.x does not expand `$` or
   `~` in `pseudo_dir`, `outdir`, `wfcdir` (`trimcheck.f90`): now an error.
7. GUI: a step editor (resources with the partition's limits as you type,
   the runs, links, a live preview of the script, the checks' results) and an
   Edit step action. **Done** (2026-11-21): `gui/step_editor.py`; the Project
   tab has New step… (the editor), Edit step… (or a double-click, for steps
   with a description), Add own script… (the earlier dialog) and Check
   (`Runner.check`, with the cluster when connected). The live checks are
   `jobspec.check` (resources, inputs read from where they are chosen, before
   being copied: its new `inputs` argument; the project's pseudopotentials);
   the chain and what is on the cluster wait for Check and Submit. Saving an
   edited step (`Project.update_job_step`) writes its script again, after
   asking if it was edited by hand. What Visual Excitons needs of a BSE input
   moved to `input_checks.check_for_visual_excitons`, so that it shows as you
   type.

   **Added after** (2026-11-21, from the Tirant test: a 15-minute job waiting
   behind `main`'s queue while `interactive` stood idle): a step may name its
   own QOS and account (`qos`, `account` in `[job]`; the editor; `--qos`,
   `--account`), instead of the profile's. Discover keeps, per partition, the
   QOS you may use there with the time limit of each (`qos = { class_t =
   "3-00:00:00" }`; `access()` keeps the limit per QOS); the checks refuse a
   QOS the partition does not accept for you (the profile's `class_t` on
   `interactive`) and take the chosen QOS's time limit.
8. Docs and design brought up to date; a real run on Tirant to settle
   `--mpi=pmi2` under `pmix`.
   **Docs done** (2026-11-21): README (status, quick start with job
   descriptions), projects (the two kinds of job scripts), troubleshooting
   (refused submissions), command line; `vwf clusters discover` and
   `vwf clusters program` added, so a profile can be filled from the command
   line too (`core/discovery.py`, shared with the GUI's Discover). **Tirant
   test, pending:** a silicon scf in a step with a job description, its
   described run with plain `srun` and a line before it with
   `srun --mpi=pmi2` (exit code echoed), so one job settles the question
   and goes through the whole path (Discover, the program's modules, the
   module test, the project's pseudopotentials, the checks, the written
   script, Submit, the log). **Done** (2026-11-21): job 400542 on
   `interactive` (the step's own QOS), 9 s; `pmi2: exit 0`, and both pw.x
   runs (`--mpi=pmi2` and plain `srun`) on 4 processes, converged, `JOB
   DONE.`; the status lines recorded the job's modules, Get results brought
   the outputs. **Phase 2 is complete.**

### Phase 3 plan

pw.x inputs made with the program rather than by hand: a structure, its
symmetry and Brillouin zone, k-points, pseudopotentials and the main
settings, written as inputs the Phase 2 checks already read. Then the usual
chain for Yambo (scf → nscf, and bands) made in one go, consistent by
construction. What Phase 2 left to "the crystal's symmetry" becomes exact.

**What there is to build on:** the pw.x reader (`yambopy_ext/qepy`), the
checks, the step editor and job descriptions, the UPF reader; spglib (in the
environment, and a YamboPy dependency); the YamboPy fork's `BrillouinZone`
and `lattice_data_for_cell` (QE's `ibrav` classification, Setyawan–Curtarolo
paths, k-points in QE's format; no monoclinic or triclinic lattices), which
Visual Excitons already uses. Not there (before Phase 3): ASE.

**Decisions to take:**

1. Structure files: ASE as a dependency (CIF, POSCAR, XYZ, QE inputs and
   outputs, and more, read well), or only QE inputs and CIF with readers of
   our own.
2. The YamboPy fork as a dependency, as Visual Excitons has it, for the
   Brillouin zone and band paths (and the same lattice conventions in both
   programs); monoclinic and triclinic cells get no automatic path.
3. Pseudopotentials: download PseudoDojo's tables (NC; SR or FR; PBE, PBEsol
   or LDA; standard or stringent) into a library on the PC, copied into a
   project's `pseudos/` with where they came from, so their cutoff hints are
   known.
4. A 3D view of the structure now (pyqtgraph's OpenGL, as in Visual
   Excitons), or tables and the space group first, the view later.

**Decided** (2026-11-22): ASE as a dependency for structure files; the
YamboPy fork as a dependency (as Visual Excitons), monoclinic and triclinic
cells getting no automatic path; the PseudoDojo picker, with the tables'
provenance kept in the project; tables and the space group first, the 3D
view later (reusing Visual Excitons' pyqtgraph/OpenGL code).

**Tasks** (about one commit each):

1. Structures: read (QE input, CIF, and with ASE the rest), symmetry with
   spglib (space group, primitive cell, symmetries with fractional
   translations), QE's description (`ibrav` and `celldm`, or `ibrav = 0`),
   written as `CELL_PARAMETERS`/`ATOMIC_POSITIONS`; tested on Si, hBN, MoS₂,
   WSe₂ (bulk and layers). **Done** (2026-11-22): `core/structure.py`
   (`Structure`: read with ASE, from a pw.x input, spglib's symmetry with the
   operations that need fractional translations, the primitive idealised
   cell, the Bravais lattice as the YamboPy fork names it, pw.x cards). New
   structures are written with `ibrav = 0` and the exact cell, as ASE does,
   without re-expressing positions in QE's choice of vectors. (Corrected in
   tasks 2 and 3: pw.x then finds the crystal's full symmetry only if the
   cell is in QE's orientation; structures can be turned to it, or go in
   with their own ibrav.) ASE reads pw.x inputs only with `ibrav = 0`, so QE's
   `latgen` and `abc2celldm` are ported (`yambopy_ext/qepy/lattice.py`, every
   `ibrav`, the units of the cards): the same cells as the fork's for the 14
   it has, and YamboPy's tutorial inputs read (MoS₂ P-6m2; bulk hBN
   P6₃/mmc, non-symmorphic, with `force_symmorphic` in scf and nscf; Si
   Fd-3m).
2. k-points: Monkhorst–Pack grids from a spacing, their irreducible count as
   pw.x finds it (spglib, time reversal, `nspin`, `nosym`), band paths from
   `BrillouinZone` (`K_POINTS crystal_b`); the nscf grid for Yambo. **Done**
   (2026-11-23): `core/kpoints.py` (grids from a spacing, `K_POINTS
   automatic`; the Γ-centred nscf grid for Yambo; band paths from
   `BrillouinZone`, `K_POINTS crystal_b`, about a density or a total). The
   count is pw.x's own, not spglib's: pw.x looks only for 32 fixed Cartesian
   rotations (a turned cell loses them: rhombohedral As given as
   `ibrav = 14` keeps the inversion alone), takes fractional translations
   only of 0 or 1/n (n = 2, 3, 4, 6), none in a supercell, and unfolds
   the lattice's reduced grid into the crystal's stars. So `set_sym_bl`,
   `sgam_at`, `kpoint_grid` and `irreducible_BZ` are ported
   (`yambopy_ext/qepy/symmetry.py`, `Structure.pw_operations`), and the
   count matches pw.x on all 108 test-suite benchmarks of QE 7.5 with an
   automatic grid it can count (and Si 4×4×4 on Tirant: 8);
   `scripts/check-kpoints-against-qe.py` runs that comparison. Noncollinear
   magnetic runs give no count (their symmetry depends on the
   magnetization); inputs with `space_group` are not read yet. The pw.x
   reader now skips any text before a namelist, as Fortran does. For task 3:
   a structure written with `ibrav = 0` should be in QE's orientation, or
   pw.x finds fewer operations than spglib (`pw_operations` tells).
3. Writing pw.x inputs (`yambopy_ext/qepy`): from a reader's `PwInput`,
   values kept, names and cards in pw.x's order; every input the reader
   reads written back the same (YamboPy's inputs as tests). **Done**
   (2026-11-23): `write_pw_input`, and `PwInput.set`, `unset`, `set_card`.
   Namelists in the order pw.x reads them, each variable where it was, cards
   in INPUT_PW's order; read back, the same input for all 479 pw.x inputs of
   QE 7.5's test suite and YamboPy's. `core/qe_inputs.py` puts a structure
   (`ibrav = 0`) and k-points into an input, keeping its other settings:
   species keep their order, and the 14 per-species arrays of INPUT_PW
   (`starting_magnetization(i)`, `hubbard_occ(i,j)`, the third index of
   `starting_ns_eigenvalue`...) follow their species; a cell variable
   removed, a species without a pseudopotential, HUBBARD naming a species
   gone, fixed-atom flags lost are told in notes. pw.x tries only rotations
   about QE's axes, so a cell turned away from them loses operations (ASE's
   rhombohedral Bi: 4 of 12): a note says so, and `Structure.oriented_for_pw`
   turns it (spglib's standard orientation; crystal coordinates unchanged),
   left to the user as Cartesian settings (magnetization angles, fields)
   would need turning too. Then (2026-11-23) structures can also go in
   with an ibrav: `set_structure(..., ibrav="auto")` or a given one,
   `Structure.qe_lattice` naming the lattice (spglib's holohedry, as the
   fork's `lattice_data_for_cell`, which does not return it), building
   QE's exact cell from celldm (`latgen`) and re-expressing the atoms in
   QE's vectors (the fork's `find_basis_change`), the basis change's sign
   chosen so a left-handed cell does not mirror a chiral crystal, and the
   space group checked after. pw.x then finds every operation (QE's
   orientation), and the input reads as people write it. Monoclinic and
   triclinic lattices stay at `ibrav = 0` ("auto" falls back, with a note).
4. The pseudopotential library and PseudoDojo picker (decision 3), with
   suggested cutoffs and a warning below a table's low hint. **Done**
   (2026-11-23). The tables are PseudoDojo's "latest version" GitHub
   repositories (ONCVPSP v0.4.1), which hold UPF files: `X_std.upf` and
   `X_str.upf` per element (checked: their psp8 twins have the md5 sums of
   the standard and stringent tables), and the tables' hints (`*.djson`, in
   Ha). PBE and PBEsol, SR and FR, standard and stringent: 8 tables of 71–72
   elements, each pinned to a commit. LDA is left out: its SR table has UPF
   files for 28 elements only. The library (`core/pseudodojo.py`) lives in
   the program's data folder (`~/.local/share/visual-suite/visual-workflows/
   pseudos` on Linux), filled one element at a time; each download is
   checked (a UPF for that element, the table's valence, spin–orbit exactly
   when the table is FR) before it is kept. Copied into a project's
   `pseudos/` as `X.<table>.upf`, with `sources.toml` recording the source
   URL, sha256, valence, hints (Ry), licence (CC BY 4.0) and citation; a
   file edited later loses its hints. Suggested cutoffs: the highest hint
   among the species (normal by default), ecutrho 4 × that. The checks warn
   when ecutwfc is below a species' low hint. PBE FR stringent gives no
   hints for Li, Ge, Sb, Te: their files are used without. The picker
   (`gui/pseudo_picker.py`) shows the table chosen, each element's file and
   hints and whether it is already here, and the cutoffs suggested; task 6's
   editor opens it.
5. The Phase 2 checks made exact: pools against the irreducible k-points; a
   missing `force_symmorphic` an error only when the crystal has fractional
   translations; Yambo's k, c and v role sizes (k-points, occupied bands from
   the valence charges). **Done** (2026-11-23).
   - Pools: `pw_kpoint_count` gives the k-points as pw.x prints them for any
     input (a grid reduced as pw.x does, a list, Γ, a path's points; none
     with Berry phase, electric fields or a noncollinear magnetization), so
     automatic grids are checked exactly (more pools than k-points, uneven
     splits); without a readable structure, the grid's size stays an upper
     bound. The count was made fast (numpy, cached): 18×18×18 for Si in a
     fraction of a second, as the checks run while one types.
   - p2y: without `force_symmorphic` (or `nosym`) in the nscf, an error
     only when pw.x will use operations with fractional translations in
     that cell (diamond Si: 24 of 48); otherwise nothing is said.
   - Yambo roles (from Yambo 5.3's `PARALLEL_structure` and
     `PARALLEL_global_dimensions`): a role given more processes than it
     has elements leaves them all empty and yambo stops ("USER parallel
     structure does not fit"). From the nscf the run's SAVE comes from:
     k is the irreducible k-points (DIP, BS, BZINDX, RT, NL), or the whole
     zone's for the response function (X, X_and_IO; unshifted grids only);
     q the irreducible q-points, or the range `QpntsRX*` gives; c and v the
     empty and occupied bands within the response's `BndsRnX*` and, for the
     dipoles, the narrowest of it and `BSEBands` (not with a terminator).
     Occupied bands come from the pseudopotentials' valence charges, only
     for insulators (fixed occupations, nspin 1 or noncollinear) whose
     pseudopotentials are in the project; otherwise no role is checked
     from them.
   - `QPkrange` beyond the nscf's irreducible k-points: a warning, as yambo
     changes it without a word (a last k beyond becomes the last one, a
     first k beyond becomes 1). `BSSNEig` needs no check: yambo resizes it.
6. GUI: a pw.x input editor (structure, settings, k-points, pseudopotentials;
   the input and the checks as you type), opened from a run in the step
   editor; and "New QE chain…" making scf, nscf (for Yambo) and bands steps
   with consistent `prefix`, `outdir`, `nbnd`, `force_symmorphic`.
   **The editor, done** (2026-11-23): `gui/pw_editor.py` (`PwEditor`),
   opened by **Edit pw.x input…** in the step editor for the selected (or
   only) pw.x run; the text it returns is kept aside and copied into the
   step's folder when the step is saved, as other new inputs (Cancel drops
   it). Tabs: structure (imported with ASE; ibrav kept, 0, or the
   lattice's own; primitive cell; turned for pw.x), settings (the main
   variables as fields, the others kept), k-points (grid from a spacing or
   by hand, with pw.x's count; a band path), pseudopotentials (files per
   species; the PseudoDojo picker; suggested cutoffs, applied at once when
   none is set). A grid from a spacing gives 1 point across vacuum
   (`Structure.vacuum_axes`: a gap of 7 Å or more between the atoms along a
   cell vector), as a CIF of a layer says nothing of its periodicity.
   **New QE chain…, done** (2026-11-23): `core/qe_chain.py` (`QeChain`,
   `chain_inputs`, `add_qe_chain`) and `gui/qe_chain_dialog.py`. From one
   scf input: the scf (`outdir = './out'`), an nscf for Yambo (the scf's
   prefix, `outdir = '../NN-scf/out'`, `nbnd`, the grid unshifted,
   `force_symmorphic` only where pw.x would use fractional translations),
   p2y (`-I ../NN-scf/out/<prefix>.save -O .`, one task, no MPI) and a
   bands run (the same outdir, a band path); each but the scf optional.
   nscf and bands write their wave functions into the same `prefix.save`,
   so bands runs after p2y (after the nscf without p2y); and a chain check
   now makes it an error for p2y to read a bands run's output. Default
   `nbnd`: 4 × the occupied bands (at least 20 more) for the nscf, a few
   empty ones for the bands, when the occupied bands are known
   (`chain.occupied_bands_of`). The chain made is checked at once (none of
   the chain checks fires on it, by construction).
7. CLI: `vwf structure FILE` (symmetry, `ibrav`, k-points for a spacing),
   `vwf pseudos get ELEMENT… --table …`, `vwf project add-qe-chain …`. **Done**
   (2026-11-23): `vwf structure` (any file ASE reads, and pw.x inputs with
   any ibrav as pw.x reads them: symmetry, what pw.x finds and needs
   fractional translations, vacuum, ibrav and celldm, a grid for a spacing
   with pw.x's count, a band path, `--input` for the start of an input),
   `vwf pseudos get` (elements, or `--from` a file; `--xc`, `--fr`,
   `--stringent` choose the table) and `vwf pseudos list`, `vwf project
   add-qe-chain` (as New QE chain…; `--nscf-nbnd` asked for when the
   occupied bands are not known). The chain now sets `max_seconds` from the
   job's time when the input has none (both in the GUI and the CLI).
8. Docs, and a real scf → nscf → bands chain on a cluster. **Done**
   (2026-11-23). Docs: QE's symmetry search as it really is
   (quantum-espresso.md §4), the README's scope and quick start, the
   commands and the editor. The nscf for Yambo now sets `diago_full_acc`.
   The run: bulk Si on Tirant (interactive partition, 8 tasks), made with
   `vwf pseudos get Si` and `vwf project add-qe-chain` (PseudoDojo PBE
   standard, 6×6×6, nbnd 24, the lattice's path), `vwf project check
   --on-cluster` clean; jobs 402912–402915 completed in about a minute.
   pw.x agreed with the suite throughout: scf 48 operations (24 with
   fractional translations), 16 k-points; nscf 24 operations with
   `force_symmorphic`, 16 k-points; bands 149 k-points; p2y wrote the SAVE
   (`ns.db1`; its own log `l_p2y`: 16 k-points, 24 bands, 24 symmetries
   with time reversal). Found on the way: `vwf project log` showed only the
   job's log, while pw.x writes to its own file (`scf.out`) and Yambo's
   programs to theirs (`l_p2y`; the job's log stays empty). Now `vwf
   project log STEP --run N` and the Project tab's log pane (a choice above
   it) show a run's own output: `Runner.run_output` (a QE run's output
   file; for Yambo's programs the newest `l_*` / `l-<job>_*` in their -O
   folder, `LOG/..._CPU_1` with several tasks).

**Later** (not Phase 3):

- Monoclinic and triclinic lattices in the YamboPy fork
  (`lattice_data_for_cell`, `get_lattice_data`: ibrav 12, -12, 13, -13, 14,
  and their Brillouin-zone paths), done in the fork once its pending pull
  request is settled. Visual Workflows then names them too (`ibrav="auto"`)
  and gets their band paths.
- A 3D view of the structure (decision 4), reusing Visual Excitons'
  pyqtgraph/OpenGL code.
- k-point counts for noncollinear magnetic runs (pw.x's magnetic symmetry,
  `sgam_at_mag`, depends on the magnetization's directions).
- Reading inputs with `space_group` and `ATOMIC_POSITIONS crystal_sg`
  (Wyckoff positions).
- PseudoDojo's LDA tables (UPF files for few elements: from the psp8 files,
  converted).

### Phase 4 plan

Yambo inputs made with the program, as pw.x's are since Phase 3: written
from what the SAVE's nscf already tells (k-points, bands, occupied bands,
valence), with the checks of Phases 2 and 3 as one goes; and the QE chain
continued to a BSE that Visual Excitons opens.

**What there is to build on:** the yambo input reader and the names yambo
reads per version, with its command-line options (`yambopy_ext/io`); the
yambo checks (inputs, arguments, parallel roles sized from the nscf, chain
checks, what Visual Excitons needs); the QE chain (scf → nscf → p2y →
bands) and its nscf (`nbnd`, grid, occupied bands); the pw.x editor as a
model for an input editor. Not there: which variables each runlevel brings,
their descriptions and defaults; writing yambo inputs.

**Decided** (2026-11-23):

1. Inputs are made offline, by the program, so a whole chain to the BSE can
   be made in one go before anything runs. A **refresh from the cluster's
   yambo** checks and completes them once p2y has run: the build that will
   run the job writes its own input for the same runlevels against the
   SAVE (`yambo -F … <options>`: it writes the input and stops), and the
   differences are reported (a name that build does not know; variables it
   adds, such as SLEPc's or the parallel ones of an MPI build; its computed
   defaults, as the SAVE's G-vectors). The template is a safeguard and a
   source of exact defaults, not a requirement.
2. Where the refresh runs is a per-cluster choice in the profile: on the
   login node (seconds, no calculation), as a short job on a partition such
   as `interactive` where login nodes must not run programs, or not at all.
3. The chain for excitons offers both: GW (plasmon-pole) then BSE with the
   QP energies, or a cheaper BSE on DFT energies with a scissor shift. Both
   end in what Visual Excitons reads: a diagonalising solver, eigenvectors
   written (`WRbsWF`), QP energies covering `BSEBands` when they are used.
4. The editor shows the variables that matter as a form (bands, cutoffs,
   solver, runlevels), and every other variable as a table, kept as it is;
   the input and the checks beside it, as the pw.x editor.

**Tasks** (about one commit each):

1. What each runlevel brings, per version: the variables and flags yambo
   writes for each runlevel and option (`INIT_activate.F`), with their
   descriptions and units (`INIT_load.F`), extracted by
   `scripts/extract-input-names.py` into `yambopy_ext/io` as the name lists
   are; checked against the inputs yambo wrote in YamboPy's references and
   tutorials. **Done** (2026-11-23):
   `yambopy_ext/io/yambo_runlevels.py` (`input_names(runlevels, values,
   version, mpi=, scalapack=, spin_polarized=, databases=)`: the names yambo
   writes, runlevels first, each with its description, unit and verbosity).
   INIT_activate.F is translated rule by rule (104 rules in 5.3; 97 in
   5.1.1, 109 in 5.4) into conditions of a small language that a parser
   evaluates, allowing nothing else; `#if defined` blocks of other
   executables (yambo_rt, yambo_sc, yambo_nl, yambo_ph) dropped, `_MPI` and
   `_SCALAPACK` kept as the build's. INIT.F adds the runlevels' switches
   (with those set from `Chimod` and `BSKmod`), the runlevels a runlevel
   brings, and `QPkrange`/`QPerange` (written by INIT_QP, not activated);
   INIT_QP_ctl_switch.F the QP controls per section and spin; INIT_load.F
   the descriptions, units and verbosity (the response function's 18 stems
   per kind from Xload). Values decide too: `BSSmod` with d or s brings
   `WRbsWF`, h the Haydock variables; databases already on disk (the
   screening's) change what a BSE asks. YamboPy's reference inputs were
   written by Yambo 4.0.1 and its tutorials' are edited by hand, so the
   check against what yambo writes is done with Tirant's yambo 5.3 and the
   Si SAVE of the Phase 3 run. That check showed that a fresh SAVE (straight
   from p2y) gets only the setup input whatever is asked: yambo indexes the
   SAVE first (`ndb.gops`, `ndb.kindx`), so the chain needs a setup run after
   p2y (task 4) and the refresh runs it when missing (task 5). The setup
   input then matched exactly, basic and `-V all`, once names that no
   executable of the version registers are left out (`EvalMagn`, activated
   but never defined) and `QptCoord` added (written by the setup run before
   the SAVE is indexed, at the `kpt` verbosity). After the setup run, the
   seven inputs yambo 5.3 wrote (GW with plasmon pole, basic and `-V all`;
   BSE with static screening, diagonalisation, Haydock; static screening;
   optics) match name for name and in order, once three more things from
   the source were followed: yambo writing a new input from command-line
   options adds no implied runlevels (INIT.F adds them before the options'
   runlevels are on: a plasmon-pole GW input has no `dipoles`), while a run
   of an input does (`new_input=`); only names registered as input
   variables are written (`RIM_W_diag` is only read, with `parser`); the
   parallel variables and QP controls, built in loops, are verbose (`par`,
   `qp`), with their descriptions evaluated from the loops' strings. Those
   inputs are test data (`tests/data/yambo/tirant-5.3-inputs.txt`).
   Found on the way: the intermittent test
   failure seen since Phase 2 was a tab's timer (a log followed) firing
   after its test, in whichever test came next; closing the window now
   stops every timer of its tabs. And `vwf run` took options placed after
   the cluster's name (`--timeout 600 --`) as part of the remote command.
2. Writing yambo inputs (`yambopy_ext/io`): `write_yambo_input` and edits
   (flags, variables with units, blocks), in yambo's own layout; every input
   the reader reads written back the same (YamboPy's and Yambo's own as
   tests). **Done** (2026-11-23): `write_yambo_input(inp, order=,
   describe=)` and `YamboInput.set_flag`, `set`, `set_block`, `unset`.
   Runlevels and flags first, then variables and blocks (`name= value
   unit`, `% name` rows `|` unit `%`), in an order given (yambo's, from
   `input_names`) or as read, with yambo's descriptions as comments when
   asked; names set take yambo's spelling (it finds a unit only on a line
   spelling the name its way); strings keep whether they were quoted
   (yambo keeps the default for an unquoted one). Refused: a string with
   `#` (yambo cuts it there), with both quotes, unquoted and not one word, a
   number not finite. Read back, the same input for the 73 yambo inputs on
   the development PC (YamboPy's references and tutorials, and the seven
   yambo 5.3 wrote on Tirant); Yambo's repository has no example inputs.
3. Making inputs (`core/`): GW (HF, plasmon-pole screening, Dyson) and BSE
   (optics, kernel, diagonalisation; with QP energies or a scissor) from the
   nscf: band ranges around the gap from the occupied bands, `QPkrange`
   covering `BSEBands`, cutoffs in Ry, `WRbsWF`; parallel roles suggested
   within the sizes Phase 3 checks; what each default is and why, as notes. **Done**
   (2026-11-23): `core/yambo_inputs.py` (`SaveInfo.from_nscf`, `gw_input`,
   `bse_input`, `Made.text()` in yambo's order with its descriptions).
   Starting points for convergence, from YamboPy's tutorials, each with a
   note: response and Green's-function bands all the SAVE has (yambo's
   default); `NGsBlkXp`/`NGsBlkXs` 3 Ry (yambo's default, 1 G-vector, gives
   an unconverged result without a word); `EXXRLvcs`, `VXCRLvcs` left to
   yambo (all G-vectors, exact); `BSENGexx` 10 Ry; the bands 2 below and 2
   above the gap for `QPkrange` and `BSEBands` alike, so the QP energies
   cover the BSE. The BSE computes its own static screening and reads only
   the GW's `ndb.QP` (`KfnQPdb`), or shifts DFT energies by a scissor
   (`KfnQP_E`): through a `-J` link to the GW's folder it would take the
   plasmon-pole screening instead (INIT.F). Parallel roles are split from
   the MPI tasks within each role's size (left to yambo, with a note, when
   they do not fit), so the Phase 3 role check never fires on them. The
   inputs made pass every yambo check, Visual Excitons' included.
4. The chain continued: after p2y, a GW step and a BSE step (or the BSE
   alone with a scissor), linked to the SAVE and to each other (`-J`,
   `KfnQPdb`), consistent by construction and checked at once. **Done**
   (2026-11-23): `QeChain(gw=, bse="gw"|"scissor", scissor_ev=, yambo_bands=,
   yambo_job=)` and `add_qe_chain(..., yambo_version=)`. With GW or a BSE,
   the p2y step also runs yambo's setup (serial, seconds), so the SAVE is
   indexed once (a fresh one gets only the setup input, as task 1 found on
   Tirant) and the yambo steps never rebuild `ndb.kindx` together; the GW
   step and the BSE step link `SAVE` to p2y's and run as jobs `gw` and
   `bse`; the BSE reads the GW's `../NN-gw/gw/ndb.QP` (`KfnQPdb`) or shifts
   DFT energies by a scissor; the bands step comes last. The inputs come
   from task 3, with the names of the yambo the profile's modules give
   (`yambo/5.3-single-omp` → 5.3) and the roles for the yambo steps' tasks.
   Both chains pass the chain checks and every step's own checks.
5. The refresh from the cluster's yambo (decisions 1 and 2): the profile's
   choice, the input yambo writes for the same runlevels against the SAVE,
   compared with ours; `vwf project refresh-input STEP` and an action in
   the editor. **Done** (2026-11-23), but the editor's action (task 6):
   `core/yambo_refresh.py` and `vwf project refresh-input STEP [--run N]
   [--complete]`; the profile's `programs.yambo.refresh` = `login` (default),
   `job` or `off`. The options asking for the same runs come from the
   input's runlevels and values (`-x -p p -g n` for our GW, `-d s -o b -k sex
   -y d` for our BSE); on the cluster, in the step's `.vwf/refresh`, the
   SAVE the step will use is linked (its `SAVE` link resolved: the step's
   own is made only by its job script), yambo's modules loaded as the job
   does, the setup run if the SAVE has no `ndb.kindx`, and `yambo -F ref.in
   <options> -V all` asked; `job` wraps the same lines in `sbatch --wait`
   on the step's partition and QOS. Compared: our names that build does not
   read; the basic ones it writes that ours lacks, with its values (for Si:
   `EXXRLvcs = 7799 RL`, all its G-vectors); `--complete` adds those.
6. GUI: a yambo input editor (decision 4), opened from a yambo run in the
   step editor; New QE chain… continued to GW and BSE.
7. CLI: the chain's GW and BSE steps from the command line
   (`vwf project add-qe-chain --gw --bse` or `--bse --scissor`), and a
   yambo input made for an existing SAVE.
8. ypp: its names per version (its loader, as yambo's), its inputs read and
   checked; the inputs Visual Excitons' real-space views need.
9. Docs, and a real chain to the BSE on Tirant, opened in Visual Excitons.

**Found out** (2026-11-23): every Yambo module on Tirant is an MPI build
(`yambo/5.3-single-omp`: single precision, MPI and OpenMP; also 5.3 single,
double, double-omp, 5.2.0 and a real-time build), so the GW and BSE runs use
the module p2y ran with. Tirant's user manual (July 2025) sends processes
needing more than 10 minutes of CPU to an interactive session (`salloc`
on `interactive`), so shorter ones, as yambo writing an input, may run on
its login nodes (of the compute nodes' architecture): the refresh runs
there.

## 12. Open questions

- Lynx: `/scratch` seems to be local to each compute node (only `compute03`
  there), so not usable from the login node. (Its Spack modules are in
  `/apps/spack/share/spack/modules/linux-arch-x86_64`, named
  `{name}/{version}-{compiler}-{compiler version}-{hash:7}`.)
- A Docker Slurm cluster for end-to-end tests, if the fakes stop being enough.
- Tirant: the `qe/oneapi-2024.2.1` modules (Intel MPI 2021.13) were not
  tried (`qe/7.5` with Intel MPI 2021.14 starts with plain `srun`, settled in
  Phase 2).
- Tirant: its scratch purge policy.
- Tirant: whether outbound connections are restricted on its login nodes.
  (Its manual, July 2025: password login, changed at the first one, no
  one-time codes; processes over 10 minutes of CPU belong in an interactive
  session; `python3` is there, 3.6.)
- Other QE and Yambo programs (2026-11-21): today pw.x and yambo inputs are
  checked fully, p2y and ypp command lines only, other programs run
  unchecked. In order of value for excitons: ypp inputs (Visual Excitons'
  real-space and analysis views; names from Yambo's ypp loader with the same
  extraction script), ph.x (exciton–phonon: images over q-points and
  irreducible representations, consistency with its pw.x run), projwfc.x
  (orbital character). ypp with Phase 4's Yambo inputs, ph.x with
  exciton–phonon workflows. Not planned: programs outside
  the excitons workflow (neb.x, cp.x, plotting tools).
- Pseudopotential sets: settled in Phase 3, task 4 (PseudoDojo's PBE and
  PBEsol tables, downloaded on demand). LDA could come from its psp8 files
  converted to UPF, if needed.
- Hand-off to Visual Excitons (Phase 5). Visual Excitons (2026-11-21)
  recognises a project folder by its `project.toml` (its `format` kind and
  `steps` list only) and searches each step's `outputs/`, 3 levels deep
  within it: `SAVE` inside `prefix.save` and `-J` names with folders are
  found (the first `-J` name only warns beyond 3 levels). With several
  candidates of a kind (two BSE steps), it asks which to open, the first in
  the project's order offered. `visual-excitons <project> [--save DIR]
  [--bse DIR] [--qp PATH]` opens one from the command line (exit code 2 and
  a message on stderr when a path holds the wrong thing), asking only for
  what was not given. The hand-off is then an "Open in Visual Excitons"
  action running that command, with `--bse` for the step it is started
  from. The p2y step still runs `p2y -I <nscf's prefix.save> -O .` (checked
  in Yambo's source, 5.1.1 and 5.3.0), for a tidy `outputs/SAVE`. A lighter
  `excitons` download without `ns.wf*` (gigabytes) would do for spectra and
  composition.
- Whether the suite becomes one program with modules or stays separate
  programs sharing a library — affects how much of the GUI framework is
  shared with Visual Excitons.
