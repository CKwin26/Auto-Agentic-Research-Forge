from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from research_forge.cli import _parser
from research_forge.models import (
    Direction,
    LiteratureSource,
    LiteratureSourceType,
    MetricDefinition,
    PlanEvidenceBinding,
    ProjectState,
    ResearchPlanDraft,
    Stage,
)
from research_forge.storage import read_json, sha256_file, write_json_atomic
from research_forge.publication_readiness import freeze_publication_experiment_target
from research_forge.study import (
    STUDY_SECONDARY_METRICS,
    archive_failed_stage2_protocol,
    STUDY_TASKS,
    audit_stage2_protocol,
    freeze_stage2_protocol,
    supersede_empty_stage2_protocol,
)
from research_forge.study_models import (
    ClaimVerdict,
    SemanticClaimJudgment,
    SemanticClaimJudgmentBatch,
    Stage2ArmDefinition,
    Stage2Cell,
    Stage2ManualAuditPlan,
    Stage2Protocol,
    Stage2TaskBinding,
    StudyArm,
    StudyClaim,
    StudyClaimRegistry,
    StudyClaimType,
    StudyFinalizerClaim,
    StudyFinalizerMetric,
)
from research_forge.study_runner import (
    _canonical_finalizer_metric_values,
    _normalize_registry_artifacts,
    _validate_judgments,
    _validate_revision,
    audit_registry_structure,
)
from research_forge import publication_synthesis
from research_forge.publication_synthesis import _render_latex, publication_layout_gate


def test_finalizer_collapses_identical_duplicate_metrics_and_preserves_conflicts_for_evidence() -> None:
    claim = StudyFinalizerClaim(
        claim_id="claim-1",
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The protected run recorded Accuracy equal to 0.5.",
        experiment_run_id="run-1",
        metric_values=[
            StudyFinalizerMetric(name="Accuracy", value=0.5),
            StudyFinalizerMetric(name="Accuracy", value=0.5),
        ],
        artifact_paths=["record.json"],
    )
    assert [(item.name, item.value) for item in claim.metric_values] == [("Accuracy", 0.5)]
    conflicting = StudyFinalizerClaim(
        claim_id="claim-1",
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The protected run recorded an Accuracy value.",
        experiment_run_id="run-1",
        metric_values=[
            StudyFinalizerMetric(name="Accuracy", value=0.5),
            StudyFinalizerMetric(name="Accuracy", value=0.6),
        ],
        artifact_paths=["record.json"],
    )
    assert [(item.name, item.value) for item in conflicting.metric_values] == [
        ("Accuracy", 0.5),
        ("Accuracy", 0.6),
    ]


def test_conflicting_metric_is_resolved_only_by_linked_protected_run() -> None:
    claim = StudyFinalizerClaim(
        claim_id="claim-1",
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The linked run recorded its protected Accuracy.",
        experiment_run_id="candidate-run",
        metric_values=[
            StudyFinalizerMetric(name="Accuracy", value=0.5),
            StudyFinalizerMetric(name="Accuracy", value=0.6),
        ],
        artifact_paths=["record.json"],
    )
    packet = [
        {
            "run_id": "candidate-run",
            "aggregate_metrics": {"Accuracy": 0.6},
            "improvement": 0.1,
        }
    ]
    changes: list[dict[str, object]] = []
    assert _canonical_finalizer_metric_values(
        claim, packet, normalization_log=changes
    ) == {"Accuracy": 0.6}
    assert changes[0]["removed_cross_run_values"] == [0.5]


def test_conflicting_metric_without_unique_protected_match_fails_closed() -> None:
    claim = StudyFinalizerClaim(
        claim_id="claim-1",
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The linked run recorded an Accuracy value.",
        experiment_run_id="candidate-run",
        metric_values=[
            StudyFinalizerMetric(name="Accuracy", value=0.5),
            StudyFinalizerMetric(name="Accuracy", value=0.6),
        ],
        artifact_paths=["record.json"],
    )
    with pytest.raises(ValueError, match="cannot be uniquely resolved"):
        _canonical_finalizer_metric_values(
            claim,
            [{"run_id": "candidate-run", "aggregate_metrics": {"Accuracy": 0.7}}],
        )


