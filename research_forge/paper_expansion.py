from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .manuscript_depth import audit_manuscript_depth
from .models import LiteratureSource, LiteratureSourceType, StrictModel, utc_now
from .paper_author_voice import (
    AuthorVoiceProfile,
    SentenceLengthDistribution,
)
from .paper_authoring import (
    EvidenceClaimMap,
    HierarchicalPaperOutline,
    OutlineNode,
    PanelDecision,
    PaperArtifactManifest,
    SubmissionGenreProfile,
    academicize_publication_text,
    build_evidence_claim_map,
    build_paper_artifacts,
    decide_panel,
    load_or_create_submission_genre,
    materialize_artifact_callouts,
    publication_internal_tokens,
    validate_publication_title,
    validate_outline,
)
from .paper_humanize import (
    HumanizationPlan,
    HumanizationSectionDiff,
    HumanizationTrace,
    audit_humanization_integrity,
    snapshot_semantics,
)
from .paper_narrative import PublicationNarrativeContract
from .paper_pipeline import (
    GENERIC_JOURNAL_ARTICLE,
    PaperStructureContract,
    markdown_heading,
    normalize_unstructured_abstract,
    validate_abstract_prose,
    validate_manuscript_narrative,
    validate_markdown_structure,
)
from .paper_typesetting import write_submission_latex
from .paper_venue_policy import GENERIC_JOURNAL_POLICY, get_venue_policy
from .paper_visual_strategy import VisualArgumentPlan, compile_visual_slots
from .storage import read_json, sha256_file, write_json_atomic

# Read-only compatibility exports. New readiness decisions use the selected
# VenuePolicyProfile rather than these module-level defaults.
MIN_VERIFIED_PAPERS = GENERIC_JOURNAL_POLICY.minimum_verified_papers
MIN_NUMERIC_EVIDENCE = GENERIC_JOURNAL_POLICY.minimum_numeric_evidence
_CITATION_RE = re.compile(r"\[([a-z0-9][a-z0-9-]{1,79})\]")
_ARTIFACT_CALLOUT_RE = re.compile(r"\[(FIGURE|TABLE):([a-z0-9-]{3,84})\]")
_NUMBER_TOKEN_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?%?")
_REVIEW_ROLES = ("feynman", "tukey", "shannon", "popper")


class PaperDraftSections(StrictModel):
    """Structured prose returned by the paper-writing agent.

    References and the frozen numeric-evidence table are rendered locally so
    the model cannot invent either one.
    """

    title: str = Field(min_length=5, max_length=500)
    abstract: str = Field(min_length=100, max_length=5_000)
    introduction: str = Field(min_length=300, max_length=30_000)
    related_work: str = Field(min_length=300, max_length=35_000)
    methods: str = Field(min_length=500, max_length=45_000)
    results: str = Field(min_length=400, max_length=35_000)
    discussion: str = Field(min_length=500, max_length=40_000)
    limitations: str = Field(min_length=200, max_length=15_000)
    conclusion: str = Field(min_length=150, max_length=8_000)
    data_availability: str = Field(min_length=30, max_length=3_000)
    ethics_statement: str = Field(min_length=30, max_length=3_000)
    author_contributions: str = Field(min_length=30, max_length=3_000)
    conflict_of_interest: str = Field(min_length=10, max_length=2_000)
    funding: str = Field(min_length=10, max_length=2_000)
    ai_disclosure: str = Field(min_length=30, max_length=3_000)


class ManuscriptSectionRevision(StrictModel):
    """A bounded prose repair that cannot overwrite unrelated sections."""

    # Fixed fields are intentional. Codex strict structured outputs do not
    # accept an arbitrary-key object reliably, and this repair surface is the
    # only one that may add explanatory prose after scientific rereview.
    results: str = Field(min_length=100, max_length=20_000)
    discussion: str = Field(min_length=100, max_length=20_000)
    conclusion: str = Field(min_length=100, max_length=8_000)


def _compact_ai_disclosure_model_mentions(value: str) -> str:
    """Collapse repeated ``model (purpose)`` entries in legacy disclosures."""

    prefix = "AI-assisted tools supported "
    legacy_prefix = "AI-assisted tools were used for "
    active_prefix = prefix if value.startswith(prefix) else legacy_prefix
    if not value.startswith(active_prefix):
        return value
    first_sentence, separator, remainder = value.partition(". ")
    mentions = re.findall(r"([A-Za-z0-9][A-Za-z0-9._:-]*) \(([^)]+)\)", first_sentence)
    if len(mentions) < 2:
        return value
    purposes_by_model: dict[str, set[str]] = {}
    for model_id, purpose in mentions:
        purposes_by_model.setdefault(model_id, set()).add(purpose.strip())
    compact = "; ".join(
        f"{', '.join(sorted(purposes))} using {model_id}"
        for model_id, purposes in sorted(purposes_by_model.items())
    )
    return prefix + compact + (". " + remainder if separator else ".")


def academicize_paper_draft(
    draft: PaperDraftSections,
    *,
    aliases: dict[str, str] | None = None,
    language: Literal["zh", "en"] = "zh",
) -> PaperDraftSections:
    """Return a publication view without exposing audit-layer vocabulary."""

    updates = {
        field_name: academicize_publication_text(
                str(getattr(draft, field_name)),
                aliases=aliases,
                language=language,
            )
        for field_name in type(draft).model_fields
    }
    updates["ai_disclosure"] = _compact_ai_disclosure_model_mentions(
        updates["ai_disclosure"]
    )
    return draft.model_copy(update=updates)


def normalize_reader_facing_governance(
    draft: PaperDraftSections,
) -> PaperDraftSections:
    """Keep internal provenance-state wording out of scientific sections.

    ``frozen`` remains meaningful in Methods and Limitations, where the
    operational control is described.  In the Introduction, Results,
    Discussion, and Conclusion, ``registered`` conveys the same scientific
    constraint without making the paper read like an internal audit report.
    """

    updates: dict[str, str] = {}
    stale_visual_phrases = (
        "callout marks",
        "pending artifact",
        "not supplied for inspection",
    )
    # These words are legitimate in ordinary English, but they are also the
    # exact serialized workflow states rejected by the publication leak
    # audit.  Reader-facing scientific sections should state their meaning,
    # not expose the compact state token.  The immutable audit artifacts keep
    # the original value.
    reader_status_phrases = {
        "qualified": "eligible for the registered analysis",
        "inconclusive": "not resolved by the available evidence",
        "incomplete": "not fully specified",
        "disqualified": "not eligible for the registered analysis",
        "untouched": "unchanged by the analysis",
        "unverifiable": "not verifiable from the available evidence",
    }
    for field_name in (
        "abstract",
        "introduction",
        "results",
        "discussion",
        "conclusion",
    ):
        value = str(getattr(draft, field_name))
        value = re.sub(
            r"\bfrozen\b",
            "registered",
            value,
            flags=re.IGNORECASE,
        )
        value = re.sub(
            r"\bsupplied bindings\b",
            "available evidence",
            value,
            flags=re.IGNORECASE,
        )
        for internal, public in reader_status_phrases.items():
            value = re.sub(
                rf"(?<![A-Za-z0-9_]){re.escape(internal)}(?![A-Za-z0-9_])",
                public,
                value,
                flags=re.IGNORECASE,
            )
        # A generated figure may replace an earlier planned callout.  Any
        # paragraph that still says the visual is pending is production
        # metadata, not a scientific result.  Removing that isolated paragraph
        # is deterministic and leaves numbers, citations, and bound claims
        # untouched.
        paragraphs = re.split(r"\n\s*\n", value)
        value = "\n\n".join(
            paragraph
            for paragraph in paragraphs
            if not any(
                phrase in paragraph.casefold()
                for phrase in stale_visual_phrases
            )
        )
        updates[field_name] = value
    return draft.model_copy(update=updates)


def academicize_paper_outline(
    outline: HierarchicalPaperOutline,
    *,
    aliases: dict[str, str] | None = None,
    language: Literal["zh", "en"] = "zh",
) -> HierarchicalPaperOutline:
    """Create a reader-facing outline while retaining binding identifiers."""

    def transform_node(node: OutlineNode) -> OutlineNode:
        return node.model_copy(
            update={
                "heading": academicize_publication_text(
                    node.heading, aliases=aliases, language=language
                ),
                "purpose": academicize_publication_text(
                    node.purpose, aliases=aliases, language=language
                ),
                "argument": academicize_publication_text(
                    node.argument, aliases=aliases, language=language
                ),
                "children": [
                    transform_node(child) for child in node.children
                ],
            }
        )

    return outline.model_copy(
        update={
            "title": academicize_publication_text(
                outline.title, aliases=aliases, language=language
            ),
            "thesis": academicize_publication_text(
                outline.thesis, aliases=aliases, language=language
            ),
            "abstract_moves": [
                academicize_publication_text(
                    item, aliases=aliases, language=language
                )
                for item in outline.abstract_moves
            ],
            "sections": [
                transform_node(section) for section in outline.sections
            ],
        }
    )


class PaperExpansionPlan(StrictModel):
    schema_version: int = 1
    planned_at: str = Field(default_factory=utc_now)
    track_id: str
    venue_policy_id: str = GENERIC_JOURNAL_POLICY.profile_id
    minimum_verified_papers: int = Field(
        default=GENERIC_JOURNAL_POLICY.minimum_verified_papers, ge=0
    )
    minimum_numeric_evidence: int = Field(
        default=GENERIC_JOURNAL_POLICY.minimum_numeric_evidence, ge=0
    )
    ready: bool
    idea_gate_passed: bool
    evidence_gate_passed: bool
    literature_gate_passed: bool
    verified_paper_count: int = Field(ge=0)
    frozen_literature_manifest_valid: bool
    numeric_evidence_count: int = Field(ge=0)
    diagnostic_owner: Literal[
        "idea_validation",
        "evidence_packaging",
        "literature_grounding",
        "paper_writer",
        "none",
    ]
    blockers: list[str]
    next_action: str
    planned_artifacts: list[str]


class PaperExpansionAudit(StrictModel):
    schema_version: int = 1
    audited_at: str = Field(default_factory=utc_now)
    track_id: str
    passed: bool
    full_manuscript_generated: bool
    manuscript_depth_passed: bool
    genre_compliance_passed: bool
    citation_integrity_passed: bool
    conclusion_binding_passed: bool
    numeric_evidence_binding_passed: bool
    paper_draft_ready: bool
    publication_ready: bool
    diagnostic_owner: Literal[
        "idea_validation",
        "evidence_packaging",
        "literature_grounding",
        "paper_writer",
        "none",
    ]
    checks: dict[str, bool]
    violations: list[str]
    publication_blockers: list[str]


