"""Apps: what is installed on this PC, biggest first, and a way to uninstall what you don't want.

Uninstalling never deletes anything itself: it starts the app's own uninstaller (which shows its own
window and, if it needs administrator rights, asks Windows for them), and the list updates when the
app disappears. Sizes are what each installer recorded; "Measure sizes" adds up the real folder
sizes (nothing is opened or changed), which also corrects installers that record nonsense.
"""

from __future__ import annotations

import datetime as dt
import os
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from ... import apps
from ..core import AppContext, reveal
from .widgets import EmptyState, FileList, Row, SortButton, button, chip, fmt_size, label

SORTS = [("largest", "Largest first"), ("smallest", "Smallest first"), ("name", "Name"),
         ("newest", "Recently installed"), ("oldest", "Longest installed")]
STALE_S = 120
WATCH_S = 600
WATCH_EVERY_MS = 3000


class AppsPage(QWidget):
    def __init__(self, ctx: AppContext, toast):
        super().__init__()
        self.ctx, self.toast = ctx, toast
        self.apps: list[apps.App] = []
        self.measured: dict[str, int] = {}  # app key -> real size, kept across refreshes
        self.loaded_at = 0.0
        self.loading = False
        self._measuring = None  # the running Worker
        self._watch: dict | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 26, 32, 18)
        lay.setSpacing(10)
        lay.addWidget(label("Apps", "largeTitle"))
        lay.addWidget(label("Everything installed on this PC, biggest first. Uninstall opens the app's own "
                            "uninstaller; your documents are never touched by this program.", "secondary", wrap=True))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter apps")
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(240)
        self.filter.textChanged.connect(lambda _: self._render())
        self.components = chip("Show runtimes and drivers")
        self.components.setToolTip("Visual C++, .NET, drivers and other pieces that other programs may rely on")
        self.components.toggled.connect(lambda _: self._render())
        self.count = label("", "caption")
        self.sort = SortButton(SORTS, "largest")
        self.sort.changed.connect(lambda k: self.list.model_.sort(k))
        row.addWidget(self.filter)
        row.addWidget(self.components)
        row.addStretch(1)
        row.addWidget(self.count)
        row.addWidget(self.sort)
        lay.addLayout(row)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.list = FileList()
        self.list.model_.order = "largest"
        self.list.selectionModel().currentChanged.connect(lambda *_: self._inspect())
        self.stack.addWidget(self.list)
        self.state = EmptyState("package", "Looking at your apps...", "")
        self.stack.addWidget(self.state)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.info = label("", "secondary", wrap=True)
        self.measure_btn = button("Measure sizes", "secondary", "sync", self.measure)
        self.measure_btn.setToolTip("Adds up the file sizes in each app's folder (nothing is opened or changed). "
                                    "Also corrects installers that record a wrong size.")
        self.folder_btn = button("Show folder", "secondary", "reveal", self.show_folder)
        self.remove_btn = button("Uninstall...", "danger", "trash", self.uninstall)
        bar.addWidget(self.info, 1)
        bar.addWidget(self.measure_btn)
        bar.addWidget(self.folder_btn)
        bar.addWidget(self.remove_btn)
        lay.addLayout(bar)
        self._timer = QTimer(self)
        self._timer.setInterval(WATCH_EVERY_MS)
        self._timer.timeout.connect(self._watch_tick)
        self._inspect()

    # ---- loading ---------------------------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        if not self.loaded_at or time.monotonic() - self.loaded_at > STALE_S:
            self.refresh()

    def refresh(self) -> None:
        if self.loading:
            return
        self.loading = True
        if not self.apps:
            self.state.set_text("Looking at your apps...", "")
            self.stack.setCurrentWidget(self.state)
        self.ctx.run(lambda job: apps.list_apps(), self._loaded, self._failed, on_finished=self._finished)

    def _finished(self) -> None:
        self.loading = False

    def _failed(self, msg: str) -> None:
        self.state.set_text("Couldn't read the list of apps", msg.split("\n\n")[0][:160])
        self.stack.setCurrentWidget(self.state)

    def _loaded(self, found: list) -> None:
        for a in found:
            if a.key in self.measured:
                a.size, a.size_note = self.measured[a.key], "measured"
        self.apps = found
        self.loaded_at = time.monotonic()
        self._render()

    def _row(self, i: int, a: apps.App) -> Row:
        sub = "  ·  ".join(s for s in (a.publisher, f"Version {a.version}" if a.version else "",
                                       {"user": "Installed just for you", "store": "Microsoft Store"}.get(a.scope, ""))
                           if s)
        when = ""
        if a.installed:
            try:
                when = dt.date.fromisoformat(a.installed).strftime("%d %b %Y").lstrip("0")
            except ValueError:
                pass
        return Row(i, a.name, sub, fmt_size(a.size) if a.size else "Unknown size", when, a.icon,
                   badge="Runtime or driver" if a.component else "", data=a, size=a.size, when=a.installed_ts,
                   extra={"glyph": "package"})

    def _render(self) -> None:
        text = self.filter.text().strip().lower()
        show = self.components.isChecked()
        shown = [a for a in self.apps if (show or not a.component)
                 and (not text or text in a.name.lower() or text in a.publisher.lower())]
        self.list.set_rows([self._row(i, a) for i, a in enumerate(shown)])
        total = sum(a.size for a in shown)
        self.count.setText(f"{len(shown)} app{'s' * (len(shown) != 1)}" + (f"  ·  {fmt_size(total)}" if total else ""))
        self._retitle_measure()
        if shown:
            self.stack.setCurrentWidget(self.list)
        else:
            self.state.set_text("No apps match" if self.apps else "No apps found",
                                "Try a different word." if self.apps else "")
            self.stack.setCurrentWidget(self.state)
        self._inspect()

    # ---- the chosen app --------------------------------------------------------------------
    def _current(self) -> apps.App | None:
        r = self.list.current_row()
        return r.data if r is not None else None

    def _inspect(self) -> None:
        a = self._current()
        self.remove_btn.setEnabled(a is not None)
        self.folder_btn.setEnabled(a is not None and bool(a.location or a.icon))
        if a is None:
            self.info.setText("Choose an app to see where it lives." if self.apps else "")
            return
        size = (fmt_size(a.size) + (f" ({a.size_note})" if a.size_note else "")) if a.size else "size unknown"
        self.info.setText(f"{a.name}  ·  {size}" + (f"  ·  {a.location}" if a.location else ""))

    def show_folder(self) -> None:
        a = self._current()
        if a is not None:
            target = a.location or os.path.dirname(a.icon)
            if target and os.path.exists(target):
                reveal(target)

    # ---- measuring -------------------------------------------------------------------------
    def _retitle_measure(self) -> None:
        if self._measuring is not None:
            return
        unknown = sum(1 for a in self.apps if not a.size and apps.measurable(a.location))
        self.measure_btn.setText(f"Measure sizes ({unknown} unknown)" if unknown else "Measure sizes")

    def measure(self) -> None:
        if self._measuring is not None:
            self._measuring.job.cancel.set()  # the button reads "Stop" while measuring
            return
        targets = [a for a in self.apps if apps.measurable(a.location)]
        if not targets:
            self.toast.show_message("None of these apps say where they are installed, so there is nothing to measure.")
            return
        targets.sort(key=lambda a: (a.key in self.measured, bool(a.size)))  # unknown sizes first
        found: dict[str, int] = {}

        def work(job):
            for i, a in enumerate(targets):
                if job.cancel.is_set():
                    break
                size, complete = apps.folder_size(a.location, 10.0, cancel=job.cancel.is_set)
                if complete:
                    found[a.key] = size
                job.progress("measure", i + 1, len(targets), a.name)
            return dict(found)

        self.measure_btn.setText("Stop measuring")
        self._measuring = self.ctx.run(work, self._measured, lambda msg: self.toast.show_message("Couldn't measure sizes."),
                                       on_progress=lambda ph, d, t, m: self.info.setText(f"Measuring {d} of {t}: {m}"),
                                       on_finished=self._measure_done)

    def _measured(self, sizes: dict) -> None:
        self.measured.update(sizes)
        for a in self.apps:
            if a.key in sizes:
                a.size, a.size_note = sizes[a.key], "measured"
        self._render()
        if sizes:
            self.toast.show_message(f"Measured {len(sizes)} app{'s' * (len(sizes) != 1)}")

    def _measure_done(self) -> None:
        self._measuring = None
        self._retitle_measure()

    # ---- uninstalling ----------------------------------------------------------------------
    def uninstall(self) -> None:
        a = self._current()
        if a is None:
            return
        msg = (f"Uninstall {a.name}?\n\nThis opens the app's own uninstaller, which decides what to remove. "
               "Your documents are not touched by this program.")
        if a.component:
            msg += "\n\nOther programs may rely on this. Remove it only if you're sure."
        ask = QMessageBox.question(self, "Uninstall app", msg,
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                   QMessageBox.StandardButton.Cancel)
        if ask != QMessageBox.StandardButton.Yes:
            return
        try:
            proc = apps.launch_uninstall(a)
        except (OSError, ValueError) as e:
            self.toast.show_message(f"Couldn't start the uninstaller for {a.name}: {e}")
            return
        self.toast.show_message(f"The uninstaller for {a.name} is open. This list updates when it finishes.", ms=8000)
        self._watch = {"app": a, "proc": proc, "until": time.monotonic() + WATCH_S}
        self._timer.start()

    def _watch_tick(self) -> None:
        w = self._watch
        if w is None or time.monotonic() > w["until"]:
            self._watch = None
            self._timer.stop()
            return
        a, proc = w["app"], w["proc"]
        if a.scope == "store":  # asking for the Store list takes a second: only once the remover has ended
            if proc is None or proc.poll() is None:
                return
            gone = not apps.still_installed(a)
        else:
            gone = not apps.still_installed(a)
        if gone:
            self._watch = None
            self._timer.stop()
            self.apps = [x for x in self.apps if x.key != a.key]
            self._render()
            self.toast.show_message(f"{a.name} was uninstalled")
            self.loaded_at = 0.0  # re-read the real list next time the page is shown
