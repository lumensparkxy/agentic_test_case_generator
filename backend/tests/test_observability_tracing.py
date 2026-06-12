from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app
from app.models import AuthUser
from app.observability.logging import bind_log_context, reset_log_context
from app.observability.tracing import configure_tracing, extract_trace_id_from_traceparent, get_current_trace_id
from app.services.audit_service import record_usage_event, start_workflow_run


VALID_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
VALID_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


class ObservabilityTracingTests(unittest.TestCase):
    def test_extract_trace_id_from_valid_traceparent(self) -> None:
        self.assertEqual(extract_trace_id_from_traceparent(VALID_TRACEPARENT), VALID_TRACE_ID)

    def test_extract_trace_id_rejects_invalid_traceparent(self) -> None:
        self.assertIsNone(extract_trace_id_from_traceparent("not-a-traceparent"))
        self.assertIsNone(extract_trace_id_from_traceparent("00-00000000000000000000000000000000-00f067aa0ba902b7-01"))
        self.assertIsNone(extract_trace_id_from_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01"))

    def test_configure_tracing_is_disabled_by_default_without_optional_imports(self) -> None:
        with patch.dict("os.environ", {"OTEL_ENABLED": "false"}, clear=False):
            result = configure_tracing(app)

        self.assertFalse(result.enabled)
        self.assertFalse(result.instrumented)
        self.assertEqual(result.reason, "disabled")

    def test_get_current_trace_id_prefers_bound_log_context(self) -> None:
        token = bind_log_context(trace_id=VALID_TRACE_ID)
        try:
            self.assertEqual(get_current_trace_id(), VALID_TRACE_ID)
        finally:
            reset_log_context(token)

    def test_request_middleware_exposes_trace_id_from_traceparent(self) -> None:
        with self.assertLogs("app.main", level="INFO") as captured:
            with TestClient(app) as client:
                response = client.get("/health", headers={"traceparent": VALID_TRACEPARENT})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Trace-ID"), VALID_TRACE_ID)
        completion_records = [record for record in captured.records if getattr(record, "event", "") == "http.request.completed"]
        self.assertTrue(completion_records)
        self.assertEqual(completion_records[-1].trace_id, VALID_TRACE_ID)

    def test_audit_payloads_include_bound_trace_id(self) -> None:
        collection = MagicMock()
        workflow_document = MagicMock()
        usage_document = MagicMock()
        collection.document.side_effect = [workflow_document, usage_document]
        user = AuthUser(sub="user-trace", email="trace@example.com", name="Trace User")
        token = bind_log_context(trace_id=VALID_TRACE_ID)
        try:
            with patch("app.services.audit_service.get_firestore_client") as get_client:
                get_client.return_value.collection.return_value = collection
                run_id = start_workflow_run(
                    operation="requirements.parse",
                    actor=user,
                    request_id="req-trace",
                )
                record_usage_event(
                    event_type="requirements.parsed",
                    billing_key="requirements.parse",
                    quantity=1,
                    unit="requirement",
                    actor=user,
                    request_id="req-trace",
                    workflow_run_id=run_id,
                    status="completed",
                )
        finally:
            reset_log_context(token)

        workflow_payload = workflow_document.set.call_args[0][0]
        usage_payload = usage_document.set.call_args[0][0]
        self.assertEqual(workflow_payload["trace_id"], VALID_TRACE_ID)
        self.assertEqual(usage_payload["trace_id"], VALID_TRACE_ID)


if __name__ == "__main__":
    unittest.main()
