from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_forge.stage_two import (
    MethodCard,
    ResourceCategory,
    _comparison_frame_for_scope,
    _idea_scaffold_note,
    _infer_primary_outcome,
    _resource_category,
    _selected_verified_research_chain,
    _safe_int_list,
    _safe_positive_int,
    _stage_two_sandbox_ignore,
    approve_stage_two_contract,
    approve_stage_two_mvp,
    bind_stage_two_evaluation_dataset,
    ensure_stage_two_dag,
    propose_stage_two_amendment,
    revise_stage_two_protocol,
    select_stage_two_topic,
)
from research_forge.stage_three_build import (
    stage3_build_admission_from_stage2,
)
from research_forge.storage import sha256_file
from research_forge.workflow_domain import (
    ArtifactStatus,
    EntryMode,
    ExecutionStatus,
    ExecutorType,
    GateType,
    Phase,
    ScopeContractVersion,
    WorkflowRepository,
)
from research_forge.workflow_scheduler import (
    PersistentDAGScheduler,
    approve_discovery_direction,
    create_project_discovery_study,
    stage_one_handlers,
    workflow_handlers,
)


def _bundle(root: Path, *, runnable_baseline: bool = False) -> Path:
    source = root / "bundle"
    (source / "data").mkdir(parents=True)
    (source / "src").mkdir()
    (source / "reports").mkdir()
    for split in ("train", "valid", "test"):
        (source / "data" / f"{split}.csv").write_text(
            "x,label\n1,1\n2,0\n", encoding="utf-8"
        )
    (source / "src" / "model.py").write_text(
        "def predict(value):\n    return int(value > 0)\n", encoding="utf-8"
    )
    (source / "reports" / "study.md").write_text(
        "# Contributions\n\n"
        "The project evaluates a reproducible classifier under fixed splits.\n",
        encoding="utf-8",
    )
    if runnable_baseline:
        (source / "baseline.py").write_text(
            "import json, pathlib, sys\n"
            "pathlib.Path(sys.argv[1]).write_text("
            "json.dumps({'accuracy': 0.5}), encoding='utf-8')\n",
            encoding="utf-8",
        )
        (source / "research-forge.experiments.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "experiments": [
                        {
                            "experiment_id": "baseline-v1",
                            "action_ids": ["action-stage2-baseline"],
                            "title": "Declared baseline",
                            "command": [
                                "{python}",
                                "baseline.py",
                                "{metrics_file}",
                            ],
                            "cwd": ".",
                            "timeout_seconds": 30,
                            "required_inputs": [
                                "data/train.csv",
                                "data/valid.csv",
                                "data/test.csv",
                            ],
                            "network_access": False,
                            "artifacts": [
                                {
                                    "path": "metrics.json",
                                    "format": "json",
                                    "required_keys": ["accuracy"],
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
    return source


def test_primary_outcome_is_inferred_from_a_frozen_top_k_question() -> None:
    outcome = _infer_primary_outcome(
        "to be selected from a verified project metric",
        (
            "候选横截面排序模型能否提高高收益事件在 Top-K 组合中的识别率？"
        ),
    )
    assert outcome == "Top-K high-return event identification rate"


def test_finance_scope_compiles_a_domain_specific_design_before_stage_three() -> None:
    scope = ScopeContractVersion(
        study_id="study-finance-default",
        version=1,
        status=ArtifactStatus.FROZEN,
        contract_level="specific_topic",
        direction="Cross-sectional equity return prediction",
        research_question=(
            "Can a robust ranker improve Top-K high-return event "
            "identification over the existing technology-quality ranker?"
        ),
        scope_in=["point-in-time monthly equity snapshots"],
        scope_out=["live trading claims"],
        candidate_contribution=(
            "A paired comparison of dual-quality and technology-quality "
            "cross-sectional ranking"
        ),
        unit_of_analysis="selected security position in a monthly snapshot",
        study_design="paired computational comparison",
        primary_outcome="Top-K high-return event identification rate",
        comparison="dual_quality_top5 versus v3_tech_quality_top5",
        field_diff={
            "academic_concepts": [
                "cross-sectional equity return prediction",
                "rare high-return event ranking",
            ],
            "operational_definition": (
                "forward 20-session executable return is in the point-in-time "
                "industry top decile and is at least 10 percent"
            ),
        },
    )

    frame = _comparison_frame_for_scope(scope)

    assert frame["primary_outcome"] == (
        "Top-K high-return event identification rate"
    )
    assert frame["comparator"] == "v3_tech_quality_top5 ranking"
    assert frame["intervention"] == "dual_quality_top5 ranking"
    assert frame["minimum_meaningful_effect"] == 0.05
    assert "monthly snapshot" in frame["data_boundary"][
        "sampling_frame"
    ]
    assert "20-session" in frame["data_boundary"]["target_rule"]


def _direction_approved(
    tmp_path: Path, *, runnable_baseline: bool = False
) -> tuple[WorkflowRepository, str]:
    source = _bundle(tmp_path, runnable_baseline=runnable_baseline)
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository,
        source,
        include_external=False,
        identity="stage-two",
    )
    PersistentDAGScheduler(repository, stage_one_handlers()).run(study_id)
    portfolio_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "discovery_portfolio"
    )
    portfolio = repository.load_step_result(
        study_id, portfolio_step.step_instance_id
    )["discovery_portfolio"]
    approve_discovery_direction(
        repository,
        study_id,
        portfolio["recommended_direction_id"],
    )
    return repository, study_id


def test_generic_comparison_frame_survives_scope_freeze_into_stage_two(
    tmp_path: Path,
) -> None:
    source = tmp_path / "docs-only"
    source.mkdir()
    (source / "retrieval-refactor.md").write_text(
        """# InternalProduct Retrieval Refactor

## Baseline
The professor recommendation pipeline runs whichever search channels happen
to be available and lets the model emit scoring signals.

## Problem
Coverage is not explicit and the model and fallback paths are asymmetric.

## Target Path
Persist mandatory dimension search packets, normalize an evidence ledger, and
run deterministic scoring before rendering the recommendation.
""",
        encoding="utf-8",
    )
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository,
        source,
        include_external=False,
        identity="generic-frame",
    )
    PersistentDAGScheduler(repository, stage_one_handlers()).run(study_id)
    portfolio_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "discovery_portfolio"
    )
    portfolio = repository.load_step_result(
        study_id, portfolio_step.step_instance_id
    )["discovery_portfolio"]

    approve_discovery_direction(
        repository,
        study_id,
        portfolio["recommended_direction_id"],
    )

    scope = repository.latest_scope_contract(study_id)
    assert scope is not None
    assert scope.field_diff["comparison_frame"]["primary_outcome"] == (
        "evidence-dimension coverage rate"
    )
    topics_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "generate_candidate_topics"
    )
    topics = repository.load_step_result(
        study_id, topics_step.step_instance_id
    )["candidates"]
    controlled = next(
        item
        for item in topics
        if item["intervention_or_method"].startswith(
            "mandatory multi-dimensional evidence retrieval"
        )
    )
    assert "InternalProduct" not in controlled["title"]
    assert controlled["primary_outcome"] == (
        "evidence-dimension coverage rate"
    )
    assert controlled["intervention_or_method"].startswith(
        "mandatory multi-dimensional evidence retrieval"
    )
    assert controlled["blocking_resources"] == []
    assert any(
        "acquired or built" in item
        for item in controlled["unresolved_conditions"]
    )
    select_stage_two_topic(
        repository,
        study_id,
        controlled["topic_id"],
    )
    PersistentDAGScheduler(
        repository, workflow_handlers()
    ).run(study_id)
    baseline_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "validate_stage2_baseline"
    )
    baseline = repository.load_step_result(
        study_id, baseline_step.step_instance_id
    )
    gate_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "assess_stage2_gate"
    )
    gate_report = repository.load_step_result(
        study_id, gate_step.step_instance_id
    )["stage2_gate_report"]

    assert baseline["feasibility_mvp_verified"] is True
    assert baseline["formal_treatment_executed"] is False
    assert gate_report["status"] == "DESIGN_READY"
    assert gate_report["blockers"] == []


