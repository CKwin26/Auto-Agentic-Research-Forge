# Study Design Kernel implementation plan

Research Forge currently binds domain execution, study design, inference, and
verdict semantics inside one `ExperimentProfileBundle`.  The new kernel splits
those responsibilities without introducing a second workflow, Run, Artifact,
Verdict, maturity, or publication system.

## Reused types and lifecycle

- `ExperimentProfileDescriptor` and the existing domain Profile registry remain
  authoritative for data, tools, candidate execution, and domain outputs.
- `ResearchContractVersion` remains the only scientific contract and keeps its
  immutable vNext rules.
- `RunPlan`, `ResearchRun`, `ResultEnvelope`, `EvaluationRecord`, scientific
  Verdicts, Evidence Chains, Stage 4, and Completion Records are reused.
- Existing completion authority classes are retained and extended with richer
  issue metadata in `StudyDesignCompletionPatch`.

## Contract migration

New contracts declare a domain Profile, one Study Design, and zero or more
Inference Modules.  Existing `experiment_profile` and `profile_parameters`
remain readable and writable.  When `domain_profile` is absent, the legacy
field is the derived domain binding.  Existing Profile validators and frozen
contracts are not reinterpreted as independent-group designs.

## New files

The first implementation adds `research_forge/study_design/` with schemas,
SDK protocols, registries, completion and validation services, an analysis
compiler, evidence adapter, acceptance evaluator, the independent-group
design, and noninferiority/equivalence, multiplicity, and Bayesian modules.
It also adds focused tests and then connects the kernel to the existing Stage
2, Stage 3, Stage 4, capability API, and UI.

## Maturity target

- `independent_group_comparison_v1`: target C3 only after the formal black-box
  Idea-to-paper case, reference recalculation, negative tests, mutation tests,
  paper audit, and hash-bound acceptance report all pass.
- `noninferiority_equivalence_v1` and `multiplicity_control_v1`: target C3 only
  if exercised in that formal lifecycle.
- `bayesian_inference_v1`: maximum C2 in this iteration; the first executable
  scope is binary Beta-Binomial sensitivity analysis.
- Future factorial, longitudinal, survival, causal, online A/B, and human-rating
  designs remain explicit C0 roadmap entries and cannot enter formal routing.
