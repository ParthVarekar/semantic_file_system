"""Worker tasks used to prove crash/hang isolation (run inside spawned worker processes)."""

import os
import time


def crash(payload):
    os._exit(3)  # simulates a native crash in a parser


def hang(payload):
    time.sleep(payload.get("seconds", 60))
    return {"ok": True}


def echo(payload):
    return {"ok": True, "value": payload.get("value")}


def broken_vision(payload):
    """Image worker that cannot work at all this time (e.g. not enough memory for the model)."""
    return {"ok": False, "error": "MemoryError: simulated: not enough memory to load the image model"}


def fake_vision(payload):
    """Stand-in for lfi.vision.embed_task: deterministic vectors from file bytes; a file whose
    name contains CRASH kills the worker (simulates a decoder crash on a malicious image)."""
    import hashlib

    import numpy as np

    out = []
    for p in payload["paths"]:
        if "CRASH" in os.path.basename(p):
            os._exit(3)
        seed = int.from_bytes(hashlib.blake2b(open(p, "rb").read(), digest_size=8).digest(), "little")
        v = np.random.default_rng(seed).standard_normal(768).astype(np.float32)
        out.append((v / np.linalg.norm(v)).tobytes())
    return {"ok": True, "vecs": out, "errors": {}, "device": "cpu", "fallback": None}


def fake_embed(payload):
    """Stand-in for lfi.embeddings.embed_task: the deterministic hashing model, in a worker process."""
    import numpy as np

    from lfi.embeddings import HashingBackend

    v = HashingBackend().embed_documents(payload["texts"])
    return {"ok": True, "vecs": np.ascontiguousarray(v, dtype=np.float32).tobytes(), "dim": int(v.shape[1]),
            "device": "directml"}


def crashing_embed(payload):
    """An embedding worker that dies every time (driver crash, out of GPU memory...)."""
    os._exit(7)
