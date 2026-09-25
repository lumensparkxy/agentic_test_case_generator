import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agents import use_case_agent as agent
from app.agents.scenario_assessment import assessment_input, assess_scenarios, combine_review, local_findings, parse_critic_output, CRITERIA
from app.models import Requirement, WorkflowSettings
from app.contracts.requirements import ReviewResult


REQ = Requirement(id="REQ-001", text="Booking accepts 1 to 8 attendees. Nine attendees are rejected without creating a booking.")


def plan(*scenarios):
    return [
        {
            "requirement_id": REQ.id,
            "requirement_text": REQ.text,
            "scenarios": [
                {
                    "id": f"REQ-001-SCN-{index:02}",
                    "requirement_id": REQ.id,
                    "title": title,
                    "objective": objective,
                    "scenario_type": "Happy Path" if index == 1 else "Negative",
                    "priority": "High",
                    "must_have": True,
                }
                for index, (title, objective) in enumerate(scenarios, 1)
            ],
        }
    ]


def passes(coverage, context=""):
    return [
        {
            "requirement_id": entry["requirement_id"],
            "scenario_id": entry["scenario"]["id"],
            "content_hash": entry["content_hash"],
            **{criterion: {"passed": True, "reason": "Concrete supplied input and observable result."} for criterion in CRITERIA},
        }
        for entry in assessment_input(coverage, [REQ], context)
    ]


