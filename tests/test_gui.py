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

    # Another program (the command line) records a job meanwhile: the window
    # follows, and saving an edit does not undo it
    import os
    from datetime import datetime, timezone
    from visual_workflows.core.project import JobRecord, Project as ProjectFiles
    e = StepEditor(pv, pv.project, profile, pv.project.step("scf"))
    cli_side = ProjectFiles.load(pv.project.path)
    cli_side.step("scf").jobs.append(JobRecord("4242", datetime.now(timezone.utc)))
    cli_side.step("scf").save()
    step_file = cli_side.step("scf").folder / "step.toml"
    t = step_file.stat().st_mtime_ns + 10**9
    os.utime(step_file, ns=(t, t))
    e.tasks.setValue(16)
    e._accept()
    async def yes(*args):
        return True
    monkeypatch.setattr(project_view, "confirm", yes)
    pv.table.selectRow(0)
    run(loop, pv.edit_step(e))
    again = ProjectFiles.load(pv.project.path).step("scf")
    assert again.job.tasks_per_node == 16 and [j.id for j in again.jobs] == ["4242"]
    assert pv.table.item(0, 1).text() == "4242"           # shown

    # Check, without the cluster
    errors, warnings = run(loop, pv.check())
    assert errors == {} and "Checked 1 step(s) (connect to also test" in pv.status.text()


def test_step_editor_suggests_the_qos(window, tmp_path):
    """On a partition that does not take the profile's QOS, its only QOS is chosen."""
    from visual_workflows.core.profile import ClusterProfile, Partition, Program
    from visual_workflows.core.project import Project
    from visual_workflows.gui.step_editor import StepEditor
    profile = ClusterProfile(
        name="Tirant", connection=ConnectionSettings(host="h"), qos="class_t",
        partitions={"main": Partition("3-00:00:00", 48, 94.0, qos={"class_t": "3-00:00:00"}),
                    "interactive": Partition("01:00:00", 48, 94.0,
                                             qos={"interactive": "01:00:00"})},
        programs={"qe": Program([], executables={"pw": "pw.x"})})
    e = StepEditor(window, Project.create(tmp_path / "P", cluster="Tirant"), profile)
    assert e.partition.currentText() == "main" and e.qos.currentText() == ""
    e.partition.setCurrentText("interactive")
    assert e.qos.currentText() == "interactive"
    e.update_preview()
    assert "#SBATCH --qos=interactive" in e.preview.toPlainText()
    assert not any("QOS" in e.problems.item(i).text() for i in range(e.problems.count()))
    e.partition.setCurrentText("main")               # back: the profile's fits again
    assert e.qos.currentText() == ""
    # No input yet: one error for it, not a second about the output
    texts = [e.problems.item(i).text() for i in range(e.problems.count())]
    assert any("no input file" in t for t in texts) and not any("overwrite" in t for t in texts)


def test_pseudo_picker(loop, qtbot, tmp_path):
    """Choose a table, see the hints and the suggestion, get the files into pseudos/."""
    from PySide6.QtWidgets import QDialogButtonBox
    from test_pseudodojo import FakeWeb
    from visual_workflows.core.pseudodojo import Library, read_sources
    from visual_workflows.gui.pseudo_picker import PseudoPicker
    web = FakeWeb()
    folder = tmp_path / "project" / "pseudos"
    d = PseudoPicker(None, ["Mo", "Si", "Mo"], folder, Library(tmp_path / "lib", web))
    qtbot.addWidget(d)
    get = d.buttons.button(QDialogButtonBox.Ok)
    run(loop, d._loading)
    assert d.table.rowCount() == 2 and d.table.item(0, 2).text() == "72"
    assert d.table.item(0, 5).text() == "to download"
    assert "ecutwfc = 80 Ry, ecutrho = 320 Ry" in d.suggestion.text() and get.isEnabled()
    d.level.setCurrentIndex(2)
    assert "ecutwfc = 92 Ry" in d.suggestion.text()
    d.accuracy.setCurrentIndex(1)                       # stringent: its own table file
    run(loop, d._loading)
    assert d.chosen_table().accuracy == "stringent"
    get.click()
    run(loop, d._getting)
    assert d.result() == QDialog.Accepted
    assert d.files == {"Mo": "Mo.nc-sr-04_pbe_stringent.upf", "Si": "Si.nc-sr-04_pbe_stringent.upf"}
    assert set(read_sources(folder)) == set(d.files.values()) and d.cutoffs.ecutwfc == 92.0


