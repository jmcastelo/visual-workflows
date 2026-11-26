"""The nodes a job must get (sbatch --nodelist): typed, or chosen from the
partition's nodes that Discover recorded in the profile."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout,
                               QWidget)

from ..core.jobspec import node_names
from ..core.profile import ClusterProfile


class NodeListEdit(QWidget):
    """A node list as text ("compute01 compute02", or Slurm's "compute[01-02]")
    with Choose… to tick the partition's nodes. `partition` gives the
    partition chosen in the form; `nodes`, the form's node count, raised to
    the nodes chosen."""

    changed = Signal()

    def __init__(self, profile: ClusterProfile, partition: Callable[[], str],
                 nodes: QSpinBox | None = None, nodelist=(), parent=None):
        super().__init__(parent)
        self.profile, self.partition, self.nodes = profile, partition, nodes
        self.text = QLineEdit(" ".join(nodelist), placeholderText="any (Slurm chooses)")
        self.text.setToolTip("Nodes the job must get, by name (compute01 compute02) or as "
                             "Slurm's ranges (compute[01-02]); with fewer than the job's "
                             "nodes, Slurm chooses the others")
        self.text.textChanged.connect(self.changed)
        self.choose = QPushButton("Choose…")
        self.choose.setToolTip("Tick the partition's nodes (as Discover found them)")
        self.choose.clicked.connect(self.choose_nodes)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.text, 1)
        row.addWidget(self.choose)

    def nodelist(self) -> list[str]:
        """The node list as written, one word per entry (commas are read later,
        with Slurm's ranges: core.jobspec.node_names)."""
        return self.text.text().split()

    def available(self) -> list[str]:
        p = self.profile.partitions.get(self.partition().strip())
        return list(p.nodes) if p is not None else []

    def choose_nodes(self):
        dialog = NodeChooser(self.partition().strip(), self.available(),
                             node_names(self.nodelist()), self)
        if dialog.exec() != QDialog.Accepted:
            return
        chosen = dialog.chosen()
        self.text.setText(" ".join(chosen))
        if self.nodes is not None and len(chosen) > self.nodes.value():
            self.nodes.setValue(len(chosen))


class NodeChooser(QDialog):
    def __init__(self, partition: str, nodes: list[str], chosen: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nodes to use")
        layout = QVBoxLayout(self)
        if nodes:
            note = (f"The nodes of {partition}. Ticked nodes are given to the job; with fewer "
                    "than the job's nodes, Slurm chooses the others.")
        else:
            note = (f"The profile does not list {partition or 'the partition'}'s nodes: "
                    "Discover in the Cluster tab fills them in. Type their names instead.")
        layout.addWidget(QLabel(note, wordWrap=True))
        self.list = QListWidget()
        for name in nodes + [n for n in chosen if n not in nodes]:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in chosen else Qt.Unchecked)
            if name not in nodes:
                item.setToolTip(f"Not one of {partition}'s nodes")
            self.list.addItem(item)
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        clear = buttons.addButton("None", QDialogButtonBox.ResetRole)
        clear.setToolTip("Untick all: Slurm chooses every node")
        clear.clicked.connect(lambda: [self.list.item(i).setCheckState(Qt.Unchecked)
                                       for i in range(self.list.count())])
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]
