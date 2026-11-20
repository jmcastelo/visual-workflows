# Tirant test data

Command outputs recorded on Tirant (Rocky Linux 8.10, Slurm 24.11.5 with
accounting, Lmod 8.7.55) on 2026-11-20 with `scripts/cluster-survey.sh` and
`scripts/cluster-survey-jobs.sh`, keeping only each command's output.

Made anonymous: the user name (`alice`), the research group (`lv00`, also in
folder paths) and the numeric user and group ids (2001). Job ids, node,
partition, QOS and module names are real.

The queue was busy: of the four test jobs (`job-ids.txt`), "held" and
"cancelled" were cancelled while still pending, and "failed" and "timeout"
had not started when this was recorded. `squeue-cancelled.*`,
`scontrol-jobs-cancelled.txt` and `sacct-pending-cancelled.txt` show that
moment. `module-avail-j.txt` is Lmod's answer to `module -j avail`, an option
it does not have.
