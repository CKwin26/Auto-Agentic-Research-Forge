from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


GATE_ID = "manuscript-depth-v1"

_WORD_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]+(?:[-'][A-Za-z]+)*(?![A-Za-z])")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?%?(?![A-Za-z])")
_LATEX_SECTION_RE = re.compile(r"(?m)^\\section\*?\{([^{}]+)\}")
_LATEX_SUBSECTION_RE = re.compile(r"(?m)^\\subsection\*?\{([^{}]+)\}")
_MARKDOWN_SECTION_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
_MARKDOWN_SUBSECTION_RE = re.compile(r"(?m)^###\s+(.+?)\s*$")


_SECTION_ALIASES = {
    "abstract": "abstract",
    "摘要": "abstract",
    "introduction": "introduction",
    "引言": "introduction",
    "related work": "related_work",
    "related work and registered sources": "related_work",
    "background": "related_work",
    "相关工作": "related_work",
    "文献综述": "related_work",
    "methods": "methods",
    "method": "methods",
    "methodology": "methods",
    "方法": "methods",
    "研究方法": "methods",
    "results": "results",
    "findings": "results",
    "结果": "results",
    "实验结果": "results",
    "discussion": "discussion",
    "讨论": "discussion",
    "limitations": "limitations",
    "threats to validity": "limitations",
    "局限性": "limitations",
    "效度威胁": "limitations",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "结论": "conclusion",
    "references": "references",
    "bibliography": "references",
    "参考文献": "references",
}


@dataclass(frozen=True)
class DepthProfile:
    profile_id: str
    minimum_total: int
    section_minimums: dict[str, int]
    paragraph_minimums: dict[str, int]
    subsection_minimums: dict[str, int]
    minimum_references: int
    minimum_related_work_citations: int
    minimum_result_numbers: int
    maximum_duplicate_paragraph_ratio: float = 0.08
    abstract_maximum: int | None = None
    unit: Literal["words", "han_chars"] = "words"
    required_sections: tuple[str, ...] = (
        "abstract",
        "introduction",
        "related_work",
        "methods",
        "results",
        "discussion",
        "conclusion",
    )


ENGLISH_JOURNAL_ARTICLE = DepthProfile(
    profile_id="journal-article-en-v1",
    minimum_total=6000,
    section_minimums={
        "abstract": 150,
        "introduction": 650,
        "related_work": 750,
        "methods": 1200,
        "results": 1000,
        "discussion": 1000,
        "conclusion": 150,
    },
    paragraph_minimums={
        "introduction": 5,
        "related_work": 6,
        "methods": 8,
        "results": 7,
        "discussion": 7,
        "conclusion": 2,
    },
    subsection_minimums={"methods": 4, "results": 3, "discussion": 3},
    minimum_references=15,
    minimum_related_work_citations=8,
    minimum_result_numbers=15,
    abstract_maximum=350,
)


CHINESE_JOURNAL_ARTICLE = DepthProfile(
    profile_id="journal-article-zh-v1",
    minimum_total=10_000,
    section_minimums={
        "abstract": 250,
        "introduction": 1000,
        "related_work": 1200,
        "methods": 2000,
        "results": 1600,
        "discussion": 1600,
        "conclusion": 250,
    },
    paragraph_minimums={
        "introduction": 5,
        "related_work": 6,
        "methods": 8,
        "results": 7,
        "discussion": 7,
        "conclusion": 2,
    },
    subsection_minimums={"methods": 4, "results": 3, "discussion": 3},
    minimum_references=15,
    minimum_related_work_citations=8,
    minimum_result_numbers=15,
    abstract_maximum=600,
    unit="han_chars",
)


ENGLISH_SHORT_REPORT = DepthProfile(
    profile_id="short-report-en-v1",
    minimum_total=3000,
    section_minimums={
        "abstract": 120,
        "introduction": 350,
        "related_work": 350,
        "methods": 650,
        "results": 550,
        "discussion": 500,
        "conclusion": 100,
    },
    paragraph_minimums={
        "introduction": 3,
        "related_work": 3,
        "methods": 5,
        "results": 4,
        "discussion": 4,
        "conclusion": 1,
    },
    subsection_minimums={"methods": 2, "results": 2, "discussion": 2},
    minimum_references=8,
    minimum_related_work_citations=4,
    minimum_result_numbers=8,
    abstract_maximum=350,
)

CHINESE_SHORT_REPORT = DepthProfile(
    profile_id="short-report-zh-v1",
    minimum_total=6_000,
    section_minimums={
        "abstract": 180,
        "introduction": 700,
        "related_work": 650,
        "methods": 1400,
        "results": 1100,
        "discussion": 1100,
        "conclusion": 180,
    },
    paragraph_minimums={
        "introduction": 3,
        "related_work": 3,
        "methods": 5,
        "results": 4,
        "discussion": 4,
        "conclusion": 1,
    },
    subsection_minimums={"methods": 3, "results": 2, "discussion": 2},
    minimum_references=8,
    minimum_related_work_citations=4,
    minimum_result_numbers=8,
    abstract_maximum=500,
    unit="han_chars",
)


@dataclass
class SectionMetrics:
    key: str
    heading: str
    count: int
    paragraphs: int
    subsections: int
    citations: int
    numeric_tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "heading": self.heading,
            "count": self.count,
            "paragraphs": self.paragraphs,
            "subsections": self.subsections,
            "citations": self.citations,
            "numeric_tokens": self.numeric_tokens,
        }


@dataclass
class ManuscriptDepthReport:
    path: str
    format: str
    language: str
    profile: str
    unit: str
    total_count: int
    reference_count: int
    duplicate_paragraph_ratio: float
    sections: dict[str, SectionMetrics]
    checks: dict[str, bool]
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: int = 1
    gate_id: str = GATE_ID
    source_sha256: str = ""

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "gate_id": self.gate_id,
            "path": self.path,
            "source_sha256": self.source_sha256,
            "format": self.format,
            "language": self.language,
            "profile": self.profile,
            "unit": self.unit,
            "passed": self.passed,
            "total_count": self.total_count,
            "reference_count": self.reference_count,
            "duplicate_paragraph_ratio": self.duplicate_paragraph_ratio,
            "sections": {key: value.as_dict() for key, value in self.sections.items()},
            "checks": self.checks,
            "violations": self.violations,
            "warnings": self.warnings,
        }


