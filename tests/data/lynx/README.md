# Lynx test data

Command outputs recorded on Lynx (Slurm 26.05.4, Environment Modules 5.3.1)
on 2026-11-20 with `scripts/cluster-survey.sh` and
`scripts/cluster-survey-jobs.sh`, keeping only each command's output.

Made anonymous: user names, home folders and numeric user/group ids are
replaced (`alice`, `bob`, 2001). Job ids, node and partition names are real.

`job-ids.txt` names the four test jobs: held (then cancelled), failed
(exit code 3), timeout (1-minute limit), cancelled while running.
`scontrol-jobs-ended.jsons` is one JSON document per job, one after another.

`sinfo-nodes.txt` is in the format used before the node layout (`%z`) was
asked for, and its tests check that such lines still read. Lynx's layout,
2 sockets of 24 cores with 2 threads each, is in `sinfo.json` and in
`sinfo-h-n.txt`.

Recorded again on 2026-11-23 with `vwf clusters record` (user and home
folder written as `alice`): `sinfo-h-n.txt` (the nodes with `%z`),
`id-gn.txt`, `python3-version.txt`, `module-version-echo.txt`, and the
modules with the profile's Spack folder (`module-use-apps*.txt`). The queue
was empty then, so the survey's ended test jobs are kept for the tests.

`cluster.toml` maps the program's commands to these files, with what the program must find here (tests/test_recorded_clusters.py).
