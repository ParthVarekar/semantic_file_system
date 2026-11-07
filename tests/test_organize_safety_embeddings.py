import os
import socket
import sys

import numpy as np
import pytest
from conftest import REAL_MODELS, file_id

from lfi import library
from lfi.embeddings import HashingBackend, ModelUnavailable, OllamaBackend
from lfi.organize import build_plan, merge_groups, rename_group, set_category, split_group
from lfi.safety import ScopePolicy, is_within, long_path, norm


# ---------------------------------------------------------------- organize
def test_plan_respects_projects_coherent_folders_and_cleanup(indexed, settings):
    conn, root, m, *_ = indexed
    plan = build_plan(conn, settings, HashingBackend())
    moved = {os.path.basename(mv.src) for mv in plan.moves}
    kept = dict(plan.kept)
    assert "index.js" not in moved and "lecture07_eigenvalues.docx" not in moved
    assert "ZoomInstaller_setup.exe" in moved and "movie_download.crdownload" not in moved
    assert "lecture07_eigenvalues - Copy.docx" not in moved  # duplicate -> Duplicates page
    assert any("project" in why for why in kept.values())
    for mv in plan.moves:
        assert mv.dst.startswith(str(root / "Organized"))
        assert mv.reasons and mv.confidence in ("high", "medium", "low")
    inst = next(mv for mv in plan.moves if mv.src.endswith("ZoomInstaller_setup.exe"))
    assert inst.selected and "Installers" in inst.dst
    weak = [mv for mv in plan.moves if mv.confidence == "low"]
    assert all(not mv.selected for mv in weak)


def test_folder_units_keep_structure(corpus, settings):
    from conftest import index

    from lfi import db

    root, m = corpus
    unit = root / "Downloads" / "Semester 3 Notes" / "week1"
    unit.mkdir(parents=True)
    (unit / "lecture1.txt").write_text("Lecture 1 notes: thermodynamics syllabus and homework.")
    (unit.parent / "lecture2.txt").write_text("Lecture 2 notes: entropy homework.")
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings)
    plan = build_plan(conn, settings, HashingBackend())
    dsts = {os.path.basename(mv.src): mv.dst for mv in plan.moves}
    assert dsts["lecture1.txt"].endswith(os.path.join("Semester 3 Notes", "week1", "lecture1.txt"))
    assert dsts["lecture2.txt"].endswith(os.path.join("Semester 3 Notes", "lecture2.txt"))


def test_collisions_and_edits(indexed, settings):
    conn, root, m, *_ = indexed
    plan = build_plan(conn, settings, HashingBackend())
    inst = next(mv for mv in plan.moves if mv.src.endswith("ZoomInstaller_setup.exe"))
    os.makedirs(os.path.dirname(inst.dst), exist_ok=True)
    with open(inst.dst, "w") as fh:
        fh.write("someone else's file")
    from lfi.organize import refresh_destinations

    refresh_destinations(conn, plan)
    assert inst.dst.endswith("ZoomInstaller_setup (2).exe") and "collision" in inst.note
    key = inst.group_key
    rename_group(conn, plan, key, "Setup files")
    assert "Setup files" in inst.dst
    other = next(k for k, g in plan.groups.items() if k != key and any(mv.group_key == k for mv in plan.moves))
    merge_groups(conn, plan, key, other)
    assert other not in plan.groups
    ids = [mv.file_id for mv in plan.moves if mv.group_key == key][:1]
    nk = split_group(conn, plan, key, ids, "Split part")
    assert all(mv.group_key == nk for mv in plan.moves if mv.file_id in ids)
    set_category(conn, plan, inst.file_id, "Software Archive")
    pref = conn.execute("SELECT category FROM prefs WHERE path_key=?", (norm(inst.src),)).fetchone()
    assert pref["category"] == "Software Archive"
    plan2 = build_plan(conn, settings, HashingBackend())
    assert next(mv for mv in plan2.moves if mv.file_id == inst.file_id).category == "Software Archive"


