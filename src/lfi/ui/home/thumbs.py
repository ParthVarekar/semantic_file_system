"""Thumbnails for result cards, loaded in the background so results appear at once and pictures
fade in as they arrive.

Photos are decoded on a background thread (Qt's image readers, scaled while decoding). Document
previews (``previews.py``) come from one isolated worker process that starts on first use and
exits after a minute of quiet, so a damaged PDF can't take the window down and nothing stays
resident when you're not browsing. A newer search makes queued requests for older ones moot.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from collections import OrderedDict

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QImageReader

from . import previews

log = logging.getLogger(__name__)

IDLE_EXIT_S = 60.0
CACHE_ITEMS = 160


def decode_image(path: str, px: int) -> QImage | None:
    r = QImageReader(path)
    r.setAutoTransform(True)
    sz = r.size()
    if not sz.isValid() or sz.width() * sz.height() > 80_000_000:
        return None
    sz.scale(px, px, Qt.AspectRatioMode.KeepAspectRatioByExpanding)
    r.setScaledSize(sz)
    img = r.read()
    return None if img.isNull() else img


def _fit(img: QImage, px: int) -> QImage:
    if max(img.width(), img.height()) <= px:
        return img
    return img.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


class ThumbLoader(QObject):
    """``request(gen, path, kind, px)``; ``ready(gen, path, px, image)`` fires on the GUI thread."""

    ready = Signal(int, str, int, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gen = 0
        self._queues = {False: queue.Queue(), True: queue.Queue()}  # photos / documents: neither waits for the other
        self._threads: dict[bool, threading.Thread] = {}
        self._cache: OrderedDict[tuple, QImage] = OrderedDict()
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def wanted(self, kind: str, path: str) -> bool:
        return kind == "image" or previews.can_preview(path)

    def cached(self, path: str, px: int) -> QImage | None:
        with self._lock:
            img = self._cache.get((path, px))
            if img is not None:
                self._cache.move_to_end((path, px))
            return img

    def new_generation(self) -> int:
        self.gen += 1
        return self.gen

    def request(self, gen: int, path: str, kind: str, px: int) -> None:
        if not self.wanted(kind, path):
            return
        hit = self.cached(path, px)
        if hit is not None:
            self.ready.emit(gen, path, px, hit)
            return
        docs = previews.can_preview(path)
        th = self._threads.get(docs)
        if th is None or not th.is_alive():
            self._stop.clear()
            th = self._threads[docs] = threading.Thread(target=self._run, args=(docs,), daemon=True,
                                                        name="lfi-previews" if docs else "lfi-thumbs")
            th.start()
        self._queues[docs].put((gen, path, kind, px))

    def shutdown(self) -> None:
        self._stop.set()
        for q in self._queues.values():
            q.put(None)

    # ---- background thread -------------------------------------------------------------------
    def _run(self, docs: bool) -> None:
        q, pool = self._queues[docs], None
        try:
            while not self._stop.is_set():
                try:
                    item = q.get(timeout=IDLE_EXIT_S if pool is not None else 300)
                except queue.Empty:
                    if pool is not None:  # quiet for a while: give the worker's memory back
                        pool.close()
                        pool = None
                        continue
                    return
                if item is None:
                    return
                gen, path, kind, px = item
                if gen < self.gen - 1 or self.cached(path, px) is not None:
                    continue  # superseded by a newer search (the previous one may still be on screen)
                img = None
                try:
                    if not docs:
                        img = decode_image(path, px)
                    elif os.path.isfile(path):
                        if pool is None:
                            from ...pool import IsolatedPool

                            pool = IsolatedPool(1, timeout=10.0)
                            pool.__enter__()
                        for _, res in pool.imap_unordered([(path, previews.TASK, {"path": path, "px": px})]):
                            if res.get("ok") and res.get("data"):
                                got = QImage.fromData(res["data"])
                                img = None if got.isNull() else _fit(got, px)
                except Exception:
                    log.debug("no preview for %s", path, exc_info=True)
                if img is None:
                    continue
                with self._lock:
                    self._cache[(path, px)] = img
                    while len(self._cache) > CACHE_ITEMS:
                        self._cache.popitem(last=False)
                self.ready.emit(gen, path, px, img)
        finally:
            if pool is not None:
                pool.close()
