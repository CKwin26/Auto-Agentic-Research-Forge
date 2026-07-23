from __future__ import annotations

"""Publication workflow adapter boundary.

The core registry owns adapter identity, frozen configuration, and action
dispatch.  Case-specific modules remain behind an adapter and are imported
only when that adapter executes an action.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from .models import MacroStage, StrictModel, utc_now
from .storage import read_json, write_json_atomic


PUBLICATION_ADAPTER_FILENAME = "publication_adapter.json"
RESEARCH_AGENT_EVIDENCE_ADAPTER_ID = "research-agent-evidence-publication-v1"


class PublicationAdapterBinding(StrictModel):
    schema_version: int = 1
    adapter_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,99}$")
    adapter_version: str = Field(min_length=1, max_length=40)
    settings: dict[str, Any] = Field(default_factory=dict)
    configured_at: str = Field(default_factory=utc_now)


@dataclass(frozen=True)
class PublicationActionSpec:
    action: str
    stage: MacroStage
    resumable: bool
    description: str


class PublicationAdapter(Protocol):
    adapter_id: str
    version: str

    def actions(self) -> tuple[PublicationActionSpec, ...]: ...

    def execute(
        self,
        project: Path,
        action: str,
        parameters: dict[str, Any],
        binding: PublicationAdapterBinding,
    ) -> dict[str, Any]: ...


class PublicationAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, PublicationAdapter] = {}

    def register(self, adapter: PublicationAdapter) -> None:
        if adapter.adapter_id in self._adapters:
            raise ValueError(f"publication adapter already registered: {adapter.adapter_id}")
        self._adapters[adapter.adapter_id] = adapter

    def get(self, adapter_id: str) -> PublicationAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise ValueError(f"unknown publication adapter: {adapter_id}") from exc

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "adapter_id": adapter.adapter_id,
                "version": adapter.version,
                "actions": [item.action for item in adapter.actions()],
            }
            for adapter in sorted(self._adapters.values(), key=lambda item: item.adapter_id)
        ]


class ResearchAgentEvidencePublicationAdapter:
    """Golden-case adapter retained for compatibility, not a core policy."""

    adapter_id = RESEARCH_AGENT_EVIDENCE_ADAPTER_ID
    version = "1"

    _ACTIONS = (
        PublicationActionSpec("evaluate", MacroStage.EXPERIMENTATION, False, "Run the frozen NLI evaluation."),
        PublicationActionSpec("audit-pairs", MacroStage.EXPERIMENTATION, True, "Audit protected shared-artifact pairs."),
        PublicationActionSpec("manual-prepare", MacroStage.EXPERIMENTATION, True, "Prepare blinded human-audit packets."),
        PublicationActionSpec("manual-submit", MacroStage.EXPERIMENTATION, False, "Freeze two completed auditor packets."),
        PublicationActionSpec("manual-finalize", MacroStage.EXPERIMENTATION, False, "Adjudicate and close the human gate."),
        PublicationActionSpec("manual-audit", MacroStage.EXPERIMENTATION, True, "Audit the human-review chain."),
        PublicationActionSpec("manual-import-workbooks", MacroStage.EXPERIMENTATION, False, "Import signed audit workbooks."),
        PublicationActionSpec("context-record", MacroStage.EXPERIMENTATION, False, "Append a context-restored review."),
        PublicationActionSpec("context-audit", MacroStage.EXPERIMENTATION, True, "Audit context-restored review records."),
        PublicationActionSpec("context-verify-repair", MacroStage.EXPERIMENTATION, True, "Replay diagnosed evaluator repairs."),
        PublicationActionSpec("successor-prepare", MacroStage.PROTOCOL, False, "Freeze a prospective successor plan."),
        PublicationActionSpec("successor-audit", MacroStage.PROTOCOL, True, "Audit successor non-reuse boundaries."),
        PublicationActionSpec("synthesize", MacroStage.SYNTHESIS, True, "Create the evidence-bound publication manuscript."),
        PublicationActionSpec("audit-synthesis", MacroStage.SYNTHESIS, True, "Audit publication synthesis artifacts."),
        PublicationActionSpec("render-layout", MacroStage.SYNTHESIS, True, "Run the readiness-bound layout gate."),
        PublicationActionSpec("supplement-prepare", MacroStage.SYNTHESIS, True, "Prepare an anonymous supplement."),
        PublicationActionSpec("supplement-audit", MacroStage.SYNTHESIS, True, "Audit the anonymous supplement."),
        PublicationActionSpec("review-package-prepare", MacroStage.SYNTHESIS, True, "Prepare a review submission package."),
        PublicationActionSpec("review-package-audit", MacroStage.SYNTHESIS, True, "Audit the review submission package."),
    )

    def actions(self) -> tuple[PublicationActionSpec, ...]:
        return self._ACTIONS

    def execute(
        self,
        project: Path,
        action: str,
        parameters: dict[str, Any],
        binding: PublicationAdapterBinding,
    ) -> dict[str, Any]:
        del binding  # The v1 case stores its frozen parameters inside the project.
        supported = {item.action for item in self._ACTIONS}
        if action not in supported:
            raise ValueError(f"adapter {self.adapter_id} does not support action: {action}")

        if action == "evaluate":
            from .publication_nli_evaluation import run_publication_nli_evaluation

            value = run_publication_nli_evaluation(project)
        elif action == "audit-pairs":
            from .publication_pair_audit import audit_publication_pairs

            value = audit_publication_pairs(project, persist=True)
        elif action == "manual-prepare":
            from .publication_manual_audit import prepare_publication_manual_audit_packets

            value = prepare_publication_manual_audit_packets(project)
        elif action == "manual-submit":
            from .publication_manual_audit import submit_publication_manual_audits

            value = submit_publication_manual_audits(
                project, Path(parameters["auditor_1"]), Path(parameters["auditor_2"])
            )
        elif action == "manual-finalize":
            from .publication_manual_audit import finalize_publication_manual_audit

            adjudication = parameters.get("adjudication")
            value = finalize_publication_manual_audit(
                project, Path(adjudication) if adjudication else None
            )
        elif action == "manual-audit":
            from .publication_manual_audit import audit_publication_manual_audit

            value = audit_publication_manual_audit(project)
        elif action == "manual-import-workbooks":
            from .publication_audit_workbooks import import_publication_manual_audit_workbooks

            value = import_publication_manual_audit_workbooks(
                project,
                Path(parameters["auditor_1_xlsx"]),
                Path(parameters["auditor_2_xlsx"]),
                Path(parameters["supplement_xlsx"]),
            )
        elif action == "context-record":
            from .publication_context_review import record_publication_context_review

            value = record_publication_context_review(
                project,
                verdicts=str(parameters["verdicts"]),
                reviewer_id=str(parameters["reviewer_id"]),
            )
        elif action == "context-audit":
            from .publication_context_review import audit_publication_context_review

            value = audit_publication_context_review(project)
        elif action == "context-verify-repair":
            from .publication_context_review import verify_publication_context_repair

            value = verify_publication_context_repair(project)
        elif action == "successor-prepare":
            from .publication_context_review import prepare_publication_successor_protocol_plan

            value = prepare_publication_successor_protocol_plan(project)
        elif action == "successor-audit":
            from .publication_context_review import audit_publication_successor_protocol_plan

            value = audit_publication_successor_protocol_plan(project)
        elif action == "synthesize":
            from .publication_synthesis import synthesize_publication_study

            value = synthesize_publication_study(project)
        elif action == "audit-synthesis":
            from .publication_synthesis import audit_publication_synthesis

            value = audit_publication_synthesis(project, persist=True)
        elif action == "render-layout":
            from .publication_synthesis import render_publication_layout

            value = render_publication_layout(project, Path(parameters["readiness"]))
        elif action == "supplement-prepare":
            from .publication_supplement import prepare_anonymous_supplement

            value = prepare_anonymous_supplement(project)
        elif action == "supplement-audit":
            from .publication_supplement import audit_anonymous_supplement

            value = audit_anonymous_supplement(project)
        elif action == "review-package-prepare":
            from .publication_package import prepare_review_submission_package

            value = prepare_review_submission_package(project, Path(parameters["readiness"]))
        else:
            from .publication_package import audit_review_submission_package

            value = audit_review_submission_package(project)
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if not isinstance(value, dict):
            return {"value": value}
        return value


def default_publication_registry() -> PublicationAdapterRegistry:
    registry = PublicationAdapterRegistry()
    registry.register(ResearchAgentEvidencePublicationAdapter())
    return registry


def freeze_publication_adapter(
    project: str | Path,
    adapter_id: str,
    *,
    settings: dict[str, Any] | None = None,
    registry: PublicationAdapterRegistry | None = None,
) -> PublicationAdapterBinding:
    root = Path(project).expanduser().resolve()
    adapter = (registry or default_publication_registry()).get(adapter_id)
    binding = PublicationAdapterBinding(
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.version,
        settings=settings or {},
    )
    path = root / PUBLICATION_ADAPTER_FILENAME
    if path.is_file():
        existing = PublicationAdapterBinding.model_validate(read_json(path))
        if existing.adapter_id != binding.adapter_id or existing.adapter_version != binding.adapter_version:
            raise ValueError("publication adapter is frozen; create a new project revision to change it")
        return existing
    write_json_atomic(path, binding)
    return binding


def load_publication_adapter_binding(
    project: str | Path,
    *,
    legacy_default: bool = True,
    registry: PublicationAdapterRegistry | None = None,
) -> PublicationAdapterBinding:
    root = Path(project).expanduser().resolve()
    path = root / PUBLICATION_ADAPTER_FILENAME
    if path.is_file():
        return PublicationAdapterBinding.model_validate(read_json(path))
    if not legacy_default:
        raise FileNotFoundError(f"publication adapter binding not found: {path}")
    adapter = (registry or default_publication_registry()).get(
        RESEARCH_AGENT_EVIDENCE_ADAPTER_ID
    )
    return PublicationAdapterBinding(
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.version,
        settings={"compatibility_mode": "implicit_legacy_binding"},
    )


def execute_publication_action(
    project: str | Path,
    action: str,
    *,
    parameters: dict[str, Any] | None = None,
    adapter_id: str | None = None,
    registry: PublicationAdapterRegistry | None = None,
) -> dict[str, Any]:
    root = Path(project).expanduser().resolve()
    active_registry = registry or default_publication_registry()
    binding = (
        freeze_publication_adapter(root, adapter_id, registry=active_registry)
        if adapter_id
        else load_publication_adapter_binding(root, registry=active_registry)
    )
    adapter = active_registry.get(binding.adapter_id)
    if adapter.version != binding.adapter_version:
        raise ValueError(
            f"publication adapter version mismatch: frozen={binding.adapter_version}, installed={adapter.version}"
        )
    return adapter.execute(root, action, parameters or {}, binding)
