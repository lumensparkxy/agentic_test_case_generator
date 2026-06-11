from __future__ import annotations

import base64
import json
import re
import ssl
from dataclasses import dataclass
from html import unescape
from typing import Any, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse, unquote
from urllib.request import Request, urlopen

try:
    import certifi
except ImportError:  # pragma: no cover - optional dependency fallback
    certifi = None

from ..models import (
    AzureDevOpsProjectSummary,
    AzureDevOpsStoredConnection,
    AzureDevOpsWorkItemSummary,
    AzureDevOpsWorkItemTypeSummary,
)


DEFAULT_WORK_ITEM_FIELDS = (
    "System.Id",
    "System.Title",
    "System.WorkItemType",
    "System.State",
    "System.Description",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
    "System.ChangedDate",
    "System.TeamProject",
    "System.AreaPath",
    "System.IterationPath",
    "System.Tags",
    "System.AssignedTo",
    "System.Parent",
)


@dataclass(frozen=True)
class AzureDevOpsLocation:
    organization_url: str
    organization: str
    default_project: Optional[str] = None


class AzureDevOpsAdapterError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None, payload: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class AzureDevOpsAdapter:
    def __init__(
        self,
        organization_url: str,
        personal_access_token: str,
        *,
        default_project: Optional[str] = None,
        timeout_seconds: int = 15,
        api_version: str = "7.1",
    ):
        location = normalize_azure_devops_url(organization_url)
        self.organization_url = location.organization_url
        self.organization = location.organization
        self.default_project = str(default_project or location.default_project or "").strip() or None
        self.personal_access_token = personal_access_token
        self.timeout_seconds = max(1, int(timeout_seconds or 15))
        self.api_version = str(api_version or "7.1").strip() or "7.1"

    @classmethod
    def from_connection(
        cls,
        connection: AzureDevOpsStoredConnection,
        *,
        timeout_seconds: int = 15,
        api_version: str = "7.1",
    ) -> "AzureDevOpsAdapter":
        return cls(
            organization_url=str(connection.organization_url),
            personal_access_token=connection.personal_access_token,
            default_project=connection.default_project,
            timeout_seconds=timeout_seconds,
            api_version=api_version,
        )

    def validate_connection(self) -> dict[str, Any]:
        projects = self.list_projects(max_results=1)
        return {
            "organization": self.organization,
            "organizationUrl": self.organization_url,
            "projectCountVisible": len(projects),
        }

    def list_projects(self, *, query: Optional[str] = None, max_results: int = 50) -> list[AzureDevOpsProjectSummary]:
        normalized_max_results = max(1, int(max_results or 50))
        response = self._request_json(
            "GET",
            "/_apis/projects",
            query={"$top": normalized_max_results},
        )
        projects = response.get("value") if isinstance(response.get("value"), list) else []
        filtered = self._filter_projects(projects, query or "")
        return [self._parse_project(project) for project in filtered[:normalized_max_results] if project.get("id")]

    def get_project_work_item_types(self, project: str) -> list[AzureDevOpsWorkItemTypeSummary]:
        normalized_project = _normalize_required_value(project, "Project is required to load Azure DevOps work item types")
        response = self._request_json(
            "GET",
            f"/{quote(normalized_project, safe='')}/_apis/wit/workitemtypes",
        )
        work_item_types = response.get("value") if isinstance(response.get("value"), list) else []
        parsed: list[AzureDevOpsWorkItemTypeSummary] = []
        for item in work_item_types:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            parsed.append(
                AzureDevOpsWorkItemTypeSummary(
                    name=name,
                    reference_name=str(item.get("referenceName") or "").strip() or None,
                    description=str(item.get("description") or "").strip() or None,
                    color=str(item.get("color") or "").strip() or None,
                    icon=str(item.get("icon") or "").strip() or None,
                )
            )
        return parsed

    def query_work_item_ids(self, *, project: str, wiql: str, max_results: int = 50) -> list[int]:
        normalized_project = _normalize_required_value(project or self.default_project, "Project is required for Azure DevOps WIQL queries")
        normalized_wiql = _normalize_required_value(wiql, "WIQL is required to search Azure DevOps work items")
        response = self._request_json(
            "POST",
            f"/{quote(normalized_project, safe='')}/_apis/wit/wiql",
            query={"$top": max(1, int(max_results or 50))},
            body={"query": normalized_wiql},
        )
        references = response.get("workItems") if isinstance(response.get("workItems"), list) else []
        return [int(item.get("id")) for item in references if str(item.get("id") or "").isdigit()]

    def search_work_items(
        self,
        *,
        project: str,
        query: Optional[str] = None,
        work_item_type: Optional[str] = None,
        max_results: int = 50,
    ) -> tuple[int, list[AzureDevOpsWorkItemSummary]]:
        normalized_project = _normalize_required_value(project or self.default_project, "Project is required to search Azure DevOps work items")
        wiql = self._build_search_wiql(
            project=normalized_project,
            query=query,
            work_item_type=work_item_type,
        )
        ids = self.query_work_item_ids(project=normalized_project, wiql=wiql, max_results=max_results)
        if not ids:
            return 0, []
        work_items = self.get_work_items(normalized_project, ids[: max(1, int(max_results or 50))])
        return len(ids), work_items

    def get_work_item(
        self,
        project: str,
        work_item_id: int,
        *,
        fields: Optional[Sequence[str]] = None,
        expand_relations: bool = False,
    ) -> AzureDevOpsWorkItemSummary:
        normalized_project = _normalize_required_value(project or self.default_project, "Project is required to fetch Azure DevOps work items")
        query: dict[str, Any] = {}
        if expand_relations:
            query["$expand"] = "relations"
        else:
            query["fields"] = self._format_fields(fields)
        response = self._request_json(
            "GET",
            f"/{quote(normalized_project, safe='')}/_apis/wit/workitems/{int(work_item_id)}",
            query=query,
        )
        return self._parse_work_item(response, project=normalized_project)

    def get_work_items(
        self,
        project: str,
        work_item_ids: Sequence[int],
        *,
        fields: Optional[Sequence[str]] = None,
        expand_relations: bool = False,
    ) -> list[AzureDevOpsWorkItemSummary]:
        normalized_project = _normalize_required_value(project or self.default_project, "Project is required to fetch Azure DevOps work items")
        ids = [int(work_item_id) for work_item_id in work_item_ids if int(work_item_id) > 0]
        work_items: list[AzureDevOpsWorkItemSummary] = []
        for batch in _chunked(ids, 200):
            query: dict[str, Any] = {
                "ids": ",".join(str(work_item_id) for work_item_id in batch),
            }
            if expand_relations:
                query["$expand"] = "relations"
            else:
                query["fields"] = self._format_fields(fields)
            response = self._request_json(
                "GET",
                f"/{quote(normalized_project, safe='')}/_apis/wit/workitems",
                query=query,
            )
            values = response.get("value") if isinstance(response.get("value"), list) else []
            work_items.extend(self._parse_work_item(item, project=normalized_project) for item in values)
        return work_items

    def get_work_item_with_children(self, project: str, work_item_id: int) -> list[AzureDevOpsWorkItemSummary]:
        root = self.get_work_item(project, work_item_id, expand_relations=True)
        child_ids = self._extract_child_ids(root.relations)
        children = self.get_work_items(project, child_ids) if child_ids else []
        return [root, *children]

    def update_work_item_description(
        self,
        *,
        project: str,
        work_item_id: int,
        html_description: str,
        rev: Optional[int] = None,
        history_note: Optional[str] = None,
    ) -> dict[str, Any]:
        normalized_project = _normalize_required_value(project or self.default_project, "Project is required to update Azure DevOps work items")
        operations: list[dict[str, Any]] = []
        if rev is not None:
            operations.append({"op": "test", "path": "/rev", "value": int(rev)})
        operations.append({"op": "add", "path": "/fields/System.Description", "value": html_description})
        if str(history_note or "").strip():
            operations.append({"op": "add", "path": "/fields/System.History", "value": str(history_note).strip()})
        return self._request_json(
            "PATCH",
            f"/{quote(normalized_project, safe='')}/_apis/wit/workitems/{int(work_item_id)}",
            body=operations,
            content_type="application/json-patch+json",
        )

    def create_work_item(
        self,
        *,
        project: str,
        work_item_type: str,
        fields: dict[str, Any],
        parent_id: Optional[int] = None,
        relation_comment: Optional[str] = None,
    ) -> AzureDevOpsWorkItemSummary:
        normalized_project = _normalize_required_value(project or self.default_project, "Project is required to create Azure DevOps work items")
        normalized_type = _normalize_required_value(work_item_type, "Work item type is required to create Azure DevOps work items")
        operations = [
            {"op": "add", "path": f"/fields/{field_name}", "value": value}
            for field_name, value in (fields or {}).items()
            if str(field_name or "").strip() and value is not None and value != ""
        ]
        if not operations:
            raise AzureDevOpsAdapterError("At least one field is required to create an Azure DevOps work item")
        if parent_id is not None:
            relation: dict[str, Any] = {
                "rel": "System.LinkTypes.Hierarchy-Reverse",
                "url": f"{self.organization_url}/_apis/wit/workItems/{int(parent_id)}",
            }
            if str(relation_comment or "").strip():
                relation["attributes"] = {"comment": str(relation_comment).strip()}
            operations.append({"op": "add", "path": "/relations/-", "value": relation})

        response = self._request_json(
            "POST",
            f"/{quote(normalized_project, safe='')}/_apis/wit/workitems/${quote(normalized_type, safe='')}",
            body=operations,
            content_type="application/json-patch+json",
        )
        return self._parse_work_item(response, project=normalized_project)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: Optional[dict[str, Any]] = None,
        body: Optional[Any] = None,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        url = f"{self.organization_url}{path}"
        query_payload = {
            key: value
            for key, value in {"api-version": self.api_version, **(query or {})}.items()
            if value is not None and value != "" and value != []
        }
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
                **({"Content-Type": content_type} if body is not None else {}),
            },
        )

        try:
            payload = self._read_response_with_ssl_fallback(request)
        except HTTPError as exc:
            payload_text = exc.read().decode("utf-8", errors="ignore")
            parsed_payload = self._parse_json(payload_text)
            message = self._build_http_error_message(exc, parsed_payload, payload_text)
            raise AzureDevOpsAdapterError(
                message,
                status_code=exc.code,
                payload=parsed_payload or {},
            ) from exc
        except URLError as exc:
            raise AzureDevOpsAdapterError(self._build_connection_error_message(exc)) from exc

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
                f"SSL certificate verification failed while connecting to Azure DevOps at {self.organization_url}. "
                "The backend retried using the bundled CA store, but the certificate chain still could not be trusted. "
                f"Original error: {reason}"
            )
        return f"Could not reach Azure DevOps at {self.organization_url}: {reason}"

    def _build_authorization_header(self) -> str:
        token = base64.b64encode(f":{self.personal_access_token}".encode("utf-8")).decode("utf-8")
        return f"Basic {token}"

    def _parse_project(self, project: dict[str, Any]) -> AzureDevOpsProjectSummary:
        return AzureDevOpsProjectSummary(
            project_id=str(project.get("id") or ""),
            name=str(project.get("name") or ""),
            description=str(project.get("description") or "").strip() or None,
            state=str(project.get("state") or "").strip() or None,
            visibility=str(project.get("visibility") or "").strip() or None,
            url=project.get("url") or None,
        )

    def _parse_work_item(self, work_item: dict[str, Any], *, project: Optional[str] = None) -> AzureDevOpsWorkItemSummary:
        fields = work_item.get("fields") if isinstance(work_item.get("fields"), dict) else {}
        work_item_id = int(work_item.get("id") or fields.get("System.Id") or 0)
        parsed_project = str(fields.get("System.TeamProject") or project or self.default_project or "").strip() or None
        return AzureDevOpsWorkItemSummary(
            work_item_id=work_item_id,
            title=str(fields.get("System.Title") or f"Work item {work_item_id}"),
            work_item_type=str(fields.get("System.WorkItemType") or "Work Item"),
            state=str(fields.get("System.State") or "").strip() or None,
            project=parsed_project,
            area_path=str(fields.get("System.AreaPath") or "").strip() or None,
            iteration_path=str(fields.get("System.IterationPath") or "").strip() or None,
            assigned_to=self._display_identity(fields.get("System.AssignedTo")),
            changed_at=fields.get("System.ChangedDate") or None,
            tags=self._parse_tags(fields.get("System.Tags")),
            parent_id=_coerce_optional_int(fields.get("System.Parent")),
            web_url=self._build_work_item_url(parsed_project, work_item_id) if parsed_project and work_item_id else None,
            description_text=_html_to_text(fields.get("System.Description")) or None,
            acceptance_criteria_text=_html_to_text(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria")) or None,
            rev=_coerce_optional_int(work_item.get("rev")),
            fields=fields,
            relations=work_item.get("relations") if isinstance(work_item.get("relations"), list) else [],
        )

    def _build_work_item_url(self, project: str, work_item_id: int) -> str:
        return f"{self.organization_url}/{quote(project, safe='')}/_workitems/edit/{int(work_item_id)}"

    def _format_fields(self, fields: Optional[Sequence[str]]) -> str:
        return ",".join(dict.fromkeys(field for field in list(fields or DEFAULT_WORK_ITEM_FIELDS) if str(field).strip()))

    def _build_search_wiql(self, *, project: str, query: Optional[str], work_item_type: Optional[str]) -> str:
        clauses = [f"[System.TeamProject] = '{_escape_wiql_literal(project)}'"]
        normalized_type = str(work_item_type or "").strip()
        if normalized_type and normalized_type.lower() not in {"any", "all", "any work item type"}:
            clauses.append(f"[System.WorkItemType] = '{_escape_wiql_literal(normalized_type)}'")
        normalized_query = str(query or "").strip()
        if normalized_query:
            escaped = _escape_wiql_literal(normalized_query)
            clauses.append(f"([System.Title] CONTAINS '{escaped}' OR [System.Description] CONTAINS '{escaped}')")
        return "SELECT [System.Id] FROM WorkItems WHERE " + " AND ".join(clauses) + " ORDER BY [System.ChangedDate] DESC"

    def _extract_child_ids(self, relations: Sequence[dict[str, Any]]) -> list[int]:
        child_ids: list[int] = []
        for relation in relations or []:
            if str(relation.get("rel") or "") != "System.LinkTypes.Hierarchy-Forward":
                continue
            match = re.search(r"/workItems/(\d+)(?:$|[/?#])", str(relation.get("url") or ""))
            if match:
                child_ids.append(int(match.group(1)))
        return list(dict.fromkeys(child_ids))

    def _display_identity(self, value: Any) -> Optional[str]:
        if isinstance(value, dict):
            return str(value.get("displayName") or value.get("uniqueName") or "").strip() or None
        return str(value or "").strip() or None

    def _parse_tags(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [tag.strip() for tag in str(value or "").split(";") if tag.strip()]

    def _filter_projects(self, projects: Sequence[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        normalized_query = str(query or "").strip().lower()
        if not normalized_query:
            return list(projects)
        return [
            project
            for project in projects
            if normalized_query in str(project.get("name") or "").strip().lower()
            or normalized_query in str(project.get("id") or "").strip().lower()
        ]

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
        if isinstance(payload.get("message"), str) and payload["message"].strip():
            return payload["message"].strip()
        if isinstance(payload.get("value"), dict) and payload["value"].get("Message"):
            return str(payload["value"].get("Message")).strip()
        return str(payload.get("typeKey") or payload.get("errorCode") or "").strip() or None

    def _build_http_error_message(
        self,
        exc: HTTPError,
        payload: Optional[dict[str, Any]],
        payload_text: str,
    ) -> str:
        raw_message = self._extract_error_message(payload) or payload_text or str(exc)
        if exc.code == 401:
            return (
                f"Azure DevOps rejected the PAT for {self.organization_url}. "
                "Verify the token is active and has access to this organization."
            )
        if exc.code == 403:
            return (
                f"The Azure DevOps token authenticated but does not have enough access for {self.organization_url}. "
                "Grant the PAT Project/team read plus Work Items read/write scopes for the target organization."
            )
        return f"Azure DevOps request failed ({exc.code}): {raw_message}"


def normalize_azure_devops_url(raw_url: str) -> AzureDevOpsLocation:
    normalized = str(raw_url or "").strip().rstrip("/")
    if not normalized:
        raise AzureDevOpsAdapterError("Azure DevOps organization URL is required")
    if "://" not in normalized:
        normalized = f"https://{normalized}"

    parsed = urlparse(normalized)
    host = parsed.netloc.lower()
    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]

    if host == "dev.azure.com":
        if not segments:
            raise AzureDevOpsAdapterError("Azure DevOps URL must include an organization, for example https://dev.azure.com/{organization}")
        organization = segments[0]
        default_project = segments[1] if len(segments) > 1 else None
        organization_url = f"https://dev.azure.com/{quote(organization, safe='')}"
        return AzureDevOpsLocation(organization_url=organization_url, organization=organization, default_project=default_project)

    if host.endswith(".visualstudio.com"):
        organization = host[: -len(".visualstudio.com")]
        if not organization:
            raise AzureDevOpsAdapterError("Azure DevOps visualstudio.com URL must include an organization subdomain")
        default_project = segments[0] if segments else None
        organization_url = f"https://dev.azure.com/{quote(organization, safe='')}"
        return AzureDevOpsLocation(organization_url=organization_url, organization=organization, default_project=default_project)

    raise AzureDevOpsAdapterError("Azure DevOps URL must look like https://dev.azure.com/{organization} or https://dev.azure.com/{organization}/{project}")


def _normalize_required_value(value: Any, message: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise AzureDevOpsAdapterError(message)
    return normalized


def _escape_wiql_literal(value: str) -> str:
    return str(value or "").replace("'", "''")


def _coerce_optional_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _html_to_text(value: Any) -> str:
    normalized = str(value or "")
    if not normalized.strip():
        return ""
    normalized = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", normalized)
    normalized = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", normalized)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    normalized = unescape(normalized)
    normalized = re.sub(r"[ \t\r\f\v]+", " ", normalized)
    return "\n".join(part.strip() for part in normalized.splitlines() if part.strip()).strip()


def _chunked(values: Sequence[int], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]