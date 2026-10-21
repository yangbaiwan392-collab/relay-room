"""Embedding similarity with graceful degradation.

Primary: Ollama `/api/embed` with an embedding model (e.g. nomic-embed-text).
Fallback: hashed character-trigram vectors, so the supervisor never hard-fails if the
embedding model is absent. The fallback is labelled in every result it produces.
"""
from __future__ import annotations

import hashlib
import json
import math
import urllib.request

OLLAMA = "http://127.0.0.1:11434"
DIM = 512


def _hashed(text: str) -> list[float]:
    v = [0.0] * DIM
    t = " ".join(text.lower().split())
    for i in range(len(t) - 2):
        h = int(hashlib.md5(t[i:i + 3].encode()).hexdigest()[:8], 16)
        v[h % DIM] += 1.0
    return v


def embed(texts: list[str], model: str | None, *, timeout: int = 60) -> tuple[list[list[float]], str]:
    """Return (vectors, method). method is 'ollama:<model>' or 'hashed-fallback'."""
    if model:
        try:
            payload = {"model": model, "input": texts}
            req = urllib.request.Request(
                OLLAMA + "/api/embed", data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                doc = json.loads(r.read().decode())
            vecs = doc.get("embeddings") or []
            if len(vecs) == len(texts) and vecs and vecs[0]:
                return vecs, f"ollama:{model}"
        except Exception:
            pass
    return [_hashed(t) for t in texts], "hashed-fallback"


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return num / (na * nb)


def mean_pairwise(vecs: list[list[float]]) -> float:
    if len(vecs) < 2:
        return 1.0
    sims = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            sims.append(cosine(vecs[i], vecs[j]))
    return sum(sims) / len(sims) if sims else 1.0


def skeleton(text: str, n: int = 4) -> set[str]:
    """Content-free structure: keep digits/punctuation shape, mask Chinese chars.

    Two sentences built from the same template ('嘿老张，我试了个新招：X吧——每月N块…')
    produce the same skeleton even though their nouns differ.
    """
    masked = "".join("字" if "\u4e00" <= ch <= "\u9fff" else ch for ch in text)
    masked = "".join(ch for ch in masked if not ch.isascii() or ch.isdigit() or ch in "，。！？、…—")
    return {masked[i:i + n] for i in range(len(masked) - n + 1)}


def skeleton_jaccard(a: str, b: str, n: int = 4) -> float:
    sa, sb = skeleton(a, n), skeleton(b, n)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
