import hashlib
import json
from pathlib import Path

from research_forge.claim_discovery import (
    AuthorClaim,
    ClaimDiscoveryReport,
    ProjectResearchFingerprint,
    RecommendedClaim,
    SourceSpan,
    TrendSignal,
    build_academic_concept_normalizations,
    build_discovery_portfolio,
    build_discovery_query_intents,
    build_project_fingerprint,
    discover_project_claims,
    extract_author_claims,
    fetch_crossref_trends,
    recommend_claims,
    _claims_from_text,
)


def test_crossref_trends_preserve_real_authors_separately_from_venue() -> None:
    requests = []

    def fake_http(request):
        requests.append(request)
        return {
            "message": {
                "items": [
                    {
                        "DOI": "10.1234/example",
                        "title": ["Prompt evaluation study"],
                        "abstract": "Prompt evaluation under controlled conditions.",
                        "URL": "https://doi.org/10.1234/example",
                        "published": {"date-parts": [[2026]]},
                        "is-referenced-by-count": 3,
                        "container-title": ["Journal of Evaluation"],
                        "author": [
                            {"given": "Ada", "family": "Lovelace"},
                            {"given": "Alan", "family": "Turing"},
                        ],
                    }
                ]
            }
        }

    signals = fetch_crossref_trends(
        ["prompt evaluation"],
        http_json=fake_http,
    )

    assert "author" in requests[0].full_url
    assert signals[0].source_name == "Journal of Evaluation"
    assert signals[0].metadata["authors"] == ["Ada Lovelace", "Alan Turing"]


def _resource(path: Path, root: Path) -> dict:
    payload = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "suffix": path.suffix,
    }


def test_internal_project_label_is_normalized_before_academic_search(
    tmp_path: Path,
) -> None:
    report = tmp_path / "docs" / "ranker.md"
    report.parent.mkdir()
    report.write_text(
        "\n".join(
            [
                "# 极端赢家排序器 V2 研究报告",
                "算法采用 LightGBM LambdaMART，并加入公开催化特征。",
                "目标固定为：未来20个交易日可执行收益位于所属行业前 10%，"
                "并且绝对收益不低于 +10%。",
            ]
        ),
        encoding="utf-8",
    )
    candidate = {
        "track_id": "extreme-winner-ranker-v2",
        "display_title": "极端赢家排序器 V2 研究报告",
        "novelty_seed": "公开催化剂特征可能改善极端赢家排序。",
        "protocol_path": "protocols/ranker.json",
        "report_path": "docs/ranker.md",
    }

    rows = build_academic_concept_normalizations(tmp_path, [candidate], [])
    fingerprint = build_project_fingerprint(
        [],
        [candidate],
        [],
        source_root=tmp_path,
    )
    queries = build_discovery_query_intents(fingerprint, [])

    assert rows[0].status == "normalized"
    assert "极端赢家" not in rows[0].academic_title
    assert "横截面股票排序" in rows[0].academic_title
    assert "未来20个交易日" in rows[0].operational_definition
    assert queries[0].query.startswith("cross-sectional equity ranking")
    assert "extreme winner" not in " ".join(item.query for item in queries)


def test_generic_engineering_brief_becomes_comparative_research_frame(
    tmp_path: Path,
) -> None:
    report = tmp_path / "docs" / "product-refactor.md"
    report.parent.mkdir()
    report.write_text(
        """# ProjectZephyr Retrieval Refactor

## Baseline

The professor recommendation pipeline runs whichever search channels happen
to be available and lets the model emit scoring signals.

## Problem

Coverage is not explicit, so missing dimensions cannot be distinguished from
unplanned sources. Model and fallback paths are asymmetric.

## Target Path

Persist mandatory dimension search packets, normalize an evidence ledger, and
run deterministic scoring before rendering the recommendation.
""",
        encoding="utf-8",
    )
    candidate = {
        "track_id": "project-zephyr-refactor",
        "display_title": "ProjectZephyr Retrieval Refactor",
        "novelty_seed": "Internal architecture proposal",
        "report_path": "docs/product-refactor.md",
    }

    row = build_academic_concept_normalizations(
        tmp_path, [candidate], []
    )[0]

    assert row.status == "normalized"
    assert "ProjectZephyr" not in row.academic_title
    assert "学术导师推荐" in row.academic_title
    assert row.comparison_frame["comparator"].startswith(
        "opportunistic retrieval"
    )
    assert (
        row.comparison_frame["primary_outcome"]
        == "evidence-dimension coverage rate"
    )
    assert row.comparison_frame["scientific_evidence_status"] == (
        "not_yet_validated"
    )


