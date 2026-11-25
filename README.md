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

**Under development.** Phases 1 to 4 are complete: clusters, projects, jobs,
logs and downloads; steps described by their resources and runs, with their
job scripts written for you; checks, before a job is queued, of the
resources, the modules, the QE and Yambo inputs and their parallelisation,
the pseudopotentials and the chain of steps; pw.x inputs built from
structure files, with PseudoDojo pseudopotentials; and GW and BSE inputs for
Yambo, so that the scf → nscf → p2y → GW → BSE chain is made in one go and
its results open in Visual Excitons (tried on bulk silicon). Next come
convergence studies and the hand-off to Visual Excitons (see the
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
  - **Checked in part:** p2y, only its command-line options; ypp, its
    command-line options and its inputs' names and values.
  - **Not checked yet:** ph.x, projwfc.x and other QE tools. ph.x is
    planned.
- **Inputs it builds:** pw.x inputs, from structure files (CIF, POSCAR…):
  the cell with its own `ibrav` or `ibrav = 0`, k-point grids and band
  paths, PseudoDojo pseudopotentials with their cutoff hints; GW and BSE
  inputs for Yambo, checked against the cluster's own yambo; and the scf →
  nscf → p2y → GW → BSE chain for Visual Excitons. ypp inputs are not
  built.
- **What it does not do:** it does not judge the physics (convergence,
  sensible parameters). Nothing is installed on the cluster.

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
vwf clusters discover Lynx
vwf clusters program Lynx qe --modules qe/7.3.1b-intelmpi-omp --launcher-options=--mpi=pmi2
vwf project new WSe2 --cluster Lynx && cd WSe2
vwf project add-step scf --partition magma --tasks-per-node 48 --mem 60G \
    --time 12:00:00 --run "pw ../pw.scf.in -nk 4"
vwf project add-step nscf --after scf --partition magma --tasks-per-node 48 \
    --mem 60G --time 12:00:00 --run "pw ../pw.nscf.in -nk 4"
vwf project check && vwf project submit && vwf project watch
vwf project get --for-excitons
```

Or the whole chain to excitons from a structure file:

```bash
vwf pseudos get --from WSe2.cif
vwf structure WSe2.cif --input > scf.in        # prefix, pseudopotentials, cutoffs, k-points
vwf project add-qe-chain scf.in --gw --bse gw --partition magma \
    --tasks-per-node 48 --mem 60G --time 12:00:00
vwf project check && vwf project submit 01-scf 02-nscf 03-p2y && vwf project watch
vwf project get 03-p2y && vwf project bands --fit   # GW and BSE bands: whole degenerate sets
vwf project refresh-input gw --complete        # the cluster's yambo reads the inputs
vwf project refresh-input bse --complete
vwf project submit && vwf project watch
vwf project get --for-excitons
vwf project results                            # gaps, GW corrections, excitons, warnings
vwf project excitons 05-bse                    # opened in Visual Excitons
```

The GW and BSE inputs are starting points for a quick test, not converged
calculations: k-points, bands and cutoffs need raising for real results.

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
- [Decisions and questions for the experts](docs/expert-questions.md): the physics choices the program makes, each with its reason, the silicon test, and what stays open for QE and Yambo experts.

The codes the program runs (references from their official documentation):

- [Quantum ESPRESSO](docs/quantum-espresso.md): running pw.x, its parallelisation levels and their rules, the input file, the `.save` folder and what Yambo takes from it, ph.x, projwfc.x and bands.x, pitfalls for job scripts.
- [Yambo](docs/yambo.md): p2y, yambo and ypp and their command lines, the input file, the parallel variables and their rules, BSE and GW settings and databases, the chain from QE, pitfalls.

## License

GPL-2.0-or-later, as Visual Excitons and YamboPy.
