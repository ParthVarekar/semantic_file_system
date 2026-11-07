"""Journaled, verified, reversible file operations.

Guarantees (each enforced here, not delegated to callers):

* No overwrite, ever. Same-volume moves use ``os.rename``, which on Windows calls
  ``MoveFileExW`` *without* ``MOVEFILE_REPLACE_EXISTING`` and fails if the target exists.
  On POSIX ``link()+unlink()`` gives the same no-replace semantics.
* Identity check before acting: path in scope, not a link/junction, not an online-only cloud
  placeholder, not inside a software project, and size + mtime (+ file ID, + optional content
  hash) still match what was indexed. Otherwise the file is left untouched.
* Write-ahead journal: an ``operations`` row is committed as ``pending`` *before* the
  filesystem changes and marked ``done`` after verification. ``recover()`` reconciles any
  interrupted operation from the actual filesystem state at the next start.
* Quarantine stays on the source volume (atomic rename, no copy) and keeps an append-only
  ``manifest.jsonl`` so files remain restorable even if the index database is deleted.
* Cross-volume moves are disabled by default; when enabled they copy to ``*.lfi-partial``,
  fsync, verify the BLAKE3 hash, rename into place, and only then remove the source.
* Permanent deletion only removes files that are already in quarantine and requires an
  explicit confirmation argument from the UI.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field

from . import config, db, library
from .config import Settings
from .extract import hash_file
from .safety import (IS_WINDOWS, ScopePolicy, drive_system_dir, find_project_root, is_cloud_placeholder,
                     is_link_like, is_within, link_between, long_path, norm, protected_directories, real_path,
                     same_volume)

log = logging.getLogger(__name__)

PARTIAL = ".lfi-partial"
CONFIRM_PURGE = "DELETE PERMANENTLY"


class OpRefused(Exception):
    """The operation was not attempted because a safety check failed."""


class _WriterLock:
    """One writer at a time across processes (the app, the CLI, a second window). A batch of file
    operations and crash recovery both need the journal to themselves: recovery run by another
    process would mistake a live batch's 'pending' rows for crash debris. The OS releases the
    lock if the holder dies, so a crash can never leave it stuck."""

    def __init__(self, path: str):
        self.path = path
        self.fh = None

    def acquire(self, wait_s: float) -> bool:
        fh = open(self.path, "a+b")
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fh.seek(0)
                if IS_WINDOWS:
                    import msvcrt

                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fh = fh
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    fh.close()
                    return False
                time.sleep(0.2)

    def release(self) -> None:
        if self.fh is None:
            return
        try:
            self.fh.seek(0)
            if IS_WINDOWS:
                import msvcrt

                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        finally:
            self.fh.close()
            self.fh = None


def _exclusive(method):
    """Run a batch method while holding the writer lock (waits up to 30 s for another one)."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if self._lock_depth:
            return method(self, *args, **kwargs)
        if not self._lock.acquire(30.0):
            raise OpRefused("another window or command is moving files right now; try again when it has finished")
        self._lock_depth += 1
        try:
            return method(self, *args, **kwargs)
        finally:
            self._lock_depth -= 1
            self._lock.release()

    return wrapper


@dataclass
class OpResult:
    op_id: int | None
    src: str
    dst: str
    ok: bool
    message: str = ""


