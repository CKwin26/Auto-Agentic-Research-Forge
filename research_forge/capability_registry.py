from __future__ import annotations

"""Evidence-gated maturity registry for Research Forge product capabilities.

The upstream registry answers "what did we learn from or connect to?".  This
module answers the stricter product question: "what may we currently claim the
platform can do?".  A capability may not advance because code exists alone.
Each maturity level requires durable evidence for every preceding level.
"""

from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import Field, model_validator

from .models import StrictModel


class CapabilityMaturity(IntEnum):
    C0_CONCEPT = 0
    C1_IMPLEMENTED = 1
    C2_COMPONENT_VALIDATED = 2
    C3_CONTROLLED_E2E = 3
    C4_REAL_CASE_VALIDATED = 4
    C5_INDEPENDENTLY_VALIDATED = 5

    @property
    def label(self) -> str:
        return {
            0: "概念定义",
            1: "已有实现",
            2: "组件验证",
            3: "受控端到端",
            4: "真实案例验证",
            5: "独立验证",
        }[int(self)]


class CapabilityEvidenceLevel(IntEnum):
    """Strength of the best verification evidence, separate from implementation."""

    E0_DOCUMENTED = 0
    E1_COMPONENT_TESTED = 1
    E2_CONTROLLED_REPLAY = 2
    E3_REAL_CASE = 3
    E4_EXTERNAL_INDEPENDENT = 4


class CapabilityScope(StrEnum):
    COMPONENT = "component"
    BOUNDED = "bounded"
    LIMITED_REAL_CASE = "limited_real_case"
    EXTENSION_ONLY = "extension_only"


EVIDENCE_ORDER = (
    "design",
    "source",
    "component_validation",
    "controlled_e2e",
    "real_case",
    "independent_validation",
)


class CapabilityEvidence(StrictModel):
    design: list[str] = Field(default_factory=list)
    source: list[str] = Field(default_factory=list)
    component_validation: list[str] = Field(default_factory=list)
    controlled_e2e: list[str] = Field(default_factory=list)
    real_case: list[str] = Field(default_factory=list)
    independent_validation: list[str] = Field(default_factory=list)

    def locators_for(self, level: CapabilityMaturity) -> list[str]:
        return list(getattr(self, EVIDENCE_ORDER[int(level)]))


class CapabilityManifest(StrictModel):
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    title: str
    category: str
    claim: str
    claim_boundary: str
    declared_maturity: CapabilityMaturity
    evidence_level: CapabilityEvidenceLevel | None = None
    scope: CapabilityScope = CapabilityScope.BOUNDED
    supported_profiles: list[str] = Field(default_factory=list)
    entrypoint: str
    inputs: list[str]
    outputs: list[str]
    validators: list[str]
    positive_cases: list[str]
    negative_cases: list[str]
    upstream_sources: list[str] = Field(default_factory=list)
    evidence: CapabilityEvidence

    @model_validator(mode="after")
    def manifest_is_operationally_described(self) -> "CapabilityManifest":
        if not self.inputs or not self.outputs or not self.validators:
            raise ValueError("capability inputs, outputs and validators are required")
        if not self.positive_cases or not self.negative_cases:
            raise ValueError(
                "every capability requires positive and negative acceptance cases"
            )
        if not self.claim_boundary.strip():
            raise ValueError("every capability needs an explicit claim boundary")
        if self.evidence_level is None:
            derived = max(0, min(4, int(self.declared_maturity) - 1))
            self.evidence_level = CapabilityEvidenceLevel(derived)
        return self


class CapabilityAuditItem(StrictModel):
    capability_id: str
    declared_maturity: CapabilityMaturity
    evidence_level: CapabilityEvidenceLevel
    scope: CapabilityScope
    evidenced_maturity: CapabilityMaturity | None
    checked_evidence: list[str]
    missing_evidence: list[str]
    maturity_supported: bool
    permitted_claim: str


class CapabilityAuditReport(StrictModel):
    schema_version: int = 1
    repository_root: str
    manifest_count: int
    supported_count: int
    unsupported_count: int
    items: list[CapabilityAuditItem]


def default_registry_path() -> Path:
    return Path(__file__).with_name("capability_registry.yaml")


def load_capability_registry(path: str | Path | None = None) -> list[CapabilityManifest]:
    registry_path = Path(path) if path else default_registry_path()
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if payload.get("include"):
        included = registry_path.with_name(str(payload["include"]))
        payload = yaml.safe_load(included.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported capability manifest schema")
    manifests = [CapabilityManifest.model_validate(item) for item in payload["capabilities"]]
    ids = [item.capability_id for item in manifests]
    if len(ids) != len(set(ids)):
        raise ValueError("capability_id values must be unique")
    return manifests


def _resolve_locator(root: Path, locator: str) -> Path | None:
    if "://" in locator:
        return None
    return root / locator.split("#", 1)[0]


def evidenced_maturity(
    manifest: CapabilityManifest,
    root: Path,
) -> tuple[CapabilityMaturity | None, list[str], list[str]]:
    checked: list[str] = []
    missing: list[str] = []
    achieved: CapabilityMaturity | None = None
    for value in range(0, 6):
        level = CapabilityMaturity(value)
        locators = manifest.evidence.locators_for(level)
        if not locators:
            break
        level_valid = True
        for locator in locators:
            resolved = _resolve_locator(root, locator)
            if resolved is None:
                missing.append(f"{EVIDENCE_ORDER[value]}:{locator}:not-local-durable-evidence")
                level_valid = False
            elif resolved.exists():
                checked.append(locator)
            else:
                missing.append(f"{EVIDENCE_ORDER[value]}:{locator}")
                level_valid = False
        if not level_valid:
            break
        achieved = level
    return achieved, checked, missing


def audit_capability_registry(
    root: str | Path,
    manifests: Iterable[CapabilityManifest] | None = None,
) -> CapabilityAuditReport:
    repository_root = Path(root).resolve()
    records = list(manifests or load_capability_registry())
    items: list[CapabilityAuditItem] = []
    for manifest in records:
        achieved, checked, missing = evidenced_maturity(manifest, repository_root)
        supported = achieved is not None and achieved >= manifest.declared_maturity
        items.append(
            CapabilityAuditItem(
                capability_id=manifest.capability_id,
                declared_maturity=manifest.declared_maturity,
                evidence_level=manifest.evidence_level,
                scope=manifest.scope,
                evidenced_maturity=achieved,
                checked_evidence=checked,
                missing_evidence=missing,
                maturity_supported=supported,
                permitted_claim=(manifest.claim if supported else manifest.claim_boundary),
            )
        )
    supported_count = sum(item.maturity_supported for item in items)
    return CapabilityAuditReport(
        repository_root=str(repository_root),
        manifest_count=len(items),
        supported_count=supported_count,
        unsupported_count=len(items) - supported_count,
        items=items,
    )


def capability_map(
    manifests: Iterable[CapabilityManifest] | None = None,
) -> dict[str, CapabilityManifest]:
    records = list(manifests or load_capability_registry())
    return {record.capability_id: record for record in records}
