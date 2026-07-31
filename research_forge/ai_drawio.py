from __future__ import annotations

"""Evidence-bounded AI planning for editable draw.io diagrams.

The model never emits XML or arbitrary styles.  It proposes a small typed graph
which Research Forge renders through the deterministic draw.io backend.  The
result can then be opened and edited with next-ai-draw-io without granting the
editor scientific authority over claims or numeric results.
"""

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .drawio_backend import (
    DrawioDiagram,
    DrawioEdge,
    DrawioNode,
    write_drawio,
)
from .models import MacroStage, StrictModel
from .storage import sha256_file, write_json_atomic


NEXT_AI_DRAWIO_MCP_PACKAGE = "@next-ai-drawio/mcp-server@0.2.3"
_NUMBER = re.compile(r"(?<![\w.])[-+]?(?:\d+(?:\.\d+)?|\.\d+)%?(?![\w.])")
_FORBIDDEN_XML = re.compile(
    r"<\s*(?:script|iframe|object|embed)\b|javascript\s*:|data\s*:\s*text/html",
    re.IGNORECASE,
)


def numeric_tokens_in_text(value: str) -> set[str]:
    return set(_NUMBER.findall(value))


class AIDiagramNode(StrictModel):
    node_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    label: str = Field(min_length=1, max_length=100)
    detail: str | None = Field(default=None, max_length=180)
    lane: int = Field(ge=0, le=7)
    order: int = Field(ge=0, le=19)
    shape: Literal["process", "decision", "datastore", "actor", "document"] = (
        "process"
    )


class AIDiagramEdge(StrictModel):
    source: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    target: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    label: str = Field(default="", max_length=80)


