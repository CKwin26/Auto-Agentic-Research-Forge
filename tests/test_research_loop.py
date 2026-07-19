from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_forge.contracts import validate_proposal
from research_forge.models import (
    Direction,
    ExecutionContract,
    ExperimentProposal,
    FileReplacement,
    LiteratureCandidate,
    LiteratureApprovalRecord,
    LiteratureDiscoveryRecord,
    LiteratureReviewEnvelope,
    LiteratureSearchPlan,
    LiteratureScreeningDecision,
    LiteratureScreeningRecord,
    LiteratureSourceType,
    LiteratureSynthesis,
    MetricDefinition,
    NoveltyCandidate,
    ParameterOverride,
    PlanEvidenceBinding,
    ProposalEnvelope,
    RelatedWorkTheme,
    ResearchPlanDraft,
    Stage,
    Stage1Manifest,
)
from research_forge.runner import execute_run, promote_run, recover_run
from research_forge.service import (
    configure_project,
    create_project,
    freeze_literature_manifest,
    freeze_project,
    register_literature_source,
)
from research_forge.storage import (
    load_jsonl,
    load_state,
    read_json,
    save_state,
    sha256_file,
    write_json_atomic,
)
from research_forge.synthesis import (
    audit_synthesis,
    complete_project,
    synthesize_project,
    verify_completion_certificate,
)


def _plan() -> ResearchPlanDraft:
    return ResearchPlanDraft(
        title="Deterministic score study",
        research_question="Does the proposed controlled change improve the held-out score?",
        hypothesis="A bounded parameter change will improve score over the fixed baseline.",
        novelty_claim="The test isolates one controlled change in a reproducible loop.",
        scope_in=["single deterministic evaluator"],
        scope_out=["paper writing", "unbounded search"],
        method_outline=["register baseline", "run one-variable candidates"],
        datasets=["deterministic test fixture"],
        metrics=[
            MetricDefinition(
                name="score",
                description="Held-out deterministic score",
                direction=Direction.MAXIMIZE,
            )
        ],
        baseline_definition="The checked-in experiment with no parameter override.",
        ablation_axes=["score parameter"],
        confounders=["stale metrics files"],
        stop_conditions=["run budget exhausted"],
        risks=["toy fixture does not represent model training"],
        clarifying_questions=[],
        readiness_summary="The baseline and evaluator contract are concrete.",
        ready_to_freeze=True,
    )


def _frozen_project(tmp_path: Path) -> Path:
    project = create_project("Test Project", "Test a deterministic research loop", root=tmp_path)
    _register_test_source(project)
    state = load_state(project)
    state.stage = Stage.PLAN_REVIEW
    state.latest_plan_draft = "plan-ready"
    save_state(project, state)
    write_json_atomic(project / "plans" / "plan-ready.json", _plan())
    configure_project(
        project,
        primary_metric="score",
        direction=Direction.MAXIMIZE,
        entrypoint="run_experiment.py",
        timeout_seconds=30,
        max_runs=10,
        required_repeats=1,
        max_repeats=3,
        min_delta=0.0,
    )
    _seed_stage1_test_artifacts(project, "plan-ready")
    freeze_project(project)
    return project


def _register_test_source(project: Path) -> None:
    for index in range(1, 6):
        source_id = "fixture-paper" if index == 1 else f"fixture-paper-{index}"
        register_literature_source(
            project,
            source_id=source_id,
            source_type=LiteratureSourceType.PAPER,
            title=f"Deterministic research-loop fixture {index}",
            authors=[f"Test Author {index}"],
            year=2026,
            locator=f"https://doi.org/10.0000/research-forge-fixture-{index}",
            notes=f"Fixture {index} defines deterministic conditions used by the research loop.",
            verified=True,
            verification_method="Test fixture metadata checked against the local canonical record.",
        )


