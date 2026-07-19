from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, model_validator

from .manuscript_depth import audit_manuscript_depth
from .models import StrictModel
from .storage import read_json, sha256_file, write_json_atomic
from .venue_evidence import (
    OpenAlexSearchClient,
    SimilarPaperEvidence,
    VenueEvidenceBundle,
    collect_venue_evidence,
    normalized_venue_name,
)


REGISTRY_PATH = (
    Path(__file__).resolve().parent
    / "resources"
    / "venues"
    / "ai_research_strict.v2.json"
)
CALIBRATION_MINIMUM = 5


class JournalProfile(StrictModel):
    id: str
    name: str
    venue_type: Literal["journal", "conference"]
    track: str
    organizer: str
    publisher: str
    official_venue_url: str
    scope_url: str
    scope_summary: str
    aliases: list[str] = Field(default_factory=list)
    rankings: dict[str, str] = Field(default_factory=dict)
    scope_terms: dict[str, float]
    required_scope_any: list[str]
    article_types: list[str]
    archival_peer_reviewed: bool
    official_source_verified: bool
    archival_record: str
    whitelist_evidence: list[str]
    submission_model: Literal["rolling", "monthly_batch", "annual_deadline"]
    cycle_label: str | None = None
    cycle_status: Literal["rolling", "open", "closed", "not_announced"]
    abstract_deadline: str | None = None
    submission_deadline: str | None = None
    deadline_url: str | None = None
    selectivity_band: str
    desk_prior: float = Field(ge=0, le=1)
    review_success_prior: float = Field(ge=0, le=1)
    quality_bar: float = Field(ge=0, le=1)
    weights: dict[str, float]
    requires_public_software: bool
    notes: str

    @model_validator(mode="after")
    def validate_weights(self) -> "JournalProfile":
        if not self.weights or abs(sum(self.weights.values()) - 1.0) > 0.001:
            raise ValueError(f"journal weights must sum to one: {self.id}")
        if not self.archival_peer_reviewed or not self.official_source_verified:
            raise ValueError(f"venue is not eligible for the strict whitelist: {self.id}")
        if not self.whitelist_evidence or not self.archival_record:
            raise ValueError(f"venue lacks whitelist evidence: {self.id}")
        if self.venue_type == "conference" and self.submission_model != "annual_deadline":
            raise ValueError(f"conference must use an annual deadline model: {self.id}")
        if self.venue_type == "journal" and self.cycle_status != "rolling":
            raise ValueError(f"journal must be available on a rolling basis: {self.id}")
        return self


class JournalRegistry(StrictModel):
    schema_version: int
    registry_id: str
    policy: str
    calibration_status: str
    source_checked_at: str
    venues: list[JournalProfile]


class ProbabilityInterval(StrictModel):
    low: float = Field(ge=0, le=1)
    center: float = Field(ge=0, le=1)
    high: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self) -> "ProbabilityInterval":
        if not self.low <= self.center <= self.high:
            raise ValueError("probability interval must be ordered")
        return self


class ScientificBlocker(StrictModel):
    code: str
    severity: Literal["critical", "high", "medium"]
    reason: str
    required_action: str
    source: str


class ManuscriptAssessment(StrictModel):
    manuscript_path: str
    manuscript_sha256: str
    title: str
    abstract: str
    paper_type: Literal[
        "empirical_research",
        "methodology",
        "benchmark_evaluation",
        "resource_dataset",
        "position_or_theory",
        "unknown",
    ]
    method_terms: list[str]
    contribution_terms: list[str]
    depth_gate_passed: bool
    total_count: int
    reference_count: int
    reporting_score: float = Field(ge=0, le=1)
    method_rigor_score: float = Field(ge=0, le=1)
    evidence_score: float = Field(ge=0, le=1)
    novelty_score: float = Field(ge=0, le=1)
    reproducibility_score: float = Field(ge=0, le=1)
    software_readiness_score: float = Field(ge=0, le=1)
    maturity_score: float = Field(ge=0, le=1)
    task_count: int = Field(ge=0)
    seed_count: int = Field(ge=0)
    claim_count: int = Field(ge=0)
    human_validation_complete: bool
    primary_analysis_interpretable: bool
    publication_ready: bool
    external_public_repository: bool
    maximum_claim_tier: str
    blockers: list[ScientificBlocker]
    evidence_sources: list[str]


class JournalRecommendation(StrictModel):
    rank: int
    journal_id: str
    journal_name: str
    venue_type: Literal["journal", "conference"]
    track: str
    organizer: str
    publisher: str
    official_venue_url: str
    scope_url: str
    scope_summary: str
    article_types: list[str]
    archival_record: str
    whitelist_evidence: list[str]
    submission_model: str
    cycle_label: str | None
    cycle_status: str
    abstract_deadline: str | None
    submission_deadline: str | None
    current_cycle_eligible: bool
    calibration_status: str
    recommendation_band: Literal["not_ready", "stretch", "target", "conservative"]
    rankings: dict[str, str]
    matched_scope_terms: list[str]
    scope_fit: float = Field(ge=0, le=1)
    semantic_fit: float | None = Field(default=None, ge=0, le=1)
    method_fit: float = Field(ge=0, le=1)
    article_type_fit: float = Field(ge=0, le=1)
    venue_fit_score: float = Field(ge=0, le=1)
    evidence_count: int = Field(ge=0)
    similar_papers: list[SimilarPaperEvidence]
    quality_score: float = Field(ge=0, le=1)
    desk_screen_survival: ProbabilityInterval
    conditional_post_review_success: ProbabilityInterval
    combined_submission_success: ProbabilityInterval
    current_cycle_submission_success: ProbabilityInterval
    after_known_blockers_resolved: ProbabilityInterval
    estimated_uplift: float
    routing_label: Literal[
        "do_not_submit_yet",
        "candidate_after_blockers",
        "stretch_after_major_revision",
        "ready_to_submit",
    ]
    hard_blockers: list[str]
    reasons_for_fit: list[str]
    cautions: list[str]


class JournalRecommendationReport(StrictModel):
    schema_version: int = 1
    generated_at: str
    method_id: str = "research-forge-strict-venue-routing-v2"
    venue_policy: str
    calibration_status: str
    probability_interpretation: str
    registry_id: str
    registry_source_checked_at: str
    registry_warnings: list[str]
    evidence: VenueEvidenceBundle
    assessment: ManuscriptAssessment
    recommendations: list[JournalRecommendation]
    discovered_candidates: list["DiscoveredVenueCandidate"]
    recommended_next_actions: list[str]


class DiscoveredVenueCandidate(StrictModel):
    venue_name: str
    venue_type: Literal["journal", "conference", "unknown"]
    evidence_count: int = Field(ge=1)
    mean_relevance: float = Field(ge=0, le=1)
    top_papers: list[SimilarPaperEvidence]
    status: Literal["needs_official_scope_and_policy_verification"] = (
        "needs_official_scope_and_policy_verification"
    )


