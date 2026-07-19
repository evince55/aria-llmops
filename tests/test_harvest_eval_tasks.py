import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evals.harvest_eval_tasks import (
    scrub, is_task_shaped, harvest, from_transcripts, from_telemetry, norm,
)

TASK = "Add a retry with exponential backoff to the /api/play handler in backend/app.py"


# --- privacy: this repo is public and these are real operator prompts -------

def test_scrub_removes_home_paths_ips_and_emails():
    dirty = ("deploy from /Users/chait/MusicAppIOS to eugene@100.76.103.1 "
             "and email me at someone@example.com")
    clean = scrub(dirty)
    assert "/Users/chait" not in clean and "~" in clean
    assert "100.76.103.1" not in clean and "<IP>" in clean
    assert "someone@example.com" not in clean and "<EMAIL>" in clean


def test_scrub_removes_token_shaped_strings():
    assert "sk-" not in scrub("use sk-abcdef123456 for the call")
    assert "ghp_" not in scrub("token ghp_abcdef123456 works")


def test_scrubbing_happens_before_write_not_after():
    rows = harvest([("transcript", "Fix the crash on /Users/chait/app and ping 10.0.0.4 about it")])
    assert rows and "/Users/chait" not in rows[0]["task"] and "10.0.0.4" not in rows[0]["task"]


# --- task-shaped filtering -------------------------------------------------

def test_accepts_a_real_task():
    assert is_task_shaped(TASK)


def test_rejects_conversational_chatter():
    for chatter in ("proceed", "Do both.", "It is done.", "ok thanks", "merged"):
        assert not is_task_shaped(chatter)


def test_rejects_too_short_and_too_long():
    assert not is_task_shaped("fix it")
    # past 2000 chars the production classifier truncates anyway
    assert not is_task_shaped("Add a feature " + "x" * 2100)


def test_keeps_long_multiparagraph_prose_tasks():
    # the keyword-blind regime: verbose, ambiguous, exactly what the eval lacks
    prose = ("Improve the felt quality of streaming playback in the app. " * 12)
    assert 700 < len(prose) < 2000
    assert is_task_shaped(prose)


def test_rejects_harness_injected_content():
    assert not is_task_shaped("<command-message>init</command-message> add a thing to the repo")
    assert not is_task_shaped("[SYSTEM NOTIFICATION] add retries to the handler please now")


def test_rejects_pasted_docs_and_code_blocks():
    assert not is_task_shaped("# Update Config Skill — modify settings.json and add a hook to it")
    assert not is_task_shaped("```python\nadd_handler()\n```  implement the handler now")


def test_requires_an_action_verb():
    # a question about state is not a routable engineering task
    assert not is_task_shaped("is there a way I can ssh into a remote machine from the desktop app?")


# --- train/eval disjointness ----------------------------------------------

def test_training_tasks_are_excluded():
    rows = harvest([("transcript", TASK)], exclude_texts=[TASK])
    assert rows == []


def test_exclusion_is_normalization_insensitive():
    rows = harvest([("transcript", TASK)], exclude_texts=["  " + TASK.upper() + "  "])
    assert rows == []


def test_duplicates_within_the_harvest_are_dropped():
    rows = harvest([("transcript", TASK), ("telemetry", TASK + "  ")])
    assert len(rows) == 1


# --- source readers --------------------------------------------------------

def test_transcript_reader_takes_user_turns_and_flattens_blocks(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": TASK}]}}) + "\n" +
        json.dumps({"type": "assistant", "message": {"content": "I will not be harvested"}}) + "\n" +
        "not json at all\n"
    )
    got = from_transcripts([str(f)])
    assert got == [("transcript", TASK)]


def test_telemetry_reader_handles_nested_and_flat_task_text(tmp_path):
    f = tmp_path / "e.jsonl"
    f.write_text(
        json.dumps({"task_text": TASK}) + "\n" +
        json.dumps({"data": {"task_text": "Refactor the queue to use an actor"}}) + "\n"
    )
    assert [t for _, t in from_telemetry(str(f))] == [TASK, "Refactor the queue to use an actor"]


def test_missing_files_do_not_raise():
    assert from_transcripts(["/nope/missing.jsonl"]) == []
    assert from_telemetry("/nope/missing.jsonl") == []


# --- approval chatter & meta-instructions (first harvest was polluted) ------

def test_rejects_approvals_that_contain_an_action_verb():
    # these all passed the first filter because "merge"/"make" are action verbs
    for approval in (
        "Proceed with merge, it works fine.",
        "It looks good, go ahead and merge it. Ill merge it from github.com",
        "Go ahead and merge #7, I merged this from github directly.",
        "Looks good, deploy it whenever you are ready to.",
    ):
        assert not is_task_shaped(approval), approval


def test_rejects_instructions_about_the_agents_own_behaviour():
    for meta in (
        "Make a note to always utilize your skills when possible, especially superpowers",
        "From now on, always run the tests before you commit anything to the branch",
        "Remember to update the AGENTS.md file whenever you change a convention here",
    ):
        assert not is_task_shaped(meta), meta


def test_still_accepts_a_real_task_that_mentions_merging():
    assert is_task_shaped(
        "Fix the merge conflict in backend/app.py where both branches edited the ruler config"
    )


def test_redact_terms_are_a_parameter_not_baked_into_source():
    # the operator's real handles must never be literals in this public repo
    src = open(os.path.join(os.path.dirname(__file__), "..", "evals", "harvest_eval_tasks.py")).read()
    assert "redact=()" in src
    assert scrub("ssh to chai-homelab as eugene", redact=["chai-homelab", "eugene"]) \
        == "ssh to <REDACTED> as <REDACTED>"


def test_redaction_is_case_insensitive_and_applied_during_harvest():
    rows = harvest([("transcript", "Deploy the updated backend to Chai-Homelab and restart the aria service")],
                   redact=["chai-homelab"])
    assert rows and "Chai-Homelab" not in rows[0]["task"] and "<REDACTED>" in rows[0]["task"]


def test_deployment_verbs_are_recognised_as_tasks():
    # "deploy"/"restart"/"upgrade" were missing from the first verb list
    assert is_task_shaped("Deploy the updated backend service and restart it cleanly")
    assert is_task_shaped("Upgrade the pinned yt-dlp version and verify the cache still works")
