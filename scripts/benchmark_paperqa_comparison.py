"""Compare upstream PaperQA retrieval with Research Forge evidence synthesis.

The comparison deliberately reuses the same frozen ``Docs`` index and the same
question set.  This isolates the governance/synthesis layer from corpus and
retriever differences.  It does not silently call PaperQA's default OpenAI
models; an upstream full-answer comparison therefore remains unavailable when
no separately configured LLM provider exists.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_forge.models import utc_now  # noqa: E402
from research_forge.retrieval.domain.repository import (  # noqa: E402
    RetrievalRepository,
)
from research_forge.retrieval.evidence import PaperQAEvidenceService  # noqa: E402
from research_forge.retrieval.evidence.paperqa_runtime import (  # noqa: E402
    PaperQARuntime,
    _page_from_name,
    _paperqa_settings,
    _paperqa_version,
)
from research_forge.storage import write_json_atomic  # noqa: E402


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold()


def term_group_coverage(text: str, groups: list[list[str]]) -> dict[str, Any]:
    """Score required fact groups without pretending this is semantic grading."""

    normalized = _normalized(text)
    hits = [
        bool(group) and all(_normalized(term) in normalized for term in group)
        for group in groups
    ]
    return {
        "groups": len(groups),
        "hits": sum(hits),
        "hit_vector": hits,
        "coverage": (sum(hits) / len(hits)) if hits else None,
    }


def _is_abstention(answer: str) -> bool:
    normalized = _normalized(answer)
    signals = (
        "cannot answer",
        "do not identify",
        "does not identify",
        "no specific",
        "no evidence",
        "insufficient",
        "not established",
        "cannot be named",
    )
    return any(signal in normalized for signal in signals)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def full_answer_claim_policy(
    upstream_model_label: str,
    forge_model_label: str,
) -> dict[str, Any]:
    same_model = upstream_model_label.strip() == forge_model_label.strip()
    return {
        "same_answer_model": same_model,
        "fair_end_to_end_comparison": same_model,
        "claim_ceiling": (
            "paired end-to-end comparison"
            if same_model
            else "diagnostic only; answer models differ"
        ),
    }


def _load_cases(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not value:
        raise ValueError("benchmark cases must be a non-empty JSON list")
    for item in value:
        if not isinstance(item, dict) or not str(item.get("question") or "").strip():
            raise ValueError("every benchmark case requires a question")
        groups = item.get("required_term_groups", [])
        if not isinstance(groups, list) or any(not isinstance(row, list) for row in groups):
            raise ValueError("required_term_groups must be a list of lists")
    return value


def run_benchmark(
    *,
    workflow_root: Path,
    corpus_id: str,
    cases: list[dict[str, Any]],
    run_forge_synthesis: bool,
) -> dict[str, Any]:
    repository = RetrievalRepository(workflow_root)
    corpus = repository.load_corpus(corpus_id)
    runtime = PaperQARuntime(repository)
    docs = runtime._load_indexed_docs(corpus)
    if docs is None:
        raise ValueError("benchmark requires a persisted, verified PaperQA index")

    service = PaperQAEvidenceService(repository) if run_forge_synthesis else None
    rows: list[dict[str, Any]] = []
    for case in cases:
        question = str(case["question"]).strip()
        groups = [
            [str(term) for term in group]
            for group in case.get("required_term_groups", [])
        ]
        started = time.perf_counter()
        session = asyncio.run(
            docs.aget_evidence(question, settings=_paperqa_settings())
        )
        upstream_latency = time.perf_counter() - started
        contexts = list(session.contexts)
        context_text = "\n".join(str(item.context) for item in contexts)
        upstream_context_ids = [str(item.id) for item in contexts]
        row: dict[str, Any] = {
            "case_id": str(case.get("case_id") or question),
            "question": question,
            "expected_answerable": bool(case.get("expected_answerable", True)),
            "upstream_paperqa": {
                "operation": "aget_evidence",
                "latency_seconds": round(upstream_latency, 6),
                "context_count": len(contexts),
                "context_ids": upstream_context_ids,
                "pages": [_page_from_name(item.text.name) for item in contexts],
                "required_fact_retrieval": term_group_coverage(context_text, groups),
                "full_answer_status": (
                    "not_run: upstream full answers require a separately "
                    "configured LLM provider/API credential"
                ),
            },
        }
        if service is not None:
            started = time.perf_counter()
            # A benchmark must measure a fresh synthesis call rather than the
            # ordinary idempotent EvidenceResult reuse path.
            result = service.ask(corpus_id, question, reuse_existing=False)
            forge_latency = time.perf_counter() - started
            cited_ids = [span.section for span in result.evidence_spans]
            answer_score = term_group_coverage(result.answer, groups)
            row["research_forge"] = {
                "operation": "hash-bound retrieval plus Codex synthesis",
                "latency_seconds": round(forge_latency, 6),
                "answer": result.answer,
                "answer_required_fact_coverage": answer_score,
                "abstained": _is_abstention(result.answer),
                "evidence_span_count": len(result.evidence_spans),
                "pages": [span.page for span in result.evidence_spans],
                "cited_context_ids": cited_ids,
                "citations_within_upstream_top_k": all(
                    item in upstream_context_ids for item in cited_ids
                ),
                "all_spans_hash_bound": all(
                    bool(span.quote_hash)
                    and bool(span.snapshot_id)
                    and span.end_offset > span.start_offset
                    for span in result.evidence_spans
                ),
                "internal_context_token_leak": bool(
                    re.search(r"pqac-|\ue200|\ue201|\ue202", result.answer, re.I)
                ),
                "limitations": result.limitations,
                "answerability": result.answerability,
                "unsupported_statements": result.unsupported_statements,
                "cache_key": result.cache_key,
                "latency_breakdown": result.latency_breakdown,
                "verdict_authority": result.verdict_authority,
                "added_latency_seconds": round(
                    max(0.0, forge_latency - upstream_latency), 6
                ),
            }
        rows.append(row)

    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "workflow_root": str(workflow_root.resolve()),
        "corpus_id": corpus_id,
        "corpus_manifest_hash": corpus.manifest_hash,
        "index_hash": corpus.index_hash,
        "paperqa_version": _paperqa_version(),
        "comparison_design": (
            "paired same-corpus same-index same-question comparison; upstream "
            "PaperQA measures retrieval, while Research Forge adds Codex "
            "synthesis and immutable provenance enforcement"
        ),
        "upstream_full_answer_comparison_available": False,
        "upstream_full_answer_limitation": (
            "The machine has no separately configured upstream PaperQA LLM "
            "credential, and the user selected Codex-local synthesis instead."
        ),
        "cases": rows,
    }
    if run_forge_synthesis:
        forge_rows = [row["research_forge"] for row in rows]
        upstream_latencies = [
            row["upstream_paperqa"]["latency_seconds"] for row in rows
        ]
        forge_latencies = [row["latency_seconds"] for row in forge_rows]
        answerable = [
            row
            for row in rows
            if row["expected_answerable"]
            and row["research_forge"]["answer_required_fact_coverage"]["coverage"]
            is not None
        ]
        unanswerable = [row for row in rows if not row["expected_answerable"]]
        report["summary"] = {
            "case_count": len(rows),
            "mean_upstream_retrieval_seconds": round(
                sum(upstream_latencies) / len(upstream_latencies),
                6,
            ),
            "median_upstream_retrieval_seconds": round(
                statistics.median(upstream_latencies), 6
            ),
            "mean_forge_end_to_end_seconds": round(
                sum(forge_latencies) / len(forge_latencies),
                6,
            ),
            "median_forge_end_to_end_seconds": round(
                statistics.median(forge_latencies), 6
            ),
            "max_forge_end_to_end_seconds": round(max(forge_latencies), 6),
            "mean_answer_fact_coverage": (
                round(
                    sum(
                        row["research_forge"]["answer_required_fact_coverage"][
                            "coverage"
                        ]
                        for row in answerable
                    )
                    / len(answerable),
                    6,
                )
                if answerable
                else None
            ),
            "unanswerable_abstention_rate": (
                round(
                    sum(row["research_forge"]["abstained"] for row in unanswerable)
                    / len(unanswerable),
                    6,
                )
                if unanswerable
                else None
            ),
            "citation_subset_rate": round(
                sum(row["citations_within_upstream_top_k"] for row in forge_rows)
                / len(forge_rows),
                6,
            ),
            "hash_bound_span_rate": round(
                sum(row["all_spans_hash_bound"] for row in forge_rows)
                / len(forge_rows),
                6,
            ),
            "internal_context_token_leak_rate": round(
                sum(row["internal_context_token_leak"] for row in forge_rows)
                / len(forge_rows),
                6,
            ),
            "answerability_accuracy": round(
                sum(
                    (
                        row["research_forge"]["answerability"] == "unanswerable"
                    )
                    == (not row["expected_answerable"])
                    for row in rows
                )
                / len(rows),
                6,
            ),
            "unsupported_statement_rate": round(
                sum(
                    bool(row["research_forge"]["unsupported_statements"])
                    for row in rows
                )
                / len(rows),
                6,
            ),
            "latency_percentiles_seconds": {
                "upstream_retrieval": {
                    name: round(value, 6) if value is not None else None
                    for name, value in {
                        "p50": _percentile(upstream_latencies, 0.50),
                        "p90": _percentile(upstream_latencies, 0.90),
                        "p95": _percentile(upstream_latencies, 0.95),
                        "p99": _percentile(upstream_latencies, 0.99),
                    }.items()
                },
                "forge_end_to_end": {
                    name: round(value, 6) if value is not None else None
                    for name, value in {
                        "p50": _percentile(forge_latencies, 0.50),
                        "p90": _percentile(forge_latencies, 0.90),
                        "p95": _percentile(forge_latencies, 0.95),
                        "p99": _percentile(forge_latencies, 0.99),
                    }.items()
                },
            },
            "cold_start_seconds": forge_latencies[0],
            "warm_session_seconds": (
                round(statistics.median(forge_latencies[1:]), 6)
                if len(forge_latencies) > 1
                else None
            ),
        }
    return report


def run_scale_matrix(
    *,
    workflow_root: Path,
    corpus_specs: list[tuple[str, int]],
    cases: list[dict[str, Any]],
    question_counts: list[int],
) -> dict[str, Any]:
    """Measure bounded batches; callers supply corpora with 1/10/100 documents."""

    repository = RetrievalRepository(workflow_root)
    service = PaperQAEvidenceService(repository)
    rows = []
    for corpus_id, expected_document_count in corpus_specs:
        corpus = repository.load_corpus(corpus_id)
        if len(corpus.documents) != expected_document_count:
            raise ValueError(
                f"{corpus_id} has {len(corpus.documents)} documents, expected "
                f"{expected_document_count}"
            )
        for question_count in question_counts:
            questions = [
                {
                    "question_id": f"scale-{question_count}-{index}",
                    "question": str(cases[index % len(cases)]["question"]),
                }
                for index in range(question_count)
            ]
            started = time.perf_counter()
            results = service.query_evidence_batch(
                corpus_id,
                questions,
                reuse_existing=False,
            )
            uncached_seconds = time.perf_counter() - started
            started = time.perf_counter()
            cached = service.query_evidence_batch(
                corpus_id,
                questions,
                reuse_existing=True,
            )
            cached_seconds = time.perf_counter() - started
            component_names = (
                "index_load_ms",
                "paperqa_retrieval_ms",
                "evidence_serialization_ms",
                "queue_wait_ms",
                "worker_client_init_ms",
                "session_start_ms",
                "model_round_trip_ms",
                "schema_validation_ms",
                "evidence_validation_ms",
            )
            component_summary = {}
            for component in component_names:
                values = [
                    float(item.latency_breakdown[component])
                    for item in results
                    if isinstance(item.latency_breakdown.get(component), int)
                ]
                component_summary[component] = {
                    "p50": (
                        round(float(_percentile(values, 0.50)), 3)
                        if values
                        else None
                    ),
                    "p95": (
                        round(float(_percentile(values, 0.95)), 3)
                        if values
                        else None
                    ),
                    "max": round(max(values), 3) if values else None,
                }
            rows.append(
                {
                    "corpus_id": corpus_id,
                    "document_count": len(corpus.documents),
                    "question_count": question_count,
                    "batch_limit": 20,
                    "synthesis_job_count": (question_count + 19) // 20,
                    "uncached_seconds": round(uncached_seconds, 6),
                    "cached_seconds": round(cached_seconds, 6),
                    "uncached_seconds_per_question": round(
                        uncached_seconds / question_count, 6
                    ),
                    "cached_seconds_per_question": round(
                        cached_seconds / question_count, 6
                    ),
                    "answerability": [
                        item.answerability for item in results
                    ],
                    "cache_keys_stable": [
                        item.cache_key for item in results
                    ]
                    == [item.cache_key for item in cached],
                    "hash_bound_span_rate": round(
                        sum(
                            bool(span.quote_hash and span.snapshot_id)
                            for item in results
                            for span in item.evidence_spans
                        )
                        / max(
                            1,
                            sum(len(item.evidence_spans) for item in results),
                        ),
                        6,
                    ),
                    "unsupported_statement_rate": round(
                        sum(bool(item.unsupported_statements) for item in results)
                        / len(results),
                        6,
                    ),
                    "latency_components_ms": component_summary,
                }
            )
    uncached = [float(item["uncached_seconds"]) for item in rows]
    cached = [float(item["cached_seconds"]) for item in rows]
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "design": (
            "bounded batch matrix over explicitly supplied frozen corpora; "
            "question counts above 20 are split into multiple stateless jobs"
        ),
        "rows": rows,
        "latency_percentiles_seconds": {
            "uncached": {
                name: round(value, 6) if value is not None else None
                for name, value in {
                    "p50": _percentile(uncached, 0.50),
                    "p90": _percentile(uncached, 0.90),
                    "p95": _percentile(uncached, 0.95),
                    "p99": _percentile(uncached, 0.99),
                }.items()
            },
            "cached": {
                name: round(value, 6) if value is not None else None
                for name, value in {
                    "p50": _percentile(cached, 0.50),
                    "p90": _percentile(cached, 0.90),
                    "p95": _percentile(cached, 0.95),
                    "p99": _percentile(cached, 0.99),
                }.items()
            },
        },
    }


def run_upstream_full_answers(
    *,
    workflow_root: Path,
    corpus_id: str,
    cases: list[dict[str, Any]],
    settings_payload: dict[str, Any],
    upstream_model_label: str,
    forge_model_label: str,
    use_codex_lmi: bool = False,
) -> dict[str, Any]:
    """Run PaperQA's complete answer path with an explicit model configuration."""

    from paperqa import Settings

    repository = RetrievalRepository(workflow_root)
    corpus = repository.load_corpus(corpus_id)
    docs = PaperQARuntime(repository)._load_indexed_docs(corpus)
    if docs is None:
        raise ValueError("full-answer comparison requires a verified PaperQA index")
    settings = Settings.model_validate(settings_payload)
    llm_model = None
    if use_codex_lmi:
        from research_forge.agent_runtime import codex_provider_binding
        from research_forge.retrieval.evidence.codex_lmi import (
            build_codex_lmi_model,
        )

        llm_model = build_codex_lmi_model(cwd=workflow_root.parent)
        actual_model = codex_provider_binding()["provider_model"]
        upstream_model_label = actual_model
        forge_model_label = actual_model
    rows = []
    for case in cases:
        started = time.perf_counter()
        session = asyncio.run(
            docs.aquery(
                str(case["question"]),
                settings=settings,
                llm_model=llm_model,
                summary_llm_model=llm_model,
            )
        )
        latency = time.perf_counter() - started
        answer = str(getattr(session, "answer", "") or "")
        rows.append(
            {
                "case_id": str(case.get("case_id") or case["question"]),
                "answer": answer,
                "latency_seconds": round(latency, 6),
                "required_fact_coverage": term_group_coverage(
                    answer,
                    [
                        [str(term) for term in group]
                        for group in case.get("required_term_groups", [])
                    ],
                ),
                "abstained": _is_abstention(answer),
            }
        )
    claim_policy = full_answer_claim_policy(
        upstream_model_label,
        forge_model_label,
    )
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "corpus_id": corpus_id,
        "upstream_model_label": upstream_model_label,
        "forge_model_label": forge_model_label,
        "upstream_uses_codex_lmi_adapter": use_codex_lmi,
        **claim_policy,
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-root", type=Path, default=Path(".rfab"))
    parser.add_argument("--corpus-id", required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--run-forge-synthesis", action="store_true")
    parser.add_argument(
        "--scale-corpus",
        action="append",
        default=[],
        metavar="CORPUS_ID:DOCUMENT_COUNT",
    )
    parser.add_argument(
        "--scale-question-counts",
        default="1,5,20,50",
    )
    parser.add_argument("--upstream-settings-json", type=Path)
    parser.add_argument("--upstream-model-label")
    parser.add_argument("--forge-model-label")
    parser.add_argument("--upstream-use-codex", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_benchmark(
        workflow_root=args.workflow_root,
        corpus_id=args.corpus_id,
        cases=_load_cases(args.cases),
        run_forge_synthesis=args.run_forge_synthesis,
    )
    if args.scale_corpus:
        report["scale_matrix"] = run_scale_matrix(
            workflow_root=args.workflow_root,
            corpus_specs=[
                (value.rsplit(":", 1)[0], int(value.rsplit(":", 1)[1]))
                for value in args.scale_corpus
            ],
            cases=_load_cases(args.cases),
            question_counts=[
                int(value)
                for value in args.scale_question_counts.split(",")
                if value.strip()
            ],
        )
    if args.upstream_settings_json:
        if (
            not args.upstream_use_codex
            and (not args.upstream_model_label or not args.forge_model_label)
        ):
            parser.error(
                "full-answer comparison requires --upstream-model-label and "
                "--forge-model-label"
            )
        full_answers = run_upstream_full_answers(
            workflow_root=args.workflow_root,
            corpus_id=args.corpus_id,
            cases=_load_cases(args.cases),
            settings_payload=json.loads(
                args.upstream_settings_json.read_text(encoding="utf-8")
            ),
            upstream_model_label=args.upstream_model_label or "codex",
            forge_model_label=args.forge_model_label or "codex",
            use_codex_lmi=args.upstream_use_codex,
        )
        report["upstream_full_answers"] = full_answers
        report["upstream_full_answer_comparison_available"] = True
        report["upstream_full_answer_limitation"] = None
        if args.run_forge_synthesis:
            forge_by_id = {
                str(item["case_id"]): item["research_forge"]
                for item in report["cases"]
            }
            paired = [
                (item, forge_by_id[str(item["case_id"])])
                for item in full_answers["cases"]
                if str(item["case_id"]) in forge_by_id
            ]
            report["paired_full_answer_summary"] = {
                "fair_end_to_end_comparison": full_answers[
                    "fair_end_to_end_comparison"
                ],
                "case_count": len(paired),
                "mean_upstream_answer_seconds": round(
                    sum(item[0]["latency_seconds"] for item in paired)
                    / len(paired),
                    6,
                )
                if paired
                else None,
                "mean_forge_answer_seconds": round(
                    sum(item[1]["latency_seconds"] for item in paired)
                    / len(paired),
                    6,
                )
                if paired
                else None,
                "mean_upstream_fact_coverage": round(
                    sum(
                        item[0]["required_fact_coverage"]["coverage"]
                        for item in paired
                        if item[0]["required_fact_coverage"]["coverage"] is not None
                    )
                    / max(
                        1,
                        sum(
                            item[0]["required_fact_coverage"]["coverage"] is not None
                            for item in paired
                        ),
                    ),
                    6,
                ),
                "mean_forge_fact_coverage": round(
                    sum(
                        item[1]["answer_required_fact_coverage"]["coverage"]
                        for item in paired
                        if item[1]["answer_required_fact_coverage"]["coverage"]
                        is not None
                    )
                    / max(
                        1,
                        sum(
                            item[1]["answer_required_fact_coverage"]["coverage"]
                            is not None
                            for item in paired
                        ),
                    ),
                    6,
                ),
            }
    if args.output:
        write_json_atomic(args.output, report)
        print(args.output.resolve())
    else:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
