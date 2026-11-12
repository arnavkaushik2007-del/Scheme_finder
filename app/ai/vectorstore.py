"""Vector store: ChromaDB (local, persistent) or an in-memory store for tests."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class Hit:
    id: str
    text: str
    meta: dict[str, Any]
    score: float  # cosine similarity, higher is better


class VectorStore(Protocol):
    name: str

    def reset(self) -> None: ...

    def add(self, ids: list[str], texts: list[str], vectors: list[list[float]], metas: list[dict[str, Any]]) -> None: ...

    def query(self, vector: list[float], k: int, where: dict[str, Any] | None = None) -> list[Hit]: ...

    def count(self) -> int: ...


class MemoryStore:
    name = "memory"

    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, list[float], dict[str, Any]]] = {}

    def reset(self) -> None:
        self.rows.clear()

    def add(self, ids, texts, vectors, metas) -> None:  # type: ignore[no-untyped-def]
        for i, t, v, m in zip(ids, texts, vectors, metas, strict=True):
            self.rows[i] = (t, v, m)

    def query(self, vector, k, where=None) -> list[Hit]:  # type: ignore[no-untyped-def]
        out = []
        for i, (t, v, m) in self.rows.items():
            if where and any(m.get(key) != val for key, val in where.items()):
                continue
            dot = sum(a * b for a, b in zip(vector, v, strict=False))
            na = math.sqrt(sum(a * a for a in vector)) or 1.0
            nb = math.sqrt(sum(b * b for b in v)) or 1.0
            out.append(Hit(i, t, m, dot / (na * nb)))
        return sorted(out, key=lambda h: -h.score)[:k]

    def count(self) -> int:
        return len(self.rows)


class ChromaStore:
    name = "chroma"
    COLLECTION = "scheme_chunks"

    def __init__(self, path: str) -> None:
        import chromadb

        self._client = chromadb.PersistentClient(path=path)
        self._col = self._get()

    def _get(self) -> Any:
        return self._client.get_or_create_collection(self.COLLECTION, metadata={"hnsw:space": "cosine"},
                                                     embedding_function=None)

    def reset(self) -> None:
        try:
            self._client.delete_collection(self.COLLECTION)
        except Exception:  # noqa: BLE001 - did not exist
            pass
        self._col = self._get()

    def add(self, ids, texts, vectors, metas) -> None:  # type: ignore[no-untyped-def]
        for start in range(0, len(ids), 256):
            sl = slice(start, start + 256)
            self._col.upsert(ids=ids[sl], documents=texts[sl], embeddings=vectors[sl], metadatas=metas[sl])

    def query(self, vector, k, where=None) -> list[Hit]:  # type: ignore[no-untyped-def]
        if self._col.count() == 0:
            return []
        if where and len(where) > 1:
            where = {"$and": [{key: val} for key, val in where.items()]}
        res = self._col.query(query_embeddings=[vector], n_results=min(k, self._col.count()), where=where or None)
        return [Hit(i, d, m, 1.0 - dist) for i, d, m, dist in
                zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0], strict=True)]

    def count(self) -> int:
        return self._col.count()


_store: VectorStore | None = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        s = get_settings()
        if s.vector_backend == "chroma":
            try:
                _store = ChromaStore(s.chroma_dir)
            except Exception as exc:  # noqa: BLE001
                log.warning("ChromaDB unavailable (%s); using in-memory vector store", type(exc).__name__)
                _store = MemoryStore()
        else:
            _store = MemoryStore()
        log.info("vector store: %s", _store.name)
    return _store


def set_store(s: VectorStore | None) -> None:
    global _store
    _store = s
