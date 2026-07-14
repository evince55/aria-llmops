# Flywheel intake — spec

**Date:** 2026-07-14 · **Scope:** data intake for the tier-classifier fine-tuning loop
**Methodology:** an explicit solo-dev-scale replication of the LLM→SLM conversion
algorithm from NVIDIA's SLM-agents paper (arXiv 2506.02153 §6).

## Mapping onto the paper's algorithm

| Paper step | Component here | Status |
|---|---|---|
| S1 secure usage data collection | `telemetry/hooks/claude_code_prompt_route.py` (UserPromptSubmit shadow-routing, session-id stamped) + SessionEnd usage ingest | **live** |
| S2 curation & filtering | `telemetry/flywheel.py export_pairs`: dedup, noise filters (in the hook), **eval-set quarantine** | **this PR** |
| S3 task clustering | `evals/task_clusters.py`: stdlib TF-IDF + deterministic agglomerative; per-cluster spend | **this PR** |
| S4 SLM selection | roster research + capability probes (R1) | done |
| S5 specialized fine-tuning | mlx-lm LoRA/DoRA on E2B/4B; **teacher distillation** to bridge the paper's 10k–100k example band | next |
| S6 iteration & refinement | eval-gated promotion: swap `CLASSIFIER_MODEL` only when routing-quality eval ≥ incumbent | next |

## Non-negotiables

1. **Quarantine**: the 42 labeled tasks in `evals/datasets/` are the held-out
   instrument. `export_pairs` drops them (or marks `quarantined` with
   `--include-quarantined`); they must never enter training data.
2. **Outcome honesty**: pairs carry `outcome ∈ {success, failure, None}`.
   None-outcome pairs are kept and marked — usable as distillation inputs
   (teacher labels them under S5), never assumed successful.
3. **Privacy**: pairs contain prompt text → output (`telemetry/flywheel_pairs.jsonl`)
   is gitignored, like the ledger.
4. **Stdlib runtime**: clustering is TF-IDF/cosine (deterministic, CI-hermetic).
   An oMLX `/v1/embeddings` vectorizer may slot in behind the same interface
   later; it must remain optional.

## Data-volume plan (the honest gap)

The paper's fine-tuning rule of thumb is **10k–100k examples**; organic intake
is tens per week. Bridge per S5: cloud-teacher distillation seeded by harvested
real tasks (paraphrase + variation generation), with harvested-vs-synthetic
provenance tagged so evals can report both. Target for first training run:
≥5k pairs, ≥25% harvested-or-derived-from-harvested.

## CLI

```bash
python3 telemetry.py flywheel export [--out PATH] [--include-quarantined]
python3 telemetry.py flywheel clusters
```
