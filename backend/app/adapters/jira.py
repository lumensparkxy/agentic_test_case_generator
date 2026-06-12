from __future__ import annotations

import base64
import json
import ssl
from typing import Any, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

try:
    import certifi
except ImportError:  # pragma: no cover - optional dependency fallback
    certifi = None

from ..models import JiraIssueSummary, JiraIssueTypeSummary, JiraProjectSummary, JiraStoredConnection
from ..observability.integrations import observe_integration_request


DEFAULT_ISSUE_FIELDS = (
    "summary",
    "description",
    "issuetype",
    "status",
    "parent",
    "labels",
    "updated",
)


class JiraAdapterError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None, payload: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class JiraAdapter:
    def __init__(self, base_url: str, email: str, api_token: str, *, timeout_seconds: int = 15):
        self.base_url = str(base_url).rstrip("/")
        self.email = email
        self.api_token = api_token
        self.timeout_seconds = max(1, int(timeout_seconds or 15))

    @classmethod
    def from_connection(cls, connection: JiraStoredConnection, *, timeout_seconds: int = 15) -> "JiraAdapter":
        return cls(
            base_url=str(connection.base_url),
            email=connection.email,
            api_token=connection.api_token,
            timeout_seconds=timeout_seconds,
        )

    def validate_connection(self) -> dict[str, Any]:
        return self._request_json("GET", "/rest/api/3/myself", operation="validate_connection")

    def list_projects(self, *, query: Optional[str] = None, max_results: int = 50) -> list[JiraProjectSummary]:
        normalized_query = str(query or "").strip()
        normalized_max_results = max(1, int(max_results or 50))
        response = self._request_json(
            "GET",
            "/rest/api/3/project/search",
            query={
                "query": normalized_query or None,
                "maxResults": normalized_max_results,
            },
            operation="list_projects",
        )
        projects = response.get("values") if isinstance(response.get("values"), list) else []
        if not projects:
            fallback_response = self._request_json("GET", "/rest/api/3/project", operation="list_projects")
            projects = fallback_response.get("value") if isinstance(fallback_response.get("value"), list) else []

        filtered_projects = self._filter_projects(projects, normalized_query)
        return [
            JiraProjectSummary(
                project_id=str(project.get("id") or ""),
                key=str(project.get("key") or ""),
                name=str(project.get("name") or project.get("key") or ""),
            )
            for project in filtered_projects[:normalized_max_results]
            if project.get("id") and project.get("key")
        ]

    def get_issue(self, issue_key: str, *, fields: Optional[Sequence[str]] = None) -> JiraIssueSummary:
        response = self._request_json(
            "GET",
            f"/rest/api/3/issue/{quote(issue_key, safe='')}",
            query={"fields": self._format_fields(fields)},
            operation="get_issue",
        )
        return self._parse_issue(response)

    def get_project_issue_types(self, project_key: str) -> list[JiraIssueTypeSummary]:
        response = self._request_json(
            "GET",
            f"/rest/api/3/project/{quote(str(project_key or '').strip(), safe='')}",
            operation="get_project_issue_types",
        )
        issue_types = response.get("issueTypes") if isinstance(response.get("issueTypes"), list) else []
        parsed_issue_types: list[JiraIssueTypeSummary] = []
        for issue_type in issue_types:
            issue_type_id = str(issue_type.get("id") or "").strip()
            name = str(issue_type.get("name") or "").strip()
            if not issue_type_id or not name:
                continue
            scope = issue_type.get("scope") or {}
            parsed_issue_types.append(
                JiraIssueTypeSummary(
                    issue_type_id=issue_type_id,
                    name=name,
                    description=str(issue_type.get("description") or "").strip() or None,
                    hierarchy_level=issue_type.get("hierarchyLevel"),
                    subtask=bool(issue_type.get("subtask")),
                    scope_type=str(scope.get("type") or "").strip() or None,
                )
            )
        return parsed_issue_types

    def update_issue_fields(self, issue_key: str, fields: dict[str, Any]) -> None:
        self._request_json(
            "PUT",
            f"/rest/api/3/issue/{quote(issue_key, safe='')}",
            body={"fields": fields},
            operation="update_issue_fields",
        )

    def update_issue_description(self, issue_key: str, description_adf: dict[str, Any]) -> None:
        self.update_issue_fields(issue_key, {"description": description_adf})

    def search_issues(
        self,
        jql: str,
        *,
        fields: Optional[Sequence[str]] = None,
        max_results: int = 50,
        next_page_token: Optional[str] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "jql": jql,
            "fields": self._resolve_fields(fields),
            "maxResults": max(1, int(max_results or 50)),
        }
        if next_page_token:
            body["nextPageToken"] = next_page_token
        return self._request_json("POST", "/rest/api/3/search/jql", body=body, operation="search_issues")

    def search_issue_summaries(
        self,
        jql: str,
        *,
        fields: Optional[Sequence[str]] = None,
        max_results: int = 50,
        start_at: int = 0,
    ) -> tuple[int, list[JiraIssueSummary]]:
        response = self.search_issues(
            jql,
            fields=fields,
            max_results=max_results,
            next_page_token=None,
        )
        issues = [self._parse_issue(issue) for issue in (response.get("issues") or [])]
        return int(response.get("total") or len(issues)), issues

    def search_issue_summaries_paginated(
        self,
        jql: str,
        *,
        fields: Optional[Sequence[str]] = None,
        max_results: int = 100,
        page_size: int = 50,
    ) -> tuple[int, list[JiraIssueSummary]]:
        all_issues: list[JiraIssueSummary] = []
        next_page_token: Optional[str] = None
        normalized_page_size = max(1, int(page_size or 50))
        normalized_max_results = max(1, int(max_results or normalized_page_size))

        while len(all_issues) < normalized_max_results:
            page_limit = min(normalized_page_size, normalized_max_results - len(all_issues))
            response = self.search_issues(
                jql,
                fields=fields,
                max_results=page_limit,
                next_page_token=next_page_token,
            )
            page = [self._parse_issue(issue) for issue in (response.get("issues") or [])]
            if not page:
                break
            all_issues.extend(page)
            next_page_token = str(response.get("nextPageToken") or "").strip() or None
            if not next_page_token:
                break

        return len(all_issues), all_issues[:normalized_max_results]

    def get_epic_with_children(self, epic_key: str, *, page_size: int = 50) -> list[JiraIssueSummary]:
        normalized_key = str(epic_key or "").strip()
        if not normalized_key:
            raise JiraAdapterError("Epic key is required to fetch JIRA child issues")

        jql = (
            f'issuekey = "{self._escape_jql_value(normalized_key)}" '
            f'OR parent = "{self._escape_jql_value(normalized_key)}" '
            f'OR "Epic Link" = "{self._escape_jql_value(normalized_key)}" '
            "ORDER BY key ASC"
        )
        _, issues = self.search_issue_summaries_paginated(
            jql,
            max_results=max(page_size * 4, page_size),
            page_size=page_size,
        )

        deduped: list[JiraIssueSummary] = []
        seen: set[str] = set()
        for issue in sorted(issues, key=lambda item: (0 if item.key == normalized_key else 1, item.parent_key or "", item.key)):
            if issue.key in seen:
                continue
            seen.add(issue.key)
            deduped.append(issue)
        return deduped

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: Optional[dict[str, Any]] = None,
        body: Optional[dict[str, Any]] = None,
        operation: str = "request",
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        query_payload = {key: value for key, value in (query or {}).items() if value is not None and value != "" and value != []}
        if query_payload:
            url = f"{url}?{urlencode(query_payload, doseq=True)}"

        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            url,
            data=data,
            method=method.upper(),
            headers={
                "Accept": "application/json",
                "Authorization": self._build_authorization_header(),
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )

        with observe_integration_request(provider="jira", operation=operation):
            try:
                payload = self._read_response_with_ssl_fallback(request)
            except HTTPError as exc:
                payload_text = exc.read().decode("utf-8", errors="ignore")
                parsed_payload = self._parse_json(payload_text)
                message = self._build_http_error_message(exc, parsed_payload, payload_text)
                raise JiraAdapterError(
                    message,
                    status_code=exc.code,
                    payload=parsed_payload or {},
                ) from exc
            except URLError as exc:
                raise JiraAdapterError(self._build_connection_error_message(exc)) from exc

            parsed_payload = self._parse_json(payload)
        return parsed_payload or {}

    def _read_response_with_ssl_fallback(self, request: Request) -> str:
        try:
            return self._read_response(request)
        except URLError as exc:
            fallback_context = self._build_certifi_ssl_context()
            if fallback_context is None or not self._is_certificate_verification_error(exc):
                raise
            return self._read_response(request, context=fallback_context)

    def _read_response(self, request: Request, *, context: Optional[ssl.SSLContext] = None) -> str:
        open_kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
        if context is not None:
            open_kwargs["context"] = context

        with urlopen(request, **open_kwargs) as response:
            return response.read().decode("utf-8")

    def _build_certifi_ssl_context(self) -> Optional[ssl.SSLContext]:
        if certifi is None:
            return None
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:  # pragma: no cover - defensive fallback for broken certificate stores
            return None

    def _is_certificate_verification_error(self, exc: URLError) -> bool:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return True
        if isinstance(reason, ssl.SSLError):
            normalized = str(reason).lower()
            return "certificate verify failed" in normalized or "certificate_verify_failed" in normalized
        normalized = str(reason).lower()
        return "certificate verify failed" in normalized or "certificate_verify_failed" in normalized

    def _build_connection_error_message(self, exc: URLError) -> str:
        reason = getattr(exc, "reason", exc)
        if self._is_certificate_verification_error(exc):
            return (
                f"SSL certificate verification failed while connecting to JIRA Cloud at {self.base_url}. "
                "The backend retried using the bundled CA store, but the certificate chain still could not be trusted. "
                f"Original error: {reason}"
            )
        return f"Could not reach JIRA Cloud at {self.base_url}: {reason}"

    def _build_authorization_header(self) -> str:
        token = base64.b64encode(f"{self.email}:{self.api_token}".encode("utf-8")).decode("utf-8")
        return f"Basic {token}"

    def _resolve_fields(self, fields: Optional[Sequence[str]]) -> list[str]:
        return list(dict.fromkeys(field for field in list(fields or DEFAULT_ISSUE_FIELDS) if field))

    def _format_fields(self, fields: Optional[Sequence[str]]) -> str:
        return ",".join(self._resolve_fields(fields))

    def _parse_issue(self, issue: dict[str, Any]) -> JiraIssueSummary:
        fields = issue.get("fields") or {}
        parent = fields.get("parent") or {}
        description_value = fields.get("description")
        return JiraIssueSummary(
            issue_id=str(issue.get("id") or ""),
            key=str(issue.get("key") or ""),
            summary=str(fields.get("summary") or issue.get("key") or ""),
            issue_type=str((fields.get("issuetype") or {}).get("name") or "Issue"),
            status=str((fields.get("status") or {}).get("name") or "") or None,
            parent_key=str(parent.get("key") or "") or None,
            web_url=self._build_issue_url(str(issue.get("key") or "")) if issue.get("key") else None,
            updated_at=fields.get("updated") or None,
            labels=[str(label) for label in (fields.get("labels") or []) if str(label).strip()],
            description_text=self._adf_to_text(description_value) or None,
            description_adf=description_value if isinstance(description_value, dict) else None,
        )

    def _build_issue_url(self, issue_key: str) -> str:
        return f"{self.base_url}/browse/{quote(issue_key, safe='')}"

    def _adf_to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return self._join_fragments((self._adf_to_text(item) for item in value), separator="\n")
        if not isinstance(value, dict):
            return str(value).strip()

        if value.get("type") == "text":
            return str(value.get("text") or "")

        separator = "\n" if value.get("type") in {"doc", "paragraph", "heading", "listItem", "bulletList", "orderedList"} else " "
        return self._join_fragments(
            (self._adf_to_text(child) for child in (value.get("content") or [])),
            separator=separator,
        )

    def _join_fragments(self, fragments, *, separator: str) -> str:
        cleaned = [str(fragment).strip() for fragment in fragments if str(fragment).strip()]
        joined = separator.join(cleaned)
        return "\n".join(part.strip() for part in joined.splitlines() if part.strip()).strip()

    def _parse_json(self, payload_text: str) -> Optional[dict[str, Any]]:
        payload_text = (payload_text or "").strip()
        if not payload_text:
            return {}
        try:
            parsed = json.loads(payload_text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def _extract_error_message(self, payload: Optional[dict[str, Any]]) -> Optional[str]:
        if not payload:
            return None
        if isinstance(payload.get("errorMessages"), list) and payload["errorMessages"]:
            return "; ".join(str(item) for item in payload["errorMessages"] if str(item).strip())
        errors = payload.get("errors")
        if isinstance(errors, dict) and errors:
            return "; ".join(f"{key}: {value}" for key, value in errors.items())
        return str(payload.get("message") or payload.get("detail") or "").strip() or None

    def _build_http_error_message(
        self,
        exc: HTTPError,
        payload: Optional[dict[str, Any]],
        payload_text: str,
    ) -> str:
        raw_message = self._extract_error_message(payload) or payload_text or str(exc)
        normalized = str(raw_message).lower()

        if exc.code == 401:
            return (
                f"JIRA Cloud rejected the credentials for {self.email} at {self.base_url}. "
                "Verify that the Atlassian email and API token belong to the same account and that the token is still active."
            )

        if exc.code == 403 and (
            "not permitted to use jira" in normalized or "insufficient permissions" in normalized or "access denied" in normalized or "forbidden" in normalized
        ):
            return (
                f"The Atlassian account {self.email} authenticated, but does not have enough Jira access for {self.base_url}. "
                "Ask a Jira site admin to grant this user Jira product access. "
                "If the connection succeeds but no projects appear, also grant Browse Projects permission for the target project."
            )

        return f"JIRA request failed ({exc.code}): {raw_message}"

    def _filter_projects(self, projects: Sequence[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        normalized_query = str(query or "").strip().lower()
        if not normalized_query:
            return list(projects)

        filtered: list[dict[str, Any]] = []
        for project in projects:
            key = str(project.get("key") or "").strip().lower()
            name = str(project.get("name") or "").strip().lower()
            if normalized_query in key or normalized_query in name:
                filtered.append(project)
        return filtered

    def _escape_jql_value(self, value: str) -> str:
        return str(value or "").replace("\\", "\\\\").replace('"', '\\"')
