import json
import re
from html import unescape
from typing import Callable, List
from urllib.parse import urljoin

from ..models import ArtifactSource, EnrichInput, GroundedApiSurface, GroundedContext, GroundedUIElement, GroundedWorkflow
from .artifact_fetcher import fetch_artifact

FetchArtifactFn = Callable[[str], dict]


def _strip_html(text: str) -> str:
    normalized = re.sub(r"<[^>]+>", " ", unescape(text or ""))
    normalized = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _html_attr(attrs: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*=\s*(['\"])(.*?)\1", attrs, re.IGNORECASE | re.DOTALL)
    if match:
        return unescape(match.group(2)).strip()

    unquoted = re.search(rf"\b{name}\s*=\s*([^\s>]+)", attrs, re.IGNORECASE)
    return unescape(unquoted.group(1)).strip() if unquoted else ""


def _normalize_link_href(href: str, base_url: str | None) -> str:
    normalized = href.strip()
    if not normalized or normalized.startswith("#"):
        return ""
    if re.match(r"^(?:javascript|mailto|tel):", normalized, re.IGNORECASE):
        return ""
    if base_url:
        return urljoin(base_url, normalized)
    return normalized


def extract_ui_elements_from_html(
    source_id: str,
    html_text: str,
    *,
    base_url: str | None = None,
) -> List[GroundedUIElement]:
    elements: List[GroundedUIElement] = []

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = _strip_html(title_match.group(1))
        if title:
            elements.append(
                GroundedUIElement(
                    id=f"{source_id}-UI-001",
                    source_id=source_id,
                    name=title,
                    element_type="Page",
                    description=f"Page title discovered from artifact: {title}",
                )
            )

    for index, heading in enumerate(re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", html_text, re.IGNORECASE | re.DOTALL), start=1):
        label = _strip_html(heading)
        if not label:
            continue
        elements.append(
            GroundedUIElement(
                    id=f"{source_id}-UI-H-{index:02d}",
                    source_id=source_id,
                    name=label,
                    element_type="Heading",
                    description=f"Heading extracted from artifact: {label}",
                )
            )
        if index >= 4:
            break

    for index, link in enumerate(
        re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<label>.*?)</a>", html_text, re.IGNORECASE | re.DOTALL),
        start=1,
    ):
        label = _strip_html(link.group("label"))
        href = _normalize_link_href(_html_attr(link.group("attrs"), "href"), base_url)
        if not label and not href:
            continue
        name = label or href
        description = f"Navigation link extracted from artifact: {name}"
        if href:
            description = f"{description} -> {href}"
        elements.append(
            GroundedUIElement(
                id=f"{source_id}-UI-L-{index:02d}",
                source_id=source_id,
                name=name,
                element_type="Navigation",
                description=description,
                href=href or None,
            )
        )
        if index >= 12:
            break

    for index, button in enumerate(re.findall(r"<button[^>]*>(.*?)</button>", html_text, re.IGNORECASE | re.DOTALL), start=1):
        label = _strip_html(button)
        if not label:
            continue
        elements.append(
            GroundedUIElement(
                id=f"{source_id}-UI-B-{index:02d}",
                source_id=source_id,
                name=label,
                element_type="Button",
                description=f"Button label extracted from artifact: {label}",
            )
        )
        if index >= 4:
            break

    for index, field in enumerate(
        re.findall(r"<(?:input|textarea|select)[^>]*(?:name|id|placeholder)=['\"]?([^'\"\s>]+)", html_text, re.IGNORECASE),
        start=1,
    ):
        label = _strip_html(field)
        if not label:
            continue
        elements.append(
            GroundedUIElement(
                id=f"{source_id}-UI-F-{index:02d}",
                source_id=source_id,
                name=label,
                element_type="Field",
                description=f"Field identifier extracted from artifact: {label}",
            )
        )
        if index >= 6:
            break

    return elements


def extract_api_surfaces_from_json(source_id: str, raw_text: str) -> List[GroundedApiSurface]:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return []

    paths = payload.get("paths") if isinstance(payload, dict) else None
    if not isinstance(paths, dict):
        return []

    api_surfaces: List[GroundedApiSurface] = []
    for path_index, (path, operations) in enumerate(paths.items(), start=1):
        if not isinstance(operations, dict):
            continue
        for method, details in operations.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            description = ""
            if isinstance(details, dict):
                description = str(details.get("summary") or details.get("description") or "").strip()
            api_surfaces.append(
                GroundedApiSurface(
                    id=f"{source_id}-API-{path_index:02d}-{method.upper()}",
                    source_id=source_id,
                    name=f"{method.upper()} {path}",
                    description=description or f"Endpoint discovered from artifact: {method.upper()} {path}",
                    method=method.upper(),
                    path=path,
                    auth_required=None,
                )
            )
            if len(api_surfaces) >= 12:
                return api_surfaces

    return api_surfaces


def infer_workflows_from_requirements(payload: EnrichInput) -> List[GroundedWorkflow]:
    requirement_text = " ".join(requirement.text for requirement in payload.requirements).lower()
    states = [state for state in ["Draft", "Submitted", "Approved", "Rejected", "Locked"] if state.lower() in requirement_text]
    actors = [actor for actor in ["User", "Employee", "Manager", "Finance Administrator"] if actor.lower() in requirement_text]
    transitions: List[str] = []

    if "draft" in requirement_text and "submit" in requirement_text:
        transitions.append("Draft → Submitted")
    if "approve" in requirement_text and "submitted" in requirement_text:
        transitions.append("Submitted → Approved")
    if "reject" in requirement_text:
        transitions.append("Submitted → Rejected")
    if "lock" in requirement_text:
        transitions.append("Active → Locked")

    if not states and not transitions:
        return []

    return [
        GroundedWorkflow(
            id="WF-001",
            name="Inferred workflow",
            description="Workflow inferred from requirement wording and provided context.",
            actors=actors,
            states=states,
            transitions=transitions,
        )
    ]


def build_grounded_context(payload: EnrichInput, fetcher: FetchArtifactFn = fetch_artifact) -> GroundedContext:
    artifact_sources: List[ArtifactSource] = []
    ui_elements: List[GroundedUIElement] = []
    api_surfaces: List[GroundedApiSurface] = []
    workflows = infer_workflows_from_requirements(payload)

    def register_source(source: ArtifactSource) -> None:
        artifact_sources.append(source)

    if payload.app_link:
        register_source(ArtifactSource(id="ART-APP-01", source_type="app", label="Application link", url=payload.app_link))
    if payload.prototype_link:
        register_source(ArtifactSource(id="ART-PROTO-01", source_type="prototype", label="Prototype link", url=payload.prototype_link))
    for index, link in enumerate(payload.diagram_links or [], start=1):
        register_source(ArtifactSource(id=f"ART-DIAG-{index:02d}", source_type="diagram", label=f"Diagram {index}", url=link))
    for index, link in enumerate(payload.image_links or [], start=1):
        register_source(ArtifactSource(id=f"ART-IMG-{index:02d}", source_type="image", label=f"Image {index}", url=link))
    if payload.notes:
        register_source(ArtifactSource(id="ART-NOTE-01", source_type="note", label="Context notes", notes=payload.notes))

    analyzed_count = 0
    unavailable_count = 0
    for index, source in enumerate(list(artifact_sources)):
        if not source.url:
            continue
        result = fetcher(str(source.url))
        artifact_sources[index] = source.model_copy(update={
            "status": result.get("status") or source.status,
            "notes": result.get("error") or result.get("content_type") or source.notes,
        })
        if result.get("status") == "Analyzed":
            analyzed_count += 1
        elif result.get("status") == "Unavailable":
            unavailable_count += 1

        if result.get("status") != "Analyzed" or not result.get("text"):
            continue

        text = str(result.get("text") or "")
        content_type = str(result.get("content_type") or "")
        if "html" in content_type:
            ui_elements.extend(extract_ui_elements_from_html(source.id, text, base_url=str(source.url) if source.url else None))
        if "json" in content_type:
            api_surfaces.extend(extract_api_surfaces_from_json(source.id, text))

    summary = (
        f"Registered {len(artifact_sources)} artifact reference(s); analyzed {analyzed_count}, unavailable {unavailable_count}. "
        f"Extracted {len(ui_elements)} UI element(s), {len(api_surfaces)} API surface(s), and {len(workflows)} workflow(s)."
    )

    return GroundedContext(
        artifact_sources=artifact_sources,
        ui_elements=ui_elements,
        api_surfaces=api_surfaces,
        workflows=workflows,
        summary=summary,
    )
