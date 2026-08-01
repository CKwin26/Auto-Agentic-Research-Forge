from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import research_forge.capability_assurance as assurance_module
from research_forge.capability_assurance import audit_capability_assurance


ROOT = Path(__file__).resolve().parents[1]


def test_product_catalog_maps_22_features_to_all_18_registry_capabilities() -> None:
    report = audit_capability_assurance(
        ROOT,
        now=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )

    assert report.product_feature_mapping == "22/22"
    assert report.registry_coverage == "18/18"
    assert len(report.items) == 18


def test_layered_audit_reports_fresh_replay_and_real_case_counts() -> None:
    report = audit_capability_assurance(
        ROOT,
        now=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )

    assert report.evidence_freshness == "18/18"
    assert report.replay_passing == "18/18"
    assert report.real_case_validated == "6/18"
    assert report.release_evidence_ceiling == "E3_real_case"
    assert report.external_validation_required is False
    assert not report.replay_executed
    assert all(item.replay_status == "verified_pass" for item in report.items)


def test_expired_evidence_is_downgraded_instead_of_kept_green() -> None:
    report = audit_capability_assurance(
        ROOT,
        now=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )

    assert report.evidence_freshness == "0/18"
    assert report.replay_passing == "0/18"
    assert all(not item.evidence_fresh for item in report.items)
    assert all(item.replay_status == "not_executed" for item in report.items)


def test_replay_failure_is_attributed_only_to_its_capability(monkeypatch) -> None:
    def fake_replay(_root: Path, selectors: list[str]) -> int:
        return int(any("test_project_replays_twice" in item for item in selectors))

    monkeypatch.setattr(
        assurance_module, "_run_capability_replay", fake_replay
    )
    report = audit_capability_assurance(ROOT, execute_replay=True)

    failed = [
        item.capability_id
        for item in report.items
        if item.replay_status == "failed"
    ]
    assert failed == ["reproduction.sealed_existing_project_replay"]
    assert report.replay_passing == "17/18"
