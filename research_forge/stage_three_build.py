"""Profile-driven construction boundary before formal Stage 3 execution.

This module separates the scientific specification frozen in Stage 2 from the
concrete execution implementation frozen in Stage 3.  Feasibility probes are
deliberately non-evidentiary and can never be promoted into a formal run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from .experiment_execution import (
    experiment_for_action,
    load_project_experiment_manifest,
    run_declared_experiment,
)
from .storage import (
    read_json,
    sha256_file,
    write_json_atomic,
    write_text_atomic,
)
from .workflow_domain import (
    ArtifactRole,
    ArtifactStatus,
    BuildAssetStrategy,
    EstimandSpecification,
    ExperimentBlueprint,
    ExperimentBuildItem,
    ExperimentBuildPlan,
    ExecutionStatus,
    ExecutionPackageSeal,
    ExecutionTrustLevel,
    ExecutorType,
    MVPFeasibilityReceipt,
    ProfileCapabilityStatus,
    ProtocolStatus,
    Phase,
    ScientificSpecificationSeal,
    Stage3BuildMode,
    Stage3HandoffPackage,
    Stage3Profile,
    StepDefinition,
    WorkflowRepository,
    is_secret_path,
    stable_id,
)

_PROFILE_V1_GENERATION_TIMEOUT_SECONDS = max(
    60,
    int(
        os.getenv(
            "RESEARCH_FORGE_STAGE3_BUILD_TIMEOUT_SECONDS",
            str(30 * 60),
        )
    ),
)


class Stage3BuildAdmissionError(ValueError):
    """The scientific handoff is insufficient or internally inconsistent."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = list(dict.fromkeys(violations))
        super().__init__("; ".join(self.violations))


def stage3_contract_readiness_violations(
    contract: Any,
) -> list[str]:
    """Return actionable Stage 2 contract gaps before Stage 3 construction.

    These checks deliberately cover only fields that Stage 2 has scientific
    authority to freeze.  Concrete commands, file hashes, and environment
    locks remain Stage 3 build responsibilities.
    """

    violations: list[str] = []
    if contract.experiment_profile is None:
        violations.append(
            "CONTRACT_PROFILE_MISSING: select a certified Stage 3 "
            "experiment Profile"
        )
    if not contract.tasks:
        violations.append(
            "FORMAL_TASKS_MISSING: declare at least one formal evaluation task"
        )
    if not str(contract.baseline.get("experiment_id") or "").strip():
        violations.append(
            "BASELINE_ID_MISSING: declare the conceptual baseline experiment"
        )
    if not str(contract.baseline.get("action_id") or "").strip():
        violations.append(
            "BASELINE_ACTION_MISSING: reserve a baseline action binding"
        )
    if not str(contract.treatment.get("experiment_id") or "").strip():
        violations.append(
            "TREATMENT_ID_MISSING: declare the conceptual treatment experiment"
        )
    if not str(contract.treatment.get("action_id") or "").strip():
        violations.append(
            "TREATMENT_ACTION_MISSING: reserve a treatment action binding"
        )
    threshold = contract.statistical_rules.get("effect_threshold")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        violations.append(
            "NUMERIC_EFFECT_THRESHOLD_MISSING: freeze a numeric minimum "
            "effect before formal execution"
        )
    placeholder_tasks = {
        "formal-primary-task",
        "primary-task",
        "task",
        "default",
    }
    if any(
        str(item).strip().casefold() in placeholder_tasks
        for item in contract.tasks
    ):
        violations.append(
            "TASK_SEMANTICS_MISSING: replace placeholder task names with an "
            "executable operation, prediction semantics, and authoritative "
            "target rule"
        )
    arm_specs = {
        "baseline": dict(contract.baseline),
        "treatment": dict(contract.treatment),
    }
    generic_arm_names = {
        "baseline",
        "treatment",
        "the primary implementation",
        "frozen replication and sensitivity conditions",
    }
    for arm_name, specification in arm_specs.items():
        descriptive_fields = {
            "behavior",
            "implementation_spec",
            "operation",
            "parameters",
            "preprocessing",
        }
        if (
            str(specification.get("name") or "").strip().casefold()
            in generic_arm_names
            and not descriptive_fields.intersection(specification)
        ):
            violations.append(
                f"{arm_name.upper()}_BEHAVIOR_MISSING: freeze the "
                f"{arm_name} computational behavior and its allowed "
                "difference from the other arm"
            )
    data = dict(contract.data_requirements or contract.data_boundary)
    unknown_data_fields = [
        name
        for name in (
            "denominator",
            "unit_of_analysis",
            "label_origin",
            "time_boundary",
        )
        if str(data.get(name) or "").strip().casefold()
        in {"", "unknown", "unverified"}
    ]
    if unknown_data_fields:
        violations.append(
            "DATA_BOUNDARY_INCOMPLETE: freeze "
            + ", ".join(unknown_data_fields)
        )
    metric = dict(contract.metrics[0]) if contract.metrics else {}
    if (
        str(metric.get("name") or "").strip().casefold()
        in {"latency", "runtime", "duration"}
        and not (
            metric.get("measurement_protocol")
            or contract.profile_parameters.get("measurement_protocol")
        )
    ):
        violations.append(
            "METRIC_PROTOCOL_MISSING: freeze the timing unit, measured "
            "boundary, warm-up, caching, clock, and repetition policy"
        )
    missing_data_policy = str(
        contract.statistical_rules.get("missing_data_policy") or ""
    ).strip()
    if (
        not missing_data_policy
        or "must be frozen" in missing_data_policy.casefold()
    ):
        violations.append(
            "MISSING_DATA_POLICY_UNRESOLVED: state the operative missing-data "
            "rule before Stage 3"
        )
    statistical_text = " ".join(
        str(contract.statistical_rules.get(name) or "")
        for name in ("analysis", "test", "method", "confidence_interval")
    ).casefold()
    if "bootstrap" in statistical_text and not all(
        contract.statistical_rules.get(name) is not None
        for name in ("bootstrap_resamples", "bootstrap_seed")
    ):
        violations.append(
            "BOOTSTRAP_PROTOCOL_INCOMPLETE: freeze the resample count, seed, "
            "resampling unit, implementation, and interval convention"
        )
    if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
        if not (
            contract.statistical_rules.get("effect_scale")
            and (
                metric.get("unit")
                or contract.statistical_rules.get("effect_unit")
            )
        ):
            violations.append(
                "EFFECT_THRESHOLD_SEMANTICS_MISSING: declare whether the "
                "threshold is absolute or relative and freeze its unit"
            )
    success_rule = str(
        contract.statistical_rules.get("success_threshold") or ""
    ).casefold()
    if (
        "secondary" in success_rule
        and not contract.statistical_rules.get("protected_secondary_outcomes")
    ):
        violations.append(
            "SECONDARY_OUTCOME_TOLERANCE_MISSING: freeze every protected "
            "secondary outcome and tolerance referenced by the success rule"
        )
    max_runs = (
        (contract.budget_security.get("budget") or {}).get("max_runs")
        if isinstance(contract.budget_security, dict)
        else None
    )
    planned_arm_runs = (
        len(contract.tasks)
        * len(contract.splits)
        * len(contract.seeds)
        * contract.replicates
        * 2
    )
    if (
        isinstance(max_runs, int)
        and not isinstance(max_runs, bool)
        and planned_arm_runs > max_runs
    ):
        violations.append(
            "RUN_BUDGET_CONFLICT: the frozen matrix requires "
            f"{planned_arm_runs} arm executions but max_runs is {max_runs}"
        )
    return violations


def _run_profile_v1_generation_worker(
    prompt: str,
    *,
    cwd: Path,
    timeout_seconds: float,
) -> Any:
    """Run Codex generation in a killable process boundary."""

    from .stage_three_generation import GeneratedProfileV1Package

    with tempfile.TemporaryDirectory(prefix="research-forge-stage3-") as raw:
        temporary_root = Path(raw)
        request_path = temporary_root / "request.json"
        response_path = temporary_root / "response.json"
        write_json_atomic(
            request_path,
            {
                "schema_version": 1,
                "prompt": prompt,
                "cwd": str(cwd),
            },
        )
        command = [
            sys.executable,
            "-m",
            "research_forge.stage3_profile_worker",
            "--request",
            str(request_path),
            "--response",
            str(response_path),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(Path(__file__).resolve().parent.parent),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                "Stage 3 profile worker exceeded its bounded runtime"
            ) from exc
        payload = read_json(response_path) if response_path.is_file() else {}
        if completed.returncode != 0:
            message = str(
                payload.get("error")
                or completed.stderr
                or "Stage 3 profile worker failed without an error record"
            )[:4_000]
            if payload.get("error_type") in {"ValueError", "ValidationError"}:
                raise ValueError(message)
            raise RuntimeError(message)
        return GeneratedProfileV1Package.model_validate(payload["package"])


async def _generate_profile_v1_package_bounded(
    prompt: str,
    *,
    cwd: Path,
    timeout_seconds: float,
) -> Any:
    return await asyncio.to_thread(
        _run_profile_v1_generation_worker,
        prompt,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )


def _record_build_milestone(
    repository: WorkflowRepository,
    study_id: str,
    *,
    group_id: str,
    step_type: str,
    executor_type: ExecutorType,
    result: dict[str, Any],
    depends_on_step_types: list[str] | None = None,
) -> None:
    """Persist a completed 3A/3B action as a first-class DAG step."""

    step = _start_build_step(
        repository,
        study_id,
        group_id=group_id,
        step_type=step_type,
        executor_type=executor_type,
        depends_on_step_types=depends_on_step_types,
    )
    if step is None:
        return
    artifact = repository.save_step_result(
        study_id, step.step_instance_id, result
    )
    repository.update_step(
        study_id,
        step.step_instance_id,
        ExecutionStatus.SUCCEEDED,
        output_artifact_ids=[artifact.artifact_id],
    )


def _start_build_step(
    repository: WorkflowRepository,
    study_id: str,
    *,
    group_id: str,
    step_type: str,
    executor_type: ExecutorType,
    depends_on_step_types: list[str] | None = None,
) -> Any | None:
    """Create and start one idempotent Stage 3 build StepInstance."""

    task_group = f"stage3-build:{group_id}"
    repository.save_step_definition(
        StepDefinition(
            step_type=step_type,
            phase=Phase.EXPERIMENT,
            executor_type=executor_type,
            expected_output="Stage 3 build milestone result",
            scientific_failure_enters_diagnosis=False,
        )
    )
    existing = [
        item
        for item in repository.list_steps(study_id)
        if item.task_group == task_group and item.step_type == step_type
    ]
    if existing:
        if existing[-1].status is ExecutionStatus.SUCCEEDED:
            return None
        current = existing[-1]
        if current.status not in {
            ExecutionStatus.RUNNING,
            ExecutionStatus.RETRYING,
        }:
            repository.update_step(
                study_id, current.step_instance_id, ExecutionStatus.QUEUED
            )
            repository.update_step(
                study_id, current.step_instance_id, ExecutionStatus.RUNNING
            )
        return repository.load_step(study_id, current.step_instance_id)
    dependency_types = set(depends_on_step_types or [])
    dependencies = [
        item.step_instance_id
        for item in repository.list_steps(study_id)
        if item.task_group == task_group
        and item.step_type in dependency_types
    ]
    if len(dependencies) != len(dependency_types):
        missing = sorted(
            dependency_types.difference(
                {
                    item.step_type
                    for item in repository.list_steps(study_id)
                    if item.task_group == task_group
                }
            )
        )
        raise ValueError(
            "Stage 3 build milestone dependencies are missing: "
            + ", ".join(missing)
        )
    step = repository.add_step(
        study_id,
        step_type,
        Phase.EXPERIMENT,
        executor_type,
        depends_on=dependencies,
        task_group=task_group,
        parameters={"stage3_build_group_id": group_id},
        expected_output="Stage 3 build milestone result",
    )
    repository.update_step(
        study_id, step.step_instance_id, ExecutionStatus.RUNNING
    )
    return repository.load_step(study_id, step.step_instance_id)


def _block_build_step(
    repository: WorkflowRepository,
    study_id: str,
    *,
    group_id: str,
    step_type: str,
    executor_type: ExecutorType,
    kind: str,
    reasons: list[str],
    depends_on_step_types: list[str] | None = None,
) -> None:
    """Persist a truthful build blocker before returning an admission error."""

    step = _start_build_step(
        repository,
        study_id,
        group_id=group_id,
        step_type=step_type,
        executor_type=executor_type,
        depends_on_step_types=depends_on_step_types,
    )
    if step is None:
        return
    repository.update_step(
        study_id,
        step.step_instance_id,
        ExecutionStatus.BLOCKED,
        blocker={
            "kind": kind,
            "reasons": list(dict.fromkeys(reasons)),
            "operational_state": "build_blocked",
            "scientific_verdict_changed": False,
        },
    )


