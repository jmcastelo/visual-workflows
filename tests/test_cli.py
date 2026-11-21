import pytest

from fakes import FakeTransport, recorded, recorded_cluster
from visual_workflows import cli


def lynx(name):
    return recorded("lynx", name)


# The recorded Lynx (tests/data/lynx/cluster.toml)
LYNX_ANSWERS = recorded_cluster("lynx")[0]


class Cluster(FakeTransport):
    uploads: list = []

    async def put(self, local, remote):
        self.uploads.append((str(local), remote))


@pytest.fixture
def vwf(tmp_path, monkeypatch, capsys):
    """Runs `vwf ...` against the recorded Lynx; returns (exit code, out, err)."""
    transports = []

    def make(profile):
        t = Cluster(dict(LYNX_ANSWERS))
        t.uploads = []
        transports.append(t)
        return t

    monkeypatch.setattr(cli, "make_transport", make)

    def run(*argv):
        code = cli.main(["--profiles", str(tmp_path / "clusters"), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    run.transports = transports
    run("clusters", "add", "Lynx", "--host", "lynx.example.org", "--user", "alice")
    return run


def test_clusters(vwf, tmp_path):
    code, out, _ = vwf("clusters", "list")
    assert code == 0 and "Lynx" in out and "lynx.example.org" in out
    code, out, _ = vwf("clusters", "show", "lynx")          # names are case-insensitive
    assert 'format = "cluster-profile/1"' in out and 'username = "alice"' in out
    code, _, err = vwf("clusters", "add", "lynx", "--host", "x")
    assert code == 1 and "exists already" in err
    shared = tmp_path / "shared.cluster.toml"
    assert vwf("clusters", "export", "Lynx", str(shared))[0] == 0
    assert 'username = ""' in shared.read_text()
    assert vwf("clusters", "remove", "Lynx")[0] == 0
    code, out, _ = vwf("clusters", "import", str(shared))
    assert code == 0 and "no username" in out


def test_unknown_cluster(vwf):
    code, _, err = vwf("jobs", "tirant")
    assert code == 1 and "No cluster named 'tirant'" in err and "Lynx" in err


def test_invalid_cluster_is_refused(vwf):
    code, _, err = vwf("clusters", "add", "X", "--host", "h", "--port", "70000")
    assert code == 1 and "70000" in err


def test_test_command(vwf):
    code, out, _ = vwf("test", "Lynx")
    assert code == 0
    assert "Slurm 26.05.4 (JSON output)" in out
    assert "Accounting: no (Slurm accounting storage is disabled)" in out
    assert "magma" in out and "Python 3.14.7" in out and "Modules: environment-modules 5.3.1" in out


def test_test_command_with_lmod(vwf, monkeypatch):
    """Lmod (Tirant) starts its version with an empty line: still found."""
    answers = dict(LYNX_ANSWERS)
    answers["module --version"] = (0, recorded("tirant", "module-version.txt"))
    monkeypatch.setattr(cli, "make_transport", lambda profile: Cluster(answers))
    code, out, _ = vwf("test", "Lynx")
    assert code == 0 and "Modules: lmod 8.7.55" in out


def test_partitions(vwf):
    code, out, _ = vwf("partitions", "Lynx")
    assert code == 0
    lines = {line.split()[0]: line for line in out.splitlines()[1:5]}
    assert set(lines) == {"total*", "arqus", "magma", "novas"}
    assert "5000 MB/node" in lines["total*"] and "unlimited" in lines["total*"]
    assert "48/96" in lines["arqus"]           # novas04 half busy in the recording
    assert "CORES/NODE" in out.splitlines()[0] and "node's" in lines["magma"]   # no memory limit


def test_partitions_hides_those_not_allowed(vwf):
    original = dict(LYNX_ANSWERS)
    LYNX_ANSWERS["id -Gn"] = (0, "alice magma\n")
    try:
        _, out, _ = vwf("partitions", "Lynx")
        assert "arqus" not in out and "magma" in out
        _, out, _ = vwf("partitions", "Lynx", "--all")
        assert "arqus" in out and " no" in out.splitlines()[2]
    finally:
        LYNX_ANSWERS.clear()
        LYNX_ANSWERS.update(original)


def test_jobs(vwf):
    code, out, _ = vwf("jobs", "Lynx")
    assert code == 0
    rows = {line.split()[1]: line for line in out.splitlines()[1:]}
    assert "failed" in rows["vw-failed"] and "exit 3" in rows["vw-failed"]
    assert "timeout" in rows["vw-timeout"] and "signal 9" in rows["vw-timeout"]
    assert "JobHeldUser" in rows["vw-held"]


def test_submit_uploads_then_submits(vwf, tmp_path):
    script = tmp_path / "relax.sh"
    script.write_text("#!/bin/bash\n")
    LYNX_ANSWERS["cd calcs/WSe2 && sbatch"] = (0, "393600\n")
    try:
        code, out, _ = vwf("submit", "Lynx", str(script), "--to", "calcs/WSe2",
                           "--after", "393599")
    finally:
        del LYNX_ANSWERS["cd calcs/WSe2 && sbatch"]
    assert code == 0 and "Submitted job 393600" in out
    t = vwf.transports[-1]
    assert t.uploads == [(str(script), "calcs/WSe2/relax.sh")]
    assert t.commands[-1].endswith("--dependency=afterok:393599 relax.sh")


def test_submit_missing_local_file(vwf, tmp_path):
    code, _, err = vwf("submit", "Lynx", str(tmp_path / "nope.sh"), "--to", "x")
    assert code == 1 and "No such file" in err


def test_cancel(vwf):
    LYNX_ANSWERS["scancel"] = (0, "")
    try:
        code, out, _ = vwf("cancel", "Lynx", "393491", "393492")
    finally:
        del LYNX_ANSWERS["scancel"]
    assert code == 0 and vwf.transports[-1].commands[-1] == "scancel 393491 393492"


def test_cancel_refuses_odd_ids(vwf):
    code, _, err = vwf("cancel", "Lynx", "1;reboot")
    assert code == 1 and "Not a Slurm job id" in err


def test_run_passes_exit_code_and_quotes_words(vwf):
    LYNX_ANSWERS["ls 'a b'"] = (2, "", "ls: cannot access 'a b'\n")
    try:
        code, _, err = vwf("run", "Lynx", "--", "ls", "a b")
    finally:
        del LYNX_ANSWERS["ls 'a b'"]
    assert code == 2 and "cannot access" in err
    # One argument is a shell line, passed as it is
    LYNX_ANSWERS["module avail 2>&1 | grep qe"] = (0, "qe/7.3.1b-intelmpi-omp\n")
    try:
        vwf("run", "Lynx", "module avail 2>&1 | grep qe")
    finally:
        del LYNX_ANSWERS["module avail 2>&1 | grep qe"]
    assert vwf.transports[-1].commands[-1] == "module avail 2>&1 | grep qe"


def test_formatting():
    assert cli.minutes(None) == "unlimited"
    assert cli.minutes(5) == "0:05" and cli.minutes(750) == "12:30"
    assert cli.minutes(3 * 1440 + 61) == "3-01:01"
    assert cli.megabytes(5000) == "5000 MB" and cli.megabytes(2048) == "2 GB"
    assert cli.megabytes(182457) == "178 GB" and cli.megabytes(512) == "512 MB"
    assert cli.megabytes(None) == "-"


def test_modules(vwf):
    code, out, _ = vwf("modules", "Lynx", "qe")
    lines = out.splitlines()
    assert code == 0 and lines[1].split()[:2] == ["quantum-espresso", "7.5"]
    assert "spack" in lines[1] and "[ra7jhfo]" not in lines[1]     # unique: no hash
    assert lines[-1].startswith("15 modules (environment-modules 5.3.1)")
    _, out, _ = vwf("modules", "Lynx", "yambo/gpl", "--describe")
    assert "yambo 5.2.4 single precission OpenMP INTELMPI" in out


def test_modules_check(vwf):
    LYNX_ANSWERS["module purge"] = (0, "@@load 0\n@@list\nyambo/gpl/5.1.1-intelmpi\n@@end\n"
                                       "@@exe|yambo|/apps/packages/yambo/bin/yambo\n"
                                       "@@missing|yambo|\tlibnetcdff.so.7 => not found\n")
    try:
        code, out, _ = vwf("modules", "Lynx", "--check", "yambo/gpl/5.1.1-intelmpi",
                           "--exe", "yambo")
    finally:
        del LYNX_ANSWERS["module purge"]
    assert code == 1
    assert "missing libraries: libnetcdff.so.7" in out and "a library module not loaded?" in out


def test_project_commands(vwf, tmp_path, monkeypatch):
    from fakes import LocalSlurm
    from visual_workflows.core.transport import LocalTransport
    home = tmp_path / "home"
    home.mkdir()
    slurm = LocalSlurm(home)
    monkeypatch.setattr(cli, "make_transport", lambda profile: LocalTransport(home))
    monkeypatch.setattr(cli, "scheduler_for", lambda kind, transport: slurm)
    (tmp_path / "scf.sh").write_text("#!/bin/bash\necho done > out.txt\n")
    (tmp_path / "pw.in").write_text("&control /\n")
    proj = str(tmp_path / "WSe2")
    code, out, _ = vwf("project", "new", proj, "--cluster", "lynx")
    assert code == 0 and "for Lynx" in out
    code, out, _ = vwf("project", "add-step", "scf", "--script", str(tmp_path / "scf.sh"),
                       "--input", str(tmp_path / "pw.in"), "-C", proj)
    assert code == 0 and "01-scf: pw.in, scf.sh" in out
    code, out, _ = vwf("project", "status", "-C", proj)
    assert "not run" in out
    code, out, _ = vwf("project", "submit", "-C", proj)
    assert code == 0 and "01-scf: job 1001" in out
    code, out, err = vwf("project", "add-step", "x", "--script", "nope.sh", "-C", proj)
    assert code == 1 and "No such job script" in err


def test_project_get_watch_and_log(vwf, tmp_path, monkeypatch):
    import asyncio as aio
    from fakes import LocalSlurm
    from visual_workflows.core.transport import LocalTransport
    home = tmp_path / "home"
    home.mkdir()
    slurm = LocalSlurm(home, forget=True, blocking=True)
    monkeypatch.setattr(cli, "make_transport", lambda profile: LocalTransport(home))
    monkeypatch.setattr(cli, "scheduler_for", lambda kind, transport: slurm)
    import visual_workflows.core.monitor as monitor
    monkeypatch.setattr(monitor, "MIN_INTERVAL", 0)
    (tmp_path / "bse.sh").write_text(
        "#!/bin/bash\nmkdir -p SAVE bse\necho db > SAVE/ns.db1\necho wf > SAVE/ns.wf\n"
        "echo e > bse/ndb.BS_diago_Q1\necho log > yambo.log\necho finished\n")
    proj = str(tmp_path / "p")
    vwf("project", "new", proj, "--cluster", "lynx")
    vwf("project", "add-step", "bse", "--script", str(tmp_path / "bse.sh"), "-C", proj)
    vwf("project", "submit", "-C", proj)
    code, out, _ = vwf("project", "watch", "--interval", "0.05", "-C", proj)
    assert code == 0 and "completed" in out and "Every step has ended." in out
    code, out, _ = vwf("project", "log", "bse", "-C", proj)
    assert code == 0 and "finished" in out
    code, out, _ = vwf("project", "get", "--dry-run", "-C", proj)
    assert "skipped, wave functions: 1 files" in out and "SAVE/ns.db1" in out
    assert not (tmp_path / "p" / "01-bse" / "outputs").exists()
    code, out, _ = vwf("project", "get", "--for-excitons", "-C", proj)
    got = tmp_path / "p" / "01-bse" / "outputs"
    assert code == 0 and sorted(str(f.relative_to(got)) for f in got.rglob("*") if f.is_file()) == \
        ["SAVE/ns.db1", "SAVE/ns.wf", "bse/ndb.BS_diago_Q1"]
    code, out, _ = vwf("project", "get", "-C", proj)
    assert "2 up to date" in out and "yambo.log" in out


def test_partitions_on_tirant_show_qos(vwf, monkeypatch):
    answers = recorded_cluster("tirant")[0]
    monkeypatch.setattr(cli, "make_transport", lambda profile: Cluster(answers))
    code, out, _ = vwf("partitions", "Lynx")
    lines = {line.split()[0]: line for line in out.splitlines() if line[:1].isalpha()}
    assert code == 0 and "YOUR QOS" in lines["PARTITION"]
    assert "3-00:00" in lines["main*"] and "class_t*" in lines["main*"]
    assert "1:00" in lines["interactive"] and "interactive*" in lines["interactive"]
    assert "Your accounts: lv00" in out
    assert " 48 " in lines["main*"] and "92000 MB" in lines["main*"]   # 2x24 cores, MaxMemPerNode


def test_clusters_discover_and_program(vwf):
    code, out, _ = vwf("clusters", "discover", "Lynx")
    assert code == 0 and "partitions you may use" in out and "magma" in out
    code, out, _ = vwf("clusters", "program", "Lynx", "qe", "--modules", "qe/7.3.1b-intelmpi-omp",
                       "--launcher-options=--mpi=pmi2")
    assert code == 0 and "qe: modules qe/7.3.1b-intelmpi-omp; srun --mpi=pmi2; pw=pw.x ph=ph.x" in out
    code, out, _ = vwf("clusters", "show", "Lynx")
    assert 'launcher_options = "--mpi=pmi2"' in out and "[partitions.magma]" in out
    assert "threads_per_core" in out
    code, _, err = vwf("clusters", "program", "Lynx", "qe", "--launcher", "aprun")
    assert code == 1 and "one of srun" in err


def test_cancel_records_the_new_state(vwf, tmp_path, monkeypatch):
    """vwf project cancel records "cancelled" at once (a window showing the
    project follows), not only at the next refresh."""
    from datetime import datetime, timezone
    from visual_workflows.core.project import JobRecord, Project
    from visual_workflows.core.scheduler import Job, JobState, Scheduler

    class Queue(Scheduler):
        state = JobState.PENDING

        async def jobs(self):
            return {"7": Job(id="7", state=Queue.state, reason="Priority")}

        async def cancel(self, ids):
            Queue.state = JobState.CANCELLED

        async def history(self, ids):
            return {}

        async def _unused(self, *args, **kwargs):
            raise AssertionError("not asked in this test")

        version = partitions = nodes = user_groups = submit = job = _unused
        accounting_available = associations = qos = _unused

    from visual_workflows.core.transport import LocalTransport
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cli, "scheduler_for", lambda kind, transport: Queue())
    monkeypatch.setattr(cli, "make_transport", lambda profile: LocalTransport(home))
    (tmp_path / "scf.sh").write_text("#!/bin/bash\n")
    p = Project.create(tmp_path / "P", cluster="Lynx")
    p.add_step("scf", tmp_path / "scf.sh")
    p.step("scf").jobs.append(JobRecord("7", datetime.now(timezone.utc)))
    p.step("scf").save()
    code, out, _ = vwf("project", "cancel", "-C", str(p.path))
    assert code == 0 and "Cancelled 7" in out
    assert Project.load(p.path).step("scf").latest.state is JobState.CANCELLED
