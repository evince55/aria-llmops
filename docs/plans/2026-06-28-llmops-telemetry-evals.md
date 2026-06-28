# LLMOps Telemetry, Evals & Observability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a harness-agnostic telemetry ledger, a router eval suite, and a self-contained observability dashboard so future Claude Code / opencode sessions generate real LLMOps data.

**Architecture:** An append-only JSONL event ledger (the spine) fed by per-harness ingestion (Claude Code transcript parser + opencode route-decision logging), consumed by three independent units: an eval suite, a static-HTML dashboard, and a routing-tuning helper. Each unit talks only through the ledger schema.

**Tech Stack:** Python 3.9+ standard library only (runtime). pytest for tests. No web framework, no external charting CDN.

**Spec:** `docs/specs/2026-06-28-llmops-telemetry-evals-design.md`

## Global Constraints

- **Runtime is standard-library only.** No third-party imports in any module under `telemetry/`, `evals/`, `dashboard/`. pytest is a dev/test dependency only.
- **Python 3.9+** compatible (use `from __future__ import annotations`; no 3.10+ syntax like `match`).
- **No network in any code path or test.** Ingestion reads local files; evals read the local ledger.
- **Ledger is append-only and idempotent.** Re-ingesting a session must never double-count (`usage` dedup key = `(harness, session_id, msg_id)`).
- **Cost honesty:** every `usage` event carries `cost_model` (`"metered"` | `"subscription"`), `actual_usd`, and `imputed_usd`. Claude Code on Max → `actual_usd = 0`.
- **Run tests with:** `/Users/chait/MusicAppIOS/tools/llmops/.venv/bin/python -m pytest` (venv created in Task 1).
- **Work happens in the `aria-llmops` git repo** at `/Users/chait/MusicAppIOS/tools/llmops` (remote `origin` → github.com/chaitea321/aria-llmops). Commit after every task.

---

### Task 1: Package scaffolding, dev venv, and pricing module

**Files:**
- Create: `telemetry/__init__.py` (empty)
- Create: `telemetry/pricing.py`
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py` (empty)
- Test: `tests/test_pricing.py`

**Interfaces:**
- Produces: `telemetry.pricing.PRICING: dict[str, dict[str, float]]`; `telemetry.pricing.imputed_usd(model: str, *, input_tokens=0, output_tokens=0, cache_write_tokens=0, cache_read_tokens=0) -> float`

- [ ] **Step 1: Create the dev venv and install pytest**

```bash
cd /Users/chait/MusicAppIOS/tools/llmops
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip pytest
printf 'pytest>=8.0\n' > requirements-dev.txt
mkdir -p telemetry tests evals dashboard telemetry/hooks evals/datasets tests/fixtures
touch telemetry/__init__.py tests/__init__.py
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_pricing.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from telemetry import pricing


def test_imputed_cost_opus_basic():
    # 1,000,000 input @ $15 + 1,000,000 output @ $75 = $90
    assert pricing.imputed_usd("claude-opus-4-8", input_tokens=1_000_000, output_tokens=1_000_000) == 90.0


def test_imputed_cost_counts_cache_tokens():
    # cache read 1,000,000 @ $1.5 (0.1x of $15) = $1.5; cache write 1,000,000 @ $18.75 = $18.75
    got = pricing.imputed_usd("claude-opus-4-8", cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    assert got == round(1.5 + 18.75, 6)


def test_local_model_is_free():
    assert pricing.imputed_usd("llama-cpp/qwen35b", input_tokens=5_000_000, output_tokens=5_000_000) == 0.0


def test_unknown_model_is_zero():
    assert pricing.imputed_usd("totally-unknown", input_tokens=1_000_000) == 0.0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pricing.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'telemetry.pricing'`)

- [ ] **Step 4: Write the implementation**

```python
# telemetry/pricing.py
"""Model pricing (USD per 1M tokens) and imputed-cost calculation.

Covers Claude models (for Claude Code usage events) and the opencode/local
models from llmops.MODEL_RATES. Cache tokens are priced separately: reads are
cheap (~0.1x input), writes/creation cost a premium (~1.25x input).

NOTE: Claude list rates below should be sanity-checked against current public
pricing via the `claude-api` skill. The math is rate-table-driven, so updating
a number here is the only change needed.
"""
from __future__ import annotations

PRICING: dict[str, dict[str, float]] = {
    # Claude list API rates (USD / 1M tokens)
    "claude-opus-4-8":   {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.5},
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0, "cache_write": 3.75,  "cache_read": 0.30},
    "claude-haiku-4-5":  {"input": 1.0,  "output": 5.0,  "cache_write": 1.25,  "cache_read": 0.10},
    # opencode / local (mirror of llmops.MODEL_RATES; local self-hosted = free)
    "opencode-go/minimax-m3":          {"input": 0.30, "output": 1.20},
    "opencode/deepseek-v4-flash":      {"input": 0.14, "output": 0.28},
    "opencode/qwen3.7-plus":           {"input": 0.40, "output": 1.60},
    "opencode/deepseek-v4-flash-free": {"input": 0.0,  "output": 0.0},
    "llama-cpp/qwen35b":               {"input": 0.0,  "output": 0.0},
}


