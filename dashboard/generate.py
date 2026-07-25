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
from telemetry import schema
from telemetry.token_split import (  # noqa: E402
    by_model as split_by_model, per_session_output, split_totals)
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

    # A1 — the input/output split. Output tokens cost 3-4x input on every priced
    # cloud model, so cost-by-model alone cannot tell you whether verbosity is
    # what you are paying for. BOTH ratio definitions are shown and labelled:
    # under prompt caching they differ by ~300x on this repo's own events
    # (0.882 excluding cache, 0.003 including it), so an unlabelled single
    # number would be worse than none.
    split = split_totals(events)
    per_sess = per_session_output(events)
    split_rows = "".join(
        f"<tr><td>{_html.escape(r['model'])}</td><td>{r['input_tokens']:,}</td>"
        f"<td>{r['output_tokens']:,}</td><td>{r['cache_read_tokens']:,}</td>"
        f"<td>{r['output_ratio_excl_cache']:.3f}</td>"
        f"<td>{r['output_ratio_incl_cache']:.3f}</td><td>{r['n_events']:,}</td></tr>"
        for r in split_by_model(events)[:12])
    split_block = f"""
<h2>Token split (A1)</h2>
<div class="cards">
  <div class="card"><div class="sub">Input tokens</div><div class="big">{split['input_tokens']:,}</div></div>
  <div class="card"><div class="sub">Output tokens</div><div class="big">{split['output_tokens']:,}</div></div>
  <div class="card"><div class="sub">Cached input (read)</div><div class="big">{split['cache_read_tokens']:,}</div></div>
  <div class="card"><div class="sub">Output ratio <b>excluding</b> cache</div><div class="big">{split['output_ratio_excl_cache']:.3f}</div></div>
  <div class="card"><div class="sub">Output ratio <b>including</b> cache</div><div class="big">{split['output_ratio_incl_cache']:.3f}</div></div>
</div>
<p class="sub">Two ratios, never one: cached input is billed separately and tracks prompt
reuse, not verbosity. The cache-<b>exclusive</b> figure is the one a brevity change moves.</p>
<h2>Output tokens per session</h2>
<div class="cards">
  <div class="card"><div class="sub">Median per session</div><div class="big">{per_sess['median']:,.0f}</div></div>
  <div class="card"><div class="sub">Mean per session</div><div class="big">{per_sess['mean']:,.0f}</div></div>
  <div class="card"><div class="sub">Heaviest session</div><div class="big">{per_sess['max']:,}</div></div>
  <div class="card"><div class="sub">Sessions</div><div class="big">{per_sess['n_sessions']:,}</div></div>
</div>
<p class="sub">Median alongside mean because one runaway agentic session otherwise sets the headline.</p>
<h2>Token split by model</h2>
<table><tr><th>model</th><th>input</th><th>output</th><th>cache read</th>
<th>out ratio (excl cache)</th><th>out ratio (incl cache)</th><th>events</th></tr>
{split_rows}</table>"""

    cls_block = ""
    if classification:
        # `classification` maps a dataset label -> eval result. The keyword-
        # tuned set is the TUNING TARGET (the keywords were written against it,
        # so its accuracy is self-fulfilling); the prose-blind set is the
        # honest out-of-distribution number. Headline the honest one — a
        # dashboard that leads with the tuning-set score is marketing.
        parts = ["<h2>Router classification accuracy (keyword classifier)</h2>"]
        blind = classification.get("prose_blind")
        tuned = classification.get("keyword_tuned")
        if blind:
            parts.append(
                f"<p class='big'>{blind['accuracy']*100:.0f}%"
                f" <span class='sub'>on keyword-blind prose (n={blind['n']}) — the honest number</span></p>")
        if tuned:
            parts.append(
                f"<p class='sub'>{tuned['accuracy']*100:.0f}% on the keyword-tuned seed set "
                f"(n={tuned['n']}) — the tuning target, self-fulfilling by construction; "
                f"shown for drift detection only.</p>")
            parts.append(f"<pre>{_html.escape(json.dumps(tuned['per_tier'], indent=2))}</pre>")
        cls_block = "".join(parts)

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
 table{{border-collapse:collapse;font-size:13px;margin-top:.6rem}}
 th,td{{text-align:right;padding:.3rem .7rem;border-bottom:1px solid #2b3038}}
 th{{color:#8a93a3;font-weight:500}} td:first-child,th:first-child{{text-align:left}}
</style></head><body>
<h1>Aria LLMOps Dashboard</h1>
<p class="sub">{len(usage)} usage events · generated from telemetry/events.jsonl</p>
<div class="cards">
  <div class="card"><div class="sub">Imputed cost (list rates)</div><div class="big">${total_imputed}</div></div>
  <div class="card"><div class="sub">Actual spend</div><div class="big">${total_actual}</div></div>
  <div class="card"><div class="sub">Sessions in local-first tiers (config, not quality)</div><div class="big">{eff['local_first_sessions_pct']}%</div></div>
  <div class="card"><div class="sub">Sessions analyzed</div><div class="big">{eff['n_sessions']}</div></div>
</div>
{split_block}
<h2>Imputed cost by model</h2>
{_bar_svg(model_pairs)}
<h2>Tasks by predicted complexity</h2>
{_bar_svg(tier_pairs)}
{cls_block}
</body></html>"""


def generate(ledger=None, out=None) -> Path:
    events = schema.read_events(ledger=ledger) if ledger else schema.read_events()
    # Offline keyword-classifier accuracy on BOTH datasets. No model calls —
    # dashboard generation must work with nothing but the repo on disk.
    classification = None
    try:
        from evals.router_classification_eval import load_dataset, evaluate as cls_eval
        ds_dir = Path(__file__).resolve().parents[1] / "evals" / "datasets"
        classification = {}
        for label, fname in (("keyword_tuned", "labeled_tasks.jsonl"),
                             ("prose_blind", "labeled_tasks_prose.jsonl")):
            ds_path = ds_dir / fname
            if ds_path.exists():
                classification[label] = cls_eval(load_dataset(ds_path))
        classification = classification or None
    except Exception:
        classification = None
    out = Path(out) if out else Path(__file__).parent / "index.html"
    out.write_text(build_html(events, classification), encoding="utf-8")
    return out


def main() -> int:
    p = generate()
    print(json.dumps({"written": str(p)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
