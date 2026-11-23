"""Editing a pw.x input: its structure (imported from a file, with its
symmetry), the main settings, k-points and pseudopotentials, with the input
as it will be written and the checks' results shown as you go. Variables the
form does not show are kept as they are."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox,
                               QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget, QHeaderView)

from ..core import input_checks, pseudos
from ..core.kpoints import Grid, KPointsError, band_path, grid_from_spacing, pw_kpoint_count
from ..core.qe_inputs import orientation_notes, set_kpoints, set_structure
from ..core.structure import Structure, StructureError, element
from ..yambopy_ext.qepy.pw_input import PwInputError, read_pw_input, write_pw_input
from .asyncqt import ask, launch, show_error
from .step_editor import ERROR_COLOUR, WARNING_COLOUR

NEW_INPUT = """&control
    calculation = 'scf'
    prefix      = 'pw'
    outdir      = './out'
/
&system
/
&electrons
/
"""

_IBRAV = [("Keep as written", "keep"), ("ibrav = 0 (CELL_PARAMETERS)", 0),
          ("The lattice's own ibrav", "auto")]


class PwEditor(QDialog):
    """A pw.x input: `text` (None: a new one); `step_folder` places it in a
    project (its pseudos/ folder, the checks). After it is accepted: `text()`."""

    def __init__(self, parent, text: str | None, step_folder: Path, library_dir: str = "",
                 title: str = "pw.x input"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1200, 760)
        self.step_folder = Path(step_folder)
        self.library_dir = library_dir           # the profile's on the cluster (for the checks)
        self.library = None                      # PseudoDojo's library (None: the default one)
        try:
            self.pw = read_pw_input(text if text is not None else NEW_INPUT)
        except PwInputError as e:
            show_error(self, title, f"The input cannot be read ({e}): starting a new one.")
            self.pw = read_pw_input(NEW_INPUT)
        self.imported: Structure | None = None   # a structure from a file, not yet in the input
        self.notes: list[str] = []
        self.cutoffs = None
        self._loading = False
        self._timer = QTimer(self, singleShot=True, interval=250)
        self._timer.timeout.connect(self.refresh)

        outer = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        outer.addWidget(split)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._structure_tab(), "Structure")
        self.tabs.addTab(self._settings_tab(), "Settings")
        self.tabs.addTab(self._kpoints_tab(), "k-points")
        self.tabs.addTab(self._pseudos_tab(), "Pseudopotentials")
        split.addWidget(self.tabs)

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
        cl.addWidget(self.summary)
        cl.addWidget(self.problems)
        right.addWidget(checks)
        right.setSizes([480, 200])
        split.addWidget(right)
        split.setSizes([620, 580])

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self.load_form()
        self.refresh()

    # -- the tabs ------------------------------------------------------------------------

    def _structure_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        row = QHBoxLayout()
        load = QPushButton("Import structure…")
        load.setToolTip("CIF, POSCAR, XYZ with a cell, a pw.x input or output, and what else "
                        "ASE reads")
        load.clicked.connect(self._browse_structure)
        self.primitive = QPushButton("Primitive cell")
        self.primitive.setToolTip("spglib's primitive cell, idealised (noise removed)")
        self.primitive.clicked.connect(lambda: self._replace_structure(self.structure().primitive()))
        self.turn = QPushButton("Turn for pw.x")
        self.turn.setToolTip("The same cell turned to QE's axes, so that pw.x finds every "
                             "symmetry operation (crystal coordinates do not change)")
        self.turn.clicked.connect(lambda: self._replace_structure(self.structure().oriented_for_pw()))
        for b in (load, self.primitive, self.turn):
            row.addWidget(b)
        row.addStretch()
        self.ibrav = QComboBox()
        for text, value in _IBRAV:
            self.ibrav.addItem(text, value)
        self.ibrav.setToolTip("With the lattice's own ibrav, QE builds the cell itself (exact, "
                              "in its orientation); monoclinic and triclinic cells stay at 0")
        self.ibrav.currentIndexChanged.connect(self._ibrav_changed)
        self.structure_info = QLabel(wordWrap=True)
        self.structure_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.atoms = QTableWidget(0, 4)
        self.atoms.setHorizontalHeaderLabels(["Species", "x", "y", "z"])
        self.atoms.setEditTriggers(QTableWidget.NoEditTriggers)
        self.atoms.horizontalHeader().setStretchLastSection(True)
        self.atoms.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.structure_notes = QLabel(wordWrap=True)
        f.addRow(row)
        f.addRow("Cell", self.ibrav)
        f.addRow(self.structure_info)
        f.addRow("Atoms (crystal)", self.atoms)
        f.addRow(self.structure_notes)
        return w

    def _settings_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.fields = {}
        combo = lambda items: (lambda c: (c.addItems(items), c)[1])(QComboBox())   # noqa: E731
        specs = [
            ("control", "calculation", combo(["scf", "nscf", "bands"]), "Calculation"),
            ("control", "prefix", QLineEdit(), "prefix"),
            ("control", "outdir", QLineEdit(placeholderText="./ (pw.x's default)"), "outdir"),
            ("system", "ecutwfc", self._cutoff(), "ecutwfc (Ry)"),
            ("system", "ecutrho", self._cutoff(), "ecutrho (Ry)"),
            ("system", "nbnd", QSpinBox(minimum=0, maximum=100000, specialValueText="pw.x's default"),
             "nbnd"),
            ("system", "occupations", combo(["", "fixed", "smearing", "tetrahedra"]), "occupations"),
            ("system", "smearing", combo(["", "gaussian", "mv", "mp", "fd"]), "smearing"),
            ("system", "degauss", QDoubleSpinBox(decimals=4, maximum=1.0, singleStep=0.005,
                                                 specialValueText="unset"), "degauss (Ry)"),
            ("system", "nspin", combo(["", "1", "2"]), "nspin"),
            ("system", "noncolin", QCheckBox(), "noncolin"),
            ("system", "lspinorb", QCheckBox(), "lspinorb (spin–orbit)"),
            ("system", "force_symmorphic", QCheckBox(), "force_symmorphic (for Yambo)"),
            ("electrons", "conv_thr", QLineEdit(placeholderText="1e-6 (pw.x's default)"), "conv_thr"),
        ]
        for namelist, name, widget, label in specs:
            self.fields[(namelist, name)] = widget
            f.addRow(label, widget)
            signal = (widget.currentTextChanged if isinstance(widget, QComboBox) else
                      widget.toggled if isinstance(widget, QCheckBox) else
                      widget.valueChanged if isinstance(widget, (QSpinBox, QDoubleSpinBox)) else
                      widget.textChanged)
            signal.connect(lambda *_, n=namelist, k=name: self._field_changed(n, k))
        f.addRow(QLabel("Other variables are kept as they are in the input.", wordWrap=True))
        return w

    @staticmethod
    def _cutoff() -> QDoubleSpinBox:
        return QDoubleSpinBox(decimals=1, maximum=10000.0, singleStep=5.0, specialValueText="unset")

    def _kpoints_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.k_mode = QComboBox()
        self.k_mode.addItem("As written", "keep")
        self.k_mode.addItem("Grid (K_POINTS automatic)", "grid")
        self.k_mode.addItem("Band path (K_POINTS crystal_b)", "path")
        self.k_mode.currentIndexChanged.connect(self._kpoints_changed)
        self.spacing = QDoubleSpinBox(decimals=3, minimum=0.0, maximum=5.0, singleStep=0.01, value=0.2)
        self.spacing.setToolTip("At most this step between k-points along each reciprocal "
                                "vector (Å⁻¹, 2π included, as ASE's kspacing)")
        from_spacing = QPushButton("Grid from spacing")
        from_spacing.clicked.connect(self._grid_from_spacing)
        srow = QHBoxLayout()
        srow.addWidget(self.spacing)
        srow.addWidget(from_spacing)
        self.grid = [QSpinBox(minimum=1, maximum=200, value=4) for _ in range(3)]
        self.shift = QCheckBox("Shifted by half a step")
        self.shift.setToolTip("Monkhorst–Pack shift; Yambo needs an unshifted grid")
        grow = QHBoxLayout()
        for b in self.grid:
            grow.addWidget(b)
            b.valueChanged.connect(self._kpoints_changed)
        grow.addWidget(self.shift)
        self.shift.toggled.connect(self._kpoints_changed)
        self.path = QLineEdit(placeholderText="the lattice's standard path, e.g. GXWKGLUWLK,UX")
        self.path.editingFinished.connect(self._kpoints_changed)
        self.density = QDoubleSpinBox(decimals=1, minimum=1.0, maximum=500.0, value=20.0)
        self.density.setToolTip("k-points per Å⁻¹ (2π included) along the path")
        self.density.valueChanged.connect(self._kpoints_changed)
        self.k_info = QLabel(wordWrap=True)
        self._grid_widgets = [self.spacing, from_spacing, *self.grid, self.shift]
        self._path_widgets = [self.path, self.density]
        self.k_mode.currentIndexChanged.connect(self._enable_kpoints)
        f.addRow("k-points", self.k_mode)
        f.addRow("Spacing (Å⁻¹)", srow)
        f.addRow("Grid", grow)
        f.addRow("Path", self.path)
        f.addRow("Density", self.density)
        f.addRow(self.k_info)
        return w

    def _enable_kpoints(self, *_):
        mode = self.k_mode.currentData()
        for w in self._grid_widgets:
            w.setEnabled(mode != "path")         # "As written": a grid may still be made
        for w in self._path_widgets:
            w.setEnabled(mode == "path")

    def _pseudos_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.species = QTableWidget(0, 3)
        self.species.setHorizontalHeaderLabels(["Species", "Mass", "File"])
        self.species.horizontalHeader().setStretchLastSection(True)
        self.species.itemChanged.connect(self._species_edited)
        v.addWidget(self.species)
        row = QHBoxLayout()
        dojo = QPushButton("PseudoDojo…")
        dojo.setToolTip("Norm-conserving pseudopotentials from PseudoDojo's tables, into the "
                        "project's pseudos/ folder")
        dojo.clicked.connect(lambda: launch(self, self.pick_pseudos(), "PseudoDojo"))
        self.use_cutoffs = QPushButton("Use suggested cutoffs")
        self.use_cutoffs.setEnabled(False)
        self.use_cutoffs.clicked.connect(self._use_cutoffs)
        row.addWidget(dojo)
        row.addWidget(self.use_cutoffs)
        row.addStretch()
        v.addLayout(row)
        self.pseudo_info = QLabel(wordWrap=True)
        v.addWidget(self.pseudo_info)
        v.addStretch()
        return w

    # -- from the input to the form -------------------------------------------------------

    def load_form(self):
        """The form's fields from the input."""
        self._loading = True
        try:
            for (namelist, name), widget in self.fields.items():
                value = self.pw.get(namelist, name)
                if isinstance(widget, QComboBox):
                    text = "" if value is None else str(value)
                    if widget.findText(text) < 0:
                        widget.addItem(text)
                    widget.setCurrentText(text)
                elif isinstance(widget, QCheckBox):
                    widget.setChecked(value is True)
                elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    widget.setValue(value if isinstance(value, (int, float)) and not isinstance(value, bool)
                                    else widget.minimum())
                else:
                    widget.setText("" if value is None else str(value))
            try:
                k = self.pw.k_points()
            except PwInputError:
                k = None
            if k is not None and k.kind == "automatic":
                for box, n in zip(self.grid, k.grid):
                    box.setValue(n)
                self.shift.setChecked(any(k.shift))
            self.k_mode.setCurrentIndex(0)
            self._enable_kpoints()
            self._load_species()
        finally:
            self._loading = False

    def _load_species(self):
        self._loading, was = True, self._loading
        card = self.pw.cards.get("ATOMIC_SPECIES")
        lines = [line.split() for line in (card.lines if card else [])]
        self.species.setRowCount(0)
        for words in lines:
            r = self.species.rowCount()
            self.species.insertRow(r)
            for c in range(3):
                item = QTableWidgetItem(words[c] if c < len(words) else "")
                if c < 2:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.species.setItem(r, c, item)
        self._loading = was

    # -- from the form to the input -------------------------------------------------------

    def _field_changed(self, namelist, name):
        if self._loading:
            return
        widget = self.fields[(namelist, name)]
        if isinstance(widget, QComboBox):
            text = widget.currentText().strip()
            value = (int(text) if name == "nspin" else text) if text else None
        elif isinstance(widget, QCheckBox):
            value = True if widget.isChecked() else None
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            value = widget.value() if widget.value() != widget.minimum() else None
        else:
            text = widget.text().strip()
            value = None
            if text:
                try:
                    value = float(text.replace("d", "e").replace("D", "e")) if name == "conv_thr" else text
                except ValueError:
                    value = text
        self.pw.set(namelist, name, value)
        self.schedule()

    def structure(self) -> Structure:
        """The structure being edited: one imported, else the input's."""
        if self.imported is not None:
            return self.imported
        return Structure.from_pw(self.pw)

    def _browse_structure(self):
        path, _ = QFileDialog.getOpenFileName(self, "Structure file")
        if path:
            self.import_structure(path)

    def import_structure(self, path):
        try:
            s = Structure.read(path)
        except StructureError as e:
            show_error(self, "Import structure", str(e))
            return
        if self.ibrav.currentData() == "keep":
            self.ibrav.setCurrentIndex(2)        # a new structure: the lattice's own ibrav
        self._replace_structure(s)

    def _replace_structure(self, s: Structure):
        self.imported = s
        self._apply_structure()

    def _ibrav_changed(self):
        if self._loading:
            return
        if self.ibrav.currentData() != "keep" and self.imported is None:
            try:
                self.imported = Structure.from_pw(self.pw)
            except StructureError as e:
                self.structure_notes.setText(str(e))
                return
        self._apply_structure()

    def _apply_structure(self):
        if self.imported is None:
            return
        choice = self.ibrav.currentData()
        try:
            self.notes = set_structure(self.pw, self.imported,
                                       ibrav=0 if choice == "keep" else choice)
        except StructureError as e:
            self.notes = [str(e)]
        self._load_species()
        if self.k_mode.currentData() == "keep" and self.pw.cards.get("K_POINTS") is None:
            self._grid_from_spacing()
        self.schedule()

    def _grid_from_spacing(self):
        try:
            grid = grid_from_spacing(self.structure(), self.spacing.value())
        except (StructureError, KPointsError) as e:
            self.k_info.setText(str(e))
            return
        self._loading = True
        for box, n in zip(self.grid, grid.n):
            box.setValue(n)
        self._loading = False
        self.k_mode.setCurrentIndex(1)
        self._kpoints_changed()

    def _kpoints_changed(self, *_):
        if self._loading:
            return
        mode = self.k_mode.currentData()
        try:
            if mode == "grid":
                shift = (1, 1, 1) if self.shift.isChecked() else (0, 0, 0)
                set_kpoints(self.pw, Grid(tuple(b.value() for b in self.grid), shift))
            elif mode == "path":
                set_kpoints(self.pw, band_path(self.structure(), self.path.text().strip() or None,
                                               self.density.value()))
        except (StructureError, KPointsError) as e:
            self.k_info.setText(str(e))
            return
        self.schedule()

    def _species_edited(self, item):
        if self._loading or item.column() != 2:
            return
        self._write_species()

    def _write_species(self):
        lines = []
        for r in range(self.species.rowCount()):
            cells = [self.species.item(r, c).text().strip() if self.species.item(r, c) else ""
                     for c in range(3)]
            lines.append(" ".join(cells))
        self.pw.set_card("ATOMIC_SPECIES", "", lines)
        self.schedule()

    async def pick_pseudos(self):
        from .pseudo_picker import PseudoPicker
        rows = [(r, self.species.item(r, 0).text()) for r in range(self.species.rowCount())]
        if not rows:
            self.pseudo_info.setText("No species yet: import a structure first.")
            return
        elements = [element(label) for _, label in rows]
        relativistic = self.pw.get("system", "lspinorb", False) is True
        picker = PseudoPicker(self, elements, pseudos.project_pseudos(self.step_folder),
                              self.library, relativistic=relativistic)
        if await ask(picker) != QDialog.Accepted:
            return
        self.apply_pseudos(picker.files, picker.cutoffs)

    def apply_pseudos(self, files: dict, cutoffs):
        """Files chosen for the elements ({element: name}), with the cutoffs suggested."""
        self._loading = True
        for r in range(self.species.rowCount()):
            label = self.species.item(r, 0).text()
            name = files.get(element(label))
            if name:
                self.species.item(r, 2).setText(name)
        self._loading = False
        self._write_species()
        self.cutoffs = cutoffs
        if cutoffs is not None and cutoffs.ecutwfc is not None:
            self.use_cutoffs.setEnabled(True)
            self.pseudo_info.setText(f"Suggested: ecutwfc = {cutoffs.ecutwfc:g} Ry, ecutrho = "
                                     f"{cutoffs.ecutrho:g} Ry (PseudoDojo's hints).")
            if self.pw.get("system", "ecutwfc") is None:
                self._use_cutoffs()

    def _use_cutoffs(self):
        if self.cutoffs is None or self.cutoffs.ecutwfc is None:
            return
        self.fields[("system", "ecutwfc")].setValue(self.cutoffs.ecutwfc)
        self.fields[("system", "ecutrho")].setValue(self.cutoffs.ecutrho)

    # -- the input and its checks ------------------------------------------------------------

    def schedule(self, *_):
        self._timer.start()

    def text(self) -> str:
        return write_pw_input(self.pw)

    def refresh(self):
        try:
            text = self.text()
        except PwInputError as e:
            self.preview.setPlainText(f"(cannot be written: {e})")
            return
        self.preview.setPlainText(text)
        self._show_structure()
        problems = input_checks.check_pw_input(text, self.pw)
        files = pseudos.pseudo_files(self.pw, self.step_folder, self.library_dir)
        headers = {f.remote: pseudos.read_local_header(f.local) for f in files if f.local is not None}
        problems += input_checks.check_pseudopotentials(
            self.pw, files, headers, hints=pseudos.known_hints(files, self.step_folder))
        self.problems.clear()
        for p in sorted(problems, key=lambda p: not p.error):
            item = QListWidgetItem(("Error: " if p.error else "Warning: ") + p.message)
            item.setForeground(QBrush(ERROR_COLOUR if p.error else WARNING_COLOUR))
            self.problems.addItem(item)
        errors = sum(p.error for p in problems)
        self.summary.setText("No problems found." if not problems else
                             f"{errors} error{'s' if errors != 1 else ''}, {len(problems) - errors} "
                             f"warning{'s' if len(problems) - errors != 1 else ''}.")
        count = pw_kpoint_count(self.pw)
        nspin = self.pw.get("system", "nspin", 1)
        self.k_info.setText("" if count is None else
                            f"pw.x will compute {count} k-point{'s' if count != 1 else ''}"
                            + (" (× 2 spins)" if nspin == 2 else "") + ".")

    def _show_structure(self):
        try:
            s = Structure.from_pw(self.pw)
        except StructureError as e:
            self.structure_info.setText(f"No structure yet ({e}).")
            self.primitive.setEnabled(False)
            self.turn.setEnabled(False)
            self.atoms.setRowCount(0)
            return
        sym = s.symmetry()
        found = len(s.pw_operations())
        self.structure_info.setText(
            f"{s.formula}: space group {sym.international} ({sym.number})"
            + (f", {sym.lattice} lattice" if sym.lattice else "")
            + f"; {len(s.symbols)} atoms, {s.volume:.3f} Å³; pw.x finds {found} of its "
              f"{sym.operations} symmetry operations"
            + (f", {sum(1 for _, t in s.pw_operations() if any(abs(x) > 1e-6 for x in t))} with "
               "fractional translations" if not sym.symmorphic else "") + ".")
        self.primitive.setEnabled(True)
        self.turn.setEnabled(found < sym.operations and self.pw.get("system", "ibrav") == 0)
        self.atoms.setRowCount(len(s.symbols))
        for r, (kind, p) in enumerate(zip(s.kinds, s.positions)):
            for c, text in enumerate([kind] + [f"{x:.6f}" for x in p]):
                self.atoms.setItem(r, c, QTableWidgetItem(text))
        notes = list(self.notes)
        if self.pw.get("system", "ibrav") == 0:
            notes += [n for n in orientation_notes(s) if n not in notes]
        self.structure_notes.setText("\n".join(notes))
