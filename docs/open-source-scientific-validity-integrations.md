# Open-source scientific-validity integration review

This review records which external open-source projects can strengthen
Research Forge's new cross-stage scientific-validity gates. External tools are
optional executors: their outputs must be frozen and normalized before they
enter the artifact graph, and none of them may directly decide a scientific
verdict.

## Recommended integrations

| Project | License / role | Research Forge use | Integration boundary |
|---|---|---|---|
| [Evidently](https://github.com/evidentlyai/evidently) | Apache-2.0; machine-readable ML/LLM evaluation and test suites | Stage 3 optional diagnostic executor for drift, confusion matrices, calibration, segment performance, and custom metrics | Import JSON results into `ScientificValidityReport`; never let an LLM judge or Evidently threshold override the frozen statistical rule |
| [Great Expectations](https://github.com/fivetran/great_expectations) | Apache-2.0; data and schema expectations with Checkpoints | Stage 2 MVP and Stage 3 admission checks for schema, ranges, uniqueness, missingness, and split invariants | Freeze the expectation suite and Checkpoint result hashes; run locally/offline against owner-authorized data |
| [Manubot](https://github.com/manubot/manubot) | open-source scholarly-manuscript utilities | Stage 4 optional citation metadata normalization and an independent manuscript build check | Keep Research Forge's claim/evidence and readiness authority; use Manubot only for citation/build validation |
| [statcheck](https://github.com/MicheleNuijten/statcheck) | GPL; narrow statistical consistency checker | Stage 4 optional check that reported test statistics, degrees of freedom, and p-values agree | Run out of process and store only a normalized report; absence of statcheck-compatible tests is not a failure |
| [DVC](https://github.com/treeverse/dvc) | Apache-2.0; data/pipeline versioning | Optional source of content hashes and reproduction commands for projects that already use DVC | Do not replace the Forge Artifact Dependency Graph or mutate historical runs |

Compatibility spike (2026-07-28): Evidently `0.7.21` was installed in an
isolated virtual environment. A `DataSummaryPreset` report ran on a small
DataFrame and produced a JSON-serializable snapshot with top-level `metrics`
and `tests`, matching the normalized adapter boundary in
`research_forge.external_validators`. This package remains optional and is not
added to the core dependency set.

## Not embedded in the core

[Deepchecks](https://github.com/deepchecks/deepchecks) has useful dataset,
model, leakage, and weak-segment checks, but its current licensing is
AGPL-oriented. Research Forge should support it only through a separately
installed, out-of-process adapter after a licensing review. Its checks overlap
substantially with Evidently and Great Expectations, so it is not required for
the first integration.

Experiment trackers such as Aim, ClearML, MLflow, and SwanLab are useful run
views but do not solve the reviewer findings that motivated this work:
unreachable thresholds, construct validity, train/evaluation circularity,
component identification, or manuscript narrative quality. They may export
telemetry into Stage 3, but must not become scientific authority.

## Integration order

1. Great Expectations adapter for Stage 2 MVP and Stage 3 data admission.
2. Evidently adapter for Stage 3 diagnostic evidence.
3. Manubot citation metadata check in Stage 4.
4. Optional statcheck subprocess adapter.

Every adapter must return a normalized record containing:

- tool name and version;
- frozen input artifact IDs and hashes;
- frozen configuration hash;
- execution status;
- normalized findings with severity;
- raw output artifact ID;
- explicit authority statement (`diagnostic_only`);
- deterministic mapping into a Research Forge validity finding.

Provider installation failure must produce `blocked_optional_dependency` or
`skipped_not_applicable`, never a successful scientific check.
