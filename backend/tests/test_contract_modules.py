import unittest
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import contracts, models
from app.contracts import (
    auth,
    automation,
    billing,
    execution,
    exports,
    grounding,
    impact,
    integrations,
    orchestrator,
    projects,
    reporting,
    requirements,
    test_cases,
)


class ContractModuleTests(unittest.TestCase):
    def test_models_facade_reexports_domain_contracts(self):
        representative_contracts = {
            "Requirement": requirements.Requirement,
            "GroundedContext": grounding.GroundedContext,
            "AuthUser": auth.AuthUser,
            "TestCase": test_cases.TestCase,
            "ExecutionPreviewResponse": execution.ExecutionPreviewResponse,
            "ImpactAnalysisResult": impact.ImpactAnalysisResult,
            "QaProjectDetail": projects.QaProjectDetail,
            "OrchestratorStatusResponse": orchestrator.OrchestratorStatusResponse,
            "JiraImportInput": integrations.JiraImportInput,
            "AzureDevOpsImportInput": integrations.AzureDevOpsImportInput,
            "ExportTestCasesInput": exports.ExportTestCasesInput,
            "AutomationResponse": automation.AutomationResponse,
            "UsageReportResponse": reporting.UsageReportResponse,
            "BillingEntitlementResponse": billing.BillingEntitlementResponse,
        }

        for name, domain_contract in representative_contracts.items():
            with self.subTest(name=name):
                self.assertIs(getattr(models, name), domain_contract)

    def test_models_facade_exports_match_contract_package(self):
        self.assertEqual(models.__all__, contracts.__all__)
        self.assertGreater(len(models.__all__), 100)
        for name in models.__all__:
            with self.subTest(name=name):
                self.assertIs(getattr(models, name), getattr(contracts, name))


if __name__ == "__main__":
    unittest.main()
