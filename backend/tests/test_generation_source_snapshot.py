"""Generation must not replace an unchanged reviewed upstream artifact."""

from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import AuthUser, GenerateTestCasesResponse
from app.routers.testcases import _append_project_generation_snapshots
from app.services.workflow_project_service import ProjectConflictError


class GenerationSourceSnapshotTests(unittest.TestCase):
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
        self.project = SimpleNamespace(current_snapshots={"use_cases": self.source}, stage_state={"use_cases": self.state})

    def persist(self, append_effect=None):
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
                operation="testcases.generate",
                actor=AuthUser(sub="tester", name="Synthetic tester"),
                request_id="request-1",
                workflow_run_id="run-1",
                source_event_id="event-1",
                base_project_revision=7,
            )
        return append

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


if __name__ == "__main__":
    unittest.main()
