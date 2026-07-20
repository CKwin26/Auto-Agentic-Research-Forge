import pytest

from research_forge.publication_nli_evaluation import (
    _conditional_probabilities,
    _hierarchical_bootstrap,
    _publication_cell_experiment_packet,
    _verdict_from_scores,
)
from research_forge.storage import sha256_file, write_json_atomic


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
