"""Structured Codex output contract for Profile v1 experiment construction."""

from __future__ import annotations

import csv
import io
import json
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel


class GeneratedSourceFile(StrictModel):
    path: str = Field(min_length=3, max_length=240)
    role: Literal[
        "dataset",
        "target_dataset",
        "smoke_dataset",
        "smoke_target_dataset",
        "dataset_adapter",
        "baseline",
        "treatment",
        "evaluator",
        "test",
        "documentation",
    ]
    content: str = Field(max_length=500_000)
    source_basis: list[str] = Field(min_length=1)
    generated: Literal[True] = True

    @model_validator(mode="after")
    def safe_relative_path(self) -> "GeneratedSourceFile":
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "." in path.parts
            or "\\" in self.path
        ):
            raise ValueError("generated file path must be safe and relative")
        if path.suffix.casefold() not in {
            ".csv",
            ".json",
            ".jsonl",
            ".md",
            ".py",
        }:
            raise ValueError("generated file type is not allowed")
        return self


class GeneratedProfileV1Package(StrictModel):
    schema_version: int = 1
    builder_plugin: Literal[
        "computational_paired_comparison_codex_v1"
    ] = "computational_paired_comparison_codex_v1"
    files: list[GeneratedSourceFile] = Field(min_length=5, max_length=80)
    formal_data_path: str
    formal_target_path: str
    smoke_data_path: str
    smoke_target_path: str
    baseline_command: list[str] = Field(min_length=2)
    treatment_command: list[str] = Field(min_length=2)
    evaluator_command: list[str] = Field(min_length=2)
    raw_prediction_path: str = "predictions.jsonl"
    metric_output_path: str = "metrics.json"
    expected_raw_fields: list[str] = Field(min_length=3)
    expected_metric_fields: list[str] = Field(min_length=3)
    conformance_claims: list[str] = Field(min_length=1)
    declared_allowed_arm_delta: list[str] = Field(default_factory=list)
    unresolved_requirements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_package_boundary(self) -> "GeneratedProfileV1Package":
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("generated package contains duplicate paths")
        partition_paths = {
            self.formal_data_path,
            self.formal_target_path,
            self.smoke_data_path,
            self.smoke_target_path,
        }
        if len(partition_paths) != 4:
            raise ValueError(
                "candidate inputs and evaluator targets must use four "
                "different files"
            )
        if not partition_paths.issubset(set(paths)):
            raise ValueError(
                "all candidate and target paths must be declared files"
            )
        roles = {item.role for item in self.files}
        required_roles = {
            "dataset",
            "target_dataset",
            "smoke_dataset",
            "smoke_target_dataset",
            "baseline",
            "treatment",
            "evaluator",
        }
        if not required_roles.issubset(roles):
            raise ValueError(
                "generated package lacks required independent components"
            )
        for command in (
            self.baseline_command,
            self.treatment_command,
            self.evaluator_command,
        ):
            if command[0] != "{python}":
                raise ValueError(
                    "generated commands must use the declared Python runtime"
                )
            if any(not item or "\x00" in item for item in command):
                raise ValueError("generated command contains an invalid token")
        for name, command in (
            ("baseline", self.baseline_command),
            ("treatment", self.treatment_command),
        ):
            if "{data_file}" not in command or "{prediction_file}" not in command:
                raise ValueError(
                    f"{name} command must bind data_file and prediction_file"
                )
        baseline_paths = {
            item.path for item in self.files if item.role == "baseline"
        }
        treatment_paths = {
            item.path for item in self.files if item.role == "treatment"
        }
        if (
            len(self.baseline_command) != len(self.treatment_command)
            or self.baseline_command[1] not in baseline_paths
            or self.treatment_command[1] not in treatment_paths
            or self.baseline_command[2:] != self.treatment_command[2:]
        ):
            raise ValueError(
                "Profile v1 arms must use structurally identical commands "
                "that differ only by their declared implementation file"
            )
        if len(self.declared_allowed_arm_delta) != len(
            set(self.declared_allowed_arm_delta)
        ):
            raise ValueError("declared arm deltas must be unique")
        if not {
            "sample_id",
            "prediction",
            "target_reference",
        }.issubset(self.expected_raw_fields):
            raise ValueError(
                "raw output must support platform-side metric evaluation"
            )
        target_files = {
            item.path: item.role
            for item in self.files
            if item.role in {"target_dataset", "smoke_target_dataset"}
        }
        if target_files.get(self.formal_target_path) != "target_dataset":
            raise ValueError("formal_target_path must identify target_dataset")
        if target_files.get(self.smoke_target_path) != "smoke_target_dataset":
            raise ValueError(
                "smoke_target_path must identify smoke_target_dataset"
            )
        contents = {item.path: item.content for item in self.files}
        formal_rows = _structured_rows(
            self.formal_data_path, contents[self.formal_data_path]
        )
        formal_targets = _structured_rows(
            self.formal_target_path, contents[self.formal_target_path]
        )
        smoke_rows = _structured_rows(
            self.smoke_data_path, contents[self.smoke_data_path]
        )
        smoke_targets = _structured_rows(
            self.smoke_target_path, contents[self.smoke_target_path]
        )
        _validate_candidate_target_partition(
            "formal", formal_rows, formal_targets
        )
        _validate_candidate_target_partition(
            "smoke", smoke_rows, smoke_targets
        )
        formal_ids = {str(row["sample_id"]) for row in formal_rows}
        smoke_ids = {str(row["sample_id"]) for row in smoke_rows}
        if formal_ids.intersection(smoke_ids):
            raise ValueError(
                "smoke and formal partitions contain overlapping sample IDs"
            )
        return self