def test_stage_two_is_not_created_before_direction_scope_freezes(
    tmp_path: Path,
) -> None:
    source = _bundle(tmp_path)
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository, source, include_external=False, identity="no-scope"
    )

    with pytest.raises(ValueError, match="frozen direction-level Scope"):
        ensure_stage_two_dag(repository, study_id)

    assert not [
        item for item in repository.list_steps(study_id)
        if item.phase is Phase.PROTOCOL
    ]


def test_blocked_input_check_can_retry_without_mutating_attempt_record(
    tmp_path: Path,
) -> None:
    source = tmp_path / "arrives-later"
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project(
        "Delayed bundle", source_root=str(source)
    )
    study = repository.create_study(
        project.project_id,
        "Retryable Stage 2",
        entry_mode=EntryMode.PROJECT_TO_PAPER,
    )
    scope = ScopeContractVersion(
        study_id=study.study_id,
        version=1,
        direction="Bounded computational direction",
        research_question="Can the declared bundle support the direction?",
        scope_in=["declared local bundle"],
        scope_out=["external treatment execution"],
        candidate_contribution="A bounded feasibility result",
    )
    repository.save_scope_contract(scope)
    gate = repository.create_gate(
        study.study_id,
        GateType.SCOPE_APPROVAL,
        "scope_contract",
        f"{study.study_id}:scope-v1",
        subject_version=1,
    )
    repository.decide_gate(
        study.study_id,
        gate.gate_id,
        approve=True,
        decided_by="project_owner",
    )
    repository.save_scope_contract(
        scope.model_copy(update={"status": ArtifactStatus.FROZEN})
    )
    scan = repository.add_step(
        study.study_id,
        "project_scan",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
    )
    repository.update_step(
        study.study_id, scan.step_instance_id, ExecutionStatus.RUNNING
    )
    repository.save_step_result(
        study.study_id,
        scan.step_instance_id,
        {"resources": [], "hdf5_metadata": []},
    )
    repository.update_step(
        study.study_id, scan.step_instance_id, ExecutionStatus.SUCCEEDED
    )
    ensure_stage_two_dag(repository, study.study_id)
    scheduler = PersistentDAGScheduler(repository, workflow_handlers())
    first = scheduler.run(study.study_id)
    input_step = next(
        item
        for item in first["steps"]
        if item["step_type"] == "stage2_input_check"
    )
    assert input_step["status"] == "blocked"
    stage2 = repository.root / "studies" / study.study_id / "stage2"
    attempt = stage2 / "stage2_input_check.attempt-001.json"
    assert attempt.is_file()
    original_attempt = attempt.read_bytes()

    source.mkdir()
    scheduler.retry_step(study.study_id, input_step["step_instance_id"])
    second = scheduler.run(study.study_id)

    assert next(
        item
        for item in second["steps"]
        if item["step_type"] == "stage2_input_check"
    )["status"] == "succeeded"
    assert (stage2 / "stage2_input_check.json").is_file()
    assert attempt.read_bytes() == original_attempt


def test_resource_empty_direction_emits_buildable_conditional_topics(
    tmp_path: Path,
) -> None:
    source = tmp_path / "empty-project"
    source.mkdir()
    repository = WorkflowRepository(tmp_path / "workflow")
    project = repository.create_project(
        "Empty project", source_root=str(source)
    )
    study = repository.create_study(
        project.project_id,
        "Infeasible direction",
        entry_mode=EntryMode.PROJECT_TO_PAPER,
    )
    scope = ScopeContractVersion(
        study_id=study.study_id,
        version=1,
        direction="A computational direction without resources",
        research_question="Can this empty bundle support a formal experiment?",
        scope_in=["local computational resources"],
        scope_out=["unavailable external experiments"],
        candidate_contribution="A feasibility diagnosis",
    )
    repository.save_scope_contract(scope)
    gate = repository.create_gate(
        study.study_id,
        GateType.SCOPE_APPROVAL,
        "scope_contract",
        f"{study.study_id}:scope-v1",
        subject_version=1,
    )
    repository.decide_gate(
        study.study_id,
        gate.gate_id,
        approve=True,
        decided_by="project_owner",
    )
    repository.save_scope_contract(
        scope.model_copy(update={"status": ArtifactStatus.FROZEN})
    )
    scan = repository.add_step(
        study.study_id,
        "project_scan",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
    )
    repository.update_step(
        study.study_id, scan.step_instance_id, ExecutionStatus.RUNNING
    )
    repository.save_step_result(
        study.study_id,
        scan.step_instance_id,
        {"resources": [], "hdf5_metadata": []},
    )
    repository.update_step(
        study.study_id, scan.step_instance_id, ExecutionStatus.SUCCEEDED
    )
    ensure_stage_two_dag(repository, study.study_id)
    PersistentDAGScheduler(
        repository, workflow_handlers()
    ).run(study.study_id)

    stage2 = repository.root / "studies" / study.study_id / "stage2"
    topics = json.loads(
        (stage2 / "candidate_topics.json").read_text(encoding="utf-8")
    )
    assert {item["status"] for item in topics["candidates"]} == {"conditional"}
    assert topics["recommended_topic_id"] is not None
    assert not (stage2 / "scope_change_request.json").exists()
    assert all(
        item["resource_boundary_summary"].startswith(
            "Acquisition or experiment build required:"
        )
        for item in topics["candidates"]
    )


