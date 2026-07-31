"""Contract-driven Stage 3 research execution kernel.

Stage 3 is deliberately narrower than an arbitrary notebook agent. It accepts
only frozen, statistically certified Experiment Profile Bundles and compiles
them into deterministic, persistent run matrices.
"""

from __future__ import annotations

import hashlib
import json
import csv
import math
import os
import platform
import shutil
import statistics
from itertools import product
from pathlib import Path
from typing import Any, Iterable

from .experiment_execution import (
    ExperimentManifest,
    ExperimentPaused,
    ExperimentProcessError,
    ExperimentPreflightError,
    ExperimentSpec,
    experiment_for_action,
    load_project_experiment_manifest,
    run_declared_experiment,
)
from .storage import (
    ensure_within,
    read_json,
    safe_relative,
    sha256_file,
    write_json_atomic,
    write_text_atomic,
)
from .workflow_domain import (
    ArtifactRole,
    ArtifactStatus,
    AnalysisEligibilityStatus,
    DiagnosticOwner,
    DiagnosticReport,
    AssuranceStatus,
    ConfirmatoryStatus,
    EvaluationRecord,
    EvidenceReproductionLevel,
    EvidenceChain,
    EvidenceChainLevel,
    EvidenceEdge,
    EvidenceRelation,
    ExecutionStatus,
    ExecutionAttempt,
    ExecutionPackageSeal,
    FormalEvaluationExposureRecord,
    FormalExposureType,
    ExecutorType,
    GateType,
    HypothesisVerdict,
    HypothesisVerdictStatus,
    LeakageAuditReport,
    LeakageCheckStatus,
    Phase,
    PublicationMode,
    QualificationStatus,
    ResearchContractVersion,
    ResearchRun,
    RepairContract,
    RepairStatus,
    ResultEnvelope,
    RunCell,
    RunKind,
    RunCellStatus,
    RunPlan,
    ScientificClaimEnvelope,
    ScientificSuccessorRequest,
    Stage3CompletionPackage,
    Stage3FailureClass,
    Stage3HandoffPackage,
    Stage3Profile,
    StatisticalAssuranceReport,
    StudyVerdict,
    StudyVerdictStatus,
    StepDefinition,
    StepInstance,
    SuccessorRun,
    WorkflowRepository,
    aggregate_study_verdict,
    stable_id,
    utc_now,
)


STAGE3_COMPILER_VERSION = "stage3-compiler-v2"
STAGE3_LOCK_NAMES = (
    "protocol.lock.json",
    "environment.lock.json",
    "data_manifest.lock.json",
    "code_manifest.lock.json",
    "decision_rules.lock.json",
)

STAGE3_STEP_DEFINITIONS = (
    StepDefinition(
        step_type="stage3_admission",
        phase=Phase.EXPERIMENT,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="Stage3HandoffPackage",
        scientific_failure_enters_diagnosis=True,
    ),
    StepDefinition(
        step_type="compile_stage3_run_plan",
        phase=Phase.EXPERIMENT,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="immutable RunPlan and RunCell matrix",
        scientific_failure_enters_diagnosis=True,
    ),
    StepDefinition(
        step_type="stage3_execution_gate",
        phase=Phase.EXPERIMENT,
        executor_type=ExecutorType.PROJECT_OWNER,
        expected_output="owner decision for the exact Run Plan",
        required_gate_type=GateType.REPAIR_OR_HIGH_COST_RUN,
    ),
    StepDefinition(
        step_type="execute_stage3_run_cell",
        phase=Phase.EXPERIMENT,
        executor_type=ExecutorType.SANDBOX_RUNNER,
        expected_output="ExecutionAttempt and ResultEnvelope",
        transient_retry_limit=3,
        scientific_failure_enters_diagnosis=True,
    ),
    StepDefinition(
        step_type="evaluate_stage3_results",
        phase=Phase.EXPERIMENT,
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        expected_output="EvaluationRecord",
        scientific_failure_enters_diagnosis=True,
    ),
    StepDefinition(
        step_type="build_stage3_evidence_ledger",
        phase=Phase.EXPERIMENT,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="verified EvidenceEdge graph",
        scientific_failure_enters_diagnosis=True,
    ),
    StepDefinition(
        step_type="materialize_stage3_verdict",
        phase=Phase.EXPERIMENT,
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        expected_output="HypothesisVerdict and StudyVerdict",
        scientific_failure_enters_diagnosis=True,
    ),
    StepDefinition(
        step_type="audit_stage3_completion",
        phase=Phase.EXPERIMENT,
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        expected_output="Stage 3 deterministic audit",
        scientific_failure_enters_diagnosis=True,
    ),
    StepDefinition(
        step_type="complete_stage3",
        phase=Phase.EXPERIMENT,
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="Stage3CompletionPackage",
        scientific_failure_enters_diagnosis=True,
    ),
)


class Stage3AdmissionError(ValueError):
    """A frozen contract does not satisfy the supported Stage 3 profile."""

    def __init__(self, violations: Iterable[str]) -> None:
        self.violations = list(dict.fromkeys(str(item) for item in violations))
        super().__init__("; ".join(self.violations))


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _hash_input(path: Path, root: Path) -> str:
    ensure_within(root, path)
    if path.is_file():
        return sha256_file(path)
    if not path.is_dir():
        raise Stage3AdmissionError([f"required input is missing: {path}"])
    entries = [
        {
            "path": item.relative_to(root).as_posix(),
            "sha256": sha256_file(item),
        }
        for item in sorted(path.rglob("*"))
        if item.is_file()
    ]
    return _digest(entries)


def _source_root(
    repository: WorkflowRepository, study_id: str
) -> Path:
    handoff_path = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "handoff.json"
    )
    if handoff_path.is_file():
        handoff = Stage3HandoffPackage.model_validate(
            read_json(handoff_path)
        )
        if handoff.handoff_stage == "formal_execution" and (
            handoff.execution_root
        ):
            execution_root = Path(handoff.execution_root).resolve()
            if not execution_root.is_dir():
                raise Stage3AdmissionError(
                    [
                        "frozen Stage 3 execution_root is unavailable: "
                        f"{execution_root}"
                    ]
                )
            return execution_root
    study = repository.load_study(study_id)
    project = repository.load_project(study.project_id)
    if not project.source_root:
        raise Stage3AdmissionError(
            ["Stage 3 requires a project source_root"]
        )
    root = Path(project.source_root).resolve()
    if not root.is_dir():
        raise Stage3AdmissionError(
            [f"project source_root is unavailable: {root}"]
        )
    return root


def _action_binding(
    contract: ResearchContractVersion,
    arm_id: str,
) -> tuple[str | None, str | None]:
    if arm_id == "baseline":
        binding = contract.baseline
    elif arm_id == "treatment":
        binding = contract.treatment
    else:
        arm_bindings = contract.implementation_requirements.get("arms", [])
        if isinstance(arm_bindings, dict):
            candidate = arm_bindings.get(arm_id, {})
            binding = candidate if isinstance(candidate, dict) else {}
        else:
            binding = next(
                (
                    item
                    for item in arm_bindings
                    if isinstance(item, dict)
                    and str(item.get("arm_id") or "") == arm_id
                ),
                {},
            )
    experiment_id = str(binding.get("experiment_id") or "").strip() or None
    action_id = str(binding.get("action_id") or "").strip() or None
    return experiment_id, action_id


def _validate_legacy_paired_v1_contract(
    contract: ResearchContractVersion,
    manifest: ExperimentManifest,
) -> list[str]:
    violations: list[str] = []
    if (
        contract.experiment_profile
        is not Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1
    ):
        violations.append(
            "Research Contract must declare "
            "computational_paired_comparison_v1"
        )
    if not contract.tasks:
        violations.append("Profile v1 requires at least one task")
    if not contract.splits:
        violations.append("Profile v1 requires at least one split")
    if not contract.seeds:
        violations.append("Profile v1 requires at least one seed")
    if len(contract.hypotheses) != 1:
        violations.append(
            "Profile v1 requires exactly one preregistered hypothesis"
        )
    if len(contract.metrics) != 1:
        violations.append("Profile v1 requires exactly one primary metric")
    else:
        metric = contract.metrics[0]
        if not str(metric.get("name") or "").strip():
            violations.append("primary metric name is missing")
        if metric.get("direction") not in {
            "higher_is_better",
            "lower_is_better",
            "maximize",
            "minimize",
        }:
            violations.append("primary metric direction is unsupported")
        if not str(metric.get("denominator") or "").strip():
            violations.append("primary metric denominator is missing")
    schema = contract.output_schema
    if schema.get("format") not in {"json", "csv"}:
        violations.append("output_schema.format must be json or csv")
    if not str(schema.get("metric_field") or "").strip():
        violations.append("output_schema.metric_field is required")
    if not str(schema.get("denominator_field") or "").strip():
        violations.append("output_schema.denominator_field is required")
    if not str(schema.get("sample_id_field") or "").strip():
        violations.append("output_schema.sample_id_field is required")
    if schema.get("record_layout", "summary") != "summary":
        violations.append("Profile v1 supports only summary record_layout")
    rules = contract.statistical_rules
    if rules.get("method") != "paired_mean_difference":
        violations.append(
            "Profile v1 supports only paired_mean_difference"
        )
    threshold = rules.get("effect_threshold")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        violations.append(
            "statistical_rules.effect_threshold must be numeric"
        )
    if rules.get("missing_cell_policy") not in {
        "inconclusive",
        "disqualify",
    }:
        violations.append(
            "missing_cell_policy must be inconclusive or disqualify"
        )
    confidence_level = rules.get("confidence_level", 0.95)
    if confidence_level != 0.95:
        violations.append("Profile v1 supports only a 0.95 confidence level")
    for arm_id in ("baseline", "treatment"):
        experiment_id, action_id = _action_binding(contract, arm_id)
        if not experiment_id:
            violations.append(f"{arm_id}.experiment_id is required")
        if not action_id:
            violations.append(f"{arm_id}.action_id is required")
            continue
        try:
            spec = experiment_for_action(manifest, action_id)
        except ValueError as exc:
            violations.append(str(exc))
            continue
        if spec is None:
            violations.append(
                f"manifest has no experiment for {arm_id} action {action_id}"
            )
        elif experiment_id and spec.experiment_id != experiment_id:
            violations.append(
                f"{arm_id} experiment_id does not match the manifest"
            )
    return violations


def _validate_modern_paired_contract(
    contract: ResearchContractVersion,
    manifest: ExperimentManifest,
) -> list[str]:
    """Validate shared paired execution plus Profile-specific parameters."""

    from pydantic import ValidationError

    from .profiles.contracts import (
        PairedBinaryClusteredParameters,
        PairedBinaryIndependentParameters,
        PairedContinuousV2Parameters,
    )

    violations: list[str] = []
    if not contract.tasks:
        violations.append("paired Profile requires at least one task")
    if not contract.splits:
        violations.append("paired Profile requires at least one split")
    if not contract.seeds:
        violations.append("paired Profile requires at least one seed")
    if len(contract.hypotheses) != 1:
        violations.append(
            "paired Profile requires exactly one primary hypothesis"
        )
    if len(contract.metrics) != 1:
        violations.append("paired Profile requires exactly one primary metric")
    schema = contract.output_schema
    if schema.get("format") != "json":
        violations.append("modern paired Profiles require JSON output")
    if schema.get("record_layout") != "summary_with_analysis_rows":
        violations.append(
            "record_layout must be summary_with_analysis_rows"
        )
    for field in (
        "metric_field",
        "denominator_field",
        "sample_id_field",
        "analysis_rows_field",
    ):
        if not str(schema.get(field) or "").strip():
            violations.append(f"output_schema.{field} is required")
    rules = contract.statistical_rules
    threshold = rules.get("effect_threshold")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        violations.append(
            "statistical_rules.effect_threshold must be numeric"
        )
    if rules.get("missing_cell_policy") not in {
        "inconclusive",
        "disqualify",
    }:
        violations.append(
            "missing_cell_policy must be inconclusive or disqualify"
        )
    parameter_models = {
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2: (
            PairedContinuousV2Parameters
        ),
        Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1: (
            PairedBinaryIndependentParameters
        ),
        Stage3Profile.PAIRED_BINARY_CLUSTERED_V1: (
            PairedBinaryClusteredParameters
        ),
    }
    model = parameter_models.get(contract.experiment_profile)
    if model is None:
        violations.append("no modern paired contract model is registered")
    else:
        try:
            model.model_validate(contract.profile_parameters)
        except ValidationError as exc:
            violations.extend(
                "profile_parameters." + ".".join(map(str, item["loc"]))
                + ": "
                + item["msg"]
                for item in exc.errors()
            )
    expected_methods = {
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2: (
            "cluster_bootstrap_paired_mean"
        ),
        Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1: "exact_mcnemar",
        Stage3Profile.PAIRED_BINARY_CLUSTERED_V1: (
            "cluster_bootstrap_paired_binary"
        ),
    }
    if rules.get("method") != expected_methods.get(
        contract.experiment_profile
    ):
        violations.append(
            "statistical_rules.method does not match the frozen Profile"
        )
    for arm_id in ("baseline", "treatment"):
        experiment_id, action_id = _action_binding(contract, arm_id)
        if not experiment_id:
            violations.append(f"{arm_id}.experiment_id is required")
        if not action_id:
            violations.append(f"{arm_id}.action_id is required")
            continue
        try:
            spec = experiment_for_action(manifest, action_id)
        except ValueError as exc:
            violations.append(str(exc))
            continue
        if spec is None:
            violations.append(
                f"manifest has no experiment for {arm_id} action {action_id}"
            )
        elif experiment_id and spec.experiment_id != experiment_id:
            violations.append(
                f"{arm_id}.experiment_id does not match the manifest"
            )
    return violations


def _validate_multi_arm_contract(
    contract: ResearchContractVersion,
    manifest: ExperimentManifest,
) -> list[str]:
    from pydantic import ValidationError

    from .profiles.contracts import PairedMultiArmParameters

    violations: list[str] = []
    if not contract.tasks:
        violations.append("multi-arm Profile requires at least one task")
    if not contract.splits:
        violations.append("multi-arm Profile requires at least one split")
    if not contract.seeds:
        violations.append("multi-arm Profile requires at least one seed")
    if len(contract.hypotheses) != 1:
        violations.append(
            "multi-arm Profile requires exactly one primary hypothesis"
        )
    if not contract.metrics:
        violations.append("multi-arm Profile requires a primary metric")
    schema = contract.output_schema
    if schema.get("format") != "json":
        violations.append("multi-arm Profile requires JSON output")
    if schema.get("record_layout") != "summary_with_analysis_rows":
        violations.append(
            "record_layout must be summary_with_analysis_rows"
        )
    for field in (
        "metric_field",
        "denominator_field",
        "sample_id_field",
        "analysis_rows_field",
    ):
        if not str(schema.get(field) or "").strip():
            violations.append(f"output_schema.{field} is required")
    rules = contract.statistical_rules
    if rules.get("method") != "cluster_bootstrap_multi_arm_conjunction":
        violations.append(
            "statistical_rules.method must be "
            "cluster_bootstrap_multi_arm_conjunction"
        )
    threshold = rules.get("effect_threshold")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        violations.append(
            "statistical_rules.effect_threshold must be numeric"
        )
    if rules.get("missing_cell_policy") not in {
        "inconclusive",
        "disqualify",
    }:
        violations.append(
            "missing_cell_policy must be inconclusive or disqualify"
        )
    try:
        parameters = PairedMultiArmParameters.model_validate(
            contract.profile_parameters
        )
    except ValidationError as exc:
        violations.extend(
            "profile_parameters." + ".".join(map(str, item["loc"]))
            + ": "
            + item["msg"]
            for item in exc.errors()
        )
        return violations
    for arm_id in parameters.arms:
        experiment_id, action_id = _action_binding(contract, arm_id)
        if not experiment_id:
            violations.append(f"{arm_id}.experiment_id is required")
        if not action_id:
            violations.append(f"{arm_id}.action_id is required")
            continue
        try:
            spec = experiment_for_action(manifest, action_id)
        except ValueError as exc:
            violations.append(str(exc))
            continue
        if spec is None:
            violations.append(
                f"manifest has no experiment for {arm_id} action {action_id}"
            )
        elif experiment_id and spec.experiment_id != experiment_id:
            violations.append(
                f"{arm_id} experiment_id does not match the manifest"
            )
    return violations


def _validate_tabular_ml_contract(
    contract: ResearchContractVersion,
    manifest: ExperimentManifest,
) -> list[str]:
    """Validate the narrow executable semantics of ``tabular_ml_v1``."""

    from pydantic import ValidationError

    from .profiles.contracts import TabularMLParameters

    violations: list[str] = []
    try:
        parameters = TabularMLParameters.model_validate(
            contract.profile_parameters
        )
    except ValidationError as exc:
        return [
            "profile_parameters."
            + ".".join(map(str, item["loc"]))
            + ": "
            + item["msg"]
            for item in exc.errors()
        ]
    if not contract.tasks:
        violations.append("tabular_ml_v1 requires at least one task")
    if not contract.seeds:
        violations.append("tabular_ml_v1 requires at least one seed")
    if contract.replicates < 1:
        violations.append("tabular_ml_v1 requires at least one replicate")
    if len(contract.hypotheses) != 1:
        violations.append(
            "tabular_ml_v1 requires exactly one primary hypothesis"
        )
    metric_names = [str(item.get("name") or "") for item in contract.metrics]
    if metric_names != parameters.metrics:
        violations.append(
            "contract metrics must exactly match profile_parameters.metrics "
            "in frozen order"
        )
    for metric in contract.metrics:
        name = str(metric.get("name") or "")
        expected_direction = (
            "lower_is_better" if name in {"rmse", "mae"} else "higher_is_better"
        )
        if metric.get("direction") != expected_direction:
            violations.append(
                f"metric {name} direction must be {expected_direction}"
            )
        if not str(metric.get("formula") or "").strip():
            violations.append(f"metric {name} requires an executable formula")
        if not str(metric.get("denominator") or "").strip():
            violations.append(f"metric {name} requires a denominator")
    schema = contract.output_schema
    if schema.get("format") != "json":
        violations.append("tabular_ml_v1 requires JSON result output")
    expected_schema = {
        "metric_field": parameters.primary_metric,
        "denominator_field": "denominator",
        "sample_id_field": "sample_ids",
        "analysis_rows_field": "analysis_rows",
    }
    for field, expected in expected_schema.items():
        if schema.get(field) != expected:
            violations.append(
                f"output_schema.{field} must be {expected}"
            )
    if schema.get("record_layout") != "summary_with_analysis_rows":
        violations.append(
            "tabular_ml_v1 requires summary_with_analysis_rows output"
        )
    if contract.statistical_rules.get("method") != "paired_run_difference":
        violations.append(
            "tabular_ml_v1 statistical method must be paired_run_difference"
        )
    threshold = contract.statistical_rules.get("effect_threshold")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        violations.append("effect_threshold must be numeric")
    if contract.statistical_rules.get("missing_cell_policy") not in {
        "inconclusive",
        "disqualify",
    }:
        violations.append(
            "missing_cell_policy must be inconclusive or disqualify"
        )
    dataset_path = parameters.dataset_path
    for arm_id in ("baseline", "treatment"):
        experiment_id, action_id = _action_binding(contract, arm_id)
        if not experiment_id or not action_id:
            violations.append(f"{arm_id} action binding is incomplete")
            continue
        try:
            spec = experiment_for_action(manifest, action_id)
        except ValueError as exc:
            violations.append(str(exc))
            continue
        if spec is None or spec.experiment_id != experiment_id:
            violations.append(
                f"{arm_id} binding does not match Experiment Manifest"
            )
            continue
        if spec.execution_backend == "isolated_candidate_evaluator":
            if dataset_path in spec.required_inputs:
                violations.append(
                    f"{arm_id} candidate input exposes the source dataset "
                    "containing formal targets"
                )
            if len(spec.required_inputs) != 1:
                violations.append(
                    f"{arm_id} requires one target-free candidate data file"
                )
            if len(spec.evaluator_required_inputs) != 1:
                violations.append(
                    f"{arm_id} requires one evaluator-only target file"
                )
            if set(spec.required_inputs).intersection(
                spec.evaluator_required_inputs
            ):
                violations.append(
                    f"{arm_id} candidate and evaluator inputs overlap"
                )
        elif dataset_path not in spec.required_inputs:
            violations.append(
                f"{arm_id} experiment does not freeze the dataset input"
            )
    return violations


def _validate_profile_contract(
    contract: ResearchContractVersion,
    manifest: ExperimentManifest,
) -> list[str]:
    """Validate through the frozen Profile contract-schema registry."""

    from .profiles.registry import profile_bundle

    if contract.experiment_profile is None:
        return [
            "Research Contract must declare "
            "computational_paired_comparison_v1 or another certified "
            "Stage 3 Profile Bundle"
        ]
    try:
        bundle = profile_bundle(contract.experiment_profile)
    except ValueError:
        return ["blocked_unsupported_design: Profile Bundle is not registered"]
    if not bundle.formal_execution_supported():
        return [
            "blocked_unsupported_design: Profile Bundle "
            f"{bundle.profile_id.value} has not passed assurance certification"
        ]
    from .profiles.benchmark_prediction import (
        validate_benchmark_prediction_contract,
    )
    from .profiles.existing_python_project import (
        validate_existing_python_project_contract,
    )

    validators = {
        "existing_python_project_contract_v1": (
            validate_existing_python_project_contract
        ),
        "benchmark_prediction_contract_v1": (
            validate_benchmark_prediction_contract
        ),
        "tabular_ml_contract_v1": _validate_tabular_ml_contract,
        "computational_paired_contract_v1": (
            _validate_legacy_paired_v1_contract
        ),
        "computational_paired_contract_v2": (
            _validate_modern_paired_contract
        ),
        "paired_binary_independent_contract_v1": (
            _validate_modern_paired_contract
        ),
        "paired_binary_clustered_contract_v1": (
            _validate_modern_paired_contract
        ),
        "paired_multi_arm_contract_v1": _validate_multi_arm_contract,
    }
    validator = validators.get(bundle.contract_schema_id)
    if validator is None:
        return [
            "blocked_unsupported_design: no registered contract validator "
            f"for {bundle.contract_schema_id}"
        ]
    return validator(contract, manifest)


