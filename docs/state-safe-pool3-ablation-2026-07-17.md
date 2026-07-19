# State-safe three-candidate-pool ablation

## Bottom line

The state-safety defect in the candidate pool is fixed and exercised by a real frozen experiment. Every pool-enabled execution waited for three distinct, current-state candidates; after a promotion, pending candidates were deterministically rebased, then either revalidated or invalidated before another candidate could be selected. Across the final 12-cell matrix, this guard prevented 19 stale candidates from running and revalidated 3 state-invariant candidates. No SICK cell incurred a stale-candidate execution failure.

The pool is now structurally correct, but it did **not** win this descriptive benchmark. The no-candidate-pool condition had the highest mean normalized gain (`0.352740`) and mean anytime AUC (`0.240817`), versus `0.224051` and `0.210068` for the full controller. It also required 7 proposal calls for 7 executions, while the full controller required 16 proposals for the same 7 executions. The current textual information-score heuristic is therefore not yet demonstrating enough selection value to pay for pool generation and state-change invalidation.

This is one registered seed across three small CPU tasks with stochastic Codex proposals. It proves the state-safety mechanism and exposes the next bottleneck; it is not a powered claim that candidate pools are generally inferior.

## Frozen protocol

- Matrix ID: `controller-ablation-20260717T070745.983951+0000-df5aeb`
- Manifest SHA-256: `68a6b9bbf54170a8ea03e68ede06fe3d253a16027294d9116bd7cc0ef58e6b83`
- Model: `codex:gpt-5.4`
- Runtime image: `rf-airs-cpu:v1`
- Image ID: `sha256:ed0c4ef7a59bc0587c92b69ef994b1873d0fa9eb84c01ec8ce4f8bb5b126812d`
- Capability manifest SHA-256: `a2ad47c62d247ae9aa6c3fc573ec1e5aaf7f14325e5105813f64353d9af67dba`
- Seed: `0`
- Tasks: WSC Accuracy (1 execution), SICK Accuracy (3), SICK Spearman (3)
- Required repeats: 2 per baseline and candidate
- Proposal allowance: at most 6 attempts while filling a pool in one iteration
- Pool target: 3 for full/no-duplicate/no-diagnosis; 1 for no-pool
- Scientific candidate executions: 28
- Official proposal files: 57

The matrix verification passed against its frozen task hashes, 11 controller-source hashes, model identity, image ID, and capability manifest before the post-run recovery hardening described below was added. The matrix remains a one-seed descriptive controller ablation.

## Aggregate results

| Variant | Mean normalized gain | Paired delta vs full | Anytime AUC | Mean execution yield | Proposals | Candidate runs | Invalid runs | Stale candidates blocked | Mean pool fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Full controller | 0.224051 | 0.000000 | 0.210068 | 0.420635 | 16 | 7 | 0 | 5 | 1.000 |
| No candidate pool | **0.352740** | **+0.128689** | **0.240817** | **1.000000** | **7** | 7 | 0 | 0 | 1.000 |
| No duplicate detection | 0.219773 | -0.004278 | 0.145199 | 0.396825 | 17 | 7 | 0 | 6 | 1.000 |
| No failure diagnosis | 0.297326 | +0.073275 | 0.127564 | 0.285714 | 17 | 7 | 1 | 8 | 1.000 |

“Execution yield” is the report's mean valid-submission-rate field: valid candidate executions divided by proposal files per cell. For pool variants it falls when valid-but-unselected or state-invalidated proposals are not executed; it is not an envelope-validity rate. The single invalid run occurred in the no-diagnosis WSC cell and was not a stale-candidate failure.

## Per-task best scores

| Task | Full | No pool | No duplicate detection | No failure diagnosis |
|---|---:|---:|---:|---:|
| WSC Accuracy | 0.634615 | 0.634615 | **0.644231** | 0.634615 |
| SICK Accuracy | 0.773135 | **0.815124** | 0.779658 | 0.751325 |
| SICK Spearman | 0.593598 | 0.666288 | 0.576456 | **0.672818** |

No condition reached a registered task target. WSC's one-execution budget is especially weak evidence about controller quality.

## Comparison with the state-unsafe pool

The preceding frozen matrix used the old state-unsafe pool (`controller-ablation-20260717T044241.306133+0000-ab12a4`). Its aggregate results are retained as historical context, not paired stochastic replications.

| Variant | Old mean gain | State-safe mean gain | Delta | Old proposals | State-safe proposals | Old invalid runs | State-safe invalid runs |
|---|---:|---:|---:|---:|---:|---:|---:|
| Full controller | 0.233014 | 0.224051 | -0.008963 | 10 | 16 | 1 | 0 |
| No candidate pool | 0.336613 | 0.352740 | +0.016127 | 7 | 7 | 0 | 0 |
| No duplicate detection | 0.212054 | 0.219773 | +0.007719 | 10 | 17 | 0 | 0 |
| No failure diagnosis | 0.267169 | 0.297326 | +0.030157 | 10 | 17 | 1 | 1 |

