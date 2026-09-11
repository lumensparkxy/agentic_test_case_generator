from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json

from fastapi import HTTPException
from ..contracts.knowledge import KnowledgeMutation, KnowledgeDraft, KnowledgeEntry, KnowledgeCollection
from ..contracts.guidance import MemoryGuidance, OmittedGuidance
from .knowledge_repository import FirestoreKnowledgeRepository
from .guidance_service import STAGES, enabled, skill_catalog
from .workflow_project_service import get_project, project_error_to_http


def repository():
    return FirestoreKnowledgeRepository()


def project_for(project_id, actor):
    if not project_id:
        return None
    try:
        return get_project(project_id, actor=actor)
    except HTTPException:
        raise
    except Exception as exc:
        raise project_error_to_http(exc) from exc


def requirement_hashes(project):
    if project is None:
        return {}
    snapshot = project.current_snapshots.get("requirements")
    rows = snapshot.payload.get("requirements", []) if snapshot else []
    return {str(r.get("id")): sha256(json.dumps(r.get("text", ""), sort_keys=True).encode()).hexdigest() for r in rows}


def version(entry, number):
    return next((v for v in entry.get("versions", []) if v["revision"] == number), None)


def current_version(entry):
    return version(entry, entry.get("active_revision"))


def needs_confirmation(value, hashes):
    return bool(value and any(hashes.get(key) != old for key, old in value.get("requirement_hashes", {}).items()))


def public_entry(entry, state, project_id=None, hashes=None):
    active = current_version(entry)
    status = entry["status"]
    if status == "active" and needs_confirmation(active, hashes or {}):
        status = "needs_confirmation"
    return KnowledgeEntry(
        id=entry["id"],
        project_id=entry.get("project_id"),
        scope="project" if entry.get("project_id") else "personal",
        revision=entry["revision"],
        status=status,
        active=active,
        pending=version(entry, entry.get("pending_revision")),
        selected=entry["id"] in state["selections"].get(project_id, []),
        selected_by_projects=[p for p, ids in state["selections"].items() if entry["id"] in ids],
    )


def knowledge_signature(entries):
    canonical = [(e.id, e.status, e.active and e.active["revision"], e.selected) for e in entries]
    return sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def list_knowledge(actor, project_id=None):
    project = project_for(project_id, actor)
    state = repository().read(actor.sub)
    selected = state["selections"].get(project_id, [])
    entries = [
        public_entry(e, state, project_id, requirement_hashes(project))
        for e in state["entries"].values()
        if (e.get("project_id") == project_id or (project_id and e["id"] in selected and not e.get("project_id"))) and e["status"] != "deleted"
    ]
    entries.sort(key=lambda e: e.id)
    return KnowledgeCollection(
        entries=entries,
        revision=knowledge_signature(entries),
        skills=skill_catalog(),
        features={s: {"skills": enabled("skills", s), "memory": enabled("memory", s)} for s in STAGES},
    )


