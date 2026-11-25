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
| `core/paths.py` | The program's folders on the PC (settings; data, as the pseudopotential library). |
| `core/profile.py` | `ClusterProfile` (with `Partition`, `Program`): TOML load/save, validation, export without personal fields; `ProfileStore`. |
| `core/scheduler/base.py` | `Scheduler` interface and its data: `Job`, `JobState`, `Partition`, `Node`, `Association`, `Qos`. |
| `core/scheduler/slurm.py` | `Slurm`: the commands and their parsers (JSON and text). |
| `core/discovery.py` | What Discover finds for a profile: the partitions the user may use with their limits, and, with accounting, the accounts and QOS giving access to them; shared by the GUI and `vwf clusters discover`. |
| `core/report.py` | A cluster's connection report in a few lines (`vwf test`, Test connection): one text for both. |
| `core/recording.py` | Recording a cluster for the tests: the program's own commands and their answers, with a manifest the tests' fake cluster replays (`tests/fakes.py`). |
| `core/modules.py` | `Modules`: module system, listing, `module show`, `ModuleName` (readable names, Spack), the load check (`which` + `ldd`), remembering the ones that passed. |
| `core/project.py` | `Project`, `Step`, `JobRecord` and their TOML files; steps with a job description (`add_job_step`, `write_script`, `script_edited`, `keep_script`). |
| `core/jobspec.py` | `JobSpec` and `Run`: a step's `[job]` table; `check()` against the cluster profile (partition, time, memory, cores, programs and executables) giving errors and warnings; `render()` writes the job script. |
| `core/parallel.py` | Parallelisation checks of pw.x runs (command-line options as pw.x reads them, divisibility, the linear-algebra grid, pools against the k-points pw.x keeps, `max_seconds`) and yambo runs (roles per section, products, role sizes, threads, linear-algebra counts, Yambo 4 names), as `Problem`s. |
| `core/input_checks.py` | Checks of pw.x inputs (namelists and names per version, values, lattice, spin, cards) and yambo inputs and command lines (unknown names, unquoted strings, units and their spelling, block columns, runlevel combinations, solvers, `Lkind`, `BSKmod`, cutoff, `KfnQPdb`; options that make yambo edit its input instead of running), as `Problem`s. |
| `yambopy_ext/qepy/pw_namelists.py`, `yambopy_ext/io/yambo_variables.py` | The names pw.x and yambo read per version, and yambo's options, extracted from their sources by `scripts/extract-input-names.py`. |
| `core/chain.py` | Checks across a chain of steps: what each run writes and reads as project paths (links followed), readers matched with writers before them, then the scf → nscf → p2y → yambo rules and what Visual Excitons needs; fractional translations as pw.x finds them, and Yambo's role sizes from the nscf (k-points, q-points, occupied and empty bands). |
| `core/pseudos.py` | Where pw.x finds a run's pseudopotentials (`pseudo_dir`, `$ESPRESSO_PSEUDO`: the project's `pseudos/` or the profile's library, `~/espresso/pseudo`); their headers here or, at submission, on the cluster; PseudoDojo's hints for the project's files whose source is recorded. |
| `core/pseudodojo.py` | PseudoDojo's norm-conserving tables (PBE, PBEsol; SR, FR; standard, stringent), pinned to commits: the library on this PC filled as needed (each file checked), copies into a project's `pseudos/` with `sources.toml` (source, checksum, cutoff hints, licence), suggested cutoffs. |
| `yambopy_ext/qepy/upf.py` | The header of a UPF file (UPF 1, UPF 2, the schema form): valence charge, kind, spin–orbit, functional, cutoffs. |
| `core/structure.py` | `Structure`: a crystal for pw.x inputs, read with ASE or from a pw.x input (any `ibrav`), its symmetry (spglib: space group, operations needing fractional translations, primitive idealised cell, the Bravais lattice; the operations pw.x itself will use; the crystal in QE's cell for an ibrav), its vacuum (a layer's cartesian cutoff direction, as yambo's slab cutoff needs it; the G-vector shells RIM-W reaches), written as pw.x cards. |
| `core/kpoints.py` | k-points for pw.x: Monkhorst–Pack grids (from a spacing; Γ-centred for Yambo), the irreducible count as pw.x finds it (`kpoint_grid` and `irreducible_BZ` ported; for any pw.x input: `pw_kpoint_count`), band paths from the YamboPy fork's `BrillouinZone` as `K_POINTS crystal_b`. |
| `core/qe_inputs.py` | A structure and k-points put into a pw.x input (`ibrav = 0`, or the lattice's ibrav and celldm, the species' order and per-species values kept, fixed-atom flags kept when the atoms are the same), with notes on what else changed; a note when the cell's orientation costs pw.x symmetry operations. |
| `core/qe_chain.py` | The chain for Yambo made from an scf input: scf, nscf (unshifted grid, empty bands, `force_symmorphic` where needed), p2y with yambo's setup, GW and BSE (on GW energies or a scissor), bands, as steps with consistent `prefix`, `outdir` and links, in an order that keeps the nscf's wave functions for p2y. |
| `core/yambo_inputs.py` | GW (plasmon pole) and BSE inputs (QP energies from the GW, or a scissor, given or fitted) made from what the SAVE will hold (`SaveInfo` from the nscf): bands around the gap (twice with spinors), starting cutoffs, a layer's Coulomb cutoff with RIM-W (its screening raised to what RIM-W reaches) and the field in its plane, the terminator and the BSE at every Q as options, what Visual Excitons needs, parallel roles within their sizes, a large BSE's warning, with notes on each value. |
| `core/results.py` | Results read from what was downloaded, without yambo or ypp: the SAVE's `ns.db1` (bands, gaps on the grid), `ndb.QP` (GW corrections, Z), `ndb.BS_diago_Q*` (excitons, oscillator strengths, absorption peaks rebuilt; every Q in order, the dispersion); consistency checks (a gap GW closed, Z out of range, excitons above the transitions or bound unusually, more for bulk than for layers). |
| `core/diagnosis.py` | Why a job ended badly: Slurm's state and exit code, the programs' own stops (pw.x's `errore`, yambo's `[ERROR]`), known Slurm, MPI, shell and module messages; on the cluster, the job's log and its runs' outputs written by that job (yambo's `_01`, `_02` logs of a run again), and a yambo that wrote nothing traced to a database a stopped job left half-written. |
| `core/excitons.py` | Opening a project's results in Visual Excitons: the folders a step's results came from (`--save`, `--bse`, `--qp`), or the project. |
| `core/bands.py` | A downloaded SAVE's bands: energy ranges, gaps, the density of states, the bands the transitions up to an energy need and the BSE's size; degenerate sets split by a window; the windows grown to whole degenerate sets at the gap's edges (`bands --fit`). |
| `core/studies.py` | Convergence studies: a step per value of one yambo variable, copied from a GW or BSE step; the quantity read back from each, its tolerance (decision 7) and the recommended order, with the next variable for a step. |
| `core/scissor.py` | A scissor fitted to a GW's `ndb.QP` as yambo's `KfnQP_E` applies it (shift and two stretches), with its errors and the gaps it gives; set in a BSE step's input. |
| `core/yambo_refresh.py` | The refresh from the cluster's yambo: the input it writes for the same runs against the step's SAVE (on the login node or as a short job), compared with the step's input (names it does not read, what it adds and its values), and completed. |
| `yambopy_ext/qepy/lattice.py` | QE's `latgen` and `abc2celldm` ported (every `ibrav`), and the cell and positions of a pw.x input in bohr. |
| `yambopy_ext/qepy/symmetry.py` | pw.x's own symmetry search ported (`set_sym_bl`, `sgam_at`, `find_sym`): the operations pw.x uses for a cell as given, fewer than spglib's when the cell is turned or a translation is not 1/n. |
| `yambopy_ext/io/yambo_runlevels.py` | What yambo writes in its input for given runlevels, per version: rules translated from its source (INIT_activate.F, INIT.F), evaluated by a parser of a small condition language; each name's description, unit and verbosity. Regenerated by `scripts/extract-input-names.py`. |
| `core/problems.py` | `Problem`: an error or a warning from the checks. |
| `yambopy_ext/qepy/pw_input.py`, `yambopy_ext/io/yambo_input.py` | Readers of pw.x and yambo inputs, written for YamboPy (its conventions, standard library only): namelists and cards; runlevels, variables and blocks. Both also edit and write inputs (`write_pw_input`, `write_yambo_input`: read back, the same input). See `yambopy_ext/__init__.py`. |
| `core/jobstatus.py` | The status lines added to job scripts, status files, `final_state()`: how a job ended. |
| `core/runner.py` | `Runner`: a project on its cluster: upload, submit with dependencies, refresh, cancel. Before submitting, `prepare()` checks steps with a job description (hand edits: `ScriptEdited`; errors in the description or the module test: `SubmitRefused`). |
| `core/transfer.py` | Downloads: `scan` (one `find`), `choose` (modes, patterns), `plan` (against `outputs/`), `fetch` (resumable). |
| `core/monitor.py` | `watch()` (polling with a floor) and `LogFollower`. |
| `cli.py` | `vwf`: argparse commands calling the core; `TerminalPrompter`. |
| `gui/asyncqt.py` | Running core coroutines from Qt: `launch()`, `ask()`, `confirm()`, error boxes, `GuiPrompter`, `settings()`. |
| `gui/session.py` | `Session`: one cluster's connection, scheduler and modules, shared by the views. |
| `gui/main.py` | Starting the GUI: Qt's event loop runs asyncio too (qasync). |
| `gui/sizing.py` | Every window's spinners, combo boxes and buttons sized to their contents, once, when first shown (an application-wide event filter): text fields and tables stretch, rows of fixed widgets keep their spare room at the end. |
| `gui/main_window.py` | Cluster selector, menus, the four tabs, sessions, recent projects. |
| `gui/cluster_editor.py`, `jobs_view.py`, `files_view.py` | The Cluster, Jobs and Files tabs. |
| `gui/project_view.py` | The Project tab: the project's own actions on top; its steps as a tree (a study's copies under their base step); the selected step's state, the actions that fit it, and its output, input, script and results; the windows for results, diagnosis, bands and studies. |
| `gui/bands_view.py` | `BandsDialog`: a SAVE's bands report, the transitions up to an energy as one types it, the density of states drawn, Fit the project's windows. |
| `gui/study_view.py` | `StudiesDialog` and `NewStudyDialog`: studies made in the recommended order, their results as a table and a plot with the tolerance shaded. |
| `gui/step_editor.py` | `StepEditor`: a step's job description (runs, resources with the partition's limits, links, lines) with the job script and the checks' results updated as you type; a pw.x run's input edited in `PwEditor`, kept aside until the step is saved. |
| `gui/pseudo_picker.py` | `PseudoPicker`: a PseudoDojo table for a structure's elements, with each file's hints and the cutoffs they suggest; downloads into the library and copies into the project. |
| `gui/pw_editor.py` | `PwEditor`: a pw.x input's structure (imported, its symmetry, ibrav), main settings, k-points and pseudopotentials, with the input and the checks shown as you go; opened from the step editor. |
| `gui/qe_chain_dialog.py` | `QeChainDialog` (Add ▸ QE chain to excitons…): in five sections, the scf input made in `PwEditor`, the nscf, p2y, bands, GW and BSE steps with their defaults (grid, `nbnd` and the band window from the occupied bands; a layer's cutoff, RIM-W; the terminator, the BSE at every Q as options), one set of resources. |
| `gui/yambo_editor.py` | `YamboEditor`: a yambo input's runlevels, main variables as a form, every other variable as a table with yambo's descriptions, the checks, and the refresh from the cluster's yambo; opened from the step editor. |

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
