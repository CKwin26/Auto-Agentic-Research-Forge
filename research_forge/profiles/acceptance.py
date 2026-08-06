"""Evidence-derived acceptance and maturity for Experiment Profiles.

Profile maturity is deliberately computed from test receipts.  A descriptor
may say what code exists, but it cannot promote itself to C3 merely because a
compiler, runner, and evaluator are registered.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
import hashlib
import json

from pydantic import ConfigDict, Field, model_validator

from ..models import StrictModel
from .sdk import ProfileMaturity


class ProfileImplementationStatus(StrEnum):
    DESCRIBED = "described"
    SCHEMA_REGISTERED = "schema_registered"
    COMPONENT_VALIDATED = "component_validated"
    CONTROLLED_E2E_VALIDATED = "controlled_e2e_validated"
    REAL_CASE_VALIDATED = "real_case_validated"
    EXTERNALLY_REPRODUCED = "externally_reproduced"


class ProfileAvailability(StrEnum):
    """Deployment availability, deliberately separate from implementation maturity."""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    OWNER_APPROVAL_REQUIRED = "owner_approval_required"


class AcceptanceCheck(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    check_id: str
    passed: bool
    receipt_artifact_id: str | None = None
    detail: str = ""


class ProfileAcceptanceReport(StrictModel):
    """Machine-readable evidence used to derive, never declare, maturity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = 1
    profile_id: str
    profile_version: str
    code_commit: str | None = None
    environment_digest: str | None = None
    source_hashes: dict[str, str] = Field(default_factory=dict)
    registry_check: AcceptanceCheck
    schema_check: AcceptanceCheck
    qualification_check: AcceptanceCheck
    contract_completion_check: AcceptanceCheck
    semantic_diff_check: AcceptanceCheck
    run_plan_serialization_check: AcceptanceCheck
    dry_run_check: AcceptanceCheck
    formal_e2e_check: AcceptanceCheck
    verdict_check: AcceptanceCheck
    evidence_binding_check: AcceptanceCheck
    completion_record_check: AcceptanceCheck
    independent_metric_recompute_check: AcceptanceCheck
    negative_case_checks: tuple[AcceptanceCheck, ...] = ()
    mutation_checks: tuple[AcceptanceCheck, ...] = ()
    real_success_case_check: AcceptanceCheck | None = None
    real_blocked_case_check: AcceptanceCheck | None = None
    clean_room_replay_check: AcceptanceCheck | None = None
    second_implementation_recompute_check: AcceptanceCheck | None = None
    immutability_check: AcceptanceCheck | None = None
    external_reproduction_check: AcceptanceCheck | None = None
    recompute_command: tuple[str, ...] = ()
    known_limits: tuple[str, ...] = ()
    assessed_maturity: ProfileMaturity | None = None

    @model_validator(mode="after")
    def maturity_must_match_evidence(self) -> "ProfileAcceptanceReport":
        for label, checks in (
            ("negative_case_checks", self.negative_case_checks),
            ("mutation_checks", self.mutation_checks),
        ):
            identifiers = [item.check_id for item in checks]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} must contain unique check_id values")
        derived = assess_profile_maturity(self, ignore_declared=True)
        if self.assessed_maturity is not None and self.assessed_maturity != derived:
            raise ValueError(
                "assessed_maturity is evidence-derived and does not match receipts"
            )
        return self

    def with_assessed_maturity(self) -> "ProfileAcceptanceReport":
        return self.model_copy(
            update={"assessed_maturity": assess_profile_maturity(self)}
        )


def _passed(check: AcceptanceCheck | None) -> bool:
    return bool(check and check.passed)


