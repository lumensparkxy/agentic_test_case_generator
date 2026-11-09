"""Generation must not replace an unchanged reviewed upstream artifact."""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import AuthUser, GenerateTestCasesInput, GenerateTestCasesResponse
from app.routers import testcases as routes
from app.routers.testcases import _append_project_generation_snapshots, _refinement_source_inputs
from app.services.test_case_review_progress import GenerationReviewProgress
from app.services.workflow_project_service import ProjectConflictError


class GenerationSourceSnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.response = GenerateTestCasesResponse(
            test_cases=[],
            coverage_plan=[
                {
                    "requirement_id": "REQ-1",
                    "requirement_text": "Create one booking",
                    "scenarios": [{"id": "SCN-1", "requirement_id": "REQ-1", "title": "Book", "objective": "One durable booking"}],
                }
            ],
            approved=False,
        )
        self.source = SimpleNamespace(
            snapshot_id="reviewed-source",
            payload={
                "coverage_plan": [p.model_dump(mode="json") for p in self.response.coverage_plan],
                "requirement_analysis": [{"requirement_id": "REQ-1", "summary": "Reviewed source analysis"}],
                "semantic_assessment": {"status": "passed"},
                "review": {"approved": True},
            },
        )
        self.state = SimpleNamespace(
            current_snapshot_id=self.source.snapshot_id,
            stale=False,
            approved=True,
            metadata={"latest_human_review": {"decision": "approve", "snapshot_id": self.source.snapshot_id}},
        )
        self.project = SimpleNamespace(current_revision=7, current_snapshots={"use_cases": self.source}, stage_state={"use_cases": self.state})
        self.actor = AuthUser(sub="tester", name="Synthetic tester")
        self.payload = GenerateTestCasesInput(
            project_id="project-1",
            requirements=[{"id": "REQ-1", "text": "Create one booking"}],
            template={"name": "QA", "format": "json", "fields": ["steps"]},
        )

    def persist(self, append_effect=None, operation="testcases.generate", **source):
        with (
            patch("app.routers.testcases.get_project", return_value=self.project),
            patch(
                "app.routers.testcases.append_stage_snapshot",
                side_effect=append_effect,
                return_value=SimpleNamespace(snapshot_id="new-snapshot"),
            ) as append,
        ):
            _append_project_generation_snapshots(
                project_id="project-1",
                response=self.response,
                operation=operation,
                actor=AuthUser(sub="tester", name="Synthetic tester"),
                request_id="request-1",
                workflow_run_id="run-1",
                source_event_id="event-1",
                base_project_revision=7,
                **source,
            )
        return append

    async def test_generation_binds_server_approved_plan_before_model_and_persists_only_test_cases(self):
        # Firestore omits nulls, and a caller can send an older client-side plan.
        self.source.payload["coverage_plan"] = [p.model_dump(mode="json", exclude_none=True) for p in self.response.coverage_plan]
        self.payload.coverage_plan = [p.model_copy(update={"requirement_text": "Obsolete client input"}) for p in self.response.coverage_plan]
        before = deepcopy(self.project)
        with ExitStack() as stack:
            for name in ("validate_generation_baseline", "enforce_billing_access", "start_workflow_run", "_log_success", "_record_billing_consumption_safe"):
                stack.enter_context(patch.object(routes, name))
            stack.enter_context(patch.object(routes, "get_project", return_value=self.project))
            generate = stack.enter_context(patch.object(routes, "generate_test_cases", return_value=self.response.model_dump()))
            versions = stack.enter_context(patch.object(routes, "persist_test_case_versions", return_value=[]))
            append = stack.enter_context(patch.object(routes, "append_stage_snapshot"))
            stack.enter_context(patch.object(routes, "finish_generation", side_effect=lambda response, *_: response))
            failure = stack.enter_context(patch.object(routes, "_log_failure"))

            await routes.generate_test_cases_endpoint(Request({"type": "http"}), self.payload, self.actor)

            self.assertEqual(generate.call_args.kwargs["source_coverage_plan"], [p.model_dump(mode="json") for p in self.response.coverage_plan])
            self.assertEqual(generate.call_args.kwargs["source_requirement_analysis"], self.source.payload["requirement_analysis"])
            self.assertIsInstance(generate.call_args.kwargs["review_progress"], GenerationReviewProgress)
            append.assert_called_once()
            self.assertEqual(append.call_args.kwargs["stage"], "test_cases")
            self.assertEqual(append.call_args.kwargs["source_snapshot_id"], self.source.snapshot_id)
            self.assertEqual(append.call_args.kwargs["base_project_revision"], 7)
            self.assertEqual(append.call_args.kwargs["transaction_guard"], generate.call_args.kwargs["review_progress"].publish_on_commit)
            self.assertEqual(self.project, before)

            append.reset_mock()
            versions.reset_mock()
            generate.return_value["coverage_plan"][0]["scenarios"][0]["objective"] = "Replanned by the model"
            with self.assertRaises(HTTPException) as error:
                await routes.generate_test_cases_endpoint(Request({"type": "http"}), self.payload, self.actor)
            self.assertEqual(error.exception.status_code, 409)
            failure.assert_called_once()
            append.assert_not_called()
            versions.assert_not_called()

    def test_generation_keeps_legacy_path_without_a_current_approval(self):
        with patch.object(routes, "get_project", return_value=self.project):
            for stale, approved, review_id in [(True, True, self.source.snapshot_id), (False, False, self.source.snapshot_id), (False, True, "old")]:
                with self.subTest(stale=stale, approved=approved, review_id=review_id):
                    self.state.stale = stale
                    self.state.approved = approved
                    self.state.metadata["latest_human_review"]["snapshot_id"] = review_id
                    self.assertEqual(routes._project_generation_source(self.payload, self.actor), (7, None, {}))
            self.payload.project_id = None
            self.assertEqual(routes._project_generation_source(self.payload, self.actor), (None, None, {}))

    def test_bound_generation_can_scope_approved_plan_without_replacing_it(self):
        other_group = self.response.coverage_plan[0].model_copy(update={"requirement_id": "REQ-2"})
        self.source.payload["coverage_plan"].append(other_group.model_dump(mode="json"))
        with patch.object(routes, "get_project", return_value=self.project):
            revision, snapshot_id, sources = routes._project_generation_source(self.payload, self.actor)
        self.assertEqual(revision, 7)
        self.assertEqual([group["requirement_id"] for group in sources["source_coverage_plan"]], ["REQ-1"])
        self.persist(approved_use_case_snapshot_id=snapshot_id).assert_called_once()

    def test_bound_generation_rejects_new_snapshot(self):
        self.source.snapshot_id = self.state.current_snapshot_id = "new-reviewed-source"
        with self.assertRaises(HTTPException) as error:
            self.persist(approved_use_case_snapshot_id="reviewed-source")
        self.assertEqual(error.exception.status_code, 409)

    def test_unchanged_plan_preserves_source_review_analysis_and_identity(self):
        before = deepcopy(self.project)
        append = self.persist()
        append.assert_called_once()
        args = append.call_args.kwargs
        self.assertEqual(args["stage"], "test_cases")
        self.assertEqual(args["source_snapshot_id"], "reviewed-source")
        self.assertEqual(args["metadata"]["source_use_case_snapshot_id"], "reviewed-source")
        self.assertEqual(args["base_project_revision"], 7)
        self.assertFalse(args["approved"])
        self.assertEqual(self.project, before)

    def test_reusing_unapproved_source_does_not_create_human_approval(self):
        self.state.approved = False
        self.state.metadata = {}
        append = self.persist()
        append.assert_called_once()
        self.assertFalse(self.state.approved)
        self.assertEqual(self.state.metadata, {})

    def test_changed_stale_empty_or_missing_source_requires_new_unapproved_snapshot(self):
        original = deepcopy(self.project)
        for condition in ("changed", "stale", "empty", "missing", "missing-state", "different-state-id"):
            with self.subTest(condition=condition):
                self.project = deepcopy(original)
                if condition == "changed":
                    self.project.current_snapshots["use_cases"].payload["coverage_plan"][0]["scenarios"][0]["objective"] = "Changed obligation"
                elif condition == "stale":
                    self.project.stage_state["use_cases"].stale = True
                elif condition == "empty":
                    self.project.current_snapshots["use_cases"].payload["coverage_plan"] = []
                elif condition == "missing":
                    self.project.current_snapshots = {}
                elif condition == "missing-state":
                    self.project.stage_state = {}
                else:
                    self.project.stage_state["use_cases"].current_snapshot_id = "different-source"
                append = self.persist()
                self.assertEqual(append.call_count, 2)
                use_cases, tests = append.call_args_list
                self.assertEqual(use_cases.kwargs["stage"], "use_cases")
                self.assertFalse(use_cases.kwargs["approved"])
                self.assertEqual(use_cases.kwargs["base_project_revision"], 7)
                self.assertEqual(tests.kwargs["source_snapshot_id"], "new-snapshot")

    def test_empty_plans_do_not_reuse_source_approval(self):
        self.response.coverage_plan = []
        self.source.payload["coverage_plan"] = []
        self.assertEqual(self.persist().call_count, 2)

    def test_reused_source_still_enforces_client_revision(self):
        def concurrent_write(**kwargs):
            self.assertEqual(kwargs["stage"], "test_cases")
            self.assertEqual(kwargs["base_project_revision"], 7)
            raise ProjectConflictError(8)

        with self.assertRaises(HTTPException) as error:
            self.persist(concurrent_write)
        self.assertEqual(error.exception.status_code, 409)

    def test_refinement_binds_current_plan_without_replacing_reviewed_source(self):
        before = deepcopy(self.project)
        with patch("app.routers.testcases.get_project", return_value=self.project):
            source = _refinement_source_inputs("project-1", AuthUser(sub="tester", name="Synthetic tester"))
        self.assertEqual(source["source_coverage_plan"], self.source.payload["coverage_plan"])
        self.assertEqual(source["source_requirement_analysis"], self.source.payload["requirement_analysis"])
        append = self.persist(operation="testcases.refine")
        append.assert_called_once()
        self.assertEqual(append.call_args.kwargs["source_snapshot_id"], self.source.snapshot_id)
        self.assertEqual(self.project, before)

    def test_refinement_rejects_missing_stale_and_changed_source(self):
        for stale in (True, False):
            with self.subTest(stale=stale):
                self.state.stale = stale
                if not stale:
                    self.source.payload["coverage_plan"] = []
                with patch("app.routers.testcases.get_project", return_value=self.project), self.assertRaises(HTTPException) as error:
                    _refinement_source_inputs("project-1", AuthUser(sub="tester", name="Synthetic tester"))
                self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPException) as error:
            self.persist(operation="testcases.refine")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(_refinement_source_inputs(None, AuthUser(sub="tester", name="Synthetic tester")), {})


if __name__ == "__main__":
    unittest.main()
