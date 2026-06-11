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

from app.adk_client import DEFAULT_MODEL as DEFAULT_ADK_MODEL
from app.config import DEFAULT_MODEL_NAME, _SuppressNonTextPartsWarning, _load_environment_file, get_billing_settings, get_settings


class ConfigSettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()
        get_billing_settings.cache_clear()

    def test_default_model_name_is_current_gemini_default(self) -> None:
        self.assertEqual(DEFAULT_MODEL_NAME, "gemini-3.5-flash")
        self.assertEqual(DEFAULT_ADK_MODEL, DEFAULT_MODEL_NAME)

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

    def test_get_settings_prefers_google_key_and_removes_gemini_alias(self) -> None:
        with patch.dict(
            os.environ,
            {"GOOGLE_API_KEY": "google-key", "GEMINI_API_KEY": "gemini-key", "MODEL_NAME": DEFAULT_MODEL_NAME},
            clear=False,
        ):
            get_settings.cache_clear()
            with patch("app.config._warn_if_dependency_mismatch"), self.assertLogs(level="WARNING") as logs:
                settings = get_settings()

            self.assertEqual(settings.gemini_api_key, "google-key")
            self.assertEqual(os.environ.get("GOOGLE_API_KEY"), "google-key")
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


if __name__ == "__main__":
    unittest.main()
