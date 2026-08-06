from __future__ import annotations

"""Layered capability assurance beyond evidence-path existence checks."""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
from typing import Literal

import yaml
from pydantic import Field, model_validator

from .capability_registry import (
    CapabilityEvidenceLevel,
    CapabilityScope,
    audit_capability_registry,
    capability_map,
)
from .models import StrictModel


class VerificationContext(StrictModel):
    last_verified_at: datetime
    verified_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    environment: str
    environment_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    run_url: str
    last_replay_status: Literal["passed", "failed"]
    evidence_expires_at: datetime


class CapabilityVerificationRecord(StrictModel):
    capability_id: str
    evidence_level: CapabilityEvidenceLevel
    scope: CapabilityScope
    positive_tests: list[str]
    negative_tests: list[str]
    mutation_tests: list[str]
    replay_evidence: list[str] = Field(default_factory=list)
    real_case_evidence: list[str] = Field(default_factory=list)
    external_receipt: str | None = None

    @model_validator(mode="after")
    def required_falsification_inputs_exist(self) -> "CapabilityVerificationRecord":
        if not self.positive_tests or not self.negative_tests or not self.mutation_tests:
            raise ValueError(
                "every registered capability needs positive, negative, and mutation tests"
            )
        for selector in self.positive_tests + self.negative_tests + self.mutation_tests:
            if "::test_" not in selector:
                raise ValueError(
                    "falsification evidence must identify a concrete pytest node"
                )
        if self.evidence_level >= CapabilityEvidenceLevel.E2_CONTROLLED_REPLAY:
            if not self.replay_evidence:
                raise ValueError("E2+ capability needs replay evidence")
        if self.evidence_level >= CapabilityEvidenceLevel.E3_REAL_CASE:
            if not self.real_case_evidence:
                raise ValueError("E3+ capability needs real-case evidence")
        if self.evidence_level >= CapabilityEvidenceLevel.E4_EXTERNAL_INDEPENDENT:
            if not self.external_receipt:
                raise ValueError("E4 capability needs an external receipt")
        return self


class ProductFeatureMapping(StrictModel):
    number: int = Field(ge=1)
    title: str
    capability_ids: list[str]
    mapping_role: Literal["direct", "composite", "presentation_layer"]


class CapabilityLayerResult(StrictModel):
    capability_id: str
    registry_valid: bool
    evidence_fresh: bool
    replay_status: Literal["passed", "verified_pass", "failed", "not_executed"]
    evidence_level: CapabilityEvidenceLevel
    scope: CapabilityScope
    changed_since_verification: list[str] = Field(default_factory=list)
    missing_locators: list[str] = Field(default_factory=list)


class CapabilityAssuranceReport(StrictModel):
    schema_version: int = 1
    verified_commit: str
    verification_run: str
    registry_coverage: str
    product_feature_mapping: str
    evidence_freshness: str
    replay_passing: str
    real_case_validated: str
    release_evidence_ceiling: Literal["E3_real_case"] = "E3_real_case"
    external_validation_required: bool = False
    replay_executed: bool
    items: list[CapabilityLayerResult]


