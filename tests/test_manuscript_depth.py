from __future__ import annotations

from pathlib import Path

from research_forge.manuscript_depth import _profile, audit_manuscript_depth


def _paragraph(prefix: str, index: int, words: int, *, numbers: int = 0) -> str:
    tokens = [prefix, "paragraph", str(index)]
    tokens.extend(f"evidenceword{index}" for _ in range(max(words - len(tokens) - numbers, 0)))
    tokens.extend(str(index * 100 + item) for item in range(numbers))
    return " ".join(tokens) + "."


def _section(
    heading: str,
    *,
    words: int,
    paragraphs: int,
    subsections: int = 0,
    citations: int = 0,
    numbers: int = 0,
) -> str:
    blocks: list[str] = [rf"\section{{{heading}}}"]
    base = words // paragraphs
    remainder = words % paragraphs
    citation_budget = citations
    number_budget = numbers
    for index in range(paragraphs):
        if index < subsections:
            blocks.append(rf"\subsection{{{heading} analysis {index + 1}}}")
        paragraph_citations = 1 if citation_budget > 0 else 0
        citation_budget -= paragraph_citations
        paragraph_numbers = min(number_budget, 3)
        number_budget -= paragraph_numbers
        text = _paragraph(
            heading.replace(" ", ""),
            index + 1,
            base + (1 if index < remainder else 0),
            numbers=paragraph_numbers,
        )
        if paragraph_citations:
            text += rf" \cite{{source-{index + 1}}}."
        blocks.append(text)
    while citation_budget > 0:
        blocks[-1] += rf" \cite{{source-extra-{citation_budget}}}."
        citation_budget -= 1
    while number_budget > 0:
        blocks[-1] += f" {number_budget}."
        number_budget -= 1
    return "\n\n".join(blocks)


def _journal_latex() -> str:
    abstract = _paragraph("Abstract", 1, 180)
    body = [
        r"\documentclass{article}",
        r"\begin{document}",
        r"\begin{abstract}",
        abstract,
        r"\end{abstract}",
        _section("Introduction", words=820, paragraphs=5),
        _section("Related Work", words=900, paragraphs=6, citations=8),
        _section("Methods", words=1400, paragraphs=8, subsections=4),
        _section("Results", words=1200, paragraphs=7, subsections=3, numbers=18),
        _section("Discussion", words=1300, paragraphs=7, subsections=3),
        _section("Conclusion", words=250, paragraphs=2),
        r"\begin{thebibliography}{99}",
    ]
    for index in range(15):
        body.append(rf"\bibitem{{source-{index + 1}}} Author. Verified source {index + 1}.")
    body.extend([r"\end{thebibliography}", r"\end{document}"])
    return "\n\n".join(body) + "\n"


def test_journal_depth_gate_accepts_balanced_long_form_article(tmp_path: Path) -> None:
    manuscript = tmp_path / "paper.tex"
    report_path = tmp_path / "depth.json"
    manuscript.write_text(_journal_latex(), encoding="utf-8")

    report = audit_manuscript_depth(manuscript, report_path=report_path)

    assert report.passed
    assert report.total_count >= 6000
    assert report.sections["methods"].subsections == 4
    assert report.sections["related_work"].citations == 8
    assert report.sections["results"].numeric_tokens >= 15
    assert report_path.is_file()


def test_journal_depth_gate_rejects_heading_complete_but_thin_article(
    tmp_path: Path,
) -> None:
    manuscript = tmp_path / "thin.tex"
    text = "\n\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            r"\begin{abstract}",
            _paragraph("Abstract", 1, 80),
            r"\end{abstract}",
            *(
                _section(section, words=120, paragraphs=1, subsections=1)
                for section in (
                    "Introduction",
                    "Related Work",
                    "Methods",
                    "Results",
                    "Discussion",
                    "Conclusion",
                )
            ),
            r"\begin{thebibliography}{99}",
            *(rf"\bibitem{{source-{index}}} Source {index}." for index in range(15)),
            r"\end{thebibliography}",
            r"\end{document}",
        ]
    )
    manuscript.write_text(text, encoding="utf-8")

    report = audit_manuscript_depth(manuscript)

    assert not report.passed
    assert report.checks["section_present_introduction"]
    assert not report.checks["minimum_total_depth"]
    assert not report.checks["minimum_section_depth_introduction"]
    assert not report.checks["minimum_paragraphs_discussion"]


def test_current_v3_is_caught_as_overcompressed_regression() -> None:
    manuscript = (
        Path(__file__).resolve().parents[1]
        / "output"
        / "pdf"
        / "research-agent-evidence-v3-paper-en.tex"
    )

    report = audit_manuscript_depth(manuscript)

    assert not report.passed
    assert report.total_count < 6000
    assert not report.checks["minimum_section_depth_introduction"]
    assert not report.checks["minimum_section_depth_related_work"]


def test_bilingual_abstract_does_not_overwrite_primary_language_abstract(
    tmp_path: Path,
) -> None:
    manuscript = tmp_path / "bilingual.tex"
    chinese_abstract = "证据" * 130
    manuscript.write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                r"\begin{document}",
                r"\begin{abstract}",
                chinese_abstract,
                r"\end{abstract}",
                r"\section*{Abstract}",
                "English companion abstract that should not replace the Chinese abstract.",
                r"\section{引言}",
                "研究" * 100,
                r"\section{相关工作}",
                "文献" * 100,
                r"\section{方法}",
                "方法" * 100,
                r"\section{结果}",
                "结果" * 100,
                r"\section{讨论}",
                "讨论" * 100,
                r"\section{结论}",
                "结论" * 100,
                r"\end{document}",
            ]
        ),
        encoding="utf-8",
    )

    report = audit_manuscript_depth(manuscript, language="zh")

    assert report.sections["abstract"].count == len(chinese_abstract)
    assert report.checks["minimum_section_depth_abstract"]


def test_auto_language_detection_ignores_english_resource_appendix(
    tmp_path: Path,
) -> None:
    manuscript = tmp_path / "project-paper.md"
    chinese_body = "研究证据表明当前结论仍需独立验证。" * 40
    english_resources = "\n".join(
        f"- `backend/research_module_{index}.py` — Implements reproducible project evidence."
        for index in range(120)
    )
    manuscript.write_text(
        "\n\n".join(
            [
                "# 项目论文",
                f"## 摘要\n\n{chinese_body}",
                f"## 方法\n\n{chinese_body}",
                f"## 结果\n\n{chinese_body}",
                f"## 讨论\n\n{chinese_body}",
                f"## 结论\n\n{chinese_body}",
                f"## 可复现性与资源调用\n\n{english_resources}",
            ]
        ),
        encoding="utf-8",
    )

    report = audit_manuscript_depth(manuscript)

    assert report.language == "zh"
    assert report.profile == "journal-article-zh-v1"


def test_chinese_short_report_has_a_native_depth_profile() -> None:
    profile = _profile("short-report", "zh")

    assert profile.profile_id == "short-report-zh-v1"
    assert profile.unit == "han_chars"
    assert profile.minimum_total == 6_000
    assert profile.minimum_references == 8
