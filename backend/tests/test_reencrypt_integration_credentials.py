import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import scripts.reencrypt_integration_credentials as reencrypt_cli


class ReencryptIntegrationCredentialsCliTests(unittest.TestCase):
    def test_defaults_to_dry_run_for_all_providers(self) -> None:
        with patch.object(sys, "argv", ["reencrypt_integration_credentials.py"]):
            with patch.object(
                reencrypt_cli,
                "reencrypt_jira_connection_credentials",
                return_value={"provider": "jira", "failed": 0},
            ) as jira:
                with patch.object(
                    reencrypt_cli,
                    "reencrypt_azure_devops_connection_credentials",
                    return_value={"provider": "azure_devops", "failed": 0},
                ) as azure:
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        exit_code = reencrypt_cli.main()

        self.assertEqual(exit_code, 0)
        jira.assert_called_once_with(dry_run=True)
        azure.assert_called_once_with(dry_run=True)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertEqual([item["provider"] for item in payload["results"]], ["jira", "azure_devops"])

    def test_apply_one_provider_returns_failure_when_records_fail(self) -> None:
        with patch.object(sys, "argv", ["reencrypt_integration_credentials.py", "--provider", "jira", "--apply"]):
            with patch.object(
                reencrypt_cli,
                "reencrypt_jira_connection_credentials",
                return_value={"provider": "jira", "failed": 1},
            ) as jira:
                with patch.object(reencrypt_cli, "reencrypt_azure_devops_connection_credentials") as azure:
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        exit_code = reencrypt_cli.main()

        self.assertEqual(exit_code, 1)
        jira.assert_called_once_with(dry_run=False)
        azure.assert_not_called()
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["dry_run"])
        self.assertEqual(payload["results"], [{"provider": "jira", "failed": 1}])


if __name__ == "__main__":
    unittest.main()