def test_bounded_self_play_maps_to_agent_critique_not_play_therapy(
    tmp_path: Path,
) -> None:
    report = tmp_path / "README.md"
    report.write_text(
        """
# Bounded self-play claim verification

Compare a single-pass claim proposer with one bounded proposer-critic-revision
round over the same frozen claim-evidence items.
""",
        encoding="utf-8",
    )
    candidate = {
        "track_id": "bounded-self-play-v1",
        "display_title": "Bounded self-play claim verification",
        "novelty_seed": "auditable proposer-critic revision",
        "report_path": "README.md",
    }

    row = build_academic_concept_normalizations(
        tmp_path, [candidate], []
    )[0]

    assert row.status == "normalized"
    assert "self-critique in language models" in row.academic_concepts
    assert "play therapy" not in " ".join(row.academic_query_terms)
    assert row.comparison_frame["unit_of_analysis"] == (
        "one registered task-seed pair"
    )


def test_prompt_corpus_and_account_tutorial_do_not_become_research_claims() -> None:
    prompt_claims = _claims_from_text(
        (
            "# prompt sheet\n"
            "masterpiece, detailed CG, 32K, a child holding food, "
            "Steps: 28, Sampler: Euler, CFG scale: 7, Seed: 42\n"
        ),
        "materials/提示词/爆款关键词.xlsx",
        "a" * 64,
        allow_typed_lines=True,
    )
    tutorial_claims = _claims_from_text(
        "这是目前唯一能实现无限积分的方法！！！\n",
        "无限积分注册教程/完整步骤.docx",
        "b" * 64,
        allow_typed_lines=True,
    )

    assert prompt_claims == []
    assert tutorial_claims == []


def test_generative_media_materials_form_a_testable_prompt_study() -> None:
    candidate = {
        "track_id": "derived-generative-media-v1",
        "display_title": "AI生成视频指南",
        "novelty_seed": "用生成模型制作视频",
        "output_path": "提示词/风景类关键词.xlsx",
    }

    row = build_academic_concept_normalizations(
        None, [candidate], []
    )[0]

    assert row.status == "normalized"
    assert "提示词属性" in row.academic_title
    assert row.comparison_frame["primary_outcome"] == (
        "prompt-output semantic alignment rate"
    )
    assert "即梦" not in " ".join(row.academic_query_terms)


def test_technical_manual_forms_a_computational_retrieval_study() -> None:
    manual = {
        "track_id": "derived-user-manual-v1",
        "display_title": "Notebook Instructions for Use",
        "novelty_seed": "使用说明书包括安全须知、电池和 BIOS 故障处理。",
        "output_path": "电子说明书.pdf",
    }

    row = build_academic_concept_normalizations(
        None, [manual], []
    )[0]

    assert row.status == "normalized"
    assert "操作指引可发现性" in row.academic_title
    assert row.comparison_frame["primary_outcome"] == (
        "correct instruction retrieval at k"
    )
    assert row.comparison_frame["source"] == (
        "technical_manual_material"
    )


def test_author_claims_keep_exact_file_provenance(tmp_path: Path) -> None:
    report = tmp_path / "docs" / "report.md"
    report.parent.mkdir()
    report.write_text(
        """# 灵活退出研究

## 结论

灵活退出机制在冻结回测中降低了最大回撤。

- 主要收益指标没有低于固定退出基线。
""",
        encoding="utf-8",
    )

    claims = extract_author_claims(tmp_path, [_resource(report, tmp_path)])

    assert len(claims) == 2
    assert claims[0].evidence_status == "author_statement_only"
    assert claims[0].source_spans[0].path == "docs/report.md"
    assert claims[0].source_spans[0].line == 5
    assert claims[0].claim_type == "comparative"


def test_research_contract_uses_title_and_hypothesis_not_embedded_task_brief(
    tmp_path: Path,
) -> None:
    contract = tmp_path / "nested" / "research_contract.json"
    contract.parent.mkdir()
    contract.write_text(
        json.dumps(
            {
                "title": "AIRS-Bench: Textual Classification",
                "research_question": (
                    "# Overview\n## Task Description\n"
                    + "benchmark instructions " * 100
                    + "\n## Dataset Structure\ntrain and test"
                ),
                "hypothesis": (
                    "A bounded, evidence-driven change can improve the frozen "
                    "benchmark metric."
                ),
            }
        ),
        encoding="utf-8",
    )

    claims = extract_author_claims(tmp_path, [_resource(contract, tmp_path)])

    assert len(claims) == 1
    assert claims[0].statement.startswith(
        "AIRS-Bench: Textual Classification:"
    )
    assert "Task Description" not in claims[0].statement


