from __future__ import annotations

import asyncio
import json
import urllib.parse
from pathlib import Path

import pytest

from research_forge.literature import (
    approve_literature,
    audit_stage1,
    discover_literature,
    screen_literature,
    synthesize_literature,
)
from research_forge.models import (
    Direction,
    LiteratureSearchPlan,
    LiteratureScreeningDecision,
    LiteratureScreeningOutput,
    LiteratureSourceType,
    LiteratureSynthesis,
    MetricDefinition,
    NoveltyCandidate,
    RelatedWorkTheme,
    ResearchPlanDraft,
)
from research_forge.service import (
    create_project,
    plan_project,
    register_literature_source,
)
from research_forge.storage import read_json, write_json_atomic


class FakeScholarlyClient:
    crossref_mailto = None
    semantic_scholar_api_key = None

    def get(self, url: str, provider: str):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if provider == "crossref":
            items = [
                {
                    "DOI": f"10.1234/stage1.{index}",
                    "title": [
                        f"Autonomous research agents with citation verification and evidence provenance {index}"
                    ],
                    "author": [{"given": "Test", "family": f"Author {index}"}],
                    "published": {"date-parts": [[2024]]},
                    "abstract": (
                        "This abstract describes an autonomous research agent with citation verification, "
                        "experiment provenance, and evidence-grounded evaluation."
                    ),
                    "container-title": ["Fixture Transactions"],
                    "type": "journal-article",
                    "URL": f"https://doi.org/10.1234/stage1.{index}",
                    "is-referenced-by-count": index,
                }
                for index in range(1, 11)
            ]
            payload = {"message": {"items": items}}
        else:
            items = [
                {
                    "paperId": f"s2-{index}",
                    "title": (
                        f"Autonomous research agents with citation verification and evidence provenance {index}"
                    ),
                    "authors": [{"name": f"Test Author {index}"}],
                    "year": 2024,
                    "abstract": (
                        "This abstract describes an autonomous research agent with citation verification, "
                        "experiment provenance, and evidence-grounded evaluation."
                    ),
                    "venue": "Fixture Transactions",
                    "publicationTypes": ["JournalArticle"],
                    "citationCount": index,
                    "url": f"https://www.semanticscholar.org/paper/s2-{index}",
                    "externalIds": {"DOI": f"10.1234/stage1.{index}"},
                }
                for index in range(1, 11)
            ]
            payload = {"data": items, "query": query.get("query")}
        raw = json.dumps(payload).encode("utf-8")
        return payload, raw


def _project_with_search_plan(tmp_path: Path) -> Path:
    project = create_project(
        "Stage 1 Test",
        "Evaluate evidence-grounded autonomous research agents.",
        root=tmp_path,
    )
    plan_id = "search-plan-test"
    plan = LiteratureSearchPlan(
        review_question="How can autonomous research agents bind citations to experiment evidence?",
        queries=[
            "autonomous research agents evidence provenance",
            "citation verification scientific agents",
        ],
        key_concepts=["autonomous research agents", "citation verification", "evidence provenance"],
        inclusion_criteria=["evaluates an autonomous scientific research workflow"],
        exclusion_criteria=["does not report a scholarly research workflow"],
        scope_limitations=["This bounded metadata search is not an exhaustive systematic review."],
    )
    write_json_atomic(project / "literature" / "search_plans" / f"{plan_id}.json", plan)
    write_json_atomic(project / "literature" / "latest_search_plan.json", {"search_plan_id": plan_id})
    return project


