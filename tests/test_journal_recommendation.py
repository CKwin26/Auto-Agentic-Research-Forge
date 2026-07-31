from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
import research_forge.journal_recommendation as recommendation_module

from research_forge.journal_recommendation import (
    REGISTRY_PATH,
    load_journal_registry,
    overlay_ccf_deadlines,
    recommend_journals,
    recommend_venues,
    render_journal_recommendation_markdown,
)
from research_forge.cli import main as cli_main
from research_forge.venue_evidence import SimilarPaperEvidence


ROOT = Path(__file__).resolve().parents[1]
MANUSCRIPT = ROOT / "output" / "pdf" / "research-agent-evidence-v4-paper-en.tex"
PROJECT = ROOT / "stage1_runs" / "research-agent-evidence-v2"


def _recommendation(report, journal_id: str):
    return next(item for item in report.recommendations if item.journal_id == journal_id)


def test_current_v4_routes_to_specialist_journal_but_blocks_submission() -> None:
    report = recommend_journals(MANUSCRIPT, project=PROJECT)

    assert report.calibration_status == "uncalibrated_heuristic_v1"
    assert report.assessment.depth_gate_passed is True
    assert report.assessment.publication_ready is False
    assert report.assessment.primary_analysis_interpretable is False
    assert report.recommendations[0].journal_id == "research-integrity-and-peer-review"
    assert all(item.routing_label == "do_not_submit_yet" for item in report.recommendations)
    assert report.recommendations[0].combined_submission_success.center < 0.15
    assert (
        report.recommendations[0].after_known_blockers_resolved.center
        > report.recommendations[0].combined_submission_success.center
    )


def test_default_registry_is_a_strict_peer_reviewed_archival_whitelist() -> None:
    registry = load_journal_registry()
    excluded_fallbacks = {
        "plos-one",
        "frontiers-in-artificial-intelligence",
        "peerj-computer-science",
        "discover-artificial-intelligence",
        "softwarex",
    }

    assert registry.policy.startswith("Default recommendations include only established")
    assert excluded_fallbacks.isdisjoint({item.id for item in registry.venues})
    assert {item.venue_type for item in registry.venues} == {"journal", "conference"}
    assert all(item.archival_peer_reviewed for item in registry.venues)
    assert all(item.official_source_verified for item in registry.venues)
    assert all(item.whitelist_evidence for item in registry.venues)


def test_non_archival_or_unverified_custom_venue_is_rejected(tmp_path: Path) -> None:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    registry["venues"][0]["archival_peer_reviewed"] = False
    invalid = tmp_path / "invalid-registry.json"
    invalid.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="strict whitelist"):
        load_journal_registry(invalid)


def test_conferences_are_ranked_separately_and_deadlines_gate_current_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 7, 20, 12, 0, 0)
            return value.replace(tzinfo=tz) if tz is not None else value

    monkeypatch.setattr(recommendation_module, "datetime", FixedDatetime)
    report = recommend_venues(MANUSCRIPT, project=PROJECT)
    journals = [item for item in report.recommendations if item.venue_type == "journal"]
    conferences = [item for item in report.recommendations if item.venue_type == "conference"]

    assert journals[0].rank == 1
    assert conferences[0].rank == 1
    assert conferences[0].journal_id == "aaai-27-main"
    assert conferences[0].current_cycle_eligible is True
    assert conferences[0].current_cycle_submission_success.center > 0
    closed = _recommendation(report, "neurips-2026-evaluations-datasets")
    assert closed.current_cycle_eligible is False
    assert closed.current_cycle_submission_success.center == 0
    assert "CURRENT_SUBMISSION_CYCLE_UNAVAILABLE" in closed.hard_blockers
    markdown = render_journal_recommendation_markdown(report)
    assert "## 正规同行评审期刊" in markdown
    assert "## 正式归档型学术会议" in markdown
    assert "Frontiers" not in markdown
    assert "PLOS ONE" not in markdown


def test_same_journal_history_activates_local_calibration(tmp_path: Path) -> None:
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps(
            {
                "submissions": [
                    {
                        "journal_id": "research-integrity-and-peer-review",
                        "desk_passed": True,
                        "final_outcome": "accepted" if index < 4 else "rejected",
                    }
                    for index in range(5)
                ]
            }
        ),
        encoding="utf-8",
    )

    report = recommend_journals(MANUSCRIPT, project=PROJECT, history_path=history)

    assert "locally_calibrated_v1_n=5" in report.calibration_status


