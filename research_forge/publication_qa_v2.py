from __future__ import annotations

"""Deterministic, fail-closed publication QA for canonical Stage 4.

This gate does not judge scientific novelty and cannot change a frozen verdict.
It checks whether a manuscript faithfully and completely presents the artifacts
that Stage 4 was authorized to read.
"""

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .models import StrictModel, utc_now
from .scientific_figure_engine import FigureAcceptanceReport
from .storage import sha256_file


AuditSeverity = Literal["critical", "major", "minor"]


class PublicationQAFinding(StrictModel):
    finding_id: str = Field(pattern=r"^publication-qa-[a-z0-9-]{2,160}$")
    audit: Literal[
        "manuscript_source",
        "citation_resolution",
        "numerical_consistency",
        "statistical_semantics",
        "figure_table",
        "language_style",
        "compiled_pdf",
    ]
    severity: AuditSeverity
    code: str
    message: str
    location: str | None = None
    required_action: str


class _AuditBase(StrictModel):
    schema_version: int = 2
    passed: bool
    checks: dict[str, bool]
    findings: list[PublicationQAFinding]
    audited_at: str = Field(default_factory=utc_now)


class ManuscriptSourceAudit(_AuditBase):
    source_path: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CitationResolutionAudit(_AuditBase):
    cited_keys: list[str]
    registry_keys: list[str]
    unresolved_keys: list[str]


class NumericalSurfaceValue(StrictModel):
    surface_id: str
    artifact_id: str
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    field: str
    value: int | float | str
    unit: str | None = None
    direction: str | None = None
    display_precision: int | None = Field(default=None, ge=0, le=15)


class NumericalConsistencyAudit(_AuditBase):
    surfaces: list[NumericalSurfaceValue]
    artifact_ids: list[str]


class StatisticalSemanticsAudit(_AuditBase):
    figure_ids: list[str]


class FigureTableAudit(_AuditBase):
    planned_figure_ids: list[str]
    accepted_figure_ids: list[str]
    referenced_figure_ids: list[str]


class LanguageStyleAudit(_AuditBase):
    source_language: str = "English"


class CompiledPDFAudit(_AuditBase):
    pdf_path: str
    pdf_sha256: str | None = None
    page_count: int = Field(ge=0)
    rendered_page_count: int = Field(ge=0)


class PublicationAcceptanceReport(StrictModel):
    schema_version: int = 2
    manuscript_id: str
    scientific_verdict: str
    manuscript_source: ManuscriptSourceAudit
    citation_resolution: CitationResolutionAudit
    numerical_consistency: NumericalConsistencyAudit
    statistical_semantics: StatisticalSemanticsAudit
    figure_table: FigureTableAudit
    language_style: LanguageStyleAudit
    compiled_pdf: CompiledPDFAudit
    critical_findings: list[PublicationQAFinding]
    major_findings: list[PublicationQAFinding]
    publication_ready: bool
    scientific_verdict_unchanged: Literal[True] = True
    generated_at: str = Field(default_factory=utc_now)


_PLACEHOLDER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("TODO", re.compile(r"\bTODO\b", re.IGNORECASE)),
    ("TBD", re.compile(r"\bTBD\b", re.IGNORECASE)),
    ("PLACEHOLDER", re.compile(r"\bplaceholder\b", re.IGNORECASE)),
    ("UNRESOLVED", re.compile(r"\bunresolved\b", re.IGNORECASE)),
    ("PENDING_CALLOUT", re.compile(r"pending\s+(?:figure|table|visual)?\s*callout", re.IGNORECASE)),
    ("CITATION_NEEDED", re.compile(r"citation\s+needed", re.IGNORECASE)),
    ("QUESTION_MARK_CITATION", re.compile(r"\[(?:\s*\d+\s*,\s*)?\?\s*(?:,\s*\?)?\]")),
    ("EMPTY_CITATION", re.compile(r"\\cite\s*\{\s*\}")),
    ("TEMPLATE_VARIABLE", re.compile(r"\{\{[^{}]+\}\}|\$\{[^{}]+\}")),
)

_LOCAL_PATH = re.compile(
    r"(?:\b[A-Za-z]:\\+(?:Users|Documents|AppData|Temp)\\+|/(?:Users|home|tmp)/)",
    re.IGNORECASE,
)
_SNAKE_CASE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){1,}\b")
_RUN_ID = re.compile(r"\b(?:run|task|study|artifact|step)-[a-z0-9-]{6,}\b", re.IGNORECASE)
_ADJACENT_DUPLICATE = re.compile(r"\b([A-Za-z]{2,})\s+\1\b", re.IGNORECASE)
_BAD_ARTICLE = re.compile(r"\bAn\s+not\b", re.IGNORECASE)