@dataclass
class BatchResult:
    batch_id: int | None
    results: list[OpResult] = field(default_factory=list)

    @property
    def ok(self) -> list[OpResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[OpResult]:
        return [r for r in self.results if not r.ok]


def _exists(p: str) -> bool:
    return os.path.lexists(long_path(p))


def _rename_noreplace(src: str, dst: str) -> None:
    if IS_WINDOWS:
        os.rename(long_path(src), long_path(dst))  # raises FileExistsError if dst exists
    else:
        os.link(src, dst)  # raises FileExistsError if dst exists
        os.unlink(src)


def _fsync_dir(path: str) -> None:
    if IS_WINDOWS:
        return  # NTFS metadata journaling; directory handles cannot be fsynced portably
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _quarantine_root_of(qpath: str) -> str:
    """Quarantined files live at ``<quarantine root>\\<batch>\\<operation>\\<name>``."""
    return os.path.dirname(os.path.dirname(os.path.dirname(qpath)))


def _unique_name(path: str, tag: str = "") -> str:
    base, ext = os.path.splitext(path)
    if not _exists(path):
        return path
    for i in range(1 if tag else 2, 10_000):
        label = f" ({tag} {i})" if tag and i > 1 else (f" ({tag})" if tag else f" ({i})")
        cand = f"{base}{label}{ext}"
        if not _exists(cand):
            return cand
    raise OpRefused("could not find a free file name")


class FileOps:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, policy: ScopePolicy):
        self.conn = conn
        # The journal must be durable *before* a rename happens, even across power loss.
        conn.execute("PRAGMA synchronous=FULL")
        self.settings = settings
        self.policy = policy
        self.audit_path = config.logs_dir() / "operations.jsonl"
        self._unexpected = False
        self._lock = _WriterLock(str(config.app_home() / "ops.lock"))
        self._lock_depth = 0

    # ------------------------------------------------------------------ journal helpers
    def _audit(self, **rec) -> None:
        rec["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(self.audit_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _manifest(self, qroot: str, **rec) -> None:
        rec["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(os.path.join(long_path(qroot), "manifest.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _new_batch(self, kind: str, description: str) -> int:
        return self.conn.execute(
            "INSERT INTO batches(kind, description, status, created_at) VALUES(?,?, 'running', ?)",
            (kind, description, time.time())).lastrowid

    def _finish_batch(self, batch_id: int, res: BatchResult) -> None:
        if self._unexpected:
            self._unexpected = False
            self.recover()
        status = "completed" if not res.failed else ("partial" if res.ok else "failed")
        self.conn.execute("UPDATE batches SET status=?, finished_at=? WHERE id=?", (status, time.time(), batch_id))

    def _journal(self, batch_id: int, kind: str, src: str, dst: str, exp: dict, reason: str,
                 reverses: int | None = None) -> int:
        now = time.time()
        with db.tx(self.conn):
            op_id = self.conn.execute(
                "INSERT INTO operations(batch_id, kind, src, dst, size, mtime_ns, ino, hash, state, reason,"
                " reverses, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?, 'planned', ?,?,?,?)",
                (batch_id, kind, src, dst, exp.get("size"), exp.get("mtime_ns"), exp.get("ino"), exp.get("hash"),
                 reason, reverses, now, now)).lastrowid
        return op_id

    def _set_state(self, op_id: int, state: str, error: str | None = None, **cols) -> None:
        sets = ", ".join(f"{k}=?" for k in cols)
        args = list(cols.values())
        self.conn.execute(
            f"UPDATE operations SET state=?, error=?, updated_at=?{', ' + sets if sets else ''} WHERE id=?",
            [state, error, time.time(), *args, op_id])

    # ------------------------------------------------------------------ validation
    def _validate_source(self, path: str, exp: dict, *, in_scope: bool = True, verify_hash: bool = False,
                         allow_project: bool = False, container: str | None = None) -> os.stat_result:
        """``container``: for files outside the selected folders (quarantined copies), the folder
        that must hold the file without any junction or symbolic link in between."""
        if in_scope:
            reason = self.policy.check_physical(path)
            if reason:
                raise OpRefused(f"not allowed: {reason}")
        else:
            link = link_between(path, container or self.policy.root_for(path))
            if link:
                raise OpRefused(f"the path goes through a junction or symbolic link ({link})")
        try:
            st = os.lstat(long_path(path))
        except FileNotFoundError:
            raise OpRefused("file no longer exists")
        except OSError as e:
            raise OpRefused(f"cannot access file: {e.strerror or e}")
        if is_link_like(st):
            raise OpRefused("is a symbolic link / junction; links are never moved")
        if is_cloud_placeholder(st):
            raise OpRefused("online-only cloud file; open it in OneDrive first")
        if not os.path.isfile(long_path(path)):
            raise OpRefused("not a regular file")
        if exp.get("size") is not None and st.st_size != exp["size"]:
            raise OpRefused("file size changed since it was indexed; re-index first")
        if exp.get("mtime_ns") is not None and st.st_mtime_ns != exp["mtime_ns"]:
            raise OpRefused("file was modified since it was indexed; re-index first")
        if exp.get("ino") and str(st.st_ino) != str(exp["ino"]):
            raise OpRefused("a different file now exists at this path; re-index first")
        if not allow_project:
            root = self.policy.root_for(path)
            if root and find_project_root(path, root):
                raise OpRefused("inside a software project; project contents are never reorganized")
        if verify_hash and exp.get("hash"):
            if hash_file(long_path(path)) != exp["hash"]:
                raise OpRefused("content no longer matches the indexed hash")
        return st

    @staticmethod
    def _missing_parents(dst: str) -> list[str]:
        """Folders that must be created for ``dst``, outermost first."""
        chain = []
        parent = os.path.dirname(dst)
        while parent and not _exists(parent):
            chain.append(parent)
            nxt = os.path.dirname(parent)
            if nxt == parent:
                break
            parent = nxt
        return list(reversed(chain))

    def _move_verified(self, op_id: int, src: str, dst: str, st: os.stat_result, exp: dict) -> str:
        """Perform the journaled move. Returns method used. Raises on failure (nothing changed)."""
        if _exists(dst):
            raise OpRefused("destination already exists (never overwritten)")
        created = self._missing_parents(dst)
        # Journal first (including folders about to be created), then touch the filesystem.
        self._set_state(op_id, "pending", created_dirs=json.dumps(created))
        try:
            for d in created:
                os.mkdir(long_path(d))
        except OSError as e:
            self._remove_created(created)
            raise OpRefused(f"cannot create destination folder: {e.strerror or e}")
        if same_volume(long_path(src), long_path(os.path.dirname(dst))):
            try:
                _rename_noreplace(src, dst)
            except FileExistsError:
                self._remove_created(created)
                raise OpRefused("destination already exists (never overwritten)")
            except PermissionError as e:
                self._remove_created(created)
                raise OpRefused(f"file is locked or access denied: {e.strerror or e}")
            except OSError as e:
                self._remove_created(created)
                raise OpRefused(f"move failed: {e.strerror or e}")
            try:
                dst_st = os.lstat(long_path(dst))
            except OSError:
                raise RuntimeError("moved file cannot be found at destination")  # journal stays pending
            if dst_st.st_size != st.st_size or _exists(src):
                raise RuntimeError("post-move verification failed")
            if str(dst_st.st_ino) != str(st.st_ino):
                log.warning("file ID changed across rename for %s", dst)
            _fsync_dir(os.path.dirname(dst))
            return "rename"
        if not self.settings.allow_cross_volume_moves:
            self._remove_created(created)
            raise OpRefused("destination is on another drive; cross-drive moves are disabled in Settings")
        return self._copy_verify_delete(op_id, src, dst, st, exp)

    def _copy_verify_delete(self, op_id: int, src: str, dst: str, st: os.stat_result, exp: dict) -> str:
        import blake3

        partial = dst + PARTIAL
        self._set_state(op_id, "pending", method="copy")
        h = blake3.blake3()
        try:
            with open(long_path(src), "rb") as fi, open(long_path(partial), "xb") as fo:
                while True:
                    b = fi.read(1 << 20)
                    if not b:
                        break
                    h.update(b)
                    fo.write(b)
                fo.flush()
                os.fsync(fo.fileno())
            shutil.copystat(long_path(src), long_path(partial))
        except OSError as e:
            if _exists(partial):
                os.remove(long_path(partial))
            raise OpRefused(f"copy failed: {e.strerror or e}")
        src_hash = h.hexdigest()
        if (exp.get("hash") and src_hash != exp["hash"]) or hash_file(long_path(partial)) != src_hash:
            os.remove(long_path(partial))
            raise OpRefused("copy verification failed (hash mismatch); source untouched")
        try:
            _rename_noreplace(partial, dst)
        except OSError as e:
            os.remove(long_path(partial))
            raise OpRefused(f"could not finalize copy: {e}")
        self._set_state(op_id, "copied", hash=src_hash)
        cur = os.lstat(long_path(src))
        if cur.st_size != st.st_size or cur.st_mtime_ns != st.st_mtime_ns:
            os.remove(long_path(dst))
            raise OpRefused("source changed during copy; copy removed, source untouched")
        os.remove(long_path(src))
        return "copy"

    def _remove_created(self, created: list[str]) -> None:
        for d in reversed(created):
            try:
                os.rmdir(long_path(d))
            except OSError:
                pass

    # ------------------------------------------------------------------ quarantine
    def quarantine_root_for(self, path: str) -> str:
        """Same-volume quarantine: the app's folder if it shares the volume, else a hidden
        ``.lfi-quarantine`` folder at the top of the user-selected root containing the file."""
        default = str(config.default_quarantine_dir())
        os.makedirs(default, exist_ok=True)
        if same_volume(long_path(path), default):
            return default
        root = self.policy.root_for(path)
        if root is None:
            raise OpRefused("file is outside the selected folders")
        row = self.conn.execute("SELECT quarantine_dir FROM roots WHERE path_key=?", (norm(root),)).fetchone()
        q = (row["quarantine_dir"] if row and row["quarantine_dir"] else None) or os.path.join(root, ".lfi-quarantine")
        link = link_between(q, root)
        if link:
            raise OpRefused(f"quarantine folder goes through a junction or symbolic link ({link})")
        if not _exists(q):
            os.makedirs(long_path(q), exist_ok=True)
            if IS_WINDOWS:
                import ctypes

                ctypes.windll.kernel32.SetFileAttributesW(long_path(q), 0x2)  # hidden
        return q

    @_exclusive
    def quarantine(self, file_ids: list[int], reason: str, basket: str | None = None,
                   verify_keepers: dict[int, int] | None = None) -> BatchResult:
        """Move indexed files into quarantine. ``verify_keepers`` maps a duplicate's file id to
        its keeper's file id: the keeper must still exist with identical content."""
        batch = self._new_batch("quarantine", reason)
        res = BatchResult(batch)
        for fid in file_ids:
            row = self.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
            if row is None:
                res.results.append(OpResult(None, f"#{fid}", "", False, "not in index"))
                continue
            src = row["path"]
            exp = {"size": row["size"], "mtime_ns": row["mtime_ns"], "ino": row["ino"], "hash": row["hash"]}
            pref = self.conn.execute("SELECT pinned FROM prefs WHERE path_key=?", (row["path_key"],)).fetchone()
            try:
                if pref and pref["pinned"]:
                    raise OpRefused("file is pinned as important")
                if row["status"] == "cloud":
                    raise OpRefused("online-only cloud file")
                keeper_id = (verify_keepers or {}).get(fid)
                if keeper_id is not None:
                    self._verify_keeper(keeper_id, row)
                st = self._validate_source(src, exp, verify_hash=keeper_id is not None)
                qroot = self.quarantine_root_for(src)
                op_id = self._journal(batch, "quarantine", src, "", exp, reason)
                dst = os.path.join(qroot, f"{batch:06d}", f"{op_id:06d}", os.path.basename(src))
                self.conn.execute("UPDATE operations SET dst=? WHERE id=?", (dst, op_id))
                self._audit(op=op_id, batch=batch, kind="quarantine", state="pending", src=src, dst=dst)
                try:
                    method = self._move_verified(op_id, src, dst, st, exp)
                except OpRefused as e:
                    self._set_state(op_id, "failed", str(e))
                    raise
                with db.tx(self.conn):
                    self._set_state(op_id, "done", method=method)
                    library.delete_file_rows(self.conn, [fid])
                    library.record_feedback(self.conn, "quarantined", src, basket, reason)
                self._manifest(qroot, op=op_id, batch=batch, state="quarantined", original=src, quarantined=dst,
                               size=row["size"], hash=row["hash"], reason=reason)
                self._audit(op=op_id, batch=batch, kind="quarantine", state="done", src=src, dst=dst)
                res.results.append(OpResult(op_id, src, dst, True, "quarantined"))
            except OpRefused as e:
                res.results.append(OpResult(None, src, "", False, str(e)))
            except Exception as e:  # unexpected: journal row stays pending and is reconciled below
                log.exception("file operation failed unexpectedly")
                self._unexpected = True
                res.results.append(OpResult(None, "", "", False, f"unexpected error ({type(e).__name__}: {e}); "
                                            "the operation journal was reconciled"))
        self._finish_batch(batch, res)
        return res

    def _verify_keeper(self, keeper_id: int, dup_row: sqlite3.Row) -> None:
        k = self.conn.execute("SELECT * FROM files WHERE id=?", (keeper_id,)).fetchone()
        if k is None or not k["hash"] or k["hash"] != dup_row["hash"]:
            raise OpRefused("the copy being kept is no longer indexed with identical content")
        try:
            st = os.lstat(long_path(k["path"]))
        except OSError:
            raise OpRefused("the copy being kept no longer exists")
        if st.st_size != k["size"] or st.st_mtime_ns != k["mtime_ns"]:
            raise OpRefused("the copy being kept was modified; re-index before removing duplicates")
        if k["ino"] and dup_row["ino"] and k["ino"] == dup_row["ino"] and k["dev"] == dup_row["dev"]:
            raise OpRefused("both paths are hard links to the same data; nothing would be reclaimed")
        if hash_file(long_path(k["path"])) != k["hash"]:
            raise OpRefused("the copy being kept no longer has identical content")

    def quarantined(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT o.*, b.description FROM operations o JOIN batches b ON b.id=o.batch_id"
            " WHERE o.kind='quarantine' AND o.state='done' ORDER BY o.id DESC").fetchall()

    @_exclusive
    def restore(self, op_ids: list[int], on_conflict: str = "rename") -> BatchResult:
        """Return quarantined files to their original paths. ``on_conflict``: 'rename' restores
        next to a newer file as 'name (restored).ext'; 'skip' leaves it in quarantine."""
        batch = self._new_batch("restore", f"restore {len(op_ids)} file(s)")
        res = BatchResult(batch)
        for oid in op_ids:
            op = self.conn.execute("SELECT * FROM operations WHERE id=?", (oid,)).fetchone()
            if op is None or op["kind"] != "quarantine" or op["state"] != "done":
                res.results.append(OpResult(oid, "", "", False, "not an active quarantine record"))
                continue
            qpath, original = op["dst"], op["src"]
            try:
                self._check_target(original)
                exp = {"size": op["size"], "hash": op["hash"]}
                st = self._validate_source(qpath, exp, in_scope=False, verify_hash=bool(op["hash"]),
                                           allow_project=True, container=_quarantine_root_of(qpath))
                target = original
                if _exists(target):
                    if on_conflict != "rename":
                        raise OpRefused("original location is occupied; left in quarantine")
                    target = _unique_name(original, "restored")
                rop = self._journal(batch, "restore", qpath, target, {**exp, "ino": str(st.st_ino),
                                                                      "mtime_ns": st.st_mtime_ns},
                                    f"restore of op {oid}", reverses=oid)
                self._audit(op=rop, batch=batch, kind="restore", state="pending", src=qpath, dst=target)
                try:
                    method = self._move_verified(rop, qpath, target, st, exp)
                except OpRefused as e:
                    self._set_state(rop, "failed", str(e))
                    raise
                with db.tx(self.conn):
                    self._set_state(rop, "done", method=method)
                    self._set_state(oid, "restored")
                    came_from = self.conn.execute(
                        "SELECT basket FROM feedback WHERE path_key=? AND action='quarantined' ORDER BY id DESC LIMIT 1",
                        (norm(original),)).fetchone()
                    library.record_feedback(self.conn, "restored", original, came_from[0] if came_from else None,
                                            "user restored from quarantine")
                self._manifest(os.path.dirname(os.path.dirname(os.path.dirname(qpath))), op=oid, state="restored",
                               original=original, restored_to=target)
                self._audit(op=rop, batch=batch, kind="restore", state="done", src=qpath, dst=target)
                self._cleanup_quarantine_dirs(qpath)
                msg = "restored" if target == original else f"restored as {os.path.basename(target)}"
                res.results.append(OpResult(rop, qpath, target, True, msg))
            except OpRefused as e:
                res.results.append(OpResult(oid, qpath, original, False, str(e)))
            except Exception as e:  # unexpected: journal row stays pending and is reconciled below
                log.exception("file operation failed unexpectedly")
                self._unexpected = True
                res.results.append(OpResult(None, "", "", False, f"unexpected error ({type(e).__name__}: {e}); "
                                            "the operation journal was reconciled"))
        self._finish_batch(batch, res)
        return res

    def _check_target(self, path: str) -> None:
        """Restore/undo targets: allowed inside the selected folders (same physical rules as any
        move), or outside them (folder since removed from the index) unless the location - as
        written or as it resolves on disk through junctions and aliases - is protected."""
        if self.policy.check(path) is None:
            reason = self.policy.check_physical(path)
            if reason:
                raise OpRefused(f"target not allowed: {reason}")
            return
        qdir = norm(config.default_quarantine_dir())
        for key in (norm(path), norm(real_path(path))):
            for p in protected_directories():
                if is_within(key, p) and not is_within(key, qdir):
                    raise OpRefused(f"target is in a protected location ({p})")
            if drive_system_dir(key):
                raise OpRefused(f"target is in a protected Windows folder ({drive_system_dir(key)})")

    def _cleanup_quarantine_dirs(self, qpath: str) -> None:
        for d in (os.path.dirname(qpath), os.path.dirname(os.path.dirname(qpath))):
            try:
                os.rmdir(long_path(d))  # only succeeds if empty
            except OSError:
                break

    def trash_quarantined(self, op_ids: list[int]) -> BatchResult:
        """Send quarantined files to the Windows Recycle Bin (still user-recoverable)."""
        from send2trash import send2trash

        return self._dispose(op_ids, "trash", lambda p: send2trash(long_path(p)))

    def purge_quarantined(self, op_ids: list[int], confirmation: str) -> BatchResult:
        """Permanently delete quarantined files. Requires the exact confirmation phrase."""
        if confirmation != CONFIRM_PURGE:
            raise OpRefused(f"permanent deletion requires typing '{CONFIRM_PURGE}'")
        return self._dispose(op_ids, "purge", lambda p: os.remove(long_path(p)))

    @_exclusive
    def _dispose(self, op_ids: list[int], kind: str, action) -> BatchResult:
        batch = self._new_batch(kind, f"{kind} {len(op_ids)} quarantined file(s)")
        res = BatchResult(batch)
        for oid in op_ids:
            op = self.conn.execute("SELECT * FROM operations WHERE id=?", (oid,)).fetchone()
            if op is None or op["kind"] != "quarantine" or op["state"] != "done":
                res.results.append(OpResult(oid, "", "", False, "not an active quarantine record"))
                continue
            path = op["dst"]
            qroot = _quarantine_root_of(path)
            if not self._is_quarantine_copy(op, qroot):
                res.results.append(OpResult(oid, path, "", False, "refused: file is not inside a quarantine folder"))
                continue
            try:
                self._validate_source(path, {"size": op["size"]}, in_scope=False, allow_project=True,
                                      container=qroot)
                action(path)
            except (OpRefused, OSError) as e:
                res.results.append(OpResult(oid, path, "", False, str(e)))
                continue
            state = "trashed" if kind == "trash" else "purged"
            self._set_state(oid, state)
            self._manifest(qroot, op=oid, state=state, original=op["src"])
            self._audit(op=oid, batch=batch, kind=kind, state="done", src=path)
            self._cleanup_quarantine_dirs(path)
            res.results.append(OpResult(oid, path, "", True, state))
        self._finish_batch(batch, res)
        return res

    def _is_quarantine_copy(self, op: sqlite3.Row, qroot: str) -> bool:
        """Only our own quarantine layout may be disposed of: a known quarantine folder, then the
        batch and operation folders this very record created."""
        path = op["dst"] or ""
        if os.path.basename(os.path.dirname(path)) != f"{op['id']:06d}" or \
                os.path.basename(os.path.dirname(os.path.dirname(path))) != f"{op['batch_id']:06d}":
            return False
        key = norm(qroot)
        if key == norm(config.default_quarantine_dir()) or os.path.basename(key) == ".lfi-quarantine":
            return True
        return any(r["quarantine_dir"] and norm(r["quarantine_dir"]) == key
                   for r in self.conn.execute("SELECT quarantine_dir FROM roots"))

    # ------------------------------------------------------------------ organize moves
    @_exclusive
    def move_files(self, moves: list[tuple[int, str]], description: str) -> BatchResult:
        """Apply an approved organization plan: ``[(file_id, destination_path), ...]``."""
        batch = self._new_batch("organize", description)
        res = BatchResult(batch)
        for fid, dst in moves:
            row = self.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
            if row is None:
                res.results.append(OpResult(None, f"#{fid}", dst, False, "not in index"))
                continue
            src = row["path"]
            exp = {"size": row["size"], "mtime_ns": row["mtime_ns"], "ino": row["ino"], "hash": row["hash"]}
            try:
                reason = self.policy.check_physical(dst)
                if reason:
                    raise OpRefused(f"destination not allowed: {reason}")
                if norm(src) == norm(dst):
                    raise OpRefused("already at destination")
                st = self._validate_source(src, exp)
                droot = self.policy.root_for(dst)
                if droot and find_project_root(dst, droot):
                    raise OpRefused("destination is inside a software project")
                op_id = self._journal(batch, "move", src, dst, {**exp, "ino": str(st.st_ino)}, description)
                self._audit(op=op_id, batch=batch, kind="move", state="pending", src=src, dst=dst)
                try:
                    method = self._move_verified(op_id, src, dst, st, exp)
                except OpRefused as e:
                    self._set_state(op_id, "failed", str(e))
                    raise
                root_row = self.conn.execute("SELECT id FROM roots WHERE path_key=?",
                                             (norm(droot),)).fetchone() if droot else None
                with db.tx(self.conn):
                    self._set_state(op_id, "done", method=method)
                    library.update_file_path(self.conn, fid, dst, root_row["id"] if root_row else None)
                self._audit(op=op_id, batch=batch, kind="move", state="done", src=src, dst=dst)
                res.results.append(OpResult(op_id, src, dst, True, "moved"))
            except OpRefused as e:
                res.results.append(OpResult(None, src, dst, False, str(e)))
            except Exception as e:  # unexpected: journal row stays pending and is reconciled below
                log.exception("file operation failed unexpectedly")
                self._unexpected = True
                res.results.append(OpResult(None, "", "", False, f"unexpected error ({type(e).__name__}: {e}); "
                                            "the operation journal was reconciled"))
        self._finish_batch(batch, res)
        return res

    @_exclusive
    def undo_batch(self, batch_id: int) -> BatchResult:
        """Reverse a completed organize batch (moves files back, removes folders it created if
        they are empty). Quarantine batches are reversed with ``restore``."""
        b = self.conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if b is None:
            raise OpRefused(f"there is no batch {batch_id} (see the history)")
        if b["kind"] != "organize":
            raise OpRefused("only organize batches can be undone this way; quarantined files are restored instead")
        ops = self.conn.execute("SELECT * FROM operations WHERE batch_id=? AND kind='move' AND state='done'"
                                " ORDER BY id DESC", (batch_id,)).fetchall()
        if not ops:
            raise OpRefused(f"batch {batch_id} has nothing left to undo (already undone)")
        undo = self._new_batch("undo", f"undo of batch {batch_id}")
        res = BatchResult(undo)
        for op in ops:
            cur, back = op["dst"], op["src"]
            try:
                self._check_target(back)
                st = self._validate_source(cur, {"size": op["size"], "mtime_ns": op["mtime_ns"]}, in_scope=False,
                                           allow_project=True)
                if op["ino"] and str(st.st_ino) != str(op["ino"]):
                    raise OpRefused("a different file is now at the moved location")
                if _exists(back):
                    raise OpRefused("original location is occupied; not overwritten")
                rid = self._journal(undo, "move", cur, back, {"size": op["size"], "mtime_ns": op["mtime_ns"],
                                                              "ino": op["ino"], "hash": op["hash"]},
                                    f"undo op {op['id']}", reverses=op["id"])
                try:
                    method = self._move_verified(rid, cur, back, st, {"hash": op["hash"]})
                except OpRefused as e:
                    self._set_state(rid, "failed", str(e))
                    raise
                fid_row = self.conn.execute("SELECT id FROM files WHERE path_key=?", (norm(cur),)).fetchone()
                with db.tx(self.conn):
                    self._set_state(rid, "done", method=method)
                    self._set_state(op["id"], "undone")
                    if fid_row:
                        root = self.policy.root_for(back)
                        rr = self.conn.execute("SELECT id FROM roots WHERE path_key=?", (norm(root),)).fetchone() \
                            if root else None
                        library.update_file_path(self.conn, fid_row["id"], back, rr["id"] if rr else None)
                self._remove_created(json.loads(op["created_dirs"] or "[]"))
                self._audit(op=rid, batch=undo, kind="undo", state="done", src=cur, dst=back)
                res.results.append(OpResult(rid, cur, back, True, "moved back"))
            except OpRefused as e:
                res.results.append(OpResult(op["id"], cur, back, False, str(e)))
            except Exception as e:  # unexpected: journal row stays pending and is reconciled below
                log.exception("file operation failed unexpectedly")
                self._unexpected = True
                res.results.append(OpResult(None, "", "", False, f"unexpected error ({type(e).__name__}: {e}); "
                                            "the operation journal was reconciled"))
        self._finish_batch(undo, res)
        if res.ok:  # the history should say the organize batch was taken back
            left = self.conn.execute("SELECT count(*) FROM operations WHERE batch_id=? AND kind='move'"
                                     " AND state='done'", (batch_id,)).fetchone()[0]
            self.conn.execute("UPDATE batches SET status=? WHERE id=?",
                              ("undone" if left == 0 else "partially undone", batch_id))
        return res

    # ------------------------------------------------------------------ crash recovery
    def recover(self) -> list[str]:
        """Reconcile operations interrupted by a crash or power loss. Never deletes user data:
        the only files it may remove are ``*.lfi-partial`` copies and verified duplicate copies
        this module itself created. Skipped (returns []) while another process holds the writer
        lock: its unfinished rows are live work, not crash debris."""
        if self._lock_depth:
            return self._recover()
        if not self._lock.acquire(0.0):
            log.info("recovery skipped: another process is performing file operations")
            return []
        self._lock_depth += 1
        try:
            return self._recover()
        finally:
            self._lock_depth -= 1
            self._lock.release()

    def _recover(self) -> list[str]:
        report: list[str] = []
        rows = self.conn.execute("SELECT * FROM operations WHERE state IN ('planned','pending','copied')").fetchall()
        for op in rows:
            src, dst = op["src"], op["dst"]
            s_ok, d_ok = _exists(src), bool(dst) and _exists(dst)
            partial = dst + PARTIAL if dst else ""
            if partial and _exists(partial):
                os.remove(long_path(partial))
                report.append(f"op {op['id']}: removed incomplete copy {partial}")
            if op["state"] == "planned" or (s_ok and not d_ok):
                self._set_state(op["id"], "failed", "interrupted before the move; file untouched")
                self._remove_created(json.loads(op["created_dirs"] or "[]"))
                report.append(f"op {op['id']}: not performed; {src} untouched")
            elif not s_ok and d_ok:
                try:
                    size_ok = os.lstat(long_path(dst)).st_size == op["size"]
                except OSError:
                    size_ok = False
                if size_ok:
                    self._complete_after_crash(op)
                    report.append(f"op {op['id']}: completed before interruption; recorded as done ({dst})")
                else:
                    self._set_state(op["id"], "conflict", "destination exists but does not match; review manually")
                    report.append(f"op {op['id']}: NEEDS REVIEW - unexpected file at {dst}")
            elif s_ok and d_ok:
                # A cross-drive copy interrupted after its copy was finalized (state 'copied', or
                # still 'pending' if the crash hit between the rename and the journal update). The
                # journal proves the destination did not exist before, so a byte-identical copy
                # there is ours and removing it loses nothing.
                copying = op["state"] == "copied" or op["method"] == "copy"
                h_dst = hash_file(long_path(dst)) if copying else None
                if copying and h_dst is not None and h_dst == hash_file(long_path(src)) \
                        and (not op["hash"] or h_dst == op["hash"]):
                    os.remove(long_path(dst))
                    self._set_state(op["id"], "failed", "interrupted cross-drive move rolled back; source kept")
                    self._remove_created(json.loads(op["created_dirs"] or "[]"))
                    report.append(f"op {op['id']}: rolled back verified copy; original kept at {src}")
                else:
                    self._set_state(op["id"], "conflict", "both source and destination exist; nothing removed")
                    report.append(f"op {op['id']}: NEEDS REVIEW - both {src} and {dst} exist (nothing removed)")
            else:
                self._set_state(op["id"], "lost", "neither source nor destination exists")
                report.append(f"op {op['id']}: NEEDS REVIEW - file not found at {src} or {dst}")
        for b in self.conn.execute("SELECT id FROM batches WHERE status='running'").fetchall():
            self.conn.execute("UPDATE batches SET status='interrupted', finished_at=? WHERE id=?", (time.time(), b["id"]))
        if report:
            self._audit(kind="recovery", report=report)
        return report

    def _complete_after_crash(self, op: sqlite3.Row) -> None:
        with db.tx(self.conn):
            self._set_state(op["id"], "done", "completed (recovered after interruption)")
            row = self.conn.execute("SELECT id FROM files WHERE path_key=?", (norm(op["src"]),)).fetchone()
            if row:
                if op["kind"] == "quarantine":
                    library.delete_file_rows(self.conn, [row["id"]])
                else:
                    library.update_file_path(self.conn, row["id"], op["dst"], None)
            if op["kind"] == "restore" and op["reverses"]:
                self._set_state(op["reverses"], "restored")
            if op["kind"] == "move" and op["reverses"]:  # an undo that had finished moving the file
                self._set_state(op["reverses"], "undone")
        if op["kind"] == "move" and op["reverses"]:
            orig = self.conn.execute("SELECT created_dirs FROM operations WHERE id=?", (op["reverses"],)).fetchone()
            if orig:
                self._remove_created(json.loads(orig["created_dirs"] or "[]"))
        if op["kind"] == "quarantine":
            qroot = os.path.dirname(os.path.dirname(os.path.dirname(op["dst"])))
            self._manifest(qroot, op=op["id"], state="quarantined", original=op["src"], quarantined=op["dst"],
                           size=op["size"], hash=op["hash"], note="recovered")

    def history(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT b.*, (SELECT count(*) FROM operations o WHERE o.batch_id=b.id) AS n_ops,"
            " (SELECT count(*) FROM operations o WHERE o.batch_id=b.id AND o.state IN ('done','restored','undone',"
            "'trashed','purged')) AS n_done FROM batches b ORDER BY b.id DESC LIMIT ?", (limit,)).fetchall()
