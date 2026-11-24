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
  pseudos/                 pseudopotentials for every step (optional)
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

### Pseudopotentials

pw.x looks for the files named in `ATOMIC_SPECIES` in `pseudo_dir`, if the
input sets it; otherwise in `$ESPRESSO_PSEUDO`; otherwise in
`~/espresso/pseudo` on the cluster. Keep them in one of these places:

- **The project's `pseudos/` folder**, next to the steps: it is uploaded at
  Submit (only files that are new or changed), and job scripts written from
  a job description set `ESPRESSO_PSEUDO` to it, so inputs need no
  `pseudo_dir`. With your own job script, write `pseudo_dir = '../pseudos'`.
- **A library on the cluster**, such as a group's folder, named in the
  cluster profile (**Pseudopotentials**, `[paths] pseudos`): used, the same
  way, by projects without their own folder.
- **Anywhere**, with `pseudo_dir` in the input: inside the project (uploaded
  with it), or a path on the cluster.

**From PseudoDojo:** in the pw.x input editor (**Edit pw.x input…** in a
step), **PseudoDojo…** picks a table (PBE or PBEsol; scalar or fully
relativistic, for spin–orbit; standard or stringent) for the structure's
elements, downloads the files once into a library on this PC, and copies them
into the project's `pseudos/` as `Mo.nc-sr-04_pbe_standard.upf`. The
folder's `sources.toml` records where each came from, its checksum and
PseudoDojo's cutoff hints; the checks warn when `ecutwfc` is below a file's
low hint (as long as the file is unchanged). PseudoDojo's files are CC BY 4.0:
cite M. J. van Setten et al., Comput. Phys. Commun. 226, 39 (2018).

