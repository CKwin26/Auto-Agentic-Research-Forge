"""Persistent DAG execution for Workflow v2.

The scheduler treats ``StepInstance`` records as the execution source of truth.
Completed nodes are never repeated, ready siblings may run concurrently, and a
paused Study does not start another node.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, cast

from .claim_discovery import (
    AcademicConceptNormalization,
    AuthorClaim,
    ClaimDiscoveryReport,
    DiscoveryPortfolio,
    DiscoveryQueryIntent,
    ProjectResearchFingerprint,
    SourceSpan,
    TrendSignal,
    _terms,
    build_discovery_portfolio,
    build_discovery_query_intents,
    build_academic_concept_normalizations,
    build_project_fingerprint,
    extract_author_claims,
    recommend_claims,
    _is_procedural_statement,
)
from .models import ProjectMeta
from .project_bundle import (
    BundleInspection,
    BundleResource,
    NoveltyCandidate,
    discover_code_project_candidate,
    discover_derived_material_candidate,
    discover_hdf5_metadata_candidate,
    discover_novelty_candidates,
    inventory_project_bundle,
    inventory_project_hdf5_metadata,
    is_dependency_cache_root,
    is_driver_installation_bundle,
    is_excluded_bundle_path,
    is_shared_binary_dependency_bundle,
)
from .hdf5_metadata import HDF5MetadataResource
from .workflow_domain import (
    ArtifactStatus,
    ArtifactRole,
    DiagnosticOwner,
    EntryMode,
    ExecutionStatus,
    ExecutorType,
    GateType,
    HumanInterventionRecord,
    LiteratureSetVersion,
    Phase,
    RepairContract,
    RepairStatus,
    ScopeContractVersion,
    StepAcceptanceStatus,
    StepInstance,
    StudyLifecycle,
    WorkflowDiagnostic,
    WorkflowRepository,
    stable_id,
    utc_now,
)
from .retrieval.domain.models import (
    NetworkMode,
    ResourceType,
    RetrievalBudget,
    RetrievalPhase,
    retrieval_id,
)
from .retrieval.interfaces.service import RetrievalGateway
from .retrieval.workflow.step_definitions import (
    UNCONNECTED_RETRIEVAL_STEPS,
    retrieval_step_definitions,
)
from .retrieval.workflow.handlers import external_research_handlers
from .storage import read_json, sha256_file


class TransientStepError(RuntimeError):
    """An execution/environment failure that may succeed on a bounded retry."""

    def __init__(
        self, message: str, *, fallback_result: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.fallback_result = fallback_result


class BlockedStepError(RuntimeError):
    """A non-transient missing condition that requires an external change."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "requirement",
        redirect_phase: Phase | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.redirect_phase = redirect_phase


StepHandler = Callable[["StepContext"], dict[str, Any]]


@dataclass(frozen=True)
class StepContext:
    repository: WorkflowRepository
    study_id: str
    step: StepInstance

    def result(self, step_type: str) -> dict[str, Any]:
        # Only traverse immutable, already-succeeded ancestors. Reading every
        # step file here races with sibling status updates on Windows.
        pending = list(self.step.depends_on)
        visited: set[str] = set()
        matches: list[StepInstance] = []
        while pending:
            step_id = pending.pop()
            if step_id in visited:
                continue
            visited.add(step_id)
            ancestor = self.repository.load_step(self.study_id, step_id)
            if (
                ancestor.step_type == step_type
                and ancestor.status is ExecutionStatus.SUCCEEDED
            ):
                matches.append(ancestor)
            pending.extend(ancestor.depends_on)
        if not matches:
            raise BlockedStepError(
                f"required result is unavailable: {step_type}",
                kind="missing_dependency_result",
            )
        return self.repository.load_step_result(
            self.study_id, matches[-1].step_instance_id
        )


