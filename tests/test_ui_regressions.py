"""Regression tests for UI defects found in review (offscreen Qt, synthetic files, private LFI_HOME).

Run: pytest tests/test_ui_regressions.py
"""

import importlib
import os
import shutil
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"))

import pytest  # noqa: E402

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402

from conftest import index  # noqa: E402

UR = Qt.ItemDataRole.UserRole


def make_many(root, n):
    for i in range(n):
        d = root / f"batch_{i // 200:03d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"note_{i:05d}.txt").write_text(f"Note {i}: " + "lorem ipsum dolor sit amet " * 40)


@pytest.fixture
def calls(monkeypatch, tmp_path):
    """No desktop side effects; every dialog is answered Yes and recorded."""
    import send2trash
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QMessageBox

    import lfi.ui.core as core

    rec = types.SimpleNamespace(msgs=[], confirms=[], trashed=[], opened=[])
    bin_dir = tmp_path / "fake_recycle_bin"
    bin_dir.mkdir()

    def fake_trash(p):
        rec.trashed.append(p)
        src = str(p).replace("\\\\?\\", "")
        shutil.move(src, bin_dir / f"{len(rec.trashed)}_{os.path.basename(src)}")

    monkeypatch.setattr(send2trash, "send2trash", fake_trash)
    if hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", lambda *a, **k: rec.opened.append(a))
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: rec.opened.append(url) or True))
    monkeypatch.setattr(core, "subprocess", types.SimpleNamespace(Popen=lambda *a, **k: rec.opened.append(a)))

    def answer(kind):
        def f(parent, title, text, *a, **k):
            rec.msgs.append((kind, title, text))
            return QMessageBox.StandardButton.Yes
        return staticmethod(f)

    for kind in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, kind, answer(kind))

    def fake_confirm(parent, title, text, detail=""):
        rec.confirms.append((title, text))
        return True

    for mod in ("lfi.ui.core", "lfi.ui.pages.duplicates", "lfi.ui.pages.cleanup", "lfi.ui.pages.quarantine",
                "lfi.ui.pages.activity", "lfi.ui.pages.organize", "lfi.ui.pages.settings"):
        m = importlib.import_module(mod)
        if hasattr(m, "confirm"):
            monkeypatch.setattr(m, "confirm", fake_confirm)
    return rec


@pytest.fixture
def window(qtbot, settings, monkeypatch, calls):
    from lfi.config import Settings
    from lfi.embeddings import HashingBackend
    from lfi.ui.core import AppContext
    from lfi.ui.main_window import MainWindow

    settings.image_search = False
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, path=None: settings))
    ctx = AppContext()
    ctx.backend = HashingBackend()
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.show()
    yield ctx, win
    win.pages["folders"].shutdown()
    ctx.pool.waitForDone(60000)
    qtbot.wait(20)  # deliver the workers' last queued signals


@pytest.fixture
def indexed_win(window, corpus, settings):
    from lfi import library

    ctx, win = window
    root, _ = corpus
    library.add_root(ctx.conn, str(root), validate=False)
    ix, _ = index(settings)
    ix.conn.close()
    return ctx, win, root


def analyzed_cleanup(qtbot, win):
    cp = win.pages["cleanup"]
    cp.run()
    qtbot.waitUntil(lambda: cp.report is not None and cp.analyze_btn.isEnabled(), timeout=60000)
    return cp


def found_duplicates(qtbot, win):
    dp = win.pages["duplicates"]
    dp.run()
    qtbot.waitUntil(lambda: dp.scan.isEnabled() and "exact groups" in dp.status.text(), timeout=60000)
    return dp


def applied_organize(qtbot, ctx, win):
    op = win.pages["organize"]
    op.run()
    qtbot.waitUntil(lambda: op.plan is not None and op.gen.isEnabled(), timeout=60000)
    if not op.plan.selected():
        for m in op.plan.moves[:3]:
            m.selected = True
    assert op.plan.selected()
    return op


def start_big_index(qtbot, ctx, win, tmp_path, n=2500):
    from lfi import library

    root = tmp_path / "many"
    make_many(root, n)
    library.add_root(ctx.conn, str(root), validate=False)
    fp = win.pages["folders"]
    fp.refresh()
    fp.start_index()
    qtbot.waitUntil(lambda: ctx.conn.execute("SELECT count(*) FROM files").fetchone()[0] > 0, timeout=60000)
    return fp


