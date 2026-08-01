import json
from pathlib import Path

from research_forge.contract_compiler import (
    compile_research_contract,
    contract_amendment_proposal,
    dry_run_compiled_contract,
)
from research_forge.workflow_domain import (
    Hypothesis,
    HypothesisRole,
    ProtocolStatus,
    ResearchContractVersion,
    Stage3Profile,
)


def _contract() -> ResearchContractVersion:
    return ResearchContractVersion(
        study_id="study-contract-compiler",
        version=1,
        scope_version=1,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hypothesis-primary",
                statement="The treatment improves accuracy over baseline.",
                role=HypothesisRole.PRIMARY,
                decision_rule={
                    "supported_if": "paired effect >= 0.05",
                    "otherwise": "inconclusive",
                },
            )
        ],
        data_boundary={
            "population": "frozen evaluation cases",
            "denominator": "all qualified frozen cases",
            "unit_of_analysis": "case",
            "label_origin": "frozen gold label field",
            "time_boundary": "snapshot at contract freeze",
        },
        data_requirements={
            "population": "frozen evaluation cases",
            "sampling_frame": "census of every qualified frozen case",
            "denominator": "all qualified frozen cases",
            "unit_of_analysis": "case",
            "label_origin": "frozen gold label field",
            "target_rule": "prediction equals frozen gold label",
            "time_boundary": "snapshot at contract freeze",
        },
        metrics=[
            {
                "name": "accuracy",
                "direction": "higher_is_better",
                "unit": "proportion",
                "formula": "correct_predictions / qualified_cases",
            }
        ],
        baseline={
            "experiment_id": "baseline-v1",
            "action_id": "baseline-action",
            "behavior": "apply the frozen baseline classifier to each case",
        },
        treatment={
            "experiment_id": "treatment-v1",
            "action_id": "treatment-action",
            "behavior": "apply the candidate classifier to the same cases",
        },
        tasks=["frozen-case-classification"],
        seeds=[11, 29],
        runtime_binding={"approved_resource_ids": ["resource-fixture"]},
        evaluator_policy={"authority": "deterministic metric evaluator"},
        experiment_profile=(
            Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1
        ),
        output_schema={"type": "object"},
        statistical_rules={
            "effect_threshold": 0.05,
            "effect_scale": "absolute",
            "effect_unit": "proportion",
            "missing_data_policy": "exclude only schema-invalid cases",
            "success_threshold": "paired effect >= 0.05",
            "method": "paired effect estimate",
        },
        estimand={
            "population": "frozen evaluation cases",
            "experimental_unit": "case",
            "variance_unit": "case",
            "outcome": "accuracy",
            "contrast": "treatment minus baseline",
        },
        scientific_validity_contract={
            "expected_information_value": (
                "Decide whether the candidate classifier should replace the "
                "frozen baseline under the registered effect threshold."
            ),
            "sample_adequacy_basis": (
                "Census of all qualified frozen cases; paired uncertainty is "
                "estimated at the registered case variance unit."
            ),
            "independence_justification": (
                "Cases are the independent scientific and variance units; "
                "seeds and reruns do not increase n."
            ),
        },
        environment_requirements={"network": "offline"},
        resource_policy={"selected_only": True},
        budget_security={"budget": {"max_runs": 20}},
    )


def test_contract_compiles_to_explicit_specs_and_dry_run() -> None:
    report = compile_research_contract(_contract())

    assert report.protocol_status is ProtocolStatus.EXECUTABLE
    assert report.compile_passed
    assert report.executable_algorithm_count == 2
    assert all(report.scientific_contribution_checks.values())
    assert len(report.run_specifications) == 4
    assert report.evaluation_specification is not None
    assert report.analysis_specification is not None
    assert report.dataset_specification is not None
    assert len(report.algorithm_specifications) == 2
    assert report.executable_run_dag is not None
    assert report.executable_run_dag.dependencies[
        report.executable_run_dag.analysis_node_id
    ] == [report.executable_run_dag.evaluation_node_id]
    assert all(
        item.dataset_specification_id
        == report.dataset_specification.dataset_specification_id
        for item in report.run_specifications
    )

    dry_run = dry_run_compiled_contract(report)
    assert dry_run.passed
    assert not dry_run.evidence_eligible
    assert not dry_run.formal_run_eligible


def test_contract_with_placeholder_task_cannot_compile_or_dry_run() -> None:
    contract = _contract().model_copy(
        update={"tasks": ["formal-primary-task"]}
    )

    report = compile_research_contract(contract)
    dry_run = dry_run_compiled_contract(report)

    assert report.protocol_status is ProtocolStatus.BLOCKED
    assert not report.compile_passed
    assert any(
        "TASK_SEMANTICS_MISSING" in item
        for item in report.blocking_issues
    )
    assert not dry_run.passed


