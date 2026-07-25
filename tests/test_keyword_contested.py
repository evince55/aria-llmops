"""The keyword classifier must not report confidence it hasn't earned.

`classify_detailed` returns (tier, matched), and `matched` means "trust this,
don't consult a model". CRITICAL/COMPLEX/SIMPLE win on a SINGLE keyword and are
checked before MODERATE — so one broad word (`performance`, `test`, `docs`)
silently overrides multi-signal evidence pointing at MODERATE.

Measured 2026-07-25: keywords answer 29 of 60 MODERATE eval rows and get 45%
right, versus the model's 74%. `Add portfolio performance calculation endpoint`
reads COMPLEX on `performance`; `[REFACTOR] Rename variables` reads COMPLEX on
`refactor`. The rules match subject-matter VOCABULARY while the tier is set by
the WORK, and no additional rule fixes that.

The fix is not to delete rules but to stop overclaiming: when a single-keyword
tier fires AND there is competing MODERATE evidence, the row is CONTESTED —
report the tier but mark it unconfident, so `classify_hybrid` consults the
model. Deferring only contested rows preserves keyword accuracy on the
uncontested ones (they were 32/32 on true-SIMPLE), which is what made the
blunt "defer every SIMPLE/COMPLEX prediction" policy regress SIMPLE.

CRITICAL is deliberately exempt: under-routing security work is far worse than
over-routing it, and CRITICAL recall is already 0.93-0.97.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llmops  # noqa: E402
from llmops import CodingMemory, ModelRouter  # noqa: E402


@pytest.fixture
def guard_on(monkeypatch):
    """The guard ships OFF: it promotes against the S7 config but regresses
    cumulative SIMPLE against the original incumbent. Tests opt in explicitly."""
    monkeypatch.setattr(llmops, "KEYWORD_CONTESTED_GUARD", True)


def _router(tmp_path, **kw):
    return ModelRouter(memory=CodingMemory(tmp_path / "m.json"),
                       ledger=tmp_path / "e.jsonl", log_decisions=False, **kw)


class TestDefaultIsOff:
    """Production behaviour must be byte-identical unless the flag is set."""

    def test_contested_row_stays_confident_by_default(self, tmp_path):
        assert llmops.KEYWORD_CONTESTED_GUARD is False
        assert _router(tmp_path).classify_detailed(
            "Add portfolio performance calculation endpoint") == ("COMPLEX", True)

    def test_flag_enables_the_guard(self, tmp_path, guard_on):
        assert _router(tmp_path).classify_detailed(
            "Add portfolio performance calculation endpoint") == ("COMPLEX", False)


class TestUncontestedRowsAreUnchanged:
    """The guard must not touch rows where keywords are reliable."""

    def test_typo_stays_confident_simple(self, tmp_path):
        assert _router(tmp_path).classify_detailed("fix a typo in the README") == ("SIMPLE", True)

    def test_rename_stays_confident_simple(self, tmp_path):
        assert _router(tmp_path).classify_detailed(
            "rename a variable in PlayerManager") == ("SIMPLE", True)

    def test_moderate_two_hit_path_is_unchanged(self, tmp_path):
        assert _router(tmp_path).classify_detailed(
            "Build a star-rating component with hover preview") == ("MODERATE", True)

    def test_no_match_still_defaults_unconfidently(self, tmp_path):
        tier, matched = _router(tmp_path).classify_detailed("qwerty zxcvb")
        assert (tier, matched) == ("MODERATE", False)


class TestContestedRowsLoseConfidence:
    def test_complex_keyword_with_competing_moderate_evidence_is_contested(self, tmp_path, guard_on):
        """`performance` fires COMPLEX; `add`+`endpoint` say MODERATE. It is
        MODERATE work, and the keywords cannot tell — so do not claim they can."""
        tier, matched = _router(tmp_path).classify_detailed(
            "Add portfolio performance calculation endpoint")
        assert tier == "COMPLEX"
        assert matched is False

    def test_simple_keyword_with_one_moderate_hit_is_contested(self, tmp_path, guard_on):
        """`test` fires SIMPLE; `add` competes. One hit is enough — the dev set
        showed the >=1 threshold recovers more MODERATE at zero SIMPLE cost."""
        tier, matched = _router(tmp_path).classify_detailed(
            "Add a unit test for the checkout total calculation")
        assert tier == "SIMPLE"
        assert matched is False

    def test_the_reported_tier_is_still_the_keyword_tier(self, tmp_path, guard_on):
        """Callers without a model must keep getting the keyword's best guess;
        only the confidence flag changes."""
        tier, _ = _router(tmp_path).classify_detailed(
            "Add portfolio performance calculation endpoint")
        assert tier == "COMPLEX"


class TestCriticalIsExempt:
    """Asymmetric error cost: over-routing security work is the safe mistake."""

    def test_critical_keeps_confidence_despite_competing_evidence(self, tmp_path, guard_on):
        tier, matched = _router(tmp_path).classify_detailed(
            "Add a logout endpoint that wipes the JWT token")
        assert (tier, matched) == ("CRITICAL", True)

    def test_critical_with_many_moderate_hits_still_preempts(self, tmp_path, guard_on):
        tier, matched = _router(tmp_path).classify_detailed(
            "Add and implement a new auth service component and endpoint")
        assert (tier, matched) == ("CRITICAL", True)


class TestHybridConsultsTheModelOnContestedRows:
    def test_contested_row_reaches_the_model(self, tmp_path, guard_on):
        class _Clf:
            def complete(self, prompt, max_tokens=800, timeout=None, temperature=0.2):
                return "MODERATE", {}
        r = _router(tmp_path, use_model_classifier=True, classifier_client=_Clf())
        tier, matched = r.classify_hybrid("Add portfolio performance calculation endpoint")
        assert tier == "MODERATE", "contested row did not reach the model"

    def test_uncontested_row_never_reaches_the_model(self, tmp_path, guard_on):
        class _Boom:
            def complete(self, *a, **k):
                raise AssertionError("model consulted for an uncontested keyword row")
        r = _router(tmp_path, use_model_classifier=True, classifier_client=_Boom())
        assert r.classify_hybrid("fix a typo in the README")[0] == "SIMPLE"
