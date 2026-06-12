from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.auth.authorization import require_org_admin
from app.auth.jwt_auth import create_access_token, decode_access_token, get_current_user
from app.config import AUTH_TOKEN_MODE_FIREBASE_ONLY, AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT
from app.models import AuthUser


def _auth_settings(mode: str = AUTH_TOKEN_MODE_FIREBASE_ONLY) -> SimpleNamespace:
    return SimpleNamespace(
        auth_token_mode=mode,
        jwt_secret_key="unit-test-jwt-secret-with-32-bytes",
        jwt_algorithm="HS256",
        jwt_expiration_minutes=60,
    )


class JwtAuthDependencyTests(unittest.TestCase):
    def test_get_current_user_uses_backend_jwt_when_compatibility_mode_enabled(self) -> None:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="legacy-token")
        legacy_user = AuthUser(sub="legacy-sub", email="legacy@example.com", name="Legacy User")

        with patch("app.auth.jwt_auth.get_auth_settings", return_value=_auth_settings(AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT)):
            with patch("app.auth.jwt_auth.try_decode_legacy_access_token", return_value=legacy_user) as legacy_decode:
                with patch("app.auth.jwt_auth.verify_firebase_access_token") as firebase_verify:
                    current_user = get_current_user(credentials)

        self.assertEqual(current_user, legacy_user)
        legacy_decode.assert_called_once_with("legacy-token")
        firebase_verify.assert_not_called()

    def test_get_current_user_falls_back_to_firebase_verification_in_compatibility_mode(self) -> None:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="firebase-token")
        firebase_user = AuthUser(sub="firebase-sub", email="firebase@example.com", name="Firebase User")

        with patch("app.auth.jwt_auth.get_auth_settings", return_value=_auth_settings(AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT)):
            with patch("app.auth.jwt_auth.try_decode_legacy_access_token", return_value=None) as legacy_decode:
                with patch("app.auth.jwt_auth.verify_firebase_access_token", return_value=firebase_user) as firebase_verify:
                    current_user = get_current_user(credentials)

        self.assertEqual(current_user, firebase_user)
        legacy_decode.assert_called_once_with("firebase-token")
        firebase_verify.assert_called_once_with("firebase-token")

    def test_get_current_user_verifies_firebase_directly_in_firebase_only_mode(self) -> None:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="firebase-token")
        firebase_user = AuthUser(sub="firebase-sub", email="firebase@example.com", name="Firebase User")

        with patch("app.auth.jwt_auth.get_auth_settings", return_value=_auth_settings(AUTH_TOKEN_MODE_FIREBASE_ONLY)):
            with patch("app.auth.jwt_auth.try_decode_legacy_access_token") as legacy_decode:
                with patch("app.auth.jwt_auth.verify_firebase_access_token", return_value=firebase_user) as firebase_verify:
                    current_user = get_current_user(credentials)

        self.assertEqual(current_user, firebase_user)
        legacy_decode.assert_not_called()
        firebase_verify.assert_called_once_with("firebase-token")

    def test_get_current_user_rejects_backend_jwt_in_firebase_only_mode(self) -> None:
        user = AuthUser(sub="legacy-sub", email="legacy@example.com", name="Legacy User")

        with patch("app.auth.jwt_auth.get_auth_settings", return_value=_auth_settings(AUTH_TOKEN_MODE_FIREBASE_ONLY)):
            token, _ = create_access_token(user)
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            with patch("app.auth.jwt_auth.verify_firebase_access_token") as firebase_verify:
                with self.assertRaises(HTTPException) as context:
                    get_current_user(credentials)

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "Backend-issued access tokens are disabled in AUTH_TOKEN_MODE=firebase-only")
        firebase_verify.assert_not_called()

    def test_get_current_user_requires_bearer_credentials(self) -> None:
        with self.assertRaises(HTTPException) as context:
            get_current_user(None)

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "Missing bearer access token")

    def test_decode_access_token_preserves_org_admin_claims(self) -> None:
        user = AuthUser(
            sub="legacy-admin",
            email="admin@acme.com",
            name="Legacy Admin",
            organization_domain="acme.com",
            tenant_id="tenant-acme",
            roles=["tenant_admin"],
            is_org_admin=True,
        )

        with patch("app.auth.jwt_auth.get_auth_settings", return_value=_auth_settings(AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT)):
            token, _ = create_access_token(user)
            decoded_user = decode_access_token(token)

        self.assertEqual(decoded_user.organization_domain, "acme.com")
        self.assertEqual(decoded_user.tenant_id, "tenant-acme")
        self.assertIn("tenant_admin", decoded_user.roles)
        self.assertTrue(decoded_user.is_org_admin)

    def test_require_org_admin_rejects_non_admin_user(self) -> None:
        with self.assertRaises(HTTPException) as context:
            require_org_admin(AuthUser(sub="user-1", email="user@acme.com", name="User"))

        self.assertEqual(context.exception.status_code, 403)

    def test_require_org_admin_accepts_explicit_admin_user(self) -> None:
        current_user = AuthUser(
            sub="admin-1",
            email="admin@acme.com",
            name="Admin",
            organization_domain="acme.com",
            roles=["org_admin"],
            is_org_admin=True,
        )

        self.assertEqual(require_org_admin(current_user), current_user)


if __name__ == "__main__":
    unittest.main()
