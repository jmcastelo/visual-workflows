"""The user's Slurm jobs on the selected cluster."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.monitor import DEFAULT_INTERVAL
from .asyncqt import confirm, launch

COLUMNS = ["Job", "Name", "Partition", "State", "Reason", "Nodes", "CPUs", "Started", "Result"]


def when(t) -> str:
    if t is None:
        return ""
    if t.tzinfo is not None:
        t = t.astimezone()
    return t.strftime("%Y-%m-%d %H:%M")


def result_text(exit_code, signal) -> str:
    if exit_code is not None:
        return f"exit {exit_code}"
    return f"signal {signal}" if signal is not None else ""


class JobsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(lambda: launch(self, self.refresh(), "Jobs"))
        self.cancel_button = QPushButton("Cancel selected")
        self.cancel_button.clicked.connect(lambda: launch(self, self.cancel_selected(), "Cancel"))
        self.auto = QCheckBox(f"Every {DEFAULT_INTERVAL:.0f} s")
        self.auto.toggled.connect(self._auto)
        self.status = QLabel()
        for w in (self.refresh_button, self.cancel_button, self.auto):
            bar.addWidget(w)
        bar.addStretch()
        bar.addWidget(self.status)
        layout.addLayout(bar)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        self.timer = QTimer(self, interval=int(DEFAULT_INTERVAL * 1000))
        self.timer.timeout.connect(lambda: launch(self, self.refresh(), "Jobs"))
        self.set_session(None)

    def set_session(self, session):
        self.session = session
        self.table.setRowCount(0)
        self.status.setText("" if session else "No cluster selected")
        self.setEnabled(session is not None)

    def _auto(self, on: bool):
        if on:
            self.timer.start()
            launch(self, self.refresh(), "Jobs")
        else:
            self.timer.stop()

    async def refresh(self):
        if self.session is None:
            return
        _, slurm = await self.session.ready()
        jobs = await slurm.jobs()
        self.table.setRowCount(0)
        for j in sorted(jobs.values(), key=lambda j: [int(x) for x in j.id.split("_")]):
            r = self.table.rowCount()
            self.table.insertRow(r)
            values = [j.id, j.name, j.partition, j.state.value, j.reason, j.nodes,
                      j.num_cpus if j.num_cpus is not None else "", when(j.start_time),
                      result_text(j.exit_code, j.signal)]
            for c, v in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self.status.setText(f"{len(jobs)} jobs · {datetime.now():%H:%M:%S}")

    def selected_ids(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).text() for r in rows]

    async def cancel_selected(self):
        ids = self.selected_ids()
        if not ids:
            raise ValueError("Select the jobs to cancel first.")
        if not await confirm(self, "Cancel jobs", f"Cancel job(s) {', '.join(ids)}?"):
            return
        _, slurm = await self.session.ready()
        await slurm.cancel(ids)
        await self.refresh()
