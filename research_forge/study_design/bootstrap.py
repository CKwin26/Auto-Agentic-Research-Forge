"""Register built-in designs and modules exactly once."""

from __future__ import annotations

_BOOTSTRAPPED = False


def ensure_builtin_components() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    from .designs.causal import CausalInference
    from .designs.factorial import FactorialExperiment
    from .designs.independent_group import IndependentGroupComparison
    from .designs.longitudinal import LongitudinalRepeatedMeasures
    from .designs.human_rating import OpenGenerationHumanRating
    from .designs.online_ab import OnlineABTest
    from .designs.survival import SurvivalAnalysis
    from .inference.bayesian import BayesianInference
    from .inference.multiplicity import MultiplicityControl
    from .inference.noninferiority import NoninferiorityEquivalence
    from .registry import register_inference_module, register_study_design

    register_study_design(IndependentGroupComparison())
    register_study_design(FactorialExperiment())
    register_study_design(LongitudinalRepeatedMeasures())
    register_study_design(SurvivalAnalysis())
    register_study_design(CausalInference())
    register_study_design(OnlineABTest())
    register_study_design(OpenGenerationHumanRating())
    register_inference_module(NoninferiorityEquivalence())
    register_inference_module(MultiplicityControl())
    register_inference_module(BayesianInference())
    _BOOTSTRAPPED = True


__all__ = ["ensure_builtin_components"]
