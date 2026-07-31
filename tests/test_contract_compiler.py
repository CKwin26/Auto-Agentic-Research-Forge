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
