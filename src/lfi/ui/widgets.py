"""Reusable widgets."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QProgressBar, QPushButton, QSizePolicy,
                               QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from .core import fmt_size

IMAGE_PREVIEW = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".ico", ".tif", ".tiff"}


class SortItem(QTableWidgetItem):
    """Table item that sorts by a hidden key (e.g. bytes, timestamp) instead of display text."""

    def __init__(self, text: str, key=None, data=None):
        super().__init__(text)
        self._key = key if key is not None else text.lower()
        if data is not None:
            self.setData(Qt.ItemDataRole.UserRole, data)

    def __lt__(self, other):
        try:
            return self._key < other._key
        except TypeError:
            return str(self._key) < str(other._key)


def make_table(headers: list[str], stretch: int | None = None, checkable_first: bool = False) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.setWordWrap(False)
    t.setSortingEnabled(True)
    h = t.horizontalHeader()
    h.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)  # unsorted until the user clicks a header
    h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    h.setStretchLastSection(stretch is None)
    if stretch is not None:
        h.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    t.verticalHeader().setDefaultSectionSize(26)
    return t


def end_fill(t: QTableWidget) -> None:
    """Re-enable sorting after filling ``t`` without re-sorting it: rows keep the order they were
    added in (the engine's ranking) until the user clicks a column header."""
    t.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    t.setSortingEnabled(True)


def size_item(n: int) -> SortItem:
    it = SortItem(fmt_size(n), n)
    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


def check_item(text: str, checked: bool, data=None, key=None) -> SortItem:
    it = SortItem(text, key, data)
    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    it.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    return it


def checked_rows(t: QTableWidget, col: int = 0) -> list[int]:
    return [r for r in range(t.rowCount()) if t.item(r, col) and t.item(r, col).checkState() == Qt.CheckState.Checked]


def heading(text: str, size: int = 15) -> QLabel:
    lab = QLabel(text)
    f = QFont()
    f.setPointSize(size)
    f.setBold(True)
    lab.setFont(f)
    return lab


def note(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setObjectName("note")
    return lab


class Card(QFrame):
    def __init__(self, title: str, value: str = "-", sub: str = ""):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.t = QLabel(title)
        self.t.setObjectName("cardTitle")
        self.v = QLabel(value)
        self.v.setObjectName("cardValue")
        self.s = QLabel(sub)
        self.s.setObjectName("note")
        self.s.setWordWrap(True)
        for w in (self.t, self.v, self.s):
            lay.addWidget(w)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, value: str, sub: str = "") -> None:
        self.v.setText(value)
        self.s.setText(sub)


class BarList(QWidget):
    """Horizontal bars: label | bar | value."""

    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setColumnStretch(1, 1)

    def set_rows(self, rows: list[tuple[str, float, str]]) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        top = max((r[1] for r in rows), default=0) or 1
        for i, (label, val, text) in enumerate(rows):
            self.grid.addWidget(QLabel(label), i, 0)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(int(1000 * val / top))
            bar.setTextVisible(False)
            bar.setFixedHeight(10)
            self.grid.addWidget(bar, i, 1)
            v = QLabel(text)
            v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.grid.addWidget(v, i, 2)


class PreviewPane(QWidget):
    """Safe preview: image thumbnail or extracted-text snippet. Never executes file content."""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMaximumHeight(260)
        self.text = QTextBrowser()
        self.text.setOpenLinks(False)
        lay.addWidget(self.title)
        lay.addWidget(self.image)
        lay.addWidget(self.text, 1)

    def show_file(self, path: str, header_html: str, body: str) -> None:
        self.title.setText(header_html)
        ext = os.path.splitext(path)[1].lower()
        self.image.clear()
        self.image.setVisible(False)
        if ext in IMAGE_PREVIEW and os.path.isfile(path) and os.path.getsize(path) < 40 * 1024 * 1024:
            pm = QPixmap(path)
            if not pm.isNull():
                self.image.setPixmap(pm.scaled(QSize(420, 250), Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
                self.image.setVisible(True)
        self.text.setPlainText(body)

    def clear(self) -> None:
        self.title.clear()
        self.image.clear()
        self.text.clear()


class TypedConfirmDialog(QDialog):
    """Requires the user to type an exact phrase (used for permanent deletion)."""

    def __init__(self, parent, title: str, message: str, phrase: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.phrase = phrase
        lay = QVBoxLayout(self)
        m = QLabel(message)
        m.setWordWrap(True)
        lay.addWidget(m)
        lay.addWidget(QLabel(f"Type <b>{phrase}</b> to confirm:"))
        self.edit = QLineEdit()
        lay.addWidget(self.edit)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.edit.textChanged.connect(
            lambda t: self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(t == phrase))
        lay.addWidget(self.buttons)

    def typed(self) -> str:
        return self.edit.text()


def button_row(*buttons: QPushButton, stretch_first: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    if stretch_first:
        row.addStretch(1)
    for b in buttons:
        row.addWidget(b)
    if not stretch_first:
        row.addStretch(1)
    return row
