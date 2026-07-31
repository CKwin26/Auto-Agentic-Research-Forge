"""Generic, non-scientific Stage 2 feasibility MVP.

The adapter verifies that a newly designed comparison can be represented,
measured, and handed to Stage 3.  Its synthetic smoke values are never
scientific evidence and never contribute to a Study verdict.
"""

from __future__ import annotations

import hashlib
import json
import platform
from typing import Any, Sequence


def _digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def run_generic_feasibility_mvp(
    *,
    comparison_frame: dict[str, Any],
    primary_metric: str,
    metric_direction: str,
    denominator: str,
    resource_candidates: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Exercise a comparison schema with three explicitly synthetic cases."""

    comparator = str(comparison_frame.get("comparator") or "").strip()
    intervention = str(comparison_frame.get("intervention") or "").strip()
    outcome = str(comparison_frame.get("primary_outcome") or "").strip()
    unit = str(
        comparison_frame.get("unit_of_analysis")
        or "one preregistered eligible case"
    ).strip()
    local = [
        item
        for item in resource_candidates
        if item.get("source_kind") == "local_project"
        and item.get("eligibility") != "ineligible"
    ]
    external = [
        item
        for item in resource_candidates
        if item.get("source_kind") == "external_retrieval"
        and item.get("eligibility") != "ineligible"
        and item.get("content_hash")
    ]
    routes = [
        {
            "route": "reuse_local_resources",
            "status": "verified_available" if local else "not_selected",
            "resource_ids": [str(item.get("resource_id")) for item in local],
        },
        {
            "route": "acquire_owner_approved_external_resources",
            "status": (
                "verified_available"
                if external
                else "available_after_owner_authorized_retrieval"
            ),
            "resource_ids": [
                str(item.get("resource_id")) for item in external
            ],
        },
        {
            "route": "build_new_bounded_experiment",
            "status": (
                "verified_for_stage2_smoke"
                if comparator and intervention and outcome
                else "blocked"
            ),
            "resource_ids": [],
        },
    ]
    values = (0.25, 0.50, 0.75)
    cases = [
        {
            "case_id": f"synthetic-smoke-{index}",
            "synthetic": True,
            "scientific_evidence_eligible": False,
            "unit_of_analysis": unit,
            "baseline_arm_instantiated": bool(comparator),
            "treatment_arm_instantiated": bool(intervention),
            "metric_input": value,
            "metric_output": value,
            "denominator_increment": 1,
            "passed": bool(
                comparator
                and intervention
                and outcome
                and primary_metric
                and metric_direction
                not in {"", "unknown", "predeclare_before_freeze"}
                and denominator
            ),
        }
        for index, value in enumerate(values, start=1)
    ]
    metric_mean = sum(
        float(item["metric_output"]) for item in cases
    ) / len(cases)
    errors: list[str] = []
    if not comparator:
        errors.append("conceptual baseline arm is missing")
    if not intervention:
        errors.append("conceptual treatment arm is missing")
    if not outcome or not primary_metric:
        errors.append("primary outcome or metric is missing")
    if metric_direction in {"", "unknown", "predeclare_before_freeze"}:
        errors.append("metric direction is not frozen")
    if not denominator:
        errors.append("metric denominator is missing")
    if not all(item["passed"] for item in cases):
        errors.append("one or more synthetic smoke cases failed")
    status = "verified" if not errors else "blocked"
    return {
        "schema_version": 1,
        "adapter": "research_forge_generic_feasibility_mvp_v1",
        "status": status,
        "scientific_evidence_eligible": False,
        "formal_baseline_executed": False,
        "formal_treatment_executed": False,
        "comparison_frame_hash": _digest(comparison_frame),
        "environment": {
            "runtime": "python-standard-library",
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        "resource_routes": routes,
        "resource_sufficiency": {
            "stage2_smoke_resources_sufficient": status == "verified",
            "formal_stage3_resources_frozen": False,
            "interpretation": (
                "The platform can instantiate and measure the designed "
                "comparison. Full domain-valid resources remain a Stage 3 "
                "acquisition, construction, and freeze task."
            ),
        },
        "smoke_cases": cases,
        "metric_smoke_fixture": {
            "metric": primary_metric,
            "direction": metric_direction,
            "denominator": len(cases),
            "aggregate_value": metric_mean,
            "synthetic": True,
        },
        "metric_computable": not errors,
        "reset_or_isolation_verified": True,
        "errors": errors,
    }
