from __future__ import annotations

"""Runtime contracts for evidence-first, venue-aware manuscript authoring."""

import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from .ai_drawio import (
    NEXT_AI_DRAWIO_MCP_PACKAGE,
    audit_ai_edited_drawio,
    numeric_tokens_in_text,
    write_next_ai_drawio_mcp_config,
)
from .drawio_backend import (
    DrawioDiagram,
    DrawioEdge,
    DrawioNode,
    export_drawio,
    find_drawio,
    write_drawio,
)
from .models import LiteratureSource, StrictModel, utc_now
from .paper_pipeline import PaperStructureContract
from .storage import read_json, sha256_file, write_json_atomic

_PUBLICATION_CODE_IDENTIFIER_RE = re.compile(
    r"(?<![\w])(?:[A-Za-z][A-Za-z0-9]*_){1,}[A-Za-z0-9]+(?![\w])"
)
_CITATION_TOKEN_RE = re.compile(r"\[resource-[a-f0-9]{8,}\]")
_PUBLICATION_OBJECT_ID_RE = re.compile(
    r"\b(?:resource-selection|formal-primary-task|study|task|run|step)-"
    r"[a-z0-9][a-z0-9-]{7,}\b"
)
_PUBLICATION_KEY_VALUE_RE = re.compile(
    r"\b[a-z][a-z0-9_]*=(?:true|false|null|[a-z][a-z0-9_]*|\d+)\b"
)
_PUBLICATION_AUTHORITY_ALIASES = {
    "frozen protocol and eligibility": "冻结的研究协议与资格规则",
    "deterministic integrity and exact bindings": "确定性完整性检查与精确证据绑定",
    "machine-readable outputs": "机器可读实验输出",
    "frozen statistical rules": "预先冻结的统计判定规则",
    "append-only human adjudication": "仅追加的人类裁决记录",
    "AI scientific review": "AI 科学评审",
    "NLI risk alert": "自然语言推断风险告警",
    "evaluator_policy": "评估器策略",
    "uses_learned_evaluator=false": "未使用学习型评估器",
    "approved_benchmark_ids": "已批准基准清单",
    "resource_selection_id": "资源选择记录",
    "authority_order": "证据权威顺序",
    "confirmatory_status=untouched": "确认性状态未发生变更",
    "qualification=incomplete": "资格审查未完成",
    "decision=unverifiable": "研究结论不可验证",
    "independent_unit_count=0": "独立分析单元数为0",
    "variance_unit=registered_pair": "方差单位登记为注册配对",
    "default split": "默认分析分层",
}
_PUBLICATION_WORD_ALIASES = {
    "qualified": "具备分析资格",
    "inconclusive": "证据不足",
    "incomplete": "资格信息不完整",
    "disqualified": "不具备分析资格",
    "untouched": "未变更",
    "unverifiable": "不可验证",
}


def publication_code_identifiers(text: str) -> list[str]:
    """Find implementation identifiers that do not belong in a paper title."""

    return list(dict.fromkeys(_PUBLICATION_CODE_IDENTIFIER_RE.findall(text)))


def academicize_publication_text(
    text: str,
    *,
    aliases: dict[str, str] | None = None,
) -> str:
    """Translate audit vocabulary into reader-facing scientific language.

    The exact values remain unchanged in frozen contracts and audit artifacts.
    This function is only for the rendered manuscript representation.
    """

    rendered = text
    replacements = dict(_PUBLICATION_AUTHORITY_ALIASES)
    replacements.update(aliases or {})
    for internal, public in sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if internal:
            rendered = rendered.replace(internal, public)
    for internal, public in _PUBLICATION_WORD_ALIASES.items():
        rendered = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(internal)}(?![A-Za-z0-9_])",
            public,
            rendered,
        )
    rendered = _PUBLICATION_OBJECT_ID_RE.sub("冻结的内部记录", rendered)
    return rendered


def publication_internal_tokens(text: str) -> list[str]:
    """Find audit-layer tokens that remain in reader-facing prose."""

    scrubbed = _CITATION_TOKEN_RE.sub("", text)
    scrubbed = re.sub(r"paper_assets[/\\][^\s)\]}]+", "", scrubbed)
    found = [
        *_PUBLICATION_CODE_IDENTIFIER_RE.findall(scrubbed),
        *_PUBLICATION_OBJECT_ID_RE.findall(scrubbed),
        *_PUBLICATION_KEY_VALUE_RE.findall(scrubbed),
    ]
    for internal in _PUBLICATION_AUTHORITY_ALIASES:
        if internal in scrubbed:
            found.append(internal)
    for internal in _PUBLICATION_WORD_ALIASES:
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(internal)}(?![A-Za-z0-9_])",
            scrubbed,
        ):
            found.append(internal)
    return list(dict.fromkeys(found))


