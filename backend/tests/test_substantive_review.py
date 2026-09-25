import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.substantive_review import (
    review_input,
    validate_assessment,
    assess_delivered_suite,
    apply_assessment,
    assessment_tasks,
)
from app.models import Requirement, TestCase


def fixture():
    req = Requirement(id="ROOM-440", text="Double-click and lost-response retries must keep one booking with the same identity after refresh.")
    case = TestCase(
        id="TC-RETRY",
        title="Retry",
        description="Retry booking",
        linked_requirement_ids=[req.id],
        steps=[{"step": 1, "action": "Double-click Create then refresh", "expected": "Exactly one booking with original identity B1 remains"}],
        expected_result="One booking B1",
    )
    data, digest, _ = review_input([case], [req], None)
    evidence = {"test_case_id": case.id, **case.steps[0].model_dump(include={"step", "action", "expected"})}
    output = {
        "input_hash": digest,
        "obligations": [
            {
                "requirement_id": req.id,
                "source_quote": "Double-click",
                "obligation": "Durable double-click identity and count",
                "status": "covered",
                "reason": "Step exercises duplicate and refresh",
                "evidence": [evidence],
            },
            {
                "requirement_id": req.id,
                "source_quote": "lost-response retries",
                "obligation": "Durable lost-response retry identity and count",
                "status": "unmet",
                "reason": "No lost response trigger is exercised",
                "evidence": [],
            },
        ],
        "case_grounding": [{"test_case_id": case.id, "reason": "Synthetic booking must exist", "prerequisites": []}],
    }
    return req, case, data, digest, output