class PersistentDAGScheduler:
    def __init__(
        self,
        repository: WorkflowRepository,
        handlers: dict[str, StepHandler],
        *,
        max_concurrency: int = 4,
        retry_backoff_seconds: float = 0.05,
        recover_interrupted: bool = False,
    ) -> None:
        self.repository = repository
        self.handlers = handlers
        self.max_concurrency = max(1, max_concurrency)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._repository_lock = threading.RLock()
        self.recover_interrupted = recover_interrupted

    def run(self, study_id: str) -> dict[str, Any]:
        """Run until success, a user gate, pause, failure, or dependency block."""
        if self.recover_interrupted:
            self._recover_interrupted_steps(study_id)
            self.recover_interrupted = False
        while True:
            study = self.repository.load_study(study_id)
            if study.lifecycle is not StudyLifecycle.ACTIVE:
                return self.repository.snapshot(study_id)
            if study.execution_status is ExecutionStatus.PAUSED:
                return self.repository.snapshot(study_id)

            self._resolve_owner_steps(study_id)
            steps = self.repository.list_steps(study_id)
            self._block_impossible_descendants(study_id, steps)
            steps = self.repository.list_steps(study_id)
            ready = [
                step
                for step in steps
                if step.status is ExecutionStatus.QUEUED
                and all(
                    self.repository.load_step(study_id, dependency).status
                    is ExecutionStatus.SUCCEEDED
                    for dependency in step.depends_on
                )
            ]
            if not ready:
                return self.repository.snapshot(study_id)

            automatic: list[StepInstance] = []
            owner_steps_waiting = False
            for step in ready:
                if step.executor_type is ExecutorType.PROJECT_OWNER:
                    gate_label = {
                        "regression_scope_review": "repair scope",
                        "select_specific_topic": "specific research topic",
                        "research_contract_review": "Research Contract",
                        "stage3_execution_gate": "Stage 3 run plan",
                        "publication_narrative_selection": "Publication Narrative Contract",
                        "visual_argument_plan_approval": "Visual Argument Plan",
                        "final_visual_approval": "final figures and tables",
                        "humanization_author_approval": "humanized manuscript",
                        "author_final_review_and_approval": "final submission package",
                    }.get(step.step_type, "Scope Contract")
                    self.repository.update_step(
                        study_id,
                        step.step_instance_id,
                        ExecutionStatus.WAITING_FOR_USER,
                        blocker={
                            "kind": "owner_gate",
                            "message": (
                                f"The project owner must approve the {gate_label}."
                            ),
                        },
                    )
                    owner_steps_waiting = True
                else:
                    automatic.append(step)
            if not automatic:
                if owner_steps_waiting:
                    self._resolve_owner_steps(study_id)
                    if any(
                        self.repository.load_step(
                            study_id, step.step_instance_id
                        ).status
                        is ExecutionStatus.SUCCEEDED
                        for step in ready
                        if step.executor_type is ExecutorType.PROJECT_OWNER
                    ):
                        continue
                return self.repository.snapshot(study_id)

            with ThreadPoolExecutor(
                max_workers=min(self.max_concurrency, len(automatic))
            ) as pool:
                futures = {
                    pool.submit(self._execute_step, study_id, step): step
                    for step in automatic
                }
                for future in as_completed(futures):
                    future.result()
            # A sibling that succeeds after a blocked step can otherwise
            # overwrite the blocked step's phase redirect. Re-apply the
            # earliest requested redirect after the whole concurrent batch.
            redirected = [
                Phase(str(step.blocker["redirect_phase"]))
                for step in self.repository.list_steps(study_id)
                if step.status is ExecutionStatus.BLOCKED
                and isinstance(step.blocker, dict)
                and step.blocker.get("redirect_phase")
            ]
            if redirected:
                phase_order = {
                    Phase.DISCOVERY: 0,
                    Phase.PROTOCOL: 1,
                    Phase.EXPERIMENT: 2,
                    Phase.PAPER: 3,
                }
                redirect_phase = min(
                    redirected, key=lambda item: phase_order[item]
                )
                study = self.repository.load_study(study_id)
                if study.phase is not redirect_phase:
                    self.repository.save_study(
                        study.model_copy(update={"phase": redirect_phase}),
                        "blocked_batch_redirected_phase",
                    )

    def retry_step(
        self,
        study_id: str,
        step_id: str,
        *,
        authorized_by: str | None = None,
        reason: str | None = None,
    ) -> StepInstance:
        step = self.repository.load_step(study_id, step_id)
        if step.status not in {
            ExecutionStatus.FAILED,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.WAITING_FOR_USER,
        }:
            raise ValueError("only failed, blocked, or waiting steps can be retried")
        if step.step_type == "execute_stage3_run_cell":
            if not authorized_by or not reason:
                raise ValueError(
                    "manual Stage 3 rerun requires authorized_by and reason"
                )
            plan_id = str(step.parameters.get("plan_id") or "")
            repository_plan = self.repository.load_run_plan(
                study_id, plan_id
            )
            self.repository.save_human_intervention(
                HumanInterventionRecord(
                    intervention_id=stable_id(
                        "human-intervention",
                        study_id,
                        step_id,
                        step.attempt,
                        utc_now(),
                    ),
                    study_id=study_id,
                    plan_id=repository_plan.plan_id,
                    run_cell_id=str(
                        step.parameters.get("run_cell_id") or ""
                    ),
                    intervention_type="manual_rerun",
                    reason=reason,
                    authorized_by=authorized_by,
                )
            )
        # A retry is an explicit new scheduling decision; the attempt counter and
        # prior audit events stay intact.
        retried = self.repository.update_step(
            study_id, step_id, ExecutionStatus.QUEUED, blocker=None
        )
        self._requeue_dependency_blocked_descendants(study_id, step_id)
        return retried

    def _requeue_dependency_blocked_descendants(
        self, study_id: str, retried_step_id: str
    ) -> None:
        """Re-open only descendants blocked by the retried dependency chain.

        Permanent scientific, policy, integrity, or user-gate blockers remain
        untouched.  This makes a bounded retry useful without erasing attempts
        or indiscriminately resetting unrelated branches of the Study DAG.
        """

        steps = self.repository.list_steps(study_id)
        children: dict[str, list[StepInstance]] = {}
        for candidate in steps:
            for dependency_id in candidate.depends_on:
                children.setdefault(dependency_id, []).append(candidate)

        pending = [retried_step_id]
        visited: set[str] = set()
        while pending:
            parent_id = pending.pop()
            if parent_id in visited:
                continue
            visited.add(parent_id)
            for candidate in children.get(parent_id, []):
                blocker = candidate.blocker or {}
                dependency_ids = set(blocker.get("dependency_step_ids") or [])
                is_dependency_block = (
                    candidate.status is ExecutionStatus.BLOCKED
                    and blocker.get("kind") == "upstream_not_succeeded"
                    and parent_id in dependency_ids
                )
                if is_dependency_block:
                    self.repository.update_step(
                        study_id,
                        candidate.step_instance_id,
                        ExecutionStatus.QUEUED,
                        blocker=None,
                    )
                    pending.append(candidate.step_instance_id)

    def _execute_step(self, study_id: str, step: StepInstance) -> None:
        handler = self.handlers.get(step.step_type)
        if handler is None:
            self.repository.update_step(
                study_id,
                step.step_instance_id,
                ExecutionStatus.BLOCKED,
                blocker={
                    "kind": "missing_executor",
                    "message": f"no executor registered for {step.step_type}",
                },
            )
            return
        with self._repository_lock:
            self.repository.update_step(
                study_id, step.step_instance_id, ExecutionStatus.RUNNING
            )
        while True:
            current = self.repository.load_step(study_id, step.step_instance_id)
            try:
                result = handler(StepContext(self.repository, study_id, current))
                with self._repository_lock:
                    self._persist_success(study_id, current, result)
                return
            except TransientStepError as exc:
                latest = self.repository.load_step(
                    study_id, step.step_instance_id
                )
                if (
                    latest.lease_id != current.lease_id
                    or latest.fencing_token != current.fencing_token
                ):
                    return
                if latest.attempt >= latest.max_retries + 1:
                    with self._repository_lock:
                        if exc.fallback_result is not None:
                            self._persist_success(
                                study_id,
                                current,
                                exc.fallback_result,
                            )
                        else:
                            self._update_leased_step(
                                study_id,
                                current,
                                ExecutionStatus.FAILED,
                                blocker={
                                    "kind": "retry_exhausted",
                                    "message": str(exc),
                                },
                            )
                    return
                with self._repository_lock:
                    updated = self._update_leased_step(
                        study_id,
                        current,
                        ExecutionStatus.RETRYING,
                        blocker={"kind": "transient", "message": str(exc)},
                    )
                    if updated is None:
                        return
                time.sleep(
                    self.retry_backoff_seconds
                    * (2 ** max(0, latest.attempt - 1))
                )
            except BlockedStepError as exc:
                with self._repository_lock:
                    updated = self._update_leased_step(
                        study_id,
                        current,
                        ExecutionStatus.BLOCKED,
                        blocker={
                            "kind": exc.kind,
                            "message": str(exc),
                            "redirect_phase": (
                                exc.redirect_phase.value
                                if exc.redirect_phase is not None
                                else None
                            ),
                        },
                    )
                    if updated is None:
                        return
                    if exc.redirect_phase is not None:
                        study = self.repository.load_study(study_id)
                        self.repository.save_study(
                            study.model_copy(
                                update={"phase": exc.redirect_phase}
                            ),
                            "blocked_step_redirected_phase",
                        )
                return
            except Exception as exc:
                with self._repository_lock:
                    self._update_leased_step(
                        study_id,
                        current,
                        ExecutionStatus.FAILED,
                        blocker={
                            "kind": "permanent_execution_error",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        },
                    )
                return

    def _update_leased_step(
        self,
        study_id: str,
        step: StepInstance,
        status: ExecutionStatus,
        **kwargs: Any,
    ) -> StepInstance | None:
        try:
            return self.repository.update_step(
                study_id,
                step.step_instance_id,
                status,
                lease_id=step.lease_id,
                fencing_token=step.fencing_token,
                **kwargs,
            )
        except ValueError as exc:
            if "expired or mismatched execution lease" in str(exc):
                return None
            raise

    def _persist_success(
        self, study_id: str, step: StepInstance, result: dict[str, Any]
    ) -> None:
        acceptance = dict(result.get("_workflow_acceptance") or {})
        postcondition_passed = bool(
            acceptance.get("scientific_postcondition_passed", True)
        )
        if not postcondition_passed:
            raise ValueError(
                "step output failed its declared scientific postcondition"
            )
        artifact = self.repository.save_step_result(
            study_id,
            step.step_instance_id,
            result,
            lease_id=step.lease_id,
            fencing_token=step.fencing_token,
        )
        extra_output_ids = [
            str(item) for item in result.get("_workflow_output_artifact_ids", [])
        ]
        for dependency_id in step.depends_on:
            dependency = self.repository.load_step(study_id, dependency_id)
            for input_artifact_id in dependency.output_artifact_ids:
                self.repository.add_dependency(
                    study_id,
                    input_artifact_id,
                    artifact.artifact_id,
                    relation="step_depends_on",
                )
        updated = self._update_leased_step(
            study_id,
            step,
            ExecutionStatus.SUCCEEDED,
            output_artifact_ids=[artifact.artifact_id, *extra_output_ids],
            acceptance_status=StepAcceptanceStatus.ACCEPTED,
            output_produced=True,
            schema_validated=True,
            scientific_postcondition_passed=True,
            acceptance_checks={
                str(key): bool(value)
                for key, value in dict(acceptance.get("checks") or {}).items()
            },
        )
        if updated is None:
            return
        next_phase = result.get("_workflow_next_phase")
        if next_phase:
            study = self.repository.load_study(study_id)
            self.repository.save_study(
                study.model_copy(update={"phase": Phase(str(next_phase))}),
                "workflow_phase_advanced",
            )
        if step.step_type == "completion_record":
            self.repository.finish_study(
                study_id, StudyLifecycle.COMPLETED
            )

    def _resolve_owner_steps(self, study_id: str) -> None:
        gates = self.repository.list_gates(study_id)
        latest_contract = self.repository.latest_research_contract(study_id)
        for step in self.repository.list_steps(study_id):
            if (
                step.executor_type is not ExecutorType.PROJECT_OWNER
                or step.status is not ExecutionStatus.WAITING_FOR_USER
            ):
                continue
            relevant = (
                [gate for gate in gates if gate.gate_type is GateType.SCOPE_APPROVAL]
                if step.step_type in {"scope_review", "scope_review_gate"}
                else [
                    gate
                    for gate in gates
                    if gate.gate_type is GateType.RESEARCH_CONTRACT
                    and latest_contract is not None
                    and gate.subject_version
                    == latest_contract.version
                ]
                if step.step_type == "research_contract_review"
                else [
                    gate
                    for gate in gates
                    if gate.gate_type is GateType.REPAIR_OR_HIGH_COST_RUN
                    and (
                        step.step_type != "stage3_execution_gate"
                        or (
                            gate.subject_type == "stage3_run_plan"
                            and gate.subject_id
                            == step.parameters.get("plan_id")
                        )
                    )
                ]
                if step.step_type in {
                    "regression_scope_review",
                    "stage3_execution_gate",
                }
                else [
                    gate
                    for gate in gates
                    if (
                        (
                            step.step_type == "publication_narrative_selection"
                            and gate.gate_type is GateType.PUBLICATION_NARRATIVE
                        )
                        or (
                            step.step_type
                            in {
                                "visual_argument_plan_approval",
                                "final_visual_approval",
                            }
                            and gate.gate_type is GateType.VISUAL_ARGUMENT
                        )
                        or (
                            step.step_type == "humanization_author_approval"
                            and gate.gate_type is GateType.AUTHOR_VOICE
                        )
                        or (
                            step.step_type == "author_final_review_and_approval"
                            and gate.gate_type is GateType.FINAL_SUBMISSION
                        )
                    )
                    and (
                        not step.parameters.get("subject_id")
                        or gate.subject_id == step.parameters.get("subject_id")
                    )
                ]
                if step.step_type
                in {
                    "publication_narrative_selection",
                    "visual_argument_plan_approval",
                    "final_visual_approval",
                    "humanization_author_approval",
                    "author_final_review_and_approval",
                }
                else []
            )
            if step.step_type == "research_contract_review" and not relevant:
                assessments = [
                    candidate
                    for candidate in self.repository.list_steps(study_id)
                    if candidate.step_type == "assess_stage2_gate"
                    and candidate.status is ExecutionStatus.SUCCEEDED
                ]
                if assessments:
                    latest_assessment = assessments[-1]
                    result = self.repository.load_step_result(
                        study_id, latest_assessment.step_instance_id
                    )
                    gate_report = result.get("stage2_gate_report", result)
                    if gate_report.get("status") in {
                        "FAIL",
                        "BUILD_REQUIRED",
                    }:
                        self.repository.update_step(
                            study_id,
                            step.step_instance_id,
                            ExecutionStatus.BLOCKED,
                            blocker={
                                "kind": (
                                    "experiment_build_required"
                                    if gate_report.get("status")
                                    == "BUILD_REQUIRED"
                                    else "gate_conditions_failed"
                                ),
                                "message": (
                                    "Stage 2 still needs a non-scientific MVP: "
                                    "a minimum runnable environment, 2–3 smoke "
                                    "cases, metric computation, and conceptual "
                                    "baseline feasibility."
                                    if gate_report.get("status")
                                    == "BUILD_REQUIRED"
                                    else (
                                        "Stage 2 Gate conditions failed; revise "
                                        "the draft contract or resource selection "
                                        "before requesting owner approval."
                                    )
                                ),
                            },
                        )
                        continue
            if any(gate.status.value == "approved" for gate in relevant):
                self._persist_success(
                    study_id,
                    step,
                    {
                        "decision": "approved",
                        "gate_ids": [gate.gate_id for gate in relevant],
                    },
                )
            elif any(gate.status.value == "rejected" for gate in relevant):
                self.repository.update_step(
                    study_id,
                    step.step_instance_id,
                    ExecutionStatus.BLOCKED,
                    blocker={
                        "kind": "gate_rejected",
                        "message": (
                            "The project owner rejected the required workflow Gate."
                        ),
                    },
                )

    def _recover_interrupted_steps(self, study_id: str) -> None:
        for step in self.repository.list_steps(study_id):
            if (
                step.status is ExecutionStatus.BLOCKED
                and step.blocker
                and step.blocker.get("kind") == "paused_checkpoint"
            ):
                self.repository.update_step(
                    study_id,
                    step.step_instance_id,
                    ExecutionStatus.QUEUED,
                    blocker={
                        "kind": "resumed_from_safe_checkpoint",
                        "message": (
                            "The paused run cell will start a new append-only "
                            "ExecutionAttempt."
                        ),
                    },
                )
                continue
            if step.status not in {
                ExecutionStatus.RUNNING,
                ExecutionStatus.RETRYING,
            }:
                continue
            self.repository.update_step(
                study_id,
                step.step_instance_id,
                ExecutionStatus.QUEUED,
                blocker={
                    "kind": "recovered_after_process_restart",
                    "message": "The previous executor stopped before a terminal checkpoint.",
                },
            )

    def _block_impossible_descendants(
        self, study_id: str, steps: list[StepInstance]
    ) -> None:
        impossible = {
            ExecutionStatus.FAILED,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.CANCELLED,
        }
        current = steps
        while True:
            statuses = {item.step_instance_id: item.status for item in current}
            changed = False
            for step in current:
                if step.status is not ExecutionStatus.QUEUED:
                    continue
                failed = [
                    dependency
                    for dependency in step.depends_on
                    if statuses.get(dependency) in impossible
                ]
                if failed:
                    self.repository.update_step(
                        study_id,
                        step.step_instance_id,
                        ExecutionStatus.BLOCKED,
                        blocker={
                            "kind": "upstream_not_succeeded",
                            "dependency_step_ids": failed,
                            "message": "An upstream dependency did not succeed.",
                        },
                    )
                    changed = True
            if not changed:
                return
            current = self.repository.list_steps(study_id)