def _finding(
    audit: PublicationQAFinding.__annotations__["audit"],
    code: str,
    message: str,
    *,
    severity: AuditSeverity = "critical",
    location: str | None = None,
    action: str = "correct the manuscript and rerun Publication QA",
) -> PublicationQAFinding:
    safe = re.sub(r"[^a-z0-9]+", "-", f"{audit}-{code}".casefold()).strip("-")
    return PublicationQAFinding(
        finding_id=f"publication-qa-{safe}",
        audit=audit,
        severity=severity,
        code=code,
        message=message,
        location=location,
        required_action=action,
    )


def _sections(text: str) -> list[tuple[str, str]]:
    heading = re.compile(
        r"(?m)^(?:#{1,6}\s+|\\(?:section|subsection|subsubsection)\*?\{)([^}\n]+)\}?\s*$"
    )
    matches = list(heading.finditer(text))
    return [
        (
            match.group(1).strip(),
            text[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(text)].strip(),
        )
        for index, match in enumerate(matches)
    ]


def audit_manuscript_source_v2(path: str | Path) -> ManuscriptSourceAudit:
    source = Path(path).resolve()
    text = source.read_text(encoding="utf-8", errors="replace")
    findings: list[PublicationQAFinding] = []
    checks: dict[str, bool] = {}
    for code, pattern in _PLACEHOLDER_PATTERNS:
        matched = pattern.search(text)
        checks[f"no_{code.casefold()}"] = matched is None
        if matched:
            findings.append(
                _finding(
                    "manuscript_source",
                    code,
                    f"manuscript contains prohibited unresolved token: {matched.group(0)}",
                    location=f"character {matched.start()}",
                )
            )
    local = _LOCAL_PATH.search(text)
    checks["no_local_absolute_path"] = local is None
    if local:
        findings.append(
            _finding(
                "manuscript_source",
                "LOCAL_ABSOLUTE_PATH",
                "manuscript exposes a local absolute path",
                location=f"character {local.start()}",
            )
        )
    run_id = _RUN_ID.search(text)
    checks["no_internal_run_id"] = run_id is None
    if run_id:
        findings.append(
            _finding(
                "manuscript_source",
                "INTERNAL_RUN_ID",
                f"manuscript exposes internal identifier {run_id.group(0)}",
                location=f"character {run_id.start()}",
            )
        )
    # LaTeX labels/cite keys legitimately use snake_case.  Strip control syntax
    # before auditing reader-facing prose.
    prose = re.sub(r"\\(?:cite|ref|label|includegraphics)\*?(?:\[[^\]]*\])?\{[^{}]*\}", " ", text)
    snake = _SNAKE_CASE.search(prose)
    checks["no_reader_facing_snake_case"] = snake is None
    if snake:
        findings.append(
            _finding(
                "manuscript_source",
                "READER_FACING_SNAKE_CASE",
                f"reader-facing prose exposes internal token {snake.group(0)}",
                location=f"character {snake.start()}",
            )
        )
    empty_sections = [title for title, body in _sections(text) if len(re.sub(r"\s+", " ", body)) < 40]
    checks["no_empty_sections"] = not empty_sections
    for title in empty_sections:
        findings.append(
            _finding(
                "manuscript_source",
                "EMPTY_SECTION",
                f"section has a heading but no substantive body: {title}",
                location=title,
            )
        )
    related = [body for title, body in _sections(text) if "related work" in title.casefold()]
    checks["related_work_has_body"] = not related or all(len(body.split()) >= 80 for body in related)
    if related and not checks["related_work_has_body"]:
        findings.append(
            _finding(
                "manuscript_source",
                "RELATED_WORK_BODY_MISSING",
                "Related Work has a heading but lacks substantive synthesis",
                location="Related Work",
            )
        )
    include_paths = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}", text)
    missing_figures = [item for item in include_paths if not (source.parent / item).is_file()]
    checks["figure_files_present"] = not missing_figures
    for item in missing_figures:
        findings.append(
            _finding(
                "manuscript_source",
                "FIGURE_FILE_MISSING",
                f"included figure does not exist: {item}",
                location=item,
            )
        )
    return ManuscriptSourceAudit(
        passed=not any(item.severity == "critical" for item in findings),
        checks=checks,
        findings=findings,
        source_path=str(source),
        source_sha256=sha256_file(source),
    )


def audit_citation_resolution_v2(
    text: str,
    *,
    registry_keys: set[str],
) -> CitationResolutionAudit:
    cited: list[str] = []
    for value in re.findall(r"\\cite\w*(?:\[[^\]]*\])?\{([^{}]+)\}", text):
        cited.extend(item.strip() for item in value.split(",") if item.strip())
    cited.extend(re.findall(r"\[([A-Za-z][A-Za-z0-9:._-]{2,})\]", text))
    cited_keys = sorted(set(cited))
    unresolved = sorted(set(cited_keys) - registry_keys)
    findings = [
        _finding(
            "citation_resolution",
            "UNRESOLVED_CITATION_KEY",
            f"citation key is absent from the frozen registry: {key}",
            location=key,
            action="resolve the identifier through a typed citation adapter and freeze its metadata",
        )
        for key in unresolved
    ]
    return CitationResolutionAudit(
        passed=not findings,
        checks={"all_citation_keys_resolve": not unresolved},
        findings=findings,
        cited_keys=cited_keys,
        registry_keys=sorted(registry_keys),
        unresolved_keys=unresolved,
    )


