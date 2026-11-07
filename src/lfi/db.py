"""SQLite persistence: metadata, FTS5 lexical index, sqlite-vec vectors, journal, feedback.

One database file holds everything so the index can be inspected, backed up, cleared, or
deleted as a unit. WAL mode lets the UI read while the indexer writes. Each thread opens its
own connection via ``connect()``.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA_VERSION = 1

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS roots (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    path_key TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    quarantine_dir TEXT,
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS exclusions (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    path_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    root_id INTEGER REFERENCES roots(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    path_key TEXT NOT NULL UNIQUE,
    parent_key TEXT NOT NULL,
    name TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    ctime_ns INTEGER,
    attrs INTEGER,
    ino TEXT,
    dev TEXT,
    nlink INTEGER,
    kind TEXT,
    mime TEXT,
    hash TEXT,
    prehash TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    error TEXT,
    stage INTEGER NOT NULL DEFAULT 0,
    text_chars INTEGER DEFAULT 0,
    truncated INTEGER DEFAULT 0,
    title TEXT,
    project_root TEXT,
    width INTEGER, height INTEGER, phash TEXT, dhash TEXT, exif_time TEXT, camera TEXT,
    is_screenshot INTEGER DEFAULT 0,
    seen_gen INTEGER NOT NULL DEFAULT 0,
    first_seen REAL, indexed_at REAL
);
CREATE INDEX IF NOT EXISTS ix_files_hash ON files(hash) WHERE hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_files_size ON files(size);
CREATE INDEX IF NOT EXISTS ix_files_parent ON files(parent_key);
CREATE INDEX IF NOT EXISTS ix_files_stage ON files(stage);
CREATE INDEX IF NOT EXISTS ix_files_root ON files(root_id);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunks_file ON chunks(file_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='id', tokenize="{FTS_TOKENIZE}"
);
-- The three standard external-content triggers keep chunks_fts identical to chunks, including
-- when text is blanked for privacy (an UPDATE). Never edit chunks_fts by hand: a 'delete' that
-- doesn't match the index exactly corrupts an external-content FTS5 table.
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE OF text ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS names_fts USING fts5(
    name, folders, tokenize="{FTS_TOKENIZE}"
);

CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
    DELETE FROM names_fts WHERE rowid = old.id;
END;

CREATE TABLE IF NOT EXISTS embedding_spaces (
    slug TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS image_spaces (
    slug TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS file_terms (
    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    terms TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prefs (
    path_key TEXT PRIMARY KEY,
    pinned INTEGER NOT NULL DEFAULT 0,
    never_suggest INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    path_key TEXT,
    ext TEXT,
    basket TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_feedback_basket ON feedback(basket, ext);

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    finished_at REAL
);

CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    kind TEXT NOT NULL,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    size INTEGER,
    mtime_ns INTEGER,
    ino TEXT,
    hash TEXT,
    state TEXT NOT NULL,
    method TEXT,
    error TEXT,
    created_dirs TEXT,
    reverses INTEGER,
    reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ops_state ON operations(state);
CREATE INDEX IF NOT EXISTS ix_ops_batch ON operations(batch_id);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    stats TEXT
);
"""
# Letters, numbers, private-use and combining marks are word characters, so Devanagari (and
# other scripts with vowel signs) index whole words; mirrored by textutil.words().
FTS_TOKENIZE = "unicode61 remove_diacritics 2 categories 'L* N* Co M*'"
FTS_VERSION = "2"  # v2: tokenizer above + update trigger (v1's manual deletes could corrupt chunks_fts)
SCHEMA = SCHEMA.replace("{FTS_TOKENIZE}", FTS_TOKENIZE)

_load_lock = threading.Lock()
_vec_available: bool | None = None


def _load_vec(conn: sqlite3.Connection) -> bool:
    global _vec_available
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _vec_available = True
    except Exception:
        _vec_available = False
    return bool(_vec_available)


def vec_available() -> bool:
    return bool(_vec_available)


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = str(path or config.db_path())
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA cache_size=-32768")  # 32 MB page cache (default 2 MB): fewer reads on big indexes
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA journal_size_limit=67108864")  # trim the log file back to 64 MB after checkpoints
    _load_vec(conn)
    with _load_lock:
        _ensure_schema(conn)
    return conn


MIGRATIONS = [  # (table, column, DDL) added after the first release; applied idempotently
    ("files", "img_stage", "ALTER TABLE files ADD COLUMN img_stage INTEGER NOT NULL DEFAULT 0"),
    ("files", "img_tags", "ALTER TABLE files ADD COLUMN img_tags TEXT"),
]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for table, col, ddl in MIGRATIONS:
        if col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_files_img ON files(img_stage) WHERE kind='image'")
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    if meta_get(conn, "fts_version") != FTS_VERSION:
        _rebuild_fts(conn)


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    """Recreate both full-text indexes from the tables they mirror. Also repairs a chunks_fts
    that v1's triggers corrupted. Idempotent: if interrupted, it simply runs again next time."""
    from .textutil import folder_tokens, name_tokens

    for trig in ("chunks_ai", "chunks_ad", "chunks_au", "files_ad"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trig}")
    conn.execute("DROP TABLE IF EXISTS chunks_fts")
    conn.execute("DROP TABLE IF EXISTS names_fts")
    conn.executescript(SCHEMA)
    with tx(conn):
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        conn.executemany("INSERT INTO names_fts(rowid, name, folders) VALUES(?,?,?)",
                         ((r[0], name_tokens(r[1]), folder_tokens(r[2]))
                          for r in conn.execute("SELECT id, name, path FROM files")))
        meta_set(conn, "fts_version", FTS_VERSION)


