from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from research_forge.profiles import (
    ProfileCertificationStatus,
    ProfileMaturity,
    profile_bundle,
    profile_descriptor,
    resolve_profile_capability,
)
from research_forge.profiles.contracts import LLMEvaluationParameters
from research_forge.profiles.llm_evaluation import (
    evaluate_llm_response_contrast,
    normalize_answer,
    run_llm_response_evaluation,
)
from research_forge.profiles.llm_acceptance import (
    ControlledScienceBackend,
    LLM_PUBLICATION_REQUIRED_EVIDENCE_FACETS,
    ModelReply,
    run_llm_profile_acceptance,
    upgrade_llm_stage_four_evidence_handoff,
    verify_llm_completion,
)
from research_forge.workflow_domain import ProfileCapabilityStatus, Stage3Profile


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _parameters(**changes: object) -> LLMEvaluationParameters:
    payload: dict[str, object] = {
        "candidate_response_path": "responses.jsonl",
        "evaluator_reference_path": "references.jsonl",
        "task_id_field": "task_id",
        "baseline_response_field": "baseline",
        "treatment_response_field": "treatment",
        "reference_answers_field": "answers",
        "effect_threshold": 0.25,
    }
    payload.update(changes)
    return LLMEvaluationParameters.model_validate(payload)


def test_llm_response_profile_pairs_tasks_and_recovers_deterministic_gain(
    tmp_path: Path,
) -> None:
    _write_jsonl(
        tmp_path / "responses.jsonl",
        [
            {"task_id": "q1", "baseline": "Paris", "treatment": "Paris"},
            {"task_id": "q2", "baseline": "4.0", "treatment": " FOUR "},
            {"task_id": "q3", "baseline": "", "treatment": "北京"},
        ],
    )
    _write_jsonl(
        tmp_path / "references.jsonl",
        [
            {"task_id": "q1", "answers": ["paris"]},
            {"task_id": "q2", "answers": ["four"]},
            {"task_id": "q3", "answers": ["北京"]},
        ],
    )
    parameters = _parameters()
    baseline = run_llm_response_evaluation(parameters, arm="baseline", root=tmp_path)
    treatment = run_llm_response_evaluation(parameters, arm="treatment", root=tmp_path)
    contrast = evaluate_llm_response_contrast(baseline, treatment, parameters)

    assert baseline.normalized_exact_match == pytest.approx(1 / 3)
    assert treatment.normalized_exact_match == pytest.approx(1.0)
    assert contrast.effect == pytest.approx(2 / 3)
    assert contrast.verdict == "supported"
    assert normalize_answer("  ＦＯＵＲ\n") == "four"


def test_llm_evaluation_blocks_reference_leakage_and_task_mismatch(
    tmp_path: Path,
) -> None:
    payload = _parameters().model_dump(mode="json")
    with pytest.raises(ValidationError, match="must be separate"):
        LLMEvaluationParameters.model_validate(
            {**payload, "evaluator_reference_path": "responses.jsonl"}
        )
    _write_jsonl(
        tmp_path / "responses.jsonl",
        [{"task_id": "q1", "baseline": "a", "treatment": "a"}],
    )
    _write_jsonl(
        tmp_path / "references.jsonl",
        [{"task_id": "q2", "answers": ["a"]}],
    )
    with pytest.raises(ValueError, match="must match exactly"):
        run_llm_response_evaluation(_parameters(), arm="baseline", root=tmp_path)


def test_llm_profile_is_formally_admitted_only_by_acceptance_evidence() -> None:
    bundle = profile_bundle(Stage3Profile.LLM_EVALUATION_V1)
    descriptor = profile_descriptor(Stage3Profile.LLM_EVALUATION_V1)
    assert bundle.certification_status is ProfileCertificationStatus.CERTIFIED
    assert descriptor.maturity is ProfileMaturity.C2_DRY_RUN
    assert resolve_profile_capability(
        Stage3Profile.LLM_EVALUATION_V1,
        runnable_assets_present=True,
    ) is ProfileCapabilityStatus.SUPPORTED


