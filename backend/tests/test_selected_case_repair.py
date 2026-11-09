from copy import deepcopy
import json
from unittest import TestCase
from unittest.mock import patch

from app.contracts.selected_case_repair import SelectedCaseRepairInput

from fastapi import HTTPException

from app.agents import substantive_review, test_case_agent
from app.agents.substantive_review import bind_reviewed_inputs, review_input, review_prompt, validate_assessment
from app.contracts.test_cases import GenerateTestCasesResponse, TestStep as SavedStep
from app.services import project_draft_admission as admission
from app.services import selected_case_repair as repair
from app.services.suite_quality import build_suite_design_proof, reviewed_case_content_hash
from test_project_draft_admission import SOURCE_IDS, _AdmissionFixture
from test_suite_quality import attach_suite, fixture


class SelectedRepairEvidenceTests(TestCase):
    def setUp(self):
        self.project, payload, self.metadata = fixture()
        row = payload["test_cases"][0]
        sibling = deepcopy(row)
        sibling.update(id="TC-2", artifact_item_id="item-2", artifact_version_id="version-2", title="A preserved sibling")
        payload["test_cases"].append(sibling)
        self.assessment = payload["substantive_assessment"]
        self.assessment["case_grounding"].append({"test_case_id": "TC-2", "reason": "Preserved grounded case.", "prerequisites": []})
        self.assessment.update(status="incomplete", model_status="incomplete", design_status="incomplete")
        self.assessment["obligations"][0].update(status="unmet", source_quote="exact confirmation number")
        reviewed_hash = reviewed_case_content_hash(payload["test_cases"])
        self.assessment["reviewed_case_content_hash"] = reviewed_hash
        payload["generation_evidence"]["design_precheck"]["case_content_hash"] = reviewed_hash
        self.suite = self.attach(payload)
        self.enterContext(patch.object(repair, "get_project_stage_snapshot", side_effect=lambda *_a, **_k: self.suite))
        self.defects = self.enterContext(patch.object(repair, "_defect_recorded", return_value=False))

    def attach(self, payload):
        proof = build_suite_design_proof(self.project, payload, self.metadata, plan_snapshot_id="plan-v1")
        self.assertIsNotNone(proof)
        return attach_suite(self.project, payload, self.metadata, proof=proof)

    def source(self):
        return repair._repair_source(object(), self.project, object(), self.suite.snapshot_id, "TC-1")

    def response(self, source, *, new_step=2, action="Read the stored checkout confirmation number.", expected="The order records confirmation number β-01."):
        prior = source.case
        added = SavedStep(step=new_step, action=action, expected=expected)
        candidate = prior.model_copy(update={"steps": [*prior.steps, added]})
        target = source.targets[0]
        selected = repair._selected_obligation(target)
        data, input_hash, _ = review_input([candidate], [source.requirement], source.context, scope_plan=[source.plan], selected_obligation=selected)
        self.assertIn("Decide independently", review_prompt(data, input_hash))
        raw = {
            "input_hash": input_hash,
            "obligations": [
                {
                    "requirement_id": target.requirement_id,
                    "source_field": "text",
                    "obligation": target.obligation,
                    "status": "covered",
                    "reason": "The new observation checks the recorded confirmation value.",
                    "evidence": [{"test_case_id": prior.id, "step": added.step}],
                }
            ],
            "case_grounding": [{"test_case_id": prior.id, "reason": "Observed checkout result.", "prerequisites": []}],
        }
        checked = bind_reviewed_inputs(validate_assessment(json.dumps(raw), data, input_hash), data)
        self.assertEqual(checked["obligations"][0]["source_quote"], target.source_quote)
        return GenerateTestCasesResponse(test_cases=[candidate], substantive_assessment=checked)

    def test_stable_target_requires_exact_step_and_excludes_application_defects(self):
        original = self.source().targets[0]
        self.assertEqual(self.source().targets[0], original)
        self.assertEqual(original.step_numbers, [1])
        tampered = deepcopy(self.suite.payload)
        tampered["substantive_assessment"]["obligations"][0]["evidence"][0]["expected"] = "An invented observation."
        self.suite = self.attach(tampered)
        self.assertIsNone(self.source())
        self.suite = self.attach({**tampered, "substantive_assessment": self.assessment})
        self.defects.return_value = True
        self.assertIsNone(self.source())

    def test_source_staleness_and_unknown_assessment_are_ineligible(self):
        self.project.stage_state["test_cases"].stale = True
        self.assertIsNone(self.source())
        self.project.stage_state["test_cases"].stale = False
        unverified = deepcopy(self.suite.payload)
        unverified["substantive_assessment"]["model_status"] = "unknown"
        self.suite = self.attach(unverified)
        self.assertIsNone(self.source())

    def test_invalid_version_does_not_reach_admission_billing_or_provider(self):
        source = self.source()
        payload = SelectedCaseRepairInput(
            snapshot_id=source.suite.snapshot_id,
            base_project_revision=self.project.current_revision,
            case_id=source.case.id,
            case_version_id="obsolete-version",
            target_id=source.targets[0].target_id,
        )
        with (
            patch.object(repair, "_receipt", return_value=None),
            patch.object(repair, "draft_admission_client", return_value=object()),
            patch.object(repair, "approval_test_cases_enabled", return_value=True),
            patch.object(repair, "get_project", return_value=self.project),
            patch.object(repair, "claim_draft_attempt") as claim,
            patch.object(repair, "enforce_billing_access") as billing,
            patch.object(repair, "refine_test_cases") as provider,
            self.assertRaises(HTTPException) as failure,
        ):
            repair.repair_selected_case(self.project.project_id, payload, object(), "exact-request")
        self.assertEqual(failure.exception.detail["code"], "selected_repair_target_changed")
        for callback in (claim, billing, provider):
            callback.assert_not_called()

    def test_observation_preserves_exact_literal_and_sibling_without_promoting_suite(self):
        source = self.source()
        response = self.response(source)
        result, evidence = repair._verified_observation(response, source, source.targets[0])
        self.assertEqual([case.id for case in result.test_cases], ["TC-1", "TC-2"])
        self.assertEqual(result.test_cases[0].steps[0], source.case.steps[0])
        self.assertEqual(result.test_cases[0].steps[0].test_data, "  β-01  ")
        self.assertEqual(result.test_cases[1], source.baseline.test_cases[1])
        self.assertEqual(result.test_cases[0].status, "Draft")
        self.assertEqual(result.substantive_assessment["status"], "unknown")
        self.assertFalse(result.approved)
        self.assertIsNone(result.generation_evidence.design_precheck)
        self.assertEqual(evidence["added_step"], 2)
        self.assertEqual(evidence["target"]["target_id"], source.targets[0].target_id)

    def test_refine_agent_keeps_selected_critic_binding_through_serialization(self):
        # Saved generation already normalizes source tags and empty source references.
        payload = deepcopy(self.suite.payload)
        for row in payload["test_cases"]:
            row.update(tags=row["linked_requirement_ids"], source_refs=[])
        reviewed_hash = reviewed_case_content_hash(payload["test_cases"])
        payload["substantive_assessment"]["reviewed_case_content_hash"] = reviewed_hash
        payload["generation_evidence"]["design_precheck"]["case_content_hash"] = reviewed_hash
        self.suite = self.attach(payload)
        source = self.source()
        target = source.targets[0]
        model_input, specialist_inputs = repair._model_input(self.project.project_id, self.project.current_revision, source, target)
        candidate = self.response(source).test_cases[0]
        workflow = {"test_cases": [candidate.model_dump(mode="json")], "workflow_diagnostics": {"status": "completed"}}

        def assess(data, input_hash, _settings, _progress):
            self.assertEqual(data["selected_obligation"], repair._selected_obligation(target))
            self.assertEqual(data["test_cases"][0]["steps"][0]["test_data"], "  β-01  ")
            raw = {
                "input_hash": input_hash,
                "obligations": [
                    {
                        "requirement_id": target.requirement_id,
                        "source_field": "text",
                        "obligation": target.obligation,
                        "status": "covered",
                        "reason": "An exact recorded value is observed.",
                        "evidence": [{"test_case_id": candidate.id, "step": candidate.steps[-1].step}],
                    }
                ],
                "case_grounding": [{"test_case_id": candidate.id, "reason": "Observed checkout record.", "prerequisites": []}],
            }
            return validate_assessment(json.dumps(raw), data, input_hash)

        with (
            patch.object(test_case_agent, "_get_model_settings_or_none") as settings,
            patch.object(test_case_agent, "_run_workflow_sync", return_value=workflow),
            patch.object(substantive_review, "_assess_review_data", side_effect=assess),
        ):
            settings.return_value.model_name = "gemini-local-fixture"
            raw = test_case_agent.refine_test_cases(model_input, operation=repair.REPAIR_OPERATION, **specialist_inputs)
        response = GenerateTestCasesResponse.model_validate(raw)
        repaired, _ = repair._verified_observation(response, source, target)
        self.assertEqual(repaired.test_cases[0].steps[-1], candidate.steps[-1])

    def test_rejects_mutation_weakening_unverified_source_and_unrelated_citation(self):
        source = self.source()
        responses = []
        for action, expected in (
            ("Observe the value, then click Submit.", "The order records confirmation number β-01."),
            ("Read the stored checkout confirmation number.", "The test passes."),
        ):
            responses.append(self.response(source, action=action, expected=expected))
        weakened = self.response(source)
        weakened.test_cases[0].steps[0] = weakened.test_cases[0].steps[0].model_copy(update={"expected": "Some order is confirmed."})
        responses.append(weakened)
        wrong_source = self.response(source)
        wrong_source.substantive_assessment["reviewed_source_binding"] = {"requirement_hashes": {}, "context": "absent"}
        responses.append(wrong_source)
        prior_citation = self.response(source)
        cited = source.case.steps[0]
        prior_citation.substantive_assessment["obligations"][0]["evidence"] = [
            {"test_case_id": source.case.id, "step": cited.step, "action": cited.action, "expected": cited.expected}
        ]
        responses.append(prior_citation)
        for response in responses:
            with self.subTest(response=response), self.assertRaises(HTTPException) as failure:
                repair._verified_observation(response, source, source.targets[0])
            self.assertEqual(failure.exception.detail["code"], "selected_repair_unverified")


