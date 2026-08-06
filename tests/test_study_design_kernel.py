from __future__ import annotations

import copy
import json
import math
from types import SimpleNamespace

import pytest

from research_forge.study_design import (
    blocking_issues_for_composable_contract,
    blocking_issues_for_realized_data,
    build_authority_mutation_report,
    compile_analysis_plan,
    inference_module_catalog,
    produce_claim_envelope,
    required_negative_acceptance_cases,
    study_design,
    study_design_catalog,
    validate_composable_contract,
)
from research_forge.study_design.acceptance import (
    DefaultStudyDesignAcceptanceEvaluator,
    build_acceptance_report,
    verify_acceptance_report,
)
from research_forge.study_design.inference.bayesian import beta_binomial_difference
from research_forge.study_design.inference.bayesian import BayesianInference
from research_forge.study_design.inference.multiplicity import adjust_p_values
from research_forge.study_design.inference.noninferiority import interval_decision
from research_forge.study_design.reference import (
    recalculate_continuous_primary,
    recalculate_independent_group,
)
from research_forge.study_design.paper_lab import (
    PACKAGE_NAMES,
    append_human_review,
    inspect_profile_paper_package,
)
from research_forge.study_design.paper_case import (
    generate_independent_group_paper_package,
)
from research_forge.study_design.evidence import validate_claim_envelope_authority
from research_forge.study_design.workflow_acceptance import (
    prepare_independent_group_workflow_acceptance,
)
from research_forge.study_design.workflow_evidence import derive_workflow_receipts
from research_forge.profiles.stage_four_evidence import (
    build_stage_four_evidence_from_repository,
)
from research_forge.workflow_domain import (
    ArtifactStatus,
    EntryMode,
    GateStatus,
    GateType,
    WorkflowRepository,
)


def plan_payload() -> dict:
    return {
        "schema_version": 1,
        "study_design_id": "independent_group_comparison_v1",
        "study_design_version": "1",
        "unit_structure": {
            "row_unit": "one participant outcome record",
            "observation_unit": "participant",
            "assignment_unit": "participant",
            "analysis_unit": "participant",
            "variance_unit": "participant",
            "independent_unit": "participant",
        },
        "allocation": {
            "mechanism": "randomized",
            "evidence": "Frozen allocation ledger with concealed random sequence.",
        },
        "arms": [
            {"arm_id": "control", "label": "Standard procedure", "role": "control", "definition": "Existing procedure"},
            {"arm_id": "treatment", "label": "Assisted procedure", "role": "treatment", "definition": "Procedure with the registered assistance"},
        ],
        "outcomes": [
            {"outcome_id": "quality", "label": "Task quality", "kind": "continuous", "field": "quality", "role": "primary", "beneficial_direction": "higher"},
            {"outcome_id": "completion", "label": "Successful completion", "kind": "binary", "field": "completed", "role": "secondary", "beneficial_direction": "higher", "event_value": True},
        ],
        "estimands": [
            {"estimand_id": "quality_difference", "outcome_id": "quality", "treatment_arm_id": "treatment", "control_arm_id": "control", "effect_measure": "mean_difference", "analysis_population": "complete_case"},
            {"estimand_id": "completion_difference", "outcome_id": "completion", "treatment_arm_id": "treatment", "control_arm_id": "control", "effect_measure": "risk_difference", "analysis_population": "complete_case"},
        ],
        "estimator_plan": {
            "quality": {"estimator_id": "welch_mean_difference_v1", "equal_variance_assumed": False},
            "completion": {"estimator_id": "wald_risk_difference_v1", "equal_variance_assumed": False},
        },
        "inference_plan": {
            "quality": {"method": "Welch t interval", "confidence_level": 0.95},
            "completion": {"method": "Wald risk-difference interval", "confidence_level": 0.95},
        },
        "missingness": {"policy": "complete_case", "denominator_rule": "Outcome-specific observed participants by arm", "exclusion_reasons_required": True},
        "multiplicity": {"method": "holm", "family_id": "confirmatory_family", "hypothesis_ids": ["quality", "completion"], "alpha_or_q": 0.05},
        "decision_rules": {
            "quality": {"mode": "superiority", "effect_measure": "mean_difference", "beneficial_direction": "higher", "confidence_level": 0.95},
            "completion": {"mode": "superiority", "effect_measure": "risk_difference", "beneficial_direction": "higher", "confidence_level": 0.95},
        },
        "claim_boundary": {"generalization": "Participants satisfying the frozen eligibility criteria.", "reproduction_materials": ["frozen plan", "de-identified analysis rows", "reference recalculator"]},
    }


