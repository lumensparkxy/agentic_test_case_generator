from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.specialist_contracts import (
    SPECIALIST_AGENT_CONTRACT_VERSION,
    RequirementTaskInput,
    RequirementTaskOutput,
    SpecialistTaskTrace,
)
from app.agents.specialist_registry import SpecialistAgentAdapter, SpecialistAgentRegistry, get_default_agent_registry
from app.models import QaProjectStageSnapshot


class SpecialistAgentRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trace = SpecialistTaskTrace(
            request_id="req-123",
            workflow_run_id="wf-123",
            actor_user_id="user-1",
            actor_email="user@example.com",
            project_id="project-1",
            project_revision=3,
            source_event_id="event-1",
            source_snapshot_ids={"requirements": "snap-req-1", "test_cases": "snap-tc-1"},
        )
        self.requirement = {
            "id": "REQ-001",
            "text": "Users can submit an expense report for approval.",
            "review_status": "Approved",
        }
        self.template = {
            "name": "Standard QA",
            "format": "structured",
            "fields": ["id", "title", "steps", "expected_result"],
        }
        self.test_case = {
            "id": "TC-001",
            "title": "Submit expense report",
            "status": "Ready",
            "automation_status": "To Be Automated",
            "steps": [
                {
                    "step": 1,
                    "action": "Open https://example.com",
                    "expected": "Page loads successfully",
                }
            ],
            "linked_requirement_ids": ["REQ-001"],
            "scenario_refs": ["REQ-001-SCN-01"],
        }
        self.review = {
            "approved": True,
            "score": 95,
            "threshold": 90,
            "summary": "Approved",
            "blocking_issues": [],
            "suggestions": [],
            "unmet_criteria": [],
        }
        self.generation_response = {
            "test_cases": [self.test_case],
            "approved": True,
            "review": self.review,
            "iteration_history": [
                {
                    "iteration": 1,
                    "actor": "TestCaseValidatorAgent",
                    "approved": True,
                    "score": 95,
                    "threshold": 90,
                    "summary": "Approved",
                    "artifact_count": 1,
                    "artifact_ids": ["TC-001"],
                }
            ],
            "coverage_plan": [
                {
                    "requirement_id": "REQ-001",
                    "requirement_text": self.requirement["text"],
                    "scenarios": [
                        {
                            "id": "REQ-001-SCN-01",
                            "requirement_id": "REQ-001",
                            "scenario_type": "Happy Path",
                            "title": "Submit expense",
                            "objective": "Verify expense submission.",
                            "priority": "High",
                        }
                    ],
                }
            ],
            "requirement_analysis": [],
            "coverage_metrics": {"traceability": {"linked_requirement_count": 1}},
            "workflow_settings": {},
            "workflow_diagnostics": {},
        }

    def _snapshot(self, stage: str, payload: dict, snapshot_id: str) -> QaProjectStageSnapshot:
        return QaProjectStageSnapshot(
            snapshot_id=snapshot_id,
            project_id="project-1",
            stage=stage,
            version=1,
            project_revision=1,
            operation="seed",
            approved=True,
            payload=payload,
            created_at=datetime.now(timezone.utc),
        )

    def test_manifest_covers_all_epic_86_specialist_agents(self) -> None:
        manifest = get_default_agent_registry().manifest()

        kinds = {item["agent_kind"] for item in manifest}
        self.assertEqual(
            kinds,
            {
                "requirements",
                "use_cases",
                "impact",
                "test_cases",
                "automation",
                "execution",
                "review",
                "report",
            },
        )
        for item in manifest:
            self.assertEqual(item["contract_version"], SPECIALIST_AGENT_CONTRACT_VERSION)
            self.assertEqual(item["implementation"], "local")
            self.assertTrue(item["input_model"].endswith("Input"))
            self.assertTrue(item["output_model"].endswith("Output"))

    def test_default_registry_dispatches_all_local_contracts_with_schema_valid_outputs(self) -> None:
        registry = get_default_agent_registry()
        requirements_output = {
            "requirements": [self.requirement],
            "approved": True,
            "review": self.review,
            "iteration_history": [],
            "coverage_metrics": {},
            "workflow_settings": {},
            "workflow_diagnostics": {},
        }
        current_requirements = self._snapshot("requirements", {"requirements": [self.requirement]}, "snap-req-current")
        baseline_requirements = self._snapshot("requirements", {"requirements": [self.requirement]}, "snap-req-baseline")
        use_cases = self._snapshot("use_cases", {"coverage_plan": self.generation_response["coverage_plan"]}, "snap-use-current")
        baseline_use_cases = self._snapshot("use_cases", {"coverage_plan": self.generation_response["coverage_plan"]}, "snap-use-baseline")
        test_cases = self._snapshot("test_cases", {"test_cases": [self.test_case]}, "snap-tc-baseline")

        payloads = {
            "requirements": {"text": self.requirement["text"], "document_count": 1},
            "use_cases": {"requirements": [self.requirement], "template": self.template},
            "impact": {
                "current_requirements_snapshot": current_requirements,
                "current_use_cases_snapshot": use_cases,
                "baseline_requirements_snapshot": baseline_requirements,
                "baseline_use_cases_snapshot": baseline_use_cases,
                "test_cases_snapshot": test_cases,
            },
            "test_cases": {"requirements": [self.requirement], "template": self.template},
            "automation": {"test_cases": [self.test_case], "target_base_url": "https://example.com"},
            "execution": {"mode": "preview", "preview": {"test_cases": [self.test_case], "target_base_url": "https://example.com"}},
            "review": {
                "stage": "test_cases",
                "review": self.review,
                "artifact_payload": {"test_cases": [self.test_case]},
                "traceability_ids": ["REQ-001", "REQ-001-SCN-01"],
            },
            "report": {
                "format": "json",
                "test_cases": [self.test_case],
                "approved": True,
                "review": self.review,
                "execution_run": {"status": "passed"},
            },
        }

        with (
            patch("app.agents.requirements_agent.extract_requirements", return_value=requirements_output) as extract_mock,
            patch("app.agents.test_case_agent.generate_test_cases", return_value=self.generation_response) as generate_mock,
            patch(
                "app.agents.automation_agent.generate_playwright_pom",
                return_value={"status": "generated", "files": ["tests/test_generated.py"], "notes": "synthetic"},
            ) as automation_mock,
        ):
            results = {kind: registry.dispatch(kind, payload, self.trace) for kind, payload in payloads.items()}

        self.assertEqual(extract_mock.call_args.kwargs["request_id"], "req-123")
        self.assertEqual(extract_mock.call_args.kwargs["workflow_run_id"], "wf-123")
        self.assertGreaterEqual(generate_mock.call_count, 2)
        self.assertEqual(automation_mock.call_count, 1)
        for kind, result in results.items():
            with self.subTest(kind=kind):
                self.assertEqual(result.status, "completed")
                self.assertEqual(result.trace.request_id, "req-123")
                self.assertEqual(result.trace.workflow_run_id, "wf-123")
                self.assertEqual(result.contract_version, SPECIALIST_AGENT_CONTRACT_VERSION)
                self.assertEqual(result.diagnostics, [])
                self.assertTrue(result.output_payload)
        self.assertEqual(results["requirements"].output_payload["requirements"][0]["id"], "REQ-001")
        self.assertEqual(results["impact"].output_payload["analysis"]["summary"]["changed_item_count"], 0)
        self.assertEqual(results["review"].output_payload["traceability_ids"], ["REQ-001", "REQ-001-SCN-01"])
        self.assertEqual(results["report"].output_payload["traceability_ids"], ["REQ-001", "REQ-001-SCN-01"])

    def test_input_validation_failure_returns_structured_diagnostic(self) -> None:
        result = get_default_agent_registry().dispatch(
            "execution",
            {"mode": "run", "preview": {"test_cases": [self.test_case]}},
            self.trace,
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.diagnostics[0].code, "agent_input_validation_failed")
        self.assertFalse(result.output_payload)

    def test_output_validation_failure_returns_structured_diagnostic(self) -> None:
        registry = SpecialistAgentRegistry()

        def malformed_output(_task_input, _trace):
            return {"requirements": [{"id": "REQ-001"}], "approved": True, "review": self.review}

        registry.register(SpecialistAgentAdapter("requirements", RequirementTaskInput, RequirementTaskOutput, malformed_output))

        result = registry.dispatch("requirements", {"text": self.requirement["text"]}, self.trace)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.diagnostics[0].code, "agent_output_validation_failed")
        self.assertIn("RequirementTaskOutput", result.diagnostics[0].message)


if __name__ == "__main__":
    unittest.main()
