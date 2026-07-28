"""S10 ensemble — combine independent tool-call arms.

Rules are PRE-REGISTERED in `docs/research/2026-07-26-s10-ensemble-preregistration.md`,
written before any ensemble was run. Both arms' row-level failures were already
known by then, so a rule invented afterwards would be fitted to 13 examples and
would measure my recall of the failure table rather than the method. Finding 14
of the write-up is about precisely this class of self-deception.

WHAT AN ENSEMBLE OF TWO CAN AND CANNOT DO. With two arms and no tiebreaker there
is no majority, so `agree_or_escalate` cannot raise accuracy — it can only
identify *when the small models are trustworthy*. That is selective prediction,
and it is reported as such: coverage, precision on covered rows, and an
end-to-end accuracy that counts an abstention as unanswered. Reporting only
"precision on covered rows" would let a rule that abstains on everything score
1.00, which is why `cascade_score` returns both.
"""
from __future__ import annotations


def _key(answer):
    """Order-insensitive identity of an answer. Falsy answers are never equal.

    Handles both shapes these rules are used on: a tool call `{tool, args}` and a
    plain label string like `"COMPLEX"`. Rule A is the only round-2 claim that
    survived every instrument change, so it is the one worth transferring to the
    routing task — and transferring it must not mean re-implementing it. Two
    copies would drift, and the second would be a second thing to get wrong.

    An empty answer is NOT an answer: two arms both returning "" have not agreed,
    they have both failed, and treating that as consensus would ship exactly the
    rows nothing understood.
    """
    if not answer:
        return None
    if isinstance(answer, str):
        return answer
    if not isinstance(answer, dict):
        return None
    args = answer.get("args")
    if not isinstance(args, dict):
        return None
    return (answer.get("tool"), tuple(sorted(args.items(), key=lambda kv: kv[0])))


def agree_or_escalate(calls) -> dict:
    """RULE A. Accept only when every arm emits the same call.

    A missing call from any arm escalates. One arm failing to answer is not
    licence to trust the other alone — the whole premise is that a lone small
    model is the thing being checked.
    """
    keys = [_key(c) for c in calls]
    accepted = bool(keys) and all(k is not None and k == keys[0] for k in keys)
    return {"accepted": accepted, "call": calls[0] if accepted else None,
            "votes": len(calls)}


def majority(calls):
    """RULE B. The call emitted by at least 2 arms, else None.

    Arms that produced no call do not vote. Two silent arms must not be read as
    agreeing on silence.
    """
    tally = {}
    for c in calls:
        k = _key(c)
        if k is None:
            continue
        tally.setdefault(k, []).append(c)
    if not tally:
        return None
    best = max(tally.values(), key=len)
    return best[0] if len(best) >= 2 else None


def cascade_score(rows) -> dict:
    """Score RULE A. `rows` carry `accepted`, `call`, `truth`.

    Three numbers, because one of them alone is misleading:
      coverage                        — how often the cascade answered at all
      precision_on_covered            — how right it was when it did
      accuracy_if_unanswered_is_wrong — the end-to-end number, escalations
                                        counted as unanswered
    A rule that abstains on everything scores precision 1.00 and accuracy 0.00.
    """
    n = len(rows) or 1
    covered = [r for r in rows if r["accepted"]]
    right = sum(_key(r["call"]) == _key(r["truth"]) for r in covered)
    coverage = len(covered) / n
    return {
        "n": len(rows),
        "coverage": coverage,
        "precision_on_covered": (right / len(covered)) if covered else None,
        "escalation_rate": 1 - coverage,
        "accuracy_if_unanswered_is_wrong": right / n,
    }


def vote_score(rows) -> dict:
    """Score RULE B. `rows` carry `call` (possibly None) and `truth`."""
    n = len(rows) or 1
    right = sum(_key(r["call"]) == _key(r["truth"]) for r in rows if r["call"])
    abstained = sum(1 for r in rows if not r["call"])
    return {"n": len(rows), "accuracy": right / n, "abstain_rate": abstained / n}
