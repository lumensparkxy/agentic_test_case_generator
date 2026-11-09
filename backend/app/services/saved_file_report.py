"""Read immutable file report evidence without rebuilding or publishing a file."""

import re

from pydantic import ValidationError

from ..contracts.exports import (
    ExportAuditMetadata,
    FileReportDeliveryKind,
    FileReportFormat,
    SavedFileReport,
    SavedFileReportCaseVersion,
    SavedFileReportEvidence,
)
from ..contracts.test_cases import TestCase
from ..models import AuthUser
from .requirement_reconciliation import digest
from .workflow_project_service import ProjectNotFoundError, _document_to_dict, _get_project_doc, _require_owner, _serialize_value


REPORT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$"
_FILE_OPERATIONS: dict[str, FileReportFormat] = {"export.json": "json", "export.csv": "csv", "export.excel": "excel"}
_UPSTREAM_KEYS = {
    "requirements": "source_requirements_snapshot_id",
    "context": "source_context_snapshot_id",
    "use_cases": "source_use_case_snapshot_id",
}
_SOURCE_STAGES = {*_UPSTREAM_KEYS, "test_cases", "execution"}
_CASE_VERSION_KEYS = ("artifact_item_id", "artifact_version_id", "artifact_version_number")


def _map(value):
    return value if isinstance(value, dict) else {}


def _document_id(value):
    return isinstance(value, str) and re.fullmatch(REPORT_ID_PATTERN, value) is not None


def _snapshot(snapshots, project_id, snapshot_id, stage, *, field_paths=None):
    if not _document_id(snapshot_id):
        return None
    raw = _document_to_dict(snapshots.document(snapshot_id).get(field_paths=field_paths))
    if raw and (raw.get("project_id"), raw.get("snapshot_id"), raw.get("stage")) == (project_id, snapshot_id, stage):
        return raw
    return None


def _stored_audit(raw, fingerprint):
    if not isinstance(raw, dict) or not isinstance(fingerprint, str):
        return None
    try:
        parsed = ExportAuditMetadata.model_validate(raw)
        restored = parsed.model_dump(mode="json")
        if parsed.design_delivery is None:
            restored.pop("design_delivery")
        # The export fingerprint includes these nullable keys in open audit maps.
        # Project snapshot serialization drops their null values in storage.
        for item in restored["case_versions"]:
            for key in _CASE_VERSION_KEYS:
                item.setdefault(key, None)
        for item in restored["source_mappings"]:
            for source in item["sources"]:
                source.setdefault("source_section", None)
        coverage = restored["coverage"]
        for stage in ("requirements", "scenarios"):
            for key in ("total", "covered_count", "missing_count", "covered_ids", "missing_ids"):
                coverage[stage].setdefault(key, None)
        coverage.setdefault("behavioral_assessment", None)
        if _serialize_value(restored) == raw and digest(restored) == fingerprint:
            return parsed
    except ValidationError, TypeError, KeyError, AttributeError:
        pass
    return None


def _legacy_approval(report, payload):
    approved = report.get("approved")
    if type(approved) is bool and payload.get("approved", approved) is approved:
        return approved
    return None


def _delivery_kind(payload, metadata, legacy_approved) -> FileReportDeliveryKind:
    design_markers = (payload.get("delivery_kind"), metadata.get("delivery_kind"))
    if design_markers == ("reviewed_design", "reviewed_design"):
        return "reviewed_design"
    if design_markers != (None, None):
        return "unknown"
    draft_override = payload.get("draft_override_requested")
    if legacy_approved is True and draft_override in (None, False):
        return "approved"
    if legacy_approved is not True and draft_override is True:
        return "draft"
    return "unknown"


def _case_versions(suite, audit):
    rows = _map(suite.get("payload")).get("test_cases")
    if not isinstance(rows, list) or len(rows) != audit.delivered_case_count or len(rows) != len(audit.case_versions):
        return []
    versions = []
    seen = set()
    for row, recorded in zip(rows, audit.case_versions):
        if not isinstance(row, dict):
            return []
        case_id = row.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen or case_id != recorded.get("case_id"):
            return []
        seen.add(case_id)
        if any(type(row.get(key)) is not type(recorded.get(key)) or row.get(key) != recorded.get(key) for key in _CASE_VERSION_KEYS):
            return []
        item_id, version_id, number = (row.get(key) for key in _CASE_VERSION_KEYS)
        if item_id is None and version_id is None and number is None:
            continue
        if not isinstance(item_id, str) or not item_id or not isinstance(version_id, str) or not version_id or type(number) is not int or number < 1:
            return []
        try:
            TestCase.model_validate(row)
        except ValidationError:
            return []
        versions.append(SavedFileReportCaseVersion(case_id=case_id, case_version_id=version_id, case_version_number=number))
    return versions


