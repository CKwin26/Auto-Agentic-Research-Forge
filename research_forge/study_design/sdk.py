"""Public interfaces for composable Study Designs and Inference Modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .schemas import (
    AnalysisPlan,
    ClaimEnvelope,
    StudyDesignCompletionPatch,
    StudyDesignEvaluation,
    StudyDesignMaturity,
)


@dataclass(frozen=True)
class StudyDesignDescriptor:
    design_id: str
    version: str
    title: str
    summary: str
    maturity: StudyDesignMaturity
    formal_execution_supported: bool
    known_limits: tuple[str, ...] = ()


@dataclass(frozen=True)
class InferenceModuleDescriptor:
    module_id: str
    version: str
    title: str
    summary: str
    maturity: StudyDesignMaturity
    formal_execution_supported: bool
    sensitivity_only_by_default: bool = False
    known_limits: tuple[str, ...] = ()


@runtime_checkable
class StudyDesignProfile(Protocol):
    descriptor: StudyDesignDescriptor

    def qualify(self, contract: Any, resources: Any | None = None) -> Any: ...
    def complete_contract(
        self, task_brief: Any, draft_contract: Any, resources: Any | None = None
    ) -> StudyDesignCompletionPatch: ...
    def validate_contract(self, contract: Any) -> list[str]: ...
    def compile_analysis_plan(self, contract: Any) -> AnalysisPlan: ...
    def validate_realized_data(
        self, plan: AnalysisPlan, rows: list[dict[str, Any]]
    ) -> list[str]: ...
    def evaluate(
        self, plan: AnalysisPlan, rows: list[dict[str, Any]]
    ) -> StudyDesignEvaluation: ...
    def adjudicate(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> StudyDesignEvaluation: ...
    def produce_claim_envelope(
        self, plan: AnalysisPlan, evaluation: StudyDesignEvaluation
    ) -> ClaimEnvelope: ...


@runtime_checkable
class InferenceModule(Protocol):
    descriptor: InferenceModuleDescriptor

    def qualify(self, contract: Any) -> Any: ...
    def complete_contract(
        self, contract: Any
    ) -> StudyDesignCompletionPatch: ...
    def validate_contract(self, contract: Any) -> list[str]: ...
    def compute(self, payload: Any) -> Any: ...
    def adjudicate(self, payload: Any) -> Any: ...
    def produce_claim_constraints(self, payload: Any) -> list[str]: ...


@runtime_checkable
class StudyDesignAcceptanceEvaluator(Protocol):
    def evaluate_component_tests(self, evidence: Any) -> Any: ...
    def evaluate_e2e_run(self, evidence: Any) -> Any: ...
    def evaluate_negative_tests(self, evidence: Any) -> Any: ...
    def evaluate_mutation_tests(self, evidence: Any) -> Any: ...
    def derive_maturity(self, evidence: Any) -> StudyDesignMaturity: ...


__all__ = [
    "InferenceModule",
    "InferenceModuleDescriptor",
    "StudyDesignAcceptanceEvaluator",
    "StudyDesignDescriptor",
    "StudyDesignProfile",
]
