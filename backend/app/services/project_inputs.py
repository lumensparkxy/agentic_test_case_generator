"""Mandatory, immutable project inputs. Never controlled by memory feature flags."""

from hashlib import sha256
from pathlib import Path
from fastapi import HTTPException
from ..contracts.project_sync import ProjectInputManifest


def manifest_from_payload(payload):
    states = payload.get("stage_state") or {}
    return ProjectInputManifest(
        project_id=payload["project_id"],
        revision=payload.get("input_revision", 0),
        requirements_snapshot_id=(states.get("requirements") or {}).get("current_snapshot_id"),
        context_snapshot_id=(states.get("context") or {}).get("current_snapshot_id"),
        decisions=payload.get("project_decisions") or (),
    )


def load_inputs(project_id, actor):
    from . import workflow_project_service as projects

    try:
        payload = projects._get_project_payload(project_id)
        projects._require_owner(payload, actor)
    except Exception as exc:
        raise projects.project_error_to_http(exc) from exc
    return manifest_from_payload(payload)


def behavioural_version():
    """Pin deployed methods and prompts; publishing is a separate deployment."""
    root = Path(__file__).resolve().parents[1]
    digest = sha256()
    files = sorted((root / "agents").rglob("*.py")) + sorted((root / "skills").rglob("*"))
    for path in files:
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def check_run_inputs(payload):
    from .guidance_service import current_guidance

    guidance = current_guidance()
    if not guidance:
        return
    if guidance.project_inputs and guidance.project_inputs != manifest_from_payload(payload):
        raise HTTPException(409, "Project facts changed during generation. Old results were not published; refresh the current drafts.")
    if guidance.behaviour_version and guidance.behaviour_version != behavioural_version():
        raise HTTPException(409, "Agent behaviour changed during this run. Start a new run against the published version.")
