#!/usr/bin/env python3
"""
LLMOps toolkit for the Aria iOS project.

Three classes:
  CodingMemory  - persists solved problems to .coding_memory.json with
                  sha256-similarity lookup and cumulative cost metrics.
  CostMonitor   - real OpenCode Zen / opencode-go token prices (per 1M),
                  tier limits, and a 80%-threshold gate for forcing local
                  routing.
  ModelRouter   - keyword-based complexity classifier + cost-aware routing
                  across local llama.cpp, opencode-go, and opencode/ providers.

CLI surface (for OpenCode to call from bash):
  python3 tools/llmops/llmops.py --task "..." --tokens 1500
  python3 tools/llmops/llmops.py --store --problem "..." --solution "..." --cost 0.05
  python3 tools/llmops/llmops.py --report

Standard library only. Python 3.9+.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Logging (replaces a separate observability MCP; keeps context clean)
# ---------------------------------------------------------------------------
LOG = logging.getLogger("llmops")
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
LOG.addHandler(_handler)
LOG.setLevel(os.environ.get("LLMOPS_LOG_LEVEL", "INFO"))


# ---------------------------------------------------------------------------
# Model pricing (per 1M tokens, USD)
# Source: https://opencode.ai/docs/zen/ (verified 2026-06-24)
#
# Note: opencode-go/minimax-m3 is not yet on the published Zen list.
# The closest published model is minimax-m2.5 ($0.30 / $1.20); using the
# same tier for m3 since they're same-vendor and adjacent versions. Adjust
# here when m3 pricing is published.
# ---------------------------------------------------------------------------
MODEL_RATES: dict[str, dict[str, float]] = {
    "opencode-go/minimax-m3":          {"input": 0.30, "output": 1.20},
    "opencode/deepseek-v4-flash":      {"input": 0.14, "output": 0.28},
    "opencode/qwen3.7-plus":           {"input": 0.40, "output": 1.60},
    "opencode/deepseek-v4-flash-free": {"input": 0.00, "output": 0.00},
    "llama-cpp/qwen35b":               {"input": 0.00, "output": 0.00},
}

# Default model per complexity tier (cheapest plausible model that can
# handle the task). "local" means any llama.cpp model (router picks one).
TIER_PREFERENCE: dict[str, list[str]] = {
    # CRITICAL tasks have a local fallback so the cost gate can actually
    # downgrade. The local 35B REAP is a credible choice for security work
    # when the budget is exhausted; the route should surface this in the
    # reason so the user knows quality may dip.
    "CRITICAL": ["opencode-go/minimax-m3", "llama-cpp/qwen35b"],
    "COMPLEX":  ["llama-cpp/qwen35b", "opencode-go/minimax-m3"],
    "MODERATE": ["llama-cpp/qwen35b", "opencode/deepseek-v4-flash"],
    "SIMPLE":   ["llama-cpp/qwen35b"],
}

COMPLEXITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "CRITICAL": (
        r"\barchitecture\b", r"\bsecurity\b", r"\bsecure\b", r"\bauth(?!or)\b",
        r"\bauthentication\b", r"\bauthorization\b", r"\bencryption\b",
        r"\bpassword", r"\bsecret", r"\bcredential", r"\bprivate\s+key\b",
        r"\bjwt\b", r"\boauth\b", r"\bcsrf\b", r"\bxss\b",
        r"\bmigration\b", r"\bbreaking\b", r"\bvulnerab",
        r"\bauth[- ]?flow\b", r"\bdesign\s+system\b",
        # iOS / Swift / Apple-platform security and privacy surfaces.
        # classify() lowercases input first, so patterns here must be lowercase.
        r"\binfo\.plist\b", r"\bats\b", r"\bnsexceptiondomains\b",
        r"\bapp\s+transport\s+security\b", r"\bnsallowsarbitraryloads\b",
        r"\bentitlement\b", r"\buibackgroundmodes\b",
        r"\bbackground\s+mode\b", r"\bkeychain\b", r"\bbiometryany\b",
        r"\bksecattr", r"\bnsprivacyaccessed", r"\bapp\s+tracking\s+transparency\b",
    ),
    "COMPLEX": (
        r"\balgorithm\b", r"\brefactor\b", r"\boptimi[sz]e\b", r"\bperformance\b",
        r"\bintegrate\b", r"\bdebug.*root\b", r"\broot\s+cause\b", r"\barchitect\b",
        r"\bbrainstorm\b", r"\bconcurrent\b", r"\brace\s+condition\b",
        r"\bmemory\s+leak\b", r"\bconcurrency\b",
        # iOS / Swift concurrency + media frameworks
        r"\basync\s*/\s*await\b", r"\bactor\s+isolation\b", r"\bsendable\b",
        r"@mainactor\b", r"\bnonisolated\b", r"\btask\.detached\b",
        r"\bavplayer\b", r"\bavaudioengine\b", r"\bavaudiosession\b",
        r"\bcoreaudio\b", r"\bmidi\b", r"\bmpnowplayinginfocenter\b",
        r"\bmpremotecommandcenter\b", r"\binterruption\b", r"\broute\s+change\b",
        # legacy guardrails (WKWebView removed but re-introductions are a smell)
        r"\bwkwebview\b", r"\bimport\s+webkit\b",
    ),
    "MODERATE": (
        # Individually LOW-precision action words. classify() requires >=2 of
        # these to fire before trusting MODERATE; a single hit falls through to
        # the explicit default. Domain nouns (swiftui, @published, @stateobject,
        # controller, protocol, coordinator, loadable, ...) were intentionally
        # removed — they signal *domain*, not *difficulty*, and caused e.g. a
        # research task to classify MODERATE merely for mentioning "SwiftUI".
        r"\bfeature\b", r"\bcomponent\b", r"\bimplement\b", r"\badd\b",
        r"\bbuild\b", r"\bcreate\b", r"\bendpoint\b", r"\bview\b", r"\bscreen\b",
        r"\bconnect\b", r"\bwire\s+up\b", r"\bservice\b", r"\bmanager\b",
        r"\bextract\b", r"\bpromote\b", r"\bmigrate\b",
    ),
    "SIMPLE": (
        r"\bfunction\b", r"\btest\b", r"\bdocs?\b", r"\bcomment\b", r"\brename\b",
        r"\bformat\b", r"\btypo\b", r"\bfix\s+typo\b", r"\bprint\b", r"\blog\b",
        r"\btweak\b", r"\bbump\b",
        # iOS dev-loop vocabulary
        r"\bbuild\s+error\b", r"\bxcodebuild\b", r"\bsimulator\b",
        r"\bdevice\b", r"\btest\s+failure\b", r"\blint\b",
    ),
}


# ---------------------------------------------------------------------------
# Local inference backend (llama.cpp OpenAI-compatible server)
# ---------------------------------------------------------------------------
# Runtime is stdlib-only, so we POST with urllib. Qwen 3.6 is a REASONING model
# whose default spends the entire token budget in the reasoning channel and
# returns empty content — `enable_thinking=false` is REQUIRED for coding use
# (verified 2026-07-01 local capability probe; `/no_think` in the prompt is
# ignored). Endpoint/model are env-overridable so the LAN IP isn't hard-pinned.
LOCAL_BASE_URL = os.environ.get("LLMOPS_LOCAL_BASE_URL", "http://192.168.1.84:8080/v1")
LOCAL_MODEL_NAME = os.environ.get("LLMOPS_LOCAL_MODEL", "qwen3.6-35b-a3b-q8_k.gguf")
LOCAL_ENABLE_THINKING = os.environ.get("LLMOPS_LOCAL_THINKING", "0") == "1"


class LocalLlamaClient:
    """Minimal stdlib client for a local llama.cpp OpenAI-compatible server."""

    def __init__(
        self,
        base_url: str = LOCAL_BASE_URL,
        model: str = LOCAL_MODEL_NAME,
        enable_thinking: bool = LOCAL_ENABLE_THINKING,
        timeout: float = 180.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.enable_thinking = enable_thinking
        self.timeout = timeout

    def _build_body(self, prompt: str, max_tokens: int) -> dict:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            # Load-bearing: without this the model returns empty content.
            "chat_template_kwargs": {"enable_thinking": self.enable_thinking},
        }

    def complete(self, prompt: str, max_tokens: int = 800) -> tuple[str, dict]:
        """Return (text, usage_dict). Raises on transport/HTTP error."""
        import urllib.request
        body = json.dumps(self._build_body(prompt, max_tokens)).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", body, {"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.load(resp)
        msg = data["choices"][0]["message"]
        return (msg.get("content") or ""), (data.get("usage") or {})


# ---------------------------------------------------------------------------
# Model-based tier classifier (cheap runtime classification)
# ---------------------------------------------------------------------------
_TIERS = ("CRITICAL", "COMPLEX", "MODERATE", "SIMPLE")
_CLASSIFY_PROMPT = (
    "Classify the complexity of this software engineering task as EXACTLY one "
    "word: SIMPLE, MODERATE, COMPLEX, or CRITICAL.\n"
    "SIMPLE = typo, rename, formatting, one small function, a failing build/test.\n"
    "MODERATE = a feature/component/endpoint, or wiring across a few files.\n"
    "COMPLEX = refactor, concurrency, performance, a subtle bug, algorithm design.\n"
    "CRITICAL = security, auth, encryption, migrations, breaking changes.\n"
    "Reply with ONLY the one tier word.\n\nTask: {task}"
)


class ModelClassifier:
    """Classify a task's tier by asking a cheap model, with the keyword
    classifier as a fast, always-available fallback.

    A model reads intent from real (multi-paragraph, ambiguous) prompts far
    better than keyword matching — which the 2026-07-01 review showed fires
    SIMPLE 0/1773 and silently defaults ~half of real prompts. The keyword
    fallback keeps routing working (and free) when the model is unreachable or
    replies with something unparseable.
    """

    def __init__(self, complete, keyword_classify) -> None:
        self._complete = complete          # callable(prompt: str, max_tokens: int) -> str
        self._keyword = keyword_classify    # callable(task: str) -> tuple[str, bool]

    def classify(self, task: str) -> tuple[str, str]:
        """Return (tier, source) with source in {"model", "keyword-fallback"}."""
        try:
            raw = self._complete(_CLASSIFY_PROMPT.format(task=task[:2000]), 8) or ""
        except Exception as exc:  # unreachable / bad response -> keyword fallback
            LOG.warning("model classifier unavailable (%s); keyword fallback", exc)
            raw = ""
        up = raw.strip().upper()
        for tier in _TIERS:  # most-specific first; first mention wins
            if tier in up:
                return tier, "model"
        return self._keyword(task)[0], "keyword-fallback"


# ---------------------------------------------------------------------------
# CodingMemory
# ---------------------------------------------------------------------------
class CodingMemory:
    """Persists solved problems + cost metrics to a JSON file.

    Similarity is computed by sha256 over a normalized problem string.
    The store is small (handful of MB) so an O(n) scan is fine; if it
    grows, swap in a real ANN index.
    """

    def __init__(self, path: Path | str = ".coding_memory.json") -> None:
        self.path = Path(path)
        self.entries: list[dict[str, Any]] = []
        self.cost_metrics: dict[str, float] = {
            "total_spent": 0.0,
            "tasks_completed": 0,
            "avg_cost_per_task": 0.0,
        }
        self._load()

    # -- persistence --------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            LOG.debug("memory file %s does not exist yet", self.path)
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            LOG.warning("corrupt memory file %s: %s; starting fresh", self.path, exc)
            return
        self.entries = data.get("entries", [])
        self.cost_metrics = data.get("cost_metrics", self.cost_metrics)

    def _save(self) -> None:
        payload = {
            "entries": self.entries,
            "cost_metrics": self.cost_metrics,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Atomic write so a crash mid-flush doesn't corrupt the file
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=str(self.path.parent),
            prefix=".coding_memory.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as fh:
            tmp_path = Path(fh.name)
            json.dump(payload, fh, indent=2, sort_keys=True)
        tmp_path.replace(self.path)
        LOG.debug("memory persisted: %d entries", len(self.entries))

    # -- similarity ---------------------------------------------------------
    @staticmethod
    def _normalize(text: str) -> str:
        lowered = text.lower().strip()
        # collapse whitespace + strip trailing punctuation
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", lowered)).strip()

    @staticmethod
    def _hash_problem(text: str) -> str:
        return hashlib.sha256(CodingMemory._normalize(text).encode("utf-8")).hexdigest()

    @staticmethod
    def _token_overlap(a: str, b: str) -> float:
        """Jaccard similarity over word tokens. 0.0-1.0."""
        wa = set(CodingMemory._normalize(a).split())
        wb = set(CodingMemory._normalize(b).split())
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / len(wa | wb)

    def retrieve_similar(
        self, current_problem: str, limit: int = 3, min_score: float = 0.25
    ) -> list[dict[str, Any]]:
        scored = [
            (self._token_overlap(current_problem, e["problem"]), e)
            for e in self.entries
        ]
        scored = [s for s in scored if s[0] >= min_score]
        scored.sort(key=lambda s: s[0], reverse=True)
        return [
            {**entry, "similarity": round(score, 3)}
            for score, entry in scored[:limit]
        ]

    # -- write --------------------------------------------------------------
    def store_solution(
        self,
        problem: str,
        solution: str,
        pattern_type: str,
        cost: float,
        category: str | None = None,
    ) -> dict[str, Any]:
        entry = {
            "problem": problem,
            "problem_hash": self._hash_problem(problem),
            "solution": solution,
            "pattern_type": pattern_type,
            "category": (category or "uncategorized").strip().lower(),
            "cost": round(float(cost), 6),
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }
        self.entries.append(entry)
        self.cost_metrics["total_spent"] = round(
            self.cost_metrics["total_spent"] + cost, 6
        )
        self.cost_metrics["tasks_completed"] += 1
        self.cost_metrics["avg_cost_per_task"] = round(
            self.cost_metrics["total_spent"] / self.cost_metrics["tasks_completed"], 6
        )
        self._save()
        LOG.info(
            "stored solution: pattern=%s category=%s cost=$%.4f (total_spent=$%.4f, n=%d)",
            pattern_type, entry["category"], cost,
            self.cost_metrics["total_spent"], self.cost_metrics["tasks_completed"],
        )
        return entry

    # -- aggregation --------------------------------------------------------
    def by_category(self) -> dict[str, dict[str, Any]]:
        """Return spend + count grouped by category, sorted by cost desc.

        Missing categories on older entries default to 'uncategorized'.
        """
        buckets: dict[str, dict[str, Any]] = {}
        for e in self.entries:
            cat = (e.get("category") or "uncategorized").lower()
            b = buckets.setdefault(cat, {"count": 0, "total_cost": 0.0})
            b["count"] += 1
            b["total_cost"] = round(b["total_cost"] + float(e.get("cost", 0.0)), 6)
        # Sort by cost descending so the most-expensive area is first
        for b in buckets.values():
            b["total_cost"] = round(b["total_cost"], 6)
        return dict(sorted(buckets.items(), key=lambda kv: kv[1]["total_cost"], reverse=True))

    def spend_since(self, seconds: float, now: datetime | None = None) -> float:
        """Sum entry `cost` for entries stored within the last `seconds`.

        Used by CostMonitor for a *rolling-window* budget gate. Entries with a
        missing or unparseable `stored_at` are excluded (conservative: they never
        trip the gate). This is what lets old spend age out — the previous gate
        compared *lifetime* total_spent to 5h/weekly caps, so once cumulative
        spend ever crossed a cap it forced local routing permanently.
        """
        now = now or datetime.now(timezone.utc)
        cutoff = now.timestamp() - seconds
        total = 0.0
        for e in self.entries:
            ts = e.get("stored_at")
            if not ts:
                continue
            try:
                t = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t.timestamp() >= cutoff:
                total += float(e.get("cost", 0.0) or 0.0)
        return round(total, 6)

    def by_pattern(self) -> dict[str, dict[str, Any]]:
        """Return spend + count grouped by pattern_type, sorted by cost desc."""
        buckets: dict[str, dict[str, Any]] = {}
        for e in self.entries:
            pat = e.get("pattern_type") or "unknown"
            b = buckets.setdefault(pat, {"count": 0, "total_cost": 0.0})
            b["count"] += 1
            b["total_cost"] = round(b["total_cost"] + float(e.get("cost", 0.0)), 6)
        for b in buckets.values():
            b["total_cost"] = round(b["total_cost"], 6)
        return dict(sorted(buckets.items(), key=lambda kv: kv[1]["total_cost"], reverse=True))


# ---------------------------------------------------------------------------
# CostMonitor
# ---------------------------------------------------------------------------
@dataclass
class TierLimits:
    five_hour_usd: float
    weekly_usd: float
    monthly_usd: float
    force_local_threshold: float = 0.80  # 80%

    @classmethod
    def from_env(cls) -> "TierLimits":
        return cls(
            five_hour_usd=float(os.environ.get("LLMOPS_5HR_USD", "12")),
            weekly_usd=float(os.environ.get("LLMOPS_WEEKLY_USD", "30")),
            monthly_usd=float(os.environ.get("LLMOPS_MONTHLY_USD", "60")),
        )


class CostMonitor:
    """Tracks tier spend and decides when to force local routing."""

    def __init__(
        self,
        memory: CodingMemory,
        limits: TierLimits | None = None,
        rates: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.memory = memory
        self.limits = limits or TierLimits.from_env()
        self.rates = rates or MODEL_RATES
        LOG.info(
            "cost monitor ready: 5h=$%.2f wk=$%.2f mo=$%.2f threshold=%.0f%%",
            self.limits.five_hour_usd, self.limits.weekly_usd, self.limits.monthly_usd,
            self.limits.force_local_threshold * 100,
        )

    # -- pricing ------------------------------------------------------------
    def estimate_cost(self, model: str, tokens: int, output_ratio: float = 0.4) -> float:
        """Estimate USD cost. tokens = total; output_ratio = output/total."""
        if model not in self.rates:
            LOG.warning("unknown model %s; treating as $0", model)
            return 0.0
        rate = self.rates[model]
        in_t = tokens * (1.0 - output_ratio)
        out_t = tokens * output_ratio
        return (in_t / 1_000_000) * rate["input"] + (out_t / 1_000_000) * rate["output"]

    # -- gate ---------------------------------------------------------------
    # Rolling windows for each tier (seconds).
    _WINDOW_SECONDS = {"5hr": 5 * 3600, "weekly": 7 * 86400, "monthly": 30 * 86400}

    def should_route_to_local(self, estimated_cost: float) -> bool:
        """Force local routing if any tier's ROLLING-WINDOW spend is at >= the
        threshold once this task's estimated cost is added.

        Each tier is measured over its own time window (5h / 7d / 30d) via
        CodingMemory.spend_since — so spend ages out and the gate reflects
        *recent* budget pressure. (Previously all three tiers compared *lifetime*
        total_spent to their caps, which meant that once cumulative spend ever
        crossed 0.8 x the 5h cap it forced local routing forever, silently
        downgrading even CRITICAL work.)
        """
        tiers = {
            "5hr": (self.limits.five_hour_usd, self.memory.spend_since(self._WINDOW_SECONDS["5hr"])),
            "weekly": (self.limits.weekly_usd, self.memory.spend_since(self._WINDOW_SECONDS["weekly"])),
            "monthly": (self.limits.monthly_usd, self.memory.spend_since(self._WINDOW_SECONDS["monthly"])),
        }
        for name, (cap, used) in tiers.items():
            if cap <= 0:
                continue
            ratio = (used + estimated_cost) / cap
            if ratio >= self.limits.force_local_threshold:
                LOG.info(
                    "forcing local: tier=%s used=$%.4f + est=$%.4f = %.1f%% of $%.2f cap",
                    name, used, estimated_cost, ratio * 100, cap,
                )
                return True
        return False

    # -- report -------------------------------------------------------------
    def generate_report(
        self,
        by_area: bool = False,
        by_pattern: bool = False,
    ) -> dict[str, Any]:
        m = self.memory.cost_metrics
        report: dict[str, Any] = {
            "limits": asdict(self.limits),
            "cost_metrics": m,
            # Rolling-window utilization (matches the gate in should_route_to_local),
            # not lifetime total_spent.
            "tier_utilization": {
                "5hr":    round(self.memory.spend_since(self._WINDOW_SECONDS["5hr"]) / self.limits.five_hour_usd * 100, 1)
                          if self.limits.five_hour_usd else None,
                "weekly": round(self.memory.spend_since(self._WINDOW_SECONDS["weekly"]) / self.limits.weekly_usd * 100, 1)
                          if self.limits.weekly_usd else None,
                "monthly":round(self.memory.spend_since(self._WINDOW_SECONDS["monthly"]) / self.limits.monthly_usd * 100, 1)
                          if self.limits.monthly_usd else None,
            },
            "memory_entries": len(self.memory.entries),
            "model_rates_count": len(self.rates),
        }
        if by_area:
            report["by_category"] = self.memory.by_category()
        if by_pattern:
            report["by_pattern"] = self.memory.by_pattern()
        return report


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------
class ModelRouter:
    """Classifies complexity, picks the cheapest viable model, and gates
    expensive cloud calls when the cost monitor says to.
    """

    def __init__(
        self,
        memory: CodingMemory | None = None,
        monitor: CostMonitor | None = None,
        preferences: dict[str, list[str]] | None = None,
        log_decisions: bool = True,
        ledger=None,
        harness: str = "opencode",
        local_client: "LocalLlamaClient | None" = None,
        use_model_classifier: bool = False,
    ) -> None:
        self.memory = memory or CodingMemory()
        self.monitor = monitor or CostMonitor(self.memory)
        self.preferences = preferences or TIER_PREFERENCE
        self.log_decisions = log_decisions
        self.ledger = ledger
        self.harness = harness
        self.local_client = local_client or LocalLlamaClient()
        self.use_model_classifier = use_model_classifier

    # -- classification -----------------------------------------------------
    def classify(self, task: str) -> str:
        return self.classify_detailed(task)[0]

    def classify_detailed(self, task: str) -> tuple[str, bool]:
        """Return (tier, matched). `matched` is False when no rule fired and the
        tier is the explicit MODERATE *default* — callers can treat that as
        low-confidence ("needs a better signal") rather than a confident MODERATE.

        High-precision tiers (CRITICAL, COMPLEX, SIMPLE) win on a single keyword
        and are checked BEFORE MODERATE, so the SIMPLE tier is actually reachable
        (its keywords — typo/rename/build-error/lint — are high precision; the
        old CRITICAL>COMPLEX>MODERATE>SIMPLE order meant broad MODERATE words like
        'add'/'build'/'view' shadowed SIMPLE entirely, so it never fired on real
        traffic). MODERATE keywords are individually low-precision, so they
        require >=2 distinct hits before being trusted.
        """
        text = task.lower()
        for tier in ("CRITICAL", "COMPLEX", "SIMPLE"):
            for pattern in COMPLEXITY_KEYWORDS[tier]:
                if re.search(pattern, text):
                    return tier, True
        moderate_hits = sum(
            1 for pattern in COMPLEXITY_KEYWORDS["MODERATE"] if re.search(pattern, text)
        )
        if moderate_hits >= 2:
            return "MODERATE", True
        return "MODERATE", False  # explicit default (nothing matched confidently)

    def classify_via_model(self, task: str) -> tuple[str, str]:
        """Classify with the local model, falling back to keywords. Returns
        (tier, source) where source is "model" or "keyword-fallback"."""
        clf = ModelClassifier(
            complete=lambda p, mt: self.local_client.complete(p, max_tokens=mt)[0],
            keyword_classify=self.classify_detailed,
        )
        return clf.classify(task)

    def _classify(self, task: str) -> tuple[str, bool]:
        """(tier, matched) using whichever classifier is configured. For the
        model classifier, `matched` means the model (not the fallback) decided."""
        if self.use_model_classifier:
            tier, source = self.classify_via_model(task)
            return tier, (source == "model")
        return self.classify_detailed(task)

    # -- main entrypoint ----------------------------------------------------
    def route_task(self, task_description: str, estimated_tokens: int = 1000) -> dict[str, Any]:
        complexity, matched = self._classify(task_description)
        if not matched:
            LOG.info("router: low-confidence tier (default/fallback) for: %.80s", task_description)
        candidates = self.preferences[complexity]
        similar = self.memory.retrieve_similar(task_description)

        # Always compute cost for every candidate (so the user sees the
        # full alternatives table, not just the chosen one).
        all_costs: list[dict[str, Any]] = [
            {
                "model": cand,
                "estimated_cost": round(self.monitor.estimate_cost(cand, estimated_tokens), 6),
            }
            for cand in candidates
        ]

        # Pick the first candidate whose cost doesn't break the bank.
        # The cost gate forces a local candidate; if no local candidate
        # exists in the preference chain, we accept the cheapest option
        # and surface a forced-budget warning in the reason.
        chosen: str | None = None
        chosen_cost: float = 0.0
        forced: bool = False
        for cand in candidates:
            cost = self.monitor.estimate_cost(cand, estimated_tokens)
            if not self.monitor.should_route_to_local(cost):
                chosen = cand
                chosen_cost = cost
                break
        if chosen is None:
            # Cost gate blocked every cloud candidate. Fall back to the
            # first local one in the chain, or the cheapest overall.
            local_first = next(
                (c for c in candidates if c.startswith("llama-cpp")),
                None,
            )
            if local_first is not None:
                chosen = local_first
                chosen_cost = self.monitor.estimate_cost(chosen, estimated_tokens)
                forced = True
            else:
                cheapest = min(all_costs, key=lambda c: c["estimated_cost"])
                chosen = cheapest["model"]
                chosen_cost = cheapest["estimated_cost"]
                forced = True

        reason = self._build_reason(complexity, candidates, similar, chosen, chosen_cost, forced)
        if not matched:
            reason = "no keyword matched — defaulted to MODERATE (low confidence); " + reason
        result = {
            "model": chosen,
            "reason": reason,
            "estimated_cost": round(chosen_cost, 6),
            "complexity": complexity,
            "alternatives": all_costs,
            "similar_solutions": similar,
        }
        if self.log_decisions:
            self._log_decision(task_description, result)
        return result

    def _log_decision(self, task: str, result: dict) -> None:
        """Append a route_decision event to the telemetry ledger. Guarded so a
        telemetry failure never breaks routing, and stays stdlib-only."""
        try:
            from telemetry import schema
            ledger = self.ledger if self.ledger is not None else schema.LEDGER_DEFAULT
            schema.append_events([schema.make_route_decision_event(
                harness=self.harness,
                task_text=task,
                complexity=result["complexity"],
                chosen_model=result["model"],
                estimated_usd=result["estimated_cost"],
                alternatives=result["alternatives"],
            )], ledger=ledger)
        except Exception:
            pass

    # -- execution ----------------------------------------------------------
    def run_task(
        self,
        task_description: str,
        estimated_tokens: int = 1000,
        max_tokens: int = 800,
        executor=None,
    ) -> dict[str, Any]:
        """Route the task, and if it routes to a LOCAL model, actually execute it
        on the local llama.cpp server — logging both the route_decision (via
        route_task) and a `usage` event for the local call. This is what
        exercises the local half of the telemetry pipeline (route_decision +
        local usage) that otherwise never runs. Cloud/frontier tiers are decided
        but not executed here (we can't call those providers from this process)."""
        decision = self.route_task(task_description, estimated_tokens=estimated_tokens)
        model = decision["model"]
        result: dict[str, Any] = {**decision, "executed": False}
        if model.startswith("llama-cpp"):
            run = executor or (lambda p: self.local_client.complete(p, max_tokens=max_tokens))
            try:
                text, usage = run(task_description)
                in_t = int(usage.get("prompt_tokens", 0) or 0)
                out_t = int(usage.get("completion_tokens", 0) or 0)
                self._log_local_usage(task_description, model, in_t, out_t)
                result.update(executed=True, output=text,
                              usage={"input_tokens": in_t, "output_tokens": out_t})
            except Exception as exc:  # never let a local call crash the router
                LOG.warning("local execution failed for %s: %s", model, exc)
                result.update(executed=False, error=str(exc))
        return result

    def _log_local_usage(self, task: str, model: str, in_t: int, out_t: int) -> None:
        """Append a `usage` event for a local model call. Guarded; stdlib-only."""
        try:
            import uuid
            from telemetry import schema, pricing
            ledger = self.ledger if self.ledger is not None else schema.LEDGER_DEFAULT
            schema.append_events([schema.make_usage_event(
                harness="llmops-local",
                session_id=f"llmops-{uuid.uuid4().hex[:8]}",
                msg_id=uuid.uuid4().hex,
                model=model,
                input_tokens=in_t,
                output_tokens=out_t,
                cost_model="local",
                actual_usd=0.0,
                imputed_usd=pricing.imputed_usd(model, input_tokens=in_t, output_tokens=out_t),
                task_text=task[:500],
            )], ledger=ledger)
        except Exception:
            pass

    @staticmethod
    def _build_reason(
        complexity: str,
        candidates: list[str],
        similar: list[dict[str, Any]],
        chosen: str,
        cost: float,
        forced: bool = False,
    ) -> str:
        bits: list[str] = []
        bits.append(f"classified as {complexity}")
        bits.append(f"preference chain = {candidates}")
        if similar:
            top = similar[0]
            bits.append(
                f"{len(similar)} similar past solution(s); "
                f"top match score {top['similarity']} on pattern '{top['pattern_type']}'"
            )
        if forced:
            bits.append(f"BUDGET EXHAUSTED — forced local fallback to {chosen}")
        bits.append(f"selected {chosen} at est. ${cost:.4f}")
        return "; ".join(bits)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="llmops",
        description="LLMOps routing + memory CLI for the Aria iOS project.",
    )
    p.add_argument("--task", help="Task description to route.")
    p.add_argument("--tokens", type=int, default=1000,
                   help="Estimated total tokens for the task (default 1000).")
    p.add_argument("--run", action="store_true",
                   help="Execute the task on the chosen model when it routes to local (llama.cpp).")
    p.add_argument("--max-tokens", type=int, default=800,
                   help="Max output tokens for --run local execution (default 800).")
    p.add_argument("--classifier", choices=["keyword", "model"], default="keyword",
                   help="Tier classifier: fast keyword heuristic (default) or the local model.")
    p.add_argument("--memory-file", default=".coding_memory.json",
                   help="Path to the coding memory JSON file.")
    p.add_argument("--store", action="store_true",
                   help="Store a solution in memory (requires --problem/--solution/--cost).")
    p.add_argument("--problem", help="Problem text (for --store).")
    p.add_argument("--solution", help="Solution ref e.g. commit SHA or PR URL (for --store).")
    p.add_argument("--pattern-type", default="unknown",
                   help="Pattern type tag for the solution (for --store). "
                        "See AGENTS.md 'Standard pattern-type tags for Aria' for conventions.")
    p.add_argument("--category", default=None,
                   help="iOS-area category bucket for the solution (for --store). "
                        "E.g. view, service, manager, model, test, build-fix, info-plist, plugin, mcp.")
    p.add_argument("--cost", type=float, default=0.0,
                   help="Cost in USD (for --store).")
    p.add_argument("--report", action="store_true",
                   help="Print a cost report and exit.")
    p.add_argument("--by-area", action="store_true",
                   help="With --report: include breakdown by iOS area (--category).")
    p.add_argument("--by-pattern", action="store_true",
                   help="With --report: include breakdown by --pattern-type.")
    p.add_argument("--dry-run", action="store_true",
                   help="Skip persistence for --store (still prints what would be stored).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    memory = CodingMemory(args.memory_file)
    monitor = CostMonitor(memory)
    router = ModelRouter(memory, monitor, harness="llmops" if args.run else "opencode",
                         use_model_classifier=(args.classifier == "model"))

    if args.store:
        if not (args.problem and args.solution is not None):
            print(json.dumps({"error": "--store requires --problem and --solution"}))
            return 2
        if args.dry_run:
            entry = memory._hash_problem(args.problem)  # type: ignore[attr-defined]
            print(json.dumps({"dry_run": True, "would_store": {
                "problem": args.problem, "problem_hash": entry,
                "solution": args.solution, "pattern_type": args.pattern_type,
                "category": args.category or "uncategorized",
                "cost": args.cost,
            }}, indent=2))
            return 0
        entry = memory.store_solution(
            problem=args.problem,
            solution=args.solution,
            pattern_type=args.pattern_type,
            cost=args.cost,
            category=args.category,
        )
        print(json.dumps({"stored": True, "entry": entry}, indent=2))
        return 0

    if args.report:
        print(json.dumps(
            monitor.generate_report(by_area=args.by_area, by_pattern=args.by_pattern),
            indent=2,
        ))
        return 0

    if not args.task:
        print(json.dumps({"error": "--task is required (or use --report/--store)"}))
        return 2

    if args.run:
        decision = router.run_task(args.task, estimated_tokens=args.tokens, max_tokens=args.max_tokens)
    else:
        decision = router.route_task(args.task, estimated_tokens=args.tokens)
    print(json.dumps(decision, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
