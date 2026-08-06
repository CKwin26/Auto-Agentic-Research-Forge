from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from research_forge.profiles import (
    ProfileCertificationStatus,
    ProfileMaturity,
    profile_bundle,
    profile_descriptor,
    resolve_profile_capability,
)
from research_forge.profiles.acceptance import (
    assess_profile_maturity,
    load_profile_acceptance_report,
)
from research_forge.profiles.contracts import TimeSeriesBacktestParameters
from research_forge.profiles.time_series_backtest import (
    evaluate_time_series_backtest,
    materialize_isolated_time_series_backtest_package,
    run_time_series_backtest,
)
from research_forge.experiment_execution import load_experiment_manifest
from research_forge.container_execution import inspect_local_container_image
from research_forge.stage_three import (
    STAGE3_LOCK_NAMES,
    admit_stage_three,
    compile_run_plan,
    ensure_stage_three_dag,
)
from research_forge.storage import sha256_file
from research_forge.workflow_scheduler import (
    PersistentDAGScheduler,
    workflow_handlers,
)
from research_forge.workflow_domain import (
    ArtifactRole,
    ArtifactStatus,
    EntryMode,
    GateType,
    Hypothesis,
    HypothesisRole,
    Phase,
    ResearchContractVersion,
    ScopeContractVersion,
    WorkflowRepository,
)
from research_forge.workflow_domain import ProfileCapabilityStatus, Stage3Profile


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _parameters(**changes: object) -> TimeSeriesBacktestParameters:
    payload: dict[str, object] = {
        "candidate_input_path": "signals.csv",
        "evaluator_target_path": "targets.csv",
        "timestamp_field": "date",
        "feature_as_of_field": "feature_as_of",
        "asset_id_field": "asset",
        "eligibility_field": "eligible",
        "baseline_signal_field": "baseline_score",
        "treatment_signal_field": "treatment_score",
        "target_return_field": "forward_return",
        "evaluation_start": "2026-01-01",
        "evaluation_end": "2026-01-31",
        "training_end": "2025-12-31",
        "top_k": 1,
        "minimum_eligible_assets": 3,
        "effect_threshold": 0.05,
        "fee_bps_per_round_trip": 10,
        "slippage_bps_per_round_trip": 20,
        "calendar_id": "frozen-three-period-fixture",
        "return_horizon": "next_registered_period",
        "target_return_definition": "total return over the next registered period",
        "corporate_action_policy": "targets are total-return adjusted",
        "delisting_policy": "delisting returns remain in evaluator targets",
        "suspension_policy": "ineligible before ranking",
    }
    payload.update(changes)
    return TimeSeriesBacktestParameters.model_validate(payload)


def test_point_in_time_backtest_pairs_periods_and_recovers_signal_gain(
    tmp_path: Path,
) -> None:
    signal_rows: list[dict[str, object]] = []
    target_rows: list[dict[str, object]] = []
    for period in ("2026-01-02", "2026-01-09", "2026-01-16"):
        for index, asset in enumerate(("A", "B", "C")):
            signal_rows.append(
                {
                    "date": period,
                    "feature_as_of": period,
                    "asset": asset,
                    "eligible": True,
                    "baseline_score": 3 - index,
                    "treatment_score": index + 1,
                }
            )
            target_rows.append(
                {
                    "date": period,
                    "asset": asset,
                    "forward_return": (-0.10, 0.00, 0.20)[index],
                }
            )
    _write_csv(tmp_path / "signals.csv", signal_rows)
    _write_csv(tmp_path / "targets.csv", target_rows)
    parameters = _parameters()

    baseline = run_time_series_backtest(
        parameters, arm="baseline", root=tmp_path
    )
    treatment = run_time_series_backtest(
        parameters, arm="treatment", root=tmp_path
    )
    evaluation = evaluate_time_series_backtest(
        baseline, treatment, parameters
    )

    assert baseline.denominator == treatment.denominator == 3
    assert all(row.selected_asset_ids == ["A"] for row in baseline.rows)
    assert all(row.selected_asset_ids == ["C"] for row in treatment.rows)
    assert evaluation.effect == pytest.approx(0.30)
    assert evaluation.verdict == "supported"


