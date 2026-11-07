"""Quick search: a small launcher-style window (in the spirit of Flow Launcher) summoned by a
global hotkey. Type, see results instantly, press Enter to open.

    python -m lfi.ui.quick            start (shows the window; lives in the system tray)
    python -m lfi.ui.quick --hidden   start in the tray only (e.g. at sign-in)

Keys: Enter open - Ctrl+Enter show in folder - Ctrl+C copy path - Shift+Enter open the full app -
Tab switch All/Documents/Images - Up/Down choose - Esc hide.

Resources: searching runs on one background thread with its own read-only use of the index.
Keyword results appear at once; the embedding models load on the first search and are released
again after ``quick_idle_unload_min`` minutes without a search, so the tray process stays small.
"""

from __future__ import annotations

import os

from .. import BLAS_THREADS_DEFAULTED

if BLAS_THREADS_DEFAULTED:  # a tray process: keep NumPy's OpenBLAS from reserving per-thread buffers
    os.environ["OPENBLAS_NUM_THREADS"] = "1"

import ctypes  # noqa: E402
import gc  # noqa: E402
import getpass
import logging
import multiprocessing
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import (QRectF, QPointF, QAbstractListModel, QAbstractNativeEventFilter, QEvent, QModelIndex, QObject, QPoint,
                            QRect, QSize, Qt, QThread, QTimer, Signal)
from PySide6.QtGui import (QRadialGradient, QLinearGradient, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QImage, QImageReader,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QFileIconProvider, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
                               QLabel, QLineEdit, QListView, QMenu, QStyle, QStyledItemDelegate, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from .. import config, db
from .core import open_file, reveal

log = logging.getLogger(__name__)

SCOPES = [("all", "All"), ("documents", "Documents"), ("images", "Images")]
MAX_RESULTS = 30
VISIBLE_ROWS = 7
ROW_H = 54
THUMB = 36


# ---------------------------------------------------------------------------------------- theme
@dataclass
class Theme:
    panel: QColor
    border: QColor
    text: QColor
    sub: QColor
    sel: QColor
    hover: QColor
    accent: QColor


def theme() -> Theme:
    dark = False
    try:
        dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        pass
    if dark:
        return Theme(QColor(32, 32, 36, 246), QColor(255, 255, 255, 24), QColor(240, 240, 244),
                     QColor(160, 160, 170), QColor(76, 130, 247, 70), QColor(255, 255, 255, 14), QColor(96, 150, 255))
    return Theme(QColor(251, 251, 253, 248), QColor(0, 0, 0, 22), QColor(27, 27, 31), QColor(105, 105, 116),
                 QColor(37, 99, 235, 38), QColor(0, 0, 0, 10), QColor(37, 99, 235))


def render_app_icon(size: int = 64) -> QImage:
    """The app's icon, drawn: a dark rounded square holding a lit glass sphere (the same sphere as
    the search field), so nothing extra is shipped and it matches the monochrome look."""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    bg = QLinearGradient(0, 0, 0, size)
    bg.setColorAt(0, QColor("#222226"))
    bg.setColorAt(1, QColor("#08080A"))
    p.setBrush(bg)
    p.setPen(QPen(QColor(255, 255, 255, 46), max(1.0, size * 0.02)))
    p.drawRoundedRect(QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92), size * 0.24, size * 0.24)
    c, r = QPointF(size * 0.5, size * 0.5), size * 0.27
    g = QRadialGradient(c, r, QPointF(size * 0.42, size * 0.40))
    g.setColorAt(0.0, QColor("#FFFFFF"))
    g.setColorAt(0.22, QColor("#D9D9DE"))
    g.setColorAt(0.62, QColor("#55555C"))
    g.setColorAt(1.0, QColor("#0D0D10"))
    p.setBrush(g)
    p.setPen(QPen(QColor(255, 255, 255, 80), max(0.8, size * 0.012)))
    p.drawEllipse(c, r, r)
    p.end()
    return img


def app_icon(size: int = 64) -> QIcon:
    return QIcon(QPixmap.fromImage(render_app_icon(size)))


# ---------------------------------------------------------------------------------------- results
@dataclass
class QuickHit:
    path: str
    name: str
    kind: str
    size: int
    mtime_ns: int
    reasons: list[str] = field(default_factory=list)
    snippet: str = ""
    thumb: QImage | None = None


