"""iOS round: planted SwiftUI bugs for the perception cascade.

Same philosophy as the web round (bugs.py) — realistic minimal edits that
compile cleanly — but chosen for the regime that motivated the cascade: no DOM,
no console, a minutes-long build loop, and causes that live away from where the
symptom points.

Both bugs are FINGERPRINT-FREE (the retry's whole point): the changed line reads
as correct in isolation, no comment contradicts it, no copy-paste tell, and the
symptom keyword doesn't grep to the cause. Only the RENDER reveals the problem —
so a cheap model must genuinely SEE, not just read. (The first round's i1 was a
token value with a copy-paste tell + stale comment, so it got fixed by reading;
that mechanism is removed here.)

i1  A `.opacity(0.35)` on the empty-state subtitle. The line reads as a plausible
    "soften the hint" styling choice, and the fix is code-findable — but whether
    the result is READABLE is a perceptual judgment. This is the arm-SEPARATOR:
    it probes whether a builder rubber-stamps a present-but-WCAG-failing fix
    (0.35, ~2.3:1) or verifies it back to legible (full, ~6:1). A lenient tier-0
    VLM answering "is the text visible? YES" is exactly what should fail here.

i2  ContentView ZStack order: the theme background moves after the content
    VStack in source order, so SwiftUI paints it on top — the app launches to a
    flat dark screen with nothing visible or tappable. Compiles clean, console
    clean, symptom names no file. COMPREHENSION-gated: you must see the blank
    screen AND understand back-to-front ZStack painting to fix it.

Each spec is a list of (old, new) exact-match edits; inject() refuses to run
unless every anchor matches exactly once.
"""
from __future__ import annotations

from pathlib import Path

IOS_BUGS = {
    "i1": {
        "edits": [(
            "Views/Favorites/FavoritesView.swift",
            "                    .foregroundColor(tokens.textSecondary)\n"
            "                    .multilineTextAlignment(.center)",
            "                    .foregroundColor(tokens.textSecondary)\n"
            "                    .opacity(0.35)\n"
            "                    .multilineTextAlignment(.center)",
        )],
        "symptom": ("On the Favorites screen when it's empty, the subtitle line "
                    "under the big 'No Favorites Yet' heading is really hard to "
                    "read — it's so faint against the dark background it looks "
                    "almost invisible. The heading itself is crisp. No crash."),
        "commit": "polish: soften empty-state subtitle",
    },
    "i2": {
        "edits": [
            (
                "Views/Root/ContentView.swift",
                "        ZStack {\n            themeManager.background\n                .ignoresSafeArea()\n\n            VStack(spacing: 0) {",
                "        ZStack {\n            VStack(spacing: 0) {",
            ),
            (
                "Views/Root/ContentView.swift",
                "                customTabBar\n            }\n",
                "                customTabBar\n            }\n\n            themeManager.background\n                .ignoresSafeArea()\n",
            ),
        ],
        "symptom": ("The app launches to a completely flat dark screen — no tab "
                    "bar, no content, nothing tappable. It doesn't crash; it just "
                    "shows nothing. Started after a small refactor commit."),
        "commit": "refactor: group scene layers",
    },
}


def inject_ios(bug: str, repo: Path) -> None:
    for file, old, new in IOS_BUGS[bug]["edits"]:
        path = repo / file
        text = path.read_text()
        if text.count(old) != 1:
            raise RuntimeError(f"{bug}: anchor found {text.count(old)}x in {file}")
        path.write_text(text.replace(old, new, 1))
