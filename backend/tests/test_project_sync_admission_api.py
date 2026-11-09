from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import AuthUser
from app.routers import project_sync as routes
from app.services.workflow_project_service import ProjectPermissionError


_PATH = "/projects/project-1/changes"
_OPERATION_ID = f"draft_{'9' * 64}"


class ProjectSyncAdmissionApiTests(TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(routes.router)
        self.actor = AuthUser(sub="owner-1", name="Project owner")
        app.dependency_overrides[routes.get_current_user] = lambda: self.actor
        self.client = self.enterContext(TestClient(app))

    def test_reads_disabled_status_and_requires_admission_for_release(self):
        with patch.object(routes.admission, "draft_admission_client", return_value=None) as client:
            status = self.client.get(f"{_PATH}/admission")
            self.assertEqual(status.status_code, 200, status.text)
            self.assertEqual(status.json(), {"enabled": False, "blocked": False, "attempt": None})
            client.assert_called_once_with(read_only=True)
            release = self.client.post(f"{_PATH}/admission/{_OPERATION_ID}/release", json={"fence": 1})
            self.assertEqual(release.status_code, 409, release.text)

    def test_reads_exact_attempt_and_releases_only_the_sync_family_and_fence(self):
        firestore = object()
        attempt = {
            "operation_id": _OPERATION_ID,
            "state": "interrupted",
            "fence": 3,
            "stage": "test_cases",
            "change_id": "change-1",
            "request_id": "test-case-refresh",
        }
        release_path = f"{_PATH}/admission/{_OPERATION_ID}/release"
        with (
            patch.object(routes.admission, "draft_admission_client", return_value=firestore),
            patch.object(routes.admission, "read_project_sync_admission", return_value={"blocked": True, "attempt": attempt}) as read,
            patch.object(routes.project_sync_rebind, "sync_rebind_status", return_value=None),
            patch.object(routes.admission, "release_draft_attempt", return_value=True) as release,
        ):
            status = self.client.get(f"{_PATH}/admission")
            self.assertEqual(status.status_code, 200, status.text)
            self.assertEqual(status.json(), {"enabled": True, "blocked": True, "attempt": attempt})
            read.assert_called_once_with(firestore, "project-1", self.actor.sub)
            invalid = self.client.post(release_path, json={"fence": "3"})
            self.assertEqual(invalid.status_code, 422, invalid.text)
            release.assert_not_called()
            result = self.client.post(release_path, json={"fence": 3})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json(), {"released": True})
            release.assert_called_once_with(firestore, "project-1", self.actor.sub, _OPERATION_ID, 3, project_sync=True)
            read.side_effect = ProjectPermissionError("project-1")
            release.side_effect = ProjectPermissionError("project-1")
            self.assertEqual(self.client.get(f"{_PATH}/admission").status_code, 403)
            self.assertEqual(self.client.post(release_path, json={"fence": 3}).status_code, 403)

    def test_resume_passes_explicit_request_identity_and_keeps_header_optional(self):
        sentinel = routes.HTTPException(409, "Simulated busy project")
        with patch.object(routes.project_sync, "resume_sync", side_effect=sentinel) as resume:
            response = self.client.post(f"{_PATH}/resume", json={"change_id": "change-1"}, headers={"X-Request-ID": "sync-retry"})
            self.assertEqual(response.status_code, 409, response.text)
            resume.assert_called_once_with("project-1", self.actor, "change-1", "sync-retry")
            resume.reset_mock()
            response = self.client.post(f"{_PATH}/resume", json={"change_id": "change-1"})
            self.assertEqual(response.status_code, 409, response.text)
            resume.assert_called_once_with("project-1", self.actor, "change-1", None)
