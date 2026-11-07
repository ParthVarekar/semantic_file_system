"""The everyday window, driven offscreen through the real user flows on synthetic files:
first run -> search; clean up + undo; tidy up + undo; restore from Recently Removed; settings."""

import os
import sys

import pytest

pytest.importorskip("pytestqt")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PySide6.QtCore import Qt  # noqa: E402

from lfi import library  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(autouse=True)
def restore_default_look(qapp):
    """The everyday window styles the whole application; in real use the Advanced tools run in
    their own process. Undo the styling so later tests see Qt's defaults."""
    font, palette = qapp.font(), qapp.palette()
    yield
    qapp.setStyleSheet("")
    qapp.setFont(font)
    qapp.setPalette(palette)


@pytest.fixture
def hashing_settings(settings):
    settings.save()  # the window loads settings from the (isolated) app home
    return settings


def _window(qtbot):
    from lfi.ui.core import AppContext
    from lfi.ui.home import theme
    from lfi.ui.home.window import HomeWindow
    from PySide6.QtWidgets import QApplication

    ctx = AppContext()
    theme.apply(QApplication.instance(), "light")
    win = HomeWindow(ctx)
    qtbot.addWidget(win)
    ctx.load_backend()
    win.show()
    return win, ctx


def _settle(qtbot, win, ctx):
    qtbot.waitUntil(lambda: ctx.backend is not None and not ctx.index_running and win.service.state == "idle",
                    timeout=60000)


def test_first_run_then_search(qtbot, corpus, hashing_settings, monkeypatch):
    from lfi.ui.home import welcome

    root, _ = corpus
    monkeypatch.setattr(welcome, "standard_folders", lambda: [("Documents", str(root))])
    monkeypatch.setattr(welcome, "models_missing", lambda s: False)
    real_add = library.add_root
    monkeypatch.setattr(library, "add_root", lambda conn, p, validate=True: real_add(conn, p, validate=False))
    win, ctx = _window(qtbot)
    assert win.root.currentWidget() is win.welcome
    qtbot.mouseClick(win.welcome.go, Qt.MouseButton.LeftButton)
    assert win.root.currentWidget() is win.main
    qtbot.waitUntil(lambda: ctx.conn.execute("SELECT count(*) FROM files").fetchone()[0] > 20, timeout=60000)
    _settle(qtbot, win, ctx)
    page = win.page["search"]
    qtbot.keyClicks(page.field, "eigenvalues")
    qtbot.waitUntil(lambda: bool(page.list.model_.rows) and page.list.model_.rows[0].title.startswith("lecture07"),
                    timeout=15000)
    page.list.setCurrentIndex(page.list.model_.index(0))
    assert page.inspector.isVisible() and page.i_name.text().startswith("lecture07")


def test_clean_up_remove_then_undo(qtbot, indexed, hashing_settings):
    conn, *_ = indexed
    win, ctx = _window(qtbot)
    _settle(qtbot, win, ctx)
    win.go("cleanup")
    page = win.page["cleanup"]
    qtbot.waitUntil(lambda: page.report is not None and not page.busy.isVisible(), timeout=30000)
    page.open_category("exact_duplicates")
    rows = list(page.list.model_.rows)
    assert rows and all(r.checked for r in rows)  # duplicates are pre-selected; the kept copy never appears
    page.remove()
    qtbot.waitUntil(lambda: win.toast.isVisible(), timeout=30000)  # what the user sees once it's done
    assert win.toast.action.text() == "Undo" and len(ctx.fileops(conn).quarantined()) == len(rows)
    assert all(not os.path.exists(r.path) for r in rows)
    win.toast.action.click()
    qtbot.waitUntil(lambda: not ctx.fileops(conn).quarantined(), timeout=30000)
    assert all(os.path.exists(r.path) for r in rows)


def test_tidy_up_then_undo(qtbot, indexed, hashing_settings):
    conn, *_ = indexed
    win, ctx = _window(qtbot)
    _settle(qtbot, win, ctx)
    win.go("organize")
    page = win.page["organize"]
    qtbot.waitUntil(lambda: bool(page.cards) and page.apply_btn.isEnabled(), timeout=60000)
    moves = [(m.src, m.dst) for c in page.cards for m in c.moves]
    qtbot.mouseClick(page.apply_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: win.toast.isVisible() and win.toast.action.isVisible(), timeout=30000)
    assert all(os.path.exists(d) for _, d in moves)
    win.toast.action.click()
    qtbot.waitUntil(lambda: all(os.path.exists(s) for s, _ in moves), timeout=30000)
    assert not any(os.path.exists(d) for _, d in moves)


