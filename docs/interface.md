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
  level too high. Review, then **Save**. What an empty value means is
  written in grey: `unlimited` time, `0` GPUs, `none` for the memory limit
  per job (the node's memory then); type a value over it to set one.
- **Programs:** select a row (qe, yambo) and **Choose modules…** to pick from
  the cluster's modules, or **Check modules** to load them on the login node
  and check that each executable is found and has all its libraries.
- **Save** checks the profile and writes it. Test, Discover and the module
  buttons save first: the cluster always gets what the form shows.

Details in [Clusters](clusters.md).

## Jobs tab

All your Slurm jobs on the cluster, not only the ones started here: queued,
running, and ended in the last minutes (Slurm forgets ended jobs after a while,
5 minutes on Lynx), listed as soon as the cluster is connected (the tab
never connects by itself). **Refresh**, or tick **Every 60 s**. Select jobs and
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

The tab only reads: it lists, shows and downloads, never changes or deletes.
It shows what your account may read, as SSH does: other users' folders too,
where their permissions allow. The **Owner** column says whose each entry
is, and the status line says when the folder you are in belongs to someone
else. Who may read what is the cluster's filesystem's call, not the
program's: hiding folders here would not stop SSH, and would block shared
group folders, `/apps`, or a colleague's results.

## Project tab

A project: a folder with one sub-folder per step, mirrored on the cluster. The tab follows
changes made to the project by the command line (or another window): it
reads the project again when its files change, and before each action, so
that what it saves never undoes them.

With no project open, the tab offers **New project…** and **Open
project…** (also in the File menu, with Recent projects). **New project…**
asks for the project's name, where to create its folder, its cluster (the
toolbar's, by default) and its folder on the cluster (from the name, by
default), showing both full paths before you **Create** it. Opening a
project selects its cluster.

A project shows as three parts:

- **On top**, what acts on the whole project: **Add ▾** (a QE chain to
  excitons, a step, a step with your own script, a convergence study, each
  described below), **Check all**, **Submit all** (every step that has not
  completed and is not running, in order, each waiting for the steps it
  needs), **Refresh** and **every 60 s** (each step's state; watching stops
  by itself when every step has ended), then **Get results** (every step's;
  the arrow chooses which files), **Results**, **Studies** and **Visual
  Excitons** for the whole project.
- **On the left, the steps:** state (coloured: completed, failed, running,
  waiting), job, result and end; a waiting job's reason as the state's
  tooltip. A study's copies sit under the step they were copied from, in a
  group named after the study (selecting the group selects them all).
  Several steps can be selected; right-click for the same actions as the
  panel.
