from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.test_case_agent import _hydrate_test_cases


class TestCaseHydrationTests(unittest.TestCase):
    def test_hydrate_test_cases_accepts_string_steps(self) -> None:
        raw_test_cases = [
            {
                "id": "TC-001",
                "title": "String step case",
                "steps": [
                    "Open the login page -> Login form is displayed",
                    "Enter valid credentials",
                ],
                "priority": "High",
                "type": "Functional",
                "status": "Draft",
                "automation_status": "Manual",
            }
        ]

        hydrated = _hydrate_test_cases(raw_test_cases)

        self.assertEqual(len(hydrated), 1)
        self.assertEqual(len(hydrated[0].steps), 2)
        self.assertEqual(hydrated[0].steps[0].action, "Open the login page")
        self.assertEqual(hydrated[0].steps[0].expected, "Login form is displayed")
        self.assertEqual(hydrated[0].steps[1].action, "Enter valid credentials")
        self.assertEqual(hydrated[0].steps[1].expected, "")

    def test_hydrate_test_cases_accepts_single_multiline_string_blob(self) -> None:
        raw_test_cases = [
            {
                "id": "TC-002",
                "title": "Multiline step blob",
                "steps": """1. Open the upgrade monitor\nExpected: The upgrade monitor page is visible\n2. Review the current SAP release -> The release details are displayed\n3. Confirm FPS level\nExpected Result: FPS01 is shown""",
                "priority": "High",
                "type": "Functional",
                "status": "Draft",
                "automation_status": "Manual",
            }
        ]

        hydrated = _hydrate_test_cases(raw_test_cases)

        self.assertEqual(len(hydrated), 1)
        self.assertEqual(len(hydrated[0].steps), 3)
        self.assertEqual(hydrated[0].steps[0].action, "Open the upgrade monitor")
        self.assertEqual(hydrated[0].steps[0].expected, "The upgrade monitor page is visible")
        self.assertEqual(hydrated[0].steps[1].action, "Review the current SAP release")
        self.assertEqual(hydrated[0].steps[1].expected, "The release details are displayed")
        self.assertEqual(hydrated[0].steps[2].action, "Confirm FPS level")
        self.assertEqual(hydrated[0].steps[2].expected, "FPS01 is shown")


if __name__ == "__main__":
    unittest.main()
