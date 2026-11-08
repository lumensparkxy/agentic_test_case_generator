from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import asyncio
import json
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "scripts")]
import evaluate_guidance as evaluation
from app.config import get_settings, model_scope
from app.agents.context_evidence import MAX_CONTEXT_CHARS, prepare_context_evidence
from app.agents.test_case_agent import (
    _build_generation_pipeline,
    _build_test_case_generator_agent,
    _combined_event_text,
    _prepare_workflow_inputs,
    _record_event_error,
)
from app.agents.evaluation_limits import EvaluationLimits, evaluation_limits_scope
from app.agents.quality_goal import QUALITY_GOAL
from app.services.guidance_service import apply_agent_guidance
from app.contracts.grounding import EnrichInput, GroundedContext, GroundedUIElement, GroundedApiSurface
from app.models import Requirement, TestCaseTemplate
from plain_english_test_framework.playwright_generator import _render_action, PlaywrightGenerationError


class QualityHarnessTests(unittest.TestCase):
    def test_trial_context_and_writer_limits_are_scoped(self):
        requirement = Requirement(id="R", text="Verify the control")
        marker = "TAIL-EVIDENCE"
        context = EnrichInput(requirements=[requirement], notes="x" * 180_000 + marker)
        self.assertIn(marker, prepare_context_evidence([requirement], context))
        with evaluation_limits_scope(EvaluationLimits(context_chars=128_000, case_output_tokens=256_000)):
            self.assertNotIn(marker, prepare_context_evidence([requirement], context))
            for model, expected in (("gemini-4-argon", 256_000), ("gemini-3.8-flash", 20_000)):
                agent = _build_test_case_generator_agent(model, "R", "context", "template")
                self.assertEqual(agent.generate_content_config.max_output_tokens, expected)
        self.assertIn(marker, prepare_context_evidence([requirement], context))
        self.assertEqual(_build_test_case_generator_agent("gemini-4-argon", "R", "context", "template").generate_content_config.max_output_tokens, 20_000)

    def test_continued_json_joins_without_separators_and_omits_thoughts(self):
        from google.genai import types

        content = types.Content(parts=[types.Part(text="internal thought", thought=True), types.Part(text='{"title":"Approve exp'), types.Part(text='ense"}')])
        event = SimpleNamespace(content=content)
        self.assertEqual(json.loads(_combined_event_text(event)), {"title": "Approve expense"})
        for finish_reason in (types.FinishReason.MAX_TOKENS, types.FinishReason.CONTINUATION):
            diagnostics = {"status": "completed", "failure_reason": None, "warnings": []}
            _record_event_error(diagnostics, "TestCaseGeneratorAgent", SimpleNamespace(finish_reason=finish_reason))
            self.assertEqual(diagnostics["status"], "partial")
            self.assertEqual(diagnostics["failure_reason"], "incomplete_model_output")

    def test_app_context_limit_applies_to_oversized_omission_ids(self):
        requirement = Requirement(id="R", text="Verify the control")
        notes = "x" * 100_000 + "TAIL-EVIDENCE"
        context = EnrichInput(
            requirements=[requirement],
            notes=notes,
            grounded_context=GroundedContext(ui_elements=[GroundedUIElement(id="x" * 180_000, name="Control", description="Control")]),
        )
        rendered = prepare_context_evidence([requirement], context)
        self.assertEqual(MAX_CONTEXT_CHARS, 256_000)
        self.assertLessEqual(len(rendered), MAX_CONTEXT_CHARS)
        selected = json.loads(rendered)
        self.assertEqual(selected["notes"], notes)
        self.assertEqual(selected["omissions"], {"count": 1, "ids": [], "listed_count": 0})

    def test_default_review_budget_allows_large_input_without_bypassing_validation(self):
        from app.agents.substantive_review import assess_delivered_suite

        requirement = Requirement(id="R", text="Verify the control")
        context = EnrichInput(requirements=[requirement], notes="x" * 180_000)
        with patch("app.agents.substantive_review._call_critic", return_value="invalid JSON") as critic:
            with evaluation_limits_scope(EvaluationLimits(review_bytes=160_000)):
                baseline = assess_delivered_suite([{"id": "TC-1"}], [requirement], context, settings=object())
            critic.assert_not_called()
            self.assertIn("bounded review size", baseline["reason"])
            trial = assess_delivered_suite([{"id": "TC-1"}], [requirement], context, settings=object())
            self.assertEqual(critic.call_count, 2)
            self.assertEqual(trial["status"], "unknown")
            self.assertEqual(trial["diagnostics"]["validation_error"], "malformed_json")
            oversized = context.model_copy(update={"notes": "x" * 512_000})
            result = assess_delivered_suite([{"id": "TC-1"}], [requirement], oversized, settings=object())
            self.assertEqual(critic.call_count, 2)
            self.assertIn("bounded review size", result["reason"])

    def test_requested_model_controls_settings_and_restores_them(self):
        before = get_settings().model_name
        with model_scope("candidate"):
            self.assertEqual(get_settings().model_name, "candidate")
            with model_scope("nested"):
                self.assertEqual(get_settings().model_name, "nested")
            self.assertEqual(get_settings().model_name, "candidate")
        self.assertEqual(get_settings().model_name, before)

    def test_large_context_retains_behavior_fields_and_reports_budget_omissions(self):
        req = Requirement(id="R", text="Supervisor approves expense", source_excerpt="x" * 260 + "TAIL-MARKER")
        context = EnrichInput(
            requirements=[req],
            grounded_context=GroundedContext(
                ui_elements=[GroundedUIElement(id=f"UI-{i}", source_id="SOURCE", name=f"Control {i}", description="Role-sensitive action") for i in range(20)],
                api_surfaces=[GroundedApiSurface(id="API", name="Approve", description="Supervisor only", auth_required=True)],
            ),
        )
        requirement_text, context_text, _ = _prepare_workflow_inputs([req], context, TestCaseTemplate(name="default", format="table", fields=[]))
        self.assertIn("TAIL-MARKER", requirement_text)
        self.assertIn('"UI-19"', context_text)
        self.assertIn('"auth_required": true', context_text)
        self.assertIn("Role-sensitive action", context_text)
        limited = json.loads(prepare_context_evidence([req], context, budget=2400))
        self.assertGreater(limited["omissions"]["count"], 0)
        self.assertIn("unknown/blocked", limited["evidence_limit"])

    def test_reviewers_share_goal_and_original_grounded_context(self):
        pipeline = apply_agent_guidance(_build_generation_pipeline("model", "requirements", "CONTEXT-MARKER", "template", 1, 85), "test_cases")
        loop = next(agent for agent in pipeline.sub_agents if "Loop" in agent.name)
        for agent in loop.sub_agents:
            self.assertIn("CONTEXT-MARKER", agent.instruction)
            self.assertIn(QUALITY_GOAL, agent.instruction)
        from google.adk.agents import LlmAgent

        agent = LlmAgent(name="CallableReviewer", instruction=lambda _: "review", model="model")
        apply_agent_guidance(agent, "use_cases")
        self.assertIn(QUALITY_GOAL, asyncio.run(agent.instruction(None)))

    def test_changed_output_cannot_reuse_old_review(self):
        output = {"test_cases": ["original"]}
        hashed = evaluation.canonical_hash(output)
        output["test_cases"] = ["modified"]
        with self.assertRaises(ValueError):
            evaluation.adjudicate({"results": [{"id": "x", "output": output, "output_hash": hashed}]}, {"x": {"output_hash": hashed}})

    def test_partial_suite_is_not_complete_even_when_requirement_ids_match(self):
        output = {"test_cases": [{"linked_requirement_ids": ["R"]}], "workflow_diagnostics": {"status": "partial"}, "generation_tasks": [{"id": "pending"}]}
        self.assertIn("incomplete_generation", evaluation.validate_artifact("test_cases", output, [{"id": "R"}]))
        self.assertTrue(evaluation.contains_fallback({"status": "timed_out"}))

    def test_failed_usage_remains_unknown_and_prompt_content_is_hashed(self):
        from google.genai.models import Models

        with patch.object(Models, "generate_content", side_effect=RuntimeError("synthetic")):
            with evaluation.collect_usage() as usage:
                with self.assertRaises(RuntimeError):
                    Models.generate_content(None, model="candidate", contents="private synthetic marker", config=None)
        self.assertEqual(usage["missing_usage"], 1)
        self.assertEqual(usage["failed_calls"], 1)
        self.assertNotIn("private synthetic marker", json.dumps(usage))
        self.assertEqual(usage["requests"][0]["model"], "candidate")

    def test_usage_capture_allows_adk_to_pass_a_response_schema_class(self):
        from google.genai import types
        from google.genai.models import AsyncModels

        config = types.GenerateContentConfig(response_schema=Requirement, temperature=0.2, system_instruction="private instructions")
        response = types.GenerateContentResponse(usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=5, candidates_token_count=2))
        with patch.object(AsyncModels, "generate_content", return_value=response) as generate:
            with evaluation.collect_usage() as usage:
                result = asyncio.run(AsyncModels.generate_content(None, model="candidate", contents="private fixture", config=config))
        generate.assert_awaited_once_with(None, model="candidate", contents="private fixture", config=config)
        self.assertIs(result, response)
        self.assertEqual(usage["requests"][0]["config"], {"temperature": 0.2})
        self.assertEqual((usage["calls"], usage["input_tokens"], usage["output_tokens"]), (1, 5, 2))
        self.assertNotIn("private", json.dumps(usage))

    def test_runtime_binding_reads_only_test_namespace_and_never_embeds_value(self):
        step = {"action": "fill", "locator": {"strategy": "label", "value": "Password"}, "value": "[[env:PETF_TEST_LOGIN]]"}
        with patch.dict("os.environ", {"PETF_TEST_LOGIN": "not-in-script"}):
            script = _render_action(step)
        self.assertIn('process.env["PETF_TEST_LOGIN"]', script)
        self.assertNotIn("not-in-script", script)
        with self.assertRaises(PlaywrightGenerationError):
            _render_action({**step, "value": "[[env:GOOGLE_API_KEY]]"})

    def test_new_generation_cannot_drop_a_corrected_project_prerequisite(self):
        from app.agents.test_case_agent import _build_response
        from app.models import TestCase

        req = Requirement(id="R", text="Supervisors approve submitted expenses")
        context = EnrichInput(
            requirements=[req],
            execution_prerequisites=[
                {
                    "name": "supervisor",
                    "kind": "credential",
                    "description": "Supervisor role",
                    "reference": "env:PETF_TEST_SUPERVISOR",
                    "status": "configured",
                    "requirement_ids": ["R"],
                }
            ],
        )
        case = TestCase(
            id="TC",
            title="Approve expense",
            linked_requirement_ids=["R"],
            steps=[{"step": 1, "action": "Approve submitted expense", "expected": "Expense is approved"}],
        )
        response = _build_response([case], {}, [req], context)
        self.assertEqual(response["test_cases"][0].execution_prerequisites[0].reference, "env:PETF_TEST_SUPERVISOR")
        self.assertEqual(response["test_cases"][0].steps[0].expected, "Expense is approved")

    def test_provenance_mutation_cannot_reuse_a_review(self):
        result = {"id": "x", "stage": "test_cases", "arm": "baseline", "output": {"cases": []}, "output_hash": evaluation.canonical_hash({"cases": []})}
        provenance = {"model": "original"}
        result["run_hash"] = evaluation.canonical_hash(
            {"provenance": provenance, **{key: result.get(key) for key in ("input_hash", "output_hash", "guidance", "usage", "obligations")}}
        )
        with self.assertRaises(ValueError):
            evaluation.adjudicate(
                {"provenance": {"model": "changed"}, "results": [result]}, {"x": {"output_hash": result["output_hash"], "run_hash": result["run_hash"]}}
            )

    def test_reenriching_context_preserves_corrected_prerequisites(self):
        from starlette.requests import Request
        from app.models import AuthUser
        from app.routers.requirements import enrich_requirements

        payload = EnrichInput(
            requirements=[],
            grounded_context=GroundedContext(),
            execution_prerequisites=[
                {"name": "employee", "kind": "test_data", "description": "Named employee", "reference": "fixture:employee", "status": "configured"}
            ],
            project_id="synthetic-project",
            base_project_revision=2,
        )
        with (
            patch("app.routers.requirements.start_workflow_run", return_value="run"),
            patch("app.routers.requirements._log_success", return_value="event"),
            patch("app.routers.requirements.append_stage_snapshot") as append_snapshot,
        ):
            response = asyncio.run(
                enrich_requirements(Request({"type": "http", "headers": []}), payload, AuthUser(sub="synthetic-owner", name="Synthetic owner"))
            )
        self.assertEqual(response.execution_prerequisites, payload.execution_prerequisites)
        self.assertEqual(
            append_snapshot.call_args.kwargs["payload"]["execution_prerequisites"], [item.model_dump() for item in payload.execution_prerequisites]
        )
