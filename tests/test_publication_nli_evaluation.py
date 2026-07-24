import pytest

from research_forge.publication_nli_evaluation import (
    _conditional_probabilities,
    _deterministic_experiment_support,
    _hierarchical_bootstrap,
    _publication_cell_experiment_packet,
    _verdict_from_scores,
)
from research_forge.counterfactual_rebranch import _evidence_chunks
from research_forge.storage import sha256_file, write_json_atomic
from research_forge.study_models import StudyArm, StudyClaim, StudyClaimType


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


def _experiment_claim(text: str, value: float) -> StudyClaim:
    return StudyClaim(
        claim_id="claim-1",
        run_id="agent-treatment--task--seed-0",
        arm=StudyArm.TREATMENT,
        task_pack="task",
        seed=0,
        claim_type=StudyClaimType.EXPERIMENT,
        claim_text=text,
        experiment_run_id="run-1",
        metric_values={"Accuracy": value},
        artifact_paths=["record.json"],
    )


def _context_packet(value: float, target: float = 0.905) -> dict[str, object]:
    return {
        "linked_experiment_evidence": {
            "valid": True,
            "isolation_verified": True,
            "aggregate_metrics": {"Accuracy": value},
            "verdict": "candidate_improves",
            "improvement": 0.2,
        },
        "linked_task_specification": {
            "primary_metric": "Accuracy",
            "direction": "maximize",
            "baseline_score": 0.5,
            "target_score": target,
            "task_specification_sha256": "a" * 64,
        },
        "linked_source_records": [],
    }


def test_exact_metric_entailment_precedes_probabilistic_nli() -> None:
    claim = _experiment_claim(
        "The valid candidate run achieved an Accuracy of 0.814512841418671.",
        0.814512841418671,
    )
    rationale = _deterministic_experiment_support(
        claim, _context_packet(0.814512841418671)
    )
    assert rationale is not None
    assert "exact-metric" in rationale

    blinded_task_claim = _experiment_claim(
        "The verified evaluated run achieved an Accuracy of 0.5686913982878108 on the task.",
        0.5686913982878108,
    )
    assert _deterministic_experiment_support(
        blinded_task_claim, _context_packet(0.5686913982878108)
    ) is not None


def test_hash_bound_target_comparison_is_deterministically_supported() -> None:
    claim = _experiment_claim(
        "The valid candidate run achieved an Accuracy of 0.814512841418671, "
        "below the task target of 0.905.",
        0.814512841418671,
    )
    rationale = _deterministic_experiment_support(
        claim, _context_packet(0.814512841418671)
    )
    assert rationale is not None
    assert "task-context" in rationale


def test_extra_semantic_claim_does_not_receive_deterministic_support() -> None:
    claim = _experiment_claim(
        "The valid candidate run achieved an Accuracy of 0.8 and proves the method is superior.",
        0.8,
    )
    assert _deterministic_experiment_support(claim, _context_packet(0.8)) is None


def test_task_specification_and_experiment_share_one_nli_chunk() -> None:
    claim = _experiment_claim(
        "The valid candidate run achieved an Accuracy of 0.8, below the task target of 0.9.",
        0.8,
    )
    chunks = _evidence_chunks(claim, _context_packet(0.8, 0.9))
    assert any(
        "Frozen task specification:" in item and "Experiment record:" in item
        for item in chunks
    )


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


def test_publication_evaluator_resolves_only_hash_bound_shared_evidence(tmp_path) -> None:
    project = tmp_path / "project"
    stage2 = project / "stage2"
    pair_dir = stage2 / "shared" / "pairs" / "01"
    record_path = pair_dir / "evidence" / "run-1" / "record.json"
    write_json_atomic(stage2 / "protocol.json", {"protocol_id": "stage2-test"})
    write_json_atomic(pair_dir / "pair_manifest.json", {"pair_key": "task--seed-0"})
    write_json_atomic(pair_dir / "shared_registry.json", {"schema_version": 1})
    write_json_atomic(
        record_path,
        {
            "run_id": "run-1",
            "is_baseline": False,
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:01+00:00",
            "contract_hash": "a" * 64,
            "code_hash": "b" * 64,
            "trials": [{"index": 0, "exit_code": 0, "duration_seconds": 1.0, "valid": True}],
            "aggregate_metrics": {"score": 1.0},
            "valid": True,
            "verdict": "candidate_improves",
            "improvement": 0.1,
        },
    )
    cell_dir = stage2 / "r" / "t" / "01"
    write_json_atomic(
        cell_dir / "shared_artifact_binding.json",
        {
            "protocol_id": "stage2-test",
            "pair_key": "task--seed-0",
            "shared_registry_path": "stage2/shared/pairs/01/shared_registry.json",
            "shared_registry_sha256": sha256_file(pair_dir / "shared_registry.json"),
        },
    )

    packet = _publication_cell_experiment_packet(project, stage2, cell_dir)
    assert packet[0]["run_id"] == "run-1"
    assert packet[0]["artifacts"] == ["stage2/shared/pairs/01/evidence/run-1/record.json"]

    binding = cell_dir / "shared_artifact_binding.json"
    write_json_atomic(
        binding,
        {
            "protocol_id": "stage2-test",
            "pair_key": "task--seed-0",
            "shared_registry_path": "stage2/shared/pairs/01/shared_registry.json",
            "shared_registry_sha256": "0" * 64,
        },
    )
    with pytest.raises(ValueError, match="hash"):
        _publication_cell_experiment_packet(project, stage2, cell_dir)
