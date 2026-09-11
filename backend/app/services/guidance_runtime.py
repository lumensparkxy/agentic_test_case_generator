"""Resolve before generation and freeze references durably for request retries."""

from hashlib import sha256
import json
import os
from fastapi import HTTPException
from google.cloud.firestore_v1 import transactional
from ..config import DEFAULT_MODEL_NAME
from ..contracts.guidance import GuidanceManifest, MemoryGuidance
from .guidance_service import build_manifest, enabled, public_manifest, load_skill, stages_for_run, run_enabled
from .knowledge_service import resolve_memories, knowledge_version
from .firestore_repository import get_required_firestore_client


def unavailable(exc):
    if isinstance(exc, HTTPException) and exc.status_code in (401, 403, 404, 409, 422):
        return exc
    return HTTPException(
        503, {"code": "knowledge_unavailable", "message": "Remembered guidance could not be loaded. Retry or explicitly run without it.", "can_bypass": True}
    )


def restore_manifest(value, actor, project_id):
    data = dict(value)
    skills = []
    for recorded in value["skills"]:
        skill = load_skill(recorded["stage"])
        if skill.content_hash != recorded["content_hash"]:
            raise HTTPException(409, "Skill version changed. Start a new run instead of changing a saved run's guidance.")
        skills.append(skill)
    memories = []
    for recorded in value["memories"]:
        content = knowledge_version(actor, project_id, recorded["id"], recorded["revision"])
        if content.get("unavailable"):
            raise RuntimeError("A saved memory was deleted")
        if sha256(content["text"].encode()).hexdigest() != recorded["content_hash"]:
            raise RuntimeError("Knowledge version integrity mismatch")
        memories.append(MemoryGuidance(**{k: v for k, v in recorded.items() if k != "content_hash"}, text=content["text"]))
    data.update(skills=tuple(skills), memories=tuple(memories))
    return GuidanceManifest.model_validate(data)


def prepare_run(stage, actor, project_id, request_id, *, requirement_ids=(), memory_bypass=False, input_fingerprint=""):
    model = os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME)
    # Disabled and explicit-bypass paths never depend on knowledge storage.
    if not run_enabled("memory", stage) or memory_bypass or not project_id:
        return build_manifest(stage, model, project_id=project_id, memory_bypass=memory_bypass)
    try:
        client = get_required_firestore_client(unavailable_message="Guidance provenance storage unavailable")
        key = sha256(json.dumps([actor.sub, project_id, request_id, stage]).encode()).hexdigest()
        doc = client.collection("agent_guidance_runs").document(key)
        saved = doc.get().to_dict()
        if saved:
            if saved["input_fingerprint"] != input_fingerprint:
                raise HTTPException(409, "Request ID already belongs to different generation inputs")
            return restore_manifest(saved["manifest"], actor, project_id)
        stages = tuple(s for s in stages_for_run(stage) if enabled("memory", s))
        memories, omitted, revision = resolve_memories(actor, project_id, stages, requirement_ids)
        manifest = build_manifest(stage, model, project_id=project_id, memories=memories, omitted=omitted, knowledge_revision=revision)
        value = {"manifest": public_manifest(manifest), "input_fingerprint": input_fingerprint, "owner_user_id": actor.sub, "project_id": project_id}

        @transactional
        def save(transaction):
            existing = doc.get(transaction=transaction).to_dict()
            if existing:
                if existing["input_fingerprint"] != input_fingerprint:
                    raise HTTPException(409, "Request ID already belongs to different generation inputs")
                return existing
            transaction.set(doc, value)
            return value

        saved = save(client.transaction())
        return manifest if saved == value else restore_manifest(saved["manifest"], actor, project_id)
    except Exception as exc:
        raise unavailable(exc) from exc
