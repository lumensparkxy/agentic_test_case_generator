"""Exercise bounded model recovery without accepting fabricated coverage."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app.agents.analysis_agent import fallback_requirement_analysis
from app.agents.test_case_agent import _ParallelTestCaseShard, _run_parallel_test_case_shard_with_fallback
from app.models import Requirement, TestCaseTemplate, WorkflowSettings
from app.utils.llm_json import parse_test_cases_json_detailed


class PartialShardRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.requirement = Requirement(id="REQ-001", text="Accept attendee counts 1 and 8; reject 0.")
        self.plan = [{"requirement_id": "REQ-001", "scenarios": [{"id": f"S{i}"} for i in range(1, 4)]}]
        self.shard = _ParallelTestCaseShard(
            index=1, requirements=[self.requirement], requirement_analysis=fallback_requirement_analysis([self.requirement]), coverage_plan=self.plan
        )

    def case(self, scenario):
        return {
            "id": f"TC-{scenario}",
            "title": f"Attendee check {scenario}",
            "description": "Verify the specified attendee boundary.",
            "steps": [{"step": 1, "action": f"Enter the attendee count for {scenario} and click Book.", "expected": "The specified result appears."}],
            "scenario_refs": [scenario],
            "linked_requirement_ids": ["REQ-001"],
        }

    def workflow(self, cases, **diagnostics):
        return {"test_cases": cases, "workflow_diagnostics": {"status": "completed", **diagnostics}}

    def run_shard(self, responses, timeout=None):
        with patch("app.agents.test_case_agent._run_parallel_test_case_shard_workflow_sync", side_effect=responses) as model:
            result = _run_parallel_test_case_shard_with_fallback(
                shard=self.shard,
                context=None,
                template=TestCaseTemplate(name="test", format="json", fields=["steps"]),
                model="test-model",
                human_feedback="Keep exact boundary assertions.",
                workflow_settings=WorkflowSettings(timeout_seconds=timeout),
                actor_user_id="synthetic-reviewer",
            )
        return result, model

    def test_truncated_payload_recovers_only_missing_scenarios_and_preserves_good_cases(self):
        raw = '{"test_cases": [' + json.dumps(self.case("S1")) + ', {"id": "cut-off'
        parsed, error = parse_test_cases_json_detailed(raw)
        before = deepcopy(parsed)
        result, model = self.run_shard([self.workflow(parsed, parser_recoveries=[error]), self.workflow([self.case("S2"), self.case("S3")])])
        self.assertEqual(model.call_count, 2)
        recovery = model.call_args.kwargs
        self.assertEqual([s["id"] for p in recovery["shard"].coverage_plan for s in p["scenarios"]], ["S2", "S3"])
        self.assertEqual(recovery["human_feedback"], "Keep exact boundary assertions.")
        self.assertEqual(recovery["workflow_settings"].timeout_seconds, 120)
        self.assertEqual(result.test_cases, before + [self.case("S2"), self.case("S3")])
        self.assertEqual(parsed, before)
        self.assertEqual(result.diagnostics["attempt_count"], 2)
        self.assertEqual(result.diagnostics["recovery_reason"], "incomplete_shard_coverage")
        self.assertEqual(result.evidence["parser_recovery_count"], 1)
        self.assertFalse(result.used_fallback)

    def test_duplicate_case_count_does_not_hide_missing_scenario(self):
        result, model = self.run_shard([self.workflow([self.case("S1"), self.case("S1"), self.case("S2")]), self.workflow([self.case("S3")])], timeout=15)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(model.call_args.kwargs["shard"].coverage_plan[0]["scenarios"], [{"id": "S3"}])
        self.assertEqual(model.call_args.kwargs["workflow_settings"].timeout_seconds, 15)
        self.assertIn(self.case("S3"), result.test_cases)

    def test_invalid_placeholder_and_unrelated_recovery_cannot_close_missing_coverage(self):
        bad_contract = {**self.case("S2"), "preconditions": {"invalid": True}}
        placeholder = {**self.case("S2"), "steps": []}
        wrong_owner = {**self.case("S3"), "linked_requirement_ids": ["REQ-999"]}
        result, model = self.run_shard(
            [
                self.workflow([self.case("S1"), bad_contract, placeholder]),
                self.workflow([self.case("S1"), bad_contract, placeholder, wrong_owner, self.case("UNKNOWN")]),
            ]
        )
        self.assertEqual(model.call_count, 2)
        self.assertEqual(result.test_cases, [self.case("S1")])
        self.assertEqual(result.diagnostics["failure_reason"], "incomplete_shard_coverage")
        self.assertIn("Still missing: S2, S3", " ".join(result.diagnostics["warnings"]))

    def test_recovery_exception_keeps_initial_cases_and_does_not_retry_again(self):
        result, model = self.run_shard([self.workflow([self.case("S1")]), RuntimeError("private upstream details")])
        self.assertEqual(model.call_count, 2)
        self.assertEqual(result.test_cases, [self.case("S1")])
        self.assertFalse(result.used_fallback)
        self.assertNotIn("private upstream details", str(result.diagnostics))

    def test_recovery_fallback_does_not_count_as_model_completion(self):
        result, model = self.run_shard(
            [self.workflow([self.case("S1")]), self.workflow([self.case("S2"), self.case("S3")], used_fallback=True, timed_out=True)]
        )
        self.assertEqual(model.call_count, 2)
        self.assertEqual(result.test_cases, [self.case("S1")])
        self.assertTrue(result.diagnostics["timed_out"])

    def test_recovery_discards_duplicates_and_already_covered_cases(self):
        result, _ = self.run_shard([self.workflow([self.case("S1")]), self.workflow([self.case("S1"), self.case("S2"), self.case("S2"), self.case("S3")])])
        self.assertEqual(result.test_cases, [self.case("S1"), self.case("S2"), self.case("S3")])

    def test_complete_shard_and_initial_fallback_make_no_recovery_call(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                cases = [self.case("S1")] if fallback else [self.case(f"S{i}") for i in range(1, 4)]
                result, model = self.run_shard([self.workflow(cases, used_fallback=fallback)])
                self.assertEqual(model.call_count, 1)
                self.assertEqual(result.test_cases, cases)
