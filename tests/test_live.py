"""Live, read-only checks of real clusters, with your own profiles and logins:

    pytest -m live --cluster Tirant [--cluster Lynx] [--record-to tests/data] -s

(-s lets a password or one-time code be typed). Recordings replay what a
cluster once said; these ask it now, so they also catch a cluster that
changed (a new Slurm, another module system). Nothing is submitted or
written on the cluster; --record-to writes recordings here, to check for
personal names before committing (docs/development.md)."""

import re

import pytest

from visual_workflows import cli
from visual_workflows.core.discovery import discover
from visual_workflows.core.modules import Modules, for_program
from visual_workflows.core.paths import cluster_profiles_dir
from visual_workflows.core.profile import ProfileStore
from visual_workflows.core.report import cluster_report
from visual_workflows.core.scheduler import scheduler_for

pytestmark = pytest.mark.live


def pytest_generate_tests(metafunc):
    if "cluster_name" in metafunc.fixturenames:
        metafunc.parametrize("cluster_name", metafunc.config.getoption("--cluster") or ["none"])


@pytest.fixture
async def connected(cluster_name):
    profiles, _ = ProfileStore(cluster_profiles_dir()).load_all()
    profile = next((p for p in profiles if p.name.lower() == cluster_name.lower()), None)
    if profile is None:
        pytest.fail(f"No cluster profile named {cluster_name} in {cluster_profiles_dir()}")
    transport = cli.make_transport(profile)
    await transport.connect()
    try:
        yield profile, transport, scheduler_for(profile.scheduler, transport)
    finally:
        await transport.close()


async def test_report(connected):
    profile, transport, scheduler = connected
    report = await cluster_report(transport, scheduler, profile)
    assert re.match(r"\d+\.\d+", report.slurm_version), report.slurm_version
    assert report.groups, "no groups"
    assert report.modules is not None and report.modules.kind, "no module system found"
    print("\n" + "\n".join(report.lines(profile.python)))


async def test_discover(connected):
    profile, transport, scheduler = connected
    found = await discover(scheduler)
    assert found.partitions, "no partition you may use"
    for name, p in found.partitions.items():
        assert p.cores_per_node > 0 and p.memory_per_node_gb > 0, name
        assert p.threads_per_core > 0, f"{name}: the node layout (%z) was not read"


async def test_modules(connected):
    profile, transport, scheduler = connected
    modules = Modules(transport, profile.module_use)
    available = await modules.available()
    assert available, "no modules listed"
    assert for_program(available, "qe") or for_program(available, "yambo"), \
        "neither QE nor Yambo modules found"
    for name, program in profile.programs.items():
        if program.modules:
            result = await modules.check(program.modules, list(program.executables.values()))
            assert result.ok, f"{name}: " + "; ".join(result.problems())


async def test_jobs(connected):
    profile, transport, scheduler = connected
    assert isinstance(await scheduler.jobs(), dict)


async def test_record(connected, request):
    folder = request.config.getoption("--record-to")
    if not folder:
        pytest.skip("give --record-to DIR to record")
    from pathlib import Path
    from visual_workflows.core.recording import record_cluster, write_recording
    profile, transport, _ = connected
    home = await transport.home()
    recording = await record_cluster(transport, lambda t: scheduler_for(profile.scheduler, t),
                                     profile)
    user = profile.connection.username or Path(home).name
    write_recording(Path(folder) / profile.name.lower(), recording,
                    {home: "/home/alice", user: "alice"})