def _models(payload: list[dict[str, Any]], model: Any) -> list[Any]:
    return [model.model_validate(item) for item in payload]


def _project_scan(context: StepContext) -> dict[str, Any]:
    project = context.repository.load_project(
        context.repository.load_study(context.study_id).project_id
    )
    if not project.source_root:
        raise BlockedStepError("project source folder is not configured")
    if is_dependency_cache_root(project.source_root):
        raise BlockedStepError(
            "selected source is a package-manager dependency cache, not a "
            "research project bundle"
        )
    if is_shared_binary_dependency_bundle(project.source_root):
        raise BlockedStepError(
            "selected source is a shared binary runtime/dependency "
            "installation bundle, not a research project or "
            "research-material bundle"
        )
    resources, excluded = inventory_project_bundle(project.source_root)
    hdf5_metadata = inventory_project_hdf5_metadata(project.source_root)
    from .pdf_materials import extract_pdf_material

    pdf_metadata = []
    source_root = Path(project.source_root).resolve()
    for resource in resources:
        if resource.suffix != ".pdf":
            continue
        metadata = extract_pdf_material(
            source_root / resource.path, limit=200_000
        )
        pdf_metadata.append(
            {
                key: value
                for key, value in metadata.items()
                if key != "text"
            }
            | {"path": resource.path}
        )
    if is_driver_installation_bundle(project.source_root, resources):
        raise BlockedStepError(
            "selected source is a binary device-driver installation bundle, "
            "not a research project bundle"
        )
    return {
        "source_root": str(Path(project.source_root).resolve()),
        "resource_count": len(resources),
        "excluded_count": excluded,
        "resources": [item.model_dump(mode="json") for item in resources],
        "hdf5_metadata": [
            item.model_dump(mode="json") for item in hdf5_metadata
        ],
        "pdf_metadata": pdf_metadata,
        "include_external": bool(
            context.repository.load_study(context.study_id).settings.get(
                "include_external_discovery", True
            )
        ),
    }


