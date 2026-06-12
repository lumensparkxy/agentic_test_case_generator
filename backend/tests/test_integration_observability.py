from pathlib import Path
import io
import json
import logging
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.observability.integrations import observe_integration_request
from app.observability.logging import JsonLogFormatter, bind_log_context, reset_log_context
from app.observability.metrics import render_prometheus_metrics, reset_metrics


class IntegrationObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_metrics()

    def tearDown(self) -> None:
        reset_metrics()

    def _capture_json_logs(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLogFormatter())
        logger = logging.getLogger("app.observability.integrations")
        previous_level = logger.level
        previous_propagate = logger.propagate
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        return logger, handler, stream, previous_level, previous_propagate

    def test_success_log_includes_bound_request_context_and_safe_labels(self) -> None:
        logger, handler, stream, previous_level, previous_propagate = self._capture_json_logs()
        token = bind_log_context(
            request_id="req-integration-1",
            workflow_run_id="run-integration-1",
            actor_user_id="user-integration-1",
            operation="integrations.jira.import",
        )
        try:
            with observe_integration_request(provider="jira", operation="search_issues"):
                pass
        finally:
            reset_log_context(token)
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate

        payload = json.loads(stream.getvalue().splitlines()[0])
        rendered = render_prometheus_metrics()

        self.assertEqual(payload["event"], "integration.request.completed")
        self.assertEqual(payload["request_id"], "req-integration-1")
        self.assertEqual(payload["workflow_run_id"], "run-integration-1")
        self.assertEqual(payload["actor_user_id"], "user-integration-1")
        self.assertEqual(payload["operation"], "integrations.jira.import")
        self.assertEqual(payload["provider"], "jira")
        self.assertEqual(payload["integration_operation"], "search_issues")
        self.assertEqual(payload["integration_status"], "success")
        self.assertIn('integration_requests_total{operation="search_issues",provider="jira",status="success"} 1', rendered)

    def test_failure_log_omits_exception_message_and_records_failure_metric(self) -> None:
        logger, handler, stream, previous_level, previous_propagate = self._capture_json_logs()
        try:
            with self.assertRaises(RuntimeError):
                with observe_integration_request(provider="jira", operation="validate_connection"):
                    raise RuntimeError("token secret leaked at https://acme.example")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate

        payload = json.loads(stream.getvalue().splitlines()[0])
        rendered = render_prometheus_metrics()
        serialized_payload = json.dumps(payload)

        self.assertEqual(payload["event"], "integration.request.failed")
        self.assertEqual(payload["provider"], "jira")
        self.assertEqual(payload["integration_operation"], "validate_connection")
        self.assertEqual(payload["integration_status"], "failure")
        self.assertEqual(payload["error_type"], "RuntimeError")
        self.assertNotIn("token secret", serialized_payload)
        self.assertNotIn("https://acme.example", serialized_payload)
        self.assertIn('integration_requests_total{operation="validate_connection",provider="jira",status="failure"} 1', rendered)


if __name__ == "__main__":
    unittest.main()
