"""Regression tests: path aliases of protected folders, junction escapes at operation time,
crash-recovery gaps, and folders that cannot be listed during a re-scan. Synthetic temp files
only; every junction points inside the test's own temporary directory."""

import ctypes
import os
import shutil
import subprocess
import sys

import pytest
from conftest import file_id, index

from lfi import library
from lfi.fileops import FileOps
from lfi.organize import _clean_label
from lfi.safety import ScopePolicy, real_path

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")


def junction(link, target) -> None:
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def short_name(path: str) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
    return buf.value


@pytest.fixture
def ops(indexed, settings):
    conn, root, m, *_ = indexed
    return FileOps(conn, settings, library.scope_policy(conn, settings)), conn, root


def _aliases():
    win = os.environ.get("SystemRoot", r"C:\Windows")
    drive = os.environ.get("SystemDrive", "C:")
    out = ["\\\\?\\" + win, "\\\\.\\" + win, win + "::$INDEX_ALLOCATION",
           f"\\\\localhost\\{drive[0]}$\\Windows", f"\\\\127.0.0.1\\{drive[0]}$\\Windows",
           os.path.join(os.environ["USERPROFILE"], "AppData")]
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    if short_name(pf) and short_name(pf).lower() != pf.lower():
        out.append(short_name(pf))
    return out


@pytest.mark.parametrize("spelling", _aliases())
def test_validate_root_refuses_aliases_of_protected_folders(spelling):
    assert ScopePolicy.validate_root(spelling) is not None


def test_app_home_cannot_be_added_with_a_device_prefix(isolated_home):
    isolated_home.mkdir(parents=True, exist_ok=True)
    assert ScopePolicy.validate_root("\\\\?\\" + str(isolated_home)) is not None


def test_real_path_expands_short_names(tmp_path):
    d = tmp_path / "A folder with a long name"
    d.mkdir()
    s = short_name(str(d))
    assert real_path(s).lower() == str(d).lower()


def test_organize_move_through_a_junction_is_refused(ops, tmp_path):
    fo, conn, root = ops
    outside = tmp_path / "outside"
    outside.mkdir()
    junction(root / "Organized", outside)
    src = root / "Misc" / "config.yaml"
    res = fo.move_files([(file_id(conn, "config.yaml"), str(root / "Organized" / "Data" / "config.yaml"))], "t")
    assert not res.ok and "junction" in res.failed[0].message
    assert src.exists() and not any(outside.iterdir())


def test_quarantine_through_a_replaced_folder_is_refused(ops, tmp_path):
    fo, conn, root = ops
    fid = file_id(conn, "gnn_survey.pdf")
    moved = tmp_path / "moved_out"
    os.rename(root / "Research", moved)  # same file IDs, now physically outside the root
    junction(root / "Research", moved)
    res = fo.quarantine([fid], "t")
    assert not res.ok and "junction" in res.failed[0].message
    assert (moved / "gnn_survey.pdf").exists()


def test_restore_through_a_junction_is_refused_and_copy_kept(ops, tmp_path):
    fo, conn, root = ops
    q = fo.quarantine([file_id(conn, "config.yaml")], "t")
    assert q.ok
    outside = tmp_path / "outside"
    outside.mkdir()
    os.rename(root / "Misc", tmp_path / "misc_away")
    junction(root / "Misc", outside)
    rr = fo.restore([q.ok[0].op_id])
    assert not rr.ok and "junction" in rr.failed[0].message
    assert os.path.exists(q.ok[0].dst) and not any(outside.iterdir())


def test_recover_completes_an_interrupted_undo(ops):
    fo, conn, root = ops
    src = root / "Misc" / "config.yaml"
    dst = root / "Sorted" / "Data" / "config.yaml"
    b = fo.move_files([(file_id(conn, "config.yaml"), str(dst))], "t")
    assert b.ok and dst.exists()
    op = conn.execute("SELECT * FROM operations WHERE kind='move' AND state='done'").fetchone()
    # The crash state: undo journaled as pending and the file already moved back.
    rid = fo._journal(fo._new_batch("undo", "t"), "move", str(dst), str(src),
                      {"size": op["size"], "mtime_ns": op["mtime_ns"]}, "undo", reverses=op["id"])
    fo._set_state(rid, "pending")
    os.rename(dst, src)
    fo.recover()
    states = dict(conn.execute("SELECT id, state FROM operations"))
    assert states[op["id"]] == "undone" and states[rid] == "done"
    assert not (root / "Sorted").exists()  # folders the organize batch created are gone
    assert conn.execute("SELECT path FROM files WHERE name='config.yaml'").fetchone()[0] == str(src)


def test_recover_rolls_back_a_pending_verified_copy(ops):
    fo, conn, root = ops
    src = root / "Misc" / "data.json"
    dst = root / "Elsewhere" / "data.json"
    st = os.stat(src)
    rid = fo._journal(fo._new_batch("organize", "t"), "move", str(src), str(dst),
                      {"size": st.st_size, "mtime_ns": st.st_mtime_ns}, "t")
    dst.parent.mkdir()
    fo._set_state(rid, "pending", method="copy", created_dirs=f'["{dst.parent.as_posix()}"]')
    shutil.copy2(src, dst)  # crash after the copy was finalized, before it was journaled
    report = fo.recover()
    assert any("rolled back" in r for r in report)
    assert src.exists() and not dst.exists() and not dst.parent.exists()