class SubmissionGenreProfile(StrictModel):
    schema_version: int = 1
    profile_id: str
    configured_at: str = Field(default_factory=utc_now)
    venue_id: str | None = None
    venue_name: str | None = None
    document_type: Literal["research_article"] = "research_article"
    language: Literal["zh", "en"] = "zh"
    structure_profile_id: str
    abstract_style: Literal["unstructured", "structured"]
    abstract_paragraph_count: int | None = Field(default=1, ge=1, le=8)
    abstract_minimum_characters: int = Field(ge=50, le=5_000)
    abstract_maximum_characters: int = Field(ge=100, le=10_000)
    renderer_owns_headings: bool = True
    renderer_owns_numbering: bool = True
    figures_must_bind_frozen_evidence: bool = True
    tables_must_bind_frozen_evidence: bool = True
    author_approval_required: bool = True

    @model_validator(mode="after")
    def limits_are_ordered(self) -> "SubmissionGenreProfile":
        if self.abstract_minimum_characters >= self.abstract_maximum_characters:
            raise ValueError("abstract minimum must be smaller than maximum")
        return self


class EvidencePointer(StrictModel):
    path: str
    sha256: str | None = None
    json_path: str | None = None
    source_id: str | None = None
    evidence_type: Literal["project_artifact", "verified_literature"]


class EvidenceClaimBinding(StrictModel):
    claim_id: str
    kind: str
    statement: str
    evidence: list[EvidencePointer]
    allowed_sections: list[str]
    claim_strength: Literal[
        "descriptive", "associational", "comparative", "causal", "limitation"
    ]
    evidence_status: Literal["bound", "context_only", "unsupported"]


class EvidenceClaimMap(StrictModel):
    schema_version: int = 1
    built_at: str = Field(default_factory=utc_now)
    track_id: str
    frozen_conclusion: str
    bindings: list[EvidenceClaimBinding]
    verified_source_ids: list[str]
    forbidden_moves: list[str]
    source_registry_sha256: str

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> "EvidenceClaimMap":
        claim_ids = [item.claim_id for item in self.bindings]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("evidence-claim map contains duplicate claim IDs")
        if len(self.verified_source_ids) != len(set(self.verified_source_ids)):
            raise ValueError("evidence-claim map contains duplicate source IDs")
        return self


class FigureSlot(StrictModel):
    slot_id: str = Field(pattern=r"^fig-[a-z0-9-]{2,80}$")
    purpose: str = Field(min_length=10, max_length=1000)
    evidence_claim_ids: list[str] = Field(min_length=1, max_length=50)
    source_paths: list[str] = Field(min_length=1, max_length=100)
    chart_type: Literal[
        "flow", "bar", "line", "scatter", "forest", "heatmap", "diagram"
    ]
    renderer: Literal["auto", "drawio", "next_ai_drawio", "native_svg"] = (
        "next_ai_drawio"
    )
    caption_contract: str = Field(min_length=10, max_length=1500)


class TableSlot(StrictModel):
    slot_id: str = Field(pattern=r"^tab-[a-z0-9-]{2,80}$")
    purpose: str = Field(min_length=10, max_length=1000)
    evidence_claim_ids: list[str] = Field(min_length=1, max_length=100)
    source_paths: list[str] = Field(min_length=1, max_length=100)
    columns: list[str] = Field(min_length=2, max_length=30)
    caption_contract: str = Field(min_length=10, max_length=1500)


class OutlineNode(StrictModel):
    node_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,80}$")
    section_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,80}$")
    heading: str = Field(min_length=2, max_length=300)
    level: int = Field(ge=2, le=4)
    purpose: str = Field(min_length=10, max_length=1500)
    argument: str = Field(min_length=10, max_length=2500)
    claim_ids: list[str] = Field(default_factory=list, max_length=100)
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    figure_slot_ids: list[str] = Field(default_factory=list, max_length=30)
    table_slot_ids: list[str] = Field(default_factory=list, max_length=30)
    children: list["OutlineNode"] = Field(default_factory=list, max_length=30)


class HierarchicalPaperOutline(StrictModel):
    schema_version: int = 1
    created_at: str = Field(default_factory=utc_now)
    title: str = Field(min_length=5, max_length=500)
    thesis: str = Field(min_length=20, max_length=3000)
    abstract_moves: list[str] = Field(min_length=5, max_length=5)
    sections: list[OutlineNode] = Field(min_length=6, max_length=30)
    figure_slots: list[FigureSlot] = Field(default_factory=list, max_length=30)
    table_slots: list[TableSlot] = Field(default_factory=list, max_length=30)


class PublicationTitleCandidate(StrictModel):
    """A reader-facing title proposed from an already frozen study boundary."""

    schema_version: int = 1
    title: str = Field(min_length=5, max_length=300)
    basis: list[str] = Field(min_length=1, max_length=6)
    avoids_result_overclaim: bool
    avoids_internal_identifiers: bool