def contract(payload: dict | None = None):
    return SimpleNamespace(
        study_design={"id": "independent_group_comparison_v1", "version": "1"},
        inference_modules=[],
        study_design_spec=payload or plan_payload(),
    )


def fixture_rows() -> list[dict]:
    rows = []
    for index in range(80):
        rows.append({"subject_id": f"c-{index:03d}", "arm": "control", "quality": None if index in {2, 17} else 50 + (index % 11) * 0.7, "completed": index % 5 != 0})
        rows.append({"subject_id": f"t-{index:03d}", "arm": "treatment", "quality": None if index in {9, 33, 70} else 53 + (index % 13) * 0.65, "completed": index % 8 != 0})
    return rows


def test_catalog_separates_execution_design_and_inference_modules():
    designs = {item["design_id"]: item for item in study_design_catalog()}
    modules = {item["module_id"]: item for item in inference_module_catalog()}
    assert designs["independent_group_comparison_v1"]["formal_execution_supported"] is True
    assert designs["independent_group_comparison_v1"]["maturity"] == "c2_dry_run"
    assert designs["factorial_experiment_v1"]["maturity"] == "c2_dry_run"
    assert designs["factorial_experiment_v1"]["verified_maturity"] == "c3_real_fixture"
    assert designs["factorial_experiment_v1"]["formal_execution_supported"] is True
    assert designs["longitudinal_repeated_measures_v1"]["maturity"] == "c2_dry_run"
    assert designs["longitudinal_repeated_measures_v1"]["formal_execution_supported"] is True
    assert designs["survival_analysis_v1"]["maturity"] == "c2_dry_run"
    assert designs["survival_analysis_v1"]["formal_execution_supported"] is True
    assert designs["causal_inference_v1"]["maturity"] == "c2_dry_run"
    assert designs["causal_inference_v1"]["formal_execution_supported"] is True
    assert designs["online_ab_test_v1"]["maturity"] == "c2_dry_run"
    assert (
        designs["online_ab_test_v1"]["verified_maturity"]
        == "c3_real_fixture"
    )
    assert designs["online_ab_test_v1"]["formal_execution_supported"] is True
    assert designs["open_generation_human_rating_v1"]["maturity"] == "c2_dry_run"
    assert (
        designs["open_generation_human_rating_v1"]["verified_maturity"]
        == "c3_real_fixture"
    )
    assert designs["open_generation_human_rating_v1"]["formal_execution_supported"] is True
    assert modules["noninferiority_equivalence_v1"]["formal_execution_supported"] is True
    assert modules["multiplicity_control_v1"]["formal_execution_supported"] is True
    assert modules["noninferiority_equivalence_v1"]["maturity"] == "c2_dry_run"
    assert (
        modules["noninferiority_equivalence_v1"]["verified_maturity"]
        == "c3_real_fixture"
    )
    assert modules["multiplicity_control_v1"]["maturity"] == "c2_dry_run"
    assert modules["bayesian_inference_v1"]["maturity"] == "c2_dry_run"
    assert (
        modules["bayesian_inference_v1"]["verified_maturity"]
        == "c3_real_fixture"
    )
    assert modules["bayesian_inference_v1"]["formal_execution_supported"] is True


def test_black_box_160_subject_e2e_and_reference_agreement():
    plan = compile_analysis_plan(contract())
    rows = fixture_rows()
    result = study_design(plan.study_design_id).evaluate(plan, rows)
    reference = recalculate_continuous_primary(plan, rows)
    primary = next(item for item in result.outcomes if item.outcome_id == "quality")
    assert len(rows) == 160
    assert result.eligible is True
    assert primary.denominator == 155
    assert primary.missing_count == 5
    assert math.isclose(primary.effect, reference["effect"], rel_tol=1e-12)
    assert primary.confidence_interval == pytest.approx(reference["confidence_interval"])
    envelope = produce_claim_envelope(plan, result)
    rendered = " ".join(envelope.effect_estimates + envelope.permitted_claims + envelope.prohibited_claims)
    assert "quality" in rendered.lower()
    assert not any(token in rendered for token in ("quality_difference", "run-", "C:\\", "study-"))


