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


def test_dashboard_cmd_writes(tmp_path):
    led = tmp_path / "events.jsonl"; _seed(led)
    out = tmp_path / "index.html"
    assert cli.main(["dashboard", "--ledger", str(led), "--out", str(out)]) == 0
    assert out.exists()


def test_suggest_runs(capsys):
    assert cli.main(["suggest"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "mismatches" in out
