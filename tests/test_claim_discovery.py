import hashlib
import json
from pathlib import Path

from research_forge.claim_discovery import (
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
