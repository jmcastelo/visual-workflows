"""A downloaded SAVE's bands in a window, drawn (pyqtgraph, as Visual
Excitons' plots): a summary line; the band energies at each irreducible
k-point beside the density of states, the gap marked, the bands of the
transitions up to an energy drawn bold, a GW's energies over the DFT ones;
the band structure along the path of a bands step; and the text report
(energy ranges, gaps, the project's windows checked) under Details.

Colours: occupied bands blue, empty bands red, GW green, the gap amber."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QPalette
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QHBoxLayout, QLabel, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget)

from ..core.bands import SaveBands, report

# The plots' top (eV above the valence top), unless the window reaches further:
# the highest bands are the least converged
UPPER_EV = 15.0
VALENCE, CONDUCTION, GW, GAP = QColor("#1f5fbf"), QColor("#c0392b"), QColor("#1e8449"), QColor("#f2c94c")
BAR = 0.38                     # half a bar's width, in k-points


def _colour(c: QColor, alpha: int) -> QColor:
    c = QColor(c)
    c.setAlpha(alpha)
    return c


def _style(widget: pg.GraphicsLayoutWidget, plots):
    """The window's colours on the plots (light or dark theme)."""
    pal = widget.palette()
    widget.setBackground(pal.color(QPalette.Base))
    text = pal.color(QPalette.Text)
    for plot in plots:
        for name in ("left", "bottom"):
            axis = plot.getAxis(name)
            axis.setPen(pg.mkPen(text))
            axis.setTextPen(pg.mkPen(text))


def _dos_plot(layout: pg.GraphicsLayoutWidget, energies, dos, gap: float | None, row: int, col: int):
    """The density of states on its side: occupied states blue, empty red."""
    plot = layout.addPlot(row=row, col=col)
    plot.setLabel("bottom", "DOS (states/eV per cell)")
    plot.getAxis("left").setStyle(showValues=False)
    plot.setMouseEnabled(x=False, y=True)
    plot.hideButtons()
    w, d = np.asarray(energies), np.asarray(dos)
    for mask, colour in ((w <= 1e-9, VALENCE), (w >= (gap if gap is not None else np.inf), CONDUCTION)):
        if mask.any():
            fill = pg.FillBetweenItem(pg.PlotDataItem(d[mask], w[mask]), pg.PlotDataItem(np.zeros(mask.sum()), w[mask]),
                                      brush=_colour(colour, 110))
            plot.addItem(fill)
    plot.addItem(pg.PlotDataItem(d, w, pen=pg.mkPen(layout.palette().color(QPalette.Text), width=1)))
    if gap is not None and gap > 0:
        plot.addItem(pg.LinearRegionItem((0.0, gap), orientation="horizontal", movable=False,
                                         brush=_colour(GAP, 90), pen=pg.mkPen(None)))
    plot.setXRange(0, float(d.max()) * 1.05 if len(d) else 1, padding=0)
    return plot


def _align(*plots, height: int = 48):
    """Plots side by side on one energy axis: the same bottom axis height, so
    that their plot areas, and each energy, sit at the same height (their y
    ranges are linked; an axis with a title would otherwise take more room)."""
    for plot in plots:
        plot.getAxis("bottom").setHeight(height)


def _gap_marks(plot, low: float, high: float, x_top: float, x_bottom: float, colour: QColor,
               label: str, shade: bool = True, at: float = 0.5):
    """A gap from the valence top (`low`, at `x_top`) to the conduction bottom
    (`high`, at `x_bottom`): shaded, its edges marked and joined, its value written."""
    items = []
    if shade:
        items.append(pg.LinearRegionItem((low, high), orientation="horizontal", movable=False,
                                         brush=_colour(GAP, 90), pen=pg.mkPen(None)))
    pen = pg.mkPen(colour, width=1.5, style=Qt.DashLine)
    items += [pg.PlotDataItem([x_top, x_bottom], [low, high], pen=pen),
              pg.ScatterPlotItem([x_top, x_bottom], [low, high], size=9, brush=pg.mkBrush(colour),
                                 pen=pg.mkPen(colour))]
    # the label at a fraction `at` of the way, above the joining line
    text = pg.TextItem(label, color=colour, anchor=(0.5, 1.1))
    text.setPos(x_top + at * (x_bottom - x_top), low + at * (high - low))
    items.append(text)
    for item in items:
        plot.addItem(item)
    return items


