from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from research_forge.publication_manual_audit import (
    PublicationManualAuditError,
    _select_blinded_sample,
    audit_publication_manual_audit,
    finalize_publication_manual_audit,
    prepare_publication_manual_audit_packets,
    submit_publication_manual_audits,
)
from research_forge.publication_audit_workbooks import (
    import_publication_manual_audit_workbooks,
)
from research_forge.publication_synthesis import _render_markdown
from research_forge.storage import read_json, sha256_file, write_json_atomic


def test_publication_manual_audit_includes_every_rare_unsupported_claim() -> None:
    candidates = [
        {"task_id": "task", "arm": "baseline", "sort_key": "1", "evaluator_class": "unsupported"},
        {"task_id": "task", "arm": "baseline", "sort_key": "2", "evaluator_class": "non_unsupported"},
        {"task_id": "task", "arm": "treatment", "sort_key": "3", "evaluator_class": "non_unsupported"},
        {"task_id": "task", "arm": "treatment", "sort_key": "4", "evaluator_class": "non_unsupported"},
    ]
    selected, audit = _select_blinded_sample(
        candidates,
        {
            "tasks": [{"task_id": "task"}],
            "manual_audit": {
                "claims_per_task_arm": 2,
                "target_unsupported_per_task_arm": 1,
                "target_non_unsupported_per_task_arm": 1,
                "total_claims": 4,
            },
        },
    )
    assert len(selected) == 4
    assert audit["all_available_unsupported_selected"] is True
    assert audit["available_evaluator_unsupported"] == 1


def _publication_audit_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    result_root = project / "stage2" / "protected_nli_evaluation"
    audit_root = result_root / "manual-audit"
    audit_root.mkdir(parents=True)
    summary = {
        "schema_version": 1,
        "protocol_id": "publication-test-protocol",
        "analysis_status": "protected_independent_nli_complete_human_audit_pending",
        "primary_analysis_interpretable": False,
        "pair_count": 40,
        "primary_metric": "unsupported_claim_rate_effect",
        "paired_analysis": {"mean": -0.1, "median": 0.0, "ci_95": [-0.2, 0.0]},
        "leave_one_task_out": [],
        "arm_metrics": {
            "baseline": {"unsupported_claim_rate": 0.2},
            "treatment": {"unsupported_claim_rate": 0.1},
        },
        "evaluation_hashes": {},
        "construct_boundary": "Automated proxy until human audit completion.",
    }
    write_json_atomic(result_root / "summary.json", summary)
    sample = {
        "schema_version": 1,
        "protocol_id": "publication-test-protocol",
        "arm_blinded": True,
        "task_blinded": True,
        "evaluator_verdict_blinded": True,
        "independent_auditors_required": 2,
        "adjudication_required": True,
        "planned_total": 2,
        "actual_total": 2,
        "items": [
            {
                "audit_id": f"audit-{index}",
                "claim_type": "result",
                "claim_text": f"Claim {index}",
                "linked_evidence": {"evidence": f"Evidence {index}"},
                "auditor_1": {"verdict": None, "rationale": ""},
                "auditor_2": {"verdict": None, "rationale": ""},
                "adjudication": {"verdict": None, "rationale": ""},
            }
            for index in range(2)
        ],
    }
    write_json_atomic(audit_root / "sample.json", sample)
    manifest = {
        "schema_version": 1,
        "protocol_id": "publication-test-protocol",
        "source_summary_sha256": sha256_file(result_root / "summary.json"),
        "source_evaluation_hashes": {},
        "sample_sha256": sha256_file(audit_root / "sample.json"),
        "status": "awaiting_two_independent_auditors",
        "selection": {
            "actual_total": 2,
            "available_evaluator_unsupported": 2,
            "all_available_unsupported_selected": True,
            "group_counts": {
                "task|baseline": {"selected_evaluator_unsupported": 1},
                "task|treatment": {"selected_evaluator_unsupported": 1},
            },
        },
    }
    write_json_atomic(audit_root / "manifest.json", manifest)
    write_json_atomic(
        project / "stage2" / "protocol.json",
        {
            "protocol_id": "publication-test-protocol",
            "tasks": [{"task_id": "task"}],
            "manual_audit": {
                "independent_auditors": 2,
                "adjudication_required": True,
                "total_claims": 2,
                "claims_per_task_arm": 1,
                "target_unsupported_per_task_arm": 1,
                "target_non_unsupported_per_task_arm": 0,
                "false_positive_threshold": 0.15,
            },
        },
    )
    return project


def _complete_packet(path: Path, auditor_id: str, verdicts: list[str]) -> Path:
    packet = read_json(path)
    packet["auditor_attestation"] = {
        "auditor_id": auditor_id,
        "completed_at": "2026-07-21T12:00:00+08:00",
        "worked_independently": True,
        "did_not_view_other_answers": True,
        "did_not_view_unblinding_or_evaluator_output": True,
    }
    for item, verdict in zip(packet["items"], verdicts, strict=True):
        item["response"] = {"verdict": verdict, "rationale": f"Evidence supports {verdict}."}
    completed = path.with_name(path.stem + "_completed.json")
    write_json_atomic(completed, packet)
    return completed


