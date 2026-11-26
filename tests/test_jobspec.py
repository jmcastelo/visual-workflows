"""Job descriptions: reading, checks against Lynx and Tirant profiles, scripts."""

import os
import subprocess
import tomllib

import pytest
import tomli_w

from fakes import recorded
from visual_workflows.core import jobstatus
from visual_workflows.core.jobspec import (JobSpec, JobSpecError, Run, check, memory_mb, render,
                                           yambo_walltime)
from visual_workflows.core.profile import ClusterProfile, Partition, Program, discovered_partition
from visual_workflows.core.scheduler import access
from visual_workflows.core.scheduler.slurm import (parse_associations, parse_nodes,
                                                   parse_partitions, parse_qos)
from visual_workflows.core.ssh import ConnectionSettings

SPACK = "/apps/spack/share/spack/modules/linux-arch-x86_64"


def lynx_profile() -> ClusterProfile:
    """Lynx's magma partition as Discover fills it (threads per core from its
    sinfo.json: 2 sockets × 24 cores × 2 threads), with its QE and Yambo."""
    return ClusterProfile(
        name="Lynx", connection=ConnectionSettings(host="lynx.example.org"), module_use=[SPACK],
        partitions={"magma": Partition("", cores_per_node=96, memory_per_node_gb=178.1,
                                       threads_per_core=2, min_memory_per_node_gb=119.2)},
        programs={"qe": Program(["qe/7.3.1b-intelmpi-omp"], launcher_options="--mpi=pmi2",
                                executables={"pw": "pw.x", "ph": "ph.x"}),
                  "yambo": Program(["yambo/intel-libs", "yambo/gpl/5.1.1-intelmpi"],
                                   launcher_options="--mpi=pmi2",
                                   executables={"p2y": "p2y", "yambo": "yambo", "ypp": "ypp"})})


def tirant_profile() -> ClusterProfile:
    """Tirant's partitions as Discover reads them from the recordings."""
    def t(name):
        return recorded("tirant", name)
    assoc, qos = parse_associations(t("sacctmgr-assoc.txt")), parse_qos(t("sacctmgr-qos.txt"))
    nodes = parse_nodes(t("sinfo-nodes.txt"))
    partitions = {p.name: discovered_partition(p, access(p, ["lv00"], assoc, qos), nodes)
                  for p in parse_partitions(t("scontrol-partitions.txt"))}
    return ClusterProfile(
        name="Tirant", connection=ConnectionSettings(host="tirant.example.org"),
        account="lv00", qos="class_t", partitions=partitions,
        programs={"qe": Program(["qe/7.3.1-omp"], executables={"pw": "pw.x"}),
                  "yambo": Program(["yambo/5.3-double-omp"],
                                   executables={"p2y": "p2y", "yambo": "yambo", "ypp": "ypp"})})


def scf(**kw) -> JobSpec:
    values = dict(partition="magma", nodes=1, tasks_per_node=48, cpus_per_task=1,
                  memory="100G", time="12:00:00",
                  runs=[Run("qe", "pw", "pw.scf.in", args="-nk 4")])
    values.update(kw)
    return JobSpec(**values)


def bse(**kw) -> JobSpec:
    values = dict(partition="main", nodes=1, tasks_per_node=24, cpus_per_task=2,
                  memory="80G", time="12:00:00", links={"SAVE": "../03-p2y/SAVE"},
                  runs=[Run("qe", "pw", "pw.nscf.in", args="-nk 4"),
                        Run("yambo", "yambo", "bse.in", jobs=["diago"])])
    values.update(kw)
    return JobSpec(**values)


def messages(problems, errors=True):
    return "\n".join(p.message for p in problems if p.error == errors)


# -- reading and writing [job] ---------------------------------------------------------

TABLE = """
[job]
partition = "magma"
nodes = 2
tasks_per_node = 48
cpus_per_task = 1
memory = "60G"
time = "12:00:00"
links = { SAVE = "../03-p2y/SAVE" }
before = ["echo start"]
[[job.runs]]
program = "qe"
executable = "pw"
input = "pw.scf.in"
args = "-nk 4"
[[job.runs]]
program = "yambo"
executable = "yambo"
input = "bse.in"
jobs = ["diago", "bse"]
"""


