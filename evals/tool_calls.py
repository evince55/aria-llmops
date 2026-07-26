"""S10 — the agentic subtask: emit a correct tool call.

WHY THIS SUBTASK, AND NOT THE OBVIOUS ONE. `docs/REPRODUCTION.md` names the
sharpest divergence from the paper: it converted a single-turn *classifier* while
the paper is about replacing LLM agents in *agentic* subtasks. Its first
"what I'd do differently" is **choose the converted component by leverage**. So
this target was chosen by measurement, not convenience — a headroom probe on the
conversion target (Gemma-4-E2B, 4-bit) under strict programmatic verification:

    code edit  (file in -> edited file out)   1.00   ceiling, nothing to learn
    tool call  (task + schema -> JSON call)   0.70   30 points of headroom

and the tool-call failure decomposes: 9/10 parsed, 9/10 chose the right tool,
**7/10 got the arguments exactly right**. The convertible skill is ARGUMENT
PRECISION, which is narrow and repetitive — precisely the paper's thesis.

NO LLM JUDGE ANYWHERE IN THIS MODULE. Verification is exact structural
comparison against a known-correct call. That removes the entire cloud cost, and
it removes the failure that voided A2b: a judge grading a task its harness could
not perform turned an A/B into a confabulation contest. A deterministic verifier
cannot be flattered.

Ground truth is authored WITH the task, so every example is correct by
construction rather than by a teacher's opinion — the distillation step the paper
uses is not needed here, which is why this round costs nothing to run.
"""
from __future__ import annotations

import json
import re

# The tool surface. Deliberately small and agent-shaped: read, search, test,
# write — the four things a coding agent actually does. Types matter because
# argument precision is the skill under test (a string "true" is not `True`).
TOOLS = {
    "read_file":  {"path": "str"},
    "search":     {"pattern": "str", "glob": "str"},
    "run_tests":  {"target": "str", "verbose": "bool"},
    "write_file": {"path": "str", "content": "str"},
}

SCHEMA_PROMPT = """You have exactly these tools:

  read_file(path: str)                      -> file contents
  search(pattern: str, glob: str)           -> matching lines
  run_tests(target: str, verbose: bool)     -> test results
  write_file(path: str, content: str)       -> writes a file

Reply with ONE tool call as strict JSON and NOTHING else:
{"tool": "<name>", "args": {...}}"""


def build_prompt(task: str) -> str:
    return f"{SCHEMA_PROMPT}\n\nTask: {task}"


# --------------------------------------------------------------- native arm
# WHY A SECOND DELIVERY MECHANISM EXISTS. The first Ornith-9B measurement asked
# a TOOL-TUNED model for freeform JSON in prose and read 0.69 tool accuracy —
# a number that indicts the prompt, not the model. A tool-tuned model expects
# the OpenAI `tools` parameter, where the call is emitted through a constrained
# decoding path rather than as prose. Comparing a converted model against a
# selected one is only meaningful if the selected one is measured at its best.
#
# The surface must be IDENTICAL in content — same tools, same argument names,
# same types, all required — so the only variable is the delivery mechanism.
# Tests assert that equivalence against `TOOLS` rather than trusting this
# literal, because a schema that quietly drops `verbose` would hand the native
# arm an easier task and the comparison would be rigged in its favour.
_JSON_TYPES = {"str": "string", "bool": "boolean"}

_DESCRIPTIONS = {
    "read_file": "Read a file and return its contents",
    "search": "Search files matching a glob for a pattern",
    "run_tests": "Run a named test suite",
    "write_file": "Write content to a file",
}


def native_tool_schema() -> list:
    """`TOOLS` expressed as OpenAI function definitions. Derived, never hand-kept."""
    out = []
    for name, args in TOOLS.items():
        out.append({"type": "function", "function": {
            "name": name,
            "description": _DESCRIPTIONS[name],
            "parameters": {
                "type": "object",
                "properties": {a: {"type": _JSON_TYPES[t]} for a, t in args.items()},
                # Every argument is required. An optional `verbose` would let a
                # model omit the one field the prose arm is measured on.
                "required": list(args),
            },
        }})
    return out


