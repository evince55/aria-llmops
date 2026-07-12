"""Semantic k-NN tier classifier using the local embedding model.

Embeds a labeled reference set once, then classifies a new task by cosine
similarity to the reference examples (top-k, similarity-weighted vote). A
third classification strategy alongside the keyword and 9B-hybrid classifiers;
score it on the same labeled set with:

    router_classification_eval.evaluate(dataset, classify=clf.classify)

Stdlib-only (pure-Python cosine — the runtime has no numpy). Needs the local
embedding server (llama-swap /v1/embeddings), same external dependency as the
9B-hybrid path. Env: EMBED_BASE_URL, EMBED_MODEL, EMBED_KNN_K.
"""
from __future__ import annotations

import json
import math
import os
import urllib.request
from pathlib import Path

EMBED_URL = os.environ.get("EMBED_BASE_URL", "http://localhost:8080/v1").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "embed-qwen3")
DEFAULT_K = int(os.environ.get("EMBED_KNN_K", "5"))


def embed(texts: list[str], timeout: float = 180.0) -> list[list[float]]:
    """Embed a batch of texts; returns vectors in input order."""
    body = json.dumps({"model": EMBED_MODEL, "input": texts}).encode("utf-8")
    req = urllib.request.Request(EMBED_URL + "/embeddings", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def load_dataset(path) -> list:
    rows = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


class EmbeddingClassifier:
    """k-NN over embedded reference examples. `reference` is a list of
    {"task", "expected_tier"}. Embeds the reference set once at construction."""

    def __init__(self, reference: list, k: int = DEFAULT_K):
        self.k = k
        self.tiers = [r["expected_tier"] for r in reference]
        self.tasks = [r["task"] for r in reference]
        self.ref_vecs = embed(self.tasks) if reference else []

    def classify(self, task: str) -> str:
        if not self.ref_vecs:
            return "MODERATE"
        v = embed([task])[0]
        sims = sorted(((_cosine(v, rv), tier) for rv, tier in zip(self.ref_vecs, self.tiers)),
                      reverse=True)
        scores: dict = {}
        for sim, tier in sims[:self.k]:
            scores[tier] = scores.get(tier, 0.0) + sim   # similarity-weighted vote
        return max(scores, key=scores.get)
