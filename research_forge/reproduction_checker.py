"""Standalone verification for Stage 3 reproduction packages and receipts."""

from __future__ import annotations

import base64
import shutil
import tempfile
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization

from .reproduction_attestation import (
    verify_signed_reproduction_receipt,
)
from .reproduction_domain import (
    ReproductionAssetMode,
    ReproductionPackageManifest,
    ReproductionPolicy,
    SignedReproductionReceipt,
)
from .reproduction_package import reproduction_manifest_digest
from .stage_three_trust import (
    SignedResearchAttestation,
    _safe_extract,
    verify_attestation,
    verify_stage3_completion_package,
)
from .storage import read_json, sha256_file


def _trusted_attestation_key(
    attestation: SignedResearchAttestation,
    *,
    key_id: str,
    trusted_control_plane_keys: dict[str, bytes],
) -> bool:
    public_pem = trusted_control_plane_keys.get(key_id)
    if public_pem is None:
        return False
    try:
        public = serialization.load_pem_public_key(public_pem)
        raw = public.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode("ascii") == (
            attestation.public_key_base64
        )
    except Exception:
        return False


def verify_reproduction_package(
    package_path: str | Path,
    *,
    trusted_control_plane_keys: dict[str, bytes] | None = None,
    resolved_assets: dict[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Verify without a Workflow database or Research Forge web service."""

    package = Path(package_path).resolve()
    trusted_keys = trusted_control_plane_keys or {}
    resolved = resolved_assets or {}
    checks: dict[str, bool] = {}
    violations: list[str] = []
    blockers: list[str] = []
    manifest: ReproductionPackageManifest | None = None
    policy: ReproductionPolicy | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="rf-repro-verify-") as temp:
            root = Path(temp) / "package"
            root.mkdir()
            _safe_extract(package, root)
            manifest = ReproductionPackageManifest.model_validate(
                read_json(root / "package-manifest.json")
            )
            policy = ReproductionPolicy.model_validate(
                read_json(root / "reproduction-policy.json")
            )
            checks["manifest_digest"] = (
                reproduction_manifest_digest(manifest)
                == manifest.package_sha256
            )
            checks["policy_identity"] = (
                policy.policy_id == manifest.policy_id
                and policy.study_id == manifest.study_id
                and policy.completion_package_id
                == manifest.completion_package_id
                and policy.profile_id == manifest.profile_id
            )
            checks["policy_hash"] = (
                sha256_file(root / "reproduction-policy.json")
                == manifest.policy_sha256
            )
            checks["file_hashes"] = all(
                (root / relative).is_file()
                and sha256_file(root / relative) == digest
                for relative, digest in manifest.files.items()
            )
            attestation = SignedResearchAttestation.model_validate(
                read_json(
                    root / "signatures" / "package-attestation.json"
                )
            )
            checks["signature_valid"] = (
                verify_attestation(attestation)
                and attestation.subject_type
                == "Stage3ReproductionPackage"
                and attestation.subject_digest == manifest.package_sha256
                and attestation.subject_path == "package-manifest.json"
            )
            checks["trusted_control_plane_signer"] = _trusted_attestation_key(
                attestation,
                key_id=manifest.signing_key_id,
                trusted_control_plane_keys=trusted_keys,
            )
            original_object = (
                root
                / "objects"
                / "sha256"
                / manifest.original_archive_sha256
            )
            checks["original_archive_bound"] = (
                original_object.is_file()
                and sha256_file(original_object)
                == manifest.original_archive_sha256
            )
            if checks["original_archive_bound"]:
                verified_original = (
                    root
                    / (
                        "original-completion.tar.zst"
                        if manifest.original_archive_format == "tar.zst"
                        else "original-completion.zip"
                    )
                )
                shutil.copy2(original_object, verified_original)
                original_verification = verify_stage3_completion_package(
                    verified_original
                )
            else:
                original_verification = {
                    "passed": False,
                    "violations": ["original unavailable"],
                }
            checks["original_stage3_package_verified"] = bool(
                original_verification["passed"]
            )
            missing_required: list[str] = []
            asset_hashes_valid = True
            for asset in manifest.assets:
                if asset.mode is ReproductionAssetMode.EMBEDDED:
                    path = root / str(asset.object_path)
                else:
                    value = resolved.get(asset.asset_id)
                    path = Path(value).resolve() if value else None
                available = bool(
                    path
                    and path.is_file()
                    and sha256_file(path) == asset.sha256
                )
                if not available:
                    asset_hashes_valid = False
                    if asset.required:
                        missing_required.append(asset.asset_id)
            checks["asset_hashes"] = asset_hashes_valid
            if missing_required:
                blockers.append(
                    "reproduction_blocked_missing_asset:"
                    + ",".join(sorted(missing_required))
                )
            checks["all_required_assets_available"] = not missing_required
            from .reproduction_policy import (
                reproduction_comparator_registered,
            )

            checks["profile_comparator_registered"] = (
                reproduction_comparator_registered(manifest.profile_id)
            )
    except Exception as exc:
        violations.append(str(exc))
    violations.extend(
        name
        for name, passed in checks.items()
        if not passed
        and name
        not in {
            "trusted_control_plane_signer",
            "all_required_assets_available",
            "asset_hashes",
        }
    )
    package_valid = not violations and bool(checks)
    trusted = checks.get("trusted_control_plane_signer", False)
    assets_ready = checks.get("all_required_assets_available", False)
    return {
        "passed": package_valid,
        "trusted": package_valid and trusted,
        "reproduction_ready": package_valid and trusted and assets_ready,
        "checks": checks,
        "violations": violations,
        "blockers": blockers,
        "manifest": (
            manifest.model_dump(mode="json") if manifest is not None else None
        ),
        "policy": (
            policy.model_dump(mode="json") if policy is not None else None
        ),
        "evidence_grade": (
            "RF-E1_package_verified"
            if package_valid and trusted
            else None
        ),
    }


def verify_reproduction_receipt(
    signed_receipt_path: str | Path,
    *,
    trusted_verifier_keys: dict[str, bytes],
) -> dict[str, Any]:
    signed = SignedReproductionReceipt.model_validate(
        read_json(Path(signed_receipt_path).resolve())
    )
    signature_valid = verify_signed_reproduction_receipt(
        signed, trusted_public_keys=trusted_verifier_keys
    )
    receipt = signed.receipt
    return {
        "passed": signature_valid,
        "signature_valid": signature_valid,
        "receipt_id": receipt.receipt_id,
        "study_id": receipt.study_id,
        "result": receipt.result.value,
        "evidence_grade": (
            receipt.evidence_grade_awarded.value
            if signature_valid
            else None
        ),
    }


__all__ = [
    "verify_reproduction_package",
    "verify_reproduction_receipt",
]