# ---------------------------------------------------------------- safety
def test_scope_policy(tmp_path):
    root = tmp_path / "root"
    (root / "a" / "node_modules").mkdir(parents=True)
    (root / "b").mkdir()
    pol = ScopePolicy([str(root)], exclude_globs=["node_modules"], exclude_paths=[str(root / "b")])
    assert pol.check(str(root / "a" / "x.txt")) is None
    assert "excluded name" in pol.check(str(root / "a" / "node_modules" / "y.js"))
    assert pol.check(str(root / "b" / "z.txt")) == "excluded folder"
    assert pol.check(str(tmp_path / "rootsibling" / "q.txt")) == "outside selected folders"
    assert pol.check(str(root) + "x" + os.sep + "f") == "outside selected folders"  # prefix trick


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")
def test_protected_roots_rejected():
    assert "protected" in ScopePolicy.validate_root(os.environ["WINDIR"])
    assert "protected" in ScopePolicy.validate_root(os.environ["ProgramFiles"])
    assert "protected" in ScopePolicy.validate_root(os.environ["APPDATA"])


def test_path_helpers():
    assert is_within(norm("C:/a/b/c"), norm("C:/a/b")) and not is_within(norm("C:/a/bc"), norm("C:/a/b"))
    assert library.location_parts(r"C:\Users\me\Desktop\Root\College\x.pdf", r"C:\Users\me\Desktop\Root") == \
        ["root", "college"]
    if sys.platform == "win32":
        assert long_path("C:\\" + "a" * 250).startswith("\\\\?\\")


# ---------------------------------------------------------------- embeddings
def test_hashing_backend_deterministic():
    b = HashingBackend(128)
    v1, v2 = b.embed_documents(["hello world", "hello world"])
    assert np.allclose(v1, v2) and abs(np.linalg.norm(v1) - 1) < 1e-5
    assert b.embed_query("hello world") @ v1 > b.embed_query("unrelated words") @ v1


def test_missing_model_message():
    from lfi.config import Settings
    from lfi.embeddings import create_backend

    with pytest.raises(ModelUnavailable, match="not downloaded"):
        create_backend(Settings())


def test_ollama_must_be_local():
    with pytest.raises(ModelUnavailable, match="this computer"):
        OllamaBackend("nomic-embed-text", "http://example.com:11434")


@pytest.mark.model
def test_real_model_offline_cpu(monkeypatch):
    """Requires `lfi models download BAAI/bge-small-en-v1.5`. Network is blocked."""
    from lfi.embeddings import FastEmbedBackend

    if not (REAL_MODELS / "models--Qdrant--bge-small-en-v1.5-onnx-Q").exists():
        pytest.skip("model not downloaded")
    monkeypatch.setenv("LFI_MODELS", str(REAL_MODELS))

    def no_network(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    b = FastEmbedBackend("BAAI/bge-small-en-v1.5", device="cpu")
    assert b.dim == 384 and b.device == "cpu"
    docs = b.embed_documents(["Electricity bill payment receipt for March", "Eigenvalues of a symmetric matrix"])
    q = b.embed_query("how much did I pay for power")
    assert q @ docs[0] > q @ docs[1]


@pytest.mark.model
def test_real_model_end_to_end_semantic(corpus, settings, monkeypatch):
    from conftest import index

    from lfi import db
    from lfi.embeddings import FastEmbedBackend
    from lfi.search import SearchEngine

    if not (REAL_MODELS / "models--Qdrant--bge-small-en-v1.5-onnx-Q").exists():
        pytest.skip("model not downloaded")
    monkeypatch.setenv("LFI_MODELS", str(REAL_MODELS))
    root, _ = corpus
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    b = FastEmbedBackend("BAAI/bge-small-en-v1.5", device="cpu")
    index(settings, backend=b)
    se = SearchEngine(conn, b)
    assert se.search("code that makes pictures smaller")[0].name == "resize_images.py"
    assert se.search("sunny day at the seaside photo")[0].name.startswith("beach_holiday")
    assert file_id(conn, "resize_images.py")
