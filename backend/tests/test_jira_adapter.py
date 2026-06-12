from pathlib import Path
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

from app.adapters.jira import JiraAdapter, JiraAdapterError


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.payload


class JiraAdapterTests(unittest.TestCase):
    def test_search_issue_summaries_uses_enhanced_jql_search_endpoint(self) -> None:
        adapter = JiraAdapter(
            base_url="https://acme.atlassian.net",
            email="qa@example.com",
            api_token="token-123",
        )
        captured_requests = []

        def fake_urlopen(request, timeout, context=None):
            captured_requests.append(request)
            return _FakeResponse(
                b'{"issues":[{"id":"10000","key":"THEONE-1","fields":{"summary":"Issue one","issuetype":{"name":"Task"},"status":{"name":"Open"}}}]}'
            )

        with patch("app.adapters.jira.urlopen", side_effect=fake_urlopen):
            total, issues = adapter.search_issue_summaries('project = "THEONE"', max_results=20)

        self.assertEqual(total, 1)
        self.assertEqual([issue.key for issue in issues], ["THEONE-1"])
        self.assertEqual(captured_requests[0].get_method(), "POST")
        self.assertIn("/rest/api/3/search/jql", captured_requests[0].full_url)
        payload = json.loads(captured_requests[0].data.decode("utf-8"))
        self.assertEqual(payload["jql"], 'project = "THEONE"')
        self.assertEqual(payload["maxResults"], 20)
        self.assertIn("summary", payload["fields"])

    def test_search_issue_summaries_paginated_uses_next_page_token(self) -> None:
        adapter = JiraAdapter(
            base_url="https://acme.atlassian.net",
            email="qa@example.com",
            api_token="token-123",
        )
        captured_payloads = []
        responses = iter(
            [
                _FakeResponse(
                    b"{"
                    b'"issues":['
                    b'{"id":"10000","key":"THEONE-1","fields":{"summary":"Issue one","issuetype":{"name":"Task"},"status":{"name":"Open"}}}'
                    b"],"
                    b'"nextPageToken":"TOKEN-2"'
                    b"}"
                ),
                _FakeResponse(
                    b'{"issues":[{"id":"10001","key":"THEONE-2","fields":{"summary":"Issue two","issuetype":{"name":"Bug"},"status":{"name":"Open"}}}]}'
                ),
            ]
        )

        def fake_urlopen(request, timeout, context=None):
            captured_payloads.append(json.loads(request.data.decode("utf-8")))
            return next(responses)

        with patch("app.adapters.jira.urlopen", side_effect=fake_urlopen):
            total, issues = adapter.search_issue_summaries_paginated('project = "THEONE"', max_results=2, page_size=1)

        self.assertEqual(total, 2)
        self.assertEqual([issue.key for issue in issues], ["THEONE-1", "THEONE-2"])
        self.assertIsNone(captured_payloads[0].get("nextPageToken"))
        self.assertEqual(captured_payloads[1].get("nextPageToken"), "TOKEN-2")

    def test_get_project_issue_types_parses_project_issue_types(self) -> None:
        adapter = JiraAdapter(
            base_url="https://acme.atlassian.net",
            email="qa@example.com",
            api_token="token-123",
        )

        with patch(
            "app.adapters.jira.urlopen",
            return_value=_FakeResponse(
                b"{"
                b'"issueTypes":['
                b'{"id":"10000","name":"Epic","description":"Epic work","hierarchyLevel":1,"subtask":false},'
                b'{"id":"10001","name":"Bug","description":"Bug work","hierarchyLevel":0,"subtask":false}'
                b"]"
                b"}"
            ),
        ):
            issue_types = adapter.get_project_issue_types("THEONE")

        self.assertEqual([issue_type.name for issue_type in issue_types], ["Epic", "Bug"])
        self.assertEqual(issue_types[0].hierarchy_level, 1)

    def test_list_projects_falls_back_to_project_index_when_search_returns_empty(self) -> None:
        adapter = JiraAdapter(
            base_url="https://acme.atlassian.net",
            email="qa@example.com",
            api_token="token-123",
        )
        responses = iter(
            [
                _FakeResponse(b'{"values":[]}'),
                _FakeResponse(b'[{"id":"10001","key":"THEONE","name":"TheONE"},{"id":"10002","key":"OTHER","name":"Other Project"}]'),
            ]
        )

        with patch("app.adapters.jira.urlopen", side_effect=lambda request, timeout, context=None: next(responses)):
            projects = adapter.list_projects(query="theone", max_results=50)

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].key, "THEONE")
        self.assertEqual(projects[0].name, "TheONE")

    def test_validate_connection_retries_certificate_failure_with_certifi_bundle(self) -> None:
        adapter = JiraAdapter(
            base_url="https://acme.atlassian.net",
            email="qa@example.com",
            api_token="token-123",
        )
        cert_context = object()
        call_contexts: list[object | None] = []
        cert_error = ssl.SSLCertVerificationError(1, "certificate verify failed")

        def fake_urlopen(request, timeout, context=None):
            call_contexts.append(context)
            if len(call_contexts) == 1:
                raise URLError(cert_error)
            return _FakeResponse(b'{"accountId":"acct-1"}')

        with patch("app.adapters.jira.certifi") as certifi_module:
            certifi_module.where.return_value = "/tmp/certifi.pem"
            with patch("app.adapters.jira.ssl.create_default_context", return_value=cert_context) as create_context:
                with patch("app.adapters.jira.urlopen", side_effect=fake_urlopen):
                    payload = adapter.validate_connection()

        self.assertEqual(payload["accountId"], "acct-1")
        self.assertEqual(call_contexts, [None, cert_context])
        create_context.assert_called_once_with(cafile="/tmp/certifi.pem")

    def test_validate_connection_surfaces_helpful_ssl_message_when_no_bundle_available(self) -> None:
        adapter = JiraAdapter(
            base_url="https://acme.atlassian.net",
            email="qa@example.com",
            api_token="token-123",
        )
        cert_error = ssl.SSLCertVerificationError(1, "certificate verify failed")

        with patch("app.adapters.jira.certifi", None):
            with patch("app.adapters.jira.urlopen", side_effect=URLError(cert_error)):
                with self.assertRaises(JiraAdapterError) as raised:
                    adapter.validate_connection()

        self.assertIn("SSL certificate verification failed", str(raised.exception))
        self.assertIn("https://acme.atlassian.net", str(raised.exception))

    def test_validate_connection_surfaces_permission_guidance_for_forbidden_account(self) -> None:
        adapter = JiraAdapter(
            base_url="https://acme.atlassian.net",
            email="qa@example.com",
            api_token="token-123",
        )
        http_error = HTTPError(
            url="https://acme.atlassian.net/rest/api/3/myself",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=io.BytesIO(b'{"errorMessages":["Current user not permitted to use Jira"]}'),
        )

        with patch("app.adapters.jira.urlopen", side_effect=http_error):
            with self.assertRaises(JiraAdapterError) as raised:
                adapter.validate_connection()

        self.assertIn("does not have enough Jira access", str(raised.exception))
        self.assertIn("grant this user Jira product access", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
