"""Commit a completed browser run against its admitted project revision."""

from fastapi import HTTPException

from ..contracts.execution import ExecutionRunResponse
from ..contracts.projects import QaProjectExecutionRun
from ..models import AuthUser
from ..observability.metrics import record_workflow_completed
from . import workflow_project_service as projects
from .audit_service import complete_workflow_run, record_usage_event
from .execution_result_storage import execution_result_writes
from .quality_feedback import execution_finding_writes


OPERATION = "automation.execution.run"
EVENT_TYPE = "automation.execution.ran"
_REPORT_OPERATION = "reports.execution_summary"


class ExecutionPublicationUnconfirmed(RuntimeError):
    """The transaction result could not be verified without risking a false audit."""


def execution_run_counts(response: ExecutionRunResponse) -> dict:
    return {
        "status": response.status,
        "run_id": response.run_id,
        "passed_count": response.summary.passed,
        "failed_count": response.summary.failed,
        "flaky_count": response.summary.flaky,
        "invalid_count": response.summary.invalid,
        "skipped_count": response.summary.skipped,
    }


def _run_payload(response: ExecutionRunResponse, target: dict) -> dict:
    return {
        **target,
        "run_id": response.run_id,
        "status": response.status,
        "artifacts_root": response.artifacts_root,
        "playwright_report_paths": response.playwright_report_paths,
        "summary": response.summary.model_dump(mode="json"),
        "warnings": response.warnings,
        "results": [
            item.model_dump(
                mode="json",
                include={
                    "id",
                    "source_test_case_id",
                    "title",
                    "status",
                    "returncode",
                    "generated_spec_path",
                    "artifacts_dir",
                    "report_json_path",
                    "playwright_report_path",
                    "issues",
                },
            )
            for item in response.results
        ],
    }


