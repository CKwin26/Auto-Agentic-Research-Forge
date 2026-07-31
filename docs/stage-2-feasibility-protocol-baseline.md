# Stage 2: Specific Question, Experiment Plan, and Feasibility MVP

Stage 2 converts a frozen broad research direction into a specific,
falsifiable computational Study. It defines the conceptual experiment and
builds a deliberately small, non-scientific MVP before handing the Study to
Stage 3. The MVP proves that the minimum environment can start, the conceptual
arms can be instantiated, and the primary metric can be computed. It does not
estimate a treatment effect and cannot emit a scientific verdict.

## Authority boundary

Stage 2 may:

- investigate related methods, baselines, datasets, metrics, and known failure
  modes through the Forge Retrieval Gateway;
- inventory local data, code, models, software, compute, evaluation, and
  operational resources without modifying the project bundle;
- inspect metadata, test data loading and schemas, assess leakage risks, run
  two or three non-scientific smoke cases, and estimate runtime/reset cost;
- create a specific-topic Scope vNext, a Research Contract draft, deterministic
  decision rules, an MVP specification, a Stage 3 resource plan, feasibility
  reports, and immutable scientific-core lock files.

Stage 2 may not:

- generate or execute a treatment command;
- construct or require the complete formal evaluation dataset;
- execute the full baseline or treatment matrix;
- inspect treatment results while choosing metrics, thresholds, or stopping
  rules;
- turn retrieved attention signals or inferred artifact relations into verdict
  evidence;
- treat a failed command as a refuted hypothesis;
- freeze a failed Stage 2 Gate.

## Persisted DAG

| Step | Type | Required output |
|---|---|---|
| 2.0 | Input check | `stage2_input_check.json` |
| 2.1 | Method investigation | `related_method_cards.jsonl`, `research_method_summary.md`, `baseline_candidates.json` |
| 2.2 | Local resource inventory | data, code, compute, resource, quality, and provenance manifests |
| 2.3 | Data boundary | `data_boundary.json`, `leakage_risk_report.json` |
| 2.4 | Resource gaps | `resource_gap_report.json`, `resource_request_cards.jsonl`, `resource_acquisition_plan.md` |
| 2.5 | Resource requirements | `resource_requirements.json` with contract fields, capabilities, formats, and query terms |
| 2.6 | Concrete resource discovery | `concrete_resource_candidates.json` for local and Retrieval Gateway candidates |
| 2.7 | Resource validation and comparison | `resource_candidate_evaluation.json`, `resource_candidate_comparison.md` |
| 2.8 | Candidate topics | `candidate_topics.json`, `topic_feasibility_matrix.md`, `topic_recommendation.md` |
| 2.9 | Owner topic and resource selection | frozen specific-topic `ScopeContract vNext`, `resource_selection.json` |
| 2.10 | MVP and Stage 3 plan | `mvp_spec.json`, `stage3_resource_plan.json`, reusable experiment scaffolding |
| 2.11 | Protocol draft | `protocol.draft.json`, draft `ResearchContractVersion` |
| 2.12 | Decision semantics | `decision_rules.json` |
| 2.13 | Design preflight | `preflight_report.json` |
| 2.14 | Feasibility MVP validation | `baseline_validation_report.json` (legacy filename; no scientific baseline result) |
| 2.15 | Gate assessment | `stage2_gate_report.json` |
| 2.16 | Owner approval and freeze | frozen Research Contract and five lock files |
| 2.17 | Exit | Stage 3 handoff |

Every step is a persisted `StepInstance`. Method investigation and local
resource inventory run in parallel after 2.0. The workflow stops at 2.9 and
2.16 for the two required owner decisions.

If the input check is blocked, each failed attempt is retained as
`stage2_input_check.attempt-NNN.json`. An explicit retry writes a new attempt;
once the external condition is corrected, the successful check writes the
canonical `stage2_input_check.json` without changing any earlier attempt.

## Resource semantics

Every resource or gap records its category, availability, validation state,
blocking level, license state, acquisition method, and evidence state
(`verified`, `reported`, `inferred`, or `unknown`).

The purpose of this loop is experimental-boundary formation, not resource
cataloguing. Candidate topics consume the resource evaluation directly:
missing required resource types block a topic, metadata-only or not-yet-probed
resources make it conditional, and only eligible version-bound resources can
make the resource dimension ready. Different topic templates may therefore
have different boundaries even under the same broad direction. The selected
topic records its required resource types, recommended candidate IDs, and a
human-readable boundary summary.

Unknown values remain unknown. HDF5 metadata inspection, for example, verifies
container structure and shapes; it does not silently claim that labels,
missingness, or scientific suitability were validated.

Preflight checks use the same evidence vocabulary. A deterministic manifest,
hash, schema, or runner check may be `verified`; a value supplied through the
owner's protocol form is `reported`; a conclusion derived from inspected
metadata is `inferred`. For projects that must build or acquire experiment
assets, full-data and full-runtime checks are Stage 3 work. Their Stage 2 Gate
instead requires a verified non-scientific MVP plus a complete scientific
design and Stage 3 resource plan.

Protocol retrieval uses `RetrievalPhase.PROTOCOL`, a frozen Scope reference,
an explicit purpose and usage role, a bounded budget, sanitized queries, and an
idempotency key. Offline policy produces an explicit `not_authorized` result
and empty method cards rather than invented literature.

The concrete-resource loop turns each gap into a typed requirement for a
dataset, implementation/toolkit, benchmark, or model. Candidate records retain
the canonical identifier, provider, version or revision, content hash, license,
availability, metadata status, and safe-probe status. The comparison score is
an explainable ranking only; a failed blocking check cannot be outweighed by a
high score.

