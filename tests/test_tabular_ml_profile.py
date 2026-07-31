from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from research_forge.profiles import (
    ProfileCertificationStatus,
    profile_bundle,
    resolve_profile_capability,
)
from research_forge.profiles.contracts import TabularMLParameters
from research_forge.profiles.tabular_ml import (
    TabularMLRunResult,
    materialize_isolated_tabular_ml_package,
    materialize_tabular_ml_package,
    run_tabular_ml,
    validate_tabular_ml_result,
)
from research_forge.workflow_domain import (
    ProfileCapabilityStatus,
    Stage3Profile,
)


MODULE = "research_forge.profiles.tabular_ml"


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _classification_spec() -> TabularMLParameters:
    return TabularMLParameters.model_validate(
        {
            "task_type": "classification",
            "dataset_path": "data.csv",
            "id_field": "sample_id",
            "target_field": "label",
            "feature_fields": ["x1", "x2"],
            "split_strategy": "fixed_split",
            "split_field": "partition",
            "train_values": ["train"],
            "test_values": ["test"],
            "primary_metric": "accuracy",
            "metrics": ["accuracy", "f1", "auroc"],
            "positive_label": "1",
            "baseline_estimator": {
                "import_path": f"{MODULE}:MajorityClassifier"
            },
            "treatment_estimator": {
                "import_path": f"{MODULE}:NearestCentroidClassifier"
            },
        }
    )


def _regression_spec() -> TabularMLParameters:
    return TabularMLParameters.model_validate(
        {
            "task_type": "regression",
            "dataset_path": "data.csv",
            "id_field": "sample_id",
            "target_field": "target",
            "feature_fields": ["x"],
            "split_strategy": "kfold_cv",
            "folds": 4,
            "primary_metric": "rmse",
            "metrics": ["rmse", "mae"],
            "baseline_estimator": {
                "import_path": f"{MODULE}:MeanRegressor"
            },
            "treatment_estimator": {
                "import_path": f"{MODULE}:LinearLeastSquaresRegressor"
            },
        }
    )


def test_tabular_ml_bundle_is_certified_with_isolated_fixed_split_scope() -> None:
    bundle = profile_bundle(Stage3Profile.TABULAR_ML_V1)
    assert bundle.certification_status is ProfileCertificationStatus.CERTIFIED
    assert bundle.contract_schema_id == "tabular_ml_contract_v1"
    assert resolve_profile_capability(
        Stage3Profile.TABULAR_ML_V1,
        runnable_assets_present=True,
    ) is ProfileCapabilityStatus.SUPPORTED


def test_tabular_contract_rejects_target_leakage_and_identical_arms() -> None:
    payload = _classification_spec().model_dump(mode="json")
    with pytest.raises(ValidationError, match="identifier, target, or split"):
        TabularMLParameters.model_validate(
            {**payload, "feature_fields": ["x1", "label"]}
        )
    with pytest.raises(ValidationError, match="estimators must differ"):
        TabularMLParameters.model_validate(
            {
                **payload,
                "treatment_estimator": payload["baseline_estimator"],
            }
        )


def test_fixed_split_classification_runs_and_recomputes_metrics(
    tmp_path: Path,
) -> None:
    rows: list[dict[str, object]] = []
    for i, (x1, x2, label) in enumerate(
        [
            (-3, -2, 0),
            (-2, -3, 0),
            (-2, -1, 0),
            (2, 1, 1),
            (3, 2, 1),
            (1, 3, 1),
            (-4, -2, 0),
            (-1, -2, 0),
            (2, 2, 1),
            (4, 3, 1),
        ]
    ):
        rows.append(
            {
                "sample_id": f"s{i}",
                "x1": x1,
                "x2": x2,
                "label": label,
                "partition": "train" if i < 6 else "test",
            }
        )
    _write_rows(tmp_path / "data.csv", rows)
    spec = _classification_spec()

    baseline = run_tabular_ml(spec, arm="baseline", seed=7, root=tmp_path)
    treatment = run_tabular_ml(spec, arm="treatment", seed=7, root=tmp_path)

    assert baseline.denominator == treatment.denominator == 4
    assert baseline.sample_ids == treatment.sample_ids
    assert treatment.accuracy == pytest.approx(1.0)
    assert treatment.accuracy > baseline.accuracy
    assert treatment.auroc == pytest.approx(1.0)
    assert validate_tabular_ml_result(treatment, spec)["valid"] is True

    tampered = treatment.model_dump(mode="json")
    tampered["metrics"]["accuracy"] = 0.25
    tampered["accuracy"] = 0.25
    tampered["primary_metric_value"] = 0.25
    parsed = TabularMLRunResult.model_validate(tampered)
    audit = validate_tabular_ml_result(parsed, spec)
    assert audit["valid"] is False
    assert "accuracy" in audit["mismatches"]


