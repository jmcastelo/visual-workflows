# Command line

[← Documentation](../README.md#documentation)

`vwf` does what the program does, from a terminal: for scripts, for a quick
look, or over a slow connection. It uses the same profiles and projects.
`vwf <command> --help` lists each command's options.

Passwords, passphrases, one-time codes and unknown host keys are asked in the
terminal (see [Logging in](clusters.md#logging-in)). Errors are printed as
`error: …`, with exit code 1.

## Clusters

| Command | Does |
|---|---|
| `vwf clusters list` | Lists the profiles. |
| `vwf clusters add NAME --host HOST [--port N] [--user USER] [--auth key\|agent\|password\|interactive] [--key FILE]` | Adds a profile. `--host` may be a `~/.ssh/config` alias. |
| `vwf clusters show NAME` | Prints the profile file. |
| `vwf clusters record NAME DIR [--as-user NAME]` | For the tests: asks the cluster what the program asks and writes every command's answer, with a `cluster.toml` manifest, into `DIR`, your username and home folder replaced (see [Development](development.md#recording-a-cluster)). Read-only on the cluster. |
| `vwf clusters discover NAME` | Fills in the partitions you may use, with their limits (time from your QOS, cores, threads per core, node memory, the job memory limit), and, on clusters with accounting, the account and QOS if the profile has none: as **Discover** in the cluster editor. |
| `vwf clusters program NAME PROGRAM [--modules M…] [--launcher L] [--launcher-options O] [--exe KEY=FILE…] [--preamble LINE…]` | Sets a program's modules (in load order), MPI launcher (`srun`, `mpirun`, `mpiexec`) and its options (e.g. `--mpi=pmi2`), executables (`pw=pw.x`) and lines after the modules. A new `qe` or `yambo` starts with the usual executables. Then check that the modules load with `vwf modules NAME --check`. |
| `vwf clusters remove NAME` | Removes the profile (the cluster's files are not touched). |
| `vwf clusters export NAME FILE` | Writes a copy to share, without your username and key file. |
| `vwf clusters import FILE` | Adds a shared profile (never overwrites one you have). |

Cluster names are not case-sensitive.

## A cluster

| Command | Does |
|---|---|
| `vwf test CLUSTER` | Logs in; reports home, Slurm version, accounting, your groups, Python, module system. |
| `vwf partitions CLUSTER [--all]` | Partitions you may use: nodes, free/total CPUs, cores per node (`48x2`: 48 cores of 2 hardware threads), memory per node, the most memory a job may ask per node, default memory, time limit, state; on clusters with accounting, also the QOS that let you use each (`*`: default) and your accounts. `--all` adds those you may not use. |
| `vwf jobs CLUSTER` | Your jobs: queued, running, and ended in the last minutes, with reason and result. |
| `vwf run CLUSTER [--timeout S] -- COMMAND…` | Runs a command in a login shell and passes on its output and exit code. One argument is a shell line (`'module avail 2>&1 \| grep qe'`); several are quoted word by word. `--timeout` in seconds (default 300), before or after the cluster's name. |
| `vwf submit CLUSTER SCRIPT [--to DIR] [--after JOB…]` | Submits a job script that is on the cluster, or with `--to`, a local one uploaded to `DIR` first. `--after`: start once these jobs succeed. The profile's account and QOS are added if the script has none. |
| `vwf cancel CLUSTER JOB…` | Cancels jobs. |
| `vwf modules CLUSTER [FILTER] [--describe]` | Lists modules: all, a program's (`qe`, `yambo`: newest first), or those whose name contains `FILTER`. `--describe` adds each module's description. |
| `vwf modules CLUSTER --check MODULE… --exe EXE…` | Loads the modules from a clean slate and checks each executable and its libraries. Exit code 1 if something is wrong. |

## Projects

Project commands work on the project in the current folder, or the one given
with `-C FOLDER`.

| Command | Does |
|---|---|
| `vwf project new FOLDER --cluster CLUSTER [--name NAME] [--remote-dir DIR]` | Creates a project. `--remote-dir`: its folder on the cluster (default: the folder's name, under the profile's projects folder). |
| `vwf project add-step NAME --script FILE [--input FILE…] [--after STEP…]` | Adds a step with a job script of your own, copying it and the inputs (files or folders). |
| `vwf project add-step NAME --run RUN… [resources] [--input FILE…] [--after STEP…]` | Adds a step with a job description: its job script is written from it. Each `--run` is `"[PROGRAM:]EXECUTABLE [INPUT] [ARGS]"`, in order: `"pw pw.scf.in -nk 4"`, `"p2y -I ../01-scf/out/si.save -O . --serial"`, `"yambo bse.in -J diago,../04-gw/gw"` (`-J` gives Yambo's job names, `--serial` runs without MPI, an input given as a path is copied). Resources: `--partition`, `--nodes`, `--tasks-per-node` (MPI tasks), `--cpus-per-task` (threads), `--mem` (per node), `--time` (or `UNLIMITED`), `--qos` and `--account` (for this step, instead of the profile's). Also `--link NAME=TARGET` (made before the runs, e.g. `SAVE=../03-p2y/SAVE`), `--line-before LINE`, `--line-after LINE`. The step is checked as it is added. |
| `vwf project add-qe-chain SCF_INPUT [--nscf-grid N N N] [--nscf-nbnd N] [--no-nscf] [--no-p2y] [--no-bands] [--bands-path PATH] [--bands-nbnd N] [--density D] [resources] [--after STEP…]` | Adds the QE steps for Yambo from an scf input: scf, an nscf (the scf's grid unshifted, or `--nscf-grid`; `nbnd` 4 × the occupied bands, at least 20 more, when the project's pseudopotentials tell them, else give `--nscf-nbnd`; `force_symmorphic` where the crystal needs it), p2y, and a band structure (the lattice's path, or `--bands-path`; `--density` k-points per Å⁻¹), all with the scf's `prefix` and `outdir`; bands runs after p2y. Resources (`--partition`, `--nodes`, `--tasks-per-node`, `--mem`, `--time`, `--qos`, `--account`) for the pw.x steps; p2y runs on one task. Inputs without `max_seconds` get one from `--time`. The steps are checked as they are added. |
| `vwf project refresh-input STEP [--run N] [--complete]` | Asks the cluster's yambo (the profile's modules) for its own input for the same runs as the step's yambo run (the first, or run `N`), against the SAVE the step uses, and compares: names that yambo does not read (it ignores them without a word), and what it writes that the input lacks, with the values it computes from the SAVE. `--complete` adds those to the input, keeping the input's own values. Runs on the login node, or as a short job, as the profile says (`refresh`); a SAVE not indexed yet gets yambo's setup first. |
| `vwf project script STEP` | Shows a step's job script: for a step with a job description, as Submit will write it. |
| `vwf project check [STEP…] [--on-cluster]` | Checks steps (default: all) without submitting: the job against the partition, the inputs, the parallelisation, the pseudopotentials in the project and the chain of steps. `--on-cluster` also tests the modules and reads the pseudopotentials kept on the cluster. Exit code 1 if there are errors. |
| `vwf project submit [STEP…]` | Uploads and submits the steps (default: all not completed and not running), each after the steps it needs. Steps with a job description are checked first (resources against the partition, programs, and a test that their modules load): problems stop the submission, `--no-check` submits anyway; warnings are printed. A script written from a job description and edited by hand stops it too: `--keep-edits` keeps the edits (the step then has a script of its own), `--rewrite` writes the script again. |
| `vwf project status` | Updates and shows each step's latest job. |
| `vwf project watch [--interval S]` | Updates every `S` seconds (default 60, at least 30) until every step has ended, showing what changes. |
| `vwf project log STEP [--run N] [-f] [--bytes N] [--interval S]` | Shows the end of the step's output (`N` bytes, default 4096): the job's log, or with `--run N` that run's own output (a QE run's output file, such as `scf.out`; the log p2y, yambo or ypp writes, such as `l_p2y`); `-f` follows it, reading every `S` seconds (default 10), until the job ends. |
| `vwf project get [STEP…] [--all \| --for-excitons] [--include PATTERN…] [--exclude PATTERN…] [--max-size SIZE] [--dry-run]` | Downloads results into each step's `outputs/` (see [Getting results](projects.md#getting-results)). `SIZE` like `500M`, `2G`. |
| `vwf project cancel [STEP…]` | Cancels the steps' jobs (default: all running). |

Steps can be named by name (`scf`) or folder (`01-scf`).

## Structures and pseudopotentials

| Command | Does |
|---|---|
| `vwf structure FILE [--spacing S] [--shift] [--ibrav auto\|0\|N] [--primitive] [--turn] [--path [PATH]] [--density D] [--input]` | A structure file (CIF, POSCAR, a pw.x input with any `ibrav` or output, …): its space group, lattice and symmetry operations, how many pw.x finds in this cell and how many need fractional translations (then an nscf for Yambo needs `force_symmorphic`), vacuum (one k-point across it), its `ibrav` and `celldm` (`auto`, the default) or `--ibrav 0`, the grid for a k-point spacing (Å⁻¹ with 2π, default 0.2) with the k-points pw.x will compute, and with `--path` a band path (the lattice's, or e.g. `GMKG`). `--primitive`: spglib's primitive cell first; `--turn`: the cell turned to QE's axes, when its orientation hides symmetry from pw.x. `--input`: also the structure and k-points as the start of a pw.x input. No cluster needed. |
| `vwf pseudos get ELEMENT… [--from FILE] [--xc PBE\|PBEsol] [--fr] [--stringent]` | PseudoDojo's norm-conserving pseudopotentials for the elements (or those of a structure or pw.x input, `--from`), downloaded once into the library on this PC and copied into the project's `pseudos/`, with their cutoff hints and the cutoffs they suggest. `--fr`: fully relativistic (spin–orbit); `--stringent`: the stringent table. |
| `vwf pseudos list` | The project's pseudopotentials, with the PseudoDojo table each came from and its low cutoff hint (a file changed since has none). |

## Examples

```bash
# What QE builds are there, and does Yambo load with its libraries?
vwf modules Lynx qe
vwf modules Lynx --check yambo/intel-libs yambo/gpl/5.1.1-intelmpi --exe yambo p2y

# A one-off job
vwf submit Lynx relax.sh --to calcs/WSe2
vwf jobs Lynx

# A structure, its pseudopotentials, and the QE chain for Yambo
vwf structure WSe2.cif --path
vwf structure WSe2.cif --input > scf.in        # then add &control, cutoffs...
vwf pseudos get --from WSe2.cif -C WSe2
vwf project add-qe-chain scf.in --partition cpu --tasks-per-node 48 --mem 100G \
    --time 12:00:00 -C WSe2

# A project, followed until it ends, then what Visual Excitons needs
vwf project submit -C WSe2
vwf project watch -C WSe2
vwf project get --for-excitons --exclude 'ns.wf*' -C WSe2
```
