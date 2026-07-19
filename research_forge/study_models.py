from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .models import Direction, StrictModel, utc_now


class StudyArm(StrEnum):
    BASELINE = "baseline"
    TREATMENT = "treatment"


class StudyClaimType(StrEnum):
    LITERATURE = "literature"
    NOVELTY = "novelty"
    EXPERIMENT = "experiment"


class ClaimVerdict(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    ABSTAIN = "abstain"


class ClaimFailureMode(StrEnum):
    MISSING_SOURCE_ID = "missing_source_id"
    WRONG_SOURCE_ID = "wrong_or_misattributed_source_id"
    SOURCE_DOES_NOT_SUPPORT = "source_does_not_support_claim"
    MISSING_RUN_ID = "missing_run_id"
    EXACT_METRIC_MISMATCH = "exact_metric_mismatch"
    MISSING_ARTIFACT = "missing_artifact"
    EXPERIMENT_DOES_NOT_SUPPORT = "experiment_artifact_does_not_support_claim"
    UNSUPPORTED_NOVELTY = "unsupported_novelty_comparison_claim"
    VERIFIER_ABSTENTION = "verifier_abstention"
    GATE_REMOVED = "gate_removed_claim"


class Stage2Cell(StrictModel):
    sequence: Annotated[int, Field(ge=1, le=20_000)]
    cell_id: str = Field(pattern=r"^(baseline|treatment)--[a-z0-9-]{2,80}--seed-[0-9]+$")
    arm: StudyArm
    task_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)]


class Stage2TaskBinding(StrictModel):
    task_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    snapshot_path: str = Field(pattern=r"^stage2/task_packs/[a-z0-9-]{2,80}$")
    task_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_metric: str = Field(min_length=1, max_length=100)
    direction: Direction
    baseline_score: float
    target_score: float
    iteration_cap: Annotated[int, Field(ge=1, le=100)]
    timeout_seconds: Annotated[int, Field(ge=1, le=604_800)]


class Stage2ArmDefinition(StrictModel):
    arm: StudyArm
    initial_finalizer: str = Field(min_length=20, max_length=4000)
    verification_gate: Literal["disabled", "reject_revise_recheck"]
    revision_limit: Annotated[int, Field(ge=0, le=1)]
    removal_after_failed_recheck: bool

    @model_validator(mode="after")
    def gate_semantics_match_arm(self) -> "Stage2ArmDefinition":
        if self.arm == StudyArm.BASELINE:
            if self.verification_gate != "disabled" or self.revision_limit != 0:
                raise ValueError("baseline arm cannot use the verification gate")
            if self.removal_after_failed_recheck:
                raise ValueError("baseline arm cannot remove claims after verifier review")
        else:
            if self.verification_gate != "reject_revise_recheck" or self.revision_limit != 1:
                raise ValueError("treatment arm must use one reject/revise/recheck cycle")
            if not self.removal_after_failed_recheck:
                raise ValueError("treatment arm must remove claims that fail recheck")
        return self


class Stage2RuntimeBinding(StrictModel):
    runtime: Literal["docker"] = "docker"
    image: str = Field(min_length=1, max_length=256)
    image_id: str = Field(min_length=8, max_length=256)
    network: Literal["none"] = "none"
    read_only_rootfs: Literal[True] = True
    capabilities_dropped: Literal[True] = True
    no_new_privileges: Literal[True] = True
    candidate_evaluator_separated: Literal[True] = True
    controlled_environment: Literal[True] = True
    capability_verified: Literal[True] = True
    limits: dict[str, object]
    capabilities: dict[str, object]


