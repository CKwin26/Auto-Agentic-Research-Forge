from __future__ import annotations

import pytest

from research_forge.workflow_domain import (
    Hypothesis,
    HypothesisRole,
    HypothesisVerdict,
    HypothesisVerdictStatus,
    StudyVerdictStatus,
    aggregate_study_verdict,
)


def _primary(suffix: str) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=f"hypothesis-primary-{suffix}",
        statement=f"Primary hypothesis {suffix} satisfies its frozen rule.",
        role=HypothesisRole.PRIMARY,
        decision_rule={"metric": "registered_metric", "threshold": 0.5},
    )


def _verdict(
    hypothesis: Hypothesis,
    status: HypothesisVerdictStatus,
    suffix: str,
) -> HypothesisVerdict:
    return HypothesisVerdict(
        verdict_id="hverdict-" + suffix * 16,
        study_id="study-capability-verdict-fixture",
        hypothesis_id=hypothesis.hypothesis_id,
        status=status,
        evidence_chain_ids=["chain-" + suffix * 16],
        eligible_evidence=True,
        rationale="Issued from an eligible verified evidence chain and frozen rule.",
    )


@pytest.mark.parametrize(
    ("hypothesis_status", "expected_study_status", "suffix"),
    [
        (
            HypothesisVerdictStatus.SUPPORTED,
            StudyVerdictStatus.SUPPORTED,
            "1",
        ),
        (
            HypothesisVerdictStatus.REFUTED,
            StudyVerdictStatus.REFUTED,
            "2",
        ),
        (
            HypothesisVerdictStatus.INCONCLUSIVE,
            StudyVerdictStatus.INCONCLUSIVE,
            "3",
        ),
    ],
    ids=["supported", "refuted", "inconclusive"],
)
def test_single_primary_verdict_fixture(
    hypothesis_status: HypothesisVerdictStatus,
    expected_study_status: StudyVerdictStatus,
    suffix: str,
) -> None:
    hypothesis = _primary(suffix)
    result = aggregate_study_verdict(
        "study-capability-verdict-fixture",
        [hypothesis],
        [_verdict(hypothesis, hypothesis_status, suffix)],
    )

    assert result.status is expected_study_status


def test_mixed_primary_verdict_fixture() -> None:
    supported = _primary("a")
    refuted = _primary("b")

    result = aggregate_study_verdict(
        "study-capability-verdict-fixture",
        [supported, refuted],
        [
            _verdict(supported, HypothesisVerdictStatus.SUPPORTED, "a"),
            _verdict(refuted, HypothesisVerdictStatus.REFUTED, "b"),
        ],
    )

    assert result.status is StudyVerdictStatus.MIXED


def test_execution_failure_is_unverifiable_not_refuted() -> None:
    hypothesis = _primary("c")
    verdict = HypothesisVerdict(
        verdict_id="hverdict-" + "c" * 16,
        study_id="study-capability-verdict-fixture",
        hypothesis_id=hypothesis.hypothesis_id,
        status=HypothesisVerdictStatus.UNVERIFIABLE,
        evidence_chain_ids=[],
        eligible_evidence=False,
        rationale="Execution failed before an eligible verified evidence chain existed.",
    )

    result = aggregate_study_verdict(
        "study-capability-verdict-fixture", [hypothesis], [verdict]
    )

    assert result.status is StudyVerdictStatus.UNVERIFIABLE
    assert result.status is not StudyVerdictStatus.REFUTED
