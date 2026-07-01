"""Generate a self-contained static HTML dashboard from the telemetry ledger.

No external CDN, no server: data is embedded and charts are inline SVG. Open the
output file directly in a browser."""
from __future__ import annotations

import html as _html
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry import schema  # noqa: E402
from evals.routing_efficiency_eval import evaluate as efficiency  # noqa: E402


def _bar_svg(pairs, width=520, bar_h=22, gap=8):
    """pairs: list of (label, value). Returns an inline SVG bar chart."""
    if not pairs:
        return "<p>(no data)</p>"
    maxv = max(v for _, v in pairs) or 1
    rows = []
    y = 0
    for label, v in pairs:
        w = int((v / maxv) * (width - 160))
        rows.append(
            f'<g transform="translate(0,{y})">'
            f'<text x="0" y="15" font-size="12" fill="#ccc">{_html.escape(str(label))[:22]}</text>'
            f'<rect x="150" y="3" width="{w}" height="{bar_h-6}" fill="#4f9da6"/>'
            f'<text x="{150+w+5}" y="15" font-size="11" fill="#888">{v}</text>'
            f'</g>'
        )
        y += bar_h + gap
    return f'<svg width="{width}" height="{y}">{"".join(rows)}</svg>'


def build_html(events: list, classification: Optional[dict] = None) -> str:
    usage = [e for e in events if e.get("event") == "usage"]
    total_imputed = round(sum(float(e.get("imputed_usd", 0) or 0) for e in usage), 4)
    total_actual = round(sum(float(e.get("actual_usd", 0) or 0) for e in usage), 4)
    by_model = defaultdict(float)
    for e in usage:
        by_model[e.get("model", "unknown")] += float(e.get("imputed_usd", 0) or 0)
    model_pairs = sorted(((m, round(v, 4)) for m, v in by_model.items()), key=lambda x: -x[1])

    eff = efficiency(events)
    tier_pairs = sorted(eff["by_complexity"].items(), key=lambda x: -x[1])

    cls_block = ""
    if classification:
        cls_block = (
            f"<h2>Router classification accuracy</h2>"
            f"<p class='big'>{classification['accuracy']*100:.0f}%</p>"
            f"<pre>{_html.escape(json.dumps(classification['per_tier'], indent=2))}</pre>"
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Aria LLMOps Dashboard</title>
<style>
 body{{font-family:-apple-system,system-ui,sans-serif;background:#16181d;color:#e6e6e6;margin:2rem;}}
 h1{{font-weight:600}} h2{{color:#9ad;margin-top:2rem}}
 .cards{{display:flex;gap:1rem;flex-wrap:wrap}}
 .card{{background:#1f232b;border-radius:10px;padding:1rem 1.4rem;min-width:160px}}
 .big{{font-size:2rem;font-weight:700;margin:.2rem 0}}
 .sub{{color:#8a93a3;font-size:.8rem}}
 pre{{background:#1f232b;padding:1rem;border-radius:8px;overflow:auto;font-size:12px}}
</style></head><body>
<h1>Aria LLMOps Dashboard</h1>
<p class="sub">{len(usage)} usage events · generated from telemetry/events.jsonl</p>
<div class="cards">
  <div class="card"><div class="sub">Imputed cost (list rates)</div><div class="big">${total_imputed}</div></div>
  <div class="card"><div class="sub">Actual spend</div><div class="big">${total_actual}</div></div>
  <div class="card"><div class="sub">Sessions in local-first tiers (config, not quality)</div><div class="big">{eff['local_first_sessions_pct']}%</div></div>
  <div class="card"><div class="sub">Sessions analyzed</div><div class="big">{eff['n_sessions']}</div></div>
</div>
<h2>Imputed cost by model</h2>
{_bar_svg(model_pairs)}
<h2>Tasks by predicted complexity</h2>
{_bar_svg(tier_pairs)}
{cls_block}
</body></html>"""


def generate(ledger=None, out=None) -> Path:
    events = schema.read_events(ledger=ledger) if ledger else schema.read_events()
    classification = None
    try:
        from evals.router_classification_eval import load_dataset, evaluate as cls_eval
        ds_path = Path(__file__).resolve().parents[1] / "evals" / "datasets" / "labeled_tasks.jsonl"
        if ds_path.exists():
            classification = cls_eval(load_dataset(ds_path))
    except Exception:
        pass
    out = Path(out) if out else Path(__file__).parent / "index.html"
    out.write_text(build_html(events, classification), encoding="utf-8")
    return out


def main() -> int:
    p = generate()
    print(json.dumps({"written": str(p)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