The important change is not the stochastic score delta. The old full controller executed a stale pending candidate and produced an invalid run. The state-safe matrix blocked stale targets before execution, filled every official pool to its frozen target, and produced zero invalid runs in the full condition. The cost is visible: promotions forced replacement proposals, so pool-enabled variants used 16–17 proposal calls rather than 10.

## What the experiment establishes

### Candidate-pool correctness

- 28/28 candidate executions were preceded by a full pool event.
- Pool-enabled events were 3/3; no-pool events were 1/1.
- 19 drifted pending candidates were invalidated after canonical-state changes.
- 3 state-invariant candidates were deterministically revalidated.
- Event replay confirmed that every selected proposal's generation-state fingerprint equaled the pool's canonical-state fingerprint at selection time.
- Official proposal-file count, persisted proposal-attempt count, and unique admission journal agreed in every final cell.

The pool can retain candidates when the canonical state is unchanged, so this is selective invalidation rather than unconditional clearing.

### Candidate ranking

The current information score rewards novelty, causal axes, repair relevance, and proposal structure; it is not a calibrated predictor of downstream metric improvement. In the clean no-diagnosis Spearman rerun, the first selected candidate was valid but scored only `0.226370`, far below the `0.575719` baseline. Later selections improved to `0.638721` and `0.672818`. This is concrete evidence that the pool's remaining problem is selection quality, not pool-state consistency.

The next candidate-pool benchmark should preregister a train-only proxy screen or successive-halving stage for all three candidates, select one for the protected evaluator, and record offline selection regret by evaluating the two unselected candidates without feeding their hidden-label results back into the controller. That separates proposal diversity from ranking quality.

### Duplicate detection

No official cell emitted a duplicate rejection. The no-duplicate variant happened to generate unique targets, so this matrix still provides no causal evidence for removing deterministic duplicate detection. Keep it enabled and add a targeted duplicate-prone test.

### Failure diagnosis

The no-diagnosis flag was active and recorded deterministic `diagnosis_disabled` events. Its stronger mean gain than the full controller was driven by one stochastic SICK Spearman trajectory, while it was worse on SICK Accuracy and produced the matrix's only invalid run. One seed cannot establish that diagnosis is harmful or helpful.

## Integrity and recovery evidence

- 12/12 official cells reached a terminal loop state with a non-empty stop reason.
- 12/12 independent project audits passed.
- 12/12 verified candidate/evaluator Docker isolation.
- 40 evidence records matched 40 authoritative run records.
- All official proposal envelopes recorded `codex:gpt-5.4`.
- 57 official proposal files matched 57 persisted proposal attempts and unique admission records.
- 28 completed iterations matched 28 full-pool events.

Two recovery defects were found and handled transparently:

1. An interrupted Codex stream initially caused the wrapper to summarize cell 08 while its loop remained `running` with zero candidate executions. The deterministic recovery check reopened it and completed all three iterations.
2. An early concurrent recovery race left two orphan proposal files in the original cell 10 output. That entire output was preserved under `excluded-cells`, removed from the official cell path, and cell 10 was rerun from a fresh baseline under the same frozen protocol. Only the clean rerun is included above.

Post-run hardening now marks errored/non-terminal loop reports non-publishable and makes the ablation summarizer reject non-terminal loops, report errors, proposal-journal/file mismatches, partial pools, missing proposal files, invalidated selections, and stale-state selections.

## Authoritative artifacts

- `benchmark_runs/controller-ablation-20260717T070745.983951+0000-df5aeb/matrix.json`
- `benchmark_runs/controller-ablation-20260717T070745.983951+0000-df5aeb/results.json`
- `benchmark_runs/controller-ablation-20260717T070745.983951+0000-df5aeb/report.md`
- Official per-cell projects under `.rfab/68a6b9bbf541/`
- Preserved excluded output under `benchmark_runs/controller-ablation-20260717T070745.983951+0000-df5aeb/excluded-cells/`
- Historical state-unsafe report: `docs/controller-ablation-2026-07-17.md`

## Decision

Keep the state-safe pool implementation; it fixes a real correctness failure. Do not claim that the present three-candidate selector improves task performance. For the default self-use workflow, either use pool size 1 for the cheapest reliable path or keep pool size 3 only when adding a deterministic train-only screening stage. The next experiment should measure selection regret across at least three seeds before making pool size 3 the performance default.
