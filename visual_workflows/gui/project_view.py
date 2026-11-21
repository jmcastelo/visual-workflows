"""A project: its steps, running them, their logs and results."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.monitor import DEFAULT_INTERVAL, LogFollower
from ..core.project import OUTPUTS_DIR, Project, folder_name
from ..core.runner import Runner, ScriptEdited, SubmitRefused
from ..core.transfer import choose, fetch, human, plan, scan
from .asyncqt import ask, confirm, launch, settings, show_error
from .jobs_view import result_text, when
from .step_editor import StepEditor

COLUMNS = ["Step", "Job", "State", "Reason", "Result", "Started", "Ended", "After"]
GET_MODES = {"Results (no wave functions, nothing over 1 GB)": "default",
             "For Visual Excitons": "excitons", "Everything": "all"}


class AddStepDialog(QDialog):
    def __init__(self, parent, project: Project):
        super().__init__(parent)
        self.setWindowTitle("Add step")
        self.resize(560, 420)
        form = QFormLayout(self)
        self.name = QLineEdit(placeholderText="e.g. scf, nscf, p2y, gw, bse")
        self.script = QLineEdit()
        browse = QPushButton("…", maximumWidth=30)
        browse.clicked.connect(self._browse_script)
        row = QHBoxLayout()
        row.addWidget(self.script)
        row.addWidget(browse)
        self.inputs = QListWidget()
        add_files = QPushButton("Add files…")
        add_files.clicked.connect(self._add_files)
        add_dir = QPushButton("Add folder…")
        add_dir.clicked.connect(self._add_dir)
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda: [self.inputs.takeItem(self.inputs.row(i))
                                        for i in self.inputs.selectedItems()])
        irow = QHBoxLayout()
        for b in (add_files, add_dir, remove):
            irow.addWidget(b)
        self.after = QListWidget()
        for s in project.steps:
            item = QListWidgetItem(s.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if s is project.steps[-1] else Qt.Unchecked)
            self.after.addItem(item)
        form.addRow("Name", self.name)
        form.addRow("Job script", row)
        form.addRow("Inputs", self.inputs)
        form.addRow("", irow)
        form.addRow("Runs after", self.after)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse_script(self):
        path, _ = QFileDialog.getOpenFileName(self, "Job script")
        if path:
            self.script.setText(path)

    def _add_files(self):
        for p in QFileDialog.getOpenFileNames(self, "Input files")[0]:
            self.inputs.addItem(p)

    def _add_dir(self):
        p = QFileDialog.getExistingDirectory(self, "Input folder")
        if p:
            self.inputs.addItem(p)

    def values(self):
        inputs = [self.inputs.item(i).text() for i in range(self.inputs.count())]
        after = [self.after.item(i).text() for i in range(self.after.count())
                 if self.after.item(i).checkState() == Qt.Checked]
        return self.name.text().strip(), self.script.text().strip(), inputs, after


class NewProjectDialog(QDialog):
    """Name, place and cluster of a new project, with where it will be."""

    def __init__(self, parent, profiles, cluster: str | None, location: str):
        super().__init__(parent)
        self.setWindowTitle("New project")
        self.setMinimumWidth(560)
        self.profiles = {p.name: p for p in profiles}
        form = QFormLayout(self)
        self.name = QLineEdit(placeholderText="e.g. WSe2 monolayer BSE")
        self.location = QLineEdit(location)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.location)
        row.addWidget(browse)
        self.cluster = QComboBox()
        self.cluster.addItems(list(self.profiles))
        if cluster in self.profiles:
            self.cluster.setCurrentText(cluster)
        self.remote = QLineEdit()
        self.remote.setToolTip("The project's folder on the cluster: under the cluster's "
                               "projects folder, or an absolute path")
        self.where = QLabel(wordWrap=True, textInteractionFlags=Qt.TextSelectableByMouse)
        form.addRow("Name", self.name)
        form.addRow("Location", row)
        form.addRow("Cluster", self.cluster)
        form.addRow("Folder on the cluster", self.remote)
        form.addRow("", self.where)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Create")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)
        # The cluster folder follows the name until the user types one
        self._remote_edited = False
        self.remote.textEdited.connect(lambda: setattr(self, "_remote_edited", True))
        self.name.textChanged.connect(self._name_changed)
        for signal in (self.location.textChanged, self.cluster.currentTextChanged,
                       self.remote.textChanged):
            signal.connect(self._update)
        self._update()

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Where to create the project",
                                                self.location.text())
        if path:
            self.location.setText(path)

    def local_path(self) -> Path:
        return Path(self.location.text().strip()).expanduser() / folder_name(self.name.text())

    def remote_dir(self) -> str:
        return self.remote.text().strip() or folder_name(self.name.text())

    def problem(self) -> str:
        """Why the project cannot be created as it stands; empty if it can."""
        if not self.name.text().strip():
            return "Give the project a name."
        if not self.profiles:
            return "Add a cluster first (Clusters ▸ New cluster)."
        location = Path(self.location.text().strip()).expanduser()
        if not location.is_dir():
            return f"{location} is not a folder."
        target = self.local_path()
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            return f"{target} exists already."
        return ""

    def _name_changed(self):
        if not self._remote_edited:
            self.remote.setText(folder_name(self.name.text()) if self.name.text().strip() else "")
        self._update()

    def _update(self):
        problem = self.problem()
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not problem)
        if problem:
            self.where.setText(problem)
            return
        profile = self.profiles[self.cluster.currentText()]
        remote = self.remote_dir()
        on_cluster = remote if remote.startswith(("/", "~", "$")) else \
            f"{profile.work_root.rstrip('/')}/{remote}"
        self.where.setText(f"Here: {self.local_path()}\nOn {profile.name}: {on_cluster}")


class ProjectView(QWidget):
    projectOpened = Signal(object)        # the Project

    def __init__(self, session_for_cluster, current_cluster, profiles=lambda: [], parent=None):
        super().__init__(parent)
        self.session_for_cluster = session_for_cluster    # name -> Session
        self.current_cluster = current_cluster            # () -> name or None
        self.profiles = profiles                          # () -> the cluster profiles
        self.project: Project | None = None
        self.follower: LogFollower | None = None
        self._build()
        self._update()

    def _build(self):
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.new_button = QPushButton("New…")
        self.new_button.clicked.connect(lambda: launch(self, self.new_project(), "New project"))
        self.open_button = QPushButton("Open…")
        self.open_button.clicked.connect(lambda: self.open_dialog())
        self.new_step_button = QPushButton("New step…")
        self.new_step_button.setToolTip("A step with a job description: resources and runs, "
                                        "its job script written and checked for you")
        self.new_step_button.clicked.connect(lambda: launch(self, self.new_step(), "New step"))
        self.edit_button = QPushButton("Edit step…")
        self.edit_button.setToolTip("Change the selected step's job description")
        self.edit_button.clicked.connect(lambda: launch(self, self.edit_step(), "Edit step"))
        self.add_button = QPushButton("Add own script…")
        self.add_button.setToolTip("A step with a job script you wrote")
        self.add_button.clicked.connect(lambda: launch(self, self.add_step(), "Add step"))
        self.check_button = QPushButton("Check")
        self.check_button.setToolTip("Check the selected steps (or all) without submitting")
        self.check_button.clicked.connect(lambda: launch(self, self.check(), "Check"))
        self.submit_button = QPushButton("Submit")
        self.submit_button.setToolTip("Selected steps, or every step not done (in order, "
                                      "each after the steps it needs)")
        self.submit_button.clicked.connect(lambda: launch(self, self.submit(), "Submit"))
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(lambda: launch(self, self.cancel(), "Cancel"))
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(lambda: launch(self, self.refresh(), "Refresh"))
        self.watch = QCheckBox(f"Every {DEFAULT_INTERVAL:.0f} s")
        self.watch.toggled.connect(self._watch)
        for w in (self.new_button, self.open_button, self.new_step_button, self.edit_button,
                  self.add_button, self.check_button, self.submit_button,
                  self.cancel_button, self.refresh_button, self.watch):
            bar.addWidget(w)
        bar.addStretch()
        self.get_mode = QComboBox()
        self.get_mode.addItems(GET_MODES)
        self.get_button = QPushButton("Get results…")
        self.get_button.clicked.connect(lambda: launch(self, self.get_results(), "Get results"))
        bar.addWidget(self.get_mode)
        bar.addWidget(self.get_button)
        layout.addLayout(bar)
        self.title = QLabel()
        layout.addWidget(self.title)

        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._step_selected)
        self.table.itemDoubleClicked.connect(
            lambda item: launch(self, self.edit_step(), "Edit step"))
        split.addWidget(self.table)
        self.log = QPlainTextEdit(readOnly=True, placeholderText="The selected step's output")
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.log.setMaximumBlockCount(20000)
        split.addWidget(self.log)
        layout.addWidget(split)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.watch_timer = QTimer(self, interval=int(DEFAULT_INTERVAL * 1000))
        self.watch_timer.timeout.connect(lambda: launch(self, self.refresh(), "Refresh"))
        # The project's files changed by another program (the command line):
        # read again, a moment after the last change
        self.disk_watcher = QFileSystemWatcher(self)
        self.disk_timer = QTimer(self, singleShot=True, interval=300)
        self.disk_watcher.directoryChanged.connect(lambda _: self.disk_timer.start())
        self.disk_watcher.fileChanged.connect(lambda _: self.disk_timer.start())
        self.disk_timer.timeout.connect(self.reload_if_stale)
        self.log_timer = QTimer(self, interval=10_000)
        self.log_timer.timeout.connect(lambda: launch(self, self._read_log(), "Log"))

    # -- project ---------------------------------------------------------------------

    def _update(self):
        has = self.project is not None
        for b in (self.add_button, self.new_step_button, self.check_button, self.submit_button,
                  self.cancel_button, self.refresh_button, self.watch, self.get_button,
                  self.get_mode):
            b.setEnabled(has)
        self._update_edit_button()
        if not has:
            self.title.setText("No project open. A project is a folder with one sub-folder "
                               "per step, mirrored on the cluster.")
            self.table.setRowCount(0)
            return
        p = self.project
        self.title.setText(f"<b>{p.name}</b> · {p.path} · cluster {p.cluster} · "
                           f"cluster folder {p.remote_dir}")
        selected = set(self.selected_steps())
        self.table.setRowCount(0)
        for step in p.steps:
            r = self.table.rowCount()
            self.table.insertRow(r)
            j = step.latest
            values = [step.id] + ([j.id, j.state.value, j.reason, result_text(j.exit_code, j.signal),
                                   when(j.started), when(j.ended)] if j else
                                  ["", "not run", "", "", "", ""]) + [", ".join(step.after)]
            for c, v in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))
            if step.id in selected:
                self.table.selectRow(r)

    def reload_if_stale(self) -> bool:
        """Read the project again if its files changed since (written by the
        command line, say): the window then shows them, and what it saves
        does not undo them. Whether it was read again."""
        if self.project is None or not self.project.stale():
            return False
        self.project = Project.load(self.project.path)
        self._watch_files()
        self._update()
        return True

    def _watch_files(self):
        paths = self.disk_watcher.directories() + self.disk_watcher.files()
        if paths:
            self.disk_watcher.removePaths(paths)
        if self.project is not None:
            # Folders, not files: files are replaced when saved (written whole)
            self.disk_watcher.addPaths([str(self.project.path)]
                                       + [str(s.folder) for s in self.project.steps])

    def open_project(self, path):
        self.project = Project.load(path)
        self._watch_files()
        self.follower = None
        self.log.clear()
        self._update()
        self.projectOpened.emit(self.project)

    def open_dialog(self):
        path = QFileDialog.getExistingDirectory(self, "Open project")
        if path:
            try:
                self.open_project(path)
            except Exception as e:
                show_error(self, "Open project", str(e))

    async def new_project(self, dialog: NewProjectDialog | None = None):
        """Ask for the name, place and cluster, then create and open the project."""
        if not self.profiles():
            raise ValueError("Add a cluster first (Clusters ▸ New cluster).")
        if dialog is None:
            location = settings().value("projectLocation", str(Path.home()))
            dialog = NewProjectDialog(self, self.profiles(), self.current_cluster(), location)
            if await ask(dialog) != QDialog.Accepted:
                return
        problem = dialog.problem()
        if problem:
            raise ValueError(problem)
        path = dialog.local_path()
        Project.create(path, cluster=dialog.cluster.currentText(),
                       name=dialog.name.text().strip(), remote_dir=dialog.remote_dir())
        settings().setValue("projectLocation", str(path.parent))
        self.open_project(path)

    async def add_step(self):
        self.reload_if_stale()
        d = AddStepDialog(self, self.project)
        if await ask(d) != QDialog.Accepted:
            return
        name, script, inputs, after = d.values()
        if not name or not script:
            raise ValueError("A step needs a name and a job script.")
        self.project.add_step(name, script, inputs, after)
        self._update()

    def _update_edit_button(self):
        keys = self.selected_steps() if self.project is not None else []
        described = len(keys) == 1 and self.project.step(keys[0]).described
        self.edit_button.setEnabled(described)
        self.edit_button.setToolTip("Change the selected step's job description" if described
                                    else "Select one step made with New step…")

    def _profile(self):
        name = self.project.cluster.lower()
        profile = next((p for p in self.profiles() if p.name.lower() == name), None)
        if profile is None:
            raise ValueError(f"No cluster profile named {self.project.cluster}: add it first "
                             "(Clusters ▸ New cluster).")
        return profile

    async def new_step(self, dialog: StepEditor | None = None):
        self.reload_if_stale()
        profile = self._profile()
        dialog = dialog or StepEditor(self, self.project, profile)
        if dialog.result() != QDialog.Accepted and await ask(dialog) != QDialog.Accepted:
            return
        step = self.project.add_job_step(dialog.name.text().strip(), dialog.spec(),
                                         dialog.new_inputs(), dialog.after_steps(), profile)
        self._update()
        self.status.setText(f"Added step {step.id}.")

    async def edit_step(self, dialog: StepEditor | None = None):
        self.reload_if_stale()
        keys = self.selected_steps()
        if len(keys) != 1 or not self.project.step(keys[0]).described:
            return
        step = self.project.step(keys[0])
        profile = self._profile()
        dialog = dialog or StepEditor(self, self.project, profile, step)
        if dialog.result() != QDialog.Accepted and await ask(dialog) != QDialog.Accepted:
            return
        # Changed meanwhile (a job submitted from the command line)? Apply the
        # edit to the step as it is now
        if self.reload_if_stale():
            step = self.project.step(step.id)
        if step.script_edited and not await confirm(
                self, "Edit step", f"{step.id}/{step.script} was edited by hand. Saving writes it "
                "again from the description: the edits are lost. Save?"):
            return
        self.project.update_job_step(step, dialog.spec(), dialog.after_steps(),
                                     dialog.new_inputs(), profile)
        self._update()
        self.status.setText(f"Saved {step.id}.")

    async def check(self):
        self.reload_if_stale()
        """Every check of the selected steps (or all), without submitting; with the
        cluster when connected (modules, pseudopotentials kept there)."""
        keys = self.selected_steps()
        steps = [self.project.step(k) for k in keys] if keys else list(self.project.steps)
        session = self.session_for_cluster(self.project.cluster)
        on_cluster = session.connected
        if on_cluster:
            transport, scheduler = await session.ready()
            runner = Runner(self.project, session.profile, transport, scheduler, session.modules)
        else:
            runner = Runner(self.project, self._profile(), None, None)
        errors, warnings = await runner.check(steps, on_cluster=on_cluster)
        lines = [f"Error: {s}: {m}" for s, ms in errors.items() for m in ms] + \
                [f"Warning: {s}: {m}" for s, ms in warnings.items() for m in ms]
        n_err, n_warn = sum(map(len, errors.values())), sum(map(len, warnings.values()))
        text = (f"Checked {len([s for s in steps if s.described])} step(s)"
                + (" with the cluster" if on_cluster else " (connect to also test the modules "
                   "and the pseudopotentials kept on the cluster)")
                + f": {n_err} error{'s' if n_err != 1 else ''}, {n_warn} warning"
                + f"{'s' if n_warn != 1 else ''}.")
        self.status.setText(text)
        self.status.setToolTip("\n".join(lines))
        if lines:
            box = QMessageBox(QMessageBox.Warning if errors else QMessageBox.Information,
                              "Check", text, parent=self)
            box.setDetailedText("\n".join(lines))
            await ask(box)
        return errors, warnings

    def selected_steps(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).text() for r in rows if self.table.item(r, 0)]

    # -- running ----------------------------------------------------------------------

    async def runner(self) -> Runner:
        session = self.session_for_cluster(self.project.cluster)
        transport, scheduler = await session.ready()
        return Runner(self.project, session.profile, transport, scheduler, session.modules)

    async def submit(self):
        self.reload_if_stale()
        runner = await self.runner()
        keys = self.selected_steps()
        steps = [self.project.step(k) for k in keys] if keys else None
        check = True
        while True:
            try:
                submitted = await runner.submit(steps, check=check)
                break
            except ScriptEdited as e:
                choice = await self._ask_about_edits(e)
                if choice is None:
                    return
                for step in e.steps:
                    if choice == "keep":
                        step.keep_script()
                    else:
                        step.write_script(runner.profile)
            except SubmitRefused as e:
                if not await self._ask_submit_anyway(e):
                    return
                check = False
        self._update()
        text = ("Submitted " + ", ".join(f"{s.id} (job {j})" for s, j in submitted)
                if submitted else "Nothing to submit: every step has completed or is queued.")
        warnings = [f"{step}: {m}" for step, ms in runner.warnings.items() for m in ms]
        self.status.setText(text + (f" ({len(warnings)} warnings)" if warnings else ""))
        self.status.setToolTip("\n".join(warnings))

    async def _ask_about_edits(self, e: ScriptEdited) -> str | None:
        """"keep", "rewrite", or None to cancel."""
        names = ", ".join(s.id for s in e.steps)
        box = QMessageBox(QMessageBox.Question, "Submit",
                          f"The job script of {names} was edited by hand since it was written "
                          "from the step's job description.", parent=self)
        box.setInformativeText("Keep the edits (the step then has a script of its own, no "
                               "longer written from the description), or write the script "
                               "again (the edits are lost)?")
        keep = box.addButton("Keep the edits", QMessageBox.AcceptRole)
        rewrite = box.addButton("Write it again", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        await ask(box)
        clicked = box.clickedButton()
        return "keep" if clicked is keep else "rewrite" if clicked is rewrite else None

    async def _ask_submit_anyway(self, e: SubmitRefused) -> bool:
        box = QMessageBox(QMessageBox.Warning, "Submit",
                          "The checks found problems, so nothing was submitted.", parent=self)
        box.setInformativeText("Submit anyway? Slurm may refuse the jobs, or they may fail.")
        box.setDetailedText("\n".join(f"{step}: {m}" for step, ms in e.problems.items()
                                       for m in ms))
        anyway = box.addButton("Submit anyway", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        await ask(box)
        return box.clickedButton() is anyway

    async def cancel(self):
        self.reload_if_stale()
        keys = self.selected_steps()
        steps = [self.project.step(k) for k in keys] if keys else None
        what = ", ".join(keys) if keys else "every running step"
        if not await confirm(self, "Cancel", f"Cancel the jobs of {what}?"):
            return
        ids = await (await self.runner()).cancel(steps)
        await self.refresh()
        self.status.setText("Cancelled " + " ".join(ids) if ids else "Nothing running.")

    async def refresh(self):
        self.reload_if_stale()
        changed = await (await self.runner()).refresh()
        self._update()
        if not any(s.active for s in self.project.steps):
            self.watch.setChecked(False)
        if changed:
            self.status.setText("Updated " + ", ".join(s.id for s in changed))

    def _watch(self, on: bool):
        if on:
            self.watch_timer.start()
            launch(self, self.refresh(), "Refresh")
        else:
            self.watch_timer.stop()

    # -- log ----------------------------------------------------------------------------

    def _step_selected(self):
        self._update_edit_button()
        keys = self.selected_steps()
        self.log_timer.stop()
        self.follower = None
        if len(keys) == 1:
            launch(self, self._open_log(keys[0]), "Log")

    async def _open_log(self, key: str):
        step = self.project.step(key)
        job = step.latest
        self.log.clear()
        if job is None or not job.output:
            return
        runner = await self.runner()
        path = f"{await runner.remote_dir(step)}/{job.output}"
        self.follower = LogFollower(runner.transport, path)
        self.log.setPlainText(await self.follower.read_new() or f"({job.output} is empty or not there yet)")
        if step.active:
            self.log_timer.start()

    async def _read_log(self):
        if self.follower is None:
            return
        text = await self.follower.read_new()
        if text:
            self.log.moveCursor(self.log.textCursor().MoveOperation.End)
            self.log.insertPlainText(text)

    # -- results -------------------------------------------------------------------------

    async def get_results(self, ask_first: bool = True):
        self.reload_if_stale()
        runner = await self.runner()
        mode = GET_MODES[self.get_mode.currentText()]
        keys = self.selected_steps()
        steps = [self.project.step(k) for k in keys] if keys else \
            [s for s in self.project.steps if s.jobs]
        if not steps:
            raise ValueError("No step has run yet.")
        plans, lines, total, count, have = [], [], 0, 0, 0
        for step in steps:
            self.status.setText(f"Listing {step.id} on the cluster…")
            remote = await runner.remote_dir(step)
            choices = choose(await scan(runner.transport, remote), mode)
            planned = plan(choices, step.folder / OUTPUTS_DIR)
            items = [i for i in planned if not i.have]
            size = sum(i.file.size - i.offset for i in items)
            skipped = [c for c in choices if c.reason]
            parts = [f"{len(items)} to get ({human(size)})"]
            if len(planned) > len(items):
                parts.append(f"{len(planned) - len(items)} already here")
            if skipped:
                parts.append(f"{len(skipped)} skipped ({human(sum(c.file.size for c in skipped))})")
            lines.append(f"{step.id}: " + ", ".join(parts))
            plans.append((step, remote, items))
            total, count, have = total + size, count + len(items), have + len(planned) - len(items)
        if count == 0:
            # Nothing new: say so, nothing to confirm
            self.status.setText(f"Nothing new to get: {have} files already in the steps' "
                                f"{OUTPUTS_DIR}/ folders. " + " · ".join(lines))
            return
        if ask_first and not await confirm(self, "Get results", "\n".join(lines)
                                           + f"\n\nDownload {count} files ({human(total)}) into "
                                             f"each step's {OUTPUTS_DIR}/ folder?"):
            return
        done = 0
        for step, remote, items in plans:
            for item in items:
                self.status.setText(f"{step.id}: {item.file.path} ({human(item.file.size)}) · "
                                    f"{human(done)} of {human(total)}")
                await fetch(runner.transport, remote, item)
                done += item.file.size - item.offset
        self.status.setText(f"Downloaded {human(done)}.")

    def output_folder(self, key: str) -> Path:
        return self.project.step(key).folder / OUTPUTS_DIR
