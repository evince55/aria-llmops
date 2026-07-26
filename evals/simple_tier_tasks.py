"""SIMPLE-tier task pool for A2b — the tier-conditional brevity experiment.

A2 killed the blanket brevity clause on a correctness regression (0.708 → 0.625),
but the regression was CONCENTRATED: terse lost 6 tasks needing a real
implementation with edge cases and won 4 small/edit tasks. The follow-up
hypothesis is that the clause is safe where the work is genuinely small.

WHY THIS REDUCES TO ONE SLICE. A tier-conditional clause applies brevity only to
SIMPLE-tier work and leaves MODERATE+ untouched — so on MODERATE+ the two arms
are byte-identical by construction and cannot differ in quality. The whole
experiment is therefore: **does the clause hurt on SIMPLE-tier tasks?** That
makes it both cheaper and sharper than re-running the mixed set.

MEMBERSHIP IS THE ROUTER'S CALL, NOT MINE. A task belongs to this pool only if
`ModelRouter.classify_detailed` actually returns SIMPLE for it, because that is
what would gate the clause in production. Hand-labelling the pool would test a
hypothesis the router cannot act on. `select_simple()` does the filtering, so
the pool below is CANDIDATES — some will be rejected, and that is expected.

A2's gate was underpowered (n=24, so its 0.05 tolerance was 1.2 tasks). This
pool is sized to roughly double the resolution.
"""
from __future__ import annotations

import re

# A2b WAS INVALIDATED BY THIS. 64% of the pool below names a file, module or
# identifier whose contents the model was never shown, and the harness is a bare
# completion endpoint with no file access. On those tasks the honest answer is
# "show me the file" — which the baseline arm gave on 31 of 53 tasks and the
# grader marked wrong every time, so the gate rewarded confabulation and
# reported a +0.245 correctness "improvement" that was an artefact.
#
# Kept as a guard rather than deleted: a pool for a file-blind harness must be
# checkable for this, and the fix is to SUPPLY the context, not to quietly drop
# the tasks (they are what the router's SIMPLE tier actually contains).
_FILE_REF = re.compile(
    r"\b[\w/]+\.(?:py|js|ts|css|json|ya?ml|toml|cfg|md|sh|txt|xml|ini)\b"
    r"|\b(?:README|CHANGELOG|LICENSE|CONTRIBUTING)\b"
    r"|\bthe (?:docs|changelog)\b"
    r"|\bMakefile\b|\b\.gitignore\b", re.I)


def needs_file_context(task: str) -> bool:
    """True when the task names something the model would have to be shown.

    A file-blind harness cannot grade these: asking for the file is the correct
    answer, and any harness that scores that as failure is measuring willingness
    to fabricate.
    """
    return bool(_FILE_REF.search(task or ""))

