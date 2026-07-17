from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app, get_current_user
from app.models import AuthUser, WorkspaceSummaryResponse


GENERATED_AT = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


class WorkspaceSummaryEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current_user = AuthUser(
            sub="workspace-user",
            email="workspace@example.com",
            name="Workspace User",
            provider="google.com",
        )
        app.dependency_overrides[get_current_user] = lambda: self.current_user

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_endpoint_returns_empty_shape_and_forwards_default_arguments(self) -> None:
        service_response = WorkspaceSummaryResponse(generated_at=GENERATED_AT)

        with patch(
            "app.routers.workspace.get_workspace_summary",
            return_value=service_response,
        ) as get_summary:
            with TestClient(app) as client:
                response = client.get("/workspace/summary")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "continue_working": None,
                "projects": [],
                "work_items": [],
                "recent_runs": [],
                "recent_reports": [],
                "generated_at": "2026-07-17T12:00:00Z",
            },
        )
        get_summary.assert_called_once_with(
            actor=self.current_user,
            include_archived=False,
            projects_limit=20,
            work_items_limit=50,
            runs_limit=20,
            reports_limit=20,
        )

    def test_endpoint_forwards_explicit_query_arguments(self) -> None:
        service_response = WorkspaceSummaryResponse(generated_at=GENERATED_AT)

        with patch(
            "app.routers.workspace.get_workspace_summary",
            return_value=service_response,
        ) as get_summary:
            with TestClient(app) as client:
                response = client.get(
                    "/workspace/summary",
                    params={
                        "include_archived": "true",
                        "projects_limit": 3,
                        "work_items_limit": 4,
                        "runs_limit": 5,
                        "reports_limit": 6,
                    },
                )

        self.assertEqual(response.status_code, 200)
        get_summary.assert_called_once_with(
            actor=self.current_user,
            include_archived=True,
            projects_limit=3,
            work_items_limit=4,
            runs_limit=5,
            reports_limit=6,
        )

    def test_endpoint_requires_authentication(self) -> None:
        app.dependency_overrides.clear()

        with patch("app.routers.workspace.get_workspace_summary") as get_summary:
            with TestClient(app) as client:
                response = client.get("/workspace/summary")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Missing bearer access token")
        self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")
        get_summary.assert_not_called()

    def test_endpoint_rejects_limits_outside_supported_range(self) -> None:
        invalid_params = {
            "projects_limit": (0, 51),
            "work_items_limit": (0, 51),
            "runs_limit": (0, 51),
            "reports_limit": (0, 51),
        }

        with patch("app.routers.workspace.get_workspace_summary") as get_summary:
            with TestClient(app) as client:
                for parameter, values in invalid_params.items():
                    for value in values:
                        with self.subTest(parameter=parameter, value=value):
                            response = client.get("/workspace/summary", params={parameter: value})
                            self.assertEqual(response.status_code, 422)

        get_summary.assert_not_called()

    def test_endpoint_translates_service_failure_to_safe_503(self) -> None:
        with patch(
            "app.routers.workspace.get_workspace_summary",
            side_effect=RuntimeError("firestore composite index details must not leak"),
        ):
            with TestClient(app) as client:
                response = client.get("/workspace/summary")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Workspace summary is unavailable"})
        self.assertNotIn("firestore", response.text.lower())


if __name__ == "__main__":
    unittest.main()
