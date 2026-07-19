from research_forge.publication_nli_evaluation import (
    _conditional_probabilities,
    _hierarchical_bootstrap,
    _verdict_from_scores,
)


def test_conditional_probabilities_exclude_neutral_mass() -> None:
    entailment, contradiction = _conditional_probabilities(
        {"entailment": 0.20, "contradiction": 0.05, "neutral": 0.75}
    )
    assert entailment == 0.8
    assert contradiction == 0.2


def test_calibrated_verdict_uses_locked_conditional_threshold() -> None:
    verdict, probabilities = _verdict_from_scores(
        [{"entailment": 0.20, "contradiction": 0.05, "neutral": 0.75}],
        entailment_threshold=0.55,
        contradiction_threshold=0.55,
    )
    assert verdict == "supported"
    assert probabilities["max_conditional_entailment"] == 0.8


def test_hierarchical_bootstrap_is_deterministic() -> None:
    rows = [
        {"task_id": "a", "effect": -0.5},
        {"task_id": "a", "effect": 0.0},
        {"task_id": "b", "effect": -0.25},
        {"task_id": "b", "effect": 0.25},
    ]
    first = _hierarchical_bootstrap(rows, metric="effect", resamples=100, seed=7)
    second = _hierarchical_bootstrap(rows, metric="effect", resamples=100, seed=7)
    assert first == second
    assert first["mean"] == -0.125
