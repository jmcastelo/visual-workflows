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
    transport.py       Transport interface; SSHTransport (asyncssh); LocalTransport (tests)
    credentials.py     keyring access, ~/.ssh/config, host-key checks
    shell.py           running commands in a login shell, quoting, timeouts
    helper.py          uploading and calling the remote helper
    modules.py         Environment Modules: detect Lmod/Tcl, list, test a load
    scheduler/
      base.py          Scheduler interface, JobState, Partition, JobSpec
      slurm.py         Slurm implementation
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
  are unaffected (they live in Slurm, not in the connection).

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
    async def accounting(self, job_ids: list[str]) -> dict[str, JobRecord]
    def render(self, spec: JobSpec) -> str              # #SBATCH header
```

Slurm implementation:

| Need | Command | Notes |
|---|---|---|
| Version | `sinfo --version` | decides whether `--json` is available (Slurm ≥ 21.08) |
| Partitions, nodes | `sinfo --json`, else `sinfo -h -o <fields>` | limits: `scontrol show partition -o` |
| Accounts, QOS | `sacctmgr -nP show assoc user=$USER format=...` | may be restricted; then from the profile |
| Submit | `sbatch --parsable [--dependency=afterok:…] script` | the id is the first field |
| Running/queued jobs | `squeue --json --jobs=…`, else `squeue -h -o` | |
| Finished jobs | `sacct --json -j …`, else `sacct -nP` | exit code, elapsed, MaxRSS, reason |
| Cancel | `scancel id…` | |

Both parsers (JSON and text) are implemented; the text one is the fallback for
old Slurm versions and is tested against recorded outputs.

**Polling:** one `squeue` call for all of the user's active jobs, at most every
60 s (configurable, minimum 30 s), and `sacct` once when a job leaves the
queue. Polling stops when no job is active. Admins notice heavy polling, so
manual refresh is rate-limited too.

**Job states:** `draft → submitted → pending → running → completed | failed |
cancelled | timeout | out_of_memory | unknown`. Slurm states map onto these;
the raw Slurm state and reason are kept for display.

## 6. Environment Modules

- Detect the system: `module --version` / `$LMOD_VERSION` (Lmod) or Tcl
  modules (`$MODULESHOME`).
- List: Lmod `module -t avail` / `module spider` (terse output); Tcl
  `module -t avail`. Parsed by the helper, with a fallback in `core`.
- **Test a load before submitting:** run `module purge; module load …;
  which pw.x yambo` on the login node, and report missing executables. This
  catches most "module not found" job failures in seconds instead of after
  queueing.
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

[programs.qe]
modules = ["intel/2023", "qe/7.3"]
preamble = []
mpi_launcher = "srun"           # "srun" | "mpirun"
executables = { pw = "pw.x", ph = "ph.x" }

[programs.yambo]
modules = ["intel/2023", "yambo/5.2"]
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

### Phase 0: Lynx survey

Commands to run once on Lynx, saving their outputs into `tests/data/lynx/`:

```bash
sinfo --version
sinfo --json | head -c 2000; sinfo -s
scontrol show partition -o
squeue --json --me | head -c 2000
sacct --json -S now-7days | head -c 2000
sacctmgr -nP show assoc user=$USER
module --version 2>&1; echo "LMOD=$LMOD_VERSION MODULESHOME=$MODULESHOME"
module -t avail 2>&1 | head -50
python3 --version
echo $HOME; df -h $HOME; quota -s 2>/dev/null
ls / ; ls /scratch 2>/dev/null | head
```

### Phase 1 tasks

1. `identity.py`, `core/transport.py`: `Transport` interface; `SSHTransport`
   (connect with key, agent or password; `~/.ssh/config`; known hosts;
   `run(cmd) -> (code, out, err)` in a login shell; SFTP get/put/listdir/stat);
   `LocalTransport` for tests.
2. `core/credentials.py`: keyring with a "remember" option.
3. `core/profile.py`: load/save/validate cluster profiles (TOML, `format` key,
   unknown keys kept).
4. `core/scheduler/slurm.py`: version detection; partitions, submit, cancel,
   states, accounting, with JSON and text parsers; tests on Lynx outputs.
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

- Lynx: Slurm version (`--json`?), Lmod or Tcl, Python version, scratch
  policy (purged?), whether accounts/QOS are needed.
- Tirant: authentication (one-time codes?), and whether outbound connections
  or `python3` are restricted on its login nodes.
- Pseudopotential sets to ship or download (SSSP, PseudoDojo; Yambo needs
  norm-conserving).
- Whether the suite becomes one program with modules or stays separate
  programs sharing a library — affects how much of the GUI framework is
  shared with Visual Excitons.
