from pathlib import Path

from research_forge.resource_validation import (
    assess_execution_readiness,
    freeze_resource_record,
    validate_tabular_resource,
)
from research_forge.great_expectations_gate import (
    FrozenDataQualityContract,
    FrozenExpectation,
    validate_frozen_tabular_resource,
)
from research_forge.storage import sha256_file
from research_forge.workflow_domain import ResourceLifecycleStatus


SEMANTIC_CONTRACT = {
    "label_rule": "label is the frozen gold field",
    "target_population": "all rows in the frozen evaluation snapshot",
    "inclusion_rule": "include rows with complete inputs and target",
    "exclusion_rule": "exclude malformed rows with immutable reason code",
    "denominator_rule": "all included frozen rows",
    "version_policy": "content SHA-256 identifies the dataset version",
}


def _dataset(path: Path) -> None:
    path.write_text(
        "case_id,feature,label,feature_time,decision_time\n"
        "a,1,0,2026-01-01,2026-01-02\n"
        "b,2,1,2026-01-01,2026-01-02\n",
        encoding="utf-8",
    )


def test_three_layer_validation_can_freeze_a_semantic_resource(tmp_path: Path) -> None:
    source = tmp_path / "dataset.csv"
    _dataset(source)

    report = validate_tabular_resource(
        source,
        resource_id="resource-dataset",
        required_columns=["case_id", "feature", "label"],
        unique_key="case_id",
        target_column="label",
        feature_columns=["feature"],
        decision_time_column="decision_time",
        feature_time_column="feature_time",
        semantic_contract=SEMANTIC_CONTRACT,
    )
    frozen = freeze_resource_record(
        report.record, contract_ref="study-1:research-v1"
    )

    assert report.status is ResourceLifecycleStatus.SEMANTICALLY_VALIDATED
    assert frozen.status is ResourceLifecycleStatus.FROZEN
    assert frozen.authorized_contract_refs == ["study-1:research-v1"]


def test_target_leakage_prevents_semantic_validation(tmp_path: Path) -> None:
    source = tmp_path / "dataset.csv"
    _dataset(source)

    report = validate_tabular_resource(
        source,
        resource_id="resource-leaky",
        required_columns=["case_id", "feature", "label"],
        unique_key="case_id",
        target_column="label",
        feature_columns=["feature", "label"],
        semantic_contract=SEMANTIC_CONTRACT,
    )

    assert report.status is ResourceLifecycleStatus.SCHEMA_VALIDATED
    assert any(
        item.check_id == "target_not_feature" and not item.passed
        for item in report.checks
    )


def test_missing_research_semantics_stops_at_schema_validation(tmp_path: Path) -> None:
    source = tmp_path / "dataset.csv"
    _dataset(source)

    report = validate_tabular_resource(
        source,
        resource_id="resource-underspecified",
        required_columns=["case_id", "feature", "label"],
        unique_key="case_id",
        target_column="label",
        feature_columns=["feature"],
        semantic_contract={"label_rule": "gold field"},
    )

    assert report.status is ResourceLifecycleStatus.SCHEMA_VALIDATED


def test_execution_gate_requires_resources_and_algorithm_entrypoints(tmp_path: Path) -> None:
    source = tmp_path / "dataset.csv"
    _dataset(source)
    report = validate_tabular_resource(
        source,
        resource_id="resource-dataset",
        required_columns=["case_id", "feature", "label"],
        unique_key="case_id",
        target_column="label",
        feature_columns=["feature"],
        semantic_contract=SEMANTIC_CONTRACT,
    )
    entrypoint = tmp_path / "run.py"
    entrypoint.write_text("print('ok')\n", encoding="utf-8")

    ready = assess_execution_readiness(
        study_id="study-1",
        contract_version=1,
        compile_passed=True,
        dry_run_passed=True,
        required_resource_ids=["resource-dataset"],
        resource_records=[report.record],
        required_algorithm_ids=["algorithm-1"],
        resolved_algorithm_entrypoints={"algorithm-1": str(entrypoint)},
    )
    blocked = assess_execution_readiness(
        study_id="study-1",
        contract_version=1,
        compile_passed=True,
        dry_run_passed=True,
        required_resource_ids=["resource-missing"],
        resource_records=[report.record],
        required_algorithm_ids=["algorithm-1"],
        resolved_algorithm_entrypoints={},
    )

    assert ready.ready
    assert not blocked.ready
    assert set(blocked.blocking_issues) == {
        "resources_semantically_validated",
        "algorithm_entrypoints_materialized",
    }


def _gx_contract(source: Path, *, unique: bool = True) -> FrozenDataQualityContract:
    expectations = [
        FrozenExpectation(
            expectation_id="columns",
            kind="columns_match_set",
            columns=[
                "case_id",
                "feature",
                "label",
                "feature_time",
                "decision_time",
            ],
        ),
        FrozenExpectation(
            expectation_id="id-not-null",
            kind="not_null",
            column="case_id",
        ),
    ]
    if unique:
        expectations.append(
            FrozenExpectation(
                expectation_id="id-unique", kind="unique", column="case_id"
            )
        )
    return FrozenDataQualityContract(
        contract_id="resource-quality-v1",
        dataset_sha256=sha256_file(source),
        expectations=expectations,
    )


def test_hash_bound_gx_gate_enters_resource_lifecycle(tmp_path: Path) -> None:
    source = tmp_path / "dataset.csv"
    _dataset(source)
    gx_report = validate_frozen_tabular_resource(
        dataset_path=source,
        contract=_gx_contract(source),
        report_output=tmp_path / "evidence" / "gx.json",
    )

    report = validate_tabular_resource(
        source,
        resource_id="resource-gx-qualified",
        required_columns=["case_id", "feature", "label"],
        unique_key="case_id",
        target_column="label",
        feature_columns=["feature"],
        decision_time_column="decision_time",
        feature_time_column="feature_time",
        semantic_contract=SEMANTIC_CONTRACT,
        external_schema_report=gx_report,
    )

    assert report.status is ResourceLifecycleStatus.SEMANTICALLY_VALIDATED
    assert report.record.external_schema_validator == "great_expectations"
    assert (
        report.record.external_schema_report_sha256
        == gx_report["report_subject_sha256"]
    )


def test_failed_or_mismatched_gx_gate_blocks_schema_qualification(tmp_path: Path) -> None:
    source = tmp_path / "dataset.csv"
    _dataset(source)
    gx_report = validate_frozen_tabular_resource(
        dataset_path=source,
        contract=_gx_contract(source),
        report_output=tmp_path / "evidence" / "gx.json",
    )
    gx_report["dataset_sha256"] = "0" * 64

    report = validate_tabular_resource(
        source,
        resource_id="resource-gx-mismatch",
        required_columns=["case_id", "feature", "label"],
        unique_key="case_id",
        target_column="label",
        feature_columns=["feature"],
        semantic_contract=SEMANTIC_CONTRACT,
        external_schema_report=gx_report,
    )

    assert report.status is ResourceLifecycleStatus.MATERIALIZED
    assert any(
        item.check_id == "great_expectations_frozen_structure_gate"
        and not item.passed
        for item in report.checks
    )
