# RF-Bench v1

RF-Bench evaluates the research loop as an evidence-producing system. A fluent report cannot compensate for a changed evaluator, a missing run, a non-finite metric, or a claim that does not match the recorded artifacts.

## What is implemented

- separate candidate and evaluator processes: the candidate writes a submission; only the evaluator writes metrics;
- frozen research and execution contracts plus a protected-artifact manifest for evaluator and task files;
- one baseline and a bounded candidate budget per seed;
- deterministic grid calibration and the real Codex proposal strategy;
- three packaged CPU diagnostics covering maximize, minimize, and coupled parameters;
- per-seed normalized gain, target success, valid-submission rate, reproducibility, discrete anytime AUC, and wall time;
- a post-run audit joining the evidence ledger to run records, manifests, parameters, trial metrics, workspace hashes, contract hashes, and promotion lineage;
- a two-container Docker runtime that separates candidate-visible inputs from protected evaluator assets and records the actual isolation command;
- a controlled CPU ML image with a digest-pinned base, hash-locked Python wheels, an isolated import/smoke-test probe, and capability evidence attached to every run;
- a resumable run-loop controller with deterministic diagnosis, equivalent-target fingerprints, bounded candidate-pool ranking, auto-promotion, and hard stop budgets;
- AIRS-Bench metadata import into the RF-Bench task schema.

## Task registry

Task IDs resolve through one discovery path. Packaged RF-Bench diagnostics live under `research_forge/benchmark_tasks/`; imported or activated external packs live under `benchmarks/<provider>/<task_id>/`. `benchmark list` reports the owning registry, and discovery fails on duplicate task IDs instead of silently shadowing one task with another. A direct task-pack path remains supported for development.

The current registry contains seven tasks: three built-in diagnostics and four AIRS packs. Six are runnable; the GPU rideshare MAE pack remains registered but non-runnable until its data/runtime adapter is activated.

RF-Bench v1 is a development evaluation, not a scientific-novelty benchmark. Public anti-cheating comparison additionally requires an OS/container boundary. When Docker is unavailable, reports are deliberately marked `publishable: false` even if every integrity check passes.

## Commands

```powershell
$py = ".\.venv\Scripts\python.exe"

& $py main.py benchmark list
& $py main.py benchmark doctor

# Deterministic harness calibration.
& $py main.py benchmark run rf-quadratic-max `
  --strategy grid `
  --seed 0 --seed 1 --seed 2 `
  --iterations 3

# Automatic controller. Docker plus rf-airs-cpu:v1 is the secure default for run-loop.
& $py main.py benchmark build-env
& $py main.py benchmark doctor
& $py main.py benchmark run-loop rf-quadratic-max `
  --strategy grid `
  --runtime docker `
  --seeds 0,1,2 `
  --iterations 3

# Exercise the actual Codex proposal path.
& $py main.py benchmark run rf-quadratic-max `
  --strategy codex `
  --seed 0 `
  --iterations 3

# Freeze, then run or resume the registered three-task controller ablation.
& $py main.py benchmark freeze-ablation
& $py main.py benchmark run-ablation "ABSOLUTE_FROZEN_MATRIX_DIRECTORY"

# Re-audit one materialized seed project.
& $py main.py benchmark audit "ABSOLUTE_PROJECT_PATH"

# Convert an AIRS task definition into an RF-Bench task pack.
& $py main.py benchmark import-airs "PATH_TO_AIRS_TASK" `
  --output-root ".\benchmarks\airs"

# AIRS scripted datasets currently require datasets 3.6.0. Keep this
# heavier data environment off a space-constrained system drive if needed.
py -3.12 -m venv "D:\ResearchForgeBenchEnv"
& "D:\ResearchForgeBenchEnv\Scripts\python.exe" -m pip install datasets==3.6.0

& $py main.py benchmark doctor `
  --dataset-python "D:\ResearchForgeBenchEnv\Scripts\python.exe"

# Prepare a CPU development version using the official dataset split and
# metric semantics. The imported pack becomes runnable after this command.
& $py main.py benchmark activate-airs-lite ".\benchmarks\airs\TASK_ID" `
  --dataset-python "D:\ResearchForgeBenchEnv\Scripts\python.exe" `
  --cache-dir "D:\ResearchForgeBenchData\datasets"
```

Outputs go under `benchmark_runs/<benchmark-id>/` by default:

```text
report.json
report.md
loop_manifest.json              # run-loop only
loop_summary.json               # run-loop only
projects/
  seed-<n>-<task-hash>/          # compact to stay under Windows path limits
    benchmark/task.json
    protected_manifest.json
    frozen_manifest.json
    evaluator/
    runs/
    evidence.jsonl
    lineage.jsonl
    benchmark/loop_state.json   # run-loop checkpoint
    benchmark/loop_events.jsonl # controller journal
```

## Integrity gates

A seed fails integrity if any of these checks fails:

