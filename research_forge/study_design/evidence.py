"""Natural-language, authority-bounded Stage 4 handoff helpers."""

from __future__ import annotations

from .registry import study_design
from .schemas import AnalysisPlan, ClaimEnvelope, StudyDesignEvaluation


def produce_claim_envelope(
    plan: AnalysisPlan, evaluation: StudyDesignEvaluation
) -> ClaimEnvelope:
    return study_design(plan.study_design_id).produce_claim_envelope(
        plan, evaluation
    )


def validate_claim_envelope_authority(
    evaluation: StudyDesignEvaluation, envelope: ClaimEnvelope
) -> list[str]:
    """Fail closed when Stage 4 attempts to rewrite scientific authority."""

    violations: list[str] = []
    if envelope.scientific_verdict != evaluation.primary_decision:
        violations.append("Stage 4 cannot upgrade or rewrite the Scientific Verdict")
    if not envelope.permitted_claims:
        violations.append("Stage 4 requires at least one authority-bounded permitted claim")
    if not envelope.generalization_boundary.strip():
        violations.append("Stage 4 cannot remove the frozen generalization boundary")
    return violations


__all__ = ["produce_claim_envelope", "validate_claim_envelope_authority"]
