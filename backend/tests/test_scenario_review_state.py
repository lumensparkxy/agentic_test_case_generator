import unittest
from copy import deepcopy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.scenario_review_state import carry_scenario_reviews, current_scenario_reviews, scenario_index
from app.contracts.use_case_reviews import UseCaseReviewRequest
from pydantic import ValidationError


class ScenarioReviewStateTests(unittest.TestCase):
    def test_regeneration_retains_only_identical_scenario_and_source(self):
        old = {
            "coverage_plan": [
                {"requirement_id": "R1", "requirement_text": "Original source", "scenarios": [{"id": "S1", "title": "Same"}, {"id": "S2", "title": "Before"}]}
            ]
        }
        items = current_scenario_reviews(old, {}, "v1")
        for item in items.values():
            item.update(status="approved", quality_flags=["Duplicate"], reviewer_user_id="reviewer")
        new = deepcopy(old)
        new["coverage_plan"][0]["scenarios"][1]["title"] = "After"
        new["coverage_plan"][0]["scenarios"].append({"id": "S3", "title": "New"})
        metadata = {"scenario_reviews": {"snapshot_id": "v1", "items": items}}
        carried = carry_scenario_reviews(new, old, metadata, "v1", "v2")["items"]
        self.assertEqual(carried['["R1","S1"]']["status"], "approved")
        self.assertEqual(carried['["R1","S1"]']["reviewer_user_id"], "reviewer")
        self.assertEqual(carried['["R1","S2"]']["status"], "needs_review")
        self.assertEqual(carried['["R1","S3"]']["status"], "needs_review")
        analysis_changed = deepcopy(old)
        analysis_changed["requirement_analysis"] = [{"requirement_id": "R1", "risk_signals": ["New risk"]}]
        self.assertTrue(all(item["status"] == "needs_review" for item in carry_scenario_reviews(analysis_changed, old, metadata, "v1", "v3")["items"].values()))
        new["coverage_plan"][0]["requirement_text"] = "Changed source"
        self.assertTrue(all(item["status"] == "needs_review" for item in carry_scenario_reviews(new, old, metadata, "v1", "v3")["items"].values()))

    def test_duplicate_identity_and_invalid_updates_are_rejected(self):
        with self.assertRaises(ValueError):
            scenario_index({"coverage_plan": [{"requirement_id": "R1", "scenarios": [{"id": "S1"}, {"id": "S1"}]}]})
        update = {"requirement_id": "R1", "scenario_id": "S1", "status": "approved", "quality_flags": []}
        for updates in [[], [update, update], [{**update, "quality_flags": ["invented"]}]]:
            with self.assertRaises(ValidationError):
                UseCaseReviewRequest(snapshot_id="v1", base_project_revision=1, decision="review_scenarios", scenario_reviews=updates)

    def test_other_snapshot_metadata_cannot_approve_rows(self):
        payload = {"coverage_plan": [{"requirement_id": "R1", "scenarios": [{"id": "S1"}]}]}
        items = current_scenario_reviews(payload, {"latest_human_review": {"snapshot_id": "other", "decision": "approve"}}, "current")
        self.assertEqual(next(iter(items.values()))["status"], "needs_review")
