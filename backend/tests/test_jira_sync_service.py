from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import JiraIssueSummary, JiraSyncApplyInput, JiraSyncPreviewInput, Requirement
from app.services.jira_sync_service import apply_jira_requirement_sync, preview_jira_requirement_sync
from app.models import AuthUser


class FakeJiraAdapter:
    def __init__(self, issue_responses):
        self.issue_responses = list(issue_responses)
        self.updated_payloads = []

    def get_issue(self, issue_key):
        if not self.issue_responses:
            raise AssertionError(f"No fake JIRA issue response queued for {issue_key}")
        return self.issue_responses.pop(0)

    def update_issue_description(self, issue_key, description_adf):
        self.updated_payloads.append((issue_key, description_adf))


class JiraSyncServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = AuthUser(sub="firebase-user-1", email="user@example.com", name="User")

    def test_preview_detects_conflict_when_jira_issue_changed_after_import(self) -> None:
        requirement = Requirement(
            id="REQ-001",
            text="The system shall support login",
            source_system="jira",
            source_issue_key="EPIC-1",
            source_issue_type="Epic",
            source_issue_updated_at="2026-04-22T00:00:00Z",
            sync_target_issue_key="EPIC-1",
        )
        live_issue = JiraIssueSummary(
            issue_id="10000",
            key="EPIC-1",
            summary="Login epic",
            issue_type="Epic",
            updated_at="2026-04-22T01:00:00Z",
            web_url="https://acme.atlassian.net/browse/EPIC-1",
            description_adf={
                "version": 1,
                "type": "doc",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Existing description"}]}],
            },
            description_text="Existing description",
        )
        adapter = FakeJiraAdapter([live_issue])

        with patch("app.services.jira_sync_service.get_firestore_client") as get_firestore_client:
            with patch("app.services.jira_sync_service.get_jira_adapter_for_user", return_value=adapter):
                response = preview_jira_requirement_sync(
                    current_user=self.user,
                    payload=JiraSyncPreviewInput(requirements=[requirement]),
                )

        get_firestore_client.assert_not_called()
        self.assertEqual(response.conflict_count, 1)
        self.assertEqual(response.issues[0].status, "conflict")
        self.assertIn("REQ-001", response.issues[0].rendered_description_excerpt)
        self.assertIn("updated in JIRA", response.issues[0].conflict_reason)

    def test_apply_updates_managed_block_and_refreshes_requirement_baseline(self) -> None:
        requirement = Requirement(
            id="REQ-001",
            text="The system shall support login",
            source_system="jira",
            source_issue_key="EPIC-1",
            source_issue_type="Epic",
            source_issue_updated_at="2026-04-22T00:00:00Z",
            sync_target_issue_key="EPIC-1",
            artifact_item_id="req-item-1",
        )
        before_update = JiraIssueSummary(
            issue_id="10000",
            key="EPIC-1",
            summary="Login epic",
            issue_type="Epic",
            updated_at="2026-04-22T00:00:00Z",
            web_url="https://acme.atlassian.net/browse/EPIC-1",
            description_adf={
                "version": 1,
                "type": "doc",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Existing description"}]}],
            },
            description_text="Existing description",
        )
        after_update = JiraIssueSummary(
            issue_id="10000",
            key="EPIC-1",
            summary="Login epic",
            issue_type="Epic",
            updated_at="2026-04-22T02:00:00Z",
            web_url="https://acme.atlassian.net/browse/EPIC-1",
            description_adf={
                "version": 1,
                "type": "doc",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Updated description"}]}],
            },
            description_text="Updated description",
        )
        adapter = FakeJiraAdapter([before_update, after_update])

        with patch("app.services.jira_sync_service.get_firestore_client") as get_firestore_client:
            with patch("app.services.jira_sync_service.get_jira_adapter_for_user", return_value=adapter):
                response = apply_jira_requirement_sync(
                    current_user=self.user,
                    payload=JiraSyncApplyInput(requirements=[requirement]),
                )

        get_firestore_client.assert_not_called()
        self.assertEqual(response.updated_issue_count, 1)
        self.assertEqual(response.results[0].status, "updated")
        self.assertEqual(len(adapter.updated_payloads), 1)
        issue_key, description_adf = adapter.updated_payloads[0]
        self.assertEqual(issue_key, "EPIC-1")
        rendered_text = str(description_adf)
        self.assertIn("AGENTIC_REQUIREMENTS_START", rendered_text)
        self.assertIn("REQ-001", rendered_text)
        self.assertEqual(response.requirements[0].source_issue_updated_at.isoformat(), "2026-04-22T02:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
