"""Composable study-design and inference layer for Research Forge."""

from .bootstrap import ensure_builtin_components
from .acceptance import DefaultStudyDesignAcceptanceEvaluator
from .compiler import compile_analysis_plan
from .completion import complete_study_design_contract
from .evidence import produce_claim_envelope
from .paper_case import (
    generate_independent_group_paper_package,
    publish_package_to_lab,
)
from .factorial_workflow_acceptance import (
    prepare_factorial_workflow_acceptance,
    run_factorial_workflow_acceptance,
)
from .longitudinal_workflow_acceptance import (
    prepare_longitudinal_workflow_acceptance,
    run_longitudinal_workflow_acceptance,
)
from .survival_workflow_acceptance import (
    prepare_survival_workflow_acceptance,
    run_survival_workflow_acceptance,
)
from .causal_workflow_acceptance import (
    prepare_causal_workflow_acceptance,
    run_causal_workflow_acceptance,
)
from .online_ab_workflow_acceptance import (
    prepare_online_ab_workflow_acceptance,
    run_online_ab_workflow_acceptance,
)
from .human_rating_workflow_acceptance import (
    prepare_human_rating_workflow_acceptance,
    run_human_rating_workflow_acceptance,
)
from .workflow_acceptance import (
    prepare_independent_group_workflow_acceptance,
    run_independent_group_workflow_acceptance,
)
from .workflow_package import (
    build_bayesian_workflow_package,
    build_causal_workflow_package,
    build_factorial_workflow_package,
    build_human_rating_workflow_package,
    build_independent_group_workflow_package,
    build_longitudinal_workflow_package,
    build_multiplicity_workflow_package,
    build_noninferiority_equivalence_workflow_package,
    build_online_ab_workflow_package,
    build_survival_workflow_package,
)
from .mutations import build_authority_mutation_report
from .negative_acceptance import required_negative_acceptance_cases
from .registry import (
    inference_module,
    inference_module_catalog,
    study_design,
    study_design_catalog,
)
from .validation import (
    blocking_issues_for_composable_contract,
    blocking_issues_for_realized_data,
    validate_composable_contract,
)

ensure_builtin_components()

__all__ = [
    "compile_analysis_plan",
    "DefaultStudyDesignAcceptanceEvaluator",
    "blocking_issues_for_composable_contract",
    "blocking_issues_for_realized_data",
    "build_authority_mutation_report",
    "required_negative_acceptance_cases",
    "complete_study_design_contract",
    "inference_module",
    "inference_module_catalog",
    "generate_independent_group_paper_package",
    "build_bayesian_workflow_package",
    "build_causal_workflow_package",
    "build_factorial_workflow_package",
    "build_human_rating_workflow_package",
    "build_independent_group_workflow_package",
    "build_longitudinal_workflow_package",
    "build_multiplicity_workflow_package",
    "build_noninferiority_equivalence_workflow_package",
    "build_online_ab_workflow_package",
    "build_survival_workflow_package",
    "prepare_factorial_workflow_acceptance",
    "prepare_causal_workflow_acceptance",
    "prepare_longitudinal_workflow_acceptance",
    "prepare_independent_group_workflow_acceptance",
    "prepare_online_ab_workflow_acceptance",
    "prepare_human_rating_workflow_acceptance",
    "prepare_survival_workflow_acceptance",
    "produce_claim_envelope",
    "publish_package_to_lab",
    "run_factorial_workflow_acceptance",
    "run_causal_workflow_acceptance",
    "run_longitudinal_workflow_acceptance",
    "run_independent_group_workflow_acceptance",
    "run_online_ab_workflow_acceptance",
    "run_human_rating_workflow_acceptance",
    "run_survival_workflow_acceptance",
    "study_design",
    "study_design_catalog",
    "validate_composable_contract",
]
