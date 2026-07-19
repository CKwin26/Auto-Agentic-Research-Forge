from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path


MANIFEST_PATH = Path("/opt/research-forge/capabilities.json")
IMPORT_NAMES = {
    "python-dateutil": "dateutil",
    "scikit-learn": "sklearn",
}


def verify() -> dict[str, object]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    errors: list[str] = []
    expected_python = str(manifest["python"])
    actual_python = platform.python_version()
    if not actual_python.startswith(expected_python + "."):
        errors.append(f"python: expected {expected_python}.x, got {actual_python}")

    installed: dict[str, str] = {}
    for distribution, expected in dict(manifest["packages"]).items():
        try:
            actual = importlib.metadata.version(distribution)
            importlib.import_module(IMPORT_NAMES.get(distribution, distribution.replace("-", "_")))
            installed[distribution] = actual
            if actual != expected:
                errors.append(f"{distribution}: expected {expected}, got {actual}")
        except Exception as exc:
            errors.append(f"{distribution}: {type(exc).__name__}: {exc}")

    if not errors:
        try:
            import numpy as np
            import pandas as pd
            from scipy import sparse
            from sklearn.pipeline import make_pipeline
            from sklearn.svm import LinearSVC
            from sklearn.feature_extraction.text import TfidfVectorizer

            if float(np.asarray([1.0, 2.0]) @ np.asarray([3.0, 4.0])) != 11.0:
                raise RuntimeError("NumPy dot-product smoke test failed")
            if int(sparse.csr_matrix([[0, 1], [2, 0]]).sum()) != 3:
                raise RuntimeError("SciPy sparse-matrix smoke test failed")
            if int(pd.DataFrame({"value": [1, 2]}).value.sum()) != 3:
                raise RuntimeError("pandas dataframe smoke test failed")
            model = make_pipeline(TfidfVectorizer(), LinearSVC(random_state=0))
            model.fit(["cat sat", "dog ran", "cat slept", "dog barked"], [0, 1, 0, 1])
            if int(model.predict(["cat ran"])[0]) not in {0, 1}:
                raise RuntimeError("scikit-learn text classifier smoke test failed")
        except Exception as exc:
            errors.append(f"smoke-test: {type(exc).__name__}: {exc}")

    return {
        **manifest,
        "status": "ok" if not errors else "error",
        "verified": not errors,
        "actual_python": actual_python,
        "installed_packages": installed,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = verify()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("controlled environment: " + str(result["status"]))
        for error in result["errors"]:
            print(error, file=sys.stderr)
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