def audit_numerical_consistency_v2(
    surfaces: list[NumericalSurfaceValue],
) -> NumericalConsistencyAudit:
    findings: list[PublicationQAFinding] = []
    groups: dict[str, list[NumericalSurfaceValue]] = {}
    for item in surfaces:
        groups.setdefault(item.surface_id, []).append(item)
    for surface_id, values in groups.items():
        signatures = {
            (
                item.artifact_id,
                item.artifact_sha256,
                item.field,
                str(item.value),
                item.unit,
                item.direction,
                item.display_precision,
            )
            for item in values
        }
        if len(signatures) > 1:
            findings.append(
                _finding(
                    "numerical_consistency",
                    "SURFACE_VALUE_CONFLICT",
                    f"the same registered quantity differs across manuscript surfaces: {surface_id}",
                    location=surface_id,
                    action="bind abstract, prose, table, figure, and conclusion to one formal artifact value",
                )
            )
    return NumericalConsistencyAudit(
        passed=not findings,
        checks={"registered_surface_values_consistent": not findings},
        findings=findings,
        surfaces=surfaces,
        artifact_ids=sorted({item.artifact_id for item in surfaces}),
    )


def audit_statistical_semantics_v2(
    figures: list[FigureAcceptanceReport],
) -> StatisticalSemanticsAudit:
    findings: list[PublicationQAFinding] = []
    for figure in figures:
        for issue in figure.semantic_validation.issues:
            findings.append(
                _finding(
                    "statistical_semantics",
                    issue.code,
                    issue.message,
                    location=figure.figure_id,
                    action=issue.required_action,
                )
            )
    return StatisticalSemanticsAudit(
        passed=not findings and all(item.accepted for item in figures),
        checks={
            "all_figure_semantics_pass": not findings,
            "all_figures_accepted": all(item.accepted for item in figures),
        },
        findings=findings,
        figure_ids=[item.figure_id for item in figures],
    )


def audit_figure_table_v2(
    *,
    planned_figure_ids: list[str],
    figure_reports: list[FigureAcceptanceReport],
    manuscript_text: str,
) -> FigureTableAudit:
    accepted = {item.figure_id for item in figure_reports if item.accepted}
    referenced = set(re.findall(r"\\label\{(fig-[a-z0-9-]+)\}", manuscript_text))
    referenced |= set(re.findall(r"\b(fig-[a-z0-9-]+)\b", manuscript_text))
    findings: list[PublicationQAFinding] = []
    for figure_id in sorted(set(planned_figure_ids) - accepted):
        findings.append(
            _finding(
                "figure_table",
                "PLANNED_FIGURE_NOT_ACCEPTED",
                f"planned scientific figure lacks an accepted semantic/visual report: {figure_id}",
                location=figure_id,
            )
        )
    for figure_id in sorted(set(planned_figure_ids) - referenced):
        findings.append(
            _finding(
                "figure_table",
                "PLANNED_FIGURE_NOT_REFERENCED",
                f"accepted planned figure is not bound into the manuscript: {figure_id}",
                location=figure_id,
            )
        )
    return FigureTableAudit(
        passed=not findings,
        checks={
            "all_planned_figures_accepted": set(planned_figure_ids) <= accepted,
            "all_planned_figures_referenced": set(planned_figure_ids) <= referenced,
        },
        findings=findings,
        planned_figure_ids=sorted(set(planned_figure_ids)),
        accepted_figure_ids=sorted(accepted),
        referenced_figure_ids=sorted(referenced),
    )


def audit_language_style_v2(text: str) -> LanguageStyleAudit:
    findings: list[PublicationQAFinding] = []
    duplicate = _ADJACENT_DUPLICATE.search(text)
    if duplicate:
        findings.append(
            _finding(
                "language_style",
                "ADJACENT_DUPLICATE_WORD",
                f"adjacent duplicate word: {duplicate.group(0)}",
                location=f"character {duplicate.start()}",
            )
        )
    article = _BAD_ARTICLE.search(text)
    if article:
        findings.append(
            _finding(
                "language_style",
                "INVALID_ARTICLE_CONSTRUCTION",
                f"invalid article construction: {article.group(0)}",
                location=f"character {article.start()}",
            )
        )
    return LanguageStyleAudit(
        passed=not findings,
        checks={
            "no_adjacent_duplicate_words": duplicate is None,
            "no_known_article_error": article is None,
        },
        findings=findings,
    )


