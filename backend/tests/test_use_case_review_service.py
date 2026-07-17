from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from typing import Any, Callable
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app, get_current_user
from app.models import (
    AuthUser,
    OrchestratorStatusResponse,
    QaProjectStageState,
    UseCaseReviewRecord,
    UseCaseReviewRequest,
    UseCaseReviewResponse,
)
from app.services.use_case_review_service import (
    UseCaseReviewConflictError,
    review_use_case_snapshot,
)
from app.services.workflow_project_service import ProjectPermissionError


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
PROJECT_ID = "project-review"
SNAPSHOT_ID = "snapshot-use-cases-v3"
PROJECT_REVISION = 7


class FakeSnapshot:
    def __init__(self, payload: dict[str, Any] | None):
        self._payload = deepcopy(payload)
        self.exists = payload is not None

    def to_dict(self) -> dict[str, Any] | None:
        return deepcopy(self._payload)


class FakeDocument:
    def __init__(self, path: str, store: dict[str, dict[str, Any]]):
        self.path = path
        self.id = path.rsplit("/", 1)[-1]
        self.store = store

    def get(
        self,
        *,
        transaction: Any | None = None,
        field_paths: tuple[str, ...] | None = None,
    ) -> FakeSnapshot:
        del transaction, field_paths
        return FakeSnapshot(self.store.get(self.path))

    def collection(self, name: str) -> "FakeCollection":
        return FakeCollection(f"{self.path}/{name}", self.store)


class FakeCollection:
    def __init__(self, path: str, store: dict[str, dict[str, Any]]):
        self.path = path
        self.store = store

    def document(self, document_id: str) -> FakeDocument:
        return FakeDocument(f"{self.path}/{document_id}", self.store)

    def stream(self):
        prefix = f"{self.path}/"
        for path, payload in list(self.store.items()):
            if not path.startswith(prefix):
                continue
            remainder = path[len(prefix) :]
            if "/" not in remainder:
                yield FakeSnapshot(payload)


class FakeTransaction:
    def __init__(
        self,
        store: dict[str, dict[str, Any]],
        *,
        fail_on_create_segment: str | None = None,
    ):
        self.store = store
        self.operations: list[tuple[str, str]] = []
        self.pending: list[tuple[str, FakeDocument, dict[str, Any]]] = []
        self.fail_on_create_segment = fail_on_create_segment

    def create(self, document: FakeDocument, payload: dict[str, Any]) -> None:
        if self.fail_on_create_segment and self.fail_on_create_segment in document.path:
            raise RuntimeError(f"synthetic create failure: {document.path}")
        if document.path in self.store or any(
            operation == "create" and pending_document.path == document.path for operation, pending_document, _payload in self.pending
        ):
            raise RuntimeError(f"document already exists: {document.path}")
        self.pending.append(("create", document, deepcopy(payload)))
        self.operations.append(("create", document.path))

    def update(self, document: FakeDocument, payload: dict[str, Any]) -> None:
        if document.path not in self.store:
            raise RuntimeError(f"document missing: {document.path}")
        self.pending.append(("update", document, deepcopy(payload)))
        self.operations.append(("update", document.path))

    def commit(self) -> None:
        next_store = deepcopy(self.store)
        for operation, document, payload in self.pending:
            if operation == "create":
                next_store[document.path] = payload
            else:
                next_store[document.path].update(payload)
        self.store.clear()
        self.store.update(next_store)
        self.pending.clear()

    def rollback(self) -> None:
        self.pending.clear()


class FakeFirestoreClient:
    def __init__(self, store: dict[str, dict[str, Any]]):
        self.store = store
        self.transactions: list[FakeTransaction] = []
        self.fail_on_create_segment: str | None = None

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(name, self.store)

    def transaction(self) -> FakeTransaction:
        transaction = FakeTransaction(
            self.store,
            fail_on_create_segment=self.fail_on_create_segment,
        )
        self.transactions.append(transaction)
        return transaction


def _run_transaction(callback: Callable[[FakeTransaction], Any]):
    def run(transaction: FakeTransaction) -> Any:
        try:
            result = callback(transaction)
        except Exception:
            transaction.rollback()
            raise
        transaction.commit()
        return result

    return run