def test_stale_registry_is_reported(tmp_path: Path) -> None:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    registry["source_checked_at"] = "2020-01-01"
    stale = tmp_path / "journals.json"
    stale.write_text(json.dumps(registry), encoding="utf-8")

    assert load_journal_registry(stale).source_checked_at == "2020-01-01"
    report = recommend_journals(MANUSCRIPT, project=PROJECT, registry_path=stale)
    assert report.registry_warnings


def test_probability_intervals_are_ordered() -> None:
    report = recommend_venues(MANUSCRIPT, project=PROJECT)
    for item in report.recommendations:
        for interval in (
            item.desk_screen_survival,
            item.conditional_post_review_success,
            item.combined_submission_success,
            item.current_cycle_submission_success,
            item.after_known_blockers_resolved,
        ):
            assert 0 <= interval.low <= interval.center <= interval.high <= 1


def _manuscript(path: Path, topic: str = "machine learning evaluation") -> Path:
    path.write_text(
        "\n".join(
            [
                "# Evidence-Grounded Machine Learning Evaluation",
                "",
                "## Abstract",
                "",
                (
                    "We present a machine learning evaluation framework with controlled experiments, "
                    f"ablation studies, and reproducibility evidence for {topic}. The method measures "
                    "claim preservation and reliability across multiple tasks and reports uncertainty."
                ),
                "",
                "## Introduction",
                "",
                "This empirical study evaluates learning systems and research reporting.",
                "",
                "## Methods",
                "",
                "We use a controlled experiment, statistical test, benchmark, and ablation design.",
                "",
                "## Results",
                "",
                "The evaluation covers 8 tasks and 3 seeds.",
                "",
                "## Discussion",
                "",
                "The evidence supports a bounded claim and identifies limitations.",
                "",
                "## References",
                "",
                "1. Example reference.",
            ]
        ),
        encoding="utf-8",
    )
    return path


class _FakeOpenAlex:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def search_similar(self, query: str, *, from_year: int, max_results: int):
        self.calls.append(query)
        return (
            [
                SimilarPaperEvidence(
                    provider="openalex",
                    provider_id="W1",
                    title="Auditable Evaluation for Learning Systems",
                    year=2025,
                    venue_name="Transactions on Machine Learning Research",
                    venue_type="journal",
                    doi="https://doi.org/10.0000/example",
                    url="https://openalex.org/W1",
                    relevance_score=0.94,
                ),
                SimilarPaperEvidence(
                    provider="openalex",
                    provider_id="W2",
                    title="A Different Pattern Recognition Study",
                    year=2024,
                    venue_name="Pattern Recognition",
                    venue_type="journal",
                    url="https://openalex.org/W2",
                    relevance_score=0.88,
                ),
                SimilarPaperEvidence(
                    provider="openalex",
                    provider_id="W3",
                    title="Reliable Generalization in Deep Learning",
                    year=2025,
                    venue_name="Advances in Neural Information Processing Systems",
                    venue_type="conference",
                    url="https://openalex.org/W3",
                    relevance_score=0.86,
                ),
            ],
            "a" * 64,
        )


class _UnavailableOpenAlex:
    def search_similar(self, query: str, *, from_year: int, max_results: int):
        raise RuntimeError("openalex HTTP 504")


def test_live_semantic_evidence_is_hash_bound_and_explainable(tmp_path: Path) -> None:
    manuscript = _manuscript(tmp_path / "paper.md")
    cache = tmp_path / "evidence.json"
    client = _FakeOpenAlex()

    report = recommend_venues(
        manuscript,
        live_evidence=True,
        evidence_cache_path=cache,
        evidence_client=client,
        top=8,
    )

    tmlr = _recommendation(report, "tmlr")
    assert client.calls and "Evidence-Grounded" in client.calls[0]
    assert report.evidence.sent_fields == ["title", "abstract"]
    assert report.evidence.manuscript_sha256 == report.assessment.manuscript_sha256
    assert cache.is_file()
    assert tmlr.semantic_fit == pytest.approx(0.94)
    assert tmlr.evidence_count == 1
    assert tmlr.similar_papers[0].provider_id == "W1"
    assert _recommendation(report, "neurips-2026-main").evidence_count == 1
    assert _recommendation(report, "neurips-2026-evaluations-datasets").evidence_count == 0
    assert any(item.venue_name == "Pattern Recognition" for item in report.discovered_candidates)

    cached = recommend_venues(manuscript, evidence_cache_path=cache, top=8)
    assert cached.evidence.cache_hit is True
    assert _recommendation(cached, "tmlr").semantic_fit == pytest.approx(0.94)


