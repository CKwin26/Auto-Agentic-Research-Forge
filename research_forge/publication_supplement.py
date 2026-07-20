from __future__ import annotations

"""Local, anonymous pre-publication supplement preparation.

This module intentionally prepares a hash-bound package but never calls a
hosting service or represents the package as public.  External release remains
an explicit authorization boundary.
"""

import json
import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .storage import read_json, safe_relative, sha256_file, write_json_atomic


PACKAGE_SCHEMA_VERSION = 1
INDEX_FILENAME = "anonymous_supplement_package.json"
_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN_CONTENT = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"C:\\\\Users\\", re.IGNORECASE),
    re.compile(r"austaining@gmail\.com", re.IGNORECASE),
)
_WINDOWS_USER_PATH = re.compile(r"[A-Za-z]:\\Users\\[^\\/\s\"']+(?:\\[^\s\"']+)*", re.IGNORECASE)


def _artifact_map(project: Path) -> dict[str, Path]:
    """Return the minimal evidence and implementation surface for reproduction."""
    return {
        "evidence/protocol.json": project / "stage2" / "protocol.json",
        "evidence/frozen_manifest.json": project / "stage2" / "frozen_manifest.json",
        "evidence/publication_pair_audit.json": project / "stage2" / "publication_pair_audit.json",
        "evidence/protected_evaluation_manifest.json": project / "stage2" / "protected_nli_evaluation" / "manifest.json",
        "evidence/protected_evaluation_summary.json": project / "stage2" / "protected_nli_evaluation" / "summary.json",
        "evidence/protected_evaluation_unblinding.json": project / "stage2" / "protected_nli_evaluation" / "unblinding.json",
        "evidence/independent_calibration_contract.json": project / "design_revisions" / "independent_calibration_contract.json",
        "analysis/publication_analysis.json": project / "synthesis" / "publication_analysis.json",
        "analysis/publication_claims.json": project / "synthesis" / "publication_claims.json",
        "analysis/publication_synthesis_audit.json": project / "synthesis" / "publication_synthesis_audit.json",
        "analysis/manuscript_depth.json": project / "synthesis" / "publication_manuscript_depth.json",
        "manuscript/publication_manuscript.md": project / "synthesis" / "publication_manuscript.md",
        "environment/Dockerfile": _SOURCE_ROOT / "docker" / "airs-cpu" / "Dockerfile",
        "environment/requirements.lock": _SOURCE_ROOT / "docker" / "airs-cpu" / "requirements.lock",
        "source/research_forge/publication_nli_evaluation.py": _SOURCE_ROOT / "research_forge" / "publication_nli_evaluation.py",
        "source/research_forge/publication_pair_audit.py": _SOURCE_ROOT / "research_forge" / "publication_pair_audit.py",
        "source/research_forge/publication_synthesis.py": _SOURCE_ROOT / "research_forge" / "publication_synthesis.py",
        "source/research_forge/publication_readiness.py": _SOURCE_ROOT / "research_forge" / "publication_readiness.py",
        "source/research_forge/journal_recommendation.py": _SOURCE_ROOT / "research_forge" / "journal_recommendation.py",
        "source/research_forge/study.py": _SOURCE_ROOT / "research_forge" / "study.py",
        "source/research_forge/study_runner.py": _SOURCE_ROOT / "research_forge" / "study_runner.py",
        "source/research_forge/study_models.py": _SOURCE_ROOT / "research_forge" / "study_models.py",
        "source/research_forge/runtime.py": _SOURCE_ROOT / "research_forge" / "runtime.py",
        "source/research_forge/storage.py": _SOURCE_ROOT / "research_forge" / "storage.py",
        "prompts/study_finalizer.md": _SOURCE_ROOT / "research_forge" / "prompts" / "study_finalizer.md",
        "prompts/experimenter.md": _SOURCE_ROOT / "research_forge" / "prompts" / "experimenter.md",
    }