def _load_yaml(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError(f"unsupported schema in {path}")
    return payload


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def _locator_paths(record: CapabilityVerificationRecord) -> list[str]:
    return list(
        dict.fromkeys(
            record.positive_tests
            + record.negative_tests
            + record.mutation_tests
            + record.replay_evidence
            + record.real_case_evidence
            + ([record.external_receipt] if record.external_receipt else [])
        )
    )


def _selector_path(locator: str) -> str:
    return locator.split("::", 1)[0]


def _run_capability_replay(
    repository_root: Path, selectors: list[str]
) -> int:
    replay = subprocess.run(
        ["python", "-m", "pytest", "-q", *selectors],
        cwd=repository_root,
        check=False,
    )
    return replay.returncode


def audit_capability_assurance(
    root: str | Path,
    *,
    execute_replay: bool = False,
    now: datetime | None = None,
) -> CapabilityAssuranceReport:
    repository_root = Path(root).resolve()
    manifests = capability_map()
    registry_report = audit_capability_registry(repository_root, manifests.values())

    verification_payload = _load_yaml(
        repository_root / "research_forge" / "capability_verification.yaml"
    )
    context = VerificationContext.model_validate(
        verification_payload["verification_context"]
    )
    records = [
        CapabilityVerificationRecord.model_validate(item)
        for item in verification_payload["capabilities"]
    ]
    record_map = {item.capability_id: item for item in records}
    if len(record_map) != len(records):
        raise ValueError("duplicate capability verification record")
    if set(record_map) != set(manifests):
        missing = sorted(set(manifests) - set(record_map))
        extra = sorted(set(record_map) - set(manifests))
        raise ValueError(f"verification coverage mismatch; missing={missing}; extra={extra}")

    mapping_payload = _load_yaml(
        repository_root / "research_forge" / "product_capability_map.yaml"
    )
    features = [ProductFeatureMapping.model_validate(item) for item in mapping_payload["features"]]
    numbers = [item.number for item in features]
    if numbers != list(range(1, mapping_payload["document_feature_count"] + 1)):
        raise ValueError("product feature mapping must be consecutively numbered")
    mapped_ids = {capability_id for item in features for capability_id in item.capability_ids}
    if mapped_ids != set(manifests):
        raise ValueError("22-item product catalog does not map exactly onto registry IDs")

    ancestor = _git(
        repository_root,
        "merge-base",
        "--is-ancestor",
        context.verified_commit,
        "HEAD",
    )
    if ancestor.returncode != 0:
        raise ValueError("verified commit is not an ancestor of the audited checkout")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    not_expired = current <= context.evidence_expires_at

    replay_returncodes: dict[str, int] = {}
    if execute_replay:
        for record in records:
            selectors = sorted(
                set(
                    record.positive_tests
                    + record.negative_tests
                    + record.mutation_tests
                )
            )
            replay_returncodes[record.capability_id] = (
                _run_capability_replay(repository_root, selectors)
            )

    replay_all_passed = bool(replay_returncodes) and all(
        returncode == 0 for returncode in replay_returncodes.values()
    )

    current_head = _git(repository_root, "rev-parse", "HEAD").stdout.strip()
    effective_verified_commit = (
        current_head
        if execute_replay and replay_all_passed
        else context.verified_commit
    )
    effective_verification_run = context.run_url
    if execute_replay and replay_all_passed:
        server = os.environ.get("GITHUB_SERVER_URL")
        repository = os.environ.get("GITHUB_REPOSITORY")
        run_id = os.environ.get("GITHUB_RUN_ID")
        effective_verification_run = (
            f"{server}/{repository}/actions/runs/{run_id}"
            if server and repository and run_id
            else "local://capability-replay"
        )

    registry_by_id = {item.capability_id: item for item in registry_report.items}
    items: list[CapabilityLayerResult] = []
    for capability_id, record in record_map.items():
        replay_returncode = replay_returncodes.get(capability_id)
        manifest = manifests[capability_id]
        if record.evidence_level != manifest.evidence_level:
            raise ValueError(f"evidence level mismatch for {capability_id}")
        if record.scope != manifest.scope:
            raise ValueError(f"scope mismatch for {capability_id}")

        manifest_locators = [
            locator
            for evidence_kind in (
                "design",
                "source",
                "component_validation",
                "controlled_e2e",
                "real_case",
                "independent_validation",
            )
            for locator in getattr(manifest.evidence, evidence_kind)
            if "://" not in locator
        ]
        locators = list(
            dict.fromkeys(
                [_selector_path(item) for item in _locator_paths(record)]
                + manifest_locators
            )
        )
        missing = [path for path in locators if not (repository_root / path).exists()]
        diff = _git(
            repository_root,
            "diff",
            "--name-only",
            f"{context.verified_commit}..HEAD",
            "--",
            *locators,
        )
        changed = [line for line in diff.stdout.splitlines() if line.strip()]
        fresh = (
            not missing
            and (
                replay_returncode == 0
                if execute_replay
                else not changed and not_expired
            )
        )
        replay_status: Literal["passed", "verified_pass", "failed", "not_executed"]
        if replay_returncode is None:
            replay_status = (
                "verified_pass"
                if fresh and context.last_replay_status == "passed"
                else "not_executed"
            )
        else:
            replay_status = "passed" if replay_returncode == 0 else "failed"
        items.append(
            CapabilityLayerResult(
                capability_id=capability_id,
                registry_valid=registry_by_id[capability_id].maturity_supported,
                evidence_fresh=fresh,
                replay_status=replay_status,
                evidence_level=record.evidence_level,
                scope=record.scope,
                changed_since_verification=changed,
                missing_locators=missing,
            )
        )

    registry_count = sum(item.registry_valid for item in items)
    freshness_count = sum(item.evidence_fresh for item in items)
    replay_count = sum(item.replay_status in {"passed", "verified_pass"} for item in items)
    real_count = sum(
        item.evidence_level >= CapabilityEvidenceLevel.E3_REAL_CASE for item in items
    )
    denominator = len(items)
    return CapabilityAssuranceReport(
        verified_commit=effective_verified_commit,
        verification_run=effective_verification_run,
        registry_coverage=f"{registry_count}/{denominator}",
        product_feature_mapping=f"{len(features)}/{mapping_payload['document_feature_count']}",
        evidence_freshness=f"{freshness_count}/{denominator}",
        replay_passing=f"{replay_count}/{denominator}",
        real_case_validated=f"{real_count}/{denominator}",
        replay_executed=execute_replay,
        items=items,
    )
