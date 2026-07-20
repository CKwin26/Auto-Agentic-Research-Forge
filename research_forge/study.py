from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .agent_runtime import backend_name, backend_status, model_name
from .benchmark import REGISTERED_TASKS, load_task
from .contracts import transition
from .literature import audit_stage1
from .models import (
    EventRecord,
    FrozenManifest,
    PlanEvidenceBinding,
    ProtectedManifest,
    ResearchPlanDraft,
    Stage,
)
from .runtime import (
    DEFAULT_CONTROLLED_CPU_IMAGE,
    ExecutionRuntime,
    RuntimeOptions,
    build_runtime,
)
from .root_cause_preflight import ReviewTarget, analyze_root_causes
from .service import freeze_literature_manifest
from .storage import (
    append_jsonl,
    load_state,
    read_json,
    safe_relative,
    save_state,
    sha256_file,
    sha256_tree,
    write_json_atomic,
)
from .study_models import (
    Stage2ArmDefinition,
    Stage2Audit,
    Stage2BackboneManifest,
    Stage2Cell,
    Stage2ManualAuditPlan,
    Stage2Protocol,
    Stage2RuntimeBinding,
    Stage2TaskBinding,
    StudyArm,
    StudyClaimRegistry,
)


STUDY_TASKS = [
    "textualclassificationsickaccuracy",
    "textualsimilaritysickspearmancorrelation",
    "coreferenceresolutionsupergluewscaccuracy",
]
STUDY_SEEDS = [0, 1, 2]
STUDY_SECONDARY_METRICS = [
    "citation_correctness",
    "experiment_detail_error_rate",
    "evidence_coverage",
    "task_native_score",
    "wall_clock_runtime_seconds",
    "verifier_abstention_rate",
    "failure_mode_count",
    "audit_false_positive_rate",
]
PUBLICATION_CONSTRUCT_METRICS = [
    "claim_retention_or_deletion",
    "semantic_change_type",
    "informativeness_or_usefulness",
]
PUBLICATION_TELEMETRY_METRICS = [
    "token_count",
    "model_call_count",
    "monetary_cost_usd",
]
SHARED_PROMPTS = [
    "experimenter.md",
    "study_finalizer.md",
    "study_gate_verifier.md",
    "study_gate_reviser.md",
    "study_claim_verifier.md",
]
SHARED_CONTROLLER_FILES = [
    "agent_runtime.py",
    "benchmark.py",
    "benchmark_models.py",
    "contracts.py",
    "models.py",
    "research_loop.py",
    "runner.py",
    "runtime.py",
    "service.py",
    "storage.py",
]
ITERATION_CAP = 1


def audit_stage2_publication_design(
    project: Path,
    *,
    contract_path: str | Path | None = None,
    protocol: dict[str, object] | None = None,
    root_cause_report: dict[str, object] | None = None,
    persist: bool = True,
):
    """Test a Stage 2 design against a locked strict-venue publication target."""

    from .publication_readiness import (
        EXPERIMENT_GATE_FILENAME,
        EXPERIMENT_TARGET_FILENAME,
        audit_publication_experiment_design,
        load_publication_experiment_target,
    )

    project = project.resolve()
    target_path = (
        Path(contract_path).resolve()
        if contract_path
        else project / EXPERIMENT_TARGET_FILENAME
    )
    target = load_publication_experiment_target(target_path)
    if Path(target.project_path).resolve() != project:
        raise ValueError(
            "publication experiment contract belongs to a different project: "
            f"{target.project_path}"
        )
    protocol_value = protocol or read_json(project / "stage2" / "protocol.json")
    design_preflight_path = project / "stage2" / "design_preflight.json"
    if not design_preflight_path.is_file():
        design_preflight_path = project / "synthesis" / "root_cause_preflight.json"
    root_value = root_cause_report or read_json(design_preflight_path)
    novelty_refresh = project / "design_revisions" / "novelty_refresh.json"
    report = audit_publication_experiment_design(
        target,
        protocol=protocol_value,
        root_cause_report=root_value,
        novelty_refresh_present=novelty_refresh.is_file(),
        source_paths=[
            str(target_path),
            str(project / "stage2" / "protocol.json"),
            str(design_preflight_path),
            str(novelty_refresh),
        ],
    )
    if persist:
        write_json_atomic(
            project / "design_revisions" / EXPERIMENT_GATE_FILENAME,
            report,
        )
    return report


def _project_stage2(project: Path) -> Path:
    return project / "stage2"