def test_pseudo_picker_missing_element(loop, qtbot, tmp_path):
    from PySide6.QtWidgets import QDialogButtonBox
    from test_pseudodojo import FakeWeb
    from visual_workflows.core.pseudodojo import Library
    from visual_workflows.gui.pseudo_picker import PseudoPicker
    d = PseudoPicker(None, ["Si", "Og"], tmp_path / "pseudos", Library(tmp_path / "lib", FakeWeb()))
    qtbot.addWidget(d)
    run(loop, d._loading)
    assert "Og: not in" in d.status.text()
    assert not d.buttons.button(QDialogButtonBox.Ok).isEnabled()


def _editor_profile():
    from visual_workflows.core.profile import ClusterProfile, Partition, Program
    return ClusterProfile(name="Tirant", connection=ConnectionSettings(host="h"),
                          partitions={"main": Partition("3-00:00:00", 48, 94.0)},
                          programs={"qe": Program([], executables={"pw": "pw.x"})})


def test_pw_editor_new_input_from_a_structure(loop, qtbot, tmp_path):
    """A new input: a structure imported (its own ibrav), k-points from a spacing,
    pseudopotentials and the cutoffs they suggest; the input and checks follow."""
    import ase.io
    from ase.build import mx2
    from visual_workflows.core.pseudodojo import Cutoffs
    from visual_workflows.gui.pw_editor import PwEditor
    from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input
    project = tmp_path / "proj"
    (project / "pseudos").mkdir(parents=True)
    from fakes import upf
    (project / "pseudos" / "Mo.upf").write_text(upf("Mo", 14.0))
    (project / "pseudos" / "S.upf").write_text(upf("S", 6.0))
    ase.io.write(tmp_path / "MoS2.cif", mx2("MoS2", "2H", a=3.18, vacuum=10))
    e = PwEditor(None, None, project / "01-scf")
    qtbot.addWidget(e)
    assert "No structure yet" in e.structure_info.text()
    e.import_structure(tmp_path / "MoS2.cif")
    e.refresh()
    pw = read_pw_input(e.text())
    assert pw.get("system", "ibrav") == 4 and "CELL_PARAMETERS" not in pw.cards
    assert "P-6m2 (187)" in e.structure_info.text() and "pw.x finds 12 of its 12" in e.structure_info.text()
    # from the spacing: a CIF does not say the layer is not periodic along c, its vacuum does
    assert pw.k_points().grid[2] == 1 and pw.k_points().grid[0] > 1
    assert e.species.rowCount() == 2 and "pw.x will compute" in e.k_info.text()
    e.apply_pseudos({"Mo": "Mo.upf", "S": "S.upf"}, Cutoffs(80.0, 320.0, []))
    e.refresh()
    pw = read_pw_input(e.text())
    assert [line.split()[2] for line in pw.cards["ATOMIC_SPECIES"].lines] == ["Mo.upf", "S.upf"]
    assert pw.get("system", "ecutwfc") == 80.0 and pw.get("system", "ecutrho") == 320.0
    e.fields[("system", "nbnd")].setValue(40)
    e.fields[("control", "calculation")].setCurrentText("nscf")
    e.fields[("system", "force_symmorphic")].setChecked(True)
    e.refresh()
    pw = read_pw_input(e.text())
    assert (pw.get("system", "nbnd"), pw.get("control", "calculation"),
            pw.get("system", "force_symmorphic")) == (40, "nscf", True)
    errors = [e.problems.item(i).text() for i in range(e.problems.count())
              if e.problems.item(i).text().startswith("Error")]
    assert errors == []
    # ibrav = 0 instead
    e.ibrav.setCurrentIndex(1)
    e.refresh()
    assert read_pw_input(e.text()).get("system", "ibrav") == 0 and "CELL_PARAMETERS" in e.text()


