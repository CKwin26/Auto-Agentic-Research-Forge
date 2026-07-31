"""Backward-compatible facade over the certified Profile Bundle registry."""

from __future__ import annotations

from dataclasses import dataclass

from .profiles.registry import PROFILE_BUNDLE_REGISTRY, profile_bundle
from .workflow_domain import Stage3Profile


@dataclass(frozen=True)
class ExperimentProfileDefinition:
    """Legacy view retained for existing builders and historical readers."""

    profile: Stage3Profile
    required_contract_fields: tuple[str, ...]
    supported_data_types: tuple[str, ...]
    supported_arm_structure: str
    run_plan_compiler: str
    builder_plugins: tuple[str, ...]
    canonical_output_schema: str
    evaluator_plugin: str
    verdict_policy: str
    frontend_renderer: str
    repair_policy: str


def _legacy_view(profile: Stage3Profile) -> ExperimentProfileDefinition:
    bundle = profile_bundle(profile)
    return ExperimentProfileDefinition(
        profile=bundle.profile_id,
        required_contract_fields=bundle.required_contract_fields,
        supported_data_types=bundle.supported_data_types,
        supported_arm_structure=bundle.design_id,
        run_plan_compiler=bundle.run_plan_compiler_id,
        builder_plugins=bundle.builder_plugins,
        canonical_output_schema=bundle.candidate_schema_id,
        evaluator_plugin=bundle.evaluator_id,
        verdict_policy=bundle.verdict_policy_id,
        frontend_renderer=bundle.frontend_renderer_id,
        repair_policy=bundle.repair_policy_id,
    )


PROFILE_REGISTRY = {
    profile: _legacy_view(profile)
    for profile in PROFILE_BUNDLE_REGISTRY
}


def profile_definition(
    profile: Stage3Profile,
) -> ExperimentProfileDefinition:
    try:
        return PROFILE_REGISTRY[profile]
    except KeyError as exc:
        raise ValueError("blocked_unsupported_design") from exc


__all__ = [
    "ExperimentProfileDefinition",
    "PROFILE_REGISTRY",
    "profile_definition",
]
