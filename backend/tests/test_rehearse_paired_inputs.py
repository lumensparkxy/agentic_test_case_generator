"""Provider-free unit checks for frozen input rehearsal evidence."""

from hashlib import sha256
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import rehearse_paired_inputs as rehearsal


class RehearsePairedInputsTests(unittest.TestCase):
    def test_loads_only_hash_bound_generation_inputs(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            name = "W01-booking-state"
            source = {"requirements": [{"id": "R1", "text": "Use the same record."}], "context": {"notes": "Fixture only"}}
            workflow = {"generation_input": source}
            calibration = rehearsal.json_bytes({"baseline": "baseline", "workflows": {name: workflow}})
            held_out = rehearsal.json_bytes({"baseline": "baseline", "workflows": {}})
            (root / "calibration-inputs.json").write_bytes(calibration)
            (root / "held-out-inputs.json").write_bytes(held_out)
            manifest = {
                "baseline": "baseline",
                "content_hashes": {"calibration_inputs": sha256(calibration).hexdigest(), "held_out_inputs": sha256(held_out).hexdigest()},
                "workflow_index": {name: {"generation_input_sha256": rehearsal.digest(source)}},
            }
            path = root / "manifest.json"
            path.write_bytes(rehearsal.json_bytes(manifest))
            with patch.object(rehearsal, "FROZEN_MANIFEST", path):
                self.assertEqual(rehearsal.generation_inputs(root), (manifest, {name: workflow}))
                (root / "calibration-inputs.json").write_bytes(calibration + b" ")
                with self.assertRaisesRegex(ValueError, "Changed frozen calibration_inputs"):
                    rehearsal.generation_inputs(root)

    def test_non_numeric_labels_bind_to_exact_saved_quotes_and_lines(self):
        frozen = [{"id": "JSON-VALID", "text": "Preserve é  literally. "}]
        document = rehearsal.markdown_source(frozen)
        row = {
            "id": "REQ-001",
            "text": frozen[0]["text"],
            "requirement_uid": "stable-uid",
            "sources": [
                {
                    "label": "payload.md",
                    "source_version": sha256(document.encode()).hexdigest(),
                    "excerpt_verified": True,
                    "excerpt": "Preserve é  literally.",
                    "source_section": "Lines 2-2",
                    "original_requirement_ids": [],
                }
            ],
        }
        project = {"current_snapshots": {"requirements": {"payload": {"requirements": [row]}}}}
        mapping = rehearsal.exact_saved_rows(project, frozen, document, "payload.md")
        self.assertEqual(mapping["JSON-VALID"]["requirement_uid"], "stable-uid")
        row["text"] = row["text"].strip()
        with self.assertRaisesRegex(ValueError, "Saved requirement differs"):
            rehearsal.exact_saved_rows(project, frozen, document, "payload.md")

    def test_context_preserves_literals_and_uses_saved_prerequisite_ids(self):
        frozen = [{"id": "JSON-VALID", "text": "Accept the provided JSON."}]
        context = {
            "requirements": frozen,
            "notes": 'Send {target} exactly: {\n  "value": "β  test"\n}\n',
            "execution_prerequisites": [{"name": "fixture", "requirement_ids": ["JSON-VALID"]}],
        }
        output = rehearsal.context_payload(context, frozen, [{"id": "REQ-003"}])
        self.assertEqual(output["notes"], context["notes"])
        self.assertEqual(output["execution_prerequisites"][0]["requirement_ids"], ["REQ-003"])
        self.assertEqual(context["execution_prerequisites"][0]["requirement_ids"], ["JSON-VALID"])
        self.assertNotIn("requirements", output)

    def test_grounded_context_accepts_defaults_but_rejects_mutated_literal_and_missing_row(self):
        frozen = {"ui_elements": [{"id": "last", "name": "Send for correction", "description": "Show β  and keep the state.\n"}]}
        actual = {"ui_elements": [{**frozen["ui_elements"][0], "source_id": None, "element_type": "Other"}], "artifact_sources": []}
        self.assertTrue(rehearsal.matches_supplied(frozen, actual))
        actual["ui_elements"][0]["description"] = "Show β and keep the state.\n"
        self.assertFalse(rehearsal.matches_supplied(frozen, actual))
        self.assertFalse(rehearsal.matches_supplied(frozen, {"ui_elements": []}))

    def test_revised_booking_document_preserves_exact_labeled_order(self):
        original = [{"id": "ROOM-101", "text": "Accept 1–8."}, {"id": "ROOM-205", "text": "Preserve the occupied slot."}]
        revision = "# Booking\n\n## ROOM-101 — Capacity\n\nAccept 1–6.\n\n## ROOM-205 — Conflict\n\nPreserve the occupied slot.\n"
        self.assertEqual(rehearsal.revised_rows(revision, original), [{"id": "ROOM-101", "text": "Accept 1–6."}, original[1]])
        with self.assertRaisesRegex(ValueError, "changed the frozen requirement IDs"):
            rehearsal.revised_rows(revision.replace("ROOM-205", "ROOM-207"), original)
