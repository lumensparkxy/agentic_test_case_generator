import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from os import environ
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from google.adk.models import Gemini, LlmRequest
from google.api_core.exceptions import FailedPrecondition, NotFound
from google.genai import types
from test_approval_continuation import _Client
from test_use_case_generation import generated
from test_use_case_review_service import _run_transaction

from app.agents import substantive_review
from app.agents.adk_runtime import bounded_adk_model
from app.config import DEFAULT_MODEL_NAME
from app.models import AuthUser, TestCaseTemplate
from app.routers import testcases as test_case_routes
from app.services import approval_continuation as runs, approval_scenario_worker as worker, use_case_generation_service as generation
from app.services import use_case_review_service as reviews
from app.services import workflow_project_service as projects
from app.services.guidance_service import submit_with_guidance


_EMULATOR = "127.0.0.1:44123"


class ScenarioWorkerPollingTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
        self.client = Mock()
        self.query = self.client.collection.return_value.where.return_value.order_by.return_value.limit
        self.scan = self.query.return_value.stream
        self.enterContext(patch.object(worker, "approval_scenario_client_allowed", return_value=True))
        self.enterContext(patch.object(worker, "_utcnow", return_value=self.now))

    def record(self, project_id, update_time):
        row = Mock(update_time=update_time)
        row.to_dict.return_value = {"project_id": project_id, "run_id": project_id}
        return row

    def test_skipped_batch_yields_to_a_different_ready_project(self):
        available = {f"blocked-{index}": self.now - timedelta(seconds=2) for index in range(12)}
        available["ready"] = self.now - timedelta(seconds=1)
        rows = {name: self.record(name, when) for name, when in available.items()}
        for name, row in rows.items():

            def update(fields, *, option, project_id=name):
                available[project_id] = fields["available_at"]

            row.reference.update.side_effect = update

        def scan():
            due = sorted((name for name in available if available[name] <= self.now), key=available.__getitem__)
            return (rows[name] for name in due[: self.query.call_args.args[0]])

        self.scan.side_effect = scan
        with patch.object(worker, "_handle_due", side_effect=lambda _client, project, _run: project == "ready") as handle:
            self.assertEqual(worker.run_due_scenarios(self.client), 0)
            self.assertEqual(worker.run_due_scenarios(self.client), 1)
        self.assertEqual(handle.call_count, 13)
        handle.assert_called_with(self.client, "ready", "ready")
        self.query.assert_called_with(12)
        self.assertTrue(all(when > self.now for name, when in available.items() if name != "ready"))
        rows["ready"].reference.update.assert_not_called()
        for name, row in rows.items():
            if name != "ready":
                self.client.write_option.assert_any_call(last_update_time=row.update_time)
                row.reference.update.assert_called_once_with({"available_at": available[name]}, option=self.client.write_option.return_value)

    def test_concurrent_update_or_removal_of_skipped_row_does_not_stop_scan(self):
        rows = [self.record(str(index), self.now) for index in range(3)]
        rows[0].reference.update.side_effect = FailedPrecondition("schedule changed")
        rows[1].reference.update.side_effect = NotFound("row removed")
        self.scan.return_value = iter(rows)
        with patch.object(worker, "_handle_due", side_effect=[False, False, True]) as handle:
            self.assertEqual(worker.run_due_scenarios(self.client), 1)
        self.assertEqual(handle.call_count, 3)
        rows[0].reference.update.assert_called_once()
        rows[1].reference.update.assert_called_once()
        rows[2].reference.update.assert_not_called()


class ScenarioWorkerTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
        self.enterContext(
            patch.dict(
                environ,
                {
                    "TCG_APPROVAL_SCENARIOS_ENABLED": "true",
                    "FIREBASE_PROJECT_ID": worker.DEMO_PROJECT_ID,
                    "FIRESTORE_EMULATOR_HOST": _EMULATOR,
                    "ADK_SKILLS_ENABLED": "false",
                    "ADK_MEMORY_ENABLED": "false",
                    "GOOGLE_GENAI_USE_VERTEXAI": "false",
                    "MODEL_NAME": DEFAULT_MODEL_NAME,
                },
            )
        )
        self.client = _Client({})
        self.client.project = worker.DEMO_PROJECT_ID
        self.client._emulator_host = _EMULATOR
        self.enterContext(patch.object(runs, "transactional", side_effect=_run_transaction))
        self.enterContext(patch.object(runs, "_utcnow", side_effect=lambda: self.now))
        self.enterContext(patch.object(worker, "_utcnow", side_effect=lambda: self.now))
        self.enterContext(patch.object(projects, "get_required_firestore_collection", return_value=self.client.collection("qa_projects")))
        self.enterContext(patch.object(projects, "get_required_firestore_client", return_value=self.client))
        self.enterContext(patch.object(projects, "transactional", side_effect=_run_transaction))
        self.enterContext(patch.object(generation, "get_required_firestore_client", return_value=self.client))
        self.billing = self.enterContext(patch.object(generation, "enforce_billing_access"))
        self.enterContext(
            patch("app.services.audit_repository.get_optional_firestore_collection", side_effect=lambda name, **kwargs: self.client.collection(name))
        )
        self.actor = AuthUser(sub="emulator-user", name="Emulator user")
        self.project_id = projects.create_project(name="Demo", description=None, actor=self.actor, request_id="create").project_id
        self.project_doc = self.client.collection("qa_projects").document(self.project_id)
        source = projects.append_stage_snapshot(
            project_id=self.project_id,
            stage="requirements",
            payload={"requirements": [{"id": "REQ-1", "text": "Reject bookings for occupied slots.", "review_status": "Approved"}]},
            operation="requirements.review",
            actor=self.actor,
            request_id="approve-scope",
            approved=True,
        )
        self.source = source
        transaction = self.client.transaction()
        self.run_id = runs.enqueue_approval(
            transaction,
            self.project_doc,
            client=self.client,
            stage="prepare_scenarios",
            actor_id=self.actor.sub,
            approval_id=source.snapshot_id,
            request_id="approve-scope",
            resulting_revision=source.project_revision,
            source_snapshot_ids={"requirements": source.snapshot_id, "context": None},
            requirement_ids=["REQ-1"],
            options=worker.scenario_continuation_options(),
        )
        transaction.commit()
        self.requests = []
        self.after_request = None
        self.expected_reservation = 7

        async def mock_request(model, request, stream=False):
            del stream
            self.requests.append(request.config)
            self.assertFalse(model.client_kwargs["vertexai"])
            self.assertEqual(model.client_kwargs["http_options"].async_client_args, {"follow_redirects": False, "allow_redirects": False})
            self.assertEqual(self.saved_run()["continuation"]["provider_requests_reserved"], self.expected_reservation)
            if self.after_request is not None:
                self.after_request()
            yield object()

        self.enterContext(patch.object(Gemini, "generate_content_async", mock_request))
        self.generate = self.enterContext(patch.object(generation, "generate_use_cases", side_effect=self.generate_synthetic))

    def saved_run(self):
        return self.project_doc.collection("orchestrator_runs").document(self.run_id).get().to_dict()

    def handle(self):
        return worker._handle_due(self.client, self.project_id, self.run_id)

    def request_model(self):
        async def run():
            model = bounded_adk_model(DEFAULT_MODEL_NAME)
            request = LlmRequest(config=types.GenerateContentConfig(http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=5))))
            return [response async for response in model.generate_content_async(request)]

        return asyncio.run(run())

    def generate_synthetic(self, payload, **_):
        self.request_model()
        return generated(payload.requirements)

    def assert_no_plan(self):
        project = projects.get_project(self.project_id, actor=self.actor)
        self.assertNotIn("use_cases", project.current_snapshots)

    def approve_saved_plan_for_tests(self, template=None):
        self.assertTrue(self.handle())
        parent_run_id = self.run_id
        project = projects.get_project(self.project_id, actor=self.actor)
        plan = project.current_snapshots["use_cases"]
        self.assertTrue(plan.metadata["source_context_omitted"])
        with (
            patch.object(reviews, "get_required_firestore_client", return_value=self.client),
            patch.object(reviews, "transactional", side_effect=_run_transaction),
        ):
            result = reviews.review_use_case_snapshot(
                project_id=self.project_id,
                snapshot_id=plan.snapshot_id,
                base_project_revision=project.current_revision,
                decision="approve",
                comment=None,
                actor=self.actor,
                request_id="approve-scenarios",
                continuation=worker.test_case_continuation_options(template),
                require_prepared_journey=True,
            )
        self.run_id = runs.continuation_run_id(self.project_id, self.actor.sub, "generate_tests", result.review.review_id)
        parent = self.project_doc.collection("orchestrator_runs").document(parent_run_id).get().to_dict()
        self.assertEqual(parent["continuation"]["journey_budget"]["child_run_id"], self.run_id)
        self.assertEqual(parent["continuation"]["journey_budget"]["scenario_requests_reserved"], 7)
        self.expected_reservation = 24
        self.requests.clear()
        return plan

    def test_gate_requires_approved_emulator_transport_and_provider_configuration(self):
        self.assertTrue(worker.approval_scenario_client_allowed(self.client))
        for values in (
            {"TCG_APPROVAL_SCENARIOS_ENABLED": "false"},
            {"FIREBASE_PROJECT_ID": "production-project"},
            {"FIRESTORE_EMULATOR_HOST": "127.0.0.1.attacker.test:44123"},
            {"FIRESTORE_EMULATOR_HOST": "127.0.0.1:0"},
            {"GOOGLE_GENAI_USE_VERTEXAI": "true"},
            {"ADK_SKILLS_ENABLED": "true", "ADK_GUIDANCE_STAGES": "use_cases"},
        ):
            with self.subTest(values=values), patch.dict(environ, values):
                self.assertFalse(worker.approval_scenarios_enabled())
        self.client._emulator_host = None
        self.assertFalse(worker.approval_scenario_client_allowed(self.client))
        with self.assertRaises(RuntimeError):
            worker.run_due_scenarios(self.client)

    def test_exact_plan_and_run_advance_once_with_existing_publication_receipt(self):
        publish = generation.append_stage_snapshot

        def lose_reply(**kwargs):
            publish(**kwargs)
            raise RuntimeError("synthetic commit reply loss")

        with patch.object(generation, "append_stage_snapshot", side_effect=lose_reply):
            self.assertTrue(self.handle())
        self.assertFalse(self.handle())
        self.generate.assert_called_once()
        self.billing.assert_called_once()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].http_options.retry_options.attempts, 1)
        self.assertTrue(self.requests[0].automatic_function_calling.disable)
        self.assertLessEqual(self.requests[0].http_options.timeout, 25000)
        project = projects.get_project(self.project_id, actor=self.actor)
        plan = project.current_snapshots["use_cases"]
        self.assertEqual(plan.source_snapshot_id, self.source.snapshot_id)
        self.assertFalse(project.stage_state["use_cases"].approved)
        run = self.saved_run()
        self.assertEqual(run["continuation"]["state"], "awaiting_user")
        self.assertEqual(run["produced_snapshot_ids"], {"use_cases": plan.snapshot_id})
        [usage] = [row for path, row in self.client.store.items() if path.startswith("usage_events/")]
        self.assertEqual(usage["event_id"], plan.source_event_id)
        self.assertFalse(any(path.startswith("billing_consumption/") for path in self.client.store))

    def test_worker_restart_after_unstarted_lease_expiry(self):
        claim = runs.claim_stage(self.client, project_id=self.project_id, run_id=self.run_id, lease_seconds=1)
        self.assertIsNotNone(claim)
        self.assertFalse(self.handle())
        self.now += timedelta(seconds=2)
        self.assertTrue(self.handle())
        self.assertEqual(self.saved_run()["continuation"]["state"], "awaiting_user")
        self.generate.assert_called_once()

    def test_worker_restart_after_possible_dispatch_does_not_retry(self):
        claim = runs.claim_stage(self.client, project_id=self.project_id, run_id=self.run_id, lease_seconds=1)
        runs.mark_dispatched(self.client, claim, provider_requests=7)
        self.now += timedelta(seconds=2)
        self.assertFalse(self.handle())
        self.generate.assert_not_called()
        self.assertEqual(self.saved_run()["continuation"]["state"], "interrupted")
        self.assert_no_plan()

    def test_settings_mismatch_stops_before_model_invocation(self):
        with patch.dict(environ, {"MODEL_NAME": "gemini-different-model"}):
            self.assertTrue(self.handle())
        self.generate.assert_not_called()
        intent = self.saved_run()["continuation"]
        self.assertEqual((intent["state"], intent["reason"]), ("failed", "settings_changed"))
        self.assert_no_plan()

    def test_behaviour_change_preserves_saved_approval_and_stops_before_model_dispatch(self):
        before = projects.get_project(self.project_id, actor=self.actor)
        requirements = before.current_snapshots["requirements"]
        self.assertTrue(before.stage_state["requirements"].approved)
        with patch.object(worker, "behavioural_version", return_value="new-test-design-evidence"):
            self.assertTrue(self.handle())
        self.generate.assert_not_called()
        intent = self.saved_run()["continuation"]
        self.assertEqual((intent["state"], intent["reason"]), ("failed", "settings_changed"))
        self.assertEqual(intent["provider_requests_reserved"], 0)
        saved = projects.get_project(self.project_id, actor=self.actor)
        self.assertEqual(saved.current_revision, before.current_revision)
        self.assertEqual(saved.current_snapshots["requirements"].snapshot_id, requirements.snapshot_id)
        self.assertTrue(saved.stage_state["requirements"].approved)

    def test_budget_is_shared_across_shard_contexts_and_does_not_exceed_seven(self):
        def generate_in_shards(payload, **_):
            with ThreadPoolExecutor(max_workers=3) as executor:
                tasks = [submit_with_guidance(executor, self.request_model) for _ in range(8)]
                results = []
                for task in tasks:
                    try:
                        task.result()
                        results.append(True)
                    except runs.ContinuationConflict:
                        results.append(False)
            self.assertEqual(results.count(True), 7)
            return generated(payload.requirements)

        self.generate.side_effect = generate_in_shards
        self.assertTrue(self.handle())
        self.assertEqual(len(self.requests), 7)
        self.assertEqual(self.saved_run()["continuation"]["state"], "awaiting_user")

    def test_cancellation_after_first_call_fences_next_call_and_publication(self):
        self.after_request = lambda: runs.cancel_stage(self.client, project_id=self.project_id, run_id=self.run_id, actor_id=self.actor.sub)

        def plan(payload, **_):
            self.request_model()
            with self.assertRaises(runs.ContinuationConflict):
                self.request_model()
            return generated(payload.requirements)

        self.generate.side_effect = plan
        self.assertTrue(self.handle())
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.saved_run()["continuation"]["state"], "cancelled")
        self.assert_no_plan()

    def test_revision_change_after_first_call_fences_next_call_and_publication(self):
        self.after_request = lambda: projects.update_project(project_id=self.project_id, actor=self.actor, request_id="change", name="New project input")

        def plan(payload, **_):
            self.request_model()
            with self.assertRaises(runs.ContinuationConflict):
                self.request_model()
            return generated(payload.requirements)

        self.generate.side_effect = plan
        self.assertTrue(self.handle())
        self.assertEqual(len(self.requests), 1)
        intent = self.saved_run()["continuation"]
        self.assertEqual((intent["state"], intent["reason"]), ("interrupted", "sources_changed_provider_outcome_unconfirmed"))
        self.assert_no_plan()

    def test_test_case_worker_declines_held_guidance_without_claiming(self):
        self.approve_saved_plan_for_tests()
        with (
            patch.dict(environ, {"ADK_SKILLS_ENABLED": "true", "ADK_GUIDANCE_STAGES": "test_cases"}),
            patch.object(test_case_routes, "generate_bound_test_cases") as generate_tests,
        ):
            self.assertFalse(self.handle())
        generate_tests.assert_not_called()
        self.assertEqual(self.saved_run()["continuation"]["state"], "queued")

    def test_omitted_template_uses_legacy_worker_default(self):
        self.approve_saved_plan_for_tests()
        self.assertNotIn("template", self.saved_run()["continuation"]["options"])
        with patch.object(test_case_routes, "generate_bound_test_cases") as generate_tests:
            self.assertTrue(self.handle())
        generate_tests.assert_called_once()
        self.assertEqual(generate_tests.call_args.args[0].template, TestCaseTemplate(name="Default", format="json", fields=[]))

    def test_changed_saved_template_fails_before_test_generation(self):
        template = TestCaseTemplate(name="Expense", format="table", fields=["id", "expected_result"])
        self.approve_saved_plan_for_tests(template)
        self.client.store[f"{self.project_doc.path}/orchestrator_runs/{self.run_id}"]["continuation"]["options"]["template"]["name"] = "Different"
        with patch.object(test_case_routes, "generate_bound_test_cases") as generate_tests:
            self.assertTrue(self.handle())
        generate_tests.assert_not_called()
        intent = self.saved_run()["continuation"]
        self.assertEqual((intent["state"], intent["reason"], intent["provider_requests_reserved"]), ("failed", "settings_changed", 0))

    def test_test_case_generation_and_direct_critic_share_one_durable_request_budget(self):
        template = TestCaseTemplate(name="default", format="table", fields=["id", "title", "steps", "expected_result"])
        plan = self.approve_saved_plan_for_tests(template)
        self.assertEqual(self.saved_run()["continuation"]["options"]["template"], template.model_dump(mode="json"))
        critic_settings = SimpleNamespace(gemini_api_key="offline-fixture", model_name=DEFAULT_MODEL_NAME)

        def request_critic():
            return substantive_review._call_critic(critic_settings, "synthetic reviewer prompt")

        async def send_critic(**kwargs):
            self.assertEqual(self.saved_run()["continuation"]["provider_requests_reserved"], 24)
            self.assertEqual(kwargs["config"].http_options.retry_options.attempts, 1)
            return SimpleNamespace(candidates=[], text="{}")

        def produce_tests(payload, **sources):
            self.assertIsNone(payload.context)
            self.assertEqual([row.id for row in payload.requirements], ["REQ-1"])
            self.assertEqual(sources["source_coverage_plan"], plan.payload["coverage_plan"])
            with ThreadPoolExecutor(max_workers=3) as executor:
                requests = [submit_with_guidance(executor, self.request_model if index % 2 else request_critic) for index in range(24)]
                for result in requests:
                    result.result()
            case = {
                "id": "TC-1",
                "title": "Reject an occupied-slot booking",
                "steps": [{"step": 1, "action": "Submit a booking for an occupied slot", "expected": "The booking is rejected"}],
                "linked_requirement_ids": ["REQ-1"],
                "scenario_refs": ["REQ-1-SCN-01"],
            }
            assessment = substantive_review.assess_delivered_suite([case], payload.requirements, None, settings=critic_settings)
            self.assertEqual(assessment["diagnostics"]["failure_reason"], "provider_unavailable")
            return substantive_review.apply_assessment(
                {
                    "test_cases": [case],
                    "coverage_plan": sources["source_coverage_plan"],
                    "requirement_analysis": sources["source_requirement_analysis"],
                    "approved": True,
                    "review": {"approved": True, "score": 95, "threshold": 90, "blocking_issues": []},
                    "workflow_diagnostics": {"status": "completed"},
                    "generation_evidence": {},
                    "coverage_metrics": {},
                },
                assessment,
            )

        with (
            patch.object(substantive_review.genai, "Client") as critic_client,
            patch.object(test_case_routes, "generate_test_cases", side_effect=produce_tests) as model,
            patch.object(test_case_routes, "get_required_firestore_client", return_value=self.client),
            patch("app.services.versioning_service.get_optional_firestore_collection", side_effect=lambda name, **_: self.client.collection(name)),
            patch.object(test_case_routes, "enforce_billing_access"),
            patch.object(test_case_routes, "record_billing_consumption") as bill,
        ):
            send = AsyncMock(side_effect=send_critic)
            critic_client.return_value.__enter__.return_value.aio.__aenter__.return_value.models.generate_content = send
            self.assertTrue(self.handle())
            self.assertFalse(self.handle())
        self.assertEqual((len(self.requests), send.await_count), (12, 12))
        self.assertFalse(critic_client.call_args.kwargs["vertexai"])
        self.assertFalse(critic_client.call_args.kwargs["http_options"].async_client_args["allow_redirects"])
        model.assert_called_once()
        self.assertEqual(model.call_args.args[0].template, template)
        bill.assert_called_once()
        project = projects.get_project(self.project_id, actor=self.actor)
        suite = project.current_snapshots["test_cases"]
        self.assertEqual(suite.source_snapshot_id, plan.snapshot_id)
        self.assertTrue(suite.metadata["review_retry_available"])
        self.assertEqual(self.saved_run()["produced_snapshot_ids"], {"test_cases": suite.snapshot_id})
