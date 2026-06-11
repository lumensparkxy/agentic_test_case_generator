#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the FastAPI OpenAPI schema.")
    parser.add_argument("--output", required=True, help="Path to write the OpenAPI JSON schema.")
    parser.add_argument("--indent", type=int, default=2, help="JSON indentation level. Use 0 for compact output.")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    indent = None if args.indent == 0 else args.indent
    output_path.write_text(json.dumps(app.openapi(), indent=indent, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