- **On the right, the selected step:** its kind (GW, BSE, pw…), state, job,
  result, start and end, and the steps it runs after; then only the actions
  that fit it: **Edit…** (a step with a job description; double-click too),
  **Submit** (or **Submit again**), **Cancel job** while it runs or waits,
  **Check**, **Diagnose** when its job ended badly, **Get results** (the
  arrow chooses: results without wave functions and big files, what Visual
  Excitons reads, or everything), **Results** once something is downloaded,
  **Bands** for a downloaded SAVE, **Study…** for a GW or BSE step, **Visual
  Excitons** for a downloaded BSE. Below, its **Output** (the job's log, or
  a run's own output, chosen in **Show**, followed live while the job runs),
  **Input** (its input files), **Script** (its job script) and **Results**
  (what its downloaded results hold). With several steps selected, the
  actions for them all.

The windows the actions open:

- **Add ▸ Step…:** a step with a job description, its job script written for
  you. The step's name, the steps it runs after and its inputs; its **runs**
  in order (the program and executable, from the cluster profile; the input;
  arguments such as `-nk 4`; Yambo's `-J` jobs; with or without MPI); its
  **resources** (partition, nodes, MPI tasks per node, threads per task,
  memory per node, time, or no time limit, and a QOS or account for this
  step instead of the profile's), with the partition's limits
  shown below it (cores, threads per core, node memory, the job memory
  limit, the time limit) and what the job takes of each node; links made
  before the runs (`SAVE` → `../03-p2y/SAVE`) and extra shell lines. On the
  right, the checks' results (errors in red, warnings in amber, updated as
  you type, their count in the tab's title) and, in a tab of its own, the
  job script as it will be written. The pseudopotentials
  and modules on the cluster, and the chain of steps, are checked at
  **Check** and **Submit**.
- **Add ▸ QE chain to excitons…:** the usual Quantum ESPRESSO steps for Yambo made in one
  go from an scf input, in five sections, one page each (chosen on the left,
  or with Back and Next; a section whose steps are off is greyed; the
  summary of the steps to be made and **Add steps** always in view): the
  scf, nscf and p2y, the band structure, GW and BSE, the resources. From
  an scf input, made or edited in the pw.x input editor: the scf;
  an nscf for Yambo (an unshifted grid, the scf's by default, and `nbnd`,
  4 × the occupied bands by default, at least 20 more, when the
  pseudopotentials in the project tell them); p2y; a band structure (a path
  through the lattice's special points). All share the scf's `prefix` and
  `outdir`; the nscf gets `force_symmorphic` only if the crystal needs it
  for p2y. The bands run replaces the nscf's wave functions in
  `prefix.save`, so it runs after p2y. One set of resources for the pw.x
  steps (p2y runs on one task; inputs without `max_seconds` get one from the
  time); each step can be changed afterwards with **Edit…**. With
  **Yambo, after p2y**: a GW step (plasmon pole) and a BSE on its QP
  energies, or a BSE on DFT energies with a scissor shift, for the bands
  around the gap (2 below and 2 above by default); p2y's step then also runs
  yambo's setup, and the yambo steps link its SAVE. A layer's GW and BSE
  get the Coulomb cutoff and RIM-W (yambo 5.2 and later; **RIM-W in a
  layer's BSE kernel**, on by default, keeps it in the BSE), **BSE at every Q** the
  excitons' dispersion (one BSE per irreducible k-point) (see
  [Projects](projects.md#layers-the-coulomb-cutoff)).
- **Edit input…** (under the runs): for a pw.x run, its input in the pw.x editor; for a yambo run, in the yambo editor: its runlevels (with GW and BSE presets), the variables that matter as a form (bands, cutoffs in Ry, `QPkrange`, the Dyson solver, the BSE's kernel, solver and bands, the QP energies from a GW or a scissor, `WRbsWF`), every other variable these runlevels read as a table with yambo's descriptions (empty: yambo's default; names yambo would not read are marked), and the input with the checks beside it (yambo's, the parallel roles, Visual Excitons'). For a step that exists, **Refresh from the cluster's yambo** asks the yambo it will run for its own input against the step's SAVE (connecting if needed) and says what it would not read and what it adds; **Complete with yambo's values** adds those. **Scissor from a GW…** fits a scissor (shift and stretches) to a GW's `ndb.QP` on the BSE's bands, in place of its QP database, and shows how good the fit is.
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
- **Edit…** (or double-click): the same, for a step with a job description. If its job script was edited by hand, saving asks first: the
  script is written again from the description.
- **Add ▸ Step with your own script…:** a step with a job script you wrote: its name, the
  script, its input files and folders, and the steps it runs after.
- **Check:** every check of the selected steps (Check all: every step)
  without submitting; connected, also the module tests and the
  pseudopotentials kept on the cluster. The results are in a message and in
  the status line's tooltip.
- **Diagnose:** why the selected steps' jobs ended so: the causes
  recognised in their logs and outputs, each with what to do.
- **Get results:** per step, how many files are to get, already here or
  skipped, then a confirmation. Files go into each step's `outputs/`
  folder.
- **Bands:** the downloaded SAVE's bands: energy ranges, gaps, the density
  of states drawn, and for an energy typed in, the bands a BSE up to it
  needs, its size and any degenerate states split.
- **Studies:** convergence studies: **New study…** copies a GW or BSE step
  over several values of one variable (offered in the recommended order,
  the next one for the step chosen); a study's results show as a table and
  a plot against its values, with the tolerance shaded (0.05 eV for
  energies, 0.1 eV for a peak, as the quantity read back).
- **Visual Excitons:** the selected step's results (its SAVE, BSE and QP
  database), or the project's, opened in Visual Excitons.
- **Results:** what the downloaded results hold, read and checked: the
  SAVE's gaps, the GW corrections, the excitons and absorption peaks, and
  warnings where something looks inconsistent.

Details in [Projects](projects.md).
