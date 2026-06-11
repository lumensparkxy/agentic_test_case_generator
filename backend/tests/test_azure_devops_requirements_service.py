from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import AzureDevOpsSettings
from app.models import AuthUser, AzureDevOpsImportInput, AzureDevOpsWorkItemSummary, AzureDevOpsWorkItemTypeSummary, Requirement
from app.services.azure_devops_requirements_service import (
    import_requirements_from_azure_devops,
    list_azure_devops_project_work_item_types,
    persist_azure_devops_requirement_mappings,
    search_azure_devops_work_items,
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


class AzureDevOpsRequirementsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = AuthUser(sub="firebase-user-1", email="user@example.com", name="User")
        self.settings = AzureDevOpsSettings(
            connection_secret_key="azure-secret-key",
            api_timeout_seconds=15,
            api_version="7.1",
            project_page_size=50,
            work_item_page_size=50,
        )

    def test_import_requirements_keeps_work_item_traceability_and_renumbers_globally(self) -> None:
        parent = AzureDevOpsWorkItemSummary(
            work_item_id=101,
            title="Login epic",
            work_item_type="Epic",
            state="Active",
            project="Payments",
            description_text="The system shall support login.",
            web_url="https://dev.azure.com/acme/Payments/_workitems/edit/101",
        )
        child = AzureDevOpsWorkItemSummary(
            work_item_id=102,
            title="Password reset story",
            work_item_type="User Story",
            state="Active",
            project="Payments",
            parent_id=101,
            description_text="The system shall support password reset.",
            web_url="https://dev.azure.com/acme/Payments/_workitems/edit/102",
        )
        payload = AzureDevOpsImportInput(project="Payments", work_item_id=101, include_children=True)
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
        fake_adapter.default_project = None
        fake_adapter.get_work_item_with_children = lambda project, work_item_id: [parent, child]

        with patch("app.services.azure_devops_requirements_service.get_azure_devops_settings", return_value=self.settings):
            with patch("app.services.azure_devops_requirements_service.get_azure_devops_adapter_for_user", return_value=fake_adapter):
                with patch("app.services.azure_devops_requirements_service.extract_requirements", side_effect=workflow_responses):
                    result = import_requirements_from_azure_devops(current_user=self.user, payload=payload)

        requirements = result["requirements"]
        self.assertEqual([requirement.id for requirement in requirements], ["REQ-001", "REQ-002"])
        self.assertEqual([requirement.source_issue_key for requirement in requirements], ["101", "102"])
        self.assertTrue(all(requirement.source_system == "azure_devops" for requirement in requirements))
        self.assertEqual(result["source_work_item_ids"], ["101", "102"])
        self.assertEqual(result["coverage_metrics"]["source_work_item_count"], 2)

    def test_list_work_item_types_returns_project_types(self) -> None:
        fake_adapter = type("Adapter", (), {})()
        fake_adapter.get_project_work_item_types = lambda project: [
            AzureDevOpsWorkItemTypeSummary(name="Epic", reference_name="Microsoft.VSTS.WorkItemTypes.Epic"),
            AzureDevOpsWorkItemTypeSummary(name="Bug", reference_name="Microsoft.VSTS.WorkItemTypes.Bug"),
        ]

        with patch("app.services.azure_devops_requirements_service.get_azure_devops_adapter_for_user", return_value=fake_adapter):
            response = list_azure_devops_project_work_item_types(current_user=self.user, project="Payments")

        self.assertEqual(response.project, "Payments")
        self.assertEqual([item.name for item in response.work_item_types], ["Epic", "Bug"])

    def test_search_work_items_delegates_to_adapter(self) -> None:
        captured = {}
        fake_item = AzureDevOpsWorkItemSummary(
            work_item_id=101,
            title="Login support",
            work_item_type="User Story",
            project="Payments",
        )

        def fake_search_work_items(project, query, work_item_type, max_results):
            captured["project"] = project
            captured["query"] = query
            captured["work_item_type"] = work_item_type
            captured["max_results"] = max_results
            return 1, [fake_item]

        fake_adapter = type("Adapter", (), {})()
        fake_adapter.search_work_items = fake_search_work_items

        with patch("app.services.azure_devops_requirements_service.get_azure_devops_settings", return_value=self.settings):
            with patch("app.services.azure_devops_requirements_service.get_azure_devops_adapter_for_user", return_value=fake_adapter):
                response = search_azure_devops_work_items(
                    current_user=self.user,
                    project="Payments",
                    query="login",
                    work_item_type="User Story",
                    max_results=20,
                )

        self.assertEqual(response.total, 1)
        self.assertEqual(response.work_items[0].work_item_id, 101)
        self.assertEqual(captured["project"], "Payments")
        self.assertEqual(captured["max_results"], 20)

    def test_persist_azure_requirement_mappings_writes_mapping_documents(self) -> None:
        client = FakeFirestoreClient()
        requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall support login",
                source_system="azure_devops",
                source_issue_key="101",
                source_issue_type="User Story",
                source_issue_url="https://dev.azure.com/acme/Payments/_workitems/edit/101",
                sync_target_issue_key="101",
                artifact_set_id="req-set-1",
                artifact_item_id="req-item-1",
                artifact_version_id="req-ver-1",
                artifact_version_number=1,
            )
        ]

        with patch("app.services.azure_devops_requirements_service.get_firestore_client", return_value=client):
            persisted = persist_azure_devops_requirement_mappings(
                requirements=requirements,
                actor=self.user,
                request_id="req-123",
                workflow_run_id="run-123",
                source_event_id="event-123",
            )

        self.assertEqual(persisted[0].artifact_item_id, "req-item-1")
        mapping = client.collections["azure_devops_requirement_mappings"]["req-item-1"]
        self.assertEqual(mapping["azure_work_item_id"], "101")
        self.assertEqual(mapping["sync_target_work_item_id"], "101")
        self.assertEqual(mapping["actor_user_id"], self.user.sub)
        self.assertTrue(mapping["content_hash"])


if __name__ == "__main__":
    unittest.main()