from pathlib import Path
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
E2E_DIR = REPO_ROOT / "backend" / "e2e"
if str(E2E_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_DIR))

from workflow_runner import (
    AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT,
    DEFAULT_BASE_URL,
    DEFAULT_TARGET_URL,
    RealAdkE2EError,
    RealAdkWorkflowConfig,
    build_summary,
    config_from_env,
    mint_auth_token,
    real_adk_e2e_enabled,
    validate_environment,
    write_json_artifact,
)


class RealAdkE2EHarnessTests(unittest.TestCase):
    def test_real_adk_e2e_enabled_requires_explicit_gate(self) -> None:
        self.assertFalse(real_adk_e2e_enabled({}))
        self.assertFalse(real_adk_e2e_enabled({"RUN_REAL_ADK_E2E": "true"}))
        self.assertTrue(real_adk_e2e_enabled({"RUN_REAL_ADK_E2E": "1"}))

    def test_config_from_env_defaults_and_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            requirements = Path(tmpdir) / "requirements.md"
            requirements.write_text("REQ-1: The system shall support smoke tests.", encoding="utf-8")
            config = config_from_env(
                {
                    "REAL_ADK_E2E_BASE_URL": "http://127.0.0.1:9000/",
                    "REAL_ADK_E2E_OUTPUT_DIR": tmpdir,
                    "REAL_ADK_E2E_REQUIREMENTS_FILE": str(requirements),
                    "REAL_ADK_E2E_TARGET_URL": "https://example.test/docs/",
                    "REAL_ADK_E2E_TIMEOUT_SECONDS": "123",
                },
                env_file=None,
            )

        self.assertEqual(config.base_url, "http://127.0.0.1:9000")
        self.assertEqual(config.target_url, "https://example.test/docs/")
        self.assertEqual(config.timeout_seconds, 123)
        self.assertEqual(config.output_base_dir, Path(tmpdir))
        self.assertEqual(config.requirements_file, requirements)

    def test_config_from_env_rejects_invalid_timeout(self) -> None:
        with self.assertRaisesRegex(RealAdkE2EError, "REAL_ADK_E2E_TIMEOUT_SECONDS"):
            config_from_env({"REAL_ADK_E2E_TIMEOUT_SECONDS": "soon"}, env_file=None)

    def test_validate_environment_reports_missing_real_service_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            requirements = Path(tmpdir) / "requirements.md"
            requirements.write_text("REQ-1: The system shall support smoke tests.", encoding="utf-8")
            config = RealAdkWorkflowConfig(
                output_base_dir=Path(tmpdir),
                requirements_file=requirements,
                require_execution_runtime=False,
            )

            with self.assertRaises(RealAdkE2EError) as context:
                validate_environment(config, env={}, env_file=None)

        message = str(context.exception)
        self.assertIn("GEMINI_API_KEY or GOOGLE_API_KEY", message)
        self.assertIn("JWT_SECRET_KEY", message)
        self.assertIn(f"AUTH_TOKEN_MODE={AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT}", message)

    def test_validate_environment_allows_auth_token_without_local_jwt_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            requirements = Path(tmpdir) / "requirements.md"
            requirements.write_text("REQ-1: The system shall support smoke tests.", encoding="utf-8")
            config = RealAdkWorkflowConfig(
                output_base_dir=Path(tmpdir),
                requirements_file=requirements,
                require_execution_runtime=False,
            )

            validate_environment(config, env={"GEMINI_API_KEY": "test-key", "AUTH_TOKEN": "firebase-token"}, env_file=None)

    def test_mint_auth_token_uses_auth_token_override(self) -> None:
        self.assertEqual(mint_auth_token({"AUTH_TOKEN": "existing-token"}, env_file=None), "existing-token")

    def test_mint_auth_token_requires_compatible_mode(self) -> None:
        with self.assertRaisesRegex(RealAdkE2EError, "AUTH_TOKEN_MODE"):
            mint_auth_token({"JWT_SECRET_KEY": "secret"}, env_file=None)

    def test_mint_auth_token_requires_secret(self) -> None:
        with self.assertRaisesRegex(RealAdkE2EError, "JWT_SECRET_KEY"):
            mint_auth_token({"AUTH_TOKEN_MODE": AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT}, env_file=None)

    def test_write_json_artifact_and_summary_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            artifact_path = write_json_artifact(output_dir, "01_parse.json", {"requirements": [{"id": "REQ-1"}]})
            config = RealAdkWorkflowConfig(
                base_url=DEFAULT_BASE_URL,
                output_base_dir=output_dir,
                requirements_file=output_dir / "requirements.md",
                target_url=DEFAULT_TARGET_URL,
                require_execution_runtime=False,
            )
            summary = build_summary(
                config=config,
                artifacts={"parse": artifact_path},
                parse_result={"requirements": [{"id": "REQ-1"}], "workflow_diagnostics": {"warnings": ["parse warning"]}},
                enrich_result={"grounded_context": {"artifact_sources": [{"id": "source-1"}], "ui_elements": [{"id": "ui-1"}]}},
                generate_result={
                    "approved": False,
                    "review": {"score": 70, "threshold": 85, "summary": "Needs review", "blocking_issues": ["Draft"]},
                    "requirement_analysis": [{"requirement_id": "REQ-1"}],
                    "coverage_plan": [{"requirement_id": "REQ-1", "scenarios": [{"id": "SCN-1"}]}],
                    "test_cases": [{"id": "TC-1"}],
                    "workflow_diagnostics": {"warnings": ["generation warning"]},
                },
                automation_result={"status": "generated", "files": ["tests/test_docs.py"], "notes": "print('ok')"},
                preview_result={"summary": {"executable": 1, "manual": 0, "unsupported": 0, "invalid": 0}, "warnings": ["preview warning"]},
                exports={"json": {"path": str(output_dir / "06_export.json"), "bytes": 2}},
            )

            self.assertTrue(artifact_path.is_file())
            self.assertEqual(summary["counts"]["requirements"], 1)
            self.assertEqual(summary["counts"]["test_cases"], 1)
            self.assertEqual(summary["counts"]["coverage_plan"], 1)
            self.assertEqual(summary["approval"]["score"], 70)
            self.assertEqual(summary["automation"]["notes_characters"], len("print('ok')"))
            self.assertIn("parse", summary["artifact_paths"])
            self.assertEqual(summary["warnings"], ["parse warning", "generation warning", "preview warning"])


if __name__ == "__main__":
    unittest.main()
