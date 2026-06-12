from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import AuthUser, AzureDevOpsSyncApplyInput, AzureDevOpsSyncPreviewInput, AzureDevOpsWorkItemSummary, Requirement
from app.services.azure_devops_sync_service import apply_azure_devops_requirement_sync, preview_azure_devops_requirement_sync


class AzureDevOpsSyncServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = AuthUser(sub="firebase-user-1", email="user@example.com", name="User")
        self.baseline = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
        self.requirement = Requirement(
            id="REQ-001",
            text="The system shall support login",
            source_system="azure_devops",
            source_issue_key="101",
            source_issue_type="User Story",
            source_issue_url="https://dev.azure.com/acme/Payments/_workitems/edit/101",
            source_issue_updated_at=self.baseline,
            sync_target_issue_key="101",
            artifact_item_id="req-item-1",
        )

    def test_preview_returns_ready_plan_with_managed_html_excerpt(self) -> None:
        live_item = AzureDevOpsWorkItemSummary(
            work_item_id=101,
            title="Login support",
            work_item_type="User Story",
            project="Payments",
            changed_at=self.baseline,
            rev=7,
            web_url="https://dev.azure.com/acme/Payments/_workitems/edit/101",
            description_text="Existing description",
            fields={"System.Description": "<p>Existing description</p>"},
        )
        fake_adapter = type("Adapter", (), {})()
        fake_adapter.default_project = None
        fake_adapter.get_work_item = lambda project, work_item_id: live_item

        with patch("app.services.azure_devops_sync_service.get_firestore_client") as get_firestore_client:
            with patch("app.services.azure_devops_sync_service.get_azure_devops_adapter_for_user", return_value=fake_adapter):
                response = preview_azure_devops_requirement_sync(
                    current_user=self.user,
                    payload=AzureDevOpsSyncPreviewInput(requirements=[self.requirement]),
                )

        get_firestore_client.assert_not_called()
        self.assertEqual(response.ready_work_item_count, 1)
        self.assertEqual(response.conflict_count, 0)
        self.assertEqual(response.work_items[0].project, "Payments")
        self.assertIn("REQ-001", response.work_items[0].rendered_description_excerpt)

    def test_preview_blocks_when_live_item_changed_after_import_baseline(self) -> None:
        live_item = AzureDevOpsWorkItemSummary(
            work_item_id=101,
            title="Login support",
            work_item_type="User Story",
            project="Payments",
            changed_at=datetime(2026, 5, 8, 11, 0, tzinfo=timezone.utc),
            rev=8,
            web_url="https://dev.azure.com/acme/Payments/_workitems/edit/101",
            description_text="Updated elsewhere",
            fields={"System.Description": "<p>Updated elsewhere</p>"},
        )
        fake_adapter = type("Adapter", (), {})()
        fake_adapter.default_project = None
        fake_adapter.get_work_item = lambda project, work_item_id: live_item

        with patch("app.services.azure_devops_sync_service.get_firestore_client") as get_firestore_client:
            with patch("app.services.azure_devops_sync_service.get_azure_devops_adapter_for_user", return_value=fake_adapter):
                response = preview_azure_devops_requirement_sync(
                    current_user=self.user,
                    payload=AzureDevOpsSyncPreviewInput(requirements=[self.requirement]),
                )

        get_firestore_client.assert_not_called()
        self.assertEqual(response.ready_work_item_count, 0)
        self.assertEqual(response.conflict_count, 1)
        self.assertIn("after the last imported baseline", response.work_items[0].conflict_reason)

    def test_apply_updates_description_and_returns_refreshed_requirements(self) -> None:
        live_item = AzureDevOpsWorkItemSummary(
            work_item_id=101,
            title="Login support",
            work_item_type="User Story",
            project="Payments",
            changed_at=self.baseline,
            rev=7,
            web_url="https://dev.azure.com/acme/Payments/_workitems/edit/101",
            description_text="Existing description",
            fields={"System.Description": "<p>Existing description</p>"},
        )
        refreshed = live_item.model_copy(update={"changed_at": datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc), "rev": 8})
        calls = {}

        def fake_update_work_item_description(project, work_item_id, html_description, rev=None, history_note=None):
            calls["project"] = project
            calls["work_item_id"] = work_item_id
            calls["html_description"] = html_description
            calls["rev"] = rev
            calls["history_note"] = history_note

        fake_adapter = type("Adapter", (), {})()
        fake_adapter.default_project = None
        fake_adapter.get_work_item = lambda project, work_item_id: live_item if "html_description" not in calls else refreshed
        fake_adapter.update_work_item_description = fake_update_work_item_description

        with patch("app.services.azure_devops_sync_service.get_firestore_client") as get_firestore_client:
            with patch("app.services.azure_devops_sync_service.get_azure_devops_adapter_for_user", return_value=fake_adapter):
                response = apply_azure_devops_requirement_sync(
                    current_user=self.user,
                    payload=AzureDevOpsSyncApplyInput(requirements=[self.requirement]),
                )

        get_firestore_client.assert_not_called()
        self.assertEqual(response.updated_work_item_count, 1)
        self.assertEqual(calls["project"], "Payments")
        self.assertEqual(calls["work_item_id"], 101)
        self.assertEqual(calls["rev"], 7)
        self.assertIn("AGENTIC_REQUIREMENTS_START", calls["html_description"])
        self.assertEqual(response.requirements[0].source_issue_updated_at, refreshed.changed_at)


if __name__ == "__main__":
    unittest.main()
