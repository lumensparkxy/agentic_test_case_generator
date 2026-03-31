from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.artifact_fetcher import fetch_artifact, is_safe_artifact_url


class ArtifactFetcherTests(unittest.TestCase):
    def test_is_safe_artifact_url_allows_public_https(self) -> None:
        allowed, reason = is_safe_artifact_url("https://example.com/docs")
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


if __name__ == "__main__":
    unittest.main()
