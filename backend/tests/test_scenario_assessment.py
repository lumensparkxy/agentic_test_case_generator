import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agents import use_case_agent as agent
from app.agents.scenario_assessment import assessment_input, assess_scenarios, combine_review, local_findings, parse_critic_output, CRITERIA
from app.agents.scenario_boundary_coverage import inclusive_input_ranges
from app.models import Requirement, WorkflowSettings
from app.contracts.requirements import ReviewResult


REQ = Requirement(id="REQ-001", text="Booking accepts 1 to 8 attendees. Nine attendees are rejected without creating a booking.")


def plan(*scenarios):
    return [
        {
            "requirement_id": REQ.id,
            "requirement_text": REQ.text,
            "scenarios": [
                {
                    "id": f"REQ-001-SCN-{index:02}",
                    "requirement_id": REQ.id,
                    "title": title,
                    "objective": objective,
                    "scenario_type": "Happy Path" if index == 1 else "Negative",
                    "priority": "High",
                    "must_have": True,
                }
                for index, (title, objective) in enumerate(scenarios, 1)
            ],
        }
    ]


def passes(coverage, context="", requirements=None):
    return [
        {
            "requirement_id": entry["requirement_id"],
            "scenario_id": entry["scenario"]["id"],
            "content_hash": entry["content_hash"],
            **{criterion: {"passed": True, "reason": "Concrete supplied input and observable result."} for criterion in CRITERIA},
        }
        for entry in assessment_input(coverage, requirements or [REQ], context)
    ]