class BandsDialog(QDialog):
    def __init__(self, title: str, sb: SaveBands, extra: list[str], parent=None, fit=None,
                 names: list[str] | None = None, gws=(), structures=(), save_step: str = ""):
        """`fit`: a function fitting the project's windows, returning lines to
        show. `names`: each k-point's name (Γ, X… or k<n>). `gws`: [(step id,
        QpSummary)], GW energies on this SAVE. `structures`: [(step id,
        BandStructure)], band structures along a path. `save_step`: the step
        whose SAVE it is, named in the summary (the title names none: the
        window shows several steps' results)."""
        self.save_step = save_step
        super().__init__(parent, windowTitle=title)
        self.sb, self.fit = sb, fit
        self.gws, self.structures = list(gws), list(structures)
        self.names = names or [f"k{i}" for i in range(1, sb.save.nk_ibz + 1)]
        layout = QVBoxLayout(self)
        self.summary = QLabel(wordWrap=True, textFormat=Qt.RichText)
        layout.addWidget(self.summary)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # The bands at the k-points, beside the DOS
        page = QWidget()
        pl = QVBoxLayout(page)
        self.plots = pg.GraphicsLayoutWidget()
        pl.addWidget(self.plots, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Transitions up to"))
        self.energy = QDoubleSpinBox(suffix=" eV", decimals=1, singleStep=0.5, maximum=100.0)
        self.energy.setToolTip("The bands of the transitions up to this energy are drawn bold: "
                               "the bands a BSE up to it needs")
        row.addWidget(self.energy)
        self.window_label = QLabel()
        row.addWidget(self.window_label, 1)
        self.gw = QComboBox()
        self.gw.addItem("DFT only", None)
        for step_id, _ in self.gws:
            self.gw.addItem(f"GW of {step_id}", step_id)
        self.gw.setToolTip("A GW's quasiparticle energies drawn over the DFT ones (green)")
        self.gw.setEnabled(bool(self.gws))
        row.addWidget(self.gw)
        pl.addLayout(row)
        self.hover = QLabel(" ")
        pl.addWidget(self.hover)
        self.tabs.addTab(page, "Bands at the k-points")

        # The band structure along a path
        self.path_plots = None
        if self.structures:
            page = QWidget()
            sl = QVBoxLayout(page)
            self.path_choice = QComboBox()
            for step_id, _ in self.structures:
                self.path_choice.addItem(step_id)
            if len(self.structures) > 1:
                sl.addWidget(self.path_choice)
            self.path_plots = pg.GraphicsLayoutWidget()
            sl.addWidget(self.path_plots, 1)
            self.path_note = QLabel(wordWrap=True)
            sl.addWidget(self.path_note)
            self.tabs.addTab(page, "Band structure")
            self.path_choice.currentIndexChanged.connect(self._draw_path)

        # The report, as text
        page = QWidget()
        dl = QVBoxLayout(page)
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.text.setPlainText("\n".join(report(sb) + extra))
        dl.addWidget(self.text)
        self.tabs.addTab(page, "Details")

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.addButton("Save image…", QDialogButtonBox.ActionRole)
        self.save_button.setToolTip("The plot in view, as PNG or SVG")
        self.save_button.clicked.connect(lambda: self.save_image())
        self.fit_button = buttons.addButton("Fit the project's windows", QDialogButtonBox.ActionRole)
        self.fit_button.setToolTip("Grow the yambo inputs' QPkrange and BSEBands of steps not run yet to "
                                   "whole degenerate sets at the gap's edges")
        self.fit_button.clicked.connect(self._fit)
        self.fit_button.setEnabled(fit is not None and sb.edge_sets() is not None)
        layout.addWidget(buttons)

        self._draw_ladder()
        if self.structures:
            self._draw_path()
        self.energy.valueChanged.connect(self._window)
        self.gw.currentIndexChanged.connect(self._show_gw)
        if sb.save.direct is not None:
            self.energy.setValue(round(sb.save.direct.value + 1.0, 1))
        else:
            self.energy.setEnabled(False)
            self.window_label.setText("No windows: the occupied bands are not known.")
        if self.gws:
            self.gw.setCurrentIndex(1)
        self._summarise()
        self.resize(1000, 780)

    # -- the summary ------------------------------------------------------------------------

    def _name(self, k: int) -> str:
        """A k-point's name (k from 1)."""
        name = self.names[k - 1] if 0 < k <= len(self.names) else f"k{k}"
        return name if name.startswith("k") else f"{name} (k{k})"

    def _summarise(self):
        s = self.sb.save
        parts = [f"SAVE of {self.save_step}"] if self.save_step else []
        if s.indirect is not None and not s.metal:
            kind = "direct" if s.indirect.direct else "indirect"
            where = (f"at {self._name(s.indirect.k_valence)}" if s.indirect.direct else
                     f"{self._name(s.indirect.k_valence)} → {self._name(s.indirect.k_conduction)}")
            parts.append(f"<b>DFT gap {s.indirect.value:.2f} eV</b>, {kind}, {where}")
            if s.direct is not None and not s.indirect.direct:
                parts.append(f"lowest direct {s.direct.value:.2f} eV at {self._name(s.direct.k_valence)}")
        elif s.metal:
            parts.append("<b>No gap on the grid</b> (a metal, or a wrong occupation)")
        qp = self._qp()
        if qp is not None and qp.indirect is not None:
            parts.append(f"<b><span style='color:{GW.name()}'>GW gap {qp.indirect[1].value:.2f} eV</span></b>"
                         + (f", direct {qp.direct[1].value:.2f} eV" if qp.direct else ""))
        parts.append(f"{s.occupied} occupied of {s.nbnd} bands" if s.occupied else f"{s.nbnd} bands")
        parts.append(f"{s.nk_ibz} k-points" + (f" ({self.sb.nk_bz} in the whole zone)" if self.sb.nk_bz else ""))
        self.summary.setText(" · ".join(parts))

    # -- the bands at the k-points ----------------------------------------------------------

    def _top(self) -> float:
        e = self.sb.save.energies
        return min(float(e.max()), UPPER_EV)

    def _draw_ladder(self):
        s = self.sb.save
        e = s.energies[0]                               # (k, band); one spin channel drawn
        nk, nb = e.shape
        occ = s.occupied or 0
        self.ladder = self.plots.addPlot(row=0, col=0)
        self.ladder.setLabel("left", "Energy (eV), zero at the valence top" if s.occupied else "Energy (eV)")
        self.ladder.setLabel("bottom", "irreducible k-points")
        self.ladder.getAxis("bottom").setTicks([[(k, self.names[k - 1] if k <= len(self.names) else f"k{k}")
                                                 for k in range(1, nk + 1)]])
        self.ladder.setMouseEnabled(x=False, y=True)
        self.band_items = []
        for b in range(nb):
            xs = np.ravel([[k + 1 - BAR, k + 1 + BAR] for k in range(nk)])
            ys = np.repeat(e[:, b], 2)
            item = pg.PlotDataItem(xs, ys, connect="pairs")
            self.ladder.addItem(item)
            self.band_items.append((item, VALENCE if b < occ else CONDUCTION))
        self._style_bands(None)
        self.gap_items = []
        if s.indirect is not None and not s.metal:
            self.gap_items = _gap_marks(self.ladder, 0.0, s.indirect.value, s.indirect.k_valence,
                                        s.indirect.k_conduction, self.palette().color(QPalette.Text),
                                        f"{s.indirect.value:.2f} eV", at=0.3)
        self.gw_items = []
        w, d = self.sb.dos()
        gap = s.indirect.value if s.indirect is not None and not s.metal else None
        self.dos = _dos_plot(self.plots, w, d, gap, 0, 1)
        self.dos.setYLink(self.ladder)
        _align(self.ladder, self.dos)
        self.plots.ci.layout.setColumnStretchFactor(0, 3)
        self.plots.ci.layout.setColumnStretchFactor(1, 1)
        self.ladder.setXRange(0.4, nk + 0.6, padding=0)
        self.ladder.setYRange(float(e.min()) - 0.5, self._top(), padding=0)
        _style(self.plots, [self.ladder, self.dos])
        self.plots.scene().sigMouseMoved.connect(self._hovered)

    def _style_bands(self, window):
        """Bold the bands in `window` (first, last), the others faded."""
        for b, (item, colour) in enumerate(self.band_items, start=1):
            inside = window is None or window[0] <= b <= window[1]
            item.setPen(pg.mkPen(_colour(colour, 255 if inside else 80), width=3 if inside else 1.5))

    def _window(self, energy: float):
        w = self.sb.window(energy)
        if w is None:
            self.window_label.setText("no transition that low")
            self._style_bands(None)
            return
        size = f"; BSE size {w.valence} × {w.conduction} × {w.nk_bz} = {w.bse_size}" if w.bse_size else ""
        splits = self.sb.splits(w.first, w.last)
        self.window_label.setText(f"bands {w.first}–{w.last} ({w.valence} valence, {w.conduction} conduction)"
                                  + size + ("; <b>splits degenerate bands</b>" if splits else ""))
        self.window_label.setToolTip("\n".join(splits))
        self._style_bands((w.first, w.last))

    def _qp(self):
        step = self.gw.currentData()
        return next((qp for step_id, qp in self.gws if step_id == step), None)

    def _gw_levels(self, qp) -> dict:
        """{(band, k): GW energy}, zero at the GW's own valence top: each DFT
        level moved by its correction, then all by the valence top's (a GW's
        corrections are known up to a common shift; the gaps are what count)."""
        e = self.sb.save.energies[0]
        moved = {(int(b), int(k)): e[int(k) - 1, int(b) - 1] + (v - d)
                 for b, k, d, v in zip(qp.band_of, qp.k_of, qp.dft, qp.qp)
                 if 0 < k <= e.shape[0] and 0 < b <= e.shape[1]}
        occ = self.sb.save.occupied
        valence = [v for (b, _), v in moved.items() if b == occ]
        top = max(valence) if occ and valence else 0.0
        return {key: v - top for key, v in moved.items()}

    def _show_gw(self):
        """The chosen GW's energies over the DFT ones, each with its own valence
        top at zero."""
        for item in self.gw_items:
            self.ladder.removeItem(item)
        self.gw_items = []
        qp = self._qp()
        if qp is not None:
            levels = self._gw_levels(qp)
            xs, ys = [], []
            for (b, k), value in levels.items():
                xs += [k - BAR * 0.7, k + BAR * 0.7]
                ys += [value, value]
            bars = pg.PlotDataItem(xs, ys, connect="pairs", pen=pg.mkPen(GW, width=3))
            self.ladder.addItem(bars)
            self.gw_items.append(bars)
            if qp.indirect is not None and self.sb.save.occupied:
                g = qp.indirect[1]
                self.gw_items += _gap_marks(self.ladder, 0.0, g.value, g.k_valence, g.k_conduction,
                                            GW, f"GW {g.value:.2f} eV", shade=False, at=0.7)
        self.ladder.setLabel("bottom", "irreducible k-points" + (
            f" · <span style='color:{GW.name()}'>green: the GW of {self.gw.currentData()}, "
            "its own valence top at 0</span>" if qp is not None else ""))
        self._summarise()

    def _hovered(self, pos):
        if not self.ladder.sceneBoundingRect().contains(pos):
            return
        point = self.ladder.vb.mapSceneToView(pos)
        k = int(round(point.x()))
        e = self.sb.save.energies[0]
        if not 1 <= k <= e.shape[0]:
            self.hover.setText(" ")
            return
        y0, y1 = self.ladder.vb.viewRange()[1]
        b = int(np.abs(e[k - 1] - point.y()).argmin()) + 1
        if abs(e[k - 1, b - 1] - point.y()) > 0.02 * (y1 - y0):
            self.hover.setText(f"{self._name(k)}")
            return
        text = f"{self._name(k)}, band {b}: {e[k - 1, b - 1]:.3f} eV"
        qp = self._qp()
        if qp is not None:
            level = self._gw_levels(qp).get((b, k))
            if level is not None:
                text += f" · GW {level:.3f} eV"
        self.hover.setText(text)

    # -- the band structure along a path ----------------------------------------------------

    def _draw_path(self):
        self.path_plots.clear()
        step_id, bs = self.structures[self.path_choice.currentIndex()]
        plot = self.path_plots.addPlot(row=0, col=0)
        plot.setLabel("left", f"Energy (eV), zero at {bs.zero}")
        plot.setLabel("bottom", "path through the Brillouin zone")
        plot.getAxis("bottom").setTicks([[(x, label) for x, label in bs.ticks]])
        plot.setMouseEnabled(x=False, y=True)
        text = self.palette().color(QPalette.Text)
        for x, _ in bs.ticks:
            plot.addItem(pg.InfiniteLine(x, angle=90, pen=pg.mkPen(_colour(text, 70), width=1)))
        connect = np.ones(len(bs.x), dtype=bool)
        for i in bs.jumps:
            connect[i - 1] = False                      # no line across a jump between sections
        occ = bs.occupied or 0
        for spin in range(bs.energies.shape[0]):
            for b in range(bs.energies.shape[2]):
                colour = VALENCE if b < occ else CONDUCTION
                plot.addItem(pg.PlotDataItem(bs.x, bs.energies[spin, :, b], connect=connect,
                                             pen=pg.mkPen(colour, width=2,
                                                          style=Qt.SolidLine if spin == 0 else Qt.DashLine)))
        gap = bs.gap
        if gap is not None and gap[0] > 0:
            _gap_marks(plot, 0.0, gap[0], gap[1], gap[2], text, f"{gap[0]:.2f} eV")
        plot.setXRange(float(bs.x[0]), float(bs.x[-1]), padding=0.01)   # the end points' names shown
        plot.setYRange(float(bs.energies.min()) - 0.5, min(float(bs.energies.max()), UPPER_EV), padding=0)
        w, d = self.sb.dos()
        s = self.sb.save
        dos = _dos_plot(self.path_plots, w, d, s.indirect.value if s.indirect and not s.metal else None, 0, 1)
        dos.setYLink(plot)
        _align(plot, dos)
        self.path_dos = dos
        self.path_plots.ci.layout.setColumnStretchFactor(0, 3)
        self.path_plots.ci.layout.setColumnStretchFactor(1, 1)
        _style(self.path_plots, [plot, dos])
        self.path_plot = plot
        note = (f"{step_id}: {len(bs.x)} k-points along the path; the density of states beside it is "
                "the SAVE's, on its grid.")
        if gap is not None and gap[0] > 0:
            note += f" Gap on the path {gap[0]:.2f} eV"
            grid = s.indirect.value if s.indirect is not None and not s.metal else None
            if grid is not None and gap[0] < grid - 0.02:
                note += (f", below the {grid:.2f} eV on the SAVE's grid: the band edges lie between "
                         "the grid's k-points")
            note += "."
        self.path_note.setText(note)

    # -- actions ----------------------------------------------------------------------------

    def save_image(self, path: str | None = None):
        """The plot in view as an image (PNG, or SVG by its name)."""
        widget = self.path_plots if self.tabs.currentIndex() == 1 and self.path_plots is not None else self.plots
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Save image", "bands.png", "Images (*.png *.svg)")
            if not path:
                return
        if path.lower().endswith(".svg"):
            # Qt's own SVG writer: pyqtgraph's SVG exporter fails on the paths this
            # Qt version writes (0.14 with PySide6 6.8+)
            from PySide6.QtCore import QRectF, QSize
            from PySide6.QtGui import QPainter
            from PySide6.QtSvg import QSvgGenerator
            scene = widget.scene()
            rect = scene.sceneRect()
            generator = QSvgGenerator()
            generator.setFileName(path)
            generator.setSize(QSize(int(rect.width()), int(rect.height())))
            generator.setViewBox(QRectF(0, 0, rect.width(), rect.height()))
            generator.setTitle(self.windowTitle())
            painter = QPainter(generator)
            scene.render(painter, QRectF(0, 0, rect.width(), rect.height()), rect)
            painter.end()
            return
        from pyqtgraph import exporters
        exporters.ImageExporter(widget.scene()).export(path)

    def _fit(self):
        lines = self.fit()
        self.text.appendPlainText("\n" + "\n".join(lines))
        self.tabs.setCurrentIndex(self.tabs.count() - 1)
