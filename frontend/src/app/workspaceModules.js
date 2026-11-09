import { deferWorkspace } from "./deferWorkspace";
import { PROJECT_DESTINATIONS } from "./workflowRoutes";

export const ProjectsPage = deferWorkspace(() => import("../pages/ProjectsPage"), "Projects", { page: true });
export const ReviewsPage = deferWorkspace(() => import("../pages/ReviewsPage"), "Review Inbox", { page: true });
export const RunsPage = deferWorkspace(() => import("../pages/RunsPage"), "Runs", { page: true });
export const ReportsPage = deferWorkspace(() => import("../pages/ReportsPage"), "Reports", { page: true });
export const UseCaseReviewPage = deferWorkspace(() => import("../pages/UseCaseReviewPage"), "Use Cases", { page: true });

export const RequirementImportReview = deferWorkspace(() => import("../components/requirements/RequirementImportReview"), "import review");
export const RequirementReviewWorkbench = deferWorkspace(
	() => import("../components/requirements/RequirementReviewWorkbench"),
	"requirements review"
);
export const ContextInputsPanel = deferWorkspace(() => import("../components/context/ContextInputsPanel"), "Context");
export const TemplateSetupPanel = deferWorkspace(() => import("../components/template/TemplateSetupPanel"), "template setup");
export const GeneratedTestCasesView = deferWorkspace(
	() => import("../components/generation/GeneratedTestCasesView"),
	"generated test cases"
);
export const LinkedTestCaseView = deferWorkspace(() => import("../components/generation/LinkedTestCaseView"), "saved test case");
export const ImproveTestsPanel = deferWorkspace(() => import("../components/generation/ImproveTestsPanel"), "Improve tests");
export const TraceabilityMatrixPanel = deferWorkspace(
	() => import("../components/generation/TraceabilityMatrixPanel"),
	"traceability matrix"
);
export const AutomationPanel = deferWorkspace(() => import("../components/automation/AutomationPanel"), "Automation");
export const ExportPanel = deferWorkspace(() => import("../components/export/ExportPanel"), "project Reports");
export const OrchestratorCockpitPanel = deferWorkspace(
	() => import("../components/projects/OrchestratorCockpitPanel"),
	"project recommendations"
);

export const projectRouteModules = new Map([
	[PROJECT_DESTINATIONS.CONTEXT, ContextInputsPanel],
	[PROJECT_DESTINATIONS.USE_CASES, UseCaseReviewPage],
	[PROJECT_DESTINATIONS.AUTOMATION, AutomationPanel],
	[PROJECT_DESTINATIONS.REPORTS, ExportPanel],
]);
