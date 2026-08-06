from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_forge.profile_figure_data import build_profile_figure_data_bundle
from research_forge.scientific_figure_engine import render_and_accept_figure
from research_forge.study_design.causal_case import causal_acceptance_plan, causal_acceptance_rows
from research_forge.study_design.designs.causal import CausalInference
from research_forge.study_design.designs.factorial import FactorialExperiment
from research_forge.study_design.designs.human_rating import OpenGenerationHumanRating
from research_forge.study_design.designs.longitudinal import LongitudinalRepeatedMeasures
from research_forge.study_design.designs.online_ab import OnlineABTest
from research_forge.study_design.designs.survival import SurvivalAnalysis
from research_forge.study_design.factorial_case import factorial_acceptance_plan, factorial_acceptance_rows
from research_forge.study_design.human_rating_case import human_rating_acceptance_plan, human_rating_acceptance_rows
from research_forge.study_design.inference.bayesian import beta_binomial_difference
from research_forge.study_design.longitudinal_case import longitudinal_acceptance_plan, longitudinal_acceptance_rows
from research_forge.study_design.online_ab_case import online_ab_acceptance_plan, online_ab_acceptance_rows
from research_forge.study_design.registry import study_design
from research_forge.study_design.schemas import AnalysisPlan
from research_forge.study_design.survival_case import survival_acceptance_plan, survival_acceptance_rows
from research_forge.study_design.workflow_acceptance import independent_group_acceptance_plan_rows


def _sources() -> tuple[list[str], list[str], dict[str, str]]:
    return ["artifact-evaluation-formal"], ["stage3/evaluation.json"], {"stage3/evaluation.json": "a" * 64}


def _build(profile_id, plan, rows, evaluation, *, records=None):
    ids, paths, hashes = _sources()
    return build_profile_figure_data_bundle(
        study_id="study-profile-figures",
        profile_id=profile_id,
        evaluation=evaluation.model_dump(mode="json"),
        research_contract_spec=plan.model_dump(mode="json"),
        formal_rows=rows,
        source_artifact_ids=ids,
        source_paths=paths,
        source_hashes=hashes,
        evaluation_records=records or [],
    )


@pytest.mark.parametrize(
    ("profile_id", "plan_factory", "rows_factory", "profile"),
    [
        ("factorial_experiment_v1", factorial_acceptance_plan, factorial_acceptance_rows, FactorialExperiment()),
        ("longitudinal_repeated_measures_v1", longitudinal_acceptance_plan, longitudinal_acceptance_rows, LongitudinalRepeatedMeasures()),
        ("survival_analysis_v1", survival_acceptance_plan, survival_acceptance_rows, SurvivalAnalysis()),
        ("causal_inference_v1", causal_acceptance_plan, causal_acceptance_rows, CausalInference()),
        ("online_ab_test_v1", online_ab_acceptance_plan, online_ab_acceptance_rows, OnlineABTest()),
        ("open_generation_human_rating_v1", human_rating_acceptance_plan, human_rating_acceptance_rows, OpenGenerationHumanRating()),
    ],
)
def test_profile_specific_stage3_figure_data_is_complete(
    profile_id, plan_factory, rows_factory, profile
):
    raw_plan = plan_factory()
    plan = raw_plan if isinstance(raw_plan, AnalysisPlan) else AnalysisPlan.model_validate(raw_plan)
    rows = rows_factory()
    evaluation = profile.evaluate(plan, rows)
    bundle = _build(profile_id, plan, rows, evaluation)

    assert bundle.complete, [item.message for item in bundle.blocking_issues]
    assert bundle.specs
    assert len(bundle.specs) == len(bundle.bindings)
    assert all(set(spec.output_formats) == {"svg", "pdf", "png"} for spec in bundle.specs)


@pytest.mark.parametrize(
    "variant,profile_id",
    [
        ("superiority", "independent_group_comparison_v1"),
        ("equivalence", "noninferiority_equivalence_v1"),
        ("multiplicity", "multiplicity_control_v1"),
    ],
)
def test_independent_family_profiles_have_distinct_figure_contracts(variant, profile_id):
    plan, rows = independent_group_acceptance_plan_rows(variant)
    evaluation = study_design("independent_group_comparison_v1").evaluate(plan, rows)
    bundle = _build(profile_id, plan, rows, evaluation)
    assert bundle.complete, [item.message for item in bundle.blocking_issues]


def test_bayesian_profile_uses_frozen_posterior_settings():
    plan, rows = independent_group_acceptance_plan_rows("bayesian")
    evaluation = study_design("independent_group_comparison_v1").evaluate(plan, rows)
    completion = next(item for item in evaluation.outcomes if item.outcome_id == "completion")
    control = completion.arm_statistics["control"]
    treatment = completion.arm_statistics["treatment"]
    posterior = beta_binomial_difference(
        control_events=int(control["events"]),
        control_n=int(control["n"]),
        treatment_events=int(treatment["events"]),
        treatment_n=int(treatment["n"]),
        prior_alpha=1.0,
        prior_beta=1.0,
        seed=20260804,
        draws=20_000,
        rope=(-0.02, 0.02),
    )
    record = {
        "metric_name": "Bayesian sensitivity: successful completion",
        "statistical_rule": {
            "prior": posterior["prior"],
            "seed": posterior["seed"],
            "draws": posterior["draws"],
            "rope": posterior["rope"],
        },
        "contrast_estimates": {"posterior_sensitivity": posterior},
    }
    bundle = _build(
        "bayesian_inference_v1", plan, rows, evaluation, records=[record]
    )
    assert bundle.complete, [item.message for item in bundle.blocking_issues]
    assert bundle.specs[0].interval_kind == "credible"


def test_every_profile_figure_renders_all_formats(tmp_path: Path):
    plan = AnalysisPlan.model_validate(factorial_acceptance_plan())
    rows = factorial_acceptance_rows()
    evaluation = FactorialExperiment().evaluate(plan, rows)
    bundle = _build("factorial_experiment_v1", plan, rows, evaluation)
    for spec, binding in zip(bundle.specs, bundle.bindings, strict=True):
        report = render_and_accept_figure(spec, binding, tmp_path / spec.figure_id)
        assert report.accepted, json.dumps(report.model_dump(mode="json"), indent=2)
        assert {Path(name).suffix for name in report.output_hashes} == {".svg", ".pdf", ".png"}