class ScenarioAssessmentTests(unittest.TestCase):
    def test_incomplete_booking_plan_cannot_pass_when_each_delivered_scenario_passes(self):
        range_requirement = REQ.model_copy(
            update={"text": "The system shall require Attendees to be a whole number from 1 to 8 inclusive, displaying an inline error on invalid input."}
        )
        coverage = plan(
            ("Reject empty input", "Submit with Attendees empty. An inline error appears and no booking is created."),
            ("Reject zero", 'Submit with Attendees set to 0. The inline error says "Enter a whole number from 1 to 8".'),
            ("Reject 9", "Submit with 9 attendees. An inline error appears and no booking is created."),
            ("Reject 1.5", "Submit with 1.5 attendees. An inline error appears and no booking is created."),
            ("Create confirmed booking", "Submit an available room with a valid attendee count. The booking appears as Confirmed."),
        )
        coverage[0]["requirement_text"] = range_requirement.text
        rows = passes(coverage, requirements=[range_requirement])
        (interval,) = inclusive_input_ranges([range_requirement])
        rows[1]["boundary_evidence"] = [
            {
                "range_id": interval.range_id,
                "endpoint": "1",
                "input_quote": "whole number from 1",
                "outcome_quote": "The inline error says",
            }
        ]
        semantic = assess_scenarios(coverage, [range_requirement], "", rows)
        self.assertTrue(all(item["status"] == "passed" for item in semantic["items"]))
        self.assertEqual(semantic["status"], "needs_review")
        self.assertEqual(semantic["flagged_count"], 0)
        review = ReviewResult.model_validate(combine_review({"approved": True, "score": 100}, semantic))
        self.assertFalse(review.approved)
        (finding,) = review.semantic_assessment.requirement_findings
        self.assertEqual((finding.requirement_id, finding.source_excerpt, finding.missing_endpoints), (REQ.id, "from 1 to 8 inclusive", ["1", "8"]))

    def test_exact_positive_boundaries_use_related_success_group_and_current_source(self):
        validation = REQ.model_copy(update={"text": "Attendees must be a whole number from 1 to 6 inclusive. A count of 7 is rejected."})
        creation = Requirement(id="REQ-002", text="Submitting a valid attendee count for an available room creates a Confirmed booking.")
        coverage = plan(("Reject seven attendees", "Submit 7 attendees and show the inline error."))
        coverage[0]["requirement_text"] = validation.text
        success = plan(
            ("Book with 1 attendee", "Submit with Attendees set to 1. The booking appears as Confirmed."),
            ("Book with 6 attendees", "Submit with Attendees set to 6. The booking appears as Confirmed."),
        )[0]
        success.update(requirement_id=creation.id, requirement_text=creation.text)
        for index, scenario in enumerate(success["scenarios"], 1):
            scenario.update(id=f"{creation.id}-SCN-{index:02}", requirement_id=creation.id)
        coverage.append(success)
        requirements = [validation, creation]
        (interval,) = inclusive_input_ranges(requirements)
        rows = passes(coverage, requirements=requirements)
        for row, endpoint in zip(rows[1:], ("1", "6"), strict=True):
            row["boundary_evidence"] = [
                {
                    "range_id": interval.range_id,
                    "endpoint": endpoint,
                    "input_quote": f"Attendees set to {endpoint}",
                    "outcome_quote": "The booking appears as Confirmed",
                }
            ]
        parsed = parse_critic_output(json.dumps({"assessments": rows}))
        assessment = assess_scenarios(coverage, requirements, "", parsed)
        self.assertEqual(assessment["status"], "passed")
        self.assertEqual(assessment["items"][-1]["boundary_evidence"], rows[-1]["boundary_evidence"])

        for changed_evidence in (
            {"endpoint": "8"},
            {"input_quote": "Attendees set to 66"},
            {"outcome_quote": "The booking is saved and emailed"},
            {"range_id": "wrong-range"},
        ):
            with self.subTest(changed_evidence=changed_evidence):
                changed_rows = deepcopy(rows)
                changed_rows[-1]["boundary_evidence"][0].update(changed_evidence)
                missing = assess_scenarios(coverage, requirements, "", changed_rows)["requirement_findings"]
                self.assertEqual(missing[0]["missing_endpoints"], ["6"])
        unsupported = deepcopy(rows)
        unsupported[-1]["source_grounding"] = {"passed": False, "reason": "The asserted outcome is unsupported."}
        self.assertEqual(assess_scenarios(coverage, requirements, "", unsupported)["requirement_findings"][0]["missing_endpoints"], ["6"])

        overlapping = deepcopy(rows)
        overlapping[-1]["boundary_evidence"][0]["outcome_quote"] = "Submit with Attendees set to 6. The booking appears as Confirmed."
        self.assertEqual(assess_scenarios(coverage, requirements, "", overlapping)["status"], "passed")

        longer_value = deepcopy(coverage)
        longer_value[1]["scenarios"][1]["objective"] = success["scenarios"][1]["objective"].replace("6", "6.5")
        rebound = passes(longer_value, requirements=requirements)
        for new, old in zip(rebound[1:], rows[1:], strict=True):
            new["boundary_evidence"] = old["boundary_evidence"]
        self.assertEqual(assess_scenarios(longer_value, requirements, "", rebound)["requirement_findings"][0]["missing_endpoints"], ["6"])

        prior = validation.model_copy(update={"text": validation.text.replace("to 6", "to 8")})
        result = assess_scenarios(coverage, [prior, creation], "", rows)
        self.assertEqual(result["assessed_count"], 0)
        self.assertEqual(result["requirement_findings"][0]["missing_endpoints"], ["1", "8"])

    def test_inclusive_range_detection_ignores_quotes_denials_and_nonranges(self):
        texts = [
            "The system accepts a temperature from -2.5 to 3.5 (inclusive).",
            "Batch size must be between 1 and 8 inclusive.",
            'Send an error that says "Enter from 1 to 8 inclusive".',
            "The system rejects choices from 1 to 8 inclusive.",
            "The system does not allow quantities from 1 to 8 inclusive.",
            "The service displays rows 1 to 8.",
        ]
        rows = [Requirement(id=f"REQ-{index}", text=value) for index, value in enumerate(texts)]
        self.assertEqual(
            [(item.requirement_id, item.endpoints) for item in inclusive_input_ranges(rows)],
            [("REQ-0", ("-2.5", "3.5")), ("REQ-1", ("1", "8"))],
        )

    def test_all_nine_category_scaffolds_warn_even_if_critic_passes(self):
        categories = [
            "Happy Path",
            "Negative",
            "Boundary",
            "Validation",
            "Authorization",
            "State Transition",
            "Integration",
            "Error Handling",
            "Data Variation",
        ]
        coverage = plan(
            *[(f"{category} coverage for REQ-001", f"Validate requirement REQ-001 under {category.lower()} conditions.") for category in categories]
        )
        result = assess_scenarios(coverage, [REQ], "", passes(coverage))
        self.assertEqual(result["flagged_count"], 9)
        self.assertEqual(result["status"], "needs_review")
        for row in result["items"]:
            self.assertIn("concrete trigger and observable outcome", row["warnings"][0])

    def test_server_fallback_parenthetical_source_is_still_category_scaffolding(self):
        coverage = plan(("Negative coverage for REQ-001", f"Validate requirement REQ-001 ({REQ.text}) under negative conditions."))
        result = assess_scenarios(coverage, [REQ], "")
        self.assertEqual(result["flagged_count"], 1)
        self.assertFalse(result["items"][0]["review_available"])

    def test_semantic_timeout_survives_shard_merge(self):
        diagnostics = agent._new_use_case_diagnostics()
        shard = agent._UseCaseShard(index=1, requirements=[REQ])
        result = agent._UseCaseShardResult(
            shard=shard,
            requirement_analysis=[],
            coverage_plan=[],
            diagnostics={
                "status": "partial",
                "timed_out": True,
                "failure_reason": "semantic_review_timeout",
            },
        )
        agent._merge_shard_diagnostics(diagnostics, [result])
        self.assertTrue(diagnostics["timed_out"])
        self.assertEqual(diagnostics["failure_reason"], "semantic_review_timeout")
        self.assertEqual(diagnostics["status"], "partial")

    def test_short_title_or_category_word_is_not_a_failure(self):
        coverage = plan(
            ("Boundary", "Submit nine attendees; reject the request without a booking."),
            ("Nine attendees are rejected without a booking", "Validate requirement REQ-001 under negative conditions."),
        )
        self.assertEqual(local_findings(coverage, REQ.text), {(REQ.id, "REQ-001-SCN-01"): [], (REQ.id, "REQ-001-SCN-02"): []})
        result = assess_scenarios(coverage, [REQ], "", passes(coverage))
        self.assertEqual(result["status"], "passed")

    def test_duplicate_category_padding_flags_both_members(self):
        coverage = plan(("Negative", "Submit nine attendees; no booking is created."), ("Validation", "Submit nine attendees; no booking is created."))
        result = assess_scenarios(coverage, [REQ], "", passes(coverage))
        self.assertEqual(result["flagged_count"], 2)
        self.assertTrue(all("Duplicate" in row["warnings"][0] for row in result["items"]))

    def test_cross_requirement_objectives_are_compared_even_with_different_titles(self):
        other = Requirement(id="REQ-002", text="Nine attendees are rejected without a booking.")
        coverage = plan(("Attendee validation", "Submit nine attendees; no booking is created."))
        second = deepcopy(coverage[0])
        second.update(requirement_id=other.id, requirement_text=other.text)
        second["scenarios"][0].update(id="REQ-002-SCN-01", requirement_id=other.id, title="Boundary rejection")
        coverage.append(second)
        result = assess_scenarios(coverage, [REQ, other], "", passes(coverage, requirements=[REQ, other]))
        self.assertEqual(result["flagged_count"], 2)
        self.assertTrue(all("across requirements" in row["warnings"][0] for row in result["items"]))
        # Shared language with a different input/outcome is not exact duplication.
        second["scenarios"][0]["objective"] = "Submit one attendee; one booking is created."
        self.assertEqual(assess_scenarios(coverage, [REQ, other], "", passes(coverage, requirements=[REQ, other]))["status"], "passed")
        # Identical generic objectives do not erase distinct concrete titles.
        coverage[0]["scenarios"][0].update(title="Nine attendees are rejected without a booking", objective="Verify the primary flow.")
        second["scenarios"][0].update(title="One attendee creates one booking", objective="Verify the primary flow.")
        self.assertEqual(assess_scenarios(coverage, [REQ, other], "", passes(coverage, requirements=[REQ, other]))["status"], "passed")

    def test_shard_passes_and_changed_neighbours_cannot_certify_the_complete_plan(self):
        coverage = plan(("Accept one", "Submit one attendee; booking created."), ("Reject nine", "Submit nine; no booking."))
        rows = passes(coverage)
        changed = deepcopy(coverage)
        changed[0]["scenarios"][1]["objective"] = "Submit one attendee; booking created."
        result = assess_scenarios(changed, [REQ], "", rows)
        self.assertEqual(result["assessed_count"], 0)
        partial = deepcopy(coverage)
        partial[0]["scenarios"] = partial[0]["scenarios"][:1]
        partial_rows = passes(partial)
        self.assertEqual(assess_scenarios(coverage, [REQ], "", partial_rows)["assessed_count"], 0)

    def test_unsupported_prescriptions_need_confirmation(self):
        coverage = plan(("Create booking", "POST /api/bookings must debounce clicks for 500ms."))
        row = assess_scenarios(coverage, [REQ], "", passes(coverage))["items"][0]
        self.assertEqual(len(row["warnings"]), 2)
        self.assertTrue(all("assumption" in warning for warning in row["warnings"]))
        context = "The supplied interface uses POST /api/bookings and debounces clicks for 500ms."
        self.assertEqual(assess_scenarios(coverage, [REQ], context, passes(coverage, context))["status"], "passed")

    def test_missing_or_malformed_critic_is_unavailable_not_pass(self):
        coverage = plan(("Boundary", "Submit nine attendees; no booking is created."))
        for raw in ("", "not json", '{"assessments":[{"passed":true}]}', "null", "[]"):
            with self.subTest(raw=raw):
                result = assess_scenarios(coverage, [REQ], "", parse_critic_output(raw))
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["assessed_count"], 0)

    def test_route_template_instances_require_source_values_and_matching_path(self):
        context = "Direct route /bookings/{bookingId}. Alice owns booking B-EXISTING."
        for route, supported in (
            ("/bookings/B-EXISTING", True),
            ("/bookings/{bookingId}", True),
            ("/bookings/INVENTED", False),
            ("/bookings/B-EXIST", False),
            ("/booking/B-EXISTING", False),
            ("/bookings/B-EXISTING/cancel", False),
            ("/bookings/B-EXISTING/", False),
            ("/Bookings/B-EXISTING", False),
            ("/bookings/b-existing", False),
            ("/bookings/{otherId}", False),
        ):
            with self.subTest(route=route):
                coverage = plan(("Non-owner cancellation", f"Bob opens {route}; cancellation is denied and the booking remains Confirmed."))
                result = assess_scenarios(coverage, [REQ], context, passes(coverage, context))
                self.assertEqual(result["status"], "passed" if supported else "needs_review")

    def test_route_matching_is_exact_and_does_not_treat_source_regex_characters_as_patterns(self):
        for context, route in (
            ("Route /bookings/archive", "/bookings"),
            ("Route /bookings/archive", "/bookings/arch"),
            ("Route /bookings/{bookingId}; archive-old is a fixture.", "/bookings/archive"),
            ("Route /v1.0/bookings/{bookingId}; B-EXISTING is a fixture.", "/v1x0/bookings/B-EXISTING"),
            ("Route /bookings/{bookingId}; another route /other/INVENTED.", "/bookings/INVENTED"),
            ("Route /bookings/prefix-{bookingId}; B-EXISTING is a fixture.", "/bookings/prefix-B-EXISTING"),
        ):
            with self.subTest(context=context, route=route):
                coverage = plan(("Inspect booking", f"Open {route}; status remains Confirmed."))
                self.assertEqual(assess_scenarios(coverage, [REQ], context, passes(coverage, context))["status"], "needs_review")

    def test_context_browser_page_grounds_only_the_observed_same_origin_route(self):
        target = "http://127.0.0.1:8810/"
        route = "/bookings/B-EXISTING"
        for notes, scenario_route, expected in (
            (f'Browser page "{target}bookings/B-EXISTING"', route, "passed"),
            (f'Browser page "{target}bookings/B-EXISTING"', route + "/cancel", "needs_review"),
            (f'The page is "{target}bookings/B-EXISTING".', route, "needs_review"),
            ('Browser page "http://127.0.0.1:8811/bookings/B-EXISTING"', route, "needs_review"),
            ('Route /bookings/{bookingId}.\nBrowser page "https://foreign.test/bookings/B-EXISTING"', route, "needs_review"),
        ):
            with self.subTest(notes=notes, route=scenario_route):
                context = json.dumps({"app_link": target, "notes": notes})
                coverage = plan(("Inspect existing booking", f"Open {scenario_route}; status remains Confirmed."))
                self.assertEqual(assess_scenarios(coverage, [REQ], context, passes(coverage, context))["status"], expected)

    def test_route_instances_with_multiple_parameters_require_every_source_value(self):
        coverage = plan(("Inspect booking", "Open /rooms/Atlas/bookings/B-EXISTING; Confirmed is displayed."))
        template = "Route /rooms/{roomId}/bookings/{bookingId}. Room Atlas."
        for context, supported in ((template, False), (template + " Booking B-EXISTING.", True)):
            with self.subTest(context=context):
                self.assertEqual(assess_scenarios(coverage, [REQ], context, passes(coverage, context))["status"], "passed" if supported else "needs_review")

    def test_supported_route_never_replaces_complete_passing_critic(self):
        context = "Direct route /bookings/{bookingId}. Booking B-EXISTING."
        coverage = plan(("Inspect booking", "Open /bookings/B-EXISTING; Confirmed is displayed."))
        self.assertEqual(assess_scenarios(coverage, [REQ], context)["status"], "unavailable")
        rows = passes(coverage, context)
        rows[0]["source_grounding"] = {"passed": False, "reason": "The behavior is not specified for this actor."}
        result = assess_scenarios(coverage, [REQ], context, rows)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["items"][0]["warnings"], ["The behavior is not specified for this actor."])

    def test_string_booleans_blank_reasons_and_omitted_criteria_are_not_reviews(self):
        coverage = plan(("Boundary", "Submit nine attendees; no booking is created."))
        for mutate in (
            lambda x: x[0]["specific_trigger"].update(passed="false"),
            lambda x: x[0]["observable_outcome"].update(reason=""),
            lambda x: x[0].pop("distinct_behavior"),
        ):
            rows = passes(coverage)
            mutate(rows)
            self.assertEqual(assess_scenarios(coverage, [REQ], "", rows)["status"], "unavailable")

    def test_exact_content_and_source_binding_required(self):
        coverage = plan(("Boundary", "Submit nine attendees; no booking is created."))
        rows = passes(coverage)
        changed = deepcopy(coverage)
        changed[0]["scenarios"][0]["objective"] = "Submit two attendees; booking is created."
        for input_plan, reqs, context in (
            (changed, [REQ], ""),
            (coverage, [REQ], "New source context"),
            (coverage, [REQ.model_copy(update={"text": "Different requirement"})], ""),
        ):
            self.assertEqual(assess_scenarios(input_plan, reqs, context, rows)["status"], "unavailable")

    def test_duplicate_review_rows_and_missing_scenarios_cannot_pass(self):
        coverage = plan(("Accept", "Submit one attendee; booking created."), ("Reject", "Submit nine; no booking."))
        rows = passes(coverage)
        for invalid in (rows[:1], [rows[0], rows[0], rows[1]]):
            result = assess_scenarios(coverage, [REQ], "", invalid)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["assessed_count"], 1)

    def test_failed_criterion_preserves_actionable_reason(self):
        coverage = plan(("Save", "Submit data successfully."))
        rows = passes(coverage)
        rows[0]["observable_outcome"] = {"passed": False, "reason": "Name the observable persisted booking or error; success alone is ambiguous."}
        result = assess_scenarios(coverage, [REQ], "", rows)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("persisted booking", result["items"][0]["warnings"][0])

    def test_structural_100_is_not_semantic_approval_and_contract_retains_evidence(self):
        coverage = plan(("Boundary", "Submit nine attendees; no booking is created."))
        structural = {"approved": True, "score": 100, "threshold": 85, "summary": "Structural checks passed."}
        for rows, approved in ((None, False), (passes(coverage), True)):
            review = combine_review(structural, assess_scenarios(coverage, [REQ], "", rows))
            delivered = ReviewResult.model_validate(review).model_dump()
            self.assertEqual(delivered["approved"], approved)
            self.assertEqual(delivered["structural_checks"]["score"], 100)
            self.assertEqual(len(delivered["semantic_assessment"]["items"]), 1)

    def test_missing_credentials_fallback_cannot_approve_generic_scenarios(self):
        from app.models import GenerateTestCasesInput, TestCaseTemplate

        payload = GenerateTestCasesInput(requirements=[REQ], template=TestCaseTemplate(name="Use Cases", format="json", fields=[]))
        with patch.object(agent, "_get_model_settings_or_none", return_value=None):
            result = agent.generate_use_cases(payload)
        self.assertFalse(result["approved"])
        self.assertEqual(result["review"]["semantic_assessment"]["assessed_count"], 0)

    def test_critic_failure_preserves_delivered_plan_without_success(self):
        coverage = plan(("Boundary", "Submit nine attendees; no booking is created."))

        class FailingRunner:
            def __init__(self, **kwargs):
                pass

            async def run_async(self, **kwargs):
                yield SimpleNamespace(
                    author="CoveragePlannerAgent", content=SimpleNamespace(parts=[SimpleNamespace(text=json.dumps({"coverage_plan": coverage}))])
                )
                raise RuntimeError("synthetic reviewer outage")

        with patch.object(agent, "Runner", FailingRunner), patch.object(agent, "memory_service_for_run", return_value=None):
            result = asyncio.run(
                agent._run_single_use_case_shard_workflow_async(
                    shard=agent._UseCaseShard(index=1, requirements=[REQ]),
                    context=None,
                    requirements_text=REQ.text,
                    context_text="",
                    model="test-model",
                    workflow_settings=WorkflowSettings(timeout_seconds=5),
                )
            )
        self.assertEqual(result["coverage_plan"], coverage)
        self.assertEqual(result["semantic_rows"], [])
        self.assertIn("Semantic review did not finish", str(result["workflow_diagnostics"]))

    def test_merged_critic_is_bounded_and_returns_only_complete_parseable_output(self):
        coverage = plan(("Accept one", "Submit one attendee; one booking is created."))
        rows = passes(coverage)
        for mode, failure in (
            ("success", None),
            ("malformed", "semantic_review_unavailable"),
            ("timeout", "semantic_review_timeout"),
            ("error", "semantic_review_error"),
            ("event_error", "semantic_review_error"),
            ("setup_error", "semantic_review_error"),
        ):
            with self.subTest(mode=mode):
                session = SimpleNamespace(id="review-session", state={})
                sessions = SimpleNamespace(create_session=AsyncMock(return_value=session), get_session=AsyncMock(return_value=session))

                class ReviewerRunner:
                    calls = 0

                    def __init__(self, **kwargs):
                        if mode == "setup_error":
                            raise RuntimeError("provider detail must not leak")
                        self.agent = kwargs["agent"]

                    async def run_async(self, **kwargs):
                        ReviewerRunner.calls += 1
                        if mode == "timeout":
                            await asyncio.Event().wait()
                        if mode == "error":
                            raise RuntimeError("provider detail must not leak")
                        if mode == "event_error":
                            yield SimpleNamespace(error_code="provider_failed")
                            return
                        session.state[agent.STATE_SCENARIO_ASSESSMENT] = json.dumps({"assessments": rows}) if mode == "success" else "malformed"
                        yield SimpleNamespace()

                with patch.object(agent, "Runner", ReviewerRunner), patch.object(agent, "InMemorySessionService", return_value=sessions):
                    actual, reason = asyncio.run(
                        agent._review_merged_use_cases_async(
                            coverage_plan=coverage, requirements=[REQ], context_text="", model="fixture", timeout_seconds=0.01, actor_user_id=None
                        )
                    )
                self.assertEqual(actual, rows if mode == "success" else [])
                self.assertEqual(reason, failure)
                self.assertLessEqual(ReviewerRunner.calls, 1)


if __name__ == "__main__":
    unittest.main()