def validate_publication_title(title: str) -> list[str]:
    """Reject implementation vocabulary before a title can reach a manuscript."""

    violations: list[str] = []
    leaked = publication_internal_tokens(title)
    if leaked:
        violations.append(
            "title exposes internal implementation or audit identifiers: "
            + ", ".join(leaked)
        )
    if "\n" in title or "\r" in title:
        violations.append("title must be a single line")
    if len(title.strip()) < 5:
        violations.append("title is too short")
    return violations


def validate_outline(
    outline: HierarchicalPaperOutline,
    *,
    contract: PaperStructureContract,
    evidence_claim_map: EvidenceClaimMap,
) -> list[str]:
    """Check that an outline stays inside the venue and evidence contracts."""

    violations: list[str] = []
    title_violations = validate_publication_title(outline.title)
    violations.extend(f"outline {item}" for item in title_violations)
    claim_ids = {item.claim_id for item in evidence_claim_map.bindings}
    source_ids = set(evidence_claim_map.verified_source_ids)
    figure_ids = {item.slot_id for item in outline.figure_slots}
    table_ids = {item.slot_id for item in outline.table_slots}
    if len(figure_ids) != len(outline.figure_slots):
        violations.append("outline contains duplicate figure slot IDs")
    if len(table_ids) != len(outline.table_slots):
        violations.append("outline contains duplicate table slot IDs")

    flattened: list[OutlineNode] = []

    def visit(node: OutlineNode, parent_level: int | None = None) -> None:
        flattened.append(node)
        if parent_level is not None and node.level != parent_level + 1:
            violations.append(
                f"outline node {node.node_id} has level {node.level}; expected {parent_level + 1}"
            )
        unknown_claims = sorted(set(node.claim_ids) - claim_ids)
        unknown_sources = sorted(set(node.source_ids) - source_ids)
        unknown_figures = sorted(set(node.figure_slot_ids) - figure_ids)
        unknown_tables = sorted(set(node.table_slot_ids) - table_ids)
        if unknown_claims:
            violations.append(
                f"outline node {node.node_id} cites unknown claims: {', '.join(unknown_claims)}"
            )
        if unknown_sources:
            violations.append(
                f"outline node {node.node_id} cites unknown sources: {', '.join(unknown_sources)}"
            )
        if unknown_figures:
            violations.append(
                f"outline node {node.node_id} cites unknown figure slots: {', '.join(unknown_figures)}"
            )
        if unknown_tables:
            violations.append(
                f"outline node {node.node_id} cites unknown table slots: {', '.join(unknown_tables)}"
            )
        for child in node.children:
            visit(child, node.level)

    for section in outline.sections:
        visit(section)

    top_level_keys = [item.section_key for item in outline.sections]
    required_keys = [item.key for item in contract.sections if item.required]
    missing = [key for key in required_keys if key not in top_level_keys]
    if missing:
        violations.append("outline is missing required sections: " + ", ".join(missing))
    ordered = [top_level_keys.index(key) for key in required_keys if key in top_level_keys]
    if ordered != sorted(ordered):
        violations.append("outline sections are out of venue-contract order")
    for item in outline.figure_slots:
        unknown = sorted(set(item.evidence_claim_ids) - claim_ids)
        if unknown:
            violations.append(
                f"figure slot {item.slot_id} cites unknown claims: {', '.join(unknown)}"
            )
        bound_paths = {
            pointer.path
            for binding in evidence_claim_map.bindings
            if binding.claim_id in item.evidence_claim_ids
            for pointer in binding.evidence
        }
        if set(item.source_paths) != bound_paths:
            violations.append(
                f"figure slot {item.slot_id} source paths do not exactly match frozen claim bindings"
            )
    for item in outline.table_slots:
        unknown = sorted(set(item.evidence_claim_ids) - claim_ids)
        if unknown:
            violations.append(
                f"table slot {item.slot_id} cites unknown claims: {', '.join(unknown)}"
            )
        bound_paths = {
            pointer.path
            for binding in evidence_claim_map.bindings
            if binding.claim_id in item.evidence_claim_ids
            for pointer in binding.evidence
        }
        if set(item.source_paths) != bound_paths:
            violations.append(
                f"table slot {item.slot_id} source paths do not exactly match frozen claim bindings"
            )
    return list(dict.fromkeys(violations))


class ReviewFinding(StrictModel):
    finding_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,100}$")
    severity: Literal["blocking", "major", "minor"]
    category: Literal[
        "evidence",
        "falsifiability",
        "statistics",
        "information",
        "structure",
        "genre",
        "clarity",
    ]
    location: str
    diagnosis: str = Field(min_length=10, max_length=2500)
    required_change: str = Field(min_length=10, max_length=2500)
    claim_ids: list[str] = Field(default_factory=list, max_length=50)


class RoleReview(StrictModel):
    schema_version: int = 1
    reviewed_at: str = Field(default_factory=utc_now)
    role: Literal["feynman", "tukey", "shannon", "popper", "editor"]
    artifact: Literal["outline", "draft"]
    recommendation: Literal["accept", "revise", "abstain"]
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=100)
    abstention_reason: str | None = Field(default=None, max_length=1000)


