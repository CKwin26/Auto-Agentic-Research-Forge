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
    build_discovery_portfolio,
    build_discovery_query_intents,
    discover_project_claims,
    extract_author_claims,
    recommend_claims,
)


def _resource(path: Path, root: Path) -> dict:
    payload = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "suffix": path.suffix,
    }


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