def record_stage3_build_failure(
    repository: WorkflowRepository,
    study_id: str,
    step_type: str,
    error: Stage3BuildAdmissionError,
) -> None:
    """Persist API-facing build failures without changing a scientific verdict."""

    handoff_path = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "handoff.json"
    )
    group_id = (
        repository.load_stage3_handoff(study_id).handoff_id
        if handoff_path.is_file()
        else stable_id("stage3-build", study_id)
    )
    joined = " ".join(error.violations).casefold()
    kind = (
        "model_timeout"
        if "exceeded the bounded" in joined or "timeout" in joined
        else "contract_revision_required"
        if "generated package abstained because requirements are missing"
        in joined
        else "unsupported_profile"
        if "unsupported" in joined
        else "license_blocked"
        if "license_blocked" in joined or "license" in joined
        else "resource_blocked"
        if "resource_blocked" in joined or "resource" in joined
        else "build_blocked"
    )
    executor = {
        "resolve_external_resources": ExecutorType.RETRIEVAL_SERVICE,
        "resolve_or_build_assets": ExecutorType.CODEX,
        "run_engineering_smoke_tests": ExecutorType.SANDBOX_RUNNER,
        "verify_spec_conformance": ExecutorType.DETERMINISTIC_EVALUATOR,
        "formal_execution_admission": ExecutorType.DETERMINISTIC_EVALUATOR,
    }.get(step_type, ExecutorType.DETERMINISTIC_SERVICE)
    task_group = f"stage3-build:{group_id}"
    existing_attempts = [
        item
        for item in repository.list_steps(study_id)
        if item.task_group == task_group and item.step_type == step_type
    ]
    if (
        existing_attempts
        and existing_attempts[-1].status is ExecutionStatus.BLOCKED
        and existing_attempts[-1].attempt
        >= existing_attempts[-1].max_retries + 1
    ):
        return
    if any(
        item.task_group == task_group
        and item.step_type == step_type
        and item.status is ExecutionStatus.BLOCKED
        and (item.blocker or {}).get("reasons") == error.violations
        for item in repository.list_steps(study_id)
    ):
        return
    _block_build_step(
        repository,
        study_id,
        group_id=group_id,
        step_type=step_type,
        executor_type=executor,
        kind=kind,
        reasons=error.violations,
    )
    if kind == "contract_revision_required":
        from .contract_compiler import (
            blocking_issue_report,
            contract_amendment_proposal,
        )

        current = repository.latest_research_contract(study_id)
        if current is None:
            return
        issue_report = blocking_issue_report(
            current,
            error.violations,
            source_phase="experiment",
        )
        issue_path = (
            repository.root
            / "studies"
            / study_id
            / "stage3"
            / "diagnostics"
            / f"{issue_report.issue_report_id}.json"
        )
        write_json_atomic(issue_path, issue_report)
        repository.register_artifact(
            study_id,
            str(issue_path),
            sha256_file(issue_path),
            kind="stage3_blocking_issue_report",
            role=ArtifactRole.AUDIT,
        )
        proposal = contract_amendment_proposal(
            current,
            error.violations,
            source_phase="experiment",
        )
        proposal_path = (
            repository.root
            / "studies"
            / study_id
            / "stage3"
            / "diagnostics"
            / f"{proposal.proposal_id}.json"
        )
        write_json_atomic(proposal_path, proposal)
        repository.register_artifact(
            study_id,
            str(proposal_path),
            sha256_file(proposal_path),
            kind="contract_amendment_proposal",
            role=ArtifactRole.PROTOCOL,
        )
        latest = repository.latest_research_contract(study_id)
        assert latest is not None
        if latest.status is ArtifactStatus.FROZEN:
            repository.save_research_contract(
                latest.model_copy(
                    update={
                        "version": latest.version + 1,
                        "status": ArtifactStatus.DRAFT,
                        "protocol_status": ProtocolStatus.BLOCKED,
                        "predecessor_version": latest.version,
                        "compile_report_id": None,
                        "dry_run_report_id": None,
                        "unresolved_placeholders": list(error.violations),
                        "run_specification_ids": [],
                        "dataset_specification_id": None,
                        "algorithm_specification_ids": [],
                        "evaluation_specification_id": None,
                        "analysis_specification_id": None,
                        "executable_run_dag_id": None,
                        "field_diff": {
                            "stage3_blocking_issue_report_id": (
                                issue_report.issue_report_id
                            ),
                            "stage3_blocking_issues": list(error.violations),
                            "contract_amendment_proposal_id": (
                                proposal.proposal_id
                            ),
                            "contract_amendment_field_proposals": [
                                item.model_dump(mode="json")
                                for item in proposal.field_proposals
                            ],
                        },
                        "frozen_at": None,
                    }
                )
            )
        study = repository.load_study(study_id)
        repository.save_study(
            study.model_copy(
                update={
                    "phase": Phase.PROTOCOL,
                    "execution_status": ExecutionStatus.BLOCKED,
                }
            ),
            "stage3_contract_revision_returned_to_protocol",
        )


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _study_source_root(
    repository: WorkflowRepository, study_id: str
) -> Path:
    study = repository.load_study(study_id)
    project = repository.load_project(study.project_id)
    if project.source_root:
        root = Path(project.source_root).resolve()
        if root.is_dir():
            return root
    # A local project bundle is optional for Build-from-Blueprint.  The
    # per-Study directory is a bounded, auditable context for generated
    # material and never implies that a ready-made experiment exists.
    root = repository.root / "studies" / study_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def _approved_local_resource_manifest(
    repository: WorkflowRepository,
    study_id: str,
    contract: Any,
) -> list[dict[str, Any]]:
    """Resolve contract-approved local IDs to bounded, hash-verified paths."""

    selection_path = (
        repository.root
        / "studies"
        / study_id
        / "stage2"
        / "resource_selection.json"
    )
    if not selection_path.is_file():
        return []
    selected = read_json(selection_path)
    approved_ids = {
        str(item)
        for item in contract.runtime_binding.get(
            "approved_resource_ids", []
        )
        if str(item).strip()
    }
    root = _study_source_root(repository, study_id)
    manifest: list[dict[str, Any]] = []
    for item in selected.get("candidates", []):
        resource_id = str(item.get("resource_id") or "")
        if (
            resource_id not in approved_ids
            or item.get("source_kind") != "local_project"
        ):
            continue
        relative_text = str(item.get("canonical_identifier") or "").strip()
        if not relative_text or is_secret_path(relative_text):
            continue
        relative = Path(relative_text)
        source = (root / relative).resolve()
        if (
            source != root
            and root not in source.parents
        ) or not source.is_file():
            continue
        actual_hash = sha256_file(source)
        declared_hash = str(item.get("content_hash") or "").strip()
        if declared_hash and declared_hash != actual_hash:
            raise Stage3BuildAdmissionError(
                [
                    "LOCAL_RESOURCE_HASH_MISMATCH: "
                    f"{relative.as_posix()} changed after Stage 2 selection"
                ]
            )
        manifest.append(
            {
                "resource_id": resource_id,
                "need_type": str(item.get("need_type") or ""),
                "relative_path": relative.as_posix(),
                "sha256": actual_hash,
                "size_bytes": source.stat().st_size,
                "read_only": True,
            }
        )
    return sorted(
        manifest,
        key=lambda item: (
            item["need_type"],
            item["relative_path"],
            item["resource_id"],
        ),
    )


def resolve_profile_capability(
    repository: WorkflowRepository,
    study_id: str,
    blueprint: ExperimentBlueprint,
) -> tuple[ProfileCapabilityStatus, Stage3BuildMode]:
    """Resolve capability without letting an agent invent a new profile."""

    from .profiles.registry import (
        resolve_profile_capability as resolve_bundle_capability,
    )

    contract = repository.load_research_contract(
        study_id, blueprint.contract_version
    )
    if blueprint.profile is not contract.experiment_profile:
        return (
            ProfileCapabilityStatus.UNSUPPORTED,
            Stage3BuildMode.BUILD_FROM_BLUEPRINT,
        )
    root = _study_source_root(repository, study_id)
    manifest, _ = load_project_experiment_manifest(root)
    ready_made_compatible = manifest is not None
    if ready_made_compatible and contract.schema_version >= 2:
        # A manifest can be runnable while still violating the scientific
        # isolation required by the frozen contract. Treat that situation as
        # "build from blueprint" up front instead of admitting the package and
        # surprising the user with a Stage 3 dead end later.
        for binding in (contract.baseline, contract.treatment):
            spec = experiment_for_action(
                manifest,
                str(binding.get("action_id") or ""),
            )
            if (
                spec is None
                or spec.execution_backend
                != "isolated_candidate_evaluator"
                or not spec.evaluator_required_inputs
                or not spec.evaluator_code_paths
                or set(spec.required_inputs).intersection(
                    spec.evaluator_required_inputs
                )
            ):
                ready_made_compatible = False
                break
    capability = resolve_bundle_capability(
        blueprint.profile,
        runnable_assets_present=ready_made_compatible,
    )
    if capability is ProfileCapabilityStatus.SUPPORTED:
        return (
            capability,
            Stage3BuildMode.READY_MADE_EXPERIMENT,
        )
    return (
        capability,
        Stage3BuildMode.BUILD_FROM_BLUEPRINT,
    )


def stage3_build_admission(
    repository: WorkflowRepository,
    study_id: str,
    blueprint: ExperimentBlueprint,
    receipt: MVPFeasibilityReceipt,
) -> tuple[
    Stage3HandoffPackage,
    ScientificSpecificationSeal,
    ProfileCapabilityStatus,
]:
    """Admit a Stage 2 scientific specification without requiring built assets."""

    study = repository.load_study(study_id)
    violations: list[str] = []
    if study.active_scope_version is None:
        violations.append("a frozen Scope is required")
        scope = None
    else:
        scope = repository.load_scope_contract(
            study_id, study.active_scope_version
        )
        if scope.status is not ArtifactStatus.FROZEN:
            violations.append("the active Scope is not frozen")
    if study.active_contract_version is None:
        violations.append("a frozen Research Contract is required")
        contract = None
    else:
        contract = repository.load_research_contract(
            study_id, study.active_contract_version
        )
        if contract.status is not ArtifactStatus.FROZEN:
            violations.append("the active Research Contract is not frozen")
    if blueprint.study_id != study_id or receipt.study_id != study_id:
        violations.append("handoff objects belong to a different Study")
    if contract is not None and (
        blueprint.contract_version != contract.version
        or receipt.contract_version != contract.version
    ):
        violations.append(
            "Blueprint and MVP receipt must bind the active contract version"
        )
    if receipt.evidence_eligible or receipt.formal_run_eligible:
        violations.append("Stage 2 MVP must be non-evidentiary")
    if not (
        receipt.metric_computable
        and receipt.schema_feasible
        and receipt.resource_feasible
        and receipt.reproducible_seed_probe
    ):
        violations.append(
            "MVP must establish metric, schema, resource, and seed feasibility"
        )
    if not blueprint.resource_routes:
        violations.append("at least one resource route is required")
    if contract is not None and blueprint.profile is not contract.experiment_profile:
        violations.append("Blueprint profile does not match Research Contract")
    if contract is not None:
        try:
            contract_estimand = EstimandSpecification.model_validate(
                contract.estimand
            )
        except Exception:
            violations.append(
                "Research Contract lacks the complete frozen Estimand"
            )
        else:
            if contract_estimand != blueprint.estimand:
                violations.append(
                    "Experiment Blueprint Estimand differs from Research Contract"
                )
        if contract.data_requirements != blueprint.data_requirements:
            violations.append(
                "Experiment Blueprint data requirements differ from "
                "Research Contract"
            )
        if (
            list(
                contract.implementation_requirements.get(
                    "allowed_arm_delta", []
                )
            )
            != blueprint.allowed_arm_delta
        ):
            violations.append(
                "Experiment Blueprint arm delta differs from Research Contract"
            )
        if (
            contract.environment_requirements
            != blueprint.environment_requirements
        ):
            violations.append(
                "Experiment Blueprint environment requirements differ from "
                "Research Contract"
            )
        if not contract.resource_policy:
            violations.append(
                "Research Contract lacks a frozen resource policy"
            )
        if not contract.budget_security:
            violations.append(
                "Research Contract lacks frozen budget/security limits"
            )
    if violations:
        raise Stage3BuildAdmissionError(violations)
    assert scope is not None
    assert contract is not None

    capability, mode = resolve_profile_capability(
        repository, study_id, blueprint
    )
    if capability is ProfileCapabilityStatus.UNSUPPORTED:
        raise Stage3BuildAdmissionError(["blocked_unsupported_design"])

    blueprint = repository.save_experiment_blueprint(blueprint)
    receipt = repository.save_mvp_feasibility_receipt(receipt)
    scope_path = (
        repository.root
        / "studies"
        / study_id
        / "contracts"
        / f"scope-v{scope.version}.json"
    )
    contract_path = (
        repository.root
        / "studies"
        / study_id
        / "contracts"
        / f"research-v{contract.version}.json"
    )
    blueprint_path = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "specifications"
        / f"{blueprint.blueprint_id}.json"
    )
    rules_sha = _digest(
        {
            "statistical_rules": contract.statistical_rules,
            "hypothesis_rules": [
                item.decision_rule for item in contract.hypotheses
            ],
        }
    )
    seal = ScientificSpecificationSeal(
        seal_id=stable_id(
            "scientific-seal",
            study_id,
            scope.version,
            contract.version,
            sha256_file(blueprint_path),
            rules_sha,
        ),
        study_id=study_id,
        scope_version=scope.version,
        contract_version=contract.version,
        scope_sha256=sha256_file(scope_path),
        contract_sha256=sha256_file(contract_path),
        blueprint_sha256=sha256_file(blueprint_path),
        decision_rules_sha256=rules_sha,
    )
    seal = repository.save_scientific_specification_seal(seal)
    handoff_id = stable_id(
        "stage3-handoff",
        study_id,
        seal.seal_id,
        blueprint.blueprint_id,
        receipt.receipt_id,
    )
    current_handoff_path = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "handoff.json"
    )
    current_handoff = (
        repository.load_stage3_handoff(study_id)
        if current_handoff_path.is_file()
        else None
    )
    predecessor_handoff_id = (
        current_handoff.predecessor_handoff_id
        if current_handoff is not None
        and current_handoff.handoff_id == handoff_id
        else current_handoff.handoff_id
        if current_handoff is not None
        else None
    )
    handoff = Stage3HandoffPackage(
        handoff_id=handoff_id,
        study_id=study_id,
        scope_version=scope.version,
        contract_version=contract.version,
        profile=blueprint.profile,
        handoff_stage="build",
        build_mode=mode,
        scientific_specification_seal_id=seal.seal_id,
        experiment_blueprint_id=blueprint.blueprint_id,
        mvp_feasibility_receipt_id=receipt.receipt_id,
        predecessor_handoff_id=predecessor_handoff_id,
    )
    handoff = repository.save_stage3_handoff(handoff)
    group_id = handoff.handoff_id
    _record_build_milestone(
        repository,
        study_id,
        group_id=group_id,
        step_type="receive_stage2_handoff",
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        result={
            "handoff_id": handoff.handoff_id,
            "research_contract_version": contract.version,
            "experiment_blueprint_id": blueprint.blueprint_id,
            "mvp_feasibility_receipt_id": receipt.receipt_id,
        },
    )
    _record_build_milestone(
        repository,
        study_id,
        group_id=group_id,
        step_type="validate_scientific_handoff",
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        result={
            "scientific_specification_seal_id": seal.seal_id,
            "research_contract_immutable": True,
            "mvp_evidence_eligible": False,
        },
        depends_on_step_types=["receive_stage2_handoff"],
    )
    _record_build_milestone(
        repository,
        study_id,
        group_id=group_id,
        step_type="stage3_build_admission",
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        result={"admitted": True, "handoff_id": handoff.handoff_id},
        depends_on_step_types=["validate_scientific_handoff"],
    )
    _record_build_milestone(
        repository,
        study_id,
        group_id=group_id,
        step_type="resolve_experiment_profile",
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        result={
            "profile": handoff.profile.value,
            "capability_status": capability.value,
            "build_mode": mode.value,
        },
        depends_on_step_types=["stage3_build_admission"],
    )
    return handoff, seal, capability