def test_discovery_deduplicates_providers_and_stage1_audit_detects_tampering(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project_with_search_plan(tmp_path)
    discovery = discover_literature(
        project,
        rows_per_query=10,
        include_count=5,
        from_year=2020,
        client=FakeScholarlyClient(),
    )
    assert len(discovery.candidates) == 10
    assert len(discovery.included_source_ids) == 5
    assert all(
        candidate.verification_status == "cross_provider"
        for candidate in discovery.candidates
    )
    for source_id in discovery.included_source_ids:
        source = read_json(project / "literature" / "sources" / f"{source_id}.json")
        assert source["origin"] == "discovery"
        assert source["origin_id"] == discovery.discovery_id

    async def fake_screening(prompt: str, *, cwd=None) -> LiteratureScreeningOutput:
        candidate_ids = [item["candidate_id"] for item in json.loads(prompt)["candidates"]]
        return LiteratureScreeningOutput(
            decisions=[
                LiteratureScreeningDecision(
                    candidate_id=candidate_id,
                    decision="core" if index < 5 else "supporting",
                    rationale="The supplied title and abstract directly match the bounded review question.",
                    criteria_matches=["autonomous research workflow", "evidence provenance"],
                    concerns=[],
                )
                for index, candidate_id in enumerate(candidate_ids)
            ]
        )

    monkeypatch.setattr(
        "research_forge.agent_runtime.screen_literature_candidates",
        fake_screening,
    )
    screening = asyncio.run(screen_literature(project, max_candidates=10, include_count=5))
    assert len(screening.included_source_ids) == 5

    source_ids = screening.included_source_ids

    async def fake_synthesis(prompt: str, *, cwd=None) -> LiteratureSynthesis:
        return LiteratureSynthesis(
            review_scope="A bounded review of evidence-grounded autonomous scientific research agents.",
            themes=[
                RelatedWorkTheme(
                    theme_id="theme-evidence-binding",
                    label="Evidence binding",
                    summary="The registered metadata links agent workflows to citation verification.",
                    source_ids=source_ids[:3],
                )
            ],
            novelty_candidates=[
                NoveltyCandidate(
                    novelty_id="novelty-run-claim-binding",
                    gap_statement="The bounded set does not establish end-to-end run-to-claim verification.",
                    proposed_question="Does exact run-to-claim binding reduce unsupported scientific conclusions?",
                    differentiator="The proposed study audits every numerical statement against run records.",
                    falsification_risk="A broader review may find an existing equivalent end-to-end audit.",
                    source_ids=source_ids[:3],
                )
            ],
            recommended_novelty_id="novelty-run-claim-binding",
            evidence_limitations=["Only provider metadata and abstracts were reviewed."],
        )

    monkeypatch.setattr(
        "research_forge.agent_runtime.generate_literature_synthesis",
        fake_synthesis,
    )
    review, pending_audit = asyncio.run(synthesize_literature(project))
    assert not pending_audit.passed
    assert not pending_audit.checks["human_approval_present"]
    rendered_review = (project / "literature" / "review.md").read_text(encoding="utf-8")
    assert "## Human approval checklist" in rendered_review
    assert "## Screening audit trail" in rendered_review
    assert screening.screening_id not in rendered_review  # Decisions, not an unverifiable summary.
    assert screening.evaluated_candidate_ids[0] in rendered_review
    with pytest.raises(ValueError, match="selected novelty ID"):
        approve_literature(
            project,
            confirmation=review.review_id,
            selected_novelty_id="novelty-not-in-review",
        )
    _, audit = approve_literature(
        project,
        confirmation=review.review_id,
        selected_novelty_id="novelty-run-claim-binding",
        note="Test approval",
    )
    assert not audit.passed
    assert not audit.checks["research_plan_present"]
    assert audit.provider_count == 2
    assert audit.verified_paper_count == 5

    register_literature_source(
        project,
        source_id="manual-unapproved",
        source_type=LiteratureSourceType.PAPER,
        title="An unrelated manually registered source",
        authors=["Manual Author"],
        year=2024,
        locator="https://example.test/manual-unapproved",
        verification_method="Checked in the deterministic test fixture.",
        verified=True,
    )
    captured: dict[str, object] = {}

    async def fake_plan(prompt: str, *, cwd=None) -> ResearchPlanDraft:
        captured.update(json.loads(prompt))
        return ResearchPlanDraft(
            title="Evidence-grounded research-agent study",
            research_question="Does exact provenance binding reduce unsupported research claims?",
            hypothesis="Exact provenance binding will reduce unsupported claims under a fixed task set.",
            novelty_claim="The bounded review motivates a joint literature and experiment provenance test.",
            scope_in=["fixed research tasks", "claim-level provenance audit"],
            scope_out=["publication-level novelty proof"],
            method_outline=["run a frozen baseline", "add provenance binding", "compare errors"],
            datasets=["a fixed manually reviewed research-task suite"],
            metrics=[
                MetricDefinition(
                    name="unsupported_claim_rate",
                    description="Fraction of conclusions lacking registered evidence.",
                    direction=Direction.MINIMIZE,
                )
            ],
            baseline_definition="The same research agent without exact provenance binding.",
            ablation_axes=["literature-only versus dual provenance"],
            confounders=["retrieval quality"],
            stop_conditions=["the fixed task and run budgets are exhausted"],
            risks=["the task suite may be too small"],
            clarifying_questions=[],
            readiness_summary="The question, baseline, data decision, metric, and stop rule are explicit.",
            ready_to_freeze=True,
        )

    monkeypatch.setattr("research_forge.agent_runtime.generate_plan", fake_plan)
    draft_id, _ = asyncio.run(plan_project(project, "Use the recommended bounded novelty candidate."))
    prompt_source_ids = {
        source["source_id"] for source in captured["verified_research_sources"]
    }
    assert prompt_source_ids == set(screening.included_source_ids)
    assert "manual-unapproved" not in prompt_source_ids
    binding = read_json(project / "plans" / f"{draft_id}-evidence.json")
    assert binding["source_ids"] == sorted(screening.included_source_ids)
    assert binding["selected_novelty_id"] == "novelty-run-claim-binding"
    complete = audit_stage1(project)
    assert complete.passed
    assert complete.checks["plan_evidence_binding_valid"]
    assert complete.checks["plan_artifacts_in_manifest"]

    plan_path = project / "plans" / f"{draft_id}.json"
    original_plan = plan_path.read_bytes()
    plan_path.write_bytes(original_plan + b"\n")
    changed_plan = audit_stage1(project)
    assert not changed_plan.passed
    assert not changed_plan.checks["stage1_artifacts_hash_valid"]
    assert not changed_plan.checks["plan_evidence_binding_valid"]
    plan_path.write_bytes(original_plan)
    assert audit_stage1(project).passed

    newer_plan_id = "search-plan-newer"
    write_json_atomic(
        project / "literature" / "search_plans" / f"{newer_plan_id}.json",
        LiteratureSearchPlan(
            review_question="Does a revised evidence audit change the bounded research question?",
            queries=["revised research agent evidence audit", "revised provenance benchmark"],
            key_concepts=["research agents", "provenance audit"],
            inclusion_criteria=["evaluates scientific-agent evidence"],
            exclusion_criteria=["does not evaluate a research workflow"],
            scope_limitations=["This remains a bounded metadata search."],
        ),
    )
    write_json_atomic(
        project / "literature" / "latest_search_plan.json",
        {"search_plan_id": newer_plan_id},
    )
    stale = audit_stage1(project)
    assert not stale.passed
    assert not stale.checks["discovery_bound_to_latest_search_plan"]

    raw_relative = next(iter(discovery.raw_response_hashes))
    raw_path = project / raw_relative
    raw_path.write_text("{}", encoding="utf-8")
    tampered = audit_stage1(project)
    assert not tampered.passed
    assert any("changed or disappeared" in item for item in tampered.violations)


class AlwaysFailScholarlyClient(FakeScholarlyClient):
    def get(self, url: str, provider: str):
        raise RuntimeError(f"{provider} unavailable in failure fixture")


def test_failed_rediscovery_preserves_previous_registered_sources(tmp_path: Path) -> None:
    project = _project_with_search_plan(tmp_path)
    discover_literature(
        project,
        rows_per_query=10,
        include_count=5,
        client=FakeScholarlyClient(),
    )
    before = {
        path.name: path.read_bytes()
        for path in (project / "literature" / "sources").glob("*.json")
    }
    with pytest.raises(RuntimeError, match="zero usable candidates"):
        discover_literature(
            project,
            rows_per_query=10,
            include_count=5,
            client=AlwaysFailScholarlyClient(),
        )
    after = {
        path.name: path.read_bytes()
        for path in (project / "literature" / "sources").glob("*.json")
    }
    assert after == before
