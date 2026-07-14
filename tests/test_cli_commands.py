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
    assert "classification" in out and "efficiency" in out and "sol" in out


def test_eval_sol_runs(capsys, tmp_path):
    led = tmp_path / "events.jsonl"; _seed(led)
    assert cli.main(["eval", "sol", "--ledger", str(led)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "headroom_usd" in out["sol"]
    assert "assumptions" in out["sol"]


def test_flywheel_export_cmd(capsys, tmp_path):
    led = tmp_path / "events.jsonl"
    schema.append_events([
        schema.make_route_decision_event(
            harness="claude-code", task_text="a totally novel task about queues",
            complexity="SIMPLE", chosen_model="llama-cpp/qwen35b",
            estimated_usd=0.0, alternatives=[], session_id="s1"),
        schema.make_usage_event(
            harness="claude-code", session_id="s1", msg_id="m1",
            model="claude-opus-4-8", input_tokens=1, output_tokens=1,
            task_text="a totally novel task about queues", outcome="success"),
    ], ledger=led)
    out_path = tmp_path / "pairs.jsonl"
    assert cli.main(["flywheel", "export", "--ledger", str(led), "--out", str(out_path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["pairs"] == 1 and summary["joined"] == 1
    assert out_path.exists()


def test_flywheel_clusters_cmd(capsys, tmp_path):
    led = tmp_path / "events.jsonl"
    schema.append_events([
        schema.make_route_decision_event(
            harness="claude-code", task_text="fix the queue race condition",
            complexity="COMPLEX", chosen_model="x", estimated_usd=0.0,
            alternatives=[], session_id="s1"),
    ], ledger=led)
    assert cli.main(["flywheel", "clusters", "--ledger", str(led)]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["n_tasks"] == 1 and r["n_clusters"] == 1


def test_dashboard_cmd_writes(tmp_path):
    led = tmp_path / "events.jsonl"; _seed(led)
    out = tmp_path / "index.html"
    assert cli.main(["dashboard", "--ledger", str(led), "--out", str(out)]) == 0
    assert out.exists()


def test_suggest_runs(capsys):
    assert cli.main(["suggest"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "mismatches" in out