_BLOCKER_EXPLANATIONS: dict[str, tuple[str, str]] = {
    "RC-MEASUREMENT-CIRCULARITY": (
        "生成器、干预和主评估器没有完成独立校准。",
        "完成盲态人工校准或真正跨模型家族的校准，并报告一致性和错误结构。",
    ),
    "RC-COUNTERFACTUAL-NONISOLATION": (
        "原始两组没有隔离同一实验产物上的反事实效应。",
        "做前瞻性的同产物分支实验，或采用随机化、交错执行的对照设计。",
    ),
    "RC-CONSTRUCT-UNDERCOVERAGE": (
        "代理指标没有完整测量信息性和科学用途。",
        "冻结并增加主张保留、语义变化、信息性和用途指标。",
    ),
    "RC-MATURITY-TARGET-MISMATCH": (
        "当前证据仍是临时性 pilot，而投稿需要可解释的验证结果。",
        "在进入投稿路由前完成预注册的验证门。",
    ),
    "RC-REPORTING-CONTRACT-GAP": (
        "当前报告契约不足以支撑目标结论层级。",
        "把每个核心结论绑定到冻结分析，并披露全部协议偏差。",
    ),
    "RC-TELEMETRY-SCHEMA-GAP": (
        "执行遥测不足以支撑部分机制层面的结论。",
        "在前瞻性实验中冻结并采集缺失的遥测字段。",
    ),
    "RC-LITERATURE-SCOPE-COUPLING": (
        "新颖性边界依赖于范围有限的冻结文献包。",
        "在提出宽泛新颖性主张前刷新并独立核验领域检索。",
    ),
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(max(value, low), high)


def _round(value: float) -> float:
    return round(_clamp(value), 4)


def _logit(probability: float) -> float:
    p = _clamp(probability, 0.001, 0.999)
    return math.log(p / (1 - p))


def _logistic(value: float) -> float:
    return 1 / (1 + math.exp(-value))


def _interval(center: float, kind: str) -> ProbabilityInterval:
    center = _clamp(center)
    if kind == "desk":
        low, high = center - 0.16, center + 0.14
    elif kind == "review":
        low, high = center - 0.17, center + 0.17
    else:
        low, high = center * 0.42, center * 1.75 + 0.015
    return ProbabilityInterval(low=_round(low), center=_round(center), high=_round(high))


def load_journal_registry(path: str | Path | None = None) -> JournalRegistry:
    registry_path = Path(path or REGISTRY_PATH).resolve()
    return JournalRegistry.model_validate(read_json(registry_path))


def _read_optional(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def _plain_manuscript(text: str) -> str:
    value = re.sub(r"(?m)(?<!\\)%.*$", " ", text)
    value = re.sub(r"\\(?:cite|ref|label|url|path)[a-zA-Z*]*(?:\[[^]]*\])?\{[^{}]*\}", " ", value)
    value = re.sub(r"\\[a-zA-Z@]+\*?(?:\[[^]]*\])?", " ", value)
    value = re.sub(r"[{}\\$&_~^|]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _extract_title(text: str, path: Path) -> str:
    if path.suffix.lower() == ".tex":
        match = re.search(r"\\title\{(?P<title>.*?)\}\s*(?:\\author|\\date|\\begin)", text, re.S)
        if match:
            title = re.sub(r"\\(?:bfseries|thanks)\b", " ", match.group("title"))
            title = re.sub(r"\\\\", ": ", title)
            title = re.sub(r"\{.*?\}", " ", title)
            return re.sub(r"\s+", " ", title).replace("::", ":").strip(" :")[:500]
    match = re.search(r"(?m)^#\s+(.+)$", text)
    return match.group(1).strip()[:500] if match else path.stem


def _extract_abstract(text: str, path: Path) -> str:
    if path.suffix.lower() == ".tex":
        match = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", text, re.S | re.I)
        if match:
            return _plain_manuscript(match.group(1))[:8_000]
    match = re.search(
        r"(?ims)^#{1,6}\s+(?:abstract|summary)\s*$\s*(.*?)(?=^#{1,6}\s+|\Z)",
        text,
    )
    if match:
        return _plain_manuscript(match.group(1))[:8_000]
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    for paragraph in paragraphs:
        plain = _plain_manuscript(paragraph)
        if 80 <= len(plain) <= 4_000 and not plain.startswith("#"):
            return plain
    return ""


def _paper_type(corpus: str) -> str:
    if any(term in corpus for term in ("position paper", "conceptual framework", "formal proof", "theorem")):
        return "position_or_theory"
    if any(term in corpus for term in ("dataset release", "new dataset", "corpus release", "resource paper")):
        return "resource_dataset"
    if any(term in corpus for term in ("benchmark", "evaluation framework", "evaluation protocol", "meta evaluation")):
        return "benchmark_evaluation"
    if any(term in corpus for term in ("we propose a method", "methodology", "algorithm", "system architecture")):
        return "methodology"
    if any(term in corpus for term in ("experiment", "empirical", "randomized", "ablation", "case study")):
        return "empirical_research"
    return "unknown"


def _detected_terms(corpus: str, candidates: tuple[str, ...]) -> list[str]:
    return [term for term in candidates if _contains(corpus, term)]


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _contains(corpus: str, phrase: str) -> bool:
    return _normalized(phrase) in corpus


def _count_protocol_dimensions(protocol: dict[str, Any] | None) -> tuple[int, int]:
    if not protocol:
        return 0, 0
    cells = protocol.get("cells", [])
    if not isinstance(cells, list):
        return 0, 0
    tasks = {str(cell.get("task_id")) for cell in cells if isinstance(cell, dict) and cell.get("task_id") is not None}
    seeds = {str(cell.get("seed")) for cell in cells if isinstance(cell, dict) and cell.get("seed") is not None}
    return len(tasks), len(seeds)


def _project_blockers(project: Path | None) -> tuple[list[ScientificBlocker], str, list[str]]:
    if project is None:
        return [], "unknown", []
    root_path = project / "synthesis" / "root_cause_preflight.json"
    report = _read_optional(root_path)
    if not report:
        return [], "unknown", []
    blockers: list[ScientificBlocker] = []
    for item in report.get("findings", []):
        if not isinstance(item, dict) or item.get("resolved") is True:
            continue
        code = str(item.get("code", "UNSPECIFIED"))
        reason, action = _BLOCKER_EXPLANATIONS.get(
            code,
            (str(item.get("root_cause", "Unresolved scientific defect.")), str(item.get("required_action", "Resolve before submission."))),
        )
        severity = str(item.get("severity", "medium"))
        if severity not in {"critical", "high", "medium"}:
            severity = "medium"
        blockers.append(
            ScientificBlocker(
                code=code,
                severity=severity,
                reason=reason,
                required_action=action,
                source=str(root_path),
            )
        )
    return blockers, str(report.get("maximum_claim_tier", "unknown")), [str(root_path)]


def assess_manuscript(
    manuscript: str | Path,
    *,
    project: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[ManuscriptAssessment, str]:
    source = Path(manuscript).resolve()
    project_path = Path(project).resolve() if project else None
    text = source.read_text(encoding="utf-8", errors="replace")
    title = _extract_title(text, source)
    abstract = _extract_abstract(text, source)
    corpus = _normalized(_plain_manuscript(text))
    depth = audit_manuscript_depth(source)
    evidence_sources = [str(source)]

    protocol = _read_optional(project_path / "stage2" / "protocol.json") if project_path else None
    analysis = _read_optional(project_path / "stage2" / "provisional_analysis.json") if project_path else None
    closed = _read_optional(project_path / "stage2" / "closed_loop_status.json") if project_path else None
    certificate = _read_optional(project_path / "completion_certificate.json") if project_path else None
    blockers, claim_tier, blocker_sources = _project_blockers(project_path)
    evidence_sources.extend(blocker_sources)
    for relative, value in (
        ("stage2/protocol.json", protocol),
        ("stage2/provisional_analysis.json", analysis),
        ("stage2/closed_loop_status.json", closed),
        ("completion_certificate.json", certificate),
    ):
        if value is not None and project_path is not None:
            evidence_sources.append(str(project_path / relative))

    task_count, seed_count = _count_protocol_dimensions(protocol)
    study_evidence = closed.get("study_evidence", {}) if closed else {}
    claim_count = int(study_evidence.get("claim_count", 0) or 0)
    human_complete = bool(
        analysis
        and str(analysis.get("human_validation", "")).lower() in {"complete", "completed", "validated"}
    )
    primary_interpretable = bool(
        (analysis or {}).get("primary_analysis_interpretable") is True
        or (closed or {}).get("primary_analysis_interpretable") is True
    )
    publication_ready = bool(
        (certificate or {}).get("publication_ready") is True
        and not any(item.severity == "critical" for item in blockers)
    )
    repository_pattern = re.compile(r"https?\s*:?\s*//\s*(?:www\s*\.\s*)?(?:github|gitlab)\s*\.\s*(?:com|org)\s*/", re.I)
    availability_section = any(
        token in corpus for token in ("code availability", "data and code availability", "software availability")
    )
    external_repo = bool(availability_section and repository_pattern.search(text))

    sections = depth.sections
    reporting = 0.45
    reporting += 0.22 if depth.passed else 0.05
    reporting += min(depth.reference_count / 20, 1) * 0.10
    reporting += min(sum(item.numeric_tokens for item in sections.values()) / 30, 1) * 0.08
    reporting += min(len(sections) / 7, 1) * 0.10
    reporting += 0.05 if depth.duplicate_paragraph_ratio < 0.03 else 0

    severity_counts = {
        severity: sum(item.severity == severity for item in blockers)
        for severity in ("critical", "high", "medium")
    }
    method = 0.76 - 0.13 * severity_counts["critical"] - 0.07 * severity_counts["high"] - 0.025 * severity_counts["medium"]
    if task_count >= 8:
        method += 0.08
    elif task_count and task_count < 4:
        method -= 0.05

    evidence = 0.30
    evidence += min(task_count / 8, 1) * 0.14
    evidence += min(seed_count / 5, 1) * 0.10
    evidence += min(claim_count / 20, 1) * 0.08
    evidence += 0.20 if human_complete and primary_interpretable else 0
    evidence += 0.07 if depth.reference_count >= 15 else 0

    tier_scores = {
        "internal_proxy_association": 0.40,
        "validated_association": 0.58,
        "bounded_causal_effect": 0.72,
        "generalizable_causal_effect": 0.86,
        "unknown": 0.48,
    }
    novelty = tier_scores.get(claim_tier, 0.48)
    if "novel" in corpus or "contribution" in corpus:
        novelty += 0.04

    reproducibility = 0.25
    if protocol:
        reproducibility += 0.17
    if closed and study_evidence.get("completed_cells"):
        reproducibility += 0.13
    if project_path and (project_path / "stage2" / "frozen_manifest.json").is_file():
        reproducibility += 0.12
    if "sha 256" in corpus or "hash pinned" in corpus or "commit pinned" in corpus:
        reproducibility += 0.08
    if external_repo:
        reproducibility += 0.15

    software = 0.28
    if project_path and (project_path / "stage2" / "backbone_manifest.json").is_file():
        software += 0.15
    if "software" in corpus or "research forge" in corpus:
        software += 0.10
    if external_repo:
        software += 0.38

    maturity = 0.24
    maturity += 0.16 if depth.passed else 0
    maturity += 0.22 if human_complete and primary_interpretable else 0
    maturity += 0.20 if publication_ready else 0
    maturity -= 0.07 * severity_counts["critical"] + 0.03 * severity_counts["high"]

    values: dict[str, Any] = {
        "reporting_score": _round(reporting),
        "method_rigor_score": _round(method),
        "evidence_score": _round(evidence),
        "novelty_score": _round(novelty),
        "reproducibility_score": _round(reproducibility),
        "software_readiness_score": _round(software),
        "maturity_score": _round(maturity),
        "task_count": task_count,
        "seed_count": seed_count,
        "claim_count": claim_count,
        "human_validation_complete": human_complete,
        "primary_analysis_interpretable": primary_interpretable,
        "publication_ready": publication_ready,
        "external_public_repository": external_repo,
        "maximum_claim_tier": claim_tier,
    }
    allowed_overrides = set(values)
    for key, value in (overrides or {}).items():
        if key not in allowed_overrides:
            raise ValueError(f"unsupported assessment override: {key}")
        values[key] = value

    assessment = ManuscriptAssessment(
        manuscript_path=str(source),
        manuscript_sha256=sha256_file(source),
        title=title,
        abstract=abstract,
        paper_type=_paper_type(corpus),
        method_terms=_detected_terms(
            corpus,
            (
                "ablation",
                "benchmark",
                "case study",
                "causal",
                "controlled experiment",
                "deep learning",
                "evaluation framework",
                "human evaluation",
                "language model",
                "machine learning",
                "meta analysis",
                "natural language processing",
                "randomized",
                "representation learning",
                "simulation",
                "statistical test",
                "systematic review",
            ),
        ),
        contribution_terms=_detected_terms(
            corpus,
            (
                "algorithm",
                "benchmark",
                "dataset",
                "evaluation",
                "framework",
                "method",
                "model",
                "resource",
                "software",
                "system",
                "theory",
            ),
        ),
        depth_gate_passed=depth.passed,
        total_count=depth.total_count,
        reference_count=depth.reference_count,
        blockers=blockers,
        evidence_sources=sorted(set(evidence_sources)),
        **values,
    )
    return assessment, corpus


def _history_priors(
    profile: JournalProfile,
    history: dict[str, Any] | None,
) -> tuple[float, float, str]:
    records = [
        item
        for item in (history or {}).get("submissions", [])
        if isinstance(item, dict)
        and (item.get("venue_id") or item.get("journal_id")) == profile.id
    ]
    if len(records) < CALIBRATION_MINIMUM:
        return profile.desk_prior, profile.review_success_prior, "uncalibrated_heuristic_v1"
    desk_passes = sum(bool(item.get("desk_passed")) for item in records)
    accepted = sum(str(item.get("final_outcome", "")).lower() == "accepted" for item in records)
    prior_strength = 5.0
    desk = (profile.desk_prior * prior_strength + desk_passes) / (prior_strength + len(records))
    review_denominator = max(desk_passes, 0)
    review = (
        profile.review_success_prior * prior_strength + accepted
    ) / (prior_strength + review_denominator)
    return desk, review, f"locally_calibrated_v1_n={len(records)}"


def _scope_fit(profile: JournalProfile, corpus: str) -> tuple[float, list[str], bool]:
    matched = [term for term in profile.scope_terms if _contains(corpus, term)]
    total_weight = sum(profile.scope_terms.values()) or 1.0
    matched_weight = sum(profile.scope_terms[item] for item in matched)
    required = any(_contains(corpus, item) for item in profile.required_scope_any)
    score = 0.25 + 0.75 * min(matched_weight / (total_weight * 0.72), 1.0)
    if not required:
        score *= 0.48
    return _round(score), matched, required


def _venue_name_matches(profile: JournalProfile, venue_name: str) -> bool:
    source = normalized_venue_name(venue_name)
    if not source:
        return False
    profile_names = [profile.name, profile.id, *profile.aliases]
    if profile.venue_type == "conference":
        profile_names.append(re.sub(r"-(?:19|20)?\d{2}.*$", "", profile.id))
    normalized = {normalized_venue_name(item) for item in profile_names if item}
    for candidate in normalized:
        if not candidate:
            continue
        if source == candidate:
            return True
        if len(candidate) >= 4 and (candidate in source or source in candidate):
            return True
        source_tokens = set(source.split())
        candidate_tokens = set(candidate.split())
        if candidate_tokens and len(source_tokens & candidate_tokens) / len(candidate_tokens) >= 0.8:
            return True
    return False


def _evidence_by_profile(
    registry: JournalRegistry,
    evidence: VenueEvidenceBundle,
) -> tuple[dict[str, list[SimilarPaperEvidence]], list[DiscoveredVenueCandidate]]:
    grouped: dict[str, list[SimilarPaperEvidence]] = defaultdict(list)
    unmatched: dict[tuple[str, str], list[SimilarPaperEvidence]] = defaultdict(list)
    for paper in evidence.papers:
        matches = [
            profile
            for profile in registry.venues
            if (paper.venue_type == "unknown" or profile.venue_type == paper.venue_type)
            and _venue_name_matches(profile, paper.venue_name)
        ]
        if len(matches) > 1 and paper.venue_type == "conference":
            source_name = normalized_venue_name(paper.venue_name)
            explicit_track_matches = []
            for profile in matches:
                track = normalized_venue_name(profile.track)
                track_tokens = {
                    token
                    for token in track.split()
                    if token not in {"archival", "full", "paper", "track"}
                }
                if track_tokens and track_tokens.issubset(set(source_name.split())):
                    explicit_track_matches.append(profile)
            main_matches = [
                profile
                for profile in matches
                if any(token in profile.track.casefold() for token in ("main", "technical"))
            ]
            matches = explicit_track_matches or main_matches[:1] or matches[:1]
        if matches:
            for profile in matches:
                grouped[profile.id].append(paper)
        else:
            unmatched[(paper.venue_name, paper.venue_type)].append(paper)
    discovered: list[DiscoveredVenueCandidate] = []
    for (venue_name, venue_type), papers in unmatched.items():
        ranked = sorted(papers, key=lambda item: item.relevance_score, reverse=True)
        discovered.append(
            DiscoveredVenueCandidate(
                venue_name=venue_name,
                venue_type=venue_type,
                evidence_count=len(ranked),
                mean_relevance=_round(sum(item.relevance_score for item in ranked[:5]) / min(len(ranked), 5)),
                top_papers=ranked[:5],
            )
        )
    discovered.sort(key=lambda item: (item.evidence_count, item.mean_relevance), reverse=True)
    return grouped, discovered[:15]


def _semantic_fit(papers: list[SimilarPaperEvidence]) -> float | None:
    if not papers:
        return None
    scores = sorted((paper.relevance_score for paper in papers), reverse=True)[:5]
    return _round(sum(scores) / len(scores))


def _method_fit(profile: JournalProfile, assessment: ManuscriptAssessment) -> float:
    if not assessment.method_terms:
        return 0.5
    venue_text = _normalized(
        " ".join(
            [profile.scope_summary, profile.track, *profile.scope_terms.keys(), *profile.article_types]
        )
    )
    matched = sum(_contains(venue_text, term) for term in assessment.method_terms)
    return _round(0.35 + 0.65 * matched / len(assessment.method_terms))


def _article_type_fit(profile: JournalProfile, assessment: ManuscriptAssessment) -> float:
    venue_text = _normalized(" ".join([profile.track, *profile.article_types, profile.scope_summary]))
    expected = {
        "empirical_research": ("research article", "paper", "empirical", "technical"),
        "methodology": ("method", "methodology", "technical", "research article"),
        "benchmark_evaluation": ("evaluation", "benchmark", "dataset", "technical"),
        "resource_dataset": ("resource", "dataset", "data", "corpus"),
        "position_or_theory": ("position", "theory", "theoretical", "research article"),
        "unknown": (),
    }[assessment.paper_type]
    if not expected:
        return 0.5
    matched = sum(_contains(venue_text, term) for term in expected)
    return _round(0.4 + 0.6 * min(matched / 2, 1.0))


def _venue_fit(
    *,
    scope_fit: float,
    semantic_fit: float | None,
    method_fit: float,
    article_type_fit: float,
    quality_fit: float,
) -> float:
    if semantic_fit is None:
        return _round(
            0.46 * scope_fit
            + 0.20 * method_fit
            + 0.14 * article_type_fit
            + 0.20 * quality_fit
        )
    return _round(
        0.27 * scope_fit
        + 0.33 * semantic_fit
        + 0.15 * method_fit
        + 0.10 * article_type_fit
        + 0.15 * quality_fit
    )


def _quality(profile: JournalProfile, assessment: ManuscriptAssessment) -> float:
    feature_map = {
        "method_rigor": assessment.method_rigor_score,
        "evidence": assessment.evidence_score,
        "novelty": assessment.novelty_score,
        "reproducibility": assessment.reproducibility_score,
        "reporting": assessment.reporting_score,
        "maturity": assessment.maturity_score,
        "software_readiness": assessment.software_readiness_score,
    }
    return _round(sum(feature_map[key] * weight for key, weight in profile.weights.items()))


def _resolved_assessment(assessment: ManuscriptAssessment) -> ManuscriptAssessment:
    data = assessment.model_dump(mode="python")
    data.update(
        {
            "method_rigor_score": max(assessment.method_rigor_score, 0.82),
            "evidence_score": max(assessment.evidence_score, 0.80),
            "novelty_score": max(assessment.novelty_score, 0.58),
            "reproducibility_score": max(assessment.reproducibility_score, 0.82),
            "software_readiness_score": max(assessment.software_readiness_score, 0.78),
            "maturity_score": max(assessment.maturity_score, 0.82),
            "task_count": max(assessment.task_count, 8),
            "human_validation_complete": True,
            "primary_analysis_interpretable": True,
            "publication_ready": True,
            "external_public_repository": True,
            "maximum_claim_tier": "validated_association",
            "blockers": [],
        }
    )
    return ManuscriptAssessment.model_validate(data)


def _score_journal(
    profile: JournalProfile,
    assessment: ManuscriptAssessment,
    corpus: str,
    history: dict[str, Any] | None,
    evidence_papers: list[SimilarPaperEvidence],
) -> tuple[JournalRecommendation, str]:
    scope, matched, scope_required = _scope_fit(profile, corpus)
    quality = _quality(profile, assessment)
    semantic = _semantic_fit(evidence_papers)
    method_fit = _method_fit(profile, assessment)
    article_type_fit = _article_type_fit(profile, assessment)
    venue_fit = _venue_fit(
        scope_fit=scope,
        semantic_fit=semantic,
        method_fit=method_fit,
        article_type_fit=article_type_fit,
        quality_fit=quality,
    )
    desk_prior, review_prior, calibration = _history_priors(profile, history)
    critical = sum(item.severity == "critical" for item in assessment.blockers)
    high = sum(item.severity == "high" for item in assessment.blockers)
    desk_center = _logistic(
        _logit(desk_prior)
        + 3.3 * (scope - 0.65)
        + 1.7 * (assessment.reporting_score - 0.70)
        + 1.2 * (assessment.maturity_score - 0.60)
    )
    desk_center *= max(0.40, 1.0 - 0.10 * critical - 0.04 * high)
    review_center = _logistic(_logit(review_prior) + 4.0 * (quality - profile.quality_bar))

    blocker_factor = max(0.25, 1.0 - 0.14 * critical - 0.06 * high)
    hard_blockers = [item.code for item in assessment.blockers if item.severity == "critical"]
    requirement_factor = 1.0
    cautions: list[str] = []
    current_cycle_eligible = profile.venue_type == "journal" or profile.cycle_status == "open"
    if profile.venue_type == "conference" and profile.submission_deadline:
        deadline = date.fromisoformat(profile.submission_deadline)
        if deadline < datetime.now(timezone.utc).date():
            current_cycle_eligible = False
    if profile.venue_type == "conference" and not current_cycle_eligible:
        hard_blockers.append("CURRENT_SUBMISSION_CYCLE_UNAVAILABLE")
        if profile.cycle_status == "closed":
            cautions.append(
                f"{profile.cycle_label or 'Current cycle'} is closed; the fit estimate applies only to a future call with materially similar scope."
            )
        else:
            cautions.append("The next archival conference call has not been officially announced.")
    if not scope_required:
        requirement_factor *= 0.45
        hard_blockers.append("OUT_OF_SCOPE_REQUIRED_TERMS_MISSING")
        cautions.append("No required in-scope topic was detected; do not route without manual scope confirmation.")
    if profile.requires_public_software and not assessment.external_public_repository:
        requirement_factor *= 0.22
        hard_blockers.append("PUBLIC_SOFTWARE_RELEASE_MISSING")
        cautions.append("This venue requires publicly inspectable and reusable software; no manuscript-bound public repository was detected.")
    if not assessment.depth_gate_passed:
        requirement_factor *= 0.55
        hard_blockers.append("MANUSCRIPT_DEPTH_GATE_FAILED")
    if not assessment.primary_analysis_interpretable:
        cautions.append("The primary analysis is explicitly marked uninterpretable until deferred validation is completed.")
    if not assessment.publication_ready:
        cautions.append("The project completion certificate/root-cause gate does not mark this evidence publication-ready.")

    combined_center = desk_center * review_center * blocker_factor * requirement_factor
    combined_center = min(combined_center, desk_prior * review_prior * 1.20)

    resolved = _resolved_assessment(assessment)
    resolved_quality = _quality(profile, resolved)
    resolved_desk = _logistic(
        _logit(desk_prior)
        + 3.3 * (scope - 0.65)
        + 1.7 * (resolved.reporting_score - 0.70)
        + 1.2 * (resolved.maturity_score - 0.60)
    )
    resolved_review = _logistic(_logit(review_prior) + 4.0 * (resolved_quality - profile.quality_bar))
    resolved_center = min(resolved_desk * resolved_review, desk_prior * review_prior * 1.20)

    if hard_blockers or critical or not assessment.publication_ready:
        route = "do_not_submit_yet"
    elif combined_center >= 0.24:
        route = "ready_to_submit"
    elif resolved_center >= 0.20:
        route = "candidate_after_blockers"
    else:
        route = "stretch_after_major_revision"

    if route == "do_not_submit_yet":
        recommendation_band = "not_ready"
    elif venue_fit >= 0.78 and quality >= profile.quality_bar:
        recommendation_band = "conservative"
    elif venue_fit >= 0.64 and quality >= profile.quality_bar - 0.08:
        recommendation_band = "target"
    else:
        recommendation_band = "stretch"

    reasons = [
        f"Detected {len(matched)} weighted scope terms; scope-fit score {scope:.2f}.",
        (
            f"Matched {len(evidence_papers)} similar published papers; semantic-fit score {semantic:.2f}."
            if semantic is not None
            else "No verified similar-paper evidence was available; semantic fit did not contribute to ranking."
        ),
        f"Method fit {method_fit:.2f}; paper-type fit {article_type_fit:.2f}; combined venue-fit {venue_fit:.2f}.",
        f"Venue-weighted evidence/method/maturity score {quality:.2f} against internal bar {profile.quality_bar:.2f}.",
        profile.notes,
    ]
    recommendation = JournalRecommendation(
        rank=0,
        journal_id=profile.id,
        journal_name=profile.name,
        venue_type=profile.venue_type,
        track=profile.track,
        organizer=profile.organizer,
        publisher=profile.publisher,
        official_venue_url=profile.official_venue_url,
        scope_url=profile.scope_url,
        scope_summary=profile.scope_summary,
        article_types=profile.article_types,
        archival_record=profile.archival_record,
        whitelist_evidence=profile.whitelist_evidence,
        submission_model=profile.submission_model,
        cycle_label=profile.cycle_label,
        cycle_status=profile.cycle_status,
        abstract_deadline=profile.abstract_deadline,
        submission_deadline=profile.submission_deadline,
        current_cycle_eligible=current_cycle_eligible,
        calibration_status=calibration,
        recommendation_band=recommendation_band,
        rankings=profile.rankings,
        matched_scope_terms=matched,
        scope_fit=scope,
        semantic_fit=semantic,
        method_fit=method_fit,
        article_type_fit=article_type_fit,
        venue_fit_score=venue_fit,
        evidence_count=len(evidence_papers),
        similar_papers=sorted(
            evidence_papers,
            key=lambda item: item.relevance_score,
            reverse=True,
        )[:5],
        quality_score=quality,
        desk_screen_survival=_interval(desk_center, "desk"),
        conditional_post_review_success=_interval(review_center, "review"),
        combined_submission_success=_interval(combined_center, "combined"),
        current_cycle_submission_success=(
            _interval(combined_center, "combined")
            if current_cycle_eligible
            else ProbabilityInterval(low=0, center=0, high=0)
        ),
        after_known_blockers_resolved=_interval(resolved_center, "combined"),
        estimated_uplift=_round(resolved_center - combined_center),
        routing_label=route,
        hard_blockers=sorted(set(hard_blockers)),
        reasons_for_fit=reasons,
        cautions=cautions,
    )
    return recommendation, calibration


def _registry_warnings(registry: JournalRegistry) -> list[str]:
    warnings: list[str] = []
    checked = date.fromisoformat(registry.source_checked_at)
    age = (datetime.now(timezone.utc).date() - checked).days
    if age > 180:
        warnings.append(f"Venue registry is {age} days old; refresh official pages before routing.")
    today = datetime.now(timezone.utc).date()
    for profile in registry.venues:
        if profile.venue_type != "conference":
            continue
        if profile.cycle_status == "open" and profile.submission_deadline:
            deadline = date.fromisoformat(profile.submission_deadline)
            if deadline < today:
                warnings.append(
                    f"{profile.id} is marked open but its official full-paper deadline has passed; refresh the cycle record."
                )
            elif (deadline - today).days <= 14:
                warnings.append(
                    f"{profile.name} {profile.track} full-paper deadline is in {(deadline - today).days} days ({profile.submission_deadline})."
                )
        if profile.cycle_status == "open" and profile.abstract_deadline:
            abstract_deadline = date.fromisoformat(profile.abstract_deadline)
            if today <= abstract_deadline and (abstract_deadline - today).days <= 14:
                warnings.append(
                    f"{profile.name} abstract deadline is in {(abstract_deadline - today).days} days ({profile.abstract_deadline})."
                )
    return warnings


def _deadline_value(value: object) -> str | None:
    text = str(value or "").strip()
    match = re.match(r"((?:19|20)\d{2}-\d{2}-\d{2})", text)
    return match.group(1) if match else None


def overlay_ccf_deadlines(
    registry: JournalRegistry,
    path: str | Path | None,
) -> tuple[JournalRegistry, list[str]]:
    """Overlay optional ccf-deadlines checkout metadata without widening the whitelist."""

    if path is None:
        return registry, []
    root = Path(path).resolve()
    conference_root = root / "conference" if (root / "conference").is_dir() else root
    if not conference_root.is_dir():
        raise FileNotFoundError(f"ccf-deadlines directory not found: {conference_root}")
    records: list[dict[str, Any]] = []
    for source in sorted((*conference_root.rglob("*.yml"), *conference_root.rglob("*.yaml"))):
        try:
            payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        values = payload if isinstance(payload, list) else [payload]
        records.extend(item for item in values if isinstance(item, dict) and item.get("title"))
    today = datetime.now(timezone.utc).date()
    updated: list[JournalProfile] = []
    matched_count = 0
    for profile in registry.venues:
        if profile.venue_type != "conference":
            updated.append(profile)
            continue
        match = next(
            (item for item in records if _venue_name_matches(profile, str(item.get("title") or ""))),
            None,
        )
        if match is None:
            updated.append(profile)
            continue
        matched_count += 1
        rankings = {str(key): str(value) for key, value in (match.get("rank") or {}).items() if value}
        cycle_candidates: list[dict[str, Any]] = []
        for cycle in match.get("confs") or []:
            if not isinstance(cycle, dict):
                continue
            abstract_deadline: str | None = None
            full_deadlines: list[str] = []
            for timing in cycle.get("timeline") or []:
                if not isinstance(timing, dict):
                    continue
                deadline = _deadline_value(timing.get("deadline"))
                if not deadline:
                    continue
                comment = str(timing.get("comment") or "").casefold()
                if "abstract" in comment:
                    abstract_deadline = deadline
                else:
                    full_deadlines.append(deadline)
            if not full_deadlines:
                direct = _deadline_value(cycle.get("deadline"))
                if direct:
                    full_deadlines.append(direct)
            submission_deadline = min(full_deadlines) if full_deadlines else None
            deadline_date = date.fromisoformat(submission_deadline) if submission_deadline else None
            cycle_candidates.append(
                {
                    "label": str(cycle.get("id") or f"{match.get('title')} {cycle.get('year') or ''}").strip(),
                    "submission_deadline": submission_deadline,
                    "abstract_deadline": abstract_deadline,
                    "deadline_date": deadline_date,
                    "url": str(cycle.get("link") or profile.deadline_url or "") or None,
                    "year": int(cycle.get("year") or 0),
                }
            )
        future = [item for item in cycle_candidates if item["deadline_date"] and item["deadline_date"] >= today]
        chosen = min(future, key=lambda item: item["deadline_date"]) if future else (
            max(cycle_candidates, key=lambda item: (item["year"], item["deadline_date"] or date.min))
            if cycle_candidates
            else None
        )
        changes: dict[str, Any] = {"rankings": {**profile.rankings, **rankings}}
        if chosen:
            changes.update(
                {
                    "cycle_label": chosen["label"],
                    "cycle_status": "open" if chosen in future else (
                        "closed" if chosen["submission_deadline"] else "not_announced"
                    ),
                    "submission_deadline": chosen["submission_deadline"],
                    "abstract_deadline": chosen["abstract_deadline"],
                    "deadline_url": chosen["url"],
                }
            )
        updated.append(profile.model_copy(update=changes))
    warnings = [
        f"Loaded ccf-deadlines overlay from {root}; matched {matched_count} strict-whitelist conferences.",
        "Community deadline metadata is a discovery aid; confirm the displayed date on the linked official CFP before submission.",
    ]
    return registry.model_copy(update={"venues": updated}), warnings


def _recommended_actions(assessment: ManuscriptAssessment) -> list[str]:
    actions = [item.required_action for item in assessment.blockers]
    if not assessment.external_public_repository:
        actions.append("发布匿名、带版本的软件/数据补充材料，并在论文中绑定发布哈希。")
    if assessment.task_count < 8:
        actions.append(f"在冻结协议下，把验证集从 {assessment.task_count} 个任务扩展到至少 8 个异质任务。")
    return list(dict.fromkeys(actions))


def recommend_venues(
    manuscript: str | Path,
    *,
    project: str | Path | None = None,
    registry_path: str | Path | None = None,
    history_path: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
    top: int | None = None,
    venue_types: set[str] | None = None,
    live_evidence: bool = False,
    evidence_cache_path: str | Path | None = None,
    openreview_export: str | Path | None = None,
    local_evidence: str | Path | None = None,
    evidence_from_year: int = 2021,
    evidence_results: int = 50,
    evidence_client: OpenAlexSearchClient | None = None,
    ccf_deadlines_path: str | Path | None = None,
) -> JournalRecommendationReport:
    if top is not None and top < 1:
        raise ValueError("top must be at least one")
    current_year = datetime.now(timezone.utc).year
    if not 1900 <= evidence_from_year <= current_year + 1:
        raise ValueError(f"evidence_from_year must be between 1900 and {current_year + 1}")
    if not 1 <= evidence_results <= 50:
        raise ValueError("evidence_results must be between 1 and 50")
    selected_types = venue_types or {"journal", "conference"}
    unknown_types = selected_types - {"journal", "conference"}
    if unknown_types:
        raise ValueError(f"unsupported venue types: {sorted(unknown_types)}")
    registry = load_journal_registry(registry_path)
    registry, overlay_warnings = overlay_ccf_deadlines(registry, ccf_deadlines_path)
    history = _read_optional(Path(history_path).resolve()) if history_path else None
    assessment, corpus = assess_manuscript(manuscript, project=project, overrides=overrides)
    source = Path(manuscript).resolve()
    if evidence_cache_path is None:
        cache_root = (
            Path(project).resolve() / "synthesis" / "venue_evidence"
            if project
            else source.parent / ".venue_evidence"
        )
        evidence_cache_path = cache_root / f"{source.stem}.openalex.json"
    evidence = collect_venue_evidence(
        manuscript_sha256=assessment.manuscript_sha256,
        title=assessment.title,
        abstract=assessment.abstract,
        cache_path=evidence_cache_path,
        live_openalex=live_evidence,
        from_year=evidence_from_year,
        max_results=evidence_results,
        openreview_export=openreview_export,
        local_evidence=local_evidence,
        client=evidence_client,
    )
    profile_evidence, discovered = _evidence_by_profile(registry, evidence)
    scored: list[JournalRecommendation] = []
    statuses: list[str] = []
    for profile in registry.venues:
        if profile.venue_type not in selected_types:
            continue
        item, status = _score_journal(
            profile,
            assessment,
            corpus,
            history,
            profile_evidence.get(profile.id, []),
        )
        scored.append(item)
        statuses.append(status)
    ranked: list[JournalRecommendation] = []
    for venue_type in ("journal", "conference"):
        group = [item for item in scored if item.venue_type == venue_type]
        if any(item.semantic_fit is not None for item in group):
            group.sort(
                key=lambda item: (
                    item.current_cycle_eligible if venue_type == "conference" else True,
                    {"conservative": 3, "target": 2, "stretch": 1, "not_ready": 0}[
                        item.recommendation_band
                    ],
                    item.venue_fit_score,
                    item.semantic_fit if item.semantic_fit is not None else -1,
                    item.scope_fit,
                ),
                reverse=True,
            )
        else:
            # Without verified similar-paper evidence, retain the conservative
            # scope/maturity router rather than pretending lexical venue fit is
            # a semantic publication match.
            group.sort(
                key=lambda item: (
                    item.current_cycle_eligible if venue_type == "conference" else True,
                    item.combined_submission_success.center,
                    item.scope_fit,
                    item.after_known_blockers_resolved.center,
                ),
                reverse=True,
            )
        for rank, item in enumerate(group, start=1):
            item.rank = rank
        ranked.extend(group[:top] if top is not None else group)
    calibrated = sorted({item for item in statuses if item != "uncalibrated_heuristic_v1"})
    calibration_status = (
        "mixed:" + ",".join(calibrated) + ";others=uncalibrated_heuristic_v1"
        if calibrated
        else "uncalibrated_heuristic_v1"
    )
    return JournalRecommendationReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        venue_policy=registry.policy,
        calibration_status=calibration_status,
        probability_interpretation=(
            "Wide decision-support intervals from explicit Research Forge priors and manuscript/project features; "
            "they are not venue-published acceptance rates, are not used as the primary ranking signal, and are hidden "
            "from the human-readable table until locally calibrated. Centers become locally calibrated only after at "
            f"least {CALIBRATION_MINIMUM} recorded submissions to the same venue."
        ),
        registry_id=registry.registry_id,
        registry_source_checked_at=registry.source_checked_at,
        registry_warnings=[*_registry_warnings(registry), *overlay_warnings],
        evidence=evidence,
        assessment=assessment,
        recommendations=ranked,
        discovered_candidates=discovered,
        recommended_next_actions=_recommended_actions(assessment),
    )


def recommend_journals(
    manuscript: str | Path,
    *,
    project: str | Path | None = None,
    registry_path: str | Path | None = None,
    history_path: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
    top: int | None = None,
    **evidence_options: Any,
) -> JournalRecommendationReport:
    """Backward-compatible journal-only wrapper around strict venue routing."""

    return recommend_venues(
        manuscript,
        project=project,
        registry_path=registry_path,
        history_path=history_path,
        overrides=overrides,
        top=top,
        venue_types={"journal"},
        **evidence_options,
    )


def render_journal_recommendation_markdown(report: JournalRecommendationReport) -> str:
    a = report.assessment
    venue_types = {item.venue_type for item in report.recommendations}
    if venue_types == {"journal"}:
        title = "# Research Forge 正规期刊投稿分析"
    elif venue_types == {"conference"}:
        title = "# Research Forge 正式归档型学术会议投稿分析"
    else:
        title = "# Research Forge 正规期刊与学术会议投稿分析"
    lines = [
        title,
        "",
        f"- 论文：**{a.title}**",
        f"- 稿件 SHA-256：`{a.manuscript_sha256}`",
        f"- 论文类型：`{a.paper_type}`",
        f"- 生成时间：`{report.generated_at}`",
        f"- 校准状态：`{report.calibration_status}`",
        f"- 当前投稿门：**{'通过' if a.publication_ready else '未通过'}**",
        f"- 最高可辩护结论层级：`{a.maximum_claim_tier}`",
        f"- 近邻论文证据：{len(report.evidence.papers)} 篇；来源状态 `{report.evidence.provider_status}`",
        "- 白名单政策：只收录官方来源可核验的成熟同行评审期刊或正式归档型 full-paper conference track；排除 workshop、poster、竞赛提案、非归档 track 和宽泛商业兜底刊。",
        "",
        "> 排名以范围、相似已发表论文、方法、论文类型和稿件成熟度为依据。未校准的内部概率不参与主排名，也不在表格中展示；“保守候选”仍不代表保证录用。",
        "",
    ]

    band_labels = {
        "not_ready": "暂不投稿",
        "stretch": "冲刺",
        "target": "主投",
        "conservative": "保守候选",
    }

    def score(value: float | None) -> str:
        return "无证据" if value is None else f"{value:.0%}"

    def ranks(item: JournalRecommendation) -> str:
        return "；".join(f"{key.upper()} {value}" for key, value in item.rankings.items()) or "—"

    journals = [item for item in report.recommendations if item.venue_type == "journal"]
    conferences = [item for item in report.recommendations if item.venue_type == "conference"]
    if journals:
        lines.extend(
            [
                "## 正规同行评审期刊",
                "",
                "| 排名 | 期刊 / 栏目 | 等级 | 分档 | 综合匹配 | 范围 | 近邻语义 | 方法 | 同刊近邻数 |",
                "|---:|---|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for item in journals:
            lines.append(
                f"| {item.rank} | [{item.journal_name}]({item.scope_url}) / {item.track} | {ranks(item)} | "
                f"{band_labels[item.recommendation_band]} | {item.venue_fit_score:.0%} | {item.scope_fit:.0%} | "
                f"{score(item.semantic_fit)} | {item.method_fit:.0%} | {item.evidence_count} |"
            )
    if conferences:
        lines.extend(
            [
                "",
                "## 正式归档型学术会议",
                "",
                "> 会议排名先检查当前周期是否仍可投稿；已关闭会议仅用于规划下一届，不代表现在可以提交。",
                "",
                "| 排名 | 会议 / Track | 等级 | 周期 | 全文截止 | 分档 | 综合匹配 | 范围 | 近邻语义 | 同会近邻数 |",
                "|---:|---|---|---|---|---|---:|---:|---:|---:|",
            ]
        )
        for item in conferences:
            cycle = f"{item.cycle_label or '待公布'} / {item.cycle_status}"
            deadline_display = item.submission_deadline or (
                "本周期已关闭" if item.cycle_status == "closed" else "待官方公布"
            )
            lines.append(
                f"| {item.rank} | [{item.journal_name}]({item.scope_url}) / {item.track} | {ranks(item)} | {cycle} | "
                f"{deadline_display} | {band_labels[item.recommendation_band]} | {item.venue_fit_score:.0%} | "
                f"{item.scope_fit:.0%} | {score(item.semantic_fit)} | {item.evidence_count} |"
            )
    lines.extend(["", "## 可复核的相似论文依据", ""])
    evidence_items = [item for item in report.recommendations if item.similar_papers]
    if evidence_items:
        for item in evidence_items:
            lines.append(f"### {item.journal_name} / {item.track}")
            lines.append("")
            for paper in item.similar_papers[:3]:
                year = f" ({paper.year})" if paper.year else ""
                lines.append(
                    f"- [{paper.title}]({paper.url}){year}；来源 `{paper.provider}`；相似度 {paper.relevance_score:.3f}。"
                )
            lines.append("")
    else:
        lines.append("- 当前没有与白名单场所匹配的近邻论文证据；结果仅是词汇与项目成熟度初筛。")

    if report.discovered_candidates:
        lines.extend(["", "## 白名单外的待核验候选", ""])
        lines.append(
            "> 这些场所来自相似论文，但尚未完成官方范围、同行评审、归档类型和当前投稿政策核验，因此不能直接作为正式推荐。"
        )
        lines.append("")
        lines.append("| 场所 | 类型 | 近邻数 | 平均相似度 | 状态 |")
        lines.append("|---|---|---:|---:|---|")
        for item in report.discovered_candidates[:10]:
            lines.append(
                f"| {item.venue_name} | {item.venue_type} | {item.evidence_count} | "
                f"{item.mean_relevance:.0%} | 待官方核验 |"
            )

    lines.extend(["", "## 投稿阻塞与注意", ""])
    if a.blockers:
        for blocker in a.blockers:
            lines.append(f"- **{blocker.severity.upper()} · {blocker.code}**：{blocker.reason} 需要：{blocker.required_action}")
    else:
        lines.append("- 未发现项目级根因阻塞；仍需逐项核对目标期刊投稿清单。")
    lines.extend(["", "## 最有价值的下一步", ""])
    lines.extend(f"- {action}" for action in report.recommended_next_actions)
    lines.extend(["", "## 评分依据", ""])
    lines.append(
        f"写作/报告 {a.reporting_score:.2f}；方法严谨度 {a.method_rigor_score:.2f}；"
        f"证据 {a.evidence_score:.2f}；新颖性 {a.novelty_score:.2f}；"
        f"复现性 {a.reproducibility_score:.2f}；软件就绪度 {a.software_readiness_score:.2f}；"
        f"投稿成熟度 {a.maturity_score:.2f}。"
    )
    lines.extend(
        [
            "",
            "近邻证据说明：仅在明确使用 `--live-evidence` 时，系统才会向 OpenAlex 发送标题与摘要；不会发送全文。OpenReview 导出文件在本地计算相似度。",
            "",
            f"Venue 官方资料核对日期：`{report.registry_source_checked_at}`。",
        ]
    )
    for warning in report.registry_warnings:
        lines.append(f"- 警告：{warning}")
    for warning in report.evidence.warnings:
        lines.append(f"- 证据警告：{warning}")
    return "\n".join(lines) + "\n"


def persist_journal_recommendation(
    report: JournalRecommendationReport,
    *,
    json_path: str | Path,
    markdown_path: str | Path,
) -> tuple[Path, Path]:
    json_output = Path(json_path).resolve()
    markdown_output = Path(markdown_path).resolve()
    write_json_atomic(json_output, report)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(render_journal_recommendation_markdown(report), encoding="utf-8", newline="\n")
    return json_output, markdown_output
