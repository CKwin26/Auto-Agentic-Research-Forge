"""Built-in Profile catalog and migration bindings for current experiments."""

from __future__ import annotations

from collections.abc import Callable

from ..workflow_domain import Stage3Profile
from .benchmark_prediction import validate_benchmark_prediction_contract
from .contracts import (
    BenchmarkPredictionParameters,
    DeterministicSimulationParameters,
    ExistingPythonProjectParameters,
    LLMEvaluationParameters,
    PairedBinaryClusteredParameters,
    PairedBinaryIndependentParameters,
    PairedContinuousV2Parameters,
    PairedMultiArmParameters,
    TabularMLParameters,
    TimeSeriesBacktestParameters,
)
from .existing_python_project import validate_existing_python_project_contract
from .time_series_backtest import validate_time_series_backtest_contract
from .sdk import (
    ExperimentProfileDescriptor,
    ExperimentProfileRuntime,
    OperationReadiness,
    ProfileAutomationMode,
    ProfileFamily,
    ProfileMaturity,
    ProfileOperation,
    operation_matrix,
)
from .validation import (
    validate_legacy_paired_contract,
    validate_modern_paired_contract,
    validate_multi_arm_contract,
    validate_tabular_ml_contract,
)


_ALL_INTEGRATED = operation_matrix(OperationReadiness.INTEGRATION_TESTED)
_ALL_CERTIFIED = operation_matrix(OperationReadiness.CERTIFIED)


def _descriptor(
    profile_id: str,
    version: str,
    family: ProfileFamily,
    *,
    title: str,
    summary: str,
    maturity: ProfileMaturity,
    automation_mode: ProfileAutomationMode,
    operations: dict[ProfileOperation, OperationReadiness],
    resources: tuple[str, ...],
    outputs: tuple[str, ...],
    boundary: str,
    references: tuple[str, ...] = (),
    gates: tuple[str, ...] = (),
) -> ExperimentProfileDescriptor:
    return ExperimentProfileDescriptor(
        profile_id=profile_id,
        profile_version=version,
        family=family,
        title=title,
        summary=summary,
        maturity=maturity,
        automation_mode=automation_mode,
        operations=operations,
        required_resource_roles=resources,
        produced_artifact_roles=outputs,
        automation_boundary=boundary,
        reference_patterns=references,
        ethics_or_safety_gates=gates,
    )


