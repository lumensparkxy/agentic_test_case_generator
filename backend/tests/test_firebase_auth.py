from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.auth.firebase_auth import verify_firebase_access_token


class FirebaseAuthTests(unittest.TestCase):
    def test_verify_firebase_access_token_maps_org_admin_and_tenant_claims(self) -> None:
        decoded_token = {
            "uid": "firebase-user-1",
            "email": "admin@acme.com",
            "name": "Acme Admin",
            "picture": "https://example.com/avatar.png",
            "email_verified": True,
            "roles": ["tenant_admin", "reporting_admin"],
            "tenant_id": "tenant-acme",
            "organization_domain": "acme.com",
            "org_admin": True,
            "firebase": {"sign_in_provider": "google.com"},
        }

        with patch("app.auth.firebase_auth.get_firebase_admin_app", return_value=object()):
            with patch("app.auth.firebase_auth.firebase_admin_auth.verify_id_token", return_value=decoded_token):
                user = verify_firebase_access_token("firebase-token")

        self.assertEqual(user.sub, "firebase-user-1")
        self.assertEqual(user.organization_domain, "acme.com")
        self.assertEqual(user.tenant_id, "tenant-acme")
        self.assertIn("tenant_admin", user.roles)
        self.assertTrue(user.is_org_admin)


if __name__ == "__main__":
    unittest.main()