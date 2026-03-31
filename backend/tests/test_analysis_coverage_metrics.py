from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.test_case_agent import _compute_requirement_analysis_metrics, _heuristic_test_case_review
from app.models import Requirement


class RequirementAnalysisCoverageMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall allow managers to approve only expense reports that are in Submitted status.",
            )
        ]
        self.requirement_analysis = [
            {
                "requirement_id": "REQ-001",
                "requirement_text": self.requirements[0].text,
                "business_rules": [
                    {
                        "id": "REQ-001-BR-01",
                        "requirement_id": "REQ-001",
                        "title": "Managers approve submitted reports",
                        "description": "Only managers may approve expense reports in Submitted status.",
                        "rule_type": "Authorization",
                    }
                ],
                "field_constraints": [],
                "role_permissions": [
                    {
                        "id": "REQ-001-RP-01",
                        "requirement_id": "REQ-001",
                        "role": "Manager",
                        "action": "Approve expense report",
                        "effect": "Allow",
                        "conditions": "Report status is Submitted",
                    }
                ],
                "state_transitions": [
                    {
                        "id": "REQ-001-ST-01",
                        "requirement_id": "REQ-001",
                        "entity": "Expense report",
                        "from_state": "Submitted",
                        "to_state": "Approved",
                        "trigger": "Manager approves the report",
                        "guards": "Actor has Manager role",
                    }
                ],
                "risk_signals": [
                    {
                        "id": "REQ-001-RS-01",
                        "requirement_id": "REQ-001",
                        "title": "Unauthorized approval path",
                        "rationale": "Approval permissions must be enforced.",
                        "category": "Security",
                        "severity": "High",
                    }
                ],
                "suggested_scenarios": ["Authorization", "State Transition", "Negative"],
                "dependencies": [],
            }
        ]

    def test_metrics_cover_authorization_and_transition_signals(self) -> None:
        test_cases = [
            {
                "id": "TC-001",
                "title": "Manager approves a submitted report",
                "description": "Verify a manager can approve a submitted expense report.",
                "priority": "High",
                "type": "Functional",
                "status": "Draft",
                "preconditions": "An expense report exists in Submitted status.",
                "steps": [
                    {"step": 1, "action": "Log in as a manager", "expected": "Manager dashboard is displayed", "test_data": None},
                    {"step": 2, "action": "Open the submitted report and approve it", "expected": "Report moves to Approved", "test_data": None},
                ],
                "expected_result": "Only the manager can transition the report from Submitted to Approved.",
                "test_data": None,
                "estimated_time": "5 mins",
                "automation_status": "To Be Automated",
                "component": "Expense Reports",
                "tags": ["REQ-001", "scenario:authorization", "scenario:state-transition"],
            }
        ]

        metrics = _compute_requirement_analysis_metrics(self.requirement_analysis, test_cases, self.requirements)

        self.assertEqual(metrics["business_rules_total"], 1)
        self.assertEqual(metrics["business_rules_covered"], 1)
        self.assertEqual(metrics["role_permissions_covered"], 1)
        self.assertEqual(metrics["state_transitions_covered"], 1)
        self.assertEqual(metrics["risk_signals_covered"], 1)
        self.assertEqual(metrics["high_risk_items_without_tests"], [])

    def test_high_risk_gaps_block_review(self) -> None:
        weak_test_cases = [
            {
                "id": "TC-002",
                "title": "Employee views expense report",
                "description": "Verify an employee can open a submitted report.",
                "priority": "Medium",
                "type": "Functional",
                "status": "Draft",
                "preconditions": "A submitted expense report exists.",
                "steps": [
                    {"step": 1, "action": "Log in as an employee", "expected": "Employee dashboard is displayed", "test_data": None},
                    {"step": 2, "action": "Open the submitted report", "expected": "Report details are displayed", "test_data": None},
                ],
                "expected_result": "The report can be viewed.",
                "test_data": None,
                "estimated_time": "5 mins",
                "automation_status": "Manual",
                "component": "Expense Reports",
                "tags": ["REQ-001", "scenario:happy-path"],
            }
        ]

        review = _heuristic_test_case_review(
            weak_test_cases,
            self.requirements,
            90,
            coverage_plan=[
                {
                    "requirement_id": "REQ-001",
                    "requirement_text": self.requirements[0].text,
                    "scenarios": [
                        {
                            "id": "REQ-001-SCN-01",
                            "requirement_id": "REQ-001",
                            "scenario_type": "Authorization",
                            "title": "Manager approval",
                            "objective": "Verify manager-only approval",
                            "priority": "High",
                            "must_have": True,
                        }
                    ],
                }
            ],
            requirement_analysis=self.requirement_analysis,
        )

        self.assertFalse(review["approved"])
        self.assertTrue(any("High-risk requirement analysis items" in issue for issue in review["blocking_issues"]))


if __name__ == "__main__":
    unittest.main()
