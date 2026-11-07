"""Settings: models, device, indexing limits, cleanup weights, privacy & data controls."""

from __future__ import annotations

from dataclasses import fields

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ... import config, db
from ...config import ScoreWeights
from ...embeddings import CATALOG, download_model, model_is_cached
from ...vision import VISION_PAIRS, download_pair, pair_is_cached
from ...sysinfo import onnx_providers
from .. import quick
from ..core import AppContext, confirm, fmt_size, open_file
from ..widgets import button_row, heading, note


def _spin(lo, hi, val, step=1, suffix=""):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(val))
    s.setSingleStep(step)
    if suffix:
        s.setSuffix(suffix)
    return s


class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.addWidget(heading("Settings", 18))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        s = ctx.settings

        gm = QGroupBox("Embedding model (local)")
        fm = QFormLayout(gm)
        self.backend = QComboBox()
        self.backend.addItem("Built-in (ONNX Runtime via fastembed)", "fastembed")
        self.backend.addItem("Ollama running on this computer", "ollama")
        self.backend.setCurrentIndex(max(0, self.backend.findData(s.embedding_backend)))
        fm.addRow("Runtime:", self.backend)
        self.model = QComboBox()
        self.model.setEditable(True)
        for m in CATALOG.values():
            self.model.addItem(m.name)
        self.model.setCurrentText(s.embedding_model)
        self.model.currentTextChanged.connect(self._model_info)
        fm.addRow("Model:", self.model)
        self.minfo = note("")
        fm.addRow("", self.minfo)
        self.dl = QPushButton("Download selected model...")
        self.dl.clicked.connect(self.download)
        fm.addRow("", self.dl)
        self.device = QComboBox()
        for label, v in (("Automatic (GPU if available, else CPU)", "auto"), ("CPU only", "cpu"), ("Prefer GPU", "gpu")):
            self.device.addItem(label, v)
        self.device.setCurrentIndex(max(0, self.device.findData(s.device)))
        fm.addRow("Device:", self.device)
        prov = [p for p in onnx_providers() if p != "AzureExecutionProvider"]
        fm.addRow("", note("ONNX Runtime providers installed: " + ", ".join(prov)
                           + (". Run setup with -Gpu to enable DirectML GPU acceleration."
                              if "DmlExecutionProvider" not in prov and "CUDAExecutionProvider" not in prov else "")))
        self.ollama_url = QLineEdit(s.ollama_url)
        fm.addRow("Ollama URL (local only):", self.ollama_url)
        self.batch = _spin(0, 256, s.embed_batch_size)
        fm.addRow("Batch size (0 = auto):", self.batch)
        fm.addRow("", note("Changing the model re-computes vectors at the next indexing run (text is not re-read)."))
        lay.addWidget(gm)

        gv = QGroupBox("Image search (find pictures by describing them)")
        fv = QFormLayout(gv)
        self.img_on = QCheckBox("Understand image content so photos and screenshots can be found by description")
        self.img_on.setChecked(s.image_search)
        fv.addRow("", self.img_on)
        self.vpair = QComboBox()
        for key, vp in VISION_PAIRS.items():
            self.vpair.addItem(f"{key}: {vp.image_model.split('/')[-1]}", key)
        self.vpair.setCurrentIndex(max(0, self.vpair.findData(s.vision_pair)))
        self.vpair.currentIndexChanged.connect(lambda _: self._vision_info())
        fv.addRow("Model:", self.vpair)
        self.vinfo = note("")
        fv.addRow("", self.vinfo)
        self.vdl = QPushButton("Download image model...")
        self.vdl.clicked.connect(self.download_vision)
        fv.addRow("", self.vdl)
        self.vthreads = _spin(0, 64, s.vision_threads)
        fv.addRow("CPU threads for images (0 = a quarter of the CPU):", self.vthreads)
        fv.addRow("", note("Images are decoded and embedded in one separate low-priority process, a few at a "
                           "time, so the laptop stays responsive. Auto-tags are conservative and may be wrong."))
        lay.addWidget(gv)

        gq = QGroupBox("Quick search (launcher)")
        fq = QFormLayout(gq)
        fq.addRow("", note("A small search bar in the style of Flow Launcher: press the shortcut anywhere, type, "
                           "Enter opens the file. It lives in the system tray."))
        self.qhotkey = QLineEdit(s.quick_hotkey)
        self.qhotkey.setPlaceholderText("e.g. Alt+Shift+Space")
        fq.addRow("Shortcut:", self.qhotkey)
        self.qscope = QComboBox()
        for key, label in (("all", "All files"), ("documents", "Documents"), ("images", "Images")):
            self.qscope.addItem(label, key)
        self.qscope.setCurrentIndex(max(0, self.qscope.findData(s.quick_scope)))
        fq.addRow("Search first in:", self.qscope)
        self.qidle = _spin(1, 240, s.quick_idle_unload_min, 1, " min")
        fq.addRow("Free model memory after:", self.qidle)
        self.qstartup = QCheckBox("Start quick search when I sign in to Windows")
        self.qstartup.setChecked(quick.startup_enabled())
        fq.addRow("", self.qstartup)
        qstart = QPushButton("Start quick search now")
        qstart.clicked.connect(lambda: quick.launch_quick())
        fq.addRow("", qstart)
        lay.addWidget(gq)

        gi = QGroupBox("Indexing limits")
        fi = QFormLayout(gi)
        self.max_chars = _spin(1000, 5_000_000, s.max_text_chars, 10000, " chars")
        self.max_pages = _spin(1, 10000, s.max_pdf_pages, 10, " pages")
        self.max_mb = _spin(1, 100_000, s.max_extract_bytes // 2**20, 10, " MB")
        self.timeout = _spin(5, 3600, int(s.extract_timeout_s), 5, " s")
        self.workers = _spin(0, 32, s.extract_workers)
        self.store_text = QCheckBox("Keep extracted text in the index (enables keyword search and previews)")
        self.store_text.setChecked(s.store_text)
        fi.addRow("Text per file:", self.max_chars)
        fi.addRow("PDF pages per file:", self.max_pages)
        fi.addRow("Skip text extraction above:", self.max_mb)
        fi.addRow("Per-file timeout:", self.timeout)
        fi.addRow("Reader processes (0 = auto):", self.workers)
        fi.addRow("", self.store_text)
        lay.addWidget(gi)

        gc = QGroupBox("Cleanup review")
        fc = QFormLayout(gc)
        self.large = _spin(1, 1_000_000, s.large_file_bytes // 2**20, 50, " MB")
        self.inst_age = _spin(0, 3650, s.installer_min_age_days, 10, " days")
        self.img_dist = _spin(0, 16, s.image_hash_distance)
        self.doc_thr = QDoubleSpinBox()
        self.doc_thr.setRange(0.5, 0.999)
        self.doc_thr.setDecimals(3)
        self.doc_thr.setSingleStep(0.01)
        self.doc_thr.setValue(s.similar_doc_threshold)
        fc.addRow("Large file threshold:", self.large)
        fc.addRow("Installer minimum age:", self.inst_age)
        fc.addRow("Image similarity (max bits different of 64):", self.img_dist)
        fc.addRow("Similar documents (cosine at least):", self.doc_thr)
        fc.addRow(QLabel("<b>Review-priority weights</b> (points each signal contributes):"))
        self.w = {}
        for f in fields(ScoreWeights):
            sp = QDoubleSpinBox()
            sp.setRange(-100, 100)
            sp.setValue(getattr(s.weights, f.name))
            self.w[f.name] = sp
            fc.addRow(f.name.replace("_", " ") + ":", sp)
        lay.addWidget(gc)

        go = QGroupBox("File operations")
        fo = QFormLayout(go)
        self.cross = QCheckBox("Allow moves between drives (copy, verify hash, then remove original)")
        self.cross.setChecked(s.allow_cross_volume_moves)
        fo.addRow("", self.cross)
        lay.addWidget(go)

        gp = QGroupBox("Privacy & data")
        pl = QVBoxLayout(gp)
        pl.addWidget(note(f"All data stays in <code>{config.app_home()}</code>. The index contains file paths, "
                          "metadata and text excerpts from your documents - treat it as sensitive. No telemetry; "
                          "the network is only used when you click Download."))
        b1, b2, b3, b4 = (QPushButton("Open data folder"), QPushButton("Remove extracted text..."),
                          QPushButton("Clear index..."), QPushButton("Rebuild (clear + re-index)..."))
        b1.clicked.connect(lambda: open_file(str(config.app_home())))
        b2.clicked.connect(self.clear_text)
        b3.clicked.connect(lambda: self.clear_index(False))
        b4.clicked.connect(lambda: self.clear_index(True))
        pl.addLayout(button_row(b1, b2))
        pl.addLayout(button_row(b3, b4))
        self.index_buttons = [b2, b3, b4]
        for b in self.index_buttons:
            b.setToolTip("Not available while indexing or a file operation is running")
        ctx.busy_changed.connect(lambda busy: [b.setEnabled(not busy) for b in self.index_buttons])
        self.dbsize = QLabel("")
        pl.addWidget(self.dbsize)
        lay.addWidget(gp)
        lay.addStretch(1)

        save = QPushButton("Save settings")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        outer.addLayout(button_row(save))
        self._model_info(self.model.currentText())
        self._vision_info()

    def showEvent(self, e):
        p = config.db_path()
        size = sum(f.stat().st_size for f in p.parent.glob(p.name + "*") if f.exists())
        self.dbsize.setText(f"Index size on disk: {fmt_size(size)}")
        super().showEvent(e)

    def _model_info(self, name: str) -> None:
        m = CATALOG.get(name)
        if m is None:
            self.minfo.setText("Custom model name (must be supported by fastembed, or an Ollama model name).")
            return
        cached = model_is_cached(name)
        self.minfo.setText(f"{m.dim}-dimensional, ~{m.size_mb} MB download, license {m.license}, {m.languages}. "
                           f"{m.note} {'Downloaded - works offline.' if cached else 'Not downloaded yet.'}")
        self.dl.setEnabled(not cached)

    def _vision_info(self) -> None:
        vp = VISION_PAIRS[self.vpair.currentData()]
        cached = pair_is_cached(vp.key)
        self.vinfo.setText(f"{vp.image_model} + {vp.text_model}: ~{vp.size_mb} MB, {vp.dim}-d, license {vp.license}. "
                           f"{vp.note} {'Downloaded - works offline.' if cached else 'Not downloaded yet.'}")
        self.vdl.setEnabled(not cached)

    def download_vision(self) -> None:
        vp = VISION_PAIRS[self.vpair.currentData()]
        if not confirm(self, "Download image model", f"Download '{vp.key}' (about {vp.size_mb} MB) from Hugging Face "
                       f"(huggingface.co) into {config.models_dir()}?\n\nAfter this, image search runs fully offline."):
            return
        self.vdl.setEnabled(False)
        self.vinfo.setText("Downloading...")
        self.ctx.run(lambda job: download_pair(vp.key), lambda _: self._vision_info(),
                     on_finished=lambda: self._vision_info())

    def download(self) -> None:
        name = self.model.currentText()
        m = CATALOG.get(name)
        size = f"about {m.size_mb} MB" if m else "unknown size"
        if not confirm(self, "Download model", f"Download '{name}' ({size}) from Hugging Face "
                       f"(huggingface.co) into {config.models_dir()}?\n\nThis is the only time the app uses the "
                       "network. After this, the model runs fully offline."):
            return
        self.dl.setEnabled(False)
        self.minfo.setText("Downloading...")
        self.ctx.run(lambda job: download_model(name), lambda _: self._model_info(name),
                     on_finished=lambda: self.dl.setEnabled(True))

    def save(self) -> None:
        s = self.ctx.settings
        hotkey = self.qhotkey.text().strip()
        try:
            quick.parse_hotkey(hotkey)
        except ValueError as e:
            QMessageBox.warning(self, "Settings", f"Quick search shortcut '{hotkey}': {e}. Nothing was saved.")
            return
        hotkey_changed = hotkey != s.quick_hotkey
        s.quick_hotkey, s.quick_scope, s.quick_idle_unload_min = hotkey, self.qscope.currentData(), self.qidle.value()
        if self.qstartup.isChecked() != quick.startup_enabled():
            try:
                quick.set_startup(self.qstartup.isChecked())
            except Exception as e:
                QMessageBox.warning(self, "Settings", f"Could not change the sign-in shortcut: {e}")
        model_changed = (s.embedding_model != self.model.currentText() or s.embedding_backend != self.backend.currentData()
                         or s.device != self.device.currentData() or s.image_search != self.img_on.isChecked()
                         or s.vision_pair != self.vpair.currentData())
        s.image_search = self.img_on.isChecked()
        s.vision_pair = self.vpair.currentData()
        s.vision_threads = self.vthreads.value()
        s.embedding_backend = self.backend.currentData()
        s.embedding_model = self.model.currentText().strip()
        s.device = self.device.currentData()
        s.ollama_url = self.ollama_url.text().strip()
        s.embed_batch_size = self.batch.value()
        s.max_text_chars = self.max_chars.value()
        s.max_pdf_pages = self.max_pages.value()
        s.max_extract_bytes = self.max_mb.value() * 2**20
        s.extract_timeout_s = float(self.timeout.value())
        s.extract_workers = self.workers.value()
        s.store_text = self.store_text.isChecked()
        s.large_file_bytes = self.large.value() * 2**20
        s.installer_min_age_days = self.inst_age.value()
        s.image_hash_distance = self.img_dist.value()
        s.similar_doc_threshold = self.doc_thr.value()
        for k, sp in self.w.items():
            setattr(s.weights, k, sp.value())
        s.allow_cross_volume_moves = self.cross.isChecked()
        self.ctx.save_settings()
        if model_changed:
            self.ctx.backend = None
            self.ctx.vision = None
            self.ctx.load_backend()
        QMessageBox.information(self, "Settings", "Saved." + (" Loading the new model..." if model_changed else "")
                                + (" Restart quick search (tray icon > Quit, then start it again) to use the new "
                                   "shortcut." if hotkey_changed else ""))

    def _refused_while_busy(self, title: str) -> bool:
        if self.ctx.index_running:  # the indexer is writing the same tables
            QMessageBox.information(self, title, "Stop indexing first (Folders & indexing > Cancel), then try again.")
            return True
        return self.ctx.fileop_refused(self)

    def clear_text(self) -> None:
        if self._refused_while_busy("Remove extracted text"):
            return
        if confirm(self, "Remove extracted text", "Delete all stored text excerpts from the index? Metadata, hashes "
                   "and vectors stay; keyword search and previews will be empty until re-indexed."):
            db.clear_text_cache(self.ctx.conn)
            self.ctx.data_changed.emit("index")

    def clear_index(self, rebuild: bool) -> None:
        """Clear the index; with ``rebuild`` indexing starts again right away (Folders page)."""
        if self._refused_while_busy("Rebuild index" if rebuild else "Clear index"):
            return
        if not confirm(self, "Clear index", "Delete the index (paths, metadata, text, vectors)? Your files are not "
                       "touched. Quarantine records and history are kept so files stay restorable."):
            return
        db.clear_index(self.ctx.conn)
        self.ctx.data_changed.emit("index")
        if rebuild:
            self.ctx.reindex_requested.emit()
            if self.ctx.index_running:
                QMessageBox.information(self, "Rebuild", "Index cleared; re-indexing has started. Progress is shown "
                                                         "on 'Folders & indexing'.")