def test_pw_editor_existing_input(qtbot, tmp_path):
    from fakes import pw_input
    from visual_workflows.gui.pw_editor import PwEditor
    from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input
    text = pw_input(system="noinv = .true.") + "! a comment pw.x ignores\n"
    e = PwEditor(None, text, tmp_path / "proj" / "01-scf")
    qtbot.addWidget(e)
    assert e.fields[("control", "calculation")].currentText() == "scf"
    assert e.fields[("system", "ecutwfc")].value() == 30.0 and e.grid[0].value() == 4
    assert "Si2: space group Fd-3m (227)" in e.structure_info.text()
    assert "24 with fractional translations" in e.structure_info.text()
    assert "pw.x will compute 8 k-points" in e.k_info.text()   # noinv: the crystal has inversion anyway
    assert read_pw_input(e.text()).get("system", "noinv") is True     # kept, not in the form
    e.k_mode.setCurrentIndex(2)                                        # a band path
    e.path.setText("GXL")
    e._kpoints_changed()
    e.refresh()
    assert read_pw_input(e.text()).cards["K_POINTS"].option == "crystal_b"
    e.fields[("system", "ecutwfc")].setValue(e.fields[("system", "ecutwfc")].minimum())   # unset
    assert read_pw_input(e.text()).get("system", "ecutwfc") is None


def test_step_editor_edits_a_pw_input(loop, qtbot, tmp_path):
    """Edit pw.x input…: the text is kept aside and copied into the step on save."""
    from fakes import pw_input
    from visual_workflows.core.project import Project
    from visual_workflows.gui.pw_editor import PwEditor
    from visual_workflows.gui.step_editor import StepEditor
    from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input
    project = Project.create(tmp_path / "P", cluster="Tirant")
    e = StepEditor(None, project, _editor_profile())
    qtbot.addWidget(e)
    e.name.setText("scf")
    editor = PwEditor(e, pw_input(), e.folder())
    editor.fields[("system", "ecutwfc")].setValue(45.0)
    editor.accept()
    run(loop, e._edit_pw(0, "scf.in", editor))
    assert e.runs.cellWidget(0, 1).currentText() == "scf.in"
    ecut = lambda text: read_pw_input(text).get("system", "ecutwfc")   # noqa: E731
    assert ecut(e.input_text("scf.in")) == 45.0
    step = project.add_job_step("scf", e.spec(), e.new_inputs())
    assert ecut((step.folder / "scf.in").read_text()) == 45.0
    # editing it again: the step's file, then a new text replacing it on save
    e2 = StepEditor(None, project, _editor_profile(), step)
    qtbot.addWidget(e2)
    editor = PwEditor(e2, e2.input_text("scf.in"), e2.folder())
    assert editor.fields[("system", "ecutwfc")].value() == 45.0
    editor.fields[("system", "ecutwfc")].setValue(60.0)
    editor.accept()
    run(loop, e2._edit_pw(0, "scf.in", editor))
    project.update_job_step(step, e2.spec(), e2.after_steps(), e2.new_inputs())
    assert ecut((step.folder / "scf.in").read_text()) == 60.0