def _canonical_heading(value: str) -> str | None:
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    normalized = re.sub(r"^[\d.\s]+", "", normalized)
    return _SECTION_ALIASES.get(normalized)


def _strip_latex(text: str, *, drop_floats: bool = True) -> str:
    text = re.sub(r"(?m)(?<!\\)%.*$", " ", text)
    environments = ["equation", "equation*", "align", "align*", "displaymath", "verbatim"]
    if drop_floats:
        environments.extend(["table", "table*", "figure", "figure*"])
    for environment in environments:
        text = re.sub(
            rf"(?s)\\begin\{{{re.escape(environment)}\}}.*?\\end\{{{re.escape(environment)}\}}",
            " ",
            text,
        )
    text = re.sub(r"(?s)\$\$.*?\$\$|\\\[.*?\\\]|\$[^$]*\$", " ", text)
    text = re.sub(
        r"\\(?:cite|citep|citet|ref|eqref|label|url|path)\*?(?:\[[^\]]*\])?\{[^{}]*\}",
        " ",
        text,
    )
    text = re.sub(r"\\href\{[^{}]*\}\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:begin|end)\{[^{}]*\}", " ", text)
    text = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", " ", text)
    text = re.sub(r"[{}&_~^|]", " ", text)
    text = re.sub(r"\\.", " ", text)
    return text


def _strip_markdown(text: str, *, drop_tables: bool = True) -> str:
    text = re.sub(r"(?s)```.*?```|~~~.*?~~~", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    if drop_tables:
        text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("|"))
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*(?:[-*+] |\d+[.)]\s+)", "", text)
    text = re.sub(r"[*_>~]", " ", text)
    return text


def _count(text: str, language: str) -> int:
    return len(_HAN_RE.findall(text)) if language == "zh" else len(_WORD_RE.findall(text))


def _paragraphs(cleaned: str, language: str) -> list[str]:
    candidates = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"\n\s*\n", cleaned)]
    minimum = 30 if language == "zh" else 20
    return [item for item in candidates if _count(item, language) >= minimum]


def _duplicate_paragraph_ratio(paragraphs: list[str]) -> float:
    if not paragraphs:
        return 0.0
    normalized = [re.sub(r"\W+", " ", item.lower()).strip() for item in paragraphs]
    duplicate_count = len(normalized) - len(set(normalized))
    return duplicate_count / len(normalized)


