from __future__ import annotations

"""Research Forge's self-owned paper workflow and structure contracts.

This module is deliberately independent of external Codex skills.  Skills may
inform the design of the workflow, but runtime generation, audit, revision and
rendering all consume the contracts defined here.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


AbstractStyle = Literal["unstructured", "structured"]


@dataclass(frozen=True)
class AbstractContract:
    """Venue-facing abstract form, kept separate from its semantic moves."""

    style: AbstractStyle = "unstructured"
    minimum_characters: int = 100
    maximum_characters: int = 2_500
    paragraph_count: int | None = 1
    labels: tuple[str, ...] = ()
    preferred_numeric_token_count: int = 0
    maximum_numeric_token_count: int = 2
    maximum_governance_term_count: int = 2
    rhetorical_route: tuple[str, ...] = (
        "scientific_problem_or_tension",
        "bounded_study_design",
        "principal_finding_in_plain_language",
        "scientific_implication",
        "single_calibrated_boundary",
    )

    def prompt_contract(self) -> dict[str, object]:
        if self.style == "unstructured":
            rule = (
                "Write exactly one continuous paragraph. Cover context, objective, "
                "method, results, and conclusion as prose, but render no move labels, "
                "Markdown headings, bullets, or numbered items."
            )
        else:
            rule = "Use only the venue-required labels, in the declared order."
        return {
            "style": self.style,
            "minimum_characters": self.minimum_characters,
            "maximum_characters": self.maximum_characters,
            "paragraph_count": self.paragraph_count,
            "allowed_labels": list(self.labels),
            "preferred_numeric_token_count": self.preferred_numeric_token_count,
            "maximum_numeric_token_count": self.maximum_numeric_token_count,
            "maximum_governance_term_count": self.maximum_governance_term_count,
            "rhetorical_route": list(self.rhetorical_route),
            "numeric_style_rule": (
                "Express the principal finding accurately in natural language. "
                "Prefer no numerals. If a numeral is essential, keep only one "
                "or two reader-critical quantities; move all other estimates, "
                "intervals, p-values, counts, seeds, and implementation numbers "
                "to Results."
            ),
            "reader_facing_rhetoric_rule": (
                "Open with the scientific problem or tension, not a workflow "
                "requirement. Name only the design information needed to trust "
                "the comparison. State the principal finding before limitations, "
                "then explain its scientific meaning. End with at most one "
                "calibrated scope sentence. Do not turn the abstract into an "
                "audit report or a catalogue of disclaimers."
            ),
            "depth_rule": (
                "Use the venue depth profile as a genuine target, not a finish "
                "line. A complete abstract must give enough context to understand "
                "the problem, identify the comparison, report the principal "
                "finding, explain why it matters, and state one decisive boundary."
            ),
            "terminology_rule": (
                "Use ordinary scholarly language. Never expose snake_case names, "
                "task or run identifiers, file names, workflow states, hashes, "
                "ledger terminology, or repeated governance vocabulary. Put "
                "load-bearing protocol detail in Methods."
            ),
            "semantic_review_rule": (
                "A model review must confirm that the problem, finding, and "
                "implication form a coherent rhetorical arc. Deterministic checks "
                "enforce presentation anti-patterns but do not infer scientific "
                "meaning."
            ),
            "rule": rule,
        }


UNSTRUCTURED_ABSTRACT = AbstractContract()

_ABSTRACT_LABEL = (
    r"Abstract|Background|Context|Objective|Objectives|Purpose|Methods?|"
    r"Results?|Conclusions?|Keywords?|摘要|背景|目的|目标|方法|结果|结论|关键词"
)
_ABSTRACT_LABEL_RE = re.compile(
    rf"(?i)(?<![\w-])(?:{_ABSTRACT_LABEL})\s*[:：]\s*"
)
_ABSTRACT_HEADING_RE = re.compile(
    rf"(?im)^\s*#{{1,6}}\s*(?:{_ABSTRACT_LABEL})\s*:?[：]?\s*$"
)
_KEYWORDS_BLOCK_RE = re.compile(
    r"(?is)\n\s*\n\s*\*\*(?:Keywords?|关键词)\s*[:：]?\*\*\s*[:：]?\s*(.+?)\s*$"
)
_ABSTRACT_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
)
_ABSTRACT_INTERNAL_IDENTIFIER_RE = re.compile(
    r"(?<!\w)(?:task|study|step|run)-[a-z0-9-]+(?!\w)"
    r"|(?<!\w)[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?!\w)",
    re.IGNORECASE,
)
_ABSTRACT_GOVERNANCE_TERM_RE = re.compile(
    r"(?i)(?<!\w)(?:frozen|immutable|hash(?:es)?|ledger|audit(?:ed|ing)?|"
    r"gate(?:d|s|ing)?|invalidated|diagnostic_owner|repair contract|"
    r"workflow state|artifact lineage)(?!\w)"
    r"|(?:冻结|不可变|哈希|账本|审计|门控|失效|修复契约|工作流状态|工件谱系)"
)
_ABSTRACT_LIMITATION_MARKER_RE = re.compile(
    r"(?i)\b(?:does not|do not|cannot|can not|fails? to|limited to|"
    r"doesn't|cannot establish|no evidence)\b"
    r"|(?:不代表|不证明|不能|无法|仅限于|没有证据|未能证明)"
)


def abstract_reader_facing_violations(
    value: str, contract: AbstractContract
) -> list[str]:
    """Return machine-checkable reader-facing rhetoric violations.

    Scientific meaning remains a model-review responsibility. These checks
    target only obvious presentation failures that should never reach a reader.
    """

    text = value.strip()
    violations: list[str] = []
    internal_identifiers = sorted(
        set(_ABSTRACT_INTERNAL_IDENTIFIER_RE.findall(text))
    )
    if internal_identifiers:
        violations.append(
            "abstract exposes internal identifiers: "
            + ", ".join(internal_identifiers)
        )
    governance_terms = _ABSTRACT_GOVERNANCE_TERM_RE.findall(text)
    if len(governance_terms) > contract.maximum_governance_term_count:
        violations.append(
            "abstract uses audit or governance vocabulary "
            f"{len(governance_terms)} times; maximum is "
            f"{contract.maximum_governance_term_count}"
        )
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?。！？])\s*", text)
        if item.strip()
    ]
    if len(sentences) >= 2 and all(
        _ABSTRACT_LIMITATION_MARKER_RE.search(item)
        for item in sentences[-2:]
    ):
        violations.append(
            "abstract ends with consecutive limitation sentences; state the "
            "scientific implication before one calibrated boundary sentence"
        )
    return violations


def abstract_numeric_token_count(value: str) -> int:
    """Count reader-visible numeric tokens in abstract prose."""

    return len(_ABSTRACT_NUMBER_RE.findall(value))


def split_abstract_keywords(value: str) -> tuple[str, str | None]:
    """Separate optional keyword metadata from the abstract prose block."""

    match = _KEYWORDS_BLOCK_RE.search(value.strip())
    if match is None:
        return value.strip(), None
    return value.strip()[: match.start()].strip(), match.group(1).strip()


def normalize_unstructured_abstract(value: str) -> tuple[str, tuple[str, ...]]:
    """Remove writing scaffolds and collapse an abstract to one Markdown paragraph.

    This is deliberately editorial rather than scientific: it changes no number,
    citation, or claim, and records every kind of deterministic repair applied.
    """

    original = value.strip()
    changes: list[str] = []
    without_headings = _ABSTRACT_HEADING_RE.sub(" ", original)
    if without_headings != original:
        changes.append("removed_abstract_scaffold_heading")
    without_labels = _ABSTRACT_LABEL_RE.sub("", without_headings)
    if without_labels != without_headings:
        changes.append("removed_structured_abstract_labels")
    normalized = re.sub(r"\s+", " ", without_labels).strip()
    if normalized != without_labels.strip():
        changes.append("collapsed_abstract_to_single_paragraph")
    return normalized, tuple(changes)


def validate_abstract_prose(value: str, contract: AbstractContract) -> list[str]:
    """Return deterministic venue-genre violations for one abstract value."""

    text = value.strip()
    violations: list[str] = []
    if len(text) < contract.minimum_characters:
        violations.append(
            f"abstract has {len(text)} characters; minimum is {contract.minimum_characters}"
        )
    if len(text) > contract.maximum_characters:
        violations.append(
            f"abstract has {len(text)} characters; maximum is {contract.maximum_characters}"
        )
    paragraphs = [item for item in re.split(r"\n\s*\n", text) if item.strip()]
    if contract.paragraph_count is not None and len(paragraphs) != contract.paragraph_count:
        violations.append(
            f"abstract has {len(paragraphs)} paragraphs; expected {contract.paragraph_count}"
        )
    if contract.style == "unstructured":
        if _ABSTRACT_HEADING_RE.search(text):
            violations.append("unstructured abstract contains a Markdown heading")
        if _ABSTRACT_LABEL_RE.search(text):
            violations.append("unstructured abstract contains structured move labels")
        if re.search(r"(?m)^\s*(?:[-*+] |\d+[.)]\s+)", text):
            violations.append("unstructured abstract contains a list")
    else:
        found = tuple(match.group(0).split(":", 1)[0].strip() for match in _ABSTRACT_LABEL_RE.finditer(text))
        if contract.labels and tuple(item.casefold() for item in found) != tuple(
            item.casefold() for item in contract.labels
        ):
            violations.append("structured abstract labels do not match the venue contract")
    numeric_count = abstract_numeric_token_count(text)
    if numeric_count > contract.maximum_numeric_token_count:
        violations.append(
            "abstract contains "
            f"{numeric_count} numeric tokens; maximum is "
            f"{contract.maximum_numeric_token_count}"
        )
    violations.extend(abstract_reader_facing_violations(text, contract))
    return violations


class PaperWorkflowStage(StrEnum):
    RESEARCH = "research"
    WRITE = "write"
    PRE_REVIEW_INTEGRITY = "pre_review_integrity"
    REVIEW = "review"
    REVISE = "revise"
    RE_REVIEW = "re_review"
    FINAL_INTEGRITY = "final_integrity"
    FINALIZE = "finalize"


@dataclass(frozen=True)
class PaperWorkflowStageSpec:
    stage: PaperWorkflowStage
    purpose: str
    gate: str


PAPER_WORKFLOW: tuple[PaperWorkflowStageSpec, ...] = (
    PaperWorkflowStageSpec(PaperWorkflowStage.RESEARCH, "freeze the research boundary and evidence", "evidence_ready"),
    PaperWorkflowStageSpec(PaperWorkflowStage.WRITE, "draft only from frozen claims and sources", "draft_complete"),
    PaperWorkflowStageSpec(PaperWorkflowStage.PRE_REVIEW_INTEGRITY, "check citations, numbers, claims and structure", "integrity_passed"),
    PaperWorkflowStageSpec(PaperWorkflowStage.REVIEW, "run independent scientific and editorial review", "review_decision_recorded"),
    PaperWorkflowStageSpec(PaperWorkflowStage.REVISE, "repair findings without silently changing frozen evidence", "revision_trace_complete"),
    PaperWorkflowStageSpec(PaperWorkflowStage.RE_REVIEW, "verify that material findings were actually resolved", "material_findings_closed"),
    PaperWorkflowStageSpec(PaperWorkflowStage.FINAL_INTEGRITY, "repeat the integrity audit after revision", "final_integrity_passed"),
    PaperWorkflowStageSpec(PaperWorkflowStage.FINALIZE, "apply venue layout and author-approved metadata", "submission_metadata_approved"),
)


@dataclass(frozen=True)
class PaperSectionSpec:
    key: str
    english_title: str
    chinese_title: str
    level: Literal[2, 3, 4]
    numbered: bool
    required: bool = True
    parent_key: str | None = None
    aliases: tuple[str, ...] = ()

    def title(self, language: Literal["en", "zh"] = "en") -> str:
        return self.chinese_title if language == "zh" else self.english_title


@dataclass(frozen=True)
class SectionNarrativeSpec:
    """Reader-facing rhetorical responsibility for one manuscript section."""

    key: str
    purpose: str
    required_moves: tuple[str, ...]
    prohibited_moves: tuple[str, ...] = ()

    def prompt_contract(self) -> dict[str, object]:
        return {
            "purpose": self.purpose,
            "required_moves": list(self.required_moves),
            "prohibited_moves": list(self.prohibited_moves),
        }


@dataclass(frozen=True)
class ManuscriptNarrativeContract:
    """Cross-section story rules for a conventional empirical paper.

    This contract governs rhetoric, not scientific authority.  Frozen claims,
    numbers and citations remain controlled by their existing evidence
    bindings; this layer only decides where and how they are explained.
    """

    version: int
    sections: tuple[SectionNarrativeSpec, ...]
    maximum_conclusion_numeric_tokens: int = 2
    maximum_conclusion_paragraphs: int = 2
    maximum_nonmethods_governance_terms: int = 8

    def section(self, key: str) -> SectionNarrativeSpec:
        for section in self.sections:
            if section.key == key:
                return section
        raise KeyError(f"unknown narrative section: {key}")

    def prompt_contract(self) -> dict[str, object]:
        return {
            "version": self.version,
            "global_story_rule": (
                "Lead with the scientific problem, the study's response, the "
                "principal finding, and its meaning. Evidence governance is a "
                "supporting method, not the paper's protagonist. State a "
                "limitation once in the section where it belongs rather than "
                "repeating the same defensive boundary throughout the paper."
            ),
            "cross_section_rule": (
                "Introduction motivates; Related Work synthesizes; Methods "
                "makes the comparison reproducible; Results observes; "
                "Discussion interprets; Limitations bounds; Conclusion answers."
            ),
            "sections": {
                section.key: section.prompt_contract()
                for section in self.sections
            },
            "maximum_conclusion_numeric_tokens": self.maximum_conclusion_numeric_tokens,
            "maximum_conclusion_paragraphs": self.maximum_conclusion_paragraphs,
        }


EMPIRICAL_ARTICLE_NARRATIVE = ManuscriptNarrativeContract(
    version=3,
    sections=(
        SectionNarrativeSpec(
            "introduction",
            "Turn a real scientific problem into a precise research question and contribution.",
            ("problem_or_tension", "prior_gap", "study_response", "contribution_and_result_preview"),
            ("workflow_status", "audit_chronicle", "repeated_limitations"),
        ),
        SectionNarrativeSpec(
            "related_work",
            "Synthesize literature by concepts or competing approaches and locate the study's difference.",
            ("conceptual_axes", "agreement_or_tension", "specific_differentiation"),
            ("one_source_per_sentence_catalogue", "internal_source_registry_commentary"),
        ),
        SectionNarrativeSpec(
            "methods",
            "Give the design, data, intervention, outcomes and analysis needed to understand or reproduce the comparison.",
            ("study_design", "data_and_sampling", "arms_or_intervention", "outcomes_and_analysis"),
            ("result_interpretation", "publication_workflow_commentary"),
        ),
        SectionNarrativeSpec(
            "results",
            "Report observations, estimates, uncertainty, prespecified checks and concrete failure cases.",
            (
                "result_orientation",
                "primary_result",
                "uncertainty_and_denominator",
                "prespecified_secondary_results",
            ),
            ("causal_explanation", "policy_defence", "audit_or_writing_agent_commentary"),
        ),
        SectionNarrativeSpec(
            "discussion",
            "Explain the result, compare it with prior work, consider alternatives and state scientific implications.",
            ("interpretation", "alternative_explanations", "relation_to_prior_work", "implications"),
            ("results_table_repetition", "workflow_status"),
        ),
        SectionNarrativeSpec(
            "limitations",
            "Concentrate material scope and validity boundaries and explain their consequence.",
            ("material_boundaries", "consequence_for_inference"),
            ("generic_disclaimer_catalogue", "new_results"),
        ),
        SectionNarrativeSpec(
            "conclusion",
            "Answer the research question, state the scientific meaning, and retain at most one decisive boundary.",
            ("direct_answer", "scientific_meaning"),
            ("new_evidence", "audit_summary", "dense_numeric_recap", "future_work_catalogue"),
        ),
    ),
)


@dataclass(frozen=True)
class PaperStructureContract:
    profile_id: str
    version: int
    sections: tuple[PaperSectionSpec, ...]
    abstract: AbstractContract = UNSTRUCTURED_ABSTRACT
    narrative: ManuscriptNarrativeContract = EMPIRICAL_ARTICLE_NARRATIVE

    def section(self, key: str) -> PaperSectionSpec:
        for section in self.sections:
            if section.key == key:
                return section
        raise KeyError(f"unknown paper section: {key}")

    def section_for_title(self, title: str) -> PaperSectionSpec | None:
        normalized = normalize_heading_title(title)
        for section in self.sections:
            candidates = (section.english_title, section.chinese_title, *section.aliases)
            if normalized in {normalize_heading_title(item) for item in candidates}:
                return section
        return None

    def prompt_contract(self, language: Literal["en", "zh"] = "en") -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "rule": "Return section prose in the structured response; the local renderer owns headings and numbering.",
            "abstract": self.abstract.prompt_contract(),
            "manuscript_narrative": self.narrative.prompt_contract(),
            "sections": [
                {
                    "key": item.key,
                    "title": item.title(language),
                    "level": item.level,
                    "numbered_in_final_document": item.numbered,
                    "parent_key": item.parent_key,
                    "required": item.required,
                }
                for item in self.sections
            ],
        }


GENERIC_JOURNAL_ARTICLE = PaperStructureContract(
    profile_id="generic-journal-article-v1",
    version=3,
    sections=(
        PaperSectionSpec("abstract", "Abstract", "摘要", 2, False),
        PaperSectionSpec("introduction", "Introduction", "引言", 2, True),
        PaperSectionSpec("related_work", "Related Work", "相关工作", 2, True, aliases=("Related Work and Registered Sources",)),
        PaperSectionSpec("methods", "Methods", "方法", 2, True),
        PaperSectionSpec("results", "Results", "结果", 2, True),
        PaperSectionSpec("discussion", "Discussion", "讨论", 2, True),
        PaperSectionSpec("limitations", "Limitations", "局限性", 2, True),
        PaperSectionSpec("conclusion", "Conclusion", "结论", 2, True, aliases=("Conclusions",)),
        PaperSectionSpec(
            "reproducibility_statement",
            "Reproducibility statement",
            "可复现性声明",
            2,
            False,
            required=False,
            aliases=("Reproducibility",),
        ),
        PaperSectionSpec("data_availability", "Data and materials availability", "数据与材料可得性", 2, False),
        PaperSectionSpec("ethics_statement", "Ethics statement", "伦理声明", 2, False),
        PaperSectionSpec("author_contributions", "Author contributions", "作者贡献", 2, False),
        PaperSectionSpec("conflict_of_interest", "Conflicts of interest", "利益冲突", 2, False),
        PaperSectionSpec("funding", "Funding", "资助声明", 2, False),
        PaperSectionSpec("ai_disclosure", "Use of artificial intelligence", "AI 使用披露", 2, False),
        PaperSectionSpec("references", "References", "参考文献", 2, False),
    ),
)


RIRP_RESEARCH_ARTICLE = PaperStructureContract(
    profile_id="rirp-research-article-v1",
    version=2,
    sections=(
        PaperSectionSpec("abstract", "Abstract", "摘要", 2, False),
        PaperSectionSpec("background", "Background", "背景", 2, True, aliases=("Introduction",)),
        PaperSectionSpec("related_work", "Related Work and Registered Sources", "相关工作与注册来源", 3, True, parent_key="background", aliases=("Related Work",)),
        PaperSectionSpec("methods", "Methods", "方法", 2, True),
        PaperSectionSpec("results", "Results", "结果", 2, True),
        PaperSectionSpec("discussion", "Discussion", "讨论", 2, True),
        PaperSectionSpec("limitations", "Limitations", "局限性", 2, True),
        PaperSectionSpec("conclusions", "Conclusions", "结论", 2, True, aliases=("Conclusion",)),
        PaperSectionSpec("abbreviations", "List of abbreviations", "缩略语表", 2, False),
        PaperSectionSpec("declarations", "Declarations", "声明", 2, False),
        PaperSectionSpec("references", "References", "参考文献", 2, False),
    ),
)


PAPER_STRUCTURE_PROFILES = {
    GENERIC_JOURNAL_ARTICLE.profile_id: GENERIC_JOURNAL_ARTICLE,
    RIRP_RESEARCH_ARTICLE.profile_id: RIRP_RESEARCH_ARTICLE,
}


def get_paper_structure_contract(profile_id: str) -> PaperStructureContract:
    try:
        return PAPER_STRUCTURE_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown paper structure profile: {profile_id}") from exc


def normalize_heading_title(title: str) -> str:
    value = re.sub(r"^[\d.]+\s*", "", title.strip())
    return re.sub(r"\s+", " ", value).casefold()


_NARRATIVE_NUMBER_RE = re.compile(
    r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?%?"
)
_GOVERNANCE_RE = re.compile(
    r"\b(?:frozen|freeze|audit|auditor|ledger|governance|immutable|"
    r"evidence[- ]bound|workflow|writing agent|stage[ -]4)\b|"
    r"冻结|审计|账本|治理|不可变|写作代理|第四阶段",
    re.IGNORECASE,
)
_LIMITATION_RE = re.compile(
    r"\b(?:cannot|could not|unable|unavailable|limitation|not claim|"
    r"does not establish|insufficient)\b|不能|无法|缺少|不足|局限|不宣称",
    re.IGNORECASE,
)
_META_PRODUCTION_PHRASES = (
    "evidence-bound complete-manuscript candidate",
    "pending figure slot",
    "pending table slot",
    "pending artifact",
    "callout marks",
    "not supplied for inspection",
    "the system copies",
    "writing agent cannot",
    "authoritative prose statement",
    "supplied bindings",
    "frozen scientific verdict",
    "controlled acceptance fixture",
    "完整论文候选稿",
    "待生成图",
    "待生成表",
    "系统直接写入",
    "写作代理不能",
)
_DECLARATION_PLACEHOLDERS = (
    "[?]",
    "tbd",
    "to be provided",
    "will be provided during review",
    "insert link",
    "insert repository",
    "待补充",
    "稍后提供",
    "填写链接",
)


def _narrative_paragraphs(value: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", item).strip()
        for item in re.split(r"\n\s*\n", value)
        if len(re.sub(r"\s+", " ", item).strip()) >= 120
    ]


def _paragraph_tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z-]{2,}|[\u4e00-\u9fff]{2,}", value)
    }


def compact_conclusion_paragraphs(
    conclusion: str,
    *,
    maximum_paragraphs: int = 2,
) -> str:
    """Repair conclusion segmentation without rewriting scientific prose.

    Paragraph count is a presentation constraint, not a reason to ask a model
    to regenerate the complete manuscript.  When a conclusion contains too
    many paragraphs, retain the leading paragraph(s) verbatim and join the
    remaining paragraphs in order.  The operation changes whitespace only, so
    claims, numbers, citations, and visual callouts remain identical.
    """

    value = str(conclusion or "").strip()
    if not value or maximum_paragraphs < 1:
        return value
    paragraphs = [
        item.strip()
        for item in re.split(r"\n\s*\n", value)
        if item.strip()
    ]
    if len(paragraphs) <= maximum_paragraphs:
        return value
    if maximum_paragraphs == 1:
        return " ".join(paragraphs)
    retained = paragraphs[: maximum_paragraphs - 1]
    retained.append(" ".join(paragraphs[maximum_paragraphs - 1 :]))
    return "\n\n".join(retained)


def centralize_defensive_boundaries(
    introduction: str,
    limitations: str,
    *,
    maximum_introduction_matches: int = 3,
) -> tuple[str, str]:
    """Move excess boundary sentences from Introduction to Limitations.

    This is a lossless editorial operation: sentences are moved verbatim, so
    numbers, citations, and scientific qualifiers remain unchanged.  It is
    deliberately conservative and acts only after the deterministic narrative
    audit has counted more boundary markers than the Introduction permits.
    """

    if len(_LIMITATION_RE.findall(introduction)) <= maximum_introduction_matches:
        return introduction, limitations
    paragraphs = [
        item.strip()
        for item in re.split(r"\n\s*\n", introduction.strip())
        if item.strip()
    ]
    kept_paragraphs: list[str] = []
    moved: list[str] = []
    retained_matches = 0
    for paragraph in paragraphs:
        sentences = [
            item.strip()
            for item in re.split(r"(?<=[.!?。！？])\s+", paragraph)
            if item.strip()
        ]
        kept_sentences: list[str] = []
        for sentence in sentences:
            count = len(_LIMITATION_RE.findall(sentence))
            if count and retained_matches + count > maximum_introduction_matches:
                moved.append(sentence)
            else:
                kept_sentences.append(sentence)
                retained_matches += count
        if kept_sentences:
            kept_paragraphs.append(" ".join(kept_sentences))
    if not moved or not kept_paragraphs:
        return introduction, limitations
    revised_limitations = limitations.strip()
    if revised_limitations:
        revised_limitations += "\n\n"
    revised_limitations += " ".join(moved)
    return "\n\n".join(kept_paragraphs), revised_limitations


def relocate_conclusion_numeric_detail(
    conclusion: str,
    results: str,
    *,
    maximum_numeric_tokens: int = 2,
) -> tuple[str, str]:
    """Move dense quantitative recap sentences from Conclusion to Results.

    Quantities and any attached citations are moved verbatim rather than
    deleted or paraphrased.  The caller can then append a nonnumeric synthesis
    paragraph to the Conclusion without weakening the evidence record.
    """

    if len(_NARRATIVE_NUMBER_RE.findall(conclusion)) <= maximum_numeric_tokens:
        return conclusion, results
    paragraphs = [
        item.strip()
        for item in re.split(r"\n\s*\n", conclusion.strip())
        if item.strip()
    ]
    kept_paragraphs: list[str] = []
    moved: list[str] = []
    retained_numbers = 0
    for paragraph in paragraphs:
        sentences = [
            item.strip()
            for item in re.split(r"(?<=[.!?。！？])\s+", paragraph)
            if item.strip()
        ]
        kept_sentences: list[str] = []
        for sentence in sentences:
            count = len(_NARRATIVE_NUMBER_RE.findall(sentence))
            if count and retained_numbers + count > maximum_numeric_tokens:
                moved.append(sentence)
            else:
                kept_sentences.append(sentence)
                retained_numbers += count
        if kept_sentences:
            kept_paragraphs.append(" ".join(kept_sentences))
    if not moved or not kept_paragraphs:
        return conclusion, results
    revised_results = results.rstrip()
    if revised_results:
        revised_results += "\n\n"
    revised_results += "### Additional registered detail\n\n" + " ".join(moved)
    return "\n\n".join(kept_paragraphs), revised_results


def validate_manuscript_narrative(
    sections: dict[str, str],
    contract: ManuscriptNarrativeContract = EMPIRICAL_ARTICLE_NARRATIVE,
) -> list[str]:
    """Detect audit-report rhetoric and cross-section role collapse.

    The audit is intentionally deterministic and conservative: it rejects
    unmistakable production metadata, dense conclusion recaps, repeated
    defensive boundaries, declaration placeholders, and near-duplicate prose.
    It does not attempt to judge novelty or scientific correctness.
    """

    violations: list[str] = []
    normalized = {key: str(value or "") for key, value in sections.items()}
    for key, value in normalized.items():
        folded = value.casefold()
        leaked = [phrase for phrase in _META_PRODUCTION_PHRASES if phrase in folded]
        if leaked:
            violations.append(
                f"{key} exposes manuscript-production commentary: "
                + ", ".join(leaked)
            )

    conclusion = normalized.get("conclusion", "")
    if len(_NARRATIVE_NUMBER_RE.findall(conclusion)) > contract.maximum_conclusion_numeric_tokens:
        violations.append(
            "conclusion contains a dense numeric recap; retain at most two reader-critical quantities"
        )
    conclusion_paragraphs = [
        item for item in re.split(r"\n\s*\n", conclusion.strip()) if item.strip()
    ]
    if len(conclusion_paragraphs) > contract.maximum_conclusion_paragraphs:
        violations.append(
            "conclusion is over-segmented; answer the research question in one or two paragraphs"
        )

    for key, limit in (("introduction", 3), ("results", 4), ("conclusion", 2)):
        count = len(_LIMITATION_RE.findall(normalized.get(key, "")))
        if count > limit:
            violations.append(
                f"{key} repeats too many defensive boundaries ({count}); centralize them in Limitations"
            )

    nonmethods = "\n".join(
        normalized.get(key, "")
        for key in ("introduction", "results", "discussion", "conclusion")
    )
    governance_count = len(_GOVERNANCE_RE.findall(nonmethods))
    if governance_count > contract.maximum_nonmethods_governance_terms:
        violations.append(
            "reader-facing scientific sections overuse governance vocabulary "
            f"({governance_count}); keep operational controls in Methods or Limitations"
        )

    for key in (
        "data_availability",
        "ethics_statement",
        "author_contributions",
        "conflict_of_interest",
        "funding",
        "ai_disclosure",
    ):
        folded = normalized.get(key, "").casefold()
        placeholders = [item for item in _DECLARATION_PLACEHOLDERS if item in folded]
        if placeholders:
            violations.append(
                f"{key} contains unresolved submission placeholders: "
                + ", ".join(placeholders)
            )

    paragraph_index: list[tuple[str, str, set[str]]] = []
    for key in (
        "introduction",
        "related_work",
        "methods",
        "results",
        "discussion",
        "limitations",
        "conclusion",
    ):
        for paragraph in _narrative_paragraphs(normalized.get(key, "")):
            tokens = _paragraph_tokens(paragraph)
            if len(tokens) >= 12:
                paragraph_index.append((key, paragraph, tokens))
    for index, (left_key, _left, left_tokens) in enumerate(paragraph_index):
        for right_key, _right, right_tokens in paragraph_index[index + 1 :]:
            if left_key == right_key:
                continue
            overlap = len(left_tokens & right_tokens) / max(
                1, len(left_tokens | right_tokens)
            )
            if overlap >= 0.78:
                violations.append(
                    f"near-duplicate prose is repeated across {left_key} and {right_key}; give each section a distinct role"
                )
                break

    return list(dict.fromkeys(violations))


def markdown_heading(section: PaperSectionSpec, language: Literal["en", "zh"] = "en") -> str:
    return f"{'#' * section.level} {section.title(language)}"


def latex_heading(command: str, title: str, *, numbered: bool) -> str:
    star = "" if numbered else "*"
    return rf"\{command}{star}{{{title}}}"


def validate_markdown_structure(markdown: str, contract: PaperStructureContract) -> list[str]:
    """Return deterministic structure violations without judging manuscript prose."""

    found: list[tuple[int, PaperSectionSpec]] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?m)^(#{2,4})\s+(.+?)\s*$", markdown):
        section = contract.section_for_title(match.group(2))
        if section is None:
            continue
        if section.key in seen:
            duplicates.append(section.key)
        seen.add(section.key)
        found.append((len(match.group(1)), section))

    violations = [f"duplicate paper section: {key}" for key in sorted(set(duplicates))]
    found_by_key = {section.key: (index, level) for index, (level, section) in enumerate(found)}
    for section in contract.sections:
        if section.required and section.key not in found_by_key:
            violations.append(f"missing required paper section: {section.key}")
        elif section.key in found_by_key and found_by_key[section.key][1] != section.level:
            violations.append(
                f"wrong heading level for {section.key}: expected H{section.level}, got H{found_by_key[section.key][1]}"
            )
    ordered = [found_by_key[item.key][0] for item in contract.sections if item.key in found_by_key]
    if ordered != sorted(ordered):
        violations.append("paper sections are out of contract order")
    abstract_entry = found_by_key.get("abstract")
    if abstract_entry is not None:
        abstract_position = found[abstract_entry[0]][1]
        heading = markdown_heading(abstract_position, "zh")
        match = re.search(
            rf"(?s)^{re.escape(heading)}\s*$\s*(.*?)(?=^##\s+|\Z)",
            markdown,
            re.MULTILINE,
        )
        if match is None:
            english_heading = markdown_heading(abstract_position, "en")
            match = re.search(
                rf"(?s)^{re.escape(english_heading)}\s*$\s*(.*?)(?=^##\s+|\Z)",
                markdown,
                re.MULTILINE,
            )
        if match is not None:
            abstract_prose, _ = split_abstract_keywords(match.group(1))
            violations.extend(validate_abstract_prose(abstract_prose, contract.abstract))
    return violations
