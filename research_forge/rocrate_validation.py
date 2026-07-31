"""Offline invocation of the maintained CRS4 RO-Crate validator.

The validator runs as an external checker process.  Research Forge freezes its
machine-readable report and never upgrades an internal shape test into a claim
of standards conformance.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from .storage import sha256_file, write_json_atomic


VALIDATOR_PACKAGE = "roc-validator"
DEFAULT_PROFILE = "ro-crate-1.1"


def _default_validator_command() -> list[str]:
    configured = os.environ.get("RF_ROCRATE_VALIDATOR")
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError("RF_ROCRATE_VALIDATOR does not exist")
        return [str(path)]
    discovered = shutil.which("rocrate-validator")
    if discovered:
        return [discovered]
    scripts = Path(sys.executable).resolve().parent / "Scripts"
    for name in ("rocrate-validator.exe", "rocrate-validator"):
        candidate = scripts / name
        if candidate.is_file():
            return [str(candidate)]
    raise FileNotFoundError(
        "roc-validator is not installed; install the maintained CRS4 "
        "validator and set RF_ROCRATE_VALIDATOR when it is outside PATH"
    )


def _run(
    command: Sequence[str], *, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        shell=False,
        check=False,
    )


def validate_rocrate_external(
    crate_root: str | Path,
    *,
    evidence_output: str | Path,
    profile: str = DEFAULT_PROFILE,
    validator_command: Sequence[str] | None = None,
    offline: bool = True,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Run the external CLI and freeze its JSON report plus provenance.

    Production use defaults to offline mode so the validator cannot perform
    an ungoverned context retrieval.  An operator may warm the validator cache
    separately under an approved deployment/network procedure.
    """

    root = Path(crate_root).resolve()
    metadata = root / "ro-crate-metadata.json"
    if not root.is_dir() or not metadata.is_file():
        raise FileNotFoundError("RO-Crate root lacks ro-crate-metadata.json")
    output = Path(evidence_output).resolve()
    if root == output or root in output.parents:
        raise ValueError("validator evidence must be stored outside the crate")
    base = list(validator_command or _default_validator_command())
    if not base:
        raise ValueError("validator command cannot be empty")
    version_run = _run([*base, "--version"], timeout_seconds=timeout_seconds)
    version_text = (version_run.stdout or version_run.stderr).strip()
    with tempfile.TemporaryDirectory(prefix="rf-rocrate-validator-") as temp:
        raw_report = Path(temp) / "validator-report.json"
        command = [
            *base,
            "-y",
            "--disable-color",
            "validate",
            "-p",
            profile,
            "-l",
            "required",
            "-f",
            "json",
            "-o",
            str(raw_report),
        ]
        if offline:
            command.append("--offline")
        else:
            command.append("--no-cache")
        command.append(str(root))
        completed = _run(command, timeout_seconds=timeout_seconds)
        if raw_report.is_file():
            report = json.loads(raw_report.read_text(encoding="utf-8"))
            raw_report_sha256 = sha256_file(raw_report)
        else:
            report = {
                "passed": False,
                "issues": [
                    {
                        "severity": "REQUIRED",
                        "message": "external validator did not emit its JSON report",
                    }
                ],
            }
            raw_report_sha256 = None
    issues = list(report.get("issues") or [])
    required_issues = [
        item for item in issues
        if str(item.get("severity") or "").upper() == "REQUIRED"
    ]
    passed = bool(report.get("passed")) and completed.returncode == 0
    evidence = {
        "schema_version": 1,
        "checker": "crs4/rocrate-validator",
        "checker_distribution": VALIDATOR_PACKAGE,
        "checker_version_output": version_text,
        "profile": profile,
        "offline": offline,
        "crate_metadata_sha256": sha256_file(metadata),
        "exit_code": completed.returncode,
        "passed": passed,
        "required_issue_count": len(required_issues),
        "required_issues": required_issues,
        "validator_report_sha256": raw_report_sha256,
        "validator_report": report,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    write_json_atomic(output, evidence)
    return evidence


__all__ = ["validate_rocrate_external"]
