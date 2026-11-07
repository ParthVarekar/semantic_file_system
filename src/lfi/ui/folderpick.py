"""A folder picker that lets you select several folders at once (Ctrl/Shift+click).

The Windows folder dialog only returns one folder, so this uses Qt's own dialog and switches its
views to multi-selection. Protected folders (Windows, Program Files, ...) can be selected without
harm: ``add_folders`` skips them and says which ones it skipped."""

from __future__ import annotations

import os

from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QListView, QTreeView, QWidget

from .. import library
from ..safety import ScopePolicy, drive_tool_dir, norm


def pick_folders(parent: QWidget | None, title: str = "Choose folders") -> list[str]:
    dlg = QFileDialog(parent, title + "  (Ctrl+click or Shift+click to choose several)")
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
    for view in dlg.findChildren(QListView) + dlg.findChildren(QTreeView):
        if view.objectName() in ("listView", "treeView"):
            view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    if not dlg.exec():
        return []
    return [os.path.normpath(p) for p in dlg.selectedFiles() if os.path.isdir(p)]


def add_folders(conn, paths: list[str]) -> tuple[list[str], list[str]]:
    """Add each folder as an index root. Returns (added, notes) where notes explain skipped ones."""
    added, notes = [], []
    skipped = []
    for p in paths:
        name = os.path.basename(p.rstrip("\\/")) or p
        if len(paths) > 1 and drive_tool_dir(norm(p)):  # "select all" on a drive: leave programs out
            skipped.append(name)
            continue
        err = ScopePolicy.validate_root(p)
        if err:
            notes.append(f"{name}: skipped, {err[0].lower() + err[1:]}")
            continue
        try:
            library.add_root(conn, p)
            added.append(p)
        except ValueError as e:
            if "overlaps" in str(e):
                notes.append(f"{name}: already covered by a folder in the list.")
            else:
                notes.append(f"{name}: {e}")
    if skipped:
        notes.append(f"Left out as programs or system accounts, not your files: {', '.join(skipped)}. "
                     "Add one on its own if you do want it.")
    return added, notes
