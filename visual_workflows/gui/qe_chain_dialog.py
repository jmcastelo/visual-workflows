"""New QE chain…: an scf input (made in the pw.x editor), and the steps
derived from it: an nscf with empty bands for Yambo, p2y, a band structure;
one set of resources for the pw.x steps."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSpinBox, QVBoxLayout)

from ..core.chain import occupied_bands_of
from ..core.jobspec import JobSpec
from ..core.kpoints import Grid, KPointsError, band_path, grid_from_spacing, pw_kpoint_count
from ..core.profile import ClusterProfile
from ..core.project import Project, _slug
from ..core.qe_chain import QeChain, chain_inputs, suggested_bands
from ..core.structure import Structure, StructureError
from ..yambopy_ext.qepy.pw_input import PwInputError, read_pw_input
from .asyncqt import ask, launch


class QeChainDialog(QDialog):
    def __init__(self, parent, project: Project, profile: ClusterProfile):
        super().__init__(parent)
        self.project, self.profile = project, profile
        self.setWindowTitle("New QE chain")
        self.resize(720, 640)
        self.scf_text: str | None = None
        layout = QVBoxLayout(self)

        scf = QGroupBox("scf")
        s = QFormLayout(scf)
        row = QHBoxLayout()
        self.edit_scf = QPushButton("Make the scf input…")
        self.edit_scf.clicked.connect(lambda: launch(self, self.edit_scf_input(), "scf input"))
        row.addWidget(self.edit_scf)
        row.addStretch()
        self.scf_info = QLabel("Structure, settings, k-points and pseudopotentials: the other "
                               "steps take them from the scf.", wordWrap=True)
        s.addRow(row)
        s.addRow(self.scf_info)
        layout.addWidget(scf)

        self.nscf = QGroupBox("nscf for Yambo (empty bands, an unshifted grid)", checkable=True)
        n = QFormLayout(self.nscf)
        self.grid = [QSpinBox(minimum=1, maximum=200, value=6) for _ in range(3)]
        grow = QHBoxLayout()
        for b in self.grid:
            grow.addWidget(b)
            b.valueChanged.connect(self.update_summary)
        self.nscf_nbnd = QSpinBox(minimum=0, maximum=100000, specialValueText="set it")
        self.nscf_nbnd.setToolTip("Bands for Yambo: occupied and empty (GW and BSE need many "
                                  "empty ones)")
        self.p2y = QCheckBox("Then p2y (makes Yambo's SAVE)")
        self.p2y.setChecked(True)
        n.addRow("Grid", grow)
        n.addRow("nbnd", self.nscf_nbnd)
        n.addRow(self.p2y)
        layout.addWidget(self.nscf)

        self.bands = QGroupBox("Band structure", checkable=True)
        b = QFormLayout(self.bands)
        self.path = QLineEdit(placeholderText="the lattice's standard path")
        self.density = QDoubleSpinBox(decimals=1, minimum=1.0, maximum=500.0, value=20.0)
        self.density.setToolTip("k-points per Å⁻¹ (2π included) along the path")
        self.bands_nbnd = QSpinBox(minimum=0, maximum=100000, specialValueText="pw.x's default")
        b.addRow("Path", self.path)
        b.addRow("Density", self.density)
        b.addRow("nbnd", self.bands_nbnd)
        layout.addWidget(self.bands)

        resources = QGroupBox("Resources of the pw.x steps (p2y: one task)")
        r = QFormLayout(resources)
        self.partition = QComboBox(editable=True)
        self.partition.addItems(list(profile.partitions))
        self.nodes = QSpinBox(minimum=1, maximum=100000, value=1)
        partition = next(iter(profile.partitions.values()), None)
        cores = (partition.cores_per_node // max(1, partition.threads_per_core)
                 if partition and partition.cores_per_node else 1)    # physical cores
        self.tasks = QSpinBox(minimum=1, maximum=100000, value=cores)
        self.memory = QLineEdit(placeholderText="per node, e.g. 60G")
        self.time = QLineEdit("12:00:00", placeholderText="e.g. 12:00:00")
        r.addRow("Partition", self.partition)
        r.addRow("Nodes", self.nodes)
        r.addRow("MPI tasks per node", self.tasks)
        r.addRow("Memory", self.memory)
        r.addRow("Time", self.time)
        layout.addWidget(resources)

        self.summary = QLabel(wordWrap=True)
        layout.addWidget(self.summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Add steps")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for w in (self.nscf, self.bands, self.p2y):
            w.toggled.connect(self.update_summary)
        self.nscf_nbnd.valueChanged.connect(self.update_summary)
        self.update_summary()

    # -- the scf --------------------------------------------------------------------------

    def scf_folder(self):
        return self.project.path / f"{len(self.project.steps) + 1:02d}-scf"

    async def edit_scf_input(self, editor=None):
        from .pw_editor import PwEditor
        editor = editor or PwEditor(self, self.scf_text, self.scf_folder(), self.profile.pseudos,
                                    title="pw.x input: scf")
        self.pw_editor = editor                  # for the tests
        if editor.result() != QDialog.Accepted and await ask(editor) != QDialog.Accepted:
            return
        self.set_scf(editor.text())

    def set_scf(self, text: str):
        """The scf input, and the defaults it gives the other steps."""
        self.scf_text = text
        self.edit_scf.setText("Edit the scf input…")
        try:
            pw = read_pw_input(text)
            structure = Structure.from_pw(pw)
        except (PwInputError, StructureError) as e:
            self.scf_info.setText(f"The scf input has no structure yet ({e}).")
            self.update_summary()
            return
        try:
            k = pw.k_points()
        except PwInputError:
            k = None
        grid = k.grid if k is not None and k.kind == "automatic" else None
        if grid is None:
            try:
                grid = grid_from_spacing(structure, 0.2).n
            except KPointsError:
                grid = (6, 6, 6)
        for box, value in zip(self.grid, grid):
            box.setValue(value)
        occupied = occupied_bands_of(pw, self.scf_folder())
        nscf, bands = suggested_bands(occupied)
        if nscf:
            self.nscf_nbnd.setValue(nscf)
            self.bands_nbnd.setValue(bands)
        self.scf_info.setText(
            f"{structure.formula}, {structure.symmetry().international}; prefix "
            f"'{pw.get('control', 'prefix', 'pw')}'"
            + (f"; {occupied} occupied bands" if occupied else
               "; occupied bands not known (an insulator with pseudopotentials in the project "
               "tells them): set nbnd yourself"))
        self.update_summary()

    # -- what will be made --------------------------------------------------------------------

    def chain(self) -> QeChain:
        job = JobSpec(partition=self.partition.currentText().strip(), nodes=self.nodes.value(),
                      tasks_per_node=self.tasks.value(), cpus_per_task=1,
                      memory=self.memory.text().strip(), time=self.time.text().strip())
        path = None
        if self.bands.isChecked() and self.scf_text:
            path = band_path(Structure.from_pw_input(self.scf_text), self.path.text().strip() or None,
                             self.density.value())
        return QeChain(self.scf_text or "", job,
                       nscf_grid=Grid(tuple(b.value() for b in self.grid)) if self.nscf.isChecked() else None,
                       nscf_nbnd=self.nscf_nbnd.value() or None, p2y=self.p2y.isChecked(),
                       bands_path=path, bands_nbnd=self.bands_nbnd.value() or None)

    def update_summary(self, *_):
        n = len(self.project.steps)
        kinds = ["scf"] + (["nscf"] if self.nscf.isChecked() else []) \
            + (["p2y"] if self.nscf.isChecked() and self.p2y.isChecked() else []) \
            + (["bands"] if self.bands.isChecked() else [])
        text = "Steps: " + " → ".join(f"{n + i:02d}-{_slug(k)}" for i, k in enumerate(kinds, 1))
        if self.scf_text and self.nscf.isChecked():
            try:
                inputs = chain_inputs(QeChain(self.scf_text, JobSpec(),
                                              nscf_grid=Grid(tuple(b.value() for b in self.grid))),
                                      f"{n + 1:02d}-scf")
                nscf = read_pw_input(inputs["nscf"])
                count = pw_kpoint_count(nscf)
                if count is not None:
                    text += f". The nscf computes {count} k-points"
                if nscf.get("system", "force_symmorphic") is True:
                    text += ", with force_symmorphic (the crystal's fractional translations would stop p2y)"
            except (PwInputError, StructureError, KPointsError):
                pass
        if self.nscf.isChecked() and self.bands.isChecked():
            text += (". The bands run replaces the nscf's wave functions in prefix.save: it runs "
                     "after " + ("p2y." if self.p2y.isChecked() else "the nscf (run p2y before it)."))
        self.summary.setText(text if text.endswith(".") else text + ".")

    def _accept(self):
        if not self.scf_text:
            self.summary.setText("Make the scf input first.")
            return
        try:
            Structure.from_pw_input(self.scf_text)
        except StructureError as e:
            self.summary.setText(f"The scf input has no structure: {e}")
            return
        if self.nscf.isChecked() and not self.nscf_nbnd.value():
            self.summary.setText("Set the nscf's nbnd: Yambo needs empty bands.")
            return
        try:
            self.chain()
        except KPointsError as e:
            self.summary.setText(str(e))
            return
        self.accept()
