from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import AuthUser, Requirement, RequirementsWorkflowResponse
from app.contracts.requirement_imports import ImportApplyInput, ImportRecoveryInput, RequirementReviewsInput
from app.services.requirement_reconciliation import canonical_requirements, build_preview, reconcile
from app.services import requirement_import_service as service
from app.agents.requirement_matching_agent import suggest_matches
from app.main import app, get_current_user
from test_use_case_review_service import FakeFirestoreClient, _run_transaction

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
ACTOR = AuthUser(sub="owner", name="Owner")
PID = "import-project"


def requirement(i, text=None, **kwargs):
    return Requirement(
        id=f"REQ-{i:03d}",
        text=text or f"The system shall preserve document rule {i} for task creation.",
        source_system="file",
        source_path="audit-requirements.md",
        review_status="Approved",
        **kwargs,
    )


def snapshot(requirements, sid="original", revision=2):
    return {
        "snapshot_id": sid,
        "project_id": PID,
        "stage": "requirements",
        "version": 1,
        "project_revision": revision,
        "approved": True,
        "operation": "requirements.parse",
        "created_at": NOW,
        "payload": {"requirements": [r.model_dump(mode="json") for r in requirements], "source_name": "audit-requirements.md"},
    }


def preview(rows=None, incoming=None):
    current, _ = canonical_requirements(PID, snapshot(rows or [requirement(i) for i in range(1, 9)]))
    return build_preview(
        PID,
        7,
        current,
        incoming
        or [
            Requirement(id="REQ-001", text="The portal displays agricultural documents in PDF format.", source_system="jira", source_issue_key="TX-6"),
            Requirement(
                id="REQ-002", text="A category sidebar provides hierarchical navigation of agricultural topics.", source_system="jira", source_issue_key="TX-6"
            ),
        ],
        "TX-6",
        "requirements.import.jira",
        "preview-1",
    )


def choices(p, **kwargs):
    return ImportApplyInput(
        base_project_revision=p.base_project_revision,
        idempotency_key="apply-1",
        decisions=[{"candidate_id": c.candidate_id, "action": "add"} for c in p.candidates],
        **kwargs,
    )


