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


class AzureDevOpsIntegrationEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: AuthUser(
            sub="firebase-uid-999",
            email="tester@example.com",
            name="Test User",
            provider="microsoft.com",
        )

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_azure_devops_connection_upsert_returns_saved_summary(self) -> None:
        service_response = {
            "connected": True,
            "connection": {
                "organization_url": "https://dev.azure.com/acme",
                "organization": "acme",
                "default_project": "Payments",
                "auth_type": "pat",
                "display_name": "acme",
                "account_email": "qa@acme.com",
                "token_hint": "••••1234",
                "connected_at": "2026-05-08T00:00:00Z",
                "updated_at": "2026-05-08T00:00:00Z",
                "last_validated_at": "2026-05-08T00:00:00Z",
            },
        }

        with patch("app.routers.integrations_azure_devops.start_workflow_run", return_value="run-azure-connection-1"):
            with patch("app.routers.integrations_azure_devops.complete_workflow_run"):
                with patch("app.routers.integrations_azure_devops.upsert_azure_devops_connection", return_value=service_response):
                    with TestClient(app) as client:
                        response = client.post(
                            "/integrations/azure-devops/connection",
                            json={
                                "organization_url": "https://dev.azure.com/acme/Payments",
                                "personal_access_token": "azure-pat-1234",
                                "account_email": "qa@acme.com",
                            },
                        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["connection"]["organization"], "acme")
        self.assertEqual(response.json()["connection"]["default_project"], "Payments")
        self.assertEqual(response.json()["connection"]["token_hint"], "••••1234")

    def test_azure_devops_project_work_item_types_returns_types(self) -> None:
        service_response = {
            "project": "Payments",
            "work_item_types": [
                {"name": "Epic", "reference_name": "Microsoft.VSTS.WorkItemTypes.Epic"},
                {"name": "User Story", "reference_name": "Microsoft.VSTS.WorkItemTypes.UserStory"},
            ],
        }

        with patch("app.routers.integrations_azure_devops.list_azure_devops_project_work_item_types", return_value=service_response):
            with TestClient(app) as client:
                response = client.get("/integrations/azure-devops/projects/Payments/work-item-types")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project"], "Payments")
        self.assertEqual([item["name"] for item in response.json()["work_item_types"]], ["Epic", "User Story"])

    def test_azure_devops_work_item_search_returns_items(self) -> None:
        service_response = {
            "total": 1,
            "work_items": [
                {
                    "work_item_id": 101,
                    "title": "Login support",
                    "work_item_type": "User Story",
                    "state": "Active",
                    "project": "Payments",
                    "web_url": "https://dev.azure.com/acme/Payments/_workitems/edit/101",
                    "tags": ["auth"],
                }
            ],
        }

        with patch("app.routers.integrations_azure_devops.search_azure_devops_work_items", return_value=service_response):
            with TestClient(app) as client:
                response = client.get(
                    "/integrations/azure-devops/work-items/search",
                    params={"project": "Payments", "query": "login", "work_item_type": "User Story"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["work_items"][0]["work_item_id"], 101)

    def test_azure_devops_import_logs_usage_and_persists_requirement_mappings(self) -> None:
        workflow_result = {
            "source_name": "101",
            "source_names": ["101", "102"],
            "source_work_item_ids": ["101", "102"],
            "work_item_count": 2,
            "source_project": "Payments",
            "raw_text": "--- SOURCE: Azure DevOps #101 ---",
            "requirements": [
                Requirement(
                    id="REQ-001",
                    text="The system shall support login",
                    source_system="azure_devops",
                    source_issue_key="101",
                    source_issue_type="User Story",
                    sync_target_issue_key="101",
                )
            ],
            "approved": True,
            "review": {"approved": True, "score": 95, "threshold": 80, "summary": "ok", "blocking_issues": [], "suggestions": [], "unmet_criteria": []},
            "iteration_history": [],
            "coverage_metrics": {"source_work_item_count": 2},
            "workflow_settings": {},
            "workflow_diagnostics": {"status": "completed"},
        }
        persisted_requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall support login",
                source_system="azure_devops",
                source_issue_key="101",
                source_issue_type="User Story",
                sync_target_issue_key="101",
                artifact_set_id="req-set-1",
                artifact_item_id="req-item-1",
                artifact_version_id="req-ver-1",
                artifact_version_number=1,
            )
        ]

        with patch("app.routers.integrations_azure_devops.enforce_billing_access", return_value=object()):
            with patch("app.routers.integrations_azure_devops.start_workflow_run", return_value="run-azure-import-1") as start_run:
                with patch("app.routers.integrations_azure_devops.complete_workflow_run") as complete_run:
                    with patch("app.routers.integrations_azure_devops.record_usage_event", return_value="event-azure-import-1") as record_event:
                        with patch("app.routers.integrations_azure_devops._record_billing_consumption_safe") as record_billing:
                            with patch("app.routers.integrations_azure_devops.persist_requirement_versions", return_value=persisted_requirements) as persist_versions:
                                with patch("app.routers.integrations_azure_devops.persist_azure_devops_requirement_mappings", return_value=persisted_requirements) as persist_mappings:
                                    with patch("app.routers.integrations_azure_devops.import_requirements_from_azure_devops", return_value=workflow_result) as import_service:
                                        with TestClient(app) as client:
                                            response = client.post(
                                                "/integrations/azure-devops/import",
                                                json={"project": "Payments", "work_item_id": 101, "include_children": True},
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

    def test_azure_devops_sync_preview_returns_preview_summary(self) -> None:
        preview_response = {
            "work_items": [
                {
                    "work_item_id": 101,
                    "work_item_type": "User Story",
                    "work_item_url": "https://dev.azure.com/acme/Payments/_workitems/edit/101",
                    "project": "Payments",
                    "status": "ready",
                    "requirement_ids": ["REQ-001"],
                    "target_field": "system_description_managed_block",
                    "live_changed_at": "2026-05-08T02:00:00Z",
                    "mapped_changed_at": "2026-05-08T00:00:00Z",
                    "existing_description_excerpt": "Existing description",
                    "rendered_description_excerpt": "Rendered description",
                    "conflict_reason": None,
                    "warning": None,
                }
            ],
            "ready_work_item_count": 1,
            "conflict_count": 0,
            "skipped_requirement_ids": [],
            "warnings": [],
        }

        with patch("app.routers.integrations_azure_devops.start_workflow_run", return_value="run-azure-preview-1") as start_run:
            with patch("app.routers.integrations_azure_devops.complete_workflow_run") as complete_run:
                with patch("app.routers.integrations_azure_devops.record_usage_event", return_value="event-azure-preview-1") as record_event:
                    with patch("app.routers.integrations_azure_devops.preview_azure_devops_requirement_sync", return_value=preview_response) as preview_service:
                        with TestClient(app) as client:
                            response = client.post(
                                "/integrations/azure-devops/sync/preview",
                                json={
                                    "requirements": [
                                        {
                                            "id": "REQ-001",
                                            "text": "The system shall support login",
                                            "source_system": "azure_devops",
                                            "source_issue_key": "101",
                                            "source_issue_url": "https://dev.azure.com/acme/Payments/_workitems/edit/101",
                                            "sync_target_issue_key": "101",
                                        }
                                    ]
                                },
                            )

        self.assertEqual(response.status_code, 200)
        preview_service.assert_called_once()
        start_run.assert_called_once()
        complete_run.assert_called_once()
        record_event.assert_called_once()
        self.assertEqual(response.json()["ready_work_item_count"], 1)

    def test_azure_devops_sync_apply_persists_updated_requirement_mappings(self) -> None:
        applied_response = {
            "results": [
                {
                    "work_item_id": 101,
                    "status": "updated",
                    "requirement_ids": ["REQ-001"],
                    "work_item_url": "https://dev.azure.com/acme/Payments/_workitems/edit/101",
                    "updated_at": "2026-05-08T02:00:00Z",
                    "message": "Updated managed requirements block in Azure DevOps work item #101.",
                }
            ],
            "updated_work_item_count": 1,
            "skipped_work_item_count": 0,
            "conflict_count": 0,
            "warnings": [],
            "requirements": [
                {
                    "id": "REQ-001",
                    "text": "The system shall support login",
                    "source_system": "azure_devops",
                    "source_issue_key": "101",
                    "source_issue_type": "User Story",
                    "sync_target_issue_key": "101",
                    "source_issue_url": "https://dev.azure.com/acme/Payments/_workitems/edit/101",
                    "source_issue_updated_at": "2026-05-08T02:00:00Z",
                    "artifact_set_id": "req-set-1",
                    "artifact_item_id": "req-item-1",
                    "artifact_version_id": "req-ver-1",
                    "artifact_version_number": 1,
                }
            ],
        }

        with patch("app.routers.integrations_azure_devops.start_workflow_run", return_value="run-azure-sync-1") as start_run:
            with patch("app.routers.integrations_azure_devops.complete_workflow_run") as complete_run:
                with patch("app.routers.integrations_azure_devops.record_usage_event", return_value="event-azure-sync-1") as record_event:
                    with patch("app.routers.integrations_azure_devops.persist_azure_devops_requirement_mappings", return_value=[Requirement(
                        id="REQ-001",
                        text="The system shall support login",
                        source_system="azure_devops",
                        source_issue_key="101",
                        source_issue_type="User Story",
                        sync_target_issue_key="101",
                        artifact_set_id="req-set-1",
                        artifact_item_id="req-item-1",
                        artifact_version_id="req-ver-1",
                        artifact_version_number=1,
                    )]) as persist_mappings:
                        with patch("app.routers.integrations_azure_devops.apply_azure_devops_requirement_sync", return_value=applied_response) as apply_service:
                            with TestClient(app) as client:
                                response = client.post(
                                    "/integrations/azure-devops/sync",
                                    json={
                                        "requirements": [
                                            {
                                                "id": "REQ-001",
                                                "text": "The system shall support login",
                                                "source_system": "azure_devops",
                                                "source_issue_key": "101",
                                                "source_issue_url": "https://dev.azure.com/acme/Payments/_workitems/edit/101",
                                                "sync_target_issue_key": "101",
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
        self.assertEqual(response.json()["updated_work_item_count"], 1)


if __name__ == "__main__":
    unittest.main()