def test_reference_recalculates_all_outcomes_multiplicity_and_decisions():
    plan = compile_analysis_plan(contract())
    rows = fixture_rows()
    production = study_design(plan.study_design_id).evaluate(plan, rows)
    reference = recalculate_independent_group(plan, rows)
    by_id = {item.outcome_id: item for item in production.outcomes}
    assert set(reference["outcomes"]) == {"quality", "completion"}
    for outcome_id, expected in reference["outcomes"].items():
        observed = by_id[outcome_id]
        assert observed.denominator == expected["denominator"]
        assert observed.missing_count == expected["missing_count"]
        assert observed.effect == pytest.approx(expected["effect"])
        assert observed.confidence_interval == pytest.approx(
            expected["confidence_interval"]
        )
        assert observed.adjusted_p_value == pytest.approx(
            expected["adjusted_p_value"]
        )
        assert observed.decision == expected["decision"]
    assert production.primary_decision == reference["primary_decision"]
    binary = next(item for item in production.outcomes if item.kind == "binary")
    assert {
        "risk_difference",
        "risk_ratio",
        "odds_ratio",
    } <= set(binary.details)
    assert set(reference["outcomes"]["completion"]["alternate_effects"]) == {
        "risk_difference",
        "risk_ratio",
        "odds_ratio",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p["arms"].append({"arm_id": "third", "label": "Third", "role": "treatment", "definition": "Third arm"}), "exactly two"),
        (lambda p: p["outcomes"].__setitem__(0, {**p["outcomes"][0], "role": "secondary"}), "exactly one primary"),
        (lambda p: p["unit_structure"].__setitem__("repeated_measure_unit", "participant"), "repeated measures"),
        (lambda p: p["unit_structure"].__setitem__("cluster_unit", "clinic"), "clustered"),
        (lambda p: p["unit_structure"].__setitem__("analysis_unit", "visit"), "must be independent"),
        (lambda p: p["estimator_plan"].__setitem__("completion", {"estimator_id": "welch_mean_difference_v1", "equal_variance_assumed": False}), "cannot use a continuous"),
        (lambda p: p["multiplicity"].__setitem__("method", "no_correction"), "multiplicity"),
        (lambda p: p["decision_rules"].pop("quality"), "each outcome requires"),
    ],
)
def test_contract_mutations_are_detected(mutation, message):
    payload = plan_payload()
    mutation(payload)
    mutated = contract(payload)
    assert any(message in item for item in validate_composable_contract(mutated))
    blockers = blocking_issues_for_composable_contract(mutated)
    assert blockers
    assert all(item.severity == "critical" for item in blockers)
    assert all(item.owner_approval_required is False for item in blockers)
    assert any(message in str(item.observed) for item in blockers)


@pytest.mark.parametrize(
    "bad_rows, expected",
    [
        ([{"subject_id": "same", "arm": "control", "quality": 1, "completed": True}, {"subject_id": "same", "arm": "treatment", "quality": 2, "completed": True}], "both independent arms"),
        ([{"subject_id": "same", "arm": "control", "quality": 1, "completed": True}, {"subject_id": "same", "arm": "control", "quality": 2, "completed": True}], "longitudinal"),
        ([{"subject_id": "one", "arm": "unknown", "quality": 1, "completed": True}], "unregistered arm"),
        ([{"arm": "control", "quality": 1, "completed": True}], "subject_id"),
        ([{"subject_id": "one", "arm": "control", "completed": True}], "outcome field is absent"),
    ],
)
def test_realized_data_negative_routes(bad_rows, expected):
    plan = compile_analysis_plan(contract())
    errors = study_design(plan.study_design_id).validate_realized_data(plan, bad_rows)
    assert any(expected in item for item in errors)
    blockers = blocking_issues_for_realized_data(plan, bad_rows)
    assert blockers
    assert all(item.field_path == "formal_dataset" for item in blockers)
    assert any(expected in str(item.observed) for item in blockers)