# ---- 1: tables keep the engine's order --------------------------------------------------------
def test_tables_keep_the_engine_order(qtbot, indexed_win):
    ctx, win, root = indexed_win
    sp = win.pages["search"]
    sp.q.setText("eigenvalues")
    sp.run()
    names = [sp.table.item(r, 0).text() for r in range(sp.table.rowCount())]
    assert len(names) >= 3
    assert names == [h.name for h in sp.hits], "rows are not in rank order"
    assert sp._cur() is sp.hits[0], "the best match is not the selected/previewed row"
    sp.table.sortItems(0, Qt.SortOrder.AscendingOrder)  # the Name header sorts by name
    names = [sp.table.item(r, 0).text() for r in range(sp.table.rowCount())]
    assert names == sorted(names, key=str.lower)

    cp = analyzed_cleanup(qtbot, win)
    for i in range(cp.baskets.count()):
        cp.baskets.setCurrentRow(i)
        assert [cp.table.item(r, 2).text() for r in range(cp.table.rowCount())] == [c.name for c in cp.cands]

    ids = [r[0] for r in ctx.conn.execute("SELECT id FROM files WHERE kind='document' ORDER BY id LIMIT 4")]
    ctx.fileops(ctx.conn).quarantine(ids, "test", "manual")
    qp = win.pages["quarantine"]
    qp.refresh()
    assert qp.table.rowCount() >= 2
    shown = [qp.rows[qp.table.item(r, 0).data(UR)]["id"] for r in range(qp.table.rowCount())]
    assert shown == [r["id"] for r in qp.rows]  # newest first


# ---- 2: one file operation at a time ------------------------------------------------------------
def test_file_operation_buttons_lock_while_a_batch_runs(qtbot, indexed_win, calls):
    from lfi.fileops import OpRefused

    ctx, win, root = indexed_win
    op = applied_organize(qtbot, ctx, win)
    n = len(calls.msgs)
    op.apply()
    assert ctx.fileop_running
    assert "Moving" in op.status.text()
    for b in (op.apply_btn, op.undo_btn, win.pages["duplicates"].quarantine_btn, win.pages["cleanup"].quarantine_btn,
              win.pages["activity"].undo_btn, *win.pages["quarantine"].op_buttons):
        assert not b.isEnabled(), b.text()
    op.apply()  # second click while the first batch runs
    qtbot.waitUntil(lambda: not ctx.fileop_running, timeout=60000)
    assert ctx.conn.execute("SELECT count(*) FROM batches WHERE kind='organize'").fetchone()[0] == 1
    assert any("still running" in m[2] for m in calls.msgs[n:])
    assert op.apply_btn.isEnabled() and op.undo_btn.isEnabled()
    assert all(b.isEnabled() for b in win.pages["quarantine"].op_buttons)

    def refuse(fo):
        raise OpRefused("another window or command is moving files right now; try again when it has finished")

    ctx.run_fileop(win, "Test", "Testing...", refuse, lambda res: None)
    qtbot.waitUntil(lambda: not ctx.fileop_running, timeout=10000)
    assert calls.msgs[-1][0] == "information" and calls.msgs[-1][2].startswith("Nothing was done: another window")
    assert not [m for m in calls.msgs if m[0] == "critical"]


def test_restore_pressed_twice_runs_once(qtbot, indexed_win, calls):
    ctx, win, root = indexed_win
    ids = [r[0] for r in ctx.conn.execute("SELECT id FROM files WHERE kind='document' ORDER BY id LIMIT 4")]
    ctx.fileops(ctx.conn).quarantine(ids, "test", "manual")
    qp = win.pages["quarantine"]
    qp.refresh()
    qp.check_all()
    qp.restore()
    assert "Restoring" in qp.summary.text()
    qp.restore()
    qtbot.waitUntil(lambda: not ctx.fileop_running, timeout=60000)
    assert ctx.conn.execute("SELECT count(*) FROM batches WHERE kind='restore'").fetchone()[0] <= 1
    assert not [p for p in root.rglob("*") if "(restored)" in p.name]