def _author_claim_extraction(context: StepContext) -> dict[str, Any]:
    scan = context.result("project_scan")
    resources = _models(scan["resources"], BundleResource)
    study = context.repository.load_study(context.study_id)
    source_root = Path(scan["source_root"])
    if (
        study.entry_mode is EntryMode.IDEA_TO_PAPER
        and (source_root / "project.json").is_file()
    ):
        meta = ProjectMeta.model_validate(read_json(source_root / "project.json"))
        claims = [
            AuthorClaim(
                claim_id=stable_id(
                    "author-claim", context.study_id, "research-idea"
                ),
                statement=meta.idea,
                claim_type="research_question",
                source_spans=[
                    SourceSpan(
                        path="project.json",
                        sha256=sha256_file(source_root / "project.json"),
                        section="idea",
                    )
                ],
            )
        ]
    else:
        claims = extract_author_claims(source_root, resources)
    repair_actions = set(
        context.repository.load_study(context.study_id).settings.get(
            "discovery_repair_actions", []
        )
    )
    if "procedural_claim_false_positive" in repair_actions:
        claims = [
            item for item in claims if not _is_procedural_statement(item.statement)
        ]
    if "duplicate_author_claim" in repair_actions:
        unique: list[AuthorClaim] = []
        seen: set[tuple[str, str]] = set()
        for item in claims:
            key = (
                item.statement.strip().casefold(),
                item.source_spans[0].path.strip().casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        claims = unique
    return {"author_claims": [item.model_dump(mode="json") for item in claims]}


def _candidate_discovery(context: StepContext) -> dict[str, Any]:
    scan = context.result("project_scan")
    resources = _models(scan["resources"], BundleResource)
    study = context.repository.load_study(context.study_id)
    source_root = Path(scan["source_root"])
    if (
        study.entry_mode is EntryMode.IDEA_TO_PAPER
        and (source_root / "project.json").is_file()
    ):
        meta = ProjectMeta.model_validate(read_json(source_root / "project.json"))
        project_resource = next(
            (item for item in resources if item.path == "project.json"),
            None,
        )
        candidates = [
            NoveltyCandidate(
                track_id=stable_id(
                    "idea-track", context.study_id, meta.idea
                ),
                novelty_seed=meta.idea,
                protocol_path="project.json",
                latest_artifact_at=(
                    project_resource.modified_at
                    if project_resource is not None
                    else utc_now()
                ),
                evidence_maturity="mixed_or_unspecified",
                artifact_chain_complete=False,
                protocol_bound_to_output=False,
                paperability_score=3,
                paperability_reasons=[
                    "owner-approved research direction",
                    "prospective experiment can be designed",
                ],
                blockers=[
                    "frozen_protocol_output_binding",
                    "independent_validation",
                ],
                source_mode="derived_materials",
                closure_input_ready=False,
                display_title=meta.name,
            )
        ]
    else:
        candidates = discover_novelty_candidates(scan["source_root"], resources)
    if not candidates:
        hdf5_candidate = discover_hdf5_metadata_candidate(
            scan["source_root"],
            _models(scan.get("hdf5_metadata", []), HDF5MetadataResource),
        )
        if hdf5_candidate is not None:
            candidates = [hdf5_candidate]
        else:
            derived = discover_derived_material_candidate(
                scan["source_root"], resources
            )
            if derived is not None:
                candidates = [derived]
            else:
                code_candidate = discover_code_project_candidate(
                    scan["source_root"], resources
                )
                if code_candidate is not None:
                    candidates = [code_candidate]
    return {"candidates": [item.model_dump(mode="json") for item in candidates]}


def _academic_concept_normalization(context: StepContext) -> dict[str, Any]:
    scan = context.result("project_scan")
    claims = _models(
        context.result("author_claim_extraction")["author_claims"], AuthorClaim
    )
    candidates = context.result("candidate_discovery")["candidates"]
    normalizations = build_academic_concept_normalizations(
        scan["source_root"], candidates, claims
    )
    return {
        "academic_normalizations": [
            item.model_dump(mode="json") for item in normalizations
        ]
    }


def _fingerprint(context: StepContext) -> dict[str, Any]:
    scan = context.result("project_scan")
    claims = context.result("author_claim_extraction")
    candidates = context.result("candidate_discovery")
    fingerprint = build_project_fingerprint(
        scan["resources"],
        candidates["candidates"],
        _models(claims["author_claims"], AuthorClaim),
        source_root=scan["source_root"],
    )
    normalization_step = next(
        (
            item
            for item in context.repository.list_steps(context.study_id)
            if item.step_type == "academic_concept_normalization"
            and item.status is ExecutionStatus.SUCCEEDED
        ),
        None,
    )
    if normalization_step is not None:
        payload = context.repository.load_step_result(
            context.study_id, normalization_step.step_instance_id
        )
        normalizations = _models(
            payload["academic_normalizations"],
            AcademicConceptNormalization,
        )
        fingerprint = fingerprint.model_copy(
            update={"academic_normalizations": normalizations}
        )
    return {"fingerprint": fingerprint.model_dump(mode="json")}


def _gateway(context: StepContext) -> RetrievalGateway:
    return RetrievalGateway(str(context.repository.root))


def _draft_discovery_query_plan(context: StepContext) -> dict[str, Any]:
    scan = context.result("project_scan")
    fingerprint = ProjectResearchFingerprint.model_validate(
        context.result("project_fingerprint")["fingerprint"]
    )
    claims = _models(
        context.result("author_claim_extraction")["author_claims"], AuthorClaim
    )
    query_intents = build_discovery_query_intents(fingerprint, claims)
    all_queries = [item.query for item in query_intents]
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    execute_step = next(
        item
        for item in context.repository.list_steps(context.study_id)
        if item.step_type == "execute_discovery_retrieval"
    )
    gateway = _gateway(context)
    policy = gateway.get_policy(project.project_id)
    provider_types = {
        "paper_search_mcp": {ResourceType.PUBLICATION, ResourceType.PREPRINT},
        "semantic_scholar": {ResourceType.PUBLICATION},
        "crossref": {ResourceType.PUBLICATION},
        "github": {ResourceType.CODE_REPOSITORY},
        "huggingface": {
            ResourceType.MODEL,
            ResourceType.DATASET,
            ResourceType.SPACE,
        },
        "codex_native_web_search": {ResourceType.WEB_SOURCE},
        "openai_web_search": {ResourceType.WEB_SOURCE},
        "redfox_wechat": {ResourceType.WEB_SOURCE},
    }
    providers = [
        provider for provider in provider_types if provider in policy.allowed_providers
    ]
    resource_types = sorted(
        {
            resource_type
            for provider in providers
            for resource_type in provider_types[provider]
        },
        key=lambda item: item.value,
    )
    if (
        policy.mode in {NetworkMode.PUBLIC_WEB_READ, NetworkMode.AUTHENTICATED_READ}
        and "redfox_wechat" in policy.allowed_providers
    ):
        if "redfox_wechat" not in providers:
            providers.append("redfox_wechat")
            resource_types.append(ResourceType.WEB_SOURCE)
    request = gateway.plan(
        project_id=project.project_id,
        study_id=context.study_id,
        phase=RetrievalPhase.DISCOVERY,
        step_instance_id=execute_step.step_instance_id,
        purpose="related_work_search",
        queries=all_queries,
        providers=providers,
        resource_types=resource_types,
        usage_role="background_source",
        budget=RetrievalBudget(
            max_queries=8,
            max_results=120,
            max_download_bytes=5_000_000,
            max_cost=1.0,
        ),
        idempotency_key=f"{context.study_id}:discovery:v1",
        internal_identifiers=[
            project.title,
            Path(scan["source_root"]).name,
        ],
        research_need="Find related work, negative results, and external attention signals.",
        freshness=("cache_only" if policy.mode is NetworkMode.OFFLINE else "live"),
        blocked_domains=["scholar.google.com"],
    )
    return {
        "request_id": request.request_id,
        "query_plan_id": request.query_plan_id,
        "network_policy_id": request.network_policy_id,
        "query_intents": [
            item.model_dump(mode="json") for item in query_intents
        ],
    }


def _evaluate_discovery_policy(context: StepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    request = gateway.repository.load_request(
        context.result("draft_discovery_query_plan")["request_id"]
    )
    plan = gateway.repository.load_query_plan(request.query_plan_id)
    policy = gateway.get_policy(request.project_id)
    decision = gateway.policy_engine.evaluate(
        request, policy, sanitized=bool(plan.sanitized_queries)
    )
    return decision.model_dump(mode="json")


def _sanitize_discovery_queries(context: StepContext) -> dict[str, Any]:
    gateway = _gateway(context)
    request = gateway.repository.load_request(
        context.result("draft_discovery_query_plan")["request_id"]
    )
    plan = gateway.repository.load_query_plan(request.query_plan_id)
    return {
        "query_plan_id": plan.query_plan_id,
        "sanitized_queries": plan.sanitized_queries,
        "raw_query_digest": plan.raw_query_digest,
    }


def _execute_discovery_retrieval(context: StepContext) -> dict[str, Any]:
    request_id = context.result("draft_discovery_query_plan")["request_id"]
    gateway = _gateway(context)
    execution = gateway.run(request_id)
    workflow_artifact_ids: list[str] = []
    for retrieval_artifact_id in execution.run.output_artifact_ids:
        item = gateway.repository.load_artifact(retrieval_artifact_id)
        role = (
            ArtifactRole.LITERATURE_BACKGROUND
            if item.kind
            in {
                "normalized_resources",
                "verification_report",
                "resource_set",
            }
            else ArtifactRole.AUDIT
        )
        registered = context.repository.register_artifact(
            context.study_id,
            item.path,
            item.content_hash,
            kind=f"retrieval_{item.kind}",
            role=role,
        )
        workflow_artifact_ids.append(registered.artifact_id)
    attempts = execution.run.provider_attempts
    statuses: dict[str, str] = {}
    requested_providers = gateway.repository.load_request(
        request_id
    ).requested_providers
    visible_providers = list(
        dict.fromkeys(
            [
                *requested_providers,
                "paper_search_mcp",
                "semantic_scholar",
                "crossref",
                "github",
                "huggingface",
                "codex_native_web_search",
                "openai_web_search",
                "redfox_wechat",
            ]
        )
    )
    include_external = bool(
        context.repository.load_study(context.study_id).settings.get(
            "include_external_discovery", False
        )
    )
    for provider in visible_providers:
        provider_attempts = [item for item in attempts if item.provider == provider]
        succeeded = [
            item for item in provider_attempts if item.status.value == "succeeded"
        ]
        if succeeded:
            statuses[provider] = f"ok:{succeeded[-1].result_count}"
        elif provider_attempts:
            error = provider_attempts[-1].error_classification
            statuses[provider] = f"degraded:{error.value if error else 'unknown'}"
        else:
            statuses[provider] = (
                "not_requested" if not include_external else "policy_denied"
            )
    if (
        include_external
        and execution.run.execution_status.value == "blocked"
    ):
        classification = (
            execution.run.error_classification.value
            if execution.run.error_classification is not None
            else "retrieval_blocked"
        )
        raise BlockedStepError(
            (
                "External discovery was requested, but the Project retrieval "
                f"policy blocked every provider ({classification}). Approve a "
                "public read-only retrieval policy for this Project before "
                "continuing discovery."
            ),
            kind="retrieval_policy_approval_required",
        )
    return {
        **execution.model_dump(),
        "provider_status": statuses,
        "evidence_gap": execution.run.execution_status.value == "blocked"
        or not (execution.resource_set and execution.resource_set.binding_ids),
        "_workflow_output_artifact_ids": workflow_artifact_ids,
    }


def _normalize_external_resources(context: StepContext) -> dict[str, Any]:
    execution = context.result("execute_discovery_retrieval")
    gateway = _gateway(context)
    resource_ids = {
        item.resource_id
        for item in gateway.repository.list_bindings(context.study_id)
        if item.phase is RetrievalPhase.DISCOVERY
    }
    resources = [
        gateway.repository.load_resource(resource_id)
        for resource_id in sorted(resource_ids)
    ]
    signals: list[dict[str, Any]] = []
    for resource in resources:
        metadata = resource.metadata
        provider = resource.providers[0]
        summary = str(metadata.get("summary", ""))
        source_terms = sorted(
            {
                *[str(item) for item in (metadata.get("terms") or [])],
                *_terms(f"{resource.title} {summary}"),
            }
        )[:100]
        signal_class_value = str(
            metadata.get("signal_class")
            or (
                "market_attention"
                if provider == "redfox_wechat"
                else "adoption_signal"
                if provider in {"github", "huggingface"}
                else "official_source"
                if provider
                in {
                    "codex_native_web_search",
                    "openai_web_search",
                }
                else "scholarly_attention"
            )
        )
        if signal_class_value not in {
            "market_attention",
            "scholarly_attention",
            "official_source",
            "adoption_signal",
        }:
            signal_class_value = "official_source"
        signal_class = cast(
            Literal[
                "market_attention",
                "scholarly_attention",
                "official_source",
                "adoption_signal",
            ],
            signal_class_value,
        )
        signals.append(
            TrendSignal(
                signal_id=retrieval_id("signal", resource.resource_id),
                provider=provider,  # type: ignore[arg-type]
                signal_class=signal_class,
                query="gateway-normalized",
                title=resource.title,
                summary=summary,
                url=resource.url or resource.canonical_identifier,
                published_at=resource.publication_or_release_date or "",
                source_name=(
                    resource.authors_or_owners[0] if resource.authors_or_owners else ""
                ),
                engagement=metadata.get("attention") or {},
                terms=source_terms,
                trend_score=float(metadata.get("attention_score", 0)),
                scientific_density=float(metadata.get("scientific_density", 0)),
                metadata={
                    key: value
                    for key, value in metadata.items()
                    if key
                    not in {
                        "title",
                        "summary",
                        "published_at",
                        "source_name",
                    }
                },
            ).model_dump(mode="json")
        )
    return {
        "signals": signals,
        "resource_ids": [item.resource_id for item in resources],
        "provider_status": execution["provider_status"],
        "evidence_gap": execution["evidence_gap"],
    }


def _deduplicate_external_resources(context: StepContext) -> dict[str, Any]:
    normalized = context.result("normalize_external_resources")
    coverage = context.result("execute_discovery_retrieval").get("coverage")
    return {
        "resource_ids": normalized["resource_ids"],
        "input_count": (coverage or {}).get("raw_result_count", 0),
        "output_count": (coverage or {}).get("deduplicated_result_count", 0),
        "metadata_conflicts": (coverage or {}).get("metadata_conflicts", []),
    }


def _verify_external_metadata(context: StepContext) -> dict[str, Any]:
    coverage = context.result("execute_discovery_retrieval").get("coverage")
    return {
        "resource_ids": context.result("deduplicate_external_resources")[
            "resource_ids"
        ],
        "verified_count": (coverage or {}).get("verified_result_count", 0),
        "license_constraints": (coverage or {}).get("license_constraints", []),
        "metadata_conflicts": (coverage or {}).get("metadata_conflicts", []),
    }


def _build_discovery_source_set(context: StepContext) -> dict[str, Any]:
    execution = context.result("execute_discovery_retrieval")
    return {
        "resource_set": execution.get("resource_set"),
        "coverage": execution.get("coverage"),
        "evidence_role": "background_and_discovery_only",
        "verdict_eligible": False,
    }


def bind_frozen_discovery_literature_set(
    repository: WorkflowRepository,
    study_id: str,
    *,
    resource_set_payload: dict[str, Any] | None = None,
) -> LiteratureSetVersion | None:
    """Bind a frozen retrieval ResourceSet into Workflow literature v1.

    This is a compatibility bridge between the horizontal Retrieval Gateway
    and the versioned publication workflow.  It creates a new immutable
    binding only; it never changes the frozen retrieval set or promotes
    background sources into decision evidence.
    """

    existing = repository.list_literature_sets(study_id)
    if existing:
        return existing[-1]
    resource_set = resource_set_payload
    if resource_set is None:
        freeze_steps = [
            item
            for item in repository.list_steps(study_id)
            if item.step_type == "freeze_discovery_source_set"
            and item.status is ExecutionStatus.SUCCEEDED
        ]
        if not freeze_steps:
            return None
        result = repository.load_step_result(
            study_id, freeze_steps[-1].step_instance_id
        )
        resource_set = result.get("resource_set")
    if (
        not isinstance(resource_set, dict)
        or resource_set.get("status") != "frozen"
    ):
        return None
    gateway = RetrievalGateway(str(repository.root))
    source_ids: set[str] = set()
    for binding_id in resource_set.get("binding_ids", []):
        binding = gateway.repository.load_binding(str(binding_id))
        resource = gateway.repository.load_resource(binding.resource_id)
        if resource.resource_type in {
            ResourceType.PUBLICATION,
            ResourceType.PREPRINT,
        }:
            source_ids.add(resource.resource_id)
    if not source_ids:
        return None
    literature = LiteratureSetVersion(
        literature_set_id="literature-discovery-sources",
        study_id=study_id,
        version=1,
        status=ArtifactStatus.FROZEN,
        background_source_ids=sorted(source_ids),
        decision_source_ids=[],
        affects_novelty=True,
        affects_research_design=False,
    )
    return repository.save_literature_set(literature)


def _freeze_discovery_source_set(context: StepContext) -> dict[str, Any]:
    payload = context.result("build_discovery_source_set")
    resource_set = payload.get("resource_set")
    if not resource_set:
        return {
            "status": "not_applicable",
            "reason": "offline or empty retrieval produced no ResourceSet",
        }
    frozen = _gateway(context).freeze_resource_set(str(resource_set["resource_set_id"]))
    literature = bind_frozen_discovery_literature_set(
        context.repository,
        context.study_id,
        resource_set_payload=frozen.model_dump(mode="json"),
    )
    return {
        "resource_set": frozen.model_dump(mode="json"),
        "literature_set": (
            literature.model_dump(mode="json")
            if literature is not None
            else None
        ),
    }


def _recommendation(context: StepContext) -> dict[str, Any]:
    scan = context.result("project_scan")
    candidates = context.result("candidate_discovery")["candidates"]
    claims = _models(
        context.result("author_claim_extraction")["author_claims"], AuthorClaim
    )
    fingerprint = ProjectResearchFingerprint.model_validate(
        context.result("project_fingerprint")["fingerprint"]
    )
    retrieval = context.result("normalize_external_resources")
    trends = _models(retrieval["signals"], TrendSignal)
    recommendations = recommend_claims(fingerprint, claims, trends, candidates)
    coverage = context.result("execute_discovery_retrieval").get("coverage") or {}
    warnings = [
        *[str(item) for item in coverage.get("known_blind_spots", [])],
        *[str(item) for item in coverage.get("metadata_conflicts", [])],
    ]
    report = ClaimDiscoveryReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_root=scan["source_root"],
        fingerprint=fingerprint,
        author_claims=claims,
        trend_signals=trends,
        recommended_claims=recommendations,
        provider_status=retrieval["provider_status"],
        warnings=warnings,
    )
    return {"claim_discovery": report.model_dump(mode="json")}


def _evidence_chain_detection(context: StepContext) -> dict[str, Any]:
    candidates = context.result("candidate_discovery")["candidates"]
    chains = []
    for candidate in candidates:
        outputs = [
            path
            for path in (
                candidate.get("output_path"),
                candidate.get("report_path"),
            )
            if path
        ]
        chains.append(
            {
                "track_id": candidate["track_id"],
                "level": (
                    "verified_chain"
                    if candidate.get("artifact_chain_complete")
                    and candidate.get("protocol_bound_to_output")
                    else "inferred_chain"
                ),
                "protocol_path": candidate["protocol_path"],
                "output_paths": outputs,
                "verdict_eligible": bool(
                    candidate.get("artifact_chain_complete")
                    and candidate.get("protocol_bound_to_output")
                ),
            }
        )
    return {"chains": chains}


def _discovery_portfolio(context: StepContext) -> dict[str, Any]:
    fingerprint = ProjectResearchFingerprint.model_validate(
        context.result("project_fingerprint")["fingerprint"]
    )
    candidates = context.result("candidate_discovery")["candidates"]
    claim_report = ClaimDiscoveryReport.model_validate(
        context.result("claim_recommendation")["claim_discovery"]
    )
    query_plan = context.result("draft_discovery_query_plan")
    query_intents = _models(
        query_plan.get("query_intents", []), DiscoveryQueryIntent
    )
    source_set = context.result("build_discovery_source_set")
    resource_set = source_set.get("resource_set") or {}
    resource_set_ids = [
        str(resource_set["resource_set_id"])
    ] if resource_set.get("resource_set_id") else []
    portfolio = build_discovery_portfolio(
        fingerprint,
        candidates,
        claim_report,
        query_intents,
        query_plan_id=str(query_plan.get("query_plan_id") or "") or None,
        resource_set_ids=resource_set_ids,
        coverage=source_set.get("coverage") or {},
    )
    return {"discovery_portfolio": portfolio.model_dump(mode="json")}


def _scope_drafting(context: StepContext) -> dict[str, Any]:
    portfolio = DiscoveryPortfolio.model_validate(
        context.result("discovery_portfolio")["discovery_portfolio"]
    )
    selected = next(
        (
            item
            for item in portfolio.directions
            if item.direction_id == portfolio.recommended_direction_id
        ),
        portfolio.directions[0] if portfolio.directions else None,
    )
    if selected is None:
        raise BlockedStepError(
            "Discovery Portfolio contains no viable direction.",
            kind="no_viable_direction",
        )
    contract = ScopeContractVersion(
        study_id=context.study_id,
        version=1,
        direction=selected.title[:300],
        research_question=selected.research_question[:1200],
        scope_in=selected.scope_in,
        scope_out=selected.scope_out,
        candidate_contribution=selected.candidate_contribution[:2000],
        project_resource_ids=selected.local_evidence_paths,
        literature_set_id=(
            portfolio.resource_set_ids[0] if portfolio.resource_set_ids else None
        ),
        field_diff={
            "selected_direction_id": selected.direction_id,
            "selected_primary_track_id": selected.primary_track_id,
            "comparison_frame": selected.comparison_frame,
            "academic_concepts": selected.academic_concepts,
            "operational_definition": selected.operational_definition,
        },
    )
    context.repository.save_scope_contract(contract)
    existing = [
        item
        for item in context.repository.list_gates(context.study_id)
        if item.gate_type is GateType.SCOPE_APPROVAL
        and item.subject_version == contract.version
    ]
    gate = (
        existing[0]
        if existing
        else context.repository.create_gate(
            context.study_id,
            GateType.SCOPE_APPROVAL,
            "scope_contract",
            f"{context.study_id}:scope-v{contract.version}",
            subject_version=contract.version,
        )
    )
    return {
        "scope_contract": contract.model_dump(mode="json"),
        "selected_direction_id": selected.direction_id,
        "gate": gate.model_dump(mode="json"),
    }


def _freeze_scope_contract(context: StepContext) -> dict[str, Any]:
    # The owner approval handler may enrich the draft after scope_drafting.
    # Freeze the repository's latest draft instead of replaying the stale step
    # output and silently discarding approved semantic fields.
    draft = context.repository.latest_scope_contract(context.study_id)
    if draft is None:
        raise BlockedStepError(
            "No Scope draft is available to freeze.",
            kind="missing_scope_draft",
        )
    frozen = context.repository.save_scope_contract(
        draft.model_copy(
            update={
                "status": ArtifactStatus.FROZEN,
                "frozen_at": utc_now(),
            }
        )
    )
    return {"scope_contract": frozen.model_dump(mode="json")}


def stage_one_handlers() -> dict[str, StepHandler]:
    handlers: dict[str, StepHandler] = {
        "project_scan": _project_scan,
        "author_claim_extraction": _author_claim_extraction,
        "candidate_discovery": _candidate_discovery,
        "academic_concept_normalization": _academic_concept_normalization,
        "project_fingerprint": _fingerprint,
        "draft_discovery_query_plan": _draft_discovery_query_plan,
        "evaluate_network_policy": _evaluate_discovery_policy,
        "sanitize_discovery_queries": _sanitize_discovery_queries,
        "execute_discovery_retrieval": _execute_discovery_retrieval,
        "normalize_external_resources": _normalize_external_resources,
        "deduplicate_external_resources": _deduplicate_external_resources,
        "verify_external_metadata": _verify_external_metadata,
        "build_discovery_source_set": _build_discovery_source_set,
        "freeze_discovery_source_set": _freeze_discovery_source_set,
        "claim_recommendation": _recommendation,
        "evidence_chain_detection": _evidence_chain_detection,
        "discovery_portfolio": _discovery_portfolio,
        "scope_drafting": _scope_drafting,
        "freeze_scope_contract": _freeze_scope_contract,
    }
    handlers.update(external_research_handlers())
    for step_type in UNCONNECTED_RETRIEVAL_STEPS:
        handlers.setdefault(step_type, _unconnected_retrieval_handler)
    return handlers


def workflow_handlers() -> dict[str, StepHandler]:
    """Return every connected Workflow v2 handler.

    The lazy import keeps the scheduler primitives usable by the Stage 2 module
    without introducing an import cycle.
    """

    from .stage_two import stage_two_handlers
    from .stage_three import stage_three_handlers
    from .stage_four import stage_four_handlers

    handlers = stage_one_handlers()
    handlers.update(stage_two_handlers())
    handlers.update(stage_three_handlers())
    handlers.update(stage_four_handlers())
    return handlers


def _unconnected_retrieval_handler(context: StepContext) -> dict[str, Any]:
    raise BlockedStepError(
        (
            f"{context.step.step_type} has a domain contract and policy profile "
            "but its phase business adapter is not connected yet"
        ),
        kind="not_implemented",
    )


def create_project_discovery_study(
    repository: WorkflowRepository,
    source_root: str | Path,
    *,
    title: str | None = None,
    include_external: bool = True,
    identity: str | None = None,
    entry_mode: EntryMode = EntryMode.PROJECT_TO_PAPER,
) -> tuple[str, str]:
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"project bundle directory not found: {root}")
    for definition in retrieval_step_definitions():
        repository.save_step_definition(definition)
    suffix = identity or root.as_posix()
    project = repository.create_project(
        title or root.name,
        source_root=str(root),
        project_id=stable_id("project", root.as_posix()),
    )
    study = repository.create_study(
        project.project_id,
        title or f"{root.name} discovery",
        entry_mode=entry_mode,
        study_id=stable_id("study", project.project_id, "discovery", suffix),
        settings={"include_external_discovery": include_external},
    )
    existing_steps = repository.list_steps(study.study_id)
    if existing_steps:
        by_type = {item.step_type: item for item in existing_steps}
        if (
            "discovery_portfolio" not in by_type
            and {
                "claim_recommendation",
                "evidence_chain_detection",
                "build_discovery_source_set",
                "draft_discovery_query_plan",
            }.issubset(by_type)
        ):
            repository.add_step(
                study.study_id,
                "discovery_portfolio",
                Phase.DISCOVERY,
                ExecutorType.DETERMINISTIC_SERVICE,
                depends_on=[
                    by_type["claim_recommendation"].step_instance_id,
                    by_type["evidence_chain_detection"].step_instance_id,
                    by_type["build_discovery_source_set"].step_instance_id,
                    by_type["draft_discovery_query_plan"].step_instance_id,
                ],
                expected_output=(
                    "Compatibility-generated Discovery Portfolio for a pre-portfolio Study"
                ),
            )
        if (
            "freeze_scope_contract" not in by_type
            and {"scope_review", "scope_drafting"}.issubset(by_type)
        ):
            repository.add_step(
                study.study_id,
                "freeze_scope_contract",
                Phase.DISCOVERY,
                ExecutorType.DETERMINISTIC_SERVICE,
                depends_on=[
                    by_type["scope_review"].step_instance_id,
                    by_type["scope_drafting"].step_instance_id,
                ],
                expected_output="Owner-approved frozen Scope Contract v1",
            )
        return project.project_id, study.study_id

    scan = repository.add_step(
        study.study_id,
        "project_scan",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        expected_output="Read-only resource inventory",
    )
    claims = repository.add_step(
        study.study_id,
        "author_claim_extraction",
        Phase.DISCOVERY,
        ExecutorType.CODEX,
        depends_on=[scan.step_instance_id],
        task_group="project_discovery",
    )
    candidates = repository.add_step(
        study.study_id,
        "candidate_discovery",
        Phase.DISCOVERY,
        ExecutorType.MODEL,
        depends_on=[scan.step_instance_id],
        task_group="project_discovery",
    )
    academic_normalization = repository.add_step(
        study.study_id,
        "academic_concept_normalization",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[
            scan.step_instance_id,
            claims.step_instance_id,
            candidates.step_instance_id,
        ],
        expected_output=(
            "Internal labels, operational definitions, scholarly concepts, "
            "academic titles, and query terms"
        ),
    )
    fingerprint = repository.add_step(
        study.study_id,
        "project_fingerprint",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[
            claims.step_instance_id,
            candidates.step_instance_id,
            academic_normalization.step_instance_id,
        ],
    )
    query_plan = repository.add_step(
        study.study_id,
        "draft_discovery_query_plan",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[fingerprint.step_instance_id],
        task_group="retrieval_gateway",
    )
    policy = repository.add_step(
        study.study_id,
        "evaluate_network_policy",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[query_plan.step_instance_id],
        task_group="retrieval_gateway",
    )
    sanitize = repository.add_step(
        study.study_id,
        "sanitize_discovery_queries",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[query_plan.step_instance_id],
        task_group="retrieval_gateway",
    )
    execute_retrieval = repository.add_step(
        study.study_id,
        "execute_discovery_retrieval",
        Phase.DISCOVERY,
        ExecutorType.RETRIEVAL_SERVICE,
        depends_on=[policy.step_instance_id, sanitize.step_instance_id],
        task_group="retrieval_gateway",
    )
    normalize = repository.add_step(
        study.study_id,
        "normalize_external_resources",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[execute_retrieval.step_instance_id],
        task_group="retrieval_gateway",
    )
    deduplicate = repository.add_step(
        study.study_id,
        "deduplicate_external_resources",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[normalize.step_instance_id],
        task_group="retrieval_gateway",
    )
    verify = repository.add_step(
        study.study_id,
        "verify_external_metadata",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        depends_on=[deduplicate.step_instance_id],
        task_group="retrieval_gateway",
    )
    source_set = repository.add_step(
        study.study_id,
        "build_discovery_source_set",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[verify.step_instance_id],
        task_group="retrieval_gateway",
    )
    recommendation = repository.add_step(
        study.study_id,
        "claim_recommendation",
        Phase.DISCOVERY,
        ExecutorType.MODEL,
        depends_on=[
            fingerprint.step_instance_id,
            normalize.step_instance_id,
            execute_retrieval.step_instance_id,
        ],
    )
    evidence = repository.add_step(
        study.study_id,
        "evidence_chain_detection",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[candidates.step_instance_id],
    )
    portfolio = repository.add_step(
        study.study_id,
        "discovery_portfolio",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[
            recommendation.step_instance_id,
            evidence.step_instance_id,
            source_set.step_instance_id,
            query_plan.step_instance_id,
        ],
        expected_output=(
            "Candidate-specific Claim–Source matches and comparable research directions"
        ),
    )
    scope = repository.add_step(
        study.study_id,
        "scope_drafting",
        Phase.DISCOVERY,
        ExecutorType.CODEX,
        depends_on=[portfolio.step_instance_id],
    )
    review = repository.add_step(
        study.study_id,
        "scope_review",
        Phase.DISCOVERY,
        ExecutorType.PROJECT_OWNER,
        depends_on=[scope.step_instance_id],
        expected_output="Owner decision on Scope Contract v1",
    )
    frozen_scope = repository.add_step(
        study.study_id,
        "freeze_scope_contract",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[review.step_instance_id, scope.step_instance_id],
        expected_output="Owner-approved frozen Scope Contract v1",
    )
    repository.add_step(
        study.study_id,
        "freeze_discovery_source_set",
        Phase.DISCOVERY,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[frozen_scope.step_instance_id, source_set.step_instance_id],
        task_group="retrieval_gateway",
        expected_output="Frozen discovery ResourceSet or explicit evidence gap",
    )
    return project.project_id, study.study_id


