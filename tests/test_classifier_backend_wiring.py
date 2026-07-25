"""The router must actually USE the configured classifier backend.

Config resolution and the MLX client are tested separately; this pins the wiring
between them — the step that turns S7's promoted adapter from "cleared by the
gate" into "what production runs".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llmops  # noqa: E402
from llmops import CodingMemory, ModelRouter  # noqa: E402
from mlx_classifier import MLXClassifierClient  # noqa: E402


def _router(tmp_path, **kw):
    return ModelRouter(memory=CodingMemory(tmp_path / "m.json"),
                       ledger=tmp_path / "e.jsonl", log_decisions=False, **kw)


class TestBackendSelection:
    def test_http_backend_builds_the_http_client(self, tmp_path, monkeypatch):
        monkeypatch.setattr(llmops, "CLASSIFIER_BACKEND", "http", raising=False)
        assert isinstance(_router(tmp_path).classifier_client, llmops.LocalLlamaClient)

    def test_mlx_backend_builds_the_local_mlx_client(self, tmp_path, monkeypatch):
        monkeypatch.setattr(llmops, "CLASSIFIER_BACKEND", "mlx", raising=False)
        assert isinstance(_router(tmp_path).classifier_client, MLXClassifierClient)

    def test_mlx_backend_uses_the_configured_base_and_adapter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(llmops, "CLASSIFIER_BACKEND", "mlx", raising=False)
        monkeypatch.setattr(llmops, "MLX_BASE", "/models/e2b", raising=False)
        monkeypatch.setattr(llmops, "MLX_ADAPTER", "/adapters/e2b_v3", raising=False)
        c = _router(tmp_path).classifier_client
        assert (c.model_path, c.adapter_path) == ("/models/e2b", "/adapters/e2b_v3")

    def test_an_explicit_client_still_wins_over_the_backend_setting(self, tmp_path, monkeypatch):
        """Injection is how every existing test and the eval harness drive the
        router; the backend default must never override an explicit client."""
        monkeypatch.setattr(llmops, "CLASSIFIER_BACKEND", "mlx", raising=False)
        sentinel = object()
        assert _router(tmp_path, classifier_client=sentinel).classifier_client is sentinel

    def test_selecting_mlx_does_not_load_a_model_at_construction(self, tmp_path, monkeypatch):
        """Building a router must stay cheap — a 3 GB load here would punish
        every keyword-only invocation, including `--help`."""
        monkeypatch.setattr(llmops, "CLASSIFIER_BACKEND", "mlx", raising=False)
        c = _router(tmp_path).classifier_client
        assert c._model is None