def test_noninferiority_and_equivalence_do_not_use_nonsignificance_as_success():
    assert interval_decision((-0.02, 0.03), mode="superiority", direction="higher") == "inconclusive"
    assert interval_decision((-0.02, 0.03), mode="noninferiority", direction="higher", margin=0.05) == "supported"
    assert interval_decision((-0.02, 0.03), mode="equivalence", direction="higher", lower_margin=-0.05, upper_margin=0.05) == "supported"
    assert interval_decision((-0.20, 0.03), mode="equivalence", direction="higher", lower_margin=-0.05, upper_margin=0.05) == "inconclusive"
    with pytest.raises(ValueError):
        interval_decision((-0.02, 0.03), mode="noninferiority", direction="higher")
    with pytest.raises(ValueError):
        interval_decision((-0.02, 0.03), mode="equivalence", direction="higher", lower_margin=0.05, upper_margin=-0.05)


def test_negative_same_arm_estimand_fails_closed():
    payload = plan_payload()
    payload["estimands"][0]["treatment_arm_id"] = "control"
    violations = validate_composable_contract(contract(payload))
    assert any("treatment" in item or "same arm" in item for item in violations)


def test_negative_noninferiority_direction_mismatch_fails_closed():
    payload = plan_payload()
    payload["decision_rules"]["quality"] = {
        "mode": "noninferiority",
        "effect_measure": "mean_difference",
        "beneficial_direction": "lower",
        "margin": 1.0,
        "margin_unit": "quality points",
        "margin_provenance": "owner-approved pilot rule",
        "owner_approved": True,
    }
    violations = validate_composable_contract(contract(payload))
    assert any("contradicts the outcome direction" in item for item in violations)


def test_negative_post_exposure_confirmatory_addition_fails_closed():
    payload = plan_payload()
    payload["multiplicity"]["registered_before_results"] = False
    violations = validate_composable_contract(contract(payload))
    assert any("before formal results" in item for item in violations)


def test_negative_exploratory_outcome_cannot_join_confirmatory_family():
    payload = plan_payload()
    payload["outcomes"].append({
        "outcome_id": "exploratory_speed",
        "label": "Exploratory speed",
        "kind": "continuous",
        "field": "speed",
        "role": "exploratory",
        "beneficial_direction": "higher",
    })
    payload["estimands"].append({
        "estimand_id": "exploratory_speed_difference",
        "outcome_id": "exploratory_speed",
        "treatment_arm_id": "treatment",
        "control_arm_id": "control",
        "effect_measure": "mean_difference",
        "analysis_population": "complete_case",
    })
    payload["estimator_plan"]["exploratory_speed"] = {
        "estimator_id": "welch_mean_difference_v1",
        "equal_variance_assumed": False,
    }
    payload["inference_plan"]["exploratory_speed"] = {
        "method": "Welch t interval",
        "confidence_level": 0.95,
    }
    payload["decision_rules"]["exploratory_speed"] = {
        "mode": "superiority",
        "effect_measure": "mean_difference",
        "beneficial_direction": "higher",
    }
    payload["multiplicity"]["hypothesis_ids"].append("exploratory_speed")
    payload["multiplicity"]["exploratory_hypothesis_ids"] = ["exploratory_speed"]
    violations = validate_composable_contract(contract(payload))
    assert any("exploratory hypotheses" in item for item in violations)


def test_negative_informative_bayesian_prior_requires_owner_approval():
    candidate = SimpleNamespace(
        study_design={"id": "independent_group_comparison_v1", "version": "1"},
        study_design_spec=plan_payload(),
        inference_modules=[{
            "id": "bayesian_inference_v1",
            "version": "1",
            "extension": {
                "prior": {"alpha": 10, "beta": 2},
                "prior_provenance": "historical evidence",
                "informative": True,
                "owner_approved": False,
                "seed": 7,
            },
        }],
    )
    violations = BayesianInference().validate_contract(candidate)
    assert "informative Bayesian prior requires owner approval" in violations