class PanelDecision(StrictModel):
    schema_version: int = 1
    decided_at: str = Field(default_factory=utc_now)
    artifact: Literal["outline", "draft"]
    decision: Literal["accept", "revise", "halt"]
    reviews: list[RoleReview]
    blocking_finding_ids: list[str]
    required_finding_ids: list[str]
    abstaining_roles: list[str]


class PaperArtifactRecord(StrictModel):
    slot_id: str
    kind: Literal["figure", "table"]
    status: Literal["rendered", "needs_evidence"]
    evidence_claim_ids: list[str]
    source_paths: list[str]
    data_path: str | None = None
    data_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    artifact_path: str | None = None
    artifact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    editable_source_path: str | None = None
    editable_source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    editor_backend: Literal["next_ai_drawio"] | None = None
    editor_package: str | None = None
    editor_config_path: str | None = None
    editor_config_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    editor_audit_path: str | None = None
    editor_audit_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    editor_audit_passed: bool | None = None
    renderer: str | None = None
    rendering_code_path: str | None = None
    rendering_code_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    rendered_markdown: str | None = None
    reason: str


class PaperArtifactManifest(StrictModel):
    schema_version: int = 1
    built_at: str = Field(default_factory=utc_now)
    ready: bool
    records: list[PaperArtifactRecord]
    unresolved_slot_ids: list[str]
    policy: str = "frozen_evidence_only"


_FINAL_NUMBER_RE = re.compile(r"=\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$")


def _slot_rows(
    claim_ids: list[str], evidence_claim_map: EvidenceClaimMap
) -> list[dict[str, str]]:
    by_id = {item.claim_id: item for item in evidence_claim_map.bindings}
    rows: list[dict[str, str]] = []
    for claim_id in claim_ids:
        binding = by_id.get(claim_id)
        if binding is None:
            continue
        pointers = binding.evidence or [None]
        for pointer in pointers:
            rows.append(
                {
                    "claim_id": claim_id,
                    "statement": binding.statement,
                    "source_path": pointer.path if pointer is not None else "",
                    "json_path": pointer.json_path or "" if pointer is not None else "",
                    "sha256": pointer.sha256 or "" if pointer is not None else "",
                }
            )
    return rows


