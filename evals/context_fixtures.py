"""A tiny synthetic repo so SIMPLE-tier tasks are actually answerable (A2b).

A2b was invalidated because the harness is a bare completion endpoint with no
file access while the router's SIMPLE tier is mostly file-edit work: 64% of the
pool named files the model was never shown, the baseline correctly answered
"show me the file", and the grader marked all 31 such answers wrong. The gate
measured willingness to fabricate.

The fix is not to avoid file-edit tasks — those ARE the population a
tier-conditional brevity clause would gate — but to SUPPLY the file, which is
what a real build agent has. Each task here names a file in FIXTURES and is
rendered with that file's contents inline, so the work is genuinely doable and
"please provide the file" is no longer the right answer.

THE LOAD-BEARING INVARIANT: every task declares `must_contain` — the exact
tokens it refers to — and a test asserts those tokens appear in the file it
names. Without that, a fixture can silently drift away from its task and the
round is invalid again in the same way, just harder to notice. A task whose
target is missing from its file is unanswerable by construction.

Both arms are rendered from the identical fixture; only the brevity clause
differs. Files are deliberately small: the point is to make the task answerable,
not to test long-context handling.
"""
from __future__ import annotations

FIXTURES = {
    "api/client.py": '''\
import requests

BASE_URL = "https://api.example.com"


def parse_response(resp):
    # Parse the JSON body we recieve from the upstream service.
    tmp = resp.json()
    return tmp.get("data", {})


def fetch(path):
    return requests.get(f"{BASE_URL}/{path}")
''',
    "utils/text.py": '''\
import os
import re


def slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def truncate(s, n):
    return s if len(s) <= n else s[: n - 1] + "\\u2026"
''',
    "uploader.py": '''\
import time

MAX_RETRIES = 3


def upload(client, path):
    for attempt in range(MAX_RETRIES):
        try:
            return client.put(path)
        except OSError:
            time.sleep(2 ** attempt)
    raise RuntimeError("upload failed")
''',
    "validators.py": '''\
import re

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def check_length(value, limit):
    if len(value) > limit:
        raise ValueError(f"value exceeds maximum lenght of {limit}")
    return value
''',
    "cache.py": '''\
_store = {}


def get(key):
    return _store.get(key)


def clear():
    _store.clear()


# TODO: add per-key expiry
''',
    "worker.py": '''\
import queue

MAX = 8
_jobs = queue.Queue()


def run_forever(handler):
    while True:
        job = _jobs.get()
        handler(job)
''',
    "db/pool.py": '''\
import logging

LOG = logging.getLogger(__name__)
_pool = None


def connect(dsn):
    global _pool
    try:
        _pool = _open(dsn)
    except OSError:
        LOG.error("Faild to connect to the database")
        raise
    return _pool
''',
    "billing/format.py": '''\
def helper2(amount, currency="USD"):
    return f"{amount:,.2f} {currency}"


def total(lines):
    return sum(line.amount for line in lines)
''',
    "tests/test_forms.py": '''\
import pytest

from forms import submit


def test_1():
    assert submit({"email": "a@b.com"})["ok"] is True


def test_rejects_missing_email():
    with pytest.raises(ValueError):
        submit({})
''',
    "config.yaml": '''\
service:
  name: aria
  port: 8000
logging:
  level: WARNING
cache:
  ttl_seconds: 300
''',
}