def audit_compiled_pdf_v2(
    path: str | Path | None,
    *,
    rendered_page_count: int = 0,
) -> CompiledPDFAudit:
    findings: list[PublicationQAFinding] = []
    checks = {
        "pdf_present": False,
        "pdf_nonempty": False,
        "all_pages_have_text_or_graphics": False,
        "all_pages_rendered": False,
        "no_unresolved_tokens_in_pdf": False,
    }
    page_count = 0
    pdf_hash: str | None = None
    resolved = Path(path).resolve() if path is not None else None
    if resolved is not None and resolved.is_file():
        checks["pdf_present"] = True
        checks["pdf_nonempty"] = resolved.stat().st_size > 1_000
        pdf_hash = sha256_file(resolved)
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(resolved))
            page_count = len(reader.pages)
            page_text = [(page.extract_text() or "").strip() for page in reader.pages]
            checks["all_pages_have_text_or_graphics"] = bool(page_count) and all(
                text or bool(page.get("/Resources")) for text, page in zip(page_text, reader.pages, strict=True)
            )
            combined = "\n".join(page_text)
            checks["no_unresolved_tokens_in_pdf"] = not any(
                pattern.search(combined) for _, pattern in _PLACEHOLDER_PATTERNS
            )
        except Exception as exc:
            findings.append(
                _finding(
                    "compiled_pdf",
                    "PDF_PARSE_FAILURE",
                    f"compiled PDF could not be parsed: {exc}",
                )
            )
    checks["all_pages_rendered"] = page_count > 0 and rendered_page_count == page_count
    messages = {
        "pdf_present": "compiled PDF is missing",
        "pdf_nonempty": "compiled PDF is empty or implausibly small",
        "all_pages_have_text_or_graphics": "compiled PDF contains a blank page",
        "all_pages_rendered": "not every compiled PDF page was rendered for visual QA",
        "no_unresolved_tokens_in_pdf": "compiled PDF contains an unresolved token",
    }
    existing = {item.code for item in findings}
    for name, passed in checks.items():
        code = name.upper()
        if not passed and code not in existing:
            findings.append(
                _finding(
                    "compiled_pdf",
                    code,
                    messages[name],
                    action="compile, render every page, and rerun PDF visual QA",
                )
            )
    return CompiledPDFAudit(
        passed=not any(item.severity == "critical" for item in findings),
        checks=checks,
        findings=findings,
        pdf_path=str(resolved) if resolved is not None else "",
        pdf_sha256=pdf_hash,
        page_count=page_count,
        rendered_page_count=rendered_page_count,
    )


def build_publication_acceptance_report_v2(
    *,
    manuscript_id: str,
    scientific_verdict: str,
    manuscript_source: ManuscriptSourceAudit,
    citation_resolution: CitationResolutionAudit,
    numerical_consistency: NumericalConsistencyAudit,
    statistical_semantics: StatisticalSemanticsAudit,
    figure_table: FigureTableAudit,
    language_style: LanguageStyleAudit,
    compiled_pdf: CompiledPDFAudit,
) -> PublicationAcceptanceReport:
    audits: list[_AuditBase] = [
        manuscript_source,
        citation_resolution,
        numerical_consistency,
        statistical_semantics,
        figure_table,
        language_style,
        compiled_pdf,
    ]
    findings = [item for audit in audits for item in audit.findings]
    critical = [item for item in findings if item.severity == "critical"]
    major = [item for item in findings if item.severity == "major"]
    return PublicationAcceptanceReport(
        manuscript_id=manuscript_id,
        scientific_verdict=scientific_verdict,
        manuscript_source=manuscript_source,
        citation_resolution=citation_resolution,
        numerical_consistency=numerical_consistency,
        statistical_semantics=statistical_semantics,
        figure_table=figure_table,
        language_style=language_style,
        compiled_pdf=compiled_pdf,
        critical_findings=critical,
        major_findings=major,
        publication_ready=not critical and all(audit.passed for audit in audits),
    )


__all__ = [
    "CitationResolutionAudit",
    "CompiledPDFAudit",
    "FigureTableAudit",
    "LanguageStyleAudit",
    "ManuscriptSourceAudit",
    "NumericalConsistencyAudit",
    "NumericalSurfaceValue",
    "PublicationAcceptanceReport",
    "PublicationQAFinding",
    "StatisticalSemanticsAudit",
    "audit_citation_resolution_v2",
    "audit_compiled_pdf_v2",
    "audit_figure_table_v2",
    "audit_language_style_v2",
    "audit_manuscript_source_v2",
    "audit_numerical_consistency_v2",
    "audit_statistical_semantics_v2",
    "build_publication_acceptance_report_v2",
]
