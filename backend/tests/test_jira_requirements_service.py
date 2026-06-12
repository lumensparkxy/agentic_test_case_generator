from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import JiraSettings
from app.models import AuthUser, JiraImportInput, JiraIssueSummary, JiraIssueTypeSummary, Requirement
from app.services.jira_requirements_service import (
    import_requirements_from_jira,
    list_jira_project_issue_types,
    persist_jira_requirement_mappings,
    search_jira_issues,
)


class FakeSnapshot:
    def __init__(self, payload):
        self._payload = payload

    @property
    def exists(self):
        return self._payload is not None

    def to_dict(self):
        return dict(self._payload or {})


class FakeDocument:
    def __init__(self, store, doc_id):
        self.store = store
        self.doc_id = doc_id

    def set(self, payload, merge=False):
        existing = dict(self.store.get(self.doc_id, {})) if merge else {}
        existing.update(payload)
        self.store[self.doc_id] = existing

    def get(self):
        return FakeSnapshot(self.store.get(self.doc_id))


class FakeCollection:
    def __init__(self, store):
        self.store = store

    def document(self, doc_id):
        return FakeDocument(self.store, doc_id)


class FakeFirestoreClient:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return FakeCollection(self.collections.setdefault(name, {}))


class JiraRequirementsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = AuthUser(sub="firebase-user-1", email="user@example.com", name="User")
        self.settings = JiraSettings(
            connection_secret_key="jira-secret-key",
            api_timeout_seconds=15,
            project_page_size=50,
            issue_page_size=50,
        )

    def test_import_requirements_from_jira_keeps_issue_traceability_and_renumbers_globally(self) -> None:
        epic = JiraIssueSummary(
            issue_id="10000",
            key="EPIC-1",
            summary="Login epic",
            issue_type="Epic",
            description_text="The system shall support login.",
            web_url="https://acme.atlassian.net/browse/EPIC-1",
        )
        child = JiraIssueSummary(
            issue_id="10001",
            key="STORY-1",
            summary="Password reset story",
            issue_type="Story",
            parent_key="EPIC-1",
            description_text="The system shall support password reset.",
            web_url="https://acme.atlassian.net/browse/STORY-1",
        )
        payload = JiraImportInput(epic_key="EPIC-1", include_children=True)
        workflow_responses = [
            {
                "requirements": [Requirement(id="REQ-001", text="The system shall support login")],
                "approved": True,
                "review": {"approved": True, "score": 96, "threshold": 80, "summary": "ok", "blocking_issues": [], "suggestions": [], "unmet_criteria": []},
                "iteration_history": [],
                "coverage_metrics": {},
                "workflow_settings": {},
                "workflow_diagnostics": {"status": "completed", "attempt_count": 1},
            },
            {
                "requirements": [Requirement(id="REQ-001", text="The system shall support password reset")],
                "approved": True,
                "review": {"approved": True, "score": 92, "threshold": 80, "summary": "ok", "blocking_issues": [], "suggestions": [], "unmet_criteria": []},
                "iteration_history": [],
                "coverage_metrics": {},
                "workflow_settings": {},
                "workflow_diagnostics": {"status": "completed", "attempt_count": 1},
            },
        ]

        fake_adapter = type("Adapter", (), {})()
        fake_adapter.get_issue = lambda key: epic
        fake_adapter.get_epic_with_children = lambda key, page_size=50: [epic, child]

        with patch("app.services.jira_requirements_service.get_jira_settings", return_value=self.settings):
            with patch("app.services.jira_requirements_service.get_jira_adapter_for_user", return_value=fake_adapter):
                with patch("app.services.jira_requirements_service.extract_requirements", side_effect=workflow_responses):
                    result = import_requirements_from_jira(current_user=self.user, payload=payload)

        requirements = result["requirements"]
        self.assertEqual([requirement.id for requirement in requirements], ["REQ-001", "REQ-002"])
        self.assertEqual([requirement.source_issue_key for requirement in requirements], ["EPIC-1", "STORY-1"])
        self.assertTrue(all(requirement.source_system == "jira" for requirement in requirements))
        self.assertEqual(requirements[1].source_path, "EPIC-1 > STORY-1 · Story: Password reset story")
        self.assertEqual(requirements[1].source_hierarchy, ["EPIC-1", "STORY-1 · Story: Password reset story"])
        self.assertIn("Password reset story", requirements[1].source_excerpt)
        self.assertEqual(result["source_issue_keys"], ["EPIC-1", "STORY-1"])
        self.assertEqual(result["coverage_metrics"]["source_issue_count"], 2)

    def test_list_jira_project_issue_types_returns_project_issue_types(self) -> None:
        fake_adapter = type("Adapter", (), {})()
        fake_adapter.get_project_issue_types = lambda project_key: [
            JiraIssueTypeSummary(issue_type_id="10000", name="Epic", hierarchy_level=1),
            JiraIssueTypeSummary(issue_type_id="10001", name="Bug", hierarchy_level=0),
        ]

        with patch("app.services.jira_requirements_service.get_jira_adapter_for_user", return_value=fake_adapter):
            response = list_jira_project_issue_types(current_user=self.user, project_key="THEONE")

        self.assertEqual(response.project_key, "THEONE")
        self.assertEqual([issue_type.name for issue_type in response.issue_types], ["Epic", "Bug"])

    def test_search_jira_issues_appends_order_by_without_extra_and(self) -> None:
        captured = {}
        fake_issue = JiraIssueSummary(
            issue_id="10000",
            key="THEONE-1",
            summary="MRV support",
            issue_type="Task",
        )

        def fake_search_issue_summaries(jql, max_results):
            captured["jql"] = jql
            captured["max_results"] = max_results
            return 1, [fake_issue]

        fake_adapter = type("Adapter", (), {})()
        fake_adapter.search_issue_summaries = fake_search_issue_summaries

        with patch("app.services.jira_requirements_service.get_jira_settings", return_value=self.settings):
            with patch("app.services.jira_requirements_service.get_jira_adapter_for_user", return_value=fake_adapter):
                response = search_jira_issues(
                    current_user=self.user,
                    project_key="TX",
                    query="mrv",
                    issue_type="Any issue type",
                    max_results=20,
                )

        self.assertEqual(response.total, 1)
        self.assertEqual([issue.key for issue in response.issues], ["THEONE-1"])
        self.assertEqual(captured["max_results"], 20)
        self.assertEqual(captured["jql"], 'project = "TX" AND summary ~ "mrv" ORDER BY updated DESC')
        self.assertNotIn("AND ORDER BY", captured["jql"])

    def test_search_jira_issues_without_query_or_type_keeps_valid_order_by(self) -> None:
        captured = {}

        def fake_search_issue_summaries(jql, max_results):
            captured["jql"] = jql
            captured["max_results"] = max_results
            return 0, []

        fake_adapter = type("Adapter", (), {})()
        fake_adapter.search_issue_summaries = fake_search_issue_summaries

        with patch("app.services.jira_requirements_service.get_jira_settings", return_value=self.settings):
            with patch("app.services.jira_requirements_service.get_jira_adapter_for_user", return_value=fake_adapter):
                response = search_jira_issues(
                    current_user=self.user,
                    project_key="TX",
                    query=None,
                    issue_type=None,
                    max_results=20,
                )

        self.assertEqual(response.total, 0)
        self.assertEqual(response.issues, [])
        self.assertEqual(captured["max_results"], 20)
        self.assertEqual(captured["jql"], 'project = "TX" ORDER BY updated DESC')
        self.assertNotIn("AND ORDER BY", captured["jql"])

    def test_persist_jira_requirement_mappings_writes_mapping_documents(self) -> None:
        client = FakeFirestoreClient()
        requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall support login",
                source_system="jira",
                source_issue_key="EPIC-1",
                source_issue_type="Epic",
                source_issue_url="https://acme.atlassian.net/browse/EPIC-1",
                sync_target_issue_key="EPIC-1",
                artifact_set_id="req-set-1",
                artifact_item_id="req-item-1",
                artifact_version_id="req-ver-1",
                artifact_version_number=1,
            )
        ]

        with patch("app.services.firestore_repository.get_firestore_client", return_value=client):
            persisted = persist_jira_requirement_mappings(
                requirements=requirements,
                actor=self.user,
                request_id="req-123",
                workflow_run_id="run-123",
                source_event_id="event-123",
            )

        self.assertEqual(persisted[0].artifact_item_id, "req-item-1")
        mapping = client.collections["jira_requirement_mappings"]["req-item-1"]
        self.assertEqual(mapping["jira_issue_key"], "EPIC-1")
        self.assertEqual(mapping["sync_target_issue_key"], "EPIC-1")
        self.assertEqual(mapping["actor_user_id"], self.user.sub)
        self.assertTrue(mapping["content_hash"])


if __name__ == "__main__":
    unittest.main()
