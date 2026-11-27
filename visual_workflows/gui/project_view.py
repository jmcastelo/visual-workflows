"""A project: its steps, running them, their logs and results."""

from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QStackedWidget,
                               QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ..core.monitor import DEFAULT_INTERVAL, LogFollower
from ..core.project import OUTPUTS_DIR, Project, folder_name
from ..core.runner import Runner, ScriptEdited, SubmitRefused
from ..core.scheduler import JobState
from ..core.transfer import choose, fetch, human, plan, scan
from .asyncqt import ask, confirm, launch, settings, show_error
from .jobs_view import result_text, when
from .step_editor import StepEditor

COLUMNS = ["Step", "State", "Job", "Result", "Ended"]
STATE_COLORS = {"completed": "#2e7d32", "failed": "#c62828", "cancelled": "#c62828", "timeout": "#c62828",
                "running": "#1565c0", "pending": "#8d6e00", "queued": "#8d6e00"}
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
        """Around the steps: a few project-wide actions on top; the steps on the
        left (each study's copies under the step they were copied from); the
        selected step on the right, with only the actions that fit it and its
        output, input, script and results (the GUI redesign, stage 2)."""
        outer = QVBoxLayout(self)
        self.pages = QStackedWidget()
        outer.addWidget(self.pages)

        # -- no project open --------------------------------------------------------------
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        self.empty_label = QLabel("No project open. A project is a folder with one sub-folder per "
                                  "step, mirrored on the cluster.", alignment=Qt.AlignCenter,
                                  wordWrap=True)
        el.addWidget(self.empty_label)
        row = QHBoxLayout()
        row.addStretch(1)
        self.new_button = QPushButton("New project…")
        self.new_button.clicked.connect(lambda: launch(self, self.new_project(), "New project"))
        self.open_button = QPushButton("Open project…")
        self.open_button.clicked.connect(lambda: self.open_dialog())
        row.addWidget(self.new_button)
        row.addWidget(self.open_button)
        row.addStretch(1)
        el.addLayout(row)
        el.addStretch(2)
        self.pages.addWidget(empty)

        # -- a project ----------------------------------------------------------------------
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(textInteractionFlags=Qt.TextSelectableByMouse)
        layout.addWidget(self.title)
        bar = QHBoxLayout()
        self.add_menu_button = QToolButton(text="Add", popupMode=QToolButton.InstantPopup)
        self.add_menu_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        add_menu = QMenu(self.add_menu_button)
        self.chain_action = add_menu.addAction("QE chain to excitons…",
                                               lambda: launch(self, self.new_qe_chain(), "New QE chain"))
        self.chain_action.setToolTip("scf, nscf, p2y, bands (and GW, BSE) made in one go from an scf input")
        self.new_step_action = add_menu.addAction("Step…", lambda: launch(self, self.new_step(), "New step"))
        self.new_step_action.setToolTip("A step with a job description: its job script written and "
                                        "checked for you")
        self.add_script_action = add_menu.addAction("Step with your own script…",
                                                    lambda: launch(self, self.add_step(), "Add step"))
        self.study_action = add_menu.addAction("Convergence study…", self.show_studies)
        add_menu.setToolTipsVisible(True)
        self.add_menu_button.setMenu(add_menu)
        self.check_button = QPushButton("Check all")
        self.check_button.setToolTip("Every step's checks, without submitting")
        self.check_button.clicked.connect(lambda: launch(self, self.check(everything=True), "Check"))
        self.submit_all_button = QPushButton("Submit all")
        self.submit_all_button.setToolTip("Every step not done, in order, each after the steps it needs")
        self.submit_all_button.clicked.connect(lambda: launch(self, self.submit(everything=True), "Submit"))
        self.diagnose_all_button = QPushButton("Diagnose all")
        self.diagnose_all_button.setToolTip("Why jobs ended badly: every step whose latest job "
                                            "failed, timed out or was cancelled, its logs and "
                                            "outputs read for known messages")
        self.diagnose_all_button.clicked.connect(
            lambda: launch(self, self.diagnose(everything=True), "Diagnose"))
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("The steps' jobs' states, from the cluster")
        self.refresh_button.clicked.connect(lambda: launch(self, self.refresh(), "Refresh"))
        self.watch = QCheckBox(f"every {DEFAULT_INTERVAL:.0f} s")
        self.watch.setToolTip("Refresh on its own while steps run")
        self.watch.toggled.connect(self._watch)
        self.get_all_button = self._get_button("Get results", everything=True)
        self.results_button = QPushButton("Results")
        self.results_button.setToolTip("Every step's downloaded results, read and checked")
        self.results_button.clicked.connect(lambda: self.show_results(everything=True))
        self.studies_button = QPushButton("Studies")
        self.studies_button.setToolTip("Convergence studies: a GW or BSE step over several values of one "
                                       "variable, and a result read back from each")
        self.studies_button.clicked.connect(self.show_studies)
        self.excitons_button = QPushButton("Visual Excitons")
        self.excitons_button.setToolTip("The project's results opened in Visual Excitons (it asks which "
                                        "step's, when several have some)")
        self.excitons_button.clicked.connect(
            lambda: launch(self, self.open_in_excitons(everything=True), "Open in Visual Excitons"))
        for w in (self.add_menu_button, self.check_button, self.submit_all_button,
                  self.diagnose_all_button, self.refresh_button, self.watch):
            bar.addWidget(w)
        bar.addStretch(1)
        for w in (self.get_all_button, self.results_button, self.studies_button, self.excitons_button):
            bar.addWidget(w)
        layout.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setRootIsDecorated(True)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._step_selected)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        split.addWidget(self.tree)

        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        self.step_label = QLabel(wordWrap=True, textInteractionFlags=Qt.TextSelectableByMouse)
        pl.addWidget(self.step_label)
        actions = QHBoxLayout()
        self.edit_button = QPushButton("Edit…")
        self.edit_button.setToolTip("The step's job description: runs, inputs, resources")
        self.edit_button.clicked.connect(lambda: launch(self, self.edit_step(), "Edit step"))
        self.submit_button = QPushButton("Submit")
        self.submit_button.setToolTip("The selected steps, each after the steps it needs")
        self.submit_button.clicked.connect(lambda: launch(self, self.submit(), "Submit"))
        self.cancel_button = QPushButton("Cancel job")
        self.cancel_button.clicked.connect(lambda: launch(self, self.cancel(), "Cancel"))
        self.step_check_button = QPushButton("Check")
        self.step_check_button.clicked.connect(lambda: launch(self, self.check(), "Check"))
        self.diagnose_button = QPushButton("Diagnose")
        self.diagnose_button.setToolTip("Why the job ended badly: its logs and outputs read for known messages")
        self.diagnose_button.clicked.connect(lambda: launch(self, self.diagnose(), "Diagnose"))
        self.get_button = self._get_button("Get results")
        self.step_results_button = QPushButton("Results")
        self.step_results_button.clicked.connect(lambda: self.show_results())
        self.bands_button = QPushButton("Bands")
        self.bands_button.setToolTip("The downloaded bands drawn: at the SAVE's k-points with the density "
                                     "of states, a GW's energies over them, the band structure along a "
                                     "path; the gaps, and the bands a BSE up to an energy needs")
        self.bands_button.clicked.connect(self.show_bands)
        self.step_studies_button = QPushButton("Study…")
        self.step_studies_button.setToolTip("A convergence study from this step")
        self.step_studies_button.clicked.connect(self.show_studies)
        self.step_excitons_button = QPushButton("Visual Excitons")
        self.step_excitons_button.clicked.connect(
            lambda: launch(self, self.open_in_excitons(), "Open in Visual Excitons"))
        self.step_buttons = [self.edit_button, self.submit_button, self.cancel_button, self.step_check_button,
                             self.diagnose_button, self.get_button, self.step_results_button,
                             self.bands_button, self.step_studies_button, self.step_excitons_button]
        for b in self.step_buttons:
            actions.addWidget(b)
        actions.addStretch(1)
        pl.addLayout(actions)
        self.step_tabs = QTabWidget()
        out = QWidget()
        ol = QVBoxLayout(out)
        row = QHBoxLayout()
        row.addWidget(QLabel("Show"))
        self.log_source = QComboBox()
        self.log_source.setToolTip("The job's log, or a run's own output: a QE run's output file, "
                                   "the log p2y, yambo or ypp writes")
        self.log_source.activated.connect(lambda _: self._log_source_changed())
        row.addWidget(self.log_source)
        row.addStretch(1)
        ol.addLayout(row)
        self.log = QPlainTextEdit(readOnly=True, placeholderText="The selected step's output")
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.log.setMaximumBlockCount(20000)
        ol.addWidget(self.log)
        self.step_tabs.addTab(out, "Output")
        inp = QWidget()
        il = QVBoxLayout(inp)
        row = QHBoxLayout()
        row.addWidget(QLabel("File"))
        self.input_choice = QComboBox()
        self.input_choice.activated.connect(lambda _: self._show_input())
        row.addWidget(self.input_choice)
        row.addStretch(1)
        il.addLayout(row)
        self.input_view = QPlainTextEdit(readOnly=True, placeholderText="The step's input files")
        self.input_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        il.addWidget(self.input_view)
        self.step_tabs.addTab(inp, "Input")
        self.script_view = QPlainTextEdit(readOnly=True, placeholderText="The step's job script")
        self.script_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.step_tabs.addTab(self.script_view, "Script")
        self.results_view = QPlainTextEdit(readOnly=True, placeholderText="The step's downloaded results")
        self.results_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.results_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.step_tabs.addTab(self.results_view, "Results")
        self.step_tabs.currentChanged.connect(lambda _: self._fill_step_tabs())
        pl.addWidget(self.step_tabs, 1)
        split.addWidget(panel)
        split.setStretchFactor(1, 1)
        split.setSizes([420, 900])
        layout.addWidget(split, 1)
        self.status = QLabel(wordWrap=True)
        layout.addWidget(self.status)
        self.pages.addWidget(page)

        self.get_mode = "default"                       # what Get results brings back (GET_MODES)
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

    def _get_button(self, text: str, everything: bool = False) -> QToolButton:
        """Get results, with what to bring back in its menu (the results by
        default; what Visual Excitons reads; everything)."""
        button = QToolButton(text=text, popupMode=QToolButton.MenuButtonPopup)
        button.setToolTip(("Every step's" if everything else "The selected steps'") + " outputs, "
                          "downloaded into each step's outputs/ folder; the arrow says which files")
        menu = QMenu(button)
        for label, mode in GET_MODES.items():
            menu.addAction(label, lambda m=mode: self._get(m, everything))
        button.setMenu(menu)
        button.clicked.connect(lambda: self._get("default", everything))
        return button

    def _get(self, mode: str, everything: bool):
        self.get_mode = mode
        launch(self, self.get_results(everything=everything), "Get results")

    # -- project ---------------------------------------------------------------------

    def _update(self):
        has = self.project is not None
        self.pages.setCurrentIndex(1 if has else 0)
        if not has:
            self.tree.clear()
            return
        p = self.project
        self.title.setText(f"<b>{p.name}</b> · {p.path} · cluster {p.cluster} · "
                           f"cluster folder {p.remote_dir}")
        # Diagnose all only when there is something to diagnose
        self.diagnose_all_button.setVisible(any(
            s.latest is not None and s.latest.state.ended and s.latest.state is not JobState.COMPLETED
            for s in p.steps))
        selected = set(self.selected_steps())
        expanded = {i.data(0, Qt.UserRole) for i in self._all_items()
                    if i.isExpanded() and i.data(0, Qt.UserRole)}
        self.tree.blockSignals(True)
        self.tree.clear()
        from ..core.studies import studies
        in_study = {}
        for st in studies(p):
            for sid in st.steps:
                in_study[sid] = st
        items = {}
        for step in p.steps:
            if step.id in in_study:
                continue
            items[step.id] = self._step_item(step)
            self.tree.addTopLevelItem(items[step.id])
        for st in studies(p):
            parent = items.get(st.base)
            group = QTreeWidgetItem([f"study {st.name}: {st.variable} × {len(st.steps)}"])
            group.setData(0, Qt.UserRole, f"study:{st.name}")
            group.setToolTip(0, f"{st.variable} over {', '.join(st.values)}"
                             + (f" {st.unit}" if st.unit else "") + f", copied from {st.base}")
            for sid in st.steps:
                try:
                    group.addChild(self._step_item(p.step(sid)))
                except Exception:                            # noqa: BLE001 - a step removed by hand
                    continue
            (parent.addChild(group) if parent is not None else self.tree.addTopLevelItem(group))
        for item in self._all_items():
            key = item.data(0, Qt.UserRole)
            if key in expanded:
                item.setExpanded(True)
            if key in selected:
                item.setSelected(True)
                parent = item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
        self.tree.blockSignals(False)
        self._show_panel()

    def _step_item(self, step) -> QTreeWidgetItem:
        j = step.latest
        values = [step.id] + ([j.state.value, j.id, result_text(j.exit_code, j.signal), when(j.ended)]
                              if j else ["not run", "", "", ""])
        item = QTreeWidgetItem([str(v) for v in values])
        item.setData(0, Qt.UserRole, step.id)
        color = STATE_COLORS.get(j.state.value if j else "")
        if color:
            item.setForeground(1, QBrush(QColor(color)))
        if j and j.reason:
            item.setToolTip(1, j.reason)
        return item

    def _all_items(self):
        out, stack = [], [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop(0)
            out.append(item)
            stack[0:0] = [item.child(i) for i in range(item.childCount())]
        return out

    def select_steps(self, ids):
        """Select these steps (ids or names) in the tree, the others not."""
        ids = {self.project.step(k).id for k in ids}
        self.tree.clearSelection()
        for item in self._all_items():
            if item.data(0, Qt.UserRole) in ids:
                parent = item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                item.setSelected(True)

    def step_states(self) -> dict:
        """Each step's state as the tree shows it."""
        return {i.data(0, Qt.UserRole): i.text(1) for i in self._all_items()
                if i.data(0, Qt.UserRole) and not str(i.data(0, Qt.UserRole)).startswith("study:")}

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

    async def new_qe_chain(self, dialog=None):
        from ..core.chain import check_chain
        from ..core.qe_chain import add_qe_chain
        from .qe_chain_dialog import QeChainDialog
        self.reload_if_stale()
        profile = self._profile()
        dialog = dialog or QeChainDialog(self, self.project, profile)
        if dialog.result() != QDialog.Accepted and await ask(dialog) != QDialog.Accepted:
            return
        steps = add_qe_chain(self.project, dialog.chain(), profile=profile)
        self._update()
        found = check_chain(self.project, steps)
        problems = sum(len(v) for v in found.values())
        self.status.setText(f"Added steps {', '.join(s.id for s in steps)}"
                            + (f"; {problems} problem{'s' if problems != 1 else ''} in the chain: "
                               "Check tells them." if problems else "."))

    async def _refresh_input(self, step, inp):
        from ..core.yambo_refresh import RefreshError, refresh_input
        try:
            return await refresh_input(await self.runner(), step, inp)
        except RefreshError as e:
            raise ValueError(str(e)) from None

    async def edit_step(self, dialog: StepEditor | None = None):
        self.reload_if_stale()
        keys = self.selected_steps()
        if len(keys) != 1 or not self.project.step(keys[0]).described:
            return
        step = self.project.step(keys[0])
        profile = self._profile()
        if dialog is None:
            dialog = StepEditor(self, self.project, profile, step)
            dialog.refresher = self._refresh_input        # connects when asked
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

    async def check(self, everything: bool = False):
        """Every check of the selected steps (or all), without submitting; with the
        cluster when connected (modules, pseudopotentials kept there)."""
        self.reload_if_stale()
        keys = [] if everything else self.selected_steps()
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

    async def diagnose(self, everything: bool = False):
        """Why the selected steps' latest jobs (or, with `everything` or none
        selected, every one that ended badly) ended so, in a window of their own."""
        from ..core.diagnosis import diagnose_step, report
        ids = [] if everything else self.selected_steps()
        runner = await self.runner()
        await runner.refresh()                       # the states as they are now
        self._update()
        steps = [self.project.step(i) for i in ids] if ids else [
            s for s in self.project.steps if s.latest is not None and s.latest.state.ended
            and s.latest.state is not JobState.COMPLETED]
        steps = [s for s in steps if s.latest is not None]
        if not steps:
            self.status.setText("No step's latest job ended badly; select a step and Diagnose "
                                "to read its job anyway.")
            return
        blocks = []
        for step in steps:
            blocks.append("\n".join(report(await diagnose_step(runner, self.project.step(step.id)))))
        self._text_window(f"Diagnosis · {self.project.name}", "\n\n".join(blocks),
                          "Causes read from the job's log and its runs' outputs (their last 64 KB).")
        self.diagnosis_dialog = self._last_window

    def _text_window(self, title: str, text: str, note: str):
        dialog = QDialog(self, windowTitle=title)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit(readOnly=True)
        view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        view.setPlainText(text)
        layout.addWidget(view)
        layout.addWidget(QLabel(note, wordWrap=True))
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(900, 500)
        self._last_window = dialog
        dialog.show()

    async def open_in_excitons(self, everything: bool = False):
        """Visual Excitons started on the selected step's results (one step),
        or on the project."""
        from ..core.excitons import ExcitonsError, opening, shell_line, start
        ids = [] if everything else self.selected_steps()
        if len(ids) > 1:
            self.status.setText("Select one step, or none for the whole project.")
            return
        try:
            o = opening(self.project, self.project.step(ids[0]) if ids else None)
            self.status.setText("Starting Visual Excitons…")
            await asyncio.to_thread(start, o.arguments())     # its early stop waited for aside
        except ExcitonsError as e:
            show_error(self, "Open in Visual Excitons", str(e))
            return
        self.status.setText("Visual Excitons started" + (f" ({' '.join(o.notes)})" if o.notes else "")
                            + ": " + shell_line(o.arguments()))

    def show_bands(self):
        """The downloaded SAVE's bands (the selected step's, or the project's
        first), in a window of their own, with the GW energies computed on it
        and the project's band structures along a path."""
        from ..core.band_structure import BandStructureError, is_bands_step, step_band_structure
        from ..core.bands import (band_structure_steps, find_save, gw_of_save, k_point_names,
                                  save_bands, used_windows)
        from ..core.results import ResultsError, read_qp
        from .bands_view import BandsDialog
        ids = self.selected_steps()
        chosen = self.project.step(ids[0]) if ids else None
        path_step = chosen if chosen is not None and is_bands_step(chosen) else None
        try:
            # a bands step has no SAVE of its own: the project's
            step, path = find_save(self.project, None if path_step is not None else chosen)
            sb = save_bands(path)
        except ResultsError as e:
            show_error(self, "Bands", str(e))
            return
        gws = []
        for step_id, qp_path in gw_of_save(self.project, path):
            try:
                gws.append((step_id, read_qp(qp_path, sb.save.occupied)))
            except ResultsError:
                continue
        structures, problems = [], []
        for s in band_structure_steps(self.project):
            try:
                structures.append((s.id, step_band_structure(self.project, s)))
            except BandStructureError as e:
                problems.append(f"  {e}")
        if path_step is not None:               # the selected bands step first
            structures.sort(key=lambda item: item[0] != path_step.id)
        extra = []
        if sb.save.occupied:
            for step_id, name, first, last in used_windows(self.project):
                splits = sb.splits(first, last)
                extra.append(f"  {step_id} {name} bands {first}–{last}: "
                             + ("no degenerate states split" if not splits else "; ".join(splits)))
        edges = sb.edge_sets()
        if edges:
            extra.insert(0, f"  Degenerate sets at the gap's edges: bands {edges[0]}–{edges[1]} hold them whole")

        def fit():
            from ..core.bands import fit_report, fit_windows
            return fit_report(fit_windows(self.project, sb))
        extra += problems
        self.bands_dialog = BandsDialog(f"Bands · {step.id} · {self.project.name}", sb, extra, self, fit,
                                        names=k_point_names(path), gws=gws, structures=structures)
        if path_step is not None and structures:
            self.bands_dialog.tabs.setCurrentIndex(1)
        self.bands_dialog.show()

    def _has_path(self, step) -> bool:
        """A bands step whose band structure is downloaded."""
        from ..core.band_structure import find_bands_xml, is_bands_step
        return is_bands_step(step) and find_bands_xml(self.project, step) is not None

    def show_studies(self):
        from .study_view import StudiesDialog
        ids = self.selected_steps()
        self.studies_dialog = StudiesDialog(self.project, ids[0] if ids else None, self._update, self)
        self.studies_dialog.show()

    def show_results(self, everything: bool = False):
        """The downloaded results of the selected steps (or all), read and
        checked, in a window of their own (no cluster)."""
        from ..core.results import project_results, report
        ids = [] if everything else self.selected_steps()
        steps = [self.project.step(i) for i in ids] if ids else None
        try:
            results = project_results(self.project, steps)
        except Exception as e:                       # noqa: BLE001 - shown, never fatal
            show_error(self, "Results", str(e))
            return
        text = ("\n".join(report(results)) if results else
                "No results downloaded yet: Get results… with \"for Visual Excitons\" brings back "
                "what this reads (the SAVE, ndb.QP, ndb.BS_diago_Q*).")
        self._text_window(f"Results · {self.project.name}", text,
                          "Warnings say what looks inconsistent, never whether a result is "
                          "converged. Peaks are rebuilt from the excitons, 0.1 eV broadening.")
        self.results_dialog = self._last_window

    def selected_steps(self) -> list[str]:
        """The selected steps, in the project's order; a study's group stands
        for its steps."""
        if self.project is None:
            return []
        chosen = set()
        for item in self.tree.selectedItems():
            key = item.data(0, Qt.UserRole)
            if str(key).startswith("study:"):
                chosen |= {item.child(i).data(0, Qt.UserRole) for i in range(item.childCount())}
            elif key:
                chosen.add(key)
        return [s.id for s in self.project.steps if s.id in chosen]

    # -- the selected step ------------------------------------------------------------

    def _show_panel(self):
        """The selected step's state, and only the actions that fit it."""
        keys = self.selected_steps()
        for b in self.step_buttons:
            b.setVisible(False)
        self.step_tabs.setEnabled(len(keys) == 1)
        if not keys:
            self.step_label.setText("Select a step: its state, what can be done with it, and its "
                                    "output, input, script and results. Right-click for the same.")
            self._fill_step_tabs()
            return
        steps = [self.project.step(k) for k in keys]
        if len(steps) > 1:
            running = sum(s.active for s in steps)
            self.step_label.setText(f"<b>{len(steps)} steps</b>: {', '.join(keys)}"
                                    + (f" ({running} running or queued)" if running else ""))
            for b in (self.submit_button, self.step_check_button, self.get_button,
                      self.step_results_button, self.diagnose_button):
                b.setVisible(True)
            self.cancel_button.setVisible(any(s.active for s in steps))
            self._fill_step_tabs()
            return
        step = steps[0]
        j = step.latest
        kind = self._kind(step)
        parts = [f"<b>{step.id}</b>" + (f" ({kind})" if kind else "")]
        if j:
            parts.append(f"{j.state.value}" + (f" ({j.reason})" if j.reason else "")
                         + f" · job {j.id}" + (f" · {result_text(j.exit_code, j.signal)}"
                                               if j.exit_code is not None or j.signal else ""))
            parts.append(f"{when(j.started) or '?'} → {when(j.ended) or '…'}")
        else:
            parts.append("not run yet")
        if step.after:
            parts.append(f"after {', '.join(step.after)}")
        self.step_label.setText(" · ".join(parts))
        outputs = step.folder / OUTPUTS_DIR
        has_outputs = outputs.is_dir() and any(outputs.iterdir())
        ended_badly = j is not None and j.state.ended and j.state is not JobState.COMPLETED
        show = {
            self.edit_button: step.described,
            self.submit_button: not step.active,
            self.cancel_button: step.active,
            self.step_check_button: step.described,
            self.diagnose_button: ended_badly,
            self.get_button: bool(step.jobs),
            self.step_results_button: has_outputs,
            self.bands_button: has_outputs and (any(outputs.rglob("ns.db1")) or self._has_path(step)),
            self.step_studies_button: kind in ("GW", "BSE"),
            self.step_excitons_button: has_outputs and any(outputs.rglob("ndb.BS_diago_Q*")),
        }
        for b, on in show.items():
            b.setVisible(on)
        self.submit_button.setText("Submit again" if j is not None and j.state.ended else "Submit")
        self._fill_step_tabs()

    @staticmethod
    def _kind(step) -> str:
        from ..core.studies import kind_of
        kind = kind_of(step) if step.job else None
        if kind:
            return kind.upper()
        runs = step.job.runs if step.job else []
        return ", ".join(dict.fromkeys(r.executable for r in runs)) if runs else ("own script" if not
                                                                                  step.described else "")

    def _fill_step_tabs(self):
        """The current tab's contents for the selected step (Output follows
        the selection by itself)."""
        keys = self.selected_steps()
        step = self.project.step(keys[0]) if self.project is not None and len(keys) == 1 else None
        tab = self.step_tabs.currentIndex()
        if tab == 1:
            self.input_choice.clear()
            if step is not None:
                for rel in step.inputs():
                    if rel.as_posix() != step.script:
                        self.input_choice.addItem(rel.as_posix())
            self._show_input()
        elif tab == 2:
            try:
                text = step.script_text() if step is not None else ""
            except OSError:
                text = ""
            if step is not None and not text:
                text = ("(No job script yet: it is written from the step's job description when the "
                        "step is first submitted, or saved with Edit….)")
            self.script_view.setPlainText(text)
        elif tab == 3:
            self.results_view.setPlainText(self._results_text([step]) if step is not None else "")

    def _show_input(self):
        keys = self.selected_steps()
        name = self.input_choice.currentText()
        if self.project is None or len(keys) != 1 or not name:
            self.input_view.setPlainText("")
            return
        path = self.project.step(keys[0]).folder / name
        try:
            if path.stat().st_size > 2_000_000:
                self.input_view.setPlainText(f"({name}: {human(path.stat().st_size)}, not shown)")
                return
            self.input_view.setPlainText(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            self.input_view.setPlainText(f"({name}: {e.strerror or e})")

    def _results_text(self, steps) -> str:
        from ..core.results import project_results, report
        try:
            results = project_results(self.project, steps)
        except Exception as e:                       # noqa: BLE001 - shown, never fatal
            return f"Not read: {e}"
        return ("\n".join(report(results)) if results else
                "No results downloaded yet: Get results (its arrow: for Visual Excitons) brings back "
                "the SAVE, ndb.QP and ndb.BS_diago_Q* this reads.")

    def _double_clicked(self, item, _column):
        key = item.data(0, Qt.UserRole)
        if key and not str(key).startswith("study:") and self.project.step(key).described:
            launch(self, self.edit_step(), "Edit step")

    def _context_menu(self, pos):
        """The selected step's actions, as the panel shows them."""
        if self.tree.itemAt(pos) is None:
            return
        menu = QMenu(self.tree)
        for b in self.step_buttons:
            if b.isVisible() or b.isVisibleTo(self):
                if isinstance(b, QToolButton):
                    sub = menu.addMenu(b.text())
                    for action in b.menu().actions():
                        sub.addAction(action)
                else:
                    menu.addAction(b.text(), b.click)
        self.context_menu = menu
        menu.popup(self.tree.viewport().mapToGlobal(pos))

    # -- running ----------------------------------------------------------------------

    async def runner(self) -> Runner:
        session = self.session_for_cluster(self.project.cluster)
        transport, scheduler = await session.ready()
        return Runner(self.project, session.profile, transport, scheduler, session.modules)

    async def submit(self, everything: bool = False):
        self.reload_if_stale()
        runner = await self.runner()
        keys = [] if everything else self.selected_steps()
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
        self._show_panel()
        keys = self.selected_steps()
        self.log_timer.stop()
        self.follower = None
        if len(keys) == 1:
            launch(self, self._open_log(keys[0]), "Log")

    def _fill_log_sources(self, step):
        self.log_source.clear()
        self.log_source.addItem("Job log", 0)
        for i, run in enumerate(step.job.runs if step.job else [], start=1):
            what = run.input or run.args
            self.log_source.addItem(f"Run {i}: {run.executable}" + (f" {what}" if what else ""), i)

    def _log_source_changed(self):
        keys = self.selected_steps()
        self.log_timer.stop()
        self.follower = None
        if len(keys) == 1:
            launch(self, self._open_log(keys[0], self.log_source.currentData() or 0), "Log")

    async def _open_log(self, key: str, run: int | None = None):
        step = self.project.step(key)
        if run is None:                  # a step just selected: its job log
            self._fill_log_sources(step)
            run = 0
        job = step.latest
        self.log.clear()
        if job is None or not job.output:
            return
        runner = await self.runner()
        if run:
            path = await runner.run_output(step, run)
            if path is None:
                self.log.setPlainText(f"(run {run} writes to the job's log, or has written "
                                      "nothing yet)")
                return
        else:
            path = f"{await runner.remote_dir(step)}/{job.output}"
        name = path.rsplit("/", 1)[-1]
        self.follower = LogFollower(runner.transport, path)
        self.log.setPlainText(await self.follower.read_new() or f"({name} is empty or not there yet)")
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

    async def get_results(self, ask_first: bool = True, everything: bool = False):
        self.reload_if_stale()
        runner = await self.runner()
        mode = self.get_mode
        keys = [] if everything else self.selected_steps()
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
