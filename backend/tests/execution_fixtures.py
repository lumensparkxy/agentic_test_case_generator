from datetime import datetime, timezone

from app.contracts.projects import QaProjectDetail


def case():
    return {
        "id": "TC-1",
        "title": "Show checkout",
        "steps": [{"step": 1, "action": "Open /checkout", "expected": 'The URL includes "/checkout"'}],
        "automation_status": "Automated",
    }


def project_fixture(cases=None, owner="qa-user", snapshot_id="tests-1"):
    now = datetime.now(timezone.utc)
    return QaProjectDetail(
        project_id="project-1",
        name="Execution QA",
        owner_user_id=owner,
        current_revision=7,
        created_at=now,
        updated_at=now,
        stage_state={
            "requirements": {"current_snapshot_id": "req-1", "approved": True},
            "use_cases": {
                "current_snapshot_id": "use-1",
                "approved": True,
                "metadata": {"latest_human_review": {"snapshot_id": "use-1", "decision": "approve"}},
            },
            "test_cases": {"current_snapshot_id": snapshot_id, "approved": True},
        },
        current_snapshots={
            "test_cases": {
                "snapshot_id": snapshot_id,
                "project_id": "project-1",
                "stage": "test_cases",
                "version": 1,
                "project_revision": 7,
                "operation": "testcases.generate",
                "approved": True,
                "created_at": now,
                "payload": {"test_cases": cases or [case()], "generation_tasks": []},
                "metadata": {"source_requirements_snapshot_id": "req-1", "source_use_case_snapshot_id": "use-1"},
            }
        },
    )
