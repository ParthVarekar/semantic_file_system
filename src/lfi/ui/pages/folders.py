"""Folder selection, exclusions, indexing progress/cancellation, and optional live watching."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from ... import library
from ...indexer import Indexer
from ..core import AppContext, fmt_size
from ..widgets import button_row, end_fill, heading, make_table, note, SortItem

PHASES = {"discover": "Scanning folders", "process": "Reading files", "embed": "Computing embeddings",
          "images": "Understanding images (background, low priority)",
          "hash": "Verifying large duplicates", "done": "Finished"}


class FoldersPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.worker = None
        self.observer = None
        self._dirty = threading.Event()
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Folders & indexing", 18))
        lay.addWidget(note("Only the folders listed here are ever read. System and application folders "
                           "(Windows, Program Files, AppData) are always protected. Indexing is read-only."))

        g = QGroupBox("Folders to index")
        gl = QVBoxLayout(g)
        self.roots = QListWidget()
        self.roots.setMinimumHeight(40)
        gl.addWidget(self.roots)
        add, rem = QPushButton("Add folder..."), QPushButton("Remove from index")
        add.clicked.connect(self.add_root)
        rem.clicked.connect(self.remove_root)
        self.remove_btn = rem
        gl.addLayout(button_row(add, rem))

        ge = QGroupBox("Exclusions")
        el = QVBoxLayout(ge)
        self.excl = QListWidget()
        self.excl.setMaximumHeight(90)
        self.excl.setMinimumHeight(40)
        el.addWidget(self.excl)
        ea, er = QPushButton("Exclude folder..."), QPushButton("Remove exclusion")
        ea.clicked.connect(self.add_excl)
        er.clicked.connect(self.remove_excl)
        el.addLayout(button_row(ea, er))
        skip = QLabel("Always skip folders/files named (comma-separated, wildcards allowed):")
        skip.setWordWrap(True)
        el.addWidget(skip)
        self.globs = QLineEdit(", ".join(ctx.settings.exclude_globs))
        self.globs.editingFinished.connect(self.save_globs)
        el.addWidget(self.globs)
        top = QHBoxLayout()  # side by side, so the indexing controls stay visible on short screens
        top.addWidget(g, 1)
        top.addWidget(ge, 1)
        lay.addLayout(top)

        gi = QGroupBox("Index")
        il = QVBoxLayout(gi)
        self.embed_cb = QCheckBox("Compute semantic embeddings (uses the local model; slower on CPU)")
        self.embed_cb.setChecked(True)
        self.watch_cb = QCheckBox("Watch these folders and update the index automatically")
        self.watch_cb.toggled.connect(self.toggle_watch)
        il.addWidget(self.embed_cb)
        il.addWidget(self.watch_cb)
        self.start = QPushButton("Start indexing")
        self.start.setObjectName("primary")
        self.cancel = QPushButton("Cancel")
        self.cancel.setEnabled(False)
        self.start.clicked.connect(self.start_index)
        self.cancel.clicked.connect(self.cancel_index)
        self.problems = QPushButton("Show problem files")
        self.problems.clicked.connect(self.show_problems)
        il.addLayout(button_row(self.start, self.cancel, self.problems))
        self.phase = QLabel("Idle")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        il.addWidget(self.phase)
        il.addWidget(self.bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMinimumHeight(40)
        il.addWidget(self.log, 1)
        self.table = make_table(["File", "Status", "Problem"], stretch=2)
        self.table.setVisible(False)
        il.addWidget(self.table, 1)
        lay.addWidget(gi, 1)
        self.refresh()
        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._watch_tick)
        self._watch_timer.start(5000)
        ctx.data_changed.connect(lambda what: what != "settings" and self.refresh())
        ctx.busy_changed.connect(lambda _: self.remove_btn.setEnabled(not ctx.index_running))
        ctx.reindex_requested.connect(self.start_index)

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)

    # ---- roots & exclusions --------------------------------------------------------------
    def refresh(self) -> None:
        row = self.roots.currentRow()
        self.roots.clear()
        for r in library.roots(self.ctx.conn, enabled_only=False):
            n = self.ctx.conn.execute("SELECT count(*), coalesce(sum(size),0) FROM files WHERE root_id=?",
                                      (r["id"],)).fetchone()
            self.roots.addItem(f"{r['path']}    ({n[0]:,} files, {fmt_size(n[1])})")
        if 0 <= row < self.roots.count():
            self.roots.setCurrentRow(row)
        self.excl.clear()
        for p in library.exclusions(self.ctx.conn):
            self.excl.addItem(p)

    def add_root(self) -> None:
        from ..folderpick import add_folders, pick_folders

        paths = pick_folders(self, "Choose folders to index")
        if not paths:
            return
        drives = [p for p in paths if len(p) <= 3]
        if drives and QMessageBox.question(self, "Index a whole drive?",
                                           f"Index all of {', '.join(drives)}? This can take a long time. "
                                           "Protected system folders are skipped automatically."
                                           ) != QMessageBox.StandardButton.Yes:
            paths = [p for p in paths if p not in drives]
        added, notes = add_folders(self.ctx.conn, paths)
        if notes:
            QMessageBox.information(self, "Some folders were not added", "\n".join(notes))
        self.refresh()
        self.ctx.data_changed.emit("index")
        for p in added:
            self.log.appendPlainText(f"Added {p}. Click 'Start indexing' to read it.")

    def _selected_root(self) -> str | None:
        it = self.roots.currentItem()
        return it.text().split("    (")[0] if it else None

    def remove_root(self) -> None:
        p = self._selected_root()
        if not p:
            return
        if self.ctx.index_running:  # the indexer would keep writing rows for the removed folder
            QMessageBox.information(self, "Remove folder", "Stop indexing first (Cancel below), then remove the "
                                    "folder.")
            return
        if QMessageBox.question(self, "Remove folder", f"Stop indexing {p} and delete its entries from the index?\n"
                                "Your files on disk are not touched.") != QMessageBox.StandardButton.Yes:
            return
        library.remove_root(self.ctx.conn, p)
        self.refresh()
        self.ctx.data_changed.emit("index")

    def add_excl(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to exclude")
        if path:
            library.add_exclusion(self.ctx.conn, os.path.normpath(path))
            self.refresh()
            self.ctx.data_changed.emit("index")

    def remove_excl(self) -> None:
        it = self.excl.currentItem()
        if it:
            library.remove_exclusion(self.ctx.conn, it.text())
            self.refresh()

    def save_globs(self) -> None:
        self.ctx.settings.exclude_globs = [g.strip() for g in self.globs.text().split(",") if g.strip()]
        self.ctx.save_settings()

    # ---- indexing ------------------------------------------------------------------------
    def start_index(self, quiet: bool = False) -> None:
        if self.ctx.index_running:
            return
        if not library.roots(self.ctx.conn):
            if not quiet:
                QMessageBox.information(self, "No folders", "Add a folder first.")
            return
        self.ctx.set_index_running(True)
        self.start.setEnabled(False)
        self.cancel.setEnabled(True)
        self.table.setVisible(False)
        self.log.appendPlainText("Indexing started (read-only)...")
        settings, embed, backend = self.ctx.settings, self.embed_cb.isChecked(), self.ctx.backend
        ctx = self.ctx

        def work(job):
            ix = Indexer(settings, backend=backend, progress=job.progress, cancel=job.cancel, embed=embed,
                         vision=ctx.vision)
            try:
                return ix.run()
            finally:
                ix.conn.close()
                if ctx.backend is None and ix.backend is not None:
                    ctx.backend = ix.backend  # reuse the model the indexer loaded (no second copy)
                    try:
                        ctx.backend_changed.emit()
                    except RuntimeError:  # the window was closed meanwhile
                        pass

        self.worker = self.ctx.run(work, self.done, self.failed, self.on_progress, self.finished)

    def cancel_index(self) -> None:
        if self.worker is not None:
            self.worker.job.cancel.set()
            self.phase.setText("Cancelling... (progress so far is kept)")

    def on_progress(self, phase: str, done: int, total: int, msg: str) -> None:
        label = PHASES.get(phase, phase)
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.phase.setText(f"{label}: {done:,} / {total:,}")
        else:
            self.bar.setRange(0, 0)
            self.phase.setText(f"{label}: {done:,}" + (f" - {msg[-80:]}" if msg else ""))

    def done(self, st) -> None:
        s = st.__dict__
        parts = [f"{s['discovered']:,} files found", f"{s['new']:,} new", f"{s['changed']:,} changed",
                 f"{s['removed']:,} removed", f"{s['processed']:,} read", f"{s['embedded_files']:,} embedded"]
        if s["errors"]:
            parts.append(f"{s['errors']} could not be read")
        if s["encrypted"]:
            parts.append(f"{s['encrypted']} encrypted")
        if s["skipped_links"]:
            parts.append(f"{s['skipped_links']} links/junctions skipped")
        self.log.appendPlainText(("Cancelled: " if s["cancelled"] else "Done: ") + ", ".join(parts)
                                 + f" in {s['elapsed_s']} s.")
        if s["embed_error"]:
            self.log.appendPlainText("Semantic vectors skipped: " + s["embed_error"])
        if s["images_embedded"] or s["images_failed"]:
            self.log.appendPlainText(f"Images understood: {s['images_embedded']:,}"
                                     + (f" ({s['images_failed']} could not be decoded)" if s["images_failed"] else ""))
        if s["image_error"]:
            self.log.appendPlainText("Image search skipped: " + s["image_error"])
        for d in s["unreadable_dirs"][:20]:
            self.log.appendPlainText("Unreadable folder: " + d)

    def failed(self, msg: str) -> None:
        self.log.appendPlainText("Indexing failed: " + msg.split("\n\n")[0])

    def finished(self) -> None:
        self.worker = None
        self.ctx.set_index_running(False)
        self.start.setEnabled(True)
        self.cancel.setEnabled(False)
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.phase.setText("Idle")
        self.refresh()
        self.ctx.data_changed.emit("index")

    def show_problems(self) -> None:
        rows = self.ctx.conn.execute(
            "SELECT path, CASE WHEN img_stage=2 AND status='ok' THEN 'image not understood' ELSE status END"
            " AS status, error FROM files WHERE status IN ('error','timeout','locked','changed','encrypted')"
            " OR (img_stage=2 AND error IS NOT NULL) ORDER BY status, path LIMIT 2000").fetchall()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, SortItem(r["path"]))
            self.table.setItem(i, 1, SortItem(r["status"]))
            self.table.setItem(i, 2, SortItem(r["error"] or ""))
        end_fill(self.table)
        self.table.setVisible(True)
        self.log.appendPlainText(f"{len(rows)} file(s) with problems listed below (they never stop indexing).")

    # ---- watching ------------------------------------------------------------------------
    def toggle_watch(self, on: bool) -> None:
        if on:
            try:
                from watchdog.events import FileSystemEventHandler
                from watchdog.observers import Observer
            except ImportError:
                QMessageBox.warning(self, "Unavailable", "The watchdog package is not installed.")
                self.watch_cb.setChecked(False)
                return
            dirty = self._dirty

            class H(FileSystemEventHandler):
                def on_any_event(self, event):
                    p = getattr(event, "src_path", "") or ""
                    if ".lfi-quarantine" not in p and "LocalFileIntelligence" not in p:
                        dirty.set()

            self.observer = Observer()
            for r in library.roots(self.ctx.conn):
                if os.path.isdir(r["path"]):
                    self.observer.schedule(H(), r["path"], recursive=True)
            self.observer.start()
            self.log.appendPlainText("Watching folders; changes are indexed shortly after they happen.")
        elif self.observer is not None:
            self.observer.stop()
            self.observer = None
            self.log.appendPlainText("Stopped watching folders.")

    def _watch_tick(self) -> None:
        if self.observer is not None and self._dirty.is_set() and not self.ctx.index_running:
            self._dirty.clear()
            self.log.appendPlainText("Change detected; updating index...")
            self.start_index(quiet=True)

    def shutdown(self) -> None:
        if self.observer is not None:
            self.observer.stop()
        if self.worker is not None:
            self.worker.job.cancel.set()

