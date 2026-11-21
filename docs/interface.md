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

A project: a folder with one sub-folder per step, mirrored on the cluster.

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
  memory per node, time, or no time limit), with the partition's limits
  shown below it (cores, threads per core, node memory, the job memory
  limit, the time limit) and what the job takes of each node; links made
  before the runs (`SAVE` → `../03-p2y/SAVE`) and extra shell lines. On the
  right, the job script as it will be written, and the checks' results,
  errors in red, warnings in amber, updated as you type. The pseudopotentials
  and modules on the cluster, and the chain of steps, are checked at
  **Check** and **Submit**.
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
- **Refresh**, or **Every 60 s**: updates each step's state. Watching stops by
  itself when every step has ended.
- **The step table:** job, state, reason, result, start and end, and the steps
  it runs after.
- **The lower pane:** the selected step's output (Slurm's output file),
  followed live while the job runs.
- **Get results…:** choose what to download (results without wave functions
  and big files, what Visual Excitons reads, or everything), see per step how
  many files are to get, already here or skipped, and confirm. Files go into
  each step's `outputs/` folder.

Details in [Projects](projects.md).
