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


def test_build_html_escapes_special_chars():
    from telemetry import schema
    events = [schema.make_usage_event(harness="claude-code", session_id="s", msg_id="x",
                                      model="<script>alert(1)</script>", imputed_usd=1.0, task_text="t")]
    html = dash.build_html(events)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# A1 — the input/output token split must be visible, not just costed
# ---------------------------------------------------------------------------
def _usage(model, i, o, cw=0, cr=0, session="s"):
    return schema.make_usage_event(
        harness="claude-code", session_id=session, msg_id=f"m{i}{o}", model=model,
        input_tokens=i, output_tokens=o, cache_write_tokens=cw, cache_read_tokens=cr,
        imputed_usd=0.0, task_text="t")


def test_dashboard_surfaces_the_token_split():
    """Cost alone cannot tell you whether verbosity is what you are paying for."""
    html = dash.build_html([_usage("m", 1000, 3000)])
    low = html.lower()
    assert "output" in low and "input" in low
    assert "3,000" in html or "3000" in html


def test_dashboard_labels_which_ratio_it_is_showing():
    """With caching, an unlabelled 'output ratio' is a lie — 0.882 vs 0.003 on
    the same events depending on the denominator."""
    html = dash.build_html([_usage("m", 100, 100, cr=9800)])
    assert "cache" in html.lower()


def test_dashboard_reports_output_tokens_per_session():
    html = dash.build_html([_usage("m", 10, 500, session="a"),
                            _usage("m", 10, 700, session="b")])
    assert "per session" in html.lower() or "per-session" in html.lower()


def test_token_split_section_survives_events_with_no_token_fields():
    """Older ledgers predate the split; the dashboard must still render."""
    html = dash.build_html([schema.make_usage_event(
        harness="claude-code", session_id="s", msg_id="m", model="m",
        imputed_usd=1.0, task_text="t")])
    assert html.lstrip().startswith("<!DOCTYPE html>")
