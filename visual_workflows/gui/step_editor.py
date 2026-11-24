"""Making and editing a step with a job description: resources, runs, links,
with the partition's limits, the job script and the checks' results shown as
you type (core/jobspec.py)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import jobspec
from ..core.jobspec import DEFAULT_EXECUTABLES, UNLIMITED, JobSpec, Run
from ..core.profile import ClusterProfile, Partition
from ..core.project import JOB_SCRIPT, Project, Step
from ..core.pseudos import espresso_pseudo

# Executables offered for each program when the profile names none
DEFAULT_RUNS = (("qe", "pw"), ("yambo", "p2y"), ("yambo", "yambo"), ("yambo", "ypp"))
RUN_COLUMNS = ("Program", "Input", "Arguments", "-J jobs", "MPI")
ERROR_COLOUR, WARNING_COLOUR = QColor("#c62828"), QColor("#b26a00")


def partition_limits(p: Partition, spec: JobSpec | None = None) -> str:
    """The partition's limits in a line, and what the job asks of them."""
    parts = []
    if p.cores_per_node:
        cores = p.physical_cores_per_node
        parts.append(f"{cores} cores per node" + (
            f" ({p.cores_per_node} CPUs: {p.threads_per_core} threads per core)"
            if p.threads_per_core > 1 else ""))
    largest, smallest = p.memory_per_node_gb, p.min_memory_per_node_gb or p.memory_per_node_gb
    if largest:
        parts.append(f"nodes of {smallest:g}–{largest:g} GB" if smallest != largest
                     else f"nodes of {largest:g} GB")
    if p.memory_limit_per_node_gb:
        parts.append(f"jobs up to {p.memory_limit_per_node_gb:g} GB per node")
    if p.qos:
        parts.append("time up to " + ", ".join(
            f"{limit or 'no limit'} with QOS {name}" for name, limit in p.qos.items()))
    else:
        parts.append(f"time up to {p.max_walltime}" if p.max_walltime.strip()
                     and p.max_walltime.upper() not in UNLIMITED else "no time limit")
    text = "; ".join(parts) + "."
    if spec is not None and p.cores_per_node:
        used = spec.tasks_per_node * spec.cpus_per_task
        text += f" This job: {used} of the {p.physical_cores_per_node} cores of each node."
    return text


