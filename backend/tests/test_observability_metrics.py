import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app
from app.config import get_metrics_settings
from app.observability.metrics import (
    record_agent_fallback,
    record_audit_dead_letter,
    record_audit_write_failure,
    record_audit_write_retry,
    record_http_request,
    record_integration_request,
    record_workflow_completed,
    record_workflow_started,
    render_prometheus_metrics,
    reset_metrics,
)


class ObservabilityMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        get_metrics_settings.cache_clear()
        reset_metrics()

    def tearDown(self) -> None:
        get_metrics_settings.cache_clear()
        reset_metrics()

    def test_prometheus_renderer_exposes_http_workflow_fallback_and_audit_metrics(self) -> None:
        record_http_request(method="GET", path="/health", status_code=200, duration_seconds=0.125)
        record_workflow_started("run-1", "requirements.parse")
        record_workflow_completed("run-1", "completed")
        record_agent_fallback(workflow="testcases.generate", reason="fallback_generated_artifacts")
        record_audit_write_failure(collection="workflow_runs", operation="workflow_run_start")
        record_audit_write_retry(collection="workflow_runs", operation="workflow_run_start", outcome="scheduled")
        record_audit_dead_letter(collection="workflow_runs", operation="workflow_run_start")
        record_integration_request(provider="jira", operation="search_issues", status="success", duration_seconds=0.25)

        rendered = render_prometheus_metrics()

        self.assertIn("# TYPE http_requests_total counter", rendered)
        self.assertIn('http_requests_total{method="GET",path="/health",status_code="200"} 1', rendered)
        self.assertIn('http_request_duration_seconds_count{method="GET",path="/health",status_code="200"} 1', rendered)
        self.assertIn('workflow_runs_total{operation="requirements.parse",status="started"} 1', rendered)
        self.assertIn('workflow_runs_total{operation="requirements.parse",status="completed"} 1', rendered)
        self.assertIn('workflow_duration_seconds_count{operation="requirements.parse",status="completed"} 1', rendered)
        self.assertIn('agent_fallbacks_total{reason="fallback_generated_artifacts",workflow="testcases.generate"} 1', rendered)
        self.assertIn('audit_write_failures_total{collection="workflow_runs",operation="workflow_run_start"} 1', rendered)
        self.assertIn('audit_write_retries_total{collection="workflow_runs",operation="workflow_run_start",outcome="scheduled"} 1', rendered)
        self.assertIn('audit_dead_letters_total{collection="workflow_runs",operation="workflow_run_start"} 1', rendered)
        self.assertIn('integration_requests_total{operation="search_issues",provider="jira",status="success"} 1', rendered)
        self.assertIn('integration_request_duration_seconds_count{operation="search_issues",provider="jira",status="success"} 1', rendered)
        self.assertIn('integration_request_duration_seconds_sum{operation="search_issues",provider="jira",status="success"} 0.25', rendered)

    def test_metrics_endpoint_exposes_observed_http_request(self) -> None:
        with patch.dict(os.environ, {"METRICS_ENABLED": "true", "METRICS_ACCESS_TOKEN": ""}, clear=False):
            get_metrics_settings.cache_clear()
            with TestClient(app) as client:
                health_response = client.get("/health")
                metrics_response = client.get("/metrics")

        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(metrics_response.status_code, 200)
        self.assertIn("text/plain", metrics_response.headers.get("content-type", ""))
        self.assertIn('http_requests_total{method="GET",path="/health",status_code="200"} 1', metrics_response.text)

    def test_metrics_endpoint_returns_not_found_when_disabled(self) -> None:
        with patch.dict(os.environ, {"METRICS_ENABLED": "false", "METRICS_ACCESS_TOKEN": ""}, clear=False):
            get_metrics_settings.cache_clear()
            with TestClient(app) as client:
                response = client.get("/metrics")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Metrics endpoint is disabled")

    def test_metrics_endpoint_requires_configured_bearer_token(self) -> None:
        with patch.dict(os.environ, {"METRICS_ENABLED": "true", "METRICS_ACCESS_TOKEN": "metrics-secret"}, clear=False):
            get_metrics_settings.cache_clear()
            with TestClient(app) as client:
                missing_response = client.get("/metrics")
                wrong_response = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
                allowed_response = client.get("/metrics", headers={"Authorization": "Bearer metrics-secret"})

        self.assertEqual(missing_response.status_code, 401)
        self.assertEqual(missing_response.headers.get("www-authenticate"), "Bearer")
        self.assertEqual(wrong_response.status_code, 401)
        self.assertEqual(allowed_response.status_code, 200)
        self.assertIn("text/plain", allowed_response.headers.get("content-type", ""))


if __name__ == "__main__":
    unittest.main()