Before submitting, the files are found and their headers read: here for those
in the project, over the connection for those on the cluster (the first 64 KB
of each). A missing or unreadable file stops the submission (in nscf and
bands runs it is a warning: pw.x looks in the scf's `prefix.save` first), and
so do `nbnd` below the occupied bands (from the files' valence charges),
spin–orbit without a fully relativistic pseudopotential, and no `ecutwfc`
when no file suggests one. Files with different functionals, or for another
element than the species, are warnings.

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

A step gets its job script in one of two ways.

**From a job description** (**New step…**, or `vwf project add-step` with
`--run`): its resources (partition, nodes, MPI tasks per node, threads per
task, memory, time, and, for this step only, a QOS or account other than
the profile's: a short interactive or debug QOS for a test, say) and its runs (pw.x with its input and options such as
`-nk 4`, p2y, yambo with its `-J` jobs…), kept in `step.toml` as a `[job]`
table. The program writes `job.sh` from it for the cluster: the `#SBATCH`
lines (each task's CPUs counting both threads of its cores where cores have 2, the profile's
account and QOS), the program's modules from a clean slate, the threads'
settings, the links, and each run through its launcher (pw.x with `-inp`,
yambo with `-F`, `-J` and a `-walltime` that stops it before the job's end),
stopping at the first run that fails. It is written again at every Submit,
so a change of the profile (modules, account) reaches it. If you edit
`job.sh` by hand, Submit notices and asks: keep the edits (the step then has
a script of its own) or write it again (`--keep-edits`, `--rewrite`).
`vwf project script STEP` shows it.

**Your own script** (**Add own script…**, or `--script`): any Slurm job
script, with its `#SBATCH` lines at the top, written as you would for
`sbatch` with the cluster's modules. Such steps are not checked. For
example, on Lynx:

```bash
#!/bin/bash -l
#SBATCH --job-name=scf
#SBATCH --partition=magma
#SBATCH --nodes=1
#SBATCH --ntasks=48
#SBATCH --cpus-per-task=2              # both threads of a core: one task per core
#SBATCH --mem=60G
#SBATCH --time=12:00:00

module purge
module load qe/7.3.1b-intelmpi-omp
export OMP_NUM_THREADS=1
export SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK
srun --mpi=pmi2 pw.x -inp pw.scf.in > pw.scf.out
```

On Lynx, whose Slurm counts each hardware thread as a CPU, ask for whole
cores this way rather than with `--hint=nomultithread`: with the hint, the
job gets one CPU per task but `srun` then asks for one core per task and
stops ("More processors requested than permitted").

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

**Checks before submitting.** Steps with a job description are checked
before anything is uploaded, and nothing is submitted if one has an error:

- the job against the partition and your limits (time, memory, cores);
- each program's modules, loaded on the cluster once per connection;
- the inputs of pw.x and yambo runs, as their versions read them: names,
  values, cards, units, runlevels, parallelisation, the pseudopotentials;
- the chain: an nscf against its scf, p2y against the nscf it reads, yambo
  runs against the SAVE, screening and QP database they use, and, for BSE
  runs, what Visual Excitons needs (a diagonalising solver, `WRbsWF`).

Errors are what would make a program stop, or silently compute something
else than asked (an unquoted yambo string keeps the default, for example).
**Submit anyway** (`--no-check`) submits all the same; warnings are shown
after submitting.

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

### Why a job ended badly

**Diagnose** (`vwf project diagnose [STEP…]`) reads the end (64 KB) of the
job's log and of each run's own output (a pw.x run's output file, the log
yambo writes), for the selected steps or, by default, every step whose
latest job did not complete. `vwf project status` names those steps. It
reports:

- how the job ended, when that says enough: the time limit, out of memory,
  a node failure, a cancel;
- the programs' own stops, quoted: pw.x's `Error in routine <name> (<n>):`
  and its message, yambo's and p2y's `[ERROR] <message>` (with the section
  it was in), explained where the cause is known (too few bands, a
  pseudopotential not found, an nscf without the scf's data, the
  diagonalisation failing; yambo's parallel variables not fitting the tasks,
  too few bands in the SAVE, memory);
- known messages of Slurm, MPI, the shell and the modules: `srun` asking for
  more CPUs than the job has, MPI unable to reach Slurm's process manager
  (the launcher's `--mpi`), a missing library or command, a module that
  does not exist, memory killed, the disk full, a crash; and pw.x's scf not
  converging or stopping at `max_seconds`, yambo stopping at its own time
  limit.

Each with where it was read and what to do:

```
01-scf: job 393617 failed, exit code 1
  srun asked for more CPUs than the job was given: ... [job log]
    read: srun: error: Unable to create step for job 393617: More processors requested than permitted
    what to do: Write the job script again ...
```

A consequence (an MPI abort) is shown only when nothing says more, with what
the exit code means (137: killed, 139: a crash). When nothing is recognised,
the job log's last lines are shown.

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

**Visual Excitons.** After `--for-excitons`, the steps' `outputs/` folders
have what Visual Excitons needs, in the same layout as on the cluster.
**Open in Visual Excitons** (`vwf project excitons [STEP]`) starts it: on
the selected step's results, with the SAVE, BSE and QP database that step
used (a BSE step's SAVE through its `SAVE` link, its QP energies through its
input's `KfnQPdb`), or on the whole project when no step is selected. It
says what Visual Excitons will lack (the QP database or the wave functions
not downloaded), and shows Visual Excitons' own message when it refuses a
path. `--dry-run` only prints the command. Visual Excitons is found as the
`visual-excitons` command, or installed in the same Python environment.

Or open the project folder in Visual Excitons (`File › Open dataset…`, or
`visual-excitons WSe2` from a terminal): it reads the project's steps and
finds SAVE and the GW and BSE results in their `outputs/` folders. When
several steps have BSE results (or GW, or a SAVE), it asks which to open; or
give it, `visual-excitons WSe2 --bse WSe2/06-bse-dispersion/outputs/diago`. Leave out the wave functions
(`--exclude 'ns.wf*'`) if you only need spectra and dispersion: they are
large.

### Choosing bands

**Bands** (`vwf project bands [STEP]`) reads a downloaded SAVE (the selected
step's own, or the one it links; by default the first) and shows what GW and
BSE bands are chosen from:

- each band's lowest and highest energy (zero at the valence top), the gap
  and the lowest direct transition, with their k-points;
- for some energies (`--window EV …`; by default the lowest direct
  transition plus 1, 2 and 4 eV), the bands holding every direct transition
  up to it, and the BSE's size: valence × conduction bands × k-points of the
  whole zone, one row per transition (silicon, bands 3–6 on 6×6×6: 2 × 2 ×
  216 = 864, the excitons yambo wrote);
- the windows the project's yambo inputs use (`QPkrange`, `BSEBands`),
  checked for degenerate states they split: a band kept while its partner,
  at the same energy and k-point, is left out;
- `--dos FILE` writes the density of states (states/eV per cell, 0.1 eV
  Gaussians, `--broadening`), the k-points weighted by YamboPy from the
  SAVE's symmetries.

In the Project tab, **Bands** shows the same, with the density of states
drawn and a box for the energy: the bands, the BSE's size and any split are
shown as it changes, and the energies of the states taking part are shaded.

### Reading results

**Results** (`vwf project results [STEP…]`) reads what was downloaded, with
nothing run on the cluster, and says what each step's databases hold:

- **SAVE** (`ns.db1`): electrons, bands, k-points, the occupied bands, and
  the DFT gaps on the grid (indirect, and the smallest direct one, with
  their k-points).
- **GW** (`ndb.QP`): the approximation and solver, each band's correction
  (mean, lowest and highest over the k-points), the gaps before and after,
  and the renormalisation factors Z.
- **BSE** (`ndb.BS_diago_Q*`): how many excitons and how many weak or dark
  (under 5% of the strongest), the lowest and lowest bright ones, how far
  the lowest lies below the lowest direct transition on the energies the
  BSE was given (QP, or DFT with a scissor, from its input), and the peaks
  of the absorption rebuilt from the excitons (`--broadening`, 0.1 eV by
  default; yambo's own spectrum is its `o-*` file).

For bulk silicon on a 6×6×6 grid it reads:

```
04-gw:
  GW (PPA, Newton solver): bands 3–6 at 16 k-points; Z 0.75–0.80
    band 4: +0.08 eV (-0.07 to +0.22)
    band 5: +0.62 eV (+0.48 to +0.76)
    gap 0.70 → 0.97 eV; lowest direct transition 2.55 → 3.03 eV
05-bse:
  BSE (Q = 0, bands 3–6 on QP energies): 864 excitons, 853 weak or dark (under 5% of the strongest)
    lowest 2.92 eV, lowest bright 2.93 eV
    0.11 eV below the lowest direct transition (3.03 eV)
```

Then warnings, where something looks inconsistent: no gap (a metal, or a
wrong occupation); Z outside 0.5–1 (a state near a pole of the self-energy);
a GW gap smaller than DFT's; excitons below zero, none bright, or the lowest
above the transitions it comes from; the lowest exciton below the lowest DFT
transition when the BSE was given QP energies (bound by more than GW opened
the gap, or the QP energies not applied). The warnings never judge
convergence: a converged result and an unconverged one look alike from the
databases. A database that cannot be read is named, and the command exits
with 1.

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
