#!/usr/bin/env python3
"""Run the opt-in scenario worker against the isolated Firestore emulator."""

import argparse
import logging
from pathlib import Path
from random import uniform
import sys
from time import sleep

import firebase_admin
from firebase_admin import credentials
from google.auth.credentials import AnonymousCredentials

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.approval_scenario_worker import DEMO_PROJECT_ID, approval_scenario_client_allowed, approval_scenarios_enabled, run_due_scenarios
from app.services.firestore_repository import get_required_firestore_client


class _EmulatorCredential(credentials.Base):
    def get_credential(self):
        return AnonymousCredentials()


def _emulator_client():
    if not approval_scenarios_enabled():
        raise RuntimeError("Configure the enabled loopback demo scenario environment first")
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(_EmulatorCredential(), {"projectId": DEMO_PROJECT_ID})
    client = get_required_firestore_client(unavailable_message="Scenario worker storage is unavailable")
    if not approval_scenario_client_allowed(client):
        raise RuntimeError("The Firestore client does not point to the configured demo emulator")
    return client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Handle one bounded scan of due scenario work and exit")
    args = parser.parse_args()
    try:
        client = _emulator_client()
    except RuntimeError as error:
        parser.error(str(error))

    if args.once:
        print(f"Handled {run_due_scenarios(client)} scenario claim(s)")
        return
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            run_due_scenarios(client)
        except Exception as error:
            logging.warning("Scenario work could not be scanned (%s)", type(error).__name__)
        sleep(uniform(1.5, 2.5))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