def _write_minimal_xlsx(
    path: Path, sheets: list[tuple[str, dict[str, str]]]
) -> None:
    workbook_sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _) in enumerate(sheets, 1)
    )
    relationships = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    content_types = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{content_types}</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{workbook_sheets}</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{relationships}</Relationships>",
        )
        for index, (_, cells) in enumerate(sheets, 1):
            rows: dict[int, list[tuple[str, str]]] = {}
            for address, value in cells.items():
                row = int("".join(character for character in address if character.isdigit()))
                rows.setdefault(row, []).append((address, value))
            row_xml = "".join(
                f'<row r="{row}">'
                + "".join(
                    f'<c r="{address}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
                    for address, value in sorted(row_cells)
                )
                + "</row>"
                for row, row_cells in sorted(rows.items())
            )
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<sheetData>{row_xml}</sheetData></worksheet>",
            )


def test_publication_manual_audit_closes_the_human_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _publication_audit_project(tmp_path)
    packet_manifest = prepare_publication_manual_audit_packets(project)
    assert packet_manifest["items_per_auditor"] == 2
    assert prepare_publication_manual_audit_packets(project) == packet_manifest
    packet_root = project / "stage2" / "protected_nli_evaluation" / "manual-audit" / "independent-packets"
    auditor_1 = _complete_packet(packet_root / "auditor_1.json", "human-a", ["unsupported"] * 2)
    auditor_2 = _complete_packet(packet_root / "auditor_2.json", "human-b", ["unsupported"] * 2)
    submission = submit_publication_manual_audits(project, auditor_1, auditor_2)
    assert submission["status"] == "ready_to_finalize"
    monkeypatch.setattr(
        "research_forge.publication_manual_audit._publication_evaluator_verdicts",
        lambda _project, _protocol: {"audit-0": "unsupported", "audit-1": "unsupported"},
    )
    result = finalize_publication_manual_audit(project)
    assert result["primary_analysis_interpretable"] is True
    assert result["analysis_status"] == "publication_human_audit_complete_primary_analysis_unlocked"
    audit = audit_publication_manual_audit(project)
    assert audit["passed"] is True
    assert audit["complete"] is True


def test_publication_audit_workbooks_import_and_finalize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _publication_audit_project(tmp_path)
    auditor_1 = tmp_path / "auditor_1.xlsx"
    auditor_2 = tmp_path / "auditor_2.xlsx"
    source_claims = {
        "A7": "audit-0",
        "C7": "Claim 0",
        "E7": "B",
        "A8": "audit-1",
        "C8": "Claim 1",
        "E8": "B",
    }
    _write_minimal_xlsx(auditor_1, [("审计清单", source_claims)])
    second_claims = dict(source_claims)
    second_claims["E8"] = "C"
    _write_minimal_xlsx(auditor_2, [("审计清单", second_claims)])
    supplement = tmp_path / "supplement.xlsx"
    confirmation = {
        "D7": "支持",
        "D8": "不支持",
        "D9": "无法判断",
        "B13": "human-a",
        "B14": "2026-07-22T12:00:00+08:00",
        "B15": "是",
        "B16": "是",
        "B17": "是",
        "B18": "是",
        "B19": "I compared the claim only with the displayed evidence.",
        "E13": "human-b",
        "E14": "2026-07-22T12:05:00+08:00",
        "E15": "是",
        "E16": "是",
        "E17": "是",
        "E18": "是",
        "E19": "I compared the claim only with the displayed evidence.",
    }
    adjudication = {
        "B3": "human-c",
        "D3": "2026-07-22T12:10:00+08:00",
        "F3": "是",
        "A6": "audit-1",
        "H6": "不支持",
        "I6": "The displayed evidence does not support the claim.",
    }
    verification = {
        "B4": sha256_file(auditor_1),
        "C4": sha256_file(auditor_2),
    }
    _write_minimal_xlsx(
        supplement,
        [
            ("补充确认", confirmation),
            ("分歧裁决", adjudication),
            ("核验摘要", verification),
        ],
    )
    monkeypatch.setattr(
        "research_forge.publication_manual_audit._publication_evaluator_verdicts",
        lambda _project, _protocol: {"audit-0": "unsupported", "audit-1": "unsupported"},
    )
    imported = import_publication_manual_audit_workbooks(
        project, auditor_1, auditor_2, supplement
    )
    assert imported["status"] == "complete"
    assert imported["analysis_status"] == (
        "publication_human_audit_complete_primary_analysis_unlocked"
    )
    assert imported["unified_rationale_substitutions"] == {
        "auditor_1": 2,
        "auditor_2": 2,
    }
    assert audit_publication_manual_audit(project)["complete"] is True


