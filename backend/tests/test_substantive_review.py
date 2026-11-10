import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from google.genai import errors as genai_errors, types
import httpx

from app.agents.substantive_review import (
    MAX_INPUT_BYTES,
    review_input,
    review_prompt,
    validate_assessment,
    assess_delivered_suite,
    apply_assessment,
    assessment_tasks,
)
from app.contracts.guidance import GuidanceManifest, MemoryGuidance, SkillGuidance
from app.models import Requirement, TestCase
from app.services.guidance_service import guidance_scope
from app.services.suite_quality import reviewed_case_content_hash
from app.services.test_case_quality import case_source_hash


def fixture(*, retry=True):
    req = Requirement(
        id="ROOM-440", text="Double-click" + (" and lost-response retries" if retry else "") + " must keep one booking with the same identity after refresh."
    )
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
    if not retry:
        output["obligations"] = output["obligations"][:1]
    return req, case, data, digest, output


def rejected_booking_fixture():
    target = "https://booking.example/"
    error = "Enter a whole number from 1 to 8"
    context = {
        "app_link": target,
        "notes": f'Browser rejection "{error}" on "{target}" after button "Book" preserves css "#bookings tr" when exact heading "My bookings" is visible',
    }
    requirement = Requirement(id="BOOK-1", text="Show an inline error and create no booking when the attendee count is above the maximum.")
    case = TestCase(
        id="TC-REJECT",
        title="Reject an oversized booking",
        automation_status="To Be Automated",
        linked_requirement_ids=[requirement.id],
        expected_result="The inline error is displayed and no booking is created.",
        steps=[
            {"step": 1, "action": f'Open "{target}"', "expected": ""},
            {"step": 2, "action": 'Click exact button "Sign in as Alice"', "expected": 'exact heading "My bookings" is visible'},
            {"step": 3, "action": "Inspect the bookings", "expected": 'css "#bookings tr" count should be 1'},
            {"step": 4, "action": 'Enter "9" into the "Attendees" field', "expected": 'field "Attendees" value should equal "9"'},
            {"step": 5, "action": 'Click exact button "Book"', "expected": f'exact text "{error}" is visible'},
            {"step": 6, "action": f'Open "{target}"', "expected": 'exact heading "My bookings" is visible'},
            {"step": 7, "action": "Inspect the bookings", "expected": 'css "#bookings tr" count should be 1'},
        ],
    )
    return requirement, case, context


