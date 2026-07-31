"""Base types for reusable scientific components and frozen bundles."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field, model_validator

from ..models import StrictModel
from ..workflow_domain import Stage3Profile


class ComponentKind(StrEnum):
    DESIGN = "design"
    OUTCOME = "outcome"
    ESTIMAND = "estimand"
    ESTIMATOR = "estimator"
    INFERENCE = "inference"
    MISSINGNESS = "missingness"
    MULTIPLICITY = "multiplicity"
    VERDICT = "verdict"


class ProfileCertificationStatus(StrEnum):
    LEGACY_FROZEN = "legacy_frozen"
    CERTIFIED = "certified"
    DEVELOPMENT = "development"
    RETIRED_READ_ONLY = "retired_read_only"


class ProfileComponent(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    component_id: str
    kind: ComponentKind
    version: str
    description: str
    implementation_id: str
    configuration_schema: dict[str, Any] = Field(default_factory=dict)
    frozen_defaults: dict[str, Any] = Field(default_factory=dict)


class ExperimentProfileBundle(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_id: Stage3Profile
    profile_version: str
    certification_status: ProfileCertificationStatus

    design_id: str
    outcome_id: str
    estimand_id: str
    estimator_id: str
    inference_id: str
    missingness_id: str
    multiplicity_id: str
    verdict_policy_id: str

    contract_schema_id: str
    run_plan_compiler_id: str
    candidate_schema_id: str
    analysis_table_id: str
    qualification_id: str
    evaluator_id: str
    claim_envelope_id: str
    repair_policy_id: str
    reproduction_comparator_id: str
    assurance_suite_id: str
    frontend_renderer_id: str

    required_contract_fields: tuple[str, ...]
    supported_data_types: tuple[str, ...]
    builder_plugins: tuple[str, ...]
    historical_semantics_immutable: bool = True
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def legacy_bundle_is_immutable(self) -> "ExperimentProfileBundle":
        if (
            self.certification_status
            is ProfileCertificationStatus.LEGACY_FROZEN
            and not self.historical_semantics_immutable
        ):
            raise ValueError("legacy Profile semantics must remain immutable")
        return self

    def formal_execution_supported(self) -> bool:
        return self.certification_status in {
            ProfileCertificationStatus.LEGACY_FROZEN,
            ProfileCertificationStatus.CERTIFIED,
        }

    def component_ids(self) -> dict[ComponentKind, str]:
        return {
            ComponentKind.DESIGN: self.design_id,
            ComponentKind.OUTCOME: self.outcome_id,
            ComponentKind.ESTIMAND: self.estimand_id,
            ComponentKind.ESTIMATOR: self.estimator_id,
            ComponentKind.INFERENCE: self.inference_id,
            ComponentKind.MISSINGNESS: self.missingness_id,
            ComponentKind.MULTIPLICITY: self.multiplicity_id,
            ComponentKind.VERDICT: self.verdict_policy_id,
        }


__all__ = [
    "ComponentKind",
    "ExperimentProfileBundle",
    "ProfileCertificationStatus",
    "ProfileComponent",
]
