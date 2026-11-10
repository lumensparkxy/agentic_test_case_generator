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

    def test_normalization_does_not_turn_payload_text_into_policy(self) -> None:
        requirements = [
            Requirement(id="REQ-001", text='Paste JSON in the message field. Show "Text accepted" and display the submitted text. '),
            Requirement(id="REQ-002", text='Reject invalid input and show "Rejected expression". Leave the submitted text visible.'),
            Requirement(id="REQ-003", text="Show “Move from Draft to Approved” as a help example and export the help history to CSV."),
        ]
        raw = [{"requirement_id": "REQ-001", "requirement_text": requirements[0].text.strip(), "field_constraints": [], "risk_signals": []}]

        for requirement, row in zip(requirements, normalize_requirement_analysis(raw, requirements)):
            with self.subTest(requirement=requirement.id):
                self.assertEqual(row["requirement_text"], requirement.text)
                self.assertNotIn("File Type", [constraint["constraint_type"] for constraint in row["field_constraints"]])
                self.assertEqual(row["state_transitions"], [])
                self.assertNotIn("Workflow", [risk["category"] for risk in row["risk_signals"]])
                self.assertNotIn("State Transition", row["suggested_scenarios"])
                self.assertNotIn("State Transition", [rule["rule_type"] for rule in row["business_rules"]])

    def test_upload_formats_and_size_follow_their_own_clause(self) -> None:
        requirements = [
            Requirement(id="REQ-001", text="Accept only PDF receipt uploads up to 2 MB. Reject DOCX files and export the summary as CSV."),
            Requirement(id="REQ-002", text="Allow users to upload a photo in JPG or PNG format."),
            Requirement(id="REQ-003", text="Upload only PNG/PDF with max 2 MB."),
            Requirement(id="REQ-004", text="Do not accept PDF files for upload. Send a status message with max 2 MB response data."),
            Requirement(id="REQ-005", text="Upload a log in PNG format and only CSV files may be exported."),
            Requirement(id="REQ-006", text="Only PDF and JSON files may be uploaded as evidence."),
        ]

        exclusive, supported, direct, rejected, mixed, passive = fallback_requirement_analysis(requirements)
        self.assertEqual(
            [(item["constraint_type"], item["value"]) for item in exclusive["field_constraints"]],
            [("File Size", "2 MB"), ("File Type", "PDF")],
        )
        self.assertEqual(supported["field_constraints"][0]["value"], "JPG, PNG")
        self.assertIsNone(supported["field_constraints"][0]["negative_example"])
        self.assertEqual([item["value"] for item in direct["field_constraints"]], ["2 MB", "PNG, PDF"])
        self.assertEqual(rejected["field_constraints"], [])
        self.assertEqual([item["value"] for item in mixed["field_constraints"]], ["PNG"])
        self.assertEqual([item["value"] for item in passive["field_constraints"]], ["PDF, JSON"])

    def test_attachment_output_and_upload_policies_stay_separate(self) -> None:
        controls = [
            ("Show a preview of the existing attachment in JSON format.", []),
            ("Export each receipt attachment in CSV format.", []),
            ("Upload the receipt attachment, which the system exports in CSV format.", []),
            ("Upload the receipt and the exported response must not exceed 2 MB.", []),
            ("Show the help text `Upload only PNG/PDF with max 2 MB`.", []),
            ("Upload receipts in PNG format and do not accept DOCX.", [("File Type", "PNG")]),
            ("The uploaded file must not exceed 2 MB.", [("File Size", "2 MB")]),
        ]
        requirements = [Requirement(id=f"REQ-{index:03}", text=source) for index, (source, _) in enumerate(controls, start=1)]

        for (source, expected), row in zip(controls, fallback_requirement_analysis(requirements)):
            with self.subTest(source=source):
                self.assertEqual([(item["constraint_type"], item["value"]) for item in row["field_constraints"]], expected)

    def test_workflow_state_order_and_unknowns_follow_the_source(self) -> None:
        requirements = [
            Requirement(id="REQ-001", text='On correction, move the order from "Approved" to "Draft".'),
            Requirement(id="REQ-002", text="The manager can reject an expense report and does not require a rejection reason."),
            Requirement(id="REQ-003", text="The system shall lock the account after five failed attempts."),
            Requirement(id="REQ-004", text="Only show the amendment button when the order is in Approved status."),
            Requirement(id="REQ-005", text="Never reject submitted expense reports after the payment cutoff."),
        ]

        ordered, rejection, lock, guard, prohibited = fallback_requirement_analysis(requirements)
        self.assertEqual(
            [(item["from_state"], item["to_state"]) for item in ordered["state_transitions"]],
            [("Approved", "Draft")],
        )
        self.assertEqual(
            [(item["from_state"], item["to_state"], item["guards"]) for item in rejection["state_transitions"]],
            [("Unknown", "Rejected", None)],
        )
        self.assertEqual(rejection["field_constraints"], [])
        self.assertEqual(
            [(item["from_state"], item["to_state"]) for item in lock["state_transitions"]],
            [("Unknown", "Locked")],
        )
        self.assertEqual(guard["state_transitions"], [])
        self.assertIn("Workflow", [risk["category"] for risk in guard["risk_signals"]])
        self.assertEqual(prohibited["state_transitions"], [])

    def test_blocked_or_displayed_state_changes_are_not_reported_as_transitions(self) -> None:
        controls = [
            "Prevent managers from rejecting an expense report after the payment cutoff.",
            "Do not allow managers to approve expense reports in Submitted status.",
            "Transition from Active to Locked is prohibited.",
            "Show the help text `Move from Draft to Approved`.",
        ]
        requirements = [Requirement(id=f"REQ-{index:03}", text=source) for index, source in enumerate(controls, start=1)]
        requirements.append(Requirement(id="REQ-005", text="When an expense report is returned, move the account from Active to Locked."))

        *blocked, account = fallback_requirement_analysis(requirements)
        for source, row in zip(controls, blocked):
            with self.subTest(source=source):
                self.assertEqual(row["state_transitions"], [])
        self.assertEqual(
            [(item["entity"], item["from_state"], item["to_state"]) for item in account["state_transitions"]],
            [("Account", "Active", "Locked")],
        )


if __name__ == "__main__":
    unittest.main()