def stage3_build_admission_from_stage2(
    repository: WorkflowRepository,
    study_id: str,
) -> tuple[
    Stage3HandoffPackage,
    ScientificSpecificationSeal,
    ProfileCapabilityStatus,
]:
    """Adapt the persisted Stage 2 scientific design into the typed handoff."""

    study = repository.load_study(study_id)
    if study.active_contract_version is None:
        raise Stage3BuildAdmissionError(
            ["Stage 2 has no active frozen Research Contract"]
        )
    contract = repository.load_research_contract(
        study_id, study.active_contract_version
    )
    contract_violations = stage3_contract_readiness_violations(contract)
    if contract_violations:
        raise Stage3BuildAdmissionError(contract_violations)
    stage2 = repository.root / "studies" / study_id / "stage2"
    protocol_paths = sorted(
        stage2.glob("protocol.draft*.json"),
        key=lambda item: item.stat().st_mtime_ns,
    )
    receipt_paths = sorted(
        stage2.glob("baseline_validation_report*.json"),
        key=lambda item: item.stat().st_mtime_ns,
    )
    if not protocol_paths or not receipt_paths:
        raise Stage3BuildAdmissionError(
            ["Stage 2 protocol or MVP feasibility report is missing"]
        )
    protocol = read_json(protocol_paths[-1])
    mvp = read_json(receipt_paths[-1])
    if not bool(mvp.get("feasibility_mvp_verified")):
        raise Stage3BuildAdmissionError(
            ["Stage 2 MVP did not establish build feasibility"]
        )
    metric = dict(protocol.get("primary_metric") or contract.metrics[0])
    metric_name = str(metric.get("name") or contract.metrics[0]["name"])
    routes = list(
        (protocol.get("resource_strategy") or {}).get(
            "resource_routes", []
        )
        or (protocol.get("resource_strategy") or {}).get(
            "stage3_resource_tasks", []
        )
        or contract.resource_policy.get("routes", [])
    )
    if not routes:
        routes = [
            {
                "strategy": (
                    "reuse"
                    if mvp.get("manifest_path")
                    else "implement"
                ),
                "source": mvp.get("implementation_source")
                or "stage2-approved-resource-route",
            }
        ]
    routes = [
        (
            dict(item)
            if isinstance(item, dict)
            else {"strategy": "implement", "source": str(item)}
        )
        for item in routes
    ]
    estimand_payload = dict(contract.estimand)
    estimand = EstimandSpecification(
        population=str(
            estimand_payload.get("population")
            or protocol.get("sample_population")
            or "frozen task and split population"
        ),
        experimental_unit=str(
            estimand_payload.get("experimental_unit")
            or protocol.get("experimental_unit")
            or "registered paired run unit"
        ),
        pairing_key=list(
            estimand_payload.get("pairing_key")
            or ["task", "split", "seed", "replicate"]
        ),
        outcome=str(estimand_payload.get("outcome") or metric_name),
        contrast=str(
            estimand_payload.get("contrast")
            or "treatment minus baseline"
        ),
        aggregation_hierarchy=list(
            estimand_payload.get("aggregation_hierarchy")
            or ["pair", "task", "study"]
        ),
        weighting_policy=str(
            estimand_payload.get("weighting_policy")
            or "equal weight per registered pair"
        ),
        variance_unit=str(
            estimand_payload.get("variance_unit")
            or protocol.get("experimental_unit")
            or "registered pair"
        ),
    )
    blueprint_payload = {
        "study_id": study_id,
        "contract_version": contract.version,
        "profile": contract.experiment_profile.value,
        "estimand": estimand.model_dump(mode="json"),
        "data_requirements": (
            contract.data_requirements or contract.data_boundary
        ),
        "baseline_requirements": (
            contract.implementation_requirements.get("baseline")
            or contract.baseline
        ),
        "treatment_requirements": (
            contract.implementation_requirements.get("treatment")
            or contract.treatment
        ),
        "allowed_arm_delta": (
            contract.implementation_requirements.get("allowed_arm_delta")
            or ["registered treatment intervention"]
        ),
        "environment_requirements": (
            contract.environment_requirements
            or contract.runtime_binding
        ),
        "resource_routes": routes,
    }
    blueprint = ExperimentBlueprint(
        blueprint_id=stable_id(
            "blueprint", study_id, contract.version, _digest(blueprint_payload)
        ),
        **blueprint_payload,
        preprocessing_requirements=list(
            protocol.get("data_preprocessing") or []
        )
        if isinstance(protocol.get("data_preprocessing"), list)
        else [str(protocol.get("data_preprocessing") or "frozen adapter")],
        runner_requirements={
            "tasks": contract.tasks,
            "splits": contract.splits,
            "seeds": contract.seeds,
            "replicates": contract.replicates,
            "arms": ["baseline", "treatment"],
        },
        raw_output_fields=list(
            contract.output_schema.get("raw_fields")
            or [
                "sample_id",
                "prediction",
                "target_reference",
                "abstention",
                "runtime",
                "resource_usage",
            ]
        ),
        smoke_test_requirements=[
            "program starts on smoke/dev partition",
            "both arms emit schema-valid output",
            "fixed seed probe is reproducible",
            "formal evaluation partition is not compared",
        ],
        completion_criteria=[
            "dataset, code, environment, evaluator, and manifest are frozen",
            "implementation conforms to the scientific specification",
        ],
    )
    receipt_payload = {
        "study_id": study_id,
        "contract_version": contract.version,
        "prototype_resource_ids": [
            str(item)
            for item in (
                mvp.get("required_input_bindings")
                or mvp.get("configuration", {}).get(
                    "selected_hdf5_paths", []
                )
            )
        ],
        "smoke_case_ids": [
            f"smoke-{index + 1}"
            for index in range(
                max(
                    1,
                    int(
                        (
                            mvp.get("actual_result")
                            if isinstance(mvp.get("actual_result"), dict)
                            else {}
                        ).get("smoke_case_count", 1)
                    ),
                )
            )
        ],
        "metric_computable": bool(
            mvp.get("frozen_denominator_computable")
            or mvp.get("output_schema_valid")
        ),
        "schema_feasible": bool(mvp.get("output_schema_valid")),
        "resource_feasible": bool(
            mvp.get("artifact_binding_valid")
            or mvp.get("feasibility_mvp_verified")
        ),
        "reproducible_seed_probe": bool(
            mvp.get("sample_integrity_valid")
        ),
        "temporary_implementation_notes": [
            str(mvp.get("implementation_source") or "Stage 2 prototype")
        ],
        "unresolved_assumptions": [
            str(item)
            for item in mvp.get("unresolved_integrity_errors", [])
        ],
    }
    receipt = MVPFeasibilityReceipt(
        receipt_id=stable_id(
            "mvp-receipt",
            study_id,
            contract.version,
            _digest(receipt_payload),
        ),
        **receipt_payload,
    )
    return stage3_build_admission(
        repository, study_id, blueprint, receipt
    )


def create_experiment_build_plan(
    repository: WorkflowRepository,
    study_id: str,
    handoff_id: str,
) -> ExperimentBuildPlan:
    """Create an immutable, auditable plan for the supported Profile v1."""

    handoff = repository.load_stage3_handoff(study_id)
    if handoff.handoff_id != handoff_id or handoff.handoff_stage != "build":
        raise Stage3BuildAdmissionError(
            ["Experiment Build Plan requires the active build handoff"]
        )
    assert handoff.experiment_blueprint_id
    assert handoff.scientific_specification_seal_id
    blueprint = repository.load_experiment_blueprint(
        study_id, handoff.experiment_blueprint_id
    )
    seal = repository.load_scientific_specification_seal(
        study_id, handoff.scientific_specification_seal_id
    )
    contract = repository.load_research_contract(
        study_id, handoff.contract_version
    )
    capability, mode = resolve_profile_capability(
        repository, study_id, blueprint
    )
    if capability is ProfileCapabilityStatus.UNSUPPORTED:
        raise Stage3BuildAdmissionError(["blocked_unsupported_design"])

    if mode is Stage3BuildMode.READY_MADE_EXPERIMENT:
        strategies = {
            "dataset": BuildAssetStrategy.REUSE,
            "dataset_adapter": BuildAssetStrategy.ADAPT,
            "baseline": BuildAssetStrategy.REUSE,
            "treatment": BuildAssetStrategy.REUSE,
            "evaluator": BuildAssetStrategy.ADAPT,
            "environment": BuildAssetStrategy.REUSE,
            "experiment_manifest": BuildAssetStrategy.REUSE,
        }
    else:
        strategies = {
            "dataset": BuildAssetStrategy.ADAPT,
            "dataset_adapter": BuildAssetStrategy.IMPLEMENT,
            "baseline": BuildAssetStrategy.IMPLEMENT,
            "treatment": BuildAssetStrategy.IMPLEMENT,
            "evaluator": BuildAssetStrategy.IMPLEMENT,
            "environment": BuildAssetStrategy.IMPLEMENT,
            "experiment_manifest": BuildAssetStrategy.IMPLEMENT,
        }
        for route in blueprint.resource_routes:
            raw_strategy = str(route.get("strategy", "")).casefold()
            try:
                route_strategy = BuildAssetStrategy(raw_strategy)
            except ValueError:
                continue
            asset_type = str(route.get("asset_type", "")).casefold()
            asset_role = str(route.get("asset_role", "")).casefold()
            if not asset_type and asset_role in _RETRIEVED_PARTITION_ROLES:
                asset_type = "dataset"
            if not asset_type:
                # Compatibility for the original Stage 2 route shape, where
                # the only routed resource was the dataset.
                asset_type = "dataset"
            if asset_type in strategies:
                strategies[asset_type] = route_strategy
    cost_class = {
        BuildAssetStrategy.REUSE: "low",
        BuildAssetStrategy.ADAPT: "medium",
        BuildAssetStrategy.RETRIEVE: "bounded_network",
        BuildAssetStrategy.GENERATE: "bounded_model_and_container",
        BuildAssetStrategy.IMPLEMENT: "high",
    }
    items = [
        ExperimentBuildItem(
            build_item_id=stable_id(
                "build-item", study_id, handoff_id, asset_type
            ),
            asset_type=asset_type,
            strategy=strategy,
            source={
                "blueprint_id": blueprint.blueprint_id,
                "resource_routes": blueprint.resource_routes,
            },
            license=next(
                (
                    str(route["license"])
                    for route in blueprint.resource_routes
                    if route.get("license")
                    and (
                        str(route.get("asset_type", "")).casefold()
                        == asset_type
                        or asset_type == "dataset"
                        and str(route.get("asset_role", "")).casefold()
                        in _RETRIEVED_PARTITION_ROLES
                    )
                ),
                None,
            ),
            acceptance_checks=[
                "artifact hash recorded",
                "conforms to frozen scientific specification",
                "source, transformation, and license provenance recorded",
                (
                    "isolated smoke required"
                    if strategy
                    in {
                        BuildAssetStrategy.RETRIEVE,
                        BuildAssetStrategy.GENERATE,
                        BuildAssetStrategy.IMPLEMENT,
                    }
                    and asset_type
                    in {"baseline", "treatment", "evaluator"}
                    else "bounded deterministic validation required"
                ),
            ],
            estimated_cost={
                "class": cost_class[strategy],
                "project_budget_cap": contract.budget_security,
            },
            risks=(
                [
                    "isolated execution required before use",
                    "source lineage and license must remain frozen",
                ]
                if strategy
                in {
                    BuildAssetStrategy.RETRIEVE,
                    BuildAssetStrategy.GENERATE,
                    BuildAssetStrategy.IMPLEMENT,
                }
                and asset_type
                in {"baseline", "treatment", "evaluator"}
                else (
                    ["external availability and license may block construction"]
                    if strategy is BuildAssetStrategy.RETRIEVE
                    else []
                )
            ),
        )
        for asset_type, strategy in strategies.items()
    ]
    plan = ExperimentBuildPlan(
        build_plan_id=stable_id(
            "build-plan",
            study_id,
            handoff_id,
            *[
                f"{item.asset_type}:{item.strategy.value}"
                for item in items
            ],
        ),
        study_id=study_id,
        handoff_id=handoff_id,
        contract_version=handoff.contract_version,
        profile=handoff.profile,
        capability_status=capability,
        build_mode=mode,
        items=items,
        research_contract_sha256=seal.contract_sha256,
    )
    plan = repository.save_experiment_build_plan(plan)
    _record_build_milestone(
        repository,
        study_id,
        group_id=handoff.handoff_id,
        step_type="create_experiment_build_plan",
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        result=plan.model_dump(mode="json"),
        depends_on_step_types=["resolve_experiment_profile"],
    )
    _record_build_milestone(
        repository,
        study_id,
        group_id=handoff.handoff_id,
        step_type="approve_experiment_build_plan",
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        result={
            "approved": True,
            "approval_authority": "profile_registry_and_frozen_contract",
            "build_plan_id": plan.build_plan_id,
        },
        depends_on_step_types=["create_experiment_build_plan"],
    )
    return plan


_RETRIEVED_PARTITION_ROLES = {
    "formal_candidate",
    "formal_target",
    "smoke_candidate",
    "smoke_target",
}
_RETRIEVED_CODE_ROLES = {
    "dataset_adapter": "dataset_adapter_source",
    "baseline": "baseline_source",
    "treatment": "treatment_source",
    "evaluator": "evaluator_source",
}


