# Verified Stage 2 baseline matrix — 2026-07-18

## Outcome

The preregistered no-gate baseline arm completed all nine real agent-run cells at `stage1_runs/research-agent-evidence-v2`. The project is now in `baseline_verified` with baseline run ID `baseline-matrix-stage2-22e124e44294`.

This is a baseline milestone, not the study result. The treatment arm, protected arm-blinded semantic evaluation, manual audit, and paired statistical analysis have not yet run. The completed audit establishes controller isolation and structured evidence integrity; it does not yet establish that the registered prose claims are semantically supported.

| Item | Verified value |
|---|---|
| Protocol | `stage2-22e124e44294`, revision 4 |
| Approved review | `review-2026-07-17T153937.084280+0000-3d8078` |
| Selected novelty | `novelty-02` |
| Research plan | `plan-2026-07-17T165102.797670+0000-58ebcd` |
| Backbone | Codex `gpt-5.4`, `openai-codex==0.1.0b3`, ChatGPT Pro authentication |
| Runtime | Docker 29.6.1, `rf-airs-cpu:v1` |
| Image ID | `sha256:ed0c4ef7a59bc0587c92b69ef994b1873d0fa9eb84c01ec8ce4f8bb5b126812d` |
| Matrix completed | baseline arm × 3 tasks × 3 seeds = 9/9 cells |
| Candidate executions | 9 valid, 0 invalid, 2 improving |
| Registered claims | 36 total, four per cell |
| Total wall clock | 2721.128495 seconds (45 minutes 21.128495 seconds) |
| Protocol audit | 15/15 checks pass, zero violations |
| Baseline audit | 5/5 checks pass, zero violations |

## Cell results

`Final` is the task-native score retained after the deterministic controller's promotion decision. It is not the study's primary claim-quality metric.

| Cell | Task | Seed | Fixed baseline | Candidate | Improvement | Final | Verdict |
|---|---|---:|---:|---:|---:|---:|---|
| b01 | SICK classification Accuracy | 0 | 0.568691399 | 0.590705259 | +0.022013861 | 0.590705259 | promoted |
| b02 | SICK classification Accuracy | 1 | 0.568691399 | 0.286180188 | -0.282511211 | 0.568691399 | valid, not promoted |
| b03 | SICK classification Accuracy | 2 | 0.568691399 | 0.614757440 | +0.046066042 | 0.614757440 | promoted |
| b04 | SICK Spearman correlation | 0 | 0.575718647 | 0.572554896 | -0.003163751 | 0.575718647 | valid, not promoted |
| b05 | SICK Spearman correlation | 1 | 0.575718647 | 0.516016235 | -0.059702412 | 0.575718647 | valid, not promoted |
| b06 | SICK Spearman correlation | 2 | 0.575718647 | 0.572554896 | -0.003163751 | 0.575718647 | valid, not promoted |
| b07 | SuperGLUE WSC Accuracy | 0 | 0.634615385 | 0.365384615 | -0.269230769 | 0.634615385 | valid, not promoted |
| b08 | SuperGLUE WSC Accuracy | 1 | 0.634615385 | 0.365384615 | -0.269230769 | 0.634615385 | valid, not promoted |
| b09 | SuperGLUE WSC Accuracy | 2 | 0.634615385 | 0.615384615 | -0.019230769 | 0.634615385 | valid, not promoted |

The retained three-seed scores are:

- SICK classification Accuracy: `0.5907052588666939`, `0.5686913982878108`, `0.6147574398695475`;
- SICK Spearman correlation: `0.5757186473267853` for all three seeds;
- SuperGLUE WSC Accuracy: `0.6346153846153846` for all three seeds.

## Frozen execution boundary

Each cell used the same frozen Research Forge/Codex backbone, one proposal iteration, a one-candidate state-safe pool, deterministic duplicate checks and failure diagnosis, and two Docker trials per run. Candidate and evaluator containers had no network, a read-only root filesystem, dropped capabilities, `no-new-privileges`, and fixed CPU, memory, PID, temporary-storage, output-size, package, task, prompt, and controller bindings.

The controller output root was the ASCII-only absolute path `C:\Users\austa\.research-forge-study\565ea1b0ec96-r4`, avoiding the Docker bind-mount Unicode I/O failure documented in abandoned protocol revision 3. Completed cell evidence was copied back into the protected project tree and bound by hashes in each `complete.json`.

The no-gate arm used the shared frozen finalizer and performed no claim rejection, verifier-guided revision, or removal. Structural auditing checked that experiment run IDs resolve to valid isolated runs, metrics exactly match records, artifact paths exist and are permitted, literature source IDs resolve, and novelty claims remain within the approved novelty source set.

## Recorded operational correction

Cell b04 initially failed structural validation because claim 2 attached three redundant baseline-run paths in addition to the declared candidate-run paths. The declared candidate `record.json` already contained the exact candidate score and exact improvement relative to baseline. The original registry, audit, and invalid record were archived under `stage2/operational_corrections/b04-redundant-cross-run-artifacts/`.

The correction removed only the three redundant artifact links and recomputed the registry ID. It did not change claim text, final output text, metrics, source IDs, controller output, or model output, and it did not rerun Codex or Docker. The frozen structural auditor then passed the corrected registry. The correction record contains before/after SHA-256 hashes and the exact removed paths.

Two earlier finalizer transport-schema HTTP 400 failures are also preserved under `stage2/operational_failures/`. They occurred after the controller run and before a registry was produced, and the controller was not rerun. The transport schema was narrowed while Pydantic post-validation remained active.

## Audit commands

```powershell
& .\.venv\Scripts\research-forge.exe --home stage1_runs study audit research-agent-evidence-v2
& .\.venv\Scripts\research-forge.exe --home stage1_runs study audit-baseline research-agent-evidence-v2
```

At completion, the protocol audit reported all 15 checks true and the baseline audit reported all five checks true. The latter verified exact matrix completion, cell integrity, prefix order, absence of active invalid cells, and the nine task-native scores above.

## Boundary and next gate

No unsupported-claim-rate comparison can be reported yet. The next valid step is to run the nine treatment cells with the same initial finalizer plus the frozen reject–revise–recheck gate, then score all 18 outputs with the protected arm-blinded evaluator. The preregistered 48-claim manual audit and false-positive stopping rule remain required before interpreting the primary metric.