def test_backtest_contract_blocks_target_leakage_and_undersized_universe() -> None:
    payload = _parameters().model_dump(mode="json")
    with pytest.raises(ValidationError, match="separate"):
        TimeSeriesBacktestParameters.model_validate(
            {**payload, "evaluator_target_path": payload["candidate_input_path"]}
        )
    with pytest.raises(ValidationError, match="at least top_k"):
        TimeSeriesBacktestParameters.model_validate(
            {**payload, "top_k": 4, "minimum_eligible_assets": 3}
        )


def test_backtest_contract_blocks_overlapping_training_window_and_identical_arms() -> None:
    payload = _parameters().model_dump(mode="json")
    with pytest.raises(ValidationError, match="precede evaluation_start"):
        TimeSeriesBacktestParameters.model_validate(
            {**payload, "training_end": payload["evaluation_start"]}
        )
    with pytest.raises(ValidationError, match="must be distinct"):
        TimeSeriesBacktestParameters.model_validate(
            {
                **payload,
                "treatment_signal_field": payload["baseline_signal_field"],
            }
        )


def test_backtest_blocks_future_features_and_candidate_target_column(
    tmp_path: Path,
) -> None:
    base = {
        "date": "2026-01-02",
        "asset": "A",
        "eligible": True,
        "baseline_score": 1,
        "treatment_score": 2,
    }
    rows = [
        {**base, "asset": asset, "feature_as_of": "2026-01-03"}
        for asset in ("A", "B", "C")
    ]
    _write_csv(tmp_path / "signals.csv", rows)
    _write_csv(
        tmp_path / "targets.csv",
        [
            {"date": "2026-01-02", "asset": asset, "forward_return": 0.1}
            for asset in ("A", "B", "C")
        ],
    )
    with pytest.raises(ValueError, match="follows the decision"):
        run_time_series_backtest(_parameters(), arm="baseline", root=tmp_path)

    leaked = [
        {**row, "feature_as_of": "2026-01-02", "forward_return": 0.1}
        for row in rows
    ]
    _write_csv(tmp_path / "signals.csv", leaked)
    with pytest.raises(ValueError, match="must not contain"):
        run_time_series_backtest(_parameters(), arm="baseline", root=tmp_path)


def test_missing_evaluator_target_blocks_instead_of_dropping_a_case(
    tmp_path: Path,
) -> None:
    _write_csv(
        tmp_path / "signals.csv",
        [
            {
                "date": "2026-01-02",
                "feature_as_of": "2026-01-02",
                "asset": asset,
                "eligible": True,
                "baseline_score": index,
                "treatment_score": index,
            }
            for index, asset in enumerate(("A", "B", "C"))
        ],
    )
    _write_csv(
        tmp_path / "targets.csv",
        [
            {"date": "2026-01-02", "asset": "A", "forward_return": 0.1},
            {"date": "2026-01-02", "asset": "B", "forward_return": 0.2},
        ],
    )
    with pytest.raises(ValueError, match="has no frozen target"):
        run_time_series_backtest(_parameters(), arm="baseline", root=tmp_path)


def test_backtest_profile_is_c2_until_full_acceptance_report_exists() -> None:
    bundle = profile_bundle(Stage3Profile.TIME_SERIES_BACKTEST_V1)
    descriptor = profile_descriptor(Stage3Profile.TIME_SERIES_BACKTEST_V1)

    assert bundle.certification_status is ProfileCertificationStatus.CERTIFIED
    assert descriptor.maturity is ProfileMaturity.C2_DRY_RUN
    assert resolve_profile_capability(
        Stage3Profile.TIME_SERIES_BACKTEST_V1,
        runnable_assets_present=True,
    ) is ProfileCapabilityStatus.SUPPORTED

    report = load_profile_acceptance_report(
        Stage3Profile.TIME_SERIES_BACKTEST_V1.value
    )
    assert report is None


