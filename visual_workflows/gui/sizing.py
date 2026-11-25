"""Widgets sized to their contents, everywhere.

Qt's forms and rows stretch every field to the space there is: a spinner for
a one-digit number as wide as the window, a row of buttons spread out. Here,
once per window, when it is first shown: spinners, combo boxes and buttons
keep the width their contents need; text fields, text views and tables still
stretch; a row left with only fixed widgets gets its spare room at its end.
Widgets inside tables (cell editors) are left as they are.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication,
                               QBoxLayout, QComboBox, QFormLayout, QLabel, QLineEdit, QSizePolicy,
                               QWidget)

_DONE = "_vwf_sized"
# Kinds that keep their contents' width
_FIXED = (QAbstractSpinBox, QComboBox, QAbstractButton)


def _in_item_view(widget: QWidget) -> bool:
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractItemView):
            return True
        parent = parent.parentWidget()
    return False


def _stretches(widget: QWidget) -> bool:
    """Whether a widget in a row is meant to take its spare room: anything
    but the fixed kinds and labels (text fields, views, tables, plots)."""
    if isinstance(widget, QLineEdit):
        return True
    if isinstance(widget, (*_FIXED, QLabel)):
        return bool(widget.property("stretch"))
    return widget.sizePolicy().horizontalPolicy() not in (QSizePolicy.Fixed, QSizePolicy.Maximum)


def fit_to_contents(root: QWidget) -> None:
    """Size `root`'s spinners, combo boxes and buttons to their contents, and
    let rows of them keep their spare room at the end."""
    for w in root.findChildren(QWidget):
        if not isinstance(w, _FIXED) or _in_item_view(w) or w.property("stretch"):
            continue
        if isinstance(w, QComboBox):
            w.setSizeAdjustPolicy(QComboBox.AdjustToContents)
            if w.isEditable() and w.minimumContentsLength() == 0:
                hint = w.lineEdit().placeholderText() if w.lineEdit() else ""
                w.setMinimumContentsLength(max(12, len(hint)))   # room to type in, and its hint
        policy = w.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Fixed)
        w.setSizePolicy(policy)
    for layout in [root.layout()] + [w.layout() for w in root.findChildren(QWidget)]:
        if layout is not None:
            _tidy(layout)


def _tidy(layout) -> None:
    if isinstance(layout, QFormLayout):
        layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item.layout() is not None:
            _tidy(item.layout())
    if not isinstance(layout, QBoxLayout) or layout.direction() not in (QBoxLayout.LeftToRight,
                                                                         QBoxLayout.RightToLeft):
        return
    items = [layout.itemAt(i) for i in range(layout.count())]
    if any(it.spacerItem() is not None for it in items) or \
            any(layout.stretch(i) > 0 for i in range(layout.count())):
        return                                          # its spare room is placed already
    widgets = [it.widget() for it in items if it.widget() is not None]
    nested = [it.layout() for it in items if it.layout() is not None]
    if widgets and not nested and not any(_stretches(w) for w in widgets):
        layout.addStretch(1)


class _Sizer(QObject):
    """Sizes each window once, when it is first shown."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() \
                and not obj.property(_DONE):
            obj.setProperty(_DONE, True)
            fit_to_contents(obj)
        return False


_sizer: _Sizer | None = None


def install() -> None:
    """Size every window the application shows from now on (once each)."""
    global _sizer
    app = QApplication.instance()
    if app is None or _sizer is not None:
        return
    _sizer = _Sizer(app)
    app.installEventFilter(_sizer)