class AIDiagramPlan(StrictModel):
    schema_version: int = 1
    title: str = Field(min_length=2, max_length=160)
    orientation: Literal["left_to_right", "top_to_bottom"] = "left_to_right"
    nodes: list[AIDiagramNode] = Field(min_length=2, max_length=40)
    edges: list[AIDiagramEdge] = Field(default_factory=list, max_length=80)
    evidence_claim_ids: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def graph_is_well_formed(self) -> "AIDiagramPlan":
        node_ids = [item.node_id for item in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("AI diagram plan contains duplicate node IDs")
        known = set(node_ids)
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError("AI diagram edge references an unknown node")
            if edge.source == edge.target:
                raise ValueError("AI diagram self-edges are not permitted")
        occupied = [(item.lane, item.order) for item in self.nodes]
        if len(occupied) != len(set(occupied)):
            raise ValueError("AI diagram nodes cannot share a layout cell")
        return self


class DrawioSourceAudit(StrictModel):
    schema_version: int = 1
    passed: bool
    source_path: str
    source_sha256: str
    cell_count: int
    numeric_tokens: list[str]
    unauthorized_numeric_tokens: list[str]
    violations: list[str]


def next_ai_drawio_mcp_config(
    *,
    command: str = "npx",
    drawio_base_url: str | None = None,
) -> dict[str, object]:
    """Return a portable, pinned MCP configuration without provider secrets."""

    server: dict[str, object] = {
        "command": command,
        "args": ["-y", NEXT_AI_DRAWIO_MCP_PACKAGE],
    }
    if drawio_base_url:
        if not drawio_base_url.startswith(("https://", "http://localhost")):
            raise ValueError("DRAWIO_BASE_URL must use HTTPS or localhost")
        server["env"] = {"DRAWIO_BASE_URL": drawio_base_url.rstrip("/")}
    return {"mcpServers": {"drawio": server}}


def write_next_ai_drawio_mcp_config(
    destination: str | Path,
    *,
    command: str = "npx",
    drawio_base_url: str | None = None,
) -> Path:
    path = Path(destination).resolve()
    write_json_atomic(
        path,
        next_ai_drawio_mcp_config(
            command=command,
            drawio_base_url=drawio_base_url,
        ),
    )
    return path


def _node_style(shape: str) -> str:
    base = (
        "whiteSpace=wrap;html=1;strokeColor=#43546a;fontColor=#172b4d;"
        "fontSize=13;fontFamily=Arial;spacing=8;shadow=0;"
    )
    shapes = {
        "process": "rounded=1;arcSize=12;fillColor=#f4f7fb;",
        "decision": "rhombus;fillColor=#fff4d6;",
        "datastore": "shape=cylinder3;boundedLbl=1;backgroundOutline=1;fillColor=#e8f3ff;",
        "actor": "shape=mxgraph.basic.person;fillColor=#f2ecff;",
        "document": "shape=document;boundedLbl=1;fillColor=#eef8ee;",
    }
    return base + shapes[shape]


def render_ai_diagram_plan(plan: AIDiagramPlan) -> DrawioDiagram:
    """Convert a typed AI plan into deterministic, editable draw.io geometry."""

    horizontal = plan.orientation == "left_to_right"
    nodes: list[DrawioNode] = []
    for item in plan.nodes:
        x_index, y_index = (
            (item.order, item.lane) if horizontal else (item.lane, item.order)
        )
        safe_label = html.escape(item.label)
        label = safe_label
        if item.detail:
            label = (
                f"<b>{safe_label}</b><br><font color=\"#52667a\">"
                f"{html.escape(item.detail)}</font>"
            )
        nodes.append(
            DrawioNode(
                node_id=item.node_id,
                label=label,
                x=50 + x_index * 245,
                y=70 + y_index * 135,
                width=190,
                height=78,
                style=_node_style(item.shape),
            )
        )
    edges = tuple(
        DrawioEdge(source=item.source, target=item.target, label=item.label)
        for item in plan.edges
    )
    max_x = max(node.x + node.width for node in nodes)
    max_y = max(node.y + node.height for node in nodes)
    return DrawioDiagram(
        title=plan.title,
        nodes=tuple(nodes),
        edges=edges,
        page_width=max(1169, int(max_x + 70)),
        page_height=max(827, int(max_y + 70)),
    )


def validate_plan_evidence_bounds(
    plan: AIDiagramPlan,
    *,
    evidence_context: str,
) -> None:
    """Prevent an AI plan from introducing unbound numbers or external content."""

    visible = " ".join(
        filter(
            None,
            [
                plan.title,
                *(item.label for item in plan.nodes),
                *(item.detail for item in plan.nodes),
                *(item.label for item in plan.edges),
            ],
        )
    )
    if re.search(r"https?://|(?:href|link)\s*=", visible, re.IGNORECASE):
        raise ValueError("AI diagram plan may not introduce links or remote content")
    proposed_numbers = set(_NUMBER.findall(visible))
    evidence_numbers = set(_NUMBER.findall(evidence_context))
    unauthorized = sorted(proposed_numbers.difference(evidence_numbers))
    if unauthorized:
        raise ValueError(
            "AI diagram plan contains numeric tokens outside frozen evidence: "
            + ", ".join(unauthorized)
        )


async def generate_ai_diagram_plan(
    prompt: str,
    *,
    evidence_context: str = "",
    evidence_claim_ids: list[str] | None = None,
    cwd: str | Path | None = None,
) -> AIDiagramPlan:
    """Use the configured Research Forge model backend to plan a concept figure."""

    from .agent_runtime import _instructions, _run_structured

    request = {
        "diagram_request": prompt,
        "frozen_evidence_context": evidence_context,
        "allowed_evidence_claim_ids": evidence_claim_ids or [],
    }
    plan = await _run_structured(
        "Evidence-bounded concept diagram planner",
        _instructions("concept_diagram_v1.md"),
        AIDiagramPlan,
        json.dumps(request, ensure_ascii=False, sort_keys=True),
        cwd=cwd,
        stage=MacroStage.SYNTHESIS,
        skill_id="manuscript-writing",
    )
    expected = set(evidence_claim_ids or [])
    if not set(plan.evidence_claim_ids).issubset(expected):
        raise ValueError("AI diagram cited claim IDs outside the supplied evidence envelope")
    validate_plan_evidence_bounds(plan, evidence_context=evidence_context)
    return plan


async def create_ai_drawio(
    prompt: str,
    destination: str | Path,
    *,
    evidence_context: str = "",
    evidence_claim_ids: list[str] | None = None,
    cwd: str | Path | None = None,
) -> dict[str, object]:
    """Generate a governed editable source plus its structured planning record."""

    source = Path(destination).resolve()
    plan = await generate_ai_diagram_plan(
        prompt,
        evidence_context=evidence_context,
        evidence_claim_ids=evidence_claim_ids,
        cwd=cwd,
    )
    write_drawio(source, render_ai_diagram_plan(plan))
    plan_path = source.with_suffix(source.suffix + ".plan.json")
    write_json_atomic(plan_path, plan)
    return {
        "source": str(source),
        "source_sha256": sha256_file(source),
        "plan": str(plan_path),
        "plan_sha256": sha256_file(plan_path),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "renderer": "research_forge_typed_plan_to_drawio",
        "interactive_editor": "next-ai-draw-io",
        "scientific_authority": False,
    }


def audit_ai_edited_drawio(
    source: str | Path,
    *,
    allowed_numeric_tokens: set[str] | None = None,
    max_cells: int = 250,
) -> DrawioSourceAudit:
    """Audit a browser-edited source before it may re-enter publication assets."""

    path = Path(source).resolve()
    raw = path.read_text(encoding="utf-8")
    violations: list[str] = []
    if _FORBIDDEN_XML.search(raw):
        violations.append("active or embedded content is forbidden")
    if re.search(
        r"(?:href|link)\s*=(?:&quot;|[\"'])|https?://",
        raw,
        re.IGNORECASE,
    ):
        violations.append("external links and remote images are forbidden")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        root = None
        violations.append(f"invalid XML: {exc}")
    cells: list[ET.Element] = []
    if root is not None:
        if root.tag not in {"mxfile", "mxGraphModel"}:
            violations.append("root must be mxfile or mxGraphModel")
        cells = list(root.iter("mxCell"))
        if len(cells) > max_cells:
            violations.append(f"cell count exceeds limit: {len(cells)} > {max_cells}")
        for element in root.iter():
            for key, value in element.attrib.items():
                lowered = f"{key}={value}".lower()
                if (
                    key.lower() in {"href", "link"}
                    or "image=http://" in lowered
                    or "image=https://" in lowered
                ):
                    if "external links and remote images are forbidden" not in violations:
                        violations.append(
                            "external links and remote images are forbidden"
                        )
                    break
    visible_text = " ".join(
        cell.attrib.get("value", "") for cell in cells if cell.attrib.get("value")
    )
    numeric_tokens = sorted(set(_NUMBER.findall(visible_text)))
    allowed = allowed_numeric_tokens or set()
    unauthorized = sorted(set(numeric_tokens).difference(allowed))
    if unauthorized:
        violations.append("diagram contains numeric tokens not bound to frozen evidence")
    return DrawioSourceAudit(
        passed=not violations,
        source_path=str(path),
        source_sha256=sha256_file(path),
        cell_count=len(cells),
        numeric_tokens=numeric_tokens,
        unauthorized_numeric_tokens=unauthorized,
        violations=violations,
    )
