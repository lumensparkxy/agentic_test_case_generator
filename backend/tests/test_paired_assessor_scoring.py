"""Offline coverage for the frozen evaluator comparison thresholds."""

from hashlib import sha256
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.prepare_case_adjudication import FROZEN_MANIFEST, json_bytes, read_json
from scripts.score_paired_assessor import compare


class PairedAssessorScoringTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.pins = {"baseline": read_json(FROZEN_MANIFEST.read_bytes())["baseline"], "candidate": "c" * 40}
        self.examples = []
        self.labels = []
        self.captures = {"baseline": [], "candidate": []}
        for layer in ("structural", "design"):
            for verdict in ("valid", "defective"):
                key = f"{layer}-{verdict}"
                path = self._write(f"{key}.json", {"case": key})
                identity = {"id": key, "input_sha256": sha256(path.read_bytes()).hexdigest()}
                example = {**identity, "layer": layer, "file": path.name}
                self.examples.append(example)
                self.labels.append({**identity, "verdict": verdict, "critical": verdict == "defective", "reason": f"Example is {verdict}."})
                for arm in self.captures:
                    approved = verdict == "valid" or arm == "baseline"
                    if layer == "design":
                        native = self._write(f"{arm}-{key}.json", {"review_input": key, "rubric": arm})
                        native_hash = sha256(native.read_bytes()).hexdigest()
                        example.setdefault("review_inputs", {})[arm] = {"file": native.name, "input_sha256": native_hash}
                    output = (
                        {"approved": approved}
                        if layer == "structural"
                        else {
                            "input_hash": native_hash,
                            "design_status": "assessed_complete" if approved else "incomplete",
                            "execution_status": "blocked",
                            "status": "incomplete",
                        }
                    )
                    self.captures[arm].append({**identity, "status": "completed", "output": output})

    def _write(self, name, value):
        path = self.root / name
        path.write_bytes(json_bytes(value))
        return path

    def _score(self, *, cohort="independent", labels=True, manifest_hash=None, candidate_pin=None):
        manifest = self._write(
            "manifest.json",
            {"cohort": cohort, **{f"{arm}_commit": pin for arm, pin in self.pins.items()}, "examples": self.examples},
        )
        digest = sha256(manifest.read_bytes()).hexdigest()
        identity = {"cohort": cohort, "manifest_sha256": digest}
        captures = {
            arm: self._write(
                f"{arm}.json",
                {**identity, "arm": arm, "commit": candidate_pin if arm == "candidate" and candidate_pin else self.pins[arm], "rows": rows},
            )
            for arm, rows in self.captures.items()
        }
        labels_path = self._write("labels.json", {**identity, "rows": self.labels}) if labels else None
        return compare(manifest, manifest_hash or digest, captures["baseline"], captures["candidate"], labels_path)

    def test_compares_layers_using_design_status_despite_blocked_execution(self):
        result = self._score()
        self.assertEqual(result["paired_human_thresholds"], "pass")
        for layer in result["layers"].values():
            self.assertEqual(layer["known_pairs"], 2)
            self.assertEqual(layer["baseline"]["false_approvals"], 1)
            self.assertEqual(layer["baseline"]["critical_false_approvals"], 1)
            self.assertEqual(layer["candidate"]["critical_false_approvals"], 0)
            self.assertEqual(layer["candidate"]["false_rejections"], 0)
        self.assertEqual(self._score(cohort="rehearsal")["paired_human_thresholds"], "rehearsal")

    def test_reports_missing_failed_and_unknown_evidence_without_passing(self):
        missing = self.captures["baseline"].pop(0)
        failed = self.captures["candidate"][1]
        failed.pop("output")
        failed.update(status="failed", reason="Capture crashed")
        self.captures["candidate"][2]["output"]["design_status"] = "unknown"
        self.labels[0].update(verdict="unknown", reason="Missing authoritative outcome")
        self.labels.pop()
        result = self._score()
        self.assertEqual(result["paired_human_thresholds"], "incomplete")
        layers = result["layers"]
        self.assertEqual(layers["structural"]["baseline"]["observed"]["missing"], 1)
        self.assertEqual(layers["structural"]["candidate"]["observed"]["failed"], 1)
        self.assertEqual(layers["structural"]["unknown_labels"], 1)
        self.assertEqual(layers["design"]["candidate"]["observed"]["unknown"], 1)
        self.assertEqual(layers["design"]["missing_labels"], 1)
        self.assertEqual(result["examples"][0]["id"], missing["id"])
        self.assertEqual(result["examples"][1]["candidate"]["reason"], "Capture crashed")
        self.assertEqual(self._score(labels=False)["layers"]["design"]["missing_labels"], 2)

    def test_rejects_drifted_manifest_bytes_input_bytes_capture_and_label(self):
        with self.assertRaisesRegex(ValueError, "manifest SHA-256"):
            self._score(manifest_hash="a" * 64)
        with self.assertRaisesRegex(ValueError, "Evidence identity"):
            self._score(candidate_pin="d" * 40)
        self._write("design-valid.json", {"case": "different"})
        with self.assertRaisesRegex(ValueError, "input bytes differ"):
            self._score()
        self._write("design-valid.json", {"case": "design-valid"})
        for rows in (self.captures["candidate"], self.labels):
            original = rows[0]["input_sha256"]
            rows[0]["input_sha256"] = "b" * 64
            with self.assertRaisesRegex(ValueError, "different input"):
                self._score()
            rows[0]["input_sha256"] = original
        self.labels.append(self.labels[0])
        with self.assertRaisesRegex(ValueError, "duplicate evidence row"):
            self._score()

    def test_fails_on_critical_false_approval_or_increased_false_rejection(self):
        candidate = self.captures["candidate"]
        candidate[1]["output"]["approved"] = True
        result = self._score()
        self.assertEqual(result["paired_human_thresholds"], "fail")
        self.assertEqual(result["layers"]["structural"]["candidate"]["critical_false_approvals"], 1)
        candidate[1]["output"]["approved"] = False
        candidate[0]["output"]["approved"] = False
        result = self._score()
        self.assertEqual(result["paired_human_thresholds"], "fail")
        self.assertEqual(result["layers"]["structural"]["candidate"]["false_rejections"], 1)
        self.captures["baseline"][0]["output"]["approved"] = False
        self.assertEqual(self._score()["paired_human_thresholds"], "pass")

    def test_unknown_provider_or_groups_cannot_be_used_as_known_rejection(self):
        result = self.captures["candidate"][3]["output"]
        for unavailable in ({"model_status": "unknown"}, {"diagnostics": {"unverified_group_count": 1}}):
            with self.subTest(unavailable=unavailable):
                result.update(unavailable)
                report = self._score()
                self.assertEqual(report["paired_human_thresholds"], "incomplete")
                self.assertEqual(report["layers"]["design"]["candidate"]["observed"]["unknown"], 1)
                for key in unavailable:
                    del result[key]

    def test_rejects_a_design_output_swapped_between_inputs(self):
        candidate = self.captures["candidate"]
        candidate[3]["output"] = candidate[2]["output"]
        with self.assertRaisesRegex(ValueError, "different design projection"):
            self._score()

    def test_requires_valid_and_critical_defective_reference_examples(self):
        for critical in (False, True):
            with self.subTest(critical=critical):
                for label in self.labels:
                    label.update(verdict="defective" if critical else "valid", critical=critical)
                self.assertEqual(self._score()["paired_human_thresholds"], "incomplete")
