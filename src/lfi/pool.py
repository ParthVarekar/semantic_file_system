"""Crash- and hang-isolated worker processes for file processing.

``concurrent.futures.ProcessPoolExecutor`` cannot cancel one stuck task, and a native crash in
any worker breaks the whole pool. Parsers for untrusted files (PDF, Office, images) can do
both, so this small supervisor gives each worker its own pipe, enforces a per-task deadline,
kills and respawns only the worker that hung or died, and reports that file as failed.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from multiprocessing.connection import Connection, wait
from typing import Any, Iterable, Iterator

log = logging.getLogger(__name__)


def _worker_main(conn: Connection) -> None:
    from . import BLAS_THREADS_DEFAULTED

    if BLAS_THREADS_DEFAULTED:  # parsers do no matrix work: skip OpenBLAS's per-thread buffers
        os.environ["OPENBLAS_NUM_THREADS"] = "1"
    # Low priority so indexing never makes the desktop sluggish.
    try:
        if os.name == "nt":
            import ctypes

            BELOW_NORMAL = 0x4000
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL)
        else:
            os.nice(5)
    except Exception:
        pass
    from .extract import TASKS

    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        if msg is None:
            return
        task_id, kind, payload = msg
        try:
            fn = TASKS.get(kind)
            if fn is None:  # "package.module:function" - lets tests inject crash/hang tasks
                import importlib

                mod, _, name = kind.partition(":")
                fn = getattr(importlib.import_module(mod), name)
            result = fn(payload)
        except BaseException as e:  # never let the loop die on a task error
            result = {"ok": False, "status": "error", "error": f"{type(e).__name__}: {e}"[:300]}
        try:
            conn.send((task_id, result))
        except (EOFError, OSError):
            return


@dataclass
class _Worker:
    proc: Any
    conn: Connection
    task: tuple | None = None
    started: float = 0.0


class IsolatedPool:
    """``size_fn(current) -> wanted`` (optional) lets the pool grow and shrink while it runs, e.g.
    ``resources.Governor.workers``: idle workers beyond the wanted size are retired (their memory
    goes back to the system), busy ones finish their task first. ``workers`` is the starting size."""

    def __init__(self, workers: int, timeout: float = 60.0, size_fn=None, rescale_every: float = 3.0):
        self.n = max(1, workers)
        self.timeout = timeout
        self._ctx = mp.get_context("spawn")
        self._workers: list[_Worker] = []
        self.restarts = 0
        self.size_fn = size_fn
        self.rescale_every = rescale_every
        self._last_scale = time.monotonic()
        self.peak_workers = self.n

    @property
    def size(self) -> int:
        return len(self._workers)

    def _rescale(self) -> None:
        if self.size_fn is None or time.monotonic() - self._last_scale < self.rescale_every:
            return
        self._last_scale = time.monotonic()
        try:
            want = max(1, int(self.size_fn(len(self._workers))))
        except Exception:
            log.exception("pool sizing failed; keeping the current size")
            return
        while len(self._workers) < want:
            self._workers.append(self._spawn())
        self.peak_workers = max(self.peak_workers, len(self._workers))
        extra = len(self._workers) - want
        for w in [w for w in self._workers if w.task is None][:max(0, extra)]:
            self._retire(w)

    def _retire(self, w: _Worker) -> None:
        self._workers.remove(w)
        try:
            w.conn.send(None)
            w.proc.join(2)
        except Exception:
            pass
        if w.proc.is_alive():
            w.proc.kill()
        try:
            w.conn.close()
        except Exception:
            pass

    def __enter__(self) -> "IsolatedPool":
        for _ in range(self.n):
            self._workers.append(self._spawn())
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _spawn(self) -> _Worker:
        parent, child = self._ctx.Pipe(duplex=True)
        p = self._ctx.Process(target=_worker_main, args=(child,), daemon=True, name="lfi-worker")
        p.start()
        child.close()
        return _Worker(proc=p, conn=parent)

    def _replace(self, w: _Worker) -> _Worker:
        try:
            w.proc.kill()
            w.proc.join(5)
        except Exception:
            pass
        try:
            w.conn.close()
        except Exception:
            pass
        self.restarts += 1
        nw = self._spawn()
        self._workers[self._workers.index(w)] = nw
        return nw

    def close(self) -> None:
        for w in self._workers:
            try:
                w.conn.send(None)
            except Exception:
                pass
        deadline = time.monotonic() + 3
        for w in self._workers:
            w.proc.join(max(0.1, deadline - time.monotonic()))
            if w.proc.is_alive():
                w.proc.kill()
            try:
                w.conn.close()
            except Exception:
                pass
        self._workers.clear()

    def imap_unordered(
        self, tasks: Iterable[tuple[Any, str, dict]], cancel: threading.Event | None = None
    ) -> Iterator[tuple[Any, dict]]:
        """Yield ``(key, result)`` for each ``(key, kind, payload)`` task, in completion order."""
        it = iter(tasks)
        pending: deque = deque()
        exhausted = False
        seq = 0
        keys: dict[int, Any] = {}

        def refill() -> None:
            nonlocal exhausted
            while not exhausted and len(pending) < max(1, len(self._workers)) * 2:
                try:
                    pending.append(next(it))
                except StopIteration:
                    exhausted = True

        while True:
            if cancel is not None and cancel.is_set():
                for w in list(self._workers):
                    if w.task is not None:
                        self._replace(w)
                return
            self._rescale()
            refill()
            for w in list(self._workers):
                if w.task is None and pending:
                    key, kind, payload = pending.popleft()
                    seq += 1
                    keys[seq] = key
                    try:
                        w.conn.send((seq, kind, payload))
                        w.task = (seq, kind, payload)
                        w.started = time.monotonic()
                    except (OSError, EOFError):
                        pending.appendleft((key, kind, payload))
                        self._replace(w)
            busy = [w for w in self._workers if w.task is not None]
            if not busy:
                if exhausted and not pending:
                    return
                continue
            ready = wait([w.conn for w in busy], timeout=0.25)
            now = time.monotonic()
            for w in list(busy):
                if w.conn in ready:
                    try:
                        task_id, result = w.conn.recv()
                    except (EOFError, OSError):
                        tid = w.task[0]
                        code = w.proc.exitcode
                        self._replace(w)
                        yield keys.pop(tid), {
                            "ok": False, "status": "error",
                            "error": f"extractor process crashed (exit code {code})",
                        }
                        continue
                    w.task = None
                    yield keys.pop(task_id), result
                elif now - w.started > self.timeout:
                    tid = w.task[0]
                    self._replace(w)
                    yield keys.pop(tid), {
                        "ok": False, "status": "timeout",
                        "error": f"extraction exceeded {self.timeout:.0f}s and was stopped",
                    }
                elif not w.proc.is_alive():
                    tid = w.task[0]
                    code = w.proc.exitcode
                    self._replace(w)
                    yield keys.pop(tid), {
                        "ok": False, "status": "error",
                        "error": f"extractor process crashed (exit code {code})",
                    }
