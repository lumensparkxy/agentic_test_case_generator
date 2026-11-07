"""Project-scoped feedback: capture -> backfill -> verify, preserving immutable evidence."""

from copy import deepcopy
from hashlib import sha256
import json
from fastapi import HTTPException
from ..contracts.quality_feedback import QualityFindingInput, QualityFindingAction
from ..contracts.knowledge import KnowledgeMutation
from . import workflow_project_service as projects
from .knowledge_service import mutate_knowledge


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _project(project_id, actor):
    try:
        project = projects.get_project(project_id, actor=actor)
    except Exception as exc:
        raise projects.project_error_to_http(exc) from exc
    if project.status != "active":
        raise HTTPException(409, "Restore the project before updating findings")
    return project


def list_findings(project_id, actor):
    project = _project(project_id, actor)
    rows = projects._stream_collection(projects._get_project_doc(project_id).collection("quality_findings"))
    return {"project_revision": project.current_revision, "findings": sorted(rows, key=lambda r: r["created_at"], reverse=True)}


def _check_revision(project, revision):
    if project.current_revision != revision:
        raise HTTPException(409, "Project changed. Reload before updating feedback.")


def _commit(project, actor, request_id, record, *, updates=None, writes=(), guard=None):
    revision = project.current_revision + 1
    now = projects._utcnow()
    record["updated_at"] = now.isoformat()
    reference = projects._get_project_doc(project.project_id).collection("quality_findings").document(record["id"])
    try:
        projects._commit_project_revision(
            project.project_id,
            actor,
            project.current_revision,
            {"current_revision": revision, "updated_at": now, **(updates or {})},
            {
                "event_id": "feedback_" + digest([record["id"], request_id])[:24],
                "project_id": project.project_id,
                "event_type": "quality.feedback",
                "summary": f"Quality finding {record['status']}",
                "project_revision": revision,
                "actor_user_id": actor.sub,
                "metadata": {"finding_id": record["id"]},
                "occurred_at": now,
            },
            pending_writes=[*writes, (reference, record, False)],
            transaction_guard=guard,
        )
    except (projects.ProjectConflictError, projects.ProjectPermissionError, projects.ProjectNotFoundError) as exc:
        raise projects.project_error_to_http(exc) from exc
    return {"project_revision": revision, "finding": record}


def capture_finding(project_id, actor, payload: QualityFindingInput, request_id):
    project = _project(project_id, actor)
    try:
        snapshot = projects.get_project_stage_snapshot(project_id, payload.source_snapshot_id, actor=actor)
    except Exception as exc:
        raise projects.project_error_to_http(exc) from exc
    source_cases = project.current_snapshots.get("test_cases")
    if snapshot.stage == "test_cases":
        source_cases = snapshot
    elif snapshot.stage == "execution" and snapshot.source_snapshot_id:
        source_cases = projects.get_project_stage_snapshot(project_id, snapshot.source_snapshot_id, actor=actor)
    cases = source_cases.payload.get("test_cases", []) if source_cases else []
    if set(payload.test_case_ids) - {row["id"] for row in cases}:
        raise HTTPException(422, "Finding references unknown current test cases")
    requirements = project.current_snapshots.get("requirements")
    known_requirements = {r["id"] for r in requirements.payload.get("requirements", [])} if requirements else set()
    if set(payload.requirement_ids) - known_requirements:
        raise HTTPException(422, "Finding references unknown requirements")
    data = payload.model_dump(mode="json", exclude={"base_project_revision"})
    identity = "finding_" + digest(data)[:24]
    ref = projects._get_project_doc(project_id).collection("quality_findings").document(identity)
    existing = projects._document_to_dict(ref.get())
    if existing:
        return {"project_revision": project.current_revision, "finding": existing}
    _check_revision(project, payload.base_project_revision)
    record = {
        **data,
        "id": identity,
        "status": "open",
        "revision": 1,
        "source_stage": snapshot.stage,
        "source_project_revision": snapshot.project_revision,
        "captured_project_revision": project.current_revision + 1,
        "created_at": projects._utcnow().isoformat(),
        "source_test_case_snapshot_id": source_cases.snapshot_id if source_cases else None,
        "case_assertions_hash": digest([{k: row.get(k) for k in ("id", "steps", "expected_result")} for row in cases if row["id"] in payload.test_case_ids]),
        "affected_stages": ["context", "use_cases", "test_cases", "execution", "reports"],
        "attempts": 0,
    }
    if record["category"] in ("ambiguous_requirement", "unsupported_assumption"):
        return _commit_clarification(project, actor, request_id, record)
    return _commit(project, actor, request_id, record)