def _sources_match(snapshots, project_id, audit, suite):
    sources = audit.source_snapshot_ids
    suite_metadata = _map(suite.get("metadata"))
    suite_sources = _map(suite_metadata.get("source_snapshot_ids"))
    for stage, key in _UPSTREAM_KEYS.items():
        expected = sources.get(stage)
        flattened, nested = suite_metadata.get(key), suite_sources.get(stage)
        if expected != (flattened or nested) or (flattened is not None and nested is not None and flattened != nested):
            return False
    for stage, snapshot_id in sources.items():
        if stage == "test_cases":
            continue
        fields = ("project_id", "snapshot_id", "stage", "operation", "source_snapshot_id")
        source = _snapshot(snapshots, project_id, snapshot_id, stage, field_paths=fields)
        if not source or (
            stage == "execution" and (source.get("operation") != "automation.execution.run" or source.get("source_snapshot_id") != audit.snapshot_id)
        ):
            return False
    return True


def _report_evidence(snapshots, report, header):
    payload, metadata = _map(report.get("payload")), _map(report.get("metadata"))
    audit = _stored_audit(payload.get("audit_metadata"), metadata.get("export_audit_fingerprint"))
    if audit is None:
        return None
    sources = audit.source_snapshot_ids
    runs = audit.execution_run_ids
    recorded = _map(payload.get("evidence"))
    if (
        audit.project_id != header.project_id
        or audit.project_revision != header.project_revision - 1
        or audit.snapshot_id != report.get("source_snapshot_id")
        or audit.snapshot_id != sources.get("test_cases")
        or sources != metadata.get("source_snapshot_ids")
        or sources != recorded.get("source_snapshot_ids")
        or sources.keys() - _SOURCE_STAGES
        or runs != metadata.get("execution_run_ids")
        or runs != recorded.get("execution_run_ids")
        or audit.draft_override_used is not payload.get("draft_override_requested")
        or audit.draft_override_reason != payload.get("draft_override_reason")
    ):
        return None

    design = audit.design_delivery
    if design is not None:
        if (
            header.delivery_kind != "reviewed_design"
            or design.legacy_approved is not header.legacy_approved
            or design.suite_snapshot_id != audit.snapshot_id
            or design.scenario_snapshot_id != sources.get("use_cases")
            or design.human_acceptance != audit.human_review_status
            or audit.is_draft
        ):
            return None
    elif header.delivery_kind not in ("draft", "approved") or header.legacy_approved is not (not audit.is_draft):
        return None

    versions = []
    if audit.snapshot_id is not None:
        suite = _snapshot(snapshots, header.project_id, audit.snapshot_id, "test_cases")
        if (
            not suite
            or type(suite.get("version")) is not int
            or suite["version"] != audit.snapshot_version
            or not _sources_match(snapshots, header.project_id, audit, suite)
        ):
            return None
        versions = _case_versions(suite, audit)
    elif sources or runs or audit.case_versions or audit.snapshot_version is not None:
        return None

    requirements = [item.get("normalized_requirement_id") for item in audit.source_mappings]
    if any(not isinstance(item, str) or not item for item in requirements) or (requirements and "requirements" not in sources):
        return None
    return SavedFileReportEvidence(
        source_snapshot_ids=sources,
        suite_snapshot_id=audit.snapshot_id,
        exported_at=audit.exported_at,
        design_delivery=design,
        human_review_status=audit.human_review_status,
        machine_review_status=audit.machine_review_status,
        draft_override_reason=audit.draft_override_reason,
        execution_status=audit.execution_status,
        execution_run_ids=runs,
        requirement_ids=list(dict.fromkeys(requirements)),
        case_versions=versions,
    )


def read_saved_file_report(project_id: str, report_id: str, *, actor: AuthUser) -> SavedFileReport:
    project_doc = _get_project_doc(project_id)
    project = _document_to_dict(project_doc.get(field_paths=("project_id", "owner_user_id", "stage_state")))
    if not project or project.get("project_id") != project_id:
        raise ProjectNotFoundError(project_id)
    _require_owner(project, actor)
    snapshots = project_doc.collection("snapshots")
    report = _snapshot(snapshots, project_id, report_id, "reports")
    if not report:
        raise ProjectNotFoundError(report_id)
    operation = report.get("operation")
    file_format = _FILE_OPERATIONS.get(operation) if isinstance(operation, str) else None
    payload, metadata = _map(report.get("payload")), _map(report.get("metadata"))
    if (
        file_format is None
        or payload.get("source", "export") != "export"
        or payload.get("format", file_format) != file_format
        or metadata.get("format", file_format) != file_format
    ):
        raise ProjectNotFoundError(report_id)

    state = _map(_map(project.get("stage_state")).get("reports"))
    historical = state.get("current_snapshot_id") != report_id
    legacy_approved = _legacy_approval(report, payload)
    try:
        header = SavedFileReport(
            project_id=project_id,
            report_id=report_id,
            format=file_format,
            created_at=report.get("created_at"),
            project_revision=report.get("project_revision"),
            legacy_approved=legacy_approved,
            delivery_kind=_delivery_kind(payload, metadata, legacy_approved),
            historical=historical,
            stale=not historical and state.get("stale") is True,
            evidence=None,
        )
    except ValidationError as exc:
        raise ProjectNotFoundError(report_id) from exc
    return header.model_copy(update={"evidence": _report_evidence(snapshots, report, header)})
