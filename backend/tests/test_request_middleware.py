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

    def test_request_completion_is_logged_with_request_context(self) -> None:
        with self.assertLogs("app.main", level="INFO") as captured:
            with TestClient(app) as client:
                response = client.get("/health", headers={"X-Request-ID": "req-log-test"})

        self.assertEqual(response.status_code, 200)
        completion_records = [
            record
            for record in captured.records
            if getattr(record, "event", "") == "http.request.completed"
        ]
        self.assertTrue(completion_records)
        record = completion_records[-1]
        self.assertEqual(record.request_id, "req-log-test")
        self.assertEqual(record.method, "GET")
        self.assertEqual(record.path, "/health")
        self.assertEqual(record.status_code, 200)
        self.assertGreaterEqual(record.duration_ms, 0)


if __name__ == "__main__":
    unittest.main()