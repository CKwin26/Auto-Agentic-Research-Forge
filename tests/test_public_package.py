from __future__ import annotations

import csv
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from research_forge.public_package import (
    verify_public_blocked_contract_package,
    build_public_hidden_prediction_package,
    verify_public_hidden_prediction_package,
)


def _csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _acceptance(root: Path) -> Path:
    for relative in ("candidate.py",):
        path = root / "candidate-input" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("print('candidate')", encoding="utf-8")
    _csv(root / "candidate-input/task/train.csv", ["sample_id", "x", "target"], [{"sample_id": "a", "x": 0, "target": "Mine"}])
    _csv(root / "candidate-input/task/test.csv", ["sample_id", "x"], [{"sample_id": "b", "x": 1}])
    (root / "evaluator-source").mkdir(parents=True)
    (root / "evaluator-source/evaluate.py").write_text("print('evaluate')", encoding="utf-8")
    _csv(root / "evaluator-source/targets.csv", ["sample_id", "target"], [{"sample_id": "b", "target": "Rock"}])
    for arm, prediction, value in (("baseline", "Mine", 0.0), ("treatment", "Rock", 1.0)):
        _csv(root / f"candidate-output/{arm}/submission.csv", ["sample_id", "prediction", "score"], [{"sample_id": "b", "prediction": prediction, "score": value}])
        result = root / f"evaluator-output/{arm}/result.json"
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(json.dumps({"primary_metric_value": value}), encoding="utf-8")
    report = {
        "profile_id": "benchmark_prediction_v1",
        "arms": {
            "baseline": {"primary_metric_value": 0.0},
            "treatment": {"primary_metric_value": 1.0},
        },
        "scientific_decision": {"verdict": "supported"},
        "independent_validation": {"candidate_never_received_hidden_target": True},
    }
    (root / "acceptance-report.json").write_text(json.dumps(report), encoding="utf-8")
    return root


def test_public_package_is_self_contained_and_tamper_evident(tmp_path: Path) -> None:
    package = tmp_path / "public.zip"
    result = build_public_hidden_prediction_package(
        _acceptance(tmp_path / "acceptance"), output_path=package
    )
    assert result["verification"]["passed"]
    with zipfile.ZipFile(package) as archive:
        assert "verify.py" in archive.namelist()
        assert "data/targets.csv" in archive.namelist()

    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(changed, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "submissions/treatment.csv":
                content += b"changed"
            target.writestr(name, content)
    verification = verify_public_hidden_prediction_package(changed)
    assert not verification["passed"]
    assert any("changed file" in item for item in verification["violations"])


def test_public_blocked_case_is_self_contained_and_has_no_run_or_verdict(
    tmp_path: Path,
) -> None:
    package = tmp_path / "blocked-contract-case.zip"
    completed = subprocess.run(
        [
            sys.executable,
            "examples/public-blocked-contract-case/verify.py",
            "--build-package",
            str(package),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    verification = verify_public_blocked_contract_package(package)
    assert verification["passed"]
    with zipfile.ZipFile(package) as archive:
        report = json.loads(archive.read("compile-report.json"))
        assert report["protocol_status"] == "blocked"
        assert report["run_specifications"] == []
