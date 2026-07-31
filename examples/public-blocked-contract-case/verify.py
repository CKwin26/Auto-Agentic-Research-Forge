from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from research_forge.contract_compiler import compile_research_contract
from research_forge.public_package import build_public_blocked_contract_package
from research_forge.workflow_domain import (
    Hypothesis,
    HypothesisRole,
    ProtocolStatus,
    ResearchContractVersion,
    Stage3Profile,
)


contract = ResearchContractVersion(
    study_id="public-blocked-contract-case",
    version=1,
    scope_version=1,
    hypotheses=[
        Hypothesis(
            hypothesis_id="hypothesis-public-primary",
            statement="The treatment improves accuracy over baseline.",
            role=HypothesisRole.PRIMARY,
            decision_rule={"supported_if": "effect exceeds a frozen threshold"},
        )
    ],
    data_boundary={
        "population": "candidate local rows",
        "denominator": "all qualified rows",
        "unit_of_analysis": "row",
    },
    data_requirements={
        "population": "candidate local rows",
        "denominator": "all qualified rows",
        "unit_of_analysis": "row",
    },
    metrics=[{"name": "accuracy", "direction": "higher_is_better"}],
    baseline={
        "experiment_id": "baseline",
        "action_id": "baseline-action",
        "behavior": "apply the same classifier to every formal row",
    },
    treatment={
        "experiment_id": "treatment",
        "action_id": "treatment-action",
        "behavior": "apply the same classifier to every formal row",
    },
    tasks=["classification-task"],
    seeds=[1],
    runtime_binding={},
    evaluator_policy={"authority": "deterministic evaluator"},
    experiment_profile=Stage3Profile.COMPUTATIONAL_PAIRED_COMPARISON_V1,
    output_schema={"type": "object"},
    statistical_rules={
        "effect_threshold": 0.05,
        "effect_scale": "absolute",
        "effect_unit": "proportion",
        "missing_data_policy": "inconclusive",
        "success_threshold": "effect >= 0.05",
        "method": "paired difference",
    },
    estimand={
        "population": "candidate local rows",
        "experimental_unit": "row",
        "variance_unit": "row",
        "outcome": "accuracy",
        "contrast": "treatment minus baseline",
    },
    environment_requirements={"network": "offline"},
    resource_policy={"selected_only": True},
    budget_security={"budget": {"max_runs": 2}},
)

report = compile_research_contract(contract)
required_codes = {
    "ARM_DELTA_MISSING",
    "APPROVED_RESOURCES_MISSING",
    "TARGET_RULE_MISSING",
    "SAMPLING_FRAME_MISSING",
    "METRIC_FORMULA_MISSING",
}
observed = {
    issue.split(":", 1)[0] for issue in report.blocking_issues
}
passed = (
    report.protocol_status is ProtocolStatus.BLOCKED
    and not report.compile_passed
    and not report.run_specifications
    and required_codes.issubset(observed)
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-package", type=Path)
    args = parser.parse_args()
    if args.build_package:
        result = build_public_blocked_contract_package(
            contract,
            output_path=args.build_package,
            required_issue_codes=required_codes,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(
        json.dumps(
            {
                "passed": passed,
                "protocol_status": report.protocol_status.value,
                "compile_passed": report.compile_passed,
                "run_specification_count": len(report.run_specifications),
                "required_issue_codes": sorted(required_codes),
                "observed_issue_codes": sorted(observed),
                "scientific_verdict_created": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