def parse_native_call(message):
    """Lift an OpenAI `tool_calls` message into the same shape `parse_call` returns.

    Returning the common shape is the point: both arms then flow through the
    SAME `grade()`, so a difference in score cannot come from a difference in
    grading. Failures stay failures — a reply with no tool call, a call with no
    name, or unparseable arguments all return None rather than being coerced
    into the nearest plausible call.
    """
    calls = (message or {}).get("tool_calls") or []
    if not calls:
        return None
    fn = (calls[0] or {}).get("function") or {}
    name = fn.get("name")
    if not isinstance(name, str) or not name:
        return None
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    if not isinstance(args, dict):
        return None
    return {"tool": name, "args": args}


# Above this share of truncated rows a run is not a measurement of the model.
MAX_TRUNCATION_FOR_SOUND = 0.05

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


_DECODER = json.JSONDecoder()


def _json_objects(text):
    """Yield every JSON object decodable at some `{` in `text`, in order.

    WHY NOT A REGEX. This was `re.compile(r"\\{.*\\}", re.DOTALL)`, which is
    GREEDY: it matches from the first brace in the text to the last, so a model
    that reasons in prose before answering — and echoes the schema `{"tool": ...}`
    while reasoning — produced one un-parseable blob and was scored parse_rate
    0.00 with a perfectly correct call sitting at the end of its reply. That is a
    harness failure that reads exactly like incapacity, and it only bites models
    whose output style differs from the arm the harness was built against.

    WHY NOT BRACE COUNTING EITHER. A depth counter never returns to zero after an
    *unmatched* `{` in the preamble — easy to emit while reasoning about code —
    and then swallows the real answer that follows. `raw_decode` parses a prefix
    and reports where it ended, so a stray brace costs one failed attempt instead
    of the whole reply. It also handles braces inside string literals for free.
    """
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = _DECODER.raw_decode(text, i)
        except ValueError:
            continue
        yield obj


def parse_call(raw):
    """Extract a tool call. Returns None rather than guessing.

    An unparseable reply is a FAILURE, not something to coerce into the nearest
    plausible call — coercion is how a broken model comes to look competent (see
    the degenerate-model artifact, finding 5 of the reproduction write-up).

    The LAST valid call wins: models draft, reconsider, and restate, and the
    final answer is the answer.
    """
    best = None
    for obj in _json_objects(_ANSI.sub("", raw or "")):
        if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
            best = obj
    return best


def grade(raw, expected_tool: str, expected_args: dict, finish_reason=None) -> dict:
    """Deterministic grade of one tool call. No judge, no tolerance, no opinion.

    Reported in three levels because they are different skills and the probe
    showed they fail at different rates:
      parsed      — did it emit usable JSON at all
      tool_ok     — did it pick the right tool  (base model: 9/10)
      args_ok     — are the arguments EXACTLY right (base model: 7/10)  <- the gap

    `finish_reason="length"` marks the row TRUNCATED. A first baseline read 0.55
    with a 35% parse-failure rate and was measuring a 300-token cap: the model
    emits a reasoning preamble, needs ~427 tokens, and hit the cap with an EMPTY
    content field. "Ran out of room" and "could not do it" must never be the same
    number — the fifth appearance of this trap in this project.
    """
    truncated = finish_reason == "length"
    got = parse_call(raw)
    if got is None:
        return {"parsed": False, "tool_ok": False, "args_ok": False, "exact": False,
                "truncated": truncated, "got": None}
    tool_ok = got.get("tool") == expected_tool
    args = got.get("args")
    args_ok = bool(tool_ok) and args == expected_args
    return {"parsed": True, "tool_ok": tool_ok, "args_ok": args_ok,
            "exact": bool(tool_ok and args_ok), "truncated": truncated, "got": got}


def score(rows) -> dict:
    """Aggregate grades. `rows` are grade() outputs."""
    n = len(rows) or 1
    trunc = sum(r.get("truncated", False) for r in rows) / n
    return {
        "n": len(rows),
        "parse_rate": sum(r["parsed"] for r in rows) / n,
        "tool_accuracy": sum(r["tool_ok"] for r in rows) / n,
        "strict_accuracy": sum(r["exact"] for r in rows) / n,
        "truncation_rate": trunc,
        # A run measured through a token cap is not a measurement. Raise the cap
        # and re-run rather than reporting the number.
        "sound": trunc <= MAX_TRUNCATION_FOR_SOUND,
    }


