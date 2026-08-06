from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from research_forge.profiles import (
    PROFILE_BUNDLE_REGISTRY,
    ProfileEvidencePointer,
    ProfileReportingFinding,
    ProfileStageFourEvidenceInput,
    STAGE_FOUR_EVIDENCE_ADAPTER_ID,
    build_stage_four_evidence_handoff,
    persist_stage_four_evidence_handoff,
    queue_stage_four_from_profile_handoff,
)
from research_forge.stage_four import (
    _ACTIVE_STAGE4_REVISION,
    _stage_four_evidence_handoff,
    stage_four_handlers,
)
from research_forge.workflow_domain import EntryMode, WorkflowRepository


def _pointer() -> ProfileEvidencePointer:
    return ProfileEvidencePointer(path="statistics.json", sha256="a" * 64)


def _input() -> ProfileStageFourEvidenceInput:
    return ProfileStageFourEvidenceInput(
        study_id="study-profile-adapter",
        profile_id="example_profile_v1",
        profile_version="1.0.0",
        source_claim_envelope_id="claim-envelope-0123456789abcdef",
        frozen_conclusion="The frozen comparison was inconclusive.",
        findings=[
            ProfileReportingFinding(
                claim_id="primary-effect",
                reporting_item_id="report-primary-effect",
                category="primary",
                statement="The paired effect was 0.10 with a frozen interval.",
                evidence=[_pointer()],
                required_destination="main_text",
                destination_section="results",
                rationale="The primary registered result must be visible in the Results section.",
                claim_strength="comparative",
            ),
            ProfileReportingFinding(
                claim_id="external-validity-limit",
                reporting_item_id="report-external-validity-limit",
                category="limitation",
                statement="The controlled sample does not represent deployment traffic.",
                required_destination="limitations",
                destination_section="limitations",
                rationale="The sampling boundary limits external validity and must be disclosed.",
                claim_strength="limitation",
            ),
        ],
    )


def test_every_registered_profile_declares_the_canonical_stage_four_adapter() -> None:
    assert PROFILE_BUNDLE_REGISTRY
    assert {
        bundle.stage_four_evidence_adapter_id
        for bundle in PROFILE_BUNDLE_REGISTRY.values()
    } == {STAGE_FOUR_EVIDENCE_ADAPTER_ID}


def test_adapter_builds_matching_reporting_and_claim_contracts(tmp_path: Path) -> None:
    handoff = build_stage_four_evidence_handoff(_input())
    assert handoff.adapter_complete is True
    assert handoff.claim_binding_coverage == 1.0
    assert handoff.manuscript_generated_by_profile is False
    assert handoff.formal_manuscript_authority == "canonical_stage_four_dag"
    assert handoff.mandatory_reporting_register.required_claim_ids() == {
        item.claim_id for item in handoff.evidence_claim_map.bindings
    }

    hashes = persist_stage_four_evidence_handoff(tmp_path, handoff)
    assert set(hashes) == {
        "handoff",
        "mandatory_reporting_register",
        "evidence_claim_map",
    }
    persisted = json.loads(
        (tmp_path / "stage_four_evidence_handoff.json").read_text(encoding="utf-8")
    )
    assert persisted["adapter_complete"] is True
    assert not (tmp_path / "manuscript.pdf").exists()


def test_adapter_rejects_material_scientific_claim_without_evidence() -> None:
    with pytest.raises(ValidationError, match="hash-bound evidence pointer"):
        ProfileReportingFinding(
            claim_id="unsupported-primary",
            reporting_item_id="report-unsupported-primary",
            category="primary",
            statement="The treatment improved the outcome.",
            required_destination="main_text",
            destination_section="results",
            rationale="A material result cannot enter Stage 4 without frozen supporting evidence.",
        )


