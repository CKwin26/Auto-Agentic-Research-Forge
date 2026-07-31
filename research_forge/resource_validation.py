from __future__ import annotations

"""Deterministic resource lifecycle and scientific-semantic validation.

This validator intentionally covers transparent CSV/JSONL resources first.
Great Expectations can provide a hash-bound external structure gate, but it
cannot replace the temporal and research-semantic checks implemented here.
"""

import csv
import json
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from .models import StrictModel, utc_now
from .storage import sha256_file
from .workflow_domain import ResourceLifecycleStatus, stable_id


_LIFECYCLE_ORDER = list(ResourceLifecycleStatus)


class ResourceValidationCheck(StrictModel):
    check_id: str
    layer: str
    passed: bool
    detail: str


class ResourceLifecycleRecord(StrictModel):
    schema_version: int = 1
    resource_id: str
    status: ResourceLifecycleStatus
    local_path: str | None = None
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    schema_report_id: str | None = None
    external_schema_validator: str | None = None
    external_schema_report_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    temporal_report_id: str | None = None
    semantic_report_id: str | None = None
    checks: list[ResourceValidationCheck] = Field(default_factory=list)
    authorized_contract_refs: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def evidence_matches_status(self) -> "ResourceLifecycleRecord":
        rank = _LIFECYCLE_ORDER.index(self.status)
        if rank >= _LIFECYCLE_ORDER.index(ResourceLifecycleStatus.MATERIALIZED):
            if not self.local_path or not self.content_sha256:
                raise ValueError("materialized resources require path and hash")
        if rank >= _LIFECYCLE_ORDER.index(ResourceLifecycleStatus.SCHEMA_VALIDATED):
            if not self.schema_report_id:
                raise ValueError("schema-validated resources require a report")
        if rank >= _LIFECYCLE_ORDER.index(
            ResourceLifecycleStatus.SEMANTICALLY_VALIDATED
        ):
            if not self.temporal_report_id or not self.semantic_report_id:
                raise ValueError(
                    "semantic validation requires temporal and semantic reports"
                )
        return self


class ResourceValidationReport(StrictModel):
    schema_version: int = 1
    report_id: str
    resource_id: str
    status: ResourceLifecycleStatus
    row_count: int = Field(ge=0)
    columns: list[str]
    checks: list[ResourceValidationCheck]
    record: ResourceLifecycleRecord
    generated_at: str = Field(default_factory=utc_now)


class ExecutionReadinessReport(StrictModel):
    schema_version: int = 1
    readiness_id: str
    study_id: str
    contract_version: int = Field(ge=1)
    ready: bool
    checks: dict[str, bool]
    blocking_issues: list[str]
    generated_at: str = Field(default_factory=utc_now)


def _read_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = [dict(item) for item in reader]
            return rows, list(reader.fieldnames or [])
    if suffix in {".jsonl", ".ndjson"}:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        columns = sorted({key for row in rows for key in row})
        return rows, columns
    raise ValueError("only CSV and JSONL resources are currently certified")


