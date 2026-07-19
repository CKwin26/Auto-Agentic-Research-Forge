from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stage(StrEnum):
    SCOPING = "scoping"
    PLAN_REVIEW = "plan_review"
    CONTRACT_FROZEN = "contract_frozen"
    BASELINE_PENDING = "baseline_pending"
    BASELINE_VERIFIED = "baseline_verified"
    EXPERIMENT_DESIGN = "experiment_design"
    EXPERIMENT_RUNNING = "experiment_running"
    RESULT_REVIEW = "result_review"
    SYNTHESIS = "synthesis"
    PAUSED = "paused"
    COMPLETED = "completed"


class MacroStage(StrEnum):
    DISCOVERY = "stage_1_discovery"
    PROTOCOL = "stage_2_protocol"
    EXPERIMENTATION = "stage_3_experimentation"
    SYNTHESIS = "stage_4_synthesis"
    PAUSED = "paused"


def macro_stage_for(stage: Stage) -> MacroStage:
    if stage in {Stage.SCOPING, Stage.PLAN_REVIEW}:
        return MacroStage.DISCOVERY
    if stage in {Stage.CONTRACT_FROZEN, Stage.BASELINE_PENDING, Stage.BASELINE_VERIFIED}:
        return MacroStage.PROTOCOL
    if stage in {Stage.EXPERIMENT_DESIGN, Stage.EXPERIMENT_RUNNING, Stage.RESULT_REVIEW}:
        return MacroStage.EXPERIMENTATION
    if stage in {Stage.SYNTHESIS, Stage.COMPLETED}:
        return MacroStage.SYNTHESIS
    return MacroStage.PAUSED


