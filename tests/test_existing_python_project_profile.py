from __future__ import annotations

import json
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
from research_forge.profiles.contracts import (
    ExistingPythonProjectParameters,
)
from research_forge.profiles.existing_python_project import (
    collect_frozen_project_files,
    parse_existing_project_result,
    validate_existing_python_project_contract,
)
from research_forge.workflow_domain import (
    ArtifactStatus,
    Hypothesis,
    HypothesisRole,
    ResearchContractVersion,
    Stage3Profile,
)


def _parameters() -> ExistingPythonProjectParameters:
    return ExistingPythonProjectParameters.model_validate(
        {
            "container_image": "python:3.12-slim",
            "project_code_paths": ["project.py", "requirements.lock"],
            "requirements_lock_path": "requirements.lock",
            "candidate_data_path": "data/formal.json",
            "evaluator_target_path": "evaluator/targets.json",
            "baseline_command": [
                "python",
                "/workspace/input/project.py",
                "--mode",
                "baseline",
                "--input",
                "{data_file}",
                "--output",
                "{prediction_file}",
            ],
            "treatment_command": [
                "python",
                "/workspace/input/project.py",
                "--mode",
                "treatment",
                "--input",
                "{data_file}",
                "--output",
                "{prediction_file}",
            ],
            "evaluator_command": [
                "python",
                "/workspace/input/evaluator.py",
                "--prediction",
                "{prediction_file}",
                "--target",
                "{target_file}",
                "--output",
                "{metrics_file}",
            ],
            "prediction_artifact_path": "predictions.json",
            "result_artifact_path": "result.json",
            "metric_names": ["accuracy"],
            "primary_metric": "accuracy",
        }
    )


def _experiment(arm: str) -> ExperimentSpec:
    parameters = _parameters()
    return ExperimentSpec(
        experiment_id=f"existing-{arm}",
        action_ids=[f"action-existing-{arm}"],
        title=arm,
        command=(
            parameters.baseline_command
            if arm == "baseline"
            else parameters.treatment_command
        ),
        smoke_command=["python", "smoke.py"],
        smoke_required_inputs=["smoke.py", "smoke/data.json"],
        execution_backend="isolated_candidate_evaluator",
        container_image=parameters.container_image,
        required_inputs=[parameters.candidate_data_path],
        candidate_code_paths=parameters.project_code_paths,
        evaluator_command=parameters.evaluator_command,
        evaluator_code_paths=["evaluator.py"],
        evaluator_required_inputs=[parameters.evaluator_target_path],
        prediction_artifact_path=parameters.prediction_artifact_path,
        artifacts=[
            ExperimentArtifactSpec(
                path=parameters.result_artifact_path,
                format="json",
                required_keys=[
                    "accuracy",
                    "denominator",
                    "sample_ids",
                    "analysis_rows",
                ],
            )
        ],
    )


def _contract() -> ResearchContractVersion:
    parameters = _parameters()
    return ResearchContractVersion(
        study_id="study-existing-project",
        version=1,
        scope_version=1,
        status=ArtifactStatus.FROZEN,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hypothesis-primary-existing",
                statement="The treatment reproduces a higher held-out accuracy.",
                role=HypothesisRole.PRIMARY,
                decision_rule={"metric": "accuracy"},
            )
        ],
        data_boundary={"population": "frozen project test cases"},
        metrics=[
            {
                "name": "accuracy",
                "direction": "higher_is_better",
                "formula": "correct predictions / frozen test cases",
                "denominator": "all frozen test cases",
            }
        ],
        baseline={
            "experiment_id": "existing-baseline",
            "action_id": "action-existing-baseline",
        },
        treatment={
            "experiment_id": "existing-treatment",
            "action_id": "action-existing-treatment",
        },
        tasks=["existing-project-replay"],
        seeds=[7],
        runtime_binding={"container": parameters.container_image},
        evaluator_policy={"authority": "isolated_evaluator"},
        experiment_profile=Stage3Profile.EXISTING_PYTHON_PROJECT_V1,
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
        environment_requirements={
            "container_image": parameters.container_image,
            "requirements_lock": parameters.requirements_lock_path,
        },
    )


def test_existing_python_project_bundle_is_certified() -> None:
    bundle = profile_bundle(Stage3Profile.EXISTING_PYTHON_PROJECT_V1)
    assert bundle.certification_status is ProfileCertificationStatus.CERTIFIED
    assert bundle.reproduction_comparator_id == "existing_python_clean_replay_v1"


def test_existing_project_contract_binds_commands_environment_and_targets() -> None:
    manifest = ExperimentManifest(
        experiments=[_experiment("baseline"), _experiment("treatment")]
    )
    assert validate_existing_python_project_contract(_contract(), manifest) == []

    unsafe = _experiment("treatment").model_copy(
        update={
            "execution_backend": "controlled_local",
            "network_access": True,
        }
    )
    violations = validate_existing_python_project_contract(
        _contract(),
        ExperimentManifest(
            experiments=[_experiment("baseline"), unsafe]
        ),
    )
    assert any("isolated container" in item for item in violations)
    assert any("offline" in item for item in violations)


def test_result_parser_and_file_collector_are_hash_bound(tmp_path: Path) -> None:
    (tmp_path / "project.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "requirements.lock").write_text("# frozen\n", encoding="utf-8")
    records = collect_frozen_project_files(
        tmp_path, ["project.py", "requirements.lock"]
    )
    assert len(records) == 2
    assert all(len(item["sha256"]) == 64 for item in records)

    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "accuracy": 0.75,
                "denominator": 4,
                "sample_ids": ["a", "b", "c", "d"],
                "analysis_rows": [
                    {"pair_id": item, "value": value}
                    for item, value in zip(
                        ["a", "b", "c", "d"], [1, 1, 1, 0]
                    )
                ],
            }
        ),
        encoding="utf-8",
    )
    parsed = parse_existing_project_result(result_path, _parameters())
    assert parsed["valid"] is True
    assert parsed["primary_metric_value"] == pytest.approx(0.75)
    assert len(parsed["result_sha256"]) == 64

    with pytest.raises(ValueError):
        collect_frozen_project_files(tmp_path, ["../outside.py"])