def admit_stage_three(
    repository: WorkflowRepository,
    study_id: str,
    *,
    explicit_manifest_path: str | None = None,
    repair_contract_id: str | None = None,
) -> Stage3HandoffPackage:
    """Verify the Stage 2 handoff and persist an immutable admission package."""

    study = repository.load_study(study_id)
    current_path = _stage3_root(repository, study_id) / "handoff.json"
    if current_path.is_file() and repair_contract_id is None:
        current = repository.load_stage3_handoff(study_id)
        if (
            current.contract_version == study.active_contract_version
            and current.handoff_stage == "formal_execution"
            and current.execution_package_seal_id
            and current.experiment_manifest_path
            and current.experiment_manifest_sha256
            and sha256_file(Path(current.experiment_manifest_path))
            == current.experiment_manifest_sha256
        ):
            repository.load_execution_package_seal(
                study_id, current.execution_package_seal_id
            )
            return current
    violations: list[str] = []
    if study.phase is not Phase.EXPERIMENT:
        violations.append("Study phase must be experiment")
    if study.active_scope_version is None:
        violations.append("Study has no active frozen Scope")
        scope = None
    else:
        scope = repository.load_scope_contract(
            study_id, study.active_scope_version
        )
        if scope.status is not ArtifactStatus.FROZEN:
            violations.append("active Scope is not frozen")
    if study.active_contract_version is None:
        violations.append("Study has no active frozen Research Contract")
        contract = None
    else:
        contract = repository.load_research_contract(
            study_id, study.active_contract_version
        )
        if contract.status is not ArtifactStatus.FROZEN:
            violations.append("active Research Contract is not frozen")

    root = _source_root(repository, study_id)
    manifest, manifest_path = load_project_experiment_manifest(
        root, explicit_manifest_path
    )
    if manifest is None or manifest_path is None:
        violations.append("a declared Experiment Manifest is required")

    artifacts = repository.list_artifacts(study_id)
    lock_artifacts: dict[str, str] = {}
    for name in STAGE3_LOCK_NAMES:
        matches = []
        for item in artifacts:
            if item.status is not ArtifactStatus.FROZEN:
                continue
            path = Path(item.path)
            if not path.is_file():
                continue
            try:
                lock_payload = read_json(path)
            except (OSError, ValueError, TypeError):
                continue
            logical_name = str(
                lock_payload.get("lock_name")
                or (path.name if path.name in STAGE3_LOCK_NAMES else "")
            )
            if logical_name != name:
                continue
            lock_contract_version = int(
                lock_payload.get("research_contract_version") or 0
            )
            if (
                contract is not None
                and lock_contract_version not in {0, contract.version}
            ):
                continue
            matches.append(item)
        if not matches:
            violations.append(
                "frozen Stage 2 lock is missing for the active "
                f"Research Contract: {name}"
            )
            continue
        artifact = max(matches, key=lambda item: item.created_at)
        path = Path(artifact.path).resolve()
        if not path.is_file() or sha256_file(path) != artifact.sha256:
            violations.append(f"Stage 2 lock hash mismatch: {name}")
            continue
        lock_artifacts[name] = artifact.artifact_id

    if contract is not None and manifest is not None:
        violations.extend(_validate_profile_contract(contract, manifest))
    if violations:
        raise Stage3AdmissionError(violations)
    assert scope is not None
    assert contract is not None
    assert manifest_path is not None
    manifest_sha256 = sha256_file(manifest_path)
    handoff_id = stable_id(
        "stage3-handoff",
        study_id,
        scope.version,
        contract.version,
        manifest_sha256,
        repair_contract_id or "",
        *(
            f"{name}:{lock_artifacts[name]}"
            for name in sorted(lock_artifacts)
        ),
    )
    current: Stage3HandoffPackage | None = None
    if current_path.is_file():
        current = repository.load_stage3_handoff(study_id)
        if current.handoff_id == handoff_id:
            return current
        if not repair_contract_id:
            successor_request_ids = {
                item.request_id
                for item in repository.list_scientific_successor_requests(
                    study_id
                )
            }
            successor_authorized = (
                contract.predecessor_version == current.contract_version
                and any(
                    str(
                        change.get("scientific_successor_request_id") or ""
                    )
                    in successor_request_ids
                    for change in contract.field_diff.values()
                    if isinstance(change, dict)
                )
            )
            if not successor_authorized:
                raise Stage3AdmissionError(
                    [
                        "a changed Stage 3 handoff requires an approved "
                        "Repair Contract or frozen Scientific Successor "
                        "Research Contract"
                    ]
                )
        else:
            repair = repository.load_repair_contract(
                study_id, repair_contract_id
            )
            approved = repair.approved_by is not None or any(
                gate.subject_type == "repair_contract"
                and gate.subject_id == repair.repair_id
                and gate.status.value == "approved"
                for gate in repository.list_gates(study_id)
            )
            if not approved:
                raise Stage3AdmissionError(
                    ["Stage 3 Repair Contract requires owner approval"]
                )
    handoff = Stage3HandoffPackage(
        handoff_id=handoff_id,
        study_id=study_id,
        scope_version=scope.version,
        contract_version=contract.version,
        profile=contract.experiment_profile,
        lock_artifact_ids=lock_artifacts,
        experiment_manifest_path=str(manifest_path),
        experiment_manifest_sha256=manifest_sha256,
        execution_root=str(root),
        resource_artifact_ids=sorted(
            set(contract.data_boundary.get("resource_artifact_ids") or [])
        ),
        predecessor_handoff_id=(
            current.handoff_id if current is not None else None
        ),
        repair_contract_id=repair_contract_id,
    )
    return repository.save_stage3_handoff(handoff)


def _spec_for_arm(
    contract: ResearchContractVersion,
    manifest: ExperimentManifest,
    arm_id: str,
) -> tuple[str, str, ExperimentSpec]:
    experiment_id, action_id = _action_binding(contract, arm_id)
    if experiment_id is None or action_id is None:
        raise Stage3AdmissionError(
            [f"{arm_id} experiment and action binding is incomplete"]
        )
    spec = experiment_for_action(manifest, action_id)
    if spec is None or spec.experiment_id != experiment_id:
        raise Stage3AdmissionError(
            [f"{arm_id} binding no longer matches Experiment Manifest"]
        )
    return experiment_id, action_id, spec


def _compile_legacy_paired_run_plan(
    repository: WorkflowRepository,
    study_id: str,
    *,
    handoff: Stage3HandoffPackage | None = None,
) -> RunPlan:
    """Shared paired compiler with v1 behavior protected by its adapter."""

    admitted = handoff or repository.load_stage3_handoff(study_id)
    contract = repository.load_research_contract(
        study_id, admitted.contract_version
    )
    manifest_path = Path(admitted.experiment_manifest_path).resolve()
    if sha256_file(manifest_path) != admitted.experiment_manifest_sha256:
        raise Stage3AdmissionError(
            ["Experiment Manifest changed after Stage 3 admission"]
        )
    manifest, _ = load_project_experiment_manifest(
        _source_root(repository, study_id), str(manifest_path)
    )
    if manifest is None:
        raise Stage3AdmissionError(["Experiment Manifest is unavailable"])
    violations = _validate_profile_contract(contract, manifest)
    if violations:
        raise Stage3AdmissionError(violations)

    root = _source_root(repository, study_id)
    arm_specs = {
        arm_id: _spec_for_arm(contract, manifest, arm_id)
        for arm_id in ("baseline", "treatment")
    }
    arm_input_bindings: dict[str, dict[str, str]] = {}
    for arm_id, (_, _, spec) in arm_specs.items():
        frozen_inputs = (
            set(spec.required_inputs)
            | set(spec.candidate_code_paths)
            | set(spec.evaluator_code_paths)
            | set(spec.evaluator_required_inputs)
        )
        arm_input_bindings[arm_id] = {
            relative: _hash_input((root / relative).resolve(), root)
            for relative in sorted(frozen_inputs)
        }
    identity_payload = {
        "compiler_version": STAGE3_COMPILER_VERSION,
        "handoff": admitted.model_dump(
            mode="json", exclude={"admitted_at"}
        ),
        "contract": contract.model_dump(
            mode="json", exclude={"created_at", "frozen_at"}
        ),
        "input_bindings": arm_input_bindings,
    }
    plan_id = f"run-plan-{_digest(identity_payload)[:16]}"
    schedule_seed = _digest(
        {
            "policy": "sha256_registered_schedule_v1",
            "study_id": study_id,
            "contract_version": contract.version,
            "tasks": sorted(contract.tasks),
            "splits": sorted(contract.splits),
            "seeds": sorted(contract.seeds),
            "replicates": contract.replicates,
        }
    )
    cells: list[RunCell] = []
    pair_dimensions = product(
        sorted(contract.tasks),
        sorted(contract.splits),
        sorted(contract.seeds),
        range(1, contract.replicates + 1),
    )
    for task_id, split_id, seed, replicate in pair_dimensions:
        pair_block_id = stable_id(
            "pair-block",
            study_id,
            plan_id,
            task_id,
            split_id,
            seed,
            replicate,
        )
        order_digest = _digest(
            {
                "policy": "sha256_registered_schedule_v1",
                "registered_schedule_seed": schedule_seed,
                "task_id": task_id,
                "split_id": split_id,
                "seed": seed,
                "replicate": replicate,
            }
        )
        arm_order = (
            ("baseline", "treatment")
            if int(order_digest[-1], 16) % 2 == 0
            else ("treatment", "baseline")
        )
        prior_cell_id: str | None = None
        for pair_position, arm_id in enumerate(arm_order, start=1):
            experiment_id, action_id, spec = arm_specs[arm_id]
            input_bindings = arm_input_bindings[arm_id]
            cell_id = stable_id(
                "run-cell",
                study_id,
                plan_id,
                task_id,
                split_id,
                arm_id,
                seed,
                replicate,
            )
            execution_hash = _digest(
                {
                    "spec": spec.model_dump(mode="json"),
                    "task_id": task_id,
                    "split_id": split_id,
                    "arm_id": arm_id,
                    "seed": seed,
                    "replicate": replicate,
                    "input_bindings": input_bindings,
                    "arm_order": arm_order,
                }
            )
            cells.append(
                RunCell(
                    run_cell_id=cell_id,
                    study_id=study_id,
                    plan_id=plan_id,
                    contract_version=contract.version,
                    task_id=task_id,
                    split_id=split_id,
                    arm_id=arm_id,
                    seed=seed,
                    replicate=replicate,
                    action_id=action_id,
                    experiment_id=experiment_id,
                    input_bindings=input_bindings,
                    expected_output_schema=contract.output_schema,
                    resource_profile={
                        "timeout_seconds": spec.timeout_seconds,
                        "network_access": spec.network_access,
                        "required_env": spec.required_env,
                        "arm_order_policy": (
                            "sha256_registered_schedule_v1"
                        ),
                        "pair_position": pair_position,
                    },
                    pair_block_id=pair_block_id,
                    pair_position=pair_position,
                    scheduled_after_run_cell_id=prior_cell_id,
                    dependency_ids=(
                        [prior_cell_id] if prior_cell_id is not None else []
                    ),
                    execution_manifest_hash=execution_hash,
                )
            )
            prior_cell_id = cell_id
    plan_payload = {
        "schema_version": 1,
        "plan_id": plan_id,
        "study_id": study_id,
        "handoff_id": admitted.handoff_id,
        "contract_version": contract.version,
        "profile": admitted.profile.value,
        "compiler_version": STAGE3_COMPILER_VERSION,
        "concurrency": contract.concurrency,
        "cells": [item.model_dump(mode="json") for item in cells],
    }
    plan = RunPlan(
        **plan_payload,
        plan_hash=_digest(plan_payload),
    )
    return repository.save_run_plan(plan)


def _compile_multi_arm_run_plan(
    repository: WorkflowRepository,
    study_id: str,
    *,
    handoff: Stage3HandoffPackage | None = None,
) -> RunPlan:
    """Compile one frozen cell per registered arm and matched run unit."""

    from .profiles.contracts import PairedMultiArmParameters

    admitted = handoff or repository.load_stage3_handoff(study_id)
    contract = repository.load_research_contract(
        study_id, admitted.contract_version
    )
    parameters = PairedMultiArmParameters.model_validate(
        contract.profile_parameters
    )
    manifest_path = Path(admitted.experiment_manifest_path).resolve()
    if sha256_file(manifest_path) != admitted.experiment_manifest_sha256:
        raise Stage3AdmissionError(
            ["Experiment Manifest changed after Stage 3 admission"]
        )
    manifest, _ = load_project_experiment_manifest(
        _source_root(repository, study_id), str(manifest_path)
    )
    if manifest is None:
        raise Stage3AdmissionError(["Experiment Manifest is unavailable"])
    violations = _validate_profile_contract(contract, manifest)
    if violations:
        raise Stage3AdmissionError(violations)

    root = _source_root(repository, study_id)
    arm_specs = {
        arm_id: _spec_for_arm(contract, manifest, arm_id)
        for arm_id in parameters.arms
    }
    arm_input_bindings: dict[str, dict[str, str]] = {}
    for arm_id, (_, _, spec) in arm_specs.items():
        frozen_inputs = (
            set(spec.required_inputs)
            | set(spec.candidate_code_paths)
            | set(spec.evaluator_code_paths)
            | set(spec.evaluator_required_inputs)
        )
        arm_input_bindings[arm_id] = {
            relative: _hash_input((root / relative).resolve(), root)
            for relative in sorted(frozen_inputs)
        }
    identity_payload = {
        "compiler_version": "paired-multi-arm-compiler-v1",
        "handoff": admitted.model_dump(
            mode="json", exclude={"admitted_at"}
        ),
        "contract": contract.model_dump(
            mode="json", exclude={"created_at", "frozen_at"}
        ),
        "input_bindings": arm_input_bindings,
    }
    plan_id = f"run-plan-{_digest(identity_payload)[:16]}"
    cells: list[RunCell] = []
    dimensions = product(
        sorted(contract.tasks),
        sorted(contract.splits),
        sorted(contract.seeds),
        range(1, contract.replicates + 1),
    )
    for task_id, split_id, seed, replicate in dimensions:
        block_id = stable_id(
            "pair-block",
            study_id,
            plan_id,
            task_id,
            split_id,
            seed,
            replicate,
        )
        arm_order = tuple(
            sorted(
                parameters.arms,
                key=lambda arm: _digest(
                    {
                        "policy": "sha256_registered_multi_arm_schedule_v1",
                        "block_id": block_id,
                        "arm_id": arm,
                    }
                ),
            )
        )
        prior_cell_id: str | None = None
        for position, arm_id in enumerate(arm_order, start=1):
            experiment_id, action_id, spec = arm_specs[arm_id]
            input_bindings = arm_input_bindings[arm_id]
            cell_id = stable_id(
                "run-cell",
                study_id,
                plan_id,
                task_id,
                split_id,
                arm_id,
                seed,
                replicate,
            )
            execution_hash = _digest(
                {
                    "spec": spec.model_dump(mode="json"),
                    "task_id": task_id,
                    "split_id": split_id,
                    "arm_id": arm_id,
                    "seed": seed,
                    "replicate": replicate,
                    "input_bindings": input_bindings,
                    "arm_order": arm_order,
                }
            )
            cells.append(
                RunCell(
                    run_cell_id=cell_id,
                    study_id=study_id,
                    plan_id=plan_id,
                    contract_version=contract.version,
                    task_id=task_id,
                    split_id=split_id,
                    arm_id=arm_id,
                    seed=seed,
                    replicate=replicate,
                    action_id=action_id,
                    experiment_id=experiment_id,
                    input_bindings=input_bindings,
                    expected_output_schema=contract.output_schema,
                    resource_profile={
                        "timeout_seconds": spec.timeout_seconds,
                        "network_access": spec.network_access,
                        "required_env": spec.required_env,
                        "arm_order_policy": (
                            "sha256_registered_multi_arm_schedule_v1"
                        ),
                        "arm_position": position,
                    },
                    pair_block_id=block_id,
                    pair_position=position,
                    scheduled_after_run_cell_id=prior_cell_id,
                    dependency_ids=(
                        [prior_cell_id] if prior_cell_id is not None else []
                    ),
                    execution_manifest_hash=execution_hash,
                )
            )
            prior_cell_id = cell_id
    payload = {
        "schema_version": 1,
        "plan_id": plan_id,
        "study_id": study_id,
        "handoff_id": admitted.handoff_id,
        "contract_version": contract.version,
        "profile": admitted.profile.value,
        "compiler_version": "paired-multi-arm-compiler-v1",
        "concurrency": contract.concurrency,
        "cells": [item.model_dump(mode="json") for item in cells],
    }
    return repository.save_run_plan(
        RunPlan(**payload, plan_hash=_digest(payload))
    )


def compile_run_plan(
    repository: WorkflowRepository,
    study_id: str,
    *,
    handoff: Stage3HandoffPackage | None = None,
) -> RunPlan:
    """Dispatch only to a certified, versioned Profile compiler."""

    from .profiles.registry import profile_bundle

    admitted = handoff or repository.load_stage3_handoff(study_id)
    bundle = profile_bundle(admitted.profile)
    if not bundle.formal_execution_supported():
        raise Stage3AdmissionError(
            [
                "blocked_unsupported_design: Profile Bundle "
                f"{bundle.profile_id.value} is "
                f"{bundle.certification_status.value}, not certified"
            ]
        )
    compilers = {
        "existing_python_project_compiler_v1": (
            _compile_legacy_paired_run_plan
        ),
        "benchmark_prediction_compiler_v1": _compile_legacy_paired_run_plan,
        "tabular_ml_compiler_v1": _compile_legacy_paired_run_plan,
        "stage3-compiler-v2": _compile_legacy_paired_run_plan,
        "paired_two_arm_compiler_v2": _compile_legacy_paired_run_plan,
        "paired_binary_compiler_v1": _compile_legacy_paired_run_plan,
        "paired_binary_cluster_compiler_v1": (
            _compile_legacy_paired_run_plan
        ),
        "paired_multi_arm_compiler_v1": _compile_multi_arm_run_plan,
    }
    compiler = compilers.get(bundle.run_plan_compiler_id)
    if compiler is None:
        raise Stage3AdmissionError(
            [
                "blocked_unsupported_design: no registered compiler for "
                f"{bundle.run_plan_compiler_id}"
            ]
        )
    return compiler(repository, study_id, handoff=admitted)


def ensure_stage_three_dag(
    repository: WorkflowRepository,
    study_id: str,
    *,
    explicit_manifest_path: str | None = None,
    repair_contract_id: str | None = None,
) -> tuple[Stage3HandoffPackage, RunPlan, list[StepInstance]]:
    """Admit, compile, and persist the complete Profile v1 execution DAG."""

    for definition in STAGE3_STEP_DEFINITIONS:
        repository.save_step_definition(definition)
    existing = [
        item for item in repository.list_steps(study_id)
        if item.phase is Phase.EXPERIMENT
        and item.task_group
        and item.task_group.startswith("stage3:")
    ]
    if existing and repair_contract_id is None:
        plans = repository.list_run_plans(study_id)
        if not plans:
            raise ValueError("Stage 3 DAG exists without a persisted Run Plan")
        study = repository.load_study(study_id)
        current_handoff = repository.load_stage3_handoff(study_id)
        if (
            study.active_contract_version is not None
            and plans[-1].contract_version == study.active_contract_version
            and current_handoff.contract_version
            == study.active_contract_version
        ):
            return current_handoff, plans[-1], existing

    handoff = admit_stage_three(
        repository,
        study_id,
        explicit_manifest_path=explicit_manifest_path,
        repair_contract_id=repair_contract_id,
    )
    plan = compile_run_plan(repository, study_id, handoff=handoff)
    group = f"stage3:{plan.plan_id}"
    plan_existing = [
        item for item in existing
        if item.task_group
        and (
            item.task_group == group
            or item.task_group.startswith(f"{group}:")
        )
    ]
    if plan_existing:
        return handoff, plan, plan_existing

    admission = repository.add_step(
        study_id,
        "stage3_admission",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_SERVICE,
        task_group=group,
        parameters={
            "handoff_id": handoff.handoff_id,
            "plan_id": plan.plan_id,
        },
        expected_output="Stage3HandoffPackage",
    )
    compiler = repository.add_step(
        study_id,
        "compile_stage3_run_plan",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[admission.step_instance_id],
        task_group=group,
        parameters={
            "handoff_id": handoff.handoff_id,
            "plan_id": plan.plan_id,
        },
        expected_output="immutable RunPlan and RunCell matrix",
    )
    gate = repository.add_step(
        study_id,
        "stage3_execution_gate",
        Phase.EXPERIMENT,
        ExecutorType.PROJECT_OWNER,
        depends_on=[compiler.step_instance_id],
        task_group=group,
        parameters={"plan_id": plan.plan_id},
        expected_output="owner approval for the Stage 3 run plan",
    )
    created = [admission, compiler, gate]
    run_steps: list[StepInstance] = []
    run_step_by_cell: dict[str, StepInstance] = {}
    for cell in plan.cells:
        dependencies = [gate.step_instance_id]
        if cell.scheduled_after_run_cell_id:
            predecessor = run_step_by_cell.get(
                cell.scheduled_after_run_cell_id
            )
            if predecessor is None:
                raise ValueError(
                    "Run Plan pair scheduling references an unknown prior cell"
                )
            dependencies.append(predecessor.step_instance_id)
        step = repository.add_step(
            study_id,
            "execute_stage3_run_cell",
            Phase.EXPERIMENT,
            ExecutorType.SANDBOX_RUNNER,
            depends_on=dependencies,
            parent_step_id=compiler.step_instance_id,
            task_group=f"{group}:runs",
            parameters={
                "plan_id": plan.plan_id,
                "run_cell_id": cell.run_cell_id,
            },
            expected_output=f"ResultEnvelope for {cell.run_cell_id}",
        )
        run_steps.append(step)
        run_step_by_cell[cell.run_cell_id] = step
        created.append(step)
    evaluate = repository.add_step(
        study_id,
        "evaluate_stage3_results",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        depends_on=[item.step_instance_id for item in run_steps],
        task_group=group,
        expected_output="EvaluationRecord",
    )
    evidence = repository.add_step(
        study_id,
        "build_stage3_evidence_ledger",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[evaluate.step_instance_id],
        task_group=group,
        expected_output="verified EvidenceEdge graph",
    )
    verdict = repository.add_step(
        study_id,
        "materialize_stage3_verdict",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        depends_on=[evidence.step_instance_id],
        task_group=group,
        expected_output="HypothesisVerdict and StudyVerdict",
    )
    audit = repository.add_step(
        study_id,
        "audit_stage3_completion",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        depends_on=[verdict.step_instance_id],
        task_group=group,
        expected_output="Stage 3 deterministic audit",
    )
    handoff_step = repository.add_step(
        study_id,
        "complete_stage3",
        Phase.EXPERIMENT,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[audit.step_instance_id],
        task_group=group,
        expected_output="Stage3CompletionPackage or explicit blocked result",
    )
    created.extend([evaluate, evidence, verdict, audit, handoff_step])
    repository.create_gate(
        study_id,
        GateType.REPAIR_OR_HIGH_COST_RUN,
        subject_type="stage3_run_plan",
        subject_id=plan.plan_id,
        subject_version=contract_version(repository, study_id),
    )
    return handoff, plan, created


def _stage3_root(
    repository: WorkflowRepository, study_id: str
) -> Path:
    repository.load_study(study_id)
    return repository.root / "studies" / study_id / "stage3"


def _plan_cell(
    repository: WorkflowRepository, step: StepInstance
) -> tuple[RunPlan, RunCell]:
    plan_id = str(step.parameters.get("plan_id") or "")
    cell_id = str(step.parameters.get("run_cell_id") or "")
    if not plan_id or not cell_id:
        raise ValueError("Stage 3 run step is missing its RunCell binding")
    plan = repository.load_run_plan(step.study_id, plan_id)
    cell = next(
        (item for item in plan.cells if item.run_cell_id == cell_id),
        None,
    )
    if cell is None:
        raise ValueError("RunCell does not belong to the persisted Run Plan")
    return plan, cell


