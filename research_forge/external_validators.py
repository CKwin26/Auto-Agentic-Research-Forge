from __future__ import annotations

"""Normalized, diagnostic-only boundary for optional open-source validators."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from .models import StrictModel, utc_now


class ExternalValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED_NOT_APPLICABLE = "skipped_not_applicable"
    BLOCKED_OPTIONAL_DEPENDENCY = "blocked_optional_dependency"


class ExternalValidatorFinding(StrictModel):
    code: str
    severity: Literal["blocking", "major", "warning"]
    message: str
    source_check: str


class ExternalValidationRecord(StrictModel):
    schema_version: int = 1
    tool: Literal["evidently", "great_expectations", "manubot", "statcheck"]
    tool_version: str
    status: ExternalValidationStatus
    authority: Literal["diagnostic_only"] = "diagnostic_only"
    input_artifact_ids: list[str] = Field(min_length=1)
    input_hashes: dict[str, str] = Field(min_length=1)
    configuration_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    raw_output_artifact_id: str
    findings: list[ExternalValidatorFinding] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


def _status(value: Any) -> str:
    return str(value or "").strip().casefold()


def normalize_great_expectations_checkpoint(
    raw: dict[str, Any],
    *,
    tool_version: str,
    input_artifact_ids: list[str],
    input_hashes: dict[str, str],
    configuration_sha256: str,
    raw_output_artifact_id: str,
) -> ExternalValidationRecord:
    """Normalize a frozen Great Expectations Checkpoint result.

    The raw dictionary must already be stored as an immutable artifact.
    """

    findings: list[ExternalValidatorFinding] = []
    for result in dict(raw.get("run_results") or {}).values():
        validation = (
            result.get("validation_result", result) if isinstance(result, dict) else {}
        )
        for item in validation.get("results", []):
            if not isinstance(item, dict) or item.get("success") is True:
                continue
            config = dict(item.get("expectation_config") or {})
            check = str(
                config.get("type")
                or config.get("expectation_type")
                or "unknown_expectation"
            )
            findings.append(
                ExternalValidatorFinding(
                    code=f"GX-{check.upper().replace('_', '-')}",
                    severity="blocking",
                    message="A frozen data expectation failed.",
                    source_check=check,
                )
            )
    success = raw.get("success") is True and not findings
    return ExternalValidationRecord(
        tool="great_expectations",
        tool_version=tool_version,
        status=(
            ExternalValidationStatus.PASSED
            if success
            else ExternalValidationStatus.FAILED
        ),
        input_artifact_ids=input_artifact_ids,
        input_hashes=input_hashes,
        configuration_sha256=configuration_sha256,
        raw_output_artifact_id=raw_output_artifact_id,
        findings=findings,
    )


def normalize_evidently_snapshot(
    raw: dict[str, Any],
    *,
    tool_version: str,
    input_artifact_ids: list[str],
    input_hashes: dict[str, str],
    configuration_sha256: str,
    raw_output_artifact_id: str,
) -> ExternalValidationRecord:
    """Normalize frozen Evidently tests without granting them verdict authority."""

    findings: list[ExternalValidatorFinding] = []
    tests = raw.get("tests")
    if not isinstance(tests, list):
        tests = raw.get("test_results")
    for index, item in enumerate(tests or [], start=1):
        if not isinstance(item, dict):
            continue
        status = _status(item.get("status") or item.get("result"))
        if status in {"success", "passed", "pass", "ok"}:
            continue
        check = str(
            item.get("name") or item.get("metric") or item.get("id") or f"test_{index}"
        )
        findings.append(
            ExternalValidatorFinding(
                code=f"EVIDENTLY-{index}",
                severity=("warning" if status in {"warning", "warn"} else "major"),
                message=str(
                    item.get("description")
                    or item.get("message")
                    or f"Evidently diagnostic {check} did not pass."
                ),
                source_check=check,
            )
        )
    failed = any(item.severity != "warning" for item in findings)
    return ExternalValidationRecord(
        tool="evidently",
        tool_version=tool_version,
        status=(
            ExternalValidationStatus.FAILED
            if failed
            else ExternalValidationStatus.PASSED
        ),
        input_artifact_ids=input_artifact_ids,
        input_hashes=input_hashes,
        configuration_sha256=configuration_sha256,
        raw_output_artifact_id=raw_output_artifact_id,
        findings=findings,
    )


def blocked_optional_validator(
    *,
    tool: Literal["evidently", "great_expectations", "manubot", "statcheck"],
    tool_version: str = "not_installed",
    input_artifact_ids: list[str],
    input_hashes: dict[str, str],
    configuration_sha256: str,
    raw_output_artifact_id: str,
) -> ExternalValidationRecord:
    return ExternalValidationRecord(
        tool=tool,
        tool_version=tool_version,
        status=ExternalValidationStatus.BLOCKED_OPTIONAL_DEPENDENCY,
        input_artifact_ids=input_artifact_ids,
        input_hashes=input_hashes,
        configuration_sha256=configuration_sha256,
        raw_output_artifact_id=raw_output_artifact_id,
        findings=[
            ExternalValidatorFinding(
                code="OPTIONAL-DEPENDENCY-NOT-INSTALLED",
                severity="warning",
                message=(
                    f"{tool} is unavailable; no scientific validation was "
                    "claimed for this optional check."
                ),
                source_check="dependency_probe",
            )
        ],
    )


__all__ = [
    "ExternalValidationRecord",
    "ExternalValidationStatus",
    "ExternalValidatorFinding",
    "blocked_optional_validator",
    "normalize_evidently_snapshot",
    "normalize_great_expectations_checkpoint",
]
