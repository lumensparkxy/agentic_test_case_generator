"""Final contract checks must retain supported data and account for every rejection."""

from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.test_case_agent import _build_response, generate_test_cases, refine_test_cases
from app.agents.test_case_hydration import (
    MAX_STRUCTURED_TEST_DATA_CHARS,
    MAX_STRUCTURED_TEST_DATA_NODES,
    MAX_STRUCTURED_TEST_DATA_DEPTH,
    _hydrate_test_cases,
    _serialize_test_cases,
)
from app.models import GenerateTestCasesInput, GenerateTestCasesResponse, RefineTestCasesInput, Requirement, TestCase


def case(identifier="TC-1", requirement="REQ-1", scenario="SCN-1"):
    return {
        "id": identifier,
        "title": "Create a booking",
        "description": "Confirm the selected free room is booked exactly once.",
        "steps": [{"step": 1, "action": "Select free room Birch and submit Book.", "expected": "One Confirmed Birch booking is shown."}],
        "linked_requirement_ids": [requirement],
        "scenario_refs": [scenario],
        "generation_source": "model",
    }


def workflow(rows):
    return {
        "test_cases": rows,
        "approved": True,
        "review": {"approved": True, "score": 100, "threshold": 90},
        "strict_coverage_plan": True,
        "coverage_plan": [
            {
                "requirement_id": f"REQ-{i}",
                "requirement_text": f"Book room {i}",
                "scenarios": [
                    {
                        "id": f"SCN-{i}",
                        "requirement_id": f"REQ-{i}",
                        "scenario_type": "Happy Path",
                        "title": "Book room",
                        "objective": "A Confirmed booking is created.",
                        "must_have": True,
                    }
                ],
            }
            for i in (1, 2)
        ],
        "generation_evidence": {"final_test_case_count": len(rows), "final_status": "completed"},
        "workflow_diagnostics": {"status": "completed"},
    }