def _snapshot_payload(
    *,
    snapshot_id: str,
    stage: str,
    approved: bool,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "project_id": PROJECT_ID,
        "stage": stage,
        "version": 1,
        "project_revision": PROJECT_REVISION,
        "operation": f"{stage}.save",
        "approved": approved,
        "payload": payload,
        "metadata": {},
        "created_at": NOW,
    }


def _seed_store(*, owner_user_id: str = "reviewer-1") -> dict[str, dict[str, Any]]:
    project_path = f"qa_projects/{PROJECT_ID}"
    return {
        project_path: {
            "project_id": PROJECT_ID,
            "name": "Checkout QA",
            "description": "Review transaction fixture",
            "status": "active",
            "owner_user_id": owner_user_id,
            "current_revision": PROJECT_REVISION,
            "created_at": NOW,
            "updated_at": NOW,
            "stage_state": {
                "requirements": {
                    "current_snapshot_id": "snapshot-requirements-v1",
                    "version": 1,
                    "approved": True,
                    "stale": False,
                    "updated_at": NOW,
                    "metadata": {},
                },
                "use_cases": {
                    "current_snapshot_id": SNAPSHOT_ID,
                    "version": 3,
                    "approved": False,
                    "stale": False,
                    "updated_at": NOW,
                    "operation": "use_cases.save",
                    "metadata": {"coverage_plan_count": 4},
                },
                "impact_analysis": {
                    "version": 2,
                    "approved": False,
                    "stale": True,
                    "stale_reason": "Existing upstream change",
                    "updated_at": NOW,
                    "metadata": {"preserve": "impact"},
                },
                "test_cases": {
                    "version": 5,
                    "approved": True,
                    "stale": False,
                    "updated_at": NOW,
                    "metadata": {"preserve": "test-cases"},
                },
            },
        },
        f"{project_path}/snapshots/snapshot-requirements-v1": _snapshot_payload(
            snapshot_id="snapshot-requirements-v1",
            stage="requirements",
            approved=True,
            payload={
                "requirements": [{"id": "REQ-1", "text": "Checkout"}],
                "review": {"approved": True},
            },
        ),
        f"{project_path}/snapshots/{SNAPSHOT_ID}": _snapshot_payload(
            snapshot_id=SNAPSHOT_ID,
            stage="use_cases",
            approved=False,
            payload={
                "coverage_plan": [{"requirement_id": "REQ-1", "scenarios": []}],
                "review": {"approved": False, "blocking_issues": []},
            },
        ),
    }


