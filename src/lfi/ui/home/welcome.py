"""First run: choose folders (sensible ones pre-selected), optionally fetch the search models, go."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ... import library
from ..core import AppContext
from ..quick import app_icon
from .widgets import Switch, button, card, icon_label, label


def standard_folders() -> list[tuple[str, str]]:
    home = os.path.expanduser("~")
    out = []
    for name in ("Desktop", "Documents", "Downloads", "Pictures"):
        p = os.path.join(home, name)
        onedrive = os.path.join(os.environ.get("OneDrive", ""), name) if os.environ.get("OneDrive") else ""
        if onedrive and os.path.isdir(onedrive) and not os.path.isdir(p):
            p = onedrive
        if os.path.isdir(p):
            out.append((name, p))
    return out


def models_missing(settings) -> bool:
    from ...embeddings import model_is_cached
    from ...vision import pair_is_cached

    return not model_is_cached(settings.embedding_model) or (settings.image_search and not pair_is_cached(settings.vision_pair))


class FolderRow(QWidget):
    def __init__(self, title: str, path: str, on: bool = True, icon: str = "folder"):
        super().__init__()
        self.path = path
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 6, 0, 6)
        h.setSpacing(12)
        h.addWidget(icon_label(icon, 22))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(title, "headline"))
        col.addWidget(label(path, "caption"))
        h.addLayout(col, 1)
        self.switch = Switch(on)
        h.addWidget(self.switch)


class WelcomePage(QWidget):
    finished = Signal()

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        center = QHBoxLayout(inner)
        center.addStretch(1)
        col = QVBoxLayout()
        col.setSpacing(14)
        center.addLayout(col)
        center.addStretch(1)
        inner.setMinimumWidth(560)

        col.addSpacing(36)
        logo = QLabel()
        logo.setPixmap(app_icon(72).pixmap(72, 72))
        col.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        t = label("Welcome to Local File Intelligence", "largeTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(t)
        s = label("Find any file by describing it, clear out duplicates and keep folders tidy - all on this PC. "
                  "Nothing is uploaded.", "secondary", wrap=True)
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setFixedWidth(520)
        col.addWidget(s, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(8)

        fc, fl = card(spacing=2)
        fc.setFixedWidth(520)
        fl.addWidget(label("Which folders should it look after?", "headline"))
        fl.addWidget(label("Files are only read. Nothing is moved or removed unless you say so.", "caption"))
        fl.addSpacing(6)
        self.rows: list[FolderRow] = []
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(0)
        fl.addLayout(self.rows_box)
        for name, p in standard_folders():
            self._add_row(name, p, True)
        fl.addWidget(button("Add Another Folder...", "plain", "add", self.add_other), 0, Qt.AlignmentFlag.AlignLeft)
        col.addWidget(fc, 0, Qt.AlignmentFlag.AlignHCenter)

        self.dl_row = None
        if models_missing(ctx.settings):
            mc, ml = card()
            mc.setFixedWidth(520)
            self.dl_row = FolderRow("Smart search", "One-time download of about 300 MB from huggingface.co, so you can "
                                    "search by meaning and describe photos. Afterwards it all works offline.",
                                    True, "download")
            self.dl_row.findChildren(QLabel)[-1].setWordWrap(True)
            ml.addWidget(self.dl_row)
            col.addWidget(mc, 0, Qt.AlignmentFlag.AlignHCenter)

        self.error = label("", "caption", wrap=True)
        self.error.setStyleSheet("color: #FF3B30;")
        self.error.setFixedWidth(520)
        self.error.hide()
        col.addWidget(self.error, 0, Qt.AlignmentFlag.AlignHCenter)
        self.go = button("Get Started", "primary", slot=self.get_started)
        self.go.setMinimumWidth(220)
        self.go.setMinimumHeight(38)
        col.addSpacing(6)
        col.addWidget(self.go, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(30)

    def _add_row(self, title: str, path: str, on: bool) -> None:
        row = FolderRow(title, path, on)
        self.rows.append(row)
        self.rows_box.addWidget(row)

    def add_other(self) -> None:
        from ..folderpick import pick_folders

        for p in pick_folders(self):
            if all(os.path.normcase(r.path) != os.path.normcase(p) for r in self.rows):
                self._add_row(os.path.basename(p) or p, p, True)

    def chosen(self) -> list[str]:
        return [r.path for r in self.rows if r.switch.isChecked()]

    def get_started(self) -> None:
        paths = self.chosen()
        if not paths:
            self._error("Choose at least one folder.")
            return
        problems = []
        for p in paths:
            try:
                library.add_root(self.ctx.conn, p)
            except ValueError as e:
                if "overlaps" not in str(e):  # e.g. Desktop and a folder inside it: the outer one covers it
                    problems.append(f"{os.path.basename(p)}: {e}")
        if not library.roots(self.ctx.conn):
            self._error(" ".join(problems) or "None of these folders can be used.")
            return
        if self.dl_row is not None and self.dl_row.switch.isChecked():
            self.go.setEnabled(False)
            self.go.setText("Downloading... (about 300 MB)")
            settings = self.ctx.settings

            def work(job):
                from ...embeddings import download_model, model_is_cached
                from ...vision import download_pair, pair_is_cached

                if not model_is_cached(settings.embedding_model):
                    download_model(settings.embedding_model)
                if settings.image_search and not pair_is_cached(settings.vision_pair):
                    download_pair(settings.vision_pair)

            def failed(msg: str) -> None:
                self._error("The download didn't finish (" + msg.split("\n\n")[0][:120] + "). Search by name and "
                            "text still works; you can download later in Settings.")

            self.ctx.run(work, lambda _: None, failed, on_finished=self._done)
            return
        self._done()

    def _done(self) -> None:
        self.ctx.load_backend()
        self.ctx.data_changed.emit("index")
        self.finished.emit()

    def _error(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()
