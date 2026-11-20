"""One connection per cluster, shared by every view."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..core.credentials import CredentialStore
from ..core.modules import Modules
from ..core.profile import ClusterProfile
from ..core.scheduler import Scheduler, scheduler_for
from ..core.ssh import SSHTransport
from ..core.transport import Transport
from .asyncqt import GuiPrompter


def make_transport(profile: ClusterProfile, parent) -> Transport:
    """The connection for a profile (tests replace this function)."""
    credentials = CredentialStore()
    return SSHTransport(profile.connection, GuiPrompter(parent, credentials), credentials)


def make_scheduler(profile: ClusterProfile, transport: Transport) -> Scheduler:
    return scheduler_for(profile.scheduler, transport)


class Session(QObject):
    stateChanged = Signal()

    def __init__(self, profile: ClusterProfile, parent_widget=None):
        super().__init__()
        self.profile = profile
        self._widget = parent_widget
        self.transport: Transport | None = None
        self.scheduler: Scheduler | None = None
        self.modules: Modules | None = None
        self._stale = False      # the connection settings changed since connecting

    @property
    def connected(self) -> bool:
        return self.transport is not None and self.transport.is_connected

    async def connect(self) -> None:
        if self._stale:
            await self.close()
            self._stale = False
        if self.transport is None:
            self.transport = make_transport(self.profile, self._widget)
            self.scheduler = make_scheduler(self.profile, self.transport)
            self.modules = Modules(self.transport, self.profile.module_use)
        try:
            await self.transport.connect()
        finally:
            self.stateChanged.emit()

    async def ready(self) -> tuple[Transport, Scheduler]:
        """Connected (connecting if needed): the transport and scheduler."""
        if not self.connected or self._stale:
            await self.connect()
        return self.transport, self.scheduler

    async def close(self) -> None:
        if self.transport is not None:
            await self.transport.close()
        self.transport = self.scheduler = self.modules = None
        self.stateChanged.emit()

    def profile_changed(self, profile: ClusterProfile):
        """A saved edit. New connection settings: reconnect on next use; the
        scheduler and module folders follow the profile at once."""
        self._stale = self._stale or (self.transport is not None
                                      and profile.connection != self.profile.connection)
        self.profile = profile
        if self.modules is not None:
            self.modules.module_use = list(profile.module_use)