def test_publication_protocol_supports_dynamic_eight_by_five_factorial() -> None:
    task_ids = [f"publication-task-{index}" for index in range(8)]
    seeds = list(range(5))
    tasks = [
        Stage2TaskBinding(
            task_id=task_id,
            snapshot_path=f"stage2/task_packs/{task_id}",
            task_hash=f"{index + 1:064x}",
            primary_metric="score",
            direction=Direction.MAXIMIZE,
            baseline_score=0.0,
            target_score=1.0,
            iteration_cap=1,
            timeout_seconds=120,
        )
        for index, task_id in enumerate(task_ids)
    ]
    cells = []
    for sequence, (arm, task_id, seed) in enumerate(
        (
            (arm, task_id, seed)
            for arm in (StudyArm.BASELINE, StudyArm.TREATMENT)
            for task_id in task_ids
            for seed in seeds
        ),
        start=1,
    ):
        cells.append(
            Stage2Cell(
                sequence=sequence,
                cell_id=f"{arm.value}--{task_id}--seed-{seed}",
                arm=arm,
                task_id=task_id,
                seed=seed,
            )
        )
    protocol = Stage2Protocol(
        protocol_id="stage2-123456789abc",
        study_intent="publication",
        publication_contract_id="pubexp-1234567890abcdef",
        title="Prospective publication factorial",
        research_question="Does the frozen gate improve claim evidence quality prospectively?",
        hypothesis="Shared-artifact treatment branches will reduce unsupported claims.",
        plan_id="plan-publication",
        plan_hash="a" * 64,
        review_id="review-publication",
        review_hash="b" * 64,
        selected_novelty_id="novelty-02",
        backbone_manifest_hash="c" * 64,
        arms=[
            Stage2ArmDefinition(
                arm=StudyArm.BASELINE,
                initial_finalizer="Use the shared artifact without verifier-guided revision or removal.",
                verification_gate="disabled",
                revision_limit=0,
                removal_after_failed_recheck=False,
            ),
            Stage2ArmDefinition(
                arm=StudyArm.TREATMENT,
                initial_finalizer="Use the same shared artifact before one verifier-guided revision cycle.",
                verification_gate="reject_revise_recheck",
                revision_limit=1,
                removal_after_failed_recheck=True,
            ),
        ],
        tasks=tasks,
        seeds=seeds,
        cells=cells,
        secondary_metrics=[
            *STUDY_SECONDARY_METRICS,
            "claim_retention_or_deletion",
            "semantic_change_type",
            "informativeness_or_usefulness",
        ],
        counterfactual_source="shared_run_artifact",
        branch_order="pair_randomized",
        pair_branch_order={
            f"{task_id}--seed-{seed}": (
                "baseline_first" if seed % 2 == 0 else "treatment_first"
            )
            for task_id in task_ids
            for seed in seeds
        },
            pair_execution_concurrency=2,
            independent_calibration_contract="design_revisions/independent_calibration_contract.json",
            secondary_evaluator_contract="design_revisions/secondary_evaluator_contract.json",
            evidence_gate_specification={
                "claim_extraction_prompt_sha256": "a" * 64,
                "evidence_matching_prompt_sha256": "b" * 64,
                "decision_policy": "reject_revise_recheck_once_then_remove",
                "allowed_actions": ["retain", "revise", "remove", "abstain"],
                "decision_trace_schema": "study_gate_trace.v1",
            },
            construct_analysis_requirements=[
                "claim_retention_deletion",
                "semantic_change_distribution",
                "informativeness_usefulness",
                "per_task_effects",
            ],
            pair_level_table=True,
            telemetry_contract={"wall_clock_definition": "active_attempt_seconds"},
            manual_audit=Stage2ManualAuditPlan(total_claims=128),
        max_completed_cells=80,
        stop_conditions=["Stop after 80 cells.", "Fail closed on integrity drift."],
    )
    assert len(protocol.cells) == 80
    assert protocol.max_completed_cells == 80
    assert protocol.pair_execution_concurrency == 2


class FakeControlledRuntime:
    name = "docker"

    def attestation(self, *, evaluator_separated: bool) -> dict[str, object]:
        assert evaluator_separated
        return {
            "runtime": "docker",
            "isolation_verified": True,
            "candidate_evaluator_separated": True,
            "network": "none",
            "read_only_rootfs": True,
            "capabilities_dropped": True,
            "no_new_privileges": True,
            "image": "rf-airs-cpu:test",
            "image_id": "sha256:" + "a" * 64,
            "controlled_environment": True,
            "capability_verified": True,
            "capabilities": {
                "controlled": True,
                "verified": True,
                "environment": "airs-cpu",
                "version": "test",
            },
            "limits": {
                "kind": "docker",
                "image": "rf-airs-cpu:test",
                "cpus": 1.0,
                "memory_mb": 2048,
                "pids_limit": 256,
                "tmpfs_mb": 512,
                "max_output_mb": 256,
            },
        }


def test_study_freeze_cli_defaults_to_publication() -> None:
    args = _parser().parse_args(["study", "freeze", "future-project"])
    assert args.intent == "publication"
    assert args.pilot_reason is None


def test_publication_matrix_cli_exposes_dedicated_evaluation_and_pair_audit() -> None:
    parser = _parser()
    evaluate = parser.parse_args(
        ["study", "evaluate-publication", "future-publication-project"]
    )
    pair_audit = parser.parse_args(
        ["study", "audit-publication-pairs", "future-publication-project"]
    )

    assert evaluate.study_command == "evaluate-publication"
    assert pair_audit.study_command == "audit-publication-pairs"


def test_publication_matrix_cli_exposes_synthesis_and_audit() -> None:
    parser = _parser()
    synthesis = parser.parse_args(
        ["study", "synthesize-publication", "future-publication-project"]
    )
    audit = parser.parse_args(
        ["study", "audit-publication-synthesis", "future-publication-project"]
    )

    assert synthesis.study_command == "synthesize-publication"
    assert audit.study_command == "audit-publication-synthesis"


def test_publication_layout_gate_rejects_score_only_or_acceptance_estimate(tmp_path: Path) -> None:
    readiness = tmp_path / "readiness.json"
    write_json_atomic(
        readiness,
        {
            "readiness_score": 0.80,
            "readiness_threshold": 0.60,
            "automated_hard_gate_passed": False,
            "automated_publication_gate_passed": False,
            "publication_submission_ready": False,
            "estimated_acceptance_probability": {"center": 0.90},
        },
    )
    with pytest.raises(ValueError, match="publication layout is blocked"):
        publication_layout_gate(readiness)


