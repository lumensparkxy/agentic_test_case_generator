"""Publish a reviewed intake preview and its audit in one project transaction."""

from dataclasses import dataclass

from fastapi import HTTPException

from ..contracts.requirement_imports import RequirementImportPreview
from ..models import AuthUser
from ..observability.metrics import record_workflow_completed
from .audit_service import complete_workflow_run, record_usage_event
from .billing_service import record_billing_consumption
from .requirement_import_service import repository, stage_extraction
from .requirement_reconciliation import digest
from .workflow_project_service import project_error_to_http


class ExtractionPublicationUnconfirmed(HTTPException):
    def __init__(self):
        super().__init__(503, {"code": "requirement_extraction_unconfirmed", "message": "The requirements preview could not be confirmed. Retry this request."})


@dataclass(frozen=True)
class ProjectRequirementExtraction:
    project_id: str
    revision: int
    actor: AuthUser
    request_id: str
    operation: str
    fingerprint: str

    @property
    def import_id(self):
        return digest(["requirement-extraction", self.actor.sub, self.request_id])

    def find_preview(self, *, required=False):
        try:
            result = repository().find_extraction(self.project_id, self.import_id, self.actor, self.revision, self.fingerprint)
            if result is not None:
                return RequirementImportPreview.model_validate(result)
        except HTTPException:
            raise
        except Exception as exc:
            raise ExtractionPublicationUnconfirmed() from exc
        if required:
            raise ExtractionPublicationUnconfirmed()
        return None

    def publish(self, *, response, event_type, billing_key, run_id, billing_context, quantity, result_metadata, billing_metadata):
        try:
            repo = repository()
        except RuntimeError as exc:
            raise project_error_to_http(exc) from exc
        import_id = self.import_id
        writes = []
        metadata = {"operation": self.operation, "project_id": self.project_id, "import_id": import_id, **result_metadata}
        event_id = record_usage_event(
            event_type=event_type,
            billing_key=billing_key,
            quantity=quantity,
            unit="requirement",
            actor=self.actor,
            request_id=self.request_id,
            workflow_run_id=run_id,
            status="completed",
            metadata=metadata,
            pending_writes=writes,
            firestore_client=repo.client,
        )
        complete_workflow_run(run_id, status="completed", metadata=metadata, pending_writes=writes, firestore_client=repo.client)

        def commit_audit(transaction):
            record_billing_consumption(
                current_user=self.actor,
                billing_context=billing_context,
                source_event_id=event_id,
                request_id=self.request_id,
                workflow_run_id=run_id,
                billing_key=billing_key,
                quantity=quantity,
                unit="requirement",
                metadata={"import_id": import_id, **billing_metadata},
                transaction=transaction,
                firestore_client=repo.client,
            )
            for reference, value, merge in writes:
                transaction.set(reference, value, merge=merge)

        extraction = {"fingerprint": self.fingerprint, "workflow_run_id": run_id, "source_event_id": event_id}
        try:
            saved = stage_extraction(repo, self.project_id, self.actor, self.revision, response, self.operation, import_id, extraction, commit_audit)
        except Exception as publish_error:
            try:
                # Reconcile a past commit before checking whether it can still be replayed.
                saved = repo.find_extraction(self.project_id, import_id, self.actor, self.revision, self.fingerprint, require_pending=False)
            except HTTPException:
                raise
            except Exception as exc:
                raise ExtractionPublicationUnconfirmed() from exc
            if saved is None:
                if isinstance(publish_error, HTTPException):
                    raise
                raise project_error_to_http(publish_error) from publish_error
        if saved["extraction"]["workflow_run_id"] == run_id:
            record_workflow_completed(run_id, "completed")
        else:
            complete_workflow_run(run_id, status="completed", metadata={"reused_import_id": import_id})
