# Frozen three-task controller ablation

## Bottom line

The first frozen Research Forge controller ablation completed all 12 cells with no failed integrity gate. It compared the full controller against removal of candidate pooling, deterministic duplicate detection, or deterministic failure diagnosis on WSC Accuracy, SICK Accuracy, and SICK Spearman.

The no-candidate-pool condition produced the highest descriptive mean normalized gain (`0.336613`, full controller `0.233014`), used 7 rather than 10 proposal attempts, and had no invalid runs. This is useful evidence that the current pool implementation needs work, not evidence that candidate pools are generally harmful. The matrix uses one registered seed, WSC permits only one candidate execution, no condition reached a registered task target, and Codex proposals are stochastic.

The main actionable finding is structural: a proposal left pending in the pool is generated against one canonical experiment state. If another candidate is promoted, that pending proposal can become stale when later applied to the new state. The full controller and no-failure-diagnosis SICK Accuracy cells each incurred one invalid run after this pattern. The next controller change should bind pending proposals to their generation-state hash and invalidate or regenerate them after promotion.

## Frozen protocol

- Matrix ID: `controller-ablation-20260717T044241.306133+0000-ab12a4`
- Manifest SHA-256: `98c590f11be31c0b52e34f89f3ce54afb978fab71540587861e9b6ccded0186d`
- Model: `codex:gpt-5.4`
- Runtime image: `rf-airs-cpu:v1`
- Image ID: `sha256:ed0c4ef7a59bc0587c92b69ef994b1873d0fa9eb84c01ec8ce4f8bb5b126812d`
- Capability manifest SHA-256: `a2ad47c62d247ae9aa6c3fc573ec1e5aaf7f14325e5105813f64353d9af67dba`
- Seed: `0`
- Tasks: WSC Accuracy (1 candidate), SICK Accuracy (3), SICK Spearman (3)
- Required repeats: 2 per baseline and candidate
- Variants: full, no candidate pool, no duplicate detection, no failure diagnosis
- Candidate executions: 28 total
- Proposal attempts: 37 total
- Wall time recorded across cells: about 2 hours 8 minutes

The execution order was frozen as a task-blocked cyclic schedule, so the full controller was not always first. All variants used the same task hash, seed, model, Docker image, runtime limits, patience, invalid-run budget, auto-promotion rule, and target-stop rule. The no-candidate-pool condition retained the same maximum invalid-proposal retry allowance but stopped proposing once it had one valid candidate.

## Aggregate results

| Variant | Mean normalized gain | Paired delta vs full | Anytime AUC | Mean valid rate | Proposals | Candidate runs | Invalid |
|---|---:|---:|---:|---:|---:|---:|---:|
| Full controller | 0.233014 | 0.000000 | 0.214133 | 0.583333 | 10 | 7 | 1 |
| No candidate pool | **0.336613** | +0.103598 | 0.212648 | **1.000000** | **7** | 7 | **0** |
| No duplicate detection | 0.212054 | -0.020960 | 0.137727 | 0.666667 | 10 | 7 | 0 |
| No failure diagnosis | 0.267169 | +0.034154 | 0.102007 | 0.583333 | 10 | 7 | 1 |

The full controller has the highest mean anytime AUC, but only narrowly over no candidate pool (`0.214133` versus `0.212648`). No candidate pool has higher final gain because its SICK Accuracy and Spearman trajectories happened to find stronger proposals within three executions. With one seed, this difference cannot separate controller effect from proposal stochasticity.

## Per-task results

| Task | Full | No pool | No duplicate detection | No failure diagnosis |
|---|---:|---:|---:|---:|
| WSC Accuracy | 0.634615 | 0.634615 | 0.634615 | 0.634615 |
| SICK Accuracy | 0.802894 | **0.814309** | 0.767631 | 0.774766 |
| SICK Spearman | 0.576456 | **0.653499** | 0.588136 | 0.628245 |

These are best scores, not normalized gains. Every WSC condition remained at baseline under its one-execution registered budget. No task/variant cell reached its registered target.

## What each ablation actually tested

### Candidate pool

The no-pool variant used one proposal per executed candidate (7 proposals, 7 runs). Pool variants used 10 proposals for the same 7 runs. Pooling did provide real pre-execution choice and retained unselected proposals across iterations. However, after promotion, retained proposals could be incompatible with the new canonical code. This makes the present result a test of the current state-unsafe pool, not of an ideal revalidated pool.

### Duplicate detection

No duplicate target was generated inside any cell: duplicate rejection count was zero for all variants, and executed fingerprints were unique inside the no-duplicate-detection cells. Therefore this matrix did not activate the duplicate-detection mechanism and provides no causal evidence for removing it. A future targeted diagnostic should deliberately create duplicate-prone proposal histories or use a longer budget.

### Failure diagnosis

The flag worked as intended: no-failure-diagnosis cells recorded `diagnosis_disabled` once per iteration and no `diagnosis_updated` events. The full SICK Accuracy cell classified a stale-candidate failure as `invalid_execution` and supplied stderr/repair priority on the final proposal turn. The newly generated repair proposal entered the pool, but a re-scored retained proposal was selected and improved instead. Thus the run verifies that diagnosis changes context and ranking, but does not isolate a successful repair caused by diagnosis.

## Integrity evidence

- 12/12 cells completed.
- 12/12 independent post-run audits passed.
- 12/12 cells verified candidate/evaluator Docker isolation.
- 40 evidence records matched 40 authoritative run records.
- Every cell report was publishable under RF-Bench's local integrity policy.
- AIRS-lite is still not an official AIRS leaderboard submission.

Authoritative artifacts:

- `benchmark_runs/controller-ablation-20260717T044241.306133+0000-ab12a4/matrix.json`
- `benchmark_runs/controller-ablation-20260717T044241.306133+0000-ab12a4/results.json`
- `benchmark_runs/controller-ablation-20260717T044241.306133+0000-ab12a4/report.md`
- Raw per-cell projects under `.rfab/98c590f11be3/`

## Preserved failed preflight

The first frozen attempt, `controller-ablation-20260717T043914.130148+0000-bda1bd` (manifest `4016f51a...8939`), created zero scientific run records because nested Windows paths exceeded `MAX_PATH`. It is preserved as infrastructure-failure evidence and excluded from this comparison. The corrected matrix froze a short cell-storage layout before rerunning; no task, model, variant, seed, or scientific budget changed.

## Decision

Do not remove candidate pooling or deterministic diagnosis based on this pilot. First make the pool state-safe by attaching a canonical generation-state hash and invalidating/reproposing stale candidates after every promotion. Then repeat the matrix with at least three independent seeds and a WSC budget greater than one if its registered task contract is deliberately revised. Keep duplicate detection enabled until a targeted test actually exercises it.
