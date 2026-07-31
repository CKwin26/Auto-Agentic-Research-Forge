from pathlib import Path

from research_forge.capability_registry import (
    CapabilityEvidence,
    CapabilityManifest,
    CapabilityMaturity,
    audit_capability_registry,
    capability_map,
    load_capability_registry,
)


ROOT = Path(__file__).resolve().parents[1]


def test_registry_is_unique_and_every_declared_level_is_evidenced() -> None:
    manifests = load_capability_registry()
    assert len(manifests) == len({item.capability_id for item in manifests})

    report = audit_capability_registry(ROOT, manifests)

    assert report.manifest_count == len(manifests)
    assert report.unsupported_count == 0
    assert all(item.maturity_supported for item in report.items)
    assert all(item.positive_cases for item in manifests)
    assert all(item.negative_cases for item in manifests)


def test_scientific_loop_claims_are_split_instead_of_overstated() -> None:
    records = capability_map()

    assert records["workflow.control_loop"].declared_maturity is CapabilityMaturity.C3_CONTROLLED_E2E
    assert records["scientific_completion.empirical_loop"].declared_maturity is CapabilityMaturity.C2_COMPONENT_VALIDATED
    assert records["scientific_completion.cross_domain_autonomy"].declared_maturity is CapabilityMaturity.C1_IMPLEMENTED
    assert "不证明" in records["workflow.control_loop"].claim_boundary
    assert "尚未达到" in records["scientific_completion.cross_domain_autonomy"].claim_boundary


def test_real_case_claim_cannot_be_made_without_real_case_evidence() -> None:
    manifest = CapabilityManifest(
        capability_id="example.overclaim",
        title="Overclaim",
        category="test",
        claim="real case works",
        claim_boundary="only component tested",
        declared_maturity=CapabilityMaturity.C4_REAL_CASE_VALIDATED,
        entrypoint="example",
        inputs=["input"],
        outputs=["output"],
        validators=["validator"],
        positive_cases=["positive"],
        negative_cases=["negative"],
        evidence=CapabilityEvidence(
            design=["README.md"],
            source=["README.md"],
            component_validation=["README.md"],
            controlled_e2e=["README.md"],
        ),
    )

    report = audit_capability_registry(ROOT, [manifest])

    assert report.unsupported_count == 1
    assert report.items[0].evidenced_maturity is CapabilityMaturity.C3_CONTROLLED_E2E
    assert report.items[0].permitted_claim == "only component tested"


def test_missing_local_evidence_stops_maturity_progression() -> None:
    manifest = CapabilityManifest(
        capability_id="example.missing",
        title="Missing",
        category="test",
        claim="implemented",
        claim_boundary="concept only",
        declared_maturity=CapabilityMaturity.C1_IMPLEMENTED,
        entrypoint="example",
        inputs=["input"],
        outputs=["output"],
        validators=["validator"],
        positive_cases=["positive"],
        negative_cases=["negative"],
        evidence=CapabilityEvidence(
            design=["README.md"],
            source=["research_forge/does_not_exist.py"],
        ),
    )

    report = audit_capability_registry(ROOT, [manifest])

    assert report.items[0].evidenced_maturity is CapabilityMaturity.C0_CONCEPT
    assert report.items[0].missing_evidence
    assert not report.items[0].maturity_supported


def test_controlled_clean_room_replay_does_not_claim_independent_validation() -> None:
    record = capability_map()["reproduction.controlled_clean_room_replay"]

    assert record.declared_maturity is CapabilityMaturity.C3_CONTROLLED_E2E
    assert "RF-E2" in record.claim_boundary
    assert "C5" in record.claim_boundary


def test_external_data_and_provenance_validators_keep_scientific_boundaries() -> None:
    records = capability_map()

    gx = records["data_quality.great_expectations_gate"]
    assert gx.declared_maturity is CapabilityMaturity.C3_CONTROLLED_E2E
    assert "不证明" in gx.claim_boundary

    prov = records["interchange.prov_external_validation"]
    assert prov.declared_maturity is CapabilityMaturity.C3_CONTROLLED_E2E
    assert "不宣称" in prov.claim_boundary


def test_public_package_is_real_case_but_not_external_reproduction() -> None:
    record = capability_map()["reproduction.public_minimal_research_package"]

    assert record.declared_maturity is CapabilityMaturity.C4_REAL_CASE_VALIDATED
    assert "C5" in record.claim_boundary
    assert "外部托管执行" in record.claim_boundary
    assert "独立科学操作者" in record.claim_boundary


def test_hidden_target_benchmark_has_real_case_but_not_independent_scope() -> None:
    record = capability_map()["scientific_execution.hidden_target_benchmark"]

    assert record.declared_maturity is CapabilityMaturity.C4_REAL_CASE_VALIDATED
    assert "C5" in record.claim_boundary
    assert "不代表" in record.claim_boundary
