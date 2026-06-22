from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.test_case_coverage import (
    _compute_planned_scenario_metrics,
    _compute_requirement_analysis_metrics,
    _extract_scenario_types_from_test_case,
    _normalize_coverage_plan,
)
from app.agents.test_case_review import (
    _heuristic_test_case_review,
    _merge_review_results,
    _prefer_review,
    _resolve_test_case_workflow_settings,
)
from app.models import Requirement, WorkflowSettings


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


class CoveragePlanNormalizationTests(unittest.TestCase):
    def test_backfilled_scenarios_do_not_become_must_have(self) -> None:
        requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall display the dashboard summary for the signed-in user.",
            )
        ]

        normalized_plan = _normalize_coverage_plan(
            [
                {
                    "requirement_id": "REQ-001",
                    "requirement_text": requirements[0].text,
                    "scenarios": [
                        {
                            "id": "REQ-001-SCN-01",
                            "scenario_type": "Happy Path",
                            "title": "Dashboard summary is displayed",
                            "objective": "Verify the signed-in user sees the dashboard summary.",
                            "priority": "High",
                            "must_have": True,
                        }
                    ],
                }
            ],
            requirements,
        )

        scenarios = normalized_plan[0]["scenarios"]
        self.assertEqual(scenarios[0]["scenario_type"], "Happy Path")
        self.assertTrue(scenarios[0]["must_have"])
        self.assertTrue(all(not scenario["must_have"] for scenario in scenarios[1:]))

    def test_heuristic_review_ignores_backfilled_optional_scenario_gaps(self) -> None:
        requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall display the dashboard summary for the signed-in user.",
            )
        ]
        normalized_plan = _normalize_coverage_plan(
            [
                {
                    "requirement_id": "REQ-001",
                    "requirement_text": requirements[0].text,
                    "scenarios": [
                        {
                            "id": "REQ-001-SCN-01",
                            "scenario_type": "Happy Path",
                            "title": "Dashboard summary is displayed",
                            "objective": "Verify the signed-in user sees the dashboard summary.",
                            "priority": "High",
                            "must_have": True,
                        }
                    ],
                }
            ],
            requirements,
        )

        review = _heuristic_test_case_review(
            [
                {
                    "id": "TC-001",
                    "title": "Signed-in user sees dashboard summary",
                    "description": "Verify the dashboard summary appears after sign-in.",
                    "priority": "High",
                    "type": "Functional",
                    "status": "Draft",
                    "preconditions": "A user is signed in.",
                    "steps": [
                        {"step": 1, "action": "Sign in with valid credentials", "expected": "Dashboard loads", "test_data": None},
                        {"step": 2, "action": "View the dashboard summary", "expected": "Summary widgets are visible", "test_data": None},
                    ],
                    "expected_result": "The signed-in user sees the dashboard summary.",
                    "test_data": None,
                    "estimated_time": "5 mins",
                    "automation_status": "Manual",
                    "component": "Dashboard",
                    "tags": ["REQ-001", "scenario:happy-path"],
                }
            ],
            requirements,
            90,
            coverage_plan=normalized_plan,
            requirement_analysis=[
                {
                    "requirement_id": "REQ-001",
                    "requirement_text": requirements[0].text,
                    "business_rules": [
                        {
                            "id": "REQ-001-BR-01",
                            "requirement_id": "REQ-001",
                            "title": "Dashboard summary is displayed",
                            "description": "The signed-in user sees the dashboard summary.",
                            "rule_type": "Business",
                        }
                    ],
                    "field_constraints": [],
                    "role_permissions": [],
                    "state_transitions": [],
                    "risk_signals": [],
                    "suggested_scenarios": ["Happy Path"],
                    "dependencies": [],
                }
            ],
        )

        self.assertFalse(any("Missing must-have planned scenarios" in issue for issue in review["blocking_issues"]))
        self.assertGreaterEqual(review["score"], 90)

    def test_exact_scenario_refs_cover_one_to_one_and_combined_cases(self) -> None:
        requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall support manager approval with audit capture.",
            )
        ]
        coverage_plan = [
            {
                "requirement_id": "REQ-001",
                "requirement_text": requirements[0].text,
                "scenarios": [
                    {
                        "id": "REQ-001-SCN-01",
                        "requirement_id": "REQ-001",
                        "scenario_type": "Happy Path",
                        "title": "Manager approves a submitted report",
                        "objective": "Verify manager approval succeeds.",
                        "priority": "High",
                        "must_have": True,
                    },
                    {
                        "id": "REQ-001-SCN-02",
                        "requirement_id": "REQ-001",
                        "scenario_type": "Integration",
                        "title": "Audit record is written",
                        "objective": "Verify approval writes audit history.",
                        "priority": "High",
                        "must_have": True,
                    },
                ],
            }
        ]

        one_to_one_metrics = _compute_planned_scenario_metrics(
            coverage_plan,
            [
                {
                    "id": "TC-001",
                    "title": "Manager approves report",
                    "linked_requirement_ids": ["REQ-001"],
                    "scenario_refs": ["REQ-001-SCN-01"],
                    "tags": ["scenario:happy-path"],
                },
                {
                    "id": "TC-002",
                    "title": "Approval audit record",
                    "linked_requirement_ids": ["REQ-001"],
                    "scenario_refs": ["REQ-001-SCN-02"],
                    "tags": ["scenario:integration"],
                },
            ],
            requirements,
        )
        combined_metrics = _compute_planned_scenario_metrics(
            coverage_plan,
            [
                {
                    "id": "TC-003",
                    "title": "Manager approval writes audit",
                    "linked_requirement_ids": ["REQ-001"],
                    "scenario_refs": ["REQ-001-SCN-01", "REQ-001-SCN-02"],
                    "tags": ["scenario:happy-path", "scenario:integration"],
                }
            ],
            requirements,
        )

        self.assertEqual(one_to_one_metrics["scenario_coverage_ratio"], 1.0)
        self.assertEqual(combined_metrics["scenario_coverage_ratio"], 1.0)
        self.assertEqual(combined_metrics["scenario_ref_coverage_mode"], "exact")
        self.assertFalse(combined_metrics["scenario_ref_coverage_degraded"])

    def test_exact_scenario_refs_take_precedence_over_scenario_type_inference(self) -> None:
        requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall support two independent happy-path approval variants.",
            )
        ]
        coverage_plan = [
            {
                "requirement_id": "REQ-001",
                "requirement_text": requirements[0].text,
                "scenarios": [
                    {
                        "id": "REQ-001-SCN-01",
                        "requirement_id": "REQ-001",
                        "scenario_type": "Happy Path",
                        "title": "Manager approves a standard report",
                        "objective": "Verify standard approval.",
                        "priority": "High",
                        "must_have": True,
                    },
                    {
                        "id": "REQ-001-SCN-02",
                        "requirement_id": "REQ-001",
                        "scenario_type": "Happy Path",
                        "title": "Manager approves an escalated report",
                        "objective": "Verify escalated approval.",
                        "priority": "High",
                        "must_have": True,
                    },
                ],
            }
        ]

        metrics = _compute_planned_scenario_metrics(
            coverage_plan,
            [
                {
                    "id": "TC-001",
                    "title": "Manager approves standard report",
                    "linked_requirement_ids": ["REQ-001"],
                    "scenario_refs": ["REQ-001-SCN-01"],
                    "tags": ["scenario:happy-path"],
                }
            ],
            requirements,
        )

        self.assertEqual(metrics["covered_scenario_ids"], ["REQ-001-SCN-01"])
        self.assertEqual(metrics["missing_scenario_ids"], ["REQ-001-SCN-02"])
        self.assertEqual(metrics["scenario_ref_coverage_mode"], "exact")
        self.assertFalse(metrics["scenario_ref_coverage_degraded"])

    def test_missing_scenario_refs_are_marked_as_degraded_heuristic_coverage(self) -> None:
        requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall display the dashboard summary for the signed-in user.",
            )
        ]
        coverage_plan = [
            {
                "requirement_id": "REQ-001",
                "requirement_text": requirements[0].text,
                "scenarios": [
                    {
                        "id": "REQ-001-SCN-01",
                        "requirement_id": "REQ-001",
                        "scenario_type": "Happy Path",
                        "title": "Dashboard summary is displayed",
                        "objective": "Verify dashboard summary after sign-in.",
                        "priority": "High",
                        "must_have": True,
                    }
                ],
            }
        ]
        test_cases = [
            {
                "id": "TC-001",
                "title": "Signed-in user sees dashboard summary",
                "description": "Verify the dashboard summary appears after sign-in.",
                "priority": "High",
                "type": "Functional",
                "status": "Draft",
                "preconditions": "A user is signed in.",
                "steps": [
                    {"step": 1, "action": "Sign in with valid credentials", "expected": "Dashboard loads", "test_data": None},
                    {"step": 2, "action": "View the dashboard summary", "expected": "Summary widgets are visible", "test_data": None},
                ],
                "expected_result": "The signed-in user sees the dashboard summary.",
                "linked_requirement_ids": ["REQ-001"],
                "tags": ["scenario:happy-path"],
            }
        ]

        metrics = _compute_planned_scenario_metrics(coverage_plan, test_cases, requirements)
        review = _heuristic_test_case_review(test_cases, requirements, 90, coverage_plan=coverage_plan)

        self.assertEqual(metrics["scenario_ref_coverage_mode"], "heuristic")
        self.assertTrue(metrics["scenario_ref_coverage_degraded"])
        self.assertEqual(metrics["scenario_ref_missing_case_count"], 1)
        self.assertTrue(any("heuristic scenario-type inference" in suggestion for suggestion in review["suggestions"]))

    def test_custom_scenario_tags_map_back_to_canonical_coverage_types(self) -> None:
        test_case = {
            "id": "TC-INT-001",
            "title": "Upgrade existing installation while ignoring non-prefixed files",
            "description": "Verify installation upgrades the browser engine and skips unsupported non-prefixed files.",
            "tags": ["REQ-001", "scenario:upgrade", "scenario:ignore-non-prefixed-files"],
            "steps": [
                {"step": 1, "action": "Upgrade the existing installation", "expected": "The browser engine is installed", "test_data": None},
                {"step": 2, "action": "Process non-prefixed files", "expected": "Non-prefixed files are ignored", "test_data": None},
            ],
            "expected_result": "Upgrade completes and non-prefixed files are skipped.",
        }

        extracted = _extract_scenario_types_from_test_case(test_case)

        self.assertEqual(extracted, ["Integration", "Negative"])

    def test_heuristic_review_recognizes_custom_scenario_tag_slugs(self) -> None:
        requirements = [
            Requirement(
                id="REQ-001",
                text="The system shall upgrade the existing installation by installing the specific browser engine and ignoring non-prefixed files.",
            )
        ]

        review = _heuristic_test_case_review(
            [
                {
                    "id": "TC-001",
                    "title": "Upgrade existing installation and ignore non-prefixed files",
                    "description": "Verify the upgrade installs the browser engine and ignores invalid non-prefixed files.",
                    "priority": "High",
                    "type": "Functional",
                    "status": "Draft",
                    "preconditions": "An older installation exists.",
                    "steps": [
                        {"step": 1, "action": "Run the upgrade", "expected": "The specific browser engine is installed", "test_data": None},
                        {"step": 2, "action": "Include non-prefixed files in the input", "expected": "The system ignores them", "test_data": None},
                    ],
                    "expected_result": "The installation is upgraded and non-prefixed files are ignored.",
                    "test_data": None,
                    "estimated_time": "5 mins",
                    "automation_status": "Manual",
                    "component": "Installer",
                    "tags": ["REQ-001", "scenario:upgrade", "scenario:ignore-non-prefixed-files"],
                }
            ],
            requirements,
            90,
            coverage_plan=[
                {
                    "requirement_id": "REQ-001",
                    "requirement_text": requirements[0].text,
                    "scenarios": [
                        {
                            "id": "REQ-001-SCN-01",
                            "requirement_id": "REQ-001",
                            "scenario_type": "Integration",
                            "title": "Upgrade existing installation",
                            "objective": "Install the specific browser engine during upgrade.",
                            "priority": "High",
                            "must_have": True,
                        },
                        {
                            "id": "REQ-001-SCN-02",
                            "requirement_id": "REQ-001",
                            "scenario_type": "Negative",
                            "title": "Ignore non-prefixed files",
                            "objective": "Ensure invalid non-prefixed files are ignored.",
                            "priority": "High",
                            "must_have": True,
                        },
                    ],
                }
            ],
            requirement_analysis=[
                {
                    "requirement_id": "REQ-001",
                    "requirement_text": requirements[0].text,
                    "business_rules": [
                        {
                            "id": "REQ-001-BR-01",
                            "requirement_id": "REQ-001",
                            "title": "Installer handles browser engine upgrade",
                            "description": "The specific browser engine is installed during upgrade.",
                            "rule_type": "Integration",
                        }
                    ],
                    "field_constraints": [],
                    "role_permissions": [],
                    "state_transitions": [],
                    "risk_signals": [],
                    "suggested_scenarios": ["Integration", "Negative"],
                    "dependencies": [],
                }
            ],
        )

        self.assertFalse(any("Missing must-have planned scenarios" in issue for issue in review["blocking_issues"]))
        self.assertGreaterEqual(review["score"], 90)