class StepEditor(QDialog):
    """A new step (`step` None) or a step with a job description to change."""

    def __init__(self, parent, project: Project, profile: ClusterProfile, step: Step | None = None):
        super().__init__(parent)
        self.project, self.profile, self.step = project, profile, step
        self.setWindowTitle(f"Edit step {step.id}" if step else "New step")
        self.resize(1240, 760)
        job = step.job if step is not None else JobSpec()
        self._timer = QTimer(self, singleShot=True, interval=250)
        self._timer.timeout.connect(self.update_preview)

        outer = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        outer.addWidget(split)
        form_widget = QWidget()
        form = QVBoxLayout(form_widget)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(form_widget)
        split.addWidget(scroll)

        # The step
        general = QGroupBox("Step")
        g = QFormLayout(general)
        self.name = QLineEdit(step.name if step else "",
                              placeholderText="e.g. scf, nscf, p2y, gw, bse")
        self.name.setEnabled(step is None)          # a step's name is its folder's
        self.after = QListWidget(maximumHeight=64)
        for s in project.steps:
            if step is not None and s.id == step.id:
                continue
            item = QListWidgetItem(s.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            checked = (s.id in step.after) if step else (s is project.steps[-1])
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            self.after.addItem(item)
        self.inputs = QListWidget(maximumHeight=72)
        if step is not None:
            for rel in step.inputs():
                if rel.as_posix() == JOB_SCRIPT:
                    continue
                item = QListWidgetItem(rel.as_posix())
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                item.setForeground(QBrush(QColor("gray")))
                item.setToolTip("In the step's folder")
                self.inputs.addItem(item)
        buttons = QHBoxLayout()
        for label, slot in (("Add files…", self._add_files), ("Add folder…", self._add_folder),
                            ("Remove", self._remove_inputs)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        g.addRow("Name", self.name)
        g.addRow("Runs after", self.after)
        g.addRow("Inputs", self.inputs)
        g.addRow("", buttons)
        form.addWidget(general)

        # Resources
        resources = QGroupBox("Resources")
        r = QFormLayout(resources)
        self.partition = QComboBox(editable=True)
        self.partition.addItems(list(profile.partitions))
        self.partition.setCurrentText(job.partition or next(iter(profile.partitions), ""))
        self.limits = QLabel(wordWrap=True)
        self.nodes = QSpinBox(minimum=1, maximum=100000, value=job.nodes)
        self.tasks = QSpinBox(minimum=1, maximum=100000, value=job.tasks_per_node)
        self.cpus = QSpinBox(minimum=1, maximum=1024, value=job.cpus_per_task)
        self.memory = QLineEdit(job.memory, placeholderText="per node, e.g. 60G")
        self.time = QLineEdit("" if job.unlimited else job.time,
                              placeholderText="e.g. 12:00:00 or 2-00:00:00")
        self.unlimited = QCheckBox("No time limit")
        self.unlimited.setToolTip("Only where the partition and your QOS allow it: the job "
                                  "cannot start early in gaps (backfill), and a hung run keeps "
                                  "its nodes")
        self.unlimited.setChecked(job.unlimited)
        self.time.setEnabled(not job.unlimited)
        self.unlimited.toggled.connect(lambda on: self.time.setEnabled(not on))
        self.qos = QComboBox(editable=True)
        self.qos.lineEdit().setPlaceholderText(f"the profile's ({profile.qos})" if profile.qos
                                               else "none (the cluster's default)")
        self.qos.setToolTip("Another QOS than the profile's for this step, e.g. a short "
                            "interactive or debug one; the list: what the partition accepts")
        self._fill_qos(job.qos)
        self.account = QLineEdit(job.account, placeholderText=(
            f"the profile's ({profile.account})" if profile.account else "none"))
        time_row = QHBoxLayout()
        time_row.addWidget(self.time)
        time_row.addWidget(self.unlimited)
        r.addRow("Partition", self.partition)
        r.addRow("QOS", self.qos)
        r.addRow("", self.limits)
        r.addRow("Nodes", self.nodes)
        r.addRow("MPI tasks per node", self.tasks)
        r.addRow("Threads per task", self.cpus)
        r.addRow("Memory", self.memory)
        r.addRow("Time", time_row)
        r.addRow("Account", self.account)
        # Runs
        runs = QGroupBox("Runs (in order)")
        rl = QVBoxLayout(runs)
        self.runs = QTableWidget(0, len(RUN_COLUMNS))
        self.runs.setHorizontalHeaderLabels(RUN_COLUMNS)
        self.runs.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = self.runs.horizontalHeader()
        for column, mode in enumerate((QHeaderView.Interactive, QHeaderView.Interactive,
                                       QHeaderView.Stretch, QHeaderView.Stretch,
                                       QHeaderView.ResizeToContents)):
            header.setSectionResizeMode(column, mode)
        self.runs.setColumnWidth(0, 130)
        self.runs.setColumnWidth(1, 100)
        self.runs.setMinimumHeight(140)
        rl.addWidget(self.runs)
        row = QHBoxLayout()
        for label, slot in (("Add run", lambda: self.add_run(None)), ("Remove", self._remove_run),
                            ("Up", lambda: self._move_run(-1)), ("Down", lambda: self._move_run(1))):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        self.edit_input = QPushButton("Edit input…")
        self.edit_input.setToolTip("The selected run's input: pw.x (structure, settings, k-points, "
                                   "pseudopotentials) or yambo (runlevels, bands, cutoffs, solvers), "
                                   "checked as you go")
        self.edit_input.clicked.connect(self.edit_run_input)
        self.refresher = None            # set by the Project tab when connected: (step, input) -> Refresh
        row.addWidget(self.edit_input)
        rl.addLayout(row)
        form.addWidget(runs)
        form.addWidget(resources)

        # Links and lines
        extra = QGroupBox("Before the runs")
        e = QFormLayout(extra)
        self.links = QTableWidget(0, 2)
        self.links.setHorizontalHeaderLabels(["Link", "Target"])
        self.links.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.links.setMaximumHeight(110)
        for name, target in job.links.items():
            self._add_link(name, target)
        link_row = QHBoxLayout()
        add_link = QPushButton("Add link")
        add_link.clicked.connect(lambda: self._add_link("SAVE", "../"))
        remove_link = QPushButton("Remove")
        remove_link.clicked.connect(lambda: [self.links.removeRow(i.row())
                                             for i in reversed(self.links.selectedIndexes())]
                                    and self.schedule())
        link_row.addWidget(add_link)
        link_row.addWidget(remove_link)
        link_row.addStretch()
        self.before = QPlainTextEdit("\n".join(job.before), maximumHeight=60,
                                     placeholderText="Shell lines before the runs")
        self.after_lines = QPlainTextEdit("\n".join(job.after), maximumHeight=60,
                                          placeholderText="Shell lines after the runs")
        e.addRow("Links", self.links)
        e.addRow("", link_row)
        e.addRow("Lines before", self.before)
        e.addRow("Lines after", self.after_lines)
        form.addWidget(extra)
        form.addStretch()

        # The script and the checks
        right = QSplitter(Qt.Vertical)
        self.preview = QPlainTextEdit(readOnly=True)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        right.addWidget(self.preview)
        checks = QWidget()
        cl = QVBoxLayout(checks)
        cl.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel()
        self.problems = QListWidget(wordWrap=True)
        cl.addWidget(self.summary)
        cl.addWidget(self.problems)
        right.addWidget(checks)
        right.setSizes([420, 220])
        split.addWidget(right)
        split.setSizes([700, 540])

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Save" if step else "Add step")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)

        for run in job.runs:
            self.add_run(run)
        if not job.runs and step is None:
            self.add_run(None)
        for w in (self.name, self.memory, self.time, self.account):
            w.textChanged.connect(self.schedule)
        self.partition.currentTextChanged.connect(self.schedule)
        self.partition.currentTextChanged.connect(lambda _: self._fill_qos(self.qos.currentText(),
                                                                           suggest=True))
        self.qos.currentTextChanged.connect(self.schedule)
        for w in (self.nodes, self.tasks, self.cpus):
            w.valueChanged.connect(self.schedule)
        self.unlimited.toggled.connect(self.schedule)
        self.links.itemChanged.connect(self.schedule)
        for w in (self.before, self.after_lines):
            w.textChanged.connect(self.schedule)
        self.after.itemChanged.connect(self.schedule)
        self.update_preview()

    def _fill_qos(self, current: str, suggest: bool = False):
        """The QOS the chosen partition accepts, keeping what was typed; with
        `suggest`, the partition's only QOS when it does not take the
        profile's (Tirant's interactive: only the interactive QOS)."""
        partition = self.profile.partitions.get(self.partition.currentText().strip())
        if suggest and partition is not None and partition.qos:
            if current and current not in partition.qos and self.profile.qos in partition.qos:
                current = ""                     # back to the profile's, which fits
            elif (current or self.profile.qos) not in partition.qos and len(partition.qos) == 1:
                current = next(iter(partition.qos))
        self.qos.blockSignals(True)
        self.qos.clear()
        self.qos.addItems([""] + list(partition.qos if partition is not None else []))
        self.qos.setCurrentText(current)
        self.qos.blockSignals(False)

    # -- runs ----------------------------------------------------------------------------

    def _choices(self) -> list[tuple[str, str]]:
        choices = [(name, key) for name, prog in self.profile.programs.items()
                   for key in prog.executables]
        for pair in DEFAULT_RUNS:
            if pair[0] not in self.profile.programs and pair not in choices:
                choices.append(pair)
        return choices

    def _input_names(self) -> list[str]:
        return [self.inputs.item(i).text().rstrip("/") for i in range(self.inputs.count())]

    def add_run(self, run: Run | None):
        run = run or Run()
        r = self.runs.rowCount()
        self.runs.insertRow(r)
        program = QComboBox()
        for name, key in self._choices():
            exe = self.profile.programs[name].executables.get(key) if name in self.profile.programs \
                else DEFAULT_EXECUTABLES.get(key, key)
            program.addItem(f"{key} ({name}: {exe})", (name, key))
        index = program.findData((run.program, run.executable))
        if index < 0:
            program.addItem(f"{run.executable} ({run.program})", (run.program, run.executable))
            index = program.count() - 1
        program.setCurrentIndex(index)
        source = QComboBox(editable=True)
        source.addItems([""] + self._input_names())
        source.setCurrentText(run.input)
        args = QLineEdit(run.args, placeholderText="e.g. -nk 4")
        jobs = QLineEdit(",".join(run.jobs), placeholderText="e.g. diago,../04-gw/gw")
        mpi = QCheckBox()
        mpi.setChecked(run.mpi)
        for c, w in enumerate((program, source, args, jobs, mpi)):
            self.runs.setCellWidget(r, c, w)
        program.currentIndexChanged.connect(self.schedule)
        source.currentTextChanged.connect(self.schedule)
        args.textChanged.connect(self.schedule)
        jobs.textChanged.connect(self.schedule)
        mpi.toggled.connect(self.schedule)
        self.schedule()

    def _run_at(self, r: int) -> Run:
        program, executable = self.runs.cellWidget(r, 0).currentData()
        return Run(program=program, executable=executable,
                   input=self.runs.cellWidget(r, 1).currentText().strip(),
                   args=self.runs.cellWidget(r, 2).text().strip(),
                   jobs=[j.strip() for j in self.runs.cellWidget(r, 3).text().split(",") if j.strip()],
                   mpi=self.runs.cellWidget(r, 4).isChecked())

    # -- pw.x inputs ----------------------------------------------------------------------

    def _pw_row(self) -> int | None:
        """The selected run, or the only pw.x run."""
        return self._input_row("pw")

    def _input_row(self, executable=None) -> int | None:
        """The selected run, or the only run with an input (of `executable`)."""
        rows = sorted({i.row() for i in self.runs.selectedIndexes()})
        if len(rows) == 1:
            return rows[0]
        found = [r for r in range(self.runs.rowCount())
                 if (self._run_at(r).executable == executable if executable else
                     self._run_at(r).executable in ("pw", "yambo"))]
        return found[0] if len(found) == 1 else None

    def edit_run_input(self):
        r = self._input_row()
        if r is not None and self._run_at(r).executable == "yambo":
            return self.edit_yambo_input(r)
        return self.edit_pw_input()

    def edit_yambo_input(self, r: int):
        from .asyncqt import launch
        name = self._run_at(r).input
        if not name:
            taken = set(self._input_names())
            name = next(n for n in ("gw.in", "bse.in", "yambo.in", *(f"yambo{i}.in" for i in range(2, 99)))
                        if n not in taken)
        return launch(self, self._edit_yambo(r, name), "yambo input")

    async def _edit_yambo(self, r: int, name: str, editor=None):
        from ..core.parallel import yambo_version
        from .asyncqt import ask
        from .yambo_editor import YamboEditor
        if editor is None:
            program = self.profile.programs.get("yambo")
            run, spec = self._run_at(r), self.spec()
            refresher = None
            if self.refresher is not None and self.step is not None:
                step = self.step
                refresher = lambda inp: self.refresher(step, inp)          # noqa: E731
            numbers = (None, None)
            if self.step is not None:
                from ..core.chain import bse_numbers
                numbers = bse_numbers(self.project, self.step)
            editor = YamboEditor(self, self.input_text(name),
                                 yambo_version(program.modules) if program else None,
                                 spec.tasks if run.mpi else 1, spec.cpus_per_task, refresher,
                                 title=f"yambo input: {name}", save_numbers=numbers)
        self.yambo_editor = editor              # for the tests
        if editor.result() != QDialog.Accepted and await ask(editor) != QDialog.Accepted:
            return
        self.set_input_text(name, editor.text())
        self.runs.cellWidget(r, 1).setCurrentText(name)

    def input_text(self, name: str) -> str | None:
        """An input's text as it stands: edited here, added here, or in the step's folder."""
        for i in range(self.inputs.count()):
            item = self.inputs.item(i)
            if item.text() == name and item.flags() & Qt.ItemIsSelectable:
                path = Path(item.data(Qt.UserRole))
                return path.read_text(encoding="utf-8") if path.is_file() else None
        path = self.folder() / name
        try:
            return path.read_text(encoding="utf-8") if path.is_file() else None
        except (OSError, UnicodeDecodeError):
            return None

    def edit_pw_input(self):
        from .asyncqt import launch
        r = self._pw_row()
        if r is None or self._run_at(r).executable != "pw":
            self.summary.setText("Select a pw.x or yambo run to edit its input.")
            return
        name = self._run_at(r).input
        if not name:
            taken = set(self._input_names())
            name = next(n for n in ("scf.in", "nscf.in", "bands.in", "pw.in", *(f"pw{i}.in" for i in range(2, 99)))
                        if n not in taken)
        return launch(self, self._edit_pw(r, name), "pw.x input")

    async def _edit_pw(self, r: int, name: str, editor=None):
        from .asyncqt import ask
        from .pw_editor import PwEditor
        editor = editor or PwEditor(self, self.input_text(name), self.folder(), self.profile.pseudos,
                                    title=f"pw.x input: {name}")
        self.pw_editor = editor                  # for the tests
        if editor.result() != QDialog.Accepted and await ask(editor) != QDialog.Accepted:
            return
        self.set_input_text(name, editor.text())
        self.runs.cellWidget(r, 1).setCurrentText(name)

    def set_input_text(self, name: str, text: str):
        """An input written here: copied into the step's folder when the step is saved."""
        import tempfile
        if getattr(self, "_staging", None) is None:
            self._staging = tempfile.TemporaryDirectory(prefix="vwf-inputs-")
        path = Path(self._staging.name) / name
        path.write_text(text, encoding="utf-8")
        for i in reversed(range(self.inputs.count())):
            item = self.inputs.item(i)
            if item.text() == name and item.flags() & Qt.ItemIsSelectable:
                self.inputs.takeItem(i)
        self.add_input(str(path))

    def _remove_run(self):
        for r in sorted({i.row() for i in self.runs.selectedIndexes()}, reverse=True):
            self.runs.removeRow(r)
        self.schedule()

    def _move_run(self, delta: int):
        rows = sorted({i.row() for i in self.runs.selectedIndexes()})
        if len(rows) != 1:
            return
        r, t = rows[0], rows[0] + delta
        if not 0 <= t < self.runs.rowCount():
            return
        runs = [self._run_at(i) for i in range(self.runs.rowCount())]
        runs[r], runs[t] = runs[t], runs[r]
        self.runs.setRowCount(0)
        for run in runs:
            self.add_run(run)
        self.runs.selectRow(t)

    # -- inputs and links ----------------------------------------------------------------

    def _add_files(self):
        for p in QFileDialog.getOpenFileNames(self, "Input files")[0]:
            self.add_input(p)

    def _add_folder(self):
        p = QFileDialog.getExistingDirectory(self, "Input folder")
        if p:
            self.add_input(p)

    def add_input(self, path: str):
        item = QListWidgetItem(Path(path).name + ("/" if Path(path).is_dir() else ""))
        item.setData(Qt.UserRole, str(path))
        item.setToolTip(str(path))
        self.inputs.addItem(item)
        for r in range(self.runs.rowCount()):
            box = self.runs.cellWidget(r, 1)
            if box.findText(Path(path).name) < 0:
                box.addItem(Path(path).name)
        self.schedule()

    def _remove_inputs(self):
        for item in self.inputs.selectedItems():
            self.inputs.takeItem(self.inputs.row(item))
        self.schedule()

    def _add_link(self, name: str, target: str):
        r = self.links.rowCount()
        self.links.insertRow(r)
        self.links.setItem(r, 0, QTableWidgetItem(name))
        self.links.setItem(r, 1, QTableWidgetItem(target))
        self.schedule()

    def new_inputs(self) -> list[str]:
        """Files and folders to copy into the step (not those already there)."""
        return [self.inputs.item(i).data(Qt.UserRole) for i in range(self.inputs.count())
                if self.inputs.item(i).flags() & Qt.ItemIsSelectable]

    # -- the description, the script and the checks ---------------------------------------

    def spec(self) -> JobSpec:
        links = {}
        for r in range(self.links.rowCount()):
            name, target = self.links.item(r, 0), self.links.item(r, 1)
            if name and name.text().strip():
                links[name.text().strip()] = target.text().strip() if target else ""
        lines = lambda w: [x for x in w.toPlainText().splitlines() if x.strip()]  # noqa: E731
        return JobSpec(partition=self.partition.currentText().strip(), nodes=self.nodes.value(),
                       tasks_per_node=self.tasks.value(), cpus_per_task=self.cpus.value(),
                       memory=self.memory.text().strip(),
                       time="UNLIMITED" if self.unlimited.isChecked() else self.time.text().strip(),
                       qos=self.qos.currentText().strip(), account=self.account.text().strip(),
                       links=links, before=lines(self.before), after=lines(self.after_lines),
                       runs=[self._run_at(r) for r in range(self.runs.rowCount())])

    def after_steps(self) -> list[str]:
        return [self.after.item(i).text() for i in range(self.after.count())
                if self.after.item(i).checkState() == Qt.Checked]

    def folder(self) -> Path:
        """The step's folder (for a new step, where it will be: it is not there yet)."""
        if self.step is not None:
            return self.step.folder
        return self.project.path / f"{len(self.project.steps) + 1:02d}-new"

    def schedule(self, *_):
        self._timer.start()

    def update_preview(self):
        spec = self.spec()
        partition = self.profile.partitions.get(spec.partition)
        self.limits.setText(partition_limits(partition, spec) if partition is not None else
                            "Not a partition of the profile: Discover lists them.")
        name = self.step.id if self.step else f"{len(self.project.steps) + 1:02d}-" \
            + (self.name.text().strip() or "step")
        folder = self.folder()
        self.preview.setPlainText(jobspec.render(spec, self.profile, name,
                                                 espresso_pseudo(folder, self.profile.pseudos)))
        inputs = {Path(p).name: Path(p) for p in self.new_inputs() if Path(p).is_file()}
        problems = jobspec.check(spec, self.profile, folder, None, inputs)
        self.problems.clear()
        for p in sorted(problems, key=lambda p: not p.error):
            item = QListWidgetItem(("Error: " if p.error else "Warning: ") + p.message)
            item.setForeground(QBrush(ERROR_COLOUR if p.error else WARNING_COLOUR))
            self.problems.addItem(item)
        errors = sum(p.error for p in problems)
        warnings = len(problems) - errors
        self.summary.setText(
            ("No problems found." if not problems else
             f"{errors} error{'s' if errors != 1 else ''}, {warnings} warning"
             f"{'s' if warnings != 1 else ''}.")
            + " Pseudopotentials and modules on the cluster, and the chain of steps, are "
              "checked at Submit.")
        self.errors = errors

    def _accept(self):
        self.update_preview()
        if self.step is None and not self.name.text().strip():
            self.summary.setText("Give the step a name.")
            return
        if not self.spec().runs:
            self.summary.setText("Add at least one run.")
            return
        self.accept()