def test_llm_profile_black_box_acceptance_hands_all_results_to_stage_four(
    tmp_path: Path,
) -> None:
    summary = run_llm_profile_acceptance(output_root=tmp_path)

    assert summary.overall == "PASS"
    assert summary.formal_calls == 300
    assert summary.evaluator_agreement == 1.0
    assert summary.claim_binding_coverage == 1.0
    assert summary.promotion_eligible is True
    run_root = Path(summary.completion_package)
    assert verify_llm_completion(run_root) is True
    assert summary.paper is None
    assert not (run_root / "manuscript.pdf").exists()
    assert not (run_root / "manuscript.tex").exists()
    handoff = json.loads(
        (run_root / "stage_four_evidence_handoff.json").read_text(encoding="utf-8")
    )
    assert handoff["formal_manuscript_authority"] == "canonical_stage_four_dag"
    assert handoff["manuscript_generated_by_profile"] is False
    assert handoff["adapter_complete"] is True
    assert handoff["required_claim_count"] >= 10
    assert handoff["publication_evidence_coverage"] == 1.0
    assert handoff["missing_evidence_facets"] == []
    assert set(handoff["covered_evidence_facets"]) >= set(
        LLM_PUBLICATION_REQUIRED_EVIDENCE_FACETS
    )
    assert {
        facet
        for binding in handoff["evidence_claim_map"]["bindings"]
        for facet in binding["evidence_facets"]
    } >= set(LLM_PUBLICATION_REQUIRED_EVIDENCE_FACETS)
    statements = " ".join(
        item["statement"] for item in handoff["evidence_claim_map"]["bindings"]
    )
    assert "95% paired-bootstrap interval" in statements
    assert "McNemar p-value" in statements
    assert "candidate model calls" in statements
    assert "only arm difference" in statements
    assert "missing or invalid response was scored as zero" in statements
    assert "unique item-arm calls" in statements
    assert "frozen seed" in statements
    assert (run_root / "profile_acceptance_report.html").is_file()
    trace = json.loads((run_root / "workflow_trace.json").read_text(encoding="utf-8"))
    assert [step["step_type"] for step in trace["steps"]] == [
        "scope_drafting",
        "profile_qualification",
        "contract_completion",
        "resource_resolution",
        "dry_run",
        "formal_run",
        "deterministic_evaluation",
        "scientific_verdict",
        "stage_four_evidence_adaptation",
        "stage_four_handoff_audit",
    ]
    responses = [json.loads(line) for line in (run_root / "responses.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(responses) == 300
    assert len({(row["item_id"], row["arm_id"]) for row in responses}) == 300
    assert all(row["evidence_class"] == "formal" for row in responses)


def test_legacy_llm_handoff_can_be_upgraded_without_rerunning_calls(
    tmp_path: Path,
) -> None:
    summary = run_llm_profile_acceptance(output_root=tmp_path)
    run_root = Path(summary.completion_package)
    responses_before = (run_root / "responses.jsonl").read_bytes()
    payload = json.loads(
        (run_root / "stage_four_evidence_handoff.json").read_text(encoding="utf-8")
    )
    method_claims = {
        "llm-profile-arm-definition",
        "llm-profile-scoring-and-pairing",
        "llm-profile-formal-execution-matrix",
        "llm-profile-statistical-procedure",
    }
    payload["mandatory_reporting_register"]["items"] = [
        item
        for item in payload["mandatory_reporting_register"]["items"]
        if item["claim_id"] not in method_claims
    ]
    payload["evidence_claim_map"]["bindings"] = [
        item
        for item in payload["evidence_claim_map"]["bindings"]
        if item["claim_id"] not in method_claims
    ]
    payload.update(
        {
            "required_claim_count": 6,
            "bound_claim_count": 6,
            "claim_binding_coverage": 1.0,
            "required_evidence_facets": [],
            "covered_evidence_facets": [],
            "missing_evidence_facets": [],
            "publication_evidence_coverage": 1.0,
            "adapter_complete": True,
        }
    )
    (run_root / "stage_four_evidence_handoff.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    upgraded = upgrade_llm_stage_four_evidence_handoff(run_root)

    assert upgraded.adapter_complete is True
    assert upgraded.publication_evidence_coverage == 1.0
    assert upgraded.missing_evidence_facets == []
    assert (run_root / "stage_four_evidence_handoff_v2.json").is_file()
    assert (run_root / "responses.jsonl").read_bytes() == responses_before
    assert upgrade_llm_stage_four_evidence_handoff(run_root) == upgraded


def test_llm_profile_acceptance_fails_closed_on_unknown_model_revision(
    tmp_path: Path,
) -> None:
    class BadRevisionBackend(ControlledScienceBackend):
        def invoke(self, **kwargs: object) -> ModelReply:
            reply = super().invoke(**kwargs)
            return reply.model_copy(update={"actual_revision": "unknown"})

    with pytest.raises(ValueError, match="DEGRADED_MODEL_PROVENANCE"):
        run_llm_profile_acceptance(
            output_root=tmp_path,
            backend=BadRevisionBackend(),
            item_count=5,
        )


def test_completion_replay_detects_response_or_hidden_target_tampering(
    tmp_path: Path,
) -> None:
    summary = run_llm_profile_acceptance(output_root=tmp_path, item_count=5)
    run_root = Path(summary.completion_package)
    responses_path = run_root / "responses.jsonl"
    original = responses_path.read_text(encoding="utf-8")
    responses_path.write_text(original.replace('"response": "A"', '"response": "B"', 1), encoding="utf-8")
    assert verify_llm_completion(run_root) is False
    responses_path.write_text(original, encoding="utf-8")
    answer_path = run_root / "evaluator_only" / "hidden_answers.json"
    answer_path.write_text(answer_path.read_text(encoding="utf-8").replace('"answer": "A"', '"answer": "B"', 1), encoding="utf-8")
    assert verify_llm_completion(run_root) is False


def test_formal_contract_rejects_target_placeholder_leakage() -> None:
    with pytest.raises(ValidationError, match="BLOCKED_TARGET_LEAKAGE"):
        _parameters(
            dataset_path="items.json",
            hidden_answer_path="answers.json",
            baseline_prompt="{question} {answer}",
            treatment_prompt="verify {question}",
            provider="provider",
            model_id="model",
            model_revision="revision",
            sample_count=150,
        )