BUILTIN_DESCRIPTORS: dict[str, ExperimentProfileDescriptor] = {
    Stage3Profile.LLM_EVALUATION_V1.value: _descriptor(
        Stage3Profile.LLM_EVALUATION_V1.value,
        "1.0.0",
        ProfileFamily.LLM_EVALUATION,
        title="Frozen LLM response evaluation",
        summary=(
            "Target-isolated deterministic evaluation of paired frozen responses."
        ),
        maturity=ProfileMaturity.C2_DRY_RUN,
        automation_mode=ProfileAutomationMode.AUTOMATED,
        operations=operation_matrix(OperationReadiness.INTEGRATION_TESTED),
        resources=(
            "frozen_responses",
            "evaluator_only_references",
            "task_manifest",
        ),
        outputs=("task_scores", "paired_score_effect"),
        boundary=(
            "Controlled paired MCQ execution supports a versioned backend, exact "
            "choice scoring, McNemar analysis and paired bootstrap inference. "
            "Open-ended generation and LLM-as-judge remain outside this Profile."
        ),
        references=(
            "https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/task_guide.md",
        ),
    ),
    Stage3Profile.TIME_SERIES_BACKTEST_V1.value: _descriptor(
        Stage3Profile.TIME_SERIES_BACKTEST_V1.value,
        "1.0.0",
        ProfileFamily.TIME_SERIES_BACKTEST,
        title="Point-in-time time-series backtest",
        summary=(
            "Target-isolated comparison of frozen cross-sectional ranking signals."
        ),
        # Component tests, isolated smoke execution and RunPlan compilation
        # are C2 evidence.  C3 is awarded only by a complete acceptance report.
        maturity=ProfileMaturity.C2_DRY_RUN,
        automation_mode=ProfileAutomationMode.AUTOMATED,
        # The generic Stage 3 adjudicator applies the frozen effect direction
        # and threshold after the domain evaluator emits paired period rows.
        # This is a connected lifecycle operation even though it is shared by
        # several Profiles rather than implemented in this module.
        operations=operation_matrix(OperationReadiness.INTEGRATION_TESTED),
        resources=(
            "point_in_time_signals",
            "evaluator_only_returns",
            "universe_policy",
            "cost_model",
            "calendar",
        ),
        outputs=("period_portfolios", "net_returns", "paired_effect"),
        boundary=(
            "Formal support begins with already-authoritative point-in-time "
            "signals and evaluator-only realized returns. Raw feed ingestion, "
            "calendar construction and corporate-action adjustment are not certified."
        ),
        references=("https://mlcommons.org/benchmarks/training/",),
    ),
    Stage3Profile.TABULAR_ML_V1.value: _descriptor(
        Stage3Profile.TABULAR_ML_V1.value,
        "1.0.0",
        ProfileFamily.TABULAR_SUPERVISED,
        title="Frozen tabular supervised comparison",
        summary="Paired baseline/treatment evaluation on numeric tabular data.",
        maturity=ProfileMaturity.C3_REAL_FIXTURE,
        automation_mode=ProfileAutomationMode.AUTOMATED,
        operations=_ALL_CERTIFIED,
        resources=("dataset", "split", "baseline_estimator", "treatment_estimator"),
        outputs=("predictions", "metrics", "analysis_rows", "verdict"),
        boundary=(
            "Numeric CSV classification/regression with frozen target isolation; "
            "it does not imply image, sequence, causal, or human-study support."
        ),
        references=("https://mlcommons.org/benchmarks/training/",),
    ),
    Stage3Profile.BENCHMARK_PREDICTION_V1.value: _descriptor(
        Stage3Profile.BENCHMARK_PREDICTION_V1.value,
        "1.0.0",
        ProfileFamily.BENCHMARK_PREDICTION,
        title="Target-isolated benchmark prediction",
        summary="Candidate submission evaluated against evaluator-only targets.",
        maturity=ProfileMaturity.C2_DRY_RUN,
        automation_mode=ProfileAutomationMode.AUTOMATED,
        operations=_ALL_INTEGRATED,
        resources=("candidate_input", "hidden_targets", "evaluator"),
        outputs=("submission", "metrics", "verdict"),
        boundary="Requires explicit candidate/evaluator isolation.",
    ),
    Stage3Profile.EXISTING_PYTHON_PROJECT_V1.value: _descriptor(
        Stage3Profile.EXISTING_PYTHON_PROJECT_V1.value,
        "1.0.0",
        ProfileFamily.EXISTING_COMPUTATIONAL_PROJECT,
        title="Existing Python project replay",
        summary="Replays explicit two-arm commands in a frozen environment.",
        maturity=ProfileMaturity.C2_DRY_RUN,
        automation_mode=ProfileAutomationMode.AUTOMATED,
        operations=_ALL_INTEGRATED,
        resources=("code_snapshot", "requirements_lock", "container", "evaluator"),
        outputs=("run_outputs", "metrics", "replay_record", "verdict"),
        boundary="Only explicit Python command and result-schema adapters are supported.",
    ),
    Stage3Profile.DETERMINISTIC_SIMULATION_V1.value: _descriptor(
        Stage3Profile.DETERMINISTIC_SIMULATION_V1.value,
        "0.1.0",
        ProfileFamily.DETERMINISTIC_SIMULATION,
        title="Deterministic known-effect simulation",
        summary="Common-random-number fixture for testing scientific machinery.",
        maturity=ProfileMaturity.C2_DRY_RUN,
        automation_mode=ProfileAutomationMode.AUTOMATED,
        operations=operation_matrix(
            OperationReadiness.COMPONENT_TESTED,
            overrides={
                ProfileOperation.COMPILE_RUN_DAG: OperationReadiness.NOT_IMPLEMENTED,
                ProfileOperation.BIND_EVIDENCE: OperationReadiness.NOT_IMPLEMENTED,
            },
        ),
        resources=("simulation_parameters", "seed_policy"),
        outputs=("paired_rows", "known_effect_evaluation"),
        boundary="Development fixture; formal Stage 3 execution remains blocked.",
        references=("https://fmi-standard.org/docs/main/",),
    ),
}


