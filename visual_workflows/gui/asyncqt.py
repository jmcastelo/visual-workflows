"""Running the core's async code from Qt (through qasync), and the login
questions as dialogs that do not block the window."""

from __future__ import annotations

import asyncio
import traceback

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QLineEdit, QMessageBox, QVBoxLayout, QWidget)

from ..core.credentials import CredentialStore
from ..core.profile import ProfileError
from ..core.project import ProjectError
from ..core.scheduler import SchedulerError
from ..core.ssh import Secret
from ..core.transport import TransportError
from ..identity import APP_NAME, ORG_NAME, SETTINGS_NAME

# Errors the user can act on: shown as they are, without a traceback
EXPECTED = (TransportError, SchedulerError, ProjectError, ProfileError, ValueError)


def settings() -> QSettings:
    return QSettings(QSettings.IniFormat, QSettings.UserScope, ORG_NAME, SETTINGS_NAME)


def show_error(parent: QWidget | None, title: str, text: str, details: str = ""):
    box = QMessageBox(QMessageBox.Warning, title or APP_NAME, text, QMessageBox.Ok, parent)
    if details:
        box.setDetailedText(details)
    box.open()
    return box


async def ask(dialog: QDialog) -> int:
    """Show a dialog without a nested event loop; its result."""
    future = asyncio.get_running_loop().create_future()

    def done(result):
        if not future.done():
            future.set_result(result)
    dialog.finished.connect(done)
    dialog.open()
    return await future


async def confirm(parent, title: str, text: str) -> bool:
    box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.Yes | QMessageBox.No, parent)
    box.setDefaultButton(QMessageBox.No)
    return await ask(box) == QMessageBox.Yes


# Running tasks, kept so they are not garbage-collected mid-way
_tasks: set[asyncio.Task] = set()


def launch(parent: QWidget | None, coro, what: str = "") -> asyncio.Task:
    """Run a coroutine; errors become a message box titled `what`."""
    task = asyncio.ensure_future(_guarded(parent, coro, what))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def _guarded(parent, coro, what):
    try:
        return await coro
    except asyncio.CancelledError:
        raise
    except EXPECTED as e:
        show_error(parent, what, str(e))
    except Exception as e:  # a bug: show it, with the traceback to report
        show_error(parent, what, f"Unexpected error: {e}", traceback.format_exc())


class _SecretDialog(QDialog):
    def __init__(self, parent, title: str, label: str, can_remember: bool):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(label))
        self.edit = QLineEdit(echoMode=QLineEdit.Password)
        layout.addWidget(self.edit)
        self.remember = QCheckBox("Remember it in the system keyring")
        self.remember.setVisible(can_remember)
        layout.addWidget(self.remember)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class _QuestionsDialog(QDialog):
    def __init__(self, parent, name, instructions, prompts):
        super().__init__(parent)
        self.setWindowTitle(name or "Login")
        form = QFormLayout(self)
        if instructions:
            form.addRow(QLabel(instructions))
        self.edits = []
        for text, echo in prompts:
            edit = QLineEdit(echoMode=QLineEdit.Normal if echo else QLineEdit.Password)
            form.addRow(text.strip(), edit)
            self.edits.append(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class GuiPrompter:
    """AuthPrompter as dialogs."""

    def __init__(self, parent: QWidget | None, credentials: CredentialStore):
        self.parent = parent
        self.can_remember = credentials.available

    async def _secret(self, title, label) -> Secret | None:
        d = _SecretDialog(self.parent, title, label, self.can_remember)
        if await ask(d) != QDialog.Accepted:
            return None
        return Secret(d.edit.text(), d.remember.isChecked())

    async def password(self, username, host, *, retry):
        return await self._secret("Password", ("Wrong password. " if retry else "")
                                  + f"Password for {username}@{host}:")

    async def passphrase(self, key_file, *, retry):
        return await self._secret("Key passphrase", ("Wrong passphrase. " if retry else "")
                                  + f"Passphrase for {key_file}:")

    async def keyboard_interactive(self, name, instructions, prompts):
        d = _QuestionsDialog(self.parent, name, instructions, prompts)
        if await ask(d) != QDialog.Accepted:
            return None
        return [e.text() for e in d.edits]

    async def accept_host_key(self, host, port, algorithm, fingerprint):
        return await confirm(
            self.parent, "Unknown host key",
            f"The host key of {host} (port {port}) is not known yet:\n\n"
            f"{algorithm} {fingerprint}\n\n"
            "Check it with the cluster's documentation or administrators. Trust it and connect?")
