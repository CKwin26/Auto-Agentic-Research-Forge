import json
from pathlib import Path

from research_forge.nuwa_panel import (
    NuwaPersonaReview,
    NuwaVote,
    aggregate_nuwa_panel,
    build_nuwa_jobs,
    build_nuwa_packet,
    validate_nuwa_review,
)
from research_forge.paper_authoring import (
    EvidenceClaimBinding,
    EvidenceClaimMap,
    EvidencePointer,
)


def _map(tmp_path: Path) -> EvidenceClaimMap:
    evidence_path = tmp_path / "result.json"
    evidence_path.write_text(
        json.dumps(
            {
                "metric": 0.81,
                "task": "hidden-task",
                "seed": 7,
                "arm": "treatment",
                "verdict": "supported",
            }
        ),
        encoding="utf-8",
    )
    return EvidenceClaimMap(
        track_id="track-1",
        frozen_conclusion="The registered estimate is 0.81.",
        bindings=[
            EvidenceClaimBinding(
                claim_id="claim-result",
                kind="result_metric",
                statement="The registered estimate is 0.81.",
                evidence=[
                    EvidencePointer(
                        path="result.json",
                        sha256=None,
                        json_path=None,
                        evidence_type="project_artifact",
                    )
                ],
                allowed_sections=["results"],
                claim_strength="descriptive",
                evidence_status="bound",
            )
        ],
        verified_source_ids=[],
        forbidden_moves=[],
        source_registry_sha256="a" * 64,
    )


def _review(persona: str, packet, verdict: str, modes: list[str]):
    decisive = [] if verdict == "abstain" else ["metric=0.81"]
    return NuwaPersonaReview(
        panel_id=packet.panel_id,
        persona_id=persona,
        source_sample_sha256=packet.source_sample_sha256,
        blinded=True,
        items=[
            NuwaVote(
                audit_id=packet.items[0].audit_id,
                verdict=verdict,
                rationale="The packet provides a directly checkable metric.",
                failure_modes=modes,
                decisive_evidence=decisive,
            )
        ],
    )


def test_packet_is_blinded_and_routes_exactly_three_personas(tmp_path: Path) -> None:
    packet = build_nuwa_packet(
        _map(tmp_path),
        root=tmp_path,
        panel_id="panel-test",
    )
    jobs = build_nuwa_jobs(packet)

    assert set(jobs) == {"feynman", "tukey", "shannon"}
    assert all(len(job["items"]) == 1 for job in jobs.values())
    content = packet.items[0].linked_evidence[0].content
    assert "hidden-task" not in content
    assert "treatment" not in content
    assert "supported" not in content
    assert '"metric":0.81' in content


def test_hard_veto_and_mixed_vote_rules_are_deterministic(tmp_path: Path) -> None:
    packet = build_nuwa_packet(
        _map(tmp_path),
        root=tmp_path,
        panel_id="panel-test",
    )
    reviews = [
        _review("feynman", packet, "supported", []),
        _review(
            "tukey",
            packet,
            "unsupported",
            ["exact_metric_mismatch"],
        ),
        _review("shannon", packet, "supported", []),
    ]
    jobs = build_nuwa_jobs(packet)
    for review in reviews:
        validate_nuwa_review(review, job=jobs[review.persona_id])

    report = aggregate_nuwa_panel(
        packet,
        reviews,
        job_hashes={persona: "b" * 64 for persona in jobs},
    )

    claim = report.claims[0]
    assert claim.final_verdict == "unsupported"
    assert claim.hard_veto_failure_modes == ["exact_metric_mismatch"]
    assert claim.disagreement
    assert claim.requires_human_adjudication
    assert report.human_adjudication_queue == [packet.items[0].audit_id]


def test_single_unsupported_without_hard_veto_abstains(tmp_path: Path) -> None:
    packet = build_nuwa_packet(
        _map(tmp_path),
        root=tmp_path,
        panel_id="panel-test",
    )
    reviews = [
        _review("feynman", packet, "supported", []),
        _review("tukey", packet, "unsupported", ["overgeneralization"]),
        _review("shannon", packet, "supported", []),
    ]
    report = aggregate_nuwa_panel(
        packet,
        reviews,
        job_hashes={persona: "b" * 64 for persona in build_nuwa_jobs(packet)},
    )
    assert report.claims[0].final_verdict == "abstain"
    assert report.claims[0].requires_human_adjudication


def test_long_registered_protocol_is_partitioned_without_dropping_binding(
    tmp_path: Path,
) -> None:
    evidence_map = _map(tmp_path)
    long_statement = "; ".join(
        f"Registered design field {index} has frozen value value-{index}"
        for index in range(500)
    ) + "."
    binding = evidence_map.bindings[0].model_copy(
        update={
            "claim_id": "claim-long-protocol",
            "kind": "operational",
            "statement": long_statement,
        }
    )
    packet = build_nuwa_packet(
        evidence_map.model_copy(update={"bindings": [binding]}),
        root=tmp_path,
        panel_id="panel-long-protocol",
    )

    assert len(packet.items) > 1
    assert all(len(item.claim_text) <= 8_000 for item in packet.items)
    assert set(packet.audit_to_claim_id.values()) == {"claim-long-protocol"}
    assert all(item.linked_evidence for item in packet.items)
    assert "Registered design field 0" in packet.items[0].claim_text
    assert "value-499" in packet.items[-1].claim_text
