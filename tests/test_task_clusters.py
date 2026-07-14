import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evals.task_clusters import cluster_texts, report


def test_near_duplicates_cluster_together():
    texts = [
        "fix the race condition in PlayerManager queue handling",
        "fix a race condition in the PlayerManager queue",
        "write a marketing plan for the app store launch",
    ]
    clusters = cluster_texts(texts)
    by_member = {i: ci for ci, members in enumerate(clusters) for i in members}
    assert by_member[0] == by_member[1]
    assert by_member[2] != by_member[0]


def test_clustering_is_deterministic():
    texts = ["alpha beta gamma", "alpha beta delta", "unrelated zebra text",
             "zebra text unrelated extras"]
    assert cluster_texts(texts) == cluster_texts(texts)


def test_singletons_allowed():
    clusters = cluster_texts(["one lonely task about databases"])
    assert clusters == [[0]]


def test_report_aggregates_spend_per_cluster():
    ev = [
        {"event": "route_decision", "task_text": "fix race condition in queue",
         "complexity": "COMPLEX", "session_id": "s1", "harness": "claude-code",
         "chosen_model": "x", "estimated_usd": 0, "alternatives": [], "ts": "t"},
        {"event": "route_decision", "task_text": "fix the race condition in the queue",
         "complexity": "COMPLEX", "session_id": "s2", "harness": "claude-code",
         "chosen_model": "x", "estimated_usd": 0, "alternatives": [], "ts": "t"},
        {"event": "usage", "session_id": "s1", "task_text": "fix race condition in queue",
         "model": "claude-opus-4-8", "imputed_usd": 10.0, "outcome": "success", "msg_id": "a"},
        {"event": "usage", "session_id": "s2", "task_text": "fix the race condition in the queue",
         "model": "claude-opus-4-8", "imputed_usd": 5.0, "outcome": "success", "msg_id": "b"},
    ]
    r = report(ev)
    assert r["n_clusters"] >= 1
    top = r["clusters"][0]
    assert top["n_tasks"] == 2
    assert abs(top["session_usd"] - 15.0) < 1e-6
    assert top["example"].startswith("fix")
