"""Boot dashboard/server.py as a real subprocess with NO local models and hit
the five panes' endpoints. This is the community-tester path (and the CI
cross-OS proof): stdlib only, keyword classifier, execution never triggered."""
import json
import os
import random
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def server():
    port = random.randint(20000, 40000)
    env = dict(os.environ,
               ARIA_DASH_PORT=str(port),
               ARIA_DASH_HOST="127.0.0.1",
               # a ledger in a temp spot so the smoke run never touches real data
               LLMOPS_LEDGER=str(REPO / "telemetry" / f"events.smoke-{port}.jsonl"))
    # No LLMOPS_CLASSIFIER_* -> whatever default endpoint is absent on CI ->
    # classify_hybrid degrades to keyword-only. That degradation IS the test.
    proc = subprocess.Popen([sys.executable, str(REPO / "dashboard" / "server.py")],
                            cwd=str(REPO), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 15
        last = None
        while time.time() < deadline:
            try:
                urllib.request.urlopen(base + "/api/overview", timeout=2)
                break
            except Exception as e:  # noqa: BLE001 — retry until deadline
                last = e
                time.sleep(0.3)
        else:
            raise RuntimeError(f"server never came up: {last}")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        smoke = env["LLMOPS_LEDGER"]
        if os.path.exists(smoke):
            os.remove(smoke)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def test_overview(server):
    code, d = _get(server, "/api/overview")
    assert code == 200 and "imputed_usd" in d and "route_decisions" in d


def test_calculator(server):
    code, d = _get(server, "/api/calculator")
    assert code == 200 and isinstance(d, dict) and d


def test_datasets_lists_the_shipped_sets(server):
    code, d = _get(server, "/api/datasets")
    names = {x["name"] for x in d["datasets"]}
    assert code == 200 and "labeled_tasks_balanced.jsonl" in names


def test_run_decide_only_no_model(server):
    """Routing must work with zero models (keyword classifier)."""
    code, d = _post(server, "/api/run", {"task": "fix a typo in the readme", "execute": False})
    assert code == 200 and d.get("tier") in ("SIMPLE", "MODERATE", "COMPLEX", "CRITICAL")
    assert d.get("executed") is False and d.get("run_id")


def test_ledger_explorer_sees_the_run(server):
    code, d = _get(server, "/api/ledger?harness=dashboard-runner")
    assert code == 200 and {"rows", "facets", "summary"} <= set(d)


def test_cut_endpoints_stay_cut(server):
    for path in ("/api/classification", "/api/liverun"):
        try:
            urllib.request.urlopen(server + path, timeout=5)
            assert False, path + " should 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
