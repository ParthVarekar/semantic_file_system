"""Replaceable embedding backends.

``EmbeddingBackend`` is the only interface the indexer and search use, so the model or the
runtime can change without touching them. Implementations:

* ``FastEmbedBackend`` - ONNX Runtime models via Qdrant's fastembed (default). Runs fully
  offline once the model is cached (``local_files_only=True``); GPU via DirectML or CUDA
  execution providers when installed, with automatic CPU fallback.
* ``OllamaBackend`` - a locally running Ollama server (``/api/embed``), loopback only.
* ``HashingBackend`` - deterministic feature hashing, no download. Used by the test suite and
  never presented as semantic search.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from . import config

log = logging.getLogger(__name__)

try:  # fastembed logs model-cache misses at ERROR level via loguru; we report them ourselves
    from loguru import logger as _loguru

    _loguru.disable("fastembed")
except Exception:
    pass


@dataclass(frozen=True)
class ModelInfo:
    name: str
    dim: int
    size_mb: int
    license: str
    languages: str
    note: str
    query_prefix: str = ""
    doc_prefix: str = ""
    max_chars: int = 2000  # characters per chunk passed to the model
    gpu_recommended: bool = False


CATALOG: dict[str, ModelInfo] = {
    m.name: m
    for m in [
        ModelInfo("BAAI/bge-small-en-v1.5", 384, 67, "MIT", "English",
                  "Default. Fast on CPU, strong English retrieval quality for its size.",
                  query_prefix="Represent this sentence for searching relevant passages: "),
        ModelInfo("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 384, 220, "Apache-2.0",
                  "50+ languages", "Multilingual, CPU-friendly.", max_chars=1000),
        ModelInfo("minishlab/potion-base-8M", 256, 30, "MIT", "English",
                  "Static (Model2Vec) embeddings: extremely fast, lower quality. For slow machines."),
        ModelInfo("nomic-ai/nomic-embed-text-v1.5", 768, 520, "Apache-2.0", "English",
                  "Higher quality, long context; slower on CPU.",
                  query_prefix="search_query: ", doc_prefix="search_document: "),
        ModelInfo("Qwen/Qwen3-Embedding-0.6B-Q", 1024, 1120, "Apache-2.0", "100+ languages",
                  "Best quality, multilingual and code-aware; int8, runs on the CPU (DirectML has no int8 "
                  "support) - slow for large libraries.",
                  query_prefix="Instruct: Given a search query, retrieve relevant files and documents\nQuery:",
                  max_chars=3000, gpu_recommended=True),
    ]
}


class EmbeddingBackend(Protocol):
    model_id: str
    dim: int
    device: str

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class ModelUnavailable(RuntimeError):
    """Model weights are not cached locally (offline) or the runtime failed to load."""


def _normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def _gpu_providers() -> list[str]:
    try:
        import onnxruntime as ort

        avail = ort.get_available_providers()
    except Exception:
        return []
    return [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider") if p in avail]


class FastEmbedBackend:
    def __init__(self, model: str, device: str = "auto", threads: int = 0, batch_size: int = 32,
                 allow_download: bool = False):
        self.model_id = model
        self.info = CATALOG.get(model)
        self.batch_size = batch_size
        self._lock = threading.Lock()  # one ONNX session; serialize access from UI threads
        self.fallback_reason: str | None = None
        from fastembed import TextEmbedding

        kwargs: dict = {"cache_dir": str(config.models_dir()), "local_files_only": not allow_download}
        if threads:
            kwargs["threads"] = threads
        self._kwargs = kwargs
        gpu = _gpu_providers() if device in ("auto", "gpu") else []
        if self.info is not None and self.info.name.endswith("-Q") and gpu == ["DmlExecutionProvider"]:
            # Verified on RTX 5050 / ORT-DirectML 1.24: int8 Qwen3 graph fails at inference time.
            gpu = []
            self.fallback_reason = "int8 model is not supported by DirectML; using CPU"
        attempts: list[tuple[str, list | None]] = []
        if gpu:
            attempts.append((gpu[0], [gpu[0], "CPUExecutionProvider"]))
        attempts.append(("CPUExecutionProvider", ["CPUExecutionProvider"]))
        last_err: Exception | None = None
        for label, providers in attempts:
            try:
                self._model = TextEmbedding(model_name=model, providers=providers, **kwargs)
                self.device = {"CUDAExecutionProvider": "cuda", "DmlExecutionProvider": "directml"}.get(label, "cpu")
                if label == "CPUExecutionProvider" and device == "gpu":
                    self.fallback_reason = "no GPU execution provider available; using CPU"
                break
            except Exception as e:
                last_err = e
                if label != "CPUExecutionProvider":
                    self.fallback_reason = f"GPU init failed ({type(e).__name__}); using CPU"
                    log.warning("GPU embedding init failed, falling back to CPU: %s", e)
        else:
            raise ModelUnavailable(_explain_load_error(model, last_err))
        probe = next(iter(self._model.embed(["probe"], batch_size=1)))
        self.dim = int(len(probe))
        if self.device != "cpu":
            self._verify_gpu_session()
        self._auto_batch = batch_size <= 0
        self._set_batch()

    def _set_batch(self) -> None:
        # Measured on a 10-core laptop CPU (bge-small, 2000 files): batch 8 = 435 MB peak and fastest;
        # batch 32 = 1.2 GB peak and ~30% slower. GPUs prefer larger batches.
        if self._auto_batch:
            self.batch_size = 32 if self.device != "cpu" else 8

    def _verify_gpu_session(self) -> None:
        sess = getattr(getattr(self._model, "model", None), "model", None)
        try:
            used = sess.get_providers() if sess is not None else []
        except Exception:
            used = []
        if used and used[0] == "CPUExecutionProvider":
            self.device = "cpu"
            self.fallback_reason = "GPU provider not used by ONNX Runtime; running on CPU"

    def _prep(self, texts: list[str], prefix: str) -> list[str]:
        limit = self.info.max_chars if self.info else 2000
        return [prefix + t[:limit] for t in texts]

    def _run(self, texts: list[str], batch_size: int) -> list[np.ndarray]:
        with self._lock:
            try:
                return list(self._model.embed(texts, batch_size=min(batch_size, self.batch_size)))
            except Exception as e:
                if self.device == "cpu":
                    raise
                # GPU kernels can fail on specific graphs/drivers at run time: rebuild on CPU once.
                from fastembed import TextEmbedding

                log.warning("GPU inference failed (%s); switching embedding to CPU", e)
                self._model = TextEmbedding(model_name=self.model_id, providers=["CPUExecutionProvider"],
                                            **self._kwargs)
                self.device = "cpu"
                self._set_batch()
                self.fallback_reason = f"GPU inference failed ({type(e).__name__}); using CPU"
                return list(self._model.embed(texts, batch_size=batch_size))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        prefix = self.info.doc_prefix if self.info else ""
        prepped = self._prep(texts, prefix)
        # Batch similar lengths together: every sequence in a batch is padded to the longest one,
        # so mixing short file descriptors with long chunks wastes compute and attention memory.
        order = sorted(range(len(prepped)), key=lambda i: len(prepped[i]))
        vecs = self._run([prepped[i] for i in order], self.batch_size)
        out = np.empty((len(prepped), self.dim), dtype=np.float32)
        for pos, i in enumerate(order):
            out[i] = vecs[pos]
        return _normalize(out)

    def embed_query(self, text: str) -> np.ndarray:
        prefix = self.info.query_prefix if self.info else ""
        return _normalize(np.asarray(self._run(self._prep([text], prefix), 1)[0]))


def _explain_load_error(model: str, err: Exception | None) -> str:
    msg = str(err) if err else "unknown error"
    if any(t in msg for t in ("local_files_only", "Could not find", "from any source")) or "offline" in msg.lower():
        return (f"Embedding model '{model}' is not downloaded. Open Settings > Embedding model and click "
                f"'Download selected model' (one-time, needs internet), or run: lfi models download \"{model}\"")
    return f"Could not load embedding model '{model}': {msg[:300]}"


def download_model(model: str) -> str:
    """Explicit, user-initiated download into the app's model cache. Returns cache path."""
    from fastembed import TextEmbedding

    with config.online_for_download():
        TextEmbedding(model_name=model, cache_dir=str(config.models_dir()), local_files_only=False)
    return str(config.models_dir())


