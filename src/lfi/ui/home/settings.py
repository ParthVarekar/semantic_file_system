"""Settings sheet. Every change applies at once (no Save button), as in macOS System Settings."""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLineEdit, QMessageBox, QScrollArea, QVBoxLayout,
                               QWidget)

from ... import config, library
from .. import quick
from ..core import AppContext, open_file
from . import theme
from .widgets import FileList, Row, Segmented, Switch, button, card, label


def _setting_row(title: str, caption: str, control: QWidget) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 4, 0, 4)
    col = QVBoxLayout()
    col.setSpacing(0)
    col.addWidget(label(title, ""))
    if caption:
        col.addWidget(label(caption, "caption", wrap=True))
    h.addLayout(col, 1)
    h.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return w


def launch_advanced() -> None:
    """The detailed tools run as their own window and process (their own look and controls)."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--advanced"]
    else:
        w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        cmd = [w if os.path.exists(w) else sys.executable, "-m", "lfi.ui.app", "--advanced"]
    subprocess.Popen(cmd, close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


class SettingsDialog(QDialog):
    appearance_changed = Signal(str)
    folders_changed = Signal()
    watching_changed = Signal(bool)
    update_now = Signal()

    def __init__(self, ctx: AppContext, parent=None, focus: str = ""):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Settings")
        self.resize(600, 700)
        s = ctx.settings
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(14)
        lay.addWidget(label("Settings", "largeTitle"))

        # folders
        fc, fl = card(spacing=6)
        fl.addWidget(label("Folders", "headline"))
        fl.addWidget(label("Only these folders are read. System folders are always left alone.", "caption"))
        self.folders = FileList()
        self.folders.setMinimumHeight(150)
        fl.addWidget(self.folders)
        self.folder_msg = label("", "caption", wrap=True)
        self.folder_msg.hide()
        fl.addWidget(self.folder_msg)
        row = QHBoxLayout()
        row.addWidget(button("Add Folder...", "plain", "add", self.add_folder))
        self.remove_btn = button("Remove", "plain", "remove", self.remove_folder)
        row.addWidget(self.remove_btn)
        row.addStretch(1)
        row.addWidget(button("Update Now", "secondary", "sync", self.update_now.emit))
        fl.addLayout(row)
        self.keep = Switch(s.keep_updated)
        self.keep.toggled.connect(self._keep)
        fl.addWidget(_setting_row("Keep up to date automatically",
                                  "Notices new and changed files in these folders and updates the index by itself.",
                                  self.keep))
        lay.addWidget(fc)

        # smart search models
        mc, ml = card(spacing=6)
        ml.addWidget(label("Smart search", "headline"))
        self.model_state = label("", "caption", wrap=True)
        ml.addWidget(self.model_state)
        self.dl_btn = button("Download (about 300 MB)...", "secondary", "download", self.download)
        ml.addWidget(self.dl_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.gpu = Switch(s.device != "cpu")
        self.gpu.toggled.connect(self._gpu)
        ml.addWidget(_setting_row("Use the graphics card when possible",
                                  "Makes indexing several times faster. Falls back to the processor by itself.",
                                  self.gpu))
        self.speed = Segmented([("gentle", "Gentle"), ("balanced", "Balanced"), ("fast", "Fast")], s.speed)
        self.speed.changed.connect(self._speed)
        ml.addWidget(_setting_row("Indexing speed", "How much of the PC indexing may use. It always leaves room for "
                                  "Windows and your other apps, and slows down by itself when you get busy.",
                                  self.speed))
        lay.addWidget(mc)

        # quick search
        qc, ql = card(spacing=6)
        ql.addWidget(label("Quick Search", "headline"))
        ql.addWidget(label("A small search bar you can open from anywhere with a keyboard shortcut.", "caption"))
        self.hotkey = QLineEdit(s.quick_hotkey)
        self.hotkey.setFixedWidth(170)
        self.hotkey.editingFinished.connect(self._hotkey)
        ql.addWidget(_setting_row("Shortcut", "", self.hotkey))
        self.opens = Segmented([("app", "This window"), ("bar", "Small search bar")], s.quick_opens)
        self.opens.changed.connect(self._opens)
        ql.addWidget(_setting_row("The shortcut opens", "", self.opens))
        self.signin = Switch(quick.startup_enabled())
        self.signin.toggled.connect(self._signin)
        ql.addWidget(_setting_row("Open at sign-in", "Starts quietly in the system tray when you sign in to Windows.",
                                  self.signin))
        ql.addWidget(button("Start Quick Search", "plain", "search", lambda: quick.launch_quick()), 0,
                     Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(qc)

        # appearance
        ac, al = card(spacing=6)
        self.appearance = Segmented([("system", "Automatic"), ("light", "Light"), ("dark", "Dark")], s.ui_appearance)
        self.appearance.changed.connect(self._appearance)
        al.addWidget(_setting_row("Appearance", "", self.appearance))
        lay.addWidget(ac)

        # advanced
        vc, vl = card(spacing=6)
        vl.addWidget(label("Advanced", "headline"))
        vl.addWidget(label("Detailed tools: every duplicate type, review scores, organization editing, indexing "
                           "details, activity history and permanent deletion.", "caption", wrap=True))
        row = QHBoxLayout()
        row.addWidget(button("Open Advanced Tools...", "secondary", slot=launch_advanced))
        row.addWidget(button("Open Data Folder", "plain", slot=lambda: open_file(str(config.app_home()))))
        row.addStretch(1)
        vl.addLayout(row)
        lay.addWidget(vc)
        lay.addWidget(label("Everything stays on this PC. The internet is only used when you click Download.",
                            "caption", wrap=True))
        lay.addStretch(1)
        self.refresh_folders()
        self._model_state()
        if focus == "download":
            self.dl_btn.setFocus()

    # ---- folders -------------------------------------------------------------------------
    def refresh_folders(self) -> None:
        conn = self.ctx.conn
        rows = []
        for r in library.roots(conn, enabled_only=False):
            n = conn.execute("SELECT count(*) FROM files WHERE root_id=?", (r["id"],)).fetchone()[0]
            rows.append(Row(r["id"], os.path.basename(r["path"].rstrip("\\/")) or r["path"], r["path"],
                            f"{n:,} files", "", r["path"], data=r["path"]))
        self.folders.set_rows(rows)
        self.remove_btn.setEnabled(bool(rows))

    def _note(self, text: str) -> None:
        self.folder_msg.setText(text)
        self.folder_msg.setVisible(bool(text))

    def add_folder(self) -> None:
        from ..folderpick import add_folders, pick_folders

        paths = pick_folders(self)
        if not paths:
            return
        _, notes = add_folders(self.ctx.conn, paths)
        self._note("\n".join(notes))
        self.refresh_folders()
        self.folders_changed.emit()

    def remove_folder(self) -> None:
        r = self.folders.current_row()
        if r is None:
            self._note("Choose a folder in the list first.")
            return
        if self.ctx.index_running:
            self._note("Please wait until the update has finished.")
            return
        if QMessageBox.question(self, "Remove Folder", f"Stop looking after “{r.title}”?\n\n"
                                "The files themselves stay where they are; they just won't show up in search.",
                                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
                                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            return
        library.remove_root(self.ctx.conn, r.data)
        self._note("")
        self.refresh_folders()
        self.folders_changed.emit()

    # ---- instant settings ----------------------------------------------------------------
    def _save(self) -> None:
        self.ctx.save_settings()

    def _keep(self, on: bool) -> None:
        self.ctx.settings.keep_updated = on
        self._save()
        self.watching_changed.emit(on)

    def _gpu(self, on: bool) -> None:
        self.ctx.settings.device = "auto" if on else "cpu"
        self._save()
        self.ctx.backend = None
        self.ctx.load_backend()

    def _opens(self, key: str) -> None:
        self.ctx.settings.quick_opens = key
        self._save()

    def _speed(self, key: str) -> None:
        self.ctx.settings.speed = key
        self._save()

    def _hotkey(self) -> None:
        text = self.hotkey.text().strip()
        try:
            quick.parse_hotkey(text)
        except ValueError as e:
            self.hotkey.setText(self.ctx.settings.quick_hotkey)
            QMessageBox.information(self, "Shortcut", f"“{text}” can't be used: {e}.")
            return
        if text != self.ctx.settings.quick_hotkey:
            self.ctx.settings.quick_hotkey = text
            self._save()
            QMessageBox.information(self, "Shortcut", "Saved. If Quick Search is running, quit it from its tray icon "
                                    "and start it again to use the new shortcut.")

    def _signin(self, on: bool) -> None:
        try:
            quick.set_startup(on)
        except Exception as e:
            QMessageBox.warning(self, "Open at sign-in", f"That didn't work: {e}")
            self.signin.blockSignals(True)
            self.signin.setChecked(not on)
            self.signin.blockSignals(False)

    def _appearance(self, key: str) -> None:
        self.ctx.settings.ui_appearance = key
        self._save()
        self.appearance_changed.emit(key)

    # ---- models --------------------------------------------------------------------------
    def _model_state(self) -> None:
        from .welcome import models_missing

        missing = models_missing(self.ctx.settings)
        b = self.ctx.backend
        if missing:
            self.model_state.setText("Not set up yet. Without it, search still finds files by name and words.")
        elif b is not None:
            where = "graphics card" if b.device not in ("cpu", "") else "processor"
            self.model_state.setText(f"Ready - running on the {where}.")
        else:
            self.model_state.setText("Ready.")
        self.dl_btn.setVisible(missing)

    def download(self) -> None:
        if QMessageBox.question(self, "Download", "Download the smart search models (about 300 MB) from "
                                "huggingface.co? This happens once; afterwards everything works offline.",
                                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
                                QMessageBox.StandardButton.Ok) != QMessageBox.StandardButton.Ok:
            return
        self.dl_btn.setEnabled(False)
        self.dl_btn.setText("Downloading...")
        settings = self.ctx.settings

        def work(job):
            from ...embeddings import download_model, model_is_cached
            from ...vision import download_pair, pair_is_cached

            if not model_is_cached(settings.embedding_model):
                download_model(settings.embedding_model)
            if settings.image_search and not pair_is_cached(settings.vision_pair):
                download_pair(settings.vision_pair)

        def finished() -> None:
            self.dl_btn.setEnabled(True)
            self.dl_btn.setText("Download (about 300 MB)...")
            self.ctx.load_backend()
            self._model_state()

        self.ctx.run(work, lambda _: None, lambda msg: self.model_state.setText(
            "The download didn't finish: " + msg.split("\n\n")[0][:140]), on_finished=finished)

    def showEvent(self, e):
        super().showEvent(e)
        theme.dark_title_bar(self, theme.current().dark)
