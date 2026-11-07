"""Quick search window (launcher style), driven offscreen on a synthetic indexed corpus."""

import ctypes
import os
import sys
from ctypes import wintypes

import pytest

pytest.importorskip("pytestqt")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PySide6.QtCore import Qt  # noqa: E402

from lfi.ui import quick  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture
def win(indexed, settings, qtbot):
    w = quick.QuickSearch(settings, hide_on_blur=False)
    qtbot.addWidget(w)
    opened, revealed, apps = [], [], []
    w.opener, w.revealer, w.app_launcher = opened.append, revealed.append, lambda: apps.append(1)
    w.calls = (opened, revealed, apps)
    w.summon()
    yield w
    w.shutdown()


def _type(qtbot, w, text):
    w.edit.clear()
    qtbot.keyClicks(w.edit, text)
    qtbot.waitUntil(lambda: w.final_seq == w.seq and w.seq > 0, timeout=15000)  # the settled answer


def test_typing_shows_results_and_enter_opens(win, qtbot):
    _type(qtbot, win, "eigenvalues")
    assert win.model.rowCount() >= 1 and win.list.isVisible()
    assert win.model.hits[0].name.startswith("lecture07_eigenvalues")  # the original or one of its copies
    qtbot.keyClick(win.edit, Qt.Key.Key_Return)
    assert win.calls[0] == [win.model.hits[0].path] and not win.isVisible()