def _detect_language(text: str) -> Literal["en", "zh"]:
    han = len(_HAN_RE.findall(text))
    words = len(_WORD_RE.findall(text))
    return "zh" if han > words * 2 else "en"


def _extract_latex(text: str) -> tuple[dict[str, tuple[str, str]], int, str]:
    document = text.split(r"\begin{document}", 1)[-1]
    reference_count = len(re.findall(r"\\bibitem\{", document))
    bibliography = re.search(
        r"(?s)\\begin\{thebibliography\}.*?\\end\{thebibliography\}", document
    )
    if bibliography:
        document_without_refs = document[: bibliography.start()] + document[bibliography.end() :]
    else:
        document_without_refs = document
    sections: dict[str, tuple[str, str]] = {}
    abstract = re.search(r"(?s)\\begin\{abstract\}(.*?)\\end\{abstract\}", document)
    if abstract:
        sections["abstract"] = ("Abstract", abstract.group(1))
    matches = list(_LATEX_SECTION_RE.finditer(document_without_refs))
    for index, match in enumerate(matches):
        heading = match.group(1)
        key = _canonical_heading(heading)
        if key is None or key == "references":
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(document_without_refs)
        content = document_without_refs[match.end() : end]
        if key in sections:
            existing_heading, existing_content = sections[key]
            sections[key] = (existing_heading, existing_content + "\n\n" + content)
        else:
            sections[key] = (heading, content)
    return sections, reference_count, document_without_refs


def _extract_markdown(text: str) -> tuple[dict[str, tuple[str, str]], int, str]:
    sections: dict[str, tuple[str, str]] = {}
    matches = list(_MARKDOWN_SECTION_RE.finditer(text))
    reference_count = 0
    narrative_parts: list[str] = []
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        key = _canonical_heading(heading)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[match.end() : end]
        if key == "references":
            reference_count = len(re.findall(r"(?m)^\s*(?:[-*+] |\d+[.)]\s+)", content))
            continue
        if key is not None:
            if key in sections:
                existing_heading, existing_content = sections[key]
                sections[key] = (existing_heading, existing_content + "\n\n" + content)
            else:
                sections[key] = (heading, content)
            narrative_parts.append(content)
    return sections, reference_count, "\n\n".join(narrative_parts)


def _profile(name: str, language: str) -> DepthProfile:
    normalized = name.strip().lower().replace("_", "-")
    if normalized == "journal-article":
        return CHINESE_JOURNAL_ARTICLE if language == "zh" else ENGLISH_JOURNAL_ARTICLE
    if normalized == "short-report" and language == "en":
        return ENGLISH_SHORT_REPORT
    if normalized == "short-report" and language == "zh":
        return CHINESE_SHORT_REPORT
    raise ValueError(f"unknown manuscript depth profile: {name}")


