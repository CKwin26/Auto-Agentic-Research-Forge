# Stage 3 build and execution architecture

Stage 3 turns the frozen scientific specification and the non-evidentiary
Stage 2 feasibility receipt into a concrete, auditable experiment. It is not
an arbitrary notebook agent and it cannot revise the Research Contract after
seeing formal results.

Version 1 supports exactly:

```text
computational_paired_comparison_v1
```

An unsupported design is persisted as `unsupported_profile`; it is never
represented as completed scientific work.

## Two freezes and two admissions

Stage 2 freezes the scientific specification:

- scope, hypotheses, and estimand;
- experimental unit and pairing key;
- baseline, treatment, and the only permitted arm delta;
- task, split, seed, and replicate policies;
- primary metric, denominator, direction, threshold, and decision rules;
- data, implementation, and environment requirements;
- resource routes, licences, network policy, and budget.

Stage 2 also supplies an `MVPFeasibilityReceipt`. The receipt can establish
metric computability, schema feasibility, rough resource cost, and minimum
runtime feasibility. It is always:

```text
purpose = feasibility_only
evidence_eligible = false
formal_run_eligible = false
```

`stage3_build_admission` checks whether that handoff is sufficient to start
construction. It does not require the final data, code, environment, or
manifest to exist.

After construction and isolated smoke tests, Stage 3 freezes the execution
implementation:

```text
protocol.lock.json
data_manifest.lock.json
code_manifest.lock.json
environment.lock.json
decision_rules.lock.json
research-forge.experiments.json
evaluator.lock.json
execution-package.lock.json
```

Each implementation lock records conformance to the Stage 2 scientific
specification. `formal_execution_admission` accepts only the completed,
conformant execution package.

## Profile-driven build layer

Both supported entry modes produce the same `ExecutionPackageSeal`.

### Ready-made experiment

The user already has code, data, and an experiment manifest. Stage 3 imports
them read-only, verifies hashes and declarations, runs bounded smoke checks,
checks specification conformance, and freezes the package.

### Build from blueprint

The Stage 2 handoff contains an `ExperimentBlueprint` but no complete runnable
package. The immutable `ExperimentBuildPlan` classifies every required asset
as:

```text
reuse | adapt | retrieve | generate | implement
```

Each build item records source routes, licence, acceptance checks, estimated
cost, and risks. Generated source is returned as structured data and
materialized by the platform; the model does not receive write access to the
materialization directory.

External resources are resolved only through the Forge Retrieval Gateway.
Only resources named by the frozen Research Contract can be promoted from
Protocol use to a new Experimentation `ResourceUseBinding`. Existing bindings
and snapshots remain immutable. A pinned third-party source archive is frozen
before use and is staged as read-only builder input; raw third-party code is
never executed during adaptation.

Missing resources, unusable licences, unsupported profiles, and construction
errors become distinct persisted build blockers:

```text
resource_blocked
license_blocked
unsupported_profile
build_blocked
```

They do not create a scientific verdict.

## Smoke and isolation boundary

Smoke execution can verify imports, schemas, deterministic seeds, resource
limits, and whether both arms produce legal sample-level output. It cannot:

- mount the formal target partition into a candidate container;
- compare formal baseline and treatment performance;
- change a metric, threshold, sample rule, or arm definition;
- promote smoke or MVP artifacts into the formal evidence chain.

Generated and remotely acquired code require isolated container execution:

- network disabled;
- read-only inputs;
- separate writable output;
- bounded CPU, memory, disk, process count, and timeout;
- no host credentials or writable project mount;
- candidate and evaluator run in different containers.

The candidate receives formal inputs but never the target values. The
independent platform evaluator receives frozen targets and recomputes the
primary metric from sample-level predictions. The generated evaluator is
tested with golden vectors but does not receive formal scientific authority.

## Persistent formal execution

After formal admission, the deterministic compiler expands:

```text
task x split x arm x seed x replicate
```

The Plan identity binds the Research Contract, scientific seal, execution
package seal, manifest, evaluator, compiler version, and all declared input
hashes. Identical inputs produce identical Plan and RunCell identifiers;
changed inputs create a successor identity instead of mutating history.

The persistent flow is:

```text
Stage3HandoffPackage
  -> ScientificSpecificationSeal
  -> ExperimentBuildPlan
  -> ExecutionPackageSeal
  -> FormalExecutionAdmission
  -> RunPlan / RunCell[]
  -> owner high-cost Gate
  -> StepInstance[] / ExecutionAttempt[]
  -> ResultEnvelope[]
  -> EvaluationRecord
  -> EvidenceEdge[] / EvidenceChain
  -> HypothesisVerdict / StudyVerdict
  -> Stage3CompletionPackage
  -> phase=paper
```

Only commands in the frozen manifest can run. Runtime state belongs to
`StepInstance`; every process launch creates an append-only
`ExecutionAttempt`.

## Qualification and scientific authority

The evaluator verifies expected cells, unique sample IDs, denominators,
pairing completeness, output schemas, frozen hashes, and baseline/treatment
sample alignment. It applies only the frozen metric and decision rules.

The three state spaces remain separate:

```text
operational_state
scientific_verdict
publication_readiness
```

