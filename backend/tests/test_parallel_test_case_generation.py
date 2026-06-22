from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.analysis_agent import fallback_requirement_analysis
from app.agents.test_case_agent import _plan_parallel_test_case_shards, generate_test_cases
from app.agents.test_case_coverage import _fallback_coverage_plan
from app.config import GenerationSettings
from app.models import GenerateTestCasesInput, GenerateTestCasesResponse, Requirement, TestCaseTemplate


def _requirements(count: int) -> list[Requirement]:
    return [
        Requirement(
            id=f"REQ-{index:03d}",
            text=f"The system shall support workflow {index} with validation and error handling.",
            review_status="Approved",
        )
        for index in range(1, count + 1)
    ]


def _payload(requirement_count: int) -> GenerateTestCasesInput:
    requirements = _requirements(requirement_count)
    return GenerateTestCasesInput(
        requirements=requirements,
        template=TestCaseTemplate(
            name="Standard QA",
            format="structured",
            fields=["id", "title", "steps", "expected_result"],
        ),
        requirement_analysis=fallback_requirement_analysis(requirements),
        coverage_plan=_fallback_coverage_plan(requirements),
    )


def _scenario(requirement_id: str, index: int) -> dict:
    return {
        "id": f"{requirement_id}-SCN-{index:02d}",
        "requirement_id": requirement_id,
        "scenario_type": "Happy Path",
        "title": f"Scenario {index} for {requirement_id}",
        "objective": f"Validate scenario {index} for {requirement_id}.",
        "priority": "High",
        "must_have": True,
    }


def _raw_case(case_id: str, requirement_id: str, scenario_ref: str) -> dict:
    return {
        "id": case_id,
        "title": f"Generated coverage for {requirement_id}",
        "description": f"Verify {requirement_id} behavior.",
        "priority": "High",
        "type": "Functional",
        "status": "Draft",
        "preconditions": "Synthetic fixture precondition.",
        "steps": [{"step": 1, "action": f"Run {requirement_id}", "expected": "The behavior is verified."}],
        "expected_result": f"{requirement_id} is covered.",
        "automation_status": "To Be Automated",
        "tags": [requirement_id],
        "linked_requirement_ids": [requirement_id],
        "scenario_refs": [scenario_ref],
    }


def _worker_output(shard, **_kwargs):
    test_cases = []
    for plan in shard.coverage_plan:
        requirement_id = plan["requirement_id"]
        for scenario in plan["scenarios"]:
            test_cases.append(
                {
                    "id": "TC-001",
                    "title": f"{scenario['scenario_type']} for {requirement_id}",
                    "description": scenario["objective"],
                    "priority": scenario["priority"],
                    "type": "Functional",
                    "status": "Draft",
                    "preconditions": "Synthetic fixture precondition.",
                    "steps": [
                        {
                            "step": 1,
                            "action": f"Run {scenario['scenario_type']} flow for {requirement_id}",
                            "expected": scenario["objective"],
                            "test_data": None,
                        }
                    ],
                    "expected_result": scenario["objective"],
                    "test_data": None,
                    "estimated_time": "5 mins",
                    "automation_status": "To Be Automated",
                    "component": "Synthetic",
                    "tags": [],
                    "linked_requirement_ids": [],
                    "scenario_refs": [scenario["id"], scenario["id"]],
                    "source_refs": [],
                }
            )
    return {
        "test_cases": test_cases,
        "workflow_diagnostics": {
            "status": "completed",
            "used_fallback": False,
            "warnings": [],
            "parser_failures": [],
            "parser_recoveries": [],
        },
    }