# ---- 3: changing the keeper keeps the user's check marks --------------------------------------
def test_make_keeper_keeps_the_users_check_marks(qtbot, indexed_win):
    ctx, win, root = indexed_win
    dp = found_duplicates(qtbot, win)
    tree = dp.trees["exact"]
    assert tree.topLevelItemCount() >= 2
    g0, g1 = tree.topLevelItem(0), tree.topLevelItem(1)
    c0 = next(g0.child(j) for j in range(g0.childCount()) if g0.child(j).checkState(0) == Qt.CheckState.Checked)
    c0.setCheckState(0, Qt.CheckState.Unchecked)  # the user decides to keep this copy too
    gi1 = g1.data(0, UR)[0]
    old_keeper = tree.groups[gi1].keeper_id
    in_g1 = {f.id for f in tree.groups[gi1].files}
    before = [p for p in tree.checked() if p[0] not in in_g1]
    c1 = next(g1.child(j) for j in range(g1.childCount()) if g1.child(j).data(0, UR)[1] != old_keeper)
    new_keeper = c1.data(0, UR)[1]
    tree.setCurrentItem(c1)
    dp.make_keeper()
    assert [p for p in tree.checked() if p[0] not in in_g1] == before, "check marks in other groups changed"
    assert tree.groups[gi1].keeper_id == new_keeper
    g1 = tree.topLevelItem(1)
    items = {g1.child(j).data(0, UR)[1]: g1.child(j) for j in range(g1.childCount())}
    assert not items[new_keeper].flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert items[old_keeper].checkState(0) == Qt.CheckState.Unchecked
    assert items[old_keeper].flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert all(fid != old_keeper for fid, _ in tree.checked())


# ---- 4: "Not junk" acts on the selected row -----------------------------------------------------
def test_not_junk_acts_on_the_selected_row_and_the_note_stays(qtbot, indexed_win):
    ctx, win, root = indexed_win
    cp = analyzed_cleanup(qtbot, win)
    keys = [cp.baskets.item(i).data(UR) for i in range(cp.baskets.count())]
    cp.baskets.setCurrentRow(keys.index("exact_duplicates"))
    assert len(cp._checked()) >= 2  # pre-checked rows that must not be affected
    cp.table.selectRow(cp.table.rowCount() - 1)
    cur = cp._cur()
    cp.feedback(True)  # "Never suggest again"
    never = [r[0] for r in ctx.conn.execute("SELECT path_key FROM prefs WHERE never_suggest=1")]
    assert len(never) == 1 and os.path.basename(never[0]).lower() == cur.name.lower()
    qtbot.waitUntil(lambda: cp.report is not None and cp.analyze_btn.isEnabled(), timeout=60000)
    assert "Noted: 1 file" in cp.summary.text(), "the confirmation was overwritten by the re-analysis"


# ---- 5: destructive index actions wait for indexing --------------------------------------------
def test_index_changing_actions_wait_for_indexing(qtbot, window, tmp_path, calls):
    from lfi import library

    ctx, win = window
    fp = start_big_index(qtbot, ctx, win, tmp_path)
    st = win.pages["settings"]
    assert not any(b.isEnabled() for b in st.index_buttons) and not fp.remove_btn.isEnabled()
    n = len(calls.msgs)
    st.clear_index(False)
    st.clear_index(True)
    fp.roots.setCurrentRow(0)
    fp.remove_root()
    assert len(calls.msgs) == n + 3 and all("Stop indexing first" in m[2] for m in calls.msgs[n:])
    assert library.roots(ctx.conn) and ctx.conn.execute("SELECT count(*) FROM files").fetchone()[0] > 0
    fp.cancel_index()
    qtbot.waitUntil(lambda: not ctx.index_running, timeout=60000)
    assert "Indexing failed" not in fp.log.toPlainText()
    assert all(b.isEnabled() for b in st.index_buttons) and fp.remove_btn.isEnabled()


# ---- 6: closing during indexing ----------------------------------------------------------------
def test_closing_during_indexing_cancels_then_closes(qtbot, window, tmp_path):
    ctx, win = window
    start_big_index(qtbot, ctx, win, tmp_path)
    t0 = time.perf_counter()
    win.close()
    assert time.perf_counter() - t0 < 1.0, "closing blocked the GUI thread"
    assert win.isVisible() and "Stopping indexing" in win.status.text()
    qtbot.waitUntil(lambda: not win.isVisible(), timeout=60000)
    assert not ctx.index_running