def test_stage_two_builds_portfolio_and_stops_at_owner_topic_selection(
    tmp_path: Path,
) -> None:
    repository, study_id = _direction_approved(tmp_path)
    steps = {item.step_type: item for item in repository.list_steps(study_id)}

    for step_type in (
        "stage2_input_check",
        "investigate_related_methods",
        "inventory_research_resources",
        "define_data_boundary",
        "diagnose_resource_gaps",
        "define_resource_requirements",
        "discover_concrete_resources",
        "validate_and_compare_resources",
        "generate_candidate_topics",
    ):
        assert steps[step_type].status is ExecutionStatus.SUCCEEDED
    assert steps["select_specific_topic"].status is ExecutionStatus.WAITING_FOR_USER
    assert steps["validate_stage2_baseline"].status is ExecutionStatus.QUEUED
    assert (
        repository.load_study(study_id).phase is Phase.PROTOCOL
    )

    stage2 = repository.root / "studies" / study_id / "stage2"
    for name in (
        "stage2_input_check.json",
        "direction_contract.json",
        "related_method_cards.jsonl",
        "research_method_summary.md",
        "baseline_candidates.json",
        "local_data_manifest.json",
        "local_code_manifest.json",
        "compute_environment.json",
        "resource_inventory.json",
        "data_quality_report.json",
        "data_provenance.json",
        "data_boundary.json",
        "leakage_risk_report.json",
        "resource_gap_report.json",
        "resource_request_cards.jsonl",
        "resource_acquisition_plan.md",
        "resource_requirements.json",
        "concrete_resource_candidates.json",
        "resource_candidate_evaluation.json",
        "resource_candidate_comparison.md",
        "candidate_topics.json",
        "topic_feasibility_matrix.md",
        "topic_recommendation.md",
    ):
        assert (stage2 / name).is_file(), name
    baseline_candidates = json.loads(
        (stage2 / "baseline_candidates.json").read_text(encoding="utf-8")
    )
    assert baseline_candidates["retrieval"]["status"] == "not_authorized"
    requirements = json.loads(
        (stage2 / "resource_requirements.json").read_text(encoding="utf-8")
    )
    assert {item["need_type"] for item in requirements["requirements"]} == {
        "dataset",
        "implementation",
        "benchmark",
    }
    evaluation = json.loads(
        (stage2 / "resource_candidate_evaluation.json").read_text(
            encoding="utf-8"
        )
    )
    assert evaluation["candidates"]
    assert all(
        set(item["score_components"]) == {
            "requirement_match",
            "metadata",
            "version_pinning",
            "license",
            "availability",
            "safe_probe",
        }
        for item in evaluation["candidates"]
    )
    assert all(
        item["install_probe"] == "not_applicable"
        for item in evaluation["candidates"]
        if item["need_type"] == "dataset"
    )
    assert all(
        item["install_probe"] == "not_run"
        for item in evaluation["candidates"]
        if item["need_type"] == "implementation"
    )
    assert not (stage2 / "protocol.draft.json").exists()
    topics = json.loads(
        (stage2 / "candidate_topics.json").read_text(encoding="utf-8")
    )
    candidate = topics["candidates"][0]
    assert {
        "background_and_motivation",
        "relation_to_direction",
        "existing_work_gap",
        "intervention_or_method",
        "minimum_meaningful_effect",
        "required_resource_types",
        "recommended_resource_candidate_ids",
        "resource_boundary_summary",
        "required_data_ids",
        "required_baselines",
        "required_tools",
        "required_compute",
        "blocking_resources",
        "strengthening_resources",
        "major_risks",
        "alternative_designs",
        "expected_evidence_strength",
        "expected_reproducibility",
        "compliance_status",
        "feasibility_dimensions",
    } <= set(candidate)
    recommended = next(
        item
        for item in topics["candidates"]
        if item["topic_id"] == topics["recommended_topic_id"]
    )
    assert recommended["required_resource_types"] == [
        "dataset",
        "implementation",
    ]
    assert recommended["resource_boundary_summary"]
    assert {
        item["status"] for item in topics["candidates"]
    } == {"conditional"}
    assert set(candidate["feasibility_dimensions"]) == {
        "scientific_value",
        "novelty",
        "falsifiability",
        "project_relevance",
        "data_availability",
        "baseline_reproducibility",
        "compute_feasibility",
        "implementation_feasibility",
        "benchmark_fit",
        "auditability",
        "expected_evidence_strength",
        "compliance",
        "time_cost",
        "monetary_cost",
    }
    assert "aggregate score" not in json.dumps(candidate).casefold()
    assert candidate["evidence_status"] == "inferred"
    assert {
        item["evidence_status"]
        for item in candidate["feasibility_dimensions"].values()
    } == {"inferred"}
    method_schema = MethodCard.model_json_schema()["properties"]
    required_method_concepts = {
        "research_question",
        "research_object",
        "unit_of_analysis",
        "dataset_name_and_version",
        "dataset_scale",
        "data_preprocessing",
        "train_validation_test_split",
        "model_and_algorithm",
        "model_version",
        "hyperparameters",
        "baseline",
        "control_group",
        "experimental_group",
        "primary_outcome",
        "secondary_outcomes",
        "success_threshold",
        "random_seeds",
        "repetitions",
        "sample_size",
        "statistical_test",
        "uncertainty_analysis",
        "ablation_experiments",
        "robustness_checks",
        "hardware",
        "software_environment",
        "code_url",
        "data_url",
        "license",
        "run_command_public",
        "environment_lock_available",
        "third_party_reproduced",
        "known_failures_or_controversies",
        "paper_code_consistency",
        "missing_information",
        "reproducibility_grade",
    }
    assert required_method_concepts <= set(method_schema)


def test_topic_selection_creates_specific_scope_vnext_without_mutating_direction(
    tmp_path: Path,
) -> None:
    repository, study_id = _direction_approved(tmp_path)
    direction = repository.load_scope_contract(study_id, 1)
    topics = json.loads(
        (
            repository.root
            / "studies"
            / study_id
            / "stage2"
            / "candidate_topics.json"
        ).read_text(encoding="utf-8")
    )

    with pytest.raises(ValueError, match="do not belong to this Study"):
        select_stage_two_topic(
            repository,
            study_id,
            topics["recommended_topic_id"],
            resource_candidate_ids=["resource-candidate-not-from-study"],
        )
    with pytest.raises(ValueError, match="do not satisfy the topic boundary"):
        select_stage_two_topic(
            repository,
            study_id,
            topics["recommended_topic_id"],
            resource_candidate_ids=[],
        )
    selected = select_stage_two_topic(
        repository,
        study_id,
        topics["recommended_topic_id"],
        overrides={"primary_outcome": "accuracy"},
    )

    specific = repository.latest_scope_contract(study_id)
    assert specific is not None
    assert specific.version == 2
    assert specific.contract_level == "specific_topic"
    assert specific.status is ArtifactStatus.FROZEN
    assert specific.predecessor_version == 1
    assert repository.load_scope_contract(study_id, 1) == direction
    assert selected["scope_contract"]["primary_outcome"] == "accuracy"
    scope_document = json.loads(
        (
            repository.root
            / "studies"
            / study_id
            / "stage2"
            / "scope_contract.json"
        ).read_text(encoding="utf-8")
    )
    assert scope_document["approved_by_user"] is True
    assert scope_document["selected_candidate_id"] == topics[
        "recommended_topic_id"
    ]
    assert scope_document["forbidden_scope_changes"]
    selection = json.loads(
        (
            repository.root
            / "studies"
            / study_id
            / "stage2"
            / "resource_selection.json"
        ).read_text(encoding="utf-8")
    )
    assert selection["status"] == "frozen"
    assert selection["selected_resource_ids"]
    assert selection["acquisition_authorized"] is False
    assert selection["execution_authorized"] is False
    assert ensure_stage_two_dag(repository, study_id)


