"""Public SDK for typed, lifecycle-aware Experiment Profiles.

The Stage 3 kernel historically discovered behavior by looking up string
identifiers in several local dictionaries.  This module provides the common
contract that new experiment families must implement.  A Profile may be
listed before every operation is connected, but an unconnected operation is
always explicit and can never be interpreted as successful scientific work.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..experiment_execution import ExperimentManifest
from ..models import StrictModel
from ..workflow_domain import ResearchContractVersion
from .base import ExperimentProfileBundle


class ProfileFamily(StrEnum):
    TABULAR_SUPERVISED = "tabular_supervised"
    BENCHMARK_PREDICTION = "benchmark_prediction"
    EXISTING_COMPUTATIONAL_PROJECT = "existing_computational_project"
    PAIRED_COMPUTATIONAL = "paired_computational"
    DETERMINISTIC_SIMULATION = "deterministic_simulation"
    TIME_SERIES_BACKTEST = "time_series_backtest"
    LLM_EVALUATION = "llm_evaluation"
    IMAGE_SUPERVISED = "image_supervised"
    RETRIEVAL_RAG = "retrieval_rag"
    RL_ENVIRONMENT = "rl_environment"
    ENGINEERING_SIMULATION = "engineering_simulation"
    BIOINFORMATICS_PIPELINE = "bioinformatics_pipeline"
    CAUSAL_OBSERVATIONAL = "causal_observational"
    HUMAN_BEHAVIOR = "human_behavior"
    WET_LAB_PROTOCOL = "wet_lab_protocol"


class ProfileMaturity(StrEnum):
    """Evidence ladder for a Profile implementation, not for a study result."""

    C0_DESCRIBED = "c0_described"
    C1_SCHEMA = "c1_schema"
    C2_DRY_RUN = "c2_dry_run"
    C3_REAL_FIXTURE = "c3_real_fixture"
    C4_INDEPENDENT_REPLAY = "c4_independent_replay"
    C5_MULTI_PROJECT = "c5_multi_project"


class ProfileAutomationMode(StrEnum):
    AUTOMATED = "automated"
    ASSISTED = "assisted"
    DESIGN_AND_IMPORT_ONLY = "design_and_import_only"


class ProfileOperation(StrEnum):
    QUALIFY = "qualify"
    COMPLETE_CONTRACT = "complete_contract"
    VALIDATE_CONTRACT = "validate_contract"
    RESOLVE_RESOURCES = "resolve_resources"
    COMPILE_RUN_DAG = "compile_run_dag"
    DRY_RUN = "dry_run"
    EXECUTE = "execute"
    EVALUATE = "evaluate"
    ADJUDICATE = "adjudicate"
    BIND_EVIDENCE = "bind_evidence"


class OperationReadiness(StrEnum):
    NOT_IMPLEMENTED = "not_implemented"
    COMPONENT_TESTED = "component_tested"
    INTEGRATION_TESTED = "integration_tested"
    CERTIFIED = "certified"

    def connected(self) -> bool:
        return self is not OperationReadiness.NOT_IMPLEMENTED


class ProfileQualificationStatus(StrEnum):
    QUALIFIED = "qualified"
    BUILD_REQUIRED = "build_required"
    CONTRACT_INCOMPLETE = "contract_incomplete"
    UNSUPPORTED = "unsupported"


class ContractCompletionKind(StrEnum):
    """Authority class for one proposed Contract completion field."""

    DETERMINISTIC_DERIVATION = "deterministic_derivation"
    PROFILE_DEFAULT = "profile_default"
    SCIENTIFIC_DECISION = "scientific_decision"
    UNRESOLVABLE = "unresolvable"


class ContractCompletionItem(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field_path: str = Field(min_length=1)
    kind: ContractCompletionKind
    candidate_value: Any | None = None
    source: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    requires_owner_approval: bool
    blocking: bool = False

    def model_post_init(self, __context: Any) -> None:
        if self.kind is ContractCompletionKind.DETERMINISTIC_DERIVATION:
            if self.candidate_value is None or self.requires_owner_approval:
                raise ValueError(
                    "deterministic derivation requires a value and no owner approval"
                )
        elif self.kind is ContractCompletionKind.PROFILE_DEFAULT:
            if self.candidate_value is None or not self.requires_owner_approval:
                raise ValueError(
                    "Profile defaults require a candidate value and owner approval"
                )
        elif self.kind is ContractCompletionKind.SCIENTIFIC_DECISION:
            if not self.requires_owner_approval:
                raise ValueError("scientific decisions require owner approval")
        elif not self.blocking:
            raise ValueError("unresolvable completion items must block")


class ContractCompletionPatch(StrictModel):
    """Structured, auditable result of Profile-specific Contract completion."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = 1
    profile_id: str
    profile_version: str
    items: tuple[ContractCompletionItem, ...] = ()

    @property
    def automatic_updates(self) -> tuple[ContractCompletionItem, ...]:
        return tuple(
            item
            for item in self.items
            if item.kind is ContractCompletionKind.DETERMINISTIC_DERIVATION
        )

    @property
    def owner_decisions(self) -> tuple[ContractCompletionItem, ...]:
        return tuple(item for item in self.items if item.requires_owner_approval)

    @property
    def blockers(self) -> tuple[ContractCompletionItem, ...]:
        return tuple(item for item in self.items if item.blocking)


