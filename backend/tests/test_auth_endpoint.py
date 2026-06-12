from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app, get_current_user
from app.config import AUTH_TOKEN_MODE_FIREBASE_ONLY, AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT
from app.models import AuthUser


class AuthEndpointTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_google_login_returns_access_token_and_user_in_compatibility_mode(self) -> None:
        user = AuthUser(
            sub="google-user-1",
            email="qa@example.com",
            name="QA User",
            provider="google.com",
            email_verified=True,
        )

        with patch(
            "app.routers.auth.get_auth_settings",
            return_value=SimpleNamespace(
                auth_token_mode=AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT,
                google_client_ids=["web-client-id"],
            ),
        ):
            with patch("app.routers.auth.verify_google_credential", return_value=user.model_dump()) as verify_google:
                with patch("app.routers.auth.create_access_token", return_value=("access-token", 3600)) as create_token:
                    with TestClient(app) as client:
                        response = client.post(
                            "/auth/google/login",
                            json={"credential": "google-id-token", "client_id": "web-client-id"},
                        )

        self.assertEqual(response.status_code, 200)
        verify_google.assert_called_once_with("google-id-token", ["web-client-id"], "web-client-id")
        create_token.assert_called_once_with(user)
        payload = response.json()
        self.assertEqual(payload["access_token"], "access-token")
        self.assertEqual(payload["token_type"], "bearer")
        self.assertEqual(payload["expires_in"], 3600)
        self.assertEqual(payload["user"]["sub"], "google-user-1")

    def test_google_login_is_disabled_in_firebase_only_mode(self) -> None:
        with patch(
            "app.routers.auth.get_auth_settings",
            return_value=SimpleNamespace(
                auth_token_mode=AUTH_TOKEN_MODE_FIREBASE_ONLY,
                google_client_ids=["web-client-id"],
            ),
        ):
            with patch("app.routers.auth.verify_google_credential") as verify_google:
                with patch("app.routers.auth.create_access_token") as create_token:
                    with TestClient(app) as client:
                        response = client.post(
                            "/auth/google/login",
                            json={"credential": "google-id-token", "client_id": "web-client-id"},
                        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "/auth/google/login is disabled unless AUTH_TOKEN_MODE=firebase-or-backend-jwt")
        verify_google.assert_not_called()
        create_token.assert_not_called()

    def test_auth_me_returns_current_user_dependency(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: AuthUser(
            sub="firebase-user-1",
            email="firebase@example.com",
            name="Firebase User",
            provider="google.com",
        )

        with TestClient(app) as client:
            response = client.get("/auth/me")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sub"], "firebase-user-1")

    def test_logout_returns_ok_status(self) -> None:
        with TestClient(app) as client:
            response = client.post("/auth/logout")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