CANDIDATES = (
    # typos / naming / formatting
    "Fix the typo 'recieve' in the comment above parse_response in api/client.py.",
    "Rename the variable `tmp` to `parsed_payload` in handlers/webhook.py.",
    "Fix the misspelled CSS class `.conatiner` in styles/layout.css.",
    "Correct 'seperate' to 'separate' in the README installation section.",
    "Rename the function `doIt` to `applyDiscount` in cart.js.",
    "Reformat settings.py to 4-space indentation; it currently mixes tabs and spaces.",
    "Fix the inconsistent capitalisation of 'GitHub' in CONTRIBUTING.md.",
    "Rename `usr_id` to `user_id` in the three places it appears in models/order.py.",
    # comments / docs
    "Add a one-line comment above the regex in validators.py explaining what it matches.",
    "Add a docstring to `slugify(title)` describing the arguments and return value.",
    "Document the `timeout` parameter in the fetch helper's docstring.",
    "Add a comment explaining why the retry count is 3 in uploader.py.",
    "Update the README to mention that Python 3.10 is the minimum version.",
    "Add a note to the changelog that the /v1/legacy endpoint is deprecated.",
    "Fix the broken link to the contributing guide in the README.",
    "Add an example value to the `query` field in the OpenAPI schema so docs show a sample.",
    # logging / prints / small edits
    "Add a log line at INFO level when the cache is cleared in cache.py.",
    "Change the print statement in migrate.py to use the logging module at DEBUG level.",
    "Add the request id to the existing error log line in middleware.py.",
    "Bump the version string in pyproject.toml from 0.4.2 to 0.4.3.",
    "Update the copyright year in LICENSE to 2026.",
    "Change the default page size constant from 20 to 50 in constants.py.",
    "Add a `# noqa: E501` to the one over-long line in legacy/import.py.",
    "Remove the unused `import os` at the top of utils/text.py.",
    # tiny single-function work
    "Write a one-line function `is_even(n)` that returns whether n is even.",
    "Write a function `to_snake_case(s)` that lowercases and replaces spaces with underscores.",
    "Write a helper that returns the file extension from a filename.",
    "Write a function that clamps a number between a min and a max.",
    "Write a one-liner that reverses the keys and values of a dict.",
    "Write a function `first_or_none(items)` returning the first element or None.",
    "Write a predicate `is_blank(s)` that is true for None, empty, or whitespace-only strings.",
    "Write a function that joins a list of path segments with forward slashes.",
    "Write a helper `pct(part, whole)` returning a percentage rounded to one decimal.",
    "Write a function that truncates a string to n characters with an ellipsis.",
    # config / small fixes
    "Add a `.DS_Store` entry to .gitignore.",
    "Set the HTTP client timeout to 10 seconds in api/client.py; it is currently unset.",
    "Add the `--verbose` flag to the pytest invocation in the Makefile's test target.",
    "Change the log level in config.yaml from WARNING to INFO.",
    "Add `py.typed` to the package data list in setup.cfg.",
    "Fix the YAML indentation error around line 23 of .github/workflows/release.yml.",
    "Add a missing trailing newline to scripts/deploy.sh.",
    "Pin the requests version to >=2.31 in requirements.txt.",
    # trivial test edits
    "Add an assertion to test_slugify that an empty string returns an empty string.",
    "Rename the test `test_1` to `test_rejects_empty_email` in tests/test_forms.py.",
    "Mark the flaky test in tests/test_upload.py with `@pytest.mark.skip` and a reason.",
    "Add a test id to the parametrised cases in tests/test_parser.py.",
    "Fix the failing assertion in test_pct: it expects 33.3 but the function returns 33.33.",
    "Add `tmp_path` to the fixture list of test_writes_file so it stops using /tmp.",
    "Change the hardcoded port 8000 in tests/conftest.py to a fixture value.",
    "Add a `__repr__` to the Point dataclass returning 'Point(x, y)'.",
    "Remove the commented-out block at the bottom of parsers/csv.py.",
    "Add type hints to `def merge(a, b):` in utils/dicts.py.",
    # --- second block: sampled to hit the router's actual SIMPLE vocabulary ---
    # The router's SIMPLE tier fires on typo/rename/comment/docs/test/log/format/
    # bump/lint, so a pool that ignores those would not be the population the
    # clause actually gates in production. These are still genuinely small work.
    "Fix the typo in the docstring of `normalize_email` in auth/utils.py.",
    "Rename the test file test_a.py to test_email_validation.py.",
    "Add a comment explaining the magic number 86400 in scheduler.py.",
    "Fix the typo 'lenght' in the error message in validators.py.",
    "Rename the `cb` parameter to `on_complete` in the download helper.",
    "Format the JSON fixture in tests/data/user.json with 2-space indentation.",
    "Add a docstring to the `retry` decorator explaining the backoff behaviour.",
    "Fix the typo in the log message 'Faild to connect' in db/pool.py.",
    "Rename the constant `MAX` to `MAX_RETRIES` in worker.py.",
    "Add a comment above the sleep call in poller.py explaining the interval.",
    "Update the docs to rename the `--out` flag to `--output` in the CLI reference.",
    "Fix the lint error about an unused variable in reports/render.py.",
    "Add a log line when the worker starts, at INFO level.",
    "Rename `helper2` to `format_currency` in billing/format.py.",
    "Fix the typo 'occured' in three comments across the parsers package.",
    "Add a docstring to the Config dataclass listing its fields.",
    "Reformat the long import list in app/__init__.py one per line.",
    "Bump the pinned pytest version and note it in the changelog docs.",
    "Add a comment marking the TODO in cache.py with the tracking issue number.",
    "Rename the log category from 'app' to 'aria' in logging_config.py.",
    "Fix the typo in the CLI help text for the `--dry-run` flag.",
    "Add a docstring to `chunk(items, n)` describing the last-batch behaviour.",
    "Rename the fixture `db` to `session` in tests/conftest.py.",
    "Add a comment explaining why the lock is released before the callback.",
    "Fix the docs example that still shows the removed `legacy=True` argument.",
    "Rename the module `misc.py` to `text_utils.py` and update its two imports.",
    "Add a log line at WARNING when a retry is exhausted.",
    "Fix the typo 'paramter' in the function comment in api/serializers.py.",
    "Format the markdown table in the README so the columns align.",
    "Add a docstring to the test helper `make_user()` explaining its defaults.",
    "Rename the variable `x` to `elapsed_ms` in the timing block of profiler.py.",
    "Fix the typo in the comment above the rate-limit constant.",
    "Add a comment noting that the sort is stable in ranking.py.",
    "Rename `test_thing` to `test_rejects_negative_amounts` in tests/test_billing.py.",
    "Fix the lint warning about a bare except in scripts/backfill.py.",
)


def select_simple(classify, candidates=CANDIDATES, limit: int = 0) -> list:
    """Keep only the candidates the ROUTER classifies SIMPLE.

    `classify(task) -> (tier, matched)` — the router's own
    `classify_detailed`. Membership must be the router's decision because that
    is what would gate the clause in production; a hand-labelled pool would test
    a rule the router cannot actually apply.
    """
    kept = []
    for task in candidates:
        tier, _ = classify(task)
        if tier == "SIMPLE":
            kept.append(task)
            if limit and len(kept) >= limit:
                break
    return kept
