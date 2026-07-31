from __future__ import annotations

"""Specification and audit helpers for the Research Forge capability suite.

The suite has twelve core scientific cases plus two clean-room replay
extensions.  Merely listing a case never counts as a pass: a case becomes
validated only when its durable test or run evidence is present and the status
is advanced by the benchmark runner.
"""

from enum import Enum
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from .models import StrictModel


class BenchmarkCategory(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    VERDICT = "verdict"
    CLEAN_ROOM = "clean_room"


class BenchmarkImplementationStatus(str, Enum):
    SPECIFIED = "specified"
    COMPONENT_TESTED = "component_tested"
    CONTROLLED_E2E = "controlled_e2e"
    REAL_CASE_VALIDATED = "real_case_validated"
    INDEPENDENTLY_VALIDATED = "independently_validated"


class CapabilityBenchmarkCase(StrictModel):
    case_id: str = Field(pattern=r"^capbench-[a-z0-9-]+$")
    category: BenchmarkCategory
    title: str
    profile: str
    expected_outcome: str
    status: BenchmarkImplementationStatus
    evidence: list[str] = Field(default_factory=list)
    notes: str

    @model_validator(mode="after")
    def evidence_required_after_specification(self) -> "CapabilityBenchmarkCase":
        if self.status is not BenchmarkImplementationStatus.SPECIFIED and not self.evidence:
            raise ValueError("validated benchmark cases require durable evidence")
        return self


def default_benchmark_path() -> Path:
    return Path(__file__).with_name("capability_benchmark.yaml")


def load_capability_benchmark(
    path: str | Path | None = None,
) -> list[CapabilityBenchmarkCase]:
    source = Path(path) if path else default_benchmark_path()
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported capability benchmark schema")
    cases = [
        CapabilityBenchmarkCase.model_validate(item)
        for item in payload["cases"]
    ]
    ids = [item.case_id for item in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("capability benchmark case IDs must be unique")
    return cases


def benchmark_evidence_gaps(
    root: str | Path,
    cases: list[CapabilityBenchmarkCase] | None = None,
) -> dict[str, list[str]]:
    base = Path(root)
    gaps: dict[str, list[str]] = {}
    for case in cases or load_capability_benchmark():
        missing = [
            locator
            for locator in case.evidence
            if not (base / locator.split("#", 1)[0]).exists()
        ]
        if missing:
            gaps[case.case_id] = missing
    return gaps
