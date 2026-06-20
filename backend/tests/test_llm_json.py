from pathlib import Path
import sys
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.utils.llm_json import (
    parse_coverage_plan_json_detailed,
    parse_requirement_analysis_json,
    parse_requirement_analysis_json_detailed,
    parse_requirements_json_detailed,
    parse_review_json,
    parse_review_json_detailed,
    parse_test_cases_json_detailed,
)


class ParseRequirementAnalysisJsonTests(unittest.TestCase):
    def test_parses_wrapped_requirement_analysis_payload(self) -> None:
        payload = """
        {
          "requirement_analysis": [
            {
              "requirement_id": "REQ-001",
              "requirement_text": "The system shall allow users to sign in using email and password.",
              "business_rules": [
                {
                  "id": "BR-001",
                  "requirement_id": "REQ-001",
                  "title": "Email and password login",
                  "description": "Users can authenticate with email and password.",
                  "rule_type": "Business"
                }
              ],
              "field_constraints": [
                {
                  "id": "FC-001",
                  "requirement_id": "REQ-001",
                  "field_name": "email",
                  "description": "Email must be supplied.",
                  "constraint_type": "Required"
                }
              ],
              "role_permissions": [
                {
                  "id": "RP-001",
                  "requirement_id": "REQ-001",
                  "role": "User",
                  "action": "Sign in",
                  "effect": "Allow"
                }
              ],
              "state_transitions": [
                {
                  "id": "ST-001",
                  "requirement_id": "REQ-001",
                  "entity": "Session",
                  "from_state": "Signed Out",
                  "to_state": "Signed In",
                  "trigger": "Submit valid credentials"
                }
              ],
              "risk_signals": [
                {
                  "id": "RS-001",
                  "requirement_id": "REQ-001",
                  "title": "Credential misuse",
                  "rationale": "Authentication endpoints are security-sensitive.",
                  "category": "Security",
                  "severity": "High"
                }
              ],
              "suggested_scenarios": ["Happy Path", "Negative", "Negative"],
              "dependencies": ["Email service", "Email service"]
            }
          ]
        }
        """

        parsed = parse_requirement_analysis_json(payload)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["requirement_id"], "REQ-001")
        self.assertEqual(parsed[0]["suggested_scenarios"], ["Happy Path", "Negative"])
        self.assertEqual(parsed[0]["dependencies"], ["Email service"])
        self.assertEqual(len(parsed[0]["business_rules"]), 1)
        self.assertEqual(len(parsed[0]["field_constraints"]), 1)
        self.assertEqual(len(parsed[0]["role_permissions"]), 1)
        self.assertEqual(len(parsed[0]["state_transitions"]), 1)
        self.assertEqual(len(parsed[0]["risk_signals"]), 1)

    def test_returns_empty_list_for_malformed_payload(self) -> None:
        parsed = parse_requirement_analysis_json("not-json-at-all")
        self.assertEqual(parsed, [])

    def test_detailed_parser_reports_requirement_analysis_errors(self) -> None:
        parsed, error = parse_requirement_analysis_json_detailed("not-json-at-all")

        self.assertEqual(parsed, [])
        self.assertEqual(error, "no JSON payload found")

    def test_filters_invalid_analysis_items(self) -> None:
        payload = """
        [
          {
            "requirement_id": "REQ-002",
            "requirement_text": "The system shall require a rejection reason.",
            "business_rules": ["ignore me"],
            "suggested_scenarios": ["Validation", " ", "Validation"]
          },
          {
            "requirement_text": "Missing id should be dropped"
          }
        ]
        """

        parsed = parse_requirement_analysis_json(payload)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["requirement_id"], "REQ-002")
        self.assertEqual(parsed[0]["business_rules"], [])
        self.assertEqual(parsed[0]["suggested_scenarios"], ["Validation"])


class ParseReviewJsonTests(unittest.TestCase):
    def test_parses_decimal_and_fraction_like_scores(self) -> None:
        payload = """
        {
          "approved": true,
          "score": "94.0/100",
          "threshold": "90.0",
          "summary": "Review completed.",
          "blocking_issues": [],
          "suggestions": [],
          "unmet_criteria": []
        }
        """

        parsed = parse_review_json(payload, default_threshold=90)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["score"], 94)
        self.assertEqual(parsed["threshold"], 90)

    def test_detailed_review_parser_reports_invalid_json(self) -> None:
        parsed, error = parse_review_json_detailed('{"approved": true', default_threshold=90)

        self.assertIsNone(parsed)
        self.assertEqual(error, "invalid JSON payload: Expecting ',' delimiter")


