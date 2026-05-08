#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the FastAPI OpenAPI schema for contract validation or type generation.")
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the OpenAPI schema JSON. If omitted, the schema is printed to stdout.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation level. Use 0 for compact output.",
    )
    return parser.parse_args()


def _serialize_schema(schema: dict[str, Any], indent: int) -> str:
    return json.dumps(schema, indent=indent or None, sort_keys=True, default=str) + "\n"


def main() -> int:
    args = _parse_args()

    from app.main import app

    schema = app.openapi()
    content = _serialize_schema(schema, args.indent)

    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        print(f"Wrote OpenAPI schema to {output_path}")
    else:
        print(content, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
