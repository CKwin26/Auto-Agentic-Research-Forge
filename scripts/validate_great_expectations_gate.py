from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_forge.great_expectations_gate import (
    FrozenDataQualityContract,
    validate_frozen_tabular_resource,
)
from research_forge.storage import read_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset")
    parser.add_argument("contract")
    parser.add_argument("report")
    args = parser.parse_args()
    contract = FrozenDataQualityContract.model_validate(
        read_json(Path(args.contract))
    )
    result = validate_frozen_tabular_resource(
        dataset_path=args.dataset,
        contract=contract,
        report_output=args.report,
    )
    print(result["report_subject_sha256"])
    return 0 if result["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