def audit_discovery_study(
    repository: WorkflowRepository, study_id: str
) -> list[WorkflowDiagnostic]:
    """Find deterministic Stage-1 output defects without changing history."""

    steps = {item.step_type: item for item in repository.list_steps(study_id)}
    claims_step = steps.get("author_claim_extraction")
    candidate_step = steps.get("candidate_discovery")
    findings: list[tuple[str, str, str, list[str]]] = []
    claims: list[dict[str, Any]] = []
    if claims_step is not None and claims_step.status is ExecutionStatus.SUCCEEDED:
        payload = repository.load_step_result(study_id, claims_step.step_instance_id)
        claims = payload.get("author_claims", [])
    procedural = [
        str(item.get("statement", ""))
        for item in claims
        if _is_procedural_statement(str(item.get("statement", "")))
    ]
    if procedural:
        findings.append(
            (
                "procedural_claim_false_positive",
                (
                    f"{len(procedural)} process instruction(s) were emitted as "
                    "author research claims."
                ),
                "author_claim_extraction",
                claims_step.output_artifact_ids if claims_step else [],
            )
        )
    seen: set[tuple[str, str]] = set()
    duplicates = 0
    for item in claims:
        spans = item.get("source_spans") or [{}]
        key = (
            str(item.get("statement", "")).strip().casefold(),
            str(spans[0].get("path", "")).strip().casefold(),
        )
        if key in seen:
            duplicates += 1
        seen.add(key)
    if duplicates:
        findings.append(
            (
                "duplicate_author_claim",
                f"{duplicates} duplicate author claim(s) were emitted.",
                "author_claim_extraction",
                claims_step.output_artifact_ids if claims_step else [],
            )
        )
    if (
        candidate_step is not None
        and candidate_step.status is ExecutionStatus.SUCCEEDED
    ):
        candidate_payload = repository.load_step_result(
            study_id, candidate_step.step_instance_id
        )
        contaminated_paths: set[str] = set()
        for candidate in candidate_payload.get("candidates", []):
            paths = [
                candidate.get("protocol_path"),
                candidate.get("output_path"),
                candidate.get("report_path"),
                *candidate.get("implementation_paths", []),
                *candidate.get("test_paths", []),
            ]
            contaminated_paths.update(
                str(path)
                for path in paths
                if path and is_excluded_bundle_path(str(path))
            )
        if contaminated_paths:
            findings.append(
                (
                    "cache_boundary_contamination",
                    (
                        "Candidate discovery used excluded cache content: "
                        + ", ".join(sorted(contaminated_paths)[:10])
                    ),
                    "project_scan",
                    candidate_step.output_artifact_ids,
                )
            )

    existing_codes = {
        item.failure_code for item in repository.list_diagnostics(study_id)
    }
    diagnostics: list[WorkflowDiagnostic] = []
    for failure_code, rationale, earliest_step_type, evidence_ids in findings:
        if failure_code in existing_codes:
            continue
        diagnostic = WorkflowDiagnostic(
            diagnostic_id=stable_id(
                "diagnostic", study_id, failure_code, earliest_step_type
            ),
            study_id=study_id,
            phase=Phase.DISCOVERY,
            earliest_affected_step_type=earliest_step_type,
            diagnostic_owner=DiagnosticOwner.EVIDENCE_PACKAGING,
            failure_code=failure_code,
            rationale=rationale,
            evidence_artifact_ids=evidence_ids,
            scientific_change=False,
            auto_repair_eligible=True,
        )
        diagnostics.append(repository.save_diagnostic(diagnostic))
    return diagnostics