def test_new_qe_chain(window, loop, qtbot, tmp_path, monkeypatch):
    """New QE chain…: the scf input made in the editor, the defaults it gives
    (grid, nbnd from the occupied bands), and the steps made."""
    from fakes import pw_input, upf
    from visual_workflows.core.chain import check_chain
    from visual_workflows.core.profile import Partition, Program
    from visual_workflows.gui import project_view
    from visual_workflows.gui.project_view import NewProjectDialog
    from visual_workflows.gui.pw_editor import PwEditor
    from visual_workflows.gui.qe_chain_dialog import QeChainDialog
    from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input
    profile = add_lynx(window)
    profile.partitions = {"magma": Partition("", 96, 178.1, threads_per_core=2)}
    profile.programs = {"qe": Program([], executables={"pw": "pw.x"})}
    pv = window.project
    d = NewProjectDialog(pv, pv.profiles(), "Lynx", str(tmp_path))
    d.name.setText("Si")
    run(loop, pv.new_project(d))
    (pv.project.path / "pseudos").mkdir()
    (pv.project.path / "pseudos" / "Si.upf").write_text(upf())

    c = QeChainDialog(pv, pv.project, profile)
    qtbot.addWidget(c)
    assert c.tasks.value() == 48                       # physical cores, not hardware threads
    c._accept()
    assert "Make the scf input first" in c.summary.text() and c.result() != QDialog.Accepted
    editor = PwEditor(c, pw_input(), c.scf_folder())
    editor.accept()
    run(loop, c.edit_scf_input(editor))
    assert [b.value() for b in c.grid] == [4, 4, 4]   # the scf's grid
    assert c.nscf_nbnd.value() == 24 and c.bands_nbnd.value() == 12   # 4 occupied bands
    assert "Si2, Fd-3m; prefix 'si'; 4 occupied bands" in c.scf_info.text()
    assert "01-scf → 02-nscf → 03-p2y → 04-bands" in c.summary.text()      # all, by default
    assert "computes 8 k-points, with force_symmorphic" in c.summary.text()
    assert "it runs after p2y" in c.summary.text()
    c.bands.setChecked(False)
    assert "04-bands" not in c.summary.text()
    c.bands.setChecked(True)
    c.time.setText("02:00:00")
    c._accept()
    assert c.result() == QDialog.Accepted

    async def answered(dialog):
        return QDialog.Accepted
    monkeypatch.setattr(project_view, "ask", answered)
    run(loop, pv.new_qe_chain(c))
    p = pv.project
    assert [s.id for s in p.steps] == ["01-scf", "02-nscf", "03-p2y", "04-bands"]
    nscf = read_pw_input((p.steps[1].folder / "nscf.in").read_text())
    assert nscf.get("system", "nbnd") == 24 and nscf.get("system", "force_symmorphic") is True
    assert p.steps[0].job.time == "02:00:00" and (p.steps[0].folder / "job.sh").exists()
    assert check_chain(p) == {} and "Added steps 01-scf, 02-nscf, 03-p2y, 04-bands." in pv.status.text()


def test_log_of_a_run(window, loop, local_cluster, tmp_path):
    """The log panel: the job's log, or a run's own output (pw.x's file, p2y's l_p2y)."""
    from datetime import datetime, timezone
    from pathlib import Path
    from visual_workflows.core.jobspec import JobSpec, Run
    from visual_workflows.core.project import JobRecord
    from visual_workflows.gui.project_view import NewProjectDialog
    home, _ = local_cluster
    pv = window.project
    d = NewProjectDialog(pv, pv.profiles(), "Lynx", str(tmp_path))
    d.name.setText("Si")
    run(loop, pv.new_project(d))
    step = pv.project.add_job_step("chain", JobSpec(runs=[
        Run("qe", "pw", "scf.in"), Run("yambo", "p2y", args="-I out/si.save -O .", mpi=False)]))
    step.jobs.append(JobRecord("7", datetime.now(timezone.utc), JobState.COMPLETED, output="slurm-7.out"))
    step.save()
    remote = Path(run(loop, _remote_dir(pv, step)))
    remote.mkdir(parents=True)
    (remote / "slurm-7.out").write_text("job log\n")
    (remote / "scf.out").write_text("     number of k points=    16\n")
    (remote / "l_p2y").write_text(" <--->  == P2Y completed ==\n")
    pv._update()
    run(loop, pv._open_log(step.id))
    assert pv.log.toPlainText() == "job log\n"
    assert [pv.log_source.itemText(i) for i in range(pv.log_source.count())] == [
        "Job log", "Run 1: pw scf.in", "Run 2: p2y -I out/si.save -O ."]
    run(loop, pv._open_log(step.id, 1))
    assert "number of k points=    16" in pv.log.toPlainText()
    run(loop, pv._open_log(step.id, 2))
    assert "P2Y completed" in pv.log.toPlainText()


async def _remote_dir(pv, step):
    return await (await pv.runner()).remote_dir(step)