def test_stage_two_rejects_treatment_authority_and_tolerates_bad_source_numbers(
    tmp_path: Path,
) -> None:
    repository, study_id = _direction_approved(tmp_path)
    topics_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "generate_candidate_topics"
    )
    topics = repository.load_step_result(
        study_id, topics_step.step_instance_id
    )

    with pytest.raises(ValueError, match="cannot authorize treatment"):
        select_stage_two_topic(
            repository,
            study_id,
            topics["recommended_topic_id"],
            protocol_overrides={
                "formal_treatment_execution_allowed": True,
            },
        )

    assert _safe_int_list(["1", "bad", None, 3]) == [1, 3]
    assert _safe_positive_int("4") == 4
    assert _safe_positive_int("unknown") is None
    sandbox = tmp_path / "sandbox-source"
    sandbox.mkdir()
    (sandbox / ".env").write_text("SECRET=not-for-sandbox\n", encoding="utf-8")
    (sandbox / "node_modules").mkdir()
    assert _stage_two_sandbox_ignore(
        str(sandbox), [".env", "node_modules", "model.py"]
    ) == {".env", "node_modules"}


def test_generic_stage_two_compiles_complete_default_scientific_design(
    tmp_path: Path,
) -> None:
    repository, study_id = _direction_approved(tmp_path)
    topics_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "generate_candidate_topics"
    )
    topics = repository.load_step_result(
        study_id, topics_step.step_instance_id
    )
    select_stage_two_topic(
        repository,
        study_id,
        topics["recommended_topic_id"],
        overrides={"primary_outcome": "accuracy"},
    )
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)

    gate = json.loads(
        (
            repository.root
            / "studies"
            / study_id
            / "stage2"
            / "stage2_gate_report.json"
        ).read_text(encoding="utf-8")
    )
    assert gate["status"] == "DESIGN_READY"
    assert any(
        item["check_id"] == "scientific_specification_complete"
        and item["status"] == "pass"
        for item in gate["freeze_conditions"]
    )
    assert not any(
        blocker.startswith("TASK_SEMANTICS_MISSING:")
        for blocker in gate["blockers"]
    )
    build_dir = (
        repository.root
        / "studies"
        / study_id
        / "stage2"
        / "experiment_build"
    )
    for name in (
        "dataset_spec.json",
        "annotation_protocol.json",
        "research-forge.experiments.json",
        "baseline_runner.py",
        "treatment_declaration.json",
    ):
        assert (build_dir / name).is_file()
    protocol = json.loads(
        (
            repository.root
            / "studies"
            / study_id
            / "stage2"
            / "protocol.draft.json"
        ).read_text(encoding="utf-8")
    )
    assert protocol["resource_strategy"]["status"] == "mvp_ready_to_implement"
    assert protocol["tasks"]
    assert protocol["tasks"][0] != "formal-primary-task"
    assert "authoritative target" in protocol["tasks"][0]
    assert protocol["baseline"]["behavior"]
    assert protocol["treatment"]["behavior"]
    assert protocol["baseline"]["experiment_id"] == "baseline-v1"
    assert protocol["seeds"] == [11, 29, 47, 71, 97]
    review = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "research_contract_review"
    )
    assert review.status is ExecutionStatus.WAITING_FOR_USER
    assert review.blocker["kind"] == "owner_gate"
    assert repository.latest_research_contract(study_id).status is ArtifactStatus.DRAFT
    mvp = json.loads(
        (
            repository.root
            / "studies"
            / study_id
            / "stage2"
            / "stage2_mvp_report.json"
        ).read_text(encoding="utf-8")
    )
    assert mvp["status"] == "verified"
    assert mvp["scientific_evidence_eligible"] is False
    assert len(mvp["smoke_cases"]) == 3

    stage2 = repository.root / "studies" / study_id / "stage2"
    original_protocol = (stage2 / "protocol.draft.json").read_bytes()
    revision = revise_stage_two_protocol(
        repository,
        study_id,
        {
            "sample_size": "12 frozen rows",
            "statistical_power": "descriptive feasibility revision",
        },
        reason="Fill two blocking protocol fields before freeze.",
    )
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)
    revision_steps = {
        item.step_type
        for item in repository.list_steps(study_id)
        if item.task_group == "stage2-revision-v2"
    }
    assert {
        "lint_research_contract",
        "compile_research_contract",
        "dry_run_research_contract",
        "execution_readiness_gate",
    }.issubset(revision_steps)

    assert revision["requested_version"] == 2
    assert repository.latest_research_contract(study_id).version == 2
    assert repository.latest_research_contract(
        study_id
    ).status is ArtifactStatus.DRAFT
    assert (stage2 / "protocol.draft.v2.json").is_file()
    assert (stage2 / "decision_rules.v2.json").is_file()
    assert (stage2 / "preflight_report.v2.json").is_file()
    assert (stage2 / "stage2_gate_report.v2.json").is_file()
    assert (stage2 / "protocol.draft.json").read_bytes() == original_protocol


def test_stage_two_uses_local_design_cues_to_propose_metric(
    tmp_path: Path,
) -> None:
    repository, study_id = _direction_approved(tmp_path)
    study = repository.load_study(study_id)
    project = repository.load_project(study.project_id)
    (Path(project.source_root) / "README.md").write_text(
        "# Evaluation\n\nThe preregistered primary outcome is accuracy.\n",
        encoding="utf-8",
    )
    topics_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "generate_candidate_topics"
    )
    topics = repository.load_step_result(
        study_id, topics_step.step_instance_id
    )
    select_stage_two_topic(
        repository,
        study_id,
        topics["recommended_topic_id"],
    )
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)

    stage2 = repository.root / "studies" / study_id / "stage2"
    protocol = json.loads(
        (stage2 / "protocol.draft.json").read_text(encoding="utf-8")
    )
    gate = json.loads(
        (stage2 / "stage2_gate_report.json").read_text(encoding="utf-8")
    )
    assert protocol["primary_metric"]["name"] == "accuracy"
    assert protocol["primary_metric"]["direction"] == "maximize"
    baseline = json.loads(
        (stage2 / "baseline_validation_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert baseline["feasibility_mvp_verified"] is True
    assert baseline["execution_status"] == "non_scientific_mvp_verified"
    assert gate["status"] == "DESIGN_READY"


def test_stage_two_freezes_explicit_local_paired_profile_for_stage_three(
    tmp_path: Path,
) -> None:
    repository, study_id = _direction_approved(tmp_path)
    study = repository.load_study(study_id)
    project = repository.load_project(study.project_id)
    (Path(project.source_root) / "README.md").write_text(
        "# Paired experiment\n\n"
        "Baseline: always predict class 0;\n"
        "Treatment: read the preregistered binary signal;\n"
        "The primary metric is accuracy. The matrix contains 2 tasks and "
        "2 seeds. The frozen success threshold is paired improvement of "
        "at least 0.40 across all 6 formal rows.\n",
        encoding="utf-8",
    )
    topics_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "generate_candidate_topics"
    )
    topics = repository.load_step_result(
        study_id, topics_step.step_instance_id
    )
    select_stage_two_topic(
        repository,
        study_id,
        topics["recommended_topic_id"],
    )
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)

    contract = repository.latest_research_contract(study_id)
    assert contract is not None
    assert (
        contract.experiment_profile.value
        == "computational_paired_comparison_v1"
    )
    assert contract.tasks == ["task-1", "task-2"]
    assert contract.seeds == [1, 2]
    assert contract.baseline["action_id"] == "action-baseline"
    assert contract.treatment["action_id"] == "action-treatment"
    assert contract.statistical_rules["effect_threshold"] == 0.40
    assert contract.output_schema["metric_field"] == "accuracy"
    approve_stage_two_contract(repository, study_id)
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)
    handoff, _, capability = stage3_build_admission_from_stage2(
        repository, study_id
    )
    assert handoff.profile.value == "computational_paired_comparison_v1"
    assert capability.value == "supported_with_build"


