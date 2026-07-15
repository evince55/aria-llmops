"""Flywheel export — steps S1/S2 of the SLM-agents conversion algorithm
(arXiv 2506.02153 §6): turn logged routing traffic into candidate training
pairs for tier-classifier fine-tuning.

Joins route_decision events to per-session outcomes (session_id primary;
task-text prefix fallback for legacy events logged before session_id existed)
and emits deduplicated pairs. Pairs whose task text appears in the labeled
eval datasets are QUARANTINED — those 42 rows are the held-out measurement
instrument and must never become training data.

Pairs may carry outcome=None (session unlabeled or unjoined): they are still
useful as distillation inputs (S5 lets the teacher label them) and are kept,
clearly marked, rather than silently dropped.
"""
from __future__ import annotations

import json
from pathlib import Path

_DATASET_DIR = Path(__file__).resolve().parents[1] / "evals" / "datasets"
# Prefix length for the fallback join: both sides store the clipped first
# prompt, so a generous prefix identifies the same session without demanding
# byte-identical clipping.
_PREFIX = 200


def _eval_task_texts() -> set:
    texts = set()
    for p in sorted(_DATASET_DIR.glob("labeled_tasks*.jsonl")):
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    texts.add(json.loads(line)["task"])
    return texts


def _keyword_confidence():
    """Lazy keyword classifier (deterministic, offline): task -> (tier, matched).
    Built once per export; import deferred so flywheel stays importable without
    the router's module-level env reads in exotic embeddings."""
    from llmops import ModelRouter
    router = ModelRouter(log_decisions=False)
    return router.classify_detailed


def export_pairs(events: list, include_quarantined: bool = False,
                 classify=None) -> list[dict]:
    """`classify`: optional `task -> (tier, source)` (classify_via_model shape)
    used to ENRICH pairs whose keyword classification is the low-confidence
    MODERATE default — the batch 9B backfill the intake spec promises. Every
    pair carries `tier_source`:

      keyword         — a keyword rule fired confidently; tier is trustworthy
      model           — keyword defaulted; the model classifier assigned the tier
      keyword-default — keyword defaulted and no model verdict was available;
                        training should exclude or down-weight these
    """
    outcome_by_sid: dict = {}
    task_outcomes: list = []
    for e in events:
        if e.get("event") != "usage":
            continue
        oc = e.get("outcome")
        if not oc:
            continue
        sid = e.get("session_id")
        if sid and sid not in outcome_by_sid:
            outcome_by_sid[sid] = oc
        if e.get("task_text"):
            task_outcomes.append((e["task_text"], oc))

    eval_texts = _eval_task_texts()
    kw_confidence = _keyword_confidence()
    pairs: list = []
    seen: set = set()
    for e in events:
        if e.get("event") != "route_decision":
            continue
        task = e.get("task_text") or ""
        if not task:
            continue
        key = (task, e.get("complexity"))
        if key in seen:
            continue
        seen.add(key)

        outcome = outcome_by_sid.get(e.get("session_id"))
        if outcome is None:
            for t, oc in task_outcomes:
                if t.startswith(task[:_PREFIX]) or task.startswith(t[:_PREFIX]):
                    outcome = oc
                    break

        # Tier + provenance. Recompute keyword confidence (the logged event
        # doesn't store `matched`); a confident keyword tier wins outright.
        kw_tier, matched = kw_confidence(task)
        if matched:
            tier, tier_source = kw_tier, "keyword"
        else:
            tier, tier_source = e.get("complexity"), "keyword-default"
            if classify is not None:
                m_tier, m_source = classify(task)
                if m_source == "model":
                    tier, tier_source = m_tier, "model"

        pair = {
            "task_text": task,
            "tier": tier,
            "tier_source": tier_source,
            "chosen_model": e.get("chosen_model"),
            "outcome": outcome,
            "session_id": e.get("session_id"),
            "harness": e.get("harness"),
            "ts": e.get("ts"),
        }
        if task in eval_texts:
            if include_quarantined:
                pair["quarantined"] = True
                pairs.append(pair)
            continue
        pairs.append(pair)
    return pairs
