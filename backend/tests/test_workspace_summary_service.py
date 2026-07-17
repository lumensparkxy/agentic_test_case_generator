from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from typing import Any, Optional
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import (
    AuthUser,
    OrchestratorActionRecommendation,
    OrchestratorBlocker,
    OrchestratorStageState,
    OrchestratorStatusResponse,
    QaProjectDetail,
    QaProjectExecutionRun,
    QaProjectStageSnapshot,
    QaProjectStageState,
)
from app.services.workspace_summary_service import build_workspace_summary, get_workspace_summary
from app.services.workflow_project_service import list_workspace_projects


BASE_TIME = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)


def _snapshot(
    *,
    project_id: str,
    stage: str,
    snapshot_id: str,
    created_at: datetime,
    payload: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
    approved: bool = False,
    operation: Optional[str] = None,
    source_snapshot_id: Optional[str] = None,
    project_revision: int = 2,
) -> QaProjectStageSnapshot:
    return QaProjectStageSnapshot(
        snapshot_id=snapshot_id,
        project_id=project_id,
        stage=stage,
        version=1,
        project_revision=project_revision,
        operation=operation or f"{stage}.save",
        approved=approved,
        source_snapshot_id=source_snapshot_id,
        metadata=metadata or {},
        payload=payload or {},
        created_at=created_at,
    )


def _project(
    project_id: str,
    *,
    updated_at: datetime = BASE_TIME,
    owner_user_id: str = "user-1",
    status: str = "active",
    stage_state: Optional[dict[str, QaProjectStageState]] = None,
    snapshots: Optional[dict[str, QaProjectStageSnapshot]] = None,
    execution_runs: Optional[list[QaProjectExecutionRun]] = None,
    revision: int = 3,
) -> QaProjectDetail:
    return QaProjectDetail(
        project_id=project_id,
        name=f"Project {project_id}",
        status=status,
        owner_user_id=owner_user_id,
        current_revision=revision,
        created_at=BASE_TIME - timedelta(days=1),
        updated_at=updated_at,
        stage_state=stage_state or {},
        current_snapshots=snapshots or {},
        timeline=[],
        execution_runs=execution_runs or [],
    )


def _stage_state(
    snapshot_id: Optional[str],
    *,
    updated_at: datetime,
    approved: bool = False,
    stale: bool = False,
    operation: str = "stage.save",
    metadata: Optional[dict[str, Any]] = None,
) -> QaProjectStageState:
    return QaProjectStageState(
        current_snapshot_id=snapshot_id,
        version=1 if snapshot_id else 0,
        approved=approved,
        stale=stale,
        updated_at=updated_at,
        operation=operation,
        metadata=metadata or {},
    )


def _status(
    *,
    project_id: str,
    current_stage: str,
    stage_state: OrchestratorStageState,
    actions: Optional[list[OrchestratorActionRecommendation]] = None,
) -> OrchestratorStatusResponse:
    return OrchestratorStatusResponse(
        project_id=project_id,
        project_revision=3,
        current_stage=current_stage,
        stages={current_stage: stage_state},
        next_actions=actions or [],
        generated_at=BASE_TIME,
    )


class FakeSnapshot:
    def __init__(self, payload: Optional[dict[str, Any]], document_id: str = "") -> None:
        self._payload = payload
        self.id = document_id
        self.exists = payload is not None

    def to_dict(self) -> Optional[dict[str, Any]]:
        return dict(self._payload) if self._payload is not None else None


class FakeDocument:
    def __init__(self, path: str, store: dict[str, dict[str, Any]], query_log: list[tuple[Any, ...]]) -> None:
        self.path = path
        self.store = store
        self.query_log = query_log

    def get(self, field_paths: Optional[tuple[str, ...]] = None) -> FakeSnapshot:
        del field_paths
        return FakeSnapshot(self.store.get(self.path), self.path.rsplit("/", 1)[-1])

    def collection(self, name: str) -> "FakeCollection":
        return FakeCollection(f"{self.path}/{name}", self.store, self.query_log)


