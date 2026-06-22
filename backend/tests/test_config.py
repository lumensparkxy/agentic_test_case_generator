from pathlib import Path
import logging
import os
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import (
    AUTH_TOKEN_MODE_FIREBASE_ONLY,
    AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT,
    DEFAULT_MODEL_NAME,
    _SuppressNonTextPartsWarning,
    _load_environment_file,
    _warn_if_dependency_mismatch,
    get_auth_settings,
    get_azure_devops_settings,
    get_billing_settings,
    get_jira_settings,
    get_generation_settings,
    get_metrics_settings,
    get_settings,
)


class ConfigSettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_auth_settings.cache_clear()
        get_settings.cache_clear()
        get_billing_settings.cache_clear()
        get_jira_settings.cache_clear()
        get_azure_devops_settings.cache_clear()
        get_metrics_settings.cache_clear()
        get_generation_settings.cache_clear()

    def test_load_environment_file_prefers_project_env_over_existing_process_value(self) -> None:
        with TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            (temp_root / ".env").write_text("EXAMPLE_KEY=from_project_env\n", encoding="utf-8")

            with patch.dict(os.environ, {"EXAMPLE_KEY": "from_process_env"}, clear=False):
                with patch("app.config.REPO_ROOT", temp_root), patch("app.config.Path.cwd", return_value=temp_root):
                    _load_environment_file()

                self.assertEqual(os.environ.get("EXAMPLE_KEY"), "from_project_env")

    def test_get_settings_promotes_gemini_key_to_google_key(self) -> None:
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-only-key", "MODEL_NAME": DEFAULT_MODEL_NAME}, clear=False):
            os.environ.pop("GOOGLE_API_KEY", None)
            get_settings.cache_clear()
            with patch("app.config._warn_if_dependency_mismatch"):
                settings = get_settings()

            self.assertEqual(settings.gemini_api_key, "gemini-only-key")
            self.assertEqual(os.environ.get("GOOGLE_API_KEY"), "gemini-only-key")
            self.assertNotIn("GEMINI_API_KEY", os.environ)

    def test_get_settings_prefers_gemini_alias_and_removes_it_after_normalization(self) -> None:
        with patch.dict(
            os.environ,
            {"GOOGLE_API_KEY": "google-key", "GEMINI_API_KEY": "gemini-key", "MODEL_NAME": DEFAULT_MODEL_NAME},
            clear=False,
        ):
            get_settings.cache_clear()
            with patch("app.config._warn_if_dependency_mismatch"), self.assertLogs(level="WARNING") as logs:
                settings = get_settings()

            self.assertEqual(settings.gemini_api_key, "gemini-key")
            self.assertEqual(os.environ.get("GOOGLE_API_KEY"), "gemini-key")
            self.assertNotIn("GEMINI_API_KEY", os.environ)
            self.assertTrue(any("Both GOOGLE_API_KEY and GEMINI_API_KEY are set" in entry for entry in logs.output))

    def test_google_genai_non_text_warning_filter_suppresses_only_known_noise(self) -> None:
        log_filter = _SuppressNonTextPartsWarning()

        noisy_record = logging.LogRecord(
            name="google_genai.types",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="Warning: there are non-text parts in the response: ['function_call']",
            args=(),
            exc_info=None,
        )
        normal_record = logging.LogRecord(
            name="google_genai.types",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="A different warning should still appear",
            args=(),
            exc_info=None,
        )

        self.assertFalse(log_filter.filter(noisy_record))
        self.assertTrue(log_filter.filter(normal_record))

    def test_dependency_mismatch_accepts_current_adk_and_genai_versions(self) -> None:
        versions = {"google-adk": "2.2.0", "google-genai": "2.8.0"}

        with patch("app.config.version", side_effect=lambda package_name: versions[package_name]):
            with patch("app.config.logging.warning") as warning:
                _warn_if_dependency_mismatch()

        warning.assert_not_called()

    def test_dependency_mismatch_warns_for_versions_below_current_floor(self) -> None:
        versions = {"google-adk": "2.1.0", "google-genai": "2.7.0"}

        with patch("app.config.version", side_effect=lambda package_name: versions[package_name]):
            with patch("app.config.logging.warning") as warning:
                _warn_if_dependency_mismatch()

        warning_messages = [call.args[0] for call in warning.call_args_list]
        self.assertIn("google-adk version may be too old for current workflow patterns: %s", warning_messages)
        self.assertIn("google-genai version may be too old for current SDK behavior: %s", warning_messages)

    def test_get_auth_settings_parses_auth_token_mode(self) -> None:
        with patch.dict(os.environ, {"AUTH_TOKEN_MODE": AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT}, clear=True):
            get_auth_settings.cache_clear()
            settings = get_auth_settings()

        self.assertEqual(settings.auth_token_mode, AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT)

    def test_get_auth_settings_defaults_missing_auth_token_mode_with_warning(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            get_auth_settings.cache_clear()
            with self.assertLogs(level="WARNING") as logs:
                settings = get_auth_settings()

        self.assertEqual(settings.auth_token_mode, AUTH_TOKEN_MODE_FIREBASE_ONLY)
        self.assertTrue(any("AUTH_TOKEN_MODE is not configured" in entry for entry in logs.output))

    def test_get_auth_settings_defaults_invalid_auth_token_mode_with_warning(self) -> None:
        with patch.dict(os.environ, {"AUTH_TOKEN_MODE": "backend-jwt-only"}, clear=True):
            get_auth_settings.cache_clear()
            with self.assertLogs(level="WARNING") as logs:
                settings = get_auth_settings()

        self.assertEqual(settings.auth_token_mode, AUTH_TOKEN_MODE_FIREBASE_ONLY)
        self.assertTrue(any("Invalid AUTH_TOKEN_MODE=backend-jwt-only" in entry for entry in logs.output))

    def test_get_metrics_settings_defaults_to_enabled_without_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            get_metrics_settings.cache_clear()
            settings = get_metrics_settings()

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.access_token, "")

    def test_get_metrics_settings_parses_disablement_and_token(self) -> None:
        with patch.dict(os.environ, {"METRICS_ENABLED": "false", "METRICS_ACCESS_TOKEN": "metrics-secret"}, clear=True):
            get_metrics_settings.cache_clear()
            settings = get_metrics_settings()

        self.assertFalse(settings.enabled)
        self.assertEqual(settings.access_token, "metrics-secret")

    def test_get_generation_settings_parses_parallel_test_case_controls(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PARALLEL_TEST_CASE_GENERATION_ENABLED": "false",
                "PARALLEL_TEST_CASE_MIN_SCENARIOS": "12",
                "PARALLEL_TEST_CASE_MAX_WORKERS": "4",
                "PARALLEL_TEST_CASE_TARGET_SCENARIOS_PER_SHARD": "11",
                "PARALLEL_TEST_CASE_MAX_SHARDS": "17",
            },
            clear=True,
        ):
            get_generation_settings.cache_clear()
            settings = get_generation_settings()

        self.assertFalse(settings.parallel_test_case_generation_enabled)
        self.assertEqual(settings.parallel_test_case_min_scenarios, 12)
        self.assertEqual(settings.parallel_test_case_max_workers, 4)
        self.assertEqual(settings.parallel_test_case_target_scenarios_per_shard, 11)
        self.assertEqual(settings.parallel_test_case_max_shards, 17)

    def test_get_generation_settings_parses_parallel_automation_controls(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PARALLEL_AUTOMATION_GENERATION_ENABLED": "false",
                "PARALLEL_AUTOMATION_MIN_CASES": "24",
                "PARALLEL_AUTOMATION_MAX_WORKERS": "5",
            },
            clear=True,
        ):
            get_generation_settings.cache_clear()
            settings = get_generation_settings()

        self.assertFalse(settings.parallel_automation_generation_enabled)
        self.assertEqual(settings.parallel_automation_min_cases, 24)
        self.assertEqual(settings.parallel_automation_max_workers, 5)

    def test_get_billing_settings_parses_limits_launch_date_and_shadow_mode(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BILLING_CONTACT_EMAIL": "billing@example.com",
                "BILLING_LAUNCH_DATE": "2026-04-17T00:00:00Z",
                "BILLING_SHADOW_MODE": "false",
                "BILLING_PRICING_VERSION": "pilot-v1",
                "BILLING_TOKEN_UNIT_SIZE": "4",
                "BILLING_PILOT_REQUIREMENTS_LIMIT": "200",
                "BILLING_PILOT_TEST_CASE_LIMIT": "200",
                "BILLING_ADMIN_EMAILS": "ops@example.com,finance@example.com",
                "BILLING_MAX_OVERDRAFT_UNITS": "8",
            },
            clear=False,
        ):
            get_billing_settings.cache_clear()
            settings = get_billing_settings()

        self.assertEqual(settings.contact_email, "billing@example.com")
        self.assertEqual(settings.pricing_version, "pilot-v1")
        self.assertEqual(settings.token_unit_size, 4)
        self.assertEqual(settings.pilot_requirements_limit, 200)
        self.assertEqual(settings.pilot_test_cases_limit, 200)
        self.assertFalse(settings.shadow_mode)
        self.assertEqual(settings.admin_emails, ["ops@example.com", "finance@example.com"])
        self.assertEqual(settings.max_overdraft_units, 8)
        self.assertIsNotNone(settings.launch_date)
        self.assertEqual(settings.launch_date.isoformat(), "2026-04-17T00:00:00+00:00")

    def test_get_jira_settings_falls_back_to_jwt_secret_and_parses_limits(self) -> None:
        with patch.dict(
            os.environ,
            {
                "JWT_SECRET_KEY": "jwt-secret-key",
                "AUTH_TOKEN_MODE": AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT,
                "JIRA_CONNECTION_SECRET_KEY": "",
                "JIRA_CONNECTION_PREVIOUS_SECRET_KEYS": "old-jira-1, old-jira-2, old-jira-1",
                "JIRA_API_TIMEOUT_SECONDS": "18",
                "JIRA_PROJECT_PAGE_SIZE": "25",
                "JIRA_ISSUE_PAGE_SIZE": "40",
            },
            clear=False,
        ):
            get_auth_settings.cache_clear()
            get_jira_settings.cache_clear()
            settings = get_jira_settings()

        self.assertEqual(settings.connection_secret_key, "jwt-secret-key")
        self.assertEqual(settings.previous_connection_secret_keys, ["old-jira-1", "old-jira-2"])
        self.assertEqual(settings.api_timeout_seconds, 18)
        self.assertEqual(settings.project_page_size, 25)
        self.assertEqual(settings.issue_page_size, 40)

    def test_get_azure_devops_settings_parses_previous_keys_and_limits(self) -> None:
        with patch.dict(
            os.environ,
            {
                "JWT_SECRET_KEY": "jwt-secret-key",
                "AUTH_TOKEN_MODE": AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT,
                "AZURE_DEVOPS_CONNECTION_SECRET_KEY": "azure-primary",
                "AZURE_DEVOPS_CONNECTION_PREVIOUS_SECRET_KEYS": "old-azure-1, old-azure-2",
                "AZURE_DEVOPS_API_TIMEOUT_SECONDS": "19",
                "AZURE_DEVOPS_API_VERSION": "7.2-preview",
                "AZURE_DEVOPS_PROJECT_PAGE_SIZE": "30",
                "AZURE_DEVOPS_WORK_ITEM_PAGE_SIZE": "45",
            },
            clear=False,
        ):
            get_auth_settings.cache_clear()
            get_azure_devops_settings.cache_clear()
            settings = get_azure_devops_settings()

        self.assertEqual(settings.connection_secret_key, "azure-primary")
        self.assertEqual(settings.previous_connection_secret_keys, ["old-azure-1", "old-azure-2"])
        self.assertEqual(settings.api_timeout_seconds, 19)
        self.assertEqual(settings.api_version, "7.2-preview")
        self.assertEqual(settings.project_page_size, 30)
        self.assertEqual(settings.work_item_page_size, 45)


if __name__ == "__main__":
    unittest.main()
