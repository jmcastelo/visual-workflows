"""The refresh from the cluster's yambo: the same runs asked of the build that
will run the step, against its SAVE, and compared. The cluster is a folder
here, with a `yambo` that writes what yambo 5.3 wrote on Tirant."""

import re
from pathlib import Path

import pytest

from fakes import pw_input, upf
from visual_workflows import cli
from visual_workflows.core.jobspec import JobSpec
from visual_workflows.core.kpoints import Grid
from visual_workflows.core.profile import ClusterProfile, Partition, Program
from visual_workflows.core.project import Project
from visual_workflows.core.qe_chain import QeChain, add_qe_chain
from visual_workflows.core.runner import Runner
from visual_workflows.core.ssh import ConnectionSettings
from visual_workflows.core.transport import LocalTransport
from visual_workflows.core.yambo_inputs import SaveInfo, bse_input, gw_input
from visual_workflows.core.yambo_refresh import (RefreshError, compare, complete, job_command,
                                                 options_for, refresh_step)
from visual_workflows.yambopy_ext.io.yambo_input import read_yambo_input

DATA = Path(__file__).parent / "data" / "yambo" / "tirant-5.3-inputs.txt"
TIRANT = dict(re.findall(r"=== (\w+): [^\n]*\n(.*?)(?=\n=== |\Z)", DATA.read_text(), re.S))
SI = SaveInfo(nbnd=24, occupied=4, nk_ibz=16, nk_bz=216)

YAMBO = """#!/bin/bash
# yambo: with no argument the setup (the SAVE indexed); with -F, the input
echo "yambo $*" >> "$TOOLS_LOG"
if [ $# -eq 0 ]; then touch SAVE/ndb.gops SAVE/ndb.kindx; exit 0; fi
while [ $# -gt 0 ]; do [ "$1" = "-F" ] && f="$2"; shift; done
cp "$YAMBO_TEMPLATE" "$f"
"""


@pytest.mark.parametrize("make, options", [
    (lambda: gw_input(SI), "-x -p p -g n"),
    (lambda: bse_input(SI, scissor_ev=0.5), "-d s -o b -k sex -y d"),
])
def test_options_for_our_inputs(make, options):
    assert options_for(read_yambo_input(make().text("5.3"))) == options


def test_options_for_others():
    assert options_for(read_yambo_input("optics\nchi\n")) == "-o c"
    assert options_for(read_yambo_input("optics\nbse\nbss\nBSSmod= 'h'\nBSKmod= 'hartree'\n")) == \
        "-o b -k hartree -y h"
    with pytest.raises(RefreshError, match="no runlevel"):
        options_for(read_yambo_input("NGsBlkXs= 3 Ry\n"))


def test_compare_with_what_yambo_5_3_wrote():
    ours = read_yambo_input(gw_input(SI).text("5.3"))
    r = compare(ours, TIRANT["gw_all"], "5.3")
    assert r.unknown == []
    assert r.defaults["EXXRLvcs"] == "7799 RL" and "EXXRLvcs" in r.added
    assert "GDamping" not in r.added                     # verbose: yambo's default without a word
    done = complete(ours, r)
    assert done.variable("EXXRLvcs").value == 7799 and done.get("NGsBlkXp") == 3.0   # ours kept
    ours.set("NGsBlkXq", 3, "Ry")                        # misspelt: this yambo reads no such name
    assert "NGsBlkXq" in compare(ours, TIRANT["gw_all"], "5.3").unknown


def test_job_mode_command():
    command = job_command("echo hi", "/w/P/04-gw", "interactive", "interactive", "lv00")
    assert "sbatch --wait" in command and "--partition=interactive" in command
    assert "--qos=interactive" in command and "--account=lv00" in command
    assert "--wrap='echo hi'" in command and command.endswith("exit $rc")


# -- on a cluster that is a folder ---------------------------------------------------------

def profile(refresh="login"):
    return ClusterProfile(name="Local", connection=ConnectionSettings(host="localhost"),
                          partitions={"cpu": Partition("1-00:00:00", 8, 16, threads_per_core=1)},
                          programs={"qe": Program([], executables={"pw": "pw.x"}),
                                    "yambo": Program(["yambo/5.3"], refresh=refresh,
                                                     executables={"p2y": "p2y", "yambo": "yambo"})})


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("yambo", YAMBO), ("module", "#!/bin/bash\nexit 0\n")):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    (tmp_path / "template.in").write_text(TIRANT["gw_all"])
    log = tmp_path / "tools.log"
    log.touch()
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("TOOLS_LOG", str(log))
    monkeypatch.setenv("YAMBO_TEMPLATE", str(tmp_path / "template.in"))
    home = tmp_path / "home"
    home.mkdir()
    p = Project.create(tmp_path / "Si", cluster="Local")
    (p.path / "pseudos").mkdir()
    (p.path / "pseudos" / "Si.upf").write_text(upf())
    add_qe_chain(p, QeChain(pw_input(control=""), JobSpec("cpu", 1, 4, 1, "8G", "01:00:00"),
                            nscf_grid=Grid((6, 6, 6)), nscf_nbnd=24, gw=True), yambo_version=(5, 3))
    return p, home, log


def run(coro):
    import asyncio
    return asyncio.run(coro)


def test_refresh_on_the_cluster(cluster):
    p, home, log = cluster

    async def go(prof=profile()):
        async with LocalTransport(home) as t:
            runner = Runner(p, prof, t, None)
            save = Path(await runner.remote_dir(p.step("03-p2y"))) / "SAVE"
            save.mkdir(parents=True, exist_ok=True)
            (save / "ns.db1").write_text("db")
            return await refresh_step(runner, p.step("04-gw"))
    r_run, ours, refresh = run(go())
    assert r_run.input == "gw.in" and refresh.unknown == [] and "EXXRLvcs" in refresh.added
    calls = log.read_text().splitlines()
    assert calls == ["yambo ", "yambo -F ref.in -x -p p -g n -V all"]       # setup, once
    run(go())
    assert log.read_text().splitlines()[2:] == ["yambo -F ref.in -x -p p -g n -V all"]   # indexed now


def test_refresh_refused(cluster):
    p, home, _ = cluster

    async def go(prof, step="04-gw"):
        async with LocalTransport(home) as t:
            return await refresh_step(Runner(p, prof, t, None), p.step(step))
    with pytest.raises(RefreshError, match="switched off"):
        run(go(profile("off")))
    with pytest.raises(RefreshError, match="has p2y run"):              # no SAVE there yet
        run(go(profile()))
    with pytest.raises(RefreshError, match="no yambo run"):
        run(go(profile(), "01-scf"))


def test_command_line(cluster, tmp_path, monkeypatch, capsys):
    p, home, _ = cluster
    profiles = tmp_path / "clusters"
    profiles.mkdir()
    profile().save(profiles / "local.cluster.toml")
    monkeypatch.setattr(cli, "make_transport", lambda prof: LocalTransport(home))
    save = home / "visual-workflows" / "Si" / "03-p2y" / "SAVE"
    save.mkdir(parents=True)
    (save / "ns.db1").write_text("db")
    code = cli.main(["--profiles", str(profiles), "project", "refresh-input", "gw", "--complete",
                     "-C", str(p.path)])
    out = capsys.readouterr().out
    assert code == 0 and "04-gw/gw.in against Local's yambo:" in out
    assert "EXXRLvcs = 7799 RL" in out and "Completed" in out
    done = read_yambo_input((p.step("04-gw").folder / "gw.in").read_text())
    assert done.variable("EXXRLvcs").value == 7799 and done.get("NGsBlkXp") == 3.0
