"""Starting the GUI: Qt's event loop runs asyncio too (qasync)."""

from __future__ import annotations

import asyncio
import sys

from PySide6.QtWidgets import QApplication

from ..identity import APP_NAME, ORG_NAME, SETTINGS_NAME


def main() -> int:
    import qasync

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(SETTINGS_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationDisplayName(APP_NAME)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    from .main_window import MainWindow
    window = MainWindow()
    window.show()
    quit_event = asyncio.Event()
    app.aboutToQuit.connect(quit_event.set)
    with loop:
        loop.run_until_complete(quit_event.wait())
        loop.run_until_complete(window.close_sessions())
    return 0


if __name__ == "__main__":
    sys.exit(main())