class PaperCompletionCertificate(StrictModel):
    schema_version: int = 1
    completed_at: str = Field(default_factory=utc_now)
    track_id: str
    paper_draft_ready: bool
    publication_ready: bool
    manuscript_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    genre_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_claim_map_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    outline_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    outline_review_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    draft_review_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision_trace_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    prose_polish_trace_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    typeset_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    typesetting_report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    audit_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PaperRevisionTrace(StrictModel):
    """Auditable deterministic edits between the model draft and rendered prose."""

    schema_version: int = 1
    revised_at: str = Field(default_factory=utc_now)
    stage: Literal["revise"] = "revise"
    policy: str = "deterministic_genre_normalization"
    changed_fields: list[str]
    operations: list[str]
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    revised_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scientific_content_change_allowed: bool = False


class PaperProsePolishTrace(StrictModel):
    schema_version: int = 1
    polished_at: str = Field(default_factory=utc_now)
    policy: str = "academic_humanizer_claim_preserving"
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    polished_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    number_multiset_preserved: bool
    citation_multiset_preserved: bool
    artifact_callout_multiset_preserved: bool
    frozen_conclusion_preserved: bool
    accepted: bool
    reverted: bool = False
    violations: list[str]


class PaperTypesettingReport(StrictModel):
    schema_version: int = 1
    rendered_at: str = Field(default_factory=utc_now)
    structure_profile_id: str
    language: Literal["zh", "en"]
    latex_path: str
    latex_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdf_generated: bool
    pdf_path: str | None = None
    pdf_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    compiler: str | None = None
    blocker: str | None = None
    author_metadata_required: bool = True


