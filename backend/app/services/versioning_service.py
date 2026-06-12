import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence
from uuid import uuid4

from ..models import AuthUser, Requirement, TestCase
from .audit_service import build_actor_snapshot
from .firestore_repository import get_optional_firestore_collection

REQUIREMENT_SETS_COLLECTION = "requirements_sets"
TEST_CASE_SETS_COLLECTION = "test_case_sets"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_payload(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _get_collection(collection_name: str):
    return get_optional_firestore_collection(
        collection_name,
        unavailable_message=f"Firestore client unavailable for {collection_name} persistence",
    )


def _safe_set(document_ref, payload: Dict[str, Any], *, operation: str, merge: bool = False) -> bool:
    try:
        document_ref.set(payload, merge=merge)
        return True
    except Exception as exc:  # pragma: no cover - depends on Firestore runtime state
        logging.warning("Firestore %s skipped because write failed: %s", operation, exc)
        return False


def _coerce_requirement(item: Requirement | Dict[str, Any]) -> Requirement:
    return item if isinstance(item, Requirement) else Requirement.model_validate(item)


def _coerce_test_case(item: TestCase | Dict[str, Any]) -> TestCase:
    return item if isinstance(item, TestCase) else TestCase.model_validate(item)


def _requirement_payload(requirement: Requirement) -> Dict[str, Any]:
    return requirement.model_dump(exclude={"artifact_set_id", "artifact_item_id", "artifact_version_id", "artifact_version_number"})


def _test_case_payload(test_case: TestCase) -> Dict[str, Any]:
    return test_case.model_dump(exclude={"artifact_set_id", "artifact_item_id", "artifact_version_id", "artifact_version_number"})


def persist_requirement_versions(
    *,
    current_requirements: Sequence[Requirement],
    previous_requirements: Optional[Sequence[Requirement | Dict[str, Any]]] = None,
    actor: Optional[AuthUser],
    request_id: str,
    workflow_run_id: Optional[str],
    source_event_id: Optional[str],
    operation: str,
    source_name: Optional[str] = None,
    source_names: Optional[Sequence[str]] = None,
    raw_text: Optional[str] = None,
    approved: Optional[bool] = None,
) -> list[Requirement]:
    requirements = [requirement if isinstance(requirement, Requirement) else Requirement.model_validate(requirement) for requirement in current_requirements]
    previous = [_coerce_requirement(requirement) for requirement in (previous_requirements or [])]
    collection = _get_collection(REQUIREMENT_SETS_COLLECTION)
    if collection is None:
        return requirements

    existing_set_id = next(
        (requirement.artifact_set_id for requirement in previous if requirement.artifact_set_id),
        None,
    ) or next(
        (requirement.artifact_set_id for requirement in requirements if requirement.artifact_set_id),
        None,
    )
    set_id = existing_set_id or str(uuid4())
    set_doc = collection.document(set_id)
    actor_snapshot = build_actor_snapshot(actor)
    now = _utcnow()
    overall_success = _safe_set(
        set_doc,
        {
            "set_id": set_id,
            "artifact_type": "requirements",
            "current_operation": operation,
            "request_id": request_id,
            "workflow_run_id": workflow_run_id,
            "latest_event_id": source_event_id,
            "actor": actor_snapshot,
            "actor_user_id": actor.sub if actor else None,
            "source_name": source_name,
            "source_names": list(source_names or []),
            "raw_text_hash": _hash_payload({"raw_text": raw_text}) if raw_text else None,
            "approved": approved,
            "item_count": len(requirements),
            "updated_at": now,
            **({"created_at": now} if not existing_set_id else {}),
        },
        operation="requirements_set_upsert",
        merge=True,
    )

    previous_by_item_id = {requirement.artifact_item_id: requirement for requirement in previous if requirement.artifact_item_id}
    previous_by_requirement_id = {requirement.id: requirement for requirement in previous}
    updated_requirements: list[Requirement] = []

    for requirement in requirements:
        previous_requirement = None
        if requirement.artifact_item_id and requirement.artifact_item_id in previous_by_item_id:
            previous_requirement = previous_by_item_id[requirement.artifact_item_id]
        elif requirement.id in previous_by_requirement_id:
            previous_requirement = previous_by_requirement_id[requirement.id]

        item_id = requirement.artifact_item_id or (previous_requirement.artifact_item_id if previous_requirement else None) or str(uuid4())
        previous_version_id = previous_requirement.artifact_version_id if previous_requirement else None
        version_number = int(previous_requirement.artifact_version_number or 0) + 1 if previous_requirement else 1
        version_id = str(uuid4())
        item_doc = set_doc.collection("items").document(item_id)
        requirement_payload = _requirement_payload(requirement)
        item_success = _safe_set(
            item_doc,
            {
                "item_id": item_id,
                "set_id": set_id,
                "requirement_id": requirement.id,
                "current_version_id": version_id,
                "latest_event_id": source_event_id,
                "actor": actor_snapshot,
                "actor_user_id": actor.sub if actor else None,
                "updated_at": now,
                **({"created_at": now} if not previous_requirement else {}),
            },
            operation="requirements_item_upsert",
            merge=True,
        )
        version_success = _safe_set(
            item_doc.collection("versions").document(version_id),
            {
                "version_id": version_id,
                "set_id": set_id,
                "item_id": item_id,
                "version_number": version_number,
                "previous_version_id": previous_version_id,
                "source_event_id": source_event_id,
                "workflow_run_id": workflow_run_id,
                "request_id": request_id,
                "operation": operation,
                "actor": actor_snapshot,
                "actor_user_id": actor.sub if actor else None,
                "content_hash": _hash_payload(requirement_payload),
                "payload": requirement_payload,
                "created_at": now,
            },
            operation="requirements_version_create",
        )
        overall_success = overall_success and item_success and version_success
        updated_requirements.append(
            requirement.model_copy(
                update={
                    "artifact_set_id": set_id,
                    "artifact_item_id": item_id,
                    "artifact_version_id": version_id,
                    "artifact_version_number": version_number,
                }
            )
        )

    return updated_requirements if overall_success else requirements


def persist_test_case_versions(
    *,
    current_test_cases: Sequence[TestCase],
    previous_test_cases: Optional[Sequence[TestCase | Dict[str, Any]]] = None,
    actor: Optional[AuthUser],
    request_id: str,
    workflow_run_id: Optional[str],
    source_event_id: Optional[str],
    operation: str,
    approved: Optional[bool] = None,
) -> list[TestCase]:
    test_cases = [test_case if isinstance(test_case, TestCase) else TestCase.model_validate(test_case) for test_case in current_test_cases]
    previous = [_coerce_test_case(test_case) for test_case in (previous_test_cases or [])]
    collection = _get_collection(TEST_CASE_SETS_COLLECTION)
    if collection is None:
        return test_cases

    existing_set_id = next(
        (test_case.artifact_set_id for test_case in previous if test_case.artifact_set_id),
        None,
    ) or next(
        (test_case.artifact_set_id for test_case in test_cases if test_case.artifact_set_id),
        None,
    )
    set_id = existing_set_id or str(uuid4())
    set_doc = collection.document(set_id)
    actor_snapshot = build_actor_snapshot(actor)
    now = _utcnow()
    overall_success = _safe_set(
        set_doc,
        {
            "set_id": set_id,
            "artifact_type": "test_cases",
            "current_operation": operation,
            "request_id": request_id,
            "workflow_run_id": workflow_run_id,
            "latest_event_id": source_event_id,
            "actor": actor_snapshot,
            "actor_user_id": actor.sub if actor else None,
            "approved": approved,
            "item_count": len(test_cases),
            "updated_at": now,
            **({"created_at": now} if not existing_set_id else {}),
        },
        operation="test_case_set_upsert",
        merge=True,
    )

    previous_by_item_id = {test_case.artifact_item_id: test_case for test_case in previous if test_case.artifact_item_id}
    previous_by_case_id = {test_case.id: test_case for test_case in previous}
    updated_test_cases: list[TestCase] = []

    for test_case in test_cases:
        previous_test_case = None
        if test_case.artifact_item_id and test_case.artifact_item_id in previous_by_item_id:
            previous_test_case = previous_by_item_id[test_case.artifact_item_id]
        elif test_case.id in previous_by_case_id:
            previous_test_case = previous_by_case_id[test_case.id]

        item_id = test_case.artifact_item_id or (previous_test_case.artifact_item_id if previous_test_case else None) or str(uuid4())
        previous_version_id = previous_test_case.artifact_version_id if previous_test_case else None
        version_number = int(previous_test_case.artifact_version_number or 0) + 1 if previous_test_case else 1
        version_id = str(uuid4())
        item_doc = set_doc.collection("items").document(item_id)
        test_case_payload = _test_case_payload(test_case)
        item_success = _safe_set(
            item_doc,
            {
                "item_id": item_id,
                "set_id": set_id,
                "test_case_id": test_case.id,
                "current_version_id": version_id,
                "latest_event_id": source_event_id,
                "actor": actor_snapshot,
                "actor_user_id": actor.sub if actor else None,
                "updated_at": now,
                **({"created_at": now} if not previous_test_case else {}),
            },
            operation="test_case_item_upsert",
            merge=True,
        )
        version_success = _safe_set(
            item_doc.collection("versions").document(version_id),
            {
                "version_id": version_id,
                "set_id": set_id,
                "item_id": item_id,
                "version_number": version_number,
                "previous_version_id": previous_version_id,
                "source_event_id": source_event_id,
                "workflow_run_id": workflow_run_id,
                "request_id": request_id,
                "operation": operation,
                "actor": actor_snapshot,
                "actor_user_id": actor.sub if actor else None,
                "content_hash": _hash_payload(test_case_payload),
                "payload": test_case_payload,
                "created_at": now,
            },
            operation="test_case_version_create",
        )
        overall_success = overall_success and item_success and version_success
        updated_test_cases.append(
            test_case.model_copy(
                update={
                    "artifact_set_id": set_id,
                    "artifact_item_id": item_id,
                    "artifact_version_id": version_id,
                    "artifact_version_number": version_number,
                }
            )
        )

    return updated_test_cases if overall_success else test_cases
