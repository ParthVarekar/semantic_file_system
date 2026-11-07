"""GUI responsiveness and cancellation (offscreen Qt). Run: pytest tests/test_gui.py"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"))

import pytest  # noqa: E402

pytest.importorskip("pytestqt")


def make_many(root, n):
    for i in range(n):
        d = root / f"batch_{i // 200:03d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"note_{i:05d}.txt").write_text(f"Note {i}: " + "lorem ipsum dolor sit amet " * 40)


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    from lfi.config import Settings
    from lfi.embeddings import HashingBackend
    from lfi.ui.core import AppContext
    from lfi.ui.main_window import MainWindow

    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, path=None: settings))
    ctx = AppContext()
    ctx.backend = HashingBackend()
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.show()
    return ctx, win


def test_ui_stays_responsive_and_cancel_works(qtbot, window, tmp_path):
    from PySide6.QtCore import QTimer

    from lfi import library

    ctx, win = window
    root = tmp_path / "many"
    make_many(root, 2500)
    library.add_root(ctx.conn, str(root), validate=False)
    win.go("folders")
    fp = win.pages["folders"]
    gaps, last = [], [time.perf_counter()]

    def tick():
        now = time.perf_counter()
        gaps.append(now - last[0])
        last[0] = now

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(20)
    fp.start_index()
    assert ctx.index_running
    qtbot.waitUntil(lambda: ctx.conn.execute("SELECT count(*) FROM files WHERE stage>=1").fetchone()[0] > 50,
                    timeout=60000)
    t0 = time.perf_counter()
    fp.cancel_index()
    qtbot.waitUntil(lambda: not ctx.index_running, timeout=30000)
    cancel_s = time.perf_counter() - t0
    timer.stop()
    assert cancel_s < 15, cancel_s
    assert max(gaps) < 0.5, f"UI thread blocked for {max(gaps):.2f}s"
    assert "Cancelled" in fp.log.toPlainText()
    # resume to completion
    fp.start_index()
    qtbot.waitUntil(lambda: not ctx.index_running, timeout=180000)
    assert ctx.conn.execute("SELECT count(*) FROM files WHERE stage=2").fetchone()[0] == 2500
    win.go("dashboard")
    win.pages["dashboard"].refresh()
    assert win.pages["dashboard"].c_files.v.text() == "2,500"