def validate_tabular_resource(
    path: str | Path,
    *,
    resource_id: str,
    required_columns: list[str],
    unique_key: str,
    target_column: str,
    feature_columns: list[str],
    semantic_contract: dict[str, Any],
    decision_time_column: str | None = None,
    feature_time_column: str | None = None,
    external_schema_report: dict[str, Any] | None = None,
) -> ResourceValidationReport:
    source = Path(path).resolve()
    if not source.is_file():
        raise ValueError("resource is not materialized at the declared path")
    rows, columns = _read_rows(source)
    checks: list[ResourceValidationCheck] = []

    def check(check_id: str, layer: str, passed: bool, detail: str) -> None:
        checks.append(
            ResourceValidationCheck(
                check_id=check_id,
                layer=layer,
                passed=passed,
                detail=detail,
            )
        )

    missing_columns = sorted(set(required_columns).difference(columns))
    check(
        "required_columns",
        "structure",
        not missing_columns,
        "all required columns exist"
        if not missing_columns
        else "missing: " + ", ".join(missing_columns),
    )
    keys = [str(row.get(unique_key, "")) for row in rows]
    check(
        "unique_key",
        "structure",
        bool(rows) and all(keys) and len(keys) == len(set(keys)),
        "unique, non-empty row identifiers are required",
    )
    check(
        "target_present",
        "structure",
        target_column in columns
        and all(row.get(target_column) not in {None, ""} for row in rows),
        "the authoritative target must be present for every formal row",
    )
    external_report_digest = None
    if external_schema_report is not None:
        external_report_digest = str(
            external_schema_report.get("report_subject_sha256") or ""
        )
        external_valid = (
            external_schema_report.get("validator") == "great_expectations"
            and external_schema_report.get("execution_mode")
            == "ephemeral_offline"
            and external_schema_report.get("dataset_sha256")
            == sha256_file(source)
            and external_schema_report.get("success") is True
            and len(external_report_digest) == 64
        )
        check(
            "great_expectations_frozen_structure_gate",
            "structure",
            external_valid,
            (
                "hash-bound Great Expectations structural validation passed"
                if external_valid
                else "Great Expectations structural validation is absent, failed, or bound to another dataset"
            ),
        )

    check(
        "target_not_feature",
        "temporal_information_boundary",
        target_column not in set(feature_columns),
        "the target cannot be an input feature",
    )
    if decision_time_column and feature_time_column:
        time_valid = all(
            str(row.get(feature_time_column, ""))
            <= str(row.get(decision_time_column, ""))
            and bool(row.get(feature_time_column))
            and bool(row.get(decision_time_column))
            for row in rows
        )
        check(
            "point_in_time_availability",
            "temporal_information_boundary",
            time_valid,
            "feature timestamps must not exceed decision timestamps",
        )

    semantic_fields = (
        "label_rule",
        "target_population",
        "inclusion_rule",
        "exclusion_rule",
        "denominator_rule",
        "version_policy",
    )
    missing_semantics = [
        name
        for name in semantic_fields
        if not str(semantic_contract.get(name) or "").strip()
    ]
    check(
        "research_semantics",
        "research_semantics",
        not missing_semantics,
        "all semantic rules are frozen"
        if not missing_semantics
        else "missing: " + ", ".join(missing_semantics),
    )

    structure_passed = all(
        item.passed for item in checks if item.layer == "structure"
    )
    temporal_passed = all(
        item.passed
        for item in checks
        if item.layer == "temporal_information_boundary"
    )
    semantic_passed = all(
        item.passed for item in checks if item.layer == "research_semantics"
    )
    status = ResourceLifecycleStatus.MATERIALIZED
    schema_report_id = None
    temporal_report_id = None
    semantic_report_id = None
    digest = sha256_file(source)
    if structure_passed:
        status = ResourceLifecycleStatus.SCHEMA_VALIDATED
        schema_report_id = stable_id(
            "schema-report", resource_id, digest, external_report_digest or "internal"
        )
    if structure_passed and temporal_passed and semantic_passed:
        status = ResourceLifecycleStatus.SEMANTICALLY_VALIDATED
        temporal_report_id = stable_id("temporal-report", resource_id, digest)
        semantic_report_id = stable_id("semantic-report", resource_id, digest)
    record = ResourceLifecycleRecord(
        resource_id=resource_id,
        status=status,
        local_path=str(source),
        content_sha256=digest,
        schema_report_id=schema_report_id,
        external_schema_validator=(
            "great_expectations" if external_schema_report is not None else None
        ),
        external_schema_report_sha256=external_report_digest,
        temporal_report_id=temporal_report_id,
        semantic_report_id=semantic_report_id,
        checks=checks,
    )
    return ResourceValidationReport(
        report_id=stable_id("resource-validation", resource_id, digest),
        resource_id=resource_id,
        status=status,
        row_count=len(rows),
        columns=columns,
        checks=checks,
        record=record,
    )


def freeze_resource_record(
    record: ResourceLifecycleRecord,
    *,
    contract_ref: str,
) -> ResourceLifecycleRecord:
    if record.status is not ResourceLifecycleStatus.SEMANTICALLY_VALIDATED:
        raise ValueError("only semantically validated resources may be frozen")
    return record.model_copy(
        update={
            "status": ResourceLifecycleStatus.FROZEN,
            "authorized_contract_refs": sorted(
                {*record.authorized_contract_refs, contract_ref}
            ),
            "updated_at": utc_now(),
        }
    )


def assess_execution_readiness(
    *,
    study_id: str,
    contract_version: int,
    compile_passed: bool,
    dry_run_passed: bool,
    required_resource_ids: list[str],
    resource_records: list[ResourceLifecycleRecord],
    required_algorithm_ids: list[str],
    resolved_algorithm_entrypoints: dict[str, str],
) -> ExecutionReadinessReport:
    by_resource = {item.resource_id: item for item in resource_records}
    resource_ready = all(
        resource_id in by_resource
        and by_resource[resource_id].status
        in {
            ResourceLifecycleStatus.SEMANTICALLY_VALIDATED,
            ResourceLifecycleStatus.FROZEN,
        }
        for resource_id in required_resource_ids
    )
    algorithms_ready = all(
        algorithm_id in resolved_algorithm_entrypoints
        and Path(resolved_algorithm_entrypoints[algorithm_id]).is_file()
        for algorithm_id in required_algorithm_ids
    )
    checks = {
        "contract_compiled": compile_passed,
        "non_formal_dry_run_passed": dry_run_passed,
        "resources_semantically_validated": resource_ready,
        "algorithm_entrypoints_materialized": algorithms_ready,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    return ExecutionReadinessReport(
        readiness_id=stable_id(
            "execution-readiness", study_id, contract_version, *blockers
        ),
        study_id=study_id,
        contract_version=contract_version,
        ready=not blockers,
        checks=checks,
        blocking_issues=blockers,
    )


__all__ = [
    "ExecutionReadinessReport",
    "ResourceLifecycleRecord",
    "ResourceValidationCheck",
    "ResourceValidationReport",
    "assess_execution_readiness",
    "freeze_resource_record",
    "validate_tabular_resource",
]
