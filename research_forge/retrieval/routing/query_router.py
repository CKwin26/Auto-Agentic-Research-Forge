"""Deterministic provider routing for External Research V1."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from ...models import StrictModel, utc_now
from ..domain.external_models import SearchFreshness
from ..domain.models import RetrievalPhase, retrieval_id


class QueryIntent(StrEnum):
    PUBLICATION = "publication"
    CODE = "code"
    MODEL = "model"
    DATASET = "dataset"
    OFFICIAL_WEB = "official_web"
    OPEN_FULL_TEXT = "open_full_text"
    SUBSCRIPTION_FULL_TEXT = "subscription_full_text"
    FULL_TEXT_EVIDENCE = "full_text_evidence"


class RouteDecision(StrictModel):
    route_id: str
    study_id: str
    phase: RetrievalPhase
    purpose: str
    intent: QueryIntent
    providers: list[str]
    freshness: SearchFreshness
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class QueryRouter:
    _ROUTES = {
        QueryIntent.PUBLICATION: ["paper_search_mcp"],
        QueryIntent.CODE: ["github"],
        QueryIntent.MODEL: ["huggingface"],
        QueryIntent.DATASET: ["huggingface"],
        QueryIntent.OFFICIAL_WEB: ["codex_native_web_search"],
        QueryIntent.OPEN_FULL_TEXT: ["open_access"],
        QueryIntent.SUBSCRIPTION_FULL_TEXT: ["institutional_access"],
        QueryIntent.FULL_TEXT_EVIDENCE: ["paperqa"],
    }

    def route(
        self,
        *,
        study_id: str,
        phase: RetrievalPhase,
        purpose: str,
        intent: QueryIntent,
        freshness: SearchFreshness = SearchFreshness.CACHE_ONLY,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
    ) -> RouteDecision:
        if (
            phase is RetrievalPhase.EXPERIMENTATION
            and intent
            in {
                QueryIntent.PUBLICATION,
                QueryIntent.MODEL,
                QueryIntent.DATASET,
            }
            and purpose
            in {
                "metric_grounding",
                "baseline_discovery",
                "novelty_check",
            }
        ):
            raise ValueError(
                "open result-directed search is forbidden in experimentation"
            )
        providers = list(self._ROUTES[intent])
        return RouteDecision(
            route_id=retrieval_id(
                "provider-route",
                study_id,
                phase.value,
                purpose,
                intent.value,
                freshness.value,
                *(allowed_domains or []),
                *(blocked_domains or []),
            ),
            study_id=study_id,
            phase=phase,
            purpose=purpose,
            intent=intent,
            providers=providers,
            freshness=freshness,
            allowed_domains=sorted(set(allowed_domains or [])),
            blocked_domains=sorted(set(blocked_domains or [])),
            reasons=["deterministic intent-to-provider routing"],
        )
