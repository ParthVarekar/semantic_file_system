"""Shared UI infrastructure: background workers, app-wide context, small helpers.

All long-running work (indexing, model loading, analysis, file operations) runs on a
``QThreadPool`` worker with its own SQLite connection; the GUI thread only does short reads.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import threading
import time
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget

from .. import config, db, library
from ..fileops import FileOps, OpRefused


def fmt_size(n: float | None) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def fmt_time(ns_or_s: float | None) -> str:
    if not ns_or_s:
        return ""
    t = ns_or_s / 1e9 if ns_or_s > 1e12 else ns_or_s
    return dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def open_file(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def reveal(path: str) -> None:
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


def confirm(parent: QWidget, title: str, text: str, detail: str = "") -> bool:
    box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.Yes |
                      QMessageBox.StandardButton.Cancel, parent)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    if detail:
        box.setDetailedText(detail)
    return box.exec() == QMessageBox.StandardButton.Yes


class Job:
    """Handed to background functions: progress reporting and cooperative cancellation."""

    def __init__(self, signals: "WorkerSignals"):
        self.cancel = threading.Event()
        self._signals = signals
        self._last = 0.0

    def progress(self, phase: str, done: int, total: int, msg: str = "") -> None:
        now = time.monotonic()
        if now - self._last > 0.1 or phase == "done":  # throttle cross-thread signals
            self._last = now
            _emit(self._signals.progress, phase, done, total, msg)


def _emit(signal, *args) -> None:
    """Emit from a worker thread; the window (and the signal's owner) may already be gone at exit."""
    try:
        signal.emit(*args)
    except RuntimeError:  # "Signal source has been deleted"
        pass


class WorkerSignals(QObject):
    progress = Signal(str, int, int, str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, fn: Callable[[Job], Any]):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()
        self.job = Job(self.signals)

    def run(self) -> None:
        try:
            r = self.fn(self.job)
        except Exception as e:
            _emit(self.signals.error, f"{type(e).__name__}: {e}\n\n{traceback.format_exc(limit=6)}")
        else:
            _emit(self.signals.result, r)
        finally:
            _emit(self.signals.finished)


class AppContext(QObject):
    backend_changed = Signal()
    data_changed = Signal(str)  # what changed: index | files | quarantine | settings
    busy_changed = Signal(bool)  # indexing or a file operation started / finished
    reindex_requested = Signal()  # e.g. "Rebuild" in Settings; the Folders page starts indexing

    def __init__(self):
        super().__init__()
        config.enforce_offline_env()
        self.settings = config.Settings.load()
        self.conn = db.connect()
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(max(4, self.pool.maxThreadCount()))
        self._workers: set[Worker] = set()
        self.backend = None
        self.backend_error: str | None = None
        self.vision = None  # text tower of the image-text model, for describe-to-find image search
        self.vision_error: str | None = None
        self.backend_loading = False
        self.index_running = False
        self.fileop_running = False  # one batch of file operations at a time from this window
        self.fileop_label = ""
        self.recovery_report: list[str] = []

    # ---- workers ---------------------------------------------------------------------------
    def run(self, fn: Callable[[Job], Any], on_result: Callable | None = None, on_error: Callable | None = None,
            on_progress: Callable | None = None, on_finished: Callable | None = None) -> Worker:
        w = Worker(fn)
        self._workers.add(w)
        if on_result:
            w.signals.result.connect(on_result)
        w.signals.error.connect(on_error or self._default_error)
        if on_progress:
            w.signals.progress.connect(on_progress)
        if on_finished:
            w.signals.finished.connect(on_finished)
        w.signals.finished.connect(lambda: self._workers.discard(w))
        self.pool.start(w)
        return w

    def _default_error(self, msg: str) -> None:
        QMessageBox.critical(None, "Something went wrong", msg.split("\n\n")[0], QMessageBox.StandardButton.Ok)

    # ---- busy state ------------------------------------------------------------------------
    def busy(self) -> bool:
        return self.index_running or self.fileop_running

    def set_index_running(self, on: bool) -> None:
        self.index_running = on
        self.busy_changed.emit(self.busy())

    def fileop_refused(self, parent: QWidget | None) -> bool:
        """True (after telling the user) while another file operation from this window is running."""
        if not self.fileop_running:
            return False
        QMessageBox.information(parent, "Please wait", f"{self.fileop_label} Another file operation is still "
                                "running; try again when it has finished.")
        return True

    def run_fileop(self, parent: QWidget | None, title: str, label: str, fn: Callable[[FileOps], Any],
                   on_result: Callable, on_finished: Callable | None = None) -> Worker | None:
        """Run one batch of file operations (``fn(fileops)``) on a worker. Refused while another
        batch from this window runs; a batch that FileOps refuses (``OpRefused``) is reported calmly."""
        if self.fileop_refused(parent):
            return None
        self.fileop_running, self.fileop_label = True, label
        self.busy_changed.emit(True)

        def work(job):
            conn = db.connect()
            try:
                return fn(self.fileops(conn))
            finally:
                conn.close()

        def err(msg: str) -> None:
            first = msg.split("\n\n")[0]
            prefix = OpRefused.__name__ + ": "
            if first.startswith(prefix):
                QMessageBox.information(parent, title, "Nothing was done: " + first[len(prefix):])
            else:
                self._default_error(msg)

        def done() -> None:
            self.fileop_running, self.fileop_label = False, ""
            self.busy_changed.emit(self.busy())
            if on_finished:
                on_finished()

        return self.run(work, on_result, err, on_finished=done)

    # ---- embedding backend -----------------------------------------------------------------
    def load_backend(self) -> None:
        if self.backend_loading:
            return
        self.backend_loading = True
        settings = self.settings

        def work(job):
            from ..embeddings import create_backend

            vision, vision_error = None, None
            if settings.image_search:
                from ..vision import VISION_PAIRS, VisionBackend, pair_is_cached

                pair = VISION_PAIRS.get(settings.vision_pair)
                if pair is None or not pair_is_cached(pair.key):
                    vision_error = "image model not downloaded (Settings > Image search)"
                else:
                    try:
                        vision = VisionBackend(pair, load_image=False)  # ~130 MB text tower; images load in a worker
                    except Exception as e:
                        vision_error = str(e).splitlines()[0] if str(e) else type(e).__name__
            self.vision, self.vision_error = vision, vision_error
            return create_backend(settings)

        def ok(b):
            self.backend, self.backend_error = b, None

        def err(msg):
            self.backend, self.backend_error = None, msg.split("\n\n")[0]

        def done():
            self.backend_loading = False
            self.backend_changed.emit()

        self.run(work, ok, err, on_finished=done)

    def backend_status(self) -> str:
        if self.backend_loading:
            return "Loading embedding model..."
        if self.backend is not None:
            fb = f" - {self.backend.fallback_reason}" if getattr(self.backend, "fallback_reason", None) else ""
            return f"Semantic search: {self.backend.model_id} on {self.backend.device.upper()}{fb}"
        return "Keyword search only - " + (self.backend_error or "no embedding model loaded")

    def vision_status(self) -> str:
        if not self.settings.image_search:
            return "Image search: off"
        if self.vision is not None:
            return f"Image search: {self.vision.pair.key}"
        return "Image search: " + (self.vision_error or "loading...")

    # ---- helpers ---------------------------------------------------------------------------
    def policy(self, conn=None):
        return library.scope_policy(conn or self.conn, self.settings)

    def fileops(self, conn) -> FileOps:
        return FileOps(conn, self.settings, self.policy(conn))

    def save_settings(self) -> None:
        self.settings.save()
        self.data_changed.emit("settings")


class Debouncer(QObject):
    """Calls ``fn`` once activity has been quiet for ``delay_ms``."""

    def __init__(self, fn: Callable[[], None], delay_ms: int, parent=None):
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(delay_ms)
        self.timer.timeout.connect(fn)

    def poke(self) -> None:
        self.timer.start()
