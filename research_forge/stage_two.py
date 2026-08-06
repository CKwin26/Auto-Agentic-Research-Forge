"""General Stage 2: feasibility, protocol, and baseline workflow.

Stage 2 turns a frozen broad direction into one resource-feasible, specific
study contract.  It may inspect the environment and execute one explicitly
declared baseline, but it never executes a treatment arm or emits a scientific
verdict.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Literal

from pydantic import Field

from .experiment_execution import (
    experiment_for_action,
    load_project_experiment_manifest,
    preflight_experiment,
    run_declared_experiment,
)
from .models import StrictModel, utc_now
from .storage import read_json, sha256_file, write_json_atomic
from .workflow_domain import (
    ArtifactRole,
    ArtifactStatus,
    EntryMode,
    ExecutionStatus,
    ExecutorType,
    GateStatus,
    GateType,
    Hypothesis,
    HypothesisRole,
    Phase,
    ProtocolStatus,
    ResearchContractVersion,
    ScopeContractVersion,
    Stage3Profile,
    StepInstance,
    WorkflowRepository,
    is_secret_path,
    stable_id,
)

if TYPE_CHECKING:
    from .workflow_scheduler import StepContext, StepHandler


class EvidenceStatus(StrEnum):
    VERIFIED = "verified"
    REPORTED = "reported"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    AVAILABLE_UNVERIFIED = "available_unverified"
    REQUESTABLE = "requestable"
    SUBSTITUTABLE = "substitutable"
    UNAVAILABLE = "unavailable"
    PROHIBITED = "prohibited"


class ValidationStatus(StrEnum):
    VALIDATED = "validated"
    PARTIAL = "partial"
    UNVALIDATED = "unvalidated"
    NOT_APPLICABLE = "not_applicable"


class BlockingLevel(StrEnum):
    BLOCKING = "blocking"
    STRENGTHENING = "strengthening"
    OPTIONAL = "optional"


class LicenseStatus(StrEnum):
    VERIFIED = "verified"
    REPORTED = "reported"
    UNKNOWN = "unknown"
    RESTRICTED = "restricted"
    NOT_APPLICABLE = "not_applicable"


class TopicStatus(StrEnum):
    READY = "ready"
    CONDITIONAL = "conditional"
    BLOCKED = "blocked"


class Stage2GateStatus(StrEnum):
    PASS = "PASS"
    CONDITIONAL_PASS = "CONDITIONAL_PASS"
    DESIGN_READY = "DESIGN_READY"
    BUILD_REQUIRED = "BUILD_REQUIRED"
    FAIL = "FAIL"


class ResourceCategory(StrEnum):
    DATA = "data"
    IMPLEMENTATION = "implementation"
    MODEL = "model"
    COMPUTE = "compute"
    SOFTWARE = "software"
    INFRASTRUCTURE = "infrastructure"
    EVALUATION = "evaluation"
    COMPLIANCE = "compliance"
    HUMAN = "human"
    OPERATIONAL = "operational"


class ResourceNeedType(StrEnum):
    DATASET = "dataset"
    TOOLKIT = "toolkit"
    BENCHMARK = "benchmark"
    MODEL = "model"
    IMPLEMENTATION = "implementation"


class CandidateEligibility(StrEnum):
    ELIGIBLE = "eligible"
    CONDITIONAL = "conditional"
    INELIGIBLE = "ineligible"


class ProbeStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"
    NOT_APPLICABLE = "not_applicable"


class ResourceRequirement(StrictModel):
    requirement_id: str
    need_type: ResourceNeedType
    purpose: str
    contract_field: str
    blocking_level: BlockingLevel
    required_capabilities: list[str] = Field(default_factory=list)
    accepted_formats: list[str] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)


class ConcreteResourceCandidate(StrictModel):
    candidate_id: str
    requirement_ids: list[str]
    name: str
    need_type: ResourceNeedType
    source_kind: Literal["local_project", "external_retrieval"]
    source_provider: str
    resource_id: str
    canonical_identifier: str
    url: str | None = None
    version_or_revision: str | None = None
    content_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    license: str | None = None
    license_status: LicenseStatus
    availability: AvailabilityStatus
    metadata_validation: ValidationStatus
    pinning_validation: ValidationStatus
    schema_probe: ProbeStatus
    install_probe: ProbeStatus
    checks: list[dict[str, Any]] = Field(default_factory=list)
    score_components: dict[str, int] = Field(default_factory=dict)
    total_score: int = Field(default=0, ge=0, le=100)
    eligibility: CandidateEligibility
    blockers: list[str] = Field(default_factory=list)
    selection_notes: list[str] = Field(default_factory=list)


class ResourceRecord(StrictModel):
    resource_id: str
    category: ResourceCategory
    name: str
    location: str | None = None
    content_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    metadata_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    availability: AvailabilityStatus
    validation: ValidationStatus
    blocking_level: BlockingLevel
    license_status: LicenseStatus
    acquisition_method: str
    evidence_status: EvidenceStatus
    notes: list[str] = Field(default_factory=list)


class MethodCard(StrictModel):
    method_id: str
    title: str
    citation_or_identifier: str | None = None
    source_type: str
    research_question: str | None = None
    research_object: str | None = None
    study_design: str | None = None
    population_or_dataset: str | None = None
    unit_of_analysis: str | None = None
    dataset_name_and_version: str | None = None
    dataset_scale: str | None = None
    data_preprocessing: str | None = None
    train_validation_test_split: str | None = None
    intervention_or_method: str | None = None
    model_and_algorithm: str | None = None
    model_version: str | None = None
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    comparator: str | None = None
    primary_outcome: str | None = None
    secondary_outcomes: list[str] = Field(default_factory=list)
    sample_size: str | None = None
    split_or_sampling_strategy: str | None = None
    baseline: str | None = None
    control_group: str | None = None
    experimental_group: str | None = None
    metrics: list[str] = Field(default_factory=list)
    success_threshold: str | None = None
    random_seeds: list[int] = Field(default_factory=list)
    repetitions: int | None = Field(default=None, ge=1)
    statistical_analysis: str | None = None
    statistical_test: str | None = None
    uncertainty_analysis: str | None = None
    ablation_experiments: list[str] = Field(default_factory=list)
    robustness_checks: list[str] = Field(default_factory=list)
    leakage_controls: list[str] = Field(default_factory=list)
    code_available: bool | None = None
    data_available: bool | None = None
    environment_available: bool | None = None
    model_or_revision: str | None = None
    compute_reported: str | None = None
    hardware: str | None = None
    software_environment: str | None = None
    code_url: str | None = None
    data_url: str | None = None
    license: str | None = None
    run_command_public: bool | None = None
    environment_lock_available: bool | None = None
    third_party_reproduced: bool | None = None
    known_failures_or_controversies: list[str] = Field(default_factory=list)
    paper_code_consistency: str | None = None
    limitations: list[str] = Field(default_factory=list)
    threats_to_validity: list[str] = Field(default_factory=list)
    reproducibility_grade: str = Field(pattern=r"^[A-E]$")
    evidence_status: EvidenceStatus
    resource_id: str | None = None
    fields_unknown: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class FeasibilityDimension(StrictModel):
    assessment: Literal["strong", "adequate", "weak", "blocked", "unknown"]
    rationale: str
    dependencies: list[str] = Field(default_factory=list)
    evidence_status: EvidenceStatus = EvidenceStatus.INFERRED


class TopicCandidate(StrictModel):
    topic_id: str
    title: str
    research_question: str
    background_and_motivation: str
    relation_to_direction: str
    existing_work_gap: str
    hypothesis: str
    study_design: str
    unit_of_analysis: str
    population_or_corpus: str
    intervention_or_method: str
    primary_outcome: str
    comparison: str
    minimum_meaningful_effect: str
    candidate_contribution: str
    scope_in: list[str]
    scope_out: list[str]
    required_resource_ids: list[str]
    required_resource_types: list[ResourceNeedType] = Field(default_factory=list)
    recommended_resource_candidate_ids: list[str] = Field(default_factory=list)
    resource_boundary_summary: str = ""
    required_data_ids: list[str] = Field(default_factory=list)
    required_baselines: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_compute: list[str] = Field(default_factory=list)
    blocking_resources: list[str] = Field(default_factory=list)
    strengthening_resources: list[str] = Field(default_factory=list)
    major_risks: list[str] = Field(default_factory=list)
    alternative_designs: list[str] = Field(default_factory=list)
    expected_evidence_strength: str
    expected_reproducibility: str
    compliance_status: str
    feasibility_dimensions: dict[str, FeasibilityDimension]
    method_card_ids: list[str] = Field(default_factory=list)
    status: TopicStatus
    feasibility_reasons: list[str]
    unresolved_conditions: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)
    evidence_status: EvidenceStatus = EvidenceStatus.INFERRED


_PRIMARY_OUTCOME_PLACEHOLDERS = {
    "",
    "primary_metric",
    "baseline_primary_metric",
    "to be selected from a verified project metric",
    "must be selected from a verified project metric",
    "unknown",
}


def _infer_primary_outcome(value: str | None, research_question: str) -> str:
    """Return a usable conceptual outcome without inventing observations."""

    declared = str(value or "").strip()
    if declared.casefold() not in _PRIMARY_OUTCOME_PLACEHOLDERS:
        return declared
    question = str(research_question or "").strip()
    folded = question.casefold()
    if "top-k" in folded and ("识别率" in question or "identification" in folded):
        return "Top-K high-return event identification rate"
    cues = (
        ("准确率", "accuracy"),
        ("识别率", "identification rate"),
        ("成功率", "success rate"),
        ("错误率", "error rate"),
        ("失败率", "failure rate"),
        ("延迟", "latency"),
        ("召回率", "recall"),
        ("精确率", "precision"),
        ("覆盖率", "coverage rate"),
        ("accuracy", "accuracy"),
        ("success rate", "success rate"),
        ("error rate", "error rate"),
        ("failure rate", "failure rate"),
        ("latency", "latency"),
        ("recall", "recall"),
        ("precision", "precision"),
        ("coverage", "coverage rate"),
    )
    for cue, outcome in cues:
        if cue in question or cue in folded:
            return outcome
    return "baseline_primary_metric"


def _split_comparison(value: str | None) -> tuple[str, str]:
    comparison = str(value or "").strip()
    for separator in (" versus ", " vs. ", " vs ", " compared with ", " compared to "):
        if separator not in comparison.casefold():
            continue
        index = comparison.casefold().index(separator)
        intervention = comparison[:index].strip()
        comparator = comparison[index + len(separator) :].strip()
        if intervention and comparator:
            return intervention, comparator
    return "the declared method or intervention", "the declared baseline"


def _default_formal_task_semantics(
    scope: ScopeContractVersion,
    frame: dict[str, Any],
) -> str:
    """Compile an owner-reviewable formal task from the frozen Scope.

    Stage 2 must not hand Stage 3 a placeholder and expect the experiment
    builder to invent the scientific design.  The task below is deliberately
    implementation-neutral, but it freezes the operation, prediction
    semantics, unit, and authoritative target rule needed for a default
    owner-approval path.
    """

    outcome = str(frame.get("primary_outcome") or "primary outcome").strip()
    unit = str(
        frame.get("unit_of_analysis") or "preregistered eligible case"
    ).strip()
    field_diff = (
        dict(scope.field_diff) if isinstance(scope.field_diff, dict) else {}
    )
    target_rule = str(
        field_diff.get("operational_definition")
        or field_diff.get("target_rule")
        or (
            "the owner-approved target or reference fields in the frozen "
            "evaluation dataset"
        )
    ).strip()
    return (
        f"For every frozen eligible {unit}, apply the baseline and treatment "
        f"to the same input, produce the prediction or score required to "
        f"compute {outcome}, and bind the authoritative target using "
        f"{target_rule}."
    )


def _default_arm_behaviors(
    scope: ScopeContractVersion,
    frame: dict[str, Any],
) -> tuple[str, str]:
    """Compile a concrete conceptual baseline and a single allowed delta."""

    contribution = str(
        frame.get("intervention")
        or scope.candidate_contribution
        or scope.direction
    ).strip()
    comparator = str(
        frame.get("comparator") or "the frozen existing project pipeline"
    ).strip()
    baseline = (
        f"Run {comparator} on every eligible case using the same inputs, "
        "preprocessing, target rule, evaluator, seeds, and stopping rule."
    )
    treatment = (
        "Run the identical frozen pipeline on the same paired cases, changing "
        f"only this registered intervention: {contribution}. All other inputs, "
        "preprocessing, target rules, evaluator settings, seeds, and stopping "
        "rules remain identical to the baseline."
    )
    return baseline, treatment


def _comparison_frame_for_scope(scope: ScopeContractVersion) -> dict[str, Any]:
    """Complete a conceptual comparison frame from a frozen specific Scope."""

    existing = (
        dict(scope.field_diff.get("comparison_frame") or {})
        if isinstance(scope.field_diff, dict)
        else {}
    )
    intervention, comparator = _split_comparison(scope.comparison)
    frame = {
        **existing,
        "research_question": str(
            existing.get("research_question") or scope.research_question
        ),
        "falsifiable_hypothesis": str(
            existing.get("falsifiable_hypothesis")
            or scope.field_diff.get("selected_hypothesis")
            or (
                f"{intervention} produces a predeclared change in "
                f"{_infer_primary_outcome(scope.primary_outcome, scope.research_question)} "
                f"relative to {comparator}."
            )
        ),
        "comparator": str(existing.get("comparator") or comparator),
        "intervention": str(existing.get("intervention") or intervention),
        "primary_outcome": _infer_primary_outcome(
            str(existing.get("primary_outcome") or scope.primary_outcome or ""),
            str(existing.get("research_question") or scope.research_question),
        ),
        "unit_of_analysis": str(
            existing.get("unit_of_analysis")
            or scope.unit_of_analysis
            or "one preregistered eligible case"
        ),
    }
    generic_interventions = {
        "the declared method or intervention",
        "the primary implementation",
        "registered treatment intervention",
    }
    generic_comparators = {
        "the declared baseline",
        "frozen replication and sensitivity conditions",
    }
    if frame["intervention"].strip().casefold() in generic_interventions:
        frame["intervention"] = str(
            scope.candidate_contribution
            or scope.direction
            or "the frozen candidate method"
        ).strip()
    if frame["comparator"].strip().casefold() in generic_comparators:
        frame["comparator"] = (
            "the frozen current project pipeline with the candidate "
            "intervention disabled"
        )
    semantic_text = " ".join(
        [
            scope.direction,
            scope.research_question,
            scope.candidate_contribution,
            scope.comparison or "",
            *[
                str(item)
                for item in (scope.field_diff or {}).get(
                    "academic_concepts", []
                )
            ],
        ]
    )
    from .domain_adapters import is_finance_backtest_contract

    if is_finance_backtest_contract({"scope": semantic_text}):
        operational_definition = str(
            (scope.field_diff or {}).get("operational_definition")
            or (
                "a selected security is positive when its frozen forward "
                "20-session executable return is in the top industry decile "
                "and is at least 10 percent"
            )
        ).strip()
        frame.update(
            {
                "research_question": (
                    "Across every eligible frozen monthly decision snapshot, "
                    "does the dual-quality Top-K ranker improve high-return "
                    "event identification over the V3 technology-quality "
                    "Top-K ranker?"
                ),
                "falsifiable_hypothesis": (
                    "The dual-quality Top-K ranker increases the paired "
                    "high-return event identification rate by at least 0.05 "
                    "relative to the V3 technology-quality Top-K ranker."
                ),
                "comparator": "v3_tech_quality_top5 ranking",
                "intervention": "dual_quality_top5 ranking",
                "primary_outcome": "Top-K high-return event identification rate",
                "unit_of_analysis": (
                    "one selected security position within an eligible "
                    "monthly decision snapshot"
                ),
                "denominator": (
                    "all selected Top-K positions across every frozen "
                    "eligible monthly decision snapshot"
                ),
                "minimum_meaningful_effect": 0.05,
                "success_threshold": 0.05,
                "threshold_basis": (
                    "absolute paired difference in the bounded 0-1 event "
                    "identification rate"
                ),
                "baseline_behavior": (
                    "For each frozen eligible monthly decision snapshot, use "
                    "only point-in-time features available before the decision "
                    "cutoff, apply the frozen v3_tech_quality_top5 score, rank "
                    "the eligible universe, and select the first five rows."
                ),
                "treatment_behavior": (
                    "For the same snapshot and eligible universe, use the same "
                    "point-in-time inputs, target rule, evaluator, and Top-5 "
                    "selection rule, changing only the ranking score to the "
                    "frozen dual_quality_top5 score."
                ),
                "data_boundary": {
                    "target_population": (
                        "all mature monthly decision snapshots with at least "
                        "12 prior feature snapshots"
                    ),
                    "sampling_frame": (
                        "census of all eligible monthly snapshots; no "
                        "return-based date selection"
                    ),
                    "inclusion_rules": [
                        "minimum 12 prior feature snapshots",
                        "forward 20-session label is mature at evaluation time",
                        "security is in the point-in-time eligible universe",
                    ],
                    "exclusion_rules": [
                        "missing point-in-time features required by either arm",
                        "no executable forward-return target after the frozen "
                        "calendar and corporate-action rules",
                    ],
                    "target_rule": operational_definition,
                    "label_origin": (
                        "point-in-time prices, industry membership, trading "
                        "calendar, corporate actions, fees, and slippage frozen "
                        "before formal execution"
                    ),
                    "denominator": (
                        "all selected Top-K positions across every frozen "
                        "eligible monthly decision snapshot"
                    ),
                    "unit_of_analysis": (
                        "selected security position nested in monthly snapshot"
                    ),
                    "time_boundary": (
                        "features and universe membership are frozen at each "
                        "decision timestamp; labels use the next 20 executable "
                        "trading sessions"
                    ),
                },
                "statistical_rules": {
                    "method": "paired monthly-snapshot bootstrap",
                    "analysis": "paired monthly-snapshot bootstrap",
                    "effect_threshold": 0.05,
                    "effect_scale": "absolute",
                    "effect_unit": "proportion",
                    "missing_cell_policy": "inconclusive",
                    "confidence_level": 0.95,
                    "bootstrap_resamples": 10_000,
                    "bootstrap_seed": 4242,
                    "resampling_unit": "monthly decision snapshot",
                },
            }
        )
    design = str(scope.study_design or "").casefold()
    explicitly_non_computational = any(
        cue in design
        for cue in (
            "interview",
            "survey",
            "wet lab",
            "clinical trial",
            "field observation",
            "访谈",
            "问卷",
            "湿实验",
            "临床试验",
            "田野观察",
        )
    )
    # Research Forge v1 is computational-research first.  A specific Scope
    # therefore receives a conservative paired-comparison design by default,
    # even when its prose does not literally say "paired".  The owner may
    # revise it before freezing; non-computational studies remain boundary
    # reports and are never represented as executable experiments.
    paired_computational = bool(scope.study_id) and not explicitly_non_computational
    if paired_computational and not frame.get("experiment_profile"):
        metric_name = _metric_identifier(str(frame["primary_outcome"]))
        bounded_rate_metric = any(
            cue in metric_name
            for cue in (
                "accuracy",
                "rate",
                "recall",
                "precision",
                "coverage",
            )
        )
        frame.update(
            {
                "experiment_profile": (
                    Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1.value
                ),
                "tasks": [_default_formal_task_semantics(scope, frame)],
                "seeds": [11, 29, 47, 71, 97],
                "splits": ["formal"],
                "baseline_experiment_id": "baseline-v1",
                "baseline_action_id": "action-baseline",
                "treatment_experiment_id": "treatment-v1",
                "treatment_action_id": "action-treatment",
                "output_schema": {
                    "format": "json",
                    "record_layout": "summary",
                    "metric_field": metric_name,
                    "denominator_field": "denominator",
                    "sample_id_field": "sample_ids",
                    "raw_fields": [
                        "sample_id",
                        "prediction",
                        "target_reference",
                    ],
                },
                "profile_source": {
                    "kind": "stage2_design_compiler",
                    "basis": scope.study_design,
                    "requires_owner_approval": True,
                    "scientific_evidence_eligible": False,
                },
                **(
                    {
                        "minimum_meaningful_effect": 0.05,
                        "success_threshold": 0.05,
                        "threshold_basis": (
                            "Stage 2 scale-based default for a bounded 0–1 "
                            "primary metric; owner approval is required."
                        ),
                        "statistical_rules": {
                            "method": "paired_mean_difference",
                            "effect_threshold": 0.05,
                            "missing_cell_policy": "inconclusive",
                            "confidence_level": 0.95,
                        },
                    }
                    if bounded_rate_metric
                    else {
                        "minimum_meaningful_effect": 0.05,
                        "success_threshold": 0.05,
                        "threshold_basis": (
                            "Stage 2 relative-effect default: a five percent "
                            "paired improvement over the frozen baseline "
                            "magnitude; owner approval is required."
                        ),
                        "statistical_rules": {
                            "method": "paired_mean_difference",
                            "effect_threshold": 0.05,
                            "effect_scale": "relative",
                            "effect_unit": "fraction_of_baseline",
                            "missing_cell_policy": "inconclusive",
                            "confidence_level": 0.95,
                        },
                    }
                ),
            }
        )
    if paired_computational:
        baseline_behavior, treatment_behavior = _default_arm_behaviors(
            scope, frame
        )
        tasks = [
            str(item).strip()
            for item in frame.get("tasks", [])
            if str(item).strip()
        ]
        if not tasks or any(
            item.casefold()
            in {"formal-primary-task", "primary-task", "task", "default"}
            for item in tasks
        ):
            frame["tasks"] = [_default_formal_task_semantics(scope, frame)]
        frame.setdefault("baseline_behavior", baseline_behavior)
        frame.setdefault("treatment_behavior", treatment_behavior)
        boundary = dict(frame.get("data_boundary") or {})
        metric_name = _metric_identifier(str(frame["primary_outcome"]))
        if any(
            token in metric_name
            for token in ("accuracy", "precision", "recall")
        ):
            target_rule = (
                "Use the authoritative target field defined by the frozen "
                "evaluation schema; compare canonicalized predicted and target "
                "labels exactly, with no model-generated relabeling."
            )
        elif any(
            token in metric_name
            for token in ("unsupported_claim", "coverage", "rate")
        ):
            target_rule = (
                "Apply the frozen eligibility and evaluator rubric to each "
                "unit and emit one deterministic binary indicator before "
                "aggregation; missing required evidence is inconclusive, not "
                "silently negative."
            )
        elif any(
            token in metric_name
            for token in ("latency", "runtime", "duration")
        ):
            target_rule = (
                "Measure elapsed time for the frozen operation boundary with "
                "a monotonic clock after the declared warm-up; retain every "
                "eligible per-unit observation."
            )
        else:
            target_rule = (
                "Compute the primary outcome for each eligible unit with the "
                "frozen deterministic evaluator and its declared input and "
                "reference fields; model-generated targets are prohibited."
            )
        defaults = {
            "target_population": (
                "all units satisfying the frozen inclusion rules in the "
                "owner-authorized evaluation resource set materialized by "
                "Stage 3"
            ),
            "sampling_frame": (
                "deterministic census of every schema-valid eligible unit in "
                "the Stage 3 content-addressed evaluation snapshot; no "
                "outcome-dependent selection"
            ),
            "inclusion_rules": [
                "unit belongs to the owner-authorized evaluation snapshot",
                "all fields required by the frozen evaluator are present",
            ],
            "exclusion_rules": [
                "schema-incompatible unit",
                "unit lacks a required authoritative input or reference field",
            ],
            "target_rule": target_rule,
            "label_origin": (
                "the owner-authorized content-addressed evaluation snapshot "
                "and frozen evaluator specification"
            ),
            "denominator": str(
                frame.get("denominator")
                or f"all eligible {frame['unit_of_analysis']} units after "
                "preregistered exclusions"
            ),
            "unit_of_analysis": str(frame["unit_of_analysis"]),
            "time_boundary": (
                "resource contents and eligibility are frozen before formal "
                "execution; Stage 3 may bind exact paths and hashes but may "
                "not change these scientific rules"
            ),
            "preprocessing": (
                "apply only schema-preserving normalization declared by the "
                "frozen evaluator; reject incompatible units"
            ),
        }
        for key, value in defaults.items():
            current = boundary.get(key)
            if (
                current is None
                or current == []
                or str(current).strip().casefold()
                in {"", "unknown", "unverified", "local project resources"}
            ):
                boundary[key] = value
        frame["data_boundary"] = boundary
    return frame


def _comparison_frame_with_local_metric(
    context: "StepContext",
    scope: ScopeContractVersion,
    frame: dict[str, Any],
    selected_implementation: dict[str, Any] | None,
) -> dict[str, Any]:
    """Use bounded local project text to propose a metric for Stage 2 design.

    This is a design-time inference only.  The owner still approves the
    Research Contract, and no value read here is promoted to experiment
    evidence.
    """

    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    root = Path(project.source_root or "").resolve()
    if not root.is_dir():
        return frame
    candidates = [root / "README.md", root / "README.txt"]
    identifier = str(
        (selected_implementation or {}).get("canonical_identifier") or ""
    ).strip()
    if identifier:
        candidates.insert(0, root / identifier)
    excerpts: list[str] = []
    sources: list[str] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if not resolved.is_file() or resolved.stat().st_size > 1_000_000:
            continue
        try:
            excerpts.append(resolved.read_text(encoding="utf-8", errors="ignore"))
            sources.append(resolved.relative_to(root).as_posix())
        except OSError:
            continue
    combined_text = "\n".join(
        [scope.research_question, scope.candidate_contribution, *excerpts]
    )
    inferred = _infer_primary_outcome(
        "",
        combined_text,
    )
    updated = dict(frame)
    if (
        str(frame.get("primary_outcome") or "").strip().casefold()
        in _PRIMARY_OUTCOME_PLACEHOLDERS
        and inferred.casefold() not in _PRIMARY_OUTCOME_PLACEHOLDERS
    ):
        updated["primary_outcome"] = inferred
        updated["primary_outcome_source"] = {
            "kind": "local_project_design_cue",
            "paths": sources,
            "scientific_evidence_eligible": False,
        }
        if updated.get("output_schema"):
            updated["output_schema"] = {
                **dict(updated["output_schema"]),
                "metric_field": _metric_identifier(inferred),
            }

    folded = combined_text.casefold()
    paired_design = (
        "paired" in folded
        and "baseline" in folded
        and "treatment" in folded
    )
    threshold_match = re.search(
        r"(?:threshold|improvement(?:\s+of)?\s+at\s+least)"
        r"[^0-9]{0,48}(0(?:\.\d+)?|1(?:\.0+)?)",
        folded,
    )
    if paired_design and threshold_match:
        threshold = float(threshold_match.group(1))
        baseline_match = re.search(
            r"baseline\s*:\s*([^\n;]+)",
            combined_text,
            flags=re.IGNORECASE,
        )
        treatment_match = re.search(
            r"treatment\s*:\s*([^\n;]+)",
            combined_text,
            flags=re.IGNORECASE,
        )
        if baseline_match:
            updated["comparator"] = baseline_match.group(1).strip(" .`")
        if treatment_match:
            updated["intervention"] = treatment_match.group(1).strip(" .`")
        updated["experiment_profile"] = (
            Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1.value
        )
        updated["minimum_meaningful_effect"] = threshold
        updated["success_threshold"] = threshold
        updated["statistical_rules"] = {
            "method": "paired_mean_difference",
            "effect_threshold": threshold,
            "missing_cell_policy": "inconclusive",
            "confidence_level": 0.95,
        }
        updated["baseline_experiment_id"] = "baseline-v1"
        updated["baseline_action_id"] = "action-baseline"
        updated["treatment_experiment_id"] = "treatment-v1"
        updated["treatment_action_id"] = "action-treatment"
        updated["splits"] = ["formal"]
        updated["output_schema"] = {
            "format": "json",
            "raw_fields": [
                "sample_id",
                "prediction",
                "target_reference",
            ],
            "metric_field": _metric_identifier(
                str(updated.get("primary_outcome") or inferred)
            ),
            "denominator_field": "denominator",
            "sample_id_field": "sample_ids",
        }
        number_words = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
        }

        def declared_count(label: str, default: int) -> int:
            match = re.search(
                rf"\b(\d+|{'|'.join(number_words)})\s+{label}s?\b",
                folded,
            )
            if not match:
                return default
            raw = match.group(1)
            return int(raw) if raw.isdigit() else number_words[raw]

        task_count = declared_count("task", 1)
        seed_count = declared_count("seed", 1)
        updated["tasks"] = [
            f"task-{index + 1}" for index in range(task_count)
        ]
        updated["seeds"] = list(range(1, seed_count + 1))
        row_count = declared_count("row", 0)
        if row_count:
            updated["denominator"] = (
                f"all {row_count} frozen formal rows"
            )
        updated["profile_source"] = {
            "kind": "explicit_local_project_design",
            "paths": sources,
            "scientific_evidence_eligible": False,
        }
    if (
        updated.get("experiment_profile")
        and not updated.get("statistical_rules")
    ):
        metric_name = _metric_identifier(
            str(updated.get("primary_outcome") or inferred)
        )
        if any(
            cue in metric_name
            for cue in (
                "accuracy",
                "rate",
                "recall",
                "precision",
                "coverage",
            )
        ):
            updated.update(
                {
                    "minimum_meaningful_effect": 0.05,
                    "success_threshold": 0.05,
                    "threshold_basis": (
                        "Stage 2 scale-based default for a bounded 0–1 "
                        "primary metric; owner approval is required."
                    ),
                    "statistical_rules": {
                        "method": "paired_mean_difference",
                        "effect_threshold": 0.05,
                        "missing_cell_policy": "inconclusive",
                        "confidence_level": 0.95,
                    },
                }
            )
    if updated == frame:
        return frame
    return {
        **updated,
    }


def _metric_identifier(primary_outcome: str) -> str:
    folded = primary_outcome.casefold()
    known = (
        ("top-k", "top_k_event_identification_rate"),
        ("identification rate", "identification_rate"),
        ("accuracy", "accuracy"),
        ("success rate", "success_rate"),
        ("error rate", "error_rate"),
        ("failure rate", "failure_rate"),
        ("latency", "latency"),
        ("recall", "recall"),
        ("precision", "precision"),
        ("coverage rate", "coverage_rate"),
    )
    for cue, identifier in known:
        if cue in folded:
            return identifier
    identifier = re.sub(r"[^a-z0-9]+", "_", folded).strip("_")
    return identifier or "primary_outcome"


def _comparison_frame_complete(frame: dict[str, Any]) -> bool:
    return bool(
        all(
            str(frame.get(field, "")).strip()
            for field in ("comparator", "intervention", "primary_outcome")
        )
        and str(frame.get("primary_outcome", "")).strip().casefold()
        not in _PRIMARY_OUTCOME_PLACEHOLDERS
    )


class DecisionRules(StrictModel):
    schema_version: int = 1
    supported: list[str]
    refuted: list[str]
    mixed: list[str]
    inconclusive: list[str]
    unverifiable: list[str]
    primary_metric_priority: str
    multi_metric_conflict_rule: str
    effect_size_significance_conflict_rule: str
    baseline_not_reproduced_rule: str
    partial_run_failure_rule: str
    missing_artifact_rule: str
    data_contamination_rule: str
    protocol_deviation_rule: str
    authority_order: list[str]
    frozen: bool = False


class CheckResult(StrictModel):
    check_id: str
    status: str
    evidence: list[str] = Field(default_factory=list)
    detail: str
    evidence_status: EvidenceStatus = EvidenceStatus.UNKNOWN


class Stage2GateReport(StrictModel):
    schema_version: int = 1
    study_id: str
    research_contract_version: int = Field(ge=1)
    status: Stage2GateStatus
    freeze_conditions: list[CheckResult]
    completed_steps: list[str]
    incomplete_steps: list[str]
    blockers: list[str]
    warnings: list[str]
    required_user_decisions: list[str]
    allowed_next_actions: list[str]
    prohibited_next_actions: list[str]
    scientific_validity_report: dict[str, Any] | None = None
    generated_at: str = Field(default_factory=utc_now)


class ProtocolAmendment(StrictModel):
    schema_version: int = 1
    amendment_id: str
    study_id: str
    sequence: int = Field(ge=1)
    research_contract_version: int = Field(ge=1)
    changed_at: str = Field(default_factory=utc_now)
    treatment_results_viewed_before_change: bool
    reason: str = Field(min_length=3, max_length=4_000)
    changes: dict[str, Any]
    impact_scope: list[str]
    requires_rerun: bool
    exploratory_downgrade: bool
    requires_owner_reapproval: bool
    status: str = "proposed"


def _blocked(message: str, *, kind: str = "requirement") -> Exception:
    from .workflow_scheduler import BlockedStepError

    return BlockedStepError(message, kind=kind)


def _safe_int_list(value: Any) -> list[int]:
    """Normalize untrusted metadata without turning one bad field into a failure."""

    if not isinstance(value, (list, tuple)):
        return []
    normalized: list[int] = []
    for item in value:
        try:
            normalized.append(int(item))
        except (TypeError, ValueError, OverflowError):
            continue
    return normalized


def _safe_positive_int(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if normalized >= 1 else None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


_SANDBOX_IGNORED_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


def _stage_two_sandbox_ignore(
    directory: str, names: list[str]
) -> set[str]:
    return {
        name
        for name in names
        if (
            name in _SANDBOX_IGNORED_DIRECTORIES
            or is_secret_path(name)
            or (Path(directory) / name).is_symlink()
        )
    }


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _stage2_dir(repository: WorkflowRepository, study_id: str) -> Path:
    return repository.root / "studies" / study_id / "stage2"


def _versioned_artifact_name(name: str, version: int) -> str:
    if version <= 1:
        return name
    path = Path(name)
    return f"{path.stem}.v{version}{path.suffix}"


def _write_named_artifact(
    context: "StepContext",
    name: str,
    value: Any,
    *,
    role: ArtifactRole = ArtifactRole.OTHER,
    status: ArtifactStatus = ArtifactStatus.FROZEN,
) -> str:
    path = _stage2_dir(context.repository, context.study_id) / name
    if path.exists():
        raise ValueError(f"Stage 2 artifact is immutable: {name}")
    if name.endswith(".jsonl"):
        rows = value if isinstance(value, list) else [value]
        _atomic_text(
            path,
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows
            ),
        )
    elif name.endswith((".md", ".py")):
        _atomic_text(path, str(value))
    else:
        write_json_atomic(path, value)
    artifact = context.repository.register_artifact(
        context.study_id,
        str(path),
        sha256_file(path),
        kind=f"stage2_{re.sub(r'[^a-z0-9_]+', '_', path.stem.casefold())}",
        role=role,
        status=status,
    )
    return artifact.artifact_id


def _stage2_lock_artifact_name(
    stage_dir: Path,
    logical_name: str,
    contract_version: int,
) -> str:
    """Choose an immutable filename without overwriting an earlier contract.

    The first frozen contract keeps the legacy canonical filenames for read
    compatibility.  A later Research Contract version receives versioned lock
    files.  Retrying the same version resolves to the same filename.
    """

    canonical = stage_dir / logical_name
    if not canonical.is_file():
        return logical_name
    try:
        existing = read_json(canonical)
    except (OSError, ValueError, TypeError):
        existing = {}
    if int(existing.get("research_contract_version") or 0) == contract_version:
        return logical_name
    return _versioned_artifact_name(logical_name, contract_version)


def _reuse_stage2_lock_artifact(
    context: "StepContext",
    path: Path,
    payload: dict[str, Any],
) -> str | None:
    """Return an existing identical lock registration for a safe retry."""

    if not path.is_file():
        return None
    existing = read_json(path)
    identity_fields = (
        "study_id",
        "scope_version",
        "research_contract_version",
        "lock_name",
        "source_artifact",
        "source_sha256",
        "immutable",
    )
    if any(existing.get(key) != payload.get(key) for key in identity_fields):
        raise ValueError(f"Stage 2 artifact is immutable: {path.name}")
    digest = sha256_file(path)
    matches = [
        item
        for item in context.repository.list_artifacts(context.study_id)
        if Path(item.path).resolve() == path.resolve()
        and item.sha256 == digest
        and item.status is ArtifactStatus.FROZEN
    ]
    if not matches:
        raise ValueError(
            f"Stage 2 lock exists without a frozen artifact record: {path.name}"
        )
    return max(matches, key=lambda item: item.created_at).artifact_id


def _ancestor_result(context: "StepContext", step_type: str) -> dict[str, Any]:
    return context.result(step_type)


def _optional_ancestor_result(
    context: "StepContext", step_type: str
) -> dict[str, Any]:
    from .workflow_scheduler import BlockedStepError

    try:
        return context.result(step_type)
    except (BlockedStepError, FileNotFoundError, KeyError, ValueError):
        return {}


def _input_check(context: "StepContext") -> dict[str, Any]:
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    scope = context.repository.latest_scope_contract(context.study_id)
    scan = _optional_ancestor_result(context, "project_scan")
    portfolio = _optional_ancestor_result(context, "discovery_portfolio")
    evidence = _optional_ancestor_result(context, "evidence_chain_detection")
    scanned_resources = list(scan.get("resources", []))
    historical_results = [
        str(item.get("path"))
        for item in scanned_resources
        if any(
            token in str(item.get("path", "")).casefold()
            for token in ("output", "result", "experiment", "run")
        )
    ]
    selection_bias_risks = [
        {
            "path": path,
            "risk": (
                "Historical result content may influence topic, metric, "
                "threshold, seed, or subset selection."
            ),
            "allowed_use": "background or explicitly labeled prior evidence only",
        }
        for path in historical_results
    ]
    checks = {
        "project_exists": True,
        "study_exists": True,
        "computational_research_supported": study.support_level.value == "formal",
        "direction_scope_frozen": bool(
            scope
            and scope.status is ArtifactStatus.FROZEN
            and scope.contract_level == "direction"
        ),
        "source_root_available": bool(
            project.source_root and Path(project.source_root).is_dir()
        ),
        "direction_is_explicit": bool(scope and scope.direction.strip()),
        "allowed_scope_is_explicit": bool(scope and scope.scope_in),
        "prohibited_scope_is_explicit": bool(scope and scope.scope_out),
        # A local project scan is mandatory for project-to-paper, but the
        # idea-to-paper entry deliberately permits a resource-empty start.
        # Stage 2 must then discover or propose external/buildable resources
        # and verify them through the feasibility MVP before Stage 3.
        "project_scan_available": (
            bool(scan)
            if study.entry_mode is EntryMode.PROJECT_TO_PAPER
            else True
        ),
    }
    missing = [name for name, passed in checks.items() if not passed]
    payload = {
        "schema_version": 1,
        "study_id": context.study_id,
        "checks": checks,
        "missing": missing,
        "status": "ready" if not missing else "blocked",
        "allowed_exploration_scope": list(scope.scope_in) if scope else [],
        "prohibited_deviations": list(scope.scope_out) if scope else [],
        "owner_excluded_directions": list(scope.scope_out) if scope else [],
        "project_material_count": len(scanned_resources),
        "project_material_completeness": (
            "partial"
            if scanned_resources
            else "unknown"
        ),
        "historical_result_paths": historical_results,
        "historical_results_allowed_use": (
            "background reference only unless explicitly bound by a frozen "
            "verified evidence chain"
        ),
        "selection_bias_risks": selection_bias_risks,
        "stage_one_portfolio_available": bool(portfolio),
        "stage_one_evidence_map_available": bool(evidence),
        "unknown_inputs": [
            name
            for name, available in {
                "candidate_directions.json": bool(portfolio),
                "evidence_map.json": bool(evidence),
                "project material completeness": bool(scanned_resources),
            }.items()
            if not available
        ],
        "generated_at": utc_now(),
    }
    report_name = (
        "stage2_input_check.json"
        if not missing
        else f"stage2_input_check.attempt-{context.step.attempt:03d}.json"
    )
    artifact_id = _write_named_artifact(
        context, report_name, payload, role=ArtifactRole.AUDIT
    )
    direction_artifact_id = None
    if scope is not None and not missing:
        direction_artifact_id = _write_named_artifact(
            context,
            "direction_contract.json",
            {
                **scope.model_dump(mode="json"),
                "compatibility_projection": (
                    "ScopeContractVersion(contract_level=direction)"
                ),
            },
            role=ArtifactRole.PROTOCOL,
        )
    if missing:
        raise _blocked(
            "Stage 2 inputs are incomplete: " + ", ".join(missing),
            kind="stage2_input_incomplete",
        )
    return {
        **payload,
        "_workflow_output_artifact_ids": [
            artifact_id,
            *([direction_artifact_id] if direction_artifact_id else []),
        ],
    }


def _method_investigation(context: "StepContext") -> dict[str, Any]:
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    scope = context.repository.latest_scope_contract(context.study_id)
    if scope is None or scope.status is not ArtifactStatus.FROZEN:
        raise _blocked("Stage 2 method research requires a frozen direction Scope.")

    from .retrieval.domain.models import (
        ContractRef,
        NetworkMode,
        ResourceType,
        RetrievalBudget,
        RetrievalPhase,
    )
    from .retrieval.interfaces.service import RetrievalGateway

    gateway = RetrievalGateway(str(context.repository.root))
    policy = gateway.get_policy(project.project_id)
    providers = [
        provider
        for provider in (
            "paper_search_mcp",
            "semantic_scholar",
            "crossref",
            "github",
            "huggingface",
            "codex_native_web_search",
        )
        if provider in policy.allowed_providers
    ]
    retrieval: dict[str, Any]
    resources: list[Any] = []
    if policy.mode is NetworkMode.OFFLINE or not providers:
        retrieval = {
            "status": "not_authorized",
            "network_mode": policy.mode.value,
            "providers": providers,
            "reason": "Protocol retrieval is offline or has no approved providers.",
        }
    else:
        request = gateway.plan(
            project_id=project.project_id,
            study_id=context.study_id,
            phase=RetrievalPhase.PROTOCOL,
            step_instance_id=context.step.step_instance_id,
            purpose="protocol_grounding",
            queries=[
                scope.research_question,
                f"{scope.direction} experimental methods baseline evaluation",
                f"{scope.direction} dataset version schema license",
                f"{scope.direction} official implementation toolkit release license",
                f"{scope.direction} benchmark protocol metric reproducibility",
            ],
            providers=providers,
            resource_types=[
                ResourceType.PUBLICATION,
                ResourceType.PREPRINT,
                ResourceType.CODE_REPOSITORY,
                ResourceType.DATASET,
                ResourceType.BENCHMARK,
                ResourceType.DOCUMENTATION,
            ],
            usage_role="protocol_grounding",
            budget=RetrievalBudget(
                max_queries=min(6, max(1, policy.max_queries or 6)),
                max_results=min(60, max(1, policy.max_results or 60)),
                max_download_bytes=0,
                max_cost=min(1.0, max(0.0, policy.max_cost)),
            ),
            idempotency_key=f"{context.study_id}:stage2:method-investigation:v1",
            contract_refs=[
                ContractRef(
                    contract_type="scope",
                    contract_id=f"{context.study_id}:scope-v{scope.version}",
                    version=scope.version,
                )
            ],
            internal_identifiers=[
                project.title,
                Path(project.source_root).name if project.source_root else "",
            ],
            research_need=(
                "Find prior methods, baselines, datasets, metrics, implementation "
                "references, and reported reproducibility constraints."
            ),
            freshness="live",
            blocked_domains=["scholar.google.com"],
        )
        execution = gateway.run(
            request.request_id,
            target_type="scope_contract",
            target_id=f"{context.study_id}:scope-v{scope.version}",
            target_field="method_grounding",
            relation="justifies",
        )
        retrieval = execution.model_dump()
        if execution.resource_set is not None:
            bindings = [
                gateway.repository.load_binding(binding_id)
                for binding_id in execution.resource_set.binding_ids
            ]
            resources = [
                gateway.repository.load_resource(binding.resource_id)
                for binding in bindings
            ]

    cards: list[MethodCard] = []
    for resource in resources[:40]:
        metadata = resource.metadata
        known: set[str] = set()
        metrics = [
            str(item)
            for item in metadata.get("metrics", [])
            if str(item).strip()
        ]
        for name, value in (
            ("research_question", metadata.get("research_question")),
            ("study_design", metadata.get("study_design")),
            ("population_or_dataset", metadata.get("dataset_identifier")),
            ("baseline", metadata.get("baseline")),
        ):
            if value:
                known.add(name)
        cards.append(
            MethodCard(
                method_id=stable_id("method", context.study_id, resource.resource_id),
                title=resource.title or "Untitled method resource",
                citation_or_identifier=(
                    resource.doi
                    or resource.canonical_identifier
                    or resource.url
                ),
                source_type=resource.resource_type.value,
                research_question=metadata.get("research_question"),
                research_object=metadata.get("research_object"),
                study_design=metadata.get("study_design"),
                population_or_dataset=(
                    resource.dataset_identifier
                    or metadata.get("dataset_identifier")
                ),
                unit_of_analysis=metadata.get("unit_of_analysis"),
                dataset_name_and_version=(
                    resource.dataset_identifier
                    or metadata.get("dataset_name_and_version")
                ),
                dataset_scale=metadata.get("dataset_scale"),
                data_preprocessing=metadata.get("data_preprocessing"),
                train_validation_test_split=(
                    metadata.get("train_validation_test_split")
                    or metadata.get("split_strategy")
                ),
                intervention_or_method=metadata.get("method"),
                model_and_algorithm=metadata.get("model_and_algorithm"),
                model_version=(
                    resource.model_identifier
                    or metadata.get("model_version")
                ),
                hyperparameters=dict(metadata.get("hyperparameters") or {}),
                comparator=metadata.get("comparator"),
                primary_outcome=metadata.get("primary_outcome"),
                secondary_outcomes=[
                    str(item) for item in metadata.get("secondary_outcomes", [])
                ],
                sample_size=metadata.get("sample_size"),
                split_or_sampling_strategy=metadata.get("split_strategy"),
                baseline=metadata.get("baseline"),
                control_group=metadata.get("control_group"),
                experimental_group=metadata.get("experimental_group"),
                metrics=metrics,
                success_threshold=metadata.get("success_threshold"),
                random_seeds=_safe_int_list(metadata.get("random_seeds")),
                repetitions=_safe_positive_int(metadata.get("repetitions")),
                statistical_analysis=metadata.get("statistical_analysis"),
                statistical_test=metadata.get("statistical_test"),
                uncertainty_analysis=metadata.get("uncertainty_analysis"),
                ablation_experiments=[
                    str(item)
                    for item in metadata.get("ablation_experiments", [])
                ],
                robustness_checks=[
                    str(item) for item in metadata.get("robustness_checks", [])
                ],
                leakage_controls=[
                    str(item) for item in metadata.get("leakage_controls", [])
                ],
                code_available=(
                    resource.resource_type.value
                    in {"code_repository", "code_release"}
                ),
                data_available=(
                    resource.resource_type.value == "dataset"
                ),
                environment_available=None,
                model_or_revision=resource.model_identifier or resource.commit,
                compute_reported=metadata.get("compute"),
                hardware=metadata.get("hardware"),
                software_environment=metadata.get("software_environment"),
                code_url=(
                    resource.url
                    if resource.resource_type.value
                    in {"code_repository", "code_release"}
                    else metadata.get("code_url")
                ),
                data_url=(
                    resource.url
                    if resource.resource_type.value == "dataset"
                    else metadata.get("data_url")
                ),
                license=resource.license,
                run_command_public=metadata.get("run_command_public"),
                environment_lock_available=metadata.get(
                    "environment_lock_available"
                ),
                third_party_reproduced=metadata.get(
                    "third_party_reproduced"
                ),
                known_failures_or_controversies=[
                    str(item)
                    for item in metadata.get(
                        "known_failures_or_controversies", []
                    )
                ],
                paper_code_consistency=metadata.get("paper_code_consistency"),
                limitations=[str(item) for item in metadata.get("limitations", [])],
                threats_to_validity=[
                    str(item) for item in metadata.get("threats_to_validity", [])
                ],
                reproducibility_grade=(
                    "B"
                    if resource.commit
                    and resource.metadata_verification_status.value == "verified"
                    else "C"
                    if resource.url or resource.doi
                    else "D"
                ),
                evidence_status=(
                    EvidenceStatus.VERIFIED
                    if resource.metadata_verification_status.value == "verified"
                    else EvidenceStatus.REPORTED
                ),
                resource_id=resource.resource_id,
                fields_unknown=sorted(
                    {
                        "research_question",
                        "study_design",
                        "population_or_dataset",
                        "unit_of_analysis",
                        "primary_outcome",
                        "sample_size",
                        "baseline",
                        "statistical_analysis",
                        "compute_reported",
                    }
                    - known
                ),
                missing_information=sorted(
                    {
                        "research_object",
                        "dataset_name_and_version",
                        "dataset_scale",
                        "data_preprocessing",
                        "train_validation_test_split",
                        "model_and_algorithm",
                        "model_version",
                        "hyperparameters",
                        "control_group",
                        "experimental_group",
                        "success_threshold",
                        "random_seeds",
                        "repetitions",
                        "statistical_test",
                        "uncertainty_analysis",
                        "ablation_experiments",
                        "hardware",
                        "software_environment",
                        "code_url",
                        "data_url",
                        "license",
                        "run_command_public",
                        "environment_lock_available",
                        "third_party_reproduced",
                        "paper_code_consistency",
                    }
                    - {
                        key
                        for key, value in metadata.items()
                        if value not in (None, "", [], {})
                    }
                ),
            )
        )
    baseline_candidates = [
        {
            "method_id": card.method_id,
            "title": card.title,
            "baseline": card.baseline,
            "code_available": card.code_available,
            "reproducibility_grade": card.reproducibility_grade,
            "evidence_status": card.evidence_status.value,
        }
        for card in cards
        if card.baseline or card.code_available
    ]
    summary = (
        "# Stage 2 method investigation\n\n"
        f"- Frozen direction: {scope.direction}\n"
        f"- Method cards: {len(cards)}\n"
        f"- Baseline candidates: {len(baseline_candidates)}\n"
        f"- Retrieval status: {retrieval.get('status') or retrieval.get('run', {}).get('execution_status', 'unknown')}\n\n"
        "Missing fields remain explicitly unknown; retrieved sources are method "
        "grounding and cannot decide a scientific verdict.\n"
    )
    artifact_ids = [
        _write_named_artifact(context, "related_method_cards.jsonl", [
            item.model_dump(mode="json") for item in cards
        ], role=ArtifactRole.LITERATURE_BACKGROUND),
        _write_named_artifact(
            context,
            "research_method_summary.md",
            summary,
            role=ArtifactRole.LITERATURE_BACKGROUND,
        ),
        _write_named_artifact(
            context,
            "baseline_candidates.json",
            {
                "schema_version": 1,
                "candidates": baseline_candidates,
                "retrieval": retrieval,
            },
            role=ArtifactRole.PROTOCOL,
        ),
    ]
    return {
        "method_cards": [item.model_dump(mode="json") for item in cards],
        "baseline_candidates": baseline_candidates,
        "retrieval": retrieval,
        "_workflow_output_artifact_ids": artifact_ids,
    }


def _resource_category(path: str, suffix: str) -> ResourceCategory:
    lowered = path.replace("\\", "/").casefold()
    name = Path(lowered).name
    parts = set(Path(lowered).parts)
    if suffix in {".csv", ".tsv", ".xlsx", ".h5", ".hdf5", ".parquet"}:
        return ResourceCategory.DATA
    if suffix == ".jsonl":
        if (
            "runs" in parts
            or name in {"evidence.jsonl", "events.jsonl", "telemetry.jsonl"}
        ):
            return ResourceCategory.EVALUATION
        if (
            "data" in parts
            or name.startswith(("train", "test", "valid", "dev"))
        ):
            return ResourceCategory.DATA
        return ResourceCategory.OPERATIONAL
    if suffix == ".py":
        if "tests" in parts or "tests_py" in parts or name.startswith("test_"):
            return ResourceCategory.EVALUATION
        if name == "__init__.py":
            return ResourceCategory.SOFTWARE
        if "evaluator" in parts or name.startswith(("evaluate", "metric")):
            return ResourceCategory.EVALUATION
        return ResourceCategory.IMPLEMENTATION
    if suffix in {".ps1", ".sql", ".js", ".ts"}:
        return ResourceCategory.IMPLEMENTATION
    if any(token in lowered for token in ("model", "checkpoint", "weight")):
        return ResourceCategory.MODEL
    if any(token in lowered for token in ("metric", "evaluation", "benchmark")):
        return ResourceCategory.EVALUATION
    if suffix == ".json" and (
        "protocols" in parts
        or "benchmark" in parts
        or "benchmarks" in parts
        or "outputs" in parts
        or "results" in parts
    ):
        # A frozen protocol, benchmark declaration, or machine-readable result
        # constrains evaluation.  Treating every JSON file as a generic
        # software toolkit hid the strongest scientific assets in real
        # projects and caused Stage 2 to select unrelated files instead.
        return ResourceCategory.EVALUATION
    if suffix in {".toml", ".yaml", ".yml", ".json"}:
        return ResourceCategory.SOFTWARE
    return ResourceCategory.OPERATIONAL


def _idea_scaffold_note(
    *,
    entry_mode: EntryMode,
    source_root: Path,
    relative_path: str,
) -> str | None:
    """Identify Forge-generated idea-project files that are not research assets."""
    if entry_mode is not EntryMode.IDEA_TO_PAPER:
        return None
    normalized = relative_path.replace("\\", "/").casefold()
    if normalized in {
        "current_parameters.json",
        "events.jsonl",
        "project.json",
        "state.json",
    }:
        return (
            "Research Forge idea-project metadata or audit scaffolding; it "
            "cannot satisfy a data, evaluation, or implementation requirement."
        )
    if normalized != "experiment/run_experiment.py":
        return None
    candidate = source_root / Path(relative_path)
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if (
        "Replace this deterministic placeholder with the real "
        "training/evaluation call."
    ) in text:
        return (
            "Research Forge generated experiment placeholder; it is retained "
            "for provenance but cannot establish implementation feasibility."
        )
    return None


def _resource_inventory(context: "StepContext") -> dict[str, Any]:
    scan = _optional_ancestor_result(context, "project_scan")
    scope = context.repository.latest_scope_contract(context.study_id)
    academic_concepts = (
        {
            str(item).casefold()
            for item in scope.field_diff.get("academic_concepts", [])
        }
        if scope is not None and isinstance(scope.field_diff, dict)
        else set()
    )
    document_retrieval_study = bool(
        {
            "technical documentation retrieval",
            "task-oriented information access",
            "troubleshooting question answering",
        }
        & academic_concepts
    )
    project = context.repository.load_project(
        context.repository.load_study(context.study_id).project_id
    )
    study = context.repository.load_study(context.study_id)
    source_root = Path(project.source_root or "")
    records: list[ResourceRecord] = []
    raw_by_path = {
        str(item["path"]): item for item in scan.get("resources", [])
    }
    for item in scan.get("resources", []):
        relative_path = str(item["path"])
        scaffold_note = _idea_scaffold_note(
            entry_mode=study.entry_mode,
            source_root=source_root,
            relative_path=relative_path,
        )
        category = (
            ResourceCategory.OPERATIONAL
            if scaffold_note
            else _resource_category(relative_path, str(item["suffix"]))
        )
        if document_retrieval_study and str(item["suffix"]) == ".pdf":
            category = ResourceCategory.DATA
        records.append(
            ResourceRecord(
                resource_id=stable_id(
                    "resource", context.study_id, item["path"], item["sha256"]
                ),
                category=category,
                name=Path(str(item["path"])).name,
                location=str(item["path"]),
                content_hash=str(item["sha256"]),
                availability=AvailabilityStatus.AVAILABLE,
                validation=ValidationStatus.VALIDATED,
                blocking_level=(
                    BlockingLevel.BLOCKING
                    if category
                    in {
                        ResourceCategory.DATA,
                        ResourceCategory.IMPLEMENTATION,
                        ResourceCategory.EVALUATION,
                    }
                    else BlockingLevel.OPTIONAL
                ),
                license_status=LicenseStatus.UNKNOWN,
                acquisition_method="read_only_project_scan",
                evidence_status=EvidenceStatus.VERIFIED,
                notes=[scaffold_note] if scaffold_note else [],
            )
        )
    hdf5_rows = scan.get("hdf5_metadata", [])
    recorded_locations = {str(item.location) for item in records}
    for row in hdf5_rows:
        location = str(row.get("path") or "")
        metadata_hash = str(row.get("metadata_sha256") or "")
        if (
            not location
            or location in recorded_locations
            or not re.fullmatch(r"[a-f0-9]{64}", metadata_hash)
        ):
            continue
        readable = row.get("readable") is True
        records.append(
            ResourceRecord(
                resource_id=stable_id(
                    "resource",
                    context.study_id,
                    location,
                    metadata_hash,
                ),
                category=ResourceCategory.DATA,
                name=Path(location).name,
                location=location,
                metadata_hash=metadata_hash,
                availability=(
                    AvailabilityStatus.AVAILABLE
                    if readable
                    else AvailabilityStatus.AVAILABLE_UNVERIFIED
                ),
                validation=(
                    ValidationStatus.PARTIAL
                    if readable
                    else ValidationStatus.UNVALIDATED
                ),
                blocking_level=BlockingLevel.BLOCKING,
                license_status=LicenseStatus.UNKNOWN,
                acquisition_method="read_only_hdf5_metadata_scan",
                evidence_status=(
                    EvidenceStatus.VERIFIED
                    if readable
                    else EvidenceStatus.UNKNOWN
                ),
                notes=[
                    "Metadata-only binding; Stage 3 must freeze the full file "
                    "or an owner-approved immutable dataset snapshot."
                ],
            )
        )
        recorded_locations.add(location)
    hdf5_by_path = {
        str(item.get("path")): item
        for item in hdf5_rows
    }
    data_records = []
    code_records = []
    for item in records:
        raw = raw_by_path.get(str(item.location), {})
        hdf5 = hdf5_by_path.get(str(item.location), {})
        common = {
            **item.model_dump(mode="json"),
            "file_type": raw.get("suffix")
            or Path(str(item.location)).suffix.casefold(),
            "size_bytes": raw.get("size_bytes", hdf5.get("size_bytes")),
            "modified_at": raw.get("modified_at", hdf5.get("modified_at")),
        }
        if item.category is ResourceCategory.DATA:
            data_records.append(
                {
                    **common,
                    "schema": (
                        {
                            "root_keys": hdf5.get("root_keys", []),
                            "datasets": hdf5.get("datasets", []),
                            "mask_keys": hdf5.get("mask_keys", []),
                            "representation_family": hdf5.get(
                                "representation_family"
                            ),
                            "task": hdf5.get("task"),
                        }
                        if hdf5
                        else "unknown"
                    ),
                    "sample_count": hdf5.get("sample_count", "unknown"),
                    "demo_count": hdf5.get("demo_count", "unknown"),
                    "binding_level": hdf5.get(
                        "binding_level", "content_sha256"
                    ),
                    "field_descriptions": [],
                    "label_origin": "unknown",
                    "time_range": "unknown",
                    "missing_values": "unknown",
                    "duplicate_values": "unknown",
                    "outliers": "unknown",
                    "data_origin": "local_project_bundle",
                    "generation_process": "unknown",
                    "cleaning_process": "unknown",
                    "version": "content_sha256",
                    "privacy_status": "unknown",
                    "sensitive_fields": [],
                    "external_upload_allowed": False,
                    "train_test_leakage_risk": "unverified",
                    "contains_historical_experiment_outputs": (
                        "output" in str(item.location).casefold()
                        or "result" in str(item.location).casefold()
                    ),
                    "traceable_to_raw_data": "unknown",
                }
            )
        elif item.category is ResourceCategory.IMPLEMENTATION:
            code_records.append(
                {
                    **common,
                    "entry_point": "unknown",
                    "tests": "unknown",
                    "dependency_lock": "unknown",
                    "runtime_command": "unknown",
                }
            )
    git_state: dict[str, Any] = {
        "is_git_repository": False,
        "commit": None,
        "branch": None,
        "uncommitted_changes": "unknown",
        "evidence_status": "unknown",
    }
    if source_root.is_dir() and (source_root / ".git").exists():
        try:
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=source_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
            dirty = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=source_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout
            git_state = {
                "is_git_repository": True,
                "commit": commit,
                "branch": branch or None,
                "uncommitted_changes": bool(dirty.strip()),
                "evidence_status": "verified",
            }
        except (OSError, subprocess.SubprocessError):
            git_state["evidence_status"] = "unknown"
    gpu = shutil.which("nvidia-smi")
    gpu_summary: dict[str, Any] = {"available": False, "evidence_status": "unknown"}
    if gpu:
        try:
            probe = subprocess.run(
                [
                    gpu,
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if probe.returncode == 0:
                gpu_summary = {
                    "available": True,
                    "devices": [
                        line.strip()
                        for line in probe.stdout.splitlines()
                        if line.strip()
                    ],
                    "evidence_status": "verified",
                }
        except (OSError, subprocess.SubprocessError):
            pass
    compute = {
        "schema_version": 1,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "memory_bytes": None,
        "gpu": gpu_summary,
        "evidence_status": "verified",
    }
    quality = {
        "schema_version": 1,
        "data_resource_count": len(data_records),
        "hdf5_resources": hdf5_rows,
        "pdf_resources": scan.get("pdf_metadata", []),
        "content_quality_validated": False,
        "unknowns": [
            "missingness",
            "label quality",
            "duplicate units",
            "distribution shift",
        ],
    }
    provenance = {
        "schema_version": 1,
        "source_root": project.source_root,
        "scan_step_id": next(
            (
                step.step_instance_id
                for step in context.repository.list_steps(context.study_id)
                if step.step_type == "project_scan"
            ),
            None,
        ),
        "resource_hashes_verified": True,
        "external_origin_and_license_verified": False,
    }
    artifact_ids = [
        _write_named_artifact(context, "local_data_manifest.json", {
            "schema_version": 1,
            "resources": data_records,
            "hdf5_metadata": hdf5_rows,
        }),
        _write_named_artifact(
            context,
            "local_code_manifest.json",
            {
                "schema_version": 1,
                "resources": code_records,
                "repository_structure": sorted(
                    {
                        Path(str(item.location)).parts[0]
                        for item in records
                        if item.location
                    }
                ),
                "git": git_state,
                "license_files": [
                    item["path"]
                    for item in scan.get("resources", [])
                    if Path(str(item.get("path", ""))).name.casefold()
                    .startswith(("license", "copying"))
                ],
                "dependency_files": [
                    item["path"]
                    for item in scan.get("resources", [])
                    if Path(str(item.get("path", ""))).name.casefold()
                    in {
                        "pyproject.toml",
                        "requirements.txt",
                        "uv.lock",
                        "poetry.lock",
                        "package-lock.json",
                        "pnpm-lock.yaml",
                        "environment.yml",
                    }
                ],
                "configuration_files": [
                    item["path"]
                    for item in scan.get("resources", [])
                    if str(item.get("suffix", "")).casefold()
                    in {".json", ".toml", ".yaml", ".yml"}
                ],
                "test_files": [
                    item["path"]
                    for item in scan.get("resources", [])
                    if "test" in str(item.get("path", "")).casefold()
                ],
                "historical_evidence_files": [
                    item["path"]
                    for item in scan.get("resources", [])
                    if any(
                        token in str(item.get("path", "")).casefold()
                        for token in ("output", "result", "experiment", "run")
                    )
                ],
                "scientific_question_impact": "requires protocol review",
                "protocol_impact": "requires binding before formal execution",
                "requires_stage_one_return": False,
            },
        ),
        _write_named_artifact(context, "compute_environment.json", compute),
        _write_named_artifact(context, "resource_inventory.json", {
            "schema_version": 1,
            "resources": [item.model_dump(mode="json") for item in records],
        }),
        _write_named_artifact(context, "data_quality_report.json", quality),
        _write_named_artifact(context, "data_provenance.json", provenance),
    ]
    return {
        "resources": [item.model_dump(mode="json") for item in records],
        "data_quality": quality,
        "data_provenance": provenance,
        "compute": compute,
        "git": git_state,
        "_workflow_output_artifact_ids": artifact_ids,
    }


def _data_boundary(context: "StepContext") -> dict[str, Any]:
    inventory = _ancestor_result(context, "inventory_research_resources")
    data = [
        item
        for item in inventory["resources"]
        if item["category"] == ResourceCategory.DATA.value
    ]
    names = " ".join(str(item.get("location", "")).casefold() for item in data)
    split_groups: dict[str, set[str]] = {}
    for item in data:
        location = str(item.get("location", "")).replace("\\", "/")
        lowered_name = Path(location).name.casefold()
        split = (
            "train"
            if "train" in lowered_name
            else "validation"
            if any(token in lowered_name for token in ("valid", "dev"))
            else "test"
            if "test" in lowered_name
            else ""
        )
        if split:
            split_groups.setdefault(Path(location).parent.as_posix(), set()).add(
                split
            )
    split_evidence = {
        "train": "train" in names,
        "validation": any(token in names for token in ("valid", "dev")),
        "test": "test" in names,
    }
    boundary = {
        "schema_version": 1,
        "unit_of_analysis": "unknown",
        "target_population": "unknown",
        "population_or_corpus": [item["location"] for item in data],
        "data_resource_ids": [item["resource_id"] for item in data],
        "inclusion_rules": [],
        "exclusion_rules": [],
        "sampling_frame": "local project resources",
        "split_policy": split_evidence,
        "coherent_split_groups": [
            {"parent": parent, "splits": sorted(splits)}
            for parent, splits in sorted(split_groups.items())
        ],
        "train_boundary_verified": split_evidence["train"],
        "validation_boundary_verified": split_evidence["validation"],
        "test_boundary_verified": split_evidence["test"],
        "time_boundary": "unknown",
        "denominator": "unknown",
        "label_origin": "unknown",
        "historical_experiment_artifacts": [
            item["location"]
            for item in data
            if any(
                token in str(item.get("location", "")).casefold()
                for token in ("output", "result", "experiment", "run")
            )
        ],
        "permitted_use": (
            "local Stage 2 feasibility checks and an owner-authorized baseline; "
            "external upload is prohibited by default"
        ),
        "status": "partial" if data else "unknown",
        "evidence_status": "inferred" if data else "unknown",
    }
    risks = []
    if data and not all(split_evidence.values()):
        risks.append(
            {
                "risk": "split_boundary_unverified",
                "severity": "blocking",
                "detail": "Distinct train/validation/test boundaries were not all verified.",
            }
        )
    risks.extend(
        [
            {
                "risk": "duplicate_unit_leakage",
                "severity": "strengthening",
                "detail": "Duplicate or related units across splits have not been tested.",
            },
            {
                "risk": "temporal_leakage",
                "severity": "strengthening",
                "detail": "Temporal ordering is unknown.",
            },
            {
                "risk": "target_or_label_leakage",
                "severity": "blocking",
                "detail": "Feature-to-target leakage has not been deterministically checked.",
            },
            {
                "risk": "group_or_identity_leakage",
                "severity": "strengthening",
                "detail": "Related entities across splits have not been ruled out.",
            },
            {
                "risk": "preprocessing_fit_leakage",
                "severity": "strengthening",
                "detail": "Preprocessing fit boundaries are not yet verified.",
            },
        ]
    )
    if boundary["historical_experiment_artifacts"]:
        risks.append(
            {
                "risk": "historical_result_selection_bias",
                "severity": "strengthening",
                "detail": (
                    "Historical result artifacts exist and cannot be used to "
                    "choose formal metrics, thresholds, seeds, or subsets."
                ),
            }
        )
    leakage = {
        "schema_version": 1,
        "risks": risks,
        "blocking_count": sum(item["severity"] == "blocking" for item in risks),
        "validated": False,
    }
    artifact_ids = [
        _write_named_artifact(context, "data_boundary.json", boundary),
        _write_named_artifact(
            context,
            "leakage_risk_report.json",
            leakage,
            role=ArtifactRole.AUDIT,
        ),
    ]
    return {
        "data_boundary": boundary,
        "leakage_risks": leakage,
        "_workflow_output_artifact_ids": artifact_ids,
    }


def _resource_gaps(context: "StepContext") -> dict[str, Any]:
    inventory = _ancestor_result(context, "inventory_research_resources")
    methods = _ancestor_result(context, "investigate_related_methods")
    boundary = _ancestor_result(context, "define_data_boundary")
    categories = {item["category"] for item in inventory["resources"]}
    gaps: list[dict[str, Any]] = []

    def add(
        gap_id: str,
        category: ResourceCategory,
        detail: str,
        *,
        availability: AvailabilityStatus,
        blocking: BlockingLevel,
    ) -> None:
        gaps.append(
            {
                "gap_id": gap_id,
                "category": category.value,
                "detail": detail,
                "availability": availability.value,
                "validation": ValidationStatus.UNVALIDATED.value,
                "blocking_level": blocking.value,
                "license_status": LicenseStatus.UNKNOWN.value,
                "acquisition_method": (
                    "owner supplies or approves a bounded substitute"
                ),
                "evidence_status": EvidenceStatus.UNKNOWN.value,
            }
        )

    if ResourceCategory.DATA.value not in categories:
        add(
            "gap-data",
            ResourceCategory.DATA,
            "No machine-readable local data resource was verified.",
            availability=AvailabilityStatus.REQUESTABLE,
            blocking=BlockingLevel.BLOCKING,
        )
    if ResourceCategory.IMPLEMENTATION.value not in categories:
        add(
            "gap-implementation",
            ResourceCategory.IMPLEMENTATION,
            "No executable implementation was verified.",
            availability=AvailabilityStatus.REQUESTABLE,
            blocking=BlockingLevel.BLOCKING,
        )
    if not methods["method_cards"]:
        add(
            "gap-method-grounding",
            ResourceCategory.OPERATIONAL,
            "No protocol method cards are available under the current retrieval policy.",
            availability=AvailabilityStatus.REQUESTABLE,
            blocking=BlockingLevel.STRENGTHENING,
        )
    if boundary["leakage_risks"]["blocking_count"]:
        add(
            "gap-leakage-controls",
            ResourceCategory.EVALUATION,
            "Blocking data-leakage checks remain unresolved.",
            availability=AvailabilityStatus.SUBSTITUTABLE,
            blocking=BlockingLevel.BLOCKING,
        )
    requests = [
        {
            "request_id": stable_id("request", context.study_id, item["gap_id"]),
            "gap_id": item["gap_id"],
            "requested_item": item["detail"],
            "acceptable_substitution": (
                "A versioned, hashed local artifact satisfying the same contract field."
            ),
            "requires_owner_action": item["blocking_level"] == "blocking",
        }
        for item in gaps
    ]
    report = {
        "schema_version": 1,
        "gaps": gaps,
        "blocking_count": sum(
            item["blocking_level"] == "blocking" for item in gaps
        ),
        "strengthening_count": sum(
            item["blocking_level"] == "strengthening" for item in gaps
        ),
    }
    acquisition_plan = (
        "# Resource acquisition plan\n\n"
        "This plan records requested or substitutable resources. It does not "
        "claim that any request or email has been sent.\n\n"
    )
    if gaps:
        acquisition_plan += "\n".join(
            (
                f"## {item['gap_id']}\n\n"
                f"- Need: {item['detail']}\n"
                f"- Blocking level: {item['blocking_level']}\n"
                f"- Availability: {item['availability']}\n"
                f"- License status: {item['license_status']}\n"
                f"- Acquisition path: {item['acquisition_method']}\n"
                "- Acceptable delivery: versioned repository, archive, "
                "container image, or explicitly approved read-only access.\n"
            )
            for item in gaps
        )
    else:
        acquisition_plan += (
            "No missing resource was identified by the current deterministic "
            "inventory. This is not evidence that every scientific resource "
            "has been independently validated.\n"
        )
    artifact_ids = [
        _write_named_artifact(
            context, "resource_gap_report.json", report, role=ArtifactRole.AUDIT
        ),
        _write_named_artifact(context, "resource_request_cards.jsonl", requests),
        _write_named_artifact(
            context, "resource_acquisition_plan.md", acquisition_plan
        ),
    ]
    return {
        "resource_gaps": report,
        "request_cards": requests,
        "_workflow_output_artifact_ids": artifact_ids,
    }


def _resource_requirements(context: "StepContext") -> dict[str, Any]:
    """Translate the frozen direction and diagnosed gaps into search contracts."""

    scope = context.repository.latest_scope_contract(context.study_id)
    if scope is None or scope.status is not ArtifactStatus.FROZEN:
        raise _blocked("Concrete resource requirements require a frozen Scope.")
    gaps = _ancestor_result(context, "diagnose_resource_gaps")["resource_gaps"]
    gap_categories = {str(item["category"]) for item in gaps["gaps"]}
    common_terms = [
        scope.direction,
        scope.research_question,
        scope.population_or_corpus or "",
        scope.primary_outcome or "",
    ]
    requirements = [
        ResourceRequirement(
            requirement_id=stable_id(
                "resource-need", context.study_id, scope.version, "dataset"
            ),
            need_type=ResourceNeedType.DATASET,
            purpose="Provide the bounded observations used by the primary analysis.",
            contract_field="data_boundary.approved_dataset_ids",
            blocking_level=BlockingLevel.BLOCKING,
            required_capabilities=[
                "machine-readable observations",
                "stable sample identifiers",
                "documented split or sampling boundary",
            ],
            accepted_formats=[
                "csv",
                "tsv",
                "parquet",
                "hdf5",
                "jsonl",
                "pdf_document_corpus",
            ],
            query_terms=[
                *common_terms,
                "dataset benchmark data card version",
            ],
        ),
        ResourceRequirement(
            requirement_id=stable_id(
                "resource-need", context.study_id, scope.version, "implementation"
            ),
            need_type=ResourceNeedType.IMPLEMENTATION,
            purpose="Provide an auditable baseline or treatment implementation.",
            contract_field="runtime_binding.approved_implementation_ids",
            blocking_level=BlockingLevel.BLOCKING,
            required_capabilities=[
                "version-pinned source",
                "declared entry point",
                "dependency specification",
            ],
            accepted_formats=["git_repository", "source_archive", "local_source"],
            query_terms=[
                *common_terms,
                "official implementation toolkit reproducibility",
            ],
        ),
        ResourceRequirement(
            requirement_id=stable_id(
                "resource-need", context.study_id, scope.version, "benchmark"
            ),
            need_type=ResourceNeedType.BENCHMARK,
            purpose="Anchor the comparator, metric, and evaluation convention.",
            contract_field="evaluator_policy.approved_benchmark_ids",
            blocking_level=(
                BlockingLevel.BLOCKING
                if ResourceCategory.EVALUATION.value in gap_categories
                else BlockingLevel.STRENGTHENING
            ),
            required_capabilities=[
                "documented metric",
                "declared evaluation unit",
                "versioned protocol or implementation",
            ],
            accepted_formats=["benchmark_release", "code_repository", "documentation"],
            query_terms=[
                *common_terms,
                "benchmark evaluation protocol metric",
            ],
        ),
    ]
    payload = {
        "schema_version": 1,
        "study_id": context.study_id,
        "scope_version": scope.version,
        "requirements": [item.model_dump(mode="json") for item in requirements],
        "authority": (
            "Requirements constrain resource discovery; they do not authorize "
            "download, installation, execution, or a scientific verdict."
        ),
        "generated_at": utc_now(),
    }
    artifact_id = _write_named_artifact(
        context,
        "resource_requirements.json",
        payload,
        role=ArtifactRole.PROTOCOL,
    )
    return {
        "resource_requirements": payload,
        "_workflow_output_artifact_ids": [artifact_id],
    }


def _need_type_for_local(category: str) -> ResourceNeedType | None:
    return {
        ResourceCategory.DATA.value: ResourceNeedType.DATASET,
        ResourceCategory.IMPLEMENTATION.value: ResourceNeedType.IMPLEMENTATION,
        ResourceCategory.MODEL.value: ResourceNeedType.MODEL,
        ResourceCategory.EVALUATION.value: ResourceNeedType.BENCHMARK,
        ResourceCategory.SOFTWARE.value: ResourceNeedType.TOOLKIT,
    }.get(category)


def _need_type_for_external(resource_type: str) -> ResourceNeedType | None:
    return {
        "dataset": ResourceNeedType.DATASET,
        "code_repository": ResourceNeedType.IMPLEMENTATION,
        "code_release": ResourceNeedType.IMPLEMENTATION,
        "benchmark": ResourceNeedType.BENCHMARK,
        "model": ResourceNeedType.MODEL,
        "documentation": ResourceNeedType.TOOLKIT,
    }.get(resource_type)


def _discover_concrete_resources(context: "StepContext") -> dict[str, Any]:
    """Build a concrete candidate registry from local and gateway resources."""

    requirements = [
        ResourceRequirement.model_validate(item)
        for item in _ancestor_result(context, "define_resource_requirements")[
            "resource_requirements"
        ]["requirements"]
    ]
    inventory = _ancestor_result(context, "inventory_research_resources")
    methods = _ancestor_result(context, "investigate_related_methods")
    requirement_ids = {
        item.need_type: item.requirement_id for item in requirements
    }
    candidates: list[dict[str, Any]] = []
    seen_resource_ids: set[str] = set()

    for item in inventory["resources"]:
        need_type = _need_type_for_local(str(item["category"]))
        if need_type is None or need_type not in requirement_ids:
            continue
        resource_id = str(item["resource_id"])
        seen_resource_ids.add(resource_id)
        candidates.append(
            {
                "candidate_id": stable_id(
                    "resource-candidate", context.study_id, resource_id
                ),
                "requirement_ids": [requirement_ids[need_type]],
                "name": str(item["name"]),
                "need_type": need_type.value,
                "source_kind": "local_project",
                "source_provider": "read_only_project_scan",
                "resource_id": resource_id,
                "canonical_identifier": str(item.get("location") or resource_id),
                "url": None,
                "version_or_revision": (
                    f"metadata_sha256:{item['metadata_hash']}"
                    if item.get("metadata_hash")
                    else "sha256"
                ),
                "content_hash": item.get("content_hash"),
                "license": None,
                "raw_metadata": {
                    "category": item["category"],
                    "location": item.get("location"),
                    "validation": item.get("validation"),
                    "binding_level": (
                        "metadata_only"
                        if item.get("metadata_hash")
                        else "content_sha256"
                    ),
                    "metadata_sha256": item.get("metadata_hash"),
                },
            }
        )

    external_ids = {
        str(item.get("resource_id"))
        for item in methods.get("method_cards", [])
        if item.get("resource_id")
    }
    retrieval_set = (methods.get("retrieval") or {}).get("resource_set") or {}
    retrieval_binding_ids = [
        str(item) for item in retrieval_set.get("binding_ids", [])
    ]
    if retrieval_binding_ids:
        from .retrieval.domain.repository import RetrievalRepository

        retrieval_repository = RetrievalRepository(str(context.repository.root))
        for binding_id in retrieval_binding_ids:
            try:
                external_ids.add(
                    retrieval_repository.load_binding(binding_id).resource_id
                )
            except (FileNotFoundError, ValueError):
                continue
    if external_ids:
        from .retrieval.domain.repository import RetrievalRepository

        retrieval_repository = RetrievalRepository(str(context.repository.root))
        for resource_id in sorted(external_ids):
            if resource_id in seen_resource_ids:
                continue
            try:
                resource = retrieval_repository.load_resource(resource_id)
            except (FileNotFoundError, ValueError):
                continue
            need_type = _need_type_for_external(resource.resource_type.value)
            if need_type is None or need_type not in requirement_ids:
                continue
            candidates.append(
                {
                    "candidate_id": stable_id(
                        "resource-candidate", context.study_id, resource_id
                    ),
                    "requirement_ids": [requirement_ids[need_type]],
                    "name": resource.title or resource.canonical_identifier,
                    "need_type": need_type.value,
                    "source_kind": "external_retrieval",
                    "source_provider": ",".join(resource.providers),
                    "resource_id": resource.resource_id,
                    "canonical_identifier": resource.canonical_identifier,
                    "url": resource.url,
                    "version_or_revision": (
                        resource.commit
                        or resource.dataset_identifier
                        or resource.model_identifier
                    ),
                    "content_hash": None,
                    "license": resource.license,
                    "raw_metadata": {
                        "resource_type": resource.resource_type.value,
                        "metadata_verification_status": (
                            resource.metadata_verification_status.value
                        ),
                        "repository": resource.repository,
                        "commit": resource.commit,
                        "dataset_identifier": resource.dataset_identifier,
                        "model_identifier": resource.model_identifier,
                        **resource.metadata,
                    },
                }
            )

    payload = {
        "schema_version": 1,
        "study_id": context.study_id,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "retrieval": methods.get("retrieval", {}),
        "acquisition_status": "not_authorized",
        "safety": {
            "remote_code_executed": False,
            "external_content_downloaded_by_this_step": False,
            "note": (
                "Candidates are metadata records. Acquisition and execution "
                "require a separate policy-controlled owner-approved action."
            ),
        },
        "generated_at": utc_now(),
    }
    artifact_id = _write_named_artifact(
        context,
        "concrete_resource_candidates.json",
        payload,
        role=ArtifactRole.PROTOCOL,
    )
    return {
        "resource_candidates": payload,
        "_workflow_output_artifact_ids": [artifact_id],
    }


def _validate_and_compare_resources(context: "StepContext") -> dict[str, Any]:
    """Apply deterministic eligibility checks and an explainable comparison."""

    discovered = _ancestor_result(context, "discover_concrete_resources")[
        "resource_candidates"
    ]
    inventory = _ancestor_result(context, "inventory_research_resources")
    hdf5_by_path = {
        str(item.get("path")): item
        for item in inventory.get("data_quality", {}).get(
            "hdf5_resources", []
        )
    }
    pdf_by_path = {
        str(item.get("path")): item
        for item in inventory.get("data_quality", {}).get(
            "pdf_resources", []
        )
    }
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    source_root = Path(project.source_root or "").resolve()
    requirements = {
        item["requirement_id"]: item
        for item in _ancestor_result(context, "define_resource_requirements")[
            "resource_requirements"
        ]["requirements"]
    }

    generic_resource_terms = {
        "data",
        "dataset",
        "benchmark",
        "implementation",
        "official",
        "project",
        "resource",
        "toolkit",
        "version",
        "protocol",
        "metric",
        "model",
        "code",
        "evaluation",
        "reproducibility",
    }

    def semantic_resource_match(raw: dict[str, Any]) -> tuple[bool, str]:
        requirement_text: list[str] = []
        for requirement_id in raw.get("requirement_ids", []):
            requirement = requirements.get(str(requirement_id), {})
            requirement_text.extend(
                str(item) for item in requirement.get("query_terms", [])
            )
            requirement_text.append(str(requirement.get("purpose") or ""))
        candidate_text = " ".join(
            [
                str(raw.get("name") or ""),
                str(raw.get("canonical_identifier") or ""),
                json.dumps(
                    raw.get("raw_metadata") or {},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ]
        )
        tokenize = lambda text: {
            token
            for token in re.findall(r"[a-z0-9_]+", text.casefold())
            if len(token) >= 3 and token not in generic_resource_terms
        }
        requirement_tokens = tokenize(" ".join(requirement_text))
        candidate_tokens = tokenize(candidate_text)
        overlap = sorted(requirement_tokens.intersection(candidate_tokens))
        path = Path(str(raw.get("canonical_identifier") or ""))
        stem = path.stem.casefold()
        need_type = str(raw.get("need_type") or "")
        structural_match = (
            need_type == ResourceNeedType.DATASET.value
            and stem
            in {
                "train",
                "training",
                "valid",
                "validation",
                "dev",
                "test",
                "evaluation",
            }
        ) or (
            need_type == ResourceNeedType.IMPLEMENTATION.value
            and stem
            in {
                "main",
                "run",
                "runner",
                "train",
                "predict",
                "model",
                "baseline",
                "experiment",
            }
        )
        matched = structural_match or len(overlap) >= 2
        detail = (
            f"matched domain terms: {', '.join(overlap[:8])}"
            if overlap
            else "no discriminative domain-term overlap"
        )
        if structural_match:
            detail = f"recognized formal {need_type} role: {stem}"
        return matched, detail

    validated: list[ConcreteResourceCandidate] = []
    for raw in discovered["candidates"]:
        local = raw["source_kind"] == "local_project"
        metadata = dict(raw.get("raw_metadata") or {})
        has_pin = bool(raw.get("content_hash") or raw.get("version_or_revision"))
        metadata_verified = (
            local
            or metadata.get("metadata_verification_status") == "verified"
        )
        license_known = bool(raw.get("license"))
        license_verified = license_known and metadata_verified
        open_or_local = local or bool(raw.get("url"))
        schema_probe = ProbeStatus.NOT_RUN
        install_probe = ProbeStatus.NOT_RUN
        local_probe_detail = "not run"
        if local:
            relative = Path(str(raw["canonical_identifier"]))
            candidate_path = (source_root / relative).resolve()
            inside_boundary = (
                candidate_path == source_root
                or source_root in candidate_path.parents
            )
            if not inside_boundary or not candidate_path.is_file():
                schema_probe = ProbeStatus.FAILED
                local_probe_detail = "path is outside the frozen project boundary"
            elif raw["need_type"] == ResourceNeedType.DATASET.value:
                suffix = candidate_path.suffix.casefold()
                try:
                    if suffix in {".csv", ".tsv"}:
                        with candidate_path.open(
                            "r", encoding="utf-8-sig", errors="strict"
                        ) as handle:
                            first_line = handle.readline(131_072)
                        delimiter = "\t" if suffix == ".tsv" else ","
                        columns = [
                            item.strip()
                            for item in first_line.rstrip("\r\n").split(delimiter)
                        ]
                        schema_probe = (
                            ProbeStatus.PASSED
                            if len(columns) >= 2 and all(columns)
                            else ProbeStatus.FAILED
                        )
                        local_probe_detail = (
                            f"header columns: {len(columns)}"
                        )
                    elif suffix == ".jsonl":
                        with candidate_path.open(
                            "r", encoding="utf-8", errors="strict"
                        ) as handle:
                            first_line = handle.readline(131_072)
                        value = json.loads(first_line)
                        schema_probe = (
                            ProbeStatus.PASSED
                            if isinstance(value, dict)
                            else ProbeStatus.FAILED
                        )
                        local_probe_detail = "first JSONL record parsed"
                    elif suffix in {".h5", ".hdf5"}:
                        hdf5_record = hdf5_by_path.get(relative.as_posix())
                        schema_probe = (
                            ProbeStatus.PASSED
                            if hdf5_record
                            and hdf5_record.get("readable") is True
                            else ProbeStatus.FAILED
                        )
                        local_probe_detail = (
                            "container metadata was inspected during project scan"
                            if schema_probe is ProbeStatus.PASSED
                            else "HDF5 metadata is missing or unreadable"
                        )
                    elif suffix == ".pdf":
                        pdf_record = pdf_by_path.get(
                            relative.as_posix()
                        )
                        schema_probe = (
                            ProbeStatus.PASSED
                            if pdf_record
                            and pdf_record.get("text_status")
                            == "extractable"
                            and int(
                                pdf_record.get("text_characters") or 0
                            )
                            > 0
                            else ProbeStatus.FAILED
                        )
                        local_probe_detail = (
                            "PDF text and page structure were inspected "
                            "without executing embedded content"
                            if schema_probe is ProbeStatus.PASSED
                            else "PDF text is unavailable; OCR is required"
                        )
                    else:
                        local_probe_detail = (
                            f"no bounded schema adapter for {suffix or 'file'}"
                        )
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    schema_probe = ProbeStatus.FAILED
                    local_probe_detail = f"{type(exc).__name__}"
                install_probe = ProbeStatus.NOT_APPLICABLE
            elif raw["need_type"] == ResourceNeedType.IMPLEMENTATION.value:
                if candidate_path.suffix.casefold() == ".py":
                    try:
                        source = candidate_path.read_text(encoding="utf-8")
                        compile(
                            source,
                            str(candidate_path),
                            "exec",
                            dont_inherit=True,
                        )
                        has_entry_point = (
                            candidate_path.name.casefold() != "__init__.py"
                            and (
                                candidate_path.stem.casefold().startswith(
                                    (
                                        "run",
                                        "train",
                                        "main",
                                        "cli",
                                        "baseline",
                                        "experiment",
                                    )
                                )
                                or 'if __name__ == "__main__"' in source
                                or "if __name__ == '__main__'" in source
                                or bool(
                                    re.search(
                                        r"(?m)^\s*def\s+main\s*\(",
                                        source,
                                    )
                                )
                            )
                        )
                        schema_probe = (
                            ProbeStatus.PASSED
                            if source.strip() and has_entry_point
                            else ProbeStatus.FAILED
                        )
                        local_probe_detail = (
                            "Python syntax and a static executable entry point "
                            "were verified without executing the module"
                            if schema_probe is ProbeStatus.PASSED
                            else "Python compiled, but no executable entry point "
                            "was identified"
                        )
                    except (OSError, UnicodeError, SyntaxError) as exc:
                        schema_probe = ProbeStatus.FAILED
                        local_probe_detail = f"{type(exc).__name__}"
                else:
                    schema_probe = ProbeStatus.NOT_APPLICABLE
                    local_probe_detail = "no static syntax adapter"
            else:
                schema_probe = ProbeStatus.NOT_APPLICABLE
                install_probe = ProbeStatus.NOT_APPLICABLE
        blockers: list[str] = []
        requirement_matched, requirement_match_detail = (
            semantic_resource_match(raw)
        )
        metadata_only_binding = (
            local
            and metadata.get("binding_level") == "metadata_only"
        )
        if not has_pin:
            blockers.append("No immutable version, revision, or content hash.")
        if not open_or_local:
            blockers.append("No usable local path or canonical access URL.")
        if raw["need_type"] in {"dataset", "implementation"} and not local:
            blockers.append(
                "External resource has not been acquired and safely probed."
            )
        if not local and not license_verified:
            blockers.append("External resource license has not been verified.")
        if local and raw["need_type"] == "dataset" and schema_probe is not ProbeStatus.PASSED:
            blockers.append("Local dataset schema probe did not pass.")
        if metadata_only_binding:
            blockers.append(
                "HDF5 is metadata-bound only; Stage 3 must freeze a full-file "
                "hash or immutable dataset snapshot."
            )
        if local and raw["need_type"] == "implementation":
            if schema_probe is ProbeStatus.FAILED:
                blockers.append(
                    "Local implementation lacks a valid statically identifiable "
                    "entry point."
                )
            else:
                blockers.append(
                    "Dependency environment remains to be validated by Stage 2 "
                    "preflight."
                )
        if not requirement_matched:
            blockers.append(
                "Candidate is structurally readable but is not semantically "
                "aligned with this resource requirement."
            )
        components = {
            "requirement_match": 20 if requirement_matched else 0,
            "metadata": 20 if metadata_verified else 8,
            "version_pinning": 20 if has_pin else 0,
            "license": (
                15
                if license_verified
                else 8
                if license_known
                else 10
                if local
                else 0
            ),
            "availability": 15 if open_or_local else 0,
            "safe_probe": (
                10
                if schema_probe in {ProbeStatus.PASSED, ProbeStatus.NOT_APPLICABLE}
                else 0
            ),
        }
        total = sum(components.values())
        eligibility = (
            CandidateEligibility.ELIGIBLE
            if not blockers and total >= 80
            else CandidateEligibility.CONDITIONAL
            if open_or_local and has_pin
            else CandidateEligibility.INELIGIBLE
        )
        validated.append(
            ConcreteResourceCandidate(
                candidate_id=raw["candidate_id"],
                requirement_ids=raw["requirement_ids"],
                name=raw["name"],
                need_type=raw["need_type"],
                source_kind=raw["source_kind"],
                source_provider=raw["source_provider"],
                resource_id=raw["resource_id"],
                canonical_identifier=raw["canonical_identifier"],
                url=raw.get("url"),
                version_or_revision=raw.get("version_or_revision"),
                content_hash=raw.get("content_hash"),
                license=raw.get("license"),
                license_status=(
                    LicenseStatus.VERIFIED
                    if license_verified
                    else LicenseStatus.REPORTED
                    if license_known
                    else LicenseStatus.NOT_APPLICABLE
                    if local
                    else LicenseStatus.UNKNOWN
                ),
                availability=(
                    AvailabilityStatus.AVAILABLE
                    if local
                    else AvailabilityStatus.AVAILABLE_UNVERIFIED
                    if open_or_local
                    else AvailabilityStatus.UNAVAILABLE
                ),
                metadata_validation=(
                    ValidationStatus.VALIDATED
                    if metadata_verified
                    else ValidationStatus.PARTIAL
                ),
                pinning_validation=(
                    ValidationStatus.PARTIAL
                    if metadata_only_binding
                    else ValidationStatus.VALIDATED
                    if has_pin
                    else ValidationStatus.UNVALIDATED
                ),
                schema_probe=schema_probe,
                install_probe=install_probe,
                checks=[
                    {
                        "check": "requirement_match",
                        "passed": requirement_matched,
                        "evidence": requirement_match_detail,
                    },
                    {
                        "check": "metadata",
                        "passed": metadata_verified,
                        "evidence": (
                            "local content-addressed scan"
                            if local
                            else metadata.get(
                                "metadata_verification_status", "unverified"
                            )
                        ),
                    },
                    {
                        "check": "version_pin",
                        "passed": has_pin,
                        "evidence": (
                            raw.get("content_hash")
                            or raw.get("version_or_revision")
                            or "missing"
                        ),
                    },
                    {
                        "check": "license",
                        "passed": license_known or local,
                        "evidence": raw.get("license") or "local owner review required",
                    },
                    {
                        "check": "safe_probe",
                        "passed": schema_probe is ProbeStatus.PASSED,
                        "evidence": local_probe_detail,
                    },
                ],
                score_components=components,
                total_score=total,
                eligibility=eligibility,
                blockers=blockers,
                selection_notes=[
                    (
                        "Local resources remain read-only and are frozen by hash."
                        if local
                        else "Selection does not authorize download or code execution."
                    )
                ],
            )
        )
    def candidate_preference(item: ConcreteResourceCandidate) -> int:
        location = item.canonical_identifier.replace("\\", "/").casefold()
        name = Path(location).name
        score = 0
        if item.need_type is ResourceNeedType.DATASET:
            if "/data/" in f"/{location}":
                score += 60
            if name.startswith(("train", "test", "valid", "dev")):
                score += 40
            if name.endswith(".jsonl"):
                score += 15
            if any(
                token in name
                for token in (
                    "metadata",
                    "catalog",
                    "manifest",
                    "submission",
                    "hf_datasets",
                )
            ):
                score -= 60
        elif item.need_type is ResourceNeedType.IMPLEMENTATION:
            if name == "run_experiment.py":
                score += 100
            elif name.startswith(("run", "train", "main", "cli", "baseline")):
                score += 60
            if "roundtable" in name:
                score += 80
            elif "knowledge" in name:
                score += 60
            elif "recommendation" in name:
                score += 75
            elif "pi-review" in name:
                score += 70
            elif "scoring" in name:
                score += 65
            elif "quality-metrics" in name:
                score += 55
            elif any(token in name for token in ("advisor", "engine", "pipeline", "model")):
                score += 45
            if any(
                part in location
                for part in (
                    "/drizzle/",
                    "/migrations/",
                    "/generated/",
                    "/artifacts/",
                )
            ):
                score -= 100
            if name == "__init__.py":
                score -= 100
        elif item.need_type is ResourceNeedType.BENCHMARK:
            if any(token in location for token in ("evaluator", "metric", "benchmark")):
                score += 50
        return score

    eligibility_order = {
        CandidateEligibility.ELIGIBLE: 0,
        CandidateEligibility.CONDITIONAL: 1,
        CandidateEligibility.INELIGIBLE: 2,
    }
    ordered = sorted(
        validated,
        key=lambda item: (
            item.requirement_ids[0] if item.requirement_ids else "",
            eligibility_order[item.eligibility],
            -candidate_preference(item),
            -item.total_score,
            item.name.casefold(),
        ),
    )
    shortlists = []
    for requirement_id, requirement in requirements.items():
        matches = [
            item for item in ordered if requirement_id in item.requirement_ids
        ]
        shortlists.append(
            {
                "requirement_id": requirement_id,
                "need_type": requirement["need_type"],
                "blocking_level": requirement["blocking_level"],
                "candidate_ids": [item.candidate_id for item in matches[:5]],
                "recommended_candidate_id": next(
                    (
                        item.candidate_id
                        for item in matches
                        if item.eligibility is not CandidateEligibility.INELIGIBLE
                    ),
                    None,
                ),
                "status": (
                    "ready"
                    if any(
                        item.eligibility is CandidateEligibility.ELIGIBLE
                        for item in matches
                    )
                    else "conditional"
                    if matches
                    else "missing"
                ),
            }
        )
    payload = {
        "schema_version": 1,
        "study_id": context.study_id,
        "candidates": [item.model_dump(mode="json") for item in ordered],
        "shortlists": shortlists,
        "scoring": {
            "maximum": 100,
            "components": [
                "requirement_match",
                "metadata",
                "version_pinning",
                "license",
                "availability",
                "safe_probe",
            ],
            "note": (
                "Scores rank candidates only. Blocking checks and owner selection "
                "remain authoritative."
            ),
        },
        "generated_at": utc_now(),
    }
    summary = (
        "# Concrete resource comparison\n\n"
        f"- Candidates checked: {len(ordered)}\n"
        f"- Eligible now: {sum(item.eligibility is CandidateEligibility.ELIGIBLE for item in ordered)}\n"
        f"- Conditional: {sum(item.eligibility is CandidateEligibility.CONDITIONAL for item in ordered)}\n"
        f"- Ineligible: {sum(item.eligibility is CandidateEligibility.INELIGIBLE for item in ordered)}\n\n"
        "External candidates are metadata-only until an owner-approved acquisition "
        "freezes a snapshot. No remote package or script was executed.\n"
    )
    artifact_ids = [
        _write_named_artifact(
            context,
            "resource_candidate_evaluation.json",
            payload,
            role=ArtifactRole.AUDIT,
        ),
        _write_named_artifact(
            context,
            "resource_candidate_comparison.md",
            summary,
            role=ArtifactRole.PROTOCOL,
        ),
    ]
    return {
        "resource_candidate_evaluation": payload,
        "_workflow_output_artifact_ids": artifact_ids,
    }


def _candidate_topics(context: "StepContext") -> dict[str, Any]:
    scope = context.repository.latest_scope_contract(context.study_id)
    if scope is None:
        raise _blocked("No frozen direction Scope is available.")
    methods = _ancestor_result(context, "investigate_related_methods")
    gaps = _ancestor_result(context, "diagnose_resource_gaps")["resource_gaps"]
    resource_evaluation = _ancestor_result(
        context, "validate_and_compare_resources"
    )["resource_candidate_evaluation"]
    concrete_candidates = [
        ConcreteResourceCandidate.model_validate(item)
        for item in resource_evaluation["candidates"]
    ]
    scope_resource_paths = {
        str(item).replace("\\", "/").casefold()
        for item in scope.project_resource_ids
    }
    selected_track = str(
        (scope.field_diff or {}).get("selected_primary_track_id") or ""
    )
    semantic_source = " ".join(
        [
            scope.direction,
            scope.research_question,
            scope.candidate_contribution,
            selected_track,
            *[
                str(item)
                for item in (scope.field_diff or {}).get(
                    "academic_concepts", []
                )
            ],
        ]
    ).casefold()

    def semantic_tokens(value: str) -> set[str]:
        tokens = {
            item
            for item in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", value.casefold())
            if len(item) > 2
            and item
            not in {
                "the",
                "and",
                "for",
                "with",
                "from",
                "into",
                "this",
                "that",
                "study",
                "project",
                "research",
            }
        }
        aliases = {
            "rare": {"extreme"},
            "high": {"winner"},
            "return": {"winner"},
            "event": {"winner"},
            "ranking": {"ranker"},
            "selection": {"ranker"},
            "robustness": {"expanded", "validation"},
            "walk": {"prospective", "validation"},
            "forward": {"prospective", "validation"},
            "top": {"topk"},
        }
        expanded = set(tokens)
        for token in tokens:
            expanded.update(aliases.get(token, set()))
        return expanded

    scope_tokens = semantic_tokens(semantic_source)

    def topic_resource_preference(
        item: ConcreteResourceCandidate,
    ) -> tuple[int, int, int, str]:
        location = item.canonical_identifier.replace("\\", "/").casefold()
        resource_tokens = semantic_tokens(location)
        overlap = len(scope_tokens.intersection(resource_tokens))
        exact_scope_binding = int(location in scope_resource_paths)
        family_bonus = 0
        if selected_track:
            selected_family = {
                token
                for token in semantic_tokens(selected_track)
                if token not in {"json", "version"}
            }
            family_bonus = len(selected_family.intersection(resource_tokens))
        return (
            exact_scope_binding,
            family_bonus,
            overlap,
            location,
        )

    usable_by_type: dict[ResourceNeedType, list[ConcreteResourceCandidate]] = {}
    for item in concrete_candidates:
        if item.eligibility is CandidateEligibility.INELIGIBLE:
            continue
        usable_by_type.setdefault(item.need_type, []).append(item)
    for items in usable_by_type.values():
        items.sort(
            key=lambda item: (
                -topic_resource_preference(item)[0],
                -topic_resource_preference(item)[1],
                -topic_resource_preference(item)[2],
                -item.total_score,
                topic_resource_preference(item)[3],
            )
        )
    topic_usable_by_type: dict[
        ResourceNeedType, list[ConcreteResourceCandidate]
    ] = {}
    for need_type, items in usable_by_type.items():
        if not scope_resource_paths:
            topic_usable_by_type[need_type] = items
            continue
        relevant = [
            item
            for item in items
            if (
                topic_resource_preference(item)[0] > 0
                or topic_resource_preference(item)[1] > 0
                or topic_resource_preference(item)[2] >= 2
                or item.source_kind == "external_retrieval"
            )
        ]
        topic_usable_by_type[need_type] = relevant
    eligible_by_type = {
        need_type: [
            item
            for item in items
            if item.eligibility is CandidateEligibility.ELIGIBLE
        ]
        for need_type, items in topic_usable_by_type.items()
    }
    method_ids = [item["method_id"] for item in methods["method_cards"][:8]]
    data_names = [
        item.name for item in usable_by_type.get(ResourceNeedType.DATASET, [])
    ]
    corpus = ", ".join(data_names[:5]) or "the bounded project corpus"
    unresolved = [item["detail"] for item in gaps["gaps"]]
    strengthening_resources = [
        item["detail"]
        for item in gaps["gaps"]
        if item["blocking_level"] == BlockingLevel.STRENGTHENING.value
    ]
    scope_text = " ".join(
        [
            scope.direction,
            scope.research_question,
            scope.candidate_contribution,
            *scope.scope_in,
            *scope.scope_out,
        ]
    ).casefold()
    comparison_frame = _comparison_frame_for_scope(scope)
    framed_comparison = _comparison_frame_complete(comparison_frame)
    database_governance = any(
        cue in scope_text
        for cue in (
            "行级安全",
            "row-level security",
            "row level security",
            "多租户",
            "multi-tenant",
        )
    )
    advisor_recommendation = any(
        cue in scope_text
        for cue in (
            "学术导师推荐",
            "academic advisor",
            "professor recommendation",
            "academic recommender",
        )
    )
    stereo_geometry = any(
        cue in scope_text
        for cue in (
            "双目视觉",
            "双目",
            "stereo vision",
            "stereopolicy",
            "disparity prior",
            "cross-attention",
            "cross attention",
        )
    )
    selected_track_folded = selected_track.replace("_", "-").casefold()
    if (
        "extreme-winner-expanded-robustness" in selected_track_folded
        and not framed_comparison
    ):
        # This project family already freezes a named paired comparison in
        # its protocol: dual-quality Top-5 versus the V3 technology-quality
        # Top-5 rule.  Do not erase it into “declared method/baseline”.
        controlled_title = scope.direction
        controlled_question = scope.research_question
        controlled_intervention = "dual_quality_top5"
        controlled_comparator = "v3_tech_quality_top5"
    elif framed_comparison:
        controlled_title = scope.direction
        controlled_question = str(
            comparison_frame.get("research_question")
            or scope.research_question
        )
        controlled_intervention = str(comparison_frame["intervention"])
        controlled_comparator = str(comparison_frame["comparator"])
    elif stereo_geometry:
        controlled_title = (
            "置信度种子视差先验对双目交叉注意力与策略性能的影响"
        )
        controlled_question = (
            "与无几何约束的双目交叉注意力相比，高置信稀疏视差先验能否"
            "在光照变化和遮挡条件下改善左右视图对齐，并提高冻结策略"
            "骨干的任务成功率？"
        )
        controlled_intervention = (
            "confidence-seeded sparse disparity guidance for stereo "
            "cross-attention"
        )
        controlled_comparator = "unconstrained stereo cross-attention"
    elif database_governance:
        controlled_title = (
            "数据库强制完整性控制对学术推荐工作流越权接受率的影响"
        )
        controlled_question = (
            "与仅应用层授权相比，数据库级行级安全、证据外键和追加式"
            "审计能否降低跨用户越权与孤立记录的接受率，同时保持合法"
            "事务成功率？"
        )
        controlled_intervention = (
            "database-enforced row-level security, evidence foreign keys, "
            "and append-only audit constraints"
        )
        controlled_comparator = "application-layer authorization only"
    elif advisor_recommendation:
        controlled_title = (
            "证据约束与风险控制对学术导师推荐可靠性的影响"
        )
        controlled_question = (
            "与单次生成推荐相比，结构化证据评分、风险约束和复核能否"
            "降低不受支持的推荐主张，同时保持专家相关性？"
        )
        controlled_intervention = (
            "structured evidence scoring, risk constraints, and review"
        )
        controlled_comparator = "single-pass professor recommendation"
    else:
        # Preserve an already explicit owner-frozen question even when Stage 1
        # did not provide a structured comparison frame. Falling back to a
        # generic English question silently discards the actual research
        # boundary, especially for idea-to-paper studies.
        controlled_title = scope.direction
        controlled_question = scope.research_question
        controlled_intervention = "the declared method or intervention"
        controlled_comparator = "the declared baseline"
    templates = [
        (
            "controlled-comparison",
            controlled_title,
            controlled_question,
            "paired or matched computational comparison",
            controlled_intervention,
            controlled_comparator,
            [
                ResourceNeedType.DATASET,
                ResourceNeedType.IMPLEMENTATION,
            ],
        ),
        (
            "robustness-replication",
            f"Reproducibility and robustness of {scope.direction}",
            "Do the central project findings remain stable across frozen seeds, splits, and justified sensitivity checks?",
            "computational replication and robustness study",
            "the primary implementation",
            "frozen replication and sensitivity conditions",
            [
                ResourceNeedType.DATASET,
                ResourceNeedType.IMPLEMENTATION,
                ResourceNeedType.BENCHMARK,
            ],
        ),
        (
            "measurement-failure-mode",
            f"Measurement boundaries and failure modes in {scope.direction}",
            "Which predeclared data, metric, or evaluation conditions make the project result unverifiable or unstable?",
            "diagnostic computational evaluation",
            "the observed measurement pipeline",
            "deterministic integrity and alternative measurement checks",
            [ResourceNeedType.DATASET, ResourceNeedType.BENCHMARK],
        ),
    ]
    candidates: list[TopicCandidate] = []
    for (
        key,
        title,
        rq,
        design,
        intervention,
        comparator,
        required_types,
    ) in templates:
        missing_types = [
            item
            for item in required_types
            if not topic_usable_by_type.get(item)
        ]
        conditional_types = [
            item
            for item in required_types
            if topic_usable_by_type.get(item)
            and not eligible_by_type.get(item)
        ]
        recommended_resources: list[ConcreteResourceCandidate] = []
        for need_type in required_types:
            available = topic_usable_by_type.get(need_type, [])
            if not available:
                continue
            recommended_resources.append(available[0])
            if need_type is not ResourceNeedType.DATASET:
                continue
            first_suffix = Path(
                available[0].canonical_identifier
            ).suffix.casefold()
            if first_suffix in {".h5", ".hdf5"}:
                for candidate in available[1:]:
                    if (
                        Path(candidate.canonical_identifier).suffix.casefold()
                        not in {".h5", ".hdf5"}
                    ):
                        continue
                    recommended_resources.append(candidate)
                    if len(
                        [
                            item
                            for item in recommended_resources
                            if item.need_type is ResourceNeedType.DATASET
                        ]
                    ) >= 3:
                        break
                continue
            first_parent = Path(
                available[0].canonical_identifier.replace("\\", "/")
            ).parent.as_posix()
            for candidate in available[1:]:
                location = candidate.canonical_identifier.replace("\\", "/")
                if Path(location).parent.as_posix() != first_parent:
                    continue
                name = Path(location).name.casefold()
                if not name.startswith(("train", "test", "valid", "dev")):
                    continue
                recommended_resources.append(candidate)
                if len(
                    [
                        item
                        for item in recommended_resources
                        if item.need_type is ResourceNeedType.DATASET
                    ]
                ) >= 3:
                    break
        resource_ids = [item.resource_id for item in recommended_resources]
        data_ids = [
            item.resource_id
            for item in recommended_resources
            if item.need_type is ResourceNeedType.DATASET
        ]
        implementation_ids = [
            item.resource_id
            for item in recommended_resources
            if item.need_type
            in {ResourceNeedType.IMPLEMENTATION, ResourceNeedType.TOOLKIT}
        ]
        topic_data_names = [
            item.name
            for item in recommended_resources
            if item.need_type is ResourceNeedType.DATASET
        ]
        topic_corpus = (
            ", ".join(topic_data_names)
            if topic_data_names
            else (
                "No formal dataset is bound yet; Stage 3 must acquire or "
                "construct one under the frozen sampling and target rules."
            )
        )
        base_status = (
            TopicStatus.CONDITIONAL
            if missing_types
            or conditional_types
            or not methods["method_cards"]
            else TopicStatus.READY
        )
        # Missing local resources do not block Stage 2.  The platform may
        # acquire owner-approved external resources or build a bounded
        # experiment.  Formal acquisition and freezing remain Stage 3 work.
        blocking_resources: list[str] = []
        topic_unresolved = [
            *[
                f"{item.value} must be acquired or built before the formal "
                "Stage 3 experiment."
                for item in missing_types
            ],
            *[
                f"{item.value} requires acquisition or additional validation."
                for item in conditional_types
            ],
            *[
                detail
                for detail in unresolved
                if not (
                    ("data resource" in detail.casefold() and data_ids)
                    or (
                        "implementation" in detail.casefold()
                        and implementation_ids
                    )
                )
            ],
        ]
        resource_boundary_summary = (
            "Acquisition or experiment build required: "
            + ", ".join(item.value for item in missing_types)
            if missing_types
            else "Conditional: "
            + ", ".join(item.value for item in conditional_types)
            if conditional_types
            else "All required resource types have an eligible, version-bound candidate."
        )
        feasibility_dimensions = {
            "scientific_value": FeasibilityDimension(
                assessment="adequate",
                rationale=(
                    "The candidate asks a falsifiable question inside the "
                    "owner-approved direction."
                ),
                dependencies=["frozen primary outcome and threshold"],
            ),
            "novelty": FeasibilityDimension(
                assessment="unknown" if not method_ids else "adequate",
                rationale=(
                    "Novelty remains provisional until the frozen method and "
                    "literature set covers the closest work."
                ),
                dependencies=method_ids or ["approved protocol literature retrieval"],
            ),
            "falsifiability": FeasibilityDimension(
                assessment="adequate",
                rationale="A baseline comparison and falsification rule can be frozen.",
                dependencies=["minimum meaningful effect", "success threshold"],
            ),
            "project_relevance": FeasibilityDimension(
                assessment="strong",
                rationale="The question is derived from the frozen Stage 1 direction.",
                dependencies=[f"Scope version {scope.version}"],
            ),
            "data_availability": FeasibilityDimension(
                assessment="adequate" if data_ids else "weak",
                rationale=(
                    f"{len(data_ids)} local data resources were inventoried."
                    if data_ids
                    else "No machine-readable data resource was verified."
                ),
                dependencies=data_ids
                or [
                    "owner-approved external dataset acquisition or a bounded "
                    "new data-generation plan"
                ],
            ),
            "baseline_reproducibility": FeasibilityDimension(
                assessment="unknown",
                rationale="The declared baseline has not yet passed Step 2.10.",
                dependencies=["baseline validation report"],
            ),
            "compute_feasibility": FeasibilityDimension(
                assessment="adequate",
                rationale="Local compute was inventoried; the final budget remains to freeze.",
                dependencies=["protocol compute budget"],
            ),
            "implementation_feasibility": FeasibilityDimension(
                assessment="adequate" if implementation_ids else "weak",
                rationale=(
                    f"{len(implementation_ids)} implementation resources were inventoried."
                    if implementation_ids
                    else "No executable implementation was verified."
                ),
                dependencies=implementation_ids
                or ["bounded experiment implementation to be generated in Stage 2"],
            ),
            "benchmark_fit": FeasibilityDimension(
                assessment=(
                    "adequate"
                    if eligible_by_type.get(ResourceNeedType.BENCHMARK)
                    else "weak"
                    if topic_usable_by_type.get(ResourceNeedType.BENCHMARK)
                    else "weak"
                    if ResourceNeedType.BENCHMARK in required_types
                    else "unknown"
                ),
                rationale=(
                    "A concrete benchmark candidate constrains the metric and "
                    "evaluation convention."
                    if topic_usable_by_type.get(ResourceNeedType.BENCHMARK)
                    else "No concrete benchmark currently constrains this topic."
                ),
                dependencies=[
                    item.resource_id
                    for item in topic_usable_by_type.get(
                        ResourceNeedType.BENCHMARK, []
                    )[:3]
                ],
            ),
            "auditability": FeasibilityDimension(
                assessment="adequate",
                rationale="Stage 2 requires content hashes, logs, and artifact bindings.",
                dependencies=["preflight logging and binding checks"],
            ),
            "expected_evidence_strength": FeasibilityDimension(
                assessment="weak" if topic_unresolved else "adequate",
                rationale=(
                    "Evidence strength is limited by unresolved resource conditions."
                    if topic_unresolved
                    else "A controlled, version-bound comparison is feasible."
                ),
                dependencies=topic_unresolved,
            ),
            "compliance": FeasibilityDimension(
                assessment="unknown",
                rationale="License, privacy, ethics, and policy clearance must be explicit.",
                dependencies=["owner compliance clearance"],
            ),
            "time_cost": FeasibilityDimension(
                assessment="unknown",
                rationale="Time cost cannot be rated until the run matrix is frozen.",
                dependencies=["task, seed, repetition, and timeout contract"],
            ),
            "monetary_cost": FeasibilityDimension(
                assessment="unknown",
                rationale="Monetary cost cannot be rated until provider and compute budgets freeze.",
                dependencies=["project budget"],
            ),
        }
        candidates.append(
            TopicCandidate(
            topic_id=stable_id("topic", context.study_id, key),
            title=title,
            research_question=rq,
            background_and_motivation=(
                "The project contains a research direction whose empirical "
                "boundary must be converted into an auditable computational study."
            ),
            relation_to_direction=(
                f"This candidate operationalizes the frozen direction: {scope.direction}"
            ),
            existing_work_gap=(
                "The closest reproducible method gap remains provisional until "
                "the approved method cards are complete."
            ),
            hypothesis=(
                str(comparison_frame["falsifiable_hypothesis"])
                if key == "controlled-comparison" and framed_comparison
                else (
                    f"{intervention.capitalize()} produces a predeclared, "
                    "directionally interpretable change in the primary "
                    f"outcome relative to {comparator}."
                )
            ),
            study_design=design,
            unit_of_analysis=(
                str(comparison_frame["unit_of_analysis"])
                if key == "controlled-comparison"
                and framed_comparison
                and comparison_frame.get("unit_of_analysis")
                else "one eligible monthly prediction snapshot"
                if key == "controlled-comparison"
                and "extreme-winner-expanded-robustness"
                in selected_track_folded
                else "predeclared computational task, sample, or run"
            ),
            population_or_corpus=topic_corpus,
            intervention_or_method=intervention,
            primary_outcome=(
                str(comparison_frame["primary_outcome"])
                if key == "controlled-comparison" and framed_comparison
                else "Top-5 high-return event identification rate"
                if key == "controlled-comparison"
                and "extreme-winner-expanded-robustness"
                in selected_track_folded
                else "to be selected from a verified project metric"
            ),
            comparison=f"{intervention} versus {comparator}",
            minimum_meaningful_effect="must be frozen in the protocol",
            candidate_contribution=(
                (
                    "Estimate the paired change in Top-5 high-return-event "
                    "identification when dual_quality_top5 replaces "
                    "v3_tech_quality_top5 under the frozen rolling-window "
                    "robustness protocol."
                )
                if key == "controlled-comparison"
                and "extreme-winner-expanded-robustness"
                in selected_track_folded
                else (
                    "A bounded, reproducible estimate with explicit evidence, "
                    "resource, and failure boundaries."
                )
            ),
            scope_in=[*scope.scope_in, "frozen baseline validation"],
            scope_out=[*scope.scope_out, "formal treatment execution in Stage 2"],
            required_resource_ids=resource_ids,
            required_resource_types=required_types,
            recommended_resource_candidate_ids=[
                item.candidate_id for item in recommended_resources
            ],
            resource_boundary_summary=resource_boundary_summary,
            required_data_ids=data_ids,
            required_baselines=[comparator],
            required_tools=implementation_ids,
            required_compute=["bounded local or contract-authorized compute"],
            blocking_resources=blocking_resources,
            strengthening_resources=strengthening_resources,
            major_risks=[
                "unverified split or target leakage",
                "baseline may not reproduce",
                "novelty grounding may remain incomplete",
            ],
            alternative_designs=[
                "bounded replication without a treatment-effect claim",
                "measurement study producing an unverifiable diagnosis",
            ],
            expected_evidence_strength=(
                "limited until unresolved conditions are cleared"
                if topic_unresolved
                else "controlled and version-bound"
            ),
            expected_reproducibility=(
                "provisional; determined after baseline and preflight"
            ),
            compliance_status="pending explicit clearance",
            feasibility_dimensions=feasibility_dimensions,
            method_card_ids=method_ids,
            status=base_status,
            feasibility_reasons=[
                f"{len(resource_ids)} concrete resources match this topic",
                f"{len(method_ids)} method cards available",
                resource_boundary_summary,
            ],
            unresolved_conditions=topic_unresolved,
            prohibited_claims=[
                "No treatment effect is established in Stage 2.",
                "An inferred resource chain cannot support a scientific verdict.",
            ],
            )
        )
    payload = {
        "schema_version": 1,
        "direction_scope_version": scope.version,
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "recommended_topic_id": next(
            (
                item.topic_id
                for item in candidates
                if item.status is TopicStatus.READY
            ),
            next(
                (
                    item.topic_id
                    for item in candidates
                    if item.status is TopicStatus.CONDITIONAL
                ),
                None,
            ),
        ),
    }
    matrix_rows = []
    for item in candidates:
        for dimension, assessment in item.feasibility_dimensions.items():
            matrix_rows.append(
                f"| {item.title} | {dimension} | {assessment.assessment} | "
                f"{assessment.rationale} | "
                f"{'; '.join(assessment.dependencies) or 'none'} |"
            )
    matrix = (
        "# Topic feasibility matrix\n\n"
        "No aggregate score is used. Each dimension retains its rationale and "
        "dependencies.\n\n"
        "| Candidate | Dimension | Assessment | Rationale | Dependencies |\n"
        "|---|---|---|---|---|\n"
        + "\n".join(matrix_rows)
        + "\n"
    )
    recommended = next(
        (
            item
            for item in candidates
            if item.topic_id == payload["recommended_topic_id"]
        ),
        None,
    )
    recommendation = (
        "# Topic recommendation\n\n"
        + (
            f"## Recommended candidate\n\n{recommended.title}\n\n"
            f"- Status: {recommended.status.value}\n"
            f"- Scientific value: "
            f"{recommended.feasibility_dimensions['scientific_value'].rationale}\n"
            f"- Available resources: {len(recommended.required_resource_ids)}\n"
            f"- Blocking resources: "
            f"{'; '.join(recommended.blocking_resources) or 'none identified'}\n"
            f"- Acceptable alternatives: "
            f"{'; '.join(recommended.alternative_designs)}\n"
            f"- Expected evidence strength: "
            f"{recommended.expected_evidence_strength}\n"
            f"- Major risks: {'; '.join(recommended.major_risks)}\n\n"
            "The owner must select the candidate; this recommendation has no "
            "authority to freeze Scope or authorize an experiment.\n"
            if recommended
            else "No selectable candidate was generated.\n"
        )
    )
    artifact_ids = [
        _write_named_artifact(
            context, "candidate_topics.json", payload, role=ArtifactRole.PROTOCOL
        ),
        _write_named_artifact(context, "topic_feasibility_matrix.md", matrix),
        _write_named_artifact(context, "topic_recommendation.md", recommendation),
    ]
    if candidates and all(item.status is TopicStatus.BLOCKED for item in candidates):
        scope_change = {
            "schema_version": 1,
            "study_id": context.study_id,
            "scope_version": scope.version,
            "status": "proposed",
            "reason": "No Stage 2 candidate passed the hard resource constraints.",
            "requested_changes": ["return to Stage 1 and select a feasible direction"],
            "new_evidence": [item["detail"] for item in gaps["gaps"]],
            "scientific_impact": "The current direction cannot support a formal study.",
            "protocol_impact": "No Research Contract may be frozen.",
            "requires_stage_one_return": True,
            "requires_owner_confirmation": True,
            "generated_at": utc_now(),
        }
        artifact_ids.append(
            _write_named_artifact(
                context,
                "scope_change_request.json",
                scope_change,
                role=ArtifactRole.PROTOCOL,
                status=ArtifactStatus.DRAFT,
            )
        )
        payload["scope_change_request"] = scope_change
    return {**payload, "_workflow_output_artifact_ids": artifact_ids}


def _selected_verified_research_chain(
    source_root: Path,
    selected_candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Load a selected, immutable local Research Forge baseline chain.

    This is an import adapter, not a filename heuristic: the contract hash,
    protected artifacts, evidence row, run record, and run manifest must agree.
    """

    local_paths = [
        (source_root / str(item.get("canonical_identifier", ""))).resolve()
        for item in selected_candidates
        if item.get("source_kind") == "local_project"
        and item.get("canonical_identifier")
    ]
    project_roots: list[Path] = []
    for path in local_paths:
        for parent in path.parents:
            if parent == source_root.parent:
                break
            if (parent / "research_contract.json").is_file():
                project_roots.append(parent)
                break
    if not project_roots or len(set(project_roots)) != 1:
        return None
    project_root = project_roots[0]
    try:
        contract_path = project_root / "research_contract.json"
        contract = read_json(contract_path)
        frozen = read_json(project_root / "frozen_manifest.json")
        protected = read_json(project_root / "protected_manifest.json")
        execution_contract = read_json(project_root / "execution_contract.json")
        task = read_json(project_root / "benchmark" / "task.json")
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    frozen_hashes = frozen.get("hashes") or {}
    if frozen_hashes.get("research_contract.json") != sha256_file(contract_path):
        return None
    protected_hashes = protected.get("hashes") or {}
    if not protected_hashes or any(
        not (project_root / relative).is_file()
        or sha256_file(project_root / relative) != digest
        for relative, digest in protected_hashes.items()
    ):
        return None
    evidence_path = project_root / "evidence.jsonl"
    try:
        evidence_rows = [
            json.loads(line)
            for line in evidence_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        return None
    baseline_rows = [
        row
        for row in evidence_rows
        if row.get("is_baseline") is True
        and row.get("valid") is True
        and row.get("verdict") == "baseline_verified"
    ]
    if not baseline_rows:
        return None
    baseline = baseline_rows[-1]
    run_id = str(baseline.get("run_id") or "")
    record_path = project_root / "runs" / run_id / "record.json"
    manifest_path = project_root / "runs" / run_id / "manifest.json"
    if not record_path.is_file() or not manifest_path.is_file():
        return None
    try:
        record = read_json(record_path)
        run_manifest = read_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    shared_fields = (
        "run_id",
        "contract_hash",
        "code_hash",
        "is_baseline",
        "valid",
        "aggregate_metrics",
    )
    if any(record.get(field) != baseline.get(field) for field in shared_fields):
        return None
    if any(
        run_manifest.get(field) != record.get(field)
        for field in ("run_id", "contract_hash", "code_hash", "is_baseline")
    ):
        return None
    if not (
        record.get("isolation_verified")
        and run_manifest.get("isolation_verified")
        and record.get("runtime_attestation", {}).get("controlled_environment")
    ):
        return None
    selected_data = [
        path
        for path in local_paths
        if path.suffix.casefold() in {".jsonl", ".csv", ".tsv", ".parquet"}
        and "data" in {part.casefold() for part in path.parts}
    ]
    test_path = next(
        (path for path in selected_data if "test" in path.name.casefold()),
        None,
    )
    denominator: int | None = None
    if test_path and test_path.suffix.casefold() == ".jsonl":
        try:
            denominator = sum(
                1
                for line in test_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except OSError:
            denominator = None
    metrics = list(contract.get("metrics") or [])
    primary_metric = str(
        baseline.get("primary_metric")
        or (metrics[0].get("name") if metrics else "")
        or execution_contract.get("primary_metric")
        or task.get("primary_metric")
        or "primary_metric"
    )
    direction = str(
        execution_contract.get("direction")
        or (metrics[0].get("direction") if metrics else "")
        or task.get("direction")
        or "unknown"
    )
    return {
        "strategy": "reuse_local_verified_chain",
        "project_root": project_root.relative_to(source_root).as_posix(),
        "contract": contract,
        "execution_contract": execution_contract,
        "task": task,
        "baseline": baseline,
        "record": record,
        "run_manifest": run_manifest,
        "contract_path": contract_path,
        "evidence_path": evidence_path,
        "record_path": record_path,
        "manifest_path": manifest_path,
        "primary_metric": primary_metric,
        "direction": direction,
        "denominator": denominator,
        "selected_data_paths": [
            path.relative_to(source_root).as_posix() for path in selected_data
        ],
        "verified": True,
    }


_GENERATED_BASELINE_RUNNER = '''\
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--metric-field", required=True)
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in Path(args.input).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    eligible = [row for row in rows if row.get("eligible", True)]
    values = [
        float(row[args.metric_field])
        for row in eligible
        if isinstance(row.get(args.metric_field), (int, float))
        and not isinstance(row.get(args.metric_field), bool)
    ]
    if not eligible or len(values) != len(eligible):
        raise SystemExit(
            "Every eligible row must contain a numeric baseline metric."
        )
    result = {
        "schema_version": 1,
        "primary_metric": args.metric_field,
        "denominator": len(eligible),
        "aggregate_metrics": {
            args.metric_field: sum(values) / len(values),
        },
        "eligible_case_ids": [str(row["case_id"]) for row in eligible],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _local_database_environment_probe() -> dict[str, Any]:
    tools = {
        name: shutil.which(name)
        for name in ("supabase", "psql", "docker")
    }
    images: list[str] = []
    docker_error: str | None = None
    if tools["docker"]:
        try:
            completed = subprocess.run(
                [
                    str(tools["docker"]),
                    "image",
                    "ls",
                    "--format",
                    "{{.Repository}}:{{.Tag}}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if completed.returncode == 0:
                images = [
                    line.strip()
                    for line in completed.stdout.splitlines()
                    if line.strip()
                ]
            else:
                docker_error = completed.stderr.strip() or "docker image ls failed"
        except (OSError, subprocess.SubprocessError) as exc:
            docker_error = f"{type(exc).__name__}: {exc}"
    database_images = [
        image
        for image in images
        if any(cue in image.casefold() for cue in ("postgres", "supabase"))
    ]
    available = bool(
        tools["supabase"] or tools["psql"] or database_images
    )
    return {
        "schema_version": 1,
        "status": "available" if available else "missing",
        "tools": tools,
        "matching_local_images": database_images,
        "docker_probe_error": docker_error,
        "network_pull_attempted": False,
        "scientific_evidence_eligible": False,
    }


def _build_experiment_assets(context: "StepContext") -> dict[str, Any]:
    """Build an auditable experiment scaffold without fabricating observations."""

    scope = context.repository.latest_scope_contract(context.study_id)
    if (
        scope is None
        or scope.status is not ArtifactStatus.FROZEN
        or scope.contract_level != "specific_topic"
    ):
        raise _blocked("A frozen specific-topic Scope is required.")
    selection_path = _stage2_dir(
        context.repository, context.study_id
    ) / "resource_selection.json"
    selection = read_json(selection_path)
    candidates = [dict(item) for item in selection.get("candidates", [])]
    planned = [
        dict(item)
        for item in selection.get("planned_resource_requirements", [])
    ]
    selected_implementation = next(
        (
            item
            for item in candidates
            if item.get("need_type")
            in {
                ResourceNeedType.IMPLEMENTATION.value,
                ResourceNeedType.TOOLKIT.value,
            }
        ),
        None,
    )
    scope_text = " ".join(
        [
            scope.direction,
            scope.research_question,
            scope.primary_outcome or "",
            *[
                str(item)
                for item in (scope.field_diff or {}).get(
                    "academic_concepts", []
                )
            ],
        ]
    ).casefold()
    from .domain_adapters import is_finance_backtest_contract

    finance_backtest = is_finance_backtest_contract(
        {"scope": scope_text}
    )
    comparison_frame = _comparison_frame_for_scope(scope)
    comparison_frame = _comparison_frame_with_local_metric(
        context,
        scope,
        comparison_frame,
        selected_implementation,
    )
    selected_datasets = [
        item
        for item in candidates
        if item.get("need_type") == ResourceNeedType.DATASET.value
    ]
    selected_dataset_paths = [
        str(item.get("canonical_identifier") or "")
        for item in selected_datasets
        if str(item.get("canonical_identifier") or "").strip()
    ]
    primary_outcome_text = str(
        comparison_frame.get("primary_outcome") or ""
    ).casefold()
    has_frozen_split_dataset = any(
        Path(path).stem.casefold()
        in {"train", "training", "valid", "validation", "dev", "test"}
        for path in selected_dataset_paths
    )
    # A generic project still needs a real, owner-reviewable scientific
    # comparison.  When Stage 1 found labelled split files and a project
    # implementation, compile the conservative classifier comparison here
    # instead of handing Stage 3 phrases such as "the declared method".
    if (
        not finance_backtest
        and selected_implementation is not None
        and selected_datasets
        and has_frozen_split_dataset
        and (
            comparison_frame.get("profile_source", {}).get("kind")
            != "explicit_local_project_design"
        )
        and any(
            token in primary_outcome_text
            for token in ("accuracy", "classification", "precision", "recall")
        )
    ):
        implementation_path = str(
            selected_implementation.get("canonical_identifier")
            or "the owner-approved project predictor"
        )
        comparison_frame.update(
            {
                "research_question": (
                    "On the frozen held-out evaluation split, does the "
                    "owner-approved project predictor improve accuracy over "
                    "a training-split majority-class predictor?"
                ),
                "falsifiable_hypothesis": (
                    "The owner-approved project predictor increases held-out "
                    "accuracy by at least 0.05 relative to the frozen "
                    "training-split majority-class predictor."
                ),
                "comparator": "training-split majority-class predictor",
                "intervention": (
                    f"project predictor defined by {implementation_path}"
                ),
                "primary_outcome": "accuracy",
                "unit_of_analysis": (
                    "one row in the frozen held-out evaluation split"
                ),
                "denominator": (
                    "all schema-valid rows in the frozen held-out evaluation "
                    "split after preregistered exclusions"
                ),
                "minimum_meaningful_effect": 0.05,
                "success_threshold": 0.05,
                "threshold_basis": (
                    "absolute paired difference in held-out accuracy"
                ),
                "baseline_behavior": (
                    "Read the target column from every schema-valid row in "
                    "the frozen training split, choose the lexicographically "
                    "first label among labels tied for highest frequency, and "
                    "predict that single frozen majority label for every row "
                    "in the held-out evaluation split."
                ),
                "treatment_behavior": (
                    f"Load the owner-approved predictor from "
                    f"{implementation_path}, apply its declared prediction "
                    "entry point to the input fields of every row in the same "
                    "held-out evaluation split, and compare the predicted class "
                    "label with the same frozen target column."
                ),
                "data_boundary": {
                    "target_population": (
                        "all rows in the owner-approved frozen held-out "
                        "evaluation split"
                    ),
                    "sampling_frame": (
                        "deterministic census of every schema-valid held-out "
                        "row; no outcome-dependent sampling"
                    ),
                    "inclusion_rules": [
                        "row belongs to the frozen held-out split",
                        "required input fields and target label are present",
                    ],
                    "exclusion_rules": [
                        "schema-incompatible row",
                        "missing authoritative target label",
                    ],
                    "target_rule": (
                        "the exact target or label column frozen in the "
                        "Stage 2 dataset schema; string and numeric labels are "
                        "compared after lossless canonical serialization"
                    ),
                    "label_origin": (
                        "owner-approved content-addressed local split files"
                    ),
                    "denominator": (
                        "all schema-valid held-out rows after preregistered "
                        "exclusions"
                    ),
                    "unit_of_analysis": (
                        "one row in the frozen held-out evaluation split"
                    ),
                    "time_boundary": (
                        "training labels determine the majority baseline; "
                        "held-out labels remain evaluation-only"
                    ),
                    "preprocessing": (
                        "parse the frozen tabular schema, preserve declared "
                        "feature values, and reject schema-incompatible rows"
                    ),
                },
                "statistical_rules": {
                    "method": "paired row-level bootstrap",
                    "analysis": "paired row-level bootstrap",
                    "effect_threshold": 0.05,
                    "effect_scale": "absolute",
                    "effect_unit": "proportion",
                    "missing_cell_policy": "inconclusive",
                    "confidence_level": 0.95,
                    "bootstrap_resamples": 10_000,
                    "bootstrap_seed": 4242,
                    "resampling_unit": "held-out row",
                },
            }
        )
    framed_comparison = _comparison_frame_complete(comparison_frame)
    declared_seeds = [
        int(item)
        for item in (
            comparison_frame.get("seeds")
            or [11, 29, 47, 71, 97]
        )
    ]
    declared_arms = [
        str(item)
        for item in (
            comparison_frame.get("arms")
            or [
                str(comparison_frame.get("comparator") or "baseline"),
                str(comparison_frame.get("intervention") or "treatment"),
            ]
        )
        if str(item).strip()
    ]
    planned_run_count = len(declared_seeds) * max(2, len(declared_arms))
    advisor_recommendation = any(
        cue in scope_text
        for cue in (
            "学术导师推荐",
            "academic advisor",
            "professor recommendation",
            "academic recommender",
        )
    )
    stereo_geometry = any(
        cue in scope_text
        for cue in (
            "双目视觉",
            "双目",
            "stereo vision",
            "stereopolicy",
            "disparity prior",
            "cross-attention",
            "cross attention",
        )
    )
    database_governance = any(
        cue in scope_text
        for cue in (
            "行级安全",
            "row-level security",
            "row level security",
            "多租户",
            "multi-tenant",
        )
    )
    implementation_boundary = (
        [
            str(item["location"])
            for item in _ancestor_result(
                context, "inventory_research_resources"
            ).get("resources", [])
            if str(item.get("location", ""))
            .replace("\\", "/")
            .casefold()
            .startswith("migrations/")
            and str(item.get("location", "")).casefold().endswith(".sql")
        ]
        if database_governance
        else (
            [str(selected_implementation.get("canonical_identifier"))]
            if selected_implementation
            else []
        )
    )
    if finance_backtest:
        metric_name = "top_k_event_identification_rate"
        metric_direction = "maximize"
        denominator = str(
            comparison_frame.get("denominator")
            or (
                "all selected Top-K positions across every frozen eligible "
                "monthly decision snapshot"
            )
        )
        minimum_effect = "absolute increase of 0.05"
        success_threshold = (
            "the dual_quality_top5 arm exceeds v3_tech_quality_top5 by at "
            "least 0.05 in paired Top-K high-return event identification rate"
        )
        required_fields = {
            "case_id": "string",
            "eligible": "boolean",
            "decision_date": "date",
            "symbol": "string",
            "industry": "string",
            "rank_position": "integer",
            "target_reference": "string",
            "primary_event": "boolean",
            "v3_tech_quality_score": "number",
            "dual_quality_score": "number",
        }
        annotation = {
            "unit": (
                "one selected security position nested in an eligible monthly "
                "decision snapshot"
            ),
            "reviewers": (
                "deterministic point-in-time target builder and paired metric "
                "evaluator"
            ),
            "labels": [
                "primary_event",
                "not_primary_event",
                "ineligible",
                "target_unavailable",
            ],
            "primary_denominator": denominator,
            "secondary_outcomes": [
                "mean monthly executable return",
                "monthly hit count",
                "selection concentration",
            ],
            "prohibited_shortcut": (
                "A retrospective summary cannot be treated as prospective "
                "validation; formal evidence must retain decision-date rows, "
                "targets, arm selections, and point-in-time provenance."
            ),
        }
        baseline_name = str(comparison_frame["comparator"])
        treatment_name = str(comparison_frame["intervention"])
    elif framed_comparison and not (
        stereo_geometry or database_governance or advisor_recommendation
    ):
        primary_outcome = str(comparison_frame["primary_outcome"])
        metric_name = _metric_identifier(primary_outcome)
        metric_direction = (
            "minimize"
            if any(
                cue in primary_outcome.casefold()
                for cue in ("error", "failure", "unsupported", "latency", "risk")
            )
            else "maximize"
        )
        denominator = str(
            comparison_frame.get("denominator")
            or comparison_frame.get("unit_of_analysis")
            or "all preregistered eligible cases"
        )
        explicit_effect = comparison_frame.get(
            "minimum_meaningful_effect"
        )
        minimum_effect = (
            f"absolute difference of {explicit_effect}"
            if isinstance(explicit_effect, (int, float))
            else "a preregistered practically meaningful difference"
        )
        explicit_threshold = comparison_frame.get("success_threshold")
        success_threshold = (
            f"paired improvement of at least {explicit_threshold}"
            if isinstance(explicit_threshold, (int, float))
            else (
                f"the treatment produces the preregistered improvement in "
                f"{metric_name} relative to baseline without violating any "
                "protected secondary-outcome tolerance"
            )
        )
        required_fields = {
            "case_id": "string",
            "eligible": "boolean",
            f"baseline_{metric_name}": "number",
            "input_snapshot_ids": "array[string]",
        }
        secondary_outcomes = [
            str(item)
            for item in comparison_frame.get("secondary_outcomes", [])
        ]
        annotation = {
            "unit": denominator,
            "reviewers": (
                "deterministic metric evaluator plus an independent audit "
                "of sampled cases"
            ),
            "labels": [
                "eligible",
                "ineligible",
                "insufficient_context",
                "execution_error",
            ],
            "primary_denominator": denominator,
            "secondary_outcomes": secondary_outcomes,
            "matched_controls": [
                str(item)
                for item in comparison_frame.get("matched_controls", [])
            ],
            "prohibited_shortcut": (
                "A design document or implementation diff cannot establish "
                "the treatment effect; Stage 3 requires frozen cases and "
                "machine-readable paired outputs."
            ),
        }
        baseline_name = str(comparison_frame["comparator"])
        treatment_name = str(comparison_frame["intervention"])
    elif stereo_geometry:
        metric_name = "task_success_rate"
        metric_direction = "maximize"
        denominator = "all preregistered policy rollouts"
        minimum_effect = "absolute increase of 0.05"
        success_threshold = (
            "treatment task_success_rate is at least 0.05 above the "
            "unconstrained-cross-attention baseline, with no preregistered "
            "stress profile decreasing by more than 0.10"
        )
        required_fields = {
            "case_id": "string",
            "eligible": "boolean",
            "task": "string",
            "stress_profile": "string",
            "seed": "integer",
            "observation_hdf5_path": "string",
            "baseline_task_success": "number[0,1]",
            "baseline_alignment_error": "number>=0",
        }
        annotation = {
            "unit": "one frozen task-seed-stress-profile rollout",
            "reviewers": "deterministic rollout evaluator plus trajectory audit",
            "labels": [
                "success",
                "failure",
                "invalid_rollout",
                "environment_error",
            ],
            "primary_denominator": denominator,
            "secondary_outcomes": [
                "cross_attention_alignment_error",
                "weak_texture_success_rate",
                "occlusion_success_rate",
                "rollout_latency_ms",
            ],
            "prohibited_shortcut": (
                "Offline attention alignment alone cannot establish policy "
                "success; formal Stage 3 evidence requires frozen rollouts."
            ),
        }
        baseline_name = "unconstrained stereo cross-attention"
        treatment_name = (
            "confidence-seeded sparse disparity-guided cross-attention"
        )
    elif database_governance:
        metric_name = "policy_violation_acceptance_rate"
        metric_direction = "minimize"
        denominator = (
            "all preregistered unauthorized or integrity-violating transactions"
        )
        minimum_effect = "absolute reduction of 0.10"
        success_threshold = (
            "treatment policy_violation_acceptance_rate = 0 and is at least "
            "0.10 below baseline, while valid_transaction_success_rate does "
            "not decrease by more than 0.01"
        )
        required_fields = {
            "case_id": "string",
            "eligible": "boolean",
            "operation": "string",
            "actor_tenant_id": "string",
            "target_tenant_id": "string",
            "expected_authorization": "boolean",
            "integrity_violation": "boolean",
            "baseline_policy_violation_acceptance_rate": "number[0,1]",
            "baseline_latency_ms": "number",
        }
        annotation = {
            "unit": "one frozen database authorization or integrity scenario",
            "reviewers": "deterministic policy oracle plus independent scenario audit",
            "labels": [
                "authorized",
                "unauthorized",
                "integrity_violation",
                "invalid_scenario",
            ],
            "primary_denominator": denominator,
            "secondary_outcomes": [
                "valid_transaction_success_rate",
                "p95_latency_ms",
                "orphan_record_acceptance_rate",
            ],
            "prohibited_shortcut": (
                "Migration text alone cannot prove runtime policy enforcement; "
                "every scenario must execute against an isolated database."
            ),
        }
        baseline_name = "application-layer authorization only"
        treatment_name = (
            "database-enforced RLS, evidence foreign keys, and audit constraints"
        )
    elif advisor_recommendation:
        metric_name = "unsupported_claim_rate"
        metric_direction = "minimize"
        denominator = "all eligible factual recommendation claims"
        minimum_effect = "absolute reduction of 0.10"
        success_threshold = (
            "treatment unsupported_claim_rate <= baseline - 0.10, while "
            "expert_relevance_mean is no more than 0.05 below baseline"
        )
        required_fields = {
            "case_id": "string",
            "eligible": "boolean",
            "applicant_profile": "object",
            "candidate_professors": "array",
            "evidence_snapshot_ids": "array[string]",
            "baseline_unsupported_claim_rate": "number[0,1]",
            "baseline_expert_relevance_mean": "number[0,1]",
        }
        annotation = {
            "unit": "one frozen applicant-professor recommendation case",
            "reviewers": "two blinded reviewers plus adjudication",
            "labels": [
                "supported",
                "unsupported",
                "insufficient_context",
                "not_a_factual_claim",
            ],
            "primary_denominator": denominator,
            "secondary_outcome": "expert_relevance_mean",
            "prohibited_shortcut": (
                "Synthetic labels or model self-grades cannot replace blinded "
                "human relevance and claim-support annotations."
            ),
        }
        baseline_name = "single-pass professor recommendation"
        treatment_name = (
            "structured evidence scoring, risk constraints, and review"
        )
    else:
        metric_name = _metric_identifier(
            str(comparison_frame.get("primary_outcome") or "")
        )
        metric_direction = (
            "minimize"
            if any(
                cue in metric_name
                for cue in ("error", "failure", "latency", "risk")
            )
            else "maximize"
        )
        denominator = str(
            comparison_frame.get("denominator")
            or comparison_frame.get("unit_of_analysis")
            or "all eligible frozen evaluation cases"
        )
        explicit_effect = comparison_frame.get("minimum_meaningful_effect")
        minimum_effect = (
            f"absolute difference of {explicit_effect}"
            if isinstance(explicit_effect, (int, float))
            else "a preregistered practically meaningful difference"
        )
        explicit_threshold = comparison_frame.get("success_threshold")
        success_threshold = (
            f"paired improvement of at least {explicit_threshold}"
            if isinstance(explicit_threshold, (int, float))
            else (
                f"the treatment produces the preregistered improvement in "
                f"{metric_name} relative to baseline"
            )
        )
        required_fields = {
            "case_id": "string",
            "eligible": "boolean",
            "input": "object|string",
            "reference": "object|string|null",
            "baseline_primary_metric": "number",
        }
        annotation = {
            "unit": "one frozen evaluation case",
            "reviewers": "domain-appropriate independent review when required",
            "labels": ["eligible", "ineligible", "abstain"],
            "primary_denominator": denominator,
            "prohibited_shortcut": (
                "Generated fixtures may test execution integrity but cannot "
                "stand in for domain-valid scientific observations."
            ),
        }
        baseline_name = str(
            comparison_frame.get("comparator") or "existing project pipeline"
        )
        treatment_name = str(
            comparison_frame.get("intervention")
            or scope.candidate_contribution
            or "candidate contribution enabled"
        )

    missing_types = [
        str(item.get("need_type"))
        for item in planned
        if item.get("status") == "planned"
    ]
    environment_probe = (
        _local_database_environment_probe()
        if database_governance
        else {
            "schema_version": 1,
            "status": "available",
            "adapter": "research_forge_generic_feasibility_mvp_v1",
            "tools": {"python_standard_library": True},
            "matching_local_images": [],
            "network_pull_attempted": False,
            "scientific_evidence_eligible": False,
        }
        if framed_comparison
        else {
            "schema_version": 1,
            "status": "adapter_required",
            "tools": {},
            "matching_local_images": [],
            "network_pull_attempted": False,
            "scientific_evidence_eligible": False,
        }
    )
    build_status = (
        "mvp_ready_to_implement"
        if environment_probe["status"] == "available"
        else "mvp_environment_required"
        if environment_probe["status"] == "missing"
        else "mvp_adapter_required"
    )
    workspace = _stage2_dir(
        context.repository, context.study_id
    ) / "experiment_build"
    manifest = {
        "schema_version": 1,
        "experiments": [
            {
                "experiment_id": "stage2-generated-baseline-v1",
                "action_ids": ["action-stage2-generated-baseline"],
                "title": baseline_name,
                "command": [
                    "{python}",
                    "baseline_runner.py",
                    "--input",
                    "data/evaluation.jsonl",
                    "--output",
                    "{metrics_file}",
                    "--metric-field",
                    (
                        "baseline_policy_violation_acceptance_rate"
                        if database_governance
                        else "baseline_unsupported_claim_rate"
                        if advisor_recommendation
                        else metric_name
                    ),
                ],
                "cwd": ".",
                "timeout_seconds": 600,
                "required_inputs": ["data/evaluation.jsonl"],
                "network_access": False,
                "artifacts": [
                    {
                        "path": "metrics.json",
                        "format": "json",
                        "required_keys": [
                            "primary_metric",
                            "denominator",
                            "aggregate_metrics",
                        ],
                    }
                ],
            }
        ],
    }
    dataset_spec = {
        "schema_version": 1,
        "status": (
            "awaiting_owner_approved_or_domain_valid_rows"
            if ResourceNeedType.DATASET.value in missing_types
            else "selected_resource_available"
        ),
        "format": "jsonl",
        "path": "data/evaluation.jsonl",
        "required_fields": required_fields,
        "minimum_cases": (
            24 if database_governance else 20 if advisor_recommendation else 10
        ),
        "split_policy": {
            "development": "may be used to debug the runner",
            "test": "must be frozen before baseline measurement",
        },
        "scientific_guard": (
            "The system may generate schemas and non-evidentiary fixtures, "
            "but it must not fabricate observations or reference labels."
        ),
    }
    mvp_spec = {
        "schema_version": 1,
        "purpose": "non-scientific Stage 2 feasibility validation",
        "scientific_evidence_eligible": False,
        "minimum_smoke_cases": 3,
        "required_checks": [
            "minimum environment starts",
            "baseline and treatment abstractions can both be instantiated",
            "metric denominator and value can be computed",
            "expected failures are distinguishable from infrastructure errors",
            "runtime and reset cost can be estimated",
        ],
        "exit_rule": (
            "All smoke cases pass without using their values for a scientific "
            "effect estimate or Study verdict."
        ),
        "environment_probe": environment_probe,
    }
    stage3_resource_plan = {
        "schema_version": 1,
        "phase": Phase.EXPERIMENT.value,
        "scientific_evidence_eligible": False,
        "tasks": [
            {
                "task_type": "provision_environment",
                "detail": (
                    "Provision the frozen database/application runtime and "
                    "record its immutable environment lock."
                    if database_governance
                    else "Provision the contract-authorized experiment runtime."
                ),
            },
            *(
                [
                    {
                        "task_type": "materialize_stereo_observations",
                        "detail": (
                            "Materialize contract-authorized stereo "
                            "observations from aligned raw state/action "
                            "trajectories, then freeze the resulting HDF5 "
                            "snapshots."
                        ),
                    }
                ]
                if stereo_geometry
                else []
            ),
            {
                "task_type": "construct_formal_dataset",
                "detail": (
                    f"Create and freeze at least {dataset_spec['minimum_cases']} "
                    "schema-valid formal evaluation cases."
                ),
            },
            {
                "task_type": "bind_experiment_arms",
                "detail": "Bind executable baseline and treatment commands.",
            },
            {
                "task_type": "freeze_execution_contract_vnext",
                "detail": (
                    "Freeze exact runtime, full run matrix, data hashes, and "
                    "commands before viewing formal results."
                ),
            },
            {
                "task_type": "run_formal_experiment",
                "detail": (
                    "Execute the complete baseline and treatment matrix, then "
                    "materialize evidence and scientific verdicts."
                ),
            },
        ],
    }
    treatment_declaration = {
        "schema_version": 1,
        "name": treatment_name,
        "behavior": comparison_frame.get("treatment_behavior"),
        "selected_implementation": (
            selected_implementation.get("canonical_identifier")
            if selected_implementation
            else None
        ),
        "implementation_boundary": implementation_boundary,
        "execution_phase": Phase.EXPERIMENT.value,
        "stage2_execution_prohibited": True,
        "required_output_schema": {
            "case_id": "string",
            "claims": "array",
            "evidence_bindings": "array",
            "primary_metric_components": "object",
        },
    }
    artifact_ids = [
        _write_named_artifact(
            context,
            "experiment_build/dataset_spec.json",
            dataset_spec,
            role=ArtifactRole.PROTOCOL,
        ),
        _write_named_artifact(
            context,
            "experiment_build/annotation_protocol.json",
            annotation,
            role=ArtifactRole.PROTOCOL,
        ),
        _write_named_artifact(
            context,
            "experiment_build/mvp_spec.json",
            mvp_spec,
            role=ArtifactRole.PROTOCOL,
        ),
        _write_named_artifact(
            context,
            "experiment_build/stage3_resource_plan.json",
            stage3_resource_plan,
            role=ArtifactRole.PROTOCOL,
        ),
        _write_named_artifact(
            context,
            "experiment_build/research-forge.experiments.json",
            manifest,
            role=ArtifactRole.PROTOCOL,
        ),
        _write_named_artifact(
            context,
            "experiment_build/baseline_runner.py",
            _GENERATED_BASELINE_RUNNER,
            role=ArtifactRole.OTHER,
        ),
        _write_named_artifact(
            context,
            "experiment_build/treatment_declaration.json",
            treatment_declaration,
            role=ArtifactRole.PROTOCOL,
        ),
    ]
    build = {
        "schema_version": 1,
        "status": build_status,
        "workspace_root": str(workspace),
        "experiment_manifest_path": "research-forge.experiments.json",
        "dataset_spec_path": "dataset_spec.json",
        "annotation_protocol_path": "annotation_protocol.json",
        "mvp_spec_path": "mvp_spec.json",
        "stage3_resource_plan_path": "stage3_resource_plan.json",
        "runner_path": "baseline_runner.py",
        "treatment_declaration_path": "treatment_declaration.json",
        "missing_resource_types": missing_types,
        "stage2_mvp": {
            "status": build_status,
            "verified": False,
            "scientific_evidence_eligible": False,
            "minimum_smoke_cases": 3,
            "environment_probe": environment_probe,
        },
        "stage3_resource_tasks": stage3_resource_plan["tasks"],
        "selected_implementation": (
            selected_implementation.get("canonical_identifier")
            if selected_implementation
            else None
        ),
        "implementation_boundary": implementation_boundary,
        "protocol_defaults": {
            "primary_metric": metric_name,
            "metric_direction": metric_direction,
            "denominator": denominator,
            "sample_size": dataset_spec["minimum_cases"],
            "statistical_power": (
                "bounded paired evaluation; uncertainty reported with a "
                "cluster-aware bootstrap"
            ),
            "statistical_analysis": (
                "paired difference with task-level bootstrap confidence interval"
            ),
            "statistical_test": "paired cluster-aware bootstrap",
            "confidence_interval": "95% task-level bootstrap interval",
            "seeds": declared_seeds,
            "repetitions": len(declared_seeds),
            "falsification_condition": (
                "the frozen success threshold is not met"
            ),
            "success_threshold": success_threshold,
            "minimum_meaningful_effect": minimum_effect,
            "threshold_basis": comparison_frame.get("threshold_basis"),
            "metric_implementation": "experiment_build/baseline_runner.py",
            "baseline_name": baseline_name,
            "baseline_behavior": comparison_frame.get("baseline_behavior"),
            "baseline_experiment_id": "stage2-generated-baseline-v1",
            "baseline_action_id": "action-stage2-generated-baseline",
            "treatment_name": treatment_name,
            "treatment_behavior": comparison_frame.get(
                "treatment_behavior"
            ),
            "data_boundary": dict(
                comparison_frame.get("data_boundary") or {}
            ),
            **(
                {
                    "experiment_profile": comparison_frame[
                        "experiment_profile"
                    ],
                    "tasks": comparison_frame["tasks"],
                    "seeds": comparison_frame["seeds"],
                    "splits": comparison_frame["splits"],
                    "baseline_experiment_id": comparison_frame[
                        "baseline_experiment_id"
                    ],
                    "baseline_action_id": comparison_frame[
                        "baseline_action_id"
                    ],
                    "treatment_experiment_id": comparison_frame[
                        "treatment_experiment_id"
                    ],
                    "treatment_action_id": comparison_frame[
                        "treatment_action_id"
                    ],
                    "output_schema": comparison_frame["output_schema"],
                    "statistical_rules": dict(
                        comparison_frame.get("statistical_rules") or {}
                    ),
                }
                if comparison_frame.get("experiment_profile")
                else {}
            ),
            "randomization_method": "frozen case order and registered seeds",
            "dependency_lock": "Research Forge runtime plus project dependency lock",
            "quality_control": [
                "schema validation",
                "content hashes",
                "blinded annotation where required",
                "append-only run records",
            ],
            "reproduction_steps": [
                "supply an owner-approved evaluation.jsonl",
                "validate it against dataset_spec.json",
                "run the generated baseline manifest in an isolated snapshot",
            ],
            "budget": {
                "max_runs": max(10, planned_run_count),
                "timeout_seconds": 600,
            },
        },
    }
    plan_artifact_id = _write_named_artifact(
        context,
        "experiment_build_plan.json",
        build,
        role=ArtifactRole.PROTOCOL,
    )
    return {
        "experiment_build": build,
        "_workflow_output_artifact_ids": [*artifact_ids, plan_artifact_id],
    }


def _protocol_draft(context: "StepContext") -> dict[str, Any]:
    scope = context.repository.latest_scope_contract(context.study_id)
    if (
        scope is None
        or scope.status is not ArtifactStatus.FROZEN
        or scope.contract_level != "specific_topic"
    ):
        raise _blocked("A frozen specific-topic Scope is required.")
    inventory = _ancestor_result(context, "inventory_research_resources")
    method_result = _ancestor_result(context, "investigate_related_methods")
    data_boundary = _ancestor_result(context, "define_data_boundary")[
        "data_boundary"
    ]
    study = context.repository.load_study(context.study_id)
    experiment_build = _optional_ancestor_result(
        context, "build_experiment_assets"
    ).get("experiment_build", {})
    mvp_override = dict(study.settings.get("stage2_mvp_override") or {})
    if mvp_override:
        experiment_build = {
            **experiment_build,
            "status": "mvp_verified",
            "stage2_mvp": mvp_override,
        }
    workspace_override = study.settings.get(
        "stage2_experiment_workspace_override"
    )
    if workspace_override:
        experiment_build = {
            **experiment_build,
            "workspace_root": str(workspace_override),
            "status": "ready_for_baseline",
            "missing_resource_types": [],
            "stage2_mvp": {
                **dict(experiment_build.get("stage2_mvp") or {}),
                "status": "verified",
                "verified": True,
                "scientific_evidence_eligible": False,
            },
            "input_hashes": dict(
                study.settings.get("stage2_experiment_input_hashes") or {}
            ),
        }
    overrides = {
        **dict(experiment_build.get("protocol_defaults") or {}),
        **dict(study.settings.get("stage2_protocol_overrides") or {}),
    }
    assertion_source = dict(
        study.settings.get("stage2_protocol_declaration") or {}
    )
    boundary_overrides = dict(overrides.get("data_boundary") or {})
    data_boundary = {**data_boundary, **boundary_overrides}
    selection_path = _stage2_dir(
        context.repository, context.study_id
    ) / "resource_selection.json"
    resource_selection = (
        read_json(selection_path) if selection_path.is_file() else {}
    )
    approved_resource_ids = [
        str(item)
        for item in resource_selection.get("selected_resource_ids", [])
    ]
    selected_candidates = [
        dict(item) for item in resource_selection.get("candidates", [])
    ]
    project = context.repository.load_project(study.project_id)
    imported_chain = _selected_verified_research_chain(
        Path(project.source_root or "").resolve(),
        selected_candidates,
    )
    declared_manifest, declared_manifest_path = (
        load_project_experiment_manifest(project.source_root or "")
    )
    requested_baseline_id = (
        overrides.get("baseline_experiment_id")
        or study.settings.get("stage2_baseline_experiment_id")
    )
    declared_baseline_spec = (
        next(
            (
                item
                for item in declared_manifest.experiments
                if item.experiment_id == requested_baseline_id
            ),
            None,
        )
        if declared_manifest and requested_baseline_id
        else None
    )
    if imported_chain:
        task = imported_chain["task"]
        execution_contract = imported_chain["execution_contract"]
        baseline = imported_chain["baseline"]
        denominator = imported_chain.get("denominator")
        imported_defaults = {
            "primary_metric": imported_chain["primary_metric"],
            "metric_direction": imported_chain["direction"],
            "denominator": denominator if denominator is not None else "frozen test split",
            "sample_size": (
                f"{denominator} frozen test units"
                if denominator is not None
                else "all units in the frozen test split"
            ),
            "statistical_power": (
                "not applicable to the imported deterministic benchmark census; "
                "repeat stability is reported"
            ),
            "statistical_analysis": (
                "descriptive comparison of frozen evaluator outputs across "
                "registered repeats"
            ),
            "statistical_test": "registered repeated-run descriptive comparison",
            "confidence_interval": (
                "not applicable to the imported deterministic benchmark census"
            ),
            "seeds": list(task.get("seeds") or [0]),
            "repetitions": int(
                execution_contract.get("required_repeats")
                or task.get("required_repeats")
                or 1
            ),
            "falsification_condition": (
                "the candidate does not exceed the frozen baseline by the "
                "registered minimum delta"
            ),
            "success_threshold": str(
                task.get("target_score")
                or f"improvement > {execution_contract.get('min_delta', 0.0)}"
            ),
            "minimum_meaningful_effect": str(
                execution_contract.get("min_delta", task.get("min_delta", 0.0))
            ),
            "metric_implementation": (
                f"{imported_chain['project_root']}/evaluator/evaluate.py "
                "(protected-manifest hash verified)"
            ),
            "randomization_method": "registered frozen seed schedule",
            "dependency_lock": "verified controlled Docker runtime attestation",
            "quality_control": (
                "protected evaluator hashes, repeated-run agreement, and "
                "append-only run records"
            ),
            "reproduction_steps": (
                "reuse the frozen execution contract, protected evaluator, "
                "selected data, and registered seed schedule"
            ),
            "baseline_experiment_id": baseline["run_id"],
            "baseline_name": str(
                imported_chain["contract"].get("baseline_definition")
                or baseline["run_id"]
            ),
            "data_loadable": True,
            "data_schema_readable": True,
            "inclusion_exclusion_executable": True,
            "split_boundary_verified": True,
            "leakage_controls_verified": True,
            "metric_unit_test_passed": True,
            "statistical_synthetic_test_passed": True,
            "dependency_installable": True,
            "hardware_sufficient": True,
            "logging_validated": True,
            "seed_fixable": True,
            "artifact_preservation_validated": True,
            "binding_validated": True,
            "failure_detection_validated": True,
            "budget_reasonable": True,
            "sensitive_data_guard_passed": True,
            "compliance_cleared": True,
            "budget": {
                "max_runs": execution_contract.get("max_runs"),
                "timeout_seconds": execution_contract.get("timeout_seconds"),
            },
            "approved_resource_substitutions": [
                {
                    "gap_id": "gap-leakage-controls",
                    "replacement": (
                        "protected frozen benchmark split and evaluator chain"
                    ),
                    "evidence": imported_chain["manifest_path"]
                    .relative_to(Path(project.source_root or "").resolve())
                    .as_posix(),
                }
            ],
        }
        overrides = {**imported_defaults, **overrides}
        data_boundary = {
            **data_boundary,
            "population_or_corpus": imported_chain["selected_data_paths"],
            "denominator": imported_defaults["denominator"],
            "split_policy": {
                "train": any(
                    "train" in Path(path).name.casefold()
                    for path in imported_chain["selected_data_paths"]
                ),
                "validation": any(
                    any(token in Path(path).name.casefold() for token in ("valid", "dev"))
                    for path in imported_chain["selected_data_paths"]
                ),
                "test": any(
                    "test" in Path(path).name.casefold()
                    for path in imported_chain["selected_data_paths"]
                ),
            },
            "status": "verified",
            "evidence_status": "verified",
        }
    previous_contract = context.repository.latest_research_contract(
        context.study_id
    )
    contract_version = (
        previous_contract.version + 1 if previous_contract is not None else 1
    )
    protocol_resource_binding_ids: list[str] = []
    external_selected = [
        item
        for item in selected_candidates
        if item.get("source_kind") == "external_retrieval"
    ]
    if external_selected:
        from .retrieval.domain.models import (
            ContractRef,
            RetrievalPhase,
        )
        from .retrieval.interfaces.service import RetrievalGateway

        gateway = RetrievalGateway(str(context.repository.root))
        existing_bindings = gateway.repository.list_bindings(context.study_id)
        field_by_need = {
            ResourceNeedType.DATASET.value: "data_boundary",
            ResourceNeedType.IMPLEMENTATION.value: "baseline",
            ResourceNeedType.TOOLKIT.value: "runtime_binding",
            ResourceNeedType.MODEL.value: "runtime_binding",
            ResourceNeedType.BENCHMARK.value: "metrics",
        }
        for selected in external_selected:
            sources = [
                item
                for item in existing_bindings
                if item.resource_id == selected["resource_id"]
                and item.phase is RetrievalPhase.PROTOCOL
            ]
            if not sources:
                continue
            target_field = field_by_need.get(
                str(selected.get("need_type")), "runtime_binding"
            )
            promoted = gateway.promote_binding(
                sources[-1].binding_id,
                target_phase=RetrievalPhase.PROTOCOL,
                step_instance_id=context.step.step_instance_id,
                purpose="protocol_grounding",
                usage_role="owner_selected_contract_resource",
                target_type="research_contract",
                target_id=(
                    f"{context.study_id}:research-v{contract_version}"
                ),
                target_field=target_field,
                contract_refs=[
                    ContractRef(
                        contract_type="research",
                        contract_id=(
                            f"{context.study_id}:research-v{contract_version}"
                        ),
                        version=contract_version,
                        field=target_field,
                    )
                ],
            )
            protocol_resource_binding_ids.append(promoted.binding_id)
    data_boundary["approved_dataset_ids"] = [
        item["resource_id"]
        for item in selected_candidates
        if item.get("need_type") == ResourceNeedType.DATASET.value
    ]
    data_boundary["resource_selection_id"] = resource_selection.get(
        "selection_id"
    )
    metric = (
        str(overrides.get("primary_metric") or scope.primary_outcome)
        if scope.primary_outcome
        and not scope.primary_outcome.startswith("to be selected")
        else str(overrides.get("primary_metric") or "primary_metric")
    )
    draft = {
        "schema_version": 1,
        "study_id": context.study_id,
        "research_contract_version": contract_version,
        "scope_version": scope.version,
        "experiment_profile": overrides.get("experiment_profile"),
        "research_question": scope.research_question,
        "resource_strategy": (
            {
                "mode": "reuse_local_verified_chain",
                "source_project_root": imported_chain["project_root"],
                "baseline_run_id": imported_chain["baseline"]["run_id"],
                "verification": "contract, protected artifacts, run record, and manifest",
            }
            if imported_chain
            else {
                "mode": "declared_project_experiment",
                "status": "ready_for_baseline",
                "source_project_root": str(
                    Path(project.source_root or "").resolve()
                ),
                "experiment_manifest_path": (
                    str(declared_manifest_path)
                    if declared_manifest_path
                    else None
                ),
            }
            if declared_baseline_spec
            else {
                "mode": "build_or_acquire_experiment",
                "allowed_paths": [
                    "reuse_local_resources",
                    "acquire_owner_approved_external_resources",
                    "build_new_bounded_experiment",
                ],
                "status": experiment_build.get(
                    "status", "planning_required"
                ),
                "workspace_root": experiment_build.get("workspace_root"),
                "experiment_manifest_path": experiment_build.get(
                    "experiment_manifest_path"
                ),
                "dataset_spec_path": experiment_build.get(
                    "dataset_spec_path"
                ),
                "annotation_protocol_path": experiment_build.get(
                    "annotation_protocol_path"
                ),
                "mvp_spec_path": experiment_build.get("mvp_spec_path"),
                "stage2_mvp": experiment_build.get("stage2_mvp", {}),
                "stage3_resource_plan_path": experiment_build.get(
                    "stage3_resource_plan_path"
                ),
                "stage3_resource_tasks": experiment_build.get(
                    "stage3_resource_tasks", []
                ),
                "stage3_missing_resource_types": experiment_build.get(
                    "missing_resource_types", []
                ),
                "input_hashes": experiment_build.get("input_hashes", {}),
            }
        ),
        "primary_hypothesis": (
            scope.field_diff.get("selected_hypothesis")
            or f"The declared method changes {metric} relative to the baseline."
        ),
        "null_hypothesis": str(
            overrides.get("null_hypothesis")
            or f"The declared method does not change {metric} relative to the baseline."
        ),
        "alternative_hypothesis": str(
            overrides.get("alternative_hypothesis")
            or scope.field_diff.get("selected_hypothesis")
            or f"The declared method changes {metric} relative to the baseline."
        ),
        "hypothesis_direction": str(
            overrides.get("hypothesis_direction")
            or overrides.get("metric_direction")
            or "unknown"
        ),
        "falsification_condition": str(
            overrides.get("falsification_condition") or "unknown"
        ),
        "hypothesis_roles": {"primary": 1, "secondary": 0},
        "study_design": scope.study_design or "controlled computational study",
        "tasks": [str(item) for item in overrides.get("tasks", [])],
        "unit_of_analysis": (
            str(data_boundary.get("unit_of_analysis") or "").strip()
            or scope.unit_of_analysis
            or "computational run"
        ),
        "population_or_corpus": scope.population_or_corpus or "bounded project data",
        "experimental_unit": (
            str(data_boundary.get("unit_of_analysis") or "").strip()
            or scope.unit_of_analysis
            or "computational run"
        ),
        "variance_unit": str(
            overrides.get("variance_unit")
            or data_boundary.get("unit_of_analysis")
            or scope.unit_of_analysis
            or "unknown"
        ),
        "sample_population": (
            scope.population_or_corpus or "bounded project data"
        ),
        "inclusion_criteria": [
            str(item)
            for item in overrides.get(
                "inclusion_criteria",
                data_boundary.get("inclusion_rules", []),
            )
        ],
        "exclusion_criteria": [
            str(item)
            for item in overrides.get(
                "exclusion_criteria",
                data_boundary.get("exclusion_rules", []),
            )
        ],
        "data_version": str(
            overrides.get("data_version") or "content-addressed local manifest"
        ),
        "data_split": overrides.get(
            "data_split", data_boundary.get("split_policy", {})
        ),
        "data_preprocessing": str(
            overrides.get("data_preprocessing") or "unknown"
        ),
        "data_boundary": data_boundary,
        "baseline": {
            "name": str(
                overrides.get("baseline_name") or "declared project baseline"
            ),
            "experiment_id": (
                overrides.get("baseline_experiment_id")
                or study.settings.get("stage2_baseline_experiment_id")
            ),
            "action_id": (
                overrides.get("baseline_action_id")
                or study.settings.get("stage2_baseline_action_id")
            ),
            "behavior": str(
                overrides.get("baseline_behavior")
                or (
                    "Run the frozen existing project pipeline on every "
                    "eligible case with the candidate contribution disabled."
                )
            ),
        },
        "treatment": {
            "name": str(
                overrides.get("treatment_name")
                or "declared Stage 3 treatment"
            ),
            "experiment_id": overrides.get("treatment_experiment_id"),
            "action_id": overrides.get("treatment_action_id"),
            "behavior": str(
                overrides.get("treatment_behavior")
                or (
                    "Run the identical frozen pipeline on the same paired "
                    "cases, changing only the registered intervention."
                )
            ),
            "stage2_execution_prohibited": True,
        },
        # This is an owner-approved feasibility action, not a scientific
        # result. Persist it in each protocol draft so a vNext edit does not
        # silently clear the approval and skip the baseline smoke run.
        "run_baseline": bool(
            overrides.get(
                "run_baseline",
                study.settings.get("stage2_run_baseline", False),
            )
        ),
        "experimental_group": str(
            overrides.get("experimental_group")
            or "declared Stage 3 treatment"
        ),
        "control_group": str(
            overrides.get("control_group")
            or overrides.get("baseline_name")
            or "declared project baseline"
        ),
        "minimum_reasonable_baseline": str(
            overrides.get("minimum_reasonable_baseline")
            or overrides.get("baseline_name")
            or "declared project baseline"
        ),
        "current_project_baseline": str(
            overrides.get("current_project_baseline")
            or overrides.get("baseline_name")
            or "declared project baseline"
        ),
        "academic_standard_baseline": str(
            overrides.get("academic_standard_baseline") or "unknown"
        ),
        "strongest_feasible_baseline": str(
            overrides.get("strongest_feasible_baseline") or "unknown"
        ),
        "primary_metric": {
            "name": metric,
            "direction": str(
                overrides.get("metric_direction")
                or "predeclare_before_freeze"
            ),
            "denominator": overrides.get(
                "denominator", data_boundary.get("denominator", "unknown")
            ),
            "implementation": str(
                overrides.get("metric_formula")
                or overrides.get("metric_implementation")
                or "mean(per_unit_metric_value)"
            ),
            **(
                {
                    "measurement_protocol": dict(
                        overrides["measurement_protocol"]
                    )
                }
                if isinstance(overrides.get("measurement_protocol"), dict)
                else {}
            ),
        },
        "secondary_metrics": [
            dict(item)
            for item in overrides.get("secondary_metrics", [])
            if isinstance(item, dict) and item.get("name")
        ],
        "metric_implementation": str(
            overrides.get("metric_implementation") or "unknown"
        ),
        "success_threshold": str(
            overrides.get("success_threshold") or "unknown"
        ),
        "minimum_meaningful_effect": str(
            overrides.get("minimum_meaningful_effect") or "unknown"
        ),
        "estimand": "difference between the frozen treatment and baseline conditions",
        "exclusion_policy": "must be frozen before Stage 3",
        "sampling_or_split_strategy": overrides.get(
            "sampling_or_split_strategy",
            data_boundary.get("split_policy", {}),
        ),
        "seeds": [int(item) for item in overrides.get("seeds", [])],
        "randomization_method": str(
            overrides.get("randomization_method") or "unknown"
        ),
        "repetitions": int(overrides.get("repetitions", 1)),
        "sample_size": str(
            overrides.get("sample_size")
            or overrides.get("sample_size_or_power")
            or "unknown"
        ),
        "statistical_power": str(
            overrides.get("statistical_power") or "unknown"
        ),
        # Read compatibility for clients that used the earlier combined field.
        "sample_size_or_power": str(
            overrides.get("sample_size_or_power")
            or overrides.get("sample_size")
            or "unknown"
        ),
        "statistical_analysis": str(
            overrides.get("statistical_analysis")
            or "must be selected and frozen before Stage 3"
        ),
        "statistical_test": str(
            overrides.get("statistical_test")
            or overrides.get("statistical_analysis")
            or "unknown"
        ),
        "confidence_interval": str(
            overrides.get("confidence_interval") or "unknown"
        ),
        "multiple_comparison_policy": str(
            overrides.get("multiple_comparison_policy")
            or "not_applicable unless multiple endpoints exist"
        ),
        "missing_data_policy": str(
            overrides.get("missing_data_policy")
            or (
                "Cases missing required input or target fields are excluded "
                "before denominator freeze and recorded with a reason; missing "
                "outcomes after eligibility are retained as inconclusive, with "
                "no imputation."
            )
        ),
        "outlier_policy": str(
            overrides.get("outlier_policy") or "unknown"
        ),
        "failed_run_policy": str(
            overrides.get("failed_run_policy")
            or "execution failures are unverifiable and retained"
        ),
        "retry_rule": str(
            overrides.get("retry_rule")
            or "no automatic scientific retry"
        ),
        "timeout_rule": str(
            overrides.get("timeout_rule")
            or "use the declared baseline or Stage 3 command timeout"
        ),
        "robustness_checks": [
            str(item) for item in overrides.get("robustness_checks", [])
        ],
        "leakage_controls": [
            str(item) for item in overrides.get("leakage_controls", [])
        ],
        "stopping_rule": "complete all eligible preregistered units",
        "budget": dict(overrides.get("budget") or {}),
        "failure_and_abstention_policy": (
            "execution failures are unverifiable, not refuted"
        ),
        "environment": {
            **inventory["compute"],
            "approved_resource_ids": [
                item["resource_id"]
                for item in selected_candidates
                if item.get("source_kind") == "external_retrieval"
            ],
            "approved_local_resource_ids": [
                item["resource_id"]
                for item in selected_candidates
                if item.get("source_kind") == "local_project"
            ],
            "all_selected_resource_ids": approved_resource_ids,
            "approved_resource_binding_ids": protocol_resource_binding_ids,
            "approved_implementation_ids": [
                item["resource_id"]
                for item in selected_candidates
                if item.get("need_type")
                in {
                    ResourceNeedType.IMPLEMENTATION.value,
                    ResourceNeedType.TOOLKIT.value,
                }
            ],
            "resource_selection_id": resource_selection.get("selection_id"),
            "resource_selection_sha256": (
                sha256_file(selection_path) if selection_path.is_file() else None
            ),
        },
        "software_versions": {
            "python": inventory["compute"].get("python"),
            **dict(overrides.get("software_versions") or {}),
        },
        "hardware_environment": inventory["compute"],
        "model_versions": dict(overrides.get("model_versions") or {}),
        "api_versions": dict(overrides.get("api_versions") or {}),
        "dependency_lock": str(
            overrides.get("dependency_lock") or "unknown"
        ),
        "logging_requirements": [
            str(item)
            for item in overrides.get(
                "logging_requirements",
                ["command", "timestamps", "exit code", "stdout", "stderr"],
            )
        ],
        "telemetry_requirements": [
            str(item)
            for item in overrides.get("telemetry_requirements", [])
        ],
        "artifact_preservation_requirements": [
            str(item)
            for item in overrides.get(
                "artifact_preservation_requirements",
                ["declared outputs", "execution record", "artifact hashes"],
            )
        ],
        "artifact_binding_method": str(
            overrides.get("artifact_binding_method")
            or "Study, Scope, Contract, run, and content SHA-256"
        ),
        "citation_and_source_recording": str(
            overrides.get("citation_and_source_recording")
            or "frozen ResourceUseBinding and source identifiers"
        ),
        "quality_control": [
            str(item) for item in overrides.get("quality_control", [])
        ],
        "approved_resource_substitutions": [
            str(item)
            for item in overrides.get("approved_resource_substitutions", [])
        ],
        "compliance_clearance": str(
            overrides.get("compliance_clearance") or "unknown"
        ),
        "reproduction_steps": [
            str(item) for item in overrides.get("reproduction_steps", [])
        ],
        "resource_ids": [
            item["resource_id"] for item in inventory["resources"]
        ],
        "method_card_ids": [
            item["method_id"] for item in method_result["method_cards"]
        ],
        "evaluator_priority": [
            "frozen protocol and eligibility",
            "deterministic integrity and exact bindings",
            "machine-readable outputs",
            "frozen statistical rules",
            "append-only human adjudication",
            "AI scientific review",
            "NLI risk alert",
        ],
        "formal_treatment_execution_allowed": False,
        "preflight_assertions": {
            "data_loadable": bool(
                overrides.get("data_loadable", False)
            ),
            "data_schema_readable": bool(
                overrides.get("data_schema_readable", False)
            ),
            "inclusion_exclusion_executable": bool(
                overrides.get("inclusion_exclusion_executable", False)
            ),
            "split_boundary_verified": bool(
                overrides.get("split_boundary_verified", False)
            ),
            "leakage_controls_verified": bool(
                overrides.get("leakage_controls_verified", False)
            ),
            "metric_unit_test_passed": bool(
                overrides.get("metric_unit_test_passed", False)
            ),
            "statistical_synthetic_test_passed": bool(
                overrides.get("statistical_synthetic_test_passed", False)
            ),
            "dependency_installable": bool(
                overrides.get("dependency_installable", False)
            ),
            "hardware_sufficient": bool(
                overrides.get("hardware_sufficient", False)
            ),
            "logging_validated": bool(
                overrides.get("logging_validated", False)
            ),
            "seed_fixable": bool(
                overrides.get("seed_fixable", bool(overrides.get("seeds")))
            ),
            "artifact_preservation_validated": bool(
                overrides.get("artifact_preservation_validated", False)
            ),
            "binding_validated": bool(
                overrides.get("binding_validated", False)
            ),
            "failure_detection_validated": bool(
                overrides.get("failure_detection_validated", False)
            ),
            "budget_reasonable": bool(
                overrides.get("budget_reasonable", bool(overrides.get("budget")))
            ),
            "sensitive_data_guard_passed": bool(
                overrides.get("sensitive_data_guard_passed", False)
            ),
            "compliance_cleared": bool(
                overrides.get("compliance_cleared", False)
            ),
        },
        "preflight_assertion_source": {
            "evidence_status": EvidenceStatus.REPORTED.value,
            "declared_by": assertion_source.get("declared_by"),
            "declared_at": assertion_source.get("declared_at"),
            "asserted_fields": list(
                assertion_source.get("asserted_fields") or []
            ),
            "interpretation": (
                "These values are owner declarations, not independent "
                "deterministic verification."
            ),
        },
        "unknown_fields": [
            "primary metric" if metric == "primary_metric" else "",
            (
                "metric direction"
                if str(
                    overrides.get("metric_direction")
                    or "predeclare_before_freeze"
                )
                == "predeclare_before_freeze"
                else ""
            ),
            (
                "denominator"
                if overrides.get(
                    "denominator", data_boundary.get("denominator", "unknown")
                )
                == "unknown"
                else ""
            ),
            (
                "sample size"
                if not (
                    overrides.get("sample_size")
                    or overrides.get("sample_size_or_power")
                )
                else ""
            ),
            (
                "statistical power"
                if not overrides.get("statistical_power")
                else ""
            ),
            "statistical analysis",
            "seeds" if not overrides.get("seeds") else "",
            (
                "falsification condition"
                if not overrides.get("falsification_condition")
                else ""
            ),
            (
                "success threshold"
                if not overrides.get("success_threshold")
                else ""
            ),
            (
                "minimum meaningful effect"
                if not overrides.get("minimum_meaningful_effect")
                else ""
            ),
            (
                "metric implementation"
                if not overrides.get("metric_implementation")
                else ""
            ),
            (
                "randomization method"
                if not overrides.get("randomization_method")
                else ""
            ),
            (
                "confidence interval"
                if not overrides.get("confidence_interval")
                else ""
            ),
            (
                "dependency lock"
                if not overrides.get("dependency_lock")
                else ""
            ),
            (
                "quality control"
                if not overrides.get("quality_control")
                else ""
            ),
            (
                "reproduction steps"
                if not overrides.get("reproduction_steps")
                else ""
            ),
        ],
        "created_at": utc_now(),
    }
    draft["unknown_fields"] = [
        item
        for item in draft["unknown_fields"]
        if item
        and not (
            item == "statistical analysis"
            and not draft["statistical_analysis"].startswith("must be")
        )
    ]
    from .scientific_validity import ScientificValidityContract

    requested_validity = dict(
        overrides.get("scientific_validity_contract") or {}
    )
    requested_validity.setdefault(
        "enforcement_level",
        str(overrides.get("scientific_enforcement_level") or "exploratory"),
    )
    requested_validity.setdefault(
        "requested_claim_tier",
        str(overrides.get("requested_claim_tier") or "controlled_effect"),
    )
    primary_metric_name = str(
        draft["primary_metric"].get("name") or ""
    ).casefold()
    inferred_outcome_structure = (
        "binary_classification"
        if any(
            token in primary_metric_name
            for token in (
                "binary",
                "mcc",
                "matthews",
                "sensitivity",
                "specificity",
            )
        )
        else "other"
    )
    requested_validity.setdefault(
        "outcome_structure",
        str(overrides.get("outcome_structure") or inferred_outcome_structure),
    )
    requested_validity.setdefault(
        "data_provenance_mode",
        str(overrides.get("data_provenance_mode") or "unknown"),
    )
    requested_validity.setdefault(
        "identification_target",
        str(
            overrides.get("identification_target")
            or "bundled_intervention_effect"
        ),
    )
    for field_name in (
        "mechanism_claims",
        "arm_variation_dimensions",
        "invariant_dimensions",
        "matched_control_ids",
        "feature_target_relationships",
        "feature_ablation_ids",
        "data_generator_disclosure",
        "behavioral_decomposition_plan",
        "heterogeneity_plan",
        "robust_inference_plan",
        "threshold_sensitivity_plan",
        "boundary_analysis_plan",
        "seed_role_plan",
        "safeguard_metric_names",
        "reproducibility_release_plan",
        "required_manuscript_disclosures",
    ):
        requested_validity.setdefault(
            field_name,
            list(overrides.get(field_name) or []),
        )
    requested_validity.setdefault(
        "preferred_metric_names",
        dict(overrides.get("preferred_metric_names") or {}),
    )
    requested_validity.setdefault(
        "metric_direction",
        (
            draft["primary_metric"]["direction"]
            if draft["primary_metric"]["direction"] in {"maximize", "minimize"}
            else "maximize"
        ),
    )
    requested_validity.setdefault(
        "minimum_meaningful_effect",
        _optional_float(draft["minimum_meaningful_effect"]),
    )
    requested_validity.setdefault(
        "threshold_basis",
        str(overrides.get("threshold_basis") or "").strip() or None,
    )
    requested_validity.setdefault(
        "expected_information_value",
        str(overrides.get("expected_information_value") or "").strip()
        or (
            "Determine whether the registered treatment clears the frozen "
            "success threshold and is eligible for a scientific verdict."
        ),
    )
    requested_validity.setdefault(
        "sample_adequacy_basis",
        str(overrides.get("sample_adequacy_basis") or "").strip()
        or (
            f"sample_size={draft['sample_size']}; "
            f"statistical_power={draft['statistical_power']}"
        ),
    )
    requested_validity.setdefault(
        "independence_justification",
        str(overrides.get("independence_justification") or "").strip()
        or (
            f"Inference uses variance_unit={draft['variance_unit']}; "
            "seeds and repeated executions are not counted as independent "
            "scientific units."
        ),
    )
    requested_validity.setdefault(
        "baseline_ids",
        [
            str(draft["baseline"]["experiment_id"] or draft["baseline"]["name"])
        ],
    )
    requested_validity.setdefault(
        "uncertainty_plan",
        [
            item
            for item in (
                draft["confidence_interval"],
                draft["statistical_test"],
                (
                    f"per-seed reporting for {len(draft['seeds'])} seeds"
                    if draft["seeds"]
                    else ""
                ),
            )
            if item and item != "unknown"
        ],
    )
    scientific_validity_contract = ScientificValidityContract.model_validate(
        requested_validity
    )
    draft["scientific_validity_contract"] = (
        scientific_validity_contract.model_dump(mode="json")
    )
    resolved_data_boundary = {
        **data_boundary,
        "denominator": str(
            overrides.get("denominator")
            or data_boundary.get("denominator")
            or draft["primary_metric"]["denominator"]
        ),
        "unit_of_analysis": str(
            overrides.get("unit_of_analysis")
            or data_boundary.get("unit_of_analysis")
            or draft["experimental_unit"]
        ),
        "label_origin": str(
            overrides.get("label_origin")
            or data_boundary.get("label_origin")
            or (
                "authoritative target or reference fields in the "
                "owner-approved frozen evaluation dataset"
            )
        ),
        "time_boundary": str(
            overrides.get("time_boundary")
            or data_boundary.get("time_boundary")
            or "content-addressed dataset snapshot frozen before formal execution"
        ),
    }
    from .domain_adapters import (
        finance_backtest_defaults,
        is_finance_backtest_contract,
    )

    if is_finance_backtest_contract(
        {
            "research_question": scope.research_question,
            "candidate_contribution": scope.candidate_contribution,
            "direction": scope.direction,
            "comparison": scope.comparison,
            "data_boundary": resolved_data_boundary,
        }
    ):
        # These are reviewable design defaults, not observed facts.  They
        # become authoritative only after the owner freezes the contract.
        resolved_data_boundary = {
            **finance_backtest_defaults(),
            **resolved_data_boundary,
        }
    for key in ("denominator", "unit_of_analysis", "label_origin", "time_boundary"):
        if resolved_data_boundary[key].strip().casefold() in {
            "",
            "unknown",
            "unverified",
        }:
            fallback = {
                "denominator": draft["primary_metric"]["denominator"],
                "unit_of_analysis": draft["experimental_unit"],
                "label_origin": (
                    "authoritative target or reference fields in the "
                    "owner-approved frozen evaluation dataset"
                ),
                "time_boundary": (
                    "content-addressed dataset snapshot frozen before formal "
                    "execution"
                ),
            }[key]
            resolved_data_boundary[key] = str(fallback)
    effect_threshold = _optional_float(draft["minimum_meaningful_effect"])
    statistical_overrides = dict(overrides.get("statistical_rules") or {})
    effect_threshold = statistical_overrides.get(
        "effect_threshold", effect_threshold
    )
    task_count = max(1, len(overrides.get("tasks", [])))
    split_count = max(1, len(overrides.get("splits", ["default"])))
    seed_count = max(1, len(draft["seeds"]))
    replicate_count = max(1, int(overrides.get("repetitions", 1)))
    planned_arm_runs = (
        task_count * split_count * seed_count * replicate_count * 2
    )
    normalized_budget = dict(draft["budget"])
    normalized_budget["max_runs"] = max(
        planned_arm_runs,
        int(normalized_budget.get("max_runs") or 0),
    )
    requested_evaluator_policy = dict(
        overrides.get("evaluator_policy") or {}
    )
    uses_learned_evaluator = requested_evaluator_policy.get(
        "uses_learned_evaluator"
    )
    if not isinstance(uses_learned_evaluator, bool):
        operational_evaluator_text = json.dumps(
            {
                key: value
                for key, value in requested_evaluator_policy.items()
                if key != "authority_order"
            },
            ensure_ascii=False,
            sort_keys=True,
        ).casefold()
        uses_learned_evaluator = any(
            token in operational_evaluator_text
            for token in (
                "nli",
                "deberta",
                "classifier",
                "semantic",
                "llm",
                "model",
            )
        )
    contract = ResearchContractVersion(
        schema_version=2,
        study_id=context.study_id,
        version=contract_version,
        scope_version=scope.version,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hypothesis-primary-1",
                statement=draft["primary_hypothesis"],
                role=HypothesisRole.PRIMARY,
                decision_rule={"decision_rules_artifact": "decision_rules.json"},
            )
        ],
        data_boundary=resolved_data_boundary,
        metrics=[
            draft["primary_metric"],
            *draft["secondary_metrics"],
        ],
        baseline=draft["baseline"],
        treatment=draft["treatment"],
        tasks=[str(item) for item in overrides.get("tasks", [])],
        seeds=draft["seeds"],
        concurrency=int(overrides.get("concurrency", 1)),
        runtime_binding={
            **draft["environment"],
            "approved_resource_ids": sorted(
                {
                    str(item["resource_id"])
                    for item in selected_candidates
                    if item.get("resource_id")
                }
            ),
            "protocol_resource_binding_ids": sorted(
                set(protocol_resource_binding_ids)
            ),
        },
        evaluator_policy={
            "authority_order": draft["evaluator_priority"],
            "approved_benchmark_ids": [
                item["resource_id"]
                for item in selected_candidates
                if item.get("need_type") == ResourceNeedType.BENCHMARK.value
            ],
            "resource_selection_id": resource_selection.get("selection_id"),
            "uses_learned_evaluator": uses_learned_evaluator,
            **requested_evaluator_policy,
        },
        eligibility_rules=[
            dict(item)
            for item in (
                overrides.get("eligibility_rules")
                or [
                    {
                        "rule_id": "immutable-exclusion-reason",
                        "applies_to": "every excluded run cell or sample",
                        "required_fields": [
                            "eligible",
                            "exclusion_reason_code",
                        ],
                        "rule": (
                            "Every exclusion must be recorded at the decision "
                            "boundary with an immutable reason code."
                        ),
                    }
                ]
            )
        ],
        experiment_profile=overrides.get("experiment_profile"),
        profile_parameters={
            **dict(overrides.get("profile_parameters") or {}),
            **(
                {
                    "measurement_protocol": dict(
                        overrides["measurement_protocol"]
                    )
                }
                if isinstance(overrides.get("measurement_protocol"), dict)
                else {}
            ),
        },
        splits=[
            str(item)
            for item in overrides.get("splits", ["default"])
        ],
        replicates=int(overrides.get("repetitions", 1)),
        output_schema=dict(
            overrides.get("output_schema")
            or {
                "format": "json",
                "metric_field": draft["primary_metric"]["name"],
                "denominator_field": "denominator",
                "sample_id_field": "sample_ids",
                "raw_fields": [
                    "sample_id",
                    "eligible",
                    "exclusion_reason_code",
                    "prediction",
                    "target_reference",
                    "abstention",
                    "runtime",
                    "resource_usage",
                ],
            }
        ),
        statistical_rules={
            "test": draft["statistical_test"],
            "analysis": draft["statistical_analysis"],
            "confidence_interval": draft["confidence_interval"],
            "success_threshold": draft["success_threshold"],
            "minimum_meaningful_effect": draft[
                "minimum_meaningful_effect"
            ],
            "missing_data_policy": draft["missing_data_policy"],
            "multiple_comparison_policy": draft[
                "multiple_comparison_policy"
            ],
            "effect_threshold": effect_threshold,
            "effect_scale": str(
                statistical_overrides.get("effect_scale") or "absolute"
            ),
            "effect_unit": str(
                statistical_overrides.get("effect_unit")
                or f"{draft['primary_metric']['name']} units"
            ),
            "bootstrap_resamples": int(
                statistical_overrides.get("bootstrap_resamples") or 2000
            ),
            "bootstrap_seed": int(
                statistical_overrides.get("bootstrap_seed") or 1729
            ),
            "bootstrap_resampling_unit": str(
                statistical_overrides.get("bootstrap_resampling_unit")
                or draft["variance_unit"]
            ),
            "bootstrap_implementation": str(
                statistical_overrides.get("bootstrap_implementation")
                or "deterministic percentile bootstrap"
            ),
            **statistical_overrides,
        },
        estimand=dict(
            overrides.get("estimand")
            or {
                "population": draft["sample_population"],
                "experimental_unit": draft["experimental_unit"],
                "pairing_key": list(
                    overrides.get("pairing_key")
                    or ["task", "split", "seed", "replicate"]
                ),
                "outcome": draft["primary_metric"]["name"],
                "contrast": "treatment minus baseline",
                "aggregation_hierarchy": list(
                    overrides.get("aggregation_hierarchy")
                    or ["pair", "task", "study"]
                ),
                "weighting_policy": str(
                    overrides.get("weighting_policy")
                    or "equal weight per registered pair"
                ),
                "variance_unit": str(
                    overrides.get("variance_unit")
                    or draft["variance_unit"]
                ),
            }
        ),
        data_requirements=dict(
            overrides.get("data_requirements") or resolved_data_boundary
        ),
        implementation_requirements=dict(
            overrides.get("implementation_requirements")
            or {
                "baseline": draft["baseline"],
                "treatment": draft["treatment"],
                "allowed_arm_delta": list(
                    overrides.get("allowed_arm_delta")
                    or ["registered treatment intervention"]
                ),
            }
        ),
        scientific_validity_contract=draft[
            "scientific_validity_contract"
        ],
        environment_requirements=dict(
            overrides.get("environment_requirements")
            or draft["environment"]
        ),
        resource_policy=dict(
            overrides.get("resource_policy")
            or {
                "mode": draft["resource_strategy"].get("mode"),
                "routes": list(
                    draft["resource_strategy"].get(
                        "resource_routes",
                        draft["resource_strategy"].get(
                            "stage3_resource_tasks", []
                        ),
                    )
                    or []
                ),
                "approved_resource_ids": sorted(
                    {
                        str(item["resource_id"])
                        for item in selected_candidates
                        if item.get("resource_id")
                    }
                ),
            }
        ),
        budget_security=dict(
            overrides.get("budget_security")
            or {
                "budget": normalized_budget,
                "network_policy": "project_policy",
                "formal_execution_network": "disabled",
                "max_download_bytes": int(
                    (draft["budget"] or {}).get(
                        "max_download_bytes", 50_000_000
                    )
                ),
            }
        ),
        created_by="research_forge_stage2",
    )
    context.repository.save_research_contract(contract)
    protocol_markdown = (
        "# Stage 2 Research Protocol Draft\n\n"
        f"## Research question\n\n{draft['research_question']}\n\n"
        f"## Primary hypothesis\n\n{draft['primary_hypothesis']}\n\n"
        f"## Design\n\n- Study design: {draft['study_design']}\n"
        f"- Experimental unit: {draft['experimental_unit']}\n"
        f"- Data version: {draft['data_version']}\n"
        f"- Baseline: {draft['baseline']['name']}\n"
        f"- Experimental group: {draft['experimental_group']}\n\n"
        f"## Primary outcome\n\n- Metric: {draft['primary_metric']['name']}\n"
        f"- Direction: {draft['primary_metric']['direction']}\n"
        f"- Denominator: {draft['primary_metric']['denominator']}\n"
        f"- Success threshold: {draft['success_threshold']}\n"
        f"- Minimum meaningful effect: {draft['minimum_meaningful_effect']}\n\n"
        f"## Analysis and reproducibility\n\n"
        f"- Statistical test: {draft['statistical_test']}\n"
        f"- Confidence interval: {draft['confidence_interval']}\n"
        f"- Sample size: {draft['sample_size']}\n"
        f"- Statistical power: {draft['statistical_power']}\n"
        f"- Seeds: {draft['seeds']}\n"
        f"- Stopping rule: {draft['stopping_rule']}\n"
        f"- Unknown fields: {', '.join(draft['unknown_fields']) or 'none'}\n\n"
        "> Stage 2 does not authorize formal treatment execution or a scientific verdict.\n"
    )
    artifact_id = _write_named_artifact(
        context,
        _versioned_artifact_name(
            "protocol.draft.json", contract_version
        ),
        draft,
        role=ArtifactRole.PROTOCOL,
        status=ArtifactStatus.DRAFT,
    )
    markdown_artifact_id = _write_named_artifact(
        context,
        _versioned_artifact_name(
            "protocol.draft.md", contract_version
        ),
        protocol_markdown,
        role=ArtifactRole.PROTOCOL,
        status=ArtifactStatus.DRAFT,
    )
    return {
        "protocol": draft,
        "research_contract": contract.model_dump(mode="json"),
        "_workflow_output_artifact_ids": [
            artifact_id,
            markdown_artifact_id,
        ],
    }


def _decision_rules(context: "StepContext") -> dict[str, Any]:
    protocol = _ancestor_result(context, "draft_research_protocol")[
        "protocol"
    ]
    contract_version = int(protocol["research_contract_version"])
    rules = DecisionRules(
        supported=[
            "All primary hypotheses meet their frozen directional and statistical criteria.",
            "All decisive evidence uses verified chains and eligible denominators.",
        ],
        refuted=[
            "All primary hypotheses meet their frozen refutation criteria.",
            "Execution and integrity checks remain valid.",
        ],
        mixed=[
            "At least one primary hypothesis is supported and at least one is refuted."
        ],
        inconclusive=[
            "Valid evidence exists but does not reach a frozen supported or refuted threshold."
        ],
        unverifiable=[
            "No valid bound evidence exists for a primary hypothesis.",
            "Execution, schema, hash, leakage, denominator, or eligibility integrity fails.",
        ],
        primary_metric_priority=(
            "The frozen primary metric and estimand decide the primary "
            "hypothesis; secondary metrics remain separate."
        ),
        multi_metric_conflict_rule=(
            "Conflicting prespecified primary endpoints produce mixed; "
            "secondary endpoints cannot overturn the primary endpoint."
        ),
        effect_size_significance_conflict_rule=(
            "A result must satisfy the frozen minimum meaningful effect and "
            "uncertainty rule; statistical significance alone is insufficient."
        ),
        baseline_not_reproduced_rule=(
            "A failed or unverifiable baseline makes the affected comparison "
            "unverifiable and blocks treatment interpretation."
        ),
        partial_run_failure_rule=(
            "Apply the frozen eligibility and missing-run policy; if the "
            "denominator or uncertainty requirement fails, return inconclusive "
            "or unverifiable as specified, never supported."
        ),
        missing_artifact_rule=(
            "A missing decisive artifact or broken binding triggers unverifiable."
        ),
        data_contamination_rule=(
            "Confirmed leakage or contamination invalidates affected evidence "
            "and triggers unverifiable plus repair."
        ),
        protocol_deviation_rule=(
            "Unapproved deviations cannot enter the confirmatory endpoint; "
            "material deviations require an amendment and successor run."
        ),
        authority_order=[
            "frozen_protocol_and_eligibility",
            "deterministic_integrity_numeric_citation_artifact_checks",
            "machine_readable_experiment_output",
            "frozen_statistical_decision_rules",
            "append_only_human_adjudication",
            "ai_scientific_review_panel",
            "nli_risk_alert",
        ],
    )
    artifact_id = _write_named_artifact(
        context,
        _versioned_artifact_name("decision_rules.json", contract_version),
        rules,
        role=ArtifactRole.PROTOCOL,
        status=ArtifactStatus.DRAFT,
    )
    return {
        "decision_rules": rules.model_dump(mode="json"),
        "_workflow_output_artifact_ids": [artifact_id],
    }


_PREFLIGHT_CHECKS = (
    "data_loadable",
    "schema_matches_protocol",
    "sample_size_reasonable",
    "inclusion_exclusion_executable",
    "split_boundary_verified",
    "no_obvious_data_leakage",
    "metric_unit_test_passed",
    "statistical_synthetic_test_passed",
    "code_entry_executable",
    "dependencies_installable",
    "hardware_meets_minimum",
    "logging_complete",
    "random_seed_fixable",
    "artifacts_preservable",
    "result_version_binding_valid",
    "failed_runs_detectable",
    "budget_estimate_reasonable",
    "sensitive_data_guard_passed",
)


def _load_protocol_experiment_manifest(
    protocol: dict[str, Any],
    project_source_root: str | Path,
) -> tuple[Any, Path | None, Path]:
    strategy = dict(protocol.get("resource_strategy") or {})
    workspace_value = strategy.get("workspace_root")
    manifest_value = strategy.get("experiment_manifest_path")
    if (
        strategy.get("mode") == "build_or_acquire_experiment"
        and workspace_value
        and manifest_value
    ):
        workspace = Path(str(workspace_value)).resolve()
        manifest, path = load_project_experiment_manifest(
            workspace, str(manifest_value)
        )
        return manifest, path, workspace
    source_root = Path(project_source_root).resolve()
    manifest, path = load_project_experiment_manifest(source_root)
    return manifest, path, source_root


def _preflight(context: "StepContext") -> dict[str, Any]:
    protocol = _ancestor_result(context, "draft_research_protocol")["protocol"]
    inventory = _ancestor_result(context, "inventory_research_resources")
    boundary = _ancestor_result(context, "define_data_boundary")
    project = context.repository.load_project(
        context.repository.load_study(context.study_id).project_id
    )
    manifest, manifest_path, _ = _load_protocol_experiment_manifest(
        protocol, project.source_root or ""
    )
    baseline_id = protocol["baseline"].get("experiment_id")
    baseline_spec = (
        next(
            (
                item
                for item in manifest.experiments
                if item.experiment_id == baseline_id
            ),
            None,
        )
        if manifest and baseline_id
        else None
    )
    data_resources = [
        item for item in inventory["resources"] if item["category"] == "data"
    ]
    generated_input_hashes = dict(
        protocol.get("resource_strategy", {}).get("input_hashes") or {}
    )
    checks: list[CheckResult] = []
    assertions = protocol["preflight_assertions"]
    selection_path = _stage2_dir(
        context.repository, context.study_id
    ) / "resource_selection.json"
    resource_selection = (
        read_json(selection_path) if selection_path.is_file() else {}
    )
    selected_candidates = [
        dict(item) for item in resource_selection.get("candidates", [])
    ]
    selected_bindings_valid = bool(selected_candidates) and all(
        bool(item.get("content_hash"))
        and item.get("eligibility") != CandidateEligibility.INELIGIBLE.value
        for item in selected_candidates
    )
    assertion_source = protocol.get("preflight_assertion_source") or {}
    declaration_ref = (
        "owner-declaration:"
        f"{assertion_source.get('declared_by') or 'unknown'}:"
        f"{assertion_source.get('declared_at') or 'unknown'}"
    )
    values: dict[str, tuple[str, str, list[str]]] = {
        "data_loadable": (
            "pass"
            if (data_resources or generated_input_hashes)
            and assertions["data_loadable"]
            else "fail",
            "Every protocol data input must load through a bounded adapter.",
            [
                *[item["resource_id"] for item in data_resources],
                *sorted(generated_input_hashes),
            ],
        ),
        "schema_matches_protocol": (
            "pass" if assertions["data_schema_readable"] else "fail",
            "Observed schemas must match the protocol's required fields and types.",
            [],
        ),
        "sample_size_reasonable": (
            "pass"
            if (
                protocol["sample_size"] != "unknown"
                and protocol["statistical_power"] != "unknown"
            )
            else "fail",
            "Sample size and statistical-power rationale must be declared.",
            [],
        ),
        "inclusion_exclusion_executable": (
            "pass"
            if assertions["inclusion_exclusion_executable"]
            else "fail",
            "Inclusion and exclusion rules must be machine executable.",
            [],
        ),
        "split_boundary_verified": (
            "pass"
            if assertions["split_boundary_verified"]
            or all(boundary["data_boundary"]["split_policy"].values())
            else "fail",
            "Train, validation, and test boundaries must be verified.",
            [],
        ),
        "no_obvious_data_leakage": (
            "pass"
            if assertions["leakage_controls_verified"]
            or boundary["leakage_risks"]["blocking_count"] == 0
            else "fail",
            "Blocking leakage risks must be resolved.",
            [],
        ),
        "metric_unit_test_passed": (
            "pass" if assertions["metric_unit_test_passed"] else "fail",
            "Metric implementation must pass a unit test.",
            [],
        ),
        "statistical_synthetic_test_passed": (
            "pass"
            if assertions["statistical_synthetic_test_passed"]
            else "fail",
            "Statistical code must pass a synthetic-data test.",
            [],
        ),
        "code_entry_executable": (
            "pass"
            if baseline_id
            and (
                baseline_spec
                or protocol.get("resource_strategy", {}).get("mode")
                == "reuse_local_verified_chain"
            )
            else "fail",
            (
                "The baseline entry point resolves from a declared experiment "
                "manifest or an imported verified Research Forge run chain."
            ),
            (
                [str(manifest_path)]
                if manifest_path
                else [protocol.get("resource_strategy", {}).get("source_project_root", "")]
            ),
        ),
        "dependencies_installable": (
            "pass" if assertions["dependency_installable"] else "fail",
            "Dependencies must be installable from a declared lock or environment.",
            [],
        ),
        "hardware_meets_minimum": (
            "pass" if assertions["hardware_sufficient"] else "fail",
            "Recorded hardware must meet the declared minimum.",
            [],
        ),
        "logging_complete": (
            "pass" if assertions["logging_validated"] else "fail",
            "The runner must retain command, timestamps, exit code, stdout, and stderr.",
            [],
        ),
        "random_seed_fixable": (
            "pass" if assertions["seed_fixable"] else "fail",
            "Every stochastic component must accept a frozen seed.",
            [str(item) for item in protocol["seeds"]],
        ),
        "artifacts_preservable": (
            "pass"
            if assertions["artifact_preservation_validated"]
            else "fail",
            "Every declared output must be retained and hashed.",
            [],
        ),
        "result_version_binding_valid": (
            "pass"
            if assertions["binding_validated"] and selected_bindings_valid
            else "fail",
            (
                "Results must bind to the owner-selected, content-frozen data, "
                "code, model, environment, and contract versions."
            ),
            (
                ["stage2/resource_selection.json"]
                if selection_path.is_file()
                else []
            ),
        ),
        "failed_runs_detectable": (
            "pass"
            if assertions["failure_detection_validated"]
            else "fail",
            "Non-zero exits, timeouts, schema errors, and missing outputs must be detectable.",
            [],
        ),
        "budget_estimate_reasonable": (
            "pass" if assertions["budget_reasonable"] else "fail",
            "Compute, time, query, and monetary budgets must be bounded.",
            [],
        ),
        "sensitive_data_guard_passed": (
            "pass"
            if assertions["sensitive_data_guard_passed"]
            else "fail",
            "Sensitive data must not leave the approved boundary.",
            [],
        ),
    }
    reported_checks = {
        "data_loadable",
        "schema_matches_protocol",
        "sample_size_reasonable",
        "inclusion_exclusion_executable",
        "metric_unit_test_passed",
        "statistical_synthetic_test_passed",
        "dependencies_installable",
        "hardware_meets_minimum",
        "logging_complete",
        "random_seed_fixable",
        "artifacts_preservable",
        "result_version_binding_valid",
        "failed_runs_detectable",
        "budget_estimate_reasonable",
        "sensitive_data_guard_passed",
    }
    for check_id in _PREFLIGHT_CHECKS:
        status, detail, evidence = values[check_id]
        evidence_status = EvidenceStatus.UNKNOWN
        if status == "pass":
            if check_id == "code_entry_executable":
                evidence_status = EvidenceStatus.VERIFIED
            elif check_id == "split_boundary_verified":
                evidence_status = (
                    EvidenceStatus.REPORTED
                    if assertions["split_boundary_verified"]
                    else EvidenceStatus.INFERRED
                )
            elif check_id == "no_obvious_data_leakage":
                evidence_status = (
                    EvidenceStatus.REPORTED
                    if assertions["leakage_controls_verified"]
                    else EvidenceStatus.INFERRED
                )
            elif check_id in reported_checks:
                evidence_status = EvidenceStatus.REPORTED
        evidence_refs = list(evidence)
        if evidence_status is EvidenceStatus.REPORTED:
            evidence_refs.append(declaration_ref)
        elif (
            evidence_status is EvidenceStatus.INFERRED
            and "stage2/data_boundary.json" not in evidence_refs
        ):
            evidence_refs.append("stage2/data_boundary.json")
        checks.append(
            CheckResult(
                check_id=check_id,
                status=status,
                detail=detail,
                evidence=evidence_refs,
                evidence_status=evidence_status,
            )
        )
    payload = {
        "schema_version": 1,
        "research_contract_version": protocol["research_contract_version"],
        "checks": [item.model_dump(mode="json") for item in checks],
        "pass_count": sum(item.status == "pass" for item in checks),
        "fail_count": sum(item.status == "fail" for item in checks),
        "unknown_count": sum(item.status == "unknown" for item in checks),
        "evidence_status_counts": {
            evidence_status.value: sum(
                item.evidence_status is evidence_status for item in checks
            )
            for evidence_status in EvidenceStatus
        },
        "baseline_experiment_id": baseline_id,
        "formal_treatment_executed": False,
        "generated_at": utc_now(),
    }
    artifact_id = _write_named_artifact(
        context,
        _versioned_artifact_name(
            "preflight_report.json",
            int(protocol["research_contract_version"]),
        ),
        payload,
        role=ArtifactRole.AUDIT,
    )
    return {**payload, "_workflow_output_artifact_ids": [artifact_id]}


def _baseline_validation(context: "StepContext") -> dict[str, Any]:
    study = context.repository.load_study(context.study_id)
    project = context.repository.load_project(study.project_id)
    protocol = _ancestor_result(context, "draft_research_protocol")["protocol"]
    contract_version = int(protocol["research_contract_version"])
    preflight = _ancestor_result(context, "run_stage2_preflight")
    baseline_id = protocol["baseline"].get("experiment_id")
    selection_path = _stage2_dir(
        context.repository, context.study_id
    ) / "resource_selection.json"
    resource_selection = (
        read_json(selection_path) if selection_path.is_file() else {}
    )
    imported_chain = _selected_verified_research_chain(
        Path(project.source_root or "").resolve(),
        [dict(item) for item in resource_selection.get("candidates", [])],
    )
    if (
        imported_chain
        and protocol.get("resource_strategy", {}).get("mode")
        == "reuse_local_verified_chain"
        and imported_chain["baseline"]["run_id"] == baseline_id
    ):
        baseline = imported_chain["baseline"]
        record = imported_chain["record"]
        run_manifest = imported_chain["run_manifest"]
        completed = bool(
            baseline.get("valid")
            and record.get("valid")
            and baseline.get("aggregate_metrics")
            == record.get("aggregate_metrics")
            and record.get("isolation_verified")
            and run_manifest.get("isolation_verified")
            and preflight["fail_count"] == 0
        )
        report = {
            "schema_version": 1,
            "research_contract_version": contract_version,
            "study_id": context.study_id,
            "baseline_experiment_id": baseline_id,
            "baseline_id": baseline_id,
            "implementation_source": imported_chain["manifest_path"]
            .relative_to(Path(project.source_root or "").resolve())
            .as_posix(),
            "implementation_origin": "imported_verified_research_forge_chain",
            "official_or_third_party": "project_declared",
            "code_commit": None,
            "data_version": protocol["data_version"],
            "configuration": {
                "run_id": baseline_id,
                "protocol_scope_version": protocol["scope_version"],
                "resource_strategy": "reuse_local_verified_chain",
            },
            "environment": record.get("runtime_attestation", {}),
            "run_command": None,
            "expected_result": "a valid, isolated, contract-bound baseline record",
            "actual_result": [
                {
                    "aggregate_metrics": record.get("aggregate_metrics"),
                    "metric_stddev": record.get("metric_stddev"),
                    "trials": record.get("trials"),
                }
            ],
            "deviation": [],
            "passed": completed,
            "deviation_interpretation": "none" if completed else "import validation failed",
            "impact_on_formal_experiment": (
                "eligible for owner Gate"
                if completed
                else "blocks Stage 3 treatment interpretation"
            ),
            "manifest_path": str(imported_chain["manifest_path"]),
            "execution_requested": False,
            "execution_status": "imported_verified",
            "baseline_run_succeeded": completed,
            "preregistered_units_accounted_for": completed,
            "output_schema_valid": completed,
            "frozen_denominator_computable": (
                protocol["primary_metric"]["denominator"] != "unknown"
            ),
            "sample_integrity_valid": preflight["fail_count"] == 0,
            "required_input_bindings": [
                {
                    "path": path,
                    "matches_inventory": True,
                }
                for path in imported_chain["selected_data_paths"]
            ],
            "hashes_match_contract": completed,
            "artifact_binding_valid": completed,
            "unresolved_integrity_errors": [],
            "formal_treatment_executed": False,
            "execution": {
                "status": "imported_verified",
                "record_path": str(imported_chain["record_path"]),
                "evidence_path": str(imported_chain["evidence_path"]),
            },
            "verification_evidence_status": EvidenceStatus.VERIFIED.value,
            "validated_at": utc_now(),
            "baseline_verified": completed,
            "feasibility_mvp_verified": completed,
            "validation_scope": "stage2_non_scientific_feasibility",
            "mvp_scientific_evidence_eligible": False,
        }
        artifact_id = _write_named_artifact(
            context,
            _versioned_artifact_name(
                "baseline_validation_report.json", contract_version
            ),
            report,
            role=ArtifactRole.EVALUATION,
        )
        summary_artifact_id = _write_named_artifact(
            context,
            _versioned_artifact_name(
                "baseline_validation_summary.md", contract_version
            ),
            (
                "# Baseline validation summary\n\n"
                f"- Baseline: {baseline_id}\n"
                "- Strategy: reuse local verified Research Forge chain\n"
                f"- Verified: {completed}\n"
                f"- Metrics: {json.dumps(record.get('aggregate_metrics'), ensure_ascii=False)}\n"
            ),
            role=ArtifactRole.EVALUATION,
        )
        return {
            **report,
            "_workflow_output_artifact_ids": [
                artifact_id,
                summary_artifact_id,
            ],
        }
    if (
        protocol.get("resource_strategy", {}).get("mode")
        == "build_or_acquire_experiment"
    ):
        scope = context.repository.latest_scope_contract(context.study_id)
        comparison_frame = (
            _comparison_frame_for_scope(scope)
            if scope is not None
            else {}
        )
        protocol_metric = str(
            protocol.get("primary_metric", {}).get("name") or ""
        ).strip()
        if (
            not _comparison_frame_complete(comparison_frame)
            and protocol_metric.casefold() not in _PRIMARY_OUTCOME_PLACEHOLDERS
        ):
            comparison_frame = {
                **comparison_frame,
                "primary_outcome": protocol_metric,
                "primary_outcome_source": {
                    "kind": "stage2_protocol_design",
                    "scientific_evidence_eligible": False,
                },
            }
        if _comparison_frame_complete(comparison_frame):
            from .generic_mvp import run_generic_feasibility_mvp

            mvp = run_generic_feasibility_mvp(
                comparison_frame=comparison_frame,
                primary_metric=str(protocol["primary_metric"]["name"]),
                metric_direction=str(protocol["primary_metric"]["direction"]),
                denominator=str(protocol["primary_metric"]["denominator"]),
                resource_candidates=[
                    dict(item)
                    for item in resource_selection.get("candidates", [])
                ],
            )
            mvp_verified = mvp["status"] == "verified"
            mvp_artifact_id = _write_named_artifact(
                context,
                _versioned_artifact_name(
                    "stage2_mvp_report.json", contract_version
                ),
                mvp,
                role=ArtifactRole.EVALUATION,
            )
            report = {
                "schema_version": 1,
                "research_contract_version": contract_version,
                "study_id": context.study_id,
                "baseline_experiment_id": baseline_id,
                "baseline_id": baseline_id,
                "implementation_source": mvp["adapter"],
                "implementation_origin": (
                    "research_forge_generated_feasibility_adapter"
                ),
                "official_or_third_party": "non_scientific_feasibility",
                "code_commit": None,
                "data_version": protocol["data_version"],
                "configuration": {
                    "protocol_scope_version": protocol["scope_version"],
                    "primary_metric": protocol["primary_metric"]["name"],
                    "resource_routes": mvp["resource_routes"],
                },
                "environment": mvp["environment"],
                "run_command": None,
                "expected_result": (
                    "Three synthetic schema, arm-instantiation, denominator, "
                    "and metric-computation checks."
                ),
                "actual_result": {
                    "smoke_case_count": len(mvp["smoke_cases"]),
                    "metric_smoke_fixture": mvp[
                        "metric_smoke_fixture"
                    ],
                    "resource_sufficiency": mvp[
                        "resource_sufficiency"
                    ],
                },
                "deviation": list(mvp["errors"]),
                "passed": mvp_verified,
                "deviation_interpretation": (
                    "none"
                    if mvp_verified
                    else "The generated feasibility adapter is incomplete."
                ),
                "impact_on_formal_experiment": (
                    "The design may enter owner review. Stage 3 must acquire "
                    "or construct domain-valid resources, freeze them, bind "
                    "both executable arms, and run the formal matrix."
                    if mvp_verified
                    else "Stage 2 remains BUILD_REQUIRED."
                ),
                "manifest_path": None,
                "execution_requested": True,
                "execution_status": (
                    "non_scientific_mvp_verified"
                    if mvp_verified
                    else "non_scientific_mvp_blocked"
                ),
                "baseline_run_succeeded": False,
                "preregistered_units_accounted_for": False,
                "output_schema_valid": bool(mvp["metric_computable"]),
                "frozen_denominator_computable": bool(
                    mvp["metric_computable"]
                ),
                "sample_integrity_valid": bool(
                    mvp["reset_or_isolation_verified"]
                ),
                "required_input_bindings": [],
                "hashes_match_contract": False,
                "artifact_binding_valid": mvp_verified,
                "unresolved_integrity_errors": list(mvp["errors"]),
                "formal_treatment_executed": False,
                "execution": {
                    "status": mvp["status"],
                    "adapter": mvp["adapter"],
                    "scientific_evidence_eligible": False,
                },
                "verification_evidence_status": (
                    EvidenceStatus.VERIFIED.value
                    if mvp_verified
                    else EvidenceStatus.UNKNOWN.value
                ),
                "validated_at": utc_now(),
                "baseline_verified": False,
                "feasibility_mvp_verified": mvp_verified,
                "validation_scope": "stage2_non_scientific_feasibility",
                "mvp_scientific_evidence_eligible": False,
            }
            artifact_id = _write_named_artifact(
                context,
                _versioned_artifact_name(
                    "baseline_validation_report.json", contract_version
                ),
                report,
                role=ArtifactRole.EVALUATION,
            )
            summary_artifact_id = _write_named_artifact(
                context,
                _versioned_artifact_name(
                    "baseline_validation_summary.md", contract_version
                ),
                (
                    "# Generic Stage 2 feasibility MVP\n\n"
                    f"- Adapter: {mvp['adapter']}\n"
                    f"- Smoke cases: {len(mvp['smoke_cases'])}\n"
                    f"- Feasibility verified: {mvp_verified}\n"
                    "- Scientific evidence eligible: false\n"
                    "- Formal baseline executed: false\n"
                    "- Formal treatment executed: false\n\n"
                    "Local reuse, owner-authorized external acquisition, and "
                    "bounded new construction are all valid resource routes. "
                    "Full domain resources remain a Stage 3 freeze task.\n"
                ),
                role=ArtifactRole.EVALUATION,
            )
            return {
                **report,
                "_workflow_output_artifact_ids": [
                    mvp_artifact_id,
                    artifact_id,
                    summary_artifact_id,
                ],
            }
        from .hdf5_mvp import run_hdf5_feasibility_mvp

        inventory = _ancestor_result(
            context, "inventory_research_resources"
        )
        selected_hdf5_paths = {
            Path(str(item.get("canonical_identifier") or "")).as_posix()
            for item in resource_selection.get("candidates", [])
            if str(item.get("need_type")) == ResourceNeedType.DATASET.value
            and Path(
                str(item.get("canonical_identifier") or "")
            ).suffix.casefold()
            in {".h5", ".hdf5"}
        }
        hdf5_rows = [
            dict(item)
            for item in inventory.get("data_quality", {}).get(
                "hdf5_resources", []
            )
            if Path(str(item.get("path") or "")).as_posix()
            in selected_hdf5_paths
        ]
        if hdf5_rows:
            mvp = run_hdf5_feasibility_mvp(
                project.source_root or "",
                hdf5_rows,
                primary_metric=str(protocol["primary_metric"]["name"]),
            )
            mvp_artifact_id = _write_named_artifact(
                context,
                _versioned_artifact_name(
                    "stage2_mvp_report.json", contract_version
                ),
                mvp,
                role=ArtifactRole.EVALUATION,
            )
            mvp_verified = mvp.get("status") == "verified"
            report = {
                "schema_version": 1,
                "research_contract_version": contract_version,
                "study_id": context.study_id,
                "baseline_experiment_id": baseline_id,
                "baseline_id": baseline_id,
                "implementation_source": (
                    "research_forge_hdf5_feasibility_mvp_v1"
                ),
                "implementation_origin": "research_forge_generated_adapter",
                "official_or_third_party": "non_scientific_feasibility",
                "code_commit": inventory.get("git", {}).get("commit"),
                "data_version": protocol["data_version"],
                "configuration": {
                    "protocol_scope_version": protocol["scope_version"],
                    "selected_hdf5_paths": sorted(selected_hdf5_paths),
                    "primary_metric": protocol["primary_metric"]["name"],
                },
                "environment": mvp.get("environment", {}),
                "run_command": None,
                "expected_result": (
                    "2–3 read-only HDF5 interface checks and a synthetic "
                    "metric-schema aggregation"
                ),
                "actual_result": {
                    "smoke_case_count": len(mvp.get("smoke_cases", [])),
                    "metric_smoke_fixture": mvp.get(
                        "metric_smoke_fixture"
                    ),
                },
                "deviation": list(mvp.get("errors", [])),
                "passed": mvp_verified,
                "deviation_interpretation": (
                    "none"
                    if mvp_verified
                    else "The Stage 2 HDF5 feasibility adapter is incomplete."
                ),
                "impact_on_formal_experiment": (
                    "Stage 2 design may enter owner review; Stage 3 must "
                    "still freeze full HDF5 hashes, bind executable arms, "
                    "and run the formal matrix."
                    if mvp_verified
                    else "Stage 2 remains BUILD_REQUIRED."
                ),
                "manifest_path": None,
                "execution_requested": True,
                "execution_status": (
                    "non_scientific_mvp_verified"
                    if mvp_verified
                    else "non_scientific_mvp_blocked"
                ),
                "baseline_run_succeeded": False,
                "preregistered_units_accounted_for": False,
                "output_schema_valid": bool(mvp.get("metric_computable")),
                "frozen_denominator_computable": bool(
                    mvp.get("metric_computable")
                ),
                "sample_integrity_valid": bool(
                    mvp.get("reset_or_isolation_verified")
                ),
                "required_input_bindings": [
                    {
                        "path": item.get("input_path"),
                        "metadata_sha256": item.get("metadata_sha256"),
                        "binding_level": "metadata_only",
                        "source_unchanged": item.get("source_unchanged"),
                    }
                    for item in mvp.get("smoke_cases", [])
                ],
                "hashes_match_contract": False,
                "artifact_binding_valid": mvp_verified,
                "unresolved_integrity_errors": list(mvp.get("errors", [])),
                "formal_treatment_executed": False,
                "execution": {
                    "status": mvp.get("status"),
                    "adapter": mvp.get("adapter"),
                    "scientific_evidence_eligible": False,
                },
                "verification_evidence_status": (
                    EvidenceStatus.VERIFIED.value
                    if mvp_verified
                    else EvidenceStatus.UNKNOWN.value
                ),
                "validated_at": utc_now(),
                "baseline_verified": False,
                "feasibility_mvp_verified": mvp_verified,
                "validation_scope": "stage2_non_scientific_feasibility",
                "mvp_scientific_evidence_eligible": False,
            }
            artifact_id = _write_named_artifact(
                context,
                _versioned_artifact_name(
                    "baseline_validation_report.json", contract_version
                ),
                report,
                role=ArtifactRole.EVALUATION,
            )
            summary_artifact_id = _write_named_artifact(
                context,
                _versioned_artifact_name(
                    "baseline_validation_summary.md", contract_version
                ),
                (
                    "# Stage 2 HDF5 MVP summary\n\n"
                    f"- Adapter: {mvp.get('adapter')}\n"
                    f"- Smoke cases: {len(mvp.get('smoke_cases', []))}\n"
                    f"- Feasibility verified: {mvp_verified}\n"
                    "- Scientific evidence eligible: false\n"
                    "- Formal baseline executed: false\n\n"
                    "Full data hashing, executable-arm binding, and formal "
                    "rollouts remain Stage 3 responsibilities.\n"
                ),
                role=ArtifactRole.EVALUATION,
            )
            return {
                **report,
                "_workflow_output_artifact_ids": [
                    mvp_artifact_id,
                    artifact_id,
                    summary_artifact_id,
                ],
            }
    manifest, manifest_path, experiment_source_root = (
        _load_protocol_experiment_manifest(
            protocol, project.source_root or ""
        )
    )
    spec = (
        next(
            (
                item
                for item in manifest.experiments
                if item.experiment_id == baseline_id
            ),
            None,
        )
        if manifest and baseline_id
        else None
    )
    execution: dict[str, Any] | None = None
    errors: list[str] = []
    explicit_run = bool(study.settings.get("stage2_run_baseline", False))
    current_contract = context.repository.latest_research_contract(
        context.study_id
    )
    baseline_run_variables = {
        "task_id": (
            current_contract.tasks[0]
            if current_contract is not None and current_contract.tasks
            else "stage2-feasibility"
        ),
        "split_id": (
            current_contract.splits[0]
            if current_contract is not None and current_contract.splits
            else "smoke"
        ),
        "arm_id": "baseline",
        "seed": str(
            current_contract.seeds[0]
            if current_contract is not None and current_contract.seeds
            else 0
        ),
        "replicate": "1",
        "run_cell_id": f"{context.study_id}:stage2-baseline",
    }
    if spec and explicit_run:
        action_id = str(
            study.settings.get("stage2_baseline_action_id")
            or spec.action_ids[0]
        )
        if experiment_for_action(manifest, action_id) != spec:
            errors.append("baseline action does not uniquely bind to the selected spec")
        else:
            evidence_dir = (
                _stage2_dir(context.repository, context.study_id)
                / "baseline"
                / f"contract-v{contract_version}"
                / spec.experiment_id
            )
            try:
                sandbox_parent = (
                    _stage2_dir(context.repository, context.study_id)
                    / "baseline_sandboxes"
                )
                sandbox_parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(
                    prefix=f"{spec.experiment_id}-",
                    dir=sandbox_parent,
                ) as temporary:
                    sandbox_root = Path(temporary) / "project"
                    shutil.copytree(
                        experiment_source_root,
                        sandbox_root,
                        symlinks=True,
                        ignore=_stage_two_sandbox_ignore,
                    )
                    execution = run_declared_experiment(
                        spec,
                        source_root=sandbox_root,
                        evidence_dir=evidence_dir,
                        action_id=action_id,
                        plan_id=f"{context.study_id}:stage2-baseline",
                        network_authorized=project.network_policy.network_enabled,
                        report_progress=lambda **_: None,
                        run_variables=baseline_run_variables,
                        use_smoke=(
                            spec.execution_backend
                            == "isolated_candidate_evaluator"
                        ),
                    )
                    execution["source_snapshot_isolated"] = True
                    execution["source_snapshot_disposition"] = (
                        "temporary copy removed after execution"
                    )
            except Exception as exc:  # execution failure is evidence, not refutation
                errors.append(f"{type(exc).__name__}: {exc}")
    elif spec and not explicit_run:
        try:
            prepared = preflight_experiment(
                spec,
                source_root=project.source_root or "",
                evidence_dir=(
                    _stage2_dir(context.repository, context.study_id)
                    / "baseline"
                    / f"contract-v{contract_version}"
                    / spec.experiment_id
                ),
                action_id=spec.action_ids[0],
                plan_id=f"{context.study_id}:stage2-baseline",
                network_authorized=project.network_policy.network_enabled,
                run_variables=baseline_run_variables,
                use_smoke=(
                    spec.execution_backend
                    == "isolated_candidate_evaluator"
                ),
            )
            execution = {
                "status": "not_run",
                "preflight_passed": True,
                "command": prepared["command"],
                "cwd": str(prepared["cwd"]),
            }
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    else:
        errors.append("no declared baseline experiment is bound to the protocol")
    completed = bool(execution and execution.get("status") == "completed")
    inventory = _ancestor_result(
        context, "inventory_research_resources"
    )
    inventory_hashes = {
        Path(str(item["location"])).as_posix(): item.get("content_hash")
        for item in inventory.get("resources", [])
        if item.get("location")
    }
    inventory_hashes.update(
        {
            Path(str(path)).as_posix(): str(content_hash)
            for path, content_hash in dict(
                protocol.get("resource_strategy", {}).get("input_hashes") or {}
            ).items()
        }
    )
    required_input_bindings: list[dict[str, Any]] = []
    if completed and execution:
        for item in execution.get("required_inputs_before", []):
            absolute = Path(str(item.get("path", ""))).resolve()
            relative = str(item.get("relative_path") or "")
            expected_hash = inventory_hashes.get(relative)
            observed_hash = item.get("sha256")
            required_input_bindings.append(
                {
                    "path": relative or str(absolute),
                    "expected_sha256": expected_hash,
                    "observed_sha256": observed_hash,
                    "matches_inventory": bool(
                        expected_hash
                        and observed_hash
                        and expected_hash == observed_hash
                    ),
                }
            )
    contract_input_hashes_match = bool(required_input_bindings) and all(
        item["matches_inventory"] for item in required_input_bindings
    )
    implementation_source = (
        str(manifest_path) if manifest_path else "unavailable"
    )
    actual_results = (
        list(execution.get("artifacts", []))
        if completed and execution
        else []
    )
    report = {
        "schema_version": 1,
        "research_contract_version": contract_version,
        "study_id": context.study_id,
        "baseline_experiment_id": baseline_id,
        "baseline_id": baseline_id,
        "implementation_source": implementation_source,
        "implementation_origin": "project_declared",
        "official_or_third_party": "project_declared",
        "code_commit": inventory.get("git", {}).get("commit"),
        "data_version": protocol["data_version"],
        "configuration": {
            "experiment_id": baseline_id,
            "action_id": study.settings.get("stage2_baseline_action_id"),
            "protocol_scope_version": protocol["scope_version"],
        },
        "environment": protocol["environment"],
        "run_command": (
            execution.get("command")
            if execution
            else None
        ),
        "expected_result": protocol["baseline"].get(
            "expected_result", "successful declared output validation"
        ),
        "actual_result": actual_results,
        "deviation": (
            []
            if completed
            else ["baseline did not produce a fully verified declared result"]
        ),
        "passed": completed,
        "deviation_interpretation": (
            "none"
            if completed
            else "The formal comparison remains unverifiable."
        ),
        "impact_on_formal_experiment": (
            "eligible for owner Gate"
            if completed
            else "blocks Stage 3 treatment interpretation"
        ),
        "manifest_path": str(manifest_path) if manifest_path else None,
        "execution_requested": explicit_run,
        "execution_status": (
            execution.get("status") if execution else "unavailable"
        ),
        "baseline_run_succeeded": completed,
        "preregistered_units_accounted_for": completed,
        "output_schema_valid": completed,
        "frozen_denominator_computable": (
            protocol["primary_metric"]["denominator"] != "unknown"
        ),
        "sample_integrity_valid": (
            preflight["fail_count"] == 0 and preflight["unknown_count"] == 0
        ),
        "required_input_bindings": required_input_bindings,
        "hashes_match_contract": bool(
            completed
            and execution
            and execution.get("input_binding_valid")
            and contract_input_hashes_match
        ),
        "artifact_binding_valid": bool(
            completed
            and execution
            and execution.get("artifacts")
            and all(
                item.get("sha256")
                for item in execution.get("artifacts", [])
            )
        ),
        "unresolved_integrity_errors": errors,
        "formal_treatment_executed": False,
        "execution": execution,
        "verification_evidence_status": (
            EvidenceStatus.REPORTED.value
            if preflight["evidence_status_counts"].get(
                EvidenceStatus.REPORTED.value, 0
            )
            or preflight["evidence_status_counts"].get(
                EvidenceStatus.INFERRED.value, 0
            )
            else EvidenceStatus.VERIFIED.value
        ),
        "validated_at": utc_now(),
    }
    report["baseline_verified"] = all(
        (
            report["baseline_run_succeeded"],
            report["preregistered_units_accounted_for"],
            report["output_schema_valid"],
            report["frozen_denominator_computable"],
            report["sample_integrity_valid"],
            report["hashes_match_contract"],
            report["artifact_binding_valid"],
            not report["unresolved_integrity_errors"],
        )
    )
    report["feasibility_mvp_verified"] = report["baseline_verified"]
    report["validation_scope"] = "stage2_non_scientific_feasibility"
    report["mvp_scientific_evidence_eligible"] = False
    baseline_artifact_ids: list[str] = []
    if execution:
        candidate_paths = [
            execution.get("stdout_log"),
            execution.get("stderr_log"),
            *[
                item.get("path")
                for item in execution.get("artifacts", [])
                if isinstance(item, dict)
            ],
        ]
        for candidate in candidate_paths:
            if not candidate:
                continue
            path = Path(str(candidate)).resolve()
            if not path.is_file():
                continue
            baseline_artifact_ids.append(
                context.repository.register_artifact(
                    context.study_id,
                    str(path),
                    sha256_file(path),
                    kind="stage2_baseline_output",
                    role=ArtifactRole.OUTPUT,
                ).artifact_id
            )
    artifact_id = _write_named_artifact(
        context,
        _versioned_artifact_name(
            "baseline_validation_report.json", contract_version
        ),
        report,
        role=ArtifactRole.EVALUATION,
    )
    summary = (
        "# Baseline validation summary\n\n"
        f"- Baseline: {baseline_id or 'not bound'}\n"
        f"- Execution status: {report['execution_status']}\n"
        f"- Verified: {report['baseline_verified']}\n"
        f"- Implementation source: {implementation_source}\n"
        f"- Impact: {report['impact_on_formal_experiment']}\n\n"
        "A failed baseline is an integrity outcome, not a refuted hypothesis.\n"
    )
    summary_artifact_id = _write_named_artifact(
        context,
        _versioned_artifact_name(
            "baseline_validation_summary.md", contract_version
        ),
        summary,
        role=ArtifactRole.EVALUATION,
    )
    return {
        **report,
        "_workflow_output_artifact_ids": [
            artifact_id,
            summary_artifact_id,
            *baseline_artifact_ids,
        ],
    }


def _lint_research_contract(context: "StepContext") -> dict[str, Any]:
    from .contract_compiler import compile_research_contract

    contract = context.repository.latest_research_contract(context.study_id)
    if contract is None:
        raise _blocked("Research Contract draft is unavailable.")
    report = compile_research_contract(contract)
    artifact_id = _write_named_artifact(
        context,
        _versioned_artifact_name(
            "contract_lint_report.json", contract.version
        ),
        {
            "schema_version": 1,
            "study_id": context.study_id,
            "contract_version": contract.version,
            "passed": report.lint_passed,
            "blocking_issues": report.blocking_issues,
            "generated_at": report.generated_at,
        },
        role=ArtifactRole.AUDIT,
    )
    return {
        "lint_passed": report.lint_passed,
        "blocking_issues": report.blocking_issues,
        "_workflow_output_artifact_ids": [artifact_id],
        "_workflow_acceptance": {
            "scientific_postcondition_passed": True,
            "checks": {"lint_report_produced": True},
        },
    }


def _compile_research_contract(context: "StepContext") -> dict[str, Any]:
    from .contract_compiler import (
        blocking_issue_report,
        compile_research_contract,
    )

    contract = context.repository.latest_research_contract(context.study_id)
    if contract is None:
        raise _blocked("Research Contract draft is unavailable.")
    compiling = context.repository.save_research_contract(
        contract.model_copy(update={"protocol_status": ProtocolStatus.COMPILING})
    )
    report = compile_research_contract(compiling)
    report_artifact_id = _write_named_artifact(
        context,
        _versioned_artifact_name(
            "contract_compile_report.json", contract.version
        ),
        report,
        role=ArtifactRole.AUDIT,
    )
    if not report.compile_passed:
        issue_report = blocking_issue_report(
            contract,
            report.blocking_issues,
            source_phase="protocol",
        )
        _write_named_artifact(
            context,
            _versioned_artifact_name(
                "blocking_issue_report.json", contract.version
            ),
            issue_report,
            role=ArtifactRole.AUDIT,
        )
        context.repository.save_research_contract(
            compiling.model_copy(
                update={
                    "protocol_status": ProtocolStatus.BLOCKED,
                    "compile_report_id": report.compile_report_id,
                    "unresolved_placeholders": report.blocking_issues,
                }
            )
        )
        raise _blocked(
            "Research Contract cannot compile: "
            + "; ".join(report.blocking_issues),
            kind="contract_revision_required",
        )
    compiled_artifact_ids = [report_artifact_id]
    for filename, payload, role in (
        (
            "dataset_specification.json",
            report.dataset_specification,
            ArtifactRole.PROTOCOL,
        ),
        (
            "algorithm_specifications.json",
            {
                "algorithms": [
                    item.model_dump(mode="json")
                    for item in report.algorithm_specifications
                ]
            },
            ArtifactRole.PROTOCOL,
        ),
        (
            "run_specifications.json",
            {
                "runs": [
                    item.model_dump(mode="json")
                    for item in report.run_specifications
                ]
            },
            ArtifactRole.PROTOCOL,
        ),
        (
            "evaluation_specification.json",
            report.evaluation_specification,
            ArtifactRole.PROTOCOL,
        ),
        (
            "analysis_specification.json",
            report.analysis_specification,
            ArtifactRole.PROTOCOL,
        ),
        (
            "executable_run_dag.json",
            report.executable_run_dag,
            ArtifactRole.PROTOCOL,
        ),
        (
            "scientific_contribution_report.json",
            report.scientific_contribution_report,
            ArtifactRole.AUDIT,
        ),
    ):
        if payload is None:
            continue
        compiled_artifact_ids.append(
            _write_named_artifact(
                context,
                _versioned_artifact_name(filename, contract.version),
                payload,
                role=role,
            )
        )
    updated_contract = context.repository.save_research_contract(
        compiling.model_copy(
            update={
                "protocol_status": ProtocolStatus.EXECUTABLE,
                "compile_report_id": report.compile_report_id,
                "unresolved_placeholders": [],
                "run_specification_ids": [
                    item.run_specification_id
                    for item in report.run_specifications
                ],
                "dataset_specification_id": (
                    report.dataset_specification.dataset_specification_id
                    if report.dataset_specification is not None
                    else None
                ),
                "algorithm_specification_ids": [
                    item.algorithm_specification_id
                    for item in report.algorithm_specifications
                ],
                "evaluation_specification_id": (
                    report.evaluation_specification.evaluation_specification_id
                    if report.evaluation_specification is not None
                    else None
                ),
                "analysis_specification_id": (
                    report.analysis_specification.analysis_specification_id
                    if report.analysis_specification is not None
                    else None
                ),
                "executable_run_dag_id": (
                    report.executable_run_dag.run_dag_id
                    if report.executable_run_dag is not None
                    else None
                ),
            }
        )
    )
    return {
        "compile_report": report.model_dump(mode="json"),
        "research_contract": updated_contract.model_dump(mode="json"),
        "_workflow_output_artifact_ids": compiled_artifact_ids,
        "_workflow_acceptance": {
            "scientific_postcondition_passed": True,
            "checks": {
                "lint_passed": report.lint_passed,
                "compile_passed": report.compile_passed,
                "two_algorithms_resolved": (
                    report.executable_algorithm_count
                    == report.required_algorithm_count
                ),
                "dataset_specification_resolved": (
                    report.dataset_specification is not None
                ),
                "run_dag_resolved": report.executable_run_dag is not None,
            },
        },
    }


def _dry_run_research_contract(context: "StepContext") -> dict[str, Any]:
    from .contract_compiler import (
        ContractCompileReport,
        dry_run_compiled_contract,
    )

    compiled = _ancestor_result(context, "compile_research_contract")[
        "compile_report"
    ]
    report = dry_run_compiled_contract(
        ContractCompileReport.model_validate(compiled)
    )
    artifact_id = _write_named_artifact(
        context,
        _versioned_artifact_name(
            "contract_dry_run_report.json", report.contract_version
        ),
        report,
        role=ArtifactRole.FEASIBILITY,
    )
    if not report.passed:
        raise _blocked(
            "Research Contract dry run failed: "
            + "; ".join(report.blocking_issues),
            kind="contract_revision_required",
        )
    contract = context.repository.latest_research_contract(context.study_id)
    if contract is None:
        raise _blocked("Research Contract draft is unavailable.")
    updated = context.repository.save_research_contract(
        contract.model_copy(
            update={"dry_run_report_id": report.dry_run_report_id}
        )
    )
    return {
        "dry_run_report": report.model_dump(mode="json"),
        "research_contract": updated.model_dump(mode="json"),
        "_workflow_output_artifact_ids": [artifact_id],
        "_workflow_acceptance": {
            "scientific_postcondition_passed": report.passed,
            "checks": report.checks,
        },
    }


def _execution_readiness_gate(context: "StepContext") -> dict[str, Any]:
    compiled = _ancestor_result(context, "compile_research_contract")[
        "compile_report"
    ]
    dry_run = _ancestor_result(context, "dry_run_research_contract")[
        "dry_run_report"
    ]
    readiness = {
        "schema_version": 1,
        "study_id": context.study_id,
        "contract_version": int(compiled["contract_version"]),
        "status": "ready" if dry_run["passed"] else "blocked",
        "executable_algorithms": (
            f"{compiled['executable_algorithm_count']}/"
            f"{compiled['required_algorithm_count']}"
        ),
        "run_specification_count": len(compiled["run_specifications"]),
        "data_semantics_resolved": not any(
            "DATA_BOUNDARY" in item or "TARGET_RULE" in item
            for item in compiled["blocking_issues"]
        ),
        "metric_resolved": compiled["evaluation_specification"] is not None,
        "analysis_resolved": compiled["analysis_specification"] is not None,
        "dataset_specification_resolved": (
            compiled["dataset_specification"] is not None
        ),
        "algorithm_specifications_resolved": (
            len(compiled["algorithm_specifications"])
            == compiled["required_algorithm_count"]
        ),
        "run_dag_resolved": compiled["executable_run_dag"] is not None,
        "scientific_contribution_resolved": all(
            compiled["scientific_contribution_checks"].values()
        ),
        "dry_run_passed": bool(dry_run["passed"]),
        "formal_evidence_created": False,
        "blocker_count": len(compiled["blocking_issues"])
        + len(dry_run["blocking_issues"]),
    }
    artifact_id = _write_named_artifact(
        context,
        _versioned_artifact_name(
            "execution_readiness_gate.json",
            readiness["contract_version"],
        ),
        readiness,
        role=ArtifactRole.AUDIT,
    )
    if readiness["status"] != "ready":
        raise _blocked(
            "Execution Readiness Gate is blocked.",
            kind="contract_revision_required",
        )
    return {
        "execution_readiness": readiness,
        "_workflow_output_artifact_ids": [artifact_id],
        "_workflow_acceptance": {
            "scientific_postcondition_passed": True,
            "checks": {
                "contract_compiled": True,
                "scientific_contribution_resolved": readiness[
                    "scientific_contribution_resolved"
                ],
                "dry_run_passed": True,
                "no_formal_evidence_created": True,
            },
        },
    }


def _gate_assessment(context: "StepContext") -> dict[str, Any]:
    preflight = _ancestor_result(context, "run_stage2_preflight")
    baseline = _ancestor_result(context, "validate_stage2_baseline")
    protocol = _ancestor_result(context, "draft_research_protocol")["protocol"]
    scope = context.repository.latest_scope_contract(context.study_id)
    gaps = _ancestor_result(context, "diagnose_resource_gaps")[
        "resource_gaps"
    ]
    decision_rules = _ancestor_result(context, "define_decision_rules")[
        "decision_rules"
    ]
    strategy_mode = protocol.get("resource_strategy", {}).get("mode")
    mvp_contract = dict(
        protocol.get("resource_strategy", {}).get("stage2_mvp") or {}
    )
    mvp_verified = bool(
        baseline.get("feasibility_mvp_verified")
        or mvp_contract.get("verified")
    )
    blockers: list[str] = []
    warnings: list[str] = []
    if preflight["fail_count"] and strategy_mode != "build_or_acquire_experiment":
        blockers.append(f"{preflight['fail_count']} preflight checks failed")
    if preflight["unknown_count"]:
        warnings.append(f"{preflight['unknown_count']} preflight checks are unknown")
    non_verified_preflight = [
        item
        for item in preflight["checks"]
        if item["status"] == "pass"
        and item.get("evidence_status") != EvidenceStatus.VERIFIED.value
    ]
    if non_verified_preflight:
        warnings.append(
            f"{len(non_verified_preflight)} passing preflight checks rely on "
            "reported or inferred evidence and require owner acceptance"
        )
    if (
        not baseline["baseline_verified"]
        and strategy_mode != "build_or_acquire_experiment"
    ):
        blockers.append("baseline is not verified")
    if protocol["unknown_fields"]:
        blockers.append(
            "protocol fields remain unknown: "
            + ", ".join(protocol["unknown_fields"])
        )
    preflight_results_by_id = {
        item["check_id"]: item for item in preflight["checks"]
    }
    preflight_by_id = {
        check_id: item["status"]
        for check_id, item in preflight_results_by_id.items()
    }
    resource_substitutions = protocol["approved_resource_substitutions"]
    variance_unit = " ".join(
        str(protocol.get("variance_unit") or "").strip().lower().split()
    )
    profile = str(protocol.get("experiment_profile") or "").strip()
    if profile == Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1.value:
        registered_data_unit = " ".join(
            str(
                protocol.get("data_boundary", {}).get("unit_of_analysis")
                or ""
            )
            .strip()
            .lower()
            .split()
        )
        supported_variance_unit = variance_unit in {
            "task",
            "tasks",
            "registered pair",
            "pair",
            "task split seed replicate pair",
        } or bool(
            variance_unit
            and registered_data_unit
            and variance_unit == registered_data_unit
            and str(
                protocol.get("data_boundary", {}).get("sampling_frame")
                or ""
            ).strip()
        )
        variance_unit_detail = (
            "Profile v1 requires a task, registered pair, or the explicit "
            "frozen data-boundary unit as the independent statistical "
            "variance unit."
        )
    else:
        supported_variance_unit = bool(variance_unit) and variance_unit not in {
            "unknown",
            "predeclared computational task, sample, or run",
        }
        variance_unit_detail = (
            "The independent statistical variance unit is explicit and "
            "compatible with the selected experiment profile."
        )
    freeze_values: list[tuple[str, bool, str]] = [
        (
            "specific_research_question_frozen",
            bool(
                scope
                and scope.status is ArtifactStatus.FROZEN
                and scope.contract_level == "specific_topic"
            ),
            "A specific-topic Scope vNext is frozen.",
        ),
        (
            "hypothesis_falsifiable",
            protocol["falsification_condition"] != "unknown",
            "The primary hypothesis has an explicit falsification condition.",
        ),
        (
            "experimental_unit_explicit",
            bool(protocol["experimental_unit"]),
            "The experimental unit is explicit.",
        ),
        (
            "variance_unit_supported",
            supported_variance_unit,
            variance_unit_detail,
        ),
        (
            "primary_metric_explicit",
            protocol["primary_metric"]["name"] != "primary_metric",
            "The primary metric is named.",
        ),
        (
            "minimum_meaningful_effect_explicit",
            protocol["minimum_meaningful_effect"] != "unknown",
            "The minimum meaningful effect is frozen.",
        ),
        (
            "success_threshold_explicit",
            protocol["success_threshold"] != "unknown",
            "The success threshold is frozen.",
        ),
        (
            "data_boundary_explicit",
            protocol["primary_metric"]["denominator"] != "unknown"
            and bool(protocol["data_boundary"]),
            "The data and denominator boundaries are explicit.",
        ),
        (
            "blocking_resources_resolved",
            gaps["blocking_count"] == 0 or bool(resource_substitutions),
            "Blocking resources are available or have owner-approved substitutes.",
        ),
        (
            "primary_baseline_verified",
            bool(baseline["baseline_verified"]),
            "The primary baseline passed the baseline verification contract.",
        ),
        (
            "metric_implementation_verified",
            preflight_by_id.get("metric_unit_test_passed") == "pass",
            "Metric implementation passed a unit test.",
        ),
        (
            "leakage_check_passed",
            preflight_by_id.get("no_obvious_data_leakage") == "pass",
            "Blocking data leakage was excluded.",
        ),
        (
            "environment_recorded",
            bool(protocol["software_versions"])
            and bool(protocol["hardware_environment"]),
            "Software and hardware environments are recorded.",
        ),
        (
            "logging_and_artifacts_verified",
            preflight_by_id.get("logging_complete") == "pass"
            and preflight_by_id.get("artifacts_preservable") == "pass",
            "Logging and artifact preservation were verified.",
        ),
        (
            "statistical_method_determined",
            protocol["statistical_test"] != "unknown"
            and protocol["confidence_interval"] != "unknown",
            "The statistical test and uncertainty method are frozen.",
        ),
        (
            "stopping_rule_determined",
            bool(protocol["stopping_rule"]),
            "The stopping rule is frozen before treatment.",
        ),
        (
            "five_verdict_rules_frozen",
            all(
                decision_rules.get(name)
                for name in (
                    "supported",
                    "refuted",
                    "mixed",
                    "inconclusive",
                    "unverifiable",
                )
            ),
            "All five verdict semantics are defined.",
        ),
        (
            "license_privacy_ethics_compliance_cleared",
            bool(protocol["preflight_assertions"]["compliance_cleared"])
            and preflight_by_id.get("sensitive_data_guard_passed") == "pass",
            "No unresolved critical license, privacy, ethics, or compliance issue remains.",
        ),
    ]
    reported_freeze_conditions = {
        "hypothesis_falsifiable",
        "experimental_unit_explicit",
        "variance_unit_supported",
        "primary_metric_explicit",
        "minimum_meaningful_effect_explicit",
        "success_threshold_explicit",
        "data_boundary_explicit",
        "blocking_resources_resolved",
        "statistical_method_determined",
        "stopping_rule_determined",
        "license_privacy_ethics_compliance_cleared",
    }
    preflight_freeze_sources = {
        "metric_implementation_verified": "metric_unit_test_passed",
        "leakage_check_passed": "no_obvious_data_leakage",
        "logging_and_artifacts_verified": "logging_complete",
    }
    freeze_conditions = [
        CheckResult(
            check_id=check_id,
            status="pass" if passed else "fail",
            detail=detail,
            evidence_status=(
                EvidenceStatus.UNKNOWN
                if not passed
                else EvidenceStatus.REPORTED
                if check_id in reported_freeze_conditions
                else EvidenceStatus(
                    preflight_results_by_id[
                        preflight_freeze_sources[check_id]
                    ].get("evidence_status", EvidenceStatus.UNKNOWN.value)
                )
                if check_id in preflight_freeze_sources
                else EvidenceStatus.VERIFIED
            ),
        )
        for check_id, passed, detail in freeze_values
    ]
    failed_freeze = [
        item.check_id for item in freeze_conditions if item.status != "pass"
    ]
    if failed_freeze:
        blockers.append(
            "freeze conditions failed: " + ", ".join(failed_freeze)
        )
    if strategy_mode == "build_or_acquire_experiment":
        gate_contract = context.repository.latest_research_contract(
            context.study_id
        )
        # Stage 2 owns the complete scientific specification.  Do not allow a
        # superficially populated contract (for example placeholder task names
        # or unnamed arm behaviour) to reach the owner freeze Gate and rely on
        # Stage 3 to discover the missing design decisions.
        from .stage_three_build import stage3_contract_readiness_violations

        contract_readiness_violations = (
            stage3_contract_readiness_violations(gate_contract)
            if gate_contract is not None
            else ["RESEARCH_CONTRACT_MISSING: create a Research Contract draft"]
        )
        effect_threshold = (
            gate_contract.statistical_rules.get("effect_threshold")
            if gate_contract is not None
            else None
        )
        design_checks = [
            (
                "specific_research_question_frozen",
                bool(
                    scope
                    and scope.status is ArtifactStatus.FROZEN
                    and scope.contract_level == "specific_topic"
                ),
                "A specific research question is frozen.",
            ),
            (
                "hypothesis_and_success_rule_frozen",
                protocol["falsification_condition"] != "unknown"
                and protocol["success_threshold"] != "unknown"
                and protocol["minimum_meaningful_effect"] != "unknown",
                "The hypothesis, falsification rule, and effect threshold are frozen.",
            ),
            (
                "conceptual_arms_defined",
                bool(protocol["baseline"].get("name"))
                and bool(protocol["treatment"].get("name")),
                "Conceptual baseline and treatment arms are defined.",
            ),
            (
                "experiment_profile_compiled",
                bool(
                    gate_contract is not None
                    and gate_contract.experiment_profile is not None
                ),
                (
                    "Stage 2 compiled the conceptual design into a certified "
                    "Stage 3 experiment Profile."
                ),
            ),
            (
                "formal_tasks_declared",
                bool(gate_contract is not None and gate_contract.tasks),
                "At least one formal evaluation task is declared.",
            ),
            (
                "arm_bindings_reserved",
                all(
                    str(value or "").strip()
                    for value in (
                        (
                            gate_contract.baseline.get("experiment_id")
                            if gate_contract is not None
                            else None
                        ),
                        (
                            gate_contract.baseline.get("action_id")
                            if gate_contract is not None
                            else None
                        ),
                        (
                            gate_contract.treatment.get("experiment_id")
                            if gate_contract is not None
                            else None
                        ),
                        (
                            gate_contract.treatment.get("action_id")
                            if gate_contract is not None
                            else None
                        ),
                    )
                ),
                (
                    "Stable baseline and treatment identifiers are reserved; "
                    "Stage 3 will bind their concrete commands."
                ),
            ),
            (
                "numeric_effect_threshold_frozen",
                isinstance(effect_threshold, (int, float))
                and not isinstance(effect_threshold, bool),
                (
                    "A numeric minimum effect is frozen before formal "
                    "execution."
                ),
            ),
            (
                "primary_metric_and_denominator_defined",
                protocol["primary_metric"]["name"] != "primary_metric"
                and protocol["primary_metric"]["denominator"] != "unknown",
                "The primary metric, direction, and denominator are defined.",
            ),
            (
                "stage2_mvp_verified",
                mvp_verified,
                (
                    "A non-scientific MVP verified the minimum environment, "
                    "2–3 smoke cases, metric pipeline, and baseline feasibility."
                ),
            ),
            (
                "stage3_resource_plan_recorded",
                bool(
                    protocol.get("resource_strategy", {}).get(
                        "stage3_resource_tasks"
                    )
                ),
                "Full data, environment, arm binding, and execution work is assigned to Stage 3.",
            ),
            (
                "five_verdict_rules_defined",
                all(
                    decision_rules.get(name)
                    for name in (
                        "supported",
                        "refuted",
                        "mixed",
                        "inconclusive",
                        "unverifiable",
                    )
                ),
                "Scientific verdict semantics are defined before formal execution.",
            ),
            (
                "scientific_specification_complete",
                not contract_readiness_violations,
                (
                    "The complete task semantics, arm behaviours, data boundary, "
                    "measurement protocol, decision thresholds, uncertainty "
                    "procedure, and run budget are frozen in Stage 2."
                ),
            ),
        ]
        freeze_conditions = [
            CheckResult(
                check_id=check_id,
                status="pass" if passed else "fail",
                detail=detail,
                evidence_status=(
                    EvidenceStatus.VERIFIED
                    if passed and check_id == "stage2_mvp_verified"
                    else EvidenceStatus.REPORTED
                    if passed
                    else EvidenceStatus.UNKNOWN
                ),
            )
            for check_id, passed, detail in design_checks
        ]
        blockers = []
        if contract_readiness_violations:
            blockers.extend(contract_readiness_violations)
        failed_design = [
            item.check_id
            for item in freeze_conditions
            if item.status != "pass"
        ]
        if protocol["unknown_fields"]:
            blockers.append(
                "scientific design fields remain unknown: "
                + ", ".join(protocol["unknown_fields"])
            )
        if not mvp_verified:
            environment_status = str(
                mvp_contract.get("environment_probe", {}).get(
                    "status", "unknown"
                )
            )
            blockers.append(
                "Stage 2 MVP is not verified: establish a minimum runnable "
                "environment, execute 2–3 non-scientific smoke cases, and "
                f"verify metric computation (environment={environment_status})."
            )
        remaining = [
            item for item in failed_design if item != "stage2_mvp_verified"
        ]
        if remaining:
            blockers.append(
                "design freeze conditions failed: " + ", ".join(remaining)
            )
        warnings = [
            (
                "The full dataset, exact runtime lock, formal baseline matrix, "
                "treatment execution, and scientific effect estimate are "
                "explicit Stage 3 tasks."
            )
        ]
        status = (
            Stage2GateStatus.BUILD_REQUIRED
            if blockers
            else Stage2GateStatus.DESIGN_READY
        )
    else:
        status = (
            Stage2GateStatus.FAIL
            if blockers
            else Stage2GateStatus.CONDITIONAL_PASS
            if warnings
            else Stage2GateStatus.PASS
        )
    from .scientific_validity import (
        ScientificValidityContract,
        ValiditySeverity,
        audit_design_validity,
    )

    current_contract = context.repository.latest_research_contract(
        context.study_id
    )
    validity_contract = ScientificValidityContract.model_validate(
        (
            current_contract.scientific_validity_contract
            if current_contract is not None
            else protocol.get("scientific_validity_contract") or {}
        )
    )
    validity_report = audit_design_validity(validity_contract)
    blocking_validity = [
        item
        for item in validity_report.findings
        if item.severity is ValiditySeverity.BLOCKING
    ]
    nonblocking_validity = [
        item
        for item in validity_report.findings
        if item.severity is not ValiditySeverity.BLOCKING
    ]
    if blocking_validity:
        blockers.extend(
            f"{item.code}: {item.message}" for item in blocking_validity
        )
        status = Stage2GateStatus.FAIL
    warnings.extend(
        f"{item.code}: {item.message}" for item in nonblocking_validity
    )
    completed = [
        "2.0 input check",
        "2.1 method investigation",
        "2.2 local resource inventory",
        "2.3 data boundary",
        "2.4 resource gaps",
        "2.5 candidate topics",
        "2.6 specific Scope",
        "2.6b experiment asset construction",
        "2.7 protocol draft",
        "2.8 decision rules",
        "2.9 MVP preflight",
        "2.10 non-scientific feasibility probe",
    ]
    report = Stage2GateReport(
        study_id=context.study_id,
        research_contract_version=int(
            protocol["research_contract_version"]
        ),
        status=status,
        freeze_conditions=freeze_conditions,
        completed_steps=completed,
        incomplete_steps=(
            ["2.11 freeze locks", "2.12 exit Gate"]
            if status
            in {Stage2GateStatus.FAIL, Stage2GateStatus.BUILD_REQUIRED}
            else []
        ),
        blockers=blockers,
        warnings=warnings,
        required_user_decisions=(
            ["Resolve blockers and generate a protocol vNext."]
            if status is Stage2GateStatus.FAIL
            else (
                [
                    "Complete and verify the non-scientific Stage 2 MVP; full "
                    "scientific resources remain a Stage 3 responsibility."
                ]
                if status is Stage2GateStatus.BUILD_REQUIRED
                else ["Approve the Research Contract."]
            )
        ),
        allowed_next_actions=(
            ["edit protocol inputs", "supply resources", "rerun baseline"]
            if status is Stage2GateStatus.FAIL
            else (
                [
                    "provision the minimum local feasibility environment",
                    "run 2–3 non-scientific smoke cases",
                    "verify metric computation and conceptual baseline feasibility",
                    "record estimated runtime and reset cost",
                ]
                if status is Stage2GateStatus.BUILD_REQUIRED
                else ["approve Research Contract", "freeze Stage 2"]
            )
        ),
        prohibited_next_actions=[
            "execute formal treatment before Stage 2 freezes",
            "emit a supported/refuted Study verdict from Stage 2",
        ],
        scientific_validity_report=validity_report.model_dump(mode="json"),
    )
    artifact_id = _write_named_artifact(
        context,
        _versioned_artifact_name(
            "stage2_gate_report.json",
            int(protocol["research_contract_version"]),
        ),
        report,
        role=ArtifactRole.AUDIT,
    )
    contract = context.repository.latest_research_contract(context.study_id)
    gate = None
    if status in {
        Stage2GateStatus.PASS,
        Stage2GateStatus.CONDITIONAL_PASS,
        Stage2GateStatus.DESIGN_READY,
    } and contract is not None:
        existing = [
            item
            for item in context.repository.list_gates(context.study_id)
            if item.gate_type is GateType.RESEARCH_CONTRACT
            and item.subject_version == contract.version
        ]
        gate = (
            existing[0]
            if existing
            else context.repository.create_gate(
                context.study_id,
                GateType.RESEARCH_CONTRACT,
                "research_contract",
                f"{context.study_id}:research-v{contract.version}",
                subject_version=contract.version,
            )
        )
    return {
        "stage2_gate_report": report.model_dump(mode="json"),
        "gate": gate.model_dump(mode="json") if gate else None,
        "_workflow_output_artifact_ids": [artifact_id],
    }


def _freeze_contract(context: "StepContext") -> dict[str, Any]:
    assessment = _ancestor_result(context, "assess_stage2_gate")[
        "stage2_gate_report"
    ]
    if assessment["status"] not in {
        Stage2GateStatus.PASS.value,
        Stage2GateStatus.CONDITIONAL_PASS.value,
        Stage2GateStatus.DESIGN_READY.value,
    }:
        raise _blocked(
            "Stage 2 is not ready to freeze; complete the scientific design "
            "and non-scientific MVP or repair work first.",
            kind="stage2_not_ready",
        )
    contract = context.repository.latest_research_contract(context.study_id)
    if contract is None:
        raise _blocked("Research Contract draft is unavailable.")
    from .contract_compiler import (
        compile_research_contract,
        dry_run_compiled_contract,
    )

    compiled = compile_research_contract(contract)
    dry_run = dry_run_compiled_contract(compiled)
    if not compiled.compile_passed or not dry_run.passed:
        raise _blocked(
            "Stage 2 scientific specification is not executable: "
            + "; ".join(
                [*compiled.blocking_issues, *dry_run.blocking_issues]
            ),
            kind="contract_revision_required",
        )
    frozen = context.repository.save_research_contract(
        contract.model_copy(
            update={
                "status": ArtifactStatus.FROZEN,
                "protocol_status": ProtocolStatus.FROZEN_EXECUTABLE,
                "compile_report_id": compiled.compile_report_id,
                "dry_run_report_id": dry_run.dry_run_report_id,
                "unresolved_placeholders": [],
                "run_specification_ids": [
                    item.run_specification_id
                    for item in compiled.run_specifications
                ],
                "dataset_specification_id": (
                    compiled.dataset_specification.dataset_specification_id
                    if compiled.dataset_specification is not None
                    else None
                ),
                "algorithm_specification_ids": [
                    item.algorithm_specification_id
                    for item in compiled.algorithm_specifications
                ],
                "evaluation_specification_id": (
                    compiled.evaluation_specification.evaluation_specification_id
                    if compiled.evaluation_specification is not None
                    else None
                ),
                "analysis_specification_id": (
                    compiled.analysis_specification.analysis_specification_id
                    if compiled.analysis_specification is not None
                    else None
                ),
                "executable_run_dag_id": (
                    compiled.executable_run_dag.run_dag_id
                    if compiled.executable_run_dag is not None
                    else None
                ),
                "frozen_at": utc_now(),
            }
        )
    )
    return {"research_contract": frozen.model_dump(mode="json")}


def _freeze_locks(context: "StepContext") -> dict[str, Any]:
    stage_dir = _stage2_dir(context.repository, context.study_id)
    contract = context.repository.latest_research_contract(context.study_id)
    if contract is None or contract.status is not ArtifactStatus.FROZEN:
        raise _blocked("A frozen Research Contract is required.")
    scope = context.repository.load_scope_contract(
        context.study_id, contract.scope_version
    )
    protocol_path = stage_dir / _versioned_artifact_name(
        "protocol.draft.json", contract.version
    )
    rules_path = stage_dir / _versioned_artifact_name(
        "decision_rules.json", contract.version
    )
    protocol_payload = read_json(protocol_path)
    data_payload = read_json(stage_dir / "local_data_manifest.json")
    code_payload = read_json(stage_dir / "local_code_manifest.json")
    environment_payload = read_json(stage_dir / "compute_environment.json")
    rules_payload = read_json(rules_path)
    approval = next(
        (
            item
            for item in context.repository.list_gates(context.study_id)
            if item.gate_type is GateType.RESEARCH_CONTRACT
            and item.subject_version == contract.version
            and item.status is GateStatus.APPROVED
        ),
        None,
    )
    if approval is None:
        raise _blocked("The frozen Research Contract has no approved owner Gate.")
    common = {
        "lock_level": "stage2_scientific_core_and_feasibility_mvp",
        "requires_stage3_execution_contract_vnext": True,
        "scope_contract_sha256": sha256_file(
            stage_dir / "scope_contract.json"
        ),
        "scope_contract_version": scope.version,
        "research_contract_version": contract.version,
        "resource_selection": (
            {
                "artifact": "resource_selection.json",
                "sha256": sha256_file(stage_dir / "resource_selection.json"),
                "selection_id": read_json(
                    stage_dir / "resource_selection.json"
                ).get("selection_id"),
                "approved_resource_ids": contract.runtime_binding.get(
                    "approved_resource_ids", []
                ),
                "approved_local_resource_ids": contract.runtime_binding.get(
                    "approved_local_resource_ids", []
                ),
            }
            if (stage_dir / "resource_selection.json").is_file()
            else {
                "artifact": None,
                "sha256": None,
                "selection_id": None,
                "approved_resource_ids": [],
            }
        ),
        "data_file_hashes": [
            {
                "path": item.get("location"),
                "sha256": item.get("content_hash"),
            }
            for item in data_payload.get("resources", [])
        ],
        "code_commit": code_payload.get("git", {}).get("commit"),
        "uncommitted_changes": code_payload.get("git", {}).get(
            "uncommitted_changes"
        ),
        "model_versions": protocol_payload.get("model_versions", {}),
        "model_weight_hashes": protocol_payload.get(
            "model_weight_hashes", {}
        ),
        "software_dependencies": protocol_payload.get(
            "software_versions", {}
        ),
        "dependency_lock": protocol_payload.get("dependency_lock"),
        "container_image": protocol_payload.get(
            "container_image", "not_applicable_or_unknown"
        ),
        "hardware": environment_payload,
        "metric_implementation": protocol_payload.get(
            "metric_implementation"
        ),
        "random_seeds": protocol_payload.get("seeds", []),
        "statistical_rules": {
            "test": protocol_payload.get("statistical_test"),
            "confidence_interval": protocol_payload.get(
                "confidence_interval"
            ),
            "multiple_comparisons": protocol_payload.get(
                "multiple_comparison_policy"
            ),
            "minimum_meaningful_effect": protocol_payload.get(
                "minimum_meaningful_effect"
            ),
            "success_threshold": protocol_payload.get(
                "success_threshold"
            ),
        },
        "run_budget": protocol_payload.get("budget", {}),
        "log_schema": protocol_payload.get("logging_requirements", []),
        "artifact_directory_rules": protocol_payload.get(
            "artifact_preservation_requirements", []
        ),
        "verdict_decision_rules_sha256": sha256_file(
            rules_path
        ),
        "owner_approval": {
            "gate_id": approval.gate_id,
            "decided_by": approval.decided_by,
            "decided_at": approval.decided_at,
            "reason": approval.reason,
        },
        "formal_treatment_executed": False,
    }
    sources = {
        "protocol.lock.json": protocol_path,
        "environment.lock.json": stage_dir / "compute_environment.json",
        "data_manifest.lock.json": stage_dir / "local_data_manifest.json",
        "code_manifest.lock.json": stage_dir / "local_code_manifest.json",
        "decision_rules.lock.json": rules_path,
    }
    artifact_ids: list[str] = []
    locks: dict[str, Any] = {}
    for name, source in sources.items():
        if not source.is_file():
            raise _blocked(f"Cannot freeze missing source artifact: {source.name}")
        artifact_name = _stage2_lock_artifact_name(
            stage_dir,
            name,
            contract.version,
        )
        payload = {
            "schema_version": 1,
            "study_id": context.study_id,
            "scope_version": contract.scope_version,
            "research_contract_version": contract.version,
            "lock_name": name,
            "source_artifact": source.name,
            "source_sha256": sha256_file(source),
            "frozen_content": (
                {**rules_payload, "frozen": True}
                if name == "decision_rules.lock.json"
                else read_json(source)
            ),
            **common,
            "frozen_at": utc_now(),
            "immutable": True,
        }
        target_path = stage_dir / artifact_name
        artifact_id = _reuse_stage2_lock_artifact(
            context,
            target_path,
            payload,
        )
        if artifact_id is None:
            artifact_id = _write_named_artifact(
                context,
                artifact_name,
                payload,
                role=ArtifactRole.PROTOCOL,
            )
        artifact_ids.append(artifact_id)
        locks[name] = payload
    return {
        "locks": locks,
        "_workflow_output_artifact_ids": artifact_ids,
    }


def _complete_stage2(context: "StepContext") -> dict[str, Any]:
    return {
        "stage2_status": "completed",
        "next_phase": Phase.EXPERIMENT.value,
        "formal_treatment_executed": False,
        "_workflow_next_phase": Phase.EXPERIMENT.value,
    }


def stage_two_handlers() -> dict[str, "StepHandler"]:
    return {
        "stage2_input_check": _input_check,
        "investigate_related_methods": _method_investigation,
        "inventory_research_resources": _resource_inventory,
        "define_data_boundary": _data_boundary,
        "diagnose_resource_gaps": _resource_gaps,
        "define_resource_requirements": _resource_requirements,
        "discover_concrete_resources": _discover_concrete_resources,
        "validate_and_compare_resources": _validate_and_compare_resources,
        "generate_candidate_topics": _candidate_topics,
        "build_experiment_assets": _build_experiment_assets,
        "draft_research_protocol": _protocol_draft,
        "define_decision_rules": _decision_rules,
        "run_stage2_preflight": _preflight,
        "validate_stage2_baseline": _baseline_validation,
        "lint_research_contract": _lint_research_contract,
        "compile_research_contract": _compile_research_contract,
        "dry_run_research_contract": _dry_run_research_contract,
        "execution_readiness_gate": _execution_readiness_gate,
        "assess_stage2_gate": _gate_assessment,
        "freeze_research_contract": _freeze_contract,
        "freeze_stage2_locks": _freeze_locks,
        "complete_stage2": _complete_stage2,
    }


def ensure_stage_two_dag(
    repository: WorkflowRepository,
    study_id: str,
) -> list[StepInstance]:
    """Append the Stage 2 DAG after a broad direction Scope is frozen."""

    existing = repository.list_steps(study_id)
    by_type = {item.step_type: item for item in existing}
    if "stage2_input_check" in by_type:
        return [
            item
            for item in existing
            if item.phase is Phase.PROTOCOL
            and item.task_group == "stage2"
        ]
    scope = repository.latest_scope_contract(study_id)
    if (
        scope is None
        or scope.status is not ArtifactStatus.FROZEN
        or scope.contract_level != "direction"
    ):
        raise ValueError("Stage 2 requires a frozen direction-level Scope")
    freeze_scope = by_type.get("freeze_scope_contract")
    project_scan = by_type.get("project_scan")
    dependencies = [
        item.step_instance_id
        for item in (freeze_scope, project_scan)
        if item is not None
    ]
    created: list[StepInstance] = []

    def add(
        step_type: str,
        executor: ExecutorType,
        depends_on: Iterable[StepInstance],
        expected_output: str,
    ) -> StepInstance:
        step = repository.add_step(
            study_id,
            step_type,
            Phase.PROTOCOL,
            executor,
            depends_on=[item.step_instance_id for item in depends_on],
            task_group="stage2",
            expected_output=expected_output,
        )
        created.append(step)
        return step

    input_check = repository.add_step(
        study_id,
        "stage2_input_check",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=dependencies,
        task_group="stage2",
        expected_output="stage2_input_check.json",
    )
    created.append(input_check)
    methods = add(
        "investigate_related_methods",
        ExecutorType.RETRIEVAL_SERVICE,
        [input_check],
        "method cards, method summary, and baseline candidates",
    )
    resources = add(
        "inventory_research_resources",
        ExecutorType.DETERMINISTIC_SERVICE,
        [
            input_check,
            *([project_scan] if project_scan is not None else []),
        ],
        "local data/code/compute/resource manifests",
    )
    boundary = add(
        "define_data_boundary",
        ExecutorType.DETERMINISTIC_SERVICE,
        [resources],
        "data_boundary.json and leakage_risk_report.json",
    )
    gaps = add(
        "diagnose_resource_gaps",
        ExecutorType.DETERMINISTIC_SERVICE,
        [methods, resources, boundary],
        "resource gap report and request cards",
    )
    requirements = add(
        "define_resource_requirements",
        ExecutorType.DETERMINISTIC_SERVICE,
        [gaps, boundary],
        "resource_requirements.json",
    )
    discovery = add(
        "discover_concrete_resources",
        ExecutorType.RETRIEVAL_SERVICE,
        [requirements, methods, resources],
        "concrete_resource_candidates.json",
    )
    comparison = add(
        "validate_and_compare_resources",
        ExecutorType.DETERMINISTIC_EVALUATOR,
        [requirements, discovery],
        "resource_candidate_evaluation.json and comparison",
    )
    topics = add(
        "generate_candidate_topics",
        ExecutorType.MODEL,
        [gaps, comparison],
        "candidate_topics.json",
    )
    selection = add(
        "select_specific_topic",
        ExecutorType.PROJECT_OWNER,
        [topics],
        "owner-selected, frozen specific-topic Scope vNext",
    )
    experiment_build = add(
        "build_experiment_assets",
        ExecutorType.CODEX,
        [selection, requirements, comparison, resources],
        (
            "dataset specification, annotation protocol, metric contract, "
            "baseline runner, and treatment declaration"
        ),
    )
    protocol = add(
        "draft_research_protocol",
        ExecutorType.CODEX,
        [selection, experiment_build, methods, resources, boundary],
        "protocol.draft.json and Research Contract draft",
    )
    rules = add(
        "define_decision_rules",
        ExecutorType.DETERMINISTIC_SERVICE,
        [protocol],
        "decision_rules.json",
    )
    preflight = add(
        "run_stage2_preflight",
        ExecutorType.DETERMINISTIC_SERVICE,
        [protocol, rules, resources, boundary],
        "18-check preflight_report.json",
    )
    baseline = add(
        "validate_stage2_baseline",
        ExecutorType.SANDBOX_RUNNER,
        [preflight],
        "baseline_validation_report.json",
    )
    lint = add(
        "lint_research_contract",
        ExecutorType.DETERMINISTIC_EVALUATOR,
        [baseline],
        "contract_lint_report.json",
    )
    compile_contract = add(
        "compile_research_contract",
        ExecutorType.DETERMINISTIC_SERVICE,
        [lint],
        "RunSpecification[], EvaluationSpecification, AnalysisSpecification",
    )
    dry_run = add(
        "dry_run_research_contract",
        ExecutorType.SANDBOX_RUNNER,
        [compile_contract],
        "non-evidentiary contract dry-run report",
    )
    readiness = add(
        "execution_readiness_gate",
        ExecutorType.DETERMINISTIC_EVALUATOR,
        [dry_run],
        "execution readiness decision packet",
    )
    assessment = add(
        "assess_stage2_gate",
        ExecutorType.DETERMINISTIC_EVALUATOR,
        [readiness],
        "stage2_gate_report.json",
    )
    review = add(
        "research_contract_review",
        ExecutorType.PROJECT_OWNER,
        [assessment],
        "owner approval of a non-failing Research Contract",
    )
    freeze = add(
        "freeze_research_contract",
        ExecutorType.DETERMINISTIC_SERVICE,
        [review],
        "frozen Research Contract",
    )
    locks = add(
        "freeze_stage2_locks",
        ExecutorType.DETERMINISTIC_SERVICE,
        [freeze],
        "five immutable Stage 2 lock artifacts",
    )
    add(
        "complete_stage2",
        ExecutorType.DETERMINISTIC_SERVICE,
        [locks],
        "Stage 2 exit and Stage 3 handoff",
    )
    return created


def _latest_successful_step(
    repository: WorkflowRepository,
    study_id: str,
    step_type: str,
) -> StepInstance:
    candidates = [
        item
        for item in repository.list_steps(study_id)
        if item.step_type == step_type
        and item.status is ExecutionStatus.SUCCEEDED
    ]
    if not candidates:
        raise ValueError(f"required Stage 2 step is unavailable: {step_type}")
    return max(candidates, key=lambda item: item.updated_at)


def revise_stage_two_protocol(
    repository: WorkflowRepository,
    study_id: str,
    protocol_overrides: dict[str, Any],
    *,
    decided_by: str = "project_owner",
    reason: str = "Revise the unfrozen Stage 2 protocol.",
) -> dict[str, Any]:
    """Create an append-only protocol draft vNext before contract freeze."""

    if not protocol_overrides:
        raise ValueError("protocol_overrides cannot be empty")
    forbidden = {
        "formal_treatment_execution_allowed",
        "treatment_execution",
        "scientific_verdict",
    }.intersection(protocol_overrides)
    if forbidden:
        raise ValueError(
            "Stage 2 protocol revisions cannot authorize treatment or verdicts: "
            + ", ".join(sorted(forbidden))
        )
    current_contract = repository.latest_research_contract(study_id)
    if current_contract is None:
        raise ValueError("the Study has no Research Contract to revise")
    study = repository.load_study(study_id)
    if current_contract.status is ArtifactStatus.FROZEN:
        started_runs = [
            item
            for item in repository.list_research_runs(study_id)
            if item.status
            in {
                ExecutionStatus.RUNNING,
                ExecutionStatus.RETRYING,
                ExecutionStatus.SUCCEEDED,
            }
        ]
        if started_runs:
            raise ValueError(
                "a frozen contract with started formal runs requires a "
                "Repair Contract and successor run"
            )
        study = repository.save_study(
            study.model_copy(update={"phase": Phase.PROTOCOL}),
            "frozen_research_contract_revision_requested",
        )
    elif study.phase is not Phase.PROTOCOL:
        raise ValueError("pre-freeze protocol revision requires phase=protocol")
    current_primary_metric = (
        dict(current_contract.metrics[0])
        if current_contract.metrics
        else {}
    )
    # A vNext proposal is a field-level revision of the current scientific
    # design.  Recompiling unrelated fields from the original Scope can
    # silently regress a concrete contract back to generic placeholders.
    # Seed the draft from the latest contract, then apply persisted and newly
    # requested edits on top.
    inherited_contract_fields = {
        "primary_metric": current_primary_metric.get("name"),
        "metric_direction": current_primary_metric.get("direction"),
        "denominator": current_primary_metric.get("denominator")
        or current_contract.data_boundary.get("denominator"),
        "baseline_name": current_contract.baseline.get("name"),
        "baseline_behavior": current_contract.baseline.get("behavior")
        or current_contract.baseline.get("operation"),
        "baseline_experiment_id": current_contract.baseline.get(
            "experiment_id"
        ),
        "baseline_action_id": current_contract.baseline.get("action_id"),
        "treatment_name": current_contract.treatment.get("name"),
        "treatment_behavior": current_contract.treatment.get("behavior")
        or current_contract.treatment.get("operation"),
        "treatment_experiment_id": current_contract.treatment.get(
            "experiment_id"
        ),
        "treatment_action_id": current_contract.treatment.get("action_id"),
        "tasks": list(current_contract.tasks),
        "seeds": list(current_contract.seeds),
        "splits": list(current_contract.splits),
        "repetitions": current_contract.replicates,
        "experiment_profile": current_contract.experiment_profile,
        "profile_parameters": dict(current_contract.profile_parameters),
        "output_schema": dict(current_contract.output_schema),
        "statistical_rules": dict(current_contract.statistical_rules),
        "data_boundary": dict(current_contract.data_boundary),
        "evaluator_policy": dict(current_contract.evaluator_policy),
        "eligibility_rules": [
            dict(item) for item in current_contract.eligibility_rules
        ],
    }
    merged = {
        **{
            key: value
            for key, value in inherited_contract_fields.items()
            if value not in (None, "", [], {})
        },
        **dict(study.settings.get("stage2_protocol_overrides") or {}),
        **protocol_overrides,
    }
    scope = repository.latest_scope_contract(study_id)
    if scope is None:
        raise ValueError("the Study has no Scope Contract for design compilation")
    compiled_frame = _comparison_frame_for_scope(scope)
    merged_tasks = [
        str(item).strip()
        for item in merged.get("tasks", [])
        if str(item).strip()
    ]
    if not merged_tasks or any(
        item.casefold()
        in {"formal-primary-task", "primary-task", "task", "default"}
        for item in merged_tasks
    ):
        merged["tasks"] = list(compiled_frame.get("tasks") or [])
    if not str(merged.get("baseline_behavior") or "").strip():
        merged["baseline_behavior"] = compiled_frame.get("baseline_behavior")
    if not str(merged.get("treatment_behavior") or "").strip():
        merged["treatment_behavior"] = compiled_frame.get(
            "treatment_behavior"
        )
    for gate in repository.list_gates(study_id):
        if (
            gate.gate_type is GateType.RESEARCH_CONTRACT
            and gate.subject_version == current_contract.version
            and gate.status is GateStatus.AWAITING_USER
        ):
            repository.decide_gate(
                study_id,
                gate.gate_id,
                approve=False,
                decided_by="research_forge_system",
                reason=(
                    f"Superseded by unfrozen Research Contract "
                    f"v{current_contract.version + 1}."
                ),
            )
    for step in repository.list_steps(study_id):
        if (
            step.step_type == "research_contract_review"
            and step.status is ExecutionStatus.WAITING_FOR_USER
        ):
            repository.update_step(
                study_id,
                step.step_instance_id,
                ExecutionStatus.CANCELLED,
                blocker={
                    "kind": "superseded",
                    "message": (
                        f"Superseded by Research Contract "
                        f"v{current_contract.version + 1}."
                    ),
                },
            )
        if (
            current_contract.status is ArtifactStatus.FROZEN
            and step.phase is Phase.EXPERIMENT
            and step.status
            in {
                ExecutionStatus.QUEUED,
                ExecutionStatus.BLOCKED,
                ExecutionStatus.FAILED,
                ExecutionStatus.WAITING_FOR_USER,
            }
        ):
            repository.update_step(
                study_id,
                step.step_instance_id,
                ExecutionStatus.CANCELLED,
                blocker={
                    "kind": "superseded_contract",
                    "message": (
                        "Superseded by Research Contract "
                        f"v{current_contract.version + 1}; the historical "
                        "Stage 3 attempt remains in the audit ledger."
                    ),
                },
            )
    declaration_time = utc_now()
    repository.save_study(
        study.model_copy(
            update={
                "settings": {
                    **study.settings,
                    "stage2_protocol_overrides": merged,
                    "stage2_protocol_declaration": {
                        "declared_by": decided_by,
                        "declared_at": declaration_time,
                        "reason": reason,
                        "asserted_fields": sorted(
                            key
                            for key, value in merged.items()
                            if isinstance(value, bool) and value
                        ),
                    },
                    "stage2_baseline_experiment_id": merged.get(
                        "baseline_experiment_id"
                    ),
                    "stage2_run_baseline": bool(
                        merged.get("run_baseline", False)
                    ),
                    "stage2_baseline_action_id": merged.get(
                        "baseline_action_id"
                    ),
                }
            }
        ),
        "stage2_protocol_revision_requested",
    )
    selection = _latest_successful_step(
        repository, study_id, "select_specific_topic"
    )
    inventory = _latest_successful_step(
        repository, study_id, "inventory_research_resources"
    )
    methods = _latest_successful_step(
        repository, study_id, "investigate_related_methods"
    )
    boundary = _latest_successful_step(
        repository, study_id, "define_data_boundary"
    )
    gaps = _latest_successful_step(
        repository, study_id, "diagnose_resource_gaps"
    )
    experiment_build = _latest_successful_step(
        repository, study_id, "build_experiment_assets"
    )
    version = current_contract.version + 1
    task_group = f"stage2-revision-v{version}"
    draft = repository.add_step(
        study_id,
        "draft_research_protocol",
        Phase.PROTOCOL,
        ExecutorType.CODEX,
        depends_on=[
            selection.step_instance_id,
            experiment_build.step_instance_id,
            inventory.step_instance_id,
            methods.step_instance_id,
            boundary.step_instance_id,
        ],
        task_group=task_group,
        expected_output=_versioned_artifact_name(
            "protocol.draft.json", version
        ),
    )
    rules = repository.add_step(
        study_id,
        "define_decision_rules",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[draft.step_instance_id],
        task_group=task_group,
        expected_output=_versioned_artifact_name(
            "decision_rules.json", version
        ),
    )
    preflight = repository.add_step(
        study_id,
        "run_stage2_preflight",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[
            draft.step_instance_id,
            inventory.step_instance_id,
            boundary.step_instance_id,
        ],
        task_group=task_group,
        expected_output=_versioned_artifact_name(
            "preflight_report.json", version
        ),
    )
    baseline = repository.add_step(
        study_id,
        "validate_stage2_baseline",
        Phase.PROTOCOL,
        ExecutorType.SANDBOX_RUNNER,
        depends_on=[
            draft.step_instance_id,
            preflight.step_instance_id,
            inventory.step_instance_id,
        ],
        task_group=task_group,
        expected_output=_versioned_artifact_name(
            "baseline_validation_report.json", version
        ),
    )
    lint = repository.add_step(
        study_id,
        "lint_research_contract",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        depends_on=[baseline.step_instance_id],
        task_group=task_group,
        expected_output=_versioned_artifact_name(
            "contract_lint_report.json", version
        ),
    )
    compile_contract = repository.add_step(
        study_id,
        "compile_research_contract",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[lint.step_instance_id],
        task_group=task_group,
        expected_output=_versioned_artifact_name(
            "contract_compile_report.json", version
        ),
    )
    dry_run = repository.add_step(
        study_id,
        "dry_run_research_contract",
        Phase.PROTOCOL,
        ExecutorType.SANDBOX_RUNNER,
        depends_on=[compile_contract.step_instance_id],
        task_group=task_group,
        expected_output=_versioned_artifact_name(
            "contract_dry_run_report.json", version
        ),
    )
    readiness = repository.add_step(
        study_id,
        "execution_readiness_gate",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_EVALUATOR,
        depends_on=[dry_run.step_instance_id],
        task_group=task_group,
        expected_output=_versioned_artifact_name(
            "execution_readiness_gate.json", version
        ),
    )
    assessment = repository.add_step(
        study_id,
        "assess_stage2_gate",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[
            readiness.step_instance_id,
            gaps.step_instance_id,
            rules.step_instance_id,
        ],
        task_group=task_group,
        expected_output=_versioned_artifact_name(
            "stage2_gate_report.json", version
        ),
    )
    review = repository.add_step(
        study_id,
        "research_contract_review",
        Phase.PROTOCOL,
        ExecutorType.PROJECT_OWNER,
        depends_on=[assessment.step_instance_id],
        task_group=task_group,
        expected_output="owner decision",
    )
    freeze = repository.add_step(
        study_id,
        "freeze_research_contract",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[review.step_instance_id, assessment.step_instance_id],
        task_group=task_group,
        expected_output=f"ResearchContractVersion v{version}",
    )
    locks = repository.add_step(
        study_id,
        "freeze_stage2_locks",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[freeze.step_instance_id],
        task_group=task_group,
        expected_output="five immutable Stage 2 lock artifacts",
    )
    complete = repository.add_step(
        study_id,
        "complete_stage2",
        Phase.PROTOCOL,
        ExecutorType.DETERMINISTIC_SERVICE,
        depends_on=[locks.step_instance_id],
        task_group=task_group,
        expected_output="Stage 2 exit and Stage 3 handoff",
    )
    return {
        "study_id": study_id,
        "requested_version": version,
        "reason": reason,
        "decided_by": decided_by,
        "step_instance_ids": [
            item.step_instance_id
            for item in (
                draft,
                rules,
                preflight,
                baseline,
                assessment,
                review,
                freeze,
                locks,
                complete,
            )
        ],
    }


def bind_stage_two_evaluation_dataset(
    repository: WorkflowRepository,
    study_id: str,
    dataset_path: str | Path,
    *,
    decided_by: str = "project_owner",
    compliance_cleared: bool = False,
    metric_direction: str | None = None,
    success_threshold: str | None = None,
    minimum_meaningful_effect: str | None = None,
) -> dict[str, Any]:
    """Bind an approved JSONL evaluation set and request a baseline successor.

    The caller supplies scientific observations; Research Forge validates and
    snapshots them but never invents missing labels or outcome values.
    """

    source = Path(dataset_path).resolve()
    if not source.is_file() or source.suffix.casefold() != ".jsonl":
        raise ValueError("Stage 2 evaluation data must be an existing JSONL file")
    if is_secret_path(source.name):
        raise ValueError("secret or credential files cannot become experiment data")
    if source.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Stage 2 evaluation data exceeds the 25 MiB limit")
    stage_dir = _stage2_dir(repository, study_id)
    build_plan_path = stage_dir / "experiment_build_plan.json"
    dataset_spec_path = stage_dir / "experiment_build" / "dataset_spec.json"
    manifest_path = (
        stage_dir
        / "experiment_build"
        / "research-forge.experiments.json"
    )
    runner_path = stage_dir / "experiment_build" / "baseline_runner.py"
    if not all(
        path.is_file()
        for path in (
            build_plan_path,
            dataset_spec_path,
            manifest_path,
            runner_path,
        )
    ):
        raise ValueError("Stage 2 experiment scaffold is unavailable")
    spec = read_json(dataset_spec_path)
    required_fields = set(dict(spec.get("required_fields") or {}))
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        source.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid JSONL at line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(row, dict):
            raise ValueError(f"evaluation row {line_number} must be an object")
        missing = sorted(required_fields.difference(row))
        if missing:
            raise ValueError(
                f"evaluation row {line_number} misses fields: "
                + ", ".join(missing)
            )
        rows.append(row)
    eligible = [row for row in rows if row.get("eligible", True)]
    minimum_cases = int(spec.get("minimum_cases") or 1)
    if len(eligible) < minimum_cases:
        raise ValueError(
            f"evaluation data needs at least {minimum_cases} eligible cases"
        )
    case_ids = [str(row.get("case_id", "")) for row in eligible]
    if any(not item for item in case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("eligible case_id values must be non-empty and unique")
    build_plan = read_json(build_plan_path)
    metric_name = str(
        build_plan["protocol_defaults"]["primary_metric"]
    )
    default_direction = str(
        build_plan["protocol_defaults"]["metric_direction"]
    )
    if (
        default_direction == "predeclare_before_freeze"
        and not metric_direction
    ):
        raise ValueError(
            "metric_direction is required for a generic evaluation dataset"
        )
    baseline_field = (
        "baseline_policy_violation_acceptance_rate"
        if metric_name == "policy_violation_acceptance_rate"
        else "baseline_unsupported_claim_rate"
        if metric_name == "unsupported_claim_rate"
        else metric_name
    )
    invalid_metrics = [
        case_id
        for case_id, row in zip(case_ids, eligible, strict=True)
        if not isinstance(row.get(baseline_field), (int, float))
        or isinstance(row.get(baseline_field), bool)
    ]
    if invalid_metrics:
        raise ValueError(
            f"eligible rows require numeric {baseline_field}: "
            + ", ".join(invalid_metrics[:5])
        )

    dataset_hash = sha256_file(source)
    binding_id = stable_id(
        "experiment-build-binding", study_id, dataset_hash
    )
    workspace = stage_dir / "experiment_build_bindings" / binding_id
    data_target = workspace / "data" / "evaluation.jsonl"
    if workspace.exists():
        if not data_target.is_file() or sha256_file(data_target) != dataset_hash:
            raise ValueError("experiment build binding is not idempotent")
    else:
        (workspace / "data").mkdir(parents=True, exist_ok=False)
        shutil.copy2(source, data_target)
        shutil.copy2(manifest_path, workspace / manifest_path.name)
        shutil.copy2(runner_path, workspace / runner_path.name)
    dataset_artifact = repository.register_artifact(
        study_id,
        str(data_target),
        dataset_hash,
        kind="stage2_evaluation_dataset",
        role=ArtifactRole.PROTOCOL,
        status=ArtifactStatus.FROZEN,
    )
    binding_record = {
        "schema_version": 1,
        "binding_id": binding_id,
        "study_id": study_id,
        "dataset_path": str(data_target),
        "dataset_sha256": dataset_hash,
        "eligible_cases": len(eligible),
        "metric_field": baseline_field,
        "workspace_root": str(workspace),
        "approved_by": decided_by,
        "compliance_cleared": compliance_cleared,
        "bound_at": utc_now(),
    }
    binding_path = stage_dir / f"{binding_id}.json"
    if not binding_path.exists():
        write_json_atomic(binding_path, binding_record)
    binding_artifact = repository.register_artifact(
        study_id,
        str(binding_path),
        sha256_file(binding_path),
        kind="stage2_experiment_build_binding",
        role=ArtifactRole.PROTOCOL,
        status=ArtifactStatus.FROZEN,
    )
    study = repository.load_study(study_id)
    repository.save_study(
        study.model_copy(
            update={
                "settings": {
                    **study.settings,
                    "stage2_experiment_workspace_override": str(workspace),
                    "stage2_experiment_input_hashes": {
                        "data/evaluation.jsonl": dataset_hash,
                    },
                }
            }
        ),
        "stage2_evaluation_dataset_bound",
    )
    revision = revise_stage_two_protocol(
        repository,
        study_id,
        {
            "run_baseline": True,
            "baseline_experiment_id": "stage2-generated-baseline-v1",
            "baseline_action_id": "action-stage2-generated-baseline",
            "data_loadable": True,
            "data_schema_readable": True,
            "inclusion_exclusion_executable": True,
            "split_boundary_verified": True,
            "leakage_controls_verified": True,
            "metric_unit_test_passed": True,
            "statistical_synthetic_test_passed": True,
            "dependency_installable": True,
            "hardware_sufficient": True,
            "logging_validated": True,
            "seed_fixable": True,
            "artifact_preservation_validated": True,
            "binding_validated": True,
            "failure_detection_validated": True,
            "budget_reasonable": True,
            "sensitive_data_guard_passed": True,
            "compliance_cleared": compliance_cleared,
            "approved_resource_substitutions": [
                (
                    "owner-approved, content-frozen evaluation dataset "
                    f"{dataset_hash}"
                )
            ],
            "inclusion_criteria": ["eligible=true and schema-valid"],
            "exclusion_criteria": ["eligible=false or schema-invalid"],
            "data_version": dataset_hash,
            "metric_direction": metric_direction or default_direction,
            "success_threshold": (
                success_threshold
                or build_plan["protocol_defaults"]["success_threshold"]
            ),
            "minimum_meaningful_effect": (
                minimum_meaningful_effect
                or build_plan["protocol_defaults"][
                    "minimum_meaningful_effect"
                ]
            ),
        },
        decided_by=decided_by,
        reason="Bind an owner-approved evaluation set and validate the baseline.",
    )
    return {
        "binding": binding_record,
        "dataset_artifact_id": dataset_artifact.artifact_id,
        "binding_artifact_id": binding_artifact.artifact_id,
        "revision": revision,
    }


def approve_stage_two_mvp(
    repository: WorkflowRepository,
    study_id: str,
    report_path: str | Path,
    *,
    decided_by: str = "project_owner",
) -> dict[str, Any]:
    """Bind a non-scientific 2–3 case feasibility report and request vNext."""

    source = Path(report_path).resolve()
    if not source.is_file() or source.suffix.casefold() != ".json":
        raise ValueError("Stage 2 MVP report must be an existing JSON file")
    if is_secret_path(source.name):
        raise ValueError("secret or credential files cannot enter an MVP report")
    payload = read_json(source)
    if payload.get("scientific_evidence_eligible") is not False:
        raise ValueError(
            "MVP report must explicitly set scientific_evidence_eligible=false"
        )
    smoke_cases = list(payload.get("smoke_cases") or [])
    if not 2 <= len(smoke_cases) <= 3:
        raise ValueError("Stage 2 MVP requires exactly 2–3 smoke cases")
    case_ids = [str(item.get("case_id", "")) for item in smoke_cases]
    if any(not item for item in case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("MVP smoke case IDs must be non-empty and unique")
    if any(item.get("status") != "passed" for item in smoke_cases):
        raise ValueError("every Stage 2 MVP smoke case must pass")
    required_true = (
        "environment_started",
        "metric_computable",
        "baseline_instantiable",
        "failure_modes_distinguishable",
        "reset_or_isolation_verified",
    )
    missing = [name for name in required_true if payload.get(name) is not True]
    if missing:
        raise ValueError(
            "MVP report has unverified feasibility checks: "
            + ", ".join(missing)
        )
    prohibited = {
        "scientific_verdict",
        "treatment_effect",
        "effect_size",
        "p_value",
    }.intersection(payload)
    if prohibited:
        raise ValueError(
            "Stage 2 MVP cannot contain scientific result fields: "
            + ", ".join(sorted(prohibited))
        )
    runtime_seconds = payload.get("runtime_seconds")
    if (
        not isinstance(runtime_seconds, (int, float))
        or isinstance(runtime_seconds, bool)
        or runtime_seconds <= 0
    ):
        raise ValueError("MVP runtime_seconds must be a positive number")

    report_hash = sha256_file(source)
    stage_dir = _stage2_dir(repository, study_id)
    binding_id = stable_id("stage2-mvp", study_id, report_hash)
    target = stage_dir / "mvp_bindings" / binding_id / "mvp_report.json"
    if target.exists():
        if sha256_file(target) != report_hash:
            raise ValueError("MVP binding is not idempotent")
    else:
        target.parent.mkdir(parents=True, exist_ok=False)
        shutil.copy2(source, target)
    artifact = repository.register_artifact(
        study_id,
        str(target),
        report_hash,
        kind="stage2_non_scientific_mvp",
        role=ArtifactRole.EVALUATION,
        status=ArtifactStatus.FROZEN,
    )
    mvp_record = {
        "status": "verified",
        "verified": True,
        "scientific_evidence_eligible": False,
        "binding_id": binding_id,
        "report_path": str(target),
        "report_sha256": report_hash,
        "smoke_case_count": len(smoke_cases),
        "runtime_seconds": float(runtime_seconds),
        "environment_probe": {
            "status": "verified_by_mvp",
            "evidence": str(target),
        },
        "approved_by": decided_by,
        "approved_at": utc_now(),
    }
    study = repository.load_study(study_id)
    repository.save_study(
        study.model_copy(
            update={
                "settings": {
                    **study.settings,
                    "stage2_mvp_override": mvp_record,
                }
            }
        ),
        "stage2_mvp_bound",
    )
    revision = revise_stage_two_protocol(
        repository,
        study_id,
        {"mvp_verified": True},
        decided_by=decided_by,
        reason="Bind a verified non-scientific Stage 2 MVP.",
    )
    return {
        "mvp": mvp_record,
        "artifact_id": artifact.artifact_id,
        "revision": revision,
    }


def select_stage_two_topic(
    repository: WorkflowRepository,
    study_id: str,
    topic_id: str,
    *,
    resource_candidate_ids: list[str] | None = None,
    decided_by: str = "project_owner",
    reason: str | None = None,
    overrides: dict[str, Any] | None = None,
    protocol_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select a candidate and freeze a specific-topic Scope vNext."""

    selection_step = next(
        (
            item
            for item in repository.list_steps(study_id)
            if item.step_type == "select_specific_topic"
        ),
        None,
    )
    topics_step = next(
        (
            item
            for item in repository.list_steps(study_id)
            if item.step_type == "generate_candidate_topics"
        ),
        None,
    )
    if selection_step is None or topics_step is None:
        raise ValueError("Stage 2 topic selection is not initialized")
    if selection_step.status.value not in {"waiting_for_user", "queued"}:
        raise ValueError("Stage 2 topic selection is not awaiting a decision")
    payload = repository.load_step_result(
        study_id, topics_step.step_instance_id
    )
    candidate = next(
        (
            TopicCandidate.model_validate(item)
            for item in payload["candidates"]
            if item["topic_id"] == topic_id
        ),
        None,
    )
    if candidate is None:
        raise ValueError("topic_id does not belong to this Study")
    if candidate.status is TopicStatus.BLOCKED:
        raise ValueError("a blocked topic cannot be selected")
    comparison_step = _latest_successful_step(
        repository, study_id, "validate_and_compare_resources"
    )
    comparison = repository.load_step_result(
        study_id, comparison_step.step_instance_id
    )["resource_candidate_evaluation"]
    available_candidates = {
        str(item["candidate_id"]): item
        for item in comparison.get("candidates", [])
    }
    if resource_candidate_ids is None:
        required_type_values = {
            item.value for item in candidate.required_resource_types
        }
        chosen_resource_candidates = [
            item
            for item in available_candidates.values()
            if item.get("source_kind") == "local_project"
            and (
                item.get("resource_id") in candidate.required_resource_ids
                or item.get("need_type") in required_type_values
            )
            and item.get("eligibility") != CandidateEligibility.INELIGIBLE.value
        ]
    else:
        unknown = sorted(set(resource_candidate_ids) - set(available_candidates))
        if unknown:
            raise ValueError(
                "resource_candidate_ids do not belong to this Study: "
                + ", ".join(unknown)
            )
        chosen_resource_candidates = [
            available_candidates[item] for item in resource_candidate_ids
        ]
    ineligible = [
        item["candidate_id"]
        for item in chosen_resource_candidates
        if item.get("eligibility") == CandidateEligibility.INELIGIBLE.value
    ]
    if ineligible:
        raise ValueError(
            "ineligible resource candidates cannot be selected: "
            + ", ".join(ineligible)
        )
    chosen_types = {
        str(item.get("need_type")) for item in chosen_resource_candidates
    }
    missing_required_types = [
        item.value
        for item in candidate.required_resource_types
        if item.value not in chosen_types
    ]
    planned_resource_types = [
        need_type
        for need_type in missing_required_types
        if not any(
            item.get("need_type") == need_type
            and item.get("eligibility")
            != CandidateEligibility.INELIGIBLE.value
            for item in available_candidates.values()
        )
    ]
    allow_build_plan = (
        candidate.status is TopicStatus.CONDITIONAL
        and set(planned_resource_types) == set(missing_required_types)
    )
    if missing_required_types and not allow_build_plan:
        raise ValueError(
            "selected resources do not satisfy the topic boundary: "
            + ", ".join(missing_required_types)
        )
    if protocol_overrides:
        forbidden = {
            "formal_treatment_execution_allowed",
            "treatment_execution",
            "scientific_verdict",
        }.intersection(protocol_overrides)
        if forbidden:
            raise ValueError(
                "Stage 2 protocol overrides cannot authorize treatment or verdicts: "
                + ", ".join(sorted(forbidden))
            )
        study = repository.load_study(study_id)
        declaration_time = utc_now()
        repository.save_study(
            study.model_copy(
                update={
                    "settings": {
                        **study.settings,
                        "stage2_protocol_overrides": protocol_overrides,
                        "stage2_protocol_declaration": {
                            "declared_by": decided_by,
                            "declared_at": declaration_time,
                            "asserted_fields": sorted(
                                key
                                for key, value in protocol_overrides.items()
                                if isinstance(value, bool) and value
                            ),
                        },
                        "stage2_baseline_experiment_id": (
                            protocol_overrides.get("baseline_experiment_id")
                            or study.settings.get("stage2_baseline_experiment_id")
                        ),
                        "stage2_run_baseline": bool(
                            protocol_overrides.get("run_baseline", False)
                        ),
                        "stage2_baseline_action_id": (
                            protocol_overrides.get("baseline_action_id")
                            or study.settings.get("stage2_baseline_action_id")
                        ),
                    }
                }
            ),
            "stage2_protocol_inputs_configured",
        )
    previous = repository.latest_scope_contract(study_id)
    if previous is None or previous.status is not ArtifactStatus.FROZEN:
        raise ValueError("the direction Scope is not frozen")
    values = {
        "title": candidate.title,
        "research_question": candidate.research_question,
        "hypothesis": candidate.hypothesis,
        "study_design": candidate.study_design,
        "unit_of_analysis": candidate.unit_of_analysis,
        "population_or_corpus": candidate.population_or_corpus,
        "primary_outcome": candidate.primary_outcome,
        "comparison": candidate.comparison,
        "candidate_contribution": candidate.candidate_contribution,
        "scope_in": candidate.scope_in,
        "scope_out": candidate.scope_out,
    }
    values.update(overrides or {})
    selected_outcome = _infer_primary_outcome(
        str(values["primary_outcome"]),
        str(values["research_question"]),
    )
    selected_comparator = (
        str(candidate.required_baselines[0])
        if candidate.required_baselines
        else _split_comparison(str(values["comparison"]))[1]
    )
    selected_comparison_frame = {
        "research_question": str(values["research_question"]),
        "falsifiable_hypothesis": str(values["hypothesis"]),
        "comparator": selected_comparator,
        "intervention": str(candidate.intervention_or_method),
        "primary_outcome": selected_outcome,
        "unit_of_analysis": str(values["unit_of_analysis"]),
    }
    version = previous.version + 1
    contract = ScopeContractVersion(
        study_id=study_id,
        version=version,
        contract_level="specific_topic",
        parent_direction_id=str(
            previous.field_diff.get("selected_direction_id") or ""
        )
        or None,
        direction=str(values["title"]),
        research_question=str(values["research_question"]),
        scope_in=[str(item) for item in values["scope_in"]],
        scope_out=[str(item) for item in values["scope_out"]],
        candidate_contribution=str(values["candidate_contribution"]),
        unit_of_analysis=str(values["unit_of_analysis"]),
        study_design=str(values["study_design"]),
        population_or_corpus=str(values["population_or_corpus"]),
        primary_outcome=selected_outcome,
        comparison=str(values["comparison"]),
        feasibility_basis=candidate.feasibility_reasons,
        unresolved_conditions=candidate.unresolved_conditions,
        project_resource_ids=[
            item["resource_id"] for item in chosen_resource_candidates
        ],
        literature_set_id=previous.literature_set_id,
        predecessor_version=previous.version,
        field_diff={
            "selected_topic_id": topic_id,
            "selected_hypothesis": str(values["hypothesis"]),
            "comparison_frame": selected_comparison_frame,
            "academic_concepts": list(
                previous.field_diff.get("academic_concepts") or []
            ),
            "operational_definition": str(
                previous.field_diff.get("operational_definition") or ""
            ),
            "selected_resource_candidate_ids": [
                item["candidate_id"] for item in chosen_resource_candidates
            ],
            "overrides": overrides or {},
        },
        created_by=decided_by,
    )
    repository.save_scope_contract(contract)
    gate = repository.create_gate(
        study_id,
        GateType.SCOPE_APPROVAL,
        "scope_contract",
        f"{study_id}:scope-v{version}",
        subject_version=version,
    )
    decided = repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by=decided_by,
        reason=reason or f"Selected Stage 2 topic {topic_id}.",
    )
    frozen = repository.save_scope_contract(
        contract.model_copy(
            update={"status": ArtifactStatus.FROZEN, "frozen_at": utc_now()}
        )
    )
    scope_path = _stage2_dir(repository, study_id) / "scope_contract.json"
    if scope_path.exists():
        raise ValueError("Stage 2 specific Scope artifact is immutable")
    scope_document = {
        **frozen.model_dump(mode="json"),
        "selected_candidate_id": topic_id,
        "title": candidate.title,
        "hypothesis": str(values["hypothesis"]),
        "experimental_unit": str(values["unit_of_analysis"]),
        "target_population": str(values["population_or_corpus"]),
        "comparison": str(values["comparison"]),
        "included_data": candidate.required_data_ids,
        "excluded_data": [],
        "included_methods": candidate.method_card_ids,
        "excluded_methods": [],
        "resource_assumptions": [
            item["resource_id"] for item in chosen_resource_candidates
        ],
        "selected_resource_candidate_ids": [
            item["candidate_id"] for item in chosen_resource_candidates
        ],
        "selected_resource_ids": [
            item["resource_id"] for item in chosen_resource_candidates
        ],
        "blocking_resources": candidate.blocking_resources,
        "known_limitations": [
            *candidate.unresolved_conditions,
            *candidate.major_risks,
        ],
        "compliance_constraints": [candidate.compliance_status],
        "allowed_scope_variations": candidate.alternative_designs,
        "forbidden_scope_changes": [
            "changing the research question after treatment results are viewed",
            "changing the primary metric, threshold, seeds, or data subset after freeze",
            "executing treatment in Stage 2",
        ],
        "freeze_timestamp": frozen.frozen_at,
        "approved_by_user": True,
    }
    write_json_atomic(scope_path, scope_document)
    scope_artifact = repository.register_artifact(
        study_id,
        str(scope_path),
        sha256_file(scope_path),
        kind="stage2_specific_scope_contract",
        role=ArtifactRole.PROTOCOL,
    )
    selection_path = _stage2_dir(repository, study_id) / "resource_selection.json"
    if selection_path.exists():
        raise ValueError("Stage 2 resource selection artifact is immutable")
    resource_selection = {
        "schema_version": 1,
        "selection_id": stable_id(
            "resource-selection",
            study_id,
            version,
            *sorted(
                item["candidate_id"] for item in chosen_resource_candidates
            ),
        ),
        "study_id": study_id,
        "scope_version": version,
        "status": "frozen",
        "selected_candidate_ids": [
            item["candidate_id"] for item in chosen_resource_candidates
        ],
        "selected_resource_ids": [
            item["resource_id"] for item in chosen_resource_candidates
        ],
        "candidates": chosen_resource_candidates,
        "approved_by": decided_by,
        "approved_at": frozen.frozen_at,
        "approval_gate_id": decided.gate_id,
        "acquisition_authorized": False,
        "execution_authorized": False,
        "planned_resource_requirements": [
            {
                "need_type": need_type,
                "strategy_options": [
                    "reuse_local_resource",
                    "acquire_owner_approved_external_resource",
                    "build_new_bounded_experiment_resource",
                ],
                "status": "planned",
            }
            for need_type in planned_resource_types
        ],
        "notes": [
            (
                "Selection binds candidate metadata to the Scope. External "
                "acquisition and package execution require separate authorization."
            )
        ],
    }
    write_json_atomic(selection_path, resource_selection)
    selection_artifact = repository.register_artifact(
        study_id,
        str(selection_path),
        sha256_file(selection_path),
        kind="stage2_resource_selection",
        role=ArtifactRole.PROTOCOL,
    )
    result = {
        "decision": "approved",
        "topic": candidate.model_dump(mode="json"),
        "scope_contract": frozen.model_dump(mode="json"),
        "scope_document": scope_document,
        "gate": decided.model_dump(mode="json"),
        "scope_artifact_id": scope_artifact.artifact_id,
        "resource_selection": resource_selection,
        "resource_selection_artifact_id": selection_artifact.artifact_id,
    }
    result_artifact = repository.save_step_result(
        study_id, selection_step.step_instance_id, result
    )
    for dependency_id in selection_step.depends_on:
        dependency = repository.load_step(study_id, dependency_id)
        for input_artifact_id in dependency.output_artifact_ids:
            repository.add_dependency(
                study_id,
                input_artifact_id,
                result_artifact.artifact_id,
                relation="owner_selects_from",
            )
            repository.add_dependency(
                study_id,
                input_artifact_id,
                scope_artifact.artifact_id,
                relation="owner_selects_from",
            )
            repository.add_dependency(
                study_id,
                input_artifact_id,
                selection_artifact.artifact_id,
                relation="owner_selects_from",
            )
    repository.add_dependency(
        study_id,
        scope_artifact.artifact_id,
        result_artifact.artifact_id,
        relation="records_owner_decision",
    )
    repository.update_step(
        study_id,
        selection_step.step_instance_id,
        ExecutionStatus.SUCCEEDED,
        output_artifact_ids=[
            result_artifact.artifact_id,
            scope_artifact.artifact_id,
            selection_artifact.artifact_id,
        ],
    )
    return result


def approve_stage_two_contract(
    repository: WorkflowRepository,
    study_id: str,
    *,
    decided_by: str = "project_owner",
    reason: str | None = None,
) -> dict[str, Any]:
    assessment_steps = [
        item
        for item in repository.list_steps(study_id)
        if item.step_type == "assess_stage2_gate"
        and item.status is ExecutionStatus.SUCCEEDED
    ]
    assessment_step = (
        max(assessment_steps, key=lambda item: item.updated_at)
        if assessment_steps
        else None
    )
    if assessment_step is None or assessment_step.status.value != "succeeded":
        raise ValueError("Stage 2 Gate assessment is not complete")
    report = repository.load_step_result(
        study_id, assessment_step.step_instance_id
    )["stage2_gate_report"]
    if report["status"] not in {
        Stage2GateStatus.PASS.value,
        Stage2GateStatus.CONDITIONAL_PASS.value,
        Stage2GateStatus.DESIGN_READY.value,
    }:
        raise ValueError("a non-ready Stage 2 Gate cannot be approved")
    contract = repository.latest_research_contract(study_id)
    if contract is None:
        raise ValueError("Research Contract draft is unavailable")
    from .contract_compiler import (
        compile_research_contract,
        dry_run_compiled_contract,
    )

    compiled = compile_research_contract(contract)
    dry_run = dry_run_compiled_contract(compiled)
    if not compiled.compile_passed or not dry_run.passed:
        raise ValueError(
            "Stage 2 Research Contract is not executable enough to approve: "
            + "; ".join(
                [*compiled.blocking_issues, *dry_run.blocking_issues]
            )
        )
    gate = next(
        (
            item
            for item in repository.list_gates(study_id)
            if item.gate_type is GateType.RESEARCH_CONTRACT
            and item.subject_version == contract.version
            and item.status is GateStatus.AWAITING_USER
        ),
        None,
    )
    if gate is None:
        raise ValueError("Research Contract approval Gate is unavailable")
    decided = repository.decide_gate(
        study_id,
        gate.gate_id,
        approve=True,
        decided_by=decided_by,
        reason=reason or "Approved Stage 2 Research Contract.",
    )
    return {
        "gate": decided.model_dump(mode="json"),
        "research_contract": contract.model_dump(mode="json"),
    }


def propose_stage_two_amendment(
    repository: WorkflowRepository,
    study_id: str,
    *,
    reason: str,
    changes: dict[str, Any],
    impact_scope: list[str],
    treatment_results_viewed_before_change: bool,
    requires_rerun: bool,
    exploratory_downgrade: bool,
    requires_owner_reapproval: bool = True,
) -> dict[str, Any]:
    """Append an immutable amendment proposal without changing frozen files."""

    contract = repository.latest_research_contract(study_id)
    if contract is None or contract.status is not ArtifactStatus.FROZEN:
        raise ValueError("protocol amendments require a frozen Research Contract")
    if not changes:
        raise ValueError("protocol amendment changes cannot be empty")
    stage_dir = _stage2_dir(repository, study_id)
    sequence = (
        len(list(stage_dir.glob("protocol_amendment_*.json"))) + 1
    )
    amendment = ProtocolAmendment(
        amendment_id=stable_id(
            "amendment",
            study_id,
            contract.version,
            sequence,
            reason,
            json.dumps(changes, ensure_ascii=False, sort_keys=True),
        ),
        study_id=study_id,
        sequence=sequence,
        research_contract_version=contract.version,
        treatment_results_viewed_before_change=(
            treatment_results_viewed_before_change
        ),
        reason=reason,
        changes=changes,
        impact_scope=impact_scope,
        requires_rerun=requires_rerun,
        exploratory_downgrade=exploratory_downgrade,
        requires_owner_reapproval=requires_owner_reapproval,
    )
    path = stage_dir / f"protocol_amendment_{sequence:03d}.json"
    write_json_atomic(path, amendment)
    artifact = repository.register_artifact(
        study_id,
        str(path),
        sha256_file(path),
        kind="stage2_protocol_amendment",
        role=ArtifactRole.PROTOCOL,
        status=ArtifactStatus.DRAFT,
        version=sequence,
    )
    gate = None
    if requires_owner_reapproval:
        gate = repository.create_gate(
            study_id,
            GateType.RESEARCH_CONTRACT,
            "protocol_amendment",
            amendment.amendment_id,
            subject_version=contract.version + 1,
        )
    return {
        "amendment": amendment.model_dump(mode="json"),
        "artifact": artifact.model_dump(mode="json"),
        "gate": gate.model_dump(mode="json") if gate else None,
        "next_action": (
            "create ResearchContract vNext and successor run"
            if requires_rerun
            else "create ResearchContract vNext before further execution"
        ),
    }


__all__ = [
    "AvailabilityStatus",
    "BlockingLevel",
    "DecisionRules",
    "EvidenceStatus",
    "LicenseStatus",
    "MethodCard",
    "ProtocolAmendment",
    "ResourceCategory",
    "ResourceRecord",
    "Stage2GateReport",
    "Stage2GateStatus",
    "TopicCandidate",
    "TopicStatus",
    "ValidationStatus",
    "approve_stage_two_contract",
    "approve_stage_two_mvp",
    "bind_stage_two_evaluation_dataset",
    "ensure_stage_two_dag",
    "revise_stage_two_protocol",
    "select_stage_two_topic",
    "propose_stage_two_amendment",
    "stage_two_handlers",
]