def _model_sha256(value: StrictModel) -> str:
    payload = json.dumps(
        value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_paper_draft(
    draft: PaperDraftSections,
    *,
    frozen_conclusion: str | None = None,
) -> tuple[PaperDraftSections, PaperRevisionTrace]:
    """Apply only deterministic, claim-preserving genre repairs."""

    normalized_abstract, operations = normalize_unstructured_abstract(draft.abstract)
    # Model copy-editing occasionally duplicates an adjacent scholarly noun
    # (for example, ``study task task``).  Removing only a small allowlist of
    # exact adjacent duplicates is an editorial repair: it cannot change a
    # number, citation, polarity, comparator, or scientific conclusion.
    duplicate_term = re.compile(
        r"(?i)\b(task|study|model|classifier|sample|dataset|method|result|"
        r"evidence|comparison|analysis|evaluation|experiment|protocol|outcome|"
        r"metric|baseline|treatment)\s+\1\b"
    )
    deduplicated_abstract = duplicate_term.sub(r"\1", normalized_abstract)
    if deduplicated_abstract != normalized_abstract:
        operations = (*operations, "collapsed_accidental_adjacent_term_duplicate")
        normalized_abstract = deduplicated_abstract
    update: dict[str, str] = {"abstract": normalized_abstract}
    # The scientific conclusion remains immutable in the evidence map, but it
    # is not pasted verbatim into reader-facing prose.  Claim, number and
    # citation bindings protect authority while the conclusion stays natural.
    revised = draft.model_copy(update=update)
    changed_fields = ["abstract"] if normalized_abstract != draft.abstract.strip() else []
    return revised, PaperRevisionTrace(
        changed_fields=changed_fields,
        operations=list(operations),
        source_sha256=_model_sha256(draft),
        revised_sha256=_model_sha256(revised),
    )


def _json(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _literature_sources(run_dir: Path) -> tuple[list[LiteratureSource], bool, list[str]]:
    source_dir = run_dir / "source_snapshot" / "literature" / "sources"
    manifest_path = run_dir / "source_snapshot" / "literature_manifest.json"
    violations: list[str] = []
    sources: list[LiteratureSource] = []
    if source_dir.is_dir():
        for path in sorted(source_dir.glob("*.json")):
            try:
                source = LiteratureSource.model_validate(read_json(path))
            except Exception as exc:
                violations.append(f"文献来源 {path.name} 格式无效：{exc}")
                continue
            if source.source_type == LiteratureSourceType.PAPER and source.verified:
                sources.append(source)
            else:
                violations.append(
                    f"文献来源 {source.source_id} 不是已核验论文"
                )

    manifest_valid = False
    if not manifest_path.is_file():
        violations.append("缺少冻结文献清单")
    else:
        try:
            manifest = _json(manifest_path)
            expected = manifest.get("source_hashes", {})
            if not isinstance(expected, dict) or not expected:
                raise ValueError("source_hashes is empty")
            actual_ids = {source.source_id for source in sources}
            manifest_ids = {str(source_id) for source_id in expected}
            hashes_match = actual_ids == manifest_ids
            for source_id, digest in expected.items():
                source_path = source_dir / f"{source_id}.json"
                if not source_path.is_file() or sha256_file(source_path) != str(digest):
                    hashes_match = False
            manifest_valid = hashes_match
            if not manifest_valid:
                violations.append(
                    "冻结文献清单未与全部已核验来源文件精确绑定"
                )
        except Exception as exc:
            violations.append(f"冻结文献清单格式无效：{exc}")
    return sources, manifest_valid, violations


def prepare_project_bundle_paper(
    run_dir: str | Path,
    *,
    persist: bool = True,
    venue_policy_id: str = GENERIC_JOURNAL_POLICY.profile_id,
) -> PaperExpansionPlan:
    root = Path(run_dir).resolve()
    venue_policy = get_venue_policy(venue_policy_id)
    plan_path = root / "stage_4_synthesis" / "paper_expansion_plan.json"
    verdict = _json(root / "stage_3_experimentation" / "idea_verdict.json")
    lock = _json(root / "stage_2_protocol" / "protocol_lock.json")
    sources, manifest_valid, literature_violations = _literature_sources(root)
    numeric_count = len(verdict.get("numeric_evidence") or [])

    idea_gate = bool(verdict.get("idea_validated")) and verdict.get("status") in {
        "supported",
        "refuted",
    }
    evidence_gate = bool(
        lock.get("source_mode") == "declared_chain"
        and lock.get("protocol_bound_to_output")
        and verdict.get("protocol_bound_to_output")
        and verdict.get("evidence_maturity") == "prospective_blind"
        and numeric_count >= venue_policy.minimum_numeric_evidence
    )
    literature_gate = (
        manifest_valid
        and len(sources) >= venue_policy.minimum_verified_papers
    )
    blockers: list[str] = []
    if not idea_gate:
        blockers.append(
            "想法尚未得到干净的前瞻验证；完整论文写作保持关闭，避免把写作结果误当成想法成立"
        )
    if not evidence_gate:
        if not bool(lock.get("protocol_bound_to_output")):
            blockers.append("冻结协议与机器结果没有精确绑定")
        if verdict.get("evidence_maturity") != "prospective_blind":
            blockers.append("证据不是冻结后的前瞻盲测")
        if numeric_count < venue_policy.minimum_numeric_evidence:
            blockers.append(
                f"可绑定数值证据只有 {numeric_count} 项；"
                f"{venue_policy.profile_id} 至少需要 "
                f"{venue_policy.minimum_numeric_evidence} 项"
            )
    if not literature_gate:
        blockers.append(
            f"冻结且核验过的论文文献只有 {len(sources)} 条；"
            f"{venue_policy.profile_id} 至少需要 "
            f"{venue_policy.minimum_verified_papers} 条"
        )
        blockers.extend(literature_violations)

    ready = idea_gate and evidence_gate and literature_gate
    if not idea_gate:
        owner = "idea_validation"
        next_action = "回到第三阶段，冻结窄化假设并取得新的独立前瞻证据"
    elif not evidence_gate:
        owner = "evidence_packaging"
        next_action = "补齐协议—结果绑定和可复核数值证据，不改写已经冻结的实验结论"
    elif not literature_gate:
        owner = "literature_grounding"
        next_action = "完成文献检索、来源核验与冻结清单后再启动论文写作代理"
    else:
        owner = "none"
        next_action = "前置门槛已通过，可以启动完整论文补写与独立写作审计"

    planned_at = utc_now()
    if plan_path.is_file():
        try:
            planned_at = PaperExpansionPlan.model_validate(
                read_json(plan_path)
            ).planned_at
        except Exception:
            pass
    plan = PaperExpansionPlan(
        planned_at=planned_at,
        track_id=str(verdict.get("track_id") or lock.get("track_id") or "unknown"),
        venue_policy_id=venue_policy.profile_id,
        minimum_verified_papers=venue_policy.minimum_verified_papers,
        minimum_numeric_evidence=venue_policy.minimum_numeric_evidence,
        ready=ready,
        idea_gate_passed=idea_gate,
        evidence_gate_passed=evidence_gate,
        literature_gate_passed=literature_gate,
        verified_paper_count=len(sources),
        frozen_literature_manifest_valid=manifest_valid,
        numeric_evidence_count=numeric_count,
        diagnostic_owner=owner,  # type: ignore[arg-type]
        blockers=list(dict.fromkeys(blockers)),
        next_action=next_action,
        planned_artifacts=[
            "stage_4_synthesis/submission_genre.json",
            "stage_4_synthesis/evidence_claim_map.json",
            "stage_4_synthesis/paper_outline.initial.json",
            "stage_4_synthesis/paper_outline_review.json",
            "stage_4_synthesis/paper_outline.json",
            "stage_4_synthesis/paper_draft_sections.json",
            "stage_4_synthesis/paper_draft_review.json",
            "stage_4_synthesis/paper_prose_polish_trace.json",
            "stage_4_synthesis/paper_artifact_manifest.json",
            "stage_4_synthesis/full_manuscript.tex",
            "stage_4_synthesis/paper_typesetting_report.json",
            "stage_4_synthesis/paper_revision_trace.json",
            "stage_4_synthesis/full_manuscript.md",
            "stage_4_synthesis/full_manuscript_depth.json",
            "stage_4_synthesis/paper_expansion_audit.json",
            "stage_4_synthesis/paper_completion_certificate.json",
        ],
    )
    if persist:
        write_json_atomic(plan_path, plan)
    return plan


def _references(sources: list[LiteratureSource]) -> str:
    lines = []
    for source in sources:
        authors = ", ".join(
            author
            for author in source.authors
            if author.casefold()
            not in {"unknown author", "author metadata unavailable"}
        )
        year = str(source.year) if source.year is not None else "n.d."
        author_prefix = f"{authors} " if authors else ""
        lines.append(
            f"- [{source.source_id}] {author_prefix}({year}). *{source.title}*. {source.locator}"
        )
    return "\n".join(lines)


def _cited_sources(
    draft: PaperDraftSections, sources: list[LiteratureSource]
) -> list[LiteratureSource]:
    """Return only verified sources actually cited in the manuscript prose."""

    cited_ids: list[str] = []
    for field_name in type(draft).model_fields:
        for source_id in _CITATION_RE.findall(str(getattr(draft, field_name))):
            if source_id not in cited_ids:
                cited_ids.append(source_id)
    source_by_id = {source.source_id: source for source in sources}
    return [source_by_id[source_id] for source_id in cited_ids if source_id in source_by_id]


def _numeric_table(verdict: dict[str, Any]) -> str:
    lines = [
        "### 冻结数值证据",
        "",
        "下表由系统直接从第三阶段判定产物写入，写作代理不能修改字段路径或数值。",
        "",
        "Table: 第三阶段冻结数值证据",
        "| 证据路径 | 冻结值 |",
        "|---|---:|",
    ]
    for item in verdict.get("numeric_evidence") or []:
        label = item.get("label") or item["path"]
        value = item.get("display_value", item["value"])
        lines.append(f"| {label} | {value} |")
    return "\n".join(lines)


def _localized_numeric_table(
    verdict: dict[str, Any], *, language: Literal["zh", "en"]
) -> str:
    if language == "zh":
        return _numeric_table(verdict)
    lines = [
        "### Primary outcome summary",
        "",
        (
            "The table summarizes the prespecified quantitative outcomes used "
            "to evaluate the research question."
        ),
        "",
        "Table: Primary outcome summary",
        "| Measure | Value |",
        "|---|---:|",
    ]
    for item in verdict.get("numeric_evidence") or []:
        label = item.get("label") or item["path"]
        label = re.sub(r"[_./]+", " ", str(label)).strip()
        value = item.get("display_value", item["value"])
        lines.append(f"| {label} | {value} |")
    return "\n".join(lines)


def render_full_manuscript(
    draft: PaperDraftSections,
    *,
    verdict: dict[str, Any],
    sources: list[LiteratureSource],
    artifact_manifest: PaperArtifactManifest | None = None,
    structure_contract: PaperStructureContract = GENERIC_JOURNAL_ARTICLE,
    language: Literal["zh", "en"] = "zh",
) -> str:
    draft, _ = normalize_paper_draft(draft)
    heading = lambda key: markdown_heading(
        structure_contract.section(key), language
    )
    manuscript = (
        f"# {draft.title}\n\n"
        f"{heading('abstract')}\n\n{draft.abstract}\n\n"
        f"{heading('introduction')}\n\n{draft.introduction}\n\n"
        f"{heading('related_work')}\n\n{draft.related_work}\n\n"
        f"{heading('methods')}\n\n{draft.methods}\n\n"
        f"{heading('results')}\n\n{draft.results}\n\n{_localized_numeric_table(verdict, language=language)}\n\n"
        f"{heading('discussion')}\n\n{draft.discussion}\n\n"
        f"{heading('limitations')}\n\n{draft.limitations}\n\n"
        f"{heading('conclusion')}\n\n{draft.conclusion}\n\n"
        f"{heading('data_availability')}\n\n{draft.data_availability}\n\n"
        f"{heading('ethics_statement')}\n\n{draft.ethics_statement}\n\n"
        f"{heading('author_contributions')}\n\n{draft.author_contributions}\n\n"
        f"{heading('conflict_of_interest')}\n\n{draft.conflict_of_interest}\n\n"
        f"{heading('funding')}\n\n{draft.funding}\n\n"
        f"{heading('ai_disclosure')}\n\n{draft.ai_disclosure}\n\n"
        f"{heading('references')}\n\n{_references(_cited_sources(draft, sources))}\n"
    )
    if artifact_manifest is not None:
        manuscript = materialize_artifact_callouts(manuscript, artifact_manifest)
        # Artifact captions are inserted after the prose draft is normalized.
        # Apply the same reader-facing projection here so frozen internal
        # governance terminology cannot leak into the submitted manuscript.
        manuscript = academicize_publication_text(
            manuscript,
            language=language,
        )
    return manuscript


def _audit_full_manuscript(
    root: Path, plan: PaperExpansionPlan, *, persist: bool = True
) -> PaperExpansionAudit:
    manuscript_path = root / "stage_4_synthesis" / "full_manuscript.md"
    verdict = _json(root / "stage_3_experimentation" / "idea_verdict.json")
    sources, manifest_valid, literature_violations = _literature_sources(root)
    checks: dict[str, bool] = {
        "paper_expansion_plan_ready": plan.ready,
        "full_manuscript_present": manuscript_path.is_file(),
        "frozen_literature_manifest_valid": manifest_valid,
        "submission_genre_present": (root / "stage_4_synthesis" / "submission_genre.json").is_file(),
        "evidence_claim_map_present": (root / "stage_4_synthesis" / "evidence_claim_map.json").is_file(),
        "approved_outline_present": (root / "stage_4_synthesis" / "paper_outline.json").is_file(),
        "outline_panel_review_present": (root / "stage_4_synthesis" / "paper_outline_review.json").is_file(),
        "revision_trace_present": (root / "stage_4_synthesis" / "paper_revision_trace.json").is_file(),
        "draft_panel_review_present": (root / "stage_4_synthesis" / "paper_draft_review.json").is_file(),
        "prose_polish_trace_present": (root / "stage_4_synthesis" / "paper_prose_polish_trace.json").is_file(),
        "typeset_source_present": (root / "stage_4_synthesis" / "full_manuscript.tex").is_file(),
        "typesetting_report_present": (root / "stage_4_synthesis" / "paper_typesetting_report.json").is_file(),
    }
    artifact_manifest_path = root / "stage_4_synthesis" / "paper_artifact_manifest.json"
    try:
        artifact_manifest = PaperArtifactManifest.model_validate(read_json(artifact_manifest_path))
        checks["paper_artifacts_ready"] = artifact_manifest.ready
    except Exception:
        checks["paper_artifacts_ready"] = False
    violations: list[str] = []
    if not checks["paper_artifacts_ready"]:
        violations.append(
            "one or more approved figure/table slots could not be rendered from frozen evidence; repair evidence or revise the outline"
        )
    try:
        polish_trace = PaperProsePolishTrace.model_validate(
            read_json(root / "stage_4_synthesis" / "paper_prose_polish_trace.json")
        )
        checks["academic_humanizer_preserved_immutable_content"] = bool(
            polish_trace.accepted or polish_trace.reverted
        )
    except Exception:
        checks["academic_humanizer_preserved_immutable_content"] = False
    if not checks["academic_humanizer_preserved_immutable_content"]:
        violations.append("academic humanizer output was neither integrity-safe nor reverted")
    depth_passed = False
    genre_passed = False
    citation_passed = False
    conclusion_passed = False
    numeric_passed = False
    narrative_passed = False
    if manuscript_path.is_file():
        manuscript = manuscript_path.read_text(encoding="utf-8")
        checks["no_unresolved_artifact_callouts"] = not _ARTIFACT_CALLOUT_RE.search(manuscript)
        if not checks["no_unresolved_artifact_callouts"]:
            violations.append("full manuscript still contains unresolved figure/table callouts")
        structure_violations = validate_markdown_structure(
            manuscript, GENERIC_JOURNAL_ARTICLE
        )
        checks["paper_structure_contract_passed"] = not structure_violations
        genre_violations = [
            item
            for item in structure_violations
            if item.startswith("abstract ") or item.startswith("unstructured abstract")
        ]
        genre_passed = not genre_violations
        checks["abstract_genre_contract_passed"] = genre_passed
        violations.extend(structure_violations)
        allowed = {source.source_id for source in sources}
        cited = set(_CITATION_RE.findall(manuscript))
        related_heading = markdown_heading(
            GENERIC_JOURNAL_ARTICLE.section("related_work"), "zh"
        )
        related_match = re.search(
            rf"(?s)^{re.escape(related_heading)}\s*$\s*(.*?)(?=^##\s+)",
            manuscript,
            re.MULTILINE,
        )
        related = related_match.group(1) if related_match else ""
        related_cited = set(_CITATION_RE.findall(related))
        citation_passed = bool(allowed) and cited <= allowed and allowed <= related_cited
        checks["citations_resolve_to_frozen_sources"] = citation_passed
        if not citation_passed:
            violations.append(
                "稿件引用必须精确解析到冻结来源，且每条冻结论文都必须出现在相关工作中"
            )

        conclusion = str(verdict.get("conclusion") or "")
        conclusion_titles = (
            GENERIC_JOURNAL_ARTICLE.section("conclusion").english_title,
            GENERIC_JOURNAL_ARTICLE.section("conclusion").chinese_title,
        )
        conclusion_match = re.search(
            rf"(?s)^##\s+(?:{'|'.join(re.escape(item) for item in conclusion_titles)})\s*$\s*(.*?)(?=^##\s+|\Z)",
            manuscript,
            re.MULTILINE,
        )
        manuscript_conclusion = conclusion_match.group(1).strip() if conclusion_match else ""
        conclusion_passed = bool(manuscript_conclusion)
        checks["scientific_conclusion_authority_preserved"] = conclusion_passed
        if not conclusion_passed:
            violations.append(
                "the reader-facing conclusion is missing"
            )

        numeric_passed = all(
            str(item.get("display_value", item["value"])) in manuscript
            for item in verdict.get("numeric_evidence") or []
        )
        checks["all_frozen_numeric_evidence_present"] = numeric_passed
        if not numeric_passed:
            violations.append("完整稿修改或遗漏了冻结数值证据")
        draft_path = root / "stage_4_synthesis" / "paper_draft_sections.json"
        narrative_contract_path = (
            root / "stage_4_synthesis" / "publication_narrative_contract.json"
        )
        if not narrative_contract_path.is_file():
            # Read-only compatibility for legacy bundle-paper runs created
            # before Workflow v2 introduced a frozen narrative contract.
            narrative_violations = []
        elif draft_path.is_file():
            try:
                structured_draft = PaperDraftSections.model_validate(read_json(draft_path))
                narrative_violations = validate_manuscript_narrative(
                    {
                        field_name: str(getattr(structured_draft, field_name))
                        for field_name in PaperDraftSections.model_fields
                    }
                )
            except Exception as exc:
                narrative_violations = [f"manuscript narrative audit failed: {exc}"]
        else:
            narrative_violations = [
                "structured manuscript sections are unavailable for narrative audit"
            ]
        narrative_passed = not narrative_violations
        checks["manuscript_narrative_contract_passed"] = narrative_passed
        violations.extend(narrative_violations)
        try:
            depth = audit_manuscript_depth(
                manuscript_path,
                profile="journal-article",
                report_path=root
                / "stage_4_synthesis"
                / "full_manuscript_depth.json",
            )
            depth_passed = depth.passed
            checks["journal_article_depth_passed"] = depth_passed
            if not depth_passed:
                violations.extend(depth.violations)
        except Exception as exc:
            checks["journal_article_depth_passed"] = False
            violations.append(f"完整稿深度审计执行失败：{exc}")
    else:
        checks.update(
            {
                "citations_resolve_to_frozen_sources": False,
                "scientific_conclusion_authority_preserved": False,
                "manuscript_narrative_contract_passed": False,
                "all_frozen_numeric_evidence_present": False,
                "journal_article_depth_passed": False,
                "paper_structure_contract_passed": False,
                "abstract_genre_contract_passed": False,
                "no_unresolved_artifact_callouts": False,
            }
        )
        violations.append("缺少完整论文稿")

    violations.extend(literature_violations)
    paper_ready = bool(
        plan.ready
        and manuscript_path.is_file()
        and depth_passed
        and citation_passed
        and conclusion_passed
        and numeric_passed
        and all(checks.values())
    )
    owner: Literal[
        "idea_validation",
        "evidence_packaging",
        "literature_grounding",
        "paper_writer",
        "none",
    ] = "none" if paper_ready else (plan.diagnostic_owner if not plan.ready else "paper_writer")
    publication_blockers = [
        "尚未完成全文级新颖性复核与独立人工同行评审",
        "尚未完成期刊选择、格式适配、作者确认和投稿批准",
    ]
    audit = PaperExpansionAudit(
        track_id=plan.track_id,
        passed=paper_ready,
        full_manuscript_generated=manuscript_path.is_file(),
        manuscript_depth_passed=depth_passed,
        genre_compliance_passed=genre_passed,
        citation_integrity_passed=citation_passed,
        conclusion_binding_passed=conclusion_passed,
        numeric_evidence_binding_passed=numeric_passed,
        paper_draft_ready=paper_ready,
        publication_ready=False,
        diagnostic_owner=owner,
        checks=checks,
        violations=list(dict.fromkeys(violations)),
        publication_blockers=publication_blockers,
    )
    if persist:
        write_json_atomic(root / "stage_4_synthesis" / "paper_expansion_audit.json", audit)
    return audit


def _compact_prompt_tree(
    value: Any,
    *,
    max_string: int = 1600,
    max_list: int = 120,
    max_dict: int = 160,
) -> Any:
    if isinstance(value, str):
        return value[:max_string]
    if isinstance(value, list):
        return [
            _compact_prompt_tree(
                item,
                max_string=max_string,
                max_list=max_list,
                max_dict=max_dict,
            )
            for item in value[:max_list]
        ]
    if isinstance(value, dict):
        return {
            str(key): _compact_prompt_tree(
                item,
                max_string=max_string,
                max_list=max_list,
                max_dict=max_dict,
            )
            for key, item in list(value.items())[:max_dict]
        }
    return value


def _agent_prompt(
    root: Path,
    sources: list[LiteratureSource],
    *,
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
    outline: HierarchicalPaperOutline,
    narrative_contract: PublicationNarrativeContract | None = None,
    visual_argument_plan: VisualArgumentPlan | None = None,
    structure_contract: PaperStructureContract = GENERIC_JOURNAL_ARTICLE,
) -> str:
    payload = {
        "task": (
            "Write a complete academic manuscript in the requested manuscript "
            "language from the frozen bundle. Return only the requested structured "
            "sections."
        ),
        "scope_contract": _compact_prompt_tree(
            _json(root / "stage_1_discovery" / "scope_contract.json")
        ),
        "protocol_lock": _compact_prompt_tree(
            _json(root / "stage_2_protocol" / "protocol_lock.json")
        ),
        "idea_verdict": _compact_prompt_tree(
            _json(root / "stage_3_experimentation" / "idea_verdict.json")
        ),
        "submission_genre": genre.model_dump(mode="json"),
        "evidence_claim_map": _compact_prompt_tree(
            evidence_claim_map.model_dump(mode="json")
        ),
        "approved_hierarchical_outline": _compact_prompt_tree(
            outline.model_dump(mode="json")
        ),
        "publication_narrative_contract": (
            narrative_contract.model_dump(mode="json")
            if narrative_contract is not None
            else None
        ),
        "visual_argument_plan": (
            visual_argument_plan.model_dump(mode="json")
            if visual_argument_plan is not None
            else None
        ),
        "verified_literature": _compact_prompt_tree(
            [source.model_dump(mode="json") for source in sources]
        ),
        "paper_structure_contract": structure_contract.prompt_contract(genre.language),
        "requirements": {
            "language": "English" if genre.language == "en" else "Chinese",
            "minimum_length": 6000 if genre.language == "en" else 10000,
            "length_unit": "English words" if genre.language == "en" else "Han characters",
            "citation_syntax": "[source_id]",
            "citation_rule": "Use only exact source_id values supplied above. Cite every source in related_work.",
            "evidence_rule": (
                "Do not invent, recompute, strengthen, or suppress any result. "
                "Express the frozen scientific conclusion naturally and preserve "
                "its polarity, scope, and reader-critical quantities; never paste "
                "internal verdict prose merely to satisfy a string check."
            ),
            "section_length_targets": (
                {
                    "abstract": "150-300",
                    "introduction": 720,
                    "related_work": 960,
                    "methods": 1440,
                    "results": 1140,
                    "discussion": 1140,
                    "limitations": 360,
                    "conclusion": 180,
                }
                if genre.language == "en"
                else {
                    "abstract": "250-600",
                    "introduction": 1200,
                    "related_work": 1600,
                    "methods": 2400,
                    "results": 1900,
                    "discussion": 1900,
                    "limitations": 600,
                    "conclusion": 300,
                }
            ),
            "abstract_form": structure_contract.abstract.prompt_contract(),
            "abstract_semantic_moves": (
                [
                    "scientific problem or tension",
                    "bounded comparison design",
                    "principal finding in plain language",
                    "scientific implication",
                    "one calibrated boundary sentence",
                ]
                if genre.language == "en"
                else [
                    "科学问题或研究张力",
                    "有边界的比较设计",
                    "用自然语言表达的核心发现",
                    "发现的科学意义",
                    "一句校准后的适用边界",
                ]
            ),
            "subsections": "Use at least four ### subsections in methods, three in results, and three in discussion.",
            "integrity": "Do not fabricate references, data, authors, venues, statistical tests, or causal claims.",
        },
    }
    rendered = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    if len(rendered) > 90_000:
        payload = _compact_prompt_tree(
            payload,
            max_string=600,
            max_list=60,
            max_dict=100,
        )
        rendered = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
    return rendered


def _outline_prompt(
    *,
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
    sources: list[LiteratureSource],
    narrative_contract: PublicationNarrativeContract | None = None,
    visual_argument_plan: VisualArgumentPlan | None = None,
    structure_contract: PaperStructureContract = GENERIC_JOURNAL_ARTICLE,
) -> str:
    compact_bindings: list[dict[str, Any]] = []
    for binding in evidence_claim_map.bindings[:160]:
        compact_bindings.append(
            {
                "claim_id": binding.claim_id,
                "kind": binding.kind,
                "statement": binding.statement[:2000],
                "allowed_sections": binding.allowed_sections,
                "claim_strength": binding.claim_strength,
                "evidence_status": binding.evidence_status,
                "evidence": [
                    {
                        "evidence_type": pointer.evidence_type,
                        "source_id": pointer.source_id,
                        "path": Path(pointer.path).name,
                        "json_path": pointer.json_path,
                        "sha256": pointer.sha256,
                    }
                    for pointer in binding.evidence[:20]
                ],
            }
        )
    compact_evidence_claim_map = {
        "schema_version": evidence_claim_map.schema_version,
        "track_id": evidence_claim_map.track_id,
        "frozen_conclusion": evidence_claim_map.frozen_conclusion,
        "bindings": compact_bindings,
        "omitted_binding_count": max(
            0, len(evidence_claim_map.bindings) - len(compact_bindings)
        ),
        "verified_source_ids": evidence_claim_map.verified_source_ids,
        "forbidden_moves": evidence_claim_map.forbidden_moves[:50],
        "source_registry_sha256": evidence_claim_map.source_registry_sha256,
    }
    compact_sources = [
        {
            "source_id": source.source_id,
            "source_type": source.source_type,
            "title": source.title,
            "authors": source.authors[:12],
            "year": source.year,
            "locator": source.locator,
            "notes": source.notes[:1200],
            "verified": source.verified,
            "verification_method": source.verification_method[:500],
            "origin": source.origin,
        }
        for source in sources[:80]
    ]
    payload = {
        "task": "Create the evidence-bound hierarchical outline before any manuscript prose is drafted.",
        "submission_genre": genre.model_dump(mode="json"),
        "paper_structure_contract": structure_contract.prompt_contract("zh"),
        "evidence_claim_map": compact_evidence_claim_map,
        "verified_literature": compact_sources,
        "omitted_verified_literature_count": max(0, len(sources) - len(compact_sources)),
        "publication_narrative_contract": (
            narrative_contract.model_dump(mode="json")
            if narrative_contract is not None
            else None
        ),
        "visual_argument_plan": (
            visual_argument_plan.model_dump(mode="json")
            if visual_argument_plan is not None
            else None
        ),
        "slot_policy": {
            "draft_must_leave_slots_visible": True,
            "figures_and_tables_are_rendered_after_draft_revision": True,
            "slot_data_must_come_only_from_frozen_evidence": True,
            "missing_data_action": "rollback_to_evidence_packaging; never invent or estimate",
        },
        "publication_terminology_policy": {
            "reader_facing_title": (
                "Name the scientific problem, population, intervention role, "
                "outcome, or study design. Never copy snake_case fields, file "
                "names, task IDs, versioned pipeline labels, metric keys, or "
                "internal arm identifiers into the title."
            ),
            "reader_facing_prose": (
                "Use stable scholarly aliases such as candidate method, "
                "reference method, and primary outcome. Exact implementation "
                "identifiers belong only to the non-rendered reproducibility "
                "mapping, not the title, abstract, headings, introduction, "
                "discussion, or conclusion."
            ),
            "no_semantic_inference": (
                "Do not infer a mechanism from an identifier. If its meaning is "
                "not bound, describe only its experimental role."
            ),
        },
    }
    rendered = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    if len(rendered) > 90_000:
        # Outline generation needs the frozen conclusion and the claim/evidence
        # index, not full prose-sized notes or repeated pointer metadata.
        for source in compact_sources:
            source["notes"] = source["notes"][:240]
            source["verification_method"] = source["verification_method"][:120]
        for binding in compact_bindings:
            binding["statement"] = binding["statement"][:800]
            binding["evidence"] = binding["evidence"][:8]
        rendered = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
    if len(rendered) > 90_000:
        payload["evidence_claim_map"]["bindings"] = compact_bindings[:80]
        payload["evidence_claim_map"]["omitted_binding_count"] = (
            len(evidence_claim_map.bindings)
            - len(payload["evidence_claim_map"]["bindings"])
        )
        rendered = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
    return rendered


def _review_prompt(
    *,
    role: str,
    artifact: Literal["outline", "draft"],
    content: dict[str, Any],
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
) -> str:
    payload: dict[str, Any] = {
            "task": f"Review the {artifact} independently and return only your own verdict.",
            "required_role": role,
            "required_artifact": artifact,
            "submission_genre": genre.model_dump(mode="json"),
            "evidence_claim_map": evidence_claim_map.model_dump(mode="json"),
            "artifact": content,
        }
    if artifact == "draft":
        payload["workflow_lifecycle"] = {
            "current_step": "scientific prose review before deterministic rendering",
            "artifact_callouts": (
                "Exact [FIGURE:slot-id] and [TABLE:slot-id] tokens are required "
                "control anchors. They are materialized from frozen evidence "
                "after this review, then checked by a separate deterministic "
                "visual-integrity audit. Do not treat an expected callout as a "
                "missing figure or request its deletion. Do not require the "
                "prose draft itself to contain a slot-to-evidence mapping, "
                "encoding specification, caption, denominator annotation, or "
                "rendered visual; those are frozen and audited in the later "
                "visual-planning steps. You may flag a planned visual only when "
                "its named slot points to a scientific claim that has no bound "
                "evidence in the supplied evidence-claim map."
            ),
            "references": (
                "The exact bibliography is appended deterministically after "
                "structured prose review. Frozen source IDs are intentional "
                "citation keys. Do not treat the absent rendered reference list "
                "as a manuscript defect at this step; do flag unsupported, "
                "misused, or unresolvable citation keys."
            ),
            "author_declarations": (
                "Funding, conflicts of interest, author contributions, and any "
                "author-controlled ethics declaration are completed and approved "
                "at the later reporting/disclosure and final-author Gates. At "
                "this scientific-prose step, do not classify an explicitly "
                "marked pending author declaration as a scientific-content "
                "finding or submission blocker, and never invent its value. "
                "You may flag a false scientific ethics claim or an undeclared "
                "human/animal intervention when the evidence shows one."
            ),
        }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def _review_outline(
    *,
    root: Path,
    outline: HierarchicalPaperOutline,
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
) -> PanelDecision:
    from .agent_runtime import review_bundle_paper_artifact

    reviews = await asyncio.gather(
        *[
            review_bundle_paper_artifact(
                _review_prompt(
                    role=role,
                    artifact="outline",
                    content=outline.model_dump(mode="json"),
                    genre=genre,
                    evidence_claim_map=evidence_claim_map,
                ),
                cwd=root,
            )
            for role in _REVIEW_ROLES
        ]
    )
    for expected_role, review in zip(_REVIEW_ROLES, reviews, strict=True):
        if review.role != expected_role or review.artifact != "outline":
            raise ValueError(
                f"outline reviewer identity mismatch: expected {expected_role}, got {review.role}/{review.artifact}"
            )
    return decide_panel("outline", list(reviews))


async def _review_draft(
    *,
    root: Path,
    draft: PaperDraftSections,
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
) -> PanelDecision:
    from .agent_runtime import review_bundle_paper_artifact

    reviews = await asyncio.gather(
        *[
            review_bundle_paper_artifact(
                _review_prompt(
                    role=role,
                    artifact="draft",
                    content=draft.model_dump(mode="json"),
                    genre=genre,
                    evidence_claim_map=evidence_claim_map,
                ),
                cwd=root,
            )
            for role in _REVIEW_ROLES
        ]
    )
    for expected_role, review in zip(_REVIEW_ROLES, reviews, strict=True):
        if review.role != expected_role or review.artifact != "draft":
            raise ValueError(
                f"draft reviewer identity mismatch: expected {expected_role}, got {review.role}/{review.artifact}"
            )
    return decide_panel("draft", list(reviews))


def _draft_text(draft: PaperDraftSections) -> str:
    return "\n".join(str(value) for value in draft.model_dump(mode="json").values())


def restore_required_artifact_callouts(
    source: PaperDraftSections,
    revised: PaperDraftSections,
) -> PaperDraftSections:
    """Restore frozen visual anchors removed by a prose-only model revision.

    Artifact callouts are workflow control tokens rather than manuscript
    prose. A language-model revision may rephrase the surrounding section,
    but it has no authority to delete or invent those tokens. Missing tokens
    are deterministically restored to their original section, moved original
    tokens are preserved, and newly invented tokens are removed.
    """

    updates: dict[str, str] = {}
    required_callouts = list(dict.fromkeys(
        f"[{kind}:{slot_id}]"
        for kind, slot_id in _ARTIFACT_CALLOUT_RE.findall(_draft_text(source))
    ))
    required_counts = Counter(required_callouts)
    retained_counts: Counter[str] = Counter()
    for field_name in PaperDraftSections.model_fields:
        revised_value = str(getattr(revised, field_name))

        def retain_required_callout(match: re.Match[str]) -> str:
            callout = match.group(0)
            if retained_counts[callout] >= required_counts[callout]:
                return ""
            retained_counts[callout] += 1
            return callout

        sanitized = _ARTIFACT_CALLOUT_RE.sub(
            retain_required_callout,
            revised_value,
        )
        if sanitized != revised_value:
            updates[field_name] = sanitized
    if updates:
        revised = revised.model_copy(update=updates)
    current_counts = Counter(
        f"[{kind}:{slot_id}]"
        for kind, slot_id in _ARTIFACT_CALLOUT_RE.findall(
            _draft_text(revised)
        )
    )
    for field_name in PaperDraftSections.model_fields:
        source_value = str(getattr(source, field_name))
        revised_value = str(getattr(revised, field_name))
        original_callouts = [
            f"[{kind}:{slot_id}]"
            for kind, slot_id in _ARTIFACT_CALLOUT_RE.findall(source_value)
        ]
        missing: list[str] = []
        for callout in original_callouts:
            if current_counts[callout] < required_counts[callout]:
                missing.append(callout)
                current_counts[callout] += 1
        if missing:
            updates[field_name] = (
                revised_value.rstrip() + "\n\n" + "\n\n".join(missing)
            )
    return revised.model_copy(update=updates)


def _draft_contract_violations(
    draft: PaperDraftSections,
    *,
    outline: HierarchicalPaperOutline,
    evidence_claim_map: EvidenceClaimMap,
    structure_contract: PaperStructureContract = GENERIC_JOURNAL_ARTICLE,
    frozen_conclusion_text: str | None = None,
) -> list[str]:
    text = _draft_text(draft)
    # Abstract rhetoric is a repairable presentation concern.  The dedicated
    # Stage 4 presentation pass rewrites it before final rendering, so draft
    # integrity checks here focus on evidence, citations and visual bindings.
    violations: list[str] = []
    for field_name in type(draft).model_fields:
        leaked = publication_internal_tokens(str(getattr(draft, field_name)))
        if leaked:
            violations.append(
                f"{field_name} exposes internal audit tokens instead of "
                "scholarly terminology: "
                + ", ".join(leaked)
            )
    allowed_sources = set(evidence_claim_map.verified_source_ids)
    unknown_sources = sorted(set(_CITATION_RE.findall(text)) - allowed_sources)
    if unknown_sources:
        violations.append("draft cites unknown sources: " + ", ".join(unknown_sources))
    if not draft.conclusion.strip():
        violations.append("draft conclusion is missing")
    expected_callouts = {
        *(f"FIGURE:{item.slot_id}" for item in outline.figure_slots),
        *(f"TABLE:{item.slot_id}" for item in outline.table_slots),
    }
    found_callouts = {f"{kind}:{slot_id}" for kind, slot_id in _ARTIFACT_CALLOUT_RE.findall(text)}
    if expected_callouts != found_callouts:
        missing = sorted(expected_callouts - found_callouts)
        unknown = sorted(found_callouts - expected_callouts)
        if missing:
            violations.append("draft is missing artifact callouts: " + ", ".join(missing))
        if unknown:
            violations.append("draft contains unknown artifact callouts: " + ", ".join(unknown))
    duplicate_callouts = sorted(
        f"{kind}:{slot_id}"
        for (kind, slot_id), count in Counter(
            _ARTIFACT_CALLOUT_RE.findall(text)
        ).items()
        if count > 1
    )
    if duplicate_callouts:
        violations.append(
            "draft repeats artifact callouts that must appear exactly once: "
            + ", ".join(duplicate_callouts)
        )
    return list(dict.fromkeys(violations))


def _polish_trace(
    source: PaperDraftSections,
    polished: PaperDraftSections,
    *,
    frozen_conclusion: str,
    allow_numeric_deduplication: bool = False,
) -> PaperProsePolishTrace:
    source_text = _draft_text(source)
    polished_text = _draft_text(polished)
    source_numbers = Counter(_NUMBER_TOKEN_RE.findall(source_text))
    polished_numbers = Counter(_NUMBER_TOKEN_RE.findall(polished_text))
    numbers_ok = source_numbers == polished_numbers
    if allow_numeric_deduplication:
        # Whole-manuscript narrative repair may remove a repeated numeric
        # recap from the conclusion after the same value has already been
        # reported in Results.  Permit only reduced repetition: every numeric
        # value must remain present, no new value may appear, and no value may
        # gain occurrences.  This resolves the otherwise contradictory
        # requirements to keep Results complete and make the conclusion
        # concise without authorizing a changed estimate.
        numbers_ok = (
            set(source_numbers) == set(polished_numbers)
            and all(
                polished_numbers[token] <= source_numbers[token]
                for token in source_numbers
            )
        )
    citations_ok = Counter(_CITATION_RE.findall(source_text)) == Counter(
        _CITATION_RE.findall(polished_text)
    )
    callouts_ok = Counter(_ARTIFACT_CALLOUT_RE.findall(source_text)) == Counter(
        _ARTIFACT_CALLOUT_RE.findall(polished_text)
    )
    # The complete-draft numeric multiset is checked above.  Numbers may move
    # from Conclusion to Results during narrative repair; requiring them to
    # remain in Conclusion would directly conflict with a concise scholarly
    # conclusion.
    conclusion_ok = bool(polished.conclusion.strip())
    violations: list[str] = []
    if not numbers_ok:
        violations.append(
            "academic humanizer changed the numeric-token multiset"
            if not allow_numeric_deduplication
            else (
                "academic narrative repair added, removed entirely, or "
                "increased a numeric token"
            )
        )
    if not citations_ok:
        violations.append("academic humanizer changed the citation-key multiset")
    if not callouts_ok:
        violations.append("academic humanizer changed the figure/table callout multiset")
    if not conclusion_ok:
        violations.append(
            "academic humanizer removed the conclusion"
        )
    return PaperProsePolishTrace(
        policy=(
            "academic_narrative_numeric_deduplication"
            if allow_numeric_deduplication
            else "academic_humanizer_claim_preserving"
        ),
        source_sha256=_model_sha256(source),
        polished_sha256=_model_sha256(polished),
        number_multiset_preserved=numbers_ok,
        citation_multiset_preserved=citations_ok,
        artifact_callout_multiset_preserved=callouts_ok,
        frozen_conclusion_preserved=conclusion_ok,
        accepted=not violations,
        reverted=False,
        violations=violations,
    )


async def _review_revise_and_polish_draft(
    *,
    root: Path,
    draft: PaperDraftSections,
    outline: HierarchicalPaperOutline,
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
    narrative_contract: PublicationNarrativeContract | None = None,
    voice_profile: AuthorVoiceProfile | None = None,
    structure_contract: PaperStructureContract = GENERIC_JOURNAL_ARTICLE,
) -> tuple[
    PaperDraftSections,
    PanelDecision,
    PaperRevisionTrace,
    PaperProsePolishTrace,
    PaperArtifactManifest,
]:
    from .agent_runtime import humanize_bundle_paper_draft, revise_bundle_paper_draft

    synthesis_dir = root / "stage_4_synthesis"
    draft, normalization = normalize_paper_draft(
        draft,
        frozen_conclusion=evidence_claim_map.frozen_conclusion,
    )
    violations = _draft_contract_violations(
        draft,
        outline=outline,
        evidence_claim_map=evidence_claim_map,
        structure_contract=structure_contract,
    )
    if violations:
        raise ValueError("initial paper draft violates contracts: " + "; ".join(violations))
    write_json_atomic(synthesis_dir / "paper_draft_sections.initial.json", draft)
    decision = await _review_draft(
        root=root,
        draft=draft,
        genre=genre,
        evidence_claim_map=evidence_claim_map,
    )
    write_json_atomic(synthesis_dir / "paper_draft_review.json", decision)
    if decision.decision == "halt":
        raise ValueError("draft panel halted revision because review context was insufficient")
    revised = draft
    if decision.decision == "revise":
        revised = await revise_bundle_paper_draft(
            json.dumps(
                {
                    "submission_genre": genre.model_dump(mode="json"),
                    "evidence_claim_map": evidence_claim_map.model_dump(mode="json"),
                    "approved_outline": outline.model_dump(mode="json"),
                    "draft": draft.model_dump(mode="json"),
                    "panel_decision": decision.model_dump(mode="json"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            cwd=root,
        )
        revised, revision_normalization = normalize_paper_draft(
            revised,
            frozen_conclusion=evidence_claim_map.frozen_conclusion,
        )
        normalization = PaperRevisionTrace(
            changed_fields=list(dict.fromkeys(normalization.changed_fields + revision_normalization.changed_fields)),
            operations=list(dict.fromkeys(normalization.operations + revision_normalization.operations)),
            source_sha256=_model_sha256(draft),
            revised_sha256=_model_sha256(revised),
        )
        violations = _draft_contract_violations(
            revised,
            outline=outline,
            evidence_claim_map=evidence_claim_map,
            structure_contract=structure_contract,
        )
        if violations:
            raise ValueError("revised paper draft violates contracts: " + "; ".join(violations))
    artifact_manifest = build_paper_artifacts(
        root,
        outline=outline,
        evidence_claim_map=evidence_claim_map,
    )
    effective_voice = voice_profile or AuthorVoiceProfile(
        profile_id="voice-default-precise",
        language=genre.language,
        source_types=["style_questionnaire"],
        sentence_length_distribution=SentenceLengthDistribution(
            short=0.25,
            medium=0.55,
            long=0.20,
        ),
    )
    humanization_plan = HumanizationPlan(
        plan_id="humanize-claim-preserving-v1",
        study_id=(
            narrative_contract.study_id
            if narrative_contract is not None
            else "legacy-project-bundle"
        ),
        source_artifact_id=_model_sha256(revised),
        narrative_contract_id=(
            narrative_contract.contract_sha256
            if narrative_contract is not None
            else "legacy-bounded-draft"
        ),
        voice_profile_id=effective_voice.profile_id,
        level="authorial_voice",
        sections=list(revised.model_dump(mode="json")),
        operations=[
            "grammar",
            "punctuation",
            "conciseness",
            "remove_ai_tells",
            "sentence_rhythm",
            "terminology",
            "paragraph_logic",
            "calibrate_claim_verbs",
            "neutral_limitation_framing",
        ],
    )
    write_json_atomic(
        synthesis_dir / "paper_author_voice_profile.json",
        effective_voice,
    )
    write_json_atomic(
        synthesis_dir / "paper_humanization_plan.json",
        humanization_plan,
    )
    polished = await humanize_bundle_paper_draft(
        json.dumps(
            {
                "submission_genre": genre.model_dump(mode="json"),
                "approved_outline": outline.model_dump(mode="json"),
                "evidence_claim_map": evidence_claim_map.model_dump(mode="json"),
                "paper_artifact_manifest": artifact_manifest.model_dump(mode="json"),
                "approved_draft": revised.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        cwd=root,
    )
    polished, polish_normalization = normalize_paper_draft(polished)
    polish = _polish_trace(
        revised,
        polished,
        frozen_conclusion=evidence_claim_map.frozen_conclusion,
    )
    claim_ids = [item.claim_id for item in evidence_claim_map.bindings]
    evidence_ids = sorted(
        {
            pointer.source_id or pointer.path
            for binding in evidence_claim_map.bindings
            for pointer in binding.evidence
        }
    )
    relationships = {
        item.claim_id: item.claim_strength
        for item in evidence_claim_map.bindings
    }
    scope_qualifiers = {
        claim_id: list(narrative_contract.advantage_conditions)
        for claim_id in claim_ids
    } if narrative_contract is not None else {}
    result_roles: dict[str, str] = {}
    if narrative_contract is not None:
        for claim_id in narrative_contract.required_claim_ids:
            result_roles[claim_id] = "primary"
        for claim_id in narrative_contract.supporting_claim_ids:
            result_roles[claim_id] = "secondary"
        for claim_id in narrative_contract.mandatory_negative_claim_ids:
            result_roles[claim_id] = "limitation"
    source_snapshot = snapshot_semantics(
        prose=_draft_text(revised),
        claim_ids=claim_ids,
        evidence_ids=evidence_ids,
        claim_relationships=relationships,
        scope_qualifiers=scope_qualifiers,
        uncertainty_levels={},
        result_roles=result_roles,
        confirmatory_status="frozen_upstream_status",
        evidence_maturity="frozen_evidence_claim_map",
        definitions={},
    )
    output_snapshot = snapshot_semantics(
        prose=_draft_text(polished),
        claim_ids=claim_ids,
        evidence_ids=evidence_ids,
        claim_relationships=relationships,
        scope_qualifiers=scope_qualifiers,
        uncertainty_levels={},
        result_roles=result_roles,
        confirmatory_status="frozen_upstream_status",
        evidence_maturity="frozen_evidence_claim_map",
        definitions={},
    )
    humanization_integrity = audit_humanization_integrity(
        source_snapshot,
        output_snapshot,
        source_prose=_draft_text(revised),
        humanized_prose=_draft_text(polished),
    )
    write_json_atomic(
        synthesis_dir / "paper_humanization_integrity.attempt.json",
        humanization_integrity,
    )
    if not humanization_integrity.passed:
        polish = polish.model_copy(
            update={
                "accepted": False,
                "violations": list(
                    dict.fromkeys(
                        [
                            *polish.violations,
                            *humanization_integrity.violations,
                        ]
                    )
                ),
            }
        )
    if not polish.accepted:
        polished = revised
        polish = polish.model_copy(
            update={
                "accepted": False,
                "reverted": True,
                "polished_sha256": _model_sha256(revised),
                "violations": polish.violations + ["polished output was rejected and reverted"],
            }
        )
        output_snapshot = source_snapshot
        humanization_integrity = audit_humanization_integrity(
            source_snapshot,
            output_snapshot,
            source_prose=_draft_text(revised),
            humanized_prose=_draft_text(revised),
        )
    final_violations = _draft_contract_violations(
        polished,
        outline=outline,
        evidence_claim_map=evidence_claim_map,
        structure_contract=structure_contract,
    )
    if narrative_contract is not None:
        final_violations.extend(
            validate_manuscript_narrative(
                {
                    field_name: str(getattr(polished, field_name))
                    for field_name in PaperDraftSections.model_fields
                },
                structure_contract.narrative,
            )
        )
    final_violations = list(dict.fromkeys(final_violations))
    if final_violations:
        raise ValueError("final polished draft violates contracts: " + "; ".join(final_violations))
    if polish_normalization.operations:
        normalization = PaperRevisionTrace(
            changed_fields=list(dict.fromkeys(normalization.changed_fields + polish_normalization.changed_fields)),
            operations=list(dict.fromkeys(normalization.operations + polish_normalization.operations)),
            source_sha256=normalization.source_sha256,
            revised_sha256=_model_sha256(polished),
        )
    section_diffs = [
        HumanizationSectionDiff(
            section_key=section,
            source_sha256=hashlib.sha256(
                str(getattr(revised, section)).encode("utf-8")
            ).hexdigest(),
            humanized_sha256=hashlib.sha256(
                str(getattr(polished, section)).encode("utf-8")
            ).hexdigest(),
            changed=getattr(revised, section) != getattr(polished, section),
            operation_summary=(
                ["authorial academic copy edit"]
                if getattr(revised, section) != getattr(polished, section)
                else []
            ),
        )
        for section in revised.model_dump(mode="json")
    ]
    humanization_trace = HumanizationTrace(
        trace_id="humanization-trace-claim-preserving-v1",
        plan_id=humanization_plan.plan_id,
        source_artifact_id=_model_sha256(revised),
        output_artifact_id=_model_sha256(polished),
        section_diffs=section_diffs,
        prompt_version="academic-humanizer-v1",
    )
    write_json_atomic(
        synthesis_dir / "paper_humanized_sections.json",
        polished,
    )
    write_json_atomic(
        synthesis_dir / "paper_humanization_diff.json",
        humanization_trace,
    )
    write_json_atomic(
        synthesis_dir / "paper_humanization_integrity.json",
        humanization_integrity,
    )
    return polished, decision, normalization, polish, artifact_manifest


def _sanitize_outline_references(
    outline: HierarchicalPaperOutline,
    *,
    evidence_claim_map: EvidenceClaimMap,
) -> HierarchicalPaperOutline:
    """Remove model-invented identifiers before contract validation.

    This does not add evidence or repair scientific content. It only prevents a
    generated outline from treating an identifier absent from the immutable
    EvidenceClaimMap as authority. The scientific panel still reviews the
    resulting omissions and can require a real, registered binding.
    """

    allowed_claims = {
        binding.claim_id for binding in evidence_claim_map.bindings
    }
    allowed_sources = set(evidence_claim_map.verified_source_ids)
    allowed_figures = {item.slot_id for item in outline.figure_slots}
    allowed_tables = {item.slot_id for item in outline.table_slots}

    def sanitize(node: OutlineNode) -> OutlineNode:
        return node.model_copy(
            update={
                "claim_ids": [
                    item for item in node.claim_ids if item in allowed_claims
                ],
                "source_ids": [
                    item for item in node.source_ids if item in allowed_sources
                ],
                "figure_slot_ids": [
                    item
                    for item in node.figure_slot_ids
                    if item in allowed_figures
                ],
                "table_slot_ids": [
                    item
                    for item in node.table_slot_ids
                    if item in allowed_tables
                ],
                "children": [sanitize(child) for child in node.children],
            }
        )

    return outline.model_copy(
        update={"sections": [sanitize(section) for section in outline.sections]}
    )


async def _prepare_reviewed_outline(
    *,
    root: Path,
    genre: SubmissionGenreProfile,
    evidence_claim_map: EvidenceClaimMap,
    sources: list[LiteratureSource],
    narrative_contract: PublicationNarrativeContract | None = None,
    visual_argument_plan: VisualArgumentPlan | None = None,
    structure_contract: PaperStructureContract = GENERIC_JOURNAL_ARTICLE,
    publication_aliases: dict[str, str] | None = None,
    title_basis: dict[str, Any] | None = None,
) -> tuple[HierarchicalPaperOutline, PanelDecision]:
    from .agent_runtime import (
        generate_bundle_paper_outline,
        generate_bundle_publication_title,
        revise_bundle_paper_outline,
    )

    initial = await generate_bundle_paper_outline(
        _outline_prompt(
            genre=genre,
            evidence_claim_map=evidence_claim_map,
            sources=sources,
            narrative_contract=narrative_contract,
            visual_argument_plan=visual_argument_plan,
            structure_contract=structure_contract,
        ),
        cwd=root,
    )
    initial = academicize_paper_outline(
        initial,
        aliases=publication_aliases,
        language=genre.language,
    )
    safe_scope_title = academicize_publication_text(
        str((title_basis or {}).get("direction") or "").strip(),
        aliases=publication_aliases,
        language=genre.language,
    )
    if validate_publication_title(initial.title) and not validate_publication_title(
        safe_scope_title
    ):
        initial = initial.model_copy(update={"title": safe_scope_title})
    initial = _sanitize_outline_references(
        initial,
        evidence_claim_map=evidence_claim_map,
    )
    if visual_argument_plan is not None:
        figure_slots, table_slots = compile_visual_slots(visual_argument_plan)
        initial = initial.model_copy(
            update={
                "figure_slots": figure_slots,
                "table_slots": table_slots,
            }
        )
        # The model may declare and reference a self-invented slot.  Replacing
        # the declaration list with the frozen visual plan is not sufficient:
        # node-level references must be sanitized against that authoritative
        # list after the replacement, otherwise validation sees a dangling
        # figure/table identifier and permanently fails the workflow.
        initial = _sanitize_outline_references(
            initial,
            evidence_claim_map=evidence_claim_map,
        )
    violations = validate_outline(
        initial,
        contract=structure_contract,
        evidence_claim_map=evidence_claim_map,
    )
    if violations:
        raise ValueError("initial paper outline violates contracts: " + "; ".join(violations))
    synthesis_dir = root / "stage_4_synthesis"
    write_json_atomic(synthesis_dir / "paper_outline.initial.json", initial)
    decision = await _review_outline(
        root=root,
        outline=initial,
        genre=genre,
        evidence_claim_map=evidence_claim_map,
    )
    write_json_atomic(synthesis_dir / "paper_outline_review.json", decision)
    if decision.decision == "halt":
        raise ValueError("outline panel halted drafting because review context was insufficient")
    final = initial
    if decision.decision == "revise":
        final = await revise_bundle_paper_outline(
            json.dumps(
                {
                    "submission_genre": genre.model_dump(mode="json"),
                    "evidence_claim_map": evidence_claim_map.model_dump(mode="json"),
                    "initial_outline": initial.model_dump(mode="json"),
                    "panel_decision": decision.model_dump(mode="json"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            cwd=root,
        )
        final = academicize_paper_outline(
            final,
            aliases=publication_aliases,
            language=genre.language,
        )
        final = _sanitize_outline_references(
            final,
            evidence_claim_map=evidence_claim_map,
        )
        if visual_argument_plan is not None:
            figure_slots, table_slots = compile_visual_slots(
                visual_argument_plan
            )
            final = final.model_copy(
                update={
                    "figure_slots": figure_slots,
                    "table_slots": table_slots,
                }
            )
            final = _sanitize_outline_references(
                final,
                evidence_claim_map=evidence_claim_map,
            )
        final = _restore_required_outline_structure(
            revised=final,
            approved_initial=initial,
            contract=structure_contract,
        )
        violations = validate_outline(
            final,
            contract=structure_contract,
            evidence_claim_map=evidence_claim_map,
        )
        if violations:
            raise ValueError("revised paper outline violates contracts: " + "; ".join(violations))
    if title_basis:
        public_title_basis = {
            key: academicize_publication_text(
                str(value),
                aliases=publication_aliases,
                language=genre.language,
            )
            for key, value in title_basis.items()
            if value is not None and str(value).strip()
        }
        title_record: dict[str, Any]
        try:
            title_candidate = await generate_bundle_publication_title(
                json.dumps(
                    {
                        "task": "Generate one natural academic manuscript title.",
                        "language": genre.language,
                        "document_type": genre.document_type,
                        "title_basis": public_title_basis,
                        "central_thesis": academicize_publication_text(
                            final.thesis,
                            aliases=publication_aliases,
                            language=genre.language,
                        ),
                        "current_outline_title": final.title,
                        "frozen_conclusion": academicize_publication_text(
                            evidence_claim_map.frozen_conclusion,
                            aliases=publication_aliases,
                            language=genre.language,
                        ),
                        "constraints": {
                            "single_line": True,
                            "reader_facing_scholarly_language": True,
                            "no_internal_identifiers": True,
                            "no_unbound_mechanism": True,
                            "no_result_overclaim": True,
                        },
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                cwd=root,
            )
            candidate_violations = validate_publication_title(
                title_candidate.title
            )
            candidate_accepted = (
                not candidate_violations
                and title_candidate.avoids_internal_identifiers
                and title_candidate.avoids_result_overclaim
            )
            if candidate_accepted:
                final = final.model_copy(
                    update={"title": title_candidate.title.strip()}
                )
                title_record = {
                    "status": "model_title_accepted",
                    "candidate": title_candidate.model_dump(mode="json"),
                    "violations": [],
                }
            elif not validate_publication_title(safe_scope_title):
                final = final.model_copy(update={"title": safe_scope_title})
                title_record = {
                    "status": "deterministic_scope_fallback",
                    "candidate": title_candidate.model_dump(mode="json"),
                    "violations": candidate_violations,
                }
            else:
                raise ValueError(
                    "publication title generation failed validation: "
                    + "; ".join(candidate_violations)
                )
        except Exception as exc:
            if validate_publication_title(safe_scope_title):
                raise
            final = final.model_copy(update={"title": safe_scope_title})
            title_record = {
                "status": "deterministic_scope_fallback",
                "candidate": None,
                "violations": [f"{type(exc).__name__}: {exc}"],
            }
        write_json_atomic(
            synthesis_dir / "publication_title_generation.json",
            title_record,
        )
        final_violations = validate_outline(
            final,
            contract=structure_contract,
            evidence_claim_map=evidence_claim_map,
        )
        if final_violations:
            raise ValueError(
                "publication-titled outline violates contracts: "
                + "; ".join(final_violations)
            )
    write_json_atomic(synthesis_dir / "paper_outline.json", final)
    return final, decision


def _restore_required_outline_structure(
    *,
    revised: HierarchicalPaperOutline,
    approved_initial: HierarchicalPaperOutline,
    contract: PaperStructureContract,
) -> HierarchicalPaperOutline:
    """Restore venue-required section scaffolding lost during model revision.

    The initial outline has already passed the evidence and structure contracts.
    A revision may rewrite or reorder it, but it may not delete a mandatory
    section.  Restoring the previously approved node is deterministic and adds
    no new scientific claim, citation, or evidence binding.
    """

    initial_by_key = {item.section_key: item for item in approved_initial.sections}
    revised_by_key = {item.section_key: item for item in revised.sections}
    contract_order = {item.key: index for index, item in enumerate(contract.sections)}

    declared_figures = {item.slot_id for item in revised.figure_slots}
    declared_tables = {item.slot_id for item in revised.table_slots}

    def sanitize_slots(node: OutlineNode) -> OutlineNode:
        return node.model_copy(
            update={
                "figure_slot_ids": [
                    slot_id
                    for slot_id in node.figure_slot_ids
                    if slot_id in declared_figures
                ],
                "table_slot_ids": [
                    slot_id
                    for slot_id in node.table_slot_ids
                    if slot_id in declared_tables
                ],
                "children": [
                    sanitize_slots(child) for child in node.children
                ],
            }
        )

    restored = [sanitize_slots(item) for item in revised.sections]
    for spec in contract.sections:
        if spec.required and spec.key not in revised_by_key:
            fallback = initial_by_key.get(spec.key)
            if fallback is None:
                # This should be unreachable because the initial outline passed
                # validate_outline, but leave validation responsible for the
                # explicit failure if an incompatible contract is supplied.
                continue
            restored.append(fallback)

    restored.sort(
        key=lambda item: (
            contract_order.get(item.section_key, len(contract_order)),
            item.section_key,
        )
    )
    if restored == revised.sections:
        return revised
    return revised.model_copy(update={"sections": restored})


async def expand_project_bundle_paper(run_dir: str | Path) -> PaperExpansionAudit:
    root = Path(run_dir).resolve()
    plan = prepare_project_bundle_paper(root, persist=True)
    if not plan.ready:
        raise ValueError(
            "完整论文补写尚未解锁："
            + "；".join(item.rstrip("。；;") for item in plan.blockers)
        )
    sources, _, _ = _literature_sources(root)
    synthesis_dir = root / "stage_4_synthesis"
    genre = load_or_create_submission_genre(root, GENERIC_JOURNAL_ARTICLE)
    evidence_claim_map = build_evidence_claim_map(
        claims=_json(synthesis_dir / "claims.json"),
        verdict=_json(root / "stage_3_experimentation" / "idea_verdict.json"),
        sources=sources,
    )
    write_json_atomic(synthesis_dir / "evidence_claim_map.json", evidence_claim_map)
    scope_contract = _json(root / "stage_1_discovery" / "scope_contract.json")
    protocol_lock = _json(root / "stage_2_protocol" / "protocol_lock.json")
    title_basis = {
        "direction": scope_contract.get("title")
        or scope_contract.get("research_question"),
        "research_question": scope_contract.get("research_question"),
        "hypothesis": scope_contract.get("hypothesis_under_test"),
        "candidate_contribution": scope_contract.get("novelty_candidate"),
        "study_design": scope_contract.get("evidence_maturity")
        or protocol_lock.get("study_design"),
        "frozen_conclusion": evidence_claim_map.frozen_conclusion,
    }
    outline, _ = await _prepare_reviewed_outline(
        root=root,
        genre=genre,
        evidence_claim_map=evidence_claim_map,
        sources=sources,
        title_basis=title_basis,
    )
    from .agent_runtime import generate_bundle_paper_draft

    draft = await generate_bundle_paper_draft(
        _agent_prompt(
            root,
            sources,
            genre=genre,
            evidence_claim_map=evidence_claim_map,
            outline=outline,
        ),
        cwd=root,
    )
    draft, _, revision_trace, polish_trace, artifact_manifest = await _review_revise_and_polish_draft(
        root=root,
        draft=draft,
        outline=outline,
        genre=genre,
        evidence_claim_map=evidence_claim_map,
    )
    write_json_atomic(synthesis_dir / "paper_draft_sections.json", draft)
    write_json_atomic(synthesis_dir / "paper_revision_trace.json", revision_trace)
    write_json_atomic(synthesis_dir / "paper_prose_polish_trace.json", polish_trace)
    verdict = _json(root / "stage_3_experimentation" / "idea_verdict.json")
    manuscript = render_full_manuscript(
        draft,
        verdict=verdict,
        sources=sources,
        artifact_manifest=artifact_manifest,
    )
    manuscript_path = root / "stage_4_synthesis" / "full_manuscript.md"
    manuscript_path.write_text(manuscript, encoding="utf-8", newline="\n")
    latex_path = write_submission_latex(
        manuscript_path,
        contract=GENERIC_JOURNAL_ARTICLE,
        language="zh",
    )
    typesetting_report = PaperTypesettingReport(
        structure_profile_id=GENERIC_JOURNAL_ARTICLE.profile_id,
        language="zh",
        latex_path=str(latex_path.relative_to(root)).replace("\\", "/"),
        latex_sha256=sha256_file(latex_path),
        pdf_generated=False,
        blocker="PDF compilation waits for the final manuscript integrity gate.",
    )
    write_json_atomic(synthesis_dir / "paper_typesetting_report.json", typesetting_report)
    audit = _audit_full_manuscript(root, plan, persist=True)
    if audit.passed:
        try:
            from .manuscript_compile import finalize_manuscript_pdf

            compiled = finalize_manuscript_pdf(
                latex_path,
                output_dir=synthesis_dir,
                profile="journal-article",
                language="zh",
                engine="auto",
                passes=2,
                report_path=synthesis_dir / "full_manuscript.typeset.depth.json",
                manifest_path=synthesis_dir / "full_manuscript.finalization.json",
            )
            pdf_path = Path(str(compiled["pdf"])).resolve()
            typesetting_report = PaperTypesettingReport(
                structure_profile_id=GENERIC_JOURNAL_ARTICLE.profile_id,
                language="zh",
                latex_path=str(latex_path.relative_to(root)).replace("\\", "/"),
                latex_sha256=sha256_file(latex_path),
                pdf_generated=True,
                pdf_path=str(pdf_path.relative_to(root)).replace("\\", "/"),
                pdf_sha256=sha256_file(pdf_path),
                compiler=str(compiled.get("compiler") or "unknown"),
                blocker=None,
            )
        except Exception as exc:
            typesetting_report = typesetting_report.model_copy(
                update={"blocker": f"{type(exc).__name__}: {exc}"[:2000]}
            )
        write_json_atomic(synthesis_dir / "paper_typesetting_report.json", typesetting_report)
    certificate = PaperCompletionCertificate(
        track_id=plan.track_id,
        paper_draft_ready=audit.paper_draft_ready,
        publication_ready=audit.publication_ready,
        manuscript_sha256=sha256_file(manuscript_path),
        genre_sha256=sha256_file(synthesis_dir / "submission_genre.json"),
        evidence_claim_map_sha256=sha256_file(synthesis_dir / "evidence_claim_map.json"),
        outline_sha256=sha256_file(synthesis_dir / "paper_outline.json"),
        outline_review_sha256=sha256_file(synthesis_dir / "paper_outline_review.json"),
        draft_review_sha256=sha256_file(synthesis_dir / "paper_draft_review.json"),
        revision_trace_sha256=sha256_file(synthesis_dir / "paper_revision_trace.json"),
        prose_polish_trace_sha256=sha256_file(synthesis_dir / "paper_prose_polish_trace.json"),
        artifact_manifest_sha256=sha256_file(synthesis_dir / "paper_artifact_manifest.json"),
        typeset_source_sha256=sha256_file(latex_path),
        typesetting_report_sha256=sha256_file(synthesis_dir / "paper_typesetting_report.json"),
        plan_sha256=sha256_file(root / "stage_4_synthesis" / "paper_expansion_plan.json"),
        audit_sha256=sha256_file(root / "stage_4_synthesis" / "paper_expansion_audit.json"),
    )
    write_json_atomic(
        root / "stage_4_synthesis" / "paper_completion_certificate.json", certificate
    )
    return audit


def verify_project_bundle_paper(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    try:
        certificate = PaperCompletionCertificate.model_validate(
            read_json(root / "stage_4_synthesis" / "paper_completion_certificate.json")
        )
        plan = PaperExpansionPlan.model_validate(
            read_json(root / "stage_4_synthesis" / "paper_expansion_plan.json")
        )
    except Exception as exc:
        return {"passed": False, "violations": [f"论文证书无效：{exc}"]}
    paths = {
        "manuscript": root / "stage_4_synthesis" / "full_manuscript.md",
        "genre": root / "stage_4_synthesis" / "submission_genre.json",
        "evidence_claim_map": root / "stage_4_synthesis" / "evidence_claim_map.json",
        "outline": root / "stage_4_synthesis" / "paper_outline.json",
        "outline_review": root / "stage_4_synthesis" / "paper_outline_review.json",
        "draft_review": root / "stage_4_synthesis" / "paper_draft_review.json",
        "revision_trace": root / "stage_4_synthesis" / "paper_revision_trace.json",
        "prose_polish_trace": root / "stage_4_synthesis" / "paper_prose_polish_trace.json",
        "artifact_manifest": root / "stage_4_synthesis" / "paper_artifact_manifest.json",
        "typeset_source": root / "stage_4_synthesis" / "full_manuscript.tex",
        "typesetting_report": root / "stage_4_synthesis" / "paper_typesetting_report.json",
        "plan": root / "stage_4_synthesis" / "paper_expansion_plan.json",
        "audit": root / "stage_4_synthesis" / "paper_expansion_audit.json",
    }
    expected = {
        "manuscript": certificate.manuscript_sha256,
        "genre": certificate.genre_sha256,
        "evidence_claim_map": certificate.evidence_claim_map_sha256,
        "outline": certificate.outline_sha256,
        "outline_review": certificate.outline_review_sha256,
        "draft_review": certificate.draft_review_sha256,
        "revision_trace": certificate.revision_trace_sha256,
        "prose_polish_trace": certificate.prose_polish_trace_sha256,
        "artifact_manifest": certificate.artifact_manifest_sha256,
        "typeset_source": certificate.typeset_source_sha256,
        "typesetting_report": certificate.typesetting_report_sha256,
        "plan": certificate.plan_sha256,
        "audit": certificate.audit_sha256,
    }
    violations = [
        f"已认证论文产物发生变化或缺失：{name}"
        for name, path in paths.items()
        if not path.is_file() or sha256_file(path) != expected[name]
    ]
    audit = _audit_full_manuscript(root, plan, persist=False)
    if not audit.passed:
        violations.extend(audit.violations)
    return {
        "passed": not violations,
        "paper_draft_ready": audit.paper_draft_ready and not violations,
        "publication_ready": audit.publication_ready and not violations,
        "diagnostic_owner": audit.diagnostic_owner,
        "violations": list(dict.fromkeys(violations)),
    }
