"""Canonical four-phase acceptance for blinded paired human ratings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .designs.human_rating import OpenGenerationHumanRating
from .human_rating_case import (
    HUMAN_RATING_TITLE,
    human_rating_acceptance_plan,
    human_rating_acceptance_rows,
)
from .human_rating_reference import recalculate_human_rating
from .profile_workflow_acceptance import (
    ControlledProfileAcceptanceConfig,
    prepare_controlled_profile_workflow_acceptance,
    run_controlled_profile_workflow_acceptance,
)


HUMAN_RATING_ACCEPTANCE_CONFIG = ControlledProfileAcceptanceConfig(
    title=HUMAN_RATING_TITLE,
    profile_id="open_generation_human_rating_v1",
    profile_factory=OpenGenerationHumanRating,
    plan_factory=human_rating_acceptance_plan,
    rows_factory=human_rating_acceptance_rows,
    recalculator=recalculate_human_rating,
    dataset_filename="human_rating_ledger.csv",
    dataset_fields=(
        "item_id",
        "output_id",
        "output_sha256",
        "rater_id",
        "blind_label",
        "presentation_order",
        "arm",
        "rating",
    ),
    direction="Blinded paired evaluation of open-ended generated responses",
    research_question=(
        "Across forty frozen prompts, does a registered structured self-critique "
        "procedure improve prompt-level mean usefulness ratings relative to a "
        "direct response procedure under a complete blinded three-rater panel?"
    ),
    candidate_contribution=(
        "A black-box demonstration that Research Forge can preserve frozen open "
        "outputs, rater blindness, randomized presentation, panel completeness, "
        "reliability qualification, paired inference, and bounded claims through "
        "one governed paper."
    ),
    unit_of_analysis="one frozen prompt item evaluated under both response procedures",
    population_or_corpus="40 frozen prompts, 80 frozen outputs, and a three-rater panel",
    primary_outcome="prompt-level mean blinded usefulness rating on a 1-to-5 rubric",
    comparison="structured self-critique response minus direct response",
    scope_in=(
        "paired outputs for every frozen prompt",
        "three registered ratings per output",
        "condition-blinded labels",
        "randomized presentation order per rater",
        "prompt-level paired inference and reliability qualification",
    ),
    scope_out=(
        "factual accuracy or safety evaluation",
        "adaptive or incomplete rater panels",
        "individual rating rows as independent units",
        "generalization beyond the registered rubric, prompts, outputs, and panel",
    ),
    hypothesis_id="hypothesis-primary-usefulness",
    hypothesis_statement=(
        "The structured self-critique procedure has a higher prompt-level mean "
        "blinded usefulness rating than the direct response procedure across the "
        "frozen prompt set."
    ),
    hypothesis_decision_rule={
        "mode": "superiority",
        "effect_measure": "paired_mean_difference",
        "supported_if": (
            "the two-sided paired prompt-level interval is above zero after the "
            "complete-panel, blindness, and reliability qualification checks pass"
        ),
    },
    fixture_role="deterministic blinded paired human-rating acceptance study",
    fixture_task=(
        "For each of forty frozen prompts, preserve one direct and one "
        "self-critique output, blind their condition labels, randomize display "
        "order per panel member, and freeze all three rubric scores per output."
    ),
    task_specification={
        "time_boundary": "one frozen rating round after all outputs and blind labels are sealed",
        "rubric": {
            "id": "usefulness-rubric-v1",
            "scale": [1, 5],
            "construct": "usefulness under the registered rubric only",
        },
        "panel": ["rater-01", "rater-02", "rater-03"],
        "blinding": "condition identities are hidden behind condition-X and condition-Y",
        "adjudication": "frozen scores are never rewritten; integrity deviations are appended",
        "fixture_disclosure": (
            "The acceptance ledger is deterministic test data that exercises a "
            "human-rating workflow; it is not evidence about a real recruited panel."
        ),
    },
    allocation_description=(
        "every prompt receives both conditions; blinded condition labels and "
        "presentation order are frozen before rating analysis"
    ),
    allocation_fields=(
        "item_id",
        "output_id",
        "output_sha256",
        "rater_id",
        "blind_label",
        "presentation_order",
        "arm",
    ),
    run_task_id="registered_blinded_open_generation_rating_fixture",
    experiment_id="open-generation-human-rating-acceptance-v1",
    run_seed=20260805,
    result_kind="human_rating_arm_result",
    maximum_claim_tier="blinded_paired_human_rating_effect",
    allowed_claim_supported=(
        "In the frozen paired acceptance fixture, structured self-critique "
        "increased prompt-level mean blinded usefulness ratings relative to "
        "direct response generation under the registered complete panel."
    ),
    allowed_claim_other=(
        "The registered blinded paired comparison did not establish a usefulness "
        "rating benefit for structured self-critique."
    ),
    known_limitations=(
        "The acceptance ledger is deterministic test data, not ratings from a recruited external panel.",
        "Usefulness ratings do not establish factual accuracy, safety, or broader preference.",
        "The v1 Profile requires a complete balanced panel and does not support adaptive assignment.",
        "Generalization is limited to the frozen prompts, outputs, rubric, and panel structure.",
    ),
    runtime_entrypoint="research_forge.study_design.human_rating_case",
    evaluator_name="open_generation_human_rating_v1 deterministic evaluator",
    independent_unit_label="prompt item",
    paired=True,
)


def prepare_human_rating_workflow_acceptance(output_root: str | Path) -> dict[str, Any]:
    return prepare_controlled_profile_workflow_acceptance(
        output_root, HUMAN_RATING_ACCEPTANCE_CONFIG
    )


def run_human_rating_workflow_acceptance(
    root: Path,
    *,
    approve_owner_gates: bool = True,
    decided_by: str = "automated human-rating acceptance owner",
    max_cycles: int = 12,
) -> dict[str, Any]:
    return run_controlled_profile_workflow_acceptance(
        root,
        HUMAN_RATING_ACCEPTANCE_CONFIG,
        approve_owner_gates=approve_owner_gates,
        decided_by=decided_by,
        max_cycles=max_cycles,
    )


__all__ = [
    "HUMAN_RATING_ACCEPTANCE_CONFIG",
    "prepare_human_rating_workflow_acceptance",
    "run_human_rating_workflow_acceptance",
]
