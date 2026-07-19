"""Pixel grader for the iOS round.

There is no Playwright on iOS, so grading is nearest-baseline classification on
screenshots: during validation we capture a PRISTINE baseline and a PLANTED
baseline per bug; a run passes when its final screenshot is (a) decisively
closer to pristine than to planted in the bug's region of interest, and (b)
within an absolute band of pristine — so "differently broken" can't sneak past
as fixed. The status bar is cropped; the sim clock is pinned to 9:41 anyway.

Usage: grade_ios.py --shot X.png --pristine P.png --planted B.png --bug i1 --out v.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

# Regions of interest as fractions of (w, h): x0, y0, x1, y1.
ROI = {
    # subtitle band + tab-bar strip — where secondary text lives
    "i1": [(0.05, 0.52, 0.95, 0.60), (0.0, 0.90, 1.0, 1.0)],
    # whole screen minus status bar — blank-screen bug
    "i2": [(0.0, 0.06, 1.0, 1.0)],
}
STATUS_BAR_FRAC = 0.055


def load_gray(path: Path) -> Image.Image:
    img = Image.open(path).convert("L")
    w, h = img.size
    return img.crop((0, int(h * STATUS_BAR_FRAC), w, h))


def rms_diff(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        b = b.resize(a.size)
    pa, pb = a.tobytes(), b.tobytes()
    n = len(pa)
    return (sum((pa[i] - pb[i]) ** 2 for i in range(0, n, 7)) / (n / 7)) ** 0.5


def crop_roi(img: Image.Image, frac) -> Image.Image:
    w, h = img.size
    return img.crop((int(frac[0] * w), int(frac[1] * h), int(frac[2] * w), int(frac[3] * h)))


def grade(shot: Path, pristine: Path, planted: Path, bug: str) -> dict:
    s, p, b = load_gray(shot), load_gray(pristine), load_gray(planted)
    checks = {"regions": []}
    all_pass = True
    for i, frac in enumerate(ROI[bug]):
        sr, pr, br = crop_roi(s, frac), crop_roi(p, frac), crop_roi(b, frac)
        d_pristine, d_planted = rms_diff(sr, pr), rms_diff(sr, br)
        # margin: decisively closer to pristine; band: not "differently broken"
        region_pass = d_pristine < d_planted * 0.6 and d_pristine < 18.0
        checks["regions"].append({
            "roi": frac, "rms_vs_pristine": round(d_pristine, 2),
            "rms_vs_planted": round(d_planted, 2), "pass": region_pass,
        })
        all_pass = all_pass and region_pass
    checks["pass"] = all_pass
    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", required=True)
    ap.add_argument("--pristine", required=True)
    ap.add_argument("--planted", required=True)
    ap.add_argument("--bug", required=True, choices=sorted(ROI))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    verdict = {"bug": a.bug, **grade(Path(a.shot), Path(a.pristine), Path(a.planted), a.bug)}
    Path(a.out).write_text(json.dumps(verdict, indent=2))
    print(json.dumps({"bug": a.bug, "pass": verdict["pass"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