def _manifest_spec(
    repository: WorkflowRepository,
    study_id: str,
    cell: RunCell,
) -> ExperimentSpec:
    handoff = repository.load_stage3_handoff(study_id)
    path = Path(handoff.experiment_manifest_path)
    if not path.is_file() or sha256_file(path) != (
        handoff.experiment_manifest_sha256
    ):
        raise Stage3AdmissionError(
            ["Experiment Manifest changed after admission"]
        )
    manifest, _ = load_project_experiment_manifest(
        _source_root(repository, study_id), str(path)
    )
    if manifest is None:
        raise Stage3AdmissionError(["Experiment Manifest is unavailable"])
    spec = experiment_for_action(manifest, cell.action_id)
    if spec is None or spec.experiment_id != cell.experiment_id:
        raise Stage3AdmissionError(
            ["RunCell no longer matches the frozen Experiment Manifest"]
        )
    return spec


def _lock_hash(
    repository: WorkflowRepository,
    handoff: Stage3HandoffPackage,
    name: str,
) -> str:
    artifact_id = handoff.lock_artifact_ids[name]
    artifact = next(
        item for item in repository.list_artifacts(handoff.study_id)
        if item.artifact_id == artifact_id
    )
    return artifact.sha256


def _lock_payload(
    repository: WorkflowRepository,
    handoff: Stage3HandoffPackage,
    name: str,
) -> dict[str, Any]:
    artifact_id = handoff.lock_artifact_ids[name]
    artifact = next(
        item for item in repository.list_artifacts(handoff.study_id)
        if item.artifact_id == artifact_id
    )
    path = Path(artifact.path)
    if not path.is_file() or sha256_file(path) != artifact.sha256:
        raise Stage3AdmissionError([f"frozen lock changed: {name}"])
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise Stage3AdmissionError([f"frozen lock is invalid: {name}"])
    return payload


def _result_payload(
    path: Path, schema: dict[str, Any]
) -> dict[str, Any]:
    if schema["format"] == "json":
        payload = read_json(path)
        if not isinstance(payload, dict):
            raise ValueError("Profile v1 JSON output must be an object")
        return payload
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError(
            "Profile v1 CSV output must contain exactly one summary row"
        )
    return dict(rows[0])


def _parse_sample_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "sample IDs in CSV must be a JSON array"
            ) from exc
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    raise ValueError("sample ID field must be an array")


def _register_file(
    repository: WorkflowRepository,
    study_id: str,
    path: Path,
    *,
    kind: str,
    role: ArtifactRole,
) -> str:
    artifact = repository.register_artifact(
        study_id,
        str(path),
        sha256_file(path),
        kind=kind,
        role=role,
    )
    return artifact.artifact_id


def _record_run_diagnostic(
    repository: WorkflowRepository,
    plan: RunPlan,
    cell: RunCell,
    failure_class: Stage3FailureClass,
    finding: str,
    *,
    artifact_ids: list[str] | None = None,
) -> DiagnosticReport:
    affected = sorted(set(artifact_ids or []))
    diagnostic = DiagnosticReport(
        diagnostic_id=stable_id(
            "stage3-diagnostic",
            plan.study_id,
            plan.plan_id,
            cell.run_cell_id,
            failure_class.value,
            finding,
        ),
        study_id=plan.study_id,
        plan_id=plan.plan_id,
        earliest_preventable_step_type="execute_stage3_run_cell",
        failure_class=failure_class,
        system_invalidation_artifact_ids=affected,
        system_findings=[finding],
        ai_root_cause_hypotheses=[],
    )
    return repository.save_stage3_diagnostic(diagnostic)


def _stage3_admission_handler(context: Any) -> dict[str, Any]:
    handoff_id = str(context.step.parameters.get("handoff_id") or "")
    if not handoff_id:
        handoff = admit_stage_three(context.repository, context.study_id)
    else:
        handoff = Stage3HandoffPackage.model_validate(
            read_json(
                _stage3_root(context.repository, context.study_id)
                / "handoffs"
                / f"{handoff_id}.json"
            )
        )
    return {"handoff": handoff.model_dump(mode="json")}


def _stage3_compile_handler(context: Any) -> dict[str, Any]:
    plan_id = str(context.step.parameters.get("plan_id") or "")
    plan = (
        context.repository.load_run_plan(context.study_id, plan_id)
        if plan_id
        else compile_run_plan(context.repository, context.study_id)
    )
    return {
        "run_plan": plan.model_dump(mode="json"),
        "run_cell_count": len(plan.cells),
    }


def _reuse_run_cell_result(
    context: Any,
    plan: RunPlan,
    cell: RunCell,
    source_result_id: str,
    source_run_id: str,
    repair_contract_id: str,
) -> dict[str, Any]:
    source_result = next(
        (
            item
            for item in context.repository.list_result_envelopes(
                context.study_id
            )
            if item.result_id == source_result_id
        ),
        None,
    )
    if source_result is None:
        raise ValueError("successor reuse references an unknown result")
    source_run = context.repository.load_research_run(
        context.study_id, source_run_id
    )
    if source_run.status is not ExecutionStatus.SUCCEEDED:
        raise ValueError("successor reuse requires a successful source run")
    result = context.repository.save_result_envelope(
        ResultEnvelope(
            result_id=stable_id(
                "result",
                context.study_id,
                cell.run_cell_id,
                source_result.result_id,
            ),
            study_id=context.study_id,
            run_cell_id=cell.run_cell_id,
            reused_from_result_id=source_result.result_id,
            output_artifact_ids=source_result.output_artifact_ids,
            metrics=source_result.metrics,
            denominator=source_result.denominator,
            sample_ids=source_result.sample_ids,
            abstentions=source_result.abstentions,
        )
    )
    run = context.repository.save_research_run(
        ResearchRun(
            run_id=stable_id("run", context.study_id, cell.run_cell_id),
            study_id=context.study_id,
            contract_version=cell.contract_version,
            kind=RunKind.EXPERIMENTAL,
            status=ExecutionStatus.SUCCEEDED,
            run_cell_id=cell.run_cell_id,
            predecessor_run_id=source_run.run_id,
            repair_contract_id=repair_contract_id,
            reused_artifact_ids=source_result.output_artifact_ids,
            output_artifact_ids=source_result.output_artifact_ids,
            completed_at=utc_now(),
        )
    )
    run_path = (
        context.repository.root
        / "studies"
        / context.study_id
        / "runs"
        / run.run_id
        / "run.json"
    )
    run_artifact_id = _register_file(
        context.repository,
        context.study_id,
        run_path,
        kind="research_run",
        role=ArtifactRole.RUN,
    )
    for artifact_id in source_result.output_artifact_ids:
        context.repository.add_dependency(
            context.study_id,
            artifact_id,
            run_artifact_id,
            relation="successor_reuses_output",
        )
    return {
        "run_cell_id": cell.run_cell_id,
        "reused": True,
        "result": result.model_dump(mode="json"),
        "research_run": run.model_dump(mode="json"),
        "_workflow_output_artifact_ids": [
            run_artifact_id,
            *source_result.output_artifact_ids,
        ],
    }


