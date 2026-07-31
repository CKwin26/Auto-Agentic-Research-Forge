from __future__ import annotations

from research_forge.stage_three import stage4_claim_authority
from research_forge.workflow_domain import (
    ConfirmatoryStatus,
    EntryMode,
    QualificationStatus,
    ScientificClaimEnvelope,
    Stage3CompletionPackage,
    WorkflowRepository,
)


def test_stage4_uses_newest_completion_not_filename_order(tmp_path):
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project("authority ordering")
    study = repository.create_study(
        project.project_id,
        "authority ordering",
        entry_mode=EntryMode.IDEA_TO_PAPER,
    )
    plan_id = "run-plan-1111111111111111"
    old_envelope = repository.save_claim_envelope(
        ScientificClaimEnvelope(
            claim_envelope_id="claim-envelope-ffffffffffffffff",
            study_id=study.study_id,
            plan_id=plan_id,
            allowed_claim="old authority",
            population="frozen tasks",
            tasks=["task"],
            intervention="treatment",
            comparator="control",
            outcome="metric",
            confirmatory_status=ConfirmatoryStatus.UNTOUCHED,
        )
    )
    new_envelope = repository.save_claim_envelope(
        ScientificClaimEnvelope(
            claim_envelope_id="claim-envelope-0000000000000000",
            study_id=study.study_id,
            plan_id=plan_id,
            allowed_claim="new append-only authority",
            population="frozen tasks",
            tasks=["task"],
            intervention="treatment",
            comparator="control",
            outcome="metric",
            confirmatory_status=ConfirmatoryStatus.ADAPTIVE_REUSE,
        )
    )
    common = {
        "study_id": study.study_id,
        "plan_id": plan_id,
        "handoff_id": "stage3-handoff-1111111111111111",
        "evaluation_ids": ["evaluation"],
        "evidence_edge_ids": ["edge"],
        "hypothesis_verdict_ids": ["hypothesis-verdict"],
        "study_verdict_id": "study-verdict",
        "qualification_status": QualificationStatus.QUALIFIED,
        "artifact_hashes": {"result.json": "0" * 64},
    }
    repository.save_stage3_completion(
        Stage3CompletionPackage(
            **common,
            completion_id="stage3-completion-ffffffffffffffff",
            claim_envelope_id=old_envelope.claim_envelope_id,
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    repository.save_stage3_completion(
        Stage3CompletionPackage(
            **common,
            completion_id="stage3-completion-0000000000000000",
            claim_envelope_id=new_envelope.claim_envelope_id,
            created_at="2026-01-02T00:00:00+00:00",
        )
    )

    completions = repository.list_stage3_completions(study.study_id)
    assert completions[-1].claim_envelope_id == new_envelope.claim_envelope_id
    authority = stage4_claim_authority(repository, study.study_id)
    assert authority["source_completion_id"] == completions[-1].completion_id
    assert authority["claims"][0]["allowed_claim"] == (
        "new append-only authority"
    )
