"""Pin the swap-aware timeout for auxiliary model calls (classify / grade).

Why this exists: under the llama-swap topology the 9B classifier may need to be
swapped in before it can answer — measured 13.9s wall for a classify issued
right after the 35B held the GPU (2026-07-09), vs 0.3s once resident. The old
hardcoded 12s timeout was BELOW the measured swap-in, so the first 9B rescue
after 35B work always timed out and silently fell back to keywords. These tests
pin (a) the default clears the measured swap-in with margin, (b) the router
actually passes the constant to the client, (c) env override works.
"""
import subprocess
import sys

import llmops
from llmops import ModelRouter


MEASURED_SWAP_IN_SECONDS = 13.9  # 2026-07-09, 35B resident -> 9B classify, this box


def test_default_clears_measured_swap_in_with_margin():
    assert llmops.MODEL_CALL_TIMEOUT >= 2 * MEASURED_SWAP_IN_SECONDS, (
        "default timeout must comfortably cover a llama-swap swap-in, "
        "or the 9B rescue silently degrades to keywords in the live topology"
    )


def test_default_is_bounded_so_routing_cannot_hang():
    assert llmops.MODEL_CALL_TIMEOUT <= 120


class _RecordingClient:
    """Stands in for LocalLlamaClient; records the timeout it was given."""

    def __init__(self):
        self.seen_timeout = None

    def complete(self, prompt, max_tokens=8, timeout=None, temperature=0.2):
        self.seen_timeout = timeout
        return "SIMPLE", {}


def test_classify_via_model_passes_the_constant_to_the_client():
    client = _RecordingClient()
    router = ModelRouter(log_decisions=False, classifier_client=client)
    tier, source = router.classify_via_model("fix a typo in the README")
    assert (tier, source) == ("SIMPLE", "model")
    assert client.seen_timeout == llmops.MODEL_CALL_TIMEOUT


def test_env_var_overrides_default():
    # The constant is read at import; probe a fresh interpreter with the env set.
    out = subprocess.run(
        [sys.executable, "-c",
         "import os; os.environ['LLMOPS_MODEL_CALL_TIMEOUT']='7.5'; "
         "import llmops; print(llmops.MODEL_CALL_TIMEOUT)"],
        capture_output=True, text=True, cwd=str(llmops.Path(llmops.__file__).parent),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "7.5"