def test_table_round_trip():
    spec = JobSpec.from_table(tomllib.loads(TABLE)["job"])
    assert (spec.partition, spec.nodes, spec.tasks, spec.memory_mb) == ("magma", 2, 96, 61440)
    assert spec.time_seconds == 12 * 3600 and spec.links == {"SAVE": "../03-p2y/SAVE"}
    assert spec.runs[0].output_file == "pw.scf.out" and spec.runs[1].jobs == ["diago", "bse"]
    assert spec.runs[1].output_file == ""          # Yambo writes its own files
    again = JobSpec.from_table(tomllib.loads(tomli_w.dumps({"job": spec.to_table()}))["job"])
    assert again == spec


@pytest.mark.parametrize("change, message", [
    ('nodes = 2', 'nodes = "2"'),
    ('cpus_per_task = 1', 'cpus_per_task = true'),
    ('before = ["echo start"]', 'before = "echo start"'),
    ('jobs = ["diago", "bse"]', 'jobs = "diago"'),
])
def test_unreadable_tables(change, message):
    with pytest.raises(JobSpecError, match=r"step.toml: job\."):
        JobSpec.from_table(tomllib.loads(TABLE.replace(change, message))["job"])


def test_runs_must_be_tables():
    with pytest.raises(JobSpecError, match="job.runs"):
        JobSpec.from_table({"runs": {"program": "qe"}})


@pytest.mark.parametrize("text, mb", [("60G", 61440), ("60000", 60000), ("60000M", 60000),
                                      ("1.5T", 1572864), ("60 GB", 61440), ("60g", 61440),
                                      ("", None), ("lots", None)])
def test_memory(text, mb):
    assert memory_mb(text) == mb


@pytest.mark.parametrize("seconds, text", [
    (11 * 3600 + 50 * 60, "11h"),          # not 11h50m: matches only in minutes 50-59
    (26 * 3600 + 30 * 60, "1d2h"),         # not 26h: hours never pass 23
    (48 * 3600 + 59 * 60, "2d"),
    (45 * 60 + 30, "45m"), (90, "1m"), (10, "1m")])
def test_yambo_walltime(seconds, text):
    assert yambo_walltime(seconds) == text



@pytest.mark.skipif(os.name != "posix", reason="runs bash")
@pytest.mark.parametrize("seconds", [11 * 3600 + 1800, 26 * 3600 + 1800, 49 * 3600 - 1800,
                                     45 * 60 + 30, 90, 10])
def test_script_walltime_agrees(seconds):
    """The script's _vwf_walltime gives what yambo_walltime() gives."""
    text = render(bse(), tirant_profile(), "x")
    function = text[text.index("_vwf_walltime() {"):text.index("\n}\n") + 3]
    r = subprocess.run(["bash", "-c", f"_vwf_end=$(( $(date +%s) + {seconds} ))\n{function}"
                        "_vwf_walltime"], capture_output=True, text=True)
    assert r.stdout == yambo_walltime(seconds), r.stderr


# -- checks ----------------------------------------------------------------------------

def test_good_jobs_pass():
    assert check(scf(), lynx_profile()) == []
    assert check(bse(), tirant_profile()) == []


def test_time_against_the_qos_limit():
    # Tirant's main is "unlimited", but class_t allows 3 days
    problems = check(bse(time="4-00:00:00"), tirant_profile())
    assert "above the limit for you on main with QOS class_t (3-00:00:00)" in messages(problems)
    assert problems[0].field == "time"
    assert check(bse(time="3-00:00:00"), tirant_profile()) == []
    assert "not a Slurm time" in messages(check(bse(time="12 hours"), tirant_profile()))
    assert "or UNLIMITED where the partition" in messages(check(bse(time=""), tirant_profile()))


def test_no_time_limit_is_a_choice():
    # Tirant: class_t limits jobs to 3 days, so no
    problems = check(bse(time="UNLIMITED"), tirant_profile())
    assert "Jobs on main with QOS class_t are limited to 3-00:00:00" in messages(problems)
    # Lynx: no limit on magma; allowed, with what it costs
    spec = scf(time="unlimited", runs=[Run("yambo", "yambo", "bse.in")])
    problems = check(spec, lynx_profile())
    assert not messages(problems)
    assert "backfill" in messages(problems, False) and "-walltime" in messages(problems, False)
    assert spec.unlimited and spec.time_seconds is None
    lines = render(spec, lynx_profile(), "x").splitlines()
    assert "#SBATCH --time=0" in lines                 # not left out: DefaultTime may be short
    assert "srun --mpi=pmi2 yambo -F bse.in || exit $?" in lines
    assert "_vwf_walltime" not in "\n".join(lines)
    assert check(scf(time="INFINITE"), lynx_profile())[0].field == "time"