def test_kfold_regression_runs_all_rows_and_preserves_pairing(
    tmp_path: Path,
) -> None:
    _write_rows(
        tmp_path / "data.csv",
        [
            {"sample_id": f"r{i}", "x": i, "target": 2 * i + 1}
            for i in range(20)
        ],
    )
    spec = _regression_spec()
    baseline = run_tabular_ml(spec, arm="baseline", seed=19, root=tmp_path)
    treatment = run_tabular_ml(spec, arm="treatment", seed=19, root=tmp_path)

    assert baseline.denominator == treatment.denominator == 20
    assert baseline.sample_ids == treatment.sample_ids
    assert treatment.rmse == pytest.approx(0.0, abs=1e-10)
    assert treatment.mae == pytest.approx(0.0, abs=1e-10)
    assert treatment.rmse < baseline.rmse
    assert validate_tabular_ml_result(treatment, spec)["valid"] is True


def test_materialized_package_freezes_dataset_and_two_arm_commands(
    tmp_path: Path,
) -> None:
    _write_rows(
        tmp_path / "data.csv",
        [
            {
                "sample_id": "a",
                "x1": 0,
                "x2": 0,
                "label": 0,
                "partition": "train",
            },
            {
                "sample_id": "b",
                "x1": 1,
                "x2": 1,
                "label": 1,
                "partition": "test",
            },
        ],
    )
    config, manifest = materialize_tabular_ml_package(
        _classification_spec(), tmp_path
    )
    assert config.is_file() and manifest.is_file()
    payload = manifest.read_text(encoding="utf-8")
    assert "action-tabular-ml-baseline" in payload
    assert "action-tabular-ml-treatment" in payload
    assert "data.csv" in payload
    assert "{evidence_dir}/result.json" in payload


def test_isolated_package_hides_formal_targets_and_recomputes_submission(
    tmp_path: Path,
) -> None:
    rows: list[dict[str, object]] = []
    for i, (x1, x2, label) in enumerate(
        [
            (-3, -2, 0),
            (-2, -3, 0),
            (-2, -1, 0),
            (2, 1, 1),
            (3, 2, 1),
            (1, 3, 1),
            (-4, -2, 0),
            (-1, -2, 0),
            (2, 2, 1),
            (4, 3, 1),
        ]
    ):
        rows.append(
            {
                "sample_id": f"s{i}",
                "x1": x1,
                "x2": x2,
                "label": label,
                "partition": "train" if i < 6 else "test",
            }
        )
    _write_rows(tmp_path / "data.csv", rows)
    package, manifest_path = materialize_isolated_tabular_ml_package(
        _classification_spec(), tmp_path
    )
    candidate_data = json.loads(
        (package / "candidate-data.json").read_text(encoding="utf-8")
    )
    assert all("target" in row for row in candidate_data["train"])
    assert all("target" not in row for row in candidate_data["test"])

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    treatment = manifest["experiments"][1]
    assert treatment["execution_backend"] == "isolated_candidate_evaluator"
    assert not set(treatment["required_inputs"]).intersection(
        treatment["evaluator_required_inputs"]
    )
    assert treatment["network_access"] is False

    prediction = tmp_path / "predictions.json"
    result = tmp_path / "result.json"
    subprocess.run(
        [
            sys.executable,
            str(package / "candidate.py"),
            "--data",
            str(package / "candidate-data.json"),
            "--algorithm",
            "nearest_centroid_classifier",
            "--prediction",
            str(prediction),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(package / "evaluator.py"),
            "--prediction",
            str(prediction),
            "--target",
            str(package / "evaluator-targets.json"),
            "--metrics",
            str(result),
        ],
        check=True,
    )
    evaluated = json.loads(result.read_text(encoding="utf-8"))
    assert evaluated["accuracy"] == pytest.approx(1.0)
    assert evaluated["auroc"] == pytest.approx(1.0)
    assert evaluated["denominator"] == 4


def test_tabular_profile_blocks_missing_columns(tmp_path: Path) -> None:
    _write_rows(
        tmp_path / "data.csv",
        [{"sample_id": "a", "x1": 1, "label": 0, "partition": "train"}],
    )
    with pytest.raises(ValueError, match="missing columns"):
        run_tabular_ml(
            _classification_spec(), arm="baseline", seed=1, root=tmp_path
        )
