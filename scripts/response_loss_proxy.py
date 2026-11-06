#!/usr/bin/env python3
"""One-shot response loss for small, synthetic HTTP fixtures on loopback only."""

import argparse
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
from threading import Event, Lock, Thread


MAX_BODY_BYTES = 1_048_576
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}


class ResponseLossProxy:
    """Forward GET/POST unchanged; suppress one complete successful POST response.

    The caller supplies the submission path and top-level JSON ID field. This
    tool does not generate IDs, retry requests, or implement deduplication.
    Construct a new instance to arm a new isolated test.
    """

    def __init__(self, upstream_port, submission_path, *, id_field="id", port=0, evidence_path=None):
        if not 0 < upstream_port < 65536 or not 0 <= port < 65536:
            raise ValueError("Expected loopback TCP ports")
        if not submission_path.startswith("/") or submission_path.startswith("//") or "#" in submission_path:
            raise ValueError("Expected an origin-form submission path")
        if not id_field:
            raise ValueError("Expected a JSON ID field")
        self.upstream_port = upstream_port
        self.submission_path = submission_path
        self.id_field = id_field
        self.evidence_path = Path(evidence_path) if evidence_path else None
        self.evidence = None
        self._lock = Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # Never log fixture bodies, headers or credentials.

            def do_GET(self):
                self.forward()

            def do_POST(self):
                self.forward()

            def forward(self):
                self.connection.settimeout(5)
                if not self.path.startswith("/") or self.path.startswith("//") or self.headers.get("Transfer-Encoding"):
                    self.send_error(400, "Only origin-form, length-delimited fixture requests are supported")
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= size <= MAX_BODY_BYTES:
                        raise ValueError("Request size")
                    body = self.rfile.read(size)
                    if len(body) != size:
                        raise ValueError("Truncated request")
                    excluded = HOP_HEADERS | {"host", "content-length"} | {name.strip().lower() for name in self.headers.get("Connection", "").split(",")}
                    headers = {key: value for key, value in self.headers.items() if key.lower() not in excluded}
                    connection = HTTPConnection("127.0.0.1", proxy.upstream_port, timeout=5)
                    try:
                        connection.request(self.command, self.path, body=body, headers=headers)
                        upstream = connection.getresponse()
                        response_body = upstream.read(MAX_BODY_BYTES + 1)
                        if len(response_body) > MAX_BODY_BYTES:
                            raise ValueError("Response size")
                        if upstream.length not in (None, 0):
                            raise ValueError("Truncated upstream response")
                        response_headers = upstream.getheaders()
                        status = upstream.status
                    finally:
                        connection.close()
                    matching_success = self.command == "POST" and self.path == proxy.submission_path and 200 <= status < 300
                    with proxy._lock:
                        if matching_success and proxy.evidence is None:
                            payload = json.loads(response_body)
                            identity = payload.get(proxy.id_field) if isinstance(payload, dict) else None
                            if not isinstance(identity, str) or not identity.strip():
                                raise ValueError("Successful response lacks the configured string ID")
                            evidence = {
                                "event": "complete_upstream_response_before_downstream_loss",
                                "original_id": identity,
                                "upstream_status": status,
                                "request_sha256": sha256(body).hexdigest(),
                                "response_sha256": sha256(response_body).hexdigest(),
                                "response_bytes": len(response_body),
                            }
                            # Persist evidence before dropping the connection. A failed
                            # write or missing ID must never be reported as a valid loss.
                            if proxy.evidence_path:
                                proxy.evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
                            proxy.evidence = evidence
                            self.close_connection = True
                            self.connection.shutdown(socket.SHUT_RDWR)
                            return
                    excluded_response = HOP_HEADERS | {"content-length", "server", "date"}
                    for key, value in response_headers:
                        if key.lower() == "connection":
                            excluded_response.update(name.strip().lower() for name in value.split(","))
                    self.send_response(status)
                    for key, value in response_headers:
                        if key.lower() not in excluded_response:
                            self.send_header(key, value)
                    self.send_header("Content-Length", str(len(response_body)))
                    self.end_headers()
                    self.wfile.write(response_body)
                except OSError, HTTPException, ValueError:
                    self.send_error(502, "Fixture proxy could not capture or forward a complete response")

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def port(self):
        return self.server.server_port

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-port", type=int, required=True)
    parser.add_argument("--submission-path", required=True, help="Exact synthetic submission request path, including query if any")
    parser.add_argument("--id-field", default="id", help="Top-level JSON string ID field in the successful response")
    parser.add_argument("--port", type=int, default=0, help="Loopback listener port (0 selects an available port)")
    parser.add_argument("--evidence", type=Path, required=True, help="Local JSON evidence file; use only synthetic fixture data")
    args = parser.parse_args()
    with ResponseLossProxy(args.upstream_port, args.submission_path, id_field=args.id_field, port=args.port, evidence_path=args.evidence) as proxy:
        print(f"Synthetic response-loss proxy listening on http://127.0.0.1:{proxy.port}", flush=True)
        try:
            Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
