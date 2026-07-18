"""Convert the distilled ``{task, tier}`` dataset into the chat-format
``train.jsonl`` / ``valid.jsonl`` that ``mlx_lm.lora`` consumes.

The user turn carries the production ``_CLASSIFY_PROMPT`` (same rubric the 9B
classifier and the minimax-m3 teacher use), the assistant turn is the single
tier word. Training on this teaches the small model to emit the tier for the
exact prompt shape ``evals/classify_finetuned.build_prompt`` presents at eval
time — so train and eval prompts match. mlx_lm applies the model's chat
template itself, so we store RAW message content here (no manual templating).

Split is deterministic and stratified by tier so every tier is represented in
the validation set even at small n. Stdlib only.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import _CLASSIFY_PROMPT, _TIERS  # noqa: E402  (shared rubric + tier vocab)

_TASK_CHAR_CAP = 2000


def to_message_example(row: dict) -> dict:
    """One distilled row -> one mlx-lm chat example. Raises ValueError on a tier
    outside the router's vocabulary (a teacher glitch we refuse to train on)."""
    tier = row.get("tier")
    if tier not in _TIERS:
        raise ValueError(f"invalid tier {tier!r} (expected one of {_TIERS})")
    user = _CLASSIFY_PROMPT.format(task=str(row.get("task", ""))[:_TASK_CHAR_CAP])
    return {"messages": [
        {"role": "user", "content": user},
        {"role": "assistant", "content": tier},
    ]}


def split_stratified(rows: list, valid_frac: float = 0.15, seed: int = 0):
    """Deterministic, tier-stratified train/valid split. Within each tier, a
    fixed round-robin (seeded rotation) picks the validation rows, so every tier
    contributes to valid and repeated calls are identical."""
    by_tier: dict = defaultdict(list)
    for r in rows:
        by_tier[r.get("tier")].append(r)
    train, valid = [], []
    for tier in sorted(by_tier):
        items = list(by_tier[tier])
        n_val = int(round(len(items) * valid_frac))
        rot = seed % len(items) if items else 0
        items = items[rot:] + items[:rot]  # deterministic rotation, no RNG
        valid.extend(items[:n_val])
        train.extend(items[n_val:])
    return train, valid


def _write(path: Path, rows: list) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(to_message_example(r)) + "\n")
            n += 1
    return n


def convert(src: Path, out_dir: Path, valid_frac: float = 0.15, seed: int = 0) -> dict:
    src, out_dir = Path(src), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    with src.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("tier") in _TIERS and r.get("task"):
                rows.append(r)
    train, valid = split_stratified(rows, valid_frac=valid_frac, seed=seed)
    return {
        "train": _write(out_dir / "train.jsonl", train),
        "valid": _write(out_dir / "valid.jsonl", valid),
        "out": str(out_dir),
    }


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Distilled {task,tier} -> mlx-lm chat data")
    p.add_argument("--src", default="evals/datasets/distilled/train.jsonl")
    p.add_argument("--out", default="evals/datasets/distilled/mlx", help="dir to hold train.jsonl+valid.jsonl")
    p.add_argument("--valid-frac", type=float, default=0.15)
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args(argv)
    print(json.dumps(convert(Path(a.src), Path(a.out), a.valid_frac, a.seed), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