class DeliveredTestCaseContractTests(unittest.TestCase):
    def setUp(self):
        # These tests isolate generation/delivery. Independent review is covered separately.
        assessment = patch(
            "app.agents.test_case_agent.assess_delivered_suite", return_value={"status": "assessed_complete", "obligations": [], "case_grounding": []}
        )
        assessment.start()
        self.addCleanup(assessment.stop)

    def test_string_null_object_and_list_data_round_trip_at_both_levels_without_mutating_input(self):
        for value in (None, "", "Birch · Zürich", {}, [], {"room": "Birch", "attendees": [1, 8], "confirmed": False}, [None, {"room": "Atlas"}]):
            with self.subTest(value=value):
                raw = case()
                raw["test_data"] = value
                raw["steps"][0]["test_data"] = value
                before = deepcopy(raw)
                rejected = []
                delivered = _hydrate_test_cases([raw], rejections=rejected)
                self.assertEqual(rejected, [])
                self.assertEqual(len(delivered), 1)
                for actual in (delivered[0].test_data, delivered[0].steps[0].test_data):
                    if isinstance(value, (dict, list)):
                        self.assertEqual(json.loads(actual), value)
                    else:
                        self.assertEqual(actual, value)
                self.assertEqual(raw, before)
                self.assertEqual(delivered[0].scenario_refs, ["SCN-1"])
                self.assertEqual(delivered[0].linked_requirement_ids, ["REQ-1"])

    def test_unsupported_or_oversized_structured_data_is_rejected_without_leaking_values(self):
        deep = "leaf"
        for _ in range(MAX_STRUCTURED_TEST_DATA_DEPTH + 2):
            deep = [deep]
        values = [
            12,
            True,
            {"private": {"secret-value"}},
            {12: "invalid-key"},
            [float("nan")],
            [float("inf")],
            {"private": "secret-value" * MAX_STRUCTURED_TEST_DATA_CHARS},
            [None] * (MAX_STRUCTURED_TEST_DATA_NODES + 1),
            deep,
        ]
        for value in values:
            for step_level in (False, True):
                with self.subTest(value_type=type(value).__name__, step_level=step_level):
                    raw = case()
                    (raw["steps"][0] if step_level else raw)["test_data"] = value
                    rejected = []
                    with self.assertLogs(level="WARNING") as logs:
                        delivered = _hydrate_test_cases([raw], rejections=rejected)
                    self.assertEqual(delivered, [])
                    self.assertEqual(len(rejected), 1)
                    self.assertEqual(rejected[0]["source_test_case_id"], "TC-1")
                    self.assertIn("test_data", rejected[0]["reason"])
                    self.assertNotIn("secret-value", str(rejected) + str(logs.output))

    def test_structured_size_boundary_is_inclusive_and_text_is_not_truncated(self):
        # JSON list punctuation and quotes add four characters.
        value = ["a" * (MAX_STRUCTURED_TEST_DATA_CHARS - 4)]
        raw = case()
        raw["test_data"] = value
        self.assertEqual(len(_hydrate_test_cases([raw])[0].test_data), MAX_STRUCTURED_TEST_DATA_CHARS)
        raw["test_data"] = "a" * (MAX_STRUCTURED_TEST_DATA_CHARS + 1)
        self.assertEqual(_hydrate_test_cases([raw])[0].test_data, raw["test_data"])

    def test_mixed_final_validation_recomputes_delivery_and_blocks_approval_even_when_other_cases_cover_the_scenario(self):
        good = case()
        invalid = case("TC-invalid")
        invalid["title"] = {"sensitive": "must-not-be-logged"}
        rejected = []
        with self.assertLogs(level="WARNING") as logs:
            delivered = _hydrate_test_cases([good, invalid], rejections=rejected)
        result = _build_response(
            delivered,
            workflow([good, invalid]),
            [Requirement(id="REQ-1", text="Book Birch"), Requirement(id="REQ-2", text="Cancel")],
            None,
            contract_rejections=rejected,
        )
        response = GenerateTestCasesResponse(**result)
        self.assertEqual([c.id for c in response.test_cases], ["TC-1"])
        self.assertEqual(response.generation_evidence.final_test_case_count, 1)
        self.assertEqual(response.generation_evidence.missing_requirements_count, 1)
        self.assertEqual(response.coverage_metrics["requirements_covered"], 1)
        self.assertEqual(response.workflow_diagnostics.generation_source_counts, {"model": 1})
        self.assertFalse(response.approved)
        self.assertFalse(response.review.approved)
        self.assertEqual(response.review.score, 0)
        self.assertEqual(response.workflow_diagnostics.status, "partial")
        self.assertEqual(response.generation_evidence.warning_count, len(response.workflow_diagnostics.warnings))
        self.assertEqual(len([t for t in response.generation_tasks if t["source_test_case_id"] == "TC-invalid"]), 1)
        self.assertNotIn("must-not-be-logged", str(response.generation_tasks) + str(logs.output))

    def test_all_rejected_work_is_reported_once_per_case_and_never_as_success(self):
        raw = case()
        raw["steps"][0]["step"] = {"bad": "integer"}
        rejected = []
        delivered = _hydrate_test_cases([raw], rejections=rejected)
        response = _build_response(delivered, workflow([raw]), [Requirement(id="REQ-1", text="Book Birch")], None, contract_rejections=rejected)
        self.assertEqual(response["test_cases"], [])
        self.assertFalse(response["approved"])
        self.assertEqual(response["generation_evidence"]["final_test_case_count"], 0)
        self.assertEqual(response["generation_evidence"]["final_status"], "failed")
        self.assertEqual(response["workflow_diagnostics"]["failure_reason"], "final_contract_validation")
        self.assertEqual(len([t for t in response["generation_tasks"] if "SCN-1" in t["scenario_refs"]]), 1)

    def test_non_object_case_is_an_explicit_task_not_an_exception_or_silent_loss(self):
        rejected = []
        self.assertEqual(_hydrate_test_cases(["not-a-case"], rejections=rejected), [])
        self.assertEqual(len(rejected), 1)
        self.assertIn("expected an object", rejected[0]["reason"])

    def test_model_boundary_preserves_case_references_but_ignores_persistence_identifiers(self):
        original = TestCase(**case(), artifact_set_id="set", artifact_item_id="item", artifact_version_id="version", artifact_version_number=4)
        serialized = _serialize_test_cases([original])[0]
        persistence_fields = ("artifact_set_id", "artifact_item_id", "artifact_version_id", "artifact_version_number")
        for key in persistence_fields:
            self.assertNotIn(key, serialized)
        restored = _hydrate_test_cases([{**serialized, **{key: getattr(original, key) for key in persistence_fields}}])[0]
        for key in ("id", "linked_requirement_ids", "scenario_refs"):
            self.assertEqual(getattr(restored, key), getattr(original, key))
        for key in persistence_fields:
            self.assertIsNone(getattr(restored, key))

    def test_generate_and_refine_use_final_contract_validation_for_supported_and_rejected_data(self):
        common = {
            "requirements": [Requirement(id="REQ-1", text="Book Birch"), Requirement(id="REQ-2", text="Cancel Atlas")],
            "template": {"name": "minimal", "format": "json", "fields": ["id", "title"]},
        }
        for refine in (False, True):
            for invalid in (False, True):
                with self.subTest(refine=refine, invalid=invalid):
                    raw = [case(), case("TC-2", "REQ-2", "SCN-2")]
                    raw[0]["test_data"] = {"room": "Birch"}
                    raw[0]["steps"][0]["test_data"] = [1, 8]
                    if invalid:
                        raw[1]["test_data"] = 42
                    payload = (
                        RefineTestCasesInput(**common, test_cases=[TestCase(**case())], feedback="Add cancellation")
                        if refine
                        else GenerateTestCasesInput(**common)
                    )
                    with (
                        patch("app.agents.test_case_agent._get_model_settings_or_none", return_value=SimpleNamespace(model_name="mock")),
                        patch("app.agents.test_case_agent._run_workflow_sync", return_value=workflow(raw)),
                        patch("app.agents.test_case_agent._should_use_parallel_test_case_generation", return_value=False),
                    ):
                        result = (refine_test_cases if refine else generate_test_cases)(payload)
                    response = GenerateTestCasesResponse(**result)
                    self.assertEqual(response.generation_evidence.final_test_case_count, 1 if invalid else 2)
                    self.assertEqual(json.loads(response.test_cases[0].test_data), {"room": "Birch"})
                    self.assertEqual(json.loads(response.test_cases[0].steps[0].test_data), [1, 8])
                    self.assertEqual(response.approved, not invalid)
                    self.assertEqual(bool(response.generation_tasks), invalid)