def test_bayesian_contract_freezes_every_reproducibility_parameter():
    candidate = SimpleNamespace(
        inference_modules=[{
            "id": "bayesian_inference_v1",
            "version": "1",
            "extension": {
                "prior": {"family": "Beta", "alpha": 1.0, "beta": 1.0},
                "prior_provenance": "owner-approved before results",
                "informative": False,
                "owner_approved": True,
                "seed": 20260804,
                "draws": 20_000,
                "rope": [-0.02, 0.02],
                "decision_threshold": 0.90,
                "role": "prespecified_sensitivity_only",
            },
        }],
    )
    assert BayesianInference().validate_contract(candidate) == []

    incomplete = SimpleNamespace(
        inference_modules=[{
            "id": "bayesian_inference_v1",
            "version": "1",
            "extension": {
                "prior": {"family": "Beta", "alpha": 1.0, "beta": 1.0},
                "prior_provenance": "owner-approved before results",
                "seed": 20260804,
            },
        }],
    )
    violations = BayesianInference().validate_contract(incomplete)
    assert any("posterior draws" in item for item in violations)
    assert any("ROPE" in item for item in violations)
    assert any("probability threshold" in item for item in violations)
    assert any("sensitivity only" in item for item in violations)


def test_negative_missing_rows_remain_in_frozen_denominator_audit():
    plan = compile_analysis_plan(contract())
    rows = fixture_rows()
    evaluation = study_design(plan.study_design_id).evaluate(plan, rows)
    primary = next(item for item in evaluation.outcomes if item.outcome_id == "quality")
    assert primary.denominator + primary.missing_count == len(rows)
    assert primary.missing_count == 5


def test_negative_stage_four_cannot_upgrade_verdict():
    plan = compile_analysis_plan(contract())
    evaluation = study_design(plan.study_design_id).evaluate(plan, fixture_rows())
    envelope = produce_claim_envelope(plan, evaluation)
    replacement = "refuted" if evaluation.primary_decision != "refuted" else "supported"
    rewritten = envelope.model_copy(update={"scientific_verdict": replacement})
    violations = validate_claim_envelope_authority(evaluation, rewritten)
    assert violations == ["Stage 4 cannot upgrade or rewrite the Scientific Verdict"]


def test_multiplicity_retains_every_registered_result():
    values = {"primary": 0.01, "secondary_a": 0.03, "secondary_b": 0.20}
    for method in ("no_correction", "bonferroni", "holm", "benjamini_hochberg"):
        adjusted = adjust_p_values(values, method)
        assert set(adjusted) == set(values)
        assert all(0 <= value <= 1 for value in adjusted.values())


def test_bayesian_sensitivity_is_seeded_and_recomputable():
    arguments = dict(control_events=30, control_n=80, treatment_events=42, treatment_n=80, prior_alpha=1, prior_beta=1, seed=4421, draws=2000)
    assert beta_binomial_difference(**arguments) == beta_binomial_difference(**arguments)


def test_acceptance_report_invalidates_after_mutation():
    report = build_acceptance_report(
        study_id="study-independent-001", component_id="independent_group_comparison_v1",
        source_hash="a"*64, schema_hash="b"*64, environment_hash="c"*64,
        test_results={"component": True, "e2e": True, "negative": True, "mutation": True},
        independent_agreement={"passed": True, "absolute_error": 0.0},
        paper_audit={"natural_language": True, "authority_preserved": True},
        replay={"passed": True, "same_hash": True}, maturity="c3_real_fixture",
    )
    assert verify_acceptance_report(report)
    assert verify_acceptance_report(
        report,
        expected_authority_context={},
    )
    assert not verify_acceptance_report(
        report,
        expected_authority_context={"estimator_hash": "changed"},
    )
    report["source_hash"] = "d"*64
    assert not verify_acceptance_report(report)


def test_acceptance_maturity_cannot_reach_c3_without_workflow_receipts():
    evaluator = DefaultStudyDesignAcceptanceEvaluator()
    evidence = {
        "positive_tests": {"component": True},
        "negative_tests": {"fail_closed": True},
        "mutation_tests": {"successor_required": True},
        "dry_run_receipt": {"passed": True},
        "formal_workflow_completed": False,
        "canonical_stage_four_completed": False,
        "workflow_receipts": {},
        "independent_recalculator_agreement": {"passed": True},
        "paper_audit": {"authority": True},
        "claim_binding_coverage": 1.0,
    }
    assert evaluator.derive_maturity(evidence).value == "c2_dry_run"


