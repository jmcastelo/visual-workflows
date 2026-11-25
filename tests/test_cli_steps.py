"""The command line for steps with a job description: `vwf project add-step`
with --run and resources, `vwf project script`, `vwf project check`."""

import pytest

from fakes import pw_input, upf
from visual_workflows import cli
from visual_workflows.core.profile import ClusterProfile, Partition, Program
from visual_workflows.core.project import Project
from visual_workflows.core.ssh import ConnectionSettings
from visual_workflows.core.transport import LocalTransport

BSE = 'optics\nbss\nbse\nBSKmod= "SEX"\nBSSmod= "d"\nWRbsWF\n'


@pytest.fixture
def vwf(tmp_path, monkeypatch, capsys):
    profiles = tmp_path / "clusters"
    profiles.mkdir()
    ClusterProfile(
        name="Lynx", connection=ConnectionSettings(host="lynx.example.org"),
        partitions={"magma": Partition("", 96, 178.1, threads_per_core=2,
                                       min_memory_per_node_gb=119.2)},
        programs={"qe": Program([], executables={"pw": "pw.x"}),
                  "yambo": Program([], executables={"p2y": "p2y", "yambo": "yambo", "ypp": "ypp"})},
    ).save(profiles / "lynx.cluster.toml")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli, "make_transport", lambda profile: LocalTransport(home))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pw.scf.in").write_text(pw_input(control="max_seconds = 3000"))
    (tmp_path / "bse.in").write_text(BSE)

    def run(*argv):
        code = cli.main(["--profiles", str(profiles), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    run.home = home
    run("project", "new", "Si", "--cluster", "lynx")
    (tmp_path / "Si" / "pseudos").mkdir()
    (tmp_path / "Si" / "pseudos" / "Si.upf").write_text(upf())
    return run


RESOURCES = ["--partition", "magma", "--tasks-per-node", "48", "--mem", "100G", "--time", "2:00:00"]


# -- --run ------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("pw pw.scf.in -nk 4", dict(program="qe", executable="pw", input="pw.scf.in", args="-nk 4")),
    ("yambo bse.in -J diago,../04-gw/gw", dict(program="yambo", input="bse.in",
                                               jobs=["diago", "../04-gw/gw"], args="")),
    ("p2y -I ../01-scf/out/si.save -O . --serial",
     dict(program="yambo", executable="p2y", input="", args="-I ../01-scf/out/si.save -O .",
          mpi=False)),
    ("qe:projwfc projwfc.in", dict(program="qe", executable="projwfc", input="projwfc.in")),
    ("ph ph.in", dict(program="qe", executable="ph")),          # not in the profile: the default
    # Arguments kept as written: the job script's shell expands the variables
    ("yambo bse.in -J bse -O /scratch/$USER/vwf-$SLURM_JOB_ID",
     dict(input="bse.in", jobs=["bse"], args="-O /scratch/$USER/vwf-$SLURM_JOB_ID")),
    ("""p2y -I "../01 scf/out" -O '$HOME/x' --serial""",
     dict(executable="p2y", args="""-I "../01 scf/out" -O '$HOME/x'""", mpi=False)),
])
def test_parse_run(text, expected):
    profile = ClusterProfile(name="x", connection=ConnectionSettings(host="h"),
                             programs={"qe": Program(executables={"pw": "pw.x"})})
    run, local = cli.parse_run(text, profile)
    for key, value in expected.items():
        assert getattr(run, key) == value, key
    assert local is None


def test_parse_run_errors():
    with pytest.raises(cli.UserError, match="which program runs wannier90"):
        cli.parse_run("wannier90 w.in", None)
    with pytest.raises(cli.UserError, match="nothing in it"):
        cli.parse_run("", None)
    with pytest.raises(cli.UserError, match="No closing quotation"):
        cli.parse_run("pw pw.in -nk 'four", None)


# -- add-step, script, check ------------------------------------------------------------

def test_add_a_step_with_a_job_description(vwf, tmp_path):
    code, out, err = vwf("project", "add-step", "scf", "-C", "Si", *RESOURCES,
                         "--run", f"pw {tmp_path / 'pw.scf.in'} -nk 4")
    assert code == 0 and "Added step 01-scf: job.sh, pw.scf.in" in out
    assert "Error" not in err                       # a good step: no errors
    step = Project.load(tmp_path / "Si").step("scf")
    assert step.described and step.job.runs[0].args == "-nk 4" and step.job.memory == "100G"
    # The script, as Submit will write it (the project's pseudopotentials)
    code, out, _ = vwf("project", "script", "scf", "-C", "Si")
    assert "#SBATCH --cpus-per-task=2" in out and "srun pw.x -nk 4 -inp pw.scf.in" in out
    assert 'export ESPRESSO_PSEUDO="$SLURM_SUBMIT_DIR/../pseudos"' in out
    code, out, err = vwf("project", "check", "-C", "Si")
    assert code == 0 and "Checked 01-scf: 0 errors" in out and "--on-cluster" in out


