"""The three planted bugs: minimal, realistic single-token edits.

Each is the kind of change a tired dev plausibly commits: an adjacent-looking
CSS token, a "spacing tweak" with an inverted sign, an export renamed in a
refactor with a call site missed. Each compiles/parses cleanly — only the
RENDERED page (or its console) reveals the problem, which is what makes
perception load-bearing.

Symptoms are written in user voice (what a bug report would actually say),
never pointing at the file.
"""
from __future__ import annotations

from pathlib import Path

BUGS = {
    "b1": {
        "file": "css/pages/home.css",
        "old": "  color: var(--text-1);\n  max-width: 20ch;",
        "new": "  color: var(--bg-1);\n  max-width: 20ch;",
        "symptom": ("The big tagline under my name on the homepage — the one that "
                    "says what I build — has disappeared. The name shows fine, the "
                    "paragraph below shows fine, but the tagline between them is "
                    "just gone."),
        "commit": "polish: tighten hero type ramp",
    },
    "b2": {
        "file": "css/pages/home.css",
        "old": "  margin: var(--sp-8) 0 0;",
        "new": "  margin: calc(var(--sp-8) * -1) 0 0;",
        "symptom": ("On the homepage the little stat chips (homelab uptime, AZ-900, "
                    "systems in production) are sitting on top of the two buttons — "
                    "they overlap 'Explore the work' and 'Get in touch' and it looks "
                    "broken and half-unclickable."),
        "commit": "polish: pull proof chips closer to the CTAs",
    },
    "b3": {
        "file": "js/stats-source.js",
        "old": "export async function fetchStats(",
        "new": "export async function fetchStatsData(",
        "symptom": ("The live homelab strip on the homepage seems dead — it shows "
                    "the placeholder numbers but never the 'updated X ago' line or "
                    "any freshness note, and I think something is erroring. Can you "
                    "check whether the page has errors and get the live strip "
                    "working again?"),
        "commit": "refactor: clearer stats fetch naming",
    },
}


def inject(bug: str, repo: Path) -> None:
    """Apply one bug to a repo copy. Raises if the anchor text isn't found
    exactly once — a changed upstream file must fail loudly, not plant a
    different experiment than designed."""
    spec = BUGS[bug]
    path = repo / spec["file"]
    text = path.read_text()
    if text.count(spec["old"]) != 1:
        raise RuntimeError(f"{bug}: anchor found {text.count(spec['old'])}x in {spec['file']}")
    path.write_text(text.replace(spec["old"], spec["new"], 1))
