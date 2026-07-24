from pathlib import Path

from research_forge.drawio_backend import (
    DrawioDiagram,
    DrawioEdge,
    DrawioNode,
    drawio_xml,
    write_drawio,
)


def test_drawio_source_is_editable_and_deterministic(tmp_path: Path) -> None:
    diagram = DrawioDiagram(
        title="Research states",
        nodes=(
            DrawioNode("frozen", "Frozen", 40, 80),
            DrawioNode("invalidated", "Invalidated", 280, 80),
        ),
        edges=(DrawioEdge("frozen", "invalidated", "audit fails"),),
    )

    first = drawio_xml(diagram)
    second = drawio_xml(diagram)
    path = write_drawio(tmp_path / "states.drawio", diagram)

    assert first == second == path.read_text(encoding="utf-8")
    assert "mxGraphModel" in first
    assert "source=\"frozen\"" in first
    assert "target=\"invalidated\"" in first
