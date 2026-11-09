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
from app.services.orchestrator_service import build_orchestrator_status
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
    def __init__(self, payload: Optional[dict[str, Any]], reference: FakeDocument) -> None:
        self._payload = payload
        self.reference = reference
        self.id = reference.path.rsplit("/", 1)[-1]
        self.exists = payload is not None

    def to_dict(self) -> Optional[dict[str, Any]]:
        return dict(self._payload) if self._payload is not None else None


class FakeDocument:
    def __init__(self, path: str, store: dict[str, dict[str, Any]], query_log: list[tuple[Any, ...]]) -> None:
        self.path = path
        self.store = store
        self.query_log = query_log

    def snapshot(self, field_paths: Optional[tuple[str, ...]] = None) -> FakeSnapshot:
        payload = self.store.get(self.path)
        if payload is not None and field_paths is not None:
            projected = {}
            for field in field_paths:
                parts = field.split(".")
                source = payload
                for part in parts:
                    if not isinstance(source, dict) or part not in source:
                        break
                    source = source[part]
                else:
                    target = projected
                    for part in parts[:-1]:
                        target = target.setdefault(part, {})
                    target[parts[-1]] = source
            payload = projected
        return FakeSnapshot(payload, self)

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
            yield FakeSnapshot(payload, self.document(document_id))