def validate(tasks=None) -> list:
    """Problems that would make a task ungradable — checked before any run.

    A2b was voided because its tasks were unanswerable by the harness and nobody
    checked first. The analogue here: an expected call that does not match the
    declared tool surface can never be produced correctly, so the model is being
    graded against something it was never shown.
    """
    problems = []
    for t in (tasks if tasks is not None else TASKS):
        tool, args = t["tool"], t["args"]
        if tool not in TOOLS:
            problems.append((t["task"], f"unknown tool {tool!r}"))
            continue
        want = TOOLS[tool]
        if set(args) != set(want):
            problems.append((t["task"], f"args {sorted(args)} != schema {sorted(want)}"))
            continue
        for k, typ in want.items():
            if typ == "bool" and not isinstance(args[k], bool):
                problems.append((t["task"], f"{k} must be a bool, got {type(args[k]).__name__}"))
            if typ == "str" and not isinstance(args[k], str):
                problems.append((t["task"], f"{k} must be a str, got {type(args[k]).__name__}"))
    return problems


def _t(task, tool, **args):
    return {"task": task, "tool": tool, "args": args}


# Held-out evaluation set. Ground truth is authored with the task, so it is
# correct by construction. Kept separate from anything used to build training
# data — the quarantine rule this project has now been bitten by twice.
TASKS = (
    _t("Show me what's in src/auth.py.", "read_file", path="src/auth.py"),
    _t("What does tests/conftest.py contain?", "read_file", path="tests/conftest.py"),
    _t("Open the file at conf/settings.yaml.", "read_file", path="conf/settings.yaml"),
    _t("Read README.md.", "read_file", path="README.md"),
    _t("Display the contents of src/db/pool.py.", "read_file", path="src/db/pool.py"),
    _t("Find every place TODO appears in the python files.", "search", pattern="TODO", glob="*.py"),
    _t("Search for DEPRECATED across markdown files.", "search", pattern="DEPRECATED", glob="*.md"),
    _t("Grep for 'import requests' in .py files.", "search", pattern="import requests", glob="*.py"),
    _t("Look for the string API_KEY in yaml files.", "search", pattern="API_KEY", glob="*.yaml"),
    _t("Where does 'legacy' show up in the js files?", "search", pattern="legacy", glob="*.js"),
    _t("Run the billing tests with verbose output.", "run_tests", target="billing", verbose=True),
    _t("Run the auth tests, quietly.", "run_tests", target="auth", verbose=False),
    _t("Execute the parser test suite verbosely.", "run_tests", target="parser", verbose=True),
    _t("Just run the api tests, no extra output.", "run_tests", target="api", verbose=False),
    _t("Run the worker tests and show me everything.", "run_tests", target="worker", verbose=True),
    _t("Save the text hello to notes/tmp.txt.", "write_file", path="notes/tmp.txt", content="hello"),
    _t("Write 'ok' into status.txt.", "write_file", path="status.txt", content="ok"),
    _t("Create docs/note.md containing 'draft'.", "write_file", path="docs/note.md", content="draft"),
    _t("Put the word done in out/result.txt.", "write_file", path="out/result.txt", content="done"),
    _t("Store 'v2' in version.txt.", "write_file", path="version.txt", content="v2"),
)

# ADVERSARIAL SET. `TASKS` shares surface shapes with the standard eval, so a
# 1.00 on it cannot be told from a 0.95. These use phrasings, file extensions
# and multi-word contents absent from BOTH the training templates and `TASKS`,
# and they are where the arms actually separate. Committed rather than kept in
# a scratch file, because an instrument nobody can re-run is not an instrument.
HARD_TASKS = (
    _t("Peek inside vendor/lib.rs.", "read_file", path="vendor/lib.rs"),
    _t("I'd like to inspect .github/workflows/ci.yml please.", "read_file",
       path=".github/workflows/ci.yml"),
    _t("Cat out bin/run", "read_file", path="bin/run"),
    _t("Any occurrences of TODO(bug) in the shell files?", "search",
       pattern="TODO(bug)", glob="*.sh"),
    _t("Comb the sql files looking for DROP TABLE.", "search",
       pattern="DROP TABLE", glob="*.sql"),
    _t("Do the css files contain !important anywhere?", "search",
       pattern="!important", glob="*.css"),
    _t("Please execute the checkout suite; I want to see all of it.", "run_tests",
       target="checkout", verbose=True),
    _t("Do a hushed run of the ledger tests.", "run_tests", target="ledger", verbose=False),
    _t("Give the telemetry tests a full-chatter run.", "run_tests",
       target="telemetry", verbose=True),
    _t("Run inventory tests. Spare me the output.", "run_tests",
       target="inventory", verbose=False),
    _t("Stash the phrase hello world into tmp/greeting.txt.", "write_file",
       path="tmp/greeting.txt", content="hello world"),
    _t("Jot down not ready in state/flag.txt.", "write_file",
       path="state/flag.txt", content="not ready"),
    _t("Emit release candidate 4 to build/tag.txt.", "write_file",
       path="build/tag.txt", content="release candidate 4"),
)

