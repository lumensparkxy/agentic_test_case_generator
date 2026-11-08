#!/usr/bin/env python3
"""Run Chromium controls on healthy and faulted synthetic Room Booking targets."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess

from response_loss_proxy import ResponseLossProxy
from room_booking_fixture import DEFECTS, SUBMIT_PATH, booking_target

ROOT = Path(__file__).resolve().parents[1]


def run(output: Path) -> int:
    def origin(port):
        return f"http://127.0.0.1:{port}"

    with ExitStack() as stack:
        healthy = stack.enter_context(booking_target())
        broken = {name: stack.enter_context(booking_target(defect=name)) for name in DEFECTS}
        targets = {"healthy": origin(healthy), "defects": {name: origin(port) for name, port in broken.items()}, "loss": {}}
        for name, port in (("healthy", healthy), ("duplicate-submission", broken["duplicate-submission"])):
            evidence = output / f"response-loss-{name}.json"
            proxy = stack.enter_context(ResponseLossProxy(port, SUBMIT_PATH, evidence_path=evidence))
            targets["loss"][name] = {"origin": origin(proxy.port), "evidence": str(evidence)}
        local = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "PLAYWRIGHT_BROWSERS_PATH") if key in os.environ}
        return subprocess.run(
            [
                "npm",
                "run",
                "test:e2e",
                "--",
                "e2e/room-booking-target.spec.js",
                "--workers=1",
                "--retries=0",
                "--reporter=list",
                "--output",
                str(output / "browser"),
            ],
            cwd=ROOT / "frontend",
            env={**local, "E2E_ROOM_BOOKING_TARGETS": json.dumps(targets)},
            check=False,
        ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New evidence directory outside the checkout")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or output.is_relative_to(ROOT):
        parser.error("output must be a new directory outside the checkout")
    output.mkdir(parents=True)
    return run(output)


if __name__ == "__main__":
    raise SystemExit(main())
