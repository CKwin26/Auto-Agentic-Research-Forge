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


class GateStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    AWAITING_USER = "awaiting_user"
    APPROVED = "approved"
    REJECTED = "rejected"


class GateType(StrEnum):
    SCOPE_APPROVAL = "scope_approval"
    RESEARCH_CONTRACT = "research_contract"
    REPAIR_OR_HIGH_COST_RUN = "repair_or_high_cost_run"
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
    executor_type: ExecutorType
    depends_on: list[str] = Field(default_factory=list)
    parent_step_id: str | None = None
    task_group: str | None = None
    attempt: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    expected_output: str | None = None
    blocker: dict[str, Any] | None = None
    audit_event_ids: list[str] = Field(default_factory=list)
    started_at: str | None = None
    updated_at: str = Field(default_factory=utc_now)
    completed_at: str | None = None


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
    direction: str
    research_question: str
    scope_in: list[str]
    scope_out: list[str]
    candidate_contribution: str
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


class ResearchContractVersion(StrictModel):
    schema_version: int = 1
    study_id: str
    version: int = Field(ge=1)
    scope_version: int = Field(ge=1)
    status: ArtifactStatus = ArtifactStatus.DRAFT
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
    predecessor_run_id: str | None = None
    repair_contract_id: str | None = None
    reused_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    completed_at: str | None = None


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
    predecessor_run_id: str | None = None
    successor_run_id: str | None = None
    approved_by: str | None = None
    created_at: str = Field(default_factory=utc_now)


class ReadinessAssessment(StrictModel):
    schema_version: int = 1
    study_id: str
    system_publication_readiness: SystemReadiness
    ai_scientific_review: AIReviewStatus = AIReviewStatus.PENDING
    author_publication_approval: AuthorApprovalStatus = AuthorApprovalStatus.PENDING
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
        self, study_id: str, step_id: str, result: dict[str, Any]
    ) -> ArtifactRecord:
        """Persist a node result and register it as a first-class DAG artifact."""
        self.load_step(study_id, step_id)
        path = self._study_dir(study_id) / "step_results" / f"{step_id}.json"
        write_json_atomic(path, result)
        artifact = self.register_artifact(
            study_id,
            str(path),
            sha256_file(path),
            kind="step_result",
            role=ArtifactRole.OTHER,
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
    ) -> StepInstance:
        step = self.load_step(study_id, step_id)
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
        write_json_atomic(
            self._study_dir(repair.study_id) / "repairs" / f"{repair.repair_id}.json",
            repair,
        )
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
            predecessor_run_id=predecessor_run_id,
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
    "AdjudicationRecord",
    "ArtifactDependency",
    "ArtifactRecord",
    "ArtifactRole",
    "ArtifactStatus",
    "AuthorApprovalStatus",
    "CompletionRecord",
    "DiagnosticOwner",
    "EntryMode",
    "EvidenceChain",
    "EvidenceChainLevel",
    "ExecutionStatus",
    "ExecutorType",
    "GateRecord",
    "GateStatus",
    "GateType",
    "Hypothesis",
    "HypothesisRole",
    "HypothesisVerdict",
    "HypothesisVerdictStatus",
    "NetworkPolicy",
    "Phase",
    "ProjectRecord",
    "PublicationApproval",
    "ReadinessAssessment",
    "RepairContract",
    "RepairStatus",
    "ResearchContractVersion",
    "ResearchSupportLevel",
    "ScopeContractVersion",
    "StepInstance",
    "StudyLifecycle",
    "StudyRecord",
    "StudyVerdict",
    "StudyVerdictStatus",
    "SystemReadiness",
    "WorkflowRepository",
    "affected_artifacts",
    "aggregate_study_verdict",
    "is_secret_path",
    "research_support_level",
    "stable_id",
    "verify_completion_record",
]