def resolve_stage3_resource_routes(
    repository: WorkflowRepository,
    study_id: str,
    build_plan_id: str,
) -> dict[str, Any]:
    """Bind exact, contract-approved retrieval snapshots to a Build Plan.

    Experimentation never performs an open-ended search.  A ``retrieve``
    route must name resources already approved by the frozen Research
    Contract and represented by a frozen Protocol ResourceSet.  The gateway
    then creates new experimentation bindings without mutating the earlier
    bindings.
    """

    from .retrieval.domain.models import (
        ContractRef,
        ResourceType,
        ResourceSetStatus,
        RetrievalBudget,
        RetrievalPhase,
    )
    from .retrieval.interfaces.service import RetrievalGateway

    plan = repository.load_experiment_build_plan(study_id, build_plan_id)
    retrieve_items = [
        item
        for item in plan.items
        if item.strategy is BuildAssetStrategy.RETRIEVE
    ]
    if not retrieve_items:
        return {
            "status": "not_applicable",
            "build_plan_id": build_plan_id,
            "resource_bindings": [],
        }
    handoff = repository.load_stage3_handoff(study_id)
    if handoff.handoff_id != plan.handoff_id:
        raise Stage3BuildAdmissionError(["Build Plan handoff is not active"])
    step = _start_build_step(
        repository,
        study_id,
        group_id=plan.handoff_id,
        step_type="resolve_external_resources",
        executor_type=ExecutorType.RETRIEVAL_SERVICE,
        depends_on_step_types=["approve_experiment_build_plan"],
    )
    if step is None:
        path = (
            repository.root
            / "studies"
            / study_id
            / "stage3"
            / "resource_resolutions"
            / f"{build_plan_id}.json"
        )
        if not path.is_file():
            raise Stage3BuildAdmissionError(
                ["resource resolution milestone exists without its manifest"]
            )
        return read_json(path)

    contract = repository.load_research_contract(
        study_id, plan.contract_version
    )
    approved_ids = {
        str(item)
        for item in contract.runtime_binding.get(
            "approved_resource_ids", []
        )
        if str(item).strip()
    }
    route_records: list[dict[str, Any]] = []
    for item in retrieve_items:
        for route in item.source.get("resource_routes", []):
            if str(route.get("strategy", "")).casefold() != "retrieve":
                continue
            resource_ids = [
                *(
                    [str(route["resource_id"])]
                    if route.get("resource_id")
                    else []
                ),
                *[str(value) for value in route.get("resource_ids", [])],
            ]
            binding_ids = [
                *(
                    [str(route["binding_id"])]
                    if route.get("binding_id")
                    else []
                ),
                *[str(value) for value in route.get("binding_ids", [])],
            ]
            route_records.append(
                {
                    "asset_role": str(route.get("asset_role", "")).casefold(),
                    "resource_ids": list(dict.fromkeys(resource_ids)),
                    "binding_ids": list(dict.fromkeys(binding_ids)),
                    "declared_license": route.get("license"),
                    "source_format": route.get("source_format"),
                    "acquire_online": bool(route.get("acquire_online", False)),
                    "max_download_bytes": int(
                        route.get("max_download_bytes") or 0
                    ),
                }
            )

    violations: list[str] = []
    if not approved_ids:
        violations.append(
            "RESOURCE_BLOCKED: frozen Research Contract does not authorize "
            "approved_resource_ids"
        )
    expected_roles: set[str] = set()
    for item in retrieve_items:
        if item.asset_type == "dataset":
            expected_roles.update(_RETRIEVED_PARTITION_ROLES)
        elif item.asset_type in _RETRIEVED_CODE_ROLES:
            expected_roles.add(_RETRIEVED_CODE_ROLES[item.asset_type])
        else:
            expected_roles.add(f"{item.asset_type}_resource")
    role_records = {
        item["asset_role"]: item
        for item in route_records
        if item["asset_role"]
    }
    missing_roles = sorted(
        expected_roles.difference(role_records)
    )
    if missing_roles:
        violations.append(
            "RESOURCE_BLOCKED: retrieve strategies require exact frozen "
            "resource roles: "
            + ", ".join(missing_roles)
        )

    gateway = RetrievalGateway(str(repository.root))
    frozen_protocol_binding_ids = {
        binding_id
        for resource_set in gateway.repository.list_resource_sets(study_id)
        if resource_set.phase is RetrievalPhase.PROTOCOL
        and resource_set.status is ResourceSetStatus.FROZEN
        for binding_id in resource_set.binding_ids
    }
    protocol_bindings = {
        item.binding_id: item
        for item in gateway.repository.list_bindings(study_id)
        if item.phase is RetrievalPhase.PROTOCOL
        and item.binding_id in frozen_protocol_binding_ids
    }
    contract_ref = ContractRef(
        contract_type="research",
        contract_id=f"{study_id}:research-v{contract.version}",
        version=contract.version,
    )
    resolved: list[dict[str, Any]] = []
    for role in sorted(expected_roles):
        route = role_records.get(role)
        if route is None:
            continue
        candidates = [
            protocol_bindings[binding_id]
            for binding_id in route["binding_ids"]
            if binding_id in protocol_bindings
        ]
        candidates.extend(
            binding
            for binding in protocol_bindings.values()
            if binding.resource_id in route["resource_ids"]
            and binding not in candidates
        )
        if len(candidates) != 1:
            violations.append(
                f"RESOURCE_BLOCKED: {role} must resolve to exactly one "
                "frozen Protocol binding"
            )
            continue
        source_binding = candidates[0]
        if source_binding.resource_id not in approved_ids:
            violations.append(
                f"RESOURCE_BLOCKED: {source_binding.resource_id} is not "
                "authorized by the frozen Research Contract"
            )
            continue
        resource = gateway.repository.load_resource(
            source_binding.resource_id
        )
        expected_resource_types = (
            {ResourceType.DATASET}
            if role in _RETRIEVED_PARTITION_ROLES
            else {
                ResourceType.CODE_REPOSITORY,
                ResourceType.CODE_RELEASE,
            }
            if role in set(_RETRIEVED_CODE_ROLES.values())
            else {resource.resource_type}
        )
        if resource.resource_type not in expected_resource_types:
            violations.append(
                f"RESOURCE_BLOCKED: {role} has incompatible resource type "
                f"{resource.resource_type.value}"
            )
            continue
        allowed_content_levels = (
            {"dataset_file"}
            if role in _RETRIEVED_PARTITION_ROLES
            else {"source_archive"}
            if role in set(_RETRIEVED_CODE_ROLES.values())
            else {
                "documentation",
                "official_documentation",
                "repository_metadata",
            }
        )
        snapshots = [
            item
            for item in gateway.repository.list_snapshots(
                source_binding.resource_id
            )
            if item.snapshot_id == source_binding.snapshot_id
            and item.content_level in allowed_content_levels
        ]
        if not snapshots and route.get("acquire_online"):
            max_download_bytes = int(
                route.get("max_download_bytes")
                or contract.budget_security.get("max_download_bytes")
                or 0
            )
            try:
                request = gateway.plan(
                    project_id=repository.load_study(study_id).project_id,
                    study_id=study_id,
                    phase=RetrievalPhase.EXPERIMENTATION,
                    step_instance_id=step.step_instance_id,
                    purpose=(
                        "fetch_pinned_code_revision"
                        if role in set(_RETRIEVED_CODE_ROLES.values())
                        else "fetch_approved_dataset"
                    ),
                    queries=[resource.canonical_identifier],
                    providers=[
                        provider
                        for provider in resource.providers
                        if provider in {"github", "huggingface"}
                    ],
                    resource_types=[resource.resource_type],
                    usage_role="approved_experiment_resource",
                    budget=RetrievalBudget(
                        max_queries=1,
                        max_results=1,
                        max_download_bytes=max_download_bytes,
                    ),
                    idempotency_key=stable_id(
                        "stage3-acquisition",
                        study_id,
                        build_plan_id,
                        role,
                        resource.resource_id,
                        resource.commit or "",
                    ),
                    contract_refs=[contract_ref],
                    research_need=(
                        f"Acquire exact approved {role} revision"
                    ),
                    freshness="live",
                    require_search_execution=False,
                )
                acquisition = (
                    gateway.acquire_approved_experiment_resource(
                        request_id=request.request_id,
                        resource_id=resource.resource_id,
                    )
                )
                snapshots = [acquisition.snapshot]
            except Exception as exc:
                violations.append(
                    f"RESOURCE_BLOCKED: online acquisition failed for {role}: "
                    f"{type(exc).__name__}: {str(exc)[:180]}"
                )
                continue
        if len(snapshots) != 1:
            violations.append(
                f"RESOURCE_BLOCKED: frozen snapshot is unavailable for {role}"
            )
            continue
        snapshot = snapshots[0]
        if snapshot.content_level not in allowed_content_levels:
            violations.append(
                f"RESOURCE_BLOCKED: {role} snapshot content level "
                f"{snapshot.content_level} is not executable-source eligible"
            )
            continue
        license_name = str(
            route.get("declared_license")
            or resource.license
            or snapshot.license_status
            or ""
        ).strip()
        if license_name.casefold() in {
            "",
            "unknown",
            "restricted",
            "license_restricted",
        }:
            violations.append(
                f"LICENSE_BLOCKED: {role} has no usable recorded license"
            )
            continue
        try:
            source_artifact = gateway.repository.load_artifact(
                snapshot.normalized_content_artifact_id
            )
        except FileNotFoundError:
            violations.append(
                f"RESOURCE_BLOCKED: normalized retrieval artifact is missing "
                f"for {role}"
            )
            continue
        source_path = Path(source_artifact.path)
        if (
            not source_path.is_file()
            or sha256_file(source_path) != snapshot.content_hash
            or source_artifact.content_hash != snapshot.content_hash
        ):
            violations.append(
                f"RESOURCE_BLOCKED: retrieved snapshot hash failed for {role}"
            )
            continue
        existing = next(
            (
                item
                for item in gateway.repository.list_bindings(study_id)
                if item.phase is RetrievalPhase.EXPERIMENTATION
                and item.step_instance_id == step.step_instance_id
                and item.resource_id == source_binding.resource_id
                and item.snapshot_id == snapshot.snapshot_id
                and item.target_field == role
            ),
            None,
        )
        purpose = (
            "fetch_approved_dataset"
            if role in _RETRIEVED_PARTITION_ROLES
            else "fetch_pinned_code_revision"
            if role in set(_RETRIEVED_CODE_ROLES.values())
            else "fetch_official_documentation"
        )
        promoted = existing or gateway.promote_binding(
            source_binding.binding_id,
            target_phase=RetrievalPhase.EXPERIMENTATION,
            step_instance_id=step.step_instance_id,
            purpose=purpose,
            usage_role="approved_experiment_resource",
            target_type="experiment_build_plan",
            target_id=build_plan_id,
            target_field=role,
            contract_refs=[contract_ref],
            snapshot_id=snapshot.snapshot_id,
        )
        resolved.append(
            {
                "asset_role": role,
                "resource_id": resource.resource_id,
                "source_binding_id": source_binding.binding_id,
                "experimentation_binding_id": promoted.binding_id,
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_sha256": snapshot.content_hash,
                "retrieval_artifact_id": source_artifact.artifact_id,
                "source_path": str(source_path),
                "license": license_name,
                "resource_type": resource.resource_type.value,
                "source_format": route.get("source_format"),
            }
        )

    if violations:
        _block_build_step(
            repository,
            study_id,
            group_id=plan.handoff_id,
            step_type="resolve_external_resources",
            executor_type=ExecutorType.RETRIEVAL_SERVICE,
            kind=(
                "license_blocked"
                if any(item.startswith("LICENSE_BLOCKED") for item in violations)
                else "resource_blocked"
            ),
            reasons=violations,
            depends_on_step_types=["approve_experiment_build_plan"],
        )
        raise Stage3BuildAdmissionError(violations)

    manifest = {
        "schema_version": 1,
        "status": "resolved",
        "study_id": study_id,
        "build_plan_id": build_plan_id,
        "contract_version": contract.version,
        "retrieval_phase": RetrievalPhase.EXPERIMENTATION.value,
        "open_ended_search_performed": False,
        "contract_ref": contract_ref.model_dump(mode="json"),
        "resource_bindings": resolved,
    }
    path = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "resource_resolutions"
        / f"{build_plan_id}.json"
    )
    if path.is_file() and read_json(path) != manifest:
        raise Stage3BuildAdmissionError(
            ["resource resolution manifest is immutable"]
        )
    write_json_atomic(path, manifest)
    artifact = repository.register_artifact(
        study_id,
        str(path),
        sha256_file(path),
        kind="stage3_resource_resolution",
        role=ArtifactRole.PROTOCOL,
    )
    result = {
        **manifest,
        "manifest_artifact_id": artifact.artifact_id,
    }
    _record_build_milestone(
        repository,
        study_id,
        group_id=plan.handoff_id,
        step_type="resolve_external_resources",
        executor_type=ExecutorType.RETRIEVAL_SERVICE,
        result=result,
        depends_on_step_types=["approve_experiment_build_plan"],
    )
    return result


def _resolved_partition_overrides(
    repository: WorkflowRepository,
    study_id: str,
    plan: ExperimentBuildPlan,
) -> dict[str, dict[str, Any]]:
    """Load verified retrieved partitions without exposing them to Codex."""

    if not any(
        item.strategy is BuildAssetStrategy.RETRIEVE
        and item.asset_type == "dataset"
        for item in plan.items
    ):
        return {}
    path = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "resource_resolutions"
        / f"{plan.build_plan_id}.json"
    )
    if not path.is_file():
        raise Stage3BuildAdmissionError(
            [
                "RESOURCE_BLOCKED: retrieve assets must be resolved through "
                "Forge Retrieval Gateway before materialization"
            ]
        )
    manifest = read_json(path)
    records = {
        str(item["asset_role"]): item
        for item in manifest.get("resource_bindings", [])
    }
    if set(records) != _RETRIEVED_PARTITION_ROLES:
        raise Stage3BuildAdmissionError(
            ["RESOURCE_BLOCKED: retrieved partition manifest is incomplete"]
        )
    for record in records.values():
        source = Path(str(record["source_path"]))
        if (
            not source.is_file()
            or sha256_file(source) != record["snapshot_sha256"]
        ):
            raise Stage3BuildAdmissionError(
                ["RESOURCE_BLOCKED: retrieved partition changed after binding"]
            )
    return records


def _prepare_retrieved_code_inputs(
    repository: WorkflowRepository,
    study_id: str,
    plan: ExperimentBuildPlan,
    resolution: dict[str, Any],
) -> tuple[Path | None, dict[str, Any]]:
    """Safely stage pinned third-party source for read-only Codex adaptation."""

    import stat
    import tarfile
    import zipfile

    records = [
        item
        for item in resolution.get("resource_bindings", [])
        if item.get("asset_role") in set(_RETRIEVED_CODE_ROLES.values())
    ]
    if not records:
        return None, {"status": "not_applicable", "sources": []}
    root = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "builder_inputs"
        / plan.build_plan_id
    )
    manifest_path = root / "builder_input_manifest.json"
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        for item in manifest.get("files", []):
            path = root / str(item["path"])
            if not path.is_file() or sha256_file(path) != item["sha256"]:
                raise Stage3BuildAdmissionError(
                    ["RESOURCE_BLOCKED: staged third-party source changed"]
                )
        return root, manifest
    root.mkdir(parents=True, exist_ok=False)
    max_files = 1_000
    max_total_bytes = 50 * 1024 * 1024
    file_records: list[dict[str, Any]] = []
    total_bytes = 0

    def safe_relative(name: str) -> Path:
        normalized = name.replace("\\", "/")
        relative = Path(normalized)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
        ):
            raise Stage3BuildAdmissionError(
                ["RESOURCE_BLOCKED: unsafe path in retrieved source archive"]
            )
        lowered = {part.casefold() for part in relative.parts}
        if (
            ".env" in lowered
            or ".ssh" in lowered
            or any("credential" in part for part in lowered)
            or any("private_key" in part for part in lowered)
            or relative.name.casefold()
            in {"id_rsa", "id_ed25519", "secrets.json"}
        ):
            raise Stage3BuildAdmissionError(
                ["RESOURCE_BLOCKED: retrieved source archive contains secrets"]
            )
        return relative

    def write_member(role: str, relative: Path, content: bytes) -> None:
        nonlocal total_bytes
        total_bytes += len(content)
        if len(file_records) >= max_files or total_bytes > max_total_bytes:
            raise Stage3BuildAdmissionError(
                ["RESOURCE_BLOCKED: retrieved source archive exceeds limits"]
            )
        destination = root / role / relative
        destination.resolve().relative_to(root.resolve())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        file_records.append(
            {
                "path": destination.relative_to(root).as_posix(),
                "sha256": sha256_file(destination),
                "byte_size": len(content),
                "asset_role": role,
            }
        )

    for record in records:
        source = Path(str(record["source_path"]))
        if not source.is_file() or sha256_file(source) != record["snapshot_sha256"]:
            raise Stage3BuildAdmissionError(
                ["RESOURCE_BLOCKED: retrieved code snapshot changed"]
            )
        role = str(record["asset_role"])
        source_format = str(record.get("source_format") or "").casefold()
        suffixes = "".join(source.suffixes).casefold()
        if source_format == "zip" or suffixes.endswith(".zip"):
            with zipfile.ZipFile(source) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise Stage3BuildAdmissionError(
                            ["RESOURCE_BLOCKED: source archive contains a symlink"]
                        )
                    write_member(
                        role,
                        safe_relative(info.filename),
                        archive.read(info),
                    )
        elif source_format in {"tar", "tar.gz", "tgz"} or any(
            suffixes.endswith(value)
            for value in (".tar", ".tar.gz", ".tgz")
        ):
            with tarfile.open(source, mode="r:*") as archive:
                for member in archive.getmembers():
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise Stage3BuildAdmissionError(
                            [
                                "RESOURCE_BLOCKED: source archive contains "
                                "a link or special file"
                            ]
                        )
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise Stage3BuildAdmissionError(
                            ["RESOURCE_BLOCKED: source archive member is unreadable"]
                        )
                    write_member(
                        role,
                        safe_relative(member.name),
                        extracted.read(),
                    )
        elif source_format in {"python", "py", "text"}:
            write_member(role, Path("source.py"), source.read_bytes())
        else:
            raise Stage3BuildAdmissionError(
                [
                    f"RESOURCE_BLOCKED: retrieved code role {role} requires "
                    "a declared zip, tar, tar.gz, tgz, or python source_format"
                ]
            )
    manifest = {
        "schema_version": 1,
        "study_id": study_id,
        "build_plan_id": plan.build_plan_id,
        "purpose": "read_only_third_party_source_for_bounded_adaptation",
        "raw_source_executed": False,
        "network_access_authorized": False,
        "sources": [
            {
                key: item[key]
                for key in (
                    "asset_role",
                    "resource_id",
                    "experimentation_binding_id",
                    "snapshot_id",
                    "snapshot_sha256",
                    "license",
                    "source_format",
                )
            }
            for item in records
        ],
        "files": file_records,
    }
    write_json_atomic(manifest_path, manifest)
    repository.register_artifact(
        study_id,
        str(manifest_path),
        sha256_file(manifest_path),
        kind="stage3_builder_input_manifest",
        role=ArtifactRole.FEASIBILITY,
    )
    return root, manifest