class UseCaseReviewContractTests(unittest.TestCase):
    def test_request_changes_requires_a_nonblank_comment(self) -> None:
        for comment in (None, "", "   "):
            with self.subTest(comment=comment), self.assertRaises(ValidationError):
                UseCaseReviewRequest(
                    snapshot_id=SNAPSHOT_ID,
                    base_project_revision=PROJECT_REVISION,
                    decision="request_changes",
                    comment=comment,
                )

    def test_request_normalizes_snapshot_and_comment(self) -> None:
        request = UseCaseReviewRequest(
            snapshot_id=f"  {SNAPSHOT_ID}  ",
            base_project_revision=PROJECT_REVISION,
            decision="request_changes",
            comment="  Add a negative checkout scenario.  ",
        )

        self.assertEqual(request.snapshot_id, SNAPSHOT_ID)
        self.assertEqual(request.comment, "Add a negative checkout scenario.")

    def test_request_rejects_blank_snapshot_negative_revision_and_unknown_decision(self) -> None:
        invalid_payloads = (
            {"snapshot_id": " ", "base_project_revision": PROJECT_REVISION, "decision": "approve"},
            {"snapshot_id": SNAPSHOT_ID, "base_project_revision": -1, "decision": "approve"},
            {"snapshot_id": SNAPSHOT_ID, "base_project_revision": PROJECT_REVISION, "decision": "reject"},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                UseCaseReviewRequest.model_validate(payload)


class UseCaseReviewServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = AuthUser(
            sub="reviewer-1",
            email="reviewer@example.com",
            name="Review Owner",
            provider="google.com",
            roles=["qa-reviewer"],
        )
        self.store = _seed_store()
        self.client = FakeFirestoreClient(self.store)
        self.firestore_client_patch = patch(
            "app.services.use_case_review_service.get_required_firestore_client",
            return_value=self.client,
        )
        self.workflow_collection_patch = patch(
            "app.services.workflow_project_service.get_required_firestore_collection",
            return_value=self.client.collection("qa_projects"),
        )
        self.transactional_patch = patch(
            "app.services.use_case_review_service.transactional",
            side_effect=_run_transaction,
        )
        self.firestore_client_patch.start()
        self.workflow_collection_patch.start()
        self.transactional_patch.start()

    def tearDown(self) -> None:
        self.transactional_patch.stop()
        self.workflow_collection_patch.stop()
        self.firestore_client_patch.stop()

    @property
    def project(self) -> dict[str, Any]:
        return self.store[f"qa_projects/{PROJECT_ID}"]

    def _review(
        self,
        *,
        decision: str = "approve",
        comment: str | None = None,
        snapshot_id: str = SNAPSHOT_ID,
        base_project_revision: int = PROJECT_REVISION,
        actor: AuthUser | None = None,
        request_id: str = "req-review-1",
    ) -> UseCaseReviewResponse:
        return review_use_case_snapshot(
            project_id=PROJECT_ID,
            snapshot_id=snapshot_id,
            base_project_revision=base_project_revision,
            decision=decision,
            comment=comment,
            actor=actor or self.actor,
            request_id=request_id,
        )

    def _paths_containing(self, segment: str) -> list[str]:
        return sorted(path for path in self.store if segment in path)

    def test_approval_commits_review_project_and_timeline_atomically(self) -> None:
        snapshot_path = f"qa_projects/{PROJECT_ID}/snapshots/{SNAPSHOT_ID}"
        snapshot_before = deepcopy(self.store[snapshot_path])
        downstream_before = deepcopy({stage: self.project["stage_state"][stage] for stage in ("impact_analysis", "test_cases")})

        response = self._review()

        self.assertEqual(response.project_revision, PROJECT_REVISION + 1)
        self.assertTrue(response.use_cases_state.approved)
        self.assertEqual(response.review.decision, "approve")
        self.assertEqual(response.review.reviewer_user_id, self.actor.sub)
        self.assertEqual(response.review.reviewer_name, self.actor.name)
        self.assertEqual(response.review.reviewer_email, self.actor.email)
        self.assertEqual(response.review.base_project_revision, PROJECT_REVISION)
        self.assertEqual(response.review.resulting_project_revision, PROJECT_REVISION + 1)
        self.assertEqual(response.review.request_id, "req-review-1")
        self.assertEqual(response.review.idempotency_key, "use_cases.review:req-review-1")
        self.assertEqual(len(response.review.request_fingerprint), 64)
        self.assertNotIn(
            ("approve", "use_cases"),
            {(action.action, action.stage) for action in response.orchestrator_status.next_actions},
        )
        self.assertFalse(any(blocker.code == "unresolved_review" and blocker.source_stage == "use_cases" for blocker in response.orchestrator_status.blockers))

        review_paths = self._paths_containing("/use_case_reviews/")
        timeline_paths = self._paths_containing("/timeline/")
        self.assertEqual(len(review_paths), 1)
        self.assertEqual(len(timeline_paths), 1)
        raw_review = self.store[review_paths[0]]
        raw_timeline = self.store[timeline_paths[0]]
        self.assertEqual(raw_review["reviewer"]["user_id"], self.actor.sub)
        self.assertEqual(raw_review["reviewer"]["email"], self.actor.email)
        self.assertEqual(raw_timeline["event_type"], "use_cases.review_approved")
        self.assertEqual(raw_timeline["metadata"]["review_id"], response.review.review_id)
        self.assertEqual(raw_timeline["actor_user_id"], self.actor.sub)

        latest_review = self.project["stage_state"]["use_cases"]["metadata"]["latest_human_review"]
        self.assertEqual(latest_review["review_id"], response.review.review_id)
        self.assertEqual(latest_review["decision"], "approve")
        self.assertEqual(latest_review["resulting_project_revision"], PROJECT_REVISION + 1)
        self.assertEqual(self.store[snapshot_path], snapshot_before, "reviewing must not mutate the immutable snapshot")
        self.assertEqual(
            {stage: self.project["stage_state"][stage] for stage in downstream_before},
            downstream_before,
            "approval-only review must not mark downstream state stale",
        )

        transaction = self.client.transactions[0]
        self.assertEqual(
            [operation for operation, _path in transaction.operations],
            ["create", "update", "create"],
        )
        self.assertIn("/use_case_reviews/", transaction.operations[0][1])
        self.assertEqual(transaction.operations[1][1], f"qa_projects/{PROJECT_ID}")
        self.assertIn("/timeline/", transaction.operations[2][1])

    def test_request_changes_keeps_stage_unapproved_and_surfaces_reason(self) -> None:
        comment = "Add declined-card and timeout scenarios."

        response = self._review(decision="request_changes", comment=comment)

        self.assertFalse(response.use_cases_state.approved)
        latest_review = response.use_cases_state.metadata["latest_human_review"]
        self.assertEqual(latest_review["decision"], "request_changes")
        self.assertEqual(latest_review["comment"], comment)
        self.assertEqual(
            self.store[self._paths_containing("/timeline/")[0]]["event_type"],
            "use_cases.changes_requested",
        )
        approval_action = next(action for action in response.orchestrator_status.next_actions if action.action == "approve" and action.stage == "use_cases")
        self.assertIn(comment, approval_action.reason)
        review_blockers = [
            blocker for blocker in response.orchestrator_status.blockers if blocker.code == "unresolved_review" and blocker.source_stage == "use_cases"
        ]
        self.assertEqual([blocker.message for blocker in review_blockers], [comment])

    def test_use_case_approval_does_not_suppress_other_stage_machine_blockers(self) -> None:
        project_path = f"qa_projects/{PROJECT_ID}"
        requirements_snapshot = self.store[f"{project_path}/snapshots/snapshot-requirements-v1"]
        requirements_snapshot["payload"]["review"] = {
            "approved": False,
            "blocking_issues": ["Requirements blocker must remain visible"],
        }
        test_cases_snapshot_id = "snapshot-test-cases-v5"
        self.project["stage_state"]["test_cases"]["current_snapshot_id"] = test_cases_snapshot_id
        self.store[f"{project_path}/snapshots/{test_cases_snapshot_id}"] = _snapshot_payload(
            snapshot_id=test_cases_snapshot_id,
            stage="test_cases",
            approved=True,
            payload={
                "test_cases": [],
                "review": {
                    "approved": False,
                    "blocking_issues": ["Test Cases blocker must remain visible"],
                },
            },
        )

        response = self._review()

        unresolved_sources = {blocker.source_stage for blocker in response.orchestrator_status.blockers if blocker.code == "unresolved_review"}
        self.assertEqual(unresolved_sources, {"requirements", "test_cases"})

    def test_stale_revision_fails_before_any_write(self) -> None:
        with self.assertRaises(UseCaseReviewConflictError) as context:
            self._review(base_project_revision=PROJECT_REVISION - 1)

        self.assertEqual(context.exception.latest_revision, PROJECT_REVISION)
        self.assertEqual(context.exception.current_snapshot_id, SNAPSHOT_ID)
        self.assertEqual(self.client.transactions[-1].operations, [])
        self.assertEqual(self.project["current_revision"], PROJECT_REVISION)
        self.assertEqual(self._paths_containing("/use_case_reviews/"), [])
        self.assertEqual(self._paths_containing("/timeline/"), [])

    def test_stale_snapshot_fails_before_any_write(self) -> None:
        with self.assertRaises(UseCaseReviewConflictError) as context:
            self._review(snapshot_id="snapshot-use-cases-v2")

        self.assertEqual(context.exception.latest_revision, PROJECT_REVISION)
        self.assertEqual(context.exception.current_snapshot_id, SNAPSHOT_ID)
        self.assertEqual(self.client.transactions[-1].operations, [])
        self.assertEqual(self.project["current_revision"], PROJECT_REVISION)

    def test_unverifiable_current_snapshot_fails_before_any_write(self) -> None:
        del self.store[f"qa_projects/{PROJECT_ID}/snapshots/{SNAPSHOT_ID}"]

        with self.assertRaises(UseCaseReviewConflictError):
            self._review()

        self.assertEqual(self.client.transactions[-1].operations, [])
        self.assertEqual(self.project["current_revision"], PROJECT_REVISION)

    def test_transaction_failure_does_not_leave_partial_review_state(self) -> None:
        store_before = deepcopy(self.store)
        self.client.fail_on_create_segment = "/timeline/"

        with self.assertRaisesRegex(RuntimeError, "synthetic create failure"):
            self._review()

        self.assertEqual(self.store, store_before)
        self.assertEqual(
            [operation for operation, _path in self.client.transactions[-1].operations],
            ["create", "update"],
        )
        self.assertEqual(self._paths_containing("/use_case_reviews/"), [])
        self.assertEqual(self._paths_containing("/timeline/"), [])

    def test_non_owner_is_denied_before_any_write(self) -> None:
        outsider = AuthUser(sub="reviewer-2", email="other@example.com", name="Other User")

        with self.assertRaises(ProjectPermissionError):
            self._review(actor=outsider)

        self.assertEqual(self.client.transactions[-1].operations, [])
        self.assertEqual(self.project["current_revision"], PROJECT_REVISION)
        self.assertEqual(self._paths_containing("/use_case_reviews/"), [])

    def test_exact_request_replay_is_idempotent_without_duplicate_writes(self) -> None:
        first = self._review(decision="request_changes", comment="Add timeout coverage")
        project_after_first = deepcopy(self.project)
        review_paths_after_first = self._paths_containing("/use_case_reviews/")
        timeline_paths_after_first = self._paths_containing("/timeline/")

        second = self._review(decision="request_changes", comment="Add timeout coverage")

        self.assertEqual(second.review.review_id, first.review.review_id)
        self.assertEqual(second.review.timeline_event_id, first.review.timeline_event_id)
        self.assertEqual(second.review.request_fingerprint, first.review.request_fingerprint)
        self.assertEqual(second.project_revision, PROJECT_REVISION + 1)
        self.assertEqual(self.project, project_after_first)
        self.assertEqual(self._paths_containing("/use_case_reviews/"), review_paths_after_first)
        self.assertEqual(self._paths_containing("/timeline/"), timeline_paths_after_first)
        self.assertEqual(self.client.transactions[-1].operations, [])

    def test_reusing_request_identity_with_changed_payload_conflicts_without_writes(self) -> None:
        self._review(decision="request_changes", comment="Add timeout coverage")
        project_after_first = deepcopy(self.project)

        with self.assertRaises(UseCaseReviewConflictError) as context:
            self._review(decision="request_changes", comment="Add retry coverage")

        self.assertIn("request identity", str(context.exception).lower())
        self.assertEqual(self.client.transactions[-1].operations, [])
        self.assertEqual(self.project, project_after_first)
        self.assertEqual(len(self._paths_containing("/use_case_reviews/")), 1)
        self.assertEqual(len(self._paths_containing("/timeline/")), 1)


def _endpoint_response(actor: AuthUser, *, request_id: str = "req-http-review") -> UseCaseReviewResponse:
    review = UseCaseReviewRecord(
        review_id="usecasereview-endpoint",
        project_id=PROJECT_ID,
        snapshot_id=SNAPSHOT_ID,
        decision="approve",
        reviewer_user_id=actor.sub,
        reviewer_name=actor.name,
        reviewer_email=actor.email,
        request_id=request_id,
        idempotency_key=f"use_cases.review:{request_id}",
        request_fingerprint="a" * 64,
        timeline_event_id="timeline-endpoint",
        base_project_revision=PROJECT_REVISION,
        resulting_project_revision=PROJECT_REVISION + 1,
        decided_at=NOW,
    )
    return UseCaseReviewResponse(
        review=review,
        project_revision=PROJECT_REVISION + 1,
        use_cases_state=QaProjectStageState(
            current_snapshot_id=SNAPSHOT_ID,
            version=3,
            approved=True,
            stale=False,
            updated_at=NOW,
        ),
        orchestrator_status=OrchestratorStatusResponse(
            project_id=PROJECT_ID,
            project_revision=PROJECT_REVISION + 1,
            generated_at=NOW,
        ),
    )


class UseCaseReviewEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = AuthUser(
            sub="reviewer-1",
            email="reviewer@example.com",
            name="Review Owner",
        )
        app.dependency_overrides[get_current_user] = lambda: self.actor

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_endpoint_forwards_authenticated_actor_and_request_identity(self) -> None:
        with patch(
            "app.routers.projects.review_use_case_snapshot",
            return_value=_endpoint_response(self.actor),
        ) as review:
            with TestClient(app) as client:
                response = client.post(
                    f"/projects/{PROJECT_ID}/use-cases/reviews",
                    headers={"X-Request-ID": "req-http-review"},
                    json={
                        "snapshot_id": f"  {SNAPSHOT_ID}  ",
                        "base_project_revision": PROJECT_REVISION,
                        "decision": "approve",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "req-http-review")
        self.assertEqual(response.json()["review"]["decision"], "approve")
        review.assert_called_once_with(
            project_id=PROJECT_ID,
            snapshot_id=SNAPSHOT_ID,
            base_project_revision=PROJECT_REVISION,
            decision="approve",
            comment=None,
            actor=self.actor,
            request_id="req-http-review",
        )

    def test_endpoint_rejects_invalid_review_before_service_call(self) -> None:
        with patch("app.routers.projects.review_use_case_snapshot") as review:
            with TestClient(app) as client:
                response = client.post(
                    f"/projects/{PROJECT_ID}/use-cases/reviews",
                    json={
                        "snapshot_id": SNAPSHOT_ID,
                        "base_project_revision": PROJECT_REVISION,
                        "decision": "request_changes",
                        "comment": "   ",
                    },
                )

        self.assertEqual(response.status_code, 422)
        review.assert_not_called()

    def test_endpoint_maps_stale_review_to_reloadable_conflict(self) -> None:
        conflict = UseCaseReviewConflictError(
            "Reload the current snapshot",
            latest_revision=PROJECT_REVISION + 1,
            current_snapshot_id="snapshot-use-cases-v4",
        )
        with patch("app.routers.projects.review_use_case_snapshot", side_effect=conflict):
            with TestClient(app) as client:
                response = client.post(
                    f"/projects/{PROJECT_ID}/use-cases/reviews",
                    json={
                        "snapshot_id": SNAPSHOT_ID,
                        "base_project_revision": PROJECT_REVISION,
                        "decision": "approve",
                    },
                )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            {
                "message": "Reload the current snapshot",
                "latest_revision": PROJECT_REVISION + 1,
                "current_snapshot_id": "snapshot-use-cases-v4",
                "reload_required": True,
            },
        )

    def test_endpoint_maps_cross_user_denial_and_hides_unexpected_errors(self) -> None:
        cases = (
            (ProjectPermissionError(PROJECT_ID), 403, "Project access denied"),
            (RuntimeError("firestore credential details"), 503, "Use Cases review persistence is unavailable"),
        )
        for error, expected_status, expected_detail in cases:
            with self.subTest(expected_status=expected_status):
                with patch("app.routers.projects.review_use_case_snapshot", side_effect=error):
                    with TestClient(app) as client:
                        response = client.post(
                            f"/projects/{PROJECT_ID}/use-cases/reviews",
                            json={
                                "snapshot_id": SNAPSHOT_ID,
                                "base_project_revision": PROJECT_REVISION,
                                "decision": "approve",
                            },
                        )

                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json(), {"detail": expected_detail})
                self.assertNotIn("credential", response.text.lower())


if __name__ == "__main__":
    unittest.main()