def test_publication_layout_gate_allows_review_artifacts_but_not_external_submission_when_human_is_pending(tmp_path: Path) -> None:
    readiness = tmp_path / "readiness.json"
    write_json_atomic(
        readiness,
        {
            "readiness_score": 0.80,
            "readiness_threshold": 0.60,
            "automated_hard_gate_passed": True,
            "automated_publication_gate_passed": True,
            "human_gate_pending": True,
            "publication_submission_ready": False,
            "estimated_acceptance_probability": {"center": 0.99},
        },
    )

    result = publication_layout_gate(readiness)

    assert result["layout_allowed"] is True
    assert result["pdf_allowed"] is True
    assert result["review_submission_package_allowed"] is True
    assert result["external_submission_allowed"] is False


def test_publication_layout_gate_binds_review_to_current_project_manuscript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    synthesis = project / "synthesis"
    synthesis.mkdir(parents=True)
    manuscript = synthesis / "publication_manuscript.md"
    manuscript.write_text("# frozen evidence manuscript\n", encoding="utf-8")
    contract = synthesis / "publication_target_contract.json"
    contract.write_text("{}\n", encoding="utf-8")
    readiness = synthesis / "publication_readiness.json"
    write_json_atomic(
        readiness,
        {
            "readiness_score": 0.80,
            "readiness_threshold": 0.60,
            "automated_hard_gate_passed": True,
            "automated_publication_gate_passed": True,
            "human_gate_pending": True,
            "publication_submission_ready": False,
            "manuscript_path": str(manuscript.resolve()),
            "manuscript_sha256": sha256_file(manuscript),
            "contract_path": str(contract.resolve()),
        },
    )
    monkeypatch.setattr(
        publication_synthesis,
        "_preflight",
        lambda value: ({"complete": True, "passed": True}, {"pair_count": 40}),
    )

    assert publication_layout_gate(readiness, project=project)["layout_allowed"] is True

    manuscript.write_text("# edited after review\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after the fixed-venue readiness review"):
        publication_layout_gate(readiness, project=project)


def test_publication_latex_renderer_preserves_subsections_and_citation_structure() -> None:
    latex = _render_latex(
        "# Title\n\n## Methods\n\n### Freeze order\n\nText [R1].\n\n"
        "## References\n\n- [R1] Example reference.\n\n## Discussion\n"
    )

    assert "\\subsection{Freeze order}" in latex
    assert "\\cite{R1}" in latex
    assert "\\begin{thebibliography}{99}" in latex
    assert "\\bibitem{R1}" in latex


def test_publication_manuscript_keeps_references_out_of_related_work() -> None:
    source = Path(publication_synthesis.__file__).read_text(encoding="utf-8")
    related_start = source.index("## Related Work and Registered Sources")
    references_start = source.index("## References", related_start)
    related_template = source[related_start:references_start]

    assert "{refs}" not in related_template
    related_detail_start = source.index("related_detail = f")
    related_detail_end = source.index('"""', related_detail_start + len("related_detail = f\"\"\""))
    related_detail = source[related_detail_start:related_detail_end]
    assert "{autonomous_work_citations}" in related_detail
    assert "{verification_citations}" in related_detail


def _stage1_ready_project(tmp_path: Path) -> Path:
    project = tmp_path / "study-project"
    (project / "plans").mkdir(parents=True)
    (project / "literature" / "sources").mkdir(parents=True)
    write_json_atomic(project / "state.json", ProjectState(stage=Stage.PLAN_REVIEW, revision=1))

    metrics = [
        MetricDefinition(
            name="unsupported_claim_rate",
            description="Fraction of final factual conclusion claims that are unsupported.",
            direction=Direction.MINIMIZE,
        )
    ]
    for name in STUDY_SECONDARY_METRICS:
        metrics.append(
            MetricDefinition(
                name=name,
                description=f"Preregistered secondary study metric for {name}.",
                direction=(
                    Direction.MAXIMIZE
                    if name in {"citation_correctness", "evidence_coverage", "task_native_score"}
                    else Direction.MINIMIZE
                ),
            )
        )
    plan = ResearchPlanDraft(
        title="Paired claim-evidence gate ablation",
        research_question="Does a claim-evidence gate reduce unsupported final research claims?",
        hypothesis="The gate will reduce unsupported claims while preserving task-native performance.",
        novelty_claim="A bounded same-backbone intervention study selected as novelty-02.",
        scope_in=["two arms", "three AIRS-lite tasks", "three seeds"],
        scope_out=["universal generalization"],
        method_outline=["freeze the backbone", "run the paired factorial design"],
        datasets=[f"AIRS-lite task pack: {task_id}" for task_id in STUDY_TASKS],
        metrics=metrics,
        baseline_definition=(
            "Baseline = the same frozen Research Forge/Codex backbone with the shared output schema, "
            "but with no verifier-based rejection or revision."
        ),
        ablation_axes=["gate absent versus present"],
        confounders=["claim suppression"],
        stop_conditions=["stop after exactly 18 cells", "fail closed on integrity drift"],
        risks=["the task suite is narrow"],
        clarifying_questions=[],
        readiness_summary="The paired design, metrics, tasks, seeds, and stop rules are fixed.",
        ready_to_freeze=True,
    )
    plan_id = "plan-stage2-test"
    plan_path = project / "plans" / f"{plan_id}.json"
    write_json_atomic(plan_path, plan)
    state = ProjectState.model_validate(read_json(project / "state.json"))
    state.latest_plan_draft = plan_id
    write_json_atomic(project / "state.json", state)

    source = LiteratureSource(
        source_id="paper-stage2-test",
        source_type=LiteratureSourceType.PAPER,
        title="Evidence verification for autonomous research agents",
        authors=["Test Author"],
        year=2025,
        locator="https://example.test/study",
        notes="The frozen abstract reports claim-level evidence verification.",
        verified=True,
        verification_method="Verified in the deterministic Stage 2 fixture.",
    )
    write_json_atomic(project / "literature" / "sources" / "paper-stage2-test.json", source)
    write_json_atomic(project / "literature" / "review.json", {"review_id": "review-stage2-test"})
    write_json_atomic(
        project / "literature" / "approval.json",
        {"review_id": "review-stage2-test", "selected_novelty_id": "novelty-02"},
    )
    binding = PlanEvidenceBinding(
        plan_id=plan_id,
        plan_hash=sha256_file(plan_path),
        review_id="review-stage2-test",
        review_hash="b" * 64,
        selected_novelty_id="novelty-02",
        source_ids=["paper-stage2-test"],
    )
    write_json_atomic(project / "plan_evidence_binding.json", binding)
    (project / "events.jsonl").write_text("", encoding="utf-8")
    return project