def _hash_jsonable(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_approved_plan(
    project: Path,
    *,
    task_ids: list[str],
    required_metrics: set[str],
) -> tuple[ResearchPlanDraft, PlanEvidenceBinding]:
    state = load_state(project)
    if state.stage not in {Stage.PLAN_REVIEW, Stage.BASELINE_PENDING, Stage.BASELINE_VERIFIED}:
        raise ValueError("Stage 2 protocol can only be frozen from plan_review or inspected later")
    if not state.latest_plan_draft:
        raise ValueError("Stage 2 requires an approved research plan")
    stage1 = audit_stage1(project)
    if not stage1.passed:
        raise ValueError("Stage 1 gate failed: " + "; ".join(stage1.violations))
    plan_path = project / "plans" / f"{state.latest_plan_draft}.json"
    plan = ResearchPlanDraft.model_validate(read_json(plan_path))
    binding = PlanEvidenceBinding.model_validate(read_json(project / "plan_evidence_binding.json"))
    if binding.plan_id != state.latest_plan_draft or binding.plan_hash != sha256_file(plan_path):
        raise ValueError("the latest plan is not bound to the approved Stage 1 evidence")
    if binding.selected_novelty_id != "novelty-02":
        raise ValueError("this Stage 2 study is preregistered for novelty-02")
    if not plan.ready_to_freeze or plan.clarifying_questions:
        raise ValueError("the approved plan is not freeze-ready")

    declared_tasks = {
        task_id
        for task_id in task_ids
        if any(task_id in dataset for dataset in plan.datasets)
    }
    if declared_tasks != set(task_ids):
        raise ValueError("the plan does not declare every selected frozen task pack")
    metrics = {metric.name for metric in plan.metrics}
    if metrics != required_metrics:
        missing = sorted(required_metrics - metrics)
        extra = sorted(metrics - required_metrics)
        raise ValueError(f"the plan metric set differs from the preregistration; missing={missing}, extra={extra}")
    if "same frozen Research Forge/Codex backbone" not in plan.baseline_definition:
        raise ValueError("the baseline does not freeze the shared Research Forge/Codex backbone")
    return plan, binding


def _runtime_binding(runtime: ExecutionRuntime) -> Stage2RuntimeBinding:
    attestation = runtime.attestation(evaluator_separated=True)
    required_true = [
        "isolation_verified",
        "candidate_evaluator_separated",
        "read_only_rootfs",
        "capabilities_dropped",
        "no_new_privileges",
        "controlled_environment",
        "capability_verified",
    ]
    missing = [name for name in required_true if attestation.get(name) is not True]
    if missing or attestation.get("runtime") != "docker" or attestation.get("network") != "none":
        raise ValueError("controlled Docker attestation is incomplete: " + ", ".join(missing))
    image_id = attestation.get("image_id")
    if not isinstance(image_id, str) or len(image_id) < 8:
        raise ValueError("controlled Docker image ID is unavailable")
    return Stage2RuntimeBinding(
        image=str(attestation["image"]),
        image_id=image_id,
        limits=dict(attestation["limits"]),
        capabilities=dict(attestation["capabilities"]),
    )


def _verified_codex_status() -> dict[str, object]:
    latest: dict[str, object] = {}
    for _ in range(3):
        latest = backend_status()
        if (
            latest.get("codex_sdk_installed") is True
            and latest.get("codex_authenticated") is True
        ):
            return latest
    return latest


def _copy_frozen_inputs(
    project: Path,
    staging: Path,
    *,
    binding: PlanEvidenceBinding,
    task_root: Path,
    task_ids: list[str],
    seeds: list[int],
) -> tuple[
    list[Stage2TaskBinding],
    dict[str, str],
    dict[str, str],
    dict[str, str],
    dict[str, str],
]:
    task_bindings: list[Stage2TaskBinding] = []
    task_hashes: dict[str, str] = {}
    for task_id in task_ids:
        task_candidate = task_root / task_id
        task_dir, spec = load_task(task_candidate if task_candidate.is_dir() else task_id)
        if not spec.runnable or spec.compute_tier != "cpu":
            raise ValueError(f"Stage 2 task must be runnable on CPU: {task_id}")
        missing_seeds = sorted(set(seeds) - set(spec.seeds))
        if missing_seeds:
            raise ValueError(
                f"Stage 2 task does not preregister selected seeds: {task_id}; missing={missing_seeds}"
            )
        destination = staging / "task_packs" / task_id
        shutil.copytree(
            task_dir,
            destination,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "activation"),
        )
        task_hash = sha256_tree(destination)
        task_hashes[task_id] = task_hash
        task_bindings.append(
            Stage2TaskBinding(
                task_id=task_id,
                snapshot_path=f"stage2/task_packs/{task_id}",
                task_hash=task_hash,
                primary_metric=spec.primary_metric,
                direction=spec.direction,
                baseline_score=spec.baseline_score,
                target_score=spec.target_score,
                iteration_cap=ITERATION_CAP,
                timeout_seconds=spec.timeout_seconds,
            )
        )

    source_hashes: dict[str, str] = {}
    for source_id in binding.source_ids:
        source = project / "literature" / "sources" / f"{source_id}.json"
        destination = staging / "evidence" / "sources" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        source_hashes[source_id] = sha256_file(destination)
    for relative in ["review.json", "approval.json"]:
        source = project / "literature" / relative
        destination = staging / "evidence" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    prompt_hashes: dict[str, str] = {}
    prompt_root = Path(__file__).resolve().parent / "prompts"
    for name in SHARED_PROMPTS:
        source = prompt_root / name
        destination = staging / "prompts" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        prompt_hashes[name] = sha256_file(destination)
    controller_hashes: dict[str, str] = {}
    package_root = Path(__file__).resolve().parent
    for name in SHARED_CONTROLLER_FILES:
        source = package_root / name
        destination = staging / "controller" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        controller_hashes[name] = sha256_file(destination)
    return task_bindings, task_hashes, source_hashes, prompt_hashes, controller_hashes


def _cells(task_ids: list[str], seeds: list[int]) -> list[Stage2Cell]:
    result: list[Stage2Cell] = []
    sequence = 0
    for arm in (StudyArm.BASELINE, StudyArm.TREATMENT):
        for task_id in task_ids:
            for seed in seeds:
                sequence += 1
                result.append(
                    Stage2Cell(
                        sequence=sequence,
                        cell_id=f"{arm.value}--{task_id}--seed-{seed}",
                        arm=arm,
                        task_id=task_id,
                        seed=seed,
                    )
                )
    return result


def _next_protocol_revision(project: Path) -> int:
    archived = 0
    for directory in ("stage2_superseded", "stage2_abandoned", "stage2_failed"):
        root = project / directory
        if root.is_dir():
            archived += len(list(root.glob("*/protocol.json")))
    return archived + 1


