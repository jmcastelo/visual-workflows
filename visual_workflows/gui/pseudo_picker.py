"""Choosing PseudoDojo pseudopotentials for a structure's elements: the table
(functional, relativistic or not, accuracy), each element's file and cutoff
hints, and the cutoffs they suggest; the files are downloaded into the
library on this PC (once) and copied into the project's pseudos/ folder."""

from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..core.pseudodojo import (CITATION, LICENSE, LEVELS, Cutoffs, Library, PseudoDojoError, table,
                               suggested_cutoffs)
from .asyncqt import launch

_RELATIVISTIC = [("SR", "Scalar relativistic"), ("FR", "Fully relativistic (spin–orbit)")]
_COLUMNS = ["Element", "File", "Low (Ry)", "Normal (Ry)", "High (Ry)", "Here"]


class PseudoPicker(QDialog):
    """After it is accepted: `files` ({element: file name in pseudos/}) and
    `cutoffs` (the suggestion at the chosen level)."""

    def __init__(self, parent, elements: list[str], pseudos_folder: Path, library: Library | None = None,
                 relativistic: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Pseudopotentials from PseudoDojo")
        self.resize(720, 440)
        self.elements = list(dict.fromkeys(elements))
        self.folder = Path(pseudos_folder)
        self.library = library or Library()
        self.files: dict[str, str] = {}
        self.cutoffs: Cutoffs | None = None
        self._hints: dict = {}
        self._loading: asyncio.Task | None = None
        self._getting: asyncio.Task | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.xc = QComboBox()
        self.xc.addItems(["PBE", "PBEsol"])
        self.relativistic = QComboBox()
        for code, text in _RELATIVISTIC:
            self.relativistic.addItem(text, code)
        self.relativistic.setCurrentIndex(1 if relativistic else 0)
        self.accuracy = QComboBox()
        self.accuracy.addItem("Standard", "standard")
        self.accuracy.addItem("Stringent (harder, more accurate)", "stringent")
        self.level = QComboBox()
        for level in LEVELS:
            self.level.addItem(level.capitalize(), level)
        self.level.setCurrentIndex(1)
        row = QHBoxLayout()
        for w in (self.xc, self.relativistic, self.accuracy):
            row.addWidget(w)
        form.addRow("Table", row)
        form.addRow("Cutoff hint", self.level)
        layout.addLayout(form)

        self.table = QTableWidget(len(self.elements), len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.suggestion = QLabel()
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.suggestion)
        layout.addWidget(self.status)
        note = QLabel(f"PseudoDojo (ONCVPSP v0.4.1, norm-conserving), {LICENSE}. "
                      f"Please cite {CITATION}.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray")
        layout.addWidget(note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Get them")
        self.buttons.accepted.connect(self._get)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        for combo in (self.xc, self.relativistic, self.accuracy):
            combo.currentIndexChanged.connect(self.reload)
        self.level.currentIndexChanged.connect(self._show_suggestion)
        self.reload()

    # -- the table -----------------------------------------------------------------------

    def chosen_table(self):
        return table(self.xc.currentText(), self.relativistic.currentData(), self.accuracy.currentData())

    def reload(self):
        """Read the chosen table's hints (downloaded once) and show them."""
        self._hints = {}
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.status.setText("Reading the table…")
        if self._loading is not None and not self._loading.done():
            self._loading.cancel()
        self._loading = launch(self, self._load(self.chosen_table()), "PseudoDojo")

    async def _load(self, t):
        try:
            hints = await asyncio.to_thread(self.library.hints, t)
        except PseudoDojoError as e:
            self.status.setText(str(e))
            return
        if t != self.chosen_table():
            return                       # changed meanwhile
        self._hints = hints
        self._fill(t)

    def _fill(self, t):
        missing = []
        for row, element in enumerate(self.elements):
            h = self._hints.get(element)
            cells = [element, t.file_name(element) if h else "not in this table"]
            cells += [f"{h.cutoff(level):g}" if h and h.cutoff(level) is not None else "—"
                      for level in LEVELS]
            here = (self.library.root / t.id / t.file_name(element)).exists()
            cells.append("in the library" if here else "to download" if h else "")
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col >= 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, col, item)
            if h is None:
                missing.append(element)
        self.table.resizeColumnsToContents()
        self._show_suggestion()
        if missing:
            self.status.setText(f"{', '.join(missing)}: not in {t.title}.")
        else:
            self.status.setText(f"Into {self.folder}.")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not missing and bool(self.elements))

    def _show_suggestion(self):
        known = {e: self._hints[e] for e in self.elements if e in self._hints}
        if not known:
            self.suggestion.setText("")
            self.cutoffs = None
            return
        self.cutoffs = suggested_cutoffs(known, self.level.currentData())
        if self.cutoffs.ecutwfc is None:
            self.suggestion.setText("No cutoff hints for these elements.")
            return
        text = (f"Suggested: ecutwfc = {self.cutoffs.ecutwfc:g} Ry, "
                f"ecutrho = {self.cutoffs.ecutrho:g} Ry")
        if self.cutoffs.without_hints:
            text += f" (no hint for {', '.join(self.cutoffs.without_hints)})"
        self.suggestion.setText(text)

    # -- getting them --------------------------------------------------------------------

    def _get(self):
        self.buttons.setEnabled(False)
        self.status.setText("Downloading…")
        self._getting = launch(self, self._download(self.chosen_table()), "PseudoDojo")

    async def _download(self, t):
        from ..core.pseudodojo import add_to_project
        try:
            entries = await asyncio.to_thread(self.library.get, t, self.elements)
            names = await asyncio.to_thread(add_to_project, entries, self.folder)
        except (PseudoDojoError, OSError) as e:
            self.status.setText(str(e))
            self.buttons.setEnabled(True)
            return
        self.files = dict(zip([e.element for e in entries], names))
        self.accept()
