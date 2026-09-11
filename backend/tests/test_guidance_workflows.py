from copy import deepcopy
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app, get_current_user
from app.models import AuthUser, AutomationResponse, RequirementsWorkflowResponse
from app.services import guidance_runtime as runtime
from app.services.guidance_service import build_manifest, guidance_scope, current_guidance, public_manifest, memory_service_for_run, apply_agent_guidance
from app.services.knowledge_feedback import finish_generation
from app.contracts.guidance import MemoryGuidance


class FakeDocument:
    def __init__(self, database, key):
        self.db, self.key = database, key

    def get(self, **kwargs):
        return SimpleNamespace(to_dict=lambda: deepcopy(self.db.get(self.key)))

    def document(self, key):
        return FakeDocument(self.db, self.key + "/" + key)


class FakeClient:
    def __init__(self):
        self.db = {}

    def collection(self, key):
        return FakeDocument(self.db, key)

    def transaction(self):
        return self

    def set(self, doc, value):
        self.db[doc.key] = deepcopy(value)


class GuidanceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.actor = AuthUser(sub="owner", name="Owner", email="owner@example.com")
        self.memory = MemoryGuidance(id="m1", revision=1, scope="project", text="Use Supervisor terminology", source="Human feedback", reason="project_stage")
        self.env = patch.dict(
            "os.environ", {"ADK_MEMORY_ENABLED": "true", "ADK_SKILLS_ENABLED": "true", "ADK_GUIDANCE_STAGES": "requirements,use_cases,test_cases,automation"}
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_durable_retry_keeps_old_revision_without_retrieving_new_selection(self):
        client = FakeClient()
        with (
            patch.object(runtime, "get_required_firestore_client", return_value=client),
            patch.object(runtime, "transactional", side_effect=lambda f: f),
            patch.object(runtime, "resolve_memories", return_value=([self.memory], [], "v1")) as resolve,
        ):
            first = runtime.prepare_run("test_cases", self.actor, "p", "request", input_fingerprint="inputs")
            with patch.object(runtime, "knowledge_version", return_value={"text": self.memory.text}):
                second = runtime.prepare_run("test_cases", self.actor, "p", "request", input_fingerprint="inputs")
            self.assertEqual(first, second)
            resolve.assert_called_once()
            self.assertNotIn(self.memory.text, str(client.db))
            with self.assertRaises(HTTPException) as caught:
                runtime.prepare_run("test_cases", self.actor, "p", "request", input_fingerprint="changed")
            self.assertEqual(caught.exception.status_code, 409)
            # A different owner never receives the first owner's manifest.
            other = self.actor.model_copy(update={"sub": "another"})
            runtime.prepare_run("test_cases", other, "p", "request", input_fingerprint="inputs")
            self.assertEqual(len(client.db), 2)

    def test_deleted_saved_memory_blocks_resume_instead_of_silently_changing_context(self):
        manifest = build_manifest("use_cases", "model", memories=[self.memory])
        with patch.object(runtime, "knowledge_version", return_value={"unavailable": True}):
            with self.assertRaises(RuntimeError):
                runtime.restore_manifest(public_manifest(manifest), self.actor, "p")

    def test_memory_failure_stops_before_model_and_explicit_bypass_is_recorded(self):
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.addCleanup(app.dependency_overrides.clear)
        with (
            patch.object(runtime, "get_required_firestore_client", side_effect=RuntimeError("unavailable")),
            patch(
                "app.main.generate_playwright_pom",
                side_effect=lambda _: self.assertIsNotNone(current_guidance()) or AutomationResponse(status="generated", files=[]),
            ) as generate,
            patch("app.main.start_workflow_run", return_value="run") as start,
            patch("app.routers.automation.project_for"),
            patch("app.routers.automation.append_stage_snapshot"),
            patch("app.main.complete_workflow_run"),
            patch("app.main.record_usage_event", return_value="event"),
            TestClient(app) as client,
        ):
            body = {"project_id": "p", "test_cases": []}
            response = client.post("/automation/playwright", json=body, headers={"X-Request-ID": "retry"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["code"], "knowledge_unavailable")
            generate.assert_not_called()
            start.assert_not_called()
            response = client.post("/automation/playwright", json=body, headers={"X-Request-ID": "retry", "X-Knowledge-Bypass": "true"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["guidance"]["memory_bypassed"])
            self.assertEqual(response.json()["guidance"]["memories"], [])

    def test_failed_suggestion_preserves_success_and_retry_payload(self):
        response = RequirementsWorkflowResponse(requirements=[], raw_text="source", source_name="fixture", approved=True)
        with (
            guidance_scope(build_manifest("requirements", "model")),
            patch("app.services.knowledge_feedback.mutate_knowledge", side_effect=RuntimeError("offline")),
        ):
            result = finish_generation(response, self.actor, "p", "requirements", "Include negative cases", "review-1")
        self.assertTrue(result.approved)
        self.assertEqual(result.knowledge_suggestion["status"], "failed")
        self.assertEqual(result.knowledge_suggestion["retry"]["source_request_id"], "review-1")
        self.assertIsNotNone(result.guidance)

    def test_instruction_adapter_keeps_base_contract_and_attaches_read_only_service(self):
        from google.adk.agents import LlmAgent

        agent = LlmAgent(name="generator", model="test", instruction="Return strict JSON only.")
        with guidance_scope(build_manifest("use_cases", "test", memories=[self.memory])) as manifest:
            apply_agent_guidance(agent)
            self.assertTrue(agent.instruction.startswith("Return strict JSON only."))
            self.assertIn(self.memory.text, agent.instruction)
            self.assertIn("Current source requirements", agent.instruction)
            self.assertIs(memory_service_for_run(self.actor.sub).manifest, manifest)

    def test_invalid_request_retains_validation_instead_of_guidance_500(self):
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.addCleanup(app.dependency_overrides.clear)
        with patch.dict("os.environ", {"ADK_MEMORY_ENABLED": "false"}), TestClient(app) as client:
            self.assertEqual(client.post("/testcases/generate", json=["not", "an", "object"]).status_code, 422)