def test_freeze_stage2_protocol_creates_exact_factorial_and_hash_gate(
    tmp_path: Path, monkeypatch
) -> None:
    project = _stage1_ready_project(tmp_path)
    monkeypatch.setattr(
        "research_forge.study.audit_stage1",
        lambda *_args, **_kwargs: SimpleNamespace(passed=True, violations=[]),
    )
    monkeypatch.setattr(
        "research_forge.study.backend_status",
        lambda: {
            "codex_sdk_installed": True,
            "codex_authenticated": True,
            "codex_sdk_version": "test",
            "codex_account_type": "chatgpt",
            "codex_plan_type": "test",
        },
    )
    protocol = freeze_stage2_protocol(
        project,
        runtime=FakeControlledRuntime(),
        intent="pilot",
        pilot_reason="Exercise the legacy deterministic Stage 2 fixture only.",
    )
    assert len(protocol.cells) == 18
    assert [cell.sequence for cell in protocol.cells] == list(range(1, 19))
    assert [cell.arm for cell in protocol.cells[:9]] == [StudyArm.BASELINE] * 9
    assert [cell.arm for cell in protocol.cells[9:]] == [StudyArm.TREATMENT] * 9
    assert [task.task_id for task in protocol.tasks] == STUDY_TASKS
    assert all(task.iteration_cap == 1 for task in protocol.tasks)
    assert protocol.manual_audit.total_claims == 48
    assert protocol.study_intent == "pilot"
    assert protocol.publication_contract_id is None
    pilot_intent = read_json(project / "stage2" / "pilot_intent.json")
    assert pilot_intent["publication_submission_ready"] is False
    assert "publication" in pilot_intent["prohibited_routes"]
    assert read_json(project / "state.json")["stage"] == "baseline_pending"
    design_preflight = read_json(project / "stage2" / "design_preflight.json")
    assert design_preflight["target"] == "execution"
    assert design_preflight["gate_passed"] is True
    assert design_preflight["maximum_claim_tier"] == "internal_proxy_association"
    assert design_preflight["publication_submission_ready"] is False

    audit = audit_stage2_protocol(project)
    assert audit.passed
    assert audit.planned_cells == 18
    assert audit.docker_isolation_frozen

    prompt = project / "stage2" / "prompts" / "study_finalizer.md"
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    tampered = audit_stage2_protocol(project)
    assert not tampered.passed
    assert not tampered.checks["prompt_stack_unchanged"]
    assert not tampered.checks["protected_artifacts_unchanged"]
    assert not tampered.checks["frozen_artifacts_unchanged"]
    archived = supersede_empty_stage2_protocol(
        project,
        reason="Pre-data test correction adds a missing frozen control-plane hash.",
    )
    assert archived.is_dir()
    assert not (project / "stage2").exists()
    assert read_json(project / "state.json")["stage"] == "plan_review"


def test_archive_failed_protocol_recovers_result_review_after_directory_relocation(
    tmp_path: Path, monkeypatch
) -> None:
    project = _stage1_ready_project(tmp_path)
    monkeypatch.setattr(
        "research_forge.study.audit_stage1",
        lambda *_args, **_kwargs: SimpleNamespace(passed=True, violations=[]),
    )
    monkeypatch.setattr(
        "research_forge.study.backend_status",
        lambda: {
            "codex_sdk_installed": True,
            "codex_authenticated": True,
            "codex_sdk_version": "test",
            "codex_account_type": "chatgpt",
            "codex_plan_type": "test",
        },
    )
    protocol = freeze_stage2_protocol(
        project,
        runtime=FakeControlledRuntime(),
        intent="pilot",
        pilot_reason="Exercise archive behavior after formal cells are complete.",
    )
    write_json_atomic(project / "stage2" / "r" / "b" / "01" / "complete.json", {})
    state = ProjectState(stage=Stage.RESULT_REVIEW, run_count=len(protocol.cells))
    write_json_atomic(project / "state.json", state)
    relocated = project / "stage2_failed" / protocol.protocol_id
    relocated.parent.mkdir(parents=True)
    (project / "stage2").replace(relocated)

    archived = archive_failed_stage2_protocol(
        project,
        reason="A protected evaluator failed during result review and requires a successor.",
    )
    assert archived.is_dir()
    assert not (project / "stage2").exists()
    assert read_json(project / "state.json")["stage"] == "plan_review"