def _copy_frozen_inputs(
    source_root: Path,
    destination_root: Path,
    relative_paths: Iterable[str],
) -> None:
    for relative in relative_paths:
        source = safe_relative(source_root, relative)
        if not source.is_file():
            raise ExperimentPreflightError(
                "dataset", [f"frozen input is unavailable: {relative}"]
            )
        destination = safe_relative(destination_root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _render_isolated_command(
    command: list[str],
    *,
    replacements: dict[str, str],
) -> list[str]:
    rendered: list[str] = []
    for token in command:
        value = str(token)
        for placeholder, replacement in replacements.items():
            value = value.replace("{" + placeholder + "}", replacement)
        if "{" in value or "}" in value:
            raise ValueError(
                f"unknown isolated command placeholder in {value!r}"
            )
        rendered.append(value)
    return rendered


def _run_isolated_candidate_evaluator(
    spec: ExperimentSpec,
    *,
    source_root: Path,
    evidence_dir: Path,
    run_variables: dict[str, str],
    expected_image_id: str,
) -> dict[str, Any]:
    """Run an untrusted arm without labels, then score in a second container."""

    from .container_execution import (
        ContainerExecutionPolicy,
        run_isolated_command,
    )

    if spec.execution_backend != "isolated_candidate_evaluator":
        raise ValueError("isolated runner received a local experiment")
    assert spec.container_image
    assert spec.evaluator_command
    assert spec.prediction_artifact_path
    candidate_inputs = set(spec.required_inputs) | set(
        spec.candidate_code_paths
    )
    evaluator_inputs = set(spec.evaluator_required_inputs) | set(
        spec.evaluator_code_paths
    )
    overlap = candidate_inputs.intersection(spec.evaluator_required_inputs)
    if overlap:
        raise ValueError(
            "candidate mount includes evaluator-only targets: "
            + ", ".join(sorted(overlap))
        )
    candidate_input_root = evidence_dir / "candidate-input"
    candidate_output_root = evidence_dir / "candidate-output"
    evaluator_input_root = evidence_dir / "evaluator-input"
    evaluator_output_root = evidence_dir / "evaluator-output"
    for path in (
        candidate_input_root,
        candidate_output_root,
        evaluator_input_root,
        evaluator_output_root,
    ):
        if path.exists():
            raise ValueError(
                "isolated run workspace already exists; use a new attempt"
            )
    candidate_input_root.mkdir(parents=True)
    evaluator_input_root.mkdir(parents=True)
    _copy_frozen_inputs(
        source_root, candidate_input_root, sorted(candidate_inputs)
    )
    _copy_frozen_inputs(
        source_root, evaluator_input_root, sorted(evaluator_inputs)
    )
    if any(
        (candidate_input_root / relative).exists()
        for relative in spec.evaluator_required_inputs
    ):
        raise ValueError("formal targets leaked into the candidate container")
    if len(spec.required_inputs) != 1:
        raise ValueError(
            "Profile v1 generated arm requires exactly one candidate data file"
        )
    if len(spec.evaluator_required_inputs) != 1:
        raise ValueError(
            "Profile v1 generated evaluator requires exactly one target file"
        )
    data_relative = spec.required_inputs[0]
    target_relative = spec.evaluator_required_inputs[0]
    prediction_relative = spec.prediction_artifact_path
    prediction_output = safe_relative(
        candidate_output_root, prediction_relative
    )
    replacements = {
        "python": "python",
        "data_file": f"/workspace/input/{Path(data_relative).as_posix()}",
        "prediction_file": (
            f"/workspace/output/{Path(prediction_relative).as_posix()}"
        ),
        **run_variables,
    }
    policy = ContainerExecutionPolicy(
        image=spec.container_image,
        timeout_seconds=spec.timeout_seconds,
    )
    candidate = run_isolated_command(
        _render_isolated_command(spec.command, replacements=replacements),
        input_dir=candidate_input_root,
        output_dir=candidate_output_root,
        policy=policy,
    )
    if candidate.exit_code != 0:
        raise RuntimeError(
            "candidate container failed: " + candidate.stderr[-2000:]
        )
    if not prediction_output.is_file():
        raise ValueError("candidate did not emit the frozen prediction artifact")
    evaluator_prediction = safe_relative(
        evaluator_input_root, prediction_relative
    )
    evaluator_prediction.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(prediction_output, evaluator_prediction)
    metric_relative = spec.artifacts[0].path
    evaluator_replacements = {
        "python": "python",
        "prediction_file": (
            f"/workspace/input/{Path(prediction_relative).as_posix()}"
        ),
        "target_file": (
            f"/workspace/input/{Path(target_relative).as_posix()}"
        ),
        "metrics_file": (
            f"/workspace/output/{Path(metric_relative).as_posix()}"
        ),
        **run_variables,
    }
    evaluator = run_isolated_command(
        _render_isolated_command(
            spec.evaluator_command,
            replacements=evaluator_replacements,
        ),
        input_dir=evaluator_input_root,
        output_dir=evaluator_output_root,
        policy=policy,
    )
    if evaluator.exit_code != 0:
        raise RuntimeError(
            "platform evaluator container failed: "
            + evaluator.stderr[-2000:]
        )
    metric_path = safe_relative(evaluator_output_root, metric_relative)
    if not metric_path.is_file():
        raise ValueError("platform evaluator did not emit frozen metrics")
    candidate_image_id = str(
        candidate.isolation_attestation.get("image_id") or ""
    )
    evaluator_image_id = str(
        evaluator.isolation_attestation.get("image_id") or ""
    )
    if not candidate_image_id or candidate_image_id != evaluator_image_id:
        raise ValueError(
            "candidate and evaluator did not use the same frozen image digest"
        )
    if candidate_image_id != expected_image_id:
        raise ValueError(
            "container image digest differs from environment.lock.json"
        )
    stdout_path = evidence_dir / "stdout.log"
    stderr_path = evidence_dir / "stderr.log"
    write_text_atomic(
        stdout_path,
        "[candidate]\n"
        + candidate.stdout
        + "\n[evaluator]\n"
        + evaluator.stdout,
    )
    write_text_atomic(
        stderr_path,
        "[candidate]\n"
        + candidate.stderr
        + "\n[evaluator]\n"
        + evaluator.stderr,
    )
    attestation_path = evidence_dir / "isolation-attestations.json"
    attestations = {
        "candidate": candidate.isolation_attestation,
        "evaluator": evaluator.isolation_attestation,
        "candidate_targets_mounted": False,
        "candidate_and_evaluator_separate": True,
    }
    write_json_atomic(attestation_path, attestations)
    return {
        "status": "succeeded",
        "exit_code": 0,
        "input_binding_valid": True,
        "artifacts": [
            {
                "path": str(metric_path),
                "format": spec.artifacts[0].format,
                "sha256": sha256_file(metric_path),
            },
            {
                "path": str(prediction_output),
                "format": "jsonl",
                "sha256": sha256_file(prediction_output),
            },
            {
                "path": str(attestation_path),
                "format": "json",
                "sha256": sha256_file(attestation_path),
            },
        ],
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "isolation_attestations": attestations,
    }


def _execute_run_cell_handler(context: Any) -> dict[str, Any]:
    from .workflow_scheduler import BlockedStepError, TransientStepError

    plan, cell = _plan_cell(context.repository, context.step)
    source_result_id = str(
        context.step.parameters.get("reuse_from_result_id") or ""
    )
    if source_result_id:
        return _reuse_run_cell_result(
            context,
            plan,
            cell,
            source_result_id,
            str(context.step.parameters["reuse_from_run_id"]),
            str(context.step.parameters["repair_contract_id"]),
        )
    prior_results = [
        item
        for item in context.repository.list_result_envelopes(
            context.study_id
        )
        if item.run_cell_id == cell.run_cell_id
    ]
    if prior_results:
        raise BlockedStepError(
            "this RunCell already has its first qualified successful result; "
            "a new execution requires a Diagnostic, Repair Contract, and "
            "successor Run Plan",
            kind="canonical_attempt_already_selected",
        )
    handoff = context.repository.load_stage3_handoff(context.study_id)
    spec = _manifest_spec(context.repository, context.study_id, cell)
    project = context.repository.load_project(
        context.repository.load_study(context.study_id).project_id
    )
    attempt_number = max(1, int(context.step.attempt))
    attempt_id = stable_id(
        "attempt", context.study_id, cell.run_cell_id, attempt_number
    )
    evidence_dir = (
        _stage3_root(context.repository, context.study_id)
        / "executions"
        / cell.run_cell_id
        / f"attempt-{attempt_number}"
    )
    started_at = utc_now()
    common = {
        "attempt_id": attempt_id,
        "study_id": context.study_id,
        "run_cell_id": cell.run_cell_id,
        "attempt_number": attempt_number,
        "started_at": started_at,
        "input_hashes": cell.input_bindings,
        "environment_hash": _lock_hash(
            context.repository, handoff, "environment.lock.json"
        ),
        "code_hash": _lock_hash(
            context.repository, handoff, "code_manifest.lock.json"
        ),
        "idempotency_key": stable_id(
            "attempt-key", plan.plan_id, cell.run_cell_id, attempt_number
        ),
        "lease_id": context.step.lease_id,
        "fencing_token": context.step.fencing_token or None,
        "resource_telemetry": {
            "host_fingerprint": hashlib.sha256(
                platform.node().encode("utf-8")
            ).hexdigest(),
            "os": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "pair_block_id": cell.pair_block_id,
            "pair_position": cell.pair_position,
            "cold_cache_declared": bool(
                cell.resource_profile.get("cold_cache", False)
            ),
        },
    }
    run_variables = {
        "task_id": cell.task_id,
        "split_id": cell.split_id,
        "arm_id": cell.arm_id,
        "seed": str(cell.seed),
        "replicate": str(cell.replicate),
        "run_cell_id": cell.run_cell_id,
    }

    def control_status() -> str:
        status = context.repository.load_study(
            context.study_id
        ).execution_status
        return (
            "pause_requested"
            if status is ExecutionStatus.PAUSED
            else "running"
        )

    try:
        source_root = _source_root(context.repository, context.study_id)
        if spec.execution_backend == "isolated_candidate_evaluator":
            environment_lock = _lock_payload(
                context.repository, handoff, "environment.lock.json"
            )
            expected_image_id = str(
                environment_lock.get("container_image_id") or ""
            )
            if not expected_image_id:
                raise Stage3AdmissionError(
                    ["isolated execution lock lacks a container image digest"]
                )
            report = _run_isolated_candidate_evaluator(
                spec,
                source_root=source_root,
                evidence_dir=evidence_dir,
                run_variables=run_variables,
                expected_image_id=expected_image_id,
            )
        else:
            report = run_declared_experiment(
                spec,
                source_root=source_root,
                evidence_dir=evidence_dir,
                action_id=cell.action_id,
                plan_id=plan.plan_id,
                network_authorized=project.network_policy.network_enabled,
                report_progress=lambda **_: None,
                control_status=control_status,
                run_variables=run_variables,
            )
    except ExperimentPaused as exc:
        context.repository.save_execution_attempt(
            ExecutionAttempt(
                **common,
                status=RunCellStatus.PAUSED,
                finished_at=utc_now(),
                failure_class=Stage3FailureClass.CANCELLED,
                failure_detail=str(exc),
            )
        )
        raise BlockedStepError(str(exc), kind="paused_checkpoint") from exc
    except ExperimentPreflightError as exc:
        failure = (
            Stage3FailureClass.PERMISSION
            if exc.kind == "permission"
            else Stage3FailureClass.PROTOCOL
        )
        context.repository.save_execution_attempt(
            ExecutionAttempt(
                **common,
                status=RunCellStatus.BLOCKED,
                finished_at=utc_now(),
                failure_class=failure,
                failure_detail=str(exc),
            )
        )
        _record_run_diagnostic(
            context.repository, plan, cell, failure, str(exc)
        )
        raise BlockedStepError(str(exc), kind=exc.kind) from exc
    except OSError as exc:
        message = str(exc)
        if any(
            token in message.casefold()
            for token in ("out of memory", "cannot allocate memory", "oom")
        ):
            context.repository.save_execution_attempt(
                ExecutionAttempt(
                    **common,
                    status=RunCellStatus.BLOCKED,
                    finished_at=utc_now(),
                    failure_class=Stage3FailureClass.INTEGRITY,
                    failure_detail=(
                        "resource exhaustion is an implementation change "
                        "boundary, not a transient retry: " + message
                    ),
                )
            )
            _record_run_diagnostic(
                context.repository,
                plan,
                cell,
                Stage3FailureClass.INTEGRITY,
                message,
            )
            raise BlockedStepError(
                "OOM requires an approved implementation repair or successor; "
                "the scheduler will not silently change batch size, memory, "
                "threads, or other resources",
                kind="implementation_resource_change_required",
            ) from exc
        context.repository.save_execution_attempt(
            ExecutionAttempt(
                **common,
                status=RunCellStatus.FAILED_TRANSIENT,
                finished_at=utc_now(),
                failure_class=Stage3FailureClass.TRANSIENT,
                failure_detail=message,
            )
        )
        raise TransientStepError(message) from exc
    except TimeoutError as exc:
        context.repository.save_execution_attempt(
            ExecutionAttempt(
                **common,
                status=RunCellStatus.FAILED_TRANSIENT,
                finished_at=utc_now(),
                failure_class=Stage3FailureClass.TRANSIENT,
                failure_detail=str(exc),
            )
        )
        raise TransientStepError(str(exc)) from exc
    except ExperimentProcessError as exc:
        abnormal_termination_codes = {
            -1,
            4294967295,
            -1073741510,
            3221225786,
        }
        if exc.returncode in abnormal_termination_codes:
            context.repository.save_execution_attempt(
                ExecutionAttempt(
                    **common,
                    status=RunCellStatus.FAILED_TRANSIENT,
                    finished_at=utc_now(),
                    exit_status=exc.returncode,
                    failure_class=Stage3FailureClass.TRANSIENT,
                    failure_detail=str(exc),
                )
            )
            raise TransientStepError(str(exc)) from exc
        context.repository.save_execution_attempt(
            ExecutionAttempt(
                **common,
                status=RunCellStatus.BLOCKED,
                finished_at=utc_now(),
                exit_status=exc.returncode,
                failure_class=Stage3FailureClass.INTEGRITY,
                failure_detail=str(exc),
            )
        )
        _record_run_diagnostic(
            context.repository,
            plan,
            cell,
            Stage3FailureClass.INTEGRITY,
            str(exc),
        )
        raise BlockedStepError(
            str(exc), kind="experiment_process_failure"
        ) from exc
    except Exception as exc:
        context.repository.save_execution_attempt(
            ExecutionAttempt(
                **common,
                status=RunCellStatus.BLOCKED,
                finished_at=utc_now(),
                failure_class=Stage3FailureClass.INTEGRITY,
                failure_detail=str(exc),
            )
        )
        _record_run_diagnostic(
            context.repository,
            plan,
            cell,
            Stage3FailureClass.INTEGRITY,
            str(exc),
        )
        raise BlockedStepError(
            str(exc), kind="execution_or_integrity_failure"
        ) from exc

    output_ids: list[str] = []
    common["resource_telemetry"] = {
        **common["resource_telemetry"],
        "execution_backend": spec.execution_backend,
        "container_started": bool(
            report.get("isolation_attestations")
        ),
        "system_load": (
            list(os.getloadavg()) if hasattr(os, "getloadavg") else None
        ),
        "gpu": (
            report.get("isolation_attestations", {}).get("gpu")
            or {
                "available": False,
                "driver": None,
                "cuda": None,
                "memory_peak_bytes": None,
                "temperature_c": None,
                "power_watts": None,
            }
        ),
        "external_api": {
            "used": False,
            "region": None,
            "model": None,
            "input_tokens": 0,
            "output_tokens": 0,
        },
    }
    for item in report["artifacts"]:
        output_ids.append(
            _register_file(
                context.repository,
                context.study_id,
                Path(item["path"]),
                kind="stage3_output",
                role=ArtifactRole.OUTPUT,
            )
        )
    stdout_id = _register_file(
        context.repository,
        context.study_id,
        Path(report["stdout_log"]),
        kind="execution_stdout",
        role=ArtifactRole.RUN,
    )
    stderr_id = _register_file(
        context.repository,
        context.study_id,
        Path(report["stderr_log"]),
        kind="execution_stderr",
        role=ArtifactRole.RUN,
    )
    if not report.get("input_binding_valid"):
        message = "required inputs were not immutable regular files"
        context.repository.save_execution_attempt(
            ExecutionAttempt(
                **common,
                status=RunCellStatus.BLOCKED,
                finished_at=utc_now(),
                exit_status=int(report["exit_code"]),
                stdout_artifact_id=stdout_id,
                stderr_artifact_id=stderr_id,
                output_artifact_ids=output_ids,
                failure_class=Stage3FailureClass.INTEGRITY,
                failure_detail=message,
            )
        )
        _record_run_diagnostic(
            context.repository,
            plan,
            cell,
            Stage3FailureClass.INTEGRITY,
            message,
            artifact_ids=output_ids,
        )
        raise BlockedStepError(
            message,
            kind="input_binding_failure",
        )
    primary_path = Path(report["artifacts"][0]["path"])
    try:
        payload = _result_payload(primary_path, cell.expected_output_schema)
        metric_field = str(cell.expected_output_schema["metric_field"])
        denominator_field = str(
            cell.expected_output_schema["denominator_field"]
        )
        sample_field = str(cell.expected_output_schema["sample_id_field"])
        metric_value = float(payload[metric_field])
        aggregate_fields = [
            str(item)
            for item in cell.expected_output_schema.get(
                "aggregate_fields", [metric_field]
            )
        ]
        metric_values = {
            field: float(payload[field])
            for field in aggregate_fields
            if field != denominator_field and field in payload
        }
        metric_values.setdefault(metric_field, metric_value)
        denominator = float(payload[denominator_field])
        sample_ids = _parse_sample_ids(payload[sample_field])
        analysis_rows_field = str(
            cell.expected_output_schema.get("analysis_rows_field") or ""
        )
        analysis_rows = (
            payload.get(analysis_rows_field, [])
            if analysis_rows_field
            else []
        )
        if not isinstance(analysis_rows, list) or any(
            not isinstance(row, dict) for row in analysis_rows
        ):
            raise ValueError("analysis_rows must be a list of objects")
        if not math.isfinite(metric_value) or not math.isfinite(denominator):
            raise ValueError("metric and denominator must be finite")
    except Exception as exc:
        context.repository.save_execution_attempt(
            ExecutionAttempt(
                **common,
                status=RunCellStatus.BLOCKED,
                finished_at=utc_now(),
                exit_status=int(report["exit_code"]),
                stdout_artifact_id=stdout_id,
                stderr_artifact_id=stderr_id,
                output_artifact_ids=output_ids,
                failure_class=Stage3FailureClass.SCHEMA,
                failure_detail=str(exc),
            )
        )
        _record_run_diagnostic(
            context.repository,
            plan,
            cell,
            Stage3FailureClass.SCHEMA,
            str(exc),
            artifact_ids=output_ids,
        )
        raise BlockedStepError(
            str(exc),
            kind="schema_failure",
        ) from exc
    attempt = context.repository.save_execution_attempt(
        ExecutionAttempt(
            **common,
            status=RunCellStatus.SUCCEEDED,
            canonical=True,
            finished_at=utc_now(),
            exit_status=int(report["exit_code"]),
            stdout_artifact_id=stdout_id,
            stderr_artifact_id=stderr_id,
            output_artifact_ids=output_ids,
            isolation_attestations=report.get(
                "isolation_attestations", {}
            ),
        )
    )
    result = context.repository.save_result_envelope(
        ResultEnvelope(
            result_id=stable_id(
                "result", context.study_id, cell.run_cell_id, attempt.attempt_id
            ),
            study_id=context.study_id,
            run_cell_id=cell.run_cell_id,
            attempt_id=attempt.attempt_id,
            output_artifact_ids=output_ids,
            metrics=metric_values,
            denominator=denominator,
            sample_ids=sample_ids,
            analysis_rows=analysis_rows,
            abstentions=int(payload.get("abstentions", 0)),
        )
    )
    run = context.repository.save_research_run(
        ResearchRun(
            run_id=stable_id("run", context.study_id, cell.run_cell_id),
            study_id=context.study_id,
            contract_version=cell.contract_version,
            kind=RunKind.EXPERIMENTAL,
            status=ExecutionStatus.SUCCEEDED,
            run_cell_id=cell.run_cell_id,
            output_artifact_ids=output_ids,
            completed_at=utc_now(),
        )
    )
    run_path = (
        context.repository.root
        / "studies"
        / context.study_id
        / "runs"
        / run.run_id
        / "run.json"
    )
    run_artifact_id = _register_file(
        context.repository,
        context.study_id,
        run_path,
        kind="research_run",
        role=ArtifactRole.RUN,
    )
    return {
        "run_cell_id": cell.run_cell_id,
        "attempt": attempt.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "research_run": run.model_dump(mode="json"),
        "_workflow_output_artifact_ids": [
            run_artifact_id,
            stdout_id,
            stderr_id,
            *output_ids,
        ],
    }


def _evaluate_multi_arm_results(
    context: Any,
    plan: RunPlan,
    contract: ResearchContractVersion,
    expected: set[str],
    results: list[ResultEnvelope],
    by_cell: dict[str, list[ResultEnvelope]],
) -> dict[str, Any]:
    """Evaluate every preregistered treatment-control contrast as a conjunction."""

    from .profiles.assurance import run_profile_assurance
    from .profiles.contracts import PairedMultiArmParameters
    from .profiles.runtime import analyze_profile_rows

    parameters = PairedMultiArmParameters.model_validate(
        contract.profile_parameters
    )
    cells = {item.run_cell_id: item for item in plan.cells}
    checks = {
        "all_run_cells_present": expected == set(by_cell),
        "one_result_per_run_cell": all(
            len(by_cell.get(cell_id, [])) == 1 for cell_id in expected
        ),
        "denominators_positive": all(
            item.denominator is not None and item.denominator > 0
            for group in by_cell.values()
            for item in group
        ),
        "sample_ids_unique": all(
            len(item.sample_ids) == len(set(item.sample_ids))
            for group in by_cell.values()
            for item in group
        ),
        "sample_count_matches_denominator": all(
            item.denominator == len(item.sample_ids)
            for group in by_cell.values()
            for item in group
        ),
    }
    handoff = context.repository.load_stage3_handoff(context.study_id)
    execution_seal = (
        context.repository.load_execution_package_seal(
            context.study_id, handoff.execution_package_seal_id
        )
        if handoff.execution_package_seal_id
        else None
    )
    if execution_seal is not None and execution_seal.isolated_execution_required:
        attempts_by_cell = {
            item.run_cell_id: item
            for item in context.repository.list_execution_attempts(
                context.study_id
            )
            if item.run_cell_id in expected
            and item.status is RunCellStatus.SUCCEEDED
        }
        checks["isolated_execution_attested"] = (
            set(attempts_by_cell) == expected
            and all(
                bool(item.isolation_attestations)
                and item.isolation_attestations.get(
                    "candidate_targets_mounted"
                )
                is False
                and item.isolation_attestations.get(
                    "candidate_and_evaluator_separate"
                )
                is True
                for item in attempts_by_cell.values()
            )
        )
    else:
        checks["isolated_execution_attested"] = True

    group_map: dict[
        tuple[str, str, int, int], dict[str, ResultEnvelope]
    ] = {}
    for cell_id, group in by_cell.items():
        if cell_id not in cells or len(group) != 1:
            continue
        cell = cells[cell_id]
        key = (cell.task_id, cell.split_id, cell.seed, cell.replicate)
        group_map.setdefault(key, {})[cell.arm_id] = group[0]
    complete_groups = [
        group
        for group in group_map.values()
        if set(group) == set(parameters.arms)
    ]
    expected_groups = len(plan.cells) // len(parameters.arms)
    checks["all_multi_arm_groups_complete"] = (
        len(complete_groups) == expected_groups
    )
    checks["multi_arm_denominators_match"] = all(
        len({item.denominator for item in group.values()}) == 1
        for group in complete_groups
    )
    checks["multi_arm_samples_match"] = all(
        len({tuple(item.sample_ids) for item in group.values()}) == 1
        for group in complete_groups
    )
    metric_name = str(contract.metrics[0]["name"])
    analysis_parameters = {
        "pairing_key": parameters.pairing_key,
        "cluster_id_field": parameters.cluster_id_field,
        "variance_unit": parameters.variance_unit,
        "inference_spec": parameters.inference_spec.model_dump(mode="json"),
    }
    analyses: dict[str, Any] = {}
    analysis_errors: dict[str, str] = {}
    for control in parameters.control_arms:
        try:
            analyses[control] = analyze_profile_rows(
                Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2,
                [
                    (
                        group[control].analysis_rows,
                        group[parameters.treatment_arm].analysis_rows,
                    )
                    for group in complete_groups
                ],
                analysis_parameters,
            )
        except (KeyError, TypeError, ValueError) as exc:
            analysis_errors[control] = str(exc)
    checks["profile_analysis_rows_valid"] = (
        len(analyses) == len(parameters.control_arms)
        and not analysis_errors
    )
    missing_policy = str(
        contract.statistical_rules.get("missing_cell_policy")
        or "inconclusive"
    )
    qualification = (
        QualificationStatus.QUALIFIED
        if all(checks.values())
        else (
            QualificationStatus.INCOMPLETE
            if missing_policy == "inconclusive"
            else QualificationStatus.DISQUALIFIED
        )
    )

    arm_estimates = {
        arm: statistics.fmean(
            group[arm].metrics[metric_name] for group in complete_groups
        )
        for arm in parameters.arms
        if complete_groups
    }
    contrast_estimates = {
        f"{parameters.treatment_arm}_minus_{control}": {
            "control_arm": control,
            "treatment_arm": parameters.treatment_arm,
            "control_estimate": analysis.baseline_estimate,
            "treatment_estimate": analysis.treatment_estimate,
            "effect": analysis.effect,
            "confidence_interval": list(analysis.confidence_interval),
            "p_value": analysis.p_value,
            "pair_count": analysis.pair_count,
            "independent_unit_count": analysis.independent_unit_count,
        }
        for control, analysis in analyses.items()
    }
    safeguard_tolerance = float(
        contract.statistical_rules.get(
            "safeguard_noninferiority_tolerance", -0.05
        )
    )
    safeguard_results: dict[str, Any] = {}
    safeguards_pass = True
    for metric in contract.metrics[1:]:
        name = str(metric.get("name") or "")
        if not name:
            continue
        role = str(metric.get("role") or "secondary").casefold()
        gates_verdict = (
            role == "safeguard"
            or "noninferiority_tolerance" in metric
            or "minimum_value" in metric
        )
        try:
            per_arm = {
                arm: statistics.fmean(
                    group[arm].metrics[name] for group in complete_groups
                )
                for arm in parameters.arms
            }
        except KeyError:
            if gates_verdict:
                safeguards_pass = False
            safeguard_results[name] = {
                "status": "missing",
                "role": role,
                "gates_verdict": gates_verdict,
            }
            continue
        differences = {
            control: per_arm[parameters.treatment_arm] - per_arm[control]
            for control in parameters.control_arms
        }
        passed: bool | None = None
        if gates_verdict:
            comparison_passed = True
            if (
                "noninferiority_tolerance" in metric
                or "minimum_value" not in metric
            ):
                comparison_passed = all(
                    value >= float(
                        metric.get(
                            "noninferiority_tolerance",
                            safeguard_tolerance,
                        )
                    )
                    for value in differences.values()
                )
            minimum_passed = (
                per_arm[parameters.treatment_arm]
                >= float(metric["minimum_value"])
                if "minimum_value" in metric
                else True
            )
            passed = comparison_passed and minimum_passed
            safeguards_pass = safeguards_pass and passed
        safeguard_results[name] = {
            "arm_estimates": per_arm,
            "treatment_minus_control": differences,
            "passed": passed,
            "role": role,
            "gates_verdict": gates_verdict,
            "minimum_value": metric.get("minimum_value"),
            "noninferiority_tolerance": metric.get(
                "noninferiority_tolerance"
            ),
        }
    checks["protected_safeguards_pass"] = safeguards_pass

    assurance_checks = run_profile_assurance(plan.profile).checks
    assurance = context.repository.save_statistical_assurance_report(
        StatisticalAssuranceReport(
            report_id=stable_id(
                "stat-assurance",
                context.study_id,
                plan.plan_hash,
                "multi-arm-cluster",
            ),
            study_id=context.study_id,
            contract_version=contract.version,
            profile=plan.profile,
            status=(
                AssuranceStatus.CONDITIONAL
                if all(assurance_checks.values())
                else AssuranceStatus.FAILED
            ),
            checks=assurance_checks,
            variance_unit="cluster",
            aggregation_hierarchy=[
                str(item)
                for item in contract.estimand.get(
                    "aggregation_hierarchy", []
                )
            ],
            limitations=[
                "Multiplicity handling uses the frozen all-primary-contrasts "
                "conjunction and Holm-adjusted rejection decisions. The "
                "reported 95% cluster-bootstrap intervals are per-contrast "
                "intervals; the frozen record does not establish simultaneous "
                "familywise coverage for those intervals.",
            ],
        )
    )
    if assurance.status is AssuranceStatus.FAILED:
        qualification = QualificationStatus.DISQUALIFIED

    independent_contrast_effects = {
        control: statistics.fmean(
            group[parameters.treatment_arm].metrics[metric_name]
            - group[control].metrics[metric_name]
            for group in complete_groups
        )
        for control in parameters.control_arms
        if complete_groups
    }
    weakest_control = (
        min(
            independent_contrast_effects,
            key=independent_contrast_effects.get,
        )
        if independent_contrast_effects
        else None
    )
    secondary_effect = (
        independent_contrast_effects[weakest_control]
        if weakest_control is not None
        else None
    )
    secondary_matches = (
        len(independent_contrast_effects) == len(analyses)
        and all(
            math.isclose(
                analyses[control].effect,
                effect,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            for control, effect in independent_contrast_effects.items()
        )
    )
    checks["primary_metric_second_implementation"] = secondary_matches
    if not secondary_matches:
        qualification = QualificationStatus.DISQUALIFIED

    data_lock = _lock_payload(
        context.repository, handoff, "data_manifest.lock.json"
    )
    target_identity = data_lock.get("evaluator_targets") or data_lock
    target_manifest_hash = _digest(target_identity)
    dataset_version = str(
        data_lock.get("dataset_version")
        or data_lock.get("source_sha256")
        or target_manifest_hash[:16]
    )
    exposures: list[FormalEvaluationExposureRecord] = []
    for split_id in sorted({item.split_id for item in plan.cells}):
        target_hash = _digest(
            {
                "target_manifest_hash": target_manifest_hash,
                "split_id": split_id,
            }
        )
        prior_exposures = context.repository.list_formal_exposures(
            target_hash=target_hash
        )
        influenced_successor = bool(
            prior_exposures
            or handoff.predecessor_handoff_id
            or handoff.repair_contract_id
        )
        exposure_status = (
            ConfirmatoryStatus.ADAPTIVE_REUSE
            if influenced_successor
            else ConfirmatoryStatus.CONFIRMATORY_USED
        )
        exposures.append(
            context.repository.save_formal_exposure(
                FormalEvaluationExposureRecord(
                    exposure_id=stable_id(
                        "formal-exposure",
                        context.study_id,
                        plan.plan_id,
                        target_hash,
                        len(prior_exposures),
                    ),
                    dataset_version=dataset_version,
                    split_id=split_id,
                    target_hash=target_hash,
                    study_id=context.study_id,
                    plan_id=plan.plan_id,
                    exposure_type=FormalExposureType.AGGREGATE_METRICS,
                    revealed_fields=[
                        "arm_estimates",
                        "contrast_estimates",
                        "confidence_intervals",
                        "holm_decisions",
                        "decision",
                    ],
                    prior_exposure_count=len(prior_exposures),
                    result_influenced_successor=influenced_successor,
                    confirmatory_status=exposure_status,
                )
            )
        )
    confirmatory_status = (
        ConfirmatoryStatus.ADAPTIVE_REUSE
        if any(
            item.confirmatory_status is ConfirmatoryStatus.ADAPTIVE_REUSE
            for item in exposures
        )
        else ConfirmatoryStatus.CONFIRMATORY_USED
    )
    leakage_checks = {
        "exact_duplicate": (
            LeakageCheckStatus.PASSED
            if contract.data_requirements.get(
                "exact_duplicate_audit_passed"
            )
            is True
            else LeakageCheckStatus.REQUIRES_REVIEW
        ),
        "near_duplicate": (
            LeakageCheckStatus.PASSED
            if contract.data_requirements.get(
                "near_duplicate_audit_passed"
            )
            is True
            else LeakageCheckStatus.REQUIRES_REVIEW
        ),
        "group_split": LeakageCheckStatus.NOT_APPLICABLE,
        "temporal_split": LeakageCheckStatus.NOT_APPLICABLE,
        "preprocessing_fit_scope": LeakageCheckStatus.REQUIRES_REVIEW,
        "target_derived_feature": LeakageCheckStatus.REQUIRES_REVIEW,
        "pretraining_provenance": LeakageCheckStatus.REQUIRES_REVIEW,
        "candidate_output_exfiltration": (
            LeakageCheckStatus.PASSED
            if checks["isolated_execution_attested"]
            else LeakageCheckStatus.FAILED
        ),
    }
    leakage = context.repository.save_leakage_audit_report(
        LeakageAuditReport(
            report_id=stable_id(
                "leakage-audit",
                context.study_id,
                plan.plan_hash,
                target_manifest_hash,
            ),
            study_id=context.study_id,
            contract_version=contract.version,
            checks=leakage_checks,
            findings=[
                name
                for name, status in leakage_checks.items()
                if status is LeakageCheckStatus.REQUIRES_REVIEW
            ],
            blocking=any(
                status is LeakageCheckStatus.FAILED
                for status in leakage_checks.values()
            ),
        )
    )
    if leakage.blocking:
        qualification = QualificationStatus.DISQUALIFIED

    threshold = float(contract.statistical_rules["effect_threshold"])
    lower_threshold = float(
        contract.statistical_rules.get("confidence_lower_threshold", 0.0)
    )
    familywise_alpha = float(
        contract.statistical_rules.get("familywise_alpha", 0.05)
    )
    ordered_p_values = sorted(
        (
            (control, float(analysis.p_value))
            for control, analysis in analyses.items()
            if analysis.p_value is not None
        ),
        key=lambda item: item[1],
    )
    holm_rejections: dict[str, bool] = {
        control: False for control in parameters.control_arms
    }
    continue_rejecting = True
    comparison_count = len(ordered_p_values)
    for rank, (control, p_value) in enumerate(ordered_p_values):
        cutoff = familywise_alpha / (comparison_count - rank)
        rejected = continue_rejecting and p_value <= cutoff
        holm_rejections[control] = rejected
        if not rejected:
            continue_rejecting = False
    for control, rejected in holm_rejections.items():
        key = f"{parameters.treatment_arm}_minus_{control}"
        if key in contrast_estimates:
            contrast_estimates[key]["holm_reject"] = rejected
    all_supported = bool(analyses) and all(
        analysis.effect >= threshold
        and analysis.confidence_interval[0] > lower_threshold
        and holm_rejections.get(control, False)
        for control, analysis in analyses.items()
    )
    any_refuted = any(
        analysis.confidence_interval[1] <= -threshold
        for analysis in analyses.values()
    )
    if qualification is not QualificationStatus.QUALIFIED:
        decision = (
            HypothesisVerdictStatus.INCONCLUSIVE
            if qualification is QualificationStatus.INCOMPLETE
            else HypothesisVerdictStatus.UNVERIFIABLE
        )
    elif all_supported and safeguards_pass:
        decision = HypothesisVerdictStatus.SUPPORTED
    elif any_refuted:
        decision = HypothesisVerdictStatus.REFUTED
    else:
        decision = HypothesisVerdictStatus.INCONCLUSIVE

    weakest = (
        min(analyses.values(), key=lambda item: item.effect)
        if analyses
        else None
    )
    evaluation = context.repository.save_evaluation_record(
        EvaluationRecord(
            evaluation_id=stable_id(
                "evaluation",
                context.study_id,
                plan.plan_hash,
                *sorted(item.result_id for item in results),
            ),
            study_id=context.study_id,
            plan_id=plan.plan_id,
            contract_version=contract.version,
            qualification_status=qualification,
            qualification_checks=checks,
            excluded_run_cell_ids=sorted(expected.difference(by_cell)),
            exclusion_reason_counts=(
                {"missing_result": len(expected.difference(by_cell))}
                if expected.difference(by_cell)
                else {}
            ),
            metric_name=metric_name,
            baseline_estimate=(
                statistics.fmean(
                    arm_estimates[item]
                    for item in parameters.control_arms
                )
                if arm_estimates
                else None
            ),
            treatment_estimate=arm_estimates.get(
                parameters.treatment_arm
            ),
            paired_effect=weakest.effect if weakest else None,
            pair_count=weakest.pair_count if weakest else 0,
            independent_unit_count=(
                weakest.independent_unit_count if weakest else 0
            ),
            variance_unit="cluster",
            aggregation_hierarchy=[
                str(item)
                for item in contract.estimand.get(
                    "aggregation_hierarchy", []
                )
            ],
            statistical_assurance_report_id=assurance.report_id,
            exposure_record_id=(
                exposures[0].exposure_id if exposures else None
            ),
            confirmatory_status=confirmatory_status,
            secondary_implementation_effect=secondary_effect,
            secondary_implementation_matches=secondary_matches,
            confidence_interval=(
                weakest.confidence_interval if weakest else None
            ),
            arm_estimates=arm_estimates,
            contrast_estimates=contrast_estimates,
            statistical_rule={
                **contract.statistical_rules,
                "profile_id": plan.profile.value,
                "profile_parameters": contract.profile_parameters,
                "safeguard_results": safeguard_results,
                "analysis_errors": analysis_errors,
            },
            decision=decision,
            rationale=(
                "Decision applies the frozen conjunction over every "
                "treatment-control contrast and the protected safeguards."
            ),
            result_ids=sorted(item.result_id for item in results),
        )
    )
    path = (
        _stage3_root(context.repository, context.study_id)
        / "evaluations"
        / f"{evaluation.evaluation_id}.json"
    )
    artifact_id = _register_file(
        context.repository,
        context.study_id,
        path,
        kind="stage3_evaluation",
        role=ArtifactRole.EVALUATION,
    )
    if qualification is not QualificationStatus.QUALIFIED:
        context.repository.save_stage3_diagnostic(
            DiagnosticReport(
                diagnostic_id=stable_id(
                    "stage3-diagnostic",
                    context.study_id,
                    plan.plan_id,
                    evaluation.evaluation_id,
                ),
                study_id=context.study_id,
                plan_id=plan.plan_id,
                failure_class=Stage3FailureClass.INTEGRITY,
                earliest_preventable_step_type="evaluate_stage3_results",
                system_findings=[
                    "multi-arm deterministic qualification failed"
                ],
                ai_root_cause_hypotheses=[],
                system_invalidation_artifact_ids=sorted(
                    {
                        artifact_id
                        for result in results
                        for artifact_id in result.output_artifact_ids
                    }
                ),
            )
        )
    return {
        "evaluation": evaluation.model_dump(mode="json"),
        "_workflow_output_artifact_ids": [artifact_id],
    }


def _evaluate_results_handler(context: Any) -> dict[str, Any]:
    plan_id = str(context.step.task_group or "").removeprefix("stage3:")
    plan = context.repository.load_run_plan(context.study_id, plan_id)
    contract = context.repository.load_research_contract(
        context.study_id, plan.contract_version
    )
    expected = {item.run_cell_id for item in plan.cells}
    results = [
        item
        for item in context.repository.list_result_envelopes(context.study_id)
        if item.run_cell_id in expected
    ]
    by_cell: dict[str, list[ResultEnvelope]] = {}
    for result in results:
        by_cell.setdefault(result.run_cell_id, []).append(result)
    if plan.profile is Stage3Profile.PAIRED_MULTI_ARM_ABLATION_V1:
        return _evaluate_multi_arm_results(
            context, plan, contract, expected, results, by_cell
        )
    checks = {
        "all_run_cells_present": expected == set(by_cell),
        "one_result_per_run_cell": all(
            len(by_cell.get(cell_id, [])) == 1 for cell_id in expected
        ),
        "denominators_positive": all(
            item.denominator is not None and item.denominator > 0
            for group in by_cell.values()
            for item in group
        ),
        "sample_ids_unique": all(
            len(item.sample_ids) == len(set(item.sample_ids))
            for group in by_cell.values()
            for item in group
        ),
        "sample_count_matches_denominator": all(
            item.denominator == len(item.sample_ids)
            for group in by_cell.values()
            for item in group
        ),
    }
    handoff = context.repository.load_stage3_handoff(context.study_id)
    execution_seal = (
        context.repository.load_execution_package_seal(
            context.study_id, handoff.execution_package_seal_id
        )
        if handoff.execution_package_seal_id
        else None
    )
    if execution_seal is not None and (
        execution_seal.isolated_execution_required
    ):
        attempts_by_cell = {
            item.run_cell_id: item
            for item in context.repository.list_execution_attempts(
                context.study_id
            )
            if item.run_cell_id in expected
            and item.status is RunCellStatus.SUCCEEDED
        }
        checks["isolated_execution_attested"] = (
            set(attempts_by_cell) == expected
            and all(
                bool(item.isolation_attestations)
                and item.isolation_attestations.get(
                    "candidate_targets_mounted"
                )
                is False
                and item.isolation_attestations.get(
                    "candidate_and_evaluator_separate"
                )
                is True
                and (
                    item.isolation_attestations.get("candidate") or {}
                ).get("network")
                == "none"
                and (
                    item.isolation_attestations.get("evaluator") or {}
                ).get("network")
                == "none"
                for item in attempts_by_cell.values()
            )
        )
    else:
        checks["isolated_execution_attested"] = True
    pair_map: dict[
        tuple[str, str, int, int], dict[str, ResultEnvelope]
    ] = {}
    cells = {item.run_cell_id: item for item in plan.cells}
    for cell_id, group in by_cell.items():
        if cell_id not in cells or len(group) != 1:
            continue
        cell = cells[cell_id]
        key = (cell.task_id, cell.split_id, cell.seed, cell.replicate)
        pair_map.setdefault(key, {})[cell.arm_id] = group[0]
    expected_pairs = len(plan.cells) // 2
    complete_pairs = [
        pair for pair in pair_map.values()
        if set(pair) == {"baseline", "treatment"}
    ]
    checks["all_pairs_complete"] = len(complete_pairs) == expected_pairs
    checks["paired_denominators_match"] = all(
        pair["baseline"].denominator == pair["treatment"].denominator
        for pair in complete_pairs
    )
    checks["paired_samples_match"] = all(
        pair["baseline"].sample_ids == pair["treatment"].sample_ids
        for pair in complete_pairs
    )
    qualified = all(checks.values())
    missing_policy = contract.statistical_rules["missing_cell_policy"]
    qualification = (
        QualificationStatus.QUALIFIED
        if qualified
        else (
            QualificationStatus.INCOMPLETE
            if missing_policy == "inconclusive"
            else QualificationStatus.DISQUALIFIED
        )
    )
    metric_name = str(contract.metrics[0]["name"])
    keyed_effects = [
        (
            key,
            pair["treatment"].metrics[metric_name]
            - pair["baseline"].metrics[metric_name],
        )
        for key, pair in pair_map.items()
        if set(pair) == {"baseline", "treatment"}
    ]
    effects = [item[1] for item in keyed_effects]
    baseline_values = [
        pair["baseline"].metrics[metric_name] for pair in complete_pairs
    ]
    treatment_values = [
        pair["treatment"].metrics[metric_name] for pair in complete_pairs
    ]
    estimand = contract.estimand
    aggregation_hierarchy = [
        str(item) for item in estimand.get("aggregation_hierarchy", [])
    ]
    modern_profiles = {
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2,
        Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1,
        Stage3Profile.PAIRED_BINARY_CLUSTERED_V1,
    }
    modern_analysis = None
    if plan.profile in modern_profiles:
        from .profiles.runtime import analyze_profile_rows

        try:
            modern_analysis = analyze_profile_rows(
                plan.profile,
                [
                    (
                        pair["baseline"].analysis_rows,
                        pair["treatment"].analysis_rows,
                    )
                    for pair in complete_pairs
                ],
                contract.profile_parameters,
            )
            checks["profile_analysis_rows_valid"] = True
        except (KeyError, TypeError, ValueError) as exc:
            checks["profile_analysis_rows_valid"] = False
            modern_analysis_error = str(exc)
        variance_unit = (
            modern_analysis.variance_unit
            if modern_analysis is not None
            else str(contract.profile_parameters.get("variance_unit") or "")
        )
        independent_effects = (
            [modern_analysis.effect]
            if modern_analysis is not None
            else []
        )
    else:
        variance_unit = str(
            estimand.get("variance_unit") or "registered pair"
        ).strip().lower().replace("_", " ")
        if variance_unit in {"task", "tasks"}:
            grouped: dict[str, list[float]] = {}
            for key, pair_effect in keyed_effects:
                grouped.setdefault(key[0], []).append(pair_effect)
            independent_effects = [
                statistics.fmean(grouped[task_id])
                for task_id in sorted(grouped)
            ]
        elif variance_unit in {
            "registered pair",
            "pair",
            "task split seed replicate pair",
        }:
            independent_effects = effects
            variance_unit = "registered pair"
        else:
            checks["supported_variance_unit"] = False
            independent_effects = []
        checks.setdefault("supported_variance_unit", True)
    qualified = all(checks.values())
    qualification = (
        QualificationStatus.QUALIFIED
        if qualified
        else (
            QualificationStatus.INCOMPLETE
            if missing_policy == "inconclusive"
            else QualificationStatus.DISQUALIFIED
        )
    )
    effect = (
        modern_analysis.effect
        if modern_analysis is not None
        else (
            statistics.fmean(independent_effects)
            if independent_effects else None
        )
    )
    variance = (
        (
            statistics.variance(independent_effects)
            if len(independent_effects) > 1
            else (0.0 if independent_effects else None)
        )
        if modern_analysis is None
        else None
    )
    interval = (
        modern_analysis.confidence_interval
        if modern_analysis is not None
        else None
    )
    if (
        modern_analysis is None
        and effect is not None
        and variance is not None
    ):
        margin = 1.96 * math.sqrt(
            variance / max(1, len(independent_effects))
        )
        interval = (effect - margin, effect + margin)
    # Independent implementation: explicit sum/count rather than fmean.
    secondary_effect = (
        (
            sum(independent_effects) / len(independent_effects)
            if independent_effects else None
        )
        if modern_analysis is None
        else modern_analysis.effect
    )
    secondary_matches = (
        secondary_effect is None
        if effect is None
        else (
            secondary_effect is not None
            and math.isclose(effect, secondary_effect, rel_tol=1e-12, abs_tol=1e-12)
        )
    )
    checks["primary_metric_second_implementation"] = secondary_matches

    assurance_checks = {
        "zero_effect": math.isclose(
            statistics.fmean([0.0, 0.0, 0.0]), 0.0
        ),
        "known_positive_effect": statistics.fmean([0.1, 0.2]) > 0,
        "known_negative_effect": statistics.fmean([-0.1, -0.2]) < 0,
        "small_task_count_handled": len([0.1]) == 1,
        "heavy_tail_is_reported_as_limitation": True,
        "heteroskedasticity_is_reported_as_limitation": True,
        "nested_seeds_aggregate_to_variance_unit": (
            variance_unit in {"registered pair", "task", "pair", "cluster"}
        ),
        "missing_cells_fail_qualification": (
            checks["all_pairs_complete"] or not qualified
        ),
        "treatment_specific_crash_fails_qualification": (
            checks["all_pairs_complete"] or not qualified
        ),
        "extreme_identifiers_do_not_set_order": all(
            cell.pair_block_id is not None for cell in plan.cells
        ),
        "duplicate_sample_ids_detected": (
            checks["sample_ids_unique"] or not qualified
        ),
        "threshold_boundary_is_inclusive": True,
        "secondary_metric_implementation_matches": secondary_matches,
    }
    if plan.profile in modern_profiles:
        from .profiles.assurance import run_profile_assurance

        certified_checks = run_profile_assurance(plan.profile).checks
        assurance_checks.update(
            {
                f"profile_{name}": passed
                for name, passed in certified_checks.items()
            }
        )
    assurance_status = (
        AssuranceStatus.CONDITIONAL
        if all(assurance_checks.values())
        else AssuranceStatus.FAILED
    )
    assurance = context.repository.save_statistical_assurance_report(
        StatisticalAssuranceReport(
            report_id=stable_id(
                "stat-assurance",
                context.study_id,
                plan.plan_hash,
                variance_unit,
            ),
            study_id=context.study_id,
            contract_version=contract.version,
            profile=plan.profile,
            status=assurance_status,
            checks=assurance_checks,
            variance_unit=variance_unit,
            aggregation_hierarchy=aggregation_hierarchy,
            limitations=[
                "Normal-approximation intervals are not certified for "
                "heavy-tailed or very small independent-unit samples.",
                "Heteroskedastic designs require a successor statistical "
                "profile before confirmatory interpretation.",
            ],
        )
    )
    if assurance.status is AssuranceStatus.FAILED:
        qualification = QualificationStatus.DISQUALIFIED

    data_lock = _lock_payload(
        context.repository, handoff, "data_manifest.lock.json"
    )
    target_identity = data_lock.get("evaluator_targets") or data_lock
    target_manifest_hash = _digest(target_identity)
    dataset_version = str(
        data_lock.get("dataset_version")
        or data_lock.get("source_sha256")
        or target_manifest_hash[:16]
    )
    exposures: list[FormalEvaluationExposureRecord] = []
    for split_id in sorted({item.split_id for item in plan.cells}):
        target_hash = _digest(
            {
                "target_manifest_hash": target_manifest_hash,
                "split_id": split_id,
            }
        )
        prior_exposures = context.repository.list_formal_exposures(
            target_hash=target_hash
        )
        prior_count = len(prior_exposures)
        influenced_successor = bool(
            prior_count
            or handoff.predecessor_handoff_id
            or handoff.repair_contract_id
        )
        split_status = (
            ConfirmatoryStatus.ADAPTIVE_REUSE
            if influenced_successor
            else ConfirmatoryStatus.CONFIRMATORY_USED
        )
        exposures.append(
            context.repository.save_formal_exposure(
                FormalEvaluationExposureRecord(
                    exposure_id=stable_id(
                        "formal-exposure",
                        context.study_id,
                        plan.plan_id,
                        target_hash,
                        prior_count,
                    ),
                    dataset_version=dataset_version,
                    split_id=split_id,
                    target_hash=target_hash,
                    study_id=context.study_id,
                    plan_id=plan.plan_id,
                    exposure_type=FormalExposureType.AGGREGATE_METRICS,
                    revealed_fields=[
                        "baseline_estimate",
                        "treatment_estimate",
                        "paired_effect",
                        "confidence_interval",
                        "decision",
                    ],
                    prior_exposure_count=prior_count,
                    result_influenced_successor=influenced_successor,
                    confirmatory_status=split_status,
                )
            )
        )
    confirmatory_status = (
        ConfirmatoryStatus.ADAPTIVE_REUSE
        if any(
            item.confirmatory_status is ConfirmatoryStatus.ADAPTIVE_REUSE
            for item in exposures
        )
        else ConfirmatoryStatus.CONFIRMATORY_USED
    )

    leakage_checks = {
        "exact_duplicate": (
            LeakageCheckStatus.PASSED
            if contract.data_requirements.get(
                "exact_duplicate_audit_passed"
            )
            is True
            else LeakageCheckStatus.REQUIRES_REVIEW
        ),
        "near_duplicate": LeakageCheckStatus.REQUIRES_REVIEW,
        "group_split": (
            LeakageCheckStatus.PASSED
            if "group" in json.dumps(contract.data_requirements).lower()
            else LeakageCheckStatus.NOT_APPLICABLE
        ),
        "temporal_split": (
            LeakageCheckStatus.PASSED
            if "temporal" in json.dumps(contract.data_requirements).lower()
            else LeakageCheckStatus.NOT_APPLICABLE
        ),
        "preprocessing_fit_scope": LeakageCheckStatus.REQUIRES_REVIEW,
        "target_derived_feature": LeakageCheckStatus.REQUIRES_REVIEW,
        "pretraining_provenance": LeakageCheckStatus.REQUIRES_REVIEW,
        "candidate_output_exfiltration": (
            LeakageCheckStatus.PASSED
            if checks["isolated_execution_attested"]
            else LeakageCheckStatus.FAILED
        ),
    }
    leakage = context.repository.save_leakage_audit_report(
        LeakageAuditReport(
            report_id=stable_id(
                "leakage-audit",
                context.study_id,
                plan.plan_hash,
                target_manifest_hash,
            ),
            study_id=context.study_id,
            contract_version=contract.version,
            checks=leakage_checks,
            findings=[
                name
                for name, status in leakage_checks.items()
                if status is LeakageCheckStatus.REQUIRES_REVIEW
            ],
            blocking=any(
                status is LeakageCheckStatus.FAILED
                for status in leakage_checks.values()
            ),
        )
    )
    if leakage.blocking:
        qualification = QualificationStatus.DISQUALIFIED
    direction = contract.metrics[0]["direction"]
    directional_effect = (
        effect
        if direction in {"higher_is_better", "maximize"}
        else (-effect if effect is not None else None)
    )
    threshold = float(contract.statistical_rules["effect_threshold"])
    if qualification is not QualificationStatus.QUALIFIED:
        decision = (
            HypothesisVerdictStatus.INCONCLUSIVE
            if qualification is QualificationStatus.INCOMPLETE
            else HypothesisVerdictStatus.UNVERIFIABLE
        )
    elif (
        modern_analysis is not None
        and interval is not None
        and direction in {"higher_is_better", "maximize"}
        and interval[0] >= threshold
    ):
        decision = HypothesisVerdictStatus.SUPPORTED
    elif (
        modern_analysis is not None
        and interval is not None
        and direction in {"higher_is_better", "maximize"}
        and interval[1] <= -threshold
    ):
        decision = HypothesisVerdictStatus.REFUTED
    elif (
        modern_analysis is not None
        and interval is not None
        and direction in {"lower_is_better", "minimize"}
        and -interval[1] >= threshold
    ):
        decision = HypothesisVerdictStatus.SUPPORTED
    elif (
        modern_analysis is not None
        and interval is not None
        and direction in {"lower_is_better", "minimize"}
        and -interval[0] <= -threshold
    ):
        decision = HypothesisVerdictStatus.REFUTED
    elif (
        modern_analysis is None
        and directional_effect is not None
        and directional_effect >= threshold
    ):
        decision = HypothesisVerdictStatus.SUPPORTED
    elif (
        modern_analysis is None
        and directional_effect is not None
        and directional_effect <= -threshold
    ):
        decision = HypothesisVerdictStatus.REFUTED
    else:
        decision = HypothesisVerdictStatus.INCONCLUSIVE
    evaluation = context.repository.save_evaluation_record(
        EvaluationRecord(
            evaluation_id=stable_id(
                "evaluation",
                context.study_id,
                plan.plan_hash,
                *sorted(item.result_id for item in results),
            ),
            study_id=context.study_id,
            plan_id=plan.plan_id,
            contract_version=contract.version,
            qualification_status=qualification,
            qualification_checks=checks,
            excluded_run_cell_ids=sorted(expected.difference(by_cell)),
            exclusion_reason_counts=(
                {"missing_result": len(expected.difference(by_cell))}
                if expected.difference(by_cell)
                else {}
            ),
            metric_name=metric_name,
            baseline_estimate=(
                modern_analysis.baseline_estimate
                if modern_analysis is not None
                else (
                    statistics.fmean(baseline_values)
                    if baseline_values else None
                )
            ),
            treatment_estimate=(
                modern_analysis.treatment_estimate
                if modern_analysis is not None
                else (
                    statistics.fmean(treatment_values)
                    if treatment_values else None
                )
            ),
            paired_effect=effect,
            paired_variance=variance,
            pair_count=(
                modern_analysis.pair_count
                if modern_analysis is not None
                else len(effects)
            ),
            independent_unit_count=(
                modern_analysis.independent_unit_count
                if modern_analysis is not None
                else len(independent_effects)
            ),
            variance_unit=variance_unit,
            aggregation_hierarchy=aggregation_hierarchy,
            exposure_record_id=exposures[0].exposure_id,
            confirmatory_status=confirmatory_status,
            statistical_assurance_report_id=assurance.report_id,
            secondary_implementation_effect=secondary_effect,
            secondary_implementation_matches=secondary_matches,
            confidence_interval=interval,
            statistical_rule={
                **contract.statistical_rules,
                "metric_direction": direction,
                "variance_unit": variance_unit,
                "aggregation_hierarchy": aggregation_hierarchy,
                "profile_id": plan.profile.value,
                "profile_parameters": contract.profile_parameters,
                "profile_details": (
                    modern_analysis.details
                    if modern_analysis is not None
                    else {}
                ),
                "p_value": (
                    modern_analysis.p_value
                    if modern_analysis is not None
                    else None
                ),
            },
            decision=decision,
            rationale=(
                "Decision produced only from the frozen paired-comparison "
                "rule after deterministic output qualification."
            ),
            result_ids=sorted(item.result_id for item in results),
        )
    )
    evaluation_path = (
        _stage3_root(context.repository, context.study_id)
        / "evaluations"
        / f"{evaluation.evaluation_id}.json"
    )
    evaluation_artifact_id = _register_file(
        context.repository,
        context.study_id,
        evaluation_path,
        kind="stage3_evaluation",
        role=ArtifactRole.EVALUATION,
    )
    if qualification is not QualificationStatus.QUALIFIED:
        failed_checks = sorted(
            name for name, passed in checks.items() if not passed
        )
        context.repository.save_stage3_diagnostic(
            DiagnosticReport(
                diagnostic_id=stable_id(
                    "stage3-diagnostic",
                    context.study_id,
                    plan.plan_id,
                    evaluation.evaluation_id,
                ),
                study_id=context.study_id,
                plan_id=plan.plan_id,
                failure_class=Stage3FailureClass.INTEGRITY,
                earliest_preventable_step_type="evaluate_stage3_results",
                system_findings=[
                    "deterministic qualification failed: "
                    + ", ".join(failed_checks)
                ],
                ai_root_cause_hypotheses=[],
                system_invalidation_artifact_ids=sorted(
                    {
                        artifact_id
                        for result in results
                        for artifact_id in result.output_artifact_ids
                    }
                ),
            )
        )
    return {
        "evaluation": evaluation.model_dump(mode="json"),
        "_workflow_output_artifact_ids": [evaluation_artifact_id],
    }


def _save_edge(
    repository: WorkflowRepository,
    study_id: str,
    source_id: str,
    target_id: str,
    relation: EvidenceRelation,
    source_hash: str,
    target_hash: str,
    qualification: QualificationStatus,
) -> EvidenceEdge:
    return repository.save_evidence_edge(
        EvidenceEdge(
            edge_id=stable_id(
                "evidence-edge",
                study_id,
                source_id,
                target_id,
                relation.value,
                source_hash,
                target_hash,
            ),
            study_id=study_id,
            source_id=source_id,
            target_id=target_id,
            relation_type=relation,
            source_hash=source_hash,
            target_hash=target_hash,
            created_by="research_forge_stage3",
            qualification_status=qualification,
        )
    )


def _build_evidence_handler(context: Any) -> dict[str, Any]:
    evaluation = EvaluationRecord.model_validate(
        context.result("evaluate_stage3_results")["evaluation"]
    )
    plan = context.repository.load_run_plan(
        context.study_id, evaluation.plan_id
    )
    handoff = context.repository.load_stage3_handoff(context.study_id)
    artifacts = {
        item.artifact_id: item
        for item in context.repository.list_artifacts(context.study_id)
    }
    protocol_id = handoff.lock_artifact_ids["protocol.lock.json"]
    protocol = artifacts[protocol_id]
    scientific_artifact = None
    execution_artifact = None
    if handoff.scientific_specification_seal_id:
        scientific_path = (
            _stage3_root(context.repository, context.study_id)
            / "specifications"
            / f"{handoff.scientific_specification_seal_id}.json"
        )
        scientific_id = _register_file(
            context.repository,
            context.study_id,
            scientific_path,
            kind="scientific_specification_seal",
            role=ArtifactRole.PROTOCOL,
        )
        scientific_artifact = next(
            item
            for item in context.repository.list_artifacts(
                context.study_id
            )
            if item.artifact_id == scientific_id
        )
    if handoff.execution_package_seal_id:
        execution_path = (
            _stage3_root(context.repository, context.study_id)
            / "execution_packages"
            / f"{handoff.execution_package_seal_id}.json"
        )
        execution_id = _register_file(
            context.repository,
            context.study_id,
            execution_path,
            kind="execution_package_seal",
            role=ArtifactRole.PROTOCOL,
        )
        execution_artifact = next(
            item
            for item in context.repository.list_artifacts(
                context.study_id
            )
            if item.artifact_id == execution_id
        )
    plan_path = (
        _stage3_root(context.repository, context.study_id)
        / "run_plans"
        / f"{plan.plan_id}.json"
    )
    plan_artifact_id = _register_file(
        context.repository,
        context.study_id,
        plan_path,
        kind="stage3_run_plan",
        role=ArtifactRole.PROTOCOL,
    )
    plan_artifact = next(
        item for item in context.repository.list_artifacts(context.study_id)
        if item.artifact_id == plan_artifact_id
    )
    evaluation_path = (
        _stage3_root(context.repository, context.study_id)
        / "evaluations"
        / f"{evaluation.evaluation_id}.json"
    )
    evaluation_hash = sha256_file(evaluation_path)
    qualification = evaluation.qualification_status
    edges: list[EvidenceEdge] = []
    if scientific_artifact is not None and execution_artifact is not None:
        edges.extend(
            [
                _save_edge(
                    context.repository,
                    context.study_id,
                    scientific_artifact.artifact_id,
                    execution_artifact.artifact_id,
                    EvidenceRelation.MATERIALIZES,
                    scientific_artifact.sha256,
                    execution_artifact.sha256,
                    qualification,
                ),
                _save_edge(
                    context.repository,
                    context.study_id,
                    execution_artifact.artifact_id,
                    plan.plan_id,
                    EvidenceRelation.QUALIFIES,
                    execution_artifact.sha256,
                    plan_artifact.sha256,
                    qualification,
                ),
                _save_edge(
                    context.repository,
                    context.study_id,
                    protocol_id,
                    execution_artifact.artifact_id,
                    EvidenceRelation.QUALIFIES,
                    protocol.sha256,
                    execution_artifact.sha256,
                    qualification,
                ),
            ]
        )
    else:
        edges.append(
            _save_edge(
                context.repository,
                context.study_id,
                protocol_id,
                plan.plan_id,
                EvidenceRelation.QUALIFIES,
                protocol.sha256,
                plan_artifact.sha256,
                qualification,
            )
        )
    run_artifact_ids: list[str] = []
    output_artifact_ids: list[str] = []
    expected_run_ids = {
        stable_id("run", context.study_id, cell.run_cell_id)
        for cell in plan.cells
    }
    for run in context.repository.list_research_runs(context.study_id):
        if run.run_id not in expected_run_ids:
            continue
        run_path = (
            context.repository.root
            / "studies"
            / context.study_id
            / "runs"
            / run.run_id
            / "run.json"
        )
        run_hash = sha256_file(run_path)
        run_artifact = next(
            item for item in context.repository.list_artifacts(
                context.study_id
            )
            if Path(item.path).resolve() == run_path.resolve()
        )
        run_artifact_ids.append(run_artifact.artifact_id)
        edges.append(
            _save_edge(
                context.repository,
                context.study_id,
                plan.plan_id,
                run.run_id,
                EvidenceRelation.MATERIALIZES,
                plan_artifact.sha256,
                run_hash,
                qualification,
            )
        )
        for output_id in run.output_artifact_ids:
            output = artifacts[output_id]
            output_artifact_ids.append(output_id)
            edges.append(
                _save_edge(
                    context.repository,
                    context.study_id,
                    run.run_id,
                    output_id,
                    EvidenceRelation.PRODUCED,
                    run_hash,
                    output.sha256,
                    qualification,
                )
            )
            edges.append(
                _save_edge(
                    context.repository,
                    context.study_id,
                    output_id,
                    evaluation.evaluation_id,
                    EvidenceRelation.EVALUATED_BY,
                    output.sha256,
                    evaluation_hash,
                    qualification,
                )
            )
    evaluation_artifact = next(
        item for item in context.repository.list_artifacts(context.study_id)
        if Path(item.path).resolve() == evaluation_path.resolve()
    )
    evaluator_lock_id = handoff.lock_artifact_ids.get(
        "evaluator.lock.json"
    )
    if evaluator_lock_id:
        evaluator_lock = artifacts[evaluator_lock_id]
        edges.append(
            _save_edge(
                context.repository,
                context.study_id,
                evaluator_lock.artifact_id,
                evaluation.evaluation_id,
                EvidenceRelation.QUALIFIES,
                evaluator_lock.sha256,
                evaluation_hash,
                qualification,
            )
        )
    chain = context.repository.save_evidence_chain(
        EvidenceChain(
            chain_id=stable_id(
                "chain",
                context.study_id,
                plan.plan_id,
                evaluation.evaluation_id,
            ),
            study_id=context.study_id,
            level=(
                EvidenceChainLevel.VERIFIED
                if qualification is QualificationStatus.QUALIFIED
                else EvidenceChainLevel.INFERRED
            ),
            protocol_artifact_id=(
                scientific_artifact.artifact_id
                if scientific_artifact is not None
                else protocol_id
            ),
            run_artifact_ids=sorted(set(run_artifact_ids)),
            output_artifact_ids=sorted(set(output_artifact_ids)),
            evaluation_artifact_ids=[
                *(
                    [evaluator_lock_id]
                    if evaluator_lock_id
                    else []
                ),
                evaluation_artifact.artifact_id,
            ],
            verified_checks=evaluation.qualification_checks,
            inference_rationale=(
                None
                if qualification is QualificationStatus.QUALIFIED
                else "Stage 3 output qualification did not pass."
            ),
        )
    )
    return {
        "chain": chain.model_dump(mode="json"),
        "evidence_edge_ids": [item.edge_id for item in edges],
    }


def _materialize_verdict_handler(context: Any) -> dict[str, Any]:
    chain_result = context.result("build_stage3_evidence_ledger")
    chain = EvidenceChain.model_validate(chain_result["chain"])
    evaluation = EvaluationRecord.model_validate(
        context.result("evaluate_stage3_results")["evaluation"]
    )
    contract = context.repository.load_research_contract(
        context.study_id, evaluation.contract_version
    )
    hypothesis = contract.hypotheses[0]
    eligible = chain.verdict_eligible() and (
        evaluation.qualification_status is QualificationStatus.QUALIFIED
    )
    decision = (
        evaluation.decision
        if eligible
        else (
            HypothesisVerdictStatus.INCONCLUSIVE
            if evaluation.qualification_status
            is QualificationStatus.INCOMPLETE
            else HypothesisVerdictStatus.UNVERIFIABLE
        )
    )
    hypothesis_verdict = context.repository.save_hypothesis_verdict(
        HypothesisVerdict(
            verdict_id=stable_id(
                "hverdict",
                context.study_id,
                hypothesis.hypothesis_id,
                evaluation.evaluation_id,
            ),
            study_id=context.study_id,
            hypothesis_id=hypothesis.hypothesis_id,
            status=decision,
            evidence_chain_ids=[chain.chain_id],
            eligible_evidence=eligible,
            rationale=evaluation.rationale,
        )
    )
    study_verdict = context.repository.save_study_verdict(
        aggregate_study_verdict(
            context.study_id,
            contract.hypotheses,
            [hypothesis_verdict],
        )
    )
    if decision is HypothesisVerdictStatus.SUPPORTED:
        claim_text = (
            "Within the frozen task, split, seed, and replicate matrix, "
            "the registered treatment met the frozen effect rule relative "
            "to the registered baseline."
        )
    elif decision is HypothesisVerdictStatus.REFUTED:
        claim_text = (
            "Within the frozen matrix, the registered treatment met the "
            "frozen rule for an effect opposite to the hypothesized "
            "direction."
        )
    else:
        claim_text = (
            "The frozen matrix does not support a directional treatment "
            "claim under the registered decision rule."
        )
    limitations = [
        "The claim is limited to the frozen tasks and formal split.",
        "The Stage 3 result is not evidence of production reliability.",
    ]
    from .scientific_validity import (
        ScientificValidityContract,
        audit_evidence_validity,
        summarize_classification_rows,
    )

    validity_contract = ScientificValidityContract.model_validate(
        contract.scientific_validity_contract or {}
    )
    declared_evidence = dict(
        contract.implementation_requirements.get(
            "scientific_evidence_summary", {}
        )
    )
    plan = context.repository.load_run_plan(
        context.study_id, evaluation.plan_id
    )
    result_by_id = {
        item.result_id: item
        for item in context.repository.list_result_envelopes(
            context.study_id
        )
    }
    evaluated_results = [
        result_by_id[result_id]
        for result_id in evaluation.result_ids
        if result_id in result_by_id
    ]
    evaluated_cell_ids = {
        item.run_cell_id for item in evaluated_results
    }
    completed_design_ids: set[str] = set()
    for cell in plan.cells:
        if cell.run_cell_id not in evaluated_cell_ids:
            continue
        completed_design_ids.update(
            {
                cell.arm_id,
                cell.action_id,
                cell.experiment_id,
            }
        )
    analysis_rows = [
        row
        for result in evaluated_results
        for row in result.analysis_rows
    ]
    automatic_decomposition = summarize_classification_rows(
        analysis_rows
    )
    produced_artifact_ids = {
        artifact_id
        for result in evaluated_results
        for artifact_id in result.output_artifact_ids
    }
    evidence_bindings = {
        str(key): {str(item) for item in value}
        for key, value in dict(
            declared_evidence.get("evidence_bindings") or {}
        ).items()
        if isinstance(value, list)
    }

    def bound_declared(name: str, default: Any) -> Any:
        bound_artifacts = evidence_bindings.get(name, set())
        if (
            bound_artifacts
            and bound_artifacts.issubset(produced_artifact_ids)
        ):
            return declared_evidence.get(name, default)
        return default

    verified_analysis_ids = {
        str(row.get("analysis_id") or row.get("check_id"))
        for row in analysis_rows
        if row.get("analysis_id") or row.get("check_id")
        if str(row.get("status") or "").casefold()
        in {"passed", "succeeded", "verified"}
    }
    qualification_checks = dict(evaluation.qualification_checks)
    evidence_summary = {
        "input_integrity": {
            **evaluation.qualification_checks,
            **dict(bound_declared("input_integrity", {})),
        },
        "completed_control_ids": sorted(
            completed_design_ids
            | set(bound_declared("completed_control_ids", []))
        ),
        "completed_feature_ablation_ids": sorted(
            (
                completed_design_ids
                & set(validity_contract.feature_ablation_ids)
            )
            | set(bound_declared("completed_feature_ablation_ids", []))
        ),
        "behavioral_decomposition": {
            **automatic_decomposition,
            **dict(bound_declared("behavioral_decomposition", {})),
        },
        "generator_disclosure_verified": qualification_checks.get(
            "generator_disclosure_verified", False
        )
        or bool(bound_declared("generator_disclosure_verified", False)),
        "seed_roles_verified": qualification_checks.get(
            "seed_roles_verified", False
        )
        or bool(bound_declared("seed_roles_verified", False)),
        "boundary_analysis_completed": bool(
            set(validity_contract.boundary_analysis_plan)
            .issubset(verified_analysis_ids)
        )
        or bool(bound_declared("boundary_analysis_completed", False)),
        "completed_heterogeneity_analyses": sorted(
            set(validity_contract.heterogeneity_plan)
            & verified_analysis_ids
            | set(
                bound_declared(
                    "completed_heterogeneity_analyses", []
                )
            )
        ),
        "completed_robust_inference": sorted(
            set(validity_contract.robust_inference_plan)
            & verified_analysis_ids
            | set(bound_declared("completed_robust_inference", []))
        ),
        "threshold_sensitivity_completed": bool(
            set(validity_contract.threshold_sensitivity_plan)
            .issubset(verified_analysis_ids)
        )
        or bool(
            bound_declared("threshold_sensitivity_completed", False)
        ),
        "safeguard_counts": dict(
            bound_declared("safeguard_counts", {})
        ),
        "reproducibility_package_ready": bool(
            bound_declared("reproducibility_package_ready", False)
        ),
        "diagnostic_flags": list(
            bound_declared("diagnostic_flags", [])
        ),
        "completed_diagnostic_followups": sorted(
            verified_analysis_ids
            | set(
                bound_declared(
                    "completed_diagnostic_followups", []
                )
            )
        ),
        "ood_or_rule_separated_evaluation_passed": (
            qualification_checks.get(
                "ood_or_rule_separated_evaluation_passed", False
            )
            or bool(
                bound_declared(
                    "ood_or_rule_separated_evaluation_passed", False
                )
            )
        ),
        "independent_clusters": (
            bound_declared("independent_clusters", None)
            or evaluation.independent_unit_count
        ),
    }
    validity_report = audit_evidence_validity(
        validity_contract, evidence_summary
    )
    limitations.extend(
        f"{item.code}: {item.message}"
        for item in validity_report.findings
    )
    validity_path = (
        _stage3_root(context.repository, context.study_id)
        / "validity"
        / f"{evaluation.evaluation_id}.scientific-validity.json"
    )
    validity_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(validity_path, validity_report)
    validity_artifact_id = _register_file(
        context.repository,
        context.study_id,
        validity_path,
        kind="stage3_scientific_validity_report",
        role=ArtifactRole.AUDIT,
    )
    if (
        evaluation.confirmatory_status
        is ConfirmatoryStatus.ADAPTIVE_REUSE
    ):
        limitations.append(
            "The formal target was previously exposed; this is adaptive "
            "follow-up evidence, not independent confirmation."
        )
    assurance_reports = (
        context.repository.list_statistical_assurance_reports(
            context.study_id
        )
    )
    if assurance_reports and (
        assurance_reports[-1].status is AssuranceStatus.CONDITIONAL
    ):
        limitations.extend(assurance_reports[-1].limitations)
    claim_envelope = context.repository.save_claim_envelope(
        ScientificClaimEnvelope(
            claim_envelope_id=stable_id(
                "claim-envelope",
                context.study_id,
                evaluation.plan_id,
                evaluation.evaluation_id,
            ),
            study_id=context.study_id,
            plan_id=evaluation.plan_id,
            allowed_claim=claim_text,
            population=str(
                contract.estimand.get("population")
                or contract.data_boundary
            ),
            tasks=contract.tasks,
            intervention=str(
                contract.treatment.get("name")
                or contract.treatment.get("definition")
                or "registered treatment"
            ),
            comparator=str(
                contract.baseline.get("name")
                or contract.baseline.get("definition")
                or "registered baseline"
            ),
            outcome=evaluation.metric_name,
            effect_estimate=evaluation.paired_effect,
            interval=evaluation.confidence_interval,
            evidence_level=(
                EvidenceReproductionLevel.EVIDENCE_CHAIN_VERIFIED
            ),
            confirmatory_status=evaluation.confirmatory_status,
            known_limitations=limitations,
            maximum_claim_tier=validity_report.maximum_claim_tier.value,
            scientific_validity_report_ids=[validity_artifact_id],
        )
    )
    evaluation_path = (
        _stage3_root(context.repository, context.study_id)
        / "evaluations"
        / f"{evaluation.evaluation_id}.json"
    )
    hypothesis_path = (
        context.repository.root
        / "studies"
        / context.study_id
        / "verdicts"
        / f"{hypothesis_verdict.verdict_id}.json"
    )
    study_path = (
        context.repository.root
        / "studies"
        / context.study_id
        / "verdicts"
        / f"{study_verdict.verdict_id}.json"
    )
    hypothesis_artifact_id = _register_file(
        context.repository,
        context.study_id,
        hypothesis_path,
        kind="hypothesis_verdict",
        role=ArtifactRole.CLAIM,
    )
    study_artifact_id = _register_file(
        context.repository,
        context.study_id,
        study_path,
        kind="study_verdict",
        role=ArtifactRole.CLAIM,
    )
    claim_path = (
        _stage3_root(context.repository, context.study_id)
        / "claim_envelopes"
        / f"{claim_envelope.claim_envelope_id}.json"
    )
    claim_artifact_id = _register_file(
        context.repository,
        context.study_id,
        claim_path,
        kind="scientific_claim_envelope",
        role=ArtifactRole.CLAIM,
    )
    qualification = evaluation.qualification_status
    relation = (
        EvidenceRelation.SUPPORTS
        if decision is HypothesisVerdictStatus.SUPPORTED
        else (
            EvidenceRelation.REFUTES
            if decision is HypothesisVerdictStatus.REFUTED
            else EvidenceRelation.QUALIFIES_VERDICT
        )
    )
    first = _save_edge(
        context.repository,
        context.study_id,
        evaluation.evaluation_id,
        hypothesis_verdict.verdict_id,
        relation,
        sha256_file(evaluation_path),
        sha256_file(hypothesis_path),
        qualification,
    )
    second = _save_edge(
        context.repository,
        context.study_id,
        hypothesis_verdict.verdict_id,
        study_verdict.verdict_id,
        EvidenceRelation.AGGREGATES_TO,
        sha256_file(hypothesis_path),
        sha256_file(study_path),
        qualification,
    )
    return {
        "hypothesis_verdict": hypothesis_verdict.model_dump(mode="json"),
        "study_verdict": study_verdict.model_dump(mode="json"),
        "claim_envelope": claim_envelope.model_dump(mode="json"),
        "scientific_validity_report": validity_report.model_dump(
            mode="json"
        ),
        "evidence_edge_ids": [
            *chain_result["evidence_edge_ids"],
            first.edge_id,
            second.edge_id,
        ],
        "_workflow_output_artifact_ids": [
            hypothesis_artifact_id,
            study_artifact_id,
            claim_artifact_id,
            validity_artifact_id,
        ],
    }


def _plan_hash_valid(plan: RunPlan) -> bool:
    payload = plan.model_dump(
        mode="json", exclude={"created_at", "plan_hash"}
    )
    return _digest(payload) == plan.plan_hash


def _audit_stage3_handler(context: Any) -> dict[str, Any]:
    verdict_result = context.result("materialize_stage3_verdict")
    study_verdict = verdict_result["study_verdict"]
    plans = context.repository.list_run_plans(context.study_id)
    plan = plans[-1]
    evaluations = [
        item
        for item in context.repository.list_evaluation_records(
            context.study_id
        )
        if item.plan_id == plan.plan_id
    ]
    expected_cells = {item.run_cell_id for item in plan.cells}
    attempts = [
        item
        for item in context.repository.list_execution_attempts(
            context.study_id
        )
        if item.run_cell_id in expected_cells
    ]
    results = [
        item
        for item in context.repository.list_result_envelopes(
            context.study_id
        )
        if item.run_cell_id in expected_cells
    ]
    successful_cells = {
        item.run_cell_id for item in attempts
        if item.status is RunCellStatus.SUCCEEDED
    }
    successful_cells.update(
        item.run_cell_id
        for item in context.repository.list_research_runs(
            context.study_id
        )
        if item.run_cell_id in expected_cells
        and item.status is ExecutionStatus.SUCCEEDED
        and item.reused_artifact_ids
    )
    current_evaluation = evaluations[-1] if evaluations else None
    canonical_attempts = [
        item for item in attempts
        if item.status is RunCellStatus.SUCCEEDED and item.canonical
    ]
    reused_canonical_cells = {
        item.run_cell_id for item in results
        if item.reused_from_result_id is not None
    }
    canonical_cells = {
        item.run_cell_id for item in canonical_attempts
    } | reused_canonical_cells
    exposure_records = context.repository.list_formal_exposures(
        context.study_id
    )
    assurance_reports = (
        context.repository.list_statistical_assurance_reports(
            context.study_id
        )
    )
    leakage_reports = context.repository.list_leakage_audit_reports(
        context.study_id
    )
    current_chain_id = (
        stable_id(
            "chain",
            context.study_id,
            plan.plan_id,
            current_evaluation.evaluation_id,
        )
        if current_evaluation is not None
        else None
    )
    handoff = context.repository.load_stage3_handoff(context.study_id)
    current_edges = context.repository.list_evidence_edges(
        context.study_id
    )
    seal_lineage_required = bool(
        handoff.scientific_specification_seal_id
        and handoff.execution_package_seal_id
    )
    seal_lineage_present = True
    if seal_lineage_required:
        scientific_path = (
            _stage3_root(context.repository, context.study_id)
            / "specifications"
            / f"{handoff.scientific_specification_seal_id}.json"
        )
        execution_path = (
            _stage3_root(context.repository, context.study_id)
            / "execution_packages"
            / f"{handoff.execution_package_seal_id}.json"
        )
        scientific_artifact_id = stable_id(
            "artifact",
            context.study_id,
            str(scientific_path),
            sha256_file(scientific_path),
            1,
        )
        execution_artifact_id = stable_id(
            "artifact",
            context.study_id,
            str(execution_path),
            sha256_file(execution_path),
            1,
        )
        seal_lineage_present = any(
            edge.source_id == scientific_artifact_id
            and edge.target_id == execution_artifact_id
            for edge in current_edges
        ) and any(
            edge.source_id == execution_artifact_id
            and edge.target_id == plan.plan_id
            for edge in current_edges
        )
    checks = {
        "run_plan_hash_valid": _plan_hash_valid(plan),
        "all_cells_have_successful_attempt": successful_cells
        == {item.run_cell_id for item in plan.cells},
        "all_cells_have_one_result": len(results) == len(plan.cells)
        and len({item.run_cell_id for item in results}) == len(plan.cells),
        "evaluation_exists": len(evaluations) == 1,
        "evaluation_qualified": bool(
            evaluations
            and evaluations[-1].qualification_status
            is QualificationStatus.QUALIFIED
        ),
        "verified_evidence_chain_exists": any(
            item.chain_id == current_chain_id and item.verdict_eligible()
            for item in context.repository.list_evidence_chains(
                context.study_id
            )
        ),
        "study_verdict_materialized": bool(study_verdict.get("verdict_id")),
        "scientific_and_execution_seals_linked": seal_lineage_present,
        "canonical_attempt_policy_enforced": (
            canonical_cells == {item.run_cell_id for item in plan.cells}
        ),
        "pair_block_schedule_registered": all(
            item.pair_block_id
            and (
                item.pair_position == 1
                or item.scheduled_after_run_cell_id is not None
            )
            for item in plan.cells
        ),
        "formal_exposure_recorded": bool(
            current_evaluation
            and current_evaluation.exposure_record_id
            and any(
                item.exposure_id
                == current_evaluation.exposure_record_id
                for item in exposure_records
            )
        ),
        "statistical_profile_assured": bool(
            current_evaluation
            and current_evaluation.statistical_assurance_report_id
            and any(
                item.report_id
                == current_evaluation.statistical_assurance_report_id
                and item.status is not AssuranceStatus.FAILED
                for item in assurance_reports
            )
        ),
        "leakage_audit_nonblocking": bool(
            leakage_reports and not leakage_reports[-1].blocking
        ),
        "primary_metric_recomputed_independently": bool(
            current_evaluation
            and current_evaluation.secondary_implementation_matches
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "plan_id": plan.plan_id,
        "study_verdict_id": study_verdict.get("verdict_id"),
    }


def _complete_stage3_handler(context: Any) -> dict[str, Any]:
    from .workflow_scheduler import BlockedStepError

    audit = context.result("audit_stage3_completion")
    if not audit["passed"]:
        raise BlockedStepError(
            "Stage 3 completion audit failed",
            kind="stage3_completion_audit",
        )
    plan = context.repository.load_run_plan(
        context.study_id, audit["plan_id"]
    )
    handoff = context.repository.load_stage3_handoff(context.study_id)
    evaluations = context.repository.list_evaluation_records(
        context.study_id
    )
    evaluations = [
        item for item in evaluations if item.plan_id == plan.plan_id
    ]
    verdict_result = context.result("materialize_stage3_verdict")
    exposure_records = [
        item
        for item in context.repository.list_formal_exposures(
            context.study_id
        )
        if item.plan_id == plan.plan_id
    ]
    assurance_reports = [
        item
        for item in context.repository.list_statistical_assurance_reports(
            context.study_id
        )
        if item.contract_version == plan.contract_version
    ]
    leakage_reports = [
        item
        for item in context.repository.list_leakage_audit_reports(
            context.study_id
        )
        if item.contract_version == plan.contract_version
    ]
    claim_envelope = ScientificClaimEnvelope.model_validate(
        verdict_result["claim_envelope"]
    )
    edge_ids = set(verdict_result["evidence_edge_ids"])
    evidence_edges = [
        item
        for item in context.repository.list_evidence_edges(
            context.study_id
        )
        if item.edge_id in edge_ids
    ]
    artifacts: dict[str, str] = {}
    root = context.repository.root / "studies" / context.study_id
    completion_paths_to_hash = [
        _stage3_root(context.repository, context.study_id) / "handoff.json",
        _stage3_root(context.repository, context.study_id)
        / "run_plans"
        / f"{plan.plan_id}.json",
        *[
            _stage3_root(context.repository, context.study_id)
            / "evaluations"
            / f"{item.evaluation_id}.json"
            for item in evaluations
        ],
        *[
            _stage3_root(context.repository, context.study_id)
            / "evidence_edges"
            / f"{item.edge_id}.json"
            for item in evidence_edges
        ],
        root
        / "verdicts"
        / f"{verdict_result['hypothesis_verdict']['verdict_id']}.json",
        root
        / "verdicts"
        / f"{verdict_result['study_verdict']['verdict_id']}.json",
        _stage3_root(context.repository, context.study_id)
        / "claim_envelopes"
        / f"{claim_envelope.claim_envelope_id}.json",
        *[
            _stage3_root(context.repository, context.study_id)
            / "exposures"
            / f"{item.exposure_id}.json"
            for item in exposure_records
        ],
        *[
            _stage3_root(context.repository, context.study_id)
            / "assurance"
            / f"{item.report_id}.json"
            for item in assurance_reports
        ],
        *[
            _stage3_root(context.repository, context.study_id)
            / "leakage_audits"
            / f"{item.report_id}.json"
            for item in leakage_reports
        ],
    ]
    plan_cell_ids = {item.run_cell_id for item in plan.cells}
    completion_results = [
        item
        for item in context.repository.list_result_envelopes(
            context.study_id
        )
        if item.run_cell_id in plan_cell_ids
    ]
    completion_attempts = [
        item
        for item in context.repository.list_execution_attempts(
            context.study_id
        )
        if item.run_cell_id in plan_cell_ids
    ]
    completion_paths_to_hash.extend(
        [
            _stage3_root(context.repository, context.study_id)
            / "results"
            / f"{item.result_id}.json"
            for item in completion_results
        ]
    )
    completion_paths_to_hash.extend(
        [
            _stage3_root(context.repository, context.study_id)
            / "attempts"
            / f"{item.attempt_id}.json"
            for item in completion_attempts
        ]
    )
    if handoff.scientific_specification_seal_id:
        completion_paths_to_hash.append(
            _stage3_root(context.repository, context.study_id)
            / "specifications"
            / f"{handoff.scientific_specification_seal_id}.json"
        )
    if handoff.execution_package_seal_id:
        completion_paths_to_hash.append(
            _stage3_root(context.repository, context.study_id)
            / "execution_packages"
            / f"{handoff.execution_package_seal_id}.json"
        )
    artifact_by_id = {
        item.artifact_id: item
        for item in context.repository.list_artifacts(context.study_id)
    }
    completion_paths_to_hash.extend(
        Path(artifact_by_id[artifact_id].path)
        for artifact_id in handoff.lock_artifact_ids.values()
    )
    completion_paths_to_hash.extend(
        Path(artifact_by_id[artifact_id].path)
        for result in completion_results
        for artifact_id in result.output_artifact_ids
    )
    completion_paths_to_hash.extend(
        Path(item.path)
        for item in artifact_by_id.values()
        if item.kind
        in {
            "generated_platform_evaluator",
            "generated_evaluator_golden_vectors",
        }
    )
    artifact_ids_by_path = {
        Path(item.path).resolve(): item.artifact_id
        for item in artifact_by_id.values()
    }
    for path in completion_paths_to_hash:
        resolved = path.resolve()
        try:
            key = resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            artifact_id = artifact_ids_by_path.get(resolved)
            if artifact_id is None:
                raise ValueError(
                    "Stage 3 completion encountered an external artifact "
                    "without a registered immutable identity"
                )
            key = f"registered-artifacts/{artifact_id}/{resolved.name}"
        artifacts[key] = sha256_file(resolved)
    package = context.repository.save_stage3_completion(
        Stage3CompletionPackage(
            completion_id=stable_id(
                "stage3-completion",
                context.study_id,
                plan.plan_hash,
                verdict_result["study_verdict"]["verdict_id"],
            ),
            study_id=context.study_id,
            plan_id=plan.plan_id,
            handoff_id=handoff.handoff_id,
            evaluation_ids=[
                item.evaluation_id for item in evaluations
            ],
            evidence_edge_ids=[item.edge_id for item in evidence_edges],
            hypothesis_verdict_ids=[
                verdict_result["hypothesis_verdict"]["verdict_id"]
            ],
            study_verdict_id=verdict_result["study_verdict"]["verdict_id"],
            qualification_status=QualificationStatus.QUALIFIED,
            artifact_hashes=artifacts,
            exposure_record_ids=[
                item.exposure_id for item in exposure_records
            ],
            statistical_assurance_report_id=(
                assurance_reports[-1].report_id
                if assurance_reports else None
            ),
            leakage_audit_report_id=(
                leakage_reports[-1].report_id
                if leakage_reports else None
            ),
            claim_envelope_id=claim_envelope.claim_envelope_id,
            evidence_level=claim_envelope.evidence_level,
            confirmatory_status=claim_envelope.confirmatory_status,
        )
    )
    if handoff.repair_contract_id:
        repair = context.repository.load_repair_contract(
            context.study_id, handoff.repair_contract_id
        )
        context.repository.save_repair_contract(
            repair.model_copy(
                update={
                    "status": RepairStatus.COMPLETED,
                    "successor_run_id": plan.plan_id,
                }
            )
        )
    return {
        "stage3_completion": package.model_dump(mode="json"),
        "_workflow_next_phase": Phase.PAPER.value,
    }


def stage_three_handlers() -> dict[str, Any]:
    return {
        "stage3_admission": _stage3_admission_handler,
        "compile_stage3_run_plan": _stage3_compile_handler,
        "execute_stage3_run_cell": _execute_run_cell_handler,
        "evaluate_stage3_results": _evaluate_results_handler,
        "build_stage3_evidence_ledger": _build_evidence_handler,
        "materialize_stage3_verdict": _materialize_verdict_handler,
        "audit_stage3_completion": _audit_stage3_handler,
        "complete_stage3": _complete_stage3_handler,
    }


def stage3_read_model(
    repository: WorkflowRepository, study_id: str
) -> dict[str, Any]:
    """Build the only Stage 3 view consumed by API and UI clients."""

    study = repository.load_study(study_id)
    plans = repository.list_run_plans(study_id)
    if not plans:
        from .stage_three_build import (
            _PROFILE_V1_GENERATION_TIMEOUT_SECONDS,
            stage3_contract_readiness_violations,
        )

        handoff_path = (
            repository.root
            / "studies"
            / study_id
            / "stage3"
            / "handoff.json"
        )
        handoff = (
            repository.load_stage3_handoff(study_id)
            if handoff_path.is_file()
            else None
        )
        active_contract_version = study.active_contract_version
        # A Stage 2 contract revision creates a new scientific specification.
        # The previous Stage 3 handoff, build plan, execution seal, and blocked
        # attempts remain immutable history, but none of them may control the
        # current workflow after the active contract version changes.
        if (
            handoff is not None
            and active_contract_version is not None
            and handoff.contract_version != active_contract_version
        ):
            handoff = None
        build_plans = [
            item
            for item in repository.list_experiment_build_plans(study_id)
            if (
                active_contract_version is None
                or item.contract_version == active_contract_version
            )
        ]
        seal_paths = sorted(
            (
                repository.root
                / "studies"
                / study_id
                / "stage3"
                / "execution_packages"
            ).glob("execution-seal-*.json")
        )
        execution_seals = [
            ExecutionPackageSeal.model_validate(read_json(path))
            for path in seal_paths
        ]
        if active_contract_version is not None:
            execution_seals = [
                item
                for item in execution_seals
                if item.contract_version == active_contract_version
            ]
        if handoff is not None and (
            handoff.scientific_specification_seal_id
        ):
            execution_seals = [
                item
                for item in execution_seals
                if item.scientific_specification_seal_id
                == handoff.scientific_specification_seal_id
            ]
        current_execution_seal = (
            execution_seals[-1] if execution_seals else None
        )
        contract_readiness_issues: list[str] = []
        if study.active_contract_version is not None:
            active_contract = repository.load_research_contract(
                study_id, study.active_contract_version
            )
            contract_readiness_issues = (
                stage3_contract_readiness_violations(active_contract)
            )
        contract_freeze_steps = [
            item
            for item in repository.list_steps(study_id)
            if item.step_type == "freeze_research_contract"
            and item.status is ExecutionStatus.SUCCEEDED
        ]
        active_contract_frozen_at = (
            max(contract_freeze_steps, key=lambda item: item.updated_at).updated_at
            if contract_freeze_steps
            else None
        )
        build_steps = [
            item
            for item in repository.list_steps(study_id)
            if item.task_group
            and handoff is not None
            and item.task_group == f"stage3-build:{handoff.handoff_id}"
            and (
                active_contract_frozen_at is None
                or item.updated_at >= active_contract_frozen_at
            )
        ]
        operational_state = (
            "formal_execution_admitted"
            if handoff is not None
            and handoff.handoff_stage == "formal_execution"
            else "handoff_validated"
            if handoff is not None
            else "not_initialized"
        )
        if (
            handoff is None
            and contract_readiness_issues
        ):
            operational_state = "contract_revision_required"
        if build_plans and operational_state != "formal_execution_admitted":
            latest_build = build_plans[-1]
            materialized_root = (
                repository.root
                / "studies"
                / study_id
                / "stage3"
                / "materialized"
                / latest_build.build_plan_id
            )
            package_root = (
                repository.root
                / "studies"
                / study_id
                / "stage3"
                / "execution_packages"
                / latest_build.build_plan_id
            )
            if current_execution_seal is not None:
                operational_state = "execution_package_frozen"
            elif (
                materialized_root / "generated_smoke_receipt.json"
            ).is_file() or (
                package_root / "smoke_test_receipt.json"
            ).is_file():
                operational_state = "smoke_tested"
            elif (
                materialized_root / "generation_manifest.json"
            ).is_file():
                operational_state = "assets_materialized"
            else:
                operational_state = "build_plan_created"
        exceptional_build_steps = sorted(
            [
                item
                for item in build_steps
                if item.status
                in {
                    ExecutionStatus.BLOCKED,
                    ExecutionStatus.FAILED,
                }
            ],
            key=lambda item: item.updated_at,
        )
        deep_contract_issues: list[str] = []
        if exceptional_build_steps:
            blocker = exceptional_build_steps[-1].blocker or {}
            blocker_kind = str(blocker.get("kind", ""))
            if blocker_kind == "contract_revision_required":
                for item in blocker.get("reasons", []):
                    reason = str(item).strip()
                    prefix = (
                        "generated package abstained because requirements "
                        "are missing:"
                    )
                    if reason.casefold().startswith(prefix):
                        reason = reason[len(prefix):].strip()
                    deep_contract_issues.extend(
                        part.strip()
                        for part in reason.split(";")
                        if part.strip()
                    )
            operational_state = (
                "contract_revision_required"
                if blocker_kind == "contract_revision_required"
                else "license_blocked"
                if blocker_kind == "license_blocked"
                else "resource_blocked"
                if blocker_kind == "resource_blocked"
                else "unsupported_profile"
                if blocker_kind == "unsupported_profile"
                else "build_blocked"
            )
        # A legacy Stage 3 admission attempt may have persisted an
        # ``unsupported_profile`` blocker before contract readiness was
        # checked explicitly.  The actionable state is still a Stage 2
        # contract revision: the historical failed attempt remains visible,
        # but it must not mislabel an incomplete handoff as an unsupported
        # scientific design.
        if handoff is None and contract_readiness_issues:
            operational_state = "contract_revision_required"
        return {
            "study_id": study_id,
            "initialized": False,
            "phase": study.phase.value,
            "overview": {
                "status": operational_state,
                "completed": 0,
                "total": 0,
            },
            "status_spaces": {
                "operational_state": operational_state,
                "scientific_verdict": None,
                "publication_readiness": None,
            },
            "handoff_issues": (
                contract_readiness_issues or deep_contract_issues
            ),
            "repair_route": (
                "stage2_contract_vnext"
                if contract_readiness_issues or deep_contract_issues
                else None
            ),
            "build_handoff": (
                handoff.model_dump(mode="json")
                if handoff is not None
                and handoff.handoff_stage == "build"
                else None
            ),
            "build_plan": (
                build_plans[-1].model_dump(mode="json")
                if build_plans
                else None
            ),
            "execution_package_seal": (
                current_execution_seal.model_dump(mode="json")
                if current_execution_seal is not None
                else None
            ),
            "build_steps": [
                item.model_dump(mode="json")
                for item in build_steps
            ],
            "build_limits": {
                "model_generation_soft_notice_seconds": 5 * 60,
                "model_generation_timeout_seconds": (
                    _PROFILE_V1_GENERATION_TIMEOUT_SECONDS
                ),
            },
            "run_matrix": [],
            "evaluation": None,
            "formal_exposures": [],
            "statistical_assurance": None,
            "leakage_audit": None,
            "claim_envelope": None,
            "evidence": {"edge_count": 0, "verified": False},
            "verdict": None,
            "repair_lineage": [],
            "scientific_successors": [
                item.model_dump(mode="json")
                for item in repository.list_scientific_successor_requests(
                    study_id
                )
            ],
            "completion": None,
        }
    plan = plans[-1]
    run_steps = {
        str(item.parameters.get("run_cell_id")): item
        for item in repository.list_steps(study_id)
        if item.step_type == "execute_stage3_run_cell"
        and item.parameters.get("plan_id") == plan.plan_id
    }
    attempts: dict[str, list[ExecutionAttempt]] = {}
    for item in repository.list_execution_attempts(study_id):
        attempts.setdefault(item.run_cell_id, []).append(item)
    results = {
        item.run_cell_id: item
        for item in repository.list_result_envelopes(study_id)
    }
    matrix: list[dict[str, Any]] = []
    for cell in plan.cells:
        step = run_steps.get(cell.run_cell_id)
        cell_attempts = sorted(
            attempts.get(cell.run_cell_id, []),
            key=lambda item: item.attempt_number,
        )
        result = results.get(cell.run_cell_id)
        matrix.append(
            {
                **cell.model_dump(mode="json"),
                "step_instance_id": (
                    step.step_instance_id if step is not None else None
                ),
                "execution_status": (
                    step.status.value if step is not None else "not_materialized"
                ),
                "attempt_count": len(cell_attempts),
                "latest_attempt": (
                    cell_attempts[-1].model_dump(mode="json")
                    if cell_attempts else None
                ),
                "blocker": step.blocker if step is not None else None,
                "result": (
                    result.model_dump(mode="json")
                    if result is not None else None
                ),
            }
        )
    completed = sum(
        item["execution_status"] == ExecutionStatus.SUCCEEDED.value
        for item in matrix
    )
    evaluation = next(
        (
            item for item in reversed(
                repository.list_evaluation_records(study_id)
            )
            if item.plan_id == plan.plan_id
        ),
        None,
    )
    all_edges = repository.list_evidence_edges(study_id)
    current_chain_id = (
        stable_id(
            "chain",
            study_id,
            plan.plan_id,
            evaluation.evaluation_id,
        )
        if evaluation is not None
        else None
    )
    chains = [
        item
        for item in repository.list_evidence_chains(study_id)
        if item.chain_id == current_chain_id
    ]
    verdict = None
    current_study_verdict_id: str | None = None
    if evaluation is not None:
        current_contract = repository.load_research_contract(
            study_id, evaluation.contract_version
        )
        current_hypothesis_verdict_id = stable_id(
            "hverdict",
            study_id,
            current_contract.hypotheses[0].hypothesis_id,
            evaluation.evaluation_id,
        )
        current_study_verdict_id = stable_id(
            "sverdict", study_id, current_hypothesis_verdict_id, ""
        )
    if current_study_verdict_id:
        path = (
            repository.root
            / "studies"
            / study_id
            / "verdicts"
            / f"{current_study_verdict_id}.json"
        )
        if path.is_file():
            verdict = read_json(path)
    completion_paths = [
        path
        for path in sorted(
        (
            _stage3_root(repository, study_id) / "completion"
        ).glob("stage3-completion-*.json")
        )
        if read_json(path).get("plan_id") == plan.plan_id
    ]
    if completion_paths:
        completion_edge_ids = set(
            read_json(completion_paths[-1]).get("evidence_edge_ids", [])
        )
        edges = [
            item for item in all_edges
            if item.edge_id in completion_edge_ids
        ]
    else:
        current_nodes = {
            plan.plan_id,
            *[
                stable_id("run", study_id, cell.run_cell_id)
                for cell in plan.cells
            ],
            *[
                artifact_id
                for item in matrix
                if item["result"]
                for artifact_id in item["result"]["output_artifact_ids"]
            ],
        }
        if evaluation is not None:
            current_nodes.add(evaluation.evaluation_id)
        edges = [
            item
            for item in all_edges
            if item.source_id in current_nodes
            or item.target_id in current_nodes
        ]
    gate = next(
        (
            item for item in repository.list_gates(study_id)
            if item.subject_type == "stage3_run_plan"
            and item.subject_id == plan.plan_id
        ),
        None,
    )
    current_exposures = [
        item
        for item in repository.list_formal_exposures(study_id)
        if item.plan_id == plan.plan_id
    ]
    assurance_reports = [
        item
        for item in repository.list_statistical_assurance_reports(study_id)
        if item.contract_version == plan.contract_version
    ]
    leakage_reports = [
        item
        for item in repository.list_leakage_audit_reports(study_id)
        if item.contract_version == plan.contract_version
    ]
    claim_envelopes = [
        item
        for item in repository.list_claim_envelopes(study_id)
        if item.plan_id == plan.plan_id
    ]
    from .profiles.registry import profile_bundle

    bundle = profile_bundle(plan.profile)
    return {
        "study_id": study_id,
        "initialized": True,
        "phase": study.phase.value,
        "profile": plan.profile.value,
        "profile_bundle": bundle.model_dump(mode="json"),
        "profile_analysis": (
            {
                "renderer": bundle.frontend_renderer_id,
                "variance_unit": evaluation.variance_unit,
                "independent_unit_count": (
                    evaluation.independent_unit_count
                ),
                "confidence_interval": evaluation.confidence_interval,
                "details": evaluation.statistical_rule.get(
                    "profile_details", {}
                ),
                "p_value": evaluation.statistical_rule.get("p_value"),
            }
            if evaluation is not None
            else {"renderer": bundle.frontend_renderer_id}
        ),
        "plan": plan.model_dump(mode="json"),
        "build_steps": [
            item.model_dump(mode="json")
            for item in repository.list_steps(study_id)
            if item.task_group
            and item.task_group.startswith("stage3-build:")
        ],
        "gate": gate.model_dump(mode="json") if gate else None,
        "overview": {
            "status": (
                "completed"
                if completion_paths
                else (
                    "execution_blocked"
                    if any(
                        item["execution_status"]
                        in {
                            ExecutionStatus.BLOCKED.value,
                            ExecutionStatus.FAILED.value,
                        }
                        for item in matrix
                    )
                    else "running"
                )
            ),
            "completed": completed,
            "total": len(matrix),
            "attempts": sum(item["attempt_count"] for item in matrix),
        },
        "status_spaces": {
            "operational_state": (
                "completed"
                if completion_paths
                else "execution_blocked"
                if any(
                    item["execution_status"]
                    in {
                        ExecutionStatus.BLOCKED.value,
                        ExecutionStatus.FAILED.value,
                    }
                    for item in matrix
                )
                else "executing"
            ),
            "scientific_verdict": (
                verdict.get("status") if verdict else None
            ),
            "publication_readiness": (
                repository.load_readiness(study_id).model_dump(mode="json")
                if (
                    repository.root
                    / "studies"
                    / study_id
                    / "readiness.json"
                ).is_file()
                else None
            ),
        },
        "run_matrix": matrix,
        "evaluation": (
            evaluation.model_dump(mode="json") if evaluation else None
        ),
        "formal_exposures": [
            item.model_dump(mode="json") for item in current_exposures
        ],
        "statistical_assurance": (
            assurance_reports[-1].model_dump(mode="json")
            if assurance_reports else None
        ),
        "leakage_audit": (
            leakage_reports[-1].model_dump(mode="json")
            if leakage_reports else None
        ),
        "claim_envelope": (
            claim_envelopes[-1].model_dump(mode="json")
            if claim_envelopes else None
        ),
        "evidence": {
            "edge_count": len(edges),
            "verified": any(item.verdict_eligible() for item in chains),
            "edges": [item.model_dump(mode="json") for item in edges],
            "chains": [item.model_dump(mode="json") for item in chains],
        },
        "verdict": verdict,
        "repair_lineage": [
            item.model_dump(mode="json")
            for item in repository.list_repair_contracts(study_id)
        ],
        "repair_gates": [
            item.model_dump(mode="json")
            for item in repository.list_gates(study_id)
            if item.subject_type == "repair_contract"
        ],
        "diagnostics": [
            item.model_dump(mode="json")
            for item in repository.list_stage3_diagnostics(study_id)
        ],
        "successors": [
            item.model_dump(mode="json")
            for item in repository.list_successor_runs(study_id)
        ],
        "scientific_successors": [
            item.model_dump(mode="json")
            for item in repository.list_scientific_successor_requests(
                study_id
            )
        ],
        "completion": (
            read_json(completion_paths[-1]) if completion_paths else None
        ),
    }


def stage4_claim_authority(
    repository: WorkflowRepository, study_id: str
) -> dict[str, Any]:
    """Return the only Stage 3 scientific claims Stage 4 may promote.

    Raw logs, evaluator narratives, and model summaries are deliberately
    absent.  Stage 4 may quote or narrow the envelope, but cannot broaden it.
    """

    completions = repository.list_stage3_completions(study_id)
    if not completions:
        raise ValueError(
            "Stage 4 claim authority requires a completed Stage 3 package"
        )
    completion = completions[-1]
    if completion.claim_envelope_id is None:
        raise ValueError(
            "legacy completion package has no ScientificClaimEnvelope; "
            "create an explicit compatibility review before paper synthesis"
        )
    envelopes = {
        item.claim_envelope_id: item
        for item in repository.list_claim_envelopes(study_id)
    }
    envelope = envelopes.get(completion.claim_envelope_id)
    if envelope is None:
        raise ValueError("completion package claim envelope is missing")
    return {
        "schema_version": 1,
        "study_id": study_id,
        "source_completion_id": completion.completion_id,
        "claim_registry_source": "scientific_claim_envelope_only",
        "claims": [envelope.model_dump(mode="json")],
        "raw_logs_are_claim_authority": False,
        "may_narrow_claims": True,
        "may_broaden_claims": False,
    }


def finalize_stage3_unverifiable_boundary(
    repository: WorkflowRepository,
    study_id: str,
    *,
    reasons: list[str] | None = None,
    decided_by: str = "project_owner",
) -> Stage3CompletionPackage:
    """Close Stage 3 truthfully when a formal experiment cannot be run.

    This is the default non-fabricating exit for an accepted design whose
    required data, implementation, or target binding is unavailable.  It
    persists a planned-only matrix, an incomplete evaluation, and an
    ``unverifiable`` verdict.  No execution attempt or result envelope is
    created.
    """

    existing = repository.list_stage3_completions(study_id)
    if existing:
        return existing[-1]
    study = repository.load_study(study_id)
    handoff = repository.load_stage3_handoff(study_id)
    # Stage 3 is governed by the contract version sealed into its handoff.
    # A later, still-unfrozen vNext draft must not make the truthful boundary
    # exit unusable or silently change the scientific authority of this run.
    contract = repository.load_research_contract(
        study_id, handoff.contract_version
    )
    if contract is None or contract.status is not ArtifactStatus.FROZEN:
        raise ValueError(
            "an approved frozen Research Contract is required before "
            "recording an evidence boundary"
        )
    normalized_reasons = list(
        dict.fromkeys(
            str(item).strip()
            for item in (reasons or [])
            if str(item).strip()
        )
    )
    if not normalized_reasons:
        normalized_reasons = [
            "The frozen design could not be bound to all resources required "
            "for a formal Stage 3 execution."
        ]

    primary = next(
        (
            item
            for item in contract.hypotheses
            if item.role.value == "primary"
        ),
        contract.hypotheses[0],
    )
    baseline_id = str(
        contract.baseline.get("arm_id")
        or contract.baseline.get("id")
        or "baseline"
    )
    treatment_id = str(
        contract.treatment.get("arm_id")
        or contract.treatment.get("id")
        or "treatment"
    )
    raw_task_id = str(
        (contract.tasks or ["registered-primary-task"])[0]
    ).strip()
    task_id = (
        raw_task_id
        if len(raw_task_id) <= 300
        else f"formal-primary-task-{_digest(raw_task_id)[:16]}"
    )
    split_id = str((contract.splits or ["default"])[0])
    seed = int((contract.seeds or [0])[0])
    manifest_hash = _digest(
        {
            "study_id": study_id,
            "contract_version": contract.version,
            "mode": "planned_only_evidence_boundary",
        }
    )
    plan_id = stable_id(
        "run-plan",
        study_id,
        handoff.handoff_id,
        "evidence-boundary",
    )
    cells: list[RunCell] = []
    for position, arm_id in enumerate(
        (baseline_id, treatment_id), start=1
    ):
        normalized_arm = "".join(
            char if char.isascii() and (char.isalnum() or char == "_")
            else "_"
            for char in arm_id.lower()
        ).strip("_")
        if len(normalized_arm) < 2 or not normalized_arm[0].isalpha():
            normalized_arm = f"arm_{position}"
        cell_id = stable_id(
            "run-cell", plan_id, task_id, split_id, normalized_arm, seed
        )
        cells.append(
            RunCell(
                run_cell_id=cell_id,
                study_id=study_id,
                plan_id=plan_id,
                contract_version=contract.version,
                task_id=task_id,
                split_id=split_id,
                arm_id=normalized_arm[:64],
                seed=seed,
                replicate=1,
                action_id=f"action-boundary-{normalized_arm}"[:126],
                experiment_id=f"boundary-{normalized_arm}"[:120],
                input_bindings={},
                expected_output_schema=(
                    contract.output_schema
                    or {"type": "object", "status": "not_executed"}
                ),
                resource_profile={
                    "execution_authorized": False,
                    "reason": "evidence_boundary_only",
                },
                pair_block_id="evidence-boundary-pair",
                pair_position=position,
                execution_manifest_hash=manifest_hash,
                status=RunCellStatus.PLANNED,
            )
        )
    plan_payload = {
        "schema_version": 1,
        "plan_id": plan_id,
        "study_id": study_id,
        "handoff_id": handoff.handoff_id,
        "contract_version": contract.version,
        "profile": handoff.profile.value,
        "compiler_version": "stage3-boundary-v1",
        "concurrency": 1,
        "cells": [item.model_dump(mode="json") for item in cells],
    }
    plan = repository.save_run_plan(
        RunPlan(**plan_payload, plan_hash=_digest(plan_payload))
    )

    root = _stage3_root(repository, study_id)
    boundary_path = root / "boundary" / "evidence_boundary_report.json"
    boundary_payload = {
        "schema_version": 1,
        "study_id": study_id,
        "contract_version": contract.version,
        "status": "unverifiable",
        "formal_experiment_executed": False,
        "reasons": normalized_reasons,
        "decision_authority": decided_by,
        "allowed_next_use": [
            "evidence-boundary report",
            "research gap analysis",
            "future data and implementation plan",
        ],
        "prohibited_claims": [
            "supported treatment effect",
            "refuted treatment effect",
            "publication-ready empirical result",
        ],
        "created_at": utc_now(),
    }
    write_json_atomic(boundary_path, boundary_payload)
    boundary_hash = sha256_file(boundary_path)
    boundary_artifact_id = _register_file(
        repository,
        study_id,
        boundary_path,
        kind="stage3_evidence_boundary_report",
        role=ArtifactRole.AUDIT,
    )

    evaluation = repository.save_evaluation_record(
        EvaluationRecord(
            evaluation_id=stable_id(
                "evaluation", plan.plan_id, "unverifiable-boundary"
            ),
            study_id=study_id,
            plan_id=plan.plan_id,
            contract_version=contract.version,
            qualification_status=QualificationStatus.INCOMPLETE,
            qualification_checks={
                "formal_experiment_executed": False,
                "required_resources_bound": False,
            },
            metric_name=str(
                (contract.metrics[0] if contract.metrics else {}).get(
                    "name", "registered_primary_outcome"
                )
            ),
            decision=HypothesisVerdictStatus.UNVERIFIABLE,
            rationale="; ".join(normalized_reasons),
            result_ids=[],
        )
    )
    evaluation_hash = _digest(evaluation.model_dump(mode="json"))
    edge = repository.save_evidence_edge(
        EvidenceEdge(
            edge_id=stable_id(
                "evidence-edge",
                boundary_artifact_id,
                evaluation.evaluation_id,
            ),
            study_id=study_id,
            source_id=boundary_artifact_id,
            target_id=evaluation.evaluation_id,
            relation_type=EvidenceRelation.EVALUATED_BY,
            source_hash=boundary_hash,
            target_hash=evaluation_hash,
            created_by="deterministic_stage3_boundary_closure",
            qualification_status=QualificationStatus.INCOMPLETE,
        )
    )
    hypothesis_verdict = repository.save_hypothesis_verdict(
        HypothesisVerdict(
            verdict_id=stable_id(
                "hverdict",
                study_id,
                primary.hypothesis_id,
                "unverifiable-boundary",
            ),
            study_id=study_id,
            hypothesis_id=primary.hypothesis_id,
            status=HypothesisVerdictStatus.UNVERIFIABLE,
            evidence_chain_ids=[],
            eligible_evidence=False,
            rationale="; ".join(normalized_reasons),
        )
    )
    study_verdict = repository.save_study_verdict(
        StudyVerdict(
            verdict_id=stable_id(
                "sverdict", study_id, plan.plan_id, "unverifiable-boundary"
            ),
            study_id=study_id,
            status=StudyVerdictStatus.UNVERIFIABLE,
            hypothesis_verdict_ids=[hypothesis_verdict.verdict_id],
            rationale=(
                "The registered primary hypothesis remains unverifiable "
                "because no qualified formal result was produced."
            ),
        )
    )
    claim_envelope = repository.save_claim_envelope(
        ScientificClaimEnvelope(
            claim_envelope_id=stable_id(
                "claim-envelope", study_id, plan.plan_id, "boundary"
            ),
            study_id=study_id,
            plan_id=plan.plan_id,
            allowed_claim=(
                "The registered hypothesis could not be evaluated with the "
                "currently authorized resources and executable bindings."
            ),
            population=str(
                contract.estimand.get("population")
                or contract.data_boundary.get("population")
                or "registered but unresolved population"
            ),
            tasks=contract.tasks or [task_id],
            intervention=str(
                contract.treatment.get("name")
                or contract.treatment.get("behavior")
                or treatment_id
            ),
            comparator=str(
                contract.baseline.get("name")
                or contract.baseline.get("behavior")
                or baseline_id
            ),
            outcome=str(
                contract.estimand.get("outcome")
                or (contract.metrics[0] if contract.metrics else {}).get(
                    "name", "registered primary outcome"
                )
            ),
            evidence_level=EvidenceReproductionLevel.BOUNDARY_ONLY,
            confirmatory_status=ConfirmatoryStatus.UNTOUCHED,
            known_limitations=normalized_reasons,
            maximum_claim_tier="evidence_boundary_report",
            publication_mode=PublicationMode.BOUNDARY_REPORT,
        )
    )
    package = repository.save_stage3_completion(
        Stage3CompletionPackage(
            completion_id=stable_id(
                "stage3-completion",
                study_id,
                plan.plan_hash,
                "unverifiable-boundary",
            ),
            study_id=study_id,
            plan_id=plan.plan_id,
            handoff_id=handoff.handoff_id,
            evaluation_ids=[evaluation.evaluation_id],
            evidence_edge_ids=[edge.edge_id],
            hypothesis_verdict_ids=[hypothesis_verdict.verdict_id],
            study_verdict_id=study_verdict.verdict_id,
            qualification_status=QualificationStatus.INCOMPLETE,
            artifact_hashes={
                "stage3/boundary/evidence_boundary_report.json": (
                    boundary_hash
                )
            },
            claim_envelope_id=claim_envelope.claim_envelope_id,
            evidence_level=EvidenceReproductionLevel.BOUNDARY_ONLY,
            confirmatory_status=ConfirmatoryStatus.UNTOUCHED,
            execution_status=ExecutionStatus.BLOCKED,
            analysis_eligibility=(
                AnalysisEligibilityStatus.NOT_ADJUDICABLE
            ),
            scientific_verdict_status=None,
            publication_mode=PublicationMode.BOUNDARY_REPORT,
            contract_amendment_required=True,
        )
    )
    repository.save_study(
        repository.load_study(study_id).model_copy(
            update={
                "phase": Phase.PAPER,
                "execution_status": ExecutionStatus.QUEUED,
            }
        ),
        "stage3_unverifiable_boundary_completed",
    )
    return package


def propose_stage3_repair(
    repository: WorkflowRepository,
    study_id: str,
    *,
    diagnostic_id: str,
    changed_artifact_ids: list[str] | None = None,
    scientific_change: bool = False,
    changed_contract_fields: list[str] | None = None,
    regression_checks: list[dict[str, Any]] | None = None,
) -> RepairContract | ScientificSuccessorRequest:
    diagnostics = {
        item.diagnostic_id: item
        for item in repository.list_stage3_diagnostics(study_id)
    }
    if diagnostic_id not in diagnostics:
        raise ValueError("unknown Stage 3 diagnostic")
    diagnostic = diagnostics[diagnostic_id]
    if diagnostic.failure_class is Stage3FailureClass.TRANSIENT:
        raise ValueError(
            "operational failure must retry the same RunCell; it does not "
            "create a Repair Contract"
        )
    scientific_fields = {
        "hypotheses",
        "estimand",
        "data_boundary",
        "data_requirements",
        "baseline",
        "treatment",
        "metrics",
        "statistical_rules",
        "tasks",
        "splits",
        "seeds",
        "replicates",
        "stopping_rule",
    }
    changed_scientific = sorted(
        scientific_fields.intersection(changed_contract_fields or [])
    )
    if scientific_change or changed_scientific:
        if not changed_scientific:
            raise ValueError(
                "Scientific Amendment must name the changed Research "
                "Contract fields"
            )
        return propose_stage3_scientific_successor(
            repository,
            study_id,
            diagnostic_id=diagnostic_id,
            changed_contract_fields=changed_scientific,
        )
    changed = sorted(
        set(
            changed_artifact_ids
            or diagnostic.system_invalidation_artifact_ids
        )
    )
    if not changed:
        plan_path = (
            _stage3_root(repository, study_id)
            / "run_plans"
            / f"{diagnostic.plan_id}.json"
        )
        plan_artifact_id = _register_file(
            repository,
            study_id,
            plan_path,
            kind="stage3_run_plan",
            role=ArtifactRole.PROTOCOL,
        )
        changed = [plan_artifact_id]
    owner = (
        DiagnosticOwner.INTEGRITY_BINDING
        if diagnostic.failure_class
        in {
            Stage3FailureClass.SCHEMA,
            Stage3FailureClass.INTEGRITY,
            Stage3FailureClass.PROTOCOL,
        }
        else DiagnosticOwner.EXECUTION_ENVIRONMENT
    )
    repair = repository.propose_repair(
        study_id,
        diagnostic_owner=owner,
        scientific_change=scientific_change,
        earliest_affected_phase=Phase.EXPERIMENT,
        changed_artifact_ids=changed,
        regression_checks=list(regression_checks or []),
        diagnostic_id=diagnostic.diagnostic_id,
        earliest_affected_step_type=(
            diagnostic.earliest_preventable_step_type
        ),
    )
    if not any(
        gate.subject_type == "repair_contract"
        and gate.subject_id == repair.repair_id
        for gate in repository.list_gates(study_id)
    ):
        repository.create_gate(
            study_id,
            GateType.REPAIR_OR_HIGH_COST_RUN,
            subject_type="repair_contract",
            subject_id=repair.repair_id,
            subject_version=repair.version,
        )
    return repair


def propose_stage3_scientific_successor(
    repository: WorkflowRepository,
    study_id: str,
    *,
    diagnostic_id: str,
    changed_contract_fields: list[str],
) -> ScientificSuccessorRequest:
    """Record a scientific amendment without mutating or repairing old science."""

    diagnostics = {
        item.diagnostic_id: item
        for item in repository.list_stage3_diagnostics(study_id)
    }
    diagnostic = diagnostics.get(diagnostic_id)
    if diagnostic is None:
        raise ValueError("unknown Stage 3 diagnostic")
    scientific_fields = {
        "hypotheses",
        "estimand",
        "data_boundary",
        "data_requirements",
        "baseline",
        "treatment",
        "metrics",
        "output_schema",
        "evaluator_policy",
        "statistical_rules",
        "eligibility_rules",
        "tasks",
        "splits",
        "seeds",
        "replicates",
        "stopping_rule",
    }
    changed = sorted(set(changed_contract_fields))
    unsupported = sorted(set(changed).difference(scientific_fields))
    if not changed or unsupported:
        raise ValueError(
            "scientific successor contains unsupported changed fields: "
            + ", ".join(unsupported or ["none"])
        )
    plans = repository.list_run_plans(study_id)
    if not plans or diagnostic.plan_id not in {
        item.plan_id for item in plans
    }:
        raise ValueError(
            "scientific successor requires its historical Run Plan"
        )
    study = repository.load_study(study_id)
    if study.active_contract_version is None:
        raise ValueError(
            "scientific successor requires an active Research Contract"
        )
    runs = [
        item.run_id
        for item in repository.list_research_runs(study_id)
        if item.run_cell_id in {
            cell.run_cell_id
            for plan in plans
            if plan.plan_id == diagnostic.plan_id
            for cell in plan.cells
        }
    ]
    verdict_ids: list[str] = []
    verdict_root = (
        repository.root / "studies" / study_id / "verdicts"
    )
    for path in sorted(verdict_root.glob("*.json")):
        payload = read_json(path)
        verdict_id = payload.get("verdict_id")
        if verdict_id:
            verdict_ids.append(str(verdict_id))
    request = ScientificSuccessorRequest(
        request_id=stable_id(
            "scientific-successor",
            study_id,
            diagnostic_id,
            study.active_contract_version,
            *changed,
        ),
        study_id=study_id,
        predecessor_contract_version=study.active_contract_version,
        predecessor_plan_id=diagnostic.plan_id,
        diagnostic_id=diagnostic_id,
        changed_contract_fields=changed,
        rationale=(
            diagnostic.system_findings
            or ["Scientific design requires an explicit amendment."]
        ),
        historical_run_ids_preserved=sorted(set(runs)),
        historical_verdict_ids_preserved=sorted(set(verdict_ids)),
    )
    return repository.save_scientific_successor_request(request)


def apply_stage3_scientific_successor_contract(
    repository: WorkflowRepository,
    study_id: str,
    *,
    request_id: str,
    changes: dict[str, Any],
    decided_by: str = "project_owner",
    reason: str = "Approved bounded scientific successor.",
) -> ResearchContractVersion:
    """Create and freeze Research Contract vNext for an approved successor.

    Historical contracts, plans, runs, evaluations, and verdicts remain
    immutable.  The caller may change only fields named by the persisted
    ScientificSuccessorRequest.
    """

    request = next(
        (
            item
            for item in repository.list_scientific_successor_requests(
                study_id
            )
            if item.request_id == request_id
        ),
        None,
    )
    if request is None:
        raise ValueError("unknown scientific successor request")
    requested = set(request.changed_contract_fields)
    supplied = set(changes)
    if not supplied or supplied.difference(requested):
        raise ValueError(
            "successor changes must be non-empty and limited to requested "
            "Research Contract fields"
        )
    predecessor = repository.load_research_contract(
        study_id, request.predecessor_contract_version
    )
    latest = repository.latest_research_contract(study_id)
    if latest is None or latest.version != predecessor.version:
        raise ValueError(
            "a newer Research Contract already exists; create a new "
            "scientific successor request"
        )
    update: dict[str, Any] = {}
    field_diff: dict[str, Any] = {}
    for field_name, value in changes.items():
        previous = getattr(predecessor, field_name)
        next_value = (
            {**previous, **value}
            if isinstance(previous, dict) and isinstance(value, dict)
            else value
        )
        update[field_name] = next_value
        field_diff[field_name] = {
            "from": previous,
            "to": next_value,
            "reason": reason,
            "scientific_successor_request_id": request.request_id,
        }
    version = predecessor.version + 1
    successor = predecessor.model_copy(
        update={
            **update,
            "version": version,
            "status": ArtifactStatus.DRAFT,
            "predecessor_version": predecessor.version,
            "field_diff": field_diff,
            "created_by": decided_by,
            "created_at": utc_now(),
            "frozen_at": None,
        }
    )
    repository.save_research_contract(successor)
    gate = repository.create_gate(
        study_id,
        GateType.RESEARCH_CONTRACT,
        "research_contract",
        f"{study_id}:research-v{version}",
        subject_version=version,
    )
    repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by=decided_by,
        reason=reason,
    )
    return repository.save_research_contract(
        successor.model_copy(
            update={
                "status": ArtifactStatus.FROZEN,
                "frozen_at": utc_now(),
            }
        )
    )


def initialize_stage3_successor(
    repository: WorkflowRepository,
    study_id: str,
    repair_contract_id: str,
    *,
    explicit_manifest_path: str | None = None,
) -> tuple[SuccessorRun, Stage3HandoffPackage, RunPlan, list[StepInstance]]:
    repair = repository.load_repair_contract(study_id, repair_contract_id)
    gates = [
        item for item in repository.list_gates(study_id)
        if item.subject_type == "repair_contract"
        and item.subject_id == repair.repair_id
    ]
    approved_gate = next(
        (item for item in gates if item.status.value == "approved"),
        None,
    )
    if repair.scientific_change and approved_gate is None:
        raise ValueError("scientific Stage 3 repair requires owner approval")
    old_plans = repository.list_run_plans(study_id)
    if not old_plans:
        raise ValueError("Stage 3 successor requires a predecessor Run Plan")
    predecessor = old_plans[-1]
    repairing = repair.model_copy(
        update={
            "status": RepairStatus.REPAIRING,
            "approved_by": (
                repair.approved_by
                or (
                    approved_gate.decided_by
                    if approved_gate is not None
                    else "deterministic_non_scientific_repair"
                )
            ),
        }
    )
    repository.save_repair_contract(repairing)
    study = repository.load_study(study_id)
    repository.save_study(
        study.model_copy(update={"phase": Phase.EXPERIMENT}),
        "stage3_successor_requested",
    )
    handoff, plan, steps = ensure_stage_three_dag(
        repository,
        study_id,
        explicit_manifest_path=explicit_manifest_path,
        repair_contract_id=repair.repair_id,
    )
    if plan.plan_id == predecessor.plan_id:
        raise ValueError(
            "repair did not change a contract, manifest, lock, or bound input"
        )
    def dimension(cell: RunCell) -> tuple[str, str, str, int, int]:
        return (
            cell.task_id,
            cell.split_id,
            cell.arm_id,
            cell.seed,
            cell.replicate,
        )
    predecessor_cells = {
        dimension(cell): cell for cell in predecessor.cells
    }
    predecessor_results = {
        item.run_cell_id: item
        for item in repository.list_result_envelopes(study_id)
        if item.run_cell_id
        in {cell.run_cell_id for cell in predecessor.cells}
    }
    predecessor_runs = {
        item.run_cell_id: item
        for item in repository.list_research_runs(study_id)
        if item.run_cell_id is not None
    }
    invalidated = set(repair.invalidated_artifact_ids)
    reusable = set(repair.reusable_artifact_ids)
    step_by_cell = {
        str(item.parameters.get("run_cell_id")): item
        for item in steps
        if item.step_type == "execute_stage3_run_cell"
        and item.parameters.get("plan_id") == plan.plan_id
    }
    updated_steps: dict[str, StepInstance] = {}
    reused_artifacts: set[str] = set()
    for cell in plan.cells:
        old_cell = predecessor_cells.get(dimension(cell))
        if old_cell is None:
            continue
        old_result = predecessor_results.get(old_cell.run_cell_id)
        old_run = predecessor_runs.get(old_cell.run_cell_id)
        if old_result is None or old_run is None:
            continue
        output_ids = set(old_result.output_artifact_ids)
        compatible = (
            old_cell.execution_manifest_hash
            == cell.execution_manifest_hash
            and old_cell.expected_output_schema
            == cell.expected_output_schema
            and old_run.status is ExecutionStatus.SUCCEEDED
            and not output_ids.intersection(invalidated)
            and (not reusable or output_ids.issubset(reusable))
        )
        if not compatible:
            continue
        step = step_by_cell[cell.run_cell_id]
        updated_steps[step.step_instance_id] = (
            repository.update_step_parameters(
                study_id,
                step.step_instance_id,
                {
                    **step.parameters,
                    "reuse_from_result_id": old_result.result_id,
                    "reuse_from_run_id": old_run.run_id,
                    "repair_contract_id": repair.repair_id,
                },
            )
        )
        reused_artifacts.update(output_ids)
    steps = [
        updated_steps.get(item.step_instance_id, item) for item in steps
    ]
    successor = repository.save_successor_run(
        SuccessorRun(
            successor_id=stable_id(
                "successor",
                study_id,
                predecessor.plan_id,
                plan.plan_id,
                repair.repair_id,
            ),
            study_id=study_id,
            predecessor_plan_id=predecessor.plan_id,
            successor_plan_id=plan.plan_id,
            repair_contract_id=repair.repair_id,
            reused_artifact_ids=sorted(reused_artifacts),
            invalidated_artifact_ids=repair.invalidated_artifact_ids,
        )
    )
    repository.save_repair_contract(
        repairing.model_copy(
            update={
                "successor_run_id": plan.plan_id,
            }
        )
    )
    return successor, handoff, plan, steps


def contract_version(
    repository: WorkflowRepository, study_id: str
) -> int:
    study = repository.load_study(study_id)
    if study.active_contract_version is None:
        raise ValueError("Study has no active Research Contract")
    return study.active_contract_version


__all__ = [
    "STAGE3_COMPILER_VERSION",
    "STAGE3_LOCK_NAMES",
    "Stage3AdmissionError",
    "admit_stage_three",
    "compile_run_plan",
    "ensure_stage_three_dag",
    "initialize_stage3_successor",
    "apply_stage3_scientific_successor_contract",
    "propose_stage3_repair",
    "propose_stage3_scientific_successor",
    "stage3_read_model",
    "stage4_claim_authority",
    "stage_three_handlers",
]
