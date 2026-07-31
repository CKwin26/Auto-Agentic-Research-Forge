from pathlib import Path

import pytest

from research_forge.ai_drawio import (
    AIDiagramEdge,
    AIDiagramNode,
    AIDiagramPlan,
    audit_ai_edited_drawio,
    next_ai_drawio_mcp_config,
    render_ai_diagram_plan,
    validate_plan_evidence_bounds,
)
from research_forge.drawio_backend import drawio_xml
from research_forge.paper_authoring import (
    EvidenceClaimBinding,
    EvidenceClaimMap,
    EvidencePointer,
    FigureSlot,
    HierarchicalPaperOutline,
    OutlineNode,
    build_paper_artifacts,
)


def _plan() -> AIDiagramPlan:
    return AIDiagramPlan(
        title="Evidence-bound workflow",
        orientation="left_to_right",
        nodes=[
            AIDiagramNode(
                node_id="bundle",
                label="Project bundle",
                lane=0,
                order=0,
                shape="datastore",
            ),
            AIDiagramNode(
                node_id="gate",
                label="Evidence gate",
                lane=0,
                order=1,
                shape="decision",
            ),
        ],
        edges=[AIDiagramEdge(source="bundle", target="gate", label="bind")],
        evidence_claim_ids=["claim-1"],
    )


def test_typed_plan_renders_deterministic_drawio() -> None:
    plan = _plan()

    first = drawio_xml(render_ai_diagram_plan(plan))
    second = drawio_xml(render_ai_diagram_plan(plan))

    assert first == second
    assert "Project bundle" in first
    assert "shape=cylinder3" in first
    assert 'source="bundle"' in first


def test_plan_rejects_unknown_edge_and_overlapping_layout() -> None:
    with pytest.raises(ValueError, match="unknown node"):
        AIDiagramPlan(
            title="Invalid graph",
            nodes=[
                AIDiagramNode(node_id="one", label="One", lane=0, order=0),
                AIDiagramNode(node_id="two", label="Two", lane=0, order=1),
            ],
            edges=[AIDiagramEdge(source="one", target="missing")],
        )
    with pytest.raises(ValueError, match="layout cell"):
        AIDiagramPlan(
            title="Invalid layout",
            nodes=[
                AIDiagramNode(node_id="one", label="One", lane=0, order=0),
                AIDiagramNode(node_id="two", label="Two", lane=0, order=0),
            ],
        )


def test_mcp_config_is_pinned_and_contains_no_model_provider() -> None:
    config = next_ai_drawio_mcp_config()
    server = config["mcpServers"]["drawio"]

    assert server["command"] == "npx"
    assert server["args"] == ["-y", "@next-ai-drawio/mcp-server@0.2.3"]
    assert "env" not in server


def test_plan_numeric_content_must_come_from_frozen_evidence() -> None:
    plan = _plan().model_copy(
        update={
            "nodes": [
                _plan().nodes[0].model_copy(update={"detail": "40 paired runs"}),
                _plan().nodes[1],
            ]
        }
    )

    with pytest.raises(ValueError, match="outside frozen evidence"):
        validate_plan_evidence_bounds(plan, evidence_context="")

    validate_plan_evidence_bounds(plan, evidence_context="Registered design: 40 runs.")


def test_ai_edited_source_blocks_unbound_numbers_and_remote_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "figure.drawio"
    source.write_text(drawio_xml(render_ai_diagram_plan(_plan())), encoding="utf-8")
    clean = audit_ai_edited_drawio(source)
    assert clean.passed is True

    unsafe = source.read_text(encoding="utf-8").replace(
        "Project bundle",
        "Accuracy 91% &lt;a href=&quot;https://example.com&quot;&gt;source&lt;/a&gt;",
    )
    source.write_text(unsafe, encoding="utf-8")
    audit = audit_ai_edited_drawio(source)

    assert audit.passed is False
    assert audit.unauthorized_numeric_tokens == ["91%"]
    assert any("external links" in item for item in audit.violations)


