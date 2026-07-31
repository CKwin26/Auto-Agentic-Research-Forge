"""Bounded, read-only PDF material extraction."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=64)
def _extract_pdf_snapshot(
    path: str, size_bytes: int, modified_ns: int
) -> dict[str, Any]:
    """Extract one immutable-in-process snapshot per file version."""

    from pypdf import PdfReader

    source = Path(path)
    reader = PdfReader(str(source), strict=False)
    metadata = reader.metadata or {}
    parts = [f"# {source.stem}"]
    extracted_pages = 0
    limit = 1_000_000
    remaining = max(0, limit - len(parts[0]))
    for index, page in enumerate(reader.pages, start=1):
        if remaining <= 0:
            break
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = text.strip()
        if not text:
            continue
        extracted_pages += 1
        bounded = text[:remaining]
        parts.extend((f"## Page {index}", bounded))
        remaining -= len(bounded) + len(parts[-2]) + 2
    combined = "\n".join(parts)[:limit]
    text_characters = max(0, len(combined) - len(parts[0]))
    return {
        "schema_version": 1,
        "path": str(source),
        "page_count": len(reader.pages),
        "encrypted": bool(reader.is_encrypted),
        "form_present": bool(
            reader.trailer.get("/Root", {}).get("/AcroForm")
        ),
        "title": str(getattr(metadata, "title", "") or ""),
        "text": combined if extracted_pages else "",
        "text_characters": text_characters,
        "extracted_page_count": extracted_pages,
        "text_status": (
            "extractable" if extracted_pages and text_characters else "ocr_required"
        ),
        "scientific_evidence_eligible": False,
    }


def extract_pdf_material(
    path: str | Path, *, limit: int = 400_000
) -> dict[str, Any]:
    """Extract bounded text and metadata, or report that OCR is required."""

    source = Path(path).resolve()
    stat = source.stat()
    snapshot = dict(
        _extract_pdf_snapshot(
            str(source), stat.st_size, stat.st_mtime_ns
        )
    )
    text = str(snapshot.get("text") or "")[:limit]
    snapshot["text"] = text
    snapshot["text_characters"] = len(text)
    return snapshot
