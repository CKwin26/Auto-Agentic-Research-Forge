from __future__ import annotations

import pytest

from research_forge.models import MacroStage
from research_forge.publication_adapters import (
    PUBLICATION_ADAPTER_FILENAME,
    PublicationActionSpec,
    PublicationAdapterBinding,
    PublicationAdapterRegistry,
    execute_publication_action,
    freeze_publication_adapter,
)


class FixtureAdapter:
    adapter_id = "fixture-publication-v1"
    version = "7"

    def actions(self):
        return (
            PublicationActionSpec(
                "audit", MacroStage.SYNTHESIS, True, "Fixture audit action."
            ),
        )

    def execute(self, project, action, parameters, binding):
        assert isinstance(binding, PublicationAdapterBinding)
        return {"project": str(project), "action": action, "parameters": parameters}


def _registry() -> PublicationAdapterRegistry:
    registry = PublicationAdapterRegistry()
    registry.register(FixtureAdapter())
    return registry


def test_publication_adapter_is_frozen_as_project_data(tmp_path) -> None:
    binding = freeze_publication_adapter(
        tmp_path,
        "fixture-publication-v1",
        settings={"venue": "fixture"},
        registry=_registry(),
    )

    assert binding.adapter_version == "7"
    assert (tmp_path / PUBLICATION_ADAPTER_FILENAME).is_file()
    result = execute_publication_action(
        tmp_path,
        "audit",
        parameters={"strict": True},
        registry=_registry(),
    )
    assert result["action"] == "audit"
    assert result["parameters"] == {"strict": True}


def test_frozen_adapter_cannot_be_replaced_in_place(tmp_path) -> None:
    registry = _registry()
    freeze_publication_adapter(tmp_path, "fixture-publication-v1", registry=registry)

    class OtherAdapter(FixtureAdapter):
        adapter_id = "other-publication-v1"

    registry.register(OtherAdapter())
    with pytest.raises(ValueError, match="frozen"):
        freeze_publication_adapter(tmp_path, "other-publication-v1", registry=registry)


def test_registry_rejects_duplicate_adapter_ids() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="already registered"):
        registry.register(FixtureAdapter())
