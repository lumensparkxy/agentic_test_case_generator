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
from app.contracts.billing import BillingAccount, BillingUserProfile
from app.contracts.requirement_imports import ImportApplyInput, ImportRecoveryInput, RequirementImportPreview, RequirementReviewsInput
from app.routers import integrations_azure_devops, integrations_jira, requirements as requirement_routes
from app.services.billing_service import BillingAccessContext
from app.services.requirement_reconciliation import canonical_requirements, build_preview, reconcile
from app.services.requirement_source_evidence import bind_document_evidence
from app.services import requirement_import_service as service
from app.services import requirement_import_publication as publication
from app.agents.requirement_matching_agent import suggest_matches
from app.main import app, get_current_user
from test_use_case_review_service import FakeFirestoreClient, FakeTransaction, _run_transaction

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


def from_document(req, source_id="SOURCE-42", label="audit-requirements.md", excerpt=None):
    req = req.model_copy(update={"source_path": label, "source_excerpt": excerpt if excerpt is not None else req.text})
    return bind_document_evidence([req], [(label, f"## {source_id}\n\n{req.text}\n")]).requirements[0]


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

    def test_verified_original_id_ranks_a_revision_ahead_of_similar_wording_without_selecting_it(self):
        old = from_document(requirement(1, 'Show "No employees found" for an empty result.'))
        decoy = from_document(requirement(2, 'For guests, the exact feedback is "No users match your query".'), "SOURCE-43")
        new = from_document(requirement(1, 'For guests, the exact feedback is "No employees match your query".'))
        p = preview([decoy, old], [new])
        candidate = p.candidates[0]
        self.assertNotEqual(old.sources[0].source_version, new.sources[0].source_version)
        self.assertEqual(candidate.classification, "needs_decision")
        self.assertIsNone(candidate.target_requirement_uid)
        self.assertEqual([match.requirement_uid for match in candidate.suggestions], [p.current_requirements[i].requirement_uid for i in (1, 0)])
        self.assertEqual([match.verified_original_ids for match in candidate.suggestions], [["SOURCE-42"], []])

    def test_exact_wording_on_another_requirement_does_not_override_verified_source(self):
        old = from_document(requirement(1, "Guests can view meeting room amenities."))
        new = from_document(requirement(2, "Guests can view meeting room capacity."))
        decoy = from_document(requirement(3, new.text), "SOURCE-43")
        p = preview([decoy, old], [new])
        candidate = p.candidates[0]
        self.assertEqual(candidate.classification, "needs_decision")
        self.assertIsNone(candidate.target_requirement_uid)
        self.assertEqual([match.requirement_uid for match in candidate.suggestions], [p.current_requirements[i].requirement_uid for i in (1, 0)])
        self.assertEqual(candidate.suggestions[1].reason, "Identical text")

    def test_exact_wording_on_the_verified_source_keeps_the_existing_identity(self):
        old = from_document(requirement(1))
        p = preview([old], [old])
        self.assertEqual(p.candidates[0].classification, "unchanged")
        self.assertEqual(p.candidates[0].target_requirement_uid, p.current_requirements[0].requirement_uid)

    def test_split_requirements_with_a_shared_source_id_stay_ambiguous(self):
        old = from_document(requirement(1, "Guests can view room availability."))
        sibling = from_document(requirement(2, "Employees can reserve an available room."))
        new = from_document(requirement(3, "Employees can reserve an available room up to 60 days ahead."))
        p = preview([old, sibling], [new])
        candidate = p.candidates[0]
        self.assertEqual(candidate.classification, "needs_decision")
        self.assertIsNone(candidate.target_requirement_uid)
        self.assertEqual([match.requirement_uid for match in candidate.suggestions], [r.requirement_uid for r in p.current_requirements])
        self.assertEqual([match.verified_original_ids for match in candidate.suggestions], [["SOURCE-42"], ["SOURCE-42"]])

    def test_unverified_or_cross_document_source_ids_do_not_create_verified_suggestions(self):
        old = from_document(requirement(1, "Grant team administrators access to archived records."))
        new = requirement(2, "Map tides along the coast.")
        unverified = from_document(new, excerpt="This quote is absent.")
        different_document = from_document(new, label="different.md")
        legacy_claim = old.model_copy(update={"sources": [old.sources[0].model_copy(update={"excerpt_verified": False})]})
        for baseline, incoming in ((old, unverified), (old, different_document), (legacy_claim, from_document(new))):
            with self.subTest(baseline=baseline, incoming=incoming):
                candidate = preview([baseline], [incoming]).candidates[0]
                self.assertEqual(candidate.classification, "new")
                self.assertEqual(candidate.suggestions, [])

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

    def test_source_range_findings_survive_staging_and_recomparison(self):
        response = RequirementsWorkflowResponse(
            source_name="rooms.md",
            raw_text="",
            requirements=[requirement(1)],
            source_evidence_findings=[
                {
                    "kind": "uncovered_range",
                    "source_label": "rooms.md",
                    "source_section": "Lines 6-6",
                    "original_requirement_ids": ["ROOM-101"],
                    "excerpt": "Attendees must be from 1 to 6 inclusive.",
                }
            ],
        )
        saved = service.stage_extraction(
            self.repo, PID, ACTOR, 7, response, "requirements.parse", "range-preview", {"fingerprint": "range"}, lambda transaction: None
        )
        staged = RequirementImportPreview.model_validate(saved)
        self.assertEqual(staged.source_evidence_findings, response.source_evidence_findings)
        self.assertEqual(staged.candidates[0].target_requirement_uid, staged.current_requirements[0].requirement_uid)
        self.assertEqual(staged.current_requirements[0].review_status, "Approved")
        self.assertEqual(self.repo.read(PID, staged.import_id, ACTOR)["source_evidence_findings"][0]["kind"], "uncovered_range")
        repeated = service.recompare_import(PID, staged.import_id, ACTOR)
        self.assertEqual(repeated.source_evidence_findings, staged.source_evidence_findings)
        self.assertEqual(repeated.current_requirements, staged.current_requirements)

    def test_empty_extraction_and_stale_staging_fail_without_changes(self):
        before = deepcopy(self.store)
        with self.assertRaises(HTTPException):
            service.stage_extraction(
                self.repo,
                PID,
                ACTOR,
                7,
                RequirementsWorkflowResponse(source_name="empty", raw_text="", requirements=[]),
                "requirements.parse",
                "empty-preview",
                {"fingerprint": "empty"},
                lambda transaction: None,
            )
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
                    for function in ["enforce_billing_access", "_log_success", "_record_billing_consumption_safe"]:
                        stack.enter_context(patch(f"{prefix}.{function}", return_value=None))
                    stack.enter_context(patch(f"{prefix}.start_workflow_run", return_value="test-intake"))
                    if module != "requirements":
                        stack.enter_context(patch(f"{prefix}.project_for", return_value=None))
                    stack.enter_context(patch.object(publication, "record_billing_consumption"))
                    stack.enter_context(patch(f"{prefix}.{extractor}", return_value=workflow))
                    persist = stack.enter_context(patch(f"{prefix}.persist_requirement_versions"))
                    response = client.post(url, **kwargs)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["status"], "pending")
                    self.assertEqual(len(response.json()["current_requirements"]), 8)
                    self.assertEqual(len(response.json()["candidates"]), 2)
                    self.assertEqual(self.store[f"qa_projects/{PID}"]["current_revision"], 7)
                    persist.assert_not_called()

    def extraction_client(self):
        account = BillingAccount(account_id="account", scope_key=f"user:{ACTOR.sub}", owner_user_id=ACTOR.sub, plan_tier="premium", balance_units=100)
        self.store["billing_accounts/account"] = account.model_dump()
        app.dependency_overrides[get_current_user] = lambda: ACTOR
        self.addCleanup(app.dependency_overrides.clear)
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch("app.services.guidance_dependency.prepare_run", return_value=None))
        stack.enter_context(
            patch("app.services.audit_repository.get_optional_firestore_collection", side_effect=lambda name, **kwargs: self.client.collection(name))
        )
        stack.enter_context(patch("app.services.billing_service.calculate_units_for_quantity", side_effect=lambda *, billing_key, quantity: quantity * 4))
        billing = stack.enter_context(
            patch.object(
                requirement_routes,
                "enforce_billing_access",
                return_value=BillingAccessContext(
                    account=account, profile=BillingUserProfile(user_id=ACTOR.sub), pricing_entry=None, allocation=None, shadow_mode=False
                ),
            )
        )
        workflow = {
            "requirements": [c.requirement for c in self.p.candidates],
            "approved": True,
            "review": {"approved": True},
            "iteration_history": [],
            "coverage_metrics": {},
        }
        return stack.enter_context(TestClient(app)), billing, workflow

    def _extract(self, client, request_id, *, name="a.md", content=b"Original file", data=None, extra_headers=None):
        return client.post(
            "/requirements/parse",
            data={"project_id": PID, "base_project_revision": "7", "import_mode": "review", **(data or {})},
            headers={"X-Request-ID": request_id, **(extra_headers or {})},
            files={"file": (name, content, "text/markdown")},
        )

    def _rows(self, collection):
        return [value for key, value in self.store.items() if key.startswith(collection + "/")]

    def test_file_extraction_atomically_stages_and_replays_paid_preview(self):
        client, billing, workflow = self.extraction_client()
        stage = publication.stage_extraction

        def lose_commit_response(*args, **kwargs):
            stage(*args, **kwargs)
            raise RuntimeError("Synthetic lost transaction reply")

        with (
            patch.object(requirement_routes, "extract_requirements", return_value=workflow) as model,
            patch.object(publication, "stage_extraction", side_effect=lose_commit_response),
        ):
            first = self._extract(client, "file-extraction")
            self.assertEqual(first.status_code, 200, first.text)
            replay = self._extract(client, "file-extraction")
            self.assertEqual(replay.json(), first.json())
            for changed in (
                {"content": b"Different file"},
                {"name": "b.md"},
                {"data": {"workflow_settings": '{"approval_threshold": 99}'}},
                {"extra_headers": {"X-Knowledge-Bypass": "true"}},
            ):
                with self.subTest(changed=changed):
                    self.assertEqual(self._extract(client, "file-extraction", **changed).status_code, 409)
        model.assert_called_once()
        billing.assert_called_once()
        self.assertEqual(self.store[f"qa_projects/{PID}"]["current_revision"], 7)
        preview_id = first.json()["import_id"]
        staged = self.repo.read(PID, preview_id, ACTOR)
        [usage] = self._rows("usage_events")
        [consumption] = self._rows("billing_consumption")
        [ledger] = self._rows("billing_wallet_ledger")
        [run] = self._rows("workflow_runs")
        self.assertEqual(staged["extraction"]["source_event_id"], usage["event_id"])
        self.assertEqual(staged["extraction"]["workflow_run_id"], run["run_id"])
        self.assertEqual({usage["metadata"]["import_id"], consumption["metadata"]["import_id"], run["result"]["import_id"]}, {preview_id})
        self.assertEqual((usage["status"], usage["quantity"], run["status"]), ("completed", 2, "completed"))
        self.assertEqual(
            (consumption["source_event_id"], ledger["units_delta"], self.store["billing_accounts/account"]["balance_units"]), (usage["event_id"], -8, 92)
        )
        self.repo.apply(PID, preview_id, ACTOR, choices(RequirementImportPreview.model_validate(first.json())), "apply-extraction")
        self.assertEqual(self._extract(client, "file-extraction").status_code, 409)
        self.assertEqual(len(self._rows("billing_consumption")), 1)

    def test_feedback_refinement_recovers_pending_preview_and_rejects_cancelled_or_changed_request(self):
        client, billing, workflow = self.extraction_client()
        data = {"feedback": "Add clearer acceptance criteria", "existing_requirements": "[]"}
        with patch.object(requirement_routes, "refine_requirements", return_value=workflow) as model:
            original = self._extract(client, "feedback-1", data=data)
            self.assertEqual(original.status_code, 200, original.text)
            self.assertEqual(self._extract(client, "feedback-1", data=data).json(), original.json())
            self.assertEqual(self._extract(client, "feedback-1", data={**data, "feedback": "Different request"}).status_code, 409)
            self.repo.cancel(PID, original.json()["import_id"], ACTOR)
            self.assertEqual(self._extract(client, "feedback-1", data=data).status_code, 409)
        model.assert_called_once()
        billing.assert_called_once()
        [usage] = self._rows("usage_events")
        [consumption] = self._rows("billing_consumption")
        self.assertEqual((usage["event_type"], usage["quantity"]), ("requirements.refined", 2))
        self.assertEqual(consumption["source_event_id"], usage["event_id"])

    def test_unconfirmed_extraction_preserves_completed_audit_until_exact_retry(self):
        client, billing, workflow = self.extraction_client()
        stage = publication.stage_extraction

        def lose_commit_response(*args, **kwargs):
            stage(*args, **kwargs)
            raise RuntimeError("Synthetic lost transaction reply")

        with patch.object(requirement_routes, "extract_requirements", return_value=workflow) as model:
            with (
                patch.object(publication, "stage_extraction", side_effect=lose_commit_response),
                patch.object(service.RequirementImportRepository, "find_extraction", side_effect=[None, RuntimeError("Receipt read failed")]),
            ):
                unavailable = self._extract(client, "unconfirmed-file")
            self.assertEqual(unavailable.status_code, 503)
            self.assertEqual(unavailable.json()["detail"]["code"], "requirement_extraction_unconfirmed")
            replay = self._extract(client, "unconfirmed-file")
            self.assertEqual(replay.status_code, 200, replay.text)
        model.assert_called_once()
        billing.assert_called_once()
        self.assertEqual([row["status"] for row in self._rows("usage_events")], ["completed"])
        self.assertEqual([row["status"] for row in self._rows("workflow_runs")], ["completed"])

    def test_preview_write_failure_and_stale_extraction_do_not_publish_or_charge(self):
        client, _, workflow = self.extraction_client()
        project_path = f"qa_projects/{PID}"
        prior = deepcopy(self.store)
        create = FakeTransaction.create
        staged = []

        def reject_preview(transaction, document, payload):
            if "/requirement_imports/" in document.path:
                staged.extend(ref.path.split("/")[0] for _, ref, _ in transaction.pending)
                raise RuntimeError("Synthetic preview write failure")
            return create(transaction, document, payload)

        with patch.object(requirement_routes, "extract_requirements", return_value=workflow), patch.object(FakeTransaction, "create", reject_preview):
            failed = self._extract(client, "failed-file")
        self.assertEqual(failed.status_code, 503, failed.text)
        self.assertTrue({"usage_events", "workflow_runs", "billing_wallet_ledger"} <= set(staged))

        def revise(*args, **kwargs):
            self.store[project_path]["current_revision"] += 1
            return workflow

        with patch.object(requirement_routes, "extract_requirements", side_effect=revise):
            stale = self._extract(client, "stale-file")
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(self._rows("billing_consumption"), [])
        self.assertEqual(self._rows("billing_wallet_ledger"), [])
        self.assertEqual(self.store["billing_accounts/account"], prior["billing_accounts/account"])
        self.assertEqual(self._rows(f"{project_path}/requirement_imports"), [value for key, value in prior.items() if "/requirement_imports/" in key])
        self.assertEqual([(event["status"], event["quantity"]) for event in self._rows("usage_events")], [("failed", 0), ("failed", 0)])
        self.assertEqual([run["status"] for run in self._rows("workflow_runs")], ["failed", "failed"])

    def test_connector_imports_replay_current_previews_once_at_the_existing_tariff(self):
        client, billing, workflow = self.extraction_client()
        options = {"project_id": PID, "base_project_revision": 7, "import_mode": "review"}
        for provider, router, endpoint, selector, changed in (
            ("jira", integrations_jira, "jira", {"epic_key": "QA-16"}, {"epic_key": "QA-17"}),
            ("azure_devops", integrations_azure_devops, "azure-devops", {"project": "QA", "work_item_id": 16}, {"work_item_id": 17}),
        ):
            with self.subTest(provider=provider):
                workflow = {**workflow, "requirements": [r.model_copy(update={"source_system": provider}) for r in workflow["requirements"]]}
                url = f"/integrations/{endpoint}/import"
                headers = {"X-Request-ID": f"{provider}-preview"}
                body = {**options, **selector}
                with (
                    patch.object(router, "enforce_billing_access", billing),
                    patch.object(router, f"import_requirements_from_{provider}", return_value=workflow) as model,
                ):
                    first = client.post(url, headers=headers, json=body)
                    self.assertEqual(first.status_code, 200, first.text)
                    self.assertEqual(client.post(url, headers=headers, json=body).json(), first.json())
                    for different in ({**body, **changed}, {**body, "include_children": False}, {**body, "workflow_settings": {"approval_threshold": 99}}):
                        self.assertEqual(client.post(url, headers=headers, json=different).status_code, 409)
                    self.assertEqual(client.post(url, headers={**headers, "X-Knowledge-Bypass": "true"}, json=body).status_code, 409)
                    self.repo.cancel(PID, first.json()["import_id"], ACTOR)
                    self.assertEqual(client.post(url, headers=headers, json=body).status_code, 409)
                model.assert_called_once()
                preview_id = first.json()["import_id"]
                self.assertTrue(all(row["requirement"]["source_system"] == provider for row in first.json()["candidates"]))
                staged = self.repo.read(PID, preview_id, ACTOR)
                event_id = staged["extraction"]["source_event_id"]
                event = self.store[f"usage_events/{event_id}"]
                consumption = self.store[f"billing_consumption/{event_id}"]
                run = self.store[f"workflow_runs/{staged['extraction']['workflow_run_id']}"]
                self.assertEqual((event["event_type"], event["billing_key"], event["quantity"]), (f"requirements.imported.{provider}", "requirements.parse", 2))
                self.assertEqual((consumption["metadata"]["source"], consumption["units_charged"]), (provider, 8))
                self.assertEqual({event["metadata"]["import_id"], consumption["metadata"]["import_id"], run["result"]["import_id"]}, {preview_id})
        self.assertEqual(billing.call_count, 2)
        self.assertEqual(self.store["billing_accounts/account"]["balance_units"], 84)
        self.assertEqual(len(self._rows("usage_events")), 2)
        self.assertEqual(len(self._rows("billing_consumption")), 2)
        self.assertEqual(self.store[f"qa_projects/{PID}"]["current_revision"], 7)

    def test_connector_imports_leave_no_success_writes_on_stale_revision_or_preview_failure(self):
        client, billing, workflow = self.extraction_client()
        project_path = f"qa_projects/{PID}"
        before = deepcopy(self.store)
        original_create = FakeTransaction.create
        staged = []

        def reject_preview(transaction, reference, value):
            if "/requirement_imports/" in reference.path:
                staged.append({ref.path.split("/")[0] for _, ref, _ in transaction.pending})
                raise RuntimeError("Synthetic connector preview write failure")
            return original_create(transaction, reference, value)

        def revise(*args, **kwargs):
            self.store[project_path]["current_revision"] += 1
            return workflow

        for provider, router, endpoint, selector in (
            ("jira", integrations_jira, "jira", {"epic_key": "QA-16"}),
            ("azure_devops", integrations_azure_devops, "azure-devops", {"project": "QA", "work_item_id": 16}),
        ):
            url = f"/integrations/{endpoint}/import"
            body = {"project_id": PID, "base_project_revision": self.store[project_path]["current_revision"], "import_mode": "review", **selector}
            with self.subTest(provider=provider), patch.object(router, "enforce_billing_access", billing):
                with (
                    patch.object(router, f"import_requirements_from_{provider}", return_value=workflow),
                    patch.object(FakeTransaction, "create", reject_preview),
                ):
                    failed = client.post(url, headers={"X-Request-ID": f"{provider}-fail"}, json=body)
                self.assertEqual(failed.status_code, 503, failed.text)
                with patch.object(router, f"import_requirements_from_{provider}", side_effect=revise):
                    stale = client.post(url, headers={"X-Request-ID": f"{provider}-stale"}, json=body)
                self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(len(staged), 2)
        self.assertTrue(all({"billing_wallet_ledger", "usage_events", "workflow_runs"} <= collections for collections in staged))
        self.assertEqual(self._rows("billing_consumption"), [])
        self.assertEqual(self._rows("billing_wallet_ledger"), [])
        self.assertEqual(self.store["billing_accounts/account"], before["billing_accounts/account"])
        self.assertEqual(self._rows(f"{project_path}/requirement_imports"), [value for key, value in before.items() if "/requirement_imports/" in key])
        self.assertEqual([(row["status"], row["quantity"]) for row in self._rows("usage_events")], [("failed", 0)] * 4)

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
