"""Index-level helpers shared by the indexer, file operations and UI: roots, exclusions,
user preferences, and consistent row deletion across metadata, FTS and vector tables."""

from __future__ import annotations

import os
import sqlite3
import time

from . import db
from .config import Settings
from .safety import ScopePolicy, is_within, norm, real_path
from .textutil import folder_tokens, name_tokens
from .vectorstore import VectorStore


def add_root(conn: sqlite3.Connection, path: str, validate: bool = True) -> int:
    path = os.path.abspath(path)
    if validate:
        err = ScopePolicy.validate_root(path)
        if err:
            raise ValueError(err)
        path = real_path(path)  # store the one canonical spelling (long names, no device prefix)
    key = norm(path)
    inner = []
    for r in conn.execute("SELECT id, path_key FROM roots").fetchall():
        # is_within also handles drive roots, whose keys already end in a separator (d:\)
        if key != r["path_key"] and is_within(key, r["path_key"]):
            raise ValueError("Folder overlaps an already selected folder; remove that one first.")
        if key != r["path_key"] and is_within(r["path_key"], key):
            inner.append(r["id"])
    conn.execute(
        "INSERT OR IGNORE INTO roots(path, path_key, enabled, added_at) VALUES(?,?,1,?)",
        (path, key, time.time()),
    )
    root_id = conn.execute("SELECT id FROM roots WHERE path_key=?", (key,)).fetchone()["id"]
    # A wider folder (a whole drive) takes over the folders inside it, keeping their index rows,
    # so adding C:\ after Desktop doesn't read the Desktop again.
    for old in inner:
        conn.execute("UPDATE files SET root_id=? WHERE root_id=?", (root_id, old))
        conn.execute("DELETE FROM roots WHERE id=?", (old,))
    return root_id


def remove_root(conn: sqlite3.Connection, path: str) -> bool:
    """Forget a folder: its index rows are removed. Files on disk are not touched. Returns False
    if ``path`` is not a selected folder."""
    row = conn.execute("SELECT id FROM roots WHERE path_key=?", (norm(path),)).fetchone()
    if not row:
        return False
    ids = [r[0] for r in conn.execute("SELECT id FROM files WHERE root_id=?", (row["id"],))]
    delete_file_rows(conn, ids)
    conn.execute("DELETE FROM roots WHERE id=?", (row["id"],))
    return True


def location_parts(path: str, root: str | None) -> list[str]:
    """Lower-cased folder names from the selected root's own name down to the file's parent.
    Location signals ("in Downloads") must ignore where the root itself lives on disk, or a
    root under C:\\Users\\me\\Desktop would make every file look like desktop clutter."""
    folders = os.path.dirname(os.path.normpath(path))
    if root:
        rel = os.path.relpath(folders, os.path.normpath(root))
        parts = [os.path.basename(os.path.normpath(root))] + ([] if rel == "." else rel.split(os.sep))
    else:
        parts = folders.split(os.sep)[-3:]
    return [p.lower() for p in parts if p]


def root_paths_by_id(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["id"]: r["path"] for r in conn.execute("SELECT id, path FROM roots")}


def roots(conn: sqlite3.Connection, enabled_only: bool = True) -> list[sqlite3.Row]:
    q = "SELECT * FROM roots" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY path"
    return conn.execute(q).fetchall()


def add_exclusion(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("INSERT OR IGNORE INTO exclusions(path, path_key) VALUES(?,?)",
                 (os.path.abspath(path), norm(path)))
    key = norm(path)
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM files WHERE path_key = ? OR path_key LIKE ? ESCAPE '\\'",
        (key, _like_prefix(key)),
    )]
    delete_file_rows(conn, ids)


