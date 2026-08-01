"""Versioned Study workflow domain for Research Forge.

This module is the source of truth for the v2 workflow model.  The legacy
``models.Stage`` enum remains a read-only compatibility projection.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict, deque
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now
from .storage import append_jsonl, load_jsonl, read_json, sha256_file, write_json_atomic


_ID_COMPONENT = r"[a-z0-9][a-z0-9-]{1,79}"
_SECRET_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "secrets.json",
}
_SECRET_FRAGMENTS = ("api-key", "apikey", "credential", "private-key", "secret")


class Phase(StrEnum):
    DISCOVERY = "discovery"
    PROTOCOL = "protocol"
    EXPERIMENT = "experiment"
    PAPER = "paper"


class ExecutionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    PAUSED = "paused"
    WAITING_FOR_USER = "waiting_for_user"
    BLOCKED = "blocked"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"


class StepAcceptanceStatus(StrEnum):
    """Acceptance of a step artifact, independent of executor termination."""

    PENDING = "pending"
    OUTPUT_PRODUCED = "output_produced"
    SCHEMA_VALIDATED = "schema_validated"
    SCIENTIFIC_POSTCONDITION_PASSED = "scientific_postcondition_passed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ProtocolStatus(StrEnum):
    DRAFT = "draft"
    COMPILING = "compiling"
    BLOCKED = "blocked"
    EXECUTABLE = "executable"
    FROZEN_EXECUTABLE = "frozen_executable"
    SUPERSEDED = "superseded"


class AnalysisEligibilityStatus(StrEnum):
    NOT_ADJUDICABLE = "not_adjudicable"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"


class EvidenceMaturity(StrEnum):
    EXPLORATORY = "exploratory"
    ADAPTIVE_REUSE = "adaptive_reuse"
    PROSPECTIVE = "prospective"
    REPLICATED = "replicated"


class PublicationMode(StrEnum):
    NONE = "none"
    PROTOCOL_REPORT = "protocol_report"
    BOUNDARY_REPORT = "boundary_report"
    RESULTS_MANUSCRIPT = "results_manuscript"


class ResourceLifecycleStatus(StrEnum):
    PLANNED = "planned"
    DISCOVERED = "discovered"
    SELECTED = "selected"
    MATERIALIZED = "materialized"
    SCHEMA_VALIDATED = "schema_validated"
    SEMANTICALLY_VALIDATED = "semantically_validated"
    FROZEN = "frozen"


class GateStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    AWAITING_USER = "awaiting_user"
    APPROVED = "approved"
    REJECTED = "rejected"


class GateType(StrEnum):
    SCOPE_APPROVAL = "scope_approval"
    RESEARCH_CONTRACT = "research_contract"
    REPAIR_OR_HIGH_COST_RUN = "repair_or_high_cost_run"
    PUBLICATION_NARRATIVE = "publication_narrative"
    VISUAL_ARGUMENT = "visual_argument"
    AUTHOR_VOICE = "author_voice"
    FINAL_SUBMISSION = "final_submission"


class RepairStatus(StrEnum):
    NONE = "none"
    DIAGNOSING = "diagnosing"
    REPAIR_PROPOSED = "repair_proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    REPAIRING = "repairing"
    REGRESSION_CHECK = "regression_check"
    COMPLETED = "completed"


class ArtifactStatus(StrEnum):
    DRAFT = "draft"
    FROZEN = "frozen"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"
    AT_RISK = "at_risk"


class ArtifactRole(StrEnum):
    PROTOCOL = "protocol"
    RUN = "run"
    OUTPUT = "output"
    EVALUATION = "evaluation"
    CLAIM = "claim"
    LITERATURE_BACKGROUND = "literature_background"
    LITERATURE_DECISION = "literature_decision"
    MANUSCRIPT = "manuscript"
    AUDIT = "audit"
    FEASIBILITY = "feasibility"
    OTHER = "other"


class StudyLifecycle(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"


class ExecutorType(StrEnum):
    DETERMINISTIC_SERVICE = "deterministic_service"
    RETRIEVAL_SERVICE = "retrieval_service"
    MODEL = "model"
    CODEX = "codex"
    SANDBOX_RUNNER = "sandbox_runner"
    DETERMINISTIC_EVALUATOR = "deterministic_evaluator"
    NLI_SERVICE = "nli_service"
    AI_SCIENTIFIC_REVIEW_PANEL = "ai_scientific_review_panel"
    PROJECT_OWNER = "project_owner"
    EXTERNAL = "external"


class EntryMode(StrEnum):
    PROJECT_TO_PAPER = "project_to_paper"
    IDEA_TO_PAPER = "idea_to_paper"


class ResearchSupportLevel(StrEnum):
    FORMAL = "formal"
    DIAGNOSTIC_ONLY = "diagnostic_only"


class EvidenceChainLevel(StrEnum):
    INFERRED = "inferred_chain"
    VERIFIED = "verified_chain"


class HypothesisRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class HypothesisVerdictStatus(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    UNVERIFIABLE = "unverifiable"


class StudyVerdictStatus(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    MIXED = "mixed"
    INCONCLUSIVE = "inconclusive"
    UNVERIFIABLE = "unverifiable"


class SystemReadiness(StrEnum):
    NOT_READY = "not_ready"
    CONDITIONS_MET = "conditions_met"
    BLOCKED = "blocked"


class AIReviewStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    ABSTAINED = "abstained"


class AuthorApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class IntegrityGateStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class DiagnosticOwner(StrEnum):
    IDEA_VALIDATION = "idea_validation"
    EVIDENCE_PACKAGING = "evidence_packaging"
    LITERATURE_GROUNDING = "literature_grounding"
    PAPER_WRITER = "paper_writer"
    EXECUTION_ENVIRONMENT = "execution_environment"
    INTEGRITY_BINDING = "integrity_binding"


class RunKind(StrEnum):
    EXPERIMENTAL = "experimental"
    ANALYTIC = "analytic"
    FEASIBILITY_PROBE = "feasibility_probe"


class Stage3Profile(StrEnum):
    DETERMINISTIC_SIMULATION_V1 = "deterministic_simulation_v1"
    TABULAR_ML_V1 = "tabular_ml_v1"
    BENCHMARK_PREDICTION_V1 = "benchmark_prediction_v1"
    EXISTING_PYTHON_PROJECT_V1 = "existing_python_project_v1"
    COMPUTATIONAL_PAIRED_COMPARISON_V1 = (
        "computational_paired_comparison_v1"
    )
    COMPUTATIONAL_PAIRED_COMPARISON_V2 = (
        "computational_paired_comparison_v2"
    )
    PAIRED_BINARY_INDEPENDENT_V1 = "paired_binary_independent_v1"
    PAIRED_BINARY_CLUSTERED_V1 = "paired_binary_clustered_v1"
    PAIRED_MULTI_ENDPOINT_V1 = "paired_multi_endpoint_v1"
    UNPAIRED_TWO_GROUP_CONTINUOUS_V1 = (
        "unpaired_two_group_continuous_v1"
    )
    PAIRED_MULTI_ARM_ABLATION_V1 = "paired_multi_arm_ablation_v1"


class Stage3BuildMode(StrEnum):
    READY_MADE_EXPERIMENT = "ready_made_experiment"
    BUILD_FROM_BLUEPRINT = "build_from_blueprint"


class ProfileCapabilityStatus(StrEnum):
    SUPPORTED = "supported"
    SUPPORTED_WITH_BUILD = "supported_with_build"
    UNSUPPORTED = "unsupported"


class BuildAssetStrategy(StrEnum):
    REUSE = "reuse"
    ADAPT = "adapt"
    RETRIEVE = "retrieve"
    GENERATE = "generate"
    IMPLEMENT = "implement"


class ExecutionTrustLevel(StrEnum):
    PLATFORM_TEMPLATE = "platform_template"
    TRUSTED_LOCAL_PROJECT = "trusted_local_project"
    THIRD_PARTY_CODE = "third_party_code"
    AI_GENERATED_CODE = "ai_generated_code"
    UNKNOWN_BINARY = "unknown_binary"


class RunCellStatus(StrEnum):
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED_TRANSIENT = "failed_transient"
    BLOCKED = "blocked"
    INVALIDATED = "invalidated"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class Stage3FailureClass(StrEnum):
    NONE = "none"
    TRANSIENT = "transient"
    PROTOCOL = "protocol"
    SCHEMA = "schema"
    INTEGRITY = "integrity"
    SCIENTIFIC_DESIGN = "scientific_design"
    PERMISSION = "permission"
    CANCELLED = "cancelled"


class QualificationStatus(StrEnum):
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    INCOMPLETE = "incomplete"


class FormalExposureType(StrEnum):
    AGGREGATE_METRICS = "aggregate_metrics"
    VERDICT_ONLY = "verdict_only"
    SAMPLE_ERRORS = "sample_errors"


class ConfirmatoryStatus(StrEnum):
    UNTOUCHED = "untouched"
    CONFIRMATORY_USED = "confirmatory_used"
    ADAPTIVE_REUSE = "adaptive_reuse"
    EXHAUSTED = "exhausted"


class AssuranceStatus(StrEnum):
    PASSED = "passed"
    CONDITIONAL = "conditional"
    FAILED = "failed"


class LeakageCheckStatus(StrEnum):
    PASSED = "passed"
    NOT_APPLICABLE = "not_applicable"
    REQUIRES_REVIEW = "requires_review"
    FAILED = "failed"


class EvidenceReproductionLevel(StrEnum):
    BOUNDARY_ONLY = "L0_boundary_only"
    EVIDENCE_CHAIN_VERIFIED = "L1_evidence_chain_verified"
    CLEAN_ROOM_REPRODUCED = "L2_clean_room_reproduced"
    INDEPENDENTLY_REPRODUCED = "L3_independently_reproduced"
    EXTERNALLY_REPLICATED = "L4_externally_replicated"


class EvidenceRelation(StrEnum):
    QUALIFIES = "qualifies"
    MATERIALIZES = "materializes"
    PRODUCED = "produced"
    EVALUATED_BY = "evaluated_by"
    SUPPORTS = "supports"
    REFUTES = "refutes"
    QUALIFIES_VERDICT = "qualifies_verdict"
    AGGREGATES_TO = "aggregates_to"


class NetworkPolicy(StrictModel):
    schema_version: int = 1
    mode: str = "offline"
    retrieval_policy_id: str | None = None
    network_enabled: bool = False
    public_read_requests_automatic: bool = False
    external_writes_require_approval: bool = True
    secret_guard_required: Literal[True] = True
    audit_all_requests: Literal[True] = True
    allowed_domains: list[str] = Field(default_factory=list)
    budget_limit: float | None = Field(default=None, ge=0)


class NetworkAuditEvent(StrictModel):
    schema_version: int = 1
    event_id: str = Field(pattern=r"^network-[a-f0-9]{16}$")
    project_id: str
    study_id: str | None = None
    domain: str = Field(min_length=1, max_length=253)
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
    query_summary: str = Field(default="", max_length=2_000)
    request_file_paths: list[str] = Field(default_factory=list)
    response_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    cost: float = Field(default=0, ge=0)
    external_write_approved: bool = False
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def enforce_secret_guard(self) -> "NetworkAuditEvent":
        secret_paths = [
            path for path in self.request_file_paths if is_secret_path(path)
        ]
        if secret_paths:
            raise ValueError("secret or credential files cannot enter network requests")
        if self.method not in {"GET", "HEAD"} and not self.external_write_approved:
            raise ValueError("external write requires explicit approval")
        return self


class ProjectRecord(StrictModel):
    schema_version: int = 2
    project_id: str = Field(pattern=rf"^project-{_ID_COMPONENT}$")
    title: str = Field(min_length=1, max_length=300)
    source_root: str | None = None
    shared_resource_ids: list[str] = Field(default_factory=list)
    network_policy: NetworkPolicy = Field(default_factory=NetworkPolicy)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class StudyRecord(StrictModel):
    schema_version: int = 2
    study_id: str = Field(pattern=rf"^study-{_ID_COMPONENT}$")
    project_id: str = Field(pattern=rf"^project-{_ID_COMPONENT}$")
    title: str = Field(min_length=1, max_length=300)
    entry_mode: EntryMode
    research_type: str = Field(default="computational", min_length=3, max_length=100)
    support_level: ResearchSupportLevel = ResearchSupportLevel.FORMAL
    phase: Phase = Phase.DISCOVERY
    lifecycle: StudyLifecycle = StudyLifecycle.ACTIVE
    execution_status: ExecutionStatus = ExecutionStatus.QUEUED
    repair_status: RepairStatus = RepairStatus.NONE
    current_step_ids: list[str] = Field(default_factory=list)
    active_scope_version: int | None = None
    active_contract_version: int | None = None
    latest_study_verdict_id: str | None = None
    predecessor_study_id: str | None = None
    successor_study_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    def legacy_stage(self) -> str:
        if self.lifecycle is StudyLifecycle.COMPLETED:
            return "completed"
        if self.execution_status is ExecutionStatus.PAUSED:
            return "paused"
        if self.phase is Phase.DISCOVERY:
            return "plan_review" if self.active_scope_version else "scoping"
        if self.phase is Phase.PROTOCOL:
            return (
                "contract_frozen"
                if self.active_contract_version
                else "baseline_pending"
            )
        if self.phase is Phase.EXPERIMENT:
            return (
                "experiment_running"
                if self.execution_status
                in {ExecutionStatus.RUNNING, ExecutionStatus.RETRYING}
                else "result_review"
            )
        return "synthesis"


class StepDefinition(StrictModel):
    schema_version: int = 1
    step_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    phase: Phase
    executor_type: ExecutorType
    expected_output: str | None = None
    transient_retry_limit: int = Field(default=3, ge=0, le=10)
    scientific_failure_enters_diagnosis: bool = False
    required_gate_type: GateType | None = None


class StepInstance(StrictModel):
    schema_version: int = 1
    step_instance_id: str = Field(pattern=r"^step-[a-f0-9]{16}$")
    study_id: str = Field(pattern=rf"^study-{_ID_COMPONENT}$")
    step_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    phase: Phase
    status: ExecutionStatus = ExecutionStatus.QUEUED
    acceptance_status: StepAcceptanceStatus = StepAcceptanceStatus.PENDING
    output_produced: bool = False
    schema_validated: bool = False
    scientific_postcondition_passed: bool | None = None
    acceptance_checks: dict[str, bool] = Field(default_factory=dict)
    executor_type: ExecutorType
    depends_on: list[str] = Field(default_factory=list)
    parent_step_id: str | None = None
    task_group: str | None = None
    attempt: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_output: str | None = None
    blocker: dict[str, Any] | None = None
    audit_event_ids: list[str] = Field(default_factory=list)
    started_at: str | None = None
    updated_at: str = Field(default_factory=utc_now)
    completed_at: str | None = None
    lease_id: str | None = None
    fencing_token: int = Field(default=0, ge=0)


class GateRecord(StrictModel):
    schema_version: int = 1
    gate_id: str = Field(pattern=r"^gate-[a-f0-9]{16}$")
    study_id: str = Field(pattern=rf"^study-{_ID_COMPONENT}$")
    gate_type: GateType
    status: GateStatus = GateStatus.AWAITING_USER
    subject_type: str
    subject_id: str
    subject_version: int | None = None
    decided_by: str | None = None
    reason: str | None = None
    created_at: str = Field(default_factory=utc_now)
    decided_at: str | None = None


class ScopeContractVersion(StrictModel):
    schema_version: int = 1
    study_id: str
    version: int = Field(ge=1)
    status: ArtifactStatus = ArtifactStatus.DRAFT
    contract_level: Literal["direction", "specific_topic"] = "direction"
    parent_direction_id: str | None = None
    direction: str
    research_question: str
    scope_in: list[str]
    scope_out: list[str]
    candidate_contribution: str
    unit_of_analysis: str | None = None
    study_design: str | None = None
    population_or_corpus: str | None = None
    primary_outcome: str | None = None
    comparison: str | None = None
    feasibility_basis: list[str] = Field(default_factory=list)
    unresolved_conditions: list[str] = Field(default_factory=list)
    project_resource_ids: list[str] = Field(default_factory=list)
    literature_set_id: str | None = None
    predecessor_version: int | None = None
    field_diff: dict[str, Any] = Field(default_factory=dict)
    created_by: str = "project_owner"
    created_at: str = Field(default_factory=utc_now)
    frozen_at: str | None = None


class Hypothesis(StrictModel):
    hypothesis_id: str = Field(pattern=r"^hypothesis-[a-z0-9-]{2,80}$")
    statement: str = Field(min_length=5, max_length=4_000)
    role: HypothesisRole
    decision_rule: dict[str, Any]


class ModelSelectionPlan(StrictModel):
    schema_version: int = 1
    search_space: dict[str, Any] = Field(default_factory=dict)
    number_of_trials: int = Field(default=1, ge=1)
    selection_metric: str = "frozen_primary_metric"
    selection_split: str = "none"
    early_stopping_rule: dict[str, Any] = Field(
        default_factory=lambda: {"enabled": False}
    )
    checkpoint_selection_rule: str = "fixed_final_checkpoint"
    random_search_seed: int = 0
    tuning_budget: dict[str, Any] = Field(default_factory=dict)
    final_refit_policy: str = "no_refit"
    formal_results_may_influence_selection: Literal[False] = False


class ArmFairnessContract(StrictModel):
    schema_version: int = 1
    shared_resources: list[str] = Field(
        default_factory=lambda: [
            "data",
            "splits",
            "evaluator",
            "runtime",
        ]
    )
    allowed_budget_difference: float = Field(default=0.0, ge=0)
    training_compute_budget: dict[str, Any] = Field(default_factory=dict)
    inference_compute_budget: dict[str, Any] = Field(default_factory=dict)
    pretraining_policy: str = "same_or_explicitly_declared"
    external_model_policy: str = "declared_only"
    hyperparameter_tuning_parity: Literal[True] = True
    failure_handling: str = "same_rule_for_both_arms"
    partitions: dict[str, str] = Field(
        default_factory=lambda: {
            "smoke": "engineering_only",
            "development_tuning": "selection_only",
            "formal_confirmation": "verdict_only",
        }
    )


class AttemptSelectionPolicy(StrictModel):
    schema_version: int = 1
    canonical_attempt: Literal[
        "first_qualified_successful_attempt"
    ] = "first_qualified_successful_attempt"
    rerun_after_success_forbidden: Literal[True] = True
    fixed_seed_required: Literal[True] = True
    retain_failed_outputs: Literal[True] = True
    best_or_latest_selection_forbidden: Literal[True] = True
    manual_rerun_requires_intervention_record: Literal[True] = True
    implementation_change_requires_successor: Literal[True] = True
    nondeterminism_policy: str = "record_and_escalate"


class ResearchContractVersion(StrictModel):
    schema_version: int = 1
    study_id: str
    version: int = Field(ge=1)
    scope_version: int = Field(ge=1)
    status: ArtifactStatus = ArtifactStatus.DRAFT
    protocol_status: ProtocolStatus = ProtocolStatus.DRAFT
    compile_report_id: str | None = None
    unresolved_placeholders: list[str] = Field(default_factory=list)
    run_specification_ids: list[str] = Field(default_factory=list)
    dataset_specification_id: str | None = None
    algorithm_specification_ids: list[str] = Field(default_factory=list)
    evaluation_specification_id: str | None = None
    analysis_specification_id: str | None = None
    executable_run_dag_id: str | None = None
    dry_run_report_id: str | None = None
    hypotheses: list[Hypothesis] = Field(min_length=1)
    data_boundary: dict[str, Any]
    metrics: list[dict[str, Any]] = Field(min_length=1)
    baseline: dict[str, Any]
    treatment: dict[str, Any]
    tasks: list[str] = Field(default_factory=list)
    seeds: list[int] = Field(default_factory=list)
    concurrency: int = Field(default=1, ge=1, le=128)
    runtime_binding: dict[str, Any]
    evaluator_policy: dict[str, Any]
    eligibility_rules: list[dict[str, Any]] = Field(default_factory=list)
    experiment_profile: Stage3Profile | None = None
    profile_parameters: dict[str, Any] = Field(default_factory=dict)
    splits: list[str] = Field(default_factory=lambda: ["default"])
    replicates: int = Field(default=1, ge=1, le=10_000)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    statistical_rules: dict[str, Any] = Field(default_factory=dict)
    estimand: dict[str, Any] = Field(default_factory=dict)
    data_requirements: dict[str, Any] = Field(default_factory=dict)
    implementation_requirements: dict[str, Any] = Field(
        default_factory=dict
    )
    scientific_validity_contract: dict[str, Any] = Field(
        default_factory=dict
    )
    environment_requirements: dict[str, Any] = Field(default_factory=dict)
    resource_policy: dict[str, Any] = Field(default_factory=dict)
    budget_security: dict[str, Any] = Field(default_factory=dict)
    model_selection_plan: ModelSelectionPlan = Field(
        default_factory=ModelSelectionPlan
    )
    arm_fairness_contract: ArmFairnessContract = Field(
        default_factory=ArmFairnessContract
    )
    attempt_selection_policy: AttemptSelectionPolicy = Field(
        default_factory=AttemptSelectionPolicy
    )
    predecessor_version: int | None = None
    field_diff: dict[str, Any] = Field(default_factory=dict)
    created_by: str = "project_owner"
    created_at: str = Field(default_factory=utc_now)
    frozen_at: str | None = None

    @model_validator(mode="after")
    def has_primary_hypothesis(self) -> "ResearchContractVersion":
        if not any(item.role is HypothesisRole.PRIMARY for item in self.hypotheses):
            raise ValueError(
                "research contract requires at least one primary hypothesis"
            )
        if len(self.seeds) != len(set(self.seeds)):
            raise ValueError("research contract seeds must be unique")
        if len(self.splits) != len(set(self.splits)):
            raise ValueError("research contract splits must be unique")
        if (
            self.model_selection_plan.number_of_trials > 1
            and self.model_selection_plan.selection_split
            in {"formal", "formal_confirmation", "test"}
        ):
            raise ValueError(
                "model selection cannot use the formal confirmation split"
            )
        return self


class BaselineVerificationContract(StrictModel):
    schema_version: int = 1
    study_id: str
    run_id: str
    baseline_run_succeeded: bool
    preregistered_units_accounted_for: bool
    output_schema_valid: bool
    frozen_denominator_computable: bool
    sample_integrity_valid: bool
    hashes_match_contract: bool
    artifact_binding_valid: bool
    unresolved_integrity_errors: list[str] = Field(default_factory=list)
    verified_at: str = Field(default_factory=utc_now)

    @property
    def baseline_verified(self) -> bool:
        return all(
            (
                self.baseline_run_succeeded,
                self.preregistered_units_accounted_for,
                self.output_schema_valid,
                self.frozen_denominator_computable,
                self.sample_integrity_valid,
                self.hashes_match_contract,
                self.artifact_binding_valid,
                not self.unresolved_integrity_errors,
            )
        )


class ResearchRun(StrictModel):
    schema_version: int = 1
    run_id: str = Field(pattern=r"^run-[a-z0-9-]{2,100}$")
    study_id: str
    contract_version: int = Field(ge=1)
    kind: RunKind
    status: ExecutionStatus = ExecutionStatus.QUEUED
    run_cell_id: str | None = None
    predecessor_run_id: str | None = None
    repair_contract_id: str | None = None
    reused_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    purpose: Literal[
        "formal_experiment",
        "formal_analysis",
        "feasibility_only",
    ] = "formal_experiment"
    evidence_eligible: bool = True
    formal_run_eligible: bool = True
    created_at: str = Field(default_factory=utc_now)
    completed_at: str | None = None


class Stage3HandoffPackage(StrictModel):
    schema_version: int = 3
    handoff_id: str = Field(pattern=r"^stage3-handoff-[a-f0-9]{16}$")
    study_id: str
    scope_version: int = Field(ge=1)
    contract_version: int = Field(ge=1)
    profile: Stage3Profile
    handoff_stage: Literal["build", "formal_execution"] = "formal_execution"
    build_mode: Stage3BuildMode = Stage3BuildMode.READY_MADE_EXPERIMENT
    scientific_specification_seal_id: str | None = None
    experiment_blueprint_id: str | None = None
    mvp_feasibility_receipt_id: str | None = None
    execution_package_seal_id: str | None = None
    lock_artifact_ids: dict[str, str] = Field(default_factory=dict)
    experiment_manifest_path: str | None = None
    experiment_manifest_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    execution_root: str | None = None
    resource_artifact_ids: list[str] = Field(default_factory=list)
    predecessor_handoff_id: str | None = None
    repair_contract_id: str | None = None
    admitted_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_handoff_level(self) -> "Stage3HandoffPackage":
        if self.handoff_stage == "build":
            missing = [
                name
                for name, value in (
                    (
                        "scientific_specification_seal_id",
                        self.scientific_specification_seal_id,
                    ),
                    ("experiment_blueprint_id", self.experiment_blueprint_id),
                    (
                        "mvp_feasibility_receipt_id",
                        self.mvp_feasibility_receipt_id,
                    ),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "build handoff is missing: " + ", ".join(missing)
                )
        elif (
            len(self.lock_artifact_ids) < 5
            or not self.experiment_manifest_path
            or not self.experiment_manifest_sha256
        ):
            raise ValueError(
                "formal execution handoff requires frozen locks and manifest"
            )
        elif self.schema_version >= 3 and not self.execution_root:
            raise ValueError(
                "formal execution handoff requires an execution_root"
            )
        return self


class EstimandSpecification(StrictModel):
    population: str = Field(min_length=1)
    experimental_unit: str = Field(min_length=1)
    pairing_key: list[str] = Field(min_length=1)
    outcome: str = Field(min_length=1)
    contrast: str = Field(min_length=1)
    aggregation_hierarchy: list[str] = Field(min_length=1)
    weighting_policy: str = Field(min_length=1)
    variance_unit: str = Field(min_length=1)


class ExperimentBlueprint(StrictModel):
    schema_version: int = 1
    blueprint_id: str = Field(pattern=r"^blueprint-[a-f0-9]{16}$")
    study_id: str
    contract_version: int = Field(ge=1)
    profile: Stage3Profile
    estimand: EstimandSpecification
    data_requirements: dict[str, Any]
    preprocessing_requirements: list[str] = Field(default_factory=list)
    baseline_requirements: dict[str, Any]
    treatment_requirements: dict[str, Any]
    allowed_arm_delta: list[str] = Field(min_length=1)
    runner_requirements: dict[str, Any]
    raw_output_fields: list[str] = Field(min_length=1)
    environment_requirements: dict[str, Any]
    resource_routes: list[dict[str, Any]] = Field(min_length=1)
    smoke_test_requirements: list[str] = Field(min_length=1)
    completion_criteria: list[str] = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)


class MVPFeasibilityReceipt(StrictModel):
    schema_version: int = 1
    receipt_id: str = Field(pattern=r"^mvp-receipt-[a-f0-9]{16}$")
    study_id: str
    contract_version: int = Field(ge=1)
    purpose: Literal["feasibility_only"] = "feasibility_only"
    evidence_eligible: Literal[False] = False
    formal_run_eligible: Literal[False] = False
    prototype_resource_ids: list[str] = Field(default_factory=list)
    smoke_case_ids: list[str] = Field(min_length=1)
    metric_computable: bool
    schema_feasible: bool
    resource_feasible: bool
    reproducible_seed_probe: bool
    runtime_estimate_seconds: float | None = Field(default=None, ge=0)
    memory_estimate_bytes: int | None = Field(default=None, ge=0)
    temporary_implementation_notes: list[str] = Field(default_factory=list)
    unresolved_assumptions: list[str] = Field(default_factory=list)
    may_support: list[str] = Field(
        default_factory=lambda: [
            "runtime_estimation",
            "metric_computability",
            "schema_feasibility",
            "resource_feasibility",
        ]
    )
    may_not_support: list[str] = Field(
        default_factory=lambda: [
            "hypothesis_verdict",
            "study_verdict",
            "formal_effect_estimate",
        ]
    )
    created_at: str = Field(default_factory=utc_now)


class ScientificSpecificationSeal(StrictModel):
    schema_version: int = 1
    seal_id: str = Field(pattern=r"^scientific-seal-[a-f0-9]{16}$")
    study_id: str
    scope_version: int = Field(ge=1)
    contract_version: int = Field(ge=1)
    scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    blueprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision_rules_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    immutable: Literal[True] = True
    frozen_at: str = Field(default_factory=utc_now)


class ExperimentBuildItem(StrictModel):
    build_item_id: str = Field(pattern=r"^build-item-[a-f0-9]{16}$")
    asset_type: Literal[
        "dataset",
        "dataset_adapter",
        "baseline",
        "treatment",
        "evaluator",
        "environment",
        "experiment_manifest",
    ]
    strategy: BuildAssetStrategy
    source: dict[str, Any] = Field(default_factory=dict)
    license: str | None = None
    acceptance_checks: list[str] = Field(min_length=1)
    estimated_cost: dict[str, Any] = Field(default_factory=dict)
    risks: list[str] = Field(default_factory=list)


class ExperimentBuildPlan(StrictModel):
    schema_version: int = 1
    build_plan_id: str = Field(pattern=r"^build-plan-[a-f0-9]{16}$")
    study_id: str
    handoff_id: str
    contract_version: int = Field(ge=1)
    profile: Stage3Profile
    capability_status: ProfileCapabilityStatus
    build_mode: Stage3BuildMode
    items: list[ExperimentBuildItem] = Field(min_length=1)
    research_contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: str = Field(default_factory=utc_now)


class ExecutionPackageSeal(StrictModel):
    schema_version: int = 1
    seal_id: str = Field(pattern=r"^execution-seal-[a-f0-9]{16}$")
    study_id: str
    contract_version: int = Field(ge=1)
    scientific_specification_seal_id: str
    lock_artifact_ids: dict[str, str] = Field(min_length=8)
    smoke_test_artifact_id: str
    conformance_checks: dict[str, bool] = Field(min_length=1)
    trust_level: ExecutionTrustLevel
    isolated_execution_required: bool
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    immutable: Literal[True] = True
    frozen_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def all_conformance_checks_pass(self) -> "ExecutionPackageSeal":
        if not all(self.conformance_checks.values()):
            raise ValueError(
                "execution package cannot freeze before conformance passes"
            )
        if self.trust_level in {
            ExecutionTrustLevel.THIRD_PARTY_CODE,
            ExecutionTrustLevel.AI_GENERATED_CODE,
        } and not self.isolated_execution_required:
            raise ValueError(
                "third-party or generated code requires isolated execution"
            )
        return self


class RunCell(StrictModel):
    schema_version: int = 1
    run_cell_id: str = Field(pattern=r"^run-cell-[a-f0-9]{16}$")
    study_id: str
    plan_id: str = Field(pattern=r"^run-plan-[a-f0-9]{16}$")
    contract_version: int = Field(ge=1)
    task_id: str = Field(min_length=1, max_length=300)
    split_id: str = Field(min_length=1, max_length=300)
    arm_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    seed: int
    replicate: int = Field(ge=1)
    action_id: str = Field(pattern=r"^action-[a-zA-Z0-9._-]{1,119}$")
    experiment_id: str = Field(min_length=2, max_length=120)
    input_bindings: dict[str, str] = Field(default_factory=dict)
    expected_output_schema: dict[str, Any] = Field(min_length=1)
    resource_profile: dict[str, Any] = Field(default_factory=dict)
    dependency_ids: list[str] = Field(default_factory=list)
    pair_block_id: str | None = None
    pair_position: int = Field(default=1, ge=1, le=16)
    scheduled_after_run_cell_id: str | None = Field(
        default=None, pattern=r"^run-cell-[a-f0-9]{16}$"
    )
    execution_manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: RunCellStatus = RunCellStatus.PLANNED


class RunPlan(StrictModel):
    schema_version: int = 1
    plan_id: str = Field(pattern=r"^run-plan-[a-f0-9]{16}$")
    study_id: str
    handoff_id: str = Field(pattern=r"^stage3-handoff-[a-f0-9]{16}$")
    contract_version: int = Field(ge=1)
    profile: Stage3Profile
    compiler_version: str = "stage3-compiler-v1"
    concurrency: int = Field(ge=1, le=128)
    cells: list[RunCell] = Field(min_length=2)
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_matrix(self) -> "RunPlan":
        ids = [item.run_cell_id for item in self.cells]
        if len(ids) != len(set(ids)):
            raise ValueError("Run Plan contains duplicate run_cell_id values")
        if any(item.plan_id != self.plan_id for item in self.cells):
            raise ValueError("RunCell plan_id does not match RunPlan")
        if any(item.study_id != self.study_id for item in self.cells):
            raise ValueError("RunCell study_id does not match RunPlan")
        return self


class ExecutionAttempt(StrictModel):
    schema_version: int = 1
    attempt_id: str = Field(pattern=r"^attempt-[a-f0-9]{16}$")
    study_id: str
    run_cell_id: str = Field(pattern=r"^run-cell-[a-f0-9]{16}$")
    attempt_number: int = Field(ge=1)
    status: RunCellStatus
    started_at: str
    finished_at: str | None = None
    exit_status: int | None = None
    input_hashes: dict[str, str] = Field(default_factory=dict)
    environment_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    code_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    stdout_artifact_id: str | None = None
    stderr_artifact_id: str | None = None
    output_artifact_ids: list[str] = Field(default_factory=list)
    isolation_attestations: dict[str, Any] = Field(default_factory=dict)
    resource_telemetry: dict[str, Any] = Field(default_factory=dict)
    canonical: bool = False
    lease_id: str | None = None
    fencing_token: int | None = Field(default=None, ge=1)
    idempotency_key: str | None = None
    failure_class: Stage3FailureClass = Stage3FailureClass.NONE
    failure_detail: str | None = None


class ResultEnvelope(StrictModel):
    schema_version: int = 1
    result_id: str = Field(pattern=r"^result-[a-f0-9]{16}$")
    study_id: str
    run_cell_id: str = Field(pattern=r"^run-cell-[a-f0-9]{16}$")
    attempt_id: str | None = Field(
        default=None, pattern=r"^attempt-[a-f0-9]{16}$"
    )
    output_artifact_ids: list[str] = Field(min_length=1)
    metrics: dict[str, float] = Field(default_factory=dict)
    denominator: int | float | None = Field(default=None, gt=0)
    sample_ids: list[str] = Field(default_factory=list)
    analysis_rows: list[dict[str, Any]] = Field(default_factory=list)
    abstentions: int = Field(default=0, ge=0)
    reused_from_result_id: str | None = None
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_origin(self) -> "ResultEnvelope":
        if (self.attempt_id is None) == (self.reused_from_result_id is None):
            raise ValueError(
                "result must reference exactly one execution attempt "
                "or predecessor result"
            )
        return self


class EvaluationRecord(StrictModel):
    schema_version: int = 1
    evaluation_id: str = Field(pattern=r"^evaluation-[a-f0-9]{16}$")
    study_id: str
    plan_id: str = Field(pattern=r"^run-plan-[a-f0-9]{16}$")
    contract_version: int = Field(ge=1)
    qualification_status: QualificationStatus
    qualification_checks: dict[str, bool] = Field(default_factory=dict)
    excluded_run_cell_ids: list[str] = Field(default_factory=list)
    exclusion_reason_counts: dict[str, int] = Field(default_factory=dict)
    metric_name: str
    baseline_estimate: float | None = None
    treatment_estimate: float | None = None
    paired_effect: float | None = None
    paired_variance: float | None = Field(default=None, ge=0)
    pair_count: int = Field(default=0, ge=0)
    independent_unit_count: int = Field(default=0, ge=0)
    variance_unit: str = "registered_pair"
    aggregation_hierarchy: list[str] = Field(default_factory=list)
    exposure_record_id: str | None = None
    confirmatory_status: ConfirmatoryStatus = ConfirmatoryStatus.UNTOUCHED
    statistical_assurance_report_id: str | None = None
    secondary_implementation_effect: float | None = None
    secondary_implementation_matches: bool | None = None
    confidence_interval: tuple[float, float] | None = None
    arm_estimates: dict[str, float] = Field(default_factory=dict)
    contrast_estimates: dict[str, dict[str, Any]] = Field(
        default_factory=dict
    )
    statistical_rule: dict[str, Any] = Field(default_factory=dict)
    decision: HypothesisVerdictStatus
    rationale: str
    result_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def disqualified_cannot_decide(self) -> "EvaluationRecord":
        if (
            self.qualification_status is not QualificationStatus.QUALIFIED
            and self.decision
            in {
                HypothesisVerdictStatus.SUPPORTED,
                HypothesisVerdictStatus.REFUTED,
            }
        ):
            raise ValueError(
                "unqualified evaluation cannot support or refute a hypothesis"
            )
        return self


class FormalEvaluationExposureRecord(StrictModel):
    schema_version: int = 1
    exposure_id: str = Field(pattern=r"^formal-exposure-[a-f0-9]{16}$")
    dataset_version: str
    split_id: str
    target_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    study_id: str
    plan_id: str = Field(pattern=r"^run-plan-[a-f0-9]{16}$")
    exposure_type: FormalExposureType
    revealed_fields: list[str] = Field(default_factory=list)
    first_exposed_at: str = Field(default_factory=utc_now)
    prior_exposure_count: int = Field(default=0, ge=0)
    result_influenced_successor: bool = False
    confirmatory_status: ConfirmatoryStatus


class HumanInterventionRecord(StrictModel):
    schema_version: int = 1
    intervention_id: str = Field(
        pattern=r"^human-intervention-[a-f0-9]{16}$"
    )
    study_id: str
    plan_id: str = Field(pattern=r"^run-plan-[a-f0-9]{16}$")
    run_cell_id: str | None = Field(
        default=None, pattern=r"^run-cell-[a-f0-9]{16}$"
    )
    intervention_type: str
    reason: str
    authorized_by: str
    created_at: str = Field(default_factory=utc_now)


class StatisticalAssuranceReport(StrictModel):
    schema_version: int = 1
    report_id: str = Field(pattern=r"^stat-assurance-[a-f0-9]{16}$")
    study_id: str
    contract_version: int = Field(ge=1)
    profile: Stage3Profile
    status: AssuranceStatus
    checks: dict[str, bool] = Field(min_length=1)
    variance_unit: str
    aggregation_hierarchy: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    implementation_version: str = "paired-profile-assurance-v1"
    created_at: str = Field(default_factory=utc_now)


class LeakageAuditReport(StrictModel):
    schema_version: int = 1
    report_id: str = Field(pattern=r"^leakage-audit-[a-f0-9]{16}$")
    study_id: str
    contract_version: int = Field(ge=1)
    checks: dict[str, LeakageCheckStatus] = Field(min_length=1)
    findings: list[str] = Field(default_factory=list)
    blocking: bool = False
    created_at: str = Field(default_factory=utc_now)


class ScientificClaimEnvelope(StrictModel):
    schema_version: int = 1
    claim_envelope_id: str = Field(
        pattern=r"^claim-envelope-[a-f0-9]{16}$"
    )
    study_id: str
    plan_id: str = Field(pattern=r"^run-plan-[a-f0-9]{16}$")
    allowed_claim: str
    population: str
    tasks: list[str] = Field(min_length=1)
    intervention: str
    comparator: str
    outcome: str
    effect_estimate: float | None = None
    interval: tuple[float, float] | None = None
    evidence_level: EvidenceReproductionLevel = (
        EvidenceReproductionLevel.EVIDENCE_CHAIN_VERIFIED
    )
    confirmatory_status: ConfirmatoryStatus
    known_limitations: list[str] = Field(default_factory=list)
    maximum_claim_tier: str = "controlled_effect"
    publication_mode: PublicationMode = PublicationMode.RESULTS_MANUSCRIPT
    scientific_validity_report_ids: list[str] = Field(default_factory=list)
    prohibited_generalizations: list[str] = Field(
        default_factory=lambda: [
            "causal effects outside the frozen design",
            "state-of-the-art performance",
            "unseen populations",
            "production reliability",
        ]
    )
    created_at: str = Field(default_factory=utc_now)


class ComponentDefectNotice(StrictModel):
    schema_version: int = 1
    notice_id: str = Field(pattern=r"^component-defect-[a-f0-9]{16}$")
    component_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    component_name: str
    description: str = Field(min_length=5)
    severity: Literal["low", "medium", "high", "critical"]
    reported_by: str
    created_at: str = Field(default_factory=utc_now)


class ResearchErratum(StrictModel):
    schema_version: int = 1
    erratum_id: str = Field(pattern=r"^research-erratum-[a-f0-9]{16}$")
    study_id: str
    notice_id: str = Field(pattern=r"^component-defect-[a-f0-9]{16}$")
    affected_artifact_ids: list[str] = Field(default_factory=list)
    affected_verdict_ids: list[str] = Field(default_factory=list)
    verdict_overlay_status: Literal["under_review"] = "under_review"
    historical_records_preserved: Literal[True] = True
    created_at: str = Field(default_factory=utc_now)


class GlobalImpactAnalysis(StrictModel):
    schema_version: int = 1
    analysis_id: str = Field(pattern=r"^global-impact-[a-f0-9]{16}$")
    notice_id: str = Field(pattern=r"^component-defect-[a-f0-9]{16}$")
    affected_study_ids: list[str] = Field(default_factory=list)
    affected_artifact_ids_by_study: dict[str, list[str]] = Field(
        default_factory=dict
    )
    affected_verdict_ids_by_study: dict[str, list[str]] = Field(
        default_factory=dict
    )
    suggested_actions: list[str] = Field(
        default_factory=lambda: [
            "review affected verdicts",
            "propose bounded repair contracts",
            "create successor runs where scientific outputs are affected",
        ]
    )
    created_at: str = Field(default_factory=utc_now)


class EvidenceEdge(StrictModel):
    schema_version: int = 1
    edge_id: str = Field(pattern=r"^evidence-edge-[a-f0-9]{16}$")
    study_id: str
    source_id: str
    target_id: str
    relation_type: EvidenceRelation
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_by: str
    qualification_status: QualificationStatus
    created_at: str = Field(default_factory=utc_now)


class DiagnosticReport(StrictModel):
    schema_version: int = 1
    diagnostic_id: str = Field(pattern=r"^stage3-diagnostic-[a-f0-9]{16}$")
    study_id: str
    plan_id: str = Field(pattern=r"^run-plan-[a-f0-9]{16}$")
    earliest_preventable_step_type: str
    failure_class: Stage3FailureClass
    system_invalidation_artifact_ids: list[str] = Field(default_factory=list)
    system_findings: list[str] = Field(default_factory=list)
    ai_root_cause_hypotheses: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class SuccessorRun(StrictModel):
    schema_version: int = 1
    successor_id: str = Field(pattern=r"^successor-[a-f0-9]{16}$")
    study_id: str
    predecessor_plan_id: str = Field(pattern=r"^run-plan-[a-f0-9]{16}$")
    successor_plan_id: str = Field(pattern=r"^run-plan-[a-f0-9]{16}$")
    repair_contract_id: str = Field(pattern=r"^repair-[a-f0-9]{16}$")
    reused_artifact_ids: list[str] = Field(default_factory=list)
    invalidated_artifact_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class ScientificSuccessorRequest(StrictModel):
    schema_version: int = 1
    request_id: str = Field(
        pattern=r"^scientific-successor-[a-f0-9]{16}$"
    )
    study_id: str
    predecessor_contract_version: int = Field(ge=1)
    predecessor_plan_id: str = Field(pattern=r"^run-plan-[a-f0-9]{16}$")
    diagnostic_id: str = Field(
        pattern=r"^stage3-diagnostic-[a-f0-9]{16}$"
    )
    changed_contract_fields: list[str] = Field(min_length=1)
    rationale: list[str] = Field(min_length=1)
    required_actions: list[str] = Field(
        default_factory=lambda: [
            "create Research Contract vNext",
            "obtain project-owner contract approval",
            "freeze a new scientific specification seal",
            "build and freeze a new execution package",
            "run a new formal paired matrix",
        ]
    )
    historical_run_ids_preserved: list[str] = Field(default_factory=list)
    historical_verdict_ids_preserved: list[str] = Field(default_factory=list)
    status: Literal["proposed"] = "proposed"
    created_at: str = Field(default_factory=utc_now)


class Stage3CompletionPackage(StrictModel):
    schema_version: int = 1
    completion_id: str = Field(pattern=r"^stage3-completion-[a-f0-9]{16}$")
    study_id: str
    plan_id: str = Field(pattern=r"^run-plan-[a-f0-9]{16}$")
    handoff_id: str = Field(pattern=r"^stage3-handoff-[a-f0-9]{16}$")
    evaluation_ids: list[str] = Field(min_length=1)
    evidence_edge_ids: list[str] = Field(min_length=1)
    hypothesis_verdict_ids: list[str] = Field(min_length=1)
    study_verdict_id: str
    qualification_status: QualificationStatus
    artifact_hashes: dict[str, str] = Field(min_length=1)
    exposure_record_ids: list[str] = Field(default_factory=list)
    statistical_assurance_report_id: str | None = None
    leakage_audit_report_id: str | None = None
    evaluator_disagreement_report_ids: list[str] = Field(default_factory=list)
    claim_envelope_id: str | None = None
    evidence_level: EvidenceReproductionLevel = (
        EvidenceReproductionLevel.EVIDENCE_CHAIN_VERIFIED
    )
    confirmatory_status: ConfirmatoryStatus = ConfirmatoryStatus.UNTOUCHED
    execution_status: ExecutionStatus = ExecutionStatus.SUCCEEDED
    analysis_eligibility: AnalysisEligibilityStatus = (
        AnalysisEligibilityStatus.QUALIFIED
    )
    scientific_verdict_status: StudyVerdictStatus | None = None
    evidence_maturity: EvidenceMaturity = EvidenceMaturity.PROSPECTIVE
    publication_mode: PublicationMode = PublicationMode.RESULTS_MANUSCRIPT
    blocking_issue_report_id: str | None = None
    contract_amendment_required: bool = False
    created_at: str = Field(default_factory=utc_now)


class LiteratureSetVersion(StrictModel):
    schema_version: int = 1
    literature_set_id: str = Field(pattern=r"^literature-[a-z0-9-]{2,100}$")
    study_id: str
    version: int = Field(ge=1)
    status: ArtifactStatus = ArtifactStatus.DRAFT
    background_source_ids: list[str] = Field(default_factory=list)
    decision_source_ids: list[str] = Field(default_factory=list)
    retracted_source_ids: list[str] = Field(default_factory=list)
    predecessor_version: int | None = None
    affects_novelty: bool = False
    affects_research_design: bool = False
    created_at: str = Field(default_factory=utc_now)


class EvidenceChain(StrictModel):
    schema_version: int = 1
    chain_id: str = Field(pattern=r"^chain-[a-f0-9]{16}$")
    study_id: str
    level: EvidenceChainLevel
    protocol_artifact_id: str
    run_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    evaluation_artifact_ids: list[str] = Field(default_factory=list)
    claim_artifact_ids: list[str] = Field(default_factory=list)
    implementation_version: str | None = None
    inference_rationale: str | None = None
    verified_checks: dict[str, bool] = Field(default_factory=dict)

    def verdict_eligible(self) -> bool:
        return self.level is EvidenceChainLevel.VERIFIED and bool(
            self.output_artifact_ids
        )


class HypothesisVerdict(StrictModel):
    schema_version: int = 1
    verdict_id: str = Field(pattern=r"^hverdict-[a-f0-9]{16}$")
    study_id: str
    hypothesis_id: str
    status: HypothesisVerdictStatus
    evidence_chain_ids: list[str]
    eligible_evidence: bool
    rationale: str
    supersedes: str | None = None
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def unsupported_without_verified_evidence(self) -> "HypothesisVerdict":
        if (
            self.status
            in {HypothesisVerdictStatus.SUPPORTED, HypothesisVerdictStatus.REFUTED}
            and not self.eligible_evidence
        ):
            raise ValueError(
                "supported/refuted verdict requires eligible verified evidence"
            )
        return self


class StudyVerdict(StrictModel):
    schema_version: int = 1
    verdict_id: str = Field(pattern=r"^sverdict-[a-f0-9]{16}$")
    study_id: str
    status: StudyVerdictStatus
    hypothesis_verdict_ids: list[str]
    rationale: str
    supersedes: str | None = None
    created_at: str = Field(default_factory=utc_now)


class AdjudicationRecord(StrictModel):
    schema_version: int = 1
    adjudication_id: str = Field(pattern=r"^adjudication-[a-f0-9]{16}$")
    study_id: str
    subject_verdict_id: str
    decision: Literal["confirm", "invalidate", "abstain"]
    reason: str = Field(min_length=5, max_length=4_000)
    adjudicator: str
    created_at: str = Field(default_factory=utc_now)


class NLIRiskAlert(StrictModel):
    schema_version: int = 1
    alert_id: str = Field(pattern=r"^nli-alert-[a-f0-9]{16}$")
    study_id: str
    claim_artifact_id: str
    label: Literal["supported", "contradiction", "neutral", "review_required"]
    scores: dict[str, float]
    authority: Literal["risk_alert_only"] = "risk_alert_only"
    created_at: str = Field(default_factory=utc_now)


class ArtifactRecord(StrictModel):
    schema_version: int = 1
    artifact_id: str = Field(pattern=r"^artifact-[a-f0-9]{16}$")
    study_id: str
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: ArtifactStatus = ArtifactStatus.FROZEN
    version: int = Field(default=1, ge=1)
    role: ArtifactRole = ArtifactRole.OTHER
    predecessor_artifact_id: str | None = None
    created_at: str = Field(default_factory=utc_now)


class ArtifactDependency(StrictModel):
    schema_version: int = 1
    dependency_id: str = Field(pattern=r"^dependency-[a-f0-9]{16}$")
    study_id: str
    input_artifact_id: str
    output_artifact_id: str
    relation: str = "derived_from"
    created_at: str = Field(default_factory=utc_now)


class WorkflowDiagnostic(StrictModel):
    """Append-only fault-localization record.

    Diagnostics explain why a successor may be needed.  They never mutate a
    historical step result or scientific verdict.
    """

    schema_version: int = 1
    diagnostic_id: str = Field(pattern=r"^diagnostic-[a-f0-9]{16}$")
    study_id: str
    phase: Phase
    earliest_affected_step_type: str = Field(
        pattern=r"^[a-z][a-z0-9_]{1,99}$"
    )
    diagnostic_owner: DiagnosticOwner
    failure_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    rationale: str = Field(min_length=3, max_length=4_000)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    scientific_change: bool = False
    auto_repair_eligible: bool = False
    created_at: str = Field(default_factory=utc_now)


class RepairContract(StrictModel):
    schema_version: int = 1
    repair_id: str = Field(pattern=r"^repair-[a-f0-9]{16}$")
    study_id: str
    version: int = Field(ge=1)
    diagnostic_owner: DiagnosticOwner
    status: RepairStatus = RepairStatus.REPAIR_PROPOSED
    scientific_change: bool
    earliest_affected_phase: Phase
    changed_artifact_ids: list[str]
    invalidated_artifact_ids: list[str]
    reusable_artifact_ids: list[str]
    regression_checks: list[dict[str, Any]]
    diagnostic_id: str | None = None
    earliest_affected_step_type: str | None = None
    predecessor_run_id: str | None = None
    successor_run_id: str | None = None
    predecessor_study_id: str | None = None
    successor_study_id: str | None = None
    approved_by: str | None = None
    created_at: str = Field(default_factory=utc_now)


class PublicationIntegrityGates(StrictModel):
    schema_version: int = 1
    scientific_integrity: IntegrityGateStatus = IntegrityGateStatus.PENDING
    narrative_integrity: IntegrityGateStatus = IntegrityGateStatus.PENDING
    humanization_integrity: IntegrityGateStatus = IntegrityGateStatus.PENDING
    visual_integrity: IntegrityGateStatus = IntegrityGateStatus.PENDING

    def all_passed(self) -> bool:
        return all(
            value is IntegrityGateStatus.PASSED
            for value in (
                self.scientific_integrity,
                self.narrative_integrity,
                self.humanization_integrity,
                self.visual_integrity,
            )
        )


class ReadinessAssessment(StrictModel):
    schema_version: int = 2
    study_id: str
    system_publication_readiness: SystemReadiness
    ai_scientific_review: AIReviewStatus = AIReviewStatus.PENDING
    author_publication_approval: AuthorApprovalStatus = AuthorApprovalStatus.PENDING
    integrity_gates: PublicationIntegrityGates = Field(
        default_factory=PublicationIntegrityGates
    )
    system_checks: dict[str, bool] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    assessed_at: str = Field(default_factory=utc_now)
    publication_ready: bool = False

    @model_validator(mode="after")
    def derive_publication_ready(self) -> "ReadinessAssessment":
        value = (
            self.system_publication_readiness is SystemReadiness.CONDITIONS_MET
            and self.ai_scientific_review is AIReviewStatus.PASSED
            and self.author_publication_approval is AuthorApprovalStatus.APPROVED
            and self.integrity_gates.all_passed()
        )
        object.__setattr__(self, "publication_ready", value)
        return self


class PublicationApproval(StrictModel):
    schema_version: int = 1
    approval_id: str = Field(pattern=r"^publication-approval-[a-f0-9]{16}$")
    study_id: str
    status: AuthorApprovalStatus
    decided_by: str
    reason: str | None = None
    created_at: str = Field(default_factory=utc_now)


class CompletionRecord(StrictModel):
    schema_version: int = 1
    completion_record_id: str = Field(pattern=r"^completion-[a-f0-9]{16}$")
    project_id: str
    study_id: str
    scope_version: int | None = None
    research_contract_version: int | None = None
    run_ids: list[str] = Field(default_factory=list)
    completed_at: str = Field(default_factory=utc_now)
    issuer: str = "research-forge-local"
    issuer_version: str
    readiness: ReadinessAssessment
    artifact_hashes: dict[str, str] = Field(min_length=1)
    verification_command: str
    legacy_certificate_path: str | None = None
    record_sha256: str = Field(default="0" * 64, pattern=r"^[a-f0-9]{64}$")
    publication_ready: bool = False

    @model_validator(mode="after")
    def derive_publication_ready(self) -> "CompletionRecord":
        object.__setattr__(self, "publication_ready", self.readiness.publication_ready)
        return self


def seal_completion_record(record: CompletionRecord) -> CompletionRecord:
    payload = record.model_dump(mode="json", exclude={"record_sha256"})
    digest = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return record.model_copy(update={"record_sha256": digest})


def stable_id(prefix: str, *parts: object) -> str:
    material = "\0".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"


def research_support_level(research_type: str) -> ResearchSupportLevel:
    normalized = research_type.strip().casefold().replace("-", "_")
    formal = {"computational", "simulation", "computational_observational", "ai_ml"}
    return (
        ResearchSupportLevel.FORMAL
        if normalized in formal
        else ResearchSupportLevel.DIAGNOSTIC_ONLY
    )


def aggregate_study_verdict(
    study_id: str,
    hypotheses: Iterable[Hypothesis],
    verdicts: Iterable[HypothesisVerdict],
    *,
    supersedes: str | None = None,
) -> StudyVerdict:
    by_hypothesis = {item.hypothesis_id: item for item in verdicts}
    primary = [item for item in hypotheses if item.role is HypothesisRole.PRIMARY]
    if not primary:
        raise ValueError("study verdict requires at least one primary hypothesis")
    selected = [by_hypothesis.get(item.hypothesis_id) for item in primary]
    if any(item is None for item in selected):
        raise ValueError("every primary hypothesis requires a verdict")
    resolved = [item for item in selected if item is not None]
    statuses = {item.status for item in resolved}
    if len(resolved) == 1:
        status = StudyVerdictStatus(resolved[0].status.value)
    elif HypothesisVerdictStatus.UNVERIFIABLE in statuses and not any(
        item.eligible_evidence for item in resolved
    ):
        status = StudyVerdictStatus.UNVERIFIABLE
    elif {
        HypothesisVerdictStatus.SUPPORTED,
        HypothesisVerdictStatus.REFUTED,
    }.issubset(statuses):
        status = StudyVerdictStatus.MIXED
    elif statuses == {HypothesisVerdictStatus.SUPPORTED}:
        status = StudyVerdictStatus.SUPPORTED
    elif statuses == {HypothesisVerdictStatus.REFUTED}:
        status = StudyVerdictStatus.REFUTED
    else:
        status = StudyVerdictStatus.INCONCLUSIVE
    ids = [item.verdict_id for item in resolved]
    return StudyVerdict(
        verdict_id=stable_id("sverdict", study_id, *ids, supersedes or ""),
        study_id=study_id,
        status=status,
        hypothesis_verdict_ids=ids,
        rationale="Aggregated from preregistered primary-hypothesis verdicts.",
        supersedes=supersedes,
    )


def affected_artifacts(
    changed_artifact_ids: Iterable[str],
    dependencies: Iterable[ArtifactDependency],
) -> set[str]:
    outgoing: dict[str, set[str]] = defaultdict(set)
    for edge in dependencies:
        outgoing[edge.input_artifact_id].add(edge.output_artifact_id)
    affected = set(changed_artifact_ids)
    queue = deque(affected)
    while queue:
        current = queue.popleft()
        for downstream in outgoing.get(current, set()):
            if downstream not in affected:
                affected.add(downstream)
                queue.append(downstream)
    return affected


def is_secret_path(path: str) -> bool:
    normalized = path.replace("\\", "/").casefold()
    name = normalized.rsplit("/", 1)[-1]
    return (
        name in _SECRET_NAMES
        or name.startswith(".env.")
        or any(fragment in name for fragment in _SECRET_FRAGMENTS)
        or name.endswith((".pem", ".key", ".p12", ".pfx"))
    )


class WorkflowRepository:
    """Filesystem repository for Projects, Studies, DAG steps, and audit records."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _project_path(self, project_id: str) -> Path:
        self._validate_id(project_id, "project")
        return self.root / "projects" / project_id / "project.json"

    def save_step_definition(self, definition: StepDefinition) -> StepDefinition:
        write_json_atomic(
            self.root / "step_definitions" / f"{definition.step_type}.json",
            definition,
        )
        return definition

    def list_step_definitions(self) -> list[StepDefinition]:
        return [
            StepDefinition.model_validate(read_json(path))
            for path in sorted((self.root / "step_definitions").glob("*.json"))
        ]

    def _study_dir(self, study_id: str) -> Path:
        self._validate_id(study_id, "study")
        return self.root / "studies" / study_id

    @staticmethod
    def _validate_id(value: str, prefix: str) -> None:
        if re.fullmatch(rf"{prefix}-{_ID_COMPONENT}", value) is None:
            raise ValueError(f"invalid {prefix} id")

    def _event(self, study_id: str, event: str, **detail: Any) -> str:
        event_id = stable_id("event", study_id, event, utc_now(), uuid.uuid4().hex)
        append_jsonl(
            self._study_dir(study_id) / "events.jsonl",
            {
                "schema_version": 1,
                "event_id": event_id,
                "study_id": study_id,
                "event": event,
                "recorded_at": utc_now(),
                **detail,
            },
        )
        return event_id

    def create_project(
        self,
        title: str,
        *,
        source_root: str | None = None,
        project_id: str | None = None,
        network_policy: NetworkPolicy | None = None,
    ) -> ProjectRecord:
        identity = project_id or stable_id("project", source_root or title)
        path = self._project_path(identity)
        if path.is_file():
            return ProjectRecord.model_validate(read_json(path))
        project = ProjectRecord(
            project_id=identity,
            title=title,
            source_root=source_root,
            network_policy=network_policy or NetworkPolicy(),
        )
        write_json_atomic(path, project)
        return project

    def load_project(self, project_id: str) -> ProjectRecord:
        return ProjectRecord.model_validate(read_json(self._project_path(project_id)))

    def save_project(self, project: ProjectRecord) -> ProjectRecord:
        self.load_project(project.project_id)
        updated = project.model_copy(update={"updated_at": utc_now()})
        write_json_atomic(self._project_path(project.project_id), updated)
        return updated

    def list_projects(self) -> list[ProjectRecord]:
        return [
            ProjectRecord.model_validate(read_json(path))
            for path in sorted((self.root / "projects").glob("*/project.json"))
        ]

    def record_network_request(self, event: NetworkAuditEvent) -> NetworkAuditEvent:
        project = self.load_project(event.project_id)
        if not project.network_policy.network_enabled:
            raise ValueError("project network policy is offline")
        if event.study_id:
            study = self.load_study(event.study_id)
            if study.project_id != event.project_id:
                raise ValueError("network event study does not belong to project")
        ledger = self._project_path(event.project_id).parent / "network_audit.jsonl"
        spent = sum(float(item.get("cost", 0)) for item in load_jsonl(ledger))
        if (
            project.network_policy.budget_limit is not None
            and spent + event.cost > project.network_policy.budget_limit
        ):
            if event.study_id:
                study = self.load_study(event.study_id)
                self.save_study(
                    study.model_copy(
                        update={"execution_status": ExecutionStatus.BLOCKED}
                    ),
                    "network_budget_blocked",
                )
            raise ValueError("project network budget exceeded")
        append_jsonl(ledger, event)
        return event

    def create_study(
        self,
        project_id: str,
        title: str,
        *,
        entry_mode: EntryMode,
        research_type: str = "computational",
        study_id: str | None = None,
        predecessor_study_id: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> StudyRecord:
        self.load_project(project_id)
        identity = study_id or stable_id(
            "study", project_id, title, entry_mode.value, uuid.uuid4().hex
        )
        path = self._study_dir(identity) / "study.json"
        if path.is_file():
            return StudyRecord.model_validate(read_json(path))
        study = StudyRecord(
            study_id=identity,
            project_id=project_id,
            title=title,
            entry_mode=entry_mode,
            research_type=research_type,
            support_level=research_support_level(research_type),
            predecessor_study_id=predecessor_study_id,
            settings=settings or {},
        )
        write_json_atomic(path, study)
        self._event(identity, "study_created")
        return study

    def load_study(self, study_id: str) -> StudyRecord:
        return StudyRecord.model_validate(
            read_json(self._study_dir(study_id) / "study.json")
        )

    def save_study(
        self, study: StudyRecord, event: str = "study_updated"
    ) -> StudyRecord:
        updated = study.model_copy(update={"updated_at": utc_now()})
        write_json_atomic(self._study_dir(study.study_id) / "study.json", updated)
        self._event(study.study_id, event)
        return updated

    def list_studies(self, project_id: str | None = None) -> list[StudyRecord]:
        studies = [
            StudyRecord.model_validate(read_json(path))
            for path in sorted((self.root / "studies").glob("*/study.json"))
        ]
        if project_id:
            studies = [item for item in studies if item.project_id == project_id]
        return sorted(studies, key=lambda item: item.updated_at, reverse=True)

    def add_step(
        self,
        study_id: str,
        step_type: str,
        phase: Phase,
        executor_type: ExecutorType,
        *,
        depends_on: list[str] | None = None,
        parent_step_id: str | None = None,
        task_group: str | None = None,
        parameters: dict[str, Any] | None = None,
        expected_output: str | None = None,
    ) -> StepInstance:
        study = self.load_study(study_id)
        step_id = stable_id(
            "step", study_id, step_type, task_group or "", uuid.uuid4().hex
        )
        dependencies = list(dict.fromkeys(depends_on or []))
        for dependency in dependencies:
            self.load_step(study_id, dependency)
        step = StepInstance(
            step_instance_id=step_id,
            study_id=study_id,
            step_type=step_type,
            phase=phase,
            executor_type=executor_type,
            depends_on=dependencies,
            parent_step_id=parent_step_id,
            task_group=task_group,
            parameters=dict(parameters or {}),
            expected_output=expected_output,
        )
        write_json_atomic(self._study_dir(study_id) / "steps" / f"{step_id}.json", step)
        self._assert_acyclic(study_id)
        self.save_study(
            study.model_copy(update={"phase": phase}),
            "step_added",
        )
        self._event(study_id, "step_created", step_instance_id=step_id)
        return step

    def load_step(self, study_id: str, step_id: str) -> StepInstance:
        return StepInstance.model_validate(
            read_json(self._study_dir(study_id) / "steps" / f"{step_id}.json")
        )

    def list_steps(self, study_id: str) -> list[StepInstance]:
        self.load_study(study_id)
        return [
            StepInstance.model_validate(read_json(path))
            for path in sorted(
                (self._study_dir(study_id) / "steps").glob("step-*.json")
            )
        ]

    def save_step_result(
        self,
        study_id: str,
        step_id: str,
        result: dict[str, Any],
        *,
        predecessor_artifact_id: str | None = None,
        lease_id: str | None = None,
        fencing_token: int | None = None,
    ) -> ArtifactRecord:
        """Persist a node result and register it as a first-class DAG artifact."""
        step = self.load_step(study_id, step_id)
        if lease_id is not None or (
            fencing_token is not None and fencing_token > 0
        ):
            if (
                step.lease_id != lease_id
                or step.fencing_token != fencing_token
                or step.status
                not in {ExecutionStatus.RUNNING, ExecutionStatus.RETRYING}
            ):
                raise ValueError(
                    "expired or mismatched execution lease cannot publish "
                    "a step result"
                )
        path = self._study_dir(study_id) / "step_results" / f"{step_id}.json"
        write_json_atomic(path, result)
        artifact = self.register_artifact(
            study_id,
            str(path),
            sha256_file(path),
            kind="step_result",
            role=ArtifactRole.OTHER,
            predecessor_artifact_id=predecessor_artifact_id,
        )
        self._event(
            study_id,
            "step_result_written",
            step_instance_id=step_id,
            artifact_id=artifact.artifact_id,
        )
        return artifact

    def load_step_result(self, study_id: str, step_id: str) -> dict[str, Any]:
        self.load_step(study_id, step_id)
        return read_json(self._study_dir(study_id) / "step_results" / f"{step_id}.json")

    def _assert_acyclic(self, study_id: str) -> None:
        steps = self.list_steps(study_id)
        dependencies = {item.step_instance_id: set(item.depends_on) for item in steps}
        remaining = set(dependencies)
        while remaining:
            ready = {
                step_id
                for step_id in remaining
                if not dependencies[step_id].intersection(remaining)
            }
            if not ready:
                raise ValueError("step dependency graph contains a cycle")
            remaining.difference_update(ready)

    def update_step(
        self,
        study_id: str,
        step_id: str,
        status: ExecutionStatus,
        *,
        blocker: dict[str, Any] | None = None,
        input_artifact_ids: list[str] | None = None,
        output_artifact_ids: list[str] | None = None,
        acceptance_status: StepAcceptanceStatus | None = None,
        output_produced: bool | None = None,
        schema_validated: bool | None = None,
        scientific_postcondition_passed: bool | None = None,
        acceptance_checks: dict[str, bool] | None = None,
        lease_id: str | None = None,
        fencing_token: int | None = None,
    ) -> StepInstance:
        step = self.load_step(study_id, step_id)
        if lease_id is not None or (
            fencing_token is not None and fencing_token > 0
        ):
            if (
                step.lease_id != lease_id
                or step.fencing_token != fencing_token
                or step.status
                not in {
                    ExecutionStatus.RUNNING,
                    ExecutionStatus.RETRYING,
                }
            ):
                raise ValueError(
                    "expired or mismatched execution lease cannot update "
                    "step status"
                )
        study = self.load_study(study_id)
        if (
            status in {ExecutionStatus.RUNNING, ExecutionStatus.RETRYING}
            and study.execution_status is ExecutionStatus.PAUSED
        ):
            raise ValueError("paused study cannot start a new step attempt")
        if status is ExecutionStatus.RETRYING and step.attempt >= step.max_retries + 1:
            raise ValueError("step retry limit exhausted")
        if status is ExecutionStatus.RUNNING:
            unfinished = [
                dependency
                for dependency in step.depends_on
                if self.load_step(study_id, dependency).status
                is not ExecutionStatus.SUCCEEDED
            ]
            if unfinished:
                raise ValueError(
                    "step dependencies are not complete: " + ", ".join(unfinished)
                )
        update: dict[str, Any] = {
            "status": status,
            "updated_at": utc_now(),
            "blocker": blocker,
        }
        if status is ExecutionStatus.QUEUED:
            update["completed_at"] = None
        if status in {ExecutionStatus.RUNNING, ExecutionStatus.RETRYING}:
            update["attempt"] = step.attempt + 1
            update["started_at"] = step.started_at or utc_now()
            update["lease_id"] = f"lease-{uuid.uuid4().hex[:16]}"
            update["fencing_token"] = step.fencing_token + 1
        if status in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
        }:
            update["completed_at"] = utc_now()
        if input_artifact_ids is not None:
            update["input_artifact_ids"] = input_artifact_ids
        if output_artifact_ids is not None:
            update["output_artifact_ids"] = output_artifact_ids
        if acceptance_status is not None:
            update["acceptance_status"] = acceptance_status
        if output_produced is not None:
            update["output_produced"] = output_produced
        if schema_validated is not None:
            update["schema_validated"] = schema_validated
        if scientific_postcondition_passed is not None:
            update["scientific_postcondition_passed"] = (
                scientific_postcondition_passed
            )
        if acceptance_checks is not None:
            update["acceptance_checks"] = acceptance_checks
        event_id = self._event(
            study_id,
            "step_status_changed",
            step_instance_id=step_id,
            status=status.value,
        )
        update["audit_event_ids"] = [*step.audit_event_ids, event_id]
        updated = step.model_copy(update=update)
        write_json_atomic(
            self._study_dir(study_id) / "steps" / f"{step_id}.json", updated
        )
        active = [
            item.step_instance_id
            for item in self.list_steps(study_id)
            if item.status
            in {
                ExecutionStatus.RUNNING,
                ExecutionStatus.RETRYING,
                ExecutionStatus.WAITING_FOR_USER,
            }
        ]
        aggregate = (
            ExecutionStatus.PAUSED
            if study.execution_status is ExecutionStatus.PAUSED
            else status
        )
        self.save_study(
            study.model_copy(
                update={
                    "phase": step.phase,
                    "execution_status": aggregate,
                    "current_step_ids": active,
                }
            ),
            "study_execution_updated",
        )
        return updated

    def update_step_parameters(
        self,
        study_id: str,
        step_id: str,
        parameters: dict[str, Any],
    ) -> StepInstance:
        """Replace parameters before execution while preserving an audit trail."""

        step = self.load_step(study_id, step_id)
        if step.status is not ExecutionStatus.QUEUED or step.attempt:
            raise ValueError(
                "step parameters can only change before the first attempt"
            )
        event_id = self._event(
            study_id,
            "step_parameters_changed",
            step_instance_id=step_id,
            parameter_keys=sorted(parameters),
        )
        updated = step.model_copy(
            update={
                "parameters": dict(parameters),
                "updated_at": utc_now(),
                "audit_event_ids": [*step.audit_event_ids, event_id],
            }
        )
        write_json_atomic(
            self._study_dir(study_id) / "steps" / f"{step_id}.json",
            updated,
        )
        return updated

    def pause_study(self, study_id: str) -> StudyRecord:
        study = self.load_study(study_id)
        if study.lifecycle is not StudyLifecycle.ACTIVE:
            raise ValueError("only an active study can be paused")
        return self.save_study(
            study.model_copy(update={"execution_status": ExecutionStatus.PAUSED}),
            "study_paused",
        )

    def resume_study(self, study_id: str) -> StudyRecord:
        study = self.load_study(study_id)
        if study.execution_status is not ExecutionStatus.PAUSED:
            raise ValueError("study is not paused")
        return self.save_study(
            study.model_copy(update={"execution_status": ExecutionStatus.QUEUED}),
            "study_resumed",
        )

    def finish_study(self, study_id: str, lifecycle: StudyLifecycle) -> StudyRecord:
        if lifecycle is StudyLifecycle.ACTIVE:
            raise ValueError("finish_study requires a terminal lifecycle")
        study = self.load_study(study_id)
        status = (
            ExecutionStatus.SUCCEEDED
            if lifecycle is StudyLifecycle.COMPLETED
            else ExecutionStatus.CANCELLED
        )
        return self.save_study(
            study.model_copy(
                update={"lifecycle": lifecycle, "execution_status": status}
            ),
            f"study_{lifecycle.value}",
        )

    def create_gate(
        self,
        study_id: str,
        gate_type: GateType,
        subject_type: str,
        subject_id: str,
        *,
        subject_version: int | None = None,
        status: GateStatus = GateStatus.AWAITING_USER,
    ) -> GateRecord:
        self.load_study(study_id)
        gate = GateRecord(
            gate_id=stable_id(
                "gate", study_id, gate_type.value, subject_id, subject_version or 0
            ),
            study_id=study_id,
            gate_type=gate_type,
            status=status,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_version=subject_version,
        )
        write_json_atomic(
            self._study_dir(study_id) / "gates" / f"{gate.gate_id}.json", gate
        )
        self._event(study_id, "gate_created", gate_id=gate.gate_id)
        return gate

    def decide_gate(
        self,
        study_id: str,
        gate_id: str,
        *,
        approve: bool,
        decided_by: str,
        reason: str | None = None,
    ) -> GateRecord:
        path = self._study_dir(study_id) / "gates" / f"{gate_id}.json"
        gate = GateRecord.model_validate(read_json(path))
        if gate.status not in {GateStatus.AWAITING_USER, GateStatus.REJECTED}:
            raise ValueError("gate has already been decided")
        updated = gate.model_copy(
            update={
                "status": GateStatus.APPROVED if approve else GateStatus.REJECTED,
                "decided_by": decided_by,
                "reason": reason,
                "decided_at": utc_now(),
            }
        )
        write_json_atomic(path, updated)
        self._event(
            study_id,
            "gate_decided",
            gate_id=gate_id,
            decision=updated.status.value,
        )
        return updated

    def list_gates(self, study_id: str) -> list[GateRecord]:
        return [
            GateRecord.model_validate(read_json(path))
            for path in sorted(
                (self._study_dir(study_id) / "gates").glob("gate-*.json")
            )
        ]

    def save_scope_contract(
        self, contract: ScopeContractVersion
    ) -> ScopeContractVersion:
        self.load_study(contract.study_id)
        path = (
            self._study_dir(contract.study_id)
            / "contracts"
            / f"scope-v{contract.version}.json"
        )
        if path.is_file():
            existing = ScopeContractVersion.model_validate(read_json(path))
            if existing.status is ArtifactStatus.FROZEN:
                if existing.model_dump(mode="json") != contract.model_dump(mode="json"):
                    raise ValueError("a frozen scope contract requires a new version")
                return existing
            if contract.status is ArtifactStatus.FROZEN and not self._gate_approved(
                contract.study_id,
                GateType.SCOPE_APPROVAL,
                "scope_contract",
                contract.version,
            ):
                raise ValueError("scope contract cannot freeze before owner approval")
        elif contract.status is ArtifactStatus.FROZEN and not self._gate_approved(
            contract.study_id,
            GateType.SCOPE_APPROVAL,
            "scope_contract",
            contract.version,
        ):
            raise ValueError("scope contract cannot freeze before owner approval")
        write_json_atomic(path, contract)
        study = self.load_study(contract.study_id)
        active = (
            contract.version
            if contract.status is ArtifactStatus.FROZEN
            else study.active_scope_version
        )
        self.save_study(
            study.model_copy(update={"active_scope_version": active}),
            "scope_contract_saved",
        )
        return contract

    def load_scope_contract(
        self, study_id: str, version: int
    ) -> ScopeContractVersion:
        return ScopeContractVersion.model_validate(
            read_json(
                self._study_dir(study_id)
                / "contracts"
                / f"scope-v{version}.json"
            )
        )

    def latest_scope_contract(
        self, study_id: str
    ) -> ScopeContractVersion | None:
        paths = sorted(
            (self._study_dir(study_id) / "contracts").glob("scope-v*.json"),
            key=lambda item: int(item.stem.rsplit("v", 1)[-1]),
        )
        return (
            ScopeContractVersion.model_validate(read_json(paths[-1]))
            if paths
            else None
        )

    def save_research_contract(
        self, contract: ResearchContractVersion
    ) -> ResearchContractVersion:
        self.load_study(contract.study_id)
        path = (
            self._study_dir(contract.study_id)
            / "contracts"
            / f"research-v{contract.version}.json"
        )
        if path.is_file():
            existing = ResearchContractVersion.model_validate(read_json(path))
            if existing.status is ArtifactStatus.FROZEN:
                if existing.model_dump(mode="json") != contract.model_dump(mode="json"):
                    raise ValueError(
                        "a frozen research contract requires a new version"
                    )
                return existing
        if (
            contract.status is ArtifactStatus.FROZEN
            and contract.schema_version >= 2
            and contract.protocol_status
            is not ProtocolStatus.FROZEN_EXECUTABLE
        ):
            raise ValueError(
                "a Research Contract may freeze only after contract "
                "compilation and a non-evidentiary dry run"
            )
        if path.is_file():
            existing = ResearchContractVersion.model_validate(read_json(path))
            if contract.status is ArtifactStatus.FROZEN and not self._gate_approved(
                contract.study_id,
                GateType.RESEARCH_CONTRACT,
                "research_contract",
                contract.version,
            ):
                raise ValueError(
                    "research contract cannot freeze before owner approval"
                )
        elif contract.status is ArtifactStatus.FROZEN and not self._gate_approved(
            contract.study_id,
            GateType.RESEARCH_CONTRACT,
            "research_contract",
            contract.version,
        ):
            raise ValueError("research contract cannot freeze before owner approval")
        write_json_atomic(path, contract)
        study = self.load_study(contract.study_id)
        active = (
            contract.version
            if contract.status is ArtifactStatus.FROZEN
            else study.active_contract_version
        )
        self.save_study(
            study.model_copy(update={"active_contract_version": active}),
            "research_contract_saved",
        )
        return contract

    def load_research_contract(
        self, study_id: str, version: int
    ) -> ResearchContractVersion:
        return ResearchContractVersion.model_validate(
            read_json(
                self._study_dir(study_id)
                / "contracts"
                / f"research-v{version}.json"
            )
        )

    def latest_research_contract(
        self, study_id: str
    ) -> ResearchContractVersion | None:
        paths = sorted(
            (self._study_dir(study_id) / "contracts").glob("research-v*.json"),
            key=lambda item: int(item.stem.rsplit("v", 1)[-1]),
        )
        return (
            ResearchContractVersion.model_validate(read_json(paths[-1]))
            if paths
            else None
        )

    def _gate_approved(
        self,
        study_id: str,
        gate_type: GateType,
        subject_type: str,
        subject_version: int,
    ) -> bool:
        return any(
            gate.gate_type is gate_type
            and gate.subject_type == subject_type
            and gate.subject_version == subject_version
            and gate.status is GateStatus.APPROVED
            for gate in self.list_gates(study_id)
        )

    def save_baseline_verification(
        self, verification: BaselineVerificationContract
    ) -> BaselineVerificationContract:
        self.load_study(verification.study_id)
        write_json_atomic(
            self._study_dir(verification.study_id)
            / "runs"
            / verification.run_id
            / "baseline_verification.json",
            {
                **verification.model_dump(mode="json"),
                "baseline_verified": verification.baseline_verified,
            },
        )
        self._event(
            verification.study_id,
            "baseline_verified"
            if verification.baseline_verified
            else "baseline_blocked",
            run_id=verification.run_id,
        )
        return verification

    def save_research_run(self, run: ResearchRun) -> ResearchRun:
        self.load_study(run.study_id)
        write_json_atomic(
            self._study_dir(run.study_id) / "runs" / run.run_id / "run.json", run
        )
        self._event(run.study_id, "research_run_saved", run_id=run.run_id)
        return run

    def load_research_run(self, study_id: str, run_id: str) -> ResearchRun:
        return ResearchRun.model_validate(
            read_json(
                self._study_dir(study_id) / "runs" / run_id / "run.json"
            )
        )

    def list_research_runs(self, study_id: str) -> list[ResearchRun]:
        self.load_study(study_id)
        return [
            ResearchRun.model_validate(read_json(path))
            for path in sorted(
                (self._study_dir(study_id) / "runs").glob("run-*/run.json")
            )
        ]

    def save_experiment_blueprint(
        self, blueprint: ExperimentBlueprint
    ) -> ExperimentBlueprint:
        self.load_study(blueprint.study_id)
        path = (
            self._study_dir(blueprint.study_id)
            / "stage3"
            / "specifications"
            / f"{blueprint.blueprint_id}.json"
        )
        if path.is_file():
            existing = ExperimentBlueprint.model_validate(read_json(path))
            stable_existing = existing.model_dump(
                mode="json", exclude={"created_at"}
            )
            stable_new = blueprint.model_dump(
                mode="json", exclude={"created_at"}
            )
            if stable_existing != stable_new:
                raise ValueError("Experiment Blueprint is immutable")
            return existing
        write_json_atomic(path, blueprint)
        self._event(
            blueprint.study_id,
            "experiment_blueprint_saved",
            blueprint_id=blueprint.blueprint_id,
        )
        return blueprint

    def load_experiment_blueprint(
        self, study_id: str, blueprint_id: str
    ) -> ExperimentBlueprint:
        return ExperimentBlueprint.model_validate(
            read_json(
                self._study_dir(study_id)
                / "stage3"
                / "specifications"
                / f"{blueprint_id}.json"
            )
        )

    def save_mvp_feasibility_receipt(
        self, receipt: MVPFeasibilityReceipt
    ) -> MVPFeasibilityReceipt:
        self.load_study(receipt.study_id)
        path = (
            self._study_dir(receipt.study_id)
            / "stage3"
            / "specifications"
            / f"{receipt.receipt_id}.json"
        )
        if path.is_file():
            existing = MVPFeasibilityReceipt.model_validate(read_json(path))
            stable_existing = existing.model_dump(
                mode="json", exclude={"created_at"}
            )
            stable_new = receipt.model_dump(
                mode="json", exclude={"created_at"}
            )
            if stable_existing != stable_new:
                raise ValueError("MVP Feasibility Receipt is immutable")
            return existing
        write_json_atomic(path, receipt)
        self._event(
            receipt.study_id,
            "mvp_feasibility_receipt_saved",
            receipt_id=receipt.receipt_id,
        )
        return receipt

    def load_mvp_feasibility_receipt(
        self, study_id: str, receipt_id: str
    ) -> MVPFeasibilityReceipt:
        return MVPFeasibilityReceipt.model_validate(
            read_json(
                self._study_dir(study_id)
                / "stage3"
                / "specifications"
                / f"{receipt_id}.json"
            )
        )

    def save_scientific_specification_seal(
        self, seal: ScientificSpecificationSeal
    ) -> ScientificSpecificationSeal:
        self.load_study(seal.study_id)
        path = (
            self._study_dir(seal.study_id)
            / "stage3"
            / "specifications"
            / f"{seal.seal_id}.json"
        )
        if path.is_file():
            existing = ScientificSpecificationSeal.model_validate(
                read_json(path)
            )
            stable_existing = existing.model_dump(
                mode="json", exclude={"frozen_at"}
            )
            stable_new = seal.model_dump(
                mode="json", exclude={"frozen_at"}
            )
            if stable_existing != stable_new:
                raise ValueError("Scientific Specification Seal is immutable")
            return existing
        write_json_atomic(path, seal)
        self._event(
            seal.study_id,
            "scientific_specification_sealed",
            seal_id=seal.seal_id,
        )
        return seal

    def load_scientific_specification_seal(
        self, study_id: str, seal_id: str
    ) -> ScientificSpecificationSeal:
        return ScientificSpecificationSeal.model_validate(
            read_json(
                self._study_dir(study_id)
                / "stage3"
                / "specifications"
                / f"{seal_id}.json"
            )
        )

    def save_experiment_build_plan(
        self, plan: ExperimentBuildPlan
    ) -> ExperimentBuildPlan:
        self.load_study(plan.study_id)
        path = (
            self._study_dir(plan.study_id)
            / "stage3"
            / "build_plans"
            / f"{plan.build_plan_id}.json"
        )
        if path.is_file():
            existing = ExperimentBuildPlan.model_validate(read_json(path))
            stable_existing = existing.model_dump(
                mode="json", exclude={"created_at"}
            )
            stable_new = plan.model_dump(
                mode="json", exclude={"created_at"}
            )
            if stable_existing != stable_new:
                raise ValueError("Experiment Build Plan is immutable")
            return existing
        write_json_atomic(path, plan)
        self._event(
            plan.study_id,
            "experiment_build_plan_saved",
            build_plan_id=plan.build_plan_id,
        )
        return plan

    def load_experiment_build_plan(
        self, study_id: str, build_plan_id: str
    ) -> ExperimentBuildPlan:
        return ExperimentBuildPlan.model_validate(
            read_json(
                self._study_dir(study_id)
                / "stage3"
                / "build_plans"
                / f"{build_plan_id}.json"
            )
        )

    def list_experiment_build_plans(
        self, study_id: str
    ) -> list[ExperimentBuildPlan]:
        self.load_study(study_id)
        return [
            ExperimentBuildPlan.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id)
                    / "stage3"
                    / "build_plans"
                ).glob("build-plan-*.json")
            )
        ]

    def save_execution_package_seal(
        self, seal: ExecutionPackageSeal
    ) -> ExecutionPackageSeal:
        self.load_study(seal.study_id)
        path = (
            self._study_dir(seal.study_id)
            / "stage3"
            / "execution_packages"
            / f"{seal.seal_id}.json"
        )
        if path.is_file() and read_json(path) != seal.model_dump(mode="json"):
            raise ValueError("Execution Package Seal is immutable")
        write_json_atomic(path, seal)
        self._event(
            seal.study_id,
            "execution_package_sealed",
            seal_id=seal.seal_id,
        )
        return seal

    def load_execution_package_seal(
        self, study_id: str, seal_id: str
    ) -> ExecutionPackageSeal:
        return ExecutionPackageSeal.model_validate(
            read_json(
                self._study_dir(study_id)
                / "stage3"
                / "execution_packages"
                / f"{seal_id}.json"
            )
        )

    def save_stage3_handoff(
        self, handoff: Stage3HandoffPackage
    ) -> Stage3HandoffPackage:
        self.load_study(handoff.study_id)
        root = self._study_dir(handoff.study_id) / "stage3"
        path = root / "handoffs" / f"{handoff.handoff_id}.json"
        current_path = root / "handoff.json"
        if path.is_file():
            existing = Stage3HandoffPackage.model_validate(read_json(path))
            stable_existing = existing.model_dump(
                mode="json", exclude={"admitted_at"}
            )
            stable_new = handoff.model_dump(
                mode="json", exclude={"admitted_at"}
            )
            if stable_existing != stable_new:
                raise ValueError(
                    "Stage 3 handoff is immutable; create a successor"
                )
            return existing
        if current_path.is_file():
            current = Stage3HandoffPackage.model_validate(
                read_json(current_path)
            )
            if handoff.predecessor_handoff_id != current.handoff_id:
                raise ValueError(
                    "a new Stage 3 handoff must link its predecessor"
                )
        write_json_atomic(path, handoff)
        write_json_atomic(current_path, handoff)
        self._event(
            handoff.study_id,
            "stage3_handoff_admitted",
            handoff_id=handoff.handoff_id,
        )
        return handoff

    def load_stage3_handoff(self, study_id: str) -> Stage3HandoffPackage:
        return Stage3HandoffPackage.model_validate(
            read_json(self._study_dir(study_id) / "stage3" / "handoff.json")
        )

    def save_run_plan(self, plan: RunPlan) -> RunPlan:
        self.load_study(plan.study_id)
        path = (
            self._study_dir(plan.study_id)
            / "stage3"
            / "run_plans"
            / f"{plan.plan_id}.json"
        )
        if path.is_file():
            existing = RunPlan.model_validate(read_json(path))
            stable_existing = existing.model_dump(
                mode="json", exclude={"created_at"}
            )
            stable_new = plan.model_dump(mode="json", exclude={"created_at"})
            if stable_existing != stable_new:
                raise ValueError("Run Plan is immutable")
            return existing
        write_json_atomic(path, plan)
        self._event(
            plan.study_id,
            "stage3_run_plan_saved",
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
        )
        return plan

    def load_run_plan(self, study_id: str, plan_id: str) -> RunPlan:
        return RunPlan.model_validate(
            read_json(
                self._study_dir(study_id)
                / "stage3"
                / "run_plans"
                / f"{plan_id}.json"
            )
        )

    def list_run_plans(self, study_id: str) -> list[RunPlan]:
        self.load_study(study_id)
        return sorted([
            RunPlan.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id) / "stage3" / "run_plans"
                ).glob("run-plan-*.json")
            )
        ], key=lambda item: item.created_at)

    def save_execution_attempt(
        self, attempt: ExecutionAttempt
    ) -> ExecutionAttempt:
        self.load_study(attempt.study_id)
        path = (
            self._study_dir(attempt.study_id)
            / "stage3"
            / "attempts"
            / f"{attempt.attempt_id}.json"
        )
        if path.is_file():
            existing = ExecutionAttempt.model_validate(read_json(path))
            if existing.model_dump(mode="json") != attempt.model_dump(
                mode="json"
            ):
                raise ValueError("execution attempts are append-only")
            return existing
        write_json_atomic(path, attempt)
        self._event(
            attempt.study_id,
            "stage3_execution_attempt_saved",
            attempt_id=attempt.attempt_id,
            run_cell_id=attempt.run_cell_id,
        )
        return attempt

    def list_execution_attempts(
        self, study_id: str, run_cell_id: str | None = None
    ) -> list[ExecutionAttempt]:
        self.load_study(study_id)
        attempts = [
            ExecutionAttempt.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id) / "stage3" / "attempts"
                ).glob("attempt-*.json")
            )
        ]
        if run_cell_id is not None:
            attempts = [
                item for item in attempts
                if item.run_cell_id == run_cell_id
            ]
        return attempts

    def save_result_envelope(
        self, result: ResultEnvelope
    ) -> ResultEnvelope:
        self.load_study(result.study_id)
        existing_for_cell = [
            item
            for item in self.list_result_envelopes(result.study_id)
            if item.run_cell_id == result.run_cell_id
        ]
        if existing_for_cell and all(
            item.result_id != result.result_id for item in existing_for_cell
        ):
            raise ValueError(
                "a RunCell already has its canonical result; create a "
                "successor plan instead of selecting a later attempt"
            )
        path = (
            self._study_dir(result.study_id)
            / "stage3"
            / "results"
            / f"{result.result_id}.json"
        )
        if path.is_file() and read_json(path) != result.model_dump(mode="json"):
            raise ValueError("result envelopes are immutable")
        write_json_atomic(path, result)
        return result

    def list_result_envelopes(self, study_id: str) -> list[ResultEnvelope]:
        self.load_study(study_id)
        return [
            ResultEnvelope.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id) / "stage3" / "results"
                ).glob("result-*.json")
            )
        ]

    def save_evaluation_record(
        self, evaluation: EvaluationRecord
    ) -> EvaluationRecord:
        self.load_study(evaluation.study_id)
        path = (
            self._study_dir(evaluation.study_id)
            / "stage3"
            / "evaluations"
            / f"{evaluation.evaluation_id}.json"
        )
        if (
            path.is_file()
            and read_json(path) != evaluation.model_dump(mode="json")
        ):
            raise ValueError("evaluation records are immutable")
        write_json_atomic(path, evaluation)
        return evaluation

    def list_evaluation_records(
        self, study_id: str
    ) -> list[EvaluationRecord]:
        self.load_study(study_id)
        return sorted([
            EvaluationRecord.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id) / "stage3" / "evaluations"
                ).glob("evaluation-*.json")
            )
        ], key=lambda item: item.created_at)

    def save_evaluator_disagreement_report(self, report: Any) -> Any:
        from .evaluator_comparison import EvaluatorDisagreementReport

        validated = EvaluatorDisagreementReport.model_validate(report)
        self.load_study(validated.study_id)
        path = (
            self._study_dir(validated.study_id)
            / "stage3"
            / "evaluator_disagreements"
            / f"{validated.report_id}.json"
        )
        if path.is_file() and read_json(path) != validated.model_dump(mode="json"):
            raise ValueError("evaluator disagreement reports are append-only")
        write_json_atomic(path, validated)
        self._event(
            validated.study_id,
            "evaluator_disagreement_report_saved",
            report_id=validated.report_id,
            status=validated.status.value,
            requires_adjudication=validated.requires_adjudication,
        )
        return validated

    def list_evaluator_disagreement_reports(self, study_id: str) -> list[Any]:
        from .evaluator_comparison import EvaluatorDisagreementReport

        self.load_study(study_id)
        return [
            EvaluatorDisagreementReport.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id)
                    / "stage3"
                    / "evaluator_disagreements"
                ).glob("evaluator-disagreement-*.json")
            )
        ]

    def save_formal_exposure(
        self, exposure: FormalEvaluationExposureRecord
    ) -> FormalEvaluationExposureRecord:
        self.load_study(exposure.study_id)
        path = (
            self._study_dir(exposure.study_id)
            / "stage3"
            / "exposures"
            / f"{exposure.exposure_id}.json"
        )
        if path.is_file() and read_json(path) != exposure.model_dump(
            mode="json"
        ):
            raise ValueError("formal exposure records are append-only")
        write_json_atomic(path, exposure)
        self._event(
            exposure.study_id,
            "formal_evaluation_exposed",
            exposure_id=exposure.exposure_id,
            target_hash=exposure.target_hash,
            confirmatory_status=exposure.confirmatory_status.value,
        )
        return exposure

    def list_formal_exposures(
        self,
        study_id: str | None = None,
        *,
        target_hash: str | None = None,
    ) -> list[FormalEvaluationExposureRecord]:
        roots = (
            [self._study_dir(study_id)]
            if study_id is not None
            else [
                path for path in (self.root / "studies").glob("*")
                if path.is_dir()
            ]
        )
        records = [
            FormalEvaluationExposureRecord.model_validate(read_json(path))
            for root in roots
            for path in sorted(
                (root / "stage3" / "exposures").glob(
                    "formal-exposure-*.json"
                )
            )
        ]
        if target_hash is not None:
            records = [
                item for item in records
                if item.target_hash == target_hash
            ]
        return sorted(records, key=lambda item: item.first_exposed_at)

    def save_statistical_assurance_report(
        self, report: StatisticalAssuranceReport
    ) -> StatisticalAssuranceReport:
        self.load_study(report.study_id)
        path = (
            self._study_dir(report.study_id)
            / "stage3"
            / "assurance"
            / f"{report.report_id}.json"
        )
        if path.is_file() and read_json(path) != report.model_dump(mode="json"):
            raise ValueError("statistical assurance reports are immutable")
        write_json_atomic(path, report)
        return report

    def list_statistical_assurance_reports(
        self, study_id: str
    ) -> list[StatisticalAssuranceReport]:
        self.load_study(study_id)
        return [
            StatisticalAssuranceReport.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id) / "stage3" / "assurance"
                ).glob("stat-assurance-*.json")
            )
        ]

    def save_leakage_audit_report(
        self, report: LeakageAuditReport
    ) -> LeakageAuditReport:
        self.load_study(report.study_id)
        path = (
            self._study_dir(report.study_id)
            / "stage3"
            / "leakage_audits"
            / f"{report.report_id}.json"
        )
        if path.is_file() and read_json(path) != report.model_dump(mode="json"):
            raise ValueError("leakage audit reports are immutable")
        write_json_atomic(path, report)
        return report

    def list_leakage_audit_reports(
        self, study_id: str
    ) -> list[LeakageAuditReport]:
        self.load_study(study_id)
        return [
            LeakageAuditReport.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id)
                    / "stage3"
                    / "leakage_audits"
                ).glob("leakage-audit-*.json")
            )
        ]

    def save_claim_envelope(
        self, envelope: ScientificClaimEnvelope
    ) -> ScientificClaimEnvelope:
        self.load_study(envelope.study_id)
        path = (
            self._study_dir(envelope.study_id)
            / "stage3"
            / "claim_envelopes"
            / f"{envelope.claim_envelope_id}.json"
        )
        if path.is_file() and read_json(path) != envelope.model_dump(
            mode="json"
        ):
            raise ValueError("scientific claim envelopes are immutable")
        write_json_atomic(path, envelope)
        return envelope

    def list_claim_envelopes(
        self, study_id: str
    ) -> list[ScientificClaimEnvelope]:
        self.load_study(study_id)
        return [
            ScientificClaimEnvelope.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id)
                    / "stage3"
                    / "claim_envelopes"
                ).glob("claim-envelope-*.json")
            )
        ]

    def save_human_intervention(
        self, intervention: HumanInterventionRecord
    ) -> HumanInterventionRecord:
        self.load_study(intervention.study_id)
        path = (
            self._study_dir(intervention.study_id)
            / "stage3"
            / "human_interventions"
            / f"{intervention.intervention_id}.json"
        )
        if path.is_file() and read_json(path) != intervention.model_dump(
            mode="json"
        ):
            raise ValueError("human intervention records are append-only")
        write_json_atomic(path, intervention)
        return intervention

    def record_component_defect(
        self, notice: ComponentDefectNotice
    ) -> GlobalImpactAnalysis:
        """Record a shared defect and append under-review overlays per Study."""

        notice_path = (
            self.root
            / "component_defects"
            / f"{notice.notice_id}.json"
        )
        if notice_path.is_file() and read_json(
            notice_path
        ) != notice.model_dump(mode="json"):
            raise ValueError("component defect notices are immutable")
        write_json_atomic(notice_path, notice)
        artifacts_by_study: dict[str, list[str]] = {}
        verdicts_by_study: dict[str, list[str]] = {}
        for study in self.list_studies():
            artifacts = self.list_artifacts(study.study_id)
            direct = {
                item.artifact_id
                for item in artifacts
                if item.sha256 == notice.component_digest
            }
            if not direct:
                continue
            impacted = affected_artifacts(
                direct, self.list_dependencies(study.study_id)
            )
            artifacts_by_study[study.study_id] = sorted(impacted)
            verdict_ids = sorted(
                str(payload["verdict_id"])
                for path in (
                    self._study_dir(study.study_id) / "verdicts"
                ).glob("*.json")
                for payload in [read_json(path)]
                if payload.get("verdict_id")
            )
            verdicts_by_study[study.study_id] = verdict_ids
            erratum = ResearchErratum(
                erratum_id=stable_id(
                    "research-erratum",
                    study.study_id,
                    notice.notice_id,
                ),
                study_id=study.study_id,
                notice_id=notice.notice_id,
                affected_artifact_ids=sorted(impacted),
                affected_verdict_ids=verdict_ids,
            )
            write_json_atomic(
                self._study_dir(study.study_id)
                / "errata"
                / f"{erratum.erratum_id}.json",
                erratum,
            )
            self._event(
                study.study_id,
                "component_defect_under_review",
                notice_id=notice.notice_id,
                erratum_id=erratum.erratum_id,
            )
        analysis = GlobalImpactAnalysis(
            analysis_id=stable_id(
                "global-impact",
                notice.notice_id,
                notice.component_digest,
            ),
            notice_id=notice.notice_id,
            affected_study_ids=sorted(artifacts_by_study),
            affected_artifact_ids_by_study=artifacts_by_study,
            affected_verdict_ids_by_study=verdicts_by_study,
        )
        write_json_atomic(
            self.root
            / "component_defects"
            / f"{analysis.analysis_id}.json",
            analysis,
        )
        return analysis

    def save_evidence_edge(self, edge: EvidenceEdge) -> EvidenceEdge:
        self.load_study(edge.study_id)
        path = (
            self._study_dir(edge.study_id)
            / "stage3"
            / "evidence_edges"
            / f"{edge.edge_id}.json"
        )
        if path.is_file() and read_json(path) != edge.model_dump(mode="json"):
            raise ValueError("evidence edges are immutable")
        write_json_atomic(path, edge)
        return edge

    def list_evidence_edges(self, study_id: str) -> list[EvidenceEdge]:
        self.load_study(study_id)
        return [
            EvidenceEdge.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id) / "stage3" / "evidence_edges"
                ).glob("evidence-edge-*.json")
            )
        ]

    def save_stage3_diagnostic(
        self, diagnostic: DiagnosticReport
    ) -> DiagnosticReport:
        self.load_study(diagnostic.study_id)
        path = (
            self._study_dir(diagnostic.study_id)
            / "stage3"
            / "diagnostics"
            / f"{diagnostic.diagnostic_id}.json"
        )
        if (
            path.is_file()
            and read_json(path) != diagnostic.model_dump(mode="json")
        ):
            raise ValueError("Stage 3 diagnostics are append-only")
        write_json_atomic(path, diagnostic)
        return diagnostic

    def list_stage3_diagnostics(
        self, study_id: str
    ) -> list[DiagnosticReport]:
        self.load_study(study_id)
        return [
            DiagnosticReport.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id)
                    / "stage3"
                    / "diagnostics"
                ).glob("stage3-diagnostic-*.json")
            )
        ]

    def save_successor_run(self, successor: SuccessorRun) -> SuccessorRun:
        self.load_study(successor.study_id)
        path = (
            self._study_dir(successor.study_id)
            / "stage3"
            / "successors"
            / f"{successor.successor_id}.json"
        )
        if path.is_file() and read_json(path) != successor.model_dump(
            mode="json"
        ):
            raise ValueError("successor lineage is append-only")
        write_json_atomic(path, successor)
        return successor

    def list_successor_runs(self, study_id: str) -> list[SuccessorRun]:
        self.load_study(study_id)
        return [
            SuccessorRun.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id) / "stage3" / "successors"
                ).glob("successor-*.json")
            )
        ]

    def save_scientific_successor_request(
        self, request: ScientificSuccessorRequest
    ) -> ScientificSuccessorRequest:
        self.load_study(request.study_id)
        path = (
            self._study_dir(request.study_id)
            / "stage3"
            / "scientific_successors"
            / f"{request.request_id}.json"
        )
        if path.is_file() and read_json(path) != request.model_dump(
            mode="json"
        ):
            raise ValueError("scientific successor requests are append-only")
        write_json_atomic(path, request)
        self._event(
            request.study_id,
            "scientific_successor_requested",
            request_id=request.request_id,
        )
        return request

    def list_scientific_successor_requests(
        self, study_id: str
    ) -> list[ScientificSuccessorRequest]:
        self.load_study(study_id)
        return [
            ScientificSuccessorRequest.model_validate(read_json(path))
            for path in sorted(
                (
                    self._study_dir(study_id)
                    / "stage3"
                    / "scientific_successors"
                ).glob("scientific-successor-*.json")
            )
        ]

    def save_stage3_completion(
        self, package: Stage3CompletionPackage
    ) -> Stage3CompletionPackage:
        self.load_study(package.study_id)
        path = (
            self._study_dir(package.study_id)
            / "stage3"
            / "completion"
            / f"{package.completion_id}.json"
        )
        if path.is_file() and read_json(path) != package.model_dump(
            mode="json"
        ):
            raise ValueError("Stage 3 completion packages are immutable")
        write_json_atomic(path, package)
        self._event(
            package.study_id,
            "stage3_completion_saved",
            completion_id=package.completion_id,
        )
        return package

    def list_stage3_completions(
        self, study_id: str
    ) -> list[Stage3CompletionPackage]:
        """Return immutable completion packages in creation order.

        Completion identifiers are content-derived, so filename order is not
        chronological and must not determine the current claim authority.
        """

        self.load_study(study_id)
        packages = [
            Stage3CompletionPackage.model_validate(read_json(path))
            for path in (
                self._study_dir(study_id) / "stage3" / "completion"
            ).glob("stage3-completion-*.json")
        ]
        return sorted(
            packages,
            key=lambda item: (item.created_at, item.completion_id),
        )

    def save_literature_set(
        self, literature: LiteratureSetVersion
    ) -> LiteratureSetVersion:
        self.load_study(literature.study_id)
        path = (
            self._study_dir(literature.study_id)
            / "literature"
            / f"{literature.literature_set_id}-v{literature.version}.json"
        )
        if path.is_file() and read_json(path) != literature.model_dump(mode="json"):
            raise ValueError("frozen literature versions cannot be edited in place")
        write_json_atomic(path, literature)
        self._event(
            literature.study_id,
            "literature_set_saved",
            literature_set_id=literature.literature_set_id,
            version=literature.version,
        )
        return literature

    def list_literature_sets(
        self, study_id: str
    ) -> list[LiteratureSetVersion]:
        self.load_study(study_id)
        return [
            LiteratureSetVersion.model_validate(read_json(path))
            for path in sorted(
                (self._study_dir(study_id) / "literature").glob(
                    "literature-*-v*.json"
                )
            )
        ]

    def save_evidence_chain(self, chain: EvidenceChain) -> EvidenceChain:
        self.load_study(chain.study_id)
        write_json_atomic(
            self._study_dir(chain.study_id)
            / "evidence_chains"
            / f"{chain.chain_id}.json",
            chain,
        )
        return chain

    def list_evidence_chains(self, study_id: str) -> list[EvidenceChain]:
        return [
            EvidenceChain.model_validate(read_json(path))
            for path in sorted(
                (self._study_dir(study_id) / "evidence_chains").glob("chain-*.json")
            )
        ]

    def save_hypothesis_verdict(self, verdict: HypothesisVerdict) -> HypothesisVerdict:
        self.load_study(verdict.study_id)
        write_json_atomic(
            self._study_dir(verdict.study_id)
            / "verdicts"
            / f"{verdict.verdict_id}.json",
            verdict,
        )
        return verdict

    def save_study_verdict(self, verdict: StudyVerdict) -> StudyVerdict:
        study = self.load_study(verdict.study_id)
        write_json_atomic(
            self._study_dir(verdict.study_id)
            / "verdicts"
            / f"{verdict.verdict_id}.json",
            verdict,
        )
        self.save_study(
            study.model_copy(update={"latest_study_verdict_id": verdict.verdict_id}),
            "study_verdict_saved",
        )
        return verdict

    def save_adjudication(self, adjudication: AdjudicationRecord) -> AdjudicationRecord:
        self.load_study(adjudication.study_id)
        write_json_atomic(
            self._study_dir(adjudication.study_id)
            / "adjudications"
            / f"{adjudication.adjudication_id}.json",
            adjudication,
        )
        self._event(
            adjudication.study_id,
            "adjudication_appended",
            adjudication_id=adjudication.adjudication_id,
        )
        return adjudication

    def save_nli_alert(self, alert: NLIRiskAlert) -> NLIRiskAlert:
        self.load_study(alert.study_id)
        write_json_atomic(
            self._study_dir(alert.study_id) / "nli_alerts" / f"{alert.alert_id}.json",
            alert,
        )
        self._event(
            alert.study_id,
            "nli_risk_alert",
            alert_id=alert.alert_id,
            authority=alert.authority,
        )
        return alert

    def save_repair_contract(self, repair: RepairContract) -> RepairContract:
        self.load_study(repair.study_id)
        repair_path = (
            self._study_dir(repair.study_id)
            / "repairs"
            / f"{repair.repair_id}.json"
        )
        if repair_path.is_file():
            previous = RepairContract.model_validate(read_json(repair_path))
            if previous.model_dump(mode="json") != repair.model_dump(mode="json"):
                history_dir = repair_path.parent / "history"
                revision = (
                    len(list(history_dir.glob(f"{repair.repair_id}-r*.json"))) + 1
                )
                write_json_atomic(
                    history_dir / f"{repair.repair_id}-r{revision}.json",
                    previous,
                )
        write_json_atomic(repair_path, repair)
        study = self.load_study(repair.study_id)
        self.save_study(
            study.model_copy(update={"repair_status": repair.status}),
            "repair_contract_saved",
        )
        return repair

    def load_repair_contract(
        self, study_id: str, repair_id: str
    ) -> RepairContract:
        return RepairContract.model_validate(
            read_json(
                self._study_dir(study_id)
                / "repairs"
                / f"{repair_id}.json"
            )
        )

    def list_repair_contracts(self, study_id: str) -> list[RepairContract]:
        return [
            RepairContract.model_validate(read_json(path))
            for path in sorted(
                (self._study_dir(study_id) / "repairs").glob("repair-*.json")
            )
        ]

    def save_diagnostic(self, diagnostic: WorkflowDiagnostic) -> WorkflowDiagnostic:
        self.load_study(diagnostic.study_id)
        known = {item.artifact_id for item in self.list_artifacts(diagnostic.study_id)}
        unknown = sorted(set(diagnostic.evidence_artifact_ids).difference(known))
        if unknown:
            raise ValueError(
                "diagnostic references unknown artifacts: " + ", ".join(unknown)
            )
        path = (
            self._study_dir(diagnostic.study_id)
            / "diagnostics"
            / f"{diagnostic.diagnostic_id}.json"
        )
        if path.is_file():
            existing = WorkflowDiagnostic.model_validate(read_json(path))
            if existing.model_dump(mode="json") != diagnostic.model_dump(mode="json"):
                raise ValueError("diagnostics are append-only")
            return existing
        write_json_atomic(path, diagnostic)
        self._event(
            diagnostic.study_id,
            "diagnostic_appended",
            diagnostic_id=diagnostic.diagnostic_id,
            failure_code=diagnostic.failure_code,
            earliest_affected_step_type=diagnostic.earliest_affected_step_type,
        )
        return diagnostic

    def list_diagnostics(self, study_id: str) -> list[WorkflowDiagnostic]:
        self.load_study(study_id)
        return [
            WorkflowDiagnostic.model_validate(read_json(path))
            for path in sorted(
                (self._study_dir(study_id) / "diagnostics").glob(
                    "diagnostic-*.json"
                )
            )
        ]

    def propose_repair(
        self,
        study_id: str,
        *,
        diagnostic_owner: DiagnosticOwner,
        scientific_change: bool,
        earliest_affected_phase: Phase,
        changed_artifact_ids: list[str],
        regression_checks: list[dict[str, Any]],
        predecessor_run_id: str | None = None,
        diagnostic_id: str | None = None,
        earliest_affected_step_type: str | None = None,
    ) -> RepairContract:
        impact = self.impact(study_id, changed_artifact_ids)
        existing = sorted((self._study_dir(study_id) / "repairs").glob("repair-*.json"))
        version = len(existing) + 1
        repair = RepairContract(
            repair_id=stable_id("repair", study_id, version, *changed_artifact_ids),
            study_id=study_id,
            version=version,
            diagnostic_owner=diagnostic_owner,
            status=(
                RepairStatus.AWAITING_APPROVAL
                if scientific_change
                else RepairStatus.REPAIR_PROPOSED
            ),
            scientific_change=scientific_change,
            earliest_affected_phase=earliest_affected_phase,
            changed_artifact_ids=impact["changed_artifact_ids"],
            invalidated_artifact_ids=impact["invalidated_artifact_ids"],
            reusable_artifact_ids=impact["reusable_artifact_ids"],
            regression_checks=regression_checks,
            diagnostic_id=diagnostic_id,
            earliest_affected_step_type=earliest_affected_step_type,
            predecessor_run_id=predecessor_run_id,
            predecessor_study_id=study_id,
        )
        self.save_repair_contract(repair)
        if scientific_change:
            self.create_gate(
                study_id,
                GateType.REPAIR_OR_HIGH_COST_RUN,
                subject_type="repair_contract",
                subject_id=repair.repair_id,
                subject_version=repair.version,
            )
        return repair

    def save_completion_record(self, record: CompletionRecord) -> CompletionRecord:
        self.load_study(record.study_id)
        record = seal_completion_record(record)
        write_json_atomic(
            self._study_dir(record.study_id) / "completion_record.json", record
        )
        self._event(
            record.study_id,
            "completion_record_written",
            completion_record_id=record.completion_record_id,
        )
        return record

    def register_artifact(
        self,
        study_id: str,
        path: str,
        sha256: str,
        *,
        kind: str,
        role: ArtifactRole = ArtifactRole.OTHER,
        status: ArtifactStatus = ArtifactStatus.FROZEN,
        version: int = 1,
        predecessor_artifact_id: str | None = None,
    ) -> ArtifactRecord:
        artifact = ArtifactRecord(
            artifact_id=stable_id("artifact", study_id, path, sha256, version),
            study_id=study_id,
            kind=kind,
            path=path,
            sha256=sha256,
            status=status,
            version=version,
            role=role,
            predecessor_artifact_id=predecessor_artifact_id,
        )
        write_json_atomic(
            self._study_dir(study_id) / "artifacts" / f"{artifact.artifact_id}.json",
            artifact,
        )
        return artifact

    def list_artifacts(self, study_id: str) -> list[ArtifactRecord]:
        return [
            ArtifactRecord.model_validate(read_json(path))
            for path in sorted(
                (self._study_dir(study_id) / "artifacts").glob("artifact-*.json")
            )
        ]

    def add_dependency(
        self,
        study_id: str,
        input_artifact_id: str,
        output_artifact_id: str,
        *,
        relation: str = "derived_from",
    ) -> ArtifactDependency:
        known = {item.artifact_id for item in self.list_artifacts(study_id)}
        if input_artifact_id not in known or output_artifact_id not in known:
            raise ValueError("artifact dependency references an unknown artifact")
        edge = ArtifactDependency(
            dependency_id=stable_id(
                "dependency",
                study_id,
                input_artifact_id,
                output_artifact_id,
                relation,
            ),
            study_id=study_id,
            input_artifact_id=input_artifact_id,
            output_artifact_id=output_artifact_id,
            relation=relation,
        )
        write_json_atomic(
            self._study_dir(study_id) / "dependencies" / f"{edge.dependency_id}.json",
            edge,
        )
        return edge

    def list_dependencies(self, study_id: str) -> list[ArtifactDependency]:
        return [
            ArtifactDependency.model_validate(read_json(path))
            for path in sorted(
                (self._study_dir(study_id) / "dependencies").glob("dependency-*.json")
            )
        ]

    def impact(
        self, study_id: str, changed_artifact_ids: list[str]
    ) -> dict[str, list[str]]:
        all_artifacts = {item.artifact_id for item in self.list_artifacts(study_id)}
        unknown = sorted(set(changed_artifact_ids).difference(all_artifacts))
        if unknown:
            raise ValueError("unknown changed artifacts: " + ", ".join(unknown))
        invalidated = affected_artifacts(
            changed_artifact_ids, self.list_dependencies(study_id)
        )
        return {
            "changed_artifact_ids": sorted(set(changed_artifact_ids)),
            "invalidated_artifact_ids": sorted(invalidated),
            "reusable_artifact_ids": sorted(all_artifacts.difference(invalidated)),
        }

    def save_readiness(self, assessment: ReadinessAssessment) -> ReadinessAssessment:
        assessment = ReadinessAssessment.model_validate(
            assessment.model_dump(mode="json")
        )
        self.load_study(assessment.study_id)
        write_json_atomic(
            self._study_dir(assessment.study_id) / "readiness.json", assessment
        )
        completion_path = (
            self._study_dir(assessment.study_id) / "completion_record.json"
        )
        if completion_path.is_file():
            completion = CompletionRecord.model_validate(read_json(completion_path))
            write_json_atomic(
                completion_path,
                seal_completion_record(
                    CompletionRecord.model_validate(
                        {
                            **completion.model_dump(mode="json"),
                            "readiness": assessment.model_dump(mode="json"),
                        }
                    )
                ),
            )
        self._event(
            assessment.study_id,
            "publication_readiness_assessed",
            publication_ready=assessment.publication_ready,
        )
        return assessment

    def load_readiness(self, study_id: str) -> ReadinessAssessment:
        return ReadinessAssessment.model_validate(
            read_json(self._study_dir(study_id) / "readiness.json")
        )

    def submit_ai_review(
        self, study_id: str, status: AIReviewStatus
    ) -> ReadinessAssessment:
        assessment = self.load_readiness(study_id)
        return self.save_readiness(
            assessment.model_copy(
                update={"ai_scientific_review": status, "assessed_at": utc_now()}
            )
        )

    def submit_author_approval(
        self,
        study_id: str,
        status: AuthorApprovalStatus,
        *,
        decided_by: str,
        reason: str | None = None,
    ) -> tuple[PublicationApproval, ReadinessAssessment]:
        approval = PublicationApproval(
            approval_id=stable_id(
                "publication-approval", study_id, status.value, utc_now()
            ),
            study_id=study_id,
            status=status,
            decided_by=decided_by,
            reason=reason,
        )
        write_json_atomic(
            self._study_dir(study_id)
            / "publication_approvals"
            / f"{approval.approval_id}.json",
            approval,
        )
        assessment = self.load_readiness(study_id)
        updated = self.save_readiness(
            assessment.model_copy(
                update={"author_publication_approval": status, "assessed_at": utc_now()}
            )
        )
        return approval, updated

    def snapshot(self, study_id: str) -> dict[str, Any]:
        study = self.load_study(study_id)
        steps = self.list_steps(study_id)
        groups: dict[str, dict[str, int]] = {}
        for item in steps:
            if not item.task_group:
                continue
            group = groups.setdefault(item.task_group, {"completed": 0, "total": 0})
            group["total"] += 1
            group["completed"] += int(item.status is ExecutionStatus.SUCCEEDED)
        return {
            "study": {
                **study.model_dump(mode="json"),
                "legacy_stage": study.legacy_stage(),
            },
            "steps": [item.model_dump(mode="json") for item in steps],
            "gates": [
                item.model_dump(mode="json") for item in self.list_gates(study_id)
            ],
            "artifacts": [
                item.model_dump(mode="json") for item in self.list_artifacts(study_id)
            ],
            "dependencies": [
                item.model_dump(mode="json")
                for item in self.list_dependencies(study_id)
            ],
            "evidence_chains": [
                item.model_dump(mode="json")
                for item in self.list_evidence_chains(study_id)
            ],
            "diagnostics": [
                item.model_dump(mode="json")
                for item in self.list_diagnostics(study_id)
            ],
            "repairs": [
                item.model_dump(mode="json")
                for item in self.list_repair_contracts(study_id)
            ],
            "task_groups": groups,
            "readiness": (
                self.load_readiness(study_id).model_dump(mode="json")
                if (self._study_dir(study_id) / "readiness.json").is_file()
                else None
            ),
        }