def _require_file_map(files: dict[str, Path]) -> None:
    missing = [destination for destination, source in files.items() if not source.is_file()]
    if missing:
        raise FileNotFoundError(
            "anonymous supplement cannot be prepared; required artifacts are missing: "
            + ", ".join(sorted(missing))
        )


def _assert_anonymous_bytes(data: bytes, *, label: str) -> None:
    text = data.decode("utf-8", errors="replace")
    matches = [pattern.pattern for pattern in _FORBIDDEN_CONTENT if pattern.search(text)]
    if matches:
        raise ValueError(
            f"anonymous supplement refused {label}: prohibited local identifier or secret-like token"
        )


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _WINDOWS_USER_PATH.sub("<LOCAL_PATH>", value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in value.items()}
    return value


def _package_bytes(source: Path) -> bytes:
    """Produce an anonymous package representation while retaining source hashes."""
    raw = source.read_bytes()
    if source.suffix.lower() == ".json":
        value = json.loads(raw.decode("utf-8"))
        # This report is repeatedly refreshed by the audit command. Its wall
        # clock is not evidence and must not invalidate an otherwise identical
        # publication package.
        if source.name == "publication_pair_audit.json" and isinstance(value, dict):
            value.pop("audited_at", None)
        data = (json.dumps(_sanitize_value(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    else:
        data = _WINDOWS_USER_PATH.sub("<LOCAL_PATH>", raw.decode("utf-8", errors="replace")).encode("utf-8")
    _assert_anonymous_bytes(data, label=source.name)
    return data


def _manifest_payload(
    project: Path,
    files: dict[str, Path],
    package_id: str,
) -> dict[str, Any]:
    protocol = read_json(project / "stage2" / "protocol.json")
    manuscript = project / "synthesis" / "publication_manuscript.md"
    records = [
        {
            "path": destination,
            "source_content_sha256": hashlib.sha256(_package_bytes(source)).hexdigest(),
            "package_sha256": hashlib.sha256(_package_bytes(source)).hexdigest(),
            "bytes": len(_package_bytes(source)),
        }
        for destination, source in sorted(files.items())
    ]
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": package_id,
        "classification": "local_anonymous_prepublication_package_not_public",
        "protocol_id": str(protocol.get("protocol_id", "")),
        "study_intent": str(protocol.get("study_intent", "")),
        "manuscript_sha256": sha256_file(manuscript),
        "human_validation": "HUMAN_GATE_PENDING",
        "external_release": {
            "publicly_accessible": False,
            "verified": False,
            "reason": "No external upload or public-repository authorization was granted.",
        },
        "files": records,
    }


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def prepare_anonymous_supplement(project: str | Path) -> dict[str, Any]:
    """Build a versioned local package without publishing or overwriting one."""
    project_path = Path(project).resolve()
    files = _artifact_map(project_path)
    _require_file_map(files)
    protocol = read_json(project_path / "stage2" / "protocol.json")
    manuscript_hash = sha256_file(project_path / "synthesis" / "publication_manuscript.md")
    source_fingerprint = hashlib.sha256(
        "\n".join(
            f"{key}:{hashlib.sha256(_package_bytes(source)).hexdigest()}"
            for key, source in sorted(files.items())
        ).encode("utf-8")
    ).hexdigest()[:12]
    package_id = f"anon-supplement-{protocol['protocol_id']}-{manuscript_hash[:12]}-{source_fingerprint}"
    parent = project_path / "supplement_packages"
    destination = parent / package_id
    manifest = _manifest_payload(project_path, files, package_id)
    if destination.exists():
        audit = audit_anonymous_supplement(project_path, package_id=package_id)
        if not audit["passed"]:
            raise ValueError("existing anonymous supplement package failed its audit")
    else:
        parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{package_id}.", dir=parent))
        try:
            for relative, source in files.items():
                target = safe_relative(temporary, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(_package_bytes(source))
            _write_text(
                temporary / "README.md",
                "# Anonymous supplement preparation package\n\n"
                "This local package is hash-bound to a frozen publication protocol and its evidence artifacts. "
                "It is prepared for a future authorized anonymous release, but is not publicly accessible. "
                "It does not establish external replication, completed human validation, or submission readiness.\n",
            )
            _write_text(
                temporary / "PUBLICATION_STATUS.md",
                "AUTOMATED_EVIDENCE_COMPLETE + HUMAN_GATE_PENDING\n\n"
                "External release is pending explicit authorization. No hosting, upload, repository creation, "
                "or external submission was performed while preparing this package.\n",
            )
            _write_text(
                temporary / "MANIFEST.json",
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        audit = audit_anonymous_supplement(project_path, package_id=package_id)
        if not audit["passed"]:
            raise ValueError("new anonymous supplement package failed its audit")

    index = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": package_id,
        "package_relative_path": (Path("supplement_packages") / package_id).as_posix(),
        "status": "prepared_locally_not_public",
        "audit": audit,
    }
    write_json_atomic(project_path / "synthesis" / INDEX_FILENAME, index)
    return index


def audit_anonymous_supplement(
    project: str | Path,
    *,
    package_id: str | None = None,
) -> dict[str, Any]:
    """Verify package hash integrity, current-source binding, and non-public status."""
    project_path = Path(project).resolve()
    if package_id is None:
        index_path = project_path / "synthesis" / INDEX_FILENAME
        index = read_json(index_path)
        package_id = str(index.get("package_id", ""))
    package = project_path / "supplement_packages" / package_id
    manifest_path = package / "MANIFEST.json"
    checks: dict[str, bool] = {
        "package_id_valid": bool(package_id) and package.is_dir(),
        "manifest_present": manifest_path.is_file(),
    }
    violations: list[str] = []
    if not all(checks.values()):
        violations.append("anonymous supplement package or manifest is missing")
        return {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "package_id": package_id,
            "passed": False,
            "checks": checks,
            "violations": violations,
            "external_release": "not_public",
        }
    manifest = read_json(manifest_path)
    files = _artifact_map(project_path)
    expected = _manifest_payload(project_path, files, package_id)
    checks["schema_valid"] = manifest.get("schema_version") == PACKAGE_SCHEMA_VERSION
    checks["local_not_public"] = (
        manifest.get("classification") == "local_anonymous_prepublication_package_not_public"
        and manifest.get("external_release", {}).get("publicly_accessible") is False
        and manifest.get("external_release", {}).get("verified") is False
    )
    checks["protocol_and_manuscript_current"] = (
        manifest.get("protocol_id") == expected["protocol_id"]
        and manifest.get("study_intent") == "publication"
        and manifest.get("manuscript_sha256") == expected["manuscript_sha256"]
    )
    manifest_records = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    expected_records = expected["files"]
    checks["source_binding_current"] = manifest_records == expected_records
    packaged_hashes_ok = True
    anonymity_ok = True
    for record in manifest_records:
        relative = record.get("path") if isinstance(record, dict) else None
        if not isinstance(relative, str):
            packaged_hashes_ok = False
            continue
        try:
            path = safe_relative(package, relative)
            packaged_hashes_ok &= path.is_file() and sha256_file(path) == record.get("package_sha256")
            if path.is_file():
                _assert_anonymous_bytes(path.read_bytes(), label=path.name)
        except (OSError, ValueError):
            packaged_hashes_ok = False
            anonymity_ok = False
    checks["packaged_hashes_valid"] = packaged_hashes_ok
    checks["anonymity_scan_passed"] = anonymity_ok
    if not all(checks.values()):
        violations.append("anonymous supplement package is stale, incomplete, non-anonymous, or not hash-bound")
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": package_id,
        "passed": not violations,
        "checks": checks,
        "violations": violations,
        "external_release": "not_public",
    }
