from __future__ import annotations

from pathlib import Path

import pytest

from research_forge.great_expectations_gate import (
    FrozenDataQualityContract,
    FrozenExpectation,
    validate_frozen_tabular_resource,
)
from research_forge.storage import read_json, sha256_file


def _dataset(path: Path, *, duplicate: bool = False) -> Path:
    path.write_text(
        "sample_id,feature,label\n"
        "s1,0.1,0\n"
        f"{'s1' if duplicate else 's2'},0.9,1\n",
        encoding="utf-8",
    )
    return path


def _contract(path: Path) -> FrozenDataQualityContract:
    return FrozenDataQualityContract(
        contract_id="quality-contract-fixture-v1",
        dataset_sha256=sha256_file(path),
        expectations=[
            FrozenExpectation(
                expectation_id="columns",
                kind="columns_match_set",
                columns=["sample_id", "feature", "label"],
            ),
            FrozenExpectation(
                expectation_id="id-not-null",
                kind="not_null",
                column="sample_id",
            ),
            FrozenExpectation(
                expectation_id="id-unique",
                kind="unique",
                column="sample_id",
            ),
            FrozenExpectation(
                expectation_id="label-set",
                kind="in_set",
                column="label",
                value_set=[0, 1],
            ),
            FrozenExpectation(
                expectation_id="feature-range",
                kind="between",
                column="feature",
                min_value=0,
                max_value=1,
            ),
            FrozenExpectation(
                expectation_id="rows",
                kind="row_count_between",
                min_value=2,
                max_value=2,
            ),
        ],
    )


def test_real_great_expectations_runtime_qualifies_frozen_csv(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path / "dataset.csv")
    report_path = tmp_path / "evidence" / "quality-report.json"

    report = validate_frozen_tabular_resource(
        dataset_path=dataset,
        contract=_contract(dataset),
        report_output=report_path,
    )

    assert report["success"] is True
    assert report["validator_version"] == "1.19.1"
    assert report["execution_mode"] == "ephemeral_offline"
    assert len(report["checks"]) == 6
    assert read_json(report_path)["report_subject_sha256"] == report["report_subject_sha256"]
    assert "scientific_verdict" in report["cannot_establish"]


def test_failed_expectation_disqualifies_but_does_not_make_verdict(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path / "dataset.csv", duplicate=True)
    report = validate_frozen_tabular_resource(
        dataset_path=dataset,
        contract=_contract(dataset),
        report_output=tmp_path / "evidence" / "failed.json",
    )

    assert report["success"] is False
    failed = [item for item in report["checks"] if not item["success"]]
    assert [item["expectation_id"] for item in failed] == ["id-unique"]
    assert report["scientific_authority"] == "structural_qualification_only"


def test_dataset_mutation_is_rejected_before_gx_execution(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path / "dataset.csv")
    contract = _contract(dataset)
    dataset.write_text("sample_id,feature,label\ns1,0.1,0\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hash"):
        validate_frozen_tabular_resource(
            dataset_path=dataset,
            contract=contract,
            report_output=tmp_path / "evidence" / "report.json",
        )


def test_quality_report_cannot_be_written_inside_frozen_dataset(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path / "dataset.csv")
    with pytest.raises(ValueError, match="outside"):
        validate_frozen_tabular_resource(
            dataset_path=dataset,
            contract=_contract(dataset),
            report_output=dataset / "report.json",
        )