def test_problems_are_shown_when_adding_and_checking(vwf, tmp_path):
    code, out, err = vwf("project", "add-step", "bse", "-C", "Si", *RESOURCES,
                         "--link", "SAVE=../03-p2y/SAVE", "--input", "bse.in",
                         "--run", "yambo bse.in -J diago -o b")
    assert code == 0 and "Added step 01-bse" in out
    assert "Error: 01-bse: Run 1 (bse.in): -o: a runlevel option" in err
    code, out, err = vwf("project", "check", "bse", "-C", "Si")
    assert code == 1 and "1 error" in out and "-o: a runlevel option" in err
    # A hand edit is reported
    (tmp_path / "Si" / "01-bse" / "job.sh").write_text("#!/bin/bash\n")
    _, _, err = vwf("project", "check", "-C", "Si")
    assert "edited by hand" in err
    _, out, err = vwf("project", "script", "bse", "-C", "Si")
    assert "#SBATCH --job-name=01-bse" in out and "edited by hand" in err


def test_add_step_errors(vwf):
    code, _, err = vwf("project", "add-step", "x", "-C", "Si", "--script", "a.sh", "--run", "pw a.in")
    assert code == 1 and "not both" in err
    code, _, err = vwf("project", "add-step", "x", "-C", "Si", "--partition", "magma")
    assert code == 1 and "at least one --run" in err
    code, _, err = vwf("project", "add-step", "x", "-C", "Si", "--run", "pw a.in", "--link", "SAVE")
    assert code == 1 and "NAME=TARGET" in err


def test_steps_with_their_own_script(vwf, tmp_path):
    (tmp_path / "job.sh").write_text("#!/bin/bash\necho mine\n")
    vwf("project", "add-step", "own", "-C", "Si", "--script", str(tmp_path / "job.sh"))
    code, out, _ = vwf("project", "script", "own", "-C", "Si")
    assert out == "#!/bin/bash\necho mine\n"
    code, out, _ = vwf("project", "check", "-C", "Si")
    assert code == 0 and "Not checked (scripts of their own): 01-own" in out


def test_check_on_the_cluster(vwf, tmp_path):
    # Pseudopotentials from the profile's library, read on the cluster
    (tmp_path / "Si" / "pseudos" / "Si.upf").unlink()
    (tmp_path / "Si" / "pseudos").rmdir()
    profile = ClusterProfile.load(tmp_path / "clusters" / "lynx.cluster.toml")
    profile.pseudos = "$HOME/pp"
    profile.save()
    vwf("project", "add-step", "scf", "-C", "Si", *RESOURCES, "--run", f"pw {tmp_path / 'pw.scf.in'}")
    code, out, _ = vwf("project", "check", "-C", "Si")
    assert code == 0                                 # offline: the library is not read
    code, out, err = vwf("project", "check", "-C", "Si", "--on-cluster")
    assert code == 1 and "Si.upf not found at $HOME/pp/Si.upf" in err and "(with the cluster)" in out
    (vwf.home / "pp").mkdir()
    (vwf.home / "pp" / "Si.upf").write_text(upf())
    code, out, _ = vwf("project", "check", "-C", "Si", "--on-cluster")
    assert code == 0 and "0 errors" in out


def test_a_step_with_its_own_qos(vwf, tmp_path):
    code, out, err = vwf("project", "add-step", "quick", "-C", "Si", *RESOURCES, "--qos", "debug",
                         "--account", "lv99", "--run", f"pw {tmp_path / 'pw.scf.in'}")
    assert code == 0
    step = Project.load(tmp_path / "Si").step("quick")
    assert (step.job.qos, step.job.account) == ("debug", "lv99")
    _, out, _ = vwf("project", "script", "quick", "-C", "Si")
    assert "#SBATCH --qos=debug" in out and "#SBATCH --account=lv99" in out


def test_log_of_a_run(vwf, tmp_path):
    import asyncio
    from datetime import datetime, timezone
    from pathlib import Path
    from visual_workflows.core.jobspec import JobSpec, Run
    from visual_workflows.core.project import JobRecord
    from visual_workflows.core.runner import Runner
    from visual_workflows.core.scheduler import JobState
    p = Project.load(tmp_path / "Si")
    step = p.add_job_step("scf", JobSpec(runs=[Run("qe", "pw", "scf.in")]))
    step.jobs.append(JobRecord("7", datetime.now(timezone.utc), JobState.COMPLETED, output="slurm-7.out",
                               final=True))
    step.save()
    profile = ClusterProfile(name="Lynx", connection=ConnectionSettings(host="x"))

    async def where():
        async with LocalTransport(vwf.home) as t:
            return await Runner(p, profile, t, None).remote_dir(step)
    remote = Path(asyncio.run(where()))
    remote.mkdir(parents=True)
    (remote / "slurm-7.out").write_text("job log\n")
    (remote / "scf.out").write_text("     number of k points=    16\n")
    code, out, _ = vwf("project", "log", "scf", "-C", "Si")
    assert code == 0 and out == "job log\n"
    code, out, _ = vwf("project", "log", "scf", "--run", "1", "-C", "Si")
    assert code == 0 and "number of k points=    16" in out
    code, _, err = vwf("project", "log", "scf", "--run", "2", "-C", "Si")
    assert code == 1 and "has no run 2" in err
