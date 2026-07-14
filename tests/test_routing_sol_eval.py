import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evals.routing_sol_eval import evaluate


def _u(session, model, usd, outcome=None, task="t", inp=0, out=0):
    return {"event": "usage", "session_id": session, "model": model,
            "imputed_usd": usd, "outcome": outcome, "task_text": task,
            "input_tokens": inp, "output_tokens": out,
            "msg_id": f"{session}-{model}-{usd}"}


def test_confident_cheap_success_yields_full_headroom():
    # Keyword classifier confidently rates this SIMPLE; its chain leads with the
    # free local model, so the oracle cost is $0 and headroom = full actual spend.
    ev = [_u("s1", "claude-opus-4-8", 2.0, "success",
             task="fix a typo in the README", inp=10_000, out=1_000)]
    r = evaluate(ev)
    assert r["n_labeled"] == 1
    assert r["actual_usd"] == 2.0
    assert r["oracle_usd"] == 0.0
    assert r["headroom_usd"] == 2.0
    assert r["over_routing_usd"] == 2.0


def test_unconfident_success_makes_no_claim():
    # No keyword fires -> defaulted MODERATE (matched=False). The oracle must NOT
    # claim savings it can't attribute: oracle = actual.
    ev = [_u("s1", "claude-opus-4-8", 3.0, "success",
             task="hmm, something about it just feels wrong lately")]
    r = evaluate(ev)
    assert r["oracle_usd"] == 3.0
    assert r["headroom_usd"] == 0.0
    assert r["no_claim_usd"] == 3.0


def test_critical_success_repriced_at_paid_cloud_not_zero():
    # CRITICAL's preference chain leads with a paid cloud model, so oracle cost is
    # its list rate over the session's tokens - not zero.
    ev = [_u("s1", "claude-opus-4-8", 40.0, "success",
             task="rotate the backend api keys and update the authentication flow",
             inp=1_000_000, out=1_000_000)]
    r = evaluate(ev)
    # minimax-m3: 0.30/M input + 1.20/M output = 1.50 for 1M+1M
    assert r["oracle_usd"] == 1.5
    assert r["headroom_usd"] == 38.5


def test_cheap_failure_pays_escalation():
    # A failed session that leaned on a non-frontier model: hindsight-optimal is
    # escalating to the frontier immediately, so the oracle PAYS the frontier
    # reprice of the session's tokens. Headroom goes negative.
    ev = [_u("s1", "llama-cpp/qwen35b", 0.0, "failure",
             task="fix a typo in the README", inp=1_000_000, out=1_000_000)]
    r = evaluate(ev)
    # opus escalation: 5.0/M input + 25.0/M output = 30.0 for 1M+1M
    assert r["oracle_usd"] == 30.0
    assert r["under_routing_usd"] == 30.0
    assert r["headroom_usd"] == -30.0


def test_frontier_failure_makes_no_claim():
    # Failure entirely on the frontier: the oracle has no better hindsight move,
    # so it keeps the actual cost and claims nothing.
    ev = [_u("s1", "claude-opus-4-8", 4.0, "failure", task="fix a typo")]
    r = evaluate(ev)
    assert r["oracle_usd"] == 4.0
    assert r["headroom_usd"] == 0.0
    assert r["under_routing_usd"] == 0.0


def test_unlabeled_sessions_excluded_from_bound():
    ev = [_u("s1", "claude-opus-4-8", 5.0, "success", task="fix a typo in the README"),
          _u("s2", "claude-opus-4-8", 9.0, None, task="fix a typo in the README")]
    r = evaluate(ev)
    assert r["n_labeled"] == 1
    assert r["n_unlabeled"] == 1
    assert r["actual_usd"] == 5.0  # unlabeled spend never enters the bound


def test_ignores_non_usage_and_taskless_events():
    ev = [{"event": "route_decision", "chosen_model": "x"},
          {"event": "usage", "session_id": "s0", "model": "claude-opus-4-8",
           "imputed_usd": 1.0, "outcome": "success", "msg_id": "m0"},  # no task_text
          _u("s1", "claude-opus-4-8", 1.0, "success", task="fix a typo in the README")]
    r = evaluate(ev)
    assert r["n_sessions"] == 1


def test_per_tier_rollup_and_assumptions_present():
    ev = [_u("s1", "claude-opus-4-8", 2.0, "success", task="fix a typo in the README")]
    r = evaluate(ev)
    assert r["per_tier"]["SIMPLE"]["sessions"] == 1
    assert r["per_tier"]["SIMPLE"]["actual_usd"] == 2.0
    assert isinstance(r["assumptions"], list) and r["assumptions"]


class _FakeClassifierClient:
    """Stands in for the 9B: always confidently answers SIMPLE. Accepts any
    sampling kwargs the router passes (temperature, etc.) like the real client."""
    def complete(self, prompt, max_tokens=8, timeout=None, **kwargs):
        return "SIMPLE", {}


def test_hybrid_router_unlocks_no_claim_spend():
    # A keyword-blind success is 'no claim' under the keyword router, but a
    # hybrid router whose model classifier confidently rates it SIMPLE moves
    # that spend into the over-routing pool (oracle = free local model).
    from llmops import ModelRouter
    ev = [_u("s1", "claude-opus-4-8", 3.0, "success",
             task="hmm, something about it just feels wrong lately")]
    hybrid = ModelRouter(log_decisions=False, use_model_classifier=True,
                         classifier_client=_FakeClassifierClient())
    r = evaluate(ev, router=hybrid)
    assert r["no_claim_usd"] == 0.0
    assert r["over_routing_usd"] == 3.0


def test_headroom_pct_relative_to_actual():
    ev = [_u("s1", "claude-opus-4-8", 2.0, "success", task="fix a typo in the README"),
          _u("s2", "claude-opus-4-8", 2.0, "success",
             task="hmm, something about it just feels wrong lately")]
    r = evaluate(ev)
    # 2.0 headroom on 4.0 actual = 50%
    assert r["headroom_pct"] == 50.0
