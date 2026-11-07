"""Parallel, resource-governed work: identical results to the single-model path, nothing lost when
workers crash, pools that resize while running, and a governor that keeps reserves."""

import numpy as np
import pytest
from conftest import index

from lfi import db, library
from lfi.indexer import Indexer
from lfi.pool import IsolatedPool
from lfi.resources import GB, Governor, Snapshot


def _vectors(conn):
    space = library.active_space(conn)
    cs, fs = library.stores(conn, space)
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY id")]
    return fs.get(ids), cs.count()


@pytest.fixture
def parallel(monkeypatch):
    monkeypatch.setattr(Indexer, "_parallel_ok", lambda self: True)
    monkeypatch.setattr(Indexer, "EMBED_TASK", "helpers:fake_embed")
    import lfi.resources as r

    monkeypatch.setattr(r.Governor, "workers", lambda self, kind, cur, ceiling=None: 3)


def test_parallel_embedding_matches_the_single_model(corpus, settings, tmp_path, parallel, monkeypatch):
    root, _ = corpus
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    _, st = index(settings)
    par, n_chunks = _vectors(conn)
    assert st.gpu_workers == 3 and st.embedded_files == len(par) > 20
    # same library, serial path, separate index
    monkeypatch.setattr(Indexer, "_parallel_ok", lambda self: False)
    monkeypatch.setenv("LFI_HOME", str(tmp_path / "home2"))
    conn2 = db.connect()
    library.add_root(conn2, str(root), validate=False)
    _, st2 = index(settings)
    ser, n_chunks2 = _vectors(conn2)
    assert st2.gpu_workers == 0 and n_chunks == n_chunks2
    by_path = {r["path"]: r["id"] for r in conn2.execute("SELECT id, path FROM files")}
    for r in conn.execute("SELECT id, path FROM files"):
        assert np.allclose(par[r["id"]], ser[by_path[r["path"]]], atol=1e-6)


def test_crashing_embedding_workers_lose_nothing(corpus, settings, parallel, monkeypatch):
    monkeypatch.setattr(Indexer, "EMBED_TASK", "helpers:crashing_embed")
    root, _ = corpus
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    _, st = index(settings)
    assert st.embed_error is None or st.embed_error == ""
    assert conn.execute("SELECT count(*) FROM files WHERE stage=1").fetchone()[0] == 0  # every file embedded
    assert conn.execute("SELECT count(*) FROM files WHERE stage=2").fetchone()[0] == st.embedded_files > 20


def test_pool_resizes_while_running():
    sizes = iter([1, 3, 3, 1, 1, 2] + [2] * 100)
    seen = []

    def size_fn(cur):
        n = next(sizes)
        seen.append(n)
        return n

    with IsolatedPool(1, timeout=30, size_fn=size_fn, rescale_every=0.0) as pool:
        out = [r for _, r in pool.imap_unordered(((i, "helpers:echo", {"value": i}) for i in range(40)))]
        assert sorted(r["value"] for r in out) == list(range(40))
        assert pool.peak_workers == 3 and pool.size <= 3


def _gov(**snap):
    g = Governor("balanced")
    base = dict(cpu_busy=0.2, ram_free=12 * GB, ram_total=24 * GB, vram_used=1 * GB, vram_total=8 * GB, at=1e18)
    g._snap = Snapshot(**{**base, **snap})
    return g


def test_governor_grows_with_headroom_and_keeps_reserves():
    assert _gov().workers("gpu_embed", 1) == 2  # room: grow one step at a time
    assert _gov().workers("gpu_embed", 3) == 3  # never above the measured useful maximum
    assert _gov(ram_free=int(3.5 * GB)).workers("gpu_embed", 3) == 2  # RAM reserve breached: give one back
    assert _gov(cpu_busy=0.95).workers("reader", 6) == 5  # the user is busy: back off
    assert _gov(vram_used=int(6.5 * GB)).workers("gpu_embed", 3) == 2  # GPU memory reserve breached
    assert _gov(vram_used=int(5.5 * GB)).workers("gpu_embed", 2) == 2  # one more would breach it: hold
    assert _gov(cpu_busy=0.99, ram_free=GB).workers("gpu_embed", 1) == 1  # never below one worker
    assert _gov(vram_total=None, vram_used=None).workers("gpu_embed", 2) == 2  # unknown GPU memory: modest
    assert Governor("gentle").profile["cpu"] > Governor("fast").profile["cpu"]


def test_governor_reads_this_machine():
    g = Governor("balanced")
    s = g.snapshot()
    assert 0.0 <= s.cpu_busy <= 1.0 and 0 < s.ram_free <= s.ram_total
    assert 1 <= g.workers("reader", 2) <= 3
