import os
import subprocess
import threading
import time
from pathlib import Path

from research_forge.drawio_backend import (
    DrawioDiagram,
    DrawioEdge,
    DrawioNode,
    drawio_xml,
    export_drawio,
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


def test_export_waits_for_delayed_electron_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    source = write_drawio(
        tmp_path / "source.drawio",
        DrawioDiagram(
            title="Delayed export",
            nodes=(DrawioNode("node", "Node", 20, 20),),
        ),
    )
    executable = tmp_path / "drawio.exe"
    executable.write_bytes(b"fixture")
    output = tmp_path / "result.svg"
    captured: list[str] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess:
        captured.extend(command)

        def materialize() -> None:
            time.sleep(0.05)
            output.write_text("<svg></svg>", encoding="utf-8")

        threading.Thread(target=materialize, daemon=True).start()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("research_forge.drawio_backend.subprocess.run", fake_run)

    exported = export_drawio(
        source,
        output,
        executable=executable,
        timeout_seconds=3,
    )

    assert exported == output.resolve()
    assert output.read_text(encoding="utf-8") == "<svg></svg>"
    if os.name == "nt":
        assert "--disable-gpu" in captured
        assert any(item.startswith("--user-data-dir=") for item in captured)
        assert "--no-sandbox" not in captured
