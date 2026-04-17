from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.auth.jwt_auth import get_current_user
from app.models import AuthUser


class JwtAuthDependencyTests(unittest.TestCase):
    def test_get_current_user_uses_legacy_token_when_available(self) -> None:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="legacy-token")
        legacy_user = AuthUser(sub="legacy-sub", email="legacy@example.com", name="Legacy User")

        with patch("app.auth.jwt_auth.try_decode_legacy_access_token", return_value=legacy_user) as legacy_decode:
            with patch("app.auth.jwt_auth.verify_firebase_access_token") as firebase_verify:
                current_user = get_current_user(credentials)

        self.assertEqual(current_user, legacy_user)
        legacy_decode.assert_called_once_with("legacy-token")
        firebase_verify.assert_not_called()

    def test_get_current_user_falls_back_to_firebase_verification(self) -> None:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="firebase-token")
        firebase_user = AuthUser(sub="firebase-sub", email="firebase@example.com", name="Firebase User")

        with patch("app.auth.jwt_auth.try_decode_legacy_access_token", return_value=None) as legacy_decode:
            with patch("app.auth.jwt_auth.verify_firebase_access_token", return_value=firebase_user) as firebase_verify:
                current_user = get_current_user(credentials)

        self.assertEqual(current_user, firebase_user)
        legacy_decode.assert_called_once_with("firebase-token")
        firebase_verify.assert_called_once_with("firebase-token")

    def test_get_current_user_requires_bearer_credentials(self) -> None:
        with self.assertRaises(HTTPException) as context:
            get_current_user(None)

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "Missing bearer access token")


if __name__ == "__main__":
    unittest.main()