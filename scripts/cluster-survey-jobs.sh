#!/bin/bash -l
# Records how Slurm reports jobs in each state, for Visual Workflows to parse:
# pending (held), running, failed (exit code 3), timeout, cancelled.
#
# Submits four tiny jobs: 1 CPU and 100 MB each, the longest lasting at most
# ~3 minutes. The whole run takes about 4 minutes.
#     bash -l cluster-survey-jobs.sh [partition]
# Writes ./cluster-survey-jobs-<host>/ and cluster-survey-jobs-<host>.tar.gz.

partition=${1:+--partition=$1}
host=$(uname -n)
host=${host%%.*}
out="cluster-survey-jobs-$host"
mkdir -p "$out"
cd "$out" || exit 1

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

common="$partition --ntasks=1 --cpus-per-task=1 --mem=100M --parsable"

echo "Submitting test jobs"
failed=$(sbatch $common --job-name=vw-failed --time=5 --output=failed-%j.out \
    --wrap='echo started; module -t list 2>&1; env | grep ^SLURM_ | sort; sleep 60; echo failing; exit 3')
timeout=$(sbatch $common --job-name=vw-timeout --time=1 --output=timeout-%j.out \
    --wrap='echo started; trap "echo got SIGTERM" TERM; sleep 300 & wait; sleep 300')
held=$(sbatch $common --job-name=vw-held --time=1 --hold --output=held-%j.out --wrap='true')
cancelled=$(sbatch $common --job-name=vw-cancelled --time=5 --output=cancelled-%j.out \
    --wrap='echo started; sleep 300')
ids="$failed,$timeout,$held,$cancelled"
echo "  jobs $ids" | tee ids.txt
echo "failed=$failed timeout=$timeout held=$held cancelled=$cancelled" >> ids.txt

sleep 20
record 1-squeue-json       "squeue --json --me"
record 1-squeue-text       "squeue --me -h -o '%i|%j|%P|%T|%r|%M|%l|%D|%C|%V|%S|%Z'"
# scontrol takes one job id at a time
record 1-scontrol          "for j in ${ids//,/ }; do scontrol show job -o \$j; done"
record 1-scontrol-json     "scontrol show job --json $failed"

scancel "$cancelled"
sleep 5
record 2-after-scancel     "scontrol show job -o $cancelled; squeue --me -h -o '%i|%T|%r' -j $cancelled"
scancel "$held"

# Wait for the failed and timeout jobs to end (at most ~4 minutes)
for _ in $(seq 24); do
    [ -z "$(squeue -h -j "$failed,$timeout" -o %i 2>/dev/null)" ] && break
    sleep 10
done
# Slurm keeps ended jobs for MinJobAge (often 5 minutes): squeue lists them
# only with --states=all
record 3-ended-scontrol      "for j in ${ids//,/ }; do scontrol show job -o \$j; done"
record 3-ended-scontrol-json "for j in ${ids//,/ }; do scontrol show job --json \$j; done"
record 3-ended-squeue        "squeue --json --me --states=all"
record 3-ended-squeue-text   "squeue --me --states=all -h -o '%i|%j|%P|%T|%r|%M|%l|%D|%C|%V|%S|%Z'"
# Accounting, where the cluster keeps it (it may take a few seconds to appear)
record 3-ended-sacct         "sacct -nP -j $ids -o JobID,JobName,Partition,State,ExitCode,Elapsed,Timelimit,NCPUS,NNodes,NodeList,Submit,Start,End,WorkDir,MaxRSS"
record 3-outputs             'for f in *.out; do echo "=== $f"; cat "$f"; done'

cd ..
tar czf "$out.tar.gz" "$out"
echo "Done: $out.tar.gz"