def test_restore_from_recently_removed(qtbot, indexed, hashing_settings):
    conn, root, m, *_ = indexed
    win, ctx = _window(qtbot)
    _settle(qtbot, win, ctx)
    from conftest import file_id

    p = m["temp"]
    assert ctx.fileops(conn).quarantine([file_id(conn, p.name)], "test").ok
    win.go("removed")
    page = win.page["removed"]
    page.refresh()
    assert len(page.list.model_.rows) == 1 and not page.restore_btn.isEnabled()
    page.list.model_.set_all(True)
    page._update()
    qtbot.mouseClick(page.restore_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: p.exists(), timeout=30000)
    qtbot.waitUntil(lambda: page.stack.currentWidget() is page.empty, timeout=10000)


def test_settings_apply_instantly_and_advanced_opens(qtbot, indexed, hashing_settings, monkeypatch, tmp_path):
    from lfi import config
    from lfi.ui.home import settings as home_settings

    monkeypatch.setenv("LFI_STARTUP_DIR", str(tmp_path / "startup"))
    win, ctx = _window(qtbot)
    _settle(qtbot, win, ctx)
    dlg = home_settings.SettingsDialog(ctx, win)
    qtbot.addWidget(dlg)
    dlg.keep.setChecked(False)
    dlg.appearance.set_value("dark")
    dlg._appearance("dark")
    saved = config.Settings.load()
    assert saved.keep_updated is False and saved.ui_appearance == "dark"
    launched = []
    monkeypatch.setattr(home_settings.subprocess, "Popen", lambda cmd, **k: launched.append(cmd))
    home_settings.launch_advanced()
    assert launched and launched[0][-1] == "--advanced"


def test_advanced_window_still_builds(qtbot, indexed, hashing_settings):
    from lfi.ui.core import AppContext
    from lfi.ui.main_window import MainWindow

    w = MainWindow(AppContext())
    qtbot.addWidget(w)
    assert w.windowTitle()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows icon font")
def test_icons_render(qtbot):
    from lfi.ui.home import theme

    assert not theme.glyph("search", 18).pixmap(18, 18).isNull()


