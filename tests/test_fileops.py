"""Safety tests for file operations. Synthetic temp files only."""

import json
import os
import sys
import time

import pytest
from conftest import file_id, index

from lfi import config, db, library
from lfi.extract import hash_file
from lfi.fileops import CONFIRM_PURGE, FileOps, OpRefused


@pytest.fixture
def ops(indexed, settings):
    conn, root, m, *_ = indexed
    return FileOps(conn, settings, library.scope_policy(conn, settings)), conn, root, m


def test_quarantine_restore_roundtrip_preserves_bytes_and_times(ops):
    fo, conn, root, m = ops
    p = m["temp"]
    before = (hash_file(str(p)), os.stat(p).st_mtime_ns)
    res = fo.quarantine([file_id(conn, p.name)], "test", "temporary")
    assert len(res.ok) == 1 and not p.exists()
    q = res.ok[0].dst
    assert os.path.exists(q) and os.path.commonpath([q, str(config.default_quarantine_dir())])
    manifest = (config.default_quarantine_dir() / "manifest.jsonl").read_text(encoding="utf-8")
    assert json.loads(manifest.splitlines()[-1])["original"] == str(p)
    assert not conn.execute("SELECT 1 FROM files WHERE name=?", (p.name,)).fetchone()
    rr = fo.restore([res.ok[0].op_id])
    assert len(rr.ok) == 1 and p.exists()
    assert (hash_file(str(p)), os.stat(p).st_mtime_ns) == before
    assert not os.path.exists(os.path.dirname(q))  # empty per-op folder cleaned
    states = [r["state"] for r in conn.execute("SELECT state FROM operations ORDER BY id")]
    assert states == ["restored", "done"]


def test_refuses_file_changed_since_indexing(ops):
    fo, conn, root, m = ops
    p = m["temp"]
    time.sleep(0.02)
    with open(p, "ab") as fh:
        fh.write(b"more")
    res = fo.quarantine([file_id(conn, p.name)], "test")
    assert not res.ok and "re-index" in res.failed[0].message and p.exists()


def test_refuses_pinned_project_and_out_of_scope(ops, tmp_path, settings):
    fo, conn, root, m = ops
    library.set_pref(conn, str(m["paper"]), pinned=1)
    r = fo.quarantine([file_id(conn, m["paper"].name)], "test")
    assert not r.ok and "pinned" in r.failed[0].message and m["paper"].exists()
    r = fo.quarantine([file_id(conn, "index.js")], "test")
    assert not r.ok and "project" in r.failed[0].message
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    conn.execute("UPDATE files SET path=?, path_key=? WHERE name='config.yaml'", (str(outside), str(outside).lower()))
    r = fo.quarantine([file_id(conn, "config.yaml")], "test")
    assert not r.ok and "outside" in r.failed[0].message and outside.exists()


def test_never_overwrites_on_move(ops):
    fo, conn, root, m = ops
    dst = root / "Research" / "efficient_attention_transformers.pdf"  # existing, different file
    before = hash_file(str(dst))
    res = fo.move_files([(file_id(conn, "gnn_survey.pdf"), str(dst))], "test")
    assert not res.ok and "never overwritten" in res.failed[0].message
    assert hash_file(str(dst)) == before and (root / "Research" / "gnn_survey.pdf").exists()


def test_move_and_undo_remove_created_dirs(ops):
    fo, conn, root, m = ops
    src = root / "Misc" / "config.yaml"
    dst = root / "Organized" / "Data" / "config.yaml"
    res = fo.move_files([(file_id(conn, "config.yaml"), str(dst))], "test")
    assert len(res.ok) == 1 and dst.exists() and not src.exists()
    assert conn.execute("SELECT path FROM files WHERE name='config.yaml'").fetchone()[0] == str(dst)
    u = fo.undo_batch(res.batch_id)
    assert len(u.ok) == 1 and src.exists() and not (root / "Organized").exists()
    assert conn.execute("SELECT path FROM files WHERE name='config.yaml'").fetchone()[0] == str(src)


