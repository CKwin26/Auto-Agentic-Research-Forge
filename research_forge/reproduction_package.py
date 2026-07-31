"""Build the immutable Stage 3 reproduction package."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization

from .reproduction_domain import (
    ReproductionAsset,
    ReproductionAssetMode,
    ReproductionPackageManifest,
    ReproductionPolicy,
)
from .reproduction_policy import canonical_sha256
from .stage_three_trust import (
    _safe_extract,
    sign_subject,
    verify_stage3_completion_package,
)
from .storage import read_json, sha256_file, write_json_atomic
from .workflow_domain import stable_id


def _manifest_subject(manifest: ReproductionPackageManifest) -> dict[str, Any]:
    return manifest.model_dump(mode="json", exclude={"package_sha256"})


def reproduction_manifest_digest(
    manifest: ReproductionPackageManifest,
) -> str:
    return canonical_sha256(_manifest_subject(manifest))


def _archive_directory(root: Path, output: Path) -> None:
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    if output.name.endswith(".tar.zst"):
        import zstandard

        with tempfile.TemporaryDirectory(prefix="rf-repro-tar-") as temporary:
            tar_path = Path(temporary) / "package.tar"
            with tarfile.open(tar_path, "w") as archive:
                for path in sorted(root.rglob("*")):
                    archive.add(
                        path,
                        arcname=path.relative_to(root).as_posix(),
                        recursive=False,
                    )
            compressor = zstandard.ZstdCompressor(level=10)
            with tar_path.open("rb") as source, temporary_output.open("wb") as sink:
                compressor.copy_stream(source, sink)
    elif output.suffix.lower() == ".zip":
        with zipfile.ZipFile(
            temporary_output, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(root).as_posix())
    else:
        raise ValueError("reproduction package must end in .zip or .tar.zst")
    os.replace(temporary_output, output)


def inspect_stage3_package(package_path: str | Path) -> dict[str, Any]:
    """Read the frozen identifiers needed by a reproduction package."""

    package = Path(package_path).resolve()
    verification = verify_stage3_completion_package(package)
    if not verification["passed"]:
        raise ValueError(
            "Stage 3 completion package is not valid: "
            + ", ".join(verification["violations"])
        )
    with tempfile.TemporaryDirectory(prefix="rf-repro-inspect-") as temporary:
        root = Path(temporary) / "package"
        root.mkdir()
        _safe_extract(package, root)
        stage3_manifest = read_json(root / "package-manifest.json")
        completion = read_json(root / stage3_manifest["completion_path"])
        plan = read_json(
            root
            / "stage3"
            / "run_plans"
            / f"{completion['plan_id']}.json"
        )
        evaluations = [
            read_json(path)
            for path in sorted(root.glob("stage3/evaluations/*.json"))
            if read_json(path).get("evaluation_id")
            in set(completion["evaluation_ids"])
        ]
        if not evaluations:
            raise ValueError("completion package has no frozen evaluation")
        evaluation = evaluations[-1]
        verdicts = [
            read_json(path) for path in sorted(root.glob("verdicts/*.json"))
        ]
        study_verdict = next(
            item
            for item in verdicts
            if item.get("verdict_id") == completion["study_verdict_id"]
        )
        results = [
            read_json(path)
            for path in sorted(root.glob("stage3/results/*.json"))
        ]
        sample_ids = sorted(
            {
                str(sample_id)
                for result in results
                for sample_id in result.get("sample_ids", [])
            }
        )
        evaluator_candidates = sorted(
            root.glob("stage3/**/evaluator.lock.json")
        )
        if not evaluator_candidates:
            raise ValueError("completion package lacks evaluator.lock.json")
        evaluator_digest = sha256_file(evaluator_candidates[-1])
        image_digests: list[str] = []
        for path in root.glob("stage3/**/environment.lock.json"):
            payload = read_json(path)
            value = str(payload.get("container_image_id") or "")
            if value:
                image_digests.append(value)
        original_summary = {
            "evaluation_id": evaluation["evaluation_id"],
            "study_verdict_id": completion["study_verdict_id"],
            "primary_metric": evaluation.get("treatment_estimate"),
            "baseline_metric": evaluation.get("baseline_estimate"),
            "effect": evaluation.get("paired_effect"),
            "interval": evaluation.get("confidence_interval"),
            "verdict": study_verdict.get("status"),
            "sample_ids": sample_ids,
            "required_run_cells": len(plan["cells"]),
            "output_hashes": {
                path.relative_to(root).as_posix(): sha256_file(path)
                for path in sorted(root.glob("stage3/results/*.json"))
            },
        }
        return {
            "study_id": completion["study_id"],
            "completion_package_id": completion["completion_id"],
            "profile_id": plan["profile"],
            "required_run_cells": len(plan["cells"]),
            "artifact_count": len(stage3_manifest["files"]),
            "evaluator_digest": evaluator_digest,
            "container_image_digests": sorted(set(image_digests)),
            "original_summary": original_summary,
        }


def build_reproduction_package(
    *,
    stage3_package_path: str | Path,
    policy: ReproductionPolicy,
    output_path: str | Path,
    private_key_pem: bytes,
    signing_identity: str,
    signing_key_id: str,
    source_commit: str,
    embedded_assets: dict[str, str | Path] | None = None,
    referenced_assets: list[ReproductionAsset] | None = None,
) -> dict[str, Any]:
    """Create a signed, content-addressed reproduction package."""

    stage3_package = Path(stage3_package_path).resolve()
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    inspection = inspect_stage3_package(stage3_package)
    if policy.study_id != inspection["study_id"]:
        raise ValueError("reproduction policy Study does not match package")
    if policy.completion_package_id != inspection["completion_package_id"]:
        raise ValueError("reproduction policy completion id does not match")
    if policy.profile_id != inspection["profile_id"]:
        raise ValueError("reproduction policy profile does not match package")

    key = serialization.load_pem_private_key(private_key_pem, password=None)
    public_key = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_fingerprint = hashlib.sha256(public_key).hexdigest()

    with tempfile.TemporaryDirectory(prefix="rf-reproduction-package-") as temp:
        root = Path(temp) / "stage3-reproduction-package"
        root.mkdir()
        files: dict[str, str] = {}
        objects = root / "objects" / "sha256"
        objects.mkdir(parents=True)

        original_digest = sha256_file(stage3_package)
        original_relative = f"objects/sha256/{original_digest}"
        shutil.copy2(stage3_package, root / original_relative)
        files[original_relative] = original_digest

        policy_path = root / "reproduction-policy.json"
        write_json_atomic(policy_path, policy)
        files["reproduction-policy.json"] = sha256_file(policy_path)

        summary_path = root / "original-evaluation.json"
        write_json_atomic(summary_path, inspection["original_summary"])
        files["original-evaluation.json"] = sha256_file(summary_path)

        assets = list(referenced_assets or [])
        for logical_path, source_value in sorted(
            (embedded_assets or {}).items()
        ):
            source = Path(source_value).resolve()
            if not source.is_file():
                raise FileNotFoundError(source)
            digest = sha256_file(source)
            object_path = f"objects/sha256/{digest}"
            destination = root / object_path
            if not destination.exists():
                shutil.copy2(source, destination)
            files[object_path] = digest
            assets.append(
                ReproductionAsset(
                    asset_id=stable_id(
                        "repro-asset", logical_path, digest, "embedded"
                    ),
                    logical_path=logical_path,
                    mode=ReproductionAssetMode.EMBEDDED,
                    sha256=digest,
                    size_bytes=source.stat().st_size,
                    object_path=object_path,
                )
            )

        manifest_draft = ReproductionPackageManifest(
            package_id=stable_id(
                "reproduction-package",
                inspection["study_id"],
                inspection["completion_package_id"],
                canonical_sha256(policy),
                original_digest,
            ),
            package_sha256="0" * 64,
            original_archive_sha256=original_digest,
            original_archive_format=(
                "tar.zst"
                if stage3_package.name.endswith(".tar.zst")
                else "zip"
            ),
            study_id=inspection["study_id"],
            completion_package_id=inspection["completion_package_id"],
            profile_id=inspection["profile_id"],
            required_run_cells=inspection["required_run_cells"],
            artifact_count=inspection["artifact_count"],
            assets=assets,
            container_image_digests=inspection["container_image_digests"],
            evaluator_digest=inspection["evaluator_digest"],
            policy_id=policy.policy_id,
            policy_sha256=sha256_file(policy_path),
            files=files,
            signing_identity=signing_identity,
            signing_key_id=signing_key_id,
        )
        manifest = manifest_draft.model_copy(
            update={
                "package_sha256": reproduction_manifest_digest(
                    manifest_draft
                )
            }
        )
        manifest_path = root / "package-manifest.json"
        write_json_atomic(manifest_path, manifest)

        attestation = sign_subject(
            subject_type="Stage3ReproductionPackage",
            subject_path="package-manifest.json",
            subject_digest=manifest.package_sha256,
            private_key_pem=private_key_pem,
            identity=signing_identity,
            source_commit=source_commit,
            policy_version="reproduction-package-v1",
            external_parameters={
                "signing_key_id": signing_key_id,
                "public_key_fingerprint": key_fingerprint,
            },
        )
        signature_path = root / "signatures" / "package-attestation.json"
        write_json_atomic(signature_path, attestation)
        _archive_directory(root, output)

    return {
        "package_id": manifest.package_id,
        "package_sha256": manifest.package_sha256,
        "archive_sha256": sha256_file(output),
        "output": str(output),
        "signing_key_id": signing_key_id,
        "public_key_fingerprint": key_fingerprint,
        "required_run_cells": manifest.required_run_cells,
        "asset_count": len(manifest.assets),
        "evidence_grade": "RF-E1_package_verified",
        "rf_e2_awarded": False,
    }


__all__ = [
    "build_reproduction_package",
    "inspect_stage3_package",
    "reproduction_manifest_digest",
]
