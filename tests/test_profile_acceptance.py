from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from research_forge.profiles.acceptance import (
    AcceptanceCheck,
    ProfileAcceptanceReport,
    ProfileAvailability,
    ProfileImplementationStatus,
    acceptance_source_hashes_match,
    assess_profile_maturity,
    availability_for_maturity,
    implementation_status,
)
from research_forge.profiles.sdk import ProfileMaturity
from research_forge.profiles.sdk import (
    ContractCompletionItem,
    ContractCompletionKind,
    ContractCompletionPatch,
)


def _check(check_id: str, passed: bool = True) -> AcceptanceCheck:
    return AcceptanceCheck(check_id=check_id, passed=passed)


def _report(**changes: object) -> ProfileAcceptanceReport:
    payload: dict[str, object] = {
        "profile_id": "time_series_backtest_v1",
        "profile_version": "1.0.0",
        "registry_check": _check("registry"),
        "schema_check": _check("schema"),
        "qualification_check": _check("qualification"),
        "contract_completion_check": _check("completion"),
        "semantic_diff_check": _check("semantic-diff"),
        "run_plan_serialization_check": _check("run-plan"),
        "dry_run_check": _check("dry-run"),
        "formal_e2e_check": _check("formal-e2e", False),
        "verdict_check": _check("verdict", False),
        "evidence_binding_check": _check("evidence", False),
        "completion_record_check": _check("completion-record", False),
        "independent_metric_recompute_check": _check("recompute", False),
    }
    payload.update(changes)
    return ProfileAcceptanceReport.model_validate(payload)


def test_component_evidence_cannot_claim_c3() -> None:
    report = _report()
    assert assess_profile_maturity(report) is ProfileMaturity.C2_DRY_RUN
    assert implementation_status(report.with_assessed_maturity().assessed_maturity) is (
        ProfileImplementationStatus.COMPONENT_VALIDATED
    )
    assert availability_for_maturity(ProfileMaturity.C2_DRY_RUN) is (
        ProfileAvailability.DEGRADED
    )


def test_availability_is_independent_from_historical_maturity() -> None:
    assert availability_for_maturity(
        ProfileMaturity.C2_DRY_RUN,
        formal_path_connected=False,
    ) is ProfileAvailability.DEGRADED
    assert availability_for_maturity(
        ProfileMaturity.C3_REAL_FIXTURE,
        formal_path_connected=False,
    ) is ProfileAvailability.UNAVAILABLE
    assert availability_for_maturity(
        ProfileMaturity.C3_REAL_FIXTURE,
        owner_approval_required=True,
    ) is ProfileAvailability.OWNER_APPROVAL_REQUIRED
    assert availability_for_maturity(
        ProfileMaturity.C3_REAL_FIXTURE,
    ) is ProfileAvailability.AVAILABLE


def test_c3_requires_full_chain_six_negatives_four_mutations_and_recompute() -> None:
    nearly = _report(
        formal_e2e_check=_check("formal-e2e"),
        verdict_check=_check("verdict"),
        evidence_binding_check=_check("evidence"),
        completion_record_check=_check("completion-record"),
        independent_metric_recompute_check=_check("recompute"),
        negative_case_checks=tuple(_check(f"negative-{i}") for i in range(6)),
        mutation_checks=tuple(_check(f"mutation-{i}") for i in range(3)),
        recompute_command=("python", "recompute.py"),
    )
    assert assess_profile_maturity(nearly) is ProfileMaturity.C2_DRY_RUN
    accepted = nearly.model_copy(
        update={
            "mutation_checks": tuple(_check(f"mutation-{i}") for i in range(4))
        }
    )
    assert assess_profile_maturity(accepted) is ProfileMaturity.C3_REAL_FIXTURE


def test_report_rejects_manually_inflated_maturity() -> None:
    with pytest.raises(ValidationError, match="evidence-derived"):
        _report(assessed_maturity=ProfileMaturity.C3_REAL_FIXTURE)


def test_duplicate_negative_or_mutation_receipts_cannot_promote() -> None:
    with pytest.raises(ValidationError, match="unique check_id"):
        _report(negative_case_checks=tuple(_check("same") for _ in range(6)))


def test_acceptance_report_is_bound_to_current_source(tmp_path) -> None:
    source = tmp_path / "profile.py"
    source.write_text("VERSION = 1\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = _report(source_hashes={"profile.py": digest})
    assert acceptance_source_hashes_match(report, repository_root=tmp_path)
    source.write_text("VERSION = 2\n", encoding="utf-8")
    assert not acceptance_source_hashes_match(report, repository_root=tmp_path)


def test_contract_completion_patch_separates_authority_classes() -> None:
    patch = ContractCompletionPatch(
        profile_id="time_series_backtest_v1",
        profile_version="1.0.0",
        items=(
            ContractCompletionItem(
                field_path="profile.top_k",
                kind=ContractCompletionKind.DETERMINISTIC_DERIVATION,
                candidate_value=5,
                source="resource_manifest",
                reason="The frozen manifest declares K=5.",
                requires_owner_approval=False,
            ),
            ContractCompletionItem(
                field_path="profile.tie_breaker",
                kind=ContractCompletionKind.PROFILE_DEFAULT,
                candidate_value="asset_id_ascending",
                source="profile_default",
                reason="Stable deterministic tie handling.",
                requires_owner_approval=True,
            ),
            ContractCompletionItem(
                field_path="common.primary_outcome",
                kind=ContractCompletionKind.SCIENTIFIC_DECISION,
                source="owner",
                reason="The primary outcome is a scientific choice.",
                requires_owner_approval=True,
            ),
            ContractCompletionItem(
                field_path="resources.market_data",
                kind=ContractCompletionKind.UNRESOLVABLE,
                source="resource_resolver",
                reason="The authorized source cannot be accessed.",
                requires_owner_approval=False,
                blocking=True,
            ),
        ),
    )
    assert len(patch.automatic_updates) == 1
    assert len(patch.owner_decisions) == 2
    assert len(patch.blockers) == 1


def test_contract_completion_authority_cannot_be_weakened() -> None:
    with pytest.raises(ValidationError, match="require owner approval"):
        ContractCompletionItem(
            field_path="common.primary_outcome",
            kind=ContractCompletionKind.SCIENTIFIC_DECISION,
            source="model",
            reason="Invalid automatic scientific decision.",
            requires_owner_approval=False,
        )
