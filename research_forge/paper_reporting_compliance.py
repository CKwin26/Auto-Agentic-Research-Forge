from __future__ import annotations

"""Mandatory reporting controls that prevent narrative cherry-picking."""

from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel, utc_now


class MandatoryReportingItem(StrictModel):
    reporting_item_id: str = Field(pattern=r"^report-[a-z0-9-]{2,100}$")
    claim_id: str
    category: Literal[
        "primary",
        "secondary",
        "negative",
        "safety",
        "limitation",
        "operational",
    ]
    material: bool = True
    preregistered: bool = False
    required_destination: Literal[
        "main_text", "limitations", "supplement", "results_registry", "completion_package"
    ]
    destination_section: str | None = None
    rationale: str = Field(min_length=10, max_length=2000)

    @model_validator(mode="after")
    def main_text_requires_section(self) -> "MandatoryReportingItem":
        if self.required_destination in {"main_text", "limitations"} and not self.destination_section:
            raise ValueError("main-text reporting items require a destination section")
        return self


class MandatoryReportingRegister(StrictModel):
    schema_version: int = 1
    study_id: str
    source_claim_envelope_id: str
    items: list[MandatoryReportingItem]
    frozen: bool = False
    frozen_at: str | None = None
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_register(self) -> "MandatoryReportingRegister":
        identifiers = [item.reporting_item_id for item in self.items]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("mandatory reporting item IDs must be unique")
        claims = [item.claim_id for item in self.items]
        if len(claims) != len(set(claims)):
            raise ValueError("each claim may appear only once in the reporting register")
        if self.frozen and not self.frozen_at:
            raise ValueError("a frozen reporting register requires frozen_at")
        return self

    def required_claim_ids(self) -> set[str]:
        return {item.claim_id for item in self.items}

    def main_text_claim_ids(self) -> set[str]:
        return {
            item.claim_id
            for item in self.items
            if item.required_destination in {"main_text", "limitations"}
        }


class ReportingIntegrityFinding(StrictModel):
    finding_id: str = Field(pattern=r"^reporting-finding-[a-z0-9-]{2,100}$")
    severity: Literal["blocking", "major", "minor"]
    claim_id: str | None = None
    message: str
    required_action: str


class ReportingIntegrityReport(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    mandatory_claim_ids: list[str]
    presented_claim_ids: list[str]
    omitted_claim_ids: list[str]
    findings: list[ReportingIntegrityFinding]


def audit_reporting_integrity(
    register: MandatoryReportingRegister,
    *,
    presented_claim_ids: list[str],
    allowed_claim_ids: set[str] | None = None,
) -> ReportingIntegrityReport:
    presented = set(presented_claim_ids)
    mandatory = register.required_claim_ids()
    omissions = sorted(mandatory - presented)
    findings: list[ReportingIntegrityFinding] = []
    for claim_id in omissions:
        item = next(item for item in register.items if item.claim_id == claim_id)
        findings.append(
            ReportingIntegrityFinding(
                finding_id=f"reporting-finding-omitted-{claim_id.casefold().replace('_', '-')}",
                severity="blocking" if item.material or item.preregistered else "major",
                claim_id=claim_id,
                message=(
                    f"mandatory {item.category} claim {claim_id} has no declared "
                    f"destination in the publication package"
                ),
                required_action=(
                    f"place the claim in {item.required_destination} or create a "
                    "new frozen reporting-register version with an auditable reason"
                ),
            )
        )
    if allowed_claim_ids is not None:
        for claim_id in sorted(presented - allowed_claim_ids):
            findings.append(
                ReportingIntegrityFinding(
                    finding_id=f"reporting-finding-unknown-{claim_id.casefold().replace('_', '-')}",
                    severity="blocking",
                    claim_id=claim_id,
                    message="the manuscript presents a claim outside the frozen claim envelope",
                    required_action="remove the claim or return to Stage 3 for a successor verdict",
                )
            )
    return ReportingIntegrityReport(
        passed=not any(item.severity in {"blocking", "major"} for item in findings),
        mandatory_claim_ids=sorted(mandatory),
        presented_claim_ids=sorted(presented),
        omitted_claim_ids=omissions,
        findings=findings,
    )


__all__ = [
    "MandatoryReportingItem",
    "MandatoryReportingRegister",
    "ReportingIntegrityFinding",
    "ReportingIntegrityReport",
    "audit_reporting_integrity",
]
