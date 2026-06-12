from pathlib import Path
from email.message import Message
import socket
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.artifact_fetcher import fetch_artifact, is_safe_artifact_url


class FakeResponse:
    def __init__(self, payload: bytes, content_type: str | None) -> None:
        self.payload = payload
        self.headers = Message()
        if content_type:
            self.headers.add_header("Content-Type", content_type)

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self.payload
        return self.payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class ArtifactFetcherTests(unittest.TestCase):
    def test_is_safe_artifact_url_allows_public_https(self) -> None:
        allowed, reason = is_safe_artifact_url("https://93.184.216.34/docs")
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_is_safe_artifact_url_blocks_localhost_and_private_ip(self) -> None:
        self.assertEqual(is_safe_artifact_url("http://localhost:8000/test")[0], False)
        self.assertEqual(is_safe_artifact_url("http://127.0.0.1:8000/test")[0], False)
        self.assertEqual(is_safe_artifact_url("http://192.168.1.10/test")[0], False)
        self.assertEqual(is_safe_artifact_url("http://[fd00::1]/test")[0], False)

    def test_is_safe_artifact_url_blocks_embedded_credentials(self) -> None:
        allowed, reason = is_safe_artifact_url("https://user:password@93.184.216.34/docs")

        self.assertFalse(allowed)
        self.assertIn("credentials", reason)

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

    def test_is_safe_artifact_url_blocks_mixed_public_and_private_dns_answers(self) -> None:
        def resolver(hostname, port, type=None):
            return [
                (socket.AF_INET, None, None, None, ("93.184.216.34", 0)),
                (socket.AF_INET6, None, None, None, ("fd00::10", 0, 0, 0)),
            ]

        allowed, reason = is_safe_artifact_url("https://mixed.example.test/docs", resolver=resolver)

        self.assertFalse(allowed)
        self.assertIn("resolves", reason)

    def test_is_safe_artifact_url_reports_dns_resolution_failures(self) -> None:
        def failing_resolver(hostname, port, type=None):
            raise socket.gaierror("name not found")

        allowed, reason = is_safe_artifact_url("https://missing.example.test/docs", resolver=failing_resolver)

        self.assertFalse(allowed)
        self.assertIn("Could not resolve", reason)

    def test_is_safe_artifact_url_reports_empty_dns_results(self) -> None:
        def empty_resolver(hostname, port, type=None):
            return []

        allowed, reason = is_safe_artifact_url("https://empty.example.test/docs", resolver=empty_resolver)

        self.assertFalse(allowed)
        self.assertIn("IP address", reason)

    def test_fetch_artifact_rechecks_redirect_target_safety(self) -> None:
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

    def test_fetch_artifact_rejects_unsupported_content_type(self) -> None:
        response = FakeResponse(b"%PDF-1.4", "application/pdf")

        with patch("app.services.artifact_fetcher._NO_REDIRECT_OPENER.open", Mock(return_value=response)):
            result = fetch_artifact("https://93.184.216.34/file.pdf")

        self.assertEqual(result["status"], "Skipped")
        self.assertEqual(result["content_type"], "application/pdf")
        self.assertIsNone(result["text"])
        self.assertIn("Unsupported", result["error"])

    def test_fetch_artifact_blocks_oversized_supported_response(self) -> None:
        response = FakeResponse(b"12345", "text/plain")

        with patch("app.services.artifact_fetcher._NO_REDIRECT_OPENER.open", Mock(return_value=response)):
            result = fetch_artifact("https://93.184.216.34/large.txt", max_bytes=4)

        self.assertEqual(result["status"], "Unavailable")
        self.assertIsNone(result["text"])
        self.assertIn("byte size limit", result["error"])

    def test_fetch_artifact_returns_timeout_warning_without_raw_exception(self) -> None:
        with patch("app.services.artifact_fetcher._NO_REDIRECT_OPENER.open", Mock(side_effect=TimeoutError("secret timeout detail"))):
            result = fetch_artifact("https://93.184.216.34/slow")

        self.assertEqual(result["status"], "Unavailable")
        self.assertEqual(result["error"], "Artifact fetch timed out.")
        self.assertNotIn("secret timeout detail", result["error"])

    def test_fetch_artifact_normalizes_urlerror_timeout(self) -> None:
        with patch("app.services.artifact_fetcher._NO_REDIRECT_OPENER.open", Mock(side_effect=URLError(TimeoutError("socket detail")))):
            result = fetch_artifact("https://93.184.216.34/slow")

        self.assertEqual(result["status"], "Unavailable")
        self.assertEqual(result["error"], "Artifact fetch timed out.")


if __name__ == "__main__":
    unittest.main()
