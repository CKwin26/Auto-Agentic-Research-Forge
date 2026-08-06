from __future__ import annotations

from research_forge.profile_publication import (
    MINIMUM_REPRODUCIBLE_RESULT_SCHEMAS,
    PROFILE_PUBLICATION_ADAPTERS,
    ReproducibleResultField,
    validate_minimum_reproducible_result,
)


PROFILE_IDS = {
    "independent_group_comparison_v1",
    "factorial_experiment_v1",
    "longitudinal_repeated_measures_v1",
    "survival_analysis_v1",
    "causal_inference_v1",
    "noninferiority_equivalence_v1",
    "bayesian_inference_v1",
    "online_ab_test_v1",
    "open_generation_human_rating_v1",
    "multiplicity_control_v1",
}


def test_all_ten_profiles_have_publication_and_recomputation_contracts() -> None:
    assert PROFILE_IDS == set(MINIMUM_REPRODUCIBLE_RESULT_SCHEMAS)
    assert PROFILE_IDS == set(PROFILE_PUBLICATION_ADAPTERS)
    for profile_id in PROFILE_IDS:
        adapter = PROFILE_PUBLICATION_ADAPTERS[profile_id]
        schema = MINIMUM_REPRODUCIBLE_RESULT_SCHEMAS[profile_id]
        assert set(schema.required_fields) <= set(adapter.minimum_reproducibility_fields)
        assert adapter.required_figures
        assert adapter.required_methods_fields
        assert adapter.required_results_fields
        assert adapter.forbidden_claims


def test_incomplete_recomputation_package_can_only_produce_evidence_boundary() -> None:
    report = validate_minimum_reproducible_result(
        study_id="study-01",
        profile_id="independent_group_comparison_v1",
        fields=[
            ReproducibleResultField(
                field="effect",
                artifact_id="evaluation-01",
                artifact_sha256="a" * 64,
            )
        ],
        artifact_kinds=["evaluation"],
    )
    assert report.independently_recalculable is False
    assert report.submission_ready_results_allowed is False
    assert "ci_lower" in report.missing_fields
    assert "formal_output" in report.missing_artifact_kinds


def test_complete_recomputation_package_allows_results_manuscript() -> None:
    profile_id = "multiplicity_control_v1"
    schema = MINIMUM_REPRODUCIBLE_RESULT_SCHEMAS[profile_id]
    fields = [
        ReproducibleResultField(
            field=field,
            artifact_id="statistics-01",
            artifact_sha256="b" * 64,
        )
        for field in schema.required_fields
    ]
    report = validate_minimum_reproducible_result(
        study_id="study-01",
        profile_id=profile_id,
        fields=fields,
        artifact_kinds=schema.required_artifact_kinds,
    )
    assert report.independently_recalculable is True
    assert report.submission_ready_results_allowed is True
    assert not report.missing_fields
    assert not report.missing_artifact_kinds
