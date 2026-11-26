"""New QE chain…: an scf input (made in the pw.x editor), and the steps
derived from it: an nscf with empty bands for Yambo, p2y, a band structure;
one set of resources for the pw.x steps."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ..core.chain import occupied_bands_of
from ..core.jobspec import JobSpec
from ..core.kpoints import Grid, KPointsError, band_path, grid_from_spacing, pw_kpoint_count
from ..core.profile import ClusterProfile
from ..core.project import Project, _slug
from ..core.qe_chain import QeChain, chain_inputs, suggested_bands
from ..core.structure import Structure, StructureError
from ..yambopy_ext.qepy.pw_input import PwInputError, read_pw_input
from .asyncqt import ask, launch
from .node_list import NodeListEdit


class QeChainDialog(QDialog):
    def __init__(self, parent, project: Project, profile: ClusterProfile):
        super().__init__(parent)
        self.project, self.profile = project, profile
        self.setWindowTitle("QE chain to excitons")
        self.resize(900, 600)
        self.scf_text: str | None = None
        # Sections, one page each (the GUI redesign, stage 3): chosen on the
        # left or with Back and Next; the summary and Add steps always in view
        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        self.sections = QListWidget()
        self.sections.setMaximumWidth(230)
        self.section_pages = QStackedWidget()
        self.sections.currentRowChanged.connect(self._section_chosen)
        body.addWidget(self.sections)
        body.addWidget(self.section_pages, 1)
        layout.addLayout(body, 1)

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
        self._add_section("1. scf: the structure", scf,
                          "Every step takes the structure, the settings and the pseudopotentials from "
                          "the scf. Make it first: the other sections take their defaults from it.")

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
        self._add_section("2. nscf and p2y: Yambo's SAVE", self.nscf,
                          "The nscf computes the empty bands GW and BSE need, on an unshifted grid; p2y "
                          "turns its output into Yambo's SAVE.")

        self.bands = QGroupBox("Band structure", checkable=True)
        b = QFormLayout(self.bands)
        self.path = QLineEdit(placeholderText="the lattice's standard path")
        self.density = QDoubleSpinBox(decimals=1, minimum=1.0, maximum=500.0, value=20.0)
        self.density.setToolTip("k-points per Å⁻¹ (2π included) along the path")
        self.bands_nbnd = QSpinBox(minimum=0, maximum=100000, specialValueText="pw.x's default")
        b.addRow("Path", self.path)
        b.addRow("Density", self.density)
        b.addRow("nbnd", self.bands_nbnd)
        self._add_section("3. Band structure", self.bands,
                          "The DFT bands along a path through the Brillouin zone, to look at; nothing "
                          "after it needs them.")

        self.yambo = QGroupBox("Yambo, after p2y (p2y's step also runs yambo's setup)", checkable=True)
        self.yambo.setChecked(False)
        y = QFormLayout(self.yambo)
        self.gw = QCheckBox("GW (plasmon pole): QP energies")
        self.gw.setChecked(True)
        self.gw_terminator = QCheckBox("Bruneval–Gonze terminator (fewer empty bands needed)")
        self.gw_terminator.setToolTip("GTermKind = \"BG\": stands in for the bands above GbndRnge; off by "
                                      "default until a study shows what it saves")
        self.bse = QComboBox()
        self.bse.addItem("No BSE", None)
        self.bse.addItem("BSE on the GW's QP energies", "gw")
        self.bse.addItem("BSE on DFT energies with a scissor shift (cheaper)", "scissor")
        self.bse.setCurrentIndex(1)
        self.scissor = QDoubleSpinBox(decimals=3, minimum=0.0, maximum=20.0, singleStep=0.05,
                                      value=0.5, suffix=" eV")
        self.scissor.setToolTip("How much the empty bands are raised (from a GW, or experiment)")
        self.scissor_from: str | None = None         # an ndb.QP: the scissor fitted to it instead
        self.scissor_from_button = QPushButton("From a GW…")
        self.scissor_from_button.setToolTip("Fit the scissor (shift and stretches) to a GW's ndb.QP, "
                                            "on the BSE's bands, when the chain is made")
        self.scissor_from_button.clicked.connect(lambda: self.choose_scissor_from())
        self.scissor_from_label = QLabel(wordWrap=True)
        self.bse_rim_w = QCheckBox("RIM-W in a layer's BSE kernel, as in its GW (yambo 5.2+)")
        self.bse_rim_w.setChecked(True)
        self.bse_rim_w.setToolTip("The screened interaction averaged around each q-point in the BSE too, as "
                                  "in the GW: on hBN (15×15) it raised the first exciton by 0.3 eV")
        self.bse_all_q = QCheckBox("BSE at every Q: the excitons' dispersion (one BSE per irreducible k-point)")
        self.bse_all_q.setToolTip("BSEQptR = 1 | nk: as many BSEs as irreducible k-points, each as costly as "
                                  "the one at Q = 0; only Q = 0 is seen by light")
        # 0 until the scf tells the occupied bands: the default window then
        self.window = [QSpinBox(minimum=0, maximum=100000, specialValueText="from the scf") for _ in range(2)]
        wrow = QHBoxLayout()
        for b in self.window:
            wrow.addWidget(b)
            b.valueChanged.connect(self.update_summary)
        self.window_note = QLabel("Bands for QPkrange and BSEBands (default: 2 below and 2 above the gap)",
                                  wordWrap=True)
        y.addRow(self.gw)
        y.addRow(self.gw_terminator)
        y.addRow("BSE", self.bse)
        srow = QHBoxLayout()
        srow.addWidget(self.scissor)
        srow.addWidget(self.scissor_from_button)
        srow.addWidget(self.scissor_from_label, 1)
        y.addRow("Scissor", srow)
        y.addRow(self.bse_rim_w)
        y.addRow(self.bse_all_q)
        y.addRow("Bands", wrow)
        y.addRow(self.window_note)
        self._add_section("4. GW and BSE", self.yambo,
                          "GW corrects the band energies (the quasiparticles); the BSE adds the "
                          "electron–hole attraction: the excitons Visual Excitons shows.")
        for w in (self.yambo, self.gw):
            w.toggled.connect(self.update_summary)
        self.bse.currentIndexChanged.connect(self.update_summary)

        resources = QGroupBox("Resources of the pw.x steps (p2y: one task)")
        r = QFormLayout(resources)
        self.partition = QComboBox(editable=True)
        self.partition.addItems(list(profile.partitions))
        self.nodes = QSpinBox(minimum=1, maximum=100000, value=1)
        self.node_list = NodeListEdit(profile, self.partition.currentText, self.nodes)
        partition = next(iter(profile.partitions.values()), None)
        cores = (partition.cores_per_node // max(1, partition.threads_per_core)
                 if partition and partition.cores_per_node else 1)    # physical cores
        self.tasks = QSpinBox(minimum=1, maximum=100000, value=cores)
        self.memory = QLineEdit(placeholderText="per node, e.g. 60G")
        self.time = QLineEdit("12:00:00", placeholderText="e.g. 12:00:00")
        r.addRow("Partition", self.partition)
        r.addRow("Nodes", self.nodes)
        r.addRow("Nodes to use", self.node_list)
        r.addRow("MPI tasks per node", self.tasks)
        r.addRow("Memory", self.memory)
        r.addRow("Time", self.time)
        # The yambo steps' own MPI tasks and threads (the rest as the pw.x steps'):
        # yambo often runs best on fewer tasks with threads (on Lynx, 1 task)
        self.yambo_tasks = QSpinBox(minimum=0, maximum=100000, value=0,
                                    specialValueText="as the pw.x steps")
        self.yambo_threads = QSpinBox(minimum=1, maximum=1024, value=1)
        self.yambo_resources = QGroupBox("GW and BSE steps (partition, nodes, memory and time "
                                         "as above)")
        y = QFormLayout(self.yambo_resources)
        y.addRow("MPI tasks per node", self.yambo_tasks)
        y.addRow("Threads per task", self.yambo_threads)
        r.addRow(self.yambo_resources)
        self._add_section("5. Resources", resources,
                          "For the pw.x steps, and the GW and BSE steps' tasks and threads; each "
                          "step can be changed afterwards with Edit….")

        self.summary = QLabel(wordWrap=True)
        layout.addWidget(self.summary)
        bottom = QHBoxLayout()
        self.back_button = QPushButton("‹ Back")
        self.back_button.clicked.connect(lambda: self.sections.setCurrentRow(self.sections.currentRow() - 1))
        self.next_button = QPushButton("Next ›")
        self.next_button.clicked.connect(lambda: self.sections.setCurrentRow(self.sections.currentRow() + 1))
        bottom.addWidget(self.back_button)
        bottom.addWidget(self.next_button)
        bottom.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Add steps")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        bottom.addWidget(self.buttons)
        layout.addLayout(bottom)
        self.sections.setCurrentRow(0)
        for w in (self.nscf, self.bands, self.p2y):
            w.toggled.connect(self.update_summary)
        self.nscf_nbnd.valueChanged.connect(self.update_summary)
        self.update_summary()

    def _add_section(self, title: str, group: QWidget, intro: str):
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(QLabel(intro, wordWrap=True))
        pl.addWidget(group)
        pl.addStretch(1)
        self.section_pages.addWidget(page)
        self.sections.addItem(title)

    def _section_chosen(self, row: int):
        if row < 0:
            return
        self.section_pages.setCurrentIndex(row)
        if hasattr(self, "next_button"):                 # built after the sections
            self.back_button.setEnabled(row > 0)
            self.next_button.setEnabled(row < self.sections.count() - 1)

    def _mark_sections(self):
        """Each section's name says whether its steps will be made."""
        on = [True, self.nscf.isChecked(), self.bands.isChecked(),
              self.yambo.isChecked() and self.yambo.isEnabled(), True]
        for i, made in enumerate(on):
            item = self.sections.item(i)
            if item is None:
                continue
            font = item.font()
            font.setItalic(not made)
            item.setFont(font)
            item.setForeground(self.palette().text() if made else self.palette().placeholderText())
            item.setToolTip("" if made else "Off: no step made")

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
        if occupied:
            self.window[0].setValue(max(1, occupied - 1))
            self.window[1].setValue(occupied + 2)
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
                      memory=self.memory.text().strip(), time=self.time.text().strip(),
                      nodelist=self.node_list.nodelist())
        path = None
        if self.bands.isChecked() and self.scf_text:
            path = band_path(Structure.from_pw_input(self.scf_text), self.path.text().strip() or None,
                             self.density.value())
        yambo_job = None
        if self.yambo_tasks.value() or self.yambo_threads.value() > 1:
            yambo_job = replace(job, tasks_per_node=self.yambo_tasks.value() or job.tasks_per_node,
                                cpus_per_task=self.yambo_threads.value())
        return QeChain(self.scf_text or "", job, yambo_job=yambo_job,
                       nscf_grid=Grid(tuple(b.value() for b in self.grid)) if self.nscf.isChecked() else None,
                       nscf_nbnd=self.nscf_nbnd.value() or None, p2y=self.p2y.isChecked(),
                       bands_path=path, bands_nbnd=self.bands_nbnd.value() or None,
                       **self._yambo_choices())

    def choose_scissor_from(self, path: str | None = None):
        """An ndb.QP to fit the scissor to (asked for when not given); none clears it."""
        if path is None:
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(self, "A GW's QP database", "",
                                                  "ndb.QP (ndb.QP*);;All files (*)")
            if not path:
                path = ""
        self.scissor_from = path or None
        self.scissor_from_label.setText(f"fitted to {path}" if path else "")
        self.update_summary()

    def _yambo_choices(self) -> dict:
        if not (self.yambo.isChecked() and self.nscf.isChecked() and self.p2y.isChecked()):
            return {}
        bse = self.bse.currentData()
        gw = self.gw.isChecked()
        fitted = bse == "scissor" and self.scissor_from is not None
        return {"gw": gw, "bse": bse,
                "scissor_ev": self.scissor.value() if bse == "scissor" and not fitted else None,
                "scissor_from": self.scissor_from if fitted else None,
                "yambo_bands": (tuple(b.value() for b in self.window)
                                if all(b.value() for b in self.window) else None),   # None: from the occupied bands
                "bse_rim_w": bool(bse) and self.bse_rim_w.isChecked(),
                "gw_terminator": gw and self.gw_terminator.isChecked(),
                "bse_all_q": bool(bse) and self.bse_all_q.isChecked()}

    def update_summary(self, *_):
        self._mark_sections()
        n = len(self.project.steps)
        kinds = ["scf"] + (["nscf"] if self.nscf.isChecked() else []) \
            + (["p2y"] if self.nscf.isChecked() and self.p2y.isChecked() else []) \
            + (["gw"] if self._yambo_choices().get("gw") else []) \
            + (["bse"] if self._yambo_choices().get("bse") else []) \
            + (["bands"] if self.bands.isChecked() else [])
        self.yambo.setEnabled(self.nscf.isChecked() and self.p2y.isChecked())
        scissor = self.bse.currentData() == "scissor"
        self.scissor.setEnabled(scissor and self.scissor_from is None)
        self.scissor_from_button.setEnabled(scissor)
        self.bse_rim_w.setEnabled(self.bse.currentData() is not None)
        self.yambo_resources.setEnabled(bool(self._yambo_choices().get("gw")
                                             or self._yambo_choices().get("bse")))
        self.bse_all_q.setEnabled(self.bse.currentData() is not None)
        self.gw_terminator.setEnabled(self.gw.isChecked())
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
        choices = self._yambo_choices()
        if choices.get("bse") == "gw" and not choices.get("gw"):
            self.summary.setText("A BSE on the GW's QP energies needs the GW step.")
            return
        if choices and self.window[0].value() > self.window[1].value():
            self.summary.setText("The bands' first is above their last.")
            return
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