def materialize_generated_profile_v1_package(
    repository: WorkflowRepository,
    study_id: str,
    build_plan_id: str,
    generated: Any,
) -> dict[str, Any]:
    """Materialize structured Codex output without granting model write access."""

    from .stage_three_generation import GeneratedProfileV1Package
    from .stage_three_profiles import profile_definition

    plan = repository.load_experiment_build_plan(study_id, build_plan_id)
    if plan.build_mode is not Stage3BuildMode.BUILD_FROM_BLUEPRINT:
        raise Stage3BuildAdmissionError(
            ["generated materialization requires a blueprint Build Plan"]
        )
    generated_payload = (
        generated.model_dump(mode="json")
        if hasattr(generated, "model_dump")
        else dict(generated)
    )
    retrieved = _resolved_partition_overrides(
        repository, study_id, plan
    )
    retrieved_by_path: dict[str, dict[str, Any]] = {}
    if retrieved:
        partition_fields = {
            "formal_candidate": "formal_data_path",
            "formal_target": "formal_target_path",
            "smoke_candidate": "smoke_data_path",
            "smoke_target": "smoke_target_path",
        }
        files = [dict(item) for item in generated_payload.get("files", [])]
        by_path = {str(item.get("path")): item for item in files}
        for role, field in partition_fields.items():
            destination = str(generated_payload.get(field, ""))
            if destination not in by_path:
                raise Stage3BuildAdmissionError(
                    [
                        f"generated package does not declare the frozen "
                        f"retrieved {role} path"
                    ]
                )
            record = retrieved[role]
            source = Path(str(record["source_path"]))
            try:
                content = source.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise Stage3BuildAdmissionError(
                    [
                        f"RESOURCE_BLOCKED: retrieved {role} is not a UTF-8 "
                        "Profile v1 tabular partition"
                    ]
                ) from exc
            by_path[destination]["content"] = content
            by_path[destination]["source_basis"] = [
                f"retrieval_binding:{record['experimentation_binding_id']}",
                f"snapshot:{record['snapshot_id']}",
                f"sha256:{record['snapshot_sha256']}",
                f"license:{record['license']}",
            ]
            retrieved_by_path[destination] = record
        generated_payload["files"] = files
    package = GeneratedProfileV1Package.model_validate(generated_payload)
    resolution_path = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "resource_resolutions"
        / f"{plan.build_plan_id}.json"
    )
    resolved_code_records = (
        [
            item
            for item in read_json(resolution_path).get(
                "resource_bindings", []
            )
            if item.get("asset_role")
            in set(_RETRIEVED_CODE_ROLES.values())
        ]
        if resolution_path.is_file()
        else []
    )
    output_role_for_source = {
        source_role: output_role
        for output_role, source_role in _RETRIEVED_CODE_ROLES.items()
    }
    for record in resolved_code_records:
        output_role = output_role_for_source[str(record["asset_role"])]
        outputs = [item for item in package.files if item.role == output_role]
        required_basis = {
            f"retrieval_binding:{record['experimentation_binding_id']}",
            f"snapshot:{record['snapshot_id']}",
        }
        if not outputs or not any(
            required_basis.issubset(set(item.source_basis))
            for item in outputs
        ):
            raise Stage3BuildAdmissionError(
                [
                    f"generated {output_role} does not preserve the frozen "
                    "third-party source binding and snapshot lineage"
                ]
            )
    definition = profile_definition(plan.profile)
    if package.builder_plugin not in definition.builder_plugins:
        raise Stage3BuildAdmissionError(
            ["generated package uses an unregistered builder plugin"]
        )
    if package.unresolved_requirements:
        raise Stage3BuildAdmissionError(
            [
                "generated package abstained because requirements are missing: "
                + "; ".join(package.unresolved_requirements)
            ]
        )
    handoff = repository.load_stage3_handoff(study_id)
    assert handoff.scientific_specification_seal_id
    assert handoff.experiment_blueprint_id
    blueprint = repository.load_experiment_blueprint(
        study_id, handoff.experiment_blueprint_id
    )
    if package.declared_allowed_arm_delta != blueprint.allowed_arm_delta:
        raise Stage3BuildAdmissionError(
            [
                "generated package arm delta does not exactly match the "
                "frozen Experiment Blueprint"
            ]
        )
    seal = repository.load_scientific_specification_seal(
        study_id, handoff.scientific_specification_seal_id
    )
    contract_path = (
        repository.root
        / "studies"
        / study_id
        / "contracts"
        / f"research-v{plan.contract_version}.json"
    )
    if sha256_file(contract_path) != plan.research_contract_sha256:
        raise Stage3BuildAdmissionError(
            ["builder cannot continue after Research Contract mutation"]
        )
    if seal.contract_sha256 != plan.research_contract_sha256:
        raise Stage3BuildAdmissionError(
            ["Build Plan does not bind the active scientific seal"]
        )
    contract = repository.load_research_contract(
        study_id, plan.contract_version
    )
    root = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "materialized"
        / build_plan_id
    )
    file_records: list[dict[str, Any]] = []
    artifact_ids: list[str] = []
    for item in package.files:
        path = root / Path(item.path)
        path.resolve().relative_to(root.resolve())
        if path.is_file() and path.read_text(encoding="utf-8") != item.content:
            raise Stage3BuildAdmissionError(
                [f"generated file is immutable once materialized: {item.path}"]
            )
        write_text_atomic(path, item.content)
        artifact = repository.register_artifact(
            study_id,
            str(path),
            sha256_file(path),
            kind=f"generated_{item.role}",
            role=(
                ArtifactRole.FEASIBILITY
                if item.role in {"smoke_dataset", "test"}
                else ArtifactRole.OTHER
            ),
        )
        artifact_ids.append(artifact.artifact_id)
        upstream_code = next(
            (
                record
                for record in resolved_code_records
                if output_role_for_source[str(record["asset_role"])]
                == item.role
            ),
            None,
        )
        file_records.append(
            {
                "path": item.path,
                "role": item.role,
                "source_basis": item.source_basis,
                "source_kind": (
                    "frozen_retrieval_snapshot"
                    if item.path in retrieved_by_path
                    else "codex_generated"
                ),
                "generated": item.path not in retrieved_by_path,
                "resource_binding_id": (
                    retrieved_by_path[item.path][
                        "experimentation_binding_id"
                    ]
                    if item.path in retrieved_by_path
                    else None
                ),
                "snapshot_id": (
                    retrieved_by_path[item.path]["snapshot_id"]
                    if item.path in retrieved_by_path
                    else None
                ),
                "license": (
                    retrieved_by_path[item.path]["license"]
                    if item.path in retrieved_by_path
                    else upstream_code["license"]
                    if upstream_code is not None
                    else None
                ),
                "upstream_retrieval_binding_id": (
                    upstream_code["experimentation_binding_id"]
                    if upstream_code is not None
                    else None
                ),
                "upstream_snapshot_id": (
                    upstream_code["snapshot_id"]
                    if upstream_code is not None
                    else None
                ),
                "sha256": artifact.sha256,
                "artifact_id": artifact.artifact_id,
            }
        )
    from .stage_three_evaluator import compile_evaluator_source

    evaluator_source, golden_vectors = compile_evaluator_source(contract)
    evaluator_path = root / "platform_evaluator.py"
    golden_path = root / "evaluator_golden_vectors.json"
    write_text_atomic(evaluator_path, evaluator_source)
    write_json_atomic(golden_path, golden_vectors)
    for path, kind in (
        (evaluator_path, "platform_evaluator"),
        (golden_path, "evaluator_golden_vectors"),
    ):
        evaluator_artifact = repository.register_artifact(
            study_id,
            str(path),
            sha256_file(path),
            kind=kind,
            role=ArtifactRole.FEASIBILITY,
        )
        artifact_ids.append(evaluator_artifact.artifact_id)
        file_records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "role": kind,
                "source_basis": [
                    "platform evaluator plugin",
                    "frozen metric and output schema",
                ],
                "sha256": evaluator_artifact.sha256,
                "artifact_id": evaluator_artifact.artifact_id,
            }
        )
    manifest_path = root / "generation_manifest.json"
    prompt_path = (
        Path(__file__).resolve().parent
        / "prompts"
        / "stage3_profile_v1_builder.md"
    )
    manifest = {
        "schema_version": 1,
        "study_id": study_id,
        "build_plan_id": build_plan_id,
        "builder_plugin": package.builder_plugin,
        "trust_level": ExecutionTrustLevel.AI_GENERATED_CODE.value,
        "isolated_execution_required": True,
        "research_contract_sha256": plan.research_contract_sha256,
        "scientific_specification_seal_id": seal.seal_id,
        "build_item_provenance": [
            {
                **item.model_dump(mode="json"),
                "source_version": item.source.get("version"),
                "declared_license": item.license,
                "transformation_steps": [
                    "Codex returned a schema-validated source proposal",
                    "the deterministic materializer validated paths and "
                    "candidate/target separation",
                    "each output was content-addressed before smoke testing",
                ],
            }
            for item in plan.items
        ],
        "generation_tool": {
            "name": "Codex structured Stage 3 Profile v1 builder",
            "builder_plugin": package.builder_plugin,
            "prompt_sha256": sha256_file(prompt_path),
            "output_schema": "GeneratedProfileV1Package-v1",
        },
        "files": file_records,
        "formal_data_path": package.formal_data_path,
        "formal_target_path": package.formal_target_path,
        "smoke_data_path": package.smoke_data_path,
        "smoke_target_path": package.smoke_target_path,
        "commands": {
            "baseline": package.baseline_command,
            "treatment": package.treatment_command,
            "generated_evaluator_proposal": package.evaluator_command,
            "formal_evaluator": [
                "{python}",
                "platform_evaluator.py",
                "--input",
                "{prediction_file}",
                "--targets",
                "{target_file}",
                "--output",
                "{metrics_file}",
            ],
        },
        "generated_evaluator_has_formal_authority": False,
        "raw_prediction_path": package.raw_prediction_path,
        "metric_output_path": package.metric_output_path,
        "expected_raw_fields": package.expected_raw_fields,
        "expected_metric_fields": package.expected_metric_fields,
        "conformance_claims": package.conformance_claims,
        "declared_allowed_arm_delta": package.declared_allowed_arm_delta,
        "arm_command_structure_equal": True,
        "code_diff": {
            "basis": "new generated package",
            "file_hashes": {
                item["path"]: item["sha256"] for item in file_records
            },
        },
        "acceptance_status": "awaiting_isolated_smoke",
        "model_had_write_access": False,
        "retrieved_partitions_injected_after_model_generation": bool(
            retrieved_by_path
        ),
        "retrieval_binding_ids": sorted(
            {
                item["experimentation_binding_id"]
                for item in [
                    *retrieved_by_path.values(),
                    *resolved_code_records,
                ]
            }
        ),
        "retrieved_code_adapted_without_raw_execution": bool(
            resolved_code_records
        ),
        "formal_execution_performed": False,
        "evidence_eligible": False,
    }
    write_json_atomic(manifest_path, manifest)
    artifact = repository.register_artifact(
        study_id,
        str(manifest_path),
        sha256_file(manifest_path),
        kind="generated_execution_source_manifest",
        role=ArtifactRole.FEASIBILITY,
    )
    result = {
        **manifest,
        "materialization_root": str(root),
        "generation_manifest_artifact_id": artifact.artifact_id,
        "generated_artifact_ids": artifact_ids,
    }
    _record_build_milestone(
        repository,
        study_id,
        group_id=plan.handoff_id,
        step_type="resolve_or_build_assets",
        executor_type=ExecutorType.CODEX,
        result={
            "build_plan_id": build_plan_id,
            "materialization_root": str(root),
            "generation_manifest_artifact_id": artifact.artifact_id,
            "generated_artifact_ids": artifact_ids,
            "model_had_write_access": False,
        },
        depends_on_step_types=["approve_experiment_build_plan"],
    )
    return result


