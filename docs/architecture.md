# Architecture

[← Documentation](../README.md#documentation)

How the code is organised and why. The decisions behind it, with their
reasons and the clusters they were checked on, are in [design.md](design.md);
setting up and testing are in [development.md](development.md).

## Overview

```
PC                                          Cluster (login node)
┌──────────────────────────────┐   SSH   ┌──────────────────────────────────┐
│ gui/   PySide6, on qasync    │◄───────►│ bash -lc '<command>'             │
│ cli.py `vwf`, asyncio.run    │ one     │   sbatch squeue scontrol sinfo   │
│ core/  no Qt, all async:     │ conn.   │   sacct sacctmgr scancel         │
│   transport ssh scheduler    │ per     │   module  find  ldd  printf      │
│   modules profile project    │ cluster │ SFTP: files                      │
│   runner jobstatus transfer  │         │                                  │
│   monitor credentials        │         │ nothing installed                │
└──────────────────────────────┘         └──────────────────────────────────┘
```

Three layers, each using only the ones below:

- **`core/`** does everything: connections, Slurm, modules, profiles,
  projects, downloads. It imports no Qt, so it is used as it is by the
  command line, the GUI and the tests.
- **`cli.py`** is a thin command-line front end on the core.
- **`gui/`** is a PySide6 front end on the core.

Nothing runs on the cluster but standard commands, one per remote action, over
one SSH connection per cluster. Every remote action is `async`, so a slow
cluster never blocks the window.

## Modules

| Module | Responsibility |
|---|---|
| `identity.py` | Every name that a rename of the suite would change: app name, settings keys, keyring service, file `format` keys and suffixes, default remote folder. |
| `core/transport.py` | `Transport`: run a command, stat/list/read/write files, upload, download (resumable), read byte ranges. Errors: `TransportError` and its kinds. `LocalTransport`: the same on this PC (tests, local runs). |
| `core/ssh.py` | `SSHTransport` (asyncssh): `ConnectionSettings`, the four login methods, `~/.ssh/config`, host-key checks, the `AuthPrompter` protocol (how to ask the user), SFTP. |
| `core/credentials.py` | `CredentialStore` on the system keyring; `MemoryKeyring` for tests. |
| `core/shell.py` | `login_shell()` and quoting. |
| `core/paths.py` | The program's folders on the PC. |
| `core/profile.py` | `ClusterProfile` (with `Partition`, `Program`): TOML load/save, validation, export without personal fields; `ProfileStore`. |
| `core/scheduler/base.py` | `Scheduler` interface and its data: `Job`, `JobState`, `Partition`, `Node`, `Association`, `Qos`. |
| `core/scheduler/slurm.py` | `Slurm`: the commands and their parsers (JSON and text). |
| `core/modules.py` | `Modules`: module system, listing, `module show`, `ModuleName` (readable names, Spack), the load check (`which` + `ldd`). |
| `core/project.py` | `Project`, `Step`, `JobRecord` and their TOML files. |
| `core/jobspec.py` | `JobSpec` and `Run`: a step's `[job]` table; `check()` against the cluster profile (partition, time, memory, cores, programs and executables) giving errors and warnings; `render()` writes the job script. |
| `core/jobstatus.py` | The status lines added to job scripts, status files, `final_state()`: how a job ended. |
| `core/runner.py` | `Runner`: a project on its cluster: upload, submit with dependencies, refresh, cancel. |
| `core/transfer.py` | Downloads: `scan` (one `find`), `choose` (modes, patterns), `plan` (against `outputs/`), `fetch` (resumable). |
| `core/monitor.py` | `watch()` (polling with a floor) and `LogFollower`. |
| `cli.py` | `vwf`: argparse commands calling the core; `TerminalPrompter`. |
| `gui/asyncqt.py` | Running core coroutines from Qt: `launch()`, `ask()`, `confirm()`, error boxes, `GuiPrompter`, `settings()`. |
| `gui/session.py` | `Session`: one cluster's connection, scheduler and modules, shared by the views. |
| `gui/main_window.py` | Cluster selector, menus, the four tabs, sessions, recent projects. |
| `gui/cluster_editor.py`, `jobs_view.py`, `files_view.py`, `project_view.py` | The tabs. |

## Main flows

### Connecting and logging in

`SSHTransport.connect()` resolves the host through `~/.ssh/config`, reads
`known_hosts`, and calls `asyncssh.connect` with a client whose callbacks ask
a `_Login` object for secrets. `_Login` tries a remembered secret first (from
the `CredentialStore`), then asks the `AuthPrompter` (terminal or dialogs).
Secrets the user asked to remember are stored only after a successful login;
remembered ones the server refused are deleted.

Key files are read by the program (`read_private_key`) so that a passphrase is
asked only for encrypted keys. An unknown host key raises `HostKeyError`;
`connect()` then fetches the server's key, refuses if `known_hosts` has a
different one (`changed=True`), otherwise asks the user and appends it.

Every command runs as `bash -lc '<command>'` (`shell.login_shell`), so the
cluster's `module` and environment are there. A dropped connection is reopened
by the next operation; **commands are never retried**, since a repeated
`sbatch` would submit twice.

### Submitting a project

`Runner.submit(steps)`:

1. `refresh()`, so dependencies are judged on current states.
2. Steps in project order; for each, its dependencies: completed → nothing,
   active → `afterok:<id>`, never run or failed → `ProjectError`.
3. `upload()`: the step's inputs (everything in its folder but `step.toml`,
   `outputs/` and hidden files), and the job script through
   `jobstatus.add_status_lines()`, written straight to the cluster.
