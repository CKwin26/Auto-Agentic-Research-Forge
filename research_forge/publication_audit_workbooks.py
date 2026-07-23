"""Import the publication human audit from the reviewed Excel workbooks.

The importer is intentionally narrow.  It reads only the cells used by the
frozen audit workbook contract, keeps both source workbooks immutable, derives
the canonical JSON packets, and then delegates all scientific gate decisions
to :mod:`research_forge.publication_manual_audit`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .manual_audit import validate_auditor_packet_data
from .publication_manual_audit import (
    PublicationManualAuditError,
    _audit_paths,
    _load_frozen_sample,
    _validate_publication_adjudication,
    finalize_publication_manual_audit,
    prepare_publication_manual_audit_packets,
    submit_publication_manual_audits,
)
from .storage import read_json, sha256_file, write_json_atomic


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_REF = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")

_VERDICT_LABELS = {
    "支持": "supported",
    "supported": "supported",
    "不支持": "unsupported",
    "unsupported": "unsupported",
    "无法判断": "abstain",
    "無法判斷": "abstain",
    "abstain": "abstain",
}
_TRUE_LABELS = {"是", "yes", "true", "1"}


def _text(value: object) -> str:
    return str(value or "").strip().lstrip("\ufeff\u200b")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _xlsx_cells(path: Path) -> list[tuple[str, dict[str, str]]]:
    """Read text/cached values from an OOXML workbook without mutating it."""
    path = path.resolve()
    if not path.is_file():
        raise PublicationManualAuditError(f"audit workbook is missing: {path}")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PublicationManualAuditError(f"invalid xlsx workbook: {path}") from exc
    with archive:
        try:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        except (KeyError, ET.ParseError) as exc:
            raise PublicationManualAuditError(f"xlsx workbook structure is invalid: {path}") from exc

        relation_targets = {
            relation.attrib["Id"]: relation.attrib["Target"]
            for relation in relationships.findall(f"{{{_PKG_REL_NS}}}Relationship")
        }
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{{{_MAIN_NS}}}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{_MAIN_NS}}}t")))

        sheets: list[tuple[str, dict[str, str]]] = []
        sheet_root = workbook.find(f"{{{_MAIN_NS}}}sheets")
        if sheet_root is None:
            raise PublicationManualAuditError(f"xlsx workbook has no worksheets: {path}")
        for sheet in sheet_root.findall(f"{{{_MAIN_NS}}}sheet"):
            name = str(sheet.attrib.get("name", ""))
            relation_id = sheet.attrib.get(f"{{{_DOC_REL_NS}}}id")
            target = relation_targets.get(str(relation_id), "")
            if target.startswith("/"):
                member = target.lstrip("/")
            else:
                member = posixpath.normpath(posixpath.join("xl", target))
            try:
                root = ET.fromstring(archive.read(member))
            except (KeyError, ET.ParseError) as exc:
                raise PublicationManualAuditError(
                    f"xlsx worksheet {name!r} cannot be read: {path}"
                ) from exc
            values: dict[str, str] = {}
            for cell in root.iter(f"{{{_MAIN_NS}}}c"):
                address = str(cell.attrib.get("r", ""))
                if not _CELL_REF.fullmatch(address):
                    continue
                cell_type = cell.attrib.get("t")
                if cell_type == "inlineStr":
                    value = "".join(
                        node.text or "" for node in cell.iter(f"{{{_MAIN_NS}}}t")
                    )
                else:
                    value_node = cell.find(f"{{{_MAIN_NS}}}v")
                    value = value_node.text if value_node is not None and value_node.text else ""
                    if cell_type == "s" and value:
                        try:
                            value = shared[int(value)]
                        except (IndexError, ValueError) as exc:
                            raise PublicationManualAuditError(
                                f"xlsx shared-string index is invalid in {path}"
                            ) from exc
                    elif cell_type == "b":
                        value = "TRUE" if value == "1" else "FALSE"
                values[address] = value
            sheets.append((name, values))
        return sheets


def _audit_rows(sheets: list[tuple[str, dict[str, str]]]) -> list[dict[str, str]]:
    candidates: list[list[dict[str, str]]] = []
    for _, cells in sheets:
        rows: list[dict[str, str]] = []
        for row in range(1, 500):
            audit_id = _text(cells.get(f"A{row}"))
            if audit_id.startswith("audit-"):
                rows.append(
                    {
                        "audit_id": audit_id,
                        "claim_text": _text(cells.get(f"C{row}")),
                        "code": _text(cells.get(f"E{row}")),
                        "rationale": _text(cells.get(f"F{row}")),
                    }
                )
        if rows:
            candidates.append(rows)
    if len(candidates) != 1:
        raise PublicationManualAuditError(
            "each auditor workbook must contain exactly one audit table"
        )
    return candidates[0]


def _supplement_sheets(
    sheets: list[tuple[str, dict[str, str]]],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    if len(sheets) < 3:
        raise PublicationManualAuditError("supplement workbook must preserve all worksheets")
    by_name = {name: cells for name, cells in sheets}
    confirmation = by_name.get("补充确认") or by_name.get("補充確認") or sheets[0][1]
    adjudication = by_name.get("分歧裁决") or by_name.get("分歧裁決") or sheets[1][1]
    verification = by_name.get("核验摘要") or by_name.get("核驗摘要") or sheets[2][1]
    return confirmation, adjudication, verification


def _require_true(value: object, label: str) -> None:
    if _text(value).casefold() not in _TRUE_LABELS:
        raise PublicationManualAuditError(f"supplement must explicitly confirm {label}=yes")


def _mapping(confirmation: dict[str, str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for code, address in (("A", "D7"), ("B", "D8"), ("C", "D9")):
        label = _text(confirmation.get(address)).casefold()
        verdict = _VERDICT_LABELS.get(label)
        if verdict is None:
            raise PublicationManualAuditError(f"supplement {address} has an invalid verdict meaning")
        mapping[code] = verdict
    if set(mapping.values()) != {"supported", "unsupported", "abstain"}:
        raise PublicationManualAuditError("A/B/C must map one-to-one onto the three verdicts")
    return mapping


def _attestation(
    confirmation: dict[str, str], *, value_column: str, role: str
) -> tuple[dict[str, object], str]:
    auditor_id = _text(confirmation.get(f"{value_column}13"))
    completed_at = _text(confirmation.get(f"{value_column}14"))
    if not auditor_id:
        raise PublicationManualAuditError(f"{role} auditor ID is required")
    if not completed_at:
        raise PublicationManualAuditError(f"{role} completion time is required")
    for row, label in (
        (15, "worked independently"),
        (16, "did not view the other auditor answers"),
        (17, "did not view unblinding or automated evaluator output"),
        (18, "authorised the auditor-written unified rationale"),
    ):
        _require_true(confirmation.get(f"{value_column}{row}"), f"{role} {label}")
    unified_rationale = _text(confirmation.get(f"{value_column}19"))
    if not unified_rationale:
        raise PublicationManualAuditError(f"{role} unified rationale is required")
    return (
        {
            "auditor_id": auditor_id,
            "completed_at": completed_at,
            "worked_independently": True,
            "did_not_view_other_answers": True,
            "did_not_view_unblinding_or_evaluator_output": True,
        },
        unified_rationale,
    )


def _completed_packet(
    template: dict[str, Any],
    rows: list[dict[str, str]],
    sample: dict[str, Any],
    mapping: dict[str, str],
    attestation: dict[str, object],
    unified_rationale: str,
) -> tuple[dict[str, Any], int]:
    expected_items = list(sample["items"])
    if len(rows) != len(expected_items):
        raise PublicationManualAuditError(
            f"auditor workbook has {len(rows)} decisions; expected {len(expected_items)}"
        )
    packet = copy.deepcopy(template)
    packet["auditor_attestation"] = attestation
    blank_rationales = 0
    for index, (row, sample_item, packet_item) in enumerate(
        zip(rows, expected_items, packet["items"], strict=True)
    ):
        if row["audit_id"] != sample_item["audit_id"]:
            raise PublicationManualAuditError(f"auditor workbook audit ID changed at row {index + 1}")
        if row["claim_text"] != sample_item["claim_text"]:
            raise PublicationManualAuditError(f"auditor workbook claim text changed at row {index + 1}")
        code = row["code"].upper()
        if code not in mapping:
            raise PublicationManualAuditError(f"auditor workbook row {index + 1} has invalid code")
        rationale = row["rationale"]
        if not rationale:
            rationale = (
                "Auditor-confirmed shared rationale for an originally blank workbook cell: "
                + unified_rationale
            )
            blank_rationales += 1
        packet_item["response"] = {"verdict": mapping[code], "rationale": rationale}
    return packet, blank_rationales


def _completed_adjudication(
    template: dict[str, Any], cells: dict[str, str], mapping: dict[str, str]
) -> dict[str, Any]:
    adjudicator_id = _text(cells.get("B3"))
    completed_at = _text(cells.get("D3"))
    if not adjudicator_id:
        raise PublicationManualAuditError("adjudicator ID is required")
    if not completed_at:
        raise PublicationManualAuditError("adjudicator completion time is required")
    _require_true(cells.get("F3"), "adjudicator protected blinding")
    completed = copy.deepcopy(template)
    completed["adjudicator_attestation"] = {
        "adjudicator_id": adjudicator_id,
        "completed_at": completed_at,
        "did_not_view_unblinding_or_evaluator_output": True,
    }
    rows_by_id = {
        _text(cells.get(f"A{row}")): {
            "verdict": _text(cells.get(f"H{row}")),
            "rationale": _text(cells.get(f"I{row}")),
        }
        for row in range(6, 200)
        if _text(cells.get(f"A{row}"))
    }
    if set(rows_by_id) != {str(item["audit_id"]) for item in completed["items"]}:
        raise PublicationManualAuditError(
            "supplement adjudication rows do not exactly match the frozen disagreements"
        )
    for item in completed["items"]:
        audit_id = str(item["audit_id"])
        row = rows_by_id[audit_id]
        label = row["verdict"].casefold()
        verdict = _VERDICT_LABELS.get(label)
        if verdict is None and row["verdict"].upper() in mapping:
            verdict = mapping[row["verdict"].upper()]
        if verdict is None or not row["rationale"]:
            raise PublicationManualAuditError(f"adjudication for {audit_id} is incomplete")
        item["response"] = {"verdict": verdict, "rationale": row["rationale"]}
    return completed


def import_publication_manual_audit_workbooks(
    project: Path,
    auditor_1_xlsx: Path,
    auditor_2_xlsx: Path,
    supplement_xlsx: Path,
) -> dict[str, Any]:
    """Validate, freeze, adjudicate, and finalize the reviewed workbooks."""
    project = project.resolve()
    auditor_1_xlsx = auditor_1_xlsx.resolve()
    auditor_2_xlsx = auditor_2_xlsx.resolve()
    supplement_xlsx = supplement_xlsx.resolve()
    prepare_publication_manual_audit_packets(project)
    paths = _audit_paths(project)
    sample, _, _ = _load_frozen_sample(project)
    source_hash = sha256_file(paths["sample"])

    rows_1 = _audit_rows(_xlsx_cells(auditor_1_xlsx))
    rows_2 = _audit_rows(_xlsx_cells(auditor_2_xlsx))
    confirmation, adjudication_cells, verification = _supplement_sheets(
        _xlsx_cells(supplement_xlsx)
    )
    source_xlsx_hashes = {
        "auditor_1": _sha256(auditor_1_xlsx),
        "auditor_2": _sha256(auditor_2_xlsx),
        "supplement": _sha256(supplement_xlsx),
    }
    recorded_1 = _text(verification.get("B4")).casefold()
    recorded_2 = _text(verification.get("C4")).casefold()
    if recorded_1 != source_xlsx_hashes["auditor_1"]:
        raise PublicationManualAuditError("supplement is not bound to auditor 1 source workbook")
    if recorded_2 != source_xlsx_hashes["auditor_2"]:
        raise PublicationManualAuditError("supplement is not bound to auditor 2 source workbook")

    mapping = _mapping(confirmation)
    attestation_1, unified_1 = _attestation(
        confirmation, value_column="B", role="auditor_1"
    )
    attestation_2, unified_2 = _attestation(
        confirmation, value_column="E", role="auditor_2"
    )
    if attestation_1["auditor_id"] == attestation_2["auditor_id"]:
        raise PublicationManualAuditError("the two auditor IDs must be different")

    template_1 = read_json(paths["packet_root"] / "auditor_1.json")
    template_2 = read_json(paths["packet_root"] / "auditor_2.json")
    packet_1, substituted_1 = _completed_packet(
        template_1, rows_1, sample, mapping, attestation_1, unified_1
    )
    packet_2, substituted_2 = _completed_packet(
        template_2, rows_2, sample, mapping, attestation_2, unified_2
    )
    try:
        validate_auditor_packet_data(sample, packet_1, "auditor_1", source_hash)
        validate_auditor_packet_data(sample, packet_2, "auditor_2", source_hash)
    except ValueError as exc:
        raise PublicationManualAuditError(str(exc)) from exc

    import_root = paths["audit_root"] / "workbook-import"
    import_root.mkdir(parents=True, exist_ok=True)
    packet_1_path = import_root / "auditor_1_completed.json"
    packet_2_path = import_root / "auditor_2_completed.json"
    write_json_atomic(packet_1_path, packet_1)
    write_json_atomic(packet_2_path, packet_2)
    submission = submit_publication_manual_audits(project, packet_1_path, packet_2_path)

    adjudication_template = read_json(paths["adjudication"])
    completed_adjudication = _completed_adjudication(
        adjudication_template, adjudication_cells, mapping
    )
    _validate_publication_adjudication(adjudication_template, completed_adjudication)
    adjudication_path = import_root / "adjudication_completed.json"
    write_json_atomic(adjudication_path, completed_adjudication)
    result = finalize_publication_manual_audit(project, adjudication_path)

    import_manifest = {
        "schema_version": 1,
        "protocol_id": sample["protocol_id"],
        "source_sample_sha256": source_hash,
        "source_workbook_sha256": source_xlsx_hashes,
        "code_mapping": mapping,
        "unified_rationale_substitutions": {
            "auditor_1": substituted_1,
            "auditor_2": substituted_2,
        },
        "derived_packet_sha256": {
            "auditor_1": sha256_file(packet_1_path),
            "auditor_2": sha256_file(packet_2_path),
            "adjudication": sha256_file(adjudication_path),
        },
        "submission_manifest_sha256": sha256_file(paths["submission_manifest"]),
        "manual_audit_result_sha256": sha256_file(paths["result"]),
        "analysis_status": result["analysis_status"],
        "status": "complete",
    }
    manifest_path = import_root / "manifest.json"
    write_json_atomic(manifest_path, import_manifest)
    return {
        **import_manifest,
        "submission": submission,
        "manual_gate": result["manual_gate"],
        "import_manifest": str(manifest_path),
    }
