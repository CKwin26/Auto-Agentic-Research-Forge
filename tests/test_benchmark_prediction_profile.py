from __future__ import annotations

import csv
from pathlib import Path

import pytest

from research_forge.experiment_execution import (
    ExperimentArtifactSpec,
    ExperimentManifest,
    ExperimentSpec,
)
from research_forge.profiles import (
    ProfileCertificationStatus,
    profile_bundle,
)
from research_forge.profiles.benchmark_prediction import (
    evaluate_csv_submission,
    validate_benchmark_prediction_contract,
)
from research_forge.profiles.contracts import (
    BenchmarkPredictionParameters,
)
from research_forge.workflow_domain import (
    ArtifactStatus,
    Hypothesis,
    HypothesisRole,
    ResearchContractVersion,
    Stage3Profile,
)


def _parameters() -> BenchmarkPredictionParameters:
    return BenchmarkPredictionParameters.model_validate(
        {
            "task_type": "classification",
            "candidate_input_path": "task/test.csv",
            "evaluator_target_path": "evaluator/targets.csv",
            "id_field": "id",
            "prediction_field": "prediction",
            "target_field": "label",
            "score_field": "score",
            "primary_metric": "accuracy",
            "metrics": ["accuracy", "f1", "auroc"],
            "positive_label": "1",
            "prediction_artifact_path": "submission.csv",
        }
    )


def _experiment(arm: str) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id=f"benchmark-{arm}",
        action_ids=[f"action-benchmark-{arm}"],
        title=arm,
        command=["python", f"candidate/{arm}.py"],
        smoke_command=["python", "smoke.py"],
        smoke_required_inputs=["smoke.py", "smoke/test.csv"],
        execution_backend="isolated_candidate_evaluator",
        container_image="python:3.12-slim",
        required_inputs=["task/test.csv"],
        candidate_code_paths=[f"candidate/{arm}.py"],
        evaluator_command=["python", "evaluator/evaluate.py"],
        evaluator_code_paths=["evaluator/evaluate.py"],
        evaluator_required_inputs=["evaluator/targets.csv"],
        prediction_artifact_path="submission.csv",
        artifacts=[
            ExperimentArtifactSpec(
                path="result.json",
                format="json",
                required_keys=["accuracy", "denominator", "sample_ids"],
            )
        ],
    )


def _contract() -> ResearchContractVersion:
    parameters = _parameters()
    return ResearchContractVersion(
        study_id="study-benchmark-profile",
        version=1,
        scope_version=1,
        status=ArtifactStatus.FROZEN,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hypothesis-primary-benchmark",
                statement="The treatment improves held-out accuracy.",
                role=HypothesisRole.PRIMARY,
                decision_rule={"metric": "accuracy"},
            )
        ],
        data_boundary={"population": "frozen benchmark test IDs"},
        metrics=[
            {
                "name": name,
                "direction": "higher_is_better",
                "formula": f"frozen binary {name} formula",
                "denominator": "all frozen test IDs",
            }
            for name in parameters.metrics
        ],
        baseline={
            "experiment_id": "benchmark-baseline",
            "action_id": "action-benchmark-baseline",
        },
        treatment={
            "experiment_id": "benchmark-treatment",
            "action_id": "action-benchmark-treatment",
        },
        tasks=["benchmark-task"],
        seeds=[11, 29],
        runtime_binding={"container": "python:3.12-slim"},
        evaluator_policy={"authority": "isolated_evaluator"},
        experiment_profile=Stage3Profile.BENCHMARK_PREDICTION_V1,
        profile_parameters=parameters.model_dump(mode="json"),
        output_schema={
            "format": "json",
            "record_layout": "summary_with_analysis_rows",
            "metric_field": "accuracy",
            "denominator_field": "denominator",
            "sample_id_field": "sample_ids",
            "analysis_rows_field": "analysis_rows",
        },
        statistical_rules={
            "method": "paired_run_difference",
            "effect_threshold": 0.0,
            "missing_cell_policy": "inconclusive",
        },
    )


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_benchmark_prediction_bundle_is_certified() -> None:
    bundle = profile_bundle(Stage3Profile.BENCHMARK_PREDICTION_V1)
    assert bundle.certification_status is ProfileCertificationStatus.CERTIFIED
    assert bundle.evaluator_id == "isolated_prediction_evaluator_v1"


def test_contract_accepts_only_isolated_target_boundary() -> None:
    manifest = ExperimentManifest(
        experiments=[_experiment("baseline"), _experiment("treatment")]
    )
    assert validate_benchmark_prediction_contract(_contract(), manifest) == []

    leaked = _experiment("baseline").model_copy(
        update={
            "required_inputs": ["evaluator/targets.csv"],
        }
    )
    violations = validate_benchmark_prediction_contract(
        _contract(),
        ExperimentManifest(
            experiments=[leaked, _experiment("treatment")]
        ),
    )
    assert any("candidate input" in item for item in violations)


def test_reference_evaluator_requires_exact_ids_and_recomputes_metrics(
    tmp_path: Path,
) -> None:
    target = tmp_path / "targets.csv"
    submission = tmp_path / "submission.csv"
    _write_csv(
        target,
        ["id", "label"],
        [
            {"id": "a", "label": 0},
            {"id": "b", "label": 1},
            {"id": "c", "label": 0},
            {"id": "d", "label": 1},
        ],
    )
    _write_csv(
        submission,
        ["id", "prediction", "score"],
        [
            {"id": "a", "prediction": 0, "score": 0.1},
            {"id": "b", "prediction": 1, "score": 0.9},
            {"id": "c", "prediction": 0, "score": 0.2},
            {"id": "d", "prediction": 1, "score": 0.8},
        ],
    )
    result = evaluate_csv_submission(
        _parameters(), submission_path=submission, target_path=target
    )
    assert result["accuracy"] == pytest.approx(1.0)
    assert result["auroc"] == pytest.approx(1.0)
    assert result["denominator"] == 4

    _write_csv(
        submission,
        ["id", "prediction", "score"],
        [{"id": "a", "prediction": 0, "score": 0.1}],
    )
    with pytest.raises(ValueError, match="exactly and uniquely"):
        evaluate_csv_submission(
            _parameters(), submission_path=submission, target_path=target
        )