def test_restore_conflict_policies(ops):
    fo, conn, root, m = ops
    p = m["empty"]
    r = fo.quarantine([file_id(conn, p.name)], "test")
    p.write_text("a new file took its place")
    skip = fo.restore([r.ok[0].op_id], on_conflict="skip")
    assert not skip.ok and "occupied" in skip.failed[0].message
    ren = fo.restore([r.ok[0].op_id], on_conflict="rename")
    assert len(ren.ok) == 1 and ren.ok[0].dst.endswith("empty_note (restored).txt")
    assert p.read_text() == "a new file took its place"


def test_duplicate_keeper_must_still_match(ops):
    fo, conn, root, m = ops
    dup, keeper = file_id(conn, m["paper_dup"].name), file_id(conn, m["paper"].name)
    time.sleep(0.02)
    with open(m["paper"], "ab") as fh:
        fh.write(b"%edit")
    r = fo.quarantine([dup], "dupes", "exact_duplicates", {dup: keeper})
    assert not r.ok and "kept" in r.failed[0].message and m["paper_dup"].exists()


def test_duplicate_keeper_verified_ok(ops):
    fo, conn, root, m = ops
    dup, keeper = file_id(conn, m["paper_dup"].name), file_id(conn, m["paper"].name)
    r = fo.quarantine([dup], "dupes", "exact_duplicates", {dup: keeper})
    assert len(r.ok) == 1 and m["paper"].exists() and not m["paper_dup"].exists()


def test_hard_links_are_not_reclaimable(corpus, settings):
    from lfi.duplicates import exact_groups

    root, m = corpus
    link = root / "College" / "hardlink_to_lecture.docx"
    os.link(m["la"], link)
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings)
    g = next(g for g in exact_groups(conn) if any(f.name == link.name for f in g.files))
    hl = [f for f in g.files if f.hardlink_of]
    assert hl and g.reclaimable() == m["la"].stat().st_size  # only the real "- Copy" frees space
    fo = FileOps(conn, settings, library.scope_policy(conn, settings))
    a, b = file_id(conn, link.name), file_id(conn, m["la"].name)
    r = fo.quarantine([a], "t", "exact_duplicates", {a: b})
    assert not r.ok and "hard links" in r.failed[0].message


