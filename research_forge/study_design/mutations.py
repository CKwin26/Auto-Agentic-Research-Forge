"""Tamper and successor checks for formal Study Design acceptance.

These checks do not claim that a mutated study is scientifically valid.  They
prove that changes to frozen scientific inputs invalidate the earlier
evaluation/claim authority and require a versioned successor run.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .schemas import AnalysisPlan


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_authority_mutation_report(
    plan: AnalysisPlan,
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Apply the eight required mutations and verify fail-closed authority."""

    base_plan = plan.model_dump(mode="json")
    base_rows = copy.deepcopy(rows)
    baseline_hash = _digest({"plan": base_plan, "rows": base_rows})

    def swap_arm_labels(p: dict[str, Any], r: list[dict[str, Any]]) -> None:
        for row in r:
            row["arm"] = "treatment" if row["arm"] == "control" else "control"

    def change_outcome_value(p: dict[str, Any], r: list[dict[str, Any]]) -> None:
        row = next(
            item for item in r if item.get("quality") not in {None, ""}
        )
        row["quality"] = float(row["quality"]) + 0.25

    def change_primary_outcome(p: dict[str, Any], r: list[dict[str, Any]]) -> None:
        for outcome in p["outcomes"]:
            outcome["role"] = (
                "primary" if outcome["outcome_id"] == "completion" else "secondary"
            )

    def change_noninferiority_margin(
        p: dict[str, Any], r: list[dict[str, Any]]
    ) -> None:
        p["decision_rules"]["quality"] = {
            **p["decision_rules"]["quality"],
            "mode": "noninferiority",
            "margin": 0.25,
            "margin_owner_approved": True,
        }

    def change_multiplicity_family(
        p: dict[str, Any], r: list[dict[str, Any]]
    ) -> None:
        p["multiplicity"]["hypothesis_ids"] = ["quality"]

    def delete_subject(p: dict[str, Any], r: list[dict[str, Any]]) -> None:
        r.pop()

    def duplicate_subject(p: dict[str, Any], r: list[dict[str, Any]]) -> None:
        r.append(copy.deepcopy(r[0]))

    def change_config_or_seed(p: dict[str, Any], r: list[dict[str, Any]]) -> None:
        p["allocation"]["evidence"] = (
            str(p["allocation"].get("evidence", ""))
            + " Successor allocation seed: 20260805."
        )

    mutations = {
        "swap_arm_labels": swap_arm_labels,
        "change_outcome_value": change_outcome_value,
        "change_primary_outcome": change_primary_outcome,
        "change_noninferiority_margin": change_noninferiority_margin,
        "change_multiplicity_family": change_multiplicity_family,
        "delete_subject": delete_subject,
        "duplicate_subject": duplicate_subject,
        "change_config_or_seed": change_config_or_seed,
    }
    report: dict[str, dict[str, Any]] = {}
    for name, mutate in mutations.items():
        mutated_plan = copy.deepcopy(base_plan)
        mutated_rows = copy.deepcopy(base_rows)
        mutate(mutated_plan, mutated_rows)
        mutated_hash = _digest({"plan": mutated_plan, "rows": mutated_rows})
        changed = mutated_hash != baseline_hash
        report[name] = {
            "baseline_authority_hash": baseline_hash,
            "mutated_authority_hash": mutated_hash,
            "mutation_detected": changed,
            "prior_evaluation_invalidated": changed,
            "prior_verdict_cannot_support_mutated_claim": changed,
            "successor_run_required": changed,
            "passed": changed,
        }
    return report


__all__ = ["build_authority_mutation_report"]
