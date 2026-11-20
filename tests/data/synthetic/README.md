# Synthetic test data

Written by hand from Slurm's documentation, for features no recorded cluster
has yet (Lynx keeps no accounting). Replace each file with a recording from a
cluster that has the feature (Tirant?) and keep the tests passing.

- `sacct.txt`: `sacct -nP -j <ids> -o <SACCT_FIELDS>`, jobs with their steps
- `sacctmgr-assoc.txt`: `sacctmgr -nP show assoc where user=$USER format=<ASSOC_FIELDS>`
- `sacctmgr-qos.txt`: `sacctmgr -nP show qos format=<QOS_FIELDS>`

The field lists are in visual_workflows/core/scheduler/slurm.py.