def mutate_knowledge(actor, project_id, mutation: KnowledgeMutation, request_id: str, *, source="Manual guidance"):
    project = project_for(project_id, actor)
    hashes = requirement_hashes(project)
    values = mutation.model_dump()
    fingerprint = sha256(json.dumps([project_id, values, source], sort_keys=True).encode()).hexdigest()
    generated_id = "knowledge_" + sha256(f"{actor.sub}:{project_id}:{request_id}".encode()).hexdigest()[:24]
    now = datetime.now(timezone.utc).isoformat()

    def content(draft, revision, source_text):
        if draft is None:
            raise HTTPException(422, "Guidance text and stages are required")
        data = draft.model_dump()
        data["stages"] = sorted(set(data["stages"]))
        data["requirement_ids"] = sorted(set(data["requirement_ids"]))
        if not project_id and (data["kind"] == "business_fact" or data["requirement_ids"]):
            raise HTTPException(422, "Project-specific facts cannot enter the personal library")
        if data["kind"] == "business_fact" and not data["requirement_ids"]:
            raise HTTPException(422, "Link business facts to their source requirements")
        missing = set(data["requirement_ids"]) - hashes.keys()
        if missing:
            raise HTTPException(422, "Linked requirements must exist in the current project")
        return {**data, "revision": revision, "source": source_text, "created_at": now, "requirement_hashes": {r: hashes[r] for r in data["requirement_ids"]}}

    def apply(state):
        action, entry_id = mutation.action, mutation.entry_id
        entries = state["entries"]
        if action == "propose":
            if len([e for e in entries.values() if e["status"] != "deleted"]) >= 200:
                raise HTTPException(422, "Knowledge entry limit reached")
            entries[generated_id] = {
                "id": generated_id,
                "project_id": project_id,
                "revision": 1,
                "status": "suggested",
                "active_revision": None,
                "pending_revision": 1,
                "versions": [content(mutation.draft, 1, source)],
            }
            return generated_id
        entry = entries.get(entry_id)
        if not entry or entry["status"] == "deleted":
            raise HTTPException(404, "Knowledge entry not found")
        if action in ("select", "unselect"):
            if not project_id or entry.get("project_id"):
                raise HTTPException(422, "Only personal entries can be selected for a project")
        elif entry.get("project_id") != project_id:
            raise HTTPException(404, "Knowledge entry not found in this scope")
        if mutation.base_revision != entry["revision"]:
            raise HTTPException(409, "Knowledge changed; reload before saving")
        if action in ("select", "unselect"):
            if action == "select" and entry["status"] != "active":
                raise HTTPException(422, "Approve personal guidance before selecting it")
            ids = set(state["selections"].get(project_id, []))
            if action == "select":
                ids.add(entry_id)
            else:
                ids.discard(entry_id)
            state["selections"][project_id] = sorted(ids)
            return entry_id
        next_revision = entry["revision"] + 1
        if action == "revise":
            entry["versions"].append(content(mutation.draft, next_revision, source))
            entry["pending_revision"] = next_revision
            if not entry["active_revision"]:
                entry["status"] = "suggested"
        elif action == "approve":
            pending = version(entry, entry.get("pending_revision"))
            if pending is None:
                # Re-confirming changed linked requirements creates a new version.
                old = current_version(entry)
                if not old or not needs_confirmation(old, hashes):
                    raise HTTPException(422, "No proposed revision to approve")
                pending = content(KnowledgeDraft(**{k: old[k] for k in ("text", "stages", "kind", "requirement_ids")}), next_revision, old["source"])
                entry["versions"].append(pending)
            if needs_confirmation(pending, hashes):
                raise HTTPException(409, "Source requirements changed; edit the proposal before approving")
            pending.update(approved_at=now, approved_by=actor.sub)
            entry.update(status="active", active_revision=pending["revision"], pending_revision=None)
        elif action == "dismiss":
            entry["pending_revision"] = None
            entry["status"] = "active" if entry["active_revision"] else "dismissed"
        elif action == "retire":
            entry.update(status="retired", active_revision=None, pending_revision=None)
        elif action == "delete":
            entry.update(status="deleted", active_revision=None, pending_revision=None, versions=[])
            for ids in state["selections"].values():
                if entry_id in ids:
                    ids.remove(entry_id)
        elif action == "promote":
            original = current_version(entry)
            if not project_id or entry["status"] != "active" or original is None:
                raise HTTPException(422, "Only active project guidance can be promoted")
            if mutation.draft is None or mutation.draft.kind == "business_fact" or mutation.draft.requirement_ids:
                raise HTTPException(422, "Review generalized wording without project-specific facts or requirement links")
            if original["kind"] == "business_fact":
                raise HTTPException(422, "Business facts must remain project-scoped")
            promoted = content(mutation.draft, 1, f"Promoted from {entry_id}")
            entries[generated_id] = {
                "id": generated_id,
                "project_id": None,
                "revision": 1,
                "status": "suggested",
                "active_revision": None,
                "pending_revision": 1,
                "versions": [promoted],
            }
            return generated_id
        entry["revision"] = next_revision
        return entry_id

    state, entry_id = repository().mutate(actor.sub, request_id, fingerprint, apply, project_id)
    entry = state["entries"].get(entry_id)
    return public_entry(entry, state, project_id, hashes)


def resolve_memories(actor, project_id, stage, requirement_ids=None):
    collection = list_knowledge(actor, project_id)
    selected, omitted = [], []
    requested = set(requirement_ids or ())
    for entry in collection.entries:
        value = entry.active
        reason = None
        if entry.status != "active" or value is None:
            reason = entry.status
        elif stage not in value["stages"]:
            reason = "different_stage"
        elif value["requirement_ids"] and not requested.intersection(value["requirement_ids"]):
            reason = "different_requirement"
        if reason:
            omitted.append(OmittedGuidance(id=entry.id, revision=entry.revision, reason=reason))
        else:
            selected.append(
                MemoryGuidance(
                    id=entry.id,
                    revision=value["revision"],
                    scope=entry.scope,
                    text=value["text"],
                    source=value["source"],
                    requirement_ids=tuple(value["requirement_ids"]),
                    reason="linked_requirement" if value["requirement_ids"] else "selected_personal" if entry.scope == "personal" else "project_stage",
                )
            )
    selected.sort(key=lambda e: (e.scope != "project", not bool(e.requirement_ids), e.id))
    return selected, omitted, collection.revision


def knowledge_version(actor, project_id, entry_id, revision):
    project_for(project_id, actor)
    state = repository().read(actor.sub)
    entry = state["entries"].get(entry_id)
    if not entry or entry.get("project_id") not in (None, project_id):
        raise HTTPException(404, "Knowledge version unavailable")
    value = version(entry, revision)
    if value is None or entry["status"] == "deleted":
        return {"id": entry_id, "revision": revision, "unavailable": True}
    return {"id": entry_id, **value}