def test_memory_limits():
    tirant = tirant_profile()
    # MaxMemPerNode is 92000 MB on 96310 MB nodes
    assert "above what a job may ask on main (89.8 GB)" in messages(check(bse(memory="92G"), tirant))
    assert check(bse(memory="89G"), tirant) == []
    lynx = lynx_profile()
    assert "more than any magma node has (178.1 GB)" in messages(check(scf(memory="200G"), lynx))
    # Above the smallest nodes: only a warning, the job waits for the larger ones
    problems = check(scf(memory="150G"), lynx)
    assert not messages(problems) and "smallest magma nodes have (119.2 GB)" in messages(problems, False)
    assert "No memory" in messages(check(scf(memory=""), lynx))
    assert "not a memory size" in messages(check(scf(memory="lots"), lynx))


def test_cores_without_hardware_threads():
    # Lynx: 96 CPUs are 48 cores of 2 threads; job scripts use one per core
    problems = check(scf(tasks_per_node=96), lynx_profile())
    assert "96 per node, but magma nodes have 48 cores (96 CPUs counting their 2 threads" \
        in messages(problems)
    assert "96 per node" in messages(check(scf(tasks_per_node=24, cpus_per_task=4), lynx_profile()))
    assert check(scf(tasks_per_node=24, cpus_per_task=2), lynx_profile()) == []
    assert "nodes have 48 cores" in messages(check(bse(tasks_per_node=48), tirant_profile()))
    # Layout not known (a profile from before): CPUs are the bound, with a note
    old = lynx_profile()
    old.partitions["magma"].threads_per_core = 0
    assert check(scf(tasks_per_node=96), old) == []
    assert "not known" in messages(check(scf(tasks_per_node=97), old))


def test_half_empty_nodes_warn():
    problems = check(scf(nodes=4, tasks_per_node=12), lynx_profile())
    assert not messages(problems) and "12 of the 48 cores" in messages(problems, False)


def test_partition_and_programs():
    lynx = lynx_profile()
    assert "no partition 'gpu' (it has magma)" in messages(check(scf(partition="gpu"), lynx))
    assert "No partition" in messages(check(scf(partition=""), lynx))
    bare = lynx_profile()
    bare.partitions = {}
    problems = check(scf(), bare)
    assert not messages(problems) and "Discover" in messages(problems, False)
    assert "no program 'vasp'" in messages(check(scf(runs=[Run("vasp", "vasp", "INCAR")]), lynx))
    assert "no executable 'dos' in the Lynx profile (it has pw, ph)" in \
        messages(check(scf(runs=[Run("qe", "dos", "dos.in")]), lynx))
    assert "No runs" in messages(check(scf(runs=[]), lynx))


def test_runs():
    lynx = lynx_profile()
    assert "no input file" in messages(check(scf(runs=[Run("qe", "pw")]), lynx))
    assert "overwrite the input" in messages(check(scf(runs=[Run("qe", "pw", "a", "a")]), lynx))
    def yambo_jobs(*jobs):
        return check(scf(runs=[Run("yambo", "yambo", "bse.in", jobs=list(jobs))]), lynx)
    assert yambo_jobs("bse/diago", "../04-gw/outputs/gw", "/scratch/gw") == []
    assert "which separates names" in messages(yambo_jobs("diago,bse"))
    assert "inside the step's folder" in messages(yambo_jobs("../diago"))
    assert "inside the step's folder" in messages(yambo_jobs("/scratch/diago"))
    problems = yambo_jobs("a/b/c/diago")
    assert not messages(problems) and "4 levels deep" in messages(problems, False)
    problems = check(scf(runs=[Run("qe", "pw", "pw.in", args="-nk 2 -inp other.in")]), lynx)
    assert "in their fields" in messages(problems, False)
    problems = check(scf(runs=[Run("yambo", "p2y", args="-I ../02-nscf/si.save -O .", mpi=False)]),
                     lynx)
    assert not messages(problems) and "without MPI, on one task of the 48" in messages(problems, False)
    assert "no '/'" in messages(check(scf(links={"a/b": "../x"}), lynx))


# -- the script ------------------------------------------------------------------------

