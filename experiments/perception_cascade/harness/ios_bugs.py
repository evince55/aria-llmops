"""iOS round: planted SwiftUI bugs for the perception cascade.

Same philosophy as the web round (bugs.py) — realistic minimal edits that
compile cleanly — but chosen for the regime that motivated the cascade: no DOM,
no console, a minutes-long build loop, and causes that live away from where the
symptom points.

i1  DesignTokens.textSecondary dark value 0.62 -> 0.12: reads as a copy-paste
    of the adjacent cardSurface value (0.12); the doc comment above still says
    0.62. Ghosts ALL secondary text app-wide (tab labels, empty-state subtitle,
    row subtitles) at ~1.5:1 while titles and accents stay crisp.

i2  ContentView ZStack order: the theme background moves after the content
    VStack in source order, so SwiftUI paints it on top — the app launches to a
    flat dark screen with nothing visible or tappable. Compiles clean, console
    clean, and the symptom names no file.

Each spec is a list of (old, new) exact-match edits; inject() refuses to run
unless every anchor matches exactly once.
"""
from __future__ import annotations

from pathlib import Path

IOS_BUGS = {
    "i1": {
        "edits": [(
            "Managers/ThemeManager.swift",
            "        isDark ? Color(white: 0.62) : Color(white: 0.40)",
            "        isDark ? Color(white: 0.12) : Color(white: 0.40)",
        )],
        "symptom": ("In dark mode almost all the smaller gray text in the app has "
                    "become unreadable — the hint line under 'No Favorites Yet', "
                    "the unselected tab names at the bottom, song subtitle lines: "
                    "they're all so dim they look missing. Headings and the blue "
                    "accent still look fine. No crash, nothing in the logs."),
        "commit": "polish: align token values",
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
