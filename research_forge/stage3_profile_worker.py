"""Killable worker for one Stage 3 structured Codex generation attempt."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .agent_runtime import generate_stage3_profile_v1_package
from .storage import read_json, write_json_atomic


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    request_path = Path(args.request).resolve()
    response_path = Path(args.response).resolve()
    request = read_json(request_path)
    try:
        package = asyncio.run(
            generate_stage3_profile_v1_package(
                str(request["prompt"]),
                cwd=Path(str(request["cwd"])).resolve(),
            )
        )
    except Exception as exc:
        write_json_atomic(
            response_path,
            {
                "schema_version": 1,
                "error_type": type(exc).__name__,
                "error": str(exc)[:4_000],
            },
        )
        return 1
    write_json_atomic(
        response_path,
        {
            "schema_version": 1,
            "package": package.model_dump(mode="json"),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