def _seed_stage1_test_artifacts(project: Path, plan_id: str) -> None:
    search_plan_id = "search-plan-fixture"
    search_plan = LiteratureSearchPlan(
        review_question="Which deterministic controls make a research loop reproducible?",
        queries=["deterministic research loop", "reproducible experiment controller"],
        key_concepts=["deterministic control", "reproducibility"],
        inclusion_criteria=["directly evaluates deterministic research workflows"],
        exclusion_criteria=["does not describe an executable research workflow"],
        scope_limitations=["The bounded fixture search is not an exhaustive systematic review."],
    )
    search_path = project / "literature" / "search_plans" / f"{search_plan_id}.json"
    write_json_atomic(search_path, search_plan)
    write_json_atomic(project / "literature" / "latest_search_plan.json", {"search_plan_id": search_plan_id})

    discovery_id = "discovery-fixture"
    discovery_dir = project / "literature" / "discoveries" / discovery_id
    raw_hashes: dict[str, str] = {}
    for provider in ("crossref", "semantic_scholar"):
        raw_path = discovery_dir / "raw" / f"search-{provider}.json"
        write_json_atomic(raw_path, {"provider": provider, "fixture": True})
        relative = raw_path.relative_to(project).as_posix()
        raw_hashes[relative] = sha256_file(raw_path)
    source_ids = ["fixture-paper", "fixture-paper-2", "fixture-paper-3", "fixture-paper-4", "fixture-paper-5"]
    candidates = [
        LiteratureCandidate(
            candidate_id=f"candidate-{index:016x}",
            title=f"Deterministic research-loop fixture {index}",
            authors=[f"Test Author {index}"],
            year=2026,
            abstract="This fixture provides enough metadata to exercise the deterministic Stage 1 audit.",
            venue="Fixture Journal",
            work_type="journal-article",
            doi=f"10.0000/research-forge-fixture-{index}",
            locator=f"https://doi.org/10.0000/research-forge-fixture-{index}",
            citation_count=index,
            provider_ids={"crossref": str(index), "semantic_scholar": str(index)},
            matched_queries=["deterministic research loop"],
            provider_scores={"crossref": 1.0 / index, "semantic_scholar": 1.0 / index},
            relevance_score=0.8,
            verification_status="cross_provider",
            verification_method="Matched across both deterministic fixture providers.",
            screening_status="included" if index <= 5 else "excluded",
            screening_reasons=["fixed deterministic fixture screening decision"],
        )
        for index in range(1, 9)
    ]
    discovery = LiteratureDiscoveryRecord(
        discovery_id=discovery_id,
        search_plan_id=search_plan_id,
        providers_requested=["crossref", "semantic_scholar"],
        provider_status={"crossref": "ok:2/2", "semantic_scholar": "ok:2/2"},
        raw_response_hashes=raw_hashes,
        candidates=candidates,
        included_source_ids=source_ids,
    )
    discovery_path = discovery_dir / "record.json"
    write_json_atomic(discovery_path, discovery)
    write_json_atomic(project / "literature" / "latest_discovery.json", {"discovery_id": discovery_id})
    screening = LiteratureScreeningRecord(
        screening_id="screening-fixture",
        discovery_id=discovery_id,
        model="test-fixture",
        evaluated_candidate_ids=[candidate.candidate_id for candidate in candidates],
        decisions=[
            LiteratureScreeningDecision(
                candidate_id=candidate.candidate_id,
                decision="core" if index <= 5 else "exclude",
                rationale="The deterministic fixture supplies a complete bounded screening decision.",
                criteria_matches=["deterministic research workflow"] if index <= 5 else [],
                concerns=[] if index <= 5 else ["outside the fixed fixture inclusion set"],
            )
            for index, candidate in enumerate(candidates, start=1)
        ],
        included_source_ids=source_ids,
    )
    screening_path = project / "literature" / "screening.json"
    write_json_atomic(screening_path, screening)

    synthesis = LiteratureSynthesis(
        review_scope="A deterministic fixture review of evidence-preserving research-loop controls.",
        themes=[
            RelatedWorkTheme(
                theme_id="theme-deterministic-controls",
                label="Deterministic controls",
                summary="The registered fixtures cover deterministic execution and evidence preservation.",
                source_ids=source_ids[:3],
            )
        ],
        novelty_candidates=[
            NoveltyCandidate(
                novelty_id="novelty-audited-loop",
                gap_statement="The bounded fixture set does not establish a complete audited loop.",
                proposed_question="Does an evidence-bound controller preserve reproducibility across every stage?",
                differentiator="The proposed fixture binds planning, execution, and synthesis artifacts by hash.",
                falsification_risk="A broader implementation may already provide the same verified bindings.",
                source_ids=source_ids[:3],
            )
        ],
        recommended_novelty_id="novelty-audited-loop",
        evidence_limitations=["Fixture records are synthetic and support tests rather than scientific claims."],
    )
    review = LiteratureReviewEnvelope(
        review_id="review-fixture",
        discovery_id=discovery_id,
        model="test-fixture",
        synthesis=synthesis,
    )
    review_path = project / "literature" / "review.json"
    write_json_atomic(review_path, review)
    (project / "literature" / "review.md").write_text("# Fixture review\n", encoding="utf-8")
    approval = LiteratureApprovalRecord(
        review_id=review.review_id,
        discovery_id=discovery_id,
        screening_id=screening.screening_id,
        review_hash=sha256_file(review_path),
        screening_hash=sha256_file(screening_path),
        selected_novelty_id="novelty-audited-loop",
        included_source_ids=source_ids,
        note="Approved deterministic Stage 1 fixture.",
    )
    approval_path = project / "literature" / "approval.json"
    write_json_atomic(approval_path, approval)
    hashes = {
        search_path.relative_to(project).as_posix(): sha256_file(search_path),
        discovery_path.relative_to(project).as_posix(): sha256_file(discovery_path),
        "literature/review.json": sha256_file(review_path),
        "literature/review.md": sha256_file(project / "literature" / "review.md"),
        "literature/screening.json": sha256_file(screening_path),
        "literature/approval.json": sha256_file(approval_path),
        **raw_hashes,
    }
    for source_id in source_ids:
        path = project / "literature" / "sources" / f"{source_id}.json"
        hashes[path.relative_to(project).as_posix()] = sha256_file(path)
    plan_path = project / "plans" / f"{plan_id}.json"
    binding = PlanEvidenceBinding(
        plan_id=plan_id,
        plan_hash=sha256_file(plan_path),
        review_id=review.review_id,
        review_hash=sha256_file(review_path),
        selected_novelty_id="novelty-audited-loop",
        source_ids=source_ids,
    )
    root_binding_path = project / "plan_evidence_binding.json"
    per_plan_binding_path = project / "plans" / f"{plan_id}-evidence.json"
    write_json_atomic(root_binding_path, binding)
    write_json_atomic(per_plan_binding_path, binding)
    hashes[f"plans/{plan_id}.json"] = sha256_file(plan_path)
    hashes[f"plans/{plan_id}-evidence.json"] = sha256_file(per_plan_binding_path)
    hashes["plan_evidence_binding.json"] = sha256_file(root_binding_path)
    write_json_atomic(
        project / "literature" / "stage1_manifest.json",
        Stage1Manifest(review_id=review.review_id, hashes=hashes),
    )