# ---------------------------------------------------------------- FRESH slice
# Authored against the six STRUCTURAL axes fixed in
# docs/research/2026-07-26-wide-set-preregistration.md, before any task here was
# written. The axes are properties of the tool surface — path shape, verb
# distance, boolean-by-idiom, content shape, sentence form, distractor mention —
# NOT the failure modes I had already observed. Vocabulary is disjoint from the
# training generator and from both existing eval sets, so nothing here can be
# answered from a memorised filename.
#
# Patterns deliberately contain no regex metacharacters and globs use one
# consistent style: two of the n=13 set's contested rows are arguments about an
# underspecified schema rather than model errors, and multiplying that dispute
# by four would make the wider set worse, not better.
FRESH_TASKS = (
    # --- read_file: path shape, verb distance, sentence form, distractor
    _t("Pull up internal/cache/redis_client.go.", "read_file", path="internal/cache/redis_client.go"),
    _t("What's inside .env.example?", "read_file", path=".env.example"),
    _t("I need eyes on Dockerfile.", "read_file", path="Dockerfile"),
    _t("Print out migrations/0007_add_index.sql.", "read_file", path="migrations/0007_add_index.sql"),
    _t("Before running anything, just read ops/deploy-notes.txt.", "read_file", path="ops/deploy-notes.txt"),
    _t("Crack open packages/ui-kit/index.tsx.", "read_file", path="packages/ui-kit/index.tsx"),
    _t("Can you pull k8s/base/kustomization.yaml up for me?", "read_file", path="k8s/base/kustomization.yaml"),
    _t("Take a look at CHANGELOG.", "read_file", path="CHANGELOG"),
    _t("Skip searching for it, just open lib/geo/haversine.rb.", "read_file", path="lib/geo/haversine.rb"),
    _t("Let me see .vscode/settings.json.", "read_file", path=".vscode/settings.json"),
    _t("Cat cmd/server/main_test.go.", "read_file", path="cmd/server/main_test.go"),
    _t("Throw internal/auth/jwt.go on screen.", "read_file", path="internal/auth/jwt.go"),

    # --- search: fresh patterns and extensions, no regex metacharacters
    _t("Which go files mention nolint?", "search", pattern="nolint", glob="*.go"),
    _t("Scan the rust files for unsafe.", "search", pattern="unsafe", glob="*.rs"),
    _t("Anywhere in the tsx files does useLayoutEffect turn up?", "search", pattern="useLayoutEffect", glob="*.tsx"),
    _t("Dig through the toml files for edition.", "search", pattern="edition", glob="*.toml"),
    _t("Do a pass over the ruby files for rescue nil.", "search", pattern="rescue nil", glob="*.rb"),
    _t("Point me at admin_token in the ini files.", "search", pattern="admin_token", glob="*.ini"),
    _t("Trawl the java files for printStackTrace.", "search", pattern="printStackTrace", glob="*.java"),
    _t("Are there any BEGIN TRANSACTION lines in the php files?", "search", pattern="BEGIN TRANSACTION", glob="*.php"),
    _t("Don't open anything yet, just find retry_count across the go files.", "search", pattern="retry_count", glob="*.go"),
    _t("Chase down os.Exit in the go files.", "search", pattern="os.Exit", glob="*.go"),
    _t("I want every hit for HACK in the rust files.", "search", pattern="HACK", glob="*.rs"),
    _t("Rake the toml files for workspace.", "search", pattern="workspace", glob="*.toml"),

    # --- run_tests: verbosity by idiom, fresh targets
    _t("Put the pricing suite through with the full firehose.", "run_tests", target="pricing", verbose=True),
    _t("Exercise the inbox tests, minimal noise.", "run_tests", target="inbox", verbose=False),
    _t("Roll the roster tests and show me every line.", "run_tests", target="roster", verbose=True),
    _t("Give the dispatch tests a go, just the result.", "run_tests", target="dispatch", verbose=False),
    _t("Chatty mode on the tenancy tests, please.", "run_tests", target="tenancy", verbose=True),
    _t("Hush the audit tests.", "run_tests", target="audit", verbose=False),
    _t("Take the geocoder tests for a spin; I want the whole log.", "run_tests", target="geocoder", verbose=True),
    _t("Work through the sync tests, keep it to a summary.", "run_tests", target="sync", verbose=False),
    _t("Don't read the config first, just exercise the archive tests loudly.", "run_tests", target="archive", verbose=True),
    _t("Push the quota tests through as quietly as you can.", "run_tests", target="quota", verbose=False),
    _t("Set the throttle tests going and don't spare the detail.", "run_tests", target="throttle", verbose=True),
    _t("Cycle the replay tests without the chatter.", "run_tests", target="replay", verbose=False),

    # --- write_file: multi-word, digit-bearing, mixed-case contents
    _t("Park build 4172 in tmp/lock.pid.", "write_file", path="tmp/lock.pid", content="build 4172"),
    _t("Note OK to deploy in var/run/state.txt.", "write_file", path="var/run/state.txt", content="OK to deploy"),
    _t("Lay down phase two into reports/stage.txt.", "write_file", path="reports/stage.txt", content="phase two"),
    _t("File rc-9 under .cache/build-id.", "write_file", path=".cache/build-id", content="rc-9"),
    _t("Pin Needs Review to out/verdict.txt.", "write_file", path="out/verdict.txt", content="Needs Review"),
    _t("Bank the value hold for QA at qa/gate.txt.", "write_file", path="qa/gate.txt", content="hold for QA"),
    _t("Etch 2026-08-01 into release/date.txt.", "write_file", path="release/date.txt", content="2026-08-01"),
    _t("Deposit n/a in meta/owner.txt.", "write_file", path="meta/owner.txt", content="n/a"),
    _t("Without running any tests, put green in ci/last.txt.", "write_file", path="ci/last.txt", content="green"),
    _t("Lodge Rollback Candidate in ops/flag.txt.", "write_file", path="ops/flag.txt", content="Rollback Candidate"),
    _t("Scribble batch 88 onto jobs/cursor.txt.", "write_file", path="jobs/cursor.txt", content="batch 88"),
    _t("Enter DO NOT MERGE at review/status.txt.", "write_file", path="review/status.txt", content="DO NOT MERGE"),
)

