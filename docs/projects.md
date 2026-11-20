# Projects

[← Documentation](../README.md#documentation)

A **project** is a calculation you run on a cluster, made of **steps** that run
in order: for example scf, nscf, p2y, GW and BSE. Each step is a job script
with its input files. The project lives in a folder on your PC, mirrored on
the cluster.

## Folders

On your PC:

```
WSe2/                      the project
  project.toml             name, cluster, folder on the cluster, steps in order
  01-scf/                  one folder per step: <number>-<name>
    step.toml              the step: job script, steps it runs after, its jobs
    scf.sh                 the job script, as you wrote it
    pw.scf.in  pseudo/     inputs: any files and folders
    outputs/               results downloaded from the cluster
  02-nscf/
    ...
```

On the cluster, under the profile's projects folder (`$HOME/visual-workflows`
by default):

```
~/visual-workflows/WSe2/
  01-scf/                  the inputs, the job script, and what the job writes
    .vwf/job-393491.status the job's own record of how it ran
    slurm-393491.out       Slurm's output file
  02-nscf/
```

Because the step folders sit side by side, a step reaches an earlier one's
files with relative paths: `outdir = '../01-scf/out'` in an nscf input, for
example.

## Creating a project

**In the program:** Project tab ▸ **New…** (or File ▸ New project, Ctrl+N):
give it a name, a location and a cluster; its folder takes the name (`WSe2
monolayer` → `WSe2-monolayer`), here and on the cluster, where you can choose
another folder or an absolute path (e.g. on scratch). **From the command
line:**

```bash
vwf project new WSe2 --cluster Lynx      # --remote-dir to choose the folder on the cluster
```

**Adding a step** (Project ▸ **Add step…**, or `vwf project add-step`): a name,
the job script, the inputs, and the steps it runs after.

```bash
cd WSe2
vwf project add-step scf  --script ../scf.sh  --input ../pw.scf.in ../pseudo
vwf project add-step nscf --script ../nscf.sh --input ../pw.nscf.in --after scf
```

The script and inputs are **copied** into the step's folder: later edits go
there (`01-scf/pw.scf.in`), and are uploaded at the next submission.

## Job scripts

Any Slurm job script works, with its `#SBATCH` lines at the top. Until the job
script builder arrives, write them as you would for `sbatch`, with the
cluster's modules. For example, on Lynx:

```bash
#!/bin/bash -l
#SBATCH --job-name=scf
#SBATCH --partition=magma
#SBATCH --nodes=1
#SBATCH --ntasks=48
#SBATCH --hint=nomultithread
#SBATCH --mem=60G
#SBATCH --time=12:00:00

module purge
module load qe/7.3.1b-intelmpi-omp
export OMP_NUM_THREADS=1
srun --mpi=pmi2 pw.x -inp pw.scf.in > pw.scf.out
```

Always ask for memory (`--mem` or `--mem-per-cpu`): without it some clusters
give very little (5000 MB per node on Lynx, 1916 MB per CPU on Tirant).

