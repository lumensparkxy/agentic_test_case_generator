import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.main import app
from scripts.generate_frontend_api_types import SELECTED_OPERATIONS, collect_component_names, generate_outputs


class FrontendApiContractGenerationTests(unittest.TestCase):
    def test_generates_selected_high_traffic_operation_types(self) -> None:
        declarations, runtime = generate_outputs(app.openapi())

        for selected in SELECTED_OPERATIONS:
            with self.subTest(operation=selected.key):
                self.assertIn(f"\t{selected.key}: ApiOperation<", declarations)
                self.assertIn(f"{selected.key}: Object.freeze", runtime)
                self.assertIn(selected.path, runtime)

        self.assertIn("export type RequirementsParseResponse = RequirementsWorkflowResponse;", declarations)
        self.assertIn("export type TestCasesGenerateRequest = GenerateTestCasesInput;", declarations)
        self.assertIn("export type ExportCsvResponse = Blob;", declarations)
        self.assertIn("export type BillingEntitlementsMeResponse = BillingEntitlementResponse;", declarations)
        self.assertIn("export type WorkspaceSummaryGetResponse = WorkspaceSummaryResponse;", declarations)
        self.assertIn("include_archived?: boolean;", declarations)
        self.assertIn("projects_limit?: number;", declarations)
        self.assertIn("work_items_limit?: number;", declarations)
        self.assertIn('workspaceSummary: Object.freeze({ method: "GET", path: "/workspace/summary" })', runtime)

    def test_collects_referenced_component_schemas_recursively(self) -> None:
        openapi = app.openapi()
        component_names = collect_component_names(
            openapi,
            {
                "GenerateTestCasesInput",
                "GenerateTestCasesResponse",
                "BillingEntitlementResponse",
                "WorkspaceSummaryResponse",
            },
        )

        self.assertIn("Requirement", component_names)
        self.assertIn("TestCase", component_names)
        self.assertIn("BillingQuotaSummary", component_names)
        self.assertIn("WorkspaceProjectSummary", component_names)
        self.assertIn("WorkspaceWorkItem", component_names)
        self.assertIn("WorkspaceRunSummary", component_names)
        self.assertIn("WorkspaceReportSummary", component_names)


if __name__ == "__main__":
    unittest.main()