def _paired_descriptor(profile: Stage3Profile, title: str) -> None:
    BUILTIN_DESCRIPTORS[profile.value] = _descriptor(
        profile.value,
        "1.0.0" if profile.value.endswith("v1") else "2.0.0",
        ProfileFamily.PAIRED_COMPUTATIONAL,
        title=title,
        summary="Frozen paired computational comparison with explicit inference.",
        maturity=ProfileMaturity.C2_DRY_RUN,
        automation_mode=ProfileAutomationMode.AUTOMATED,
        operations=_ALL_INTEGRATED,
        resources=("paired_inputs", "baseline_action", "treatment_action", "evaluator"),
        outputs=("paired_analysis_rows", "uncertainty", "verdict"),
        boundary="Supports only the registered pairing and variance semantics.",
    )


_paired_descriptor(
    Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
    "Legacy paired computational comparison",
)
_paired_descriptor(
    Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2,
    "Cluster-aware paired computational comparison",
)
_paired_descriptor(
    Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1,
    "Independent paired binary comparison",
)
_paired_descriptor(
    Stage3Profile.PAIRED_BINARY_CLUSTERED_V1,
    "Clustered paired binary comparison",
)
_paired_descriptor(
    Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1,
    "Paired multi-arm ablation",
)


def _planned(
    profile_id: str,
    family: ProfileFamily,
    title: str,
    boundary: str,
    *,
    resources: tuple[str, ...],
    references: tuple[str, ...],
    mode: ProfileAutomationMode = ProfileAutomationMode.AUTOMATED,
    gates: tuple[str, ...] = (),
) -> ExperimentProfileDescriptor:
    return _descriptor(
        profile_id,
        "0.0.0",
        family,
        title=title,
        summary="Planned Profile template; no formal executor is registered.",
        maturity=ProfileMaturity.C0_DESCRIBED,
        automation_mode=mode,
        operations=operation_matrix(OperationReadiness.NOT_IMPLEMENTED),
        resources=resources,
        outputs=("design_package",),
        boundary=boundary,
        references=references,
        gates=gates,
    )


PLANNED_DESCRIPTORS: dict[str, ExperimentProfileDescriptor] = {
    item.profile_id: item
    for item in (
        _planned(
            "image_supervised_v1",
            ProfileFamily.IMAGE_SUPERVISED,
            "Frozen image supervised comparison",
            "Image decoding, transforms, split and label semantics require a dedicated adapter.",
            resources=("image_manifest", "labels", "transforms", "model_backend"),
            references=("https://mlcommons.org/benchmarks/training/",),
        ),
        _planned(
            "rl_environment_v1",
            ProfileFamily.RL_ENVIRONMENT,
            "Versioned reinforcement-learning environment",
            "Observation, action, reset, step, reward and termination semantics must be executable.",
            resources=("environment", "agent", "scenario_suite", "seed_policy"),
            references=(
                "https://gymnasium.farama.org/main/introduction/create_custom_env/",
            ),
        ),
        _planned(
            "engineering_simulation_v1",
            ProfileFamily.ENGINEERING_SIMULATION,
            "Engineering model exchange and co-simulation",
            "Requires a versioned FMI or domain-native simulation adapter.",
            resources=("model", "solver", "initial_conditions", "scenario_suite"),
            references=("https://fmi-standard.org/docs/main/",),
        ),
        _planned(
            "bioinformatics_pipeline_v1",
            ProfileFamily.BIOINFORMATICS_PIPELINE,
            "Containerized bioinformatics pipeline",
            "Requires samplesheet, reference data, module versions, containers and QC thresholds.",
            resources=("samplesheet", "reference_data", "pipeline", "containers"),
            references=("https://nf-co.re/docs/developing/overview",),
        ),
        _planned(
            "causal_observational_v1",
            ProfileFamily.CAUSAL_OBSERVATIONAL,
            "Target-comparator-outcome observational study",
            "Causal authority requires an estimand, time zero, adjustment strategy and diagnostics; association alone is insufficient.",
            resources=("cohort_data", "target", "comparator", "outcome", "covariates"),
            references=("https://ohdsi.github.io/CohortMethod/",),
            mode=ProfileAutomationMode.ASSISTED,
        ),
        _planned(
            "human_behavior_v1",
            ProfileFamily.HUMAN_BEHAVIOR,
            "Human behavioral experiment",
            "Research Forge may build and analyze a study package but cannot replace consent, recruitment or ethics approval.",
            resources=("timeline", "stimuli", "randomization", "participant_data"),
            references=("https://www.jspsych.org/v8/overview/plugins/",),
            mode=ProfileAutomationMode.DESIGN_AND_IMPORT_ONLY,
            gates=("ethics_approval", "informed_consent", "privacy_review"),
        ),
        _planned(
            "wet_lab_protocol_v1",
            ProfileFamily.WET_LAB_PROTOCOL,
            "Typed wet-lab protocol",
            "Without a validated robot/LIMS adapter and operator approval, only protocol export and result import are allowed.",
            resources=("materials", "containers", "instructions", "instruments"),
            references=("https://autoprotocol.org/specification/",),
            mode=ProfileAutomationMode.DESIGN_AND_IMPORT_ONLY,
            gates=("operator_approval", "biosafety_review", "instrument_validation"),
        ),
    )
}


