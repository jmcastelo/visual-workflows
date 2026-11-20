#!/bin/bash -l
# Finishes cluster-survey-jobs.sh on a busy cluster: waits for its "failed"
# and "timeout" test jobs to end (they may wait in the queue for a long time),
# then records how Slurm reports them, and packs everything again.
#
# Run it on the login node, in the background so you can log out:
#     nohup bash -l cluster-survey-jobs-finish.sh > finish.log 2>&1 &
# It only sleeps while waiting (one squeue every 60 s). When it is done,
# cluster-survey-jobs-<host>.tar.gz is updated: copy it back.

host=$(uname -n)
host=${host%%.*}
out="cluster-survey-jobs-$host"
cd "$out" || { echo "No $out folder here: run cluster-survey-jobs.sh first"; exit 1; }
# ids.txt's last line: failed=… timeout=… held=… cancelled=…
eval "$(tail -1 ids.txt)"
ids="$failed,$timeout,$held,$cancelled"

record() {
    local name=$1 rc; shift
    {
        echo "\$ $*"
        echo "----"
        eval "$@" 2>&1
        rc=$?
        echo "----"
        echo "exit code: $rc"
    } > "$name.txt"
    echo "  $name"
}

echo "Waiting for jobs $failed and $timeout to end ($(date))"
while [ -n "$(squeue -h -j "$failed,$timeout" -o %i 2>/dev/null)" ]; do
    sleep 60
done
echo "Ended ($(date)); recording"

# Slurm keeps ended jobs for MinJobAge (often 5 minutes): record at once
record 4-ended-scontrol      "for j in ${ids//,/ }; do scontrol show job -o \$j; done"
record 4-ended-scontrol-json "for j in ${ids//,/ }; do scontrol show job --json \$j; done"
record 4-ended-squeue        "squeue --json --me --states=all"
record 4-ended-squeue-text   "squeue --me --states=all -h -o '%i|%j|%P|%T|%r|%M|%l|%D|%C|%V|%S|%Z'"
# Accounting, where the cluster keeps it (it may take a few seconds to appear)
sleep 10
record 4-ended-sacct         "sacct -nP -j $ids -o JobID,JobName,Partition,State,ExitCode,Elapsed,Timelimit,NCPUS,NNodes,NodeList,Submit,Start,End,WorkDir,MaxRSS"
record 4-ended-sacct-json    "sacct --json -j $ids"
record 4-outputs             'for f in *.out; do echo "=== $f"; cat "$f"; done'

cd ..
tar czf "$out.tar.gz" "$out"
echo "Done: $out.tar.gz ($(date))"
