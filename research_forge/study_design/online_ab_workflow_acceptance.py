"""Canonical four-phase acceptance for the fixed-horizon online A/B Profile."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .designs.online_ab import OnlineABTest
from .online_ab_case import online_ab_acceptance_plan, online_ab_acceptance_rows
from .profile_workflow_acceptance import (
    ControlledProfileAcceptanceConfig,
    prepare_controlled_profile_workflow_acceptance,
    run_controlled_profile_workflow_acceptance,
)
from .reference import recalculate_independent_group


ONLINE_AB_TITLE = (
    "Does a Registered Assisted Experience Improve Conversion Without "
    "Increasing Error Incidence? A Fixed-Horizon Online A/B Study"
)


ONLINE_AB_ACCEPTANCE_CONFIG = ControlledProfileAcceptanceConfig(
    title=ONLINE_AB_TITLE,
    profile_id="online_ab_test_v1",
    profile_factory=OnlineABTest,
    plan_factory=online_ab_acceptance_plan,
    rows_factory=online_ab_acceptance_rows,
    recalculator=recalculate_independent_group,
    dataset_filename="online_ab_exposures.csv",
    dataset_fields=(
        "subject_id",
        "exposure_id",
        "exposure_timestamp",
        "arm",
        "converted",
        "error_occurred",
    ),
    direction="Fixed-horizon randomized online controlled experiment",
    research_question=(
        "Among accounts with a first valid exposure in the frozen experiment "
        "window, does the registered assisted experience increase conversion "
        "relative to the current experience while preserving transparent error "
        "guardrail reporting?"
    ),
    candidate_contribution=(
        "A black-box demonstration that Research Forge can preserve exposure "
        "eligibility, random allocation, sample-ratio mismatch checks, a fixed "
        "horizon, a primary metric, and a guardrail through one governed paper."
    ),
    unit_of_analysis="one exposed account at its first valid exposure",
    population_or_corpus="400 frozen synthetic exposed accounts",
    primary_outcome="binary task conversion at the registered outcome boundary",
    comparison="registered assisted experience minus current experience",
    scope_in=(
        "seeded account-level random allocation",
        "one authenticated first valid exposure per account",
        "one fixed-horizon confirmatory analysis",
        "conversion as the primary outcome",
        "error incidence as a protected guardrail",
    ),
    scope_out=(
        "optional stopping or always-valid sequential inference",
        "unexposed eligible traffic",
        "cross-device identity resolution",
        "generalization beyond the synthetic acceptance fixture",
    ),
    hypothesis_id="hypothesis-primary-conversion",
    hypothesis_statement=(
        "At the frozen fixed horizon, the assisted experience has a higher "
        "conversion probability than the current experience among first-valid "
        "exposed accounts."
    ),
    hypothesis_decision_rule={
        "mode": "superiority",
        "effect_measure": "risk_difference",
        "supported_if": (
            "the treatment-minus-control conversion interval is above zero "
            "under the registered Holm family after the horizon and SRM gates pass"
        ),
    },
    fixture_role="deterministic fixed-horizon online A/B acceptance study",
    fixture_task=(
        "Allocate four hundred synthetic account identifiers once, admit exactly "
        "one authenticated exposure per account during the frozen day, and record "
        "conversion and error events after the planned horizon."
    ),
    task_specification={
        "time_boundary": (
            "2026-08-01T00:00:00Z through 2026-08-02T00:00:00Z; no scientific "
            "decision occurs before all 400 first-valid exposures are frozen"
        ),
        "conversion_rule": "exact frozen event counts are 60/200 in control and 90/200 in treatment",
        "error_rule": "exact frozen event counts are 10/200 in control and 12/200 in treatment",
        "exposure_rule": "one authenticated first valid exposure per account",
        "interim_monitoring": "prohibited for confirmatory inference",
    },
    allocation_description=(
        "seeded balanced account-level random allocation without replacement; "
        "exposure identity and timestamp are frozen before analysis"
    ),
    allocation_fields=("subject_id", "exposure_id", "exposure_timestamp", "arm"),
    run_task_id="registered_online_ab_fixed_horizon_fixture",
    experiment_id="online-ab-fixed-horizon-acceptance-v1",
    run_seed=20260804,
    result_kind="online_ab_arm_result",
    maximum_claim_tier="randomized_fixed_horizon_online_effect",
    allowed_claim_supported=(
        "In the frozen synthetic online experiment, the registered assisted "
        "experience increased conversion among first-valid exposed accounts; "
        "the error guardrail remained separately reported and inconclusive."
    ),
    allowed_claim_other=(
        "The registered fixed-horizon online comparison did not establish the "
        "prespecified conversion benefit."
    ),
    known_limitations=(
        "The fixture represents exposed synthetic accounts, not production traffic.",
        "The v1 Profile supports fixed-horizon analysis and does not authorize optional stopping.",
        "The observed error guardrail does not establish noninferiority or equivalence.",
        "Transport to unexposed eligible accounts requires additional assumptions.",
    ),
    runtime_entrypoint="research_forge.study_design.online_ab_case",
    evaluator_name="online_ab_test_v1 deterministic evaluator",
    independent_unit_label="exposed account",
)


def prepare_online_ab_workflow_acceptance(output_root: str | Path) -> dict[str, Any]:
    return prepare_controlled_profile_workflow_acceptance(
        output_root, ONLINE_AB_ACCEPTANCE_CONFIG
    )


def run_online_ab_workflow_acceptance(
    root: Path,
    *,
    approve_owner_gates: bool = True,
    decided_by: str = "automated online A/B acceptance owner",
    max_cycles: int = 12,
) -> dict[str, Any]:
    return run_controlled_profile_workflow_acceptance(
        root,
        ONLINE_AB_ACCEPTANCE_CONFIG,
        approve_owner_gates=approve_owner_gates,
        decided_by=decided_by,
        max_cycles=max_cycles,
    )


__all__ = [
    "ONLINE_AB_ACCEPTANCE_CONFIG",
    "ONLINE_AB_TITLE",
    "prepare_online_ab_workflow_acceptance",
    "run_online_ab_workflow_acceptance",
]
