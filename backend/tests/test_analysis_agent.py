from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.analysis_agent import fallback_requirement_analysis, normalize_requirement_analysis
from app.models import Requirement


class RequirementAnalysisFallbackTests(unittest.TestCase):
    def test_fallback_analysis_covers_each_requirement(self) -> None:
        requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall allow only finance administrators to export Approved expense reports to CSV.",
            ),
            Requirement(
                id="REQ-002",
                text="The system shall require managers to enter a rejection reason when rejecting an expense report.",
            ),
        ]

        analysis = fallback_requirement_analysis(requirements)

        self.assertEqual(len(analysis), 2)
        export_analysis = analysis[0]
        rejection_analysis = analysis[1]
        self.assertEqual(export_analysis["requirement_id"], "REQ-001")
        self.assertTrue(export_analysis["business_rules"])
        self.assertTrue(export_analysis["role_permissions"])
        self.assertIn("Authorization", export_analysis["suggested_scenarios"])
        self.assertIn("Integration", export_analysis["suggested_scenarios"])
        self.assertTrue(rejection_analysis["field_constraints"])
        self.assertTrue(rejection_analysis["state_transitions"])
        self.assertIn("State Transition", rejection_analysis["suggested_scenarios"])
        self.assertIn("Validation", rejection_analysis["suggested_scenarios"])

    def test_normalize_analysis_fills_missing_requirements_with_fallback(self) -> None:
        requirements = [
            Requirement(id="REQ-001", text="The system shall allow users to sign in using email and password."),
            Requirement(id="REQ-002", text="The system shall lock the account after 5 failed login attempts within 10 minutes."),
        ]
        raw_analysis = [
            {
                "requirement_id": "REQ-001",
                "requirement_text": "The system shall allow users to sign in using email and password.",
                "business_rules": [
                    {
                        "title": "Credential login",
                        "description": "Users can authenticate with email and password.",
                        "rule_type": "Business",
                    }
                ],
                "suggested_scenarios": ["Happy Path", "Negative", "Negative"],
            }
        ]

        normalized = normalize_requirement_analysis(raw_analysis, requirements)

        self.assertEqual([item["requirement_id"] for item in normalized], ["REQ-001", "REQ-002"])
        self.assertEqual(normalized[0]["suggested_scenarios"], ["Happy Path", "Negative"])
        self.assertTrue(normalized[0]["business_rules"])
        self.assertTrue(normalized[1]["business_rules"])
        self.assertIn("Negative", normalized[1]["suggested_scenarios"])
        self.assertIn("Boundary", normalized[1]["suggested_scenarios"])
        self.assertIn("State Transition", normalized[1]["suggested_scenarios"])


if __name__ == "__main__":
    unittest.main()
