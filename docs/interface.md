# The main window

[← Documentation](../README.md#documentation)

## The cluster selector

The toolbar chooses the cluster that the **Cluster**, **Jobs** and **Files**
tabs show. **Connect** logs in; you are asked for a password, passphrase or
code only if needed (see [Logging in](clusters.md#logging-in)). You rarely
need to press it: any action that needs the cluster connects first.

There is one connection per cluster, shared by every tab, and it stays open
until you disconnect or close the program. A connection that drops (sleep,
network change) is opened again by the next action. Jobs are not affected by
connections: they live in Slurm.

Slow operations never freeze the window. Errors open a message; the ones
caused by a bug have a **Show Details** button with the information to report.

## Menus

- **File:** New project, Open project, Recent projects, Quit.
- **Clusters:** New cluster, Import profile, Export profile to share, Remove
  cluster. See [Clusters](clusters.md).
- **Help:** About.

## Cluster tab

The cluster's profile: how to connect, where projects go on the cluster, its
partitions, extra module folders, and the modules of each program.

- **Test connection:** logs in and reports the home folder, Slurm's version,
  whether the cluster keeps job accounting, your Unix groups and the module
  system.
- **Discover:** fills in the partitions you may use, with their time limit,
  CPUs, memory and GPUs per node, and corrects a Spack module folder given one
  level too high. Review, then **Save**.
- **Programs:** select a row (qe, yambo) and **Choose modules…** to pick from
  the cluster's modules, or **Check modules** to load them on the login node
  and check that each executable is found and has all its libraries.
- **Save** checks the profile and writes it. Test, Discover and the module
  buttons save first: the cluster always gets what the form shows.

Details in [Clusters](clusters.md).

## Jobs tab

All your Slurm jobs on the cluster, not only the ones started here: queued,
running, and ended in the last minutes (Slurm forgets ended jobs after a while,
5 minutes on Lynx). **Refresh**, or tick **Every 60 s**. Select jobs and
**Cancel selected** to cancel them (you are asked to confirm).

Columns: job id, name, partition, state, reason (why it waits or why it
ended), nodes, CPUs (as Slurm counts them: hardware threads), start time, and
the result (`exit 3`, `signal 9`).

## Files tab

The cluster's files, starting at your home folder. The **Folder** line shows
where you are; type another folder and press Enter to go there.
Double-click a folder to enter it, **Up** to go back. **Home**, **Scratch** and
**Projects** jump to your home, the profile's scratch folder (set it in the
Cluster tab) and its projects folder.

- **Selecting a file** shows its end (the last 64 KB) on the right, and its
  full path above (selectable, to copy).
- **Follow** re-reads the file every 10 s and adds what is new: an output as
  the job writes it.
- **Download…** copies the selected files and folders into a folder you
  choose. Folders come whole; files already there (same size and time) are
  skipped, and interrupted files resume.

## Project tab

A project: a folder with one sub-folder per step, mirrored on the cluster. The tab follows
changes made to the project by the command line (or another window): it
reads the project again when its files change, and before each action, so
that what it saves never undoes them.

- **New…** asks for the project's name, where to create its folder, its
  cluster (the toolbar's, by default) and its folder on the cluster (from the
  name, by default), showing both full paths before you **Create** it.
  **Open…** opens a project (also File ▸ Recent projects). Opening a project
  selects its cluster.
- **New step…:** a step with a job description, its job script written for
  you. The step's name, the steps it runs after and its inputs; its **runs**
  in order (the program and executable, from the cluster profile; the input;
  arguments such as `-nk 4`; Yambo's `-J` jobs; with or without MPI); its
  **resources** (partition, nodes, MPI tasks per node, threads per task,
  memory per node, time, or no time limit, and a QOS or account for this
  step instead of the profile's), with the partition's limits
  shown below it (cores, threads per core, node memory, the job memory
  limit, the time limit) and what the job takes of each node; links made
  before the runs (`SAVE` → `../03-p2y/SAVE`) and extra shell lines. On the
  right, the job script as it will be written, and the checks' results,
  errors in red, warnings in amber, updated as you type. The pseudopotentials
  and modules on the cluster, and the chain of steps, are checked at
  **Check** and **Submit**.
- **New QE chain…:** the usual Quantum ESPRESSO steps for Yambo made in one
  go from an scf input, made or edited in the pw.x input editor: the scf;
  an nscf for Yambo (an unshifted grid, the scf's by default, and `nbnd`,
  4 × the occupied bands by default, at least 20 more, when the
  pseudopotentials in the project tell them); p2y; a band structure (a path
  through the lattice's special points). All share the scf's `prefix` and
  `outdir`; the nscf gets `force_symmorphic` only if the crystal needs it
  for p2y. The bands run replaces the nscf's wave functions in
  `prefix.save`, so it runs after p2y. One set of resources for the pw.x
  steps (p2y runs on one task; inputs without `max_seconds` get one from the
  time); each step can be changed afterwards with **Edit step…**. With
  **Yambo, after p2y**: a GW step (plasmon pole) and a BSE on its QP
  energies, or a BSE on DFT energies with a scissor shift, for the bands
  around the gap (2 below and 2 above by default); p2y's step then also runs
  yambo's setup, and the yambo steps link its SAVE.
- **Edit input…** (under the runs): for a pw.x run, its input in the pw.x editor; for a yambo run, in the yambo editor: its runlevels (with GW and BSE presets), the variables that matter as a form (bands, cutoffs in Ry, `QPkrange`, the Dyson solver, the BSE's kernel, solver and bands, the QP energies from a GW or a scissor, `WRbsWF`), every other variable these runlevels read as a table with yambo's descriptions (empty: yambo's default; names yambo would not read are marked), and the input with the checks beside it (yambo's, the parallel roles, Visual Excitons'). For a step that exists, **Refresh from the cluster's yambo** asks the yambo it will run for its own input against the step's SAVE (connecting if needed) and says what it would not read and what it adds; **Complete with yambo's values** adds those.
- The pw.x editor: the selected pw.x run's input, new
  or the step's, in an editor of its own. **Structure**: import a file
  (CIF, POSCAR, XYZ with a cell, a pw.x input or output…), shown with its
  space group, lattice and how many symmetry operations pw.x will find;
  written with the lattice's own `ibrav` and `celldm` (QE builds the exact
  cell), or with `ibrav = 0`; **Primitive cell**, and **Turn for pw.x** when
  the cell's orientation hides symmetry from pw.x. **Settings**: the main
  variables (calculation, prefix, outdir, cutoffs, nbnd, occupations, spin,
  `force_symmorphic`, `conv_thr`); the others stay as they are.
  **k-points**: a grid from a spacing or n₁ × n₂ × n₃ (one point across a
  layer's vacuum), with how many k-points pw.x will compute, or a band path
  through the lattice's special points. **Pseudopotentials**: each species'
  file, **PseudoDojo…** to choose a table and get its files into the
  project's `pseudos/`, and **Use suggested cutoffs**. The input as it will
  be written and the checks' results are on the right. The input is copied
  into the step's folder when the step is saved.
- **Edit step…** (or double-click): the same, for a step made with **New
  step…**. If its job script was edited by hand, saving asks first: the
  script is written again from the description.
- **Add own script…:** a step with a job script you wrote: its name, the
  script, its input files and folders, and the steps it runs after.
- **Check:** every check of the selected steps (or all) without submitting;
  connected, also the module tests and the pseudopotentials kept on the
  cluster. The results are in a message and in the status line's tooltip.
- **Submit:** the selected steps, or, with none selected, every step that has
  not completed and is not running, in order, each waiting for the steps it
  needs.
- **Cancel:** the selected steps' jobs, or all running ones.
- **Diagnose:** why the selected steps' jobs (or every one that did not
  complete) ended so: the causes recognised in their logs and outputs, each
  with what to do.
- **Refresh**, or **Every 60 s**: updates each step's state. Watching stops by
  itself when every step has ended.
- **The step table:** job, state, reason, result, start and end, and the steps
  it runs after.
- **The lower pane:** the selected step's output, followed live while the
  job runs: the job's log (Slurm's output file), or, chosen above it, a
  run's own output: a QE run's output file (`scf.out`: pw.x writes there,
  not to the job's log) or the log p2y, yambo or ypp writes (`l_p2y`,
  `l-<job>_…`, the first task's in `LOG/` with several).
- **Get results…:** choose what to download (results without wave functions
  and big files, what Visual Excitons reads, or everything), see per step how
  many files are to get, already here or skipped, and confirm. Files go into
  each step's `outputs/` folder.
- **Bands:** the downloaded SAVE's bands (the selected step's, or the
  first): energy ranges, gaps, the density of states drawn, and for an
  energy typed in, the bands a BSE up to it needs, its size and any
  degenerate states split.
- **Open in Visual Excitons:** the selected step's results (its SAVE, BSE
  and QP database), or the project's, opened in Visual Excitons.
- **Results:** what the downloaded results of the selected steps (or all)
  hold, read and checked: the SAVE's gaps, the GW corrections, the excitons
  and absorption peaks, and warnings where something looks inconsistent.

Details in [Projects](projects.md).
