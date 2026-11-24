"""A downloaded SAVE's bands in a window: the report (energy ranges, gaps,
the project's windows checked), the transitions up to an energy as one
types it, and the density of states drawn."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFontDatabase, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QPlainTextEdit, QSplitter, QVBoxLayout, QWidget)

from ..core.bands import SaveBands, report


# The plot's right end (eV above the valence top), unless the window reaches further
UPPER_EV = 15.0


class DosPlot(QWidget):
    """The density of states against energy, the valence top and the
    conduction bottom marked, and the energy window shaded."""

    def __init__(self, energies, dos, gap: float | None = None, parent=None):
        super().__init__(parent)
        self.energies, self.dos, self.gap = np.asarray(energies), np.asarray(dos), gap
        self.reach: tuple[float, float] | None = None
        self.setMinimumSize(400, 220)

    def set_reach(self, reach: tuple[float, float] | None):
        """The energies the window's states span: (lowest valence, highest conduction)."""
        self.reach = reach
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        left, right, top, bottom = 50, 10, 10, 30
        w, h = self.width() - left - right, self.height() - top - bottom
        if w <= 0 or h <= 0 or not len(self.energies):
            return
        x0 = float(self.energies.min())
        # the highest bands (the least accurate) cut off, unless the window reaches them
        top = max(UPPER_EV, (self.reach[1] + 2.0) if self.reach else 0.0)
        x1 = min(float(self.energies.max()), top)
        shown = self.energies <= x1
        ymax = float(self.dos[shown].max()) or 1.0

        def at(x, y):
            return QPointF(left + (x - x0) / (x1 - x0) * w, top + h - y / ymax * h)

        if self.reach is not None and self.gap is not None:
            low, high = self.reach
            for a, b in ((low, 0.0), (self.gap, high)):
                pa, pb = at(max(a, x0), 0), at(min(b, x1), ymax)
                p.fillRect(int(pa.x()), int(pb.y()), int(pb.x() - pa.x()), int(pa.y() - pb.y()),
                           pal.highlight().color().lighter(170))
        p.setPen(QPen(pal.mid().color(), 1))
        p.drawLine(at(x0, 0), at(x1, 0))
        for x, label in ((0.0, "0"), (self.gap, f"{self.gap:.2f}") if self.gap is not None else (None, "")):
            if x is not None and x0 <= x <= x1:
                p.setPen(QPen(pal.mid().color(), 1, Qt.DashLine))
                p.drawLine(at(x, 0), at(x, ymax))
                p.setPen(pal.text().color())
                p.drawText(at(x, 0) + QPointF(-10, 15), label)
        path = QPainterPath(at(self.energies[0], self.dos[0]))
        for x, y in zip(self.energies[shown][1:], self.dos[shown][1:]):
            path.lineTo(at(x, y))
        p.setPen(QPen(pal.text().color(), 1.5))
        p.drawPath(path)
        p.setPen(pal.text().color())
        p.drawText(5, top + 10, f"{ymax:.1f}")
        p.drawText(left, self.height() - 5, f"{x0:.0f} eV")
        p.drawText(left + w - 40, self.height() - 5, f"{x1:.0f} eV")
        p.drawText(5, top + h, "0")


class BandsDialog(QDialog):
    def __init__(self, title: str, sb: SaveBands, extra: list[str], parent=None, fit=None):
        """`fit`: a function fitting the project's windows, returning lines to show."""
        super().__init__(parent, windowTitle=title)
        self.sb, self.fit = sb, fit
        layout = QVBoxLayout(self)
        split = QSplitter(Qt.Vertical)
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.text.setPlainText("\n".join(report(sb) + extra))
        split.addWidget(self.text)
        w, d = sb.dos()
        gap = sb.save.indirect.value if sb.save.indirect and not sb.save.metal else None
        self.plot = DosPlot(w, d, gap)
        split.addWidget(self.plot)
        split.setSizes([380, 320])
        layout.addWidget(split)
        row = QHBoxLayout()
        row.addWidget(QLabel("Transitions up to"))
        self.energy = QDoubleSpinBox(suffix=" eV", decimals=1, singleStep=0.5, maximum=100.0)
        row.addWidget(self.energy)
        self.window_label = QLabel()
        row.addWidget(self.window_label, 1)
        layout.addLayout(row)
        layout.addWidget(QLabel("Density of states in states/eV per cell (0.1 eV Gaussians), zero at the "
                                "valence top. Shaded: the energies of the states in the transitions up to that energy."))
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        self.fit_button = buttons.addButton("Fit the project's windows", QDialogButtonBox.ActionRole)
        self.fit_button.setToolTip("Grow the yambo inputs' QPkrange and BSEBands of steps not run yet to "
                                   "whole degenerate sets at the gap's edges")
        self.fit_button.clicked.connect(self._fit)
        self.fit_button.setEnabled(fit is not None and sb.edge_sets() is not None)
        layout.addWidget(buttons)
        self.energy.valueChanged.connect(self._window)
        if sb.save.direct is not None:
            self.energy.setValue(round(sb.save.direct.value + 1.0, 1))
        else:
            self.energy.setEnabled(False)
            self.window_label.setText("No windows: the occupied bands are not known.")
        self.resize(900, 760)

    def _fit(self):
        lines = self.fit()
        self.text.appendPlainText("\n" + "\n".join(lines))

    def _window(self, energy: float):
        w = self.sb.window(energy)
        if w is None:
            self.window_label.setText("no transition that low")
            self.plot.set_reach(None)
            return
        size = f"; BSE size {w.valence} × {w.conduction} × {w.nk_bz} = {w.bse_size}" if w.bse_size else ""
        splits = self.sb.splits(w.first, w.last)
        self.window_label.setText(f"bands {w.first}–{w.last} ({w.valence} valence, {w.conduction} conduction)"
                                  + size + ("; splits degenerate bands" if splits else ""))
        self.window_label.setToolTip("\n".join(splits))
        self.plot.set_reach(self.sb.reach(energy))
