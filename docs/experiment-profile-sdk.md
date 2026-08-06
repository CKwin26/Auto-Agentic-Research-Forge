# Experiment Profile SDK

Research Forge uses a common governed research spine and typed Experiment
Profiles. A Profile is not a prompt and is not a collection of arbitrary shell
commands. It is the versioned scientific and executable definition of one
bounded experiment family.

## Common research spine

The platform owns:

- Project, Study, Scope and Research Contract versions;
- persistent DAG and StepInstance state;
- immutable artifacts, resource bindings and dependency edges;
- owner gates, repair contracts and successor runs;
- evidence binding, verdict history, manuscript control and completion records.

An Experiment Profile owns only the Stage 2/3 specialization:

- qualification rules for a particular experiment family;
- the family-specific contract schema and completion policy;
- required resource roles and resource validation;
- run-DAG compilation and the minimum dry-run fixture;
- runtime and evaluator adapters;
- result, evidence and claim schemas;
- conformance tests and the automation boundary.

Stage 1 and Stage 4 are platform-wide and Profile-independent. Stage 1 always
uses the common discovery, portfolio, owner-selection and Scope-freeze flow.
After Stage 3, every Profile must pass through
`stage-four-evidence-handoff-v1`, which projects its frozen outputs into the
same `ScientificClaimEnvelope`, `MandatoryReportingRegister` and
`EvidenceClaimMap` contracts. The canonical Stage 4 DAG alone owns outline,
drafting, scientific review, revision, figures, typesetting and PDF output.
Profile code is prohibited from generating a formal manuscript.

## Stage 2 and Stage 3 boundary

Stage 2 freezes scientific meaning: estimand, population, unit, arms, outcome,
metric, denominator, threshold basis, missingness, sampling, fairness, safety,
and the roles that data and tools must play. Stage 3 may resolve concrete file
paths, snapshots, images, package locks, compute devices and run identifiers.

Stage 3 must not invent a treatment, target rule, denominator, estimand or
success threshold. Missing scientific semantics return a typed qualification
report to Stage 2. Missing concrete execution bindings produce
`build_required`, which is a valid Stage 2 handoff rather than a scientific
failure.

## Lifecycle

Every Profile exposes the same operations:

1. `qualify`
2. `complete_contract`
3. `resolve_resources`
4. `compile_run_dag`
5. `dry_run`
6. `execute`
7. `evaluate`
8. `bind_evidence`
9. `adapt_for_stage_four` (the shared platform adapter, never a Profile writer)

Each operation is independently marked `not_implemented`, `component_tested`,
`integration_tested`, or `certified`. Formal execution is allowed only when the
complete path is connected and the underlying Profile Bundle is certified (or
is an immutable legacy Profile). A planned Profile can be visible in the UI
without becoming selectable for formal execution.

## Maturity evidence

| Level | Evidence |
|---|---|
| C0 | Description and automation boundary only |
| C1 | Typed contract and result schemas |
| C2 | Compiler, evaluator and minimal dry run |
| C3 | Real fixture and end-to-end evidence binding |
| C4 | Independent clean replay from the frozen package |
| C5 | Adversarial validation across multiple unrelated projects |

Maturity describes evidence about the Profile implementation. It never upgrades
the scientific verdict of an individual Study.

## Current and planned families

The current runtime registry migrates existing tabular, benchmark prediction,
existing Python project, paired computational and multi-arm Profile Bundles.
`tabular_ml_v1` has a real-fixture maturity claim; narrower current Profiles
retain their existing component or integration boundaries.

`time_series_backtest_v1` is the first new SDK-native certified Profile. Its
narrow C3 boundary accepts an already-authoritative point-in-time signal CSV
and a separately mounted evaluator-only return CSV. The generated candidate
process can rank assets but cannot see realized returns; the evaluator binds
selected portfolios to returns, frozen transaction costs, the registered
denominator and paired period evidence. It compiles through the persisted
Stage 3 run-plan path. Raw price-feed ingestion, exchange-calendar creation,
survivorship reconstruction and corporate-action adjustment are intentionally
outside this certification.

`llm_evaluation_v1` is a C2 development Profile. It already provides strict
JSONL schemas, evaluator-only reference isolation, NFKC/case/whitespace
normalization, missing-response policy and paired deterministic scoring for
already-generated responses. Model invocation, prompt/decode execution and
LLM-as-judge remain unconnected, so it is visible but cannot enter formal
Stage 3.

The catalog also contains explicitly non-runnable templates for image
supervision, reinforcement learning, engineering simulation,
bioinformatics, observational causal analysis, human behavior and wet-lab
protocols. Human and wet-lab templates are initially
`design_and_import_only`; Research Forge must not claim recruitment, consent,
physical execution or ethics approval.

## Adding a Profile

A new Profile must add:

1. a unique versioned Profile id and family descriptor;
2. a strict parameter model with no silent extras;
3. a deterministic contract validator;
4. required resource roles and immutable binding rules;
5. compiler, runtime and evaluator adapters plus inputs for the shared evidence adapter;
6. a tiny dry-run fixture that produces realistic output;
7. one real success case;
8. semantic-mismatch, missing-resource and tampering failures;
9. a frozen formal run and a bounded successor repair test;
10. an independent replay test before claiming C4.

Every registered Bundle must declare the same
`stage_four_evidence_adapter_id=stage-four-evidence-handoff-v1`. A different
Stage 4 adapter id is not a new experiment family; it is an incompatible
publication pipeline and must not be registered as a Profile.

A development Profile may receive a `Stage3Profile` id so its schema and dry
run can be exercised, but it must keep a `DEVELOPMENT` Bundle and cannot appear
in the formal execution list until the full path is connected. Unconnected
adapters return an explicit unsupported state.

## Reference patterns

The SDK design follows the separation found in established systems:

- MLPerf defines benchmark data, quality targets, rules and reference
  implementations: <https://mlcommons.org/benchmarks/training/>.
- lm-evaluation-harness separates task configuration and model request
  backends: <https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/task_guide.md>.
- Gymnasium versions environment reset/step interfaces:
  <https://gymnasium.farama.org/main/introduction/create_custom_env/>.
- FMI separates model exchange and co-simulation interfaces:
  <https://fmi-standard.org/docs/main/>.
- nf-core standardizes modules, containers, schemas and small test profiles:
  <https://nf-co.re/docs/developing/overview>.
- OHDSI CohortMethod binds target, comparator, outcome and diagnostics:
  <https://ohdsi.github.io/CohortMethod/>.
- jsPsych composes human experiments from typed trial plugins and timelines:
  <https://www.jspsych.org/v8/overview/plugins/>.
- Autoprotocol expresses physical instructions, containers and constraints as
  machine-readable data: <https://autoprotocol.org/specification/>.
