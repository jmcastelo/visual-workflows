# Development

[← Documentation](../README.md#documentation)

How to set up a development copy, the tests, recording a new cluster, and
extending the program. How the code is organised is in
[architecture.md](architecture.md).

## Setting up

```
git clone https://github.com/jmcastelo/visual-workflows.git ~/visual-workflows
cd ~/visual-workflows
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

The editable install makes your changes take effect without reinstalling. Run
the program with `visual-workflows` (or `python -m visual_workflows.gui.main`)
and the command line with `vwf` (or `python -m visual_workflows.cli`).

**Version:** `visual_workflows/__init__.py` (`__version__`), read by the
packaging, `vwf --version` and the About box. Dependencies and their minimum
versions are in `pyproject.toml`.

## Tests

```
pytest                     # everything, ~10 s
pytest tests/test_slurm.py # one file
pytest -k excitons         # by name
```

No cluster, Docker or network is needed: the tests replace the cluster in
four ways.

| Fake | File | Used for |
|---|---|---|
| An SSH server inside the test process (asyncssh) | `tests/sshserver.py` | `SSHTransport` for real: every login method, passphrases, one-time codes, host keys, SFTP, reconnection, timeouts |
| `FakeTransport`: answers commands from recorded outputs | `tests/fakes.py` | Slurm and module parsing and commands, the CLI, the GUI's cluster tab |
| `LocalTransport`: commands and files on this PC, in a temporary "home" | `core/transport.py` | projects, uploads, downloads, logs, the module load check (with a fake `module` and `ldd`) |
| `LocalSlurm`: a scheduler running job scripts with bash | `tests/fakes.py` | submitting, dependencies, status lines, cancel, time limits, final states |

The lists of names pw.x and yambo read (`yambopy_ext/qepy/pw_namelists.py`, `yambopy_ext/io/yambo_variables.py`) are extracted from the programs' sources: for a new release, clone Quantum ESPRESSO and Yambo and run `scripts/extract-input-names.py QE_REPO YAMBO_REPO --qe qe-7.3.1 … --yambo 5.1.1 …` with the tags to cover (it reads them with `git show`, nothing is checked out); then run the tests, which pin known names.

The readers in `yambopy_ext/` are tested in `tests/yambopy_ext/` with plain inputs, without Qt or fakes, so that they can move to YamboPy with their tests.

`FakeTransport` answers with the longest matching command prefix; unknown
commands get exit code 127. `LocalSlurm` sets the variables Slurm gives a job,
signals the whole process group on cancel (as Slurm does), and with
`forget=True` drops ended jobs at once, as Slurm does after `MinJobAge`, so
states must come from status files. `blocking=True` runs each job to its end
inside `submit()`, for callers that start an event loop per command (the CLI
tests).

### Isolation

Tests must never touch your real keyring, configuration, ssh-agent or
settings. `tests/conftest.py` swaps the keyring for a `MemoryKeyring`, points
`XDG_CONFIG_HOME` to a temporary folder and removes `SSH_AUTH_SOCK`, for every
test. GUI tests also point `QSettings` to a temporary folder. Scripts you
write that create the GUI or settings must do the same **before** creating
them.

### Async and GUI tests

Async tests are plain `async def` functions (`asyncio_mode = "auto"`).

GUI tests (`tests/test_gui.py`) run on Qt's `offscreen` platform with a real
`qasync` loop, from the `loop` fixture:

- Drive coroutines with `loop.run_until_complete(...)`.
- **Run anything that starts jobs inside one coroutine**, so the loop keeps
  running while they run: tasks created at the end of one
  `run_until_complete` would otherwise start outside a running loop.
- The fixture closes the loop at the end, while Qt still exists.
- Answer a dialog with `QTimer.singleShot(ms, callback)` before awaiting it.

To look at a window without a display, render it offscreen and save
`window.grab()` as an image.

### Test data

- `tests/data/lynx/`: command outputs recorded on Lynx (Slurm 26.05,
  Environment Modules 5.3), anonymised. See its README.
- `tests/data/synthetic/`: outputs written from Slurm's documentation, for
  what no recorded cluster has yet (accounting). Replace them with recordings
  when a cluster with those features is surveyed.

## Recording a new cluster

1. Copy the survey scripts to the cluster and run them on its login node:

   ```bash
   scp scripts/cluster-survey.sh scripts/cluster-survey-jobs.sh CLUSTER:
   ssh CLUSTER 'bash -l cluster-survey.sh [extra module folders…] && bash -l cluster-survey-jobs.sh [partition]'
   scp 'CLUSTER:cluster-survey*.tar.gz' scratch/
   ```

   `cluster-survey.sh` records Slurm, partitions, accounting, modules
   (including `module show` of QE and Yambo), Python, tools and disk, without
   changing anything. `cluster-survey-jobs.sh` submits four tiny jobs (1 CPU,
   100 MB, about 4 minutes in all) to record how Slurm reports held, running,
   failed, timed-out and cancelled jobs. Have a job queued or running while
   the first one runs, so `squeue` has something to show.

2. Unpack in `scratch/` (ignored by git: raw outputs contain personal data).

3. **Anonymise** into `tests/data/<cluster>/`, keeping only each command's
   output (the scripts wrap it between `----` lines, and `module show` prints
   dashes too: cut at the first and the last separator). Replace:
   - your username and other people's (home folders, module paths, job
     owners) with `alice`, `bob`…;
   - numeric user and group ids (`UserId=…(1004)`, `"user_id": 1004`,
     `SLURM_JOB_UID=`…) with `2001`.

   Then check: `grep -rn` for the real names and ids must find nothing. Job
   ids, node, partition and module names may stay. Add a README saying what
   was recorded, when, and what was replaced.

4. Write tests against the new data, as `tests/test_slurm.py` and
   `tests/test_modules.py` do for Lynx.

5. Note in [design.md](design.md#phase-0-cluster-surveys) what the cluster
   taught.

## Trying it on a real cluster

Before a release, on each cluster at hand:

```bash
vwf test CLUSTER && vwf partitions CLUSTER && vwf jobs CLUSTER
vwf modules CLUSTER --check <the profile's modules> --exe <executables>
```

then a small project with two steps, one depending on the other and one
failing (`exit 3`), followed with `watch`, read with `log`, downloaded with
`get`; and once more `vwf project status` after Slurm has forgotten the jobs
(5 minutes or more), to check the status files.

## Extending

**A scheduler.** Implement `Scheduler` (`core/scheduler/base.py`), mapping its
states onto `JobState`; add it to `scheduler_for()` and to `SCHEDULERS` in
`core/profile.py`. The status lines (`jobstatus.py`) use `SLURM_*` variables:
generalise them. Record the scheduler's outputs as test data first.

**A program.** Add its module names to `PROGRAM_ALIASES` (`core/modules.py`),
its default executables to `cluster_editor.py`, and its wave function or
result patterns to `core/transfer.py` if downloads should treat them
specially.

**A profile setting.** Add the field to `ClusterProfile` (or `Program`,
`Partition`), read it in `from_dict` with a default (old files lack it), write
it in `to_dict`, check it in `validate`, show it in `cluster_editor.py`, and
document it in [clusters.md](clusters.md). Do not change the meaning of
existing keys; a change that old versions would misread needs a new format
version.

**A command.** Add a `cmd_…` function and its parser in `cli.py`, using the
core only; test it with the `vwf` fixture of `tests/test_cli.py`.

**A GUI action.** A synchronous slot that asks what it must (dialogs) and
launches a coroutine with `launch()`; see [the async rules](architecture.md#async-model).

## Conventions

- Names users see, settings keys and format keys come from `identity.py`.
- Messages say what happened and what to do, without jargon where possible.
- Every feature comes with tests; behaviour learnt from a cluster comes with
  its recorded output.
- Documentation is updated with the change it describes.
