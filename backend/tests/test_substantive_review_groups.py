from collections import Counter
import json
from threading import Barrier, Event, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from app.agents import substantive_review as review
from app.agents import test_case_agent
from app.contracts.guidance import GuidanceManifest
from app.models import GenerateTestCasesInput, Requirement, TestCase, TestCaseTemplate
from app.services.guidance_service import current_guidance, guidance_scope


def scenario_case(key, links):
    return TestCase(
        id=f"TC-{key}",
        title=f"Verify {key} saved receipt",
        description=f"Exercise {key} in the supplied fixture.",
        preconditions="Open the order fixture.",
        steps=[{"step": 1, "action": f"Submit the {key} order", "expected": f"The {key} receipt is visible"}],
        expected_result=f"The {key} receipt is visible",
        linked_requirement_ids=links,
        scenario_refs=[f"{links[0]}-SCN-01"],
    )


def critic_output(prompt):
    instructions, _, encoded = prompt.rpartition("\nDATA:\n")
    digest = instructions.split("Copy input_hash exactly as ", 1)[1].split(".", 1)[0]
    data = json.loads(encoded)
    groups = data.get("review_group") or {"requirement_ids": [item["id"] for item in data["requirements"]]}
    obligations = []
    for key in groups["requirement_ids"]:
        supporting = next((item for item in data["test_cases"] if key in item["linked_requirement_ids"]), None)
        obligations.append(
            {
                "requirement_id": key,
                "source_field": "text",
                "obligation": f"Verify {key} saved receipt",
                "status": "covered" if supporting else "unmet",
                "reason": "Check the receipt" if supporting else "No linked case in the review group",
                "evidence": [{"test_case_id": supporting["id"], "step": 1}] if supporting else [],
            }
        )
    return data, {
        "input_hash": digest,
        "obligations": obligations,
        "case_grounding": [{"test_case_id": case["id"], "reason": "Supplied fixture", "prerequisites": []} for case in data["test_cases"]],
    }


class GroupedSubstantiveReviewTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("GROUP_REVIEW_MIN_CASES", 1), ("GROUP_REVIEW_TARGET_CASES", 1)):
            patcher = patch.object(review, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.requirements = [Requirement(id=key, text=f"Submitting {key} must display its receipt.") for key in ("A", "B", "C", "D", "E", "F", "G")]
        self.cases = [
            scenario_case("F", ["F"]),
            scenario_case("AB", ["A", "B"]),
            scenario_case("G", ["G"]),
            scenario_case("BC", ["B", "C"]),
            scenario_case("E", ["E"]),
            scenario_case("D", ["D"]),
        ]
        self.context = {"notes": "The receipt remains visible after the test."}

    def assess(self, critic, *, cases=None, scope_plan=None):
        with patch.object(review, "_call_critic", side_effect=critic):
            return review.assess_delivered_suite(
                self.cases if cases is None else cases, self.requirements, self.context, settings=object(), scope_plan=scope_plan
            )

    def test_joined_requirements_shared_evidence_and_deterministic_merge(self):
        manifest = GuidanceManifest(manifest_id="test-guidance", model="fixture", stage="test_cases")
        initial = Barrier(3, timeout=5)
        early_group_done = Event()
        completed = []
        active = highest = 0
        lock = Lock()

        def critic(settings, prompt, *, timeout_seconds):
            nonlocal active, highest
            data, output = critic_output(prompt)
            keys = data["review_group"]["requirement_ids"]
            first = keys[0]
            self.assertLessEqual(len(prompt.encode("utf-8")), review.GROUP_REVIEW_MAX_BYTES)
            self.assertGreater(timeout_seconds, 0)
            self.assertLessEqual(timeout_seconds, review.GROUP_REVIEW_ATTEMPT_SECONDS)
            self.assertEqual(current_guidance(), manifest)
            self.assertEqual(data["applied_guidance"]["manifest_id"], manifest.manifest_id)
            self.assertEqual(data["context"], self.context)
            self.assertEqual([row["id"] for row in data["requirements"]], [row.id for row in self.requirements])
            with lock:
                active += 1
                highest = max(highest, active)
            if first in ("A", "D", "E"):
                initial.wait()
            if first == "A":
                self.assertTrue(early_group_done.wait(5))
            with lock:
                active -= 1
                completed.append(first)
            if first == "E":
                early_group_done.set()
            return json.dumps(output)

        with guidance_scope(manifest):
            _, digest, _ = review.review_input(self.cases, self.requirements, self.context)
            result = self.assess(critic)
        self.assertEqual(result["status"], "assessed_complete")
        self.assertEqual(result["input_hash"], digest)
        self.assertEqual([row["requirement_id"] for row in result["obligations"]], [req.id for req in self.requirements])
        self.assertEqual([row["test_case_id"] for row in result["case_grounding"]], [case.id for case in self.cases])
        self.assertEqual(result["diagnostics"]["attempt_count"], 5)
        groups = result["diagnostics"]["groups"]
        self.assertEqual(groups[0]["requirement_ids"], ["A", "B", "C"])
        self.assertEqual(groups[0]["case_ids"], ["TC-AB", "TC-BC"])
        self.assertEqual(len({group["input_hash"] for group in groups}), 5)
        self.assertEqual(highest, 3)
        self.assertLess(completed.index("E"), completed.index("A"))

    def test_missing_group_blocks_approval_and_keeps_verified_gaps(self):
        for defect in (False, True):
            calls = Counter()

            def critic(settings, prompt, **kwargs):
                data, output = critic_output(prompt)
                key = data["review_group"]["requirement_ids"][0]
                calls[key] += 1
                if key == "E":
                    raise httpx.ReadTimeout("private provider error")
                if key == "D" and defect:
                    output["obligations"][0].update(status="unmet", reason="The saved receipt was never checked.")
                return json.dumps(output)

            with self.subTest(verified_gap=defect):
                result = self.assess(critic)
                self.assertEqual(result["status"], "incomplete" if defect else "unknown")
                self.assertEqual(result["diagnostics"]["unverified_group_count"], 1)
                self.assertEqual(result["diagnostics"]["failure_reason"], "provider_unavailable")
                self.assertEqual(calls, {"A": 1, "D": 1, "E": 2, "F": 1, "G": 1})
                self.assertEqual(len(result["case_grounding"]), 5)
                tasks = review.assessment_tasks(result, self.cases)
                self.assertEqual([row["source_test_case_id"] for row in tasks], ["TC-D"] if defect else [])
                self.assertIn("1 of 5 groups unverified", result["reason"])
                self.assertNotIn("private provider error", str(result))

    def test_shared_steps_keep_exact_per_case_evidence_and_numbers(self):
        common = {"action": 'Click exact button "Réserver"', "expected": 'exact text "Réservation créée" is visible', "test_data": '{"value":null}'}
        cases = [
            TestCase.model_validate({**self.cases[1].model_dump(), "steps": [{"step": 1, **common}]}),
            TestCase.model_validate(
                {
                    **self.cases[3].model_dump(),
                    "steps": [
                        {"step": 1, "action": "Open the fixture", "expected": "Order form is visible"},
                        {"step": 2, **common},
                    ],
                }
            ),
        ]
        data, digest, _ = review.review_input(cases, self.requirements, self.context)
        group = review._review_slice(data, digest, ["A", "B", "C"], data["test_cases"])
        prompt_data, output = critic_output(group.prompt)
        steps = [case["steps"] for case in prompt_data["test_cases"]]
        self.assertEqual(steps[0], [{"step": 1, "step_ref": "s1"}])
        self.assertEqual(steps[1][1], {"step": 2, "step_ref": "s1"})
        self.assertEqual(prompt_data["shared_steps"], {"s1": common})
        self.assertEqual(group.data["test_cases"], data["test_cases"])
        output["obligations"][-1]["evidence"] = [{"test_case_id": cases[1].id, "step": 2}]
        assessed = review.validate_assessment(json.dumps(output), group.data, group.input_hash)
        self.assertEqual(
            assessed["obligations"][-1]["evidence"],
            [{"test_case_id": cases[1].id, "step": 2, "action": common["action"], "expected": common["expected"]}],
        )
        self.assertNotIn("shared_steps", review.review_prompt(data, digest))

    def test_wrong_group_hash_and_duplicate_or_missing_evidence_fail_closed(self):
        for defect, code in (
            ("stale", "content_binding_mismatch"),
            ("duplicate", "duplicate_obligation_review"),
            ("missing_case", "incomplete_case_review"),
            ("wrong_source", "incomplete_requirement_review"),
        ):

            def critic(settings, prompt, **kwargs):
                data, output = critic_output(prompt)
                group = data["review_group"]
                if group["requirement_ids"] == ["D"]:
                    if defect == "stale":
                        output["input_hash"] = group["suite_input_hash"]
                    elif defect == "duplicate":
                        output["obligations"] *= 2
                    elif defect == "missing_case":
                        output["case_grounding"].clear()
                    else:
                        output["obligations"][0]["requirement_id"] = "E"
                return json.dumps(output)

            with self.subTest(defect=defect):
                result = self.assess(critic)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["diagnostics"]["unverified_group_count"], 1)
                self.assertEqual(result["diagnostics"]["groups"][1]["validation_error"], code)

    def test_expired_or_late_group_cannot_return_evidence(self):
        data, digest, _ = review.review_input(self.cases, self.requirements, self.context)
        group = review._plan_review_groups(data, digest, review.MAX_INPUT_BYTES)[0]
        _, valid = critic_output(group.prompt)
        for clock, expected_calls in (([2], 0), ([0, 2], 1)):
            with (
                self.subTest(clock=clock),
                patch.object(review, "monotonic", side_effect=clock),
                patch.object(review, "_call_critic", return_value=json.dumps(valid)) as critic,
            ):
                result = review._assess_input(group.data, group.input_hash, group.prompt, object(), budget=review.MAX_INPUT_BYTES, deadline=1)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["diagnostics"]["failure_reason"], "review_deadline")
                self.assertEqual(result["diagnostics"]["attempt_count"], expected_calls)
                self.assertEqual(critic.call_count, expected_calls)

    def test_execution_prerequisite_does_not_turn_into_a_design_gap(self):
        cases = [
            TestCase.model_validate(
                {
                    **case.model_dump(),
                    "execution_prerequisites": [{"name": "account", "kind": "credential", "description": "Test user", "status": "missing"}],
                }
            )
            if case.id == "TC-F"
            else case
            for case in self.cases
        ]

        def critic(settings, prompt, **kwargs):
            data, output = critic_output(prompt)
            if data["review_group"]["requirement_ids"] == ["F"]:
                output["case_grounding"][0]["prerequisites"] = [
                    {
                        "description": "Provision the test user",
                        "status": "missing_prerequisite",
                        "required": True,
                        "reason": "The declared account is missing",
                        "execution_prerequisite_name": "account",
                    }
                ]
            return json.dumps(output)

        result = self.assess(critic, cases=cases)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["design_status"], "assessed_complete")
        self.assertEqual(result["execution_status"], "blocked")
        self.assertEqual(result["execution_blockers"], [{"test_case_id": "TC-F", "prerequisite_name": "account"}])
        self.assertEqual(review.assessment_tasks(result, cases), [])

    def test_selected_repair_and_indivisible_scope_keep_one_full_review(self):
        calls = []

        def critic(settings, prompt, **kwargs):
            data, output = critic_output(prompt)
            self.assertNotIn("review_group", data)
            calls.append(data["scope"])
            return json.dumps(output)

        self.assertEqual(self.assess(critic, scope_plan=[])["status"], "assessed_complete")
        indivisible = [scenario_case("all", [req.id for req in self.requirements])]
        self.assertEqual(self.assess(critic, cases=indivisible)["status"], "assessed_complete")
        self.assertEqual(calls, ["selected_scenarios", "whole_requirements"])

    def test_generation_uses_combined_review_gate(self):
        requirements = self.requirements[3:]
        cases = self.cases[:1] + self.cases[2:3] + self.cases[4:]
        plans = [
            {
                "requirement_id": req.id,
                "requirement_text": req.text,
                "scenarios": [{"id": f"{req.id}-SCN-01", "requirement_id": req.id, "title": "Receipt", "objective": req.text}],
            }
            for req in requirements
        ]
        payload = GenerateTestCasesInput(requirements=requirements, template=TestCaseTemplate(name="Tests", format="json", fields=["steps"]))
        workflow = {"test_cases": [row.model_dump() for row in cases], "coverage_plan": plans, "approved": True, "review": {"approved": True, "score": 100}}

        def critic(settings, prompt, **kwargs):
            data, output = critic_output(prompt)
            if data["review_group"]["requirement_ids"] == ["E"]:
                raise httpx.ReadTimeout("synthetic provider timeout")
            return json.dumps(output)

        with (
            patch.object(test_case_agent, "_get_model_settings_or_none", return_value=SimpleNamespace(model_name="fixture")),
            patch.object(test_case_agent, "_run_workflow_sync", return_value=workflow),
            patch.object(review, "_call_critic", side_effect=critic),
        ):
            result = test_case_agent.generate_test_cases(payload)
        self.assertEqual({TestCase.model_validate(row).id for row in result["test_cases"]}, {case.id for case in cases})
        self.assertFalse(result["approved"])
        self.assertEqual(result["substantive_assessment"]["model_status"], "unknown")
        self.assertEqual(result["substantive_assessment"]["diagnostics"]["unverified_group_count"], 1)
