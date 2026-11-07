"""Desktop entry point: ``python -m lfi.ui.app`` (or ``lfi-gui``)."""

from __future__ import annotations

import logging
import multiprocessing
import sys

from .. import config

STYLE = """
QWidget { font-size: 10pt; }
#nav { background: palette(alternate-base); border: none; border-right: 1px solid palette(mid); padding-top: 8px; }
#nav::item { padding: 9px 14px; border-radius: 6px; margin: 1px 8px; }
#nav::item:selected { background: palette(highlight); color: palette(highlighted-text); }
#card { border: 1px solid palette(mid); border-radius: 8px; background: palette(base); }
#cardTitle { color: palette(placeholder-text); font-size: 9pt; }
#cardValue { font-size: 18pt; font-weight: 600; }
#note { color: palette(placeholder-text); }
QPushButton { padding: 5px 12px; border-radius: 5px; }
QPushButton#primary { background: palette(highlight); color: palette(highlighted-text); font-weight: 600; }
QPushButton#danger { color: #b3261e; font-weight: 600; }
QGroupBox { font-weight: 600; margin-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QProgressBar { border: none; background: palette(mid); border-radius: 4px; }
QProgressBar::chunk { background: palette(highlight); border-radius: 4px; }
"""


def _forward_to_running(message: bytes) -> bool:
    """If the everyday window is already open, ask it to come forward and return True."""
    from PySide6.QtNetwork import QLocalSocket

    from .quick import app_instance_name

    sock = QLocalSocket()
    sock.connectToServer(app_instance_name())
    if not sock.waitForConnected(300):
        return False
    sock.write(message)
    sock.flush()
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def _serve_single_instance(app, win) -> None:
    from PySide6.QtNetwork import QLocalServer

    from .quick import app_instance_name

    QLocalServer.removeServer(app_instance_name())  # a stale pipe from a crashed instance
    server = QLocalServer(app)
    server.listen(app_instance_name())

    def on_peer():
        sock = server.nextPendingConnection()
        if sock is not None:
            sock.waitForReadyRead(300)
            win.bring_forward(search=bytes(sock.readAll()) == b"search")
            sock.disconnectFromServer()

    server.newConnection.connect(on_peer)
    app._single_instance = server  # keep it alive


def _index_is_usable() -> bool:
    """If the index can't be read (damaged by a disk error, a sync tool, a power cut...), say so
    plainly and offer to rebuild it. The old files are kept in a backup folder; the user's own
    files are never involved. Returns False if the user chose to quit."""
    from PySide6.QtWidgets import QMessageBox

    from .. import db

    problem = db.index_problem()
    if problem is None:
        return True
    logging.getLogger(__name__).error("index unreadable: %s", problem)
    box = QMessageBox(QMessageBox.Icon.Warning, "Local File Intelligence", "The search index needs to be rebuilt.")
    box.setInformativeText("It can't be read any more (" + problem + "). Your files themselves are not affected - "
                           "the index only describes them.\n\nRebuilding keeps the old index in a backup folder and "
                           "reads your folders again; you'll be asked which folders to look after.")
    rebuild = box.addButton("Rebuild", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(rebuild)
    box.exec()
    if box.clickedButton() is not rebuild:
        return False
    dest = db.set_aside_damaged()
    logging.getLogger(__name__).warning("damaged index moved to %s", dest)
    return True


def main(argv: list[str] | None = None) -> int:
    """The everyday window; ``--advanced`` opens the detailed tools instead."""
    multiprocessing.freeze_support()  # required for worker processes in a packaged .exe
    argv = list(sys.argv[1:] if argv is None else argv)
    advanced = "--advanced" in argv
    config.enforce_offline_env()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(config.logs_dir() / "app.log", encoding="utf-8")],
    )
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .. import library
    from ..safety import is_elevated
    from .core import AppContext
    from .main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Local File Intelligence")
    app.setStyle("Fusion")
    if not advanced and _forward_to_running(b"search" if "--search" in argv else b"show"):
        return 0  # the window is already open: it was brought forward instead
    if not _index_is_usable():
        return 0
    ctx = AppContext()
    # Reconcile any file operation interrupted by a crash/power loss before anything else runs.
    ctx.recovery_report = ctx.fileops(ctx.conn).recover()
    if advanced:
        app.setStyleSheet(STYLE)
        win = MainWindow(ctx)
        win.show()
    else:
        from .home.window import run_home

        win = run_home(app, ctx)
        _serve_single_instance(app, win)
        if "--search" in argv:
            win.bring_forward(search=True)
        try:  # the tray helper owns the global shortcut; start it quietly (no-op if running)
            from .quick import launch_quick

            launch_quick(hidden=True)
        except Exception:
            logging.getLogger(__name__).exception("could not start the shortcut helper")
    if is_elevated():
        QMessageBox.warning(win, "Running as administrator",
                            "The app is running with administrator rights. This is not needed and not "
                            "recommended; please restart it normally.")
    if ctx.recovery_report:
        QMessageBox.information(win, "Recovered interrupted operations",
                                "Some file operations were interrupted last time and have been reconciled:\n\n"
                                + "\n".join(ctx.recovery_report[:12]))
    ctx.load_backend()
    if advanced and not library.roots(ctx.conn):
        win.go("dashboard")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
