from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.test_case_agent import _compute_test_case_coverage_metrics, _hydrate_test_cases, _normalize_test_case_type
from app.models import Requirement


class TestCaseHydrationTests(unittest.TestCase):
    def test_normalize_test_case_type_maps_scenario_like_labels(self) -> None:
        self.assertEqual(_normalize_test_case_type("Validation"), "Functional")
        self.assertEqual(_normalize_test_case_type("Boundary"), "Functional")
        self.assertEqual(_normalize_test_case_type("Compliance"), "Security")
        self.assertEqual(_normalize_test_case_type("API"), "Integration")

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

    def test_hydrate_test_cases_normalizes_nonstandard_generated_type_labels(self) -> None:
        raw_test_cases = [
            {
                "id": "TC-003",
                "title": "Reject invalid upload file type",
                "steps": [
                    {"step": 1, "action": "Open the upload form", "expected": "Upload form is visible", "test_data": None},
                    {"step": 2, "action": "Upload an unsupported file", "expected": "Validation error is displayed", "test_data": None},
                ],
                "priority": "High",
                "type": "Validation",
                "status": "Draft",
                "automation_status": "Manual",
            },
            {
                "id": "TC-004",
                "title": "Enforce compliance rule for access review",
                "steps": [
                    {"step": 1, "action": "Open the access review screen", "expected": "Review screen is visible", "test_data": None},
                    {"step": 2, "action": "Approve without required attestation", "expected": "Operation is blocked", "test_data": None},
                ],
                "priority": "High",
                "type": "Compliance",
                "status": "Draft",
                "automation_status": "Manual",
            },
        ]

        hydrated = _hydrate_test_cases(raw_test_cases)

        self.assertEqual(len(hydrated), 2)
        self.assertEqual(hydrated[0].type, "Functional")
        self.assertEqual(hydrated[1].type, "Security")

    def test_hydrate_test_cases_derives_structured_links_from_tags(self) -> None:
        raw_test_cases = [
            {
                "id": "TC-005",
                "title": "Traceable legacy case",
                "steps": [{"step": 1, "action": "Run scenario", "expected": "Scenario passes"}],
                "tags": ["REQ-001", "scenario:happy-path", "generated"],
            }
        ]

        hydrated = _hydrate_test_cases(raw_test_cases)

        self.assertEqual(hydrated[0].linked_requirement_ids, ["REQ-001"])
        self.assertIn("REQ-001", hydrated[0].tags)

    def test_coverage_metrics_use_explicit_linked_requirement_ids(self) -> None:
        requirements = [
            Requirement(id="REQ-001", text="The system shall allow users to sign in."),
            Requirement(id="REQ-002", text="The system shall allow users to sign out."),
        ]
        test_cases = [
            {
                "id": "TC-001",
                "title": "Sign-in happy path",
                "description": "Validates sign-in.",
                "expected_result": "Signed in",
                "steps": [{"step": 1, "action": "Sign in", "expected": "Session starts"}],
                "linked_requirement_ids": ["REQ-001"],
                "tags": ["scenario:happy-path"],
            }
        ]

        metrics = _compute_test_case_coverage_metrics(test_cases, requirements)

        self.assertEqual(metrics["requirements_covered"], 1)
        self.assertEqual(metrics["requirements_without_tests"], ["REQ-002"])
        self.assertEqual(metrics["test_cases_per_requirement"], {"REQ-001": 1, "REQ-002": 0})


if __name__ == "__main__":
    unittest.main()
