from pathlib import Path
import asyncio
import json
import sys
import unittest
from unittest.mock import patch

from google.genai import types
from google.genai.models import AsyncModels

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from evaluate_argon import ARGON, CONFLICT_PROFILES, PROFILES, conflict_fixtures, fixtures, sample, trial_matrix
from evaluate_guidance import collect_usage, payload_for
from review_argon import Review, score
from app.agents.context_evidence import prepare_context_evidence
from app.agents.evaluation_limits import EvaluationLimits


class ArgonEvaluationTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(AsyncModels, "_generate_content_with_continuation"), "SDK lacks automatic continuation support")
    def test_sdk_continuation_counts_hops_and_normalizes_terminal_reason(self):
        def response(fragment, reason, token=None):
            return types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(role="model", parts=[types.Part(text=fragment)]),
                        finish_reason=reason,
                        continuation_token=token,
                    )
                ],
                usage_metadata=types.GenerateContentResponseUsageMetadata(candidates_token_count=5, thoughts_token_count=2),
            )

        async def generate(client, **kwargs):
            return await client._generate_content_with_continuation(**kwargs)

        first = response('{"title":"Approve exp', types.FinishReason.CONTINUATION, b"next")
        last = response('ense"}', types.FinishReason.STOP)
        with (
            patch.object(AsyncModels, "generate_content", generate),
            patch.object(AsyncModels, "_generate_content", side_effect=[first, last]) as hop,
            collect_usage({"evidence": "tail marker"}) as stats,
        ):
            merged = asyncio.run(
                AsyncModels(None).generate_content(
                    model="gemini-4-argon",
                    contents="tail marker",
                    config=types.GenerateContentConfig(max_output_tokens=256_000),
                )
            )
        self.assertEqual(json.loads(merged.text), {"title": "Approve expense"})
        self.assertEqual((hop.await_count, stats["calls"], stats["continuation_requests"]), (2, 1, 1))
        self.assertEqual(stats["requests"][0]["finish_reasons"], ["STOP"])
        self.assertEqual(stats["requests"][0]["evidence_ids"], ["evidence"])
        self.assertEqual(stats["requests"][0]["token_usage"]["thinking"], stats["thinking_tokens"])

    def test_matrix_pairs_models_and_varies_limits_only_on_long_fixtures(self):
        matrix = list(trial_matrix())
        self.assertEqual(len(matrix), 48)
        self.assertEqual(len(set(matrix)), 48)
        for fixture, spec in fixtures().items():
            profiles = set(PROFILES) if fixture in {"project_long", "project_over_budget"} else {"flash-expanded", "argon-expanded"}
            self.assertEqual({profile for name, profile, _ in matrix if name == fixture}, profiles)
            self.assertEqual(payload_for(spec["requirements"], spec).workflow_settings.timeout_seconds, 600)

    def test_conflict_pilot_pairs_only_conflicts_and_uses_app_limits(self):
        matrix = list(trial_matrix(conflict_pilot=True))
        self.assertEqual(len(matrix), 18)
        self.assertEqual(set(matrix), {(name, profile, repeat) for name in conflict_fixtures() for profile in CONFLICT_PROFILES for repeat in (1, 2, 3)})
        with patch("evaluate_argon.guidance.evaluate") as evaluate:
            with self.assertRaises(KeyError):
                sample("directory", "argon-conflict-pilot")
            evaluate.assert_not_called()
            sample("conflicting_authorization", "argon-conflict-pilot")
        self.assertEqual(evaluate.call_args.kwargs["model"], ARGON)
        self.assertEqual(evaluate.call_args.kwargs["limits"], EvaluationLimits())
        spec = conflict_fixtures()["conflicting_threshold"]
        payload = payload_for(spec["requirements"], spec)
        self.assertNotIn("250 EUR", prepare_context_evidence(payload.requirements, payload.context, budget=128_000))
        self.assertIn("250 EUR", prepare_context_evidence(payload.requirements, payload.context))

    def test_long_fixtures_expose_the_context_limit_difference(self):
        for name in ("project_long", "project_over_budget"):
            spec = fixtures()[name]
            payload = payload_for(spec["requirements"], spec)
            current = prepare_context_evidence(payload.requirements, payload.context, budget=48_000)
            expanded = prepare_context_evidence(payload.requirements, payload.context, budget=128_000)
            for marker in spec["evidence_markers"].values():
                self.assertNotIn(marker, current)
                self.assertIn(marker, expanded)

    def test_blind_review_tracks_multiple_obligations_to_one_source(self):
        packet = {
            "requirements": [{"id": "R1", "text": "Only Supervisors may approve"}],
            "answer_key": {"authority": "Only Supervisors may approve", "conflict": "Flag the older rule"},
            "coverage_plan": [{"requirement_id": "R1", "scenarios": [{"id": "S1"}]}],
            "cases": [
                {
                    "id": "TC-1",
                    "title": "Deny non-Supervisor approval",
                    "linked_requirement_ids": ["R1"],
                    "scenario_refs": ["S1"],
                    "steps": [{"step": 1, "action": "Attempt approval", "expected": "The request is denied"}],
                }
            ],
        }
        review = Review.model_validate(
            {
                "obligations": [
                    {
                        "obligation_id": "authority",
                        "accurate": True,
                        "context_used": True,
                        "reason": "Explicit denial",
                        "evidence": [{"case_id": "TC-1", "step": 1}],
                    },
                    {"obligation_id": "conflict", "accurate": False, "context_used": False, "reason": "Conflict absent", "evidence": []},
                ],
                "readability": 3,
                "readability_reason": "Setup needs one clarification",
                "unsupported_assumptions": [],
            }
        )
        metrics = score(packet, review)
        self.assertTrue(metrics["structure"])
        self.assertEqual(metrics["traceability"], 0.5)
        review.obligations[0].evidence[0].step = 2
        with self.assertRaisesRegex(ValueError, "delivered step evidence"):
            score(packet, review)
