from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_forge.prov_validation import validate_prov_external


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prov")
    parser.add_argument("report")
    args = parser.parse_args()
    report = validate_prov_external(
        prov_path=args.prov,
        report_output=args.report,
    )
    print(report["report_subject_sha256"])
    return 0 if report["conforms"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
