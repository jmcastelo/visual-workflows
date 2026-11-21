"""The GUI on Qt's offscreen platform, with qasync's event loop: the cluster
is the recorded Lynx (FakeTransport) or bash jobs on this PC (LocalSlurm)."""

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
import qasync  # noqa: E402
from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QDialog, QMessageBox  # noqa: E402

from fakes import FakeTransport, LocalSlurm, recorded, recorded_cluster  # noqa: E402
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


# The recorded Lynx (tests/data/lynx/cluster.toml)
LYNX = recorded_cluster("lynx")[0]

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
    assert e.partitions.item(row, 2).text() == "96" and e.partitions.item(row, 3).text() == "371.0"
    assert e.module_use.toPlainText() == f"{SPACK}/linux-arch-x86_64"
    assert e.save()
    assert window.current_profile().partitions["magma"].memory_per_node_gb == 178.1
    assert window.current_profile().partitions["magma"].min_memory_per_node_gb == 119.2


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
    from visual_workflows.gui.project_view import NewProjectDialog
    d = NewProjectDialog(pv, pv.profiles(), "Lynx", str(tmp_path))
    d.name.setText("WSe2")
    run(loop, pv.new_project(d))
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


def test_discover_on_tirant_fills_account_and_qos(window, loop, monkeypatch):
    answers = recorded_cluster("tirant")[0]
    monkeypatch.setattr(session_module, "make_transport", lambda profile, parent: Lynx(answers))
    add_lynx(window)
    e = window.editor
    run(loop, e.discover())
    names = [e.partitions.item(r, 0).text() for r in range(e.partitions.rowCount())]
    assert names == ["interactive", "main"]
    main = names.index("main")
    assert e.partitions.item(main, 1).text() == "3-00:00:00"       # class_t's limit
    assert e.partitions.item(main, 2).text() == "48"
    assert [e.partitions.item(main, c).text() for c in (5, 6, 7)] == ["1", "94.0", "89.8"]
    assert (e.account.text(), e.qos.text()) == ("lv00", "class_t")
    assert "lmod 8.7.55" in e.report.text() and "main: class_t" in e.report.text()
    assert e.save()                                   # what the table does not show is kept
    assert window.current_profile().partitions["main"].qos == {"class_t": "3-00:00:00"}


def test_new_project_dialog(window, tmp_path):
    from visual_workflows.gui.project_view import NewProjectDialog
    from PySide6.QtWidgets import QDialogButtonBox
    add_lynx(window)
    d = NewProjectDialog(window.project, window.project.profiles(), "Lynx", str(tmp_path))
    create = d.buttons.button(QDialogButtonBox.Ok)
    assert not create.isEnabled() and "name" in d.where.text()
    d.name.setText("WSe2 monolayer BSE")
    assert create.isEnabled()
    assert d.remote.text() == "WSe2-monolayer-BSE"
    assert d.where.text() == (f"Here: {tmp_path / 'WSe2-monolayer-BSE'}\n"
                              "On Lynx: $HOME/visual-workflows/WSe2-monolayer-BSE")
    d.remote.setText("/storage/scratch/g/u/wse2")
    d.remote.textEdited.emit("x")                       # typed by the user: kept
    (tmp_path / "Other").mkdir()
    (tmp_path / "Other" / "x").write_text("x")
    d.name.setText("Other")
    assert d.remote_dir() == "/storage/scratch/g/u/wse2"
    assert not create.isEnabled() and "exists already" in d.where.text()
    d.name.setText("Other 2")
    assert d.where.text().endswith("On Lynx: /storage/scratch/g/u/wse2")
    d.location.setText(str(tmp_path / "nowhere"))
    assert "is not a folder" in d.where.text()


def test_files_view_places(window, loop, local_cluster):
    home, _ = local_cluster
    f = window.files
    assert not f.scratch_button.isEnabled()              # no scratch in the profile
    window.editor.scratch.setText("$HOME/scratch/grp/$USER")
    assert window.editor.save()
    f.set_session(window.sessions["Lynx"])
    assert f.scratch_button.isEnabled()
    user = __import__("getpass").getuser()
    (home / "scratch" / "grp" / user).mkdir(parents=True)
    (home / "visual-workflows").mkdir()
    run(loop, f.go_profile_folder("scratch"))
    assert f.path.text() == str(home / "scratch" / "grp" / user)
    run(loop, f.go_profile_folder("work_root"))
    assert f.path.text() == str(home / "visual-workflows")