class FakeCollection:
    def __init__(
        self,
        path: str,
        store: dict[str, dict[str, Any]],
        query_log: list[tuple[Any, ...]],
        *,
        filters: Optional[list[tuple[str, str, Any]]] = None,
        orderings: Optional[list[tuple[str, Any]]] = None,
        max_items: Optional[int] = None,
    ) -> None:
        self.path = path
        self.store = store
        self.query_log = query_log
        self.filters = filters or []
        self.orderings = orderings or []
        self.max_items = max_items

    def _clone(self, **changes: Any) -> "FakeCollection":
        return FakeCollection(
            self.path,
            self.store,
            self.query_log,
            filters=changes.get("filters", list(self.filters)),
            orderings=changes.get("orderings", list(self.orderings)),
            max_items=changes.get("max_items", self.max_items),
        )

    def document(self, document_id: str) -> FakeDocument:
        return FakeDocument(f"{self.path}/{document_id}", self.store, self.query_log)

    def where(self, *, filter: Any) -> "FakeCollection":
        clause = (filter.field_path, filter.op_string, filter.value)
        self.query_log.append(("where", self.path, *clause))
        return self._clone(filters=[*self.filters, clause])

    def order_by(self, field: str, *, direction: Any) -> "FakeCollection":
        self.query_log.append(("order_by", self.path, field, direction))
        return self._clone(orderings=[*self.orderings, (field, direction)])

    def select(self, field_paths: tuple[str, ...]) -> "FakeCollection":
        self.query_log.append(("select", self.path, field_paths))
        return self._clone()

    def limit(self, value: int) -> "FakeCollection":
        self.query_log.append(("limit", self.path, value))
        return self._clone(max_items=value)

    def stream(self):
        prefix = f"{self.path}/"
        rows: list[tuple[str, dict[str, Any]]] = []
        for path, payload in self.store.items():
            if not path.startswith(prefix):
                continue
            remainder = path[len(prefix) :]
            if "/" not in remainder:
                rows.append((remainder, payload))

        for field, operator, expected in self.filters:
            if operator != "==":
                raise AssertionError(f"Unsupported fake query operator: {operator}")
            rows = [(document_id, payload) for document_id, payload in rows if payload.get(field) == expected]

        for field, direction in reversed(self.orderings):
            reverse = "DESCENDING" in str(direction).upper()
            rows.sort(
                key=lambda row: row[0] if field == "__name__" else row[1].get(field),
                reverse=reverse,
            )
        if self.max_items is not None:
            rows = rows[: self.max_items]
        self.query_log.append(("stream", self.path, len(rows)))
        for document_id, payload in rows:
            yield FakeSnapshot(payload, document_id)


