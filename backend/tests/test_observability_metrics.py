from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app
from app.observability.metrics import (
    record_agent_fallback,
    record_audit_dead_letter,
    record_audit_write_failure,
    record_audit_write_retry,
    record_http_request,
    record_workflow_completed,
    record_workflow_started,
    render_prometheus_metrics,
    reset_metrics,
)


class ObservabilityMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_metrics()

    def tearDown(self) -> None:
        reset_metrics()

    def test_prometheus_renderer_exposes_http_workflow_fallback_and_audit_metrics(self) -> None:
        record_http_request(method="GET", path="/health", status_code=200, duration_seconds=0.125)
        record_workflow_started("run-1", "requirements.parse")
        record_workflow_completed("run-1", "completed")
        record_agent_fallback(workflow="testcases.generate", reason="fallback_generated_artifacts")
        record_audit_write_failure(collection="workflow_runs", operation="workflow_run_start")
        record_audit_write_retry(collection="workflow_runs", operation="workflow_run_start", outcome="scheduled")
        record_audit_dead_letter(collection="workflow_runs", operation="workflow_run_start")

        rendered = render_prometheus_metrics()

        self.assertIn('# TYPE http_requests_total counter', rendered)
        self.assertIn('http_requests_total{method="GET",path="/health",status_code="200"} 1', rendered)
        self.assertIn('http_request_duration_seconds_count{method="GET",path="/health",status_code="200"} 1', rendered)
        self.assertIn('workflow_runs_total{operation="requirements.parse",status="started"} 1', rendered)
        self.assertIn('workflow_runs_total{operation="requirements.parse",status="completed"} 1', rendered)
        self.assertIn('workflow_duration_seconds_count{operation="requirements.parse",status="completed"} 1', rendered)
        self.assertIn('agent_fallbacks_total{reason="fallback_generated_artifacts",workflow="testcases.generate"} 1', rendered)
        self.assertIn('audit_write_failures_total{collection="workflow_runs",operation="workflow_run_start"} 1', rendered)
        self.assertIn('audit_write_retries_total{collection="workflow_runs",operation="workflow_run_start",outcome="scheduled"} 1', rendered)
        self.assertIn('audit_dead_letters_total{collection="workflow_runs",operation="workflow_run_start"} 1', rendered)

    def test_metrics_endpoint_exposes_observed_http_request(self) -> None:
        with TestClient(app) as client:
            health_response = client.get("/health")
            metrics_response = client.get("/metrics")

        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(metrics_response.status_code, 200)
        self.assertIn("text/plain", metrics_response.headers.get("content-type", ""))
        self.assertIn('http_requests_total{method="GET",path="/health",status_code="200"} 1', metrics_response.text)


if __name__ == "__main__":
    unittest.main()
