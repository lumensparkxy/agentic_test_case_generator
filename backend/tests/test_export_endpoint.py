from copy import deepcopy
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.api_core.exceptions import ServiceUnavailable
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app, get_current_user
from app.agents.export_agent import export_to_excel
from app.contracts.exports import ExportTestCasesInput
from app.models import AuthUser, TestCase
from app.contracts.projects import QaProjectDetail
from app.routers import export as routes
from app.services import workflow_project_service as projects
from app.services.export_evidence import build_export_evidence
from app.services.requirement_reconciliation import digest
from app.services.requirement_scope import approved_generation_requirements
from app.services.suite_quality import build_suite_design_proof, reviewed_case_content_hash
from app.services.test_case_quality import case_source_hash
from app.services.workflow_project_service import ProjectPermissionError
from test_suite_quality import fixture as suite_quality_fixture
from test_use_case_review_service import FakeDocument, FakeFirestoreClient, _run_transaction


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

    def test_exact_spreadsheet_steps_match_the_saved_json_export(self):
        steps = [
            {"step": 4, "action": "Clear field", "expected": "Value is empty", "test_data": ""},
            {"step": 7, "action": "Read label", "expected": "Label is visible", "test_data": None},
            {
                "step": 9,
                "action": '=Set field, "quoted"\ncontinued',
                "expected": "Saved -> Expected: [Data: ] 🧪",
                "test_data": "=SUM(1,2)\r\n  ]\t\n",
            },
        ]
        case = {**_test_case(), "steps": steps}
        project = _project()
        project.current_snapshots["test_cases"].payload["test_cases"] = [case]
        payload = _payload(test_cases=[case])
        for fmt in ("json", "csv", "excel"):
            with self.subTest(format=fmt):
                response, append, _ = self.export(fmt, payload=payload, project=project)
                self.assertEqual(response.status_code, 200, response.status_code)
                if fmt == "json":
                    self.assertEqual(response.json()["test_cases"][0]["steps"], steps)
                    self.assertNotIn("export_layout", append.call_args.kwargs["metadata"])
                elif fmt == "csv":
                    reader = csv.DictReader(io.StringIO(response.text))
                    row = next(reader)
                    self.assertEqual(reader.fieldnames[-1], "Exact Steps (JSON)")
                    self.assertTrue(row["Title"].startswith("'="))
                    self.assertEqual(json.loads(row["Exact Steps (JSON)"]), steps)
                else:
                    sheet = load_workbook(io.BytesIO(response.content))["Test Cases"]
                    self.assertEqual(sheet["H1"].value, "Steps")
                    self.assertEqual(sheet["R1"].value, "Exact Steps (JSON)")
                    self.assertEqual(sheet["R2"].data_type, "s")
                    self.assertEqual(json.loads(sheet["R2"].value), steps)
                if fmt != "json":
                    self.assertEqual(append.call_args.kwargs["metadata"]["export_layout"], "exact-steps-v1")

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

    def read_report(self, report_id):
        return self.http.get(f"/projects/{self.project_id}/reports/{report_id}")

    def audit(self, collection):
        return [value for path, value in self.store.items() if path.startswith(f"{collection}/")]

    def assert_audit(self, status, quantity, operation="export.json"):
        [event] = self.audit("usage_events")
        [workflow] = self.audit("workflow_runs")
        self.assertEqual((event["status"], event["quantity"], workflow["status"]), (status, quantity, status))
        self.assertEqual(event["event_type"], operation)
        self.assertEqual(event["workflow_run_id"], workflow["run_id"])
        return event

    def seed_reviewed_design(self):
        source, suite_payload, metadata = suite_quality_fixture(missing_setup=True)
        requirements_payload = deepcopy(source.current_snapshots["requirements"].payload)
        row = requirements_payload["requirements"][0]
        row.update(
            original_requirement_ids=["CHECKOUT-01"],
            sources=[
                {
                    "source_id": "checkout-source",
                    "source_version": "checkout-v1",
                    "import_id": "checkout-import",
                    "label": "Checkout source",
                    "excerpt": row["text"],
                    "excerpt_verified": True,
                    "original_requirement_ids": ["CHECKOUT-01"],
                }
            ],
        )
        requirements = self.save("requirements", requirements_payload, operation="requirements.review", approved=False)
        plan = self.save(
            "use_cases",
            source.current_snapshots["use_cases"].payload,
            operation="use_cases.generate",
            source_snapshot_id=requirements.snapshot_id,
            metadata={"source_requirements_snapshot_id": requirements.snapshot_id, "source_context_omitted": True},
        )
        self.store[f"qa_projects/{self.project_id}"]["stage_state"]["use_cases"]["metadata"]["latest_human_review"] = {
            "snapshot_id": plan.snapshot_id,
            "review_id": "human-review",
            "reviewer_user_id": self.actor.sub,
            "decision": "approve",
        }
        case = suite_payload["test_cases"][0]
        case["title"] = '=HYPERLINK("https://example.test")'
        case["steps"].extend(
            [
                {"step": 4, "action": "Clear the optional order note.", "expected": "The order note is empty.", "test_data": ""},
                {"step": 7, "action": "Enter the literal note.", "expected": "The note is displayed verbatim.", "test_data": "=SUM(1,2) β\t "},
            ]
        )
        suite_payload["test_cases"].append(
            {
                **deepcopy(case),
                "id": "TC-0",
                "title": "Reopen the saved checkout confirmation",
                "artifact_item_id": "item-2",
                "artifact_version_id": "version-2",
            }
        )
        suite_payload["review"] = {"approved": False, "score": 100, "threshold": 90, "blocking_issues": ["Checkout operator role is missing."]}
        suite_payload["approved"] = False
        assessment = suite_payload["substantive_assessment"]
        assessment["case_grounding"].append({"test_case_id": "TC-0", "reason": "The saved checkout displays its confirmation.", "prerequisites": []})
        reviewed_hash = reviewed_case_content_hash(suite_payload["test_cases"])
        suite_payload["generation_evidence"]["design_precheck"]["case_content_hash"] = reviewed_hash
        assessment["reviewed_case_content_hash"] = reviewed_hash
        project = self.project()
        requirement_hashes = {row.id: case_source_hash(row.model_dump(mode="json")) for row in approved_generation_requirements(project)}
        assessment["reviewed_source_binding"]["requirement_hashes"] = requirement_hashes
        metadata.update(
            source_snapshot_ids={"requirements": requirements.snapshot_id, "use_cases": plan.snapshot_id},
            source_requirements_snapshot_id=requirements.snapshot_id,
            source_use_case_snapshot_id=plan.snapshot_id,
            source_requirement_hashes=requirement_hashes,
            generation_base_project_revision=project.current_revision,
        )
        proof = build_suite_design_proof(project, suite_payload, metadata, plan_snapshot_id=plan.snapshot_id)
        self.assertIsNotNone(proof)
        self.assertEqual(proof["design_status"], "complete")
        self.suite = self.save(
            "test_cases",
            suite_payload,
            operation="testcases.generate",
            approved=False,
            source_snapshot_id=plan.snapshot_id,
            metadata=metadata,
            suite_design_proof=proof,
        )
        self.before = self.project()
        self.assertEqual(self.before.suite_quality.design_status, "complete")
        self.assertEqual(self.before.suite_quality.declared_setup, "blocked")
        self.input.update(
            base_project_revision=self.before.current_revision,
            source_snapshot_id=self.suite.snapshot_id,
            test_cases=self.suite.payload["test_cases"],
        )

    def assert_reviewed_design_file(self, fmt, response, report):
        if fmt == "json":
            document = response.json()
            rendered_cases = [(case["id"], case["steps"]) for case in document["test_cases"]]
            metadata = document["audit_metadata"]
        elif fmt == "csv":
            rows = list(csv.DictReader(io.StringIO(response.text)))
            rendered_cases = [(row["ID"], json.loads(row["Exact Steps (JSON)"])) for row in rows]
            self.assertTrue(rows[0]["Title"].startswith("'="))
            self.assertEqual(rows[0]["Audit Delivery kind"], "Reviewed design")
            self.assertEqual(rows[0]["Audit Declared setup"], "blocked")
            metadata = {field: json.loads(rows[0][f"Audit {field}"]) for field in ("design_delivery", "case_versions", "source_mappings")}
        else:
            workbook = load_workbook(io.BytesIO(response.content))
            sheet = workbook["Test Cases"]
            rendered_cases = [(row[0].value, json.loads(row[-1].value)) for row in sheet.iter_rows(min_row=2)]
            self.assertNotEqual(sheet["B2"].data_type, "f")
            audit = {row[0].value: row[1].value for row in workbook["Audit"].iter_rows(min_row=2)}
            self.assertEqual(audit["Delivery kind"], "Reviewed design")
            self.assertEqual(audit["Declared setup"], "blocked")
            metadata = {field: json.loads(audit[field]) for field in ("design_delivery", "case_versions", "source_mappings")}
        expected_cases = [(case["id"], [{"test_data": None, **step} for step in case["steps"]]) for case in self.suite.payload["test_cases"]]
        self.assertEqual(rendered_cases, expected_cases)
        self.assertEqual([row["artifact_version_id"] for row in metadata["case_versions"]], ["version-1", "version-2"])
        self.assertEqual(metadata["source_mappings"][0]["original_requirement_ids"], ["CHECKOUT-01"])
        self.assertEqual(metadata["source_mappings"][0]["sources"][0]["source_id"], "checkout-source")
        self.assertEqual(metadata["design_delivery"], report.payload["audit_metadata"]["design_delivery"])

    def test_legacy_fingerprints_and_bytes_match_the_pre_upgrade_contract(self):
        with patch.dict(os.environ, {"TCG_DESIGN_DELIVERY_ENABLED": "false"}):
            first = self.post()
        self.assertEqual(first.status_code, 200, first.text)
        report = self.project().current_snapshots["reports"]
        legacy_fields = {
            "test_cases",
            "approved",
            "review",
            "draft_override_requested",
            "draft_override_reason",
            "project_id",
            "base_project_revision",
            "source_snapshot_id",
        }
        pre_upgrade_request = ExportTestCasesInput.model_validate(self.input).model_dump(mode="json", include=legacy_fields)
        self.assertIn("draft_override_reason", pre_upgrade_request)
        self.assertIsNone(pre_upgrade_request["draft_override_reason"])
        self.assertEqual(report.metadata["export_request_fingerprint"], digest(pre_upgrade_request))
        legacy_audit = first.json()["audit_metadata"]
        self.assertNotIn("design_delivery", legacy_audit)
        self.assertIsNone(legacy_audit["artifact_version_id"])
        pre_upgrade_audit = build_export_evidence(ExportTestCasesInput.model_validate(self.input), self.before).model_dump(
            mode="json", exclude={"design_delivery"}
        )
        pre_upgrade_audit["exported_at"] = legacy_audit["exported_at"]
        self.assertEqual(legacy_audit, pre_upgrade_audit)
        self.assertEqual(report.metadata["export_audit_fingerprint"], digest(pre_upgrade_audit))
        written = deepcopy(self.store)
        with patch.dict(os.environ, {"TCG_DESIGN_DELIVERY_ENABLED": "true"}):
            replay = self.post(delivery_intent=None)
            changed = self.post(delivery_intent="reviewed_design")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.content, first.content)
        self.assertEqual(changed.status_code, 409, changed.text)
        self.assertIn("different inputs", changed.json()["detail"])
        self.assertEqual(self.store, written)
        self.assert_audit("completed", 1)

    def test_reviewed_design_is_default_off_and_preserves_legacy_draft_export(self):
        self.seed_reviewed_design()
        written = deepcopy(self.store)
        with patch.dict(os.environ):
            os.environ.pop("TCG_DESIGN_DELIVERY_ENABLED", None)
            self.assertFalse(self.project().design_delivery_enabled)
            disabled = self.post(delivery_intent="reviewed_design")
        self.assertEqual(disabled.status_code, 409, disabled.text)
        self.assertEqual(disabled.json()["detail"]["code"], "design_delivery_disabled")
        self.assertEqual(self.store, written)
        unapproved = self.post()
        self.assertEqual(unapproved.status_code, 422, unapproved.text)
        draft = self.post(draft_override_requested=True, draft_override_reason="Setup is missing.")
        self.assertEqual(draft.status_code, 200, draft.text)
        audit = draft.json()["audit_metadata"]
        self.assertTrue(audit["is_draft"])
        self.assertEqual(audit["draft_override_reason"], "Setup is missing.")
        self.assertNotIn("design_delivery", audit)
        self.assert_audit("completed", 2)

    def test_reviewed_design_all_formats_replay_after_disabling_new_delivery(self):
        self.seed_reviewed_design()
        for index, fmt in enumerate(("json", "csv", "excel"), 1):
            with self.subTest(format=fmt):
                before = self.project()
                revision = before.current_revision
                with patch.dict(os.environ, {"TCG_DESIGN_DELIVERY_ENABLED": "true"}):
                    self.assertTrue(self.project().design_delivery_enabled)
                    first = self.post(fmt, base_project_revision=revision, delivery_intent="reviewed_design")
                self.assertEqual(first.status_code, 200, first.text if fmt != "excel" else first.status_code)
                after = self.project()
                report = after.current_snapshots["reports"]
                self.assertEqual(after.current_revision, revision + 1)
                self.assertEqual(after.current_snapshots["test_cases"], before.current_snapshots["test_cases"])
                self.assertEqual(report.source_snapshot_id, self.suite.snapshot_id)
                self.assertEqual(report.payload["evidence"]["source_snapshot_ids"]["test_cases"], self.suite.snapshot_id)
                self.assertEqual(report.metadata["delivery_kind"], "reviewed_design")
                self.assertEqual(report.payload["delivery_kind"], "reviewed_design")
                self.assertFalse(report.approved)
                self.assertFalse(report.payload["approved"])
                self.assertFalse(after.stage_state["reports"].approved)
                self.assertFalse(after.stage_state["test_cases"].approved)
                audit = report.payload["audit_metadata"]
                self.assertFalse(audit["is_draft"])
                self.assertFalse(audit["draft_override_used"])
                self.assertEqual(audit["machine_review_status"], "failed")
                self.assertEqual(audit["human_review_status"], "unavailable")
                self.assertEqual(audit["execution_status"], "not_executed")
                self.assertEqual(audit["execution_run_ids"], [])
                self.assertEqual(audit["source_snapshot_ids"]["test_cases"], self.suite.snapshot_id)
                self.assertEqual(audit["coverage"]["requirements"]["covered_ids"], ["REQ-1"])
                self.assertEqual(audit["coverage"]["requirements"]["missing_ids"], [])
                self.assertEqual(
                    audit["design_delivery"],
                    {
                        "schema_version": 1,
                        "delivery_kind": "reviewed_design",
                        "legacy_approved": False,
                        "suite_snapshot_id": self.suite.snapshot_id,
                        "source_freshness": "current",
                        "design_status": "complete",
                        "scenario_decision": "approved",
                        "scenario_snapshot_id": before.current_snapshots["use_cases"].snapshot_id,
                        "scenario_review_id": "human-review",
                        "human_acceptance": "unavailable",
                        "declared_setup": "blocked",
                        "required_requirement_ids": ["REQ-1"],
                        "deferred_requirement_ids": ["REQ-2"],
                        "excluded_requirement_ids": ["REQ-3"],
                        "active_case_count": 2,
                        "deprecated_case_count": 0,
                        "target_readiness": "unknown",
                    },
                )
                self.assert_reviewed_design_file(fmt, first, report)
                event = next(item for item in self.audit("usage_events") if item["event_id"] == report.source_event_id)
                self.assertEqual((event["event_type"], event["quantity"], event["status"]), (f"export.{fmt}", 2, "completed"))
                self.assertEqual(event["workflow_run_id"], report.workflow_run_id)
                self.assertEqual(len(self.audit("usage_events")), index)
                self.assertEqual(len(self.audit("workflow_runs")), index)

                written = deepcopy(self.store)
                with patch.dict(os.environ, {"TCG_DESIGN_DELIVERY_ENABLED": "false"}):
                    replay = self.post(fmt, base_project_revision=revision, delivery_intent="reviewed_design")
                    changed = self.post(fmt, base_project_revision=revision, delivery_intent=None)
                    fresh = self.post(fmt, request_id="new-design", base_project_revision=after.current_revision, delivery_intent="reviewed_design")
                self.assertEqual(replay.status_code, 200, replay.status_code)
                self.assertEqual(changed.status_code, 409, changed.text)
                self.assertIn("different inputs", changed.json()["detail"])
                self.assertEqual(fresh.status_code, 409, fresh.text)
                self.assertEqual(fresh.json()["detail"]["code"], "design_delivery_disabled")
                self.assertEqual(replay.headers["content-disposition"], first.headers["content-disposition"])
                if fmt == "excel":
                    first_book, replay_book = (load_workbook(io.BytesIO(response.content)) for response in (first, replay))
                    self.assertEqual(first_book.sheetnames, replay_book.sheetnames)
                    for name in first_book.sheetnames:
                        self.assertEqual(list(first_book[name].values), list(replay_book[name].values))
                else:
                    self.assertEqual(replay.content, first.content)
                self.assertEqual(self.store, written)

    def test_reviewed_design_lost_publication_reply_recovers_exact_receipt(self):
        self.seed_reviewed_design()
        publish = routes.append_stage_snapshot

        def lose_reply(**kwargs):
            publish(**kwargs)
            raise RuntimeError("Synthetic design report commit reply lost")

        with (
            patch.dict(os.environ, {"TCG_DESIGN_DELIVERY_ENABLED": "true"}),
            patch.object(routes, "append_stage_snapshot", side_effect=lose_reply) as append,
        ):
            first = self.post(delivery_intent="reviewed_design")
        self.assertEqual(first.status_code, 200, first.text)
        append.assert_called_once()
        report = self.project().current_snapshots["reports"]
        self.assertEqual(self.project().current_revision, self.before.current_revision + 1)
        self.assert_reviewed_design_file("json", first, report)
        self.assertEqual(self.assert_audit("completed", 2)["event_id"], report.source_event_id)
        written = deepcopy(self.store)
        with patch.dict(os.environ, {"TCG_DESIGN_DELIVERY_ENABLED": "false"}):
            replay = self.post(delivery_intent="reviewed_design")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.content, first.content)
        self.assertEqual(self.store, written)

        self.store[f"qa_projects/{self.project_id}/snapshots/{self.suite.snapshot_id}"]["metadata"].pop("suite_design_proof")
        unverified = deepcopy(self.store)
        with patch.dict(os.environ, {"TCG_DESIGN_DELIVERY_ENABLED": "false"}):
            rejected = self.post(delivery_intent="reviewed_design")
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(rejected.json()["detail"]["code"], "design_delivery_unavailable")
        self.assertEqual(self.store, unverified)

    def test_reviewed_design_rejects_stale_or_forged_evidence_without_audit(self):
        self.seed_reviewed_design()
        base = deepcopy(self.store)
        project_path = f"qa_projects/{self.project_id}"
        suite_path = f"{project_path}/snapshots/{self.suite.snapshot_id}"
        with patch.dict(os.environ, {"TCG_DESIGN_DELIVERY_ENABLED": "true"}), patch.object(routes, "export_to_json") as render:
            for kind in (
                "wrong_revision",
                "wrong_suite",
                "modified_case",
                "stale_source",
                "missing_proof",
                "partial_assessment",
                "human_changes",
                "placeholder",
                "saved_placeholder",
            ):
                with self.subTest(kind=kind):
                    self.store.clear()
                    self.store.update(deepcopy(base))
                    posted = {
                        "delivery_intent": "reviewed_design",
                        "approved": True,
                        "review": {"approved": True, "score": 100, "threshold": 90},
                        "suite_quality": self.before.suite_quality.model_dump(mode="json"),
                        "design_delivery": {"delivery_kind": "reviewed_design", "design_status": "complete"},
                    }
                    expected = 409
                    if kind == "wrong_revision":
                        posted["base_project_revision"] = self.before.current_revision - 1
                    elif kind == "wrong_suite":
                        posted["source_snapshot_id"] = "older-suite"
                    elif kind in {"modified_case", "placeholder"}:
                        cases = deepcopy(self.input["test_cases"])
                        if kind == "modified_case":
                            cases[0]["steps"][0]["expected"] = "A forged outcome."
                        else:
                            cases[0]["steps"] = []
                            expected = 422
                        posted["test_cases"] = cases
                    elif kind == "stale_source":
                        self.save("context", {"notes": "A source changed after assessment."})
                        posted["base_project_revision"] = self.project().current_revision
                    elif kind == "missing_proof":
                        self.store[suite_path]["metadata"].pop("suite_design_proof")
                    elif kind == "partial_assessment":
                        self.store[suite_path]["payload"]["substantive_assessment"]["diagnostics"] = {"unverified_group_count": 1}
                    elif kind == "saved_placeholder":
                        self.store[suite_path]["payload"]["test_cases"].append(
                            {
                                **_test_case(),
                                "id": "TC-UNFINISHED",
                                "steps": [{"step": 1, "action": "Navigate to the feature area that implements checkout.", "expected": "Checkout opens."}],
                            }
                        )
                        self.assertEqual(self.project().current_snapshots["test_cases"].payload["test_cases"], self.input["test_cases"])
                    else:
                        self.store[project_path]["stage_state"]["test_cases"]["metadata"]["latest_human_review"] = {
                            "snapshot_id": self.suite.snapshot_id,
                            "review_id": "human-changes",
                            "reviewer_user_id": self.actor.sub,
                            "decision": "request_changes",
                        }
                    written = deepcopy(self.store)
                    response = self.post(**posted)
                    self.assertEqual(response.status_code, expected, response.text)
                    self.assertEqual(self.store, written)
            render.assert_not_called()
        self.assertEqual(self.audit("usage_events"), [])
        self.assertEqual(self.audit("workflow_runs"), [])

    def test_all_formats_replay_original_evidence_without_additional_publication(self):
        before = self.before
        for fmt in ("json", "csv", "excel"):
            with self.subTest(format=fmt):
                first = self.post(fmt, base_project_revision=before.current_revision)
                self.assertEqual(first.status_code, 200, first.status_code)
                after = self.project()
                snapshot = after.current_snapshots["reports"]
                if fmt != "json":
                    self.assertEqual(snapshot.metadata["export_layout"], "exact-steps-v1")
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

    def test_legacy_spreadsheets_keep_their_layout_after_replay_and_publication_race(self):
        publish = routes._publish_project_export
        for fmt in ("csv", "excel"):
            with self.subTest(format=fmt):
                render = getattr(routes, f"export_to_{fmt}")
                before = self.project()

                def render_legacy(cases, *, audit_metadata, include_exact_steps):
                    return render(cases, audit_metadata=audit_metadata, include_exact_steps=False)

                def publish_legacy(**kwargs):
                    return publish(**{**kwargs, "export_layout": None})

                with (
                    patch.object(routes, f"export_to_{fmt}", side_effect=render_legacy),
                    patch.object(routes, "_publish_project_export", side_effect=publish_legacy),
                ):
                    first = self.post(fmt, base_project_revision=before.current_revision)
                self.assertEqual(first.status_code, 200)
                after = self.project()
                self.assertNotIn("export_layout", after.current_snapshots["reports"].metadata)
                written = deepcopy(self.store)
                replay = self.post(fmt, base_project_revision=before.current_revision)
                self.assertEqual(self.store, written)

                # A parallel worker read the old revision before the legacy receipt committed.
                with (
                    patch.object(routes, "_find_export_receipt", return_value=None),
                    patch.object(routes, "_load_export_project", side_effect=[before, after]),
                ):
                    raced = self.post(fmt, base_project_revision=before.current_revision)
                self.assertEqual(self.project(), after)
                for restored in (replay, raced):
                    self.assertEqual(restored.status_code, 200)
                    if fmt == "csv":
                        self.assertEqual(restored.content, first.content)
                        self.assertNotIn("Exact Steps (JSON)", next(csv.reader(io.StringIO(restored.text))))
                    else:
                        expected, actual = (load_workbook(io.BytesIO(response.content)) for response in (first, restored))
                        self.assertEqual(actual.sheetnames, expected.sheetnames)
                        for name in expected.sheetnames:
                            self.assertEqual(list(actual[name].values), list(expected[name].values))
                        self.assertEqual(actual["Test Cases"].max_column, 17)
        self.assertEqual(len(self.audit("usage_events")), 2)

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

    def test_saved_reports_keep_original_decisions_and_case_versions_after_archive(self):
        self.seed_reviewed_design()
        original_suite = self.suite
        with patch.dict(os.environ, {"TCG_DESIGN_DELIVERY_ENABLED": "true"}):
            result = self.post("excel", request_id="original-design", delivery_intent="reviewed_design")
        self.assertEqual(result.status_code, 200, result.text)
        original_report = self.project().current_snapshots["reports"]
        first_read = self.read_report(original_report.snapshot_id)
        self.assertEqual(first_read.status_code, 200, first_read.text)
        original = first_read.json()
        evidence = original["evidence"]
        self.assertEqual((original["format"], original["delivery_kind"], original["legacy_approved"]), ("excel", "reviewed_design", False))
        self.assertEqual(original["project_revision"], original_report.project_revision)
        self.assertEqual(evidence["suite_snapshot_id"], original_suite.snapshot_id)
        self.assertEqual(evidence["requirement_ids"], ["REQ-1", "REQ-2", "REQ-3"])
        self.assertEqual(evidence["design_delivery"]["scenario_decision"], "approved")
        self.assertEqual(evidence["design_delivery"]["declared_setup"], "blocked")
        self.assertEqual(evidence["execution_status"], "not_executed")
        self.assertEqual(evidence["case_versions"][0], {"case_id": "TC-1", "case_version_id": "version-1", "case_version_number": 1})

        payload = deepcopy(original_suite.payload)
        payload["test_cases"][0].update(artifact_version_id="later-version", artifact_version_number=2, title="Updated checkout case")
        later_suite = self.save("test_cases", payload, approved=False, metadata=original_suite.metadata)
        self.assertTrue(self.read_report(original_report.snapshot_id).json()["stale"])
        reason = "Checkout operator account is pending."
        result = self.post(
            "csv",
            request_id="newer-draft",
            base_project_revision=self.project().current_revision,
            test_cases=later_suite.payload["test_cases"],
            source_snapshot_id=later_suite.snapshot_id,
            draft_override_requested=True,
            draft_override_reason=reason,
        )
        self.assertEqual(result.status_code, 200, result.text)
        later_report = self.project().current_snapshots["reports"]
        self.save("requirements", {"requirements": [{"id": "REQ-OTHER", "text": "Later source"}]}, approved=False)
        projects.update_project(project_id=self.project_id, actor=self.actor, request_id="archive", status_value="archived")
        before = deepcopy(self.store)
        transactions = len(self.firestore.transactions)
        with (
            patch.dict(os.environ, {"TCG_DESIGN_DELIVERY_ENABLED": "false"}),
            patch.object(routes, "build_export_evidence", side_effect=AssertionError("Historical read cannot rebuild an export")),
            patch.object(routes, "export_to_excel", side_effect=AssertionError("Historical read cannot render a file")),
        ):
            for _ in range(2):
                response = self.read_report(original_report.snapshot_id)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {**original, "historical": True, "stale": False})
            draft_response = self.read_report(later_report.snapshot_id)
            self.assertEqual(draft_response.status_code, 200, draft_response.text)
            draft = draft_response.json()
            self.assertEqual((draft["delivery_kind"], draft["historical"], draft["stale"]), ("draft", False, True))
            self.assertEqual(draft["evidence"]["draft_override_reason"], reason)
            self.assertEqual(draft["evidence"]["case_versions"][0]["case_version_id"], "later-version")
            case = self.http.get(
                f"/projects/{self.project_id}/test-case-evidence",
                params={"snapshot_id": evidence["suite_snapshot_id"], "case_id": evidence["case_versions"][0]["case_id"]},
            )
            self.assertEqual(case.status_code, 200, case.text)
            self.assertEqual(case.json()["test_case"]["artifact_version_id"], "version-1")
        self.assertEqual(self.store, before)
        self.assertEqual(len(self.firestore.transactions), transactions)

    def test_saved_report_sparse_header_and_exact_operation_identity(self):
        report = self.save("reports", {}, operation="export.csv")
        path = f"qa_projects/{self.project_id}/snapshots/{report.snapshot_id}"
        self.store[path].pop("approved")
        original = deepcopy(self.store[path])
        sparse = self.read_report(report.snapshot_id)
        self.assertEqual(sparse.status_code, 200, sparse.text)
        data = sparse.json()
        self.assertEqual((data["format"], data["delivery_kind"], data["legacy_approved"], data["evidence"]), ("csv", "unknown", None, None))
        self.assertEqual(self.read_report("missing-report").status_code, 404)
        self.assertEqual(self.read_report(self.suite.snapshot_id).status_code, 404)
        self.assertEqual(self.read_report("a" * 161).status_code, 422)
        for mismatch in (
            {"operation": "reports.execution_summary"},
            {"operation": "export.jira"},
            {"project_id": "different-project"},
            {"snapshot_id": "different-snapshot"},
            {"payload": {"source": "execution"}},
            {"payload": {"format": "json"}},
            {"metadata": {"format": "excel"}},
        ):
            with self.subTest(mismatch=mismatch):
                self.store[path] = {**original, **mismatch}
                self.assertEqual(self.read_report(report.snapshot_id).status_code, 404)
        self.store[path] = original
        self.actor = AuthUser(sub="foreign-report-reader", name="Foreign user")
        self.assertEqual(self.read_report(report.snapshot_id).status_code, 403)

    def test_saved_report_fails_closed_on_conflicting_sources_and_case_versions(self):
        self.seed_reviewed_design()
        with patch.dict(os.environ, {"TCG_DESIGN_DELIVERY_ENABLED": "true"}):
            published = self.post(delivery_intent="reviewed_design")
        self.assertEqual(published.status_code, 200, published.text)
        report = self.project().current_snapshots["reports"]
        report_path = f"qa_projects/{self.project_id}/snapshots/{report.snapshot_id}"
        suite_path = f"qa_projects/{self.project_id}/snapshots/{self.suite.snapshot_id}"
        source_id = self.suite.metadata["source_requirements_snapshot_id"]
        source_path = f"qa_projects/{self.project_id}/snapshots/{source_id}"

        saved_suite = deepcopy(self.store[suite_path])
        self.store[suite_path]["payload"]["test_cases"][0]["artifact_version_number"] = 2
        mismatched = self.read_report(report.snapshot_id).json()
        self.assertEqual(mismatched["evidence"]["case_versions"], [])
        self.store[suite_path] = saved_suite
        original_source = self.store.pop(source_path)
        self.assertIsNone(self.read_report(report.snapshot_id).json()["evidence"])
        self.store[source_path] = {**original_source, "project_id": "foreign-project"}
        self.assertIsNone(self.read_report(report.snapshot_id).json()["evidence"])
        self.store[source_path] = original_source

        self.store[report_path]["payload"]["audit_metadata"]["case_versions"][0]["artifact_version_id"] = "unverified-version"
        bad_audit = self.read_report(report.snapshot_id)
        self.assertEqual(bad_audit.status_code, 200, bad_audit.text)
        self.assertIsNone(bad_audit.json()["evidence"])

    def test_saved_report_read_outage_is_retryable_and_approved_files_remain_distinct(self):
        execution = self.save(
            "execution",
            {"run_id": "browser-run-original", "results": []},
            operation="automation.execution.run",
            source_snapshot_id=self.suite.snapshot_id,
        )
        run = projects.record_execution_run(
            project_id=self.project_id,
            actor=self.actor,
            request_id="browser-run-request",
            run_id=execution.payload["run_id"],
            target_environment="local",
            status_value="failed",
            summary={},
            test_case_count=1,
            snapshot_id=execution.snapshot_id,
            workflow_run_id=None,
            source_event_id=None,
            source_snapshot_id=self.suite.snapshot_id,
            project_revision=execution.project_revision,
        )
        result = self.post(base_project_revision=execution.project_revision)
        self.assertEqual(result.status_code, 200, result.text)
        report = self.project().current_snapshots["reports"]
        normal_get = FakeDocument.get
        for affected in (report.snapshot_id, self.suite.snapshot_id):

            def unavailable(document, **kwargs):
                if document.id == affected:
                    raise ServiceUnavailable("Temporary report storage outage")
                return normal_get(document, **kwargs)

            with self.subTest(affected=affected), patch.object(FakeDocument, "get", unavailable):
                failed = self.read_report(report.snapshot_id)
                self.assertEqual(failed.status_code, 503, failed.text)
        recovered = self.read_report(report.snapshot_id)
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["delivery_kind"], "approved")
        self.assertIs(recovered.json()["legacy_approved"], True)
        evidence = recovered.json()["evidence"]
        self.assertEqual(evidence["requirement_ids"], ["REQ-001"])
        self.assertEqual(evidence["case_versions"], [])
        self.assertEqual(evidence["execution_status"], "failed")
        self.assertEqual(evidence["execution_run_ids"], [run.run_id])
        self.assertEqual(evidence["source_snapshot_ids"]["execution"], execution.snapshot_id)
        linked_run = self.http.get(f"/projects/{self.project_id}/execution-runs/{run.run_record_id}")
        self.assertEqual(linked_run.status_code, 200, linked_run.text)
        self.assertEqual(linked_run.json()["run_id"], run.run_id)


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

    def test_long_exact_step_data_can_be_reconstructed_from_excel(self):
        steps = [{"step": 1, "action": 'Fill "Payload"', "expected": "Data saved", "test_data": "=SUM(1,2)\r\n🧪_x000D_" * 2000 + "\n"}]
        case = TestCase(id="TC-long-data", title="Exact payload", steps=steps)
        workbook = self.workbook(cases=[case])
        self.assertEqual(json.loads(self.continued_value(workbook, "Test Cases", "R2")), steps)
        self.assertFalse(any(cell.data_type == "f" for sheet in workbook for row in sheet for cell in row))
