from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app


class RequestIdMiddlewareTests(unittest.TestCase):
    def test_health_response_includes_generated_request_id(self) -> None:
        with TestClient(app) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Request-ID", response.headers)
        self.assertTrue(response.headers["X-Request-ID"])

    def test_incoming_request_id_is_preserved(self) -> None:
        with TestClient(app) as client:
            response = client.get("/health", headers={"X-Request-ID": "req-from-test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Request-ID"), "req-from-test")


if __name__ == "__main__":
    unittest.main()