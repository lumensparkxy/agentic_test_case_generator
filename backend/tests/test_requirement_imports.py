from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from threading import Event
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agents.adk_runtime import _model_reservation
from app.models import AuthUser, Requirement, RequirementsWorkflowResponse
from app.contracts.billing import BillingAccount, BillingUserProfile
from app.contracts.guidance import GuidanceManifest
from app.contracts.requirement_imports import ImportApplyInput, ImportRecoveryInput, RequirementImportPreview, RequirementReviewsInput
from app.routers import integrations_azure_devops, integrations_jira, requirements as requirement_routes
from app.services import approval_continuation as continuation_service
from app.services.approval_continuation import OUTBOX_COLLECTION, ContinuationOptions
from app.services.billing_service import BillingAccessContext
from app.services.project_inputs import manifest_from_payload
from app.services.requirement_reconciliation import canonical_requirements, build_preview, reconcile
from app.services.requirement_source_evidence import bind_document_evidence
from app.services import project_draft_admission as admission
from app.services import provider_journey_budget as journeys
from app.services import requirement_import_service as service
from app.services import requirement_import_publication as publication
from app.utils.workflow_diagnostics import RETAINED_REQUIREMENT_DRAFT_WARNING, REVIEW_SCHEMA_INVALID, source_extraction_warning
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

    def save_context(self):
        root = f"qa_projects/{PID}"
        requirements = self.store[f"{root}/snapshots/original"]["payload"]["requirements"]
        self.store[f"{root}/snapshots/context"] = {
            **snapshot([], "context", 7),
            "stage": "context",
            "payload": {"requirements": deepcopy(requirements), "notes": "Project notes"},
        }
        self.store[root]["stage_state"]["context"] = {"current_snapshot_id": "context", "version": 1, "approved": True, "stale": False}

    def test_stage_does_not_publish_and_apply_is_complete_after_reload(self):
        self.save_context()
        before = deepcopy(self.store[f"qa_projects/{PID}"])
        self.assertEqual(self.repo.list_pending(PID, ACTOR)[0].import_id, self.p.import_id)
        self.assertEqual(before["current_revision"], 7)
        project = self.repo.apply(PID, self.p.import_id, ACTOR, choices(self.p), "request")
        self.assertEqual(project.current_revision, 8)
        self.assertEqual(len(project.current_snapshots["requirements"].payload["requirements"]), 10)
        self.assertTrue(project.stage_state["context"].stale)
        self.assertEqual(project.current_snapshots["context"].snapshot_id, "context")
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

    def test_invalid_structural_review_stages_unapproved_draft_without_changing_baseline(self):
        baseline = deepcopy(self.store[f"qa_projects/{PID}"])
        incoming = from_document(requirement(1, "The system shall accept a booking with 1 to 6 attendees.")).model_copy(
            update={"review_status": "Needs Review"}
        )
        response = RequirementsWorkflowResponse(
            source_name="audit-requirements.md",
            raw_text="",
            requirements=[incoming],
            workflow_diagnostics={"status": "partial", "failure_reason": REVIEW_SCHEMA_INVALID},
        )
        staged = RequirementImportPreview.model_validate(
            service.stage_extraction(self.repo, PID, ACTOR, 7, response, "requirements.parse", "retained-draft", {"fingerprint": "draft"}, lambda tx: None)
        )
        self.assertEqual(staged.status, "pending")
        self.assertIn(RETAINED_REQUIREMENT_DRAFT_WARNING, staged.warnings)
        self.assertEqual(staged.candidates[0].requirement.review_status, "Needs Review")
        self.assertEqual(staged.candidates[0].requirement.sources, incoming.sources)
        repeated = service.recompare_import(PID, staged.import_id, ACTOR)
        self.assertIn(RETAINED_REQUIREMENT_DRAFT_WARNING, repeated.warnings)
        self.assertEqual(self.store[f"qa_projects/{PID}"], baseline)

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

    def test_local_historical_recovery_replays_pending_and_requires_new_request_after_cancel(self):
        self.enable_extraction_admission()
        client, _, _ = self.extraction_client()
        project = f"qa_projects/{PID}"
        self.store[f"{project}/snapshots/older"] = snapshot([requirement(1)], "older", 1)
        self.store[f"{project}/snapshots/another"] = snapshot([requirement(2)], "another", 1)
        root = f"/projects/{PID}/requirement-imports"
        body = {"base_project_revision": 7, "baseline_snapshot_id": "older", "incoming_snapshot_id": "original"}
        first_key = {"X-Request-ID": "historical-first"}
        alias_key = {"X-Request-ID": "historical-alias"}
        fresh_key = {"X-Request-ID": "historical-fresh"}

        with patch.object(service, "suggest_matches", side_effect=AssertionError("Local recovery must not contact a model")) as matcher:
            before = deepcopy(self.store)
            self.client.fail_on_create_segment = "/requirement_import_receipts/recovery-request-"
            with self.assertRaisesRegex(RuntimeError, "synthetic create failure"):
                client.post(f"{root}/recovery", json=body, headers=first_key)
            self.client.fail_on_create_segment = None
            self.assertEqual(self.store, before)
            same = client.post(f"{root}/recovery", json={**body, "baseline_snapshot_id": "original"})
            self.assertEqual(same.status_code, 422)

            first = client.post(f"{root}/recovery", json=body, headers=first_key)
            self.assertEqual(first.status_code, 200, first.text)
            first_id = first.json()["import_id"]
            self.assertIn("Compared the saved snapshots without model matching. Review unmatched requirements manually.", first.json()["warnings"])
            self.assertEqual(client.post(f"{root}/recovery", json=body, headers=alias_key).json(), first.json())
            changed = client.post(f"{root}/recovery", json={**body, "baseline_snapshot_id": "another"}, headers=first_key)
            self.assertEqual(changed.status_code, 409)
            with patch.dict(os.environ, {"TCG_APPROVAL_SCENARIOS_ENABLED": "false"}):
                self.assertEqual(client.post(f"{root}/recovery", json=body, headers=first_key).json(), first.json())
            self.assertEqual(client.delete(f"{root}/{first_id}").status_code, 204)
            for key in (first_key, alias_key):
                self.assertEqual(client.post(f"{root}/recovery", json=body, headers=key).status_code, 409)

            with patch.dict(os.environ, {"TCG_APPROVAL_SCENARIOS_ENABLED": "false"}):
                fresh = client.post(f"{root}/recovery", json=body, headers=fresh_key)
            self.assertEqual(fresh.status_code, 200, fresh.text)
            self.assertNotEqual(fresh.json()["import_id"], first_id)
            self.assertEqual(client.post(f"{root}/recovery", json=body, headers=fresh_key).json(), fresh.json())
            pending = [row for row in client.get(root).json() if row["recovery_snapshot_ids"]]
            self.assertEqual(pending, [fresh.json()])
            self.store[project]["current_revision"] = 8
            self.assertEqual(client.post(f"{root}/recovery", json=body, headers=fresh_key).status_code, 409)
        matcher.assert_not_called()
        self.assertEqual(self._rows("billing_consumption"), [])
        self.assertEqual(self._rows("usage_events"), [])

    def test_historical_noop_and_review_only_restore_do_not_reset_the_current_budget(self):
        self.enable_extraction_admission()
        client, _, workflow = self.extraction_client()
        project = f"qa_projects/{PID}"

        def extract(*_args, **_kwargs):
            _model_reservation.get()()
            return workflow

        with patch.object(requirement_routes, "extract_requirements", side_effect=extract):
            staged = self._extract(client, "historical-enrolled-intake")
        self.assertEqual(staged.status_code, 200, staged.text)
        preview = RequirementImportPreview.model_validate(staged.json())
        journey_id = self.repo.read(PID, preview.import_id, ACTOR)["extraction"][journeys.BUDGET_FIELD]
        budget_path = f"{project}/provider_journeys/{journey_id}"
        applied = self.repo.apply(PID, preview.import_id, ACTOR, choices(preview), "history-intake-apply")
        original = applied.current_snapshots["requirements"]
        selected = original.payload["requirements"][-1]

        def review(revision, status, key):
            return self.repo.review(
                PID,
                ACTOR,
                RequirementReviewsInput(
                    base_project_revision=revision,
                    idempotency_key=key,
                    reviews=[{"requirement_uid": selected["requirement_uid"], "review_status": status, "quality_flags": selected["quality_flags"]}],
                ),
                key,
            )

        def recovery(project_detail, key):
            new = project_detail.current_snapshots["requirements"]
            prepared = service.prepare_recovery(
                PID,
                ACTOR,
                ImportRecoveryInput(
                    base_project_revision=project_detail.current_revision,
                    baseline_snapshot_id=original.snapshot_id,
                    incoming_snapshot_id=new.snapshot_id,
                ),
                request_id=key,
            )
            uid_by_text = {row.text: row.requirement_uid for row in prepared.current_requirements}
            decisions = ImportApplyInput(
                base_project_revision=prepared.base_project_revision,
                idempotency_key=f"{key}-apply",
                decisions=[
                    {"candidate_id": row.candidate_id, "action": "keep", "target_requirement_uid": uid_by_text[row.requirement.text]}
                    for row in prepared.candidates
                ],
            )
            return prepared, decisions

        same = review(applied.current_revision, selected["review_status"], "historical-same-review")
        unchanged, keep = recovery(same, "historical-noop")
        path = f"{project}/requirement_imports/{unchanged.import_id}"
        self.store[path]["import_id"] = "different-import"
        with self.assertRaises(HTTPException) as forged:
            self.repo.apply(PID, unchanged.import_id, ACTOR, keep, "historical-noop-apply")
        self.assertEqual(forged.exception.status_code, 409)
        self.store[path]["import_id"] = unchanged.import_id
        before = deepcopy(self.store)
        with self.assertRaises(HTTPException) as refused:
            self.repo.apply(PID, unchanged.import_id, ACTOR, keep, "historical-noop-apply")
        self.assertEqual((refused.exception.status_code, refused.exception.detail["code"]), (409, "recovery_no_changes"))
        self.assertEqual(self.store, before)

        approved = review(same.current_revision, "Approved", "historical-changed-review")
        restored, apply = recovery(approved, "historical-restore-review")
        final = self.repo.apply(PID, restored.import_id, ACTOR, apply, "historical-restore-review-apply")
        snapshot_id = final.current_snapshots["requirements"].snapshot_id
        self.assertEqual(self.store[f"{project}/snapshots/{snapshot_id}"][journeys.BUDGET_FIELD], journey_id)
        self.assertEqual(self.store[budget_path]["requirements_snapshot_id"], snapshot_id)
        self.assertEqual(self.store[budget_path]["requests_reserved"], 16)
        self.assertEqual(final.current_snapshots["requirements"].payload["requirements"][-1]["review_status"], selected["review_status"])
        rebuilt = service.prepare_recovery(
            PID,
            ACTOR,
            ImportRecoveryInput(base_project_revision=final.current_revision, baseline_snapshot_id="original", incoming_snapshot_id=snapshot_id),
            request_id="historical-rebuild-identical-sources",
        )
        known = {row.text: row.requirement_uid for row in rebuilt.current_requirements}
        decisions = ImportApplyInput(
            base_project_revision=rebuilt.base_project_revision,
            idempotency_key="historical-rebuilt-apply",
            decisions=[
                {
                    "candidate_id": row.candidate_id,
                    "action": "keep" if row.requirement.text in known else "add",
                    "target_requirement_uid": known.get(row.requirement.text),
                }
                for row in rebuilt.candidates
            ],
        )
        identical_sources = self.repo.apply(PID, rebuilt.import_id, ACTOR, decisions, "historical-rebuilt-apply")
        rebuilt_id = identical_sources.current_snapshots["requirements"].snapshot_id
        self.assertEqual(self.store[f"{project}/snapshots/{rebuilt_id}"][journeys.BUDGET_FIELD], journey_id)
        self.assertEqual(self.store[budget_path]["requirements_snapshot_id"], rebuilt_id)
        self.assertEqual(self.store[budget_path]["requests_reserved"], 16)
        self.assertEqual(len(self._rows("billing_consumption")), 1)

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

    def test_review_preserves_fresh_matching_context_and_stales_dependent_suite(self):
        root = f"qa_projects/{PID}"
        self.store[f"{root}/snapshots/original"]["payload"]["requirements"][0]["review_status"] = "Needs Review"
        self.store[root]["stage_state"]["requirements"]["approved"] = False
        self.save_context()
        payload = RequirementReviewsInput(
            base_project_revision=7,
            idempotency_key="review-with-context",
            reviews=[{"requirement_uid": self.p.current_requirements[0].requirement_uid, "review_status": "Approved", "quality_flags": ["Ambiguous"]}],
        )
        before = service.get_project(PID, actor=ACTOR)
        result = self.repo.review(PID, ACTOR, payload, "review")
        self.assertTrue(result.stage_state["requirements"].approved)
        self.assertEqual(result.stage_state["context"], before.stage_state["context"])
        self.assertEqual(result.current_snapshots["context"], before.current_snapshots["context"])
        self.assertTrue(result.stage_state["test_cases"].stale)
        self.assertEqual(result.current_snapshots["test_cases"], before.current_snapshots["test_cases"])

    def test_review_does_not_keep_context_with_different_recorded_requirement_text(self):
        self.save_context()
        self.store[f"qa_projects/{PID}/snapshots/context"]["payload"]["requirements"][0]["text"] = "Context was analyzed from older wording."
        payload = RequirementReviewsInput(
            base_project_revision=7,
            idempotency_key="review-with-incompatible-context",
            reviews=[{"requirement_uid": self.p.current_requirements[0].requirement_uid, "review_status": "Approved"}],
        )
        result = self.repo.review(PID, ACTOR, payload, "review")
        self.assertTrue(result.stage_state["context"].stale)
        self.assertEqual(result.current_snapshots["context"].snapshot_id, "context")

    def review_payload(self):
        return RequirementReviewsInput(
            base_project_revision=7,
            idempotency_key="scope-approval",
            reviews=[{"requirement_uid": self.p.current_requirements[0].requirement_uid, "review_status": "Approved"}],
        )

    def review_receipt(self, payload):
        return self.store[f"qa_projects/{PID}/requirement_import_receipts/{service.digest(payload.idempotency_key)}"]

    def test_scope_approval_enqueues_the_canonical_prospective_scope_and_replays_exactly(self):
        root = f"qa_projects/{PID}"
        rows = self.store[f"{root}/snapshots/original"]["payload"]["requirements"]
        rows[0]["review_status"] = "Needs Review"
        rows[1].update(clarification_status="deferred", review_status="Needs Review")
        rows[2].update(clarification_status="excluded", review_status="Rejected")
        self.store[root]["stage_state"]["requirements"]["approved"] = False
        self.save_context()
        payload = self.review_payload()
        options = ContinuationOptions(settings_fingerprint="a" * 64, max_provider_requests=4)

        project = self.repo.review(PID, ACTOR, payload, "approve-and-prepare", continuation=options)
        self.assertFalse(project.stage_state["requirements"].approved)
        snapshot_id = project.current_snapshots["requirements"].snapshot_id
        self.assertEqual(project.stage_state["context"].current_snapshot_id, "context")
        receipt = self.review_receipt(payload)
        self.assertEqual(receipt["fingerprint"], service.digest(["review", payload.model_dump(mode="json")]))
        self.assertEqual(receipt["snapshot_id"], snapshot_id)
        run_id = receipt["continuation_run_id"]
        run = self.store[f"{root}/orchestrator_runs/{run_id}"]
        outbox = self.store[f"{OUTBOX_COLLECTION}/{run_id}"]
        self.assertEqual(outbox["run_id"], run_id)
        self.assertEqual(outbox["project_id"], PID)
        self.assertEqual(run["project_revision"], project.current_revision)
        self.assertEqual(run["actor_user_id"], ACTOR.sub)
        self.assertEqual(run["request_id"], "approve-and-prepare")
        intent = run["continuation"]
        self.assertEqual(intent["stage"], "prepare_scenarios")
        self.assertEqual(intent["approval_id"], snapshot_id)
        self.assertEqual(intent["source_snapshot_ids"], {"requirements": snapshot_id, "context": "context"})
        self.assertEqual(intent["requirement_ids"], ["REQ-001", "REQ-004", "REQ-005", "REQ-006", "REQ-007", "REQ-008"])
        self.assertEqual(intent["options"], options.model_dump(mode="json"))
        self.assertNotIn("continuation_run_id", project.model_dump())

        committed = deepcopy(self.store)
        self.repo.review(PID, ACTOR, payload, "replay", continuation=options)
        self.assertEqual(self.store, committed)
        self.assertEqual(self.client.transactions[-1].operations, [])
        for changed in (None, ContinuationOptions(settings_fingerprint="b" * 64)):
            with self.subTest(changed=changed):
                with self.assertRaises(HTTPException) as error:
                    self.repo.review(PID, ACTOR, payload, "changed-replay", continuation=changed)
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(self.store, committed)

    def test_legacy_scope_approval_does_not_enqueue_or_upgrade_on_replay(self):
        payload = self.review_payload()
        project = self.repo.review(PID, ACTOR, payload, "approve-only")
        self.assertEqual(project.current_revision, 8)
        self.assertNotIn("continuation_options_fingerprint", self.review_receipt(payload))
        committed = deepcopy(self.store)
        with self.assertRaises(HTTPException) as error:
            self.repo.review(PID, ACTOR, payload, "attempt-upgrade", continuation=ContinuationOptions(settings_fingerprint="a" * 64))
        self.assertEqual(error.exception.status_code, 409)
        self.assertFalse(any(path.startswith(f"{OUTBOX_COLLECTION}/") or "/orchestrator_runs/" in path for path in self.store))
        self.assertEqual(self.store, committed)

    def test_scope_approval_records_why_continuation_is_blocked(self):
        root = f"qa_projects/{PID}"
        self.save_context()
        initial_store = deepcopy(self.store)
        payload = self.review_payload()
        options = ContinuationOptions(settings_fingerprint="a" * 64)
        for reason in ("requirements_not_eligible", "requirements_not_current", "context_not_current"):
            with self.subTest(reason=reason):
                self.store.clear()
                self.store.update(deepcopy(initial_store))
                if reason == "requirements_not_eligible":
                    self.store[f"{root}/snapshots/original"]["payload"]["requirements"][1].update(clarification_status="open", review_status="Needs Review")
                elif reason == "requirements_not_current":
                    self.store[root]["stage_state"]["requirements"]["stale"] = True
                else:
                    self.store[f"{root}/snapshots/context"]["payload"]["requirements"][1]["text"] = "Incompatible saved wording"

                project = self.repo.review(PID, ACTOR, payload, "approve", continuation=options)
                self.assertEqual(project.current_revision, 8)
                receipt = self.review_receipt(payload)
                self.assertEqual(receipt["continuation_blocked_reason"], reason)
                self.assertNotIn("continuation_run_id", receipt)
                self.assertFalse(any(path.startswith(f"{OUTBOX_COLLECTION}/") or "/orchestrator_runs/" in path for path in self.store))

    def test_scope_approval_rolls_back_when_continuation_or_receipt_cannot_commit(self):
        payload = self.review_payload()
        options = ContinuationOptions(settings_fingerprint="a" * 64)
        before = deepcopy(self.store)
        for segment in (OUTBOX_COLLECTION, "/requirement_import_receipts/"):
            with self.subTest(segment=segment):
                self.client.fail_on_create_segment = segment
                with self.assertRaisesRegex(RuntimeError, "synthetic create failure"):
                    self.repo.review(PID, ACTOR, payload, "approve", continuation=options)
                self.assertEqual(self.store, before)
        self.client.fail_on_create_segment = None
        project = self.repo.review(PID, ACTOR, payload, "retry", continuation=options)
        run = self.store[f"qa_projects/{PID}/orchestrator_runs/{self.review_receipt(payload)['continuation_run_id']}"]
        self.assertIsNone(run["continuation"]["source_snapshot_ids"]["context"])
        self.assertEqual(run["project_revision"], project.current_revision)

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

    def enable_extraction_admission(self):
        self.client.project = admission.DEMO_PROJECT_ID
        self.client._emulator_host = "127.0.0.1:9090"
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "TCG_APPROVAL_SCENARIOS_ENABLED": "true",
                    "FIREBASE_PROJECT_ID": self.client.project,
                    "FIRESTORE_EMULATOR_HOST": self.client._emulator_host,
                    "ADK_SKILLS_ENABLED": "false",
                    "ADK_MEMORY_ENABLED": "false",
                    "GOOGLE_GENAI_USE_VERTEXAI": "false",
                },
            )
        )
        self.enterContext(patch.object(admission, "get_required_firestore_client", return_value=self.client))
        self.enterContext(patch.object(admission, "transactional", side_effect=_run_transaction))

    def test_demo_extraction_fences_inflight_replays_and_bounds_matching_before_atomic_preview(self):
        self.enable_extraction_admission()
        client, _, workflow = self.extraction_client()
        project = f"qa_projects/{PID}"
        self.store[project]["stage_state"]["use_cases"] = {"current_snapshot_id": "plan"}
        ready, allow_publish = Event(), Event()
        requests = []

        def extract(*_args, **_kwargs):
            reserve = _model_reservation.get()
            for _ in range(admission.REQUIREMENT_EXTRACTION_REQUEST_CAP - 1):
                requests.append(reserve())
            ready.set()
            self.assertTrue(allow_publish.wait(5))
            return workflow

        def match(*_args):
            reserve = _model_reservation.get()
            requests.append(reserve())
            with self.assertRaises(HTTPException) as exhausted:
                reserve()
            self.assertEqual(exhausted.exception.detail["code"], "project_draft_budget_unavailable")
            return {}, True

        stage = publication.stage_extraction

        def lose_commit_response(*args, **kwargs):
            stage(*args, **kwargs)
            raise RuntimeError("Synthetic lost transaction reply")

        status_path = f"/projects/{PID}/requirement-imports/extraction-status"
        with (
            patch.object(requirement_routes, "extract_requirements", side_effect=extract) as model,
            patch.object(service, "suggest_matches", side_effect=match) as matcher,
            patch.object(publication, "stage_extraction", side_effect=lose_commit_response),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            first = pool.submit(self._extract, client, "overlapping-demo-intake")
            try:
                self.assertTrue(ready.wait(5))
                running = client.get(status_path).json()
                attempt = running["attempt"]
                self.assertTrue(running["blocked"])
                self.assertEqual((attempt["state"], attempt["source_snapshot_id"]), ("running", "original"))
                duplicate = self._extract(client, "overlapping-demo-intake")
                self.assertEqual(duplicate.status_code, 409)
                self.assertEqual(duplicate.json()["detail"]["code"], "project_draft_busy")
                self.assertEqual(self._extract(client, "overlapping-demo-intake", content=b"Changed file").status_code, 409)
                with self.assertRaises(HTTPException) as overlapping_review:
                    admission.claim_draft_attempt(
                        self.client,
                        project_id=PID,
                        actor_id=ACTOR.sub,
                        request_id="overlapping-review",
                        operation="test_cases.review",
                        source_snapshot_ids={"requirements": "original", "use_cases": "plan", "test_cases": "tests", "context": None},
                        base_revision=7,
                        max_requests=24,
                        deadline_seconds=60,
                    )
                self.assertEqual(overlapping_review.exception.detail["code"], "project_draft_busy")
                release_path = f"/projects/{PID}/requirement-imports/extraction-attempts/{attempt['operation_id']}/release"
                self.assertEqual(client.post(release_path, json={"fence": attempt["fence"]}).status_code, 409)
            finally:
                allow_publish.set()
            saved = first.result(timeout=5)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(self._extract(client, "overlapping-demo-intake").json(), saved.json())
            self.assertEqual(self._extract(client, "overlapping-demo-intake", content=b"Changed file").status_code, 409)

        model.assert_called_once()
        matcher.assert_called_once()
        self.assertEqual(len(requests), admission.REQUIREMENT_EXTRACTION_REQUEST_CAP)
        self.assertTrue(all(0 < timeout <= 25 for timeout in requests))
        self.assertEqual(client.get(status_path).json(), {"enabled": True, "blocked": False, "attempt": None})
        import_id = saved.json()["import_id"]
        attempt_record = self.store[f"{project}/project_draft_attempts/{attempt['operation_id']}"]
        self.assertEqual(attempt_record["result_import_id"], import_id)
        self.assertEqual(attempt_record["state"], "completed")
        self.assertEqual(attempt_record["provider_requests_reserved"], admission.REQUIREMENT_EXTRACTION_REQUEST_CAP)
        self.assertEqual(self.store[project]["current_revision"], 7)
        [usage] = self._rows("usage_events")
        [consumption] = self._rows("billing_consumption")
        self.assertEqual(consumption["source_event_id"], usage["event_id"])
        self.assertEqual(usage["metadata"]["import_id"], import_id)

    def test_demo_first_file_binds_absent_requirements_and_refuses_held_guidance_before_dispatch(self):
        self.enable_extraction_admission()
        client, _, workflow = self.extraction_client()
        project = f"qa_projects/{PID}"
        self.store[project]["stage_state"].pop("requirements")
        before = deepcopy(self.store)
        with (
            patch.dict(os.environ, {"ADK_MEMORY_ENABLED": "true", "ADK_GUIDANCE_STAGES": "requirements"}),
            patch("app.services.guidance_dependency.prepare_run") as guidance,
            patch.object(requirement_routes, "extract_requirements") as model,
        ):
            refused = self._extract(client, "first-file")
            self.assertEqual(refused.status_code, 409, refused.text)
            guidance.assert_not_called()
            model.assert_not_called()
        self.assertEqual(self.store, before)

        inputs = manifest_from_payload(self.store[project])
        stale_guidance = GuidanceManifest(
            manifest_id="stale", stage="requirements", model="gemini-3.8-flash", project_inputs=inputs.model_copy(update={"revision": inputs.revision + 1})
        )
        with (
            patch("app.services.guidance_dependency.prepare_run", return_value=stale_guidance),
            patch.object(requirement_routes, "extract_requirements") as model,
        ):
            superseded = self._extract(client, "first-file")
            self.assertEqual(superseded.status_code, 409, superseded.text)
            model.assert_not_called()
        self.assertEqual(self.store, before)

        with patch.object(requirement_routes, "extract_requirements", return_value=workflow) as model:
            saved = self._extract(client, "first-file")
        self.assertEqual(saved.status_code, 200, saved.text)
        model.assert_called_once()
        self.assertEqual(saved.json()["current_requirements"], [])
        [attempt] = [row for key, row in self.store.items() if key.startswith(f"{project}/project_draft_attempts/")]
        self.assertEqual(attempt["source_snapshot_ids"], {"requirements": None})
        self.assertEqual(attempt["state"], "completed")
        self.assertEqual(attempt["provider_requests_reserved"], 0)
        self.assertEqual(attempt["result_import_id"], saved.json()["import_id"])
        self.assertEqual(self.store[project], before[project])

    def test_file_journey_links_exact_preview_apply_and_scope_and_retains_uncertain_reservations(self):
        self.enable_extraction_admission()
        self.enterContext(patch.object(continuation_service, "transactional", side_effect=_run_transaction))
        client, _, workflow = self.extraction_client()
        project = f"qa_projects/{PID}"

        def extract(*_args, **_kwargs):
            _model_reservation.get()()
            return workflow

        with patch.object(requirement_routes, "extract_requirements", side_effect=extract) as model:
            saved = self._extract(client, "journey-file")
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(self._extract(client, "journey-file").json(), saved.json())
        model.assert_called_once()
        preview = RequirementImportPreview.model_validate(saved.json())
        preview_record = self.repo.read(PID, preview.import_id, ACTOR)
        journey_id = preview_record["extraction"][journeys.BUDGET_FIELD]
        budget_path = f"{project}/provider_journeys/{journey_id}"
        budget = self.store[budget_path]
        self.assertEqual(budget["request_limit"], 79)
        self.assertEqual(budget["requests_reserved"], 16)
        self.assertEqual(budget["slots"][journeys.INTAKE_STAGE]["result_id"], preview.import_id)
        self.assertTrue(budget["preview_saved"])
        self.assertIsNone(budget["requirements_snapshot_id"])
        self.assertEqual(budget.pop("intake_operation"), journeys.INTAKE_STAGE)

        applied = self.repo.apply(PID, preview.import_id, ACTOR, choices(preview), "journey-apply")
        applied_id = applied.stage_state["requirements"].current_snapshot_id
        self.assertEqual(self.store[budget_path]["requirements_snapshot_id"], applied_id)
        self.assertEqual(self.repo.read(PID, preview.import_id, ACTOR)["applied_snapshot_id"], applied_id)
        scope = RequirementReviewsInput(
            base_project_revision=applied.current_revision,
            idempotency_key="journey-scope",
            reviews=[
                {"requirement_uid": row["requirement_uid"], "review_status": "Approved"}
                for row in applied.current_snapshots["requirements"].payload["requirements"]
            ],
        )
        options = ContinuationOptions(settings_fingerprint="a" * 64, max_provider_requests=7, deadline_seconds=240)
        approved = self.repo.review(PID, ACTOR, scope, "journey-scope", continuation=options)
        approved_id = approved.stage_state["requirements"].current_snapshot_id
        self.assertTrue(approved.stage_state["requirements"].approved)
        self.assertEqual(self.store[budget_path]["requirements_snapshot_id"], approved_id)
        receipt = self.review_receipt(scope)
        run_id = receipt["continuation_run_id"]
        self.assertEqual(self.store[f"{project}/orchestrator_runs/{run_id}"]["continuation"][journeys.BUDGET_FIELD], journey_id)

        claim = continuation_service.claim_stage(self.client, project_id=PID, run_id=run_id)
        self.assertIsNotNone(claim)
        continuation_service.mark_dispatched(self.client, claim, provider_requests=7)
        with self.assertRaises(continuation_service.ContinuationConflict):
            continuation_service.mark_dispatched(self.client, claim, provider_requests=7)
        continuation_service.stop_stage(self.client, claim, reason="scenario_preparation_failed")
        self.assertTrue(continuation_service.cancel_stage(self.client, project_id=PID, run_id=run_id, actor_id=ACTOR.sub))
        self.assertEqual(self.store[budget_path]["requests_reserved"], 23)
        self.assertEqual(self.store[budget_path]["slots"]["prepare_scenarios"]["attempt_id"], run_id)

        repeat = scope.model_copy(update={"base_project_revision": approved.current_revision, "idempotency_key": "journey-scope-again"})
        reviewed = self.repo.review(PID, ACTOR, repeat, "journey-scope-again", continuation=options)
        self.assertTrue(reviewed.stage_state["requirements"].approved)
        self.assertEqual(self.review_receipt(repeat)["continuation_blocked_reason"], journeys.EXHAUSTED)
        self.assertEqual(self.store[budget_path]["requests_reserved"], 23)
        self.assertFalse(any(key.startswith(f"{OUTBOX_COLLECTION}/") for key in self.store))

    def test_enrolled_compare_replays_one_successor_and_preserves_the_intake_budget(self):
        self.enable_extraction_admission()
        self.enterContext(patch.object(continuation_service, "transactional", side_effect=_run_transaction))
        self.repo.cancel(PID, self.p.import_id, ACTOR)
        client, _, workflow = self.extraction_client()
        project = f"qa_projects/{PID}"

        def extract(*_args, **_kwargs):
            _model_reservation.get()()
            return workflow

        with patch.object(requirement_routes, "extract_requirements", side_effect=extract) as extractor:
            initial = self._extract(client, "stale-journey-file")
        extractor.assert_called_once()
        self.assertEqual(initial.status_code, 200, initial.text)
        old = RequirementImportPreview.model_validate(initial.json())
        old_path = f"{project}/requirement_imports/{old.import_id}"
        intake = deepcopy(self.store[old_path]["extraction"])
        budget_path = f"{project}/provider_journeys/{intake[journeys.BUDGET_FIELD]}"
        original_slot = deepcopy(self.store[budget_path]["slots"][journeys.INTAKE_STAGE])
        compare = f"/projects/{PID}/requirement-imports/{old.import_id}/compare"
        root = f"/projects/{PID}/requirement-imports"

        def lose_commit_reply(callback):
            def run(transaction):
                _run_transaction(callback)(transaction)
                raise RuntimeError("Lost comparison reply")

            return run

        with (
            patch.dict(os.environ, {"TCG_APPROVAL_SCENARIOS_ENABLED": "false"}),
            patch.object(service, "suggest_matches", side_effect=AssertionError("Compare must not spend model requests")) as matcher,
        ):
            self.assertEqual(client.post(compare).content, initial.content)
            self.store[old_path]["extraction"].pop(journeys.BUDGET_FIELD)
            missing = client.post(compare)
            self.assertEqual((missing.status_code, missing.json()["detail"]["code"]), (409, journeys.UNAVAILABLE))
            missing = client.post(f"{root}/{old.import_id}/apply", json=choices(old).model_dump(mode="json"))
            self.assertEqual((missing.status_code, missing.json()["detail"]["code"]), (409, journeys.UNAVAILABLE))
            self.store[old_path]["extraction"] = deepcopy(intake)
            self.store[project]["current_revision"] = 8
            self.assertEqual(client.post(f"{root}/{old.import_id}/apply", json=choices(old).model_dump(mode="json")).status_code, 409)
            before = deepcopy(self.store)
            with patch.object(journeys, "advance_comparison", side_effect=RuntimeError("Failure after staging both preview writes")):
                with self.assertRaisesRegex(RuntimeError, "Failure after staging"):
                    client.post(compare)
            self.assertEqual(self.store, before)
            with patch.object(service, "transactional", side_effect=lose_commit_reply):
                with self.assertRaisesRegex(RuntimeError, "Lost comparison reply"):
                    client.post(compare)
            recovered = client.post(compare)
            self.assertEqual(recovered.status_code, 200, recovered.text)
            first = RequirementImportPreview.model_validate(recovered.json())
            self.assertEqual(client.post(compare).content, recovered.content)
            self.assertEqual([row["import_id"] for row in client.get(root).json()], [first.import_id])
            self.assertEqual(client.delete(f"{root}/{old.import_id}").status_code, 409)
            self.assertEqual(self.store[old_path]["extraction"], intake)
            self.assertEqual((first.base_project_revision, first.candidates, first.warnings), (8, old.candidates, old.warnings))

            self.store[project]["current_revision"] = 9
            self.assertEqual(client.post(compare).content, recovered.content)
            repeated = client.post(f"{root}/{first.import_id}/compare")
            self.assertEqual(repeated.status_code, 200, repeated.text)
            latest = RequirementImportPreview.model_validate(repeated.json())
            self.assertEqual([row["import_id"] for row in client.get(root).json()], [latest.import_id])
            self.assertEqual(client.post(compare).status_code, 409)
            self.assertEqual(client.post(f"{root}/{first.import_id}/compare").content, repeated.content)
        matcher.assert_not_called()

        other = AuthUser(sub="other", name="Other")
        with self.assertRaises(HTTPException) as denied:
            service.recompare_import(PID, latest.import_id, other)
        self.assertEqual(denied.exception.status_code, 404)
        self.assertEqual(self.store[budget_path]["slots"], {journeys.INTAKE_STAGE: original_slot})
        self.assertEqual(self.store[budget_path]["requests_reserved"], 16)
        self.assertEqual(len(self._rows("billing_consumption")), 1)
        self.assertEqual(len(self._rows("usage_events")), 1)
        self.assertNotIn(journeys.COMPARISON_FIELD, latest.model_dump(mode="json"))

        applied = self.repo.apply(PID, latest.import_id, ACTOR, choices(latest), "recovered-apply")
        applied_id = applied.stage_state["requirements"].current_snapshot_id
        self.assertEqual(self.store[f"{project}/snapshots/{applied_id}"][journeys.BUDGET_FIELD], intake[journeys.BUDGET_FIELD])
        scope = RequirementReviewsInput(
            base_project_revision=applied.current_revision,
            idempotency_key="recovered-scope",
            reviews=[
                {"requirement_uid": row["requirement_uid"], "review_status": "Approved"}
                for row in applied.current_snapshots["requirements"].payload["requirements"]
            ],
        )
        options = ContinuationOptions(settings_fingerprint="a" * 64, max_provider_requests=7, deadline_seconds=240)
        self.repo.review(PID, ACTOR, scope, "recovered-scope", continuation=options)
        run_id = self.review_receipt(scope)["continuation_run_id"]
        claim = continuation_service.claim_stage(self.client, project_id=PID, run_id=run_id)
        self.assertIsNotNone(claim)
        continuation_service.mark_dispatched(self.client, claim, provider_requests=7)
        self.assertEqual(self.store[budget_path]["requests_reserved"], 23)
        self.assertEqual(self.store[budget_path]["slots"][journeys.INTAKE_STAGE], original_slot)
        self.assertEqual(client.post(f"{root}/{first.import_id}/compare").status_code, 409)

    def test_enrolled_connector_compare_checks_changed_baseline_and_exact_lineage(self):
        self.enable_extraction_admission()
        client, billing, workflow = self.extraction_client()
        project = f"qa_projects/{PID}"

        def extract(**_kwargs):
            _model_reservation.get()()
            return workflow

        with (
            patch.object(integrations_jira, "enforce_billing_access", billing),
            patch.object(integrations_jira, "import_requirements_from_jira", side_effect=extract),
        ):
            initial = client.post(
                "/integrations/jira/import",
                headers={"X-Request-ID": "stale-journey-jira"},
                json={"project_id": PID, "base_project_revision": 7, "import_mode": "review", "epic_key": "QA-13"},
            )
        self.assertEqual(initial.status_code, 200, initial.text)
        old = RequirementImportPreview.model_validate(initial.json())
        root = f"/projects/{PID}/requirement-imports"
        text = "ZIP checksum 1734."
        self.store[f"{project}/snapshots/new-baseline"] = snapshot([requirement(9, text)], "new-baseline", 8)
        self.store[project]["stage_state"]["requirements"]["current_snapshot_id"] = "new-baseline"
        self.store[project]["current_revision"] = 8
        build_comparison = service._compare_saved

        def save_while_comparing(*args, **kwargs):
            result = build_comparison(*args, **kwargs)
            self.store[project]["current_revision"] = 9
            return result

        with patch.object(service, "suggest_matches", side_effect=AssertionError("Compare must not run semantic matching")) as matcher:
            with patch.object(service, "_compare_saved", side_effect=save_while_comparing):
                stale = client.post(f"{root}/{old.import_id}/compare")
                self.assertEqual((stale.status_code, stale.json()["detail"]["code"]), (409, "stale_import"))
            self.assertEqual(self.repo.read(PID, old.import_id, ACTOR)["status"], "pending")
            self.assertFalse(any(row.import_id.startswith("compare_") for row in self.repo.list_pending(PID, ACTOR)))
            compared = client.post(f"{root}/{old.import_id}/compare")
        matcher.assert_not_called()
        self.assertEqual(compared.status_code, 200, compared.text)
        current = RequirementImportPreview.model_validate(compared.json())
        self.assertEqual([row.text for row in current.current_requirements], [text])
        self.assertEqual([row.requirement for row in current.candidates], [row.requirement for row in old.candidates])
        self.assertTrue(all(row.classification == "needs_decision" for row in current.candidates))
        self.assertIn("Compared saved evidence without another model request. Review unmatched requirements manually.", current.warnings)
        path = f"{project}/requirement_imports/{current.import_id}"
        binding = self.store[path].pop(journeys.COMPARISON_FIELD)
        before = deepcopy(self.store)
        self.assertEqual(
            client.post(f"{root}/{current.import_id}/apply", json=choices(current).model_dump(mode="json")).json()["detail"]["code"], journeys.UNAVAILABLE
        )
        self.assertEqual(client.post(f"{root}/{old.import_id}/compare").json()["detail"]["code"], journeys.UNAVAILABLE)
        self.assertEqual(self.store, before)

        self.store[path][journeys.COMPARISON_FIELD] = {**binding, "intake_import_id": "different-intake"}
        self.assertEqual(
            client.post(f"{root}/{current.import_id}/apply", json=choices(current).model_dump(mode="json")).json()["detail"]["code"], journeys.UNAVAILABLE
        )
        self.store[path][journeys.COMPARISON_FIELD] = binding
        for row in (self.store[path]["candidates"][0]["requirement"], self.store[path]["current_requirements"][0]):
            original = row["text"]
            row["text"] = "Fabricated source text"
            refused = client.post(f"{root}/{current.import_id}/apply", json=choices(current).model_dump(mode="json"))
            self.assertEqual((refused.status_code, refused.json()["detail"]["code"]), (409, journeys.UNAVAILABLE))
            row["text"] = original
        self.assertEqual(client.delete(f"{root}/{current.import_id}").status_code, 204)
        self.assertEqual(client.post(f"{root}/{current.import_id}/compare").status_code, 409)
        self.assertEqual(client.post(f"{root}/{old.import_id}/compare").status_code, 409)
        self.assertEqual(len(self._rows("billing_consumption")), 1)

    def test_demo_refinement_blocks_stale_publication_and_requires_exact_interruption_release(self):
        self.enable_extraction_admission()
        client, _, workflow = self.extraction_client()
        project = f"qa_projects/{PID}"
        data = {"feedback": "Clarify requirement", "existing_requirements": "[]"}
        status_path = f"/projects/{PID}/requirement-imports/extraction-status"

        def supersede(*_args, **_kwargs):
            _model_reservation.get()()
            self.store[project]["current_revision"] += 1
            return workflow

        with patch.object(requirement_routes, "refine_requirements", side_effect=supersede) as refine:
            stale = self._extract(client, "superseded-refinement", data=data)
        self.assertEqual(stale.status_code, 409, stale.text)
        refine.assert_called_once()
        self.assertEqual(self._rows("billing_consumption"), [])
        [preview] = self.repo.list_pending(PID, ACTOR)
        self.assertEqual(preview.import_id, self.p.import_id)
        status = client.get(status_path).json()
        attempt = status["attempt"]
        self.assertTrue(status["blocked"])
        self.assertEqual((attempt["operation"], attempt["state"], attempt["reason"]), ("requirements.refine", "interrupted", "provider_outcome_unconfirmed"))
        release = f"/projects/{PID}/requirement-imports/extraction-attempts/{attempt['operation_id']}/release"
        self.assertEqual(client.post(release, json={"fence": attempt["fence"] + 1}).status_code, 409)
        app.dependency_overrides[get_current_user] = lambda: AuthUser(sub="other", name="Other")
        self.assertEqual(client.get(status_path).status_code, 403)
        self.assertEqual(client.post(release, json={"fence": attempt["fence"]}).status_code, 403)
        app.dependency_overrides[get_current_user] = lambda: ACTOR
        self.assertEqual(client.post(release, json={"fence": attempt["fence"]}).json(), {"released": True})
        self.assertFalse(client.get(status_path).json()["blocked"])
        record = self.store[f"{project}/project_draft_attempts/{attempt['operation_id']}"]
        self.assertEqual((record["state"], record["reason"]), ("cancelled", "provider_outcome_acknowledged"))
        with patch.object(requirement_routes, "refine_requirements", return_value=workflow) as no_replay:
            current = {**data, "base_project_revision": "8"}
            self.assertEqual(self._extract(client, "superseded-refinement", data=current).status_code, 409)
        no_replay.assert_not_called()
        with patch.object(requirement_routes, "extract_requirements") as different_operation:
            rejected = self._extract(client, "superseded-refinement", data={"base_project_revision": "8"})
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(rejected.json()["detail"]["code"], "project_draft_request_mismatch")
        different_operation.assert_not_called()

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

    def test_demo_connectors_fence_exact_dispatch_and_link_origin_to_the_intake_budget(self):
        self.enable_extraction_admission()
        client, billing, workflow = self.extraction_client()
        project = f"qa_projects/{PID}"
        cases = (
            ("jira", integrations_jira, "/integrations/jira/import", {"epic_key": "QA-16"}),
            ("azure_devops", integrations_azure_devops, "/integrations/azure-devops/import", {"project": "QA", "work_item_id": 16}),
        )
        original_stage = publication.stage_extraction

        def lose_commit_reply(*args, **kwargs):
            original_stage(*args, **kwargs)
            raise RuntimeError("Synthetic lost preview transaction reply")

        for index, (provider, router, path, selector) in enumerate(cases):
            with self.subTest(provider=provider):
                _, other_router, other_path, other_selector = cases[1 - index]
                revision = self.store[project]["current_revision"]
                options = {"project_id": PID, "base_project_revision": revision, "import_mode": "review"}
                key = f"bounded-{provider}"
                headers = {"X-Request-ID": key}
                body = {**options, **selector}
                ready, allow_publish = Event(), Event()
                requests = []
                clock = datetime.now(timezone.utc)
                result = {**workflow, "requirements": [row.model_copy(update={"source_system": provider}) for row in workflow["requirements"]]}

                def extract(**_kwargs):
                    nonlocal clock
                    # Several authorized connector reads can finish after the ordinary first lease.
                    clock += timedelta(seconds=65)
                    requests.append(_model_reservation.get()())
                    ready.set()
                    self.assertTrue(allow_publish.wait(5))
                    return result

                def match(*_args):
                    requests.append(_model_reservation.get()())
                    return {}, False

                with (
                    patch.object(admission, "_utcnow", side_effect=lambda: clock),
                    patch.object(router, "enforce_billing_access", billing),
                    patch.object(other_router, "enforce_billing_access", billing),
                    patch.object(router, f"import_requirements_from_{provider}", side_effect=extract) as extractor,
                    patch.object(service, "suggest_matches", side_effect=match) as matcher,
                    patch.object(publication, "stage_extraction", side_effect=lose_commit_reply),
                    ThreadPoolExecutor(max_workers=1) as pool,
                ):
                    in_flight = pool.submit(client.post, path, headers=headers, json=body)
                    try:
                        self.assertTrue(ready.wait(5))
                        status = client.get(f"/projects/{PID}/requirement-imports/extraction-status").json()
                        self.assertEqual((status["attempt"]["operation"], status["attempt"]["state"]), (f"requirements.import.{provider}", "running"))
                        duplicate = client.post(path, headers=headers, json=body)
                        self.assertEqual(duplicate.status_code, 409)
                        self.assertEqual(duplicate.json()["detail"]["code"], "project_draft_busy")
                        cross_operation = client.post(other_path, headers=headers, json={**options, **other_selector})
                        self.assertEqual(cross_operation.status_code, 409)
                        self.assertEqual(cross_operation.json()["detail"]["code"], "project_draft_request_mismatch")
                    finally:
                        allow_publish.set()
                    saved = in_flight.result(timeout=5)
                    self.assertEqual(saved.status_code, 200, saved.text)
                    self.assertEqual(client.post(path, headers=headers, json=body).json(), saved.json())
                    changed = client.post(path, headers=headers, json={**body, "include_children": False})
                    self.assertEqual(changed.status_code, 409)
                extractor.assert_called_once()
                matcher.assert_called_once()
                self.assertEqual(len(requests), 2)
                self.assertTrue(all(0 < timeout <= 25 for timeout in requests))
                preview = RequirementImportPreview.model_validate(saved.json())
                self.assertEqual({row.requirement.source_system for row in preview.candidates}, {provider})
                raw = self.repo.read(PID, preview.import_id, ACTOR)
                budget_path = f"{project}/provider_journeys/{raw['extraction'][journeys.BUDGET_FIELD]}"
                budget = self.store[budget_path]
                self.assertEqual((budget["schema_version"], budget["request_limit"], budget["requests_reserved"]), (2, 79, 16))
                self.assertEqual(budget["intake_operation"], f"requirements.import.{provider}")
                self.assertEqual(budget["slots"][journeys.INTAKE_STAGE]["result_id"], preview.import_id)
                decisions = choices(preview).model_copy(update={"idempotency_key": f"apply-{provider}"})
                origin = budget.pop("intake_operation")
                with self.assertRaises(HTTPException) as missing_origin:
                    self.repo.apply(PID, preview.import_id, ACTOR, decisions, key)
                self.assertEqual(missing_origin.exception.detail["code"], journeys.UNAVAILABLE)
                self.store[budget_path]["intake_operation"] = origin
                applied = self.repo.apply(PID, preview.import_id, ACTOR, decisions, key)
                self.assertEqual(self.store[budget_path]["requirements_snapshot_id"], applied.stage_state["requirements"].current_snapshot_id)
                self.assertEqual(len(self._rows("billing_consumption")), index + 1)
                self.assertEqual(len(self._rows("usage_events")), index + 1)

    def test_connector_partial_source_warnings_survive_preview_and_recompare(self):
        warnings = [
            source_extraction_warning("Jira QA-2", {"requirements": []}),
            source_extraction_warning("Jira QA-3", {"requirements": [requirement(9)], "workflow_diagnostics": {"used_fallback": True}}),
        ]
        incoming = RequirementsWorkflowResponse(
            source_name="3 Jira issues",
            raw_text=requirement(9).text,
            requirements=[requirement(9)],
            workflow_diagnostics={"status": "partial", "warnings": ["Provider exception text"]},
        )
        saved = service.stage_extraction(
            self.repo,
            PID,
            ACTOR,
            7,
            incoming,
            "requirements.import.jira",
            "partial-connector",
            {"fingerprint": "partial"},
            lambda _transaction: None,
            source_warnings=warnings,
        )
        self.assertNotIn("Provider exception text", saved["warnings"])
        for warning in warnings:
            self.assertIn(warning, saved["warnings"])
        self.store[f"qa_projects/{PID}"]["current_revision"] += 1
        recomparison = service.recompare_import(PID, "partial-connector", ACTOR)
        self.assertNotIn("Provider exception text", recomparison.warnings)
        for warning in warnings:
            self.assertIn(warning, recomparison.warnings)

    def test_demo_connector_stale_result_retains_unknown_usage_and_exact_owner_release(self):
        self.enable_extraction_admission()
        client, billing, workflow = self.extraction_client()
        project = f"qa_projects/{PID}"
        url = "/integrations/azure-devops/import"
        body = {"project_id": PID, "base_project_revision": 7, "import_mode": "review", "project": "QA", "work_item_id": 16}
        headers = {"X-Request-ID": "interrupted-ado"}

        def supersede(**_kwargs):
            _model_reservation.get()()
            self.store[project]["current_revision"] += 1
            return workflow

        with (
            patch.object(integrations_azure_devops, "enforce_billing_access", billing),
            patch.object(integrations_azure_devops, "import_requirements_from_azure_devops", side_effect=supersede) as extractor,
        ):
            stale = client.post(url, headers=headers, json=body)
        self.assertEqual(stale.status_code, 409, stale.text)
        extractor.assert_called_once()
        self.assertEqual(self._rows("billing_consumption"), [])
        status_path = f"/projects/{PID}/requirement-imports/extraction-status"
        attempt = client.get(status_path).json()["attempt"]
        self.assertEqual((attempt["operation"], attempt["state"]), ("requirements.import.azure_devops", "interrupted"))
        release = f"/projects/{PID}/requirement-imports/extraction-attempts/{attempt['operation_id']}/release"
        self.assertEqual(client.post(release, json={"fence": attempt["fence"]}).json(), {"released": True})
        self.assertIsNone(client.get(status_path).json()["attempt"])
        [budget] = [row for key, row in self.store.items() if key.startswith(f"{project}/provider_journeys/")]
        self.assertFalse(budget["preview_saved"])
        self.assertEqual(budget["requests_reserved"], 16)
        self.assertIsNone(budget["requirements_snapshot_id"])

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
