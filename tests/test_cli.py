"""CLI behaviour and the writer lock: piped output with any file name, clear errors and exit codes,
drive-root overlap, recovery that leaves another process's live batch alone, undo history.
Synthetic temp files only; the CLI runs as a subprocess with an isolated home."""

import os
import subprocess
import sys

import pytest
from conftest import file_id

from lfi import db, library
from lfi.fileops import FileOps, OpRefused


@pytest.fixture
def cli(tmp_path):
    fake = tmp_path / "profile"  # so pytest's temp folder isn't inside a protected AppData
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env.update(LFI_HOME=str(tmp_path / "home"), LFI_MODELS=str(tmp_path / "no_models"), USERPROFILE=str(fake),
               LOCALAPPDATA=str(fake / "AppData" / "Local"), APPDATA=str(fake / "AppData" / "Roaming"))

    def run(*args):
        r = subprocess.run([sys.executable, "-m", "lfi.cli", *args], env=env, capture_output=True, timeout=300)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")

    return run


def test_piped_output_with_non_ascii_names_never_crashes(cli, tmp_path):
    corpus = tmp_path / "corpus ünï 📁"
    assert cli("demo", str(corpus))[0] == 0
    code, out, err = cli("roots", "add", str(corpus))
    assert code == 0 and "📁" in out and "Traceback" not in err
    code, out, err = cli("index", "--no-embed")
    assert code == 0, err
    code, out, err = cli("search", "résumé")
    assert code == 0 and "नोट्स" in out and "Traceback" not in err


def test_bad_input_gives_messages_and_exit_codes_not_tracebacks(cli, tmp_path):
    code, _, err = cli("roots", "add", os.environ.get("SystemRoot", r"C:\Windows"), str(tmp_path / "missing"))
    assert code == 1 and err.count("error:") == 2 and "Traceback" not in err
    code, _, err = cli("roots", "remove", str(tmp_path / "never_added"))
    assert code == 1 and "not a selected folder" in err
    for args, text in ((("undo", "999"), "no batch 999"), (("quarantine", "add", "abc"), "neither a file id"),
                       (("models", "download"), "name the model"), (("models", "download-vision", "bogus"), "unknown image"),
                       (("index",), "no folders selected")):
        code, _, err = cli(*args)
        assert code in (1, 2) and text in err and "Traceback" not in err, (args, err)


def test_drive_root_and_subfolder_cannot_both_be_selected(isolated_home):
    conn = db.connect()
    library.add_root(conn, "Q:\\", validate=False)
    with pytest.raises(ValueError, match="overlaps"):
        library.add_root(conn, "Q:\\Documents", validate=False)


def test_recovery_leaves_another_process_live_batch_alone(indexed, settings):
    conn, root, *_ = indexed
    other = FileOps(conn, settings, library.scope_policy(conn, settings))
    assert other._lock.acquire(0)  # another window is in the middle of a batch...
    try:
        src = root / "Misc" / "config.yaml"
        rid = other._journal(other._new_batch("organize", "t"), "move", str(src), str(root / "X" / "config.yaml"),
                             {}, "t")
        other._set_state(rid, "pending")  # ...its operation is journaled, the move not yet done
        me = FileOps(db.connect(), settings, library.scope_policy(conn, settings))
        assert me.recover() == []
        assert conn.execute("SELECT state FROM operations WHERE id=?", (rid,)).fetchone()[0] == "pending"
        me._lock.acquire = lambda wait_s: False  # and a batch of mine would wait, then refuse
        with pytest.raises(OpRefused, match="another window"):
            me.quarantine([file_id(conn, "data.json")], "t")
    finally:
        other._lock.release()
    assert FileOps(conn, settings, library.scope_policy(conn, settings)).recover()  # now it is reconciled


def test_undo_marks_the_batch_and_cannot_run_twice(indexed, settings):
    conn, root, *_ = indexed
    fo = FileOps(conn, settings, library.scope_policy(conn, settings))
    b = fo.move_files([(file_id(conn, "config.yaml"), str(root / "Sorted" / "config.yaml"))], "t")
    assert b.ok
    assert fo.undo_batch(b.batch_id).ok
    assert conn.execute("SELECT status FROM batches WHERE id=?", (b.batch_id,)).fetchone()[0] == "undone"
    with pytest.raises(OpRefused, match="already undone"):
        fo.undo_batch(b.batch_id)
    with pytest.raises(OpRefused, match="no batch"):
        fo.undo_batch(9999)
