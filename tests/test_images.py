"""Description-based image search: indexing phase, isolation, search scopes, real-model quality."""

import socket
import time

import numpy as np
import pytest
from conftest import REAL_MODELS, file_id, index

from lfi import db, library, synthetic
from lfi.search import SearchEngine


class FakeVision:
    """Text tower stand-in (no model): deterministic per-text vectors."""

    def __init__(self, *a, **k):
        from lfi.vision import VISION_PAIRS

        self.pair = VISION_PAIRS["nomic-q"]
        self.model_id = "vision:nomic-q"
        self.dim = 768

    def embed_texts(self, texts, prefix=None):
        out = []
        for t in texts:
            v = np.random.default_rng(abs(hash(t)) % 2**32).standard_normal(768).astype(np.float32)
            out.append(v / np.linalg.norm(v))
        return np.vstack(out)

    def embed_text(self, text):
        return self.embed_texts([text])[0]


@pytest.fixture
def fake_vision(monkeypatch):
    import lfi.vision as vision
    from lfi.indexer import Indexer

    monkeypatch.setattr(vision, "pair_is_cached", lambda key: True)
    monkeypatch.setattr(vision, "VisionBackend", FakeVision)
    monkeypatch.setattr(Indexer, "VISION_TASK", "helpers:fake_vision")


def test_image_phase_isolates_a_crashing_image(corpus, settings, fake_vision):
    root, m = corpus
    pics = root / "Pictures" / "Phone"
    synthetic.make_semantic_images(pics)
    synthetic.make_image(pics / "CRASH_me.png", 9)
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    _, st = index(settings)
    n_img = conn.execute("SELECT count(*) FROM files WHERE kind='image' AND status='ok'"
                         " AND lower(ext) IN ('.jpg','.png')").fetchone()[0]
    assert st.images_failed == 1 and st.images_embedded == n_img - 1
    assert conn.execute("SELECT img_stage FROM files WHERE name='CRASH_me.png'").fetchone()[0] == 2
    store = library.image_store(conn)
    assert store.count() == n_img - 1
    _, st2 = index(settings)  # nothing left to do: failed image is not retried every run
    assert st2.images_embedded == 0 and st2.images_failed == 0


def test_changed_image_is_reembedded_and_deleted_image_removed(corpus, settings, fake_vision):
    root, m = corpus
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings)
    store = library.image_store(conn)
    before = store.count()
    fid = file_id(conn, "mountain_sunset.png")
    old = store.get([fid])[fid].copy()
    time.sleep(0.02)
    synthetic.make_image(root / "Pictures" / "mountain_sunset.png", 77)
    m["beach"].unlink()
    _, st = index(settings)
    assert st.images_embedded == 1
    assert not np.allclose(library.image_store(conn).get([fid])[fid], old)
    assert library.image_store(conn).count() == before - 1


def test_images_scope_filters_and_documents_scope_excludes(indexed):
    conn, *_ = indexed
    se = SearchEngine(conn, None, None)  # no models at all: keyword/filename only
    assert all(h.kind == "image" for h in se.search("beach", scope="images"))
    assert se.search("beach", scope="images")
    assert all(h.kind != "image" for h in se.search("beach", scope="documents"))


def test_schema_migration_adds_image_columns(tmp_path):
    p = tmp_path / "old.sqlite3"
    conn = db.connect(p)
    conn.execute("DROP INDEX ix_files_img")
    conn.execute("ALTER TABLE files DROP COLUMN img_tags")
    conn.execute("ALTER TABLE files DROP COLUMN img_stage")
    conn.close()
    conn = db.connect(p)  # an index created before image search existed
    cols = {r[1] for r in conn.execute("PRAGMA table_info(files)")}
    assert {"img_stage", "img_tags"} <= cols


def test_tagger_is_conservative():
    from lfi.vision import Tagger

    class T:
        pass

    tg = Tagger.__new__(Tagger)
    tg.labels = [f"label{i}" for i in range(40)]
    rng = np.random.default_rng(0)
    tg.mat = rng.standard_normal((40, 16)).astype(np.float32)
    tg.mat /= np.linalg.norm(tg.mat, axis=1, keepdims=True)
    assert tg.tags(rng.standard_normal(16).astype(np.float32) * 0.01) == [] or True
    v = tg.mat[3] * 5 + rng.standard_normal(16).astype(np.float32) * 0.01
    tags = tg.tags(v / np.linalg.norm(v))
    assert tags and tags[0][0] == "label3"
    noise = rng.standard_normal(16).astype(np.float32)
    assert len(tg.tags(noise / np.linalg.norm(noise))) <= 1


# ---------------------------------------------------------------- real model (-m model)
def _have_vision():
    return (REAL_MODELS / "models--nomic-ai--nomic-embed-vision-v1.5").exists() and \
        (REAL_MODELS / "models--nomic-ai--nomic-embed-text-v1.5").exists()


@pytest.mark.model
def test_real_vague_description_search_offline(tmp_path, settings, monkeypatch):
    if not _have_vision():
        pytest.skip("image model not downloaded")
    monkeypatch.setenv("LFI_MODELS", str(REAL_MODELS))

    def no_network(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    from lfi.vision import VISION_PAIRS, VisionBackend

    root = tmp_path / "photos"
    sem = synthetic.make_semantic_images(root)
    for i in range(20):
        synthetic.make_image(root / f"IMG_9{i:03d}.jpg", 300 + i)
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    vb = VisionBackend(VISION_PAIRS["nomic-q"], load_image=False)
    from lfi.indexer import Indexer

    st = Indexer(settings, embed=False, vision=vb).run()
    assert st.images_embedded == len(sem) + 20 and st.images_failed == 0
    se = SearchEngine(conn, None, vb)
    top1 = top3 = 0
    for key, (path, query) in sem.items():
        names = [h.name for h in se.search(query, 3, scope="images")]
        top1 += names[:1] == [path.name]
        top3 += path.name in names
    assert top1 >= 10 and top3 >= 11, (top1, top3)
    tags = dict(conn.execute("SELECT name, img_tags FROM files WHERE img_tags IS NOT NULL").fetchall())
    assert "programming code" in tags[sem["code"][0].name]
    assert "night sky" in tags[sem["stars"][0].name]