A completed formal run may legitimately end as `supported`, `refuted`, or
`inconclusive`. A negative or inconclusive result is completed science and
does not trigger automatic repair.

The evidence graph explicitly binds:

```text
Research Contract
  -> Scientific Specification Seal
  -> Execution Package Seal
  -> Run Plan
  -> Research Run
  -> Output Artifact
  -> Evaluator Lock
  -> Evaluation
  -> Hypothesis Verdict
  -> Study Verdict
```

AI review and NLI may add warnings but cannot change the frozen rules,
qualification result, deterministic verdict, or historical graph.

## Scientific-integrity hardening

Profile v1.1 makes several declarations executable rather than decorative:

- `ModelSelectionPlan` freezes search space, trial count, selection split,
  stopping, checkpoint, seed, budget, and refit policy. Formal confirmation
  data cannot be used for multi-trial model selection.
- `ArmFairnessContract` freezes shared resources, compute budgets, pretraining
  and external-model policy, tuning parity, failure handling, and the
  smoke/development/formal partition boundary.
- `AttemptSelectionPolicy` makes the first qualified successful attempt
  canonical. A successful RunCell cannot be rerun to select a better or later
  outcome; implementation changes require a repair and successor Plan.
- each pair has a deterministic `pair_block_id`, AB/BA order, and an enforced
  dependency from the second arm to the first.
- `variance_unit` now controls computation. A task-level design aggregates
  seeds and replicates within task before estimating uncertainty; it does not
  pretend task-by-seed cells are independent tasks.
- the primary effect is recomputed by a second implementation and compared at
  a strict numerical tolerance.

Every first view of a formal target creates an immutable
`FormalEvaluationExposureRecord`. Reuse of the same target hash after results
were visible is labelled `adaptive_reuse`, even across Studies. It is not
presented as independent confirmation. A statistical assurance report and
systematic leakage report travel with the evaluation.

Stage 4 receives a `ScientificClaimEnvelope`, not unrestricted access to raw
logs as claim authority. The envelope states the allowed claim, population,
tasks, intervention, comparator, outcome, estimate, interval, evidence level,
confirmatory status, limitations, and prohibited generalizations.

## Portable trust and reproduction

`research-forge stage3 export-completion` exports a self-contained signed ZIP
or `tar.zst` containing frozen artifacts, W3C PROV JSON-LD, Workflow Run
RO-Crate metadata, and an offline HTML view. Ed25519 private keys are supplied
from a control-plane or KMS-managed path; they are never read from a project
bundle or written into an artifact.

The package can be checked without the database, web application, or worker:

```text
research-forge verify stage3-completion-package.tar.zst
```

The checker validates safe archive structure, hashes, signatures, Run Plan
identity, RunCell coverage, canonical attempts, sample identifiers and
denominators, metric recomputation, evidence graph presence, exposure state,
and Stage 4 eligibility.

Clean-room evidence is upgraded to L2 only from a valid worker-signed receipt
that states a fresh worker was used, the development directory and caches were
not mounted, only sealed assets were available, and the effect, interval, and
verdict reproduced. Merely exporting or rechecking a package remains L1.

Scheduler result publication uses a per-attempt lease and monotonically
increasing fencing token. A worker holding an expired token cannot commit a
result after a retry or takeover. Shared component defects create immutable
notices, global impact analyses, and per-Study `under_review` erratum overlays;
historical verdicts are preserved.

The container mount preflight rejects links, devices, sockets, pipes,
Docker/Podman sockets, secret filenames, and archive-bomb-sized trees. Runtime
attestations record non-root UID, no network, read-only root filesystem,
dropped capabilities, no-new-privileges, isolated IPC, file/process/memory/
CPU/log/output limits, and input/output inventories. Image signing, SBOM and
CVE policy remain deployment controls: a package must not claim them unless a
scanner or registry adapter has supplied verifiable records.

`run_stage3_backup_restore_drill` copies the signed package into a fresh root
and invokes the standalone checker. It writes a receipt while preserving the
source package append-only.

## Repair and successor rules

- transient operational faults retry the same RunCell within the bounded
  retry policy;
- an implementation correction requires an approved `RepairContract` and an
  append-only successor Plan;
- changing a hypothesis, metric, threshold, sampling rule, data boundary,
  estimator, or stopping rule creates a Scientific Successor and Research
  Contract vNext.

Safe reuse is permitted only when the frozen cell dimensions, implementation,
inputs, evaluator, and dependency graph remain compatible. Reuse creates new
lineage records; it never rewrites the predecessor.

## API and frontend

The Stage 3 API exposes build admission, build-plan creation, resource
resolution, materialization, smoke testing, package freezing, formal
admission, execution initialization, repair, and successor actions. Every
action writes persisted `StepInstance` state, including blockers and attempts.

The frontend renders the build path, formal matrix, owner Gate, attempts,
evaluation, evidence lineage, scientific verdict, diagnostics, repairs, and
successors. It labels build failure, execution failure, and scientific
non-support separately.

## Declared boundary

Completion of Profile v1 does not imply support for arbitrary computational
designs. A new statistical design requires an explicit profile, compiler,
qualification contract, evaluator, verdict policy, repair policy, frontend
renderer, and tests. Stage 3 must block unsupported designs rather than invent
methods ad hoc.
