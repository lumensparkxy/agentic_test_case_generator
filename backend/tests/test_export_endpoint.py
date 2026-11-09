from copy import deepcopy
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app, get_current_user
from app.agents.export_agent import export_to_excel
from app.models import AuthUser, TestCase
from app.contracts.projects import QaProjectDetail
from app.routers import export as routes
from app.services import workflow_project_service as projects
from app.services.workflow_project_service import ProjectPermissionError
from test_use_case_review_service import FakeFirestoreClient, _run_transaction


def _test_case():
    return {
        "id": "TC-001",
        "title": '=HYPERLINK("https://example.test")',
        "status": "Ready",
        "steps": [{"step": 1, "action": "Open checkout", "expected": "Checkout is visible"}],
        "linked_requirement_ids": ["REQ-001"],
        "scenario_refs": ["SCN-1"],
    }


def _project(approved=False):
    now = datetime.now(timezone.utc)

    def snapshot(stage, identity, payload, **kwargs):
        return {
            "snapshot_id": identity,
            "project_id": "project-1",
            "stage": stage,
            "version": 2,
            "project_revision": 9,
            "operation": "fixture",
            "created_at": now,
            "payload": payload,
            **kwargs,
        }

    return QaProjectDetail(
        project_id="project-1",
        name="Export QA",
        owner_user_id="export-user",
        current_revision=9,
        created_at=now,
        updated_at=now,
        stage_state={"test_cases": {"current_snapshot_id": "tests-2", "approved": approved, "version": 2}},
        current_snapshots={
            "requirements": snapshot(
                "requirements",
                "req-2",
                {
                    "requirements": [
                        {
                            "id": "REQ-001",
                            "text": "Visible checkout",
                            "original_requirement_ids": ["ROOM-101"],
                            "sources": [
                                {
                                    "source_id": "source-1",
                                    "source_version": "hash-1",
                                    "import_id": "import-1",
                                    "label": "Room",
                                    "excerpt": "+SUM(1,2) Zürich",
                                    "excerpt_verified": True,
                                    "original_requirement_ids": ["ROOM-101"],
                                }
                            ],
                        },
                        {"id": "REQ-002", "text": "Cancel checkout", "sources": []},
                    ]
                },
            ),
            "test_cases": snapshot(
                "test_cases",
                "tests-2",
                {
                    "test_cases": [_test_case()],
                    "review": {"approved": approved, "score": 100 if approved else 55, "threshold": 90},
                    "generation_tasks": [] if approved else [{"reason": "Cancellation missing"}],
                    "coverage_plan": [{"requirement_id": "REQ-001", "scenarios": [{"id": "SCN-1"}]}]
                    if approved
                    else [{"requirement_id": "REQ-001", "scenarios": [{"id": "SCN-1"}, {"id": "SCN-2"}]}],
                },
                approved=approved,
                metadata={"source_requirements_snapshot_id": "req-2"},
            ),
        },
    )


def _payload(**kwargs):
    return {
        "project_id": "project-1",
        "base_project_revision": 9,
        "source_snapshot_id": "tests-2",
        "test_cases": [_test_case()],
        "approved": True,
        "review": {"approved": True},
        "draft_override_requested": True,
        "draft_override_reason": "@review Zürich",
        **kwargs,
    }


class ExportEndpointTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_current_user] = lambda: AuthUser(sub="export-user", name="Export QA")
        self.addCleanup(app.dependency_overrides.clear)
        for target, result in (("start_workflow_run", "workflow-1"), ("complete_workflow_run", None), ("record_usage_event", "event-1")):
            patcher = patch(f"app.routers.export.{target}", return_value=result)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.enterContext(patch.object(routes, "find_idempotent_stage_snapshot", return_value=None))
        self.enterContext(patch.object(routes, "get_required_firestore_client", return_value=FakeFirestoreClient({})))

    def export(self, format="json", payload=None, project=None):
        with (
            patch("app.routers.export.get_project", return_value=project or _project()) as get,
            patch(
                "app.routers.export.append_stage_snapshot",
                side_effect=lambda **kwargs: SimpleNamespace(workflow_run_id=kwargs["workflow_run_id"], metadata=kwargs["metadata"]),
            ) as append,
            TestClient(app) as client,
        ):
            result = client.post(f"/export/{format}", json=payload or _payload())
        return result, append, get

    def test_all_formats_preserve_draft_evidence_and_case_identity(self):
        for format in ("json", "csv", "excel"):
            with self.subTest(format=format):
                response, append, get = self.export(format)
                self.assertEqual(response.status_code, 200, response.text[:300] if format != "excel" else response.status_code)
                metadata = append.call_args.kwargs["payload"]["audit_metadata"]
                self.assertTrue(metadata["is_draft"])
                self.assertEqual(metadata["machine_review_status"], "failed")
                self.assertEqual(metadata["human_review_status"], "unavailable")
                self.assertFalse(metadata["suite_complete"])
                self.assertEqual(metadata["unresolved_task_count"], 1)
                self.assertEqual(metadata["coverage"]["requirements"]["missing_ids"], ["REQ-002"])
                self.assertEqual(metadata["coverage"]["scenarios"]["missing_ids"], ["SCN-2"])
                self.assertIsNone(metadata["coverage"]["behavioral_assessment"])
                self.assertEqual(metadata["source_mappings"][0]["original_requirement_ids"], ["ROOM-101"])
                self.assertEqual(metadata["execution_status"], "not_executed")
                self.assertEqual(metadata["snapshot_id"], "tests-2")
                self.assertEqual(metadata["draft_override_reason"], "@review Zürich")
                self.assertFalse(append.call_args.kwargs["approved"])
                self.assertEqual(append.call_args.kwargs["source_snapshot_id"], "tests-2")
                get.assert_called_once()
                if format == "json":
                    body = response.json()
                    self.assertEqual(body["export_format"], "test_cases_v1")
                    self.assertEqual(body["total_count"], 1)
                    self.assertEqual(body["test_cases"][0]["title"], _test_case()["title"])
                    self.assertEqual(body["test_cases"][0]["status"], "Ready")
                    self.assertTrue(body["audit_metadata"]["is_draft"])
                elif format == "csv":
                    rows = list(csv.DictReader(io.StringIO(response.text)))
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["ID"], "TC-001")
                    self.assertTrue(rows[0]["Title"].startswith("'="))
                    self.assertEqual(rows[0]["Audit draft_override_reason"], "'@review Zürich")
                    self.assertEqual(json.loads(rows[0]["Audit coverage"])["scenarios"]["missing_ids"], ["SCN-2"])
                else:
                    wb = load_workbook(io.BytesIO(response.content))
                    self.assertEqual(wb.sheetnames, ["Test Cases", "Audit", "Sources", "Coverage"])
                    self.assertEqual(wb["Test Cases"]["A2"].value, "TC-001")
                    self.assertEqual(wb["Test Cases"]["F2"].value, "Ready")
                    self.assertNotEqual(wb["Test Cases"]["B2"].data_type, "f")
                    audit = {row[0].value: row[1].value for row in wb["Audit"].iter_rows(min_row=2)}
                    self.assertEqual(audit["machine_review_status"], "failed")
                    self.assertEqual(audit["draft_override_reason"], "'@review Zürich")
                    self.assertEqual(wb["Sources"]["F2"].value, "'+SUM(1,2) Zürich")
                    self.assertFalse(any(cell.data_type == "f" for sheet in wb for row in sheet for cell in row))

    def test_approved_export_uses_matching_saved_review_not_client_claim(self):
        project = _project(True)
        project.current_snapshots["requirements"].payload["requirements"].pop()
        payload = _payload(approved=False, review={"approved": False}, draft_override_requested=False, draft_override_reason=None)
        response, append, _ = self.export(payload=payload, project=project)
        self.assertEqual(response.status_code, 200, response.text)
        metadata = response.json()["audit_metadata"]
        self.assertFalse(metadata["is_draft"])
        self.assertEqual(metadata["machine_review_status"], "approved")
        self.assertEqual(metadata["snapshot_version"], 2)
        self.assertTrue(append.call_args.kwargs["approved"])
        self.assertEqual(metadata["human_review_status"], "unavailable")

    def test_forged_approval_empty_reason_and_unknown_review_cannot_bypass_draft_gate(self):
        for variant in ("override_off", "empty_reason", "unknown_review", "incomplete_approved"):
            project = _project(variant == "incomplete_approved")
            payload = _payload(draft_override_requested=False)
            if variant == "empty_reason":
                payload.update(draft_override_requested=True, draft_override_reason=" \n ")
            if variant == "unknown_review":
                del project.current_snapshots["test_cases"].payload["review"]
            response, append, _ = self.export(payload=payload, project=project)
            self.assertEqual(response.status_code, 422, (variant, response.text))
            append.assert_not_called()

    def test_stale_mismatched_and_forged_cases_conflict_before_export(self):
        for variant in ("revision", "missing_revision", "snapshot", "case", "stale", "old_source", "unknown_snapshot", "unreadable_snapshot"):
            project = _project()
            payload = _payload()
            if variant == "revision":
                payload["base_project_revision"] = 8
            if variant == "missing_revision":
                payload.pop("base_project_revision")
            if variant == "snapshot":
                payload["source_snapshot_id"] = "old"
            if variant == "case":
                payload["test_cases"][0]["title"] = "Forged"
            if variant == "stale":
                project.stage_state["test_cases"].stale = True
            if variant == "old_source":
                project.current_snapshots["test_cases"].metadata["source_requirements_snapshot_id"] = "old"
            if variant in {"unknown_snapshot", "unreadable_snapshot"}:
                project.current_snapshots.pop("test_cases")
                if variant == "unreadable_snapshot":
                    payload.pop("source_snapshot_id")
            response, append, _ = self.export(payload=payload, project=project)
            self.assertEqual(response.status_code, 409, (variant, response.text))
            append.assert_not_called()

    def test_unbound_draft_and_legacy_provenance_are_explicitly_unknown(self):
        payload = _payload(project_id=None, source_snapshot_id=None)
        response, append, get = self.export(payload=payload)
        self.assertEqual(response.status_code, 200, response.text)
        metadata = response.json()["audit_metadata"]
        self.assertIsNone(metadata["snapshot_id"])
        self.assertIsNone(metadata["suite_complete"])
        self.assertEqual(metadata["machine_review_status"], "unavailable")
        self.assertEqual(metadata["source_mappings"], [])
        self.assertEqual(metadata["execution_status"], "unknown")
        get.assert_not_called()
        append.assert_not_called()
        project = _project()
        project.current_snapshots["test_cases"].metadata.clear()
        response, _, _ = self.export(project=project)
        self.assertEqual(response.json()["audit_metadata"]["source_mappings"], [])

    def test_only_matching_execution_runs_are_evidence(self):
        project = _project()
        now = datetime.now(timezone.utc)
        from app.contracts.projects import QaProjectExecutionRun

        project.execution_runs = [
            QaProjectExecutionRun(
                run_record_id="r",
                project_id="project-1",
                run_id="past-run",
                target_environment="qa",
                project_revision=7,
                status="passed",
                source_snapshot_id="old-tests",
                created_at=now,
            )
        ]
        response, _, _ = self.export(project=project)
        self.assertEqual(response.json()["audit_metadata"]["execution_run_ids"], [])
        self.assertEqual(response.json()["audit_metadata"]["execution_status"], "unknown")
        project.execution_runs[0].source_snapshot_id = "tests-2"
        project.execution_runs[0].status = "failed"
        response, append, _ = self.export(project=project)
        self.assertEqual(response.json()["audit_metadata"]["execution_status"], "failed")
        self.assertEqual(append.call_args.kwargs["payload"]["evidence"]["execution_run_ids"], ["past-run"])

    def test_unauthorized_project_cannot_export(self):
        with (
            patch("app.routers.export.get_project", side_effect=ProjectPermissionError("project-1")),
            patch("app.routers.export.export_to_json") as export,
            TestClient(app) as client,
        ):
            response = client.post("/export/json", json=_payload())
        self.assertEqual(response.status_code, 403)
        export.assert_not_called()

    def test_human_review_is_independent_and_bound_to_the_same_snapshot(self):
        project = _project(True)
        project.current_snapshots["requirements"].payload["requirements"].pop()
        project.stage_state["test_cases"].metadata["latest_human_review"] = {"snapshot_id": "tests-2", "decision": "request_changes"}
        response, _, _ = self.export(project=project)
        self.assertTrue(response.json()["audit_metadata"]["is_draft"])
        self.assertEqual(response.json()["audit_metadata"]["human_review_status"], "changes_requested")
        self.assertEqual(response.json()["audit_metadata"]["machine_review_status"], "approved")
        project.stage_state["test_cases"].metadata["latest_human_review"]["snapshot_id"] = "older"
        response, _, _ = self.export(project=project)
        self.assertEqual(response.json()["audit_metadata"]["human_review_status"], "unavailable")
        project.stage_state["test_cases"].metadata["latest_human_review"] = {"snapshot_id": "tests-2", "decision": "approve"}
        response, _, _ = self.export(project=project)
        self.assertEqual(response.json()["audit_metadata"]["human_review_status"], "approved")
        response, append, _ = self.export(payload=_payload(project_id=None, source_snapshot_id=None, draft_override_requested=False))
        self.assertEqual(response.status_code, 422)
        append.assert_not_called()

    def test_placeholder_remains_blocked_with_override(self):
        payload = _payload(project_id=None, source_snapshot_id=None)
        payload["test_cases"][0]["steps"] = []
        response, append, _ = self.export(payload=payload)
        self.assertEqual(response.status_code, 422, response.text)
        append.assert_not_called()


class ExportPublicationTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.firestore = FakeFirestoreClient(self.store)
        self.actor = AuthUser(sub="export-owner", name="Export owner")
        self.enterContext(patch.dict(app.dependency_overrides, {get_current_user: lambda: self.actor}))
        self.enterContext(patch.object(projects, "get_required_firestore_collection", return_value=self.firestore.collection("qa_projects")))
        self.enterContext(patch.object(projects, "get_required_firestore_client", return_value=self.firestore))
        self.enterContext(patch.object(projects, "transactional", side_effect=_run_transaction))
        self.enterContext(patch.object(routes, "get_required_firestore_client", return_value=self.firestore))
        self.enterContext(
            patch("app.services.audit_repository.get_optional_firestore_collection", side_effect=lambda name, **kwargs: self.firestore.collection(name))
        )
        self.project_id = projects.create_project(name="Report checks", description=None, actor=self.actor, request_id="seed").project_id
        source = _project(True).current_snapshots
        requirements = self.save("requirements", {"requirements": source["requirements"].payload["requirements"][:1]})
        self.suite = self.save("test_cases", source["test_cases"].payload, metadata={"source_requirements_snapshot_id": requirements.snapshot_id})
        self.save("reports", {"source": "export", "format": "json"})
        self.before = self.project()
        self.input = {
            "project_id": self.project_id,
            "base_project_revision": self.before.current_revision,
            "source_snapshot_id": self.suite.snapshot_id,
            "test_cases": self.suite.payload["test_cases"],
        }
        self.http = self.enterContext(TestClient(app))

    def save(self, stage, payload, *, operation="seed", approved=True, **kwargs):
        return projects.append_stage_snapshot(
            project_id=self.project_id,
            stage=stage,
            payload=payload,
            operation=operation,
            actor=self.actor,
            request_id=f"seed-{stage}",
            approved=approved,
            **kwargs,
        )

    def save_preview(self):
        return self.save("execution", {"summary": {"manual": 1}}, operation="automation.execution.preview", source_snapshot_id=self.suite.snapshot_id)

    def project(self):
        return projects.get_project(self.project_id, actor=self.actor)

    def post(self, fmt="json", *, request_id="export", **changes):
        return self.http.post(f"/export/{fmt}", headers={"X-Request-ID": request_id}, json={**self.input, **changes})

    def audit(self, collection):
        return [value for path, value in self.store.items() if path.startswith(f"{collection}/")]

    def assert_audit(self, status, quantity, operation="export.json"):
        [event] = self.audit("usage_events")
        [workflow] = self.audit("workflow_runs")
        self.assertEqual((event["status"], event["quantity"], workflow["status"]), (status, quantity, status))
        self.assertEqual(event["event_type"], operation)
        self.assertEqual(event["workflow_run_id"], workflow["run_id"])
        return event

    def test_all_formats_replay_original_evidence_without_additional_publication(self):
        before = self.before
        for fmt in ("json", "csv", "excel"):
            with self.subTest(format=fmt):
                first = self.post(fmt, base_project_revision=before.current_revision)
                self.assertEqual(first.status_code, 200, first.status_code)
                after = self.project()
                snapshot = after.current_snapshots["reports"]
                self.assertEqual(after.current_revision, before.current_revision + 1)
                self.assertEqual(after.current_snapshots["test_cases"], before.current_snapshots["test_cases"])
                event = next(event for event in self.audit("usage_events") if event["event_id"] == snapshot.source_event_id)
                self.assertEqual((event["quantity"], event["status"], event["workflow_run_id"]), (1, "completed", snapshot.workflow_run_id))
                self.assertEqual(event["metadata"]["exported_at"], snapshot.payload["audit_metadata"]["exported_at"])

                self.save_preview()
                previewed = self.project()
                self.assertEqual(previewed.stage_state["reports"], after.stage_state["reports"])
                self.assertEqual(previewed.current_snapshots["reports"], snapshot)
                written = deepcopy(self.store)
                replay = self.post(fmt, base_project_revision=before.current_revision)
                self.assertEqual(replay.status_code, 200, replay.status_code)
                self.assertEqual(replay.headers["content-disposition"], first.headers["content-disposition"])
                if fmt == "excel":
                    books = [load_workbook(io.BytesIO(reply.content)) for reply in (first, replay)]
                    self.assertEqual(books[0].sheetnames, books[1].sheetnames)
                    for name in books[0].sheetnames:
                        self.assertEqual(list(books[0][name].values), list(books[1][name].values))
                else:
                    self.assertEqual(replay.content, first.content)
                self.assertEqual(self.post(fmt, base_project_revision=before.current_revision, draft_override_reason="Changed").status_code, 409)
                self.assertEqual(self.store, written)
                before = previewed
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(len(self.audit("usage_events")), 3)
        self.assertEqual(len(self.audit("workflow_runs")), 3)

    def test_legacy_preview_staleness_only_recovers_with_current_evidence(self):
        first = self.post()
        self.assertEqual(first.status_code, 200, first.text)
        preview = self.save_preview()
        header = self.store[f"qa_projects/{self.project_id}"]
        header["stage_state"]["reports"].update(stale=True, stale_reason=f"execution changed in project revision {preview.project_revision}")

        written = deepcopy(self.store)
        status = self.project().stage_state["reports"]
        self.assertFalse(status.stale)
        self.assertIsNone(status.stale_reason)
        replay = self.post()
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.content, first.content)
        self.assertEqual(self.store, written)

        projects.record_execution_run(
            project_id=self.project_id,
            actor=self.actor,
            request_id="late-run",
            run_id="new-runtime-evidence",
            target_environment="test",
            status_value="passed",
            summary={"passed": 1},
            test_case_count=1,
            snapshot_id=None,
            workflow_run_id=None,
            source_event_id=None,
            project_revision=preview.project_revision - 1,
            source_snapshot_id=self.suite.snapshot_id,
            historical=True,
        )
        self.assertTrue(self.project().stage_state["reports"].stale)
        self.assertEqual(self.post().status_code, 409)
        self.assert_audit("completed", 1)

    def test_preview_preserves_draft_and_existing_stale_report_state(self):
        report = self.save("reports", {"source": "export", "format": "json"}, approved=False)
        draft_state = self.project().stage_state["reports"]
        self.save_preview()
        self.assertEqual(self.project().stage_state["reports"], draft_state)
        self.save("context", {"app_link": "https://example.test/changed"})
        stale_state = self.project().stage_state["reports"]
        self.assertTrue(stale_state.stale)
        self.assertIn("context changed", stale_state.stale_reason)
        self.save_preview()
        detail = self.project()
        self.assertEqual(detail.stage_state["reports"], stale_state)
        self.assertEqual(detail.current_snapshots["reports"], report)

    def test_postcommit_reply_loss_recovers_the_exact_saved_report(self):
        publish = routes.append_stage_snapshot

        def lose_reply(**kwargs):
            publish(**kwargs)
            raise RuntimeError("Synthetic report commit reply lost")

        with patch.object(routes, "append_stage_snapshot", side_effect=lose_reply):
            first = self.post()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.post().content, first.content)
        event = self.assert_audit("completed", 1)
        self.assertEqual(self.project().current_snapshots["reports"].source_event_id, event["event_id"])
        self.save("test_cases", self.suite.payload)
        self.assertEqual(self.post().status_code, 409)
        self.assert_audit("completed", 1)

    def test_project_revision_changed_during_file_render_leaves_only_zero_usage(self):
        render = routes.export_to_json

        def revise(cases, *, audit_metadata):
            projects.update_project(project_id=self.project_id, actor=self.actor, request_id="rename", name="Renamed during export")
            return render(cases, audit_metadata=audit_metadata)

        with patch.object(routes, "export_to_json", side_effect=revise):
            result = self.post()
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.project().current_snapshots, self.before.current_snapshots)
        self.assert_audit("failed", 0)

    def test_report_write_failure_rolls_back_completed_usage_and_keeps_the_prior_report(self):
        self.firestore.fail_on_create_segment = "/snapshots/"
        result = self.post()
        self.assertEqual(result.status_code, 503, result.text)
        self.assertEqual(self.project(), self.before)
        transaction = self.firestore.transactions[-1]
        self.assertEqual([path.split("/")[0] for action, path in transaction.operations if action == "set"], ["usage_events", "workflow_runs"])
        self.assert_audit("failed", 0)

    def test_foreign_and_archived_projects_are_blocked_before_render_and_audit(self):
        owner = self.actor
        with patch.object(routes, "export_to_json") as render:
            self.actor = AuthUser(sub="foreign-user", name="Foreign user")
            self.assertEqual(self.post().status_code, 403)
            self.actor = owner
            archived = projects.update_project(project_id=self.project_id, actor=owner, request_id="archive", status_value="archived")
            self.assertEqual(self.post(base_project_revision=archived.current_revision).status_code, 409)
            render.assert_not_called()
        self.assertFalse(self.audit("usage_events"))
        self.assertFalse(self.audit("workflow_runs"))


