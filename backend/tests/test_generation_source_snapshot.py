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
from app.models import AuthUser, GenerateTestCasesInput, GenerateTestCasesResponse, RefineTestCasesInput
from app.routers import testcases as routes
from app.routers.testcases import _append_project_generation_snapshots, _refinement_source_inputs
from app.contracts.billing import BillingAccount, BillingAllocation, BillingUserProfile
from app.services.billing_repository import build_allocation_id
from app.services.billing_service import BillingAccessContext
from app.services.test_case_review_progress import GenerationReviewProgress
from app.services.workflow_project_service import ProjectConflictError, append_stage_snapshot, create_project, get_project, update_project
from test_use_case_review_service import FakeFirestoreClient, _run_transaction


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
        self.project = SimpleNamespace(status="active", current_revision=7, current_snapshots={"use_cases": self.source}, stage_state={"use_cases": self.state})
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
            for name in (
                "validate_generation_baseline",
                "enforce_billing_access",
                "get_required_firestore_client",
                "complete_workflow_run",
                "record_usage_event",
            ):
                stack.enter_context(patch.object(routes, name))
            stack.enter_context(patch.object(routes, "_test_case_publication_receipt", return_value=None))
            stack.enter_context(patch.object(routes, "start_workflow_run", return_value="generation-run"))
            stack.enter_context(patch.object(routes, "get_project", return_value=self.project))
            generate = stack.enter_context(patch.object(routes, "generate_test_cases", return_value=self.response.model_dump()))
            versions = stack.enter_context(patch.object(routes, "persist_test_case_versions", return_value=[]))
            append = stack.enter_context(
                patch.object(
                    routes,
                    "append_stage_snapshot",
                    side_effect=lambda **kw: SimpleNamespace(payload=kw["payload"], metadata=kw["metadata"], workflow_run_id=kw["workflow_run_id"]),
                )
            )
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
            self.assertTrue(callable(append.call_args.kwargs["transaction_guard"]))
            self.assertTrue(append.call_args.kwargs["idempotency_key"])
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


class GenerationPublicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = {}
        self.client = FakeFirestoreClient(self.store)
        stack = self.enterContext(ExitStack())
        for target in ("app.services.workflow_project_service", "app.routers.testcases"):
            stack.enter_context(patch(f"{target}.get_required_firestore_client", return_value=self.client))
        for target, adapter in (
            ("workflow_project_service", "get_required_firestore_collection"),
            ("versioning_service", "get_optional_firestore_collection"),
            ("audit_repository", "get_optional_firestore_collection"),
        ):
            stack.enter_context(patch(f"app.services.{target}.{adapter}", side_effect=lambda name, **kwargs: self.client.collection(name)))
        stack.enter_context(patch("app.services.workflow_project_service.transactional", side_effect=_run_transaction))
        stack.enter_context(patch("app.services.billing_service.calculate_units_for_quantity", return_value=7))
        stack.enter_context(patch.object(routes, "validate_generation_baseline"))

        self.actor = AuthUser(sub="generation-tester", name="Synthetic tester")
        self.project = create_project(name="Atomic generation", description=None, actor=self.actor, request_id="project-setup")
        self.project_path = f"qa_projects/{self.project.project_id}"
        req = {"id": "REQ-1", "text": "Save the confirmed order", "review_status": "Approved"}
        append_stage_snapshot(
            project_id=self.project.project_id, stage="requirements", payload={"requirements": [req]}, operation="test-seed", actor=self.actor, request_id="req"
        )
        plan = [
            {
                "requirement_id": req["id"],
                "requirement_text": req["text"],
                "scenarios": [{"id": "SCN-1", "requirement_id": req["id"], "title": "Order", "objective": "Save confirmation"}],
            }
        ]
        source = append_stage_snapshot(
            project_id=self.project.project_id,
            stage="use_cases",
            payload={"coverage_plan": plan},
            operation="test-seed",
            actor=self.actor,
            request_id="use-cases",
            approved=True,
        )
        self.store[self.project_path]["stage_state"]["use_cases"]["metadata"]["latest_human_review"] = {
            "decision": "approve",
            "snapshot_id": source.snapshot_id,
        }
        self.payload = GenerateTestCasesInput(
            project_id=self.project.project_id,
            base_project_revision=source.project_revision,
            requirements=[req],
            template={"name": "QA", "format": "json", "fields": ["steps"]},
        )
        self.result = GenerateTestCasesResponse.model_validate(
            {
                "approved": True,
                "review": {"approved": True, "score": 100, "threshold": 90},
                "coverage_plan": plan,
                "test_cases": [
                    {
                        "id": "TC-1",
                        "title": "Save the order",
                        "steps": [{"step": 1, "action": "Submit the order", "expected": "A confirmed order is displayed"}],
                        "linked_requirement_ids": [req["id"]],
                        "scenario_refs": ["SCN-1"],
                        "artifact_set_id": "another-artifact-set",
                        "artifact_item_id": "another-item",
                    }
                ],
            }
        )
        self.generate = stack.enter_context(patch.object(routes, "generate_test_cases", return_value=self.result.model_dump()))
        account = BillingAccount(account_id="enterprise-account", scope_key="org:fixture", scope_type="organization", plan_tier="enterprise", balance_units=100)
        allocation = BillingAllocation(
            allocation_id=build_allocation_id(account.account_id, self.actor.sub), account_id=account.account_id, user_id=self.actor.sub, allocated_units=100
        )
        self.account_path = f"billing_accounts/{account.account_id}"
        self.allocation_path = f"billing_allocations/{allocation.allocation_id}"
        self.store[self.account_path] = account.model_copy(update={"balance_units": 80}).model_dump()
        self.store[self.allocation_path] = allocation.model_copy(update={"consumed_units": 9}).model_dump()
        self.billing = stack.enter_context(
            patch.object(
                routes,
                "enforce_billing_access",
                return_value=BillingAccessContext(
                    account=account, profile=BillingUserProfile(user_id=self.actor.sub), pricing_entry=None, allocation=allocation, shadow_mode=False
                ),
            )
        )

    async def _request(self, payload=None, *, request_id="publish-once", actor=None):
        request = Request({"type": "http"})
        request.state.request_id = request_id
        return await routes.generate_test_cases_endpoint(request, payload or self.payload, actor or self.actor)

    def _records(self, collection):
        return [value for key, value in self.store.items() if key.startswith(collection + "/")]

    async def test_commit_reply_loss_and_replay_preserve_one_publication_and_paid_debit(self):
        publish = routes._append_project_generation_snapshots

        def drop_reply(**kwargs):
            publish(**kwargs)
            raise HTTPException(503, "Synthetic commit response lost")

        with patch.object(routes, "_append_project_generation_snapshots", side_effect=drop_reply):
            generated = await self._request()
        self.assertEqual((await self._request()).model_dump(), generated.model_dump())
        self.generate.assert_called_once()
        self.billing.assert_called_once()
        final = get_project(self.project.project_id, actor=self.actor)
        self.assertEqual(final.current_revision, self.payload.base_project_revision + 1)
        snapshot = final.current_snapshots["test_cases"]
        case = generated.test_cases[0]
        self.assertNotEqual(case.artifact_set_id, "another-artifact-set")
        self.assertEqual(snapshot.payload["test_cases"][0]["artifact_version_id"], case.artifact_version_id)
        self.assertEqual(len(self._records("test_case_sets")), 3)
        self.assertEqual(self.store[self.account_path]["balance_units"], 73)
        self.assertEqual(self.store[self.allocation_path]["consumed_units"], 16)
        [usage] = self._records("usage_events")
        [ledger] = self._records("billing_wallet_ledger")
        [consumption] = self._records("billing_consumption")
        [workflow] = self._records("workflow_runs")
        self.assertEqual((usage["status"], usage["quantity"], workflow["status"]), ("completed", 1, "completed"))
        self.assertEqual(ledger["units_delta"], -7)
        self.assertEqual(consumption["source_event_id"], usage["event_id"])
        self.assertEqual(snapshot.source_event_id, usage["event_id"])
        self.assertTrue(consumption["applied"])

        before = deepcopy(self.store)
        with self.assertRaises(HTTPException) as changed:
            await self._request(self.payload.model_copy(update={"feedback": "Different requested behavior"}))
        self.assertEqual(changed.exception.status_code, 409)
        with self.assertRaises(HTTPException) as forbidden:
            await self._request(actor=AuthUser(sub="another-user", name="Another user"))
        self.assertEqual(forbidden.exception.status_code, 403)
        self.assertEqual(self.store, before)

        append_stage_snapshot(
            project_id=self.project.project_id,
            stage="test_cases",
            payload=snapshot.payload,
            operation="test_cases.review",
            actor=self.actor,
            request_id="later-review",
        )
        before_superseded_replay = deepcopy(self.store)
        with self.assertRaises(HTTPException) as superseded:
            await self._request()
        self.assertEqual(superseded.exception.status_code, 409)
        self.assertEqual(self.store, before_superseded_replay)

    async def test_failed_and_stale_publications_leave_only_failed_usage(self):
        def change_project(*args, **kwargs):
            update_project(project_id=self.project.project_id, actor=self.actor, request_id="change", name="Other revision")
            return self.result.model_dump()

        self.client.fail_on_create_segment = "/snapshots/"
        with self.assertRaises(HTTPException) as failed:
            await self._request()
        self.assertEqual(failed.exception.status_code, 503)
        self.client.fail_on_create_segment = None
        self.generate.side_effect = change_project
        with self.assertRaises(HTTPException) as stale:
            await self._request(request_id="stale-generation")
        self.assertEqual(stale.exception.status_code, 409)
        self.assertNotIn("test_cases", get_project(self.project.project_id, actor=self.actor).current_snapshots)
        self.assertEqual(self.store[self.account_path]["balance_units"], 80)
        self.assertEqual(self.store[self.allocation_path]["consumed_units"], 9)
        for collection in ("test_case_sets", "billing_consumption", "billing_wallet_ledger"):
            self.assertEqual(self._records(collection), [])
        self.assertEqual([(row["status"], row["quantity"]) for row in self._records("usage_events")], [("failed", 0), ("failed", 0)])

    async def test_unconfirmed_publication_does_not_log_a_failed_commit(self):
        publish = routes._append_project_generation_snapshots

        def drop_reply(**kwargs):
            publish(**kwargs)
            raise HTTPException(503, "Synthetic commit response lost")

        with (
            patch.object(routes, "_append_project_generation_snapshots", side_effect=drop_reply),
            patch.object(routes, "_test_case_publication_receipt", side_effect=[None, HTTPException(503, "Synthetic receipt read unavailable")]),
        ):
            with self.assertRaises(HTTPException) as unavailable:
                await self._request()
        self.assertEqual(unavailable.exception.detail["code"], "generation_publication_unconfirmed")
        saved = await self._request()
        self.assertEqual(len(saved.test_cases), 1)
        self.generate.assert_called_once()
        self.assertEqual([(row["status"], row["quantity"]) for row in self._records("usage_events")], [("completed", 1)])

    async def test_archived_project_is_rejected_before_generation(self):
        archived = update_project(project_id=self.project.project_id, actor=self.actor, request_id="archive", status_value="archived")
        with self.assertRaises(HTTPException) as stopped:
            await self._request(self.payload.model_copy(update={"base_project_revision": archived.current_revision}))
        self.assertEqual(stopped.exception.status_code, 409)
        self.generate.assert_not_called()
        self.billing.assert_not_called()
        self.assertEqual(self._records("usage_events"), [])

    async def _scoped_repair_fixture(self):
        cases = [self.result.test_cases[0].model_copy(update={"id": f"TC-{n}", "title": f"Saved case {n}"}) for n in (1, 2, 3)]
        self.generate.return_value = self.result.model_copy(update={"test_cases": cases}).model_dump()
        await self._request()
        project = get_project(self.project.project_id, actor=self.actor)
        snapshot = project.current_snapshots["test_cases"]
        saved_rows = self.store[f"{self.project_path}/snapshots/{snapshot.snapshot_id}"]["payload"]["test_cases"]
        selected = saved_rows[1]
        original_set = f"test_case_sets/{selected['artifact_set_id']}"
        legacy_set_id = "legacy-selected-set"
        legacy_set = f"test_case_sets/{legacy_set_id}"
        self.store[legacy_set] = {**self.store[original_set], "set_id": legacy_set_id, "item_count": 1}
        for suffix in (f"/items/{selected['artifact_item_id']}", f"/items/{selected['artifact_item_id']}/versions/{selected['artifact_version_id']}"):
            self.store[legacy_set + suffix] = {**self.store.pop(original_set + suffix), "set_id": legacy_set_id}
        selected["artifact_set_id"] = legacy_set_id
        saved_rows[2] = {key: value for key, value in saved_rows[2].items() if not key.startswith("artifact_")}
        saved_rows[2]["legacy_description"] = "Keep this exact historical row"
        repair = RefineTestCasesInput(
            project_id=self.project.project_id,
            base_project_revision=project.current_revision,
            selected_test_case_ids=["TC-2", "TC-3"],
            requirements=self.payload.requirements,
            test_cases=saved_rows,
            template=self.payload.template,
            feedback="Assert the exact saved receipt.",
        )
        candidate = self.result.model_copy(
            update={
                "test_cases": [
                    repair.test_cases[1].model_copy(update={"title": "Repaired case", "artifact_item_id": "foreign", "artifact_set_id": "foreign"}),
                    repair.test_cases[2].model_copy(update={"status": "Draft", "artifact_item_id": "forged-legacy"}),
                ]
            }
        )
        return repair, deepcopy(saved_rows), candidate

    async def _refine(self, payload, request_id="repair-once"):
        request = Request({"type": "http"})
        request.state.request_id = request_id
        return await routes.refine_test_cases_endpoint(request, payload, self.actor)

    async def test_selected_repair_commits_only_the_changed_case_and_replays(self):
        repair, saved_rows, candidate = await self._scoped_repair_fixture()
        prior_artifacts = {key: deepcopy(value) for key, value in self.store.items() if key.startswith("test_case_sets/")}
        with patch.object(routes, "refine_test_cases", return_value=candidate.model_dump()) as model:
            refined = await self._refine(repair)
            replay = await self._refine(repair)
        model.assert_called_once()
        self.assertEqual(refined.test_cases, replay.test_cases)
        self.assertEqual(refined.scoped_refinement, {"changed": 1, "added": 0, "preserved": 2, "changed_ids": ["TC-2"]})
        self.assertEqual(refined.substantive_assessment["status"], "unknown")
        self.assertFalse(refined.approved)
        current = get_project(self.project.project_id, actor=self.actor)
        self.assertEqual(current.current_revision, repair.base_project_revision + 1)
        rows = current.current_snapshots["test_cases"].payload["test_cases"]
        self.assertEqual([rows[0], rows[2]], [saved_rows[0], saved_rows[2]])
        self.assertEqual(rows[1]["artifact_set_id"], saved_rows[1]["artifact_set_id"])
        self.assertEqual(rows[1]["artifact_item_id"], saved_rows[1]["artifact_item_id"])
        self.assertEqual(rows[1]["artifact_version_number"], saved_rows[1]["artifact_version_number"] + 1)
        self.assertNotEqual(rows[1]["artifact_version_id"], saved_rows[1]["artifact_version_id"])
        parent = f"test_case_sets/{rows[1]['artifact_set_id']}"
        item = f"{parent}/items/{rows[1]['artifact_item_id']}"
        self.assertEqual({key for key, value in prior_artifacts.items() if self.store[key] != value}, {parent, item})
        self.assertEqual(
            {key for key in self.store if key.startswith("test_case_sets/")} - prior_artifacts.keys(), {f"{item}/versions/{rows[1]['artifact_version_id']}"}
        )
        self.assertEqual(self.store[item]["current_version_id"], rows[1]["artifact_version_id"])
        self.assertEqual([(r["status"], r["quantity"]) for r in self._records("usage_events") if r["billing_key"] == "testcases.refine"], [("completed", 1)])
        self.assertEqual(len([r for r in self._records("billing_consumption") if r["billing_key"] == "testcases.refine"]), 1)
        self.assertEqual(self.store[self.account_path]["balance_units"], 66)

    async def test_failed_and_stale_repairs_preserve_artifact_pointers_and_billing(self):
        repair, saved_rows, candidate = await self._scoped_repair_fixture()
        prefixes = ("test_case_sets/", "billing_consumption/", "billing_wallet_ledger/", self.account_path, self.allocation_path)
        initial = {key: deepcopy(value) for key, value in self.store.items() if key.startswith(prefixes)}
        noop = candidate.model_copy(update={"test_cases": repair.test_cases[1:]})
        with patch.object(routes, "refine_test_cases", return_value=noop.model_dump()), self.assertRaises(HTTPException) as unchanged:
            await self._refine(repair, "no-content-change")
        self.assertEqual(unchanged.exception.status_code, 422)
        self.client.fail_on_create_segment = "/snapshots/"
        with patch.object(routes, "refine_test_cases", return_value=candidate.model_dump()), self.assertRaises(HTTPException) as failed:
            await self._refine(repair)
        self.assertEqual(failed.exception.status_code, 503)
        self.client.fail_on_create_segment = None

        def change_project(*args, **kwargs):
            update_project(project_id=self.project.project_id, actor=self.actor, request_id="racing-update", name="New project revision")
            return candidate.model_dump()

        with patch.object(routes, "refine_test_cases", side_effect=change_project), self.assertRaises(HTTPException) as stale:
            await self._refine(repair, "stale-repair")
        self.assertEqual(stale.exception.status_code, 409)
        self.assertEqual({key: value for key, value in self.store.items() if key.startswith(prefixes)}, initial)
        current = get_project(self.project.project_id, actor=self.actor)
        self.assertEqual(current.current_snapshots["test_cases"].payload["test_cases"], saved_rows)
        usage = self._records("usage_events")
        self.assertEqual([(row["status"], row["quantity"]) for row in usage if row["billing_key"] == "testcases.refine"], [("failed", 0)] * 3)


if __name__ == "__main__":
    unittest.main()
