"""Derive C3 receipts from the canonical Workflow v2 repository.

Acceptance reports are not scientific authority.  This module reconstructs
their four-phase receipts from immutable Workflow v2 objects, gates, steps and
artifacts so a hand-written JSON report cannot promote a component harness.
"""

from __future__ import annotations

from pathlib import Path

from ..workflow_domain import (
    ArtifactStatus,
    EntryMode,
    ExecutionStatus,
    GateStatus,
    GateType,
    WorkflowRepository,
    verify_completion_record,
)


REQUIRED_C3_WORKFLOW_RECEIPTS: tuple[str, ...] = (
    "idea_intake",
    "scope_frozen",
    "profile_qualified",
    "research_contract_completed",
    "owner_contract_approved",
    "research_contract_frozen",
    "resources_bound",
    "dry_run_completed",
    "formal_evaluation_completed",
    "independent_recalculation_completed",
    "scientific_verdict_frozen",
    "evidence_claim_map_completed",
    "stage_four_manuscript_completed",
    "paper_audit_completed",
    "completion_record_verified",
)


def derive_workflow_receipts(
    repository: WorkflowRepository, study_id: str
) -> dict[str, bool]:
    study = repository.load_study(study_id)
    scope = repository.latest_scope_contract(study_id)
    contract = repository.latest_research_contract(study_id)
    steps = repository.list_steps(study_id)
    succeeded = {
        item.step_type
        for item in steps
        if item.status is ExecutionStatus.SUCCEEDED
    }
    gates = repository.list_gates(study_id)
    artifacts = repository.list_artifacts(study_id)
    artifact_kinds = {item.kind for item in artifacts}
    runs = repository.list_research_runs(study_id)
    evaluations = repository.list_evaluation_records(study_id)
    claims = repository.list_claim_envelopes(study_id)
    completion_path = repository.root / "studies" / study_id / "completion_record.json"
    completion_valid = False
    if completion_path.is_file():
        try:
            completion_valid = verify_completion_record(
                completion_path,
                artifact_root=repository.root / "studies" / study_id,
            )["passed"]
        except (ValueError, KeyError, TypeError):
            completion_valid = False

    contract_gate_approved = any(
        item.gate_type is GateType.RESEARCH_CONTRACT
        and item.status is GateStatus.APPROVED
        and contract is not None
        and item.subject_version == contract.version
        for item in gates
    )
    receipts = {
        "idea_intake": study.entry_mode is EntryMode.IDEA_TO_PAPER,
        "scope_frozen": bool(
            scope is not None and scope.status is ArtifactStatus.FROZEN
        ),
        "profile_qualified": bool(
            contract
            and contract.study_design.get("id")
            and (
                "study_design_qualification" in succeeded
                or "profile_qualification" in succeeded
            )
        ),
        "research_contract_completed": bool(
            contract and contract.study_design_spec
        ),
        "owner_contract_approved": contract_gate_approved,
        "research_contract_frozen": bool(
            contract is not None and contract.status is ArtifactStatus.FROZEN
        ),
        "resources_bound": bool(
            "study_design_resource_binding" in succeeded
            and "study_design_dataset" in artifact_kinds
        ),
        "dry_run_completed": "study_design_dry_run" in succeeded,
        "formal_evaluation_completed": bool(
            evaluations
            and any(run.status is ExecutionStatus.SUCCEEDED for run in runs)
            and "study_design_formal_evaluation" in succeeded
        ),
        "independent_recalculation_completed": bool(
            "independent_recalculation" in artifact_kinds
            and "study_design_independent_recalculation" in succeeded
        ),
        "scientific_verdict_frozen": bool(
            study.latest_study_verdict_id
            and "study_design_scientific_verdict" in succeeded
        ),
        "evidence_claim_map_completed": bool(
            claims and "evidence_claim_map" in artifact_kinds
        ),
        "stage_four_manuscript_completed": bool(
            "final_manuscript_pdf" in artifact_kinds
            and "completion_record" in succeeded
        ),
        "paper_audit_completed": bool(
            "scientific_manuscript_validity_report" in artifact_kinds
            and (
                "paper_audit" in succeeded
                or "reporting_and_disclosure_audit" in succeeded
            )
        ),
        "completion_record_verified": completion_valid,
    }
    return receipts


def workflow_is_c3_complete(receipts: dict[str, bool]) -> bool:
    return all(receipts.get(key) is True for key in REQUIRED_C3_WORKFLOW_RECEIPTS)


__all__ = [
    "REQUIRED_C3_WORKFLOW_RECEIPTS",
    "derive_workflow_receipts",
    "workflow_is_c3_complete",
]
