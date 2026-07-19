from __future__ import annotations

from copy import deepcopy

import pytest

from research_forge.manual_audit import (
    ManualAuditError,
    _auditor_packet,
    _cohen_kappa,
    _manual_gate_statistics,
    _select_candidate_sample,
    validate_auditor_packet_data,
)


def _candidate(
    audit_id: str, task_id: str, arm: str, evaluator_class: str, sort_index: int
) -> dict[str, object]:
    return {
        "audit_id": audit_id,
        "task_id": task_id,
        "arm": arm,
        "evaluator_class": evaluator_class,
        "sort_key": f"{sort_index:064d}",
    }


def test_manual_selection_audits_all_available_unsupported_when_quota_is_short() -> None:
    candidates = []
    for index in range(5):
        candidates.append(_candidate(f"b-u-{index}", "task", "baseline", "unsupported", index))
    for index in range(7):
        candidates.append(
            _candidate(f"b-o-{index}", "task", "baseline", "non_unsupported", 10 + index)
        )
    candidates.append(_candidate("t-u-0", "task", "treatment", "unsupported", 20))
    for index in range(11):
        candidates.append(
            _candidate(f"t-o-{index}", "task", "treatment", "non_unsupported", 30 + index)
        )

    selected, audit = _select_candidate_sample(
        candidates,
        [("task", "baseline"), ("task", "treatment")],
        claims_per_group=8,
        target_unsupported_per_group=4,
        target_non_unsupported_per_group=4,
    )

    assert len(selected) == 16
    assert audit["planned_unsupported_quota"] == 8
    assert audit["available_evaluator_unsupported"] == 6
    assert audit["selected_evaluator_unsupported"] == 6
    assert audit["audit_all_available_unsupported"] is True
    assert {item["audit_id"] for item in selected if "-u-" in str(item["audit_id"])} == {
        "b-u-0",
        "b-u-1",
        "b-u-2",
        "b-u-3",
        "b-u-4",
        "t-u-0",
    }


def _sample() -> dict[str, object]:
    return {
        "protocol_id": "stage2-123456789abc",
        "items": [
            {
                "audit_id": "audit-1",
                "claim_type": "experiment",
                "claim_text": "The measured score was exactly 0.5.",
                "linked_evidence": {"declared_metric_values": {"Accuracy": 0.5}},
            },
            {
                "audit_id": "audit-2",
                "claim_type": "literature",
                "claim_text": "The cited source describes a verifier.",
                "linked_evidence": {"linked_source_ids": ["paper-1"]},
            },
        ],
    }


def test_auditor_packet_validation_allows_only_attestation_and_responses() -> None:
    sample = _sample()
    source_hash = "a" * 64
    packet = _auditor_packet(sample, "auditor_1", source_hash)
    packet["auditor_attestation"] = {
        "auditor_id": "reviewer-alpha",
        "completed_at": "2026-07-18T12:00:00+00:00",
        "worked_independently": True,
        "did_not_view_other_answers": True,
        "did_not_view_unblinding_or_evaluator_output": True,
    }
    for item in packet["items"]:
        item["response"] = {"verdict": "supported", "rationale": "Directly supported."}

    auditor_id, decisions = validate_auditor_packet_data(
        sample, packet, "auditor_1", source_hash
    )
    assert auditor_id == "reviewer-alpha"
    assert list(decisions) == ["audit-1", "audit-2"]

    tampered = deepcopy(packet)
    tampered["items"][0]["claim_text"] = "Changed claim."
    with pytest.raises(ManualAuditError, match="immutable field changed"):
        validate_auditor_packet_data(sample, tampered, "auditor_1", source_hash)


def test_manual_gate_enforces_all_available_unsupported_and_threshold() -> None:
    evaluator = {
        "a": "unsupported",
        "b": "unsupported",
        "c": "unsupported",
        "d": "supported",
        "e": "supported",
    }
    decisions = {
        "a": {"verdict": "unsupported", "rationale": "x"},
        "b": {"verdict": "unsupported", "rationale": "x"},
        "c": {"verdict": "supported", "rationale": "x"},
        "d": {"verdict": "supported", "rationale": "x"},
        "e": {"verdict": "unsupported", "rationale": "x"},
    }
    result = _manual_gate_statistics(
        evaluator,
        decisions,
        planned_unsupported_quota=4,
        threshold=0.15,
    )
    assert result["audit_false_positive_rate"] == pytest.approx(1 / 3)
    assert result["audit_false_negative_rate"] == pytest.approx(1 / 2)
    assert result["threshold_passed"] is False

    with pytest.raises(ManualAuditError, match="omitted available"):
        _manual_gate_statistics(
            evaluator,
            {key: value for key, value in decisions.items() if key != "c"},
            planned_unsupported_quota=4,
            threshold=0.15,
        )


def test_cohen_kappa_is_one_for_identical_nonconstant_ratings() -> None:
    first = {
        "a": {"verdict": "supported"},
        "b": {"verdict": "unsupported"},
        "c": {"verdict": "abstain"},
    }
    assert _cohen_kappa(first, deepcopy(first)) == pytest.approx(1.0)