def model_is_cached(model: str) -> bool:
    try:
        from fastembed import TextEmbedding

        TextEmbedding(model_name=model, cache_dir=str(config.models_dir()), local_files_only=True,
                      lazy_load=True)
        return True
    except Exception:
        return False


class OllamaBackend:
    """Uses a local Ollama server. Only loopback URLs are accepted (privacy)."""

    def __init__(self, model: str, url: str = "http://127.0.0.1:11434", timeout: float = 120):
        host = urllib.request.urlparse(url).hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ModelUnavailable("Ollama URL must point to this computer (127.0.0.1 / localhost).")
        self.model_id = f"ollama:{model}"
        self._model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.device = "ollama"
        self.fallback_reason = None
        try:
            self.dim = int(self._call(["probe"]).shape[1])
        except Exception as e:
            raise ModelUnavailable(f"Ollama not reachable or model '{model}' missing: {e}") from e

    def _call(self, texts: list[str]) -> np.ndarray:
        body = json.dumps({"model": self._model, "input": texts}).encode()
        req = urllib.request.Request(self.url + "/api/embed", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        return _normalize(np.asarray(data["embeddings"], dtype=np.float32))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        out = [self._call(texts[i:i + 32]) for i in range(0, len(texts), 32)]
        return np.vstack(out) if out else np.zeros((0, self.dim), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._call([text])[0]


_TOKEN = re.compile(r"[a-z0-9]+")


class HashingBackend:
    """Deterministic bag-of-words feature hashing (no model). Test/diagnostic use only."""

    def __init__(self, dim: int = 256):
        self.model_id = f"hashing-{dim}"
        self.dim = dim
        self.device = "cpu"
        self.fallback_reason = None

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        toks = _TOKEN.findall(text.lower())
        feats = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
        for t in feats:
            h = int.from_bytes(hashlib.blake2b(t.encode(), digest_size=8).digest(), "little")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        return v

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _normalize(np.vstack([self._vec(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(self._vec(text))


def create_backend(settings: config.Settings, allow_download: bool = False) -> EmbeddingBackend:
    t0 = time.perf_counter()
    if settings.embedding_backend == "hashing":
        b: EmbeddingBackend = HashingBackend()
    elif settings.embedding_backend == "ollama":
        b = OllamaBackend(settings.embedding_model, settings.ollama_url)
    else:
        b = FastEmbedBackend(settings.embedding_model, device=settings.device,
                             threads=settings.embed_threads, batch_size=settings.embed_batch_size,
                             allow_download=allow_download)
    log.info("embedding backend %s on %s ready in %.1fs", b.model_id, b.device, time.perf_counter() - t0)
    return b


_worker_model: tuple[tuple, "FastEmbedBackend"] | None = None


def embed_task(payload: dict) -> dict:
    """Worker-process entry point for parallel embedding: one model session per process (two
    DirectML sessions in one process crash ONNX Runtime). The model stays loaded between tasks."""
    global _worker_model
    key = (payload["model"], payload.get("device", "auto"), payload.get("batch", 0), os.environ.get("LFI_MODELS"))
    if _worker_model is None or _worker_model[0] != key:
        config.enforce_offline_env()
        _worker_model = (key, FastEmbedBackend(payload["model"], device=key[1], batch_size=key[2]))
    b = _worker_model[1]
    vecs = b.embed_documents(payload["texts"])
    return {"ok": True, "vecs": np.ascontiguousarray(vecs, dtype=np.float32).tobytes(), "dim": int(vecs.shape[1]),
            "device": b.device}
