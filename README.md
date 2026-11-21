# Visual Workflows

Visual Workflows runs [Quantum ESPRESSO](https://www.quantum-espresso.org) and
[Yambo](https://www.yambo-code.eu) calculations on Slurm clusters from your
desktop. It connects over SSH (key, password or one-time codes), finds out
what each cluster offers (partitions, limits, Environment Modules, Spack),
checks that a program's modules really load before you queue a job, submits
chains of calculations in the right order, follows them, and brings back the
results you need, for example exactly the databases
[Visual Excitons](https://github.com/jmcastelo/visual-excitons) reads.

Nothing is installed on the cluster: everything goes through the commands any
Slurm cluster has. Jobs record how they end, so the program knows what
happened to them even on clusters without job accounting, and when your PC
was off.

It comes as a graphical program, `visual-workflows`, and a command-line tool,
`vwf`, on the same core.

**Under development.** Phase 1 is complete: clusters, projects, jobs, logs
and downloads, with job scripts and inputs you bring. Next come a job script
builder and graphical tools for QE and Yambo inputs (see the
[design](docs/design.md#11-phases)). "Visual Workflows" is a working name: it
forms a suite with Visual Excitons, whose name may change.

## Scope and limits

- **Clusters:** Slurm only, reached over SSH; Environment Modules or Lmod
  (Spack modules included). Other schedulers (PBS, LSF) are not supported.
- **Programs:** any program can run in a job, but the program knows Quantum
  ESPRESSO and Yambo, for excitons: it writes job scripts for them and
  checks, before a job is queued, what would make them fail or silently do
  something else.
  - **Checked fully:** pw.x and yambo, meaning their inputs, their
    parallelisation, and the pseudopotentials pw.x will read. This covers
    QE 7.3 to 7.6 and Yambo 5.1 to 5.4, with the checks read from those
    versions' source code.
  - **Checked in part:** p2y and ypp, only their command-line options.
  - **Not checked yet:** ph.x, projwfc.x and other QE tools. ypp inputs and
    ph.x are planned.
- **What it does not do:** it does not judge the physics (convergence,
  sensible parameters). It does not build inputs yet; graphical tools for
  them come in a later phase. Nothing is installed on the cluster.

## Quick start

```
python -m venv visual-workflows-env
source visual-workflows-env/bin/activate        # on Windows: visual-workflows-env\Scripts\activate
pip install git+https://github.com/jmcastelo/visual-workflows.git
visual-workflows
```

In the program: **Clusters ▸ New cluster**, fill in the host and your
username, **Save**, then **Test connection** and **Discover**. See
[Installation](docs/installation.md) and [Clusters](docs/clusters.md).

From the command line:

```bash
vwf clusters add Lynx --host lynx.example.org --user alice --key ~/.ssh/id_ed25519
vwf test Lynx
vwf project new WSe2 --cluster Lynx && cd WSe2
vwf project add-step scf --script ../scf.sh --input ../pw.scf.in ../pseudo
vwf project add-step nscf --script ../nscf.sh --input ../pw.nscf.in --after scf
vwf project submit && vwf project watch
vwf project get --for-excitons
```

## Documentation

Using the program:

- [Installation](docs/installation.md): requirements, installing, the system keyring, and where the program keeps its files.
- [The main window](docs/interface.md): the cluster selector and the Cluster, Jobs, Files and Project tabs.
- [Clusters](docs/clusters.md): cluster profiles, logging in (keys, passwords, one-time codes, host keys, remembered passwords), testing and discovering a cluster, modules and Spack, choosing and checking each program's modules, and sharing profiles.
- [Projects](docs/projects.md): projects and steps, job scripts, submitting chains, what a job's state means and where it comes from, logs, and getting results.
- [Command line](docs/command-line.md): every `vwf` command.
- [Troubleshooting](docs/troubleshooting.md): common problems and what to do.

Working on the program:

- [Architecture](docs/architecture.md): layers, modules, the main flows, the async model, file formats and compatibility rules.
- [Development](docs/development.md): setting up a development copy, the tests and their fakes, recording a new cluster, and extending the program.
- [Design](docs/design.md): goals, decisions with their reasons, the clusters surveyed, and the phases.
- [YamboPy issues](docs/yambopy-issues.md): problems found in YamboPy, written to be filed on GitHub.

The codes the program runs (references from their official documentation):

- [Quantum ESPRESSO](docs/quantum-espresso.md): running pw.x, its parallelisation levels and their rules, the input file, the `.save` folder and what Yambo takes from it, ph.x, projwfc.x and bands.x, pitfalls for job scripts.
- [Yambo](docs/yambo.md): p2y, yambo and ypp and their command lines, the input file, the parallel variables and their rules, BSE and GW settings and databases, the chain from QE, pitfalls.

## License

GPL-2.0-or-later, as Visual Excitons and YamboPy.