def test_changed_manuscript_invalidates_semantic_cache(tmp_path: Path) -> None:
    manuscript = _manuscript(tmp_path / "paper.md")
    cache = tmp_path / "evidence.json"
    recommend_venues(
        manuscript,
        live_evidence=True,
        evidence_cache_path=cache,
        evidence_client=_FakeOpenAlex(),
    )
    manuscript.write_text(manuscript.read_text(encoding="utf-8") + "\nChanged source.\n", encoding="utf-8")

    report = recommend_venues(manuscript, evidence_cache_path=cache)

    assert report.evidence.cache_hit is False
    assert report.evidence.papers == []
    assert any("does not match" in warning for warning in report.evidence.warnings)


def test_live_evidence_failure_degrades_to_audited_offline_ranking(tmp_path: Path) -> None:
    manuscript = _manuscript(tmp_path / "paper.md")

    report = recommend_venues(
        manuscript,
        live_evidence=True,
        evidence_client=_UnavailableOpenAlex(),
    )

    assert report.recommendations
    assert report.evidence.provider_status["openalex"] == "unavailable"
    assert report.evidence.papers == []
    assert any("HTTP 504" in warning for warning in report.evidence.warnings)


def test_openreview_export_is_scored_locally_without_external_upload(tmp_path: Path) -> None:
    manuscript = _manuscript(tmp_path / "paper.md", "representation learning benchmarks")
    exported = tmp_path / "accepted.json"
    exported.write_text(
        json.dumps(
            [
                {
                    "id": "accepted-1",
                    "content": {
                        "title": {"value": "Reproducible Representation Learning Evaluation"},
                        "abstract": {
                            "value": "A machine learning benchmark with controlled ablations and reliability analysis."
                        },
                        "venueid": {"value": "ICLR.cc/2025/Conference"},
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    report = recommend_venues(manuscript, openreview_export=exported)
    iclr = _recommendation(report, "iclr-2026-main")

    assert report.evidence.sent_fields == []
    assert report.evidence.provider_status["openreview_export"] == "local_ok_n=1"
    assert iclr.evidence_count == 1
    assert iclr.similar_papers[0].provider == "openreview_export"


def test_ccf_deadlines_checkout_overlays_ranks_and_future_cycle(tmp_path: Path) -> None:
    ccf = tmp_path / "ccf-deadlines" / "conference" / "AI"
    ccf.mkdir(parents=True)
    (ccf / "iclr.yml").write_text(
        """- title: ICLR
  rank:
    ccf: A
    core: A*
  confs:
    - year: 2099
      id: iclr99
      link: https://iclr.example/2099/cfp
      timeline:
        - deadline: '2098-09-20 23:59:59'
          comment: abstract
        - deadline: '2098-09-27 23:59:59'
          comment: full paper
""",
        encoding="utf-8",
    )

    registry, warnings = overlay_ccf_deadlines(load_journal_registry(), tmp_path / "ccf-deadlines")
    iclr = next(item for item in registry.venues if item.id == "iclr-2026-main")

    assert iclr.rankings == {"ccf": "A", "core": "A*"}
    assert iclr.cycle_label == "iclr99"
    assert iclr.cycle_status == "open"
    assert iclr.abstract_deadline == "2098-09-20"
    assert iclr.submission_deadline == "2098-09-27"
    assert warnings


def test_project_first_cli_resolves_synthesized_manuscript(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    synthesis = project / "synthesis"
    synthesis.mkdir(parents=True)
    manuscript = _manuscript(synthesis / "manuscript.md")
    report_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"

    exit_code = cli_main(
        [
            "--home",
            str(tmp_path),
            "venue",
            "recommend",
            "demo",
            "--top",
            "1",
            "--report",
            str(report_path),
            "--markdown",
            str(markdown_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["assessment"]["manuscript_path"] == str(manuscript.resolve())
    assert payload["evidence"]["provider_status"]["openalex"] == "not_requested"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "未校准的内部概率不参与主排名" in markdown
    assert "可复核的相似论文依据" in markdown


def test_cli_rejects_invalid_semantic_evidence_limit(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    synthesis = project / "synthesis"
    synthesis.mkdir(parents=True)
    _manuscript(synthesis / "manuscript.md")

    exit_code = cli_main(
        [
            "--home",
            str(tmp_path),
            "venue",
            "recommend",
            "demo",
            "--evidence-results",
            "0",
        ]
    )

    assert exit_code == 2
    assert not (synthesis / "venue_recommendation.json").exists()