def build_runtime_registry(
    bundles: dict[Stage3Profile, object],
) -> dict[Stage3Profile, ExperimentProfileRuntime]:
    from .base import ExperimentProfileBundle

    parameter_models = {
        Stage3Profile.TABULAR_ML_V1: TabularMLParameters,
        Stage3Profile.BENCHMARK_PREDICTION_V1: BenchmarkPredictionParameters,
        Stage3Profile.EXISTING_PYTHON_PROJECT_V1: ExistingPythonProjectParameters,
        Stage3Profile.DETERMINISTIC_SIMULATION_V1: DeterministicSimulationParameters,
        Stage3Profile.TIME_SERIES_BACKTEST_V1: TimeSeriesBacktestParameters,
        Stage3Profile.LLM_EVALUATION_V1: LLMEvaluationParameters,
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2: PairedContinuousV2Parameters,
        Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1: PairedBinaryIndependentParameters,
        Stage3Profile.PAIRED_BINARY_CLUSTERED_V1: PairedBinaryClusteredParameters,
        Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1: PairedMultiArmParameters,
    }
    validators: dict[Stage3Profile, Callable] = {
        Stage3Profile.TABULAR_ML_V1: validate_tabular_ml_contract,
        Stage3Profile.BENCHMARK_PREDICTION_V1: validate_benchmark_prediction_contract,
        Stage3Profile.EXISTING_PYTHON_PROJECT_V1: validate_existing_python_project_contract,
        Stage3Profile.TIME_SERIES_BACKTEST_V1: validate_time_series_backtest_contract,
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1: validate_legacy_paired_contract,
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2: validate_modern_paired_contract,
        Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1: validate_modern_paired_contract,
        Stage3Profile.PAIRED_BINARY_CLUSTERED_V1: validate_modern_paired_contract,
        Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1: validate_multi_arm_contract,
    }
    runtimes: dict[Stage3Profile, ExperimentProfileRuntime] = {}
    for profile, raw_bundle in bundles.items():
        if not isinstance(raw_bundle, ExperimentProfileBundle):
            raise TypeError("runtime registry requires ExperimentProfileBundle")
        descriptor = BUILTIN_DESCRIPTORS.get(profile.value)
        if descriptor is None:
            raise RuntimeError(f"Profile descriptor missing for {profile.value}")
        if raw_bundle.profile_version != descriptor.profile_version:
            raise RuntimeError(
                f"Profile version mismatch for {profile.value}: bundle "
                f"{raw_bundle.profile_version}, descriptor "
                f"{descriptor.profile_version}"
            )
        runtimes[profile] = ExperimentProfileRuntime(
            bundle=raw_bundle,
            descriptor=descriptor,
            parameter_model=parameter_models.get(profile),
            contract_validator=validators.get(profile),
        )
    return runtimes


def complete_profile_catalog() -> dict[str, ExperimentProfileDescriptor]:
    return {**BUILTIN_DESCRIPTORS, **PLANNED_DESCRIPTORS}


__all__ = [
    "BUILTIN_DESCRIPTORS",
    "PLANNED_DESCRIPTORS",
    "build_runtime_registry",
    "complete_profile_catalog",
]