def _envelope(proposal_id: str, score: float) -> ProposalEnvelope:
    return ProposalEnvelope(
        proposal_id=proposal_id,
        model="test-model",
        proposal=ExperimentProposal(
            title=f"Set score to {score}",
            hypothesis="The controlled parameter will change the measured score.",
            rationale="This isolates a single variable and uses the fixed evaluator.",
            expected_observation=f"The score becomes {score}.",
            falsification_condition="The score does not change as specified.",
            success_criteria=["The primary score improves over the best run."],
            parameters=[
                ParameterOverride(
                    name="score",
                    value=score,
                    reason="Exercise one controlled scalar change.",
                )
            ],
            estimated_minutes=1,
        ),
        valid=True,
    )


def test_baseline_candidate_promotion_and_negative_evidence(tmp_path: Path) -> None:
    project = _frozen_project(tmp_path)
    baseline = execute_run(project)
    assert baseline.valid
    assert baseline.verdict == "baseline_verified"
    assert baseline.aggregate_metrics["score"] == pytest.approx(0.5)

    state = load_state(project)
    state.stage = Stage.EXPERIMENT_DESIGN
    save_state(project, state)
    improving = execute_run(project, envelope=_envelope("proposal-up", 0.7))
    assert improving.verdict == "candidate_improves"
    assert improving.improvement == pytest.approx(0.2)
    with pytest.raises(ValueError, match="confirmation"):
        promote_run(project, improving.run_id, "wrong-id")
    promotion = promote_run(project, improving.run_id, improving.run_id)
    assert "current_parameters.json" in promotion.files
    assert read_json(project / "current_parameters.json")["score"] == pytest.approx(0.7)

    non_improving = execute_run(project, envelope=_envelope("proposal-down", 0.6))
    assert non_improving.valid
    assert non_improving.verdict == "valid_non_improving"
    evidence = load_jsonl(project / "evidence.jsonl")
    assert [item["verdict"] for item in evidence] == [
        "baseline_verified",
        "candidate_improves",
        "valid_non_improving",
    ]


def test_frozen_contract_change_blocks_execution(tmp_path: Path) -> None:
    project = _frozen_project(tmp_path)
    contract = read_json(project / "execution_contract.json")
    contract["timeout_seconds"] = 31
    write_json_atomic(project / "execution_contract.json", contract)
    with pytest.raises(ValueError, match="frozen contract changed"):
        execute_run(project)


