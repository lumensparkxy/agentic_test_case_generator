"""Conservative provider-request reservations for one exact intake-backed journey."""

from dataclasses import dataclass
from hashlib import sha256
import re

from fastapi import HTTPException


INTAKE_STAGE = "requirements.parse"
CONNECTOR_INTAKE_OPERATIONS = frozenset({"requirements.import.jira", "requirements.import.azure_devops"})
INTAKE_OPERATIONS = CONNECTOR_INTAKE_OPERATIONS | {INTAKE_STAGE}
ASSESSMENT_STAGE = "test_cases.review"
REPAIR_STAGE = "testcases.repair"
EXHAUSTED = "journey_run_budget_exhausted"
UNAVAILABLE = "journey_run_budget_unavailable"
BUDGET_FIELD = "provider_journey_id"

_STAGE_LIMITS = {
    INTAKE_STAGE: 16,
    "prepare_scenarios": 7,
    "generate_tests": 24,
    ASSESSMENT_STAGE: 24,
    REPAIR_STAGE: 8,
}
REQUEST_LIMIT = sum(_STAGE_LIMITS.values())
_COLLECTION = "provider_journeys"
_LINKS = "provider_journey_artifacts"
_ARTIFACT_FIELDS = {
    "requirements": "requirements_snapshot_id",
    "use_cases": "use_cases_snapshot_id",
    "test_cases": "test_cases_snapshot_id",
}


@dataclass(frozen=True)
class JourneyBudget:
    ref: object
    data: dict

    @property
    def id(self):
        return self.data["journey_id"]


def unavailable():
    return HTTPException(409, {"code": UNAVAILABLE, "message": "The saved provider budget does not match this journey. Check the current sources."})


def exhausted():
    return HTTPException(409, {"code": EXHAUSTED, "message": "The provider request budget for this journey has been allocated."})


def _read(transaction, ref):
    return ref.get(transaction=transaction).to_dict()


def _budget_ref(project_doc, journey_id):
    if not isinstance(journey_id, str) or not re.fullmatch(r"journey_[0-9a-f]{64}", journey_id):
        raise unavailable()
    return project_doc.collection(_COLLECTION).document(journey_id)


def _intake_operation(data):
    # Existing file-backed records predate the explicit origin field.
    return data.get("intake_operation", INTAKE_STAGE)


def _slot(stage):
    return INTAKE_STAGE if stage in INTAKE_OPERATIONS else stage


def _validate_budget(ref, data, project_id, actor_id):
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] not in {1, 2}
        or data.get("journey_id") != ref.id
        or data.get("project_id") != project_id
        or data.get("actor_user_id") != actor_id
        or not isinstance(data.get("intake_attempt_id"), str)
        or intake_journey_id(data["intake_attempt_id"]) != ref.id
        or not isinstance(data.get("import_id"), str)
        or not data["import_id"]
        or type(data.get("intake_revision")) is not int
        or data["intake_revision"] < 1
        or type(data.get("preview_saved")) is not bool
        or data.get("request_limit") != REQUEST_LIMIT
        or not isinstance(_intake_operation(data), str)
        or (data["schema_version"] == 1 and _intake_operation(data) != INTAKE_STAGE)
        or (data["schema_version"] == 2 and _intake_operation(data) not in CONNECTOR_INTAKE_OPERATIONS)
        or not isinstance(data.get("slots"), dict)
    ):
        raise unavailable()
    slots = data["slots"]
    if any(
        stage not in _STAGE_LIMITS
        or not isinstance(slot, dict)
        or type(slot.get("requests")) is not int
        or not 1 <= slot["requests"] <= _STAGE_LIMITS[stage]
        or not isinstance(slot.get("attempt_id"), str)
        or not slot["attempt_id"]
        for stage, slot in slots.items()
    ):
        raise unavailable()
    reserved = data.get("requests_reserved")
    if type(reserved) is not int or reserved != sum(slot["requests"] for slot in slots.values()) or reserved > REQUEST_LIMIT:
        raise unavailable()
    return JourneyBudget(ref, data)


def read_budget(transaction, project_doc, actor_id, journey_id):
    ref = _budget_ref(project_doc, journey_id)
    return _validate_budget(ref, _read(transaction, ref), project_doc.id, actor_id)


def intake_journey_id(attempt_id):
    return f"journey_{attempt_id.removeprefix('draft_')}"


def prepare_intake_budget(transaction, project_doc, actor_id, attempt_id, import_id, revision, *, operation=INTAKE_STAGE):
    if operation not in INTAKE_OPERATIONS:
        raise unavailable()
    journey_id = intake_journey_id(attempt_id)
    ref = _budget_ref(project_doc, journey_id)
    identity = {
        "journey_id": journey_id,
        "project_id": project_doc.id,
        "actor_user_id": actor_id,
        "intake_attempt_id": attempt_id,
        "import_id": import_id,
        "intake_revision": revision,
    }
    stored = _read(transaction, ref)
    if stored is not None:
        budget = _validate_budget(ref, stored, project_doc.id, actor_id)
        if _intake_operation(budget.data) != operation or any(budget.data.get(key) != value for key, value in identity.items()):
            raise unavailable()
        return None

    data = {
        **identity,
        "intake_operation": operation,
        "schema_version": 2 if operation in CONNECTOR_INTAKE_OPERATIONS else 1,
        "request_limit": REQUEST_LIMIT,
        "requests_reserved": 0,
        "slots": {},
        "preview_saved": False,
        **dict.fromkeys(_ARTIFACT_FIELDS.values()),
    }
    return lambda: transaction.create(ref, data)