class ReviewMergeTests(unittest.TestCase):
    def test_disagreeing_reviews_prefer_heuristic_summary(self) -> None:
        merged = _merge_review_results(
            {
                "approved": True,
                "score": 96,
                "threshold": 90,
                "summary": "All must-have scenarios are implemented.",
                "blocking_issues": [],
                "suggestions": [],
                "unmet_criteria": [],
            },
            {
                "approved": False,
                "score": 82,
                "threshold": 90,
                "summary": "Test cases still need refinement before export is unlocked.",
                "blocking_issues": ["Missing must-have planned scenarios: REQ-001 - Integration: Upgrade existing installation."],
                "suggestions": [],
                "unmet_criteria": [],
            },
        )

        self.assertEqual(merged["summary"], "Test cases still need refinement before export is unlocked.")


class WorkflowSettingsResolutionTests(unittest.TestCase):
    def test_settings_resolution_applies_overrides(self) -> None:
        resolved = _resolve_test_case_workflow_settings(
            WorkflowSettings(
                approval_threshold=94,
                max_iterations=6,
                timeout_seconds=120,
                stall_iteration_limit=3,
                retry_attempts=2,
            )
        )

        self.assertEqual(resolved["approval_threshold"], 94)
        self.assertEqual(resolved["max_iterations"], 6)
        self.assertEqual(resolved["timeout_seconds"], 120)
        self.assertEqual(resolved["stall_iteration_limit"], 3)
        self.assertEqual(resolved["retry_attempts"], 2)

    def test_prefer_review_favors_approved_candidate(self) -> None:
        candidate = {
            "approved": True,
            "score": 91,
            "threshold": 90,
            "summary": "Candidate approved",
            "blocking_issues": [],
            "suggestions": [],
            "unmet_criteria": [],
        }
        incumbent = {
            "approved": False,
            "score": 95,
            "threshold": 90,
            "summary": "Incumbent rejected",
            "blocking_issues": ["Missing must-have scenario"],
            "suggestions": [],
            "unmet_criteria": [],
        }

        self.assertTrue(_prefer_review(candidate, incumbent))


if __name__ == "__main__":
    unittest.main()