def test_frozen_literature_source_change_blocks_execution(tmp_path: Path) -> None:
    project = _frozen_project(tmp_path)
    source_path = project / "literature" / "sources" / "fixture-paper.json"
    source = read_json(source_path)
    source["notes"] = "Changed after freezing."
    write_json_atomic(source_path, source)
    with pytest.raises(ValueError, match="frozen contract changed"):
        execute_run(project)


def test_freeze_rejects_unverified_source(tmp_path: Path) -> None:
    project = create_project("Unverified Source", "Reject unverified sources", root=tmp_path)
    register_literature_source(
        project,
        source_id="unchecked-paper",
        source_type=LiteratureSourceType.PAPER,
        title="Unchecked paper metadata",
        authors=["Unknown Author"],
        year=2026,
        locator="https://example.invalid/paper",
        notes="This metadata has not been checked.",
        verified=False,
        verification_method="Pending operator verification.",
    )
    state = load_state(project)
    state.stage = Stage.PLAN_REVIEW
    state.latest_plan_draft = "plan-ready"
    save_state(project, state)
    write_json_atomic(project / "plans" / "plan-ready.json", _plan())
    configure_project(
        project,
        primary_metric="score",
        direction=Direction.MAXIMIZE,
        entrypoint="run_experiment.py",
        timeout_seconds=30,
        max_runs=3,
        required_repeats=1,
        max_repeats=1,
        min_delta=0.0,
    )
    with pytest.raises(ValueError, match="explicitly verified"):
        freeze_literature_manifest(project)


def test_proposal_path_escape_and_secret_are_rejected(tmp_path: Path) -> None:
    project = _frozen_project(tmp_path)
    contract = ExecutionContract.model_validate(read_json(project / "execution_contract.json"))
    proposal = ExperimentProposal(
        title="Unsafe replacement",
        hypothesis="This deliberately exercises deterministic path validation.",
        rationale="The model output must not escape the experiment directory.",
        expected_observation="The proposal is rejected.",
        falsification_condition="The proposal is accepted.",
        success_criteria=["Validation returns errors."],
        file_replacements=[
            FileReplacement(
                path="../state.json",
                reason="Attempt to escape",
                content='{"OPENAI_API_KEY": "sk-proj-not-a-real-key"}',
            )
        ],
        estimated_minutes=1,
    )
    errors = validate_proposal(project, proposal, contract)
    assert any("unsafe relative path" in error for error in errors)
    assert any("possible secret material" in error for error in errors)


def test_non_finite_parameter_is_rejected(tmp_path: Path) -> None:
    project = _frozen_project(tmp_path)
    contract = ExecutionContract.model_validate(read_json(project / "execution_contract.json"))
    proposal = _envelope("proposal-nan", float("nan")).proposal
    errors = validate_proposal(project, proposal, contract)
    assert any("parameter must be finite" in error for error in errors)


def test_api_key_is_removed_from_experiment_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = create_project("Secret Test", "Verify environment scrubbing", root=tmp_path)
    _register_test_source(project)
    script = '''from __future__ import annotations
import argparse, json, os
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--params", required=True)
p.add_argument("--metrics", required=True)
a = p.parse_args()
Path(a.metrics).write_text(json.dumps({"score": 0.0 if os.getenv("OPENAI_API_KEY") else 1.0}), encoding="utf-8")
'''
    (project / "experiment" / "run_experiment.py").write_text(script, encoding="utf-8")
    state = load_state(project)
    state.stage = Stage.PLAN_REVIEW
    state.latest_plan_draft = "plan-ready"
    save_state(project, state)
    write_json_atomic(project / "plans" / "plan-ready.json", _plan())
    configure_project(
        project,
        primary_metric="score",
        direction=Direction.MAXIMIZE,
        entrypoint="run_experiment.py",
        timeout_seconds=30,
        max_runs=3,
        required_repeats=1,
        max_repeats=1,
        min_delta=0.0,
    )
    _seed_stage1_test_artifacts(project, "plan-ready")
    freeze_project(project)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    baseline = execute_run(project)
    assert baseline.aggregate_metrics["score"] == pytest.approx(1.0)


