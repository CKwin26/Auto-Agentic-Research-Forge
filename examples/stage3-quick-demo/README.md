# Stage 3 quick paired experiment

This deterministic demo exercises the complete build-from-blueprint Stage 3
path without network calls. The folder also includes a declared
`research-forge.experiments.json`, `runner.py`, and frozen
`formal_dataset.csv` so the
same experiment can be discovered and executed through the Research Forge UI
without relying on an implicit script convention.

It compares:

- baseline: always predict class `0`;
- treatment: read the preregistered binary signal;
- formal data: six candidate rows with targets stored in a separate partition;
- matrix: two tasks × two seeds × two arms = eight formal RunCells;
- primary metric: independently recomputed accuracy;
- frozen success threshold: paired improvement of at least `0.40`.

Run from the repository root:

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe examples\stage3-quick-demo\run_demo.py
```

Docker must be running and the configured Python image must be available.
Generated runs are written under `.tmp/` and are not product source.

The verified 2026-07-26 run completed in 25.871 seconds and produced:

```text
Run cells: 8
Execution attempts: 8
All candidate/evaluator networks disabled: True
Candidate targets never mounted: True
Smoke evidence eligible: False
Baseline accuracy: 0.500
Treatment accuracy: 1.000
Paired effect: +0.500
Frozen effect threshold: 0.400
Qualification: qualified
Scientific verdict: supported
Evidence verified: True
Phase after completion: paper
```

This synthetic experiment demonstrates the Stage 3 mechanism. It is not
evidence that the treatment generalizes to a real-world dataset.
