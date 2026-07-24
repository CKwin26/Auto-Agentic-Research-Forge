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
- `POST /api/projects/create`
- `POST /api/studies/create`
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

Protocol, Experimentation, Synthesis, and Repair retrieval StepDefinitions and
StageProfiles are registered. Until their phase-specific business adapters are
connected, execution returns `blocked/not_implemented` and does not fabricate
successful grounding, experiment resources, citation audits, or repairs.
