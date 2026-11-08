#!/usr/bin/env python3
"""Disposable loopback target for the synthetic Room Booking requirements."""

import argparse
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from response_loss_proxy import MAX_BODY_BYTES, ResponseLossProxy

ROOMS = ("Atlas", "Birch")
SLOTS = ("2026-10-01 10:00–11:00 Europe/Zurich", "2026-10-01 11:00–12:00 Europe/Zurich")
ACTORS = ("Alice", "Bob")
SUBMIT_PATH = "/api/bookings"
DEFECTS = ("allow-nine", "allow-room-conflict", "share-room-availability", "deny-slot-release", "allow-non-owner-cancel", "duplicate-submission")
COOKIE = "booking_fixture"


class BookingWorkspace:
    def __init__(self):
        self.actor = None
        self.bookings = {"B-EXISTING": {"id": "B-EXISTING", "room": "Atlas", "slot": SLOTS[0], "attendees": 4, "owner": "Alice", "status": "Confirmed"}}
        self.submissions = {}

    def create(self, body, defect):
        room, slot, count, submission = (body.get(key) for key in ("room", "slot", "attendees", "submission"))
        upper = 9 if defect == "allow-nine" else 8
        digits = count.lstrip("0") if isinstance(count, str) else ""
        if digits not in {str(n) for n in range(1, upper + 1)}:
            return 422, {"error": "Enter a whole number from 1 to 8", "field": "attendees"}
        if room not in ROOMS or slot not in SLOTS or not isinstance(submission, str) or not 1 <= len(submission) <= 80:
            return 422, {"error": "Choose a room and time slot"}
        count = int(digits)
        key = (self.actor, submission)
        identity = self.submissions.get(key)
        replay = self.bookings.get(identity)
        if replay and (replay["room"], replay["slot"], replay["attendees"]) != (room, slot, count):
            return 409, {"error": "Submission already used"}
        if replay and defect != "duplicate-submission":
            return 200, replay.copy()
        occupied = any(
            row["slot"] == slot
            and (row["room"] == room or defect == "share-room-availability")
            and (row["status"] == "Confirmed" or defect == "deny-slot-release")
            for row in self.bookings.values()
        )
        if occupied and defect != "allow-room-conflict" and not replay:
            return 409, {"error": "Room is already booked"}
        identity = f"B-{len(self.bookings):03d}"
        row = {"id": identity, "room": room, "slot": slot, "attendees": count, "owner": self.actor, "status": "Confirmed"}
        self.bookings[identity] = row
        self.submissions[key] = identity
        return 201, row.copy()

    def cancel(self, identity, defect):
        if identity not in self.bookings:
            return 404, {"error": "Booking unavailable"}
        row = self.bookings[identity]
        if row["owner"] != self.actor and defect != "allow-non-owner-cancel":
            return 403, {"error": "Only the creator can cancel this booking"}
        row["status"] = "Cancelled"
        return 200, row.copy()

    def view(self, identity):
        return {
            "actor": self.actor,
            "bookings": [row.copy() for row in self.bookings.values() if row["owner"] == self.actor],
            "detail": self.bookings[identity].copy() if self.actor and identity in self.bookings else None,
        }


@contextmanager
def booking_target(*, port=0, defect=None):
    if defect is not None and defect not in DEFECTS:
        raise ValueError(f"Unknown fixture defect {defect}")
    document = Path(__file__).with_name("fixtures").joinpath("room_booking.html").read_bytes()
    workspaces = {}
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def reply(self, status, data, *, cookie=None):
            content = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8" if isinstance(data, bytes) else "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            if cookie:
                self.send_header("Set-Cookie", f"{COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Strict")
            self.end_headers()
            self.wfile.write(content)

        def session_key(self):
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            return cookies[COOKIE].value if COOKIE in cookies else None

        def do_GET(self):
            url = urlsplit(self.path)
            key = self.session_key()
            if url.path == "/" or url.path.startswith("/bookings/"):
                with lock:
                    new_session = key not in workspaces
                    if new_session:
                        key = uuid4().hex
                        workspaces[key] = BookingWorkspace()
                self.reply(200, document, cookie=key if new_session else None)
                return
            if url.path == "/api/view":
                identity = parse_qs(url.query).get("booking", [None])[0]
                with lock:
                    workspace = workspaces.get(key)
                    view = workspace.view(identity) if workspace else None
                if view is None:
                    self.reply(401, {"error": "Open the fixture first"})
                else:
                    self.reply(200, view)
                return
            self.reply(404, {"error": "Unknown fixture route"})

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_BODY_BYTES:
                    raise ValueError("Invalid body length")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
            except OSError, ValueError:
                self.reply(400, {"error": "Invalid fixture request"})
                return
            with lock:
                status, response = self.apply(workspaces.get(self.session_key()), body)
            self.reply(status, response)

        def apply(self, workspace, body):
            if workspace is None:
                return 401, {"error": "Open the fixture first"}
            if self.path == "/api/actor":
                actor = body.get("actor")
                if actor not in ACTORS:
                    return 400, {"error": "Choose Alice or Bob"}
                workspace.actor = actor
                return 200, {"actor": actor}
            if workspace.actor is None:
                return 401, {"error": "Choose an employee"}
            if self.path == SUBMIT_PATH:
                return workspace.create(body, defect)
            if self.path.startswith(f"{SUBMIT_PATH}/") and self.path.endswith("/cancel"):
                identity = self.path.removeprefix(f"{SUBMIT_PATH}/").removesuffix("/cancel")
                return workspace.cancel(identity, defect)
            return 404, {"error": "Unknown fixture route"}

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--defect", choices=DEFECTS)
    parser.add_argument("--loss-evidence", type=Path, help="Record and drop the first complete successful booking response")
    args = parser.parse_args()
    with booking_target(port=args.port, defect=args.defect) as port:
        if args.loss_evidence is None:
            wait_for_interrupt(port)
        else:
            with ResponseLossProxy(port, SUBMIT_PATH, evidence_path=args.loss_evidence) as proxy:
                wait_for_interrupt(proxy.port)


def wait_for_interrupt(port):
    print(f"http://127.0.0.1:{port}/", flush=True)
    try:
        Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
