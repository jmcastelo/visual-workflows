"""Browsing the cluster's files: preview, follow and download."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path, PurePosixPath

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.monitor import LogFollower
from ..core.runner import expand_remote_path
from ..core.transfer import choose, fetch, human, plan, scan
from .asyncqt import launch, show_error

PREVIEW_BYTES = 64 * 1024
FOLLOW_INTERVAL_MS = 10_000


class FilesView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.cwd = ""
        self.follower: LogFollower | None = None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.up_button = QPushButton("Up")
        self.up_button.clicked.connect(lambda: self.go(str(PurePosixPath(self.cwd).parent)))
        self.home_button = QPushButton("Home")
        self.home_button.clicked.connect(lambda: launch(self, self.go_home(), "Files"))
        self.scratch_button = QPushButton("Scratch")
        self.scratch_button.clicked.connect(lambda: launch(self, self.go_profile_folder("scratch"), "Files"))
        self.projects_button = QPushButton("Projects")
        self.projects_button.setToolTip("The cluster's projects folder, where projects go")
        self.projects_button.clicked.connect(lambda: launch(self, self.go_profile_folder("work_root"), "Files"))
        self.download_button = QPushButton("Download…")
        self.download_button.clicked.connect(self._download_clicked)
        for w in (self.up_button, self.home_button, self.scratch_button, self.projects_button,
                  self.download_button):
            bar.addWidget(w)
        bar.addStretch()
        layout.addLayout(bar)
        where = QHBoxLayout()
        where.addWidget(QLabel("Folder:"))
        self.path = QLineEdit(placeholderText="Type a folder on the cluster and press Enter")
        self.path.setToolTip("The folder shown below. Type another one and press Enter.")
        self.path.returnPressed.connect(lambda: self.go(self.path.text().strip()))
        where.addWidget(self.path)
        layout.addLayout(where)

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Size", "Modified"])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.itemDoubleClicked.connect(self._open)
        self.tree.itemSelectionChanged.connect(self._selection)
        split.addWidget(self.tree)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.file_label = QLabel(textInteractionFlags=Qt.TextSelectableByMouse)
        self.follow = QCheckBox("Follow (re-read every 10 s)")
        self.follow.toggled.connect(self._follow)
        top.addWidget(self.file_label, 1)
        top.addWidget(self.follow)
        rl.addLayout(top)
        self.preview = QPlainTextEdit(readOnly=True)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.preview.setMaximumBlockCount(20000)
        rl.addWidget(self.preview)
        split.addWidget(right)
        split.setSizes([400, 600])
        layout.addWidget(split, 1)          # the list and preview take the room
        self.status = QLabel()
        layout.addWidget(self.status)
        self.timer = QTimer(self, interval=FOLLOW_INTERVAL_MS)
        self.timer.timeout.connect(lambda: launch(self, self._read_more(), "Follow"))
        self.set_session(None)

    def set_session(self, session):
        self.session = session
        self.cwd = ""
        self.path.clear()
        self.tree.clear()
        self.preview.clear()
        self.file_label.clear()
        self.follow.setChecked(False)
        self.setEnabled(session is not None)
        self._update_places()
        if session is not None and self.isVisible():
            launch(self, self.go_home(), "Files")

    def _update_places(self):
        """Scratch only where the profile names one (it may have been edited)."""
        scratch = self.session.profile.scratch if self.session else ""
        self.scratch_button.setEnabled(bool(scratch))
        self.scratch_button.setToolTip(
            f"The cluster's scratch folder: {scratch}" if scratch else
            "No scratch folder in the cluster's profile: set it in the Cluster tab "
            "(e.g. /storage/scratch/<group>/$USER)")

    def showEvent(self, event):
        # Opened with nothing shown yet: start at the home folder
        super().showEvent(event)
        self._update_places()
        if self.session is not None and not self.cwd:
            launch(self, self.go_home(), "Files")

    def go(self, path: str):
        launch(self, self.list_dir(path), "Files")

    async def go_home(self):
        transport, _ = await self.session.ready()
        await self.list_dir(await transport.home())

    async def go_profile_folder(self, field: str):
        """The profile's scratch or projects folder, with $USER, $HOME… expanded
        on the cluster."""
        path = getattr(self.session.profile, field)
        if not path:
            raise ValueError("No such folder in the cluster's profile: set it in the Cluster tab.")
        transport, _ = await self.session.ready()
        await self.list_dir(await expand_remote_path(transport, path))

    async def list_dir(self, path: str):
        transport, _ = await self.session.ready()
        entries = await transport.listdir(path)
        self.cwd = path
        self.path.setText(path)
        self.tree.clear()
        for e in sorted(entries, key=lambda e: (not e.is_dir, e.name)):
            item = QTreeWidgetItem([e.name + ("/" if e.is_dir else ""),
                                    "" if e.is_dir else human(e.size),
                                    datetime.fromtimestamp(e.mtime).strftime("%Y-%m-%d %H:%M")])
            item.setData(0, Qt.UserRole, (e.path, e.is_dir, e.size))
            item.setTextAlignment(1, Qt.AlignRight)
            self.tree.addTopLevelItem(item)
        self.status.setText(f"{len(entries)} entries")

    def _open(self, item):
        path, is_dir, _ = item.data(0, Qt.UserRole)
        if is_dir:
            self.go(path)

    def _selected(self) -> list[tuple[str, bool, int]]:
        return [i.data(0, Qt.UserRole) for i in self.tree.selectedItems()]

    def _selection(self):
        sel = self._selected()
        self.follow.setChecked(False)
        if len(sel) == 1 and not sel[0][1]:
            launch(self, self.show_file(sel[0][0]), "Preview")
        else:
            self.file_label.setText(f"{len(sel)} selected" if sel else "")

    async def show_file(self, path: str):
        self.file_label.setText(path)
        transport, _ = await self.session.ready()
        self.follower = LogFollower(transport, path, tail=PREVIEW_BYTES)
        text = await self.follower.read_new()
        self.preview.setPlainText(text)
        self.preview.moveCursor(self.preview.textCursor().MoveOperation.End)

    def _follow(self, on: bool):
        if on and self.follower is not None:
            self.timer.start()
        else:
            self.timer.stop()

    async def _read_more(self):
        if self.follower is None:
            return
        text = await self.follower.read_new()
        if text:
            self.preview.moveCursor(self.preview.textCursor().MoveOperation.End)
            self.preview.insertPlainText(text)

    def _download_clicked(self):
        # The folder is asked before the task starts: no dialog inside a task
        if not self._selected():
            show_error(self, "Download", "Select files or folders to download first.")
            return
        target = QFileDialog.getExistingDirectory(self, "Download into")
        if target:
            launch(self, self.download(target), "Download")

    async def download(self, target: str):
        sel = self._selected()
        if not sel:
            raise ValueError("Select files or folders to download first.")
        transport, _ = await self.session.ready()
        total = 0
        for path, is_dir, size in sel:
            name = PurePosixPath(path).name
            if is_dir:
                items = plan(choose(await scan(transport, path), "all"), Path(target) / name)
                for i, item in enumerate(items):
                    if not item.have:
                        self.status.setText(f"{name}: {i + 1}/{len(items)} {item.file.path}")
                        await fetch(transport, path, item)
                        total += item.file.size
            else:
                self.status.setText(f"Downloading {name} ({human(size)})")
                await transport.get(path, Path(target) / name)
                total += size
        self.status.setText(f"Downloaded {human(total)} into {target}")
