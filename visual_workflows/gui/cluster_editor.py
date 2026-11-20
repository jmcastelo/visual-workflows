"""Editing a cluster profile, testing the connection, discovering partitions
and modules, choosing and checking each program's modules."""

from __future__ import annotations

import copy
import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.modules import for_program, labels
from ..core.profile import MPI_LAUNCHERS, ClusterProfile, Partition, Program
from ..core.scheduler import access
from ..core.ssh import AUTH_METHODS, ConnectionSettings
from .asyncqt import ask, launch, show_error

PROGRAMS = {"qe": "Quantum ESPRESSO", "yambo": "Yambo"}
DEFAULT_EXECUTABLES = {"qe": "pw=pw.x ph=ph.x", "yambo": "p2y=p2y yambo=yambo ypp=ypp"}


def walltime(minutes: int | None) -> str:
    if minutes is None:
        return ""
    d, rest = divmod(minutes, 1440)
    h, m = divmod(rest, 60)
    return f"{d}-{h:02d}:{m:02d}:00" if d else f"{h:02d}:{m:02d}:00"


def _executables_text(exes: dict[str, str]) -> str:
    return " ".join(f"{k}={v}" for k, v in exes.items())


def _parse_executables(text: str) -> dict[str, str]:
    result = {}
    for word in text.split():
        key, _, exe = word.partition("=")
        result[key] = exe or key
    return result


class ModulePicker(QDialog):
    """Choose modules for a program from what the cluster has."""

    def __init__(self, parent, title: str, modules, selected: list[str]):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose the modules to load (Ctrl+click for several, e.g. "
                                "a library module and the program):"))
        self.list = QListWidget(selectionMode=QAbstractItemView.ExtendedSelection)
        names = labels(modules)
        for m in modules:
            self.list.addItem(f"{m.full}    —  {names[m.full]}" + ("  (Spack)" if m.from_spack else ""))
            item = self.list.item(self.list.count() - 1)
            item.setData(256, m.full)
            item.setSelected(m.full in selected)
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self) -> list[str]:
        return [self.list.item(i).data(256) for i in range(self.list.count())
                if self.list.item(i).isSelected()]


