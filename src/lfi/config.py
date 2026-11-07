"""Application paths and user settings.

All state lives under one per-user data directory (``%LOCALAPPDATA%\\LocalFileIntelligence``
by default, overridable with ``LFI_HOME``). Nothing is written next to the user's files except
an optional per-root quarantine folder, and only when the user quarantines something.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_NAME = "LocalFileIntelligence"


def app_home() -> Path:
    override = os.environ.get("LFI_HOME")
    if override:
        base = Path(override)
    else:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return app_home() / "index.sqlite3"


def models_dir() -> Path:
    """The user's model cache (read and download target). Models bundled next to a packaged exe
    (package.ps1 -BundleModel) are copied into it once, so the program folder stays read-only and
    replacing it on upgrade never loses models the user downloaded."""
    override = os.environ.get("LFI_MODELS")
    p = Path(override) if override else app_home() / "models"
    p.mkdir(parents=True, exist_ok=True)
    if not override and getattr(sys, "frozen", False):
        bundled = Path(sys.executable).parent / "models"
        if bundled.is_dir() and not any(p.glob("models--*")):
            import shutil

            shutil.copytree(bundled, p, dirs_exist_ok=True)
    return p


def logs_dir() -> Path:
    p = app_home() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_quarantine_dir() -> Path:
    return app_home() / "quarantine"


def settings_path() -> Path:
    return app_home() / "settings.json"


def enforce_offline_env() -> None:
    """Make Hugging Face / fastembed refuse network access and telemetry.

    Called at startup; model downloads temporarily lift this inside ``download_model``.
    """
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


@contextmanager
def online_for_download():
    """Lift offline mode for one explicit, user-requested model download. huggingface_hub reads
    HF_HUB_OFFLINE once at import, so its cached constant must be lifted as well as the variable."""
    prev = os.environ.pop("HF_HUB_OFFLINE", None)
    try:
        import huggingface_hub.constants as hub
    except ImportError:
        hub = None
    old = hub.HF_HUB_OFFLINE if hub is not None else None
    if hub is not None:
        hub.HF_HUB_OFFLINE = False
    try:
        yield
    finally:
        if hub is not None:
            hub.HF_HUB_OFFLINE = old
        if prev is not None:
            os.environ["HF_HUB_OFFLINE"] = prev


# Folders made by tools, never by people: environments, dependency stores, build caches.
DEFAULT_EXCLUDES = [
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache",
    ".pytest_cache", ".idea", ".vs", ".gradle", "$RECYCLE.BIN", "System Volume Information",
    ".lfi-quarantine",
]
# Added later; merged once into existing settings (a default the user removed is not re-added,
# because only additions newer than the settings file's revision are applied).
EXCLUDE_ADDITIONS = {
    2: [".venv*", "site-packages", "__pypackages__", "*.egg-info", ".eggs", ".ruff_cache", ".hypothesis",
        ".ipynb_checkpoints", ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache", ".yarn", ".pnpm-store",
        "bower_components", ".dart_tool", ".expo", ".terraform", ".angular", ".cargo", ".rustup", ".m2"],
}
EXCLUDES_REV = max(EXCLUDE_ADDITIONS)
DEFAULT_EXCLUDES += [g for rev in sorted(EXCLUDE_ADDITIONS) for g in EXCLUDE_ADDITIONS[rev]]


@dataclass
class ScoreWeights:
    """Point contributions for the 0-100 review-priority score (see cleanup.py)."""

    exact_duplicate: float = 55.0
    similar_image: float = 30.0
    temp_strong: float = 45.0
    temp_weak: float = 25.0
    empty_file: float = 35.0
    old_installer: float = 25.0
    large_file_max: float = 15.0
    clutter_location: float = 10.0
    age_max: float = 8.0
    copy_name: float = 8.0
    in_project: float = -30.0
    recently_modified: float = -15.0
    feedback_max: float = 15.0


@dataclass
class Settings:
    embedding_backend: str = "fastembed"  # fastembed | ollama | hashing
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    ollama_url: str = "http://127.0.0.1:11434"
    device: str = "auto"  # auto | cpu | gpu
    embed_batch_size: int = 0  # 0 = auto: 8 on CPU (less padding, ~3x less RAM), 32 on GPU
    embed_threads: int = 0  # 0 = let ONNX Runtime decide

    extract_workers: int = 0  # 0 = auto (half the logical CPUs, max 6)
    extract_timeout_s: float = 60.0
    max_extract_bytes: int = 200 * 1024 * 1024
    max_text_chars: int = 200_000
    max_pdf_pages: int = 150
    max_chunks_per_file: int = 48
    chunk_chars: int = 1200
    chunk_overlap: int = 150
    hash_max_bytes: int = 512 * 1024 * 1024  # bigger files hashed only on size collision
    store_text: bool = True  # keep chunk text for snippets/FTS (privacy toggle)
    image_search: bool = True  # describe-to-find for photos/screenshots (vision-language model)
    vision_pair: str = "nomic-q"
    vision_threads: int = 0  # 0 = a quarter of the logical CPUs (keeps the laptop responsive)

    exclude_globs: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    exclude_defaults_rev: int = EXCLUDES_REV  # which default exclusions this settings file already received

    large_file_bytes: int = 500 * 1024 * 1024
    installer_min_age_days: int = 60
    image_hash_distance: int = 6
    similar_doc_threshold: float = 0.93
    organize_base: str = ""  # empty = "<root>\\Organized"
    quick_hotkey: str = "Alt+Shift+Space"  # global shortcut for the quick search window
    quick_opens: str = "app"  # what the shortcut opens: app (the window, on Search) | bar (small search bar)
    quick_scope: str = "all"  # scope the quick search window starts in: all | documents | images
    quick_idle_unload_min: int = 10  # release the search models after this many idle minutes
    speed: str = "balanced"  # gentle | balanced | fast: how much of the PC indexing may use (resources.py)
    ui_appearance: str = "system"  # system | light | dark (the everyday window)
    keep_updated: bool = True  # watch the selected folders and update the index by itself
    allow_cross_volume_moves: bool = False

    weights: ScoreWeights = field(default_factory=ScoreWeights)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or settings_path()
        s = cls()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return s
            known = {f.name for f in fields(cls)}
            s.exclude_defaults_rev = 1  # files written before revisions existed
            for k, v in raw.items():
                if k == "weights" and isinstance(v, dict):
                    wk = {f.name for f in fields(ScoreWeights)}
                    s.weights = ScoreWeights(**{a: b for a, b in v.items() if a in wk})
                elif k in known:
                    setattr(s, k, v)
            for rev in sorted(r for r in EXCLUDE_ADDITIONS if r > s.exclude_defaults_rev):
                s.exclude_globs += [g for g in EXCLUDE_ADDITIONS[rev] if g not in s.exclude_globs]
                s.exclude_defaults_rev = rev
        return s

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        data = json.dumps(asdict(self), indent=2)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)

    def worker_count(self) -> int:
        if self.extract_workers > 0:
            return self.extract_workers
        return max(1, min(6, (os.cpu_count() or 2) // 2))