def _snapshot(project, stage, payload, finding, actor, revision, metadata):
    old = project.current_snapshots.get(stage)
    identity = "feedback_snapshot_" + digest([finding["id"], finding["revision"], stage])[:24]
    value = {
        "snapshot_id": identity,
        "project_id": project.project_id,
        "stage": stage,
        "version": (old.version if old else 0) + 1,
        "project_revision": revision,
        "operation": "quality.feedback.apply",
        "approved": False,
        "source_snapshot_id": old.snapshot_id if old else None,
        "actor_user_id": actor.sub,
        "metadata": metadata,
        "payload": payload,
        "created_at": projects._utcnow(),
    }
    state = {k: value[k] for k in ("version", "approved", "operation", "source_snapshot_id", "metadata")}
    state.update(current_snapshot_id=identity, stale=False, stale_reason=None, updated_at=value["created_at"])
    return value, state


def _commit_clarification(project, actor, request_id, record):
    baseline = project.current_snapshots.get("requirements")
    if not baseline:
        raise HTTPException(409, "Import requirements before raising a clarification")
    cases = project.current_snapshots.get("test_cases")
    affected = set(record.get("requirement_ids") or []) | {
        rid
        for case in (cases.payload.get("test_cases", []) if cases else [])
        if case["id"] in record.get("test_case_ids", [])
        for rid in case.get("linked_requirement_ids", [])
    }
    rows = deepcopy(baseline.payload.get("requirements", []))
    source_text = {row["id"]: row["text"] for row in rows if row["id"] in affected}
    if (
        record["source_snapshot_id"] not in {snapshot.snapshot_id for snapshot in project.current_snapshots.values()}
        and record.get("requirement_source_text") != source_text
    ):
        raise HTTPException(409, "The clarification source changed. Reload and record the question against the current source.")
    record["requirement_source_text"] = source_text
    if not affected or not affected <= {row["id"] for row in rows}:
        raise HTTPException(422, "Link the clarification to current requirements")
    for row in rows:
        if row["id"] in affected:
            if row.get("clarification_status") in ("open", "deferred", "excluded") and row.get("clarification_finding_id") != record["id"]:
                raise HTTPException(409, "Resolve the existing clarification for this requirement first")
            row["review_status"] = "Rejected" if record["status"] == "excluded" else "Needs Review"
            row["clarification_status"] = record["status"]
            row["clarification_finding_id"] = record["id"]
    revision = project.current_revision + 1
    value, state = _snapshot(
        project,
        "requirements",
        {**baseline.payload, "requirements": rows},
        record,
        actor,
        revision,
        {"affected_requirement_ids": sorted(affected), "clarification_status": record["status"]},
    )
    record.setdefault("original_source_snapshot_id", record["source_snapshot_id"])
    record.update(source_snapshot_id=value["snapshot_id"], requirement_ids=sorted(affected))
    states = {key: item.model_dump(mode="json") for key, item in project.stage_state.items()}
    states["requirements"] = state
    for key in ("context", "use_cases", "test_cases", "execution", "reports"):
        if key in states:
            states[key].update(stale=True, approved=False, stale_reason="Resolve or exclude the affected requirement scope before updating this artifact.")
    reference = projects._get_project_doc(project.project_id).collection("snapshots").document(value["snapshot_id"])
    return _commit(project, actor, request_id, record, updates={"stage_state": states}, writes=[(reference, value, False)])


