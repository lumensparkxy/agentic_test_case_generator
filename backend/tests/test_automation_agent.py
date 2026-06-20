from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.automation_agent import _AutomationFragmentResult, generate_playwright_pom
from app.config import GenerationSettings
from app.models import AutomationInput, TestCase, TestStep


def _test_case(index: int, *, component: str = "Authentication", automation_status: str = "To Be Automated", steps: bool = True) -> TestCase:
    return TestCase(
        id=f"TC-{index:03d}",
        title=f"Generated workflow {index}",
        component=component,
        automation_status=automation_status,
        steps=[TestStep(step=1, action=f"Open {component} page", expected=f"{component} page is visible")] if steps else [],
    )


class AutomationAgentTests(unittest.TestCase):
    def test_playwright_pom_uses_deterministic_fallback_without_model_credentials(self) -> None:
        payload = AutomationInput(
            test_cases=[
                TestCase(
                    id="TC-001",
                    title="Docs home loads",
                    automation_status="To Be Automated",
                    steps=[TestStep(step=1, action="Open docs home", expected="The page is visible")],
                )
            ],
            target_base_url="https://playwright.dev/python/",
        )

        with patch("app.agents.automation_agent.get_settings", side_effect=RuntimeError("GEMINI_API_KEY is required")):
            response = generate_playwright_pom(payload)

        self.assertEqual(response.status, "generated")
        self.assertIn("tests/generated/test_docs.py", response.files)
        self.assertIn("https://playwright.dev/python/", response.notes)
        self.assertIn("class GeneratedPage", response.notes)
        self.assertEqual(response.diagnostics["represented_test_case_count"], 1)
        self.assertEqual(response.case_diagnostics[0].status, "fallback")

    def test_large_fallback_suite_represents_every_case_after_old_caps(self) -> None:
        payload = AutomationInput(
            test_cases=[_test_case(index, component="Authentication" if index <= 18 else "Profile") for index in range(1, 36)],
            target_base_url="https://example.test/app",
        )

        with patch("app.agents.automation_agent.get_settings", side_effect=RuntimeError("GEMINI_API_KEY is required")):
            response = generate_playwright_pom(payload)

        represented = {diagnostic.test_case_id for diagnostic in response.case_diagnostics}
        self.assertEqual(len(represented), 35)
        self.assertEqual(response.diagnostics["represented_test_case_count"], 35)
        self.assertEqual(response.diagnostics["generated_case_count"], 35)
        self.assertGreaterEqual(response.diagnostics["shard_count"], 2)
        self.assertIn("test_tc_035_generated_workflow_35", response.notes)

    def test_manual_and_unsupported_cases_are_explicit_diagnostics(self) -> None:
        payload = AutomationInput(
            test_cases=[
                _test_case(1, automation_status="Manual"),
                _test_case(2, steps=False),
                _test_case(3, automation_status="Automated"),
            ],
            target_base_url="https://example.test/app",
        )

        with patch("app.agents.automation_agent.get_settings", side_effect=RuntimeError("GEMINI_API_KEY is required")):
            response = generate_playwright_pom(payload)

        diagnostics_by_id = {diagnostic.test_case_id: diagnostic for diagnostic in response.case_diagnostics}
        self.assertEqual(diagnostics_by_id["TC-001"].status, "manual")
        self.assertEqual(diagnostics_by_id["TC-002"].status, "unsupported")
        self.assertEqual(diagnostics_by_id["TC-003"].status, "fallback")
        self.assertEqual(response.diagnostics["manual_case_count"], 1)
        self.assertEqual(response.diagnostics["unsupported_case_count"], 1)

    def test_parallel_fragments_are_assembled_with_duplicate_names_deduped(self) -> None:
        payload = AutomationInput(
            test_cases=[
                _test_case(1, component="Authentication"),
                _test_case(2, component="Profile"),
            ],
            target_base_url="https://example.test/app",
        )
        settings = GenerationSettings(parallel_automation_min_cases=1, parallel_automation_max_workers=2)

        def worker(*, shard, **_kwargs):
            return _AutomationFragmentResult(
                shard=shard,
                files={
                    "tests/generated/test_duplicate.py": """from playwright.sync_api import Page


def test_duplicate(page: Page) -> None:
    \"\"\"Duplicate worker test.\"\"\"
    assert page is not None
"""
                },
                case_diagnostics=[
                    {
                        "test_case_id": test_case.id,
                        "title": test_case.title,
                        "status": "generated",
                        "reason": "Generated by patched worker.",
                        "shard_id": shard.shard_id,
                    }
                    for test_case in shard.test_cases
                ],
                represented_case_ids={test_case.id for test_case in shard.test_cases},
                merge_warnings=[],
            )

        with (
            patch("app.agents.automation_agent.get_generation_settings", return_value=settings),
            patch("app.agents.automation_agent.get_settings", return_value=SimpleNamespace(model_name="test-model", gemini_api_key="key")),
            patch("app.agents.automation_agent._run_model_automation_fragment_worker", side_effect=worker),
        ):
            response = generate_playwright_pom(payload)

        self.assertIn("tests/generated/test_duplicate.py", response.files)
        self.assertIn("tests/generated/test_duplicate_2.py", response.files)
        self.assertIn("def test_duplicate(", response.notes)
        self.assertIn("def test_duplicate_2(", response.notes)
        self.assertEqual(response.diagnostics["failed_shard_count"], 0)
        self.assertTrue(response.diagnostics["merge_warnings"])

    def test_failed_parallel_shard_falls_back_for_affected_cases_only(self) -> None:
        payload = AutomationInput(
            test_cases=[
                _test_case(1, component="Authentication"),
                _test_case(2, component="Profile"),
                _test_case(3, component="Search"),
            ],
            target_base_url="https://example.test/app",
        )
        settings = GenerationSettings(parallel_automation_min_cases=1, parallel_automation_max_workers=3)

        def maybe_fail(*, shard, **_kwargs):
            if shard.group_name == "Profile":
                raise RuntimeError("synthetic shard failure")
            return _AutomationFragmentResult(
                shard=shard,
                files={
                    f"tests/generated/test_{shard.index}.py": "from playwright.sync_api import Page\n\n\ndef test_ok(page: Page) -> None:\n    assert page is not None\n"
                },
                case_diagnostics=[
                    {
                        "test_case_id": test_case.id,
                        "title": test_case.title,
                        "status": "generated",
                        "reason": "Generated by patched worker.",
                        "shard_id": shard.shard_id,
                    }
                    for test_case in shard.test_cases
                ],
                represented_case_ids={test_case.id for test_case in shard.test_cases},
                merge_warnings=[],
            )

        with (
            patch("app.agents.automation_agent.get_generation_settings", return_value=settings),
            patch("app.agents.automation_agent.get_settings", return_value=SimpleNamespace(model_name="test-model", gemini_api_key="key")),
            patch("app.agents.automation_agent._run_model_automation_fragment_worker", side_effect=maybe_fail),
        ):
            response = generate_playwright_pom(payload)

        diagnostics_by_id = {diagnostic.test_case_id: diagnostic for diagnostic in response.case_diagnostics}
        self.assertEqual(diagnostics_by_id["TC-001"].status, "generated")
        self.assertEqual(diagnostics_by_id["TC-002"].status, "fallback")
        self.assertEqual(diagnostics_by_id["TC-003"].status, "generated")
        self.assertEqual(response.diagnostics["failed_shard_count"], 1)
        self.assertEqual(response.diagnostics["fallback_shard_count"], 1)


if __name__ == "__main__":
    unittest.main()
