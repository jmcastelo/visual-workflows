# Tirant test data

Command outputs recorded on Tirant (Rocky Linux 8.10, Slurm 24.11.5 with
accounting, Lmod 8.7.55) on 2026-11-20 with `scripts/cluster-survey.sh` and
`scripts/cluster-survey-jobs.sh`, keeping only each command's output.
`sinfo-nodes.txt` was recorded again on 2026-11-21 with the node layout
(`%z`, last field).

Made anonymous: the user name (`alice`), the research group (`lv00`, also in
folder paths) and the numeric user and group ids (2001). Job ids, node,
partition, QOS and module names are real.

The queue was busy: of the four test jobs (`job-ids.txt`), "held" and
"cancelled" were cancelled while still pending, and "failed" and "timeout"
had not started when this was recorded. `squeue-cancelled.*`,
`scontrol-jobs-cancelled.txt` and `sacct-pending-cancelled.txt` show that
moment. `module-avail-j.txt` is Lmod's answer to `module -j avail`, an option
it does not have.

`cluster.toml` maps the program's commands to these files, with what the program must find here (tests/test_recorded_clusters.py).

Recorded again on 2026-11-21 with `vwf clusters record` (what the program
asks, files named after the commands: `sinfo-h-n.txt`, `module-t-avail.txt`,
`squeue-json-user.txt`, …, with Python's version, missing before), after a
test job (`01-scf`, job 400501) had run. Made anonymous as before: the user
name (`alice`, also in paths and `mail_user`), the research group (`lv00`,
in module and home paths), the numeric user and group ids (2001).
`cluster.toml` uses these files and, for jobs at the end of their lives, the
survey's of 2026-11-20; the unit tests (`test_slurm.py`, `test_modules.py`)
read the 2026-11-20 files, a fixed snapshot they check in detail.

