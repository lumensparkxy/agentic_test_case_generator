"""Real loopback transport tests; the upstream is a stub, not a booking app."""

from contextlib import contextmanager
from hashlib import sha256
from http.client import HTTPConnection, RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from response_loss_proxy import ResponseLossProxy


@contextmanager
def upstream_fixture():
    state = {"requests": [], "records": [], "status": 201, "response": {"fixture_id": "FIXTURE-ORIGINAL-001"}, "declared_extra_bytes": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body) + state["declared_extra_bytes"]))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append((self.path, body, self.headers.get("Content-Type")))
            # Deliberately stable stub behavior. The proxy must not implement it.
            if 200 <= state["status"] < 300 and not state["records"]:
                state["records"].append(state["response"].copy())
            self.reply(state["status"], state["response"])

        def do_GET(self):
            self.reply(200, state["records"])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server.server_port, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(port, method, path, body=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class ResponseLossProxyTests(unittest.TestCase):
    def test_complete_response_is_recorded_then_lost_and_retry_reaches_upstream_unchanged(self):
        body = b'{"synthetic_submission":"same-pending-request"}'
        with TemporaryDirectory() as directory, upstream_fixture() as (port, state):
            evidence = Path(directory) / "evidence.json"
            with ResponseLossProxy(port, "/fixture/submit", id_field="fixture_id", evidence_path=evidence) as proxy:
                self.assertEqual(json.loads(request(proxy.port, "GET", "/fixture/records")[1]), [])
                with self.assertRaises(RemoteDisconnected):
                    request(proxy.port, "POST", "/fixture/submit", body)
                recorded = json.loads(evidence.read_text())
                self.assertEqual(state["records"], [{"fixture_id": recorded["original_id"]}])
                self.assertEqual(recorded["original_id"], "FIXTURE-ORIGINAL-001")
                self.assertEqual(recorded["request_sha256"], sha256(body).hexdigest())
                status, retry = request(proxy.port, "POST", "/fixture/submit", body)
                self.assertEqual(status, 201)
                self.assertEqual(json.loads(retry)["fixture_id"], recorded["original_id"])
                self.assertEqual(recorded["response_sha256"], sha256(retry).hexdigest())
                self.assertEqual(state["requests"], [("/fixture/submit", body, "application/json")] * 2)
                self.assertEqual(json.loads(request(proxy.port, "GET", "/fixture/records")[1]), [{"fixture_id": recorded["original_id"]}])
                self.assertEqual(json.loads(evidence.read_text()), recorded)

    def test_unmatched_request_and_upstream_error_do_not_consume_the_one_shot_loss(self):
        with upstream_fixture() as (port, state), ResponseLossProxy(port, "/fixture/submit", id_field="fixture_id") as proxy:
            self.assertEqual(request(proxy.port, "POST", "/different", b"{}")[0], 201)
            state["status"] = 409
            self.assertEqual(request(proxy.port, "POST", "/fixture/submit", b"{}")[0], 409)
            self.assertIsNone(proxy.evidence)
            state["status"] = 201
            with self.assertRaises(RemoteDisconnected):
                request(proxy.port, "POST", "/fixture/submit", b"{}")
            self.assertEqual(request(proxy.port, "POST", "/fixture/submit", b"{}")[0], 201)

    def test_missing_id_and_unwritable_evidence_fail_without_claiming_verified_loss(self):
        with TemporaryDirectory() as directory, upstream_fixture() as (port, state):
            with ResponseLossProxy(port, "/fixture/submit", id_field="missing") as proxy:
                self.assertEqual(request(proxy.port, "POST", "/fixture/submit", b"{}")[0], 502)
                self.assertIsNone(proxy.evidence)
            with ResponseLossProxy(port, "/fixture/submit", id_field="fixture_id", evidence_path=Path(directory) / "absent" / "evidence.json") as proxy:
                self.assertEqual(request(proxy.port, "POST", "/fixture/submit", b"{}")[0], 502)
                self.assertIsNone(proxy.evidence)
            self.assertEqual(len(state["requests"]), 2)

    def test_truncated_upstream_response_cannot_be_recorded_as_complete(self):
        with upstream_fixture() as (port, state), ResponseLossProxy(port, "/fixture/submit", id_field="fixture_id") as proxy:
            state["declared_extra_bytes"] = 10
            self.assertEqual(request(proxy.port, "POST", "/fixture/submit", b"{}")[0], 502)
            self.assertIsNone(proxy.evidence)


if __name__ == "__main__":
    unittest.main()
