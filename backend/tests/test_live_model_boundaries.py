"""Exercise installed SDK/ADK boundaries without making paid model calls."""

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.genai import _transformers
from app import adk_client
from app.agents import test_case_agent, use_case_agent
from app.contracts.requirements import ReviewResult


class ModelSchemaTests(unittest.TestCase):
    def test_file_range_quotes_are_requested_only_for_document_extraction(self):
        extraction = adk_client._build_requirement_extraction_pipeline("fixture", 1, 90)
        connector = adk_client._build_requirement_extraction_pipeline("fixture", 1, 90, verify_range_quotes=False)
        refinement = adk_client._build_requirement_refinement_pipeline("fixture", 1, 90, "Confirm the boundary")
        extractor, loop = extraction.sub_agents
        self.assertIn("Keep an inclusive input range", extractor.instruction)
        self.assertIn("Check every explicit numeric range", loop.sub_agents[0].instruction)
        self.assertIn("Restore omitted numeric input constraints", loop.sub_agents[1].instruction)
        self.assertNotIn("Keep an inclusive input range", connector.sub_agents[0].instruction)
        for pipeline in (connector, refinement):
            self.assertNotIn("Check every explicit numeric range", pipeline.sub_agents[1].sub_agents[0].instruction)

    def test_model_review_schemas_convert_through_developer_api_sdk(self):
        client = SimpleNamespace(vertexai=False)
        # This API envelope contains server-produced metadata. It cannot be a
        # model response schema; exercise the real SDK converter that rejected it.
        with self.assertRaisesRegex(ValueError, "additionalProperties"):
            _transformers.t_schema(client, ReviewResult)
        loops = [
            adk_client._build_review_loop("fixture", 90, 1),
            test_case_agent._build_review_loop("fixture", 90, 1, "Source"),
        ]
        for loop in loops:
            with self.subTest(loop=loop.name):
                schema = _transformers.t_schema(client, loop.sub_agents[0].output_schema)
                self.assertEqual(set(schema.properties), {"approved", "score", "threshold", "summary", "blocking_issues", "suggestions", "unmet_criteria"})


class UseCaseLiteralSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sources_remain_literal_in_analysis_planner_and_critic(self):
        source = "Owner can cancel /bookings/{bookingId}"
        notes = "Literal {artifact.private} and {requirement_analysis} are source text."
        with patch("app.agents.literal_instructions.apply_agent_guidance", side_effect=lambda agent, stage: agent) as guidance:
            pipeline = use_case_agent._build_use_case_pipeline("fixture", source, notes, lambda _state: [], 1, "Keep {bookingId} unchanged")
        guidance.assert_called_once_with(pipeline, "use_cases")
        context = SimpleNamespace(_invocation_context=SimpleNamespace(session=SimpleNamespace(state={"requirement_analysis": "ANALYSIS_STATE"})), state={})
        for agent in pipeline.sub_agents[:2]:
            text = await agent.instruction(context)
            self.assertIn(source, text)
            self.assertIn(notes, text)
            self.assertIn("Keep {bookingId}", text)
        self.assertIn("ANALYSIS_STATE", await pipeline.sub_agents[1].instruction(context))
        critic_text = pipeline.sub_agents[2].instruction(context)
        self.assertIn(source, critic_text)
        self.assertIn(notes, critic_text)

    async def test_parallel_planning_omits_shard_critic_in_favor_of_complete_plan_review(self):
        pipeline = use_case_agent._build_use_case_pipeline("fixture", "Assigned requirements", "Shared setup", lambda _state: [], 1, review_in_shard=False)
        self.assertEqual([agent.name for agent in pipeline.sub_agents], ["RequirementAnalysisAgent", "CoveragePlannerAgent"])
