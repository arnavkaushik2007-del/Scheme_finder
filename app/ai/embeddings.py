"""Text embeddings for retrieval.

* ``E5Embedder``: intfloat/multilingual-e5-small (Hindi + English in one vector space) via
  sentence-transformers. Used by default when the package is installed.
* ``HashEmbedder``: dependency-free fallback (character n-gram hashing). Fine for exact-ish keyword
  matches, weak across languages. The assistant status endpoint says which one is active.

Each embedder carries its own ``abstain_below`` similarity: below it, retrieval is treated as
"nothing relevant found" and the assistant declines.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
from typing import Protocol

from app.config import get_settings

log = logging.getLogger(__name__)


class Embedder(Protocol):
    name: str
    dim: int
    abstain_below: float
    strong_above: float

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class E5Embedder:
    dim = 384
    # Calibrated on app/data/eval/rag_questions.json: relevant questions scored >= 0.852,
    # off-topic ones <= 0.811 (multilingual-e5-small similarities are compressed into ~0.7-0.95).
    abstain_below = 0.83
    strong_above = 0.88

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self._model = SentenceTransformer(model_name)

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode([f"passage: {t}" for t in texts], normalize_embeddings=True, batch_size=32)
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([f"query: {text}"], normalize_embeddings=True)[0].tolist()


class HashEmbedder:
    name = "hash-ngram-512"
    dim = 512
    abstain_below = 0.20
    strong_above = 0.40

    @staticmethod
    def _features(text: str) -> list[str]:
        from app.ai.text import tokens

        words = tokens(text)
        feats = list(words) * 2  # whole words count more than their trigrams
        for w in words:
            padded = f" {w} "
            feats += [padded[i:i + 3] for i in range(len(padded) - 2)]
        return feats

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for f in self._features(text):
            h = int(hashlib.md5(f.encode()).hexdigest()[:8], 16)
            v[h % self.dim] += 1.0 if (h >> 9) & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


_embedder: Embedder | None = None
_lock = threading.Lock()


def get_embedder() -> Embedder:
    global _embedder
    with _lock:
        if _embedder is None:
            s = get_settings()
            if s.embedding_backend in ("auto", "e5"):
                try:
                    _embedder = E5Embedder(s.embedding_model)
                except Exception as exc:  # noqa: BLE001 - package or model missing
                    if s.embedding_backend == "e5":
                        raise
                    log.warning("multilingual embedding model unavailable (%s); using hash embedder",
                                type(exc).__name__)
                    _embedder = HashEmbedder()
            else:
                _embedder = HashEmbedder()
            log.info("embedder: %s", _embedder.name)
        return _embedder


def set_embedder(e: Embedder | None) -> None:
    global _embedder
    _embedder = e
