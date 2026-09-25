from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.models import Requirement, TestCase, GenerateTestCasesInput, TestCaseTemplate, ExportTestCasesInput
from app.services.test_case_quality import placeholder_reason, project_test_case_view, separate_generation_work
from app.agents.test_case_agent import generate_test_cases
from app.routers.export import _export_audit_metadata
from fastapi import HTTPException


class TestCaseQualityTests(unittest.TestCase):
    def setUp(self):
        # These tests isolate generation/delivery. Independent review is covered separately.
        assessment = patch(
            "app.agents.test_case_agent.assess_delivered_suite", return_value={"status": "assessed_complete", "obligations": [], "case_grounding": []}
        )
        assessment.start()
        self.addCleanup(assessment.stop)

    def concrete(self, identifier="TC-FB-001"):
        return TestCase(
            id=identifier,
            title="Declined card",
            steps=[{"step": 1, "action": "Submit card 4000000000000002", "expected": "Payment declined appears; cart remains"}],
            linked_requirement_ids=["REQ-001"],
            scenario_refs=["SCN-DECLINE"],
        )

    def test_content_not_id_controls_classification_and_projection_is_immutable(self):
        good = self.concrete()
        bad = self.concrete("ordinary-id").model_dump()
        bad["steps"][0]["action"] = "Review the implemented behavior for REQ-001."
        payload = {"test_cases": [good.model_dump(), bad], "approved": True, "review": {"approved": True}}
        before = deepcopy(payload)
        view = project_test_case_view(payload)
        self.assertEqual(payload, before)
        self.assertEqual([r["id"] for r in view["test_cases"]], [good.id])
        self.assertFalse(view["approved"])
        self.assertFalse(view["review"]["approved"])
        self.assertEqual(view["generation_tasks"][0]["source_test_case_id"], "ordinary-id")
        self.assertIsNone(placeholder_reason(self.concrete("TC-IMPACT-REQ-001")))

    def test_missing_steps_and_scenarios_are_work_not_deliverables(self):
        bad = self.concrete().model_copy(update={"steps": []})
        rows, tasks = separate_generation_work([bad], [{"requirement_id": "REQ-001", "scenarios": [{"id": "SCN-DECLINE"}, {"id": "SCN-OTHER"}]}])
        self.assertEqual(rows, [])
        self.assertEqual({r for t in tasks for r in t["scenario_refs"]}, {"SCN-DECLINE", "SCN-OTHER"})
        with self.assertRaises(HTTPException) as caught:
            _export_audit_metadata(ExportTestCasesInput(test_cases=[bad], approved=True, draft_override_requested=True))
        self.assertEqual(caught.exception.status_code, 422)

    def test_targeted_generation_preserves_exact_reviewed_scenario_slice(self):
        requirement = Requirement(id="REQ-001", text="Declined cards keep cart contents", review_status="Approved")
        plan = [
            {
                "requirement_id": requirement.id,
                "requirement_text": requirement.text,
                "scenarios": [
                    {
                        "id": "SCN-DECLINE",
                        "requirement_id": requirement.id,
                        "scenario_type": "Negative",
                        "title": "Declined card",
                        "objective": "Declined card retains cart",
                    }
                ],
            }
        ]
        payload = GenerateTestCasesInput(
            requirements=[requirement], coverage_plan=plan, template=TestCaseTemplate(name="tests", format="json", fields=["steps"])
        )
        workflow = {"test_cases": [self.concrete().model_dump()], "coverage_plan": plan, "approved": True, "review": {"approved": True, "score": 95}}
        with (
            patch("app.agents.test_case_agent._get_model_settings_or_none", return_value=SimpleNamespace(model_name="mock")),
            patch("app.agents.test_case_agent._run_parallel_test_case_generation_sync", return_value=workflow) as parallel,
            patch("app.agents.test_case_agent._run_workflow_sync") as sequential,
        ):
            result = generate_test_cases(payload, operation="impact.update.apply")
        sequential.assert_not_called()
        self.assertEqual([s["id"] for p in parallel.call_args.kwargs["coverage_plan"] for s in p["scenarios"]], ["SCN-DECLINE"])
        self.assertEqual([s.id for p in result["coverage_plan"] for s in p.scenarios], ["SCN-DECLINE"])
        self.assertEqual(result["generation_tasks"], [])
        self.assertTrue(result["approved"])


class IncompleteGenerationEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_and_refine_preserve_existing_suite_before_version_writes(self):
        from contextlib import ExitStack
        from starlette.requests import Request
        from app.models import AuthUser, RefineTestCasesInput
        from app.routers import testcases as routes

        common = dict(
            requirements=[Requirement(id="REQ-001", text="Checkout")],
            template=TestCaseTemplate(name="tests", format="json", fields=["steps"]),
            project_id="project",
            base_project_revision=1,
        )
        for endpoint, generator, payload in [
            (routes.generate_test_cases_endpoint, "generate_test_cases", GenerateTestCasesInput(**common)),
            (routes.refine_test_cases_endpoint, "refine_test_cases", RefineTestCasesInput(**common, test_cases=[], feedback="Concrete actions")),
        ]:
            with self.subTest(generator=generator), ExitStack() as stack:
                for name in ("validate_generation_baseline", "enforce_billing_access", "start_workflow_run", "_log_failure"):
                    stack.enter_context(patch.object(routes, name))
                stack.enter_context(
                    patch.object(
                        routes,
                        generator,
                        return_value={
                            "test_cases": [],
                            "generation_tasks": [{"requirement_ids": ["REQ-001"], "scenario_refs": ["S1"], "reason": "Incomplete"}],
                        },
                    )
                )
                stack.enter_context(patch.object(routes, "get_project", return_value=SimpleNamespace(current_snapshots={"test_cases": object()})))
                versions = stack.enter_context(patch.object(routes, "persist_test_case_versions"))
                snapshot = stack.enter_context(patch.object(routes, "_append_project_generation_snapshots"))
                with self.assertRaises(HTTPException) as caught:
                    await endpoint(Request({"type": "http"}), payload, AuthUser(sub="owner", email="owner@example.com", name="Owner"))
                self.assertEqual(caught.exception.status_code, 422)
                versions.assert_not_called()
                snapshot.assert_not_called()
