from __future__ import annotations

"""Deterministic comparison of distinct evaluator families.

The report has no authority to rewrite an EvaluationRecord.  It records
measurement robustness and forces unstable conclusions into review.
"""

from enum import StrEnum
from typing import Literal
from typing import Any, Iterable

from pydantic import Field, model_validator

from .models import StrictModel, utc_now
from .workflow_domain import HypothesisVerdictStatus, stable_id


class EvaluatorAgreementStatus(StrEnum):
    STABLE = "stable"
    METRIC_DISAGREEMENT = "metric_disagreement"
    DIRECTIONAL_DISAGREEMENT = "directional_disagreement"
    INCOMPLETE = "incomplete"


class EvaluatorAdjudicationDecision(StrEnum):
    ACCEPT_PRIMARY = "accept_primary"
    ACCEPT_SECONDARY = "accept_secondary"
    ABSTAIN = "abstain"
    REQUIRE_SUCCESSOR = "require_successor"


class EvaluatorObservation(StrictModel):
    item_id: str
    score: float
    label: str | None = None


class EvaluatorFamilyResult(StrictModel):
    evaluator_id: str
    family_id: str
    implementation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    metric_name: str
    aggregate_score: float
    direction: Literal["higher_is_better", "lower_is_better"]
    decision: HypothesisVerdictStatus
    observations: list[EvaluatorObservation] = Field(default_factory=list)


class RowLevelDisagreement(StrictModel):
    item_id: str
    primary_score: float
    secondary_score: float
    absolute_difference: float = Field(ge=0)
    primary_label: str | None = None
    secondary_label: str | None = None


class EvaluatorDisagreementReport(StrictModel):
    schema_version: int = 1
    report_id: str
    study_id: str
    contract_version: int | None = Field(default=None, ge=1)
    plan_id: str | None = None
    primary_evaluator_id: str
    secondary_evaluator_id: str
    primary_family_id: str
    secondary_family_id: str
    metric_name: str
    common_item_count: int = Field(ge=0)
    missing_from_primary: list[str] = Field(default_factory=list)
    missing_from_secondary: list[str] = Field(default_factory=list)
    mean_absolute_difference: float | None = Field(default=None, ge=0)
    max_absolute_difference: float | None = Field(default=None, ge=0)
    row_level_disagreements: list[RowLevelDisagreement] = Field(default_factory=list)
    directional_conclusion_agreement: bool
    verdict_stable: bool
    status: EvaluatorAgreementStatus
    requires_adjudication: bool
    historical_evaluations_preserved: Literal[True] = True
    created_at: str = Field(default_factory=utc_now)


class EvaluatorAdjudicationRecord(StrictModel):
    schema_version: int = 1
    adjudication_id: str
    study_id: str
    disagreement_report_id: str
    reviewer_role: Literal["project_owner", "authorized_human_reviewer"]
    reviewed_item_ids: list[str] = Field(default_factory=list)
    decision: EvaluatorAdjudicationDecision
    rationale: str = Field(min_length=1)
    historical_evaluations_preserved: Literal[True] = True
    created_at: str = Field(default_factory=utc_now)


def create_evaluator_adjudication(
    *,
    report: EvaluatorDisagreementReport,
    reviewer_role: Literal["project_owner", "authorized_human_reviewer"],
    reviewed_item_ids: list[str],
    decision: EvaluatorAdjudicationDecision,
    rationale: str,
) -> EvaluatorAdjudicationRecord:
    known_items = {item.item_id for item in report.row_level_disagreements}
    reviewed = sorted(set(reviewed_item_ids))
    if report.requires_adjudication and known_items and not reviewed:
        raise ValueError("adjudication must identify reviewed disagreement items")
    unknown = sorted(set(reviewed).difference(known_items))
    if unknown:
        raise ValueError("adjudication references unknown disagreement items")
    return EvaluatorAdjudicationRecord(
        adjudication_id=stable_id(
            "evaluator-adjudication",
            report.report_id,
            reviewer_role,
            decision.value,
            *reviewed,
            rationale.strip(),
        ),
        study_id=report.study_id,
        disagreement_report_id=report.report_id,
        reviewer_role=reviewer_role,
        reviewed_item_ids=reviewed,
        decision=decision,
        rationale=rationale.strip(),
    )


