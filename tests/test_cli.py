import pytest

from fakes import FakeTransport, recorded
from visual_workflows import cli


def lynx(name):
    return recorded("lynx", name)


LYNX_ANSWERS = {
    "sinfo --version": (0, lynx("sinfo-version.txt")),
    "scontrol show partition": (0, lynx("scontrol-partitions.txt")),
    "sinfo -h -N": (0, lynx("sinfo-nodes.txt")),
    "id -Gn": (0, lynx("id-groups.txt")),
    "squeue --json": (0, lynx("squeue-ended.json")),
    "sacct": (1, "", lynx("sacct-disabled.txt")),
    "python3 --version": (0, "Python 3.14.7\n"),
    "module --version": (0, lynx("module-version.txt")),
    "module -j avail": (0, lynx("module-avail.json")),
    'for m in': (0, lynx("module-show.txt")),
}


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
    assert "magma" in out and "Python 3.14.7" in out and "Modules Release 5.3.1" in out


def test_partitions(vwf):
    code, out, _ = vwf("partitions", "Lynx")
    assert code == 0
    lines = {line.split()[0]: line for line in out.splitlines()[1:5]}
    assert set(lines) == {"total*", "arqus", "magma", "novas"}
    assert "5000 MB/node" in lines["total*"] and "unlimited" in lines["total*"]
    assert "48/96" in lines["arqus"]           # novas04 half busy in the recording


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
    vwf("run", "Lynx", "module avail 2>&1 | grep qe")
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