def test_stage2_defaults_to_publication_and_never_silently_falls_back_to_pilot(
    tmp_path: Path,
) -> None:
    project = _stage1_ready_project(tmp_path)

    with pytest.raises(FileNotFoundError):
        freeze_stage2_protocol(project, runtime=FakeControlledRuntime())

    assert not (project / "stage2").exists()


def test_pilot_route_requires_an_explicit_non_publication_reason(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="internal exception"):
        freeze_stage2_protocol(
            tmp_path / "not-used",
            runtime=FakeControlledRuntime(),
            intent="pilot",
        )


def test_previous_publication_failure_blocks_repeated_stage2_design(
    tmp_path: Path, monkeypatch
) -> None:
    project = _stage1_ready_project(tmp_path)
    write_json_atomic(
        project / "design_revisions" / "publication_design_repair.json",
        {
            "repairs": [
                {
                    "status": "active_for_future_runs",
                    "source_codes": ["RC-MEASUREMENT-CIRCULARITY"],
                },
                {
                    "status": "active_for_future_runs",
                    "source_codes": [
                        "READINESS-EVIDENCE-BREADTH-BELOW-HARD-MINIMUM"
                    ],
                },
            ]
        },
    )
    monkeypatch.setattr(
        "research_forge.study.audit_stage1",
        lambda *_args, **_kwargs: SimpleNamespace(passed=True, violations=[]),
    )
    monkeypatch.setattr(
        "research_forge.study.backend_status",
        lambda: {
            "codex_sdk_installed": True,
            "codex_authenticated": True,
            "codex_sdk_version": "test",
            "codex_account_type": "chatgpt",
            "codex_plan_type": "test",
        },
    )

    with pytest.raises(ValueError, match="active publication repair"):
        freeze_stage2_protocol(
            project,
            runtime=FakeControlledRuntime(),
            intent="pilot",
            pilot_reason="Exercise a repair-regression pilot without publication claims.",
        )

    audit = read_json(
        project / "design_revisions" / "latest_stage2_design_repair_audit.json"
    )
    assert audit["passed"] is False
    assert any("RC-MEASUREMENT-CIRCULARITY" in item for item in audit["violations"])
    assert any("8 tasks x 5 seeds" in item for item in audit["violations"])
    assert not (project / "stage2").exists()


def test_publication_intent_uses_locked_venue_and_blocks_pilot_design(
    tmp_path: Path, monkeypatch
) -> None:
    project = _stage1_ready_project(tmp_path)
    root = Path(__file__).resolve().parents[1]
    target, target_path = freeze_publication_experiment_target(
        project=project,
        venue_id="research-integrity-and-peer-review",
        recommendation_report_path=(
            root
            / "stage1_runs"
            / "research-agent-evidence-v2"
            / "synthesis"
            / "venue_recommendation.json"
        ),
    )
    monkeypatch.setattr(
        "research_forge.study.audit_stage1",
        lambda *_args, **_kwargs: SimpleNamespace(passed=True, violations=[]),
    )
    monkeypatch.setattr(
        "research_forge.study.backend_status",
        lambda: {
            "codex_sdk_installed": True,
            "codex_authenticated": True,
            "codex_sdk_version": "test",
            "codex_account_type": "chatgpt",
            "codex_plan_type": "test",
        },
    )

    with pytest.raises(ValueError, match="publication-intent hard gate failed"):
        freeze_stage2_protocol(
            project,
            runtime=FakeControlledRuntime(),
            intent="publication",
            publication_contract=target_path,
        )

    gate = read_json(
        project / "design_revisions" / "publication_experiment_gate.json"
    )
    assert gate["venue_id"] == target.venue_id
    assert gate["target_locked"] is True
    assert gate["pre_experiment_gate_passed"] is False
    assert any("RC-MEASUREMENT-CIRCULARITY" in item for item in gate["violations"])
    assert any("at least 8" in item for item in gate["violations"])
    assert not (project / "stage2").exists()


def test_claim_registry_requires_claim_identity_to_match_cell() -> None:
    claim = StudyClaim(
        claim_id="result-01",
        run_id="agent-run-01",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The protected run recorded Accuracy equal to 0.5.",
        experiment_run_id="experiment-run-01",
        metric_values={"Accuracy": 0.5},
        artifact_paths=["runs/experiment-run-01/record.json"],
    )
    registry = StudyClaimRegistry(
        registry_id="registry-" + "a" * 12,
        protocol_id="stage2-" + "b" * 12,
        cell_id=f"baseline--{STUDY_TASKS[0]}--seed-0",
        run_id="agent-run-01",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        final_output_text=claim.claim_text,
        claims=[claim],
    )
    assert registry.claims[0].metric_values == {"Accuracy": 0.5}
    assert Stage2Protocol.model_json_schema()["title"] == "Stage2Protocol"


