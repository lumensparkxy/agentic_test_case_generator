from pathlib import Path
from contextlib import ExitStack
import sys
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.requirements_agent import _convert_to_requirements
from app.contracts.requirements import Requirement
from app.contracts.requirement_imports import ImportApplyInput
from app.services.requirement_source_evidence import bind_document_evidence, MAX_SOURCE_MATCHES
from app.services.requirement_reconciliation import build_preview, reconcile, canonical_requirements
from app.main import app, get_current_user
from app.models import AuthUser
from app.utils.requirements_text import normalize_requirement_payloads


SOURCE = (Path(__file__).parent / "fixtures" / "room-booking-requirements.md").read_text()


def extracted(quote, **kwargs):
    return Requirement(id="REQ-001", text="The system shall enforce the stated booking rule.", source_excerpt=quote, **kwargs)


def apply_rows(rows, current=(), import_id="import-1"):
    preview = build_preview("project", 2, list(current), rows, "rooms.md", "requirements.parse", import_id)
    decisions = [
        {
            "candidate_id": candidate.candidate_id,
            "action": "keep" if candidate.classification == "unchanged" else "add",
            "target_requirement_uid": candidate.target_requirement_uid if candidate.classification == "unchanged" else None,
        }
        for candidate in preview.candidates
    ]
    return reconcile(preview, ImportApplyInput(base_project_revision=2, idempotency_key=import_id, decisions=decisions))[0]


