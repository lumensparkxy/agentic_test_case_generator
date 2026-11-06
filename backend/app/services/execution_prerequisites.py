"""References are resolved only inside execution, never copied into artifacts."""

import os
import re

REFERENCE = re.compile(r"^env:(PETF_TEST_[A-Z0-9_]+)$")


def prerequisite_blockers(case):
    blockers = []
    for item in case.execution_prerequisites:
        match = REFERENCE.fullmatch(item.reference or "")
        if item.status != "configured" or not match or not os.getenv(match[1]):
            blockers.append(f"Prerequisite {item.name} requires an available env:PETF_TEST_* reference; no execution value is recorded.")
    return blockers