def test_publication_manual_audit_rejects_same_auditor_identity(tmp_path: Path) -> None:
    project = _publication_audit_project(tmp_path)
    prepare_publication_manual_audit_packets(project)
    packet_root = project / "stage2" / "protected_nli_evaluation" / "manual-audit" / "independent-packets"
    auditor_1 = _complete_packet(packet_root / "auditor_1.json", "same-human", ["unsupported"] * 2)
    auditor_2 = _complete_packet(packet_root / "auditor_2.json", "same-human", ["unsupported"] * 2)
    with pytest.raises(PublicationManualAuditError, match="different IDs"):
        submit_publication_manual_audits(project, auditor_1, auditor_2)


def _render_completed_human_state(
    *, unlocked: bool, context_restored: bool = False, repair_verified: bool = False
) -> str:
    analysis = {
        "human_validation": "COMPLETE",
        "primary_analysis_interpretable": unlocked,
        "manual_gate": {
            "audit_false_positive_rate": 0.0 if unlocked else 0.2,
            "audit_false_positive_count": 0 if unlocked else 1,
            "audited_evaluator_unsupported": 5,
            "audit_false_negative_rate": 0.01,
        },
        "human_audit": {
            "audited_claims": 128,
            "agreement_count": 123,
            "agreement_rate": 123 / 128,
            "cohen_kappa": 0.6022374145431946,
            "adjudicated_disagreements": 5,
            "unified_rationale_substitutions": {
                "auditor_1": 123,
                "auditor_2": 125,
            },
        },
        "arm_metrics": {
            "baseline": {"non_entailment_rate": 0.05},
            "treatment": {"non_entailment_rate": 0.02},
        },
    }
    if context_restored:
        analysis["context_restored_review"] = {
            "review_type": "post_unblinding_context_restored_diagnostic",
            "reviewed_evaluator_unsupported": 5,
            "contextual_verdict_letters": "AAAAA",
            "contextual_verdict_counts": {
                "supported": 5,
                "unsupported": 0,
                "abstain": 0,
            },
            "replaces_preregistered_blinded_audit": False,
            "can_unlock_primary_analysis": False,
        }
    if repair_verified:
        analysis["context_repair_verification"] = {
            "status": "implementation_verified_pending_successor_protocol",
            "passed": True,
            "replayed_cases": 5,
            "supported_cases": 5,
            "successor_evaluator_implementation_version": (
                "publication-nli-v4-context-bound-deterministic-metrics"
            ),
            "does_not_recompute_historical_primary_analysis": True,
        }
    claims = [
        {
            "metrics": {
                "baseline_unsupported_claim_rate": 0.03,
                "treatment_unsupported_claim_rate": 0.01,
                "paired_mean_effect": -0.02,
                "ci_95_low": -0.06,
                "ci_95_high": 0.0,
            }
        },
        {
            "metrics": {
                "baseline_task_native_score": 0.34,
                "treatment_task_native_score": 0.34,
                "difference": 0.0,
            }
        },
    ]
    references = [
        {
            "citation_key": f"R{index}",
            "authors": ["Author"],
            "year": 2026,
            "title": f"Reference {index}",
            "locator": f"doi:test-{index}",
        }
        for index in range(1, 5)
    ]
    return _render_markdown(Path("."), analysis, claims, references)


def test_completed_publication_human_gate_removes_pending_language() -> None:
    markdown = _render_completed_human_state(unlocked=True)
    assert "HUMAN_AUDIT_COMPLETE" in markdown
    assert "HUMAN_GATE_PENDING" not in markdown
    assert "false positives" in markdown
    assert "123/128" in markdown
    assert "Cohen's kappa 0.602" in markdown
    assert "5 disagreements" in markdown
    assert "does not claim that the gate has completed a human review process" not in markdown
    assert "that human reviewers would agree" not in markdown
    assert "Two independent human auditors and a blinded adjudicator" in markdown
    assert "123 auditor-1 and 125 auditor-2 judgments" in markdown
    assert "rather than represented as item-specific prose" in markdown


def test_failed_publication_human_gate_reports_primary_invalidation() -> None:
    markdown = _render_completed_human_state(unlocked=False)
    assert "FAULT_LOCALIZATION_COMPLETE" in markdown
    assert "HUMAN_GATE_PENDING" not in markdown
    assert "historical automated treatment endpoint remains non-confirmatory" in markdown
    assert "When an Automated Claim-Support Endpoint Fails Human Validation" in markdown
    assert "Finally, The project-specific" not in markdown


def test_context_restored_review_reframes_failure_as_fault_localization() -> None:
    markdown = _render_completed_human_state(
        unlocked=False, context_restored=True, repair_verified=True
    )
    assert "Fault Localization in an Evidence-Governed Research Workflow" in markdown
    assert "Post-unblinding context-restored diagnostic review" in markdown
    assert "`AAAAA`" in markdown
    assert "all 5/5 were supported" in markdown
    assert "does not replace the signed blinded audit" in markdown
    assert "cannot unlock the preregistered primary analysis" in markdown
    assert "successful fault localization rather than a validated treatment benefit" in markdown
    assert "Successor implementation repair verification" in markdown
    assert "returned supported for 5/5" in markdown
    assert "does not recompute the historical primary analysis" in markdown
    assert "successor run was generated" not in markdown
    assert "final successor matrix was run" not in markdown
    assert "has not yet been used in a fresh successor matrix" in markdown
