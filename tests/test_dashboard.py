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
