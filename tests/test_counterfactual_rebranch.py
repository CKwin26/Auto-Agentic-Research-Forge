from __future__ import annotations

import math

from research_forge.counterfactual_rebranch import (
    _derive_verify_only_registry,
    _historical_stage2_evidence_audit,
    _pooled_metrics,
    _semantic_change_proxy,
    exact_sign_flip_pvalue,
)
from research_forge.study_models import (
    StudyArm,
    StudyClaim,
    StudyClaimRegistry,
    StudyClaimType,
)


def _claim(
    claim_id: str,
    text: str,
    *,
    metric_values: dict[str, float] | None = None,
) -> StudyClaim:
    return StudyClaim(
        claim_id=claim_id,
        run_id="agent-treatment--task--seed-0",
        arm=StudyArm.TREATMENT,
        task_pack="task-pack",
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text=text,
        experiment_run_id="run-001",
        metric_values=metric_values or {"accuracy": 0.75},
        artifact_paths=["evidence/record.json"],
    )


def _registry(claims: list[StudyClaim]) -> StudyClaimRegistry:
    return StudyClaimRegistry(
        registry_id="registry-0123456789ab",
        protocol_id="stage2-test",
        cell_id="treatment--task--seed-0",
        run_id="agent-treatment--task--seed-0",
        arm=StudyArm.TREATMENT,
        task_pack="task-pack",
        seed=0,
        final_output_text="\n".join(claim.claim_text for claim in claims),
        claims=claims,
    )


def test_verify_only_branch_retains_only_first_pass_supported_claims() -> None:
    claims = [
        _claim("claim-1", "Accuracy was 0.75."),
        _claim("claim-2", "Accuracy exceeded the target."),
        _claim("claim-3", "Accuracy may improve with more data."),
    ]
    result = _derive_verify_only_registry(
        _registry(claims),
        {
            "judgments": [
                {"claim_id": "claim-1", "verdict": "supported"},
                {"claim_id": "claim-2", "verdict": "unsupported"},
                {"claim_id": "claim-3", "verdict": "abstain"},
            ]
        },
    )

    assert [claim.claim_id for claim in result.claims] == ["claim-1"]
    assert result.final_output_text == "Accuracy was 0.75."
    assert result.registry_id.startswith("registry-")
    assert result.registry_id != "registry-0123456789ab"


def test_exact_sign_flip_pvalue_is_exact_and_two_sided() -> None:
    assert exact_sign_flip_pvalue([]) is None
    assert exact_sign_flip_pvalue([0.0, 0.0, 0.0]) == 1.0
    assert exact_sign_flip_pvalue([1.0, 1.0]) == 0.5
    assert math.isclose(exact_sign_flip_pvalue([1.0, 1.0, 1.0]), 0.25)


def test_semantic_change_proxy_distinguishes_hedging_and_removal() -> None:
    before = _claim("claim-1", "The method improves accuracy by 10%.")
    hedged = _claim("claim-1", "The method may improve accuracy by about 10%.")

    changed = _semantic_change_proxy(before, hedged)
    removed = _semantic_change_proxy(before, None)

    assert changed["change_type"] == "qualification_or_hedging_added"
    assert changed["delta"]["hedge_marker_count"] > 0
    assert removed["change_type"] == "removed"
    assert removed["after"] is None


def test_pooled_retention_uses_observed_reference_claim_count() -> None:
    item = {
        "claim_count": 2,
        "evaluable_claim_count": 2,
        "supported_count": 1,
        "unsupported_count": 1,
        "abstain_count": 0,
        "claims": [
            {"probabilities": {"max_entailment": 0.8}},
            {"probabilities": {"max_entailment": 0.2}},
        ],
    }

    result = _pooled_metrics([item], reference_claim_count=5)

    assert result["claim_retention_rate"] == 0.4
    assert result["nli_unsupported_rate_among_evaluable_claims"] == 0.5


def test_historical_audit_accepts_only_scoped_live_controller_drift(monkeypatch) -> None:
    class Audit:
        def __init__(self, checks, violations=(), completed_cells=0):
            self.checks = checks
            self.violations = list(violations)
            self.completed_cells = completed_cells

    monkeypatch.setattr(
        "research_forge.counterfactual_rebranch.audit_stage2_protocol",
        lambda project, persist: Audit(
            {"frozen_artifacts_unchanged": True, "live_controller_matches_frozen": False},
            ["the live shared controller differs from the frozen Stage 2 snapshot"],
        ),
    )
    monkeypatch.setattr(
        "research_forge.counterfactual_rebranch.audit_stage2_baseline",
        lambda project, persist: Audit(
            {"stage2_protocol_valid": False, "baseline_cells_integral": True},
            completed_cells=9,
        ),
    )
    monkeypatch.setattr(
        "research_forge.counterfactual_rebranch.audit_stage2_treatment",
        lambda project, persist: Audit(
            {
                "stage2_protocol_valid": False,
                "baseline_matrix_complete": False,
                "treatment_cells_integral": True,
            },
            completed_cells=9,
        ),
    )

    result = _historical_stage2_evidence_audit(None)

    assert result["passed"] is True


def test_historical_audit_rejects_any_additional_integrity_failure(monkeypatch) -> None:
    class Audit:
        def __init__(self, checks, violations=(), completed_cells=0):
            self.checks = checks
            self.violations = list(violations)
            self.completed_cells = completed_cells

    monkeypatch.setattr(
        "research_forge.counterfactual_rebranch.audit_stage2_protocol",
        lambda project, persist: Audit(
            {
                "frozen_artifacts_unchanged": False,
                "live_controller_matches_frozen": False,
            },
            [
                "the live shared controller differs from the frozen Stage 2 snapshot",
                "a frozen artifact changed",
            ],
        ),
    )
    monkeypatch.setattr(
        "research_forge.counterfactual_rebranch.audit_stage2_baseline",
        lambda project, persist: Audit(
            {"stage2_protocol_valid": False, "baseline_cells_integral": True},
            completed_cells=9,
        ),
    )
    monkeypatch.setattr(
        "research_forge.counterfactual_rebranch.audit_stage2_treatment",
        lambda project, persist: Audit(
            {
                "stage2_protocol_valid": False,
                "baseline_matrix_complete": False,
                "treatment_cells_integral": True,
            },
            completed_cells=9,
        ),
    )

    result = _historical_stage2_evidence_audit(None)

    assert result["passed"] is False
