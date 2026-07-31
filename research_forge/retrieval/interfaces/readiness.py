"""Evidence-backed readiness calculation for External Research V1."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ...models import utc_now
from ...storage import read_json
from ..domain.external_models import (
    CapabilityReadiness,
    CapabilityState,
    ExternalCapability,
    ProviderCredentialMode,
    ReadinessReport,
)
from ..domain.models import RetrievalStatus, retrieval_id
from ..domain.repository import RetrievalRepository
from ..providers.registry import ProviderRegistry


_CAPABILITY_PROVIDERS: dict[ExternalCapability, tuple[str, ...]] = {
    ExternalCapability.ACADEMIC_SEARCH: ("paper_search_mcp",),
    ExternalCapability.GITHUB_RESEARCH: ("github",),
    ExternalCapability.HUGGINGFACE_RESEARCH: ("huggingface",),
    ExternalCapability.PUBLIC_WEB: ("codex_native_web_search",),
    ExternalCapability.OPEN_ACCESS: ("open_access",),
    ExternalCapability.INSTITUTIONAL_ACCESS: ("institutional_access",),
    ExternalCapability.EVIDENCE_ANALYSIS: ("paperqa",),
}

_CREDENTIAL_MODES = {
    ExternalCapability.ACADEMIC_SEARCH: ProviderCredentialMode.NONE_REQUIRED,
    ExternalCapability.GITHUB_RESEARCH: ProviderCredentialMode.SERVER_MANAGED,
    ExternalCapability.HUGGINGFACE_RESEARCH: ProviderCredentialMode.NONE_REQUIRED,
    ExternalCapability.PUBLIC_WEB: ProviderCredentialMode.SERVER_MANAGED,
    ExternalCapability.OPEN_ACCESS: ProviderCredentialMode.NONE_REQUIRED,
    ExternalCapability.INSTITUTIONAL_ACCESS: ProviderCredentialMode.INSTITUTION_SESSION,
    ExternalCapability.EVIDENCE_ANALYSIS: ProviderCredentialMode.NONE_REQUIRED,
}

_ARTIFACT_HEALTH_REQUIRED = {
    ExternalCapability.OPEN_ACCESS,
    ExternalCapability.INSTITUTIONAL_ACCESS,
    ExternalCapability.EVIDENCE_ANALYSIS,
}


class ReadinessService:
    def __init__(
        self,
        repository: RetrievalRepository,
        providers: ProviderRegistry,
        *,
        test_evidence: dict[str, bool] | None = None,
    ) -> None:
        self.repository = repository
        self.providers = providers
        self._test_evidence_override = test_evidence

    def _test_evidence(self) -> dict[str, bool]:
        if self._test_evidence_override is not None:
            return self._test_evidence_override
        path = self.repository.root / "readiness-validation.json"
        if not path.is_file():
            return {}
        payload = read_json(path)
        return {
            str(key): bool(value)
            for key, value in payload.get("capability_tests", {}).items()
        }

    def _deployment_health(self) -> dict[str, bool]:
        path = self.repository.root / "readiness-validation.json"
        if not path.is_file():
            return {}
        payload = read_json(path)
        if not self._recent(str(payload.get("generated_at") or "")):
            return {}
        return {
            str(key): bool(value)
            for key, value in payload.get("capability_health", {}).items()
        }

    def _migration_ready(self) -> bool:
        schema = read_json(self.repository.root / "schema.json")
        migrations = set(schema.get("migrations", []))
        return int(schema.get("schema_version", 0)) >= 3 and {
            "003_external_research_v1",
            "004_retrieval_artifact_context",
        }.issubset(migrations)

    @staticmethod
    def _recent(timestamp: str | None) -> bool:
        if not timestamp:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        try:
            value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return False
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value >= cutoff

    def _recent_success(self, provider_id: str) -> bool:
        for run in self.repository.list_runs():
            if not self._recent(run.completed_at):
                continue
            if any(
                item.provider == provider_id
                and item.status is RetrievalStatus.SUCCEEDED
                and item.result_count > 0
                for item in run.provider_attempts
            ):
                return True
        return False

    def _recent_failure(self, provider_id: str) -> str | None:
        candidates = [
            item
            for item in self.repository.list_audit_events()
            if item.provider == provider_id
            and item.response_status == "failed"
            and self._recent(item.created_at)
        ]
        if not candidates:
            return None
        latest = max(candidates, key=lambda item: item.created_at)
        return latest.failure_reason or "recent policy-controlled Gateway call failed"

    def _artifact_producer(self, artifact_id: str) -> str | None:
        try:
            return self.repository.load_artifact(artifact_id).producer
        except (FileNotFoundError, ValueError):
            return None

    def _artifact_health(self, capability: ExternalCapability) -> bool:
        if capability is ExternalCapability.OPEN_ACCESS:
            return any(
                item.provider == "open_access"
                and item.content_level == "full_text"
                and item.access_mode == "open_access"
                and item.byte_size > 0
                and self._recent(item.retrieved_at)
                and self._artifact_producer(item.raw_response_artifact_id)
                == "provider:open_access"
                for item in self.repository.list_snapshots()
            )
        if capability is ExternalCapability.INSTITUTIONAL_ACCESS:
            return any(
                item.provider == "institutional_access"
                and item.content_level == "full_text"
                and item.access_mode == "institutional"
                and item.byte_size > 0
                and self._recent(item.retrieved_at)
                and self._artifact_producer(item.raw_response_artifact_id)
                == "institutional_user_handoff"
                for item in self.repository.list_snapshots()
            )
        if capability is ExternalCapability.EVIDENCE_ANALYSIS:
            for result in self.repository.list_evidence_results():
                if not result.evidence_spans or not self._recent(result.created_at):
                    continue
                corpus = self.repository.load_corpus(result.corpus_id)
                if (
                    corpus.status.value == "ready"
                    and corpus.index_hash == result.index_hash
                ):
                    return True
            return False
        return False

    def evaluate(self) -> ReadinessReport:
        checked_at = utc_now()
        test_evidence = self._test_evidence()
        deployment_health = self._deployment_health()
        migration_ready = self._migration_ready()
        results: list[CapabilityReadiness] = []
        for capability, candidates in _CAPABILITY_PROVIDERS.items():
            configured_ids = [
                provider for provider in candidates if provider in self.providers.ids()
            ]
            health_records: list[dict[str, Any]] = []
            for provider_id in configured_ids:
                try:
                    health_records.append(
                        self.providers.get(provider_id).health_check()
                    )
                except Exception as exc:
                    health_records.append(
                        {
                            "provider": provider_id,
                            "status": "unavailable",
                            "reason": type(exc).__name__,
                        }
                    )
                provider_run_success = self._recent_success(provider_id)
                provider_success = provider_run_success
                if capability in _ARTIFACT_HEALTH_REQUIRED:
                    # Resolver/session/index calls only prove that the adapter ran.
                    # These capabilities are READY solely after their required
                    # hash-bound acquisition or evidence artifact exists.
                    if provider_run_success:
                        health_records.append(
                            {
                                "provider": provider_id,
                                "status": "degraded",
                                "reason": (
                                    "provider operation succeeded; the required "
                                    "hash-bound acquisition/evidence artifact "
                                    "has not been verified"
                                ),
                            }
                        )
                    provider_success = False
                if provider_success:
                    health_records.append(
                        {
                            "provider": provider_id,
                            "status": "ready",
                            "reason": (
                                "successful policy-controlled Gateway run "
                                "recorded within seven days"
                            ),
                        }
                    )
                recent_failure = self._recent_failure(provider_id)
                if recent_failure and not provider_success:
                    health_records.append(
                        {
                            "provider": provider_id,
                            "status": "degraded",
                            "reason": f"recent Gateway failure: {recent_failure}",
                        }
                    )
            if self._artifact_health(capability):
                health_records.append(
                    {
                        "provider": capability.value,
                        "status": "ready",
                        "reason": (
                            "recent hash-bound acquisition/evidence artifact "
                            "satisfies this capability"
                        ),
                    }
                )
            if (
                capability not in _ARTIFACT_HEALTH_REQUIRED
                and deployment_health.get(capability.value)
            ):
                health_records.append(
                    {
                        "provider": capability.value,
                        "status": "ready",
                        "reason": "recent bounded deployment validation passed",
                    }
                )
            health_verified = any(
                item.get("status") == "ready" for item in health_records
            )
            configured = bool(configured_ids)
            tests_verified = bool(test_evidence.get(capability.value))
            if not configured:
                state = CapabilityState.NOT_CONFIGURED
            elif any(item.get("status") == "blocked" for item in health_records):
                state = CapabilityState.BLOCKED
            elif configured and health_verified and tests_verified and migration_ready:
                state = CapabilityState.READY
            elif all(item.get("status") == "unavailable" for item in health_records):
                state = CapabilityState.UNAVAILABLE
            else:
                state = CapabilityState.DEGRADED
            reasons = [
                f"{item.get('provider')}: {item.get('status')}"
                + (f" ({item.get('reason')})" if item.get("reason") else "")
                for item in health_records
            ]
            if not tests_verified:
                reasons.append("release test evidence is missing")
            if not migration_ready:
                reasons.append("External Research V1 migration is incomplete")
            results.append(
                CapabilityReadiness(
                    capability=capability,
                    state=state,
                    credential_mode=_CREDENTIAL_MODES[capability],
                    provider_ids=configured_ids,
                    configured=configured,
                    health_verified=health_verified,
                    tests_verified=tests_verified,
                    migration_ready=migration_ready,
                    reasons=reasons,
                    checked_at=checked_at,
                )
            )
        all_ready = all(item.state is CapabilityState.READY for item in results)
        public_ready = all(
            next(
                item.state
                for item in results
                if item.capability is capability
            )
            is CapabilityState.READY
            for capability in (
                ExternalCapability.ACADEMIC_SEARCH,
                ExternalCapability.GITHUB_RESEARCH,
                ExternalCapability.HUGGINGFACE_RESEARCH,
                ExternalCapability.PUBLIC_WEB,
                ExternalCapability.OPEN_ACCESS,
                ExternalCapability.EVIDENCE_ANALYSIS,
            )
        )
        aggregate = CapabilityReadiness(
            capability=ExternalCapability.EXTERNAL_RESEARCH_V1,
            state=(CapabilityState.READY if all_ready else CapabilityState.DEGRADED),
            credential_mode=ProviderCredentialMode.UNAVAILABLE,
            provider_ids=[],
            configured=all_ready,
            health_verified=all_ready,
            tests_verified=all_ready,
            migration_ready=migration_ready,
            reasons=(
                ["all component capabilities are READY"]
                if all_ready
                else ["one or more component capabilities are not READY"]
            ),
            checked_at=checked_at,
        )
        report = ReadinessReport(
            report_id=retrieval_id(
                "readiness",
                *[
                    f"{item.capability.value}:{item.state.value}"
                    for item in [*results, aggregate]
                ],
                checked_at,
            ),
            capabilities=[*results, aggregate],
            external_research_v1_ready=all_ready,
            public_research_loop_ready=public_ready,
            generated_at=checked_at,
        )
        return self.repository.save_readiness_report(report)


def dependency_present(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def executable_present(path: str | Path | None) -> bool:
    return bool(path and Path(path).is_file())