class DetailedPayloadParserTests(unittest.TestCase):
    def test_requirements_detailed_parser_reports_missing_keys(self) -> None:
        parsed, error = parse_requirements_json_detailed('[{"id": "REQ-001"}]')

        self.assertEqual(parsed, [])
        self.assertEqual(error, "requirements payload did not contain valid id/text objects")

    def test_test_case_detailed_parser_reports_empty_list(self) -> None:
        parsed, error = parse_test_cases_json_detailed('{"test_cases": []}')

        self.assertEqual(parsed, [])
        self.assertEqual(error, "test_cases list was empty")

    def test_test_case_detailed_parser_rejects_items_without_minimum_shape(self) -> None:
        parsed, error = parse_test_cases_json_detailed('{"test_cases": [{"id": "TC-001"}]}')

        self.assertEqual(parsed, [])
        self.assertEqual(error, "test_cases payload did not contain valid id/title/steps objects")

    def test_test_case_parser_recovers_complete_entries_from_truncated_output(self) -> None:
        payload = """
        {"test_cases":[
          {
            "id":"TC-001",
            "title":"Successful sign in",
            "description":"Verify sign in succeeds.",
            "steps":[{"step":1,"action":"Sign in with valid credentials.","expected":"Dashboard is visible."}],
            "tags":["REQ-001","scenario:happy-path"]
          },
          {
            "id":"TC-002",
            "title":"Lockout after repeated failures",
            "description":"Verify lockout policy.",
            "steps":[{"step":1,"action":"Submit invalid credentials five times.","expected":"Account is locked."}],
            "tags":["REQ-002","scenario:negative"]
          },
          {
            "id":"TC-003",
            "title":"Audit event on lockout",
            "description":"Verify audit logging.",
            "steps":[{"step":1,"action":"Trigger a lockout.","expected":"Audit event is recorded."}],
            "tags":["REQ-003","scenario:error-handling
        """

        parsed, error = parse_test_cases_json_detailed(payload)

        self.assertEqual([item["id"] for item in parsed], ["TC-001", "TC-002"])
        self.assertIsNotNone(error)
        self.assertIn("invalid JSON payload", error)
        self.assertIn("recovered 2 complete test_cases entries", error)

    def test_test_case_parser_rejects_truncated_output_without_complete_entries(self) -> None:
        payload = '{"test_cases":[{"id":"TC-001","title":"Incomplete","steps":[{"step":1,"action":"Start","expected"'

        parsed, error = parse_test_cases_json_detailed(payload)

        self.assertEqual(parsed, [])
        self.assertIsNotNone(error)
        self.assertIn("invalid JSON payload", error)

    def test_parsers_accept_structured_adk_output_schema_state(self) -> None:
        test_cases, test_case_error = parse_test_cases_json_detailed(
            {
                "test_cases": [
                    {
                        "id": "TC-001",
                        "title": "Invite valid user",
                        "steps": [{"step": 1, "action": "Submit invite", "expected": "Invite is sent"}],
                    }
                ]
            }
        )
        coverage_plan, coverage_error = parse_coverage_plan_json_detailed(
            {
                "coverage_plan": [
                    {
                        "requirement_id": "REQ-001",
                        "requirement_text": "Invite users",
                        "scenarios": [{"id": "REQ-001-SCN-01", "title": "Happy path"}],
                    }
                ]
            }
        )
        analysis, analysis_error = parse_requirement_analysis_json_detailed(
            {
                "requirement_analysis": [
                    {
                        "requirement_id": "REQ-001",
                        "requirement_text": "Invite users",
                        "suggested_scenarios": ["Happy Path"],
                    }
                ]
            }
        )
        review, review_error = parse_review_json_detailed(
            {
                "approved": True,
                "score": 95,
                "threshold": 90,
                "summary": "Ready.",
                "blocking_issues": [],
                "suggestions": [],
                "unmet_criteria": [],
            }
        )

        self.assertEqual(test_case_error, None)
        self.assertEqual(len(test_cases), 1)
        self.assertEqual(coverage_error, None)
        self.assertEqual(len(coverage_plan), 1)
        self.assertEqual(analysis_error, None)
        self.assertEqual(len(analysis), 1)
        self.assertEqual(review_error, None)
        self.assertEqual(review["score"], 95)

    def test_coverage_plan_parser_recovers_complete_entries_from_truncated_output(self) -> None:
        payload = """
        {
          "coverage_plan": [
            {
              "requirement_id": "REQ-001",
              "requirement_text": "Users can sign in.",
              "scenarios": [
                {
                  "id": "REQ-001-SCN-01",
                  "scenario_type": "Happy Path",
                  "title": "Successful sign in",
                  "objective": "Validate successful sign in.",
                  "priority": "High",
                  "must_have": true
                }
              ]
            },
            {
              "requirement_id": "REQ-002",
              "requirement_text": "Accounts lock after repeated failures.",
              "scenarios": [
                {
                  "id": "REQ-002-SCN-01",
                  "scenario_type": "Negative",
                  "title": "Account lockout",
                  "objective": "Validate lockout after failed attempts.",
                  "priority": "High",
                  "must_have": true
                }
              ]
            },
            {
              "requirement_id": "REQ-003",
              "requirement_text": "Audit events are retained.",
              "scenarios": [
                {
                  "id": "REQ-003-SCN-01",
                  "scenario_type": "Happy Path",
                  "title": "Audit retention",
                  "objective": "Validate audit retention.",
                  "priority": "High",
                  "must
        """

        coverage_plan, coverage_error = parse_coverage_plan_json_detailed(payload)

        self.assertEqual([item["requirement_id"] for item in coverage_plan], ["REQ-001", "REQ-002"])
        self.assertIsNotNone(coverage_error)
        self.assertIn("invalid JSON payload", coverage_error)
        self.assertIn("recovered 2 complete coverage_plan entries", coverage_error)

    def test_coverage_plan_parser_rejects_truncated_output_without_complete_entries(self) -> None:
        payload = '{"coverage_plan": [{"requirement_id": "REQ-001", "scenarios": [{"id": "REQ-001-SCN-01", "must'

        coverage_plan, coverage_error = parse_coverage_plan_json_detailed(payload)

        self.assertEqual(coverage_plan, [])
        self.assertIsNotNone(coverage_error)
        self.assertIn("invalid JSON payload", coverage_error)


if __name__ == "__main__":
    unittest.main()
