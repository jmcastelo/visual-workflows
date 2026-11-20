"""The GUI on Qt's offscreen platform, with qasync's event loop: the cluster
is the recorded Lynx (FakeTransport) or bash jobs on this PC (LocalSlurm)."""

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
import qasync  # noqa: E402
from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QDialog, QMessageBox  # noqa: E402

from fakes import FakeTransport, LocalSlurm, recorded  # noqa: E402
from visual_workflows.core.credentials import CredentialStore, MemoryKeyring  # noqa: E402
from visual_workflows.core.profile import ClusterProfile, ProfileStore  # noqa: E402
from visual_workflows.core.scheduler import JobState, Slurm  # noqa: E402
from visual_workflows.core.ssh import ConnectionSettings  # noqa: E402
from visual_workflows.core.transport import FileInfo, LocalTransport  # noqa: E402
from visual_workflows.gui import session as session_module  # noqa: E402
from visual_workflows.gui.asyncqt import GuiPrompter  # noqa: E402
from visual_workflows.gui.main_window import MainWindow  # noqa: E402


def lynx(name):
    return recorded("lynx", name)


LYNX = {
    "sinfo --version": (0, lynx("sinfo-version.txt")),
    "scontrol show partition": (0, lynx("scontrol-partitions.txt")),
    "sinfo -h -N": (0, lynx("sinfo-nodes.txt")),
    "id -Gn": (0, lynx("id-groups.txt")),
    "squeue --json": (0, lynx("squeue-ended.json")),
    "sacct": (1, "", lynx("sacct-disabled.txt")),
    "module --version": (0, lynx("module-version.txt")),
}

SPACK = "/apps/spack/share/spack/modules"


class Lynx(FakeTransport):
    """The recorded Lynx, with the Spack module folder of the real one."""

    async def listdir(self, path):
        if path == SPACK:
            return [FileInfo(f"{SPACK}/linux-arch-x86_64", "linux-arch-x86_64", 0, 0, True)]
        raise NotImplementedError

    async def home(self):
        return "/home/alice"


@pytest.fixture
def loop(qapp, tmp_path):
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "qsettings"))
    loop = qasync.QEventLoop(qapp, already_running=False)
    asyncio.set_event_loop(loop)
    yield loop
    asyncio.set_event_loop(None)
    loop.close()          # now, while Qt is still there


@pytest.fixture
def window(loop, tmp_path, qtbot):
    w = MainWindow(profiles_dir=tmp_path / "clusters")
    qtbot.addWidget(w)
    return w


def run(loop, coro):
    return loop.run_until_complete(coro)


def boxes(widget):
    return [b for b in widget.findChildren(QMessageBox) if b.isVisible()]


def add_lynx(window, host="lynx.example.org"):
    window.new_cluster()
    e = window.editor
    e.name.setText("Lynx")
    e.host.setText(host)
    e.username.setText("alice")
    assert e.save()
    return window.current_profile()


def test_new_cluster_is_saved(window, tmp_path):
    assert "no clusters yet" in window.state_label.text()
    profile = add_lynx(window)
    assert window.cluster_box.currentText() == "Lynx"
    assert [p.name for p in ProfileStore(tmp_path / "clusters").load_all()[0]] == ["Lynx"]
    assert profile.programs["qe"].executables["pw"] == "pw.x"     # defaults filled in
    assert "not connected" in window.state_label.text()


def test_invalid_profile_is_not_saved(window, tmp_path):
    add_lynx(window)
    window.editor.host.setText("")
    assert not window.editor.save()
    assert any("No host" in b.text() for b in boxes(window.editor))
    assert ProfileStore(tmp_path / "clusters").load_all()[0][0].connection.host == "lynx.example.org"


def test_test_connection_and_discover(window, loop, monkeypatch):
    monkeypatch.setattr(session_module, "make_transport", lambda profile, parent: Lynx(dict(LYNX)))
    add_lynx(window)
    e = window.editor
    run(loop, e.test())
    assert "Slurm 26.05.4" in e.report.text()
    assert "Accounting: no (Slurm accounting storage is disabled)" in e.report.text()
    assert "environment-modules 5.3.1" in e.report.text()
    assert window.connect_action.text() == "Disconnect"
    e.module_use.setPlainText(SPACK)
    run(loop, e.discover())
    names = [e.partitions.item(r, 0).text() for r in range(e.partitions.rowCount())]
    assert names == ["total", "arqus", "magma", "novas"]
    row = names.index("arqus")
    assert e.partitions.item(row, 2).text() == "96" and e.partitions.item(row, 3).text() == "371.1"
    assert e.module_use.toPlainText() == f"{SPACK}/linux-arch-x86_64"
    assert e.save()
    assert window.current_profile().partitions["magma"].memory_per_node_gb == 178.2


