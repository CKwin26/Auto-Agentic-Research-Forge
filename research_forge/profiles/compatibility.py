"""Explicit cross-version reuse and migration policy."""

from __future__ import annotations

from pydantic import Field

from ..models import StrictModel
from ..workflow_domain import Stage3Profile


class ProfileCompatibility(StrictModel):
    source: Stage3Profile
    target: Stage3Profile
    contract_migration_allowed: bool
    run_cell_reuse_allowed: bool
    result_reuse_allowed: bool
    verdict_reuse_allowed: bool
    requires_scientific_successor: bool
    reasons: list[str] = Field(min_length=1)


_MATRIX: dict[tuple[Stage3Profile, Stage3Profile], ProfileCompatibility] = {
    (
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
    ): ProfileCompatibility(
        source=Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
        target=Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
        contract_migration_allowed=True,
        run_cell_reuse_allowed=True,
        result_reuse_allowed=True,
        verdict_reuse_allowed=True,
        requires_scientific_successor=False,
        reasons=["identical frozen Profile semantics"],
    ),
    (
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2,
    ): ProfileCompatibility(
        source=Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
        target=Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2,
        contract_migration_allowed=True,
        run_cell_reuse_allowed=False,
        result_reuse_allowed=False,
        verdict_reuse_allowed=False,
        requires_scientific_successor=True,
        reasons=[
            "v2 changes the independent variance unit and inference",
            "v2 requires sample-level independent evaluation",
        ],
    ),
}


def profile_compatibility(
    source: Stage3Profile, target: Stage3Profile
) -> ProfileCompatibility:
    if source == target:
        return _MATRIX.get(
            (source, target),
            ProfileCompatibility(
                source=source,
                target=target,
                contract_migration_allowed=True,
                run_cell_reuse_allowed=True,
                result_reuse_allowed=True,
                verdict_reuse_allowed=True,
                requires_scientific_successor=False,
                reasons=["identical Profile id and version"],
            ),
        )
    return _MATRIX.get(
        (source, target),
        ProfileCompatibility(
            source=source,
            target=target,
            contract_migration_allowed=False,
            run_cell_reuse_allowed=False,
            result_reuse_allowed=False,
            verdict_reuse_allowed=False,
            requires_scientific_successor=True,
            reasons=[
                "cross-Profile reuse has not been certified",
                "a new frozen Research Contract and Run Plan are required",
            ],
        ),
    )


__all__ = ["ProfileCompatibility", "profile_compatibility"]