def test_symlink_or_junction_never_moved(ops, tmp_path):
    fo, conn, root, m = ops
    link = root / "Misc" / "link.txt"
    try:
        os.symlink(root / "Misc" / "config.yaml", link)
    except OSError:
        pytest.skip("symlink creation needs Developer Mode/admin on Windows")
    st = os.lstat(link)
    conn.execute("INSERT INTO files(root_id, path, path_key, parent_key, name, ext, size, mtime_ns, stage, status)"
                 " VALUES(1, ?, ?, ?, 'link.txt', '.txt', ?, ?, 1, 'ok')",
                 (str(link), str(link).lower(), str(link.parent).lower(), st.st_size, st.st_mtime_ns))
    r = fo.quarantine([file_id(conn, "link.txt")], "t")
    assert not r.ok and "link" in r.failed[0].message


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing modes")
def test_locked_file_left_untouched(ops):
    import ctypes
    from ctypes import wintypes

    fo, conn, root, m = ops
    p = m["installer"]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(p), 0x80000000, 0x1, None, 3, 0x80, None)  # share read only, not delete
    try:
        r = fo.quarantine([file_id(conn, p.name)], "t")
        assert not r.ok and ("locked" in r.failed[0].message or "denied" in r.failed[0].message)
    finally:
        k32.CloseHandle(h)
    assert p.exists()
    st = conn.execute("SELECT state FROM operations ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert st == "failed"


def test_recovery_after_crash_mid_operation(ops):
    fo, conn, root, m = ops
    # Simulate: journal says pending, then the rename happened, then the process died.
    src = str(m["lockfile"])
    fid = file_id(conn, m["lockfile"].name)
    qdst = str(config.default_quarantine_dir() / "999999" / "000001" / m["lockfile"].name)
    bid = fo._new_batch("quarantine", "crash test")
    oid = fo._journal(bid, "quarantine", src, qdst, {"size": os.path.getsize(src)}, "crash")
    fo._set_state(oid, "pending")
    os.makedirs(os.path.dirname(qdst))
    os.rename(src, qdst)
    # And a second op that was journaled but never executed.
    src2 = str(m["msi"])
    oid2 = fo._journal(bid, "quarantine", src2, src2 + ".q", {"size": os.path.getsize(src2)}, "crash")
    fo._set_state(oid2, "pending")
    report = fo.recover()
    assert any("completed before interruption" in r for r in report)
    assert any("not performed" in r for r in report)
    states = dict(conn.execute("SELECT id, state FROM operations WHERE id IN (?,?)", (oid, oid2)).fetchall())
    assert states == {oid: "done", oid2: "failed"}
    assert not conn.execute("SELECT 1 FROM files WHERE id=?", (fid,)).fetchone()
    assert os.path.exists(src2)
    assert fo.recover() == []  # idempotent


def test_recovery_removes_partial_copy(ops):
    fo, conn, root, m = ops
    src = str(m["msi"])
    dst = str(root / "Elsewhere" / "vlc.msi")
    bid = fo._new_batch("organize", "crash test")
    oid = fo._journal(bid, "move", src, dst, {"size": os.path.getsize(src)}, "crash")
    fo._set_state(oid, "pending", method="copy")
    os.makedirs(os.path.dirname(dst))
    with open(dst + ".lfi-partial", "wb") as fh:
        fh.write(b"half")
    report = fo.recover()
    assert not os.path.exists(dst + ".lfi-partial") and os.path.exists(src)
    assert any("incomplete copy" in r for r in report)


def test_cross_volume_disabled_then_verified_copy(ops, monkeypatch):
    import lfi.fileops as fops

    fo, conn, root, m = ops
    monkeypatch.setattr(fops, "same_volume", lambda a, b: False)  # pretend destination is another drive
    src = root / "Misc" / "data.json"
    dst = root / "Organized" / "data.json"
    r = fo.move_files([(file_id(conn, "data.json"), str(dst))], "t")
    assert not r.ok and "another drive" in r.failed[0].message and src.exists()
    assert not (root / "Organized").exists()
    fo.settings.allow_cross_volume_moves = True
    h = hash_file(str(src))
    r = fo.move_files([(file_id(conn, "data.json"), str(dst))], "t")
    assert len(r.ok) == 1 and not src.exists() and hash_file(str(dst)) == h
    assert conn.execute("SELECT method FROM operations WHERE id=?", (r.ok[0].op_id,)).fetchone()[0] == "copy"


def test_purge_requires_phrase_and_trash_uses_recycle_bin(ops, monkeypatch):
    fo, conn, root, m = ops
    r = fo.quarantine([file_id(conn, m["temp"].name), file_id(conn, m["empty"].name)], "t")
    a, b = (x.op_id for x in r.ok)
    with pytest.raises(OpRefused):
        fo.purge_quarantined([a], "yes")
    trashed = []
    import send2trash

    monkeypatch.setattr(send2trash, "send2trash", lambda p: (trashed.append(p), os.remove(p)))
    t = fo.trash_quarantined([b])
    assert len(t.ok) == 1 and trashed
    p = fo.purge_quarantined([a], CONFIRM_PURGE)
    assert len(p.ok) == 1
    assert not fo.quarantined()
    states = {r["state"] for r in conn.execute("SELECT state FROM operations WHERE kind='quarantine'")}
    assert states == {"trashed", "purged"}


def test_purge_refuses_files_outside_quarantine(ops):
    fo, conn, root, m = ops
    r = fo.quarantine([file_id(conn, m["temp"].name)], "t")
    conn.execute("UPDATE operations SET dst=? WHERE id=?", (str(m["paper"]), r.ok[0].op_id))
    p = fo.purge_quarantined([r.ok[0].op_id], CONFIRM_PURGE)
    assert not p.ok and m["paper"].exists()


def test_quarantine_respects_tampered_quarantine_copy(ops):
    fo, conn, root, m = ops
    r = fo.quarantine([file_id(conn, m["installer"].name)], "t")
    with open(r.ok[0].dst, "ab") as fh:
        fh.write(b"tamper")
    rr = fo.restore([r.ok[0].op_id])
    assert not rr.ok and not m["installer"].exists()


def test_audit_log_written(ops):
    fo, conn, root, m = ops
    fo.quarantine([file_id(conn, m["temp"].name)], "t")
    lines = (config.logs_dir() / "operations.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["state"] for x in lines][-2:] == ["pending", "done"]
