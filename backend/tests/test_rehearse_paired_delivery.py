"""Focused checks for the offline saved-delivery rehearsal."""

from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))

from rehearse_paired_delivery import Capture, _target, control_cases, resolved_context


class DeliveryRehearsalTests(unittest.TestCase):
    def test_resolves_both_frozen_target_aliases_without_rewriting_literal_data(self):
        notes = 'Page "{{target}}", observation "{target}".\nBrowser sample "code" is "{env.example}"'
        url = "http://127.0.0.1:9001/payload-editor"
        source = {"healthy_target_fixture": "payload-editor", "context": {"notes": notes}}
        selected = resolved_context(source, {"payload-editor": _target(url, "/payload-editor")})
        self.assertEqual(selected["notes"], notes.replace("{{target}}", url).replace("{target}", url))
        self.assertIn("{env.example}", selected["notes"])
        self.assertEqual(source["context"]["notes"], notes)
        for invalid in ("https://127.0.0.1:9001/payload-editor", "http://example.org:9001/payload-editor", f"{url}?state=mutant"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "expected loopback fixture"):
                _target(invalid, "/payload-editor")

    def test_authored_literal_control_uses_exact_scoped_case_and_context(self):
        value = "   Zürich–β\n  第二行\n"
        controls = [
            {
                "requirement_id": "frozen-id",
                "case": {
                    "title": "Keep literal payload",
                    "steps": [
                        {
                            "action": 'Enter browser sample "literal" into field "Payload"',
                            "expected": 'field "Payload" value should equal browser sample "literal"',
                        },
                        {
                            "action": "Inspect the result",
                            "expected": 'test id "saved-payload" text should equal browser sample "literal"',
                        },
                    ],
                },
            }
        ]
        prerequisite = {"name": "Synthetic case setup", "requirement_ids": ["REQ-42"]}
        plan = [{"requirement_id": "REQ-42", "scenarios": [{"id": "REQ-42-SCN-01"}]}]
        context = {
            "notes": f'Browser sample "literal" is {json.dumps(value)}',
            "execution_prerequisites": [prerequisite, {"name": "Unrelated", "requirement_ids": ["REQ-43"]}],
        }
        [case] = control_cases(controls, {"frozen-id": "REQ-42"}, plan, context)
        self.assertEqual(case["linked_requirement_ids"], ["REQ-42"])
        self.assertEqual(case["scenario_refs"], ["REQ-42-SCN-01"])
        self.assertEqual(case["execution_prerequisites"], [prerequisite])
        self.assertEqual([step["test_data"] for step in case["steps"]], [value, value])
        self.assertNotIn("test_data", controls[0]["case"]["steps"][0])

    def test_partial_capture_keeps_available_raw_evidence_and_exact_reason(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "baseline").mkdir()
            identity = {"workflow": "W05", "repeat": 1, "arm": "baseline", "cohort": "rehearsal"}
            capture = Capture(root, identity)
            raw = b'{"test_cases": [\n]}\n'
            capture.write("raw-control", raw, raw=True)
            routes = [{"path": "/export/json", "status": 422}]
            capture.finish("partial", "draft_override_required", routes)
            record = json.loads((capture.path / "record.json").read_bytes())
            run = json.loads((root / record["files"]["run"]["path"]).read_bytes())
            self.assertEqual({key: run[key] for key in identity}, identity)
            self.assertEqual((run["status"], run["reason"]), ("partial", "draft_override_required"))
            source = record["files"]["raw-control"]
            self.assertEqual(source["sha256"], sha256(raw).hexdigest())
            self.assertEqual((root / source["path"]).read_bytes(), raw)
            self.assertNotIn("exported", record["files"])
            requests = json.loads((root / record["files"]["requests"]["path"]).read_bytes())
            self.assertEqual(requests["routes"], routes)
            self.assertIn("physical_mock_requests", requests)