class RequirementSourceEvidenceTests(unittest.TestCase):
    def test_workflow_normalization_preserves_evidence_until_upload_binding(self):
        quote = "A different room in the same slot remains available."
        payload = [
            {
                "id": "REQ-42",
                "text": "The application must allow a different room to be booked.",
                "source_path": "rooms.md",
                "source_excerpt": quote,
                "original_requirement_ids": ["ROOM-205"],
                "source_hierarchy": ["rooms.md", "Availability"],
                "artifact_set_id": "model-set",
                "sources": [{"excerpt_verified": True}],
            }
        ]
        normalized = normalize_requirement_payloads(payload)
        self.assertEqual(normalized[0]["source_excerpt"], quote)
        self.assertNotIn("sources", normalized[0])
        self.assertNotIn("artifact_set_id", normalized[0])
        result = bind_document_evidence(_convert_to_requirements(normalized), [("rooms.md", SOURCE)])[0]
        self.assertEqual(result.id, "REQ-001")
        self.assertEqual(result.original_requirement_ids, ["ROOM-205"])
        self.assertEqual(result.sources[0].excerpt, quote)

    def test_upload_endpoint_binds_evidence_before_persistence_and_response(self):
        workflow = {
            "requirements": [extracted("A room may be booked.")],
            "approved": False,
            "review": {},
            "iteration_history": [],
            "coverage_metrics": {},
            "workflow_settings": {},
            "workflow_diagnostics": {},
        }
        app.dependency_overrides[get_current_user] = lambda: AuthUser(sub="source-test", name="Source test")
        try:
            with ExitStack() as stack:
                for name, value in (
                    ("enforce_billing_access", object()),
                    ("record_billing_consumption", None),
                    ("start_workflow_run", "source-run"),
                    ("complete_workflow_run", None),
                    ("record_usage_event", "source-event"),
                    ("extract_requirements", workflow),
                ):
                    stack.enter_context(patch(f"app.routers.requirements.{name}", return_value=value))
                persist = stack.enter_context(
                    patch(
                        "app.routers.requirements.persist_requirement_versions",
                        side_effect=lambda current_requirements, **kwargs: current_requirements,
                    )
                )
                with TestClient(app) as client:
                    response = client.post(
                        "/requirements/parse",
                        files={
                            "file": ("rooms.md", b"## ROOM-101\nA room may be booked.", "text/markdown"),
                        },
                    )
            self.assertEqual(response.status_code, 200, response.text)
            delivered = response.json()["requirements"][0]
            self.assertEqual(delivered["original_requirement_ids"], ["ROOM-101"])
            self.assertTrue(delivered["sources"][0]["excerpt_verified"])
            self.assertEqual(persist.call_args.kwargs["current_requirements"][0].sources[0].excerpt, "A room may be booked.")
        finally:
            app.dependency_overrides.clear()

    def test_split_children_keep_original_ids_and_verbatim_document_evidence(self):
        quotes = [
            ("ROOM-101", "Attendees is required and must be a whole number from 1 to 8 inclusive."),
            ("ROOM-101", "show an inline error beside Attendees and create no booking."),
            ("ROOM-205", "A different room in the same slot remains available."),
            ("ROOM-310", "Its status becomes Cancelled and the room/time slot becomes available."),
            ("ROOM-440", "After a browser refresh, My bookings lists that booking exactly once."),
        ]
        inputs = [extracted(quote).model_copy(update={"id": f"REQ-{index + 1:03d}"}) for index, (_, quote) in enumerate(quotes)]
        result = bind_document_evidence(inputs, [("rooms.md", SOURCE)])
        for requirement, (identifier, quote) in zip(result, quotes):
            self.assertEqual(requirement.original_requirement_ids, [identifier])
            self.assertEqual(requirement.sources[0].excerpt, quote)
            self.assertIn(requirement.sources[0].excerpt, SOURCE)
            self.assertTrue(requirement.sources[0].excerpt_verified)
            self.assertTrue(requirement.sources[0].source_section.startswith("Lines "))
        self.assertEqual(len({r.sources[0].source_id for r in result}), 1)
        self.assertEqual(len({r.sources[0].source_version for r in result}), 1)
        self.assertTrue(all(not r.sources for r in inputs))

    def test_whitespace_is_normalized_for_matching_but_original_text_is_retained(self):
        document = "## ROOM-101\nAttendees  must be\nwhole numbers."
        result = bind_document_evidence([extracted("Attendees must be whole numbers.")], [("rooms.md", document)])[0]
        self.assertEqual(result.sources[0].excerpt, "Attendees  must be\nwhole numbers.")
        for invalid in ("Attendees must be decimals.", "attendees must be whole numbers.", None):
            result = bind_document_evidence([extracted(invalid, original_requirement_ids=["ROOM-101"])], [("rooms.md", document)])[0]
            self.assertEqual(result.sources, [])
            self.assertEqual(result.original_requirement_ids, [])
            self.assertIsNone(result.source_excerpt)
            self.assertTrue(result.quality_flags)

    def test_model_id_claim_is_checked_against_matching_section_not_whole_document(self):
        result = bind_document_evidence(
            [extracted("A different room in the same slot remains available.", original_requirement_ids=["ROOM-101", "INVENTED-123"])],
            [("rooms.md", SOURCE)],
        )[0]
        self.assertEqual(result.original_requirement_ids, ["ROOM-205"])
        self.assertTrue(any("could not be verified" in flag for flag in result.quality_flags))

    def test_duplicate_original_ids_from_different_documents_remain_distinct(self):
        document = "## ROOM-101\nA room may be booked."
        result = bind_document_evidence([extracted("A room may be booked.")], [("one.md", document), ("two.md", document)])[0]
        self.assertEqual(len(result.sources), 2)
        self.assertEqual(len({s.source_id for s in result.sources}), 2)
        self.assertEqual([s.original_requirement_ids for s in result.sources], [["ROOM-101"], ["ROOM-101"]])
        saved = apply_rows([result])[0]
        self.assertEqual(len(saved.sources), 2)
        self.assertTrue(all(source.import_id == "import-1" for source in saved.sources))

    def test_missing_ids_are_not_inferred_from_normalized_ids(self):
        result = bind_document_evidence([extracted("A room may be booked.")], [("plain.md", "A room may be booked.")])[0]
        self.assertTrue(result.sources[0].excerpt_verified)
        self.assertEqual(result.original_requirement_ids, [])

    def test_uploaded_document_does_not_trust_model_source_or_persistence_identity(self):
        result = bind_document_evidence(
            [
                extracted(
                    "A room may be booked.",
                    source_system="jira",
                    source_path="invented.md",
                    source_issue_key="FAKE-999",
                    source_issue_url="https://example.test/invented",
                    artifact_set_id="model-set",
                    artifact_item_id="model-item",
                )
            ],
            [("actual.md", "A room may be booked.")],
        )[0]
        self.assertEqual(result.source_system, "file")
        self.assertEqual(result.source_path, "actual.md")
        self.assertIsNone(result.source_issue_key)
        self.assertIsNone(result.source_issue_url)
        self.assertIsNone(result.artifact_set_id)
        self.assertIsNone(result.artifact_item_id)

    def test_ambiguous_or_oversized_quotes_are_not_truncated_into_evidence(self):
        for document, quote in (("Book.\n" * (MAX_SOURCE_MATCHES + 1), "Book."), ("x" * 8001, "x" * 8001)):
            result = bind_document_evidence([extracted(quote)], [("rooms.md", document)])[0]
            self.assertFalse(result.sources)
            self.assertIsNone(result.source_excerpt)

    def test_legacy_records_never_synthesize_an_original_quote(self):
        legacy = Requirement(id="REQ-1", text="The system shall allow booking.")
        rows, _ = canonical_requirements("project", {"snapshot_id": "old", "payload": {"requirements": [legacy.model_dump()]}})
        self.assertEqual(rows[0].sources[0].excerpt, "")
        self.assertFalse(rows[0].sources[0].excerpt_verified)
        self.assertEqual(rows[0].sources[0].original_requirement_ids, [])

    def test_model_conversion_cannot_assert_verified_canonical_evidence(self):
        rows = _convert_to_requirements(
            [
                {
                    "id": "ROOM-101",
                    "text": "The system shall allow a room to be booked.",
                    "original_requirement_ids": ["ROOM-101"],
                    "source_excerpt": "A room may be booked.",
                    "sources": [{"source_id": "forged", "excerpt_verified": True}],
                }
            ]
        )
        self.assertEqual(rows[0].id, "REQ-001")
        self.assertEqual(rows[0].original_requirement_ids, ["ROOM-101"])
        self.assertEqual(rows[0].sources, [])

    def test_reimport_preserves_identity_and_approval_and_retains_additional_sources(self):
        quote = "A room may be booked."
        first = bind_document_evidence([extracted(quote)], [("one.md", "## ROOM-101\n" + quote)])
        baseline = apply_rows(first)
        baseline[0].review_status = "Approved"
        unchanged = apply_rows(first, baseline, "again")
        self.assertEqual(unchanged[0].requirement_uid, baseline[0].requirement_uid)
        self.assertEqual(unchanged[0].review_status, "Approved")
        self.assertEqual(len(unchanged[0].sources), 1)
        second = bind_document_evidence([extracted(quote)], [("two.md", "## BOOK-205\n" + quote)])
        merged = apply_rows(second, unchanged, "other-source")
        self.assertEqual(merged[0].original_requirement_ids, ["ROOM-101", "BOOK-205"])
        self.assertEqual(len(merged[0].sources), 2)
        self.assertEqual(merged[0].review_status, "Approved")

    def test_changed_source_keeps_identity_and_review_only_changes_for_affected_text(self):
        document = "## ROOM-101\nAttendees must be 1 to 8.\n## ROOM-205\nA different room remains available."
        rows = [
            Requirement(id="REQ-1", text="The system shall allow 1 to 8 attendees.", source_excerpt="Attendees must be 1 to 8."),
            Requirement(id="REQ-2", text="The system shall allow a different room.", source_excerpt="A different room remains available."),
        ]
        baseline = apply_rows(bind_document_evidence(rows, [("rooms.md", document)]))
        for requirement in baseline:
            requirement.review_status = "Approved"
        incoming = [row.model_copy(update={"text": row.text.replace("8", "6"), "source_excerpt": row.source_excerpt.replace("8", "6")}) for row in rows]
        incoming = bind_document_evidence(incoming, [("rooms.md", document.replace("8", "6"))])
        preview = build_preview("project", 2, baseline, incoming, "rooms.md", "requirements.parse", "changed")
        decision = ImportApplyInput(
            base_project_revision=2,
            idempotency_key="changed",
            decisions=[
                {"candidate_id": "incoming-1", "action": "update", "target_requirement_uid": baseline[0].requirement_uid},
                {"candidate_id": "incoming-2", "action": "keep", "target_requirement_uid": baseline[1].requirement_uid},
            ],
        )
        result, _, _ = reconcile(preview, decision)
        self.assertEqual([r.requirement_uid for r in result], [r.requirement_uid for r in baseline])
        self.assertEqual([r.id for r in result], [r.id for r in baseline])
        self.assertEqual([r.content_version for r in result], [2, 1])
        self.assertEqual([r.review_status for r in result], ["Needs Review", "Approved"])
        self.assertNotEqual(result[0].sources[0].source_version, result[0].sources[-1].source_version)
        self.assertEqual(result[0].sources[-1].original_requirement_ids, ["ROOM-101"])


if __name__ == "__main__":
    unittest.main()
