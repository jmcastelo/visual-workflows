"""The main window: a cluster selector and four tabs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QFileDialog, QLabel, QMainWindow, QMessageBox,
                               QTabWidget, QToolBar)

from .. import __version__
from ..core.paths import cluster_profiles_dir
from ..core.profile import ClusterProfile, ProfileStore
from ..core.ssh import ConnectionSettings
from ..identity import APP_NAME
from .asyncqt import confirm, launch, settings, show_error
from .cluster_editor import ClusterEditor
from .files_view import FilesView
from .jobs_view import JobsView
from .project_view import ProjectView
from .session import Session

MAX_RECENT = 10


class MainWindow(QMainWindow):
    def __init__(self, profiles_dir: Path | None = None):
        super().__init__()
        from .sizing import install
        install()                                   # every window's widgets sized to their contents
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 800)
        self.store = ProfileStore(profiles_dir or cluster_profiles_dir())
        self.profiles: list[ClusterProfile] = []
        self.sessions: dict[str, Session] = {}

        bar = QToolBar("Cluster")
        bar.setObjectName("clusterBar")
        self.addToolBar(bar)
        bar.addWidget(QLabel(" Cluster: "))
        self.cluster_box = QComboBox(minimumContentsLength=16)
        self.cluster_box.currentIndexChanged.connect(self._cluster_chosen)
        bar.addWidget(self.cluster_box)
        self.connect_action = QAction("Connect", self)
        self.connect_action.triggered.connect(self._toggle_connection)
        bar.addAction(self.connect_action)
        self.state_label = QLabel()
        bar.addWidget(self.state_label)

        self.tabs = QTabWidget()
        self.editor = ClusterEditor(self.session_for_profile)
        self.editor.saved.connect(self._profile_saved)
        self.jobs = JobsView()
        self.files = FilesView()
        self.project = ProjectView(self.session_for_cluster, self.current_cluster_name,
                                   lambda: [p for p in self.profiles if p.path is not None])
        self.project.projectOpened.connect(self._project_opened)
        for w, name in ((self.editor, "Cluster"), (self.jobs, "Jobs"), (self.files, "Files"),
                        (self.project, "Project")):
            self.tabs.addTab(w, name)
        self.setCentralWidget(self.tabs)
        self._menus()
        self.reload_profiles()

    # -- menus ----------------------------------------------------------------------

    def _menus(self):
        m = self.menuBar().addMenu("&File")
        # Lambdas: Qt would pass `checked` to slots with optional arguments
        for text, slot, key in (("New project…", lambda: launch(self, self.project.new_project(),
                                                                "New project"), QKeySequence.New),
                                ("Open project…", lambda: self.project.open_dialog(), QKeySequence.Open)):
            a = m.addAction(text, slot)
            a.setShortcut(key)
        self.recent_menu = m.addMenu("Recent projects")
        self._fill_recent()
        m.addSeparator()
        m.addAction("Quit", self.close).setShortcut(QKeySequence.Quit)
        c = self.menuBar().addMenu("&Clusters")
        c.addAction("New cluster", lambda: self.new_cluster())
        c.addAction("Import profile…", lambda: self.import_profile())
        c.addAction("Export profile to share…", lambda: self.export_profile())
        c.addSeparator()
        c.addAction("Remove cluster", lambda: launch(self, self.remove_cluster(), "Remove cluster"))
        h = self.menuBar().addMenu("&Help")
        h.addAction("About", lambda: QMessageBox.about(
            self, APP_NAME, f"{APP_NAME} {__version__}\n\nQuantum ESPRESSO and Yambo on "
                            "Slurm clusters."))

    def _fill_recent(self):
        self.recent_menu.clear()
        for path in settings().value("recentProjects", [], list) or []:
            self.recent_menu.addAction(path, lambda p=path: self._open_recent(p))
        self.recent_menu.setEnabled(not self.recent_menu.isEmpty())

    def _open_recent(self, path):
        try:
            self.project.open_project(path)
        except Exception as e:
            show_error(self, "Open project", str(e))

    def _project_opened(self, project):
        recent = [str(project.path)] + [p for p in settings().value("recentProjects", [], list) or []
                                        if p != str(project.path)]
        settings().setValue("recentProjects", recent[:MAX_RECENT])
        self._fill_recent()
        i = self.cluster_box.findText(project.cluster)
        if i >= 0:
            self.cluster_box.setCurrentIndex(i)
        self.tabs.setCurrentWidget(self.project)

    # -- clusters --------------------------------------------------------------------

    def reload_profiles(self, select: str | None = None):
        self.profiles, errors = self.store.load_all()
        current = select or self.cluster_box.currentText()
        self.cluster_box.blockSignals(True)
        self.cluster_box.clear()
        self.cluster_box.addItems([p.name for p in self.profiles])
        self.cluster_box.blockSignals(False)
        i = self.cluster_box.findText(current)
        self.cluster_box.setCurrentIndex(i if i >= 0 else (0 if self.profiles else -1))
        self._cluster_chosen()
        if errors:
            show_error(self, "Cluster profiles", "Some profiles cannot be read:\n\n" + "\n".join(errors))

    def current_profile(self) -> ClusterProfile | None:
        i = self.cluster_box.currentIndex()
        return self.profiles[i] if 0 <= i < len(self.profiles) else None

    def current_cluster_name(self) -> str | None:
        p = self.current_profile()
        return p.name if p else None

    def session_for_profile(self, profile: ClusterProfile) -> Session:
        session = self.sessions.get(profile.name)
        if session is None:
            session = self.sessions[profile.name] = Session(profile, self)
            session.stateChanged.connect(self._show_state)
        return session

    def session_for_cluster(self, name: str) -> Session:
        for p in self.profiles:
            if p.name.lower() == name.lower():
                return self.session_for_profile(p)
        raise ValueError(f"No cluster named {name!r}: add it, or import its profile.")

    def _cluster_chosen(self):
        profile = self.current_profile()
        self.editor.set_profile(profile)
        session = self.session_for_profile(profile) if profile else None
        self.jobs.set_session(session)
        self.files.set_session(session)
        self._show_state()

    def _show_state(self):
        profile = self.current_profile()
        session = self.sessions.get(profile.name) if profile else None
        on = session is not None and session.connected
        self.connect_action.setText("Disconnect" if on else "Connect")
        self.connect_action.setEnabled(profile is not None)
        self.state_label.setText(f"  connected to {profile.connection.host}" if on else
                                 "  not connected" if profile else "  no clusters yet: Clusters ▸ New cluster")

    def _toggle_connection(self):
        profile = self.current_profile()
        if profile is None:
            return
        session = self.session_for_profile(profile)
        if session.connected:
            launch(self, session.close(), "Disconnect")
        else:
            self.state_label.setText("  connecting…")
            launch(self, self._connect(session), "Connect")

    async def _connect(self, session):
        try:
            await session.connect()
        finally:
            self._show_state()

    def _profile_saved(self, profile: ClusterProfile):
        old = self.current_profile()
        if old is None or old.path is None:
            self.store.add(profile)
        else:
            profile.save(old.path)
        if old is not None and old.name in self.sessions:
            session = self.sessions.pop(old.name)
            session.profile_changed(profile)
            self.sessions[profile.name] = session
        self.reload_profiles(select=profile.name)

    def new_cluster(self):
        name, n = "New cluster", 2
        while any(p.name == name for p in self.profiles):
            name, n = f"New cluster {n}", n + 1
        profile = ClusterProfile(name=name, connection=ConnectionSettings(host=""))
        self.profiles.append(profile)
        self.cluster_box.blockSignals(True)
        self.cluster_box.addItem(name)
        self.cluster_box.setCurrentIndex(self.cluster_box.count() - 1)
        self.cluster_box.blockSignals(False)
        self._cluster_chosen()
        self.tabs.setCurrentWidget(self.editor)
        self.editor.host.setFocus()

    def import_profile(self, path: str | None = None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Import cluster profile", "",
                                                  "Cluster profiles (*.cluster.toml *.toml)")
            if not path:
                return
        try:
            profile = self.store.import_file(path)
        except Exception as e:
            show_error(self, "Import profile", str(e))
            return
        self.reload_profiles(select=profile.name)
        if not profile.connection.username:
            self.editor.report.setText("Imported. Fill in your username (and key file), then Save.")

    def export_profile(self, path: str | None = None):
        profile = self.current_profile()
        if profile is None:
            return
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export profile to share",
                                                  profile.path.name if profile.path else "",
                                                  "Cluster profiles (*.cluster.toml)")
            if not path:
                return
        profile.export(path)
        self.statusBar().showMessage(f"Exported to {path}, without your username and key file.", 8000)

    async def remove_cluster(self, ask_first: bool = True):
        profile = self.current_profile()
        if profile is None:
            return
        if ask_first and not await confirm(self, "Remove cluster",
                                           f"Remove the profile of {profile.name}?"):
            return
        session = self.sessions.pop(profile.name, None)
        if session:
            await session.close()
        self.store.delete(profile)
        self.reload_profiles()

    async def close_sessions(self):
        for s in list(self.sessions.values()):
            await s.close()

    def closeEvent(self, event):
        # Timers of the tabs (a log followed, a project watched) stop with the
        # window: firing later, they would start work with nothing to run it
        for timer in self.findChildren(QTimer):
            timer.stop()
        for s in list(self.sessions.values()):
            if s.transport is not None:
                launch(None, s.close())
        super().closeEvent(event)