def test_complete_profile_handoff_enters_the_canonical_stage_four_dag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = WorkflowRepository(tmp_path / "repository")
    project = repository.create_project("Profile Stage 4 adapter")
    study = repository.create_study(
        project.project_id,
        "Common Stage 4",
        entry_mode=EntryMode.IDEA_TO_PAPER,
        study_id="study-profile-adapter",
    )
    handoff = build_stage_four_evidence_handoff(_input())
    authority = {
        "source_completion_id": "stage3-completion-adapter-test",
        "claims": [
            {"claim_envelope_id": "claim-envelope-0123456789abcdef"}
        ],
    }
    monkeypatch.setattr(
        "research_forge.stage_four.stage4_claim_authority",
        lambda _repository, _study_id: authority,
    )

    steps = queue_stage_four_from_profile_handoff(repository, handoff)

    assert steps
    assert {step.step_type for step in steps} >= {
        "mandatory_reporting_register",
        "evidence_claim_mapping",
        "draft_generation",
        "pdf_compile_and_verify",
    }
    context = type(
        "Context",
        (),
        {"repository": repository, "study_id": study.study_id},
    )()
    loaded = _stage_four_evidence_handoff(context)
    assert loaded is not None
    assert loaded["formal_manuscript_authority"] == "canonical_stage_four_dag"

    handler_context = type(
        "HandlerContext",
        (),
        {
            "repository": repository,
            "study_id": study.study_id,
            "step": SimpleNamespace(parameters={"stage4_workflow_revision": 1}),
            "result": lambda _self, key: authority
            if key == "stage4_claim_intake"
            else {},
        },
    )()
    handlers = stage_four_handlers()
    reporting = handlers["mandatory_reporting_register"](handler_context)
    mapping = handlers["evidence_claim_mapping"](handler_context)
    assert (
        reporting["stage_four_evidence_adapter_id"]
        == STAGE_FOUR_EVIDENCE_ADAPTER_ID
    )
    assert (
        mapping["stage_four_evidence_adapter_id"]
        == STAGE_FOUR_EVIDENCE_ADAPTER_ID
    )
    assert {
        item["claim_id"]
        for item in reporting["mandatory_reporting_register"]["items"]
    } == {
        item["claim_id"] for item in mapping["evidence_claim_map"]["bindings"]
    }


def test_manuscript_revision_reuses_canonical_profile_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = WorkflowRepository(tmp_path / "repository")
    project = repository.create_project("Profile revision handoff")
    study = repository.create_study(
        project.project_id,
        "Revised manuscript",
        entry_mode=EntryMode.IDEA_TO_PAPER,
        study_id="study-profile-adapter",
    )
    handoff = build_stage_four_evidence_handoff(_input())
    authority = {
        "source_completion_id": "stage3-completion-adapter-test",
        "claims": [
            {
                "claim_envelope_id": "claim-envelope-0123456789abcdef",
                "allowed_claim": "A generic envelope statement.",
                "confirmatory_status": "confirmatory_used",
                "intervention": "the treatment",
                "outcome": "the outcome",
                "comparator": "the baseline",
                "tasks": ["task"],
                "known_limitations": [],
                "prohibited_generalizations": [],
                "effect_estimate": 0.1,
                "population": "the frozen population",
                "evidence_level": "confirmatory",
            }
        ],
    }
    monkeypatch.setattr(
        "research_forge.stage_four.stage4_claim_authority",
        lambda _repository, _study_id: authority,
    )
    queue_stage_four_from_profile_handoff(repository, handoff)

    context = SimpleNamespace(
        repository=repository,
        study_id=study.study_id,
        step=SimpleNamespace(parameters={"stage4_workflow_revision": 2}),
        result=lambda key: (
            authority
            if key == "stage4_claim_intake"
            else stage_four_handlers()["mandatory_reporting_register"](context)
            if key == "mandatory_reporting_register"
            else {}
        ),
    )
    token = _ACTIVE_STAGE4_REVISION.set(2)
    try:
        loaded = _stage_four_evidence_handoff(context)
        assert loaded is not None
        assert (
            loaded["evidence_claim_map"]["frozen_conclusion"]
            == _input().frozen_conclusion
        )

        reporting = stage_four_handlers()["mandatory_reporting_register"](context)
        context.result = lambda key: (
            authority
            if key == "stage4_claim_intake"
            else reporting
            if key == "mandatory_reporting_register"
            else {}
        )
        contribution = stage_four_handlers()["contribution_candidate_generation"](
            context
        )
        assert (
            contribution["publication_narrative_contract_draft"]["central_thesis"]
            == _input().frozen_conclusion
        )
    finally:
        _ACTIVE_STAGE4_REVISION.reset(token)
