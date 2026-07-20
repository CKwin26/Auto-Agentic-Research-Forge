from __future__ import annotations

"""Review-package assembly after the automated publication gate has passed."""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .publication_supplement import audit_anonymous_supplement, prepare_anonymous_supplement
from .publication_synthesis import publication_layout_gate
from .storage import read_json, safe_relative, sha256_file, write_json_atomic


PACKAGE_SCHEMA_VERSION = 1
INDEX_FILENAME = "review_submission_package.json"


def _files(project: Path) -> dict[str, Path]:
    return {
        "manuscript/publication_manuscript.pdf": project / "synthesis" / "final_pdf" / "publication_manuscript.pdf",
        "manuscript/publication_manuscript.tex": project / "synthesis" / "publication_manuscript.tex",
        "manuscript/publication_manuscript.md": project / "synthesis" / "publication_manuscript.md",
        "manuscript/finalization.json": project / "synthesis" / "final_pdf" / "publication_manuscript.finalization.json",
        "evidence/publication_claims.json": project / "synthesis" / "publication_claims.json",
        "evidence/publication_analysis.json": project / "synthesis" / "publication_analysis.json",
        "evidence/publication_readiness.json": project / "synthesis" / "publication_readiness.json",
        "evidence/publication_synthesis_audit.json": project / "synthesis" / "publication_synthesis_audit.json",
        "evidence/manuscript_depth.json": project / "synthesis" / "publication_manuscript_depth.json",
    }


def _required(files: dict[str, Path]) -> None:
    missing = [name for name, path in files.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError("review package requires: " + ", ".join(sorted(missing)))


def _manifest(project: Path, package_id: str, supplement: dict[str, Any]) -> dict[str, Any]:
    files = _files(project)
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": package_id,
        "classification": "local_review_submission_package_not_externally_submitted",
        "publication_readiness": read_json(project / "synthesis" / "publication_readiness.json")["readiness_score"],
        "human_validation": "HUMAN_GATE_PENDING",
        "external_submission": {"authorized": False, "performed": False},
        "files": [
            {"path": name, "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for name, path in sorted(files.items())
        ],
        "supplement": {
            "package_id": supplement["package_id"],
            "relative_path": supplement["package_relative_path"],
            "audit_passed": supplement["audit"]["passed"],
        },
    }


def prepare_review_submission_package(project: str | Path, readiness_path: str | Path) -> dict[str, Any]:
    """Assemble an auditable local review package; never upload or submit it."""
    project_path = Path(project).resolve()
    readiness = Path(readiness_path).resolve()
    gate = publication_layout_gate(readiness, project=project_path)
    supplement = prepare_anonymous_supplement(project_path)
    if not supplement["audit"]["passed"]:
        raise ValueError("anonymous supplement must pass before package assembly")
    files = _files(project_path)
    _required(files)
    pdf_manifest = read_json(files["manuscript/finalization.json"])
    if pdf_manifest.get("finalization_authorized") is not True:
        raise ValueError("review package requires a finalized PDF")
    package_id = "review-package-" + hashlib.sha256(
        (sha256_file(files["manuscript/publication_manuscript.pdf"]) + sha256_file(readiness) + supplement["package_id"]).encode("utf-8")
    ).hexdigest()[:16]
    parent = project_path / "review_submission_packages"
    destination = parent / package_id
    manifest = _manifest(project_path, package_id, supplement)
    if not destination.exists():
        parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{package_id}.", dir=parent))
        try:
            for relative, source in files.items():
                target = safe_relative(temporary, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            supplement_source = project_path / supplement["package_relative_path"]
            shutil.copytree(supplement_source, temporary / "supplement")
            (temporary / "README.md").write_text(
                "# Local review submission package\n\n"
                "This package was assembled only after the automated publication gate passed. "
                "It is a local review artifact: no external submission, repository upload, or human-validation claim has occurred.\n",
                encoding="utf-8",
                newline="\n",
            )
            (temporary / "MANIFEST.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    audit = audit_review_submission_package(project_path, package_id=package_id)
    if not audit["passed"]:
        raise ValueError("new review submission package failed its audit")
    index = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": package_id,
        "package_relative_path": (Path("review_submission_packages") / package_id).as_posix(),
        "status": "prepared_locally_not_externally_submitted",
        "release_gate": gate,
        "audit": audit,
    }
    write_json_atomic(project_path / "synthesis" / INDEX_FILENAME, index)
    return index


def audit_review_submission_package(
    project: str | Path, *, package_id: str | None = None
) -> dict[str, Any]:
    project_path = Path(project).resolve()
    if package_id is None:
        package_id = str(read_json(project_path / "synthesis" / INDEX_FILENAME).get("package_id", ""))
    package = project_path / "review_submission_packages" / package_id
    manifest_path = package / "MANIFEST.json"
    checks: dict[str, bool] = {"package_present": package.is_dir(), "manifest_present": manifest_path.is_file()}
    violations: list[str] = []
    if not all(checks.values()):
        violations.append("review package or manifest is missing")
        return {"schema_version": PACKAGE_SCHEMA_VERSION, "package_id": package_id, "passed": False, "checks": checks, "violations": violations}
    manifest = read_json(manifest_path)
    expected_files = _files(project_path)
    checks["classification_local_only"] = (
        manifest.get("classification") == "local_review_submission_package_not_externally_submitted"
        and manifest.get("external_submission") == {"authorized": False, "performed": False}
        and manifest.get("human_validation") == "HUMAN_GATE_PENDING"
    )
    records = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    expected_records = [
        {"path": name, "sha256": sha256_file(path), "bytes": path.stat().st_size}
        for name, path in sorted(expected_files.items())
    ]
    checks["current_source_binding"] = records == expected_records
    package_hashes = True
    for record in records:
        try:
            path = safe_relative(package, str(record["path"]))
            package_hashes &= path.is_file() and sha256_file(path) == record["sha256"]
        except (KeyError, OSError, ValueError):
            package_hashes = False
    checks["package_hashes_valid"] = package_hashes
    supplement_audit = audit_anonymous_supplement(project_path)
    checks["supplement_current"] = bool(supplement_audit.get("passed")) and (package / "supplement" / "MANIFEST.json").is_file()
    if not all(checks.values()):
        violations.append("review package is stale, incomplete, or missing its audited supplement")
    return {"schema_version": PACKAGE_SCHEMA_VERSION, "package_id": package_id, "passed": not violations, "checks": checks, "violations": violations}
