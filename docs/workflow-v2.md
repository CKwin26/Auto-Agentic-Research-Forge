# Research Forge Workflow v2

Workflow v2 is the source of truth for new Research Forge studies. The legacy
`Stage` value remains a read-only projection for older clients.

## Domain boundary

- A `Project` owns shared resources and a network policy.
- Every confirmed direction creates an isolated `Study`.
- `phase`, `execution_status`, `gate_status`, `repair_status`, and
  `artifact_status` are independent.
- Every executable unit is a persisted `StepInstance` in a dependency DAG.
- Only a `verified_chain` can support a formal hypothesis verdict.
- NLI output is stored as `NLIRiskAlert(authority="risk_alert_only")`.
- Human decisions append an `AdjudicationRecord`; they never overwrite history.

Research Forge v1 formally supports computational, simulation, computational
observational, and AI/ML studies with machine-readable results. Other imported
research types receive `diagnostic_only` support.

If Stage 4 discovers an evidence gap that can change the scientific
conclusion, it does not modify the historical Run or Verdict. The system
freezes the gap register and diagnosis, creates a Stage 3 scientific-successor
request, and redirects the Study phase to `experiment`. Stage 3 starts the
backfill only after the project owner approves a Research Contract vNext.
Wording, layout, and background-disclosure gaps remain in Stage 4.

## Required gates

The four owner gates are:

1. Scope approval
2. Research Contract freeze
3. Scientific repair or high-cost run approval
4. Final submission approval

Draft Scope and Research Contracts can be edited. Freezing requires the
matching approved gate. Any later change must use a new version.

## Public local API

The local server exposes:

- `GET /api/projects`
- `GET /api/step-definitions`
- `GET /api/studies?project_id=...`
- `GET /api/study?id=...`
- `GET /api/studies/stage2?study_id=...`
- `GET /api/studies/stage3?study_id=...`
- `POST /api/projects/create`
- `POST /api/studies/create`
- `POST /api/studies/discovery/select`
- `POST /api/studies/stage2/initialize`
- `POST /api/studies/stage2/topics/select`
- `POST /api/studies/stage2/protocol/revise`
- `POST /api/studies/stage2/approve`
- `POST /api/studies/stage2/amendments`
- `POST /api/studies/stage3/initialize`
- `POST /api/studies/steps/create`
- `POST /api/studies/steps/update`
- `POST /api/studies/gates/create`
- `POST /api/studies/gates/decide`
- `POST /api/studies/scope-contract`
- `POST /api/studies/research-contract`
- `POST /api/studies/baseline-verification`
- `POST /api/studies/impact`
- `POST /api/studies/repairs/propose`
- `POST /api/studies/ai-review`
- `POST /api/studies/publication-approval`
- `POST /api/studies/pause|resume|cancel|archive`
- `POST /api/network/audit`
- `POST /api/completion/verify`

Model payloads use the strict schemas in
`research_forge.workflow_domain`.

## Owner Gate decisions

Every owner Gate is a decision workspace, not a binary confirmation dialog.
The user can:

- approve the reviewed version and continue;
- type field changes or a natural-language revision request;
- leave the Gate undecided without mutating workflow state.

Before freeze, accepted edits update the pending draft and are recorded with
the owner reason and field diff. After freeze but before a formal run starts,
an edit creates `ScopeContract vNext` or `ResearchContract vNext` and preserves
the prior frozen version. Once formal execution has started, scientific edits
require a versioned `RepairContract` and successor run. No Gate action may
overwrite a historical contract, run, artifact, or verdict.

## Compatibility and migration

Closing a project bundle now creates Workflow v2 state automatically and keeps
the original `completion_certificate.json`. Existing bundle runs can be
imported with:

```text
research-forge workflow migrate-run <run-directory>
```

The new `completion_record.json` contains an integrity digest in addition to
artifact hashes. It is a local integrity record, not a digital signature or
third-party certification.

```text
research-forge workflow verify-completion <completion-record>
```

## Retry and pause semantics

Only `TransientTaskError` is retried automatically: the initial attempt plus at
most three retries with exponential backoff. Schema, permission, protocol,
hash, and scientific-integrity failures are not automatically retried.

Pausing a Study prevents new step attempts from starting. Completed artifacts
and all failed attempts remain available for a successor run.

## Forge Retrieval Gateway

External research access is horizontal Workflow v2 infrastructure. New
handlers declare Project, Study, phase, StepInstance, purpose, policy, contract,
budget, and idempotency context and call `research_forge.retrieval`; they do not
call Provider or HTTP clients directly.

The default retrieval mode is `offline`. Discovery now persists explicit DAG
nodes for query planning, policy evaluation, sanitization, retrieval,
normalization, deduplication, metadata verification, SourceSet construction,
Scope review, and SourceSet freezing. Discovery bindings are background or
attention inputs and cannot directly support a formal verdict.

The generic Protocol adapter is connected through the Stage 2 feasibility,
protocol, and baseline DAG. It performs contract-bound, policy-controlled
method investigation and records an explicit `not_authorized` result when the
Project remains offline. See
[`stage-2-feasibility-protocol-baseline.md`](stage-2-feasibility-protocol-baseline.md).
The generic Protocol adapter is followed by the connected
`computational_paired_comparison_v1` Stage 3 build and execution architecture.
Stage 2 freezes the scientific specification and a non-evidentiary MVP
receipt. Stage 3 first admits construction, resolves a Profile-driven Build
Plan, imports or builds the required assets, runs isolated smoke checks, and
freezes the concrete execution implementation. A second admission then
compiles the deterministic Run Plan, persists one StepInstance per run cell,
executes the approved matrix, independently qualifies outputs, builds a
verified evidence ledger, and materializes deterministic verdicts. Build
failure, execution failure, and scientific non-support remain separate states.
See [`stage-3-execution-kernel.md`](stage-3-execution-kernel.md).

Scientific identification is a separate cross-stage contract. Stage 2 freezes
the estimand and the dimensions that may vary, Stage 3 verifies that matched
controls, ablations, conditional analyses, robust inference, and release
artifacts were actually completed, and Stage 4 prevents the manuscript from
claiming a mechanism that the design did not isolate. See
[`scientific-identification-gates.md`](scientific-identification-gates.md).

Unsupported Experimentation profiles and unconnected Synthesis or Repair
adapters return an explicit `blocked/not_implemented` result. They do not
fabricate successful experiments, citation audits, or repairs.
