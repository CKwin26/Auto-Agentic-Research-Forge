from pathlib import Path

from pypdf import PdfWriter

from research_forge.pdf_materials import extract_pdf_material
from research_forge.project_bundle import inventory_project_bundle


def test_pdf_is_inventoried_and_scanned_pdf_requests_ocr(
    tmp_path: Path,
) -> None:
    path = tmp_path / "user-manual.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    with path.open("wb") as stream:
        writer.write(stream)

    resources, excluded = inventory_project_bundle(tmp_path)
    metadata = extract_pdf_material(path)

    assert excluded == 0
    assert [item.path for item in resources] == ["user-manual.pdf"]
    assert metadata["page_count"] == 1
    assert metadata["text_status"] == "ocr_required"
    assert metadata["text"] == ""
    assert metadata["scientific_evidence_eligible"] is False
