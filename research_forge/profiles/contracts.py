"""Strongly validated scientific parameters for certified Profile Bundles."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ..models import StrictModel


class FrozenBootstrapSpec(StrictModel):
    method: Literal["cluster_bootstrap"]
    resample_unit: str = Field(min_length=1)
    resamples: int = Field(ge=1_000, le=1_000_000)
    seed: int
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1.0)


class PairedContinuousV2Parameters(StrictModel):
    pairing_key: str = Field(min_length=1)
    cluster_id_field: str = Field(min_length=1)
    variance_unit: Literal["cluster"]
    inference_spec: FrozenBootstrapSpec

    @model_validator(mode="after")
    def inference_uses_registered_cluster(
        self,
    ) -> "PairedContinuousV2Parameters":
        if self.inference_spec.resample_unit != self.cluster_id_field:
            raise ValueError(
                "bootstrap resample_unit must equal cluster_id_field"
            )
        return self


class PairedBinaryIndependentParameters(StrictModel):
    pairing_key: str = Field(min_length=1)
    success_definition: str = Field(min_length=1)
    variance_unit: Literal["pair"]
    inference_method: Literal["exact_mcnemar"] = "exact_mcnemar"
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1.0)


class PairedBinaryClusteredParameters(StrictModel):
    pairing_key: str = Field(min_length=1)
    cluster_id_field: str = Field(min_length=1)
    success_definition: str = Field(min_length=1)
    variance_unit: Literal["cluster"]
    inference_spec: FrozenBootstrapSpec

    @model_validator(mode="after")
    def inference_uses_registered_cluster(
        self,
    ) -> "PairedBinaryClusteredParameters":
        if self.inference_spec.resample_unit != self.cluster_id_field:
            raise ValueError(
                "bootstrap resample_unit must equal cluster_id_field"
            )
        return self


class PairedMultiArmParameters(StrictModel):
    pairing_key: str = Field(min_length=1)
    cluster_id_field: str = Field(min_length=1)
    variance_unit: Literal["cluster"]
    inference_spec: FrozenBootstrapSpec
    arms: list[str] = Field(min_length=3, max_length=16)
    treatment_arm: str = Field(min_length=1)
    control_arms: list[str] = Field(min_length=2, max_length=15)
    decision_rule: Literal["all_primary_contrasts"] = "all_primary_contrasts"

    @model_validator(mode="after")
    def validate_registered_arms(self) -> "PairedMultiArmParameters":
        if len(self.arms) != len(set(self.arms)):
            raise ValueError("multi-arm profile arms must be unique")
        if self.treatment_arm not in self.arms:
            raise ValueError("treatment_arm must belong to arms")
        if len(self.control_arms) != len(set(self.control_arms)):
            raise ValueError("control_arms must be unique")
        if self.treatment_arm in self.control_arms:
            raise ValueError("treatment_arm cannot also be a control arm")
        if any(item not in self.arms for item in self.control_arms):
            raise ValueError("every control arm must belong to arms")
        if set(self.arms) != {self.treatment_arm, *self.control_arms}:
            raise ValueError(
                "every registered arm must be either treatment or control"
            )
        if self.inference_spec.resample_unit != self.cluster_id_field:
            raise ValueError(
                "bootstrap resample_unit must equal cluster_id_field"
            )
        return self


class TabularEstimatorBinding(StrictModel):
    """Frozen import and parameters for a sklearn-style estimator.

    The implementation is intentionally a protocol rather than a hard
    dependency on scikit-learn: the imported class must expose ``fit`` and
    ``predict`` and may expose ``predict_proba`` or ``decision_function``.
    """

    import_path: str = Field(
        min_length=3,
        pattern=r"^[a-zA-Z_][a-zA-Z0-9_.]*:[a-zA-Z_][a-zA-Z0-9_]*$",
    )
    parameters: dict[str, Any] = Field(default_factory=dict)


class TabularMLParameters(StrictModel):
    task_type: Literal["classification", "regression"]
    dataset_path: str = Field(min_length=1)
    id_field: str = Field(min_length=1)
    target_field: str = Field(min_length=1)
    feature_fields: list[str] = Field(min_length=1)
    split_strategy: Literal["fixed_split", "kfold_cv"]
    split_field: str | None = None
    train_values: list[str] = Field(default_factory=lambda: ["train"])
    test_values: list[str] = Field(default_factory=lambda: ["test"])
    folds: int | None = Field(default=None, ge=2, le=20)
    primary_metric: Literal["accuracy", "f1", "auroc", "rmse", "mae"]
    metrics: list[
        Literal["accuracy", "f1", "auroc", "rmse", "mae"]
    ] = Field(min_length=1)
    positive_label: str | None = None
    baseline_estimator: TabularEstimatorBinding
    treatment_estimator: TabularEstimatorBinding
    variance_unit: Literal["registered_pair"] = "registered_pair"
    missing_value_policy: Literal["block"] = "block"
    leakage_policy: Literal["target_excluded_and_split_frozen"] = (
        "target_excluded_and_split_frozen"
    )

    @model_validator(mode="after")
    def validate_tabular_semantics(self) -> "TabularMLParameters":
        if len(self.feature_fields) != len(set(self.feature_fields)):
            raise ValueError("feature_fields must be unique")
        forbidden = {self.id_field, self.target_field}
        if self.split_field:
            forbidden.add(self.split_field)
        leaked = sorted(forbidden.intersection(self.feature_fields))
        if leaked:
            raise ValueError(
                "feature_fields contain identifier, target, or split fields: "
                + ", ".join(leaked)
            )
        if self.primary_metric not in self.metrics:
            raise ValueError("primary_metric must be included in metrics")
        allowed = (
            {"accuracy", "f1", "auroc"}
            if self.task_type == "classification"
            else {"rmse", "mae"}
        )
        unsupported = sorted(set(self.metrics).difference(allowed))
        if unsupported:
            raise ValueError(
                f"metrics do not match {self.task_type}: "
                + ", ".join(unsupported)
            )
        if self.task_type == "classification" and self.positive_label is None:
            raise ValueError("classification requires positive_label")
        if self.split_strategy == "fixed_split":
            if not self.split_field:
                raise ValueError("fixed_split requires split_field")
            if not self.train_values or not self.test_values:
                raise ValueError(
                    "fixed_split requires non-empty train_values and test_values"
                )
            if set(self.train_values).intersection(self.test_values):
                raise ValueError("train_values and test_values must be disjoint")
            if self.folds is not None:
                raise ValueError("fixed_split cannot also declare folds")
        elif self.folds is None:
            raise ValueError("kfold_cv requires folds")
        if self.baseline_estimator == self.treatment_estimator:
            raise ValueError(
                "baseline and treatment estimators must differ"
            )
        return self


class BenchmarkPredictionParameters(StrictModel):
    """Frozen submission/evaluator boundary for benchmark-style tasks."""

    task_type: Literal["classification", "regression"]
    candidate_input_path: str = Field(min_length=1)
    evaluator_target_path: str = Field(min_length=1)
    id_field: str = Field(min_length=1)
    prediction_field: str = Field(min_length=1)
    target_field: str = Field(min_length=1)
    score_field: str | None = None
    primary_metric: Literal["accuracy", "f1", "auroc", "rmse", "mae"]
    metrics: list[
        Literal["accuracy", "f1", "auroc", "rmse", "mae"]
    ] = Field(min_length=1)
    positive_label: str | None = None
    submission_format: Literal["csv"] = "csv"
    prediction_artifact_path: str = Field(min_length=1)
    variance_unit: Literal["registered_pair"] = "registered_pair"

    @model_validator(mode="after")
    def validate_benchmark_boundary(self) -> "BenchmarkPredictionParameters":
        if self.candidate_input_path == self.evaluator_target_path:
            raise ValueError(
                "candidate input and evaluator target must be different files"
            )
        if self.primary_metric not in self.metrics:
            raise ValueError("primary_metric must be included in metrics")
        allowed = (
            {"accuracy", "f1", "auroc"}
            if self.task_type == "classification"
            else {"rmse", "mae"}
        )
        unsupported = sorted(set(self.metrics).difference(allowed))
        if unsupported:
            raise ValueError(
                f"metrics do not match {self.task_type}: "
                + ", ".join(unsupported)
            )
        if self.task_type == "classification" and self.positive_label is None:
            raise ValueError("classification requires positive_label")
        if "auroc" in self.metrics and not self.score_field:
            raise ValueError("AUROC requires score_field")
        return self


class ExistingPythonProjectParameters(StrictModel):
    """Frozen adapter for replaying a pre-existing Python experiment."""

    container_image: str = Field(min_length=1)
    project_code_paths: list[str] = Field(min_length=1)
    requirements_lock_path: str = Field(min_length=1)
    candidate_data_path: str = Field(min_length=1)
    evaluator_target_path: str = Field(min_length=1)
    baseline_command: list[str] = Field(min_length=1)
    treatment_command: list[str] = Field(min_length=1)
    evaluator_command: list[str] = Field(min_length=1)
    prediction_artifact_path: str = Field(min_length=1)
    result_artifact_path: str = Field(min_length=1)
    metric_names: list[str] = Field(min_length=1)
    primary_metric: str = Field(min_length=1)
    result_format: Literal["json"] = "json"
    network_access: Literal[False] = False
    clean_replay_required: Literal[True] = True

    @model_validator(mode="after")
    def validate_existing_project_boundary(
        self,
    ) -> "ExistingPythonProjectParameters":
        if len(self.project_code_paths) != len(set(self.project_code_paths)):
            raise ValueError("project_code_paths must be unique")
        if self.requirements_lock_path not in self.project_code_paths:
            raise ValueError(
                "requirements_lock_path must be included in project_code_paths"
            )
        if self.candidate_data_path == self.evaluator_target_path:
            raise ValueError(
                "candidate data and evaluator target must be separate"
            )
        if self.baseline_command == self.treatment_command:
            raise ValueError(
                "baseline and treatment commands must be operationally distinct"
            )
        if self.primary_metric not in self.metric_names:
            raise ValueError("primary_metric must be included in metric_names")
        for command in (
            self.baseline_command,
            self.treatment_command,
            self.evaluator_command,
        ):
            if any(not token or "\x00" in token for token in command):
                raise ValueError("commands contain empty or invalid arguments")
        return self


class DeterministicSimulationParameters(StrictModel):
    """Frozen paired simulation with common random numbers.

    This deliberately narrow fixture is useful for validating the scientific
    execution machinery because the treatment effect is known exactly while
    every row still contains deterministic seeded variation.
    """

    sample_count: int = Field(ge=10, le=1_000_000)
    baseline_location: float
    treatment_effect: float
    noise_scale: float = Field(ge=0.0)
    primary_metric: Literal["mean_paired_difference"] = (
        "mean_paired_difference"
    )
    effect_threshold: float
    direction: Literal["higher_is_better", "lower_is_better"]
    pairing_policy: Literal["common_random_numbers"] = "common_random_numbers"

    @model_validator(mode="after")
    def treatment_is_scientifically_distinct(
        self,
    ) -> "DeterministicSimulationParameters":
        if self.treatment_effect == 0.0:
            raise ValueError("treatment_effect must be non-zero")
        return self


__all__ = [
    "FrozenBootstrapSpec",
    "PairedBinaryClusteredParameters",
    "PairedBinaryIndependentParameters",
    "PairedContinuousV2Parameters",
    "PairedMultiArmParameters",
    "TabularEstimatorBinding",
    "TabularMLParameters",
    "BenchmarkPredictionParameters",
    "ExistingPythonProjectParameters",
    "DeterministicSimulationParameters",
]
