from __future__ import annotations

import pytest

from research_forge.stage_three_generation import (
    GeneratedProfileV1Package,
    GeneratedSourceFile,
)
from research_forge.stage_three_profiles import profile_definition
from research_forge.workflow_domain import Stage3Profile


def _file(path: str, role: str) -> GeneratedSourceFile:
    if role == "dataset":
        content = (
            '{"sample_id":"formal-1","features":{"x":1},'
            '"target_reference":"formal-target-1"}\n'
        )
    elif role == "target_dataset":
        content = (
            '{"target_reference":"formal-target-1","target":1}\n'
        )
    elif role == "smoke_dataset":
        content = (
            '{"sample_id":"smoke-1","features":{"x":0},'
            '"target_reference":"smoke-target-1"}\n'
        )
    elif role == "smoke_target_dataset":
        content = (
            '{"target_reference":"smoke-target-1","target":0}\n'
        )
    else:
        content = "# generated fixture\n"
    return GeneratedSourceFile(
        path=path,
        role=role,
        content=content,
        source_basis=["frozen blueprint field"],
    )


def test_generated_profile_package_requires_independent_evaluator_and_data_split() -> None:
    package = GeneratedProfileV1Package(
        files=[
            _file("data/formal.jsonl", "dataset"),
            _file("data/formal-targets.jsonl", "target_dataset"),
            _file("data/smoke.jsonl", "smoke_dataset"),
            _file("data/smoke-targets.jsonl", "smoke_target_dataset"),
            _file("baseline.py", "baseline"),
            _file("treatment.py", "treatment"),
            _file("evaluator.py", "evaluator"),
        ],
        formal_data_path="data/formal.jsonl",
        formal_target_path="data/formal-targets.jsonl",
        smoke_data_path="data/smoke.jsonl",
        smoke_target_path="data/smoke-targets.jsonl",
        baseline_command=[
            "{python}", "baseline.py", "{data_file}", "{prediction_file}"
        ],
        treatment_command=[
            "{python}", "treatment.py", "{data_file}", "{prediction_file}"
        ],
        evaluator_command=["{python}", "evaluator.py"],
        expected_raw_fields=[
            "sample_id",
            "prediction",
            "target_reference",
        ],
        expected_metric_fields=["accuracy", "denominator", "sample_ids"],
        conformance_claims=["Only the registered intervention differs."],
    )

    assert package.formal_data_path != package.smoke_data_path
    assert {item.role for item in package.files}.issuperset(
        {"baseline", "treatment", "evaluator"}
    )


def test_generated_paths_cannot_escape_materialization_root() -> None:
    with pytest.raises(ValueError, match="safe and relative"):
        _file("../outside.py", "baseline")


def test_candidate_partition_cannot_embed_target_values() -> None:
    leaked = _file("data/formal.jsonl", "dataset").model_copy(
        update={
            "content": (
                '{"sample_id":"formal-1","features":{"x":1},'
                '"target_reference":"formal-target-1","target":1}\n'
            )
        }
    )
    with pytest.raises(ValueError, match="expose target fields"):
        GeneratedProfileV1Package(
            files=[
                leaked,
                _file("data/formal-targets.jsonl", "target_dataset"),
                _file("data/smoke.jsonl", "smoke_dataset"),
                _file(
                    "data/smoke-targets.jsonl",
                    "smoke_target_dataset",
                ),
                _file("baseline.py", "baseline"),
                _file("treatment.py", "treatment"),
                _file("evaluator.py", "evaluator"),
            ],
            formal_data_path="data/formal.jsonl",
            formal_target_path="data/formal-targets.jsonl",
            smoke_data_path="data/smoke.jsonl",
            smoke_target_path="data/smoke-targets.jsonl",
            baseline_command=[
                "{python}",
                "baseline.py",
                "{data_file}",
                "{prediction_file}",
            ],
            treatment_command=[
                "{python}",
                "treatment.py",
                "{data_file}",
                "{prediction_file}",
            ],
            evaluator_command=["{python}", "evaluator.py"],
            expected_raw_fields=[
                "sample_id",
                "prediction",
                "target_reference",
            ],
            expected_metric_fields=[
                "accuracy",
                "denominator",
                "sample_ids",
            ],
            conformance_claims=["Registered delta only."],
        )


def test_builder_must_abstain_instead_of_hiding_missing_requirements() -> None:
    package = GeneratedProfileV1Package(
        files=[
            _file("data/formal.jsonl", "dataset"),
            _file("data/formal-targets.jsonl", "target_dataset"),
            _file("data/smoke.jsonl", "smoke_dataset"),
            _file("data/smoke-targets.jsonl", "smoke_target_dataset"),
            _file("baseline.py", "baseline"),
            _file("treatment.py", "treatment"),
            _file("evaluator.py", "evaluator"),
        ],
        formal_data_path="data/formal.jsonl",
        formal_target_path="data/formal-targets.jsonl",
        smoke_data_path="data/smoke.jsonl",
        smoke_target_path="data/smoke-targets.jsonl",
        baseline_command=[
            "{python}", "baseline.py", "{data_file}", "{prediction_file}"
        ],
        treatment_command=[
            "{python}", "treatment.py", "{data_file}", "{prediction_file}"
        ],
        evaluator_command=["{python}", "evaluator.py"],
        expected_raw_fields=[
            "sample_id",
            "prediction",
            "target_reference",
        ],
        expected_metric_fields=["score", "denominator", "sample_ids"],
        conformance_claims=["No unregistered arm difference was added."],
        unresolved_requirements=["Formal labels are not available."],
    )

    assert package.unresolved_requirements


def test_profile_registry_declares_builder_evaluator_and_repair_policy() -> None:
    profile = profile_definition(
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1
    )

    assert "estimand" in profile.required_contract_fields
    assert "computational_paired_comparison_codex_v1" in profile.builder_plugins
    assert profile.evaluator_plugin == "independent_sample_metric_v1"
    assert profile.repair_policy == "bounded_successor_v1"
