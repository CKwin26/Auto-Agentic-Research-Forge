"""Frozen reproduction policies and profile-specific comparison."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from .reproduction_domain import (
    ComparisonPolicy,
    ReproductionComparison,
    ReproductionComparisonMode,
    ReproductionMode,
    ReproductionPolicy,
    ReproductionScope,
    ResearchForgeEvidenceGrade,
)
from .workflow_domain import stable_id


def canonical_json_bytes(value: Any) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def freeze_reproduction_policy(
    *,
    study_id: str,
    completion_package_id: str,
    profile_id: str,
    comparison_mode: ReproductionComparisonMode,
    metric_tolerance: float = 0.0,
    effect_tolerance: float = 0.0,
    interval_tolerance: float = 0.0,
    scope: ReproductionScope = ReproductionScope.FULL,
    mode: ReproductionMode = ReproductionMode.CLEAN_ROOM_REPLAY,
    distribution_rule: dict[str, Any] | None = None,
) -> ReproductionPolicy:
    comparison = ComparisonPolicy(
        mode=comparison_mode,
        metric_tolerance=metric_tolerance,
        effect_tolerance=effect_tolerance,
        interval_tolerance=interval_tolerance,
        distribution_rule=distribution_rule or {},
    )
    required_grade = (
        ResearchForgeEvidenceGrade.RF_E2_CLEAN_ROOM_REPLAYED
        if scope is ReproductionScope.FULL
        else ResearchForgeEvidenceGrade.RF_E1_PACKAGE_VERIFIED
    )
    policy_id = stable_id(
        "reproduction-policy",
        study_id,
        completion_package_id,
        profile_id,
        comparison.model_dump(mode="json"),
        scope.value,
        mode.value,
    )
    return ReproductionPolicy(
        policy_id=policy_id,
        study_id=study_id,
        completion_package_id=completion_package_id,
        profile_id=profile_id,
        scope=scope,
        mode=mode,
        comparison_policy=comparison,
        required_evidence_grade=required_grade,
    )


def _numeric_match(
    original: float | None,
    reproduced: float | None,
    tolerance: float,
) -> bool:
    if original is None or reproduced is None:
        return original is reproduced
    return abs(float(original) - float(reproduced)) <= tolerance


def _interval_match(
    original: tuple[float, float] | list[float] | None,
    reproduced: tuple[float, float] | list[float] | None,
    tolerance: float,
) -> bool:
    if original is None or reproduced is None:
        return original is reproduced
    if len(original) != 2 or len(reproduced) != 2:
        return False
    return all(
        abs(float(left) - float(right)) <= tolerance
        for left, right in zip(original, reproduced, strict=True)
    )


def compare_computational_paired_v1(
    original: dict[str, Any],
    reproduced: dict[str, Any],
    policy: ReproductionPolicy,
) -> ReproductionComparison:
    """Compare a paired-comparison replay using only frozen policy fields."""

    rule = policy.comparison_policy
    if rule.mode is ReproductionComparisonMode.EXACT:
        hashes_match = (
            dict(original.get("output_hashes") or {})
            == dict(reproduced.get("output_hashes") or {})
            and bool(original.get("output_hashes"))
        )
        metric_matches = effect_matches = interval_matches = hashes_match
    else:
        metric_matches = _numeric_match(
            original.get("primary_metric"),
            reproduced.get("primary_metric"),
            rule.metric_tolerance,
        )
        effect_matches = _numeric_match(
            original.get("effect"),
            reproduced.get("effect"),
            rule.effect_tolerance,
        )
        interval_matches = _interval_match(
            original.get("interval"),
            reproduced.get("interval"),
            rule.interval_tolerance,
        )
        if (
            rule.mode
            is ReproductionComparisonMode.DISTRIBUTIONALLY_CONSISTENT
        ):
            minimum_effect = rule.distribution_rule.get("minimum_effect")
            if minimum_effect is not None:
                reproduced_effect = reproduced.get("effect")
                effect_matches = bool(
                    reproduced_effect is not None
                    and float(reproduced_effect) >= float(minimum_effect)
                )
    original_ids = sorted(str(item) for item in original.get("sample_ids", []))
    reproduced_ids = sorted(
        str(item) for item in reproduced.get("sample_ids", [])
    )
    sample_ids_match = (
        original_ids == reproduced_ids
        if rule.sample_ids_must_match
        else True
    )
    original_verdict = str(original.get("verdict") or "")
    reproduced_verdict = str(reproduced.get("verdict") or "")
    verdict_matches = (
        original_verdict == reproduced_verdict
        if rule.verdict_must_match
        else True
    )
    return ReproductionComparison(
        mode=rule.mode,
        original_metric=original.get("primary_metric"),
        reproduced_metric=reproduced.get("primary_metric"),
        metric_matches=metric_matches,
        original_effect=original.get("effect"),
        reproduced_effect=reproduced.get("effect"),
        effect_matches=effect_matches,
        original_interval=original.get("interval"),
        reproduced_interval=reproduced.get("interval"),
        interval_matches=interval_matches,
        original_verdict=original_verdict,
        reproduced_verdict=reproduced_verdict,
        verdict_matches=verdict_matches,
        sample_ids_match=sample_ids_match,
        details={
            "profile_id": policy.profile_id,
            "policy_id": policy.policy_id,
            "tolerances_frozen": True,
        },
    )


Comparator = Callable[
    [dict[str, Any], dict[str, Any], ReproductionPolicy],
    ReproductionComparison,
]


_COMPARATORS: dict[str, Comparator] = {
    "computational_paired_comparison_v1": compare_computational_paired_v1,
    "computational_paired_comparison_v2": compare_computational_paired_v1,
    "paired_binary_independent_v1": compare_computational_paired_v1,
    "paired_binary_clustered_v1": compare_computational_paired_v1,
}


def register_reproduction_comparator(
    profile_id: str, comparator: Comparator
) -> None:
    if profile_id in _COMPARATORS:
        raise ValueError(f"reproduction comparator already exists: {profile_id}")
    _COMPARATORS[profile_id] = comparator


def reproduction_comparator_registered(profile_id: str) -> bool:
    return profile_id in _COMPARATORS


def compare_reproduction(
    *,
    profile_id: str,
    original: dict[str, Any],
    reproduced: dict[str, Any],
    policy: ReproductionPolicy,
) -> ReproductionComparison:
    if policy.profile_id != profile_id:
        raise ValueError("reproduction policy profile does not match the package")
    comparator = _COMPARATORS.get(profile_id)
    if comparator is None:
        raise ValueError(f"unsupported reproduction profile: {profile_id}")
    return comparator(original, reproduced, policy)


__all__ = [
    "canonical_json_bytes",
    "canonical_sha256",
    "compare_reproduction",
    "freeze_reproduction_policy",
    "register_reproduction_comparator",
    "reproduction_comparator_registered",
]
