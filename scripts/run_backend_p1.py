"""Run the explicit critical backend regression gate; full discovery is separate."""

import argparse
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
SELECTION = ROOT / "backend/tests/priority1.txt"


def selected_suite(names):
    if not names or len(set(names)) != len(names):
        raise ValueError("P1 selection must contain unique explicit test methods")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in names:
        if len(name.split(".")) != 3 or not name.split(".")[-1].startswith("test_"):
            raise ValueError(f"Select an explicit module.class.test_method: {name}")
        test = loader.loadTestsFromName(name)
        if loader.errors or test.countTestCases() != 1:
            raise ValueError(f"P1 test is missing or does not select exactly one method: {name}\n" + "\n".join(loader.errors))
        suite.addTest(test)
    return suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Validate and list the selected test methods without running them")
    args = parser.parse_args()
    names = [line.strip() for line in SELECTION.read_text().splitlines() if line.strip() and not line.startswith("#")]
    try:
        suite = selected_suite(names)
    except ValueError as error:
        parser.error(str(error))
    if args.list:
        print("\n".join(names))
        print(f"Total: {suite.countTestCases()} critical backend tests")
        return 0
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