def test_lynx_script():
    text = render(scf(), lynx_profile(), "01-scf")
    lines = text.splitlines()
    assert lines[0] == "#!/bin/bash"
    # 2 threads per core: each task asks for both threads of its core (not
    # --hint=nomultithread, which Slurm refuses where it counts threads as CPUs)
    for option in ("--job-name=01-scf", "--partition=magma", "--nodes=1", "--ntasks-per-node=48",
                   "--cpus-per-task=2", "--mem=100G", "--time=12:00:00"):
        assert f"#SBATCH {option}" in lines
    assert not any("--hint" in line for line in lines)
    assert "export OMP_NUM_THREADS=1" in lines                 # the threads asked for, not the CPUs
    assert not any("--account" in line or "--qos" in line for line in lines)
    assert f"module use {SPACK}" in lines and "module load qe/7.3.1b-intelmpi-omp" in lines
    assert "srun --mpi=pmi2 pw.x -nk 4 -inp pw.scf.in > pw.scf.out || exit $?" in lines
    assert "OMP_PLACES" not in text and "_vwf_walltime" not in text
    assert "export SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK" in lines


def test_tirant_script():
    text = render(bse(), tirant_profile(), "06-bse")
    lines = text.splitlines()
    assert "#SBATCH --account=lv00" in lines and "#SBATCH --qos=class_t" in lines
    assert "#SBATCH --hint=nomultithread" not in lines     # one thread per core
    assert "export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK" in lines
    assert "export OMP_PLACES=cores OMP_PROC_BIND=close" in lines
    assert "ln -sfn ../03-p2y/SAVE SAVE" in lines
    # Two programs: a clean environment before each
    assert lines.count("module purge") == 2
    assert lines.index("module load qe/7.3.1-omp") < lines.index("module load yambo/5.3-double-omp")
    assert "_vwf_end=$(( $(date +%s) + 42900 ))" in lines     # 12 h less 5 minutes
    assert 'srun yambo -F bse.in -J diago -walltime "$(_vwf_walltime)" || exit $?' in lines


def test_other_launchers_and_serial_runs():
    profile = lynx_profile()
    profile.programs["qe"].mpi_launcher, profile.programs["qe"].launcher_options = "mpirun", ""
    profile.programs["yambo"].mpi_launcher = "mpiexec"
    spec = scf(runs=[Run("qe", "pw", "pw.in"),
                     Run("yambo", "p2y", args="-I ../02-nscf/si.save -O .", mpi=False),
                     Run("yambo", "ypp", "ypp.in", jobs=["diago"], output="ypp.log")],
               time="")
    lines = render(spec, profile, "x").splitlines()
    assert "mpirun -np $SLURM_NTASKS pw.x -inp pw.in > pw.out || exit $?" in lines
    assert "p2y -I ../02-nscf/si.save -O . || exit $?" in lines
    assert "mpiexec -n $SLURM_NTASKS --mpi=pmi2 ypp -F ypp.in -J diago > ypp.log || exit $?" in lines
    assert not any(line.startswith("#SBATCH --time") for line in lines)


def test_quoting_and_lines_before_and_after():
    spec = scf(links={"my SAVE": "../a b/SAVE"}, before=["date"], after=["ls -l"],
               runs=[Run("qe", "pw", "my input.in")])
    lines = render(spec, lynx_profile(), "x").splitlines()
    assert "ln -sfn '../a b/SAVE' 'my SAVE'" in lines
    assert "srun --mpi=pmi2 pw.x -inp 'my input.in' > 'my input.out' || exit $?" in lines
    # Links first, so that lines before the runs can use them
    assert lines.index("ln -sfn '../a b/SAVE' 'my SAVE'") < lines.index("date")
    assert lines[-1] == "ls -l"


def test_status_lines_go_after_the_header():
    text = jobstatus.add_status_lines(render(bse(), tirant_profile(), "06-bse"))
    lines = text.splitlines()
    begin = lines.index(jobstatus.BEGIN)
    assert all(line.startswith("#") for line in lines[:begin])
    assert lines[begin - 1] == "#SBATCH --qos=class_t"