def test_bound_evaluation_dataset_runs_generated_baseline(
    tmp_path: Path,
) -> None:
    repository, study_id = _direction_approved(tmp_path)
    topics_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "generate_candidate_topics"
    )
    topics = repository.load_step_result(
        study_id, topics_step.step_instance_id
    )
    select_stage_two_topic(
        repository,
        study_id,
        topics["recommended_topic_id"],
    )
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)
    dataset = tmp_path / "evaluation.jsonl"
    dataset.write_text(
        "".join(
            json.dumps(
                {
                    "case_id": f"case-{index:02d}",
                    "eligible": True,
                    "input": {"x": index},
                    "reference": index % 2,
                    "baseline_primary_metric": 0.5 + index / 100,
                }
            )
            + "\n"
            for index in range(10)
        ),
        encoding="utf-8",
    )

    binding = bind_stage_two_evaluation_dataset(
        repository,
        study_id,
        dataset,
        compliance_cleared=True,
        metric_direction="maximize",
        success_threshold="treatment mean >= baseline mean + 0.05",
        minimum_meaningful_effect="0.05 absolute increase",
    )
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)

    assert binding["binding"]["eligible_cases"] == 10
    stage2 = repository.root / "studies" / study_id / "stage2"
    baseline = json.loads(
        (stage2 / "baseline_validation_report.v2.json").read_text(
            encoding="utf-8"
        )
    )
    gate = json.loads(
        (stage2 / "stage2_gate_report.v2.json").read_text(encoding="utf-8")
    )
    assert baseline["execution_status"] == "completed"
    assert baseline["baseline_verified"] is True
    assert gate["status"] in {"DESIGN_READY", "PASS", "CONDITIONAL_PASS"}


def test_verified_non_scientific_mvp_does_not_become_evidence_and_design_can_proceed(
    tmp_path: Path,
) -> None:
    repository, study_id = _direction_approved(tmp_path)
    topics_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "generate_candidate_topics"
    )
    topics = repository.load_step_result(
        study_id, topics_step.step_instance_id
    )
    select_stage_two_topic(
        repository,
        study_id,
        topics["recommended_topic_id"],
        protocol_overrides={"metric_direction": "maximize"},
    )
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)
    mvp_report = tmp_path / "mvp-report.json"
    mvp_report.write_text(
        json.dumps(
            {
                "scientific_evidence_eligible": False,
                "environment_started": True,
                "metric_computable": True,
                "baseline_instantiable": True,
                "failure_modes_distinguishable": True,
                "reset_or_isolation_verified": True,
                "runtime_seconds": 2.5,
                "smoke_cases": [
                    {"case_id": "smoke-1", "status": "passed"},
                    {"case_id": "smoke-2", "status": "passed"},
                    {"case_id": "smoke-3", "status": "passed"},
                ],
            }
        ),
        encoding="utf-8",
    )

    approval = approve_stage_two_mvp(
        repository, study_id, mvp_report
    )
    snapshot = PersistentDAGScheduler(
        repository, workflow_handlers()
    ).run(study_id)

    assert approval["mvp"]["scientific_evidence_eligible"] is False
    gate = json.loads(
        (
            repository.root
            / "studies"
            / study_id
            / "stage2"
            / "stage2_gate_report.v2.json"
        ).read_text(encoding="utf-8")
    )
    review = next(
        item
        for item in snapshot["steps"]
        if item["step_type"] == "research_contract_review"
        and item["task_group"] == "stage2-revision-v2"
    )
    assert gate["status"] == "DESIGN_READY"
    assert review["status"] == "waiting_for_user"
    assert any(
        item["check_id"] == "numeric_effect_threshold_frozen"
        and item["status"] == "pass"
        for item in gate["freeze_conditions"]
    )
    assert any(
        item["check_id"] == "stage2_mvp_verified"
        and item["status"] == "pass"
        for item in gate["freeze_conditions"]
    )


def test_stage_two_classifies_jsonl_splits_and_package_init_correctly() -> None:
    assert (
        _resource_category("task/data/train.jsonl", ".jsonl")
        is ResourceCategory.DATA
    )
    assert (
        _resource_category("task/data/test.jsonl", ".jsonl")
        is ResourceCategory.DATA
    )
    assert (
        _resource_category("task/evidence.jsonl", ".jsonl")
        is ResourceCategory.EVALUATION
    )
    assert (
        _resource_category("task/src/__init__.py", ".py")
        is ResourceCategory.SOFTWARE
    )
    assert (
        _resource_category("task/experiment/run_experiment.py", ".py")
        is ResourceCategory.IMPLEMENTATION
    )
    assert (
        _resource_category("protocols/extreme-winner-v1.json", ".json")
        is ResourceCategory.EVALUATION
    )
    assert (
        _resource_category("outputs/extreme-winner-v1.json", ".json")
        is ResourceCategory.EVALUATION
    )
    assert (
        _resource_category("tests_py/test_extreme_winner.py", ".py")
        is ResourceCategory.EVALUATION
    )


def test_idea_scaffolding_does_not_count_as_research_assets(
    tmp_path: Path,
) -> None:
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    (experiment / "run_experiment.py").write_text(
        "# Replace this deterministic placeholder with the real "
        "training/evaluation call.\n",
        encoding="utf-8",
    )

    assert _idea_scaffold_note(
        entry_mode=EntryMode.IDEA_TO_PAPER,
        source_root=tmp_path,
        relative_path="events.jsonl",
    )
    assert _idea_scaffold_note(
        entry_mode=EntryMode.IDEA_TO_PAPER,
        source_root=tmp_path,
        relative_path="experiment/run_experiment.py",
    )
    assert (
        _idea_scaffold_note(
            entry_mode=EntryMode.PROJECT_TO_PAPER,
            source_root=tmp_path,
            relative_path="events.jsonl",
        )
        is None
    )
    (experiment / "run_experiment.py").write_text(
        "def train():\n    return 1\n",
        encoding="utf-8",
    )
    assert (
        _idea_scaffold_note(
            entry_mode=EntryMode.IDEA_TO_PAPER,
            source_root=tmp_path,
            relative_path="experiment/run_experiment.py",
        )
        is None
    )


def test_stage_two_protocol_preserves_secondary_metrics(
    tmp_path: Path,
) -> None:
    source = _bundle(tmp_path)
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository,
        source,
        include_external=False,
        identity="secondary-metrics",
    )
    PersistentDAGScheduler(repository, stage_one_handlers()).run(study_id)
    portfolio_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "discovery_portfolio"
    )
    portfolio = repository.load_step_result(
        study_id,
        portfolio_step.step_instance_id,
    )["discovery_portfolio"]
    approve_discovery_direction(
        repository,
        study_id,
        portfolio["recommended_direction_id"],
    )
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)
    topics_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "generate_candidate_topics"
    )
    topics = repository.load_step_result(
        study_id,
        topics_step.step_instance_id,
    )
    secondary = {
        "name": "protected_accuracy",
        "direction": "maximize",
        "denominator": "frozen safeguard items",
        "role": "safeguard",
    }
    select_stage_two_topic(
        repository,
        study_id,
        topics["recommended_topic_id"],
        protocol_overrides={
            "secondary_metrics": [secondary],
            "multiple_comparison_policy": "Holm adjustment",
        },
    )
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)

    contract = repository.latest_research_contract(study_id)
    assert contract is not None
    assert secondary in contract.metrics
    assert (
        contract.statistical_rules["multiple_comparison_policy"]
        == "Holm adjustment"
    )