def available(budget, stage, requests):
    slot = _slot(stage)
    return (
        type(requests) is int
        and 1 <= requests <= _STAGE_LIMITS[slot]
        and (slot != INTAKE_STAGE or stage == _intake_operation(budget.data))
        and slot not in budget.data["slots"]
        and budget.data["requests_reserved"] + requests <= REQUEST_LIMIT
    )


def reserve(transaction, budget, stage, attempt_id, requests, now):
    if not available(budget, stage, requests):
        raise exhausted()
    slots = {**budget.data["slots"], _slot(stage): {"attempt_id": attempt_id, "requests": requests, "reserved_at": now}}
    transaction.update(budget.ref, {"slots": slots, "requests_reserved": budget.data["requests_reserved"] + requests})


def _result_fields(budget, stage, attempt_id, result_id):
    slots = budget.data["slots"]
    slot_key = _slot(stage)
    slot = slots.get(slot_key)
    if slot is None:
        if stage in {"prepare_scenarios", "generate_tests"}:
            raise unavailable()
        # A foreground operation can produce a deterministic result without a provider reservation.
        return {}
    if slot["attempt_id"] != attempt_id or "result_id" in slot:
        raise unavailable()
    return {"slots": {**slots, slot_key: {**slot, "result_id": result_id}}}


def finish_preview(transaction, budget, attempt_id, preview):
    operation = _intake_operation(budget.data)
    if (
        budget.data["intake_attempt_id"] != attempt_id
        or budget.data["import_id"] != preview["import_id"]
        or preview.get("operation") != operation
        or budget.data["preview_saved"]
    ):
        raise unavailable()
    transaction.update(budget.ref, {"preview_saved": True, **_result_fields(budget, operation, attempt_id, preview["import_id"])})


def budget_for_preview(transaction, project_doc, actor_id, preview):
    extraction = preview.get("extraction") or {}
    if BUDGET_FIELD not in extraction:
        return None
    budget = read_budget(transaction, project_doc, actor_id, extraction[BUDGET_FIELD])
    data = budget.data
    operation = _intake_operation(data)
    if (
        not data.get("preview_saved")
        or data.get("import_id") != preview.get("import_id")
        or preview.get("operation") != operation
        or data.get("intake_revision") != preview.get("base_project_revision")
        or data.get("requirements_snapshot_id") is not None
    ):
        raise unavailable()
    attempt = _read(transaction, project_doc.collection("project_draft_attempts").document(data["intake_attempt_id"])) or {}
    binding = attempt.get("requirement_extraction") or {}
    if (
        attempt.get("project_id") != project_doc.id
        or attempt.get("actor_id") != actor_id
        or attempt.get("operation_id") != data["intake_attempt_id"]
        or attempt.get("operation") != operation
        or attempt.get("state") != "completed"
        or attempt.get(BUDGET_FIELD) != budget.id
        or attempt.get("result_import_id") != data["import_id"]
        or binding.get("request_fingerprint") != extraction.get("fingerprint")
    ):
        raise unavailable()
    return budget


def _artifact_ref(project_doc, stage, artifact_id):
    key = sha256(f"{stage}:{artifact_id}".encode()).hexdigest()
    return project_doc.collection(_LINKS).document(key)


def budget_for_artifact(transaction, project_doc, actor_id, stage, artifact_id, *, expected_id=None):
    if not artifact_id:
        if expected_id is not None:
            raise unavailable()
        return None
    artifact = (
        project_doc.collection("snapshots")
        .document(artifact_id)
        .get(transaction=transaction, field_paths=("snapshot_id", "project_id", "actor_user_id", "stage", BUDGET_FIELD))
        .to_dict()
        or {}
    )
    link = _read(transaction, _artifact_ref(project_doc, stage, artifact_id))
    if BUDGET_FIELD not in artifact and link is None and expected_id is None:
        return None
    marker = artifact.get(BUDGET_FIELD)
    if (
        artifact.get("snapshot_id") != artifact_id
        or artifact.get("project_id") != project_doc.id
        or artifact.get("actor_user_id") != actor_id
        or artifact.get("stage") != stage
        or marker is None
        or (expected_id is not None and marker != expected_id)
        or not link
        or link.get("artifact_id") != artifact_id
        or link.get("stage") != stage
        or link.get("actor_user_id") != actor_id
        or link.get(BUDGET_FIELD) != marker
    ):
        raise unavailable()
    budget = read_budget(transaction, project_doc, actor_id, marker)
    if budget.data.get(_ARTIFACT_FIELDS[stage]) != artifact_id:
        raise unavailable()
    return budget


def link_artifact(transaction, project_doc, budget, stage, artifact_id, *, producer=None):
    result = _result_fields(budget, *producer, artifact_id) if producer is not None else {}
    transaction.create(
        _artifact_ref(project_doc, stage, artifact_id),
        {"artifact_id": artifact_id, "stage": stage, "actor_user_id": budget.data["actor_user_id"], BUDGET_FIELD: budget.id},
    )
    transaction.update(budget.ref, {_ARTIFACT_FIELDS[stage]: artifact_id, **result})