def remove_exclusion(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("DELETE FROM exclusions WHERE path_key=?", (norm(path),))


def exclusions(conn: sqlite3.Connection) -> list[str]:
    return [r["path"] for r in conn.execute("SELECT path FROM exclusions ORDER BY path")]


def _like_prefix(key: str) -> str:
    esc = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc + ("\\\\" if os.sep == "\\" else os.sep) + "%"


def scope_policy(conn: sqlite3.Connection, settings: Settings, extra_roots: list[str] | None = None) -> ScopePolicy:
    rs = [r["path"] for r in roots(conn)] + list(extra_roots or [])
    return ScopePolicy(roots=rs, exclude_globs=settings.exclude_globs, exclude_paths=exclusions(conn))


def vector_spaces(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM embedding_spaces").fetchall()


def active_space(conn: sqlite3.Connection) -> sqlite3.Row | None:
    slug = db.meta_get(conn, "active_space")
    if not slug:
        return None
    return conn.execute("SELECT * FROM embedding_spaces WHERE slug=?", (slug,)).fetchone()


def stores(conn: sqlite3.Connection, space: sqlite3.Row | None = None) -> tuple[VectorStore, VectorStore] | None:
    space = space or active_space(conn)
    if space is None:
        return None
    return (VectorStore(conn, space["slug"], space["dim"], "chunks"),
            VectorStore(conn, space["slug"], space["dim"], "files"))


def active_image_space(conn: sqlite3.Connection) -> sqlite3.Row | None:
    slug = db.meta_get(conn, "active_image_space")
    if not slug:
        return None
    return conn.execute("SELECT * FROM image_spaces WHERE slug=?", (slug,)).fetchone()


def image_store(conn: sqlite3.Connection, space: sqlite3.Row | None = None) -> VectorStore | None:
    space = space or active_image_space(conn)
    return VectorStore(conn, space["slug"], space["dim"], "images") if space is not None else None


def delete_vectors_for_files(conn: sqlite3.Connection, file_ids: list[int]) -> None:
    if not file_ids:
        return
    for space in conn.execute("SELECT * FROM image_spaces").fetchall():
        VectorStore(conn, space["slug"], space["dim"], "images").delete(file_ids)
    for space in vector_spaces(conn):
        cs = VectorStore(conn, space["slug"], space["dim"], "chunks")
        fs = VectorStore(conn, space["slug"], space["dim"], "files")
        for i in range(0, len(file_ids), 500):
            part = file_ids[i:i + 500]
            chunk_ids = [r[0] for r in conn.execute(
                f"SELECT id FROM chunks WHERE file_id IN ({','.join('?' * len(part))})", part)]
            cs.delete(chunk_ids)
            fs.delete(part)


def delete_chunks_for_files(conn: sqlite3.Connection, file_ids: list[int]) -> None:
    delete_vectors_for_files(conn, file_ids)
    for i in range(0, len(file_ids), 500):
        part = file_ids[i:i + 500]
        conn.execute(f"DELETE FROM chunks WHERE file_id IN ({','.join('?' * len(part))})", part)


def delete_chunks_where(conn: sqlite3.Connection, where: str, args: tuple = ()) -> None:
    """Delete selected chunk rows together with their vectors in every text space, so no
    orphaned vector can take a nearest-neighbour slot from a real one."""
    ids = [r[0] for r in conn.execute(f"SELECT id FROM chunks WHERE {where}", args)]
    if not ids:
        return
    for space in vector_spaces(conn):
        VectorStore(conn, space["slug"], space["dim"], "chunks").delete(ids)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        conn.execute(f"DELETE FROM chunks WHERE id IN ({','.join('?' * len(part))})", part)


def delete_file_rows(conn: sqlite3.Connection, file_ids: list[int]) -> None:
    if not file_ids:
        return
    delete_vectors_for_files(conn, file_ids)
    for i in range(0, len(file_ids), 500):
        part = file_ids[i:i + 500]
        conn.execute(f"DELETE FROM files WHERE id IN ({','.join('?' * len(part))})", part)


def update_file_path(conn: sqlite3.Connection, file_id: int, new_path: str, root_id: int | None) -> None:
    """Keep the index consistent after an approved move (no re-extraction needed)."""
    conn.execute(
        "UPDATE files SET path=?, path_key=?, parent_key=?, name=?, root_id=COALESCE(?, root_id) WHERE id=?",
        (new_path, norm(new_path), norm(os.path.dirname(new_path)), os.path.basename(new_path), root_id, file_id),
    )
    conn.execute("DELETE FROM names_fts WHERE rowid=?", (file_id,))
    conn.execute("INSERT INTO names_fts(rowid, name, folders) VALUES(?,?,?)",
                 (file_id, name_tokens(os.path.basename(new_path)), folder_tokens(new_path)))


# ---- preferences & feedback -------------------------------------------------------------

def set_pref(conn: sqlite3.Connection, path: str, **fields) -> None:
    key = norm(path)
    cur = conn.execute("SELECT * FROM prefs WHERE path_key=?", (key,)).fetchone()
    vals = {"pinned": 0, "never_suggest": 0, "category": None}
    if cur:
        vals.update({k: cur[k] for k in vals})
    vals.update(fields)
    conn.execute(
        "INSERT OR REPLACE INTO prefs(path_key, pinned, never_suggest, category, updated_at) VALUES(?,?,?,?,?)",
        (key, int(vals["pinned"]), int(vals["never_suggest"]), vals["category"], time.time()),
    )


def prefs_map(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {r["path_key"]: r for r in conn.execute("SELECT * FROM prefs")}


def record_feedback(conn: sqlite3.Connection, action: str, path: str | None = None, basket: str | None = None,
                    detail: str | None = None) -> None:
    ext = os.path.splitext(path)[1].lower() if path else None
    conn.execute(
        "INSERT INTO feedback(path_key, ext, basket, action, detail, created_at) VALUES(?,?,?,?,?,?)",
        (norm(path) if path else None, ext, basket, action, detail, time.time()),
    )
