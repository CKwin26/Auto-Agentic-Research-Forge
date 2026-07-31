from __future__ import annotations

"""Deterministic draw.io source generation and headless export.

Research Forge keeps the editable ``.drawio`` file as the provenance source and
embeds only an exported PNG/PDF in the manuscript.  This prevents diagram DSL
or editor metadata from leaking into reader-facing prose.
"""

import os
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class DrawioNode:
    node_id: str
    label: str
    x: float
    y: float
    width: float = 190
    height: float = 76
    style: str = (
        "rounded=1;whiteSpace=wrap;html=1;fillColor=#f7f9fc;"
        "strokeColor=#1f4b6e;fontColor=#172b4d;fontSize=14;"
        "fontFamily=Arial;spacing=8;"
    )


@dataclass(frozen=True)
class DrawioEdge:
    source: str
    target: str
    label: str = ""
    style: str = (
        "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;"
        "jettySize=auto;html=1;endArrow=block;endFill=1;"
        "strokeColor=#2b7bb9;fontSize=11;fontFamily=Arial;"
    )


@dataclass(frozen=True)
class DrawioDiagram:
    title: str
    nodes: tuple[DrawioNode, ...]
    edges: tuple[DrawioEdge, ...] = field(default_factory=tuple)
    page_width: int = 1169
    page_height: int = 827


def find_drawio() -> Path | None:
    configured = os.environ.get("DRAWIO_BIN")
    candidates = [
        configured,
        shutil.which("draw.io"),
        shutil.which("drawio"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "draw.io" / "draw.io.exe"),
        str(Path(os.environ.get("ProgramFiles", "")) / "draw.io" / "draw.io.exe"),
    ]
    for raw in candidates:
        if raw and Path(raw).is_file():
            return Path(raw).resolve()
    return None


def drawio_xml(diagram: DrawioDiagram) -> str:
    node_ids = {item.node_id for item in diagram.nodes}
    if len(node_ids) != len(diagram.nodes):
        raise ValueError("draw.io diagram contains duplicate node IDs")
    for edge in diagram.edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            raise ValueError("draw.io edge references an unknown node")

    mxfile = ET.Element(
        "mxfile",
        {
            "host": "ResearchForge",
            "agent": "Research Forge draw.io backend",
            "version": "1",
            "compressed": "false",
        },
    )
    page = ET.SubElement(mxfile, "diagram", {"id": "research-forge", "name": diagram.title})
    model = ET.SubElement(
        page,
        "mxGraphModel",
        {
            "dx": "1200",
            "dy": "800",
            "grid": "1",
            "gridSize": "10",
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "1",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": str(diagram.page_width),
            "pageHeight": str(diagram.page_height),
            "math": "0",
            "shadow": "0",
        },
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    for node in diagram.nodes:
        cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": node.node_id,
                "value": node.label,
                "style": node.style,
                "vertex": "1",
                "parent": "1",
            },
        )
        ET.SubElement(
            cell,
            "mxGeometry",
            {
                "x": f"{node.x:g}",
                "y": f"{node.y:g}",
                "width": f"{node.width:g}",
                "height": f"{node.height:g}",
                "as": "geometry",
            },
        )
    for index, edge in enumerate(diagram.edges, start=1):
        cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": f"edge-{index}",
                "value": edge.label,
                "style": edge.style,
                "edge": "1",
                "parent": "1",
                "source": edge.source,
                "target": edge.target,
            },
        )
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    ET.indent(mxfile, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        mxfile, encoding="unicode", short_empty_elements=True
    ) + "\n"


def write_drawio(path: str | Path, diagram: DrawioDiagram) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(drawio_xml(diagram), encoding="utf-8", newline="\n")
    return destination


def export_drawio(
    source: str | Path,
    output: str | Path,
    *,
    format: Literal["png", "pdf", "svg"] | None = None,
    executable: str | Path | None = None,
    timeout_seconds: int = 90,
) -> Path:
    source_path = Path(source).resolve()
    output_path = Path(output).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"draw.io source not found: {source_path}")
    drawio = Path(executable).resolve() if executable else find_drawio()
    if drawio is None or not drawio.is_file():
        raise FileNotFoundError(
            "draw.io Desktop was not found; install JGraph.Draw or set DRAWIO_BIN"
        )
    output_format = format or output_path.suffix.lstrip(".").lower()
    if output_format not in {"png", "pdf", "svg"}:
        raise ValueError(f"unsupported draw.io export format: {output_format}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="research-forge-drawio-",
        ignore_cleanup_errors=True,
    ) as user_data_dir:
        command = [str(drawio)]
        if os.name == "nt":
            # Electron's default GPU/cache profile has repeatedly failed with
            # access-denied errors on managed Windows installations.  An
            # isolated per-export profile avoids ambient editor state; keeping
            # software rasterization enabled still produces deterministic
            # vector output.
            command.extend(
                [
                    "--disable-gpu",
                    f"--user-data-dir={user_data_dir}",
                ]
            )
        command.extend(
            [
                "--export",
                "--format",
                output_format,
                "--crop",
                "--border",
                "20",
                "--output",
                str(output_path),
                str(source_path),
            ]
        )
        completed = subprocess.run(
            command,
            cwd=source_path.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        # Some Windows draw.io builds hand work to an Electron child and return
        # before the child atomically publishes the file.  Treat process exit
        # and artifact materialization as separate completion conditions.
        deadline = time.monotonic() + min(10.0, max(1.0, timeout_seconds / 3))
        while (
            completed.returncode == 0
            and (
                not output_path.is_file()
                or output_path.stat().st_size == 0
            )
            and time.monotonic() < deadline
        ):
            time.sleep(0.1)
        if (
            completed.returncode != 0
            or not output_path.is_file()
            or output_path.stat().st_size == 0
        ):
            detail = (
                completed.stderr
                or completed.stdout
                or "draw.io exited without materializing the requested artifact"
            ).strip()
            raise RuntimeError(f"draw.io export failed: {detail}")
    return output_path
