"""Fence project browser actions with a durable single-use request identity."""

from datetime import timedelta
from hashlib import sha256

from fastapi import HTTPException
from google.api_core.exceptions import AlreadyExists

from ..contracts.execution import ExecutionRequestReceipt, ExecutionRunInput
from ..models import AuthUser
from . import workflow_project_service as projects


def _request_doc(project_doc, request_id):
    return project_doc.collection("execution_requests").document(projects._stable_document_id("request", request_id))


def read_execution_request(project_id: str, request_id: str, *, actor: AuthUser) -> dict | None:
    projects._require_owner(projects._get_project_payload(project_id), actor)
    ref = _request_doc(projects._get_project_doc(project_id), request_id)
    return projects._document_to_dict(ref.get())


def public_execution_request(record: dict) -> ExecutionRequestReceipt:
    receipt = ExecutionRequestReceipt.model_validate(record)
    if receipt.status == "pending" and projects._utcnow() - receipt.started_at >= timedelta(hours=1):
        # A missing result is still ambiguous. This changes presentation only.
        receipt.status = "unconfirmed"
    return receipt


def execution_request_fingerprint(payload: ExecutionRunInput) -> str:
    return sha256(payload.model_dump_json().encode()).hexdigest()


def reject_execution_replay(record: dict | None, fingerprint: str) -> None:
    if not record:
        return
    if record["fingerprint"] != fingerprint:
        raise HTTPException(409, detail={"code": "execution_request_conflict", "message": "This request identity belongs to different execution inputs."})
    raise HTTPException(
        409,
        detail={
            "code": "execution_request_exists",
            "message": "This execution request was already received. Check its saved status.",
            "receipt": public_execution_request(record).model_dump(mode="json"),
        },
    )


def claim_execution_request(
    *,
    project_id: str,
    request_id: str,
    actor: AuthUser,
    revision: int,
    source_snapshot_id: str,
    target_environment: str,
    fingerprint: str,
):
    client = projects.get_required_firestore_client(unavailable_message="Execution request storage is unavailable")
    project_doc = client.collection(projects.QA_PROJECTS_COLLECTION).document(project_id)
    ref = _request_doc(project_doc, request_id)

    @projects.transactional
    def claim(transaction):
        project = projects._document_to_dict(project_doc.get(transaction=transaction))
        if project is None:
            raise projects.ProjectNotFoundError(project_id)
        projects._require_owner(project, actor)
        projects._check_revision(project, revision)
        state = (project.get("stage_state") or {}).get("test_cases") or {}
        if project.get("status") != "active" or state.get("current_snapshot_id") != source_snapshot_id:
            raise projects.ProjectConflictError(project["current_revision"])
        transaction.create(
            ref,
            {
                "project_id": project_id,
                "request_id": request_id,
                "fingerprint": fingerprint,
                "status": "pending",
                "source_snapshot_id": source_snapshot_id,
                "source_project_revision": revision,
                "target_environment": target_environment,
                "started_at": projects._utcnow(),
            },
        )

    try:
        claim(client.transaction())
    except AlreadyExists, projects.ProjectConflictError:
        # A competing create can win without either transaction reading the missing receipt.
        reject_execution_replay(read_execution_request(project_id, request_id, actor=actor), fingerprint)
        raise
    return ref


def mark_execution_unconfirmed(ref) -> None:
    client = projects.get_required_firestore_client(unavailable_message="Execution request storage is unavailable")

    @projects.transactional
    def mark(transaction):
        current = projects._document_to_dict(ref.get(transaction=transaction))
        if current and current.get("status") == "pending":
            transaction.update(ref, {"status": "unconfirmed"})

    mark(client.transaction())
