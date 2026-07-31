from research_forge.generic_mvp import run_generic_feasibility_mvp


def test_generic_mvp_builds_experiment_when_local_resources_are_absent() -> None:
    report = run_generic_feasibility_mvp(
        comparison_frame={
            "comparator": "documented current pipeline",
            "intervention": "bounded proposed pipeline",
            "primary_outcome": "evidence coverage rate",
            "secondary_outcomes": ["execution consistency"],
            "unit_of_analysis": "one frozen retrieval case",
        },
        primary_metric="evidence_coverage_rate",
        metric_direction="maximize",
        denominator="all eligible frozen retrieval cases",
        resource_candidates=[],
    )

    assert report["status"] == "verified"
    assert len(report["smoke_cases"]) == 3
    assert all(item["passed"] for item in report["smoke_cases"])
    assert report["scientific_evidence_eligible"] is False
    assert (
        report["resource_routes"][1]["status"]
        == "available_after_owner_authorized_retrieval"
    )
    assert report["resource_routes"][2]["status"] == (
        "verified_for_stage2_smoke"
    )
    assert report["resource_sufficiency"][
        "formal_stage3_resources_frozen"
    ] is False
