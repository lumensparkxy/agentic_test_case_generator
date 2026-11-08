#!/usr/bin/env python3
"""Run the live browser workbench smoke on loopback with a disposable Firestore emulator."""

import argparse
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
from threading import Thread
import time
from urllib.error import URLError
from urllib.request import urlopen

from dotenv import dotenv_values

from browser_benchmark_fixtures import FIXTURES
from response_loss_proxy import ResponseLossProxy
from room_booking_fixture import DEFECTS, SUBMIT_PATH, booking_target


ROOT = Path(__file__).resolve().parents[1]
DEMO_PROJECT = "demo-tcg-workbench"
REVISED_DIRECTORY_EMPTY_COPY = "No employees match your search"
API_BOOTSTRAP = """
import os
import sys

import firebase_admin
from firebase_admin import credentials
from google.auth.credentials import AnonymousCredentials
import uvicorn

if not os.environ["FIRESTORE_EMULATOR_HOST"].startswith("127.0.0.1:") or os.environ["FIREBASE_PROJECT_ID"] != "demo-tcg-workbench":
    raise RuntimeError("The workbench smoke requires its loopback emulator and demo project.")

class EmulatorCredential(credentials.Base):
    def get_credential(self):
        return AnonymousCredentials()

firebase_admin.initialize_app(EmulatorCredential(), {"projectId": "demo-tcg-workbench"})
uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[1]))
"""


def free_ports(count: int) -> list[int]:
    with ExitStack() as stack:
        ports = []
        for _ in range(count):
            listener = stack.enter_context(socket.socket())
            listener.bind(("127.0.0.1", 0))
            ports.append(listener.getsockname()[1])
        return ports


@contextmanager
def local_service(command: list[str], *, cwd: Path, env: dict[str, str], log: Path):
    with log.open("w") as output, subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def wait_for_service(process: subprocess.Popen, address: str, log: Path) -> None:
    deadline = time.monotonic() + 30
    while process.poll() is None and time.monotonic() < deadline:
        try:
            with urlopen(address, timeout=1):
                return
        except URLError, OSError:
            time.sleep(0.2)
    raise RuntimeError(f"Local service did not become ready at {address}. See {log}.")


@contextmanager
def directory_target(fault_marker: Path):
    fixture = FIXTURES["directory"]
    healthy = fixture.documents()[fixture.route]
    broken = fixture.documents(next(defect for defect in fixture.defects if defect.name == "wrong-empty"))[fixture.route]
    revised = healthy.replace('"No employees found"', json.dumps(REVISED_DIRECTORY_EMPTY_COPY))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != fixture.route:
                self.send_error(404)
                return
            try:
                mode = fault_marker.read_text().strip()
            except FileNotFoundError:
                mode = ""
            content = {"wrong-empty": broken, "revised-empty": revised}.get(mode, healthy)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode())

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}{fixture.route}"
        finally:
            server.shutdown()
            thread.join()


def workbench_fixture(stack: ExitStack, output: Path, fixture: str) -> tuple[str, dict[str, str]]:
    if fixture in ("room-booking", "room-booking-change"):
        revision_marker = output / "booking-capacity-six" if fixture == "room-booking-change" else None
        healthy = stack.enter_context(booking_target(revised_attendee_limit_marker=revision_marker))
        loss = stack.enter_context(ResponseLossProxy(healthy, SUBMIT_PATH, evidence_path=output / "booking-response-loss.json"))
        env = {
            "E2E_ROOM_BOOKING_WORKBENCH": "1",
            "E2E_BOOKING_TARGET": f"http://127.0.0.1:{healthy}/",
            "E2E_BOOKING_LOSS_TARGET": f"http://127.0.0.1:{loss.port}/",
        }
        if revision_marker:
            env["E2E_BOOKING_REVISION_MARKER"] = str(revision_marker)
        else:
            env["E2E_BOOKING_DEFECTS"] = json.dumps(
                {name: f"http://127.0.0.1:{stack.enter_context(booking_target(defect=name))}/" for name in DEFECTS if name != "duplicate-submission"}
            )
        return "e2e/live-room-booking-workbench.spec.js", env
    fault_marker = output / "directory-fault"
    target = stack.enter_context(directory_target(fault_marker))
    env = {"E2E_LOCAL_WORKBENCH": "1", "E2E_DIRECTORY_TARGET": target, "E2E_DIRECTORY_FAULT": str(fault_marker)}
    if fixture == "directory-change":
        env["E2E_DIRECTORY_REVISED_EMPTY"] = REVISED_DIRECTORY_EMPTY_COPY
    return "e2e/live-browser-workbench.spec.js", env