class ReconciliationTests(unittest.TestCase):
    def test_eight_plus_two_preserves_ids_approvals_and_unique_identity(self):
        p = preview()
        active, retired, counts = reconcile(p, choices(p))
        self.assertEqual([r.id for r in active], [f"REQ-{i:03d}" for i in range(1, 11)])
        self.assertEqual([r.review_status for r in active], ["Approved"] * 8 + ["Needs Review"] * 2)
        self.assertEqual(len({r.requirement_uid for r in active}), 10)
        self.assertEqual(counts["active"], 10)
        self.assertFalse(retired)

    def test_cross_source_exact_match_preserves_approval_adds_provenance(self):
        old = requirement(1)
        new = old.model_copy(update={"source_system": "azure_devops", "source_path": "different", "source_issue_key": "12"})
        p = preview([old], [new])
        self.assertEqual(p.candidates[0].classification, "unchanged")
        d = choices(p)
        d.decisions[0].action = "keep"
        d.decisions[0].target_requirement_uid = p.current_requirements[0].requirement_uid
        active, _, counts = reconcile(p, d)
        self.assertEqual(active[0].content_version, 1)
        self.assertEqual(active[0].review_status, "Approved")
        self.assertEqual(len(active[0].sources), 2)
        self.assertEqual(counts["added"], 0)

    def test_cross_source_changed_match_keeps_id_and_resets_approval(self):
        old = requirement(42, "The system shall limit task titles to 100 characters.")
        new = Requirement(id="REQ-001", text="The system shall limit task titles to 200 characters.", source_system="jira")
        p = preview([old], [new])
        d = choices(p)
        d.decisions[0].action = "update"
        d.decisions[0].target_requirement_uid = p.current_requirements[0].requirement_uid
        active, _, _ = reconcile(p, d)
        self.assertEqual(active[0].id, "REQ-042")
        self.assertEqual(active[0].content_version, 2)
        self.assertEqual(active[0].review_status, "Needs Review")
        self.assertEqual(len(active[0].sources), 2)

    def test_case_sensitive_content_change_revokes_approval(self):
        p = preview([requirement(1, 'The account prefix shall equal "ADMIN".')], [requirement(1, 'The account prefix shall equal "admin".')])
        d = choices(p)
        d.decisions[0].action = "update"
        d.decisions[0].target_requirement_uid = p.current_requirements[0].requirement_uid
        active, _, _ = reconcile(p, d)
        self.assertEqual(active[0].content_version, 2)
        self.assertEqual(active[0].review_status, "Needs Review")

    def test_scope_retires_only_omitted_subset(self):
        p = preview()
        scope = [r.requirement_uid for r in p.current_requirements[:3]]
        d = choices(p, update_scope=scope, confirm_retirements=True)
        d.decisions[0].action = "update"
        d.decisions[0].target_requirement_uid = scope[0]
        active, retired, counts = reconcile(p, d)
        self.assertEqual([r.id for r in retired], ["REQ-002", "REQ-003"])
        self.assertTrue(all(r.lifecycle_status == "retired" for r in retired))
        self.assertTrue({f"REQ-{i:03d}" for i in range(4, 9)} <= {r.id for r in active})
        self.assertEqual(counts["retired"], 2)

    def test_retirement_requires_confirmation(self):
        p = preview()
        with self.assertRaises(HTTPException):
            reconcile(p, choices(p, update_scope=[p.current_requirements[0].requirement_uid]))

    def test_unknown_scope_and_missing_decisions_fail(self):
        p = preview()
        with self.assertRaises(HTTPException):
            reconcile(p, choices(p, update_scope=["unknown"], confirm_retirements=True))
        d = choices(p)
        d.decisions = d.decisions[:1]
        with self.assertRaises(HTTPException):
            reconcile(p, d)

    def test_two_updates_to_same_requirement_fail(self):
        p = preview()
        d = choices(p)
        for row in d.decisions:
            row.action = "update"
            row.target_requirement_uid = p.current_requirements[0].requirement_uid
        with self.assertRaises(HTTPException):
            reconcile(p, d)

    def test_two_identical_incoming_matches_need_decisions(self):
        old = requirement(1)
        p = preview([old], [old, old])
        self.assertTrue(all(c.classification == "needs_decision" for c in p.candidates))

    def test_same_filename_and_id_do_not_establish_identity(self):
        p = preview([requirement(1)], [requirement(1, "Publish weather maps covering coastal ocean currents.")])
        self.assertEqual(p.candidates[0].classification, "new")

    def test_failed_semantics_never_silently_assumes_unrelated(self):
        p = preview()
        from app.services.requirement_reconciliation import compare_requirements

        rows = compare_requirements(p.current_requirements, [c.requirement for c in p.candidates], semantic_failed=True)
        self.assertTrue(all(c.classification == "needs_decision" for c in rows))

    def test_retired_display_ids_are_not_reused(self):
        p = preview()
        active, _, _ = reconcile(p, choices(p), [requirement(99)])
        self.assertEqual(active[-2].id, "REQ-100")

    def test_keep_cannot_smuggle_changed_text(self):
        p = preview()
        d = choices(p)
        d.decisions[0].action = "keep"
        d.decisions[0].target_requirement_uid = p.current_requirements[0].requirement_uid
        with self.assertRaises(HTTPException):
            reconcile(p, d)

    def test_legacy_identity_is_stable_and_does_not_mutate_snapshot(self):
        s = snapshot([requirement(1)])
        before = deepcopy(s)
        self.assertEqual(canonical_requirements(PID, s), canonical_requirements(PID, s))
        self.assertEqual(s, before)

    def test_model_unknown_identifiers_are_rejected(self):
        p = preview()
        with (
            patch("app.agents.requirement_matching_agent.get_settings") as settings,
            patch(
                "app.agents.requirement_matching_agent.run_adk_json",
                return_value={"matches": [{"candidate_id": "incoming-1", "requirement_uid": "unknown", "reason": "fake"}]},
            ),
        ):
            settings.return_value.model_name = "fixture"
            matches, failed = suggest_matches(p.current_requirements, [c.requirement for c in p.candidates])
        self.assertEqual(matches, {})
        self.assertTrue(failed)

    def test_duplicate_new_candidates_require_a_decision(self):
        from app.services.requirement_reconciliation import compare_requirements

        rows = compare_requirements([], [requirement(1), requirement(1)])
        self.assertTrue(all(c.classification == "needs_decision" for c in rows))

    def test_new_parent_links_follow_allocated_ids(self):
        p = preview(incoming=[requirement(1), requirement(2, parent_requirement_id="REQ-001")])
        rows, _, _ = reconcile(p, choices(p))
        self.assertEqual(rows[-1].parent_requirement_id, "REQ-009")

    def test_reimport_uses_previously_reviewed_content_mapping(self):
        old = requirement(1)
        p = preview([old], [requirement(2, "Display coastal weather maps and alerts.")])
        d = choices(p)
        d.decisions[0].action = "update"
        d.decisions[0].target_requirement_uid = p.current_requirements[0].requirement_uid
        active, _, _ = reconcile(p, d)
        p = build_preview(PID, 8, active, [old], "audit-requirements.md", "requirements.parse", "again")
        self.assertIn(active[0].requirement_uid, [m.requirement_uid for m in p.candidates[0].suggestions])


class ImportPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.store = {
            f"qa_projects/{PID}": {
                "project_id": PID,
                "name": "Audit",
                "owner_user_id": ACTOR.sub,
                "status": "active",
                "created_at": NOW,
                "updated_at": NOW,
                "current_revision": 7,
                "stage_state": {
                    "requirements": {"current_snapshot_id": "original", "version": 1, "approved": True},
                    "test_cases": {"current_snapshot_id": "tests", "version": 1, "approved": True, "stale": False},
                },
            },
            f"qa_projects/{PID}/snapshots/original": snapshot([requirement(i) for i in range(1, 9)]),
            f"qa_projects/{PID}/snapshots/tests": {
                **snapshot([], "tests", 6),
                "stage": "test_cases",
                "payload": {"test_cases": [{"id": "TC-001", "requirement_id": "REQ-001"}]},
            },
        }
        self.client = FakeFirestoreClient(self.store)
        for target, value in [
            ("app.services.requirement_import_service.get_required_firestore_client", self.client),
            ("app.services.workflow_project_service.get_required_firestore_collection", self.client.collection("qa_projects")),
            ("app.services.workflow_project_service.get_required_firestore_client", self.client),
        ]:
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("app.services.requirement_import_service.transactional", side_effect=_run_transaction)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("app.services.requirement_import_service.suggest_matches", return_value=({}, False))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("app.services.workflow_project_service.transactional", side_effect=_run_transaction)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = service.repository()
        self.p = preview()
        self.repo.save(self.p, ACTOR, [], {})

    def test_stage_does_not_publish_and_apply_is_complete_after_reload(self):
        before = deepcopy(self.store[f"qa_projects/{PID}"])
        self.assertEqual(self.repo.list_pending(PID, ACTOR)[0].import_id, self.p.import_id)
        self.assertEqual(before["current_revision"], 7)
        project = self.repo.apply(PID, self.p.import_id, ACTOR, choices(self.p), "request")
        self.assertEqual(project.current_revision, 8)
        self.assertEqual(len(project.current_snapshots["requirements"].payload["requirements"]), 10)
        self.assertTrue(project.stage_state["test_cases"].stale)
        self.assertEqual(project.current_snapshots["test_cases"].payload["test_cases"][0]["id"], "TC-001")
        self.assertEqual(len(self.store[f"qa_projects/{PID}/snapshots/original"]["payload"]["requirements"]), 8)
        self.assertEqual(len(self.repo.history(PID, self.p.current_requirements[0].requirement_uid, ACTOR)), 2)

    def test_retry_is_idempotent_but_changed_retry_rejected(self):
        d = choices(self.p)
        self.repo.apply(PID, self.p.import_id, ACTOR, d, "first")
        before = deepcopy(self.store)
        self.repo.apply(PID, self.p.import_id, ACTOR, d, "retry")
        self.assertEqual(before, self.store)
        d.decisions[0].action = "skip"
        with self.assertRaises(HTTPException):
            self.repo.apply(PID, self.p.import_id, ACTOR, d, "different")
        self.assertEqual(before, self.store)

    def test_failure_rolls_back_snapshot_revision_and_receipt(self):
        self.client.fail_on_create_segment = "/timeline/"
        before = deepcopy(self.store)
        with self.assertRaises(RuntimeError):
            self.repo.apply(PID, self.p.import_id, ACTOR, choices(self.p), "failure")
        self.assertEqual(before, self.store)
        self.client.fail_on_create_segment = None
        self.assertEqual(self.repo.apply(PID, self.p.import_id, ACTOR, choices(self.p), "retry").current_revision, 8)

    def test_concurrent_previews_only_one_applies(self):
        second = self.p.model_copy(update={"import_id": "second"})
        self.repo.save(second, ACTOR, [], {})
        self.repo.apply(PID, self.p.import_id, ACTOR, choices(self.p), "first")
        with self.assertRaises(HTTPException) as error:
            self.repo.apply(PID, "second", ACTOR, choices(second).model_copy(update={"idempotency_key": "second"}), "second")
        self.assertEqual(error.exception.status_code, 409)

    def test_authorization_covers_read_stage_apply_cancel_and_history(self):
        other = AuthUser(sub="other", name="Other")
        for call in [
            lambda: self.repo.read(PID, self.p.import_id, other),
            lambda: self.repo.save(self.p, other, [], {}),
            lambda: self.repo.apply(PID, self.p.import_id, other, choices(self.p), "bad"),
            lambda: self.repo.cancel(PID, self.p.import_id, other),
            lambda: self.repo.history(PID, "uid", other),
        ]:
            with self.assertRaises(HTTPException) as error:
                call()
            self.assertEqual(error.exception.status_code, 404)

    def test_cancel_prevents_apply_and_leaves_project_unchanged(self):
        before = deepcopy(self.store[f"qa_projects/{PID}"])
        self.repo.cancel(PID, self.p.import_id, ACTOR)
        with self.assertRaises(HTTPException):
            self.repo.apply(PID, self.p.import_id, ACTOR, choices(self.p), "cancelled")
        self.assertEqual(before, self.store[f"qa_projects/{PID}"])

    def test_recompare_uses_new_revision_without_reextracting(self):
        self.store[f"qa_projects/{PID}"]["current_revision"] = 8
        result = service.recompare_import(PID, self.p.import_id, ACTOR)
        self.assertEqual(result.base_project_revision, 8)
        self.assertNotEqual(result.import_id, self.p.import_id)
        self.assertEqual(self.repo.read(PID, self.p.import_id, ACTOR)["status"], "cancelled")

    def test_empty_extraction_and_stale_staging_fail_without_changes(self):
        before = deepcopy(self.store)
        with self.assertRaises(HTTPException):
            service.stage_import(PID, ACTOR, 7, RequirementsWorkflowResponse(source_name="empty", raw_text="", requirements=[]), "requirements.parse")
        with self.assertRaises(HTTPException):
            self.repo.save(self.p.model_copy(update={"base_project_revision": 6}), ACTOR, [], {})
        self.assertEqual(before, self.store)

    def test_recovery_preserves_original_ids_and_approvals(self):
        self.store[f"qa_projects/{PID}/snapshots/jira"] = snapshot([c.requirement for c in self.p.candidates], "jira", 7)
        self.store[f"qa_projects/{PID}"]["stage_state"]["requirements"]["current_snapshot_id"] = "jira"
        p = service.prepare_recovery(PID, ACTOR, ImportRecoveryInput(base_project_revision=7, baseline_snapshot_id="original", incoming_snapshot_id="jira"))
        self.assertEqual(len(p.current_requirements), 8)
        project = self.repo.apply(PID, p.import_id, ACTOR, choices(p), "recovery")
        rows = project.current_snapshots["requirements"].payload["requirements"]
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["id"], "REQ-001")
        self.assertEqual(rows[0]["review_status"], "Approved")
        self.assertEqual(rows[-1]["id"], "REQ-010")

    def test_review_is_durable_and_invalidates_pending_preview(self):
        payload = RequirementReviewsInput(
            base_project_revision=7,
            idempotency_key="review",
            reviews=[{"requirement_uid": self.p.current_requirements[0].requirement_uid, "review_status": "Rejected", "quality_flags": ["Ambiguous"]}],
        )
        result = self.repo.review(PID, ACTOR, payload, "review")
        self.assertEqual(result.current_snapshots["requirements"].payload["requirements"][0]["review_status"], "Rejected")
        with self.assertRaises(HTTPException):
            self.repo.apply(PID, self.p.import_id, ACTOR, choices(self.p), "stale")

    def test_legacy_project_import_routes_fail_closed_before_extraction(self):
        app.dependency_overrides[get_current_user] = lambda: ACTOR
        self.addCleanup(app.dependency_overrides.clear)
        with (
            TestClient(app) as client,
            patch("app.services.guidance_dependency.prepare_run", return_value=None),
            patch("app.routers.requirements.extract_requirements") as extract,
        ):
            for url, kwargs in [
                ("/requirements/parse", {"data": {"project_id": PID, "base_project_revision": "7"}, "files": {"file": ("a.md", b"text", "text/markdown")}}),
                ("/integrations/jira/import", {"json": {"project_id": PID, "base_project_revision": 7, "epic_key": "TX-6"}}),
                ("/integrations/azure-devops/import", {"json": {"project_id": PID, "base_project_revision": 7, "project": "Example", "work_item_id": 1}}),
            ]:
                response = client.post(url, **kwargs)
                self.assertEqual(response.status_code, 409, response.text)
            extract.assert_not_called()

    def test_all_intake_routes_stage_and_share_the_same_apply_contract(self):
        app.dependency_overrides[get_current_user] = lambda: ACTOR
        self.addCleanup(app.dependency_overrides.clear)
        workflow = {
            "requirements": [c.requirement for c in self.p.candidates],
            "approved": False,
            "review": {},
            "iteration_history": [],
            "coverage_metrics": {},
        }
        common = {"project_id": PID, "base_project_revision": 7, "import_mode": "review"}
        cases = [
            ("requirements", "extract_requirements", "/requirements/parse", {"data": common, "files": {"file": ("renamed.md", b"text", "text/markdown")}}),
            ("requirements", "refine_requirements", "/requirements/parse", {"data": {**common, "feedback": "Clarify", "existing_requirements": "[]"}}),
            ("integrations_jira", "import_requirements_from_jira", "/integrations/jira/import", {"json": {**common, "epic_key": "TX-6"}}),
            (
                "integrations_azure_devops",
                "import_requirements_from_azure_devops",
                "/integrations/azure-devops/import",
                {"json": {**common, "project": "Example", "work_item_id": 1}},
            ),
        ]
        with TestClient(app) as client:
            for module, extractor, url, kwargs in cases:
                with self.subTest(path=url, extractor=extractor), ExitStack() as stack:
                    prefix = f"app.routers.{module}"
                    stack.enter_context(patch("app.services.guidance_dependency.prepare_run", return_value=None))
                    for function in ["enforce_billing_access", "start_workflow_run", "_log_success", "_record_billing_consumption_safe"]:
                        stack.enter_context(patch(f"{prefix}.{function}", return_value=None))
                    if module != "requirements":
                        stack.enter_context(patch(f"{prefix}.project_for", return_value=None))
                    stack.enter_context(patch(f"{prefix}.{extractor}", return_value=workflow))
                    persist = stack.enter_context(patch(f"{prefix}.persist_requirement_versions"))
                    response = client.post(url, **kwargs)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["status"], "pending")
                    self.assertEqual(len(response.json()["current_requirements"]), 8)
                    self.assertEqual(len(response.json()["candidates"]), 2)
                    self.assertEqual(self.store[f"qa_projects/{PID}"]["current_revision"], 7)
                    persist.assert_not_called()

    def test_generation_rejects_retired_forged_and_stale_requirements(self):
        project = self.repo.apply(PID, self.p.import_id, ACTOR, choices(self.p), "first")
        approved = Requirement.model_validate(project.current_snapshots["requirements"].payload["requirements"][0])
        service.validate_generation_baseline(PID, ACTOR, 8, [approved])
        for revision, rows in [
            (7, [approved]),
            (8, [approved.model_copy(update={"lifecycle_status": "retired"})]),
            (8, [approved.model_copy(update={"text": "forged"})]),
            (8, [Requirement(id="REQ-999", text="retired or absent")]),
        ]:
            with self.assertRaises(HTTPException):
                service.validate_generation_baseline(PID, ACTOR, revision, rows)

    def test_other_snapshot_writer_cannot_overwrite_a_concurrent_import(self):
        from app.services import workflow_project_service as projects

        original_commit = projects._commit_project_revision

        def concurrent_import(*args, **kwargs):
            self.repo.apply(PID, self.p.import_id, ACTOR, choices(self.p), "import-wins")
            return original_commit(*args, **kwargs)

        with patch.object(projects, "_commit_project_revision", side_effect=concurrent_import):
            with self.assertRaises(projects.ProjectConflictError):
                projects.append_stage_snapshot(
                    project_id=PID,
                    stage="test_cases",
                    payload={"test_cases": []},
                    operation="testcases.generate",
                    actor=ACTOR,
                    request_id="late",
                    base_project_revision=7,
                )
        project = projects.get_project(PID, actor=ACTOR)
        self.assertEqual(project.current_revision, 8)
        self.assertEqual(len(project.current_snapshots["requirements"].payload["requirements"]), 10)
        self.assertEqual(project.current_snapshots["test_cases"].snapshot_id, "tests")
        self.assertTrue(project.stage_state["test_cases"].stale)

    def test_other_snapshot_writer_rolls_back_a_failed_timeline(self):
        from app.services.workflow_project_service import append_stage_snapshot

        before = deepcopy(self.store)
        self.client.fail_on_create_segment = "/timeline/"
        with self.assertRaises(RuntimeError):
            append_stage_snapshot(
                project_id=PID,
                stage="test_cases",
                payload={"test_cases": []},
                operation="testcases.generate",
                actor=ACTOR,
                request_id="failed",
                base_project_revision=7,
            )
        self.assertEqual(before, self.store)
