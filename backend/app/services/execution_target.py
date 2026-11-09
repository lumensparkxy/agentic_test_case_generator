"""Bind generated browser URLs to the selected project execution origin."""

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import HttpUrl

from ..contracts.projects import QaProjectDetail


def saved_execution_source_url(project: QaProjectDetail | None) -> str | None:
    if not project:
        return None
    context = project.current_snapshots.get("context")
    suite = project.current_snapshots.get("test_cases")
    context_state = project.stage_state.get("context")
    suite_state = project.stage_state.get("test_cases")
    if (
        not context
        or not suite
        or not context_state
        or not suite_state
        or context.project_id != project.project_id
        or suite.project_id != project.project_id
        or context_state.current_snapshot_id != context.snapshot_id
        or suite_state.current_snapshot_id != suite.snapshot_id
        or not context_state.approved
        or context_state.stale
        or context.project_revision > suite.project_revision
    ):
        return None
    source_id = suite.metadata.get("source_context_snapshot_id")
    if source_id and source_id != context.snapshot_id:
        return None
    app_link = context.payload.get("app_link")
    return app_link if isinstance(app_link, str) else None


def _check_url_text(value: str) -> None:
    if "\\" in value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("The browser URL contains an invalid separator.")


def _http_url(value: str) -> str:
    _check_url_text(value)
    url = HttpUrl(value)
    if url.username is not None or url.password is not None:
        raise ValueError("Inline URL credentials are unsupported.")
    return str(url)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def bind_execution_url(value: str, *, base_url: str, source_app_url: str | None) -> str:
    _check_url_text(value)
    target = _http_url(base_url)
    target_origin = _origin(target)
    reference = urlsplit(value)
    if reference.scheme:
        if not re.match(r"^https?://", value, re.IGNORECASE):
            raise ValueError("A browser URL must use HTTP or HTTPS.")
        authored = _http_url(value)
    else:
        parts = urlsplit(target)
        directory_base = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/", "", ""))
        authored = _http_url(urljoin(target if value.startswith(("/", "?", "#")) else directory_base, value))

    authored_origin = _origin(authored)
    if authored_origin == target_origin:
        return authored
    if source_app_url and authored_origin == _origin(_http_url(source_app_url)):
        return target_origin + authored.removeprefix(authored_origin)
    raise ValueError("The browser URL is outside the selected target and saved application origins.")
