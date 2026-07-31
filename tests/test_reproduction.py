from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sys
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from research_forge.reproduction_attestation import (
    LocalDevelopmentEd25519Signer,
    create_reproduction_receipt,
    sign_reproduction_receipt,
    verify_signed_reproduction_receipt,
)
from research_forge.reproduction_domain import (
    ComparisonPolicy,
    ExternalVerifierIdentity,
    IsolationReport,
    ReproductionComparisonMode,
    ReproductionJob,
    ReproductionJobStatus,
    ReproductionPackageManifest,
    ReproductionPolicy,
    ReproductionScope,
    ResearchForgeEvidenceGrade,
    WorkerIdentity,
)
from research_forge.reproduction_checker import verify_reproduction_package
from research_forge.reproduction_package import build_reproduction_package
from research_forge.reproduction_launcher import (
    run_local_development_reproduction,
)
from research_forge.reproduction_policy import (
    compare_reproduction,
    freeze_reproduction_policy,
    reproduction_comparator_registered,
)
from research_forge.reproduction_verifier import (
    ReproductionStore,
    assess_reproduction_evidence,
)
from research_forge.reproduction_worker_protocol import (
    build_unsigned_worker_report,
)
from research_forge.stage_three_trust import sign_subject
from research_forge.storage import read_json, sha256_file
from research_forge.workflow_domain import Stage3Profile


@pytest.mark.parametrize(
    "profile",
    [
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
        Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V2,
        Stage3Profile.PAIRED_BINARY_INDEPENDENT_V1,
        Stage3Profile.PAIRED_BINARY_CLUSTERED_V1,
    ],
)
def test_every_formal_paired_profile_has_reproduction_comparator(
    profile: Stage3Profile,
) -> None:
    assert reproduction_comparator_registered(profile.value)


def _key_bytes() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_key_pem(private_key_pem: bytes) -> bytes:
    key = serialization.load_pem_private_key(
        private_key_pem, password=None
    )
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2
        )
        + "\n",
        encoding="utf-8",
    )


