"""The same questions on every recorded cluster (tests/data/<name>/cluster.toml):
the connection report, Discover, the modules, the jobs. A new recording is
tested by these at once; a command the program sends without a recording
fails them (the fake cluster is strict)."""

import pytest

from fakes import FakeTransport, recorded_cluster, recorded_clusters
from visual_workflows.core.discovery import discover
from visual_workflows.core.modules import Modules, for_program
from visual_workflows.core.profile import ClusterProfile
from visual_workflows.core.recording import record_cluster, write_recording
from visual_workflows.core.report import cluster_report
from visual_workflows.core.scheduler import Slurm
from visual_workflows.core.ssh import ConnectionSettings


@pytest.fixture(params=recorded_clusters())
def cluster(request):
    answers, expect = recorded_cluster(request.param)
    return request.param, answers, expect


def profile(name="cluster"):
    return ClusterProfile(name=name, connection=ConnectionSettings(host="h"))


def test_there_are_recorded_clusters():
    assert {"lynx", "tirant"} <= set(recorded_clusters())


async def test_report(cluster):
    name, answers, expect = cluster
    t = FakeTransport(answers)
    report = await cluster_report(t, Slurm(t), profile(name))
    assert report.slurm_version == expect["slurm"]
    assert report.json_output == expect["json_output"]
    assert report.accounting == expect["accounting"]
    assert report.modules_text == expect["modules"]
    assert report.python == expect["python"]
    assert report.groups, "no groups read"


async def test_discover(cluster):
    name, answers, expect = cluster
    found = await discover(Slurm(FakeTransport(answers)))
    assert list(found.partitions) == expect["partitions"]
    for pname, p in found.partitions.items():
        assert p.cores_per_node > 0 and p.memory_per_node_gb > 0, pname
    assert max(p.threads_per_core for p in found.partitions.values()) == expect["threads_per_core"]
    if expect["accounting"]:
        assert found.accounts and found.default_qos


async def test_modules(cluster):
    name, answers, expect = cluster
    modules = Modules(FakeTransport(answers))
    available = await modules.available()
    assert available, "no modules listed"
    qe = for_program(available, "qe")
    assert bool(qe) == expect["qe_modules"]
    if qe:
        details = await modules.details([qe[0].full])
        assert details, "module show gave nothing"


async def test_jobs(cluster):
    name, answers, expect = cluster
    jobs = await Slurm(FakeTransport(answers)).jobs()
    assert isinstance(jobs, dict)


async def test_recording_round_trip(tmp_path):
    """Recording the recorded Lynx gives a manifest that replays the same."""
    answers, expect = recorded_cluster("lynx")
    recording = await record_cluster(FakeTransport(answers), Slurm, profile("lynx"))
    commands = [r.command for r in recording.records]
    assert any(c.startswith("sinfo --version") for c in commands)
    assert any(c.startswith("for m in ") for c in commands)        # module show, asked
    assert recording.expect == {k: expect[k] for k in recording.expect}
    write_recording(tmp_path / "lynx", recording, {"/home/alice": "/home/alice", "alice": "alice"})
    replayed, again = recorded_cluster("lynx", tmp_path)
    assert again == recording.expect
    assert "for m in" in replayed                     # by its stable start: any modules
    t = FakeTransport(replayed)
    report = await cluster_report(t, Slurm(t), profile("lynx"))
    assert report.slurm_version == expect["slurm"] and report.modules_text == expect["modules"]


def test_personal_strings_are_replaced(tmp_path):
    from visual_workflows.core.recording import Recording
    from visual_workflows.core.transport import CommandResult
    recording = Recording([CommandResult("id -Gn", 0, "jmc staff\n", ""),
                           CommandResult("echo $HOME", 0, "/home/jmc\n", ""),
                           CommandResult("spack", 127, "", "spack: command not found")],
                          {"python": "", "home": "/home/jmc"})
    manifest = write_recording(tmp_path, recording, {"/home/jmc": "/home/alice", "jmc": "alice"})
    text = "".join(p.read_text() for p in tmp_path.iterdir())
    assert "jmc" not in text and "alice staff" in text and "/home/alice" in text
    assert 'missing = true' in manifest.read_text()
