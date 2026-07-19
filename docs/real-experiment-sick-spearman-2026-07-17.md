# Real controlled experiment: SICK Spearman

## Result

A bounded ten-iteration Codex search improved AIRS-lite SICK semantic-similarity Spearman correlation from `0.5757186473` to `0.7457772691` on seed 0 (`+0.1700586218`, normalized gain `0.6111031881`). The registered target of `0.854` was not reached.

The complete final candidate was then reconstructed, without another Codex search, in fresh seed 1 and seed 2 projects. The three candidate scores were `0.7457772691`, `0.7459789969`, and `0.7454820648`: mean `0.7457461103`, across-seed standard deviation `0.0002040646`. Every trial had zero within-run variance, all code hashes matched, all three audits passed, and Docker isolation was verified.

This is publishable under RF-Bench's local integrity policy. It is not an official AIRS leaderboard submission and does not establish a state-of-the-art result.

## Frozen protocol

- Task ID: `textualsimilaritysickspearmancorrelation`
- Search backend: `codex:gpt-5.4`
- Search seed: `0`
- Runtime: Docker image `rf-airs-cpu:v1`
- Image ID: `sha256:ed0c4ef7a59bc0587c92b69ef994b1873d0fa9eb84c01ec8ce4f8bb5b126812d`
- Capability manifest SHA-256: `a2ad47c62d247ae9aa6c3fc573ec1e5aaf7f14325e5105813f64353d9af67dba`
- Budget: 10 executed candidates, candidate pool 2, at most 4 proposal attempts per iteration, patience 5, invalid-run budget 3
- Required repeats: 2 with tolerance 0
- Network: disabled in both candidate and evaluator containers
- Stop reason: `iteration_budget_exhausted`

## Search trace

| Iteration | Experiment | Score | Decision |
|---:|---|---:|---|
| 1 | Pure token-length ratio (`alpha=0`) | 0.2263695665 | valid, rejected |
| 2 | Supervised word TF-IDF ridge model | 0.7245379616 | promoted |
| 3 | LinearSVR objective | 0.7073364016 | valid, rejected |
| 4 | Add a purported validation split | invalid | retained as negative evidence |
| 5 | Add word TF-IDF cosine | 0.7268659555 | promoted |
| 6 | Word TF-IDF `min_df=1` | 0.7296898973 | promoted |
| 7 | Add character `char_wb` TF-IDF cosine | 0.7364720387 | promoted |
| 8 | Character TF-IDF `min_df=1` | 0.7370877453 | promoted |
| 9 | Add low-rank latent-semantic cosine | 0.7430195568 | promoted |
| 10 | Add latent SVD difference features | 0.7457772691 | promoted |

The fourth candidate failed because the imported upstream prose mentioned a validation split while the activated pack materialized only `data/train.jsonl` and `data/test.jsonl`. The failure stayed in the evidence ledger. The controller now puts an authoritative materialized-data manifest into every Codex proposal prompt and explicitly forbids assuming complete-but-unlisted splits.

The final canonical parameter state is:

```json
{
  "alpha": 1.0,
  "char_tfidf_min_df": 1,
  "ridge_alpha": 1,
  "tfidf_min_df": 1,
  "use_char_tfidf_cosine": true,
  "use_svd_cosine": true,
  "use_svd_difference_features": true,
  "use_tfidf_cosine": true
}
```

Final code hash: `03c51cea85f528bc9dc5f863ac4cb8283fd6dd49c82ec9380eda65818d35bd03`.

## Independent-seed reconstruction

| Seed | Role | Baseline | Candidate | Improvement | Within-run stddev | Audit |
|---:|---|---:|---:|---:|---:|:---:|
| 0 | bounded Codex search | 0.5757186473 | 0.7457772691 | +0.1700586218 | 0.0000000000 | pass |
| 1 | fresh-project replay | 0.5757186473 | 0.7459789969 | +0.1702603496 | 0.0000000000 | pass |
| 2 | fresh-project replay | 0.5757186473 | 0.7454820648 | +0.1697634175 | 0.0000000000 | pass |

Seed 1/2 reconstructed the complete final experiment tree and parameters from the frozen task baseline. They did not continue the search, reuse metrics, or ask Codex to generate new code. This isolates reproducibility of the selected candidate from stochasticity and cost in the search policy.

## Evidence

- Seed 0 search report: `benchmark_runs/textualsimilaritysickspe-loop-codex-20260717T030950Z-ad6551/report.json`
- Seed 0 project: `benchmark_runs/textualsimilaritysickspe-loop-codex-20260717T030950Z-ad6551/projects/seed-0-93d2b10f62/`
- Replication report: `benchmark_runs/textualsimilaritysickspe-replication-20260717T034143Z-67c210/replication_report.json`
- Seed 1/2 projects: `benchmark_runs/textualsimilaritysickspe-replication-20260717T034143Z-67c210/projects/`
- Reusable replay command: `scripts/replicate_best_candidate.py`

The source project contains 11 evidence/run records: one verified baseline plus ten executed candidates. Each replay project contains exactly two: its independently evaluated baseline and reconstructed final candidate. The audit verified frozen/protected artifacts, run/evidence bijection, finite metrics, contract and artifact hashes, runtime claims, promotion lineage, and container isolation.

## Boundary and next decision

The result demonstrates a real end-to-end bounded experiment loop and stable candidate reconstruction, but not task generality. The next benchmark phase should therefore run the fixed three-task matrix and compare deterministic controller ablations. That phase must remain separate from this single-task search record.