def test_keyboard_navigation_reveal_copy_and_escape(win, qtbot):
    _type(qtbot, win, "notes")
    n = win.model.rowCount()
    assert n >= 2
    qtbot.keyClick(win.edit, Qt.Key.Key_Down)
    assert win.list.currentIndex().row() == 1
    qtbot.keyClick(win.edit, Qt.Key.Key_Up)
    qtbot.keyClick(win.edit, Qt.Key.Key_Up)
    assert win.list.currentIndex().row() == n - 1  # wraps around
    win.edit.deselect()
    qtbot.keyClick(win.edit, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    from PySide6.QtGui import QGuiApplication

    assert QGuiApplication.clipboard().text() == win.current().path
    qtbot.keyClick(win.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert win.calls[1] == [win.current().path]
    win.summon()
    qtbot.keyClick(win.edit, Qt.Key.Key_Escape)
    assert not win.isVisible()


def test_tab_switches_scope_and_images_scope_only_returns_images(win, qtbot):
    assert win.scope_label.text() == "All"
    qtbot.keyClick(win.edit, Qt.Key.Key_Tab)
    assert win.scope_label.text() == "Documents"
    qtbot.keyClick(win.edit, Qt.Key.Key_Tab)
    assert win.scope_label.text() == "Images"
    _type(qtbot, win, "beach holiday")
    assert win.model.hits and all(h.kind == "image" for h in win.model.hits)
    assert win.model.hits[0].thumb is not None  # thumbnails decoded off the GUI thread


def test_empty_query_and_no_match(win, qtbot):
    _type(qtbot, win, "zzqxjv")
    assert win.model.rowCount() == 0 and "No matching files" in win.footer.text()
    win.edit.clear()
    assert win.model.rowCount() == 0 and not win.list.isVisible()
    qtbot.keyClick(win.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert win.calls[2] == [1]  # Shift+Enter opens the full app


def test_stale_results_are_ignored(win, qtbot):
    _type(qtbot, win, "eigenvalues")
    first = list(win.model.hits)
    win._on_results(win.seq - 1, [])  # an older query finishing late must not replace results
    assert win.model.hits == first


def test_refined_results_keep_the_chosen_file(win, qtbot):
    _type(qtbot, win, "notes")
    hits = list(win.model.hits)
    assert len(hits) >= 2
    win._on_results(win.seq, hits[::-1])  # not moved yet: the new best match is selected
    assert win.list.currentIndex().row() == 0
    qtbot.keyClick(win.edit, Qt.Key.Key_Down)
    chosen = win.current()
    win._on_results(win.seq, hits)  # the semantic pass reorders them
    assert win.current().path == chosen.path and win.list.currentIndex().row() == hits.index(chosen)


def test_moved_file_is_not_opened(win, qtbot):
    _type(qtbot, win, "eigenvalues")
    os.rename(win.model.hits[0].path, win.model.hits[0].path + ".moved")
    qtbot.keyClick(win.edit, Qt.Key.Key_Return)
    assert win.calls[0] == [] and "no longer there" in win.footer.text()


@pytest.mark.parametrize("text,expected", [("Ctrl+Alt+Space", (0x2 | 0x1, 0x20)), ("alt+shift+f", (0x1 | 0x4, ord("F"))),
                                           ("Win+F12", (0x8, 0x7B)), ("Ctrl+`", (0x2, 0xC0))])
def test_parse_hotkey(text, expected):
    assert quick.parse_hotkey(text) == expected


@pytest.mark.parametrize("bad", ["Space", "Ctrl+Hyper+K", "Ctrl+NotAKey", ""])
def test_parse_hotkey_rejects(bad):
    with pytest.raises(ValueError):
        quick.parse_hotkey(bad)


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 message structure")
def test_hotkey_message_is_recognised(qtbot):
    hk = quick.GlobalHotkey(0, "Ctrl+Alt+Space")
    got = []
    hk.activated.connect(lambda: got.append(1))
    msg = wintypes.MSG()
    msg.message, msg.wParam = quick.WM_HOTKEY, hk.id
    assert hk.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (True, 0)
    msg.wParam = hk.id + 1
    assert hk.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0)
    assert got == [1]


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey")
def test_taken_default_hotkey_falls_back_but_a_chosen_one_does_not(qtbot, monkeypatch):
    taken = {quick.parse_hotkey(quick.DEFAULT_HOTKEY), quick.parse_hotkey("Ctrl+Alt+Space")}
    calls = []

    def fake_register(hwnd, hid, mods, vk):
        calls.append((mods & ~quick.MOD_NOREPEAT, vk))
        return (mods & ~quick.MOD_NOREPEAT, vk) not in taken

    monkeypatch.setattr(ctypes.windll.user32, "RegisterHotKey", fake_register)
    monkeypatch.setattr(ctypes.windll.user32, "UnregisterHotKey", lambda *a: True)
    hk = quick.GlobalHotkey(0, quick.DEFAULT_HOTKEY)
    assert hk.register() and hk.text == quick.FALLBACK_HOTKEYS[0] and "using" in hk.error
    hk.unregister()
    mine = quick.GlobalHotkey(0, "Ctrl+Alt+Space")
    assert not mine.register() and mine.text == "Ctrl+Alt+Space" and "already used" in mine.error


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcut")
def test_sign_in_shortcut_is_created_and_removed(tmp_path):
    d = tmp_path / "Startup"
    d.mkdir()
    assert not quick.startup_enabled(str(d))
    quick.set_startup(True, str(d))
    link = quick.startup_link(str(d))
    assert os.path.getsize(link) > 0
    quick.set_startup(False, str(d))
    assert not quick.startup_enabled(str(d))


def test_settings_page_validates_the_shortcut(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from lfi.ui.core import AppContext
    from lfi.ui.pages.settings import SettingsPage

    monkeypatch.setenv("LFI_STARTUP_DIR", str(tmp_path))  # never the real Startup folder
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    ctx = AppContext()
    page = SettingsPage(ctx)
    qtbot.addWidget(page)
    page.qhotkey.setText("Hyper+Q")
    page.save()
    assert "Nothing was saved" in shown[-1] and ctx.settings.quick_hotkey == quick.DEFAULT_HOTKEY
    page.qhotkey.setText("Ctrl+Alt+K")
    page.save()
    assert ctx.settings.quick_hotkey == "Ctrl+Alt+K" and "Restart quick search" in shown[-1]