def test_hdf5_metadata_enters_stage_two_as_conditional_dataset(
    tmp_path: Path,
) -> None:
    h5py = pytest.importorskip("h5py")
    source = tmp_path / "stereo-project"
    (source / "data").mkdir(parents=True)
    (source / "src").mkdir()
    for name in (
        "square_stereo_demo.hdf5",
        "tool_hang_stereo_demo.hdf5",
        "transport_stereo_demo.hdf5",
    ):
        with h5py.File(source / "data" / name, "w") as handle:
            data = handle.create_group("data")
            demo = data.create_group("demo_0")
            demo.attrs["num_samples"] = 2
            demo.create_dataset("actions", data=[[0.0], [1.0]])
            obs = demo.create_group("obs")
            obs.create_dataset(
                "left_image",
                data=[[[[0]]], [[[1]]]],
            )
            obs.create_dataset(
                "right_image",
                data=[[[[0]]], [[[1]]]],
            )
    (source / "src" / "run_stereo.py").write_text(
        "def main(): return 0\n"
        "if __name__ == '__main__': raise SystemExit(main())\n",
        encoding="utf-8",
    )
    (source / "README.md").write_text(
        "# Stereo study\n\nEvaluate stereo geometry priors.\n",
        encoding="utf-8",
    )
    repository = WorkflowRepository(tmp_path / "workflow")
    _, study_id = create_project_discovery_study(
        repository,
        source,
        include_external=False,
        identity="hdf5-stage-two",
    )
    PersistentDAGScheduler(repository, stage_one_handlers()).run(study_id)
    portfolio_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "discovery_portfolio"
    )
    portfolio = repository.load_step_result(
        study_id,
        portfolio_step.step_instance_id,
    )["discovery_portfolio"]
    approve_discovery_direction(
        repository,
        study_id,
        portfolio["recommended_direction_id"],
    )
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)

    inventory_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "inventory_research_resources"
    )
    inventory = repository.load_step_result(
        study_id,
        inventory_step.step_instance_id,
    )
    hdf5_resource = next(
        item
        for item in inventory["resources"]
        if item["location"] == "data/square_stereo_demo.hdf5"
    )
    assert hdf5_resource["category"] == "data"
    assert hdf5_resource["validation"] == "partial"
    assert hdf5_resource["content_hash"] is None
    assert hdf5_resource["metadata_hash"]

    comparison_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "validate_and_compare_resources"
    )
    comparison = repository.load_step_result(
        study_id,
        comparison_step.step_instance_id,
    )["resource_candidate_evaluation"]
    candidate = next(
        item
        for item in comparison["candidates"]
        if item["canonical_identifier"] == "data/square_stereo_demo.hdf5"
    )
    assert candidate["eligibility"] == "conditional"
    assert candidate["schema_probe"] == "passed"
    assert any("metadata-bound only" in item for item in candidate["blockers"])

    topics_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "generate_candidate_topics"
    )
    topics = repository.load_step_result(
        study_id,
        topics_step.step_instance_id,
    )
    selected_topic = next(
        item
        for item in topics["candidates"]
        if item["topic_id"] == topics["recommended_topic_id"]
    )
    assert len(selected_topic["required_data_ids"]) == 3
    select_stage_two_topic(
        repository,
        study_id,
        topics["recommended_topic_id"],
    )
    snapshot = PersistentDAGScheduler(
        repository,
        workflow_handlers(),
    ).run(study_id)
    gate = json.loads(
        (
            repository.root
            / "studies"
            / study_id
            / "stage2"
            / "stage2_gate_report.json"
        ).read_text(encoding="utf-8")
    )
    mvp = json.loads(
        (
            repository.root
            / "studies"
            / study_id
            / "stage2"
            / "stage2_mvp_report.json"
        ).read_text(encoding="utf-8")
    )
    review = next(
        item
        for item in snapshot["steps"]
        if item["step_type"] == "research_contract_review"
    )
    assert gate["status"] == "DESIGN_READY"
    assert mvp["status"] == "verified"
    assert mvp["scientific_evidence_eligible"] is False
    assert review["status"] == "waiting_for_user"


def test_selected_verified_local_chain_can_be_reused_as_stage_two_baseline(
    tmp_path: Path,
) -> None:
    source = tmp_path / "bundle"
    project = source / "matrix" / "project-a"
    (project / "experiment").mkdir(parents=True)
    (project / "evaluator").mkdir()
    (project / "data").mkdir()
    contract_path = project / "research_contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "title": "Verified local study",
                "metrics": [{"name": "Accuracy", "direction": "maximize"}],
                "baseline_definition": "registered baseline",
            }
        ),
        encoding="utf-8",
    )
    (project / "experiment" / "run_experiment.py").write_text(
        "def main(): return 0\nif __name__ == '__main__': main()\n",
        encoding="utf-8",
    )
    evaluator = project / "evaluator" / "evaluate.py"
    evaluator.write_text("def evaluate(x): return x\n", encoding="utf-8")
    (project / "data" / "train.jsonl").write_text(
        '{"x":1,"label":1}\n', encoding="utf-8"
    )
    (project / "data" / "test.jsonl").write_text(
        '{"x":2,"label":0}\n', encoding="utf-8"
    )
    (project / "frozen_manifest.json").write_text(
        json.dumps(
            {
                "hashes": {
                    "research_contract.json": sha256_file(contract_path)
                }
            }
        ),
        encoding="utf-8",
    )
    (project / "protected_manifest.json").write_text(
        json.dumps(
            {"hashes": {"evaluator/evaluate.py": sha256_file(evaluator)}}
        ),
        encoding="utf-8",
    )
    (project / "execution_contract.json").write_text(
        json.dumps(
            {
                "primary_metric": "Accuracy",
                "direction": "maximize",
                "required_repeats": 2,
                "max_runs": 4,
                "timeout_seconds": 60,
            }
        ),
        encoding="utf-8",
    )
    (project / "benchmark").mkdir()
    (project / "benchmark" / "task.json").write_text(
        json.dumps(
            {
                "primary_metric": "Accuracy",
                "direction": "maximize",
                "seeds": [0, 1],
                "target_score": 0.8,
            }
        ),
        encoding="utf-8",
    )
    run_id = "baseline-001"
    run_dir = project / "runs" / run_id
    run_dir.mkdir(parents=True)
    record = {
        "run_id": run_id,
        "contract_hash": sha256_file(contract_path),
        "code_hash": "code-hash",
        "is_baseline": True,
        "valid": True,
        "verdict": "baseline_verified",
        "isolation_verified": True,
        "primary_metric": "Accuracy",
        "aggregate_metrics": {"Accuracy": 0.75},
        "runtime_attestation": {"controlled_environment": True},
    }
    (run_dir / "record.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                key: record[key]
                for key in (
                    "run_id",
                    "contract_hash",
                    "code_hash",
                    "is_baseline",
                    "isolation_verified",
                )
            }
        ),
        encoding="utf-8",
    )
    (project / "evidence.jsonl").write_text(
        json.dumps(record) + "\n", encoding="utf-8"
    )
    candidates = [
        {
            "source_kind": "local_project",
            "canonical_identifier": (
                "matrix/project-a/experiment/run_experiment.py"
            ),
        },
        {
            "source_kind": "local_project",
            "canonical_identifier": "matrix/project-a/data/train.jsonl",
        },
        {
            "source_kind": "local_project",
            "canonical_identifier": "matrix/project-a/data/test.jsonl",
        },
    ]

    imported = _selected_verified_research_chain(source, candidates)

    assert imported is not None
    assert imported["strategy"] == "reuse_local_verified_chain"
    assert imported["baseline"]["run_id"] == run_id
    assert imported["denominator"] == 1


