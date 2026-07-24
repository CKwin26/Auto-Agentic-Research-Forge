from __future__ import annotations

"""Readable PDF fallback for canonical Markdown manuscripts.

This renderer is for content review when a TeX distribution is unavailable.
The LaTeX source remains the authoritative venue-facing typeset artifact.
"""

import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any


_UNNUMBERED = {
    "abstract",
    "references",
    "data and materials availability",
    "ethics statement",
    "author contributions",
    "conflicts of interest",
    "funding",
    "use of artificial intelligence",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inline(value: str) -> str:
    escaped = html.escape(value.strip())
    escaped = re.sub(r"`([^`]+)`", r"<font name='Courier'>\1</font>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", escaped)
    return escaped


def render_markdown_review_pdf(
    source: str | Path,
    *,
    output: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        KeepTogether,
        ListFlowable,
        ListItem,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    source_path = Path(source).resolve()
    destination = (
        Path(output).resolve()
        if output is not None
        else source_path.with_suffix(".review.pdf")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = source_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = next(
        (line[2:].strip() for line in lines if line.startswith("# ")),
        source_path.stem,
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="PaperTitle",
            parent=styles["Title"],
            fontName="Times-Bold",
            fontSize=18,
            leading=22,
            alignment=TA_CENTER,
            spaceAfter=18,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PaperH2",
            parent=styles["Heading1"],
            fontName="Times-Bold",
            fontSize=13,
            leading=16,
            spaceBefore=13,
            spaceAfter=7,
            textColor=colors.HexColor("#173F5F"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="PaperH3",
            parent=styles["Heading2"],
            fontName="Times-Bold",
            fontSize=11,
            leading=14,
            spaceBefore=9,
            spaceAfter=5,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PaperBody",
            parent=styles["BodyText"],
            fontName="Times-Roman",
            fontSize=9.6,
            leading=13.2,
            alignment=TA_JUSTIFY,
            firstLineIndent=0.45 * cm,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PaperAbstract",
            parent=styles["PaperBody"],
            fontSize=9.4,
            leading=12.8,
            leftIndent=0.45 * cm,
            rightIndent=0.45 * cm,
            firstLineIndent=0,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PaperReference",
            parent=styles["PaperBody"],
            fontSize=8.2,
            leading=10.6,
            firstLineIndent=-0.4 * cm,
            leftIndent=0.4 * cm,
        )
    )

    story: list[Any] = [Paragraph(_inline(title), styles["PaperTitle"])]
    section_number = 0
    subsection_number = 0
    current_section = ""
    paragraph: list[str] = []
    bullet_items: list[str] = []

    def body_style() -> Any:
        if current_section.casefold() == "abstract":
            return styles["PaperAbstract"]
        if current_section.casefold() == "references":
            return styles["PaperReference"]
        return styles["PaperBody"]

    def flush_paragraph() -> None:
        if paragraph:
            story.append(Paragraph(_inline(" ".join(paragraph)), body_style()))
            paragraph.clear()

    def flush_bullets() -> None:
        if bullet_items:
            story.append(
                ListFlowable(
                    [ListItem(Paragraph(_inline(item), body_style())) for item in bullet_items],
                    bulletType="bullet",
                    leftIndent=0.55 * cm,
                )
            )
            story.append(Spacer(1, 5))
            bullet_items.clear()

    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("# "):
            flush_paragraph()
            flush_bullets()
            index += 1
            continue
        if stripped.startswith("## "):
            flush_paragraph()
            flush_bullets()
            heading = stripped[3:].strip()
            current_section = heading
            subsection_number = 0
            if heading.casefold() in _UNNUMBERED:
                rendered_heading = heading
            else:
                section_number += 1
                rendered_heading = f"{section_number}. {heading}"
            story.append(Paragraph(_inline(rendered_heading), styles["PaperH2"]))
            index += 1
            continue
        if stripped.startswith("### "):
            flush_paragraph()
            flush_bullets()
            subsection_number += 1
            heading = stripped[4:].strip()
            prefix = f"{section_number}.{subsection_number}" if section_number else str(subsection_number)
            story.append(Paragraph(_inline(f"{prefix} {heading}"), styles["PaperH3"]))
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and re.match(
            r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$", lines[index + 1]
        ):
            flush_paragraph()
            rows = [[cell.strip() for cell in stripped.strip("|").split("|")]]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(
                    [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                )
                index += 1
            table = Table(
                [[Paragraph(_inline(cell), styles["PaperReference"]) for cell in row] for row in rows],
                repeatRows=1,
                hAlign="LEFT",
            )
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF0F4")),
                        ("FONTNAME", (0, 0), (-1, 0), "Times-Bold"),
                        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#80909B")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            story.extend([KeepTogether(table), Spacer(1, 7)])
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            bullet_items.append(stripped[2:])
            index += 1
            continue
        paragraph.append(stripped)
        index += 1
    flush_paragraph()
    flush_bullets()

    def page(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont("Times-Roman", 8)
        canvas.setFillColor(colors.HexColor("#52616B"))
        canvas.drawString(2.1 * cm, A4[1] - 1.15 * cm, "Pre-Delivery Claim–Evidence Gating")
        canvas.drawRightString(A4[0] - 2.1 * cm, 1.1 * cm, str(document.page))
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(destination),
        pagesize=A4,
        rightMargin=2.05 * cm,
        leftMargin=2.05 * cm,
        topMargin=1.75 * cm,
        bottomMargin=1.65 * cm,
        title=title,
        author="Author metadata required before submission",
    )
    document.build(story, onFirstPage=page, onLaterPages=page)
    manifest = {
        "schema_version": 1,
        "renderer": "reportlab-canonical-review-v1",
        "status": "content_review_pdf_not_venue_submission",
        "source": str(source_path),
        "source_sha256": _sha256(source_path),
        "pdf": str(destination),
        "pdf_sha256": _sha256(destination),
        "title": title,
        "abstract_style": "unstructured_single_paragraph",
        "numbered_body_sections": True,
    }
    manifest = dict(manifest)
    destination_manifest = (
        Path(manifest_path).resolve()
        if manifest_path is not None
        else destination.with_suffix(".manifest.json")
    )
    destination_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {**manifest, "manifest": str(destination_manifest)}