class SelectedRepairAdmissionTests(_AdmissionFixture, TestCase):
    BINDING = {
        "case_id": "TC-1",
        "case_version_id": "v1",
        "case_version_number": 1,
        "target_id": "repair_" + "a" * 64,
        "request_fingerprint": "b" * 64,
    }

    def claim_repair(self, request_id="repair-exact"):
        return admission.claim_draft_attempt(
            self.client,
            project_id=self.project.id,
            actor_id="owner",
            request_id=request_id,
            operation=admission.REPAIR_OPERATION,
            source_snapshot_ids=SOURCE_IDS,
            base_revision=8,
            max_requests=8,
            deadline_seconds=180,
            settings_fingerprint="c" * 64,
            selected_repair=self.BINDING,
        )

    def result(self, fingerprint=None):
        return admission.read_selected_repair_result(
            self.client, project_id=self.project.id, actor_id="owner", request_id="repair-exact", request_fingerprint=fingerprint
        )

    def repair_snapshot(self, **case_fields):
        return self.snapshot(
            request_id="repair-exact",
            operation=admission.REPAIR_OPERATION,
            source_snapshot_id="plan-v1",
            metadata={
                "source_snapshot_ids": {"requirements": "scope-v1", "use_cases": "plan-v1"},
                "source_test_cases_snapshot_id": "suite-v1",
                "selected_repair": self.BINDING,
                "generation_request_fingerprint": self.BINDING["request_fingerprint"],
            },
            payload={"test_cases": [{"id": "TC-1", "artifact_version_id": "v2", "artifact_version_number": 2, **case_fields}]},
        )

    def test_receipt_replays_after_revision_changes_and_rejects_wrong_case_version(self):
        claim = self.claim_repair()
        self.assertIsNone(self.result())
        self.assert_conflict("project_draft_result_mismatch", lambda: self.publish(claim, self.repair_snapshot(artifact_version_id="v1")))
        self.publish(claim, self.repair_snapshot())
        self.project.update({"current_revision": 10})
        self.assertEqual(self.result("b" * 64)["snapshot_id"], "reviewed-suite-v2")
        self.assert_conflict("project_draft_request_mismatch", lambda: self.result("d" * 64))

    def test_dispatched_loss_fences_all_attempts_and_exact_release_revokes_worker(self):
        claim = self.claim_repair()
        self.assert_conflict("project_draft_busy", self.claim)
        self.assertIsNone(admission.read_draft_attempt(self.client, self.project.id, "owner"))
        self.assertEqual(claim.reserve(), 25)
        claim.stop()
        state = admission.read_selected_repair_admission(self.client, self.project.id, "owner")
        self.assertTrue(state["blocked"])
        self.assertEqual(state["attempt"]["case_id"], "TC-1")
        self.assertEqual(state["attempt"]["state"], "interrupted")
        self.assert_conflict("project_draft_release_mismatch", lambda: self.release(claim))
        self.assertTrue(admission.release_draft_attempt(self.client, self.project.id, "owner", claim.operation_id, claim.fence, selected_repair=True))
        self.assert_conflict("project_draft_claim_lost", lambda: self.publish(claim, self.repair_snapshot()))
        self.assertGreater(self.claim().fence, claim.fence)
