"""Profile Paper Lab: one compact view over black-box paper acceptance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import utc_now
from ..storage import (
    append_jsonl,
    load_jsonl,
    read_json,
    sha256_file,
    write_json_atomic,
)
from ..workflow_domain import stable_id
from .registry import inference_module_catalog, study_design_catalog
from .schemas import (
    HumanProfileReviewRecord,
    PaperPackageArtifact,
    ProfilePaperPackage,
)


PACKAGE_NAMES = (
    "01_task_brief.json", "02_research_contract.json",
    "03_contract_completion_report.json", "04_execution_supplement.json",
    "05_run_plan.json", "06_formal_outputs", "07_evaluation.json",
    "08_independent_recalculation.json", "09_statistics.json",
    "10_scientific_verdict.json", "11_evidence_claim_map.json", "12_figures",
    "13_manuscript.tex", "14_manuscript.pdf", "15_reproduction_package",
    "16_profile_acceptance_report.json", "17_profile_acceptance_report.html",
    "18_human_review_packet.pdf",
)


def inspect_profile_paper_package(root: Path, *, profile_id: str, study_id: str, case_kind: str = "normal") -> ProfilePaperPackage:
    artifacts: list[PaperPackageArtifact] = []
    for ordinal, name in enumerate(PACKAGE_NAMES, start=1):
        path = root / name
        present = path.exists()
        digest = sha256_file(path) if present and path.is_file() else None
        if present and path.is_dir():
            digest = _directory_digest(path)
        artifacts.append(PaperPackageArtifact(ordinal=ordinal, name=name, relative_path=name, sha256=digest, present=present))
    acceptance_path = root / "16_profile_acceptance_report.json"
    acceptance = read_json(acceptance_path) if acceptance_path.is_file() else {}
    verdict_path = root / "10_scientific_verdict.json"
    verdict = read_json(verdict_path) if verdict_path.is_file() else {}
    return ProfilePaperPackage(
        package_id=str(acceptance.get("package_id") or stable_id("profile-paper-package", profile_id, study_id, str(root.resolve()))),
        profile_id=profile_id, study_id=study_id, case_kind=case_kind,
        artifacts=artifacts,
        scientific_verdict=str(verdict.get("verdict") or acceptance.get("scientific_verdict") or "unverifiable").lower(),
        automatic_acceptance="pass" if acceptance.get("valid") is True else "fail" if acceptance else "incomplete",
        independent_recalculation_passed=bool(acceptance.get("independent_recalculator_agreement", {}).get("passed")),
        paper_audit_passed=all((acceptance.get("paper_audit") or {}).values()) if acceptance.get("paper_audit") else False,
        formal_workflow_completed=bool(acceptance.get("formal_workflow_completed")),
        canonical_stage_four_completed=bool(acceptance.get("canonical_stage_four_completed")),
        workflow_receipts={
            str(key): bool(value)
            for key, value in (acceptance.get("workflow_receipts") or {}).items()
        },
        clean_room_replay_passed=bool((acceptance.get("replay") or {}).get("clean_room_passed")),
        real_nonfixture_case=bool(acceptance.get("real_nonfixture_case")),
        external_independent_reproduction=bool(acceptance.get("external_independent_reproduction")),
    )


def _directory_digest(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(bytes.fromhex(sha256_file(child)))
    return digest.hexdigest()


def append_human_review(root: Path, payload: dict[str, Any]) -> HumanProfileReviewRecord:
    record = HumanProfileReviewRecord(
        review_id=stable_id("profile-review", payload["package_id"], payload["reviewer"], utc_now(), json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        created_at=utc_now(), **payload,
    )
    append_jsonl(root / "profile-paper-lab" / "human_reviews.jsonl", record)
    return record


def profile_paper_lab_snapshot(root: Path) -> dict[str, Any]:
    package_root = root / "profile-paper-lab" / "packages"
    packages: dict[str, ProfilePaperPackage] = {}
    package_locations: dict[str, str] = {}
    if package_root.is_dir():
        for manifest in package_root.glob("*/package.json"):
            package = ProfilePaperPackage.model_validate(read_json(manifest))
            packages[package.profile_id] = package
            location_path = manifest.with_name("location.json")
            if location_path.is_file():
                location = read_json(location_path)
                candidate = Path(str(location.get("package_root", "")))
                manuscript = candidate / "14_manuscript.pdf"
                if manuscript.is_file():
                    package_locations[package.profile_id] = str(manuscript.resolve())
    # Historical human-review records remain readable, but an external
    # independent researcher is not a Profile maturity prerequisite.  The
    # canonical Stage 4 AI scientific panel and deterministic paper audit are
    # the platform review receipts; final publication approval belongs to the
    # project owner.
    reviews = load_jsonl(root / "profile-paper-lab" / "human_reviews.jsonl")
    optional_human_status = {
        str(item.get("profile_id")): str(item.get("decision", "pending"))
        for item in reviews
    }
    designs = []
    for item in study_design_catalog():
        package = packages.get(str(item["design_id"]))
        designs.append({
            **item,
            "verified_maturity": package.derived_maturity.value if package else "c2_dry_run" if item["formal_execution_supported"] else item["maturity"],
            "latest_package": package.model_dump(mode="json") if package else None,
            "manuscript_path": package_locations.get(str(item["design_id"])),
            "automatic_acceptance": package.automatic_acceptance if package else "incomplete",
            "independent_recalculation": "pass" if package and package.independent_recalculation_passed else "not_complete",
            "ai_scientific_review": (
                "passed" if package and package.paper_audit_passed else "pending"
            ),
            "external_human_review": optional_human_status.get(
                str(item["design_id"]), "not_required"
            ),
        })
    return {
        "profiles": designs,
        "inference_modules": inference_module_catalog(),
        "required_package_artifacts": list(PACKAGE_NAMES),
        "maturity_rule": (
            "C3 requires a complete evidence-driven paper package plus persisted "
            "Idea-to-paper receipts from all four canonical workflow phases; direct "
            "component generators stop at C2."
        ),
    }


__all__ = ["PACKAGE_NAMES", "append_human_review", "inspect_profile_paper_package", "profile_paper_lab_snapshot"]
