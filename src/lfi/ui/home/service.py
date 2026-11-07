"""Keeps the index up to date without the user having to think about it: indexes at start-up and,
when "keep up to date" is on, shortly after files change in the selected folders."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, QTimer, Signal

from ... import library
from ...indexer import IndexBusy, Indexer
from ..core import AppContext

PHASE_TEXT = {"discover": "Looking for files", "process": "Reading files", "embed": "Understanding content",
              "images": "Looking at pictures", "hash": "Checking duplicates", "done": "Finishing"}


class IndexService(QObject):
    """state: 'idle' | 'running' | 'busy-elsewhere'. ``progress`` carries (done, total, text)."""

    state_changed = Signal(str)
    progress = Signal(int, int, str)
    finished = Signal(object)  # IndexStats or None

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.worker = None
        self.state = "idle"
        self.last_stats = None
        self._dirty = threading.Event()
        self._observer = None
        self._timer = QTimer(self)
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._close_after = None

    # ---- running ---------------------------------------------------------------------------
    def start(self) -> bool:
        ctx = self.ctx
        if ctx.index_running or not library.roots(ctx.conn):
            return False
        ctx.set_index_running(True)
        self._set_state("running")
        settings, backend = ctx.settings, ctx.backend

        def work(job):
            ix = Indexer(settings, backend=backend, progress=job.progress, cancel=job.cancel, embed=True,
                         vision=ctx.vision)
            try:
                return ix.run()
            finally:
                ix.conn.close()
                if ctx.backend is None and ix.backend is not None:
                    ctx.backend = ix.backend  # reuse the model the indexer loaded
                    try:
                        ctx.backend_changed.emit()
                    except RuntimeError:
                        pass

        def err(msg: str) -> None:
            first = msg.split("\n\n")[0]
            if first.startswith(IndexBusy.__name__):
                self._set_state("busy-elsewhere")
            else:
                self.progress.emit(0, 0, "Couldn't finish: " + first.split(": ", 1)[-1][:120])

        self.worker = ctx.run(work, self._done, err, self._progress, self._finished)
        return True

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.job.cancel.set()

    def stop_then(self, fn) -> bool:
        """Cancel a running index and call ``fn`` once it has stopped. Returns False if idle."""
        if self.worker is None:
            return False
        self._close_after = fn
        self.cancel()
        return True

    def _progress(self, phase: str, done: int, total: int, msg: str) -> None:
        self.progress.emit(done, total, PHASE_TEXT.get(phase, "Updating"))

    def _done(self, st) -> None:
        self.last_stats = st

    def _finished(self) -> None:
        self.worker = None
        self.ctx.set_index_running(False)
        if self.state != "busy-elsewhere":
            self._set_state("idle")
        self.finished.emit(self.last_stats)
        self.ctx.data_changed.emit("index")
        if self._close_after:
            fn, self._close_after = self._close_after, None
            fn()

    def _set_state(self, s: str) -> None:
        self.state = s
        self.state_changed.emit(s)

    # ---- keeping up to date ---------------------------------------------------------------
    def set_watching(self, on: bool) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer = None
        if not on:
            return
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            return
        dirty = self._dirty

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                p = getattr(event, "src_path", "") or ""
                if ".lfi-quarantine" not in p and "LocalFileIntelligence" not in p:
                    dirty.set()

        self._observer = Observer()
        for r in library.roots(self.ctx.conn):
            if os.path.isdir(r["path"]):
                self._observer.schedule(Handler(), r["path"], recursive=True)
        self._observer.daemon = True
        self._observer.start()

    def _tick(self) -> None:
        if self.state == "busy-elsewhere":
            self._set_state("idle")  # try again later; the other window may be done
            self._dirty.set()
        if self._dirty.is_set() and not self.ctx.index_running:
            self._dirty.clear()
            self.start()

    def shutdown(self) -> None:
        self._timer.stop()
        if self._observer is not None:
            self._observer.stop()
        self.cancel()

    def status_text(self) -> str:
        n = self.ctx.conn.execute("SELECT count(*) FROM files").fetchone()[0]
        if self.state == "running":
            return "Updating..."
        if self.state == "busy-elsewhere":
            return "Updating in another window"
        return f"Up to date · {n:,} files" if n else "No files yet"