def imputed_usd(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Return the list-rate USD cost for the given token counts, or 0.0 for an
    unknown/free model. cache_write/cache_read fall back to the input rate when
    a model doesn't price them separately."""
    rate = PRICING.get(model)
    if rate is None:
        return 0.0
    cw = rate.get("cache_write", rate["input"])
    cr = rate.get("cache_read", rate["input"])
    total = (
        input_tokens * rate["input"]
        + output_tokens * rate["output"]
        + cache_write_tokens * cw
        + cache_read_tokens * cr
    )
    return round(total / 1_000_000, 6)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pricing.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: Add .gitignore entries and commit**

```bash
printf '%s\n' '.venv/' '__pycache__/' '*.pyc' 'telemetry/events.jsonl' 'telemetry/events.index' 'dashboard/index.html' '.pytest_cache/' > .gitignore.tmp
# merge: keep existing ignores, add new (dedup)
cat .gitignore .gitignore.tmp 2>/dev/null | sort -u > .gitignore.new && mv .gitignore.new .gitignore && rm -f .gitignore.tmp
git add telemetry/ tests/ requirements-dev.txt .gitignore
git commit -m "feat(telemetry): pricing table + imputed-cost calculator"
```

---

### Task 2: Event schema and idempotent ledger

**Files:**
- Create: `telemetry/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `telemetry.schema.LEDGER_DEFAULT: pathlib.Path`
  - `make_usage_event(*, harness, session_id, msg_id, model, input_tokens=0, output_tokens=0, cache_write_tokens=0, cache_read_tokens=0, cost_model="subscription", actual_usd=0.0, imputed_usd=0.0, ts=None, cwd=None, git_branch=None, task_text=None, outcome=None) -> dict`
  - `make_route_decision_event(*, harness, task_text, complexity, chosen_model, estimated_usd, alternatives, ts=None) -> dict`
  - `dedup_key(event: dict) -> str | None`
  - `append_events(events: Iterable[dict], ledger=LEDGER_DEFAULT) -> int`
  - `read_events(ledger=LEDGER_DEFAULT) -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schema.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from telemetry import schema


def test_make_usage_event_shape():
    e = schema.make_usage_event(
        harness="claude-code", session_id="s1", msg_id="m1",
        model="claude-opus-4-8", input_tokens=10, output_tokens=2,
        cost_model="subscription", actual_usd=0.0, imputed_usd=0.001,
    )
    assert e["event"] == "usage"
    assert e["harness"] == "claude-code"
    assert e["cost_model"] == "subscription"
    assert e["outcome"] is None


def test_dedup_key_for_usage_and_none_for_decision():
    u = schema.make_usage_event(harness="claude-code", session_id="s1", msg_id="m1", model="x")
    d = schema.make_route_decision_event(
        harness="opencode", task_text="t", complexity="SIMPLE",
        chosen_model="llama-cpp/qwen35b", estimated_usd=0.0, alternatives=[],
    )
    assert schema.dedup_key(u) == "claude-code|s1|m1"
    assert schema.dedup_key(d) is None


def test_append_is_idempotent(tmp_path):
    ledger = tmp_path / "events.jsonl"
    u = schema.make_usage_event(harness="claude-code", session_id="s1", msg_id="m1", model="x")
    assert schema.append_events([u], ledger=ledger) == 1
    assert schema.append_events([u], ledger=ledger) == 0  # duplicate skipped
    assert len(schema.read_events(ledger=ledger)) == 1


def test_route_decisions_always_append(tmp_path):
    ledger = tmp_path / "events.jsonl"
    d = schema.make_route_decision_event(
        harness="opencode", task_text="t", complexity="SIMPLE",
        chosen_model="llama-cpp/qwen35b", estimated_usd=0.0, alternatives=[],
    )
    assert schema.append_events([d], ledger=ledger) == 1
    assert schema.append_events([d], ledger=ledger) == 1  # no dedup key -> appended again
    assert len(schema.read_events(ledger=ledger)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_schema.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'telemetry.schema'`)

- [ ] **Step 3: Write the implementation**

```python
# telemetry/schema.py
"""Event constructors + an append-only, idempotent JSONL ledger."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

LEDGER_DEFAULT = Path(__file__).parent / "events.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_usage_event(
    *,
    harness: str,
    session_id: str,
    msg_id: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    cost_model: str = "subscription",
    actual_usd: float = 0.0,
    imputed_usd: float = 0.0,
    ts: Optional[str] = None,
    cwd: Optional[str] = None,
    git_branch: Optional[str] = None,
    task_text: Optional[str] = None,
    outcome: Any = None,
) -> dict:
    return {
        "event": "usage",
        "ts": ts or _now_iso(),
        "harness": harness,
        "session_id": session_id,
        "msg_id": msg_id,
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cache_write_tokens": int(cache_write_tokens),
        "cache_read_tokens": int(cache_read_tokens),
        "cost_model": cost_model,
        "actual_usd": round(float(actual_usd), 6),
        "imputed_usd": round(float(imputed_usd), 6),
        "cwd": cwd,
        "git_branch": git_branch,
        "task_text": task_text,
        "outcome": outcome,
    }


def make_route_decision_event(
    *,
    harness: str,
    task_text: str,
    complexity: str,
    chosen_model: str,
    estimated_usd: float,
    alternatives: list,
    ts: Optional[str] = None,
) -> dict:
    return {
        "event": "route_decision",
        "ts": ts or _now_iso(),
        "harness": harness,
        "task_text": task_text,
        "complexity": complexity,
        "chosen_model": chosen_model,
        "estimated_usd": round(float(estimated_usd), 6),
        "alternatives": alternatives,
    }


def dedup_key(event: dict) -> Optional[str]:
    """Stable key for usage events; None for events that should always append."""
    if event.get("event") == "usage":
        return f"{event.get('harness')}|{event.get('session_id')}|{event.get('msg_id')}"
    return None


def _load_seen_keys(ledger: Path) -> set:
    seen: set = set()
    if not ledger.exists():
        return seen
    with ledger.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                k = dedup_key(json.loads(line))
            except ValueError:
                continue
            if k is not None:
                seen.add(k)
    return seen


def append_events(events: Iterable[dict], ledger: Path = LEDGER_DEFAULT) -> int:
    """Append events, skipping usage events whose dedup key already exists.
    Returns the number actually written."""
    ledger = Path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    seen = _load_seen_keys(ledger)
    appended = 0
    with ledger.open("a", encoding="utf-8") as fh:
        for e in events:
            k = dedup_key(e)
            if k is not None:
                if k in seen:
                    continue
                seen.add(k)
            fh.write(json.dumps(e) + "\n")
            appended += 1
    return appended


def read_events(ledger: Path = LEDGER_DEFAULT) -> list:
    ledger = Path(ledger)
    out = []
    if not ledger.exists():
        return out
    with ledger.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_schema.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add telemetry/schema.py tests/test_schema.py
git commit -m "feat(telemetry): event schema + idempotent append-only ledger"
```

---

### Task 3: Claude Code transcript parser

**Files:**
- Create: `telemetry/ingest_claude_code.py`
- Create: `tests/fixtures/sample_transcript.jsonl`
- Test: `tests/test_ingest_claude_code.py`

**Interfaces:**
- Consumes: `telemetry.schema.make_usage_event`, `telemetry.pricing.imputed_usd`
- Produces:
  - `parse_transcript(path) -> list[dict]` (list of `usage` events)
  - `iter_project_transcripts(project_dir) -> list[pathlib.Path]`
  - `ingest(paths: Iterable[path], ledger=schema.LEDGER_DEFAULT) -> int`
  - `DEFAULT_PROJECT_DIR: pathlib.Path` (= `~/.claude/projects/-Users-chait-MusicAppIOS`)

- [ ] **Step 1: Create the fixture transcript**

```bash
cat > tests/fixtures/sample_transcript.jsonl <<'JSONL'
{"type":"user","message":{"role":"user","content":"Add a disk-full guard to the backend"},"sessionId":"sess-fixture","timestamp":"2026-06-28T10:00:00Z","cwd":"/Users/chait/MusicAppIOS","gitBranch":"feat/x"}
{"type":"assistant","uuid":"u1","requestId":"req-1","timestamp":"2026-06-28T10:00:05Z","sessionId":"sess-fixture","cwd":"/Users/chait/MusicAppIOS","gitBranch":"feat/x","message":{"model":"claude-opus-4-8","role":"assistant","content":[{"type":"text","text":"ok"}],"usage":{"input_tokens":1000,"output_tokens":200,"cache_creation_input_tokens":500,"cache_read_input_tokens":4000}}}
{"type":"assistant","uuid":"u2","requestId":"req-2","timestamp":"2026-06-28T10:00:09Z","sessionId":"sess-fixture","cwd":"/Users/chait/MusicAppIOS","gitBranch":"feat/x","message":{"model":"claude-opus-4-8","role":"assistant","content":[{"type":"text","text":"done"}],"usage":{"input_tokens":2000,"output_tokens":50,"cache_creation_input_tokens":0,"cache_read_input_tokens":8000}}}
{"type":"system","content":"hook fired"}
JSONL
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_ingest_claude_code.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pathlib import Path
from telemetry import ingest_claude_code as ing
from telemetry import schema, pricing

FIX = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def test_parse_extracts_one_event_per_assistant_message():
    events = ing.parse_transcript(FIX)
    assert len(events) == 2  # two assistant lines; user/system skipped
    assert all(e["event"] == "usage" for e in events)


def test_parse_populates_tokens_model_and_task_text():
    e = ing.parse_transcript(FIX)[0]
    assert e["model"] == "claude-opus-4-8"
    assert e["input_tokens"] == 1000 and e["output_tokens"] == 200
    assert e["cache_write_tokens"] == 500 and e["cache_read_tokens"] == 4000
    assert e["harness"] == "claude-code"
    assert e["session_id"] == "sess-fixture"
    assert e["msg_id"] == "req-1"
    assert e["task_text"].startswith("Add a disk-full guard")
    assert e["cost_model"] == "subscription" and e["actual_usd"] == 0.0


def test_parse_computes_imputed_cost():
    e = ing.parse_transcript(FIX)[0]
    expected = pricing.imputed_usd(
        "claude-opus-4-8", input_tokens=1000, output_tokens=200,
        cache_write_tokens=500, cache_read_tokens=4000,
    )
    assert e["imputed_usd"] == expected and expected > 0


def test_ingest_is_idempotent(tmp_path):
    ledger = tmp_path / "events.jsonl"
    assert ing.ingest([FIX], ledger=ledger) == 2
    assert ing.ingest([FIX], ledger=ledger) == 0
    assert len(schema.read_events(ledger=ledger)) == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ingest_claude_code.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'telemetry.ingest_claude_code'`)

- [ ] **Step 4: Write the implementation**

```python
# telemetry/ingest_claude_code.py
"""Parse Claude Code session transcripts (~/.claude/projects/<proj>/*.jsonl)
into normalized `usage` events. Defensive: tolerates missing fields and skips
unparseable lines. No network."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from telemetry import pricing, schema

HARNESS = "claude-code"
TASK_TEXT_MAX = 500
DEFAULT_PROJECT_DIR = Path.home() / ".claude" / "projects" / "-Users-chait-MusicAppIOS"


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return ""


def _first_task_text(lines: list) -> Optional[str]:
    for obj in lines:
        if obj.get("type") == "user":
            txt = _content_to_text(obj.get("message", {}).get("content"))
            if txt.strip():
                return txt.strip()[:TASK_TEXT_MAX]
    return None


def parse_transcript(path) -> list:
    path = Path(path)
    lines = []
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                lines.append(json.loads(raw))
            except ValueError:
                continue

    task_text = _first_task_text(lines)
    session_id = next((o.get("sessionId") for o in lines if o.get("sessionId")), path.stem)

    events = []
    for obj in lines:
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message") or {}
        usage = msg.get("usage")
        model = msg.get("model")
        if not usage or not model or str(model).startswith("<"):
            continue  # skip synthetic / usage-less messages
        msg_id = obj.get("requestId") or obj.get("uuid")
        if not msg_id:
            continue
        in_t = int(usage.get("input_tokens", 0) or 0)
        out_t = int(usage.get("output_tokens", 0) or 0)
        cw_t = int(usage.get("cache_creation_input_tokens", 0) or 0)
        cr_t = int(usage.get("cache_read_input_tokens", 0) or 0)
        events.append(schema.make_usage_event(
            harness=HARNESS,
            session_id=session_id,
            msg_id=msg_id,
            model=model,
            input_tokens=in_t,
            output_tokens=out_t,
            cache_write_tokens=cw_t,
            cache_read_tokens=cr_t,
            cost_model="subscription",
            actual_usd=0.0,
            imputed_usd=pricing.imputed_usd(
                model, input_tokens=in_t, output_tokens=out_t,
                cache_write_tokens=cw_t, cache_read_tokens=cr_t,
            ),
            ts=obj.get("timestamp"),
            cwd=obj.get("cwd"),
            git_branch=obj.get("gitBranch"),
            task_text=task_text,
        ))
    return events


def iter_project_transcripts(project_dir=DEFAULT_PROJECT_DIR) -> list:
    project_dir = Path(project_dir)
    if not project_dir.exists():
        return []
    return sorted(project_dir.glob("*.jsonl"))


def ingest(paths: Iterable, ledger=schema.LEDGER_DEFAULT) -> int:
    total = 0
    for p in paths:
        total += schema.append_events(parse_transcript(p), ledger=ledger)
    return total
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ingest_claude_code.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add telemetry/ingest_claude_code.py tests/test_ingest_claude_code.py tests/fixtures/sample_transcript.jsonl
git commit -m "feat(telemetry): Claude Code transcript parser -> usage events"
```

---

### Task 4: `telemetry.py` CLI — ingest + backfill existing sessions

**Files:**
- Create: `telemetry.py` (repo-root CLI)
- Test: `tests/test_cli_ingest.py`

**Interfaces:**
- Consumes: `telemetry.ingest_claude_code`, `telemetry.schema`
- Produces: CLI `python3 telemetry.py ingest claude-code [--all | --session PATH] [--ledger PATH] [--project-dir PATH]`; function `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_ingest.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import telemetry as cli
from telemetry import schema


def test_cli_ingest_single_session(tmp_path):
    fix = os.path.join(os.path.dirname(__file__), "fixtures", "sample_transcript.jsonl")
    ledger = tmp_path / "events.jsonl"
    rc = cli.main(["ingest", "claude-code", "--session", fix, "--ledger", str(ledger)])
    assert rc == 0
    assert len(schema.read_events(ledger=ledger)) == 2


def test_cli_ingest_all_from_project_dir(tmp_path):
    # build a fake project dir with one transcript
    proj = tmp_path / "proj"
    proj.mkdir()
    fix = os.path.join(os.path.dirname(__file__), "fixtures", "sample_transcript.jsonl")
    (proj / "a.jsonl").write_text(open(fix).read())
    ledger = tmp_path / "events.jsonl"
    rc = cli.main(["ingest", "claude-code", "--all", "--project-dir", str(proj), "--ledger", str(ledger)])
    assert rc == 0
    assert len(schema.read_events(ledger=ledger)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_ingest.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'telemetry'` resolves to the package, but `main` / CLI not defined → AttributeError)

> Note: `telemetry.py` (module) and `telemetry/` (package) coexist; `import telemetry` resolves to the package via `telemetry/__init__.py`. To expose the CLI, re-export from the package in Step 3.

- [ ] **Step 3: Write the implementation**

Create `telemetry.py` at repo root:

```python
#!/usr/bin/env python3
"""Aria LLMOps telemetry CLI. Subcommands: ingest, eval, dashboard, report, suggest.

Standard library only. Run: python3 telemetry.py <subcommand> ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from telemetry import ingest_claude_code as cc
from telemetry import schema


def _cmd_ingest(args) -> int:
    ledger = Path(args.ledger) if args.ledger else schema.LEDGER_DEFAULT
    if args.source == "claude-code":
        if args.session:
            n = cc.ingest([Path(args.session)], ledger=ledger)
        else:
            project_dir = Path(args.project_dir) if args.project_dir else cc.DEFAULT_PROJECT_DIR
            paths = cc.iter_project_transcripts(project_dir)
            n = cc.ingest(paths, ledger=ledger)
        print(json.dumps({"ingested": n, "ledger": str(ledger)}))
        return 0
    if args.source == "opencode":
        print(json.dumps({"ingested": 0, "note": "opencode usage parsing deferred; see route_decision logging"}))
        return 0
    print(json.dumps({"error": f"unknown source {args.source}"}))
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="telemetry", description="Aria LLMOps telemetry CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="Ingest usage data into the ledger")
    ing.add_argument("source", choices=["claude-code", "opencode"])
    ing.add_argument("--all", action="store_true", help="Ingest every session in the project dir")
    ing.add_argument("--session", help="Ingest a single transcript file")
    ing.add_argument("--project-dir", help="Override the Claude Code project dir")
    ing.add_argument("--ledger", help="Override the ledger path")
    ing.set_defaults(func=_cmd_ingest)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

Then re-export from the package so `import telemetry; telemetry.main(...)` works in tests. Append to `telemetry/__init__.py`:

```python
# telemetry/__init__.py
# Re-export the CLI entrypoint so `import telemetry` exposes main()/build_parser().
import importlib.util as _ilu
from pathlib import Path as _Path

_cli_path = _Path(__file__).parent.parent / "telemetry.py"
_spec = _ilu.spec_from_file_location("telemetry._cli", _cli_path)
_cli = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_cli)
main = _cli.main
build_parser = _cli.build_parser
```

> If this circular-import shim is awkward in practice, the simpler accepted alternative is to rename the CLI file to `telemetry_cli.py` and have `tests` import `telemetry_cli`. Implementer may choose; keep `python3 telemetry.py ...` working either way.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli_ingest.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Backfill the real existing sessions**

Run:
```bash
.venv/bin/python telemetry.py ingest claude-code --all
```
Expected: JSON like `{"ingested": <N>, "ledger": ".../telemetry/events.jsonl"}` with N in the hundreds (11 sessions). Verify:
```bash
wc -l telemetry/events.jsonl
```

- [ ] **Step 6: Commit (ledger stays gitignored)**

```bash
git add telemetry.py telemetry/__init__.py tests/test_cli_ingest.py
git commit -m "feat(telemetry): ingest CLI + Claude Code backfill"
```

---

### Task 5: SessionEnd hook for automatic ingestion

**Files:**
- Create: `telemetry/hooks/claude_code_session_end.py`
- Test: `tests/test_hook.py`

**Interfaces:**
- Consumes: `telemetry.ingest_claude_code.ingest`
- Produces: a script that reads hook JSON from stdin (`{"transcript_path": "...", ...}`) and ingests it; `run(stdin_text: str) -> int` for testability. Always exits 0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hook.py
import sys, os, json, importlib.util
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pathlib import Path
from telemetry import schema

HOOK = Path(__file__).parent.parent / "telemetry" / "hooks" / "claude_code_session_end.py"


def _load():
    spec = importlib.util.spec_from_file_location("hook_mod", HOOK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_hook_ingests_transcript(tmp_path, monkeypatch):
    fix = os.path.join(os.path.dirname(__file__), "fixtures", "sample_transcript.jsonl")
    ledger = tmp_path / "events.jsonl"
    monkeypatch.setenv("LLMOPS_LEDGER", str(ledger))
    m = _load()
    rc = m.run(json.dumps({"transcript_path": fix}))
    assert rc == 0
    assert len(schema.read_events(ledger=ledger)) == 2


def test_hook_survives_bad_input(monkeypatch, tmp_path):
    monkeypatch.setenv("LLMOPS_LEDGER", str(tmp_path / "e.jsonl"))
    m = _load()
    assert m.run("not json") == 0          # never raises
    assert m.run(json.dumps({})) == 0       # missing transcript_path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_hook.py -q`
Expected: FAIL (hook file does not exist)

- [ ] **Step 3: Write the implementation**

```python
# telemetry/hooks/claude_code_session_end.py
#!/usr/bin/env python3
"""Claude Code SessionEnd hook: ingest the just-finished session's transcript
into the LLMOps ledger. Reads hook JSON on stdin. ALWAYS exits 0 so a telemetry
failure can never disrupt a Claude Code session."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Make the repo importable regardless of where the hook is invoked from.
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))


def run(stdin_text: str) -> int:
    try:
        from telemetry import ingest_claude_code as cc
        from telemetry import schema
        payload = json.loads(stdin_text)
        tpath = payload.get("transcript_path")
        if not tpath or not Path(tpath).exists():
            return 0
        ledger = Path(os.environ.get("LLMOPS_LEDGER", str(schema.LEDGER_DEFAULT)))
        cc.ingest([Path(tpath)], ledger=ledger)
    except Exception:
        # Telemetry must never break a session.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.stdin.read()))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_hook.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Wire the hook into Claude Code settings**

Use the `update-config` skill (or edit `~/.claude/settings.json` directly) to add:

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/chait/MusicAppIOS/tools/llmops/telemetry/hooks/claude_code_session_end.py"
          }
        ]
      }
    ]
  }
}
```

Manually smoke-test the hook end-to-end:
```bash
echo '{"transcript_path":"tests/fixtures/sample_transcript.jsonl"}' | .venv/bin/python telemetry/hooks/claude_code_session_end.py; echo "exit=$?"
```
Expected: `exit=0` (and, if `LLMOPS_LEDGER` unset, events appended to the real ledger — dedup makes re-runs safe).

- [ ] **Step 6: Commit**

```bash
git add telemetry/hooks/claude_code_session_end.py tests/test_hook.py
git commit -m "feat(telemetry): SessionEnd hook for auto-ingestion"
```

---

### Task 6: Log opencode route decisions from llmops.py

**Files:**
- Modify: `llmops.py` (add a guarded ledger append inside `ModelRouter.route_task`)
- Test: `tests/test_route_logging.py`

**Interfaces:**
- Consumes: `telemetry.schema.make_route_decision_event`, `telemetry.schema.append_events`
- Produces: `route_task` appends one `route_decision` event per call when telemetry is enabled. New optional ctor arg `ModelRouter(..., log_decisions: bool = True, ledger=None)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_route_logging.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmops import ModelRouter, CodingMemory, CostMonitor
from telemetry import schema


def _router(tmp_path):
    mem = CodingMemory(tmp_path / "mem.json")
    mon = CostMonitor(mem)
    return ModelRouter(mem, mon, ledger=tmp_path / "events.jsonl")


def test_route_task_logs_a_decision(tmp_path):
    r = _router(tmp_path)
    r.route_task("refactor the audio engine for performance", estimated_tokens=2000)
    events = schema.read_events(ledger=tmp_path / "events.jsonl")
    decisions = [e for e in events if e["event"] == "route_decision"]
    assert len(decisions) == 1
    d = decisions[0]
    assert d["harness"] == "opencode"
    assert d["complexity"] in ("CRITICAL", "COMPLEX", "MODERATE", "SIMPLE")
    assert "chosen_model" in d and "alternatives" in d


def test_logging_can_be_disabled(tmp_path):
    mem = CodingMemory(tmp_path / "mem.json")
    r = ModelRouter(mem, CostMonitor(mem), log_decisions=False, ledger=tmp_path / "events.jsonl")
    r.route_task("rename a variable")
    assert schema.read_events(ledger=tmp_path / "events.jsonl") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_route_logging.py -q`
Expected: FAIL (`ModelRouter.__init__` has no `ledger`/`log_decisions` kwargs → TypeError)

- [ ] **Step 3: Modify `llmops.py`**

Update `ModelRouter.__init__` (around llmops.py:392-400) to accept the new kwargs:

```python
    def __init__(
        self,
        memory: CodingMemory | None = None,
        monitor: CostMonitor | None = None,
        preferences: dict[str, list[str]] | None = None,
        log_decisions: bool = True,
        ledger=None,
    ) -> None:
        self.memory = memory or CodingMemory()
        self.monitor = monitor or CostMonitor(self.memory)
        self.preferences = preferences or TIER_PREFERENCE
        self.log_decisions = log_decisions
        self.ledger = ledger
```

At the end of `route_task` (llmops.py:458-466), just before `return`, replace the `return {...}` with a stored result + logging call:

```python
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
                harness="opencode",
                task_text=task,
                complexity=result["complexity"],
                chosen_model=result["model"],
                estimated_usd=result["estimated_cost"],
                alternatives=result["alternatives"],
            )], ledger=ledger)
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_route_logging.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all tests so far)

- [ ] **Step 6: Commit**

```bash
git add llmops.py tests/test_route_logging.py
git commit -m "feat(llmops): log route decisions to the telemetry ledger"
```

---

### Task 7: Router classification eval + labeled dataset

**Files:**
- Create: `evals/datasets/labeled_tasks.jsonl`
- Create: `evals/router_classification_eval.py`
- Test: `tests/test_classification_eval.py`

**Interfaces:**
- Consumes: `llmops.ModelRouter`
- Produces: `load_dataset(path) -> list[dict]`; `evaluate(dataset, router=None) -> dict` returning `{"n", "accuracy", "per_tier": {tier: {"precision","recall","support"}}, "confusion": {actual: {predicted: count}}}`

- [ ] **Step 1: Create the labeled dataset (seed; expand later from real tasks)**

```bash
cat > evals/datasets/labeled_tasks.jsonl <<'JSONL'
{"task":"rename a variable in PlayerManager","expected_tier":"SIMPLE"}
{"task":"fix a typo in the README","expected_tier":"SIMPLE"}
{"task":"add a log line to the download path","expected_tier":"SIMPLE"}
{"task":"bump the deploy target comment","expected_tier":"SIMPLE"}
{"task":"format the SettingsManager file","expected_tier":"SIMPLE"}
{"task":"fix a failing xcodebuild test","expected_tier":"SIMPLE"}
{"task":"write a unit test for the Debouncer","expected_tier":"SIMPLE"}
{"task":"implement a new SwiftUI settings view","expected_tier":"MODERATE"}
{"task":"add an endpoint to the backend for suggestions","expected_tier":"MODERATE"}
{"task":"wire up the sleep timer to pause playback","expected_tier":"MODERATE"}
{"task":"create a QueueStore manager with @Published state","expected_tier":"MODERATE"}
{"task":"extract AVPlayerPath into its own service","expected_tier":"MODERATE"}
{"task":"build a now-playing component for the library","expected_tier":"MODERATE"}
{"task":"refactor the 1000-line PlayerManager god object","expected_tier":"COMPLEX"}
{"task":"optimize artwork decoding performance","expected_tier":"COMPLEX"}
{"task":"debug the root cause of a race condition in playback","expected_tier":"COMPLEX"}
{"task":"fix actor isolation and Sendable warnings in async/await code","expected_tier":"COMPLEX"}
{"task":"integrate AVAudioEngine with the streaming path","expected_tier":"COMPLEX"}
{"task":"resolve a memory leak in the audio tap","expected_tier":"COMPLEX"}
{"task":"design the authentication flow for the backend","expected_tier":"CRITICAL"}
{"task":"add encryption for stored credentials in the keychain","expected_tier":"CRITICAL"}
{"task":"review Info.plist ATS NSAllowsArbitraryLoads exceptions for security","expected_tier":"CRITICAL"}
{"task":"plan a breaking schema migration for the JSON stores","expected_tier":"CRITICAL"}
{"task":"audit the app for a security vulnerability in the proxy","expected_tier":"CRITICAL"}
JSONL
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_classification_eval.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pathlib import Path
from evals import router_classification_eval as ev

DATA = Path(__file__).parent.parent / "evals" / "datasets" / "labeled_tasks.jsonl"


def test_dataset_loads():
    rows = ev.load_dataset(DATA)
    assert len(rows) >= 20
    assert all("task" in r and "expected_tier" in r for r in rows)


def test_evaluate_reports_accuracy_and_confusion():
    rows = ev.load_dataset(DATA)
    res = ev.evaluate(rows)
    assert res["n"] == len(rows)
    assert 0.0 <= res["accuracy"] <= 1.0
    assert set(res["per_tier"]).issubset({"CRITICAL", "COMPLEX", "MODERATE", "SIMPLE"})
    assert "confusion" in res
    # Sanity: the seed set is keyword-aligned, so the classifier should do well.
    assert res["accuracy"] >= 0.7
```

> `evals/` must be importable as a package. Step 3 adds `evals/__init__.py`.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_classification_eval.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'evals.router_classification_eval'`)

- [ ] **Step 4: Write the implementation**

```bash
touch evals/__init__.py
```

```python
# evals/router_classification_eval.py
"""Measure ModelRouter.classify() accuracy against a labeled task->tier set."""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import ModelRouter  # noqa: E402

TIERS = ("CRITICAL", "COMPLEX", "MODERATE", "SIMPLE")


def load_dataset(path) -> list:
    rows = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def evaluate(dataset: list, router: ModelRouter | None = None) -> dict:
    router = router or ModelRouter(log_decisions=False)
    confusion: dict = {a: defaultdict(int) for a in TIERS}
    correct = 0
    support: dict = defaultdict(int)
    pred_count: dict = defaultdict(int)
    tp: dict = defaultdict(int)
    for row in dataset:
        actual = row["expected_tier"]
        predicted = router.classify(row["task"])
        confusion.setdefault(actual, defaultdict(int))[predicted] += 1
        support[actual] += 1
        pred_count[predicted] += 1
        if predicted == actual:
            correct += 1
            tp[actual] += 1
    n = len(dataset)
    per_tier = {}
    for t in TIERS:
        prec = tp[t] / pred_count[t] if pred_count[t] else 0.0
        rec = tp[t] / support[t] if support[t] else 0.0
        per_tier[t] = {"precision": round(prec, 3), "recall": round(rec, 3), "support": support[t]}
    return {
        "n": n,
        "accuracy": round(correct / n, 3) if n else 0.0,
        "per_tier": per_tier,
        "confusion": {a: dict(d) for a, d in confusion.items()},
    }


def main() -> int:
    data = load_dataset(Path(__file__).parent / "datasets" / "labeled_tasks.jsonl")
    print(json.dumps(evaluate(data), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_classification_eval.py -q`
Expected: PASS (2 passed). If accuracy < 0.7, the classifier genuinely mislabels seed tasks — record which in the confusion matrix (that's a real finding for routing-loop tuning), and lower the threshold assertion to the observed value with a comment rather than editing the dataset to cheat.

- [ ] **Step 6: Commit**

```bash
git add evals/__init__.py evals/router_classification_eval.py evals/datasets/labeled_tasks.jsonl tests/test_classification_eval.py
git commit -m "feat(evals): router classification accuracy eval + labeled dataset"
```

---

### Task 8: Routing-efficiency eval (replay real usage through the router)

**Files:**
- Create: `evals/routing_efficiency_eval.py`
- Test: `tests/test_efficiency_eval.py`

**Interfaces:**
- Consumes: `llmops.ModelRouter`, `telemetry.schema.read_events`
- Produces: `evaluate(events: list[dict], router=None) -> dict` returning `{"n_tasks", "total_actual_usd", "total_imputed_usd", "would_route_local_pct", "by_complexity": {tier: count}, "rows": [...]}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_efficiency_eval.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evals import routing_efficiency_eval as ev
from telemetry import schema


def _usage(task, imputed, model="claude-opus-4-8"):
    return schema.make_usage_event(
        harness="claude-code", session_id="s", msg_id=str(id(task)) + task[:3],
        model=model, imputed_usd=imputed, actual_usd=0.0, task_text=task,
    )


def test_efficiency_aggregates(tmp_path):
    events = [
        _usage("rename a variable", 0.5),          # SIMPLE -> would route local
        _usage("design the auth flow security", 2.0),  # CRITICAL
    ]
    res = ev.evaluate(events)
    assert res["n_tasks"] == 2
    assert res["total_imputed_usd"] == 2.5
    assert 0.0 <= res["would_route_local_pct"] <= 100.0
    assert sum(res["by_complexity"].values()) == 2


def test_ignores_events_without_task_text():
    events = [{"event": "usage", "imputed_usd": 1.0, "task_text": None, "model": "claude-opus-4-8"}]
    res = ev.evaluate(events)
    assert res["n_tasks"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_efficiency_eval.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'evals.routing_efficiency_eval'`)

- [ ] **Step 3: Write the implementation**

```python
# evals/routing_efficiency_eval.py
"""Replay real usage events through ModelRouter to estimate routing efficiency.

For each task we observed actually running (on Opus, under the Max subscription),
ask: what tier would the router assign, and would a local/cheaper model have
plausibly sufficed? Cost-only — no output-quality judgment."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import ModelRouter  # noqa: E402
from telemetry import schema  # noqa: E402

# Tiers whose preference chain leads with a local/free model -> "local would do".
_LOCAL_FIRST_TIERS = {"SIMPLE", "MODERATE", "COMPLEX"}


def evaluate(events: list, router: ModelRouter | None = None) -> dict:
    router = router or ModelRouter(log_decisions=False)
    rows = []
    by_complexity: dict = defaultdict(int)
    total_actual = 0.0
    total_imputed = 0.0
    would_local = 0
    for e in events:
        if e.get("event") and e.get("event") != "usage":
            continue
        task = e.get("task_text")
        if not task:
            continue
        tier = router.classify(task)
        by_complexity[tier] += 1
        actual = float(e.get("actual_usd", 0.0) or 0.0)
        imputed = float(e.get("imputed_usd", 0.0) or 0.0)
        total_actual += actual
        total_imputed += imputed
        local_ok = tier in _LOCAL_FIRST_TIERS
        if local_ok:
            would_local += 1
        rows.append({
            "task": task[:80],
            "actual_model": e.get("model"),
            "predicted_tier": tier,
            "imputed_usd": round(imputed, 6),
            "local_would_suffice": local_ok,
        })
    n = len(rows)
    return {
        "n_tasks": n,
        "total_actual_usd": round(total_actual, 6),
        "total_imputed_usd": round(total_imputed, 6),
        "would_route_local_pct": round(would_local / n * 100, 1) if n else 0.0,
        "by_complexity": dict(by_complexity),
        "rows": rows,
    }


def main() -> int:
    events = schema.read_events()
    print(json.dumps({k: v for k, v in evaluate(events).items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_efficiency_eval.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add evals/routing_efficiency_eval.py tests/test_efficiency_eval.py
git commit -m "feat(evals): routing-efficiency replay eval"
```

---

### Task 9: Self-contained static-HTML dashboard

**Files:**
- Create: `dashboard/__init__.py` (empty)
- Create: `dashboard/generate.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `telemetry.schema.read_events`, `evals.routing_efficiency_eval.evaluate`, `evals.router_classification_eval`
- Produces: `build_html(events: list[dict], classification: dict | None = None) -> str`; `generate(ledger=None, out=None) -> pathlib.Path`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dashboard import generate as dash
from telemetry import schema


def test_build_html_is_self_contained_and_has_data():
    events = [
        schema.make_usage_event(harness="claude-code", session_id="s", msg_id="m1",
                                 model="claude-opus-4-8", imputed_usd=1.5, task_text="refactor engine"),
        schema.make_usage_event(harness="claude-code", session_id="s", msg_id="m2",
                                 model="claude-opus-4-8", imputed_usd=0.5, task_text="fix typo"),
    ]
    html = dash.build_html(events)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "http://" not in html and "https://" not in html  # no external CDN
    assert "2.0" in html or "2.00" in html  # total imputed shows up
    assert "claude-opus-4-8" in html


def test_generate_writes_file(tmp_path):
    ledger = tmp_path / "events.jsonl"
    schema.append_events([schema.make_usage_event(
        harness="claude-code", session_id="s", msg_id="m1",
        model="claude-opus-4-8", imputed_usd=1.0, task_text="t")], ledger=ledger)
    out = tmp_path / "index.html"
    p = dash.generate(ledger=ledger, out=out)
    assert p.exists() and p.read_text().lstrip().startswith("<!DOCTYPE html>")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dashboard.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'dashboard.generate'`)

- [ ] **Step 3: Write the implementation**

```bash
touch dashboard/__init__.py
```

```python
# dashboard/generate.py
"""Generate a self-contained static HTML dashboard from the telemetry ledger.

No external CDN, no server: data is embedded and charts are inline SVG. Open the
output file directly in a browser."""
from __future__ import annotations

import html as _html
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry import schema  # noqa: E402
from evals.routing_efficiency_eval import evaluate as efficiency  # noqa: E402


def _bar_svg(pairs, width=520, bar_h=22, gap=8):
    """pairs: list of (label, value). Returns an inline SVG bar chart."""
    if not pairs:
        return "<p>(no data)</p>"
    maxv = max(v for _, v in pairs) or 1
    rows = []
    y = 0
    for label, v in pairs:
        w = int((v / maxv) * (width - 160))
        rows.append(
            f'<g transform="translate(0,{y})">'
            f'<text x="0" y="15" font-size="12" fill="#ccc">{_html.escape(str(label))[:22]}</text>'
            f'<rect x="150" y="3" width="{w}" height="{bar_h-6}" fill="#4f9da6"/>'
            f'<text x="{150+w+5}" y="15" font-size="11" fill="#888">{v}</text>'
            f'</g>'
        )
        y += bar_h + gap
    return f'<svg width="{width}" height="{y}" xmlns="http://www.w3.org/2000/svg">{"".join(rows)}</svg>'


def build_html(events: list, classification: dict | None = None) -> str:
    usage = [e for e in events if e.get("event", "usage") == "usage"]
    total_imputed = round(sum(float(e.get("imputed_usd", 0) or 0) for e in usage), 4)
    total_actual = round(sum(float(e.get("actual_usd", 0) or 0) for e in usage), 4)
    by_model = defaultdict(float)
    for e in usage:
        by_model[e.get("model", "unknown")] += float(e.get("imputed_usd", 0) or 0)
    model_pairs = sorted(((m, round(v, 4)) for m, v in by_model.items()), key=lambda x: -x[1])

    eff = efficiency(events)
    tier_pairs = sorted(eff["by_complexity"].items(), key=lambda x: -x[1])

    cls_block = ""
    if classification:
        cls_block = (
            f"<h2>Router classification accuracy</h2>"
            f"<p class='big'>{classification['accuracy']*100:.0f}%</p>"
            f"<pre>{_html.escape(json.dumps(classification['per_tier'], indent=2))}</pre>"
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Aria LLMOps Dashboard</title>
<style>
 body{{font-family:-apple-system,system-ui,sans-serif;background:#16181d;color:#e6e6e6;margin:2rem;}}
 h1{{font-weight:600}} h2{{color:#9ad;margin-top:2rem}}
 .cards{{display:flex;gap:1rem;flex-wrap:wrap}}
 .card{{background:#1f232b;border-radius:10px;padding:1rem 1.4rem;min-width:160px}}
 .big{{font-size:2rem;font-weight:700;margin:.2rem 0}}
 .sub{{color:#8a93a3;font-size:.8rem}}
 pre{{background:#1f232b;padding:1rem;border-radius:8px;overflow:auto;font-size:12px}}
</style></head><body>
<h1>Aria LLMOps Dashboard</h1>
<p class="sub">{len(usage)} usage events · generated from telemetry/events.jsonl</p>
<div class="cards">
  <div class="card"><div class="sub">Imputed cost (list rates)</div><div class="big">${total_imputed}</div></div>
  <div class="card"><div class="sub">Actual spend</div><div class="big">${total_actual}</div></div>
  <div class="card"><div class="sub">Tasks where local would suffice</div><div class="big">{eff['would_route_local_pct']}%</div></div>
  <div class="card"><div class="sub">Tasks analyzed</div><div class="big">{eff['n_tasks']}</div></div>
</div>
<h2>Imputed cost by model</h2>
{_bar_svg(model_pairs)}
<h2>Tasks by predicted complexity</h2>
{_bar_svg(tier_pairs)}
{cls_block}
</body></html>"""


def generate(ledger=None, out=None) -> Path:
    events = schema.read_events(ledger=ledger) if ledger else schema.read_events()
    classification = None
    try:
        from evals.router_classification_eval import load_dataset, evaluate as cls_eval
        ds_path = Path(__file__).resolve().parents[1] / "evals" / "datasets" / "labeled_tasks.jsonl"
        if ds_path.exists():
            classification = cls_eval(load_dataset(ds_path))
    except Exception:
        pass
    out = Path(out) if out else Path(__file__).parent / "index.html"
    out.write_text(build_html(events, classification), encoding="utf-8")
    return out


def main() -> int:
    p = generate()
    print(json.dumps({"written": str(p)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_dashboard.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Generate the real dashboard and eyeball it**

Run:
```bash
.venv/bin/python dashboard/generate.py && open dashboard/index.html
```
Expected: a dark dashboard with cost cards, bar charts by model + complexity, and the classifier accuracy block, populated from the backfilled ledger.

- [ ] **Step 6: Commit**

```bash
git add dashboard/__init__.py dashboard/generate.py tests/test_dashboard.py
git commit -m "feat(dashboard): self-contained static HTML observability dashboard"
```

---

### Task 10: CLI wiring (eval/dashboard/report/suggest) + README

**Files:**
- Modify: `telemetry.py` (add `eval`, `dashboard`, `report`, `suggest` subcommands)
- Create: `README.md`
- Test: `tests/test_cli_commands.py`

**Interfaces:**
- Consumes: everything above.
- Produces: CLI `eval [classification|efficiency|all]`, `dashboard`, `report`, `suggest`; `suggest` returns the worst classification mismatches as tuning candidates.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_commands.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import telemetry as cli
from telemetry import schema


def _seed(ledger):
    schema.append_events([
        schema.make_usage_event(harness="claude-code", session_id="s", msg_id="m1",
                                model="claude-opus-4-8", imputed_usd=1.0, task_text="rename a variable"),
    ], ledger=ledger)


def test_report_runs(tmp_path, capsys):
    led = tmp_path / "events.jsonl"; _seed(led)
    assert cli.main(["report", "--ledger", str(led)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["usage_events"] == 1


def test_eval_all_runs(capsys):
    assert cli.main(["eval", "all"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "classification" in out and "efficiency" in out


def test_dashboard_cmd_writes(tmp_path):
    led = tmp_path / "events.jsonl"; _seed(led)
    out = tmp_path / "index.html"
    assert cli.main(["dashboard", "--ledger", str(led), "--out", str(out)]) == 0
    assert out.exists()


def test_suggest_runs(capsys):
    assert cli.main(["suggest"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "mismatches" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_commands.py -q`
Expected: FAIL (subcommands not defined → argparse SystemExit / AttributeError)

- [ ] **Step 3: Extend `telemetry.py`**

Add these handlers and register them in `build_parser` (before `return p`):

```python
def _cmd_report(args) -> int:
    ledger = Path(args.ledger) if args.ledger else schema.LEDGER_DEFAULT
    events = schema.read_events(ledger=ledger)
    usage = [e for e in events if e.get("event", "usage") == "usage"]
    decisions = [e for e in events if e.get("event") == "route_decision"]
    print(json.dumps({
        "usage_events": len(usage),
        "route_decisions": len(decisions),
        "total_imputed_usd": round(sum(float(e.get("imputed_usd", 0) or 0) for e in usage), 4),
        "total_actual_usd": round(sum(float(e.get("actual_usd", 0) or 0) for e in usage), 4),
    }))
    return 0


def _cmd_eval(args) -> int:
    from evals.router_classification_eval import load_dataset, evaluate as cls_eval
    from evals.routing_efficiency_eval import evaluate as eff_eval
    ds = Path(__file__).parent / "evals" / "datasets" / "labeled_tasks.jsonl"
    out = {}
    if args.which in ("classification", "all"):
        out["classification"] = cls_eval(load_dataset(ds))
    if args.which in ("efficiency", "all"):
        ledger = Path(args.ledger) if args.ledger else schema.LEDGER_DEFAULT
        res = eff_eval(schema.read_events(ledger=ledger))
        res.pop("rows", None)
        out["efficiency"] = res
    print(json.dumps(out, indent=2))
    return 0


def _cmd_dashboard(args) -> int:
    from dashboard.generate import generate
    ledger = Path(args.ledger) if args.ledger else None
    out = Path(args.out) if args.out else None
    p = generate(ledger=ledger, out=out)
    print(json.dumps({"written": str(p)}))
    return 0


def _cmd_suggest(args) -> int:
    """Surface the worst classifier mismatches as routing-tuning candidates."""
    from evals.router_classification_eval import load_dataset
    from llmops import ModelRouter
    ds = Path(__file__).parent / "evals" / "datasets" / "labeled_tasks.jsonl"
    router = ModelRouter(log_decisions=False)
    mismatches = []
    for row in load_dataset(ds):
        pred = router.classify(row["task"])
        if pred != row["expected_tier"]:
            mismatches.append({"task": row["task"], "expected": row["expected_tier"], "predicted": pred})
    print(json.dumps({"mismatches": mismatches, "count": len(mismatches)}, indent=2))
    return 0
```

Register (inside `build_parser`, before `return p`):

```python
    rep = sub.add_parser("report", help="Summarize the ledger")
    rep.add_argument("--ledger")
    rep.set_defaults(func=_cmd_report)

    ev = sub.add_parser("eval", help="Run evals")
    ev.add_argument("which", choices=["classification", "efficiency", "all"], default="all", nargs="?")
    ev.add_argument("--ledger")
    ev.set_defaults(func=_cmd_eval)

    dash = sub.add_parser("dashboard", help="Generate the static HTML dashboard")
    dash.add_argument("--ledger")
    dash.add_argument("--out")
    dash.set_defaults(func=_cmd_dashboard)

    sug = sub.add_parser("suggest", help="List classifier mismatches to tune routing")
    sug.set_defaults(func=_cmd_suggest)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli_commands.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Write the README and run the full suite**

Create `README.md`:

```markdown
# aria-llmops

Harness-agnostic LLMOps telemetry, evals, and cost-aware routing for the Aria project.

## What it does
- **Telemetry ledger** (`telemetry/events.jsonl`): one normalized stream of model
  usage from Claude Code (transcript parser + SessionEnd hook) and opencode
  routing decisions (logged by `llmops.py`).
- **Evals** (`evals/`): router classification accuracy vs a labeled set, and a
  cost-only routing-efficiency replay over real usage.
- **Dashboard** (`dashboard/`): a self-contained static HTML view of spend,
  model mix, and routing — no server, no CDN.
- **Routing** (`llmops.py`): the existing ModelRouter/CostMonitor/CodingMemory.

## Usage
```bash
python3 telemetry.py ingest claude-code --all   # backfill existing sessions
python3 telemetry.py report
python3 telemetry.py eval all
python3 telemetry.py dashboard && open dashboard/index.html
python3 telemetry.py suggest                     # routing-tuning candidates
```

## Tests
```bash
.venv/bin/python -m pytest -q
```

## Claude Code hook
A `SessionEnd` hook in `~/.claude/settings.json` runs
`telemetry/hooks/claude_code_session_end.py` to auto-ingest each session.
```

Run the whole suite:
```bash
.venv/bin/python -m pytest -q
```
Expected: PASS (all tests, ~24+).

- [ ] **Step 6: Commit and push**

```bash
git add telemetry.py README.md tests/test_cli_commands.py
git commit -m "feat(cli): eval/dashboard/report/suggest subcommands + README"
git push
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** ledger (T2), CC parser+backfill (T3,T4), SessionEnd hook (T5), opencode route logging (T6), classification eval (T7), efficiency eval (T8), dashboard (T9), CLI+report+suggest+README (T10), pricing/cost-honesty (T1, used in T3). Routing-loop = `suggest` (T10) + documented tuning. Version control = the repo itself (done). ✓
- **Placeholders:** none — every step has runnable code/commands. The labeled dataset is concrete (24 rows). Claude rates are concrete with a verify-via-claude-api note. ✓
- **Type consistency:** `make_usage_event`/`make_route_decision_event`/`append_events`/`read_events` signatures match across T2→T3,T6,T8,T9. `evaluate()` shapes match their tests. `ModelRouter(log_decisions=, ledger=)` added in T6 and used in T7/T8. ✓
- **Out of scope (deferred, per spec §2/§10):** output-quality/LLM-judge, opencode usage parsing, automated keyword tuning, the `outcome` field population.
```
