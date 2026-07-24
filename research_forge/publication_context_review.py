"""Append-only post-unblinding review of evaluator-unsupported publication claims.

This review is deliberately diagnostic.  It restores frozen task-specification
context that was not present in the blinded human-audit packet, but it never
overwrites or unlocks the preregistered primary analysis.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .models import utc_now
from .publication_manual_audit import _candidate_inventory
from .storage import read_json, sha256_file, write_json_atomic
from .study_models import StudyClaim


LETTER_TO_VERDICT = {"A": "supported", "B": "unsupported", "C": "abstain"}


def _review_path(project: Path) -> Path:
    return (
        project
        / "stage2"
        / "protected_nli_evaluation"
        / "manual-audit"
        / "context-restored-review"
        / "result.json"
    )


def _reviewer_hash(reviewer_id: str) -> str:
    value = reviewer_id.strip()
    if not value:
        raise ValueError("context reviewer id must not be blank")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def record_publication_context_review(
    project: Path,
    *,
    verdicts: str,
    reviewer_id: str,
) -> dict[str, Any]:
    """Freeze a diagnostic re-review after restoring the task specification.

    ``verdicts`` follows the deterministic order of evaluator-unsupported
    candidates in the protected candidate inventory.  This artifact is
    explicitly post hoc and cannot replace the original blinded audit.
    """

    project = project.resolve()
    normalized = "".join(verdicts.upper().split())
    if any(letter not in LETTER_TO_VERDICT for letter in normalized):
        raise ValueError("context-review verdicts must contain only A, B, or C")

    protocol_path = project / "stage2" / "protocol.json"
    protected = project / "stage2" / "protected_nli_evaluation"
    summary_path = protected / "summary.json"
    manual_result_path = protected / "manual-audit" / "result.json"
    if not all(path.is_file() for path in (protocol_path, summary_path, manual_result_path)):
        raise FileNotFoundError("context review requires the frozen protocol and completed human audit")

    protocol = read_json(protocol_path)
    tasks = {str(item["task_id"]): dict(item) for item in protocol["tasks"]}
    candidates = [
        item
        for item in _candidate_inventory(project, protocol)
        if item["evaluator_class"] == "unsupported"
    ]
    if len(normalized) != len(candidates):
        raise ValueError(
            f"context review requires {len(candidates)} verdicts in frozen candidate order"
        )
    manual_result = read_json(manual_result_path)
    original = {
        str(item["audit_id"]): dict(item) for item in manual_result["final_decisions"]
    }

    items: list[dict[str, Any]] = []
    for candidate, letter in zip(candidates, normalized, strict=True):
        task = tasks[str(candidate["task_id"])]
        evidence = dict(candidate["linked_evidence"])
        experiment = dict(evidence.get("linked_experiment_evidence") or {})
        items.append(
            {
                "audit_id": candidate["audit_id"],
                "task_id": candidate["task_id"],
                "arm": candidate["arm"],
                "claim_id": candidate["claim_id"],
                "claim_text": candidate["claim_text"],
                "automated_verdict": "unsupported",
                "original_blinded_human_verdict": original[candidate["audit_id"]]["verdict"],
                "restored_context": {
                    "primary_metric": task["primary_metric"],
                    "direction": task["direction"],
                    "baseline_score": task["baseline_score"],
                    "target_score": task["target_score"],
                    "task_hash": task["task_hash"],
                    "aggregate_metrics": experiment.get("aggregate_metrics"),
                    "run_valid": experiment.get("valid"),
                    "isolation_verified": experiment.get("isolation_verified"),
                    "evidence_verdict": experiment.get("verdict"),
                    "improvement": experiment.get("improvement"),
                },
                "contextual_verdict_letter": letter,
                "contextual_verdict": LETTER_TO_VERDICT[letter],
                "rationale": (
                    "The frozen task specification and the linked valid experiment record were "
                    "reviewed together after unblinding; the contextual verdict is diagnostic "
                    "and does not replace the preregistered blinded judgment."
                ),
            }
        )

    result = {
        "schema_version": 1,
        "protocol_id": protocol["protocol_id"],
        "reviewed_at": utc_now(),
        "review_type": "post_unblinding_context_restored_diagnostic",
        "reviewer_role": "project_owner_contextual_reviewer",
        "reviewer_id_sha256": _reviewer_hash(reviewer_id),
        "arm_blinded": False,
        "task_blinded": False,
        "evaluator_verdict_blinded": False,
        "append_only": True,
        "replaces_preregistered_blinded_audit": False,
        "can_unlock_primary_analysis": False,
        "source_protocol_sha256": sha256_file(protocol_path),
        "source_protected_summary_sha256": sha256_file(summary_path),
        "source_manual_audit_result_sha256": sha256_file(manual_result_path),
        "reviewed_evaluator_unsupported": len(items),
        "contextual_verdict_letters": normalized,
        "contextual_verdict_counts": {
            verdict: sum(item["contextual_verdict"] == verdict for item in items)
            for verdict in ("supported", "unsupported", "abstain")
        },
        "items": items,
        "interpretation": (
            "This post-unblinding review tests whether omitted task-specification context or "
            "deterministic metric binding explains the protected evaluator's unsupported labels. "
            "It is a fault-localization and repair artifact, not a confirmatory treatment-effect result."
        ),
    }

    path = _review_path(project)
    if path.is_file():
        existing = read_json(path)
        comparable = {key: value for key, value in existing.items() if key != "reviewed_at"}
        proposed = {key: value for key, value in result.items() if key != "reviewed_at"}
        if comparable != proposed:
            raise ValueError("a different context-restored review is already frozen")
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, result)
    return result


def audit_publication_context_review(project: Path) -> dict[str, Any]:
    """Verify bindings and non-overwrite boundaries of the contextual review."""

    project = project.resolve()
    path = _review_path(project)
    if not path.is_file():
        return {"passed": False, "present": False, "violations": ["context review is missing"]}
    result = read_json(path)
    protocol = project / "stage2" / "protocol.json"
    protected = project / "stage2" / "protected_nli_evaluation"
    summary = protected / "summary.json"
    manual = protected / "manual-audit" / "result.json"
    checks = {
        "protocol_bound": result.get("source_protocol_sha256") == sha256_file(protocol),
        "protected_summary_bound": result.get("source_protected_summary_sha256")
        == sha256_file(summary),
        "manual_audit_bound": result.get("source_manual_audit_result_sha256")
        == sha256_file(manual),
        "diagnostic_only": result.get("can_unlock_primary_analysis") is False,
        "original_audit_not_replaced": result.get("replaces_preregistered_blinded_audit") is False,
        "all_items_present": len(result.get("items", []))
        == int(result.get("reviewed_evaluator_unsupported", -1)),
    }
    return {
        "schema_version": 1,
        "passed": all(checks.values()),
        "present": True,
        "review_sha256": sha256_file(path),
        "checks": checks,
        "violations": [name for name, passed in checks.items() if not passed],
    }


def verify_publication_context_repair(project: Path) -> dict[str, Any]:
    """Replay the five diagnosed cases against the successor evaluator rules."""

    from .publication_nli_evaluation import (
        IMPLEMENTATION_VERSION,
        _deterministic_experiment_support,
    )

    project = project.resolve()
    context_path = _review_path(project)
    if not context_path.is_file():
        raise FileNotFoundError("context-restored review must be frozen before repair verification")
    context = read_json(context_path)
    protocol_path = project / "stage2" / "protocol.json"
    protocol = read_json(protocol_path)
    candidates = {
        str(item["audit_id"]): item
        for item in _candidate_inventory(project, protocol)
        if item["evaluator_class"] == "unsupported"
    }
    expected_ids = [str(item["audit_id"]) for item in context["items"]]
    if set(expected_ids) != set(candidates):
        raise ValueError("repair replay candidates do not match the frozen contextual review")

    replay: list[dict[str, Any]] = []
    for audit_id in expected_ids:
        item = candidates[audit_id]
        evidence = dict(item["linked_evidence"])
        claim = StudyClaim.model_validate(
            {
                "claim_id": item["claim_id"],
                "run_id": "context-repair-replay",
                "arm": item["arm"],
                "task_pack": item["task_id"],
                "seed": 0,
                "claim_type": item["claim_type"],
                "claim_text": item["claim_text"],
                "source_ids": evidence.get("linked_source_ids", []),
                "experiment_run_id": "context-repair-evidence",
                "metric_values": evidence.get("declared_metric_values", {}),
                "artifact_paths": evidence.get("declared_artifact_paths", []),
            }
        )
        rationale = _deterministic_experiment_support(claim, evidence)
        replay.append(
            {
                "audit_id": audit_id,
                "expected_contextual_verdict": "supported",
                "successor_verdict": "supported" if rationale is not None else "not_deterministic",
                "decision_source": "deterministic_numeric_entailment" if rationale else None,
                "rationale": rationale,
                "task_specification_bound": isinstance(
                    evidence.get("linked_task_specification"), dict
                ),
            }
        )

    source_root = Path(__file__).resolve().parent
    source_paths = {
        "study_runner.py": source_root / "study_runner.py",
        "counterfactual_rebranch.py": source_root / "counterfactual_rebranch.py",
        "publication_nli_evaluation.py": source_root / "publication_nli_evaluation.py",
    }
    passed = all(
        item["successor_verdict"] == "supported"
        and item["task_specification_bound"] is True
        for item in replay
    )
    result = {
        "schema_version": 1,
        "verified_at": utc_now(),
        "protocol_id": context["protocol_id"],
        "verification_scope": "implementation_repair_on_historical_diagnostic_cases",
        "status": (
            "implementation_verified_pending_successor_protocol"
            if passed
            else "implementation_repair_failed"
        ),
        "passed": passed,
        "does_not_recompute_historical_primary_analysis": True,
        "source_context_review_sha256": sha256_file(context_path),
        "source_protocol_sha256": sha256_file(protocol_path),
        "successor_evaluator_implementation_version": IMPLEMENTATION_VERSION,
        "source_code_sha256": {
            name: sha256_file(path) for name, path in source_paths.items()
        },
        "replayed_cases": len(replay),
        "supported_cases": sum(item["successor_verdict"] == "supported" for item in replay),
        "replay": replay,
        "next_required_state": (
            "Freeze a successor protocol with these source hashes, run a fresh protected evaluation, "
            "and complete a new blinded human audit."
        ),
    }
    output = project / "design_revisions" / "publication_context_repair_verification.json"
    write_json_atomic(output, result)
    return result


def _successor_plan_path(project: Path) -> Path:
    return project / "design_revisions" / "publication_successor_protocol_plan_v1.json"


def _successor_slug(predecessor_slug: str) -> str:
    if predecessor_slug.endswith("-v1"):
        return predecessor_slug[:-3] + "-v2"
    return predecessor_slug + "-successor-v1"


def prepare_publication_successor_protocol_plan(project: Path) -> dict[str, Any]:
    """Freeze the prospective design contract needed after the context repair.

    This is deliberately a *plan*, not an executed or outcome-bearing protocol.
    It binds the repaired evaluator implementation, requires a clean successor
    project, and records any still-open pre-freeze prerequisites without
    modifying the predecessor study.
    """

    project = project.resolve()
    context_audit = audit_publication_context_review(project)
    if context_audit.get("passed") is not True:
        raise ValueError("successor planning requires a passing contextual-review audit")

    protocol_path = project / "stage2" / "protocol.json"
    project_path = project / "project.json"
    repair_path = project / "design_revisions" / "publication_context_repair_verification.json"
    if not all(path.is_file() for path in (protocol_path, project_path, repair_path)):
        raise FileNotFoundError(
            "successor planning requires project metadata, the predecessor protocol, and repair verification"
        )
    protocol = read_json(protocol_path)
    metadata = read_json(project_path)
    repair = read_json(repair_path)
    if repair.get("passed") is not True:
        raise ValueError("successor planning requires a passing implementation repair replay")

    source_root = Path(__file__).resolve().parent
    source_paths = {
        "study_runner.py": source_root / "study_runner.py",
        "counterfactual_rebranch.py": source_root / "counterfactual_rebranch.py",
        "publication_nli_evaluation.py": source_root / "publication_nli_evaluation.py",
    }
    current_hashes = {name: sha256_file(path) for name, path in source_paths.items()}
    verified_hashes = dict(repair.get("source_code_sha256") or {})
    hashes_match = current_hashes == verified_hashes
    if not hashes_match:
        raise ValueError(
            "repair-bound source changed; rerun verify-publication-context-repair before successor planning"
        )

    successor_slug = _successor_slug(str(metadata["slug"]))
    proposed_project = project.parent / successor_slug
    calibration = project / "design_revisions" / "independent_calibration_contract.json"
    secondary = project / "design_revisions" / "secondary_evaluator_contract.json"
    novelty = project / "design_revisions" / "novelty_refresh.json"
    prerequisites = {
        "context_review_integrity_passed": True,
        "repair_replay_passed": True,
        "repair_source_hashes_current": True,
        "independent_calibration_contract_available": calibration.is_file(),
        "secondary_evaluator_contract_available": secondary.is_file(),
        "contextual_novelty_refresh_available": novelty.is_file(),
        "clean_successor_project_not_yet_created": not proposed_project.exists(),
        "successor_specific_publication_contract_required": False,
    }
    # The final item is intentionally false: the predecessor's venue contract
    # is path-bound and must never be silently reused for a new formal study.
    ready_to_freeze = all(prerequisites.values())
    tasks = [
        {
            "task_id": item["task_id"],
            "task_hash": item["task_hash"],
            "primary_metric": item["primary_metric"],
            "direction": item["direction"],
            "baseline_score": item["baseline_score"],
            "target_score": item["target_score"],
        }
        for item in protocol["tasks"]
    ]
    plan_seed = "|".join(
        [
            str(protocol["protocol_id"]),
            sha256_file(protocol_path),
            sha256_file(repair_path),
            *[f"{name}:{value}" for name, value in sorted(current_hashes.items())],
        ]
    )
    plan = {
        "schema_version": 1,
        "plan_id": "successor-plan-" + hashlib.sha256(plan_seed.encode("utf-8")).hexdigest()[:16],
        "created_at": utc_now(),
        "status": (
            "ready_to_freeze" if ready_to_freeze else "prefreeze_prerequisites_open"
        ),
        "predecessor": {
            "project_slug": metadata["slug"],
            "protocol_id": protocol["protocol_id"],
            "protocol_sha256": sha256_file(protocol_path),
            "context_review_sha256": context_audit["review_sha256"],
            "repair_verification_sha256": sha256_file(repair_path),
        },
        "successor": {
            "proposed_project_slug": successor_slug,
            "proposed_project_path": str(proposed_project),
            "study_intent": "publication",
            "fresh_outcomes_required": True,
            "predecessor_outcome_reuse_allowed": False,
            "tasks": tasks,
            "seeds": list(protocol["seeds"]),
            "counterfactual_source": "shared_run_artifact",
            "branch_order": "pair_randomized",
            "primary_metric": protocol["primary_metric"],
            "secondary_metrics": list(protocol["secondary_metrics"]),
            "manual_audit": dict(protocol["manual_audit"]),
        },
        "repair_binding": {
            "evaluator_implementation_version": repair[
                "successor_evaluator_implementation_version"
            ],
            "source_code_sha256": current_hashes,
            "required_packet_fields": [
                "linked_task_specification.primary_metric",
                "linked_task_specification.direction",
                "linked_task_specification.baseline_score",
                "linked_task_specification.target_score",
                "linked_task_specification.task_hash",
            ],
            "decision_precedence": (
                "structural_fail_closed_then_deterministic_numeric_entailment_then_semantic_nli"
            ),
            "historical_diagnostic_replay": {
                "replayed_cases": repair["replayed_cases"],
                "supported_cases": repair["supported_cases"],
                "confirmatory_evidence": False,
            },
        },
        "confirmatory_boundary": {
            "freeze_before_outcome_generation": True,
            "new_protected_evaluation_required": True,
            "new_two_auditor_blinded_review_required": True,
            "post_unblinding_AAAAA_may_not_be_used_as_successor_labels": True,
            "historical_primary_analysis_remains_unchanged": True,
        },
        "prefreeze_prerequisites": prerequisites,
        "successor_protocol_ready_to_freeze": ready_to_freeze,
        "next_actions": [
            "Create a clean successor project without copying predecessor outcomes.",
            "Freeze a successor-specific publication experiment contract for the same venue or an explicitly changed venue.",
            "Provide a hash-bound evaluator contract independent from the primary evaluator.",
            "Copy only approved pre-outcome research inputs and then run study freeze under the bound repair hashes.",
        ],
    }
    path = _successor_plan_path(project)
    if path.is_file():
        existing = read_json(path)
        comparable_existing = {k: v for k, v in existing.items() if k != "created_at"}
        comparable_plan = {k: v for k, v in plan.items() if k != "created_at"}
        if comparable_existing != comparable_plan:
            raise ValueError(
                "a different successor protocol plan is already frozen; create an explicit v2 plan"
            )
        return existing
    write_json_atomic(path, plan)
    return plan


def audit_publication_successor_protocol_plan(project: Path) -> dict[str, Any]:
    """Audit a frozen successor plan without treating it as an executed study."""

    project = project.resolve()
    path = _successor_plan_path(project)
    if not path.is_file():
        return {"schema_version": 1, "present": False, "passed": False, "violations": ["successor protocol plan is missing"]}
    plan = read_json(path)
    protocol_path = project / "stage2" / "protocol.json"
    repair_path = project / "design_revisions" / "publication_context_repair_verification.json"
    context_path = _review_path(project)
    repair = read_json(repair_path)
    source_root = Path(__file__).resolve().parent
    current_hashes = {
        name: sha256_file(source_root / name)
        for name in (
            "study_runner.py",
            "counterfactual_rebranch.py",
            "publication_nli_evaluation.py",
        )
    }
    checks = {
        "predecessor_protocol_bound": plan.get("predecessor", {}).get("protocol_sha256")
        == sha256_file(protocol_path),
        "context_review_bound": plan.get("predecessor", {}).get("context_review_sha256")
        == sha256_file(context_path),
        "repair_verification_bound": plan.get("predecessor", {}).get("repair_verification_sha256")
        == sha256_file(repair_path),
        "repair_source_hashes_current": plan.get("repair_binding", {}).get("source_code_sha256")
        == current_hashes
        == repair.get("source_code_sha256"),
        "fresh_outcomes_required": plan.get("successor", {}).get("fresh_outcomes_required") is True,
        "historical_outcomes_forbidden": plan.get("successor", {}).get("predecessor_outcome_reuse_allowed") is False,
        "new_blinded_audit_required": plan.get("confirmatory_boundary", {}).get("new_two_auditor_blinded_review_required") is True,
        "post_hoc_labels_forbidden": plan.get("confirmatory_boundary", {}).get("post_unblinding_AAAAA_may_not_be_used_as_successor_labels") is True,
        "not_misrepresented_as_execution": plan.get("status") in {"ready_to_freeze", "prefreeze_prerequisites_open"},
    }
    return {
        "schema_version": 1,
        "present": True,
        "passed": all(checks.values()),
        "plan_sha256": sha256_file(path),
        "checks": checks,
        "successor_protocol_ready_to_freeze": plan.get("successor_protocol_ready_to_freeze") is True,
        "open_prerequisites": [
            name
            for name, satisfied in plan.get("prefreeze_prerequisites", {}).items()
            if satisfied is not True
        ],
        "violations": [name for name, passed in checks.items() if not passed],
    }