def test_acceptance_report_fails_when_a_negative_or_mutation_case_fails():
    report = build_acceptance_report(
        study_id="study-negative-case",
        component_id="independent_group_comparison_v1",
        source_hash="a" * 64,
        schema_hash="b" * 64,
        environment_hash="c" * 64,
        test_results={"component": True},
        positive_tests={"valid_case": True},
        negative_tests={"cross_arm_duplicate": False},
        mutation_tests={"changed_outcome": True},
        dry_run_receipt={"passed": True},
        independent_agreement={"passed": True},
        paper_audit={"authority": True},
        replay={"passed": True},
        maturity="c2_dry_run",
    )

    assert report["valid"] is False
    assert report["promotion_eligible"] is False


def test_eight_scientific_mutations_require_a_successor_run():
    plan = compile_analysis_plan(contract())
    report = build_authority_mutation_report(plan, fixture_rows())
    assert set(report) == {
        "swap_arm_labels",
        "change_outcome_value",
        "change_primary_outcome",
        "change_noninferiority_margin",
        "change_multiplicity_family",
        "delete_subject",
        "duplicate_subject",
        "change_config_or_seed",
    }
    assert all(item["mutation_detected"] for item in report.values())
    assert all(item["prior_evaluation_invalidated"] for item in report.values())
    assert all(
        item["prior_verdict_cannot_support_mutated_claim"]
        for item in report.values()
    )
    assert all(item["successor_run_required"] for item in report.values())


def test_all_fifteen_required_negative_cases_emit_blocking_issues():
    plan = compile_analysis_plan(contract())
    rows = fixture_rows()
    evaluation = study_design(plan.study_design_id).evaluate(plan, rows)
    envelope = produce_claim_envelope(plan, evaluation)
    report = required_negative_acceptance_cases(
        plan, rows, evaluation, envelope
    )

    assert len(report) == 15
    assert all(item["passed"] for item in report.values())
    assert all(
        item["blocking_issue"]["severity"] == "critical"
        for item in report.values()
    )
    assert all(
        item["blocking_issue"]["repair_class"] == "unresolvable_blocker"
        for item in report.values()
    )


