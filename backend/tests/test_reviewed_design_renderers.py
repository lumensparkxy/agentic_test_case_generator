"""Verify exact reviewed design evidence in each downloadable file format."""

import csv
import io
import json
import unittest

from openpyxl import load_workbook

from app.agents.export_agent import export_to_csv, export_to_excel, export_to_json
from app.models import TestCase


class ReviewedDesignRendererTests(unittest.TestCase):
    def setUp(self):
        self.steps = [
            {"step": 1, "action": "  Open Café — 東京 🧪\r\n\t", "expected": "  A confirmation appears\n", "test_data": "\t=SUM(1,2)\r\n🧪 "},
            {"step": 2, "action": "Leave the optional field empty", "expected": "The empty field stays empty", "test_data": ""},
            {"step": 3, "action": "Reload the saved result", "expected": "The confirmation is still visible", "test_data": None},
        ]
        self.cases = [
            TestCase(
                id="TC-2",
                title="Confirm an order",
                status="Ready",
                steps=self.steps,
                test_data="  =SUM(1,2)",
                linked_requirement_ids=["REQ-1"],
                scenario_refs=["SCN-1"],
                source_refs=["SOURCE-1"],
                artifact_set_id="set-1",
                artifact_item_id="item-2",
                artifact_version_id="version-3",
                artifact_version_number=3,
            ),
            TestCase(
                id="TC-1",
                title="Check the receipt",
                steps=[{"step": 1, "action": "Read the receipt", "expected": "The order ID matches", "test_data": None}],
                linked_requirement_ids=["REQ-1"],
                scenario_refs=["SCN-1"],
                artifact_set_id="set-1",
                artifact_item_id="item-1",
                artifact_version_id="version-2",
                artifact_version_number=2,
            ),
        ]
        self.audit = {
            "schema_version": 1,
            "snapshot_id": "suite-1",
            "snapshot_version": 3,
            "artifact_set_id": "set-1",
            "case_versions": [
                case.model_dump(include={"artifact_item_id", "artifact_version_id", "artifact_version_number"}) | {"case_id": case.id} for case in self.cases
            ],
            "machine_review_status": "failed",
            "human_review_status": "unavailable",
            "is_draft": False,
            "source_snapshot_ids": {"requirements": "requirements-1", "context": "context-1", "use_cases": "plan-1", "test_cases": "suite-1"},
            "source_mappings": [
                {
                    "normalized_requirement_id": "REQ-1",
                    "original_requirement_ids": ["R-004"],
                    "sources": [
                        {
                            "source_id": "SOURCE-1",
                            "source_version": "source-v2",
                            "excerpt": "  =Order field Café\n",
                            "excerpt_verified": True,
                        }
                    ],
                }
            ],
            "execution_status": "not_executed",
            "execution_run_ids": [],
            "execution_runs": [],
            "design_delivery": {
                "schema_version": 1,
                "delivery_kind": "reviewed_design",
                "legacy_approved": False,
                "suite_snapshot_id": "suite-1",
                "source_freshness": "current",
                "design_status": "complete",
                "scenario_decision": "approved",
                "scenario_snapshot_id": "plan-1",
                "scenario_review_id": "review-1",
                "human_acceptance": "unavailable",
                "declared_setup": "blocked",
                "target_readiness": "unknown",
                "required_requirement_ids": ["REQ-1"],
                "deferred_requirement_ids": ["REQ-2"],
                "excluded_requirement_ids": ["REQ-3"],
                "active_case_count": 2,
                "deprecated_case_count": 0,
            },
        }
        self.status_fields = {
            "Delivery kind": "Reviewed design",
            "Design assessment": "complete",
            "Source freshness": "current",
            "Scenario decision": "approved",
            "Human suite acceptance": "unavailable",
            "Declared setup": "blocked",
            "Target readiness": "unknown",
            "Execution outcome": "not_executed",
            "Legacy approval": "false",
        }

    def test_reviewed_design_json_preserves_ordered_cases_and_audit(self):
        document = json.loads(export_to_json(self.cases, audit_metadata=self.audit))
        self.assertEqual(document["export_format"], "test_cases_v1")
        self.assertEqual(document["total_count"], 2)
        self.assertEqual([case["id"] for case in document["test_cases"]], ["TC-2", "TC-1"])
        self.assertEqual(document["test_cases"][0]["steps"], self.steps)
        self.assertEqual(document["test_cases"][0]["test_data"], "  =SUM(1,2)")
        self.assertEqual(document["audit_metadata"], self.audit)

    def test_reviewed_design_csv_preserves_exact_steps_with_separate_audit_columns(self):
        reader = csv.DictReader(io.StringIO(export_to_csv(self.cases, audit_metadata=self.audit)))
        rows = list(reader)
        self.assertEqual([row["ID"] for row in rows], ["TC-2", "TC-1"])
        self.assertEqual(json.loads(rows[0]["Exact Steps (JSON)"]), self.steps)
        self.assertEqual(rows[0]["Test Data"], "'  =SUM(1,2)")
        self.assertEqual(rows[0]["Audit is_draft"], "false")
        for label, expected in self.status_fields.items():
            self.assertEqual(rows[0][f"Audit {label}"], expected)
        for key in ("design_delivery", "source_snapshot_ids", "source_mappings", "case_versions", "execution_runs"):
            self.assertEqual(json.loads(rows[0][f"Audit {key}"]), self.audit[key])

    def test_reviewed_design_excel_preserves_exact_steps_with_separate_audit_rows(self):
        workbook = load_workbook(io.BytesIO(export_to_excel(self.cases, audit_metadata=self.audit)))
        cases = workbook["Test Cases"]
        headings = {cell.value: cell.column for cell in cases[1]}
        self.assertEqual([cases.cell(row, headings["ID"]).value for row in (2, 3)], ["TC-2", "TC-1"])
        self.assertEqual(json.loads(cases.cell(2, headings["Exact Steps (JSON)"]).value), self.steps)
        self.assertEqual(cases.cell(2, headings["Test Data"]).value, "'  =SUM(1,2)")
        audit = dict(workbook["Audit"].iter_rows(min_row=2, values_only=True))
        self.assertEqual(list(audit)[: len(self.status_fields)], list(self.status_fields))
        self.assertEqual(audit["is_draft"], "false")
        for label, expected in self.status_fields.items():
            self.assertEqual(audit[label], expected)
        for key in ("design_delivery", "source_snapshot_ids", "source_mappings", "case_versions", "execution_runs"):
            self.assertEqual(json.loads(audit[key]), self.audit[key])
        self.assertIs(workbook["Sources"]["E2"].value, True)
        self.assertFalse(any(cell.data_type == "f" for sheet in workbook for row in sheet for cell in row))

    def test_legacy_audit_keeps_its_layout_without_reviewed_design_fields(self):
        audit = {"snapshot_id": "legacy-suite", "is_draft": True}
        document = json.loads(export_to_json(self.cases, audit_metadata=audit))
        self.assertEqual(document["audit_metadata"], audit)
        reader = csv.DictReader(io.StringIO(export_to_csv(self.cases, audit_metadata=audit, include_exact_steps=False)))
        self.assertEqual([field for field in reader.fieldnames if field.startswith("Audit ")], ["Audit snapshot_id", "Audit is_draft"])
        self.assertNotIn("Exact Steps (JSON)", reader.fieldnames)
        workbook = load_workbook(io.BytesIO(export_to_excel(self.cases, audit_metadata=audit, include_exact_steps=False)))
        self.assertEqual(workbook.sheetnames, ["Test Cases", "Audit", "Sources", "Coverage"])
        self.assertEqual(list(workbook["Audit"].values), [("Field", "Value"), ("snapshot_id", "legacy-suite"), ("is_draft", "true")])
        self.assertNotIn("Exact Steps (JSON)", [cell.value for cell in workbook["Test Cases"][1]])
