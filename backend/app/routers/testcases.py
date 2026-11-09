from ..services.scoped_refinement import prepare_scoped_refinement, merge_scoped_refinement
from ..services.knowledge_feedback import finish_generation, suggest_feedback
from ..services.guidance_dependency import generation_guidance
import json
import logging
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from ..agents.test_case_agent import generate_test_cases, refine_test_cases
from ..auth.jwt_auth import get_current_user
from ..contracts.requirements import RequirementCoveragePlan
from ..models import AuthUser, GenerateTestCasesInput, GenerateTestCasesResponse, RefineTestCasesInput
from ..observability.metrics import record_workflow_completed
from ..services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from ..services.billing_service import enforce_billing_access, record_billing_consumption
from ..services.firestore_repository import get_required_firestore_client
from ..services.orchestrator_service import _use_cases_human_approved
from ..services.requirement_import_service import validate_generation_baseline
from ..services.test_case_quality import case_source_hash
from ..services.test_case_review_progress import GenerationReviewProgress
from ..services.versioning_service import persist_test_case_versions
from ..services.workflow_project_service import ProjectConflictError, append_stage_snapshot, find_idempotent_stage_snapshot, get_project, project_error_to_http

router = APIRouter()


def _get_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or uuid4())


def _test_case_snapshot(test_cases: list[Any]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for index, item in enumerate(test_cases or []):
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        case_id = str((item or {}).get("id") or f"TC-{index + 1:03d}")
        snapshot[case_id] = json.dumps(item or {}, sort_keys=True, default=str)
    return snapshot


def _count_snapshot_changes(previous: dict[str, str], current: dict[str, str]) -> int:
    changed = sum(1 for key, value in current.items() if previous.get(key) != value)
    removed = sum(1 for key in previous.keys() if key not in current)
    return changed + removed


def _model_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_model_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _model_payload(item) for key, item in value.items()}
    return value


def _content_hash(value: Any) -> str:
    return case_source_hash(_model_payload(value))


def _hash_map(items: list[Any], *, id_key: str = "id") -> dict[str, str]:
    result: dict[str, str] = {}
    for index, item in enumerate(items or []):
        payload = _model_payload(item)
        if not isinstance(payload, dict):
            continue
        item_id = str(payload.get(id_key) or payload.get("requirement_id") or f"item-{index + 1}")
        result[item_id] = _content_hash(payload)
    return result