def _step_descendants(
    steps: list[StepInstance], root_step_type: str
) -> set[str]:
    roots = {
        item.step_instance_id for item in steps if item.step_type == root_step_type
    }
    if not roots:
        raise ValueError(f"unknown earliest affected step: {root_step_type}")
    affected = set(roots)
    while True:
        additions = {
            item.step_instance_id
            for item in steps
            if set(item.depends_on).intersection(affected)
        }
        if additions.issubset(affected):
            return affected
        affected.update(additions)


def _clone_reusable_discovery_steps(
    repository: WorkflowRepository,
    predecessor_study_id: str,
    successor_study_id: str,
    affected_step_ids: set[str],
) -> list[str]:
    """Bind immutable predecessor outputs into a successor Study."""

    old_steps = repository.list_steps(predecessor_study_id)
    old_by_type = {item.step_type: item for item in old_steps}
    new_by_type = {
        item.step_type: item for item in repository.list_steps(successor_study_id)
    }
    reusable_types = {
        item.step_type
        for item in old_steps
        if item.step_instance_id not in affected_step_ids
        and item.status is ExecutionStatus.SUCCEEDED
        and item.step_type in new_by_type
    }
    cloned: set[str] = set()
    while reusable_types.difference(cloned):
        progressed = False
        for step_type in sorted(reusable_types.difference(cloned)):
            old_step = old_by_type[step_type]
            old_dependency_types = {
                repository.load_step(predecessor_study_id, dependency).step_type
                for dependency in old_step.depends_on
            }
            if not old_dependency_types.intersection(reusable_types).issubset(cloned):
                continue
            new_step = new_by_type[step_type]
            result = repository.load_step_result(
                predecessor_study_id, old_step.step_instance_id
            )
            result_artifact = repository.save_step_result(
                successor_study_id,
                new_step.step_instance_id,
                result,
                predecessor_artifact_id=(
                    old_step.output_artifact_ids[0]
                    if old_step.output_artifact_ids
                    else None
                ),
            )
            extra_output_ids: list[str] = []
            for old_artifact_id in old_step.output_artifact_ids[1:]:
                old_artifact = next(
                    item
                    for item in repository.list_artifacts(predecessor_study_id)
                    if item.artifact_id == old_artifact_id
                )
                rebound = repository.register_artifact(
                    successor_study_id,
                    old_artifact.path,
                    old_artifact.sha256,
                    kind=old_artifact.kind,
                    role=old_artifact.role,
                    status=old_artifact.status,
                    version=old_artifact.version,
                    predecessor_artifact_id=old_artifact.artifact_id,
                )
                extra_output_ids.append(rebound.artifact_id)
            input_ids: list[str] = []
            for dependency_id in new_step.depends_on:
                dependency = repository.load_step(successor_study_id, dependency_id)
                input_ids.extend(dependency.output_artifact_ids)
                for input_artifact_id in dependency.output_artifact_ids:
                    repository.add_dependency(
                        successor_study_id,
                        input_artifact_id,
                        result_artifact.artifact_id,
                        relation="successor_reuses_step_dependency",
                    )
            repository.update_step(
                successor_study_id,
                new_step.step_instance_id,
                ExecutionStatus.SUCCEEDED,
                input_artifact_ids=input_ids,
                output_artifact_ids=[
                    result_artifact.artifact_id,
                    *extra_output_ids,
                ],
            )
            cloned.add(step_type)
            progressed = True
        if not progressed:
            raise ValueError("reusable discovery steps could not be topologically cloned")
    return sorted(cloned)