4. `Scheduler.submit()` in the step's remote folder; a `JobRecord` (with its
   output file name, from the script's `--output`) is appended and saved.

### Following jobs

`Runner.refresh()` asks Slurm once (`squeue --states=all`: queued, running
and recently ended jobs) for every active step. For jobs Slurm no longer
knows, it asks accounting once (`sacct`, if the cluster keeps it), then reads
each job's status file and output file. `jobstatus.final_state()` decides,
preferring Slurm, then accounting, then the status file with Slurm's messages
in the output. A record is `final` once ended; final records are never asked
about again.

`monitor.watch()` repeats `refresh()` at an interval never under 30 s, until
no step is active. `LogFollower` reads a file from where it stopped (`stat`,
then `read_range`), starting again if the file shrank.

### Downloading

`transfer.scan()` lists a step's remote folder with one
`find -printf '%s %T@ %P\0'` (NUL-separated, so any file name works);
`choose()` applies the mode and patterns, giving each skipped file a reason;
`plan()` compares with `outputs/` (same size and time ±2 s: already there) and
finds partial `.vwf-part` files to resume; `fetch()` downloads into the
`.vwf-part` file from its size on, checks the final size, sets the remote
time, and renames it.

### Checking modules

`Modules.check()` sends one shell line (`load_check_command`) that purges,
adds module folders, loads the modules, lists them, and for each executable
prints `command -v` and the `not found` lines of `ldd`, each prefixed by an
`@@` marker; `parse_load_check()` reads the markers, separating missing
libraries from wrong versions.

## Async model

The core is `asyncio` code. The command line runs each command with
`asyncio.run()`. The GUI runs Qt's event loop and asyncio's as one, through
`qasync`, so coroutines and Qt signals mix freely.

Rules in the GUI (`gui/asyncqt.py`):

- **Start coroutines with `launch(widget, coro, title)`.** It keeps a reference
  to the task and turns errors into a message box: the expected ones
  (`TransportError`, `SchedulerError`, `ProfileError`, `ProjectError`,
  `ValueError`) as they are, others with their traceback under Show Details.
- **No blocking dialogs inside a coroutine.** `exec()` or the static
  `QFileDialog`/`QMessageBox` functions start a nested event loop, which
  re-enters asyncio. Inside coroutines use `await ask(dialog)` or
  `await confirm(...)`; ask for files in the synchronous slot and pass the
  answer to the coroutine (as `FilesView._download_clicked` does).
- **Connect buttons and actions through lambdas** when the slot has optional
  arguments: Qt passes `checked` to them otherwise.
- **One `Session` per cluster**, created on demand and shared by every view.
  `Session.ready()` connects if needed and returns the transport and
  scheduler. Saving a profile with other connection settings marks the session
  stale: the next use reconnects.

## Talking to Slurm and modules

- **JSON when available, text otherwise.** Job information comes from
  `squeue --json`/`scontrol show job --json` (Slurm ≥ 21.08) and falls back to
  fixed text formats; the first unreadable JSON switches the session to text.
  Partitions and nodes come from text formats stable across versions
  (`scontrol show partition -o`, `sinfo -N -o`). `sacct`/`sacctmgr` are read as
  `-nP` text.
- **Accounting is found out, not assumed:** one `sacct` call per session.
- **Parsers are tested on recorded outputs** (`tests/data/lynx/`); anything
  written without a recording is marked as such (`tests/data/synthetic/`).
- **Polling has a floor** of 30 s, and one `squeue` serves all steps.
- **Safety:** every value put into a command is quoted (`shell.quote`); job
  ids are checked against `^\d+(_\d+)?$` before use.

## Files and compatibility

- **TOML** for cluster profiles (`*.cluster.toml`), `project.toml` and
  `step.toml`: readable, diffable, editable by hand.
- **`format = "<kind>/<version>"`** first in each file, never the program's
  name: files shared between people survive a rename. A newer version loads
  with a warning and keeps its format key.
- **Unknown keys are kept** when a file is saved again (each object keeps
  the data it was read from, `_raw`, and updates it).
- **Atomic writes:** to a temporary file, then `os.replace`.
- **No secrets in files.** Passwords and passphrases go only to the keyring.
- **Names in one place:** `identity.py`. A rename of the suite changes that
  module and the package name.

## Errors

| Exception | Raised for |
|---|---|
| `TransportError` | connection or remote operation failures; subclasses `AuthenticationError`, `HostKeyError` (`.changed`), `CommandTimeout` |
| `SchedulerError` | a Slurm command failed; the message includes Slurm's |
| `ProfileError` | a profile file that cannot be read |
| `ProjectError` | a project or step that cannot be read, created or run as asked |
| `ValueError` | invalid input (e.g. a job id) |

Messages are written for the user: they say what happened and, when there is
one, what to do.
