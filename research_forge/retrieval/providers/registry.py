from __future__ import annotations

from .academic import CrossrefAdapter, SemanticScholarAdapter
from .base import ProviderAdapter
from .codex_web import CodexNativeWebSearchAdapter
from .github import GitHubResearchAdapter
from .huggingface import HuggingFaceResearchAdapter
from .paper_search_mcp import PaperSearchMCPAdapter
from .openai_web import OpenAIWebSearchAdapter
from .open_access import OpenAccessAdapter
from .institutional import InstitutionalAccessAdapter
from .paperqa import PaperQAAdapter
from .redfox import RedFoxAdapter


class ProviderRegistry:
    def __init__(self, adapters: list[ProviderAdapter] | None = None) -> None:
        self._adapters = {adapter.provider_id: adapter for adapter in (adapters or [])}

    def get(self, provider_id: str) -> ProviderAdapter:
        try:
            return self._adapters[provider_id]
        except KeyError as exc:
            raise KeyError(f"unknown retrieval provider: {provider_id}") from exc

    def ids(self) -> list[str]:
        return sorted(self._adapters)


def default_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(
        [
            SemanticScholarAdapter(),
            CrossrefAdapter(),
            RedFoxAdapter(),
            CodexNativeWebSearchAdapter(),
            GitHubResearchAdapter(),
            HuggingFaceResearchAdapter(),
            PaperSearchMCPAdapter(),
            OpenAIWebSearchAdapter(),
            OpenAccessAdapter(),
            InstitutionalAccessAdapter(),
            PaperQAAdapter(),
        ]
    )