class WorkspaceSummaryServiceTests(unittest.TestCase):
    def test_empty_workspace_has_null_continue_and_empty_collections(self) -> None:
        generated_at = BASE_TIME + timedelta(hours=1)

        result = build_workspace_summary([], work_items_limit=50, runs_limit=20, reports_limit=20, generated_at=generated_at)

        self.assertIsNone(result.continue_working)
        self.assertEqual(result.projects, [])
        self.assertEqual(result.work_items, [])
        self.assertEqual(result.recent_runs, [])
        self.assertEqual(result.recent_reports, [])
        self.assertEqual(result.generated_at, generated_at)

    def test_attention_required_use_cases_exposes_snapshot_reason_and_scenario_count(self) -> None:
        requirement_snapshot = _snapshot(
            project_id="project-use-cases",
            stage="requirements",
            snapshot_id="snapshot-requirements",
            created_at=BASE_TIME,
            approved=True,
            payload={"requirements": [{"id": "REQ-1"}]},
        )
        use_case_snapshot = _snapshot(
            project_id="project-use-cases",
            stage="use_cases",
            snapshot_id="snapshot-use-cases",
            created_at=BASE_TIME + timedelta(minutes=5),
            approved=False,
            payload={
                "coverage_plan": [
                    {"requirement_id": "REQ-1", "scenarios": [{"id": "SCN-1"}, {"id": "SCN-2"}]},
                    {"requirement_id": "REQ-2", "scenarios": [{"id": "SCN-3"}]},
                ]
            },
        )
        project = _project(
            "project-use-cases",
            updated_at=BASE_TIME + timedelta(minutes=5),
            stage_state={
                "requirements": _stage_state(
                    requirement_snapshot.snapshot_id,
                    updated_at=BASE_TIME,
                    approved=True,
                    operation="requirements.parse",
                ),
                "use_cases": _stage_state(
                    use_case_snapshot.snapshot_id,
                    updated_at=BASE_TIME + timedelta(minutes=5),
                    approved=False,
                    operation="use_cases.save",
                ),
            },
            snapshots={"requirements": requirement_snapshot, "use_cases": use_case_snapshot},
        )

        result = build_workspace_summary([project], work_items_limit=50, runs_limit=20, reports_limit=20)

        review_item = next(item for item in result.work_items if item.stage == "use_cases")
        self.assertEqual(review_item.kind, "review")
        self.assertEqual(review_item.action, "approve")
        self.assertEqual(review_item.status, "attention_required")
        self.assertTrue(review_item.enabled)
        self.assertTrue(review_item.primary)
        self.assertEqual(review_item.current_snapshot_id, "snapshot-use-cases")
        self.assertEqual(review_item.count, 3)
        self.assertIn("approved", review_item.reason.lower())
        self.assertEqual(result.projects[0].current_stage, "use_cases")
        self.assertEqual(result.projects[0].current_status, "attention_required")

    def test_work_items_are_deduplicated_ranked_and_used_for_continue_working(self) -> None:
        enabled_project = _project("project-a", updated_at=BASE_TIME)
        blocked_project = _project("project-b", updated_at=BASE_TIME + timedelta(hours=2))
        enabled_state = OrchestratorStageState(
            stage="use_cases",
            status="attention_required",
            current_snapshot_id="snapshot-a",
            updated_at=BASE_TIME,
        )
        blocked_state = OrchestratorStageState(
            stage="requirements",
            status="blocked",
            current_snapshot_id="snapshot-b",
            updated_at=BASE_TIME + timedelta(hours=2),
        )
        statuses = {
            "project-a": _status(
                project_id="project-a",
                current_stage="use_cases",
                stage_state=enabled_state,
                actions=[
                    OrchestratorActionRecommendation(
                        action="review",
                        label="Review use cases",
                        stage="use_cases",
                        enabled=True,
                        secondary=True,
                        reason="Secondary review",
                    ),
                    OrchestratorActionRecommendation(
                        action="approve",
                        label="Approve use cases",
                        stage="use_cases",
                        enabled=True,
                        primary=True,
                        reason="Primary approval",
                    ),
                ],
            ),
            "project-b": _status(
                project_id="project-b",
                current_stage="requirements",
                stage_state=blocked_state,
                actions=[
                    OrchestratorActionRecommendation(
                        action="generate",
                        label="Generate",
                        stage="requirements",
                        enabled=False,
                        primary=True,
                        reason="Original blocked reason",
                        blockers=[
                            OrchestratorBlocker(
                                code="missing_approval",
                                message="Requirements approval is missing",
                                stage="requirements",
                                action="generate",
                            )
                        ],
                    )
                ],
            ),
        }

        with patch(
            "app.services.workspace_summary_service.build_orchestrator_status",
            side_effect=lambda project: statuses[project.project_id],
        ):
            result = build_workspace_summary(
                [blocked_project, enabled_project],
                work_items_limit=50,
                runs_limit=20,
                reports_limit=20,
            )

        self.assertEqual(len(result.work_items), 2)
        self.assertEqual(result.work_items[0].project_id, "project-a")
        self.assertEqual(result.work_items[0].action, "approve")
        self.assertEqual(result.work_items[0].reason, "Primary approval")
        self.assertEqual(result.work_items[1].kind, "information")
        self.assertEqual(result.work_items[1].reason, "Requirements approval is missing")
        self.assertEqual(result.continue_working, result.work_items[0])

    def test_run_and_report_summaries_are_bounded_sorted_and_lightweight(self) -> None:
        older_time = BASE_TIME
        newer_time = BASE_TIME + timedelta(hours=1)

        def evidence_project(project_id: str, event_time: datetime) -> QaProjectDetail:
            report_snapshot = _snapshot(
                project_id=project_id,
                stage="reports",
                snapshot_id=f"report-{project_id}",
                created_at=event_time,
                approved=False,
                operation="export.csv",
                source_snapshot_id=f"test-cases-{project_id}",
                project_revision=7,
                metadata={"format": "csv", "execution_run_ids": [f"run-{project_id}"]},
                payload={
                    "source": "export",
                    "format": "csv",
                    "evidence": {"evidence_refs": [{"id": "a"}, {"id": "b"}]},
                    "test_cases": [{"id": "must-not-leak"}],
                },
            )
            execution_run = QaProjectExecutionRun(
                run_record_id=f"run-record-{project_id}",
                project_id=project_id,
                run_id=f"run-{project_id}",
                target_environment="staging",
                project_revision=6,
                test_case_count=4,
                status="failed",
                summary={"passed": 2, "failed": 1, "invalid": 1, "skipped": 1},
                snapshot_id=f"execution-{project_id}",
                source_snapshot_id=f"test-cases-{project_id}",
                selected_test_case_ids=["TC-1", "TC-2", "TC-3"],
                created_at=event_time,
            )
            return _project(
                project_id,
                updated_at=event_time,
                revision=7,
                stage_state={
                    "reports": _stage_state(
                        report_snapshot.snapshot_id,
                        updated_at=event_time,
                        approved=False,
                        stale=True,
                        operation="export.csv",
                    )
                },
                snapshots={"reports": report_snapshot},
                execution_runs=[execution_run],
            )

        older_project = evidence_project("older", older_time)
        newer_project = evidence_project("newer", newer_time)
        status_by_project = {
            project.project_id: _status(
                project_id=project.project_id,
                current_stage="reports",
                stage_state=OrchestratorStageState(stage="reports", status="stale", updated_at=project.updated_at),
            )
            for project in (older_project, newer_project)
        }

        with patch(
            "app.services.workspace_summary_service.build_orchestrator_status",
            side_effect=lambda project: status_by_project[project.project_id],
        ):
            result = build_workspace_summary(
                [older_project, newer_project],
                work_items_limit=50,
                runs_limit=1,
                reports_limit=1,
            )

        self.assertEqual([item.project_id for item in result.recent_runs], ["newer"])
        run = result.recent_runs[0]
        self.assertEqual(run.selected_count, 3)
        self.assertEqual(run.executed_count, 5)
        self.assertEqual((run.passed_count, run.failed_count, run.invalid_count, run.skipped_count), (2, 1, 1, 1))
        self.assertNotIn("summary", run.model_dump())
        self.assertNotIn("artifacts_root", run.model_dump())

        self.assertEqual([item.project_id for item in result.recent_reports], ["newer"])
        report = result.recent_reports[0]
        self.assertEqual(report.report_id, "report-newer")
        self.assertEqual(report.status, "stale")
        self.assertEqual(report.report_type, "export")
        self.assertEqual(report.format, "csv")
        self.assertEqual(report.count, 2)
        self.assertEqual(report.execution_run_ids, ["run-newer"])
        self.assertNotIn("payload", report.model_dump())
        self.assertNotIn("test_cases", report.model_dump())
        self.assertNotIn("evidence_refs", report.model_dump())

    def test_get_workspace_summary_forwards_bounds_to_workspace_query(self) -> None:
        actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        projects = [_project("project-1")]

        with patch(
            "app.services.workspace_summary_service.list_workspace_projects",
            return_value=projects,
        ) as list_projects:
            result = get_workspace_summary(
                actor=actor,
                include_archived=True,
                projects_limit=7,
                work_items_limit=8,
                runs_limit=9,
                reports_limit=10,
            )

        list_projects.assert_called_once_with(
            actor=actor,
            include_archived=True,
            project_limit=7,
            execution_run_limit=9,
        )
        self.assertEqual([project.project_id for project in result.projects], ["project-1"])
        self.assertLessEqual(len(result.work_items), 8)
        self.assertLessEqual(len(result.recent_runs), 9)
        self.assertLessEqual(len(result.recent_reports), 10)

    def test_workspace_project_query_enforces_owner_archive_and_limits(self) -> None:
        store = {
            "qa_projects/owned-new": {
                "project_id": "owned-new",
                "name": "Owned new",
                "status": "active",
                "owner_user_id": "user-1",
                "current_revision": 2,
                "created_at": BASE_TIME,
                "updated_at": BASE_TIME + timedelta(hours=3),
                "stage_state": {},
            },
            "qa_projects/owned-old": {
                "project_id": "owned-old",
                "name": "Owned old",
                "status": "active",
                "owner_user_id": "user-1",
                "current_revision": 2,
                "created_at": BASE_TIME,
                "updated_at": BASE_TIME + timedelta(hours=1),
                "stage_state": {},
            },
            "qa_projects/owned-archived": {
                "project_id": "owned-archived",
                "name": "Owned archived",
                "status": "archived",
                "owner_user_id": "user-1",
                "current_revision": 2,
                "created_at": BASE_TIME,
                "updated_at": BASE_TIME + timedelta(hours=4),
                "stage_state": {},
            },
            "qa_projects/foreign": {
                "project_id": "foreign",
                "name": "Foreign",
                "status": "active",
                "owner_user_id": "user-2",
                "current_revision": 2,
                "created_at": BASE_TIME,
                "updated_at": BASE_TIME + timedelta(hours=5),
                "stage_state": {},
            },
            "qa_projects/owned-new/execution_runs/run-owned": {
                "run_record_id": "run-owned",
                "project_id": "owned-new",
                "run_id": "runtime-owned",
                "target_environment": "staging",
                "project_revision": 2,
                "test_case_count": 1,
                "status": "passed",
                "summary": {"passed": 1},
                "selected_test_case_ids": ["TC-1"],
                "actor_user_id": "user-1",
                "created_at": BASE_TIME + timedelta(hours=2),
            },
            "qa_projects/owned-new/execution_runs/run-foreign-actor": {
                "run_record_id": "run-foreign-actor",
                "project_id": "owned-new",
                "run_id": "runtime-foreign",
                "target_environment": "staging",
                "project_revision": 2,
                "test_case_count": 1,
                "status": "failed",
                "summary": {"failed": 1},
                "selected_test_case_ids": ["TC-foreign"],
                "actor_user_id": "user-2",
                "created_at": BASE_TIME + timedelta(hours=3),
            },
            "qa_projects/owned-new/execution_runs/run-forged-project": {
                "run_record_id": "run-forged-project",
                "project_id": "foreign",
                "run_id": "runtime-forged",
                "target_environment": "staging",
                "project_revision": 2,
                "test_case_count": 1,
                "status": "failed",
                "summary": {"failed": 1},
                "selected_test_case_ids": ["TC-forged"],
                "actor_user_id": "user-1",
                "created_at": BASE_TIME + timedelta(hours=4),
            },
        }
        query_log: list[tuple[Any, ...]] = []
        collection = FakeCollection("qa_projects", store, query_log)
        actor = AuthUser(sub="user-1", email="user@example.com", name="User")

        with patch(
            "app.services.workflow_project_service.get_required_firestore_collection",
            return_value=collection,
        ):
            active = list_workspace_projects(
                actor=actor,
                include_archived=False,
                project_limit=1,
                execution_run_limit=2,
            )
            including_archived = list_workspace_projects(
                actor=actor,
                include_archived=True,
                project_limit=3,
                execution_run_limit=2,
            )

        self.assertEqual([project.project_id for project in active], ["owned-new"])
        self.assertEqual([run.run_record_id for run in active[0].execution_runs], ["run-owned"])
        self.assertEqual(
            [project.project_id for project in including_archived],
            ["owned-archived", "owned-new", "owned-old"],
        )
        self.assertNotIn("foreign", [project.project_id for project in including_archived])
        self.assertIn(("where", "qa_projects", "owner_user_id", "==", "user-1"), query_log)
        self.assertIn(("where", "qa_projects", "status", "==", "active"), query_log)
        self.assertIn(("limit", "qa_projects", 1), query_log)
        execution_limits = [entry for entry in query_log if entry[:2] == ("limit", "qa_projects/owned-new/execution_runs")]
        self.assertTrue(execution_limits)
        self.assertTrue(all(entry[2] == 2 for entry in execution_limits))


if __name__ == "__main__":
    unittest.main()
