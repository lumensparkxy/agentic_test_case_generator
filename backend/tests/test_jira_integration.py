from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app, get_current_user
from app.models import AuthUser, Requirement


class JiraIntegrationEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: AuthUser(
            sub="firebase-uid-999",
            email="tester@example.com",
            name="Test User",
            provider="google.com",
        )

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_jira_import_logs_usage_and_persists_requirement_mappings(self) -> None:
        workflow_result = {
            "source_name": "EPIC-1",
            "source_names": ["EPIC-1", "STORY-1"],
            "source_issue_keys": ["EPIC-1", "STORY-1"],
            "issue_count": 2,
            "raw_text": "--- SOURCE: EPIC-1 ---",
            "requirements": [
                Requirement(
                    id="REQ-001",
                    text="The system shall support login",
                    source_system="jira",
                    source_issue_key="EPIC-1",
                    source_issue_type="Epic",
                    sync_target_issue_key="EPIC-1",
                )
            ],
            "approved": True,
            "review": {"approved": True, "score": 95, "threshold": 80, "summary": "ok", "blocking_issues": [], "suggestions": [], "unmet_criteria": []},
            "iteration_history": [],
            "coverage_metrics": {"source_issue_count": 2},
            "workflow_settings": {},
            "workflow_diagnostics": {"status": "completed"},
        }
        persisted_requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall support login",
                source_system="jira",
                source_issue_key="EPIC-1",
                source_issue_type="Epic",
                sync_target_issue_key="EPIC-1",
                artifact_set_id="req-set-1",
                artifact_item_id="req-item-1",
                artifact_version_id="req-ver-1",
                artifact_version_number=1,
            )
        ]

        with patch("app.main.enforce_billing_access", return_value=object()):
            with patch("app.main.start_workflow_run", return_value="run-jira-import-1") as start_run:
                with patch("app.main.complete_workflow_run") as complete_run:
                    with patch("app.main.record_usage_event", return_value="event-jira-import-1") as record_event:
                        with patch("app.main._record_billing_consumption_safe") as record_billing:
                            with patch("app.main.persist_requirement_versions", return_value=persisted_requirements) as persist_versions:
                                with patch("app.main.persist_jira_requirement_mappings", return_value=persisted_requirements) as persist_mappings:
                                    with patch("app.main.import_requirements_from_jira", return_value=workflow_result) as import_service:
                                        with TestClient(app) as client:
                                            response = client.post(
                                                "/integrations/jira/import",
                                                json={"epic_key": "EPIC-1", "include_children": True},
                                            )

        self.assertEqual(response.status_code, 200)
        import_service.assert_called_once()
        self.assertEqual(import_service.call_args.kwargs["current_user"].sub, "firebase-uid-999")
        start_run.assert_called_once()
        complete_run.assert_called_once()
        record_event.assert_called_once()
        record_billing.assert_called_once()
        persist_versions.assert_called_once()
        persist_mappings.assert_called_once()
        self.assertEqual(response.json()["requirements"][0]["artifact_item_id"], "req-item-1")

    def test_jira_connection_upsert_returns_saved_summary(self) -> None:
        service_response = {
            "connected": True,
            "connection": {
                "base_url": "https://acme.atlassian.net",
                "email": "qa@acme.com",
                "display_name": "QA User",
                "account_id": "acct-1",
                "api_token_hint": "••••1234",
                "connected_at": "2026-04-22T00:00:00Z",
                "updated_at": "2026-04-22T00:00:00Z",
                "last_validated_at": "2026-04-22T00:00:00Z",
            },
        }

        with patch("app.main.start_workflow_run", return_value="run-jira-connection-1"):
            with patch("app.main.complete_workflow_run"):
                with patch("app.main.upsert_jira_connection", return_value=service_response):
                    with TestClient(app) as client:
                        response = client.post(
                            "/integrations/jira/connection",
                            json={
                                "base_url": "https://acme.atlassian.net",
                                "email": "qa@acme.com",
                                "api_token": "jira-token-1234",
                            },
                        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["connection"]["display_name"], "QA User")
        self.assertEqual(response.json()["connection"]["api_token_hint"], "••••1234")

    def test_jira_project_issue_types_returns_project_issue_types(self) -> None:
        service_response = {
            "project_key": "THEONE",
            "issue_types": [
                {
                    "issue_type_id": "10000",
                    "name": "Epic",
                    "description": "Epic work",
                    "hierarchy_level": 1,
                    "subtask": False,
                    "scope_type": None,
                },
                {
                    "issue_type_id": "10001",
                    "name": "Bug",
                    "description": "Bug work",
                    "hierarchy_level": 0,
                    "subtask": False,
                    "scope_type": None,
                },
            ],
        }

        with patch("app.main.list_jira_project_issue_types", return_value=service_response):
            with TestClient(app) as client:
                response = client.get("/integrations/jira/projects/THEONE/issue-types")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_key"], "THEONE")
        self.assertEqual([issue_type["name"] for issue_type in response.json()["issue_types"]], ["Epic", "Bug"])

    def test_jira_sync_preview_returns_preview_summary(self) -> None:
        preview_response = {
            "issues": [
                {
                    "issue_key": "EPIC-1",
                    "issue_type": "Epic",
                    "issue_url": "https://acme.atlassian.net/browse/EPIC-1",
                    "status": "ready",
                    "requirement_ids": ["REQ-001"],
                    "target_field": "description_managed_block",
                    "live_issue_updated_at": "2026-04-22T02:00:00Z",
                    "mapped_issue_updated_at": "2026-04-22T00:00:00Z",
                    "existing_description_excerpt": "Existing description",
                    "rendered_description_excerpt": "Rendered description",
                    "conflict_reason": None,
                    "warning": None,
                }
            ],
            "ready_issue_count": 1,
            "conflict_count": 0,
            "skipped_requirement_ids": [],
            "warnings": [],
        }

        with patch("app.main.start_workflow_run", return_value="run-jira-preview-1") as start_run:
            with patch("app.main.complete_workflow_run") as complete_run:
                with patch("app.main.record_usage_event", return_value="event-jira-preview-1") as record_event:
                    with patch("app.main.preview_jira_requirement_sync", return_value=preview_response) as preview_service:
                        with TestClient(app) as client:
                            response = client.post(
                                "/integrations/jira/sync/preview",
                                json={
                                    "requirements": [
                                        {
                                            "id": "REQ-001",
                                            "text": "The system shall support login",
                                            "source_system": "jira",
                                            "source_issue_key": "EPIC-1",
                                            "sync_target_issue_key": "EPIC-1",
                                        }
                                    ]
                                },
                            )

        self.assertEqual(response.status_code, 200)
        preview_service.assert_called_once()
        start_run.assert_called_once()
        complete_run.assert_called_once()
        record_event.assert_called_once()
        self.assertEqual(response.json()["ready_issue_count"], 1)

    def test_jira_sync_apply_persists_updated_requirement_mappings(self) -> None:
        applied_response = {
            "results": [
                {
                    "issue_key": "EPIC-1",
                    "status": "updated",
                    "requirement_ids": ["REQ-001"],
                    "issue_url": "https://acme.atlassian.net/browse/EPIC-1",
                    "updated_at": "2026-04-22T02:00:00Z",
                    "message": "Updated managed requirements block in EPIC-1.",
                }
            ],
            "updated_issue_count": 1,
            "skipped_issue_count": 0,
            "conflict_count": 0,
            "warnings": [],
            "requirements": [
                {
                    "id": "REQ-001",
                    "text": "The system shall support login",
                    "source_system": "jira",
                    "source_issue_key": "EPIC-1",
                    "source_issue_type": "Epic",
                    "sync_target_issue_key": "EPIC-1",
                    "source_issue_updated_at": "2026-04-22T02:00:00Z",
                    "artifact_set_id": "req-set-1",
                    "artifact_item_id": "req-item-1",
                    "artifact_version_id": "req-ver-1",
                    "artifact_version_number": 1,
                }
            ],
        }

        with patch("app.main.start_workflow_run", return_value="run-jira-sync-1") as start_run:
            with patch("app.main.complete_workflow_run") as complete_run:
                with patch("app.main.record_usage_event", return_value="event-jira-sync-1") as record_event:
                    with patch("app.main.persist_jira_requirement_mappings", return_value=[Requirement(
                        id="REQ-001",
                        text="The system shall support login",
                        source_system="jira",
                        source_issue_key="EPIC-1",
                        source_issue_type="Epic",
                        sync_target_issue_key="EPIC-1",
                        artifact_set_id="req-set-1",
                        artifact_item_id="req-item-1",
                        artifact_version_id="req-ver-1",
                        artifact_version_number=1,
                    )]) as persist_mappings:
                        with patch("app.main.apply_jira_requirement_sync", return_value=applied_response) as apply_service:
                            with TestClient(app) as client:
                                response = client.post(
                                    "/integrations/jira/sync",
                                    json={
                                        "requirements": [
                                            {
                                                "id": "REQ-001",
                                                "text": "The system shall support login",
                                                "source_system": "jira",
                                                "source_issue_key": "EPIC-1",
                                                "sync_target_issue_key": "EPIC-1",
                                                "artifact_set_id": "req-set-1",
                                                "artifact_item_id": "req-item-1",
                                                "artifact_version_id": "req-ver-1",
                                                "artifact_version_number": 1,
                                            }
                                        ]
                                    },
                                )

        self.assertEqual(response.status_code, 200)
        apply_service.assert_called_once()
        start_run.assert_called_once()
        complete_run.assert_called_once()
        record_event.assert_called_once()
        persist_mappings.assert_called_once()
        self.assertEqual(response.json()["updated_issue_count"], 1)


if __name__ == "__main__":
    unittest.main()