def _valid_stage3_archive(tmp_path: Path, key: bytes) -> Path:
    root = tmp_path / "stage3-package"
    plan = {
        "schema_version": 1,
        "plan_id": "run-plan-aaaaaaaaaaaaaaaa",
        "study_id": "study-reproduction",
        "handoff_id": "stage3-handoff-aaaaaaaaaaaaaaaa",
        "contract_version": 1,
        "profile": "computational_paired_comparison_v1",
        "compiler_version": "stage3-compiler-v1",
        "concurrency": 1,
        "cells": [
            {
                "run_cell_id": "run-cell-aaaaaaaaaaaaaaaa",
                "task_id": "task",
                "split_id": "formal",
                "seed": 1,
                "replicate": 1,
                "arm_id": "baseline",
            },
            {
                "run_cell_id": "run-cell-bbbbbbbbbbbbbbbb",
                "task_id": "task",
                "split_id": "formal",
                "seed": 1,
                "replicate": 1,
                "arm_id": "treatment",
            },
        ],
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    plan_payload = {
        name: value
        for name, value in plan.items()
        if name not in {"created_at", "plan_hash"}
    }
    plan["plan_hash"] = hashlib.sha256(
        json.dumps(
            plan_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    _write_json(
        root / "stage3/run_plans/run-plan-aaaaaaaaaaaaaaaa.json",
        plan,
    )
    for suffix, cell, metric in (
        ("a", "run-cell-aaaaaaaaaaaaaaaa", 0.5),
        ("b", "run-cell-bbbbbbbbbbbbbbbb", 1.0),
    ):
        _write_json(
            root / f"stage3/results/result-{suffix * 16}.json",
            {
                "result_id": f"result-{suffix * 16}",
                "run_cell_id": cell,
                "metrics": {"accuracy": metric},
                "denominator": 2,
                "sample_ids": ["s1", "s2"],
                "reused_from_result_id": None,
            },
        )
        _write_json(
            root / f"stage3/attempts/attempt-{suffix * 16}.json",
            {
                "attempt_id": f"attempt-{suffix * 16}",
                "run_cell_id": cell,
                "status": "succeeded",
                "canonical": True,
            },
        )
    _write_json(
        root / "stage3/evaluations/evaluation-aaaaaaaaaaaaaaaa.json",
        {
            "evaluation_id": "evaluation-aaaaaaaaaaaaaaaa",
            "metric_name": "accuracy",
            "paired_effect": 0.5,
            "independent_unit_count": 1,
            "variance_unit": "registered_pair",
            "qualification_status": "qualified",
            "statistical_rule": {
                "effect_threshold": 0.4,
                "metric_direction": "higher_is_better",
            },
            "decision": "supported",
            "baseline_estimate": 0.5,
            "treatment_estimate": 1.0,
            "confidence_interval": [0.5, 0.5],
        },
    )
    _write_json(
        root / "verdicts/study-verdict-a.json",
        {"verdict_id": "study-verdict-a", "status": "supported"},
    )
    _write_json(
        root / "stage3/exposures/formal-exposure-a.json",
        {"confirmatory_status": "confirmatory_used"},
    )
    _write_json(
        root / "stage3/claim_envelopes/claim-envelope-a.json",
        {"allowed_claim": "bounded synthetic claim"},
    )
    _write_json(
        root / "stage3/evidence_edges/evidence-edge-a.json",
        {"relation": "qualifies"},
    )
    _write_json(
        root / "stage3/execution_packages/evaluator.lock.json",
        {"independent_from_arms": False},
    )
    _write_json(
        root
        / "stage3/completion/stage3-completion-aaaaaaaaaaaaaaaa.json",
        {
            "completion_id": "stage3-completion-aaaaaaaaaaaaaaaa",
            "study_id": "study-reproduction",
            "plan_id": "run-plan-aaaaaaaaaaaaaaaa",
            "evaluation_ids": ["evaluation-aaaaaaaaaaaaaaaa"],
            "study_verdict_id": "study-verdict-a",
            "exposure_record_ids": ["formal-exposure-a"],
            "confirmatory_status": "confirmatory_used",
            "claim_envelope_id": "claim-envelope-a",
            "evidence_edge_ids": ["evidence-edge-a"],
        },
    )
    files = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*.json"))
    }
    attestations = [
        sign_subject(
            subject_type="FrozenStage3Artifact",
            subject_path=relative,
            subject_digest=digest,
            private_key_pem=key,
            identity="control-plane",
            source_commit="commit",
        ).model_dump(mode="json")
        for relative, digest in sorted(files.items())
    ]
    _write_json(
        root / "package-manifest.json",
        {
            "schema_version": 1,
            "study_id": "study-reproduction",
            "completion_path": (
                "stage3/completion/"
                "stage3-completion-aaaaaaaaaaaaaaaa.json"
            ),
            "files": files,
            "attestations": attestations,
        },
    )
    archive = tmp_path / "stage3-completion.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(root).as_posix())
    return archive


class _TestKmsSigner(LocalDevelopmentEd25519Signer):
    @property
    def key_custody(self) -> str:
        return "kms"


def _policy() -> ReproductionPolicy:
    return freeze_reproduction_policy(
        study_id="study-reproduction",
        completion_package_id="stage3-completion-aaaaaaaaaaaaaaaa",
        profile_id="computational_paired_comparison_v1",
        comparison_mode=(
            ReproductionComparisonMode.NUMERICALLY_EQUIVALENT
        ),
        metric_tolerance=1e-6,
        effect_tolerance=1e-3,
        interval_tolerance=1e-3,
    )


def _manifest(policy: ReproductionPolicy) -> ReproductionPackageManifest:
    return ReproductionPackageManifest(
        package_id="reproduction-package-aaaaaaaaaaaaaaaa",
        package_sha256="1" * 64,
        original_archive_sha256="2" * 64,
        original_archive_format="zip",
        study_id=policy.study_id,
        completion_package_id=policy.completion_package_id,
        profile_id=policy.profile_id,
        required_run_cells=4,
        artifact_count=12,
        evaluator_digest="3" * 64,
        policy_id=policy.policy_id,
        policy_sha256="4" * 64,
        files={"objects/sha256/" + "2" * 64: "2" * 64},
        signing_identity="control-plane-domain-a",
        signing_key_id="kms-a-package",
    )


def _job(
    policy: ReproductionPolicy,
    manifest: ReproductionPackageManifest,
) -> ReproductionJob:
    return ReproductionJob(
        job_id="reproduction-job-aaaaaaaaaaaaaaaa",
        study_id=policy.study_id,
        package_id=manifest.package_id,
        package_sha256=manifest.package_sha256,
        archive_sha256="5" * 64,
        mode=policy.mode,
        scope=policy.scope,
        required_evidence_grade=policy.required_evidence_grade,
        status=ReproductionJobStatus.VERIFYING,
        requested_by="owner",
    )


def _worker() -> WorkerIdentity:
    return WorkerIdentity(
        trust_domain_id="reproduction-domain-b",
        cloud="test-cloud",
        account_id="account-b",
        instance_id="instance-ephemeral-1",
        image_id="image-fixed-1",
        boot_id="boot-1",
        agent_digest="6" * 64,
    )


def _isolation() -> IsolationReport:
    return IsolationReport(
        fresh_worker=True,
        ephemeral_worker=True,
        development_directory_mounted=False,
        original_database_accessible=False,
        cache_reused=False,
        sealed_assets_only=True,
        candidate_network_enabled=False,
        credentials_visible_to_candidate=False,
        signing_key_accessible_to_worker=False,
        original_control_plane_accessible=False,
    )


def _summary(effect: float = 0.5) -> dict[str, object]:
    return {
        "primary_metric": 1.0,
        "effect": effect,
        "interval": [0.4, 0.6],
        "verdict": "supported",
        "sample_ids": ["a", "b"],
    }


def test_partial_replay_cannot_request_rf_e2() -> None:
    with pytest.raises(ValueError, match="partial replay"):
        ReproductionPolicy(
            policy_id="reproduction-policy-aaaaaaaaaaaaaaaa",
            study_id="study",
            completion_package_id="stage3-completion-aaaaaaaaaaaaaaaa",
            profile_id="computational_paired_comparison_v1",
            scope=ReproductionScope.PARTIAL,
            comparison_policy=ComparisonPolicy(
                mode=ReproductionComparisonMode.EXACT
            ),
            required_evidence_grade=(
                ResearchForgeEvidenceGrade.RF_E2_CLEAN_ROOM_REPLAYED
            ),
        )


def test_profile_comparator_uses_frozen_tolerance() -> None:
    policy = _policy()
    comparison = compare_reproduction(
        profile_id=policy.profile_id,
        original=_summary(),
        reproduced=_summary(effect=0.5005),
        policy=policy,
    )
    outside = compare_reproduction(
        profile_id=policy.profile_id,
        original=_summary(),
        reproduced=_summary(effect=0.502),
        policy=policy,
    )

    assert comparison.passed() is True
    assert outside.effect_matches is False


def test_worker_report_is_unsigned_and_kms_verifier_awards_rf_e2() -> None:
    policy = _policy()
    manifest = _manifest(policy)
    job = _job(policy, manifest)
    report = build_unsigned_worker_report(
        job=job,
        manifest=manifest,
        policy=policy,
        worker_identity=_worker(),
        isolation=_isolation(),
        original_summary=_summary(),
        reproduced_summary=_summary(effect=0.5005),
        executed_run_cells=4,
        qualified_run_cells=4,
        checker_passed=True,
    )
    assert "signature" not in report.model_dump(mode="json")
    verifier = ExternalVerifierIdentity(
        verifier_id="rf-reproduction-verifier",
        trust_domain_id="reproduction-domain-b",
        key_id="kms-b-receipt",
        key_custody="kms",
        identity_registry_version="test-v1",
    )
    receipt = create_reproduction_receipt(
        job=job,
        policy=policy,
        report=report,
        verifier_identity=verifier,
        trusted_package=True,
    )
    key = _key_bytes()
    signer = _TestKmsSigner(key, key_id="kms-b-receipt")
    signed = sign_reproduction_receipt(receipt, signer=signer)

    assert (
        receipt.evidence_grade_awarded
        is ResearchForgeEvidenceGrade.RF_E2_CLEAN_ROOM_REPLAYED
    )
    assert verify_signed_reproduction_receipt(
        signed,
        trusted_public_keys={
            signer.key_id: signer.public_key_pem(),
        },
    )


def test_local_key_cannot_sign_rf_e2() -> None:
    policy = _policy()
    manifest = _manifest(policy)
    job = _job(policy, manifest)
    report = build_unsigned_worker_report(
        job=job,
        manifest=manifest,
        policy=policy,
        worker_identity=_worker(),
        isolation=_isolation(),
        original_summary=_summary(),
        reproduced_summary=_summary(),
        executed_run_cells=4,
        qualified_run_cells=4,
        checker_passed=True,
    )
    verifier = ExternalVerifierIdentity(
        verifier_id="verifier",
        trust_domain_id="domain-b",
        key_id="kms-b",
        key_custody="kms",
        identity_registry_version="v1",
    )
    receipt = create_reproduction_receipt(
        job=job,
        policy=policy,
        report=report,
        verifier_identity=verifier,
        trusted_package=True,
    )
    local = LocalDevelopmentEd25519Signer(
        _key_bytes(), key_id="kms-b"
    )

    with pytest.raises(ValueError, match="local or worker-held"):
        sign_reproduction_receipt(receipt, signer=local)


def test_conflicting_receipt_puts_publication_under_review(
    tmp_path: Path,
) -> None:
    policy = _policy()
    manifest = _manifest(policy)
    job = _job(policy, manifest)
    report = build_unsigned_worker_report(
        job=job,
        manifest=manifest,
        policy=policy,
        worker_identity=_worker(),
        isolation=_isolation(),
        original_summary=_summary(),
        reproduced_summary={
            **_summary(effect=0.1),
            "verdict": "inconclusive",
        },
        executed_run_cells=4,
        qualified_run_cells=4,
        checker_passed=True,
    )
    verifier = ExternalVerifierIdentity(
        verifier_id="verifier",
        trust_domain_id="domain-b",
        key_id="kms-b",
        key_custody="kms",
        identity_registry_version="v1",
    )
    receipt = create_reproduction_receipt(
        job=job,
        policy=policy,
        report=report,
        verifier_identity=verifier,
        trusted_package=True,
    )
    signer = _TestKmsSigner(_key_bytes(), key_id="kms-b")
    signed = sign_reproduction_receipt(receipt, signer=signer)
    store = ReproductionStore(tmp_path / "workflow")
    store.save_receipt(signed)
    assessment = assess_reproduction_evidence(
        study_id=policy.study_id,
        package_verified=True,
        receipts=store.list_receipts(policy.study_id),
        trusted_verifier_keys={"kms-b": signer.public_key_pem()},
        conflicts=[],
    )

    assert receipt.evidence_grade_awarded == (
        ResearchForgeEvidenceGrade.RF_E1_PACKAGE_VERIFIED
    )
    assert assessment.highest_grade == (
        ResearchForgeEvidenceGrade.RF_E1_PACKAGE_VERIFIED
    )


def test_reproduction_package_is_policy_bound_and_trust_root_verified(
    tmp_path: Path,
) -> None:
    key = _key_bytes()
    stage3 = _valid_stage3_archive(tmp_path, key)
    policy = _policy()
    output = tmp_path / "reproduction-package.zip"
    built = build_reproduction_package(
        stage3_package_path=stage3,
        policy=policy,
        output_path=output,
        private_key_pem=key,
        signing_identity="control-plane-domain-a",
        signing_key_id="kms-a-package",
        source_commit="commit",
    )
    untrusted = verify_reproduction_package(output)
    trusted = verify_reproduction_package(
        output,
        trusted_control_plane_keys={
            "kms-a-package": _public_key_pem(key)
        },
    )

    assert built["rf_e2_awarded"] is False
    assert untrusted["passed"] is True, untrusted
    assert untrusted["trusted"] is False
    assert trusted["trusted"] is True
    assert trusted["reproduction_ready"] is True
    assert trusted["evidence_grade"] == "RF-E1_package_verified"


def test_local_reproduce_runs_but_cannot_award_rf_e2(
    tmp_path: Path,
) -> None:
    key = _key_bytes()
    stage3 = _valid_stage3_archive(tmp_path, key)
    output = tmp_path / "reproduction-package.zip"
    build_reproduction_package(
        stage3_package_path=stage3,
        policy=_policy(),
        output_path=output,
        private_key_pem=key,
        signing_identity="control-plane-domain-a",
        signing_key_id="kms-a-package",
        source_commit="commit",
    )
    replay = tmp_path / "replay.py"
    replay.write_text(
        "import json,sys\n"
        "json.dump({"
        "'primary_metric':1.0,'effect':0.5,"
        "'interval':[0.5,0.5],'verdict':'supported',"
        "'sample_ids':['s1','s2'],'executed_run_cells':2,"
        "'qualified_run_cells':2},open(sys.argv[1],'w'))\n",
        encoding="utf-8",
    )
    report = tmp_path / "worker-report.json"
    result = run_local_development_reproduction(
        package_path=output,
        command=[sys.executable, str(replay), "{result_json}"],
        trusted_control_plane_keys={
            "kms-a-package": _public_key_pem(key)
        },
        requested_by="developer",
        report_output=report,
    )

    assert result["status"] == "development_validation_completed"
    assert result["rf_e2_awarded"] is False
    assert result["unsigned_worker_report"] is True
    assert read_json(report)["isolation"][
        "original_control_plane_accessible"
    ] is True
