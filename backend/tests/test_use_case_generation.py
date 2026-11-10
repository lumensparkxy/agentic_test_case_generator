from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
for directory in (BACKEND, BACKEND / "tests"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_use_case_review_service import FakeFirestoreClient, FakeTransaction, _run_transaction
from test_workflow_project_service import FakeCollection
from app.agents.adk_runtime import _model_reservation
from app.agents.analysis_agent import normalize_requirement_analysis
from app.agents.test_case_coverage import _normalize_coverage_plan
from app.main import app, get_current_user
from app.models import AuthUser, Requirement
from app.services import audit_service, project_draft_admission as admission
from app.services import use_case_generation_service as service
from app.services.use_case_review_service import review_use_case_snapshot
from app.services.workflow_project_service import (
    ProjectConflictError,
    ProjectPermissionError,
    append_stage_snapshot,
    create_project,
    get_project,
    update_project,
)


def generated(requirements):
    return {
        "requirement_analysis": [{"requirement_id": r.id, "requirement_text": r.text} for r in requirements],
        "coverage_plan": [
            {
                "requirement_id": r.id,
                "requirement_text": r.text,
                "scenarios": [{"id": f"{r.id}-SCN-01", "requirement_id": r.id, "title": f"Check {r.id}", "objective": f"Verify {r.text}"}],
            }
            for r in requirements
        ],
        "approved": True,
        "review": {"approved": True},
        "workflow_diagnostics": {"status": "completed", "used_fallback": False},
    }


class UseCaseGenerationTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.client = FakeFirestoreClient(self.store)
        self.actor = AuthUser(sub="user-1", email="user@example.com", name="User")
        patches = {
            "app.services.workflow_project_service.get_required_firestore_collection": {"return_value": FakeCollection("qa_projects", self.store)},
            "app.services.workflow_project_service.get_required_firestore_client": {"return_value": self.client},
            "app.services.workflow_project_service.transactional": {"side_effect": _run_transaction},
            "app.services.use_case_generation_service.get_required_firestore_client": {"return_value": self.client},
            "app.services.use_case_generation_service.enforce_billing_access": {},
            "app.services.use_case_generation_service.start_workflow_run": {"return_value": "run-generation"},
            "app.services.use_case_generation_service.complete_workflow_run": {},
            "app.services.use_case_generation_service.record_usage_event": {"return_value": "usage-generation"},
        }
        self.mocks = {}
        for target, kwargs in patches.items():
            p = patch(target, **kwargs)
            self.mocks[target.rsplit(".", 1)[-1]] = p.start()
            self.addCleanup(p.stop)
        p = patch.dict("os.environ", {"ADK_SKILLS_ENABLED": "false", "ADK_MEMORY_ENABLED": "false"})
        p.start()
        self.addCleanup(p.stop)
        project = create_project(name="Generation", description=None, actor=self.actor, request_id="create")
        self.project_id = project.project_id
        requirements = [{"id": f"REQ-{n:03d}", "text": f"Requirement {n}", "review_status": "Approved"} for n in range(1, 11)]
        self.save("requirements", {"requirements": requirements}, approved=True)
        self.save("context", {"notes": "Use the saved context", "grounded_context": {"summary": "Saved grounding"}}, approved=True)
        old = generated([Requirement.model_validate(r) for r in requirements[:8]])
        self.save("use_cases", old, approved=True)
        self.save("test_cases", {"test_cases": [{"id": "TC-existing", "title": "Keep me"}]}, approved=True)
        self.save("execution", {"runs": ["run-existing"]}, approved=True)
        self.save("reports", {"report": "Keep report"}, approved=True)
        self.project = get_project(self.project_id, actor=self.actor)
        self.old_snapshot = self.project.current_snapshots["use_cases"]
        self.before = deepcopy(self.store)
        p = patch.object(service, "generate_use_cases", side_effect=lambda payload, **kwargs: generated(payload.requirements))
        self.model = p.start()
        self.addCleanup(p.stop)

    def save(self, stage, payload, **kwargs):
        return append_stage_snapshot(
            project_id=self.project_id, stage=stage, payload=payload, actor=self.actor, request_id=f"seed-{stage}", operation="seed", **kwargs
        )

    def run_generation(self, *, base_project_revision=None, **kwargs):
        return service.generate_project_use_cases(
            project_id=self.project_id,
            actor=self.actor,
            request_id="generate-1",
            base_project_revision=self.project.current_revision if base_project_revision is None else base_project_revision,
            **kwargs,
        )

    def durable_audit(self):
        stack = self.enterContext(ExitStack())
        stack.enter_context(
            patch("app.services.audit_repository.get_optional_firestore_collection", side_effect=lambda name, **kwargs: self.client.collection(name))
        )
        for name in ("start_workflow_run", "complete_workflow_run", "record_usage_event"):
            stack.enter_context(patch.object(service, name, wraps=getattr(audit_service, name)))

    def test_demo_gate_defers_new_scenario_generation_and_preserves_exact_replay(self):
        with patch.object(service, "draft_admission_client", return_value=self.client), self.assertRaises(HTTPException) as deferred:
            self.run_generation()
        self.assertEqual(deferred.exception.status_code, 409)
        self.assertEqual(deferred.exception.detail["code"], "project_draft_refresh_deferred")
        self.assertEqual(self.store, self.before)
        self.model.assert_not_called()
        self.mocks["enforce_billing_access"].assert_not_called()

        saved = self.run_generation()
        before_replay = deepcopy(self.store)
        with patch.object(service, "draft_admission_client", return_value=self.client):
            self.assertEqual(self.run_generation(), saved)
        self.assertEqual(self.store, before_replay)
        self.model.assert_called_once()
        self.mocks["enforce_billing_access"].assert_called_once()

    def test_generation_commits_usage_and_recovers_exact_result_after_commit_reply_loss(self):
        self.durable_audit()
        publish = service.append_stage_snapshot

        def drop_reply(**kwargs):
            publish(**kwargs)
            raise RuntimeError("Synthetic commit reply lost")

        with patch.object(service, "append_stage_snapshot", side_effect=drop_reply):
            saved = self.run_generation()
        replay = self.run_generation()
        self.assertEqual(replay, saved)
        self.model.assert_called_once()
        self.mocks["enforce_billing_access"].assert_called_once()
        snapshot = saved.current_snapshots["use_cases"]
        self.assertEqual(saved.current_revision, self.project.current_revision + 1)
        self.assertFalse(snapshot.approved)
        [event] = [value for path, value in self.store.items() if path.startswith("usage_events/")]
        [workflow] = [value for path, value in self.store.items() if path.startswith("workflow_runs/")]
        self.assertEqual((event["status"], event["quantity"], workflow["status"]), ("completed", 10, "completed"))
        self.assertEqual(event["event_id"], snapshot.source_event_id)
        self.assertEqual({event["workflow_run_id"], workflow["run_id"]}, {snapshot.workflow_run_id})
        self.assertEqual(workflow["result"]["snapshot_id"], snapshot.snapshot_id)
        self.assertFalse(any(path.startswith("billing_consumption/") for path in self.store))

        before = deepcopy(self.store)
        for kwargs in ({"memory_bypass": True}, {"refresh_requirement_ids": []}, {"base_project_revision": saved.current_revision}):
            with self.subTest(kwargs=kwargs), self.assertRaises(HTTPException) as changed:
                self.run_generation(**kwargs)
            self.assertEqual(changed.exception.status_code, 409)
        self.assertEqual(self.store, before)

        renamed = update_project(project_id=self.project_id, actor=self.actor, request_id="rename", name="New name")
        self.assertEqual(self.run_generation(), renamed)
        self.save("context", {"notes": "Changed context"}, approved=True)
        with self.assertRaises(ProjectConflictError):
            self.run_generation()
        self.model.assert_called_once()

    def test_generation_preserves_exact_approved_text_without_accepting_a_rewrite(self):
        requirement = Requirement(id="AC-1", text='  Show the notice "  α  " after saving.\t\n', review_status="Approved")
        scope = self.save("requirements", {"requirements": [requirement.model_dump(mode="json")]}, approved=True)
        self.project = get_project(self.project_id, actor=self.actor)
        previous_plan = self.project.current_snapshots["use_cases"]
        echoed = requirement.text.replace("  α  ", "α")

        def normalized_output(payload, **_kwargs):
            output = generated(payload.requirements)
            output["requirement_analysis"][0]["requirement_text"] = echoed
            output["coverage_plan"][0]["requirement_text"] = echoed.strip()
            output["requirement_analysis"] = normalize_requirement_analysis(output["requirement_analysis"], payload.requirements)
            output["coverage_plan"] = _normalize_coverage_plan(output["coverage_plan"], payload.requirements)
            return output

        self.model.side_effect = normalized_output
        with self.assertRaises(HTTPException) as changed:
            self.run_generation()
        self.assertEqual(changed.exception.status_code, 502)
        self.assertIn("different requirements baseline", changed.exception.detail)
        rejected = get_project(self.project_id, actor=self.actor)
        self.assertEqual(rejected.current_revision, self.project.current_revision)
        self.assertEqual(rejected.current_snapshots["use_cases"], previous_plan)

        echoed = requirement.text
        saved = self.run_generation()
        plan = saved.current_snapshots["use_cases"]
        self.assertEqual(plan.source_snapshot_id, scope.snapshot_id)
        self.assertFalse(plan.approved)
        for field in ("requirement_analysis", "coverage_plan"):
            self.assertEqual(plan.payload[field][0]["requirement_text"], requirement.text)

    def test_unconfirmed_publication_keeps_committed_workflow_status_and_recovers_on_retry(self):
        self.durable_audit()
        publish = service.append_stage_snapshot

        def drop_reply(**kwargs):
            publish(**kwargs)
            raise RuntimeError("Synthetic commit reply lost")

        with (
            patch.object(service, "append_stage_snapshot", side_effect=drop_reply),
            patch.object(service, "find_idempotent_stage_snapshot", side_effect=[None, RuntimeError("Synthetic receipt read failure")]),
            self.assertRaises(HTTPException) as uncertain,
        ):
            self.run_generation()
        self.assertEqual(uncertain.exception.detail["code"], "use_case_publication_unconfirmed")
        saved = self.run_generation()
        self.model.assert_called_once()
        workflow = self.store[f"workflow_runs/{saved.current_snapshots['use_cases'].workflow_run_id}"]
        self.assertEqual(workflow["status"], "completed")
        self.assertEqual(len([key for key in self.store if key.startswith("usage_events/")]), 1)

    def test_retry_recovers_a_commit_between_receipt_lookup_and_revision_check(self):
        self.durable_audit()
        committed = None

        def read_after_commit(project_id, *, actor):
            nonlocal committed
            if committed is None:
                with patch.object(service, "get_project", get_project):
                    committed = self.run_generation()
            return get_project(project_id, actor=actor)

        with patch.object(service, "get_project", side_effect=read_after_commit):
            replay = self.run_generation()
        self.assertEqual(replay, committed)
        self.model.assert_called_once()
        self.assertEqual(len([key for key in self.store if key.startswith("usage_events/")]), 1)

    def test_superseded_commit_reply_preserves_completed_workflow(self):
        self.durable_audit()
        publish = service.append_stage_snapshot

        def supersede_then_drop_reply(**kwargs):
            publish(**kwargs)
            self.save("context", {"notes": "Superseding context"}, approved=True)
            raise RuntimeError("Synthetic commit reply lost")

        with patch.object(service, "append_stage_snapshot", side_effect=supersede_then_drop_reply), self.assertRaises(ProjectConflictError):
            self.run_generation()
        current = get_project(self.project_id, actor=self.actor)
        self.assertTrue(current.stage_state["use_cases"].stale)
        [workflow] = [value for key, value in self.store.items() if key.startswith("workflow_runs/")]
        self.assertEqual(workflow["status"], "completed")
        self.assertEqual(len([key for key in self.store if key.startswith("usage_events/")]), 1)
        with self.assertRaises(ProjectConflictError):
            self.run_generation()
        self.model.assert_called_once()

    def test_usage_write_failure_rolls_back_use_cases_and_completed_workflow(self):
        self.durable_audit()
        original_set = FakeTransaction.set
        staged = []

        def reject_usage(transaction, document, payload, merge=False):
            if document.path.startswith("usage_events/"):
                staged.extend(value["status"] for _, ref, value in transaction.pending if ref.path.startswith("workflow_runs/"))
                raise RuntimeError("Synthetic usage write failure")
            return original_set(transaction, document, payload, merge=merge)

        with patch.object(FakeTransaction, "set", reject_usage), self.assertRaisesRegex(RuntimeError, "usage write failure"):
            self.run_generation()
        self.assertEqual(staged, ["completed"])
        self.assertEqual(get_project(self.project_id, actor=self.actor), self.project)
        self.assertFalse(any(key.startswith("usage_events/") for key in self.store))
        [workflow] = [value for path, value in self.store.items() if path.startswith("workflow_runs/")]
        self.assertEqual(workflow["status"], "failed")

    def prepare_scope(self, *, removal_only=False):
        project_path = f"qa_projects/{self.project_id}"
        use_cases_path = f"{project_path}/snapshots/{self.old_snapshot.snapshot_id}"
        source = self.project.current_snapshots["requirements"]
        self.store[use_cases_path]["source_snapshot_id"] = source.snapshot_id
        self.store[use_cases_path]["payload"]["review"]["approved"] = False
        self.store[use_cases_path]["payload"]["coverage_plan"][1]["scenarios"][0]["id"] = "legacy-kept-scenario"
        self.store[project_path]["stage_state"]["use_cases"].setdefault("metadata", {})["latest_human_review"] = {
            "snapshot_id": self.old_snapshot.snapshot_id,
            "decision": "approve",
        }
        rows = deepcopy(source.payload["requirements"])
        if removal_only:
            rows = rows[:7]
        else:
            rows[0].update(text="Changed requirement 1", content_version=2)
            rows[7]["clarification_status"] = "excluded"
        self.save("requirements", {"requirements": rows}, approved=True)
        self.save("context", {"notes": "Current reviewed context"}, approved=True)
        self.project = get_project(self.project_id, actor=self.actor)
        self.old_snapshot = self.project.current_snapshots["use_cases"]

        def combine(payload, retained_groups, **kwargs):
            result = generated(payload.requirements)
            for field, attribute in (("requirement_analysis", "analysis"), ("coverage_plan", "plan")):
                result[field] = [
                    getattr(retained_groups[row["requirement_id"]], attribute) if row["requirement_id"] in retained_groups else row for row in result[field]
                ]
            count = sum(len(group["scenarios"]) for group in result["coverage_plan"])
            result["review"]["semantic_assessment"] = {
                "rubric_version": "test",
                "status": "passed",
                "assessed_count": count,
                "scenario_count": count,
            }
            return result

        self.model.side_effect = combine
        self.before = deepcopy(self.store)

    def prepare_demo_refresh(self, *, removal_only=False):
        stack = self.enterContext(ExitStack())
        demo_host = "127.0.0.1:44124"
        self.client.project = admission.DEMO_PROJECT_ID
        self.client._emulator_host = demo_host
        stack.enter_context(
            patch.dict(
                "os.environ",
                {
                    "TCG_APPROVAL_SCENARIOS_ENABLED": "true",
                    "FIREBASE_PROJECT_ID": admission.DEMO_PROJECT_ID,
                    "FIRESTORE_EMULATOR_HOST": demo_host,
                    "GOOGLE_GENAI_USE_VERTEXAI": "false",
                    "MODEL_NAME": "gemini-local-fixture",
                },
            )
        )
        stack.enter_context(patch.object(admission, "get_required_firestore_client", return_value=self.client))
        stack.enter_context(patch.object(admission, "transactional", side_effect=_run_transaction))
        stack.enter_context(patch("app.services.use_case_review_service.get_required_firestore_client", return_value=self.client))
        stack.enter_context(patch("app.services.use_case_review_service.transactional", side_effect=_run_transaction))
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.addCleanup(app.dependency_overrides.clear)

        def save(stage, payload, operation, request_id, **kwargs):
            return append_stage_snapshot(
                project_id=self.project_id, stage=stage, payload=payload, actor=self.actor, operation=operation, request_id=request_id, **kwargs
            )

        rows = deepcopy(self.project.current_snapshots["requirements"].payload["requirements"][:9])
        rows[0]["text"] = "Maximum booking attendance is 8."
        rows[1]["text"] = "A booking accepts an attendee count of 8."
        earlier_requirements = save("requirements", {"requirements": rows}, "requirements.review", "old-scope", approved=True)
        earlier_context = save("context", {"notes": "Booking form and confirmation."}, "requirements.enrich", "old-context", approved=True)
        old_plan = save(
            "use_cases",
            generated([Requirement.model_validate(row) for row in rows]),
            "use_cases.generate",
            "old-plan",
            source_snapshot_id=earlier_requirements.snapshot_id,
            metadata={"source_requirements_snapshot_id": earlier_requirements.snapshot_id, "source_context_snapshot_id": earlier_context.snapshot_id},
        )
        project = get_project(self.project_id, actor=self.actor)
        review = review_use_case_snapshot(
            project_id=self.project_id,
            snapshot_id=old_plan.snapshot_id,
            base_project_revision=project.current_revision,
            decision="approve",
            comment=None,
            actor=self.actor,
            request_id="old-plan-review",
        )
        self.assertTrue(review.use_cases_state.approved)
        old_suite = save(
            "test_cases",
            {"test_cases": [{"id": "TC-OLD", "title": "Booking attendance", "artifact_version_id": "original-case-v1"}]},
            "testcases.generate",
            "old-suite",
            source_snapshot_id=old_plan.snapshot_id,
            metadata={
                "source_requirements_snapshot_id": earlier_requirements.snapshot_id,
                "source_context_snapshot_id": earlier_context.snapshot_id,
                "source_use_case_snapshot_id": old_plan.snapshot_id,
                "source_snapshot_ids": {
                    "requirements": earlier_requirements.snapshot_id,
                    "context": earlier_context.snapshot_id,
                    "use_cases": old_plan.snapshot_id,
                },
            },
        )
        save("reports", {"report": "Prior signed report"}, "export", "old-report")
        revised = deepcopy(rows[:8] if removal_only else rows)
        if not removal_only:
            for row in revised[:2]:
                row.update(text=row["text"].replace("8", "6"), content_version=2)
        save("requirements", {"requirements": revised}, "requirements.review", "reviewed-source-change", approved=True)
        save("context", {"notes": "Exact reviewed current booking context."}, "requirements.enrich", "updated-context", approved=True)
        self.project = get_project(self.project_id, actor=self.actor)
        self.old_snapshot = self.project.current_snapshots["use_cases"]
        self.assertEqual(self.project.current_snapshots["test_cases"].snapshot_id, old_suite.snapshot_id)

        def combine(payload, retained_groups, **_kwargs):
            self.assertEqual(_model_reservation.get()(), 25)
            self.assertEqual(payload.workflow_settings.max_iterations, 1)
            self.assertEqual(payload.workflow_settings.retry_attempts, 0)
            result = generated(payload.requirements)
            for field, attribute in (("requirement_analysis", "analysis"), ("coverage_plan", "plan")):
                result[field] = [
                    getattr(retained_groups[row["requirement_id"]], attribute) if row["requirement_id"] in retained_groups else row for row in result[field]
                ]
            count = sum(len(group["scenarios"]) for group in result["coverage_plan"])
            result["review"]["semantic_assessment"] = {"rubric_version": "test", "status": "passed", "assessed_count": count, "scenario_count": count}
            return result

        self.model.side_effect = combine
        return TestClient(app), f"/projects/{self.project_id}/use-cases", self.project.current_revision

    def test_demo_scoped_refresh_recovers_exact_receipt_and_requires_explicit_plan_approval(self):
        client, url, revision = self.prepare_demo_refresh()
        status = client.get(f"{url}/refresh").json()
        self.assertTrue(status["eligible"])
        selected = ["REQ-001", "REQ-002"]
        self.assertEqual(status["required_requirement_ids"], selected)
        headers = {"X-Request-ID": "exact-scenario-refresh"}
        payload = {"base_project_revision": revision, "refresh_requirement_ids": selected}
        no_header = client.post(f"{url}/generate", json=payload)
        self.assertEqual(no_header.status_code, 422, no_header.text)
        missing = client.post(f"{url}/generate", headers=headers, json={**payload, "refresh_requirement_ids": selected[:1]})
        self.assertEqual(missing.status_code, 409, missing.text)
        self.assertEqual(missing.json()["detail"]["required_requirement_ids"], selected[1:])
        self.model.assert_not_called()
        publish = service.append_stage_snapshot

        def drop_ack(**kwargs):
            publish(**kwargs)
            raise RuntimeError("Synthetic committed response loss")

        with patch.object(service, "append_stage_snapshot", side_effect=drop_ack):
            result = client.post(f"{url}/generate", headers=headers, json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(client.post(f"{url}/generate", headers=headers, json=payload).json(), result.json())
        mismatch = client.post(f"{url}/generate", headers=headers, json={**payload, "refresh_requirement_ids": list(reversed(selected))})
        self.assertEqual(mismatch.status_code, 409)
        after = get_project(self.project_id, actor=self.actor)
        self.assertFalse(after.stage_state["use_cases"].approved)
        self.assertTrue(after.stage_state["test_cases"].stale)
        for stage in ("test_cases", "reports"):
            self.assertEqual(after.current_snapshots[stage], self.project.current_snapshots[stage])
        snapshot = after.current_snapshots["use_cases"]
        for field in ("coverage_plan", "requirement_analysis"):
            self.assertEqual(snapshot.payload[field][2:], self.old_snapshot.payload[field][2:])
        self.assertEqual(len(self.model.call_args.kwargs["retained_groups"]), 7)
        self.assertEqual(snapshot.metadata["scenario_refresh_predecessor"]["source_test_cases_snapshot_id"], after.current_snapshots["test_cases"].snapshot_id)
        self.model.assert_called_once()
        self.mocks["record_usage_event"].assert_called_once()
        review = client.post(
            f"{url}/reviews",
            headers={"X-Request-ID": "explicit-revised-plan-approval"},
            json={"snapshot_id": snapshot.snapshot_id, "base_project_revision": after.current_revision, "decision": "approve"},
        )
        self.assertEqual(review.status_code, 200, review.text)
        self.assertTrue(review.json()["use_cases_state"]["approved"])
        self.assertFalse(any(path.startswith("approval_continuation_outbox/") for path in self.store))
        receipt = client.get(f"{url}/refresh", params={"request_id": headers["X-Request-ID"]}).json()
        self.assertEqual(receipt["result"]["snapshot_id"], snapshot.snapshot_id)
        self.assertEqual(receipt["result"]["refreshed_requirement_ids"], selected)
        self.assertEqual(len(receipt["result"]["retained_requirement_ids"]), 7)
        self.assertFalse(receipt["eligible"])
        self.assertIsNone(receipt["attempt"])

    def test_demo_scenario_refresh_allows_removal_only_without_regenerating_groups(self):
        client, url, revision = self.prepare_demo_refresh(removal_only=True)
        status = client.get(f"{url}/refresh").json()
        self.assertTrue(status["eligible"])
        self.assertEqual(status["required_requirement_ids"], [])
        request_id = "removal-only-scenario-refresh"
        result = client.post(
            f"{url}/generate",
            headers={"X-Request-ID": request_id},
            json={"base_project_revision": revision, "refresh_requirement_ids": []},
        )
        self.assertEqual(result.status_code, 200, result.text)
        snapshot = result.json()["current_snapshots"]["use_cases"]
        self.assertEqual(snapshot["payload"]["coverage_plan"], self.old_snapshot.payload["coverage_plan"][:8])
        self.assertEqual(snapshot["payload"]["requirement_analysis"], self.old_snapshot.payload["requirement_analysis"][:8])
        receipt = client.get(f"{url}/refresh", params={"request_id": request_id}).json()["result"]
        self.assertEqual(receipt["refreshed_requirement_ids"], [])
        self.assertEqual(receipt["removed_requirement_ids"], ["REQ-009"])
        self.assertEqual(self.mocks["record_usage_event"].call_args.kwargs["quantity"], 0)

    def test_demo_scenario_refresh_interruption_blocks_new_work_until_exact_owner_release(self):
        client, url, revision = self.prepare_demo_refresh()
        source = self.project.current_snapshots
        payload = {"base_project_revision": revision, "refresh_requirement_ids": ["REQ-001", "REQ-002"]}
        headers = {"X-Request-ID": "interrupted-scenario-refresh"}

        def interrupt(*_args, **_kwargs):
            self.assertEqual(_model_reservation.get()(), 25)
            raise RuntimeError("Synthetic provider outcome is unconfirmed")

        self.model.side_effect = interrupt
        response = client.post(f"{url}/generate", headers=headers, json=payload)
        self.assertEqual(response.status_code, 502, response.text)
        status = client.get(f"{url}/refresh", params={"request_id": headers["X-Request-ID"]}).json()
        self.assertTrue(status["blocked"])
        self.assertIsNone(status["result"])
        attempt = status["attempt"]
        self.assertEqual(attempt["state"], "interrupted")
        self.assertEqual(attempt["source_snapshot_id"], source["use_cases"].snapshot_id)
        same = client.post(f"{url}/generate", headers=headers, json=payload)
        self.assertEqual(same.json()["detail"]["code"], "project_draft_recovery_required")
        contender = client.post(f"{url}/generate", headers={"X-Request-ID": "another"}, json=payload)
        self.assertEqual(contender.json()["detail"]["code"], "project_draft_busy")
        app.dependency_overrides[get_current_user] = lambda: AuthUser(sub="other", name="Other")
        release = f"{url}/refresh/{attempt['operation_id']}/release"
        self.assertEqual(client.post(release, json={"fence": attempt["fence"]}).status_code, 403)
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.assertEqual(client.post(release, json={"fence": attempt["fence"] + 1}).status_code, 409)
        self.assertEqual(client.post(release, json={"fence": attempt["fence"]}).json(), {"released": True})
        self.assertFalse(client.get(f"{url}/refresh").json()["blocked"])
        self.assertEqual(get_project(self.project_id, actor=self.actor).current_snapshots, source)
        self.model.assert_called_once()
        self.mocks["record_usage_event"].assert_not_called()

    def test_scoped_refresh_preserves_groups_and_historical_review_and_records_changed_scope(self):
        self.prepare_scope()
        selected = ["REQ-001", "REQ-003", "REQ-009", "REQ-010"]
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.addCleanup(app.dependency_overrides.clear)
        client = TestClient(app)
        payload = {"base_project_revision": self.project.current_revision, "refresh_requirement_ids": selected}
        headers = {"X-Request-ID": "scoped-use-cases-1"}
        response = client.post(
            f"/projects/{self.project_id}/use-cases/generate",
            json=payload,
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        replay = client.post(f"/projects/{self.project_id}/use-cases/generate", json=payload, headers=headers)
        self.assertEqual(replay.json(), response.json())
        self.model.assert_called_once()
        after = get_project(self.project_id, actor=self.actor)
        self.assertEqual(self.model.call_args.args[0].context.notes, "Current reviewed context")
        self.assertEqual(set(self.model.call_args.kwargs["retained_groups"]), {"REQ-002", "REQ-004", "REQ-005", "REQ-006", "REQ-007"})
        new = after.current_snapshots["use_cases"]
        expected = {
            "previous_snapshot_id": self.old_snapshot.snapshot_id,
            "refreshed_requirement_ids": selected,
            "retained_requirement_ids": ["REQ-002", "REQ-004", "REQ-005", "REQ-006", "REQ-007"],
            "removed_requirement_ids": ["REQ-008"],
        }
        self.assertEqual(new.metadata["use_case_refresh"], expected)
        self.assertEqual(new.source_snapshot_id, self.project.current_snapshots["requirements"].snapshot_id)
        for field in ("coverage_plan", "requirement_analysis"):
            old_by_id = {row["requirement_id"]: row for row in self.old_snapshot.payload[field]}
            new_by_id = {row["requirement_id"]: row for row in new.payload[field]}
            self.assertNotIn("REQ-008", new_by_id)
            self.assertEqual(new_by_id["REQ-001"]["requirement_text"], "Changed requirement 1")
            self.assertEqual(new_by_id["REQ-002"], old_by_id["REQ-002"])
        self.assertFalse(new.approved)
        self.assertFalse(after.stage_state["use_cases"].approved)
        self.assertNotIn("latest_human_review", after.stage_state["use_cases"].metadata)
        self.assertEqual(after.current_snapshots["test_cases"], self.project.current_snapshots["test_cases"])
        self.assertTrue(after.stage_state["test_cases"].stale)
        path = f"qa_projects/{self.project_id}/snapshots/{self.old_snapshot.snapshot_id}"
        self.assertEqual(self.store[path], self.before[path])
        self.mocks["record_usage_event"].assert_called_once()
        self.assertEqual(self.mocks["record_usage_event"].call_args.kwargs["quantity"], len(selected))

    def test_scope_requires_every_changed_and_added_requirement_and_current_context(self):
        self.prepare_scope()
        for requested, code in [(["REQ-001"], 409), (["REQ-008"], 422), (["REQ-001", "REQ-001"], 422)]:
            with self.subTest(requested=requested), self.assertRaises(HTTPException) as raised:
                self.run_generation(refresh_requirement_ids=requested)
            self.assertEqual(raised.exception.status_code, code)
            if code == 409:
                self.assertEqual(raised.exception.detail["required_requirement_ids"], ["REQ-009", "REQ-010"])
        self.store[f"qa_projects/{self.project_id}"]["stage_state"]["context"]["stale"] = True
        with self.assertRaisesRegex(HTTPException, "Refresh the saved Context"):
            self.run_generation(refresh_requirement_ids=["REQ-001", "REQ-009", "REQ-010"])
        self.model.assert_not_called()
        self.mocks["enforce_billing_access"].assert_not_called()

    def test_removal_only_reuses_every_current_group_and_charges_no_new_scenarios(self):
        self.prepare_scope(removal_only=True)
        after = self.run_generation(refresh_requirement_ids=[])
        self.assertEqual(len(self.model.call_args.kwargs["retained_groups"]), 7)
        snapshot = after.current_snapshots["use_cases"]
        self.assertEqual(snapshot.payload["coverage_plan"], self.old_snapshot.payload["coverage_plan"][:7])
        self.assertEqual(snapshot.metadata["use_case_refresh"]["removed_requirement_ids"], ["REQ-008"])
        self.assertEqual(self.mocks["record_usage_event"].call_args.kwargs["quantity"], 0)

    def test_scope_rejects_changed_retained_groups_and_unavailable_combined_review(self):
        self.prepare_scope()
        combine = self.model.side_effect
        for failure in ("changed", "timeout", "fallback"):

            def fail(payload, **kwargs):
                result = deepcopy(combine(payload, **kwargs))
                if failure == "changed":
                    result["coverage_plan"][1]["scenarios"][0]["title"] = "Unexpected change"
                elif failure == "timeout":
                    result["workflow_diagnostics"].update(status="partial", timed_out=True, failure_reason="semantic_review_timeout")
                    result["review"]["semantic_assessment"]["assessed_count"] = 1
                else:
                    result["workflow_diagnostics"].update(status="fallback", used_fallback=True)
                return result

            self.model.side_effect = fail
            with self.subTest(failure=failure), self.assertRaises(HTTPException):
                self.run_generation(refresh_requirement_ids=["REQ-001", "REQ-009", "REQ-010"])
            self.assertEqual(self.store, self.before)
        self.mocks["record_usage_event"].assert_not_called()

    def test_regenerates_all_ten_and_preserves_downstream_and_history(self):
        after = self.run_generation()
        payload = self.model.call_args.args[0]
        self.assertEqual(len(payload.requirements), 10)
        self.assertFalse(payload.coverage_plan)
        self.assertFalse(payload.requirement_analysis)
        self.assertEqual(payload.context.notes, "Use the saved context")
        self.assertEqual(len(payload.context.requirements), 10)
        new = after.current_snapshots["use_cases"]
        self.assertEqual(new.version, 2)
        self.assertEqual(len(new.payload["coverage_plan"]), 10)
        self.assertFalse(new.approved)
        self.assertFalse(after.stage_state["use_cases"].approved)
        self.assertFalse(after.stage_state["use_cases"].stale)
        self.assertEqual(new.source_snapshot_id, self.project.current_snapshots["requirements"].snapshot_id)
        self.assertEqual(new.metadata["guidance"]["stage"], "use_cases")
        for stage in ["test_cases", "execution", "reports"]:
            self.assertEqual(after.current_snapshots[stage], self.project.current_snapshots[stage])
            self.assertTrue(after.stage_state[stage].stale)
        path = f"qa_projects/{self.project_id}/snapshots/{self.old_snapshot.snapshot_id}"
        self.assertEqual(self.store[path], self.before[path])
        self.mocks["enforce_billing_access"].assert_called_once_with(current_user=self.actor, billing_key="testcases.generate")
        self.mocks["record_usage_event"].assert_called_once()

    def test_bad_inputs_fail_before_model(self):
        project_path = f"qa_projects/{self.project_id}"
        mutations = [
            lambda: self.store[project_path].update(status="archived"),
            lambda: self.store[project_path]["stage_state"]["requirements"].update(approved=False),
            lambda: self.store[project_path]["stage_state"]["requirements"].update(stale=True),
            lambda: self.store[project_path].update(current_revision=self.project.current_revision + 1),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.store.clear()
                self.store.update(deepcopy(self.before))
                mutate()
                with self.assertRaises((HTTPException, ProjectConflictError)):
                    self.run_generation()
        self.model.assert_not_called()
        self.mocks["enforce_billing_access"].assert_not_called()

    def test_other_owner_and_unapproved_row_cannot_generate(self):
        with self.assertRaises(ProjectPermissionError):
            service.generate_project_use_cases(
                project_id=self.project_id,
                actor=AuthUser(sub="other", email="other@example.com", name="Other"),
                request_id="other",
                base_project_revision=self.project.current_revision,
            )
        requirements = self.project.current_snapshots["requirements"]
        path = f"qa_projects/{self.project_id}/snapshots/{requirements.snapshot_id}"
        self.store[path]["payload"]["requirements"][0]["review_status"] = "Needs Review"
        with self.assertRaises(HTTPException):
            self.run_generation()
        self.model.assert_not_called()

    def test_failed_incomplete_or_malformed_output_preserves_snapshot(self):
        valid = generated([Requirement.model_validate(r) for r in self.project.current_snapshots["requirements"].payload["requirements"]])
        invalid = []
        for status in ["failed", "fallback"]:
            item = deepcopy(valid)
            item["workflow_diagnostics"]["status"] = status
            invalid.append(item)
        for field, value in [("used_fallback", True), ("failed_shard_count", 1), ("timed_out", True)]:
            item = deepcopy(valid)
            item["workflow_diagnostics"][field] = value
            invalid.append(item)
        for field in ["requirement_analysis", "coverage_plan"]:
            item = deepcopy(valid)
            item[field] = item[field][:8]
            invalid.append(item)
        item = deepcopy(valid)
        item["coverage_plan"][0]["scenarios"] = []
        invalid.append(item)
        item = deepcopy(valid)
        item["coverage_plan"][0]["scenarios"][0]["requirement_id"] = "unknown"
        invalid.append(item)
        invalid.append({"coverage_plan": "invalid"})
        for result in invalid:
            with self.subTest(result=result):
                self.model.side_effect = None
                self.model.return_value = result
                with self.assertRaises(HTTPException):
                    self.run_generation()
                self.assertEqual(self.store, self.before)
        self.mocks["record_usage_event"].assert_not_called()

    def test_advisory_reviewer_timeout_retains_valid_plan_as_new_unapproved_snapshot(self):
        valid = generated([Requirement.model_validate(r) for r in self.project.current_snapshots["requirements"].payload["requirements"]])
        valid["workflow_diagnostics"].update(status="partial", timed_out=True, failure_reason="semantic_review_timeout")
        self.model.side_effect = None
        self.model.return_value = valid
        after = self.run_generation()
        self.assertFalse(after.stage_state["use_cases"].approved)
        self.assertNotEqual(after.current_snapshots["use_cases"].snapshot_id, self.old_snapshot.snapshot_id)
        self.assertEqual(len(after.current_snapshots["use_cases"].payload["coverage_plan"]), 10)

    def test_model_exception_billing_and_guidance_failures_preserve_snapshot(self):
        for target in ["generate_use_cases", "enforce_billing_access", "prepare_run"]:
            with self.subTest(target=target), patch.object(service, target, side_effect=HTTPException(503, "Unavailable")):
                with self.assertRaises(HTTPException):
                    self.run_generation()
                self.assertEqual(self.store, self.before)

    def test_concurrent_edit_wins_and_generation_does_not_publish(self):
        def edit_then_generate(payload, **kwargs):
            update_project(
                project_id=self.project_id, actor=self.actor, request_id="other-edit", name="New name", base_project_revision=self.project.current_revision
            )
            return generated(payload.requirements)

        self.model.side_effect = edit_then_generate
        with self.assertRaises(ProjectConflictError):
            self.run_generation()
        after = get_project(self.project_id, actor=self.actor)
        self.assertEqual(after.name, "New name")
        self.assertEqual(after.current_snapshots, self.project.current_snapshots)
        self.assertFalse(any(key.startswith("usage_events/") for key in self.store))

    def test_unexpected_model_exception_has_generation_error_and_preserves_snapshot(self):
        self.model.side_effect = RuntimeError("Provider failure")
        with self.assertRaises(HTTPException) as raised:
            self.run_generation()
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("generation failed", raised.exception.detail)
        self.assertEqual(self.store, self.before)

    def test_endpoint_requires_auth_revision_and_rejects_client_artifacts(self):
        client = TestClient(app)
        url = f"/projects/{self.project_id}/use-cases/generate"
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.addCleanup(app.dependency_overrides.clear)
        for payload in [{}, {"base_project_revision": self.project.current_revision, "requirements": []}]:
            self.assertEqual(client.post(url, json=payload).status_code, 422)
        app.dependency_overrides.clear()
        self.assertEqual(client.post(url, json={"base_project_revision": self.project.current_revision}).status_code, 401)
        self.model.assert_not_called()

    def test_endpoint_success_and_conflict(self):
        client = TestClient(app)
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.addCleanup(app.dependency_overrides.clear)
        url = f"/projects/{self.project_id}/use-cases/generate"
        payload = {"base_project_revision": self.project.current_revision}
        response = client.post(url, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["stage_state"]["use_cases"]["approved"])
        self.assertEqual(client.post(url, json=payload).status_code, 409)
        self.assertEqual(self.model.call_count, 1)