class SubstantiveReviewTests(unittest.TestCase):
    def test_rejected_write_requires_fresh_saved_state_even_when_the_critic_approves(self):
        requirement, case, context = rejected_booking_fixture()
        case.steps = case.steps[:5]
        data, digest, _ = review_input([case], [requirement], context)
        output = {
            "input_hash": digest,
            "obligations": [
                {
                    "requirement_id": requirement.id,
                    "source_field": "text",
                    "obligation": "Reject an oversized booking",
                    "status": "covered",
                    "reason": "The error is displayed.",
                    "evidence": [{"test_case_id": case.id, "step": 5}],
                }
            ],
            "case_grounding": [{"test_case_id": case.id, "reason": "The supplied app has the booking form.", "prerequisites": []}],
        }
        result = validate_assessment(json.dumps(output), data, digest)
        self.assertEqual(result["model_status"], "assessed_complete")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["obligations"][-1]["assessment_method"], "deterministic_regression_guard")
        self.assertIn('css "#bookings tr"', result["obligations"][-1]["reason"])
        self.assertEqual(assessment_tasks(result, [case])[0]["source_test_case_id"], case.id)

    def test_rejected_write_readback_checks_the_same_loaded_collection_before_and_after(self):
        from app.agents.substantive_review import known_regression_findings

        requirement, case, context = rejected_booking_fixture()

        def findings(current, current_context=context):
            data, _, _ = review_input([current], [requirement], current_context)
            return known_regression_findings(data)[0]

        self.assertEqual(findings(case), [])
        for step_index, changes in (
            (2, {"expected": 'css "#other tr" count should be 1'}),
            (5, {"action": "Inspect the collection"}),
            (5, {"action": 'Open "https://booking.example/other"'}),
            (5, {"expected": ""}),
            (6, {"expected": 'css "#bookings tr" count should be 2'}),
            (6, {"expected": 'css "#other tr" count should be 1'}),
        ):
            with self.subTest(step=step_index, changes=changes):
                defective = case.model_copy(deep=True)
                defective.steps[step_index] = defective.steps[step_index].model_copy(update=changes)
                self.assertEqual(len(findings(defective)), 1)
        stale = case.model_copy(update={"steps": [*case.steps[:5], case.steps[-1]]})
        separate = case.model_copy(update={"id": "TC-LIST", "steps": case.steps[-2:]})
        data, _, _ = review_input([stale, separate], [requirement], context)
        self.assertEqual(len(known_regression_findings(data)[0]), 1)
        scoped_error = case.model_copy(deep=True)
        scoped_error.steps[4].expected = 'css "#error" text should equal "Enter a whole number from 1 to 8"'
        self.assertEqual(findings(scoped_error), [])
        scoped_error.steps.pop(5)
        self.assertEqual(len(findings(scoped_error)), 1)
        quoted_selector = """css '#bookings tr[data-owner="QA  User"]'"""
        quoted_context = {**context, "notes": context["notes"].replace('css "#bookings tr"', quoted_selector)}
        scoped_count = case.model_copy(deep=True)
        for step_index in (2, 6):
            scoped_count.steps[step_index].expected = f"{quoted_selector} count should be 1"
        self.assertEqual(findings(scoped_count, quoted_context), [])
        scoped_count.steps[6].expected = scoped_count.steps[6].expected.replace("QA  User", "QA User")
        quoted_findings = findings(scoped_count, quoted_context)
        self.assertEqual(len(quoted_findings), 1)
        self.assertIn(r'css "#bookings tr[data-owner=\"QA  User\"]"', quoted_findings[0]["reason"])
        self.assertEqual(findings(stale.model_copy(update={"automation_status": "Manual"})), [])
        self.assertEqual(findings(stale, {**context, "notes": "No declared rejection."}), [])
        requirement.text = "Show an inline error when the attendee count is above the maximum."
        self.assertEqual(findings(stale), [])

    def test_compact_source_references_resolve_hash_bound_text_and_excerpt(self):
        req, case, _, _, output = fixture(retry=False)
        req.source_excerpt = "Original source excerpt: retain the same identity."
        data, digest, _ = review_input([case], [req], None)
        output["input_hash"] = digest
        source = data["requirements"][0]
        for field in ("text", "source_excerpt"):
            with self.subTest(field=field):
                compact = json.loads(json.dumps(output))
                obligation = compact["obligations"][0]
                obligation.pop("source_quote")
                obligation["source_field"] = field
                result = validate_assessment(json.dumps(compact), data, digest)
                self.assertEqual(result["obligations"][0]["source_quote"], source[field])
                self.assertNotIn("source_field", result["obligations"][0])
                self.assertEqual(result["status"], "assessed_complete")

    def test_unknown_empty_or_ambiguous_source_reference_is_rejected(self):
        _, _, data, digest, output = fixture(retry=False)
        for update in (
            {"source_field": "notes"},
            {"source_field": "source_excerpt"},
            {"source_field": "text", "requirement_id": "invented"},
            {"source_field": "text", "source_quote": "Double-click"},
        ):
            with self.subTest(update=update):
                compact = json.loads(json.dumps(output))
                compact["obligations"][0].pop("source_quote")
                compact["obligations"][0].update(update)
                with self.assertRaisesRegex(ValueError, "Source reference"):
                    validate_assessment(json.dumps(compact), data, digest)

    def test_paraphrased_source_still_fails_closed_with_safe_diagnostic(self):
        req, case, _, _, output = fixture(retry=False)
        output["obligations"][0]["source_quote"] = "must be a whole number from 1 to 8 inclusive"
        with patch("app.agents.substantive_review._call_critic", return_value=json.dumps(output)) as call:
            result = assess_delivered_suite([case], [req], None, settings=object())
        call.assert_called_once()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["diagnostics"]["validation_error"], "source_citation_mismatch")
        self.assertNotIn(output["obligations"][0]["source_quote"], str(result))
        self.assertIn("source citation mismatch", result["reason"])

    def test_invalid_schema_diagnostic_does_not_expose_model_values(self):
        req, case, _, _, output = fixture(retry=False)
        output["obligations"][0]["status"] = "private model detail"
        with patch("app.agents.substantive_review._call_critic", return_value=json.dumps(output)):
            result = assess_delivered_suite([case], [req], None, settings=object())
        self.assertEqual(result["diagnostics"]["validation_error"], "invalid_output_schema")
        self.assertNotIn("private model detail", str(result))

    def test_compact_step_references_resolve_exact_delivered_evidence_and_reject_invalid_refs(self):
        _, _, data, digest, output = fixture(retry=False)
        original = output["obligations"][0]["evidence"][0].copy()
        output["obligations"][0]["evidence"] = [{"test_case_id": original["test_case_id"], "step": original["step"]}]
        result = validate_assessment(json.dumps(output), data, digest)
        self.assertEqual(result["obligations"][0]["evidence"], [original])
        for reference in (
            {"test_case_id": "invented", "step": 1},
            {"test_case_id": original["test_case_id"], "step": 99},
            {"test_case_id": original["test_case_id"], "step": True},
        ):
            output["obligations"][0]["evidence"] = [reference]
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                validate_assessment(json.dumps(output), data, digest)

    def test_covered_chain_binds_standalone_action_and_later_observable_result(self):
        requirement = Requirement(id="DIR-1", text="Pressing Search for Alex Rivera shows the exact result Alex Rivera.")
        case = TestCase(
            id="TC-SEARCH",
            title="Search the directory",
            linked_requirement_ids=[requirement.id],
            steps=[
                {"step": 1, "action": "Open the directory", "expected": "Directory heading is visible"},
                {"step": 2, "action": 'Click exact button "Search"', "expected": ""},
                {"step": 3, "action": "Inspect the matching result", "expected": 'exact text "Alex Rivera" is visible'},
            ],
        )
        data, digest, _ = review_input([case], [requirement], None)
        trigger = {"test_case_id": case.id, "step": 2}
        result_ref = {"test_case_id": case.id, "step": 3}
        output = {
            "input_hash": digest,
            "obligations": [
                {
                    "requirement_id": requirement.id,
                    "source_field": "text",
                    "obligation": "Search returns the exact result",
                    "status": "covered",
                    "reason": "Search triggers the observed exact result",
                    "evidence": [trigger, result_ref],
                }
            ],
            "case_grounding": [{"test_case_id": case.id, "reason": "Grounded directory action and result", "prerequisites": []}],
        }
        assessment = validate_assessment(json.dumps(output), data, digest)
        self.assertEqual(assessment["design_status"], "assessed_complete")
        self.assertEqual(assessment["status"], "assessed_complete")
        self.assertEqual(
            assessment["obligations"][0]["evidence"],
            [{"test_case_id": case.id, **step.model_dump(include={"step", "action", "expected"})} for step in case.steps[1:]],
        )

        invalid = json.loads(json.dumps(output))
        for evidence in ([trigger], [{"test_case_id": case.id, "step": 1}, trigger]):
            invalid["obligations"][0]["evidence"] = evidence
            with self.subTest(evidence=evidence), self.assertRaisesRegex(ValueError, "observable result after its cited actions"):
                validate_assessment(json.dumps(invalid), data, digest)
        with patch("app.agents.substantive_review._call_critic", side_effect=[json.dumps(invalid), json.dumps(output)]) as call:
            repaired = assess_delivered_suite([case], [requirement], None, settings=object())
        self.assertEqual(repaired["status"], "assessed_complete")
        self.assertEqual(repaired["diagnostics"]["first_validation_error"], "missing_observable_result")
        self.assertEqual(call.call_count, 2)

        invalid["obligations"][0]["evidence"] = [
            {**trigger, "action": case.steps[1].action, "expected": "Fabricated result"},
            result_ref,
        ]
        with self.assertRaisesRegex(ValueError, "exact delivered steps"):
            validate_assessment(json.dumps(invalid), data, digest)

    def test_transient_provider_failure_recovers_once_without_weakening_evidence(self):
        req, case, _, _, output = fixture(retry=False)
        for error in (genai_errors.ServerError(503, {"error": {"message": "private provider detail"}}), httpx.ReadTimeout("private detail")):
            with self.subTest(error=type(error).__name__), patch("app.agents.substantive_review._call_critic", side_effect=[error, json.dumps(output)]) as call:
                result = assess_delivered_suite([case], [req], None, settings=object())
                self.assertEqual(result["status"], "assessed_complete")
                self.assertEqual(result["diagnostics"], {"attempt_count": 2, "failure_reason": None})
                self.assertEqual(call.call_count, 2)
                self.assertEqual(call.call_args_list[0].args, call.call_args_list[1].args)
                self.assertTrue(all(0 < c.kwargs["timeout_seconds"] <= 120 for c in call.call_args_list))

    def test_assessment_failure_budget_and_invalid_evidence_stay_fail_closed(self):
        req, case, _, _, output = fixture(retry=False)
        private_error = genai_errors.ServerError(503, {"error": {"message": "private provider detail"}})
        scenarios = [
            ([private_error, private_error], 2, "provider_unavailable"),
            ([genai_errors.ClientError(401, {"error": {"message": "private key"}})], 1, "provider_unavailable"),
            (["not JSON", "not JSON again"], 2, "invalid_evidence"),
            ([json.dumps({**output, "input_hash": "stale"})], 1, "invalid_evidence"),
        ]
        for side_effect, attempts, reason in scenarios:
            with self.subTest(reason=reason, attempts=attempts), patch("app.agents.substantive_review._call_critic", side_effect=side_effect) as call:
                result = assess_delivered_suite([case], [req], None, settings=object())
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["diagnostics"]["attempt_count"], attempts)
                self.assertEqual(result["diagnostics"]["failure_reason"], reason)
                if reason == "invalid_evidence":
                    self.assertIn(result["diagnostics"]["validation_error"], {"malformed_json", "content_binding_mismatch"})
                self.assertEqual(call.call_count, attempts)
                self.assertNotIn("private", str(result))
        with (
            patch("app.agents.substantive_review.monotonic", side_effect=[0, 0, 181]),
            patch("app.agents.substantive_review._call_critic", side_effect=private_error) as call,
        ):
            result = assess_delivered_suite([case], [req], None, settings=object())
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result["status"], "unknown")

    def test_bad_review_format_and_cross_case_citation_can_be_retried_once(self):
        req, case, _, _, output = fixture(retry=False)
        other = case.model_copy(update={"id": "TC-OTHER"})
        _, digest, _ = review_input([case, other], [req], None)
        output["input_hash"] = digest
        output["case_grounding"].append({"test_case_id": other.id, "reason": "Grounded", "prerequisites": []})
        mixed = json.loads(json.dumps(output))
        mixed["obligations"][0]["evidence"].append({**mixed["obligations"][0]["evidence"][0], "test_case_id": other.id})
        for invalid, code in (("not JSON", "malformed_json"), (json.dumps(mixed), "incoherent_case_evidence")):
            with self.subTest(code=code), patch("app.agents.substantive_review._call_critic", side_effect=[invalid, json.dumps(output)]) as call:
                result = assess_delivered_suite([case, other], [req], None, settings=object())
            self.assertEqual(result["status"], "assessed_complete")
            self.assertEqual(result["diagnostics"]["first_validation_error"], code)
            self.assertEqual(result["diagnostics"]["attempt_count"], 2)
            self.assertIn(code, call.call_args.args[1])
            self.assertIn(digest, call.call_args.args[1])

    def test_declared_missing_setup_blocks_combined_gate_even_when_critic_omits_it(self):
        req, case, _, _, output = fixture(retry=False)
        from app.contracts.quality_feedback import ExecutionPrerequisite

        case.execution_prerequisites = [ExecutionPrerequisite(name="bookingAccount", kind="credential", description="Provision the booking account")]
        case.steps[0].test_data = "  Café–01\t "
        data, digest, _ = review_input([case], [req], None)
        output["input_hash"] = digest
        blocker = {"test_case_id": case.id, "prerequisite_name": "bookingAccount"}
        gap = {
            "description": "Provision the booking account",
            "status": "missing_prerequisite",
            "required": True,
            "reason": "No provisioned account was supplied",
            "execution_prerequisite_name": "bookingAccount",
        }
        for findings in ([], [gap]):
            output["case_grounding"][0]["prerequisites"] = findings
            with patch("app.agents.substantive_review._call_critic", return_value=json.dumps(output)):
                assessment = assess_delivered_suite([case], [req], None, settings=object())
            self.assertEqual(assessment["status"], "incomplete")
            self.assertEqual(assessment["design_status"], "assessed_complete")
            self.assertEqual(assessment["execution_status"], "blocked")
            self.assertEqual(assessment["execution_blockers"], [blocker])
            result = apply_assessment(
                {
                    "test_cases": [case],
                    "approved": True,
                    "review": {"approved": True, "score": 100, "threshold": 90},
                    "workflow_diagnostics": {"status": "completed"},
                    "generation_evidence": {},
                    "coverage_metrics": {},
                },
                assessment,
            )
            self.assertFalse(result["approved"])
            self.assertEqual(result["generation_tasks"], [])
            self.assertEqual(
                result["generation_evidence"]["design_precheck"],
                {"passed": True, "case_content_hash": reviewed_case_content_hash([case]), "assessment_input_hash": digest},
            )
            self.assertEqual(case.steps[0].test_data, "  Café–01\t ")
        output["case_grounding"][0]["prerequisites"] = [{**gap, "execution_prerequisite_name": "invented"}]
        with self.assertRaisesRegex(ValueError, "declared missing prerequisite"):
            validate_assessment(json.dumps(output), data, digest)
        output["case_grounding"][0]["prerequisites"] = [{**gap, "execution_prerequisite_name": None, "status": "unsupported_fact"}]
        self.assertEqual(validate_assessment(json.dumps(output), data, digest)["design_status"], "incomplete")

    def test_applied_assessment_keeps_score_and_specific_findings_after_project_reload(self):
        from app.services.test_case_quality import project_test_case_view

        _, case, data, digest, output = fixture()
        assessment = validate_assessment(json.dumps(output), data, digest)
        result = apply_assessment(
            {
                "test_cases": [case.model_dump(mode="json")],
                "generation_tasks": [],
                "approved": True,
                "review": {"approved": True, "score": 100, "threshold": 90},
                "workflow_diagnostics": {"status": "completed"},
                "generation_evidence": {},
                "coverage_metrics": {},
            },
            assessment,
        )
        persisted = json.loads(json.dumps(result))
        reloaded = project_test_case_view(persisted)
        self.assertEqual(result, reloaded)
        self.assertEqual(result["review"]["score"], 100)
        self.assertFalse(result["approved"])
        self.assertTrue(any("lost-response" in issue.lower() for issue in result["review"]["blocking_issues"]))
        self.assertEqual(result["coverage_metrics"]["substantive_coverage_status"], "incomplete")

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
        req, case, _, digest, output = fixture(retry=False)
        output["obligations"] = output["obligations"][:1]
        with patch("app.agents.substantive_review._call_critic", return_value=json.dumps(output)):
            assessment = assess_delivered_suite([case], [req], None, settings=object())
        self.assertEqual(assessment["status"], "assessed_complete")
        response = {
            "test_cases": [case],
            "generation_tasks": [],
            "approved": False,
            "review": {"approved": False, "score": 87, "threshold": 90},
            "workflow_diagnostics": {},
            "generation_evidence": {"design_precheck": {"passed": True, "case_content_hash": "a" * 64, "assessment_input_hash": "b" * 64}},
            "coverage_metrics": {},
        }
        result = apply_assessment(response, assessment)
        self.assertFalse(result["approved"])
        self.assertEqual(
            result["generation_evidence"]["design_precheck"],
            {"passed": False, "case_content_hash": reviewed_case_content_hash([case]), "assessment_input_hash": digest},
        )

    def test_assessment_cannot_bind_to_changed_literal_case_content(self):
        req, case, _, _, output = fixture(retry=False)
        case.steps[0].test_data = "  Order–01  "
        _, output["input_hash"], _ = review_input([case], [req], None)
        with patch("app.agents.substantive_review._call_critic", return_value=json.dumps(output)):
            assessment = assess_delivered_suite([case], [req], None, settings=object())
        self.assertEqual(assessment["design_status"], "assessed_complete")
        self.assertEqual(assessment["reviewed_case_content_hash"], reviewed_case_content_hash([case]))
        self.assertEqual(
            assessment["reviewed_source_binding"],
            {"requirement_hashes": {req.id: case_source_hash(req.model_dump(mode="json"))}, "context": "absent"},
        )
        changed = case.model_copy(deep=True)
        changed.steps[0].test_data = "Order–01"
        result = apply_assessment(
            {
                "test_cases": [changed],
                "approved": True,
                "review": {"approved": True, "score": 100, "threshold": 90},
                "generation_evidence": {},
                "coverage_metrics": {},
            },
            assessment,
        )
        self.assertNotIn("design_precheck", result["generation_evidence"])

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
        _, case, data, digest, output = fixture(retry=False)
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
        req, case, _, _, _ = fixture(retry=False)
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

    def test_regression_guard_requires_same_case_durable_identity_and_count(self):
        from app.agents.substantive_review import known_regression_findings

        _, case, data, _, _ = fixture()
        self.assertEqual(len(known_regression_findings(data)[0]), 1)
        retry_case = case.model_dump(mode="json")
        retry_case["id"] = "TC-LOST"
        retry_case["steps"][0]["action"] = "Retry the pending submission then refresh"
        data["test_cases"].append(retry_case)
        self.assertEqual(known_regression_findings(data), ([], []))
        data["test_cases"][0]["steps"][0]["expected"] = "Exactly one booking remains"
        missing = known_regression_findings(data)[0]
        self.assertEqual(len(missing), 1)
        self.assertIn("Double-click", missing[0]["obligation"])
        refresh_case = case.model_dump(mode="json")
        refresh_case["id"] = "REFRESH-ONLY"
        refresh_case["steps"][0]["action"] = "Refresh My bookings"
        data["test_cases"].append(refresh_case)
        self.assertEqual(len(known_regression_findings(data)[0]), 1)

    def test_separate_durable_readbacks_can_close_a_single_triggering_case(self):
        requirement, case, _, _, _ = fixture(retry=False)
        count = {"action": "Count the saved bookings for this submission", "expected": "Exactly one saved booking remains."}
        locator_count = {**count, "expected": 'css "#bookings tr" count should be 1.'}
        identity = {"action": "Compare the displayed booking ID with the original", "expected": "The displayed ID matches the original recorded ID."}
        for checks in ([count, identity], [identity, count], [locator_count, identity]):
            with self.subTest(checks=checks):
                steps = [
                    {"action": 'Double-click the "Book" button', "expected": "The confirmation shows the original booking ID."},
                    {"action": "Refresh My bookings", "expected": "The saved list is visible."},
                    *checks,
                ]
                delivered = TestCase.model_validate({**case.model_dump(), "steps": [{"step": i, **step} for i, step in enumerate(steps, 1)]})
                data, digest, _ = review_input([delivered], [requirement], None)
                output = {
                    "input_hash": digest,
                    "obligations": [
                        {
                            "requirement_id": requirement.id,
                            "source_field": "text",
                            "obligation": "Durable double-click identity and count",
                            "status": "covered",
                            "reason": "Both durable outcomes are checked after the same refresh.",
                            "evidence": [{"test_case_id": case.id, "step": i} for i in range(1, 5)],
                        }
                    ],
                    "case_grounding": [{"test_case_id": case.id, "reason": "Manual setup is supplied.", "prerequisites": []}],
                }
                assessed = validate_assessment(json.dumps(output), data, digest)
                self.assertEqual(assessed["model_status"], "assessed_complete")
                self.assertEqual(assessed["design_status"], "assessed_complete")
                self.assertEqual(len(assessed["obligations"]), 1)
                self.assertEqual([item["expected"] for item in assessed["obligations"][0]["evidence"]], [step["expected"] for step in steps])

    def test_retry_readbacks_cannot_cross_submissions_or_use_transient_assertions(self):
        from app.agents.substantive_review import known_regression_findings

        _, _, data, _, _ = fixture(retry=False)
        trigger = {"action": 'Double-click "Book"', "expected": ""}
        refresh = {"action": "Reload My bookings", "expected": ""}
        count = {"action": "Count saved bookings", "expected": "Exactly one booking remains."}
        identity = {"action": "Compare the displayed booking ID", "expected": "The saved booking has the same recorded ID."}
        invalid = {
            "count before reload": [trigger, count, refresh, identity],
            "reload before trigger": [{"action": 'Reload My bookings then double-click "Book"', "expected": "Exactly one booking with the same ID remains."}],
            "mentioned reload": [trigger, {"action": "Read the refresh timestamp", "expected": ""}, count, identity],
            "mentioned trigger": [{"action": "Inspect the double-click result", "expected": ""}, refresh, count, identity],
            "new submission": [trigger, refresh, count, {"action": 'Click "Book"', "expected": ""}, identity],
            "new submission in a compound action": [
                trigger,
                refresh,
                count,
                {"action": 'Inspect My bookings, click exact button "Book"', "expected": ""},
                identity,
            ],
            "new retry": [trigger, refresh, count, {"action": "Retry pending booking", "expected": ""}, identity],
            "new double-click": [trigger, refresh, count, trigger, identity],
            "transient count": [trigger, refresh, {**count, "expected": "Exactly one booking confirmation is displayed."}, identity],
            "unrelated count": [trigger, refresh, {**count, "expected": "Only one page is displayed for the booking."}, identity],
            "combined transient count": [
                trigger,
                {**refresh, "expected": "The saved booking has the same recorded ID and exactly one confirmation is displayed."},
            ],
            "identity capture only": [trigger, refresh, count, {**identity, "expected": "The original ID is recorded."}],
            "negated ID comparison": [trigger, refresh, count, {**identity, "expected": "The booking ID does not equal the original recorded ID."}],
            "separate status is not identity comparison": [
                trigger,
                refresh,
                count,
                {**identity, "expected": "The original ID is recorded. Its status remains Confirmed."},
            ],
            "count outcome is not identity comparison": [
                trigger,
                {**refresh, "expected": "Exactly one durable booking remains. The original ID is recorded."},
            ],
            "quoted refresh label": [
                {"action": 'Double-click exact button "Book then refresh"', "expected": "Exactly one booking with the same ID remains."},
            ],
        }
        for name, steps in invalid.items():
            with self.subTest(name=name):
                data["test_cases"][0]["steps"] = [{"step": i, **step} for i, step in enumerate(steps, 1)]
                self.assertEqual(len(known_regression_findings(data)[0]), 1)

        original = data["test_cases"][0]
        original["steps"] = [{"step": i, **step} for i, step in enumerate([trigger, refresh, count], 1)]
        data["test_cases"].append({**original, "id": "TC-ID-ONLY", "steps": [{"step": i, **step} for i, step in enumerate([trigger, refresh, identity], 1)]})
        self.assertEqual(len(known_regression_findings(data)[0]), 1)

    def test_regression_guard_respects_selected_scope_and_supplied_mechanisms(self):
        from app.agents.substantive_review import known_regression_findings

        _, _, data, _, _ = fixture()
        data.update(
            scope="selected_scenarios",
            selected_scenarios=[
                {"requirement_id": "ROOM-440", "scenarios": [{"title": "Double-click", "objective": "Verify durable identity and count after refresh"}]}
            ],
        )
        self.assertEqual(known_regression_findings(data), ([], []))
        data["test_cases"][0]["steps"][0]["action"] += " using an idempotency key and simulating network response interruption"
        self.assertEqual(len(known_regression_findings(data)[1]), 2)
        data["context"] = {"notes": "Synthetic harness supports response interruption and an idempotency key."}
        self.assertEqual(known_regression_findings(data), ([], []))

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
        from app.agents.substantive_review import IncompleteCriticResponse, _call_critic, review_prompt

        with patch("app.agents.substantive_review.genai.Client") as client:
            generate = client.return_value.__enter__.return_value.models.generate_content
            generate.return_value = types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(parts=[types.Part(text="thinking", thought=True), types.Part(text=json.dumps(output))]),
                        finish_reason=types.FinishReason.STOP,
                    )
                ]
            )
            settings = SimpleNamespace(gemini_api_key="synthetic", model_name="fixture")
            self.assertEqual(json.loads(_call_critic(settings, "prompt")), output)
            config = generate.call_args.kwargs["config"]
            self.assertIn("source_field", config.response_schema["$defs"]["ObligationReference"]["required"])
            self.assertNotIn("additionalProperties", json.dumps(config.response_schema))
            self.assertEqual(config.http_options.timeout, 120_000)
            self.assertEqual(config.http_options.retry_options.attempts, 1)
            generate.return_value.candidates[0].finish_reason = types.FinishReason.MAX_TOKENS
            with self.assertRaises(IncompleteCriticResponse):
                _call_critic(settings, "prompt")
        data, _, _ = review_input([case], [req], None, scope_plan=[{"requirement_id": req.id, "scenarios": []}])
        self.assertEqual(data["scope"], "selected_scenarios")
        self.assertIn(req.text, review_prompt(data, digest))

    def test_review_binding_covers_context_and_steps_not_later_version_ids(self):
        req, case, _, digest, _ = fixture()
        versioned = case.model_copy(update={"artifact_version_id": "persisted-version"})
        self.assertEqual(review_input([versioned], [req], None)[1], digest)
        self.assertNotEqual(review_input([case], [req], {"notes": "contradictory policy"})[1], digest)
        self.assertNotEqual(review_input([case.model_copy(update={"title": "changed"})], [req], None)[1], digest)

    def test_compact_prompt_keeps_behavior_and_exact_input_binding(self):
        req, case, _, _, _ = fixture(retry=False)
        case = case.model_copy(
            update={
                "generation_source": "model",
                "generation_pass_id": "opaque-pass",
                "source_shard_id": "opaque-shard",
                "source_refs": ["ART-001"],
                "scenario_refs": ["SCN-001"],
                "test_data": '{"optionalAttendee":null,"attendees":0}',
            }
        )
        scope = [{"requirement_id": req.id, "scenarios": [{"id": "SCN-001", "must_have": False, "objective": "Verify the saved identifier."}]}]
        context = {
            "requirements": [req.model_dump(mode="json")],
            "notes": "Retain the ID after refresh.",
            "grounded_context": {"ui_elements": [{"source_id": "ART-001", "name": "Room", "select_options": [], "checkbox_available": False}]},
        }
        guidance = GuidanceManifest(
            manifest_id="guidance-1",
            stage="test_cases",
            model="fixture",
            skills=(
                SkillGuidance(id="skill", version="1", stage="test_cases", description="Method", content_hash="hash", instructions="Check the final state."),
            ),
            memories=(MemoryGuidance(id="memory", revision=0, scope="project", text="Existing fixture has one booking.", source="fixture", reason="Scoped"),),
        )
        with guidance_scope(guidance):
            data, digest, encoded = review_input([case], [req], context, scope_plan=scope)
            prompt = review_prompt(data, digest)
            projected = json.loads(prompt.rsplit("\nDATA:\n", 1)[1])
            self.assertIn(digest, prompt)
            self.assertEqual(projected["requirements"][0]["text"], req.text)
            self.assertEqual(projected["context"]["grounded_context"], context["grounded_context"])
            self.assertNotIn("requirements", projected["context"])
            self.assertEqual(projected["selected_scenarios"], scope)
            self.assertEqual(projected["test_cases"][0]["steps"][0], case.steps[0].model_dump(exclude_none=True))
            self.assertEqual(projected["test_cases"][0]["test_data"], case.test_data)
            self.assertEqual(projected["test_cases"][0]["source_refs"], ["ART-001"])
            self.assertEqual(projected["test_cases"][0]["scenario_refs"], ["SCN-001"])
            self.assertNotIn("generation_source", projected["test_cases"][0])
            self.assertNotIn("opaque-pass", prompt)
            self.assertNotIn("opaque-shard", prompt)
            self.assertNotIn("guidance_manifest", projected)
            self.assertEqual(projected["applied_guidance"]["manifest_id"], "guidance-1")
            self.assertEqual(projected["applied_guidance"]["skills"][0]["instructions"], "Check the final state.")
            self.assertEqual(projected["applied_guidance"]["memories"][0]["text"], "Existing fixture has one booking.")
            self.assertEqual(projected["applied_guidance"]["memories"][0]["revision"], 0)
            self.assertEqual(json.loads(encoded), data)
            self.assertEqual(data["test_cases"][0]["generation_pass_id"], "opaque-pass")

            context["requirements"] = [{**context["requirements"][0], "text": "Different Context requirement."}]
            changed, changed_digest, _ = review_input([case], [req], context, scope_plan=scope)
            retained = json.loads(review_prompt(changed, changed_digest).rsplit("\nDATA:\n", 1)[1])
            self.assertEqual(retained["context"]["requirements"][0]["text"], "Different Context requirement.")
            self.assertNotEqual(changed_digest, digest)

    def test_200_case_prompt_fits_and_requires_all_case_citations(self):
        req, base, _, _, output = fixture(retry=False)
        cases = [
            base.model_copy(
                update={
                    "id": f"TC-{index:03d}",
                    "description": "Capture saved receipt and exact IDs. " * 52,
                    "generation_source": "model",
                    "generation_pass_id": "39cb139b-7760-4a22-8286-667ad29fc4d9",
                    "source_shard_id": "test-case-shard-01",
                }
            )
            for index in range(1, 201)
        ]
        context = {"requirements": [req.model_dump(mode="json")]}
        data, digest, encoded = review_input(cases, [req], context)
        self.assertGreater(len(encoded.encode("utf-8")), MAX_INPUT_BYTES)
        self.assertLessEqual(len(review_prompt(data, digest).encode("utf-8")), MAX_INPUT_BYTES)
        omitted = False

        def critic(settings, prompt, **kwargs):
            instructions, _, encoded = prompt.rpartition("\nDATA:\n")
            sent = json.loads(encoded)
            group_cases = sent["test_cases"]
            finding = output["obligations"][0]
            if group_cases:
                finding = {**finding, "evidence": [{"test_case_id": group_cases[0]["id"], "step": 1}]}
            else:
                finding = {**finding, "status": "unknown", "evidence": []}
            return json.dumps(
                {
                    "input_hash": instructions.split("Copy input_hash exactly as ", 1)[1].split(".", 1)[0],
                    "obligations": [finding],
                    "case_grounding": [
                        {"test_case_id": row["id"], "reason": "Grounded fixture", "prerequisites": []}
                        for row in group_cases
                        if not omitted or row["id"] != cases[-1].id
                    ],
                }
            )

        with patch("app.agents.substantive_review._call_critic", side_effect=critic) as provider:
            reviewed = assess_delivered_suite(cases, [req], context, settings=object())
            self.assertEqual(reviewed["status"], "assessed_complete")
            self.assertEqual(len(reviewed["case_grounding"]), len(cases))
            sent = [json.loads(call.args[1].rsplit("\nDATA:\n", 1)[1]) for call in provider.call_args_list]
            self.assertEqual(sorted(row["id"] for packet in sent for row in packet["test_cases"]), [case.id for case in cases])
            omitted = True
            incomplete = assess_delivered_suite(cases, [req], context, settings=object())
            self.assertEqual(incomplete["status"], "unknown")
            missing = [group for group in incomplete["diagnostics"]["groups"] if group["status"] == "unknown"]
            self.assertEqual(len(missing), 1)
            self.assertIn(cases[-1].id, missing[0]["case_ids"])
            self.assertEqual(missing[0]["validation_error"], "incomplete_case_review")

    def test_prompt_budget_counts_utf8_and_repair_instructions(self):
        req, case, _, _, _ = fixture(retry=False)
        context = {"notes": "Check pièce 雪 after reload." * 40}
        data, digest, _ = review_input([case], [req], context)
        prompt = review_prompt(data, digest)
        byte_count = len(prompt.encode("utf-8"))
        self.assertGreater(byte_count, len(prompt))
        with (
            patch("app.agents.substantive_review._call_critic", return_value="not JSON") as critic,
            patch("app.agents.substantive_review.MAX_INPUT_BYTES", len(prompt)),
        ):
            oversized = assess_delivered_suite([case], [req], context, settings=object())
            critic.assert_not_called()
        self.assertEqual(oversized["status"], "unknown")
        self.assertEqual(oversized["diagnostics"]["prompt_bytes"], byte_count)
        self.assertEqual(oversized["diagnostics"]["attempt_count"], 0)
        with (
            patch("app.agents.substantive_review._call_critic", return_value="not JSON") as critic,
            patch("app.agents.substantive_review.MAX_INPUT_BYTES", byte_count),
        ):
            retry_oversized = assess_delivered_suite([case], [req], context, settings=object())
            critic.assert_called_once()
        self.assertEqual(retry_oversized["status"], "unknown")
        self.assertEqual(retry_oversized["diagnostics"]["failure_reason"], "review_input_too_large")
        self.assertEqual(retry_oversized["diagnostics"]["first_validation_error"], "malformed_json")
        self.assertGreater(retry_oversized["diagnostics"]["prompt_bytes"], byte_count)


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
