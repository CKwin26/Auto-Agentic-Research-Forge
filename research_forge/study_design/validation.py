"""Common deterministic validation for composable Research Contracts."""

from __future__ import annotations

import hashlib
from typing import Any

from .registry import inference_module, study_design
from .schemas import CompletionIssue, RepairClass


def _binding_id(value: Any) -> str:
    if hasattr(value, "id"):
        return str(value.id)
    if isinstance(value, dict):
        return str(value.get("id") or "")
    return ""


def validate_composable_contract(contract: Any) -> list[str]:
    binding = getattr(contract, "study_design", None) or {}
    design_id = _binding_id(binding)
    if not design_id:
        return []
    violations: list[str] = []
    try:
        design = study_design(design_id)
    except ValueError:
        return [f"unsupported Study Design: {design_id}"]
    violations.extend(design.validate_contract(contract))
    for raw in getattr(contract, "inference_modules", []) or []:
        module_id = _binding_id(raw)
        try:
            module = inference_module(module_id)
        except ValueError:
            violations.append(f"unsupported Inference Module: {module_id}")
            continue
        violations.extend(module.validate_contract(contract))
    return list(dict.fromkeys(violations))


def _violation_field_path(message: str) -> str:
    """Return a stable, actionable contract location for a validation error."""

    lowered = message.lower()
    routes = (
        (("unit of analysis", "variance unit", "independent unit"), "unit_structure"),
        (("primary outcome", "outcome"), "outcomes"),
        (("estimand", "same-arm"), "estimands"),
        (("noninferiority", "equivalence", "margin"), "decision_rules"),
        (("multiplicity", "confirmatory family"), "multiplicity"),
        (("bayesian", "prior"), "bayesian_inference"),
        (("denominator", "missing"), "missing_data_policy"),
        (("arm", "allocation"), "arm_definitions"),
    )
    for tokens, path in routes:
        if any(token in lowered for token in tokens):
            return path
    return "research_contract"


def blocking_issues_for_composable_contract(contract: Any) -> list[CompletionIssue]:
    """Expose every fail-closed design violation as a structured blocker.

    The legacy string validator remains compatible, while Workflow v2 and the
    UI receive machine-readable issue IDs, field paths, authority and repair
    semantics.  These blockers cannot be silently filled by a model.
    """

    issues: list[CompletionIssue] = []
    for message in validate_composable_contract(contract):
        digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
        issues.append(
            CompletionIssue(
                issue_id=f"design-blocker-{digest}",
                field_path=_violation_field_path(message),
                severity="critical",
                repair_class=RepairClass.UNRESOLVABLE_BLOCKER,
                expected="a complete, internally consistent frozen scientific design",
                observed=message,
                provenance="deterministic composable-contract validator",
                owner_approval_required=False,
            )
        )
    return issues


def blocking_issues_for_realized_data(
    plan: Any,
    rows: list[dict[str, Any]],
) -> list[CompletionIssue]:
    """Return fail-closed realized-data violations as Workflow blocking issues."""

    design = study_design(str(plan.study_design_id))
    issues: list[CompletionIssue] = []
    for message in design.validate_realized_data(plan, rows):
        digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
        issues.append(
            CompletionIssue(
                issue_id=f"realized-data-blocker-{digest}",
                field_path="formal_dataset",
                severity="critical",
                repair_class=RepairClass.UNRESOLVABLE_BLOCKER,
                expected=(
                    "formal rows conforming to the frozen unit, arm, outcome, "
                    "missingness, and denominator contract"
                ),
                observed=message,
                provenance="deterministic realized-data validator",
                owner_approval_required=False,
            )
        )
    return issues


__all__ = [
    "blocking_issues_for_composable_contract",
    "blocking_issues_for_realized_data",
    "validate_composable_contract",
]
