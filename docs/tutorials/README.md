# Tutorials

[← Documentation](../../README.md#documentation)

Each tutorial takes one material from its crystal structure to its excitons,
explaining the physics where each step needs it. Each step shows how to do it
in the window first, then the same with `vwf` on the command line. The
numbers given are the ones we got; yours should agree with them closely.

1. [Silicon](1-silicon.md): a bulk semiconductor. The whole path: DFT, GW,
   BSE, convergence studies and the excitons' dispersion.
2. hBN monolayer (to come): a layer in vacuum, with the Coulomb cutoff and
   RIM-W, and strongly bound excitons.
3. MoS₂ monolayer (to come): spin–orbit coupling and its A and B excitons.

Do them in order: each one builds on the one before it.

## Before you start

- **The program installed** ([Installation](../installation.md)).
- **A cluster profile** that connects, with the partitions found by
  **Discover**, and the `qe` and `yambo` modules chosen and passing **Check
  modules** ([Clusters](../clusters.md)). The tutorials use Lynx's `magma`
  partition; on another cluster, use your own partition and memory and keep
  the rest.
- **About an hour of compute time** for silicon's first part, and a few
  hours more for its convergence studies. The jobs are small (one node,
  16 GB) and mostly wait in the queue.

The physics explanations are written for newcomers to QE and Yambo, and are
being reviewed by experts. Each block ends with where to read more.

## On Lynx: yambo on one MPI task

On Lynx, yambo on several MPI tasks spends minutes waiting on file locks
of the NFS home ([Troubleshooting](../troubleshooting.md#yambo-on-several-mpi-tasks-is-slow-on-lynx)).
The tutorials therefore give their GW and BSE steps 1 MPI task with 16
OpenMP threads. On a cluster with a parallel filesystem, MPI tasks are fine.
