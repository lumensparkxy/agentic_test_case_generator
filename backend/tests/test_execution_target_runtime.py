"""Opt-in Chromium proof that target binding survives real browser navigation."""

from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import ExecutionSettings
from app.models import TestCase, TestStep
from app.services.execution_service import preview_execution, run_execution


def _case(case_id: str, url: str, *steps: tuple[str, str]) -> TestCase:
    actions = [(f'Open "{url}"', 'exact heading "Directory" is visible'), *steps]
    return TestCase(
        id=case_id,
        title=case_id,
        automation_status="Automated",
        steps=[TestStep(step=index, action=action, expected=expected) for index, (action, expected) in enumerate(actions, 1)],
    )


@unittest.skipUnless(os.getenv("PETF_EXECUTION_TARGET_CHROMIUM_TEST") == "1", "Opt-in real Chromium target-binding proof")
class ExecutionTargetChromiumTests(unittest.TestCase):
    def test_selected_origin_and_escape_evidence(self):
        origins: dict[str, str] = {}
        requests: dict[str, list[tuple[str, str]]] = {"source": [], "selected": []}

        def handler_for(origin_name):
            class Directory(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_GET(self):
                    requests[origin_name].append(("GET", self.path))
                    if self.path == "/network-error":
                        self.close_connection = True
                        return
                    if self.path in ("/redirect", "/same-redirect"):
                        self.send_response(302)
                        self.send_header("Location", f"{origins['source']}/redirect-escape" if self.path == "/redirect" else "/relative?case=inside")
                        self.end_headers()
                        return
                    self.write_html(
                        "<h1>Directory</h1>"
                        f"<a href='{origins['source']}/direct-escape'>Leave target</a>"
                        f"<a href='{origins['source']}/popup-escape' target='_blank'>Open popup</a>"
                        "<form method='post' action='/outside-action'><button>Record action</button></form>"
                    )

                def do_POST(self):
                    requests[origin_name].append(("POST", self.path))
                    self.write_html("<h1>Recorded</h1>")

                def write_html(self, body):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body.encode())

            return Directory

        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="tcg-target-chromium-")))
            for name in requests:
                server = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), handler_for(name)))
                origins[name] = f"http://127.0.0.1:{server.server_port}"
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                stack.callback(thread.join)
                stack.callback(server.shutdown)

            settings = ExecutionSettings(artifact_root=root)
            target, source = origins["selected"], origins["source"]
            allowed = [
                _case("relative", "/relative?case=relative"),
                _case("same_origin", f"{target}/same?case=absolute"),
                _case("rebased", f"{source}/rebased?case=source#detail", (f'URL is exactly "{source}/rebased?case=source#detail"', "")),
                _case("same_redirect", "/same-redirect", (f'URL is exactly "{target}/relative?case=inside"', "")),
            ]
            passed = run_execution(allowed, target_base_url=target, source_app_url=source, settings=settings)
            self.assertEqual(passed.status, "passed", [(item.id, item.status, item.issues, item.stderr) for item in passed.results])
            self.assertEqual(passed.summary.passed, 4)
            self.assertIn(("GET", "/rebased?case=source"), requests["selected"])
            self.assertIn(("GET", "/relative?case=relative"), requests["selected"])
            self.assertIn(("GET", "/same?case=absolute"), requests["selected"])
            self.assertIn(("GET", "/relative?case=inside"), requests["selected"])
            self.assertEqual(requests["source"], [])

            unauthorized = [_case("unapproved", f"{source}/unapproved")]
            preview = preview_execution(unauthorized, target_base_url=target, settings=settings)
            self.assertEqual(preview.unsupported[0].unsupported_steps[0].reason_code, "execution.target_origin_mismatch")
            skipped = run_execution(unauthorized, selected_test_case_ids=["unapproved"], target_base_url=target, settings=settings)
            self.assertEqual(skipped.summary.skipped, 1)
            self.assertEqual(requests["source"], [])

            escape_attempts = [
                _case("redirect", "/redirect", ('Click exact button "Record action"', 'exact heading "Recorded" is visible')),
                _case("direct_link", "/direct", ('Click exact link "Leave target"', 'exact heading "Directory" is visible')),
                _case("popup_link", "/popup", ('Click exact link "Open popup"', 'exact heading "Directory" is visible')),
                _case("network_error", "/network-error"),
            ]
            escaped = run_execution(escape_attempts, target_base_url=target, source_app_url=source, settings=settings)
            self.assertEqual(escaped.summary.invalid, 3, [(item.id, item.status, item.issues, item.stderr) for item in escaped.results])
            self.assertEqual(escaped.summary.failed, 1)
            self.assertEqual(
                {item.id: (item.status, {issue.code for issue in item.issues}) for item in escaped.results},
                {
                    "redirect": ("invalid", {"execution.target_origin_mismatch"}),
                    "direct_link": ("invalid", {"execution.target_origin_mismatch"}),
                    "popup_link": ("invalid", {"execution.target_origin_mismatch"}),
                    "network_error": ("failed", {"playwright.error"}),
                },
            )
            self.assertNotIn(("GET", "/direct-escape"), requests["source"])
            self.assertNotIn(("GET", "/popup-escape"), requests["source"])
            self.assertNotIn(("POST", "/outside-action"), requests["source"])
            self.assertNotIn(("POST", "/outside-action"), requests["selected"])
            print(json.dumps({"origins": origins, "requests": requests, "passed": passed.summary.passed, "invalid": escaped.summary.invalid}))
