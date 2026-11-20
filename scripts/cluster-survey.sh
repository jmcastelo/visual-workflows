#!/bin/bash -l
# Records what Visual Workflows needs to know about a cluster: Slurm version
# and output formats, partitions, the module system, Python, disk space.
#
# Run it on the cluster's login node:
#     bash -l cluster-survey.sh
# It writes one file per command into ./cluster-survey-<host>/ and packs them
# into cluster-survey-<host>.tar.gz. Nothing is changed on the cluster.
#
# For the job commands to show something useful, have one job pending or
# running while it runs, and at least one finished in the last 7 days.

host=$(uname -n)
host=${host%%.*}
out="cluster-survey-$host"
mkdir -p "$out"

# Runs a command and saves its output, errors and exit code in <name>.txt
record() {
    local name=$1 rc; shift
    {
        echo "\$ $*"
        echo "----"
        eval "$@" 2>&1
        rc=$?
        echo "----"
        echo "exit code: $rc"
    } > "$out/$name.txt"
    echo "  $name"
}

echo "Recording into $out/"

# System
record os               'uname -a; cat /etc/os-release'
record python           'command -v python3; python3 --version; command -v python; python --version'
record shell            'echo "SHELL=$SHELL"; bash --version | head -1'
record tools            'for t in rsync tar gzip zstd sftp scp; do printf "%s: " $t; command -v $t || echo missing; done'

# Slurm
record slurm-version    'sinfo --version'
record sinfo-json       'sinfo --json'
record sinfo-summary    'sinfo -s'
record sinfo-text       'sinfo -h -o "%P|%a|%l|%D|%c|%m|%G|%t|%N"'
record sinfo-nodes      'sinfo -h -N -o "%N|%P|%c|%m|%G|%t|%C|%O|%e"'
record partitions       'scontrol show partition -o'
record nodes            'scontrol show node -o | head -20'
record squeue-json      'squeue --json --me'
record squeue-text      'squeue --me -h -o "%i|%j|%P|%T|%r|%M|%l|%D|%C|%V|%S|%Z"'
record sacct-json       'sacct --json -S now-7days'
record sacct-text       'sacct -nP -S now-7days -o JobID,JobName,Partition,State,ExitCode,Elapsed,Timelimit,NCPUS,NNodes,MaxRSS,ReqMem,Submit,Start,End,WorkDir'
record assoc            'sacctmgr -nP show assoc user=$USER format=Cluster,Account,User,Partition,QOS,DefaultQOS,GrpTRES,MaxWall'
record qos              'sacctmgr -nP show qos format=Name,MaxWall,MaxTRESPerUser,MaxJobsPerUser,MaxSubmitJobsPerUser'
record slurm-config     'scontrol show config'
record sbatch-help      'sbatch --help'

# Environment Modules
record module-version   'module --version; echo "LMOD_VERSION=$LMOD_VERSION"; echo "MODULESHOME=$MODULESHOME"; type module'
record module-avail     'module -t avail'
record module-spider    'module -t spider'
record module-list      'module -t list'
record module-qe-yambo  'module -t avail 2>&1 | grep -iE "espresso|qe|yambo|intel|gcc|openmpi|mpich|mkl|hdf5|netcdf|libxc"'
record executables      'for e in pw.x ph.x p2y yambo ypp mpirun srun; do printf "%s: " $e; command -v $e || echo "not in PATH"; done'

# Disk
record disk             'echo "HOME=$HOME"; df -h "$HOME"; quota -s; ls -ld /scratch* /lustre* /gpfs* /work* /storage* 2>/dev/null; env | grep -iE "^(scratch|work|store|tmp)"'

tar czf "$out.tar.gz" "$out"
echo "Done: $out.tar.gz"