def verify_completion_record(
    record_path: str | Path, *, artifact_root: str | Path | None = None
) -> dict[str, Any]:
    path = Path(record_path).resolve()
    try:
        record = CompletionRecord.model_validate(read_json(path))
    except Exception as exc:
        return {"passed": False, "publication_ready": False, "violations": [str(exc)]}
    root = Path(artifact_root).resolve() if artifact_root else path.parent
    violations: list[str] = []
    expected_record_sha = seal_completion_record(record).record_sha256
    if record.record_sha256 != expected_record_sha:
        violations.append("completion record integrity digest mismatch")
    for relative, expected in record.artifact_hashes.items():
        artifact = (root / relative).resolve()
        if artifact != root and root not in artifact.parents:
            violations.append(f"artifact escapes completion root: {relative}")
        elif not artifact.is_file():
            violations.append(f"completed artifact is missing: {relative}")
        elif sha256_file(artifact) != expected:
            violations.append(f"completed artifact changed: {relative}")
    return {
        "passed": not violations,
        "project_id": record.project_id,
        "study_id": record.study_id,
        "publication_ready": record.publication_ready and not violations,
        "violations": violations,
    }


__all__ = [
    "AIReviewStatus",
    "AnalysisEligibilityStatus",
    "AdjudicationRecord",
    "ArmFairnessContract",
    "ArtifactDependency",
    "ArtifactRecord",
    "ArtifactRole",
    "ArtifactStatus",
    "AuthorApprovalStatus",
    "AssuranceStatus",
    "AttemptSelectionPolicy",
    "BuildAssetStrategy",
    "CompletionRecord",
    "ComponentDefectNotice",
    "DiagnosticOwner",
    "DiagnosticReport",
    "ConfirmatoryStatus",
    "EntryMode",
    "EvidenceChain",
    "EvidenceChainLevel",
    "EvidenceEdge",
    "EvidenceRelation",
    "EvidenceReproductionLevel",
    "EvidenceMaturity",
    "EstimandSpecification",
    "ExecutionPackageSeal",
    "ExecutionTrustLevel",
    "ExperimentBlueprint",
    "ExperimentBuildItem",
    "ExperimentBuildPlan",
    "EvaluationRecord",
    "ExecutionAttempt",
    "ExecutionStatus",
    "ExecutorType",
    "GateRecord",
    "GateStatus",
    "GateType",
    "GlobalImpactAnalysis",
    "FormalEvaluationExposureRecord",
    "FormalExposureType",
    "HumanInterventionRecord",
    "Hypothesis",
    "HypothesisRole",
    "HypothesisVerdict",
    "HypothesisVerdictStatus",
    "IntegrityGateStatus",
    "NetworkPolicy",
    "MVPFeasibilityReceipt",
    "ModelSelectionPlan",
    "Phase",
    "ProtocolStatus",
    "ProjectRecord",
    "ProfileCapabilityStatus",
    "PublicationApproval",
    "PublicationIntegrityGates",
    "PublicationMode",
    "ReadinessAssessment",
    "RepairContract",
    "RepairStatus",
    "ResourceLifecycleStatus",
    "QualificationStatus",
    "LeakageAuditReport",
    "LeakageCheckStatus",
    "ResearchContractVersion",
    "ResearchErratum",
    "ResearchRun",
    "ResearchSupportLevel",
    "ResultEnvelope",
    "RunCell",
    "RunCellStatus",
    "RunPlan",
    "ScopeContractVersion",
    "ScientificSpecificationSeal",
    "ScientificClaimEnvelope",
    "ScientificSuccessorRequest",
    "Stage3BuildMode",
    "Stage3CompletionPackage",
    "Stage3FailureClass",
    "Stage3HandoffPackage",
    "Stage3Profile",
    "StatisticalAssuranceReport",
    "StepInstance",
    "StepAcceptanceStatus",
    "StudyLifecycle",
    "StudyRecord",
    "StudyVerdict",
    "StudyVerdictStatus",
    "SuccessorRun",
    "SystemReadiness",
    "WorkflowRepository",
    "WorkflowDiagnostic",
    "affected_artifacts",
    "aggregate_study_verdict",
    "is_secret_path",
    "research_support_level",
    "stable_id",
    "verify_completion_record",
]
