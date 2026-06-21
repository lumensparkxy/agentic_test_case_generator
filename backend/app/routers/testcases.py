import json
import logging
import hashlib
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

from ..agents.test_case_agent import generate_test_cases, refine_test_cases
from ..auth.jwt_auth import get_current_user
from ..models import AuthUser, GenerateTestCasesInput, GenerateTestCasesResponse, RefineTestCasesInput
from ..services.audit_service import complete_workflow_run, record_usage_event, start_workflow_run
from ..services.billing_service import enforce_billing_access, record_billing_consumption
from ..services.versioning_service import persist_test_case_versions
from ..services.workflow_project_service import append_stage_snapshot, get_project, project_error_to_http

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
    encoded = json.dumps(_model_payload(value), sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
) -> None:
    if not project_id:
        return
    try:
        current_project = get_project(project_id, actor=actor)
        current_requirements_snapshot = current_project.current_snapshots.get("requirements")
        current_context_snapshot = current_project.current_snapshots.get("context")
        use_case_snapshot = append_stage_snapshot(
            project_id=project_id,
            stage="use_cases",
            payload=_use_case_project_payload(response),
            operation=f"{operation}.use_cases",
            actor=actor,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=source_event_id,
            approved=response.approved,
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
        append_stage_snapshot(
            project_id=project_id,
            stage="test_cases",
            payload=_test_case_project_payload(response),
            operation=operation,
            actor=actor,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            source_event_id=source_event_id,
            approved=response.approved,
            source_snapshot_id=use_case_snapshot.snapshot_id,
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
            },
        )
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


@router.post("/testcases/generate", response_model=GenerateTestCasesResponse)
async def generate_test_cases_endpoint(
    request: Request,
    payload: GenerateTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
) -> GenerateTestCasesResponse:
    request_id = _get_request_id(request)
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
    try:
        result = await run_in_threadpool(
            generate_test_cases,
            payload,
            actor_user_id=current_user.sub,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation="testcases.generate",
        )
        response = GenerateTestCasesResponse(**result)
        event_id = _log_success(
            current_user=current_user,
            request=request,
            workflow_run_id=workflow_run_id,
            operation="testcases.generate",
            event_type="testcases.generated",
            billing_key="testcases.generate",
            quantity=len(response.test_cases),
            unit="test_case",
            result_metadata={
                "test_cases_generated_count": len(response.test_cases),
                "requirement_count": len(payload.requirements),
                "approved": response.approved,
            },
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
            base_project_revision=payload.base_project_revision,
            source_requirements=payload.requirements,
            source_context=payload.context,
        )
        return response
    except Exception as exc:
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


@router.post("/testcases/refine", response_model=GenerateTestCasesResponse)
async def refine_test_cases_endpoint(
    request: Request,
    payload: RefineTestCasesInput,
    current_user: AuthUser = Depends(get_current_user),
) -> GenerateTestCasesResponse:
    request_id = _get_request_id(request)
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
    try:
        result = await run_in_threadpool(
            refine_test_cases,
            payload,
            actor_user_id=current_user.sub,
            request_id=request_id,
            workflow_run_id=workflow_run_id,
            operation="testcases.refine",
        )
        response = GenerateTestCasesResponse(**result)
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
        return response
    except Exception as exc:
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