def test_structural_claim_audit_checks_exact_metrics_and_artifact_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    stage2 = project / "stage2"
    (stage2 / "evidence" / "sources").mkdir(parents=True)
    write_json_atomic(
        stage2 / "evidence" / "sources" / "paper-evidence.json",
        {"source_id": "paper-evidence", "notes": "Evidence-bound conclusion fixture."},
    )
    write_json_atomic(
        stage2 / "evidence" / "review.json",
        {
            "synthesis": {
                "novelty_candidates": [
                    {"novelty_id": "novelty-02", "source_ids": ["paper-evidence"]}
                ]
            }
        },
    )
    artifact = project / "artifacts" / "record.json"
    write_json_atomic(artifact, {"Accuracy": 0.5})
    claim = StudyClaim(
        claim_id="experiment-result",
        run_id="agent-run",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The isolated experiment recorded Accuracy equal to 0.5.",
        experiment_run_id="experiment-run",
        metric_values={"Accuracy": 0.5, "improvement": 0.1},
        artifact_paths=["artifacts/record.json"],
    )
    registry = StudyClaimRegistry(
        registry_id="registry-" + "c" * 12,
        protocol_id="stage2-" + "d" * 12,
        cell_id=f"baseline--{STUDY_TASKS[0]}--seed-0",
        run_id="agent-run",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        final_output_text=claim.claim_text,
        claims=[claim],
    )
    packet = [
        {
            "run_id": "experiment-run",
            "valid": True,
            "isolation_verified": True,
            "aggregate_metrics": {"Accuracy": 0.5},
            "improvement": 0.1,
            "artifacts": ["artifacts/record.json"],
        }
    ]
    assert audit_registry_structure(project, stage2, registry, packet)["passed"]
    changed = registry.model_copy(deep=True)
    changed.claims[0].metric_values["Accuracy"] = 0.6
    mismatch = audit_registry_structure(project, stage2, changed, packet)
    assert not mismatch["passed"]
    assert "experiment-result: metrics_exact" in mismatch["violations"]


def test_structural_claim_audit_accepts_bound_invalid_run_limitation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    stage2 = project / "stage2"
    (stage2 / "evidence").mkdir(parents=True)
    write_json_atomic(
        stage2 / "evidence" / "review.json",
        {"synthesis": {"novelty_candidates": [{"novelty_id": "novelty-02", "source_ids": []}]}},
    )
    artifact = project / "artifacts" / "invalid.json"
    write_json_atomic(artifact, {"valid": False, "verdict": "invalid"})
    claim = StudyClaim(
        claim_id="invalid-run-limitation",
        run_id="agent-run",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The protected experiment run was marked invalid.",
        experiment_run_id="experiment-run",
        metric_values={},
        artifact_paths=["artifacts/invalid.json"],
    )
    registry = StudyClaimRegistry(
        registry_id="registry-" + "3" * 12,
        protocol_id="stage2-" + "4" * 12,
        cell_id=f"baseline--{STUDY_TASKS[0]}--seed-0",
        run_id="agent-run",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        final_output_text=claim.claim_text,
        claims=[claim],
    )
    packet = [
        {
            "run_id": "experiment-run",
            "valid": False,
            "isolation_verified": True,
            "aggregate_metrics": {},
            "artifacts": ["artifacts/invalid.json"],
        }
    ]
    audit = audit_registry_structure(project, stage2, registry, packet)
    assert audit["passed"]
    assert audit["claim_checks"]["invalid-run-limitation"]["invalid_status_stated"]
    assert audit["claim_checks"]["invalid-run-limitation"]["invalid_metrics_absent"]


def test_structural_claim_audit_rejects_metrics_on_invalid_run(tmp_path: Path) -> None:
    project = tmp_path / "project"
    stage2 = project / "stage2"
    (stage2 / "evidence").mkdir(parents=True)
    write_json_atomic(
        stage2 / "evidence" / "review.json",
        {"synthesis": {"novelty_candidates": [{"novelty_id": "novelty-02", "source_ids": []}]}},
    )
    artifact = project / "artifacts" / "invalid.json"
    write_json_atomic(artifact, {"valid": False})
    claim = StudyClaim(
        claim_id="invalid-run-with-metric",
        run_id="agent-run",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The protected experiment run was invalid.",
        experiment_run_id="experiment-run",
        metric_values={"Accuracy": 0.5},
        artifact_paths=["artifacts/invalid.json"],
    )
    registry = StudyClaimRegistry(
        registry_id="registry-" + "5" * 12,
        protocol_id="stage2-" + "6" * 12,
        cell_id=f"baseline--{STUDY_TASKS[0]}--seed-0",
        run_id="agent-run",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        final_output_text=claim.claim_text,
        claims=[claim],
    )
    packet = [
        {
            "run_id": "experiment-run",
            "valid": False,
            "isolation_verified": True,
            "aggregate_metrics": {},
            "artifacts": ["artifacts/invalid.json"],
        }
    ]
    audit = audit_registry_structure(project, stage2, registry, packet)
    assert not audit["passed"]
    assert "invalid-run-with-metric: invalid_metrics_absent" in audit["violations"]


