"""Convergence studies in a window: making one from a GW or BSE step, and a
study's results against its values (a table and a plot)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.studies import (ORDER, QUANTITIES, SUGGESTED, TOLERANCE_EV, StudyError, add_study, kind_of,
                            next_in_order, studies, study_results, tolerance_of)


class XYPlot(QWidget):
    """Points joined in order; the band of ±tolerance around the last one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.points: list[tuple[float, float]] = []
        self.tolerance = 0.0
        self.x_label = ""
        self.setMinimumSize(400, 220)

    def set_points(self, points, tolerance: float, x_label: str):
        self.points = [(x, y) for x, y in points if not math.isnan(x)]
        self.tolerance, self.x_label = tolerance, x_label
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        left, right, top, bottom = 60, 15, 10, 30
        w, h = self.width() - left - right, self.height() - top - bottom
        if len(self.points) < 1 or w <= 0 or h <= 0:
            p.setPen(pal.text().color())
            p.drawText(self.rect(), Qt.AlignCenter, "No results yet")
            return
        xs, ys = [x for x, _ in self.points], [y for _, y in self.points]
        last = ys[-1]
        y0, y1 = min(ys + [last - self.tolerance]), max(ys + [last + self.tolerance])
        x0, x1 = min(xs), max(xs)
        if x1 == x0:
            x0, x1 = x0 - 1, x1 + 1
        pad = (y1 - y0) * 0.1 or 0.1
        y0, y1 = y0 - pad, y1 + pad

        def at(x, y):
            return QPointF(left + (x - x0) / (x1 - x0) * w, top + h - (y - y0) / (y1 - y0) * h)

        band_top, band_bottom = at(x0, last + self.tolerance), at(x1, last - self.tolerance)
        p.fillRect(int(band_top.x()), int(band_top.y()), int(band_bottom.x() - band_top.x()),
                   int(band_bottom.y() - band_top.y()), pal.highlight().color().lighter(175))
        p.setPen(QPen(pal.mid().color(), 1))
        p.drawLine(int(left), int(top + h), int(left + w), int(top + h))
        p.drawLine(int(left), int(top), int(left), int(top + h))
        path = QPainterPath(at(*self.points[0]))
        for x, y in self.points[1:]:
            path.lineTo(at(x, y))
        p.setPen(QPen(pal.text().color(), 1.5))
        p.drawPath(path)
        for x, y in self.points:
            p.drawEllipse(at(x, y), 3, 3)
        p.drawText(5, top + 10, f"{y1:.2f}")
        p.drawText(5, top + h, f"{y0:.2f}")
        p.drawText(left, self.height() - 5, f"{x0:g}")
        p.drawText(left + w - 30, self.height() - 5, f"{x1:g}")
        p.drawText(left + w // 2 - 40, self.height() - 5, self.x_label)


class NewStudyDialog(QDialog):
    """A study's base step, variable, values and unit."""

    def __init__(self, project, base: str | None = None, parent=None):
        super().__init__(parent, windowTitle="New convergence study")
        form = QFormLayout(self)
        self.base = QComboBox()
        for step in project.steps:
            if step.job and any(r.program == "yambo" and r.executable == "yambo" and r.input
                                for r in step.job.runs):
                self.base.addItem(step.id)
        self.project = project
        if base and self.base.findText(base) >= 0:
            self.base.setCurrentText(base)
        form.addRow("Step copied", self.base)
        self.variable = QComboBox(editable=True)
        form.addRow("Variable", self.variable)
        self.order = QLabel(wordWrap=True)
        self.order.setTextFormat(Qt.RichText)
        form.addRow(self.order)
        self.values = QLineEdit(placeholderText="e.g. 2 3 4 5, or for bands 20 30 40 (or 1:30)")
        form.addRow("Values", self.values)
        self.unit = QLineEdit(placeholderText="the input's, else Ry for cutoffs")
        form.addRow("Unit (if the input has none)", self.unit)
        form.addRow(QLabel("A step per value, a copy of the step with this one variable changed. "
                           "Converge the variables in the order above, each with those before it "
                           "converged.", wordWrap=True))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.base.currentTextChanged.connect(self.base_changed)
        self.base_changed()

    def base_changed(self, *_):
        """The variables in the recommended order for the step's kind, the next
        one to study chosen."""
        base = self.base.currentText()
        step = next((st for st in self.project.steps if st.id == base), None)
        kind = kind_of(step) if step is not None else None
        ordered = [v for v, _ in ORDER[kind] if v != "k-grid"] if kind else []
        self.variable.clear()
        self.variable.addItems(ordered + [v for v in SUGGESTED if v not in ordered])
        after = next_in_order(self.project, base) if kind else None
        if after:
            self.variable.setCurrentText(after[0])
        if kind:
            items = "".join(f"<li><b>{v}</b>: {why}</li>" for v, why in ORDER[kind])
            self.order.setText(f"The order for a {kind.upper()} step (Yambo's tutorials):<ol>{items}</ol>"
                               + (f"Next here: <b>{after[0]}</b>." if after else "Every variable studied."))
        else:
            self.order.setText("")


class StudiesDialog(QDialog):
    def __init__(self, project, selected: str | None = None, changed=None, parent=None):
        super().__init__(parent, windowTitle=f"Convergence studies · {project.name}")
        self.project, self.selected, self.changed = project, selected, changed
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.study = QComboBox()
        self.study.currentIndexChanged.connect(lambda _: self.refresh())
        row.addWidget(QLabel("Study"))
        row.addWidget(self.study, 1)
        self.new_button = QPushButton("New study…")
        self.new_button.clicked.connect(self.new_study)
        row.addWidget(self.new_button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.quantity = QComboBox()
        self.quantity.addItem("(default)", None)
        for key, (text, _) in QUANTITIES.items():
            self.quantity.addItem(text, key)
        self.quantity.currentIndexChanged.connect(self.quantity_changed)
        row.addWidget(QLabel("Read back"))
        row.addWidget(self.quantity, 1)
        self.tolerance = QDoubleSpinBox(suffix=" eV", decimals=3, singleStep=0.01, minimum=0.001,
                                        value=TOLERANCE_EV)
        self.tolerance.valueChanged.connect(lambda _: self.refresh())
        row.addWidget(QLabel("Tolerance"))
        row.addWidget(self.tolerance)
        layout.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Value", "Step", "State", "Result (eV)", "Change (eV)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        self.plot = XYPlot()
        layout.addWidget(self.plot)
        self.verdict = QLabel(wordWrap=True)
        layout.addWidget(self.verdict)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(800, 700)
        self.reload()

    def quantity_changed(self, *_):
        """The tolerance set to the quantity's (0.05 eV, 0.1 eV for a peak)."""
        key = self.quantity.currentData()
        self.tolerance.blockSignals(True)
        self.tolerance.setValue(tolerance_of(key) if key else TOLERANCE_EV)
        self.tolerance.blockSignals(False)
        self.refresh()

    def reload(self, choose: str | None = None):
        self.study.blockSignals(True)
        self.study.clear()
        for s in studies(self.project):
            self.study.addItem(s.name)
        if choose:
            self.study.setCurrentText(choose)
        self.study.blockSignals(False)
        self.refresh()

    def new_study(self, *, dialog: NewStudyDialog | None = None):
        d = dialog or NewStudyDialog(self.project, self.selected, self)
        if dialog is None and d.exec() != QDialog.Accepted:
            return
        try:
            s = add_study(self.project, d.base.currentText(), d.variable.currentText().strip(),
                          d.values.text().replace(",", " ").split(), unit=d.unit.text().strip() or None)
        except StudyError as e:
            self.verdict.setText(f"Not made: {e}")
            return
        if self.changed:
            self.changed()
        self.reload(s.name)

    def refresh(self):
        name = self.study.currentText()
        if not name:
            self.table.setRowCount(0)
            self.plot.set_points([], 0, "")
            self.verdict.setText("No study yet: New study… makes one from a GW or BSE step.")
            return
        s = next(x for x in studies(self.project) if x.name == name)
        try:
            sr = study_results(self.project, s, self.quantity.currentData(), self.tolerance.value())
        except StudyError as e:
            self.verdict.setText(str(e))
            return
        self.table.setRowCount(0)
        for p in sr.points:
            r = self.table.rowCount()
            self.table.insertRow(r)
            cells = [p.value, p.step, p.state, "" if p.result is None else f"{p.result:.3f}",
                     "" if p.change is None else f"{p.change:+.3f}"]
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.plot.set_points([(p.x, p.result) for p in sr.points if p.result is not None],
                             sr.tolerance, s.variable + (f" ({s.unit})" if s.unit else ""))
        have = sum(p.result is not None for p in sr.points)
        if have < 2:
            text = "Not enough results yet: submit the study's steps, then Get results… for Visual Excitons."
        elif sr.converged_at is not None:
            text = (f"Changes within {sr.tolerance:g} eV from {s.variable} = {sr.converged_at} on: "
                    "converged there.")
        else:
            text = f"The changes stay above {sr.tolerance:g} eV: not converged over these values."
        what = QUANTITIES[sr.quantity][0]
        self.verdict.setText(f"Read back: {what}; shaded, the tolerance around the last result. " + text
                             + (" Not read: " + "; ".join(sr.errors) if sr.errors else ""))