def _write_slot_csv(path: Path, rows: list[dict[str, str]]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["claim_id", "statement", "source_path", "json_path", "sha256"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _provenance_svg(slot: FigureSlot, rows: list[dict[str, str]]) -> str:
    width = 960
    item_height = 72
    height = 120 + max(1, len(rows)) * item_height
    items: list[str] = []
    for index, row in enumerate(rows):
        y = 88 + index * item_height
        label = html.escape(f"{row['claim_id']}: {row['statement'][:74]}")
        items.append(
            f'<rect x="70" y="{y}" width="820" height="48" rx="12" fill="#f5f7fa" stroke="#7d8790"/>'
            f'<text x="92" y="{y + 30}" font-family="Arial, sans-serif" font-size="16" fill="#202124">{label}</text>'
        )
    title = html.escape(slot.purpose[:100])
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="white"/>'
        f'<text x="70" y="48" font-family="Arial, sans-serif" font-size="22" font-weight="600" fill="#111">{title}</text>'
        + "".join(items)
        + "</svg>\n"
    )


def _numeric_svg(slot: FigureSlot, rows: list[dict[str, str]]) -> str | None:
    def display_label(claim_id: str) -> str:
        """Project internal claim identifiers into publication-facing labels."""

        if ":arm:" in claim_id:
            label = claim_id.rsplit(":arm:", 1)[1]
        elif ":contrast:" in claim_id:
            label = claim_id.rsplit(":contrast:", 1)[1]
        else:
            label = claim_id.rsplit(":", 1)[-1]
        abbreviations = {"lora": "LoRA", "nli": "NLI", "llm": "LLM"}
        return " ".join(
            abbreviations.get(word.lower(), word.capitalize())
            for word in label.replace("-", "_").split("_")
            if word
        )[:34]

    values: list[tuple[str, float]] = []
    for row in rows:
        match = _FINAL_NUMBER_RE.search(row["statement"])
        if match is not None:
            values.append(
                (display_label(row["claim_id"]), float(match.group(1)))
            )
    if not values:
        return None
    if any(value < 0 for _, value in values):
        return None
    width = 960
    height = 520
    baseline = 420
    chart_height = 300
    maximum = max(abs(value) for _, value in values) or 1.0
    bar_width = max(20, min(90, 700 // len(values)))
    gap = max(12, (780 - bar_width * len(values)) // (len(values) + 1))
    bars: list[str] = []
    x = 90 + gap
    for label, value in values:
        bar_height = abs(value) / maximum * chart_height
        y = baseline - bar_height
        bars.append(
            f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" height="{bar_height:.1f}" fill="#2676d9"/>'
            f'<text x="{x + bar_width / 2:.1f}" y="{baseline + 26}" text-anchor="middle" font-family="Arial, sans-serif" font-size="13">{html.escape(label)}</text>'
            f'<text x="{x + bar_width / 2:.1f}" y="{max(78, y - 8):.1f}" text-anchor="middle" font-family="Arial, sans-serif" font-size="13">{value:g}</text>'
        )
        x += bar_width + gap
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="white"/>'
        f'<text x="70" y="48" font-family="Arial, sans-serif" font-size="22" font-weight="600">{html.escape(slot.purpose[:100])}</text>'
        f'<line x1="70" y1="{baseline}" x2="900" y2="{baseline}" stroke="#333"/>'
        + "".join(bars)
        + "</svg>\n"
    )


def _provenance_drawio(slot: FigureSlot, rows: list[dict[str, str]]) -> DrawioDiagram:
    nodes: list[DrawioNode] = []
    edges: list[DrawioEdge] = []
    for index, row in enumerate(rows):
        node_id = f"claim-{index + 1}"
        statement = row["statement"]
        if len(statement) > 92:
            statement = statement[:89].rstrip() + "..."
        nodes.append(
            DrawioNode(
                node_id=node_id,
                label=(
                    f"{html.escape(row['claim_id'])}<br>"
                    f"<font color=\"#52616b\">{html.escape(statement)}</font>"
                ),
                x=40 + index * 230,
                y=90,
                width=205,
                height=92,
            )
        )
        if index:
            edges.append(DrawioEdge(source=f"claim-{index}", target=node_id))
    return DrawioDiagram(title=slot.purpose[:100], nodes=tuple(nodes), edges=tuple(edges))


def build_paper_artifacts(
    root: Path,
    *,
    outline: HierarchicalPaperOutline,
    evidence_claim_map: EvidenceClaimMap,
) -> PaperArtifactManifest:
    """Materialize approved slots from frozen evidence, never from draft prose."""

    assets = root / "stage_4_synthesis" / "paper_assets"
    synthesis_dir = root / "stage_4_synthesis"
    records: list[PaperArtifactRecord] = []
    rendering_code_path = "research_forge/paper_authoring.py"
    rendering_code_sha256 = sha256_file(Path(__file__).resolve())
    editor_config = assets / "next-ai-drawio.mcp.json"
    for slot in outline.table_slots:
        rows = _slot_rows(slot.evidence_claim_ids, evidence_claim_map)
        data = assets / "data" / f"{slot.slot_id}.csv"
        if (
            slot.slot_id == "tab-canonical-lineage"
            and rows
            and rows[0]["source_path"]
        ):
            import csv

            study_root = (
                root.parents[1]
                if root.parent.name == "stage4_revisions"
                else root
            )
            source = study_root / rows[0]["source_path"]
            payload = read_json(source)
            lineage = list(payload.get("lineage") or [])
            if lineage:
                data.parent.mkdir(parents=True, exist_ok=True)
                fields = [
                    "arm_id",
                    "seed",
                    "run_cell_id",
                    "attempt_id",
                    "attempt_sha256",
                    "result_id",
                    "result_sha256",
                    "status",
                    "exit_status",
                ]
                with data.open(
                    "w", encoding="utf-8", newline=""
                ) as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(
                        {field: item.get(field) for field in fields}
                        for item in lineage
                    )
                header = (
                    "| Arm | Seed | Attempt hash | Result hash | Exit |\n"
                    "|---|---:|---|---|---|"
                )
                body = "\n".join(
                    "| {arm} | {seed} | `{attempt_hash}` | `{result_hash}` "
                    "| {status} ({exit_status}) |".format(
                        arm=item["arm_id"],
                        seed=item["seed"],
                        attempt_hash=str(item["attempt_sha256"])[:12],
                        result_hash=str(item["result_sha256"])[:12],
                        status=item["status"],
                        exit_status=item["exit_status"],
                    )
                    for item in lineage
                )
                caption = re.sub(
                    r"^(?:Table|表)\s*\d*\s*[:：.]?\s*",
                    "",
                    slot.caption_contract,
                ).strip()
                records.append(
                    PaperArtifactRecord(
                        slot_id=slot.slot_id,
                        kind="table",
                        status="rendered",
                        evidence_claim_ids=slot.evidence_claim_ids,
                        source_paths=slot.source_paths,
                        data_path=str(
                            data.relative_to(synthesis_dir)
                        ).replace("\\", "/"),
                        data_sha256=sha256_file(data),
                        rendered_markdown=(
                            f"Table: {caption}\n{header}\n{body}"
                        ),
                        rendering_code_path=rendering_code_path,
                        rendering_code_sha256=rendering_code_sha256,
                        reason=(
                            "lineage rows were generated directly from the "
                            "frozen publication-context artifact"
                        ),
                    )
                )
                continue
        if rows and all(row["source_path"] for row in rows):
            _write_slot_csv(data, rows)
            header = "| Claim ID | Frozen statement | Evidence path |\n|---|---|---|"
            body = "\n".join(
                f"| `{row['claim_id']}` | {row['statement']} | `{row['source_path']}` |"
                for row in rows
            )
            caption = re.sub(r"^(?:Table|表)\s*\d*\s*[:：.]?\s*", "", slot.caption_contract).strip()
            markdown = f"Table: {caption}\n{header}\n{body}"
            records.append(
                PaperArtifactRecord(
                    slot_id=slot.slot_id,
                    kind="table",
                    status="rendered",
                    evidence_claim_ids=slot.evidence_claim_ids,
                    source_paths=slot.source_paths,
                    data_path=str(data.relative_to(synthesis_dir)).replace("\\", "/"),
                    data_sha256=sha256_file(data),
                    rendered_markdown=markdown,
                    rendering_code_path=rendering_code_path,
                    rendering_code_sha256=rendering_code_sha256,
                    reason="table rows were generated directly from frozen claim bindings",
                )
            )
        else:
            records.append(
                PaperArtifactRecord(
                    slot_id=slot.slot_id,
                    kind="table",
                    status="needs_evidence",
                    evidence_claim_ids=slot.evidence_claim_ids,
                    source_paths=slot.source_paths,
                    reason="one or more table claims lack a bound frozen evidence path",
                )
            )
    for slot in outline.figure_slots:
        rows = _slot_rows(slot.evidence_claim_ids, evidence_claim_map)
        data = assets / "data" / f"{slot.slot_id}.csv"
        figure = assets / "figures" / f"{slot.slot_id}.svg"
        editable_source: Path | None = None
        editor_audit_path: Path | None = None
        renderer = "native_svg"
        svg = None
        if rows and all(row["source_path"] for row in rows):
            _write_slot_csv(data, rows)
            if slot.chart_type in {"flow", "diagram"}:
                editable_source = assets / "figures" / f"{slot.slot_id}.drawio"
                figure = assets / "figures" / f"{slot.slot_id}.svg"
                write_drawio(editable_source, _provenance_drawio(slot, rows))
                write_next_ai_drawio_mcp_config(editor_config)
                evidence_text = " ".join(
                    f"{row['claim_id']} {row['statement']}" for row in rows
                )
                editor_audit = audit_ai_edited_drawio(
                    editable_source,
                    allowed_numeric_tokens=numeric_tokens_in_text(evidence_text),
                )
                editor_audit_path = (
                    assets / "audits" / f"{slot.slot_id}.next-ai-drawio.json"
                )
                write_json_atomic(editor_audit_path, editor_audit)
                if not editor_audit.passed:
                    records.append(
                        PaperArtifactRecord(
                            slot_id=slot.slot_id,
                            kind="figure",
                            status="needs_evidence",
                            evidence_claim_ids=slot.evidence_claim_ids,
                            source_paths=slot.source_paths,
                            data_path=str(data.relative_to(synthesis_dir)).replace("\\", "/"),
                            data_sha256=sha256_file(data),
                            editable_source_path=str(
                                editable_source.relative_to(synthesis_dir)
                            ).replace("\\", "/"),
                            editable_source_sha256=sha256_file(editable_source),
                            editor_backend="next_ai_drawio",
                            editor_package=NEXT_AI_DRAWIO_MCP_PACKAGE,
                            editor_config_path=str(
                                editor_config.relative_to(synthesis_dir)
                            ).replace("\\", "/"),
                            editor_config_sha256=sha256_file(editor_config),
                            editor_audit_path=str(
                                editor_audit_path.relative_to(synthesis_dir)
                            ).replace("\\", "/"),
                            editor_audit_sha256=sha256_file(editor_audit_path),
                            editor_audit_passed=False,
                            renderer="next_ai_drawio",
                            reason=(
                                "mandatory next-ai-draw-io source audit failed: "
                                + "; ".join(editor_audit.violations)
                            ),
                        )
                    )
                    continue
                if find_drawio() is None:
                    records.append(
                        PaperArtifactRecord(
                            slot_id=slot.slot_id,
                            kind="figure",
                            status="needs_evidence",
                            evidence_claim_ids=slot.evidence_claim_ids,
                            source_paths=slot.source_paths,
                            data_path=str(data.relative_to(synthesis_dir)).replace("\\", "/"),
                            data_sha256=sha256_file(data),
                            editable_source_path=str(
                                editable_source.relative_to(synthesis_dir)
                            ).replace("\\", "/"),
                            editable_source_sha256=sha256_file(editable_source),
                            editor_backend="next_ai_drawio",
                            editor_package=NEXT_AI_DRAWIO_MCP_PACKAGE,
                            editor_config_path=str(
                                editor_config.relative_to(synthesis_dir)
                            ).replace("\\", "/"),
                            editor_config_sha256=sha256_file(editor_config),
                            editor_audit_path=str(
                                editor_audit_path.relative_to(synthesis_dir)
                            ).replace("\\", "/"),
                            editor_audit_sha256=sha256_file(editor_audit_path),
                            editor_audit_passed=True,
                            renderer="next_ai_drawio",
                            reason=(
                                "mandatory next-ai-draw-io conceptual source is ready, "
                                "but draw.io Desktop is unavailable for vector export"
                            ),
                        )
                    )
                    continue
                export_drawio(editable_source, figure, format="svg")
                renderer = "next_ai_drawio"
            else:
                svg = _numeric_svg(slot, rows)
        if svg is None and not figure.is_file():
            records.append(
                PaperArtifactRecord(
                    slot_id=slot.slot_id,
                    kind="figure",
                    status="needs_evidence",
                    evidence_claim_ids=slot.evidence_claim_ids,
                    source_paths=slot.source_paths,
                    data_path=(str(data.relative_to(synthesis_dir)).replace("\\", "/") if data.is_file() else None),
                    reason="the approved chart type could not be rendered from bound frozen numeric evidence",
                )
            )
            continue
        figure.parent.mkdir(parents=True, exist_ok=True)
        if svg is not None:
            figure.write_text(svg, encoding="utf-8", newline="\n")
        relative_figure = str(figure.relative_to(synthesis_dir)).replace("\\", "/")
        records.append(
            PaperArtifactRecord(
                slot_id=slot.slot_id,
                kind="figure",
                status="rendered",
                evidence_claim_ids=slot.evidence_claim_ids,
                source_paths=slot.source_paths,
                data_path=str(data.relative_to(synthesis_dir)).replace("\\", "/"),
                data_sha256=sha256_file(data),
                artifact_path=relative_figure,
                artifact_sha256=sha256_file(figure),
                editable_source_path=(
                    str(editable_source.relative_to(synthesis_dir)).replace("\\", "/")
                    if editable_source is not None
                    else None
                ),
                editable_source_sha256=(
                    sha256_file(editable_source) if editable_source is not None else None
                ),
                editor_backend=(
                    "next_ai_drawio" if renderer == "next_ai_drawio" else None
                ),
                editor_package=(
                    NEXT_AI_DRAWIO_MCP_PACKAGE
                    if renderer == "next_ai_drawio"
                    else None
                ),
                editor_config_path=(
                    str(editor_config.relative_to(synthesis_dir)).replace("\\", "/")
                    if renderer == "next_ai_drawio"
                    else None
                ),
                editor_config_sha256=(
                    sha256_file(editor_config)
                    if renderer == "next_ai_drawio"
                    else None
                ),
                editor_audit_path=(
                    str(editor_audit_path.relative_to(synthesis_dir)).replace("\\", "/")
                    if editor_audit_path is not None
                    else None
                ),
                editor_audit_sha256=(
                    sha256_file(editor_audit_path)
                    if editor_audit_path is not None
                    else None
                ),
                editor_audit_passed=(
                    True if renderer == "next_ai_drawio" else None
                ),
                renderer=renderer,
                rendering_code_path=rendering_code_path,
                rendering_code_sha256=rendering_code_sha256,
                rendered_markdown=f"![{slot.caption_contract}]({relative_figure})",
                reason=(
                    "mandatory next-ai-draw-io source passed audit and was exported from frozen claim bindings"
                    if renderer == "next_ai_drawio"
                    else "figure was rendered deterministically from frozen claim bindings"
                ),
            )
        )
    unresolved = [item.slot_id for item in records if item.status != "rendered"]
    manifest = PaperArtifactManifest(
        ready=not unresolved,
        records=records,
        unresolved_slot_ids=unresolved,
    )
    write_json_atomic(root / "stage_4_synthesis" / "paper_artifact_manifest.json", manifest)
    return manifest


def materialize_artifact_callouts(markdown: str, manifest: PaperArtifactManifest) -> str:
    rendered = {
        ("FIGURE" if item.kind == "figure" else "TABLE", item.slot_id): item.rendered_markdown
        for item in manifest.records
        if item.status == "rendered" and item.rendered_markdown
    }

    def replace(match: re.Match[str]) -> str:
        key = (match.group(1), match.group(2))
        replacement = rendered.get(key)
        if not replacement:
            return match.group(0)
        # Artifact callouts may be embedded at the end of a prose sentence.
        # Rendered figures and tables are block elements and must be isolated
        # so caption detection and LaTeX conversion cannot merge them into the
        # surrounding paragraph.
        return "\n\n" + str(replacement).strip() + "\n\n"

    return re.sub(r"\[(FIGURE|TABLE):([a-z0-9-]{3,84})\]", replace, markdown)


def default_submission_genre(
    contract: PaperStructureContract,
    *,
    language: Literal["zh", "en"] = "zh",
) -> SubmissionGenreProfile:
    abstract = contract.abstract
    return SubmissionGenreProfile(
        profile_id=f"{contract.profile_id}-submission",
        structure_profile_id=contract.profile_id,
        language=language,
        abstract_style=abstract.style,
        abstract_paragraph_count=abstract.paragraph_count,
        abstract_minimum_characters=abstract.minimum_characters,
        abstract_maximum_characters=abstract.maximum_characters,
    )


def load_or_create_submission_genre(
    root: Path, contract: PaperStructureContract
) -> SubmissionGenreProfile:
    path = root / "stage_4_synthesis" / "submission_genre.json"
    if path.is_file():
        profile = SubmissionGenreProfile.model_validate(read_json(path))
        if profile.structure_profile_id != contract.profile_id:
            raise ValueError(
                "submission genre and paper structure profile disagree; create a new venue revision"
            )
        return profile
    profile = default_submission_genre(contract)
    write_json_atomic(path, profile)
    return profile


def _registry_sha256(claims: dict[str, Any], sources: list[LiteratureSource]) -> str:
    payload = {
        "claims": claims,
        "sources": [source.model_dump(mode="json") for source in sources],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_evidence_claim_map(
    *,
    claims: dict[str, Any],
    verdict: dict[str, Any],
    sources: list[LiteratureSource],
) -> EvidenceClaimMap:
    bindings: list[EvidenceClaimBinding] = []
    for raw in claims.get("claims") or []:
        kind = str(raw.get("kind") or "unknown")
        pointers = [
            EvidencePointer(
                path=str(item.get("path") or ""),
                sha256=str(item["sha256"]) if item.get("sha256") else None,
                json_path=str(item["json_path"]) if item.get("json_path") else None,
                evidence_type="project_artifact",
            )
            for item in raw.get("evidence") or []
            if item.get("path")
        ]
        if kind == "method":
            sections = ["abstract", "introduction", "methods"]
            strength = "descriptive"
        elif kind in {"result", "result_metric"}:
            sections = ["abstract", "results", "discussion", "conclusion"]
            strength = "comparative" if kind == "result_metric" else "descriptive"
        elif kind == "limitation":
            sections = ["abstract", "discussion", "limitations", "conclusion"]
            strength = "limitation"
        else:
            sections = ["introduction", "discussion"]
            strength = "descriptive"
        bindings.append(
            EvidenceClaimBinding(
                claim_id=str(raw.get("claim_id")),
                kind=kind,
                statement=str(raw.get("statement") or ""),
                evidence=pointers,
                allowed_sections=sections,
                claim_strength=strength,  # type: ignore[arg-type]
                evidence_status="bound" if pointers else (
                    "context_only" if kind == "limitation" else "unsupported"
                ),
            )
        )
    for source in sources:
        bindings.append(
            EvidenceClaimBinding(
                claim_id=f"literature-{source.source_id}",
                kind="background",
                statement=source.notes.strip() or source.title,
                evidence=[
                    EvidencePointer(
                        path=source.locator,
                        source_id=source.source_id,
                        evidence_type="verified_literature",
                    )
                ],
                allowed_sections=["introduction", "related_work", "discussion"],
                claim_strength="descriptive",
                evidence_status="context_only",
            )
        )
    return EvidenceClaimMap(
        track_id=str(claims.get("track_id") or verdict.get("track_id") or "unknown"),
        frozen_conclusion=str(verdict.get("conclusion") or ""),
        bindings=bindings,
        verified_source_ids=[source.source_id for source in sources],
        forbidden_moves=[
            "invent a result, statistic, citation, author, venue, or experiment",
            "upgrade descriptive or comparative evidence to a causal claim",
            "treat project-authored interpretation as independent replication",
            "change the frozen conclusion or any frozen numeric value",
            "fill a figure or table slot from prose instead of frozen evidence",
        ],
        source_registry_sha256=_registry_sha256(claims, sources),
    )


def decide_panel(artifact: Literal["outline", "draft"], reviews: list[RoleReview]) -> PanelDecision:
    expected = {"feynman", "tukey", "shannon", "popper"}
    represented = {review.role for review in reviews}
    abstentions = sorted(review.role for review in reviews if review.recommendation == "abstain")
    blocking = [
        finding.finding_id
        for review in reviews
        for finding in review.findings
        if finding.severity == "blocking"
    ]
    required = [
        finding.finding_id
        for review in reviews
        for finding in review.findings
        if finding.severity in {"blocking", "major"}
    ]
    if not expected <= represented or len(abstentions) >= 2:
        decision = "halt"
    elif blocking or required or any(review.recommendation == "revise" for review in reviews):
        decision = "revise"
    else:
        decision = "accept"
    return PanelDecision(
        artifact=artifact,
        decision=decision,
        reviews=reviews,
        blocking_finding_ids=list(dict.fromkeys(blocking)),
        required_finding_ids=list(dict.fromkeys(required)),
        abstaining_roles=abstentions,
    )
