"""Whitelist registry for certified Profile Bundles and components."""

from __future__ import annotations

from ..workflow_domain import ProfileCapabilityStatus, Stage3Profile
from .base import ComponentKind, ExperimentProfileBundle, ProfileComponent
from .bundles import ALL_BUNDLES
from .components import (
    DESIGN_COMPONENTS,
    ESTIMAND_COMPONENTS,
    ESTIMATOR_COMPONENTS,
    INFERENCE_COMPONENTS,
    MISSINGNESS_COMPONENTS,
    MULTIPLICITY_COMPONENTS,
    OUTCOME_COMPONENTS,
    VERDICT_COMPONENTS,
)


_COMPONENT_REGISTRIES: dict[
    ComponentKind, dict[str, ProfileComponent]
] = {
    ComponentKind.DESIGN: DESIGN_COMPONENTS,
    ComponentKind.OUTCOME: OUTCOME_COMPONENTS,
    ComponentKind.ESTIMAND: ESTIMAND_COMPONENTS,
    ComponentKind.ESTIMATOR: ESTIMATOR_COMPONENTS,
    ComponentKind.INFERENCE: INFERENCE_COMPONENTS,
    ComponentKind.MISSINGNESS: MISSINGNESS_COMPONENTS,
    ComponentKind.MULTIPLICITY: MULTIPLICITY_COMPONENTS,
    ComponentKind.VERDICT: VERDICT_COMPONENTS,
}

PROFILE_BUNDLE_REGISTRY: dict[Stage3Profile, ExperimentProfileBundle] = {
    item.profile_id: item for item in ALL_BUNDLES
}
if len(PROFILE_BUNDLE_REGISTRY) != len(ALL_BUNDLES):
    raise RuntimeError("duplicate Stage 3 Profile Bundle id")

for bundle in PROFILE_BUNDLE_REGISTRY.values():
    for kind, component_id in bundle.component_ids().items():
        if component_id not in _COMPONENT_REGISTRIES[kind]:
            raise RuntimeError(
                f"{bundle.profile_id} references unregistered "
                f"{kind}: {component_id}"
            )


def component_registry(
    kind: ComponentKind,
) -> dict[str, ProfileComponent]:
    return dict(_COMPONENT_REGISTRIES[kind])


def profile_bundle(profile: Stage3Profile | str) -> ExperimentProfileBundle:
    try:
        profile_id = (
            profile if isinstance(profile, Stage3Profile) else Stage3Profile(profile)
        )
        return PROFILE_BUNDLE_REGISTRY[profile_id]
    except (ValueError, KeyError) as exc:
        raise ValueError("blocked_unsupported_design") from exc


def resolve_profile_capability(
    profile: Stage3Profile | str,
    *,
    runnable_assets_present: bool,
) -> ProfileCapabilityStatus:
    bundle = profile_bundle(profile)
    if not bundle.formal_execution_supported():
        return ProfileCapabilityStatus.UNSUPPORTED
    return (
        ProfileCapabilityStatus.SUPPORTED
        if runnable_assets_present
        else ProfileCapabilityStatus.SUPPORTED_WITH_BUILD
    )


__all__ = [
    "PROFILE_BUNDLE_REGISTRY",
    "component_registry",
    "profile_bundle",
    "resolve_profile_capability",
]