def test_ai_edited_source_accepts_explicitly_bound_numeric_token(
    tmp_path: Path,
) -> None:
    source = tmp_path / "figure.drawio"
    raw = drawio_xml(render_ai_diagram_plan(_plan())).replace(
        "Evidence gate", "Evidence gate 4"
    )
    source.write_text(raw, encoding="utf-8")

    audit = audit_ai_edited_drawio(source, allowed_numeric_tokens={"4"})

    assert audit.passed is True


def _concept_outline() -> tuple[HierarchicalPaperOutline, EvidenceClaimMap]:
    claim_map = EvidenceClaimMap(
        track_id="track-concept",
        frozen_conclusion="The workflow preserves evidence boundaries.",
        bindings=[
            EvidenceClaimBinding(
                claim_id="claim-method",
                kind="method",
                statement="Evidence is bound before manuscript generation.",
                evidence=[
                    EvidencePointer(
                        path="stage3/evidence.json",
                        sha256="a" * 64,
                        evidence_type="project_artifact",
                    )
                ],
                allowed_sections=["methods"],
                claim_strength="descriptive",
                evidence_status="bound",
            )
        ],
        verified_source_ids=[],
        forbidden_moves=["invent evidence"],
        source_registry_sha256="b" * 64,
    )
    sections = [
        OutlineNode(
            node_id=f"section-{index}",
            section_key=f"section_{index}",
            heading=f"Section {index}",
            level=2,
            purpose="Explain one required part of the registered study.",
            argument="Keep the explanation inside the frozen evidence boundary.",
        )
        for index in range(1, 7)
    ]
    outline = HierarchicalPaperOutline(
        title="Evidence-bound workflow figure",
        thesis="Editable conceptual figures retain frozen evidence provenance.",
        abstract_moves=["Background", "Problem", "Method", "Result", "Implication"],
        sections=sections,
        figure_slots=[
            FigureSlot(
                slot_id="fig-concept-flow",
                purpose="Show the evidence-bound manuscript workflow.",
                evidence_claim_ids=["claim-method"],
                source_paths=["stage3/evidence.json"],
                chart_type="flow",
                caption_contract="Evidence is bound before manuscript generation.",
            )
        ],
    )
    return outline, claim_map


def test_concept_figure_requires_next_ai_drawio_and_records_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outline, claim_map = _concept_outline()
    fake_binary = tmp_path / "drawio.exe"
    fake_binary.write_bytes(b"placeholder")

    def fake_export(source: Path, output: Path, **_: object) -> Path:
        Path(output).write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            encoding="utf-8",
        )
        return Path(output)

    monkeypatch.setattr(
        "research_forge.paper_authoring.find_drawio", lambda: fake_binary
    )
    monkeypatch.setattr("research_forge.paper_authoring.export_drawio", fake_export)

    manifest = build_paper_artifacts(
        tmp_path,
        outline=outline,
        evidence_claim_map=claim_map,
    )

    record = manifest.records[0]
    assert manifest.ready is True
    assert record.renderer == "next_ai_drawio"
    assert record.editor_backend == "next_ai_drawio"
    assert record.editor_audit_passed is True
    assert record.editable_source_sha256
    assert record.editor_config_sha256
    assert record.editor_audit_sha256


def test_concept_figure_blocks_instead_of_native_svg_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outline, claim_map = _concept_outline()
    monkeypatch.setattr("research_forge.paper_authoring.find_drawio", lambda: None)

    manifest = build_paper_artifacts(
        tmp_path,
        outline=outline,
        evidence_claim_map=claim_map,
    )

    record = manifest.records[0]
    assert manifest.ready is False
    assert record.status == "needs_evidence"
    assert record.renderer == "next_ai_drawio"
    assert record.editor_audit_passed is True
    assert "draw.io Desktop is unavailable" in record.reason
