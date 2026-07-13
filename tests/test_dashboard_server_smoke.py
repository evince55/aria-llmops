"""Boot dashboard/server.py as a real subprocess with NO local models and hit
the five panes' endpoints. This is the community-tester path (and the CI
cross-OS proof): stdlib only, keyword classifier, execution never triggered.

CI note: macOS runners can be very slow to spawn a process (first run blew a
15s deadline with the server's stderr discarded — undiagnosable). So: capture
the server log, fail FAST with it if the process dies, probe the socket before
HTTP, and give startup a CI-grade deadline. A healthy server still passes in
about a second; the generous deadline only spends time when something is wrong."""
import json
import os
import random
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STARTUP_DEADLINE_S = 120


def _tail(path, n=30):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:]) or "(empty log)"
    except OSError as e:
        return f"(no log: {e})"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("smoke")
    port = random.randint(20000, 40000)
    log_path = tmp / "server.log"
    env = dict(os.environ,
               ARIA_DASH_PORT=str(port),
               ARIA_DASH_HOST="127.0.0.1",
               # a ledger in the pytest tmp dir so the smoke run never touches real data
               LLMOPS_LEDGER=str(tmp / "events.smoke.jsonl"))
    # No LLMOPS_CLASSIFIER_* -> whatever default endpoint is absent on CI ->
    # classify_hybrid degrades to keyword-only. That degradation IS the test.
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-u", str(REPO / "dashboard" / "server.py")],
                            cwd=str(REPO), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        # Phase 1: wait for the port to accept a TCP connection at all.
        deadline = time.time() + STARTUP_DEADLINE_S
        while True:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"server exited rc={proc.returncode} before listening; log:\n{_tail(log_path)}")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                if time.time() > deadline:
                    raise RuntimeError(
                        f"server never listened within {STARTUP_DEADLINE_S}s; log:\n{_tail(log_path)}")
                time.sleep(0.5)
        # Phase 2: wait for HTTP to answer (imports done, handler wired).
        last = None
        while time.time() < deadline:
            try:
                urllib.request.urlopen(base + "/api/overview", timeout=10)
                break
            except Exception as e:  # noqa: BLE001 — retry until deadline
                last = e
                time.sleep(0.5)
        else:
            raise RuntimeError(f"listening but no HTTP answer: {last}; log:\n{_tail(log_path)}")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        log.close()


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
