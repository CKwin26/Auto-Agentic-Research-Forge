# SICK lexical-overlap working-paper regression

This is Research Forge's second, unrelated end-to-end working-paper regression.
It uses the local AIRS-lite SICK textual-inference task and is deliberately
separate from the research-agent-evidence publication study.

## Frozen comparison

| Arm | Protected Accuracy | Repeats |
|---|---:|---:|
| Constant majority-label baseline | 0.5687 | 2 |
| Train-derived lexical-overlap and negation strata | 0.7338 | 2 |

The candidate exceeded the baseline by 0.1651 under the protected evaluator.
The implementation reads only the frozen training split; test labels remain in
the evaluator directory.

## Reproduce

```powershell
& .\.venv\Scripts\python.exe scripts\run_second_case_validation.py `
  --output case_studies\sick-lexical-replication-v1
```

The runner refuses to overwrite an existing output directory. It materializes
the task, freezes its contracts, runs baseline and candidate, promotes a real
improvement, synthesizes an evidence-bound working paper, and verifies the
completion certificate.

## Boundary

This is a portability regression, not a publication claim. Its result remains
`publication_ready=false` because local execution is not isolation verified,
the case has no frozen scholarly literature review, and its generated manuscript
does not meet the full journal-article depth gate. Those blockers are expected
and are part of the regression assertion.