def test_isolated_backtest_package_hides_targets_from_candidate_and_runs_smoke(
    tmp_path: Path,
) -> None:
    signal_rows: list[dict[str, object]] = []
    target_rows: list[dict[str, object]] = []
    for period in ("2026-01-02", "2026-01-09"):
        for index, asset in enumerate(("A", "B", "C")):
            signal_rows.append(
                {
                    "date": period,
                    "feature_as_of": period,
                    "asset": asset,
                    "eligible": True,
                    "baseline_score": 3 - index,
                    "treatment_score": index + 1,
                }
            )
            target_rows.append(
                {
                    "date": period,
                    "asset": asset,
                    "forward_return": (-0.1, 0.0, 0.2)[index],
                }
            )
    _write_csv(tmp_path / "signals.csv", signal_rows)
    _write_csv(tmp_path / "targets.csv", target_rows)
    package, manifest_path = materialize_isolated_time_series_backtest_package(
        _parameters(), tmp_path
    )
    manifest = load_experiment_manifest(manifest_path)
    treatment = manifest.experiments[1]
    assert treatment.execution_backend == "isolated_candidate_evaluator"
    assert "targets.csv" not in treatment.required_inputs
    assert "targets.csv" in treatment.evaluator_required_inputs
    candidate_config = json.loads(
        (package / "candidate-config.json").read_text(encoding="utf-8")
    )
    assert "target_return_field" not in candidate_config
    assert "evaluator_target_path" not in candidate_config

    output = tmp_path / "smoke-result.json"
    subprocess.run(
        [
            sys.executable,
            str(package / "smoke.py"),
            "--candidate",
            str(package / "candidate.py"),
            "--evaluator",
            str(package / "evaluator.py"),
            "--signals",
            str(tmp_path / "signals.csv"),
            "--targets",
            str(tmp_path / "targets.csv"),
            "--candidate-config",
            str(package / "candidate-config.json"),
            "--evaluator-config",
            str(package / "evaluator-config.json"),
            "--arm",
            "treatment",
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mean_net_portfolio_return"] == pytest.approx(0.197)
    assert payload["denominator"] == 2


def test_frozen_backtest_inputs_detect_four_material_mutations(
    tmp_path: Path,
) -> None:
    _write_csv(
        tmp_path / "signals.csv",
        [
            {
                "date": "2026-01-02",
                "feature_as_of": "2026-01-02",
                "asset": asset,
                "eligible": True,
                "baseline_score": index,
                "treatment_score": 3 - index,
            }
            for index, asset in enumerate(("A", "B", "C"))
        ],
    )
    _write_csv(
        tmp_path / "targets.csv",
        [
            {"date": "2026-01-02", "asset": asset, "forward_return": 0.1}
            for asset in ("A", "B", "C")
        ],
    )
    package, _ = materialize_isolated_time_series_backtest_package(
        _parameters(), tmp_path
    )
    paths = [
        tmp_path / "signals.csv",
        tmp_path / "targets.csv",
        package / "candidate.py",
        package / "evaluator.py",
    ]
    for path in paths:
        before = sha256_file(path)
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        assert sha256_file(path) != before
        path.write_bytes(original)
        assert sha256_file(path) == before


def test_certified_backtest_compiles_a_persisted_stage3_run_plan(
    tmp_path: Path,
) -> None:
    signal_rows: list[dict[str, object]] = []
    target_rows: list[dict[str, object]] = []
    for period in ("2026-01-02", "2026-01-09", "2026-01-16"):
        for index, asset in enumerate(("A", "B", "C")):
            signal_rows.append(
                {
                    "date": period,
                    "feature_as_of": period,
                    "asset": asset,
                    "eligible": True,
                    "baseline_score": 3 - index,
                    "treatment_score": index + 1,
                }
            )
            target_rows.append(
                {
                    "date": period,
                    "asset": asset,
                    "forward_return": (-0.1, 0.0, 0.2)[index],
                }
            )
    source = tmp_path / "p"
    source.mkdir()
    _write_csv(source / "signals.csv", signal_rows)
    _write_csv(source / "targets.csv", target_rows)
    parameters = _parameters()
    _, manifest_path = materialize_isolated_time_series_backtest_package(
        parameters, source
    )

    repository = WorkflowRepository(tmp_path / "w")
    project = repository.create_project("Backtest", source_root=str(source))
    study = repository.create_study(
        project.project_id, "Backtest", entry_mode=EntryMode.PROJECT_TO_PAPER
    )
    scope = ScopeContractVersion(
        study_id=study.study_id,
        version=1,
        direction="Evaluate a frozen point-in-time ranking signal",
        research_question="Does the treatment signal improve net return?",
        scope_in=["frozen signal and target fixtures"],
        scope_out=["raw market-data construction"],
        candidate_contribution="A target-isolated paired backtest.",
    )
    repository.save_scope_contract(scope)
    gate = repository.create_gate(
        study.study_id,
        GateType.SCOPE_APPROVAL,
        "scope_contract",
        f"{study.study_id}:scope-v1",
        subject_version=1,
    )
    repository.decide_gate(
        study.study_id, gate.gate_id, approve=True, decided_by="owner"
    )
    repository.save_scope_contract(
        scope.model_copy(update={"status": ArtifactStatus.FROZEN, "frozen_at": "frozen"})
    )
    contract = ResearchContractVersion(
        study_id=study.study_id,
        version=1,
        scope_version=1,
        hypotheses=[
            Hypothesis(
                hypothesis_id="hypothesis-backtest-primary",
                statement="Treatment improves mean net portfolio return by more than 0.05.",
                role=HypothesisRole.PRIMARY,
                decision_rule={"effect_threshold": 0.05},
            )
        ],
        data_boundary={"denominator": "all registered periods"},
        metrics=[
            {
                "name": "mean_net_portfolio_return",
                "direction": "higher_is_better",
                "denominator": "all registered periods",
            }
        ],
        baseline={
            "name": "baseline",
            "experiment_id": "time-series-backtest-baseline",
            "action_id": "action-time-series-backtest-baseline",
        },
        treatment={
            "name": "treatment",
            "experiment_id": "time-series-backtest-treatment",
            "action_id": "action-time-series-backtest-treatment",
        },
        tasks=["portfolio-ranking"],
        splits=["formal"],
        seeds=[1],
        replicates=1,
        concurrency=1,
        runtime_binding={"python": "test", "approved_resource_ids": []},
        evaluator_policy={"authority_order": ["deterministic"]},
        experiment_profile=Stage3Profile.TIME_SERIES_BACKTEST_V1,
        profile_parameters=parameters.model_dump(mode="json"),
        output_schema={
            "format": "json",
            "metric_field": "mean_net_portfolio_return",
            "denominator_field": "denominator",
            "sample_id_field": "sample_ids",
            "record_layout": "summary_with_analysis_rows",
            "analysis_rows_field": "analysis_rows",
        },
        statistical_rules={
            "method": "paired_run_difference",
            "effect_threshold": 0.05,
            "missing_cell_policy": "inconclusive",
        },
        estimand={
            "population": "registered evaluation periods",
            "experimental_unit": "period",
            "pairing_key": ["task", "split", "seed", "replicate"],
            "outcome": "mean_net_portfolio_return",
            "contrast": "treatment minus baseline",
            "aggregation_hierarchy": ["period", "study"],
            "weighting_policy": "equal weight per period",
            "variance_unit": "period",
        },
        data_requirements={"partition": "formal evaluation"},
        implementation_requirements={"allowed_arm_delta": ["ranking signal field"]},
        environment_requirements={"network": "offline"},
        resource_policy={"routes_must_be_explicit": True},
        budget_security={"max_download_bytes": 1_000_000},
    )
    repository.save_research_contract(contract)
    gate = repository.create_gate(
        study.study_id,
        GateType.RESEARCH_CONTRACT,
        "research_contract",
        f"{study.study_id}:research-v1",
        subject_version=1,
    )
    repository.decide_gate(
        study.study_id, gate.gate_id, approve=True, decided_by="owner"
    )
    repository.save_research_contract(
        contract.model_copy(update={"status": ArtifactStatus.FROZEN, "frozen_at": "frozen"})
    )
    stage2 = repository.root / "studies" / study.study_id / "stage2"
    for name in STAGE3_LOCK_NAMES:
        path = stage2 / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"name": name, "immutable": True}
        if name == "environment.lock.json":
            inspected = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "python:3.12-slim",
                    "--format",
                    "{{.Id}}",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            payload["container_image_id"] = (
                inspected.stdout.strip() or "sha256:compile-only"
            )
        path.write_text(json.dumps(payload), encoding="utf-8")
        repository.register_artifact(
            study.study_id,
            str(path),
            sha256_file(path),
            kind=name.removesuffix(".json").replace(".", "_"),
            role=ArtifactRole.PROTOCOL,
        )
    repository.save_study(
        repository.load_study(study.study_id).model_copy(update={"phase": Phase.EXPERIMENT}),
        "test_backtest_handoff",
    )

    handoff = admit_stage_three(
        repository, study.study_id, explicit_manifest_path=str(manifest_path)
    )
    plan = compile_run_plan(repository, study.study_id, handoff=handoff)
    assert plan.profile is Stage3Profile.TIME_SERIES_BACKTEST_V1
    assert len(plan.cells) == 2
    assert {cell.arm_id for cell in plan.cells} == {"baseline", "treatment"}


def test_backtest_controlled_fixture_runs_through_verdict_and_evidence(
    tmp_path: Path,
) -> None:
    """C3 acceptance chain: container run -> evaluation -> verdict -> evidence."""

    if inspect_local_container_image("docker", "python:3.12-slim") is None:
        pytest.skip("python:3.12-slim is required for controlled acceptance")

    # Reuse the realistic persisted Study setup above, then execute its plan.
    test_certified_backtest_compiles_a_persisted_stage3_run_plan(tmp_path)
    repository = WorkflowRepository(tmp_path / "w")
    study_id = repository.list_studies()[0].study_id
    _, plan, _ = ensure_stage_three_dag(repository, study_id)
    scheduler = PersistentDAGScheduler(
        repository, workflow_handlers(), max_concurrency=2
    )
    scheduler.run(study_id)
    gate = next(
        item
        for item in repository.list_gates(study_id)
        if item.subject_type == "stage3_run_plan"
        and item.subject_id == plan.plan_id
    )
    repository.decide_gate(
        study_id, gate.gate_id, approve=True, decided_by="acceptance-test"
    )
    snapshot = scheduler.run(study_id)
    failed = [
        item for item in snapshot["steps"] if item["status"] in {"failed", "blocked"}
    ]
    if failed:
        print(json.dumps(failed, indent=2, ensure_ascii=False))
    assert failed == [], failed
    evaluations = repository.list_evaluation_records(study_id)
    assert len(evaluations) == 1
    assert evaluations[0].qualification_status.value == "qualified"
    assert evaluations[0].paired_effect == pytest.approx(0.30)
    assert evaluations[0].decision.value == "supported"
    assert len(repository.list_claim_envelopes(study_id)) == 1
    chains = repository.list_evidence_chains(study_id)
    assert len(chains) == 1
    assert chains[0].level.value == "verified_chain"
    assert chains[0].verdict_eligible() is True