def assess_profile_maturity(
    report: ProfileAcceptanceReport,
    *,
    ignore_declared: bool = False,
) -> ProfileMaturity:
    """Return the highest rung whose complete evidence requirements pass."""

    maturity = ProfileMaturity.C0_DESCRIBED
    if not all((_passed(report.registry_check), _passed(report.schema_check))):
        return maturity
    maturity = ProfileMaturity.C1_SCHEMA
    c2_checks = (
        report.qualification_check,
        report.contract_completion_check,
        report.semantic_diff_check,
        report.run_plan_serialization_check,
        report.dry_run_check,
    )
    if not all(_passed(item) for item in c2_checks):
        return maturity
    maturity = ProfileMaturity.C2_DRY_RUN
    c3_checks = (
        report.formal_e2e_check,
        report.verdict_check,
        report.evidence_binding_check,
        report.completion_record_check,
        report.independent_metric_recompute_check,
    )
    if not (
        all(_passed(item) for item in c3_checks)
        and len(report.negative_case_checks) >= 6
        and all(_passed(item) for item in report.negative_case_checks)
        and len(report.mutation_checks) >= 4
        and all(_passed(item) for item in report.mutation_checks)
        and bool(report.recompute_command)
    ):
        return maturity
    maturity = ProfileMaturity.C3_REAL_FIXTURE
    c4_checks = (
        report.real_success_case_check,
        report.real_blocked_case_check,
        report.clean_room_replay_check,
        report.second_implementation_recompute_check,
        report.immutability_check,
    )
    if not all(_passed(item) for item in c4_checks):
        return maturity
    maturity = ProfileMaturity.C4_INDEPENDENT_REPLAY
    if _passed(report.external_reproduction_check):
        maturity = ProfileMaturity.C5_MULTI_PROJECT
    return maturity


def implementation_status(maturity: ProfileMaturity) -> ProfileImplementationStatus:
    return {
        ProfileMaturity.C0_DESCRIBED: ProfileImplementationStatus.DESCRIBED,
        ProfileMaturity.C1_SCHEMA: ProfileImplementationStatus.SCHEMA_REGISTERED,
        ProfileMaturity.C2_DRY_RUN: ProfileImplementationStatus.COMPONENT_VALIDATED,
        ProfileMaturity.C3_REAL_FIXTURE: ProfileImplementationStatus.CONTROLLED_E2E_VALIDATED,
        ProfileMaturity.C4_INDEPENDENT_REPLAY: ProfileImplementationStatus.REAL_CASE_VALIDATED,
        ProfileMaturity.C5_MULTI_PROJECT: ProfileImplementationStatus.EXTERNALLY_REPRODUCED,
    }[maturity]


def availability_for_maturity(
    maturity: ProfileMaturity,
    *,
    formal_path_connected: bool = True,
    runtime_resources_available: bool = True,
    owner_approval_required: bool = False,
) -> ProfileAvailability:
    """Return current availability without presenting maturity as availability.

    Maturity says what has been verified historically.  Availability says
    whether this installation can use that implementation now.  Callers that
    do not yet have project bindings receive a conservative release-level
    answer; Study admission performs the final resource and owner checks.
    """

    if (
        maturity is ProfileMaturity.C0_DESCRIBED
        or not runtime_resources_available
    ):
        return ProfileAvailability.UNAVAILABLE
    if maturity in {ProfileMaturity.C1_SCHEMA, ProfileMaturity.C2_DRY_RUN}:
        return ProfileAvailability.DEGRADED
    if not formal_path_connected:
        return ProfileAvailability.UNAVAILABLE
    if owner_approval_required:
        return ProfileAvailability.OWNER_APPROVAL_REQUIRED
    return ProfileAvailability.AVAILABLE


def load_profile_acceptance_report(
    profile_id: str,
) -> ProfileAcceptanceReport | None:
    """Load release evidence; invalid or absent evidence never promotes."""

    path = Path(__file__).parent / "acceptance_reports" / f"{profile_id}.json"
    if not path.is_file():
        return None
    try:
        report = ProfileAcceptanceReport.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        ).with_assessed_maturity()
        if not acceptance_source_hashes_match(report):
            return None
        return report
    except (OSError, ValueError):
        return None


def acceptance_source_hashes_match(
    report: ProfileAcceptanceReport,
    *,
    repository_root: Path | None = None,
) -> bool:
    """Fail closed when acceptance evidence no longer matches current code."""

    if not report.source_hashes:
        return False
    root = (
        repository_root.resolve()
        if repository_root is not None
        else Path(__file__).resolve().parents[2]
    )
    for relative, expected in report.source_hashes.items():
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return False
        if not candidate.is_file():
            return False
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual != expected:
            return False
    return True


__all__ = [
    "AcceptanceCheck",
    "ProfileAcceptanceReport",
    "ProfileAvailability",
    "ProfileImplementationStatus",
    "assess_profile_maturity",
    "acceptance_source_hashes_match",
    "availability_for_maturity",
    "implementation_status",
    "load_profile_acceptance_report",
]