@contextmanager
def tx(conn: sqlite3.Connection, immediate: bool = True):
    """Explicit transaction (connections run in autocommit mode otherwise)."""
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def model_slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")[:48]


def ensure_vector_tables(conn: sqlite3.Connection, model: str, dim: int) -> str:
    """Create vec0 tables for one embedding space. Returns the slug. Switching models never
    mixes vectors of different dimensionality or semantics."""
    slug = model_slug(model)
    row = conn.execute("SELECT dim FROM embedding_spaces WHERE slug=?", (slug,)).fetchone()
    if row and row["dim"] != dim:
        drop_vector_tables(conn, slug)
        row = None
    if vec_available():
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks_{slug} USING vec0("
            f"embedding float[{dim}] distance_metric=cosine)"
        )
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_files_{slug} USING vec0("
            f"embedding float[{dim}] distance_metric=cosine)"
        )
    else:  # NumPy fallback store: same layout, raw float32 blobs
        conn.execute(f"CREATE TABLE IF NOT EXISTS vec_chunks_{slug} (rowid INTEGER PRIMARY KEY, embedding BLOB)")
        conn.execute(f"CREATE TABLE IF NOT EXISTS vec_files_{slug} (rowid INTEGER PRIMARY KEY, embedding BLOB)")
    if row is None:
        conn.execute(
            "INSERT OR REPLACE INTO embedding_spaces(slug, model, dim, created_at) VALUES(?,?,?,?)",
            (slug, model, dim, time.time()),
        )
    return slug


def ensure_image_table(conn: sqlite3.Connection, model: str, dim: int) -> str:
    """Vector table for one image-text model (vision space). Returns the slug."""
    slug = model_slug(model)
    row = conn.execute("SELECT dim FROM image_spaces WHERE slug=?", (slug,)).fetchone()
    if row and row["dim"] != dim:
        drop_image_table(conn, slug)
        row = None
    if vec_available():
        conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_images_{slug} USING vec0("
                     f"embedding float[{dim}] distance_metric=cosine)")
    else:
        conn.execute(f"CREATE TABLE IF NOT EXISTS vec_images_{slug} (rowid INTEGER PRIMARY KEY, embedding BLOB)")
    if row is None:
        conn.execute("INSERT OR REPLACE INTO image_spaces(slug, model, dim, created_at) VALUES(?,?,?,?)",
                     (slug, model, dim, time.time()))
    return slug


def drop_image_table(conn: sqlite3.Connection, slug: str) -> None:
    conn.execute(f"DROP TABLE IF EXISTS vec_images_{slug}")
    conn.execute("DELETE FROM image_spaces WHERE slug=?", (slug,))


def drop_vector_tables(conn: sqlite3.Connection, slug: str) -> None:
    conn.execute(f"DROP TABLE IF EXISTS vec_chunks_{slug}")
    conn.execute(f"DROP TABLE IF EXISTS vec_files_{slug}")
    conn.execute("DELETE FROM embedding_spaces WHERE slug=?", (slug,))


def clear_index(conn: sqlite3.Connection) -> None:
    """Remove indexed content (metadata, text, vectors). Journal, quarantine records,
    preferences and feedback are kept so quarantined files stay restorable."""
    for row in conn.execute("SELECT slug FROM embedding_spaces").fetchall():
        drop_vector_tables(conn, row["slug"])
    for row in conn.execute("SELECT slug FROM image_spaces").fetchall():
        drop_image_table(conn, row["slug"])
    with tx(conn):
        conn.execute("DELETE FROM file_terms")
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM names_fts")
        conn.execute("DELETE FROM files")
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.execute("VACUUM")


def clear_text_cache(conn: sqlite3.Connection) -> None:
    """Drop stored extracted text but keep metadata, hashes and vectors."""
    with tx(conn):
        # Rows stay so chunk vectors keep their file mapping; only the text is blanked.
        conn.execute("UPDATE chunks SET text='' WHERE text <> ''")  # chunks_au keeps chunks_fts in step
        conn.execute("DELETE FROM file_terms")
    conn.execute("VACUUM")


def meta_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))


def index_problem(path: Path | str | None = None) -> str | None:
    """None if the index opens and its main tables can be read; otherwise SQLite's error text.
    Cheap enough for every start (counts walk the tables once; no full integrity check)."""
    p = Path(path or config.db_path())
    if not p.exists():
        return None
    try:
        conn = sqlite3.connect(str(p), timeout=10)
        try:
            for table in ("meta", "roots", "files", "operations", "batches"):
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                    conn.execute(f"SELECT count(*) FROM {table}").fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        return str(e)
    return None


def set_aside_damaged(path: Path | str | None = None) -> Path:
    """Move an unreadable index (and its -wal/-shm files) into a dated backup folder next to it, so a
    fresh index can be built. Nothing is deleted; the backup can be inspected or removed later."""
    p = Path(path or config.db_path())
    dest = p.parent / f"damaged-index-{time.strftime('%Y%m%d-%H%M%S')}"
    dest.mkdir(parents=True, exist_ok=False)
    for suffix in ("", "-wal", "-shm"):
        f = p.with_name(p.name + suffix)
        if f.exists():
            f.rename(dest / f.name)
    return dest
