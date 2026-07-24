"""PaperQA capability marker.

Evidence execution is handled by ``PaperQAEvidenceService`` because it consumes
frozen local snapshots rather than performing open-ended retrieval.
"""

from __future__ import annotations

import importlib.util

from ..domain.external_models import CorpusDocument, CorpusManifest, EvidenceResult
from ..domain.models import ProviderErrorClass, QueryPlan, RetrievalPhase
from ..domain.repository import RetrievalRepository
from ..evidence.paperqa_service import (
    EvidenceRunner,
    IndexRunner,
    PaperQAEvidenceService,
)
from .base import ProviderAdapter, ProviderFailure, ProviderSearchResult


PAPERQA_RELEASE = "2026.3.18"


class PaperQAAdapter(ProviderAdapter):
    provider_id = "paperqa"
    domains = {"paperqa.local"}
    http_method = "LOCAL"

    def __init__(
        self,
        *,
        repository: RetrievalRepository | None = None,
        runner: EvidenceRunner | None = None,
        index_runner: IndexRunner | None = None,
    ) -> None:
        super().__init__()
        self._service = (
            PaperQAEvidenceService(
                repository,
                runner=runner,
                index_runner=index_runner,
            )
            if repository is not None
            else None
        )

    def health_check(self) -> dict[str, str]:
        installed = importlib.util.find_spec("paperqa") is not None
        parser_installed = importlib.util.find_spec("paperqa_pypdf") is not None
        return {
            "provider": self.provider_id,
            "status": ("degraded" if installed and parser_installed else "unavailable"),
            "release": PAPERQA_RELEASE,
            "reason": (
                "package and PDF parser installed; a recent hash-bound "
                "evidence result is still required"
                if installed and parser_installed
                else "PaperQA or its PDF parser is not installed"
            ),
        }

    def search(self, plan: QueryPlan) -> ProviderSearchResult:
        raise ProviderFailure(
            "PaperQA analyzes a frozen corpus and cannot perform gateway search",
            ProviderErrorClass.POLICY_DENIED,
        )

    def create_corpus(
        self,
        *,
        study_id: str,
        phase: RetrievalPhase,
        documents: list[CorpusDocument],
        parser_version: str,
        embedding_model_hash: str,
        llm_config_hash: str,
    ) -> CorpusManifest:
        return self._require_service().create_corpus(
            study_id=study_id,
            phase=phase,
            documents=documents,
            parser_version=parser_version,
            embedding_model_hash=embedding_model_hash,
            llm_config_hash=llm_config_hash,
        )

    def add_document(
        self,
        corpus_id: str,
        document: CorpusDocument,
    ) -> CorpusManifest:
        return self._require_service().add_document(corpus_id, document)

    def remove_document(
        self,
        corpus_id: str,
        *,
        resource_id: str,
    ) -> CorpusManifest:
        return self._require_service().remove_document(
            corpus_id,
            resource_id=resource_id,
        )

    def build_index(self, corpus_id: str) -> CorpusManifest:
        return self._require_service().build_index(corpus_id)

    def query_evidence(self, corpus_id: str, question: str) -> EvidenceResult:
        return self._require_service().query_evidence(corpus_id, question)

    def query_evidence_batch(
        self,
        corpus_id: str,
        questions: list[dict[str, str]],
    ) -> list[EvidenceResult]:
        return self._require_service().query_evidence_batch(corpus_id, questions)

    def synthesize_related_work(
        self,
        corpus_id: str,
        research_question: str,
    ) -> EvidenceResult:
        return self._require_service().synthesize_related_work(
            corpus_id,
            research_question,
        )

    def find_conflicting_evidence(
        self,
        corpus_id: str,
        claim: str,
    ) -> EvidenceResult:
        return self._require_service().find_conflicting_evidence(corpus_id, claim)

    def get_index_status(self, corpus_id: str) -> CorpusManifest:
        return self._require_service().get_index_status(corpus_id)

    def invalidate_index(
        self,
        corpus_id: str,
        *,
        reason: str,
    ) -> CorpusManifest:
        return self._require_service().invalidate_index(corpus_id, reason=reason)

    def _require_service(self) -> PaperQAEvidenceService:
        if self._service is None:
            raise ProviderFailure(
                "PaperQA corpus operations require a RetrievalRepository",
                ProviderErrorClass.POLICY_DENIED,
            )
        return self._service