# ------------------------------------------------------------ REGRESSION slice
# Deliberately aimed at failure modes already OBSERVED on the n=13 set. These
# are fitted to the failure table by construction, so they are a debugging
# instrument and nothing else: they may never be pooled into a generalisation
# claim, and are reported as their own number. Kept separate rather than
# discarded, because a regression suite for known defects is genuinely useful.
REGRESSION_TASKS = (
    _t("Cat out bin/launch", "read_file", path="bin/launch"),
    _t("Dump scripts/entry", "read_file", path="scripts/entry"),
    _t("Do the ini files contain nodelay anywhere?", "search", pattern="nodelay", glob="*.ini"),
    _t("Do the go files contain gofmt anywhere?", "search", pattern="gofmt", glob="*.go"),
    _t("Emit second attempt to build/note.txt.", "write_file", path="build/note.txt", content="second attempt"),
    _t("Emit shipped to build/state.txt.", "write_file", path="build/state.txt", content="shipped"),
    _t("Give the queue tests a full-detail run.", "run_tests", target="queue", verbose=True),
    _t("Give the cache tests a full-detail run.", "run_tests", target="cache", verbose=True),
)

# The instrument used for round-2 generalisation claims: the original n=13 plus
# the fresh slice. REGRESSION is excluded on purpose.
WIDE_TASKS = HARD_TASKS + FRESH_TASKS