def execute_discovery_repair(
    repository: WorkflowRepository,
    repair: RepairContract,
    *,
    decided_by: str = "automatic_safe_repair",
) -> dict[str, Any]:
    """Create and run an append-only successor for a bounded Stage-1 repair."""

    if repair.scientific_change and not repair.approved_by:
        raise ValueError("scientific repairs require owner approval")
    if repair.successor_study_id:
        return {
            "repair": repair.model_dump(mode="json"),
            "workflow": repository.snapshot(repair.successor_study_id),
            "reused_step_types": [],
        }
    if not repair.earliest_affected_step_type:
        raise ValueError("repair contract has no earliest affected step")

    predecessor = repository.load_study(repair.study_id)
    project = repository.load_project(predecessor.project_id)
    if not project.source_root:
        raise ValueError("discovery repair requires the original project bundle")
    repairing = repair.model_copy(
        update={"status": RepairStatus.REPAIRING, "approved_by": decided_by}
    )
    repository.save_repair_contract(repairing)

    _, successor_study_id = create_project_discovery_study(
        repository,
        project.source_root,
        title=f"{predecessor.title} · repair v{repair.version}",
        include_external=bool(
            predecessor.settings.get("include_external_discovery", True)
        ),
        identity=f"successor:{repair.repair_id}",
        entry_mode=predecessor.entry_mode,
    )
    successor = repository.load_study(successor_study_id)
    repository.save_study(
        successor.model_copy(
            update={
                "predecessor_study_id": predecessor.study_id,
                "settings": {
                    **successor.settings,
                    "repair_contract_id": repair.repair_id,
                    "discovery_repair_actions": [
                        str(item.get("failure_code", ""))
                        for item in repair.regression_checks
                        if item.get("failure_code")
                    ],
                },
            }
        ),
        "successor_study_linked",
    )
    affected = _step_descendants(
        repository.list_steps(predecessor.study_id),
        repair.earliest_affected_step_type,
    )
    reused = _clone_reusable_discovery_steps(
        repository, predecessor.study_id, successor_study_id, affected
    )
    workflow = PersistentDAGScheduler(
        repository, stage_one_handlers(), recover_interrupted=True
    ).run(successor_study_id)
    post_diagnostics = audit_discovery_study(repository, successor_study_id)
    failed_codes = {
        item.failure_code
        for item in post_diagnostics
        if item.failure_code
        in {
            str(check.get("failure_code", ""))
            for check in repair.regression_checks
        }
    }
    final_status = (
        RepairStatus.DIAGNOSING if failed_codes else RepairStatus.COMPLETED
    )
    completed = repairing.model_copy(
        update={
            "status": final_status,
            "successor_study_id": successor_study_id,
        }
    )
    repository.save_repair_contract(completed)
    repository.save_study(
        predecessor.model_copy(
            update={
                "successor_study_id": successor_study_id,
                "lifecycle": (
                    StudyLifecycle.SUPERSEDED
                    if final_status is RepairStatus.COMPLETED
                    else predecessor.lifecycle
                ),
            }
        ),
        "study_successor_created",
    )
    return {
        "repair": completed.model_dump(mode="json"),
        "successor_study_id": successor_study_id,
        "reused_step_types": reused,
        "regression_passed": not failed_codes,
        "workflow": workflow,
    }


