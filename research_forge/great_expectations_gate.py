"""Controlled Great Expectations gate for frozen tabular resources.

Great Expectations is used only for the structural layer of data validation.
Its result can qualify or disqualify a resource for a typed experiment, but it
cannot establish label provenance, temporal availability, construct validity,
population semantics, or a scientific verdict.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import great_expectations as gx
import pandas as pd
from great_expectations import expectations as gxe
from pydantic import Field, model_validator

from .models import StrictModel
from .reproduction_policy import canonical_sha256
from .storage import sha256_file, write_json_atomic


class FrozenExpectation(StrictModel):
    expectation_id: str
    kind: Literal[
        "columns_match_set",
        "not_null",
        "unique",
        "between",
        "in_set",
        "row_count_between",
        "column_type",
    ]
    column: str | None = None
    columns: list[str] = Field(default_factory=list)
    min_value: float | int | None = None
    max_value: float | int | None = None
    value_set: list[str | int | float | bool] = Field(default_factory=list)
    type_name: str | None = None
    mostly: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def required_fields_match_kind(self) -> "FrozenExpectation":
        column_kinds = {"not_null", "unique", "between", "in_set", "column_type"}
        if self.kind in column_kinds and not self.column:
            raise ValueError(f"{self.kind} requires column")
        if self.kind == "columns_match_set" and not self.columns:
            raise ValueError("columns_match_set requires columns")
        if self.kind == "in_set" and not self.value_set:
            raise ValueError("in_set requires value_set")
        if self.kind == "column_type" and not self.type_name:
            raise ValueError("column_type requires type_name")
        if self.kind in {"between", "row_count_between"} and (
            self.min_value is None or self.max_value is None
        ):
            raise ValueError(f"{self.kind} requires min_value and max_value")
        return self


class FrozenDataQualityContract(StrictModel):
    schema_version: int = 1
    contract_id: str
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    file_format: Literal["csv"] = "csv"
    expectations: list[FrozenExpectation] = Field(min_length=1)
    semantic_authority: Literal["structural_only"] = "structural_only"


def _gx_expectation(spec: FrozenExpectation) -> Any:
    meta = {"research_forge_expectation_id": spec.expectation_id}
    if spec.kind == "columns_match_set":
        return gxe.ExpectTableColumnsToMatchSet(
            column_set=spec.columns,
            exact_match=True,
            meta=meta,
        )
    if spec.kind == "not_null":
        return gxe.ExpectColumnValuesToNotBeNull(
            column=str(spec.column),
            mostly=spec.mostly,
            meta=meta,
        )
    if spec.kind == "unique":
        return gxe.ExpectColumnValuesToBeUnique(
            column=str(spec.column),
            mostly=spec.mostly,
            meta=meta,
        )
    if spec.kind == "between":
        return gxe.ExpectColumnValuesToBeBetween(
            column=str(spec.column),
            min_value=spec.min_value,
            max_value=spec.max_value,
            mostly=spec.mostly,
            meta=meta,
        )
    if spec.kind == "in_set":
        return gxe.ExpectColumnValuesToBeInSet(
            column=str(spec.column),
            value_set=spec.value_set,
            mostly=spec.mostly,
            meta=meta,
        )
    if spec.kind == "row_count_between":
        return gxe.ExpectTableRowCountToBeBetween(
            min_value=int(spec.min_value),
            max_value=int(spec.max_value),
            meta=meta,
        )
    if spec.kind == "column_type":
        return gxe.ExpectColumnValuesToBeOfType(
            column=str(spec.column), type_=str(spec.type_name), meta=meta
        )
    raise ValueError(f"unsupported expectation kind: {spec.kind}")


def _result_detail(value: Any) -> dict[str, Any]:
    payload = value.to_json_dict() if hasattr(value, "to_json_dict") else dict(value)
    result = dict(payload.get("result") or {})
    allowed = {
        "element_count",
        "unexpected_count",
        "unexpected_percent",
        "missing_count",
        "missing_percent",
        "observed_value",
        "partial_unexpected_list",
    }
    return {key: result[key] for key in sorted(result) if key in allowed}


def validate_frozen_tabular_resource(
    *,
    dataset_path: str | Path,
    contract: FrozenDataQualityContract,
    report_output: str | Path,
) -> dict[str, Any]:
    """Run an ephemeral, offline GX validation and freeze normalized evidence."""

    dataset = Path(dataset_path).resolve()
    output = Path(report_output).resolve()
    if output == dataset or dataset in output.parents:
        raise ValueError("validation evidence must be outside the frozen dataset")
    actual_digest = sha256_file(dataset)
    if actual_digest != contract.dataset_sha256:
        raise ValueError("dataset hash does not match frozen quality contract")

    frame = pd.read_csv(dataset)
    context = gx.get_context(mode="ephemeral")
    datasource = context.data_sources.add_pandas(name="frozen-tabular-resource")
    asset = datasource.add_dataframe_asset(name="frozen-dataframe")
    batch = asset.add_batch_definition_whole_dataframe(name="whole-dataframe")
    suite = gx.ExpectationSuite(name="frozen-structural-quality-suite")
    for expectation in contract.expectations:
        suite.add_expectation(_gx_expectation(expectation))
    suite = context.suites.add(suite)
    validation = gx.ValidationDefinition(
        name="frozen-structural-quality-validation",
        data=batch,
        suite=suite,
    )
    validation = context.validation_definitions.add(validation)
    result = validation.run(batch_parameters={"dataframe": frame})
    by_id: dict[str, Any] = {}
    for item in result.results:
        payload = item.to_json_dict()
        configuration = dict(payload.get("expectation_config") or {})
        meta = dict(configuration.get("meta") or {})
        expectation_id = str(meta.get("research_forge_expectation_id") or "")
        if not expectation_id:
            raise ValueError("Great Expectations result lost the frozen expectation identity")
        by_id[expectation_id] = item
    checks = []
    for spec in contract.expectations:
        item = by_id.get(spec.expectation_id)
        if item is None:
            raise ValueError(f"Great Expectations omitted result: {spec.expectation_id}")
        checks.append(
            {
                "expectation_id": spec.expectation_id,
                "kind": spec.kind,
                "success": bool(item.success),
                "details": _result_detail(item),
            }
        )
    report = {
        "schema_version": 1,
        "validator": "great_expectations",
        "validator_version": gx.__version__,
        "execution_mode": "ephemeral_offline",
        "contract_id": contract.contract_id,
        "contract_sha256": canonical_sha256(contract),
        "dataset_sha256": actual_digest,
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "success": bool(result.success) and all(item["success"] for item in checks),
        "checks": checks,
        "scientific_authority": "structural_qualification_only",
        "cannot_establish": [
            "label_provenance",
            "temporal_availability",
            "target_leakage_outside_declared_columns",
            "population_and_exclusion_semantics",
            "construct_validity",
            "scientific_verdict",
        ],
    }
    report["report_subject_sha256"] = canonical_sha256(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output, report)
    return report


__all__ = [
    "FrozenDataQualityContract",
    "FrozenExpectation",
    "validate_frozen_tabular_resource",
]