class SubstantiveReviewTests(unittest.TestCase):
    def test_incomplete_is_not_closed_by_links_or_high_generator_score(self):
        req, case, data, digest, output = fixture()
        assessment = validate_assessment(json.dumps(output), data, digest)
        response = {
            "test_cases": [case],
            "generation_tasks": [],
            "approved": True,
            "review": {"approved": True, "score": 100},
            "workflow_diagnostics": {"status": "completed"},
            "generation_evidence": {},
            "coverage_metrics": {},
        }
        result = apply_assessment(response, assessment)
        self.assertFalse(result["approved"])
        self.assertEqual(result["substantive_assessment"]["status"], "incomplete")
        self.assertIn("lost-response", result["generation_tasks"][0]["reason"])
        self.assertEqual(result["test_cases"], [case])

    def test_complete_assessment_does_not_override_other_rejection(self):
        _, case, data, digest, output = fixture()
        output["obligations"] = output["obligations"][:1]
        assessment = validate_assessment(json.dumps(output), data, digest)
        self.assertEqual(assessment["status"], "assessed_complete")
        response = {
            "test_cases": [case],
            "generation_tasks": [],
            "approved": False,
            "review": {"approved": False},
            "workflow_diagnostics": {},
            "generation_evidence": {},
            "coverage_metrics": {},
        }
        self.assertFalse(apply_assessment(response, assessment)["approved"])

    def test_stale_or_fabricated_or_missing_citations_fail_closed(self):
        _, _, data, digest, output = fixture()
        for mutation in (
            lambda o: o.update(input_hash="stale"),
            lambda o: o["obligations"][0].update(source_quote="mandatory debounce"),
            lambda o: o["obligations"][0]["evidence"][0].update(expected="Booking created"),
            lambda o: o["obligations"][0].update(evidence=[]),
            lambda o: o.update(case_grounding=[]),
            lambda o: o["obligations"][0].update(requirement_id="invented"),
        ):
            with self.subTest(mutation=mutation):
                changed = json.loads(json.dumps(output))
                mutation(changed)
                with self.assertRaises(ValueError):
                    validate_assessment(json.dumps(changed), data, digest)

    def test_separate_refresh_case_cannot_complete_trigger_case(self):
        _, case, data, digest, output = fixture()
        other = case.model_dump()
        other["id"] = "REFRESH-ONLY"
        data["test_cases"].append(other)
        output["case_grounding"].append({"test_case_id": "REFRESH-ONLY", "reason": "Synthetic", "prerequisites": []})
        output["obligations"][0]["evidence"].append({**output["obligations"][0]["evidence"][0], "test_case_id": "REFRESH-ONLY"})
        with self.assertRaisesRegex(ValueError, "unrelated cases"):
            validate_assessment(json.dumps(output), data, digest)

    def test_required_assumption_or_contradiction_is_gap_optional_exploration_is_not(self):
        _, case, data, digest, output = fixture()
        output["obligations"] = output["obligations"][:1]
        for status in ("unsupported_fact", "explicit_assumption", "missing_prerequisite", "contradiction"):
            item = {"description": "Provision Alice before running", "status": status, "required": True, "reason": "Not supplied by source"}
            output["case_grounding"][0]["prerequisites"] = [item]
            result = validate_assessment(json.dumps(output), data, digest)
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(assessment_tasks(result, [case])[0]["source_test_case_id"], case.id)
            item["required"] = False
            self.assertEqual(validate_assessment(json.dumps(output), data, digest)["status"], "assessed_complete")

    def test_no_model_timeout_malformed_output_and_size_limit_are_unknown(self):
        req, case, _, _, _ = fixture()
        with patch("app.agents.substantive_review._call_critic") as call:
            self.assertEqual(assess_delivered_suite([case], [req], None)["status"], "unknown")
            call.assert_not_called()
            for result in (RuntimeError("timeout"), "{}", "not JSON"):
                call.side_effect = result if isinstance(result, Exception) else None
                call.return_value = result
                self.assertEqual(assess_delivered_suite([case], [req], None, settings=object())["status"], "unknown")
            call.reset_mock()
            call.side_effect = None
            with patch("app.agents.substantive_review.MAX_INPUT_BYTES", 10):
                self.assertEqual(assess_delivered_suite([case], [req], None, settings=object())["status"], "unknown")
            call.assert_not_called()

    def test_explicit_business_source_ids_survive_delivery_without_promoting_tags(self):
        from app.agents.test_case_hydration import _hydrate_test_cases
        from app.agents.test_case_coverage import _extract_linked_requirement_ids_from_test_case

        _, case, _, _, _ = fixture()
        raw = case.model_dump(mode="json")
        raw["tags"] = ["priority:high", "scenario:boundary", "unrelated-label"]
        delivered = _hydrate_test_cases([raw])
        self.assertEqual(delivered[0].linked_requirement_ids, ["ROOM-440"])
        self.assertEqual(_extract_linked_requirement_ids_from_test_case(raw, {"OTHER"}), [])
        self.assertEqual(_extract_linked_requirement_ids_from_test_case({"tags": raw["tags"]}), [])

    def test_saved_payload_keeps_assessment_and_gap_details(self):
        from app.models import GenerateTestCasesResponse
        from app.routers.testcases import _test_case_project_payload

        _, case, data, digest, output = fixture()
        assessment = validate_assessment(json.dumps(output), data, digest)
        response = GenerateTestCasesResponse(test_cases=[case], substantive_assessment=assessment, generation_tasks=assessment_tasks(assessment, [case]))
        saved = _test_case_project_payload(response)
        self.assertEqual(saved["substantive_assessment"], assessment)
        self.assertEqual(saved["generation_tasks"], response.generation_tasks)

    def test_transport_bounded_and_complete_source_not_truncated(self):
        req, case, _, digest, output = fixture()
        from app.agents.substantive_review import _call_critic, review_prompt

        with patch("app.agents.substantive_review.genai.Client") as client:
            client.return_value.__enter__.return_value.models.generate_content.return_value = SimpleNamespace(text=json.dumps(output))
            _call_critic(SimpleNamespace(gemini_api_key="synthetic", model_name="fixture"), "prompt")
            config = client.return_value.__enter__.return_value.models.generate_content.call_args.kwargs["config"]
            self.assertEqual(config.http_options.timeout, 60000)
            self.assertEqual(config.http_options.retry_options.attempts, 1)
        data, _, _ = review_input([case], [req], None, scope_plan=[{"requirement_id": req.id, "scenarios": []}])
        self.assertEqual(data["scope"], "selected_scenarios")
        self.assertIn(req.text, review_prompt(data, digest))

    def test_review_binding_covers_context_and_steps_not_later_version_ids(self):
        req, case, _, digest, _ = fixture()
        versioned = case.model_copy(update={"artifact_version_id": "persisted-version"})
        self.assertEqual(review_input([versioned], [req], None)[1], digest)
        self.assertNotEqual(review_input([case], [req], {"notes": "contradictory policy"})[1], digest)
        self.assertNotEqual(review_input([case.model_copy(update={"title": "changed"})], [req], None)[1], digest)


class LiteralSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_and_feedback_are_literal_while_internal_state_expands(self):
        from app.agents.literal_instructions import literal_source_agent
        from google.adk.agents import Agent

        def builder(model, requirements, context, template, human_feedback=None):
            return Agent(
                name="LiteralFixture",
                model=model,
                instruction=f"Rules: {requirements}\nContext: {context}\nTemplate: {template}\nFeedback: {human_feedback}\nState: {{cases}}",
            )

        with patch("app.agents.literal_instructions.apply_agent_guidance", side_effect=lambda agent, stage: agent):
            agent = literal_source_agent(
                builder,
                "fixture",
                "Use /bookings/{bookingId}",
                "A literal {cases} and {artifact.private}",
                '{"field": "{name}"}',
                human_feedback="Keep {bookingId} as route variable",
            )
        context = SimpleNamespace(_invocation_context=SimpleNamespace(session=SimpleNamespace(state={"cases": "DELIVERED"})))
        text = await agent.instruction(context)
        self.assertIn("/bookings/{bookingId}", text)
        self.assertIn("literal {cases} and {artifact.private}", text)
        self.assertIn('"{name}"', text)
        self.assertIn("State: DELIVERED", text)
        self.assertIn("Keep {bookingId}", text)