def test_external_trends_recommend_validation_but_never_become_evidence(
    tmp_path: Path,
) -> None:
    report = tmp_path / "docs" / "exit-report.md"
    result = tmp_path / "outputs" / "exit.json"
    report.parent.mkdir()
    result.parent.mkdir()
    report.write_text(
        """# 灵活退出与择时稳健性研究

## 主要贡献

灵活退出机制在冻结回测中降低了最大回撤，同时保持主要收益指标。
""",
        encoding="utf-8",
    )
    result.write_text(json.dumps({"maximumDrawdown": 0.12, "return": 0.31}), encoding="utf-8")
    (tmp_path / ".env.local").write_text("REDFOX_API_KEY=test-only\n", encoding="utf-8")
    resources = [_resource(report, tmp_path), _resource(result, tmp_path)]
    candidates = [
        {
            "track_id": "flexible-exit-v1",
            "display_title": "灵活退出与择时稳健性研究",
            "novelty_seed": "灵活退出是否降低最大回撤并保持收益",
            "artifact_chain_complete": True,
        }
    ]

    def fake_http(request):
        if "redfox.hk" in request.full_url:
            return {
                "code": 200,
                "data": {
                    "list": [
                        {
                            "title": "动态退出策略成为量化交易稳健性新焦点",
                            "workUrl": "https://mp.weixin.qq.com/s/example",
                            "author": "研究前沿",
                            "summary": "研究关注灵活退出、最大回撤与收益保持。",
                            "publishTime": "2026-07-20T08:00:00+00:00",
                            "readCount": 12000,
                            "likeCount": 320,
                            "shareCount": 80,
                        }
                    ]
                },
            }
        return {
            "data": [
                {
                    "title": "Adaptive exit rules for robust portfolio management",
                    "abstract": "We evaluate flexible exit rules using return and drawdown outcomes.",
                    "url": "https://www.semanticscholar.org/paper/example",
                    "year": 2026,
                    "publicationDate": "2026-06-01",
                    "citationCount": 9,
                    "influentialCitationCount": 2,
                    "venue": "Example Journal",
                }
            ]
        }

    discovery = discover_project_claims(
        tmp_path,
        resources,
        candidates,
        include_external=True,
        http_json=fake_http,
    )

    assert discovery.provider_status["redfox_wechat"].startswith("ok:")
    assert discovery.provider_status["semantic_scholar"].startswith("ok:")
    assert {item.provider for item in discovery.trend_signals} == {
        "redfox_wechat",
        "semantic_scholar",
    }
    assert all(item.evidence_role == "attention_only" for item in discovery.trend_signals)
    recommended = discovery.recommended_claims[0]
    assert recommended.origin == "trend_and_author"
    assert recommended.matched_signal_ids
    assert recommended.scientific_evidence_status == "not_yet_validated"
    assert all("weixin" not in path for path in recommended.local_evidence_paths)