class ExcelExportValueTests(unittest.TestCase):
    def workbook(self, *, cases, audit=None):
        return load_workbook(io.BytesIO(export_to_excel(cases, audit_metadata=audit)))

    def continued_value(self, workbook, sheet_name, address):
        source = workbook[sheet_name][address]
        detail = workbook["Long Values"]
        self.assertTrue(source.value.startswith("See Long Values"))
        self.assertIsNone(source.hyperlink.target)
        matching = [row for row in detail.iter_rows(min_row=2) if row[0].value == sheet_name and row[1].value == address]
        self.assertTrue(matching)
        self.assertEqual(source.hyperlink.location, f"'Long Values'!A{matching[0][0].row}")
        self.assertEqual([row[2].value for row in matching], list(range(1, len(matching) + 1)))
        for row in matching:
            self.assertEqual(row[4].data_type, "s")
            self.assertLessEqual(len(row[4].value.encode("utf-16-le")) // 2, 32_767)
            self.assertLessEqual(row[4].value.count("\n"), 253)
        text = "".join(row[4].value for row in matching)
        return json.loads(text) if matching[0][3].value == "JSON string" else text

    def test_long_audit_source_coverage_and_steps_reassemble_exactly(self):
        versions = [
            {
                "case_id": f"TC-{index:03d}",
                "artifact_item_id": f"{index:036d}",
                "artifact_version_id": f"{index + 1:036d}",
                "artifact_version_number": 2,
            }
            for index in range(260)
        ]
        excerpt = "=Évidence 東京 " * 3_100
        scenario_ids = [f"SCN-{index:03d}-" + "x" * 140 for index in range(260)]
        action = "🧪" * 33_000
        case = TestCase(id="TC-long", title="Checkout", steps=[{"step": 1, "action": action, "expected": "Final outcome"}])
        audit = {
            "case_versions": versions,
            "source_mappings": [
                {
                    "normalized_requirement_id": "REQ-1",
                    "original_requirement_ids": ["ROOM-1"],
                    "sources": [{"source_id": "SOURCE-1", "excerpt": excerpt, "excerpt_verified": True}],
                }
            ],
            "coverage": {"scenarios": {"referenced_ids": scenario_ids}},
        }
        workbook = self.workbook(cases=[case], audit=audit)
        self.assertEqual(workbook.sheetnames, ["Test Cases", "Audit", "Sources", "Coverage", "Long Values"])
        self.assertEqual(json.loads(self.continued_value(workbook, "Audit", "B2")), versions)
        self.assertEqual(json.loads(self.continued_value(workbook, "Audit", "B3")), audit["source_mappings"])
        self.assertEqual(json.loads(self.continued_value(workbook, "Audit", "B4")), audit["coverage"])
        self.assertEqual(self.continued_value(workbook, "Sources", "F2"), excerpt)
        self.assertIs(workbook["Sources"]["E2"].value, True)
        self.assertEqual(json.loads(self.continued_value(workbook, "Coverage", "E3")), scenario_ids)
        self.assertEqual(self.continued_value(workbook, "Test Cases", "H2"), f"1. {action}\n   → Final outcome")
        self.assertFalse(any(cell.data_type == "f" for sheet in workbook for row in sheet for cell in row))

    def test_cell_limits_and_reversible_xml_escape_preserve_text(self):
        base = TestCase(id="TC", title="#N/A", steps=[])
        at_boundary = "🧪" * 16_383 + "x"
        multiline = "\n".join(["line"] * 255)
        escaped = "Source\x0bscanner _x000D_ \uffff 🧪"
        formula_fragment = "x" * 48_000 + "=SUM(1,2)"
        cases = [
            base.model_copy(update={"description": at_boundary}),
            base.model_copy(update={"description": at_boundary + "🧪"}),
            base.model_copy(update={"description": multiline.removesuffix("\nline")}),
            base.model_copy(update={"description": multiline}),
            base.model_copy(update={"description": escaped}),
            base.model_copy(update={"description": formula_fragment}),
        ]
        workbook = self.workbook(cases=cases)
        sheet = workbook["Test Cases"]
        self.assertEqual((sheet["B2"].value, sheet["B2"].data_type), ("#N/A", "s"))
        self.assertEqual(sheet["C2"].value, at_boundary)
        self.assertIsNone(sheet["C2"].hyperlink)
        self.assertEqual(sheet["C4"].value.count("\n"), 253)
        self.assertIsNone(sheet["C4"].hyperlink)
        for address, expected in (("C3", at_boundary + "🧪"), ("C5", multiline), ("C6", escaped), ("C7", formula_fragment)):
            self.assertEqual(self.continued_value(workbook, "Test Cases", address), expected)
        fragments = [row[-1] for row in workbook["Long Values"].iter_rows(min_row=2) if row[1].value == "C7"]
        self.assertEqual((fragments[-1].value, fragments[-1].data_type), ("=SUM(1,2)", "s"))