def test_structural_claim_audit_requires_invalid_status_in_claim_text(tmp_path: Path) -> None:
    project = tmp_path / "project"
    stage2 = project / "stage2"
    (stage2 / "evidence").mkdir(parents=True)
    write_json_atomic(
        stage2 / "evidence" / "review.json",
        {"synthesis": {"novelty_candidates": [{"novelty_id": "novelty-02", "source_ids": []}]}},
    )
    artifact = project / "artifacts" / "invalid.json"
    write_json_atomic(artifact, {"valid": False})
    claim = StudyClaim(
        claim_id="unstated-invalid-run",
        run_id="agent-run",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The protected experiment produced a run record.",
        experiment_run_id="experiment-run",
        metric_values={},
        artifact_paths=["artifacts/invalid.json"],
    )
    registry = StudyClaimRegistry(
        registry_id="registry-" + "7" * 12,
        protocol_id="stage2-" + "8" * 12,
        cell_id=f"baseline--{STUDY_TASKS[0]}--seed-0",
        run_id="agent-run",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        final_output_text=claim.claim_text,
        claims=[claim],
    )
    packet = [
        {
            "run_id": "experiment-run",
            "valid": False,
            "isolation_verified": True,
            "aggregate_metrics": {},
            "artifacts": ["artifacts/invalid.json"],
        }
    ]
    audit = audit_registry_structure(project, stage2, registry, packet)
    assert not audit["passed"]
    assert "unstated-invalid-run: invalid_status_stated" in audit["violations"]


def test_treatment_normalization_removes_only_cross_run_artifact_links() -> None:
    claim = StudyClaim(
        claim_id="experiment-result",
        run_id="agent-treatment",
        arm=StudyArm.TREATMENT,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The candidate recorded Accuracy equal to 0.5.",
        experiment_run_id="candidate-run",
        metric_values={"Accuracy": 0.5},
        artifact_paths=["candidate/record.json", "baseline/record.json"],
    )
    registry = StudyClaimRegistry(
        registry_id="registry-" + "e" * 12,
        protocol_id="stage2-" + "f" * 12,
        cell_id=f"treatment--{STUDY_TASKS[0]}--seed-0",
        run_id="agent-treatment",
        arm=StudyArm.TREATMENT,
        task_pack=STUDY_TASKS[0],
        seed=0,
        final_output_text=claim.claim_text,
        claims=[claim],
    )
    normalized, changes = _normalize_registry_artifacts(
        registry,
        [
            {
                "run_id": "candidate-run",
                "artifacts": ["candidate/record.json"],
            }
        ],
    )
    assert normalized.claims[0].artifact_paths == ["candidate/record.json"]
    assert normalized.claims[0].claim_text == registry.claims[0].claim_text
    assert normalized.claims[0].metric_values == {"Accuracy": 0.5}
    assert changes[0]["removed_artifact_paths"] == ["baseline/record.json"]


def test_treatment_normalization_retains_exact_record_improvement() -> None:
    claim = StudyClaim(
        claim_id="experiment-result",
        run_id="agent-treatment",
        arm=StudyArm.TREATMENT,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The run recorded Accuracy 0.5 and improvement 0.1.",
        experiment_run_id="candidate-run",
        metric_values={"Accuracy": 0.5, "improvement": 0.1},
        artifact_paths=["candidate/record.json"],
    )
    registry = StudyClaimRegistry(
        registry_id="registry-" + "1" * 12,
        protocol_id="stage2-" + "2" * 12,
        cell_id=f"treatment--{STUDY_TASKS[0]}--seed-0",
        run_id="agent-treatment",
        arm=StudyArm.TREATMENT,
        task_pack=STUDY_TASKS[0],
        seed=0,
        final_output_text=claim.claim_text,
        claims=[claim],
    )
    normalized, changes = _normalize_registry_artifacts(
        registry,
        [
            {
                "run_id": "candidate-run",
                "aggregate_metrics": {"Accuracy": 0.5},
                "improvement": 0.1,
                "artifacts": ["candidate/record.json"],
            }
        ],
    )
    assert normalized.claims[0].metric_values == {"Accuracy": 0.5, "improvement": 0.1}
    assert normalized.claims[0].claim_text == claim.claim_text
    assert changes == []


def test_normalization_drops_metricless_valid_experiment_status_claim() -> None:
    metric_claim = StudyClaim(
        claim_id="metric-result",
        run_id="agent-baseline",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The valid run recorded Accuracy equal to 0.5.",
        experiment_run_id="baseline-run",
        metric_values={"Accuracy": 0.5},
        artifact_paths=["baseline/record.json"],
    )
    status_claim = StudyClaim(
        claim_id="validity-status",
        run_id="agent-baseline",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The linked baseline run was valid and isolated.",
        experiment_run_id="baseline-run",
        artifact_paths=["baseline/record.json"],
    )
    registry = StudyClaimRegistry(
        registry_id="registry-" + "3" * 12,
        protocol_id="stage2-" + "4" * 12,
        cell_id=f"baseline--{STUDY_TASKS[0]}--seed-0",
        run_id="agent-baseline",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        final_output_text="\n".join([metric_claim.claim_text, status_claim.claim_text]),
        claims=[metric_claim, status_claim],
    )
    normalized, changes = _normalize_registry_artifacts(
        registry,
        [
            {
                "run_id": "baseline-run",
                "valid": True,
                "aggregate_metrics": {"Accuracy": 0.5},
                "artifacts": ["baseline/record.json"],
            }
        ],
    )
    assert [claim.claim_id for claim in normalized.claims] == ["metric-result"]
    assert normalized.final_output_text == metric_claim.claim_text
    assert changes[0]["normalization"] == "drop_metricless_valid_experiment_claim"


