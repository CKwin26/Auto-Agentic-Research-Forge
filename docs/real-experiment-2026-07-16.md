# First real AIRS experiment: SICK textual entailment

## Scope

- Task: `TextualClassificationSickAccuracy` from the official AIRS-Bench repository.
- Dataset: official SICK train/test split exported by the AIRS-lite adapter.
- Visible data: 4,439 labeled training rows and 4,906 test sentence pairs without labels.
- Protected metric: test `Accuracy` written only by the evaluator container.
- Runtime: `docker.io/library/python:3.12-slim`, no network, read-only root filesystem, dropped capabilities, fixed CPU/memory/PID/tmpfs limits, and separate candidate/evaluator containers.
- Repeats: two evaluator runs per candidate; replication seeds: 0, 1, and 2.

## Result

| Seed | Constant-neutral baseline | Promoted candidate | Improvement | Within-run stddev | Audit |
|---:|---:|---:|---:|---:|:---:|
| 0 | 0.5686914 | 0.6763147 | +0.1076233 | 0.0 | pass |
| 1 | 0.5686914 | 0.6763147 | +0.1076233 | 0.0 | pass |
| 2 | 0.5686914 | 0.6763147 | +0.1076233 | 0.0 | pass |

The promoted implementation is a standard-library multinomial Naive Bayes classifier over sentence-pair lexical, overlap, negation, and length features. The score is identical across all three seeds because the implementation is deterministic. Mean normalized gain toward the registered AIRS target of 0.905 is `0.3200136`.

## Controller findings

The pilot exposed four orchestration defects before producing the improving run:

1. Fingerprint-only duplicate feedback was not meaningful to the proposal model. The controller now provides compact semantic candidate history containing concrete parameter values, replacement paths and hashes, statuses, and outcomes.
2. The first content-based proposal assumed `scikit-learn` existed in the slim image and failed with `ModuleNotFoundError`. Runtime constraints and bounded stderr excerpts are now included in proposal context.
3. A pending constant-label ablation narrowly outranked the generated dependency repair. `invalid_execution` now gives deterministic priority to file-based repair proposals.
4. The integrity audit incorrectly required an evaluator phase after the candidate process had already failed. It now audits the evaluator phase only when it was actually attempted, while still enforcing the candidate isolation contract.

All changes are covered by the local test suite, including semantic duplicate feedback, invalid-execution repair priority, and isolated candidate failure before evaluator launch.

## Controlled-environment follow-up

The failed scikit-learn proposal was later replayed unchanged in the new `rf-airs-cpu:v1` controlled environment. Its TF-IDF + LinearSVC candidate completed two repeats at `0.6141459437` Accuracy with zero variance, improving over the `0.5686913983` baseline; the full integrity audit passed. This validates the new environment and resolves the missing-dependency failure, but it does not replace the stronger standard-library Naive Bayes result at `0.6763147167`.

See `docs/controlled-ml-environment.md` and `benchmark_runs/controlled-env-validation-20260717T024353Z-311ddd/validation.json`.

## Evidence

- `benchmark_runs/textualclassificationsic-loop-codex-20260716T112841Z-0a5362/report.json`
- `benchmark_runs/textualclassificationsic-loop-codex-20260716T112841Z-0a5362/report.md`
- `benchmark_runs/textualclassificationsic-loop-codex-20260716T112841Z-0a5362/replication_report.json`
- Per-seed projects and append-only ledgers are under the run's `projects/` directory.

## Boundary

This result proves a reproducible, isolated baseline improvement on the official split and metric semantics. AIRS-lite is not the official AIRS leaderboard container protocol, the registered target was not reached, and the result is not evidence of scientific novelty or manuscript readiness.
