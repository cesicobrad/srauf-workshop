"""Hybrid search over a bot's chunks: keyword BM25 plus multilingual embeddings, merged by reciprocal rank.

Why both: BM25 nails exact words (prices, "tubeless", opening hours) and costs nothing; embeddings find a
Slovenian answer for a Croatian question. Each chunk gets one vector for the whole text and one per line, and
scores as its best match, so one row of a long price table still matches a short question. Embeddings run locally (fastembed, ONNX, ~220 MB model, no GPU),
so retrieval has no API cost. With EMBEDDINGS=off the bot falls back to BM25 alone.
"""
from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass

import numpy as np

from .db import Chunk

log = logging.getLogger("chatbot.search")

STOP = set("""
a an the and or but if of to in on at by for with from as is are was were be been do does did i me my we our you
your it its this that these those there here what which who how when where why can could should would will may
have has had not no so than then too very just about into any some all more most other only also get please hi
hello hey thanks thank much many
in je so sem si smo ste pa za na v s z k od do se ga jo mi vi lahko kako kaj kje kdaj ali tudi še že ki da ne
prosim hvala živjo zdravo pozdravljeni imate
i u na za od do se je su sam si smo ste li da ne što šta gdje gde kada kako koji koja koje može možete molim
hvala bok zdravo imate ima
""".split())
_TOKEN = re.compile(r"[a-zà-žčćđšž0-9]+")


def stem(word: str) -> str:
    """Crude but language-neutral: keep the first 5 letters, so Slavic case endings mostly match
    (kolesa, kolesom, kolesu -> "koles") and English plurals do too."""
    if word.isdigit():
        return word
    for suffix in ("ings", "ing", "ies", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[: -len(suffix)]
            break
    return word[:5] if len(word) > 5 else word


def tokens(text: str) -> list[str]:
    text = text.lower().replace("e-bike", "ebike").replace("wi-fi", "wifi")
    return [stem(w) for w in _TOKEN.findall(text) if w not in STOP and (len(w) > 1 or w.isdigit())]


class Embedder:
    """fastembed wrapper; loads the model on first use (a few seconds) and keeps it."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                self._model = TextEmbedding(self.model_name)
        return self._model

    @property
    def dim(self) -> int:
        return int(self.embed(["dim"]).shape[1])

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = np.array(list(self._load().embed(texts)), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.maximum(norms, 1e-9)


@dataclass
class Hit:
    chunk: Chunk
    score: float        # fused RRF score, only meaningful for ordering
    bm25: float
    cosine: float       # 0 when embeddings are off


class Index:
    """In-memory index of one bot's chunks. Rebuilt when the bot's documents change."""

    def __init__(self, chunks: list[Chunk], embedder: Embedder | None):
        self.chunks = chunks
        self.embedder = embedder
        self.docs = [Counter(tokens(f"{c.doc_title} {c.heading} {c.heading} {c.text}")) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if chunks else 1.0
        df = Counter(t for d in self.docs for t in d)
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.matrix = None      # all vectors of all chunks, stacked
        self.owner = None       # chunk index of each row in `matrix`
        if embedder and chunks and all(c.embedding for c in chunks):
            dim = embedder.dim
            blocks = [np.frombuffer(c.embedding, dtype=np.float32).reshape(-1, dim) for c in chunks]
            self.matrix = np.vstack(blocks)
            self.owner = np.concatenate([np.full(len(b), i) for i, b in enumerate(blocks)])

    def bm25(self, query: str) -> np.ndarray:
        q = list(dict.fromkeys(tokens(query)))
        scores = np.zeros(len(self.chunks), dtype=np.float32)
        k1, b = 1.4, 0.5
        for i, (counts, length) in enumerate(zip(self.docs, self.lengths)):
            s = 0.0
            for t in q:
                tf = counts.get(t, 0)
                if tf:
                    s += self.idf[t] * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / self.avg))
            scores[i] = s
        return scores

    def search(self, query: str, k: int = 6) -> list[Hit]:
        if not self.chunks:
            return []
        bm = self.bm25(query)
        cos = np.zeros(len(self.chunks), dtype=np.float32)
        rankings = [np.argsort(-bm, kind="stable")]
        if self.matrix is not None:
            try:
                qv = self.embedder.embed([query])[0]
                sims = self.matrix @ qv
                cos = np.full(len(self.chunks), -1.0, dtype=np.float32)
                np.maximum.at(cos, self.owner, sims)
                rankings.append(np.argsort(-cos, kind="stable"))
            except Exception as e:  # model files missing, etc.: keyword search still works
                log.warning("embedding search failed, using keywords only: %s", e)
        fused = np.zeros(len(self.chunks), dtype=np.float32)
        for order in rankings:
            for rank, idx in enumerate(order):
                if order is rankings[0] and bm[idx] <= 0:
                    continue  # BM25 only votes for chunks that share a word with the question
                fused[idx] += 1.0 / (60 + rank)
        order = np.argsort(-fused, kind="stable")[:k]
        return [Hit(self.chunks[i], float(fused[i]), float(bm[i]), float(cos[i])) for i in order if fused[i] > 0]


class SearchService:
    """Caches one Index per bot; `invalidate` after uploads or deletes."""

    def __init__(self, db, embedder: Embedder | None):
        self.db = db
        self.embedder = embedder
        self._cache: dict[str, Index] = {}
        self._lock = threading.Lock()

    def index(self, bot_id: str) -> Index:
        with self._lock:
            if bot_id not in self._cache:
                self._cache[bot_id] = Index(self.db.chunks(bot_id), self.embedder)
            return self._cache[bot_id]

    def invalidate(self, bot_id: str) -> None:
        with self._lock:
            self._cache.pop(bot_id, None)

    def search(self, bot_id: str, query: str, k: int = 6) -> list[Hit]:
        return self.index(bot_id).search(query, k)

    def context(self, bot_id: str, query: str, k: int = 8, full_chars: int = 0) -> list[Hit]:
        """The chunks to give Claude. A small knowledge base goes in whole, best matches first, because
        a small embedding model misses some cross-language matches ("veriga" = chain) and the whole thing
        costs a fraction of a cent. Bigger ones get the top `k`."""
        index = self.index(bot_id)
        total = sum(len(c.text) for c in index.chunks)
        if not full_chars or total > full_chars:
            return index.search(query, k)
        hits = index.search(query, len(index.chunks))
        seen = {h.chunk.id for h in hits}
        return hits + [Hit(c, 0.0, 0.0, 0.0) for c in index.chunks if c.id not in seen]

    def embed_chunks(self, chunks: list[tuple[str, str, str]]) -> list[bytes | None]:
        """(doc title, heading, text) -> one blob per chunk: the whole-chunk vector, then one per line."""
        if not self.embedder or not chunks:
            return [None] * len(chunks)
        out = []
        for title, heading, text in chunks:
            units = [f"{title}. {heading}\n{text}"]
            for line in re.split(r"\n+|(?<=[.!?])\s+", text):
                line = line.strip(" -|*")
                if len(line) >= 12 and len(units) < 40:
                    units.append(f"{heading}: {line}")
            out.append(self.embedder.embed(units).astype(np.float32).tobytes())
        return out