@runtime_checkable
class ExperimentProfile(Protocol):
    """Public lifecycle contract for specialized experiment implementations.

    Implementations may delegate shared operations to the Stage 3 kernel, but
    every operation must remain explicit in the descriptor readiness matrix.
    """

    profile_id: str
    version: str

    def qualify(self, task_brief: Any, resources: Any) -> Any: ...
    def complete_contract(
        self, task_brief: Any, draft_contract: Any, resources: Any
    ) -> ContractCompletionPatch: ...
    def validate_contract(self, contract: Any) -> Any: ...
    def resolve_resources(self, contract: Any, resource_policy: Any) -> Any: ...
    def compile_run_plan(self, contract: Any, bindings: Any) -> Any: ...
    def dry_run(self, run_plan: Any) -> Any: ...
    def execute(self, run_plan: Any) -> Any: ...
    def evaluate(self, run_bundle: Any) -> Any: ...
    def adjudicate(self, evaluation_bundle: Any) -> Any: ...
    def bind_evidence(
        self, contract: Any, runs: Any, evaluation: Any, verdict: Any
    ) -> Any: ...


class ProfileIssue(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str
    field: str | None = None
    blocking: bool = True
    can_auto_repair: bool = False


class ProfileQualificationReport(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_id: str
    profile_version: str
    status: ProfileQualificationStatus
    issues: tuple[ProfileIssue, ...] = ()
    formal_execution_allowed: bool = False


class ExperimentProfileDescriptor(StrictModel):
    """Stable public description of one experiment family implementation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_id: str
    profile_version: str
    family: ProfileFamily
    title: str
    summary: str
    maturity: ProfileMaturity
    automation_mode: ProfileAutomationMode
    operations: dict[ProfileOperation, OperationReadiness]
    required_resource_roles: tuple[str, ...] = ()
    produced_artifact_roles: tuple[str, ...] = ()
    ethics_or_safety_gates: tuple[str, ...] = ()
    automation_boundary: str
    reference_patterns: tuple[str, ...] = ()

    def operation_ready(self, operation: ProfileOperation) -> bool:
        return self.operations.get(
            operation, OperationReadiness.NOT_IMPLEMENTED
        ).connected()

    def formal_path_connected(self) -> bool:
        return all(
            self.operation_ready(operation)
            for operation in (
                ProfileOperation.QUALIFY,
                ProfileOperation.COMPLETE_CONTRACT,
                ProfileOperation.VALIDATE_CONTRACT,
                ProfileOperation.RESOLVE_RESOURCES,
                ProfileOperation.COMPILE_RUN_DAG,
                ProfileOperation.DRY_RUN,
                ProfileOperation.EXECUTE,
                ProfileOperation.EVALUATE,
                ProfileOperation.ADJUDICATE,
                ProfileOperation.BIND_EVIDENCE,
            )
        )


ContractValidator = Callable[
    [ResearchContractVersion, ExperimentManifest], list[str]
]


def _field_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes, list, tuple, set, dict)):
        return bool(value)
    return True


def _contract_field_value(
    contract: ResearchContractVersion, field_name: str
) -> Any:
    """Resolve a scientific field without confusing nesting with absence.

    Older Profile Bundles named conceptual fields in
    ``required_contract_fields`` even when the persisted Contract stored them
    inside estimand, profile_parameters, statistical_rules, or implementation
    requirements.  The SDK preserves that history while exposing one lookup
    rule to every Profile.
    """

    direct = getattr(contract, field_name, None)
    if _field_present(direct):
        return direct
    containers = (
        contract.profile_parameters,
        contract.estimand,
        contract.statistical_rules,
        contract.data_requirements,
        contract.implementation_requirements,
        contract.environment_requirements,
        contract.resource_policy,
    )
    for container in containers:
        if isinstance(container, dict) and _field_present(
            container.get(field_name)
        ):
            return container[field_name]
    # Historical multi-arm bundles called the registered conjunction
    # "primary_contrasts" while the typed schema stores the operative rule as
    # decision_rule plus treatment/control arm lists.
    if field_name == "primary_contrasts" and all(
        _field_present(contract.profile_parameters.get(name))
        for name in ("decision_rule", "treatment_arm", "control_arms")
    ):
        return {
            "decision_rule": contract.profile_parameters["decision_rule"],
            "treatment_arm": contract.profile_parameters["treatment_arm"],
            "control_arms": contract.profile_parameters["control_arms"],
        }
    return None


@dataclass(frozen=True)
class ExperimentProfileRuntime:
    """Executable binding between a descriptor and current Profile code.

    Later Profile families may provide specialized compilers and executors,
    but every runtime starts with the same parameter and contract boundary.
    """

    bundle: ExperimentProfileBundle
    descriptor: ExperimentProfileDescriptor
    parameter_model: type[BaseModel] | None
    contract_validator: ContractValidator | None

    def complete_contract(
        self,
        contract: ResearchContractVersion,
        *,
        deterministic_values: Mapping[str, Any] | None = None,
        profile_defaults: Mapping[str, Any] | None = None,
        unresolvable_reasons: Mapping[str, str] | None = None,
    ) -> ContractCompletionPatch:
        """Classify every missing Profile field by its scientific authority.

        Callers may supply only values they can prove from a frozen manifest
        or from this Profile's versioned defaults.  Everything else is an
        explicit owner decision; inaccessible or unsupported inputs block.
        """

        derived = dict(deterministic_values or {})
        defaults = dict(profile_defaults or {})
        unavailable = dict(unresolvable_reasons or {})
        items: list[ContractCompletionItem] = []
        for field_name in self.bundle.required_contract_fields:
            if _field_present(_contract_field_value(contract, field_name)):
                continue
            if field_name in derived:
                item = ContractCompletionItem(
                    field_path=f"profile.{field_name}",
                    kind=ContractCompletionKind.DETERMINISTIC_DERIVATION,
                    candidate_value=derived[field_name],
                    source="frozen_resource_manifest",
                    reason="Value is deterministically derived from frozen input metadata.",
                    requires_owner_approval=False,
                )
            elif field_name in defaults:
                item = ContractCompletionItem(
                    field_path=f"profile.{field_name}",
                    kind=ContractCompletionKind.PROFILE_DEFAULT,
                    candidate_value=defaults[field_name],
                    source=f"profile_default:{self.descriptor.profile_version}",
                    reason="Versioned Profile default proposed for owner approval.",
                    requires_owner_approval=True,
                )
            elif field_name in unavailable:
                item = ContractCompletionItem(
                    field_path=f"profile.{field_name}",
                    kind=ContractCompletionKind.UNRESOLVABLE,
                    source="resource_resolver",
                    reason=unavailable[field_name],
                    requires_owner_approval=False,
                    blocking=True,
                )
            else:
                item = ContractCompletionItem(
                    field_path=f"profile.{field_name}",
                    kind=ContractCompletionKind.SCIENTIFIC_DECISION,
                    source="project_owner",
                    reason=(
                        "No frozen source or approved Profile default determines "
                        "this scientific field."
                    ),
                    requires_owner_approval=True,
                )
            items.append(item)
        return ContractCompletionPatch(
            profile_id=self.descriptor.profile_id,
            profile_version=self.descriptor.profile_version,
            items=tuple(items),
        )

    def qualify(
        self,
        contract: ResearchContractVersion,
        *,
        manifest: ExperimentManifest | None = None,
    ) -> ProfileQualificationReport:
        issues: list[ProfileIssue] = []
        if contract.experiment_profile != self.bundle.profile_id:
            issues.append(
                ProfileIssue(
                    code="PROFILE_ID_MISMATCH",
                    field="experiment_profile",
                    message="Research Contract selects a different Profile.",
                )
            )
        for field_name in self.bundle.required_contract_fields:
            if not _field_present(_contract_field_value(contract, field_name)):
                issues.append(
                    ProfileIssue(
                        code="REQUIRED_CONTRACT_FIELD_MISSING",
                        field=field_name,
                        message=(
                            f"Profile requires Research Contract field "
                            f"{field_name}."
                        ),
                        can_auto_repair=False,
                    )
                )
        if self.parameter_model is not None:
            try:
                self.parameter_model.model_validate(
                    contract.profile_parameters
                )
            except Exception as exc:
                issues.append(
                    ProfileIssue(
                        code="PROFILE_PARAMETERS_INVALID",
                        field="profile_parameters",
                        message=str(exc),
                        can_auto_repair=True,
                    )
                )

        formal_connected = (
            self.bundle.formal_execution_supported()
            and self.descriptor.formal_path_connected()
        )
        if not formal_connected:
            issues.append(
                ProfileIssue(
                    code="PROFILE_FORMAL_PATH_NOT_CONNECTED",
                    message=(
                        "The Profile is visible for design or development, "
                        "but its complete formal lifecycle is not connected."
                    ),
                )
            )
            status = ProfileQualificationStatus.UNSUPPORTED
        elif any(item.blocking for item in issues):
            status = ProfileQualificationStatus.CONTRACT_INCOMPLETE
        elif manifest is None:
            status = ProfileQualificationStatus.BUILD_REQUIRED
            issues.append(
                ProfileIssue(
                    code="EXECUTION_BINDINGS_PENDING",
                    message=(
                        "Scientific semantics are valid; Stage 3 must bind "
                        "and validate concrete execution resources."
                    ),
                    blocking=False,
                )
            )
        elif self.contract_validator is None:
            status = ProfileQualificationStatus.UNSUPPORTED
            issues.append(
                ProfileIssue(
                    code="CONTRACT_VALIDATOR_NOT_IMPLEMENTED",
                    message="No deterministic contract validator is connected.",
                )
            )
        else:
            violations = self.contract_validator(contract, manifest)
            issues.extend(
                ProfileIssue(
                    code="PROFILE_CONTRACT_VIOLATION",
                    message=violation,
                    can_auto_repair=True,
                )
                for violation in violations
            )
            status = (
                ProfileQualificationStatus.CONTRACT_INCOMPLETE
                if violations
                else ProfileQualificationStatus.QUALIFIED
            )
        return ProfileQualificationReport(
            profile_id=self.descriptor.profile_id,
            profile_version=self.descriptor.profile_version,
            status=status,
            issues=tuple(issues),
            formal_execution_allowed=(
                status is ProfileQualificationStatus.QUALIFIED
            ),
        )


def operation_matrix(
    readiness: OperationReadiness,
    *,
    overrides: Mapping[ProfileOperation, OperationReadiness] | None = None,
) -> dict[ProfileOperation, OperationReadiness]:
    matrix = {operation: readiness for operation in ProfileOperation}
    matrix.update(dict(overrides or {}))
    return matrix


__all__ = [
    "ContractCompletionItem",
    "ContractCompletionKind",
    "ContractCompletionPatch",
    "ExperimentProfile",
    "ContractValidator",
    "ExperimentProfileDescriptor",
    "ExperimentProfileRuntime",
    "OperationReadiness",
    "ProfileAutomationMode",
    "ProfileFamily",
    "ProfileIssue",
    "ProfileMaturity",
    "ProfileOperation",
    "ProfileQualificationReport",
    "ProfileQualificationStatus",
    "operation_matrix",
]
