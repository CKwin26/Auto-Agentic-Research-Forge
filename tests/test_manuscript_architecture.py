from types import SimpleNamespace

from research_forge.manuscript_architecture import (
    audit_manuscript_citations,
    audit_source_freshness,
    build_manuscript_source_snapshot,
    build_reverse_outline,
)
from research_forge.paper_authoring import (
    EvidenceClaimBinding,
    EvidenceClaimMap,
    EvidencePointer,
)
from research_forge.paper_pipeline import EMPIRICAL_ARTICLE_NARRATIVE


def _sections() -> dict[str, str]:
    return {
        spec.key: (
            f"The {spec.key} section states its main scholarly point. "
            "A second sentence supplies bounded detail without changing the claim."
        )
        for spec in EMPIRICAL_ARTICLE_NARRATIVE.sections
    }


def test_source_snapshot_detects_stale_manuscript_inputs() -> None:
    drafted = build_manuscript_source_snapshot(
        {"evidence": {"value": 1}, "figures": ["fig-1"]}
    )
    unchanged = build_manuscript_source_snapshot(
        {"figures": ["fig-1"], "evidence": {"value": 1}}
    )
    changed = build_manuscript_source_snapshot(
        {"evidence": {"value": 2}, "figures": ["fig-1"]}
    )

    assert audit_source_freshness(drafted, unchanged).passed
    report = audit_source_freshness(drafted, changed)
    assert not report.passed
    assert report.changed_components == ["evidence"]


def test_source_snapshot_ignores_adapter_reconstruction_timestamps() -> None:
    drafted = build_manuscript_source_snapshot(
        {
            "literature_sources": [
                {
                    "source_id": "source-a",
                    "title": "Stable paper",
                    "created_at": "2026-08-03T10:00:00Z",
                }
            ]
        }
    )
    reconstructed = build_manuscript_source_snapshot(
        {
            "literature_sources": [
                {
                    "source_id": "source-a",
                    "title": "Stable paper",
                    "created_at": "2026-08-03T10:05:00Z",
                }
            ]
        }
    )
    changed_content = build_manuscript_source_snapshot(
        {
            "literature_sources": [
                {
                    "source_id": "source-a",
                    "title": "Changed paper",
                    "created_at": "2026-08-03T10:05:00Z",
                }
            ]
        }
    )

    assert audit_source_freshness(drafted, reconstructed).passed
    assert not audit_source_freshness(drafted, changed_content).passed


def test_source_snapshot_ignores_literature_added_at() -> None:
    drafted = build_manuscript_source_snapshot(
        {
            "literature_sources": [
                {"source_id": "source-a", "title": "Stable paper", "added_at": "first"}
            ]
        }
    )
    reconstructed = build_manuscript_source_snapshot(
        {
            "literature_sources": [
                {"source_id": "source-a", "title": "Stable paper", "added_at": "second"}
            ]
        }
    )

    assert audit_source_freshness(drafted, reconstructed).passed


def test_legacy_snapshot_can_migrate_a_trusted_reconstructed_component() -> None:
    drafted = build_manuscript_source_snapshot(
        {"literature_sources": [{"source_id": "source-a", "title": "Stable"}]}
    )
    current = build_manuscript_source_snapshot(
        {"literature_sources": [{"source_id": "source-a", "title": "Changed hash"}]}
    )

    ordinary = audit_source_freshness(drafted, current)
    migrated = audit_source_freshness(
        drafted,
        current,
        legacy_snapshot_migrated=True,
        compatible_reconstructed_components={"literature_sources"},
    )

    assert not ordinary.passed
    assert migrated.passed
    assert migrated.legacy_snapshot_migrated


def test_reverse_outline_exposes_each_paragraph_job() -> None:
    report = build_reverse_outline(
        _sections(),
        EMPIRICAL_ARTICLE_NARRATIVE,
    )

    assert report.passed
    results = next(
        item for item in report.sections if item.section_key == "results"
    )
    assert results.paragraphs[0].rhetorical_job == "result_orientation"
    assert results.thesis.startswith("The results section")


def test_citation_hygiene_is_separate_from_claim_source_support() -> None:
    evidence_map = EvidenceClaimMap(
        track_id="track-1",
        frozen_conclusion="The bounded conclusion remains unchanged.",
        bindings=[
            EvidenceClaimBinding(
                claim_id="literature-source-a",
                kind="background",
                statement="Source A directly supports the background claim.",
                evidence=[
                    EvidencePointer(
                        path="https://example.test/a",
                        source_id="source-a",
                        evidence_type="verified_literature",
                    )
                ],
                allowed_sections=["introduction"],
                claim_strength="descriptive",
                evidence_status="bound",
            ),
            EvidenceClaimBinding(
                claim_id="literature-source-b",
                kind="background",
                statement="Source B was screened only as context.",
                evidence=[
                    EvidencePointer(
                        path="https://example.test/b",
                        source_id="source-b",
                        evidence_type="verified_literature",
                    )
                ],
                allowed_sections=["related_work"],
                claim_strength="descriptive",
                evidence_status="context_only",
            ),
        ],
        verified_source_ids=["source-a", "source-b"],
        forbidden_moves=[],
        source_registry_sha256="a" * 64,
    )
    report = audit_manuscript_citations(
        sections={
            "introduction": "A supported statement [source-a].",
            "related_work": "A merely screened statement [source-b].",
        },
        evidence_claim_map=evidence_map,
        literature_sources=[
            SimpleNamespace(source_id="source-a"),
            SimpleNamespace(source_id="source-b"),
        ],
    )

    assert report.bibliography_hygiene_passed
    assert not report.claim_source_support_passed
    assert report.rejected_or_unverified_count == 1
    assert {item.status for item in report.support_items} == {
        "supported",
        "unverified",
    }


def test_unknown_citation_fails_bibliography_hygiene() -> None:
    evidence_map = EvidenceClaimMap(
        track_id="track-1",
        frozen_conclusion="The bounded conclusion remains unchanged.",
        bindings=[],
        verified_source_ids=[],
        forbidden_moves=[],
        source_registry_sha256="b" * 64,
    )
    report = audit_manuscript_citations(
        sections={"introduction": "Unknown source [source-x]."},
        evidence_claim_map=evidence_map,
        literature_sources=[],
    )

    assert not report.bibliography_hygiene_passed
    assert report.unknown_source_ids == ["source-x"]
