"""Trust-domain-neutral domain models for Stage 3 reproduction.

The models in this module deliberately separate three authorities:

* the Research Forge control plane signs the frozen reproduction package;
* an ephemeral worker executes the package and emits an *unsigned* report;
* an isolated reproduction verifier validates that report and signs a receipt.

No model assertion alone grants RF-E2.  The evidence assessment also requires
an independently trusted verifier key and the isolation invariants defined
below.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now


SHA256_PATTERN = r"^[a-f0-9]{64}$"


class ResearchForgeEvidenceGrade(StrEnum):
    """Research Forge internal grades; these are not external standards."""

    RF_E1_PACKAGE_VERIFIED = "RF-E1_package_verified"
    RF_E2_CLEAN_ROOM_REPLAYED = "RF-E2_clean_room_replayed"
    RF_E3_INDEPENDENTLY_REPRODUCED = "RF-E3_independently_reproduced"
    RF_E4_EXTERNALLY_REPLICATED = "RF-E4_externally_replicated"


class ReproductionMode(StrEnum):
    CLEAN_ROOM_REPLAY = "clean_room_replay"
    CLEAN_ROOM_REBUILD = "clean_room_rebuild"


class ReproductionScope(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


class ReproductionAssetMode(StrEnum):
    EMBEDDED = "embedded"
    CONTENT_ADDRESSED_REFERENCE = "content_addressed_reference"
    RESTRICTED_REFERENCE = "restricted_reference"


class ReproductionComparisonMode(StrEnum):
    EXACT = "exact"
    NUMERICALLY_EQUIVALENT = "numerically_equivalent"
    DISTRIBUTIONALLY_CONSISTENT = "distributionally_consistent"


class ReproductionJobStatus(StrEnum):
    QUEUED = "queued"
    LAUNCHING = "launching"
    RUNNING = "running"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    MISMATCH = "mismatch"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReproductionResult(StrEnum):
    REPRODUCED = "reproduced_within_registered_tolerance"
    MISMATCH = "reproduction_mismatch"
    BLOCKED_MISSING_ASSET = "reproduction_blocked_missing_asset"
    BLOCKED_POLICY = "reproduction_blocked_policy"
    FAILED_EXECUTION = "reproduction_failed_execution"
    PARTIAL_CHECK = "partial_clean_room_check"
    DEVELOPMENT_VALIDATION = "development_validation_only"


class ReproductionConflictStatus(StrEnum):
    OPEN = "open"
    DIAGNOSED = "diagnosed"
    RESOLVED_BY_SUCCESSOR = "resolved_by_successor"
    CLOSED_NO_CHANGE = "closed_no_change"


class HardwarePolicy(StrictModel):
    architecture: str = "x86_64"
    gpu_class: str | None = None
    exact_gpu_required: bool = False
    allowed_instance_types: list[str] = Field(default_factory=list)


class ComparisonPolicy(StrictModel):
    mode: ReproductionComparisonMode
    sample_ids_must_match: bool = True
    run_cells_must_be_complete: bool = True
    verdict_must_match: bool = True
    metric_tolerance: float = Field(default=0.0, ge=0)
    effect_tolerance: float = Field(default=0.0, ge=0)
    interval_tolerance: float = Field(default=0.0, ge=0)
    distribution_rule: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exact_has_zero_tolerance(self) -> "ComparisonPolicy":
        if self.mode is ReproductionComparisonMode.EXACT and any(
            value != 0
            for value in (
                self.metric_tolerance,
                self.effect_tolerance,
                self.interval_tolerance,
            )
        ):
            raise ValueError("exact reproduction cannot declare a tolerance")
        if (
            self.mode
            is ReproductionComparisonMode.DISTRIBUTIONALLY_CONSISTENT
            and not self.distribution_rule
        ):
            raise ValueError(
                "distributional reproduction requires a frozen compatibility rule"
            )
        return self


class ReproductionPolicy(StrictModel):
    schema_version: int = 1
    policy_id: str = Field(pattern=r"^reproduction-policy-[a-f0-9]{16}$")
    study_id: str
    completion_package_id: str = Field(
        pattern=r"^stage3-completion-[a-f0-9]{16}$"
    )
    profile_id: str
    scope: ReproductionScope = ReproductionScope.FULL
    mode: ReproductionMode = ReproductionMode.CLEAN_ROOM_REPLAY
    cache_policy: Literal["no_reuse"] = "no_reuse"
    network_policy: Literal["none", "frozen_allowlist"] = "none"
    network_allowlist: list[str] = Field(default_factory=list)
    hardware_policy: HardwarePolicy = Field(default_factory=HardwarePolicy)
    comparison_policy: ComparisonPolicy
    required_evidence_grade: ResearchForgeEvidenceGrade = (
        ResearchForgeEvidenceGrade.RF_E2_CLEAN_ROOM_REPLAYED
    )
    full_run_cells_required: bool = True
    frozen_before_reproduction: Literal[True] = True
    immutable: Literal[True] = True
    frozen_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_policy_scope(self) -> "ReproductionPolicy":
        if (
            self.scope is ReproductionScope.PARTIAL
            and self.required_evidence_grade
            is ResearchForgeEvidenceGrade.RF_E2_CLEAN_ROOM_REPLAYED
        ):
            raise ValueError("partial replay cannot request RF-E2")
        if self.network_policy == "none" and self.network_allowlist:
            raise ValueError("offline reproduction cannot have a network allowlist")
        if self.scope is ReproductionScope.FULL and not self.full_run_cells_required:
            raise ValueError("full replay must require every frozen RunCell")
        return self


class ReproductionAsset(StrictModel):
    asset_id: str = Field(pattern=r"^repro-asset-[a-f0-9]{16}$")
    logical_path: str = Field(min_length=1, max_length=2_000)
    mode: ReproductionAssetMode
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int | None = Field(default=None, ge=0)
    uri: str | None = Field(default=None, max_length=4_000)
    license: str | None = Field(default=None, max_length=500)
    required: bool = True
    object_path: str | None = None

    @model_validator(mode="after")
    def validate_asset_location(self) -> "ReproductionAsset":
        if self.mode is ReproductionAssetMode.EMBEDDED and not self.object_path:
            raise ValueError("embedded assets require an object_path")
        if (
            self.mode is not ReproductionAssetMode.EMBEDDED
            and not self.uri
        ):
            raise ValueError("referenced assets require a pinned URI")
        return self


class ReproductionPackageManifest(StrictModel):
    schema_version: int = 1
    package_version: str = "1.0"
    package_id: str = Field(pattern=r"^reproduction-package-[a-f0-9]{16}$")
    package_sha256: str = Field(pattern=SHA256_PATTERN)
    original_archive_sha256: str = Field(pattern=SHA256_PATTERN)
    original_archive_format: Literal["zip", "tar.zst"]
    study_id: str
    completion_package_id: str
    profile_id: str
    required_run_cells: int = Field(ge=1)
    artifact_count: int = Field(ge=1)
    assets: list[ReproductionAsset] = Field(default_factory=list)
    container_image_digests: list[str] = Field(default_factory=list)
    evaluator_digest: str = Field(pattern=SHA256_PATTERN)
    policy_id: str
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    files: dict[str, str] = Field(min_length=1)
    signing_identity: str
    signing_key_id: str
    created_at: str = Field(default_factory=utc_now)


class ReproductionJob(StrictModel):
    schema_version: int = 1
    job_id: str = Field(pattern=r"^reproduction-job-[a-f0-9]{16}$")
    study_id: str
    package_id: str
    package_sha256: str = Field(pattern=SHA256_PATTERN)
    archive_sha256: str = Field(pattern=SHA256_PATTERN)
    mode: ReproductionMode
    scope: ReproductionScope
    required_evidence_grade: ResearchForgeEvidenceGrade
    status: ReproductionJobStatus = ReproductionJobStatus.QUEUED
    requested_by: str
    blocker: dict[str, Any] | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class WorkerIdentity(StrictModel):
    trust_domain_id: str
    cloud: str | None = None
    account_id: str | None = None
    instance_id: str
    image_id: str
    instance_type: str | None = None
    region: str | None = None
    launch_template_version: str | None = None
    boot_id: str
    instance_identity_document_sha256: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    agent_digest: str = Field(pattern=SHA256_PATTERN)


class IsolationReport(StrictModel):
    fresh_worker: bool
    ephemeral_worker: bool
    development_directory_mounted: bool
    original_database_accessible: bool
    cache_reused: bool
    sealed_assets_only: bool
    candidate_network_enabled: bool
    credentials_visible_to_candidate: bool
    signing_key_accessible_to_worker: bool
    original_control_plane_accessible: bool

    def clean_room_invariants_hold(self) -> bool:
        return (
            self.fresh_worker
            and self.ephemeral_worker
            and not self.development_directory_mounted
            and not self.original_database_accessible
            and not self.cache_reused
            and self.sealed_assets_only
            and not self.candidate_network_enabled
            and not self.credentials_visible_to_candidate
            and not self.signing_key_accessible_to_worker
            and not self.original_control_plane_accessible
        )


class ReproductionCoverage(StrictModel):
    required_run_cells: int = Field(ge=1)
    executed_run_cells: int = Field(ge=0)
    qualified_run_cells: int = Field(ge=0)
    sample_ids_complete: bool

    def full(self) -> bool:
        return (
            self.executed_run_cells == self.required_run_cells
            and self.qualified_run_cells == self.required_run_cells
            and self.sample_ids_complete
        )


class ReproductionComparison(StrictModel):
    mode: ReproductionComparisonMode
    original_metric: float | None = None
    reproduced_metric: float | None = None
    metric_matches: bool
    original_effect: float | None = None
    reproduced_effect: float | None = None
    effect_matches: bool
    original_interval: tuple[float, float] | None = None
    reproduced_interval: tuple[float, float] | None = None
    interval_matches: bool
    original_verdict: str
    reproduced_verdict: str
    verdict_matches: bool
    sample_ids_match: bool
    details: dict[str, Any] = Field(default_factory=dict)

    def passed(self) -> bool:
        return all(
            (
                self.metric_matches,
                self.effect_matches,
                self.interval_matches,
                self.verdict_matches,
                self.sample_ids_match,
            )
        )


class WorkerExecutionReport(StrictModel):
    """Unsigned evidence emitted by an ephemeral worker."""

    schema_version: int = 1
    report_id: str = Field(pattern=r"^worker-report-[a-f0-9]{16}$")
    job_id: str = Field(pattern=r"^reproduction-job-[a-f0-9]{16}$")
    study_id: str
    package_id: str
    package_sha256: str = Field(pattern=SHA256_PATTERN)
    archive_sha256: str = Field(pattern=SHA256_PATTERN)
    profile_id: str
    worker_identity: WorkerIdentity
    isolation: IsolationReport
    coverage: ReproductionCoverage
    comparison: ReproductionComparison
    checker_passed: bool
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    stdout_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    stderr_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    resource_telemetry: dict[str, Any] = Field(default_factory=dict)
    failure: dict[str, Any] | None = None
    created_at: str = Field(default_factory=utc_now)


class ExternalVerifierIdentity(StrictModel):
    verifier_id: str
    trust_domain_id: str
    key_id: str
    key_custody: Literal["kms", "hsm", "external_organization"]
    isolated_from_control_plane: Literal[True] = True
    control_plane_can_sign_receipts: Literal[False] = False
    worker_can_access_signing_key: Literal[False] = False
    identity_registry_version: str


class ReproductionReceipt(StrictModel):
    schema_version: int = 1
    receipt_version: str = "1.0"
    receipt_id: str = Field(pattern=r"^reproduction-receipt-[a-f0-9]{16}$")
    job_id: str
    study_id: str
    original_package_sha256: str = Field(pattern=SHA256_PATTERN)
    original_archive_sha256: str = Field(pattern=SHA256_PATTERN)
    profile_id: str
    reproduction_scope: ReproductionScope
    reproduction_mode: ReproductionMode
    worker_identity: WorkerIdentity
    isolation: IsolationReport
    coverage: ReproductionCoverage
    comparison: ReproductionComparison
    result: ReproductionResult
    evidence_grade_awarded: ResearchForgeEvidenceGrade
    verifier_identity: ExternalVerifierIdentity
    worker_report_sha256: str = Field(pattern=SHA256_PATTERN)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def rf_e2_requires_full_clean_room(self) -> "ReproductionReceipt":
        if (
            self.evidence_grade_awarded
            is ResearchForgeEvidenceGrade.RF_E2_CLEAN_ROOM_REPLAYED
        ):
            if self.reproduction_scope is not ReproductionScope.FULL:
                raise ValueError("RF-E2 requires a full replay")
            if not self.isolation.clean_room_invariants_hold():
                raise ValueError("RF-E2 requires every clean-room invariant")
            if not self.coverage.full() or not self.comparison.passed():
                raise ValueError("RF-E2 requires full coverage and comparison")
            if self.result is not ReproductionResult.REPRODUCED:
                raise ValueError("RF-E2 requires a successful reproduction")
        return self


class SignedReproductionReceipt(StrictModel):
    schema_version: int = 1
    receipt: ReproductionReceipt
    algorithm: Literal["Ed25519", "ECDSA_SHA_256", "RSA_PSS_SHA_256"]
    key_id: str
    public_key_fingerprint: str = Field(pattern=SHA256_PATTERN)
    signature_base64: str


class ReproductionEvidenceAssessment(StrictModel):
    schema_version: int = 1
    study_id: str
    highest_grade: ResearchForgeEvidenceGrade
    package_verified: bool
    clean_room_replayed: bool
    independently_reproduced: bool
    externally_replicated: bool
    receipt_ids: list[str] = Field(default_factory=list)
    conflicts_open: bool = False
    publication_readiness_effect: Literal[
        "none", "positive", "under_review", "blocked"
    ] = "none"
    reasons: list[str] = Field(default_factory=list)
    assessed_at: str = Field(default_factory=utc_now)


class ReproductionConflict(StrictModel):
    schema_version: int = 1
    conflict_id: str = Field(pattern=r"^reproduction-conflict-[a-f0-9]{16}$")
    study_id: str
    job_id: str
    original_verdict_id: str
    receipt_id: str | None = None
    status: ReproductionConflictStatus = ReproductionConflictStatus.OPEN
    original_verdict_preserved: Literal[True] = True
    reproduction_result: ReproductionResult
    diagnostic_required: bool = True
    successor_required: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)


__all__ = [
    "ComparisonPolicy",
    "ExternalVerifierIdentity",
    "HardwarePolicy",
    "IsolationReport",
    "ReproductionAsset",
    "ReproductionAssetMode",
    "ReproductionComparison",
    "ReproductionComparisonMode",
    "ReproductionConflict",
    "ReproductionConflictStatus",
    "ReproductionCoverage",
    "ReproductionEvidenceAssessment",
    "ReproductionJob",
    "ReproductionJobStatus",
    "ReproductionMode",
    "ReproductionPackageManifest",
    "ReproductionPolicy",
    "ReproductionReceipt",
    "ReproductionResult",
    "ReproductionScope",
    "ResearchForgeEvidenceGrade",
    "SignedReproductionReceipt",
    "WorkerExecutionReport",
    "WorkerIdentity",
]