class Stage2BackboneManifest(StrictModel):
    schema_version: int = 1
    created_at: str = Field(default_factory=utc_now)
    backend: Literal["codex"] = "codex"
    model: str = Field(pattern=r"^codex:.+")
    codex_sdk_version: str = Field(min_length=1, max_length=100)
    codex_account_type: str = Field(min_length=1, max_length=100)
    codex_plan_type: str = Field(min_length=1, max_length=100)
    controller_run_root: str = Field(min_length=3, max_length=1000)
    plan_id: str
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_id: str
    review_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_novelty_id: str
    prompt_hashes: dict[str, str] = Field(min_length=2)
    controller_hashes: dict[str, str] = Field(min_length=5)
    source_hashes: dict[str, str] = Field(min_length=1)
    task_hashes: dict[str, str] = Field(min_length=3)
    task_order: list[str] = Field(min_length=3, max_length=100)
    seeds: list[int] = Field(min_length=3, max_length=100)
    candidate_pool_size: Literal[1] = 1
    proposal_attempts_per_iteration: Literal[2] = 2
    patience: Literal[1] = 1
    max_invalid_runs: Literal[1] = 1
    deduplicate_candidates: Literal[True] = True
    failure_diagnosis: Literal[True] = True
    runtime: Stage2RuntimeBinding

    @field_validator("task_order", "seeds")
    @classmethod
    def design_axes_are_unique(cls, value: list[object]) -> list[object]:
        if len(value) != len(set(value)):
            raise ValueError("Stage 2 design axes must contain unique values")
        return value


class Stage2ManualAuditPlan(StrictModel):
    total_claims: Annotated[int, Field(ge=48, le=10_000)] = 48
    claims_per_task_arm: Annotated[int, Field(ge=2, le=100)] = 8
    target_unsupported_per_task_arm: Annotated[int, Field(ge=1, le=50)] = 4
    target_non_unsupported_per_task_arm: Annotated[int, Field(ge=1, le=50)] = 4
    independent_auditors: Literal[2] = 2
    adjudication_required: Literal[True] = True
    false_positive_threshold: Literal[0.15] = 0.15

    @model_validator(mode="after")
    def strata_sum_to_sample(self) -> "Stage2ManualAuditPlan":
        if (
            self.target_unsupported_per_task_arm
            + self.target_non_unsupported_per_task_arm
            != self.claims_per_task_arm
        ):
            raise ValueError("manual-audit claim strata must sum to claims_per_task_arm")
        return self