def test_several_folders_can_be_added_at_once_and_protected_ones_are_named(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QListView

    from lfi import db, library
    from lfi.ui import folderpick

    a, b = tmp_path / "Work", tmp_path / "Photos"
    a.mkdir()
    b.mkdir()
    seen = {}

    def accept(tries=[0]):
        dlg = next(w for w in qapp.topLevelWidgets() if isinstance(w, QFileDialog) and w.isVisible())
        view = dlg.findChild(QListView, "listView")
        seen["multi"] = view.selectionMode()
        if os.path.normcase(dlg.directory().absolutePath().replace("/", os.sep)) != os.path.normcase(str(tmp_path)):
            dlg.setDirectory(str(tmp_path))
        rows = [view.model().index(i, 0, view.rootIndex()) for i in range(view.model().rowCount(view.rootIndex()))]
        if len(rows) < 2 and tries[0] < 50:  # the folder list loads in the background
            tries[0] += 1
            QTimer.singleShot(100, accept)
            return
        sel = view.selectionModel()
        for ix in rows:  # what Ctrl+click on each folder does
            sel.select(ix, sel.SelectionFlag.Select | sel.SelectionFlag.Rows)
        dlg.accept()

    QTimer.singleShot(300, accept)
    picked = folderpick.pick_folders(None)
    assert seen["multi"] == QAbstractItemView.SelectionMode.ExtendedSelection
    assert sorted(picked) == sorted([str(a), str(b)])
    conn = db.connect()
    windows = os.environ.get("SystemRoot", r"C:\Windows")
    real_check = folderpick.ScopePolicy.validate_root  # pytest's temp folders live in AppData (protected)
    monkeypatch.setattr(folderpick.ScopePolicy, "validate_root",
                        staticmethod(lambda p: real_check(p) if p == windows else None))
    added, notes = folderpick.add_folders(conn, picked + [windows])
    assert sorted(added) == sorted(picked) and len(library.roots(conn)) == 2
    assert len(notes) == 1 and notes[0].lower().startswith("windows: skipped")


def test_document_previews_are_read_only_and_skip_blank_thumbnails(corpus, tmp_path):
    import io
    import zipfile

    from PIL import Image

    from lfi.ui.home import previews

    root, m = corpus
    pdf = next(root.rglob("*.pdf"))
    before = (pdf.stat().st_size, pdf.stat().st_mtime_ns)
    out = previews.render({"path": str(pdf), "px": 200})
    assert out["ok"] and Image.open(io.BytesIO(out["data"])).size[1] > 50
    assert (pdf.stat().st_size, pdf.stat().st_mtime_ns) == before  # previews never touch the file

    def office(name, img):
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        p = tmp_path / name
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("docProps/thumbnail.jpeg", buf.getvalue())
        return str(p)

    blank = office("blank.docx", Image.new("RGB", (120, 160), "white"))
    page = Image.new("RGB", (120, 160), "white")
    page.paste((20, 20, 20), (10, 10, 110, 30))  # a heading
    assert not previews.render({"path": blank, "px": 200})["ok"]  # a template's empty page: keep the symbol
    assert previews.render({"path": office("real.pptx", page), "px": 200})["ok"]
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4 not really")
    assert not previews.render({"path": str(tmp_path / "broken.pdf"), "px": 200})["ok"]


# ---------------------------------------------------------------------------------- storage ranking
def _sizes(rows):
    return [r.size for r in rows]


def test_every_section_can_be_ranked_by_size(qtbot, indexed, hashing_settings):
    from lfi.ui.home.widgets import Row, RowModel

    conn, root, m, *_ = indexed
    win, ctx = _window(qtbot)
    _settle(qtbot, win, ctx)

    # the model itself: stable ordering, "best" restores arrival order
    model = RowModel()
    model.set_rows([Row(1, "b", size=5, when=1), Row(2, "A", size=9, when=3), Row(3, "c", size=5, when=2)])
    model.sort("largest")
    assert [r.key for r in model.rows] == [2, 1, 3]
    model.sort("smallest")
    assert [r.key for r in model.rows] == [1, 3, 2]
    model.sort("newest")
    assert [r.key for r in model.rows] == [2, 3, 1]
    model.sort("name")
    assert [r.key for r in model.rows] == [2, 1, 3]
    model.sort("best")
    assert [r.key for r in model.rows] == [1, 2, 3]

    # Search with nothing typed: ranked by the database across the whole library, not just the recent files
    page = win.page["search"]
    win.go("search")
    page.sort.set_value("largest")
    page._sorted("largest")
    biggest = conn.execute("SELECT max(size) FROM files WHERE COALESCE(kind,'other') <> 'temporary'").fetchone()[0]
    qtbot.waitUntil(lambda: len(page.list.model_.rows) > 5 and page.list.model_.rows[0].size == biggest, timeout=15000)
    sizes = _sizes(page.list.model_.rows)
    assert sizes == sorted(sizes, reverse=True)
    # with a query: the matches are re-ordered on screen
    page.sort.set_value("best")
    qtbot.keyClicks(page.field, "eigenvalues")
    qtbot.waitUntil(lambda: len(page.list.model_.rows) >= 2 and page.section.isHidden(), timeout=15000)
    page.sort.set_value("largest")
    page._sorted("largest")
    sizes = _sizes(page.list.model_.rows)
    assert sizes == sorted(sizes, reverse=True)

    # Clean Up: a category lists its biggest first, and the categories are ranked by what they would free
    win.go("cleanup")
    cp = win.page["cleanup"]
    qtbot.waitUntil(lambda: cp.report is not None and not cp.busy.isVisible(), timeout=30000)
    cp.open_category("exact_duplicates")
    sizes = _sizes(cp.list.model_.rows)
    assert len(sizes) > 1 and sizes == sorted(sizes, reverse=True)
    freed = {k: (cp.report.reclaimable(k) if cp.report.baskets.get(k) else 0) for k in cp.cards}
    in_grid = [cp.grid.itemAt(i).widget().key for i in range(cp.grid.count())]
    assert [freed[k] for k in in_grid] == sorted(freed.values(), reverse=True)

    # Organize: folder cards can be ordered by their total size, with the size on the card
    win.go("organize")
    op = win.page["organize"]
    qtbot.waitUntil(lambda: len(op.cards) > 2 and op.apply_btn.isEnabled(), timeout=60000)
    assert all(c.size > 0 for c in op.cards)
    op.sort.set_value("largest")
    op._reorder()
    assert [c.size for c in op.cards] == sorted((c.size for c in op.cards), reverse=True)
    assert [op.list_lay.itemAt(i).widget() for i in range(len(op.cards))] == op.cards

    # Recently Removed: biggest first
    from conftest import file_id

    for name in ("temp", "empty"):
        assert ctx.fileops(conn).quarantine([file_id(conn, m[name].name)], "test").ok
    win.go("removed")
    rp = win.page["removed"]
    rp.refresh()
    assert len(rp.list.model_.rows) >= 2
    rp.sort.set_value("largest")
    rp.list.model_.sort("largest")
    assert _sizes(rp.list.model_.rows) == sorted(_sizes(rp.list.model_.rows), reverse=True)
    assert rp.list.model_.rows[0].size > 0


def test_remove_a_big_file_from_search_and_undo(qtbot, indexed, hashing_settings):
    conn, root, m, *_ = indexed
    win, ctx = _window(qtbot)
    _settle(qtbot, win, ctx)
    page = win.page["search"]
    win.go("search")
    qtbot.keyClicks(page.field, "eigenvalues")
    qtbot.waitUntil(lambda: any("Copy" in r.title for r in page.list.model_.rows), timeout=15000)
    page.sort.set_value("largest")
    page._sorted("largest")
    rows = page.list.model_.rows
    target = next(r for r in rows if "Copy" in r.title)
    page.list.setCurrentIndex(page.list.model_.index(rows.index(target)))
    n = len(rows)
    page.remove_current()
    qtbot.waitUntil(lambda: win.toast.isVisible() and win.toast.action.text() == "Undo", timeout=30000)
    assert not os.path.exists(target.path) and len(ctx.fileops(conn).quarantined()) == 1  # moved, not deleted
    assert len(page.list.model_.rows) == n - 1 and not page.inspector.isVisible()
    win.toast.action.click()
    qtbot.waitUntil(lambda: os.path.exists(target.path), timeout=30000)
    assert not ctx.fileops(conn).quarantined()


# ---------------------------------------------------------------------------------- apps
def _fake_apps(tmp_path):
    from lfi import apps

    def folder(name, size):
        d = tmp_path / name
        d.mkdir()
        (d / "data.bin").write_bytes(b"x" * size)
        return str(d)

    return [
        apps.App("machine:a", "Alpha Studio", "Acme", "1.0", "2025-01-02", size=41 * 2**30,
                 location=folder("Alpha", 3000), uninstall='"C:\\Alpha\\unins.exe" /S'),  # records a nonsense size
        apps.App("machine:b", "Beta Tool", "Beta Ltd", "2", "2024-05-06", size=2 * 2**20,
                 location=folder("Beta", 9000), uninstall="MsiExec.exe /X{1}"),
        apps.App("user:c", "Gamma", "", "", "", size=0, uninstall="x"),
        apps.App("machine:d", "Microsoft Visual C++ Redistributable", "Microsoft", "14", "2023-01-01",
                 size=10 * 2**20, uninstall="y", component=True),
    ]


def test_apps_page_ranks_by_size_measures_and_asks_before_uninstalling(qtbot, tmp_path, hashing_settings, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from lfi import apps
    from lfi.ui.core import AppContext
    from lfi.ui.home import theme
    from lfi.ui.home.apps import AppsPage
    from lfi.ui.home.widgets import Toast

    theme.apply(QApplication.instance(), "dark")
    fake = _fake_apps(tmp_path)
    monkeypatch.setattr(apps, "list_apps", lambda include_store=True: list(fake))
    page = AppsPage(AppContext(), None)
    page.toast = Toast(page)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(lambda: bool(page.list.model_.rows), timeout=10000)
    assert [r.title for r in page.list.model_.rows] == ["Alpha Studio", "Beta Tool", "Gamma"]  # runtimes hidden
    assert page.list.model_.rows[-1].right == "Unknown size"
    page.components.setChecked(True)
    assert "Microsoft Visual C++ Redistributable" in [r.title for r in page.list.model_.rows]
    assert [r.title for r in page.list.model_.rows if r.badge] == ["Microsoft Visual C++ Redistributable"]
    page.components.setChecked(False)
    page.filter.setText("beta")
    assert [r.title for r in page.list.model_.rows] == ["Beta Tool"]
    page.filter.clear()

    # measuring replaces the recorded 41 GB with what is really on disk, and the ranking follows
    assert page.measure_btn.text() == "Measure sizes"  # Gamma says nowhere it lives: nothing to measure
    page.measure()
    qtbot.waitUntil(lambda: page.measure_btn.text() == "Measure sizes" and page.list.model_.rows[0].title == "Beta Tool",
                    timeout=20000)
    assert [(r.title, r.size) for r in page.list.model_.rows[:2]] == [("Beta Tool", 9000), ("Alpha Studio", 3000)]

    # Uninstall asks first (Cancel is the default) and then starts only that app's own uninstaller
    launched = []
    monkeypatch.setattr(apps, "launch_uninstall", lambda a: launched.append(a.name) or None)
    page.list.setCurrentIndex(page.list.model_.index(0))
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda parent, title, text, buttons, default: asked.append((text, default)) or QMessageBox.StandardButton.Cancel))
    page.uninstall()
    assert not launched and "Beta Tool" in asked[0][0] and asked[0][1] == QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a: QMessageBox.StandardButton.Yes))
    page.uninstall()
    assert launched == ["Beta Tool"] and page._timer.isActive()
    # the list drops the app once its registry entry is gone
    monkeypatch.setattr(apps, "still_installed", lambda a: False)
    page._watch_tick()
    assert "Beta Tool" not in [r.title for r in page.list.model_.rows] and not page._timer.isActive()
    assert page.toast.text.text() == "Beta Tool was uninstalled"


