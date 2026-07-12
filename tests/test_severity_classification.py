"""The keyword classifier escalates consequence/severity prose to CRITICAL —
data loss, financial errors, data exposure, outage — WITHOUT the model, and does
not over-escalate ordinary tasks that merely touch a sensitive domain. Offline
and deterministic (keyword path only); the 9B severity behaviour is covered by
evals/severity_eval.py against a live model.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import ModelRouter

_R = ModelRouter(log_decisions=False)

CRITICAL_SEVERITY = [
    "When two saves race, the file gets truncated and users permanently lose their playlists.",
    "Downloaded files are world-readable so other apps can read the user's library.",
    "The sync job overwrites the newer copy so users lose their edits.",
    "If this fails it could cause permanent data loss for every account.",
    "The webhook retries double-charge the customer on every retry.",
    "There's a production outage during peak checkout hours.",
]

# Touch a sensitive DOMAIN (payment/library/checkout) but carry no severity
# consequence — must NOT be escalated to CRITICAL by the keyword layer.
NOT_CRITICAL_DOMAIN = [
    "Add a loading spinner to the payment button while the charge is processing.",
    "Cache the user's library file in memory to speed up repeated reads.",
    "Add unit tests for the checkout total calculation.",
]


def test_severity_prose_escalates_to_critical():
    for task in CRITICAL_SEVERITY:
        tier, matched = _R.classify_detailed(task)
        assert tier == "CRITICAL" and matched, (task, tier, matched)


def test_domain_mention_without_severity_not_escalated():
    for task in NOT_CRITICAL_DOMAIN:
        tier, _ = _R.classify_detailed(task)
        assert tier != "CRITICAL", (task, tier)
