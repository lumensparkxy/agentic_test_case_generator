from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.artifact_fetcher import fetch_artifact, is_safe_artifact_url


class ArtifactFetcherTests(unittest.TestCase):
    def test_is_safe_artifact_url_allows_public_https(self) -> None:
        allowed, reason = is_safe_artifact_url("https://93.184.216.34/docs")
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_is_safe_artifact_url_blocks_localhost_and_private_ip(self) -> None:
        self.assertEqual(is_safe_artifact_url("http://localhost:8000/test")[0], False)
        self.assertEqual(is_safe_artifact_url("http://127.0.0.1:8000/test")[0], False)
        self.assertEqual(is_safe_artifact_url("http://192.168.1.10/test")[0], False)

    def test_fetch_artifact_skips_unsafe_url(self) -> None:
        result = fetch_artifact("http://localhost:8000/internal")
        self.assertEqual(result["status"], "Skipped")
        self.assertIn("blocked", result["error"].lower())

    def test_is_safe_artifact_url_blocks_dns_that_resolves_private(self) -> None:
        def resolver(hostname, port, type=None):
            return [(None, None, None, None, ("10.0.0.5", 0))]

        allowed, reason = is_safe_artifact_url("https://internal.example.test/docs", resolver=resolver)

        self.assertFalse(allowed)
        self.assertIn("resolves", reason)

    def test_fetch_artifact_rechecks_redirect_target_safety(self) -> None:
        redirect_error = Exception("unexpected")
        from urllib.error import HTTPError
        from email.message import Message

        headers = Message()
        headers.add_header("Location", "http://127.0.0.1/admin")
        redirect_error = HTTPError(
            "https://93.184.216.34/docs",
            302,
            "Found",
            headers,
            None,
        )

        with patch("app.services.artifact_fetcher._NO_REDIRECT_OPENER.open", Mock(side_effect=redirect_error)):
            result = fetch_artifact("https://93.184.216.34/docs")

        self.assertEqual(result["status"], "Skipped")
        self.assertEqual(result["url"], "http://127.0.0.1/admin")


if __name__ == "__main__":
    unittest.main()