class ScenarioAssessmentTests(unittest.TestCase):
    def test_all_nine_category_scaffolds_warn_even_if_critic_passes(self):
        categories = [
            "Happy Path",
            "Negative",
            "Boundary",
            "Validation",
            "Authorization",
            "State Transition",
            "Integration",
            "Error Handling",
            "Data Variation",
        ]
        coverage = plan(
            *[(f"{category} coverage for REQ-001", f"Validate requirement REQ-001 under {category.lower()} conditions.") for category in categories]
        )
        result = assess_scenarios(coverage, [REQ], "", passes(coverage))
        self.assertEqual(result["flagged_count"], 9)
        self.assertEqual(result["status"], "needs_review")
        for row in result["items"]:
            self.assertIn("concrete trigger and observable outcome", row["warnings"][0])

    def test_server_fallback_parenthetical_source_is_still_category_scaffolding(self):
        coverage = plan(("Negative coverage for REQ-001", f"Validate requirement REQ-001 ({REQ.text}) under negative conditions."))
        result = assess_scenarios(coverage, [REQ], "")
        self.assertEqual(result["flagged_count"], 1)
        self.assertFalse(result["items"][0]["review_available"])

    def test_semantic_timeout_survives_shard_merge(self):
        diagnostics = agent._new_use_case_diagnostics()
        shard = agent._UseCaseShard(index=1, requirements=[REQ])
        result = agent._UseCaseShardResult(
            shard=shard,
            requirement_analysis=[],
            coverage_plan=[],
            diagnostics={
                "status": "partial",
                "timed_out": True,
                "failure_reason": "semantic_review_timeout",
            },
        )
        agent._merge_shard_diagnostics(diagnostics, [result])
        self.assertTrue(diagnostics["timed_out"])
        self.assertEqual(diagnostics["failure_reason"], "semantic_review_timeout")
        self.assertEqual(diagnostics["status"], "partial")

    def test_short_title_or_category_word_is_not_a_failure(self):
        coverage = plan(
            ("Boundary", "Submit nine attendees; reject the request without a booking."),
            ("Nine attendees are rejected without a booking", "Validate requirement REQ-001 under negative conditions."),
        )
        self.assertEqual(local_findings(coverage, REQ.text), {(REQ.id, "REQ-001-SCN-01"): [], (REQ.id, "REQ-001-SCN-02"): []})
        result = assess_scenarios(coverage, [REQ], "", passes(coverage))
        self.assertEqual(result["status"], "passed")

    def test_duplicate_category_padding_flags_both_members(self):
        coverage = plan(("Negative", "Submit nine attendees; no booking is created."), ("Validation", "Submit nine attendees; no booking is created."))
        result = assess_scenarios(coverage, [REQ], "", passes(coverage))
        self.assertEqual(result["flagged_count"], 2)
        self.assertTrue(all("Duplicate" in row["warnings"][0] for row in result["items"]))

    def test_unsupported_prescriptions_need_confirmation(self):
        coverage = plan(("Create booking", "POST /api/bookings must debounce clicks for 500ms."))
        row = assess_scenarios(coverage, [REQ], "", passes(coverage))["items"][0]
        self.assertEqual(len(row["warnings"]), 2)
        self.assertTrue(all("assumption" in warning for warning in row["warnings"]))
        context = "The supplied interface uses POST /api/bookings and debounces clicks for 500ms."
        self.assertEqual(assess_scenarios(coverage, [REQ], context, passes(coverage, context))["status"], "passed")

    def test_missing_or_malformed_critic_is_unavailable_not_pass(self):
        coverage = plan(("Boundary", "Submit nine attendees; no booking is created."))
        for raw in ("", "not json", '{"assessments":[{"passed":true}]}', "null", "[]"):
            with self.subTest(raw=raw):
                result = assess_scenarios(coverage, [REQ], "", parse_critic_output(raw))
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["assessed_count"], 0)

    def test_string_booleans_blank_reasons_and_omitted_criteria_are_not_reviews(self):
        coverage = plan(("Boundary", "Submit nine attendees; no booking is created."))
        for mutate in (
            lambda x: x[0]["specific_trigger"].update(passed="false"),
            lambda x: x[0]["observable_outcome"].update(reason=""),
            lambda x: x[0].pop("distinct_behavior"),
        ):
            rows = passes(coverage)
            mutate(rows)
            self.assertEqual(assess_scenarios(coverage, [REQ], "", rows)["status"], "unavailable")

    def test_exact_content_and_source_binding_required(self):
        coverage = plan(("Boundary", "Submit nine attendees; no booking is created."))
        rows = passes(coverage)
        changed = deepcopy(coverage)
        changed[0]["scenarios"][0]["objective"] = "Submit two attendees; booking is created."
        for input_plan, reqs, context in (
            (changed, [REQ], ""),
            (coverage, [REQ], "New source context"),
            (coverage, [REQ.model_copy(update={"text": "Different requirement"})], ""),
        ):
            self.assertEqual(assess_scenarios(input_plan, reqs, context, rows)["status"], "unavailable")

    def test_duplicate_review_rows_and_missing_scenarios_cannot_pass(self):
        coverage = plan(("Accept", "Submit one attendee; booking created."), ("Reject", "Submit nine; no booking."))
        rows = passes(coverage)
        for invalid in (rows[:1], [rows[0], rows[0], rows[1]]):
            result = assess_scenarios(coverage, [REQ], "", invalid)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["assessed_count"], 1)

    def test_failed_criterion_preserves_actionable_reason(self):
        coverage = plan(("Save", "Submit data successfully."))
        rows = passes(coverage)
        rows[0]["observable_outcome"] = {"passed": False, "reason": "Name the observable persisted booking or error; success alone is ambiguous."}
        result = assess_scenarios(coverage, [REQ], "", rows)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("persisted booking", result["items"][0]["warnings"][0])

    def test_structural_100_is_not_semantic_approval_and_contract_retains_evidence(self):
        coverage = plan(("Boundary", "Submit nine attendees; no booking is created."))
        structural = {"approved": True, "score": 100, "threshold": 85, "summary": "Structural checks passed."}
        for rows, approved in ((None, False), (passes(coverage), True)):
            review = combine_review(structural, assess_scenarios(coverage, [REQ], "", rows))
            delivered = ReviewResult.model_validate(review).model_dump()
            self.assertEqual(delivered["approved"], approved)
            self.assertEqual(delivered["structural_checks"]["score"], 100)
            self.assertEqual(len(delivered["semantic_assessment"]["items"]), 1)

    def test_missing_credentials_fallback_cannot_approve_generic_scenarios(self):
        from app.models import GenerateTestCasesInput, TestCaseTemplate

        payload = GenerateTestCasesInput(requirements=[REQ], template=TestCaseTemplate(name="Use Cases", format="json", fields=[]))
        with patch.object(agent, "_get_model_settings_or_none", return_value=None):
            result = agent.generate_use_cases(payload)
        self.assertFalse(result["approved"])
        self.assertEqual(result["review"]["semantic_assessment"]["assessed_count"], 0)

    def test_critic_failure_preserves_delivered_plan_without_success(self):
        coverage = plan(("Boundary", "Submit nine attendees; no booking is created."))

        class FailingRunner:
            def __init__(self, **kwargs):
                pass

            async def run_async(self, **kwargs):
                yield SimpleNamespace(
                    author="CoveragePlannerAgent", content=SimpleNamespace(parts=[SimpleNamespace(text=json.dumps({"coverage_plan": coverage}))])
                )
                raise RuntimeError("synthetic reviewer outage")

        with patch.object(agent, "Runner", FailingRunner), patch.object(agent, "memory_service_for_run", return_value=None):
            result = asyncio.run(
                agent._run_single_use_case_shard_workflow_async(
                    shard=agent._UseCaseShard(index=1, requirements=[REQ]),
                    context=None,
                    requirements_text=REQ.text,
                    context_text="",
                    model="test-model",
                    workflow_settings=WorkflowSettings(timeout_seconds=5),
                )
            )
        self.assertEqual(result["coverage_plan"], coverage)
        self.assertEqual(result["semantic_rows"], [])
        self.assertIn("Semantic review did not finish", str(result["workflow_diagnostics"]))


if __name__ == "__main__":
    unittest.main()