class Direction(StrEnum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class ProjectMeta(StrictModel):
    schema_version: int = 1
    slug: str
    name: str
    idea: str
    created_at: str = Field(default_factory=utc_now)


class ProjectState(StrictModel):
    schema_version: int = 1
    stage: Stage = Stage.SCOPING
    revision: int = 0
    latest_plan_draft: str | None = None
    baseline_run_id: str | None = None
    best_run_id: str | None = None
    latest_proposal_id: str | None = None
    active_run_id: str | None = None
    run_count: int = 0
    updated_at: str = Field(default_factory=utc_now)


class MetricDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    direction: Direction


class ResearchPlanDraft(StrictModel):
    title: str = Field(min_length=3, max_length=200)
    research_question: str = Field(min_length=10, max_length=2000)
    hypothesis: str = Field(min_length=10, max_length=2000)
    novelty_claim: str = Field(min_length=5, max_length=2000)
    scope_in: list[str] = Field(min_length=1, max_length=20)
    scope_out: list[str] = Field(default_factory=list, max_length=20)
    method_outline: list[str] = Field(min_length=1, max_length=20)
    datasets: list[str] = Field(min_length=1, max_length=20)
    metrics: list[MetricDefinition] = Field(min_length=1, max_length=20)
    baseline_definition: str = Field(min_length=5, max_length=2000)
    ablation_axes: list[str] = Field(default_factory=list, max_length=20)
    confounders: list[str] = Field(default_factory=list, max_length=20)
    stop_conditions: list[str] = Field(min_length=1, max_length=20)
    risks: list[str] = Field(default_factory=list, max_length=20)
    clarifying_questions: list[str] = Field(default_factory=list, max_length=10)
    readiness_summary: str = Field(min_length=3, max_length=1000)
    ready_to_freeze: bool


class LiteratureSourceType(StrEnum):
    PAPER = "paper"
    DATASET = "dataset"
    SOFTWARE = "software"
    SPECIFICATION = "specification"
    WEB = "web"


class LiteratureSource(StrictModel):
    schema_version: int = 1
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    source_type: LiteratureSourceType
    title: str = Field(min_length=3, max_length=500)
    authors: list[str] = Field(min_length=1, max_length=100)
    year: Annotated[int, Field(ge=1000, le=3000)] | None = None
    locator: str = Field(min_length=3, max_length=2000)
    notes: str = Field(default="", max_length=5000)
    verified: bool = False
    verification_method: str = Field(min_length=3, max_length=1000)
    origin: Literal["manual", "discovery", "benchmark"] = "manual"
    origin_id: str | None = None
    added_at: str = Field(default_factory=utc_now)


class LiteratureManifest(StrictModel):
    schema_version: int = 1
    frozen_at: str = Field(default_factory=utc_now)
    source_hashes: dict[str, str] = Field(min_length=1)


class LiteratureSearchPlan(StrictModel):
    schema_version: int = 1
    review_question: str = Field(min_length=10, max_length=2000)
    queries: list[str] = Field(min_length=2, max_length=8)
    key_concepts: list[str] = Field(min_length=2, max_length=20)
    inclusion_criteria: list[str] = Field(min_length=1, max_length=20)
    exclusion_criteria: list[str] = Field(min_length=1, max_length=20)
    scope_limitations: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def queries_are_unique(self) -> "LiteratureSearchPlan":
        normalized = [query.strip().casefold() for query in self.queries]
        if len(normalized) != len(set(normalized)):
            raise ValueError("literature search queries must be unique")
        return self


class LiteratureCandidate(StrictModel):
    schema_version: int = 1
    candidate_id: str = Field(pattern=r"^candidate-[a-f0-9]{16}$")
    title: str = Field(min_length=3, max_length=1000)
    authors: list[str] = Field(default_factory=list, max_length=200)
    year: Annotated[int, Field(ge=1000, le=3000)] | None = None
    abstract: str = Field(default="", max_length=20_000)
    venue: str = Field(default="", max_length=1000)
    work_type: str = Field(default="unknown", max_length=100)
    doi: str | None = Field(default=None, max_length=500)
    arxiv_id: str | None = Field(default=None, max_length=100)
    locator: str = Field(min_length=3, max_length=2000)
    citation_count: Annotated[int, Field(ge=0)] = 0
    provider_ids: dict[str, str] = Field(min_length=1)
    matched_queries: list[str] = Field(min_length=1, max_length=20)
    provider_scores: dict[str, float] = Field(default_factory=dict)
    relevance_score: Annotated[float, Field(ge=0.0, le=1.0)]
    verification_status: Literal["cross_provider", "canonical", "unverified"]
    verification_method: str = Field(min_length=3, max_length=2000)
    screening_status: Literal["included", "excluded"]
    screening_reasons: list[str] = Field(min_length=1, max_length=20)


class LiteratureDiscoveryRecord(StrictModel):
    schema_version: int = 1
    discovery_id: str
    created_at: str = Field(default_factory=utc_now)
    search_plan_id: str
    providers_requested: list[str] = Field(min_length=1)
    provider_status: dict[str, str]
    raw_response_hashes: dict[str, str] = Field(min_length=1)
    candidates: list[LiteratureCandidate] = Field(min_length=1)
    included_source_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> "LiteratureDiscoveryRecord":
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("literature discovery candidate IDs must be unique")
        if len(self.included_source_ids) != len(set(self.included_source_ids)):
            raise ValueError("literature discovery source IDs must be unique")
        return self


class LiteratureScreeningDecision(StrictModel):
    candidate_id: str = Field(pattern=r"^candidate-[a-f0-9]{16}$")
    decision: Literal["core", "supporting", "exclude"]
    rationale: str = Field(min_length=10, max_length=1500)
    criteria_matches: list[str] = Field(default_factory=list, max_length=20)
    concerns: list[str] = Field(default_factory=list, max_length=20)


class LiteratureScreeningOutput(StrictModel):
    decisions: list[LiteratureScreeningDecision] = Field(min_length=5, max_length=60)


class LiteratureScreeningRecord(StrictModel):
    schema_version: int = 1
    screening_id: str
    created_at: str = Field(default_factory=utc_now)
    discovery_id: str
    model: str
    evaluated_candidate_ids: list[str] = Field(min_length=5, max_length=60)
    decisions: list[LiteratureScreeningDecision] = Field(min_length=5, max_length=60)
    included_source_ids: list[str] = Field(min_length=5, max_length=50)

    @model_validator(mode="after")
    def decisions_cover_evaluated_candidates(self) -> "LiteratureScreeningRecord":
        evaluated = self.evaluated_candidate_ids
        decided = [decision.candidate_id for decision in self.decisions]
        if len(evaluated) != len(set(evaluated)):
            raise ValueError("evaluated candidate IDs must be unique")
        if len(decided) != len(set(decided)):
            raise ValueError("screening decision candidate IDs must be unique")
        if evaluated != decided:
            raise ValueError(
                "screening decisions must cover every evaluated candidate exactly once and in order"
            )
        if len(self.included_source_ids) != len(set(self.included_source_ids)):
            raise ValueError("screening included source IDs must be unique")
        return self


class RelatedWorkTheme(StrictModel):
    theme_id: str = Field(pattern=r"^theme-[a-z0-9-]{2,60}$")
    label: str = Field(min_length=3, max_length=200)
    summary: str = Field(min_length=10, max_length=3000)
    source_ids: list[str] = Field(min_length=2, max_length=100)

    @model_validator(mode="after")
    def citations_are_distinct(self) -> "RelatedWorkTheme":
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("a related-work theme must cite distinct source IDs")
        return self


class NoveltyCandidate(StrictModel):
    novelty_id: str = Field(pattern=r"^novelty-[a-z0-9-]{2,60}$")
    gap_statement: str = Field(min_length=10, max_length=3000)
    proposed_question: str = Field(min_length=10, max_length=2000)
    differentiator: str = Field(min_length=10, max_length=2000)
    falsification_risk: str = Field(min_length=10, max_length=2000)
    source_ids: list[str] = Field(min_length=2, max_length=100)

    @model_validator(mode="after")
    def citations_are_distinct(self) -> "NoveltyCandidate":
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("a novelty candidate must cite distinct source IDs")
        return self


class LiteratureSynthesis(StrictModel):
    schema_version: int = 1
    review_scope: str = Field(min_length=10, max_length=3000)
    themes: list[RelatedWorkTheme] = Field(min_length=1, max_length=20)
    novelty_candidates: list[NoveltyCandidate] = Field(min_length=1, max_length=10)
    recommended_novelty_id: str
    evidence_limitations: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def recommended_candidate_exists(self) -> "LiteratureSynthesis":
        novelty_ids = [candidate.novelty_id for candidate in self.novelty_candidates]
        theme_ids = [theme.theme_id for theme in self.themes]
        if len(novelty_ids) != len(set(novelty_ids)):
            raise ValueError("novelty candidate IDs must be unique")
        if len(theme_ids) != len(set(theme_ids)):
            raise ValueError("related-work theme IDs must be unique")
        identifiers = set(novelty_ids)
        if self.recommended_novelty_id not in identifiers:
            raise ValueError("recommended_novelty_id must identify one novelty candidate")
        return self


class LiteratureReviewEnvelope(StrictModel):
    schema_version: int = 1
    review_id: str
    created_at: str = Field(default_factory=utc_now)
    discovery_id: str
    model: str
    synthesis: LiteratureSynthesis


class LiteratureApprovalRecord(StrictModel):
    schema_version: int = 1
    approved_at: str = Field(default_factory=utc_now)
    review_id: str
    discovery_id: str
    screening_id: str
    review_hash: str
    screening_hash: str
    selected_novelty_id: str = Field(pattern=r"^novelty-[a-z0-9-]{2,60}$")
    included_source_ids: list[str] = Field(min_length=5, max_length=50)
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def sources_are_unique(self) -> "LiteratureApprovalRecord":
        if len(self.included_source_ids) != len(set(self.included_source_ids)):
            raise ValueError("approved source IDs must be unique")
        return self


class PlanEvidenceBinding(StrictModel):
    schema_version: int = 1
    plan_id: str
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_id: str
    review_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    selected_novelty_id: str = Field(pattern=r"^novelty-[a-z0-9-]{2,60}$")
    source_ids: list[str] = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def sources_are_unique(self) -> "PlanEvidenceBinding":
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("plan evidence source IDs must be unique")
        return self


class Stage1Manifest(StrictModel):
    schema_version: int = 1
    created_at: str = Field(default_factory=utc_now)
    review_id: str
    hashes: dict[str, str] = Field(min_length=4)


class Stage1Audit(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    mode: Literal["scientific", "benchmark"]
    checks: dict[str, bool]
    violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    verified_paper_count: int = 0
    provider_count: int = 0
    candidate_count: int = 0
    included_count: int = 0
    review_id: str | None = None


ScalarValue = str | int | float | bool


class FileReplacement(StrictModel):
    path: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=3, max_length=500)
    content: str = Field(max_length=50_000)


class ParameterOverride(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    value: ScalarValue
    reason: str = Field(min_length=3, max_length=500)


class ExperimentProposal(StrictModel):
    title: str = Field(min_length=3, max_length=160)
    hypothesis: str = Field(min_length=10, max_length=1500)
    rationale: str = Field(min_length=10, max_length=2000)
    expected_observation: str = Field(min_length=5, max_length=1000)
    falsification_condition: str = Field(min_length=5, max_length=1000)
    success_criteria: list[str] = Field(min_length=1, max_length=10)
    file_replacements: list[FileReplacement] = Field(default_factory=list, max_length=8)
    parameters: list[ParameterOverride] = Field(default_factory=list, max_length=50)
    estimated_minutes: Annotated[int, Field(ge=1, le=10_080)]
    risks: list[str] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def require_change(self) -> "ExperimentProposal":
        if not self.file_replacements and not self.parameters:
            raise ValueError("proposal must include a file replacement or parameter override")
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("proposal parameter names must be unique")
        return self

    def parameter_map(self) -> dict[str, ScalarValue]:
        return {parameter.name: parameter.value for parameter in self.parameters}


class ExecutionContract(StrictModel):
    schema_version: int = 1
    configured_by_user: bool = False
    command: list[str] = Field(min_length=2, max_length=40)
    evaluator_command: list[str] | None = Field(default=None, min_length=2, max_length=40)
    primary_metric: str = Field(min_length=1, max_length=100)
    direction: Direction
    required_metrics: list[str] = Field(default_factory=list, max_length=30)
    min_delta: float = 0.0
    timeout_seconds: Annotated[int, Field(ge=1, le=604_800)] = 3600
    max_runs: Annotated[int, Field(ge=1, le=100_000)] = 100
    required_repeats: Annotated[int, Field(ge=1, le=50)] = 1
    max_repeats: Annotated[int, Field(ge=1, le=50)] = 5
    allowed_suffixes: list[str] = Field(
        default_factory=lambda: [".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".md"]
    )

    @field_validator("command", "evaluator_command")
    @classmethod
    def command_must_use_python_placeholder(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if value[0] != "{python}":
            raise ValueError("the first command token must be {python}")
        allowed = {
            "{python}",
            "{experiment_dir}",
            "{evaluator_dir}",
            "{run_dir}",
            "{params_file}",
            "{submission_file}",
            "{metrics_file}",
            "{project_dir}",
        }
        for token in value:
            for fragment in (piece for piece in token.split("}")[:-1] if "{" in piece):
                placeholder = fragment[fragment.rfind("{") :] + "}"
                if placeholder not in allowed:
                    raise ValueError(f"unsupported command placeholder: {placeholder}")
        return value

    @model_validator(mode="after")
    def repeats_are_consistent(self) -> "ExecutionContract":
        if self.required_repeats > self.max_repeats:
            raise ValueError("required_repeats cannot exceed max_repeats")
        required = list(dict.fromkeys([self.primary_metric, *self.required_metrics]))
        self.required_metrics = required
        return self


class FrozenManifest(StrictModel):
    schema_version: int = 1
    frozen_at: str = Field(default_factory=utc_now)
    hashes: dict[str, str]


class ProtectedManifest(StrictModel):
    schema_version: int = 1
    created_at: str = Field(default_factory=utc_now)
    hashes: dict[str, str] = Field(min_length=1)


class ProposalEnvelope(StrictModel):
    schema_version: int = 1
    proposal_id: str
    created_at: str = Field(default_factory=utc_now)
    model: str
    proposal: ExperimentProposal
    validation_errors: list[str] = Field(default_factory=list)
    valid: bool


class TrialResult(StrictModel):
    index: int
    exit_code: int | None
    evaluator_exit_code: int | None = None
    duration_seconds: float
    metrics: dict[str, float] = Field(default_factory=dict)
    valid: bool
    error: str | None = None


class RunRecord(StrictModel):
    schema_version: int = 1
    run_id: str
    proposal_id: str | None = None
    is_baseline: bool = False
    started_at: str
    finished_at: str
    contract_hash: str
    code_hash: str
    runtime: Literal["local", "docker"] = "local"
    isolation_verified: bool = False
    runtime_attestation: dict[str, object] = Field(default_factory=dict)
    parameters: dict[str, ScalarValue] = Field(default_factory=dict)
    trials: list[TrialResult]
    aggregate_metrics: dict[str, float] = Field(default_factory=dict)
    metric_stddev: dict[str, float] = Field(default_factory=dict)
    valid: bool
    verdict: Literal[
        "baseline_verified",
        "candidate_improves",
        "valid_non_improving",
        "invalid",
    ]
    improvement: float | None = None
    error: str | None = None


class EvidenceRecord(StrictModel):
    schema_version: int = 1
    recorded_at: str = Field(default_factory=utc_now)
    run_id: str
    proposal_id: str | None = None
    is_baseline: bool
    valid: bool
    verdict: str
    primary_metric: str
    primary_value: float | None = None
    improvement: float | None = None
    aggregate_metrics: dict[str, float] = Field(default_factory=dict)
    code_hash: str
    contract_hash: str
    runtime: Literal["local", "docker"] = "local"
    isolation_verified: bool = False
    error: str | None = None


class ClaimKind(StrEnum):
    BACKGROUND = "background"
    METHOD = "method"
    RESULT = "result"
    LIMITATION = "limitation"


class PaperClaim(StrictModel):
    schema_version: int = 1
    claim_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    kind: ClaimKind
    statement: str = Field(min_length=5, max_length=3000)
    evidence_run_ids: list[str] = Field(default_factory=list, max_length=100)
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    reported_metrics: dict[str, float] = Field(default_factory=dict)
    reported_improvement: float | None = None

    @model_validator(mode="after")
    def claim_has_required_support(self) -> "PaperClaim":
        if self.kind == ClaimKind.RESULT and not self.evidence_run_ids:
            raise ValueError("result claims require at least one evidence run")
        if self.kind == ClaimKind.RESULT and not self.reported_metrics:
            raise ValueError("result claims require structured reported metrics")
        if self.kind == ClaimKind.BACKGROUND and not self.source_ids:
            raise ValueError("background claims require at least one registered source")
        return self


class SynthesisAudit(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    publication_ready: bool
    checks: dict[str, bool]
    violations: list[str] = Field(default_factory=list)
    publication_blockers: list[str] = Field(default_factory=list)
    claim_count: int = 0
    evidence_run_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    manuscript_hash: str | None = None
    claims_hash: str | None = None


class TermEntry(StrictModel):
    schema_version: int = 1
    term_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,99}$")
    english: str = Field(min_length=1, max_length=200)
    chinese: str = Field(min_length=1, max_length=200)
    sense: str = Field(min_length=1, max_length=500)
    domain: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    discouraged: list[str] = Field(default_factory=list, max_length=30)
    context_keywords: list[str] = Field(default_factory=list, max_length=50)
    first_use: Literal["chinese_english", "chinese_only", "english_only"] = (
        "chinese_english"
    )
    preserve_english: bool = False
    source: str = Field(min_length=1, max_length=500)
    version: str = Field(min_length=1, max_length=50)
    status: Literal["approved"] = "approved"


class TermPack(StrictModel):
    schema_version: int = 1
    pack_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,99}$")
    language: Literal["zh-CN"] = "zh-CN"
    version: str = Field(min_length=1, max_length=50)
    terms: list[TermEntry] = Field(default_factory=list, max_length=10_000)


class TermCandidate(StrictModel):
    english: str = Field(min_length=1, max_length=200)
    suggested_chinese: str = Field(min_length=1, max_length=200)
    sense: str = Field(min_length=1, max_length=500)
    domain: str = Field(min_length=1, max_length=100)
    context: str = Field(min_length=1, max_length=1000)


class TermCandidateBatch(StrictModel):
    candidates: list[TermCandidate] = Field(default_factory=list, max_length=500)


class TermDecision(StrictModel):
    schema_version: int = 1
    decision_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,99}$")
    english: str = Field(min_length=1, max_length=200)
    chinese: str = Field(min_length=1, max_length=200)
    sense: str = Field(min_length=1, max_length=500)
    domain: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    discouraged: list[str] = Field(default_factory=list, max_length=30)
    first_use: Literal["chinese_english", "chinese_only", "english_only"] = (
        "chinese_english"
    )
    preserve_english: bool = False
    status: Literal["approved", "provisional_model_choice"]
    source_entry_id: str | None = Field(default=None, max_length=100)
    source_scope: Literal["project", "bundled", "model"]
    contexts: list[str] = Field(default_factory=list, max_length=20)
    occurrence_count: int = Field(ge=1)
    first_source_offset: int = Field(ge=0)


class TermPlan(StrictModel):
    schema_version: int = 1
    language: Literal["zh-CN"] = "zh-CN"
    source_path: str = Field(min_length=1, max_length=500)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: str = Field(default_factory=utc_now)
    model: str = Field(min_length=1, max_length=200)
    selected_domains: list[str] = Field(default_factory=list, max_length=50)
    termbase_hashes: dict[str, str] = Field(default_factory=dict)
    decisions: list[TermDecision] = Field(default_factory=list, max_length=10_000)


class TermReviewItem(StrictModel):
    decision_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,99}$")
    english: str = Field(min_length=1, max_length=200)
    chinese: str = Field(min_length=1, max_length=200)
    sense: str = Field(min_length=1, max_length=500)
    domain: str = Field(min_length=1, max_length=100)
    context: str = Field(default="", max_length=1000)
    status: Literal["pending", "approved", "rejected"] = "pending"


class TermReview(StrictModel):
    schema_version: int = 1
    language: Literal["zh-CN"] = "zh-CN"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[TermReviewItem] = Field(default_factory=list, max_length=10_000)


class LocalizedBlockDraft(StrictModel):
    block_id: str = Field(pattern=r"^block-[0-9]{4}-[0-9a-f]{8}$")
    localized_markdown: str = Field(min_length=1, max_length=50_000)
    term_ids_used: list[str] = Field(default_factory=list, max_length=500)


class LocalizedBlock(StrictModel):
    schema_version: int = 1
    block_id: str = Field(pattern=r"^block-[0-9]{4}-[0-9a-f]{8}$")
    position: int = Field(ge=0)
    kind: Literal["heading", "paragraph", "list", "table", "code", "other"]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    localized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    localized_markdown: str = Field(min_length=1, max_length=50_000)
    protected_tokens: list[str] = Field(default_factory=list, max_length=1000)
    term_ids: list[str] = Field(default_factory=list, max_length=500)


class LocalizationManifest(StrictModel):
    schema_version: int = 1
    language: Literal["zh-CN"] = "zh-CN"
    source_path: str = Field(min_length=1, max_length=500)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    term_plan_path: str = Field(min_length=1, max_length=500)
    term_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manuscript_path: str = Field(min_length=1, max_length=500)
    manuscript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: str = Field(min_length=1, max_length=200)
    created_at: str = Field(default_factory=utc_now)
    repair_attempted: bool = False
    blocks: list[LocalizedBlock] = Field(min_length=1, max_length=100_000)


class LocalizationAudit(StrictModel):
    schema_version: int = 1
    language: Literal["zh-CN"] = "zh-CN"
    audited_at: str = Field(default_factory=utc_now)
    passed: bool
    checks: dict[str, bool]
    violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    manuscript_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    provisional_term_ids: list[str] = Field(default_factory=list)


class CompletionCertificate(StrictModel):
    schema_version: int = 1
    completed_at: str = Field(default_factory=utc_now)
    publication_ready: bool
    audit_hash: str
    artifact_hashes: dict[str, str] = Field(min_length=3)


class EventRecord(StrictModel):
    schema_version: int = 1
    recorded_at: str = Field(default_factory=utc_now)
    event: str
    from_stage: Stage | None = None
    to_stage: Stage | None = None
    details: dict[str, ScalarValue | None] = Field(default_factory=dict)


class PromotionRecord(StrictModel):
    schema_version: int = 1
    promoted_at: str = Field(default_factory=utc_now)
    run_id: str
    proposal_id: str | None
    previous_code_hash: str
    promoted_code_hash: str
    files: list[str]
