import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmops import LocalLlamaClient, ModelRouter, CodingMemory
from telemetry import schema


def test_client_body_disables_thinking():
    c = LocalLlamaClient(base_url="http://x/v1", model="m", enable_thinking=False)
    body = c._build_body("hi", 100)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["model"] == "m"
    assert body["messages"][0]["content"] == "hi"
    assert body["max_tokens"] == 100


def test_client_sends_bearer_header_when_key_set():
    c = LocalLlamaClient(base_url="http://x/v1", model="m", api_key="sk-test")
    assert c._headers()["Authorization"] == "Bearer sk-test"
    assert c._headers()["Content-Type"] == "application/json"


def test_client_omits_auth_header_without_key():
    c = LocalLlamaClient(base_url="http://x/v1", model="m", api_key="")
    assert "Authorization" not in c._headers()


def _router(tmp_path):
    mem = CodingMemory(tmp_path / "mem.json")
    ledger = tmp_path / "events.jsonl"
    return ModelRouter(memory=mem, ledger=ledger, log_decisions=True, harness="llmops"), ledger


def test_run_task_executes_local_and_logs_usage(tmp_path):
    r, ledger = _router(tmp_path)
    seen = {}

    def fake_exec(prompt):
        seen["prompt"] = prompt
        return "func f() {}", {"prompt_tokens": 12, "completion_tokens": 8}

    res = r.run_task("fix a typo in the readme", executor=fake_exec)  # SIMPLE -> local
    assert res["complexity"] == "SIMPLE"
    assert res["model"].startswith("llama-cpp")
    assert res["executed"] is True
    assert res["output"] == "func f() {}"
    assert seen["prompt"] == "fix a typo in the readme"

    events = schema.read_events(ledger=ledger)
    usage = [e for e in events if e.get("event") == "usage" and e.get("harness") == "llmops-local"]
    assert len(usage) == 1
    assert usage[0]["input_tokens"] == 12 and usage[0]["output_tokens"] == 8
    assert usage[0]["model"].startswith("llama-cpp")
    decisions = [e for e in events if e.get("event") == "route_decision"]
    assert len(decisions) == 1 and decisions[0]["harness"] == "llmops"


def test_run_task_cloud_tier_not_executed(tmp_path):
    r, ledger = _router(tmp_path)
    called = {"n": 0}

    def fake_exec(prompt):
        called["n"] += 1
        return "x", {}

    res = r.run_task("design the authentication flow for the backend", executor=fake_exec)  # CRITICAL
    assert res["complexity"] == "CRITICAL"
    assert res["model"].startswith("opencode")  # minimax-m3 leads CRITICAL, not local
    assert res["executed"] is False
    assert called["n"] == 0
    assert not [e for e in schema.read_events(ledger=ledger) if e.get("harness") == "llmops-local"]


def test_run_task_local_failure_is_safe(tmp_path):
    r, ledger = _router(tmp_path)

    def boom(prompt):
        raise RuntimeError("connection refused")

    res = r.run_task("fix a typo", executor=boom)
    assert res["executed"] is False
    assert "connection refused" in res["error"]
    assert not [e for e in schema.read_events(ledger=ledger) if e.get("harness") == "llmops-local"]