class ParallelTestCaseGenerationTests(unittest.TestCase):
    def test_plans_121_scenarios_into_bounded_shards_independent_of_workers(self) -> None:
        requirements = _requirements(11)
        coverage_plan = [
            {
                "requirement_id": requirement.id,
                "requirement_text": requirement.text,
                "scenarios": [_scenario(requirement.id, index) for index in range(1, 12)],
            }
            for requirement in requirements
        ]

        shards = _plan_parallel_test_case_shards(
            requirements,
            fallback_requirement_analysis(requirements),
            coverage_plan,
            target_scenarios_per_shard=10,
            max_shards=24,
        )

        self.assertEqual(sum(len(item.get("scenarios") or []) for shard in shards for item in shard.coverage_plan), 121)
        self.assertEqual(len(shards), 11)
        self.assertTrue(all(sum(len(item.get("scenarios") or []) for item in shard.coverage_plan) == 11 for shard in shards))
        self.assertGreater(len(shards), 3)

    def test_large_precomputed_use_case_artifacts_use_parallel_generation(self) -> None:
        payload = _payload(3)

        with (
            patch("app.agents.test_case_agent._get_model_settings_or_none", return_value=SimpleNamespace(model_name="test-model")),
            patch("app.agents.test_case_agent._run_workflow_sync") as sequential_mock,
            patch("app.agents.test_case_agent._run_parallel_test_case_shard_workflow_sync", side_effect=_worker_output) as worker_mock,
        ):
            result = generate_test_cases(payload)

        sequential_mock.assert_not_called()
        self.assertEqual(worker_mock.call_count, result["workflow_diagnostics"]["shard_count"])
        self.assertTrue(result["approved"])
        self.assertEqual(result["workflow_diagnostics"]["generation_route"], "direct_parallel")
        self.assertEqual(result["workflow_diagnostics"]["worker_count"], result["workflow_diagnostics"]["shard_count"])
        self.assertEqual(result["workflow_diagnostics"]["failed_shard_count"], 0)
        self.assertEqual(result["workflow_diagnostics"]["fallback_shard_count"], 0)
        evidence = result["generation_evidence"]
        self.assertEqual(evidence["passes"][0]["pass_type"], "parallel_direct")
        self.assertEqual(len(evidence["passes"][0]["shards"]), result["workflow_diagnostics"]["shard_count"])
        self.assertTrue(all(shard["raw_output_count"] > 0 for shard in evidence["passes"][0]["shards"]))
        self.assertFalse(evidence["passes"][0]["raw_output_summary"]["raw_content_stored"])
        self.assertEqual(result["workflow_diagnostics"]["generation_source_counts"], {"model": len(result["test_cases"])})
        GenerateTestCasesResponse(**result)

    def test_large_precomputed_plan_can_use_more_shards_than_workers(self) -> None:
        payload = _payload(8)
        generation_settings = GenerationSettings(
            parallel_test_case_min_scenarios=8,
            parallel_test_case_max_workers=2,
            parallel_test_case_target_scenarios_per_shard=8,
            parallel_test_case_max_shards=20,
        )

        with (
            patch("app.agents.test_case_agent._get_model_settings_or_none", return_value=SimpleNamespace(model_name="test-model")),
            patch("app.agents.test_case_agent.get_generation_settings", return_value=generation_settings),
            patch("app.agents.test_case_agent._run_workflow_sync") as sequential_mock,
            patch("app.agents.test_case_agent._run_parallel_test_case_shard_workflow_sync", side_effect=_worker_output) as worker_mock,
        ):
            result = generate_test_cases(payload)

        sequential_mock.assert_not_called()
        self.assertGreater(worker_mock.call_count, generation_settings.parallel_test_case_max_workers)
        self.assertEqual(result["workflow_diagnostics"]["shard_count"], worker_mock.call_count)
        self.assertEqual(result["workflow_diagnostics"]["worker_count"], generation_settings.parallel_test_case_max_workers)
        self.assertEqual(result["workflow_diagnostics"]["generation_route"], "direct_parallel")

    def test_parallel_merge_remaps_duplicate_ids_and_repairs_traceability(self) -> None:
        payload = _payload(3)

        with (
            patch("app.agents.test_case_agent._get_model_settings_or_none", return_value=SimpleNamespace(model_name="test-model")),
            patch("app.agents.test_case_agent._run_parallel_test_case_shard_workflow_sync", side_effect=_worker_output),
        ):
            result = generate_test_cases(payload)

        test_cases = result["test_cases"]
        ids = [test_case.id for test_case in test_cases]
        self.assertEqual(ids, [f"TC-{index:03d}" for index in range(1, len(test_cases) + 1)])
        self.assertTrue(all(test_case.linked_requirement_ids for test_case in test_cases))
        self.assertTrue(all(len(test_case.scenario_refs) == len(set(test_case.scenario_refs)) for test_case in test_cases))
        self.assertTrue(all(test_case.generation_source == "model" for test_case in test_cases))
        self.assertTrue(all(test_case.source_case_id == "TC-001" for test_case in test_cases))
        self.assertTrue(all(test_case.source_shard_id for test_case in test_cases))
        self.assertTrue(all(test_case.generation_pass_id == result["generation_evidence"]["passes"][0]["pass_id"] for test_case in test_cases))
        self.assertIn("Remapped worker-generated test-case IDs", " ".join(result["workflow_diagnostics"]["merge_warnings"]))
        self.assertEqual(result["coverage_metrics"]["scenario_coverage_ratio"], 1.0)

    def test_failed_parallel_shard_falls_back_for_affected_group_only(self) -> None:
        payload = _payload(3)
        failed_shard_plan = []

        def maybe_fail(shard, **kwargs):
            if shard.index == 2:
                failed_shard_plan.extend(shard.coverage_plan)
                raise RuntimeError("synthetic shard failure")
            return _worker_output(shard, **kwargs)

        with (
            patch("app.agents.test_case_agent._get_model_settings_or_none", return_value=SimpleNamespace(model_name="test-model")),
            patch("app.agents.test_case_agent._run_parallel_test_case_shard_workflow_sync", side_effect=maybe_fail),
        ):
            result = generate_test_cases(payload)

        linked_requirement_ids = {requirement_id for test_case in result["test_cases"] for requirement_id in test_case.linked_requirement_ids}
        self.assertEqual(linked_requirement_ids, {"REQ-001", "REQ-002", "REQ-003"})
        self.assertTrue(result["approved"])
        self.assertTrue(result["workflow_diagnostics"]["used_fallback"])
        self.assertEqual(result["workflow_diagnostics"]["failed_shard_count"], 1)
        self.assertEqual(result["workflow_diagnostics"]["fallback_shard_count"], 1)
        self.assertEqual(result["workflow_diagnostics"]["failure_reason"], "shard_fallback")
        shard_evidence = result["generation_evidence"]["passes"][0]["shards"]
        self.assertEqual(sum(1 for shard in shard_evidence if shard["used_fallback"]), 1)
        expected_fallback_count = sum(len(item.get("scenarios") or []) for item in failed_shard_plan)
        self.assertEqual(sum(shard["fallback_case_count"] for shard in shard_evidence), expected_fallback_count)
        self.assertGreater(result["generation_evidence"]["passes"][0]["model_case_count_before_review"], 0)
        self.assertEqual(result["generation_evidence"]["passes"][0]["review_status"], "approved")
        source_counts = result["workflow_diagnostics"]["generation_source_counts"]
        self.assertGreater(source_counts["model"], 0)
        self.assertEqual(source_counts["deterministic_full_fallback"], expected_fallback_count)
        fallback_cases = [test_case for test_case in result["test_cases"] if test_case.generation_source == "deterministic_full_fallback"]
        self.assertEqual(len(fallback_cases), expected_fallback_count)
        self.assertTrue(all(test_case.source_shard_id == "test-case-shard-02" for test_case in fallback_cases))
        self.assertTrue(all(test_case.source_case_id for test_case in fallback_cases))
        GenerateTestCasesResponse(**result)

    def test_lower_count_parallel_retry_records_review_tradeoff_diagnostics(self) -> None:
        requirements = _requirements(4)
        coverage_plan = _fallback_coverage_plan(requirements)
        template = TestCaseTemplate(name="Standard QA", format="structured", fields=["id", "title", "steps", "expected_result"])
        payload = GenerateTestCasesInput(requirements=requirements, template=template)
        sequential_cases = [
            _raw_case(
                f"TC-MODEL-{index:03d}", requirements[(index - 1) % len(requirements)].id, coverage_plan[(index - 1) % len(requirements)]["scenarios"][0]["id"]
            )
            for index in range(1, 9)
        ]
        parallel_cases = [
            _raw_case(f"TC-PAR-{index:03d}", requirement.id, coverage_plan[index - 1]["scenarios"][0]["id"])
            for index, requirement in enumerate(requirements, start=1)
        ]
        sequential_workflow = {
            "test_cases": sequential_cases,
            "requirement_analysis": fallback_requirement_analysis(requirements),
            "coverage_plan": coverage_plan,
            "approved": False,
            "review": {
                "approved": False,
                "score": 25,
                "threshold": 90,
                "summary": "Sequential generation under-produced required coverage.",
                "blocking_issues": ["Synthetic under-production."],
                "suggestions": [],
                "unmet_criteria": ["Synthetic under-production."],
            },
            "iteration_history": [],
            "coverage_metrics": {},
            "workflow_settings": {"approval_threshold": 90},
            "workflow_diagnostics": {"status": "partial", "failure_reason": "quality_rejection", "warnings": []},
        }
        parallel_workflow = {
            "test_cases": parallel_cases,
            "requirement_analysis": fallback_requirement_analysis(requirements),
            "coverage_plan": coverage_plan,
            "approved": True,
            "review": {
                "approved": True,
                "score": 95,
                "threshold": 90,
                "summary": "Parallel retry improved review quality.",
                "blocking_issues": [],
                "suggestions": [],
                "unmet_criteria": [],
            },
            "iteration_history": [],
            "coverage_metrics": {},
            "workflow_settings": {"approval_threshold": 90},
            "workflow_diagnostics": {"status": "completed", "warnings": []},
            "generation_evidence": {"passes": [{"pass_type": "parallel_direct", "pass_id": "parallel-pass"}]},
        }

        with (
            patch("app.agents.test_case_agent._get_model_settings_or_none", return_value=SimpleNamespace(model_name="test-model")),
            patch("app.agents.test_case_agent._run_workflow_sync", return_value=sequential_workflow),
            patch("app.agents.test_case_agent._run_parallel_test_case_generation_sync", return_value=parallel_workflow),
        ):
            result = generate_test_cases(payload)

        self.assertEqual(result["workflow_diagnostics"]["generation_route"], "parallel_retry")
        self.assertTrue(
            any("Accepted lower-count parallel retry because review quality improved" in warning for warning in result["workflow_diagnostics"]["warnings"])
        )

    def test_small_precomputed_suite_uses_existing_sequential_workflow(self) -> None:
        payload = _payload(1)
        sequential_workflow = {
            "test_cases": _worker_output(SimpleNamespace(coverage_plan=[item.model_dump(mode="json") for item in payload.coverage_plan]))["test_cases"],
            "requirement_analysis": [item.model_dump(mode="json") for item in payload.requirement_analysis],
            "coverage_plan": [item.model_dump(mode="json") for item in payload.coverage_plan],
            "approved": True,
            "review": {
                "approved": True,
                "score": 100,
                "threshold": 90,
                "summary": "Approved",
                "blocking_issues": [],
                "suggestions": [],
                "unmet_criteria": [],
            },
            "iteration_history": [],
            "coverage_metrics": {},
            "workflow_settings": {},
            "workflow_diagnostics": {"status": "completed"},
        }

        with (
            patch("app.agents.test_case_agent._get_model_settings_or_none", return_value=SimpleNamespace(model_name="test-model")),
            patch("app.agents.test_case_agent._run_workflow_sync", return_value=sequential_workflow) as sequential_mock,
            patch("app.agents.test_case_agent._run_parallel_test_case_shard_workflow_sync") as worker_mock,
        ):
            result = generate_test_cases(payload)

        sequential_mock.assert_called_once()
        worker_mock.assert_not_called()
        self.assertTrue(result["approved"])
        self.assertEqual(result["generation_evidence"]["passes"][0]["pass_type"], "sequential")
        self.assertEqual(result["generation_evidence"]["passes"][0]["review_status"], "approved")
        self.assertEqual(result["workflow_diagnostics"]["generation_route"], "sequential")
        self.assertEqual(result["workflow_diagnostics"]["generation_source_counts"], {"model": len(result["test_cases"])})
        self.assertTrue(all(test_case.generation_source == "model" for test_case in result["test_cases"]))


if __name__ == "__main__":
    unittest.main()
