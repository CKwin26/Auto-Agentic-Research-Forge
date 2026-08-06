from __future__ import annotations

from research_forge.sci_ssci_writing import (
    SCI_SSCI_LICENSE,
    SCI_SSCI_SOURCE_REPOSITORY,
    audit_stage_four_manuscript,
    stage_four_sci_ssci_contract,
)


def _draft(**updates: str) -> dict[str, str]:
    value = {
        "title": "配对评估中的证据约束与声明可靠性",
        "abstract": (
            "本研究评估证据约束流程。配对设计比较两个条件，结果与冻结"
            "证据一致 [source-1]。这些观察提示该流程具有审计价值。"
        ),
        "introduction": "研究背景与问题。",
        "methods": "采用配对设计。",
        "results": "观察到冻结结果 [source-1]。",
        "discussion": "结果只适用于当前研究边界。",
        "limitations": "样本与任务范围有限。",
    }
    value.update(updates)
    return value


def test_contract_records_source_and_scientific_authority_boundary() -> None:
    contract = stage_four_sci_ssci_contract()
    assert contract["source"]["repository"] == SCI_SSCI_SOURCE_REPOSITORY
    assert contract["source"]["license"] == SCI_SSCI_LICENSE
    assert (
        contract["scientific_authority_boundary"]["may_change_experiment_verdict"]
        is False
    )
    assert contract["invariants"]["no_silent_claim_strength_change"] is True
    assert contract["policy_id"] == "research-forge-sci-ssci-writing-v2"
    assert contract["abstract_route"][-1] == "single_calibrated_boundary"
    assert contract["abstract_reader_contract"]["audit_report_voice_allowed"] is False


def test_manuscript_audit_accepts_bounded_noncausal_draft() -> None:
    report = audit_stage_four_manuscript(
        _draft(),
        verified_source_ids={"source-1"},
        causal_claim_authorized=False,
    )
    assert report["passed"] is True
    assert report["findings"] == []


def test_manuscript_audit_rejects_internal_title_and_unknown_source() -> None:
    report = audit_stage_four_manuscript(
        _draft(
            title="dual_quality_top5 的研究",
            abstract="本研究报告结果 [source-2]。",
        ),
        verified_source_ids={"source-1"},
        causal_claim_authorized=False,
    )
    codes = {item["code"] for item in report["findings"]}
    assert report["passed"] is False
    assert codes == {"TITLE_INTERNAL_IDENTIFIER", "UNVERIFIED_CITATION"}


def test_manuscript_audit_rejects_structured_abstract() -> None:
    report = audit_stage_four_manuscript(
        _draft(abstract="背景：研究问题。\n\n结果：观察结果。"),
        verified_source_ids={"source-1"},
        causal_claim_authorized=False,
    )
    codes = {item["code"] for item in report["findings"]}
    assert "ABSTRACT_NOT_SINGLE_PARAGRAPH" in codes
    assert "ABSTRACT_SECTION_LABEL" in codes


def test_manuscript_audit_rejects_reader_facing_audit_report_voice() -> None:
    report = audit_stage_four_manuscript(
        _draft(
            abstract=(
                "We study dual_quality_top5 in a controlled comparison. The "
                "frozen audit ledger and immutable hash gate define the result."
            )
        ),
        verified_source_ids={"source-1"},
        causal_claim_authorized=False,
    )
    codes = {item["code"] for item in report["findings"]}
    assert "ABSTRACT_INTERNAL_IDENTIFIER" in codes
    assert "ABSTRACT_AUDIT_REPORT_VOICE" in codes


def test_manuscript_audit_blocks_unauthorized_causal_promise() -> None:
    report = audit_stage_four_manuscript(
        _draft(title="证据约束导致声明可靠性提高"),
        verified_source_ids={"source-1"},
        causal_claim_authorized=False,
    )
    assert report["passed"] is False
    assert report["findings"][0]["code"] == "CLAIM_STRENGTH_ESCALATION"

    authorized = audit_stage_four_manuscript(
        _draft(title="证据约束导致声明可靠性提高"),
        verified_source_ids={"source-1"},
        causal_claim_authorized=True,
    )
    assert authorized["passed"] is True
