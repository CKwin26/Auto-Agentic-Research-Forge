# Existing project bundle workflow

This workflow turns a real project folder into a four-stage, evidence-bound working-paper run without modifying the source project.

## Why the idea gate is separate

The central diagnostic invariant is:

1. Stage 3 decides what the project evidence says about the idea.
2. Stage 4 decides whether a diagnostic working paper can be constructed from that evidence.
3. A separate paper-expansion gate decides whether complete-paper writing may start.
4. Neither Stage 4 nor the long-form writer may upgrade or rewrite the Stage 3 verdict.

This produces two independent signals:

- `stage_3_experimentation/idea_verdict.json`: `supported`, `refuted`, `mixed`, `inconclusive`, or `unverifiable`, plus evidence maturity and numerical paths.
- `stage_4_synthesis/audit.json`: whether the evidence-bound working draft closes the four-stage artifact chain.
- `stage_4_synthesis/paper_expansion_plan.json`: whether idea, evidence packaging, or literature grounding owns the next blocker.
- `stage_4_synthesis/paper_expansion_audit.json`: after expansion, whether the complete manuscript passes depth, citation, number, and conclusion-binding gates.

If the idea verdict is clear but manuscript generation fails, the paper pipeline is at fault. If the verdict is mixed or inconclusive, additional evidence is required even when a polished manuscript could be written.

## Automatic boundary convergence

The intake uses two explicitly different modes. It first scans `protocols/*.json` and looks for a complete declared chain:

```text
frozen protocol -> machine-readable output -> conclusion report
        |                    |                    |
        +---------- exact hashes and binding ----+
```

Related implementation and test files are located by the protocol identifier and track-specific terms. Complete chains rank ahead of incomplete or newer-looking files. The selected protocol supplies the question, periods, strategy, execution rule, evidence role, and forbidden interpretations. The system does not infer external academic novelty from local files; it records a project-derived novelty candidate that still needs literature verification.

If no declared chain is complete, the intake falls back to `derived_materials` mode:

```text
project/text materials -> provisional question -> evidence-gap verdict -> working paper
                                   |
                                   +-- status is locked to unverifiable
```

The fallback selects a primary boundary document, a findings document when present, the strongest structured evidence file, and related implementation/tests. It hashes and snapshots those exact inputs across the four stages. It does **not** infer a missing control group, frozen protocol, causal effect, or prospective validation. A positive sentence in a project report cannot upgrade the idea verdict in this mode.

## Four-stage resource use

| Stage | Decision | Typical source-project resources | Durable outputs |
|---|---|---|---|
| 1. Discovery | Which declared track is most paperable, or what provisional question can be derived from the materials? | README, text library, methodology/data-boundary docs, protocol, existing report | `novelty_candidates.json`, human-readable `novelty_candidates.md`, `scope_contract.json`, `resources.json` |
| 2. Protocol | Is evidence bound to an exact frozen protocol, or is the missing binding explicitly locked as a gap? | protocol/boundary source, implementation, tests, leakage/model/audit controls | `protocol_lock.json`, `resources.json` |
| 3. Experimentation | What does bound evidence say, or why is the idea currently unverifiable? | output/structured observations, protocol/boundary source, executable/test evidence | `idea_verdict.json`, `resources.json` |
| 4. Synthesis | Can those claims form an auditable working paper without changing the verdict, and is full-paper writing eligible to start? | protocol, output, conclusion report, frozen verified literature when present | `claims.json`, `manuscript.md`, `paper_expansion_plan.json`, `audit.json`, `resources.json` |

The completion certificate hashes the bundle manifest, scope, protocol lock, idea verdict, claim registry, manuscript, and audit.

## Security and reproducibility boundary

The source project is read-only. The run snapshots only selected text and structured-data resources. It excludes:

- `.env*`, credential/secret files, private keys;
- `.git`, local agent/hosting metadata, virtual environments, package/build caches, `node_modules`, temporary and generated `work` directories;
- unsupported binary files;
- individual files larger than 25 MiB;
- selected snapshots larger than 100 MiB in total.

Each copied resource is checked against the inventory SHA-256. `bundle audit` and completion verification detect later snapshot or artifact tampering.

## Commands

```powershell
$py = ".\.venv\Scripts\python.exe"

& $py main.py bundle inspect "C:\path\to\project"

& $py main.py bundle close-loop "C:\path\to\project" `
  --output-root bundle_runs `
  --track auto

& $py main.py bundle audit "C:\path\to\bundle_runs\RUN_ID"

& $py main.py bundle prepare-paper "C:\path\to\bundle_runs\RUN_ID"

& $py main.py bundle expand-paper "C:\path\to\bundle_runs\RUN_ID"

& $py main.py bundle audit-paper "C:\path\to\bundle_runs\RUN_ID"
```

Use an exact protocol `version` with `--track` when the automatically recommended chain is not the intended scientific question.

## Readiness meanings

- `pilot_draft_generated=true`: the four-stage loop produced an evidence-bound working draft with the project conclusion and idea verdict attached. This proves pipeline closure, not manuscript readiness.
- `paper_expansion_plan.ready=true`: clean prospective idea verdict, exact protocol-output binding, at least 15 bound numeric results, and at least 15 verified papers are frozen by `literature_manifest.json`.
- `full_manuscript_generated=true`: the long-form writer returned a complete manuscript candidate. Generation alone is not success.
- `manuscript_depth_passed=true` in `paper_expansion_audit.json`: the complete manuscript passed the configured journal-article structure, length, section, citation, and result-density gate.
- `paper_draft_ready=true`: full-manuscript depth, citation resolution, frozen numeric evidence, and verbatim conclusion binding all passed. It does not by itself mean the paper is publishable.
- `idea_validated=true`: only a clean supported/refuted verdict from prospective blind evidence. Retrospective results never receive this label.
- `diagnostic_owner`: reports `idea_validation`, `evidence_packaging`, `literature_grounding`, or `paper_writer`, so a failed idea cannot be confused with a failed writing agent.
- `publication_ready=true`: remains false until full-text novelty review, independent human review, venue formatting, authorship confirmation, and submission approval are complete.
