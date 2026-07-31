from pathlib import Path

from research_forge.upstream_capabilities import (
    AdoptionStatus,
    ProofLevel,
    UPSTREAM_CAPABILITIES,
    audit_upstream_capabilities,
    upstream_capability_map,
)


def test_upstream_capability_registry_has_unique_ids_and_sources() -> None:
    assert len(upstream_capability_map()) == len(UPSTREAM_CAPABILITIES)
    assert all(
        item.source_url.startswith(
            ("https://", "local-skill://", "missing-skill://")
        )
        for item in UPSTREAM_CAPABILITIES
    )
    assert all(
        item.source_url.rstrip("/") != "https://github.com"
        for item in UPSTREAM_CAPABILITIES
    )
    assert all(item.upstream_capabilities for item in UPSTREAM_CAPABILITIES)
    assert all(item.permitted_claim for item in UPSTREAM_CAPABILITIES)


def test_verified_runtime_claims_have_real_run_and_integration_proof() -> None:
    verified = [
        item
        for item in UPSTREAM_CAPABILITIES
        if item.status is AdoptionStatus.VERIFIED_RUNTIME
    ]
    assert {item.name for item in verified} == {
        "PaperQA2",
        "AIRS-Bench",
        "draw.io",
        "Workflow Run RO-Crate",
        "Great Expectations",
        "W3C PROV",
    }
    for item in verified:
        assert ProofLevel.INTEGRATION in item.proof_levels
        assert ProofLevel.REAL_RUN in item.proof_levels
        assert item.real_run_evidence
        assert item.runtime_revalidated_at


def test_known_overclaims_remain_explicitly_bounded() -> None:
    records = upstream_capability_map()
    assert len(records) == 42
    assert records["upstream-nuwa-panel"].status is AdoptionStatus.PARTIAL_RUNTIME
    assert "exact Nuwa protocol" in records["upstream-nuwa-panel"].permitted_claim
    assert (
        records["upstream-read-wechat-skill"].status
        is AdoptionStatus.STUB_OR_MISSING
    )
    assert (
        records["upstream-self-play-skill"].status
        is AdoptionStatus.STUB_OR_MISSING
    )
    assert records["upstream-deepchecks"].status is AdoptionStatus.NOT_ADOPTED
    assert records["upstream-drawio"].status is AdoptionStatus.VERIFIED_RUNTIME
    assert records["upstream-drawio"].runtime_revalidated_at == "2026-07-31"
    assert records["upstream-airs-bench"].status is AdoptionStatus.VERIFIED_RUNTIME
    assert ProofLevel.EXTERNAL_ACCEPTANCE not in records["upstream-airs-bench"].proof_levels
    assert (
        records["upstream-manubot"].status
        is AdoptionStatus.STUB_OR_MISSING
    )
    assert (
        records["upstream-statcheck"].status
        is AdoptionStatus.STUB_OR_MISSING
    )
    assert records["upstream-w3c-prov"].status is AdoptionStatus.VERIFIED_RUNTIME
    assert records["upstream-great-expectations"].status is AdoptionStatus.VERIFIED_RUNTIME
    assert records["upstream-ro-crate"].status is AdoptionStatus.VERIFIED_RUNTIME
    assert records["upstream-ro-crate"].runtime_revalidated_at == "2026-08-01"
    assert "Workflow Run Crate 0.5" in records["upstream-ro-crate"].permitted_claim
    assert records["upstream-openml"].status is AdoptionStatus.PARTIAL_RUNTIME
    for architecture_reference in (
        "upstream-osf",
        "upstream-mlflow",
        "upstream-nextflow",
        "upstream-snakemake",
        "upstream-mlperf",
        "upstream-code-ocean",
        "upstream-acm-artifact-evaluation",
        "upstream-slsa",
        "upstream-sigstore",
        "upstream-awesome-ai-research-writing",
    ):
        assert (
            records[architecture_reference].status
            is AdoptionStatus.ARCHITECTURE_ONLY
        )
    assert (
        records["upstream-ccf-deadlines"].status
        is AdoptionStatus.ADAPTER_ONLY
    )


def test_architecture_references_do_not_claim_runtime_proof() -> None:
    for item in UPSTREAM_CAPABILITIES:
        if item.status is AdoptionStatus.ARCHITECTURE_ONLY:
            assert ProofLevel.INTEGRATION not in item.proof_levels
            assert ProofLevel.REAL_RUN not in item.proof_levels


def test_machine_audit_proves_every_registry_claim_has_required_evidence() -> None:
    root = Path(__file__).resolve().parents[1]

    report = audit_upstream_capabilities(root)

    assert report.record_count == len(UPSTREAM_CAPABILITIES)
    assert report.supported_count == report.record_count
    assert report.unsupported_count == 0
    assert all(record.claim_supported for record in report.records)
    assert all(not record.missing_local_evidence for record in report.records)