On clusters that need an account or QOS (Tirant), set them in the profile
(Discover does): they are added to every job whose script has no
`#SBATCH --account` or `--qos` of its own. A script's own settings win. Check the modules first with
**Check modules** ([Clusters](clusters.md#programs-choosing-and-checking-modules)).

The job runs in its step's folder on the cluster. Its output file is Slurm's
default `slurm-<job id>.out`, or what `--output` says (`%j` and `%x` are
understood).

**What the program adds.** The copy uploaded to the cluster gets a few lines
right after the `#SBATCH` header, between two marker comments; your local
script is not changed. They append to `.vwf/job-<id>.status`: when the job
started and on which nodes, which modules were loaded, and how it ended (exit
code, or Slurm's stop signal). If Slurm stops the job (time limit, `scancel`),
they record it and end the script at once.

## Submitting

**Submit** (`vwf project submit`) uploads each step (its inputs, and the job
script with the status lines) and submits it.

- With no steps chosen: every step that has not completed and is not queued
  or running, in project order.
- A step whose earlier steps are still queued or running waits for them
  (Slurm's `afterok`): it starts only if they complete successfully.
- A step whose earlier step **completed** starts straight away.
- A step whose earlier step **never ran** or **failed** is refused, with a
  message: run that step (again) first.
- A step already queued or running is refused.

**Running a step again** (after a failure, or with new inputs): select it and
Submit (`vwf project submit nscf`). Its inputs are uploaded again; files the
earlier run left on the cluster stay. Each run is kept in the step's history
(`step.toml`); the table shows the latest.

**Cancelling** (`vwf project cancel [STEP…]`): the selected steps' jobs, or all
running ones. Steps waiting for a cancelled one will never start: Slurm keeps
them pending with the reason `DependencyNeverSatisfied`; cancel them too.

## Following

**Refresh** (`vwf project status`) updates every step whose job has not ended;
**Every 60 s** (`vwf project watch`) repeats it until every step has ended.
The program never asks Slurm more often than every 30 seconds: cluster
administrators notice programs that do.

### States

| State | Meaning |
|---|---|
| pending | Waiting in the queue. The reason says why: `Priority`, `Resources`, `Dependency`, `JobHeldUser`… |
| running | Running (also while starting or finishing). |
| completed | Ended with exit code 0. |
| failed | Ended with another exit code (shown as `exit 3`), or killed (node failure). |
| cancelled | Cancelled, or stopped by a signal (shown as `signal 15`). |
| timeout | Stopped at its time limit. |
| out_of_memory | Killed for using more memory than it asked for. |
| unknown | The program could not tell (see below). |

The exit code is the job **script's**: the last command's, unless the script
says otherwise. A script ending in `srun pw.x … | tee log` returns `tee`'s.
Check the step's output to know if the calculation itself went well.

### Where a state comes from

Slurm forgets a job a few minutes after it ends (5 minutes on Lynx), and not
every cluster keeps job accounting. So a step's state comes from the best of:

1. **Slurm**, while it still knows the job;
2. **accounting** (`sacct`), on clusters that keep it: also the run time and
   the peak memory;
3. **the job's status file** with Slurm's messages in its output file
   (`… DUE TO TIME LIMIT`, `oom_kill`…).

This is how a job that ended while your PC was off, days ago, still gets its
right state. A job with no status file never started: it shows as cancelled
("no record of the job running").

## Logs

Select a step: the lower pane shows the end of its output file, and keeps
adding to it while the job runs. From the command line:

```bash
vwf project log scf          # the last 4 KB
vwf project log scf -f       # and follow it (Ctrl+C to stop)
```

To read other files (QE's or Yambo's own outputs), use the **Files** tab:
select the file, tick **Follow**.

## Getting results

**Get results…** (`vwf project get`) downloads the files of the steps that
have run (or the selected ones) into each step's `outputs/` folder, keeping
the cluster's folder structure and file times.

| Choice | Command | What comes |
|---|---|---|
| Results | `vwf project get` | everything but wave functions (QE's `*.save/wfc*`, `*.wfc<n>`; Yambo's `SAVE/ns.wf*`) and files over 1 GB (`--max-size` to change) |
| For Visual Excitons | `--for-excitons` | `SAVE/ns.db1`, `SAVE/ns.wf*` (for its real-space views), `ndb.BS_diago_Q*`, every `ndb.QP*` |
| Everything | `--all` | every file |

On top of any choice, `--include PATTERN` adds files (also bigger than the
limit) and `--exclude PATTERN` removes them. A pattern without `/` matches
file names (`'ndb.em1*'`); with `/`, paths inside the step folder
(`'gw/*.log'`). Quote patterns in the shell.

Before downloading, the program shows per step how many files are to get,
already here (same size and time: skipped) and skipped by the choice, with
sizes (`--dry-run` lists them all). An interrupted download resumes where it
stopped: files arrive as `<name>.vwf-part` and get their name only when
complete.

The `.vwf/` status files are never downloaded.

**Visual Excitons.** After `--for-excitons`, the step's `outputs/` folder has
what Visual Excitons needs, in the same layout as on the cluster: open it
there, and choose its SAVE folder, the folder with `ndb.BS_diago_Q*` and the
one with the QP database. Leave out the wave
functions (`--exclude 'ns.wf*'`) if you only need spectra and dispersion: they
are large.

## The project files

`project.toml` and each `step.toml` are TOML: readable, and fine under git. The
step's jobs are recorded in its `step.toml`:

```toml
name = "scf"
script = "scf.sh"
after = []

[[jobs]]
id = "393505"
submitted = 2026-11-20 13:41:52+00:00
state = "completed"
exit_code = 0
started = 2026-11-20 13:41:52+00:00
ended = 2026-11-20 13:41:53+00:00
nodes = "compute01"
output = "slurm-393505.out"
final = true            # nothing more will change
source = "slurm"        # where the state came from: slurm, accounting, status file
```
