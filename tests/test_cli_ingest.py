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
