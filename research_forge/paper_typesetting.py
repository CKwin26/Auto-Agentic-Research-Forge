from __future__ import annotations

"""Deterministic venue-facing LaTeX rendering for reviewed manuscripts."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .paper_pipeline import (
    PaperStructureContract,
    split_abstract_keywords,
    validate_abstract_prose,
)


_CITATION_RE = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9._:-]{1,120})\]")
_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")
_INTERNAL_COMMENT_RE = re.compile(r"<!--(?:block|ref|anchor):.*?-->", re.DOTALL)
_TABLE_CAPTION_RE = re.compile(r"(?i)^Table:\s*(.+?)\s*$")
_PAGEBREAK_RE = re.compile(r"^<!--\s*pagebreak\s*-->$", re.IGNORECASE)


@dataclass(frozen=True)
class MarkdownSection:
    title: str
    level: int
    content: str


def _escape(value: str) -> str:
    table = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(table.get(char, char) for char in value)


def _inline(value: str) -> str:
    tokens: list[str] = []

    def stash(rendered: str) -> str:
        token = f"@@RFTOKEN{len(tokens)}@@"
        tokens.append(rendered)
        return token

    # Keep authoring-time provenance in Markdown, never in the reader-facing PDF.
    protected = _INTERNAL_COMMENT_RE.sub("", value)
    protected = protected.replace("–", "--").replace("—", "---")
    academic_symbols = {
        "κ": r"\(\kappa\)",
        "×": r"\(\times\)",
        "≤": r"\(\leq\)",
        "≥": r"\(\geq\)",
        "±": r"\(\pm\)",
        "−": r"\(-\)",
        "→": r"\(\rightarrow\)",
    }
    for symbol, latex in academic_symbols.items():
        protected = protected.replace(symbol, stash(latex))
    protected = _CITATION_RE.sub(lambda match: stash(r"\cite{" + match.group(1) + "}"), protected)
    protected = re.sub(
        r"`([^`]+)`",
        lambda match: stash(r"\texttt{" + _escape(match.group(1)) + "}"),
        protected,
    )
    protected = re.sub(
        r"\*\*([^*]+)\*\*",
        lambda match: stash(r"\textbf{" + _escape(match.group(1)) + "}"),
        protected,
    )
    protected = re.sub(
        r"(?<!\*)\*([^*\n]+)\*(?!\*)",
        lambda match: stash(r"\textit{" + _escape(match.group(1)) + "}"),
        protected,
    )
    rendered = _escape(protected)
    for index, token in enumerate(tokens):
        rendered = rendered.replace(f"@@RFTOKEN{index}@@", token)
    return rendered


def _sections(markdown: str) -> tuple[str, list[MarkdownSection]]:
    title_match = re.search(r"(?m)^#\s+(.+?)\s*$", markdown)
    if title_match is None:
        raise ValueError("manuscript title is missing")
    matches = list(re.finditer(r"(?m)^(#{2,4})\s+(.+?)\s*$", markdown))
    sections: list[MarkdownSection] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections.append(
            MarkdownSection(
                title=match.group(2).strip(),
                level=len(match.group(1)),
                content=markdown[match.end() : end].strip(),
            )
        )
    return title_match.group(1).strip(), sections


def _table(lines: list[str], *, caption: str) -> str:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    if len(rows) < 2 or not _TABLE_SEPARATOR_RE.match(lines[1]):
        raise ValueError("invalid Markdown table")
    header = rows[0]
    body = rows[2:]
    if any(len(row) != len(header) for row in body):
        raise ValueError("Markdown table rows have inconsistent column counts")
    spec = "".join("Y" for _ in header)
    rendered = [
        r"\begin{table}[tbp]",
        r"\centering",
        r"\caption{" + _inline(caption) + "}",
        r"\footnotesize",
        r"\setlength{\tabcolsep}{3.5pt}",
        r"\renewcommand{\arraystretch}{1.12}",
        rf"\begin{{tabularx}}{{\linewidth}}{{{spec}}}",
        r"\toprule",
    ]
    rendered.append(" & ".join(_inline(cell) for cell in header) + r" \\")
    rendered.append(r"\midrule")
    rendered.extend(" & ".join(_inline(cell) for cell in row) + r" \\" for row in body)
    rendered.extend([r"\bottomrule", r"\end{tabularx}", r"\end{table}"])
    return "\n".join(rendered)


def _content_to_latex(content: str) -> str:
    lines = content.splitlines()
    rendered: list[str] = []
    paragraph: list[str] = []
    in_list = False
    pending_table_caption: str | None = None

    def flush_paragraph() -> None:
        if paragraph:
            rendered.append(_inline(" ".join(item.strip() for item in paragraph)))
            rendered.append("")
            paragraph.clear()

    index = 0
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            flush_paragraph()
            if in_list:
                rendered.append(r"\end{itemize}")
                rendered.append("")
                in_list = False
            index += 1
            continue
        if _PAGEBREAK_RE.match(stripped):
            flush_paragraph()
            if in_list:
                rendered.append(r"\end{itemize}")
                in_list = False
            rendered.extend([r"\clearpage", ""])
            index += 1
            continue
        caption_match = _TABLE_CAPTION_RE.match(stripped)
        if caption_match is not None:
            flush_paragraph()
            pending_table_caption = caption_match.group(1).strip()
            index += 1
            continue
        if stripped.startswith("```mermaid") or stripped.startswith("~~~mermaid"):
            raise ValueError(
                "Mermaid source cannot be typeset as prose; render it through the draw.io backend"
            )
        image = _IMAGE_RE.match(stripped)
        if image is not None:
            flush_paragraph()
            if in_list:
                rendered.append(r"\end{itemize}")
                in_list = False
            caption, path = image.groups()
            rendered.extend(
                [
                    r"\begin{figure}[htbp]",
                    r"\centering",
                    r"\includegraphics[width=0.94\linewidth]{" + _escape(path.replace("\\", "/")) + "}",
                    r"\caption{" + _inline(caption) + "}",
                    r"\end{figure}",
                    "",
                ]
            )
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and _TABLE_SEPARATOR_RE.match(lines[index + 1]):
            flush_paragraph()
            if pending_table_caption is None:
                raise ValueError("Markdown table is missing a preceding 'Table:' caption")
            table_lines = [raw, lines[index + 1]]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            rendered.extend([_table(table_lines, caption=pending_table_caption), ""])
            pending_table_caption = None
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            if not in_list:
                rendered.append(r"\begin{itemize}")
                in_list = True
            rendered.append(r"\item " + _inline(stripped[2:]))
            index += 1
            continue
        if stripped.startswith("> "):
            flush_paragraph()
            rendered.extend(
                [r"\begin{quote}", _inline(stripped[2:]), r"\end{quote}", ""]
            )
            index += 1
            continue
        paragraph.append(stripped)
        index += 1
    flush_paragraph()
    if in_list:
        rendered.append(r"\end{itemize}")
    if pending_table_caption is not None:
        raise ValueError("Table caption is not followed by a Markdown table")
    return "\n".join(rendered).strip()


def render_submission_latex(
    markdown: str,
    *,
    contract: PaperStructureContract,
    language: Literal["zh", "en"] = "zh",
) -> str:
    title, sections = _sections(markdown)
    abstract_spec = contract.section("abstract")
    abstract_section = next(
        (item for item in sections if contract.section_for_title(item.title) == abstract_spec),
        None,
    )
    if abstract_section is None:
        raise ValueError("abstract section is missing")
    abstract_prose, keywords = split_abstract_keywords(abstract_section.content)
    abstract_violations = validate_abstract_prose(abstract_prose, contract.abstract)
    if abstract_violations:
        raise ValueError("abstract genre gate failed: " + "; ".join(abstract_violations))

    body: list[str] = []
    references_open = False
    emitted_body_section = False
    for section in sections:
        spec = contract.section_for_title(section.title)
        if spec is not None and spec.key == "abstract":
            body.extend(
                [r"\begin{abstract}", _inline(abstract_prose), r"\end{abstract}", ""]
            )
            if keywords:
                keyword_label = "关键词" if language == "zh" else "Keywords"
                body.extend(
                    [
                        r"\noindent\textbf{" + keyword_label + r":} " + _inline(keywords),
                        "",
                    ]
                )
            continue
        if spec is not None and spec.key == "references":
            references_open = True
            body.extend([r"\begin{thebibliography}{99}", r"\RaggedRight", r"\small"])
            for raw in section.content.splitlines():
                match = re.match(
                    r"- \[([A-Za-z0-9][A-Za-z0-9._:-]{1,120})\]\s*(.*)",
                    raw.strip(),
                )
                if match:
                    body.append(r"\bibitem{" + match.group(1) + "}" + _inline(match.group(2)))
            body.extend([r"\end{thebibliography}", ""])
            continue
        command = {2: "section", 3: "subsection", 4: "subsubsection"}[section.level]
        numbered = spec.numbered if spec is not None else True
        star = "" if numbered else "*"
        if section.level == 2 and emitted_body_section:
            body.extend([r"\FloatBarrier", ""])
        body.append(rf"\{command}{star}{{{_inline(section.title)}}}")
        body.append(_content_to_latex(section.content))
        body.append("")
        emitted_body_section = True
    if not references_open:
        raise ValueError("references section is missing")

    document_class = "ctexart" if language == "zh" else "article"
    font_setup = "" if language == "zh" else "\\usepackage[T1]{fontenc}\n\\usepackage{lmodern}\n"
    return (
        rf"\documentclass[11pt]{{{document_class}}}" + "\n"
        r"\usepackage[margin=2.5cm]{geometry}" + "\n"
        + font_setup
        + r"\usepackage{graphicx}" + "\n"
        + r"\usepackage{booktabs}" + "\n"
        + r"\usepackage{tabularx}" + "\n"
        + r"\usepackage{array}" + "\n"
        + r"\usepackage{ragged2e}" + "\n"
        + r"\usepackage{placeins}" + "\n"
        + r"\usepackage[font=small,labelfont=bf]{caption}" + "\n"
        + r"\usepackage[hidelinks]{hyperref}" + "\n"
        + r"\newcolumntype{Y}{>{\RaggedRight\arraybackslash\hspace{0pt}}X}" + "\n"
        + r"\hyphenpenalty=10000" + "\n"
        + r"\exhyphenpenalty=10000" + "\n"
        + r"\emergencystretch=2em" + "\n"
        + r"\setlength{\parindent}{2em}" + "\n"
        + r"\setlength{\parskip}{0.35em}" + "\n"
        + r"\title{" + _inline(title) + "}\n"
        + r"\author{Anonymous submission}" + "\n"
        + r"\date{}" + "\n"
        + r"\begin{document}" + "\n"
        + r"\maketitle" + "\n"
        + "\n".join(body)
        + "\n"
        + r"\end{document}" + "\n"
    )


def write_submission_latex(
    manuscript_path: Path,
    *,
    contract: PaperStructureContract,
    language: Literal["zh", "en"] = "zh",
) -> Path:
    source = manuscript_path.resolve()
    destination = source.with_suffix(".tex")
    destination.write_text(
        render_submission_latex(
            source.read_text(encoding="utf-8"),
            contract=contract,
            language=language,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return destination
