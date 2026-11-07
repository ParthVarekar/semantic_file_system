"""Shared fixtures. Every test runs against synthetic files in a temporary directory with a
private LFI_HOME, so neither the developer's files nor the real app index are touched."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

REAL_MODELS = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LocalFileIntelligence" / "models"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "lfi_home"
    monkeypatch.setenv("LFI_HOME", str(home))
    monkeypatch.setenv("LFI_MODELS", str(tmp_path / "no_models"))  # no model unless a test opts in
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    yield home


@pytest.fixture
def settings():
    from lfi.config import Settings

    s = Settings()
    s.embedding_backend = "hashing"
    s.extract_workers = 2
    s.large_file_bytes = 2 * 1024 * 1024
    s.extract_timeout_s = 30
    return s


@pytest.fixture
def corpus(tmp_path):
    from lfi.synthetic import build_corpus

    root = tmp_path / "corpus"
    manifest = build_corpus(root)
    return root, manifest


def index(settings, backend=None, embed=True):
    from lfi.embeddings import HashingBackend
    from lfi.indexer import Indexer

    ix = Indexer(settings, backend=backend or (HashingBackend() if embed else None), embed=embed)
    st = ix.run()
    return ix, st


@pytest.fixture
def indexed(corpus, settings):
    from lfi import db, library

    root, manifest = corpus
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    ix, st = index(settings)
    return conn, root, manifest, ix, st


def snapshot(root: Path) -> dict:
    """path -> (size, mtime_ns, blake3) for every file under root (long-path safe)."""
    from lfi.extract import hash_file
    from lfi.safety import long_path

    out = {}
    for dirpath, dirs, files in os.walk(long_path(str(root))):
        for f in files:
            p = os.path.join(dirpath, f)
            st = os.stat(p)
            out[p.replace("\\\\?\\", "")] = (st.st_size, st.st_mtime_ns, hash_file(p))
    return out


def file_id(conn, name: str) -> int:
    row = conn.execute("SELECT id FROM files WHERE name=?", (name,)).fetchone()
    assert row, f"{name} not indexed"
    return row["id"]
