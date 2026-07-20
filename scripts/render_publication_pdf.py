from __future__ import annotations

import sys
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: render_publication_pdf.py INPUT.md OUTPUT.pdf READINESS.json")
    source, destination, readiness_path = map(Path, sys.argv[1:])
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    if not (
        readiness.get("publication_submission_ready") is True
        and readiness.get("hard_gate_passed") is True
        and float(readiness.get("readiness_score", -1)) >= float(readiness.get("readiness_threshold", 0.60))
    ):
        raise SystemExit("PDF layout blocked: fixed-venue readiness and hard scientific gates must pass first")
    text = source.read_text(encoding="utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("PaperTitle", parent=styles["Title"], alignment=TA_CENTER, fontSize=16, leading=20, spaceAfter=18))
    styles.add(ParagraphStyle("PaperHeading", parent=styles["Heading2"], fontSize=12, leading=15, spaceBefore=12, spaceAfter=6, textColor=colors.HexColor("#1b365d")))
    styles.add(ParagraphStyle("PaperBody", parent=styles["BodyText"], fontSize=9.5, leading=13, spaceAfter=7))
    story = []
    bullets: list[str] = []

    def flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            story.append(ListFlowable([ListItem(Paragraph(item, styles["PaperBody"])) for item in bullets], bulletType="bullet", leftIndent=18))
            story.append(Spacer(1, 4))
            bullets = []

    for line in text.splitlines():
        if line.startswith("# "):
            flush_bullets()
            story.append(Paragraph(line[2:], styles["PaperTitle"]))
        elif line.startswith("## "):
            flush_bullets()
            story.append(Paragraph(line[3:], styles["PaperHeading"]))
        elif line.startswith("- "):
            bullets.append(line[2:])
        elif line.strip():
            flush_bullets()
            story.append(Paragraph(line.replace("`", ""), styles["PaperBody"]))
        else:
            flush_bullets()
    flush_bullets()
    SimpleDocTemplate(str(destination), pagesize=letter, rightMargin=0.75 * inch, leftMargin=0.75 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch, title="Evidence-bound prospective study").build(story)


if __name__ == "__main__":
    main()