def test_identical_arms_fail_the_scientific_contribution_gate() -> None:
    contract = _contract()
    report = compile_research_contract(
        contract.model_copy(
            update={
                "treatment": {
                    **contract.treatment,
                    "behavior": contract.baseline["behavior"],
                }
            }
        )
    )

    assert not report.compile_passed
    assert not report.scientific_contribution_checks[
        "registered_arm_delta_present"
    ]
    assert any(
        "ARM_DELTA_MISSING" in item for item in report.blocking_issues
    )


def test_generic_arm_prose_cannot_be_frozen_as_executable() -> None:
    contract = _contract().model_copy(
        update={
            "baseline": {
                "experiment_id": "baseline-v1",
                "action_id": "baseline-action",
                "behavior": (
                    "Run frozen replication and sensitivity conditions on "
                    "every eligible case."
                ),
            },
            "treatment": {
                "experiment_id": "treatment-v1",
                "action_id": "treatment-action",
                "behavior": (
                    "Change only the registered treatment intervention."
                ),
            },
        }
    )

    report = compile_research_contract(contract)

    assert not report.compile_passed
    assert any(
        "BASELINE_OPERATION_GENERIC" in item
        for item in report.blocking_issues
    )
    assert any(
        "TREATMENT_OPERATION_GENERIC" in item
        for item in report.blocking_issues
    )


def test_metric_name_without_explicit_formula_cannot_compile() -> None:
    contract = _contract()
    metric = dict(contract.metrics[0])
    metric.pop("formula")

    report = compile_research_contract(
        contract.model_copy(update={"metrics": [metric]})
    )

    assert not report.compile_passed
    assert any(
        "METRIC_FORMULA_MISSING" in item
        for item in report.blocking_issues
    )


def test_target_column_cannot_also_be_a_feature() -> None:
    contract = _contract()
    report = compile_research_contract(
        contract.model_copy(
            update={
                "data_requirements": {
                    **contract.data_requirements,
                    "target_column": "gold_label",
                    "feature_columns": ["text", "gold_label"],
                    "target_leakage_policy": (
                        "exclude target-derived and future fields"
                    ),
                }
            }
        )
    )

    assert not report.compile_passed
    assert any("TARGET_LEAKAGE" in item for item in report.blocking_issues)


def test_explicit_features_require_a_target_leakage_policy() -> None:
    contract = _contract()
    report = compile_research_contract(
        contract.model_copy(
            update={
                "data_requirements": {
                    **contract.data_requirements,
                    "target_column": "gold_label",
                    "feature_columns": ["text"],
                }
            }
        )
    )

    assert not report.compile_passed
    assert any(
        "TARGET_LEAKAGE_POLICY_MISSING" in item
        for item in report.blocking_issues
    )


def test_contract_requires_an_authorized_resource_or_builder() -> None:
    contract = _contract().model_copy(update={"runtime_binding": {}})

    report = compile_research_contract(contract)

    assert not report.compile_passed
    assert any(
        "APPROVED_RESOURCES_MISSING" in item
        for item in report.blocking_issues
    )


def test_explicit_bounded_resource_builder_satisfies_stage_two_authority() -> None:
    contract = _contract().model_copy(
        update={
            "runtime_binding": {},
            "resource_policy": {
                "mode": "build_or_acquire_experiment",
                "routes": [
                    {
                        "task": "materialize frozen evaluation cases",
                        "phase": "experiment",
                    }
                ],
            },
        }
    )

    report = compile_research_contract(contract)

    assert report.compile_passed


def test_blockers_compile_to_structured_amendment_without_mutating_contract() -> None:
    contract = _contract()
    before = contract.model_dump(mode="json")

    proposal = contract_amendment_proposal(
        contract,
        [
            "TARGET_RULE_MISSING: define authoritative targets",
            "METRIC_FORMULA_MISSING: define an executable formula",
        ],
        source_phase="experiment",
    )

    assert proposal.predecessor_contract_version == 1
    assert proposal.proposed_contract_version == 2
    assert {item.field_path for item in proposal.field_proposals} == {
        "data_requirements.target_rule",
        "metrics[0].formula",
    }
    assert contract.model_dump(mode="json") == before


def test_finance_contract_requires_point_in_time_policies() -> None:
    contract = _contract().model_copy(
        update={
            "tasks": ["rank stocks using frozen features at each trade date"],
            "estimand": {
                "population": "point-in-time stock universe",
                "outcome": "net portfolio return",
                "contrast": "treatment minus baseline",
            },
        }
    )

    report = compile_research_contract(contract)

    assert not report.compile_passed
    assert any(
        "FINANCE_POINT_IN_TIME_UNIVERSE_POLICY_MISSING" in item
        for item in report.blocking_issues
    )