def update_finding(project_id, identity, actor, payload: QualityFindingAction, request_id):
    project = _project(project_id, actor)
    ref = projects._get_project_doc(project_id).collection("quality_findings").document(identity)
    saved = projects._document_to_dict(ref.get())
    if not saved:
        raise HTTPException(404, "Finding not found")
    fingerprint = digest(payload.model_dump(mode="json"))
    if saved.get("last_request_id") == request_id:
        if saved.get("last_fingerprint") != fingerprint:
            raise HTTPException(409, "Request ID belongs to different feedback")
        return {"project_revision": project.current_revision, "finding": saved}
    _check_revision(project, payload.base_project_revision)
    record = deepcopy(saved)
    record.update(revision=saved["revision"] + 1, last_request_id=request_id, last_fingerprint=fingerprint)
    if payload.action in ("defer", "exclude"):
        if record["category"] not in ("ambiguous_requirement", "unsupported_assumption") or record["status"] not in ("open", "deferred"):
            raise HTTPException(409, "Only unresolved business clarifications can be deferred or excluded")
        if not payload.reason.strip():
            raise HTTPException(422, "Record why this scope is deferred or excluded")
        record.update(status="deferred" if payload.action == "defer" else "excluded", disposition_reason=payload.reason)
        return _commit_clarification(project, actor, request_id, record)
    if payload.action == "classify":
        if record["status"] != "open" or not payload.category:
            raise HTTPException(409, "Classify an open finding before applying a correction")
        record.update(category=payload.category, prerequisites=[p.model_dump(mode="json") for p in payload.prerequisites])
        if record["category"] in ("ambiguous_requirement", "unsupported_assumption"):
            return _commit_clarification(project, actor, request_id, record)
        return _commit(project, actor, request_id, record)
    if payload.action == "propose_lesson":
        if payload.lesson is None:
            raise HTTPException(422, "A scoped non-sensitive lesson is required")
        lesson = mutate_knowledge(
            actor,
            project_id,
            KnowledgeMutation(action="propose", draft=payload.lesson),
            f"finding:{identity}:{digest(payload.lesson.model_dump())[:16]}",
            source=f"Quality finding {identity}",
        )
        record["knowledge_entry_id"] = lesson.id
        return _commit(project, actor, request_id, record)
    if payload.action == "verify":
        if record["category"] == "unknown":
            raise HTTPException(409, "Classify the finding before verifying its resolution")
        if not payload.verification_snapshot_id or not record["test_case_ids"]:
            raise HTTPException(422, "Verification requires an execution snapshot and affected cases")
        run = projects.get_project_stage_snapshot(project_id, payload.verification_snapshot_id, actor=actor)
        cases = project.current_snapshots.get("test_cases")
        state = project.stage_state.get("test_cases")
        results = run.payload.get("results", [])
        passed = {r.get("source_test_case_id") for r in results if r.get("status") == "passed"}
        if (
            run.stage != "execution"
            or run.operation != "automation.execution.run"
            or not cases
            or not state
            or state.stale
            or not state.approved
            or run.project_revision <= record.get("applied_project_revision", record.get("captured_project_revision", record["source_project_revision"]))
            or run.source_snapshot_id != cases.snapshot_id
            or set(record["test_case_ids"]) - passed
            or run.metadata.get("target_environment") != record["environment"]
        ):
            raise HTTPException(409, "Verification needs passing affected cases from the current suite in the finding's environment")
        if record["category"] == "application_defect":
            assertions = [
                {k: row.get(k) for k in ("id", "steps", "expected_result")}
                for row in cases.payload.get("test_cases", [])
                if row["id"] in record["test_case_ids"]
            ]
            if digest(assertions) != record["case_assertions_hash"]:
                raise HTTPException(409, "Changed assertions cannot establish that the application defect was fixed")
        record.update(status="verified", verification_snapshot_id=run.snapshot_id)
        return _commit(project, actor, request_id, record)
    if record["category"] not in ("missing_data", "access", "environment"):
        raise HTTPException(409, "This finding needs source or script review; prerequisite backfill cannot change business assertions")
    if record["attempts"] >= 3:
        raise HTTPException(409, "Feedback correction limit reached; review the unresolved finding")
    if not payload.prerequisites or any(p.status != "configured" or not p.reference for p in payload.prerequisites):
        raise HTTPException(422, "Each prerequisite needs a non-secret reference before applying")
    if {p.name for p in payload.prerequisites} != {p["name"] for p in record["prerequisites"]}:
        raise HTTPException(422, "Resolve the exact recorded prerequisite set")
    current_cases = project.current_snapshots.get("test_cases")
    if not current_cases or current_cases.snapshot_id != record["source_test_case_snapshot_id"]:
        raise HTTPException(409, "Source suite changed; capture a finding against the current suite")
    if not record["test_case_ids"]:
        raise HTTPException(422, "Prerequisite backfill requires affected test cases")
    resolved = [p.model_dump(mode="json") for p in payload.prerequisites]
    requirement_ids = sorted(
        {rid for case in current_cases.payload.get("test_cases", []) if case["id"] in record["test_case_ids"] for rid in case.get("linked_requirement_ids", [])}
    )
    for prerequisite in resolved:
        prerequisite["requirement_ids"] = requirement_ids
    context = project.current_snapshots.get("context")
    context_payload = deepcopy(context.payload if context else {"requirements": []})
    context_payload["execution_prerequisites"] = list({p["name"]: p for p in [*context_payload.get("execution_prerequisites", []), *resolved]}.values())
    cases_payload = deepcopy(current_cases.payload)
    for case in cases_payload.get("test_cases", []):
        if case["id"] in record["test_case_ids"]:
            case["execution_prerequisites"] = list({p["name"]: p for p in [*case.get("execution_prerequisites", []), *resolved]}.values())
            case["preconditions"] = (
                (case.get("preconditions") or "")
                + "\n"
                + "\n".join(f"Requires {p['name']}: {p['description']} ({p['reference']}); verify before execution." for p in resolved)
            )
            for key in list(case):
                if key.startswith("artifact_"):
                    case.pop(key)
            case["status"] = "Draft"
    cases_payload["approved"] = False
    cases_payload["review"] = {**cases_payload.get("review", {}), "approved": False, "summary": "Prerequisites changed; re-review required."}
    revision = project.current_revision + 1
    states = {stage: value.model_dump(mode="json") for stage, value in project.stage_state.items()}
    for stage in projects.DOWNSTREAM_STAGES["context"]:
        if stage in states:
            states[stage].update(stale=True, approved=False, stale_reason=f"Quality finding {identity} changed prerequisites")
    context_snapshot, states["context"] = _snapshot(project, "context", context_payload, record, actor, revision, {"finding_id": identity})
    case_snapshot, states["test_cases"] = _snapshot(
        project,
        "test_cases",
        cases_payload,
        record,
        actor,
        revision,
        {**current_cases.metadata, "finding_id": identity, "source_context_snapshot_id": context_snapshot["snapshot_id"]},
    )
    # Existing scenario design and scripts still need review/regeneration; cases are drafts.
    states["test_cases"]["stale"] = True
    states["test_cases"]["stale_reason"] = "Review upstream scenarios and regenerate with corrected prerequisites"
    writes = [(projects._get_project_doc(project_id).collection("snapshots").document(s["snapshot_id"]), s, False) for s in (context_snapshot, case_snapshot)]
    record.update(
        status="applied_pending_verification",
        applied_project_revision=revision,
        attempts=record["attempts"] + 1,
        prerequisites=resolved,
        corrected_context_snapshot_id=context_snapshot["snapshot_id"],
        corrected_test_case_snapshot_id=case_snapshot["snapshot_id"],
    )
    return _commit(project, actor, request_id, record, updates={"stage_state": states}, writes=writes)