def compare_evaluator_families(
    *,
    study_id: str,
    contract_version: int | None = None,
    plan_id: str | None = None,
    primary: EvaluatorFamilyResult,
    secondary: EvaluatorFamilyResult,
    row_tolerance: float = 1e-9,
    aggregate_tolerance: float = 1e-9,
) -> EvaluatorDisagreementReport:
    if primary.family_id == secondary.family_id:
        raise ValueError("secondary evaluator must use a distinct family_id")
    if primary.metric_name != secondary.metric_name:
        raise ValueError("evaluator families must report the same frozen metric")
    if primary.direction != secondary.direction:
        raise ValueError("evaluator families must use the same metric direction")
    if row_tolerance < 0 or aggregate_tolerance < 0:
        raise ValueError("evaluator tolerances must be non-negative")

    primary_rows = {item.item_id: item for item in primary.observations}
    secondary_rows = {item.item_id: item for item in secondary.observations}
    if len(primary_rows) != len(primary.observations):
        raise ValueError("primary evaluator emitted duplicate item_id")
    if len(secondary_rows) != len(secondary.observations):
        raise ValueError("secondary evaluator emitted duplicate item_id")

    common = sorted(set(primary_rows).intersection(secondary_rows))
    missing_from_primary = sorted(set(secondary_rows).difference(primary_rows))
    missing_from_secondary = sorted(set(primary_rows).difference(secondary_rows))
    disagreements: list[RowLevelDisagreement] = []
    differences: list[float] = []
    for item_id in common:
        left = primary_rows[item_id]
        right = secondary_rows[item_id]
        difference = abs(left.score - right.score)
        differences.append(difference)
        label_disagrees = (
            left.label is not None
            and right.label is not None
            and left.label != right.label
        )
        if difference > row_tolerance or label_disagrees:
            disagreements.append(
                RowLevelDisagreement(
                    item_id=item_id,
                    primary_score=left.score,
                    secondary_score=right.score,
                    absolute_difference=difference,
                    primary_label=left.label,
                    secondary_label=right.label,
                )
            )

    aggregate_agreement = (
        abs(primary.aggregate_score - secondary.aggregate_score)
        <= aggregate_tolerance
    )
    directional_agreement = primary.decision is secondary.decision
    incomplete = bool(missing_from_primary or missing_from_secondary)
    if incomplete:
        status = EvaluatorAgreementStatus.INCOMPLETE
    elif not directional_agreement:
        status = EvaluatorAgreementStatus.DIRECTIONAL_DISAGREEMENT
    elif not aggregate_agreement or disagreements:
        status = EvaluatorAgreementStatus.METRIC_DISAGREEMENT
    else:
        status = EvaluatorAgreementStatus.STABLE

    stable = status is EvaluatorAgreementStatus.STABLE
    return EvaluatorDisagreementReport(
        report_id=stable_id(
            "evaluator-disagreement",
            study_id,
            primary.evaluator_id,
            secondary.evaluator_id,
            primary.implementation_digest,
            secondary.implementation_digest,
        ),
        study_id=study_id,
        contract_version=contract_version,
        plan_id=plan_id,
        primary_evaluator_id=primary.evaluator_id,
        secondary_evaluator_id=secondary.evaluator_id,
        primary_family_id=primary.family_id,
        secondary_family_id=secondary.family_id,
        metric_name=primary.metric_name,
        common_item_count=len(common),
        missing_from_primary=missing_from_primary,
        missing_from_secondary=missing_from_secondary,
        mean_absolute_difference=(
            sum(differences) / len(differences) if differences else None
        ),
        max_absolute_difference=max(differences) if differences else None,
        row_level_disagreements=disagreements,
        directional_conclusion_agreement=directional_agreement,
        verdict_stable=stable,
        status=status,
        requires_adjudication=not stable,
    )


def validate_evaluator_robustness_for_completion(
    evaluator_policy: dict[str, Any],
    reports: Iterable[EvaluatorDisagreementReport],
    adjudications: Iterable[EvaluatorAdjudicationRecord] = (),
) -> list[str]:
    """Return fail-closed completion blockers without changing old verdicts."""

    required = bool(
        evaluator_policy.get("secondary_required")
        or evaluator_policy.get("uses_learned_evaluator")
    )
    if not required:
        return []
    resolved = list(reports)
    if not resolved:
        return [
            "EVALUATOR_COMPARISON_MISSING: the frozen contract requires a "
            "distinct second evaluator family before Stage 3 completion"
        ]
    adjudication_by_report = {
        item.disagreement_report_id: item for item in adjudications
    }
    unstable = [
        item.report_id
        for item in resolved
        if item.requires_adjudication
        and (
            item.report_id not in adjudication_by_report
            or adjudication_by_report[item.report_id].decision
            in {
                EvaluatorAdjudicationDecision.ABSTAIN,
                EvaluatorAdjudicationDecision.REQUIRE_SUCCESSOR,
            }
        )
    ]
    if unstable:
        return [
            "EVALUATOR_DISAGREEMENT_UNRESOLVED: row-level or directional "
            "disagreement requires adjudication before a stable verdict; "
            + ", ".join(unstable)
        ]
    return []


__all__ = [
    "EvaluatorAgreementStatus",
    "EvaluatorAdjudicationDecision",
    "EvaluatorAdjudicationRecord",
    "EvaluatorDisagreementReport",
    "EvaluatorFamilyResult",
    "EvaluatorObservation",
    "RowLevelDisagreement",
    "compare_evaluator_families",
    "create_evaluator_adjudication",
    "validate_evaluator_robustness_for_completion",
]