def test_declared_baseline_can_pass_freeze_and_handoff_to_stage_three(
    tmp_path: Path,
) -> None:
    repository, study_id = _direction_approved(
        tmp_path, runnable_baseline=True
    )
    topics_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "generate_candidate_topics"
    )
    topics = repository.load_step_result(
        study_id, topics_step.step_instance_id
    )
    select_stage_two_topic(
        repository,
        study_id,
        topics["recommended_topic_id"],
        overrides={"primary_outcome": "accuracy"},
        protocol_overrides={
            "primary_metric": "accuracy",
            "metric_direction": "higher_is_better",
            "denominator": "all eligible test rows",
            "sample_size": "all six frozen local rows",
            "variance_unit": "registered pair",
            "statistical_power": "descriptive fixture; no inferential power claim",
            "statistical_analysis": "paired bootstrap confidence interval",
            "statistical_test": "paired bootstrap",
            "confidence_interval": "95% percentile bootstrap interval",
            "falsification_condition": (
                "The frozen interval and effect threshold fail the directional rule."
            ),
            "success_threshold": "accuracy difference greater than or equal to 0.05",
            "minimum_meaningful_effect": "0.05 accuracy",
            "metric_implementation": "tests/test_metric.py::test_accuracy",
            "randomization_method": "paired fixed-seed ordering",
            "seeds": [1],
            "repetitions": 1,
            "tasks": ["local-classification"],
            "baseline_experiment_id": "baseline-v1",
            "baseline_action_id": "action-stage2-baseline",
            "run_baseline": True,
            "data_loadable": True,
            "data_schema_readable": True,
            "inclusion_exclusion_executable": True,
            "split_boundary_verified": True,
            "leakage_controls_verified": True,
            "metric_unit_test_passed": True,
            "statistical_synthetic_test_passed": True,
            "dependency_installable": True,
            "hardware_sufficient": True,
            "logging_validated": True,
            "artifact_preservation_validated": True,
            "binding_validated": True,
            "failure_detection_validated": True,
            "budget_reasonable": True,
            "sensitive_data_guard_passed": True,
            "compliance_cleared": True,
            "compliance_clearance": "owner-confirmed local synthetic fixture",
            "dependency_lock": "test fixture Python environment",
            "quality_control": ["metric unit test", "schema test"],
            "reproduction_steps": ["run declared baseline-v1 command"],
            "approved_resource_substitutions": [
                "protocol assertions resolve the generic leakage gap"
            ],
            "budget": {"max_seconds": 30, "max_cost": 0},
            "leakage_controls": ["disjoint frozen split files"],
        },
    )
    snapshot = PersistentDAGScheduler(
        repository, workflow_handlers()
    ).run(study_id)
    review = next(
        item for item in snapshot["steps"]
        if item["step_type"] == "research_contract_review"
    )
    assert review["status"] == "waiting_for_user"
    gate_report = json.loads(
        (
            repository.root
            / "studies"
            / study_id
            / "stage2"
            / "stage2_gate_report.json"
        ).read_text(encoding="utf-8")
    )
    assert gate_report["status"] == "CONDITIONAL_PASS"
    assert any(
        "reported or inferred evidence" in warning
        for warning in gate_report["warnings"]
    )

    revision = revise_stage_two_protocol(
        repository,
        study_id,
        {
            "success_threshold": (
                "accuracy difference greater than or equal to 0.06"
            )
        },
        reason="Tighten the threshold before owner freeze.",
    )
    assert revision["requested_version"] == 2
    assert any(
        item.status.value == "rejected" and item.subject_version == 1
        for item in repository.list_gates(study_id)
        if item.gate_type is GateType.RESEARCH_CONTRACT
    )
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)
    stage2 = repository.root / "studies" / study_id / "stage2"
    revised_protocol = json.loads(
        (stage2 / "protocol.draft.v2.json").read_text(encoding="utf-8")
    )
    assert revised_protocol["run_baseline"] is True
    assert json.loads(
        (stage2 / "stage2_gate_report.v2.json").read_text(encoding="utf-8")
    )["status"] == "CONDITIONAL_PASS"

    approve_stage_two_contract(repository, study_id)
    final = PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)

    assert repository.load_study(study_id).phase is Phase.EXPERIMENT
    assert repository.latest_research_contract(study_id).status is ArtifactStatus.FROZEN
    assert repository.latest_research_contract(study_id).version == 2
    assert max(
        (
            item
            for item in final["steps"]
            if item["step_type"] == "complete_stage2"
        ),
        key=lambda item: item["updated_at"],
    )["status"] == "succeeded"
    assert (stage2 / "decision_rules.v2.json").is_file()
    for name in (
        "protocol.lock.json",
        "environment.lock.json",
        "data_manifest.lock.json",
        "code_manifest.lock.json",
        "decision_rules.lock.json",
    ):
        payload = json.loads((stage2 / name).read_text(encoding="utf-8"))
        assert payload["immutable"] is True
        assert len(payload["source_sha256"]) == 64
        assert payload["research_contract_version"] == 2
    assert json.loads(
        (stage2 / "protocol.lock.json").read_text(encoding="utf-8")
    )["source_artifact"] == "protocol.draft.v2.json"
    baseline = json.loads(
        (stage2 / "baseline_validation_report.v2.json").read_text(encoding="utf-8")
    )
    assert baseline["baseline_verified"] is True
    assert baseline["formal_treatment_executed"] is False
    assert baseline["hashes_match_contract"] is True
    assert baseline["artifact_binding_valid"] is True
    assert baseline["required_input_bindings"]
    assert all(
        item["matches_inventory"]
        for item in baseline["required_input_bindings"]
    )

    post_freeze_revision = revise_stage_two_protocol(
        repository,
        study_id,
        {"owner_change_request": "Add seed 3 before any formal run starts."},
        reason="Owner requested a pre-run contract change.",
    )
    assert post_freeze_revision["requested_version"] == 3
    assert repository.load_study(study_id).phase is Phase.PROTOCOL
    assert any(
        item.step_type == "research_contract_review"
        and item.task_group == "stage2-revision-v3"
        for item in repository.list_steps(study_id)
    )
    canonical_lock_bytes = {
        name: (stage2 / name).read_bytes()
        for name in (
            "protocol.lock.json",
            "environment.lock.json",
            "data_manifest.lock.json",
            "code_manifest.lock.json",
            "decision_rules.lock.json",
        )
    }
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)
    approve_stage_two_contract(repository, study_id)
    revision_final = PersistentDAGScheduler(
        repository, workflow_handlers()
    ).run(study_id)
    assert repository.load_study(study_id).phase is Phase.EXPERIMENT
    assert max(
        (
            item
            for item in revision_final["steps"]
            if item["step_type"] == "freeze_stage2_locks"
        ),
        key=lambda item: item["updated_at"],
    )["status"] == "succeeded"
    for name, original in canonical_lock_bytes.items():
        assert (stage2 / name).read_bytes() == original
        path = Path(name)
        versioned = stage2 / f"{path.stem}.v3{path.suffix}"
        payload = json.loads(versioned.read_text(encoding="utf-8"))
        assert payload["lock_name"] == name
        assert payload["research_contract_version"] == 3
        assert payload["immutable"] is True
    assert baseline["verification_evidence_status"] == "reported"
    assert baseline["execution"]["source_snapshot_isolated"] is True
    protocol = json.loads(
        (stage2 / "protocol.draft.v2.json").read_text(encoding="utf-8")
    )
    required_protocol_concepts = {
        "research_question",
        "null_hypothesis",
        "alternative_hypothesis",
        "hypothesis_direction",
        "falsification_condition",
        "experimental_unit",
        "sample_population",
        "inclusion_criteria",
        "exclusion_criteria",
        "data_version",
        "data_split",
        "data_preprocessing",
        "experimental_group",
        "control_group",
        "minimum_reasonable_baseline",
        "current_project_baseline",
        "academic_standard_baseline",
        "strongest_feasible_baseline",
        "primary_metric",
        "secondary_metrics",
        "metric_implementation",
        "success_threshold",
        "minimum_meaningful_effect",
        "sample_size",
        "statistical_power",
        "randomization_method",
        "seeds",
        "repetitions",
        "statistical_test",
        "confidence_interval",
        "multiple_comparison_policy",
        "missing_data_policy",
        "outlier_policy",
        "failed_run_policy",
        "retry_rule",
        "timeout_rule",
        "stopping_rule",
        "budget",
        "software_versions",
        "hardware_environment",
        "model_versions",
        "api_versions",
        "dependency_lock",
        "logging_requirements",
        "telemetry_requirements",
        "artifact_preservation_requirements",
        "artifact_binding_method",
        "citation_and_source_recording",
        "quality_control",
        "reproduction_steps",
    }
    assert required_protocol_concepts <= set(protocol)
    preflight = json.loads(
        (stage2 / "preflight_report.v2.json").read_text(encoding="utf-8")
    )
    assert len(preflight["checks"]) == 18
    assert preflight["evidence_status_counts"]["reported"] > 0
    assert next(
        item
        for item in preflight["checks"]
        if item["check_id"] == "code_entry_executable"
    )["evidence_status"] == "verified"
    assert next(
        item
        for item in preflight["checks"]
        if item["check_id"] == "metric_unit_test_passed"
    )["evidence_status"] == "reported"
    assert {item["check_id"] for item in preflight["checks"]} == {
        "data_loadable",
        "schema_matches_protocol",
        "sample_size_reasonable",
        "inclusion_exclusion_executable",
        "split_boundary_verified",
        "no_obvious_data_leakage",
        "metric_unit_test_passed",
        "statistical_synthetic_test_passed",
        "code_entry_executable",
        "dependencies_installable",
        "hardware_meets_minimum",
        "logging_complete",
        "random_seed_fixable",
        "artifacts_preservable",
        "result_version_binding_valid",
        "failed_runs_detectable",
        "budget_estimate_reasonable",
        "sensitive_data_guard_passed",
    }
    gate = json.loads(
        (stage2 / "stage2_gate_report.v2.json").read_text(encoding="utf-8")
    )
    assert len(gate["freeze_conditions"]) == 18
    assert (stage2 / "protocol.draft.v2.md").is_file()
    assert (stage2 / "baseline_validation_summary.v2.md").is_file()