def test_step_editor(window, loop, tmp_path, monkeypatch):
    """New step… and Edit step…: the job description, with the partition's limits,
    the script and the checks shown as it is filled in."""
    from fakes import pw_input, upf
    from visual_workflows.core.profile import Partition, Program
    from visual_workflows.gui import project_view
    from visual_workflows.gui.project_view import NewProjectDialog
    from visual_workflows.gui.step_editor import StepEditor

    async def answered(dialog):              # message boxes: answered at once
        return QDialog.Accepted
    monkeypatch.setattr(project_view, "ask", answered)
    profile = add_lynx(window)
    profile.partitions = {"magma": Partition("", 96, 178.1, threads_per_core=2,
                                             min_memory_per_node_gb=119.2)}
    profile.programs = {"qe": Program([], executables={"pw": "pw.x"})}
    pv = window.project
    d = NewProjectDialog(pv, pv.profiles(), "Lynx", str(tmp_path))
    d.name.setText("Si")
    run(loop, pv.new_project(d))
    (pv.project.path / "pseudos").mkdir()
    (pv.project.path / "pseudos" / "Si.upf").write_text(upf())
    (tmp_path / "pw.scf.in").write_text(pw_input(control="max_seconds = 3000"))

    e = StepEditor(pv, pv.project, profile)
    assert e.partition.currentText() == "magma"
    assert "48 cores per node (96 CPUs: 2 threads per core); nodes of 119.2–178.1 GB" in e.limits.text()
    e.name.setText("scf")
    e.tasks.setValue(48)
    e.memory.setText("200G")
    e.time.setText("2:00:00")
    e.add_input(str(tmp_path / "pw.scf.in"))
    e.runs.cellWidget(0, 1).setCurrentText("pw.scf.in")
    e.runs.cellWidget(0, 2).setText("-nk 4")
    e.update_preview()
    problems = [e.problems.item(i).text() for i in range(e.problems.count())]
    assert any("more than any magma node has" in p for p in problems) and e.errors == 1
    assert "This job: 48 of the 48 cores" in e.limits.text()
    e.memory.setText("100G")
    e.update_preview()
    assert e.errors == 0 and "#SBATCH --hint=nomultithread" in e.preview.toPlainText()
    assert "srun pw.x -nk 4 -inp pw.scf.in" in e.preview.toPlainText()
    assert 'ESPRESSO_PSEUDO="$SLURM_SUBMIT_DIR/../pseudos"' in e.preview.toPlainText()
    e.unlimited.setChecked(True)
    e.update_preview()
    assert "#SBATCH --time=0" in e.preview.toPlainText() and not e.time.isEnabled()
    e.unlimited.setChecked(False)
    assert e.qos.lineEdit().placeholderText().startswith("none")      # the profile has none
    e.qos.setCurrentText("debug")
    e.update_preview()
    assert "#SBATCH --qos=debug" in e.preview.toPlainText()
    e.qos.setCurrentText("")
    e._accept()
    run(loop, pv.new_step(e))
    step = pv.project.step("scf")
    assert step.described and step.job.memory == "100G" and step.job.runs[0].args == "-nk 4"
    assert (step.folder / "pw.scf.in").exists() and (step.folder / "job.sh").exists()

    # Edit: only for one step with a description
    assert not pv.edit_button.isEnabled()
    pv.table.selectRow(0)
    assert pv.edit_button.isEnabled()
    e = StepEditor(pv, pv.project, profile, step)
    assert not e.name.isEnabled() and e.memory.text() == "100G" and e.tasks.value() == 48
    assert [e.inputs.item(i).text() for i in range(e.inputs.count())] == ["pw.scf.in"]
    e.tasks.setValue(24)
    e._accept()
    run(loop, pv.edit_step(e))
    assert pv.project.step("scf").job.tasks_per_node == 24
    assert "#SBATCH --ntasks-per-node=24" in (step.folder / "job.sh").read_text()

    # A hand-edited script: saving asks first (here: no)
    (step.folder / "job.sh").write_text("#!/bin/bash\necho mine\n")

    async def no(*args):
        return False
    monkeypatch.setattr(project_view, "confirm", no)
    e = StepEditor(pv, pv.project, profile, step)
    e.tasks.setValue(12)
    e._accept()
    run(loop, pv.edit_step(e))
    assert pv.project.step("scf").job.tasks_per_node == 24
    assert "echo mine" in (step.folder / "job.sh").read_text()

    # Check, without the cluster
    errors, warnings = run(loop, pv.check())
    assert errors == {} and "Checked 1 step(s) (connect to also test" in pv.status.text()
