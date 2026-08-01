from __future__ import annotations

import pytest

from research_forge.evaluator_comparison import (
    EvaluatorAgreementStatus,
    EvaluatorFamilyResult,
    EvaluatorObservation,
    compare_evaluator_families,
    validate_evaluator_robustness_for_completion,
)
from research_forge.workflow_domain import (
    EntryMode,
    HypothesisVerdictStatus,
    WorkflowRepository,
)


def _result(
    evaluator_id: str,
    family_id: str,
    scores: list[float],
    *,
    decision: HypothesisVerdictStatus = HypothesisVerdictStatus.SUPPORTED,
) -> EvaluatorFamilyResult:
    return EvaluatorFamilyResult(
        evaluator_id=evaluator_id,
        family_id=family_id,
        implementation_digest=("a" if family_id == "reference" else "b") * 64,
        metric_name="accuracy",
        aggregate_score=sum(scores) / len(scores),
        direction="higher_is_better",
        decision=decision,
        observations=[
            EvaluatorObservation(item_id=f"row-{index}", score=value)
            for index, value in enumerate(scores)
        ],
    )


def test_distinct_evaluator_families_can_confirm_stability() -> None:
    report = compare_evaluator_families(
        study_id="study-evaluator",
        primary=_result("official", "reference", [1.0, 0.0, 1.0]),
        secondary=_result("rewrite", "independent_python", [1.0, 0.0, 1.0]),
    )

    assert report.status is EvaluatorAgreementStatus.STABLE
    assert report.verdict_stable
    assert not report.requires_adjudication


def test_row_level_metric_disagreement_is_preserved() -> None:
    report = compare_evaluator_families(
        study_id="study-evaluator",
        primary=_result("official", "reference", [1.0, 0.0, 1.0]),
        secondary=_result("rewrite", "independent_python", [1.0, 1.0, 1.0]),
        aggregate_tolerance=0.01,
    )

    assert report.status is EvaluatorAgreementStatus.METRIC_DISAGREEMENT
    assert [item.item_id for item in report.row_level_disagreements] == ["row-1"]
    assert report.requires_adjudication


def test_directional_verdict_disagreement_blocks_stability() -> None:
    report = compare_evaluator_families(
        study_id="study-evaluator",
        primary=_result("official", "reference", [1.0, 0.0]),
        secondary=_result(
            "rewrite",
            "independent_python",
            [1.0, 0.0],
            decision=HypothesisVerdictStatus.INCONCLUSIVE,
        ),
    )

    assert report.status is EvaluatorAgreementStatus.DIRECTIONAL_DISAGREEMENT
    assert not report.directional_conclusion_agreement
    assert not report.verdict_stable


def test_same_family_cannot_masquerade_as_secondary_evaluator() -> None:
    with pytest.raises(ValueError, match="distinct family_id"):
        compare_evaluator_families(
            study_id="study-evaluator",
            primary=_result("official-v1", "reference", [1.0]),
            secondary=_result("official-v2", "reference", [1.0]),
        )


def test_required_secondary_evaluator_blocks_completion_until_stable() -> None:
    policy = {"uses_learned_evaluator": True, "secondary_required": True}
    assert "EVALUATOR_COMPARISON_MISSING" in validate_evaluator_robustness_for_completion(
        policy, []
    )[0]

    unstable = compare_evaluator_families(
        study_id="study-evaluator",
        primary=_result("official", "reference", [1.0, 0.0]),
        secondary=_result("rewrite", "independent_python", [1.0, 1.0]),
    )
    assert "EVALUATOR_DISAGREEMENT_UNRESOLVED" in validate_evaluator_robustness_for_completion(
        policy, [unstable]
    )[0]

    stable = compare_evaluator_families(
        study_id="study-evaluator",
        primary=_result("official", "reference", [1.0, 0.0]),
        secondary=_result("rewrite", "independent_python", [1.0, 0.0]),
    )
    assert validate_evaluator_robustness_for_completion(policy, [stable]) == []


def test_evaluator_disagreement_report_is_append_only(tmp_path) -> None:
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project("Evaluator project", source_root="C:/fixture")
    study = repository.create_study(
        project.project_id,
        "Evaluator study",
        entry_mode=EntryMode.IDEA_TO_PAPER,
    )
    report = compare_evaluator_families(
        study_id=study.study_id,
        primary=_result("official", "reference", [1.0, 0.0]),
        secondary=_result("rewrite", "independent_python", [1.0, 0.0]),
    )

    repository.save_evaluator_disagreement_report(report)
    assert repository.list_evaluator_disagreement_reports(study.study_id) == [report]
    with pytest.raises(ValueError, match="append-only"):
        repository.save_evaluator_disagreement_report(
            report.model_copy(update={"requires_adjudication": True})
        )