1. frozen contracts, task files, or evaluator files changed;
2. an active run was left unresolved;
3. run IDs are duplicated or a run/evidence record is missing;
4. evidence fields differ from the authoritative run record;
5. a run used a different contract hash;
6. run manifests, parameters, trial metric files, aggregates, or workspace hashes disagree;
7. a promoted item is not a valid improving run;
8. the evaluated baseline differs from the registered baseline;
9. the target is not actually better than that evaluated baseline.

For a Docker run, the audit additionally verifies the recorded command contains `network=none`, a read-only root filesystem, dropped capabilities, `no-new-privileges`, CPU/memory/PID/tmpfs limits, exactly one writable output mount per phase, no evaluator mount in the candidate phase, no experiment mount in the evaluator phase, and a read-only submission mount for scoring. When an image claims to be controlled, the audit also requires a successful capability probe, a capability-manifest hash, and a non-empty exact package map. A report is publishable only when every run actually used this verified runtime; merely having Docker installed is not sufficient.

See `docs/controlled-ml-environment.md` for the lock files, verification contract, and real scikit-learn replay.

## Automatic run-loop policy

`benchmark run-loop` keeps control decisions outside the model:

1. recover an active interrupted run or reconcile a completed run that was not yet journaled;
2. summarize invalid, negative, and improving evidence into a deterministic failure diagnosis;
3. ask Codex for bounded proposals, or read registered grid candidates;
4. fingerprint the resulting code-plus-parameter target state and reject duplicates/no-ops;
5. rank the bounded candidate pool, favoring new axes and focused changes;
6. execute one candidate, score it only through the protected evaluator, and append evidence;
7. auto-promote only a valid improvement and retain every rejected/negative attempt;
8. stop on target, iteration budget, invalid-run budget, proposal-space exhaustion, or patience.

The proposal prompt also includes an authoritative manifest of files actually materialized under `data/`. When that manifest is complete, an imported description cannot make an absent split real: Codex must not assume an unlisted validation file and must derive any validation split deterministically from listed labeled training data.

`freeze-ablation` records task hashes, controller-source hashes, model/backend identity, Docker image ID, capability-manifest hash, runtime limits, seeds, task budgets, controller variants, and a cyclic execution order before any cell runs. `run-ablation` refuses source, task, model, image, or manifest drift and checkpoints every cell. On Windows, nested cell projects use the frozen short `.rfab/<manifest-prefix>/<order>/` layout to remain below `MAX_PATH`; the matrix directory retains all result references and summaries.

Resume uses the original frozen task hash, seed list, controller config, and runtime config:

```powershell
& $py main.py benchmark run-loop TASK `
  --strategy codex --runtime docker --seeds 0,1,2 --iterations 10 `
  --resume "ABSOLUTE_OUTPUT_DIRECTORY"
```

The packaged starter intentionally prints a fabricated score to stdout. The benchmark verifies that this value is ignored and that only the protected evaluator's metrics file enters the ledger.

## Scores

For a higher-is-better metric:

```text
normalized_gain = (best_valid_score - evaluated_baseline) /
                  (registered_target - evaluated_baseline)
```

For lower-is-better metrics the signs are reversed. A score of `1.0` reaches the registered target; scores above `1.0` exceed it. RF-Bench also reports:

- `success_at_n`: fraction of seeds reaching the target;
- `valid_submission_rate`: valid candidate runs divided by proposal attempts;
- `reproducible`: best run has the required repeats and stays within tolerance;
- `anytime_auc`: mean best normalized gain across the bounded iteration budget;
- `integrity_pass_rate`: fraction of seeds passing every hard gate.

The vector is authoritative. A single weighted score is intentionally not used because scientific gain and evidence integrity are not interchangeable.

## AIRS-Bench boundary

`import-airs` validates and preserves the AIRS `metadata.yaml`, task description, data preparation, and evaluator sources. The imported pack remains `runnable: false` until it is activated. This prevents an imported metadata file from being presented as a completed AIRS run.

`activate-airs-lite` currently supports AIRS tasks whose metric is `Accuracy`, `ExactMatch`, or `SpearmanCorrelation`. It downloads the official configured train/test split through Hugging Face `datasets`, exports agent-visible JSONL without test labels, generates a pure-Python baseline, and places labels beside the protected evaluator. It registers the evaluated baseline rather than trusting AIRS's estimated worst score.

The live compatibility check accepts two explicit runtimes: `datasets==3.6.0` for legacy scripted SICK tasks and `datasets==4.0.0` for current Parquet schemas that use the newer `List` feature. The selected interpreter is recorded by activation; unvalidated versions fail closed. Separate environments keep the compatibility split visible and avoid expanding the core Research Forge installation.

AIRS-lite is a development adapter, not an AIRS leaderboard submission. It uses the official data split and metric semantics but not AIRS's official container/harness; reports remain non-publishable without Docker isolation.

The next external adapter should execute prepared AIRS tasks inside the official container protocol, followed by the longer ResearchGym tasks. Manuscript and citation tracks belong after evidence-bound paper generation exists.
