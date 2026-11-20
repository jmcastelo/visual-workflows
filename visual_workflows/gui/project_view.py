"""A project: its steps, running them, their logs and results."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.monitor import DEFAULT_INTERVAL, LogFollower
from ..core.project import OUTPUTS_DIR, Project
from ..core.runner import Runner
from ..core.transfer import choose, fetch, human, plan, scan
from .asyncqt import ask, confirm, launch, show_error
from .jobs_view import result_text, when

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


class ProjectView(QWidget):
    projectOpened = Signal(object)        # the Project

    def __init__(self, session_for_cluster, current_cluster, parent=None):
        super().__init__(parent)
        self.session_for_cluster = session_for_cluster    # name -> Session
        self.current_cluster = current_cluster            # () -> name or None
        self.project: Project | None = None
        self.follower: LogFollower | None = None
        self._build()
        self._update()

    def _build(self):
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.new_button = QPushButton("New…")
        self.new_button.clicked.connect(lambda: self.new_project())
        self.open_button = QPushButton("Open…")
        self.open_button.clicked.connect(lambda: self.open_dialog())
        self.add_button = QPushButton("Add step…")
        self.add_button.clicked.connect(lambda: launch(self, self.add_step(), "Add step"))
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
        for w in (self.new_button, self.open_button, self.add_button, self.submit_button,
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
        self.log_timer = QTimer(self, interval=10_000)
        self.log_timer.timeout.connect(lambda: launch(self, self._read_log(), "Log"))

    # -- project ---------------------------------------------------------------------

    def _update(self):
        has = self.project is not None
        for b in (self.add_button, self.submit_button, self.cancel_button, self.refresh_button,
                  self.watch, self.get_button, self.get_mode):
            b.setEnabled(has)
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

    def open_project(self, path):
        self.project = Project.load(path)
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

    def new_project(self, path: str | None = None):
        cluster = self.current_cluster()
        if not cluster:
            show_error(self, "New project", "Choose the cluster first (toolbar).")
            return
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "New project folder")
            if not path:
                return
        try:
            Project.create(path, cluster=cluster)
            self.open_project(path)
        except Exception as e:
            show_error(self, "New project", str(e))

    async def add_step(self):
        d = AddStepDialog(self, self.project)
        if await ask(d) != QDialog.Accepted:
            return
        name, script, inputs, after = d.values()
        if not name or not script:
            raise ValueError("A step needs a name and a job script.")
        self.project.add_step(name, script, inputs, after)
        self._update()

    def selected_steps(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).text() for r in rows if self.table.item(r, 0)]

    # -- running ----------------------------------------------------------------------

    async def runner(self) -> Runner:
        session = self.session_for_cluster(self.project.cluster)
        transport, scheduler = await session.ready()
        return Runner(self.project, session.profile, transport, scheduler)

    async def submit(self):
        runner = await self.runner()
        keys = self.selected_steps()
        steps = [self.project.step(k) for k in keys] if keys else None
        submitted = await runner.submit(steps)
        self._update()
        self.status.setText("Submitted " + ", ".join(f"{s.id} (job {j})" for s, j in submitted)
                            if submitted else "Nothing to submit: every step has completed or is queued.")

    async def cancel(self):
        keys = self.selected_steps()
        steps = [self.project.step(k) for k in keys] if keys else None
        what = ", ".join(keys) if keys else "every running step"
        if not await confirm(self, "Cancel", f"Cancel the jobs of {what}?"):
            return
        ids = await (await self.runner()).cancel(steps)
        await self.refresh()
        self.status.setText("Cancelled " + " ".join(ids) if ids else "Nothing running.")

    async def refresh(self):
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
        runner = await self.runner()
        mode = GET_MODES[self.get_mode.currentText()]
        keys = self.selected_steps()
        steps = [self.project.step(k) for k in keys] if keys else \
            [s for s in self.project.steps if s.jobs]
        if not steps:
            raise ValueError("No step has run yet.")
        plans, lines, total = [], [], 0
        for step in steps:
            remote = await runner.remote_dir(step)
            choices = choose(await scan(runner.transport, remote), mode)
            items = [i for i in plan(choices, step.folder / OUTPUTS_DIR) if not i.have]
            size = sum(i.file.size - i.offset for i in items)
            skipped = [c for c in choices if c.reason]
            lines.append(f"{step.id}: {len(items)} files ({human(size)})"
                         + (f"; skipped {len(skipped)} ({human(sum(c.file.size for c in skipped))})"
                            if skipped else ""))
            plans.append((step, remote, items))
            total += size
        if ask_first and not await confirm(self, "Get results", "\n".join(lines)
                                           + f"\n\nDownload {human(total)} into each step's "
                                             f"{OUTPUTS_DIR}/ folder?"):
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
