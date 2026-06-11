#!/usr/bin/env python3
"""Azure DevOps smoke test utility.

Reads the PAT from AZURE_DEVOPS_PAT so secrets are not stored in source files or
printed in command history. By default the script is read-only. Pass
--create-samples to create one sample Epic and two child requirement work items.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.adapters.azure_devops import AzureDevOpsAdapter, AzureDevOpsAdapterError  # noqa: E402


DEFAULT_ORG_URL = "https://dev.azure.com/neophilex"
CHILD_TYPE_CANDIDATES = (
    "Issue",
    "User Story",
    "Product Backlog Item",
    "Requirement",
    "Task",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test Azure DevOps integration without printing secrets.")
    parser.add_argument("--org-url", default=os.getenv("AZURE_DEVOPS_ORG_URL", DEFAULT_ORG_URL))
    parser.add_argument("--project", default=os.getenv("AZURE_DEVOPS_PROJECT", ""))
    parser.add_argument("--create-samples", action="store_true", help="Create one Epic and two child requirement work items.")
    parser.add_argument("--title-prefix", default=os.getenv("AZURE_DEVOPS_SAMPLE_PREFIX", "Agentic TCG Smoke"))
    parser.add_argument("--api-version", default=os.getenv("AZURE_DEVOPS_API_VERSION", "7.1"))
    return parser.parse_args()


def _require_pat() -> str:
    pat = os.getenv("AZURE_DEVOPS_PAT", "").strip()
    if not pat:
        raise SystemExit(
            "AZURE_DEVOPS_PAT is not set. Export it in your terminal or enter it silently before running this script."
        )
    return pat


def _choose_project(adapter: AzureDevOpsAdapter, project_name: str) -> str:
    projects = adapter.list_projects(max_results=50)
    print(f"Visible projects: {len(projects)}")
    for project in projects[:10]:
        print(f"- {project.name} ({project.state or 'state unknown'})")

    requested = project_name.strip() or adapter.default_project or ""
    if requested:
        for project in projects:
            if project.name.lower() == requested.lower():
                return project.name
        raise SystemExit(f"Project '{requested}' was not visible to this PAT.")

    if not projects:
        raise SystemExit("No visible Azure DevOps projects found for this PAT.")
    return projects[0].name


def _choose_type(available_names: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    available = list(available_names)
    by_lower = {name.lower(): name for name in available}
    for candidate in candidates:
        if candidate.lower() in by_lower:
            return by_lower[candidate.lower()]
    return None


def _create_samples(adapter: AzureDevOpsAdapter, *, project: str, work_item_type_names: list[str], title_prefix: str) -> None:
    epic_type = _choose_type(work_item_type_names, ["Epic"])
    child_type = _choose_type(work_item_type_names, CHILD_TYPE_CANDIDATES)
    if not epic_type:
        raise SystemExit("Could not create sample Epic because this project does not expose an Epic work item type.")
    if not child_type:
        raise SystemExit(
            "Could not create sample child work items because no compatible Issue/User Story/Product Backlog Item/Requirement/Task type was found."
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    epic = adapter.create_work_item(
        project=project,
        work_item_type=epic_type,
        fields={
            "System.Title": f"{title_prefix}: Sample requirements epic ({timestamp})",
            "System.Description": (
                "<p>Sample epic created by Agentic Test Case Generator smoke test. "
                "Safe to delete after verification.</p>"
            ),
        },
    )
    print(f"Created {epic_type} #{epic.work_item_id}: {epic.web_url}")

    samples = [
        (
            "User can upload requirement documents",
            "<p>As a QA analyst, I can upload Markdown, Word, or Excel requirement documents so requirements can be extracted.</p>",
        ),
        (
            "User can review generated requirements before test generation",
            "<p>As a QA lead, I can review and refine extracted requirements before generating test cases.</p>",
        ),
    ]
    for title, description in samples:
        child = adapter.create_work_item(
            project=project,
            work_item_type=child_type,
            fields={
                "System.Title": f"{title_prefix}: {title}",
                "System.Description": description,
            },
            parent_id=epic.work_item_id,
            relation_comment="Linked to sample requirements epic by Agentic TCG smoke test.",
        )
        print(f"Created {child_type} #{child.work_item_id}: {child.web_url}")


def main() -> int:
    args = _parse_args()
    pat = _require_pat()
    adapter = AzureDevOpsAdapter(
        organization_url=args.org_url,
        personal_access_token=pat,
        api_version=args.api_version,
    )
    print(f"Organization: {adapter.organization} ({adapter.organization_url})")
    validation = adapter.validate_connection()
    print(f"Connection validated. Visible project sample count: {validation.get('projectCountVisible', 0)}")
    project = _choose_project(adapter, args.project)
    print(f"Selected project: {project}")
    work_item_types = adapter.get_project_work_item_types(project)
    work_item_type_names = [item.name for item in work_item_types]
    print(f"Work item types: {', '.join(work_item_type_names) or 'none'}")

    if args.create_samples:
        _create_samples(adapter, project=project, work_item_type_names=work_item_type_names, title_prefix=args.title_prefix)
    else:
        print("Read-only smoke passed. Re-run with --create-samples to create sample Epic/child work items.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AzureDevOpsAdapterError as exc:
        print(f"Azure DevOps smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(exc.status_code or 1)