class SearchWorker(QThread):
    """One background thread that owns its own index connection and the models. It always runs
    the *latest* query only, so fast typing never queues up stale searches."""

    results = Signal(int, object, bool)  # sequence number, list[QuickHit], final (no better answer coming)
    status = Signal(str)

    def __init__(self, settings: config.Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._pending: tuple[int, str, str] | None = None
        self._stop = False
        self.backend = None
        self.vision = None
        self.models = "cold"  # cold | ready | unavailable
        self._last_search = time.monotonic()

    def submit(self, seq: int, query: str, scope: str) -> None:
        with self._lock:
            self._pending = (seq, query, scope)
        self._wake.set()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def run(self) -> None:
        conn = db.connect()
        try:
            while not self._stop:
                self._wake.wait(30.0)
                self._wake.clear()
                if self._stop:
                    break
                with self._lock:
                    job, self._pending = self._pending, None
                if job is None:
                    self._maybe_unload()
                    continue
                self._last_search = time.monotonic()
                will_load = self.models == "cold" and bool(job[1].strip())
                self._search(conn, *job, final=not will_load)  # instant pass with whatever is loaded
                if will_load:
                    self._load_models()
                    with self._lock:  # then repeat the newest query with semantic + image search
                        if self._pending is None:
                            self._pending = job
                    self._wake.set()
        finally:
            conn.close()

    def _search(self, conn, seq: int, query: str, scope: str, final: bool = True) -> None:
        from ..search import SearchEngine

        if not query.strip():
            self.results.emit(seq, [], True)
            return
        try:
            hits = SearchEngine(conn, self.backend, self.vision).search(query, MAX_RESULTS, scope=scope)
        except Exception as e:  # never let one query kill the worker
            log.exception("quick search failed")
            self.status.emit(f"Search failed: {type(e).__name__}: {e}")
            self.results.emit(seq, [], True)
            return
        out = [QuickHit(h.path, h.name, h.kind, h.size, h.mtime_ns, h.reasons, h.snippet) for h in hits]
        for qh in out[:VISIBLE_ROWS * 2]:
            if qh.kind == "image":
                qh.thumb = _thumbnail(qh.path)
        self.results.emit(seq, out, final)

    def _load_models(self) -> None:
        s = self.settings
        self.status.emit("Loading search models... (keyword results shown meanwhile)")
        try:
            from ..embeddings import create_backend

            self.backend = create_backend(s)
        except Exception as e:
            self.backend = None
            log.info("quick search: no embedding model (%s)", e)
        if s.image_search:
            try:
                from ..vision import VISION_PAIRS, VisionBackend, pair_is_cached

                pair = VISION_PAIRS.get(s.vision_pair)
                if pair is not None and pair_is_cached(pair.key):
                    self.vision = VisionBackend(pair, load_image=False, threads=s.vision_threads or None)
            except Exception as e:
                self.vision = None
                log.info("quick search: no image model (%s)", e)
        self.models = "ready" if (self.backend or self.vision) else "unavailable"
        self.status.emit("" if self.models == "ready" else "Keyword search only (no model downloaded)")

    def _maybe_unload(self) -> None:
        idle = time.monotonic() - self._last_search
        if self.models == "ready" and idle > 60 * max(1, self.settings.quick_idle_unload_min):
            self.backend = self.vision = None
            self.models = "cold"
            gc.collect()
            log.info("quick search: models released after %.0f s idle", idle)


def _thumbnail(path: str) -> QImage | None:
    r = QImageReader(path)
    r.setAutoTransform(True)
    sz = r.size()
    if not sz.isValid() or sz.width() * sz.height() > 80_000_000:
        return None
    sz.scale(THUMB * 2, THUMB * 2, Qt.AspectRatioMode.KeepAspectRatio)  # 2x for high-DPI screens
    r.setScaledSize(sz)
    img = r.read()
    return None if img.isNull() else img


class HitModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hits: list[QuickHit] = []
        self._icons = QFileIconProvider()
        self._icon_cache: dict[str, QIcon] = {}

    def set_hits(self, hits: list[QuickHit]) -> None:
        self.beginResetModel()
        self.hits = hits
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.hits)

    def icon_for(self, h: QuickHit) -> QIcon:
        ext = os.path.splitext(h.name)[1].lower()
        key = h.path if ext in (".exe", ".lnk", ".ico", ".url") else ext
        if key not in self._icon_cache:
            from PySide6.QtCore import QFileInfo

            self._icon_cache[key] = self._icons.icon(QFileInfo(h.path))
        return self._icon_cache[key]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        h = self.hits[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return h.name
        if role == Qt.ItemDataRole.UserRole:
            return h
        if role == Qt.ItemDataRole.ToolTipRole:
            parts = [h.path, "; ".join(h.reasons)]
            if h.snippet:
                parts.append(h.snippet[:300])
            return "\n".join(p for p in parts if p)
        return None


class HitDelegate(QStyledItemDelegate):
    def __init__(self, view: "QuickSearch"):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), ROW_H)

    def paint(self, p: QPainter, opt, index) -> None:
        from .core import fmt_size, fmt_time

        h: QuickHit = index.data(Qt.ItemDataRole.UserRole)
        t = self.view.theme
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = opt.rect.adjusted(6, 2, -6, -2)
        if opt.state & QStyle.StateFlag.State_Selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.sel)
            p.drawRoundedRect(r, 8, 8)
        elif opt.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.hover)
            p.drawRoundedRect(r, 8, 8)
        icon_rect = QRect(r.left() + 10, r.center().y() - THUMB // 2, THUMB, THUMB)
        if h.thumb is not None:
            pm = QPixmap.fromImage(h.thumb)
            target = pm.size().scaled(icon_rect.size(), Qt.AspectRatioMode.KeepAspectRatio)
            tr = QRect(0, 0, target.width(), target.height())
            tr.moveCenter(icon_rect.center())
            clip = QPainterPath()
            clip.addRoundedRect(tr, 5, 5)
            p.setClipPath(clip)
            p.drawPixmap(tr, pm)
            p.setClipping(False)
        else:
            self.view.model.icon_for(h).paint(p, icon_rect.adjusted(2, 2, -2, -2))
        x = icon_rect.right() + 14
        right_w = 150
        title_f = QFont(self.view.font())
        title_f.setPointSizeF(10.5)
        title_f.setWeight(QFont.Weight.DemiBold)
        sub_f = QFont(self.view.font())
        sub_f.setPointSizeF(8.8)
        text_w = r.right() - right_w - x
        p.setFont(title_f)
        p.setPen(t.text)
        fm = QFontMetrics(title_f)
        p.drawText(QRect(x, r.top() + 7, text_w, fm.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(h.name, Qt.TextElideMode.ElideRight, text_w))
        p.setFont(sub_f)
        p.setPen(t.sub)
        fs = QFontMetrics(sub_f)
        folder = os.path.dirname(h.path)
        why = _short_reason(h)
        sub = folder + (f"  ·  {why}" if why else "")
        p.drawText(QRect(x, r.bottom() - 7 - fs.height(), text_w, fs.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fs.elidedText(sub, Qt.TextElideMode.ElideMiddle, text_w))
        meta1 = h.kind.capitalize() + (f"  ·  {fmt_size(h.size)}" if h.size else "")
        meta2 = fmt_time(h.mtime_ns)[:10]
        rr = QRect(r.right() - right_w, r.top() + 8, right_w - 12, fs.height())
        p.drawText(rr, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, meta1)
        p.drawText(rr.translated(0, fs.height() + 3), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, meta2)
        p.restore()


def _short_reason(h: QuickHit) -> str:
    for r in h.reasons:
        if r.startswith("picture content matches"):
            return "picture matches" + (" (strong)" if "strong" in r else "")
        if r.startswith("keyword match"):
            return "text match"
        if r.startswith("filename"):
            return "name match"
    return "similar meaning" if any(r.startswith("semantic") for r in h.reasons) else ""


# ---------------------------------------------------------------------------------------- window
class QuickSearch(QWidget):
    """The launcher window. ``hide_on_blur``: hide when another window is activated (as
    launchers do); tests turn it off because the offscreen platform has no real activation."""

    def __init__(self, settings: config.Settings | None = None, worker: SearchWorker | None = None,
                 hide_on_blur: bool = True):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        ui_font = QFont()
        ui_font.setFamilies(["Segoe UI Variable Text", "Segoe UI", ui_font.defaultFamily()])
        ui_font.setPointSizeF(10)
        self.setFont(ui_font)
        self.setWindowTitle("Local File Intelligence - Quick search")
        self.setWindowIcon(app_icon())
        self.settings = settings or config.Settings.load()
        self.hide_on_blur = hide_on_blur
        self.theme = theme()
        self.scope_i = max(0, [k for k, _ in SCOPES].index(self.settings.quick_scope)
                           if self.settings.quick_scope in dict(SCOPES) else 0)
        self.seq = 0
        self.shown_seq = 0
        self.final_seq = 0  # last query whose final answer is shown
        self._status = ""  # model loading etc. (from the worker)
        self._no_match = False
        self._user_moved = False  # arrow keys used since the last keystroke in the box
        self.opener = open_file  # replaceable in tests
        self.revealer = reveal
        self.app_launcher = launch_main_app

        self.worker = worker or SearchWorker(self.settings, self)
        self.worker.results.connect(self._on_results)
        self.worker.status.connect(self._on_status)
        if not self.worker.isRunning():
            self.worker.start(QThread.Priority.LowPriority)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        self.panel = QFrame(self)
        self.panel.setObjectName("panel")
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.panel.setGraphicsEffect(shadow)
        outer.addWidget(self.panel)
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(12, 10, 12, 8)
        lay.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.glyph = QLabel()
        self.glyph.setPixmap(app_icon(28).pixmap(28, 28))
        self.edit = QLineEdit()
        self.edit.setObjectName("query")
        self.edit.setPlaceholderText("Search your files - describe what you remember")
        self.edit.setFrame(False)
        f = QFont(self.font())
        f.setPointSizeF(15)
        self.edit.setFont(f)
        self.scope_label = QLabel()
        self.scope_label.setObjectName("scope")
        self.scope_label.setToolTip("Tab: switch between All, Documents and Images")
        row.addWidget(self.glyph)
        row.addWidget(self.edit, 1)
        row.addWidget(self.scope_label)
        lay.addLayout(row)

        self.sep = QFrame()
        self.sep.setObjectName("sep")
        self.sep.setFixedHeight(1)
        lay.addWidget(self.sep)

        self.model = HitModel(self)
        self.list = QListView()
        self.list.setObjectName("results")
        self.list.setModel(self.model)
        self.list.setItemDelegate(HitDelegate(self))
        self.list.setMouseTracking(True)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setUniformItemSizes(True)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.clicked.connect(lambda i: self.activate(i.row()))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        lay.addWidget(self.list)

        self.footer = QLabel()
        self.footer.setObjectName("footer")
        lay.addWidget(self.footer)

        self._apply_style()
        self._update_scope_label()
        self._set_footer()
        self.edit.installEventFilter(self)
        self.edit.textChanged.connect(self._on_text)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(110)
        self._debounce.timeout.connect(self._submit)
        self.setFixedWidth(760)
        self._resize_to_results()

    # ---- look ------------------------------------------------------------------------------
    def _apply_style(self) -> None:
        t = self.theme

        def rgba(c: QColor) -> str:
            return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"

        self.setStyleSheet(f"""
            QWidget {{ font-family: "Segoe UI Variable Text", "Segoe UI"; }}
            #panel {{ background: {rgba(t.panel)}; border: 1px solid {rgba(t.border)}; border-radius: 14px; }}
            #query {{ background: transparent; color: {rgba(t.text)}; selection-background-color: {rgba(t.accent)}; }}
            #scope {{ color: {rgba(t.accent)}; border: 1px solid {rgba(t.accent)}; border-radius: 9px;
                     padding: 2px 9px; font-size: 9pt; font-weight: 600; }}
            #sep {{ background: {rgba(t.border)}; }}
            #results {{ background: transparent; outline: none; }}
            #footer {{ color: {rgba(t.sub)}; font-size: 8.5pt; padding: 2px 6px 0 6px; }}
            QScrollBar:vertical {{ width: 6px; background: transparent; }}
            QScrollBar::handle:vertical {{ background: {rgba(t.border)}; border-radius: 3px; min-height: 30px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
        """)

    def _update_scope_label(self) -> None:
        self.scope_label.setText(SCOPES[self.scope_i][1])

    def _set_footer(self, text: str | None = None) -> None:
        """A one-off message (``text``), or the footer derived from the current state."""
        if text is None:
            if self._status:
                text = self._status
            elif self._no_match:
                text = "No matching files. Shift+Enter opens the full app (add folders, index, filters)."
            else:
                text = ("Enter open    Ctrl+Enter show in folder    Ctrl+C copy path    "
                        "Tab scope    Shift+Enter full app    Esc close")
        self.footer.setText(text)

    def _resize_to_results(self) -> None:
        n = min(self.model.rowCount(), VISIBLE_ROWS)
        self.list.setVisible(n > 0)
        self.sep.setVisible(n > 0)
        self.list.setFixedHeight(n * ROW_H + (6 if n else 0))
        self.adjustSize()

    def place(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(QPoint(geo.center().x() - self.width() // 2, geo.top() + int(geo.height() * 0.22)))

    # ---- show / hide -----------------------------------------------------------------------
    def summon(self) -> None:
        if self.isVisible() and self.isActiveWindow():
            self.hide()
            return
        self.theme = theme()
        self._apply_style()
        self.place()
        self.show()
        self.raise_()
        self.activateWindow()
        if sys.platform == "win32" and QGuiApplication.platformName() == "windows":
            ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
        self.edit.setFocus()
        self.edit.selectAll()  # typing replaces the last query; arrows/Enter still work on it

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.ActivationChange and self.hide_on_blur and self.isVisible() \
                and not self.isActiveWindow():
            QTimer.singleShot(0, self._hide_if_inactive)
        super().changeEvent(e)

    def _hide_if_inactive(self) -> None:
        if not self.isActiveWindow() and not QApplication.activePopupWidget():
            self.hide()

    # ---- searching -------------------------------------------------------------------------
    def _on_text(self, _text: str) -> None:
        self._user_moved = False
        if not self.edit.text().strip():
            self._debounce.stop()
            self.seq += 1
            self.shown_seq = self.final_seq = self.seq
            self.model.set_hits([])
            self._no_match = False
            self._resize_to_results()
            self._set_footer()
            return
        self._debounce.start()

    def _submit(self) -> None:
        self.seq += 1
        self.worker.submit(self.seq, self.edit.text(), SCOPES[self.scope_i][0])

    def _on_results(self, seq: int, hits: list, final: bool = True) -> None:
        if seq < self.shown_seq or seq != self.seq:
            return  # an older query finished after a newer one was typed
        self.shown_seq = seq
        if final:
            self.final_seq = seq
        # The same query is answered twice when the models finish loading (keywords first, then
        # with meaning). If the user already moved to a result, keep that file selected; otherwise
        # select the new best match.
        chosen = self.current() if self._user_moved else None
        self.model.set_hits(hits)
        if hits:
            row = next((i for i, h in enumerate(hits) if chosen and h.path == chosen.path), 0)
            self.list.setCurrentIndex(self.model.index(row))
        self._resize_to_results()
        self._no_match = not hits and bool(self.edit.text().strip())
        self._set_footer()

    def _on_status(self, text: str) -> None:
        self._status = text
        self._set_footer()

    # ---- actions ---------------------------------------------------------------------------
    def current(self) -> QuickHit | None:
        i = self.list.currentIndex()
        return self.model.hits[i.row()] if i.isValid() and i.row() < len(self.model.hits) else None

    def activate(self, row: int | None = None, how: str = "open") -> None:
        if row is not None:
            self.list.setCurrentIndex(self.model.index(row))
        h = self.current()
        if how == "app":
            self.hide()
            self.app_launcher()
            return
        if h is None:
            return
        if how == "copy":
            QGuiApplication.clipboard().setText(h.path)
            self._set_footer(f"Copied: {h.path}")
            return
        if not os.path.exists(h.path):
            self._set_footer("That file is no longer there (it was moved or deleted). Re-index in the full app.")
            return
        self.hide()
        (self.revealer if how == "reveal" else self.opener)(h.path)

    def _context_menu(self, pos) -> None:
        idx = self.list.indexAt(pos)
        if not idx.isValid():
            return
        self.list.setCurrentIndex(idx)
        m = QMenu(self)
        for label, how in (("Open", "open"), ("Show in folder", "reveal"), ("Copy path", "copy"),
                           ("Open Local File Intelligence", "app")):
            m.addAction(label, lambda h=how: self.activate(None, h))
        m.exec(self.list.viewport().mapToGlobal(pos))

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if obj is self.edit and e.type() == QEvent.Type.KeyPress:
            k, mods = e.key(), e.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            n = self.model.rowCount()
            cur = self.list.currentIndex().row() if self.list.currentIndex().isValid() else -1
            if k == Qt.Key.Key_Escape:
                self.hide()
                return True
            if k in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp) and n:
                step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: VISIBLE_ROWS,
                        Qt.Key.Key_PageUp: -VISIBLE_ROWS}[k]
                nxt = (cur + step) % n if abs(step) == 1 else max(0, min(n - 1, cur + step))
                self.list.setCurrentIndex(self.model.index(nxt))
                self._user_moved = True
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.activate(None, "app" if shift else ("reveal" if ctrl else "open"))
                return True
            if k in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                self.scope_i = (self.scope_i + (-1 if k == Qt.Key.Key_Backtab or shift else 1)) % len(SCOPES)
                self._update_scope_label()
                if self.edit.text().strip():
                    self._submit()
                return True
            if ctrl and k == Qt.Key.Key_C and not self.edit.hasSelectedText() and n:
                self.activate(None, "copy")
                return True
        return super().eventFilter(obj, e)

    def closeEvent(self, e) -> None:  # the window is only hidden; quitting goes through the tray
        e.ignore()
        self.hide()

    def shutdown(self) -> None:
        self.worker.stop()
        self.worker.wait(5000)


# ---------------------------------------------------------------------------------------- hotkey
DEFAULT_HOTKEY = "Alt+Shift+Space"
# Tried in order when the *default* is already taken (Alt+Space / Ctrl+Alt+Space are usually
# owned by other launchers). A shortcut the user chose is never silently replaced.
FALLBACK_HOTKEYS = ("Win+Alt+Space", "Ctrl+Alt+K", "Win+Shift+F")
MOD = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
_VK_NAMES = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "`": 0xC0, "/": 0xBF, ";": 0xBA, "'": 0xDE, ",": 0xBC,
             ".": 0xBE, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC}


def parse_hotkey(text: str) -> tuple[int, int]:
    """'Ctrl+Alt+Space' -> (modifier flags, virtual-key code) for RegisterHotKey."""
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("use at least one modifier, e.g. Ctrl+Alt+Space")
    mods = 0
    for p in parts[:-1]:
        if p not in MOD:
            raise ValueError(f"unknown modifier '{p}' (use Ctrl, Alt, Shift, Win)")
        mods |= MOD[p]
    key = parts[-1]
    if key in _VK_NAMES:
        vk = _VK_NAMES[key]
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"unknown key '{key}'")
    return mods, vk


class GlobalHotkey(QObject, QAbstractNativeEventFilter):
    """System-wide shortcut via Win32 RegisterHotKey, delivered to ``hwnd`` as WM_HOTKEY and picked
    up from Qt's native event stream. Nothing is hooked: Windows tells us only about this one key."""

    activated = Signal()

    def __init__(self, hwnd: int, text: str, hotkey_id: int = 0x4C46):
        QObject.__init__(self)
        QAbstractNativeEventFilter.__init__(self)
        self.hwnd, self.text, self.id = hwnd, text, hotkey_id
        self.registered = False
        self.error = ""

    def register(self) -> bool:
        if sys.platform != "win32":
            self.error = "global hotkeys are only supported on Windows"
            return False
        wanted = self.text
        candidates = [wanted] + (list(FALLBACK_HOTKEYS) if wanted.lower() == DEFAULT_HOTKEY.lower() else [])
        for text in candidates:
            try:
                mods, vk = parse_hotkey(text)
            except ValueError as e:
                self.error = str(e)
                return False
            if ctypes.windll.user32.RegisterHotKey(ctypes.c_void_p(self.hwnd), self.id, mods | MOD_NOREPEAT, vk):
                self.registered, self.text = True, text
                self.error = "" if text == wanted else f"{wanted} is used by another program; using {text}"
                QApplication.instance().installNativeEventFilter(self)
                return True
        self.error = f"{wanted} is already used by another program; choose another in Settings"
        return False

    def unregister(self) -> None:
        if self.registered:
            ctypes.windll.user32.UnregisterHotKey(ctypes.c_void_p(self.hwnd), self.id)
            QApplication.instance().removeNativeEventFilter(self)
            self.registered = False

    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                from ctypes import wintypes

                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == self.id:
                    self.activated.emit()
                    return True, 0
        except Exception:
            log.exception("hotkey filter")
        return False, 0


# ---------------------------------------------------------------------------------------- app
def _instance_name() -> str:
    return f"LocalFileIntelligence-quick-{getpass.getuser()}"


def app_instance_name() -> str:
    """Local pipe of the running everyday window (it is single-instance)."""
    return f"LocalFileIntelligence-app-{getpass.getuser()}"


def launch_main_app(search: bool = False) -> None:
    """Bring the everyday window forward (on Search when ``search``), starting it if needed."""
    if sys.platform == "win32":
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: let the window take focus from us
    sock = QLocalSocket()
    sock.connectToServer(app_instance_name())
    if sock.waitForConnected(400):
        sock.write(b"search" if search else b"show")
        sock.flush()
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        return
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        exe = sys.executable
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        cmd = [w if os.path.exists(w) else exe, "-m", "lfi.ui.app"]
    subprocess.Popen(cmd + (["--search"] if search else []), close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def _quick_command(hidden: bool = False) -> list[str]:
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--quick"]
    else:
        exe = sys.executable
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        cmd = [w if os.path.exists(w) else exe, "-m", "lfi.ui.quick"]
    return cmd + (["--hidden"] if hidden else [])


def launch_quick(hidden: bool = False) -> None:
    """Start quick search as its own process (or bring the running one forward)."""
    subprocess.Popen(_quick_command(hidden), close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def startup_link(folder: str | None = None) -> str:
    """The Startup-folder shortcut used by 'Start quick search when I sign in' (opt-in only)."""
    folder = folder or os.environ.get("LFI_STARTUP_DIR") or os.path.join(
        os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    return os.path.join(folder, "Local File Intelligence Quick Search.lnk")


def startup_enabled(folder: str | None = None) -> bool:
    return os.path.exists(startup_link(folder))


def set_startup(enabled: bool, folder: str | None = None) -> None:
    """Create or remove the sign-in shortcut (a normal .lnk the user can also delete by hand)."""
    link = startup_link(folder)
    if not enabled:
        if os.path.exists(link):
            os.remove(link)
        return
    cmd = _quick_command(hidden=True)

    def q(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({q(link)});"
              f"$s.TargetPath={q(cmd[0])};$s.Arguments={q(subprocess.list2cmdline(cmd[1:]))};"
              f"$s.WorkingDirectory={q(os.path.dirname(cmd[0]))};"
              f"$s.Description='Local File Intelligence quick search';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True,
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=60)


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    argv = list(sys.argv[1:] if argv is None else argv)
    hidden = "--hidden" in argv
    config.enforce_offline_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(config.logs_dir() / "quick.log", encoding="utf-8")])
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Local File Intelligence Quick Search")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")

    # Single instance: a second start just brings the running window forward.
    probe = QLocalSocket()
    probe.connectToServer(_instance_name())
    if probe.waitForConnected(300):
        probe.write(b"hidden" if hidden else b"show")
        probe.flush()
        probe.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(_instance_name())
    server = QLocalServer()
    server.listen(_instance_name())

    settings = config.Settings.load()
    win = QuickSearch(settings)

    def on_peer():
        sock = server.nextPendingConnection()
        if sock is not None:
            sock.waitForReadyRead(300)
            if bytes(sock.readAll()) != b"hidden":
                win.summon()
            sock.disconnectFromServer()

    server.newConnection.connect(on_peer)

    hotkey = GlobalHotkey(int(win.winId()), settings.quick_hotkey)
    def on_hotkey() -> None:
        # Read each time, so a change in Settings applies without restarting the tray helper.
        if config.Settings.load().quick_opens == "bar":
            win.summon()
        else:
            launch_main_app(search=True)

    hotkey.activated.connect(on_hotkey)
    hotkey.register()
    log.info("quick search started; hotkey %s registered=%s %s", hotkey.text, hotkey.registered, hotkey.error)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(app_icon(), app)
        label = f"Search ({hotkey.text})" if hotkey.registered else "Search"
        tray.setToolTip(f"Local File Intelligence - {label}")
        menu = QMenu()
        menu.addAction(label, on_hotkey)
        menu.addAction("Small search bar", win.summon)
        menu.addAction("Open Local File Intelligence", launch_main_app)
        menu.addSeparator()
        menu.addAction("Quit (the shortcut stops working)", app.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: on_hotkey()
                               if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        tray.show()
        if not hotkey.registered:
            tray.showMessage("Quick search", f"Global shortcut not available: {hotkey.error}. "
                             "Click the tray icon to search.", QSystemTrayIcon.MessageIcon.Warning, 6000)
        elif hotkey.error:
            tray.showMessage("Quick search", f"{hotkey.error}.", QSystemTrayIcon.MessageIcon.Information, 5000)

    def cleanup():
        hotkey.unregister()
        win.shutdown()
        server.close()

    app.aboutToQuit.connect(cleanup)
    if not hidden:
        win.summon()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
