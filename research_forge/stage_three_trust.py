"""Portable trust, export, and offline verification for Stage 3 packages."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .models import StrictModel, utc_now
from .storage import read_json, sha256_file, write_json_atomic, write_text_atomic
from .workflow_domain import WorkflowRepository


MAX_PACKAGE_BYTES = 2_000_000_000
MAX_PACKAGE_FILES = 100_000


class SignedResearchAttestation(StrictModel):
    schema_version: int = 1
    attestation_id: str
    subject_type: str
    subject_path: str
    subject_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    builder_or_executor_identity: str
    source_commit: str
    container_digest: str | None = None
    external_parameters: dict[str, Any] = Field(default_factory=dict)
    started_at: str
    ended_at: str
    policy_version: str
    algorithm: str = "Ed25519"
    public_key_base64: str
    signature_base64: str


class CleanRoomReproductionReceipt(StrictModel):
    schema_version: int = 1
    receipt_id: str
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    worker_identity: str
    status: str
    fresh_worker: bool
    original_development_directory_mounted: bool
    original_cache_mounted: bool
    sealed_assets_only: bool
    signatures_and_hashes_verified: bool
    rerun_scope: str
    effect_matches: bool
    interval_matches: bool
    verdict_matches: bool
    evidence_level: str
    attestation_id: str
    created_at: str = Field(default_factory=utc_now)


class BackupRestoreDrillReceipt(StrictModel):
    schema_version: int = 1
    receipt_id: str
    source_package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    restored_copy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fresh_restore_root: bool
    offline_verification_passed: bool
    append_only_source_preserved: Literal[True] = True
    status: str
    created_at: str = Field(default_factory=utc_now)


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _attestation_payload(
    attestation: SignedResearchAttestation | dict[str, Any],
) -> dict[str, Any]:
    payload = (
        attestation.model_dump(mode="json")
        if isinstance(attestation, SignedResearchAttestation)
        else dict(attestation)
    )
    payload.pop("signature_base64", None)
    return payload


def sign_subject(
    *,
    subject_type: str,
    subject_path: str,
    subject_digest: str,
    private_key_pem: bytes,
    identity: str,
    source_commit: str,
    policy_version: str = "stage3-trust-v1",
    container_digest: str | None = None,
    external_parameters: dict[str, Any] | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> SignedResearchAttestation:
    """Sign one immutable subject with a control-plane Ed25519 key."""

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Stage 3 attestation key must be Ed25519")
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    now = utc_now()
    unsigned = {
        "schema_version": 1,
        "attestation_id": (
            "attestation-"
            + hashlib.sha256(
                f"{subject_type}:{subject_path}:{subject_digest}".encode()
            ).hexdigest()[:16]
        ),
        "subject_type": subject_type,
        "subject_path": subject_path,
        "subject_digest": subject_digest,
        "builder_or_executor_identity": identity,
        "source_commit": source_commit,
        "container_digest": container_digest,
        "external_parameters": external_parameters or {},
        "started_at": started_at or now,
        "ended_at": ended_at or now,
        "policy_version": policy_version,
        "algorithm": "Ed25519",
        "public_key_base64": base64.b64encode(public).decode("ascii"),
    }
    signature = key.sign(_canonical_json(unsigned))
    return SignedResearchAttestation(
        **unsigned,
        signature_base64=base64.b64encode(signature).decode("ascii"),
    )


def verify_attestation(attestation: SignedResearchAttestation) -> bool:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )

    try:
        key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(attestation.public_key_base64, validate=True)
        )
        key.verify(
            base64.b64decode(attestation.signature_base64, validate=True),
            _canonical_json(_attestation_payload(attestation)),
        )
        return True
    except Exception:
        return False


def assess_clean_room_reproduction(
    *,
    package_path: str | Path,
    worker_attestation: SignedResearchAttestation | dict[str, Any],
) -> CleanRoomReproductionReceipt:
    """Read a legacy worker self-attestation without awarding clean-room status.

    This compatibility function predates the isolated-verifier protocol.  A
    worker-controlled key cannot prove that the worker lacked access to that
    key, so even a cryptographically valid legacy attestation remains RF-E1.
    New integrations must submit an unsigned ``WorkerExecutionReport`` to an
    independent ``ReproductionVerifier`` whose KMS/HSM key is unavailable to
    both the worker and the original control plane.
    """

    package = Path(package_path).resolve()
    attestation = SignedResearchAttestation.model_validate(
        worker_attestation
    )
    package_digest = sha256_file(package)
    parameters = attestation.external_parameters
    invariants = {
        "attestation_valid": verify_attestation(attestation),
        "subject_is_package": (
            attestation.subject_type == "CleanRoomReproduction"
            and attestation.subject_digest == package_digest
        ),
        "fresh_worker": parameters.get("fresh_worker") is True,
        "no_development_mount": (
            parameters.get("original_development_directory_mounted") is False
        ),
        "no_cache_mount": parameters.get("original_cache_mounted") is False,
        "sealed_assets_only": parameters.get("sealed_assets_only") is True,
        "signatures_verified": (
            parameters.get("signatures_and_hashes_verified") is True
        ),
        "effect_matches": parameters.get("effect_matches") is True,
        "interval_matches": parameters.get("interval_matches") is True,
        "verdict_matches": parameters.get("verdict_matches") is True,
    }
    passed = all(invariants.values())
    return CleanRoomReproductionReceipt(
        receipt_id=(
            "clean-room-receipt-"
            + hashlib.sha256(
                f"{package_digest}:{attestation.attestation_id}".encode()
            ).hexdigest()[:16]
        ),
        package_sha256=package_digest,
        worker_identity=attestation.builder_or_executor_identity,
        status=(
            "legacy_worker_self_attestation_rejected"
            if passed
            else "failed"
        ),
        fresh_worker=bool(parameters.get("fresh_worker")),
        original_development_directory_mounted=bool(
            parameters.get("original_development_directory_mounted")
        ),
        original_cache_mounted=bool(
            parameters.get("original_cache_mounted")
        ),
        sealed_assets_only=bool(parameters.get("sealed_assets_only")),
        signatures_and_hashes_verified=bool(
            parameters.get("signatures_and_hashes_verified")
        ),
        rerun_scope=str(parameters.get("rerun_scope") or "unspecified"),
        effect_matches=bool(parameters.get("effect_matches")),
        interval_matches=bool(parameters.get("interval_matches")),
        verdict_matches=bool(parameters.get("verdict_matches")),
        evidence_level="RF-E1_package_verified",
        attestation_id=attestation.attestation_id,
    )


def _subject_type(relative: str) -> str:
    if "specifications/" in relative:
        return "ScientificSpecificationSeal"
    if "execution_packages/" in relative:
        return "ExecutionPackageSeal"
    if "run_plans/" in relative:
        return "RunPlan"
    if "attempts/" in relative:
        return "ExecutionAttemptAttestation"
    if "evaluations/" in relative:
        return "EvaluationRecord"
    if "completion/" in relative:
        return "Stage3CompletionPackage"
    return "FrozenStage3Artifact"


def _write_interchange_exports(
    root: Path, completion: dict[str, Any]
) -> list[str]:
    published_at = str(completion.get("created_at") or utc_now())[:10]
    workflow_path = "workflow.json"
    workflow_profile = "https://w3id.org/ro/wfrun/workflow/0.5"
    process_profile = "https://w3id.org/ro/wfrun/process/0.5"
    workflow_ro_profile = (
        "https://w3id.org/workflowhub/workflow-ro-crate/1.0"
    )
    artifact_ids = [
        relative for relative, _ in sorted(completion["artifact_hashes"].items())
    ]
    artifact_entities = [
        {
            "@id": relative,
            "@type": "File",
            "name": Path(relative).name,
            "identifier": {"@id": f"#sha256-{digest}"},
        }
        for relative, digest in sorted(completion["artifact_hashes"].items())
    ]
    hash_entities = [
        {
            "@id": f"#sha256-{digest}",
            "@type": "PropertyValue",
            "propertyID": "sha256",
            "value": digest,
        }
        for digest in sorted(set(completion["artifact_hashes"].values()))
    ]
    prov = {
        "@context": "https://www.w3.org/ns/prov.jsonld",
        "entity": {
            f"rf:{relative}": {
                "prov:type": "rf:FrozenArtifact",
                "rf:sha256": digest,
            }
            for relative, digest in completion["artifact_hashes"].items()
        },
        "activity": {
            f"rf:{completion['plan_id']}": {
                "prov:type": "rf:Stage3Execution",
                "prov:generated": [
                    f"rf:{relative}"
                    for relative in completion["artifact_hashes"]
                ],
            }
        },
    }
    workflow_descriptor = {
        "schema_version": 1,
        "workflow_type": "research_forge_stage3_frozen_execution",
        "study_id": completion["study_id"],
        "plan_id": completion["plan_id"],
        "confirmatory_status": completion.get(
            "confirmatory_status", "unknown"
        ),
        "frozen_artifacts": [
            {"path": relative, "sha256": digest}
            for relative, digest in sorted(
                completion["artifact_hashes"].items()
            )
        ],
        "authority_note": (
            "This descriptor records the frozen Stage 3 execution object; "
            "it does not create or upgrade a scientific verdict."
        ),
    }
    crate = {
        "@context": "https://w3id.org/ro/crate/1.1/context",
        "@graph": [
            {
                "@id": "ro-crate-metadata.json",
                "@type": "CreativeWork",
                "about": {"@id": "./"},
                "conformsTo": {
                    "@id": "https://w3id.org/ro/crate/1.1"
                },
            },
            {
                "@id": "./",
                "@type": "Dataset",
                "name": "Research Forge Stage 3 completion package",
                "description": (
                    "A content-addressed Stage 3 completion package with "
                    "frozen scientific artifacts and bounded provenance."
                ),
                "license": "NOASSERTION",
                "datePublished": published_at,
                "conformsTo": [
                    {"@id": workflow_profile},
                    {"@id": process_profile},
                    {"@id": workflow_ro_profile},
                ],
                "mainEntity": {"@id": workflow_path},
                "mentions": {"@id": "#stage3-execution"},
                "hasPart": [
                    {"@id": relative}
                    for relative in artifact_ids
                ]
                + [
                    {"@id": workflow_path},
                ],
            },
            *artifact_entities,
            *hash_entities,
            {
                "@id": workflow_path,
                "@type": [
                    "File",
                    "SoftwareSourceCode",
                    "ComputationalWorkflow",
                ],
                "name": "Research Forge Stage 3 frozen execution workflow",
                "description": (
                    "Machine-readable descriptor of the frozen Stage 3 run, "
                    "its input artifact collection, and its output artifact "
                    "collection."
                ),
                "encodingFormat": "application/json",
                "programmingLanguage": {"@id": "#json-language"},
                "input": {"@id": "#frozen-stage3-input"},
                "output": {"@id": "#frozen-stage3-output"},
            },
            {
                "@id": "#json-language",
                "@type": "ComputerLanguage",
                "name": "JSON",
                "alternateName": "JSON",
            },
            {
                "@id": "#frozen-stage3-input",
                "@type": "FormalParameter",
                "name": "Frozen Stage 3 input artifacts",
                "description": (
                    "Content-addressed artifacts authorized by the frozen "
                    "Stage 3 plan."
                ),
                "additionalType": "https://schema.org/MediaObject",
                "workExample": {"@id": "#input-artifact-collection"},
            },
            {
                "@id": "#frozen-stage3-output",
                "@type": "FormalParameter",
                "name": "Frozen Stage 3 completion artifacts",
                "description": (
                    "Content-addressed evaluation and completion artifacts "
                    "produced by the recorded Stage 3 execution."
                ),
                "additionalType": "https://schema.org/MediaObject",
                "workExample": {"@id": "#output-artifact-collection"},
            },
            {
                "@id": "#input-artifact-collection",
                "@type": "Collection",
                "name": "Frozen Stage 3 input artifact collection",
                "hasPart": [{"@id": relative} for relative in artifact_ids],
            },
            {
                "@id": "#output-artifact-collection",
                "@type": "Collection",
                "name": "Frozen Stage 3 output artifact collection",
                "hasPart": [{"@id": relative} for relative in artifact_ids],
            },
            {
                "@id": "#stage3-execution",
                "@type": "CreateAction",
                "name": "Research Forge Stage 3 execution",
                "description": (
                    "Recorded execution that generated the frozen Stage 3 "
                    "completion artifacts."
                ),
                "actionStatus": "CompletedActionStatus",
                "instrument": {"@id": workflow_path},
                "object": {"@id": "#input-artifact-collection"},
                "result": {"@id": "#output-artifact-collection"},
                "agent": {"@id": "#research-forge"},
            },
            {
                "@id": "#research-forge",
                "@type": "Organization",
                "name": "Research Forge",
            },
            {
                "@id": workflow_profile,
                "@type": "CreativeWork",
                "name": "Workflow Run Crate 0.5",
            },
            {
                "@id": process_profile,
                "@type": "CreativeWork",
                "name": "Process Run Crate 0.5",
            },
            {
                "@id": workflow_ro_profile,
                "@type": "CreativeWork",
                "name": "Workflow RO-Crate 1.0",
            },
        ],
    }
    write_json_atomic(root / "prov.jsonld", prov)
    write_json_atomic(root / workflow_path, workflow_descriptor)
    write_json_atomic(root / "ro-crate-metadata.json", crate)
    write_text_atomic(
        root / "report.html",
        (
            "<!doctype html><meta charset='utf-8'>"
            "<title>Research Forge Stage 3 verification package</title>"
            "<h1>Stage 3 completion package</h1>"
            f"<p>Study: {completion['study_id']}</p>"
            f"<p>Plan: {completion['plan_id']}</p>"
            f"<p>Confirmatory status: "
            f"{completion.get('confirmatory_status', 'unknown')}</p>"
            "<p>This report is a portable audit view, not a third-party "
            "certification.</p>"
        ),
    )
    return [
        "prov.jsonld",
        "workflow.json",
        "ro-crate-metadata.json",
        "report.html",
    ]


def export_stage3_completion_package(
    *,
    repository_root: str | Path,
    study_id: str,
    output_path: str | Path,
    private_key_path: str | Path,
    identity: str,
    source_commit: str,
) -> dict[str, Any]:
    """Create a signed self-contained ZIP or tar.zst from frozen Stage 3 data."""

    repository_root = Path(repository_root).resolve()
    repository = WorkflowRepository(repository_root)
    study_root = repository_root / "studies" / study_id
    completions = repository.list_stage3_completions(study_id)
    if not completions:
        raise FileNotFoundError("no Stage 3 completion package was found")
    completion = completions[-1].model_dump(mode="json")
    completion_path = (
        study_root
        / "stage3"
        / "completion"
        / f"{completion['completion_id']}.json"
    )
    resolved_key_path = Path(private_key_path).resolve()
    if (
        resolved_key_path == repository_root
        or repository_root in resolved_key_path.parents
    ):
        raise ValueError(
            "attestation private key must come from the control plane or "
            "KMS-managed storage outside the workflow repository"
        )
    key_bytes = resolved_key_path.read_bytes()
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="rf-stage3-export-") as temporary:
        package_root = Path(temporary) / "package"
        package_root.mkdir()
        copied: dict[str, str] = {}
        for relative, expected_hash in completion["artifact_hashes"].items():
            source = (study_root / relative).resolve()
            if study_root != source and study_root not in source.parents:
                raise ValueError(f"artifact escapes Study root: {relative}")
            if sha256_file(source) != expected_hash:
                raise ValueError(f"artifact hash changed before export: {relative}")
            destination = package_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied[relative] = expected_hash
        completion_relative = (
            f"stage3/completion/{completion_path.name}"
        )
        completion_destination = package_root / completion_relative
        completion_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(completion_path, completion_destination)
        copied[completion_relative] = sha256_file(completion_path)
        for relative in _write_interchange_exports(package_root, completion):
            copied[relative] = sha256_file(package_root / relative)

        attestations = [
            sign_subject(
                subject_type=_subject_type(relative),
                subject_path=relative,
                subject_digest=digest,
                private_key_pem=key_bytes,
                identity=identity,
                source_commit=source_commit,
            ).model_dump(mode="json")
            for relative, digest in sorted(copied.items())
        ]
        manifest = {
            "schema_version": 1,
            "study_id": study_id,
            "completion_path": completion_relative,
            "files": copied,
            "attestations": attestations,
            "evidence_level": completion.get(
                "evidence_level", "L1_evidence_chain_verified"
            ),
            "exported_at": utc_now(),
        }
        write_json_atomic(package_root / "package-manifest.json", manifest)

        temporary_output = output.with_suffix(output.suffix + ".tmp")
        if output.name.endswith(".tar.zst"):
            import zstandard

            tar_path = Path(temporary) / "package.tar"
            with tarfile.open(tar_path, "w") as archive:
                for path in sorted(package_root.rglob("*")):
                    archive.add(
                        path,
                        arcname=path.relative_to(package_root).as_posix(),
                        recursive=False,
                    )
            compressor = zstandard.ZstdCompressor(level=10)
            with tar_path.open("rb") as source, temporary_output.open("wb") as sink:
                compressor.copy_stream(source, sink)
        elif output.suffix.lower() == ".zip":
            with zipfile.ZipFile(
                temporary_output, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for path in sorted(package_root.rglob("*")):
                    if path.is_file():
                        archive.write(
                            path,
                            path.relative_to(package_root).as_posix(),
                        )
        else:
            raise ValueError("Stage 3 export must end in .zip or .tar.zst")
        os.replace(temporary_output, output)
    return {
        "output": str(output),
        "sha256": sha256_file(output),
        "file_count": len(copied) + 1,
        "signed": True,
        "third_party_certified": False,
    }


def _safe_extract(package: Path, destination: Path) -> None:
    def validate_name(name: str, size: int, *, link: bool = False) -> Path:
        if link:
            raise ValueError("package contains a link")
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("package contains path traversal")
        if size < 0 or size > MAX_PACKAGE_BYTES:
            raise ValueError("package member exceeds size limit")
        target = (destination / relative).resolve()
        if destination != target and destination not in target.parents:
            raise ValueError("package member escapes extraction root")
        return target

    if package.suffix.lower() == ".zip":
        with zipfile.ZipFile(package) as archive:
            if len(archive.infolist()) > MAX_PACKAGE_FILES:
                raise ValueError("package has too many files")
            total = 0
            for info in archive.infolist():
                total += info.file_size
                if total > MAX_PACKAGE_BYTES:
                    raise ValueError("package exceeds uncompressed size limit")
                target = validate_name(info.filename, info.file_size)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as sink:
                        shutil.copyfileobj(source, sink)
        return
    if package.name.endswith(".tar.zst"):
        import zstandard

        tar_path = destination.parent / "package.tar"
        decompressor = zstandard.ZstdDecompressor()
        with package.open("rb") as source, tar_path.open("wb") as sink:
            decompressor.copy_stream(
                source, sink, write_size=1024 * 1024
            )
        if tar_path.stat().st_size > MAX_PACKAGE_BYTES:
            raise ValueError("decompressed tar exceeds size limit")
        with tarfile.open(tar_path, "r:") as archive:
            members = archive.getmembers()
            if len(members) > MAX_PACKAGE_FILES:
                raise ValueError("package has too many files")
            total = 0
            for member in members:
                total += member.size
                if total > MAX_PACKAGE_BYTES:
                    raise ValueError("package exceeds uncompressed size limit")
                target = validate_name(
                    member.name,
                    member.size,
                    link=member.issym() or member.islnk(),
                )
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError("tar member cannot be read")
                    with source, target.open("wb") as sink:
                        shutil.copyfileobj(source, sink)
        tar_path.unlink(missing_ok=True)
        return
    raise ValueError("unsupported package type; use .zip or .tar.zst")


def verify_stage3_completion_package(
    package_path: str | Path,
) -> dict[str, Any]:
    """Verify a Stage 3 package without a database, web app, or worker."""

    package = Path(package_path).resolve()
    violations: list[str] = []
    checks: dict[str, bool] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="rf-stage3-verify-") as temporary:
            root = Path(temporary) / "extracted"
            root.mkdir()
            _safe_extract(package, root)
            manifest = read_json(root / "package-manifest.json")
            files = dict(manifest.get("files") or {})
            checks["package_schema"] = (
                manifest.get("schema_version") == 1 and bool(files)
            )
            checks["artifact_hashes"] = all(
                (root / relative).is_file()
                and sha256_file(root / relative) == digest
                for relative, digest in files.items()
            )
            attestations = [
                SignedResearchAttestation.model_validate(item)
                for item in manifest.get("attestations") or []
            ]
            attested = {
                (item.subject_path, item.subject_digest)
                for item in attestations
                if verify_attestation(item)
            }
            checks["signatures"] = len(attestations) == len(files) and all(
                (relative, digest) in attested
                for relative, digest in files.items()
            )
            completion = read_json(root / manifest["completion_path"])
            plans = [
                read_json(path)
                for path in root.glob("stage3/run_plans/run-plan-*.json")
            ]
            evaluations = [
                read_json(path)
                for path in root.glob(
                    "stage3/evaluations/evaluation-*.json"
                )
            ]
            results = [
                read_json(path)
                for path in root.glob("stage3/results/result-*.json")
            ]
            attempts = [
                read_json(path)
                for path in root.glob("stage3/attempts/attempt-*.json")
            ]
            checks["core_records_present"] = bool(
                plans and evaluations and results
            )
            plan = next(
                item for item in plans
                if item["plan_id"] == completion["plan_id"]
            )
            plan_payload = {
                key: value for key, value in plan.items()
                if key not in {"created_at", "plan_hash"}
            }
            checks["run_plan_hash"] = (
                hashlib.sha256(
                    _canonical_json(plan_payload)
                ).hexdigest()
                == plan["plan_hash"]
            )
            cell_ids = {item["run_cell_id"] for item in plan["cells"]}
            by_cell = {
                cell_id: [
                    item for item in results
                    if item["run_cell_id"] == cell_id
                ]
                for cell_id in cell_ids
            }
            checks["one_result_per_cell"] = all(
                len(items) == 1 for items in by_cell.values()
            )
            checks["sample_denominators"] = all(
                item["denominator"] == len(item.get("sample_ids") or [])
                and len(item.get("sample_ids") or [])
                == len(set(item.get("sample_ids") or []))
                for item in results
            )
            canonical_cells = {
                item["run_cell_id"] for item in attempts
                if item.get("status") == "succeeded"
                and item.get("canonical") is True
            } | {
                item["run_cell_id"] for item in results
                if item.get("reused_from_result_id")
            }
            checks["canonical_attempts"] = canonical_cells == cell_ids
            evaluation = next(
                item for item in evaluations
                if item["evaluation_id"] in completion["evaluation_ids"]
            )
            cells = {
                item["run_cell_id"]: item for item in plan["cells"]
            }
            pair_map: dict[tuple[Any, ...], dict[str, float]] = {}
            for result in results:
                cell = cells[result["run_cell_id"]]
                key = (
                    cell["task_id"],
                    cell["split_id"],
                    cell["seed"],
                    cell["replicate"],
                )
                pair_map.setdefault(key, {})[cell["arm_id"]] = float(
                    result["metrics"][evaluation["metric_name"]]
                )
            keyed_effects = [
                (key, arms["treatment"] - arms["baseline"])
                for key, arms in pair_map.items()
                if set(arms) == {"baseline", "treatment"}
            ]
            modern_profiles = {
                "computational_paired_comparison_v2",
                "paired_binary_independent_v1",
                "paired_binary_clustered_v1",
            }
            profile_id = str(plan.get("profile") or "")
            modern_result = None
            if profile_id in modern_profiles:
                from .profiles.runtime import analyze_profile_rows
                from .workflow_domain import Stage3Profile

                row_pairs: dict[
                    tuple[Any, ...], dict[str, list[dict[str, Any]]]
                ] = {}
                for result in results:
                    cell = cells[result["run_cell_id"]]
                    key = (
                        cell["task_id"],
                        cell["split_id"],
                        cell["seed"],
                        cell["replicate"],
                    )
                    row_pairs.setdefault(key, {})[cell["arm_id"]] = list(
                        result.get("analysis_rows") or []
                    )
                modern_result = analyze_profile_rows(
                    Stage3Profile(profile_id),
                    [
                        (arms["baseline"], arms["treatment"])
                        for _, arms in sorted(row_pairs.items())
                        if set(arms) == {"baseline", "treatment"}
                    ],
                    (
                        evaluation.get("statistical_rule") or {}
                    ).get("profile_parameters")
                    or {},
                )
                independent = [modern_result.effect]
            elif evaluation.get("variance_unit") == "task":
                grouped: dict[str, list[float]] = {}
                for key, effect in keyed_effects:
                    grouped.setdefault(str(key[0]), []).append(effect)
                independent = [
                    sum(values) / len(values)
                    for values in grouped.values()
                ]
            else:
                independent = [effect for _, effect in keyed_effects]
            recomputed = (
                modern_result.effect
                if modern_result is not None
                else (
                    sum(independent) / len(independent)
                    if independent else None
                )
            )
            checks["metric_recomputed"] = (
                recomputed is not None
                and abs(recomputed - evaluation["paired_effect"]) <= 1e-12
                and (
                    modern_result.independent_unit_count
                    if modern_result is not None
                    else len(independent)
                )
                == evaluation["independent_unit_count"]
            )
            rule = evaluation.get("statistical_rule") or {}
            threshold = float(rule.get("effect_threshold", 0.0))
            direction = str(
                rule.get("metric_direction") or "higher_is_better"
            )
            directional = (
                recomputed
                if direction in {"higher_is_better", "maximize"}
                else (-recomputed if recomputed is not None else None)
            )
            if evaluation.get("qualification_status") != "qualified":
                expected_decision = (
                    "inconclusive"
                    if evaluation.get("qualification_status") == "incomplete"
                    else "unverifiable"
                )
            elif (
                modern_result is not None
                and direction in {"higher_is_better", "maximize"}
                and modern_result.confidence_interval[0] >= threshold
            ):
                expected_decision = "supported"
            elif (
                modern_result is not None
                and direction in {"higher_is_better", "maximize"}
                and modern_result.confidence_interval[1] <= -threshold
            ):
                expected_decision = "refuted"
            elif (
                modern_result is not None
                and direction in {"lower_is_better", "minimize"}
                and -modern_result.confidence_interval[1] >= threshold
            ):
                expected_decision = "supported"
            elif (
                modern_result is not None
                and direction in {"lower_is_better", "minimize"}
                and -modern_result.confidence_interval[0] <= -threshold
            ):
                expected_decision = "refuted"
            elif (
                modern_result is None
                and directional is not None
                and directional >= threshold
            ):
                expected_decision = "supported"
            elif (
                modern_result is None
                and directional is not None
                and directional <= -threshold
            ):
                expected_decision = "refuted"
            else:
                expected_decision = "inconclusive"
            checks["verdict_rule_recomputed"] = (
                evaluation.get("decision") == expected_decision
            )
            verdict_payloads = [
                read_json(path)
                for path in root.glob("verdicts/*.json")
            ]
            checks["materialized_verdict_matches"] = any(
                item.get("verdict_id")
                == completion.get("study_verdict_id")
                and item.get("status") == expected_decision
                for item in verdict_payloads
            )
            checks["exposure_state"] = bool(
                completion.get("exposure_record_ids")
                and completion.get("confirmatory_status")
                in {"confirmatory_used", "adaptive_reuse", "exhausted"}
            )
            checks["claim_envelope"] = bool(
                completion.get("claim_envelope_id")
                and list(root.glob(
                    "stage3/claim_envelopes/claim-envelope-*.json"
                ))
            )
            checks["evidence_graph"] = bool(
                completion.get("evidence_edge_ids")
                and len(
                    list(root.glob(
                        "stage3/evidence_edges/evidence-edge-*.json"
                    ))
                )
                == len(completion["evidence_edge_ids"])
            )
            evaluator_locks = list(
                root.glob("stage3/**/evaluator.lock.json")
            )
            evaluator_sources = list(
                root.glob("stage3/**/platform_evaluator.py")
            )
            evaluator_golden = list(
                root.glob("stage3/**/evaluator_golden_vectors.json")
            )
            independent_required = any(
                read_json(path).get("independent_from_arms") is True
                for path in evaluator_locks
            )
            checks["evaluator_golden_vectors_present"] = (
                not independent_required
                or bool(evaluator_sources and evaluator_golden)
            )
            checks["stage4_eligible"] = all(checks.values())
    except Exception as exc:
        violations.append(str(exc))
    violations.extend(
        name for name, passed in checks.items() if not passed
    )
    return {
        "passed": not violations and bool(checks),
        "checks": checks,
        "violations": violations,
        "stage4_eligible": checks.get("stage4_eligible", False)
        and not violations,
    }


def run_stage3_backup_restore_drill(
    package_path: str | Path,
) -> BackupRestoreDrillReceipt:
    """Copy a package into a fresh root and run the standalone verifier."""

    package = Path(package_path).resolve()
    source_digest = sha256_file(package)
    with tempfile.TemporaryDirectory(prefix="rf-stage3-restore-drill-") as temp:
        restore_root = Path(temp).resolve()
        restored = restore_root / package.name
        shutil.copy2(package, restored)
        restored_digest = sha256_file(restored)
        verification = verify_stage3_completion_package(restored)
        passed = (
            source_digest == restored_digest
            and verification["passed"] is True
        )
    return BackupRestoreDrillReceipt(
        receipt_id=(
            "backup-restore-drill-"
            + hashlib.sha256(
                f"{source_digest}:{utc_now()}".encode()
            ).hexdigest()[:16]
        ),
        source_package_sha256=source_digest,
        restored_copy_sha256=restored_digest,
        fresh_restore_root=True,
        offline_verification_passed=verification["passed"],
        status="passed" if passed else "failed",
    )


__all__ = [
    "BackupRestoreDrillReceipt",
    "CleanRoomReproductionReceipt",
    "SignedResearchAttestation",
    "assess_clean_room_reproduction",
    "export_stage3_completion_package",
    "run_stage3_backup_restore_drill",
    "sign_subject",
    "verify_attestation",
    "verify_stage3_completion_package",
]
