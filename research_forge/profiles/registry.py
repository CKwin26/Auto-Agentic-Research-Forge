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
from .catalog import build_runtime_registry, complete_profile_catalog
from .sdk import (
    ExperimentProfileDescriptor,
    ExperimentProfileRuntime,
    ProfileQualificationReport,
    ProfileMaturity,
)
from .acceptance import (
    availability_for_maturity,
    implementation_status,
    load_profile_acceptance_report,
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

PROFILE_RUNTIME_REGISTRY: dict[Stage3Profile, ExperimentProfileRuntime] = (
    build_runtime_registry(PROFILE_BUNDLE_REGISTRY)
)
PROFILE_CATALOG: dict[str, ExperimentProfileDescriptor] = (
    complete_profile_catalog()
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


def profile_runtime(
    profile: Stage3Profile | str,
) -> ExperimentProfileRuntime:
    try:
        profile_id = (
            profile if isinstance(profile, Stage3Profile) else Stage3Profile(profile)
        )
        return PROFILE_RUNTIME_REGISTRY[profile_id]
    except (ValueError, KeyError) as exc:
        raise ValueError("blocked_unsupported_design") from exc


def profile_descriptor(profile_id: Stage3Profile | str) -> ExperimentProfileDescriptor:
    key = profile_id.value if isinstance(profile_id, Stage3Profile) else str(profile_id)
    try:
        return PROFILE_CATALOG[key]
    except KeyError as exc:
        raise ValueError("blocked_unsupported_design") from exc


def qualify_profile_contract(
    profile: Stage3Profile | str,
    contract,
    *,
    manifest=None,
) -> ProfileQualificationReport:
    """Run the common Profile qualification boundary.

    Keeping this in the registry gives Stage 2 and Stage 3 the same answer
    about missing scientific semantics while allowing Stage 3-only resource
    bindings to remain unresolved during design.
    """

    return profile_runtime(profile).qualify(contract, manifest=manifest)


def profile_catalog_snapshot() -> list[dict[str, object]]:
    snapshot: list[dict[str, object]] = []
    for key in sorted(PROFILE_CATALOG):
        item = PROFILE_CATALOG[key].model_dump(mode="json")
        try:
            runtime = PROFILE_RUNTIME_REGISTRY[Stage3Profile(key)]
        except (ValueError, KeyError):
            item.update(
                {
                    "registered_runtime": False,
                    "certification_status": None,
                    "formal_execution_supported": False,
                    "implementation_status": implementation_status(
                        PROFILE_CATALOG[key].maturity
                    ).value,
                    "availability": "unavailable",
                    "verified_maturity": PROFILE_CATALOG[key].maturity.value,
                    "acceptance_report_present": False,
                    "scientific_formal_execution_eligible": False,
                    "last_verified_commit": None,
                    "verified_environment_digest": None,
                    "positive_test_count": 0,
                    "negative_test_count": 0,
                    "mutation_test_count": 0,
                    "real_case_run_count": 0,
                    "known_limits": [
                        "No executable Profile runtime is registered."
                    ],
                    "recompute_command": [],
                }
            )
        else:
            acceptance = load_profile_acceptance_report(key)
            verified_maturity = (
                acceptance.assessed_maturity
                if acceptance is not None
                else runtime.descriptor.maturity
            )
            formal_path_connected = bool(
                runtime.bundle.formal_execution_supported()
                and runtime.descriptor.formal_path_connected()
            )
            formal_maturity = verified_maturity in {
                ProfileMaturity.C3_REAL_FIXTURE,
                ProfileMaturity.C4_INDEPENDENT_REPLAY,
                ProfileMaturity.C5_MULTI_PROJECT,
            }
            acceptance_checks = (
                []
                if acceptance is None
                else [
                    value
                    for name, value in acceptance.__dict__.items()
                    if name.endswith("_check") and value is not None
                ]
            )
            item.update(
                {
                    "registered_runtime": True,
                    "certification_status": (
                        runtime.bundle.certification_status.value
                    ),
                    "formal_execution_supported": formal_path_connected,
                    "implementation_status": implementation_status(
                        verified_maturity
                    ).value,
                    "availability": availability_for_maturity(
                        verified_maturity,
                        formal_path_connected=formal_path_connected,
                    ).value,
                    "verified_maturity": verified_maturity.value,
                    "acceptance_report_present": acceptance is not None,
                    "scientific_formal_execution_eligible": bool(
                        formal_maturity and formal_path_connected
                    ),
                    "last_verified_commit": (
                        acceptance.code_commit if acceptance is not None else None
                    ),
                    "verified_environment_digest": (
                        acceptance.environment_digest
                        if acceptance is not None
                        else None
                    ),
                    "positive_test_count": sum(
                        1 for check in acceptance_checks if check.passed
                    ),
                    "negative_test_count": (
                        len(acceptance.negative_case_checks)
                        if acceptance is not None
                        else 0
                    ),
                    "mutation_test_count": (
                        len(acceptance.mutation_checks)
                        if acceptance is not None
                        else 0
                    ),
                    "real_case_run_count": (
                        sum(
                            1
                            for check in (
                                acceptance.real_success_case_check,
                                acceptance.real_blocked_case_check,
                            )
                            if check is not None and check.passed
                        )
                        if acceptance is not None
                        else 0
                    ),
                    "known_limits": (
                        list(acceptance.known_limits)
                        if acceptance is not None
                        else []
                    ),
                    "recompute_command": (
                        list(acceptance.recompute_command)
                        if acceptance is not None
                        else []
                    ),
                }
            )
        snapshot.append(item)
    return snapshot


def resolve_profile_capability(
    profile: Stage3Profile | str,
    *,
    runnable_assets_present: bool,
) -> ProfileCapabilityStatus:
    runtime = profile_runtime(profile)
    if not (
        runtime.bundle.formal_execution_supported()
        and runtime.descriptor.formal_path_connected()
    ):
        return ProfileCapabilityStatus.UNSUPPORTED
    return (
        ProfileCapabilityStatus.SUPPORTED
        if runnable_assets_present
        else ProfileCapabilityStatus.SUPPORTED_WITH_BUILD
    )


__all__ = [
    "PROFILE_CATALOG",
    "PROFILE_BUNDLE_REGISTRY",
    "PROFILE_RUNTIME_REGISTRY",
    "component_registry",
    "profile_catalog_snapshot",
    "profile_descriptor",
    "profile_bundle",
    "profile_runtime",
    "qualify_profile_contract",
    "resolve_profile_capability",
]