def test_keyboard_shortcuts_and_the_five_places(qtbot, indexed, hashing_settings):
    from PySide6.QtGui import QShortcut

    win, ctx = _window(qtbot)
    _settle(qtbot, win, ctx)
    keys = {sc.key().toString(): sc for sc in win.findChildren(QShortcut)}
    for i, place in enumerate(("search", "cleanup", "organize", "apps", "removed"), 1):
        keys[f"Ctrl+{i}"].activated.emit()
        assert win.pages.currentWidget() is win.page[place]
    assert win.rail.items[3][1] == "Apps  (Ctrl+4)"
    win.go("cleanup")
    keys["Ctrl+F"].activated.emit()
    assert win.pages.currentWidget() is win.page["search"]
    win.go("removed")
    keys["Ctrl+K"].activated.emit()
    assert win.pages.currentWidget() is win.page["search"]
    page = win.page["search"]  # Esc clears the typed words first
    page.field.setText("abc")
    qtbot.keyClick(page.field, Qt.Key.Key_Escape)
    assert page.field.text() == ""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcuts")
def test_desktop_shortcuts_have_the_icon_the_right_target_and_folder(isolated_home, tmp_path):
    import subprocess

    from lfi import shortcuts

    made = shortcuts.create(str(tmp_path))
    assert sorted(os.path.basename(p) for p in made) == ["LFI Advanced.lnk", "LFI Quick Search.lnk",
                                                         "Local File Intelligence.lnk"]
    script = (f"$w=New-Object -ComObject WScript.Shell; foreach($f in Get-ChildItem '{tmp_path}' -Filter *.lnk)"
              "{ $l=$w.CreateShortcut($f.FullName); ('{0}|{1}|{2}|{3}|{4}' -f $f.BaseName,$l.TargetPath,"
              "$l.Arguments,$l.IconLocation,$l.WorkingDirectory) }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                         timeout=60).stdout.strip().splitlines()
    rows = {line.split("|")[0]: line.split("|")[1:] for line in out}
    assert rows["Local File Intelligence"][1] == "-m lfi.ui.app"
    assert rows["LFI Advanced"][1] == "-m lfi.ui.app --advanced"
    assert rows["LFI Quick Search"][1] == "-m lfi.ui.quick"
    for target, _args, icon, cwd in rows.values():
        assert os.path.isfile(target) and os.path.basename(target).lower() in ("pythonw.exe", "python.exe")
        assert icon.endswith("lfi.ico,0") and os.path.isdir(os.path.join(cwd, "src", "lfi"))
    ico = open(icon[:-2], "rb").read()
    assert ico[:4] == b"\x00\x00\x01\x00" and int.from_bytes(ico[4:6], "little") == len(shortcuts.ICON_SIZES)