def _manifest_hashes(project: Path, stage2: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(item for item in stage2.rglob("*") if item.is_file()):
        if path.name in {"frozen_manifest.json", "audit.json"}:
            continue
        relative = path.relative_to(project).as_posix()
        hashes[relative] = sha256_file(path)
    for relative in ["research_contract.json", "literature_manifest.json", "plan_evidence_binding.json"]:
        path = project / relative
        if path.is_file():
            hashes[relative] = sha256_file(path)
    return hashes


def _pair_branch_order(
    task_ids: list[str], seeds: list[int], *, randomization_key: str
) -> dict[str, str]:
    """Create a deterministic, task-blocked and near-balanced frozen assignment."""
    assignments: dict[str, str] = {}
    for task_index, task_id in enumerate(task_ids):
        ranked = sorted(
            seeds,
            key=lambda seed: _hash_jsonable(
                {"key": randomization_key, "task_id": task_id, "seed": seed}
            ),
        )
        for rank, seed in enumerate(ranked):
            assignments[f"{task_id}--seed-{seed}"] = (
                "baseline_first" if (rank + task_index) % 2 == 0 else "treatment_first"
            )
    return assignments


def _validate_publication_auxiliary_contracts(
    project: Path, calibration_path: Path
) -> str:
    if project not in calibration_path.parents:
        raise ValueError("independent calibration contract must be stored inside the project")
    calibration = read_json(calibration_path)
    required_calibration = {
        "passed": calibration.get("passed") is True,
        "cross_family_evaluator": str(calibration.get("evaluator", "")).startswith(
            "external_evaluator_cross_family_"
        ),
        "macro_f1": float(calibration.get("locked_evaluation_macro_f1", 0.0)) >= 0.70,
        "coverage": float(calibration.get("locked_evaluation_coverage", 0.0)) >= 0.80,
        "human_not_fabricated": calibration.get("human_validation_complete") is False,
        "project_audit_pending": calibration.get("project_specific_human_audit") == "pending",
    }
    report_path = Path(str(calibration.get("calibration_report_path", ""))).resolve()
    required_calibration["report_hash"] = (
        report_path.is_file()
        and sha256_file(report_path) == calibration.get("calibration_report_sha256")
    )
    model = dict(calibration.get("model", {}))
    model_path = Path(str(model.get("model_path", "")))
    tokenizer_path = Path(str(model.get("tokenizer_path", "")))
    required_calibration["model_hash"] = (
        model_path.is_file() and sha256_file(model_path) == model.get("model_sha256")
    )
    required_calibration["tokenizer_hash"] = (
        tokenizer_path.is_file()
        and sha256_file(tokenizer_path) == model.get("tokenizer_sha256")
    )
    failures = [name for name, passed in required_calibration.items() if not passed]
    if failures:
        raise ValueError(
            "independent calibration contract failed semantic audit: " + ", ".join(failures)
        )

    novelty_path = project / "design_revisions" / "novelty_refresh.json"
    if not novelty_path.is_file():
        raise ValueError("publication protocol requires a contextual novelty refresh")
    novelty = read_json(novelty_path)
    positioning = dict(novelty.get("positioning_decision", {}))
    novelty_valid = (
        novelty.get("passed") is True
        and len(novelty.get("queries", [])) >= 3
        and len(novelty.get("screening_ledger", [])) >= 8
        and len(novelty.get("contribution_matrix", [])) >= 3
        and positioning.get("broad_pipeline_novelty") == "rejected"
        and positioning.get("claim_verification_mechanism_novelty") == "rejected"
        and positioning.get("evaluation_design_novelty") == "tentative_and_bounded"
    )
    if not novelty_valid:
        raise ValueError("contextual novelty refresh failed semantic audit")
    return str(calibration["evaluator"])


def _validate_secondary_evaluator_contract(
    project: Path, contract_path: Path, *, primary_evaluator: str
) -> str:
    """Fail closed unless a separately configured robustness evaluator is frozen.

    A calibration set establishes that the protected primary instrument can
    discriminate on its locked benchmark.  It is not a second instrument.  The
    second evaluator is deliberately an explicit, hash-bound contract so a
    manuscript cannot quietly relabel the primary evaluator as a robustness
    check after outcomes are visible.
    """
    if project not in contract_path.parents:
        raise ValueError("secondary evaluator contract must be stored inside the project")
    value = read_json(contract_path)
    evaluator = str(value.get("evaluator", "")).strip()
    required = {
        "passed": value.get("passed") is True,
        "independent": value.get("independent_from_primary") is True,
        "different_evaluator": bool(evaluator) and evaluator != primary_evaluator,
        "analysis_plan": isinstance(value.get("analysis_plan"), dict),
        "config_hash": isinstance(value.get("config_sha256"), str)
        and len(str(value.get("config_sha256"))) == 64,
    }
    failures = [name for name, passed in required.items() if not passed]
    if failures:
        raise ValueError(
            "secondary evaluator contract failed semantic audit: " + ", ".join(failures)
        )
    return contract_path.relative_to(project).as_posix()


def freeze_stage2_protocol(
    project: Path,
    *,
    task_root: str | Path = REGISTERED_TASKS / "airs",
    docker_image: str = DEFAULT_CONTROLLED_CPU_IMAGE,
    runtime: ExecutionRuntime | None = None,
    intent: str = "publication",
    publication_contract: str | Path | None = None,
    pilot_reason: str | None = None,
    task_ids: list[str] | None = None,
    seeds: list[int] | None = None,
    independent_calibration_contract: str | Path | None = None,
    secondary_evaluator_contract: str | Path | None = None,
) -> Stage2Protocol:
    project = project.resolve()
    if intent not in {"pilot", "publication"}:
        raise ValueError("Stage 2 intent must be pilot or publication")
    normalized_pilot_reason = (pilot_reason or "").strip()
    if intent == "pilot" and len(normalized_pilot_reason) < 10:
        raise ValueError(
            "pilot intent is an internal exception and requires an explicit "
            "non-publication reason of at least 10 characters"
        )
    if intent == "publication" and normalized_pilot_reason:
        raise ValueError("publication intent cannot include a pilot reason")
    stage2 = _project_stage2(project)
    if stage2.is_dir():
        audit = audit_stage2_protocol(project, persist=True)
        if not audit.passed:
            raise ValueError("existing Stage 2 protocol failed audit: " + "; ".join(audit.violations))
        existing_protocol = Stage2Protocol.model_validate(
            read_json(stage2 / "protocol.json")
        )
        if existing_protocol.study_intent != intent:
            if existing_protocol.study_intent == "pilot":
                raise ValueError(
                    "an existing pilot protocol cannot be promoted to publication; "
                    "freeze a new prospectively gated publication experiment"
                )
            raise ValueError(
                "an existing publication protocol cannot be reopened as a pilot"
            )
        if intent == "publication":
            publication_gate = audit_stage2_publication_design(
                project,
                contract_path=publication_contract,
                persist=True,
            )
            if not publication_gate.pre_experiment_gate_passed:
                raise ValueError(
                    "existing Stage 2 design is not eligible for publication intent: "
                    + "; ".join(publication_gate.violations)
                )
            if existing_protocol.publication_contract_id != publication_gate.contract_id:
                raise ValueError(
                    "existing Stage 2 protocol is bound to a different publication contract"
                )
        return existing_protocol

    publication_target = None
    publication_target_path = None
    if intent == "publication":
        from .publication_readiness import (
            EXPERIMENT_TARGET_FILENAME,
            load_publication_experiment_target,
        )

        publication_target_path = (
            Path(publication_contract).resolve()
            if publication_contract
            else project / EXPERIMENT_TARGET_FILENAME
        )
        publication_target = load_publication_experiment_target(
            publication_target_path
        )
        if Path(publication_target.project_path).resolve() != project:
            raise ValueError(
                "publication experiment contract belongs to a different project: "
                f"{publication_target.project_path}"
            )

    selected_tasks = list(task_ids or STUDY_TASKS)
    selected_seeds = list(seeds or STUDY_SEEDS)
    if len(selected_tasks) != len(set(selected_tasks)):
        raise ValueError("selected Stage 2 task IDs must be unique")
    if len(selected_seeds) != len(set(selected_seeds)):
        raise ValueError("selected Stage 2 seeds must be unique")
    if intent == "publication":
        assert publication_target is not None
        calibration_path = (
            Path(independent_calibration_contract).resolve()
            if independent_calibration_contract
            else project / "design_revisions" / "independent_calibration_contract.json"
        )
        secondary_evaluator_path = (
            Path(secondary_evaluator_contract).resolve()
            if secondary_evaluator_contract
            else project / "design_revisions" / "secondary_evaluator_contract.json"
        )
        preliminary_failures: list[str] = []
        if len(selected_tasks) < publication_target.minimum_tasks:
            preliminary_failures.append(
                f"publication protocol requires at least {publication_target.minimum_tasks} tasks"
            )
        if len(selected_seeds) < publication_target.minimum_seeds_per_task:
            preliminary_failures.append(
                f"publication protocol requires at least {publication_target.minimum_seeds_per_task} seeds"
            )
        if not calibration_path.is_file():
            preliminary_failures.append(
                "publication protocol requires a frozen independent calibration contract"
            )
        if not secondary_evaluator_path.is_file():
            preliminary_failures.append(
                "publication protocol requires a frozen secondary evaluator contract"
            )
        if preliminary_failures:
            from .publication_readiness import (
                EXPERIMENT_GATE_FILENAME,
                audit_publication_experiment_design,
            )

            preliminary_gate = audit_publication_experiment_design(
                publication_target,
                protocol={
                    "protocol_id": "unfrozen-protocol",
                    "study_intent": "publication",
                    "publication_contract_id": publication_target.contract_id,
                    "tasks": [{"task_id": item} for item in selected_tasks],
                    "seeds": selected_seeds,
                    "primary_metric": "unsupported_claim_rate",
                    "secondary_metrics": STUDY_SECONDARY_METRICS,
                },
                root_cause_report={
                    "findings": [
                        {"code": code, "resolved": False}
                        for code in publication_target.blocking_root_cause_codes
                    ]
                },
                novelty_refresh_present=(
                    project / "design_revisions" / "novelty_refresh.json"
                ).is_file(),
                source_paths=[str(publication_target_path or "")],
            )
            write_json_atomic(
                project / "design_revisions" / EXPERIMENT_GATE_FILENAME,
                preliminary_gate,
            )
            raise ValueError(
                "Stage 2 publication-intent hard gate failed before execution: "
                + "; ".join(preliminary_failures)
            )
        protected_evaluator = _validate_publication_auxiliary_contracts(
            project, calibration_path
        )
        secondary_evaluator_binding = _validate_secondary_evaluator_contract(
            project, secondary_evaluator_path, primary_evaluator=protected_evaluator
        )
        calibration_binding = calibration_path.relative_to(project).as_posix()
        secondary_metrics = [
            *STUDY_SECONDARY_METRICS,
            *PUBLICATION_CONSTRUCT_METRICS,
            *PUBLICATION_TELEMETRY_METRICS,
        ]
    else:
        protected_evaluator = "hybrid_structural_plus_arm_blinded_codex"
        calibration_binding = None
        secondary_evaluator_binding = None
        secondary_metrics = list(STUDY_SECONDARY_METRICS)
    required_metrics = {"unsupported_claim_rate", *secondary_metrics}
    plan, binding = _validate_approved_plan(
        project,
        task_ids=selected_tasks,
        required_metrics=required_metrics,
    )
    if backend_name() != "codex":
        raise ValueError("the frozen Stage 2 backbone requires the Codex backend")
    codex = _verified_codex_status()
    if codex.get("codex_sdk_installed") is not True:
        raise ValueError("the frozen Stage 2 backbone requires the openai-codex SDK")
    if codex.get("codex_authenticated") is not True:
        raise ValueError("the frozen Stage 2 backbone requires an authenticated Codex session")
    runtime_engine = runtime or build_runtime(
        RuntimeOptions(kind="docker", image=docker_image)
    )
    frozen_runtime = _runtime_binding(runtime_engine)
    protocol_revision = _next_protocol_revision(project)
    controller_run_root = (
        Path.home()
        / ".research-forge-study"
        / f"{binding.plan_hash[:12]}-r{protocol_revision}"
    ).resolve()
    try:
        str(controller_run_root).encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Stage 2 controller run root must contain ASCII characters only") from exc

    staging = project / f".stage2-{uuid.uuid4().hex[:8]}.tmp"
    if staging.exists():
        raise FileExistsError(f"Stage 2 staging path already exists: {staging}")
    staging.mkdir()
    try:
        (
            task_bindings,
            task_hashes,
            source_hashes,
            prompt_hashes,
            controller_hashes,
        ) = _copy_frozen_inputs(
            project,
            staging,
            binding=binding,
            task_root=Path(task_root).resolve(),
            task_ids=selected_tasks,
            seeds=selected_seeds,
        )
        backbone = Stage2BackboneManifest(
            backend="codex",
            model=model_name(),
            codex_sdk_version=str(codex["codex_sdk_version"]),
            codex_account_type=str(codex["codex_account_type"]),
            codex_plan_type=str(codex["codex_plan_type"]),
            provider_name=str(codex.get("provider_name") or "openai-managed"),
            provider_base_url=str(codex.get("provider_base_url") or "managed"),
            provider_config_hash=str(codex.get("provider_config_hash") or "0" * 64),
            credential_mode=str(codex.get("codex_account_type") or "unknown"),
            provider_billing_contract_hash=str(
                codex.get("provider_billing_contract_hash") or "0" * 64
            ),
            provider_billing_group=str(
                codex.get("provider_billing_group") or "managed-subscription"
            ),
            controller_run_root=str(controller_run_root),
            plan_id=binding.plan_id,
            plan_hash=binding.plan_hash,
            review_id=binding.review_id,
            review_hash=binding.review_hash,
            selected_novelty_id=binding.selected_novelty_id or "",
            prompt_hashes=prompt_hashes,
            controller_hashes=controller_hashes,
            source_hashes=source_hashes,
            task_hashes=task_hashes,
            task_order=selected_tasks,
            seeds=selected_seeds,
            runtime=frozen_runtime,
        )
        write_json_atomic(staging / "backbone_manifest.json", backbone)
        write_json_atomic(staging / "claim_schema.json", StudyClaimRegistry.model_json_schema())
        backbone_hash = sha256_file(staging / "backbone_manifest.json")
        protocol_seed = {
            "plan_hash": binding.plan_hash,
            "review_hash": binding.review_hash,
            "backbone_hash": backbone_hash,
            "tasks": task_hashes,
            "intent": intent,
            "publication_contract_id": (
                publication_target.contract_id if publication_target else None
            ),
        }
        protocol = Stage2Protocol(
            protocol_revision=protocol_revision,
            protocol_id=f"stage2-{_hash_jsonable(protocol_seed)[:12]}",
            study_intent=intent,
            publication_contract_id=(
                publication_target.contract_id
                if publication_target is not None
                else None
            ),
            pilot_reason=(normalized_pilot_reason if intent == "pilot" else None),
            title=plan.title,
            research_question=plan.research_question,
            hypothesis=plan.hypothesis,
            plan_id=binding.plan_id,
            plan_hash=binding.plan_hash,
            review_id=binding.review_id,
            review_hash=binding.review_hash,
            selected_novelty_id="novelty-02",
            backbone_manifest_hash=backbone_hash,
            arms=[
                Stage2ArmDefinition(
                    arm=StudyArm.BASELINE,
                    initial_finalizer="Use the shared frozen finalizer and emit candidate evidence links without verifier-based rejection or revision.",
                    verification_gate="disabled",
                    revision_limit=0,
                    removal_after_failed_recheck=False,
                ),
                Stage2ArmDefinition(
                    arm=StudyArm.TREATMENT,
                    initial_finalizer="Use the same shared frozen finalizer before any verifier feedback or claim revision is introduced.",
                    verification_gate="reject_revise_recheck",
                    revision_limit=1,
                    removal_after_failed_recheck=True,
                ),
            ],
            tasks=task_bindings,
            seeds=selected_seeds,
            cells=_cells(selected_tasks, selected_seeds),
            secondary_metrics=secondary_metrics,
            counterfactual_source=(
                "shared_run_artifact" if intent == "publication" else "independent_stochastic_runs"
            ),
            branch_order=(
                "pair_randomized" if intent == "publication" else "blocked_baseline_then_treatment"
            ),
            pair_branch_order=(
                _pair_branch_order(
                    selected_tasks,
                    selected_seeds,
                    randomization_key=_hash_jsonable(protocol_seed),
                )
                if intent == "publication"
                else {}
            ),
            pair_execution_concurrency=(2 if intent == "publication" else 1),
            independent_calibration_contract=calibration_binding,
            secondary_evaluator_contract=secondary_evaluator_binding,
            evidence_gate_specification=(
                {
                    "claim_extraction_prompt_sha256": prompt_hashes["study_claim_verifier.md"],
                    "evidence_matching_prompt_sha256": prompt_hashes["study_gate_verifier.md"],
                    "decision_policy": "reject_revise_recheck_once_then_remove",
                    "allowed_actions": ["retain", "revise", "remove", "abstain"],
                    "decision_trace_schema": "study_gate_trace.v1",
                }
                if intent == "publication"
                else {}
            ),
            construct_analysis_requirements=(
                [
                    "claim_retention_deletion",
                    "semantic_change_distribution",
                    "informativeness_usefulness",
                    "per_task_effects",
                ]
                if intent == "publication"
                else []
            ),
            pair_level_table=(intent == "publication"),
            protected_evaluator=protected_evaluator,
            telemetry_schema=(PUBLICATION_TELEMETRY_METRICS if intent == "publication" else []),
            telemetry_contract=(
                {
                    "wall_clock_definition": "active_attempt_seconds",
                    "required_fields": ["active_attempt_seconds", "token_count", "model_call_count", "monetary_cost_usd"],
                    "aggregation": "sum_completed_cell_active_attempt_seconds",
                }
                if intent == "publication"
                else {}
            ),
            manual_audit=Stage2ManualAuditPlan(
                total_claims=2 * len(selected_tasks) * 8
            ),
            max_completed_cells=2 * len(selected_tasks) * len(selected_seeds),
            stop_conditions=plan.stop_conditions,
        )
        design_preflight = analyze_root_causes(
            project_name=project.name,
            plan=plan.model_dump(mode="json"),
            protocol=protocol.model_dump(mode="json"),
            backbone=backbone.model_dump(mode="json"),
            target=(
                ReviewTarget.PUBLICATION
                if intent == "publication"
                else ReviewTarget.EXECUTION
            ),
            source_paths=[
                "research_contract.json",
                "stage2/protocol.json",
                "stage2/backbone_manifest.json",
            ],
        )
        if intent == "publication":
            from .publication_readiness import (
                EXPERIMENT_GATE_FILENAME,
                audit_publication_experiment_design,
            )

            assert publication_target is not None
            assert publication_target_path is not None
            publication_gate = audit_publication_experiment_design(
                publication_target,
                protocol=protocol.model_dump(mode="json"),
                root_cause_report=design_preflight.model_dump(mode="json"),
                novelty_refresh_present=(
                    project / "design_revisions" / "novelty_refresh.json"
                ).is_file(),
                source_paths=[
                    str(publication_target_path),
                    "stage2/protocol.json",
                    "stage2/design_preflight.json",
                ],
            )
            write_json_atomic(
                project / "design_revisions" / EXPERIMENT_GATE_FILENAME,
                publication_gate,
            )
            if not publication_gate.pre_experiment_gate_passed:
                raise ValueError(
                    "Stage 2 publication-intent hard gate failed before execution: "
                    + "; ".join(publication_gate.violations)
                )
            write_json_atomic(
                staging / "publication_experiment_contract.json",
                publication_target,
            )
        design_repair_path = (
            project / "design_revisions" / "publication_design_repair.json"
        )
        if design_repair_path.is_file():
            from .publication_readiness import stage2_design_repair_violations

            design_repair = read_json(design_repair_path)
            open_root_codes = {
                item.code for item in design_preflight.findings if not item.resolved
            }
            repair_violations = stage2_design_repair_violations(
                design_repair,
                open_root_cause_codes=open_root_codes,
                task_count=len(protocol.tasks),
                seed_count=len(protocol.seeds),
                novelty_refresh_present=(
                    project / "design_revisions" / "novelty_refresh.json"
                ).is_file(),
            )
            write_json_atomic(
                project
                / "design_revisions"
                / "latest_stage2_design_repair_audit.json",
                {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "repair_report": str(design_repair_path),
                    "repair_report_sha256": sha256_file(design_repair_path),
                    "protocol_id": protocol.protocol_id,
                    "task_count": len(protocol.tasks),
                    "seed_count": len(protocol.seeds),
                    "open_root_cause_codes": sorted(open_root_codes),
                    "passed": not repair_violations,
                    "violations": repair_violations,
                },
            )
            if repair_violations:
                raise ValueError(
                    "Stage 2 design still violates an active publication repair: "
                    + "; ".join(repair_violations)
                )
        if not design_preflight.execution_allowed:
            raise ValueError("Stage 2 design root-cause preflight blocked execution")
        write_json_atomic(staging / "protocol.json", protocol)
        write_json_atomic(staging / "design_preflight.json", design_preflight)
        if intent == "pilot":
            write_json_atomic(
                staging / "pilot_intent.json",
                {
                    "schema_version": 1,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "intent": "pilot",
                    "reason": normalized_pilot_reason,
                    "publication_submission_ready": False,
                    "maximum_claim_tier": design_preflight.maximum_claim_tier,
                    "prohibited_routes": [
                        "publication",
                        "external_submission_approval",
                    ],
                    "promotion_policy": (
                        "Pilot evidence cannot be promoted in place. Publication requires "
                        "a locked venue contract and a new prospectively gated experiment."
                    ),
                },
            )

        protected_paths = [
            path
            for path in sorted(item for item in staging.rglob("*") if item.is_file())
            if (
                "evaluator" in path.parts
                or "evidence" in path.parts
                or "prompts" in path.parts
                or "controller" in path.parts
                or path.name
                in {
                    "protocol.json",
                    "backbone_manifest.json",
                    "claim_schema.json",
                    "design_preflight.json",
                    "pilot_intent.json",
                    "publication_experiment_contract.json",
                }
            )
        ]
        protected = ProtectedManifest(
            hashes={
                f"stage2/{path.relative_to(staging).as_posix()}": sha256_file(path)
                for path in protected_paths
            }
        )
        write_json_atomic(staging / "protected_manifest.json", protected)
        staging.replace(stage2)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    write_json_atomic(project / "research_contract.json", plan)
    freeze_literature_manifest(project, source_ids=binding.source_ids)
    frozen = FrozenManifest(hashes=_manifest_hashes(project, stage2))
    write_json_atomic(stage2 / "frozen_manifest.json", frozen)

    audit = audit_stage2_protocol(project, persist=True)
    if not audit.passed:
        raise ValueError("Stage 2 protocol failed its freeze audit: " + "; ".join(audit.violations))

    state = load_state(project)
    if state.stage == Stage.PLAN_REVIEW:
        transition(
            project,
            state,
            Stage.CONTRACT_FROZEN,
            "stage2_protocol_frozen",
            protocol_id=protocol.protocol_id,
        )
        transition(
            project,
            state,
            Stage.BASELINE_PENDING,
            "stage2_baseline_matrix_opened",
            planned_baseline_cells=len(selected_tasks) * len(selected_seeds),
        )
        save_state(project, state)
    append_jsonl(
        project / "events.jsonl",
        EventRecord(
            event="stage2_protocol_audited",
            from_stage=state.stage,
            to_stage=state.stage,
            details={"protocol_id": protocol.protocol_id, "checks": len(audit.checks)},
        ),
    )
    audit_stage2_protocol(project, persist=True)
    return protocol


def supersede_empty_stage2_protocol(project: Path, *, reason: str) -> Path:
    """Archive a pre-data Stage 2 protocol so a corrected revision can be frozen."""
    project = project.resolve()
    if len(reason.strip()) < 10:
        raise ValueError("a concrete supersession reason is required")
    stage2 = project / "stage2"
    if not stage2.is_dir():
        raise FileNotFoundError("there is no Stage 2 protocol to supersede")
    completed = list(stage2.glob("r/*/*/complete.json")) + list(
        stage2.glob("runs/*/*/complete.json")
    )
    invalid = list(stage2.glob("r/*/*/invalid.json")) + list(
        stage2.glob("runs/*/*/invalid.json")
    )
    if completed or invalid:
        raise ValueError("a Stage 2 protocol cannot be superseded after any cell is complete or invalid")
    state = load_state(project)
    if state.stage != Stage.BASELINE_PENDING or state.run_count != 0:
        raise ValueError("only an empty baseline_pending Stage 2 protocol can be superseded")
    raw_protocol = read_json(stage2 / "protocol.json")
    protocol_id = str(raw_protocol.get("protocol_id") or "unknown-protocol")
    archive_root = project / "stage2_superseded"
    archive_root.mkdir(parents=True, exist_ok=True)
    destination = archive_root / protocol_id
    if destination.exists():
        raise FileExistsError(f"superseded protocol archive already exists: {destination}")
    stage2.replace(destination)
    write_json_atomic(
        destination / "supersession.json",
        {
            "schema_version": 1,
            "superseded_at": datetime.now(timezone.utc).isoformat(),
            "protocol_id": protocol_id,
            "reason": reason.strip(),
            "completed_cells": 0,
            "invalid_cells": 0,
        },
    )
    transition(
        project,
        state,
        Stage.PAUSED,
        "stage2_protocol_superseded_pre_data",
        protocol_id=protocol_id,
        reason=reason.strip(),
    )
    transition(
        project,
        state,
        Stage.PLAN_REVIEW,
        "stage2_protocol_revision_opened",
        superseded_protocol_id=protocol_id,
    )
    state.baseline_run_id = None
    save_state(project, state)
    return destination


def archive_failed_stage2_protocol(project: Path, *, reason: str) -> Path:
    """Retain a partially executed failed protocol and prohibit outcome promotion."""
    project = project.resolve()
    if len(reason.strip()) < 10:
        raise ValueError("a concrete failure reason is required")
    stage2 = project / "stage2"
    archive_root = project / "stage2_failed"
    source = stage2
    if not source.is_dir():
        resumable = [
            path
            for path in archive_root.glob("*")
            if path.is_dir()
            and (path / "protocol.json").is_file()
            and not (path / "failure_termination.json").exists()
        ]
        if len(resumable) != 1:
            raise FileNotFoundError("there is no Stage 2 protocol to archive")
        source = resumable[0]
    raw_protocol = read_json(source / "protocol.json")
    protocol_id = str(raw_protocol.get("protocol_id") or "unknown-protocol")
    completed = sorted(source.glob("r/*/*/complete.json"))
    invalid = sorted(source.glob("r/*/*/invalid.json"))
    shared_invalid = sorted(source.glob("shared/pairs/*/invalid.json"))
    if not completed and not invalid and not shared_invalid:
        raise ValueError("use empty-protocol supersession when no execution artifact exists")
    state = load_state(project)
    if state.stage not in {
        Stage.BASELINE_PENDING,
        Stage.BASELINE_VERIFIED,
        Stage.EXPERIMENT_DESIGN,
        Stage.EXPERIMENT_RUNNING,
        Stage.RESULT_REVIEW,
    }:
        raise ValueError("failed protocol archival requires an active Stage 2 state")
    archive_root.mkdir(parents=True, exist_ok=True)
    destination = archive_root / protocol_id
    if destination.exists() and source != destination:
        raise FileExistsError(f"failed protocol archive already exists: {destination}")
    if source != destination:
        try:
            source.replace(destination)
        except PermissionError:
            # Windows can reject os.replace for a directory just closed by a worker.
            # shutil.move preserves the whole artifact tree and is idempotently
            # finalized below; no outcome files are altered.
            shutil.move(str(source), str(destination))
    write_json_atomic(
        destination / "failure_termination.json",
        {
            "schema_version": 1,
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "protocol_id": protocol_id,
            "reason": reason.strip(),
            "completed_cells": len(completed),
            "invalid_cells": len(invalid),
            "shared_invalid_pairs": len(shared_invalid),
            "eligible_for_primary_analysis": False,
            "eligible_for_outcome_promotion": False,
            "reuse_policy": "No controller, finalizer, branch, or evaluation output from this revision may be reused in a successor formal protocol.",
        },
    )
    transition(
        project,
        state,
        Stage.PAUSED,
        "stage2_protocol_archived_after_system_failure",
        protocol_id=protocol_id,
        reason=reason.strip(),
        completed_cells=len(completed),
    )
    transition(
        project,
        state,
        Stage.PLAN_REVIEW,
        "stage2_protocol_repair_revision_opened",
        failed_protocol_id=protocol_id,
    )
    state.baseline_run_id = None
    state.run_count = 0
    save_state(project, state)
    return destination


def audit_stage2_protocol(project: Path, *, persist: bool = False) -> Stage2Audit:
    project = project.resolve()
    stage2 = _project_stage2(project)
    checks: dict[str, bool] = {}
    violations: list[str] = []
    protocol: Stage2Protocol | None = None
    backbone: Stage2BackboneManifest | None = None

    def check(name: str, passed: bool, message: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            violations.append(message)

    try:
        stage1 = audit_stage1(project)
        check("stage1_gate_still_valid", stage1.passed, "Stage 1 evidence gate no longer passes")
    except Exception as exc:
        check("stage1_gate_still_valid", False, f"could not audit Stage 1: {exc}")

    try:
        protocol = Stage2Protocol.model_validate(read_json(stage2 / "protocol.json"))
        check("protocol_schema_valid", True, "")
    except Exception as exc:
        check("protocol_schema_valid", False, f"Stage 2 protocol is invalid: {exc}")
    try:
        backbone = Stage2BackboneManifest.model_validate(
            read_json(stage2 / "backbone_manifest.json")
        )
        check("backbone_manifest_valid", True, "")
    except Exception as exc:
        check("backbone_manifest_valid", False, f"Stage 2 backbone manifest is invalid: {exc}")

    if protocol and backbone:
        check(
            "protocol_bound_to_backbone",
            protocol.backbone_manifest_hash == sha256_file(stage2 / "backbone_manifest.json"),
            "Stage 2 protocol is not bound to the frozen backbone manifest",
        )
        check(
            "plan_and_review_binding_valid",
            protocol.plan_id == backbone.plan_id
            and protocol.plan_hash == backbone.plan_hash
            and protocol.review_id == backbone.review_id
            and protocol.review_hash == backbone.review_hash
            and protocol.selected_novelty_id == backbone.selected_novelty_id,
            "Stage 2 plan/review/novelty binding is inconsistent",
        )
        task_hashes_match = all(
            (stage2 / "task_packs" / task.task_id).is_dir()
            and sha256_tree(stage2 / "task_packs" / task.task_id) == task.task_hash
            and backbone.task_hashes.get(task.task_id) == task.task_hash
            for task in protocol.tasks
        )
        check("task_snapshots_unchanged", task_hashes_match, "a frozen Stage 2 task pack changed")
        prompt_hashes_match = all(
            (stage2 / "prompts" / name).is_file()
            and sha256_file(stage2 / "prompts" / name) == expected
            for name, expected in backbone.prompt_hashes.items()
        )
        check("prompt_stack_unchanged", prompt_hashes_match, "the frozen Stage 2 prompt stack changed")
        controller_snapshots_match = all(
            (stage2 / "controller" / name).is_file()
            and sha256_file(stage2 / "controller" / name) == expected
            for name, expected in backbone.controller_hashes.items()
        )
        check(
            "controller_snapshots_unchanged",
            controller_snapshots_match,
            "the frozen shared controller snapshot changed",
        )
        live_controller_matches = all(
            (Path(__file__).resolve().parent / name).is_file()
            and sha256_file(Path(__file__).resolve().parent / name) == expected
            for name, expected in backbone.controller_hashes.items()
        )
        check(
            "live_controller_matches_frozen",
            live_controller_matches,
            "the live shared controller differs from the frozen Stage 2 snapshot",
        )
        source_hashes_match = all(
            (stage2 / "evidence" / "sources" / f"{source_id}.json").is_file()
            and sha256_file(stage2 / "evidence" / "sources" / f"{source_id}.json") == expected
            for source_id, expected in backbone.source_hashes.items()
        )
        check("source_packet_unchanged", source_hashes_match, "the frozen Stage 2 source packet changed")
        check(
            "docker_isolation_frozen",
            backbone.runtime.runtime == "docker"
            and backbone.runtime.network == "none"
            and backbone.runtime.read_only_rootfs
            and backbone.runtime.capabilities_dropped
            and backbone.runtime.no_new_privileges
            and backbone.runtime.candidate_evaluator_separated
            and backbone.runtime.controlled_environment
            and backbone.runtime.capability_verified,
            "controlled Docker isolation is not fully frozen",
        )
        run_root = Path(backbone.controller_run_root)
        try:
            str(run_root).encode("ascii")
            run_root_ascii = run_root.is_absolute()
        except UnicodeEncodeError:
            run_root_ascii = False
        check(
            "controller_run_root_is_ascii_absolute",
            run_root_ascii,
            "controller run root must be an absolute ASCII-only path for Docker bind mounts",
        )
        expected_cells = 2 * len(protocol.tasks) * len(protocol.seeds)
        check(
            "factorial_design_exact",
            len(protocol.cells) == expected_cells,
            f"Stage 2 does not contain the expected {expected_cells} factorial cells",
        )

    try:
        protected = ProtectedManifest.model_validate(read_json(stage2 / "protected_manifest.json"))
        protected_ok = all(
            safe_relative(project, relative).is_file()
            and sha256_file(safe_relative(project, relative)) == expected
            for relative, expected in protected.hashes.items()
        )
        check("protected_artifacts_unchanged", protected_ok, "a protected Stage 2 artifact changed")
    except Exception as exc:
        check("protected_artifacts_unchanged", False, f"protected manifest is invalid: {exc}")

    try:
        frozen = FrozenManifest.model_validate(read_json(stage2 / "frozen_manifest.json"))
        frozen_ok = all(
            safe_relative(project, relative).is_file()
            and sha256_file(safe_relative(project, relative)) == expected
            for relative, expected in frozen.hashes.items()
        )
        check("frozen_artifacts_unchanged", frozen_ok, "a frozen Stage 2 artifact changed")
    except Exception as exc:
        check("frozen_artifacts_unchanged", False, f"frozen manifest is invalid: {exc}")

    baseline_cells = len(list((stage2 / "r" / "b").glob("*/complete.json"))) if (stage2 / "r" / "b").is_dir() else 0
    treatment_cells = len(list((stage2 / "r" / "t").glob("*/complete.json"))) if (stage2 / "r" / "t").is_dir() else 0
    audit = Stage2Audit(
        passed=bool(checks) and all(checks.values()),
        checks=checks,
        violations=violations,
        protocol_id=protocol.protocol_id if protocol else None,
        planned_cells=len(protocol.cells) if protocol else 0,
        baseline_cells_completed=baseline_cells,
        treatment_cells_completed=treatment_cells,
        docker_isolation_frozen=checks.get("docker_isolation_frozen", False),
    )
    if persist and stage2.is_dir():
        write_json_atomic(stage2 / "audit.json", audit)
    return audit


def abandon_preexecution_stage2_protocol(project: Path, *, reason: str) -> Path:
    """Archive an infrastructure-invalid protocol only when no experiment reached execution."""
    project = project.resolve()
    if len(reason.strip()) < 10:
        raise ValueError("a concrete abandonment reason is required")
    stage2 = project / "stage2"
    if not stage2.is_dir():
        raise FileNotFoundError("there is no Stage 2 protocol to abandon")
    if list(stage2.glob("r/*/*/complete.json")):
        raise ValueError("a protocol with completed cells cannot use pre-execution abandonment")
    invalid_paths = list(stage2.glob("r/*/*/invalid.json"))
    if not invalid_paths:
        raise ValueError("pre-execution abandonment requires an invalid cell record")
    evidence_hashes: dict[str, str] = {}
    for invalid_path in invalid_paths:
        cell_dir = invalid_path.parent
        records = list(cell_dir.glob("controller/*/projects/*/runs/*/record.json"))
        stderr_logs = list(cell_dir.glob("controller/*/projects/*/runs/*/trial-*/stderr.log"))
        if not records or not stderr_logs:
            raise ValueError("invalid cell lacks the run evidence required for abandonment")
        for record_path in records:
            record = read_json(record_path)
            trials = record.get("trials")
            if (
                record.get("valid") is not False
                or record.get("aggregate_metrics")
                or not isinstance(trials, list)
                or not trials
                or any(trial.get("metrics") for trial in trials)
            ):
                raise ValueError("invalid cell contains executed metrics and cannot be abandoned")
            evidence_hashes[record_path.relative_to(project).as_posix()] = sha256_file(record_path)
        for log_path in stderr_logs:
            stderr = log_path.read_text(encoding="utf-8", errors="replace")
            if "can't open file '/workspace/experiment/run_experiment.py'" not in stderr or (
                "Input/output error" not in stderr
            ):
                raise ValueError("invalid cell is not the recognized Docker Unicode bind failure")
            evidence_hashes[log_path.relative_to(project).as_posix()] = sha256_file(log_path)
    state = load_state(project)
    if state.stage != Stage.BASELINE_PENDING or state.run_count != 0:
        raise ValueError("pre-execution abandonment requires baseline_pending with zero runs")
    raw_protocol = read_json(stage2 / "protocol.json")
    protocol_id = str(raw_protocol.get("protocol_id") or "unknown-protocol")
    archive_root = project / "stage2_abandoned"
    archive_root.mkdir(parents=True, exist_ok=True)
    destination = archive_root / protocol_id
    if destination.exists():
        raise FileExistsError(f"abandoned protocol archive already exists: {destination}")
    stage2.replace(destination)
    write_json_atomic(
        destination / "abandonment.json",
        {
            "schema_version": 1,
            "abandoned_at": datetime.now(timezone.utc).isoformat(),
            "protocol_id": protocol_id,
            "reason": reason.strip(),
            "classification": "pre_execution_docker_unicode_bind_io_error",
            "completed_cells": 0,
            "experiment_metrics_observed": 0,
            "evidence_hashes": evidence_hashes,
        },
    )
    transition(
        project,
        state,
        Stage.PAUSED,
        "stage2_protocol_abandoned_pre_execution",
        protocol_id=protocol_id,
        invalid_cells=len(invalid_paths),
    )
    transition(
        project,
        state,
        Stage.PLAN_REVIEW,
        "stage2_protocol_revision_opened",
        abandoned_protocol_id=protocol_id,
    )
    state.baseline_run_id = None
    save_state(project, state)
    return destination
