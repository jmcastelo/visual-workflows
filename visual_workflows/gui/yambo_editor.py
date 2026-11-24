"""Editing a yambo input: its runlevels, the variables that matter as a form
(bands, cutoffs, solvers, QP energies), every other variable these runlevels
read as a table with yambo's descriptions, and the input and the checks'
results beside them. A refresh from the cluster's yambo, when connected,
tells what that build reads and would add."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
                               QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..core import input_checks, parallel
from ..core.yambo_inputs import large_bse_problems
from ..yambopy_ext.io.yambo_input import (YamboInputError, read_yambo_input, write_yambo_input)
from ..yambopy_ext.io.yambo_runlevels import input_names
from .asyncqt import launch
from .step_editor import ERROR_COLOUR, WARNING_COLOUR

RUNLEVELS = [("setup", "Setup (index the SAVE)"), ("HF_and_locXC", "Hartree–Fock and Vxc"),
             ("gw0", "GW"), ("ppa", "Plasmon-pole screening"), ("dyson", "Dyson equation"),
             ("el_el_corr", "Electron–electron correlation"), ("em1d", "Dynamical screening"),
             ("em1s", "Static screening"), ("optics", "Optics"), ("chi", "Response χ (IP, RPA)"),
             ("bse", "Bethe–Salpeter equation"), ("bss", "BSE solver"), ("dipoles", "Dipoles"),
             ("rim_cut", "Coulomb cutoff (RIM)")]
PRESETS = {"GW (plasmon pole)": ["HF_and_locXC", "gw0", "ppa", "el_el_corr", "dyson", "em1d"],
           "BSE": ["em1s", "optics", "bss", "bse", "dipoles"]}

# The form: name, kind, label
FORM = [("BndsRnXp", "range", "Response bands"), ("NGsBlkXp", "cutoff", "Response block size"),
        ("GbndRnge", "range", "Green's function bands"), ("QPkrange", "qp", "QP k-points and bands"),
        ("DysSolver", "choice:n,s,g", "Dyson solver (n: Newton)"),
        ("BndsRnXs", "range", "Static screening bands"), ("NGsBlkXs", "cutoff", "Static screening block size"),
        ("BSKmod", "choice:SEX,HF,Hartree,ALDA,IP", "BSE kernel"),
        ("BSSmod", "choice:d,h,s,i", "BSE solver (d: diagonalisation)"),
        ("BSEBands", "range", "BSE bands"), ("BSENGexx", "cutoff", "BSE exchange size"),
        ("KfnQPdb", "text", "QP energies from (E < …/ndb.QP)"), ("KfnQP_E", "scissor", "Scissor (eV)"),
        ("WRbsWF", "flag", "Write the excitons' wave functions")]
_FORM_NAMES = {name.lower() for name, _, _ in FORM}
_NEW_INPUT = "HF_and_locXC\ngw0\nppa\nel_el_corr\ndyson\nem1d\n"


class YamboEditor(QDialog):
    """A yambo input: `text` (None: a new GW one). `version` (major, minor),
    `tasks`, `threads`: for the names and the checks. `refresher`: an async
    function (YamboInput) -> Refresh, or None when not connected. After it is
    accepted: `text()`."""

    def __init__(self, parent, text: str | None, version=None, tasks: int = 1, threads: int = 1,
                 refresher=None, title: str = "yambo input", save_numbers=(None, None)):
        """`save_numbers`: the occupied bands and k-points of the zone of the
        SAVE the step uses (from its nscf), for the BSE's size."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1240, 780)
        self.version = ".".join(str(x) for x in version) if version else None
        self.version_tuple = tuple(version) if version else None
        self.tasks, self.threads, self.refresher = tasks, threads, refresher
        self.save_numbers = save_numbers
        self.refresh_result = None
        try:
            self.inp = read_yambo_input(text if text is not None else _NEW_INPUT)
        except YamboInputError:
            self.inp = read_yambo_input(_NEW_INPUT)
        self._loading = False
        self._timer = QTimer(self, singleShot=True, interval=250)
        self._timer.timeout.connect(self.refresh_view)

        outer = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        outer.addWidget(split)
        left = QWidget()
        lv = QVBoxLayout(left)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(left)
        split.addWidget(scroll)

        levels = QGroupBox("Runlevels")
        ll = QVBoxLayout(levels)
        presets = QHBoxLayout()
        for label, names in PRESETS.items():
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, n=names: self.set_runlevels(n))
            presets.addWidget(b)
        presets.addStretch()
        ll.addLayout(presets)
        self.runlevels = QListWidget(minimumHeight=170, maximumHeight=230)
        for name, text_ in RUNLEVELS:
            item = QListWidgetItem(f"{name} — {text_}")
            item.setData(Qt.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            self.runlevels.addItem(item)
        self.runlevels.itemChanged.connect(self._runlevel_changed)
        ll.addWidget(self.runlevels)
        lv.addWidget(levels)

        form_box = QGroupBox("Main variables")
        self.form = QFormLayout(form_box)
        self.fields = {}
        for name, kind, label in FORM:
            widget = self._field(name, kind)
            self.fields[name] = (kind, widget)
            self.form.addRow(f"{label} ({name})", widget)
        lv.addWidget(form_box)

        table_box = QGroupBox("Every variable these runlevels read (empty: yambo's default)")
        tl = QVBoxLayout(table_box)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Value", "Description"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setMinimumHeight(260)
        self.table.itemChanged.connect(self._table_changed)
        self.show_verbose = QCheckBox("Also the verbose ones (yambo's -V)")
        self.show_verbose.toggled.connect(lambda _: self.fill_table())
        tl.addWidget(self.show_verbose)
        tl.addWidget(self.table)
        lv.addWidget(table_box)

        right = QSplitter(Qt.Vertical)
        self.preview = QPlainTextEdit(readOnly=True)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        right.addWidget(self.preview)
        checks = QWidget()
        cl = QVBoxLayout(checks)
        cl.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel(wordWrap=True)
        self.problems = QListWidget(wordWrap=True)
        row = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh from the cluster's yambo")
        self.refresh_button.setToolTip("Ask the yambo the step will run for its own input for these "
                                       "runlevels, against the step's SAVE, and compare")
        self.refresh_button.setEnabled(refresher is not None)
        if refresher is None:
            self.refresh_button.setToolTip("For a step that exists (saved): its SAVE on the cluster")
        self.refresh_button.clicked.connect(lambda: launch(self, self.refresh_from_cluster(), "Refresh"))
        self.complete_button = QPushButton("Complete with yambo's values")
        self.complete_button.setEnabled(False)
        self.complete_button.clicked.connect(self.complete_from_refresh)
        row.addWidget(self.refresh_button)
        row.addWidget(self.complete_button)
        row.addStretch()
        self.refresh_report = QLabel(wordWrap=True)
        cl.addWidget(self.summary)
        cl.addWidget(self.problems)
        cl.addLayout(row)
        cl.addWidget(self.refresh_report)
        right.addWidget(checks)
        right.setSizes([480, 300])
        split.addWidget(right)
        split.setSizes([660, 580])

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self.load()

    # -- the form's widgets ------------------------------------------------------------------

    def _field(self, name, kind):
        if kind == "range":
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            w.boxes = [QSpinBox(minimum=0, maximum=100000, specialValueText="—") for _ in range(2)]
            for b in w.boxes:
                h.addWidget(b)
                b.valueChanged.connect(lambda *_, n=name: self._field_changed(n))
            return w
        if kind == "qp":
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            w.boxes = [QSpinBox(minimum=0, maximum=100000, specialValueText="—") for _ in range(4)]
            for b, tip in zip(w.boxes, ("first k", "last k", "first band", "last band")):
                b.setToolTip(tip)
                h.addWidget(b)
                b.valueChanged.connect(lambda *_, n=name: self._field_changed(n))
            return w
        if kind == "cutoff":
            w = QDoubleSpinBox(decimals=2, maximum=10000.0, singleStep=1.0, suffix=" Ry",
                               specialValueText="yambo's default")
            w.valueChanged.connect(lambda *_, n=name: self._field_changed(n))
            return w
        if kind.startswith("choice:"):
            w = QComboBox()
            w.addItems([""] + kind.split(":", 1)[1].split(","))
            w.currentTextChanged.connect(lambda *_, n=name: self._field_changed(n))
            return w
        if kind == "scissor":
            w = QDoubleSpinBox(decimals=3, minimum=-20.0, maximum=20.0, singleStep=0.05,
                               suffix=" eV", specialValueText="none")
            w.setMinimum(-20.0)
            w.setValue(-20.0)
            w.valueChanged.connect(lambda *_, n=name: self._field_changed(n))
            return w
        if kind == "flag":
            w = QCheckBox()
            w.toggled.connect(lambda *_, n=name: self._field_changed(n))
            return w
        w = QLineEdit()
        w.textChanged.connect(lambda *_, n=name: self._field_changed(n))
        return w

    # -- from the input to the view ---------------------------------------------------------

    def load(self):
        self._loading = True
        try:
            on = {f.lower() for f in self.inp.flags}
            for i in range(self.runlevels.count()):
                item = self.runlevels.item(i)
                item.setCheckState(Qt.Checked if item.data(Qt.UserRole).lower() in on else Qt.Unchecked)
            for name, (kind, w) in self.fields.items():
                self._load_field(name, kind, w)
        finally:
            self._loading = False
        self.refresh_view()

    def _load_field(self, name, kind, w):
        v, b = self.inp.variable(name), self.inp.block(name)
        if kind == "range" or kind == "qp":
            row = b.rows[0] if b and b.rows else []
            for i, box in enumerate(w.boxes):
                box.setValue(row[i] if i < len(row) and isinstance(row[i], int) else 0)
        elif kind == "cutoff":
            w.setValue(float(v.value) if v and isinstance(v.value, (int, float)) else 0.0)
        elif kind.startswith("choice:"):
            text = str(v.value) if v else ""
            if w.findText(text) < 0:
                w.addItem(text)
            w.setCurrentText(text)
        elif kind == "scissor":
            row = b.rows[0] if b and b.rows else []
            w.setValue(float(row[0]) if row else w.minimum())
        elif kind == "flag":
            w.setChecked(self.inp.has_flag(name))
        else:
            w.setText(str(v.value) if v else "")

    def names(self, verbose=True):
        values = {k: v.value for k, v in self.inp.variables.items()}
        return [n for n in input_names(self.inp.flags, values, self.version, mpi=self.tasks > 1)
                if verbose or not n.verbosity]

    def refresh_view(self):
        relevant = {str(n).lower() for n in self.names()}
        for i, (name, _, _) in enumerate(FORM):
            self.form.setRowVisible(i, name.lower() in relevant or self.inp.variable(name) is not None
                                    or self.inp.block(name) is not None)
        self.fill_table()
        try:
            text = self.text()
        except YamboInputError as e:
            self.preview.setPlainText(f"(cannot be written: {e})")
            return
        self.preview.setPlainText(text)
        inp = read_yambo_input(text)
        problems = (input_checks.check_yambo_input(inp, self.version_tuple)
                    + parallel.check_yambo(inp, self.tasks, self.threads, self.version_tuple)
                    + input_checks.check_for_visual_excitons(inp)
                    + large_bse_problems(inp, *self.save_numbers))
        self.problems.clear()
        for p in sorted(problems, key=lambda p: not p.error):
            item = QListWidgetItem(("Error: " if p.error else "Warning: ") + p.message)
            item.setForeground(QBrush(ERROR_COLOUR if p.error else WARNING_COLOUR))
            self.problems.addItem(item)
        errors = sum(p.error for p in problems)
        self.summary.setText("No problems found." if not problems else
                             f"{errors} error{'s' if errors != 1 else ''}, {len(problems) - errors} "
                             f"warning{'s' if len(problems) - errors != 1 else ''}.")

    def fill_table(self):
        self._loading, was = True, self._loading
        names = [n for n in self.names(verbose=self.show_verbose.isChecked())
                 if n.lower() not in _FORM_NAMES and not any(n.lower() == r.lower() for r, _ in RUNLEVELS)]
        listed = {n.lower() for n in names}
        others = [n for n in list(self.inp.variables) + list(self.inp.blocks)
                  if n.lower() not in listed and n.lower() not in _FORM_NAMES]
        known = {str(n).lower() for n in self.names()}
        self.table.setRowCount(0)
        for name in names + others:
            r = self.table.rowCount()
            self.table.insertRow(r)
            item = QTableWidgetItem(str(name))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            value = QTableWidgetItem(self._value_text(name))
            description = getattr(name, "text", "") or ("not read by this yambo for these runlevels"
                                                        if str(name).lower() not in known else "")
            if getattr(name, "verbosity", ""):
                description += f"  [-V {name.verbosity}]"
            desc = QTableWidgetItem(description)
            desc.setFlags(desc.flags() & ~Qt.ItemIsEditable)
            if self._value_text(name):
                font = QFont()
                font.setBold(True)
                item.setFont(font)
            if str(name).lower() not in known:
                item.setForeground(QBrush(WARNING_COLOUR))
            for c, x in enumerate((item, value, desc)):
                self.table.setItem(r, c, x)
        self.table.resizeColumnToContents(0)
        self._loading = was

    def _value_text(self, name):
        v, b = self.inp.variable(name), self.inp.block(name)
        if v is not None:
            value = f'"{v.value}"' if v.quoted else str(v.value)
            return f"{value} {v.unit}".strip()
        if b is not None:
            rows = " ; ".join(" | ".join(str(x) for x in row) for row in b.rows)
            return f"{rows} {b.unit}".strip() if b.unit else rows
        if self.inp.has_flag(name):
            return "on"
        return ""

    # -- from the view to the input ---------------------------------------------------------------

    def set_runlevels(self, names):
        on = {n.lower() for n in names}
        for r, _ in RUNLEVELS:
            self.inp.set_flag(r, r.lower() in on)
        self.load()

    def _runlevel_changed(self, item):
        if self._loading:
            return
        self.inp.set_flag(item.data(Qt.UserRole), item.checkState() == Qt.Checked)
        self.schedule()

    def _field_changed(self, name):
        if self._loading:
            return
        kind, w = self.fields[name]
        if kind in ("range", "qp"):
            values = [b.value() for b in w.boxes]
            self.inp.set_block(name, [values] if all(values) else None)
        elif kind == "cutoff":
            self.inp.set(name, w.value() if w.value() > 0 else None, "Ry")
        elif kind.startswith("choice:"):
            self.inp.set(name, w.currentText() or None)
        elif kind == "scissor":
            self.inp.set_block(name, None if w.value() == w.minimum() else [[w.value(), 1.0, 1.0]], "")
        elif kind == "flag":
            self.inp.set_flag(name, w.isChecked())
        else:
            self.inp.set(name, w.text().strip() or None)
        self.schedule()

    def _table_changed(self, item):
        if self._loading or item.column() != 1:
            return
        name = self.table.item(item.row(), 0).text()
        text = item.text().strip()
        try:
            self.set_text_value(name, text)
        except YamboInputError as e:
            self.summary.setText(f"{name}: {e}")
            return
        self.schedule()

    def set_text_value(self, name, text):
        """A value typed in the table: empty unsets it; `on`/`off` for a flag;
        a block's rows separated by ';' (`1 | 24`); else as in an input."""
        from ..yambopy_ext.io.yambo_variables import yambo_names
        flags = {f.lower() for f in yambo_names(self.version_tuple)["flags"]}
        if not text:
            self.inp.unset(name)
        elif name.lower() in flags and text in ("on", "off"):
            self.inp.set_flag(name, text == "on")
        elif "|" in text:
            rows = [r for r in text.split(";") if r.strip()]
            unit = ""
            last = rows[-1].rsplit("|", 1)
            if len(last) == 2 and last[1].strip():
                unit, rows[-1] = last[1].strip(), last[0] + "|"
            body = "\n".join(r if r.strip().endswith("|") else r + " |" for r in rows)
            block = read_yambo_input(f"% {name}\n{body}\n%\n").block(name)
            self.inp.set_block(name, block.rows, unit or block.unit)
        else:
            variable = read_yambo_input(f"{name}= {text}\n").variable(name)
            self.inp.set(name, variable.value, variable.unit, variable.quoted)

    # -- the refresh ------------------------------------------------------------------------------

    async def refresh_from_cluster(self):
        self.refresh_report.setText("Asking the cluster's yambo…")
        self.refresh_button.setEnabled(False)
        try:
            self.refresh_result = await self.refresher(self.inp)
        finally:
            self.refresh_button.setEnabled(True)
        self.refresh_report.setText("\n".join(self.refresh_result.report()))
        self.complete_button.setEnabled(bool(self.refresh_result.added))

    def complete_from_refresh(self):
        from ..core.yambo_refresh import complete
        if self.refresh_result is None:
            return
        self.inp = complete(self.inp, self.refresh_result)
        self.complete_button.setEnabled(False)
        self.load()

    # -- the input -------------------------------------------------------------------------------

    def schedule(self, *_):
        self._timer.start()

    def text(self) -> str:
        names = self.names()
        info = {str(n).lower(): n.text for n in names}
        return write_yambo_input(self.inp, order=[str(n) for n in names],
                                 describe=lambda n: info.get(n.lower(), ""))