def audit_manuscript_depth(
    path: str | Path,
    *,
    profile: str = "journal-article",
    language: Literal["auto", "en", "zh"] = "auto",
    report_path: str | Path | None = None,
) -> ManuscriptDepthReport:
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"manuscript not found: {source}")
    text = source.read_text(encoding="utf-8")
    suffix = source.suffix.lower()
    if suffix == ".tex":
        format_name = "latex"
        raw_sections, reference_count, narrative = _extract_latex(text)
        strip = _strip_latex
        citation_pattern = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])?\{[^{}]+\}")
        subsection_pattern = _LATEX_SUBSECTION_RE
    elif suffix in {".md", ".markdown"}:
        format_name = "markdown"
        raw_sections, reference_count, narrative = _extract_markdown(text)
        strip = _strip_markdown
        citation_pattern = re.compile(r"\[[A-Za-z0-9][A-Za-z0-9._:-]{0,120}\]")
        subsection_pattern = _MARKDOWN_SUBSECTION_RE
    else:
        raise ValueError("manuscript depth audit supports only .tex, .md, and .markdown")

    detected_language = (
        _detect_language(strip(narrative)) if language == "auto" else language
    )
    selected_profile = _profile(profile, detected_language)
    metrics: dict[str, SectionMetrics] = {}
    all_paragraphs: list[str] = []
    for key, (heading, raw_content) in raw_sections.items():
        cleaned = strip(raw_content)
        paragraphs = _paragraphs(cleaned, detected_language)
        all_paragraphs.extend(paragraphs)
        metrics[key] = SectionMetrics(
            key=key,
            heading=heading,
            count=_count(cleaned, detected_language),
            paragraphs=len(paragraphs),
            subsections=len(subsection_pattern.findall(raw_content)),
            citations=len(citation_pattern.findall(raw_content)),
            numeric_tokens=len(_NUMBER_RE.findall(raw_content)),
        )

    total_count = sum(
        section.count for key, section in metrics.items() if key != "references"
    )
    duplicate_ratio = _duplicate_paragraph_ratio(all_paragraphs)
    checks: dict[str, bool] = {}
    violations: list[str] = []

    def check(name: str, passed: bool, message: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            violations.append(message)

    for key in selected_profile.required_sections:
        check(
            f"section_present_{key}",
            key in metrics,
            f"required section is missing: {key}",
        )
    check(
        "minimum_total_depth",
        total_count >= selected_profile.minimum_total,
        f"narrative depth is {total_count} {selected_profile.unit}; minimum is {selected_profile.minimum_total}",
    )
    for key, minimum in selected_profile.section_minimums.items():
        actual = metrics.get(key).count if key in metrics else 0
        check(
            f"minimum_section_depth_{key}",
            actual >= minimum,
            f"section {key} has {actual} {selected_profile.unit}; minimum is {minimum}",
        )
    if selected_profile.abstract_maximum is not None and "abstract" in metrics:
        check(
            "maximum_abstract_depth",
            metrics["abstract"].count <= selected_profile.abstract_maximum,
            f"abstract has {metrics['abstract'].count} {selected_profile.unit}; maximum is {selected_profile.abstract_maximum}",
        )
    for key, minimum in selected_profile.paragraph_minimums.items():
        actual = metrics.get(key).paragraphs if key in metrics else 0
        check(
            f"minimum_paragraphs_{key}",
            actual >= minimum,
            f"section {key} has {actual} substantive paragraphs; minimum is {minimum}",
        )
    for key, minimum in selected_profile.subsection_minimums.items():
        actual = metrics.get(key).subsections if key in metrics else 0
        check(
            f"minimum_subsections_{key}",
            actual >= minimum,
            f"section {key} has {actual} subsections; minimum is {minimum}",
        )
    check(
        "minimum_references",
        reference_count >= selected_profile.minimum_references,
        f"reference list has {reference_count} entries; minimum is {selected_profile.minimum_references}",
    )
    related_citations = metrics.get("related_work").citations if "related_work" in metrics else 0
    check(
        "minimum_related_work_citations",
        related_citations >= selected_profile.minimum_related_work_citations,
        f"related work has {related_citations} citation commands; minimum is {selected_profile.minimum_related_work_citations}",
    )
    result_numbers = metrics.get("results").numeric_tokens if "results" in metrics else 0
    check(
        "minimum_result_numeric_grounding",
        result_numbers >= selected_profile.minimum_result_numbers,
        f"results have {result_numbers} numeric tokens; minimum is {selected_profile.minimum_result_numbers}",
    )
    check(
        "maximum_duplicate_paragraph_ratio",
        duplicate_ratio <= selected_profile.maximum_duplicate_paragraph_ratio,
        (
            f"exact duplicate paragraph ratio is {duplicate_ratio:.3f}; maximum is "
            f"{selected_profile.maximum_duplicate_paragraph_ratio:.3f}"
        ),
    )

    report = ManuscriptDepthReport(
        path=str(source),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        format=format_name,
        language=detected_language,
        profile=selected_profile.profile_id,
        unit=selected_profile.unit,
        total_count=total_count,
        reference_count=reference_count,
        duplicate_paragraph_ratio=duplicate_ratio,
        sections=metrics,
        checks=checks,
        violations=list(dict.fromkeys(violations)),
        warnings=[
            "This is an internal anti-compression gate, not a venue word-limit rule.",
            "Passing length and structure checks does not establish scientific validity or claim support.",
        ],
    )
    if report_path is not None:
        destination = Path(report_path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return report


def enforce_manuscript_depth(
    path: str | Path,
    *,
    profile: str = "journal-article",
    language: Literal["auto", "en", "zh"] = "auto",
    report_path: str | Path | None = None,
) -> ManuscriptDepthReport:
    report = audit_manuscript_depth(
        path,
        profile=profile,
        language=language,
        report_path=report_path,
    )
    if not report.passed:
        raise ValueError("manuscript depth gate failed: " + "; ".join(report.violations))
    return report
