from __future__ import annotations

"""Deterministic migration of reviewed drafts into the canonical paper form.

The canonical manuscript is deliberately venue-neutral.  Venue adapters may
later turn its one-paragraph abstract or declarations into a journal-specific
layout, but they must not feed those layout choices back into the scientific
working draft.
"""

import re
from pathlib import Path
from typing import Literal

from pydantic import Field

from .manuscript_depth import audit_manuscript_depth
from .models import StrictModel, utc_now
from .paper_pipeline import (
    GENERIC_JOURNAL_ARTICLE,
    normalize_unstructured_abstract,
    split_abstract_keywords,
    validate_markdown_structure,
)
from .paper_typesetting import write_submission_latex
from .storage import sha256_file, write_json_atomic


_INTERNAL_COMMENT_RE = re.compile(r"<!--(?:block|ref|anchor):.*?-->\s*", re.DOTALL)
_H2_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
_CITATION_CLUSTER_RE = re.compile(r"\[([^\]\n]+)\]")
_DECLARATION_RE = re.compile(
    r"(?ms)^\*\*(Data and code availability|Data and materials availability|Ethics|"
    r"Author contributions|Funding and conflicts|Funding|Conflicts of interest|AI disclosure)\.\*\*\s*"
    r"(.*?)(?=\n\s*\n\*\*|\Z)"
)


def _expand_citation_clusters(value: str) -> tuple[str, bool]:
    """Expand compact authoring notation into renderer-safe citation tokens."""

    changed = False

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        raw_tokens = [item.strip() for item in match.group(1).split(",")]
        rendered: list[str] = []
        for token in raw_tokens:
            range_match = re.fullmatch(r"R(\d+)\s*[–-]\s*R?(\d+)", token)
            if range_match:
                start, end = map(int, range_match.groups())
                if start > end or end - start > 50:
                    return match.group(0)
                rendered.extend(f"[R{index}]" for index in range(start, end + 1))
                continue
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{1,120}", token):
                return match.group(0)
            rendered.append(f"[{token}]")
        if len(rendered) <= 1:
            return match.group(0)
        changed = True
        return " ".join(rendered)

    return _CITATION_CLUSTER_RE.sub(replace, value), changed


def _split_declarations(content: str) -> list[tuple[str, str]]:
    titles = {
        "Data and code availability": "Data and materials availability",
        "Data and materials availability": "Data and materials availability",
        "Ethics": "Ethics statement",
        "Author contributions": "Author contributions",
        "Funding": "Funding",
        "Conflicts of interest": "Conflicts of interest",
        "AI disclosure": "Use of artificial intelligence",
    }
    sections: list[tuple[str, str]] = []
    for match in _DECLARATION_RE.finditer(content.strip()):
        label, prose = match.groups()
        prose = prose.strip()
        if label == "Funding and conflicts":
            sections.extend(
                [
                    ("Conflicts of interest", prose),
                    ("Funding", prose),
                ]
            )
        else:
            sections.append((titles[label], prose))
    return sections


class CanonicalizationReport(StrictModel):
    schema_version: int = 1
    canonicalized_at: str = Field(default_factory=utc_now)
    source_path: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    manuscript_path: str
    manuscript_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    latex_path: str
    latex_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    structure_profile_id: str
    abstract_style: Literal["unstructured"] = "unstructured"
    abstract_paragraph_count: int = 1
    removed_sections: list[str]
    transformations: list[str]
    structure_violations: list[str]
    depth_passed: bool
    depth_violations: list[str]
    passed: bool


