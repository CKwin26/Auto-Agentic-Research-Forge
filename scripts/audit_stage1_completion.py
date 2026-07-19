from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_forge.literature import audit_stage1
from research_forge.models import (
    LiteratureApprovalRecord,
    LiteratureReviewEnvelope,
    PlanEvidenceBinding,
    ResearchPlanDraft,
    Stage,
    Stage1Manifest,
)
from research_forge.storage import load_state, read_json, safe_relative, sha256_file


REQUIRED_CHECKS = {
    "search_strategy_present",
    "discovery_record_valid",
    "review_schema_valid",
    "semantic_screening_valid",
    "human_approval_present",
    "stage1_artifacts_hash_valid",
    "minimum_verified_papers",
    "included_sources_exist",
    "discovery_bound_to_latest_search_plan",
    "screening_bound_to_discovery",
    "screening_decisions_consistent",
    "approval_bound_to_artifacts",
    "minimum_candidate_pool",
    "provider_diversity",
    "review_citations_resolve",
    "review_bound_to_latest_discovery",
    "manifest_bound_to_review",
    "research_plan_present",
    "research_plan_ready",
    "research_plan_has_no_blocking_questions",
    "plan_evidence_binding_valid",
    "plan_binding_copies_match",
    "plan_artifacts_in_manifest",
}

REQUIRED_TASKS = {
    "textualclassificationsickaccuracy",
    "textualsimilaritysickspearmancorrelation",
    "coreferenceresolutionsupergluewscaccuracy",
}


def completion_audit(project: Path) -> tuple[dict[str, object], list[str]]:
    project = project.resolve()
    errors: list[str] = []
    audit = audit_stage1(project)
    missing_checks = sorted(REQUIRED_CHECKS - set(audit.checks))
    failed_checks = sorted(name for name in REQUIRED_CHECKS if not audit.checks.get(name))
    if not audit.passed:
        errors.append("Stage 1 audit did not pass")
    if missing_checks:
        errors.append("required checks are missing: " + ", ".join(missing_checks))
    if failed_checks:
        errors.append("required checks failed: " + ", ".join(failed_checks))
    if audit.violations:
        errors.append("Stage 1 audit contains violations")

    state = load_state(project)
    if state.stage != Stage.PLAN_REVIEW or not state.latest_plan_draft:
        errors.append("project is not at the completed Stage 1 plan-review boundary")
        return ({"stage1_passed": False, "audit": audit.model_dump(mode="json")}, errors)

    plan_id = state.latest_plan_draft
    plan_path = project / "plans" / f"{plan_id}.json"
    plan = ResearchPlanDraft.model_validate(read_json(plan_path))
    approval = LiteratureApprovalRecord.model_validate(
        read_json(project / "literature" / "approval.json")
    )
    review = LiteratureReviewEnvelope.model_validate(
        read_json(project / "literature" / "review.json")
    )
    binding = PlanEvidenceBinding.model_validate(
        read_json(project / "plan_evidence_binding.json")
    )
    per_plan_binding = PlanEvidenceBinding.model_validate(
        read_json(project / "plans" / f"{plan_id}-evidence.json")
    )
    manifest = Stage1Manifest.model_validate(
        read_json(project / "literature" / "stage1_manifest.json")
    )

    if not plan.ready_to_freeze or plan.clarifying_questions:
        errors.append("research plan is not freeze-ready")
    metric_directions = {metric.name: metric.direction.value for metric in plan.metrics}
    if metric_directions.get("unsupported_claim_rate") != "minimize":
        errors.append("primary unsupported_claim_rate metric is missing or has the wrong direction")
    if not all(any(task in item for item in plan.datasets) for task in REQUIRED_TASKS):
        errors.append("research plan does not contain the fixed three-task AIRS-lite suite")
    if not any("18 total runs" in item for item in plan.scope_in):
        errors.append("research plan does not freeze the 18-run matrix")
    if not any("exactly 18 completed runs" in item for item in plan.stop_conditions):
        errors.append("research plan does not stop at the frozen 18-run matrix")

    novelty_ids = {candidate.novelty_id for candidate in review.synthesis.novelty_candidates}
    if approval.selected_novelty_id not in novelty_ids:
        errors.append("approved novelty ID is absent from the approved review")
    if binding != per_plan_binding:
        errors.append("root and per-plan evidence bindings differ")
    if binding.plan_id != plan_id or binding.plan_hash != sha256_file(plan_path):
        errors.append("plan ID or plan hash binding is stale")
    if binding.review_id != approval.review_id or binding.review_hash != approval.review_hash:
        errors.append("plan binding and approval identify different reviews")
    if binding.selected_novelty_id != approval.selected_novelty_id:
        errors.append("plan binding does not preserve the selected novelty ID")
    if binding.source_ids != sorted(approval.included_source_ids):
        errors.append("plan binding does not contain the exact approved source set")

    for relative, expected in manifest.hashes.items():
        artifact = safe_relative(project, relative.replace("\\", "/"))
        if not artifact.is_file() or sha256_file(artifact) != expected:
            errors.append(f"manifest artifact is missing or changed: {relative}")

    result: dict[str, object] = {
        "stage1_passed": not errors,
        "check_count": len(audit.checks),
        "candidate_count": audit.candidate_count,
        "included_count": audit.included_count,
        "review_id": audit.review_id,
        "selected_novelty_id": approval.selected_novelty_id,
        "plan_id": plan_id,
        "plan_hash": binding.plan_hash,
        "manifest_artifact_count": len(manifest.hashes),
        "warnings": audit.warnings,
        "errors": errors,
    }
    return result, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit completion of scientific Stage 1")
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    result, errors = completion_audit(args.project)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
