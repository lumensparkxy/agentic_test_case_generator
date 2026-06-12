from pathlib import Path
import base64
import io
import json
import ssl
import sys
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.adapters.azure_devops import AzureDevOpsAdapter, AzureDevOpsAdapterError, normalize_azure_devops_url


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.payload


class AzureDevOpsAdapterTests(unittest.TestCase):
    def test_normalize_organization_url(self) -> None:
        location = normalize_azure_devops_url("https://dev.azure.com/acme")

        self.assertEqual(location.organization_url, "https://dev.azure.com/acme")
        self.assertEqual(location.organization, "acme")
        self.assertIsNone(location.default_project)

    def test_normalize_project_url_preselects_project(self) -> None:
        location = normalize_azure_devops_url("https://dev.azure.com/acme/Payments%20Platform")

        self.assertEqual(location.organization_url, "https://dev.azure.com/acme")
        self.assertEqual(location.organization, "acme")
        self.assertEqual(location.default_project, "Payments Platform")

    def test_normalize_rejects_non_azure_url(self) -> None:
        with self.assertRaises(AzureDevOpsAdapterError):
            normalize_azure_devops_url("https://example.com/acme")

    def test_authorization_header_uses_pat_basic_auth(self) -> None:
        adapter = AzureDevOpsAdapter(
            organization_url="https://dev.azure.com/acme",
            personal_access_token="pat-123",
        )

        expected = base64.b64encode(b":pat-123").decode("utf-8")
        self.assertEqual(adapter._build_authorization_header(), f"Basic {expected}")

    def test_list_projects_parses_and_filters_projects(self) -> None:
        adapter = AzureDevOpsAdapter(
            organization_url="https://dev.azure.com/acme",
            personal_access_token="pat-123",
        )
        captured_requests = []

        def fake_urlopen(request, timeout, context=None):
            captured_requests.append(request)
            return _FakeResponse(
                b'{"value":[{"id":"p1","name":"Payments","state":"wellFormed","visibility":"private"},{"id":"p2","name":"Ops","state":"wellFormed"}]}'
            )

        with patch("app.adapters.azure_devops.urlopen", side_effect=fake_urlopen):
            projects = adapter.list_projects(query="pay", max_results=10)

        self.assertEqual([project.name for project in projects], ["Payments"])
        self.assertIn("/_apis/projects", captured_requests[0].full_url)
        self.assertIn("api-version=7.1", captured_requests[0].full_url)
        self.assertIn("%24top=10", captured_requests[0].full_url)

    def test_search_work_items_uses_wiql_and_hydrates_ids(self) -> None:
        adapter = AzureDevOpsAdapter(
            organization_url="https://dev.azure.com/acme",
            personal_access_token="pat-123",
        )
        captured_requests = []
        responses = iter(
            [
                _FakeResponse(b'{"workItems":[{"id":101},{"id":102}]}'),
                _FakeResponse(
                    b'{"value":['
                    b'{"id":101,"rev":7,"fields":{"System.Title":"Login","System.WorkItemType":"User Story","System.State":"Active","System.TeamProject":"Payments","System.ChangedDate":"2026-05-08T10:00:00Z"}},'
                    b'{"id":102,"fields":{"System.Title":"Logout","System.WorkItemType":"Bug","System.TeamProject":"Payments"}}'
                    b"]}"
                ),
            ]
        )

        def fake_urlopen(request, timeout, context=None):
            captured_requests.append(request)
            return next(responses)

        with patch("app.adapters.azure_devops.urlopen", side_effect=fake_urlopen):
            total, work_items = adapter.search_work_items(
                project="Payments",
                query="login",
                work_item_type="User Story",
                max_results=20,
            )

        self.assertEqual(total, 2)
        self.assertEqual([item.work_item_id for item in work_items], [101, 102])
        self.assertEqual(work_items[0].title, "Login")
        self.assertEqual(captured_requests[0].get_method(), "POST")
        self.assertIn("/Payments/_apis/wit/wiql", captured_requests[0].full_url)
        payload = json.loads(captured_requests[0].data.decode("utf-8"))
        self.assertIn("[System.TeamProject] = 'Payments'", payload["query"])
        self.assertIn("[System.WorkItemType] = 'User Story'", payload["query"])
        self.assertIn("[System.Title] CONTAINS 'login'", payload["query"])
        self.assertIn("ids=101%2C102", captured_requests[1].full_url)
        self.assertIn("fields=", captured_requests[1].full_url)
        self.assertNotIn("%24expand=relations", captured_requests[1].full_url)

    def test_get_work_items_batches_azure_limit(self) -> None:
        adapter = AzureDevOpsAdapter(
            organization_url="https://dev.azure.com/acme",
            personal_access_token="pat-123",
        )
        captured_urls = []

        def fake_urlopen(request, timeout, context=None):
            captured_urls.append(request.full_url)
            ids_query = request.full_url.split("ids=", 1)[1].split("&", 1)[0]
            first_id = int(ids_query.split("%2C", 1)[0])
            return _FakeResponse(
                json.dumps(
                    {
                        "value": [
                            {
                                "id": first_id,
                                "fields": {
                                    "System.Title": f"Item {first_id}",
                                    "System.WorkItemType": "Task",
                                    "System.TeamProject": "Payments",
                                },
                            }
                        ]
                    }
                ).encode("utf-8")
            )

        with patch("app.adapters.azure_devops.urlopen", side_effect=fake_urlopen):
            work_items = adapter.get_work_items("Payments", list(range(1, 202)))

        self.assertEqual(len(captured_urls), 2)
        self.assertEqual([item.work_item_id for item in work_items], [1, 201])
        self.assertTrue(all("fields=" in url for url in captured_urls))
        self.assertTrue(all("%24expand=relations" not in url for url in captured_urls))

    def test_get_work_item_with_children_expands_relations_without_fields_filter(self) -> None:
        adapter = AzureDevOpsAdapter(
            organization_url="https://dev.azure.com/acme",
            personal_access_token="pat-123",
        )
        captured_urls = []
        responses = iter(
            [
                _FakeResponse(
                    b'{"id":101,"fields":{"System.Title":"Epic","System.WorkItemType":"Epic","System.TeamProject":"Payments"},'
                    b'"relations":[{"rel":"System.LinkTypes.Hierarchy-Forward","url":"https://dev.azure.com/acme/_apis/wit/workItems/102"}]}'
                ),
                _FakeResponse(b'{"value":[{"id":102,"fields":{"System.Title":"Story","System.WorkItemType":"User Story","System.TeamProject":"Payments"}}]}'),
            ]
        )

        def fake_urlopen(request, timeout, context=None):
            captured_urls.append(request.full_url)
            return next(responses)

        with patch("app.adapters.azure_devops.urlopen", side_effect=fake_urlopen):
            work_items = adapter.get_work_item_with_children("Payments", 101)

        self.assertEqual([item.work_item_id for item in work_items], [101, 102])
        self.assertIn("%24expand=relations", captured_urls[0])
        self.assertNotIn("fields=", captured_urls[0])
        self.assertIn("fields=", captured_urls[1])
        self.assertNotIn("%24expand=relations", captured_urls[1])

    def test_update_work_item_description_uses_json_patch(self) -> None:
        adapter = AzureDevOpsAdapter(
            organization_url="https://dev.azure.com/acme/Payments",
            personal_access_token="pat-123",
        )
        captured_requests = []

        def fake_urlopen(request, timeout, context=None):
            captured_requests.append(request)
            return _FakeResponse(b'{"id":101}')

        with patch("app.adapters.azure_devops.urlopen", side_effect=fake_urlopen):
            adapter.update_work_item_description(
                project="Payments",
                work_item_id=101,
                html_description="<p>Updated</p>",
                rev=7,
                history_note="Synced requirements",
            )

        request = captured_requests[0]
        self.assertEqual(request.get_method(), "PATCH")
        self.assertEqual(request.headers["Content-type"], "application/json-patch+json")
        operations = json.loads(request.data.decode("utf-8"))
        self.assertEqual(operations[0], {"op": "test", "path": "/rev", "value": 7})
        self.assertEqual(operations[1]["path"], "/fields/System.Description")
        self.assertEqual(operations[2]["path"], "/fields/System.History")

    def test_create_work_item_uses_json_patch_and_parent_relation(self) -> None:
        adapter = AzureDevOpsAdapter(
            organization_url="https://dev.azure.com/acme",
            personal_access_token="pat-123",
        )
        captured_requests = []

        def fake_urlopen(request, timeout, context=None):
            captured_requests.append(request)
            return _FakeResponse(
                b'{"id":102,"rev":1,"fields":{"System.Title":"Sample issue","System.WorkItemType":"User Story","System.TeamProject":"Payments"}}'
            )

        with patch("app.adapters.azure_devops.urlopen", side_effect=fake_urlopen):
            created = adapter.create_work_item(
                project="Payments",
                work_item_type="User Story",
                fields={"System.Title": "Sample issue", "System.Description": "<p>Sample</p>"},
                parent_id=101,
                relation_comment="Linked to sample epic",
            )

        request = captured_requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertIn("/Payments/_apis/wit/workitems/$User%20Story", request.full_url)
        self.assertEqual(request.headers["Content-type"], "application/json-patch+json")
        operations = json.loads(request.data.decode("utf-8"))
        self.assertEqual(operations[0]["path"], "/fields/System.Title")
        self.assertEqual(operations[2]["path"], "/relations/-")
        self.assertEqual(operations[2]["value"]["rel"], "System.LinkTypes.Hierarchy-Reverse")
        self.assertTrue(operations[2]["value"]["url"].endswith("/_apis/wit/workItems/101"))
        self.assertEqual(created.work_item_id, 102)

    def test_validate_connection_retries_certificate_failure_with_certifi_bundle(self) -> None:
        adapter = AzureDevOpsAdapter(
            organization_url="https://dev.azure.com/acme",
            personal_access_token="pat-123",
        )
        cert_context = object()
        call_contexts: list[object | None] = []
        cert_error = ssl.SSLCertVerificationError(1, "certificate verify failed")

        def fake_urlopen(request, timeout, context=None):
            call_contexts.append(context)
            if len(call_contexts) == 1:
                raise URLError(cert_error)
            return _FakeResponse(b'{"value":[]}')

        with patch("app.adapters.azure_devops.certifi") as certifi_module:
            certifi_module.where.return_value = "/tmp/certifi.pem"
            with patch("app.adapters.azure_devops.ssl.create_default_context", return_value=cert_context) as create_context:
                with patch("app.adapters.azure_devops.urlopen", side_effect=fake_urlopen):
                    payload = adapter.validate_connection()

        self.assertEqual(payload["organization"], "acme")
        self.assertEqual(call_contexts, [None, cert_context])
        create_context.assert_called_once_with(cafile="/tmp/certifi.pem")

    def test_validate_connection_surfaces_permission_guidance_for_forbidden_token(self) -> None:
        adapter = AzureDevOpsAdapter(
            organization_url="https://dev.azure.com/acme",
            personal_access_token="pat-123",
        )
        http_error = HTTPError(
            url="https://dev.azure.com/acme/_apis/projects",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"TF401019: access denied"}'),
        )

        with patch("app.adapters.azure_devops.urlopen", side_effect=http_error):
            with self.assertRaises(AzureDevOpsAdapterError) as raised:
                adapter.validate_connection()

        self.assertIn("does not have enough access", str(raised.exception))
        self.assertIn("Work Items read/write", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
