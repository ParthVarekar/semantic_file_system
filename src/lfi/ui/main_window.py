"""Main window: sidebar navigation over the feature pages."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QScrollArea,
                               QSizePolicy, QStackedWidget, QWidget)

from .core import AppContext
from .pages.activity import ActivityPage
from .pages.cleanup import CleanupPage
from .pages.dashboard import DashboardPage
from .pages.duplicates import DuplicatesPage
from .pages.folders import FoldersPage
from .pages.organize import OrganizePage
from .pages.quarantine import QuarantinePage
from .pages.search import SearchPage
from .pages.settings import SettingsPage


class _PageHolder(QWidget):
    """Scroll-area content that may shrink to its minimum height: for pages with word-wrapped text
    QScrollArea would otherwise keep their preferred height and scroll even on a big window."""

    def heightForWidth(self, w: int) -> int:
        return self.minimumSizeHint().height()


NAV = [("dashboard", "Dashboard"), ("folders", "Folders & indexing"), ("search", "Search"),
       ("organize", "Organize"), ("duplicates", "Duplicates"), ("cleanup", "Cleanup review"),
       ("quarantine", "Quarantine"), ("activity", "Activity"), ("settings", "Settings")]


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._closing = False
        self.setWindowTitle("Local File Intelligence")
        self.resize(1320, 840)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(190)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        factories = {
            "dashboard": lambda: DashboardPage(ctx, self.go), "folders": lambda: FoldersPage(ctx),
            "search": lambda: SearchPage(ctx), "organize": lambda: OrganizePage(ctx),
            "duplicates": lambda: DuplicatesPage(ctx), "cleanup": lambda: CleanupPage(ctx),
            "quarantine": lambda: QuarantinePage(ctx), "activity": lambda: ActivityPage(ctx),
            "settings": lambda: SettingsPage(ctx),
        }
        for key, label in NAV:
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(it)
            page = factories[key]()
            page.setObjectName(f"page_{key}")
            self.pages[key] = page
            wrap = _PageHolder()
            wl = QHBoxLayout(wrap)
            wl.setContentsMargins(18, 14, 18, 14)
            wl.addWidget(page)
            # Pages scroll instead of forcing a minimum window size (small screens, 150 % scaling).
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(wrap)
            self.stack.addWidget(scroll)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.status = QLabel("")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # long text never widens
        self.statusBar().addWidget(self.status, 1)
        self.privacy = QLabel("Offline - nothing leaves this computer")
        self.statusBar().addPermanentWidget(self.privacy)
        ctx.backend_changed.connect(self._status)
        ctx.busy_changed.connect(self._busy_changed)
        self.nav.setCurrentRow(0)
        self._status()

    def go(self, key: str) -> None:
        self.nav.setCurrentRow([k for k, _ in NAV].index(key))

    def _status(self) -> None:
        if self._closing:
            return
        busy = "Indexing...  |  " if self.ctx.index_running else ""
        if self.ctx.fileop_running:
            busy += self.ctx.fileop_label + "  |  "
        self.status.setText(busy + self.ctx.backend_status() + "  |  " + self.ctx.vision_status())

    def _busy_changed(self, _busy: bool) -> None:
        self._status()
        if self._closing and not self.ctx.busy():
            QTimer.singleShot(0, self.close)  # the deferred close (see closeEvent)

    def closeEvent(self, e):
        if self.ctx.busy():
            # Never block the GUI thread: cancel indexing (a running file batch is let finish), close afterwards.
            if not self._closing:
                self._closing = True
                self.pages["folders"].cancel_index()
                self.centralWidget().setEnabled(False)
            self.status.setText("Stopping indexing..." if self.ctx.index_running
                                else "Finishing the file operation before closing...")
            e.ignore()
            return
        self.pages["folders"].shutdown()
        self.ctx.pool.waitForDone(5000)
        super().closeEvent(e)
