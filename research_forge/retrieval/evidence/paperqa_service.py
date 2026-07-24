"""Frozen-corpus evidence analysis with source-span enforcement."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from typing import Any, Literal, cast

from ...models import utc_now
from ..domain.external_models import (
    CorpusDocument,
    CorpusManifest,
    CorpusStatus,
    EvidenceResult,
    EvidenceSpan,
)
from ..domain.models import RetrievalPhase, retrieval_id
from ..domain.repository import RetrievalRepository


EvidenceRunner = Callable[[CorpusManifest, str], dict[str, Any]]
IndexRunner = Callable[[CorpusManifest], str]


class PaperQAEvidenceService:
    def __init__(
        self,
        repository: RetrievalRepository,
        *,
        runner: EvidenceRunner | None = None,
        index_runner: IndexRunner | None = None,
    ) -> None:
        self.repository = repository
        self.runtime: Any | None = None
        if runner is None and index_runner is None:
            from .paperqa_runtime import PaperQARuntime

            runtime = PaperQARuntime(repository)
            if runtime.available():
                self.runtime = runtime
                runner = runtime.ask
                index_runner = runtime.index
        self.runner = runner
        self.index_runner = index_runner

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
        """Create a draft corpus from rights-approved, frozen documents."""
        return self.build_corpus(
            study_id=study_id,
            phase=phase,
            documents=documents,
            parser_version=parser_version,
            embedding_model_hash=embedding_model_hash,
            llm_config_hash=llm_config_hash,
        )

    def build_corpus(
        self,
        *,
        study_id: str,
        phase: RetrievalPhase,
        documents: list[CorpusDocument],
        parser_version: str,
        embedding_model_hash: str,
        llm_config_hash: str,
    ) -> CorpusManifest:
        if not documents:
            raise ValueError("PaperQA corpus requires at least one frozen document")
        for item in documents:
            decision = self.repository.load_access_decision(item.access_decision_id)
            if not decision.model_processing_allowed:
                raise ValueError(
                    f"resource {item.resource_id} is not authorized for model processing"
                )
        canonical = {
            "study_id": study_id,
            "phase": phase.value,
            "documents": [
                item.model_dump(mode="json")
                for item in sorted(documents, key=lambda row: row.resource_id)
            ],
            "parser_version": parser_version,
            "embedding_model_hash": embedding_model_hash,
            "llm_config_hash": llm_config_hash,
        }
        manifest_hash = _hash(canonical)
        manifest = CorpusManifest(
            corpus_id=retrieval_id("paperqa-corpus", study_id, manifest_hash),
            study_id=study_id,
            phase=phase,
            status=CorpusStatus.DRAFT,
            documents=documents,
            parser_version=parser_version,
            embedding_model_hash=embedding_model_hash,
            llm_config_hash=llm_config_hash,
            manifest_hash=manifest_hash,
            index_hash=None,
        )
        saved = self.repository.save_corpus(manifest)
        try:
            binding = self.repository.load_binding(documents[0].binding_id)
        except FileNotFoundError:
            return saved
        self.repository.write_artifact(
            project_id=binding.project_id,
            study_id=study_id,
            step_instance_id=binding.step_instance_id,
            kind="paperqa_corpus_manifest",
            value=saved.model_dump(mode="json"),
            producer="paperqa_corpus_builder",
            input_artifact_ids=[
                self.repository.load_snapshot(
                    item.snapshot_id
                ).normalized_content_artifact_id
                for item in documents
            ],
        )
        return saved

    def add_document(
        self,
        corpus_id: str,
        document: CorpusDocument,
    ) -> CorpusManifest:
        """Create a new draft corpus version with one document added.

        A corpus identity is derived from its manifest. Editing a corpus in
        place would leave an existing index bound to the wrong document set, so
        additions and removals always create a new corpus ID.
        """
        corpus = self.repository.load_corpus(corpus_id)
        if corpus.status is not CorpusStatus.DRAFT:
            raise ValueError("only a draft PaperQA corpus can be revised")
        if any(
            item.resource_id == document.resource_id
            or item.snapshot_id == document.snapshot_id
            for item in corpus.documents
        ):
            raise ValueError("PaperQA corpus already contains this document")
        return self.create_corpus(
            study_id=corpus.study_id,
            phase=corpus.phase,
            documents=[*corpus.documents, document],
            parser_version=corpus.parser_version,
            embedding_model_hash=corpus.embedding_model_hash,
            llm_config_hash=corpus.llm_config_hash,
        )

    def remove_document(
        self,
        corpus_id: str,
        *,
        resource_id: str,
    ) -> CorpusManifest:
        """Create a new draft corpus version without one resource."""
        corpus = self.repository.load_corpus(corpus_id)
        if corpus.status is not CorpusStatus.DRAFT:
            raise ValueError("only a draft PaperQA corpus can be revised")
        documents = [
            item for item in corpus.documents if item.resource_id != resource_id
        ]
        if len(documents) == len(corpus.documents):
            raise KeyError(f"resource {resource_id!r} is not in the corpus")
        if not documents:
            raise ValueError("PaperQA corpus must retain at least one document")
        return self.create_corpus(
            study_id=corpus.study_id,
            phase=corpus.phase,
            documents=documents,
            parser_version=corpus.parser_version,
            embedding_model_hash=corpus.embedding_model_hash,
            llm_config_hash=corpus.llm_config_hash,
        )

    def build_index(self, corpus_id: str) -> CorpusManifest:
        return self.index(corpus_id)

    def index(self, corpus_id: str) -> CorpusManifest:
        corpus = self.repository.load_corpus(corpus_id)
        if corpus.status is CorpusStatus.READY:
            return corpus
        if corpus.status not in {CorpusStatus.DRAFT, CorpusStatus.INDEXING}:
            raise ValueError("PaperQA corpus cannot be indexed in its current state")
        if self.index_runner is None:
            raise RuntimeError(
                "PaperQA index runner is not configured; corpus remains draft"
            )
        index_hash = self.index_runner(corpus)
        if len(index_hash) != 64 or any(
            character not in "0123456789abcdef" for character in index_hash
        ):
            raise ValueError("PaperQA index runner returned an invalid content hash")
        indexed = corpus.model_copy(
            update={
                "status": CorpusStatus.READY,
                "index_hash": index_hash,
                "updated_at": utc_now(),
            }
        )
        return self.repository.save_corpus(indexed)

    def get_index_status(self, corpus_id: str) -> CorpusManifest:
        """Return the persisted status and exact index binding."""
        return self.repository.load_corpus(corpus_id)

    def invalidate_index(self, corpus_id: str, *, reason: str) -> CorpusManifest:
        """Invalidate an index without deleting its historical artifacts."""
        reason = reason.strip()
        if not reason:
            raise ValueError("PaperQA index invalidation requires a reason")
        corpus = self.repository.load_corpus(corpus_id)
        if corpus.status is CorpusStatus.INVALIDATED:
            return corpus
        invalidated = corpus.model_copy(
            update={
                "status": CorpusStatus.INVALIDATED,
                "updated_at": utc_now(),
            }
        )
        saved = self.repository.save_corpus(invalidated)
        try:
            binding = self.repository.load_binding(corpus.documents[0].binding_id)
        except FileNotFoundError:
            return saved
        self.repository.write_artifact(
            project_id=binding.project_id,
            study_id=corpus.study_id,
            step_instance_id=binding.step_instance_id,
            kind="paperqa_index_invalidation",
            value={
                "corpus_id": corpus.corpus_id,
                "manifest_hash": corpus.manifest_hash,
                "index_hash": corpus.index_hash,
                "reason": reason,
                "invalidated_at": saved.updated_at,
            },
            producer="paperqa_corpus_manager",
            input_artifact_ids=[
                self.repository.load_snapshot(
                    item.snapshot_id
                ).normalized_content_artifact_id
                for item in corpus.documents
            ],
        )
        return saved

    def query_evidence(self, corpus_id: str, question: str) -> EvidenceResult:
        return self.ask(corpus_id, question)

    def query_evidence_batch(
        self,
        corpus_id: str,
        questions: list[dict[str, str]],
        *,
        reuse_existing: bool = True,
    ) -> list[EvidenceResult]:
        return self.ask_many(
            corpus_id,
            questions,
            reuse_existing=reuse_existing,
        )

    def synthesize_related_work(
        self,
        corpus_id: str,
        research_question: str,
    ) -> EvidenceResult:
        """Synthesize only the frozen corpus; retain ordinary evidence rules."""
        return self.ask(
            corpus_id,
            (
                "Synthesize how the frozen sources relate to this research "
                f"question, including agreements and scope differences: "
                f"{research_question.strip()}"
            ),
        )

    def find_conflicting_evidence(
        self,
        corpus_id: str,
        claim: str,
    ) -> EvidenceResult:
        """Search the corpus for disagreement without acquiring verdict authority."""
        return self.ask(
            corpus_id,
            (
                "Identify evidence in the frozen corpus that contradicts, "
                "qualifies, or fails to support this claim. Distinguish direct "
                f"contradiction from missing evidence: {claim.strip()}"
            ),
        )

    def ask(
        self,
        corpus_id: str,
        question: str,
        *,
        reuse_existing: bool = True,
    ) -> EvidenceResult:
        return self.ask_many(
            corpus_id,
            [{"question_id": "q1", "question": question}],
            reuse_existing=reuse_existing,
        )[0]

    def ask_many(
        self,
        corpus_id: str,
        questions: list[dict[str, str]],
        *,
        reuse_existing: bool = True,
    ) -> list[EvidenceResult]:
        corpus = self.repository.load_corpus(corpus_id)
        if corpus.status is not CorpusStatus.READY:
            raise ValueError("PaperQA corpus is not ready")
        normalized_questions = []
        seen_ids = set()
        for position, item in enumerate(questions):
            question_id = str(item.get("question_id") or f"q{position + 1}").strip()
            question = str(item.get("question") or "").strip()
            if not question:
                raise ValueError("PaperQA question cannot be empty")
            if not question_id or question_id in seen_ids:
                raise ValueError("PaperQA batch question IDs must be unique")
            seen_ids.add(question_id)
            normalized_questions.append((question_id, question))
        if not normalized_questions:
            raise ValueError("PaperQA batch requires at least one question")
        if self.runtime is not None:
            from .paperqa_runtime import MAX_BATCH_QUESTIONS

            results = []
            for start in range(0, len(normalized_questions), MAX_BATCH_QUESTIONS):
                results.extend(
                    self._ask_many_runtime(
                        corpus,
                        normalized_questions[start : start + MAX_BATCH_QUESTIONS],
                        reuse_existing=reuse_existing,
                    )
                )
            return results
        results = []
        for question_id, question in normalized_questions:
            if reuse_existing:
                reusable = [
                    item
                    for item in self.repository.list_evidence_results(corpus_id)
                    if item.question == question
                    and item.index_hash == corpus.index_hash
                    and item.model_config_hash == corpus.llm_config_hash
                    and item.evidence_spans
                    and "pqac-" not in item.answer.casefold()
                    and not any(
                        marker in item.answer for marker in "\ue200\ue201\ue202"
                    )
                ]
                if reusable:
                    results.append(max(reusable, key=lambda item: item.created_at))
                    continue
            if self.runner is None:
                raise RuntimeError(
                    "PaperQA runner is not configured; evidence capability is degraded"
                )
            payload = self.runner(corpus, question)
            payload.setdefault("question_id", question_id)
            payload.setdefault(
                "evidence_bundle_hash",
                _hash(payload.get("evidence_spans") or []),
            )
            payload.setdefault("synthesis_prompt_version", "injected-runner-v1")
            payload.setdefault("output_schema_version", "evidence-result-v1")
            payload.setdefault("policy_version", "evidence-only-policy-v1")
            results.append(self._persist_result(corpus, question, payload))
        return results

    def _ask_many_runtime(
        self,
        corpus: CorpusManifest,
        questions: list[tuple[str, str]],
        *,
        reuse_existing: bool,
    ) -> list[EvidenceResult]:
        from .paperqa_runtime import (
            SYNTHESIS_OUTPUT_SCHEMA_VERSION,
            SYNTHESIS_POLICY_VERSION,
            SYNTHESIS_PROMPT_VERSION,
        )

        runtime = self.runtime
        if runtime is None:
            raise RuntimeError("PaperQA runtime is not configured")
        prepared = runtime.prepare_batch(corpus, questions)
        existing = self.repository.list_evidence_results(corpus.corpus_id)
        results_by_id: dict[str, EvidenceResult] = {}
        misses = []
        for item in prepared:
            cache_key, question_hash = _answer_cache_key(
                question=str(item["question"]),
                evidence_bundle_hash=str(item["evidence_bundle_hash"]),
                corpus=corpus,
                prompt_version=SYNTHESIS_PROMPT_VERSION,
                schema_version=SYNTHESIS_OUTPUT_SCHEMA_VERSION,
                policy_version=SYNTHESIS_POLICY_VERSION,
            )
            item["cache_key"] = cache_key
            item["normalized_question_hash"] = question_hash
            reusable = [
                row
                for row in existing
                if row.cache_key == cache_key
                and row.evidence_bundle_hash == item["evidence_bundle_hash"]
                and row.corpus_manifest_hash == corpus.manifest_hash
                and row.index_hash == corpus.index_hash
                and row.model_config_hash == corpus.llm_config_hash
                and row.synthesis_prompt_version == SYNTHESIS_PROMPT_VERSION
                and row.output_schema_version == SYNTHESIS_OUTPUT_SCHEMA_VERSION
                and row.policy_version == SYNTHESIS_POLICY_VERSION
            ]
            if reuse_existing and reusable:
                results_by_id[str(item["question_id"])] = max(
                    reusable, key=lambda row: row.created_at
                )
            else:
                misses.append(item)
        nonempty = [item for item in misses if item["contexts"]]
        synthesized = (
            {
                str(payload["question_id"]): payload
                for payload in runtime.synthesize_batch(corpus, nonempty)
            }
            if nonempty
            else {}
        )
        for item in misses:
            payload = synthesized.get(str(item["question_id"]))
            if payload is None:
                payload = {
                    "question_id": item["question_id"],
                    "answer": "",
                    "answerability": "unanswerable",
                    "evidence_spans": [],
                    "conflicts": [],
                    "unsupported_statements": [],
                    "limitations": ["PaperQA retrieved no source context."],
                    "abstention_reason": (
                        "No evidence was retrieved from the frozen corpus."
                    ),
                    "evidence_bundle_hash": item["evidence_bundle_hash"],
                    "synthesis_prompt_version": SYNTHESIS_PROMPT_VERSION,
                    "output_schema_version": SYNTHESIS_OUTPUT_SCHEMA_VERSION,
                    "policy_version": SYNTHESIS_POLICY_VERSION,
                    "latency_breakdown": item["latency_breakdown"],
                }
            payload["cache_key"] = item["cache_key"]
            payload["normalized_question_hash"] = item[
                "normalized_question_hash"
            ]
            results_by_id[str(item["question_id"])] = self._persist_result(
                corpus,
                str(item["question"]),
                payload,
            )
        return [results_by_id[question_id] for question_id, _ in questions]

    def _persist_result(
        self,
        corpus: CorpusManifest,
        question: str,
        payload: dict[str, Any],
    ) -> EvidenceResult:
        spans = [
            EvidenceSpan.model_validate(item)
            for item in payload.get("evidence_spans") or []
        ]
        allowed = {(item.resource_id, item.snapshot_id) for item in corpus.documents}
        if any((span.resource_id, span.snapshot_id) not in allowed for span in spans):
            raise ValueError("PaperQA returned a span outside the frozen corpus")
        result_id = retrieval_id(
            "paperqa-result", corpus.corpus_id, question, _hash(payload)
        )
        for existing in self.repository.list_evidence_results(corpus.corpus_id):
            if existing.result_id == result_id:
                return existing
        raw_answerability = str(payload.get("answerability") or "answerable")
        if raw_answerability not in {
            "answerable",
            "partially_answerable",
            "unanswerable",
        }:
            raise ValueError(f"invalid answerability: {raw_answerability}")
        answerability = cast(
            Literal["answerable", "partially_answerable", "unanswerable"],
            raw_answerability,
        )
        result = EvidenceResult(
            result_id=result_id,
            study_id=corpus.study_id,
            corpus_id=corpus.corpus_id,
            question=question,
            answer=str(payload.get("answer") or ""),
            evidence_spans=spans,
            limitations=[str(item) for item in payload.get("limitations") or []],
            answerability=answerability,
            conflicts=[str(item) for item in payload.get("conflicts") or []],
            unsupported_statements=[
                str(item) for item in payload.get("unsupported_statements") or []
            ],
            abstention_reason=(
                str(payload["abstention_reason"])
                if payload.get("abstention_reason")
                else None
            ),
            cache_key=payload.get("cache_key"),
            normalized_question_hash=payload.get("normalized_question_hash"),
            evidence_bundle_hash=payload.get("evidence_bundle_hash"),
            corpus_manifest_hash=corpus.manifest_hash,
            synthesis_prompt_version=payload.get("synthesis_prompt_version"),
            output_schema_version=payload.get("output_schema_version"),
            policy_version=payload.get("policy_version"),
            latency_breakdown={
                **dict(payload.get("latency_breakdown") or {}),
                "persistence_ms": None,
            },
            model_config_hash=corpus.llm_config_hash,
            index_hash=str(corpus.index_hash),
            verdict_authority=False,
            created_at=utc_now(),
        )
        persistence_started = time.perf_counter()
        saved = self.repository.save_evidence_result(result)
        first_document = corpus.documents[0]
        try:
            binding = self.repository.load_binding(first_document.binding_id)
        except FileNotFoundError:
            return saved
        self.repository.write_artifact(
            project_id=binding.project_id,
            study_id=corpus.study_id,
            step_instance_id=binding.step_instance_id,
            kind="evidence_results",
            value=[saved.model_dump(mode="json")],
            producer="paperqa_evidence_mapper",
            input_artifact_ids=[
                self.repository.load_snapshot(
                    item.snapshot_id
                ).normalized_content_artifact_id
                for item in corpus.documents
            ],
        )
        persistence_ms = round((time.perf_counter() - persistence_started) * 1000)
        from .paperqa_runtime import diagnose_latency_breakdown

        complete_latency = {
            **saved.latency_breakdown,
            "persistence_ms": persistence_ms,
        }
        self.repository.write_artifact(
            project_id=binding.project_id,
            study_id=corpus.study_id,
            step_instance_id=binding.step_instance_id,
            kind="synthesis_latency",
            value={
                "result_id": saved.result_id,
                "corpus_id": corpus.corpus_id,
                "cache_key": saved.cache_key,
                "latency_breakdown": complete_latency,
                "latency_diagnosis": diagnose_latency_breakdown(
                    complete_latency
                ),
            },
            producer="paperqa_synthesis_telemetry",
            input_artifact_ids=[
                self.repository.load_snapshot(
                    item.snapshot_id
                ).normalized_content_artifact_id
                for item in corpus.documents
            ],
        )
        return saved


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _normalized_question(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _answer_cache_key(
    *,
    question: str,
    evidence_bundle_hash: str,
    corpus: CorpusManifest,
    prompt_version: str,
    schema_version: str,
    policy_version: str,
) -> tuple[str, str]:
    question_hash = _hash(_normalized_question(question))
    payload = {
        "normalized_question_hash": question_hash,
        "evidence_bundle_hash": evidence_bundle_hash,
        "corpus_manifest_hash": corpus.manifest_hash,
        "paperqa_index_hash": corpus.index_hash,
        "synthesis_prompt_version": prompt_version,
        "synthesis_model_config_hash": corpus.llm_config_hash,
        "output_schema_version": schema_version,
        "policy_version": policy_version,
    }
    return _hash(payload), question_hash
