"""Build and verify public, generated-run-free v1 acceptance packages."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .contract_compiler import compile_research_contract
from .storage import sha256_file, write_json_atomic, write_text_atomic
from .workflow_domain import ProtocolStatus, ResearchContractVersion


_VERIFY_PROGRAM = r'''from __future__ import annotations
import csv, hashlib, json
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'package-manifest.json').read_text('utf-8'))
subject = dict(manifest)
expected_subject = subject.pop('package_subject_sha256')
actual_subject = hashlib.sha256(json.dumps(subject, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
if actual_subject != expected_subject:
    raise SystemExit('package manifest subject hash mismatch')
for relative, expected in manifest['files'].items():
    path = root / relative
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit(f'file missing or changed: {relative}')
report = json.loads((root / 'reports/acceptance-report.json').read_text('utf-8'))
with (root / 'data/targets.csv').open(encoding='utf-8', newline='') as handle:
    targets = {row['sample_id']: row['target'] for row in csv.DictReader(handle)}
for arm in ('baseline', 'treatment'):
    with (root / f'submissions/{arm}.csv').open(encoding='utf-8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    ids = [row['sample_id'] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(targets):
        raise SystemExit(f'{arm} submission does not exactly match target IDs')
    accuracy = sum(row['prediction'] == targets[row['sample_id']] for row in rows) / len(rows)
    registered = float(report['arms'][arm]['primary_metric_value'])
    if abs(accuracy - registered) > 1e-12:
        raise SystemExit(f'{arm} accuracy does not reproduce')
print(json.dumps({'passed': True, 'profile_id': report['profile_id'], 'verdict': report['scientific_decision']['verdict']}, sort_keys=True))
'''


_README = """# Research Forge minimal public research package

This package is a post-evaluation replay artifact for OpenML Task 39.  The
formal targets are included now so that anybody can recompute the registered
metrics; during the original candidate runs they were mounted only in a
separate evaluator container.

Run `python verify.py` from the extracted directory.  The verifier uses only
the Python standard library, checks every frozen file hash, and recomputes the
primary metric from per-sample submissions.  No original Agent conversation,
network access, Research Forge installation, or hidden local file is needed.

Scope: this proves the frozen majority-class and nearest-centroid comparison
on the official OpenML Task 39 split.  It is not an external C5 replication.
"""


_BLOCKED_VERIFY_PROGRAM = r'''from __future__ import annotations
import hashlib, json
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'package-manifest.json').read_text('utf-8'))
subject = dict(manifest)
expected_subject = subject.pop('package_subject_sha256')
actual_subject = hashlib.sha256(json.dumps(subject, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
if actual_subject != expected_subject:
    raise SystemExit('package manifest subject hash mismatch')
for relative, expected in manifest['files'].items():
    path = root / relative
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit(f'file missing or changed: {relative}')
report = json.loads((root / 'compile-report.json').read_text('utf-8'))
required = set(manifest['required_issue_codes'])
observed = {item.split(':', 1)[0] for item in report['blocking_issues']}
passed = (
    report['protocol_status'] == 'blocked'
    and report['compile_passed'] is False
    and report['run_specifications'] == []
    and required.issubset(observed)
    and manifest['scientific_verdict_created'] is False
)
print(json.dumps({'passed': passed, 'protocol_status': report['protocol_status'], 'run_specification_count': len(report['run_specifications']), 'scientific_verdict_created': False}, sort_keys=True))
raise SystemExit(0 if passed else 1)
'''


_BLOCKED_README = """# Research Forge correctly blocked contract case

This package freezes an intentionally incomplete Research Contract and the
actual deterministic Contract Compiler report produced from it.  The contract
omits an executable metric formula, authorized data resources, a target rule,
a sampling frame, and an operational difference between baseline and
treatment.