Local candidates are inspected read-only and pinned by content hash. External
candidates remain metadata-only until a separate policy-controlled acquisition
is approved. Discovery never installs a package or runs remote code. The owner
selects resource candidates together with the topic; the immutable
`resource_selection.json` explicitly records that selection alone authorizes
neither acquisition nor execution.

## Direction Scope and specific-topic Scope

Stage 1 freezes `ScopeContract v1` with `contract_level="direction"`.
Selecting a Stage 2 candidate creates a new immutable version with
`contract_level="specific_topic"` and `predecessor_version` pointing to the
direction Scope. The Stage 1 record is never overwritten.

Candidate topics are `ready`, `conditional`, or `blocked`. A blocked candidate
cannot be selected. External attention may help formulate candidates but does
not count as scientific evidence. The feasibility matrix reports scientific
value, novelty, falsifiability, project relevance, data, baseline, compute,
implementation, auditability, evidence strength, compliance, time, and
monetary cost separately with reasons and dependencies; it never collapses
them into an opaque total score. If every candidate fails a hard constraint,
Stage 2 emits a draft `scope_change_request.json` and requires an owner-confirmed
return to Stage 1.

The protocol draft preserves the 50 required design concepts as explicit
fields, including separate sample-size and statistical-power rationales.
Unknown values remain blocking unknowns rather than being filled from general
knowledge.

## Feasibility MVP

For a Study without a reusable verified experiment chain, Stage 2 emits an
`mvp_spec.json`. The bound MVP report must contain exactly two or three passed
smoke cases and verify all of the following:

- the minimum local environment started;
- the conceptual baseline can be instantiated;
- the metric denominator and value can be computed;
- expected rejection and execution failures can be distinguished;
- reset or isolation between cases works;
- runtime cost is positive and recorded.

The report must explicitly set `scientific_evidence_eligible=false` and may not
contain a scientific verdict, effect size, treatment effect, or p-value.
Approval snapshots and hashes the report, creates a protocol vNext, and leaves
all earlier Gate records unchanged.

For HDF5 projects, the bundled feasibility adapter selects two or three
owner-selected files, opens them read-only, verifies aligned action and
observation or state trajectories, checks that source size and modification
time remain unchanged, and exercises the frozen metric schema with an
explicitly synthetic fixture. Raw state/action trajectories may pass Stage 2
while creating a Stage 3 observation-materialization task. The synthetic
fixture value is never experiment evidence, and a passing MVP leaves
`baseline_verified=false`.

Projects that already contain a verified, immutable experiment chain retain
the compatibility path. Its existing baseline may be validated in Stage 2,
but a newly built full baseline is not required merely to complete Stage 2.

## Gate and locks

`stage2_gate_report.json` is one of:

- `PASS`: every preflight and baseline condition is satisfied with verified
  evidence;
- `CONDITIONAL_PASS`: no blocker remains, but explicit warnings require owner
  acceptance;
- `DESIGN_READY`: the specific question, hypotheses, conceptual arms, metric,
  denominator, verdict semantics, Stage 3 resource plan, and non-scientific MVP
  are ready; full execution assets remain Stage 3 work;
- `BUILD_REQUIRED`: the experiment design exists, but the non-scientific MVP
  has not yet demonstrated minimum executability;
- `FAIL`: at least one blocking protocol, integrity, or baseline condition
  remains.

A failed report has no approvable Research Contract Gate. After a non-failing
report and owner approval, Stage 2 freezes:

- `protocol.lock.json`
- `environment.lock.json`
- `data_manifest.lock.json`
- `code_manifest.lock.json`
- `decision_rules.lock.json`

Each lock names its source artifact, source SHA-256, Scope version, Research
Contract version, resource-selection hash and approved resource IDs, and freeze
time. For `DESIGN_READY`, these are scientific-core and feasibility locks; the
handoff explicitly requires a Stage 3 execution-contract vNext before formal
runs. The Research Contract carries selected data IDs in `data_boundary`,
implementation IDs in `runtime_binding`, and benchmark IDs in
`evaluator_policy`. Completion advances the Study to
`phase="experiment"`; only Stage 3 may run the treatment.

## Local API

- `GET /api/studies/stage2?study_id=...`
- `POST /api/studies/stage2/initialize`
- `POST /api/studies/stage2/topics/select`
- `POST /api/studies/stage2/protocol/revise`
- `POST /api/studies/stage2/mvp/approve`
- `POST /api/studies/stage2/approve`
- `POST /api/studies/stage2/amendments`

Topic selection accepts `resource_candidate_ids` plus separate
`scope_overrides` and `protocol_overrides`.
Protocol overrides cannot authorize treatment execution or a scientific
verdict. Before freeze, a protocol revision creates `ResearchContractVersion`
vNext plus versioned protocol, decision-rule, preflight, baseline, and Gate
artifacts; the earlier draft and audit records remain unchanged. A post-freeze
amendment is append-only: it records whether treatment
results were viewed, the requested changes and impact scope, rerun and
exploratory-downgrade requirements, and any required owner reapproval. It never
rewrites a frozen lock.

## Legacy compatibility

The older claim-gating experiment implementation in `study.py`,
`study_models.py`, and `study_runner.py` remains readable. Its baseline,
treatment, and evaluator matrix is a study-specific adapter. Under Workflow v2
the treatment and result verdict portions belong to Stage 3; they are not the
generic Stage 2 source of truth.