def auto_repair_discovery_study(
    repository: WorkflowRepository, study_id: str
) -> dict[str, Any] | None:
    """Automatically repair only deterministic, non-scientific Stage-1 defects."""

    audit_discovery_study(repository, study_id)
    diagnostics = repository.list_diagnostics(study_id)
    handled_diagnostic_ids = {
        item.diagnostic_id
        for item in repository.list_repair_contracts(study_id)
        if item.diagnostic_id
    }
    eligible = [
        item
        for item in diagnostics
        if item.auto_repair_eligible and not item.scientific_change
        and item.diagnostic_id not in handled_diagnostic_ids
    ]
    if not eligible:
        return None
    earliest = eligible[0].earliest_affected_step_type
    step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == earliest
    )
    repair = repository.propose_repair(
        study_id,
        diagnostic_owner=eligible[0].diagnostic_owner,
        scientific_change=False,
        earliest_affected_phase=Phase.DISCOVERY,
        changed_artifact_ids=step.output_artifact_ids,
        regression_checks=[
            {
                "failure_code": item.failure_code,
                "expectation": "not_detected_in_successor",
            }
            for item in eligible
        ],
        diagnostic_id=eligible[0].diagnostic_id,
        earliest_affected_step_type=earliest,
    )
    return execute_discovery_repair(repository, repair)


def run_project_discovery(
    source_root: str | Path,
    *,
    repository_root: str | Path,
    title: str | None = None,
    include_external: bool = True,
    identity: str | None = None,
    auto_repair: bool = True,
    entry_mode: EntryMode = EntryMode.PROJECT_TO_PAPER,
) -> dict[str, Any]:
    repository = WorkflowRepository(repository_root)
    project_id, study_id = create_project_discovery_study(
        repository,
        source_root,
        title=title,
        include_external=include_external,
        identity=identity,
        entry_mode=entry_mode,
    )
    visited: set[str] = set()
    while study_id not in visited:
        visited.add(study_id)
        successor_id = repository.load_study(study_id).successor_study_id
        if not successor_id:
            break
        study_id = successor_id
    snapshot = PersistentDAGScheduler(
        repository, stage_one_handlers(), recover_interrupted=True
    ).run(study_id)
    repair_outcome = (
        auto_repair_discovery_study(repository, study_id) if auto_repair else None
    )
    if repair_outcome is not None:
        study_id = str(repair_outcome["successor_study_id"])
        snapshot = repair_outcome["workflow"]
    scan_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "project_scan"
    )
    candidate_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "candidate_discovery"
    )
    recommendation_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "claim_recommendation"
    )
    portfolio_step = next(
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "discovery_portfolio"
    )
    if any(
        item.status is not ExecutionStatus.SUCCEEDED
        for item in (
            scan_step,
            candidate_step,
            recommendation_step,
            portfolio_step,
        )
    ):
        return {"project_id": project_id, "study_id": study_id, "workflow": snapshot}
    scan = repository.load_step_result(study_id, scan_step.step_instance_id)
    candidates_payload = repository.load_step_result(
        study_id, candidate_step.step_instance_id
    )["candidates"]
    claim_discovery = repository.load_step_result(
        study_id, recommendation_step.step_instance_id
    )["claim_discovery"]
    discovery_portfolio = repository.load_step_result(
        study_id, portfolio_step.step_instance_id
    )["discovery_portfolio"]
    candidates = _models(candidates_payload, NoveltyCandidate)
    inspection = BundleInspection(
        source_root=scan["source_root"],
        resource_count=scan["resource_count"],
        excluded_count=scan["excluded_count"],
        candidates=candidates,
        recommended_track_id=candidates[0].track_id if candidates else None,
        hdf5_metadata=_models(
            scan.get("hdf5_metadata", []), HDF5MetadataResource
        ),
        claim_discovery=ClaimDiscoveryReport.model_validate(claim_discovery),
        discovery_portfolio=DiscoveryPortfolio.model_validate(
            discovery_portfolio
        ),
    )
    return {
        **inspection.model_dump(mode="json"),
        "project_id": project_id,
        "study_id": study_id,
        "workflow": snapshot,
        "automatic_repair": repair_outcome,
    }


def approve_discovery_direction(
    repository: WorkflowRepository,
    study_id: str,
    direction_id: str,
    *,
    decided_by: str = "project_owner",
    reason: str | None = None,
    scope_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select one portfolio direction, approve Scope v1, and resume the DAG."""

    portfolio_step = next(
        (
            item
            for item in repository.list_steps(study_id)
            if item.step_type == "discovery_portfolio"
            and item.status is ExecutionStatus.SUCCEEDED
        ),
        None,
    )
    if portfolio_step is None:
        raise ValueError("the Study has no completed Discovery Portfolio")
    portfolio = DiscoveryPortfolio.model_validate(
        repository.load_step_result(
            study_id, portfolio_step.step_instance_id
        )["discovery_portfolio"]
    )
    selected = next(
        (item for item in portfolio.directions if item.direction_id == direction_id),
        None,
    )
    if selected is None:
        raise ValueError("direction_id does not belong to the Discovery Portfolio")

    current = repository.latest_scope_contract(study_id)
    if current is None:
        raise ValueError("the Study has no draft Scope Contract")
    if current.status is ArtifactStatus.FROZEN:
        selected_id = str(current.field_diff.get("selected_direction_id", ""))
        if selected_id != direction_id:
            raise ValueError("a different frozen Scope requires Scope vNext")
        return {
            "scope_contract": current.model_dump(mode="json"),
            "workflow": repository.snapshot(study_id),
        }

    overrides = dict(scope_overrides or {})
    allowed_override_fields = {
        "direction",
        "research_question",
        "scope_in",
        "scope_out",
        "candidate_contribution",
    }
    unknown_override_fields = set(overrides).difference(allowed_override_fields)
    if unknown_override_fields:
        raise ValueError(
            "unsupported Scope override fields: "
            + ", ".join(sorted(unknown_override_fields))
        )
    for field_name in ("direction", "research_question", "candidate_contribution"):
        if field_name in overrides:
            value = str(overrides[field_name]).strip()
            if not value:
                raise ValueError(f"{field_name} cannot be empty")
            overrides[field_name] = value
    for field_name in ("scope_in", "scope_out"):
        if field_name in overrides:
            value = overrides[field_name]
            if isinstance(value, str):
                value = [
                    item.strip()
                    for item in value.replace("\r", "\n").split("\n")
                    if item.strip()
                ]
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item.strip() for item in value
            ):
                raise ValueError(f"{field_name} must contain non-empty text items")
            overrides[field_name] = [item.strip() for item in value]

    selected_values = {
        "direction": selected.title[:300],
        "research_question": selected.research_question[:1200],
        "scope_in": selected.scope_in,
        "scope_out": selected.scope_out,
        "candidate_contribution": selected.candidate_contribution[:2000],
    }
    selected_values.update(overrides)
    updated = current.model_copy(
        update={
            **selected_values,
            "project_resource_ids": selected.local_evidence_paths,
            "literature_set_id": (
                portfolio.resource_set_ids[0]
                if portfolio.resource_set_ids
                else None
            ),
            "field_diff": {
                **current.field_diff,
                "selected_direction_id": selected.direction_id,
                "selected_primary_track_id": selected.primary_track_id,
                "comparison_frame": selected.comparison_frame,
                "academic_concepts": selected.academic_concepts,
                "operational_definition": selected.operational_definition,
                "owner_scope_overrides": overrides,
                "owner_revision_reason": reason,
            },
            "created_by": decided_by,
        }
    )
    repository.save_scope_contract(updated)
    gate = next(
        (
            item
            for item in repository.list_gates(study_id)
            if item.gate_type is GateType.SCOPE_APPROVAL
            and item.subject_version == updated.version
        ),
        None,
    )
    if gate is None:
        raise ValueError("the Study has no Scope approval Gate")
    decided = repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by=decided_by,
        reason=reason or f"Selected Discovery direction {direction_id}.",
    )
    from .stage_two import ensure_stage_two_dag

    PersistentDAGScheduler(repository, stage_one_handlers()).run(study_id)
    ensure_stage_two_dag(repository, study_id)
    workflow = PersistentDAGScheduler(repository, workflow_handlers()).run(study_id)
    frozen = repository.latest_scope_contract(study_id)
    return {
        "scope_contract": (
            frozen.model_dump(mode="json") if frozen is not None else None
        ),
        "gate": decided.model_dump(mode="json"),
        "workflow": workflow,
    }
