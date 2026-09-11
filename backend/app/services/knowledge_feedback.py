"""Suggestions are inactive and happen only after the underlying operation succeeds."""

from fastapi import HTTPException
from ..contracts.knowledge import KnowledgeDraft, KnowledgeMutation
from .guidance_service import enabled, public_manifest
from .knowledge_service import mutate_knowledge, project_for


def suggest_feedback(actor, project_id, stage, feedback, source_request_id):
    if not project_id or not feedback or not feedback.strip() or not enabled("memory", stage):
        return None
    try:
        # Conservatively retain explicit reviewer wording; do not invent/generalize a lesson.
        draft = KnowledgeDraft(text=feedback.strip(), stages=[stage])
    except ValueError:
        return {
            "status": "manual_entry_required",
            "message": "Feedback could not be safely converted. Add a concise, non-sensitive guidance proposal in Context.",
        }
    retry = {"stage": stage, "feedback": draft.text, "source_request_id": source_request_id}
    try:
        result = mutate_knowledge(
            actor,
            project_id,
            KnowledgeMutation(action="propose", draft=draft),
            f"feedback:{stage}:{source_request_id}",
            source=f"Review feedback from {source_request_id}",
        )
        return {"status": "suggested", "entry_id": result.id, "message": "Knowledge suggestion awaits separate approval in Context."}
    except Exception:
        return {"status": "failed", "message": "Your work was saved, but the knowledge suggestion failed.", "retry": retry}


def finish_generation(response, actor, project_id, stage, feedback, request_id):
    if hasattr(response, "guidance"):
        response.guidance = public_manifest()
    if hasattr(response, "knowledge_suggestion"):
        response.knowledge_suggestion = suggest_feedback(actor, project_id, stage, feedback, request_id)
    return response


def retry_feedback(actor, project_id, stage, feedback, source_request_id):
    project = project_for(project_id, actor)
    if not any(event.request_id == source_request_id for event in project.timeline):
        raise HTTPException(409, "The completed source operation could not be verified. Reload the project.")
    return suggest_feedback(actor, project_id, stage, feedback, source_request_id)