class ClusterEditor(QWidget):
    """Shows one profile; saved(profile) after a valid save."""

    saved = Signal(object)

    def __init__(self, session_for, parent=None):
        super().__init__(parent)
        self.session_for = session_for     # profile -> Session
        self.profile: ClusterProfile | None = None
        self._build()
        self.setEnabled(False)

    # -- layout --------------------------------------------------------------------

    def _build(self):
        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        outer.addLayout(top)

        conn = QGroupBox("Connection")
        f = QFormLayout(conn)
        self.name = QLineEdit()
        self.description = QLineEdit()
        self.host = QLineEdit(placeholderText="host name or ~/.ssh/config alias")
        self.port = QSpinBox(minimum=0, maximum=65535, specialValueText="default")
        self.username = QLineEdit(placeholderText="from ~/.ssh/config, or your local name")
        self.auth = QComboBox()
        self.auth.addItems(AUTH_METHODS)
        self.key_file = QLineEdit(placeholderText="empty: ssh-agent and default keys")
        browse = QPushButton("…", maximumWidth=30)
        browse.clicked.connect(self._browse_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_file)
        key_row.addWidget(browse)
        for label, w in (("Name", self.name), ("Description", self.description),
                         ("Host", self.host), ("Port", self.port), ("Username", self.username),
                         ("Login", self.auth)):
            f.addRow(label, w)
        f.addRow("Key file", key_row)
        top.addWidget(conn)

        place = QGroupBox("On the cluster")
        g = QFormLayout(place)
        self.work_root = QLineEdit()
        self.scratch = QLineEdit(placeholderText="e.g. /storage/scratch/<group>/$USER "
                                                 "(for the Files tab)")
        self.python = QLineEdit()
        self.account = QLineEdit(placeholderText="only if the cluster needs one")
        self.qos = QLineEdit()
        self.module_use = QPlainTextEdit(placeholderText="Extra module folders, one per line "
                                                         "(e.g. Spack's)")
        self.module_use.setMaximumHeight(70)
        for label, w in (("Projects folder", self.work_root), ("Scratch", self.scratch),
                         ("Python", self.python), ("Account", self.account), ("QOS", self.qos),
                         ("Module folders", self.module_use)):
            g.addRow(label, w)
        top.addWidget(place)

        parts = QGroupBox("Partitions")
        pl = QVBoxLayout(parts)
        self.partitions = QTableWidget(0, 5)
        self.partitions.setHorizontalHeaderLabels(
            ["Partition", "Max time", "CPUs/node", "Memory/node (GB)", "GPUs/node"])
        self.partitions.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        pl.addWidget(self.partitions)
        outer.addWidget(parts)

        progs = QGroupBox("Programs")
        gl = QVBoxLayout(progs)
        self.programs = QTableWidget(0, 5)
        self.programs.setHorizontalHeaderLabels(
            ["Program", "Modules", "MPI launcher", "Launcher options", "Executables"])
        self.programs.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.programs.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.programs.setSelectionBehavior(QAbstractItemView.SelectRows)
        gl.addWidget(self.programs)
        prow = QHBoxLayout()
        self.pick_button = QPushButton("Choose modules…")
        self.pick_button.clicked.connect(lambda: launch(self, self.pick_modules(), "Modules"))
        self.check_button = QPushButton("Check modules")
        self.check_button.setToolTip("Load the modules on the login node from a clean slate, "
                                     "then find each executable and its libraries")
        self.check_button.clicked.connect(lambda: launch(self, self.check_program(), "Check"))
        prow.addWidget(self.pick_button)
        prow.addWidget(self.check_button)
        prow.addStretch()
        gl.addLayout(prow)
        outer.addWidget(progs)

        buttons = QHBoxLayout()
        self.test_button = QPushButton("Test connection")
        self.test_button.clicked.connect(lambda: launch(self, self.test(), "Test connection"))
        self.discover_button = QPushButton("Discover")
        self.discover_button.setToolTip("Read partitions and the module system from the cluster")
        self.discover_button.clicked.connect(lambda: launch(self, self.discover(), "Discover"))
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save)
        self.report = QLabel(wordWrap=True)
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.discover_button)
        buttons.addStretch()
        buttons.addWidget(self.save_button)
        outer.addLayout(buttons)
        outer.addWidget(self.report)

    def _browse_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Private key", self.key_file.text() or "~/.ssh")
        if path:
            self.key_file.setText(path)

    # -- profile <-> form ----------------------------------------------------------

    def set_profile(self, profile: ClusterProfile | None):
        self.profile = profile
        self.setEnabled(profile is not None)
        self.report.clear()
        if profile is None:
            return
        c = profile.connection
        self.name.setText(profile.name)
        self.description.setText(profile.description)
        self.host.setText(c.host)
        self.port.setValue(c.port or 0)
        self.username.setText(c.username)
        self.auth.setCurrentText(c.auth)
        self.key_file.setText(c.key_file)
        self.work_root.setText(profile.work_root)
        self.scratch.setText(profile.scratch)
        self.python.setText(profile.python)
        self.account.setText(profile.account)
        self.qos.setText(profile.qos)
        self.module_use.setPlainText("\n".join(profile.module_use))
        self._fill_partitions(profile.partitions)
        programs = dict(profile.programs)
        for key in PROGRAMS:
            programs.setdefault(key, Program(executables=_parse_executables(DEFAULT_EXECUTABLES[key])))
        self.programs.setRowCount(0)
        for key, prog in programs.items():
            self._add_program_row(key, prog)

    def _fill_partitions(self, partitions: dict[str, Partition]):
        self.partitions.setRowCount(0)
        for name, p in partitions.items():
            r = self.partitions.rowCount()
            self.partitions.insertRow(r)
            for col, value in enumerate((name, p.max_walltime, p.cores_per_node or "",
                                         p.memory_per_node_gb or "", p.gpus_per_node or "")):
                self.partitions.setItem(r, col, QTableWidgetItem(str(value)))

    def _add_program_row(self, key: str, prog: Program):
        r = self.programs.rowCount()
        self.programs.insertRow(r)
        item = QTableWidgetItem(key)
        item.setToolTip(PROGRAMS.get(key, key))
        self.programs.setItem(r, 0, item)
        self.programs.setItem(r, 1, QTableWidgetItem(" ".join(prog.modules)))
        launcher = QComboBox()
        launcher.addItems(MPI_LAUNCHERS)
        launcher.setCurrentText(prog.mpi_launcher)
        self.programs.setCellWidget(r, 2, launcher)
        self.programs.setItem(r, 3, QTableWidgetItem(prog.launcher_options))
        self.programs.setItem(r, 4, QTableWidgetItem(_executables_text(prog.executables)))

    def _text(self, table, r, c) -> str:
        item = table.item(r, c)
        return item.text().strip() if item else ""

    def form_profile(self) -> ClusterProfile:
        """The profile as the form shows it (keeping what the form does not show)."""
        p = copy.deepcopy(self.profile)
        p.path, p.warnings = self.profile.path, self.profile.warnings
        p.name = self.name.text().strip()
        p.description = self.description.text().strip()
        p.connection = ConnectionSettings(
            host=self.host.text().strip(), port=self.port.value() or None,
            username=self.username.text().strip(), auth=self.auth.currentText(),
            key_file=self.key_file.text().strip(),
            connect_timeout=self.profile.connection.connect_timeout)
        p.work_root = self.work_root.text().strip()
        p.scratch = self.scratch.text().strip()
        p.python = self.python.text().strip() or "python3"
        p.account = self.account.text().strip()
        p.qos = self.qos.text().strip()
        p.module_use = [x.strip() for x in self.module_use.toPlainText().splitlines() if x.strip()]
        p.partitions = {}
        for r in range(self.partitions.rowCount()):
            name = self._text(self.partitions, r, 0)
            if not name:
                continue
            num = [self._text(self.partitions, r, c) for c in (2, 3, 4)]
            try:
                p.partitions[name] = Partition(self._text(self.partitions, r, 1),
                                               int(num[0] or 0), float(num[1] or 0), int(num[2] or 0))
            except ValueError:
                raise ValueError(f"Partition {name}: CPUs, memory and GPUs must be numbers") from None
        p.programs = {}
        for r in range(self.programs.rowCount()):
            key = self._text(self.programs, r, 0)
            modules = self._text(self.programs, r, 1).split()
            old = self.profile.programs.get(key, Program())
            p.programs[key] = Program(modules=modules, preamble=list(old.preamble),
                                      mpi_launcher=self.programs.cellWidget(r, 2).currentText(),
                                      launcher_options=self._text(self.programs, r, 3),
                                      executables=_parse_executables(self._text(self.programs, r, 4)))
        return p

    def save(self) -> bool:
        try:
            profile = self.form_profile()
        except ValueError as e:
            show_error(self, "Save", str(e))
            return False
        problems = profile.validate()
        if problems:
            show_error(self, "Save", "The profile cannot be saved:\n\n" + "\n".join(problems))
            return False
        self.profile = profile
        self.saved.emit(profile)
        self.report.setText(f"Saved {profile.name}.")
        return True

    # -- talking to the cluster ------------------------------------------------------

    async def _session(self):
        """The session, after saving the form (the cluster uses what is saved)."""
        if not self.save():
            raise ValueError("Fix the profile first.")
        return self.session_for(self.profile)

    async def test(self):
        session = await self._session()
        transport, slurm = await session.ready()
        self.report.setText("Connected; asking the cluster…")
        lines = [f"Home: {await transport.home()}", f"Slurm {await slurm.version()}"]
        accounting = await slurm.accounting_available()
        note = getattr(slurm, "accounting_note", "")
        lines.append("Accounting: " + ("yes" if accounting else "no" + (f" ({note})" if note else "")))
        lines.append("Your groups: " + " ".join(await slurm.user_groups()))
        system = await session.modules.system()
        lines.append(f"Modules: {system.kind} {system.version}" if system.kind else "Modules: none")
        self.report.setText("Connection works. " + " · ".join(lines))

    async def discover(self):
        session = await self._session()
        transport, slurm = await session.ready()
        partitions, nodes, groups = await slurm.partitions(), await slurm.nodes(), await slurm.user_groups()
        associations, qos = [], []
        if await slurm.accounting_available():
            associations, qos = await slurm.associations(), await slurm.qos()
        found, accesses = {}, {}
        for p in partitions:
            acc = access(p, groups, associations, qos)
            if not acc.allowed:
                continue
            accesses[p.name] = (p, acc)
            mine = [n for n in nodes if p.name in n.partitions]
            gpus = [int(m.group(1)) for n in mine for m in [re.search(r"gpu[^:]*:(?:[^:]*:)?(\d+)", n.gres)] if m]
            found[p.name] = Partition(
                max_walltime=walltime(acc.max_time_min),
                cores_per_node=max((n.cpus for n in mine), default=0),
                memory_per_node_gb=round(max((n.memory_mb for n in mine), default=0) / 1024, 1),
                gpus_per_node=max(gpus, default=0))
        self._fill_partitions(found)
        notes = [f"{len(found)} partitions you may use."]
        if associations:
            # Fill in the account and QOS if the profile has none yet
            accounts = sorted({a for _, acc in accesses.values() for a in acc.accounts})
            if not self.account.text().strip() and len(accounts) == 1:
                self.account.setText(accounts[0])
            default = next((acc for p, acc in accesses.values() if p.default),
                           next(iter(accesses.values()), (None, None))[1])
            if not self.qos.text().strip() and default is not None and default.default_qos:
                self.qos.setText(default.default_qos)
            notes.append("Accounts: " + ", ".join(accounts) + "; QOS: " + ", ".join(
                f"{name}: {', '.join(acc.qos)}" for name, (_, acc) in accesses.items()) + ".")
        system = await session.modules.system()
        notes.append(f"Modules: {system.kind} {system.version}." if system.kind
                     else "No module system found.")
        # A Spack module folder given one level too high: offer the one inside
        dirs = [x.strip() for x in self.module_use.toPlainText().splitlines() if x.strip()]
        fixed = []
        for d in dirs:
            try:
                entries = await transport.listdir(d)
            except Exception:
                fixed.append(d)
                continue
            subdirs = [e for e in entries if e.is_dir]
            if subdirs and len(subdirs) == len(entries) and all(e.name.startswith("linux-") for e in subdirs) \
                    and len(subdirs) == 1:
                fixed.append(subdirs[0].path)
                notes.append(f"Module folder changed to {subdirs[0].path} (the architecture folder "
                             "inside, where the modules are).")
            else:
                fixed.append(d)
        self.module_use.setPlainText("\n".join(fixed))
        self.report.setText(" ".join(notes) + " Review and Save.")

    def _selected_program_row(self) -> int:
        rows = {i.row() for i in self.programs.selectedIndexes()}
        if len(rows) != 1:
            raise ValueError("Select one program in the table first.")
        return rows.pop()

    async def pick_modules(self):
        row = self._selected_program_row()
        session = await self._session()
        await session.ready()
        key = self._text(self.programs, row, 0)
        available = await session.modules.available()
        mods = for_program(available, key)
        # Library bundles a program's modules may need (e.g. yambo/intel-libs) are
        # among its own; anything else can be typed in the table
        dialog = ModulePicker(self, f"Modules for {PROGRAMS.get(key, key)}", mods or available,
                              self._text(self.programs, row, 1).split())
        if await ask(dialog) == QDialog.Accepted:
            self.programs.setItem(row, 1, QTableWidgetItem(" ".join(dialog.chosen())))

    async def check_program(self):
        row = self._selected_program_row()
        session = await self._session()
        await session.ready()
        key = self._text(self.programs, row, 0)
        modules = self._text(self.programs, row, 1).split()
        if not modules:
            raise ValueError(f"No modules given for {key}.")
        exes = list(_parse_executables(self._text(self.programs, row, 4)).values())
        self.report.setText(f"Checking {key}…")
        check = await session.modules.check(modules, exes)
        if check.ok:
            self.report.setText(f"{key}: the modules load and " + ", ".join(
                f"{e} ({p})" for e, p in check.executables.items()) + " have all their libraries.")
        else:
            self.report.setText(f"{key}: NOT OK. " + " ".join(check.problems()))
