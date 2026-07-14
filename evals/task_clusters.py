"""S3 task clustering — find the recurring operations in routed traffic
(step S3 of the SLM-agents conversion algorithm, arXiv 2506.02153 §6; the
clustering-first methodology echoes DLER's CLIMB).

Stdlib-only by design (repo constraint + CI has no model endpoint): TF-IDF
vectors over the task texts, cosine similarity, deterministic greedy
agglomerative clustering (each text joins the best existing cluster above a
similarity threshold, else starts its own). An embeddings-based vectorizer
(oMLX /v1/embeddings) can slot in later behind the same interface; at current
traffic sizes (tens of tasks) TF-IDF separates topics adequately.

`report(events)` answers the R1 follow-up question directly: is the
mega-session a recurring TYPE (a cluster worth optimizing for) or a one-off?
Per cluster: task count, distinct-session imputed spend, top terms, example.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_WORD = re.compile(r"[a-z][a-z0-9_.\-]{1,}")
_STOP = frozenset(
    "the a an and or of in to for on with is are was be this that it as at by "
    "from i you we they can could would should want your my our me".split()
)


def _tokens(text: str) -> list:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def _tfidf_vectors(texts: list) -> list:
    docs = [_tokens(t) for t in texts]
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    n = len(docs)
    vecs = []
    for d in docs:
        if not d:
            vecs.append({})
            continue
        tf = Counter(d)
        v = {w: (c / len(d)) * (math.log((1 + n) / (1 + df[w])) + 1.0) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({w: x / norm for w, x in v.items()})
    return vecs


def _cos(a: dict, b: dict) -> float:
    if len(b) < len(a):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def cluster_texts(texts: list, threshold: float = 0.35) -> list:
    """Deterministic greedy agglomerative clustering. Returns a list of
    clusters, each a list of indices into `texts` (insertion-ordered)."""
    vecs = _tfidf_vectors(texts)
    clusters: list = []
    for i, v in enumerate(vecs):
        best_ci, best_sim = None, threshold
        for ci, members in enumerate(clusters):
            sim = max(_cos(v, vecs[j]) for j in members)
            if sim >= best_sim:
                best_ci, best_sim = ci, sim
        if best_ci is None:
            clusters.append([i])
        else:
            clusters[best_ci].append(i)
    return clusters


def report(events: list, threshold: float = 0.35) -> dict:
    """Cluster all distinct route_decision task texts and attach per-cluster
    session spend (imputed) via session_id, with a task-text prefix fallback."""
    rows: list = []
    seen: set = set()
    for e in events:
        if e.get("event") != "route_decision" or not e.get("task_text"):
            continue
        if e["task_text"] in seen:
            continue
        seen.add(e["task_text"])
        rows.append(e)

    usd_by_sid: dict = {}
    sid_by_task: list = []
    for e in events:
        if e.get("event") != "usage":
            continue
        sid = e.get("session_id")
        if not sid:
            continue
        usd_by_sid[sid] = usd_by_sid.get(sid, 0.0) + float(e.get("imputed_usd") or 0.0)
        if e.get("task_text"):
            sid_by_task.append((e["task_text"], sid))

    def _sid_for(row: dict):
        if row.get("session_id"):
            return row["session_id"]
        task = row["task_text"]
        for t, sid in sid_by_task:
            if t.startswith(task[:200]) or task.startswith(t[:200]):
                return sid
        return None

    texts = [r["task_text"] for r in rows]
    out = []
    for members in cluster_texts(texts, threshold=threshold):
        sids = {s for s in (_sid_for(rows[j]) for j in members) if s}
        terms = Counter()
        tiers: Counter = Counter()
        for j in members:
            terms.update(_tokens(texts[j]))
            tiers[rows[j].get("complexity", "?")] += 1
        out.append({
            "n_tasks": len(members),
            "n_sessions": len(sids),
            "session_usd": round(sum(usd_by_sid.get(s, 0.0) for s in sids), 4),
            "tiers": dict(tiers),
            "top_terms": [w for w, _ in terms.most_common(5)],
            "example": texts[members[0]][:90],
        })
    out.sort(key=lambda c: c["session_usd"], reverse=True)
    return {"n_tasks": len(texts), "n_clusters": len(out), "clusters": out}