def test_unlistable_subfolder_keeps_its_index_rows(indexed, settings, monkeypatch):
    conn, root, *_ = indexed
    before = conn.execute("SELECT count(*) FROM files WHERE path LIKE ?", ("%\\Finance\\%",)).fetchone()[0]
    assert before
    real_scandir = os.scandir
    blocked = str(root / "Finance").lower()

    def scandir(p):
        if str(p).lower().endswith(blocked):
            raise PermissionError(13, "Access is denied", str(p))
        return real_scandir(p)

    monkeypatch.setattr(os, "scandir", scandir)
    _, st = index(settings)
    assert any("Finance" in u for u in st.unreadable_dirs)
    assert conn.execute("SELECT count(*) FROM files WHERE path LIKE ?", ("%\\Finance\\%",)).fetchone()[0] == before
    monkeypatch.setattr(os, "scandir", real_scandir)
    shutil.rmtree(root / "Finance")  # now really gone: a complete walk removes the rows
    index(settings)
    assert conn.execute("SELECT count(*) FROM files WHERE path LIKE ?", ("%\\Finance\\%",)).fetchone()[0] == 0


def test_root_replaced_by_junction_is_skipped_and_index_kept(indexed, settings, tmp_path):
    conn, root, *_ = indexed
    n = conn.execute("SELECT count(*) FROM files").fetchone()[0]
    os.rename(root, tmp_path / "corpus_away")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "stranger.txt").write_text("not from the selected folder")
    junction(root, elsewhere)
    _, st = index(settings)
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == n
    assert not conn.execute("SELECT 1 FROM files WHERE name='stranger.txt'").fetchone()
    assert any("now a link" in u for u in st.unreadable_dirs)


@pytest.mark.parametrize("label,expected", [("CON", "CON folder"), ("nul", "nul folder"), ("com1", "com1 folder"),
                                            ("nul.txt", "nul.txt folder"), ("Notes.", "Notes"),
                                            ("x" * 59 + " .y", "x" * 59), ("Console", "Console")])
def test_clean_label_avoids_reserved_and_trailing_names(label, expected):
    assert _clean_label(label) == expected


def test_whole_system_drive_can_be_selected_but_its_system_folders_are_never_entered():
    drive = os.environ.get("SystemDrive", "C:") + "\\"
    assert ScopePolicy.validate_root(drive) is None
    pol = ScopePolicy(roots=[drive])
    for inside in (os.environ.get("SystemRoot", r"C:\Windows"), os.environ["ProgramFiles"],
                   os.environ["LOCALAPPDATA"], drive + "NVIDIA", drive + "$Recycle.Bin"):
        assert pol.check(inside) is not None, inside
    assert pol.check(os.path.join(os.environ["USERPROFILE"], "Documents")) is None


def test_a_drive_added_after_a_folder_takes_it_over_without_reindexing(tmp_path, settings):
    from lfi import db

    outer = tmp_path / "drive"
    (outer / "Desktop").mkdir(parents=True)
    (outer / "Desktop" / "plan.txt").write_text("the plan")
    (outer / "notes.txt").write_text("notes")
    conn = db.connect()
    library.add_root(conn, str(outer / "Desktop"), validate=False)
    index(settings, embed=False)
    before = conn.execute("SELECT id FROM files WHERE name='plan.txt'").fetchone()[0]
    new = library.add_root(conn, str(outer), validate=False)
    assert [r["id"] for r in library.roots(conn)] == [new]  # the inner folder was absorbed
    _, st = index(settings, embed=False)
    assert st.processed == 1  # only notes.txt is new; the Desktop file was not read again
    assert conn.execute("SELECT id FROM files WHERE name='plan.txt'").fetchone()[0] == before
    with pytest.raises(ValueError, match="overlaps"):
        library.add_root(conn, str(outer / "Desktop"), validate=False)  # already covered


def test_program_folders_and_other_accounts_are_skipped_in_a_drive_scan_but_can_be_chosen():
    from lfi.safety import drive_tool_dir, norm

    drive = os.environ.get("SystemDrive", "C:") + "\\"
    me = os.environ["USERPROFILE"]
    pol = ScopePolicy(roots=[drive])
    for skipped in ("Python314", "msys64", "SWSetup", "tmp", "chrome-debug", r"Users\TEMP.Font Driver Host",
                    r"Users\UMFD-0", r"Users\postgres", r"Users\Default"):
        assert pol.check(drive + skipped + r"\x.txt") is not None, skipped
    for kept in ("AI", "UEProjects", r"Users\Public", os.path.relpath(me, drive)):
        assert pol.check(drive + kept + r"\x.txt") is None, kept
    assert pol.check(drive + r"AI\tmp\python3\x.txt") is None  # only at the top of a drive
    assert ScopePolicy(roots=[drive + "llama"]).check(drive + r"llama\notes.txt") is None  # chosen on its own
    assert drive_tool_dir(norm(drive + "llama")) and not drive_tool_dir(norm(drive + "AI"))


def test_select_all_on_a_drive_leaves_program_folders_out(tmp_path, monkeypatch):
    from lfi import db
    from lfi.ui import folderpick

    drive = os.environ.get("SystemDrive", "C:") + "\\"
    monkeypatch.setattr(folderpick.ScopePolicy, "validate_root", staticmethod(lambda p: None))
    monkeypatch.setattr(folderpick.library, "add_root", lambda conn, p: 1)
    added, notes = folderpick.add_folders(db.connect(), [drive + "AI", drive + "Python314", drive + "msys64"])
    assert added == [drive + "AI"]
    assert "Python314" in notes[-1] and "msys64" in notes[-1]
    added, _ = folderpick.add_folders(db.connect(), [drive + "Python314"])  # chosen alone: allowed
    assert added == [drive + "Python314"]
