"""Incremental, resumable, cancellable, strictly read-only indexing.

Phases
1. discover - walk selected roots with ``os.scandir`` (no symlink/junction following), upsert
   metadata, detect changes by (size, mtime), and drop rows for files that vanished.
2. process  - hash + extract text/image metadata in isolated worker processes.
3. hash     - fclones-style size -> prehash -> full hash for large same-size files only.
4. embed    - chunk and file embeddings, in a parallel thread so extraction never waits on
   the model. If no model is available, keyword and filename search still work.

State lives in ``files.stage`` (0 discovered, 1 processed, 2 embedded) so an interrupted or
cancelled job resumes where it stopped. Nothing in this module opens a file for writing.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np

from . import config, db, library
from .config import Settings
from .embeddings import EmbeddingBackend, ModelUnavailable, create_backend
from .extract import kind_for
from .pool import IsolatedPool
from .safety import (ScopePolicy, has_project_marker, is_cloud_placeholder, is_link_like, is_system_file,
                     long_path, norm)
from .textutil import chunk_text, descriptor, folder_tokens, name_tokens
from .vectorstore import VectorStore

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int, str], None]
TAG_CHUNK_ORD = 10_000  # chunk slot holding an image's auto-tags (searchable text)
RETRY_STATUSES = ("locked",)
PROJECT_OUTPUT_DIRS = frozenset({"dist", "build", "target", "out", "bin", "obj", ".output", "coverage", ".cache"})  # transient extraction failures retried on every run, not only after a change


class IndexBusy(RuntimeError):
    """Another process is indexing right now."""


class Cancelled(Exception):
    pass


@dataclass
class IndexStats:
    roots: int = 0
    gpu_workers: int = 0  # most model copies running at once (parallel GPU embedding)
    images_skipped: int = 0  # tiny pictures and sampled-out video frames (not run through the image model)
    image_workers: int = 0
    reader_workers: int = 0
    discovered: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    removed: int = 0
    processed: int = 0
    errors: int = 0
    encrypted: int = 0
    timeouts: int = 0
    changed_during_read: int = 0
    metadata_only: int = 0
    skipped_links: int = 0
    unreadable_dirs: list[str] = field(default_factory=list)
    large_hashed: int = 0
    embedded_files: int = 0
    embedded_chunks: int = 0
    embed_device: str = ""
    embed_error: str | None = None
    images_embedded: int = 0
    images_failed: int = 0
    image_device: str = ""
    image_error: str | None = None
    worker_restarts: int = 0
    cancelled: bool = False
    elapsed_s: float = 0.0
    phase_s: dict = field(default_factory=dict)


class Indexer:
    VISION_TASK = "lfi.vision:embed_task"  # worker entry point for image embedding (overridable in tests)

    def __init__(self, settings: Settings, db_path: str | None = None,
                 backend: EmbeddingBackend | None = None, progress: ProgressFn | None = None,
                 cancel: threading.Event | None = None, embed: bool = True, vision=None):
        self.settings = settings
        self.db_path = db_path
        self.backend = backend
        self.progress = progress or (lambda *a: None)
        self.cancel = cancel or threading.Event()
        self.embed_enabled = embed
        self.vision = vision  # text tower of the image-text model (for auto-tags); loaded lazily
        self.stats = IndexStats()
        self.conn = db.connect(db_path)

    # ------------------------------------------------------------------ driver
    def run(self) -> IndexStats:
        """Index all selected folders. Only one indexer runs at a time across processes (the app,
        the Advanced tools, the CLI): a second one raises IndexBusy instead of racing the first."""
        from .fileops import _WriterLock

        lock = _WriterLock(str(config.app_home() / "index.lock"))
        if not lock.acquire(0.0):
            raise IndexBusy("Indexing is already running in another window.")
        try:
            return self._run()
        finally:
            lock.release()

    def _run(self) -> IndexStats:
        t0 = time.perf_counter()
        conn = self.conn
        conn.execute("UPDATE jobs SET status='interrupted' WHERE status='running'")
        job_id = conn.execute("INSERT INTO jobs(kind, status, started_at) VALUES('index','running',?)",
                              (time.time(),)).lastrowid
        status = "completed"
        try:
            self._phase("discover", self._discover_all)
            self._phase("process", self._process_with_embedding)
            if not self.cancel.is_set():
                self._phase("hash", self._hash_size_collisions)
        except Cancelled:
            status = "cancelled"
        except Exception:
            status = "failed"
            log.exception("indexing failed")
            raise
        finally:
            self.stats.cancelled = self.cancel.is_set()
            if self.stats.cancelled:
                status = "cancelled"
            self.stats.elapsed_s = round(time.perf_counter() - t0, 2)
            conn.execute("UPDATE jobs SET status=?, finished_at=?, stats=? WHERE id=?",
                         (status, time.time(), json.dumps(asdict(self.stats)), job_id))
            try:  # fold the write-ahead log back into the index so it can't grow without bound
                conn.execute("PRAGMA optimize")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass  # another window is reading; SQLite checkpoints later by itself
            self.progress("done", 1, 1, status)
        return self.stats

    def _phase(self, name: str, fn) -> None:
        t = time.perf_counter()
        fn()
        self.stats.phase_s[name] = round(time.perf_counter() - t, 2)

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    # ------------------------------------------------------------------ discover
    def _discover_all(self) -> None:
        conn = self.conn
        rows = library.roots(conn)
        self.stats.roots = len(rows)
        policy = library.scope_policy(conn, self.settings)
        gen = int(db.meta_get(conn, "scan_gen", "0")) + 1
        db.meta_set(conn, "scan_gen", str(gen))
        for r in rows:
            if not os.path.isdir(r["path"]):
                log.warning("root missing, skipped (index kept): %s", r["path"])
                continue
            try:
                root_is_link = is_link_like(os.lstat(r["path"]))
            except OSError:
                root_is_link = True
            if root_is_link:
                log.warning("root is now a junction or link, skipped (index kept): %s", r["path"])
                self.stats.unreadable_dirs.append(f"{r['path']}: selected folder is now a link; skipped")
                continue
            unreadable: set[str] = set()
            self._walk_root(r["id"], r["path"], policy, gen, unreadable)
            # Only a complete walk proves absence; a cancelled walk never deletes rows, and files
            # below a folder that could not be listed this time (permissions, a network hiccup)
            # are kept rather than treated as deleted.
            self._check_cancel()
            gone = [x["id"] for x in conn.execute(
                "SELECT id, parent_key FROM files WHERE root_id=? AND seen_gen<?", (r["id"], gen))
                if not _under_any(x["parent_key"], unreadable)]
            with db.tx(conn):
                library.delete_file_rows(conn, gone)
            self.stats.removed += len(gone)

    def _walk_root(self, root_id: int, root: str, policy: ScopePolicy, gen: int, unreadable: set[str]) -> None:
        conn = self.conn
        stack: list[tuple[str, str | None]] = [(root, None)]
        now = time.time()
        while stack:
            self._check_cancel()
            d, proj = stack.pop()
            try:
                with os.scandir(long_path(d)) as it:
                    entries = list(it)
            except OSError as e:
                self.stats.unreadable_dirs.append(f"{d}: {e.strerror or e}")
                unreadable.add(norm(d))
                continue
            names = {e.name for e in entries}
            if proj is None and has_project_marker(d, names):
                proj = d
            existing = {
                row["path_key"]: row for row in conn.execute(
                    "SELECT id, path_key, size, mtime_ns, stage, status FROM files WHERE parent_key=?", (norm(d),))
            }
            inserts, updates, touch = [], [], []
            for e in entries:
                full = os.path.join(d, e.name)
                try:
                    e.name.encode("utf-8")
                except UnicodeEncodeError:  # NTFS allows unpaired surrogates; SQLite text cannot hold them
                    shown = full.encode("utf-8", "backslashreplace").decode("utf-8")
                    self.stats.unreadable_dirs.append(f"{shown}: name is not valid Unicode; skipped")
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if is_link_like(st):
                    self.stats.skipped_links += 1
                    continue
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    # Inside a software project, its build output is regenerated by tools: skip it.
                    # (Outside projects a folder called "build" may be the user's own.)
                    if proj is not None and e.name.lower() in PROJECT_OUTPUT_DIRS:
                        continue
                    if not policy.excluded_name(e.name) and policy.check(full) is None:
                        stack.append((full, proj))
                    continue
                if is_system_file(st) or policy.check(full) is not None:
                    continue
                self.stats.discovered += 1
                key = norm(full)
                ext = os.path.splitext(e.name)[1].lower()
                cloud = 1 if is_cloud_placeholder(st) else 0
                old = existing.get(key)
                retry = old is not None and old["status"] in RETRY_STATUSES
                if old is None:
                    inserts.append((root_id, full, key, norm(d), e.name, ext, st.st_size, st.st_mtime_ns,
                                    getattr(st, "st_birthtime_ns", None) or st.st_ctime_ns,
                                    getattr(st, "st_file_attributes", 0), kind_for(ext, e.name),
                                    "cloud" if cloud else "new", proj, gen, now))
                elif old["size"] != st.st_size or old["mtime_ns"] != st.st_mtime_ns or old["stage"] == 0 or retry:
                    if old["stage"] > 0 and not retry:
                        self.stats.changed += 1
                    updates.append((old["id"], st.st_size, st.st_mtime_ns, getattr(st, "st_file_attributes", 0),
                                    "cloud" if cloud else "new", proj, gen))
                else:
                    self.stats.unchanged += 1
                    touch.append((gen, proj, old["id"]))
            if inserts or updates or touch:
                with db.tx(conn):
                    for row in inserts:
                        cur = conn.execute(
                            "INSERT INTO files(root_id, path, path_key, parent_key, name, ext, size, mtime_ns, ctime_ns,"
                            " attrs, kind, status, project_root, seen_gen, first_seen, stage)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)", row)
                        conn.execute("INSERT INTO names_fts(rowid, name, folders) VALUES(?,?,?)",
                                     (cur.lastrowid, name_tokens(row[4]), folder_tokens(row[1])))
                    if updates:
                        library.delete_chunks_for_files(conn, [u[0] for u in updates])
                        conn.executemany(
                            "UPDATE files SET size=?, mtime_ns=?, attrs=?, status=?, project_root=?, seen_gen=?,"
                            " stage=0, hash=NULL, prehash=NULL, error=NULL, img_stage=0, img_tags=NULL WHERE id=?",
                            [(u[1], u[2], u[3], u[4], u[5], u[6], u[0]) for u in updates])
                    if touch:
                        conn.executemany("UPDATE files SET seen_gen=?, project_root=? WHERE id=?", touch)
                self.stats.new += len(inserts)
            self.progress("discover", self.stats.discovered, 0, d)

    # ------------------------------------------------------------------ process
    def _task_payload(self, row: sqlite3.Row) -> dict:
        s = self.settings
        return {
            "path": long_path(row["path"]),
            "do_hash": True,
            "hash_max_bytes": s.hash_max_bytes,
            "metadata_only": row["status"] == "cloud",
            "max_text_chars": s.max_text_chars,
            "max_pdf_pages": s.max_pdf_pages,
            "max_extract_bytes": s.max_extract_bytes,
        }

    def _process_with_embedding(self) -> None:
        conn = self.conn
        work = conn.execute(
            "SELECT f.id, f.path, f.status FROM files f JOIN roots r ON r.id=f.root_id"
            " WHERE r.enabled=1 AND f.stage=0 ORDER BY f.size").fetchall()
        total = len(work)
        done_event = threading.Event()
        embed_thread = None
        if self.embed_enabled:
            embed_thread = threading.Thread(target=self._embed_loop, args=(done_event,), daemon=True,
                                            name="lfi-embed")
            embed_thread.start()
        try:
            if total:
                retry: list[sqlite3.Row] = []
                from .resources import governor

                gov, readers = governor(self.settings.speed), self.settings.worker_count()
                # Reading is rarely the bottleneck (it outpaces embedding ~3x): start at the usual
                # size and only give workers back when the machine gets busy.
                with IsolatedPool(readers, self.settings.extract_timeout_s,
                                  size_fn=lambda cur: gov.workers("reader", cur, ceiling=readers)) as pool:
                    self._run_tasks(pool, work, total, retry)
                    self.stats.reader_workers = pool.peak_workers
                    if retry and not self.cancel.is_set():
                        time.sleep(1.0)
                        self._run_tasks(pool, retry, total, None)
                    self.stats.worker_restarts = pool.restarts
            if self.settings.image_search and not self.cancel.is_set():
                # Runs while the text-embedding thread keeps going, so new auto-tags get embedded too.
                self._phase("images", self._index_images)
        finally:
            done_event.set()
            if embed_thread is not None:
                while embed_thread.is_alive():
                    embed_thread.join(0.2)
        self._check_cancel()

    def _run_tasks(self, pool: IsolatedPool, rows: list[sqlite3.Row], total: int,
                   retry: list | None) -> None:
        by_id = {r["id"]: r for r in rows}
        buf: list[tuple[int, dict]] = []
        last = time.monotonic()
        tasks = ((r["id"], "process", self._task_payload(r)) for r in rows)
        for fid, res in pool.imap_unordered(tasks, self.cancel):
            if res.get("status") == "changed" and retry is not None:
                retry.append(by_id[fid])
                self.stats.changed_during_read += 1
                continue
            buf.append((fid, res))
            if len(buf) >= 64 or time.monotonic() - last > 1.0:
                self._write_results(buf)
                buf.clear()
                last = time.monotonic()
                self.progress("process", self.stats.processed, total, by_id[fid]["path"])
        if buf:
            self._write_results(buf)
        self.progress("process", self.stats.processed, total, "")

    def _write_results(self, results: list[tuple[int, dict]]) -> None:
        conn = self.conn
        s = self.settings
        now = time.time()
        with db.tx(conn):
            for fid, res in results:
                status = res.get("status", "ok")
                if status == "missing":
                    library.delete_file_rows(conn, [fid])
                    continue
                self.stats.processed += 1
                if not res.get("ok", False):
                    if status == "timeout":
                        self.stats.timeouts += 1
                    if status != "changed":
                        self.stats.errors += 1
                if status == "encrypted":
                    self.stats.encrypted += 1
                if status == "metadata_only":
                    self.stats.metadata_only += 1
                text = res.get("text") or ""
                chunks = chunk_text(text, s.chunk_chars, s.chunk_overlap, s.max_chunks_per_file) if text else []
                stage = 0 if status == "changed" else 1
                conn.execute(
                    "UPDATE files SET hash=COALESCE(?, hash), ino=COALESCE(?, ino), dev=COALESCE(?, dev),"
                    " nlink=COALESCE(?, nlink), status=?, error=?, stage=?, text_chars=?, truncated=?, title=?,"
                    " width=?, height=?, phash=?, dhash=?, exif_time=?, camera=?, is_screenshot=?, indexed_at=?,"
                    " kind=CASE WHEN kind='other' AND ?>0 THEN 'document' ELSE kind END WHERE id=?",
                    (res.get("hash"), res.get("ino"), res.get("dev"), res.get("nlink"), status,
                     res.get("error") or res.get("note"), stage, len(text), int(bool(res.get("truncated"))),
                     res.get("title"), res.get("width"), res.get("height"), res.get("phash"), res.get("dhash"),
                     res.get("exif_time"), res.get("camera"), int(bool(res.get("is_screenshot"))), now,
                     len(chunks), fid),
                )
                library.delete_chunks_for_files(conn, [fid])
                if chunks:
                    conn.executemany("INSERT INTO chunks(file_id, ord, text) VALUES(?,?,?)",
                                     [(fid, i, c) for i, c in enumerate(chunks)])

    # ------------------------------------------------------------------ embed
    def _ensure_space(self, conn: sqlite3.Connection, backend: EmbeddingBackend) -> str:
        slug = db.ensure_vector_tables(conn, backend.model_id, backend.dim)
        if db.meta_get(conn, "active_space") != slug:
            old = db.meta_get(conn, "active_space")
            with db.tx(conn):
                conn.execute("UPDATE files SET stage=1 WHERE stage=2")
                db.meta_set(conn, "active_space", slug)
                if old and old != slug:
                    db.drop_vector_tables(conn, old)
        return slug

    def _embed_loop(self, processing_done: threading.Event) -> None:
        conn = db.connect(self.db_path)
        try:
            if self.backend is None:
                try:
                    self.backend = create_backend(self.settings)
                except ModelUnavailable as e:
                    self.stats.embed_error = str(e)
                    return
                except Exception as e:
                    self.stats.embed_error = f"embedding backend failed: {e}"
                    log.exception("embedding backend failed")
                    return
            self.stats.embed_device = self.backend.device
            slug = self._ensure_space(conn, self.backend)
            chunks = VectorStore(conn, slug, self.backend.dim, "chunks")
            files = VectorStore(conn, slug, self.backend.dim, "files")
            if self._parallel_ok() and self._embed_parallel(conn, chunks, files, processing_done):
                return
            while not self.cancel.is_set():
                n = self._embed_batch(conn, chunks, files)
                if n == 0:
                    if processing_done.is_set():
                        if self._embed_batch(conn, chunks, files) == 0:
                            break
                    else:
                        processing_done.wait(0.5)
        except Exception as e:
            self.stats.embed_error = f"{type(e).__name__}: {e}"
            log.exception("embedding failed")
        finally:
            conn.close()

    def _collect(self, conn: sqlite3.Connection, exclude: set[int] | None = None, max_texts: int = 256) -> dict | None:
        """The next files waiting for embeddings (stage 1) and the texts to embed for them."""
        if exclude:
            rows = conn.execute("SELECT * FROM files WHERE stage=1 AND id NOT IN (SELECT value FROM json_each(?))"
                                " LIMIT 64", (json.dumps(sorted(exclude)),)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM files WHERE stage=1 LIMIT 64").fetchall()
        if not rows:
            return None
        texts: list[str] = []
        owners: list[tuple[int, int | None]] = []
        read: dict[int, list[int]] = {}
        for r in rows:
            ch = conn.execute("SELECT id, text FROM chunks WHERE file_id=? ORDER BY ord", (r["id"],)).fetchall()
            if read and len(texts) + 1 + len(ch) > max_texts:
                break
            read[r["id"]] = [c["id"] for c in ch]
            texts.append(descriptor(r))
            owners.append((r["id"], None))
            for c in ch:
                if c["text"]:
                    texts.append(c["text"])
                    owners.append((r["id"], c["id"]))
        return {"texts": texts, "owners": owners, "read": read}

    def _embed_batch(self, conn: sqlite3.Connection, chunk_store: VectorStore, file_store: VectorStore,
                     max_texts: int = 256) -> int:
        batch = self._collect(conn, max_texts=max_texts)
        if batch is None:
            return 0
        self._store(conn, chunk_store, file_store, batch, self.backend.embed_documents(batch["texts"]))
        return len(batch["read"])

    def _store(self, conn: sqlite3.Connection, chunk_store: VectorStore, file_store: VectorStore, batch: dict,
               vecs) -> None:
        owners, read = batch["owners"], batch["read"]
        per_file: dict[int, dict] = {}
        chunk_items: list[tuple[int, int, np.ndarray]] = []
        for (fid, cid), v in zip(owners, vecs):
            slot = per_file.setdefault(fid, {"desc": None, "chunks": []})
            if cid is None:
                slot["desc"] = v
            else:
                slot["chunks"].append(v)
                chunk_items.append((fid, cid, v))
        file_ids, file_vecs = [], []
        for fid, slot in per_file.items():
            if slot["chunks"]:
                v = 0.3 * slot["desc"] + 0.7 * np.mean(slot["chunks"], axis=0)
            else:
                v = slot["desc"]
            n = np.linalg.norm(v)
            file_ids.append(fid)
            file_vecs.append(v / n if n else v)
        with db.tx(conn):
            # Re-check: a file may have been re-queued (changed) while we embedded, or gained a
            # chunk (the image phase adds auto-tag chunks concurrently). Such files stay at stage 1
            # and are embedded again with everything they now have.
            live = {r[0] for r in conn.execute(
                f"SELECT id FROM files WHERE stage=1 AND id IN ({','.join('?' * len(file_ids))})", file_ids)}
            live = {f for f in live if [c[0] for c in conn.execute(
                "SELECT id FROM chunks WHERE file_id=? ORDER BY ord", (f,))] == read[f]}
            keep_c = [(c, v) for fid, c, v in chunk_items if fid in live]
            if keep_c:
                chunk_store.add([c for c, _ in keep_c], np.vstack([v for _, v in keep_c]))
            keep_f = [(f, v) for f, v in zip(file_ids, file_vecs) if f in live]
            if keep_f:
                file_store.add([f for f, _ in keep_f], np.vstack([v for _, v in keep_f]))
            conn.executemany("UPDATE files SET stage=2 WHERE id=? AND stage=1", [(f,) for f, _ in keep_f])
            if not self.settings.store_text:  # the chunks_au trigger removes the text from chunks_fts too
                conn.executemany("UPDATE chunks SET text='' WHERE file_id=? AND text <> ''", [(f,) for f, _ in keep_f])
        self.stats.embedded_files += len(keep_f)
        self.stats.embedded_chunks += len(keep_c)
        self.progress("embed", self.stats.embedded_files, 0, "")

    # ------------------------------------------------------------------ parallel GPU embedding
    EMBED_TASK = "lfi.embeddings:embed_task"  # worker entry point (overridable in tests)

    def _parallel_ok(self) -> bool:
        from .embeddings import FastEmbedBackend

        return (isinstance(self.backend, FastEmbedBackend) and self.backend.device not in ("cpu", "")
                and self.settings.speed != "gentle")

    def _embed_parallel(self, conn: sqlite3.Connection, chunk_store: VectorStore, file_store: VectorStore,
                        processing_done: threading.Event) -> bool:
        """Several copies of the model, one per worker process (two GPU sessions in one process
        crash ONNX Runtime's DirectML provider), fed batches ahead of time so reading the database
        overlaps with GPU work. The governor sizes the pool from free GPU memory, RAM and CPU and
        shrinks it when the machine gets busy. A failed batch is embedded here instead; repeated
        failures switch back to the single in-process model (returns False). Nothing is lost
        either way: files only leave stage 1 once their vectors are stored."""
        from .resources import governor

        gov = governor(self.settings.speed)
        inflight: dict[int, dict] = {}
        state = {"seq": 0, "failures": 0}
        payload = {"model": self.backend.model_id, "device": self.settings.device, "batch": 0}

        def tasks():
            while not self.cancel.is_set() and state["failures"] < 3:
                taken: set[int] = set()
                for b in inflight.values():
                    taken.update(b["read"])
                batch = self._collect(conn, exclude=taken)
                if batch is None:
                    return
                state["seq"] += 1
                inflight[state["seq"]] = batch
                yield state["seq"], self.EMBED_TASK, {**payload, "texts": batch["texts"]}

        with IsolatedPool(gov.workers("gpu_embed", 1), timeout=600.0,
                          size_fn=lambda cur: gov.workers("gpu_embed", cur)) as pool:
            while not self.cancel.is_set():
                got = 0
                for key, res in pool.imap_unordered(tasks(), self.cancel):
                    batch = inflight.pop(key)
                    got += 1
                    if res.get("ok") and res.get("dim") == self.backend.dim:
                        vecs = np.frombuffer(res["vecs"], dtype=np.float32).reshape(-1, res["dim"])
                        if res.get("device") == "cpu":  # that worker lost the GPU: copies would fight for CPU
                            pool.size_fn = lambda cur: 1
                    else:
                        state["failures"] += 1
                        log.warning("embedding worker failed (%s); embedding that batch here", res.get("error"))
                        vecs = self.backend.embed_documents(batch["texts"])
                    self._store(conn, chunk_store, file_store, batch, vecs)
                    self.stats.gpu_workers = max(self.stats.gpu_workers, pool.peak_workers)
                if state["failures"] >= 3:
                    log.warning("parallel embedding turned off after repeated worker failures")
                    inflight.clear()  # their files are still at stage 1; the serial loop takes them
                    return False
                if got == 0:
                    if processing_done.is_set() and self._collect(conn) is None:
                        break
                    processing_done.wait(0.5)
        return True

    # ------------------------------------------------------------------ images (vision)
    def _index_images(self) -> None:
        from .vision import DECODABLE, VISION_PAIRS, Tagger, VisionBackend, pair_is_cached

        conn = self.conn
        pair = VISION_PAIRS.get(self.settings.vision_pair)
        if pair is None:
            self.stats.image_error = f"unknown image model '{self.settings.vision_pair}'"
            return
        if not pair_is_cached(pair.key):
            self.stats.image_error = (f"Image search model '{pair.key}' is not downloaded (Settings > Image search, "
                                      f"or: lfi models download-vision {pair.key})")
            return
        slug = db.ensure_image_table(conn, f"vision:{pair.key}", pair.dim)
        if db.meta_get(conn, "active_image_space") != slug:
            old = db.meta_get(conn, "active_image_space")
            with db.tx(conn):
                conn.execute("UPDATE files SET img_stage=0, img_tags=NULL WHERE kind='image'")
                library.delete_chunks_where(conn, "ord=?", (TAG_CHUNK_ORD,))
                db.meta_set(conn, "active_image_space", slug)
                if old and old != slug:
                    db.drop_image_table(conn, old)
        exts = sorted(DECODABLE)
        marks = ",".join("?" * len(exts))
        rows = conn.execute(
            "SELECT f.id, f.path, f.name, f.stage, f.parent_key, f.width, f.height, f.mtime_ns FROM files f"
            " JOIN roots r ON r.id=f.root_id WHERE r.enabled=1 AND"
            f" f.kind='image' AND f.status='ok' AND f.img_stage=0 AND lower(f.ext) IN ({marks}) ORDER BY f.id",
            exts).fetchall()
        skip = not_worth_understanding(rows)
        if skip:
            with db.tx(conn):  # still found by name and folder; looked at again if the file changes
                conn.executemany("UPDATE files SET img_stage=3, error=NULL WHERE id=?", [(i,) for i in skip])
            self.stats.images_skipped += len(skip)
            rows = [r for r in rows if r["id"] not in skip]
        if not rows:
            return
        if self.vision is None:
            self.vision = VisionBackend(pair, load_image=False)
        tagger = Tagger(self.vision)
        store = VectorStore(conn, slug, pair.dim, "images")
        payload = {"pair": pair.key, "device": self.settings.device, "batch": 0,  # 0 = auto per device
                   "threads": self.settings.vision_threads or None}
        batches = [rows[i:i + 16] for i in range(0, len(rows), 16)]
        done = stored = 0
        failed: list[tuple[int, str]] = []
        from .resources import governor

        gov = governor(self.settings.speed)
        with IsolatedPool(1, timeout=max(180.0, self.settings.extract_timeout_s),
                          size_fn=lambda cur: gov.workers("image", cur)) as pool:
            tasks = ((i, self.VISION_TASK, {**payload, "paths": [long_path(r["path"]) for r in b]})
                     for i, b in enumerate(batches))
            singles: list[sqlite3.Row] = []
            for bi, res in pool.imap_unordered(tasks, self.cancel):
                if not res.get("ok"):
                    singles.extend(batches[bi])  # crash/timeout: retry one by one to isolate the culprit
                    continue
                self._store_images(batches[bi], res, store, tagger)
                self.stats.image_workers = max(self.stats.image_workers, pool.peak_workers)
                done += len(batches[bi])
                stored += len(batches[bi])
                self.progress("images", done, len(rows), "")
            by_id = {r["id"]: r for r in singles}
            one = ((r["id"], self.VISION_TASK, {**payload, "paths": [long_path(r["path"])]}) for r in singles)
            for fid, res in pool.imap_unordered(one, self.cancel):
                if res.get("ok"):
                    self._store_images([by_id[fid]], res, store, tagger)
                    stored += 1
                else:
                    failed.append((fid, res.get("error") or "image worker failed"))
                done += 1
                self.progress("images", done, len(rows), by_id[fid]["path"])
        if failed and stored == 0:
            # Nothing worked at all (model could not load, out of memory, machine too busy): a
            # problem with the worker, not with these images. Leave them queued for the next run.
            self.stats.image_error = f"Image search worker failed; will retry next time ({failed[0][1]})"
        elif failed:
            with db.tx(conn):  # the run worked for other images, so these files are the problem
                conn.executemany("UPDATE files SET img_stage=2, error=? WHERE id=?", [(e, f) for f, e in failed])
            self.stats.images_failed += len(failed)

    def _store_images(self, rows: list[sqlite3.Row], res: dict, store: VectorStore, tagger) -> None:
        conn = self.conn
        self.stats.image_device = res.get("device", "")
        with db.tx(conn):
            for i, r in enumerate(rows):
                blob = res["vecs"][i]
                if blob is None:
                    conn.execute("UPDATE files SET img_stage=2 WHERE id=?", (r["id"],))
                    self.stats.images_failed += 1
                    continue
                v = np.frombuffer(blob, dtype=np.float32)
                if v.shape[0] != store.dim:  # model files replaced underneath us; never crash the run
                    conn.execute("UPDATE files SET img_stage=2, error=? WHERE id=?",
                                 (f"image model returned {v.shape[0]} values, expected {store.dim}", r["id"]))
                    self.stats.images_failed += 1
                    continue
                store.add([r["id"]], v[None, :])
                tags = [t for t, _ in tagger.tags(v)]
                conn.execute("UPDATE files SET img_stage=1, img_tags=? WHERE id=?", ("; ".join(tags) or None, r["id"]))
                library.delete_chunks_where(conn, "file_id=? AND ord=?", (r["id"], TAG_CHUNK_ORD))
                if tags:
                    conn.execute("INSERT INTO chunks(file_id, ord, text) VALUES(?,?,?)",
                                 (r["id"], TAG_CHUNK_ORD, "Image appears to show: " + ", ".join(tags)))
                    conn.execute("UPDATE files SET stage=1 WHERE id=? AND stage=2", (r["id"],))  # re-embed text
                self.stats.images_embedded += 1

    # ------------------------------------------------------------------ large-file hashing
    def _hash_size_collisions(self) -> None:
        conn = self.conn
        rows = conn.execute(
            "SELECT id, path, size FROM files WHERE hash IS NULL AND size>0 AND status NOT IN ('metadata_only','cloud')"
            " AND size IN (SELECT size FROM files WHERE size>0 GROUP BY size HAVING count(*)>1)").fetchall()
        if not rows:
            return
        with IsolatedPool(self.settings.worker_count(), max(600.0, self.settings.extract_timeout_s)) as pool:
            pre: dict[int, str] = {}
            for fid, res in pool.imap_unordered(((r["id"], "hash", {"path": long_path(r["path"]), "pre": True})
                                                 for r in rows), self.cancel):
                if res.get("ok"):
                    pre[fid] = res["prehash"]
            self._check_cancel()
            with db.tx(conn):
                conn.executemany("UPDATE files SET prehash=? WHERE id=?", [(v, k) for k, v in pre.items()])
            size_of = {r["id"]: r["size"] for r in rows}
            groups: dict[tuple, list[int]] = {}
            for fid, ph in pre.items():
                groups.setdefault((size_of[fid], ph), []).append(fid)
            need = [fid for g in groups.values() if len(g) > 1 for fid in g]
            path_of = {r["id"]: r["path"] for r in rows}
            done = 0
            for fid, res in pool.imap_unordered(((f, "hash", {"path": long_path(path_of[f])}) for f in need),
                                                self.cancel):
                if res.get("ok"):
                    conn.execute("UPDATE files SET hash=? WHERE id=?", (res["hash"], fid))
                    self.stats.large_hashed += 1
                done += 1
                self.progress("hash", done, len(need), path_of[fid])


TINY_IMAGE_PX = 100  # icons and toolbar images: not photos, the image model learns nothing from them
FRAME_DUMP_MIN = 200  # images in one folder before it can count as frames extracted from a video
FRAMES_KEPT = 60  # per frame dump, spread evenly over the sequence


def not_worth_understanding(rows) -> set[int]:
    """Images the image model would waste time on (img_stage 3): tiny pictures, and all but an even
    sample of *frame dumps* - hundreds of same-size images in one folder written within seconds of
    each other, as video tools produce. A phone's camera folder never qualifies: photos are taken
    over days, not seconds."""
    skip: set[int] = set()
    by_dir: dict[str, list] = {}
    for r in rows:
        w, h = r["width"], r["height"]
        if w and h and max(w, h) < TINY_IMAGE_PX:
            skip.add(r["id"])
        else:
            by_dir.setdefault(r["parent_key"], []).append(r)
    for group in by_dir.values():
        n = len(group)
        if n < FRAME_DUMP_MIN:
            continue
        sizes: dict[tuple, int] = {}
        for r in group:
            sizes[(r["width"], r["height"])] = sizes.get((r["width"], r["height"]), 0) + 1
        same_size = max(sizes.values()) >= 0.9 * n
        times = [r["mtime_ns"] for r in group]
        written_quickly = (max(times) - min(times)) / 1e9 < n * 1.0  # under a second per image
        if same_size and written_quickly:
            ordered = sorted(group, key=lambda r: r["name"])
            step = n / FRAMES_KEPT
            keep = {ordered[int(i * step)]["id"] for i in range(FRAMES_KEPT)} | {ordered[-1]["id"]}
            skip.update(r["id"] for r in group if r["id"] not in keep)
    return skip


def _under_any(parent_key: str, dirs: set[str]) -> bool:
    """True if folder ``parent_key`` is one of ``dirs`` or below one (checks each ancestor)."""
    if not dirs:
        return False
    cur = parent_key
    while True:
        if cur in dirs:
            return True
        nxt = os.path.dirname(cur)
        if nxt == cur:
            return False
        cur = nxt


def embed_pending(settings: Settings, db_path: str | None = None, backend: EmbeddingBackend | None = None,
                  progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> IndexStats:
    """Embed files that were indexed while no model was available (no rescan)."""
    ix = Indexer(settings, db_path, backend, progress, cancel)
    done = threading.Event()
    done.set()
    ix._embed_loop(done)
    return ix.stats