class Stage2Protocol(StrictModel):
    schema_version: int = 1
    protocol_revision: Annotated[int, Field(ge=1, le=100)] = 1
    protocol_id: str = Field(pattern=r"^stage2-[0-9a-f]{12}$")
    frozen_at: str = Field(default_factory=utc_now)
    study_intent: Literal["pilot", "publication"] = "pilot"
    publication_contract_id: str | None = Field(
        default=None,
        pattern=r"^pubexp-[0-9a-f]{16}$",
    )
    pilot_reason: str | None = Field(default=None, min_length=10, max_length=1000)
    title: str = Field(min_length=3, max_length=200)
    research_question: str = Field(min_length=10, max_length=2000)
    hypothesis: str = Field(min_length=10, max_length=2000)
    plan_id: str
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_id: str
    review_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_novelty_id: Literal["novelty-02"]
    backbone_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    arms: list[Stage2ArmDefinition] = Field(min_length=2, max_length=2)
    tasks: list[Stage2TaskBinding] = Field(min_length=3, max_length=100)
    seeds: list[int] = Field(min_length=3, max_length=100)
    cells: list[Stage2Cell] = Field(min_length=18, max_length=20_000)
    primary_metric: Literal["unsupported_claim_rate"] = "unsupported_claim_rate"
    secondary_metrics: list[str] = Field(min_length=8, max_length=30)
    claim_schema_path: Literal["stage2/claim_schema.json"] = "stage2/claim_schema.json"
    protected_evaluator: str = Field(
        default="hybrid_structural_plus_arm_blinded_codex",
        min_length=10,
        max_length=200,
    )
    treatment_gate: Literal["structural_plus_frozen_codex_gate"] = (
        "structural_plus_frozen_codex_gate"
    )
    counterfactual_source: Literal[
        "independent_stochastic_runs", "shared_run_artifact"
    ] = "independent_stochastic_runs"
    branch_order: Literal[
        "blocked_baseline_then_treatment", "pair_randomized"
    ] = "blocked_baseline_then_treatment"
    pair_branch_order: dict[str, Literal["baseline_first", "treatment_first"]] = Field(
        default_factory=dict, max_length=10_000
    )
    pair_execution_concurrency: Annotated[int, Field(ge=1, le=4)] = 1
    independent_calibration_contract: str | None = Field(
        default=None, max_length=1000
    )
    pair_level_table: bool = False
    telemetry_schema: list[str] = Field(default_factory=list, max_length=20)
    bootstrap_resamples: Literal[10_000] = 10_000
    manual_audit: Stage2ManualAuditPlan
    max_completed_cells: Annotated[int, Field(ge=18, le=20_000)] = 18
    integrity_fail_closed: Literal[True] = True
    stop_conditions: list[str] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def exact_factorial_design(self) -> "Stage2Protocol":
        if self.study_intent == "publication" and not self.publication_contract_id:
            raise ValueError("publication Stage 2 protocols must bind a publication contract")
        if self.study_intent == "publication" and self.pilot_reason is not None:
            raise ValueError("publication Stage 2 protocols cannot carry a pilot reason")
        if self.study_intent == "pilot" and self.publication_contract_id is not None:
            raise ValueError("pilot Stage 2 protocols cannot bind a publication contract")
        if self.study_intent == "publication":
            if self.counterfactual_source != "shared_run_artifact":
                raise ValueError("publication Stage 2 protocols require shared-artifact branching")
            if self.branch_order != "pair_randomized":
                raise ValueError("publication Stage 2 protocols require randomized pair branch order")
            expected_pairs = {
                f"{task.task_id}--seed-{seed}"
                for task in self.tasks
                for seed in self.seeds
            }
            if set(self.pair_branch_order) != expected_pairs:
                raise ValueError(
                    "publication Stage 2 protocols require one frozen branch-order assignment per task-seed pair"
                )
            if not self.independent_calibration_contract:
                raise ValueError("publication Stage 2 protocols require an independent calibration contract")
            if not self.pair_level_table:
                raise ValueError("publication Stage 2 protocols require pair-level reporting")
        if len(self.seeds) != len(set(self.seeds)):
            raise ValueError("Stage 2 seeds must be unique")
        if [arm.arm for arm in self.arms] != [StudyArm.BASELINE, StudyArm.TREATMENT]:
            raise ValueError("Stage 2 arms must be ordered baseline then treatment")
        task_ids = [task.task_id for task in self.tasks]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("Stage 2 task IDs must be unique")
        expected = [
            (arm, task_id, seed)
            for arm in (StudyArm.BASELINE, StudyArm.TREATMENT)
            for task_id in task_ids
            for seed in self.seeds
        ]
        actual = [(cell.arm, cell.task_id, cell.seed) for cell in self.cells]
        if actual != expected:
            raise ValueError("Stage 2 cells must be the ordered 2 x task x seed factorial design")
        expected_total = 2 * len(task_ids) * len(self.seeds)
        if [cell.sequence for cell in self.cells] != list(range(1, expected_total + 1)):
            raise ValueError("Stage 2 cell sequence must be contiguous and cover the factorial design")
        if self.max_completed_cells != expected_total:
            raise ValueError("max_completed_cells must equal the factorial cell count")
        expected_manual_claims = 2 * len(task_ids) * self.manual_audit.claims_per_task_arm
        if self.manual_audit.total_claims != expected_manual_claims:
            raise ValueError("manual-audit total_claims must cover every task-arm stratum")
        for cell in self.cells:
            expected_id = f"{cell.arm.value}--{cell.task_id}--seed-{cell.seed}"
            if cell.cell_id != expected_id:
                raise ValueError(f"non-canonical Stage 2 cell ID: {cell.cell_id}")
        return self