def execution_finding_writes(project_id, snapshot):
    """Persist conservative findings in the SAME transaction as execution evidence."""
    if snapshot["operation"] not in ("automation.execution.preview", "automation.execution.run"):
        return []
    source = projects._snapshot_for(project_id, snapshot.get("source_snapshot_id")) if snapshot.get("source_snapshot_id") else None
    cases = source.payload.get("test_cases", []) if source else []
    by_id = {row["id"]: row for row in cases}
    payload = snapshot["payload"]
    observations = []
    for result in payload.get("results", []):
        if result.get("status") in ("failed", "invalid", "skipped"):
            observations.append((result.get("source_test_case_id"), "Execution did not pass; classify using the recorded run evidence."))
    for category in ("manual", "unsupported", "invalid"):
        for candidate in payload.get("candidates", {}).get(category, []):
            observations.append((candidate.get("source_test_case_id"), f"Execution preview classified this case as {category}."))
    for blocker in (payload.get("readiness") or {}).get("blockers", []):
        observations.append((None, f"Execution blocked: {blocker['code']}. {blocker['message']}"))
    writes = []
    for case_id, summary in observations:
        case = by_id.get(case_id, {})
        prerequisites = case.get("execution_prerequisites", [])
        identity = "finding_" + digest([snapshot["snapshot_id"], case_id, summary])[:24]
        record = {
            "id": identity,
            "category": "missing_data" if any(p.get("status") == "missing" for p in prerequisites) else "unknown",
            "summary": summary,
            "expected_behavior": "Resolve the prerequisite or classify the failure without weakening source assertions.",
            "source_snapshot_id": snapshot["snapshot_id"],
            "source_stage": "execution",
            "source_project_revision": snapshot["project_revision"],
            "source_test_case_snapshot_id": source.snapshot_id if source else None,
            "test_case_ids": [case_id] if case_id in by_id else [],
            "requirement_ids": case.get("linked_requirement_ids", []),
            "environment": payload.get("target_environment") or "default",
            "prerequisites": prerequisites,
            "status": "open",
            "revision": 1,
            "attempts": 0,
            "created_at": snapshot["created_at"].isoformat(),
            "updated_at": snapshot["created_at"].isoformat(),
            "affected_stages": ["context", "use_cases", "test_cases", "execution", "reports"],
            "case_assertions_hash": digest([{k: case.get(k) for k in ("id", "steps", "expected_result")}]) if case else digest([]),
        }
        ref = projects._get_project_doc(project_id).collection("quality_findings").document(identity)
        writes.append((ref, record, False))
    return writes
