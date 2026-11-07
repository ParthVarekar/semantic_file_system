"""Image understanding for description-based search ("vision-language" embeddings).

An image tower and a text tower that share one embedding space let a plain-language query
("receipt from the grocery store", "screenshot of code") be compared directly with image
content. Both run locally through fastembed/ONNX Runtime, like the document model.

Default pair: nomic-embed-vision-v1.5 + nomic-embed-text-v1.5, int8 (Apache-2.0, ~230 MB,
ImageNet zero-shot 71.0% per the model card vs 68.3% for OpenAI CLIP ViT-B/16). Alternative:
OpenAI CLIP ViT-B/32 (MIT, ~590 MB). Choice and measurements: docs/BENCHMARKS.md.

Untrusted image files are decoded only inside an isolated, low-priority worker process
(``embed_task``), which holds the image model for its lifetime. The main process only needs
the small text tower to embed queries and tag labels.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

import numpy as np

from . import config

log = logging.getLogger(__name__)

DECODABLE = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
MAX_PIXELS = 80_000_000  # skip absurdly large images (decompression-bomb guard below Pillow's)


@dataclass(frozen=True)
class VisionPair:
    key: str
    image_model: str
    text_model: str
    dim: int
    size_mb: int
    license: str
    query_prefix: str
    center_text: bool  # nomic: reference code layer-norms text vectors before L2 normalization
    int8: bool
    note: str


VISION_PAIRS: dict[str, VisionPair] = {
    "nomic-q": VisionPair("nomic-q", "nomic-ai/nomic-embed-vision-v1.5-Q", "nomic-ai/nomic-embed-text-v1.5-Q", 768,
                          230, "Apache-2.0", "search_query: ", True, True,
                          "Default. Smallest and most accurate tested; int8, CPU."),
    "clip-b32": VisionPair("clip-b32", "Qdrant/clip-ViT-B-32-vision", "Qdrant/clip-ViT-B-32-text", 512, 590, "MIT",
                           "", False, False, "OpenAI CLIP ViT-B/32; fp32, can use the GPU (DirectML)."),
}
DEFAULT_PAIR = "nomic-q"

# Labels for automatic image tags (zero-shot). Phrased as captions, which suits these models.
TAG_LABELS = [
    "a screenshot of a computer screen", "a screenshot of programming code", "a screenshot of a phone chat",
    "a screenshot of a website", "a video game screenshot", "a presentation slide", "a photo of a receipt",
    "an invoice or bill", "a scanned document page", "a page of printed text", "handwritten notes",
    "a whiteboard with writing", "a chart or graph", "a spreadsheet table", "a diagram or flowchart",
    "a map", "a qr code", "a barcode", "an id card or passport", "a ticket or boarding pass",
    "a certificate", "a book cover", "a logo", "an illustration or drawing", "a cartoon or meme",
    "a painting", "a selfie", "a portrait of a person", "a group of people", "a baby", "a wedding",
    "a birthday party", "a concert", "people playing sports", "a classroom", "a dog", "a cat", "a bird",
    "a pet", "food on a plate", "a restaurant", "a cake", "a beach", "the sea", "a lake or river",
    "mountains", "a forest", "a garden with flowers", "a flower", "a green field", "snow", "a sunset",
    "the night sky with stars", "a city street", "buildings and architecture", "a temple", "a car",
    "a bicycle", "an airplane", "a train", "a room interior", "a kitchen", "a computer or laptop",
    "a mobile phone", "clothes or fashion", "a product photo", "shoes", "a festival", "fireworks",
]


def _providers(device: str, int8: bool) -> list[list[str]]:
    from .embeddings import _gpu_providers

    gpu = _gpu_providers() if device in ("auto", "gpu") else []
    if int8 and gpu == ["DmlExecutionProvider"]:
        gpu = []  # int8 graphs are not reliable under DirectML (verified for Qwen3 int8)
    attempts = [[gpu[0], "CPUExecutionProvider"]] if gpu else []
    return attempts + [["CPUExecutionProvider"]]


def _bg_threads() -> int:
    """Background image indexing uses at most a quarter of the logical CPUs."""
    return max(2, (os.cpu_count() or 4) // 4)


def load_image(path: str, max_side: int = 448):
    """Decode safely and cheaply: JPEG draft mode decodes at 1/2..1/8 scale directly."""
    from PIL import Image

    im = Image.open(path)
    w, h = im.size
    if w * h > MAX_PIXELS:
        raise ValueError(f"image too large ({w}x{h})")
    im.draft("RGB", (max_side, max_side))
    if getattr(im, "n_frames", 1) > 1:
        im.seek(0)
    im = im.convert("RGB")
    im.thumbnail((max_side, max_side))
    return im


class VisionBackend:
    def __init__(self, pair: VisionPair, device: str = "auto", batch_size: int = 0, load_image: bool = True,
                 load_text: bool = True, allow_download: bool = False, threads: int | None = None):
        from fastembed import ImageEmbedding, TextEmbedding

        self.pair = pair
        self.model_id = f"vision:{pair.key}"
        self.dim = pair.dim
        self.batch_size = batch_size
        self.device = "cpu"
        self.fallback_reason: str | None = None
        self._lock = threading.Lock()
        kw: dict = {"cache_dir": str(config.models_dir()), "local_files_only": not allow_download}
        self._img = self._txt = None
        if load_image:
            last = None
            for prov in _providers(device, pair.int8):
                try:
                    extra = {"threads": threads} if threads and prov == ["CPUExecutionProvider"] else {}
                    self._img = ImageEmbedding(model_name=pair.image_model, providers=prov, **kw, **extra)
                    self.device = "directml" if prov[0] == "DmlExecutionProvider" else (
                        "cuda" if prov[0] == "CUDAExecutionProvider" else "cpu")
                    break
                except Exception as e:  # missing model or GPU init failure
                    last = e
            if self._img is None:
                from .embeddings import ModelUnavailable, _explain_load_error

                raise ModelUnavailable(_explain_load_error(pair.image_model, last))
            self._img_kw = {**kw, **({"threads": threads} if threads else {})}
        if batch_size <= 0:
            # Measured (nomic-q, 4 CPU threads): 13 img/s for batch 1..8 alike, but peak memory
            # 250 / 293 / 382 / 555 MB for batch 1 / 2 / 4 / 8. GPUs benefit from larger batches.
            self.batch_size = 2 if self.device == "cpu" else 8
        if load_text:
            from .embeddings import ModelUnavailable, _explain_load_error

            try:  # single queries are faster on CPU than on a GPU (launch overhead)
                self._txt = TextEmbedding(model_name=pair.text_model, providers=["CPUExecutionProvider"], **kw)
            except Exception as e:
                raise ModelUnavailable(_explain_load_error(pair.text_model, e)) from e

    # ---- text side ---------------------------------------------------------------------------
    def embed_texts(self, texts: list[str], prefix: str | None = None) -> np.ndarray:
        prefix = self.pair.query_prefix if prefix is None else prefix
        with self._lock:
            v = np.vstack(list(self._txt.embed([prefix + t for t in texts], batch_size=16))).astype(np.float32)
        if self.pair.center_text:
            v = v - v.mean(axis=1, keepdims=True)  # == layer_norm (no affine) up to scale
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)

    def embed_text(self, text: str) -> np.ndarray:
        return self.embed_texts([text])[0]

    # ---- image side --------------------------------------------------------------------------
    def embed_images(self, images: list) -> np.ndarray:
        if not images:
            return np.zeros((0, self.dim), dtype=np.float32)
        with self._lock:
            try:
                vecs = list(self._img.embed(images, batch_size=self.batch_size))
            except Exception as e:
                if self.device == "cpu":
                    raise
                from fastembed import ImageEmbedding

                log.warning("GPU image embedding failed (%s); using CPU", e)
                self._img = ImageEmbedding(model_name=self.pair.image_model, providers=["CPUExecutionProvider"],
                                           **self._img_kw)
                self.device, self.fallback_reason = "cpu", f"GPU inference failed ({type(e).__name__}); using CPU"
                self.batch_size = min(self.batch_size, 2)
                vecs = list(self._img.embed(images, batch_size=self.batch_size))
        v = np.vstack(vecs).astype(np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)

    def embed_image_paths(self, paths: list[str]) -> np.ndarray:
        return self.embed_images([load_image(p) for p in paths])


class Tagger:
    """Zero-shot image tags: compare an image vector with caption-style label vectors and keep
    labels that stand out from the rest (z-score), at most three."""

    def __init__(self, vb: VisionBackend, labels: list[str] = TAG_LABELS):
        self.labels = labels
        self.mat = vb.embed_texts([f"a photo of {lab}" if not lab.startswith(("a ", "an ", "the ", "people"))
                                   else lab for lab in labels], prefix=vb.pair.query_prefix)

    def tags(self, img_vec: np.ndarray, k: int = 3, z_min: float = 3.3, spread: float = 1.0) -> list[tuple[str, float]]:
        """Conservative: a wrong tag is worse than no tag, because tags become searchable text.
        Calibrated on the synthetic set (bench/bench_vision.py): with nomic-q every correct top
        tag had z >= 3.49 and the only wrong one (a street map -> "chart") had z = 3.04. This is a
        heuristic from 12 images, not a validated precision guarantee."""
        s = self.mat @ img_vec
        mu, sd = float(s.mean()), float(s.std()) or 1.0
        z = (s - mu) / sd
        order = np.argsort(-z)[:k]
        top = float(z[order[0]])
        return [(self.labels[i], round(float(z[i]), 2)) for i in order if z[i] >= z_min and z[i] >= top - spread]


# ---- isolated worker entry point -------------------------------------------------------------
_worker_backend: VisionBackend | None = None
_worker_key: tuple | None = None


def embed_task(payload: dict) -> dict:
    """Runs inside an IsolatedPool worker: decode a batch of images and embed them. Returns
    float32 bytes per image (None for images that could not be decoded)."""
    global _worker_backend, _worker_key
    key = (payload["pair"], payload.get("device", "auto"), os.environ.get("LFI_MODELS"))
    if _worker_backend is None or _worker_key != key:
        _worker_backend = VisionBackend(VISION_PAIRS[payload["pair"]], device=payload.get("device", "auto"),
                                        batch_size=payload.get("batch", 0), load_text=False,
                                        threads=payload.get("threads") or _bg_threads())
        _worker_key = key
    imgs, idx, errors = [], [], {}
    for i, p in enumerate(payload["paths"]):
        try:
            imgs.append(load_image(p))
            idx.append(i)
        except Exception as e:
            errors[i] = f"{type(e).__name__}: {str(e)[:120]}"
    out: list = [None] * len(payload["paths"])
    if imgs:
        vecs = _worker_backend.embed_images(imgs)
        for j, i in enumerate(idx):
            out[i] = vecs[j].tobytes()
    from .sysinfo import rss_bytes

    return {"ok": True, "vecs": out, "errors": errors, "device": _worker_backend.device,
            "fallback": _worker_backend.fallback_reason, "worker_peak_rss": rss_bytes()[1]}


def download_pair(key: str) -> None:
    from fastembed import ImageEmbedding, TextEmbedding

    pair = VISION_PAIRS[key]
    with config.online_for_download():
        ImageEmbedding(model_name=pair.image_model, cache_dir=str(config.models_dir()), lazy_load=True)
        TextEmbedding(model_name=pair.text_model, cache_dir=str(config.models_dir()), lazy_load=True)


def pair_is_cached(key: str) -> bool:
    from fastembed import ImageEmbedding, TextEmbedding

    pair = VISION_PAIRS[key]
    try:
        ImageEmbedding(model_name=pair.image_model, cache_dir=str(config.models_dir()), local_files_only=True,
                       lazy_load=True)
        TextEmbedding(model_name=pair.text_model, cache_dir=str(config.models_dir()), local_files_only=True,
                      lazy_load=True)
        return True
    except Exception:
        return False
