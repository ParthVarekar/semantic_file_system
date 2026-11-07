"""The everyday window: an atmospheric backdrop, a floating rail with four places, and Settings.

    Search  -  Clean Up  -  Organize  -  Apps  -  Recently Removed          (Settings at the bottom)

Keys: Ctrl+1..5 the places, Ctrl+F or Ctrl+K search, Ctrl+, Settings. In Search: Esc clears the field
(then closes the preview), Delete removes the chosen file (with Undo).

While the index is being updated, a ring turns in the rail and a small status pill appears in the
top-right corner; both go away when there is nothing to say.

The detailed window (every tool and number) is still there as "Advanced tools"."""

from __future__ import annotations

import sys

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QVBoxLayout, QWidget

from ... import library
from ..core import AppContext
from ..quick import app_icon
from . import theme
from .apps import AppsPage
from .cleanup import CleanUpPage
from .organize import OrganizePage
from .removed import RemovedPage
from .search import SearchPage
from .service import IndexService
from .settings import SettingsDialog
from .welcome import WelcomePage
from .widgets import Backdrop, FadeStack, Glass, Rail, Ring, Toast, label, refresh_icons

NAV = [("search", "Search", "search"), ("cleanup", "Clean Up", "broom"), ("organize", "Organize", "folder"),
       ("apps", "Apps", "package"), ("removed", "Recently Removed", "trash")]


class HomeWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("Local File Intelligence")
        self.setWindowIcon(app_icon())
        self.resize(1180, 760)
        self.setMinimumSize(820, 520)
        self.service = IndexService(ctx)
        self._closing = False

        self.backdrop = Backdrop()
        self.setCentralWidget(self.backdrop)
        bl = QVBoxLayout(self.backdrop)
        bl.setContentsMargins(0, 0, 0, 0)
        self.root = FadeStack()
        bl.addWidget(self.root)
        self.welcome = WelcomePage(ctx)
        self.welcome.finished.connect(self._welcomed)
        self.root.addWidget(self.welcome)

        main = QWidget()
        h = QHBoxLayout(main)
        h.setContentsMargins(14, 14, 4, 14)
        h.setSpacing(6)
        self.rail = Rail([(k, f"{text}  (Ctrl+{i})", icon) for i, (k, text, icon) in enumerate(NAV, 1)])
        self.rail.changed.connect(self._go_key)
        self.rail.settings_clicked.connect(self.open_settings)
        h.addWidget(self.rail)

        self.pill = Glass(15, strong=True, parent=main)  # status, only while something is happening
        pl = QHBoxLayout(self.pill)
        pl.setContentsMargins(12, 7, 14, 7)
        pl.setSpacing(9)
        self.ring = Ring(15)
        self.status = label("", "status")
        pl.addWidget(self.ring)
        pl.addWidget(self.status)
        self.pill.hide()

        self.pages = FadeStack()
        self.pages.setObjectName("pages")
        toast_host = self.pages
        self.toast = Toast(main)
        self.page = {
            "search": SearchPage(ctx, self.service, self.toast, self.open_settings),
            "cleanup": CleanUpPage(ctx, self.toast),
            "organize": OrganizePage(ctx, self.toast),
            "apps": AppsPage(ctx, self.toast),
            "removed": RemovedPage(ctx, self.toast),
        }
        for w in self.page.values():
            toast_host.addWidget(w)
        h.addWidget(self.pages, 1)
        self.root.addWidget(main)
        self.main = main

        for i, (key, *_rest) in enumerate(NAV, 1):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self, activated=lambda k=key: self._key_go(k))
        for seq in ("Ctrl+F", "Ctrl+K"):
            QShortcut(QKeySequence(seq), self, activated=lambda: self._key_go("search", focus=True))
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.open_settings)

        self.service.progress.connect(self._progress)
        self.service.state_changed.connect(lambda s: self._status())
        self.service.finished.connect(lambda st: self._status())
        ctx.busy_changed.connect(lambda _: self._status())
        ctx.data_changed.connect(lambda _: self._status())

        if library.roots(ctx.conn):
            self.root.setCurrentWidget(main)
            self.go("search")
            self._start_background()
        else:
            self.root.setCurrentWidget(self.welcome)
        self._status()

    # ---- navigation ------------------------------------------------------------------------
    def _go_key(self, key: str) -> None:
        self.pages.show_page(self.page[key])
        self.pill.raise_()

    def _key_go(self, key: str, focus: bool = False) -> None:
        if self.root.currentWidget() is not self.main:  # still on the welcome screen
            return
        self.go(key)
        if focus:
            field = self.page["search"].field
            field.setFocus()
            field.selectAll()

    def go(self, key: str) -> None:
        self.rail.set_current(key, animate=self.isVisible())
        self._go_key(key)

    def bring_forward(self, search: bool = False) -> None:
        """Shortcut or second start: show the window in front, on Search with the box ready."""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
        if search and self.root.currentWidget() is self.main:
            self.go("search")
            field = self.page["search"].field
            field.setFocus()
            field.selectAll()

    # ---- first run & background updates -----------------------------------------------------
    def _welcomed(self) -> None:
        self.root.show_page(self.main)
        self.go("search")
        self._start_background()

    def _start_background(self) -> None:
        self.service.set_watching(self.ctx.settings.keep_updated)
        ctx = self.ctx
        if ctx.backend is None and ctx.backend_error is None:
            # Models are still loading: start once they're ready, so the indexer shares them instead
            # of loading a second copy.
            def ready():
                ctx.backend_changed.disconnect(ready)
                self.service.start()

            ctx.backend_changed.connect(ready)
            return
        self.service.start()  # incremental: only new and changed files are read

    def _progress(self, done: int, total: int, text: str) -> None:
        running = self.service.state == "running"
        value = done / total if total else -1.0
        line = f"{text}... {int(100 * done / total)}%" if total else f"{text}..."
        self.ring.set_value(value)
        self.rail.set_progress(value if running else None, line)
        self._show_pill(line if running else "")

    def _status(self) -> None:
        if self.service.state != "running":
            busy = "Working on your files..." if self.ctx.fileop_running else ""
            self.ring.set_value(-1.0)
            self.rail.set_progress(-1.0 if busy else None, busy or self.service.status_text())
            self._show_pill(busy)

    def _show_pill(self, text: str) -> None:
        self.status.setText(text)
        if not text:
            self.pill.hide()
            return
        self.pill.adjustSize()
        self._place_pill()
        self.pill.show()
        self.pill.raise_()

    def _place_pill(self) -> None:
        self.pill.move(self.main.width() - self.pill.width() - 22, 22)

    # ---- settings & theme ------------------------------------------------------------------
    def open_settings(self, focus: str = "") -> None:
        dlg = SettingsDialog(self.ctx, self, focus if isinstance(focus, str) else "")
        dlg.appearance_changed.connect(self.retheme)
        dlg.folders_changed.connect(lambda: (self.service.set_watching(self.ctx.settings.keep_updated),
                                             self.service.start(), self.ctx.data_changed.emit("index")))
        dlg.watching_changed.connect(self.service.set_watching)
        dlg.update_now.connect(self.service.start)
        dlg.exec()

    def retheme(self, appearance: str) -> None:
        from PySide6.QtWidgets import QApplication

        theme.apply(QApplication.instance(), appearance)
        self.backdrop.refresh()
        refresh_icons(self)
        theme.dark_title_bar(self, theme.current().dark)
        for w in self.findChildren(QWidget):
            w.update()

    def showEvent(self, e):
        super().showEvent(e)
        theme.dark_title_bar(self, theme.current().dark)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.move((self.main.width() - self.toast.width()) // 2, self.main.height() - self.toast.height() - 40)
        if self.pill.isVisible():
            self._place_pill()

    def closeEvent(self, e) -> None:
        if not self._closing and self.service.stop_then(self.close):
            self._closing = True
            self._show_pill("Pausing the update...")
            e.ignore()
            return
        self.service.shutdown()
        self.page["search"].shutdown()
        super().closeEvent(e)


def run_home(app, ctx: AppContext) -> HomeWindow:
    theme.apply(app, ctx.settings.ui_appearance)
    win = HomeWindow(ctx)
    win.show()
    return win