def _structured_rows(path: str, content: str) -> list[dict[str, object]]:
    suffix = PurePosixPath(path).suffix.casefold()
    if suffix == ".jsonl":
        rows = [
            json.loads(line)
            for line in content.splitlines()
            if line.strip()
        ]
    elif suffix == ".json":
        payload = json.loads(content)
        rows = (
            payload
            if isinstance(payload, list)
            else payload.get("rows", [])
            if isinstance(payload, dict)
            else []
        )
    elif suffix == ".csv":
        rows = list(csv.DictReader(io.StringIO(content)))
    else:
        raise ValueError(
            "candidate and target partitions must be JSON, JSONL, or CSV"
        )
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"generated partition has no structured rows: {path}")
    return rows


def _validate_candidate_target_partition(
    name: str,
    candidate_rows: list[dict[str, object]],
    target_rows: list[dict[str, object]],
) -> None:
    forbidden_candidate_fields = {
        "target",
        "label",
        "gold",
        "answer",
        "reference_answer",
        "ground_truth",
    }
    candidate_required = {"sample_id", "target_reference"}
    target_required = {"target_reference", "target"}
    if any(
        not candidate_required.issubset(row)
        for row in candidate_rows
    ):
        raise ValueError(
            f"{name} candidate rows require sample_id and target_reference"
        )
    leaked = sorted(
        {
            key
            for row in candidate_rows
            for key in row
            if key.casefold() in forbidden_candidate_fields
        }
    )
    if leaked:
        raise ValueError(
            f"{name} candidate rows expose target fields: "
            + ", ".join(leaked)
        )
    if any(not target_required.issubset(row) for row in target_rows):
        raise ValueError(
            f"{name} evaluator targets require target_reference and target"
        )
    sample_ids = [str(row["sample_id"]) for row in candidate_rows]
    references = [
        str(row["target_reference"]) for row in candidate_rows
    ]
    target_references = [
        str(row["target_reference"]) for row in target_rows
    ]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError(f"{name} candidate sample IDs are duplicated")
    if len(references) != len(set(references)):
        raise ValueError(f"{name} candidate target references are duplicated")
    if len(target_references) != len(set(target_references)):
        raise ValueError(f"{name} evaluator target references are duplicated")
    if set(references) != set(target_references):
        raise ValueError(
            f"{name} candidate and evaluator target references do not match"
        )


__all__ = ["GeneratedProfileV1Package", "GeneratedSourceFile"]
