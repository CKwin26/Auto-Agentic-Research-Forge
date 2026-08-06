from __future__ import annotations

import hashlib
import json

import pytest

from research_forge.scientific_figure_engine import (
    FigureDataBinding,
    FigureSemanticValidator,
    ScientificFigureSpec,
    profile_figure_adapter,
    render_and_accept_figure,
    validate_profile_figure_set,
)


def _binding(rows: list[dict[str, object]]) -> FigureDataBinding:
    payload = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return FigureDataBinding(
        binding_id="figure-data-test-01",
        source_artifact_ids=["artifact-evaluation-01"],
        source_paths=["stage3/evaluation.json"],
        source_hashes={"stage3/evaluation.json": "a" * 64},
        data_hash=hashlib.sha256(payload).hexdigest(),
        data_columns=list(rows[0]),
        rows=rows,
    )


def _effect_spec(binding: FigureDataBinding, **updates) -> ScientificFigureSpec:
    values = {
        "figure_id": "fig-effect-main",
        "profile_id": "independent_group_comparison_v1",
        "estimand_id": "estimand-main",
        "figure_type": "effect_interval",
        "source_artifact_ids": binding.source_artifact_ids,
        "data_hash": binding.data_hash,
        "data_columns": binding.data_columns,
        "point_estimate_field": "effect",
        "lower_interval_field": "ci_lower",
        "upper_interval_field": "ci_upper",
        "reference_value": 0.0,
        "group_encoding": "outcome",
        "unit": "quality-score points",
        "denominator": "160 independently assigned cases",
        "confidence_or_credible_level": 0.95,
        "interval_kind": "confidence",
        "effect_measure": "difference",
        "caption_claim_ids": ["claim-main"],
        "caption": "Effect with full interval, unit, and analyzed denominator by outcome.",
        "alt_text": "The estimated quality difference and its complete confidence interval.",
        "renderer": "builtin_interval_svg",
        "renderer_version": "2.0.0",
        "output_formats": ["svg"],
    }
    values.update(updates)
    return ScientificFigureSpec(**values)


def test_interval_semantics_fail_closed_when_lower_endpoint_is_used_as_point() -> None:
    binding = _binding(
        [{"outcome": "Quality", "effect": -0.2, "ci_lower": -0.1, "ci_upper": 0.3}]
    )
    spec = _effect_spec(binding)
    report = FigureSemanticValidator.validate(spec, binding)
    assert report.passed is False
    assert "ordered_complete_intervals" in {
        issue.code.casefold() for issue in report.issues
    }


def test_ratio_effect_requires_one_as_the_null_reference() -> None:
    binding = _binding(
        [{"outcome": "Event", "effect": 1.2, "ci_lower": 0.9, "ci_upper": 1.6}]
    )
    with pytest.raises(ValueError, match="null reference 1"):
        _effect_spec(
            binding,
            effect_measure="hazard_ratio",
            reference_value=0.0,
            unit="hazard ratio",
        )


def test_credible_interval_cannot_be_captioned_as_confidence_interval() -> None:
    binding = _binding(
        [{"outcome": "Quality", "effect": 0.2, "ci_lower": -0.1, "ci_upper": 0.5}]
    )
    spec = _effect_spec(
        binding,
        interval_kind="credible",
        effect_measure="posterior_difference",
        caption="Posterior effect with full confidence interval and unit.",
    )
    report = FigureSemanticValidator.validate(spec, binding)
    assert report.passed is False
    assert any(issue.code == "INTERVAL_LABEL_SEMANTICS" for issue in report.issues)


def test_profile_adapter_rejects_generic_factorial_effect_only_plot() -> None:
    binding = _binding(
        [{"outcome": "Interaction", "effect": 0.2, "ci_lower": 0.1, "ci_upper": 0.3}]
    )
    spec = _effect_spec(
        binding,
        profile_id="factorial_experiment_v1",
    )
    issues = validate_profile_figure_set("factorial_experiment_v1", [spec])
    assert {item.code for item in issues} == {
        "PROFILE_REQUIRED_FIGURE_MISSING"
    }
    assert {item.figure_id for item in issues} == {
        "fig-required-factorial-interaction",
        "fig-required-factorial-effect-forest",
    }


def test_each_publication_profile_has_a_typed_figure_adapter() -> None:
    profile_ids = {
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
    for profile_id in profile_ids:
        adapter = profile_figure_adapter(profile_id)
        assert adapter.profile_id == profile_id
        assert adapter.requirements


def test_rendered_figure_preserves_data_spec_source_and_output_hashes(tmp_path) -> None:
    binding = _binding(
        [
            {"outcome": "Quality", "effect": 0.2, "ci_lower": 0.1, "ci_upper": 0.3},
            {"outcome": "Completion", "effect": 0.1, "ci_lower": -0.1, "ci_upper": 0.2},
        ]
    )
    spec = _effect_spec(binding)
    report = render_and_accept_figure(spec, binding, tmp_path)
    assert report.accepted is True
    assert report.output_hashes
    assert (tmp_path / "fig-effect-main.svg").is_file()
    assert (tmp_path / "fig-effect-main.spec.json").is_file()
    assert (tmp_path / "fig-effect-main.data.json").is_file()
    assert (tmp_path / "fig-effect-main.acceptance.json").is_file()


def test_rendered_figure_exports_svg_pdf_and_png(tmp_path) -> None:
    binding = _binding(
        [{"outcome": "Quality", "effect": 0.2, "ci_lower": 0.1, "ci_upper": 0.3}]
    )
    spec = _effect_spec(
        binding, output_formats=["svg", "pdf", "png"]
    )
    report = render_and_accept_figure(spec, binding, tmp_path)

    assert report.accepted is True
    assert set(report.output_hashes) == {
        "fig-effect-main.svg",
        "fig-effect-main.pdf",
        "fig-effect-main.png",
    }
    assert all(
        (tmp_path / filename).stat().st_size > 100
        for filename in report.output_hashes
    )
