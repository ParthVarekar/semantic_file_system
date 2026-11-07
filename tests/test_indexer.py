import os
import sys
import threading
import time

import pytest
from conftest import file_id, index, snapshot

from lfi import db, library
from lfi.indexer import Indexer


def test_indexing_is_read_only(corpus, settings):
    root, _ = corpus
    before = snapshot(root)
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings)
    index(settings)  # second (incremental) run too
    assert snapshot(root) == before  # same files, sizes, mtimes and content hashes; nothing created


def test_index_contents_and_safety_skips(indexed):
    conn, root, m, ix, st = indexed
    names = {r["name"] for r in conn.execute("SELECT name FROM files")}
    assert "lecture07_eigenvalues.docx" in names and "deep_note_about_quantum_computing.txt" in names  # >260 chars
    assert "left-pad" not in str(conn.execute("SELECT group_concat(path) FROM files").fetchone()[0])  # node_modules
    assert "config" not in names  # .git excluded
    if sys.platform == "win32":
        assert "desktop.ini" not in names  # system file
        if m.get("junction"):
            assert st.skipped_links >= 1
            assert not conn.execute("SELECT 1 FROM files WHERE path LIKE '%JunctionToCollege%'").fetchone()
    assert st.errors >= 2 and st.encrypted == (1 if m["encrypted"] else 0)
    proj = conn.execute("SELECT project_root FROM files WHERE name='index.js'").fetchone()[0]
    assert proj and proj.endswith("webapp")
    assert conn.execute("SELECT count(*) FROM files WHERE stage<2").fetchone()[0] == 0  # all embedded


def test_incremental_change_add_delete(indexed, settings):
    conn, root, m, _, st1 = indexed
    _, st2 = index(settings)
    assert st2.new == 0 and st2.changed == 0 and st2.processed == 0 and st2.unchanged == st1.discovered
    time.sleep(0.05)
    (root / "Misc" / "data.json").write_text('{"changed": true}')
    (root / "Misc" / "new_note.md").write_text("A brand new note about photosynthesis.")
    (root / "Finance" / "salary_slip.csv").unlink()
    _, st3 = index(settings)
    assert (st3.new, st3.changed, st3.removed, st3.processed) == (1, 1, 1, 2)
    assert not conn.execute("SELECT 1 FROM files WHERE name='salary_slip.csv'").fetchone()


def test_cancel_then_resume(corpus, settings):
    root, _ = corpus
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    cancel = threading.Event()

    def progress(phase, done, total, msg):
        if phase == "process" and done > 0:
            cancel.set()

    from lfi.embeddings import HashingBackend

    st = Indexer(settings, backend=HashingBackend(), progress=progress, cancel=cancel).run()
    assert st.cancelled
    remaining = conn.execute("SELECT count(*) FROM files WHERE stage<2").fetchone()[0]
    assert remaining > 0
    job = conn.execute("SELECT status FROM jobs ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert job == "cancelled"
    _, st2 = index(settings)
    assert not st2.cancelled
    assert conn.execute("SELECT count(*) FROM files WHERE stage<2").fetchone()[0] == 0


def test_cancel_during_discovery_never_deletes_rows(indexed, settings):
    conn, *_ = indexed
    n = conn.execute("SELECT count(*) FROM files").fetchone()[0]
    cancel = threading.Event()
    cancel.set()
    st = Indexer(settings, cancel=cancel, embed=False).run()
    assert st.cancelled and st.removed == 0
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == n


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing modes")
def test_locked_file_does_not_stop_indexing(corpus, settings):
    import ctypes
    from ctypes import wintypes

    root, _ = corpus
    target = root / "Misc" / "locked.txt"
    target.write_text("locked content")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(target), 0x80000000, 0, None, 3, 0x80, None)  # GENERIC_READ, no sharing
    assert h and h != wintypes.HANDLE(-1).value
    try:
        conn = db.connect()
        library.add_root(conn, str(root), validate=False)
        _, st = index(settings)
        row = conn.execute("SELECT status, error FROM files WHERE name='locked.txt'").fetchone()
        assert row["status"] in ("locked", "error") and row["error"]
        assert conn.execute("SELECT count(*) FROM files WHERE status='ok'").fetchone()[0] > 20
    finally:
        k32.CloseHandle(h)
    if row["status"] == "locked":  # as documented: retried on the next run once the lock is gone
        index(settings)
        assert conn.execute("SELECT status FROM files WHERE name='locked.txt'").fetchone()[0] == "ok"


def test_model_unavailable_degrades_to_keyword_search(corpus, settings):
    from lfi.search import SearchEngine

    root, _ = corpus
    settings.embedding_backend = "fastembed"  # LFI_MODELS points to an empty folder in tests
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    st = Indexer(settings).run()
    assert st.embed_error and "not downloaded" in st.embed_error
    assert conn.execute("SELECT count(*) FROM files WHERE stage=1").fetchone()[0] > 0
    hits = SearchEngine(conn, None).search("eigenvalues")
    assert hits and "lecture07" in hits[0].name


def test_model_switch_reembeds_without_rereading(indexed, settings):
    from lfi.embeddings import HashingBackend

    conn, *_ = indexed
    ix = Indexer(settings, backend=HashingBackend(dim=128))
    st = ix.run()
    assert st.processed == 0 and st.embedded_files > 0
    space = library.active_space(conn)
    assert space["dim"] == 128
    assert len(library.vector_spaces(conn)) == 1  # old space dropped


def test_store_text_off_removes_text(corpus, settings):
    root, _ = corpus
    settings.store_text = False
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings)
    assert conn.execute("SELECT count(*) FROM chunks WHERE text!=''").fetchone()[0] == 0
    assert not conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'eigenvalues'").fetchall()


def test_remove_root_forgets_but_keeps_files(indexed):
    conn, root, *_ = indexed
    library.remove_root(conn, str(root))
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0
    assert (root / "College").exists()


def test_clear_index_keeps_journal(indexed, settings):
    from lfi.fileops import FileOps

    conn, root, m, *_ = indexed
    ops = FileOps(conn, settings, library.scope_policy(conn, settings))
    ops.quarantine([file_id(conn, "movie_download.crdownload")], "test")
    db.clear_index(conn)
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0
    assert len(ops.quarantined()) == 1


def test_exclusion_removes_and_skips(indexed, settings):
    conn, root, *_ = indexed
    library.add_exclusion(conn, str(root / "Finance"))
    assert not conn.execute("SELECT 1 FROM files WHERE path LIKE '%Finance%'").fetchone()
    index(settings)
    assert not conn.execute("SELECT 1 FROM files WHERE path LIKE '%Finance%'").fetchone()
    assert os.path.exists(root / "Finance" / "salary_slip.csv")