def test_normalization_removes_source_ids_from_experiment_claim() -> None:
    claim = StudyClaim(
        claim_id="experiment-result",
        run_id="agent-baseline",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The run recorded Accuracy equal to 0.5.",
        experiment_run_id="baseline-run",
        metric_values={"Accuracy": 0.5},
        artifact_paths=["baseline/record.json"],
        source_ids=["baseline-run"],
    )
    registry = StudyClaimRegistry(
        registry_id="registry-" + "5" * 12,
        protocol_id="stage2-" + "6" * 12,
        cell_id=f"baseline--{STUDY_TASKS[0]}--seed-0",
        run_id="agent-baseline",
        arm=StudyArm.BASELINE,
        task_pack=STUDY_TASKS[0],
        seed=0,
        final_output_text=claim.claim_text,
        claims=[claim],
    )
    normalized, changes = _normalize_registry_artifacts(
        registry,
        [
            {
                "run_id": "baseline-run",
                "valid": True,
                "aggregate_metrics": {"Accuracy": 0.5},
                "artifacts": ["baseline/record.json"],
            }
        ],
    )
    assert normalized.claims[0].source_ids == []
    assert normalized.claims[0].metric_values == {"Accuracy": 0.5}
    assert changes[0]["normalization"] == "remove_experiment_source_ids"


def test_treatment_revision_cannot_change_supported_claim_or_add_evidence() -> None:
    supported = StudyClaim(
        claim_id="supported-claim",
        run_id="agent-treatment",
        arm=StudyArm.TREATMENT,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.LITERATURE,
        claim_text="The linked source describes evidence verification.",
        source_ids=["paper-one"],
    )
    unsupported = StudyClaim(
        claim_id="unsupported-claim",
        run_id="agent-treatment",
        arm=StudyArm.TREATMENT,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.NOVELTY,
        claim_text="The linked source establishes universal novelty.",
        source_ids=["paper-two"],
    )
    registry = StudyClaimRegistry(
        registry_id="registry-" + "a" * 12,
        protocol_id="stage2-" + "b" * 12,
        cell_id=f"treatment--{STUDY_TASKS[0]}--seed-0",
        run_id="agent-treatment",
        arm=StudyArm.TREATMENT,
        task_pack=STUDY_TASKS[0],
        seed=0,
        final_output_text="\n".join([supported.claim_text, unsupported.claim_text]),
        claims=[supported, unsupported],
    )
    judgments = [
        SemanticClaimJudgment(
            claim_id="supported-claim",
            verdict=ClaimVerdict.SUPPORTED,
            rationale="The supplied source directly supports the bounded statement.",
            supporting_source_ids=["paper-one"],
        ),
        SemanticClaimJudgment(
            claim_id="unsupported-claim",
            verdict=ClaimVerdict.UNSUPPORTED,
            rationale="The supplied source does not establish universal novelty.",
            supporting_source_ids=[],
        ),
    ]
    changed_supported = registry.model_copy(deep=True)
    changed_supported.claims[0].claim_text = "Changed supported statement."
    with pytest.raises(ValueError, match="changed a supported claim"):
        _validate_revision(registry, changed_supported, judgments)

    added_source = registry.model_copy(deep=True)
    added_source.claims[1].source_ids.append("paper-three")
    with pytest.raises(ValueError, match="added a source"):
        _validate_revision(registry, added_source, judgments)

    valid = registry.model_copy(deep=True)
    valid.claims[1].claim_text = "The bounded review does not establish universal novelty."
    _validate_revision(registry, valid, judgments)


def test_experiment_judgment_discards_irrelevant_literature_source_ids() -> None:
    claim = StudyClaim(
        claim_id="experiment-claim",
        run_id="agent-treatment",
        arm=StudyArm.TREATMENT,
        task_pack=STUDY_TASKS[0],
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text="The protected run recorded Accuracy equal to 0.5.",
        experiment_run_id="experiment-run",
        metric_values={"Accuracy": 0.5},
        artifact_paths=["runs/record.json"],
    )
    registry = StudyClaimRegistry(
        registry_id="registry-" + "c" * 12,
        protocol_id="stage2-" + "d" * 12,
        cell_id=f"treatment--{STUDY_TASKS[0]}--seed-0",
        run_id="agent-treatment",
        arm=StudyArm.TREATMENT,
        task_pack=STUDY_TASKS[0],
        seed=0,
        final_output_text=claim.claim_text,
        claims=[claim],
    )
    batch = SemanticClaimJudgmentBatch(
        judgments=[
            SemanticClaimJudgment(
                claim_id="experiment-claim",
                verdict=ClaimVerdict.SUPPORTED,
                rationale="The supplied experiment record directly reports the exact metric.",
                supporting_source_ids=["irrelevant-paper-id"],
            )
        ]
    )
    validated = _validate_judgments(registry, batch)
    assert validated[0].verdict == ClaimVerdict.SUPPORTED
    assert validated[0].supporting_source_ids == []