# ---- 7: results go stale after changes elsewhere -----------------------------------------------
def test_cleanup_and_duplicates_go_stale_after_changes_elsewhere(qtbot, indexed_win, calls):
    ctx, win, root = indexed_win
    cp = analyzed_cleanup(qtbot, win)
    dp = found_duplicates(qtbot, win)
    assert cp.table.rowCount() > 0 and dp.trees["exact"].topLevelItemCount() > 0
    fid = dp.trees["exact"].checked()[0][0]
    ctx.fileops(ctx.conn).quarantine([fid], "test", "manual")  # e.g. done on another page
    ctx.data_changed.emit("quarantine")
    assert cp.report is None and cp.table.rowCount() == 0 and "Analyze again" in cp.summary.text()
    assert not cp.quarantine_btn.isEnabled()
    assert dp.trees["exact"].topLevelItemCount() == 0 and "again" in dp.status.text()
    assert not dp.quarantine_btn.isEnabled() and not dp.keeper_btn.isEnabled()
    n = len(calls.msgs)
    cp.quarantine()
    assert not [m for m in calls.msgs[n:] if "#" in m[2]]
    cp = analyzed_cleanup(qtbot, win)
    assert cp.quarantine_btn.isEnabled()
    ctx.data_changed.emit("settings")  # settings alone do not invalidate results
    assert cp.report is not None


# ---- 8: the window fits small screens ----------------------------------------------------------
def test_window_fits_900x500(qtbot, indexed_win):
    from lfi.ui.main_window import NAV

    ctx, win, root = indexed_win
    hint = win.minimumSizeHint()
    assert hint.width() <= 900 and hint.height() <= 500, hint
    win.resize(900, 500)
    for key, _ in NAV:
        win.go(key)
        qtbot.wait(10)
        assert win.stack.currentWidget().horizontalScrollBar().maximum() == 0, key


# ---- 9: search notes and related-files mode ----------------------------------------------------
def test_search_notes_reset_and_related_mode_is_page_state(qtbot, indexed_win):
    ctx, win, root = indexed_win
    sp = win.pages["search"]
    sp.q.setText("eigenvalues after:someday")
    sp.run()
    assert sp.mode.text().startswith("Ignored:")
    sp.q.setText("eigenvalues")
    sp.run()
    assert "Ignored" not in sp.mode.text()
    sp.related()
    assert sp.q.text() == "eigenvalues" and sp.related_to
    sp.scope.setCurrentIndex(1)  # Documents: re-runs the user's query, not a pseudo-query
    assert sp.table.rowCount() > 0 and sp.related_to is None


# ---- 10-12: undo state, folder counts, rebuild -------------------------------------------------
def test_undo_buttons_follow_batches_undone_elsewhere(qtbot, indexed_win, calls):
    ctx, win, root = indexed_win
    op = applied_organize(qtbot, ctx, win)
    op.apply()
    qtbot.waitUntil(lambda: not ctx.fileop_running, timeout=60000)
    bid = op.last_batch
    assert op.undo_btn.isEnabled()
    ap = win.pages["activity"]
    win.go("activity")

    def select():
        ap.bt.selectRow(next(r for r in range(ap.bt.rowCount()) if ap.bt.item(r, 0).data(UR) == bid))

    select()
    assert ap.undo_btn.isEnabled()
    ap.undo()
    qtbot.waitUntil(lambda: not ctx.fileop_running, timeout=60000)
    assert ctx.conn.execute("SELECT status FROM batches WHERE id=?", (bid,)).fetchone()[0] == "undone"
    assert not op.undo_btn.isEnabled()
    select()
    assert not ap.undo_btn.isEnabled()


def test_folder_counts_follow_clear_index(qtbot, indexed_win):
    ctx, win, root = indexed_win
    fp = win.pages["folders"]
    fp.refresh()
    assert "(0 files" not in fp.roots.item(0).text()
    win.pages["settings"].clear_index(False)
    assert "(0 files" in fp.roots.item(0).text()


def test_rebuild_clears_and_reindexes(qtbot, indexed_win):
    ctx, win, root = indexed_win
    n = ctx.conn.execute("SELECT count(*) FROM files").fetchone()[0]
    win.pages["settings"].clear_index(True)
    assert ctx.index_running
    qtbot.waitUntil(lambda: not ctx.index_running, timeout=120000)
    assert ctx.conn.execute("SELECT count(*) FROM files").fetchone()[0] == n
