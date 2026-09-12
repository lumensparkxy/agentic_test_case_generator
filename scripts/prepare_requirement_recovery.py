#!/usr/bin/env python3
"""Inspect or prepare an owner-scoped recovery preview; never applies a baseline.

This administrative tool uses the operator's existing Firestore credentials.
Without --prepare it performs only reads and reports snapshot counts.
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.models import AuthUser
from app.contracts.requirement_imports import ImportRecoveryInput
from app.services.requirement_import_service import repository, prepare_recovery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--baseline-snapshot-id", required=True)
    parser.add_argument("--incoming-snapshot-id", required=True)
    parser.add_argument("--expected-revision", type=int, required=True)
    parser.add_argument("--prepare", action="store_true", help="Save a pending comparison, using semantic suggestions. Does not apply it.")
    args = parser.parse_args()
    repo = repository()
    project = repo.project_doc(args.project_id).get().to_dict()
    if not project:
        parser.error("Project not found")
    actor = AuthUser(sub=project["owner_user_id"], name="Project owner")
    repo.baseline(args.project_id, actor, args.expected_revision)
    payload = ImportRecoveryInput(
        base_project_revision=args.expected_revision, baseline_snapshot_id=args.baseline_snapshot_id, incoming_snapshot_id=args.incoming_snapshot_id
    )
    if args.prepare:
        # Reusing a still-pending recovery avoids duplicates after an uncertain CLI response.
        existing = [
            p
            for p in repo.list_pending(args.project_id, actor)
            if p.base_project_revision == args.expected_revision and p.recovery_snapshot_ids == [args.baseline_snapshot_id, args.incoming_snapshot_id]
        ]
        preview = existing[0] if existing else prepare_recovery(args.project_id, actor, payload)
        print(
            json.dumps(
                {
                    "import_id": preview.import_id,
                    "project_id": preview.project_id,
                    "base_project_revision": preview.base_project_revision,
                    "status": preview.status,
                    "counts": preview.counts,
                    "recovery_snapshot_ids": preview.recovery_snapshot_ids,
                },
                indent=2,
            )
        )
    else:
        rows = []
        for sid in (args.baseline_snapshot_id, args.incoming_snapshot_id):
            snapshot = repo.project_doc(args.project_id).collection("snapshots").document(sid).get().to_dict()
            if not snapshot or snapshot.get("stage") != "requirements":
                parser.error("Requirements snapshot not found in the selected project")
            rows.append({"snapshot_id": sid, "project_revision": snapshot["project_revision"], "requirements": len(snapshot["payload"]["requirements"])})
        print(
            json.dumps(
                {"project_revision": project["current_revision"], "snapshots": rows, "action": "read-only; add --prepare to stage a recovery comparison"},
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
