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
class PaperStructureContract:
    profile_id: str
    version: int
    sections: tuple[PaperSectionSpec, ...]
    abstract: AbstractContract = UNSTRUCTURED_ABSTRACT

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
    version=2,
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
