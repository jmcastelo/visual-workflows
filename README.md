# Visual Workflows

Set up, run and follow [Quantum ESPRESSO](https://www.quantum-espresso.org) and
[Yambo](https://www.yambo-code.eu) calculations on Slurm clusters from a desktop GUI.

> **Status:** planning. The design is in [docs/design.md](docs/design.md).
> "Visual Workflows" is a working name, shared with
> [Visual Excitons](https://github.com/jmcastelo/visual-excitons) as part of a
> suite whose name may change.

What it will do:

- Connect to clusters over SSH (password or key), with no server to install there.
- Build QE and Yambo inputs with graphical tools: structures, k-point grids,
  Brillouin-zone paths, band and energy ranges.
- Build Slurm job scripts with the cluster's partitions and Environment Modules.
- Submit, follow and cancel jobs, and chain them (scf → nscf → p2y → GW → BSE).
- Download outputs selectively and open them in Visual Excitons.

## License

GPL-2.0-or-later, as Visual Excitons and YamboPy.
