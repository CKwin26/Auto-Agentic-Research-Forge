from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .models import Direction, ScalarValue, StrictModel, utc_now


class BenchmarkTaskSpec(StrictModel):
    schema_version: int = 1
    task_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=10_000)
    source: Literal["rfbench", "airs", "researchgym", "custom"] = "rfbench"
    primary_metric: str = Field(min_length=1, max_length=100)
    metric_description: str = Field(min_length=3, max_length=1000)
    direction: Direction
    baseline_score: float
    target_score: float
    optimal_score: float | None = None
    baseline_parameters: dict[str, ScalarValue] = Field(default_factory=dict, max_length=100)
    parameter_guidance: dict[str, str] = Field(default_factory=dict, max_length=100)
    candidate_parameters: list[dict[str, ScalarValue]] = Field(default_factory=list, max_length=100)
    seeds: list[int] = Field(default_factory=lambda: [0, 1, 2], min_length=1, max_length=20)
    max_iterations: Annotated[int, Field(ge=1, le=100)] = 3
    timeout_seconds: Annotated[int, Field(ge=1, le=604_800)] = 300
    required_repeats: Annotated[int, Field(ge=1, le=20)] = 1
    min_delta: float = 0.0
    reproducibility_tolerance: Annotated[float, Field(ge=0.0)] = 0.0
    starter_entrypoint: str = "run_experiment.py"
    evaluator_entrypoint: str = "evaluate.py"
    compute_tier: Literal["cpu", "small_gpu", "gpu"] = "cpu"
    runnable: bool = True
    setup_instructions: str | None = None

    @field_validator("seeds")
    @classmethod
    def seeds_must_be_unique(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("benchmark seeds must be unique")
        return value

    @model_validator(mode="after")
    def target_must_improve_on_baseline(self) -> "BenchmarkTaskSpec":
        if self.direction == Direction.MAXIMIZE and self.target_score <= self.baseline_score:
            raise ValueError("maximize target_score must exceed baseline_score")
        if self.direction == Direction.MINIMIZE and self.target_score >= self.baseline_score:
            raise ValueError("minimize target_score must be below baseline_score")
        return self


class IntegrityAudit(StrictModel):
    schema_version: int = 1
    project: str
    passed: bool
    checks: dict[str, bool]
    violations: list[str] = Field(default_factory=list)
    evidence_records: int = 0
    run_records: int = 0
    isolation_verified: bool = False


class BenchmarkSeedResult(StrictModel):
    schema_version: int = 1
    seed: int
    project: str
    baseline_score: float | None = None
    best_score: float | None = None
    normalized_gain: float = 0.0
    target_reached: bool = False
    proposal_attempts: int = 0
    valid_proposals: int = 0
    candidate_runs: int = 0
    valid_candidate_runs: int = 0
    valid_submission_rate: float = 0.0
    reproducible: bool = False
    anytime_auc: float = 0.0
    wall_time_seconds: float = 0.0
    integrity: IntegrityAudit
    errors: list[str] = Field(default_factory=list)


class BenchmarkReport(StrictModel):
    schema_version: int = 1
    created_at: str = Field(default_factory=utc_now)
    benchmark_id: str
    task_id: str
    strategy: str
    model: str
    runtime: Literal["local", "docker"] = "local"
    controller: Literal["bounded", "run-loop"] = "bounded"
    requested_iterations: int
    seeds: list[BenchmarkSeedResult]
    integrity_pass_rate: float
    success_at_n: float
    mean_normalized_gain: float
    median_normalized_gain: float
    mean_valid_submission_rate: float
    mean_anytime_auc: float
    total_wall_time_seconds: float
    publishable: bool
    limitations: list[str] = Field(default_factory=list)


class LoopConfig(StrictModel):
    schema_version: int = 1
    strategy: Literal["codex", "grid"] = "codex"
    runtime: Literal["local", "docker"] = "docker"
    iterations: Annotated[int, Field(ge=1, le=100)] = 10
    candidate_pool_size: Annotated[int, Field(ge=1, le=5)] = 3
    proposal_attempts_per_iteration: Annotated[int, Field(ge=1, le=20)] = 6
    patience: Annotated[int, Field(ge=1, le=100)] = 5
    max_invalid_runs: Annotated[int, Field(ge=1, le=100)] = 3
    deduplicate_candidates: bool = True
    failure_diagnosis: bool = True
    auto_promote: bool = True
    stop_at_target: bool = True
    runtime_options: dict[str, ScalarValue | None] = Field(default_factory=dict)
    runtime_capabilities: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def proposal_budget_covers_pool(self) -> "LoopConfig":
        if self.proposal_attempts_per_iteration < self.candidate_pool_size:
            raise ValueError("proposal_attempts_per_iteration must cover candidate_pool_size")
        return self


class PendingLoopCandidate(StrictModel):
    proposal_id: str
    fingerprint: str
    information_score: float
    generated_at_iteration: int
    generated_from_fingerprint: str | None = None


class ResearchLoopState(StrictModel):
    schema_version: int = 1
    loop_id: str
    task_id: str
    seed: int
    config_hash: str
    status: Literal["initialized", "running", "stopped", "completed", "error"] = "initialized"
    completed_iterations: int = 0
    proposal_attempts: int = 0
    invalid_runs: int = 0
    consecutive_non_improving: int = 0
    resumed_count: int = 0
    seen_fingerprints: list[str] = Field(default_factory=list)
    executed_fingerprints: list[str] = Field(default_factory=list)
    executed_proposal_ids: list[str] = Field(default_factory=list)
    invalidated_proposal_ids: list[str] = Field(default_factory=list)
    explored_axes: list[str] = Field(default_factory=list)
    pending_candidates: list[PendingLoopCandidate] = Field(default_factory=list)
    active_proposal_id: str | None = None
    active_fingerprint: str | None = None
    active_information_score: float | None = None
    active_generated_from_fingerprint: str | None = None
    best_score: float | None = None
    stop_reason: str | None = None
    last_diagnosis: dict[str, object] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
