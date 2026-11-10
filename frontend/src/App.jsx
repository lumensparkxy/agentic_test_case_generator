import ClarificationScopeNotice from "./components/context/ClarificationScopeNotice";
import TestingIntentPanel from "./components/context/TestingIntentPanel";
import RouteLink from "./app/RouteLink";
import { hasApprovedGenerationScope } from "./utils/requirementScope";
import useReviewDraft from "./hooks/useReviewDraft";
import useExecutionRecovery from "./hooks/useExecutionRecovery";
import DraftRecoveryNotice from "./components/reviews/DraftRecoveryNotice";
import ApprovalContinuationPanel from "./components/reviews/ApprovalContinuationPanel";
import useApprovalContinuation from "./hooks/useApprovalContinuation";
import useScopedScenarioRefresh from "./hooks/useScopedScenarioRefresh";
import useImpactApplication from "./hooks/useImpactApplication";
import ImpactApplicationBar from "./components/generation/ImpactApplicationBar";
import ScopedImpactStatus from "./components/generation/ScopedImpactStatus";
import useRequirementImports from "./hooks/useRequirementImports";
import useRequirementExtractionStatus from "./hooks/useRequirementExtractionStatus";
import RequirementExtractionStatus from "./components/requirements/RequirementExtractionStatus";
import useGuidanceRecovery from "./hooks/useGuidanceRecovery";
import GuidanceUsed from "./components/knowledge/GuidanceUsed";
import GenerationGuidance from "./components/knowledge/GenerationGuidance";
import KnowledgeSuggestion from "./components/knowledge/KnowledgeSuggestion";
import { Alert, Badge, Disclosure, Surface } from "./components/ui/surfaces";
import { Button, Link, Radio, Checkbox, Field, Input, Select, Textarea } from "./components/ui/controls";
import { TabList, Tab, TabPanel } from "./components/ui/tabs";
import { TableScroll, Table, List, ListItem, CollectionState } from "./components/ui/collections";
import ProjectPageHeader from "./components/layout/ProjectPageHeader";
import ProjectWorkspaceNavigation from "./components/layout/ProjectWorkspaceNavigation";
import { improvementFindings } from "./components/generation/improvementFindings";
import ReviewGroupSummary from "./components/generation/ReviewGroupSummary";
import { SavedRepairEvidence, SelectedCaseRepair, SelectedCaseRepairStatus } from "./components/generation/SelectedCaseRepair";
import useSelectedCaseRepair from "./hooks/useSelectedCaseRepair";
import SuiteQualitySummary, { findExactSuiteRun } from "./components/generation/SuiteQualitySummary";
import { findActiveSavedCase, prioritySavedCases } from "./components/generation/prioritySavedCases";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { browserPopupRedirectResolver, onAuthStateChanged, signInWithPopup, signOut } from "firebase/auth";
import GlobalAppShell from "./app/GlobalAppShell";
import { GlobalDestinationPlaceholderPage, ProjectLoadingPage, ProjectOverviewPage, RouteRecoveryPage } from "./app/RoutePages";
import {
	AutomationPanel,
	ContextInputsPanel,
	ExportPanel,
	GeneratedTestCasesView,
	ImproveTestsPanel,
	LinkedTestCaseView,
	OrchestratorCockpitPanel,
	projectRouteModules,
	ProjectsPage,
	ReportsPage,
	RequirementImportReview,
	RequirementReviewWorkbench,
	ReviewsPage,
	RunsPage,
	TemplateSetupPanel,
	TraceabilityMatrixPanel,
	UseCaseReviewPage,
} from "./app/workspaceModules";
import { readSavedCaseLink, readSavedCaseStep } from "./app/savedCaseLinks";
import { readSavedFileReportLink } from "./app/savedFileReportLinks";
import { getCurrentDeclaredBrowserSetup } from "./app/declaredBrowserSetup";
import { readExecutionFindingsLink } from "./app/executionFindings";
import {
	createFirebaseAuthProvider,
	firebaseAuth,
	firebaseAuthHandlerUrl,
	hasFirebaseAuthConfig,
	visibleFirebaseAuthProviders,
} from "./firebase";
import AppNavigationControls, { SignInDialog } from "./components/layout/AppHeader";
import TestCaseReadiness from "./components/projects/TestCaseReadiness";
import { formatExecutionRunSummary, getAutomationEvidenceStatus } from "./components/automation/automationEvidence";
import {
	getDefaultSelectedCandidateIds,
	getExecutionEligibility,
	normalizeAutomationPreview,
	resolveSelectedExecutableCandidates,
} from "./components/automation/automationPreview";
import ProjectSyncPanel, { ProjectSyncStatus } from "./components/context/ProjectSyncPanel";
import BillingBanner from "./components/layout/BillingBanner";
import { selectContextualTask } from "./components/projects/contextualTask";
import SettingsDialog from "./components/settings/SettingsDialog";
import WorkflowNavigationDrawer from "./components/layout/WorkflowNavigationDrawer";
import useAppSessionState from "./hooks/useAppSessionState";
import useBillingStatus from "./hooks/useBillingStatus";
import useBrowserNavigation from "./hooks/useBrowserNavigation";
import useContextWorkflowState from "./hooks/useContextWorkflowState";
import useExecutionWorkflowState from "./hooks/useExecutionWorkflowState";
import useExportWorkflowState from "./hooks/useExportWorkflowState";
import useIntegrationWorkflowState from "./hooks/useIntegrationWorkflowState";
import useProjectWorkspaceState from "./hooks/useProjectWorkspaceState";
import useRequirementWorkflowState from "./hooks/useRequirementWorkflowState";
import useTestCaseWorkflowState from "./hooks/useTestCaseWorkflowState";
import useWorkflowNavigationState from "./hooks/useWorkflowNavigationState";
import useWorkflowShellLayoutState from "./hooks/useWorkflowShellLayoutState";
import useWorkspaceSummary from "./hooks/useWorkspaceSummary";
import HomePage from "./pages/HomePage";
import {
	GLOBAL_DESTINATIONS,
	PROJECT_DESTINATIONS,
	PROJECT_WORKSPACES,
	buildProjectPath,
	buildProjectWorkspacePath,
	getDestinationForLegacyTab,
	getAdjacentProjectDestination,
	getLegacyTabForDestination,
	parseWorkflowRoute,
	resolveOrchestratorDestination,
} from "./app/workflowRoutes";
import {
	AUTH_REQUIRED_MESSAGE,
	DEFAULT_AZURE_DEVOPS_SYNC_SECTION_TITLE,
	DEFAULT_AZURE_DEVOPS_WORK_ITEM_TYPE_OPTIONS,
	DEFAULT_JIRA_ISSUE_TYPE_OPTIONS,
	DEFAULT_JIRA_SYNC_SECTION_TITLE,
	EMPTY_AZURE_DEVOPS_CONNECTION_FORM,
	EMPTY_AZURE_DEVOPS_CONNECTION_STATUS,
	EMPTY_JIRA_CONNECTION_FORM,
	EMPTY_JIRA_CONNECTION_STATUS,
	REQUIREMENT_SOURCE_OPTIONS,
	STORAGE_AUTH_TOKEN,
	STORAGE_AUTH_USER,
	STORAGE_CURRENT_PROJECT_ID,
} from "./constants/workflow";
import { buildTestCaseExportFilename } from "./services/exportFilename";
import { API_BASE, createRequestId, downloadBlob, ensureRequestIdHeader, parseApiError } from "./services/apiClient";
import { API_CONTRACT_ENDPOINTS } from "./api/generated/api-contracts";
import {
	buildAzureDevOpsConnectionForm,
	buildJiraConnectionForm,
	buildRequirementTraceability,
	getRequirementReviewStatus,
	isAzureDevOpsLinkedRequirement,
	isJiraLinkedRequirement,
	mergeRequirementMetadata,
	normalizeStringArray,
} from "./utils/requirements";
import { buildEmptyUsageSummary, getCurrentUserUsageSummary } from "./utils/usage";
import { buildWorkflowSettingsPayload, getReviewScoreMeta } from "./utils/workflow";
import "./styles/index.css";

const PROJECT_WORKSPACES_V2 = import.meta.env.VITE_PROJECT_WORKSPACES_V2 === "true";
const DRAFT_ADMISSION_ENABLED = import.meta.env.VITE_APPROVAL_CONTINUATION_ENABLED === "true";
const APPROVAL_CONTINUATION_ENABLED = PROJECT_WORKSPACES_V2 && DRAFT_ADMISSION_ENABLED;
const REQUIREMENT_INTAKE_PATHS = Object.freeze({
	"requirements.parse": API_CONTRACT_ENDPOINTS.requirementsParse.path,
	"requirements.refine": API_CONTRACT_ENDPOINTS.requirementsParse.path,
	"requirements.import.jira": API_CONTRACT_ENDPOINTS.jiraImport.path,
	"requirements.import.azure_devops": API_CONTRACT_ENDPOINTS.azureDevOpsImport.path,
});
const REQUIREMENT_PREVIEW_UNCONFIRMED =
	"Could not confirm the requirements preview. Check extraction status and review any saved import before retrying.";

const EXPORT_API_PATHS = Object.freeze({
	csv: API_CONTRACT_ENDPOINTS.exportCsv.path,
	excel: API_CONTRACT_ENDPOINTS.exportExcel.path,
	json: API_CONTRACT_ENDPOINTS.exportJson.path,
});

const PROJECT_API_PATHS = Object.freeze({
	projects: "/projects",
	project: (projectId) => `/projects/${encodeURIComponent(projectId)}`,
	orchestratorStatus: (projectId) =>
		API_CONTRACT_ENDPOINTS.projectOrchestratorStatus.path.replace("{project_id}", encodeURIComponent(projectId)),
	impactAnalysis: (projectId) => `/projects/${projectId}/impact-analysis`,
	savedSuiteReview: (projectId) =>
		API_CONTRACT_ENDPOINTS.projectTestCasesReview.path.replace("{project_id}", encodeURIComponent(projectId)),
	impactUpdateApply: (projectId) => `/projects/${projectId}/impact-update/apply`,
});

function savedReviewStatusMessage(review, sharedDraftBusy) {
	if (review.busy || (!review.statusRead && !review.statusError)) return "Checking saved review status…";
	if (review.attempt?.state === "interrupted")
		return "The saved review was interrupted. The provider may already have received the request. Acknowledge and release the attempt before starting another review.";
	if (review.attempt?.state === "running")
		return "A saved review is running. Already issued provider requests may finish after cancellation. A cancelled attempt cannot publish a result.";
	if (sharedDraftBusy) return "Another draft operation is active for this project.";
	if (review.error || review.statusError) return "Check status before starting a saved review.";
	return "Review saved suite resumes assessment of these saved cases and reuses compatible completed review groups.";
}

const createCancelledOperationError = () => {
	const error = new Error("Project creation was cancelled because the workspace changed.");
	error.name = "AbortError";
	return error;
};

const getAuthProviderLabel = (providerKeyOrId) => {
	const provider = visibleFirebaseAuthProviders.find(({ id, providerId }) => providerKeyOrId === id || providerKeyOrId === providerId);
	return provider?.label || providerKeyOrId || null;
};

const buildProviderSignInErrorMessage = (providerConfig, error) => {
	const providerLabel = providerConfig?.label || "Provider";
	const handlerUrl = firebaseAuthHandlerUrl || "https://<your-project>.firebaseapp.com/__/auth/handler";
	const rawMessage = `${error?.message || "Unknown error"}`.trim();
	const errorCode = `${error?.code || ""}`.trim();

	if (providerConfig?.id === "apple" && errorCode === "auth/operation-not-allowed") {
		return `${providerLabel} sign-in is not enabled for the Firebase project used by this frontend. In Firebase Console → Authentication → Sign-in method → Apple, enable Apple and complete the Service ID, Team ID, Key ID, and private key setup. Apple Return URL: ${handlerUrl}`;
	}

	if (providerConfig?.id === "microsoft" && /redirect_uri|invalid_request/i.test(rawMessage)) {
		return `${providerLabel} sign-in is missing the Firebase auth callback URL in the Microsoft Entra app registration. Add this Web redirect URI to the Azure/Microsoft app used by Firebase: ${handlerUrl}. Then verify the same Client ID and Client Secret are saved in Firebase Console → Authentication → Sign-in method → Microsoft.`;
	}

	if (providerConfig?.id === "microsoft" && /AADSTS7000215|invalid_client|client secret/i.test(rawMessage)) {
		return `${providerLabel} sign-in is failing because the Microsoft Entra client secret saved in Firebase is invalid. In Firebase Console → Authentication → Sign-in method → Microsoft, re-enter the Microsoft app registration secret VALUE (not the secret ID), or generate a new client secret in Microsoft Entra and save that value in Firebase. The redirect URI should remain ${handlerUrl}.`;
	}

	if (errorCode === "auth/operation-not-allowed") {
		return `${providerLabel} sign-in is not enabled for the Firebase project used by this app. Enable it in Firebase Console → Authentication → Sign-in method.`;
	}

	if (errorCode === "auth/unauthorized-domain") {
		return `This hostname is not authorized for Firebase Authentication. Add ${window.location.hostname} to Firebase Console → Authentication → Settings → Authorized domains.`;
	}

	return `Sign-in with ${providerLabel} failed: ${rawMessage}`;
};

const hasMetricValue = (metrics, key) => Boolean(metrics && Object.prototype.hasOwnProperty.call(metrics, key) && metrics[key] != null);

const formatWorkflowStatusLabel = (status) => {
	const normalized = `${status || ""}`.trim();
	if (!normalized) {
		return "";
	}

	return normalized
		.split(/[_\s-]+/)
		.filter(Boolean)
		.map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
		.join(" ");
};

const getRequirementSourceMetricMeta = (coverageMetrics) => {
	if (hasMetricValue(coverageMetrics, "source_work_item_count")) {
		return {
			countLabel: "Source work items",
			countValue: coverageMetrics.source_work_item_count,
			perLabel: "Per work item",
		};
	}

	if (hasMetricValue(coverageMetrics, "source_issue_count")) {
		return {
			countLabel: "Source issues",
			countValue: coverageMetrics.source_issue_count,
			perLabel: "Per issue",
		};
	}

	if (hasMetricValue(coverageMetrics, "document_count")) {
		return {
			countLabel: "Source docs",
			countValue: coverageMetrics.document_count,
			perLabel: "Per doc",
		};
	}

	return {
		countLabel: null,
		countValue: null,
		perLabel: "Per source",
	};
};

const normalizeImpactText = (value) => `${value || ""}`.trim().replace(/\s+/g, " ").toLowerCase();

const contextFormValues = (payload) => ({
	appLink: payload?.app_link || "",
	prototypeLink: payload?.prototype_link || "",
	diagramLinks: (payload?.diagram_links || []).join("; "),
	imageLinks: (payload?.image_links || []).join("; "),
	contextNotes: payload?.notes ?? "",
	selectedArtifactSourceIds: (payload?.grounded_context?.artifact_sources || []).map(({ id }) => id),
});

const contextSourceInputs = (context) => {
	const normalizeLink = (link) => {
		try {
			return new URL(link).href;
		} catch {
			return link || null;
		}
	};
	return JSON.stringify([
		normalizeLink(context.app_link),
		normalizeLink(context.prototype_link),
		(context.diagram_links || []).map(normalizeLink),
		(context.image_links || []).map(normalizeLink),
		context.notes || null,
	]);
};

const estimateChangedRequirementCount = (snapshots) => {
	const requirements = snapshots?.requirements?.payload?.requirements || [];
	const coveragePlan = snapshots?.test_cases?.payload?.coverage_plan || [];
	if (!requirements.length || !coveragePlan.length) {
		return null;
	}
	const baselineByRequirementId = new Map(
		coveragePlan
			.map((plan) => [`${plan.requirement_id || ""}`.trim(), normalizeImpactText(plan.requirement_text)])
			.filter(([requirementId]) => requirementId)
	);
	const currentIds = new Set();
	let changedCount = 0;
	for (const requirement of requirements) {
		const requirementId = `${requirement.id || ""}`.trim();
		if (!requirementId) {
			continue;
		}
		currentIds.add(requirementId);
		const baselineText = baselineByRequirementId.get(requirementId);
		if (baselineText == null || baselineText !== normalizeImpactText(requirement.text)) {
			changedCount += 1;
		}
	}
	for (const requirementId of baselineByRequirementId.keys()) {
		if (!currentIds.has(requirementId)) {
			changedCount += 1;
		}
	}
	return changedCount;
};

function focusWorkspaceEntry(main) {
	const path = window.location.pathname;
	const heading = main.querySelector("h1");
	const revealHeading = () => {
		const { top, bottom } = (heading || main).getBoundingClientRect();
		if (top >= 0 && top < window.innerHeight && (!heading || bottom <= window.innerHeight)) return;
		main.scrollIntoView({ block: "start", behavior: "instant" });
	};

	main.focus({ preventScroll: true });
	revealHeading();
	// An in-flight keyboard scroll can finish after the route changes.
	const entry = new AbortController();
	const stop = () => entry.abort();
	const options = { signal: entry.signal };
	document.addEventListener(
		"scrollend",
		() => {
			if (window.location.pathname !== path || document.activeElement !== main) stop();
			else revealHeading();
		},
		options
	);
	main.addEventListener("blur", stop, options);
	for (const event of ["keydown", "pointerdown", "wheel", "popstate"]) {
		window.addEventListener(event, stop, { ...options, capture: true, passive: true });
	}
}