async def generate_and_materialize_profile_v1(
    repository: WorkflowRepository,
    study_id: str,
    build_plan_id: str,
    *,
    package_generator: Callable[..., Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Ask Codex for structured source, then materialize it deterministically."""

    plan = repository.load_experiment_build_plan(study_id, build_plan_id)
    build_step = next(
        (
            item
            for item in reversed(repository.list_steps(study_id))
            if item.step_type == "resolve_or_build_assets"
            and item.task_group == f"stage3-build:{plan.handoff_id}"
        ),
        None,
    )
    if (
        build_step is not None
        and build_step.status is ExecutionStatus.BLOCKED
        and (build_step.blocker or {}).get("kind")
        == "contract_revision_required"
    ):
        raise Stage3BuildAdmissionError(
            list((build_step.blocker or {}).get("reasons", []))
            or ["CONTRACT_REVISION_REQUIRED"]
        )
    if (
        build_step is not None
        and build_step.status is ExecutionStatus.BLOCKED
        and build_step.attempt >= build_step.max_retries + 1
    ):
        raise Stage3BuildAdmissionError(
            [
                "RETRY_LIMIT_EXHAUSTED: create a diagnosed contract or "
                "implementation successor before another build attempt"
            ]
        )
    handoff = repository.load_stage3_handoff(study_id)
    if plan.handoff_id != handoff.handoff_id:
        raise Stage3BuildAdmissionError(["Build Plan handoff is not active"])
    assert handoff.experiment_blueprint_id
    blueprint = repository.load_experiment_blueprint(
        study_id, handoff.experiment_blueprint_id
    )
    receipt = repository.load_mvp_feasibility_receipt(
        study_id, str(handoff.mvp_feasibility_receipt_id)
    )
    contract = repository.load_research_contract(
        study_id, plan.contract_version
    )
    approved_local_resources = _approved_local_resource_manifest(
        repository,
        study_id,
        contract,
    )
    resource_resolution = resolve_stage3_resource_routes(
        repository, study_id, build_plan_id
    )
    builder_input_root, builder_input_manifest = (
        _prepare_retrieved_code_inputs(
            repository, study_id, plan, resource_resolution
        )
    )
    safe_resource_resolution = {
        key: value
        for key, value in resource_resolution.items()
        if key not in {"source_path"}
    }
    if resource_resolution.get("resource_bindings"):
        safe_resource_resolution["resource_bindings"] = [
            {
                key: value
                for key, value in item.items()
                if key != "source_path"
            }
            for item in resource_resolution["resource_bindings"]
        ]
    prompt = json.dumps(
        {
            "authority": {
                "research_contract_is_immutable": True,
                "may_change_scientific_fields": False,
                "formal_results_available": False,
            },
            "research_contract": contract.model_dump(mode="json"),
            "experiment_blueprint": blueprint.model_dump(mode="json"),
            "mvp_feasibility_receipt": receipt.model_dump(mode="json"),
            "experiment_build_plan": plan.model_dump(mode="json"),
            "resolved_experiment_resources": safe_resource_resolution,
            "approved_local_resources": approved_local_resources,
            "approved_local_resource_policy": {
                "paths_are_relative_to_builder_cwd": True,
                "read_only": True,
                "hashes_must_match_before_use": True,
                "unlisted_local_files_are_not_authorized_as_formal_inputs": True,
            },
            "retrieved_partition_policy": {
                "contents_visible_to_builder": False,
                "deterministic_injection_after_generation": bool(
                    resource_resolution.get("resource_bindings")
                ),
                "builder_must_not_infer_or_tune_formal_targets": True,
            },
            "retrieved_code_inputs": builder_input_manifest,
            "retrieved_code_policy": {
                "inputs_are_read_only": True,
                "raw_source_must_not_be_executed": True,
                "derived_code_requires_container_isolation": True,
                "source_basis_must_name_binding_and_snapshot": True,
            },
            "structured_output_requirements": {
                "all_declared_paths_must_exist_in_files": True,
                "jsonl_partitions_must_be_non_empty": True,
                "jsonl_format": "exactly one valid JSON object per non-empty line",
                "candidate_row_minimum_fields": [
                    "sample_id",
                    "features",
                    "target_reference",
                ],
                "target_row_minimum_fields": [
                    "target_reference",
                    "target",
                ],
                "target_reference_join_must_be_complete": True,
                "formal_and_smoke_partitions_must_be_distinct": True,
                "do_not_return_placeholders_or_empty_content": True,
                "commands_must_start_with": "{python}",
                "candidate_command_placeholders": [
                    "{data_file}",
                    "{prediction_file}",
                ],
            },
        },
        ensure_ascii=False,
        # This payload is machine-to-model context. Compact encoding removes
        # repeated whitespace from large contracts and build plans without
        # changing any scientific field or authority boundary.
        separators=(",", ":"),
    )
    _start_build_step(
        repository,
        study_id,
        group_id=plan.handoff_id,
        step_type="resolve_or_build_assets",
        executor_type=ExecutorType.CODEX,
        depends_on_step_types=["approve_experiment_build_plan"],
    )
    builder_cwd = (
        builder_input_root
        if builder_input_root is not None
        else _study_source_root(repository, study_id)
    )
    generated = None
    validation_error: ValueError | None = None
    for schema_attempt in range(1, 3):
        repair_context = (
            ""
            if validation_error is None
            else (
                "\n\nThe previous structured proposal was rejected by the "
                "deterministic package validator. Return a complete corrected "
                "object, not a patch. Every formal/smoke candidate and target "
                "path must exactly match one declared files[].path. Every "
                "JSONL partition must contain at least one valid JSON object "
                "per line; candidate rows must include sample_id, features, "
                "and target_reference; target rows must include matching "
                "target_reference and target values. Empty content, comments, "
                "and placeholder prose are forbidden. Every Python command "
                "must begin with the literal {python} placeholder, and arm "
                "commands must use {data_file} and {prediction_file}. Validator "
                "message:\n"
                + str(validation_error)[:2_000]
            )
        )
        try:
            if package_generator is not None:
                generated = await asyncio.wait_for(
                    package_generator(
                        prompt + repair_context,
                        cwd=builder_cwd,
                    ),
                    timeout=_PROFILE_V1_GENERATION_TIMEOUT_SECONDS,
                )
            else:
                generated = await _generate_profile_v1_package_bounded(
                    prompt + repair_context,
                    cwd=builder_cwd,
                    timeout_seconds=_PROFILE_V1_GENERATION_TIMEOUT_SECONDS,
                )
            break
        except TimeoutError as exc:
            raise Stage3BuildAdmissionError(
                [
                    "Codex experiment-asset generation exceeded the bounded "
                    f"{_PROFILE_V1_GENERATION_TIMEOUT_SECONDS // 60}-minute "
                    "attempt limit. The completed Stage 2 contract and build "
                    "plan were preserved; retry only this build step."
                ]
            ) from exc
        except ValueError as exc:
            validation_error = exc
            if schema_attempt == 2:
                raise Stage3BuildAdmissionError(
                    [
                        "generated package failed deterministic schema "
                        "validation after one bounded repair attempt: "
                        + str(exc)[:2_000]
                    ]
                ) from exc
        except Exception as exc:
            raise Stage3BuildAdmissionError(
                [
                    "experiment-asset builder failed before deterministic "
                    "materialization. The frozen contract and build plan were "
                    "preserved; retry only this build step. "
                    + f"({type(exc).__name__}: {str(exc)[:1_000]})"
                ]
            ) from exc
    assert generated is not None
    return materialize_generated_profile_v1_package(
        repository, study_id, build_plan_id, generated
    )


def smoke_generated_profile_v1_package(
    repository: WorkflowRepository,
    study_id: str,
    build_plan_id: str,
) -> dict[str, Any]:
    """Smoke generated arms without mounting or comparing formal data."""

    from .container_execution import (
        ContainerExecutionPolicy,
        run_isolated_command,
    )

    root = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "materialized"
        / build_plan_id
    )
    manifest_path = root / "generation_manifest.json"
    if not manifest_path.is_file():
        raise Stage3BuildAdmissionError(
            ["generated source must be materialized before smoke testing"]
        )
    manifest = read_json(manifest_path)
    candidate_input = root / "_smoke_candidate_input"
    evaluator_input = root / "_smoke_evaluator_input"
    smoke_output = root / "_smoke_output"
    if (
        candidate_input.exists()
        or evaluator_input.exists()
        or smoke_output.exists()
    ):
        raise Stage3BuildAdmissionError(
            ["smoke workspace is immutable; use a new Build Plan to rerun"]
        )
    candidate_input.mkdir(parents=True)
    evaluator_input.mkdir(parents=True)
    for item in manifest["files"]:
        if item["role"] in {
            "dataset",
            "target_dataset",
            "smoke_target_dataset",
            "evaluator",
            "platform_evaluator",
            "evaluator_golden_vectors",
        }:
            continue
        source = root / item["path"]
        destination = candidate_input / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for relative in ("platform_evaluator.py", "evaluator_golden_vectors.json"):
        source = root / relative
        destination = evaluator_input / relative
        shutil.copy2(source, destination)
    forbidden_candidate_paths = (
        manifest["formal_data_path"],
        manifest["formal_target_path"],
        manifest["smoke_target_path"],
    )
    if any(
        (candidate_input / relative).exists()
        for relative in forbidden_candidate_paths
    ):
        raise Stage3BuildAdmissionError(
            ["target or formal evaluation data leaked into candidate smoke"]
        )
    if not (candidate_input / manifest["smoke_data_path"]).is_file():
        raise Stage3BuildAdmissionError(["smoke dataset is unavailable"])
    if any(
        (evaluator_input / relative).exists()
        for relative in (
            manifest["formal_data_path"],
            manifest["formal_target_path"],
            manifest["smoke_data_path"],
            manifest["smoke_target_path"],
        )
    ):
        raise Stage3BuildAdmissionError(
            ["experiment partitions leaked into evaluator golden-vector smoke"]
        )

    attestations: dict[str, Any] = {}
    required = {"sample_id", "prediction", "target_reference"}
    for arm in ("baseline", "treatment"):
        arm_output = smoke_output / arm
        command = [
            str(token)
            .replace("{python}", "python")
            .replace(
                "{data_file}",
                f"/workspace/input/{manifest['smoke_data_path']}",
            )
            .replace(
                "{prediction_file}",
                "/workspace/output/predictions.jsonl",
            )
            for token in manifest["commands"][arm]
        ]
        result = run_isolated_command(
            command,
            input_dir=candidate_input,
            output_dir=arm_output,
            policy=ContainerExecutionPolicy(),
        )
        if result.exit_code != 0:
            raise Stage3BuildAdmissionError(
                [f"{arm} smoke container failed: {result.stderr[-1000:]}"]
            )
        prediction_path = arm_output / "predictions.jsonl"
        if not prediction_path.is_file():
            raise Stage3BuildAdmissionError(
                [f"{arm} did not emit sample-level predictions"]
            )
        with prediction_path.open("r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        if not rows or any(not required.issubset(row) for row in rows):
            raise Stage3BuildAdmissionError(
                [f"{arm} emitted invalid sample-level prediction rows"]
            )
        attestations[arm] = result.isolation_attestation

    golden_vectors = json.loads(
        (evaluator_input / "evaluator_golden_vectors.json").read_text(
            encoding="utf-8"
        )
    )
    golden = golden_vectors[0]
    write_text_atomic(
        evaluator_input / "golden_predictions.jsonl",
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in golden["rows"]
        ),
    )
    write_text_atomic(
        evaluator_input / "golden_targets.jsonl",
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in golden["targets"]
        ),
    )
    evaluator_output = smoke_output / "evaluator"
    evaluator_result = run_isolated_command(
        [
            "python",
            "platform_evaluator.py",
            "--input",
            "/workspace/input/golden_predictions.jsonl",
            "--targets",
            "/workspace/input/golden_targets.jsonl",
            "--output",
            "/workspace/output/metrics.json",
        ],
        input_dir=evaluator_input,
        output_dir=evaluator_output,
        policy=ContainerExecutionPolicy(),
    )
    if evaluator_result.exit_code != 0:
        raise Stage3BuildAdmissionError(
            ["platform evaluator golden-vector container failed"]
        )
    if read_json(evaluator_output / "metrics.json") != golden["expected"]:
        raise Stage3BuildAdmissionError(
            ["platform evaluator failed its frozen golden vector"]
        )
    receipt = {
        "schema_version": 1,
        "study_id": study_id,
        "build_plan_id": build_plan_id,
        "generation_manifest_sha256": sha256_file(manifest_path),
        "purpose": "engineering_smoke_only",
        "evidence_eligible": False,
        "formal_run_eligible": False,
        "formal_data_mounted": False,
        "formal_targets_mounted": False,
        "candidate_saw_any_targets": False,
        "arm_performance_compared": False,
        "arms_schema_valid": True,
        "evaluator_golden_vector_passed": True,
        "acceptance_checks": {
            "candidate_schema_valid": True,
            "candidate_targets_not_mounted": True,
            "formal_partition_not_mounted": True,
            "platform_evaluator_golden_vector_passed": True,
            "container_isolation_attested": True,
        },
        "isolation_attestations": {
            **attestations,
            "evaluator": evaluator_result.isolation_attestation,
        },
    }
    receipt_path = root / "generated_smoke_receipt.json"
    write_json_atomic(receipt_path, receipt)
    artifact = repository.register_artifact(
        study_id,
        str(receipt_path),
        sha256_file(receipt_path),
        kind="generated_smoke_receipt",
        role=ArtifactRole.FEASIBILITY,
    )
    result = {**receipt, "artifact_id": artifact.artifact_id}
    plan = repository.load_experiment_build_plan(study_id, build_plan_id)
    _record_build_milestone(
        repository,
        study_id,
        group_id=plan.handoff_id,
        step_type="run_engineering_smoke_tests",
        executor_type=ExecutorType.SANDBOX_RUNNER,
        result=result,
        depends_on_step_types=["resolve_or_build_assets"],
    )
    return result


def freeze_ready_made_execution_package(
    repository: WorkflowRepository,
    study_id: str,
    build_plan_id: str,
    *,
    trust_level: ExecutionTrustLevel = (
        ExecutionTrustLevel.TRUSTED_LOCAL_PROJECT
    ),
) -> ExecutionPackageSeal:
    """Validate, smoke-run, and freeze a ready-made Profile v1 package."""

    from .container_execution import (
        ContainerExecutionPolicy,
        run_isolated_command,
    )

    if trust_level is ExecutionTrustLevel.UNKNOWN_BINARY:
        raise Stage3BuildAdmissionError(
            ["unknown binary assets are blocked by default"]
        )
    if trust_level in {
        ExecutionTrustLevel.THIRD_PARTY_CODE,
        ExecutionTrustLevel.AI_GENERATED_CODE,
    }:
        raise Stage3BuildAdmissionError(
            [
                "isolated container execution is required before third-party "
                "or AI-generated code can be smoke-tested or frozen"
            ]
        )
    plan = repository.load_experiment_build_plan(study_id, build_plan_id)
    if plan.build_mode is not Stage3BuildMode.READY_MADE_EXPERIMENT:
        raise Stage3BuildAdmissionError(
            ["ready-made freezer cannot materialize a blueprint-only plan"]
        )
    handoff = repository.load_stage3_handoff(study_id)
    if handoff.handoff_id != plan.handoff_id:
        raise Stage3BuildAdmissionError(["Build Plan handoff is not active"])
    assert handoff.scientific_specification_seal_id
    scientific_seal = repository.load_scientific_specification_seal(
        study_id, handoff.scientific_specification_seal_id
    )
    contract = repository.load_research_contract(
        study_id, handoff.contract_version
    )
    root = _study_source_root(repository, study_id)
    manifest, manifest_path = load_project_experiment_manifest(root)
    if manifest is None or manifest_path is None:
        raise Stage3BuildAdmissionError(
            ["ready-made experiment manifest is unavailable"]
        )
    if sha256_file(
        repository.root
        / "studies"
        / study_id
        / "contracts"
        / f"research-v{contract.version}.json"
    ) != scientific_seal.contract_sha256:
        raise Stage3BuildAdmissionError(
            ["Research Contract changed after scientific freeze"]
        )

    arm_specs: dict[str, tuple[str, Any]] = {}
    for arm_id, binding in (
        ("baseline", contract.baseline),
        ("treatment", contract.treatment),
    ):
        action_id = str(binding.get("action_id") or "")
        spec = experiment_for_action(manifest, action_id)
        if spec is None:
            raise Stage3BuildAdmissionError(
                [f"{arm_id} action is not declared in Experiment Manifest"]
            )
        arm_specs[arm_id] = (action_id, spec)
    if contract.schema_version >= 2:
        violations: list[str] = []
        for arm_id, (_, spec) in arm_specs.items():
            if spec.execution_backend != "isolated_candidate_evaluator":
                violations.append(
                    f"{arm_id} must use isolated_candidate_evaluator"
                )
            if not spec.evaluator_required_inputs:
                violations.append(
                    f"{arm_id} must declare frozen evaluator targets"
                )
            if not spec.evaluator_code_paths:
                violations.append(
                    f"{arm_id} must declare an independent evaluator"
                )
            if set(spec.required_inputs).intersection(
                spec.evaluator_required_inputs
            ):
                violations.append(
                    f"{arm_id} candidate inputs overlap evaluator targets"
                )
        if violations:
            raise Stage3BuildAdmissionError(
                [
                    "ready-made Profile v1 packages created under Research "
                    "Contract schema v2 require sample-level independent "
                    "evaluation; add the candidate/evaluator adapter: "
                    + "; ".join(violations)
                ]
            )

    package_root = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "execution_packages"
        / build_plan_id
    )
    smoke_root = package_root / "smoke"
    smoke_receipt_path = package_root / "smoke_test_receipt.json"
    if smoke_root.exists() and not smoke_receipt_path.is_file():
        attempt = 1
        while (
            package_root / f"smoke_failed_attempt_{attempt}"
        ).exists():
            attempt += 1
        smoke_root.replace(
            package_root / f"smoke_failed_attempt_{attempt}"
        )
    smoke_reports: dict[str, Any] = {}
    smoke_image_ids: set[str] = set()
    smoke_images: set[str] = set()
    for arm_id, (action_id, spec) in arm_specs.items():
        if not spec.smoke_command or not spec.smoke_required_inputs:
            raise Stage3BuildAdmissionError(
                [
                    f"{arm_id} must declare an isolated smoke_command and "
                    "smoke_required_inputs before execution-package freeze"
                ]
            )
        if set(spec.required_inputs).intersection(
            spec.smoke_required_inputs
        ):
            raise Stage3BuildAdmissionError(
                [
                    f"{arm_id} smoke inputs overlap formal evaluation inputs"
                ]
            )
        smoke_input = smoke_root / arm_id / "input"
        smoke_output = smoke_root / arm_id / "output"
        if smoke_input.exists() or smoke_output.exists():
            raise Stage3BuildAdmissionError(
                [
                    f"{arm_id} smoke workspace already exists; "
                    "create a new Build Plan instead of mutating it"
                ]
            )
        smoke_input.mkdir(parents=True)
        for relative in spec.smoke_required_inputs:
            source = (root / relative).resolve()
            source.relative_to(root.resolve())
            if not source.is_file():
                raise Stage3BuildAdmissionError(
                    [f"{arm_id} smoke input is unavailable: {relative}"]
                )
            destination = (smoke_input / relative).resolve()
            destination.relative_to(smoke_input.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        replacements = {
            "python": "python",
            "source_root": "/workspace/input",
            "evidence_dir": "/workspace/output",
            "action_id": action_id,
            "plan_id": f"{build_plan_id}:smoke",
            "task_id": "__smoke__",
            "split_id": "smoke_dev",
            "arm_id": arm_id,
            "seed": "0",
            "replicate": "1",
            "run_cell_id": f"smoke-{arm_id}",
            "data_file": "/workspace/input/smoke-data",
            "target_file": "/workspace/input/smoke-targets",
            "prediction_file": "/workspace/output/predictions.jsonl",
            "metrics_file": "/workspace/output/metrics.json",
        }
        rendered_command: list[str] = []
        for token in spec.smoke_command:
            value = str(token)
            for placeholder, replacement in replacements.items():
                value = value.replace(
                    "{" + placeholder + "}", replacement
                )
            if "{" in value or "}" in value:
                raise Stage3BuildAdmissionError(
                    [
                        f"{arm_id} smoke command has an unknown "
                        f"placeholder: {value}"
                    ]
                )
            rendered_command.append(value)
        assert spec.container_image
        isolated = run_isolated_command(
            rendered_command,
            input_dir=smoke_input,
            output_dir=smoke_output,
            policy=ContainerExecutionPolicy(
                image=spec.container_image,
                timeout_seconds=spec.timeout_seconds,
            ),
        )
        if isolated.exit_code != 0:
            raise Stage3BuildAdmissionError(
                [
                    f"{arm_id} isolated smoke failed: "
                    + isolated.stderr[-1_000:]
                ]
            )
        verified_artifacts: list[dict[str, Any]] = []
        for artifact_spec in spec.artifacts:
            artifact_path = (
                smoke_output / artifact_spec.path
            ).resolve()
            artifact_path.relative_to(smoke_output.resolve())
            if not artifact_path.is_file():
                raise Stage3BuildAdmissionError(
                    [
                        f"{arm_id} isolated smoke did not emit "
                        f"{artifact_spec.path}"
                    ]
                )
            if artifact_path.stat().st_size < artifact_spec.min_bytes:
                raise Stage3BuildAdmissionError(
                    [
                        f"{arm_id} isolated smoke artifact is smaller "
                        f"than {artifact_spec.min_bytes} bytes"
                    ]
                )
            if artifact_spec.format == "json":
                payload = read_json(artifact_path)
                missing = [
                    key
                    for key in artifact_spec.required_keys
                    if key not in payload
                ]
                if missing:
                    raise Stage3BuildAdmissionError(
                        [
                            f"{arm_id} isolated smoke JSON is missing: "
                            + ", ".join(missing)
                        ]
                    )
            verified_artifacts.append(
                {
                    "path": str(artifact_path),
                    "format": artifact_spec.format,
                    "sha256": sha256_file(artifact_path),
                }
            )
        image_id = str(
            isolated.isolation_attestation.get("image_id") or ""
        )
        image = str(
            isolated.isolation_attestation.get("image")
            or spec.container_image
        )
        if not image_id:
            raise Stage3BuildAdmissionError(
                [f"{arm_id} smoke lacks a container image digest"]
            )
        smoke_image_ids.add(image_id)
        smoke_images.add(image)
        smoke_reports[arm_id] = {
            "status": "completed",
            "input_binding_valid": True,
            "artifact_formats": [
                item["format"] for item in verified_artifacts
            ],
            "artifact_hashes": [
                item["sha256"] for item in verified_artifacts
            ],
            "isolation_attestation": isolated.isolation_attestation,
        }
        for item in verified_artifacts:
            repository.register_artifact(
                study_id,
                item["path"],
                item["sha256"],
                kind="stage3_smoke_output",
                role=ArtifactRole.FEASIBILITY,
            )
    if len(smoke_image_ids) != 1 or len(smoke_images) != 1:
        raise Stage3BuildAdmissionError(
            [
                "all ready-made smoke containers must bind one "
                "shared image and digest"
            ]
        )
    container_image_id = next(iter(smoke_image_ids))
    container_image = next(iter(smoke_images))
    smoke_path = smoke_receipt_path
    write_json_atomic(
        smoke_path,
        {
            "schema_version": 1,
            "study_id": study_id,
            "build_plan_id": build_plan_id,
            "purpose": "engineering_smoke_only",
            "evidence_eligible": False,
            "formal_run_eligible": False,
            "formal_partition_compared": False,
            "arm_reports": smoke_reports,
        },
    )
    smoke_artifact = repository.register_artifact(
        study_id,
        str(smoke_path),
        sha256_file(smoke_path),
        kind="stage3_smoke_receipt",
        role=ArtifactRole.FEASIBILITY,
    )

    required_inputs = sorted(
        {
            relative
            for _, spec in arm_specs.values()
            for relative in spec.required_inputs
        }
    )
    data_rows = []
    for relative in required_inputs:
        path = (root / relative).resolve()
        if not path.is_file():
            raise Stage3BuildAdmissionError(
                [
                    "formal input must be a content-addressed regular file: "
                    + relative
                ]
            )
        data_rows.append({"path": relative, "sha256": sha256_file(path)})
    lock_payloads = {
        "protocol.lock.json": {
            "contract_version": contract.version,
            "contract_sha256": scientific_seal.contract_sha256,
            "scientific_specification_seal_id": scientific_seal.seal_id,
        },
        "data_manifest.lock.json": {
            "inputs": data_rows,
            "conforms_to": contract.data_requirements
            or contract.data_boundary,
        },
        "code_manifest.lock.json": {
            "experiment_manifest_sha256": sha256_file(manifest_path),
            "arm_commands": {
                arm: spec.command for arm, (_, spec) in arm_specs.items()
            },
            "conforms_to": contract.implementation_requirements,
        },
        "environment.lock.json": {
            "container_image": container_image,
            "container_image_id": container_image_id,
            "trust_level": trust_level.value,
            "network_default": "disabled",
            "read_only_inputs": True,
            "separate_outputs": True,
            "conforms_to": contract.environment_requirements
            or contract.runtime_binding,
        },
        "decision_rules.lock.json": {
            "statistical_rules": contract.statistical_rules,
            "hypothesis_rules": [
                item.decision_rule for item in contract.hypotheses
            ],
            "source_sha256": scientific_seal.decision_rules_sha256,
        },
        "model_selection.lock.json": (
            contract.model_selection_plan.model_dump(mode="json")
        ),
        "arm_fairness.lock.json": (
            contract.arm_fairness_contract.model_dump(mode="json")
        ),
        "attempt_selection.lock.json": (
            contract.attempt_selection_policy.model_dump(mode="json")
        ),
        "evaluator.lock.json": {
            "output_schema": contract.output_schema,
            "metric": contract.metrics[0],
            "evaluation_mode": (
                "sample_level_independent_platform_evaluation"
                if contract.schema_version >= 2
                else "declared_summary_with_deterministic_schema_validation"
            ),
            "independent_from_arms": contract.schema_version >= 2,
            "limitation": (
                "schema-v1 compatibility package; it cannot be promoted as "
                "independently recomputed confirmation"
                if contract.schema_version < 2
                else None
            ),
            "frozen_before_formal_results": True,
        },
    }
    lock_artifacts: dict[str, str] = {}
    lock_hashes: dict[str, str] = {}
    for name, payload in lock_payloads.items():
        path = package_root / name
        write_json_atomic(
            path,
            {
                "schema_version": 1,
                "study_id": study_id,
                "build_plan_id": build_plan_id,
                "immutable": True,
                **payload,
            },
        )
        artifact = repository.register_artifact(
            study_id,
            str(path),
            sha256_file(path),
            kind=name.removesuffix(".json").replace(".", "_"),
            role=ArtifactRole.PROTOCOL,
        )
        lock_artifacts[name] = artifact.artifact_id
        lock_hashes[name] = artifact.sha256
    manifest_artifact = repository.register_artifact(
        study_id,
        str(manifest_path),
        sha256_file(manifest_path),
        kind="experiment_manifest",
        role=ArtifactRole.PROTOCOL,
    )
    lock_artifacts["research-forge.experiments.json"] = (
        manifest_artifact.artifact_id
    )
    lock_hashes["research-forge.experiments.json"] = manifest_artifact.sha256
    execution_lock_path = package_root / "execution-package.lock.json"
    write_json_atomic(
        execution_lock_path,
        {
            "schema_version": 1,
            "study_id": study_id,
            "build_plan_id": build_plan_id,
            "scientific_specification_seal_id": scientific_seal.seal_id,
            "component_hashes": lock_hashes,
            "smoke_test_sha256": sha256_file(smoke_path),
            "container_image_id": container_image_id,
            "immutable": True,
        },
    )
    execution_lock = repository.register_artifact(
        study_id,
        str(execution_lock_path),
        sha256_file(execution_lock_path),
        kind="execution_package_lock",
        role=ArtifactRole.PROTOCOL,
    )
    lock_artifacts["execution-package.lock.json"] = (
        execution_lock.artifact_id
    )
    lock_hashes["execution-package.lock.json"] = execution_lock.sha256
    package_sha = _digest(lock_hashes)
    seal = ExecutionPackageSeal(
        seal_id=stable_id(
            "execution-seal", study_id, build_plan_id, package_sha
        ),
        study_id=study_id,
        contract_version=contract.version,
        scientific_specification_seal_id=scientific_seal.seal_id,
        lock_artifact_ids=lock_artifacts,
        smoke_test_artifact_id=smoke_artifact.artifact_id,
        conformance_checks={
            "research_contract_unchanged": True,
            "manifest_binds_both_arms": True,
            "formal_inputs_content_addressed": True,
            "smoke_outputs_schema_valid": True,
            "smoke_results_not_compared": True,
            "mvp_and_smoke_are_non_evidentiary": True,
            **(
                {"sample_level_independent_evaluator": True}
                if contract.schema_version >= 2
                else {"legacy_summary_evaluator_limit_disclosed": True}
            ),
        },
        trust_level=trust_level,
        isolated_execution_required=trust_level
        in {
            ExecutionTrustLevel.THIRD_PARTY_CODE,
            ExecutionTrustLevel.AI_GENERATED_CODE,
        },
        package_sha256=package_sha,
    )
    seal = repository.save_execution_package_seal(seal)
    _record_build_milestone(
        repository,
        study_id,
        group_id=plan.handoff_id,
        step_type="resolve_or_build_assets",
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        result={
            "build_plan_id": build_plan_id,
            "build_mode": Stage3BuildMode.READY_MADE_EXPERIMENT.value,
            "experiment_manifest_artifact_id": manifest_artifact.artifact_id,
            "trusted_local_import": True,
        },
        depends_on_step_types=["approve_experiment_build_plan"],
    )
    _record_build_milestone(
        repository,
        study_id,
        group_id=plan.handoff_id,
        step_type="run_engineering_smoke_tests",
        executor_type=ExecutorType.SANDBOX_RUNNER,
        result={
            "smoke_test_artifact_id": smoke_artifact.artifact_id,
            "formal_partition_compared": False,
            "arm_reports": smoke_reports,
        },
        depends_on_step_types=["resolve_or_build_assets"],
    )
    _record_build_milestone(
        repository,
        study_id,
        group_id=plan.handoff_id,
        step_type="verify_spec_conformance",
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        result={
            "execution_package_seal_id": seal.seal_id,
            "checks": seal.conformance_checks,
        },
        depends_on_step_types=["run_engineering_smoke_tests"],
    )
    _record_build_milestone(
        repository,
        study_id,
        group_id=plan.handoff_id,
        step_type="freeze_execution_package",
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        result=seal.model_dump(mode="json"),
        depends_on_step_types=["verify_spec_conformance"],
    )
    return seal


def freeze_generated_profile_v1_execution_package(
    repository: WorkflowRepository,
    study_id: str,
    build_plan_id: str,
) -> ExecutionPackageSeal:
    """Freeze a smoke-tested generated package for isolated formal execution."""

    plan = repository.load_experiment_build_plan(study_id, build_plan_id)
    if plan.build_mode is not Stage3BuildMode.BUILD_FROM_BLUEPRINT:
        raise Stage3BuildAdmissionError(
            ["generated freezer requires a blueprint Build Plan"]
        )
    build_handoff = repository.load_stage3_handoff(study_id)
    if (
        build_handoff.handoff_stage != "build"
        or build_handoff.handoff_id != plan.handoff_id
    ):
        raise Stage3BuildAdmissionError(
            ["generated freezer requires the active build handoff"]
        )
    assert build_handoff.scientific_specification_seal_id
    scientific_seal = repository.load_scientific_specification_seal(
        study_id, build_handoff.scientific_specification_seal_id
    )
    contract = repository.load_research_contract(
        study_id, build_handoff.contract_version
    )
    contract_path = (
        repository.root
        / "studies"
        / study_id
        / "contracts"
        / f"research-v{contract.version}.json"
    )
    if sha256_file(contract_path) != scientific_seal.contract_sha256:
        raise Stage3BuildAdmissionError(
            ["Research Contract changed after scientific freeze"]
        )

    root = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "materialized"
        / build_plan_id
    )
    generation_manifest_path = root / "generation_manifest.json"
    smoke_path = root / "generated_smoke_receipt.json"
    if not generation_manifest_path.is_file() or not smoke_path.is_file():
        raise Stage3BuildAdmissionError(
            ["generated package must be materialized and smoke-tested"]
        )
    generation = read_json(generation_manifest_path)
    smoke = read_json(smoke_path)
    smoke_required = {
        "evidence_eligible": False,
        "formal_run_eligible": False,
        "formal_data_mounted": False,
        "formal_targets_mounted": False,
        "candidate_saw_any_targets": False,
        "arm_performance_compared": False,
        "arms_schema_valid": True,
        "evaluator_golden_vector_passed": True,
    }
    if any(smoke.get(key) != value for key, value in smoke_required.items()):
        raise Stage3BuildAdmissionError(
            ["generated smoke receipt does not prove the no-peeking boundary"]
        )
    attestations = smoke.get("isolation_attestations") or {}
    image_ids = {
        str(item.get("image_id") or "")
        for item in attestations.values()
        if isinstance(item, dict)
    }
    image_ids.discard("")
    if len(image_ids) != 1:
        raise Stage3BuildAdmissionError(
            ["all generated smoke containers must bind one image digest"]
        )
    container_image_id = next(iter(image_ids))
    container_image = str(
        next(
            (
                item.get("image")
                for item in attestations.values()
                if isinstance(item, dict) and item.get("image")
            ),
            "python:3.12-slim",
        )
    )

    files_by_role: dict[str, list[str]] = {}
    for item in generation["files"]:
        relative = str(item["path"])
        path = (root / relative).resolve()
        path.relative_to(root.resolve())
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise Stage3BuildAdmissionError(
                [f"generated asset changed after materialization: {relative}"]
            )
        files_by_role.setdefault(str(item["role"]), []).append(relative)

    formal_data_path = str(generation["formal_data_path"])
    formal_target_path = str(generation["formal_target_path"])
    if (
        formal_data_path not in files_by_role.get("dataset", [])
        or formal_target_path
        not in files_by_role.get("target_dataset", [])
    ):
        raise Stage3BuildAdmissionError(
            ["formal candidate data and evaluator targets are not separated"]
        )
    common_candidate_code = files_by_role.get("dataset_adapter", [])
    platform_evaluator = files_by_role.get("platform_evaluator", [])
    if platform_evaluator != ["platform_evaluator.py"]:
        raise Stage3BuildAdmissionError(
            ["the platform evaluator is unavailable or ambiguous"]
        )
    metric_field = str(contract.output_schema["metric_field"])
    experiments: list[dict[str, Any]] = []
    for arm_id, binding in (
        ("baseline", contract.baseline),
        ("treatment", contract.treatment),
    ):
        experiment_id = str(binding.get("experiment_id") or "")
        action_id = str(binding.get("action_id") or "")
        if not experiment_id or not action_id:
            raise Stage3BuildAdmissionError(
                [f"{arm_id} lacks its frozen experiment/action binding"]
            )
        arm_code = files_by_role.get(arm_id, [])
        if not arm_code:
            raise Stage3BuildAdmissionError(
                [f"generated {arm_id} implementation is unavailable"]
            )
        experiments.append(
            {
                "experiment_id": experiment_id,
                "action_ids": [action_id],
                "title": f"Generated isolated {arm_id}",
                "command": generation["commands"][arm_id],
                "cwd": ".",
                "timeout_seconds": 300,
                "required_inputs": [formal_data_path],
                "execution_backend": "isolated_candidate_evaluator",
                "container_image": container_image,
                "candidate_code_paths": [
                    *common_candidate_code,
                    *arm_code,
                ],
                "evaluator_command": generation["commands"][
                    "formal_evaluator"
                ],
                "evaluator_code_paths": platform_evaluator,
                "evaluator_required_inputs": [formal_target_path],
                "prediction_artifact_path": generation[
                    "raw_prediction_path"
                ],
                "network_access": False,
                "artifacts": [
                    {
                        "path": generation["metric_output_path"],
                        "format": "json",
                        "required_keys": [
                            metric_field,
                            str(
                                contract.output_schema[
                                    "denominator_field"
                                ]
                            ),
                            str(
                                contract.output_schema["sample_id_field"]
                            ),
                        ],
                    }
                ],
            }
        )
    manifest_path = root / "research-forge.experiments.json"
    manifest_payload = {"schema_version": 1, "experiments": experiments}
    if (
        manifest_path.is_file()
        and read_json(manifest_path) != manifest_payload
    ):
        raise Stage3BuildAdmissionError(
            ["generated Experiment Manifest is immutable"]
        )
    write_json_atomic(manifest_path, manifest_payload)
    manifest_artifact = repository.register_artifact(
        study_id,
        str(manifest_path),
        sha256_file(manifest_path),
        kind="experiment_manifest",
        role=ArtifactRole.PROTOCOL,
    )
    smoke_artifact = repository.register_artifact(
        study_id,
        str(smoke_path),
        sha256_file(smoke_path),
        kind="generated_smoke_receipt",
        role=ArtifactRole.FEASIBILITY,
    )

    data_paths = [formal_data_path, formal_target_path]
    code_paths = sorted(
        set(
            common_candidate_code
            + files_by_role.get("baseline", [])
            + files_by_role.get("treatment", [])
        )
    )
    evaluator_paths = [
        *platform_evaluator,
        *files_by_role.get("evaluator_golden_vectors", []),
    ]
    lock_payloads = {
        "protocol.lock.json": {
            "contract_version": contract.version,
            "contract_sha256": scientific_seal.contract_sha256,
            "scientific_specification_seal_id": scientific_seal.seal_id,
        },
        "data_manifest.lock.json": {
            "candidate_inputs": [
                {"path": item, "sha256": sha256_file(root / item)}
                for item in data_paths[:1]
            ],
            "evaluator_targets": [
                {"path": item, "sha256": sha256_file(root / item)}
                for item in data_paths[1:]
            ],
            "candidate_cannot_mount_targets": True,
            "conforms_to": contract.data_requirements
            or contract.data_boundary,
        },
        "code_manifest.lock.json": {
            "candidate_code": [
                {"path": item, "sha256": sha256_file(root / item)}
                for item in code_paths
            ],
            "arm_commands": {
                arm: generation["commands"][arm]
                for arm in ("baseline", "treatment")
            },
            "conforms_to": contract.implementation_requirements,
        },
        "environment.lock.json": {
            "container_image": container_image,
            "container_image_id": container_image_id,
            "trust_level": ExecutionTrustLevel.AI_GENERATED_CODE.value,
            "network_default": "disabled",
            "read_only_inputs": True,
            "separate_outputs": True,
            "conforms_to": contract.environment_requirements
            or contract.runtime_binding,
        },
        "decision_rules.lock.json": {
            "statistical_rules": contract.statistical_rules,
            "hypothesis_rules": [
                item.decision_rule for item in contract.hypotheses
            ],
            "source_sha256": scientific_seal.decision_rules_sha256,
        },
        "model_selection.lock.json": (
            contract.model_selection_plan.model_dump(mode="json")
        ),
        "arm_fairness.lock.json": (
            contract.arm_fairness_contract.model_dump(mode="json")
        ),
        "attempt_selection.lock.json": (
            contract.attempt_selection_policy.model_dump(mode="json")
        ),
        "evaluator.lock.json": {
            "evaluator_files": [
                {"path": item, "sha256": sha256_file(root / item)}
                for item in evaluator_paths
            ],
            "command": generation["commands"]["formal_evaluator"],
            "output_schema": contract.output_schema,
            "metric": contract.metrics[0],
            "independent_from_arms": True,
            "frozen_before_formal_results": True,
        },
    }
    package_root = (
        repository.root
        / "studies"
        / study_id
        / "stage3"
        / "execution_packages"
        / build_plan_id
    )
    lock_artifacts: dict[str, str] = {}
    lock_hashes: dict[str, str] = {}
    for name, payload in lock_payloads.items():
        path = package_root / name
        lock_payload = {
            "schema_version": 1,
            "study_id": study_id,
            "build_plan_id": build_plan_id,
            "immutable": True,
            **payload,
        }
        if path.is_file() and read_json(path) != lock_payload:
            raise Stage3BuildAdmissionError(
                [f"generated execution lock is immutable: {name}"]
            )
        write_json_atomic(path, lock_payload)
        artifact = repository.register_artifact(
            study_id,
            str(path),
            sha256_file(path),
            kind=name.removesuffix(".json").replace(".", "_"),
            role=ArtifactRole.PROTOCOL,
        )
        lock_artifacts[name] = artifact.artifact_id
        lock_hashes[name] = artifact.sha256
    lock_artifacts["research-forge.experiments.json"] = (
        manifest_artifact.artifact_id
    )
    lock_hashes["research-forge.experiments.json"] = (
        manifest_artifact.sha256
    )
    execution_lock_path = package_root / "execution-package.lock.json"
    execution_lock_payload = {
        "schema_version": 1,
        "study_id": study_id,
        "build_plan_id": build_plan_id,
        "scientific_specification_seal_id": scientific_seal.seal_id,
        "component_hashes": lock_hashes,
        "smoke_test_sha256": smoke_artifact.sha256,
        "container_image_id": container_image_id,
        "immutable": True,
    }
    if (
        execution_lock_path.is_file()
        and read_json(execution_lock_path) != execution_lock_payload
    ):
        raise Stage3BuildAdmissionError(
            ["generated execution-package lock is immutable"]
        )
    write_json_atomic(execution_lock_path, execution_lock_payload)
    execution_lock = repository.register_artifact(
        study_id,
        str(execution_lock_path),
        sha256_file(execution_lock_path),
        kind="execution_package_lock",
        role=ArtifactRole.PROTOCOL,
    )
    lock_artifacts["execution-package.lock.json"] = (
        execution_lock.artifact_id
    )
    lock_hashes["execution-package.lock.json"] = execution_lock.sha256
    package_sha = _digest(lock_hashes)
    seal = ExecutionPackageSeal(
        seal_id=stable_id(
            "execution-seal", study_id, build_plan_id, package_sha
        ),
        study_id=study_id,
        contract_version=contract.version,
        scientific_specification_seal_id=scientific_seal.seal_id,
        lock_artifact_ids=lock_artifacts,
        smoke_test_artifact_id=smoke_artifact.artifact_id,
        conformance_checks={
            "research_contract_unchanged": True,
            "manifest_binds_both_arms": True,
            "formal_inputs_content_addressed": True,
                "candidate_and_targets_separated": True,
                "allowed_arm_delta_matches_blueprint": bool(
                    generation.get("declared_allowed_arm_delta")
                    == repository.load_experiment_blueprint(
                    study_id, str(build_handoff.experiment_blueprint_id)
                ).allowed_arm_delta
                ),
                "arm_command_structure_equal": bool(
                    generation.get("arm_command_structure_equal")
                ),
            "platform_evaluator_frozen": True,
            "container_image_digest_frozen": True,
            "smoke_results_not_compared": True,
            "mvp_and_smoke_are_non_evidentiary": True,
        },
        trust_level=ExecutionTrustLevel.AI_GENERATED_CODE,
        isolated_execution_required=True,
        package_sha256=package_sha,
    )
    seal = repository.save_execution_package_seal(seal)
    _record_build_milestone(
        repository,
        study_id,
        group_id=plan.handoff_id,
        step_type="verify_spec_conformance",
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        result={
            "execution_package_seal_id": seal.seal_id,
            "checks": seal.conformance_checks,
        },
        depends_on_step_types=["run_engineering_smoke_tests"],
    )
    _record_build_milestone(
        repository,
        study_id,
        group_id=plan.handoff_id,
        step_type="freeze_execution_package",
        executor_type=ExecutorType.DETERMINISTIC_SERVICE,
        result=seal.model_dump(mode="json"),
        depends_on_step_types=["verify_spec_conformance"],
    )
    return seal


def formal_execution_admission(
    repository: WorkflowRepository,
    study_id: str,
    execution_package_seal_id: str,
) -> Stage3HandoffPackage:
    """Second admission: bind concrete immutable assets to the execution kernel."""

    build_handoff = repository.load_stage3_handoff(study_id)
    if build_handoff.handoff_stage != "build":
        raise Stage3BuildAdmissionError(
            ["formal admission requires an active build handoff"]
        )
    seal = repository.load_execution_package_seal(
        study_id, execution_package_seal_id
    )
    if (
        seal.scientific_specification_seal_id
        != build_handoff.scientific_specification_seal_id
    ):
        raise Stage3BuildAdmissionError(
            ["execution package does not bind the active scientific seal"]
        )
    artifacts = {
        item.artifact_id: item
        for item in repository.list_artifacts(study_id)
    }
    for artifact_id in seal.lock_artifact_ids.values():
        artifact = artifacts.get(artifact_id)
        if (
            artifact is None
            or not Path(artifact.path).is_file()
            or sha256_file(Path(artifact.path)) != artifact.sha256
        ):
            raise Stage3BuildAdmissionError(
                ["execution package contains a missing or changed artifact"]
            )
    manifest_artifact = artifacts[
        seal.lock_artifact_ids["research-forge.experiments.json"]
    ]
    handoff = Stage3HandoffPackage(
        handoff_id=stable_id(
            "stage3-handoff",
            study_id,
            build_handoff.handoff_id,
            seal.seal_id,
        ),
        study_id=study_id,
        scope_version=build_handoff.scope_version,
        contract_version=build_handoff.contract_version,
        profile=build_handoff.profile,
        handoff_stage="formal_execution",
        build_mode=build_handoff.build_mode,
        scientific_specification_seal_id=(
            build_handoff.scientific_specification_seal_id
        ),
        experiment_blueprint_id=build_handoff.experiment_blueprint_id,
        mvp_feasibility_receipt_id=(
            build_handoff.mvp_feasibility_receipt_id
        ),
        execution_package_seal_id=seal.seal_id,
        lock_artifact_ids=seal.lock_artifact_ids,
        experiment_manifest_path=manifest_artifact.path,
        experiment_manifest_sha256=manifest_artifact.sha256,
        execution_root=str(Path(manifest_artifact.path).resolve().parent),
        predecessor_handoff_id=build_handoff.handoff_id,
    )
    handoff = repository.save_stage3_handoff(handoff)
    _record_build_milestone(
        repository,
        study_id,
        group_id=build_handoff.handoff_id,
        step_type="formal_execution_admission",
        executor_type=ExecutorType.DETERMINISTIC_EVALUATOR,
        result={
            "formal_handoff_id": handoff.handoff_id,
            "execution_package_seal_id": seal.seal_id,
            "admitted": True,
        },
        depends_on_step_types=["freeze_execution_package"],
    )
    return handoff


__all__ = [
    "Stage3BuildAdmissionError",
    "create_experiment_build_plan",
    "formal_execution_admission",
    "freeze_generated_profile_v1_execution_package",
    "freeze_ready_made_execution_package",
    "generate_and_materialize_profile_v1",
    "materialize_generated_profile_v1_package",
    "record_stage3_build_failure",
    "resolve_stage3_resource_routes",
    "smoke_generated_profile_v1_package",
    "resolve_profile_capability",
    "stage3_build_admission",
    "stage3_build_admission_from_stage2",
]