def _scenario_hash_map(coverage_plan: list[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for plan in coverage_plan or []:
        payload = _model_payload(plan)
        if not isinstance(payload, dict):
            continue
        for scenario_index, scenario in enumerate(payload.get("scenarios") or []):
            if not isinstance(scenario, dict):
                continue
            scenario_id = str(scenario.get("id") or f"{payload.get('requirement_id', 'REQ')}-SCN-{scenario_index + 1:02d}")
            result[scenario_id] = _content_hash(scenario)
    return result


def _use_case_project_payload(response: GenerateTestCasesResponse) -> dict[str, Any]:
    return {
        "requirement_analysis": _model_payload(response.requirement_analysis),
        "coverage_plan": _model_payload(response.coverage_plan),
        "review": _model_payload(response.review),
        "coverage_metrics": _model_payload(response.coverage_metrics),
        "workflow_settings": _model_payload(response.workflow_settings),
        "workflow_diagnostics": _model_payload(response.workflow_diagnostics),
        "generation_evidence": _model_payload(response.generation_evidence),
    }


def _test_case_project_payload(response: GenerateTestCasesResponse) -> dict[str, Any]:
    return {
        "test_cases": _model_payload(response.test_cases),
        "scoped_refinement": response.scoped_refinement,
        "substantive_assessment": response.substantive_assessment,
        "generation_tasks": response.generation_tasks,
        "approved": response.approved,
        "review": _model_payload(response.review),
        "iteration_history": _model_payload(response.iteration_history),
        "coverage_plan": _model_payload(response.coverage_plan),
        "requirement_analysis": _model_payload(response.requirement_analysis),
        "coverage_metrics": _model_payload(response.coverage_metrics),
        "workflow_settings": _model_payload(response.workflow_settings),
        "workflow_diagnostics": _model_payload(response.workflow_diagnostics),
        "generation_evidence": _model_payload(response.generation_evidence),
    }


def _incomplete_generation_message(response: GenerateTestCasesResponse) -> str:
    reasons = list(
        dict.fromkeys(
            str(task.get("reason") or "")
            + (" (scenarios: " + ", ".join(str(ref) for ref in task["scenario_refs"][:5]) + ")" if task.get("scenario_refs") else "")
            for task in response.generation_tasks
        )
    )
    details = "; ".join(reason[:600] for reason in reasons[:3] if reason)
    return "Generated candidate is incomplete; the existing suite was preserved. " + (
        f"Candidate findings: {details}" if details else "Review unresolved coverage and retry targeted generation."
    )


def _refinement_source_inputs(project_id: str | None, actor: AuthUser, base_revision: int | None = None) -> dict[str, Any]:
    if not project_id:
        return {}
    project = get_project(project_id, actor=actor)
    if base_revision is not None and project.current_revision != base_revision:
        raise HTTPException(409, "The source suite changed. Reload before repairing selected tests.")
    snapshot = project.current_snapshots.get("use_cases")
    state = project.stage_state.get("use_cases")
    if not snapshot or not state or state.stale or state.current_snapshot_id != snapshot.snapshot_id or not snapshot.payload.get("coverage_plan"):
        raise HTTPException(409, "Review current Use Cases before refining Test Cases; the source plan is missing or stale.")
    return {
        "source_coverage_plan": snapshot.payload["coverage_plan"],
        "source_requirement_analysis": snapshot.payload.get("requirement_analysis") or [],
    }


def _project_generation_source(payload: GenerateTestCasesInput, actor: AuthUser) -> tuple[int | None, str | None, dict[str, Any]]:
    if not payload.project_id:
        return payload.base_project_revision, None, {}
    try:
        project = get_project(payload.project_id, actor=actor)
        if payload.base_project_revision is not None and payload.base_project_revision != project.current_revision:
            raise ProjectConflictError(project.current_revision)
    except Exception as exc:
        raise project_error_to_http(exc) from exc

    if project.status != "active":
        raise HTTPException(409, "Restore the project before generating test cases.")
    state = project.stage_state.get("use_cases")
    snapshot = project.current_snapshots.get("use_cases")
    if not state or state.stale or not _use_cases_human_approved(state):
        return project.current_revision, None, {}

    error = "Review current Use Cases before generating Test Cases; the approved source plan is missing or incomplete."
    if not snapshot or snapshot.snapshot_id != state.current_snapshot_id:
        raise HTTPException(409, error)
    requirement_ids = {requirement.id for requirement in payload.requirements}
    try:
        plans = [RequirementCoveragePlan.model_validate(group) for group in snapshot.payload.get("coverage_plan") or []]
    except (TypeError, ValidationError) as exc:
        raise HTTPException(409, error) from exc
    scoped_plan = [group for group in plans if group.requirement_id in requirement_ids]
    if not scoped_plan or {group.requirement_id for group in scoped_plan if group.scenarios} != requirement_ids:
        raise HTTPException(409, error)

    return (
        project.current_revision,
        snapshot.snapshot_id,
        {
            "source_coverage_plan": _model_payload(scoped_plan),
            "source_requirement_analysis": [
                entry
                for entry in snapshot.payload.get("requirement_analysis") or []
                if isinstance(entry, dict) and entry.get("requirement_id") in requirement_ids
            ],
        },
    )


def _append_project_generation_snapshots(
    *,
    project_id: str | None,
    response: GenerateTestCasesResponse,
    operation: str,
    actor: AuthUser,
    request_id: str,
    workflow_run_id: str,
    source_event_id: str,
    base_project_revision: int | None,
    source_requirements: list[Any] | None = None,
    source_context: Any = None,
    approved_use_case_snapshot_id: str | None = None,
    review_progress: GenerationReviewProgress | None = None,
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
    pending_writes=(),
    publication_guard=None,
    repair_source=None,
    changed_case_ids=None,
):
    if not project_id:
        return
    try:
        current_project = get_project(project_id, actor=actor)
        current_requirements_snapshot = current_project.current_snapshots.get("requirements")
        current_context_snapshot = current_project.current_snapshots.get("context")
        use_case_snapshot = current_project.current_snapshots.get("use_cases")
        use_case_state = getattr(current_project, "stage_state", {}).get("use_cases")
        if (approved_use_case_snapshot_id is not None or repair_source is not None) and current_project.status != "active":
            raise HTTPException(409, "Restore the project before publishing test cases.")
        if repair_source is not None:
            suite_state = current_project.stage_state.get("test_cases")
            if not suite_state or suite_state.stale or suite_state.current_snapshot_id != repair_source.snapshot_id:
                raise HTTPException(409, "The source suite changed. Reload before repairing selected tests.")
        reuse_use_cases = bool(
            use_case_snapshot
            and use_case_state
            and not use_case_state.stale
            and use_case_state.current_snapshot_id == use_case_snapshot.snapshot_id
            and response.coverage_plan
            and use_case_snapshot.payload.get("coverage_plan") == _model_payload(response.coverage_plan)
        )
        # Bound generation and selected repair keep the exact source plan
        # verified before the model ran, including older sparse snapshots.
        if approved_use_case_snapshot_id is not None:
            if (
                not use_case_state
                or use_case_state.stale
                or not use_case_snapshot
                or use_case_state.current_snapshot_id != approved_use_case_snapshot_id
                or use_case_snapshot.snapshot_id != approved_use_case_snapshot_id
            ):
                raise HTTPException(409, "Use Cases changed during generation. Reload and retry against the current source plan.")
            reuse_use_cases = True

        # Test Case review/analysis belongs to the downstream artifact. Reusing a
        # plan must retain its exact source assessment and human review record.
        if operation == "testcases.refine" and not reuse_use_cases:
            raise HTTPException(409, "Use Cases changed during refinement. Reload and retry against the current source plan.")
        if not reuse_use_cases:
            use_case_snapshot = append_stage_snapshot(
                project_id=project_id,
                stage="use_cases",
                payload=_use_case_project_payload(response),
                operation=f"{operation}.use_cases",
                actor=actor,
                request_id=request_id,
                workflow_run_id=workflow_run_id,
                source_event_id=source_event_id,
                # Automated quality approval stays in payload.review. Advancing the
                # durable Use Cases stage requires an explicit human review record.
                approved=False,
                title="Use cases updated",
                metadata={
                    "requirement_analysis_count": len(response.requirement_analysis),
                    "coverage_plan_count": len(response.coverage_plan),
                },
                base_project_revision=base_project_revision,
            )
        source_snapshot_ids = {
            "requirements": current_requirements_snapshot.snapshot_id if current_requirements_snapshot else None,
            "context": current_context_snapshot.snapshot_id if current_context_snapshot else None,
            "use_cases": use_case_snapshot.snapshot_id,
        }
        context_selection = {}
        if operation == "testcases.generate":
            if source_context is None:
                context_selection["source_context_omitted"] = True
            elif source_context.grounded_context:
                context_selection["source_context_artifact_ids"] = [source.id for source in source_context.grounded_context.artifact_sources]

        def commit(transaction, snapshot):
            if publication_guard is not None:
                publication_guard(transaction)
            if review_progress is not None and approved_use_case_snapshot_id is not None:
                review_progress.publish_on_commit(transaction, snapshot)

        case_payload = _test_case_project_payload(response)
        if repair_source is not None:
            case_payload["test_cases"] = [
                case if case["id"] in changed_case_ids else original
                for case, original in zip(case_payload["test_cases"], repair_source.payload["test_cases"], strict=True)
            ]
        return append_stage_snapshot(
            project_id=project_id,
            stage="test_cases",
            payload=case_payload,
            operation=operation,
            actor=actor,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=source_event_id,
            approved=response.approved,
            source_snapshot_id=use_case_snapshot.snapshot_id,
            base_project_revision=base_project_revision if reuse_use_cases else None,
            idempotency_key=idempotency_key,
            pending_writes=pending_writes,
            transaction_guard=commit if publication_guard is not None or review_progress is not None else None,
            title="Test cases updated",
            metadata={
                "test_case_count": len(response.test_cases),
                "approved": response.approved,
                "source_snapshot_ids": source_snapshot_ids,
                "source_requirements_snapshot_id": source_snapshot_ids["requirements"],
                "source_context_snapshot_id": source_snapshot_ids["context"],
                "source_use_case_snapshot_id": source_snapshot_ids["use_cases"],
                "source_requirement_hashes": _hash_map(source_requirements or [], id_key="id"),
                "source_use_case_hashes": _scenario_hash_map(response.coverage_plan),
                "source_context_hash": _content_hash(source_context) if source_context is not None else None,
                "generation_request_fingerprint": request_fingerprint,
                "source_test_cases_snapshot_id": repair_source.snapshot_id if repair_source is not None else None,
                **context_selection,
            },
        )
    except HTTPException:
        raise
    except Exception as project_exc:
        raise project_error_to_http(project_exc) from project_exc


def _log_success(
    *,
    current_user: AuthUser,
    request: Request,
    workflow_run_id: str,
    operation: str,
    event_type: str,
    billing_key: str,
    quantity: int,
    unit: str,
    result_metadata: dict[str, Any],
) -> str:
    request_id = _get_request_id(request)
    complete_workflow_run(workflow_run_id, status="completed", metadata=result_metadata)
    return record_usage_event(
        event_type=event_type,
        billing_key=billing_key,
        quantity=quantity,
        unit=unit,
        actor=current_user,
        request_id=request_id,
        workflow_run_id=workflow_run_id,
        status="completed",
        metadata={"operation": operation, **result_metadata},
    )


def _log_failure(
    *,
    current_user: AuthUser,
    request: Request,
    workflow_run_id: str,
    operation: str,
    event_type: str,
    billing_key: str,
    error_message: str,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    request_id = _get_request_id(request)
    failure_metadata = {"operation": operation, **(metadata or {})}
    complete_workflow_run(
        workflow_run_id,
        status="failed",
        metadata=failure_metadata,
        error_message=error_message,
    )
    record_usage_event(
        event_type=event_type,
        billing_key=billing_key,
        quantity=0,
        unit="request",
        actor=current_user,
        request_id=request_id,
        workflow_run_id=workflow_run_id,
        status="failed",
        metadata={**failure_metadata, "error_message": error_message},
    )


def _record_billing_consumption_safe(
    *,
    current_user: AuthUser,
    billing_context,
    source_event_id: str,
    request: Request,
    workflow_run_id: str,
    billing_key: str,
    quantity: int,
    unit: str,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    try:
        record_billing_consumption(
            current_user=current_user,
            billing_context=billing_context,
            source_event_id=source_event_id,
            request_id=_get_request_id(request),
            workflow_run_id=workflow_run_id,
            billing_key=billing_key,
            quantity=quantity,
            unit=unit,
            metadata=metadata,
        )
    except Exception as exc:  # pragma: no cover - defensive billing isolation
        logging.warning("Billing consumption recording failed for %s: %s", billing_key, exc)


def _test_case_publication_receipt(project_id, request_id, actor, operation):
    try:
        key = case_source_hash([actor.sub, request_id])
        return find_idempotent_stage_snapshot(project_id, "test_cases", operation, key, actor=actor)
    except Exception as exc:
        raise project_error_to_http(exc) from exc


def _test_case_receipt_response(receipt, fingerprint):
    if receipt.metadata.get("generation_request_fingerprint") != fingerprint:
        raise HTTPException(409, "This request already published a suite for different inputs.")
    return GenerateTestCasesResponse.model_validate({**receipt.payload, "guidance": receipt.metadata.get("guidance")})


class _UnconfirmedTestCasePublication(HTTPException):
    def __init__(self):
        super().__init__(503, {"code": "generation_publication_unconfirmed", "message": "Test suite publication could not be confirmed. Retry this request."})


def _publish_bound_suite(
    payload,
    response,
    *,
    operation,
    actor,
    request_id,
    fingerprint,
    run_id,
    billing_context,
    base_revision,
    result_metadata,
    source_snapshot_id=None,
    progress=None,
    repair_source=None,
):
    try:
        client = get_required_firestore_client(unavailable_message="Project storage is unavailable")
    except RuntimeError as exc:
        raise project_error_to_http(exc) from exc
    pending_writes = []
    repairing = repair_source is not None
    changed_case_ids = set(response.scoped_refinement["changed_ids"]) if repairing else None
    quantity = len(changed_case_ids) if repairing else len(response.test_cases)
    event_id = record_usage_event(
        event_type="testcases.refined" if repairing else "testcases.generated",
        billing_key=operation,
        quantity=quantity,
        unit="test_case",
        actor=actor,
        request_id=request_id,
        workflow_run_id=run_id,
        status="completed",
        metadata={"operation": operation, **result_metadata},
        pending_writes=pending_writes,
        firestore_client=client,
    )
    response.test_cases = persist_test_case_versions(
        current_test_cases=response.test_cases
        if repairing
        else [
            case.model_copy(update={"artifact_set_id": None, "artifact_item_id": None, "artifact_version_id": None, "artifact_version_number": None})
            for case in response.test_cases
        ],
        previous_test_cases=payload.test_cases if repairing else None,
        actor=actor,
        request_id=request_id,
        workflow_run_id=run_id,
        source_event_id=event_id,
        operation=operation,
        approved=response.approved,
        changed_case_ids=changed_case_ids,
        pending_writes=pending_writes,
    )
    complete_workflow_run(run_id, status="completed", metadata=result_metadata, pending_writes=pending_writes, firestore_client=client)

    def bill(transaction):
        record_billing_consumption(
            current_user=actor,
            billing_context=billing_context,
            source_event_id=event_id,
            request_id=request_id,
            workflow_run_id=run_id,
            billing_key=operation,
            quantity=quantity,
            unit="test_case",
            metadata={"approved": response.approved, "requirement_count": len(payload.requirements)},
            transaction=transaction,
            firestore_client=client,
        )

    try:
        receipt = _append_project_generation_snapshots(
            project_id=payload.project_id,
            response=response,
            operation=operation,
            actor=actor,
            request_id=request_id,
            workflow_run_id=run_id,
            source_event_id=event_id,
            base_project_revision=base_revision,
            source_requirements=payload.requirements,
            source_context=payload.context,
            approved_use_case_snapshot_id=source_snapshot_id,
            review_progress=progress,
            idempotency_key=case_source_hash([actor.sub, request_id]),
            request_fingerprint=fingerprint,
            pending_writes=pending_writes,
            publication_guard=bill,
            repair_source=repair_source,
            changed_case_ids=changed_case_ids,
        )
    except Exception:
        try:
            receipt = _test_case_publication_receipt(payload.project_id, request_id, actor, operation)
        except HTTPException as lookup_error:
            if lookup_error.status_code == 503:
                raise _UnconfirmedTestCasePublication() from lookup_error
            raise
        if receipt is None:
            raise
    if receipt.workflow_run_id != run_id:
        receipt = _test_case_publication_receipt(payload.project_id, request_id, actor, operation)
        if receipt is None:
            raise _UnconfirmedTestCasePublication()
    published = _test_case_receipt_response(receipt, fingerprint)
    if receipt.workflow_run_id == run_id:
        record_workflow_completed(run_id, "completed")
    else:
        complete_workflow_run(run_id, status="completed", metadata={"reused_snapshot_id": receipt.snapshot_id})
    return published, receipt.workflow_run_id == run_id


@router.post("/testcases/generate", response_model=GenerateTestCasesResponse, dependencies=[Depends(generation_guidance("test_cases"))])
async def generate_test_cases_endpoint(
    request: Request,
    payload: GenerateTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
) -> GenerateTestCasesResponse:
    request_id = _get_request_id(request)
    fingerprint = _content_hash(payload) if payload.project_id else None
    if payload.project_id:
        receipt = await run_in_threadpool(_test_case_publication_receipt, payload.project_id, request_id, current_user, "testcases.generate")
        if receipt is not None:
            return _test_case_receipt_response(receipt, fingerprint)
    await run_in_threadpool(validate_generation_baseline, payload.project_id, current_user, payload.base_project_revision, payload.requirements)
    base_revision, approved_use_case_snapshot_id, source_inputs = await run_in_threadpool(_project_generation_source, payload, current_user)
    review_progress = GenerationReviewProgress() if approved_use_case_snapshot_id is not None else None
    billing_context = await run_in_threadpool(
        enforce_billing_access,
        current_user=current_user,
        billing_key="testcases.generate",
    )
    workflow_run_id = start_workflow_run(
        operation="testcases.generate",
        actor=current_user,
        request_id=request_id,
        metadata={"requirement_count": len(payload.requirements)},
    )
    published = False
    try:
        result = await run_in_threadpool(
            generate_test_cases,
            payload,
            actor_user_id=current_user.sub,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation="testcases.generate",
            review_progress=review_progress,
            **source_inputs,
        )
        response = GenerateTestCasesResponse(**result)
        if source_inputs and _model_payload(response.coverage_plan) != source_inputs["source_coverage_plan"]:
            raise HTTPException(409, "Generated Test Cases did not match the approved Use Cases plan. Retry generation.")
        if response.generation_tasks and payload.project_id:
            existing = await run_in_threadpool(get_project, payload.project_id, actor=current_user)
            if existing.current_snapshots.get("test_cases"):
                raise HTTPException(422, _incomplete_generation_message(response))
        result_metadata = {
            "test_cases_generated_count": len(response.test_cases),
            "requirement_count": len(payload.requirements),
            "approved": response.approved,
        }
        if approved_use_case_snapshot_id is not None:
            response, own_publication = await run_in_threadpool(
                _publish_bound_suite,
                payload,
                response,
                operation="testcases.generate",
                actor=current_user,
                request_id=request_id,
                fingerprint=fingerprint,
                run_id=workflow_run_id,
                billing_context=billing_context,
                base_revision=base_revision,
                source_snapshot_id=approved_use_case_snapshot_id,
                progress=review_progress,
                result_metadata=result_metadata,
            )
            published = True
            if own_publication:
                response.knowledge_suggestion = suggest_feedback(current_user, payload.project_id, "test_cases", payload.feedback, request_id)
            return response
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="testcases.generate",
            event_type="testcases.generated",
            billing_key="testcases.generate",
            quantity=len(response.test_cases),
            unit="test_case",
            result_metadata=result_metadata,
        )
        _record_billing_consumption_safe(
            current_user=current_user,
            billing_context=billing_context,
            source_event_id=event_id,
            request=request,
            workflow_run_id=workflow_run_id,
            billing_key="testcases.generate",
            quantity=len(response.test_cases),
            unit="test_case",
            metadata={"approved": response.approved, "requirement_count": len(payload.requirements)},
        )
        response.test_cases = persist_test_case_versions(
            current_test_cases=response.test_cases,
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            operation="testcases.generate",
            approved=response.approved,
        )
        _append_project_generation_snapshots(
            project_id=payload.project_id,
            response=response,
            operation="testcases.generate",
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            base_project_revision=base_revision,
            source_requirements=payload.requirements,
            source_context=payload.context,
            approved_use_case_snapshot_id=approved_use_case_snapshot_id,
            review_progress=review_progress,
        )
        return finish_generation(response, current_user, payload.project_id, "test_cases", payload.feedback, request_id)
    except Exception as exc:
        if not published and not isinstance(exc, _UnconfirmedTestCasePublication):
            _log_failure(
                current_user=current_user,
                request=request,
                workflow_run_id=workflow_run_id,
                operation="testcases.generate",
                event_type="testcases.generated",
                billing_key="testcases.generate",
                error_message=str(exc),
                metadata={"requirement_count": len(payload.requirements)},
            )
        raise


@router.post("/testcases/refine", response_model=GenerateTestCasesResponse, dependencies=[Depends(generation_guidance("test_cases"))])
async def refine_test_cases_endpoint(
    request: Request,
    payload: RefineTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
) -> GenerateTestCasesResponse:
    request_id = _get_request_id(request)
    scoped = bool(payload.project_id and payload.selected_test_case_ids)
    fingerprint = _content_hash(payload) if scoped else None
    if scoped:
        receipt = await run_in_threadpool(_test_case_publication_receipt, payload.project_id, request_id, current_user, "testcases.refine")
        if receipt is not None:
            return _test_case_receipt_response(receipt, fingerprint)
    await run_in_threadpool(validate_generation_baseline, payload.project_id, current_user, payload.base_project_revision, payload.requirements)
    source_inputs = await run_in_threadpool(_refinement_source_inputs, payload.project_id, current_user, payload.base_project_revision if scoped else None)
    model_payload, model_sources, baseline, source_project = await run_in_threadpool(prepare_scoped_refinement, payload, source_inputs, current_user)
    billing_context = await run_in_threadpool(
        enforce_billing_access,
        current_user=current_user,
        billing_key="testcases.refine",
    )
    workflow_run_id = start_workflow_run(
        operation="testcases.refine",
        actor=current_user,
        request_id=request_id,
        metadata={"existing_test_case_count": len(payload.test_cases)},
    )
    published = False
    try:
        result = await run_in_threadpool(
            refine_test_cases,
            model_payload,
            actor_user_id=current_user.sub,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation="testcases.refine",
            **model_sources,
        )
        response = GenerateTestCasesResponse(**result)
        if baseline is not None:
            response = merge_scoped_refinement(response, baseline, payload.selected_test_case_ids, source_inputs)
            payload = payload.model_copy(
                update={
                    "test_cases": baseline.test_cases,
                    "requirements": source_project.current_snapshots["requirements"].payload.get("requirements", []),
                    "context": model_payload.context,
                }
            )
            response, own_publication = await run_in_threadpool(
                _publish_bound_suite,
                payload,
                response,
                operation="testcases.refine",
                actor=current_user,
                request_id=request_id,
                fingerprint=fingerprint,
                run_id=workflow_run_id,
                billing_context=billing_context,
                base_revision=payload.base_project_revision,
                source_snapshot_id=source_project.current_snapshots["use_cases"].snapshot_id,
                repair_source=source_project.current_snapshots["test_cases"],
                result_metadata={
                    "test_cases_modified_count": response.scoped_refinement["changed"],
                    "test_cases_total": len(response.test_cases),
                    "approved": response.approved,
                },
            )
            published = True
            if own_publication:
                response.knowledge_suggestion = suggest_feedback(current_user, payload.project_id, "test_cases", payload.feedback, request_id)
            return response
        if response.generation_tasks and payload.project_id and baseline is None:
            existing = await run_in_threadpool(get_project, payload.project_id, actor=current_user)
            if existing.current_snapshots.get("test_cases"):
                raise HTTPException(422, _incomplete_generation_message(response))
        modified_count = _count_snapshot_changes(
            _test_case_snapshot(payload.test_cases),
            _test_case_snapshot(response.test_cases),
        )
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="testcases.refine",
            event_type="testcases.refined",
            billing_key="testcases.refine",
            quantity=max(1, modified_count),
            unit="test_case",
            result_metadata={
                "test_cases_modified_count": modified_count,
                "test_cases_total": len(response.test_cases),
                "approved": response.approved,
            },
        )
        _record_billing_consumption_safe(
            current_user=current_user,
            billing_context=billing_context,
            source_event_id=event_id,
            request=request,
            workflow_run_id=workflow_run_id,
            billing_key="testcases.refine",
            quantity=max(1, modified_count),
            unit="test_case",
            metadata={"approved": response.approved},
        )
        response.test_cases = persist_test_case_versions(
            current_test_cases=response.test_cases,
            previous_test_cases=payload.test_cases,
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            operation="testcases.refine",
            approved=response.approved,
        )
        _append_project_generation_snapshots(
            project_id=payload.project_id,
            response=response,
            operation="testcases.refine",
            actor=current_user,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            base_project_revision=payload.base_project_revision,
            source_requirements=payload.requirements,
            source_context=payload.context,
        )
        return finish_generation(response, current_user, payload.project_id, "test_cases", payload.feedback, request_id)
    except Exception as exc:
        if not published and not isinstance(exc, _UnconfirmedTestCasePublication):
            _log_failure(
                current_user=current_user,
                request=request,
                workflow_run_id=workflow_run_id,
                operation="testcases.refine",
                event_type="testcases.refined",
                billing_key="testcases.refine",
                error_message=str(exc),
                metadata={"existing_test_case_count": len(payload.test_cases)},
            )
        raise