def test_four_stage_loop_writes_audited_manuscript_and_certificate(tmp_path: Path) -> None:
    project = _frozen_project(tmp_path)
    baseline = execute_run(project)
    state = load_state(project)
    state.stage = Stage.EXPERIMENT_DESIGN
    save_state(project, state)
    improving = execute_run(project, envelope=_envelope("proposal-paper", 0.8))
    promote_run(project, improving.run_id, improving.run_id)

    audit = synthesize_project(project)
    assert audit.passed
    assert not audit.publication_ready
    assert "not isolation-verified" in " ".join(audit.publication_blockers)
    assert baseline.run_id in audit.evidence_run_ids
    assert improving.run_id in audit.evidence_run_ids

    certificate = complete_project(project)
    assert not certificate.publication_ready
    assert load_state(project).stage == Stage.COMPLETED
    assert (project / "synthesis" / "manuscript.md").is_file()
    assert (project / "completion_certificate.json").is_file()
    assert verify_completion_certificate(project)["passed"]

    claims = read_json(project / "synthesis" / "claims.json")
    best_claim = next(item for item in claims["claims"] if item["claim_id"] == "result-best-promoted")
    best_claim["reported_metrics"]["score"] = 123.0
    write_json_atomic(project / "synthesis" / "claims.json", claims)
    tampered = audit_synthesis(project)
    assert not tampered.passed
    assert any("misreports score" in item for item in tampered.violations)
    completion_audit = verify_completion_certificate(project)
    assert not completion_audit["passed"]
    assert any("claims.json" in item for item in completion_audit["violations"])


def test_invalid_baseline_is_preserved_in_evidence(tmp_path: Path) -> None:
    project = _frozen_project(tmp_path)
    script = (project / "experiment" / "run_experiment.py").read_text(encoding="utf-8")
    # Mutating after freeze is allowed for experiment code, but the invalid run remains evidence.
    (project / "experiment" / "run_experiment.py").write_text(
        script.replace('json.dumps({"score": score}', 'json.dumps({"wrong_metric": score}'),
        encoding="utf-8",
    )
    record = execute_run(project)
    assert not record.valid
    assert record.verdict == "invalid"
    evidence = load_jsonl(project / "evidence.jsonl")
    assert len(evidence) == 1
    assert evidence[0]["valid"] is False
    assert "required metric is missing" in evidence[0]["error"]


def test_file_replacement_runs_in_copy_until_human_promotion(tmp_path: Path) -> None:
    project = _frozen_project(tmp_path)
    baseline = execute_run(project)
    assert baseline.valid
    canonical = project / "experiment" / "run_experiment.py"
    before = canonical.read_text(encoding="utf-8")
    replacement = before.replace('params.get("score", 0.50)', 'params.get("score", 0.80)')
    proposal = ExperimentProposal(
        title="Raise the default candidate score",
        hypothesis="The isolated replacement will improve the deterministic score.",
        rationale="This test exercises the full-file replacement execution path.",
        expected_observation="The score rises from 0.5 to 0.8.",
        falsification_condition="The score does not exceed 0.5.",
        success_criteria=["The fixed evaluator reports score above baseline."],
        file_replacements=[
            FileReplacement(
                path="run_experiment.py",
                reason="Change one default scalar in the run copy.",
                content=replacement,
            )
        ],
        estimated_minutes=1,
    )
    envelope = ProposalEnvelope(
        proposal_id="proposal-file",
        model="test-model",
        proposal=proposal,
        valid=True,
    )
    state = load_state(project)
    state.stage = Stage.EXPERIMENT_DESIGN
    save_state(project, state)
    candidate = execute_run(project, envelope=envelope)
    assert candidate.verdict == "candidate_improves"
    assert canonical.read_text(encoding="utf-8") == before
    promotion = promote_run(project, candidate.run_id, candidate.run_id)
    assert "run_experiment.py" in promotion.files
    assert canonical.read_text(encoding="utf-8") == replacement


def test_explicit_recovery_preserves_abandoned_run_as_evidence(tmp_path: Path) -> None:
    project = _frozen_project(tmp_path)
    state = load_state(project)
    state.active_run_id = "baseline-interrupted"
    save_state(project, state)
    run_dir = project / "runs" / "baseline-interrupted"
    run_dir.mkdir(parents=True)
    write_json_atomic(run_dir / "parameters.json", {})
    write_json_atomic(
        run_dir / "manifest.json",
        {
            "run_id": "baseline-interrupted",
            "proposal_id": None,
            "is_baseline": True,
            "contract_hash": "contract-test",
            "code_hash": "code-test",
            "changed_files": [],
            "repeats": 1,
        },
    )
    with pytest.raises(ValueError, match="confirmation"):
        recover_run(project, "wrong")
    record = recover_run(project, "baseline-interrupted")
    assert not record.valid
    assert "abandoned" in (record.error or "")
    state = load_state(project)
    assert state.active_run_id is None
    assert state.stage == Stage.BASELINE_PENDING
    evidence = load_jsonl(project / "evidence.jsonl")
    assert evidence[0]["run_id"] == "baseline-interrupted"
    assert evidence[0]["valid"] is False
