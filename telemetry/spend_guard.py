"""A budget that can refuse — the enforcement half of the cost pillar.

Finding 11 of the reproduction write-up says a cost model that cannot see the
line item exhausting your budget is not a cost model. This project then built
that visibility: `cost_control.py` prices judge calls, splits router spend from
eval spend, and flags unpriced models rather than zeroing them.

It still had no way to **stop** anything. When the A2/A2b grading runs consumed a
monthly subscription's rolling limit in a single day, every part of the system
watched it happen and reported it accurately afterwards. **A cost model that can
see the spend but not stop it is not cost control.**

WHAT THE INCIDENT ACTUALLY LOOKED LIKE, because it determines the design. It was
not one expensive task — a per-task cost cap would have passed every single call.
It was *hundreds of cheap ones* in a loop. So the budget is scoped to a **run**,
checked **before** each call, and paired with an optional **call cap**, because a
loop that never reaches a dollar limit and never terminates is the same disaster
arriving more slowly.

THREE PROPERTIES, EACH EARNED BY A FINDING IN THIS PROJECT:

* **No default budget.** Findings 12, 19 and 23 were every one of them an unnamed
  default — a serving flag, a sampling temperature, a chat-template mode — that
  nobody chose and nothing recorded. A guard with a default budget is a guard
  whose limit nobody decided.
* **Fails closed.** An unpriceable call raises rather than counting as $0.
  `cost_control` already flags unpriced models instead of zeroing them; treating
  "I don't know what this cost" as free is precisely how an unmetered loop comes
  to look like a free one.
* **Stays tripped.** Once the budget is gone the guard refuses everything, not
  just the calls it cannot afford. A run that limps on in small increments is the
  shape of the original incident.

This is the first component here whose value cannot be measured on a held-out
set. It is *testable* — does it fire when it should and not otherwise — but not
*evaluable*, because what it produces is disasters that did not happen.
"""
from __future__ import annotations

import logging

LOG = logging.getLogger("llmops.spend_guard")


class BudgetExhausted(RuntimeError):
    """Raised instead of making a call that would exceed the run's budget."""


class Unpriceable(RuntimeError):
    """Raised when a call's cost is unknown. Never silently treated as free."""


class SpendGuard:
    """A run-scoped budget that refuses calls rather than reporting on them.

    Usage is deliberately awkward in one respect: `charge()` must be called
    BEFORE the spend, with the cost the call is about to incur. Charging
    afterwards would mean the overspend has already happened and the guard is
    just a louder logger.
    """

    def __init__(self, name: str, budget_usd, max_calls: int | None = None) -> None:
        if budget_usd is None:
            raise ValueError(
                f"SpendGuard({name!r}) needs an explicit budget_usd. There is no default: "
                "an unnamed limit is a limit nobody decided (see findings 12, 19, 23).")
        budget = float(budget_usd)
        if budget < 0:
            raise ValueError(f"SpendGuard({name!r}) budget_usd must be >= 0, got {budget}")
        if max_calls is not None and max_calls < 0:
            raise ValueError(f"SpendGuard({name!r}) max_calls must be >= 0, got {max_calls}")
        self.name = name
        self.budget = budget
        self.max_calls = max_calls
        self.spent = 0.0
        self.calls = 0
        self.tripped = False
        self.trip_reason: str | None = None

    @property
    def remaining(self) -> float:
        return max(0.0, self.budget - self.spent)

    def _trip(self, reason: str) -> None:
        # Loud on purpose. The S8 classifier-degradation work established that a
        # guard which fails quietly is indistinguishable from one that never
        # fired, and this one fires exactly when someone is about to lose money.
        self.tripped = True
        self.trip_reason = reason
        LOG.error("SPEND GUARD TRIPPED [%s]: %s", self.name, reason)
        # The name travels with the exception, not just the log line: a trace
        # that says "budget exhausted" without saying WHICH budget sends you
        # reading code to find out which loop stopped.
        raise BudgetExhausted(f"SpendGuard[{self.name}]: {reason}")

    def charge(self, cost_usd) -> float:
        """Reserve `cost_usd` against the budget. Raises rather than overspending.

        Returns the remaining budget so a caller can taper before being stopped.
        """
        if self.tripped:
            # Deliberately refuses even an affordable call. A tripped run that
            # keeps going in cheap increments is the incident, in slow motion.
            raise BudgetExhausted(
                f"SpendGuard[{self.name}] already tripped: {self.trip_reason}")

        if cost_usd is None:
            # Not a trip — the budget is intact and the run may continue if the
            # caller can price the call. But it must not proceed on a guess.
            raise Unpriceable(
                f"SpendGuard[{self.name}]: cost of this call is unknown. Refusing rather "
                "than charging $0 — an unpriced call is not a free one.")

        cost = float(cost_usd)
        if cost < 0:
            raise ValueError(f"SpendGuard[{self.name}]: negative cost {cost}")

        if self.max_calls is not None and self.calls >= self.max_calls:
            self._trip(f"call cap reached: {self.calls}/{self.max_calls} calls "
                       f"(spent ${self.spent:.2f} of ${self.budget:.2f})")

        if self.spent + cost > self.budget:
            self._trip(f"would exceed budget: ${self.spent:.2f} + ${cost:.2f} "
                       f"> ${self.budget:.2f}")

        self.spent += cost
        self.calls += 1
        return self.remaining

    def report(self) -> dict:
        return {"name": self.name, "budget": round(self.budget, 6),
                "spent": round(self.spent, 6), "remaining": round(self.remaining, 6),
                "calls": self.calls, "max_calls": self.max_calls,
                "tripped": self.tripped, "trip_reason": self.trip_reason}