Run `python verify.py` from the extracted directory.  The verifier uses only
the Python standard library, checks every frozen file hash, confirms the
required blocker codes, confirms that no Run Specification was emitted, and
confirms that no scientific Verdict exists.  A passing verification means the
platform correctly refused to turn incomplete scientific prose into a formal
experiment; it does not mean the hypothesis was refuted.
"""


_PUBLIC_FILES = {
    "candidate-input/candidate.py": "code/candidate.py",
    "candidate-input/task/train.csv": "data/train.csv",
    "candidate-input/task/test.csv": "data/test.csv",
    "evaluator-source/targets.csv": "data/targets.csv",
    "evaluator-source/evaluate.py": "code/evaluate.py",
    "candidate-output/baseline/submission.csv": "submissions/baseline.csv",
    "candidate-output/treatment/submission.csv": "submissions/treatment.csv",
    "evaluator-output/baseline/result.json": "reports/baseline-evaluator.json",
    "evaluator-output/treatment/result.json": "reports/treatment-evaluator.json",
    "acceptance-report.json": "reports/acceptance-report.json",
}


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _write_deterministic_zip(root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.relative_to(root).as_posix())
            info.date_time = (2026, 8, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    temporary.replace(output)


def build_public_hidden_prediction_package(
    acceptance_root: str | Path,
    *,
    output_path: str | Path,
) -> dict[str, Any]:
    source = Path(acceptance_root).resolve()
    output = Path(output_path).resolve()
    if output.suffix.lower() != ".zip":
        raise ValueError("public research package must be a .zip")
    report_path = source / "acceptance-report.json"
    if not report_path.is_file():
        raise FileNotFoundError("acceptance-report.json is missing")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("profile_id") != "benchmark_prediction_v1":
        raise ValueError("public package requires benchmark_prediction_v1 evidence")
    if not report.get("independent_validation", {}).get(
        "candidate_never_received_hidden_target"
    ):
        raise ValueError("acceptance report does not prove the hidden boundary")
    with tempfile.TemporaryDirectory(prefix="rf-public-package-") as temporary:
        root = Path(temporary) / "research-package"
        root.mkdir()
        for source_relative, destination_relative in _PUBLIC_FILES.items():
            item = source / source_relative
            if not item.is_file():
                raise FileNotFoundError(f"acceptance artifact is missing: {source_relative}")
            destination = root / destination_relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(item.read_bytes())
        write_text_atomic(root / "README.md", _README)
        write_text_atomic(root / "verify.py", _VERIFY_PROGRAM)
        files = {
            path.relative_to(root).as_posix(): sha256_file(path)
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }
        manifest_without_subject = {
            "schema_version": 1,
            "package_id": "rf-public-openml-39-hidden-target-v1",
            "profile_id": "benchmark_prediction_v1",
            "source_task": "OpenML Task 39",
            "generated_run_committed_to_product_repository": False,
            "external_independent_operator": False,
            "files": files,
        }
        manifest = {
            **manifest_without_subject,
            "package_subject_sha256": _canonical_sha256(manifest_without_subject),
        }
        write_json_atomic(root / "package-manifest.json", manifest)
        _write_deterministic_zip(root, output)
    verification = verify_public_hidden_prediction_package(output)
    if not verification["passed"]:
        raise RuntimeError("new public package failed independent verification")
    return {
        "package_id": "rf-public-openml-39-hidden-target-v1",
        "output": str(output),
        "archive_sha256": sha256_file(output),
        "verification": verification,
    }


def verify_public_hidden_prediction_package(package_path: str | Path) -> dict[str, Any]:
    package = Path(package_path).resolve()
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        if any(name.startswith("/") or ".." in Path(name).parts for name in names):
            return {"passed": False, "violations": ["unsafe archive path"]}
        try:
            manifest = json.loads(archive.read("package-manifest.json"))
        except (KeyError, json.JSONDecodeError):
            return {"passed": False, "violations": ["manifest missing or invalid"]}
        subject = dict(manifest)
        expected_subject = str(subject.pop("package_subject_sha256", ""))
        violations: list[str] = []
        if _canonical_sha256(subject) != expected_subject:
            violations.append("manifest subject hash mismatch")
        for relative, expected in dict(manifest.get("files") or {}).items():
            if relative not in names:
                violations.append(f"missing file: {relative}")
                continue
            actual = hashlib.sha256(archive.read(relative)).hexdigest()
            if actual != expected:
                violations.append(f"changed file: {relative}")
        required = {
            "README.md",
            "verify.py",
            "data/targets.csv",
            "submissions/baseline.csv",
            "submissions/treatment.csv",
            "reports/acceptance-report.json",
        }
        missing = sorted(required.difference(names))
        violations.extend(f"missing required public artifact: {item}" for item in missing)
    return {
        "passed": not violations,
        "violations": violations,
        "package_id": manifest.get("package_id"),
        "archive_sha256": sha256_file(package),
    }


def build_public_blocked_contract_package(
    contract: ResearchContractVersion,
    *,
    output_path: str | Path,
    required_issue_codes: set[str],
) -> dict[str, Any]:
    """Freeze a self-contained public example of a scientifically safe block."""

    output = Path(output_path).resolve()
    if output.suffix.lower() != ".zip":
        raise ValueError("public blocked-contract package must be a .zip")
    report = compile_research_contract(contract)
    observed = {item.split(":", 1)[0] for item in report.blocking_issues}
    if (
        report.protocol_status is not ProtocolStatus.BLOCKED
        or report.compile_passed
        or report.run_specifications
        or not required_issue_codes.issubset(observed)
    ):
        raise ValueError("contract is not the expected correctly blocked case")
    with tempfile.TemporaryDirectory(prefix="rf-public-blocked-") as temporary:
        root = Path(temporary) / "blocked-contract-case"
        root.mkdir()
        write_json_atomic(
            root / "contract-input.json", contract.model_dump(mode="json")
        )
        write_json_atomic(
            root / "compile-report.json", report.model_dump(mode="json")
        )
        write_text_atomic(root / "README.md", _BLOCKED_README)
        write_text_atomic(root / "verify.py", _BLOCKED_VERIFY_PROGRAM)
        files = {
            path.relative_to(root).as_posix(): sha256_file(path)
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }
        manifest_without_subject = {
            "schema_version": 1,
            "package_id": "rf-public-correctly-blocked-contract-v1",
            "case_kind": "negative_contract_compilation",
            "required_issue_codes": sorted(required_issue_codes),
            "scientific_verdict_created": False,
            "generated_run_committed_to_product_repository": False,
            "files": files,
        }
        manifest = {
            **manifest_without_subject,
            "package_subject_sha256": _canonical_sha256(manifest_without_subject),
        }
        write_json_atomic(root / "package-manifest.json", manifest)
        _write_deterministic_zip(root, output)
    verification = verify_public_blocked_contract_package(output)
    if not verification["passed"]:
        raise RuntimeError("new blocked-contract package failed verification")
    return {
        "package_id": "rf-public-correctly-blocked-contract-v1",
        "output": str(output),
        "archive_sha256": sha256_file(output),
        "verification": verification,
    }


def verify_public_blocked_contract_package(
    package_path: str | Path,
) -> dict[str, Any]:
    package = Path(package_path).resolve()
    violations: list[str] = []
    manifest: dict[str, Any] = {}
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        if any(name.startswith("/") or ".." in Path(name).parts for name in names):
            return {"passed": False, "violations": ["unsafe archive path"]}
        try:
            manifest = json.loads(archive.read("package-manifest.json"))
            report = json.loads(archive.read("compile-report.json"))
        except (KeyError, json.JSONDecodeError):
            return {"passed": False, "violations": ["manifest or report missing"]}
        subject = dict(manifest)
        expected_subject = str(subject.pop("package_subject_sha256", ""))
        if _canonical_sha256(subject) != expected_subject:
            violations.append("manifest subject hash mismatch")
        for relative, expected in dict(manifest.get("files") or {}).items():
            if relative not in names:
                violations.append(f"missing file: {relative}")
            elif hashlib.sha256(archive.read(relative)).hexdigest() != expected:
                violations.append(f"changed file: {relative}")
        observed = {
            str(item).split(":", 1)[0]
            for item in report.get("blocking_issues", [])
        }
        required = set(manifest.get("required_issue_codes") or [])
        if report.get("protocol_status") != ProtocolStatus.BLOCKED.value:
            violations.append("protocol was not blocked")
        if report.get("compile_passed") is not False:
            violations.append("compile was not rejected")
        if report.get("run_specifications") != []:
            violations.append("blocked case emitted run specifications")
        if not required.issubset(observed):
            violations.append("required blocker codes are missing")
        if manifest.get("scientific_verdict_created") is not False:
            violations.append("blocked case claims a scientific verdict")
    return {
        "passed": not violations,
        "violations": violations,
        "package_id": manifest.get("package_id"),
        "archive_sha256": sha256_file(package),
    }


__all__ = [
    "build_public_blocked_contract_package",
    "build_public_hidden_prediction_package",
    "verify_public_blocked_contract_package",
    "verify_public_hidden_prediction_package",
]
