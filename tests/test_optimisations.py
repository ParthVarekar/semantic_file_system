"""Doing less work without losing anything: tool-generated folders skipped, video frame dumps
sampled for the image model (camera folders untouched), tiny pictures skipped, log kept small."""

import json
import os
import time

import pytest
from conftest import index
from PIL import Image
from test_images import FakeVision

from lfi import config, db, library
from lfi.config import EXCLUDE_ADDITIONS, Settings
from lfi.indexer import FRAMES_KEPT, not_worth_understanding


@pytest.fixture
def fake_vision(monkeypatch):
    import lfi.vision as vision
    from lfi.indexer import Indexer

    monkeypatch.setattr(vision, "pair_is_cached", lambda key: True)
    monkeypatch.setattr(vision, "VisionBackend", FakeVision)
    monkeypatch.setattr(Indexer, "VISION_TASK", "helpers:fake_vision")


def test_new_default_exclusions_reach_old_settings_once(isolated_home):
    isolated_home.mkdir(parents=True, exist_ok=True)
    old = [g for g in config.DEFAULT_EXCLUDES[:14] if g != "venv"]  # an old file; the user had removed "venv"
    config.settings_path().write_text(json.dumps({"exclude_globs": old}), encoding="utf-8")
    s = Settings.load()
    assert ".venv*" in s.exclude_globs and ".next" in s.exclude_globs
    assert "venv" not in s.exclude_globs  # a default the user removed is not brought back
    s.exclude_globs.remove(".next")  # the user removes a new default...
    s.save()
    assert ".next" not in Settings.load().exclude_globs  # ...and it stays removed
    assert Settings.load().exclude_defaults_rev == max(EXCLUDE_ADDITIONS)


def test_tool_folders_and_project_build_output_are_skipped(tmp_path, settings):
    root = tmp_path / "root"
    for rel in ("app/package.json", "app/src/main.js", "app/dist/bundle.js", "app/.next/cache.js",
                ".venv-gpu/lib/site.py", "Notes/build/my plan.txt", "Notes/out/letter.txt"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    index(settings, embed=False)
    names = {r[0] for r in conn.execute("SELECT name FROM files")}
    assert {"package.json", "main.js", "my plan.txt", "letter.txt"} <= names  # people's "build"/"out" kept
    assert not names & {"bundle.js", "cache.js", "site.py"}


def _rows(n, w=1280, h=720, spread_s=60.0, folder="f"):
    t0 = time.time_ns()
    return [{"id": i, "name": f"frame_{i:05d}.jpg", "parent_key": folder, "width": w, "height": h,
             "mtime_ns": t0 + int(i * spread_s / n * 1e9)} for i in range(n)]


def test_frame_dumps_are_sampled_and_camera_folders_are_not():
    frames = _rows(1200, spread_s=90)  # extracted by a video tool in 90 s
    skip = not_worth_understanding(frames)
    assert len(frames) - len(skip) in (FRAMES_KEPT, FRAMES_KEPT + 1)
    camera = _rows(1200, spread_s=3 * 86400, folder="DCIM")  # photos over three days
    assert not not_worth_understanding(camera)
    mixed_sizes = [dict(r, width=800 + i % 7 * 40) for i, r in enumerate(_rows(400))]
    assert not not_worth_understanding(mixed_sizes)  # different sizes: not a single recording
    assert not not_worth_understanding(_rows(120))  # too few to call it a dump
    icons = [dict(r, width=32, height=32) for r in _rows(5, folder="icons")]
    assert not_worth_understanding(icons) == {0, 1, 2, 3, 4}


def test_skipped_images_stay_findable_by_name(tmp_path, settings, fake_vision):
    from lfi.search import SearchEngine

    root = tmp_path / "root"
    rec = root / "recording"
    rec.mkdir(parents=True)
    t = time.time()
    for i in range(240):
        p = rec / f"dashboard_tour_{i:04d}.jpg"
        Image.new("RGB", (160, 120), (i % 200, 80, 120)).save(p, "JPEG")
        os.utime(p, (t + i * 0.1, t + i * 0.1))
    Image.new("RGB", (24, 24), (200, 30, 30)).save(root / "icon.png")
    conn = db.connect()
    library.add_root(conn, str(root), validate=False)
    _, st = index(settings)
    assert st.images_skipped >= 240 - FRAMES_KEPT - 1 + 1
    assert conn.execute("SELECT count(*) FROM files WHERE img_stage=3").fetchone()[0] == st.images_skipped
    assert any(h.name == "icon.png" for h in SearchEngine(conn).search("icon"))
    _, st2 = index(settings)
    assert st2.images_skipped == 0 and st2.images_embedded == 0  # not looked at again every run
    wal = config.db_path().with_name("index.sqlite3-wal")
    assert not wal.exists() or wal.stat().st_size < 8 * 1024 * 1024  # the log was folded back in
