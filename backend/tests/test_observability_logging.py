from pathlib import Path
import json
import logging
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.observability.logging import JsonLogFormatter, bind_log_context, reset_log_context
from app.adk_client import _log_requirement_workflow
from app.agents.test_case_agent import _log_test_case_workflow


class ObservabilityLoggingTests(unittest.TestCase):
    def test_json_formatter_includes_context_and_structured_extras(self) -> None:
        token = bind_log_context(request_id="req-json-test", path="/health")
        try:
            record = logging.LogRecord(
                name="tcg.test",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg="Hello %s",
                args=("observability",),
                exc_info=None,
            )
            record.event = "unit.test"
            record.status_code = 200

            payload = json.loads(JsonLogFormatter().format(record))
        finally:
            reset_log_context(token)

        self.assertEqual(payload["message"], "Hello observability")
        self.assertEqual(payload["event"], "unit.test")
        self.assertEqual(payload["request_id"], "req-json-test")
        self.assertEqual(payload["path"], "/health")
        self.assertEqual(payload["status_code"], 200)
        self.assertEqual(payload["service_name"], "agentic-test-case-generator-api")

    def test_agent_workflow_logs_include_bound_correlation_context(self) -> None:
        token = bind_log_context(
            request_id="req-agent-test",
            workflow_run_id="run-agent-test",
            actor_user_id="user-agent-test",
            operation="test.operation",
        )
        try:
            with self.assertLogs(level="INFO") as captured:
                _log_requirement_workflow("session_started", session_id="req-session")
                _log_test_case_workflow("session_started", session_id="tc-session")
        finally:
            reset_log_context(token)

        payloads = [json.loads(record.getMessage().split("] ", 1)[1]) for record in captured.records]
        self.assertEqual(payloads[0]["event"], "session_started")
        self.assertEqual(payloads[0]["request_id"], "req-agent-test")
        self.assertEqual(payloads[0]["workflow_run_id"], "run-agent-test")
        self.assertEqual(payloads[0]["actor_user_id"], "user-agent-test")
        self.assertEqual(payloads[0]["operation"], "test.operation")
        self.assertEqual(payloads[1]["request_id"], "req-agent-test")
        self.assertEqual(payloads[1]["workflow_run_id"], "run-agent-test")


if __name__ == "__main__":
    unittest.main()