def test_jobs_view(window, loop, monkeypatch):
    monkeypatch.setattr(session_module, "make_transport", lambda profile, parent: Lynx(dict(LYNX)))
    add_lynx(window)
    run(loop, window.jobs.refresh())
    t = window.jobs.table
    rows = {t.item(r, 1).text(): [t.item(r, c).text() for c in range(t.columnCount())]
            for r in range(t.rowCount())}
    assert rows["vw-failed"][3] == "failed" and rows["vw-failed"][8] == "exit 3"
    assert rows["vw-held"][4] == "JobHeldUser"


@pytest.fixture
def local_cluster(window, monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    slurm = LocalSlurm(home)
    monkeypatch.setattr(session_module, "make_transport",
                        lambda profile, parent: LocalTransport(home))
    monkeypatch.setattr(session_module, "make_scheduler", lambda profile, t: slurm)
    add_lynx(window, host="localhost")
    return home, slurm


def test_files_view(window, loop, local_cluster, tmp_path):
    home, _ = local_cluster
    (home / "calc" / "SAVE").mkdir(parents=True)
    (home / "calc" / "run.log").write_text("iteration 1\n")
    (home / "calc" / "SAVE" / "ns.db1").write_bytes(b"db")
    f = window.files
    run(loop, f.go_home())
    assert f.path.text() == str(home)          # where we are, on its own line
    run(loop, f.list_dir(str(home / "calc")))
    assert f.path.text() == str(home / "calc")
    names = [f.tree.topLevelItem(i).text(0) for i in range(f.tree.topLevelItemCount())]
    assert names == ["SAVE/", "run.log"]
    f.tree.topLevelItem(1).setSelected(True)
    run(loop, f.show_file(str(home / "calc" / "run.log")))
    assert f.preview.toPlainText() == "iteration 1\n"
    assert f.file_label.text() == str(home / "calc" / "run.log")
    with open(home / "calc" / "run.log", "a") as fh:
        fh.write("iteration 2\n")
    run(loop, f._read_more())
    assert f.preview.toPlainText().endswith("iteration 2\n")
    f.tree.topLevelItem(0).setSelected(True)
    run(loop, f.download(str(tmp_path / "down")))
    assert (tmp_path / "down" / "SAVE" / "ns.db1").read_bytes() == b"db"
    assert (tmp_path / "down" / "run.log").exists()


def test_project_view(window, loop, local_cluster, tmp_path):
    home, slurm = local_cluster
    (tmp_path / "scf.sh").write_text("#!/bin/bash\nmkdir -p SAVE\necho db > SAVE/ns.db1\n"
                                     "echo wf > SAVE/ns.wf\necho scf done\n")
    pv = window.project
    pv.new_project(str(tmp_path / "WSe2"))
    assert pv.project is not None and pv.project.cluster == "Lynx"
    assert window.tabs.currentWidget() is pv
    pv.project.add_step("scf", tmp_path / "scf.sh")
    pv._update()

    # One coroutine for the whole run: jobs need the loop running, as in the app
    async def scenario():
        await pv.submit()
        await slurm.wait(pv.project.step("scf").latest.id)
        await pv.refresh()
        assert pv.table.item(0, 2).text() == "completed"
        pv.table.selectRow(0)
        await pv._open_log("scf")
        pv.get_mode.setCurrentIndex(0)            # default: no wave functions
        await pv.get_results(ask_first=False)
        # A second time: nothing new, and it says so without asking
        await pv.get_results(ask_first=True)
        assert pv.status.text().startswith("Nothing new to get: 3 files already in")
        assert not boxes(pv)

    run(loop, asyncio.wait_for(scenario(), 20))
    assert pv.project.step("scf").latest.state is JobState.COMPLETED
    assert "scf done" in pv.log.toPlainText()
    out = pv.project.step("scf").folder / "outputs"
    assert (out / "SAVE" / "ns.db1").exists() and not (out / "SAVE" / "ns.wf").exists()
    # Remembered as a recent project
    assert str(tmp_path / "WSe2") in [a.text() for a in window.recent_menu.actions()]


def test_import_export_and_remove(window, loop, tmp_path):
    add_lynx(window)
    window.export_profile(str(tmp_path / "shared.cluster.toml"))
    assert 'username = ""' in (tmp_path / "shared.cluster.toml").read_text()
    run(loop, window.remove_cluster(ask_first=False))
    assert window.cluster_box.count() == 0
    window.import_profile(str(tmp_path / "shared.cluster.toml"))
    assert window.cluster_box.currentText() == "Lynx"
    assert "Fill in your username" in window.editor.report.text()


def test_password_dialog(loop, qapp):
    prompter = GuiPrompter(None, CredentialStore(MemoryKeyring()))

    def answer():
        d = next(w for w in qapp.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible())
        d.edit.setText("secret")
        d.remember.setChecked(True)
        d.accept()

    QTimer.singleShot(50, answer)
    secret = run(loop, prompter.password("alice", "lynx", retry=False))
    assert (secret.value, secret.remember) == ("secret", True)

    def cancel():
        next(w for w in qapp.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible()).reject()

    QTimer.singleShot(50, cancel)
    assert run(loop, prompter.password("alice", "lynx", retry=True)) is None