@pytest.mark.skipif(os.name != "posix", reason="runs bash")
def test_script_runs(tmp_path):
    """The script under bash, with srun and module replaced by recorders."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("srun", "module"):
        tool = bin_dir / name
        tool.write_text(f'#!/bin/sh\necho "{name} $*" >> "$CALLS"\n')
        tool.chmod(0o755)
    (tmp_path / "job.sh").write_text(
        jobstatus.add_status_lines(render(bse(time="1-02:00:00"), tirant_profile(), "06-bse")))
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", CALLS=str(tmp_path / "calls"),
               SLURM_CPUS_PER_TASK="2", SLURM_JOB_ID="7")
    r = subprocess.run(["bash", "job.sh"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls[:2] == ["module purge", "module load qe/7.3.1-omp"]
    assert calls[2] == "srun pw.x -nk 4 -inp pw.nscf.in"
    # 26 hours less 5 minutes, in whole hours: 1 day and 1 hour
    yambo = next(c for c in calls if c.startswith("srun yambo"))
    assert yambo == "srun yambo -F bse.in -J diago -walltime 1d1h"
    assert calls[-1] == "module -t list"                   # the status lines, at exit
    assert (tmp_path / "SAVE").is_symlink()
    status = (tmp_path / ".vwf" / "job-7.status").read_text()
    assert "ended" in status and status.rstrip().endswith(" 0")


@pytest.mark.skipif(os.name != "posix", reason="runs bash")
def test_script_stops_at_a_failed_run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "srun").write_text('#!/bin/sh\necho "srun $*" >> "$CALLS"\nexit 3\n')
    (bin_dir / "module").write_text("#!/bin/sh\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    (tmp_path / "job.sh").write_text(render(bse(), tirant_profile(), "06-bse"))
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", CALLS=str(tmp_path / "calls"),
               SLURM_CPUS_PER_TASK="2")
    r = subprocess.run(["bash", "job.sh"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 3
    assert len((tmp_path / "calls").read_text().splitlines()) == 1     # Yambo never ran


def test_a_qos_of_its_own():
    """A step may use another QOS than the profile's (Tirant's interactive)."""
    tirant = tirant_profile()
    assert tirant.partitions["interactive"].qos == {"interactive": "01:00:00"}
    assert tirant.partitions["main"].qos == {"class_t": "3-00:00:00"}
    quick = bse(partition="interactive", time="00:30:00")
    # The profile's class_t is not accepted there
    assert "QOS 'class_t' (the profile's) is not one interactive accepts for you (it takes " \
        "interactive)" in messages(check(quick, tirant))
    quick.qos = "interactive"
    assert check(quick, tirant) == []
    quick.time = "02:00:00"
    assert "above the limit for you on interactive with QOS interactive (01:00:00)" in \
        messages(check(quick, tirant))
    lines = render(bse(partition="interactive", qos="interactive", account="lv99"),
                   tirant, "x").splitlines()
    assert "#SBATCH --qos=interactive" in lines and "#SBATCH --account=lv99" in lines
    # Kept in step.toml
    again = JobSpec.from_table(tomllib.loads(tomli_w.dumps({"job": bse(qos="interactive").to_table()}))["job"])
    assert again.qos == "interactive" and again.account == ""


# -- the nodes to use ---------------------------------------------------------------------

def nodes_profile() -> ClusterProfile:
    profile = lynx_profile()
    profile.partitions["magma"].nodes = ["compute01", "compute02", "compute03"]
    return profile


def test_node_names():
    from visual_workflows.core.jobspec import node_names
    assert node_names(["compute[01-02]", "compute02,compute03"]) == \
        ["compute01", "compute02", "compute03"]
    assert node_names("compute01 compute03") == ["compute01", "compute03"]
    assert node_names(["odd[x-y]"]) == ["odd[x-y]"]       # not a range: kept, then refused


def test_nodelist_round_trip_and_script():
    spec = scf(nodes=2, nodelist=["compute[01-02]"])
    again = JobSpec.from_table(tomllib.loads(tomli_w.dumps({"job": spec.to_table()}))["job"])
    assert again.nodelist == ["compute[01-02]"]
    assert "nodelist" not in scf().to_table()
    assert "#SBATCH --nodelist=compute01,compute02" in render(spec, nodes_profile(), "scf")
    assert "--nodelist" not in render(scf(), nodes_profile(), "scf")


def test_nodelist_checks():
    profile = nodes_profile()
    assert not messages(check(scf(nodelist=["compute03"]), profile))
    assert not messages(check(scf(nodelist=["compute03"]), profile), errors=False)
    assert "compute09 is not magma's" in messages(check(scf(nodelist=["compute09"]), profile))
    assert "has 2 nodes but the job asks for 1" in messages(
        check(scf(nodelist=["compute01", "compute02"]), profile))
    assert "Slurm chooses the others" in messages(
        check(scf(nodes=2, nodelist=["compute01"]), profile), errors=False)
    # Without the partition's nodes in the profile: only a warning
    assert "Discover fills them in" in messages(
        check(scf(nodelist=["compute09"]), lynx_profile()), errors=False)