def run_workbench(jar: Path, output: Path, api_key: str, *, fixture="directory") -> int:
    firestore_port, api_port, frontend_port = free_ports(3)
    firestore_host = f"127.0.0.1:{firestore_port}"
    api_base = f"http://127.0.0.1:{api_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    jwt_secret = secrets.token_urlsafe(48)
    # Pass only the local runtime environment and the credentials needed by each process.
    local = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "JAVA_HOME", "PLAYWRIGHT_BROWSERS_PATH") if key in os.environ}
    auth = {"AUTH_TOKEN_MODE": "firebase-or-backend-jwt", "JWT_SECRET_KEY": jwt_secret, "JWT_ALGORITHM": "HS256"}
    with ExitStack() as stack:
        spec, target_env = workbench_fixture(stack, output, fixture)
        firestore_log = output / "firestore.log"
        emulator = stack.enter_context(
            local_service(
                [
                    "java",
                    "-jar",
                    str(jar),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(firestore_port),
                    "--project_id",
                    DEMO_PROJECT,
                    "--single_project_mode",
                    "true",
                    "--single_project_mode_error",
                    "true",
                ],
                cwd=output,
                env=local,
                log=firestore_log,
            )
        )
        wait_for_service(emulator, f"http://{firestore_host}/", firestore_log)

        api_log = output / "api.log"
        api = stack.enter_context(
            local_service(
                [sys.executable, "-c", API_BOOTSTRAP, str(api_port)],
                cwd=ROOT,
                env={
                    **local,
                    **auth,
                    "PYTHONPATH": str(ROOT / "backend"),
                    "PYTHON_DOTENV_DISABLED": "1",
                    "GEMINI_API_KEY": api_key,
                    "FIREBASE_PROJECT_ID": DEMO_PROJECT,
                    "FIRESTORE_EMULATOR_HOST": firestore_host,
                    "CORS_ALLOW_ORIGINS": frontend_url,
                    "BILLING_SHADOW_MODE": "true",
                    "EXECUTION_ARTIFACT_ROOT": str(output / "execution-artifacts"),
                    "PETF_PLAYWRIGHT_BROWSER_CHANNEL": "chromium",
                },
                log=api_log,
            )
        )
        wait_for_service(api, f"{api_base}/health", api_log)

        frontend_log = output / "frontend.log"
        frontend = stack.enter_context(
            local_service(
                ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"],
                cwd=ROOT / "frontend",
                env={**local, "VITE_API_BASE": api_base, "VITE_FIREBASE_API_KEY": ""},
                log=frontend_log,
            )
        )
        wait_for_service(frontend, frontend_url, frontend_log)
        print(f"Isolated workbench and browser target ready. Evidence will be saved in {output}.", flush=True)
        return subprocess.run(
            ["npm", "run", "test:e2e", "--", spec, "--workers=1", "--reporter=list", "--output", str(output / "browser")],
            cwd=ROOT / "frontend",
            env={
                **local,
                **auth,
                **target_env,
                "E2E_BASE_URL": frontend_url,
                "E2E_API_BASE": api_base,
                "E2E_DEMO_PROJECT": DEMO_PROJECT,
            },
        ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emulator-jar", type=Path, required=True)
    parser.add_argument("--fixture", choices=("directory", "directory-change", "room-booking", "room-booking-change"), default="directory")
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory outside the checkout.")
    args = parser.parse_args()
    jar, output = args.emulator_jar.resolve(), args.output.resolve()
    if not jar.is_file():
        parser.error("supply an official Firestore emulator JAR")
    if output.exists() or output.is_relative_to(ROOT):
        parser.error("output must be a new directory outside the checkout")
    config = dotenv_values(ROOT / ".env")
    api_key = os.getenv("GEMINI_API_KEY") or config.get("GEMINI_API_KEY") or config.get("GOOGLE_API_KEY")
    if not api_key:
        parser.error("the live smoke requires the configured Gemini API key")
    output.mkdir(parents=True)
    return run_workbench(jar, output, api_key, fixture=args.fixture)


if __name__ == "__main__":
    raise SystemExit(main())
