#!/usr/bin/env python3
"""Re-encrypt stored integration credentials with the configured primary key."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.azure_devops_connection_service import reencrypt_azure_devops_connection_credentials
from app.services.jira_connection_service import reencrypt_jira_connection_credentials


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-encrypt JIRA and Azure DevOps connection credentials with the primary key.")
    parser.add_argument(
        "--provider",
        choices=["all", "jira", "azure-devops"],
        default="all",
        help="Provider credentials to process.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write updated ciphertext. Omit for a dry-run count only.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dry_run = not args.apply
    results = []

    if args.provider in {"all", "jira"}:
        results.append(reencrypt_jira_connection_credentials(dry_run=dry_run))
    if args.provider in {"all", "azure-devops"}:
        results.append(reencrypt_azure_devops_connection_credentials(dry_run=dry_run))

    print(json.dumps({"dry_run": dry_run, "results": results}, sort_keys=True))
    return 1 if any(int(result.get("failed", 0)) for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