def test_c3_requires_all_18_profile_paper_artifacts(tmp_path):
    for name in PACKAGE_NAMES:
        path = tmp_path / name
        if "." not in path.name:
            path.mkdir(parents=True)
            (path / "manifest.txt").write_text("frozen", encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
    receipts = {
        key: True for key in (
            "idea_intake", "scope_frozen", "profile_qualified",
            "research_contract_completed", "owner_contract_approved",
            "research_contract_frozen", "resources_bound", "dry_run_completed",
            "formal_evaluation_completed", "independent_recalculation_completed",
            "scientific_verdict_frozen", "evidence_claim_map_completed",
            "stage_four_manuscript_completed", "paper_audit_completed",
            "completion_record_verified",
        )
    }
    (tmp_path / "16_profile_acceptance_report.json").write_text(
        json.dumps({
            "valid": True,
            "scientific_verdict": "inconclusive",
            "independent_recalculator_agreement": {"passed": True},
            "paper_audit": {"claims": True},
            "replay": {"passed": True},
            "formal_workflow_completed": True,
            "canonical_stage_four_completed": True,
            "workflow_receipts": receipts,
        }),
        encoding="utf-8",
    )
    (tmp_path / "10_scientific_verdict.json").write_text(
        '{"verdict":"inconclusive"}', encoding="utf-8"
    )
    package = inspect_profile_paper_package(
        tmp_path, profile_id="independent_group_comparison_v1",
        study_id="study-paper-c3",
    )
    assert package.complete
    assert package.derived_maturity.value == "c3_real_fixture"
    (tmp_path / "14_manuscript.pdf").unlink()
    incomplete = inspect_profile_paper_package(
        tmp_path, profile_id="independent_group_comparison_v1",
        study_id="study-paper-c3",
    )
    assert not incomplete.complete
    assert incomplete.derived_maturity.value == "c2_dry_run"


def test_complete_direct_package_cannot_claim_c3_without_workflow_receipts(tmp_path):
    package = generate_independent_group_paper_package(tmp_path / "direct")
    assert package.complete
    assert package.automatic_acceptance == "fail"
    assert package.formal_workflow_completed is False
    assert package.canonical_stage_four_completed is False
    assert package.paper_audit_passed is False
    assert package.derived_maturity.value == "c2_dry_run"


def test_formal_acceptance_uses_canonical_workflow_and_queues_stage_four(tmp_path):
    prepared = prepare_independent_group_workflow_acceptance(tmp_path / "formal")
    repository = WorkflowRepository(prepared["workflow_repository"])
    study_id = prepared["study_id"]
    study = repository.load_study(study_id)

    assert study.entry_mode is EntryMode.IDEA_TO_PAPER
    assert repository.latest_scope_contract(study_id).status is ArtifactStatus.FROZEN
    assert repository.latest_research_contract(study_id).status is ArtifactStatus.FROZEN
    assert any(
        gate.gate_type is GateType.RESEARCH_CONTRACT
        and gate.status is GateStatus.APPROVED
        for gate in repository.list_gates(study_id)
    )
    assert len(repository.list_result_envelopes(study_id)) == 2
    evaluations = repository.list_evaluation_records(study_id)
    assert {item.metric_name for item in evaluations} == {
        "Task quality",
        "Successful completion",
    }
    assert all(
        set(evaluation.result_ids)
        == {item.result_id for item in repository.list_result_envelopes(study_id)}
        for evaluation in evaluations
    )
    assert len(repository.list_stage3_completions(study_id)) == 1
    assert prepared["stage_four_steps"] == 31

    receipts = derive_workflow_receipts(repository, study_id)
    assert all(
        receipts[key]
        for key in (
            "idea_intake",
            "scope_frozen",
            "profile_qualified",
            "research_contract_completed",
            "owner_contract_approved",
            "research_contract_frozen",
            "resources_bound",
            "dry_run_completed",
            "formal_evaluation_completed",
            "independent_recalculation_completed",
            "scientific_verdict_frozen",
        )
    )
    assert not receipts["stage_four_manuscript_completed"]
    assert not receipts["completion_record_verified"]

    handoff = build_stage_four_evidence_from_repository(repository, study_id)
    statements = [
        binding.statement
        for binding in handoff.evidence_claim_map.bindings
    ]
    joined = "\n".join(statements)
    assert "registered comparison count was 0" not in joined
    assert "confirmatory_used" not in joined
    assert "fixed operation budget" in joined
    assert "Task quality" in joined
    assert "Successful completion" in joined
    assert "standard procedure" in joined


def test_human_review_is_append_only_and_cannot_change_machine_verdict(tmp_path):
    base = {
        "package_id": "package-001", "profile_id": "independent_group_comparison_v1",
        "reviewer": "project owner", "scientific_question_reasonable": True,
        "experimental_design_reasonable": True, "statistics_correct": True,
        "result_expression_accurate": True, "conclusion_within_evidence": True,
        "paper_readable": True, "figures_acceptable": True,
        "needs_revision": False, "decision": "accepted", "comments": "Reviewed.",
        "machine_verdict_at_review": "inconclusive",
    }
    first = append_human_review(tmp_path, base)
    second = append_human_review(tmp_path, {**base, "reviewer": "second reviewer", "decision": "rejected", "comments": "Needs work."})
    lines = (tmp_path / "profile-paper-lab" / "human_reviews.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert first.machine_verdict_at_review == second.machine_verdict_at_review == "inconclusive"


@pytest.mark.parametrize(
    ("case_kind", "expected_verdict"),
    [
        ("normal", "supported"),
        ("null_or_weak", "inconclusive"),
        ("invalid", "unverifiable"),
    ],
)
def test_profile_black_box_cases_generate_complete_paper_packages(
    tmp_path, case_kind, expected_verdict
):
    package = generate_independent_group_paper_package(
        tmp_path / case_kind, case_kind=case_kind
    )
    assert package.complete
    assert package.scientific_verdict == expected_verdict
    assert package.automatic_acceptance == "fail"
    assert package.independent_recalculation_passed
    assert package.paper_audit_passed is False
    assert package.derived_maturity.value == "c2_dry_run"
    assert (tmp_path / case_kind / "14_manuscript.pdf").stat().st_size > 1000
