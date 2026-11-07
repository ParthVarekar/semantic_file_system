"""Regression tests for indexing robustness: cloud placeholders never opened, names SQLite can't
store, transient image-worker failures, embeddings racing with auto-tags, orphaned vectors."""

import os
import sys

import pytest
from conftest import index
from test_images import FakeVision

from lfi import db, library, synthetic
from lfi.extract import process_file


@pytest.fixture
def fake_vision(monkeypatch):
    import lfi.vision as vision

    monkeypatch.setattr(vision, "pair_is_cached", lambda key: True)
    monkeypatch.setattr(vision, "VisionBackend", FakeVision)


def test_metadata_only_never_opens_the_file(tmp_path):
    p = tmp_path / "online_only.docx"
    p.write_bytes(b"pretend this would be downloaded from the cloud")
    opened = []
    watch = os.path.normcase(str(p))
    sys.addaudithook(lambda ev, args: opened.append(args[0]) if ev == "open" and isinstance(args[0], str)
                     and os.path.normcase(args[0]) == watch else None)
    res = process_file({"path": str(p), "metadata_only": True})
    assert res["status"] == "metadata_only" and res.get("hash") is None
    assert opened == []


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS allows unpaired surrogates in names")
def test_name_sqlite_cannot_store_is_skipped_not_fatal(tmp_path, settings):
    root = tmp_path / "root"
    root.mkdir()
    (root / "broken\udc80name.txt").write_text("x")
    (root / "fine.txt").write_text("hello")
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    _, st = index(settings)
    assert [r[0] for r in conn.execute("SELECT name FROM files")] == ["fine.txt"]
    assert any("not valid Unicode" in u for u in st.unreadable_dirs)
    index(settings)  # and every later run keeps working


def test_transient_image_worker_failure_is_retried(corpus, settings, fake_vision, monkeypatch):
    from lfi.indexer import Indexer

    root, _ = corpus
    synthetic.make_semantic_images(root / "Pictures" / "Phone")
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    monkeypatch.setattr(Indexer, "VISION_TASK", "helpers:broken_vision")
    _, st = index(settings)
    assert st.images_failed == 0 and "retry" in st.image_error
    assert conn.execute("SELECT count(*) FROM files WHERE img_stage=2").fetchone()[0] == 0
    monkeypatch.setattr(Indexer, "VISION_TASK", "helpers:fake_vision")
    _, st = index(settings)
    assert st.images_embedded >= 12 and st.images_failed == 0


def test_chunk_added_while_embedding_is_embedded_too(tmp_path, settings):
    from lfi.embeddings import HashingBackend
    from lfi.indexer import Indexer
    from lfi.vectorstore import VectorStore

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("first chunk of text")
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    ix = Indexer(settings, backend=HashingBackend(), embed=False)
    ix.run()  # extracted (stage 1), not embedded
    fid = conn.execute("SELECT id FROM files").fetchone()[0]
    backend = HashingBackend()
    real = backend.embed_documents

    def embed_and_race(texts):  # the image phase adds an auto-tag chunk mid-batch
        out = real(texts)
        if not conn.execute("SELECT 1 FROM chunks WHERE ord=10000").fetchone():
            conn.execute("INSERT INTO chunks(file_id, ord, text) VALUES(?,?,?)", (fid, 10000, "Image appears..."))
        return out

    backend.embed_documents = embed_and_race
    ix2 = Indexer(settings, backend=backend)
    slug = ix2._ensure_space(conn, backend)
    cs, fs = VectorStore(conn, slug, backend.dim, "chunks"), VectorStore(conn, slug, backend.dim, "files")
    ix2._embed_batch(conn, cs, fs)
    assert conn.execute("SELECT stage FROM files WHERE id=?", (fid,)).fetchone()[0] == 1  # not marked done
    ix2._embed_batch(conn, cs, fs)
    ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE file_id=?", (fid,))]
    assert len(ids) == 2 and set(cs.get(ids)) == set(ids)
    assert conn.execute("SELECT stage FROM files WHERE id=?", (fid,)).fetchone()[0] == 2


def test_deleting_chunks_removes_their_vectors(indexed):
    conn, *_ = indexed
    space = library.active_space(conn)
    cs, _ = library.stores(conn, space)
    before = cs.count()
    n = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert before and n
    library.delete_chunks_where(conn, "ord=0")
    left = [r[0] for r in conn.execute("SELECT id FROM chunks")]
    assert cs.count() == len(set(cs.get(left)))  # every remaining vector belongs to a chunk
    assert cs.count() < before


def test_explicit_download_lifts_offline_mode_only_while_it_runs(monkeypatch):
    """Startup sets HF_HUB_OFFLINE=1, which huggingface_hub caches at import; an explicit download
    must still reach the network (here: a blocked socket records the attempt) and then restore it."""
    import socket

    import huggingface_hub.constants as hub

    from lfi import config
    from lfi.embeddings import download_model

    config.enforce_offline_env()
    monkeypatch.setattr(hub, "HF_HUB_OFFLINE", True)
    attempts = []

    def blocked(*a, **k):
        attempts.append(a)
        raise OSError("network blocked in test")

    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: blocked(addr))
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)  # skip the downloader's retry back-off
    with pytest.raises(Exception):
        download_model("BAAI/bge-small-en-v1.5")
    assert attempts, "the download never tried the network"
    assert hub.HF_HUB_OFFLINE is True and os.environ["HF_HUB_OFFLINE"] == "1"


def test_unreadable_index_is_detected_and_set_aside(isolated_home):
    """A damaged index is reported (never silently used) and moved aside whole, not deleted."""
    conn = db.connect()
    conn.execute("INSERT INTO meta(key, value) VALUES('x', 'y')")
    conn.close()
    assert db.index_problem() is None
    p = isolated_home / "index.sqlite3"
    data = bytearray(p.read_bytes())
    data[4096 * 2: 4096 * 6] = b"\xde\xad" * 4096  # scribble over table pages
    p.write_bytes(bytes(data))
    assert db.index_problem()
    backup = db.set_aside_damaged()
    assert (backup / "index.sqlite3").exists() and not p.exists()
    assert db.index_problem() is None  # a fresh index is created on next connect
    assert db.connect().execute("SELECT count(*) FROM files").fetchone()[0] == 0
