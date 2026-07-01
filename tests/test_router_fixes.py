import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timezone, timedelta
from pathlib import Path

from llmops import ModelRouter, CodingMemory, CostMonitor, TierLimits
from telemetry.ingest_claude_code import _resolve_git_branch


def _r():
    return ModelRouter(log_decisions=False)


# --------------------------------------------------------------------------
# Classifier: SIMPLE reachable, MODERATE needs >=2 hits, explicit default
# --------------------------------------------------------------------------
def test_simple_tier_is_reachable():
    r = _r()
    # These matched MODERATE (via 'add'/broad words) under the old ordering.
    assert r.classify("add a log line to the download path") == "SIMPLE"
    assert r.classify("fix a typo in the readme") == "SIMPLE"
    assert r.classify("fix a failing xcodebuild test") == "SIMPLE"


def test_moderate_requires_two_hits_else_default():
    r = _r()
    tier, matched = r.classify_detailed("please build it")          # single hit
    assert tier == "MODERATE" and matched is False
    tier, matched = r.classify_detailed("add an endpoint to the backend")  # two hits
    assert tier == "MODERATE" and matched is True


def test_domain_noun_alone_is_not_confident_moderate():
    r = _r()
    # "swiftui" mentioned in an otherwise research task must not yield a
    # confident MODERATE (domain != difficulty).
    tier, matched = r.classify_detailed("investigate trending github repos that mention swiftui")
    assert matched is False


def test_unmatched_task_defaults_explicitly():
    r = _r()
    tier, matched = r.classify_detailed("xyzzy plugh frobnicate")
    assert tier == "MODERATE" and matched is False


def test_labeled_set_no_regression():
    from evals.router_classification_eval import load_dataset, evaluate
    ds = Path(__file__).resolve().parents[1] / "evals" / "datasets" / "labeled_tasks.jsonl"
    res = evaluate(load_dataset(ds), router=_r())
    assert res["accuracy"] >= 0.9  # guard against a classifier regression


# --------------------------------------------------------------------------
# Cost gate: rolling-window spend (old spend ages out — no permanent lock)
# --------------------------------------------------------------------------
def _mem(tmp_path, entries):
    m = CodingMemory(tmp_path / "mem.json")
    m.entries = entries
    return m


def _entry(cost, age_seconds):
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return {"problem": "p", "cost": cost, "stored_at": ts}


def test_spend_since_windows_out_old_spend(tmp_path):
    m = _mem(tmp_path, [
        _entry(100.0, age_seconds=10 * 3600),  # 10h ago -> outside 5h window
        _entry(3.0, age_seconds=60),           # 1min ago -> inside 5h window
    ])
    assert m.spend_since(5 * 3600) == 3.0
    assert m.spend_since(30 * 86400) == 103.0


def test_gate_not_forced_when_spend_aged_out(tmp_path):
    # $100 spent 40 days ago would have tripped the OLD lifetime gate forever;
    # windowed, it's outside every window, so a cheap task routes normally.
    m = _mem(tmp_path, [_entry(100.0, age_seconds=40 * 86400)])
    mon = CostMonitor(m, limits=TierLimits(five_hour_usd=12, weekly_usd=30, monthly_usd=60))
    assert mon.should_route_to_local(0.01) is False


def test_gate_forced_when_recent_spend_near_cap(tmp_path):
    m = _mem(tmp_path, [_entry(11.0, age_seconds=60)])  # $11 in last 5h vs $12 cap
    mon = CostMonitor(m, limits=TierLimits(five_hour_usd=12, weekly_usd=30, monthly_usd=60))
    assert mon.should_route_to_local(0.5) is True       # 11.5/12 = 95.8% >= 80%


# --------------------------------------------------------------------------
# git_branch resolution (HEAD -> cwd-derived lane label)
# --------------------------------------------------------------------------
def test_git_branch_prefers_real_branch():
    assert _resolve_git_branch("feat/x", "/repo") == "feat/x"


def test_git_branch_head_falls_back_to_worktree_name():
    cwd = "/Users/x/MusicAppIOS/Aria_Music_Browser/.worktrees/feat-ui-downsample"
    assert _resolve_git_branch("HEAD", cwd) == "feat-ui-downsample"


def test_git_branch_head_falls_back_to_cwd_basename():
    assert _resolve_git_branch("HEAD", "/Users/x/MusicAppIOS/Aria_Music_Browser") == "Aria_Music_Browser"


def test_git_branch_head_with_no_cwd_stays_head():
    assert _resolve_git_branch("HEAD", None) == "HEAD"
