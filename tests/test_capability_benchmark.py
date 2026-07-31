from pathlib import Path

from research_forge.capability_benchmark import (
    BenchmarkCategory,
    BenchmarkImplementationStatus,
    benchmark_evidence_gaps,
    load_capability_benchmark,
)


ROOT = Path(__file__).resolve().parents[1]


def test_benchmark_contains_twelve_core_cases_and_two_replay_extensions() -> None:
    cases = load_capability_benchmark()
    by_category = {
        category: [case for case in cases if case.category is category]
        for category in BenchmarkCategory
    }

    assert len(cases) == 14
    assert len(by_category[BenchmarkCategory.POSITIVE]) == 4
    assert len(by_category[BenchmarkCategory.NEGATIVE]) == 4
    assert len(by_category[BenchmarkCategory.VERDICT]) == 4
    assert len(by_category[BenchmarkCategory.CLEAN_ROOM]) == 2


def test_current_suite_does_not_pretend_unimplemented_cases_pass() -> None:
    cases = load_capability_benchmark()
    records = {case.case_id: case for case in cases}

    assert records["capbench-openml-classification"].status is BenchmarkImplementationStatus.CONTROLLED_E2E
    assert records["capbench-openml-regression"].status is BenchmarkImplementationStatus.CONTROLLED_E2E
    assert records["capbench-existing-python-reproduction"].status is BenchmarkImplementationStatus.REAL_CASE_VALIDATED
    assert records["capbench-clean-room-package-a"].status is BenchmarkImplementationStatus.CONTROLLED_E2E
    assert records["capbench-clean-room-package-b"].status is BenchmarkImplementationStatus.CONTROLLED_E2E
    assert records["capbench-metric-name-no-formula"].status is BenchmarkImplementationStatus.COMPONENT_TESTED
    assert records["capbench-verdict-supported"].status is BenchmarkImplementationStatus.COMPONENT_TESTED
    assert records["capbench-verdict-refuted"].status is BenchmarkImplementationStatus.COMPONENT_TESTED
    assert records["capbench-verdict-mixed"].status is BenchmarkImplementationStatus.COMPONENT_TESTED
    assert records["capbench-verdict-inconclusive"].status is BenchmarkImplementationStatus.COMPONENT_TESTED


def test_all_claimed_benchmark_evidence_locators_exist() -> None:
    assert benchmark_evidence_gaps(ROOT) == {}
