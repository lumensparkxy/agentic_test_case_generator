#!/usr/bin/env python3
"""Validate and add the versioned Firestore composite indexes safely.

The default mode is read-only. ``--apply`` creates only missing indexes and
waits for every versioned index to become ready. Existing remote indexes are
never deleted or modified.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "firestore.indexes.json"
QUERY_SCOPE_ARGUMENTS = {
    "COLLECTION": "collection",
    "COLLECTION_GROUP": "collection-group",
}
ORDER_ARGUMENTS = {
    "ASCENDING": "ascending",
    "DESCENDING": "descending",
}


@dataclass(frozen=True)
class IndexSpec:
    collection_group: str
    query_scope: str
    fields: tuple[tuple[str, str], ...]

    @property
    def key(self) -> tuple[str, str, tuple[tuple[str, str], ...]]:
        return (self.collection_group, self.query_scope, self.fields)

    def create_command(self, *, project_id: str, database: str) -> list[str]:
        command = [
            "gcloud",
            "firestore",
            "indexes",
            "composite",
            "create",
            f"--project={project_id}",
            f"--database={database}",
            f"--collection-group={self.collection_group}",
            f"--query-scope={QUERY_SCOPE_ARGUMENTS[self.query_scope]}",
        ]
        command.extend(f"--field-config=field-path={field_path},order={ORDER_ARGUMENTS[order]}" for field_path, order in self.fields)
        command.extend(("--async", "--quiet"))
        return command


def _parse_index(payload: dict[str, Any], *, source: str) -> IndexSpec:
    collection_group = str(payload.get("collectionGroup") or "").strip()
    query_scope = str(payload.get("queryScope") or "").strip().upper()
    raw_fields = payload.get("fields")

    if not collection_group:
        raise ValueError(f"{source}: collectionGroup is required")
    if query_scope not in QUERY_SCOPE_ARGUMENTS:
        raise ValueError(f"{source}: unsupported queryScope {query_scope!r}")
    if not isinstance(raw_fields, list) or len(raw_fields) < 2:
        raise ValueError(f"{source}: fields must contain at least two ordered fields")

    fields: list[tuple[str, str]] = []
    for position, raw_field in enumerate(raw_fields, start=1):
        if not isinstance(raw_field, dict):
            raise ValueError(f"{source}: field {position} must be an object")
        field_path = str(raw_field.get("fieldPath") or "").strip()
        order = str(raw_field.get("order") or "").strip().upper()
        if not field_path:
            raise ValueError(f"{source}: field {position} requires fieldPath")
        if order not in ORDER_ARGUMENTS:
            raise ValueError(f"{source}: field {field_path!r} requires ASCENDING or DESCENDING order")
        fields.append((field_path, order))

    return IndexSpec(
        collection_group=collection_group,
        query_scope=query_scope,
        fields=tuple(fields),
    )


def load_manifest(path: Path) -> list[IndexSpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Firestore index manifest must contain one JSON object")
    raw_indexes = payload.get("indexes")
    if not isinstance(raw_indexes, list) or not raw_indexes:
        raise ValueError("Firestore index manifest must contain a non-empty indexes array")
    if payload.get("fieldOverrides", []) != []:
        raise ValueError("This release helper does not manage field overrides")

    indexes = [_parse_index(item, source=f"indexes[{position}]") for position, item in enumerate(raw_indexes)]
    keys = [index.key for index in indexes]
    if len(keys) != len(set(keys)):
        raise ValueError("Firestore index manifest contains duplicate definitions")
    return indexes


def _collection_group_from_name(name: str) -> str:
    parts = name.split("/")
    try:
        position = parts.index("collectionGroups")
        return parts[position + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Unexpected Firestore index resource name: {name!r}") from exc


def normalize_remote_index(payload: dict[str, Any]) -> tuple[IndexSpec, str]:
    fields = [field for field in payload.get("fields", []) if isinstance(field, dict) and field.get("fieldPath") != "__name__"]
    spec = _parse_index(
        {
            "collectionGroup": _collection_group_from_name(str(payload.get("name") or "")),
            "queryScope": payload.get("queryScope"),
            "fields": fields,
        },
        source=str(payload.get("name") or "remote index"),
    )
    return spec, str(payload.get("state") or "STATE_UNSPECIFIED").upper()


def normalize_remote_indexes(
    payload: Sequence[dict[str, Any]],
) -> tuple[dict[tuple[str, str, tuple[tuple[str, str], ...]], str], int]:
    inventory: dict[tuple[str, str, tuple[tuple[str, str], ...]], str] = {}
    opaque_count = 0
    for item in payload:
        try:
            spec, state = normalize_remote_index(item)
        except ValueError:
            opaque_count += 1
            continue
        inventory[spec.key] = state
    return inventory, opaque_count


def list_remote_indexes(*, project_id: str, database: str) -> tuple[dict[tuple[str, str, tuple[tuple[str, str], ...]], str], int]:
    command = [
        "gcloud",
        "firestore",
        "indexes",
        "composite",
        "list",
        f"--project={project_id}",
        f"--database={database}",
        "--format=json",
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout or "[]")
    if not isinstance(payload, list):
        raise ValueError("gcloud returned an unexpected Firestore index list")
    return normalize_remote_indexes(payload)


def assert_release_source(repo_root: Path = REPO_ROOT) -> None:
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if branch != "main":
        raise RuntimeError(f"Index apply requires main; current branch is {branch or '<detached>'}")

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise RuntimeError("Index apply requires a clean worktree")

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True).stdout.strip()
    remote_main_output = subprocess.run(
        ["git", "ls-remote", "--exit-code", "origin", "refs/heads/main"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    remote_main = remote_main_output.split()[0] if remote_main_output else ""
    if not remote_main or head != remote_main:
        raise RuntimeError("Index apply requires HEAD to match the current remote main")


def _format_index(index: IndexSpec) -> str:
    fields = ", ".join(f"{path} {order}" for path, order in index.fields)
    return f"{index.collection_group} [{index.query_scope}]: {fields}"


def deploy_missing_indexes(
    indexes: Sequence[IndexSpec],
    *,
    project_id: str,
    database: str,
) -> None:
    for index in indexes:
        print(f"Creating {_format_index(index)}", flush=True)
        subprocess.run(index.create_command(project_id=project_id, database=database), check=True)


def wait_until_ready(
    expected: Sequence[IndexSpec],
    *,
    project_id: str,
    database: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    expected_by_key = {index.key: index for index in expected}

    while True:
        remote, _opaque_count = list_remote_indexes(project_id=project_id, database=database)
        pending: list[str] = []
        failed: list[str] = []
        for key, index in expected_by_key.items():
            state = remote.get(key)
            if state == "READY":
                continue
            description = f"{_format_index(index)} ({state or 'MISSING'})"
            if state in {"ERROR", "FAILED", "NEEDS_REPAIR"}:
                failed.append(description)
            else:
                pending.append(description)

        if failed:
            raise RuntimeError("Firestore index build failed: " + "; ".join(failed))
        if not pending:
            print(f"All {len(expected)} versioned Firestore indexes are READY.", flush=True)
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for Firestore indexes: " + "; ".join(pending))

        print("Waiting for Firestore indexes: " + "; ".join(pending), flush=True)
        time.sleep(poll_seconds)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Google Cloud project ID")
    parser.add_argument("--database", default="(default)", help="Firestore database ID")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--apply", action="store_true", help="Create missing indexes; default is read-only")
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--poll-seconds", type=int, default=15)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.timeout_seconds <= 0 or args.poll_seconds <= 0:
        raise ValueError("timeout and poll intervals must be positive")

    indexes = load_manifest(args.manifest)
    print(f"Validated {len(indexes)} versioned Firestore indexes in {args.manifest}.")
    remote, opaque_count = list_remote_indexes(project_id=args.project, database=args.database)
    expected_keys = {index.key for index in indexes}
    unexpected_count = len(set(remote) - expected_keys) + opaque_count
    missing = [index for index in indexes if index.key not in remote]
    not_ready = [index for index in indexes if remote.get(index.key) != "READY"]

    print(f"Remote inventory: {len(remote)} composite indexes; {len(missing)} missing; {len(not_ready)} not ready; {unexpected_count} unmanaged.")
    if unexpected_count:
        print("Unmanaged remote indexes are preserved; this helper never deletes indexes.")

    if not args.apply:
        for index in missing:
            print(f"Would create {_format_index(index)}")
        print("Read-only plan complete. Re-run with --apply from clean, synchronized main.")
        return 0

    assert_release_source()
    deploy_missing_indexes(missing, project_id=args.project, database=args.database)
    wait_until_ready(
        indexes,
        project_id=args.project,
        database=args.database,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, TimeoutError, ValueError, RuntimeError) as exc:
        print(f"Firestore index deployment failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