class StudyClaim(StrictModel):
    schema_version: int = 1
    claim_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    run_id: str = Field(min_length=3, max_length=160)
    arm: StudyArm
    task_pack: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)]
    claim_type: StudyClaimType
    claim_text: str = Field(min_length=5, max_length=3000)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    experiment_run_id: str | None = Field(default=None, max_length=160)
    metric_values: dict[str, float] = Field(default_factory=dict, max_length=20)
    artifact_paths: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("source_ids", "artifact_paths")
    @classmethod
    def entries_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("claim support entries must be unique")
        return value

    @field_validator("metric_values")
    @classmethod
    def metric_values_are_finite(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(item) for item in value.values()):
            raise ValueError("claim metric values must be finite")
        return value


class StudyFinalizerMetric(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    value: float

    @field_validator("value")
    @classmethod
    def value_is_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("finalizer metric value must be finite")
        return value


class StudyFinalizerClaim(StrictModel):
    claim_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    claim_type: StudyClaimType
    claim_text: str = Field(min_length=5, max_length=3000)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    experiment_run_id: str | None = Field(default=None, max_length=160)
    metric_values: list[StudyFinalizerMetric] = Field(default_factory=list, max_length=20)
    artifact_paths: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("source_ids", "artifact_paths")
    @classmethod
    def support_entries_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("finalizer support entries must be unique")
        return value

    @field_validator("metric_values")
    @classmethod
    def finalizer_metric_names_are_unique(
        cls, value: list[StudyFinalizerMetric]
    ) -> list[StudyFinalizerMetric]:
        unique: dict[str, StudyFinalizerMetric] = {}
        ordered: list[StudyFinalizerMetric] = []
        for item in value:
            previous = unique.get(item.name)
            if previous is None:
                unique[item.name] = item
                ordered.append(item)
                continue
            if not math.isclose(
                previous.value, item.value, rel_tol=1e-12, abs_tol=1e-12
            ):
                raise ValueError(
                    "duplicate finalizer metric names carry conflicting values"
                )
        return ordered


class StudyFinalizerOutput(StrictModel):
    claims: list[StudyFinalizerClaim] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def claim_ids_are_unique(self) -> "StudyFinalizerOutput":
        ids = [claim.claim_id for claim in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("finalizer claim IDs must be unique")
        return self


class StudyClaimRegistry(StrictModel):
    schema_version: int = 1
    registry_id: str = Field(pattern=r"^registry-[0-9a-f]{12}$")
    created_at: str = Field(default_factory=utc_now)
    protocol_id: str
    cell_id: str
    run_id: str
    arm: StudyArm
    task_pack: str
    seed: int
    final_output_text: str = Field(default="", max_length=40_000)
    claims: list[StudyClaim] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def claims_match_registry_identity(self) -> "StudyClaimRegistry":
        ids = [claim.claim_id for claim in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("claim IDs must be unique within a registry")
        for claim in self.claims:
            if (
                claim.run_id != self.run_id
                or claim.arm != self.arm
                or claim.task_pack != self.task_pack
                or claim.seed != self.seed
            ):
                raise ValueError("claim identity does not match registry identity")
        return self


class SemanticClaimJudgment(StrictModel):
    claim_id: str
    verdict: ClaimVerdict
    rationale: str = Field(min_length=5, max_length=1500)
    supporting_source_ids: list[str] = Field(default_factory=list, max_length=20)


class SemanticClaimJudgmentBatch(StrictModel):
    judgments: list[SemanticClaimJudgment] = Field(min_length=1, max_length=40)


class StudyClaimEvaluation(StrictModel):
    claim_id: str
    claim_type: StudyClaimType
    verdict: ClaimVerdict
    failure_modes: list[ClaimFailureMode] = Field(default_factory=list)
    resolved_source_ids: list[str] = Field(default_factory=list)
    resolved_experiment_run_id: str | None = None
    structural_checks: dict[str, bool] = Field(default_factory=dict)
    semantic_rationale: str | None = None


class StudyRegistryEvaluation(StrictModel):
    schema_version: int = 1
    evaluated_at: str = Field(default_factory=utc_now)
    protocol_id: str
    registry_id: str
    cell_id: str
    evaluator: Literal["hybrid_structural_plus_arm_blinded_codex"] = (
        "hybrid_structural_plus_arm_blinded_codex"
    )
    arm_blinded: Literal[True] = True
    claims: list[StudyClaimEvaluation] = Field(default_factory=list)
    unsupported_claim_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    verifier_abstention_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    citation_correctness: Annotated[float | None, Field(default=None, ge=0.0, le=1.0)]
    experiment_detail_error_rate: Annotated[
        float | None, Field(default=None, ge=0.0, le=1.0)
    ]
    evidence_coverage: Annotated[float, Field(ge=0.0, le=1.0)]
    failure_mode_count: dict[ClaimFailureMode, int] = Field(default_factory=dict)
    protocol_valid: bool
    integrity_violations: list[str] = Field(default_factory=list)


class Stage2Audit(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    checks: dict[str, bool]
    violations: list[str] = Field(default_factory=list)
    protocol_id: str | None = None
    planned_cells: int = 0
    baseline_cells_completed: int = 0
    treatment_cells_completed: int = 0
    docker_isolation_frozen: bool = False


class Stage2BaselineAudit(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    complete: bool
    checks: dict[str, bool]
    violations: list[str] = Field(default_factory=list)
    protocol_id: str | None = None
    expected_cells: Annotated[int, Field(ge=9, le=10_000)] = 9
    completed_cells: Annotated[int, Field(ge=0, le=10_000)] = 0
    task_native_scores: dict[str, list[float]] = Field(default_factory=dict)
    total_wall_clock_seconds: Annotated[float, Field(ge=0.0)] = 0.0


class StudyGateTrace(StrictModel):
    schema_version: int = 1
    created_at: str = Field(default_factory=utc_now)
    protocol_id: str
    cell_id: str
    initial_registry_id: str
    gate_input_registry_id: str
    revised_registry_id: str | None = None
    final_registry_id: str
    first_pass: list[SemanticClaimJudgment] = Field(default_factory=list)
    recheck: list[SemanticClaimJudgment] = Field(default_factory=list)
    revised_claim_ids: list[str] = Field(default_factory=list)
    removed_claim_ids: list[str] = Field(default_factory=list)
    retained_claim_ids: list[str] = Field(default_factory=list)
    revision_count: Literal[0, 1]


class Stage2TreatmentAudit(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    complete: bool
    checks: dict[str, bool]
    violations: list[str] = Field(default_factory=list)
    protocol_id: str | None = None
    expected_cells: Annotated[int, Field(ge=9, le=10_000)] = 9
    completed_cells: Annotated[int, Field(ge=0, le=10_000)] = 0
    task_native_scores: dict[str, list[float]] = Field(default_factory=dict)
    initial_claims: Annotated[int, Field(ge=0)] = 0
    final_claims: Annotated[int, Field(ge=0)] = 0
    removed_claims: Annotated[int, Field(ge=0)] = 0
    total_wall_clock_seconds: Annotated[float, Field(ge=0.0)] = 0.0


class Stage2EvaluationAudit(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    complete: bool
    checks: dict[str, bool]
    violations: list[str] = Field(default_factory=list)
    protocol_id: str | None = None
    expected_registries: Annotated[int, Field(ge=18, le=20_000)] = 18
    evaluated_registries: Annotated[int, Field(ge=0, le=20_000)] = 0
    arm_metrics: dict[str, dict[str, float | int | None]] = Field(default_factory=dict)
    paired_effects: dict[str, float | int | None] = Field(default_factory=dict)
    manual_audit_required: bool = True
    primary_analysis_interpretable: bool = False