def _split_h2_sections(markdown: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(_H2_RE.finditer(markdown))
    if not matches:
        raise ValueError("manuscript contains no H2 paper sections")
    preamble = markdown[: matches[0].start()].rstrip()
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections.append((match.group(1).strip(), markdown[match.end() : end].strip()))
    return preamble, sections


def canonicalize_reviewed_manuscript(
    source: str | Path,
    *,
    output: str | Path | None = None,
    report_path: str | Path | None = None,
    language: Literal["en", "zh"] = "en",
) -> CanonicalizationReport:
    """Create a venue-neutral, typeset canonical paper without rewriting claims."""

    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"manuscript not found: {source_path}")
    output_path = (
        Path(output).resolve()
        if output is not None
        else source_path.with_name(source_path.stem + ".canonical.md")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    original = source_path.read_text(encoding="utf-8")
    marker_free = _INTERNAL_COMMENT_RE.sub("", original)
    preamble, raw_sections = _split_h2_sections(marker_free)
    transformations = ["removed_internal_block_markers"] if marker_free != original else []
    removed_sections: list[str] = []
    sections: list[tuple[str, str]] = []
    references: tuple[str, str] | None = None
    declarations: list[tuple[str, str]] = []

    for title, content in raw_sections:
        normalized_title = title.strip().casefold()
        if normalized_title in {"status", "research status", "研究状态"}:
            removed_sections.append(title)
            transformations.append("removed_internal_status_section")
            continue
        if normalized_title in {"declarations", "声明"}:
            declarations = _split_declarations(content)
            if not declarations:
                raise ValueError("declarations section could not be split into canonical fields")
            removed_sections.append(title)
            transformations.append("split_declarations_into_canonical_sections")
            continue
        spec = GENERIC_JOURNAL_ARTICLE.section_for_title(title)
        if spec is not None and spec.key == "abstract":
            prose, keywords = split_abstract_keywords(content)
            prose, abstract_changes = normalize_unstructured_abstract(prose)
            content = prose
            if keywords:
                label = "关键词" if language == "zh" else "Keywords"
                content += f"\n\n**{label}:** {keywords}"
            transformations.extend(abstract_changes)
            transformations.append("enforced_single_paragraph_unstructured_abstract")
        if spec is not None and spec.key == "references":
            references = (title, content)
            continue
        sections.append((title, content))

    if references is None:
        raise ValueError("manuscript references section is missing")
    sections.extend(declarations)
    sections.append(references)
    if raw_sections[-1][0] != references[0]:
        transformations.append("moved_references_to_document_end")

    canonical = preamble.strip() + "\n\n"
    canonical += "\n\n".join(
        f"## {title}\n\n{content.strip()}" for title, content in sections
    )
    canonical = re.sub(r"\n{3,}", "\n\n", canonical).strip() + "\n"
    canonical, citations_expanded = _expand_citation_clusters(canonical)
    if citations_expanded:
        transformations.append("expanded_compact_citation_clusters")
    output_path.write_text(canonical, encoding="utf-8", newline="\n")

    structure_violations = validate_markdown_structure(
        canonical, GENERIC_JOURNAL_ARTICLE
    )
    latex_path = write_submission_latex(
        output_path,
        contract=GENERIC_JOURNAL_ARTICLE,
        language=language,
    )
    depth = audit_manuscript_depth(
        output_path,
        profile="journal-article",
        language=language,
        report_path=output_path.with_suffix(".depth.json"),
    )
    report = CanonicalizationReport(
        source_path=str(source_path),
        source_sha256=sha256_file(source_path),
        manuscript_path=str(output_path),
        manuscript_sha256=sha256_file(output_path),
        latex_path=str(latex_path),
        latex_sha256=sha256_file(latex_path),
        structure_profile_id=GENERIC_JOURNAL_ARTICLE.profile_id,
        removed_sections=removed_sections,
        transformations=list(dict.fromkeys(transformations)),
        structure_violations=structure_violations,
        depth_passed=depth.passed,
        depth_violations=depth.violations,
        passed=not structure_violations and depth.passed,
    )
    destination = (
        Path(report_path).resolve()
        if report_path is not None
        else output_path.with_suffix(".canonicalization.json")
    )
    write_json_atomic(destination, report)
    return report
