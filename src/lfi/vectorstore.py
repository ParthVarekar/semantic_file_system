"""Vector storage on sqlite-vec ``vec0`` tables, with a NumPy brute-force fallback.

Personal-computer corpora (10^3 - 10^6 chunks) are well served by exact (flat) search, which
sqlite-vec performs in C inside the same database file as the metadata. If the extension
cannot load, vectors are stored as float32 blobs and searched with NumPy.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

import numpy as np

from . import db


class VectorStore:
    def __init__(self, conn: sqlite3.Connection, slug: str, dim: int, table: str):
        self.conn = conn
        self.dim = dim
        self.table = f"vec_{table}_{slug}"
        self.native = db.vec_available()
        self._cache: tuple[np.ndarray, np.ndarray] | None = None

    @staticmethod
    def _blob(v: np.ndarray) -> bytes:
        return np.ascontiguousarray(v, dtype=np.float32).tobytes()

    def add(self, ids: Iterable[int], vecs: np.ndarray) -> None:
        ids = list(ids)
        if not ids:
            return
        self.delete(ids)
        self.conn.executemany(
            f"INSERT INTO {self.table}(rowid, embedding) VALUES(?, ?)",
            [(int(i), self._blob(v)) for i, v in zip(ids, vecs)],
        )
        self._cache = None

    def delete(self, ids: Iterable[int]) -> None:
        ids = [int(i) for i in ids]
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            self.conn.execute(
                f"DELETE FROM {self.table} WHERE rowid IN ({','.join('?' * len(part))})", part
            )
        self._cache = None

    def get(self, ids: Iterable[int]) -> dict[int, np.ndarray]:
        ids = [int(i) for i in ids]
        out: dict[int, np.ndarray] = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            for r in self.conn.execute(
                f"SELECT rowid, embedding FROM {self.table} WHERE rowid IN ({','.join('?' * len(part))})", part
            ):
                out[r[0]] = np.frombuffer(r[1], dtype=np.float32)
        return out

    def all(self) -> tuple[np.ndarray, np.ndarray]:
        rows = self.conn.execute(f"SELECT rowid, embedding FROM {self.table}").fetchall()
        if not rows:
            return np.zeros(0, dtype=np.int64), np.zeros((0, self.dim), dtype=np.float32)
        ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
        mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), self.dim)
        return ids, mat

    def count(self) -> int:
        return int(self.conn.execute(f"SELECT count(*) FROM {self.table}").fetchone()[0])

    def knn_among(self, q: np.ndarray, ids: Iterable[int], k: int = 50) -> list[tuple[int, float]]:
        """Exact nearest neighbours restricted to ``ids`` (used when search filters narrow the
        candidates; an unrestricted top-k followed by filtering could miss every match)."""
        vecs = self.get(ids)
        if not vecs:
            return []
        keys = list(vecs)
        sims = np.vstack([vecs[i] for i in keys]) @ np.asarray(q, dtype=np.float32)
        top = np.argsort(-sims)[:k]
        return [(int(keys[i]), float(sims[i])) for i in top]

    def knn(self, q: np.ndarray, k: int = 50) -> list[tuple[int, float]]:
        """Return ``(rowid, cosine_similarity)`` for the ``k`` nearest vectors."""
        k = max(1, min(int(k), 4096))
        if self.native:
            rows = self.conn.execute(
                f"SELECT rowid, distance FROM {self.table} WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (self._blob(q), k),
            ).fetchall()
            return [(int(r[0]), 1.0 - float(r[1])) for r in rows]
        if self._cache is None:
            self._cache = self.all()
        ids, mat = self._cache
        if len(ids) == 0:
            return []
        sims = mat @ np.asarray(q, dtype=np.float32)
        top = np.argsort(-sims)[:k]
        return [(int(ids[i]), float(sims[i])) for i in top]
