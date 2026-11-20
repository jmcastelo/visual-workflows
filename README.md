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

## Command line

The GUI is not there yet; the `vwf` command uses the same core:

```bash
vwf clusters add Lynx --host lynx.example.org --user alice --key ~/.ssh/id_ed25519
vwf test Lynx                 # connect; Slurm version, accounting, groups, Python, modules
vwf partitions Lynx           # partitions you may use, free CPUs, default memory
vwf jobs Lynx                 # your jobs: queued, running, ended in the last minutes
vwf run Lynx -- module -t avail
vwf submit Lynx job.sh --to calcs/WSe2   # upload job.sh and submit it there
vwf cancel Lynx 393491
```

Projects run steps in order, each in its own folder, mirrored on the cluster:

```bash
vwf project new WSe2 --cluster Lynx
cd WSe2
vwf project add-step scf --script ../scf.sh --input ../pw.scf.in ../pseudo
vwf project add-step nscf --script ../nscf.sh --input ../pw.nscf.in --after scf
vwf project submit        # uploads and submits every step not done; nscf waits for scf
vwf project status        # how each step's job stands, also after Slurm forgot it
vwf project watch         # check every minute until every step has ended
vwf project log nscf -f   # the end of a step's output, following it
vwf project get --dry-run # what would be downloaded into each step's outputs/
vwf project get           # all but wave functions and files over 1 GB
vwf project get --for-excitons   # what Visual Excitons reads; --all for everything
```

`--host` may be an alias from `~/.ssh/config`. Without `--key`, the ssh-agent
and the default keys are tried; `--auth password` asks for a password.

## License

GPL-2.0-or-later, as Visual Excitons and YamboPy.