class FakeFirestoreClient:
    def __init__(self, store: dict[str, dict[str, Any]], query_log: list[tuple[Any, ...]]) -> None:
        self.store = store
        self.query_log = query_log

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(name, self.store, self.query_log)

    def get_all(self, references: list[FakeDocument], field_paths: tuple[str, ...]):
        self.query_log.append(("get_all", tuple(reference.path for reference in references), field_paths))
        return (reference.snapshot(field_paths) for reference in reversed(references))


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

    def test_persisted_use_case_counts_follow_current_snapshot_and_refresh(self) -> None:
        plan = [{"requirement_id": f"REQ-{i}", "scenarios": [{"id": f"SCN-{i}-{j}"} for j in range(4 if i < 4 else 3)]} for i in range(8)]
        actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        snapshot = _snapshot(
            project_id="counts",
            stage="use_cases",
            snapshot_id="use-cases-1",
            created_at=BASE_TIME,
            payload={"coverage_plan": plan, "private_unneeded_payload": "not projected"},
            metadata={"coverage_plan_count": 8},
        )
        project = _project(
            "counts",
            stage_state={
                "requirements": _stage_state("requirements-1", updated_at=BASE_TIME, approved=True),
                "use_cases": _stage_state(snapshot.snapshot_id, updated_at=BASE_TIME, metadata={"coverage_plan_count": 8}),
            },
        )
        project_data = project.model_dump(exclude={"current_snapshots", "timeline", "execution_runs"})
        store = {
            "qa_projects/counts": project_data,
            "qa_projects/counts/snapshots/use-cases-1": snapshot.model_dump(),
        }
        client = FakeFirestoreClient(store, [])
        with patch("app.services.workflow_project_service.get_required_firestore_client", return_value=client):
            for version, payload, expected_count, expected_groups in [
                (1, {"coverage_plan": plan}, 28, 8),
                (2, {"coverage_plan": [{"scenarios": [{"id": "new"}]}]}, 1, 1),
                (3, {"coverage_plan": []}, 0, 0),
                (4, {}, None, None),
            ]:
                with self.subTest(version=version):
                    snapshot_id = f"use-cases-{version}"
                    store[f"qa_projects/counts/snapshots/{snapshot_id}"] = {
                        **snapshot.model_dump(),
                        "snapshot_id": snapshot_id,
                        "payload": {**payload, "private_unneeded_payload": "not projected"},
                    }
                    project_data["stage_state"]["use_cases"]["current_snapshot_id"] = snapshot_id
                    projects = list_workspace_projects(actor=actor, include_archived=False, project_limit=20, execution_run_limit=20)
                    result = build_workspace_summary(projects, work_items_limit=50, runs_limit=20, reports_limit=20)
                    item = next(item for item in result.work_items if item.stage == "use_cases")
                    self.assertEqual(item.current_snapshot_id, snapshot_id)
                    self.assertEqual(item.count, expected_count)
                    self.assertEqual(item.requirement_group_count, expected_groups)
                    self.assertEqual(result.continue_working.count, expected_count)
                    self.assertNotIn("coverage_plan", item.model_dump())
                    self.assertNotIn("private_unneeded_payload", projects[0].current_snapshots["use_cases"].payload)

    def test_workspace_uses_compact_current_preview_evidence(self) -> None:
        project = _project(
            "preview",
            stage_state={
                "requirements": _stage_state("requirements-1", updated_at=BASE_TIME, approved=True),
                "test_cases": _stage_state("suite-1", updated_at=BASE_TIME, approved=True),
                "execution": _stage_state("preview-1", updated_at=BASE_TIME, approved=True, operation="automation.execution.preview"),
            },
        )
        actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        preview_path = "qa_projects/preview/snapshots/preview-1"
        store = {"qa_projects/preview": project.model_dump(exclude={"current_snapshots", "timeline", "execution_runs"})}
        query_log = []
        client = FakeFirestoreClient(store, query_log)

        with patch("app.services.workflow_project_service.get_required_firestore_client", return_value=client):
            for verified, executable, readiness, expected in (
                (True, 0, False, "report"),
                (False, 0, False, "report"),
                (True, 1, True, "execute"),
                (True, 1, False, "automate"),
            ):
                with self.subTest(verified=verified, executable=executable, readiness=readiness):
                    counts = {"executable": executable, "manual": 1 - executable, "unsupported": 0, "invalid": 0}
                    preview = _snapshot(
                        project_id="preview",
                        stage="execution",
                        snapshot_id="preview-1",
                        created_at=BASE_TIME,
                        operation="automation.execution.preview",
                        source_snapshot_id="suite-1",
                        metadata={"preview_response_fingerprint": "f" * 64} if verified else None,
                        payload={
                            "summary": counts,
                            "candidate_counts": counts,
                            "candidates": {bucket: [{"id": "TC-1"}] * count for bucket, count in counts.items()},
                            "readiness": {"run_allowed": readiness},
                            "reviewed_full_suite": True,
                            "specs": [{"id": "TC-1"}],
                            "results": [{"id": "TC-1"}],
                        },
                    )
                    store[preview_path] = preview.model_dump()
                    project.current_snapshots["execution"] = preview
                    query_log.clear()

                    projects = list_workspace_projects(actor=actor, include_archived=False, project_limit=20, execution_run_limit=20)
                    summary = build_workspace_summary(projects, work_items_limit=50, runs_limit=20, reports_limit=20)
                    self.assertEqual(build_orchestrator_status(project).next_actions[0].action, expected)
                    self.assertEqual(summary.continue_working.action, expected)
                    compact = projects[0].current_snapshots["execution"].payload
                    self.assertNotIn("specs", compact)
                    self.assertNotIn("results", compact)
                    self.assertEqual("candidates" in compact, not verified)
                    reads = [entry[2] for entry in query_log if entry[0] == "get_all" and preview_path in entry[1]]
                    self.assertEqual(len(reads), 1 if verified else 2)
                    if not verified:
                        self.assertEqual(reads[-1], ("payload.candidates",))

    def test_workspace_batches_projected_snapshots_by_mask_and_matches_exact_pointers(self) -> None:
        actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        store: dict[str, dict[str, Any]] = {}
        stage_state = {
            "requirements": _stage_state("shared-id", updated_at=BASE_TIME),
            "context": _stage_state("shared-id", updated_at=BASE_TIME),
            "use_cases": _stage_state("plan", updated_at=BASE_TIME),
            "impact_analysis": _stage_state("missing", updated_at=BASE_TIME),
            "test_cases": _stage_state("wrong-id", updated_at=BASE_TIME),
            "execution": _stage_state("preview", updated_at=BASE_TIME),
            "reports": _stage_state("wrong-owner", updated_at=BASE_TIME),
        }
        for project_id, owner, status in (
            ("a", actor.sub, "active"),
            ("b", actor.sub, "active"),
            ("archived", actor.sub, "archived"),
            ("foreign", "user-2", "active"),
        ):
            project = _project(project_id, owner_user_id=owner, status=status, stage_state=stage_state)
            project_path = f"qa_projects/{project_id}"
            store[project_path] = project.model_dump(exclude={"current_snapshots", "timeline", "execution_runs"})
            snapshots = {
                "shared-id": _snapshot(project_id=project_id, stage="requirements", snapshot_id="shared-id", created_at=BASE_TIME),
                "plan": _snapshot(project_id=project_id, stage="use_cases", snapshot_id="plan", created_at=BASE_TIME),
                "wrong-id": _snapshot(project_id=project_id, stage="test_cases", snapshot_id="other", created_at=BASE_TIME),
                "wrong-owner": _snapshot(project_id="foreign", stage="reports", snapshot_id="wrong-owner", created_at=BASE_TIME),
                "preview": _snapshot(
                    project_id=project_id,
                    stage="execution",
                    snapshot_id="preview",
                    created_at=BASE_TIME,
                    operation="automation.execution.preview",
                    payload={"candidate_counts": {"manual": 1}, "candidates": {"manual": [{"id": project_id}]}},
                ),
            }
            store.update({f"{project_path}/snapshots/{document_id}": snapshot.model_dump() for document_id, snapshot in snapshots.items()})

        query_log: list[tuple[Any, ...]] = []
        client = FakeFirestoreClient(store, query_log)
        with patch("app.services.workflow_project_service.get_required_firestore_client", return_value=client):
            projects = list_workspace_projects(actor=actor, include_archived=False, project_limit=20, execution_run_limit=20)

        self.assertEqual([project.project_id for project in projects], ["a", "b"])
        for project in projects:
            self.assertEqual(set(project.current_snapshots), {"requirements", "use_cases", "execution"})
            self.assertEqual(project.current_snapshots["execution"].payload["candidates"]["manual"], [{"id": project.project_id}])

        batches = [entry[1:] for entry in query_log if entry[0] == "get_all"]
        self.assertEqual(len(batches), 4)
        self.assertEqual(sorted(len(paths) for paths, _ in batches), [2, 2, 2, 8])
        for paths, fields in batches:
            self.assertEqual({path.split("/")[1] for path in paths}, {"a", "b"})
            self.assertEqual(len(set(paths)), len(paths))
            self.assertEqual("payload.coverage_plan" in fields, all(path.endswith("/plan") for path in paths))
            self.assertTrue({"payload.specs", "payload.results"}.isdisjoint(fields))
        self.assertEqual(batches[-1][1], ("payload.candidates",))

    def test_workspace_recovers_only_verified_legacy_preview_report_staleness(self) -> None:
        actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        project_id = "preview-report"
        path = f"qa_projects/{project_id}"
        suite = _snapshot(project_id=project_id, stage="test_cases", snapshot_id="suite", created_at=BASE_TIME)
        report = _snapshot(
            project_id=project_id,
            stage="reports",
            snapshot_id="report",
            operation="reports.execution_summary",
            source_snapshot_id="execution-run",
            project_revision=4,
            approved=True,
            created_at=BASE_TIME,
            payload={"source": "execution", "run_id": "run-1", "status": "passed"},
        )
        earlier_preview = _snapshot(
            project_id=project_id,
            stage="execution",
            snapshot_id="preview-5",
            operation="automation.execution.preview",
            source_snapshot_id=suite.snapshot_id,
            project_revision=5,
            created_at=BASE_TIME,
        )
        current_preview = earlier_preview.model_copy(update={"snapshot_id": "preview-6", "project_revision": 6})
        report_state = _stage_state(report.snapshot_id, updated_at=BASE_TIME, approved=True, stale=True)
        report_state.stale_reason = "execution changed in project revision 5"
        project = _project(
            project_id,
            revision=6,
            stage_state={
                "test_cases": _stage_state(suite.snapshot_id, updated_at=BASE_TIME, approved=True),
                "reports": report_state,
                "execution": _stage_state(current_preview.snapshot_id, updated_at=BASE_TIME, approved=True, operation=current_preview.operation),
            },
        )
        run = QaProjectExecutionRun(
            run_record_id="record-1",
            project_id=project_id,
            run_id="run-1",
            target_environment="staging",
            project_revision=3,
            status="passed",
            snapshot_id="execution-run",
            source_snapshot_id=suite.snapshot_id,
            actor_user_id=actor.sub,
            created_at=BASE_TIME,
        )
        store = {
            path: project.model_dump(exclude={"current_snapshots", "timeline", "execution_runs"}),
            **{f"{path}/snapshots/{snapshot.snapshot_id}": snapshot.model_dump() for snapshot in (suite, report, earlier_preview, current_preview)},
            f"{path}/execution_runs/{run.run_record_id}": run.model_dump(),
        }
        query_log = []
        client = FakeFirestoreClient(store, query_log)
        earlier_path = f"{path}/snapshots/{earlier_preview.snapshot_id}"
        with patch("app.services.workflow_project_service.get_required_firestore_client", return_value=client):
            projects = list_workspace_projects(actor=actor, include_archived=False, project_limit=1, execution_run_limit=1)
            summary = build_workspace_summary(projects, work_items_limit=10, runs_limit=1, reports_limit=1)
            self.assertEqual(summary.recent_reports[0].status, "approved")
            self.assertEqual(summary.recent_reports[0].execution_run_ids, ["run-1"])
            self.assertIn(("select", f"{path}/snapshots", ("project_id", "stage", "source_snapshot_id")), query_log)
            self.assertIn(("limit", f"{path}/snapshots", 1), query_log)
            self.assertTrue(store[path]["stage_state"]["reports"]["stale"])

            store[earlier_path]["operation"] = "automation.execution.run"
            projects = list_workspace_projects(actor=actor, include_archived=False, project_limit=1, execution_run_limit=1)
            summary = build_workspace_summary(projects, work_items_limit=10, runs_limit=1, reports_limit=1)
            self.assertEqual(summary.recent_reports[0].status, "stale")

            store[earlier_path]["operation"] = earlier_preview.operation
            store[f"{path}/execution_runs/{run.run_record_id}"]["status"] = "failed"
            projects = list_workspace_projects(actor=actor, include_archived=False, project_limit=1, execution_run_limit=1)
            self.assertTrue(projects[0].stage_state["reports"].stale)

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
                summary={"passed": 2, "failed": 1, "flaky": 1, "invalid": 1, "skipped": 1},
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
        self.assertEqual(run.executed_count, 6)
        self.assertEqual(run.flaky_count, 1)
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

    def test_reviewed_design_report_classification_uses_bounded_saved_metadata(self) -> None:
        actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        report = _snapshot(
            project_id="design",
            stage="reports",
            snapshot_id="report-v1",
            created_at=BASE_TIME,
            operation="export.json",
            source_snapshot_id="exact-suite-v1",
            metadata={"delivery_kind": "reviewed_design", "test_case_count": 1},
            payload={"source": "export", "format": "json", "audit_metadata": {"large_private_field": "not needed"}},
        )
        project = _project(
            "design", stage_state={"reports": _stage_state(report.snapshot_id, updated_at=BASE_TIME, approved=False, operation=report.operation)}
        )
        project_row = project.model_dump(exclude={"current_snapshots", "timeline", "execution_runs"})
        snapshot_path = "qa_projects/design/snapshots/report-v1"
        store = {"qa_projects/design": project_row, snapshot_path: report.model_dump()}
        queries = []
        client = FakeFirestoreClient(store, queries)
        with patch("app.services.workflow_project_service.get_required_firestore_client", return_value=client):
            for stale, recorded_kind, expected_kind in (
                (False, "reviewed_design", "reviewed_design"),
                (True, "reviewed_design", "reviewed_design"),
                (False, "unrecognized", None),
            ):
                with self.subTest(stale=stale, recorded_kind=recorded_kind):
                    store[snapshot_path]["metadata"]["delivery_kind"] = recorded_kind
                    project_row["stage_state"]["reports"]["stale"] = stale
                    projects = list_workspace_projects(actor=actor, include_archived=False, project_limit=20, execution_run_limit=20)
                    self.assertFalse(projects[0].design_delivery_enabled)
                    self.assertNotIn("audit_metadata", projects[0].current_snapshots["reports"].payload)
                    result = build_workspace_summary(projects, work_items_limit=50, runs_limit=20, reports_limit=20)
                    [item] = result.recent_reports
                    self.assertEqual(item.delivery_kind, expected_kind)
                    self.assertEqual(item.report_id, "report-v1")
                    self.assertEqual(item.source_snapshot_id, "exact-suite-v1")
                    self.assertEqual(item.status, "stale" if stale else "draft")
                    self.assertFalse(item.approved)
        report_reads = [entry[2] for entry in queries if entry[0] == "get_all" and snapshot_path in entry[1]]
        self.assertTrue(report_reads)
        self.assertTrue(all("metadata" in fields and "payload.audit_metadata" not in fields for fields in report_reads))

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
        client = FakeFirestoreClient(store, query_log)
        actor = AuthUser(sub="user-1", email="user@example.com", name="User")

        with patch(
            "app.services.workflow_project_service.get_required_firestore_client",
            return_value=client,
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
