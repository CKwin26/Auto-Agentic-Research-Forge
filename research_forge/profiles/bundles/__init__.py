from .computational_paired_comparison_v1 import BUNDLE as PAIRED_V1
from .computational_paired_comparison_v2 import BUNDLE as PAIRED_V2
from .paired_binary_clustered_v1 import BUNDLE as BINARY_CLUSTERED_V1
from .paired_binary_independent_v1 import BUNDLE as BINARY_INDEPENDENT_V1
from .paired_multi_arm_ablation_v1 import BUNDLE as MULTI_ARM_V1
from .tabular_ml_v1 import BUNDLE as TABULAR_ML_V1
from .benchmark_prediction_v1 import BUNDLE as BENCHMARK_PREDICTION_V1
from .existing_python_project_v1 import BUNDLE as EXISTING_PYTHON_PROJECT_V1
from .deterministic_simulation_v1 import BUNDLE as DETERMINISTIC_SIMULATION_V1
from .time_series_backtest_v1 import BUNDLE as TIME_SERIES_BACKTEST_V1
from .llm_evaluation_v1 import BUNDLE as LLM_EVALUATION_V1

ALL_BUNDLES = (
    TIME_SERIES_BACKTEST_V1,
    LLM_EVALUATION_V1,
    DETERMINISTIC_SIMULATION_V1,
    EXISTING_PYTHON_PROJECT_V1,
    BENCHMARK_PREDICTION_V1,
    TABULAR_ML_V1,
    PAIRED_V1,
    PAIRED_V2,
    BINARY_INDEPENDENT_V1,
    BINARY_CLUSTERED_V1,
    MULTI_ARM_V1,
)

__all__ = [
    "ALL_BUNDLES",
    "BINARY_CLUSTERED_V1",
    "BINARY_INDEPENDENT_V1",
    "MULTI_ARM_V1",
    "PAIRED_V1",
    "PAIRED_V2",
    "TABULAR_ML_V1",
    "BENCHMARK_PREDICTION_V1",
    "EXISTING_PYTHON_PROJECT_V1",
    "DETERMINISTIC_SIMULATION_V1",
    "TIME_SERIES_BACKTEST_V1",
    "LLM_EVALUATION_V1",
]