export default function App() {
	const [isSavingRequirementReview, setIsSavingRequirementReview] = useState(false);
	const requirementReviewLock = useRef(false);
	const requirementReviewAttempt = useRef(null);
	const { activeTab, setActiveTab } = useWorkflowNavigationState();
	const { route, location, navigate } = useBrowserNavigation();
	const [restoredTraceabilityLocation, setRestoredTraceabilityLocation] = useState(null);
	const savedCaseLink =
		route.kind === "project" && route.destination === PROJECT_DESTINATIONS.TEST_CASES ? readSavedCaseLink(location.search) : null;
	const viewingSavedCase = Boolean(savedCaseLink);
	const savedFileReportLink =
		route.kind === "project" && route.destination === PROJECT_DESTINATIONS.REPORTS ? readSavedFileReportLink(location.search) : null;
	const executionFindingsLink =
		route.kind === "project" && route.destination === PROJECT_DESTINATIONS.CONTEXT ? readExecutionFindingsLink(location.search) : null;
	const contextQuery = route.destination === PROJECT_DESTINATIONS.CONTEXT ? new URLSearchParams(location.search) : null;
	const prerequisiteSearch =
		!executionFindingsLink && (contextQuery?.has("prerequisite") || contextQuery?.has("setup")) ? location.search : "";
	const projectRouteRequestRef = useRef(0);
	const projectCreateRequestRef = useRef(0);
	const projectListRequestRef = useRef(0);
	const orchestratorRequestRef = useRef(0);
	const projectRevisionsRef = useRef({ identity: "", projects: new Map() });
	const projectOperationScopeRef = useRef({ projectId: "", userId: "", generation: 0 });
	const projectOperationUserRef = useRef("");
	const workflowMainRef = useRef(null);
	const requirementImportControl = useRef(null);
	const requirementExtractionAttempts = useRef(new Map());
	const contextAnalysisAttempt = useRef(null);
	const testingIntentSave = useRef(null);
	const fileExportAttempts = useRef(new Map());
	const requirementReviewHeading = useRef(null);
	const reviewScopeAction = useRef(null);
	const [requirementSetup, setRequirementSetup] = useState(null);
	const [requirementFocus, setRequirementFocus] = useState(null);
	const [requirementReviewRequest, setRequirementReviewRequest] = useState(null);
	const [projectRouteStatus, setProjectRouteStatus] = useState("idle");
	const [projectRouteError, setProjectRouteError] = useState("");
	const [applicationLiveStatus, setApplicationLiveStatus] = useState("");
	const [authActionError, setAuthActionError] = useState("");
	const { isWorkflowNavCollapsed, isCompactWorkflowNavigation, toggleWorkflowNavCollapsed, closeCompactWorkflowNavigation } =
		useWorkflowShellLayoutState();
	const {
		file,
		setFile,
		rawText,
		setRawText,
		requirements,
		setRequirements,
		requirementReview,
		setRequirementReview,
		requirementCoverageMetrics,
		setRequirementCoverageMetrics,
		requirementWorkflowDiagnostics,
		setRequirementWorkflowDiagnostics,
		appliedRequirementWorkflowSettings,
		setAppliedRequirementWorkflowSettings,
		requirementIterationHistory,
		setRequirementIterationHistory,
		reqFeedback,
		setReqFeedback,
		requirementWorkflowSettings,
		setRequirementWorkflowSettings,
		requirementSourceMode,
		setRequirementSourceMode,
		isParsing,
		setIsParsing,
	} = useRequirementWorkflowState();
	const {
		appLink,
		setAppLink,
		prototypeLink,
		setPrototypeLink,
		diagramLinks,
		setDiagramLinks,
		imageLinks,
		setImageLinks,
		contextNotes,
		setContextNotes,
		enrichedContext,
		setEnrichedContext,
		selectedArtifactSourceIds,
		setSelectedArtifactSourceIds,
		isAnalyzingContext,
		setIsAnalyzingContext,
		contextAnalysisError,
		setContextAnalysisError,
		resetContextAnalysis,
	} = useContextWorkflowState();
	const {
		templateName,
		setTemplateName,
		templateFormat,
		setTemplateFormat,
		testCases,
		setTestCases,
		generationTasks,
		setGenerationTasks,
		requirementAnalysis,
		setRequirementAnalysis,
		coveragePlan,
		setCoveragePlan,
		coverageMetrics,
		setCoverageMetrics,
		substantiveAssessment,
		setSubstantiveAssessment,
		testCaseReview,
		setTestCaseReview,
		testCaseWorkflowDiagnostics,
		setTestCaseWorkflowDiagnostics,
		appliedTestCaseWorkflowSettings,
		setAppliedTestCaseWorkflowSettings,
		setTestCaseIterationHistory,
		feedback,
		setFeedback,
		testCaseWorkflowSettings,
		setTestCaseWorkflowSettings,
		activeGenerateResultTab: selectedGenerateResultTab,
		setActiveGenerateResultTab,
		isGenerating,
		setIsGenerating,
		resetTestCaseWorkflowState,
	} = useTestCaseWorkflowState();
	const generationTemplate = useMemo(
		() => ({
			name: templateName,
			format: templateFormat,
			fields: [
				"id",
				"title",
				"description",
				"priority",
				"type",
				"status",
				"preconditions",
				"steps",
				"expected_result",
				"test_data",
				"estimated_time",
				"automation_status",
				"component",
				"linked_requirement_ids",
				"scenario_refs",
				"source_refs",
				"tags",
			],
		}),
		[templateName, templateFormat]
	);
	const [impactAnalysis, setImpactAnalysis] = useState(null);
	const [isAnalyzingImpact, setIsAnalyzingImpact] = useState(false);
	const [savedSuiteReview, setSavedSuiteReview] = useState(null);
	const savedSuiteReviewRequests = useRef(new Set());
	const [changedTestCaseIds, setChangedTestCaseIds] = useState(null);
	const [exportMessage, setExportMessage] = useState("");
	const [exportDraftRequiredFor, setExportDraftRequiredFor] = useState(null);
	const [exportError, setExportError] = useState("");
	const {
		executionTargetBaseUrl,
		setExecutionTargetBaseUrl,
		executionTargetEnvironment,
		setExecutionTargetEnvironment,
		executionPreview,
		setExecutionPreview,
		selectedExecutionCandidateIds,
		setSelectedExecutionCandidateIds,
		executionRunResult,
		setExecutionRunResult,
		executionError,
		setExecutionError,
		isPreviewingExecution,
		setIsPreviewingExecution,
		isRunningExecution,
		setIsRunningExecution,
		resetExecutionWorkflowState: resetExecutionState,
	} = useExecutionWorkflowState();
	const executionPreviewRequestIdRef = useRef(0);
	const executionPreviewAttempt = useRef(null);
	const executionRunRequestIdRef = useRef(0);
	const executingScopeRef = useRef(null);
	const invalidateExecutionPreviewRequest = () => {
		executionPreviewRequestIdRef.current += 1;
		setIsPreviewingExecution(false);
	};
	const invalidateExecutionRunRequest = () => {
		executionRunRequestIdRef.current += 1;
		setIsRunningExecution(false);
	};
	const resetExecutionWorkflowState = () => {
		invalidateExecutionPreviewRequest();
		invalidateExecutionRunRequest();
		resetExecutionState();
		setApplicationLiveStatus("");
	};
	const {
		isExporting,
		setIsExporting,
		draftExportOverrideRequested,
		setDraftExportOverrideRequested,
		draftExportOverrideReason,
		setDraftExportOverrideReason,
		resetExportWorkflowState,
	} = useExportWorkflowState();
	const {
		projects,
		setProjects,
		currentProject,
		setCurrentProject,
		newProjectName,
		setNewProjectName,
		isLoadingProjects,
		setIsLoadingProjects,
		isCreatingProject,
		setIsCreatingProject,
		isOpeningProject,
		setIsOpeningProject,
		orchestratorStatus,
		setOrchestratorStatus,
		isLoadingOrchestrator,
		setIsLoadingOrchestrator,
		orchestratorError,
		setOrchestratorError,
	} = useProjectWorkspaceState();
	const {
		setStatus,
		authToken,
		setAuthToken,
		currentUser,
		setCurrentUser,
		isAuthenticating,
		setIsAuthenticating,
		activeAuthProvider,
		setActiveAuthProvider,
		isSignInDialogOpen,
		setIsSignInDialogOpen,
		isSettingsDialogOpen,
		setIsSettingsDialogOpen,
		settingsSection,
		setSettingsSection,
		isVerifyingSession,
		setIsVerifyingSession,
		usageSummary,
		setUsageSummary,
		isUsageLoading,
		setIsUsageLoading,
		billingEntitlements,
		setBillingEntitlements,
		isBillingLoading,
		setIsBillingLoading,
	} = useAppSessionState();
	const {
		jiraConnectionStatus,
		setJiraConnectionStatus,
		jiraConnectionForm,
		setJiraConnectionForm,
		isJiraConnectionLoading,
		setIsJiraConnectionLoading,
		isSavingJiraConnection,
		setIsSavingJiraConnection,
		isDeletingJiraConnection,
		setIsDeletingJiraConnection,
		jiraProjectQuery,
		setJiraProjectQuery,
		jiraProjects,
		setJiraProjects,
		selectedJiraProjectKey,
		setSelectedJiraProjectKey,
		isLoadingJiraProjects,
		setIsLoadingJiraProjects,
		jiraProjectIssueTypes,
		setJiraProjectIssueTypes,
		isLoadingJiraIssueTypes,
		setIsLoadingJiraIssueTypes,
		jiraIssueType,
		setJiraIssueType,
		jiraIssueQuery,
		setJiraIssueQuery,
		jiraIssueResults,
		setJiraIssueResults,
		selectedJiraIssueKey,
		setSelectedJiraIssueKey,
		isSearchingJiraIssues,
		setIsSearchingJiraIssues,
		isImportingFromJira,
		setIsImportingFromJira,
		jiraSyncPreview,
		setJiraSyncPreview,
		jiraSyncResults,
		setJiraSyncResults,
		jiraManagedSectionTitle,
		setJiraManagedSectionTitle,
		isPreviewingJiraSync,
		setIsPreviewingJiraSync,
		isApplyingJiraSync,
		setIsApplyingJiraSync,
		azureDevOpsConnectionStatus,
		setAzureDevOpsConnectionStatus,
		azureDevOpsConnectionForm,
		setAzureDevOpsConnectionForm,
		isAzureDevOpsConnectionLoading,
		setIsAzureDevOpsConnectionLoading,
		isSavingAzureDevOpsConnection,
		setIsSavingAzureDevOpsConnection,
		isDeletingAzureDevOpsConnection,
		setIsDeletingAzureDevOpsConnection,
		azureDevOpsProjectQuery,
		setAzureDevOpsProjectQuery,
		azureDevOpsProjects,
		setAzureDevOpsProjects,
		selectedAzureDevOpsProject,
		setSelectedAzureDevOpsProject,
		isLoadingAzureDevOpsProjects,
		setIsLoadingAzureDevOpsProjects,
		azureDevOpsWorkItemTypes,
		setAzureDevOpsWorkItemTypes,
		isLoadingAzureDevOpsWorkItemTypes,
		setIsLoadingAzureDevOpsWorkItemTypes,
		azureDevOpsWorkItemType,
		setAzureDevOpsWorkItemType,
		azureDevOpsWorkItemQuery,
		setAzureDevOpsWorkItemQuery,
		azureDevOpsWorkItemResults,
		setAzureDevOpsWorkItemResults,
		selectedAzureDevOpsWorkItemId,
		setSelectedAzureDevOpsWorkItemId,
		isSearchingAzureDevOpsWorkItems,
		setIsSearchingAzureDevOpsWorkItems,
		isImportingFromAzureDevOps,
		setIsImportingFromAzureDevOps,
		azureDevOpsSyncPreview,
		setAzureDevOpsSyncPreview,
		azureDevOpsSyncResults,
		setAzureDevOpsSyncResults,
		azureDevOpsManagedSectionTitle,
		setAzureDevOpsManagedSectionTitle,
		isPreviewingAzureDevOpsSync,
		setIsPreviewingAzureDevOpsSync,
		isApplyingAzureDevOpsSync,
		setIsApplyingAzureDevOpsSync,
		resetJiraSyncState,
		resetAzureDevOpsSyncState,
		resetIntegrationSyncState,
	} = useIntegrationWorkflowState();

	const isAuthenticated = Boolean(authToken && currentUser);
	const hasVisibleAuthProviders = visibleFirebaseAuthProviders.length > 0;
	const authActionDisabled = !isAuthenticated || isAuthenticating || isVerifyingSession;
	const currentProjectId = currentProject?.project_id || "";
	const [executionRecovery, saveExecutionRecovery] = useExecutionRecovery(currentUser?.sub, currentProjectId);
	const executionNeedsResolution = Boolean(executionRecovery && executionRecovery.status !== "completed");
	const currentProjectRevision = Number.isInteger(currentProject?.current_revision) ? currentProject.current_revision : null;
	projectOperationUserRef.current = currentUser?.sub || "";
	if (projectRevisionsRef.current.identity !== projectOperationUserRef.current) {
		projectRevisionsRef.current = { identity: projectOperationUserRef.current, projects: new Map() };
	}
	const recordProjectRevision = (projectId, revision) => {
		if (projectId && Number.isInteger(revision)) {
			projectRevisionsRef.current.projects.set(projectId, Math.max(revision, projectRevisionsRef.current.projects.get(projectId) || 0));
		}
	};
	const isLatestProjectRevision = (projectId, revision) => revision >= (projectRevisionsRef.current.projects.get(projectId) || 0);
	recordProjectRevision(currentProjectId, currentProjectRevision);
	const syncProjectOperationScope = () => {
		const browserRoute = typeof window === "undefined" ? route : parseWorkflowRoute(window.location.pathname);
		const browserProjectId = browserRoute.kind === "project" ? browserRoute.projectId : "";
		const browserUserId = projectOperationUserRef.current;
		const currentScope = projectOperationScopeRef.current;
		if (currentScope.projectId !== browserProjectId || currentScope.userId !== browserUserId) {
			projectOperationScopeRef.current = {
				projectId: browserProjectId,
				userId: browserUserId,
				generation: currentScope.generation + 1,
			};
		}
		return projectOperationScopeRef.current;
	};
	const captureProjectOperationScope = () => {
		const operationScope = syncProjectOperationScope();
		return operationScope.projectId ? { ...operationScope } : null;
	};
	const isProjectOperationCurrent = (operationScope) => {
		if (!operationScope?.projectId) {
			return false;
		}
		const currentScope = syncProjectOperationScope();
		return (
			currentScope.projectId === operationScope.projectId &&
			currentScope.userId === operationScope.userId &&
			currentScope.generation === operationScope.generation
		);
	};
	const impactApplication = useImpactApplication({
		project: currentProject,
		enabled: route.kind === "project" && route.projectId === currentProjectId,
		identity: currentUser?.sub || "",
		apiRequest: (...args) => apiRequest(...args),
		scopedEnabled: Boolean(
			APPROVAL_CONTINUATION_ENABLED &&
			route.workspace === "review" &&
			route.section === "tests" &&
			!viewingSavedCase &&
			route.projectId === currentProjectId &&
			currentUser?.sub &&
			currentProject?.owner_user_id === currentUser.sub &&
			isAuthenticated &&
			!isVerifyingSession
		),
		scopedRequest: (path, options) => apiRequest(path, options, true, { allowGuidanceRecovery: false }),
		captureScope: captureProjectOperationScope,
		isCurrent: isProjectOperationCurrent,
		receiveProject: (data, scope) => {
			if (!isProjectOperationCurrent(scope) || !isLatestProjectRevision(data.project_id, data.current_revision)) return;
			recordProjectRevision(data.project_id, data.current_revision);
			setCurrentProject(data);
			if (data.current_revision !== currentProjectRevision) {
				hydrateProjectWorkflow(data);
				void loadProjectOrchestrator(data.project_id, { silent: true, operationScope: scope });
			}
		},
	});
	const scopedImpact = impactApplication.scoped;
	const isApplyingImpactUpdate = Boolean(
		scopedImpact?.active || ["submitting", "applying", "checking", "unreachable"].includes(impactApplication.state)
	);
	const isProjectRequestCurrent = (projectId, { routeRequestId = null, operationScope = null } = {}) => {
		if (routeRequestId !== null) {
			const browserRoute = typeof window === "undefined" ? route : parseWorkflowRoute(window.location.pathname);
			if (projectRouteRequestRef.current !== routeRequestId || browserRoute.kind !== "project" || browserRoute.projectId !== projectId) {
				return false;
			}
		}
		return !operationScope || (operationScope.projectId === projectId && isProjectOperationCurrent(operationScope));
	};
	syncProjectOperationScope();
	const billingEnforcementEnabled = Boolean(billingEntitlements && !billingEntitlements.shadow_mode);
	const requirementWorkflowLocked = Boolean(
		billingEnforcementEnabled && billingEntitlements?.account?.plan_tier === "pilot" && billingEntitlements?.requirements?.exhausted
	);
	const testCaseWorkflowLocked = Boolean(
		billingEnforcementEnabled && billingEntitlements?.account?.plan_tier === "pilot" && billingEntitlements?.test_cases?.exhausted
	);
	const requirementActionDisabled = authActionDisabled || requirementWorkflowLocked || isSavingRequirementReview;
	const testCaseActionDisabled = authActionDisabled || testCaseWorkflowLocked || isPreviewingExecution || isRunningExecution;
	const jiraConnection = jiraConnectionStatus?.connection || null;
	const jiraConnected = Boolean(jiraConnectionStatus?.connected && jiraConnection);
	const hasJiraRequirements = requirements.some((requirement) => isJiraLinkedRequirement(requirement));
	const jiraSyncIssues = Array.isArray(jiraSyncPreview?.issues) ? jiraSyncPreview.issues : [];
	const jiraPreviewHasReadyIssue = jiraSyncIssues.some((issue) => issue.status === "ready");
	const selectedJiraIssue = jiraIssueResults.find((issue) => issue.key === selectedJiraIssueKey) || null;
	const jiraIssueTypeOptions = jiraProjectIssueTypes.length
		? jiraProjectIssueTypes.map((issueType) => issueType.name)
		: DEFAULT_JIRA_ISSUE_TYPE_OPTIONS;
	const jiraImportedIssueKeys = [
		...new Set(
			requirements
				.filter((requirement) => isJiraLinkedRequirement(requirement))
				.map((requirement) => requirement?.source_issue_key || requirement?.sync_target_issue_key || "")
				.filter(Boolean)
		),
	];
	const azureDevOpsConnection = azureDevOpsConnectionStatus?.connection || null;
	const azureDevOpsConnected = Boolean(azureDevOpsConnectionStatus?.connected && azureDevOpsConnection);
	const hasAzureDevOpsRequirements = requirements.some((requirement) => isAzureDevOpsLinkedRequirement(requirement));
	const azureDevOpsSyncWorkItems = Array.isArray(azureDevOpsSyncPreview?.work_items) ? azureDevOpsSyncPreview.work_items : [];
	const azureDevOpsPreviewHasReadyWorkItem = azureDevOpsSyncWorkItems.some((workItem) => workItem.status === "ready");
	const selectedAzureDevOpsWorkItem =
		azureDevOpsWorkItemResults.find((workItem) => `${workItem.work_item_id}` === `${selectedAzureDevOpsWorkItemId}`) || null;
	const azureDevOpsWorkItemTypeOptions = azureDevOpsWorkItemTypes.length
		? azureDevOpsWorkItemTypes.map((workItemType) => workItemType.name)
		: DEFAULT_AZURE_DEVOPS_WORK_ITEM_TYPE_OPTIONS;
	const azureDevOpsImportedWorkItemIds = [
		...new Set(
			requirements
				.filter((requirement) => isAzureDevOpsLinkedRequirement(requirement))
				.map((requirement) => requirement?.source_issue_key || requirement?.sync_target_issue_key || "")
				.filter(Boolean)
		),
	];
	const requirementStatusCounts = requirements.reduce((acc, requirement) => {
		const status = getRequirementReviewStatus(requirement);
		acc[status] = (acc[status] || 0) + 1;
		return acc;
	}, {});
	const approvedRequirements = useMemo(
		() =>
			requirements.filter(
				(requirement) =>
					requirement.lifecycle_status !== "retired" &&
					getRequirementReviewStatus(requirement) === "Approved" &&
					!["open", "deferred", "excluded"].includes(requirement.clarification_status)
			),
		[requirements]
	);
	const approvedRequirementCount = approvedRequirements.length;
	const rejectedRequirementCount = requirementStatusCounts.Rejected || 0;
	const reviewPendingRequirementCount = requirements.length - approvedRequirementCount - rejectedRequirementCount;
	const canGenerateFromApprovedRequirements = approvedRequirementCount > 0;
	const projectStageState = currentProject?.stage_state || {};
	const useCasesStageState = projectStageState.use_cases || {};
	const latestUseCasesHumanReview = useCasesStageState.metadata?.latest_human_review;
	const useCasesHumanApproved = Boolean(
		useCasesStageState.approved &&
		!useCasesStageState.stale &&
		latestUseCasesHumanReview?.snapshot_id === useCasesStageState.current_snapshot_id &&
		latestUseCasesHumanReview?.decision === "approve"
	);
	const contextRefreshRequired = PROJECT_WORKSPACES_V2 && Boolean(projectStageState.context?.stale);
	const normalGenerationReady = Boolean(
		projectStageState.requirements?.current_snapshot_id &&
		hasApprovedGenerationScope(currentProject) &&
		!projectStageState.requirements.stale &&
		!contextRefreshRequired &&
		(!useCasesStageState.current_snapshot_id || useCasesHumanApproved)
	);
	const projectSnapshots = currentProject?.current_snapshots || {};
	const contextSnapshot = projectSnapshots.context;
	const savedContextForm = contextFormValues(contextSnapshot?.payload);
	const contextDraft = useReviewDraft({
		identity: PROJECT_WORKSPACES_V2 ? currentUser?.sub : "",
		projectId: currentProjectId,
		snapshotId: contextRefreshRequired
			? `${contextSnapshot?.snapshot_id || "empty"}:${projectStageState.requirements?.current_snapshot_id || "empty"}`
			: contextSnapshot?.snapshot_id || "empty",
		name: "context-inputs",
		initialValue: savedContextForm,
	});
	const contextDraftChanged = JSON.stringify(contextDraft.value) !== JSON.stringify(savedContextForm);
	const legacyContextForm = { appLink, prototypeLink, diagramLinks, imageLinks, contextNotes, selectedArtifactSourceIds };
	const visibleContextForm = PROJECT_WORKSPACES_V2 ? contextDraft.value : legacyContextForm;
	const hasContextInputs = Object.entries(visibleContextForm).some(
		([field, value]) => field !== "selectedArtifactSourceIds" && value.trim()
	);
	const setContextDraftField = (field) => (next) =>
		contextDraft.setValue((current) => ({ ...current, [field]: typeof next === "function" ? next(current[field]) : next }));
	const persistedTestCaseCount = projectSnapshots.test_cases?.payload?.test_cases?.length || 0;
	const hasExistingTestCaseBaseline = Boolean(testCases.length || persistedTestCaseCount || projectSnapshots.test_cases);
	const impactStageState = projectStageState.impact_analysis || {};
	const testCaseStageState = projectStageState.test_cases || {};
	const savedSuite = projectSnapshots.test_cases;
	const [priorityCaseState, setPriorityCaseState] = useState(null);
	const caseEntryState = priorityCaseState?.location === location ? priorityCaseState : null;
	const requestedPriorityCase = location.state?.priorityTestCase;
	const priorityCase =
		APPROVAL_CONTINUATION_ENABLED &&
		!caseEntryState?.dismissed &&
		!viewingSavedCase &&
		route.kind === "project" &&
		route.destination === PROJECT_DESTINATIONS.TEST_CASES &&
		currentProjectId === route.projectId &&
		currentUser?.sub &&
		requestedPriorityCase?.identity === currentUser.sub &&
		requestedPriorityCase.projectId === currentProjectId &&
		requestedPriorityCase.snapshotId === savedSuite?.snapshot_id &&
		savedSuite?.project_id === currentProjectId &&
		testCaseStageState.current_snapshot_id === savedSuite.snapshot_id &&
		!testCaseStageState.stale
			? findActiveSavedCase(savedSuite, requestedPriorityCase.caseId)
			: null;
	const openingPriorityCase = Boolean(priorityCase && !caseEntryState?.opened);
	const keepPriorityCaseFocus = useRef(false);
	keepPriorityCaseFocus.current = Boolean(priorityCase);
	const traceabilityCase =
		restoredTraceabilityLocation !== location &&
		!viewingSavedCase &&
		route.kind === "project" &&
		route.destination === PROJECT_DESTINATIONS.TEST_CASES
			? location.state?.traceabilityCase
			: null;
	const isTraceabilityReturn = (projectId, snapshotId) =>
		traceabilityCase && snapshotId && traceabilityCase.projectId === projectId && traceabilityCase.snapshotId === snapshotId;
	const traceabilityReturn =
		currentProjectId === route.projectId && isTraceabilityReturn(currentProjectId, savedSuite?.snapshot_id) ? traceabilityCase : null;
	const activeGenerateResultTab = openingPriorityCase ? "test-cases" : traceabilityReturn ? "traceability" : selectedGenerateResultTab;
	const savedSuiteReviewKey = JSON.stringify([currentUser?.sub, currentProjectId, savedSuite?.snapshot_id, currentProjectRevision]);
	const syncDraftSuite = ["project.sync.draft", "project.sync.rebind"].includes(savedSuite?.operation);
	const showSyncDraftReview = Boolean(
		APPROVAL_CONTINUATION_ENABLED &&
		syncDraftSuite &&
		currentUser?.sub &&
		currentProject?.owner_user_id === currentUser.sub &&
		savedSuite.project_id === currentProjectId &&
		savedSuite.snapshot_id &&
		savedSuite.snapshot_id === testCaseStageState.current_snapshot_id
	);
	const syncDraftPlanId = savedSuite?.source_snapshot_id;
	const currentScenarioSnapshotId = projectSnapshots.use_cases?.snapshot_id;
	const syncDraftMatchesPlan = Boolean(
		syncDraftPlanId &&
		syncDraftPlanId === currentScenarioSnapshotId &&
		syncDraftPlanId === useCasesStageState.current_snapshot_id &&
		!useCasesStageState.stale
	);
	const syncChange = currentProject?.project_sync;
	const materialSync =
		APPROVAL_CONTINUATION_ENABLED &&
		(currentProject?.material_requirements_review_required === true || currentProject?.material_scope_pending_rebind === true);
	const reboundSyncPlan = APPROVAL_CONTINUATION_ENABLED && projectSnapshots.use_cases?.operation === "project.sync.rebind";
	const materialSyncRebindRequired = materialSync && (!reboundSyncPlan || useCasesStageState.stale);
	const syncDraftChangeId = savedSuite?.metadata?.change_id;
	const syncDraftSuperseded =
		showSyncDraftReview &&
		(testCaseStageState.stale ||
			syncChange?.status === "superseded" ||
			(syncDraftChangeId && syncChange?.id && syncDraftChangeId !== syncChange.id) ||
			(syncDraftPlanId && currentScenarioSnapshotId && !syncDraftMatchesPlan));
	const approvedGenerationScopeReady = hasApprovedGenerationScope(currentProject);
	const syncDraftNeedsScenarioApproval =
		showSyncDraftReview && !syncDraftSuperseded && syncDraftMatchesPlan && approvedGenerationScopeReady && !useCasesHumanApproved;
	const syncDraftReviewReady =
		showSyncDraftReview &&
		!syncDraftSuperseded &&
		syncDraftMatchesPlan &&
		approvedGenerationScopeReady &&
		useCasesHumanApproved &&
		savedSuite.metadata?.review_retry_available === true;
	const savedSuiteReviewPending =
		persistedTestCaseCount > 0 &&
		savedSuite?.payload?.substantive_assessment?.status === "unknown" &&
		(syncDraftSuite
			? syncDraftReviewReady
			: savedSuite?.operation === "testcases.repair"
				? APPROVAL_CONTINUATION_ENABLED && !testCaseStageState.stale
				: savedSuite?.metadata?.review_retry_available === true ||
					["impact.update.apply", "testcases.refine", "quality.feedback.apply"].includes(savedSuite?.operation));

	const savedSuiteInputsReady =
		currentProject?.status === "active" &&
		approvedGenerationScopeReady &&
		useCasesHumanApproved &&
		(!testCaseStageState.stale || savedSuite?.operation === "quality.feedback.apply");
	const savedSuiteReviewState = savedSuiteReview?.key === savedSuiteReviewKey ? savedSuiteReview : null;
	const savedSuiteReviewed = savedSuite?.operation === "test_cases.review" && !testCaseStageState.stale;
	const syncSuiteReviewSaved =
		APPROVAL_CONTINUATION_ENABLED &&
		savedSuiteReviewed &&
		savedSuite.snapshot_id === testCaseStageState.current_snapshot_id &&
		savedSuite.metadata?.sync_review_binding?.test_cases_snapshot_id === savedSuite.source_snapshot_id &&
		Boolean(savedSuite.source_snapshot_id);
	const savedSuiteReviewUnavailable =
		!savedSuiteInputsReady || authActionDisabled || isGenerating || isApplyingImpactUpdate || isAnalyzingImpact || isRunningExecution;
	const testCaseFindings = improvementFindings(testCaseReview, substantiveAssessment);
	const testCasesNeedSetup =
		testCaseReview?.approved === false &&
		!testCaseStageState.stale &&
		!generationTasks.length &&
		!testCaseFindings.length &&
		substantiveAssessment?.design_status === "assessed_complete" &&
		substantiveAssessment.execution_status === "blocked";
	const upstreamChangedForImpact = Boolean(hasExistingTestCaseBaseline && (testCaseStageState.stale || impactStageState.stale));
	const estimatedImpactChangedRequirementCount = estimateChangedRequirementCount(projectSnapshots);
	const impactChangedItemCount =
		impactAnalysis?.summary?.changed_item_count ??
		impactStageState?.metadata?.changed_item_count ??
		impactAnalysis?.changed_items?.length ??
		estimatedImpactChangedRequirementCount;
	const impactRecommendationCounts = impactAnalysis?.summary?.recommendation_counts || {};
	const impactRecommendations = impactAnalysis?.recommendations || [];
	const impactAnalysisId = projectSnapshots.impact_analysis?.snapshot_id;
	const scopedImpactSelection = useReviewDraft({
		identity: scopedImpact ? currentUser?.sub : "",
		projectId: currentProjectId,
		snapshotId: impactAnalysisId || "no-analysis",
		name: "scoped-impact-selection",
		initialValue: [],
	});
	const isImpactRecommendationSelectable = (item) =>
		scopedImpact
			? scopedImpact.selectable.has(item.recommendation_id)
			: !impactApplication.providerRestricted || !["add", "update"].includes(item.action);
	const acceptedImpactRecommendationIds = scopedImpact
		? scopedImpact.boundSelection || (Array.isArray(scopedImpactSelection.value) ? scopedImpactSelection.value : [])
		: impactApplication.operation?.accepted_recommendation_ids ||
			impactRecommendations.filter((item) => item.accepted && isImpactRecommendationSelectable(item)).map((item) => item.recommendation_id);
	const impactRecommendationsForApplication =
		scopedImpact || impactApplication.providerRestricted
			? impactRecommendations.map((item) => ({ ...item, accepted: acceptedImpactRecommendationIds.includes(item.recommendation_id) }))
			: impactRecommendations;

	const impactChangedItems = impactAnalysis?.changed_items || [];
	const impactApplyBlockedByStageApproval = impactChangedItems.some((item) => {
		if (!["added", "modified"].includes(item.change_type)) return false;
		const requirementChanged = item.kind === "requirement" || item.requirement_id;
		const useCaseChanged = item.kind === "use_case" || item.use_case_id || (item.scenario_ids || []).length > 0;
		const requirementsBlocked = requirementChanged && (!projectStageState.requirements?.approved || projectStageState.requirements?.stale);
		const useCasesBlocked = useCaseChanged && !useCasesHumanApproved;
		return requirementsBlocked || useCasesBlocked;
	});
	const impactApplyBlockedByApproval = Boolean(
		impactApplyBlockedByStageApproval ||
		impactChangedItems.some((item) => ["added", "modified"].includes(item.change_type) && !item.approved)
	);
	const canAnalyzeImpact = Boolean(
		currentProjectId && hasExistingTestCaseBaseline && (upstreamChangedForImpact || generationTasks.length) && !scopedImpact?.active
	);
	const canApplyImpactUpdate = Boolean(
		currentProjectId &&
		impactAnalysis &&
		!impactApplyBlockedByApproval &&
		acceptedImpactRecommendationIds.length > 0 &&
		(!scopedImpact || (scopedImpact.canApply && scopedImpact.validSelection(acceptedImpactRecommendationIds))) &&
		(!impactApplication.providerRestricted ||
			impactRecommendations.every(
				(item) => !acceptedImpactRecommendationIds.includes(item.recommendation_id) || isImpactRecommendationSelectable(item)
			)) &&
		!isApplyingImpactUpdate &&
		["ready", "failed"].includes(impactApplication.state)
	);
	const requirementReviewMeta = getReviewScoreMeta(requirementReview);
	const [selectedTestCaseId, setSelectedTestCaseId] = useState(null);
	const selectedPriorityCaseExpired =
		caseEntryState?.opened && !caseEntryState.dismissed && requestedPriorityCase?.caseId === selectedTestCaseId && !priorityCase;
	const [findingCaseSelection, setFindingCaseSelection] = useState(null);
	const [reviewCaseScope, setReviewCaseScope] = useState(null);

	const pasteDraft = useReviewDraft({
		identity: currentUser?.sub,
		projectId: currentProjectId,
		snapshotId: currentProject?.current_snapshots?.requirements?.snapshot_id || "empty",
		name: "pasted-requirements",
		initialValue: "",
	});
	const [reportedCase, setReportedCase] = useState(null);
	const sourceCaseSnapshot = `${currentProjectId}:${currentProject?.current_snapshots?.test_cases?.snapshot_id || "draft"}`;
	const [selectedRepairReader, setSelectedRepairReader] = useState(null);
	const readSelectedRepairCase = useCallback(
		(caseId) => {
			setSelectedRepairReader({ source: sourceCaseSnapshot, identity: currentUser?.sub, caseId });
		},
		[sourceCaseSnapshot, currentUser?.sub]
	);
	const activeReviewCaseScope = reviewCaseScope?.snapshot === sourceCaseSnapshot ? reviewCaseScope : null;
	const focusResultContent = useRef(false);
	const [improveMountedScope, setImproveMountedScope] = useState(null);
	useEffect(() => {
		if (activeTab === 3 && activeGenerateResultTab === "improve") setImproveMountedScope(sourceCaseSnapshot);
	}, [activeTab, activeGenerateResultTab, sourceCaseSnapshot]);
	useEffect(() => {
		setSelectedTestCaseId(null);
		setFindingCaseSelection(null);
		setReportedCase(null);
		setChangedTestCaseIds(null);
		setReviewCaseScope(null);
	}, [sourceCaseSnapshot]);
	useEffect(() => {
		if (focusResultContent.current) {
			if (!openingPriorityCase) document.getElementById("generate-result-panel")?.focus();
			focusResultContent.current = false;
		}
	}, [activeGenerateResultTab, openingPriorityCase]);
	const dismissPriorityCase = () => {
		if (priorityCase) setPriorityCaseState({ location, opened: true, dismissed: true });
	};
	const finishPriorityCaseEntry = () => {
		if (!priorityCase) return;
		setSelectedTestCaseId(priorityCase.id);
		setActiveGenerateResultTab("test-cases");
		setPriorityCaseState({ location, opened: true });
	};
	const openCaseResults = (id = null, group = null, { fromFinding = false } = {}) => {
		dismissPriorityCase();
		setSelectedTestCaseId(id);
		setFindingCaseSelection(id && (fromFinding || group) ? { id, source: sourceCaseSnapshot } : null);
		setReviewCaseScope(group ? { ...group, snapshot: sourceCaseSnapshot } : null);
		if (activeGenerateResultTab === "test-cases") document.getElementById("generate-result-panel")?.focus();
		else focusResultContent.current = true;
		setActiveGenerateResultTab("test-cases");
	};
	const testCaseReviewMeta = getReviewScoreMeta(testCaseReview);
	const requirementSourceMetricMeta = getRequirementSourceMetricMeta(requirementCoverageMetrics);
	const requirementReportStats = [
		requirementReview
			? {
					label: "Quality score",
					value: `${requirementReviewMeta.score}/100`,
					emphasis: true,
				}
			: null,
		requirementReviewMeta.threshold > 0
			? {
					label: "Approval threshold",
					value: requirementReviewMeta.threshold,
				}
			: null,
		requirementCoverageMetrics || requirements.length > 0
			? {
					label: "Requirements",
					value: hasMetricValue(requirementCoverageMetrics, "total_requirements")
						? requirementCoverageMetrics.total_requirements
						: requirements.length,
				}
			: null,
		requirementSourceMetricMeta.countLabel
			? {
					label: requirementSourceMetricMeta.countLabel,
					value: requirementSourceMetricMeta.countValue,
				}
			: null,
		hasMetricValue(requirementCoverageMetrics, "requirements_per_document")
			? {
					label: requirementSourceMetricMeta.perLabel,
					value: requirementCoverageMetrics.requirements_per_document,
				}
			: null,
		hasMetricValue(requirementCoverageMetrics, "unique_requirements")
			? {
					label: "Unique",
					value: requirementCoverageMetrics.unique_requirements,
				}
			: null,
		hasMetricValue(requirementCoverageMetrics, "duplicate_requirements")
			? {
					label: "Duplicates",
					value: requirementCoverageMetrics.duplicate_requirements,
				}
			: null,
		hasMetricValue(requirementCoverageMetrics, "shall_format_count")
			? {
					label: "Shall format",
					value: hasMetricValue(requirementCoverageMetrics, "total_requirements")
						? `${requirementCoverageMetrics.shall_format_count}/${requirementCoverageMetrics.total_requirements}`
						: requirementCoverageMetrics.shall_format_count,
				}
			: null,
		requirementWorkflowDiagnostics?.status
			? {
					label: "Workflow status",
					value: formatWorkflowStatusLabel(requirementWorkflowDiagnostics.status),
				}
			: null,
		requirementIterationHistory.length
			? {
					label: "Iterations",
					value: requirementIterationHistory.length,
				}
			: null,
		appliedRequirementWorkflowSettings?.max_iterations != null
			? {
					label: "Max iterations",
					value: appliedRequirementWorkflowSettings.max_iterations,
				}
			: null,
	].filter(Boolean);
	const requirementReportFlags = [
		requirementWorkflowDiagnostics?.timed_out ? { label: "Timed out", tone: "warning" } : null,
		requirementWorkflowDiagnostics?.stalled ? { label: "Stalled", tone: "warning" } : null,
		requirementWorkflowDiagnostics?.used_fallback ? { label: "Fallback used", tone: "warning" } : null,
		requirementWorkflowDiagnostics?.max_iterations_reached ? { label: "Max iterations reached", tone: "warning" } : null,
		requirementWorkflowDiagnostics?.failure_reason
			? {
					label: `Reason: ${formatWorkflowStatusLabel(requirementWorkflowDiagnostics.failure_reason)}`,
					tone: "muted",
				}
			: null,
	].filter(Boolean);
	const requirementWarnings = requirementWorkflowDiagnostics?.warnings || [];
	const requirementParserFailures = requirementWorkflowDiagnostics?.parser_failures || [];
	const requirementBlockingIssues = requirementReview?.approved ? [] : requirementReview?.blocking_issues || [];
	const requirementReportDetailCount = requirementBlockingIssues.length + requirementWarnings.length + requirementParserFailures.length;

	const chooseGenerateResultTab = (data) => {
		if (data?.review?.approved === false) return "improve";
		const metrics = data?.coverage_metrics || null;
		if ((metrics?.requirements_without_tests || []).length > 0) {
			return "traceability";
		}
		if ((metrics?.missing_must_have_scenarios || []).length > 0 || (metrics?.missing_scenarios || []).length > 0) {
			return "traceability";
		}
		return "test-cases";
	};

	const resetGeneratedArtifacts = () => {
		resetTestCaseWorkflowState();
		setImpactAnalysis(null);
		resetExportWorkflowState();
		resetExecutionWorkflowState();
	};

	const saveRequirementReviews = async (reviews, { prepareScenarios = false } = {}) => {
		if (requirementReviewLock.current) return;
		const operationScope = captureProjectOperationScope();
		if (!operationScope || reviews.length === 0) return;
		requirementReviewLock.current = operationScope;
		setIsSavingRequirementReview(true);
		const changes = reviews.map((r) => ({
			requirement_uid: r.requirement_uid,
			review_status: r.review_status,
			quality_flags: r.quality_flags || [],
		}));
		const fingerprint = JSON.stringify([operationScope.projectId, currentProjectRevision, changes, prepareScenarios]);
		if (requirementReviewAttempt.current?.fingerprint !== fingerprint) {
			const prior = prepareScenarios ? approvalContinuation.pendingRequest : null;
			requirementReviewAttempt.current = { fingerprint, key: prior?.fingerprint === fingerprint ? prior.requestId : createRequestId() };
		}
		const { key } = requirementReviewAttempt.current;
		const projectSegment = encodeURIComponent(operationScope.projectId);
		const path = prepareScenarios
			? API_CONTRACT_ENDPOINTS.approveAndPrepareScenarios.path.replace("{project_id}", projectSegment)
			: `/projects/${projectSegment}/requirements/reviews`;
		if (prepareScenarios) approvalContinuation.begin({ requestId: key, fingerprint, baseRevision: currentProjectRevision });
		try {
			const res = await apiRequest(path, {
				method: prepareScenarios ? API_CONTRACT_ENDPOINTS.approveAndPrepareScenarios.method : "PATCH",
				headers: { "Content-Type": "application/json", ...(prepareScenarios ? { "X-Request-ID": key } : {}) },
				body: JSON.stringify({
					base_project_revision: currentProjectRevision,
					idempotency_key: key,
					reviews: changes,
				}),
			});
			if (!res.ok) {
				if (prepareScenarios) {
					const conflict =
						res.status === 409
							? await res
									.clone()
									.json()
									.catch(() => null)
							: null;
					const staleRevision = conflict?.detail?.code === "stale_import" && Number.isInteger(conflict.detail.latest_revision);
					if (res.status < 500 && (res.status !== 409 || staleRevision)) approvalContinuation.clearPending(key);
					if (staleRevision && isProjectOperationCurrent(operationScope)) {
						requirementReviewAttempt.current = null;
						void refreshCurrentProject({ hydrate: true, operationScope });
						throw new Error("The project changed before this approval could be saved. The current scope is being reloaded.");
					}
				}
				throw new Error(await parseApiError(res, "Could not save requirement reviews"));
			}
			const data = await res.json();
			if (!isProjectOperationCurrent(operationScope)) return;
			const project = prepareScenarios ? data.project : data;
			if (prepareScenarios) approvalContinuation.accept(data, key);
			requirementReviewAttempt.current = null;
			if (!isLatestProjectRevision(project.project_id, project.current_revision)) return;
			recordProjectRevision(project.project_id, project.current_revision);
			setCurrentProject(project);
			hydrateProjectWorkflow(project);
			setStatus("Requirement review saved.");
			await refreshRequirementGuidance(project, operationScope);
		} catch (error) {
			if (isProjectOperationCurrent(operationScope)) setStatus(error.message);
		} finally {
			if (requirementReviewLock.current === operationScope) {
				requirementReviewLock.current = false;
				setIsSavingRequirementReview(false);
			}
		}
	};
	const updateRequirementReviewStatus = (requirementId, reviewStatus) =>
		saveRequirementReviews(requirements.filter((r) => r.id === requirementId).map((r) => ({ ...r, review_status: reviewStatus })));
	const bulkUpdateRequirementReviewStatus = (reviewStatus, predicate = () => true) =>
		saveRequirementReviews(requirements.filter(predicate).map((r) => ({ ...r, review_status: reviewStatus })));
	const toggleRequirementQualityFlag = (requirementId, flag) =>
		saveRequirementReviews(
			requirements
				.filter((r) => r.id === requirementId)
				.map((r) => {
					const flags = normalizeStringArray(r.quality_flags);
					return { ...r, quality_flags: flags.includes(flag) ? flags.filter((f) => f !== flag) : [...flags, flag] };
				})
		);

	const buildContextPayload = (requirementsOverride = requirements, { saveInputs = false } = {}) => {
		const { appLink, prototypeLink, diagramLinks, imageLinks, contextNotes, selectedArtifactSourceIds } =
			PROJECT_WORKSPACES_V2 && !saveInputs ? savedContextForm : visibleContextForm;
		const context = PROJECT_WORKSPACES_V2 ? contextSnapshot?.payload : enrichedContext;
		const baseContext = {
			requirements: requirementsOverride,
			app_link: appLink || null,
			prototype_link: prototypeLink || null,
			diagram_links: diagramLinks
				? diagramLinks
						.split(";")
						.map((x) => x.trim())
						.filter(Boolean)
				: null,
			image_links: imageLinks
				? imageLinks
						.split(";")
						.map((x) => x.trim())
						.filter(Boolean)
				: null,
			notes: contextNotes || null,
			execution_prerequisites: context?.execution_prerequisites || contextSnapshot?.payload?.execution_prerequisites || [],
			project_id: currentProjectId || null,
			base_project_revision: currentProjectRevision,
		};

		if (!context?.grounded_context || (PROJECT_WORKSPACES_V2 && projectStageState.context?.stale)) {
			return { ...baseContext, grounded_context: null };
		}

		const selectedIds = new Set(selectedArtifactSourceIds);
		const groundedContext = context.grounded_context;
		return {
			...baseContext,
			grounded_context: {
				...groundedContext,
				artifact_sources: (groundedContext.artifact_sources || []).filter((source) => selectedIds.has(source.id)),
				ui_elements: (groundedContext.ui_elements || []).filter((element) => !element.source_id || selectedIds.has(element.source_id)),
			},
		};
	};

	const renderRequirementReviewReport = () => {
		if (!requirementReview && !requirementCoverageMetrics && !requirementWorkflowDiagnostics && !appliedRequirementWorkflowSettings) {
			return null;
		}

		return (
			<div className={`requirement-report-card ${requirementReview?.approved ? "approved" : "needs-work"}`}>
				<div className="requirement-report-header">
					<div className="requirement-report-copy">
						<div className="requirement-report-title-row">
							<h3>Requirement review summary</h3>
							{requirementReview && (
								<span className={`requirement-report-status ${requirementReview.approved ? "approved" : "needs-work"}`}>
									{requirementReview.approved ? "Approved" : "Needs refinement"}
								</span>
							)}
						</div>
						<p>
							{requirementReview?.summary || "A compact view of quality, coverage, and workflow state for the current requirement set."}
						</p>
					</div>
					{requirementReportFlags.length > 0 && (
						<div className="requirement-report-flags">
							{requirementReportFlags.map((flag) => (
								<span key={flag.label} className={`requirement-report-flag ${flag.tone}`}>
									{flag.label}
								</span>
							))}
						</div>
					)}
				</div>

				{requirementReportStats.length > 0 && (
					<TableScroll
						className="requirement-report-table-wrapper"
						role="region"
						aria-label="Requirement review summary table"
						tabIndex={0}
					>
						<Table className="requirement-report-table">
							<thead>
								<tr>
									{requirementReportStats.map((stat) => (
										<th key={stat.label} scope="col">
											{stat.label}
										</th>
									))}
								</tr>
							</thead>
							<tbody>
								<tr>
									{requirementReportStats.map((stat) => (
										<td key={stat.label} className={stat.emphasis ? "emphasis" : ""}>
											<strong>{stat.value}</strong>
										</td>
									))}
								</tr>
							</tbody>
						</Table>
					</TableScroll>
				)}

				{requirementReportDetailCount > 0 && (
					<Disclosure className="requirement-report-details">
						<summary>
							<span>Workflow notes</span>
							<span className="requirement-report-details-count">
								{requirementReportDetailCount} item{requirementReportDetailCount === 1 ? "" : "s"}
							</span>
						</summary>
						<div className="requirement-report-details-body">
							{requirementBlockingIssues.length > 0 && (
								<div className="requirement-report-detail-block issue">
									<strong>Blocking issues</strong>
									<List>
										{requirementBlockingIssues.slice(0, 4).map((issue) => (
											<ListItem key={issue}>{issue}</ListItem>
										))}
									</List>
								</div>
							)}

							{requirementWarnings.length > 0 && (
								<div className="requirement-report-detail-block warning">
									<strong>Warnings</strong>
									<List>
										{requirementWarnings.map((warning) => (
											<ListItem key={warning}>{warning}</ListItem>
										))}
									</List>
								</div>
							)}

							{requirementParserFailures.length > 0 && (
								<div className="requirement-report-detail-block alert">
									<strong>Parser issues</strong>
									<List>
										{requirementParserFailures.map((failure) => (
											<ListItem key={failure}>{failure}</ListItem>
										))}
									</List>
								</div>
							)}
						</div>
					</Disclosure>
				)}
			</div>
		);
	};

	const clearAuthState = (nextStatus = null) => {
		projectCreateRequestRef.current += 1;
		projectListRequestRef.current += 1;
		projectOperationUserRef.current = "";
		syncProjectOperationScope();
		setAuthToken("");
		setCurrentUser(null);
		setActiveAuthProvider("");
		setUsageSummary(null);
		setIsUsageLoading(false);
		setBillingEntitlements(null);
		setIsBillingLoading(false);
		setProjects([]);
		setCurrentProject(null);
		setOrchestratorStatus(null);
		setOrchestratorError("");
		setIsLoadingOrchestrator(false);
		setIsCreatingProject(false);
		setRequirementSourceMode("file");
		setJiraConnectionStatus(EMPTY_JIRA_CONNECTION_STATUS);
		setJiraConnectionForm(EMPTY_JIRA_CONNECTION_FORM);
		setJiraProjects([]);
		setSelectedJiraProjectKey("");
		setJiraProjectIssueTypes([]);
		setJiraIssueType("");
		setJiraIssueResults([]);
		setSelectedJiraIssueKey("");
		setJiraSyncPreview(null);
		setJiraSyncResults(null);
		resetExecutionWorkflowState();
		setAzureDevOpsConnectionStatus(EMPTY_AZURE_DEVOPS_CONNECTION_STATUS);
		setAzureDevOpsConnectionForm(EMPTY_AZURE_DEVOPS_CONNECTION_FORM);
		setAzureDevOpsProjects([]);
		setSelectedAzureDevOpsProject("");
		setAzureDevOpsWorkItemTypes([]);
		setAzureDevOpsWorkItemType("");
		setAzureDevOpsWorkItemResults([]);
		setSelectedAzureDevOpsWorkItemId("");
		setAzureDevOpsSyncPreview(null);
		setAzureDevOpsSyncResults(null);
		localStorage.removeItem(STORAGE_AUTH_TOKEN);
		localStorage.removeItem(STORAGE_AUTH_USER);
		localStorage.removeItem(STORAGE_CURRENT_PROJECT_ID);
		if (nextStatus) {
			setStatus(nextStatus);
		}
	};

	const persistAuthState = (token, user) => {
		const nextUserId = user?.sub || "";
		if (projectOperationUserRef.current !== nextUserId) {
			projectCreateRequestRef.current += 1;
			projectListRequestRef.current += 1;
		}
		projectOperationUserRef.current = nextUserId;
		syncProjectOperationScope();
		setAuthToken(token);
		setCurrentUser(user);
		if (token) {
			localStorage.setItem(STORAGE_AUTH_TOKEN, token);
		}
		if (user) {
			localStorage.setItem(STORAGE_AUTH_USER, JSON.stringify(user));
		}
	};

	const buildFirebaseFallbackUser = (firebaseUser) => ({
		sub: firebaseUser.uid,
		email: firebaseUser.email || null,
		name: firebaseUser.displayName || firebaseUser.email || firebaseUser.phoneNumber || firebaseUser.uid,
		picture: firebaseUser.photoURL || null,
		provider: firebaseUser.providerData?.[0]?.providerId || null,
		email_verified: firebaseUser.emailVerified ?? null,
	});

	const syncFirebaseSession = async (firebaseUser, successPrefix = "Signed in as") => {
		const token = await firebaseUser.getIdToken();
		const fallbackUser = buildFirebaseFallbackUser(firebaseUser);
		const res = await fetch(`${API_BASE}/auth/me`, {
			method: "GET",
			headers: {
				Authorization: `Bearer ${token}`,
			},
		});

		if (!res.ok) {
			const errorMessage = await parseApiError(res, "Session is no longer valid");
			throw new Error(errorMessage);
		}

		const data = await res.json();
		const resolvedUser = data || fallbackUser;
		persistAuthState(token, resolvedUser);
		setStatus(`${successPrefix} ${resolvedUser.name}.`);
		return token;
	};

	const getCurrentAccessToken = async () => {
		if (!firebaseAuth?.currentUser) {
			return authToken || localStorage.getItem(STORAGE_AUTH_TOKEN) || "";
		}

		const token = await firebaseAuth.currentUser.getIdToken();
		setAuthToken((current) => (current === token ? current : token));
		return token;
	};

	const refreshUsageSummary = async (userOverride = currentUser) => {
		const user = userOverride || currentUser;
		if (!user) {
			setUsageSummary(null);
			setIsUsageLoading(false);
			return;
		}

		setIsUsageLoading(true);
		try {
			const res = await apiRequest("/reports/usage/me", { method: "GET" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to load usage summary");
				throw new Error(errorMessage);
			}

			const report = await res.json();
			setUsageSummary(getCurrentUserUsageSummary(report, user));
		} catch (error) {
			console.error("Failed to refresh usage summary", error);
			setUsageSummary(buildEmptyUsageSummary(user));
		} finally {
			setIsUsageLoading(false);
		}
	};

	const refreshBillingEntitlements = async (userOverride = currentUser) => {
		const user = userOverride || currentUser;
		if (!user) {
			setBillingEntitlements(null);
			setIsBillingLoading(false);
			return;
		}

		setIsBillingLoading(true);
		try {
			const res = await apiRequest(API_CONTRACT_ENDPOINTS.billingEntitlementsMe.path, { method: "GET" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to load billing entitlements");
				throw new Error(errorMessage);
			}

			const payload = await res.json();
			setBillingEntitlements(payload || null);
		} catch (error) {
			console.error("Failed to refresh billing entitlements", error);
			setBillingEntitlements(null);
		} finally {
			setIsBillingLoading(false);
		}
	};

	const openSignInDialog = () => {
		if (isAuthenticating || !hasVisibleAuthProviders) {
			return;
		}
		setAuthActionError("");
		setIsSignInDialogOpen(true);
	};

	const closeSignInDialog = () => {
		if (isAuthenticating) {
			return;
		}
		setIsSignInDialogOpen(false);
	};

	const handleSignInDialogOverlayClick = (event) => {
		if (event.target === event.currentTarget) {
			closeSignInDialog();
		}
	};

	const openSettingsDialog = (section = "workflow") => {
		setSettingsSection(section);
		setIsSettingsDialogOpen(true);
	};

	const closeSettingsDialog = () => {
		setIsSettingsDialogOpen(false);
	};

	const handleSettingsDialogOverlayClick = (event) => {
		if (event.target === event.currentTarget) {
			closeSettingsDialog();
		}
	};

	useEffect(() => {
		if (!firebaseAuth) {
			const storedToken = localStorage.getItem(STORAGE_AUTH_TOKEN);
			const storedUserRaw = localStorage.getItem(STORAGE_AUTH_USER);
			if (!storedToken || !storedUserRaw) {
				setIsVerifyingSession(false);
				return undefined;
			}

			try {
				const storedUser = JSON.parse(storedUserRaw);
				persistAuthState(storedToken, storedUser);
				setStatus(`Welcome back, ${storedUser.name}.`);
			} catch {
				clearAuthState();
			}
			setIsVerifyingSession(false);
			return undefined;
		}

		const unsubscribe = onAuthStateChanged(firebaseAuth, async (firebaseUser) => {
			if (!firebaseUser) {
				const storedToken = localStorage.getItem(STORAGE_AUTH_TOKEN);
				const storedUserRaw = localStorage.getItem(STORAGE_AUTH_USER);
				if (!storedToken || !storedUserRaw) {
					clearAuthState();
					setIsVerifyingSession(false);
					return;
				}

				let storedUser;
				try {
					storedUser = JSON.parse(storedUserRaw);
				} catch {
					clearAuthState();
					setIsVerifyingSession(false);
					return;
				}

				const isCurrentStoredSession = () => !firebaseAuth.currentUser && localStorage.getItem(STORAGE_AUTH_TOKEN) === storedToken;
				try {
					const res = await fetch(`${API_BASE}/auth/me`, {
						method: "GET",
						headers: {
							Authorization: `Bearer ${storedToken}`,
						},
					});
					if (!res.ok) {
						throw new Error("Stored session is no longer valid");
					}
					const data = await res.json();
					if (!isCurrentStoredSession()) return;
					persistAuthState(storedToken, data || storedUser);
					setStatus(`Welcome back, ${(data || storedUser).name}.`);
				} catch {
					if (isCurrentStoredSession()) clearAuthState("Stored compatibility session expired or is disabled. Please sign in again.");
				} finally {
					if (!firebaseAuth.currentUser) setIsVerifyingSession(false);
				}
				return;
			}

			setIsVerifyingSession(true);
			try {
				await syncFirebaseSession(firebaseUser);
			} catch (error) {
				clearAuthState(`Session expired. Please sign in again. ${error.message}`.trim());
			} finally {
				setIsVerifyingSession(false);
			}
		});

		return unsubscribe;
	}, []);

	useEffect(() => {
		if (!isAuthenticated || !currentUser) {
			setUsageSummary(null);
			setIsUsageLoading(false);
			setBillingEntitlements(null);
			setIsBillingLoading(false);
			setJiraConnectionStatus(EMPTY_JIRA_CONNECTION_STATUS);
			setJiraConnectionForm(EMPTY_JIRA_CONNECTION_FORM);
			setJiraProjects([]);
			setSelectedJiraProjectKey("");
			setJiraProjectIssueTypes([]);
			setJiraIssueType("");
			setJiraIssueResults([]);
			setSelectedJiraIssueKey("");
			setJiraSyncPreview(null);
			setJiraSyncResults(null);
			setAzureDevOpsConnectionStatus(EMPTY_AZURE_DEVOPS_CONNECTION_STATUS);
			setAzureDevOpsConnectionForm(EMPTY_AZURE_DEVOPS_CONNECTION_FORM);
			setAzureDevOpsProjects([]);
			setSelectedAzureDevOpsProject("");
			setAzureDevOpsWorkItemTypes([]);
			setAzureDevOpsWorkItemType("");
			setAzureDevOpsWorkItemResults([]);
			setSelectedAzureDevOpsWorkItemId("");
			setAzureDevOpsSyncPreview(null);
			setAzureDevOpsSyncResults(null);
			return;
		}

		void Promise.all([refreshUsageSummary(currentUser), refreshBillingEntitlements(currentUser)]);
	}, [isAuthenticated, currentUser?.sub, currentUser?.email]);

	useEffect(() => {
		if (!currentUser?.email || jiraConnected) {
			return;
		}
		setJiraConnectionForm((prev) => ({
			...prev,
			email: prev.email || currentUser.email,
		}));
	}, [currentUser?.email, jiraConnected]);

	useEffect(() => {
		if (!currentUser?.email || azureDevOpsConnected) {
			return;
		}
		setAzureDevOpsConnectionForm((prev) => ({
			...prev,
			accountEmail: prev.accountEmail || currentUser.email,
		}));
	}, [currentUser?.email, azureDevOpsConnected]);

	const navigationProjectId = route.kind === "project" ? route.projectId : "";
	const knowledgeScope = `${currentUser?.sub || ""}:${navigationProjectId || ""}`;
	const guidanceRecovery = useGuidanceRecovery(knowledgeScope);
	const [generationGuidance, setGenerationGuidance] = useState({});
	const recordedGuidance = (stage) =>
		projectSnapshots[stage]?.metadata?.guidance ||
		(generationGuidance.scope === knowledgeScope ? generationGuidance[stage]?.guidance : null);
	const guidanceSummary = (stage) => (
		<GuidanceUsed
			key={`${knowledgeScope}:${recordedGuidance(stage)?.manifest_id || stage}`}
			manifest={recordedGuidance(stage)}
			request={apiRequest}
			projectId={navigationProjectId}
		/>
	);
	const suggestionSummary = (stage) =>
		generationGuidance.scope === knowledgeScope && (
			<KnowledgeSuggestion
				key={`suggestion:${knowledgeScope}:${stage}:${JSON.stringify(generationGuidance[stage]?.knowledge_suggestion)}`}
				suggestion={generationGuidance[stage]?.knowledge_suggestion}
				request={apiRequest}
				projectId={navigationProjectId}
			/>
		);
	const rememberGeneration = (stage, data) =>
		setGenerationGuidance((current) => ({
			...(current.scope === knowledgeScope ? current : {}),
			scope: knowledgeScope,
			[stage]: { guidance: data.guidance, knowledge_suggestion: data.knowledge_suggestion },
		}));
	const apiRequest = async (path, options = {}, authRequired = true, { allowGuidanceRecovery = true } = {}) => {
		const headers = ensureRequestIdHeader(options.headers || {});

		if (authRequired) {
			const token = await getCurrentAccessToken();
			if (!token) {
				setStatus(AUTH_REQUIRED_MESSAGE);
				throw new Error(AUTH_REQUIRED_MESSAGE);
			}
			headers.Authorization = `Bearer ${token}`;
		}

		let res;
		for (;;) {
			res = await fetch(`${API_BASE}${path}`, {
				...options,
				cache: "no-store",
				headers,
			});

			if (authRequired && res.status === 401) {
				clearAuthState("Session expired or unauthorized. Please sign in again.");
				throw new Error("Session expired or unauthorized. Please sign in again.");
			}

			const detail =
				res.status === 503
					? (
							await res
								.clone()
								.json()
								.catch(() => ({}))
						).detail
					: null;
			if (!allowGuidanceRecovery || detail?.code !== "knowledge_unavailable" || !detail.can_bypass) return res;
			const choice = await guidanceRecovery.decide(knowledgeScope);
			if (choice === "cancel") throw new Error("Generation cancelled before model execution.");
			if (choice === "bypass") headers["X-Knowledge-Bypass"] = "true";
			if (authRequired) {
				const token = await getCurrentAccessToken();
				if (!token) throw new Error(AUTH_REQUIRED_MESSAGE);
				headers.Authorization = `Bearer ${token}`;
			}
		}
	};
	const {
		summary: workspaceSummary,
		error: workspaceSummaryError,
		isLoading: isWorkspaceSummaryLoading,
		isRefreshing: isWorkspaceSummaryRefreshing,
		refresh: refreshWorkspaceSummary,
		retry: retryWorkspaceSummary,
		invalidateProject: invalidateWorkspaceProject,
	} = useWorkspaceSummary({
		request: apiRequest,
		enabled: isAuthenticated && !isVerifyingSession,
		identity: currentUser?.sub || "",
	});
	const previousWorkspaceRouteRef = useRef({ kind: route.kind, destination: route.destination });
	const routeFocusKey = PROJECT_WORKSPACES_V2
		? `${route.kind}:${route.projectId || ""}:${route.workspace || ""}:${route.section || route.destination || ""}`
		: `${route.kind}:${route.projectId || ""}:${route.destination || ""}`;
	const previousRouteFocusKeyRef = useRef(routeFocusKey);
	const pendingRouteFocusRef = useRef(false);
	const workspaceEntryPathRef = useRef(null);
	const navigateWorkspace = useCallback(
		(path, options) => {
			workspaceEntryPathRef.current = path === window.location.pathname ? null : path;
			navigate(path, options);
		},
		[navigate]
	);
	const focusPendingRoute = useCallback((replacingFocusedLoader = false) => {
		if (!replacingFocusedLoader && !pendingRouteFocusRef.current) return;
		const main = document.getElementById("main-content");
		if (!main || main.dataset.workspaceModulePending) return;
		const active = document.activeElement;
		const caseFocus = keepPriorityCaseFocus.current && active?.closest("#selected-test-case");
		const evidenceFocus =
			active?.matches("[id^='saved-case-step-'], #quality-feedback-heading, #saved-file-report-heading, [data-run-case-result]") &&
			main.contains(active);
		const refreshDialogFocus = document.activeElement?.closest(".scoped-scenario-refresh-dialog");
		if (!evidenceFocus && !refreshDialogFocus && (!caseFocus || !main.contains(caseFocus))) {
			if (document.activeElement === main) main.blur();
			if (workspaceEntryPathRef.current === window.location.pathname) {
				focusWorkspaceEntry(main);
			} else {
				main.focus();
			}
		}
		workspaceEntryPathRef.current = null;
		pendingRouteFocusRef.current = false;
	}, []);

	const storeExecutionPreview = (rawPreview, { source = "live", consistencyMessage = null, selectedCandidateIds = null } = {}) => {
		const normalizedPreview = normalizeAutomationPreview(rawPreview, { source });
		const nextPreview = consistencyMessage
			? {
					...normalizedPreview,
					requiresRefresh: true,
					consistencyMessage,
				}
			: normalizedPreview;
		setExecutionPreview(nextPreview);
		setSelectedExecutionCandidateIds(
			selectedCandidateIds === null
				? getDefaultSelectedCandidateIds(nextPreview)
				: resolveSelectedExecutableCandidates(nextPreview, selectedCandidateIds).map((candidate) => candidate.id)
		);
		return nextPreview;
	};

	useEffect(() => {
		const previousRoute = previousWorkspaceRouteRef.current;
		previousWorkspaceRouteRef.current = { kind: route.kind, destination: route.destination };
		const enteredWorkspaceFromProject =
			previousRoute.kind === "project" &&
			route.kind === "global" &&
			[
				GLOBAL_DESTINATIONS.HOME,
				GLOBAL_DESTINATIONS.PROJECTS,
				GLOBAL_DESTINATIONS.REVIEWS,
				GLOBAL_DESTINATIONS.RUNS,
				GLOBAL_DESTINATIONS.REPORTS,
			].includes(route.destination);
		if (enteredWorkspaceFromProject && isAuthenticated && !isVerifyingSession) {
			void refreshWorkspaceSummary();
		}
	}, [route.kind, route.destination, isAuthenticated, isVerifyingSession, refreshWorkspaceSummary]);

	useEffect(() => {
		if (isAuthenticated && !isVerifyingSession && route.kind === "project") projectRouteModules.get(route.destination)?.preload();
	}, [isAuthenticated, isVerifyingSession, route.kind, route.destination]);

	useEffect(() => {
		if (previousRouteFocusKeyRef.current !== routeFocusKey) {
			previousRouteFocusKeyRef.current = routeFocusKey;
			pendingRouteFocusRef.current = true;
		}
		if (!pendingRouteFocusRef.current) {
			workspaceEntryPathRef.current = null;
			return undefined;
		}
		if (route.kind === "project" && ["idle", "loading"].includes(projectRouteStatus)) {
			return undefined;
		}
		const focusFrame = window.requestAnimationFrame(() => focusPendingRoute());
		return () => window.cancelAnimationFrame(focusFrame);
	}, [focusPendingRoute, location.pathname, projectRouteStatus, route.kind, routeFocusKey]);

	const hydrateProjectWorkflow = (project) => {
		invalidateExecutionPreviewRequest();
		invalidateExecutionRunRequest();
		const snapshots = project?.current_snapshots || {};
		const requirementPayload = snapshots.requirements?.payload || null;
		const contextPayload = snapshots.context?.payload || null;
		const useCasePayload = snapshots.use_cases?.payload || null;
		const impactPayload = snapshots.impact_analysis?.payload || null;
		const testCasePayload = snapshots.test_cases?.payload || null;
		const executionSnapshot = snapshots.execution || null;
		const executionPayload = executionSnapshot?.payload || null;

		setFile(null);
		setReqFeedback("");
		setFeedback("");
		setRawText(requirementPayload?.raw_text || "");
		setRequirements((requirementPayload?.requirements || []).filter((r) => r.lifecycle_status !== "retired"));
		setRequirementReview(requirementPayload?.review || null);
		setRequirementCoverageMetrics(requirementPayload?.coverage_metrics || null);
		setRequirementWorkflowDiagnostics(requirementPayload?.workflow_diagnostics || null);
		setAppliedRequirementWorkflowSettings(requirementPayload?.workflow_settings || null);
		setRequirementIterationHistory(requirementPayload?.iteration_history || []);

		setAppLink(contextPayload?.app_link || "");
		setPrototypeLink(contextPayload?.prototype_link || "");
		setDiagramLinks((contextPayload?.diagram_links || []).join("; "));
		setImageLinks((contextPayload?.image_links || []).join("; "));
		setContextNotes(contextPayload?.notes ?? "");
		setContextAnalysisError("");
		if (contextPayload) {
			setEnrichedContext({
				requirements: contextPayload.requirements || requirementPayload?.requirements || [],
				app_link: contextPayload.app_link || null,
				prototype_link: contextPayload.prototype_link || null,
				diagram_links: contextPayload.diagram_links || [],
				image_links: contextPayload.image_links || [],
				notes: contextPayload.notes ?? null,
				execution_prerequisites: contextPayload.execution_prerequisites || [],
				grounded_context: contextPayload.grounded_context ?? null,
			});
			setSelectedArtifactSourceIds((contextPayload.grounded_context?.artifact_sources || []).map((source) => source.id));
		} else {
			resetContextAnalysis();
		}

		const generationPayload = testCasePayload || {};
		const useCases = useCasePayload || generationPayload;
		const hydratedTestCaseDiagnostics = generationPayload.workflow_diagnostics || useCases.workflow_diagnostics || null;
		const hydratedGenerationPayload = {
			...generationPayload,
			coverage_metrics: generationPayload.coverage_metrics || null,
			workflow_diagnostics: hydratedTestCaseDiagnostics,
		};
		setRequirementAnalysis(useCases.requirement_analysis || []);
		setCoveragePlan(useCases.coverage_plan || []);
		setCoverageMetrics(hydratedGenerationPayload.coverage_metrics);
		setTestCases(generationPayload.test_cases || []);
		setGenerationTasks(generationPayload.generation_tasks || []);
		setTestCaseReview(generationPayload.review || null);
		setSubstantiveAssessment(generationPayload.substantive_assessment || null);
		setTestCaseWorkflowDiagnostics(hydratedTestCaseDiagnostics);
		setAppliedTestCaseWorkflowSettings(generationPayload.workflow_settings || useCases.workflow_settings || null);
		setTestCaseIterationHistory(generationPayload.iteration_history || []);
		setImpactAnalysis(impactPayload || generationPayload.impact_analysis || null);
		const keepsPriorityCase =
			priorityCase && project?.project_id === currentProjectId && snapshots.test_cases?.snapshot_id === savedSuite?.snapshot_id;
		if (!keepsPriorityCase && !isTraceabilityReturn(project?.project_id, snapshots.test_cases?.snapshot_id)) {
			setActiveGenerateResultTab(chooseGenerateResultTab(hydratedGenerationPayload));
		}
		resetExportWorkflowState();
		setExportMessage("");
		setExportError("");

		setExecutionTargetBaseUrl(executionPayload?.target_base_url || "");
		setExecutionTargetEnvironment(executionPayload?.target_environment || "");
		if (executionPayload?.run_id) {
			setExecutionRunResult({
				status: executionPayload.status,
				run_id: executionPayload.run_id,
				source_snapshot_id: executionSnapshot.source_snapshot_id,
				target_environment: executionPayload.target_environment,
				target_base_url: executionPayload.target_base_url,
				artifacts_root: executionPayload.artifacts_root,
				playwright_report_paths: executionPayload.playwright_report_paths,
				results: executionPayload.results || [],
				warnings: executionPayload.warnings || [],
				issues: executionPayload.issues || [],
				summary: executionPayload.summary || {},
				preview: null,
			});
			setExecutionPreview(null);
			setSelectedExecutionCandidateIds([]);
		} else if (executionPayload) {
			const currentTestCaseSnapshotId = snapshots.test_cases?.snapshot_id || null;
			const previewSourceSnapshotId = executionSnapshot?.source_snapshot_id || null;
			const previewIsStale = Boolean(
				currentTestCaseSnapshotId && previewSourceSnapshotId && currentTestCaseSnapshotId !== previewSourceSnapshotId
			);
			storeExecutionPreview(executionPayload, {
				source: "persisted",
				consistencyMessage: previewIsStale
					? "This stored preview belongs to an older test-case snapshot. Preview execution again before running."
					: null,
			});
			setExecutionRunResult(null);
		} else {
			resetExecutionWorkflowState();
		}
	};

	const clearProjectWorkspace = ({ removeStoredProject = false } = {}) => {
		setCurrentProject(null);
		setOrchestratorStatus(null);
		setOrchestratorError("");
		setIsLoadingOrchestrator(false);
		setIsParsing(false);
		setIsAnalyzingContext(false);
		setIsGenerating(false);
		setIsPreviewingExecution(false);
		setIsRunningExecution(false);
		setIsAnalyzingImpact(false);
		setIsExporting(false);
		setIsImportingFromJira(false);
		setIsPreviewingJiraSync(false);
		setIsApplyingJiraSync(false);
		setIsImportingFromAzureDevOps(false);
		setIsPreviewingAzureDevOpsSync(false);
		setIsApplyingAzureDevOpsSync(false);
		resetIntegrationSyncState();
		hydrateProjectWorkflow(null);
		if (removeStoredProject) {
			localStorage.removeItem(STORAGE_CURRENT_PROJECT_ID);
		}
	};

	const loadProjectOrchestrator = async (projectId, { silent = false, routeRequestId = null, operationScope = null } = {}) => {
		const requestId = ++orchestratorRequestRef.current;
		const requestUserId = projectOperationUserRef.current;
		const requestIsCurrent = () =>
			requestId === orchestratorRequestRef.current &&
			requestUserId === projectOperationUserRef.current &&
			isProjectRequestCurrent(projectId, { routeRequestId, operationScope });
		if (!projectId || !isAuthenticated) {
			setOrchestratorStatus(null);
			setOrchestratorError("");
			return null;
		}
		if (!silent) {
			setIsLoadingOrchestrator(true);
		}
		try {
			const statusRes = await apiRequest(PROJECT_API_PATHS.orchestratorStatus(projectId), { method: "GET" });
			if (!statusRes.ok) {
				const errorMessage = await parseApiError(statusRes, "Failed to load orchestrator status");
				throw new Error(errorMessage);
			}
			const statusPayload = await statusRes.json();
			if (!requestIsCurrent() || statusPayload?.project_id !== projectId) {
				return null;
			}
			if (!isLatestProjectRevision(projectId, statusPayload.project_revision)) {
				throw new Error("Workflow guidance is out of date. Reload the latest project before continuing.");
			}
			recordProjectRevision(projectId, statusPayload.project_revision);
			setOrchestratorStatus(statusPayload || null);
			setOrchestratorError("");
			return { status: statusPayload || null };
		} catch (error) {
			if (!requestIsCurrent()) {
				return null;
			}
			setOrchestratorStatus(null);
			setOrchestratorError(error.message);
			if (!silent) {
				setStatus(`Orchestrator load failed: ${error.message}`);
			}
			return null;
		} finally {
			if (requestIsCurrent()) {
				setIsLoadingOrchestrator(false);
			}
		}
	};

	const refreshRequirementGuidance = async (project, operationScope) => {
		setOrchestratorStatus(null);
		invalidateWorkspaceProject(project.project_id, project.current_revision);
		const results = await Promise.all([loadProjectOrchestrator(project.project_id, { operationScope }), refreshWorkspaceSummary()]);
		if (isProjectOperationCurrent(operationScope) && results.some((result) => !result)) {
			setStatus("Requirements saved. Workflow guidance could not be refreshed; reload the latest project before continuing.");
		}
	};

	const loadProjects = async ({ silent = false, requestIsCurrent = null } = {}) => {
		const requestId = projectListRequestRef.current + 1;
		projectListRequestRef.current = requestId;
		const requestUserId = projectOperationUserRef.current;
		const canApplyResponse = () =>
			projectListRequestRef.current === requestId &&
			projectOperationUserRef.current === requestUserId &&
			(typeof requestIsCurrent !== "function" || requestIsCurrent());
		if (!isAuthenticated) {
			setProjects([]);
			setCurrentProject(null);
			setOrchestratorStatus(null);
			setOrchestratorError("");
			return [];
		}
		if (!silent) {
			setIsLoadingProjects(true);
		}
		try {
			const res = await apiRequest(PROJECT_API_PATHS.projects, { method: "GET" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to load projects");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			if (!canApplyResponse()) {
				return null;
			}
			const nextProjects = data.projects || [];
			setProjects(nextProjects);
			if (!silent) {
				setApplicationLiveStatus(`Projects refreshed: ${nextProjects.length} available.`);
			}
			return nextProjects;
		} catch (error) {
			if (!silent && canApplyResponse()) {
				const message = `Project load failed: ${error.message}`;
				setStatus(message);
				setApplicationLiveStatus(message);
			}
			return null;
		} finally {
			if (canApplyResponse()) {
				setIsLoadingProjects(false);
			}
		}
	};

	const openProject = async (
		projectId,
		{ hydrate = true, silent = false, routeRequestId = null, operationScope = null, loadOrchestrator = true } = {}
	) => {
		if (!projectId) {
			clearProjectWorkspace({ removeStoredProject: true });
			return null;
		}
		if (!silent) {
			setIsOpeningProject(true);
		}
		try {
			const res = await apiRequest(PROJECT_API_PATHS.project(projectId), { method: "GET" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to open project");
				const projectOpenError = new Error(errorMessage);
				projectOpenError.status = res.status;
				throw projectOpenError;
			}
			const data = await res.json();
			if (!isProjectRequestCurrent(projectId, { routeRequestId, operationScope })) {
				return null;
			}
			if (!data?.project_id || data.project_id !== projectId) {
				throw new Error("The project response did not match the requested project.");
			}
			if (!isLatestProjectRevision(projectId, data.current_revision)) {
				throw new Error("The project response is out of date. Reload the latest project before continuing.");
			}
			recordProjectRevision(projectId, data.current_revision);
			setCurrentProject(data || null);
			if (hydrate && data && (hydrate !== "if-changed" || data.current_revision !== currentProject?.current_revision)) {
				hydrateProjectWorkflow(data);
			}
			if (data?.project_id) {
				localStorage.setItem(STORAGE_CURRENT_PROJECT_ID, data.project_id);
				if (loadOrchestrator) {
					await loadProjectOrchestrator(data.project_id, { silent: true, routeRequestId, operationScope });
				}
				if (!isProjectRequestCurrent(projectId, { routeRequestId, operationScope })) {
					return null;
				}
			}
			if (routeRequestId !== null && projectRouteRequestRef.current === routeRequestId) {
				setProjectRouteStatus("ready");
				setProjectRouteError("");
			}
			if (!silent) {
				setStatus(data ? `Opened ${data.name}.` : "Project opened.");
			}
			return data;
		} catch (error) {
			if (!isProjectRequestCurrent(projectId, { routeRequestId, operationScope })) {
				return null;
			}
			if (routeRequestId !== null) {
				clearProjectWorkspace();
				setProjectRouteStatus(error.status === 404 ? "not-found" : error.status === 403 ? "forbidden" : "error");
				setProjectRouteError(error.message);
			}
			if (!silent) {
				setStatus(`Project open failed: ${error.message}`);
			}
			return null;
		} finally {
			if (!silent && isProjectRequestCurrent(projectId, { routeRequestId, operationScope })) {
				setIsOpeningProject(false);
			}
		}
	};

	const refreshCurrentProject = async ({ hydrate = false, operationScope = null } = {}) => {
		if (!operationScope?.projectId || !isProjectOperationCurrent(operationScope)) {
			return null;
		}
		return openProject(operationScope.projectId, { hydrate, silent: true, operationScope });
	};

	const repairReviewRoute = Boolean(
		APPROVAL_CONTINUATION_ENABLED &&
		route.workspace === "review" &&
		route.section === "tests" &&
		!viewingSavedCase &&
		route.projectId === currentProjectId &&
		currentUser?.sub &&
		currentProject?.owner_user_id === currentUser.sub &&
		isAuthenticated &&
		!isVerifyingSession
	);
	const repairSnapshotId =
		savedSuite?.project_id === currentProjectId && testCaseStageState.current_snapshot_id === savedSuite?.snapshot_id
			? savedSuite?.snapshot_id
			: null;
	const repairReaderCaseId =
		repairSnapshotId && selectedRepairReader?.source === sourceCaseSnapshot && selectedRepairReader?.identity === currentUser?.sub
			? selectedRepairReader.caseId
			: null;
	const repairReaderVersionId = repairReaderCaseId ? findActiveSavedCase(savedSuite, repairReaderCaseId)?.artifact_version_id : null;
	const selectedRepair = useSelectedCaseRepair({
		enabled: repairReviewRoute,
		identity: currentUser?.sub,
		projectId: currentProjectId,
		snapshotId: repairSnapshotId,
		caseId: repairReaderCaseId,
		caseVersionId: repairReaderVersionId,
		revision: currentProjectRevision,
		request: (path, options) => apiRequest(path, options, true, { allowGuidanceRecovery: false }),
		readProject: () => {
			const operationScope = captureProjectOperationScope();
			if (operationScope?.projectId !== currentProjectId || operationScope?.userId !== currentUser?.sub) return null;
			return refreshCurrentProject({ hydrate: "if-changed", operationScope });
		},
	});

	const canRecoverScenarioRefresh = Boolean(
		APPROVAL_CONTINUATION_ENABLED &&
		route.workspace === "review" &&
		["scope", "scenarios"].includes(route.section) &&
		route.projectId === currentProjectId &&
		currentUser?.sub &&
		currentProject?.owner_user_id === currentUser.sub &&
		isAuthenticated &&
		!isVerifyingSession
	);
	const savedScenarioId = projectSnapshots.use_cases?.snapshot_id;
	const scenarioRefreshPredecessor =
		canRecoverScenarioRefresh &&
		!materialSyncRebindRequired &&
		savedScenarioId &&
		useCasesStageState.current_snapshot_id === savedScenarioId &&
		useCasesStageState.stale &&
		useCasesStageState.approved &&
		latestUseCasesHumanReview?.snapshot_id === savedScenarioId &&
		latestUseCasesHumanReview.decision === "approve" &&
		savedSuite?.snapshot_id &&
		testCaseStageState.current_snapshot_id === savedSuite.snapshot_id &&
		testCaseStageState.stale
			? savedScenarioId
			: null;
	const scenarioRefresh = useScopedScenarioRefresh({
		enabled: canRecoverScenarioRefresh,
		identity: currentUser?.sub,
		project: currentProject,
		predecessorId: scenarioRefreshPredecessor,
		request: (path, options) => apiRequest(path, options, true, { allowGuidanceRecovery: false }),
		readProject: () => {
			const operationScope = captureProjectOperationScope();
			if (operationScope?.projectId !== currentProjectId || operationScope?.userId !== currentUser?.sub) return null;
			return refreshCurrentProject({ hydrate: "if-changed", operationScope });
		},
	});
	const savedPlanSnapshot = projectSnapshots.use_cases;
	const savedPlanSnapshotId =
		APPROVAL_CONTINUATION_ENABLED &&
		route.workspace === "review" &&
		route.projectId === currentProjectId &&
		currentProject?.status === "active" &&
		currentProject.owner_user_id === currentUser?.sub &&
		useCasesHumanApproved &&
		savedPlanSnapshot?.operation === "use_cases.generate" &&
		savedPlanSnapshot.snapshot_id === useCasesStageState.current_snapshot_id &&
		!savedPlanSnapshot.metadata?.use_case_refresh &&
		!savedSuite &&
		!testCaseStageState.current_snapshot_id
			? savedPlanSnapshot.snapshot_id
			: null;
	const approvalContinuation = useApprovalContinuation({
		enabled:
			APPROVAL_CONTINUATION_ENABLED &&
			route.workspace === "review" &&
			Boolean(currentUser?.sub) &&
			route.projectId === currentProjectId &&
			isAuthenticated &&
			!isVerifyingSession,
		projectId: currentProjectId,
		projectRevision: currentProjectRevision,
		identity: currentUser?.sub || "",
		request: (path, options) => apiRequest(path, options, true, { allowGuidanceRecovery: false }),
		savedPlanSnapshotId,
	});
	const savedReview = approvalContinuation.savedReview;
	const currentBudgetBlocker = approvalContinuation.currentBudgetBlocker;
	const budgetStopsStage = (stage, snapshotId) =>
		Boolean(
			snapshotId &&
			((currentBudgetBlocker?.stage === stage &&
				currentBudgetBlocker.source_snapshot_id === snapshotId &&
				currentBudgetBlocker.project_revision === currentProjectRevision) ||
				approvalContinuation.runs.some(
					(run) =>
						run.stage === stage &&
						run.source_snapshot_id === snapshotId &&
						run.state === "failed" &&
						["journey_run_budget_exhausted", "journey_run_budget_unavailable"].includes(run.reason)
				))
		);
	const savedPlanGeneration = approvalContinuation.savedPlanGeneration;
	const replacesLegacyGeneration = Boolean(
		savedPlanSnapshotId &&
		(!savedReview.statusRead || savedReview.enabled || approvalContinuation.available) &&
		!approvalContinuation.runs.some((run) => run.stage === "prepare_scenarios" && run.result_snapshot_id === savedPlanSnapshotId)
	);
	const useScopedScenarioRefreshFlow = Boolean(
		canRecoverScenarioRefresh &&
		(scenarioRefresh.pending ||
			scenarioRefresh.attempt ||
			scenarioRefresh.result ||
			scenarioRefresh.enabled ||
			(scenarioRefreshPredecessor && (savedReview.enabled || !scenarioRefresh.legacy)))
	);

	const trustedRepairOnly =
		repairReviewRoute && (savedReview.enabled || !selectedRepair.legacy || selectedRepair.attempt || selectedRepair.pending);
	const savedReviewRecovery =
		APPROVAL_CONTINUATION_ENABLED && currentProject?.owner_user_id === currentUser?.sub && (savedReview.enabled || !savedReview.statusRead);
	const sharedDraftBusy =
		savedReview.enabled &&
		(approvalContinuation.active || approvalContinuation.unresolved || approvalContinuation.run?.state === "interrupted");
	const savedSuiteReviewDisabled =
		savedSuiteReviewUnavailable ||
		(syncDraftSuite && !savedReview.enabled) ||
		(selectedRepair.enabled &&
			(selectedRepair.attempt ||
				selectedRepair.pending ||
				selectedRepair.checking ||
				selectedRepair.submitting ||
				selectedRepair.blocked)) ||
		(savedReviewRecovery &&
			(!savedReview.statusRead ||
				savedReview.statusError ||
				savedReview.error ||
				savedReview.busy ||
				savedReview.attempt ||
				sharedDraftBusy));
	const checkSavedSuiteReview = (operationScope, { release = false } = {}) => {
		if (!operationScope || !isProjectOperationCurrent(operationScope)) return;
		const readProject = () => refreshCurrentProject({ hydrate: true, operationScope });
		return release ? savedReview.release(readProject) : savedReview.check(readProject);
	};
	const continuationReadRevision = useRef(null);
	const continuationRevision = approvalContinuation.run?.project_revision;
	const continuationReceiptId = approvalContinuation.receipt?.request_id;
	useEffect(() => {
		if (!continuationReceiptId && (!Number.isInteger(continuationRevision) || continuationRevision <= currentProjectRevision)) return;
		const operationScope = captureProjectOperationScope();
		if (!operationScope) return;
		const readKey = JSON.stringify([operationScope, approvalContinuation.run?.run_id, continuationRevision, continuationReceiptId]);
		if (continuationReadRevision.current === readKey) return;
		continuationReadRevision.current = readKey;
		void refreshCurrentProject({ hydrate: true, operationScope });
	}, [approvalContinuation.run?.run_id, continuationRevision, continuationReceiptId, currentProjectRevision]);

	const refreshUseCaseReviewProject = async ({ projectId, orchestratorStatus: committedStatus = null } = {}) => {
		const operationScope = captureProjectOperationScope();
		if (!operationScope || operationScope.projectId !== projectId || !isProjectOperationCurrent(operationScope)) {
			return null;
		}
		if (committedStatus && !isLatestProjectRevision(projectId, committedStatus.project_revision)) committedStatus = null;
		if (committedStatus) {
			recordProjectRevision(projectId, committedStatus.project_revision);
			invalidateWorkspaceProject(projectId, committedStatus.project_revision);
			setOrchestratorStatus(committedStatus);
		}
		const requestIsCurrent = () => isProjectOperationCurrent(operationScope);
		const [project, orchestrator, projectList, workspaceSummary] = await Promise.all([
			openProject(projectId, { hydrate: false, silent: true, operationScope, loadOrchestrator: false }),
			loadProjectOrchestrator(projectId, { silent: true, operationScope }),
			loadProjects({ silent: true, requestIsCurrent }),
			refreshWorkspaceSummary(),
		]);
		if (!requestIsCurrent()) {
			return null;
		}
		const failedRefreshes = [
			!project ? "project" : null,
			!orchestrator ? "orchestrator" : null,
			projectList === null ? "project list" : null,
			!workspaceSummary ? "Home and review inbox" : null,
		].filter(Boolean);
		if (failedRefreshes.length) {
			if (committedStatus && isLatestProjectRevision(projectId, committedStatus.project_revision)) {
				setOrchestratorStatus(committedStatus);
			}
			const prefix = committedStatus ? "Decision saved, but" : "Reload incomplete:";
			throw new Error(`${prefix} ${failedRefreshes.join(", ")} state could not be refreshed. Reload latest before another decision.`);
		}
		return project;
	};

	const handleUseCasesGenerated = (project, context) => {
		const operationScope = captureProjectOperationScope();
		if (
			!operationScope ||
			operationScope.projectId !== context.projectId ||
			currentProjectRevision !== context.baseProjectRevision ||
			!isLatestProjectRevision(project.project_id, project.current_revision)
		)
			return;
		recordProjectRevision(project.project_id, project.current_revision);
		invalidateWorkspaceProject(project.project_id, project.current_revision);
		setCurrentProject(project);
		hydrateProjectWorkflow(project);
		void loadProjectOrchestrator(project.project_id, { silent: true, operationScope });
		void refreshWorkspaceSummary();
	};

	const handleUseCaseReviewCommitted = async (response, context) =>
		refreshUseCaseReviewProject({ projectId: context?.projectId, orchestratorStatus: response?.orchestrator_status || null });

	const reloadLatestUseCases = async (projectId) => refreshUseCaseReviewProject({ projectId });

	const createProject = async (nameOverride = null, { throwOnError = false } = {}) => {
		const name = `${nameOverride ?? newProjectName}`.trim();
		if (!name) {
			const error = new Error("Enter a project name before creating a project.");
			if (throwOnError) {
				throw error;
			}
			return null;
		}
		const requestId = projectCreateRequestRef.current + 1;
		projectCreateRequestRef.current = requestId;
		const requestScope = {
			requestId,
			userId: projectOperationUserRef.current,
			routeRequestId: projectRouteRequestRef.current,
			pathname: typeof window === "undefined" ? route.pathname : window.location.pathname,
		};
		const isCreateMutationCurrent = () =>
			projectCreateRequestRef.current === requestScope.requestId && projectOperationUserRef.current === requestScope.userId;
		const isCreateRequestCurrent = () =>
			isCreateMutationCurrent() &&
			projectRouteRequestRef.current === requestScope.routeRequestId &&
			(typeof window === "undefined" ? route.pathname : window.location.pathname) === requestScope.pathname;
		const assertCreateMutationCurrent = () => {
			if (!isCreateMutationCurrent()) {
				throw createCancelledOperationError();
			}
		};
		const assertCreateRequestCurrent = () => {
			if (!isCreateRequestCurrent()) {
				throw createCancelledOperationError();
			}
		};
		setIsCreatingProject(true);
		setStatus("Creating QA project...");
		try {
			const res = await apiRequest(PROJECT_API_PATHS.projects, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ name }),
			});
			assertCreateMutationCurrent();
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to create project");
				assertCreateMutationCurrent();
				throw new Error(errorMessage);
			}
			const data = await res.json();
			assertCreateMutationCurrent();
			if (!data?.project_id) {
				throw new Error("The created project response did not include a project ID.");
			}
			await loadProjects({ silent: true, requestIsCurrent: isCreateMutationCurrent });
			assertCreateMutationCurrent();
			await refreshWorkspaceSummary();
			assertCreateMutationCurrent();
			assertCreateRequestCurrent();
			setNewProjectName("");
			setStatus(`Created ${data.name}.`);
			return data;
		} catch (error) {
			if (error?.name === "AbortError" || !isCreateRequestCurrent()) {
				const cancelledError = error?.name === "AbortError" ? error : createCancelledOperationError();
				if (throwOnError) {
					throw cancelledError;
				}
				return null;
			}
			setStatus(`Project create failed: ${error.message}`);
			if (throwOnError) {
				throw error;
			}
			return null;
		} finally {
			if (projectCreateRequestRef.current === requestScope.requestId) {
				setIsCreatingProject(false);
			}
		}
	};

	const handleProjectSelection = async (projectId) => {
		if (!projectId) {
			projectRouteRequestRef.current += 1;
			clearProjectWorkspace({ removeStoredProject: true });
			setProjectRouteStatus("idle");
			setProjectRouteError("");
			navigate("/");
			return null;
		}

		navigate(buildProjectPath(projectId));
		return projects.find((project) => project.project_id === projectId) || { project_id: projectId };
	};

	const createProjectAndNavigate = async (nameOverride = null, options = {}) => {
		const createdProject = await createProject(nameOverride, options);
		if (createdProject?.project_id) {
			navigate(
				PROJECT_WORKSPACES_V2 ? buildProjectWorkspacePath(createdProject.project_id, "inputs") : buildProjectPath(createdProject.project_id)
			);
		}
		return createdProject;
	};

	const createWorkspaceProject = (name) => createProject(name, { throwOnError: true });

	const openWorkspaceProject = ({ projectId, destination, path, created = false } = {}) => {
		if (!projectId) {
			return null;
		}
		navigate(
			PROJECT_WORKSPACES_V2 && created ? buildProjectWorkspacePath(projectId, "inputs") : path || buildProjectPath(projectId, destination)
		);
		return projects.find((project) => project.project_id === projectId) || { project_id: projectId };
	};

	useEffect(() => {
		if (!isAuthenticated) {
			setProjects([]);
			setCurrentProject(null);
			setOrchestratorStatus(null);
			setOrchestratorError("");
			return;
		}
		const requestUserId = currentUser?.sub || "";
		const requestIsCurrent = () => projectOperationUserRef.current === requestUserId;
		loadProjects({ silent: true, requestIsCurrent }).then((nextProjects) => {
			if (!requestIsCurrent() || !Array.isArray(nextProjects)) {
				return;
			}
			const storedProjectId = localStorage.getItem(STORAGE_CURRENT_PROJECT_ID);
			if (storedProjectId && !nextProjects.some((project) => project.project_id === storedProjectId)) {
				localStorage.removeItem(STORAGE_CURRENT_PROJECT_ID);
			}
		});
	}, [isAuthenticated, currentUser?.sub]);

	useEffect(() => {
		const routeRequestId = projectRouteRequestRef.current + 1;
		projectRouteRequestRef.current = routeRequestId;

		if (route.kind !== "project") {
			setProjectRouteStatus("idle");
			setProjectRouteError("");
			if (currentProject) {
				clearProjectWorkspace();
			}
			return;
		}

		const routeTab = getLegacyTabForDestination(route.destination);
		setActiveTab(routeTab);

		if (isVerifyingSession) {
			setProjectRouteStatus("loading");
			setProjectRouteError("");
			return;
		}

		if (!isAuthenticated) {
			clearProjectWorkspace();
			setProjectRouteStatus("auth-required");
			setProjectRouteError("Sign in to open this project.");
			return;
		}

		if (currentProject?.project_id === route.projectId) {
			localStorage.setItem(STORAGE_CURRENT_PROJECT_ID, route.projectId);
			setProjectRouteStatus("ready");
			setProjectRouteError("");
			void openProject(route.projectId, { hydrate: "if-changed", silent: true, routeRequestId });
			return;
		}

		clearProjectWorkspace();
		setProjectRouteStatus("loading");
		setProjectRouteError("");
		void openProject(route.projectId, { hydrate: true, silent: true, routeRequestId });
	}, [route.kind, route.projectId, route.destination, viewingSavedCase, isAuthenticated, isVerifyingSession, currentUser?.sub]);

	const refreshJiraConnectionStatus = async (userOverride = currentUser, { silent = false } = {}) => {
		const user = userOverride || currentUser;
		if (!user) {
			setJiraConnectionStatus(EMPTY_JIRA_CONNECTION_STATUS);
			setJiraConnectionForm(EMPTY_JIRA_CONNECTION_FORM);
			return null;
		}

		if (!silent) {
			setIsJiraConnectionLoading(true);
		}
		try {
			const res = await apiRequest("/integrations/jira/connection", { method: "GET" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to load JIRA connection");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setJiraConnectionStatus(data || EMPTY_JIRA_CONNECTION_STATUS);
			setJiraConnectionForm(buildJiraConnectionForm(data?.connection, user));
			return data;
		} catch (error) {
			if (!silent) {
				setStatus(`JIRA connection check failed: ${error.message}`);
			}
			setJiraConnectionStatus(EMPTY_JIRA_CONNECTION_STATUS);
			setJiraConnectionForm(buildJiraConnectionForm(null, user));
			setJiraProjectIssueTypes([]);
			setJiraIssueType("");
			return null;
		} finally {
			if (!silent) {
				setIsJiraConnectionLoading(false);
			}
		}
	};

	const refreshAzureDevOpsConnectionStatus = async (userOverride = currentUser, { silent = false } = {}) => {
		const user = userOverride || currentUser;
		if (!user) {
			setAzureDevOpsConnectionStatus(EMPTY_AZURE_DEVOPS_CONNECTION_STATUS);
			setAzureDevOpsConnectionForm(EMPTY_AZURE_DEVOPS_CONNECTION_FORM);
			return null;
		}

		if (!silent) {
			setIsAzureDevOpsConnectionLoading(true);
		}
		try {
			const res = await apiRequest("/integrations/azure-devops/connection", { method: "GET" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to load Azure DevOps connection");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setAzureDevOpsConnectionStatus(data || EMPTY_AZURE_DEVOPS_CONNECTION_STATUS);
			setAzureDevOpsConnectionForm(buildAzureDevOpsConnectionForm(data?.connection, user));
			if (data?.connection?.default_project) {
				setSelectedAzureDevOpsProject((prev) => prev || data.connection.default_project);
			}
			return data;
		} catch (error) {
			if (!silent) {
				setStatus(`Azure DevOps connection check failed: ${error.message}`);
			}
			setAzureDevOpsConnectionStatus(EMPTY_AZURE_DEVOPS_CONNECTION_STATUS);
			setAzureDevOpsConnectionForm(buildAzureDevOpsConnectionForm(null, user));
			setAzureDevOpsWorkItemTypes([]);
			setAzureDevOpsWorkItemType("");
			return null;
		} finally {
			if (!silent) {
				setIsAzureDevOpsConnectionLoading(false);
			}
		}
	};

	const loadJiraProjectIssueTypes = async (projectKeyOverride = selectedJiraProjectKey, { silent = false } = {}) => {
		const normalizedProjectKey = `${projectKeyOverride || ""}`.trim();
		if (!jiraConnected || !normalizedProjectKey) {
			setJiraProjectIssueTypes([]);
			setJiraIssueType("");
			return [];
		}
		if (!silent) {
			setIsLoadingJiraIssueTypes(true);
		}
		try {
			const res = await apiRequest(`/integrations/jira/projects/${encodeURIComponent(normalizedProjectKey)}/issue-types`, {
				method: "GET",
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to load JIRA issue types");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			const issueTypes = Array.isArray(data?.issue_types) ? data.issue_types : [];
			setJiraProjectIssueTypes(issueTypes);
			setJiraIssueType((prev) => (prev && issueTypes.some((issueType) => issueType.name === prev) ? prev : ""));
			if (!silent && issueTypes.length) {
				setStatus(`Loaded ${issueTypes.length} issue type${issueTypes.length === 1 ? "" : "s"} for ${normalizedProjectKey}.`);
			}
			return issueTypes;
		} catch (error) {
			setJiraProjectIssueTypes([]);
			setJiraIssueType("");
			if (!silent) {
				setStatus(`JIRA issue type load failed: ${error.message}`);
			}
			return [];
		} finally {
			if (!silent) {
				setIsLoadingJiraIssueTypes(false);
			}
		}
	};

	const loadAzureDevOpsWorkItemTypes = async (projectOverride = selectedAzureDevOpsProject, { silent = false } = {}) => {
		const normalizedProject = `${projectOverride || ""}`.trim();
		if (!azureDevOpsConnected || !normalizedProject) {
			setAzureDevOpsWorkItemTypes([]);
			setAzureDevOpsWorkItemType("");
			return [];
		}
		if (!silent) {
			setIsLoadingAzureDevOpsWorkItemTypes(true);
		}
		try {
			const res = await apiRequest(`/integrations/azure-devops/projects/${encodeURIComponent(normalizedProject)}/work-item-types`, {
				method: "GET",
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to load Azure DevOps work item types");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			const workItemTypes = Array.isArray(data?.work_item_types) ? data.work_item_types : [];
			setAzureDevOpsWorkItemTypes(workItemTypes);
			setAzureDevOpsWorkItemType((prev) => (prev && workItemTypes.some((workItemType) => workItemType.name === prev) ? prev : ""));
			if (!silent && workItemTypes.length) {
				setStatus(
					`Loaded ${workItemTypes.length} Azure DevOps work item type${workItemTypes.length === 1 ? "" : "s"} for ${normalizedProject}.`
				);
			}
			return workItemTypes;
		} catch (error) {
			setAzureDevOpsWorkItemTypes([]);
			setAzureDevOpsWorkItemType("");
			if (!silent) {
				setStatus(`Azure DevOps work item type load failed: ${error.message}`);
			}
			return [];
		} finally {
			if (!silent) {
				setIsLoadingAzureDevOpsWorkItemTypes(false);
			}
		}
	};

	useEffect(() => {
		if (!isAuthenticated || !currentUser) {
			return;
		}
		void Promise.all([
			refreshJiraConnectionStatus(currentUser, { silent: true }),
			refreshAzureDevOpsConnectionStatus(currentUser, { silent: true }),
		]);
	}, [isAuthenticated, currentUser?.sub]);

	const loadJiraProjects = async (queryOverride = jiraProjectQuery, { silent = false, assumeConnected = false } = {}) => {
		if (!(jiraConnected || assumeConnected)) {
			if (!silent) {
				setStatus("Connect JIRA before loading projects.");
			}
			return [];
		}
		if (!silent) {
			setIsLoadingJiraProjects(true);
		}
		try {
			const params = new URLSearchParams();
			if (`${queryOverride || ""}`.trim()) {
				params.set("query", `${queryOverride}`.trim());
			}
			params.set("max_results", "50");
			const res = await apiRequest(`/integrations/jira/projects?${params.toString()}`, { method: "GET" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to load JIRA projects");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			const projects = Array.isArray(data?.projects) ? data.projects : [];
			setJiraProjects(projects);
			setSelectedJiraProjectKey((prev) => {
				if (prev && projects.some((project) => project.key === prev)) {
					return prev;
				}
				return projects[0]?.key || "";
			});
			if (!silent) {
				setStatus(
					projects.length
						? `Loaded ${projects.length} JIRA project${projects.length === 1 ? "" : "s"}.`
						: "JIRA returned no browseable projects for this account. If you expected to see a project like TheONE, check that this user has Browse Projects access."
				);
			}
			return projects;
		} catch (error) {
			if (!silent) {
				setStatus(`JIRA project load failed: ${error.message}`);
			}
			return [];
		} finally {
			if (!silent) {
				setIsLoadingJiraProjects(false);
			}
		}
	};

	const loadAzureDevOpsProjects = async (queryOverride = azureDevOpsProjectQuery, { silent = false, assumeConnected = false } = {}) => {
		if (!(azureDevOpsConnected || assumeConnected)) {
			if (!silent) {
				setStatus("Connect Azure DevOps before loading projects.");
			}
			return [];
		}
		if (!silent) {
			setIsLoadingAzureDevOpsProjects(true);
		}
		try {
			const params = new URLSearchParams();
			if (`${queryOverride || ""}`.trim()) {
				params.set("query", `${queryOverride}`.trim());
			}
			params.set("max_results", "50");
			const res = await apiRequest(`/integrations/azure-devops/projects?${params.toString()}`, { method: "GET" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to load Azure DevOps projects");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			const projects = Array.isArray(data?.projects) ? data.projects : [];
			setAzureDevOpsProjects(projects);
			setSelectedAzureDevOpsProject((prev) => {
				if (prev && projects.some((project) => project.name === prev)) {
					return prev;
				}
				if (azureDevOpsConnection?.default_project && projects.some((project) => project.name === azureDevOpsConnection.default_project)) {
					return azureDevOpsConnection.default_project;
				}
				return projects[0]?.name || prev || "";
			});
			if (!silent) {
				setStatus(
					projects.length
						? `Loaded ${projects.length} Azure DevOps project${projects.length === 1 ? "" : "s"}.`
						: "Azure DevOps returned no visible projects for this account."
				);
			}
			return projects;
		} catch (error) {
			if (!silent) {
				setStatus(`Azure DevOps project load failed: ${error.message}`);
			}
			return [];
		} finally {
			if (!silent) {
				setIsLoadingAzureDevOpsProjects(false);
			}
		}
	};

	useEffect(() => {
		if (!jiraConnected || jiraProjects.length > 0 || isLoadingJiraProjects) {
			return;
		}
		void loadJiraProjects("", { silent: true, assumeConnected: true });
	}, [jiraConnected, jiraProjects.length, isLoadingJiraProjects]);

	useEffect(() => {
		if (!azureDevOpsConnected || azureDevOpsProjects.length > 0 || isLoadingAzureDevOpsProjects) {
			return;
		}
		void loadAzureDevOpsProjects("", { silent: true, assumeConnected: true });
	}, [azureDevOpsConnected, azureDevOpsProjects.length, isLoadingAzureDevOpsProjects]);

	useEffect(() => {
		setJiraIssueResults([]);
		setSelectedJiraIssueKey("");
		if (!jiraConnected || !selectedJiraProjectKey) {
			setJiraProjectIssueTypes([]);
			setJiraIssueType("");
			return;
		}
		void loadJiraProjectIssueTypes(selectedJiraProjectKey, { silent: true });
	}, [jiraConnected, selectedJiraProjectKey]);

	useEffect(() => {
		setAzureDevOpsWorkItemResults([]);
		setSelectedAzureDevOpsWorkItemId("");
		if (!azureDevOpsConnected || !selectedAzureDevOpsProject) {
			setAzureDevOpsWorkItemTypes([]);
			setAzureDevOpsWorkItemType("");
			return;
		}
		void loadAzureDevOpsWorkItemTypes(selectedAzureDevOpsProject, { silent: true });
	}, [azureDevOpsConnected, selectedAzureDevOpsProject]);

	const saveJiraConnection = async () => {
		if (!jiraConnectionForm.baseUrl.trim() || !jiraConnectionForm.email.trim() || !jiraConnectionForm.apiToken.trim()) {
			setStatus("Enter your JIRA Cloud URL, email, and API token before connecting.");
			return;
		}
		setIsSavingJiraConnection(true);
		setStatus("Connecting to JIRA Cloud...");
		try {
			const res = await apiRequest("/integrations/jira/connection", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					base_url: jiraConnectionForm.baseUrl.trim(),
					email: jiraConnectionForm.email.trim(),
					api_token: jiraConnectionForm.apiToken,
				}),
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to connect to JIRA");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setJiraConnectionStatus(data || EMPTY_JIRA_CONNECTION_STATUS);
			setJiraConnectionForm(buildJiraConnectionForm(data?.connection, currentUser));
			setJiraProjects([]);
			setJiraProjectIssueTypes([]);
			setJiraIssueType("");
			setJiraIssueResults([]);
			setSelectedJiraIssueKey("");
			const connectedAs = data?.connection?.display_name || data?.connection?.email || jiraConnectionForm.email.trim();
			setStatus(`Connected to JIRA Cloud as ${connectedAs}. Loading projects...`);
			const projects = await loadJiraProjects("", { silent: true, assumeConnected: true });
			setStatus(
				projects.length
					? `Connected to JIRA Cloud as ${connectedAs}. Loaded ${projects.length} JIRA project${projects.length === 1 ? "" : "s"}.`
					: `Connected to JIRA Cloud as ${connectedAs}, but JIRA returned no browseable projects. If you expected to see a project like TheONE, check that this user has Browse Projects access.`
			);
		} catch (error) {
			setStatus(`JIRA connection failed: ${error.message}`);
		} finally {
			setIsSavingJiraConnection(false);
		}
	};

	const deleteStoredJiraConnection = async () => {
		setIsDeletingJiraConnection(true);
		setStatus("Disconnecting JIRA Cloud...");
		try {
			const res = await apiRequest("/integrations/jira/connection", { method: "DELETE" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to delete JIRA connection");
				throw new Error(errorMessage);
			}
			setJiraConnectionStatus(EMPTY_JIRA_CONNECTION_STATUS);
			setJiraConnectionForm(buildJiraConnectionForm(null, currentUser));
			setJiraProjects([]);
			setSelectedJiraProjectKey("");
			setJiraProjectIssueTypes([]);
			setJiraIssueType("");
			setJiraIssueResults([]);
			setSelectedJiraIssueKey("");
			setStatus("Disconnected from JIRA Cloud.");
		} catch (error) {
			setStatus(`JIRA disconnect failed: ${error.message}`);
		} finally {
			setIsDeletingJiraConnection(false);
		}
	};

	const saveAzureDevOpsConnection = async () => {
		if (!azureDevOpsConnectionForm.organizationUrl.trim() || !azureDevOpsConnectionForm.personalAccessToken.trim()) {
			setStatus("Enter your Azure DevOps organization/project URL and PAT before connecting.");
			return;
		}
		setIsSavingAzureDevOpsConnection(true);
		setStatus("Connecting to Azure DevOps...");
		try {
			const res = await apiRequest("/integrations/azure-devops/connection", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					organization_url: azureDevOpsConnectionForm.organizationUrl.trim(),
					personal_access_token: azureDevOpsConnectionForm.personalAccessToken,
					account_email: azureDevOpsConnectionForm.accountEmail.trim() || currentUser?.email || null,
				}),
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to connect to Azure DevOps");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setAzureDevOpsConnectionStatus(data || EMPTY_AZURE_DEVOPS_CONNECTION_STATUS);
			setAzureDevOpsConnectionForm(buildAzureDevOpsConnectionForm(data?.connection, currentUser));
			setAzureDevOpsProjects([]);
			setAzureDevOpsWorkItemTypes([]);
			setAzureDevOpsWorkItemType("");
			setAzureDevOpsWorkItemResults([]);
			setSelectedAzureDevOpsWorkItemId("");
			setSelectedAzureDevOpsProject(data?.connection?.default_project || "");
			const connectedAs =
				data?.connection?.display_name || data?.connection?.account_email || data?.connection?.organization || "Azure DevOps";
			setStatus(`Connected to Azure DevOps as ${connectedAs}. Loading projects...`);
			const projects = await loadAzureDevOpsProjects("", { silent: true, assumeConnected: true });
			setStatus(
				projects.length
					? `Connected to Azure DevOps as ${connectedAs}. Loaded ${projects.length} project${projects.length === 1 ? "" : "s"}.`
					: `Connected to Azure DevOps as ${connectedAs}, but no visible projects were returned.`
			);
		} catch (error) {
			setStatus(`Azure DevOps connection failed: ${error.message}`);
		} finally {
			setIsSavingAzureDevOpsConnection(false);
		}
	};

	const deleteStoredAzureDevOpsConnection = async () => {
		setIsDeletingAzureDevOpsConnection(true);
		setStatus("Disconnecting Azure DevOps...");
		try {
			const res = await apiRequest("/integrations/azure-devops/connection", { method: "DELETE" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to delete Azure DevOps connection");
				throw new Error(errorMessage);
			}
			setAzureDevOpsConnectionStatus(EMPTY_AZURE_DEVOPS_CONNECTION_STATUS);
			setAzureDevOpsConnectionForm(buildAzureDevOpsConnectionForm(null, currentUser));
			setAzureDevOpsProjects([]);
			setSelectedAzureDevOpsProject("");
			setAzureDevOpsWorkItemTypes([]);
			setAzureDevOpsWorkItemType("");
			setAzureDevOpsWorkItemResults([]);
			setSelectedAzureDevOpsWorkItemId("");
			setStatus("Disconnected from Azure DevOps.");
		} catch (error) {
			setStatus(`Azure DevOps disconnect failed: ${error.message}`);
		} finally {
			setIsDeletingAzureDevOpsConnection(false);
		}
	};

	const searchJiraIssues = async () => {
		if (!selectedJiraProjectKey) {
			setStatus("Choose a JIRA project before searching issues.");
			return;
		}
		setIsSearchingJiraIssues(true);
		const issueTypeLabel = jiraIssueType ? jiraIssueType.toLowerCase() : "issues";
		setStatus(`Searching ${issueTypeLabel} in JIRA...`);
		try {
			const params = new URLSearchParams({
				project_key: selectedJiraProjectKey,
				max_results: "20",
			});
			if (`${jiraIssueType || ""}`.trim()) {
				params.set("issue_type", `${jiraIssueType}`.trim());
			}
			if (`${jiraIssueQuery || ""}`.trim()) {
				params.set("query", `${jiraIssueQuery}`.trim());
			}
			const res = await apiRequest(`/integrations/jira/issues/search?${params.toString()}`, { method: "GET" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to search JIRA issues");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			const issues = Array.isArray(data?.issues) ? data.issues : [];
			setJiraIssueResults(issues);
			setSelectedJiraIssueKey((prev) => (issues.some((issue) => issue.key === prev) ? prev : issues[0]?.key || ""));
			setStatus(
				issues.length
					? `Found ${issues.length} JIRA ${issueTypeLabel}${issues.length === 1 || issueTypeLabel.endsWith("s") ? "" : "s"}.`
					: `No ${issueTypeLabel} matched that search.`
			);
		} catch (error) {
			setStatus(`JIRA issue search failed: ${error.message}`);
		} finally {
			setIsSearchingJiraIssues(false);
		}
	};

	const searchAzureDevOpsWorkItems = async () => {
		if (!selectedAzureDevOpsProject) {
			setStatus("Choose an Azure DevOps project before searching work items.");
			return;
		}
		setIsSearchingAzureDevOpsWorkItems(true);
		const workItemTypeLabel = azureDevOpsWorkItemType ? azureDevOpsWorkItemType.toLowerCase() : "work items";
		setStatus(`Searching ${workItemTypeLabel} in Azure DevOps...`);
		try {
			const params = new URLSearchParams({
				project: selectedAzureDevOpsProject,
				max_results: "20",
			});
			if (`${azureDevOpsWorkItemType || ""}`.trim()) {
				params.set("work_item_type", `${azureDevOpsWorkItemType}`.trim());
			}
			if (`${azureDevOpsWorkItemQuery || ""}`.trim()) {
				params.set("query", `${azureDevOpsWorkItemQuery}`.trim());
			}
			const res = await apiRequest(`/integrations/azure-devops/work-items/search?${params.toString()}`, { method: "GET" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to search Azure DevOps work items");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			const workItems = Array.isArray(data?.work_items) ? data.work_items : [];
			setAzureDevOpsWorkItemResults(workItems);
			setSelectedAzureDevOpsWorkItemId((prev) =>
				workItems.some((workItem) => `${workItem.work_item_id}` === `${prev}`)
					? prev
					: workItems[0]?.work_item_id
						? `${workItems[0].work_item_id}`
						: ""
			);
			setStatus(
				workItems.length
					? `Found ${workItems.length} Azure DevOps ${workItemTypeLabel}${workItems.length === 1 || workItemTypeLabel.endsWith("s") ? "" : "s"}.`
					: `No ${workItemTypeLabel} matched that search.`
			);
		} catch (error) {
			setStatus(`Azure DevOps work item search failed: ${error.message}`);
		} finally {
			setIsSearchingAzureDevOpsWorkItems(false);
		}
	};

	useEffect(() => {
		requirementReviewLock.current = false;
		requirementReviewAttempt.current = null;
		setIsSavingRequirementReview(false);
	}, [currentProjectId, isAuthenticated]);
	const imports = useRequirementImports(
		apiRequest,
		currentProjectId,
		isAuthenticated && !isVerifyingSession && (route.kind !== "project" || route.projectId === currentProjectId),
		async (project, didApply = true) => {
			const operationScope = captureProjectOperationScope();
			if (
				project.project_id !== currentProjectId ||
				operationScope?.projectId !== project.project_id ||
				!isLatestProjectRevision(project.project_id, project.current_revision)
			)
				return;
			recordProjectRevision(project.project_id, project.current_revision);
			setCurrentProject(project);
			hydrateProjectWorkflow(project);
			rememberGeneration("requirements", project.current_snapshots?.requirements?.payload || {});
			setStatus(
				didApply
					? "Reviewed requirements applied. Existing downstream artifacts are retained; review their impact before regenerating."
					: "Comparison refreshed against the current project. Review the new decisions before applying."
			);
			await refreshRequirementGuidance(project, operationScope);
		},
		currentUser?.sub
	);
	const requirementExtraction = useRequirementExtractionStatus({
		enabled: Boolean(
			DRAFT_ADMISSION_ENABLED &&
			route.destination === PROJECT_DESTINATIONS.REQUIREMENTS &&
			route.projectId === currentProjectId &&
			isAuthenticated &&
			!isVerifyingSession &&
			currentUser?.sub &&
			currentProject?.owner_user_id === currentUser.sub
		),
		projectId: currentProjectId,
		identity: currentUser?.sub,
		request: (path, options) => apiRequest(path, options, true, { allowGuidanceRecovery: false }),
		loadPending: imports.load,
		onReleased: (released) => {
			const path = REQUIREMENT_INTAKE_PATHS[released.operation];
			const attempts = requirementExtractionAttempts.current;
			const local = attempts.get(path);
			if (local?.requestId === released.request_id) {
				local.released = true;
				attempts.delete(path);
				local.setBusy(false);
			}
			imports.setExtractionError("");
		},
	});

	const intakeInterrupted = requirementExtraction.attempt?.state === "interrupted";
	const showParsingProgress = isParsing && !intakeInterrupted;
	const submittingRequirementPreview = isParsing || isImportingFromJira || isImportingFromAzureDevOps;
	const requirementIntakeBlocked = requirementExtraction.blocked || (requirementExtraction.managed && submittingRequirementPreview);

	const submitRequirementPreview = async ({ path, body, headers = {}, input, upload = null, operationScope, setBusy, pendingMessage }) => {
		const managed = requirementExtraction.managed;
		const fingerprint = JSON.stringify([operationScope, input]);
		const attempts = requirementExtractionAttempts.current;
		const prior = attempts.get(path);
		if (
			managed &&
			(requirementExtraction.blocked ||
				[...attempts.values()].some((entry) => entry.inFlight && entry.scope.generation === operationScope.generation))
		)
			return;
		const attempt =
			prior?.fingerprint === fingerprint && prior.file === upload
				? prior
				: { fingerprint, file: upload, requestId: createRequestId(), scope: operationScope, setBusy };
		attempt.inFlight = true;
		attempts.set(path, attempt);
		const isCurrent = () => isProjectOperationCurrent(operationScope) && !attempt.released;
		let clearAttempt = false;
		let checkPending = false;
		setBusy(true);
		imports.setExtractionError("");
		setStatus(pendingMessage);
		try {
			const options = { method: "POST", headers: { ...headers, "X-Request-ID": attempt.requestId }, body };
			const send = async () => {
				const res = await apiRequest(path, options);
				return { res, data: res.ok ? await res.json() : null };
			};
			let reply = await send().catch((error) => {
				if (error instanceof TypeError || error instanceof SyntaxError) return null;
				throw error;
			});
			const unconfirmed = async (response) => {
				if (![502, 503, 504].includes(response.status)) return false;
				const data = await response
					.clone()
					.json()
					.catch(() => null);
				return !data?.detail || data.detail.code === "requirement_extraction_unconfirmed";
			};
			if (!reply || (await unconfirmed(reply.res))) {
				if (!isCurrent()) return;
				if (managed) {
					checkPending = true;
					throw new Error(REQUIREMENT_PREVIEW_UNCONFIRMED);
				}
				reply = await send();
			}
			const { res, data } = reply;
			if (!res.ok) {
				if (await unconfirmed(res)) throw new Error("The requirements preview could not be confirmed. Retry with the same inputs.");
				if (managed && res.status === 409) {
					const rejection = await res
						.clone()
						.json()
						.catch(() => null);
					checkPending = ["project_draft_busy", "project_draft_recovery_required"].includes(rejection?.detail?.code);
				}
				clearAttempt = !checkPending;
				throw new Error(await parseApiError(res, "Failed to prepare requirements"));
			}
			if (!isCurrent()) return;
			imports.accept(data);
			clearAttempt = true;
			setStatus("Incoming requirements are ready to compare. The project baseline has not changed.");
			await Promise.all([refreshUsageSummary(), refreshBillingEntitlements()]);
		} catch (error) {
			if (isCurrent()) {
				const message =
					error instanceof TypeError || error instanceof SyntaxError
						? "The requirements preview could not be confirmed. Retry with the same inputs."
						: error.message;
				imports.setExtractionError(message);
				setStatus(message);
			}
		} finally {
			attempt.inFlight = false;
			if (clearAttempt && attempts.get(path) === attempt) attempts.delete(path);
			if (isCurrent()) {
				setBusy(false);
				if (managed) void (checkPending ? requirementExtraction.check() : requirementExtraction.refresh());
			}
		}
	};

	const importRequirementsFromJira = async () => {
		if (requirementIntakeBlocked) return;
		if (requirementWorkflowLocked) {
			const contactEmail = billingEntitlements?.account?.support_contact_email || "hello@spica-digital.eu";
			setStatus(`Requirement workflows are locked. Contact ${contactEmail} to upgrade.`);
			return;
		}
		if (!selectedJiraIssueKey) {
			setStatus("Select a JIRA epic or issue before importing requirements.");
			return;
		}
		const operationScope = captureProjectOperationScope();
		if (!operationScope || operationScope.projectId !== currentProjectId) {
			setStatus("Open a QA project before importing requirements.");
			return;
		}
		resetJiraSyncState();
		const input = {
			project_id: operationScope.projectId,
			import_mode: "review",
			base_project_revision: currentProjectRevision,
			epic_key: selectedJiraIssueKey,
			include_children: true,
			workflow_settings: buildWorkflowSettingsPayload(requirementWorkflowSettings),
		};
		await submitRequirementPreview({
			path: API_CONTRACT_ENDPOINTS.jiraImport.path,
			body: JSON.stringify(input),
			headers: { "Content-Type": "application/json" },
			input,
			operationScope,
			setBusy: setIsImportingFromJira,
			pendingMessage: "Importing requirements from JIRA...",
		});
	};

	const importRequirementsFromAzureDevOps = async () => {
		if (requirementIntakeBlocked) return;
		if (requirementWorkflowLocked) {
			const contactEmail = billingEntitlements?.account?.support_contact_email || "hello@spica-digital.eu";
			setStatus(`Requirement workflows are locked. Contact ${contactEmail} to upgrade.`);
			return;
		}
		if (!selectedAzureDevOpsProject) {
			setStatus("Choose an Azure DevOps project before importing requirements.");
			return;
		}
		if (!selectedAzureDevOpsWorkItemId) {
			setStatus("Select an Azure DevOps work item before importing requirements.");
			return;
		}
		const operationScope = captureProjectOperationScope();
		if (!operationScope || operationScope.projectId !== currentProjectId) {
			setStatus("Open a QA project before importing requirements.");
			return;
		}
		resetAzureDevOpsSyncState();
		const input = {
			project_id: operationScope.projectId,
			import_mode: "review",
			base_project_revision: currentProjectRevision,
			project: selectedAzureDevOpsProject,
			work_item_id: Number.parseInt(`${selectedAzureDevOpsWorkItemId}`, 10),
			include_children: true,
			workflow_settings: buildWorkflowSettingsPayload(requirementWorkflowSettings),
		};
		await submitRequirementPreview({
			path: API_CONTRACT_ENDPOINTS.azureDevOpsImport.path,
			body: JSON.stringify(input),
			headers: { "Content-Type": "application/json" },
			input,
			operationScope,
			setBusy: setIsImportingFromAzureDevOps,
			pendingMessage: "Importing requirements from Azure DevOps...",
		});
	};

	const previewJiraSync = async (requirementsOverride = requirements, options = {}) => {
		const { clearLastSyncResult = true, operationScope: providedOperationScope = null } = options;
		const operationScope = providedOperationScope || captureProjectOperationScope();
		if (!operationScope || !isProjectOperationCurrent(operationScope)) {
			return null;
		}
		const requirementsToSync = Array.isArray(requirementsOverride) ? requirementsOverride : [];
		if (!requirementsToSync.some((requirement) => isJiraLinkedRequirement(requirement))) {
			setStatus("Import JIRA requirements before previewing write-back updates.");
			return;
		}
		setIsPreviewingJiraSync(true);
		setStatus("Previewing JIRA updates...");
		if (clearLastSyncResult) {
			setJiraSyncResults(null);
		}
		try {
			const res = await apiRequest("/integrations/jira/sync/preview", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					requirements: requirementsToSync,
					managed_section_title: jiraManagedSectionTitle.trim() || DEFAULT_JIRA_SYNC_SECTION_TITLE,
					conflict_strategy: "block",
				}),
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to preview JIRA sync");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			if (!isProjectOperationCurrent(operationScope)) {
				return null;
			}
			setJiraSyncPreview(data || null);
			const readyCount = data?.ready_issue_count || 0;
			const conflictCount = data?.conflict_count || 0;
			setStatus(
				`JIRA preview ready: ${readyCount} issue${readyCount === 1 ? "" : "s"} ready, ${conflictCount} conflict${conflictCount === 1 ? "" : "s"}.`
			);
			return data || null;
		} catch (error) {
			if (isProjectOperationCurrent(operationScope)) {
				setStatus(`JIRA preview failed: ${error.message}`);
			}
			return null;
		} finally {
			if (isProjectOperationCurrent(operationScope)) {
				setIsPreviewingJiraSync(false);
			}
		}
	};

	const applyJiraSync = async () => {
		if (!jiraSyncPreview) {
			setStatus("Preview JIRA changes before pushing updates.");
			return;
		}
		const operationScope = captureProjectOperationScope();
		if (!operationScope || operationScope.projectId !== currentProjectId) {
			return;
		}
		setIsApplyingJiraSync(true);
		setStatus("Pushing updates back to JIRA...");
		try {
			const res = await apiRequest("/integrations/jira/sync", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					requirements,
					managed_section_title: jiraManagedSectionTitle.trim() || DEFAULT_JIRA_SYNC_SECTION_TITLE,
					conflict_strategy: "block",
					allow_conflicts: false,
				}),
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to sync requirements to JIRA");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			if (!isProjectOperationCurrent(operationScope)) {
				return;
			}
			setJiraSyncResults(data || null);
			const syncedRequirements = mergeRequirementMetadata(data?.requirements || requirements, requirements);
			setRequirements(syncedRequirements);
			const updatedCount = data?.updated_issue_count || 0;
			const conflictCount = data?.conflict_count || 0;
			setStatus(
				`JIRA sync complete: ${updatedCount} issue${updatedCount === 1 ? "" : "s"} updated, ${conflictCount} conflict${conflictCount === 1 ? "" : "s"} skipped.`
			);
			await previewJiraSync(syncedRequirements, { clearLastSyncResult: false, operationScope });
		} catch (error) {
			if (isProjectOperationCurrent(operationScope)) {
				setStatus(`JIRA sync failed: ${error.message}`);
			}
		} finally {
			if (isProjectOperationCurrent(operationScope)) {
				setIsApplyingJiraSync(false);
			}
		}
	};

	const previewAzureDevOpsSync = async (requirementsOverride = requirements, options = {}) => {
		const { clearLastSyncResult = true, operationScope: providedOperationScope = null } = options;
		const operationScope = providedOperationScope || captureProjectOperationScope();
		if (!operationScope || !isProjectOperationCurrent(operationScope)) {
			return null;
		}
		const requirementsToSync = Array.isArray(requirementsOverride) ? requirementsOverride : [];
		if (!requirementsToSync.some((requirement) => isAzureDevOpsLinkedRequirement(requirement))) {
			setStatus("Import Azure DevOps requirements before previewing write-back updates.");
			return;
		}
		setIsPreviewingAzureDevOpsSync(true);
		setStatus("Previewing Azure DevOps updates...");
		if (clearLastSyncResult) {
			setAzureDevOpsSyncResults(null);
		}
		try {
			const res = await apiRequest("/integrations/azure-devops/sync/preview", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					requirements: requirementsToSync,
					managed_section_title: azureDevOpsManagedSectionTitle.trim() || DEFAULT_AZURE_DEVOPS_SYNC_SECTION_TITLE,
					conflict_strategy: "block",
				}),
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to preview Azure DevOps sync");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			if (!isProjectOperationCurrent(operationScope)) {
				return null;
			}
			setAzureDevOpsSyncPreview(data || null);
			const readyCount = data?.ready_work_item_count || 0;
			const conflictCount = data?.conflict_count || 0;
			setStatus(
				`Azure DevOps preview ready: ${readyCount} work item${readyCount === 1 ? "" : "s"} ready, ${conflictCount} conflict${conflictCount === 1 ? "" : "s"}.`
			);
			return data || null;
		} catch (error) {
			if (isProjectOperationCurrent(operationScope)) {
				setStatus(`Azure DevOps preview failed: ${error.message}`);
			}
			return null;
		} finally {
			if (isProjectOperationCurrent(operationScope)) {
				setIsPreviewingAzureDevOpsSync(false);
			}
		}
	};

	const applyAzureDevOpsSync = async () => {
		if (!azureDevOpsSyncPreview) {
			setStatus("Preview Azure DevOps changes before pushing updates.");
			return;
		}
		const operationScope = captureProjectOperationScope();
		if (!operationScope || operationScope.projectId !== currentProjectId) {
			return;
		}
		setIsApplyingAzureDevOpsSync(true);
		setStatus("Pushing updates back to Azure DevOps...");
		try {
			const res = await apiRequest("/integrations/azure-devops/sync", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					requirements,
					managed_section_title: azureDevOpsManagedSectionTitle.trim() || DEFAULT_AZURE_DEVOPS_SYNC_SECTION_TITLE,
					conflict_strategy: "block",
					allow_conflicts: false,
				}),
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to sync requirements to Azure DevOps");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			if (!isProjectOperationCurrent(operationScope)) {
				return;
			}
			setAzureDevOpsSyncResults(data || null);
			const syncedRequirements = mergeRequirementMetadata(data?.requirements || requirements, requirements);
			setRequirements(syncedRequirements);
			const updatedCount = data?.updated_work_item_count || 0;
			const conflictCount = data?.conflict_count || 0;
			setStatus(
				`Azure DevOps sync complete: ${updatedCount} work item${updatedCount === 1 ? "" : "s"} updated, ${conflictCount} conflict${conflictCount === 1 ? "" : "s"} skipped.`
			);
			await previewAzureDevOpsSync(syncedRequirements, { clearLastSyncResult: false, operationScope });
		} catch (error) {
			if (isProjectOperationCurrent(operationScope)) {
				setStatus(`Azure DevOps sync failed: ${error.message}`);
			}
		} finally {
			if (isProjectOperationCurrent(operationScope)) {
				setIsApplyingAzureDevOpsSync(false);
			}
		}
	};

	const handleProviderSignIn = async (providerKey) => {
		if (!firebaseAuth) {
			setStatus("Firebase Auth is not configured.");
			return;
		}

		const providerConfig = visibleFirebaseAuthProviders.find((provider) => provider.id === providerKey);
		const provider = createFirebaseAuthProvider(providerKey);
		if (!providerConfig || !provider) {
			setStatus("Selected sign-in provider is not available.");
			return;
		}

		setIsAuthenticating(true);
		setActiveAuthProvider(providerKey);
		setIsSignInDialogOpen(false);
		setStatus(`Signing in with ${providerConfig.label}...`);
		try {
			await signInWithPopup(firebaseAuth, provider, browserPopupRedirectResolver);
		} catch (error) {
			clearAuthState();
			setAuthActionError(buildProviderSignInErrorMessage(providerConfig, error));
		} finally {
			setIsAuthenticating(false);
			setActiveAuthProvider("");
		}
	};

	const handleLogout = async () => {
		setIsAuthenticating(true);
		// The Firebase observer can report a null user before signOut resolves.
		clearAuthState("Signed out.");
		try {
			if (firebaseAuth) {
				await signOut(firebaseAuth);
			}
		} catch (error) {
			setAuthActionError(`Sign out failed: ${error.message}`);
		} finally {
			setIsAuthenticating(false);
		}
	};

	const parseRequirements = async (withFeedback = false) => {
		if (requirementIntakeBlocked) return;
		if (requirementWorkflowLocked) {
			const contactEmail = billingEntitlements?.account?.support_contact_email || "hello@spica-digital.eu";
			setStatus(`Requirement workflows are locked. Contact ${contactEmail} to upgrade.`);
			return;
		}
		const sourceFile =
			requirementSourceMode === "paste" ? new File([pasteDraft.value], "pasted-requirements.md", { type: "text/markdown" }) : file;
		if ((!sourceFile || (requirementSourceMode === "paste" && !pasteDraft.value.trim())) && !withFeedback) return;
		const operationScope = captureProjectOperationScope();
		if (!operationScope || operationScope.projectId !== currentProjectId) {
			setStatus("Open a QA project before parsing requirements.");
			return;
		}
		resetIntegrationSyncState();
		const formData = new FormData();
		const workflowSettingsPayload = buildWorkflowSettingsPayload(requirementWorkflowSettings);
		const feedback = withFeedback ? reqFeedback : "";
		const existing = feedback ? JSON.stringify(requirements) : "";
		if (sourceFile) formData.append("file", sourceFile);
		if (workflowSettingsPayload) formData.append("workflow_settings", JSON.stringify(workflowSettingsPayload));
		formData.append("project_id", operationScope.projectId);
		formData.append("import_mode", "review");
		if (currentProjectRevision !== null) formData.append("base_project_revision", String(currentProjectRevision));
		if (feedback) {
			formData.append("feedback", feedback);
			formData.append("existing_requirements", existing);
		}
		await submitRequirementPreview({
			path: API_CONTRACT_ENDPOINTS.requirementsParse.path,
			body: formData,
			input: [
				currentProjectRevision,
				requirementSourceMode === "paste" ? pasteDraft.value : null,
				workflowSettingsPayload,
				feedback,
				existing,
			],
			upload: requirementSourceMode === "file" ? sourceFile : null,
			operationScope,
			setBusy: setIsParsing,
			pendingMessage: withFeedback ? "Refining requirements with feedback..." : "Parsing requirements...",
		});
	};

	const buildExecutionPayload = (casesOverride = testCases, { includeProject = true, operationScope = null } = {}) => {
		const targetBaseUrl = executionTargetBaseUrl.trim() || appLink || null;
		const targetEnvironment =
			executionTargetEnvironment.trim() || (executionTargetBaseUrl.trim() ? "custom" : appLink ? "application" : "default");
		return {
			test_cases: casesOverride,
			target_base_url: targetBaseUrl,
			target_environment: targetEnvironment,
			project_id: includeProject ? operationScope?.projectId || currentProjectId || null : null,
			base_project_revision: includeProject ? currentProjectRevision : null,
		};
	};

	const changeExecutionTargetBaseUrl = (value) => {
		setExecutionTargetBaseUrl(value);
		resetExecutionWorkflowState();
	};

	const changeExecutionTargetEnvironment = (value) => {
		setExecutionTargetEnvironment(value);
		resetExecutionWorkflowState();
	};

	const previewExecution = async (casesOverride = testCases, options = {}) => {
		if (executionNeedsResolution) {
			setExecutionError("Resolve the previous browser request before previewing another run.");
			return null;
		}
		const { updateStatus = true, persistProject = true, operationScope: providedOperationScope = null } = options;
		const operationScope = providedOperationScope || captureProjectOperationScope();
		if (!operationScope || !isProjectOperationCurrent(operationScope)) {
			return null;
		}
		invalidateExecutionPreviewRequest();
		setExecutionError("");
		setExecutionPreview((previous) =>
			previous
				? { ...previous, requiresRefresh: true, consistencyMessage: "Refresh the prior preview before running candidates." }
				: previous
		);
		const casesToPreview = Array.isArray(casesOverride) ? casesOverride : [];
		if (!casesToPreview.length) {
			if (updateStatus) {
				const message = "Generate test cases before previewing execution.";
				setStatus(message);
				setApplicationLiveStatus(message);
			}
			return null;
		}

		const previewRequestId = executionPreviewRequestIdRef.current;
		const isCurrent = () => isProjectOperationCurrent(operationScope) && previewRequestId === executionPreviewRequestIdRef.current;
		setIsPreviewingExecution(true);
		if (updateStatus) {
			const message = "Previewing execution readiness...";
			setStatus(message);
			setApplicationLiveStatus(message);
		}
		try {
			const payload = buildExecutionPayload(casesToPreview, { includeProject: persistProject, operationScope });
			const fingerprint = JSON.stringify([operationScope, payload]);
			const priorAttempt = executionPreviewAttempt.current;
			const attempt = persistProject && priorAttempt?.fingerprint === fingerprint ? priorAttempt : { fingerprint, id: createRequestId() };
			if (persistProject) executionPreviewAttempt.current = attempt;
			const clearAttempt = () => {
				if (executionPreviewAttempt.current === attempt) executionPreviewAttempt.current = null;
			};
			const request = {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": attempt.id },
				body: JSON.stringify(payload),
			};
			const send = async () => {
				try {
					const res = await apiRequest(API_CONTRACT_ENDPOINTS.automationExecutionPreview.path, request);
					if (res.ok) return { data: await res.json() };
					if (persistProject && [502, 503, 504].includes(res.status)) {
						const rejection = await res
							.clone()
							.json()
							.catch(() => null);
						if (!rejection?.detail || rejection.detail.code === "execution_preview_unconfirmed") return null;
					}
					clearAttempt();
					throw new Error(await parseApiError(res, "Failed to preview execution"));
				} catch (error) {
					if (persistProject && (error instanceof TypeError || error instanceof SyntaxError)) return null;
					throw error;
				}
			};
			let reply = await send();
			if (!reply && isCurrent()) {
				reply = await send();
			}
			if (!isCurrent()) return null;
			if (!reply) throw new Error("The execution preview could not be confirmed. Retry with the same target and test cases.");
			const { data } = reply;
			clearAttempt();
			if (!data || typeof data !== "object" || Array.isArray(data)) {
				throw new Error("Preview response did not include candidate data.");
			}
			const normalizedPreview = storeExecutionPreview(data, { source: "live" });
			setExecutionRunResult(null);
			if (updateStatus) {
				if (!normalizedPreview.isConsistent) {
					const message = "Execution preview data was inconsistent. Preview again before running candidates.";
					setStatus(message);
					setApplicationLiveStatus(message);
				} else {
					const summary = normalizedPreview.summary;
					const message = `Execution preview ready: ${summary.executable} executable, ${summary.manual} manual, ${summary.unsupported} unsupported.`;
					setStatus(message);
					setApplicationLiveStatus(message);
				}
			}
			if (persistProject) {
				await refreshCurrentProject({ hydrate: false, operationScope });
				if (!isCurrent()) {
					return null;
				}
			}
			return normalizedPreview;
		} catch (error) {
			if (isCurrent()) {
				const message = `Execution preview failed: ${error.message}`;
				setApplicationLiveStatus("");
				setExecutionError(message);
			}
			return null;
		} finally {
			if (isCurrent()) {
				setIsPreviewingExecution(false);
			}
		}
	};

	const executionEligibility = getExecutionEligibility(executionPreview, {
		projectId: currentProjectId,
		projectRevision: currentProjectRevision,
		sourceSnapshotId: projectSnapshots.test_cases?.snapshot_id,
	});

	const runApprovedExecution = async () => {
		if (executionNeedsResolution || isPreviewingExecution || isRunningExecution || isProjectOperationCurrent(executingScopeRef.current))
			return;
		const operationScope = captureProjectOperationScope();
		if (!operationScope || operationScope.projectId !== currentProjectId) {
			return;
		}
		const preview = executionPreview;
		if (!isProjectOperationCurrent(operationScope)) {
			return;
		}
		if (!preview) {
			setStatus("Preview execution and review candidate selection before running.");
			return;
		}
		if (!preview?.isConsistent || preview.requiresRefresh) {
			setStatus("Preview execution again before running candidates.");
			return;
		}
		if (!executionEligibility.ready) {
			setExecutionError(executionEligibility.message);
			return;
		}
		const executableCandidates = resolveSelectedExecutableCandidates(preview, selectedExecutionCandidateIds);
		if (!executableCandidates.length) {
			setStatus("Select at least one executable candidate before running.");
			return;
		}

		executionRunRequestIdRef.current += 1;
		const executionRunRequestId = executionRunRequestIdRef.current;
		setExecutionError("");
		setIsRunningExecution(true);
		const runningMessage = `Running ${executableCandidates.length} executable candidate${executableCandidates.length === 1 ? "" : "s"}...`;
		setStatus(runningMessage);
		setApplicationLiveStatus(runningMessage);
		const requestId = createRequestId();
		executingScopeRef.current = operationScope;
		saveExecutionRecovery({ requestId, status: "pending" });
		let outcomeUnconfirmed = true;
		try {
			const payload = {
				...buildExecutionPayload(testCases, { operationScope }),
				selected_test_case_ids: executableCandidates.map((candidate) => candidate.id),
			};
			const res = await apiRequest(API_CONTRACT_ENDPOINTS.automationExecutionRun.path, {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": requestId },
				body: JSON.stringify(payload),
			});
			if (!res.ok) {
				if (res.status < 500 && isProjectOperationCurrent(operationScope)) {
					const problem = await res
						.clone()
						.json()
						.catch(() => null);
					const rejectedBeforeLaunch =
						problem?.detail?.readiness?.run_allowed === false || problem?.detail?.code === "execution_selection_rejected";
					if (rejectedBeforeLaunch && isProjectOperationCurrent(operationScope)) {
						saveExecutionRecovery(null);
						outcomeUnconfirmed = false;
					}
				}
				const errorMessage = await parseApiError(res, "Failed to run execution candidates");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			const runResponseIsValid = Boolean(
				data &&
				typeof data === "object" &&
				!Array.isArray(data) &&
				typeof data.run_id === "string" &&
				data.run_id.trim() &&
				typeof data.status === "string" &&
				data.summary &&
				typeof data.summary === "object" &&
				!Array.isArray(data.summary) &&
				data.preview &&
				typeof data.preview === "object" &&
				!Array.isArray(data.preview) &&
				Array.isArray(data.results)
			);
			if (!runResponseIsValid) {
				throw new Error("Execution response did not include valid run data.");
			}
			if (!isProjectOperationCurrent(operationScope) || executionRunRequestId !== executionRunRequestIdRef.current) {
				return;
			}
			if (data.historical) {
				if (typeof data.run_record_id !== "string" || !data.run_record_id.trim()) {
					throw new Error("The historical execution response did not include its saved run identity.");
				}
				saveExecutionRecovery({ requestId, status: "completed", runId: data.run_id, runRecordId: data.run_record_id, historical: true });
				setApplicationLiveStatus("");
				setStatus("Execution saved to history for its starting project revision.");
				await refreshCurrentProject({ hydrate: true, operationScope });
				return;
			}
			saveExecutionRecovery(null);
			setExecutionRunResult({
				...data,
				source_snapshot_id: data.preview.readiness?.source_snapshot_id,
				target_environment: payload.target_environment,
				target_base_url: data.preview.readiness?.target_base_url ?? payload.target_base_url,
			});
			storeExecutionPreview(data.preview, {
				source: "live",
				selectedCandidateIds: executableCandidates.map((candidate) => candidate.id),
			});
			setApplicationLiveStatus("");
			setStatus(formatExecutionRunSummary(data));
			await refreshCurrentProject({ hydrate: false, operationScope });
		} catch (error) {
			if (isProjectOperationCurrent(operationScope) && executionRunRequestId === executionRunRequestIdRef.current) {
				const message = `${outcomeUnconfirmed ? "Execution outcome unconfirmed" : "Execution run failed"}: ${error.message}`;
				setApplicationLiveStatus("");
				setExecutionError(message);
			}
		} finally {
			if (executingScopeRef.current === operationScope) executingScopeRef.current = null;
			if (isProjectOperationCurrent(operationScope) && executionRunRequestId === executionRunRequestIdRef.current) {
				setIsRunningExecution(false);
			}
		}
	};

	const checkExecutionRecovery = async () => {
		const scope = captureProjectOperationScope();
		const requestId = executionRecovery?.requestId;
		if (!scope || !requestId) return;
		const path = API_CONTRACT_ENDPOINTS.projectExecutionRequest.path
			.replace("{project_id}", encodeURIComponent(scope.projectId))
			.replace("{request_id}", encodeURIComponent(requestId));
		try {
			const response = await apiRequest(path);
			const receipt = response.status === 404 ? null : response.ok ? await response.json() : undefined;
			if (!isProjectOperationCurrent(scope)) return;
			if (receipt === null) {
				saveExecutionRecovery({ requestId, status: "missing" });
				setExecutionError("");
				return;
			}
			if (
				receipt?.project_id !== scope.projectId ||
				receipt?.request_id !== requestId ||
				!["pending", "completed", "unconfirmed"].includes(receipt.status) ||
				(receipt.status === "completed" && (!receipt.run_record_id || !receipt.run_id))
			) {
				throw new Error("The saved execution receipt could not be verified. Check again.");
			}
			saveExecutionRecovery({
				requestId,
				status: receipt.status,
				runId: receipt.run_id,
				runRecordId: receipt.run_record_id,
				historical: receipt.historical === true,
			});
			setExecutionError("");
			if (receipt.status === "completed") await refreshCurrentProject({ hydrate: true, operationScope: scope });
		} catch (error) {
			if (isProjectOperationCurrent(scope)) setExecutionError(error.message);
		}
	};

	const acknowledgeExecutionRecovery = () => {
		saveExecutionRecovery(null);
		resetExecutionWorkflowState();
		setStatus("Execution uncertainty acknowledged. Preview the current suite before running again.");
	};

	const generateTestCases = async (
		withFeedback = false,
		{ fullRegeneration = false, feedbackText = feedback, selectedTestCaseIds = [] } = {}
	) => {
		if (replacesLegacyGeneration) return false;
		if (
			APPROVAL_CONTINUATION_ENABLED &&
			(approvalContinuation.active || approvalContinuation.unresolved || approvalContinuation.run?.state === "interrupted")
		) {
			setStatus("Check the current scenario preparation in Review before generating tests.");
			return false;
		}
		if (contextRefreshRequired) {
			setStatus("Analyze context again in Inputs before generating tests.");
			return false;
		}
		if (withFeedback && (testCaseActionDisabled || isGenerating || upstreamChangedForImpact || !testCases.length)) return false;
		if (!withFeedback && !fullRegeneration && !normalGenerationReady) {
			setStatus("Review current requirements and Use Cases before normal generation. Use the explicit draft action when available.");
			return false;
		}
		if (testCaseWorkflowLocked) {
			const contactEmail = billingEntitlements?.account?.support_contact_email || "hello@spica-digital.eu";
			setStatus(`Test-case workflows are locked. Contact ${contactEmail} to upgrade.`);
			return false;
		}
		if (!canGenerateFromApprovedRequirements) {
			setStatus("Approve at least one requirement before generating test cases.");
			return false;
		}
		const requirementsForGeneration = approvedRequirements;
		const operationScope = captureProjectOperationScope();
		if (!operationScope || operationScope.projectId !== currentProjectId) {
			setStatus("Open a QA project before generating test cases.");
			return false;
		}
		setIsGenerating(true);
		setStatus(
			withFeedback
				? "Refining test cases with approved requirements..."
				: `${fullRegeneration ? "Regenerating the full suite" : "Generating test cases"} from ${requirementsForGeneration.length} approved requirement${requirementsForGeneration.length === 1 ? "" : "s"}...`
		);
		try {
			const requestId = createRequestId();
			const workflowSettingsPayload = buildWorkflowSettingsPayload(testCaseWorkflowSettings);
			const sharedPayload = {
				requirements: requirementsForGeneration,
				template: generationTemplate,
				context: buildContextPayload(requirementsForGeneration),
				workflow_settings: workflowSettingsPayload,
				project_id: operationScope.projectId,
				base_project_revision: currentProjectRevision,
			};

			const useRefineEndpoint = withFeedback && testCases.length > 0;
			const reusableUseCaseArtifacts =
				!useRefineEndpoint && requirementAnalysis.length && coveragePlan.length
					? {
							requirement_analysis: requirementAnalysis,
							coverage_plan: coveragePlan,
						}
					: {};
			const payload = useRefineEndpoint
				? {
						...sharedPayload,
						test_cases: testCases,
						selected_test_case_ids: selectedTestCaseIds,
						feedback: feedbackText.trim(),
					}
				: {
						...sharedPayload,
						...reusableUseCaseArtifacts,
						feedback: withFeedback && feedback ? feedback.trim() : null,
					};

			const testCaseGenerationPath = useRefineEndpoint
				? API_CONTRACT_ENDPOINTS.testCasesRefine.path
				: API_CONTRACT_ENDPOINTS.testCasesGenerate.path;
			const options = {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": requestId },
				body: JSON.stringify(payload),
			};
			const send = async () => {
				const res = await apiRequest(testCaseGenerationPath, options);
				return { res, data: res.ok ? await res.json() : null };
			};
			let reply = await send().catch((error) => {
				if (error instanceof TypeError || error instanceof SyntaxError) return null;
				throw error;
			});
			const unconfirmed =
				reply?.res.status === 503 &&
				(
					await reply.res
						.clone()
						.json()
						.catch(() => null)
				)?.detail?.code === "generation_publication_unconfirmed";
			if (!reply || unconfirmed) {
				if (!isProjectOperationCurrent(operationScope)) return false;
				reply = await send();
			}
			const { res, data } = reply;
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to generate test cases");
				throw Object.assign(new Error(errorMessage), { status: res.status });
			}
			if (!isProjectOperationCurrent(operationScope)) {
				return false;
			}
			if (withFeedback && (!Array.isArray(data.test_cases) || !data.test_cases.length))
				throw new Error("No updated test cases were returned. Your current suite has been kept.");
			rememberGeneration("test_cases", data);
			setTestCases(data.test_cases || []);
			setGenerationTasks(data.generation_tasks || []);
			setRequirementAnalysis(data.requirement_analysis || []);
			setCoveragePlan(data.coverage_plan || []);
			setCoverageMetrics(data.coverage_metrics || null);
			setTestCaseReview(data.review || null);
			setSubstantiveAssessment(data.substantive_assessment || null);
			setTestCaseWorkflowDiagnostics(data.workflow_diagnostics || null);
			setAppliedTestCaseWorkflowSettings(data.workflow_settings || null);
			setTestCaseIterationHistory(data.iteration_history || []);
			setActiveGenerateResultTab(withFeedback ? "improve" : chooseGenerateResultTab(data));
			setDraftExportOverrideRequested(false);
			setDraftExportOverrideReason("");
			resetExecutionWorkflowState();
			const generatedCount = Array.isArray(data.test_cases) ? data.test_cases.length : 0;
			const reviewStatus = data.review ? ` Review ${data.review.approved ? "approved" : "needs refinement"}.` : "";
			if (generatedCount > 0) {
				await refreshCurrentProject({ hydrate: false, operationScope });
				if (!isProjectOperationCurrent(operationScope)) {
					return false;
				}
				await previewExecution(data.test_cases || [], { updateStatus: false, persistProject: false, operationScope });
				if (!isProjectOperationCurrent(operationScope)) {
					return false;
				}
			}
			await Promise.all([refreshUsageSummary(), refreshBillingEntitlements()]);
			if (!isProjectOperationCurrent(operationScope)) {
				return false;
			}
			setStatus(
				`${withFeedback ? "Test cases refined" : fullRegeneration ? "Regenerated" : "Generated"}${generatedCount ? ` ${generatedCount} test case${generatedCount === 1 ? "" : "s"}` : ""} from ${requirementsForGeneration.length} approved requirement${requirementsForGeneration.length === 1 ? "" : "s"}.${reviewStatus}`.trim()
			);
			if (withFeedback) {
				setFeedback("");
				setActiveGenerateResultTab("improve");
			}
			return true;
		} catch (error) {
			if (isProjectOperationCurrent(operationScope)) {
				setStatus(`Generation failed: ${error.message}`);
				if (withFeedback) throw error;
				if (fullRegeneration) return { error: error.message };
			}
			return false;
		} finally {
			if (isProjectOperationCurrent(operationScope)) {
				setIsGenerating(false);
			}
		}
	};

	const analyzeImpact = async () => {
		if (!currentProjectId) {
			setStatus("Open a QA project before running impact analysis.");
			return;
		}
		if (!hasExistingTestCaseBaseline) {
			setStatus("Generate an initial test-case suite before running impact analysis.");
			return;
		}
		const operationScope = captureProjectOperationScope();
		if (!operationScope || operationScope.projectId !== currentProjectId) {
			return;
		}
		setIsAnalyzingImpact(true);
		setStatus("Analyzing impact against the current test-case baseline...");
		try {
			const res = await apiRequest(PROJECT_API_PATHS.impactAnalysis(operationScope.projectId), {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": createRequestId() },
				body: JSON.stringify({ base_project_revision: currentProjectRevision }),
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to analyze impact");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			if (!isProjectOperationCurrent(operationScope)) {
				return;
			}
			if (!data?.project_id || data.project_id !== operationScope.projectId) {
				throw new Error("The impact-analysis response did not match the originating project.");
			}
			setCurrentProject(data || null);
			if (data) {
				hydrateProjectWorkflow(data);
				await loadProjectOrchestrator(data.project_id, { silent: true, operationScope });
				if (!isProjectOperationCurrent(operationScope)) {
					return;
				}
			}
			const analysis = data?.current_snapshots?.impact_analysis?.payload || null;
			const changedCount = analysis?.summary?.changed_item_count || 0;
			const directCount = analysis?.summary?.directly_impacted_test_case_count || 0;
			setStatus(
				`Impact analysis found ${changedCount} changed item${changedCount === 1 ? "" : "s"} and ${directCount} directly impacted test case${directCount === 1 ? "" : "s"}.`
			);
		} catch (error) {
			if (isProjectOperationCurrent(operationScope)) {
				setStatus(`Impact analysis failed: ${error.message}`);
			}
		} finally {
			if (isProjectOperationCurrent(operationScope)) {
				setIsAnalyzingImpact(false);
			}
		}
	};

	const applyImpactUpdate = async () => {
		if (!canApplyImpactUpdate || testCaseActionDisabled) return;
		await impactApplication.apply(
			impactApplication.operation?.status === "failed"
				? impactApplication.operation.accepted_recommendation_ids
				: acceptedImpactRecommendationIds
		);
	};

	const reviewSavedSuite = async () => {
		if (!savedSuiteReviewPending || savedSuiteReviewDisabled || savedSuiteReviewRequests.current.has(savedSuiteReviewKey)) return;
		const scope = captureProjectOperationScope();
		if (scope?.projectId !== currentProjectId) return;
		savedSuiteReviewRequests.current.add(savedSuiteReviewKey);
		setSavedSuiteReview({ key: savedSuiteReviewKey, busy: true });
		let reviewError = null;
		try {
			const response = await apiRequest(PROJECT_API_PATHS.savedSuiteReview(scope.projectId), {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": createRequestId() },
				body: JSON.stringify({ snapshot_id: savedSuite.snapshot_id, base_project_revision: currentProjectRevision }),
			});
			if (!response.ok) {
				const details =
					response.status === 503
						? (
								await response
									.clone()
									.json()
									.catch(() => null)
							)?.detail
						: null;
				const error = new Error(await parseApiError(response, "Saved suite review failed"));
				if (details?.code === "review_groups_unverified" && Array.isArray(details.groups)) error.reviewGroups = details.groups;
				throw error;
			}
			const project = await response.json();
			if (!isProjectOperationCurrent(scope)) return;
			if (project?.project_id !== scope.projectId || !isLatestProjectRevision(project.project_id, project.current_revision)) return;
			recordProjectRevision(project.project_id, project.current_revision);
			setCurrentProject(project);
			hydrateProjectWorkflow(project);
			void loadProjectOrchestrator(project.project_id, { silent: true, operationScope: scope });
		} catch (error) {
			if (!savedReview.enabled || !(error instanceof TypeError || error instanceof SyntaxError)) reviewError = error;
		} finally {
			if (savedReview.enabled) await checkSavedSuiteReview(scope);
			savedSuiteReviewRequests.current.delete(savedSuiteReviewKey);
			const visibleError = isProjectOperationCurrent(scope) && reviewError;
			setSavedSuiteReview((current) =>
				current?.key === savedSuiteReviewKey
					? visibleError
						? { key: savedSuiteReviewKey, busy: false, error: visibleError.message, reviewGroups: visibleError.reviewGroups }
						: null
					: current
			);
		}
	};

	const orchestratorPath = (destination, action) =>
		PROJECT_WORKSPACES_V2 && destination === PROJECT_DESTINATIONS.REQUIREMENTS && ["approve", "review", "refine"].includes(action)
			? buildProjectWorkspacePath(currentProjectId, "review")
			: buildProjectPath(currentProjectId, destination);

	const handleOrchestratorAction = async (recommendation) => {
		const action = recommendation?.action;
		if (!recommendation?.enabled) {
			const blockerMessage = recommendation?.blockers?.[0]?.message || "This orchestrator action is blocked.";
			setStatus(blockerMessage);
			return false;
		}
		const destination = resolveOrchestratorDestination(recommendation);
		if (currentProjectId && destination) {
			navigate(orchestratorPath(destination, action));
		}

		if (action === "analyze_impact") {
			await analyzeImpact();
			return true;
		}
		if (action === "apply_update") {
			await applyImpactUpdate();
			return true;
		}
		if (action === "generate") {
			return generateTestCases(false);
		}
		if (action === "full_regenerate") {
			return generateTestCases(false, { fullRegeneration: true });
		}
		if (action === "automate") {
			await previewExecution();
			return true;
		}
		if (action === "execute") {
			if (!executionPreview || !executionPreview.isConsistent || executionPreview.requiresRefresh || !executionEligibility.ready) {
				await previewExecution();
				return true;
			}
			await runApprovedExecution();
			return true;
		}
		if (action === "report") {
			setStatus("Review report and export options.");
			return true;
		}
		if (action === "refine" || action === "approve") {
			setStatus(recommendation.reason || "Review and approve the required stage before continuing.");
			return true;
		}
		if (action === "review") {
			setStatus(recommendation.reason || "Review the current test-case evidence.");
			return true;
		}

		setStatus(recommendation?.reason || "Open the relevant workflow tab to continue.");
		return Boolean(destination);
	};

	const openOrchestratorWorkbench = async (recommendation) => {
		if (!recommendation?.enabled || !currentProjectId) {
			return false;
		}
		const destination = resolveOrchestratorDestination(recommendation);
		if (!destination) {
			return false;
		}
		navigate(orchestratorPath(destination, recommendation.action));
		return true;
	};

	const orchestratorActionBusy = {
		analyze_impact: isAnalyzingImpact,
		apply_update: isApplyingImpactUpdate,
		generate: isGenerating,
		full_regenerate: isGenerating,
		automate: isPreviewingExecution || isRunningExecution,
		execute: isPreviewingExecution || isRunningExecution,
	};
	const orchestratorActionDisabled = {
		generate: testCaseActionDisabled || !normalGenerationReady || replacesLegacyGeneration,
		full_regenerate: testCaseActionDisabled || replacesLegacyGeneration,
		analyze_impact: testCaseActionDisabled,
		apply_update: testCaseActionDisabled,
	};
	const exportEvidenceKey = `${currentProjectId}:${projectSnapshots.test_cases?.snapshot_id}:${currentProjectRevision}`;
	const currentSuiteSnapshotId =
		PROJECT_WORKSPACES_V2 && route.projectId === currentProjectId && savedSuite?.snapshot_id === testCaseStageState.current_snapshot_id
			? savedSuite?.snapshot_id
			: null;
	const suiteQuality =
		currentSuiteSnapshotId && currentProject?.suite_quality?.suite_snapshot_id === currentSuiteSnapshotId
			? currentProject.suite_quality
			: null;
	const declaredBrowserSetup =
		suiteQuality && [PROJECT_DESTINATIONS.REPORTS, PROJECT_DESTINATIONS.AUTOMATION].includes(route.destination)
			? getCurrentDeclaredBrowserSetup(currentProject)
			: null;
	const reviewedDesignAvailable = Boolean(
		suiteQuality &&
		currentProject?.design_delivery_enabled === true &&
		currentProject.reviewed_design_available === true &&
		!testCaseStageState.stale &&
		persistedTestCaseCount
	);
	const exactSuiteRun = findExactSuiteRun(currentProject, currentSuiteSnapshotId, executionRunResult);
	const readiness = executionPreview?.readiness;
	const hasExactPreview =
		currentSuiteSnapshotId &&
		executionPreview?.hasPreview &&
		executionPreview.isConsistent &&
		!executionPreview.requiresRefresh &&
		readiness?.project_id === currentProjectId &&
		readiness.project_revision === currentProjectRevision &&
		readiness.source_snapshot_id === currentSuiteSnapshotId &&
		typeof readiness.run_allowed === "boolean" &&
		Array.isArray(readiness.blockers);
	const targetReadiness = hasExactPreview ? (executionEligibility.ready ? "Ready" : "Blocked") : "Unknown";
	const suiteQualitySummary = currentSuiteSnapshotId ? (
		<SuiteQualitySummary
			snapshotId={currentSuiteSnapshotId}
			quality={suiteQuality}
			sourceStale={testCaseStageState.stale}
			designDeliveryEnabled={currentProject?.design_delivery_enabled}
			reviewedDesignAvailable={reviewedDesignAvailable}
			targetReadiness={targetReadiness}
			executionRun={exactSuiteRun}
		/>
	) : null;
	const exportReviewApproved = Boolean(
		exportDraftRequiredFor !== exportEvidenceKey &&
		testCaseReview?.approved &&
		projectStageState.test_cases?.approved &&
		!projectStageState.test_cases?.stale &&
		projectSnapshots.test_cases?.snapshot_id &&
		Array.isArray(projectSnapshots.test_cases?.payload?.generation_tasks) &&
		generationTasks.length === 0
	);
	const exportRequiresOverride = Boolean(testCases.length > 0 && !exportReviewApproved && !reviewedDesignAvailable);
	const draftExportOverrideReasonProvided = draftExportOverrideReason.trim().length > 0;
	const exportGateLocked = Boolean(exportRequiresOverride && (!draftExportOverrideRequested || !draftExportOverrideReasonProvided));

	const exportToFormat = async (format) => {
		if (exportGateLocked) {
			setStatus("Export locked by review gate. Add an override reason to export draft test cases.");
			return;
		}
		const operationScope = captureProjectOperationScope();
		if (!operationScope || operationScope.projectId !== currentProjectId) {
			setStatus("Open a QA project before exporting test cases.");
			return;
		}
		setIsExporting(true);
		setExportMessage("");
		setExportError("");
		setStatus(`Exporting to ${format.toUpperCase()}...`);
		try {
			const payload = {
				test_cases: reviewedDesignAvailable ? savedSuite.payload.test_cases : testCases,
				source_snapshot_id: projectSnapshots.test_cases?.snapshot_id || null,
				...(reviewedDesignAvailable
					? { delivery_intent: "reviewed_design" }
					: {
							approved: exportReviewApproved,
							review: testCaseReview || undefined,
							draft_override_requested: Boolean(exportRequiresOverride && draftExportOverrideRequested),
							draft_override_reason: exportRequiresOverride && draftExportOverrideRequested ? draftExportOverrideReason.trim() : null,
						}),
				project_id: operationScope.projectId,
				base_project_revision: currentProjectRevision,
			};
			const exportPath = EXPORT_API_PATHS[format];
			if (!exportPath) {
				throw new Error(`Unsupported export format: ${format}`);
			}
			const attempts = fileExportAttempts.current;
			const fingerprint = JSON.stringify([operationScope, payload]);
			if (attempts.get(format)?.fingerprint !== fingerprint) {
				attempts.set(format, { fingerprint, requestId: createRequestId() });
			}
			const attempt = attempts.get(format);
			const clearAttempt = () => {
				if (attempts.get(format) === attempt) attempts.delete(format);
			};
			const options = {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": attempt.requestId },
				body: JSON.stringify(payload),
			};
			const send = async () => {
				try {
					const res = await apiRequest(exportPath, options);
					if (res.ok) return await res.blob();
					if ([422, 502, 503, 504].includes(res.status)) {
						const rejection = await res
							.clone()
							.json()
							.catch(() => null);
						if (res.status !== 422 && (!rejection?.detail || rejection.detail.code === "export_publication_unconfirmed")) return null;
						if (rejection?.detail?.code === "draft_override_required" && isProjectOperationCurrent(operationScope)) {
							setExportDraftRequiredFor(exportEvidenceKey);
						}
					}
					clearAttempt();
					throw new Error(await parseApiError(res, "Export failed"));
				} catch (error) {
					if (error instanceof TypeError) return null;
					throw error;
				}
			};
			let blob = await send();
			if (!blob && isProjectOperationCurrent(operationScope)) {
				blob = await send();
			}
			if (!isProjectOperationCurrent(operationScope)) {
				return;
			}
			if (!blob) throw new Error("The export could not be confirmed. Retry the same format with the same inputs.");

			const filename = buildTestCaseExportFilename(currentProject, format);
			downloadBlob(blob, filename);
			clearAttempt();
			await refreshCurrentProject({ hydrate: false, operationScope });
			if (!isProjectOperationCurrent(operationScope)) {
				return;
			}
			const message = reviewedDesignAvailable
				? `Reviewed design exported as ${format.toUpperCase()}`
				: `Exported to ${format.toUpperCase()} successfully`;
			setExportMessage(message);
			setStatus(message);
		} catch (error) {
			if (isProjectOperationCurrent(operationScope)) {
				setExportError(`Export failed: ${error.message}`);
				setStatus(`Export failed: ${error.message}`);
			}
		} finally {
			if (isProjectOperationCurrent(operationScope)) {
				setIsExporting(false);
			}
		}
	};

	const requirementScenarioSummaries = coverageMetrics?.requirement_scenario_summary;
	const {
		rows: requirementTraceabilityRows,
		tracedRequirementCount,
		linkedCaseCount,
	} = useMemo(
		() => buildRequirementTraceability(approvedRequirements, testCases, requirementScenarioSummaries),
		[approvedRequirements, testCases, requirementScenarioSummaries]
	);
	const traceabilityGapCount = Math.max(0, approvedRequirements.length - tracedRequirementCount);
	const browserHandoffWarnings = (testCaseWorkflowDiagnostics?.warnings || []).filter((warning) => warning.startsWith("Browser handoff "));
	const hasGenerateResults = Boolean(
		testCases.length ||
		coveragePlan.length ||
		requirementAnalysis.length ||
		testCaseWorkflowDiagnostics ||
		appliedTestCaseWorkflowSettings ||
		testCaseReview
	);
	const generateResultTabs = [
		{
			id: "test-cases",
			label: "Generated Test Cases",
			badge: testCases.length ? testCases.length : "—",
			variant: testCases.length ? "default" : "muted",
		},
		{
			id: "traceability",
			label: "Traceability Matrix",
			badge: approvedRequirements.length ? `${tracedRequirementCount}/${approvedRequirements.length}` : "—",
			variant: traceabilityGapCount > 0 ? "warning" : tracedRequirementCount > 0 ? "success" : "muted",
		},
		{
			id: "improve",
			label: "Improve tests",
			badge: testCaseFindings.length + (substantiveAssessment?.execution_blockers?.length || 0) + Number(browserHandoffWarnings.length > 0),
			variant: testCaseReview?.approved === false || browserHandoffWarnings.length > 0 ? "warning" : "muted",
		},
	];

	const renderImpactAnalysisPanel = () => {
		if (!impactAnalysis) {
			return null;
		}
		const changedItems = impactAnalysis.changed_items || [];
		const impactedCases = impactAnalysis.impacted_test_cases || [];
		const recommendations = impactRecommendationsForApplication;
		const availableScopedIds = scopedImpact ? [...scopedImpact.selectable.keys()] : [];
		const selectionLocked = Boolean(scopedImpact && (scopedImpact.active || scopedImpact.checking || !scopedImpact.statusFresh));
		const titleCounts = new Map();
		for (const { title } of recommendations) titleCounts.set(title, (titleCounts.get(title) || 0) + 1);
		const actionLabels = {
			keep: "Keep",
			update: "Update",
			add: "Add",
			deprecate: "Deprecate",
		};
		return (
			<div className="impact-analysis-panel">
				<div className="impact-analysis-header">
					<div>
						<h2>Impact Analysis</h2>
						<p>
							{changedItems.length} changed item{changedItems.length === 1 ? "" : "s"} • {impactedCases.length} impacted test case
							{impactedCases.length === 1 ? "" : "s"} • {recommendations.length} recommendation
							{recommendations.length === 1 ? "" : "s"}
						</p>
					</div>
					<div className="impact-summary-pills">
						<span>Keep {impactRecommendationCounts.keep || 0}</span>
						<span>Update {impactRecommendationCounts.update || 0}</span>
						<span>Add {impactRecommendationCounts.add || 0}</span>
						<span>Deprecate {impactRecommendationCounts.deprecate || 0}</span>
					</div>
				</div>

				<ImpactApplicationBar
					{...impactApplication}
					recommendations={recommendations}
					showRemainingWork={!scopedImpact}
					analysisDisabled={isAnalyzingImpact || testCaseActionDisabled}
					approvalRequired={impactApplyBlockedByApproval}
					reviewApproved={
						savedSuiteReviewed && savedSuite.approved && savedSuite.source_snapshot_id === impactApplication.operation?.result_snapshot_id
					}
					disabled={!canApplyImpactUpdate || testCaseActionDisabled}
					onApply={applyImpactUpdate}
					onCheck={impactApplication.checkStatus}
					onAnalyze={analyzeImpact}
					onReview={() => {
						if (!impactApplication.operation || (scopedImpact && !scopedImpact.resultCurrent)) return;
						setReviewCaseScope(null);
						setChangedTestCaseIds(impactApplication.operation.changed_test_case_ids);
						setActiveGenerateResultTab("test-cases");
						window.requestAnimationFrame(() => {
							const panel = document.getElementById("generate-result-panel");
							panel?.focus();
							panel?.scrollIntoView({ block: "start" });
						});
					}}
				/>
				{changedItems.length > 0 && (
					<div className="impact-table-block">
						<h3>Changed Inputs</h3>
						<TableScroll className="selection-table-wrapper" role="region" aria-label="Changed inputs table" tabIndex={0}>
							<Table className="selection-table impact-table">
								<thead>
									<tr>
										<th scope="col">Item</th>
										<th scope="col">Type</th>
										<th scope="col">Change</th>
										<th scope="col">Approval</th>
									</tr>
								</thead>
								<tbody>
									{changedItems.map((item) => (
										<tr key={`${item.kind}-${item.item_id}`}>
											<td>
												<strong>{item.item_id}</strong>
												<span>{item.title}</span>
											</td>
											<td>{item.kind === "use_case" ? "Use case" : "Requirement"}</td>
											<td>{item.change_type}</td>
											<td>{item.change_type === "removed" ? "Not required" : item.approved ? "Approved" : "Pending"}</td>
										</tr>
									))}
								</tbody>
							</Table>
						</TableScroll>
					</div>
				)}

				{impactedCases.length > 0 && (
					<div className="impact-table-block">
						<h3>Impacted Test Cases</h3>
						<List as="div" variant="grouped" className="impact-case-list">
							{impactedCases.slice(0, 8).map((testCase) => (
								<div key={`${testCase.impact_source}-${testCase.test_case_id}`} className="impact-case-row">
									<div>
										<strong>{testCase.test_case_id}</strong>
										<span>{testCase.title}</span>
									</div>
									<span className={`impact-source-badge ${testCase.impact_source}`}>{testCase.impact_source.replace("_", " ")}</span>
								</div>
							))}
						</List>
					</div>
				)}

				<div className="impact-table-block">
					<h3>Recommendations</h3>
					{scopedImpact && (
						<div className="workbench-actions">
							<p>
								{scopedImpact.requiredCount
									? `${acceptedImpactRecommendationIds.length} of ${scopedImpact.requiredCount} required direct updates selected.`
									: `This application supports a complete set of up to ${scopedImpact.limit} direct updates.`}{" "}
								Optional neighbors remain available for review.
							</p>
							{scopedImpact.validSelection(availableScopedIds) && (
								<Button
									variant="secondary"
									size="compact"
									disabled={selectionLocked || Boolean(impactApplication.operation)}
									onClick={() => scopedImpactSelection.setValue(availableScopedIds)}
								>
									Select direct updates
								</Button>
							)}
						</div>
					)}
					<List as="div" variant="grouped" className="impact-recommendation-list">
						{recommendations.map((recommendation) => {
							const reference = recommendation.test_case_id || recommendation.scenario_refs?.join(", ") || recommendation.recommendation_id;
							const title = titleCounts.get(recommendation.title) > 1 ? `${recommendation.title} (${reference})` : recommendation.title;
							const checked = acceptedImpactRecommendationIds.includes(recommendation.recommendation_id);
							const selectable = isImpactRecommendationSelectable(recommendation);
							const scopedSelectionLimit =
								scopedImpact &&
								!checked &&
								(acceptedImpactRecommendationIds.length >= scopedImpact.limit ||
									acceptedImpactRecommendationIds.some((id) => scopedImpact.selectable.get(id) === recommendation.test_case_id));
							return (
								<div key={recommendation.recommendation_id} className={`impact-recommendation ${recommendation.action}`}>
									<div>
										<strong>{actionLabels[recommendation.action] || recommendation.action}</strong>
										<span>{title}</span>
										<p>{recommendation.reason}</p>
									</div>
									<div className="impact-recommendation-meta">
										<span>{Math.round((recommendation.confidence || 0) * 100)}%</span>
										<label>
											<Checkbox
												aria-label={`Accept ${title}`}
												data-testid={`impact-accept-${recommendation.recommendation_id}`}
												checked={checked}
												disabled={
													isApplyingImpactUpdate ||
													Boolean(impactApplication.operation) ||
													selectionLocked ||
													(!selectable && !checked) ||
													Boolean(scopedSelectionLimit)
												}
												onChange={(event) => {
													const accepted = event.target.checked;
													if (scopedImpact) {
														const next = accepted
															? [...acceptedImpactRecommendationIds, recommendation.recommendation_id]
															: acceptedImpactRecommendationIds.filter((id) => id !== recommendation.recommendation_id);
														if (!accepted || scopedImpact.validPartialSelection(next)) scopedImpactSelection.setValue(next);
														return;
													}
													setImpactAnalysis((current) => ({
														...current,
														recommendations: current.recommendations.map((item) =>
															item.recommendation_id === recommendation.recommendation_id ? { ...item, accepted } : item
														),
													}));
												}}
											/>
											{impactApplication.operation?.status === "applied" &&
											impactApplication.operation.accepted_recommendation_ids.includes(recommendation.recommendation_id)
												? "Applied"
												: checked
													? "Accepted"
													: !selectable
														? "Outside scoped update"
														: "Review"}
										</label>
									</div>
								</div>
							);
						})}
					</List>
				</div>

				{impactApplyBlockedByApproval && (
					<div className="review-banner review-needs-work">
						<strong>Approval required</strong>
						<p>Approve changed requirements/use cases before applying impact updates.</p>
					</div>
				)}
			</div>
		);
	};

	const analyzeContext = async () => {
		const operationScope = captureProjectOperationScope();
		if (!operationScope || operationScope.projectId !== currentProjectId) {
			setStatus("Open a QA project before analyzing context.");
			return;
		}
		if (PROJECT_WORKSPACES_V2 && imports.preview) return;
		setIsAnalyzingContext(true);
		setContextAnalysisError("");
		setStatus("Analyzing context artifacts...");
		try {
			const payload = buildContextPayload(requirements, { saveInputs: true });
			const previousContext = PROJECT_WORKSPACES_V2 ? contextSnapshot?.payload : enrichedContext;
			if (payload.grounded_context && contextSourceInputs(payload) !== contextSourceInputs(previousContext)) {
				payload.grounded_context = null;
			}
			const fingerprint = JSON.stringify([operationScope, payload]);
			if (contextAnalysisAttempt.current?.fingerprint !== fingerprint) {
				contextAnalysisAttempt.current = { fingerprint, requestId: createRequestId() };
			}
			const attempt = contextAnalysisAttempt.current;
			const clearAttempt = () => {
				if (contextAnalysisAttempt.current === attempt) contextAnalysisAttempt.current = null;
			};
			const options = {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": attempt.requestId },
				body: JSON.stringify(payload),
			};
			const send = async () => {
				try {
					const res = await apiRequest(API_CONTRACT_ENDPOINTS.requirementsEnrich.path, options);
					if (res.ok) return await res.json();
					if ([502, 503, 504].includes(res.status)) {
						const error = await res
							.clone()
							.json()
							.catch(() => null);
						if (!error?.detail || error.detail.code === "context_publication_unconfirmed") return null;
					}
					clearAttempt();
					throw new Error(await parseApiError(res, "Failed to analyze context"));
				} catch (error) {
					if (error instanceof TypeError || error instanceof SyntaxError) return null;
					throw error;
				}
			};
			let data = await send();
			if (!data && isProjectOperationCurrent(operationScope)) {
				data = await send();
			}
			if (!isProjectOperationCurrent(operationScope)) {
				return;
			}
			if (!data) throw new Error("Context analysis could not be confirmed. Retry with the same inputs.");
			clearAttempt();
			if (PROJECT_WORKSPACES_V2) contextDraft.clear({ keepVisible: true });
			setEnrichedContext(data);
			setSelectedArtifactSourceIds((data.grounded_context?.artifact_sources || []).map((source) => source.id));
			await refreshCurrentProject({ hydrate: false, operationScope });
			if (!isProjectOperationCurrent(operationScope)) {
				return;
			}
			setStatus("Context analyzed.");
		} catch (error) {
			if (isProjectOperationCurrent(operationScope)) {
				setStatus(`Context analysis failed: ${error.message}`);
				setContextAnalysisError(error.message);
			}
		} finally {
			if (isProjectOperationCurrent(operationScope)) {
				setIsAnalyzingContext(false);
			}
		}
	};

	const saveTestingIntent = async (testingIntent, baseRevision) => {
		const operationScope = captureProjectOperationScope();
		if (!operationScope || operationScope.projectId !== currentProjectId)
			throw new Error("Open the project before saving the testing objective.");
		if (imports.preview) throw new Error("Finish the source comparison before saving the testing objective.");
		if (isProjectOperationCurrent(testingIntentSave.current)) throw new Error("The testing objective is already saving.");
		testingIntentSave.current = operationScope;
		try {
			const response = await apiRequest(PROJECT_API_PATHS.project(operationScope.projectId), {
				method: "PATCH",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ testing_intent: testingIntent, base_project_revision: baseRevision }),
			});
			if (!response.ok) {
				const error = new Error(await parseApiError(response, "Could not save the testing objective."));
				error.status = response.status;
				throw error;
			}
			const project = await response.json();
			if (!isProjectOperationCurrent(operationScope) || !isLatestProjectRevision(project.project_id, project.current_revision)) {
				throw new Error("Project changed while saving. Reload the current project to check the saved objective.");
			}
			recordProjectRevision(project.project_id, project.current_revision);
			setCurrentProject(project);
			invalidateWorkspaceProject(project.project_id, project.current_revision);
			void loadProjectOrchestrator(project.project_id, { silent: true, operationScope });
			return project;
		} finally {
			if (testingIntentSave.current === operationScope) testingIntentSave.current = null;
		}
	};

	const tabs = navigationProjectId
		? [
				{ id: 7, label: "Overview", title: "Project status", href: buildProjectPath(navigationProjectId) },
				{ id: 0, label: "Requirements", title: "Import and review", href: buildProjectPath(navigationProjectId, "requirements") },
				{ id: 1, label: "Context", title: "Grounded inputs", href: buildProjectPath(navigationProjectId, "context") },
				{ id: 6, label: "Use Cases", title: "Review scenarios", href: buildProjectPath(navigationProjectId, "use-cases") },
				{
					id: 3,
					label: "Test Cases",
					title: "Design and generate",
					href: buildProjectPath(navigationProjectId, "test-cases"),
					attentionLabel: testCasesNeedSetup ? "Setup needed" : undefined,
				},
				{ id: 4, label: "Automation", title: "Preview and run", href: buildProjectPath(navigationProjectId, "automation") },
				{ id: 5, label: "Reports", title: "Evidence and export", href: buildProjectPath(navigationProjectId, "reports") },
			]
		: [];
	const workflowNavStatusByTabId = {
		7: currentProject ? "complete" : "pending",
		0: requirements.length ? (reviewPendingRequirementCount || rejectedRequirementCount ? "attention" : "complete") : "pending",
		1:
			projectStageState.context?.stale || (projectStageState.context?.current_snapshot_id && !projectStageState.context.approved)
				? "attention"
				: enrichedContext?.grounded_context
					? "complete"
					: "pending",
		6: useCasesHumanApproved
			? "complete"
			: projectStageState.use_cases?.current_snapshot_id
				? "attention"
				: requirements.length
					? "pending"
					: "blocked",
		3: testCases.length
			? testCaseStageState.stale || !testCaseStageState.approved || (testCaseReview && !testCaseReview.approved)
				? "attention"
				: "complete"
			: canGenerateFromApprovedRequirements
				? "pending"
				: "blocked",
		4: getAutomationEvidenceStatus(executionPreview, executionRunResult, { stale: projectStageState.execution?.stale }).status,
		5: projectStageState.reports?.current_snapshot_id
			? projectStageState.reports.approved && !projectStageState.reports.stale
				? "complete"
				: "attention"
			: testCases.length && !exportGateLocked
				? "pending"
				: "blocked",
	};

	const savedArtifactCounts = {
		requirements: projectSnapshots.requirements?.payload?.requirements?.length || 0,
		scenarios: (projectSnapshots.use_cases?.payload?.coverage_plan || []).reduce((sum, group) => sum + (group.scenarios?.length || 0), 0),
		testCases: persistedTestCaseCount,
	};
	const selectWorkflowTab = (nextTab) => {
		const resolvedTab = typeof nextTab === "function" ? nextTab(activeTab) : nextTab;
		setActiveTab(resolvedTab);
		if (route.kind === "project" && route.projectId) {
			const destination = getDestinationForLegacyTab(resolvedTab);
			if (PROJECT_WORKSPACES_V2 && destination === route.destination) return;
			navigate(buildProjectPath(route.projectId, destination));
		}
	};
	const moveProjectStep = (direction) => {
		const destination = getAdjacentProjectDestination(route.destination, direction);
		if (destination) selectWorkflowTab(getLegacyTabForDestination(destination));
	};
	const goNext = () => moveProjectStep(1);
	const requirementsRoute = route.destination === PROJECT_DESTINATIONS.REQUIREMENTS;
	const inputsSources = PROJECT_WORKSPACES_V2 && route.workspaceRoute && requirementsRoute && route.workspace === "inputs";
	const reviewScope = PROJECT_WORKSPACES_V2 && requirementsRoute && route.workspace === "review";
	const openScope = () => navigate(buildProjectWorkspacePath(navigationProjectId, "review"));
	const scenariosPath = navigationProjectId ? buildProjectWorkspacePath(navigationProjectId, "review", "scenarios") : "";
	const testsPath = navigationProjectId ? buildProjectWorkspacePath(navigationProjectId, "review", "tests") : "";
	const continuationBusy = approvalContinuation.active || approvalContinuation.run?.state === "interrupted";
	const continuationRetryAvailable =
		!approvalContinuation.unresolved &&
		approvalContinuation.run?.retry_available &&
		approvalContinuation.run.project_revision === currentProjectRevision &&
		(approvalContinuation.stage === "generate_tests" ? approvalContinuation.testsAvailable : approvalContinuation.available);
	const continuationResultStage = approvalContinuation.stage === "generate_tests" ? "test_cases" : "use_cases";
	const preparedScenarioId = projectSnapshots.use_cases?.snapshot_id;
	const retryableScenarioApproval =
		approvalContinuation.unresolved &&
		!savedPlanGeneration.pending &&
		approvalContinuation.pendingRequest?.stage === "generate_tests" &&
		approvalContinuation.pendingRequest?.baseRevision === currentProjectRevision;
	const generateOnScenarioApproval =
		!["project.sync.draft", "project.sync.rebind"].includes(projectSnapshots.use_cases?.operation) &&
		!projectSnapshots.use_cases?.metadata?.use_case_refresh &&
		approvalContinuation.testsAvailable &&
		preparedScenarioId &&
		!budgetStopsStage("generate_tests", preparedScenarioId) &&
		(!approvalContinuation.unresolved || retryableScenarioApproval) &&
		!continuationBusy &&
		approvalContinuation.runs.some(
			(run) => run.stage === "prepare_scenarios" && run.state === "awaiting_user" && run.result_snapshot_id === preparedScenarioId
		) &&
		!approvalContinuation.runs.some((run) => run.stage === "generate_tests" && run.source_snapshot_id === preparedScenarioId);
	const continuationResultCurrent =
		approvalContinuation.run?.result_snapshot_id === projectSnapshots[continuationResultStage]?.snapshot_id &&
		!projectStageState[continuationResultStage]?.stale;
	const canOpenPriorityCases =
		APPROVAL_CONTINUATION_ENABLED &&
		route.workspace === "review" &&
		route.section === "scenarios" &&
		navigationProjectId === currentProjectId &&
		currentUser?.sub &&
		!approvalContinuation.unresolved &&
		!approvalContinuation.blockedReason &&
		approvalContinuation.run?.stage === "generate_tests" &&
		approvalContinuation.run.state === "completed" &&
		Boolean(approvalContinuation.run.result_snapshot_id) &&
		continuationResultCurrent &&
		savedSuite?.project_id === currentProjectId &&
		testCaseStageState.current_snapshot_id === savedSuite.snapshot_id;
	const priorityCases = useMemo(() => (canOpenPriorityCases ? prioritySavedCases(savedSuite) : []), [canOpenPriorityCases, savedSuite]);
	const openPriorityCase = (caseId) => {
		if (!priorityCases.some((item) => item.id === caseId)) return;
		GeneratedTestCasesView.preload();
		navigate(testsPath, {
			state: {
				priorityTestCase: {
					identity: currentUser.sub,
					projectId: currentProjectId,
					snapshotId: savedSuite.snapshot_id,
					caseId,
				},
			},
		});
	};
	const testReviewRoute = route.destination === PROJECT_DESTINATIONS.TEST_CASES && activeTab === 3;
	const reviewExportPath =
		PROJECT_WORKSPACES_V2 && testReviewRoute && navigationProjectId ? buildProjectWorkspacePath(navigationProjectId, "deliver") : "";
	const savedPlanOffer = savedPlanGeneration.offer;
	const savedPlanPending = approvalContinuation.pendingRequest;
	const retryableSavedPlan =
		approvalContinuation.unresolved &&
		savedPlanGeneration.pending &&
		savedPlanPending.snapshotId === savedPlanSnapshotId &&
		savedPlanPending.reviewId === latestUseCasesHumanReview?.review_id &&
		savedPlanPending.baseRevision === currentProjectRevision;
	const showSavedPlanGeneration =
		testReviewRoute &&
		!savedCaseLink &&
		((replacesLegacyGeneration && (!approvalContinuation.unresolved || savedPlanGeneration.pending)) || savedPlanGeneration.error);
	const canStartSavedPlanGeneration =
		showSavedPlanGeneration &&
		savedPlanOffer?.snapshot_id === savedPlanSnapshotId &&
		savedPlanOffer?.review_id === latestUseCasesHumanReview?.review_id &&
		savedPlanOffer?.base_project_revision === currentProjectRevision &&
		approvalContinuation.testsAvailable &&
		!savedReview.statusError &&
		!continuationBusy &&
		(!approvalContinuation.unresolved || retryableSavedPlan) &&
		!approvalContinuation.runs.some((run) => run.stage === "generate_tests" && run.source_snapshot_id === savedPlanSnapshotId);
	const startSavedPlanGeneration = async () => {
		const operationScope = captureProjectOperationScope();
		if (!operationScope || !canStartSavedPlanGeneration || testCaseActionDisabled) return;
		const outcome = await savedPlanGeneration.start(generationTemplate);
		if (outcome === "rejected" && isProjectOperationCurrent(operationScope)) {
			void refreshCurrentProject({ hydrate: true, operationScope });
		}
	};
	const continuationPanel =
		APPROVAL_CONTINUATION_ENABLED && (!useScopedScenarioRefreshFlow || continuationBusy || approvalContinuation.unresolved) ? (
			<ApprovalContinuationPanel
				continuation={approvalContinuation}
				scenariosPath={scenariosPath}
				testsPath={testsPath}
				navigate={navigate}
				onCheck={() => {
					const operationScope = captureProjectOperationScope();
					void approvalContinuation.check(() => refreshCurrentProject({ hydrate: true, operationScope }));
				}}
				saving={isSavingRequirementReview && Boolean(approvalContinuation.pendingRequest)}
				resultCurrent={continuationResultCurrent}
				retryAvailable={continuationRetryAvailable}
				savedPlanRequest={savedPlanGeneration.pending}
				savedPlanGeneration={
					showSavedPlanGeneration
						? {
								start: canStartSavedPlanGeneration ? startSavedPlanGeneration : null,
								snapshotId: savedPlanSnapshotId,
								disabled: testCaseActionDisabled,
								submitting: savedPlanGeneration.submitting,
								error: savedPlanGeneration.error,
								statusRead: savedReview.statusRead,
							}
						: null
				}
				projectId={currentProjectId}
				priorityCases={priorityCases}
				onOpenPriorityCase={openPriorityCase}
			/>
		) : null;
	const proposedScopeApproval =
		reviewScope && approvalContinuation.available
			? requirements
					.filter(
						(row) => getRequirementReviewStatus(row) !== "Rejected" && !["open", "deferred", "excluded"].includes(row.clarification_status)
					)
					.map((row) => ({ ...row, review_status: "Approved" }))
			: [];
	const scopedRefreshEntry = location.state?.scenarioRefreshEntry;
	const canOpenScopedRefresh = scenarioRefresh.canStart && hasApprovedGenerationScope(currentProject) && !contextRefreshRequired;
	const openScopedRefresh = () =>
		navigate(scenariosPath, {
			state: {
				scenarioRefreshEntry: {
					projectId: currentProjectId,
					identity: currentUser.sub,
					predecessorId: scenarioRefreshPredecessor,
					revision: currentProjectRevision,
				},
			},
		});
	const openRefreshScope = Boolean(
		scenarioRefreshPredecessor &&
		scopedRefreshEntry?.projectId === currentProjectId &&
		scopedRefreshEntry?.identity === currentUser?.sub &&
		scopedRefreshEntry.predecessorId === scenarioRefreshPredecessor &&
		scopedRefreshEntry.revision === currentProjectRevision
	);
	const retryableApproval =
		approvalContinuation.unresolved &&
		approvalContinuation.stage === "prepare_scenarios" &&
		approvalContinuation.pendingRequest?.baseRevision === currentProjectRevision;
	const rebindReview =
		APPROVAL_CONTINUATION_ENABLED &&
		(reviewScope || (testReviewRoute && !savedCaseLink)) &&
		currentUser?.sub &&
		currentProject?.owner_user_id === currentUser.sub
			? {
					identity: currentUser.sub,
					material: materialSync,
					scopeCurrent: reviewScope,
					scopePath: buildProjectWorkspacePath(navigationProjectId, "review"),
					scenariosPath,
					navigate,
					request: (path, options) => apiRequest(path, options, true, { allowGuidanceRecovery: false }),
					onUpdated: () => {
						const operationScope = captureProjectOperationScope();
						if (operationScope?.projectId !== navigationProjectId || operationScope?.userId !== currentUser.sub) return null;
						return refreshCurrentProject({ hydrate: "if-changed", operationScope });
					},
				}
			: null;
	const openTraceabilityCase = (path, target) => {
		navigate(`${location.pathname}${location.search}${location.hash}`, {
			replace: true,
			state: {
				...location.state,
				traceabilityCase: { ...target, projectId: currentProjectId, snapshotId: savedSuite.snapshot_id },
			},
		});
		navigate(path);
	};
	const compactReview = requirementsRoute || testReviewRoute;
	const requirementSetupOpen =
		!imports.preview && (requirementSetup?.projectId === navigationProjectId ? requirementSetup.open : requirements.length === 0);
	const toggleRequirementSetup = () => setRequirementSetup({ projectId: navigationProjectId, open: !requirementSetupOpen });
	const focusPendingRequirements = () => {
		setRequirementSetup({ projectId: navigationProjectId, open: false });
		setRequirementReviewRequest({ projectId: navigationProjectId });
		setRequirementFocus({ projectId: navigationProjectId, target: "review" });
	};
	const reviewRequirementImport = async (action, payload) => {
		const completed = await imports.mutate(action, payload);
		if (!completed || action === "compare") return completed;
		setRequirementSetup({ projectId: navigationProjectId, open: action === "cancel" && !requirements.length });
		setRequirementFocus({ projectId: navigationProjectId, target: action === "apply" ? (inputsSources ? "scope" : "review") : "import" });
		return completed;
	};
	const focusRequirementTarget = useCallback(() => {
		if (!requirementsRoute || imports.preview || requirementFocus?.projectId !== navigationProjectId) return;
		const target =
			requirementFocus.target === "scope"
				? reviewScopeAction.current
				: requirementFocus.target === "review"
					? requirementReviewHeading.current
					: requirementImportControl.current;
		if (!target) return;
		target.focus({ preventScroll: true });
		target.scrollIntoView({ block: "nearest" });
		setRequirementFocus(null);
	}, [imports.preview, requirementFocus, requirementsRoute, navigationProjectId]);
	useEffect(focusRequirementTarget, [focusRequirementTarget]);

	const goPrev = () => moveProjectStep(-1);
	const activeProjectNavigationTab = route.destination === "overview" ? 7 : activeTab === 2 ? 3 : activeTab;
	const deliverWorkspace =
		PROJECT_WORKSPACES_V2 && route.workspace === "deliver" ? PROJECT_WORKSPACES.find(({ id }) => id === "deliver") : null;
	const deliverSection = deliverWorkspace?.sections.find(({ id }) => id === route.section);
	const activeProjectDestinationLabel = deliverSection
		? `${deliverWorkspace.label} · ${deliverSection.label}`
		: tabs.find((tab) => tab.id === activeProjectNavigationTab)?.label || "Project";
	const contextualTestCaseTask = selectContextualTask(orchestratorStatus, {
		destination: PROJECT_DESTINATIONS.TEST_CASES,
	}).primaryAction;
	const requirementReviewTask = requirementsRoute
		? selectContextualTask(orchestratorStatus, { destination: PROJECT_DESTINATIONS.REQUIREMENTS }).primaryAction
		: null;
	const hasOrchestratorRecommendations = Boolean(Array.isArray(orchestratorStatus?.next_actions) && orchestratorStatus.next_actions.length);
	const allowLegacyTestCaseMutations =
		!replacesLegacyGeneration &&
		(upstreamChangedForImpact ||
			((!useCasesStageState.current_snapshot_id || useCasesHumanApproved) &&
				hasApprovedGenerationScope(currentProject) &&
				!projectStageState.requirements?.stale)) &&
		!hasOrchestratorRecommendations &&
		!orchestratorError &&
		!isLoadingOrchestrator &&
		orchestratorStatus?.project_revision === currentProjectRevision;
	const { billingContactEmail, billingStatusItems, statusUsageItems, pilotAlert } = useBillingStatus(billingEntitlements, usageSummary);
	const workflowShellClassName = PROJECT_WORKSPACES_V2
		? "workflow-shell project-workspaces-v2"
		: `workflow-shell${isWorkflowNavCollapsed ? " nav-collapsed" : ""}`;
	const currentAuthProviderLabel = activeAuthProvider ? getAuthProviderLabel(activeAuthProvider) : "";
	const jiraSettings = {
		jiraConnected,
		jiraConnection,
		jiraConnectionForm,
		setJiraConnectionForm,
		saveJiraConnection,
		authActionDisabled,
		isSavingJiraConnection,
		isJiraConnectionLoading,
		refreshJiraConnectionStatus,
		currentUser,
		deleteStoredJiraConnection,
		isDeletingJiraConnection,
	};
	const azureDevOpsSettings = {
		azureDevOpsConnected,
		azureDevOpsConnection,
		azureDevOpsConnectionForm,
		setAzureDevOpsConnectionForm,
		saveAzureDevOpsConnection,
		authActionDisabled,
		isSavingAzureDevOpsConnection,
		isAzureDevOpsConnectionLoading,
		refreshAzureDevOpsConnectionStatus,
		currentUser,
		deleteStoredAzureDevOpsConnection,
		isDeletingAzureDevOpsConnection,
	};
	const navigationControls = (
		<AppNavigationControls
			isAuthenticated={isAuthenticated}
			billingStatusItems={billingStatusItems}
			statusUsageItems={statusUsageItems}
			isUsageLoading={isUsageLoading}
			isBillingLoading={isBillingLoading}
			onOpenSettings={() => openSettingsDialog("workflow")}
			isVerifyingSession={isVerifyingSession}
			currentUser={currentUser}
			getAuthProviderLabel={getAuthProviderLabel}
			handleLogout={handleLogout}
			isAuthenticating={isAuthenticating}
			hasFirebaseAuthConfig={hasFirebaseAuthConfig}
			hasVisibleAuthProviders={hasVisibleAuthProviders}
			openSignInDialog={openSignInDialog}
			currentAuthProviderLabel={currentAuthProviderLabel}
			projects={projects}
			currentProject={currentProject}
			isLoadingProjects={isLoadingProjects}
			isOpeningProject={isOpeningProject}
			authActionDisabled={authActionDisabled}
			newProjectName={newProjectName}
			setNewProjectName={setNewProjectName}
			isCreatingProject={isCreatingProject}
			onOpenProject={handleProjectSelection}
			onCreateProject={createProjectAndNavigate}
			onRefreshProjects={() => loadProjects()}
		/>
	);
	const recoveryKind =
		projectRouteStatus === "not-found"
			? "missing"
			: projectRouteStatus === "forbidden"
				? "forbidden"
				: projectRouteStatus === "auth-required"
					? "inaccessible"
					: "error";

	return (
		<div className="page">
			<Link className="skip-link" href="#main-content">
				Skip to main content
			</Link>
			<div className="sr-only" role="status" aria-live="polite" aria-atomic="true" data-testid="application-live-status">
				{applicationLiveStatus}
			</div>
			{!isAuthenticated && !isVerifyingSession && (
				<div className="auth-warning-banner" role={authActionError ? "alert" : undefined}>
					{authActionError || "🔐 Sign in to parse requirements, generate test cases, and export artifacts."}
				</div>
			)}

			<BillingBanner isAuthenticated={isAuthenticated} pilotAlert={pilotAlert} billingContactEmail={billingContactEmail} />

			<SignInDialog
				isOpen={isSignInDialogOpen}
				onOverlayClick={handleSignInDialogOverlayClick}
				onClose={closeSignInDialog}
				isAuthenticating={isAuthenticating}
				providers={visibleFirebaseAuthProviders}
				onProviderSignIn={handleProviderSignIn}
			/>

			{guidanceRecovery.dialog}
			<SettingsDialog
				key={currentUser?.sub || "anonymous"}
				request={apiRequest}
				isOpen={isSettingsDialogOpen}
				onOverlayClick={handleSettingsDialogOverlayClick}
				onClose={closeSettingsDialog}
				settingsSection={settingsSection}
				setSettingsSection={setSettingsSection}
				requirementWorkflowSettings={requirementWorkflowSettings}
				setRequirementWorkflowSettings={setRequirementWorkflowSettings}
				testCaseWorkflowSettings={testCaseWorkflowSettings}
				setTestCaseWorkflowSettings={setTestCaseWorkflowSettings}
				isAuthenticated={isAuthenticated}
				jiraSettings={jiraSettings}
				azureDevOpsSettings={azureDevOpsSettings}
			/>

			<GlobalAppShell route={route} navigate={navigate} controls={navigationControls} compactControls={isCompactWorkflowNavigation}>
				{route.kind === "global" ? (
					route.destination === "home" ? (
						<HomePage
							summary={workspaceSummary}
							isLoading={isAuthenticated && (isWorkspaceSummaryLoading || isVerifyingSession)}
							isRefreshing={isWorkspaceSummaryRefreshing}
							error={isAuthenticated ? workspaceSummaryError : ""}
							onRetry={retryWorkspaceSummary}
							onOpenProject={openWorkspaceProject}
							onCreateProject={() => navigate("/projects")}
						/>
					) : route.destination === "projects" ? (
						<ProjectsPage
							onModuleReady={focusPendingRoute}
							projects={workspaceSummary?.projects || []}
							isLoading={isAuthenticated && (isWorkspaceSummaryLoading || isVerifyingSession) && !workspaceSummary}
							error={isAuthenticated ? workspaceSummaryError : ""}
							onRetry={retryWorkspaceSummary}
							onCreateProject={createWorkspaceProject}
							onOpenProject={openWorkspaceProject}
							isCreatingProject={isCreatingProject}
						/>
					) : route.destination === "reviews" ? (
						<ReviewsPage
							onModuleReady={focusPendingRoute}
							summary={workspaceSummary}
							isLoading={isAuthenticated && (isWorkspaceSummaryLoading || isVerifyingSession)}
							isRefreshing={isWorkspaceSummaryRefreshing}
							error={isAuthenticated ? workspaceSummaryError : ""}
							onRetry={retryWorkspaceSummary}
							onRefresh={refreshWorkspaceSummary}
							onOpenProject={openWorkspaceProject}
						/>
					) : route.destination === "runs" ? (
						<RunsPage
							onModuleReady={focusPendingRoute}
							summary={workspaceSummary}
							isLoading={isAuthenticated && (isWorkspaceSummaryLoading || isVerifyingSession)}
							isRefreshing={isWorkspaceSummaryRefreshing}
							error={isAuthenticated ? workspaceSummaryError : ""}
							onRetry={retryWorkspaceSummary}
							onRefresh={refreshWorkspaceSummary}
							onOpenProject={openWorkspaceProject}
						/>
					) : route.destination === "reports" ? (
						<ReportsPage
							onModuleReady={focusPendingRoute}
							summary={workspaceSummary}
							isLoading={isAuthenticated && (isWorkspaceSummaryLoading || isVerifyingSession)}
							isRefreshing={isWorkspaceSummaryRefreshing}
							error={isAuthenticated ? workspaceSummaryError : ""}
							onRetry={retryWorkspaceSummary}
							onRefresh={refreshWorkspaceSummary}
							onOpenProject={openWorkspaceProject}
						/>
					) : (
						<GlobalDestinationPlaceholderPage destination={route.destination} navigate={navigate} />
					)
				) : route.kind === "not-found" ? (
					<RouteRecoveryPage
						kind={route.reason === "invalid-project-destination" ? "invalid-stage" : "not-found"}
						title={route.reason === "invalid-project-destination" ? "Unknown project destination" : "Page not found"}
						projectId={route.projectId}
						navigate={navigate}
					/>
				) : projectRouteStatus === "loading" || isVerifyingSession || projectRouteStatus === "idle" ? (
					<ProjectLoadingPage projectId={route.projectId} />
				) : projectRouteStatus !== "ready" ? (
					<RouteRecoveryPage
						kind={recoveryKind}
						title={
							projectRouteStatus === "not-found"
								? "Project not found"
								: projectRouteStatus === "forbidden"
									? "Project unavailable"
									: projectRouteStatus === "auth-required"
										? "Sign in to open this project"
										: "Unable to open project"
						}
						message={projectRouteError}
						projectId={route.projectId}
						navigate={navigate}
					/>
				) : currentProject?.project_id !== route.projectId ? (
					<ProjectLoadingPage projectId={route.projectId} />
				) : (
					<div className={workflowShellClassName}>
						{PROJECT_WORKSPACES_V2 ? (
							<ProjectWorkspaceNavigation route={route} navigate={navigateWorkspace} counts={savedArtifactCounts} />
						) : (
							<WorkflowNavigationDrawer
								tabs={tabs}
								activeTab={activeProjectNavigationTab}
								onTabChange={selectWorkflowTab}
								statusByTabId={workflowNavStatusByTabId}
								isCollapsed={isWorkflowNavCollapsed}
								isCompact={isCompactWorkflowNavigation}
								onToggleCollapsed={toggleWorkflowNavCollapsed}
								onRequestClose={closeCompactWorkflowNavigation}
							/>
						)}

						{route.destination === "overview" ? (
							<ProjectOverviewPage
								project={currentProject}
								status={orchestratorStatus}
								counts={
									PROJECT_WORKSPACES_V2
										? savedArtifactCounts
										: { ...savedArtifactCounts, requirements: requirements.length, testCases: testCases.length }
								}
								testCaseReview={testCaseReview}
								testCasesNeedSetup={testCasesNeedSetup}
								exportLocked={exportGateLocked}
								navigate={navigate}
								contextualTask={
									<OrchestratorCockpitPanel
										currentProject={currentProject}
										status={orchestratorStatus}
										currentDestination={PROJECT_DESTINATIONS.OVERVIEW}
										isOverview
										isLoading={isLoadingOrchestrator}
										error={orchestratorError}
										authActionDisabled={authActionDisabled}
										actionBusy={orchestratorActionBusy}
										actionDisabled={orchestratorActionDisabled}
										onAction={openOrchestratorWorkbench}
									/>
								}
							/>
						) : route.destination === "use-cases" ? (
							<UseCaseReviewPage
								onModuleReady={focusPendingRoute}
								project={currentProject}
								identity={currentUser?.sub || ""}
								request={apiRequest}
								navigate={navigate}
								scopePath={PROJECT_WORKSPACES_V2 ? buildProjectWorkspacePath(navigationProjectId, "review") : undefined}
								onDecisionCommitted={handleUseCaseReviewCommitted}
								onGenerated={handleUseCasesGenerated}
								generationDisabled={testCaseActionDisabled || isGenerating || continuationBusy || approvalContinuation.unresolved}
								continuationPanel={continuationPanel}
								continuation={APPROVAL_CONTINUATION_ENABLED ? approvalContinuation : null}
								generateOnApproval={generateOnScenarioApproval}
								generationTemplate={generationTemplate}
								scopedRefresh={useScopedScenarioRefreshFlow ? scenarioRefresh : null}
								openRefreshScope={openRefreshScope}
								materialSyncState={
									materialSyncRebindRequired ? "requires_rebind" : reboundSyncPlan && !useCasesStageState.stale ? "rebound" : null
								}
								contextRefreshRequired={contextRefreshRequired}
								onReloadLatest={() => reloadLatestUseCases(currentProject.project_id)}
								onBack={PROJECT_WORKSPACES_V2 ? openScope : goPrev}
								onNext={goNext}
							/>
						) : (
							<main
								id="main-content"
								ref={workflowMainRef}
								className={`workflow-main ${route.destination === "test-cases" ? "test-cases-page" : ""}${compactReview ? " compact-workbench" : ""}`}
								aria-label={`Workflow workspace: ${activeProjectDestinationLabel}`}
								tabIndex={-1}
							>
								<ProjectPageHeader
									title={inputsSources ? "Inputs" : reviewScope ? "Review scope" : activeProjectDestinationLabel}
									compact={compactReview}
									project={currentProject}
									navigate={navigate}
									actions={
										requirementsRoute && !reviewScope && !imports.preview ? (
											<div className="workbench-actions">
												{imports.pending.length === 1 && (
													<Button
														variant="secondary"
														size="compact"
														aria-label={`Review import: ${imports.pending[0].source_name}`}
														title={imports.pending[0].source_name}
														onClick={() => imports.accept(imports.pending[0])}
													>
														Review import
													</Button>
												)}
												{imports.pending.length > 1 && (
													<Disclosure className="pending-import-actions">
														<summary>Pending imports ({imports.pending.length})</summary>
														{imports.pending.map((item) => (
															<Button key={item.import_id} variant="secondary" size="compact" onClick={() => imports.accept(item)}>
																Review import: {item.source_name}
															</Button>
														))}
													</Disclosure>
												)}
												<Button
													ref={requirementImportControl}
													variant={requirements.length || requirementSetupOpen ? "secondary" : "primary"}
													size="compact"
													aria-expanded={requirementSetupOpen}
													aria-controls="requirement-import-setup"
													onClick={toggleRequirementSetup}
												>
													{requirementSetupOpen ? "Hide import setup" : "Import requirements"}
												</Button>
												{inputsSources ? (
													<Button ref={reviewScopeAction} size="compact" onClick={openScope} disabled={!requirements.length}>
														Review scope
													</Button>
												) : requirements.length > 0 ? (
													reviewPendingRequirementCount > 0 ? (
														<Button size="compact" onClick={focusPendingRequirements}>
															Review pending requirements
														</Button>
													) : (
														<Button size="compact" onClick={goNext}>
															Continue to Context
														</Button>
													)
												) : null}
											</div>
										) : reviewScope && !imports.preview ? (
											materialSyncRebindRequired ||
											budgetStopsStage("prepare_scenarios", projectStageState.requirements?.current_snapshot_id) ||
											(reboundSyncPlan && !useCasesStageState.stale) ||
											(continuationRetryAvailable &&
												approvalContinuation.stage === "prepare_scenarios") ? null : !useScopedScenarioRefreshFlow &&
											  approvalContinuation.available &&
											  (!useCasesStageState.current_snapshot_id || useCasesStageState.stale) ? (
												<Button
													size="compact"
													onClick={() => void saveRequirementReviews(proposedScopeApproval, { prepareScenarios: true })}
													disabled={
														requirementActionDisabled ||
														continuationBusy ||
														(approvalContinuation.unresolved && !retryableApproval) ||
														!proposedScopeApproval.length ||
														contextRefreshRequired
													}
												>
													{retryableApproval ? "Retry scope approval request" : "Approve scope and prepare scenarios"}
												</Button>
											) : (
												<Button
													size="compact"
													onClick={canOpenScopedRefresh ? openScopedRefresh : () => navigate(scenariosPath)}
													disabled={!hasApprovedGenerationScope(currentProject) && !scenarioRefresh.attempt && !scenarioRefresh.pending}
												>
													{canOpenScopedRefresh ? "Review refresh scope" : "Review scenarios"}
												</Button>
											)
										) : route.destination === "test-cases" && !savedCaseLink ? (
											<div className="workbench-actions">
												{testReviewRoute && testCases.length > 0 && exportGateLocked && (
													<Badge className="workbench-status" tone="warning">
														Export blocked
													</Badge>
												)}
												<Button variant="secondary" size="compact" onClick={() => selectWorkflowTab(activeTab === 2 ? 3 : 2)}>
													{activeTab === 2 ? "Generate and review" : "Template setup"}
												</Button>
												{testReviewRoute &&
													testCases.length > 0 &&
													!contextualTestCaseTask &&
													!generationTasks.length &&
													!upstreamChangedForImpact &&
													hasApprovedGenerationScope(currentProject) &&
													(!useCasesStageState.current_snapshot_id || useCasesHumanApproved) && (
														<Button
															size="compact"
															onClick={() => {
																if (activeGenerateResultTab === "test-cases") document.getElementById("generate-result-panel")?.focus();
																else openCaseResults(selectedTestCaseId);
															}}
														>
															Review tests
														</Button>
													)}
											</div>
										) : null
									}
								/>

								{reviewScope && !imports.preview && contextRefreshRequired && (
									<Alert tone="warning" className="compact-readiness" role="status">
										Saved context is stale.{" "}
										<RouteLink to={buildProjectWorkspacePath(navigationProjectId, "inputs", "context")} navigate={navigate}>
											Open Context
										</RouteLink>{" "}
										and select Analyze Context before preparing scenarios.
									</Alert>
								)}
								{requirementsRoute && !reviewScope && (
									<RequirementExtractionStatus
										status={requirementExtraction}
										submitting={submittingRequirementPreview}
										extractionError={imports.extractionError}
										requirementsSnapshotId={projectSnapshots.requirements?.snapshot_id}
									/>
								)}
								{(reviewScope ||
									(testReviewRoute && !savedCaseLink && (continuationResultStage === "test_cases" || showSavedPlanGeneration))) &&
									continuationPanel}
								{!inputsSources && !savedCaseLink && ["requirements", "test-cases", "automation"].includes(route.destination) && (
									<ProjectSyncStatus
										key={`sync-${navigationProjectId}-${rebindReview?.identity || "legacy"}`}
										compact={compactReview}
										project={currentProject}
										request={apiRequest}
										rebind={rebindReview}
										onUpdated={() => openProject(navigationProjectId, { silent: true })}
									/>
								)}
								{!inputsSources && !reviewScope && !savedCaseLink && ["requirements", "test-cases"].includes(route.destination) && (
									<ClarificationScopeNotice
										project={currentProject}
										onOpen={() => (PROJECT_WORKSPACES_V2 ? openScope() : navigate(buildProjectPath(navigationProjectId, "context")))}
									/>
								)}
								{!inputsSources &&
									["requirements", "test-cases", "automation"].includes(route.destination) &&
									!(requirementsRoute && (requirements.length || imports.preview)) &&
									!savedCaseLink &&
									!(testReviewRoute && testCases.length) && (
										<GenerationGuidance
											key={`${knowledgeScope}:${route.destination}:${currentProjectRevision}`}
											request={apiRequest}
											projectId={navigationProjectId}
											stage={route.destination.replaceAll("-", "_")}
											compact={compactReview}
										/>
									)}

								{!savedFileReportLink && (
									<OrchestratorCockpitPanel
										currentProject={currentProject}
										status={orchestratorStatus}
										currentDestination={route.destination}
										hasTestCases={hasExistingTestCaseBaseline}
										hidden={
											viewingSavedCase ||
											(route.destination === PROJECT_DESTINATIONS.TEST_CASES && activeTab === 2) ||
											(requirementsRoute && Boolean(imports.preview)) ||
											inputsSources
										}
										compact={compactReview}
										hidePrimary={Boolean(
											(replacesLegacyGeneration && contextualTestCaseTask?.action === "generate") ||
											(reviewPendingRequirementCount &&
												requirementReviewTask?.action === "approve" &&
												requirementReviewTask.enabled &&
												!requirementReviewTask.blockers?.length)
										)}
										isLoading={isLoadingOrchestrator}
										error={orchestratorError}
										authActionDisabled={authActionDisabled}
										actionBusy={orchestratorActionBusy}
										actionDisabled={orchestratorActionDisabled}
										focusFallbackRef={workflowMainRef}
										onAction={handleOrchestratorAction}
									/>
								)}

								<div className={inputsSources && !imports.preview ? "tab-content project-intake" : "tab-content"}>
									{activeTab === 0 && !reviewScope && (
										<section className="panel requirement-workspace" id="requirement-import-setup" hidden={!requirementSetupOpen}>
											<h2 className="panel-title">Upload Requirements</h2>
											<p className="panel-description">
												Import requirements, compare them with the current project, and apply reviewed changes. Existing requirements remain
												until you apply an update.
											</p>
											{imports.extractionError && !requirementExtraction.managed && (
												<Alert tone="danger" role="alert">
													{imports.extractionError}
												</Alert>
											)}
											<div className="choice-group source-choice-group" role="radiogroup" aria-label="Requirement source selector">
												{REQUIREMENT_SOURCE_OPTIONS.map((option) => (
													<label key={option.value} className={`choice-card ${requirementSourceMode === option.value ? "selected" : ""}`}>
														<Radio
															type="radio"
															name="requirement-source"
															value={option.value}
															checked={requirementSourceMode === option.value}
															onChange={() => {
																setRequirementSourceMode(option.value);
																imports.setExtractionError("");
															}}
														/>
														<span className="choice-card-copy">
															<span className="choice-card-title">{option.label}</span>
														</span>
													</label>
												))}
											</div>

											{requirementSourceMode === "file" ? (
												<div className="panel-form">
													<Field className="form-group">
														<label>Requirements file</label>
														<Input type="file" accept=".md,.docx,.xlsx" onChange={(e) => setFile(e.target.files?.[0] || null)} />
													</Field>
													<Button
														onClick={() => parseRequirements(false)}
														disabled={!file || isParsing || requirementActionDisabled || requirementIntakeBlocked}
													>
														{showParsingProgress ? "⏳ Parsing..." : "Parse Requirements"}
													</Button>
												</div>
											) : requirementSourceMode === "paste" ? (
												<div className="panel-form">
													<label htmlFor="pasted-requirements">Requirements text</label>
													<Textarea
														id="pasted-requirements"
														rows={9}
														value={pasteDraft.value}
														onChange={(e) => pasteDraft.setValue(e.target.value)}
														placeholder="Paste business rules, acceptance criteria or a requirement document…"
													/>
													<p className="helper-text">
														Your text becomes a named source. Review the comparison before applying it to the project.
													</p>
													<DraftRecoveryNotice draft={pasteDraft} />
													<Button
														onClick={() => parseRequirements(false)}
														disabled={!pasteDraft.value.trim() || isParsing || requirementActionDisabled || requirementIntakeBlocked}
													>
														{showParsingProgress ? "Parsing…" : "Parse Requirements"}
													</Button>
												</div>
											) : requirementSourceMode === "jira" ? (
												<div className="jira-workflow-panel">
													<div className="jira-card">
														<div className="jira-card-header">
															<div>
																<h3>Import from JIRA</h3>
																<p>
																	Pick a project, search for an epic, and import the epic plus child issues into the requirement parser.
																</p>
															</div>
														</div>
														<div className="panel-form two-cols jira-search-grid">
															<Field className="form-group">
																<label>Project search</label>
																<Input
																	placeholder="Search JIRA projects"
																	value={jiraProjectQuery}
																	onChange={(event) => setJiraProjectQuery(event.target.value)}
																	disabled={!jiraConnected}
																/>
															</Field>
															<div className="form-group jira-inline-action">
																<label>Project</label>
																<div className="jira-inline-controls">
																	<Select
																		value={selectedJiraProjectKey}
																		onChange={(event) => setSelectedJiraProjectKey(event.target.value)}
																		disabled={!jiraConnected || !jiraProjects.length}
																	>
																		<option value="">Select a JIRA project</option>
																		{jiraProjects.map((project) => (
																			<option key={project.project_id || project.key} value={project.key}>
																				{project.key} — {project.name}
																			</option>
																		))}
																	</Select>
																	<Button
																		className="secondary"
																		onClick={() => loadJiraProjects(jiraProjectQuery)}
																		disabled={!jiraConnected || isLoadingJiraProjects}
																	>
																		{isLoadingJiraProjects ? "⏳" : "Load"}
																	</Button>
																</div>
															</div>
															<Field className="form-group">
																<label>Issue type</label>
																<Select
																	value={jiraIssueType}
																	onChange={(event) => setJiraIssueType(event.target.value)}
																	disabled={!jiraConnected || isLoadingJiraIssueTypes}
																>
																	<option value="">Any issue type</option>
																	{jiraIssueTypeOptions.map((issueTypeName) => (
																		<option key={issueTypeName} value={issueTypeName}>
																			{issueTypeName}
																		</option>
																	))}
																</Select>
															</Field>
															<div className="form-group jira-inline-action">
																<label>Issue search</label>
																<div className="jira-inline-controls">
																	<Input
																		placeholder={`Search ${(jiraIssueType || "issue").toLowerCase()} summaries`}
																		value={jiraIssueQuery}
																		onChange={(event) => setJiraIssueQuery(event.target.value)}
																		disabled={!jiraConnected}
																	/>
																	<Button
																		className="secondary"
																		onClick={searchJiraIssues}
																		disabled={!jiraConnected || !selectedJiraProjectKey || isSearchingJiraIssues}
																	>
																		{isSearchingJiraIssues ? "⏳" : "Search"}
																	</Button>
																</div>
															</div>
														</div>

														{jiraIssueResults.length > 0 ? (
															<TableScroll
																className="selection-table-wrapper"
																role="region"
																aria-label="Jira issue search results table"
																tabIndex={0}
															>
																<Table className="selection-table">
																	<thead>
																		<tr>
																			<th scope="col">Select</th>
																			<th scope="col">Issue</th>
																			<th scope="col">Summary</th>
																			<th scope="col">Type</th>
																			<th scope="col">Status</th>
																			<th scope="col">Parent</th>
																		</tr>
																	</thead>
																	<tbody>
																		{jiraIssueResults.map((issue) => {
																			const selected = selectedJiraIssueKey === issue.key;
																			return (
																				<tr
																					key={issue.issue_id || issue.key}
																					className={selected ? "selected" : ""}
																					onClick={() => setSelectedJiraIssueKey(issue.key)}
																				>
																					<td>
																						<Radio
																							type="radio"
																							name="jira-issue-selection"
																							checked={selected}
																							onChange={() => setSelectedJiraIssueKey(issue.key)}
																							aria-label={`Select JIRA issue ${issue.key}`}
																						/>
																					</td>
																					<td>
																						<strong>{issue.key}</strong>
																					</td>
																					<td>{issue.summary}</td>
																					<td>{issue.issue_type || "—"}</td>
																					<td>{issue.status || "—"}</td>
																					<td>{issue.parent_key || "—"}</td>
																				</tr>
																			);
																		})}
																	</tbody>
																</Table>
															</TableScroll>
														) : (
															<span className="helper-text">Search visible issues in the selected project to choose an import source.</span>
														)}

														<div className="panel-form button-row jira-import-actions">
															<Button
																onClick={importRequirementsFromJira}
																disabled={
																	!selectedJiraIssueKey || isImportingFromJira || requirementActionDisabled || requirementIntakeBlocked
																}
															>
																{isImportingFromJira && !intakeInterrupted
																	? "⏳ Importing..."
																	: `Import ${selectedJiraIssue?.key || jiraIssueType || "issue"}`}
															</Button>
															{selectedJiraIssue ? (
																<span className="helper-text">
																	Selected source: {selectedJiraIssue.key} — {selectedJiraIssue.summary}
																</span>
															) : null}
														</div>
													</div>
												</div>
											) : (
												<div className="jira-workflow-panel">
													<div className="jira-card">
														<div className="jira-card-header">
															<div>
																<h3>Import from Azure DevOps</h3>
																<p>
																	Pick a project, search work items, and import the selected item plus children into the requirement parser.
																</p>
															</div>
														</div>
														<div className="panel-form two-cols jira-search-grid">
															<Field className="form-group">
																<label>Project search</label>
																<Input
																	placeholder="Search Azure DevOps projects"
																	value={azureDevOpsProjectQuery}
																	onChange={(event) => setAzureDevOpsProjectQuery(event.target.value)}
																	disabled={!azureDevOpsConnected}
																/>
															</Field>
															<div className="form-group jira-inline-action">
																<label>Project</label>
																<div className="jira-inline-controls">
																	<Select
																		value={selectedAzureDevOpsProject}
																		onChange={(event) => setSelectedAzureDevOpsProject(event.target.value)}
																		disabled={!azureDevOpsConnected || !azureDevOpsProjects.length}
																	>
																		<option value="">Select an Azure DevOps project</option>
																		{azureDevOpsProjects.map((project) => (
																			<option key={project.project_id || project.name} value={project.name}>
																				{project.name}
																			</option>
																		))}
																	</Select>
																	<Button
																		className="secondary"
																		onClick={() => loadAzureDevOpsProjects(azureDevOpsProjectQuery)}
																		disabled={!azureDevOpsConnected || isLoadingAzureDevOpsProjects}
																	>
																		{isLoadingAzureDevOpsProjects ? "⏳" : "Load"}
																	</Button>
																</div>
															</div>
															<Field className="form-group">
																<label>Work item type</label>
																<Select
																	value={azureDevOpsWorkItemType}
																	onChange={(event) => setAzureDevOpsWorkItemType(event.target.value)}
																	disabled={!azureDevOpsConnected || isLoadingAzureDevOpsWorkItemTypes}
																>
																	<option value="">Any work item type</option>
																	{azureDevOpsWorkItemTypeOptions.map((workItemTypeName) => (
																		<option key={workItemTypeName} value={workItemTypeName}>
																			{workItemTypeName}
																		</option>
																	))}
																</Select>
															</Field>
															<div className="form-group jira-inline-action">
																<label>Work item search</label>
																<div className="jira-inline-controls">
																	<Input
																		placeholder={`Search ${(azureDevOpsWorkItemType || "work item").toLowerCase()} titles/descriptions`}
																		value={azureDevOpsWorkItemQuery}
																		onChange={(event) => setAzureDevOpsWorkItemQuery(event.target.value)}
																		disabled={!azureDevOpsConnected}
																	/>
																	<Button
																		className="secondary"
																		onClick={searchAzureDevOpsWorkItems}
																		disabled={!azureDevOpsConnected || !selectedAzureDevOpsProject || isSearchingAzureDevOpsWorkItems}
																	>
																		{isSearchingAzureDevOpsWorkItems ? "⏳" : "Search"}
																	</Button>
																</div>
															</div>
														</div>

														{azureDevOpsWorkItemResults.length > 0 ? (
															<TableScroll
																className="selection-table-wrapper"
																role="region"
																aria-label="Azure DevOps work item search results table"
																tabIndex={0}
															>
																<Table className="selection-table">
																	<thead>
																		<tr>
																			<th scope="col">Select</th>
																			<th scope="col">Work item</th>
																			<th scope="col">Title</th>
																			<th scope="col">Type</th>
																			<th scope="col">State</th>
																			<th scope="col">Parent</th>
																		</tr>
																	</thead>
																	<tbody>
																		{azureDevOpsWorkItemResults.map((workItem) => {
																			const selected = `${selectedAzureDevOpsWorkItemId}` === `${workItem.work_item_id}`;
																			return (
																				<tr
																					key={workItem.work_item_id}
																					className={selected ? "selected" : ""}
																					onClick={() => setSelectedAzureDevOpsWorkItemId(`${workItem.work_item_id}`)}
																				>
																					<td>
																						<Radio
																							type="radio"
																							name="azure-devops-work-item-selection"
																							checked={selected}
																							onChange={() => setSelectedAzureDevOpsWorkItemId(`${workItem.work_item_id}`)}
																							aria-label={`Select Azure DevOps work item ${workItem.work_item_id}`}
																						/>
																					</td>
																					<td>
																						<strong>#{workItem.work_item_id}</strong>
																					</td>
																					<td>{workItem.title}</td>
																					<td>{workItem.work_item_type || "—"}</td>
																					<td>{workItem.state || "—"}</td>
																					<td>{workItem.parent_id ? `#${workItem.parent_id}` : "—"}</td>
																				</tr>
																			);
																		})}
																	</tbody>
																</Table>
															</TableScroll>
														) : (
															<span className="helper-text">
																Search visible work items in the selected project to choose an import source.
															</span>
														)}

														<div className="panel-form button-row jira-import-actions">
															<Button
																onClick={importRequirementsFromAzureDevOps}
																disabled={
																	!selectedAzureDevOpsWorkItemId ||
																	isImportingFromAzureDevOps ||
																	requirementActionDisabled ||
																	requirementIntakeBlocked
																}
															>
																{isImportingFromAzureDevOps && !intakeInterrupted
																	? "⏳ Importing..."
																	: `Import ${selectedAzureDevOpsWorkItem ? `#${selectedAzureDevOpsWorkItem.work_item_id}` : azureDevOpsWorkItemType || "work item"}`}
															</Button>
															{selectedAzureDevOpsWorkItem ? (
																<span className="helper-text">
																	Selected source: #{selectedAzureDevOpsWorkItem.work_item_id} — {selectedAzureDevOpsWorkItem.title}
																</span>
															) : null}
														</div>
													</div>
												</div>
											)}

											{rawText && (
												<div className="result-section compact-result-section">
													<Disclosure className="collapsible-panel raw-text-panel">
														<summary className="collapsible-panel-summary">
															<span className="collapsible-panel-copy">
																<span className="collapsible-panel-title">Raw extracted text</span>
																<span className="collapsible-panel-description">Open only when you need to inspect parser input.</span>
															</span>
															<span className="collapsible-panel-meta">
																<span className="analysis-summary-pill">{rawText.length.toLocaleString()} chars</span>
																<span className="collapsible-panel-icon" aria-hidden="true">
																	⏄
																</span>
															</span>
														</summary>
														<div className="collapsible-panel-body">
															<pre className="raw-text-pre" role="region" aria-label="Raw extracted requirements text" tabIndex={0}>
																{rawText}
															</pre>
														</div>
													</Disclosure>
												</div>
											)}
										</section>
									)}
									{activeTab === 0 && !reviewScope && imports.preview && (
										<>
											<RequirementImportReview
												key={`${imports.preview.import_id}:${imports.preview.base_project_revision}`}
												preview={imports.preview}
												identity={PROJECT_WORKSPACES_V2 ? currentUser?.sub : ""}
												revision={currentProjectRevision}
												busy={imports.busy}
												error={imports.error}
												stalePreview={imports.stalePreview}
												onAction={reviewRequirementImport}
											/>
											{imports.preview.guidance && (
												<GuidanceUsed manifest={imports.preview.guidance} request={apiRequest} projectId={currentProjectId} />
											)}
										</>
									)}
									{reviewScope && imports.preview && (
										<p role="status">
											A source comparison is open. Finish or cancel it in{" "}
											<RouteLink to={buildProjectWorkspacePath(navigationProjectId, "inputs")} navigate={navigate}>
												Inputs
											</RouteLink>{" "}
											before reviewing the scope.
										</p>
									)}
									{activeTab === 0 && !inputsSources && !imports.preview && (
										<section className="panel requirement-workspace">
											{imports.error && <p role="alert">{imports.error}</p>}
											{imports.message && <p role="status">{imports.message}</p>}
											<RequirementReviewWorkbench
												key={navigationProjectId}
												onModuleReady={focusRequirementTarget}
												headingRef={requirementReviewHeading}
												reviewRequest={requirementReviewRequest?.projectId === navigationProjectId ? requirementReviewRequest : null}
												requirements={requirements}
												scoped={reviewScope}
												approvedRequirementCount={approvedRequirementCount}
												loadHistory={(uid) => imports.read(`/projects/${currentProjectId}/requirements/${uid}/history`)}
												retiredRequirements={currentProject?.current_snapshots?.requirements?.payload?.retired_requirements || []}
												busy={isSavingRequirementReview}
												reviewPendingRequirementCount={reviewPendingRequirementCount}
												rejectedRequirementCount={rejectedRequirementCount}
												onApproveNonRejected={() =>
													bulkUpdateRequirementReviewStatus(
														"Approved",
														(requirement) =>
															getRequirementReviewStatus(requirement) !== "Rejected" &&
															(!reviewScope || !["open", "deferred", "excluded"].includes(requirement.clarification_status))
													)
												}
												onMarkAllNeedsReview={() => bulkUpdateRequirementReviewStatus("Needs Review")}
												onReviewStatusChange={updateRequirementReviewStatus}
												onQualityFlagToggle={toggleRequirementQualityFlag}
											/>

											{reviewScope && (
												<ProjectSyncPanel
													key={`scope-sync-${navigationProjectId}`}
													project={currentProject}
													request={apiRequest}
													onUpdated={() => openProject(navigationProjectId, { silent: true })}
												/>
											)}

											{hasJiraRequirements && (
												<div className="jira-sync-panel">
													<div className="jira-card-header">
														<div>
															<h3>JIRA Sync Preview</h3>
															<p>
																Preview the managed requirement block that will be written back to JIRA and then push ready updates
																explicitly.
															</p>
														</div>
														<div className="jira-connection-summary compact">
															{jiraImportedIssueKeys.map((issueKey) => (
																<span key={issueKey} className="jira-summary-pill">
																	{issueKey}
																</span>
															))}
														</div>
													</div>
													<div className="panel-form two-cols jira-sync-controls">
														<Field className="form-group">
															<label>Managed section title</label>
															<Input
																value={jiraManagedSectionTitle}
																onChange={(event) => setJiraManagedSectionTitle(event.target.value)}
																placeholder={DEFAULT_JIRA_SYNC_SECTION_TITLE}
															/>
														</Field>
														<div className="feedback-actions jira-sync-actions">
															<Button
																className="secondary"
																onClick={() => previewJiraSync()}
																disabled={authActionDisabled || isPreviewingJiraSync || isApplyingJiraSync}
															>
																{isPreviewingJiraSync ? "⏳ Previewing..." : "Preview JIRA Update"}
															</Button>
															<Button
																onClick={applyJiraSync}
																disabled={authActionDisabled || isApplyingJiraSync || !jiraSyncPreview || !jiraPreviewHasReadyIssue}
															>
																{isApplyingJiraSync ? "⏳ Syncing..." : "Push Ready Updates"}
															</Button>
														</div>
													</div>

													{jiraSyncPreview && (
														<div className="jira-sync-results">
															<div className="workflow-diagnostics-pills">
																<span className="workflow-diagnostics-pill">Ready {jiraSyncPreview.ready_issue_count || 0}</span>
																<span className="workflow-diagnostics-pill">Conflicts {jiraSyncPreview.conflict_count || 0}</span>
																<span className="workflow-diagnostics-pill">
																	Skipped {(jiraSyncPreview.skipped_requirement_ids || []).length}
																</span>
															</div>
															<List as="div" variant="grouped" className="jira-sync-preview-list">
																{jiraSyncPreview.issues?.map((issue) => (
																	<div key={issue.issue_key} className={`jira-sync-preview-card ${issue.status}`}>
																		<div className="jira-sync-preview-header">
																			<div>
																				<strong>{issue.issue_key}</strong>
																				<span>{issue.issue_type || "Issue"}</span>
																			</div>
																			<span className={`jira-status-badge ${issue.status}`}>{issue.status}</span>
																		</div>
																		<div className="jira-sync-preview-meta">
																			<span>Requirements: {(issue.requirement_ids || []).join(", ") || "—"}</span>
																			{issue.issue_url ? (
																				<Link href={issue.issue_url} target="_blank" rel="noreferrer">
																					Open in JIRA ↗
																				</Link>
																			) : null}
																		</div>
																		{issue.conflict_reason ? <p className="jira-sync-preview-warning">{issue.conflict_reason}</p> : null}
																		{issue.warning ? <p className="jira-sync-preview-warning">{issue.warning}</p> : null}
																		<div className="jira-sync-preview-excerpts">
																			<div>
																				<h4>Current description</h4>
																				<p>{issue.existing_description_excerpt || "No description yet."}</p>
																			</div>
																			<div>
																				<h4>Rendered update</h4>
																				<p>{issue.rendered_description_excerpt || "No rendered update available."}</p>
																			</div>
																		</div>
																	</div>
																))}
															</List>
															{jiraSyncPreview.warnings?.length > 0 && (
																<List className="jira-sync-warning-list">
																	{jiraSyncPreview.warnings.map((warning) => (
																		<ListItem key={warning}>{warning}</ListItem>
																	))}
																</List>
															)}
														</div>
													)}

													{jiraSyncResults && (
														<div className="jira-sync-results-summary">
															<h4>Last sync result</h4>
															<List className="jira-sync-apply-list">
																{jiraSyncResults.results?.map((result) => (
																	<ListItem key={`${result.issue_key}-${result.status}`}>
																		<strong>{result.issue_key}</strong> — {result.status}
																		{result.message ? `: ${result.message}` : ""}
																	</ListItem>
																))}
															</List>
														</div>
													)}
												</div>
											)}

											{hasAzureDevOpsRequirements && (
												<div className="jira-sync-panel">
													<div className="jira-card-header">
														<div>
															<h3>Azure DevOps Sync Preview</h3>
															<p>
																Preview the managed requirements block that will be written back to Azure DevOps work item descriptions.
															</p>
														</div>
														<div className="jira-connection-summary compact">
															{azureDevOpsImportedWorkItemIds.map((workItemId) => (
																<span key={workItemId} className="jira-summary-pill">
																	#{workItemId}
																</span>
															))}
														</div>
													</div>
													<div className="panel-form two-cols jira-sync-controls">
														<Field className="form-group">
															<label>Managed section title</label>
															<Input
																value={azureDevOpsManagedSectionTitle}
																onChange={(event) => setAzureDevOpsManagedSectionTitle(event.target.value)}
																placeholder={DEFAULT_AZURE_DEVOPS_SYNC_SECTION_TITLE}
															/>
														</Field>
														<div className="feedback-actions jira-sync-actions">
															<Button
																className="secondary"
																onClick={() => previewAzureDevOpsSync()}
																disabled={authActionDisabled || isPreviewingAzureDevOpsSync || isApplyingAzureDevOpsSync}
															>
																{isPreviewingAzureDevOpsSync ? "⏳ Previewing..." : "Preview Azure DevOps Update"}
															</Button>
															<Button
																onClick={applyAzureDevOpsSync}
																disabled={
																	authActionDisabled ||
																	isApplyingAzureDevOpsSync ||
																	!azureDevOpsSyncPreview ||
																	!azureDevOpsPreviewHasReadyWorkItem
																}
															>
																{isApplyingAzureDevOpsSync ? "⏳ Syncing..." : "Push Ready Updates"}
															</Button>
														</div>
													</div>

													{azureDevOpsSyncPreview && (
														<div className="jira-sync-results">
															<div className="workflow-diagnostics-pills">
																<span className="workflow-diagnostics-pill">Ready {azureDevOpsSyncPreview.ready_work_item_count || 0}</span>
																<span className="workflow-diagnostics-pill">Conflicts {azureDevOpsSyncPreview.conflict_count || 0}</span>
																<span className="workflow-diagnostics-pill">
																	Skipped {(azureDevOpsSyncPreview.skipped_requirement_ids || []).length}
																</span>
															</div>
															<List as="div" variant="grouped" className="jira-sync-preview-list">
																{azureDevOpsSyncPreview.work_items?.map((workItem) => (
																	<div key={workItem.work_item_id} className={`jira-sync-preview-card ${workItem.status}`}>
																		<div className="jira-sync-preview-header">
																			<div>
																				<strong>#{workItem.work_item_id}</strong>
																				<span>{workItem.work_item_type || "Work Item"}</span>
																			</div>
																			<span className={`jira-status-badge ${workItem.status}`}>{workItem.status}</span>
																		</div>
																		<div className="jira-sync-preview-meta">
																			<span>Requirements: {(workItem.requirement_ids || []).join(", ") || "—"}</span>
																			{workItem.work_item_url ? (
																				<Link href={workItem.work_item_url} target="_blank" rel="noreferrer">
																					Open in Azure DevOps ↗
																				</Link>
																			) : null}
																		</div>
																		{workItem.conflict_reason ? (
																			<p className="jira-sync-preview-warning">{workItem.conflict_reason}</p>
																		) : null}
																		{workItem.warning ? <p className="jira-sync-preview-warning">{workItem.warning}</p> : null}
																		<div className="jira-sync-preview-excerpts">
																			<div>
																				<h4>Current description</h4>
																				<p>{workItem.existing_description_excerpt || "No description yet."}</p>
																			</div>
																			<div>
																				<h4>Rendered update</h4>
																				<p>{workItem.rendered_description_excerpt || "No rendered update available."}</p>
																			</div>
																		</div>
																	</div>
																))}
															</List>
															{azureDevOpsSyncPreview.warnings?.length > 0 && (
																<List className="jira-sync-warning-list">
																	{azureDevOpsSyncPreview.warnings.map((warning) => (
																		<ListItem key={warning}>{warning}</ListItem>
																	))}
																</List>
															)}
														</div>
													)}

													{azureDevOpsSyncResults && (
														<div className="jira-sync-results-summary">
															<h4>Last sync result</h4>
															<List className="jira-sync-apply-list">
																{azureDevOpsSyncResults.results?.map((result) => (
																	<ListItem key={`${result.work_item_id}-${result.status}`}>
																		<strong>#{result.work_item_id}</strong> — {result.status}
																		{result.message ? `: ${result.message}` : ""}
																	</ListItem>
																))}
															</List>
														</div>
													)}
												</div>
											)}

											{projectSnapshots.requirements?.payload?.schema_version !== 2 && renderRequirementReviewReport()}

											{guidanceSummary("requirements")}
											{suggestionSummary("requirements")}
											{requirements.length > 0 && (
												<Disclosure className="feedback-section">
													<summary>Human Feedback</summary>
													<p className="feedback-description">
														Provide feedback on the extracted requirements. The AI will refine them based on your input.
													</p>
													<Textarea
														className="feedback-textarea"
														placeholder="Enter your feedback here... e.g., 'Merge REQ-003 and REQ-004 into one', 'Split REQ-001 into multiple requirements', 'REQ-005 is too vague, make it more specific', 'Add a requirement for error handling', etc."
														value={reqFeedback}
														onChange={(e) => setReqFeedback(e.target.value)}
														rows={4}
													/>
													<div className="feedback-actions">
														<Button
															onClick={() => {
																if (reviewScope) navigate(buildProjectWorkspacePath(navigationProjectId, "inputs"));
																void parseRequirements(true);
															}}
															disabled={!reqFeedback.trim() || isParsing || requirementActionDisabled || requirementIntakeBlocked}
															className="feedback-button"
														>
															{showParsingProgress ? "⏳ Refining Requirements..." : "🔄 Implement Changes"}
														</Button>
													</div>
												</Disclosure>
											)}
										</section>
									)}

									{activeTab === 0 && !inputsSources && (requirements.length > 0 || imports.preview) && (
										<div className="workbench-support">
											<GenerationGuidance
												compact
												key={`${knowledgeScope}:requirements:${currentProjectRevision}`}
												request={apiRequest}
												projectId={navigationProjectId}
												stage="requirements"
											/>
										</div>
									)}

									{inputsSources && imports.error && <p role="alert">{imports.error}</p>}
									{inputsSources && imports.message && <p role="status">{imports.message}</p>}
									{inputsSources && (
										<TestingIntentPanel
											project={currentProject}
											identity={currentUser?.sub || ""}
											onSave={saveTestingIntent}
											disabled={authActionDisabled || Boolean(imports.preview) || isAnalyzingContext}
										/>
									)}
									{(activeTab === 1 || inputsSources) && (
										<ContextInputsPanel
											project={currentProject}
											request={apiRequest}
											projectId={navigationProjectId}
											identity={currentUser?.sub}
											executionFindingsLink={executionFindingsLink}
											prerequisiteSearch={prerequisiteSearch}
											focusQualityFeedback={location.hash === "#quality-feedback-heading"}
											onProjectUpdated={() => openProject(navigationProjectId, { silent: true })}
											{...visibleContextForm}
											setAppLink={PROJECT_WORKSPACES_V2 ? setContextDraftField("appLink") : setAppLink}
											setPrototypeLink={PROJECT_WORKSPACES_V2 ? setContextDraftField("prototypeLink") : setPrototypeLink}
											setDiagramLinks={PROJECT_WORKSPACES_V2 ? setContextDraftField("diagramLinks") : setDiagramLinks}
											setImageLinks={PROJECT_WORKSPACES_V2 ? setContextDraftField("imageLinks") : setImageLinks}
											setContextNotes={PROJECT_WORKSPACES_V2 ? setContextDraftField("contextNotes") : setContextNotes}
											contextDraft={PROJECT_WORKSPACES_V2 ? contextDraft : null}
											contextDraftChanged={contextDraftChanged}
											contextStale={Boolean(projectStageState.context?.stale)}
											contextSaveBlocked={PROJECT_WORKSPACES_V2 && Boolean(imports.preview)}
											embedded={inputsSources}
											hasContextInputs={hasContextInputs}
											analyzeContext={analyzeContext}
											isAnalyzingContext={isAnalyzingContext}
											contextAnalysisError={contextAnalysisError}
											authActionDisabled={authActionDisabled}
											enrichedContext={PROJECT_WORKSPACES_V2 ? contextSnapshot?.payload : enrichedContext}
											resetContextAnalysis={resetContextAnalysis}
											setSelectedArtifactSourceIds={
												PROJECT_WORKSPACES_V2 ? setContextDraftField("selectedArtifactSourceIds") : setSelectedArtifactSourceIds
											}
											goPrev={goPrev}
											goNext={PROJECT_WORKSPACES_V2 ? openScope : goNext}
										/>
									)}

									{activeTab === 2 && !savedCaseLink && (
										<TemplateSetupPanel
											templateName={templateName}
											setTemplateName={setTemplateName}
											templateFormat={templateFormat}
											setTemplateFormat={setTemplateFormat}
											onReturn={() => selectWorkflowTab(3)}
										/>
									)}

									{savedCaseLink && isAuthenticated && currentUser?.sub && (
										<LinkedTestCaseView
											key={JSON.stringify([currentUser.sub, navigationProjectId, savedCaseLink.snapshotId, savedCaseLink.caseId])}
											projectId={navigationProjectId}
											{...savedCaseLink}
											savedStep={readSavedCaseStep(location.hash)}
											request={apiRequest}
											navigate={navigate}
										/>
									)}

									{activeTab === 3 && !savedCaseLink && (
										<section className={`panel test-generation-panel ${testCases.length ? "has-test-cases" : ""}`}>
											{!testCases.length && <h2 className="panel-title">Generate Test Cases</h2>}
											{suiteQualitySummary}
											<ScopedImpactStatus
												application={scopedImpact}
												projectId={currentProjectId}
												analysisId={impactAnalysisId}
												revision={currentProjectRevision}
												navigate={navigate}
												disabled={testCaseActionDisabled || impactApplyBlockedByApproval}
											/>
											{repairReviewRoute && <SelectedCaseRepairStatus repair={selectedRepair} currentSnapshotId={repairSnapshotId} />}
											{repairReviewRoute && selectedRepair.result && (
												<SavedRepairEvidence
													key={JSON.stringify([currentUser.sub, currentProjectId, selectedRepair.result.request_id])}
													result={selectedRepair.result}
													projectId={currentProjectId}
													request={apiRequest}
													navigate={navigate}
												/>
											)}
											{!replacesLegacyGeneration && (
												<TestCaseReadiness
													project={currentProject}
													status={orchestratorStatus}
													unavailable={Boolean(orchestratorError) || isLoadingOrchestrator}
													caseCount={testCases.length}
													taskCount={generationTasks.length}
													onReview={selectWorkflowTab}
												/>
											)}
											{savedReviewRecovery && (savedReview.attempt || savedSuiteReviewPending) && (
												<section className="generation-gate-card" aria-label="Saved suite review recovery">
													<div>
														<h3>Saved suite review recovery</h3>
														<p role="status">{savedReviewStatusMessage(savedReview, sharedDraftBusy)}</p>
														{savedReview.attempt &&
															(savedReview.attempt.source_snapshot_id !== savedSuite?.snapshot_id || testCaseStageState.stale) && (
																<p>This attempt uses an earlier source revision.</p>
															)}
														{savedReview.attempt && (
															<Disclosure>
																<summary>Saved review details</summary>
																<p>
																	Saved suite <code>{savedReview.attempt.source_snapshot_id}</code>
																</p>
																<p>
																	Request <code>{savedReview.attempt.request_id}</code>
																</p>
															</Disclosure>
														)}
														{(savedReview.error || savedReview.statusError) && (
															<p role="alert">{savedReview.error || savedReview.statusError}</p>
														)}
													</div>
													<div className="workbench-actions">
														<Button
															variant="secondary"
															size="compact"
															disabled={savedReview.busy || savedSuiteReviewState?.busy}
															onClick={() => void checkSavedSuiteReview(captureProjectOperationScope())}
														>
															Check saved review status
														</Button>
														{savedReview.attempt && (
															<Button
																variant="secondary"
																size="compact"
																disabled={Boolean(
																	savedReview.busy || savedReview.statusError || savedReview.error || savedSuiteReviewState?.busy
																)}
																onClick={() => void checkSavedSuiteReview(captureProjectOperationScope(), { release: true })}
															>
																{savedReview.attempt.state === "interrupted" ? "Acknowledge and release" : "Cancel saved review"}
															</Button>
														)}
													</div>
												</section>
											)}
											{showSyncDraftReview && !savedSuiteReviewPending && (
												<section className="generation-gate-card blocked" aria-label="Project Sync suite review">
													<div role="status">
														<strong>
															{syncDraftSuperseded
																? "Earlier Sync draft"
																: syncDraftNeedsScenarioApproval
																	? "Sync suite awaits scenario approval"
																	: "Sync draft saved"}
														</strong>
														<p>
															{syncDraftSuperseded
																? "These cases are saved evidence from an earlier project update."
																: syncDraftNeedsScenarioApproval
																	? "Approve the matching scenario plan before reviewing the saved suite."
																	: "Independent review requires verified evidence for this draft and its current sources."}
														</p>
													</div>
													{syncDraftNeedsScenarioApproval ? (
														<RouteLink to={scenariosPath} navigate={navigate}>
															Review matching scenarios
														</RouteLink>
													) : (
														testCases.length > 0 && (
															<Button variant="secondary" size="compact" onClick={() => openCaseResults()}>
																View saved cases
															</Button>
														)
													)}
												</section>
											)}
											{savedSuiteReviewPending && (
												<section
													className={`generation-gate-card ${savedSuiteInputsReady ? "ready" : "blocked"}`}
													aria-label={syncDraftSuite ? "Project Sync suite review" : undefined}
												>
													<div role="status">
														<strong>
															{savedSuiteReviewState?.busy
																? "Reviewing saved test cases…"
																: syncDraftSuite
																	? "Sync suite ready for independent review"
																	: "Saved suite needs review"}
														</strong>
														<p>
															{savedSuiteInputsReady
																? syncDraftSuite
																	? "Assess the saved cases against the approved scenarios and accepted correction. The saved steps and case versions stay the same."
																	: "Check the saved cases against current requirements and Use Cases. The case steps and versions stay the same."
																: "Review current Requirements and Use Cases or resolve upstream changes first."}
														</p>
													</div>
													{savedSuiteReviewState?.error && <p role="alert">{savedSuiteReviewState.error}</p>}
													<Button onClick={reviewSavedSuite} disabled={savedSuiteReviewDisabled || savedSuiteReviewState?.busy}>
														Review saved suite
													</Button>
												</section>
											)}
											{savedSuiteReviewed && (
												<div className={`generation-gate-card ${savedSuite.approved ? "ready" : "blocked"}`} role="status">
													<div>
														<strong>
															{syncSuiteReviewSaved
																? "Saved Sync suite review recorded."
																: savedSuite.approved
																	? "Saved suite review passed."
																	: "Saved suite review needs attention."}
														</strong>
														<p>{savedSuite.payload?.review?.summary}</p>
													</div>
												</div>
											)}
											<ReviewGroupSummary
												groups={savedSuiteReviewState?.reviewGroups ?? substantiveAssessment?.diagnostics?.groups}
												attempted={Boolean(savedSuiteReviewState?.reviewGroups)}
												stale={Boolean(testCaseStageState.stale)}
												onInspect={(group) => openCaseResults(group.caseIds[0], group)}
											/>
											{upstreamChangedForImpact && (
												<div className={`generation-gate-card ${canGenerateFromApprovedRequirements ? "ready" : "blocked"}`}>
													<div>
														<strong>
															{scopedImpact?.pending && !scopedImpact.submitting && scopedImpact.attempt?.state !== "running"
																? "Check pending impact application"
																: isApplyingImpactUpdate
																	? "Applying accepted recommendations"
																	: impactAnalysis && !impactStageState.stale
																		? "Review impact recommendations"
																		: "Existing suite needs impact analysis"}
														</strong>
														<p>
															{approvedRequirementCount} approved • {reviewPendingRequirementCount} pending review •{" "}
															{rejectedRequirementCount} rejected . The current suite is preserved while impact analysis reviews changed
															inputs.
														</p>
													</div>
												</div>
											)}
											{!contextualTestCaseTask &&
											allowLegacyTestCaseMutations &&
											(!hasExistingTestCaseBaseline || upstreamChangedForImpact) ? (
												<div className="panel-form button-row">
													{upstreamChangedForImpact ? (
														<Button onClick={analyzeImpact} disabled={!canAnalyzeImpact || isAnalyzingImpact || testCaseActionDisabled}>
															{isAnalyzingImpact
																? "⏳ Analyzing impact..."
																: impactChangedItemCount
																	? `Analyze Impact for ${impactChangedItemCount} Changed Item${impactChangedItemCount === 1 ? "" : "s"}`
																	: "Analyze Impact for Changed Items"}
														</Button>
													) : (
														<Button
															onClick={() => generateTestCases(false)}
															disabled={!canGenerateFromApprovedRequirements || isGenerating || testCaseActionDisabled}
														>
															{isGenerating ? "⏳ Generating..." : `Generate from ${approvedRequirementCount || 0} Approved`}
														</Button>
													)}
												</div>
											) : null}

											{generationTasks.length > 0 ? (
												<section className="compact-generation-tasks" aria-label="Unfinished test generation" role="status">
													<h3>
														{generationTasks.length} {generationTasks.length === 1 ? "generation task needs" : "generation tasks need"}{" "}
														concrete tests
													</h3>
													<Button
														onClick={analyzeImpact}
														disabled={!canAnalyzeImpact || isAnalyzingImpact || isApplyingImpactUpdate || testCaseActionDisabled}
													>
														{isAnalyzingImpact ? "Analyzing repair work…" : "Plan targeted repair"}
													</Button>
													<Disclosure>
														<summary>Show unfinished generation tasks</summary>
														<p>
															Concrete tests are retained. These coverage gaps need generation or prerequisite review before coverage is
															complete.
														</p>
														<ul>
															{generationTasks.map((task, index) => (
																<li key={index}>
																	{(task.requirement_ids || []).join(", ")}: {task.reason}
																</li>
															))}
														</ul>
														<p>Review and accept the resulting Add/Update recommendations, then apply them to generate concrete tests.</p>
													</Disclosure>
												</section>
											) : null}
											{renderImpactAnalysisPanel()}

											{hasGenerateResults ? (
												<div className="generate-results-workspace">
													<div className="generate-results-header">
														<div>
															<h3>Generation Results</h3>
															<p>Review the generated test cases and their requirement coverage.</p>
														</div>
														<span className="generate-results-summary-pill">
															{testCases.length} test case{testCases.length === 1 ? "" : "s"}
														</span>
													</div>
													<TabList className="generate-results-tabs" role="tablist" aria-label="Generation result sections">
														{generateResultTabs.map((tab) => (
															<Tab
																type="button"
																key={tab.id}
																className={`generate-result-tab ${activeGenerateResultTab === tab.id ? "active" : ""} ${tab.variant ? `generate-result-tab-${tab.variant}` : ""}`}
																onClick={() => setActiveGenerateResultTab(tab.id)}
																role="tab"
																aria-selected={activeGenerateResultTab === tab.id}
																aria-controls="generate-result-panel"
															>
																<span>{tab.label}</span>
																<span
																	className={`generate-result-tab-badge ${tab.variant ? `generate-result-tab-badge-${tab.variant}` : ""}`}
																>
																	{tab.badge}
																</span>
															</Tab>
														))}
													</TabList>
													<TabPanel
														id="generate-result-panel"
														className="generate-result-panel"
														role="tabpanel"
														aria-label={generateResultTabs.find((tab) => tab.id === activeGenerateResultTab)?.label || "Generation result"}
													>
														{(activeGenerateResultTab === "improve" || improveMountedScope === sourceCaseSnapshot) && (
															<div hidden={activeGenerateResultTab !== "improve"}>
																<ImproveTestsPanel
																	identity={currentUser?.sub}
																	projectId={currentProjectId}
																	snapshotId={sourceCaseSnapshot}
																	trustedRepairOnly={trustedRepairOnly}
																	reportedCase={reportedCase}
																	summary={currentProject?.current_snapshots?.test_cases?.payload?.scoped_refinement}
																	onClearReport={() => setReportedCase(null)}
																	key={sourceCaseSnapshot}
																	review={testCaseReview}
																	findings={testCaseFindings}
																	needsOnlySetup={testCasesNeedSetup}
																	assessment={substantiveAssessment}
																	grouped={PROJECT_WORKSPACES_V2 && testReviewRoute}
																	savedSuite={
																		savedSuite &&
																		savedSuite.project_id === currentProjectId &&
																		savedSuite.snapshot_id === testCaseStageState.current_snapshot_id
																			? savedSuite
																			: null
																	}
																	savedSuiteStale={Boolean(testCaseStageState.stale)}
																	navigate={navigate}
																	canReviewSavedSuite={savedSuiteReviewPending && !savedSuiteReviewDisabled}
																	prerequisiteSnapshotId={
																		projectStageState.test_cases?.stale ? null : currentProject?.current_snapshots?.test_cases?.snapshot_id
																	}
																	browserHandoffWarnings={browserHandoffWarnings}
																	meta={testCaseReviewMeta}
																	testCases={testCases}
																	busy={isGenerating}
																	disabled={
																		testCaseActionDisabled ||
																		upstreamChangedForImpact ||
																		!canGenerateFromApprovedRequirements ||
																		!testCases.length
																	}
																	stale={upstreamChangedForImpact}
																	blockedReason={
																		upstreamChangedForImpact
																			? "Upstream inputs changed. Review their impact before applying fixes."
																			: !testCases.length
																				? "Generate test cases before applying improvements."
																				: !canGenerateFromApprovedRequirements
																					? "Approve requirements before applying fixes."
																					: testCaseActionDisabled
																						? "Refinement is unavailable while another operation is running or your account is restricted."
																						: ""
																	}
																	onImpact={() => {
																		setActiveGenerateResultTab("test-cases");
																		document.querySelector(".generation-gate-card")?.scrollIntoView({ block: "center" });
																	}}
																	onApply={(feedbackText, selectedTestCaseIds) =>
																		generateTestCases(true, { feedbackText, selectedTestCaseIds })
																	}
																	onReload={async () => {
																		const project = await refreshCurrentProject({
																			hydrate: true,
																			operationScope: captureProjectOperationScope(),
																		});
																		if (project) {
																			setActiveGenerateResultTab("improve");
																		}
																		return Boolean(project);
																	}}
																	onViewCase={(id) => openCaseResults(id, null, { fromFinding: true })}
																	onReviewCases={() => openCaseResults()}
																/>
															</div>
														)}
														{activeGenerateResultTab === "traceability" && (
															<TraceabilityMatrixPanel
																requirementTraceabilityRows={requirementTraceabilityRows}
																tracedRequirementCount={tracedRequirementCount}
																linkedCaseCount={linkedCaseCount}
																coverageMetrics={coverageMetrics}
																testCases={testCases}
																projectId={currentProjectId}
																savedSuite={savedSuite}
																returnCase={traceabilityReturn}
																onOpenCase={openTraceabilityCase}
																onReturnHandled={() => {
																	setActiveGenerateResultTab("traceability");
																	setRestoredTraceabilityLocation(location);
																}}
															/>
														)}

														{activeGenerateResultTab === "test-cases" && (
															<>
																<GeneratedTestCasesView
																	identity={currentUser?.sub}
																	projectId={currentProjectId}
																	savedSuite={savedSuite}
																	snapshotId={sourceCaseSnapshot}
																	onReport={
																		trustedRepairOnly
																			? undefined
																			: (testCase) => {
																					setReportedCase(testCase);
																					setActiveGenerateResultTab("improve");
																					focusResultContent.current = true;
																				}
																	}
																	onReadCase={repairReviewRoute ? readSelectedRepairCase : undefined}
																	renderSelectedCaseRepair={(testCase, savedPath) =>
																		repairReviewRoute &&
																		repairSnapshotId &&
																		savedPath &&
																		testCase.artifact_version_id &&
																		!testCaseStageState.stale && (
																			<SelectedCaseRepair
																				key={`${repairSnapshotId}:${testCase.id}`}
																				repair={
																					repairReaderCaseId === testCase.id
																						? selectedRepair
																						: { ...selectedRepair, statusFresh: false, targets: [] }
																				}
																				disabled={
																					testCaseActionDisabled ||
																					isGenerating ||
																					savedSuiteReviewState?.busy ||
																					savedReview.attempt ||
																					sharedDraftBusy
																				}
																			/>
																		)
																	}
																	key={`${sourceCaseSnapshot}:${activeReviewCaseScope?.key || (changedTestCaseIds ? "changed" : "all")}`}
																	changedIds={changedTestCaseIds}
																	onClearChanged={() => setChangedTestCaseIds(null)}
																	reviewGroup={activeReviewCaseScope}
																	onClearReviewGroup={() => openCaseResults(selectedTestCaseId)}
																	testCases={testCases}
																	qualityIssues={testCaseReview?.blocking_issues || []}
																	requirements={requirements}
																	substantiveAssessment={substantiveAssessment}
																	selectedId={selectedPriorityCaseExpired ? null : selectedTestCaseId}
																	setSelectedId={setSelectedTestCaseId}
																	findingCaseId={findingCaseSelection?.source === sourceCaseSnapshot ? findingCaseSelection.id : null}
																	onDismissFindingCase={() => setFindingCaseSelection(null)}
																	priorityCaseId={priorityCase?.id}
																	focusPriorityCase={openingPriorityCase}
																	onPriorityCaseOpened={finishPriorityCaseEntry}
																	onDismissPriorityCase={dismissPriorityCase}
																/>
																<div className="workbench-support">
																	{guidanceSummary("test_cases")}
																	{suggestionSummary("test_cases")}
																</div>
															</>
														)}
													</TabPanel>
												</div>
											) : (
												<Surface as="div" className="result-section">
													<h3>Generated Test Cases</h3>
													<span className="helper-text">
														No generation run yet. Generate from approved requirements to view test cases and requirement traceability.
													</span>
												</Surface>
											)}

											{testCases.length > 0 && (
												<div className="workbench-support">
													<GenerationGuidance
														compact
														key={`${knowledgeScope}:test-cases:${currentProjectRevision}`}
														request={apiRequest}
														projectId={navigationProjectId}
														stage="test_cases"
													/>
												</div>
											)}
											<div className="panel-nav">
												<Button onClick={goPrev} className="secondary">
													Back
												</Button>
												<Button
													variant="secondary"
													onClick={reviewExportPath ? () => navigate(reviewExportPath) : goNext}
													disabled={testCases.length === 0}
												>
													{reviewExportPath ? "Continue to export" : "Next"}
												</Button>
											</div>
										</section>
									)}

									{activeTab === 4 && (
										<AutomationPanel
											projectId={navigationProjectId}
											declaredSetup={declaredBrowserSetup}
											navigate={navigate}
											guidance={guidanceSummary("automation")}
											sourceGuidance={guidanceSummary("test_cases")}
											testCases={testCases}
											executionTargetBaseUrl={executionTargetBaseUrl}
											setExecutionTargetBaseUrl={changeExecutionTargetBaseUrl}
											executionTargetEnvironment={executionTargetEnvironment}
											setExecutionTargetEnvironment={changeExecutionTargetEnvironment}
											executionPreview={executionPreview}
											selectedExecutionCandidateIds={selectedExecutionCandidateIds}
											setSelectedExecutionCandidateIds={setSelectedExecutionCandidateIds}
											executionRunResult={executionRunResult}
											executionRecovery={executionRecovery}
											executionNeedsResolution={executionNeedsResolution}
											checkExecutionRecovery={checkExecutionRecovery}
											acknowledgeExecutionRecovery={acknowledgeExecutionRecovery}
											recoveredReportPath={
												executionRecovery?.runRecordId
													? `${buildProjectPath(currentProjectId, "reports")}?run=${encodeURIComponent(executionRecovery.runRecordId)}`
													: null
											}
											executionError={executionError}
											effectiveTarget={executionTargetBaseUrl.trim() || appLink}
											executionStale={projectStageState.execution?.stale}
											executionEligibility={executionEligibility}
											isPreviewingExecution={isPreviewingExecution}
											isRunningExecution={isRunningExecution}
											authActionDisabled={authActionDisabled}
											previewExecution={previewExecution}
											runApprovedExecution={runApprovedExecution}
											goPrev={goPrev}
											goNext={goNext}
										/>
									)}

									{activeTab === 5 && (
										<ExportPanel
											testCases={testCases}
											testCaseReview={testCaseReview}
											executionRunResult={PROJECT_WORKSPACES_V2 ? exactSuiteRun : executionRunResult}
											currentSuiteSnapshotId={PROJECT_WORKSPACES_V2 ? currentSuiteSnapshotId || "" : undefined}
											suiteQualitySummary={suiteQualitySummary}
											declaredSetup={declaredBrowserSetup}
											reviewedDesignAvailable={reviewedDesignAvailable}
											currentProject={currentProject}
											accountId={currentUser?.sub || ""}
											savedFileReportLink={savedFileReportLink}
											location={location}
											navigate={navigate}
											request={apiRequest}
											exportReviewApproved={exportReviewApproved}
											exportRequiresOverride={exportRequiresOverride}
											exportGateLocked={exportGateLocked}
											draftExportOverrideRequested={draftExportOverrideRequested}
											setDraftExportOverrideRequested={setDraftExportOverrideRequested}
											draftExportOverrideReason={draftExportOverrideReason}
											setDraftExportOverrideReason={setDraftExportOverrideReason}
											isExporting={isExporting}
											authActionDisabled={authActionDisabled}
											exportToFormat={exportToFormat}
											exportMessage={exportMessage}
											exportError={exportError}
											goPrev={goPrev}
											backToTestsPath={PROJECT_WORKSPACES_V2 ? testsPath : ""}
										/>
									)}
								</div>
							</main>
						)}
					</div>
				)}
			</GlobalAppShell>
		</div>
	);
}
