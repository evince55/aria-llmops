import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evals.capability_probe import select_over_routed, build_prompt, run_probe


def _u(session, model, usd, outcome=None, task="t", inp=1000, out=100):
    return {"event": "usage", "session_id": session, "model": model,
            "imputed_usd": usd, "outcome": outcome, "task_text": task,
            "input_tokens": inp, "output_tokens": out,
            "msg_id": f"{session}-{model}-{usd}"}


LONG_TASK = ("fix a typo in the README and also " + "x" * 200)  # >80 chars, SIMPLE


def test_select_returns_full_task_text_ranked_by_savings():
    ev = [
        _u("small-session", "claude-opus-4-8", 2.0, "success", task="fix a typo in the README"),
        _u("big-session", "claude-opus-4-8", 50.0, "success", task=LONG_TASK),
        _u("fail-session", "claude-opus-4-8", 9.0, "failure", task="fix a typo somewhere"),
        _u("vague-session", "claude-opus-4-8", 9.0, "success",
           task="hmm, something about it just feels wrong lately"),
    ]
    rows = select_over_routed(ev)
    # failures and unconfident sessions are not probe candidates
    ids = [r["session_id"] for r in rows]
    assert "fail-session" not in ids and "vague-session" not in ids
    # ranked by savings: big first; task_text is FULL, not the 80-char preview
    assert ids[0] == "big-session"
    assert rows[0]["task_text"] == LONG_TASK
    assert rows[0]["tier"] == "SIMPLE"
    assert rows[0]["savings_usd"] > rows[1]["savings_usd"]


def test_select_respects_top_n():
    ev = [_u(f"s{i}", "claude-opus-4-8", 10.0 + i, "success",
             task=f"fix a typo in file {i}") for i in range(6)]
    assert len(select_over_routed(ev, top_n=3)) == 3


def test_build_prompt_is_task_adaptive_not_project_framed():
    # v2: the v1 prompt hard-framed every task as iOS-app coding, which derailed
    # non-coding tasks (2 of 4 v1 fails were framing artifacts). The prompt must
    # carry the task verbatim, keep the concrete-artifact demand for coding
    # tasks, and explicitly allow question/research tasks to be answered as such.
    p = build_prompt("rename PlayerManager to AudioManager")
    assert "rename PlayerManager to AudioManager" in p
    assert "files" in p.lower()          # coding branch still demands file-level specifics
    assert "question" in p.lower()       # non-coding branches exist
    assert "ios music" not in p.lower()  # the misleading project premise is gone


def test_run_probe_multi_shots_top_session():
    # v2: the top-savings session dominates the pool, so it gets top_multi
    # samples; all other rows stay single-shot.
    ev = [_u("big", "claude-opus-4-8", 50.0, "success", task="fix a typo in the README"),
          _u("small", "claude-opus-4-8", 5.0, "success", task="rename a variable in Debouncer")]
    client = _FakeClient()
    rows = run_probe(ev, client=client, top_n=2, top_multi=3)
    assert len(rows) == 2
    assert len(client.calls) == 4                     # 3 samples for top + 1 for the other
    top, other = rows[0], rows[1]
    assert top["session_id"] == "big"
    assert len(top["response_samples"]) == 3
    assert top["response"] == top["response_samples"][0]
    assert "response_samples" not in other


class _FakeTierClient:
    """Fake 9B classifier: confidently rates anything SIMPLE."""
    def complete(self, prompt, max_tokens=8, timeout=None, **kw):
        return "SIMPLE", {}


def test_select_with_hybrid_router_includes_rescued_sessions():
    # The probe targets the HYBRID's over-routed pool: a keyword-blind success
    # must be selected when the model classifier confidently rescues it.
    from llmops import ModelRouter
    ev = [_u("vague", "claude-opus-4-8", 9.0, "success",
             task="hmm, something about it just feels wrong lately")]
    hybrid = ModelRouter(log_decisions=False, use_model_classifier=True,
                         classifier_client=_FakeTierClient())
    rows = select_over_routed(ev, router=hybrid)
    assert [r["session_id"] for r in rows] == ["vague"]
    assert rows[0]["tier"] == "SIMPLE"


class _FakeClient:
    model = "fake-local"
    def __init__(self):
        self.calls = []
    def complete(self, prompt, max_tokens=600, timeout=None, **kw):
        self.calls.append(prompt)
        return f"I would edit README.md: answer {len(self.calls)}", {"completion_tokens": 5}


def test_run_probe_records_response_latency_and_model():
    ev = [_u("s1", "claude-opus-4-8", 20.0, "success", task="fix a typo in the README"),
          _u("s2", "claude-opus-4-8", 10.0, "success", task="rename a variable in Debouncer")]
    client = _FakeClient()
    rows = run_probe(ev, client=client, top_n=2)
    assert len(rows) == 2 and len(client.calls) == 2
    r = rows[0]
    assert r["model"] == "fake-local"
    assert "README.md" in r["response"]
    assert r["latency_s"] >= 0.0
    assert r["savings_usd"] > 0
    # grading is deliberately NOT automated: no verdict field is emitted
    assert "grade" not in r and "verdict" not in r
