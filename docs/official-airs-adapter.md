# Official AIRS / aira-dojo adapter

This is the only Research Forge path intended to produce results comparable to
the AIRS-Bench protocol. `activate-airs-lite` remains a local development and
regression adapter; Docker isolation does not upgrade Lite output into an AIRS
leaderboard result.

On Windows, official RAD evaluation runs in the Linux CPU container
`research-forge/airs-official-cpu:v1`: APPS uses the POSIX-only `SIGALRM`
interface. This is a compatibility requirement for the unchanged evaluator,
not a GPU requirement. Build it once (network is available only while building;
benchmark runs use `--network none`):

```powershell
docker build --tag research-forge/airs-official-cpu:v1 `
  --file .\docker\airs-official-runtime\Dockerfile .
```

## What is preserved

An official AIRS RAD task is imported with every source file SHA-256 bound in
`official_adapter_manifest.json`. The adapter executes the unmodified official
`prepare.py` before the solver, then unmodified `evaluate_prepare.py` and
`evaluate.py` after the aira-dojo agent has written `submission.csv`. Hidden
test labels live only in the evaluator mount.

```powershell
$py = ".\.venv\Scripts\python.exe"
$task = ".\tmp\airs-bench-official\airsbench\tasks\rad\TextualClassificationSickAccuracy"
& $py main.py benchmark import-airs-official $task --output-root ".\benchmarks\airs-official"
& $py main.py benchmark prepare-airs-official ".\benchmarks\airs-official\TextualClassificationSickAccuracy" `
  --global-shared-data-dir "D:\AIRSBenchRawData" `
  --agent-data-mount-dir "D:\AIRSBenchRuns\seed-0\agent-data" `
  --agent-log-dir "D:\AIRSBenchRuns\seed-0\agent-log" `
  --evaluator-data-mount-dir "D:\AIRSBenchRuns\seed-0\evaluator-data" `
  --python "D:\ResearchForgeBenchEnv\Scripts\python.exe" `
  --runtime docker --docker-image research-forge/airs-official-cpu:v1
```

The aira-dojo solver must write its submission into `agent-log`; only then run. In matrix mode, evaluator mounts are placed under `_protected_evaluator_data/`, outside each candidate workspace; the candidate workspace contains only `agent-data` and `agent-log`. The adapter creates the evaluator's required `./data/` work directory and invokes the original scorer from that evaluator working directory:

```powershell
& $py main.py benchmark evaluate-airs-official `
  ".\benchmarks\airs-official\TextualClassificationSickAccuracy" `
  --python "D:\ResearchForgeBenchEnv\Scripts\python.exe" `
  --runtime docker --docker-image research-forge/airs-official-cpu:v1
```

## Validation matrix

`examples/official-airs/aira-dojo-cpu-validation.json` freezes four CPU-first
tasks for smoke validation. Passing it proves adapter consistency, not a
leaderboard placement. The subsequent full run is frozen separately in
`examples/official-airs/aira-dojo-full-matrix.json`: all 20 official RAD tasks
times the 10 seeds used by AIRS Greedy leaderboard rows (200 cells). Full
benchmark reporting must retain the official per-task compute requirements and
use AIRS's normalized score definition rather than RF-Bench normalized gain.

## Resumable full matrix

Run the frozen 200-cell matrix through the resumable controller from the CPU
environment. `--candidate codex` uses the configured Codex provider; `baseline`
is only useful for protocol debugging. If a task has a genuine GPU requirement,
the cell must remain explicitly recorded as resource-unmet rather than being
silently omitted. A resource-unmet cell means the full matrix is incomplete;
it cannot be averaged or presented as an official full-matrix result.
`--require-cuda` is optional and only for a future GPU run.
Every attempted cell is appended to `official-matrix-ledger.jsonl`; a cell with
an evaluated run manifest is skipped on a later invocation.

```powershell
& .\.venv\Scripts\python.exe -m research_forge.official_airs_matrix `
  --matrix .\examples\official-airs\aira-dojo-full-matrix.json `
  --source-root .\tmp\airs-bench-official\airsbench\tasks\rad `
  --import-root .\benchmarks\airs-official-full `
  --shared-data D:\AIRSBenchRawData `
  --run-root D:\AIRSBenchRuns\official-full-matrix `
  --evaluator-python D:\ResearchForgeBenchEnv\Scripts\python.exe `
  --candidate codex --candidate-seconds 900 `
  --runtime docker --docker-image research-forge/airs-official-cpu:v1
```