# Each entry: the task, the file it edits, and the exact tokens it refers to.
# `must_contain` is the invariant a test enforces — see the module docstring.
TASKS = (
    {"task": "Fix the typo 'recieve' in the comment inside parse_response.",
     "file": "api/client.py", "must_contain": ("recieve",)},
    {"task": "Rename the local variable `tmp` to `payload` in parse_response.",
     "file": "api/client.py", "must_contain": ("tmp",)},
    {"task": "Add a docstring to parse_response describing what it returns.",
     "file": "api/client.py", "must_contain": ("def parse_response",)},
    {"task": "Set a 10 second timeout on the requests.get call in fetch.",
     "file": "api/client.py", "must_contain": ("requests.get",)},

    {"task": "Remove the unused `import os` at the top of this file.",
     "file": "utils/text.py", "must_contain": ("import os",)},
    {"task": "Add a docstring to slugify describing its argument and return value.",
     "file": "utils/text.py", "must_contain": ("def slugify",)},
    {"task": "Add a comment above the regex in slugify explaining what it matches.",
     "file": "utils/text.py", "must_contain": ("re.sub",)},
    {"task": "Rename the parameter `n` to `max_len` in truncate.",
     "file": "utils/text.py", "must_contain": ("def truncate",)},

    {"task": "Add a comment explaining why MAX_RETRIES is 3.",
     "file": "uploader.py", "must_contain": ("MAX_RETRIES = 3",)},
    {"task": "Add a log line at WARNING level when a retry is exhausted.",
     "file": "uploader.py", "must_contain": ("raise RuntimeError",)},
    {"task": "Add a docstring to upload describing the retry behaviour.",
     "file": "uploader.py", "must_contain": ("def upload",)},

    {"task": "Fix the typo 'lenght' in the error message in check_length.",
     "file": "validators.py", "must_contain": ("lenght",)},
    {"task": "Add a comment above the VIDEO_ID regex explaining what it matches.",
     "file": "validators.py", "must_contain": ("VIDEO_ID",)},
    {"task": "Add a docstring to check_length describing when it raises.",
     "file": "validators.py", "must_contain": ("def check_length",)},

    {"task": "Add a log line at INFO level when the cache is cleared.",
     "file": "cache.py", "must_contain": ("def clear",)},
    {"task": "Add a comment marking the TODO with the tracking issue number ARIA-42.",
     "file": "cache.py", "must_contain": ("# TODO",)},
    {"task": "Add a docstring to get describing what it returns for a missing key.",
     "file": "cache.py", "must_contain": ("def get",)},

    {"task": "Rename the constant `MAX` to `MAX_WORKERS`.",
     "file": "worker.py", "must_contain": ("MAX = 8",)},
    {"task": "Add a comment explaining that run_forever blocks on the queue.",
     "file": "worker.py", "must_contain": ("def run_forever",)},

    {"task": "Fix the typo in the log message 'Faild to connect'.",
     "file": "db/pool.py", "must_contain": ("Faild",)},
    {"task": "Add a docstring to connect describing the global it sets.",
     "file": "db/pool.py", "must_contain": ("def connect",)},

    {"task": "Rename `helper2` to `format_currency` and update nothing else.",
     "file": "billing/format.py", "must_contain": ("helper2",)},
    {"task": "Add a docstring to total describing what it sums.",
     "file": "billing/format.py", "must_contain": ("def total",)},

    {"task": "Rename the test `test_1` to `test_accepts_a_valid_email`.",
     "file": "tests/test_forms.py", "must_contain": ("def test_1",)},
    {"task": "Add a docstring to test_rejects_missing_email saying what it asserts.",
     "file": "tests/test_forms.py", "must_contain": ("def test_rejects_missing_email",)},

    {"task": "Change the logging level from WARNING to INFO.",
     "file": "config.yaml", "must_contain": ("level: WARNING",)},
    {"task": "Add a comment above the cache section noting the ttl is in seconds.",
     "file": "config.yaml", "must_contain": ("ttl_seconds",)},

    # --- second block: weighted to the router's SIMPLE vocabulary ---
    # The clause is gated by what classify_detailed calls SIMPLE (typo / rename /
    # comment / docstring / log / format / test / lint), so the pool must sample
    # that population rather than small work in general. Every entry still
    # targets a token present in its fixture — see unanswerable().
    {"task": "Add a comment above BASE_URL noting it is the production host.",
     "file": "api/client.py", "must_contain": ("BASE_URL",)},
    {"task": "Rename the function `fetch` to `get_path`.",
     "file": "api/client.py", "must_contain": ("def fetch",)},
    {"task": "Add a docstring to fetch describing its path argument.",
     "file": "api/client.py", "must_contain": ("def fetch",)},
    {"task": "Fix the typo in the comment so it reads 'receive'.",
     "file": "api/client.py", "must_contain": ("recieve",)},
    {"task": "Add a log line at DEBUG level before the request is sent in fetch.",
     "file": "api/client.py", "must_contain": ("def fetch",)},

    {"task": "Rename the function `slugify` to `to_slug`.",
     "file": "utils/text.py", "must_contain": ("def slugify",)},
    {"task": "Add a comment in truncate explaining the ellipsis character.",
     "file": "utils/text.py", "must_contain": ("def truncate",)},
    {"task": "Add a docstring to this module describing what it holds.",
     "file": "utils/text.py", "must_contain": ("import re",)},
    {"task": "Format the slugify regex onto its own line for readability.",
     "file": "utils/text.py", "must_contain": ("re.sub",)},

    {"task": "Rename the parameter `path` to `remote_path` in upload.",
     "file": "uploader.py", "must_contain": ("def upload",)},
    {"task": "Add a comment explaining the exponential backoff in the sleep call.",
     "file": "uploader.py", "must_contain": ("time.sleep",)},
    {"task": "Add a log line at INFO when an upload succeeds.",
     "file": "uploader.py", "must_contain": ("client.put",)},

    {"task": "Rename `check_length` to `assert_max_length`.",
     "file": "validators.py", "must_contain": ("def check_length",)},
    {"task": "Fix the typo so the error message reads 'length'.",
     "file": "validators.py", "must_contain": ("lenght",)},
    {"task": "Add a comment noting that VIDEO_ID matches exactly 11 characters.",
     "file": "validators.py", "must_contain": ("VIDEO_ID",)},
    {"task": "Add a log line at DEBUG when check_length rejects a value.",
     "file": "validators.py", "must_contain": ("raise ValueError",)},

    {"task": "Rename the module-level `_store` to `_entries`.",
     "file": "cache.py", "must_contain": ("_store",)},
    {"task": "Add a docstring to clear saying it empties the cache.",
     "file": "cache.py", "must_contain": ("def clear",)},
    {"task": "Add a comment above _store describing what it maps.",
     "file": "cache.py", "must_contain": ("_store = {}",)},

    {"task": "Add a docstring to run_forever describing the handler argument.",
     "file": "worker.py", "must_contain": ("def run_forever",)},
    {"task": "Rename `_jobs` to `_job_queue`.",
     "file": "worker.py", "must_contain": ("_jobs",)},
    {"task": "Add a log line at INFO when a job is picked up.",
     "file": "worker.py", "must_contain": ("_jobs.get()",)},

    {"task": "Rename the parameter `dsn` to `database_url` in connect.",
     "file": "db/pool.py", "must_contain": ("def connect",)},
    {"task": "Add a comment explaining why connect re-raises after logging.",
     "file": "db/pool.py", "must_contain": ("raise",)},
    {"task": "Fix the typo in the log message so it reads 'Failed'.",
     "file": "db/pool.py", "must_contain": ("Faild",)},

    {"task": "Add a docstring to helper2 describing the currency default.",
     "file": "billing/format.py", "must_contain": ("def helper2",)},
    {"task": "Rename the parameter `lines` to `line_items` in total.",
     "file": "billing/format.py", "must_contain": ("def total",)},
    {"task": "Add a comment noting the amount is formatted to two decimals.",
     "file": "billing/format.py", "must_contain": ("amount:,.2f",)},

    {"task": "Rename the test `test_rejects_missing_email` to `test_missing_email_raises`.",
     "file": "tests/test_forms.py", "must_contain": ("def test_rejects_missing_email",)},
    {"task": "Add a comment in test_1 explaining what a successful submit returns.",
     "file": "tests/test_forms.py", "must_contain": ("def test_1",)},
    {"task": "Format the import block so pytest and forms are separated by a blank line.",
     "file": "tests/test_forms.py", "must_contain": ("import pytest",)},

    {"task": "Add a comment above the service block naming the owning team.",
     "file": "config.yaml", "must_contain": ("service:",)},
    {"task": "Rename the `name` key under service from aria to aria-router.",
     "file": "config.yaml", "must_contain": ("name: aria",)},
)

CONTEXT_TEMPLATE = """Here is the current contents of `{path}`:

```
{content}```

{task}"""


def render(entry) -> str:
    """Render a task with its file inline — what a build agent actually sees."""
    return CONTEXT_TEMPLATE.format(path=entry["file"],
                                   content=FIXTURES[entry["file"]],
                                   task=entry["task"])


def unanswerable(tasks=TASKS, fixtures=FIXTURES) -> list:
    """Tasks whose target is missing from the file they name.

    These are unanswerable by construction and would recreate A2b's failure —
    the model cannot edit what it was not shown, so "please provide it" becomes
    the correct answer again and the grader punishes it.
    """
    bad = []
    for e in tasks:
        content = fixtures.get(e["file"])
        if content is None:
            bad.append((e["task"], f"no fixture for {e['file']}"))
            continue
        for token in e["must_contain"]:
            if token not in content:
                bad.append((e["task"], f"{token!r} not in {e['file']}"))
    return bad
