from __future__ import annotations

from pathlib import Path

import yaml

from research_forge.profiles.registry import PROFILE_BUNDLE_REGISTRY
from research_forge.workflow_domain import Stage3Profile


ROOT = Path(__file__).resolve().parents[1]


def test_profile_matrix_covers_every_enum_without_upgrading_extensions() -> None:
    payload = yaml.safe_load(
        (ROOT / "research_forge" / "profile_capability_matrix.yaml").read_text(
            encoding="utf-8"
        )
    )
    rows = {item["profile_id"]: item for item in payload["profiles"]}

    assert set(rows) == {item.value for item in Stage3Profile}
    for profile, bundle in PROFILE_BUNDLE_REGISTRY.items():
        row = rows[profile.value]
        assert row["registry_status"] == bundle.certification_status.value
        assert row["formal_execution"] is bundle.formal_execution_supported()
    for profile in set(Stage3Profile).difference(PROFILE_BUNDLE_REGISTRY):
        assert rows[profile.value]["registry_status"] == "unregistered"
        assert rows[profile.value]["formal_execution"] is False

    extensions = {item["extension_id"]: item for item in payload["extensions"]}
    assert extensions["finance_backtest"]["kind"] == "domain_adapter"
    assert extensions["finance_backtest"]["formal_profile"] is False
    assert extensions["survey"]["formal_profile"] is False