def test_author_only_claim_remains_recommended_when_providers_are_unavailable(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.md"
    report.write_text("## 结论\n\n候选模型在冻结数据上提高了分类准确率。\n", encoding="utf-8")
    resources = [_resource(report, tmp_path)]
    claims = extract_author_claims(tmp_path, resources)

    from research_forge.claim_discovery import build_project_fingerprint

    fingerprint = build_project_fingerprint(resources, [], claims)
    recommended = recommend_claims(fingerprint, claims, [], [])

    assert recommended[0].origin == "author_asserted"
    assert "external_trend_match" in recommended[0].missing_context
    assert recommended[0].scientific_evidence_status == "not_yet_validated"


def test_local_fingerprint_produces_auditable_multi_purpose_query_matrix() -> None:
    fingerprint = ProjectResearchFingerprint(
        domains=["量化投资"],
        problems=["灵活退出", "最大回撤"],
        methods=["排序模型"],
        metrics=["return", "drawdown"],
        terms=["flexible_exit", "drawdown", "portfolio"],
    )
    claim = AuthorClaim(
        claim_id="author-claim-test",
        statement="灵活退出机制降低最大回撤并保持组合收益。",
        claim_type="comparative",
        source_spans=[
            SourceSpan(path="reports/result.md", sha256="a" * 64, line=8)
        ],
    )

    intents = build_discovery_query_intents(fingerprint, [claim])

    assert {item.purpose for item in intents} == {
        "closest_prior_work",
        "method_and_baseline",
        "contradicting_evidence",
        "recent_trend",
        "dataset_and_model",
    }
    contradiction = next(
        item for item in intents if item.purpose == "contradicting_evidence"
    )
    assert contradiction.source_claim_ids == [claim.claim_id]
    assert "negative results" in contradiction.query


def test_discovery_portfolio_binds_local_claims_to_external_sources_without_verdict_authority() -> None:
    fingerprint = ProjectResearchFingerprint(
        domains=["量化投资"],
        problems=["灵活退出", "最大回撤"],
        methods=["排序模型"],
        metrics=["return", "drawdown"],
        terms=["flexible_exit", "drawdown", "portfolio"],
        evidence_assets=["protocols/exit.json", "outputs/exit.json"],
        source_track_ids=["flexible-exit-v1"],
    )
    signal = TrendSignal(
        signal_id="signal-prior-work",
        provider="semantic_scholar",
        signal_class="scholarly_attention",
        query="flexible exit drawdown",
        title="Adaptive portfolio exit rules and drawdown",
        summary="A benchmark study of flexible exit methods and portfolio drawdown.",
        url="https://example.org/paper",
        terms=["flexible_exit", "drawdown", "portfolio", "benchmark"],
        trend_score=0.7,
        scientific_density=0.9,
    )
    recommendation = RecommendedClaim(
        claim_id="recommended-claim-test",
        statement="灵活退出机制降低最大回撤并保持组合收益。",
        origin="trend_and_author",
        recommendation_score=80,
        trend_score=70,
        project_match_score=75,
        evidence_readiness_score=75,
        matched_signal_ids=[signal.signal_id],
        source_claim_ids=["author-claim-test"],
        match_reasons=["项目主张与外部来源具有共同研究概念"],
        local_evidence_paths=["protocols/exit.json", "outputs/exit.json"],
    )
    report = ClaimDiscoveryReport(
        generated_at="2026-07-24T00:00:00+00:00",
        source_root="C:/project",
        fingerprint=fingerprint,
        author_claims=[],
        trend_signals=[signal],
        recommended_claims=[recommendation],
        provider_status={"semantic_scholar": "ok:1"},
    )
    candidate = {
        "track_id": "flexible-exit-v1",
        "display_title": "灵活退出与最大回撤",
        "novelty_seed": recommendation.statement,
        "protocol_path": "protocols/exit.json",
        "output_path": "outputs/exit.json",
        "report_path": "reports/exit.md",
        "artifact_chain_complete": True,
        "protocol_bound_to_output": True,
        "closure_input_ready": True,
        "paperability_score": 90,
        "blockers": [],
    }
    intents = build_discovery_query_intents(fingerprint, [])

    portfolio = build_discovery_portfolio(
        fingerprint,
        [candidate],
        report,
        intents,
        query_plan_id="query-plan-test",
        resource_set_ids=["resource-set-test"],
        coverage={"verified_result_count": 1},
    )

    assert portfolio.status == "ready_for_scope_selection"
    assert portfolio.recommended_direction_id == portfolio.directions[0].direction_id
    direction = portfolio.directions[0]
    assert direction.primary_track_id == "flexible-exit-v1"
    assert direction.evidence_chain_level == "verified_chain"
    assert direction.closest_prior_work_ids == [signal.signal_id]
    assert direction.scientific_evidence_status == "discovery_only"
    assert portfolio.claim_source_matches[0].verdict_authority is False

    unmatched_report = report.model_copy(
        update={
            "recommended_claims": [
                recommendation.model_copy(update={"matched_signal_ids": []})
            ]
        }
    )
    unmatched = build_discovery_portfolio(
        fingerprint,
        [candidate],
        unmatched_report,
        intents,
        query_plan_id="query-plan-unmatched",
        resource_set_ids=["resource-set-unmatched"],
        coverage={"verified_result_count": 40},
    )

    assert unmatched.status == "external_grounding_incomplete"
    assert unmatched.directions[0].external_source_ids == []

    candidate_only = build_discovery_portfolio(
        fingerprint,
        [candidate],
        report.model_copy(
            update={
                "author_claims": [],
                "recommended_claims": [],
                "trend_signals": [],
            }
        ),
        intents,
        query_plan_id="query-plan-candidate-only",
    )

    assert candidate_only.status == "external_grounding_incomplete"
    assert candidate_only.directions[0].primary_track_id == "flexible-exit-v1"
    assert "No eligible author Claim was extracted" in " ".join(
        candidate_only.directions[0].blockers
    )