def publish_execution_outcome(
    response: ExecutionRunResponse,
    *,
    actor: AuthUser,
    request_id: str,
    request_ref,
    workflow_run_id: str,
    target_environment: str,
    selected_ids: list[str],
    test_case_count: int,
) -> QaProjectExecutionRun:
    readiness = response.preview.readiness
    if readiness is None or readiness.project_id is None or readiness.project_revision is None or readiness.source_snapshot_id is None:
        raise ValueError("A project execution outcome requires an admitted project revision and suite")
    project_id = readiness.project_id
    source_revision = readiness.project_revision
    suite_id = readiness.source_snapshot_id
    target = {"target_environment": target_environment, "target_base_url": readiness.target_base_url}
    counts = execution_run_counts(response)
    outcome = _run_payload(response, target)
    approved = response.status == "passed"
    client = projects.get_required_firestore_client(unavailable_message="Execution result storage is unavailable")
    project_doc = client.collection(projects.QA_PROJECTS_COLLECTION).document(project_id)
    key = f"{OPERATION}:{request_id}:{target_environment}"
    run_key = f"automation.execution.run_record:{request_id}:{target_environment}"
    run_record_id = projects._stable_document_id("execution", project_id, run_key)
    run_ref = project_doc.collection("execution_runs").document(run_record_id)
    snapshot_id = projects._stable_document_id("snapshot", project_id, "execution", OPERATION, key)
    snapshot_ref = project_doc.collection("snapshots").document(snapshot_id)
    snapshots = project_doc.collection("snapshots")
    timeline = project_doc.collection("timeline")

    audit_writes = []
    event_id = record_usage_event(
        event_type=EVENT_TYPE,
        billing_key=OPERATION,
        quantity=len(response.results),
        unit="test_case",
        actor=actor,
        request_id=request_id,
        workflow_run_id=workflow_run_id,
        status="completed",
        metadata={"operation": OPERATION, "project_id": project_id, "source_project_revision": source_revision, "source_snapshot_id": suite_id, **counts},
        pending_writes=audit_writes,
        firestore_client=client,
    )
    complete_workflow_run(workflow_run_id, status="completed", metadata=counts, pending_writes=audit_writes, firestore_client=client)

    def project_record(transaction=None):
        current = projects._document_to_dict(project_doc.get(transaction=transaction))
        if current is None:
            raise projects.ProjectNotFoundError(project_id)
        projects._require_owner(current, actor)
        return current

    def saved_run(transaction=None):
        saved = projects._document_to_dict(run_ref.get(transaction=transaction))
        if saved is None:
            return None
        if (
            saved.get("project_id") != project_id
            or saved.get("run_record_id") != run_record_id
            or saved.get("run_id") != response.run_id
            or saved.get("source_snapshot_id") != suite_id
            or saved.get("request_id") != request_id
            or saved.get("snapshot_id") != snapshot_id
        ):
            raise HTTPException(409, "The saved browser request belongs to a different outcome.")
        return QaProjectExecutionRun.model_validate(saved)

    @projects.transactional
    def publish(transaction):
        current = project_record(transaction)
        existing = saved_run(transaction)
        if existing is not None:
            return existing
        if request_ref is not None:
            receipt = projects._document_to_dict(request_ref.get(transaction=transaction))
            if (
                not receipt
                or receipt.get("status") not in {"pending", "unconfirmed"}
                or receipt.get("request_id") != request_id
                or receipt.get("source_snapshot_id") != suite_id
                or receipt.get("source_project_revision") != source_revision
                or receipt.get("target_environment") != target_environment
            ):
                raise RuntimeError("The admitted execution request could not be verified")

        stage_state = projects._stage_state_from_payload(current)
        historical = (
            current.get("current_revision") != source_revision
            or (current.get("status") or "active") != "active"
            or stage_state.get("test_cases", {}).get("current_snapshot_id") != suite_id
        )
        run_revision = source_revision if historical else source_revision + 1
        now = projects._utcnow()
        snapshot_fields = {
            "project_id": project_id,
            "actor": actor,
            "request_id": request_id,
            "workflow_run_id": workflow_run_id,
            "source_event_id": event_id,
            "approved": approved,
            "now": now,
        }
        execution, event = projects._stage_snapshot_change(
            {} if historical else stage_state,
            stage="execution",
            snapshot_id=snapshot_id,
            payload=outcome,
            operation=OPERATION,
            source_snapshot_id=suite_id,
            title=f"{target_environment} execution run",
            metadata={
                **target,
                **counts,
                "source_snapshot_id": suite_id,
                "source_project_revision": source_revision,
                "selected_test_case_ids": selected_ids,
                "historical": historical,
            },
            next_revision=run_revision,
            idempotency_key=key,
            **snapshot_fields,
        )
        findings = [] if historical else execution_finding_writes(project_id, execution)
        parts = execution_result_writes(snapshot_ref, execution)
        for reference, value, merge in [*audit_writes, *findings, *parts]:
            transaction.set(reference, value, merge=merge)
        transaction.create(snapshot_ref, execution)
        transaction.create(timeline.document(event["event_id"]), event)
        run = projects.record_execution_run(
            project_id=project_id,
            actor=actor,
            request_id=request_id,
            run_id=response.run_id,
            **target,
            status_value=response.status,
            summary=outcome["summary"],
            test_case_count=test_case_count,
            snapshot_id=snapshot_id,
            source_snapshot_id=suite_id,
            selected_test_case_ids=selected_ids,
            artifacts_root=response.artifacts_root,
            playwright_report_paths=response.playwright_report_paths,
            workflow_run_id=workflow_run_id,
            source_event_id=event_id,
            project_revision=run_revision,
            historical=historical,
            transaction=transaction,
            idempotency_key=run_key,
        )
        if request_ref is not None:
            transaction.update(request_ref, {"status": "completed", "run_id": run.run_id, "run_record_id": run.run_record_id, "historical": historical})
        if not historical:
            report_key = f"{_REPORT_OPERATION}:{request_id}:{target_environment}"
            report_id = projects._stable_document_id("snapshot", project_id, "reports", _REPORT_OPERATION, report_key)
            report_metadata = {key: outcome[key] for key in ("run_id", "target_environment", "status", "artifacts_root", "playwright_report_paths")}
            report, report_event = projects._stage_snapshot_change(
                stage_state,
                stage="reports",
                snapshot_id=report_id,
                payload={"source": "execution", "summary": outcome["summary"], **report_metadata},
                operation=_REPORT_OPERATION,
                source_snapshot_id=snapshot_id,
                title=f"{target_environment} execution report",
                metadata=report_metadata,
                next_revision=run_revision + 1,
                idempotency_key=report_key,
                **snapshot_fields,
            )
            transaction.create(snapshots.document(report_id), report)
            transaction.create(timeline.document(report_event["event_id"]), report_event)
            transaction.update(project_doc, {"current_revision": run_revision + 1, "stage_state": stage_state, "latest_stage": "reports", "updated_at": now})
        return run

    try:
        saved = publish(client.transaction())
    except Exception:
        try:
            project_record()
            saved = saved_run()
        except HTTPException, projects.ProjectNotFoundError, projects.ProjectPermissionError:
            raise
        except Exception as exc:
            raise ExecutionPublicationUnconfirmed() from exc
        if saved is None:
            raise
    record_workflow_completed(workflow_run_id, "completed")
    return saved
