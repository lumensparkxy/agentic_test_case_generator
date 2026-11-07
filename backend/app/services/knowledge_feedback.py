"""Reviewer feedback is durable project data, never automatic behavioural learning."""

from fastapi import HTTPException
from ..contracts.knowledge import KnowledgeDraft
from .guidance_service import public_manifest
from . import workflow_project_service as projects
from .quality_feedback import digest


def record_project_feedback(actor, project_id, stage, text, request_id):
    project = projects.get_project(project_id, actor=actor)
    source = project.current_snapshots.get("reports" if stage == "automation" else stage)
    if source is None:
        raise HTTPException(409, "The saved source artifact is unavailable")
    identity = "feedback_" + digest([project_id, stage, request_id])[:24]
    now = projects._utcnow().isoformat()
    requirements = project.current_snapshots.get("requirements")
    record = {
        "id": identity,
        "status": "open",
        "revision": 1,
        "category": "unknown",
        "summary": text,
        "expected_behavior": "Confirm the source expectation before accepting a correction.",
        "requirement_ids": [r["id"] for r in requirements.payload.get("requirements", [])] if requirements else [],
        "test_case_ids": [],
        "source_snapshot_id": source.snapshot_id,
        "source_stage": source.stage,
        "source_project_revision": source.project_revision,
        "captured_project_revision": project.current_revision,
        "source_request_id": request_id,
        "environment": "default",
        "prerequisites": [],
        "attempts": 0,
        "created_at": now,
        "updated_at": now,
    }
    client = projects.get_required_firestore_client(unavailable_message="Project feedback storage unavailable")
    parent = client.collection(projects.QA_PROJECTS_COLLECTION).document(project_id)
    ref = parent.collection("quality_findings").document(identity)

    @projects.transactional
    def save(transaction):
        current = projects._document_to_dict(parent.get(transaction=transaction))
        if not current:
            raise HTTPException(404, "Project not found")
        projects._require_owner(current, actor)
        existing = projects._document_to_dict(ref.get(transaction=transaction))
        if existing:
            if existing["summary"] != text:
                raise HTTPException(409, "Feedback request already contains different text")
            return existing
        transaction.create(ref, record)
        return record

    return save(client.transaction())


def suggest_feedback(actor, project_id, stage, feedback, source_request_id):
    if not project_id or not feedback or not feedback.strip():
        return None
    try:
        draft = KnowledgeDraft(text=feedback.strip(), stages=[stage])
    except ValueError:
        return {"status": "manual_entry_required", "message": "Record concise, non-sensitive project feedback in Context."}
    retry = {"stage": stage, "feedback": draft.text, "source_request_id": source_request_id}
    try:
        result = record_project_feedback(actor, project_id, stage, draft.text, source_request_id)
        return {"status": "suggested", "entry_id": result["id"], "message": "Project feedback saved for source review. Agent behaviour was not changed."}
    except Exception:
        return {"status": "failed", "message": "Your work was saved, but recording project feedback failed.", "retry": retry}


def finish_generation(response, actor, project_id, stage, feedback, request_id):
    if hasattr(response, "guidance"):
        response.guidance = public_manifest()
    if hasattr(response, "knowledge_suggestion"):
        response.knowledge_suggestion = suggest_feedback(actor, project_id, stage, feedback, request_id)
    return response


def retry_feedback(actor, project_id, stage, feedback, source_request_id):
    project = projects.get_project(project_id, actor=actor)
    snapshots = list(project.current_snapshots.values())
    # Source artifacts, not optional memory, establish the completed operation.
    if not any(snapshot.request_id == source_request_id for snapshot in snapshots):
        raise HTTPException(409, "The completed source operation could not be verified. Reload the project.")
    return suggest_feedback(actor, project_id, stage, feedback, source_request_id)