def test_finance_contract_compiles_with_domain_policies() -> None:
    from research_forge.domain_adapters import finance_backtest_defaults

    contract = _contract()
    report = compile_research_contract(
        contract.model_copy(
            update={
                "tasks": [
                    "rank stocks using frozen features at each trade date"
                ],
                "data_requirements": {
                    **contract.data_requirements,
                    **finance_backtest_defaults(),
                },
            }
        )
    )

    assert report.compile_passed
    assert report.compiler_version == "2.0"


def test_learned_evaluator_requires_distinct_secondary_family() -> None:
    contract = _contract().model_copy(
        update={
            "evaluator_policy": {
                "authority": "learned NLI evaluator",
                "uses_learned_evaluator": True,
                "primary_evaluator": {
                    "family_id": "deberta_nli",
                    "implementation_id": "deberta-v1",
                },
            }
        }
    )

    report = compile_research_contract(contract)

    assert not report.compile_passed
    assert any(
        "SECONDARY_EVALUATOR_MISSING" in item
        for item in report.blocking_issues
    )


def test_distinct_second_evaluator_compiles_disagreement_contract() -> None:
    contract = _contract().model_copy(
        update={
            "evaluator_policy": {
                "uses_learned_evaluator": True,
                "primary_evaluator": {
                    "family_id": "deberta_nli",
                    "implementation_id": "deberta-v1",
                },
                "secondary_evaluators": [
                    {
                        "family_id": "deterministic_binding",
                        "implementation_id": "exact-binding-v1",
                    }
                ],
                "disagreement_policy": (
                    "record rows and require adjudication before verdict"
                ),
            }
        }
    )

    report = compile_research_contract(contract)

    assert report.compile_passed
    assert report.evaluation_specification is not None
    assert report.evaluation_specification.primary_evaluator_family == "deberta_nli"
    assert report.evaluation_specification.secondary_evaluator_families == [
        "deterministic_binding"
    ]
    assert "verdict_stability" in report.evaluation_specification.agreement_metrics


def test_legacy_stock_project_is_blocked_before_stage_three_with_complete_codes() -> None:
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "stock-project-contract-v1-incomplete.json"
        ).read_text(encoding="utf-8")
    )
    patch = fixture["contract_patch"]
    contract = _contract().model_copy(
        update={
            "tasks": patch["tasks"],
            "baseline": {
                **_contract().baseline,
                "behavior": patch["baseline_behavior"],
            },
            "treatment": {
                **_contract().treatment,
                "behavior": patch["treatment_behavior"],
            },
            "data_requirements": patch["data_requirements"],
            "metrics": [patch["metric"]],
            "runtime_binding": patch["runtime_binding"],
            "resource_policy": patch["resource_policy"],
            "scientific_validity_contract": patch[
                "scientific_validity_contract"
            ],
        }
    )

    report = compile_research_contract(contract)
    blocker_codes = {item.split(":", 1)[0] for item in report.blocking_issues}

    assert not report.compile_passed
    assert set(fixture["expected_blocker_codes"]).issubset(blocker_codes)
    proposal = contract_amendment_proposal(
        contract,
        report.blocking_issues,
        source_phase="protocol",
    )
    proposed_paths = {item.field_path for item in proposal.field_proposals}
    assert "data_requirements.corporate_action_policy" in proposed_paths
    assert "scientific_validity_contract.sample_adequacy_basis" in proposed_paths


def test_stock_contract_v2_compiles_only_after_scientific_semantics_are_frozen() -> None:
    from research_forge.domain_adapters import finance_backtest_defaults

    contract = _contract()
    data_requirements = {
        **contract.data_requirements,
        **finance_backtest_defaults(),
        "population": "all securities tradable at each decision timestamp",
        "sampling_frame": (
            "census of every monthly decision timestamp and every security "
            "eligible under the frozen point-in-time universe policy"
        ),
        "denominator": "all selected Top-K slots across decision timestamps",
        "unit_of_analysis": "decision-date security",
        "target_rule": (
            "positive iff executable-session net total return over the next "
            "20 trading days is at least 0.10 after frozen fees and slippage"
        ),
    }
    report = compile_research_contract(
        contract.model_copy(
            update={
                "tasks": ["monthly point-in-time stock ranking"],
                "data_requirements": data_requirements,
                "metrics": [
                    {
                        "name": "top_k_event_identification_rate",
                        "direction": "higher_is_better",
                        "unit": "proportion",
                        "formula": (
                            "qualifying positive targets among selected Top-K "
                            "/ all selected Top-K slots"
                        ),
                    }
                ],
                "baseline": {
                    **contract.baseline,
                    "behavior": (
                        "rank each point-in-time universe by frozen trailing "
                        "12-month momentum and select the first K"
                    ),
                },
                "treatment": {
                    **contract.treatment,
                    "behavior": (
                        "rank the same universe with the frozen learned "
                        "cross-sectional scorer and select the first K"
                    ),
                },
            }
        )
    )

    assert report.compile_passed
    assert report.dataset_specification is not None
    assert "20 trading days" in report.dataset_specification.target_rule