def test_post_freeze_change_creates_amendment_without_mutating_locks(
    tmp_path: Path,
) -> None:
    repository, study_id = _direction_approved(
        tmp_path, runnable_baseline=True
    )
    topics_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "generate_candidate_topics"
    )
    topics = repository.load_step_result(
        study_id, topics_step.step_instance_id
    )
    protocol_overrides = {
        "primary_metric": "accuracy",
        "metric_direction": "higher_is_better",
        "denominator": "all eligible test rows",
        "sample_size": "all six frozen local rows",
        "statistical_power": "descriptive fixture; no inferential power claim",
        "statistical_analysis": "paired bootstrap confidence interval",
        "statistical_test": "paired bootstrap",
        "confidence_interval": "95% percentile bootstrap interval",
        "falsification_condition": "effect does not meet the frozen rule",
        "success_threshold": "difference >= 0.05",
        "minimum_meaningful_effect": "0.05",
        "metric_implementation": "fixture accuracy implementation",
        "randomization_method": "fixed paired order",
        "seeds": [1],
        "tasks": ["local-classification"],
        "baseline_experiment_id": "baseline-v1",
        "baseline_action_id": "action-stage2-baseline",
        "run_baseline": True,
        "data_loadable": True,
        "data_schema_readable": True,
        "inclusion_exclusion_executable": True,
        "split_boundary_verified": True,
        "leakage_controls_verified": True,
        "metric_unit_test_passed": True,
        "statistical_synthetic_test_passed": True,
        "dependency_installable": True,
        "hardware_sufficient": True,
        "logging_validated": True,
        "artifact_preservation_validated": True,
        "binding_validated": True,
        "failure_detection_validated": True,
        "budget_reasonable": True,
        "sensitive_data_guard_passed": True,
        "compliance_cleared": True,
        "dependency_lock": "fixture lock",
        "quality_control": ["fixture check"],
        "reproduction_steps": ["run baseline-v1"],
        "approved_resource_substitutions": ["bounded fixture substitution"],
        "budget": {"max_seconds": 30},
    }
    select_stage_two_topic(
        repository,
        study_id,
        topics["recommended_topic_id"],
        overrides={"primary_outcome": "accuracy"},
        protocol_overrides=protocol_overrides,
    )
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)
    approve_stage_two_contract(repository, study_id)
    PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)
    lock_path = (
        repository.root
        / "studies"
        / study_id
        / "stage2"
        / "protocol.lock.json"
    )
    original = lock_path.read_bytes()

    amendment = propose_stage_two_amendment(
        repository,
        study_id,
        reason="Add a second seed after resource approval.",
        changes={"seeds": {"from": [1], "to": [1, 2]}},
        impact_scope=["protocol", "future runs"],
        treatment_results_viewed_before_change=False,
        requires_rerun=True,
        exploratory_downgrade=False,
    )

    assert amendment["amendment"]["sequence"] == 1
    assert amendment["gate"]["status"] == "awaiting_user"
    assert lock_path.read_bytes() == original
    amendment_path = lock_path.parent / "protocol_amendment_001.json"
    assert amendment_path.is_file()
