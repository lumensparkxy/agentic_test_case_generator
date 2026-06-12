import { useEffect } from "react";
import { onAuthStateChanged, signInWithPopup, signOut } from "firebase/auth";
import {
	createFirebaseAuthProvider,
	firebaseAuth,
	firebaseAuthHandlerUrl,
	hasFirebaseAuthConfig,
	visibleFirebaseAuthProviders,
} from "./firebase";
import AppHeader, { SignInDialog } from "./components/layout/AppHeader";
import AutomationPanel from "./components/automation/AutomationPanel";
import ContextInputsPanel from "./components/context/ContextInputsPanel";
import ExportPanel from "./components/export/ExportPanel";
import GeneratedTestCasesView from "./components/generation/GeneratedTestCasesView";
import RequirementAnalysisPanel from "./components/generation/RequirementAnalysisPanel";
import ScenarioCoveragePanel from "./components/generation/ScenarioCoveragePanel";
import TraceabilityMatrixPanel from "./components/generation/TraceabilityMatrixPanel";
import BillingBanner from "./components/layout/BillingBanner";
import RequirementReviewWorkbench from "./components/requirements/RequirementReviewWorkbench";
import SettingsDialog from "./components/settings/SettingsDialog";
import TemplateSetupPanel from "./components/template/TemplateSetupPanel";
import WorkflowTabs from "./components/layout/WorkflowTabs";
import WorkflowDiagnostics from "./components/workflow/WorkflowDiagnostics";
import useAppSessionState from "./hooks/useAppSessionState";
import useBillingStatus from "./hooks/useBillingStatus";
import useContextWorkflowState from "./hooks/useContextWorkflowState";
import useEscapeToClose from "./hooks/useEscapeToClose";
import useExecutionWorkflowState from "./hooks/useExecutionWorkflowState";
import useExportWorkflowState from "./hooks/useExportWorkflowState";
import useIntegrationWorkflowState from "./hooks/useIntegrationWorkflowState";
import useRequirementWorkflowState from "./hooks/useRequirementWorkflowState";
import useTestCaseWorkflowState from "./hooks/useTestCaseWorkflowState";
import useWorkflowNavigationState from "./hooks/useWorkflowNavigationState";
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
} from "./constants/workflow";
import { API_BASE, createRequestId, downloadResponseBlob, ensureRequestIdHeader, parseApiError } from "./services/apiClient";
import {
	buildAzureDevOpsConnectionForm,
	buildJiraConnectionForm,
	getRequirementReviewStatus,
	getTestCaseLinkedRequirementIds,
	isAzureDevOpsLinkedRequirement,
	isJiraLinkedRequirement,
	mergeRequirementMetadata,
	normalizeStringArray,
} from "./utils/requirements";
import {
	buildEmptyUsageSummary,
	getCurrentUserUsageSummary,
} from "./utils/usage";
import { buildWorkflowSettingsPayload, getReviewScoreMeta } from "./utils/workflow";
import "./App.css";

const getAuthProviderLabel = (providerKeyOrId) => {
	const provider = visibleFirebaseAuthProviders.find(({ id, providerId }) => (
		providerKeyOrId === id || providerKeyOrId === providerId
	));
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

const hasMetricValue = (metrics, key) => Boolean(
	metrics
	&& Object.prototype.hasOwnProperty.call(metrics, key)
	&& metrics[key] != null
);

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

export default function App() {
	const {
		activeTab,
		setActiveTab,
	} = useWorkflowNavigationState();
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
		enrichedContext,
		setEnrichedContext,
		selectedArtifactSourceIds,
		setSelectedArtifactSourceIds,
		isAnalyzingContext,
		setIsAnalyzingContext,
		resetContextAnalysis,
	} = useContextWorkflowState();
	const {
		templateName,
		setTemplateName,
		templateFormat,
		setTemplateFormat,
		testCases,
		setTestCases,
		requirementAnalysis,
		setRequirementAnalysis,
		coveragePlan,
		setCoveragePlan,
		coverageMetrics,
		setCoverageMetrics,
		testCaseReview,
		setTestCaseReview,
		testCaseWorkflowDiagnostics,
		setTestCaseWorkflowDiagnostics,
		appliedTestCaseWorkflowSettings,
		setAppliedTestCaseWorkflowSettings,
		testCaseIterationHistory,
		setTestCaseIterationHistory,
		feedback,
		setFeedback,
		testCaseWorkflowSettings,
		setTestCaseWorkflowSettings,
		expandedRows,
		setExpandedRows,
		activeGenerateResultTab,
		setActiveGenerateResultTab,
		isGenerating,
		setIsGenerating,
		resetTestCaseWorkflowState,
	} = useTestCaseWorkflowState();
	const {
		executionTargetBaseUrl,
		setExecutionTargetBaseUrl,
		executionPreview,
		setExecutionPreview,
		executionRunResult,
		setExecutionRunResult,
		isPreviewingExecution,
		setIsPreviewingExecution,
		isRunningExecution,
		setIsRunningExecution,
		resetExecutionWorkflowState,
	} = useExecutionWorkflowState();
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
		status,
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
	const hasContextInputs = Boolean(appLink || prototypeLink || diagramLinks.trim() || imageLinks.trim());
	const billingEnforcementEnabled = Boolean(billingEntitlements && !billingEntitlements.shadow_mode);
	const requirementWorkflowLocked = Boolean(
		billingEnforcementEnabled
		&& billingEntitlements?.account?.plan_tier === "pilot"
		&& billingEntitlements?.requirements?.exhausted
	);
	const testCaseWorkflowLocked = Boolean(
		billingEnforcementEnabled
		&& billingEntitlements?.account?.plan_tier === "pilot"
		&& billingEntitlements?.test_cases?.exhausted
	);
	const requirementActionDisabled = authActionDisabled || requirementWorkflowLocked;
	const testCaseActionDisabled = authActionDisabled || testCaseWorkflowLocked;
	const jiraConnection = jiraConnectionStatus?.connection || null;
	const jiraConnected = Boolean(jiraConnectionStatus?.connected && jiraConnection);
	const hasJiraRequirements = requirements.some((requirement) => isJiraLinkedRequirement(requirement));
	const jiraSyncIssues = Array.isArray(jiraSyncPreview?.issues) ? jiraSyncPreview.issues : [];
	const jiraPreviewHasReadyIssue = jiraSyncIssues.some((issue) => issue.status === "ready");
	const selectedJiraIssue = jiraIssueResults.find((issue) => issue.key === selectedJiraIssueKey) || null;
	const jiraIssueTypeOptions = jiraProjectIssueTypes.length
		? jiraProjectIssueTypes.map((issueType) => issueType.name)
		: DEFAULT_JIRA_ISSUE_TYPE_OPTIONS;
	const jiraImportedIssueKeys = [...new Set(
		requirements
			.filter((requirement) => isJiraLinkedRequirement(requirement))
			.map((requirement) => requirement?.source_issue_key || requirement?.sync_target_issue_key || "")
			.filter(Boolean)
	)];
	const azureDevOpsConnection = azureDevOpsConnectionStatus?.connection || null;
	const azureDevOpsConnected = Boolean(azureDevOpsConnectionStatus?.connected && azureDevOpsConnection);
	const hasAzureDevOpsRequirements = requirements.some((requirement) => isAzureDevOpsLinkedRequirement(requirement));
	const azureDevOpsSyncWorkItems = Array.isArray(azureDevOpsSyncPreview?.work_items) ? azureDevOpsSyncPreview.work_items : [];
	const azureDevOpsPreviewHasReadyWorkItem = azureDevOpsSyncWorkItems.some((workItem) => workItem.status === "ready");
	const selectedAzureDevOpsWorkItem = azureDevOpsWorkItemResults.find((workItem) => `${workItem.work_item_id}` === `${selectedAzureDevOpsWorkItemId}`) || null;
	const azureDevOpsWorkItemTypeOptions = azureDevOpsWorkItemTypes.length
		? azureDevOpsWorkItemTypes.map((workItemType) => workItemType.name)
		: DEFAULT_AZURE_DEVOPS_WORK_ITEM_TYPE_OPTIONS;
	const azureDevOpsImportedWorkItemIds = [...new Set(
		requirements
			.filter((requirement) => isAzureDevOpsLinkedRequirement(requirement))
			.map((requirement) => requirement?.source_issue_key || requirement?.sync_target_issue_key || "")
			.filter(Boolean)
	)];
	const requirementStatusCounts = requirements.reduce((acc, requirement) => {
		const status = getRequirementReviewStatus(requirement);
		acc[status] = (acc[status] || 0) + 1;
		return acc;
	}, {});
	const approvedRequirements = requirements.filter((requirement) => getRequirementReviewStatus(requirement) === "Approved");
	const approvedRequirementCount = approvedRequirements.length;
	const rejectedRequirementCount = requirementStatusCounts.Rejected || 0;
	const reviewPendingRequirementCount = requirements.length - approvedRequirementCount - rejectedRequirementCount;
	const canGenerateFromApprovedRequirements = approvedRequirementCount > 0;
	const requirementReviewMeta = getReviewScoreMeta(requirementReview);
	const testCaseReviewMeta = getReviewScoreMeta(testCaseReview);
	const requirementSourceMetricMeta = getRequirementSourceMetricMeta(requirementCoverageMetrics);
	const requirementReportStats = [
		requirementReview ? {
			label: "Quality score",
			value: `${requirementReviewMeta.score}/100`,
			emphasis: true,
		} : null,
		requirementReviewMeta.threshold > 0 ? {
			label: "Approval threshold",
			value: requirementReviewMeta.threshold,
		} : null,
		(requirementCoverageMetrics || requirements.length > 0) ? {
			label: "Requirements",
			value: hasMetricValue(requirementCoverageMetrics, "total_requirements")
				? requirementCoverageMetrics.total_requirements
				: requirements.length,
		} : null,
		requirementSourceMetricMeta.countLabel ? {
			label: requirementSourceMetricMeta.countLabel,
			value: requirementSourceMetricMeta.countValue,
		} : null,
		hasMetricValue(requirementCoverageMetrics, "requirements_per_document") ? {
			label: requirementSourceMetricMeta.perLabel,
			value: requirementCoverageMetrics.requirements_per_document,
		} : null,
		hasMetricValue(requirementCoverageMetrics, "unique_requirements") ? {
			label: "Unique",
			value: requirementCoverageMetrics.unique_requirements,
		} : null,
		hasMetricValue(requirementCoverageMetrics, "duplicate_requirements") ? {
			label: "Duplicates",
			value: requirementCoverageMetrics.duplicate_requirements,
		} : null,
		hasMetricValue(requirementCoverageMetrics, "shall_format_count") ? {
			label: "Shall format",
			value: hasMetricValue(requirementCoverageMetrics, "total_requirements")
				? `${requirementCoverageMetrics.shall_format_count}/${requirementCoverageMetrics.total_requirements}`
				: requirementCoverageMetrics.shall_format_count,
		} : null,
		requirementWorkflowDiagnostics?.status ? {
			label: "Workflow status",
			value: formatWorkflowStatusLabel(requirementWorkflowDiagnostics.status),
		} : null,
		requirementIterationHistory.length ? {
			label: "Iterations",
			value: requirementIterationHistory.length,
		} : null,
		appliedRequirementWorkflowSettings?.max_iterations != null ? {
			label: "Max iterations",
			value: appliedRequirementWorkflowSettings.max_iterations,
		} : null,
	].filter(Boolean);
	const requirementReportFlags = [
		requirementWorkflowDiagnostics?.timed_out ? { label: "Timed out", tone: "warning" } : null,
		requirementWorkflowDiagnostics?.stalled ? { label: "Stalled", tone: "warning" } : null,
		requirementWorkflowDiagnostics?.used_fallback ? { label: "Fallback used", tone: "warning" } : null,
		requirementWorkflowDiagnostics?.max_iterations_reached ? { label: "Max iterations reached", tone: "warning" } : null,
		requirementWorkflowDiagnostics?.failure_reason ? {
			label: `Reason: ${formatWorkflowStatusLabel(requirementWorkflowDiagnostics.failure_reason)}`,
			tone: "muted",
		} : null,
	].filter(Boolean);
	const requirementWarnings = requirementWorkflowDiagnostics?.warnings || [];
	const requirementParserFailures = requirementWorkflowDiagnostics?.parser_failures || [];
	const requirementBlockingIssues = requirementReview?.approved ? [] : (requirementReview?.blocking_issues || []);
	const requirementReportDetailCount = (
		requirementBlockingIssues.length
		+ requirementWarnings.length
		+ requirementParserFailures.length
	);

	const toggleRowExpansion = (id) => {
		setExpandedRows(prev => ({ ...prev, [id]: !prev[id] }));
	};

	const chooseGenerateResultTab = (data) => {
		const diagnostics = data?.workflow_diagnostics || null;
		const metrics = data?.coverage_metrics || null;
		const hasDiagnosticAttention = Boolean(
			diagnostics?.failure_reason
			|| diagnostics?.timed_out
			|| diagnostics?.stalled
			|| (diagnostics?.status && diagnostics.status !== "completed")
			|| diagnostics?.warnings?.length
			|| diagnostics?.parser_failures?.length
		);

		if (hasDiagnosticAttention) {
			return "diagnostics";
		}
		if ((metrics?.requirements_without_tests || []).length > 0) {
			return "traceability";
		}
		if ((metrics?.missing_must_have_scenarios || []).length > 0 || (metrics?.missing_scenarios || []).length > 0) {
			return "coverage";
		}
		return "test-cases";
	};

	const resetGeneratedArtifacts = () => {
		resetTestCaseWorkflowState();
		resetExportWorkflowState();
		resetExecutionWorkflowState();
	};

	const updateRequirementReviewStatus = (requirementId, reviewStatus) => {
		setRequirements((prev) => prev.map((requirement) => (
			requirement.id === requirementId
				? { ...requirement, review_status: reviewStatus }
				: requirement
		)));
		resetGeneratedArtifacts();
		setStatus(`${requirementId} marked ${reviewStatus.toLowerCase()}.`);
	};

	const bulkUpdateRequirementReviewStatus = (reviewStatus, predicate = () => true) => {
		const targetIds = new Set(requirements.filter(predicate).map((requirement) => requirement.id));
		setRequirements((prev) => prev.map((requirement) => (
			targetIds.has(requirement.id)
				? { ...requirement, review_status: reviewStatus }
				: requirement
		)));
		resetGeneratedArtifacts();
		const updatedCount = targetIds.size;
		setStatus(`${updatedCount} requirement${updatedCount === 1 ? "" : "s"} marked ${reviewStatus.toLowerCase()}.`);
	};

	const toggleRequirementQualityFlag = (requirementId, flag) => {
		setRequirements((prev) => prev.map((requirement) => {
			if (requirement.id !== requirementId) {
				return requirement;
			}
			const currentFlags = normalizeStringArray(requirement.quality_flags);
			const nextFlags = currentFlags.includes(flag)
				? currentFlags.filter((item) => item !== flag)
				: [...currentFlags, flag];
			return { ...requirement, quality_flags: nextFlags };
		}));
		resetGeneratedArtifacts();
	};

	const buildContextPayload = (requirementsOverride = requirements) => {
		const baseContext = {
			requirements: requirementsOverride,
			app_link: appLink || null,
			prototype_link: prototypeLink || null,
			diagram_links: diagramLinks
				? diagramLinks.split(";").map((x) => x.trim()).filter(Boolean)
				: null,
			image_links: imageLinks
				? imageLinks.split(";").map((x) => x.trim()).filter(Boolean)
				: null,
			notes: "Generated via UI",
		};

		if (!enrichedContext?.grounded_context) {
			return { ...baseContext, grounded_context: null };
		}

		const selectedIds = new Set(selectedArtifactSourceIds);
		const groundedContext = enrichedContext.grounded_context;
		return {
			...baseContext,
			grounded_context: {
				...groundedContext,
				artifact_sources: (groundedContext.artifact_sources || []).filter((source) => selectedIds.has(source.id)),
				ui_elements: (groundedContext.ui_elements || []).filter((element) => !element.source_id || selectedIds.has(element.source_id)),
			},
		};
	};

	const renderWorkflowDiagnostics = (title, diagnostics, appliedSettings, iterationHistory) => (
		<WorkflowDiagnostics
			title={title}
			diagnostics={diagnostics}
			appliedSettings={appliedSettings}
			iterationHistory={iterationHistory}
		/>
	);

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
						<p>{requirementReview?.summary || "A compact view of quality, coverage, and workflow state for the current requirement set."}</p>
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
					<div className="requirement-report-table-wrapper">
						<table className="requirement-report-table">
							<thead>
								<tr>
									{requirementReportStats.map((stat) => (
										<th key={stat.label} scope="col">{stat.label}</th>
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
						</table>
					</div>
				)}

				{requirementReportDetailCount > 0 && (
					<details className="requirement-report-details">
						<summary>
							<span>Workflow notes</span>
							<span className="requirement-report-details-count">{requirementReportDetailCount} item{requirementReportDetailCount === 1 ? "" : "s"}</span>
						</summary>
						<div className="requirement-report-details-body">
							{requirementBlockingIssues.length > 0 && (
								<div className="requirement-report-detail-block issue">
									<strong>Blocking issues</strong>
									<ul>
										{requirementBlockingIssues.slice(0, 4).map((issue) => <li key={issue}>{issue}</li>)}
									</ul>
								</div>
							)}

							{requirementWarnings.length > 0 && (
								<div className="requirement-report-detail-block warning">
									<strong>Warnings</strong>
									<ul>
										{requirementWarnings.map((warning) => <li key={warning}>{warning}</li>)}
									</ul>
								</div>
							)}

							{requirementParserFailures.length > 0 && (
								<div className="requirement-report-detail-block alert">
									<strong>Parser issues</strong>
									<ul>
										{requirementParserFailures.map((failure) => <li key={failure}>{failure}</li>)}
									</ul>
								</div>
							)}
						</div>
					</details>
				)}
			</div>
		);
	};

	const clearAuthState = (nextStatus = null) => {
		setAuthToken("");
		setCurrentUser(null);
		setActiveAuthProvider("");
		setUsageSummary(null);
		setIsUsageLoading(false);
		setBillingEntitlements(null);
		setIsBillingLoading(false);
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
		setExecutionPreview(null);
		setExecutionRunResult(null);
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
		if (nextStatus) {
			setStatus(nextStatus);
		}
	};

	const persistAuthState = (token, user) => {
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
			const res = await apiRequest("/entitlements/me", { method: "GET" });
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
					persistAuthState(storedToken, data || storedUser);
					setStatus(`Welcome back, ${(data || storedUser).name}.`);
				} catch {
					clearAuthState("Session expired. Please sign in again.");
				} finally {
					setIsVerifyingSession(false);
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

		void Promise.all([
			refreshUsageSummary(currentUser),
			refreshBillingEntitlements(currentUser),
		]);
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

	useEscapeToClose(isSignInDialogOpen, closeSignInDialog);
	useEscapeToClose(isSettingsDialogOpen, closeSettingsDialog);

	const apiRequest = async (path, options = {}, authRequired = true) => {
		const headers = ensureRequestIdHeader(options.headers || {});

		if (authRequired) {
			const token = await getCurrentAccessToken();
			if (!token) {
				setStatus(AUTH_REQUIRED_MESSAGE);
				throw new Error(AUTH_REQUIRED_MESSAGE);
			}
			headers.Authorization = `Bearer ${token}`;
		}

		const res = await fetch(`${API_BASE}${path}`, {
			...options,
			cache: "no-store",
			headers
		});

		if (authRequired && res.status === 401) {
			clearAuthState("Session expired or unauthorized. Please sign in again.");
			throw new Error("Session expired or unauthorized. Please sign in again.");
		}

		return res;
	};

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
			const res = await apiRequest(`/integrations/jira/projects/${encodeURIComponent(normalizedProjectKey)}/issue-types`, { method: "GET" });
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
			const res = await apiRequest(`/integrations/azure-devops/projects/${encodeURIComponent(normalizedProject)}/work-item-types`, { method: "GET" });
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to load Azure DevOps work item types");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			const workItemTypes = Array.isArray(data?.work_item_types) ? data.work_item_types : [];
			setAzureDevOpsWorkItemTypes(workItemTypes);
			setAzureDevOpsWorkItemType((prev) => (prev && workItemTypes.some((workItemType) => workItemType.name === prev) ? prev : ""));
			if (!silent && workItemTypes.length) {
				setStatus(`Loaded ${workItemTypes.length} Azure DevOps work item type${workItemTypes.length === 1 ? "" : "s"} for ${normalizedProject}.`);
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
				setStatus(projects.length ? `Loaded ${projects.length} JIRA project${projects.length === 1 ? "" : "s"}.` : "JIRA returned no browseable projects for this account. If you expected to see a project like TheONE, check that this user has Browse Projects access.");
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
				setStatus(projects.length ? `Loaded ${projects.length} Azure DevOps project${projects.length === 1 ? "" : "s"}.` : "Azure DevOps returned no visible projects for this account.");
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
			const connectedAs = data?.connection?.display_name || data?.connection?.account_email || data?.connection?.organization || "Azure DevOps";
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
			setSelectedJiraIssueKey((prev) => issues.some((issue) => issue.key === prev) ? prev : (issues[0]?.key || ""));
			setStatus(issues.length ? `Found ${issues.length} JIRA ${issueTypeLabel}${issues.length === 1 || issueTypeLabel.endsWith("s") ? "" : "s"}.` : `No ${issueTypeLabel} matched that search.`);
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
			setSelectedAzureDevOpsWorkItemId((prev) => workItems.some((workItem) => `${workItem.work_item_id}` === `${prev}`) ? prev : (workItems[0]?.work_item_id ? `${workItems[0].work_item_id}` : ""));
			setStatus(workItems.length ? `Found ${workItems.length} Azure DevOps ${workItemTypeLabel}${workItems.length === 1 || workItemTypeLabel.endsWith("s") ? "" : "s"}.` : `No ${workItemTypeLabel} matched that search.`);
		} catch (error) {
			setStatus(`Azure DevOps work item search failed: ${error.message}`);
		} finally {
			setIsSearchingAzureDevOpsWorkItems(false);
		}
	};

	const importRequirementsFromJira = async () => {
		if (requirementWorkflowLocked) {
			const contactEmail = billingEntitlements?.account?.support_contact_email || "hello@spica-digital.eu";
			setStatus(`Requirement workflows are locked. Contact ${contactEmail} to upgrade.`);
			return;
		}
		if (!selectedJiraIssueKey) {
			setStatus("Select a JIRA epic or issue before importing requirements.");
			return;
		}
		setIsImportingFromJira(true);
		setStatus("Importing requirements from JIRA...");
		resetJiraSyncState();
		try {
			const workflowSettingsPayload = buildWorkflowSettingsPayload(requirementWorkflowSettings);
			const res = await apiRequest("/integrations/jira/import", {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": createRequestId() },
				body: JSON.stringify({
					epic_key: selectedJiraIssueKey,
					include_children: true,
					workflow_settings: workflowSettingsPayload,
				}),
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to import requirements from JIRA");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setRequirementSourceMode("jira");
			setRawText(data.raw_text || "");
			setRequirements(data.requirements || []);
			setRequirementReview(data.review || null);
			setRequirementCoverageMetrics(data.coverage_metrics || null);
			setRequirementWorkflowDiagnostics(data.workflow_diagnostics || null);
			setAppliedRequirementWorkflowSettings(data.workflow_settings || null);
			setRequirementIterationHistory(data.iteration_history || []);
			setTestCases([]);
			setRequirementAnalysis([]);
			setCoveragePlan([]);
			setCoverageMetrics(null);
			setTestCaseReview(null);
			setTestCaseWorkflowDiagnostics(null);
			setAppliedTestCaseWorkflowSettings(null);
			setTestCaseIterationHistory([]);
			setActiveGenerateResultTab("test-cases");
			resetContextAnalysis();
			setExpandedRows({});
			setFeedback("");
			setExecutionPreview(null);
			setExecutionRunResult(null);
			setReqFeedback("");
			await Promise.all([refreshUsageSummary(), refreshBillingEntitlements()]);
			setStatus(`Imported ${data.requirements?.length || 0} requirement${(data.requirements?.length || 0) === 1 ? "" : "s"} from ${data.source_name || selectedJiraIssueKey}.`);
		} catch (error) {
			setStatus(`JIRA import failed: ${error.message}`);
		} finally {
			setIsImportingFromJira(false);
		}
	};

	const importRequirementsFromAzureDevOps = async () => {
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
		setIsImportingFromAzureDevOps(true);
		setStatus("Importing requirements from Azure DevOps...");
		resetAzureDevOpsSyncState();
		try {
			const workflowSettingsPayload = buildWorkflowSettingsPayload(requirementWorkflowSettings);
			const res = await apiRequest("/integrations/azure-devops/import", {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": createRequestId() },
				body: JSON.stringify({
					project: selectedAzureDevOpsProject,
					work_item_id: Number.parseInt(`${selectedAzureDevOpsWorkItemId}`, 10),
					include_children: true,
					workflow_settings: workflowSettingsPayload,
				}),
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to import requirements from Azure DevOps");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setRequirementSourceMode("azure_devops");
			setRawText(data.raw_text || "");
			setRequirements(data.requirements || []);
			setRequirementReview(data.review || null);
			setRequirementCoverageMetrics(data.coverage_metrics || null);
			setRequirementWorkflowDiagnostics(data.workflow_diagnostics || null);
			setAppliedRequirementWorkflowSettings(data.workflow_settings || null);
			setRequirementIterationHistory(data.iteration_history || []);
			setTestCases([]);
			setRequirementAnalysis([]);
			setCoveragePlan([]);
			setCoverageMetrics(null);
			setTestCaseReview(null);
			setTestCaseWorkflowDiagnostics(null);
			setAppliedTestCaseWorkflowSettings(null);
			setTestCaseIterationHistory([]);
			setActiveGenerateResultTab("test-cases");
			resetContextAnalysis();
			setExpandedRows({});
			setFeedback("");
			setExecutionPreview(null);
			setExecutionRunResult(null);
			setReqFeedback("");
			await Promise.all([refreshUsageSummary(), refreshBillingEntitlements()]);
			setStatus(`Imported ${data.requirements?.length || 0} requirement${(data.requirements?.length || 0) === 1 ? "" : "s"} from ${data.source_name || `#${selectedAzureDevOpsWorkItemId}`}.`);
		} catch (error) {
			setStatus(`Azure DevOps import failed: ${error.message}`);
		} finally {
			setIsImportingFromAzureDevOps(false);
		}
	};

	const previewJiraSync = async (requirementsOverride = requirements, options = {}) => {
		const { clearLastSyncResult = true } = options;
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
			setJiraSyncPreview(data || null);
			const readyCount = data?.ready_issue_count || 0;
			const conflictCount = data?.conflict_count || 0;
			setStatus(`JIRA preview ready: ${readyCount} issue${readyCount === 1 ? "" : "s"} ready, ${conflictCount} conflict${conflictCount === 1 ? "" : "s"}.`);
		} catch (error) {
			setStatus(`JIRA preview failed: ${error.message}`);
		} finally {
			setIsPreviewingJiraSync(false);
		}
	};

	const applyJiraSync = async () => {
		if (!jiraSyncPreview) {
			setStatus("Preview JIRA changes before pushing updates.");
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
			setJiraSyncResults(data || null);
			const syncedRequirements = mergeRequirementMetadata(data?.requirements || requirements, requirements);
			setRequirements(syncedRequirements);
			const updatedCount = data?.updated_issue_count || 0;
			const conflictCount = data?.conflict_count || 0;
			setStatus(`JIRA sync complete: ${updatedCount} issue${updatedCount === 1 ? "" : "s"} updated, ${conflictCount} conflict${conflictCount === 1 ? "" : "s"} skipped.`);
			await previewJiraSync(syncedRequirements, { clearLastSyncResult: false });
		} catch (error) {
			setStatus(`JIRA sync failed: ${error.message}`);
		} finally {
			setIsApplyingJiraSync(false);
		}
	};

	const previewAzureDevOpsSync = async (requirementsOverride = requirements, options = {}) => {
		const { clearLastSyncResult = true } = options;
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
			setAzureDevOpsSyncPreview(data || null);
			const readyCount = data?.ready_work_item_count || 0;
			const conflictCount = data?.conflict_count || 0;
			setStatus(`Azure DevOps preview ready: ${readyCount} work item${readyCount === 1 ? "" : "s"} ready, ${conflictCount} conflict${conflictCount === 1 ? "" : "s"}.`);
		} catch (error) {
			setStatus(`Azure DevOps preview failed: ${error.message}`);
		} finally {
			setIsPreviewingAzureDevOpsSync(false);
		}
	};

	const applyAzureDevOpsSync = async () => {
		if (!azureDevOpsSyncPreview) {
			setStatus("Preview Azure DevOps changes before pushing updates.");
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
			setAzureDevOpsSyncResults(data || null);
			const syncedRequirements = mergeRequirementMetadata(data?.requirements || requirements, requirements);
			setRequirements(syncedRequirements);
			const updatedCount = data?.updated_work_item_count || 0;
			const conflictCount = data?.conflict_count || 0;
			setStatus(`Azure DevOps sync complete: ${updatedCount} work item${updatedCount === 1 ? "" : "s"} updated, ${conflictCount} conflict${conflictCount === 1 ? "" : "s"} skipped.`);
			await previewAzureDevOpsSync(syncedRequirements, { clearLastSyncResult: false });
		} catch (error) {
			setStatus(`Azure DevOps sync failed: ${error.message}`);
		} finally {
			setIsApplyingAzureDevOpsSync(false);
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
			await signInWithPopup(firebaseAuth, provider);
		} catch (error) {
			clearAuthState(buildProviderSignInErrorMessage(providerConfig, error));
		} finally {
			setIsAuthenticating(false);
			setActiveAuthProvider("");
		}
	};

	const handleLogout = async () => {
		setIsAuthenticating(true);
		try {
			if (firebaseAuth) {
				await signOut(firebaseAuth);
			}
		} catch (error) {
			setStatus(`Sign out failed: ${error.message}`);
		} finally {
			clearAuthState("Signed out.");
			setIsAuthenticating(false);
		}
	};

	const parseRequirements = async (withFeedback = false) => {
		if (requirementWorkflowLocked) {
			const contactEmail = billingEntitlements?.account?.support_contact_email || "hello@spica-digital.eu";
			setStatus(`Requirement workflows are locked. Contact ${contactEmail} to upgrade.`);
			return;
		}
		if (!file && !withFeedback) return;
		setIsParsing(true);
		setStatus(withFeedback ? "Refining requirements with feedback..." : "Parsing requirements...");
		resetIntegrationSyncState();
		try {
			const formData = new FormData();
			const requestId = createRequestId();
			const workflowSettingsPayload = buildWorkflowSettingsPayload(requirementWorkflowSettings);
			if (file) formData.append("file", file);
			if (workflowSettingsPayload) formData.append("workflow_settings", JSON.stringify(workflowSettingsPayload));
			if (withFeedback && reqFeedback) {
				formData.append("feedback", reqFeedback);
				formData.append("existing_requirements", JSON.stringify(requirements));
			}
			const res = await apiRequest("/requirements/parse", {
				method: "POST",
				headers: { "X-Request-ID": requestId },
				body: formData
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to parse requirements");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			const nextRequirements = withFeedback
				? mergeRequirementMetadata(data.requirements || [], requirements)
				: (data.requirements || []);
			if (!withFeedback) {
				setRequirementSourceMode("file");
			}
			setRawText(data.raw_text || rawText);
			setRequirements(nextRequirements);
			setRequirementReview(data.review || null);
			setRequirementCoverageMetrics(data.coverage_metrics || null);
			setRequirementWorkflowDiagnostics(data.workflow_diagnostics || null);
			setAppliedRequirementWorkflowSettings(data.workflow_settings || null);
			setRequirementIterationHistory(data.iteration_history || []);
			setTestCases([]);
			setRequirementAnalysis([]);
			setCoveragePlan([]);
			setCoverageMetrics(null);
			setTestCaseReview(null);
			setTestCaseWorkflowDiagnostics(null);
			setAppliedTestCaseWorkflowSettings(null);
			setTestCaseIterationHistory([]);
			setActiveGenerateResultTab("test-cases");
			resetContextAnalysis();
			setExpandedRows({});
			setFeedback("");
			setExecutionPreview(null);
			setExecutionRunResult(null);
			setStatus(withFeedback ? "Requirements refined." : "Parsed.");
			await Promise.all([refreshUsageSummary(), refreshBillingEntitlements()]);
			if (withFeedback) setReqFeedback("");
		} catch (error) {
			setStatus(`Parse failed: ${error.message}`);
		} finally {
			setIsParsing(false);
		}
	};

	const buildExecutionPayload = (casesOverride = testCases) => ({
		test_cases: casesOverride,
		target_base_url: executionTargetBaseUrl.trim() || appLink || null,
	});

	const previewExecution = async (casesOverride = testCases, options = {}) => {
		const { updateStatus = true } = options;
		const casesToPreview = Array.isArray(casesOverride) ? casesOverride : [];
		if (!casesToPreview.length) {
			if (updateStatus) {
				setStatus("Generate test cases before previewing execution.");
			}
			return null;
		}

		setIsPreviewingExecution(true);
		if (updateStatus) {
			setStatus("Previewing execution readiness...");
		}
		try {
			const res = await apiRequest("/automation/execution/preview", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(buildExecutionPayload(casesToPreview)),
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to preview execution");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setExecutionPreview(data || null);
			setExecutionRunResult(null);
			if (updateStatus) {
				const summary = data?.summary || {};
				setStatus(`Execution preview ready: ${summary.executable || 0} executable, ${summary.manual || 0} manual, ${summary.unsupported || 0} unsupported.`);
			}
			return data;
		} catch (error) {
			if (updateStatus) {
				setStatus(`Execution preview failed: ${error.message}`);
			}
			return null;
		} finally {
			setIsPreviewingExecution(false);
		}
	};

	const runApprovedExecution = async () => {
		const preview = executionPreview || await previewExecution(testCases, { updateStatus: false });
		const executableCandidates = preview?.executable || [];
		if (!executableCandidates.length) {
			setStatus("No executable candidates are available to run.");
			return;
		}

		setIsRunningExecution(true);
		setStatus(`Running ${executableCandidates.length} executable candidate${executableCandidates.length === 1 ? "" : "s"}...`);
		try {
			const res = await apiRequest("/automation/execution/run", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					...buildExecutionPayload(testCases),
					selected_test_case_ids: executableCandidates.map((candidate) => candidate.source_test_case_id),
				}),
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to run execution candidates");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setExecutionRunResult(data || null);
			setExecutionPreview(data?.preview || preview);
			const summary = data?.summary || {};
			setStatus(`Execution ${data?.status || "finished"}: ${summary.passed || 0} passed, ${summary.failed || 0} failed, ${summary.invalid || 0} invalid.`);
		} catch (error) {
			setStatus(`Execution run failed: ${error.message}`);
		} finally {
			setIsRunningExecution(false);
		}
	};

	const generateTestCases = async (withFeedback = false) => {
		if (testCaseWorkflowLocked) {
			const contactEmail = billingEntitlements?.account?.support_contact_email || "hello@spica-digital.eu";
			setStatus(`Test-case workflows are locked. Contact ${contactEmail} to upgrade.`);
			return;
		}
		if (!canGenerateFromApprovedRequirements) {
			setStatus("Approve at least one requirement before generating test cases.");
			return;
		}
		const requirementsForGeneration = approvedRequirements;
		setIsGenerating(true);
		setStatus(withFeedback ? "Refining test cases with approved requirements..." : `Generating test cases from ${requirementsForGeneration.length} approved requirement${requirementsForGeneration.length === 1 ? "" : "s"}...`);
		try {
			const requestId = createRequestId();
			const workflowSettingsPayload = buildWorkflowSettingsPayload(testCaseWorkflowSettings);
			const sharedPayload = {
				requirements: requirementsForGeneration,
				template: {
					name: templateName,
					format: templateFormat,
					fields: ["id", "title", "description", "priority", "type", "status", "preconditions", "steps", "expected_result", "test_data", "estimated_time", "automation_status", "component", "linked_requirement_ids", "scenario_refs", "source_refs", "tags"]
				},
				context: buildContextPayload(requirementsForGeneration),
				workflow_settings: workflowSettingsPayload,
			};

			const useRefineEndpoint = withFeedback && testCases.length > 0;
			const payload = useRefineEndpoint
				? {
					...sharedPayload,
					test_cases: testCases,
					feedback: feedback.trim()
				}
				: {
					...sharedPayload,
					feedback: withFeedback && feedback ? feedback.trim() : null
				};

			const res = await apiRequest(useRefineEndpoint ? "/testcases/refine" : "/testcases/generate", {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": requestId },
				body: JSON.stringify(payload)
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to generate test cases");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setTestCases(data.test_cases || []);
			setRequirementAnalysis(data.requirement_analysis || []);
			setCoveragePlan(data.coverage_plan || []);
			setCoverageMetrics(data.coverage_metrics || null);
			setTestCaseReview(data.review || null);
			setTestCaseWorkflowDiagnostics(data.workflow_diagnostics || null);
			setAppliedTestCaseWorkflowSettings(data.workflow_settings || null);
			setTestCaseIterationHistory(data.iteration_history || []);
			setExpandedRows({});
			setActiveGenerateResultTab(chooseGenerateResultTab(data));
			setDraftExportOverrideRequested(false);
			setDraftExportOverrideReason("");
			setExecutionPreview(null);
			setExecutionRunResult(null);
			const generatedCount = Array.isArray(data.test_cases) ? data.test_cases.length : 0;
			const reviewStatus = data.review
				? ` Review ${data.review.approved ? "approved" : "needs refinement"}.`
				: "";
			setStatus(
				`${withFeedback ? "Test cases refined" : "Generated"}${generatedCount ? ` ${generatedCount} test case${generatedCount === 1 ? "" : "s"}` : ""} from ${requirementsForGeneration.length} approved requirement${requirementsForGeneration.length === 1 ? "" : "s"}.${reviewStatus}`.trim()
			);
			if (generatedCount > 0) {
				await previewExecution(data.test_cases || [], { updateStatus: false });
			}
			await Promise.all([refreshUsageSummary(), refreshBillingEntitlements()]);
			if (withFeedback) setFeedback("");
		} catch (error) {
			setStatus(`Generation failed: ${error.message}`);
		} finally {
			setIsGenerating(false);
		}
	};
	const exportReviewApproved = Boolean(testCaseReview?.approved);
	const exportRequiresOverride = Boolean(testCases.length > 0 && testCaseReview && !testCaseReview.approved);
	const draftExportOverrideReasonProvided = draftExportOverrideReason.trim().length > 0;
	const exportGateLocked = Boolean(exportRequiresOverride && (!draftExportOverrideRequested || !draftExportOverrideReasonProvided));

	const exportToFormat = async (format) => {
		if (exportGateLocked) {
			setStatus("Export locked by review gate. Add an override reason to export draft test cases.");
			return;
		}
		setIsExporting(true);
		setStatus(`Exporting to ${format.toUpperCase()}...`);
		try {
			const payload = {
				test_cases: testCases,
				approved: exportReviewApproved,
				review: testCaseReview || undefined,
				draft_override_requested: Boolean(exportRequiresOverride && draftExportOverrideRequested),
				draft_override_reason: exportRequiresOverride && draftExportOverrideRequested ? draftExportOverrideReason.trim() : null,
			};
			const res = await apiRequest(`/export/${format}`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(payload)
			});
			
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Export failed");
				throw new Error(errorMessage);
			}
			
			const extensions = { csv: "csv", excel: "xlsx", json: "json" };
			await downloadResponseBlob(res, `test_cases.${extensions[format] || format}`);
			setStatus(`✓ Exported to ${format.toUpperCase()} successfully`);
		} catch (error) {
			setStatus(`Export failed: ${error.message}`);
		} finally {
			setIsExporting(false);
		}
	};

	const getRequirementScenarioSummary = (requirementId) => {
		return coverageMetrics?.requirement_scenario_summary?.[requirementId] || null;
	};

	const getRequirementAnalysisSummary = (requirementId) => {
		return coverageMetrics?.requirement_analysis_summary?.[requirementId] || null;
	};

	const getRequirementAnalysisGaps = (requirementId) => {
		const analysis = requirementAnalysis.find((a) => a.requirement_id === requirementId);
		if (!analysis) {
			return { highRisks: [], rules: [], constraints: [], permissions: [], transitions: [] };
		}
		const summary = coverageMetrics?.requirement_analysis_summary?.[requirementId] || {};
		const coveredRules = new Set(summary.rules_covered || []);
		const coveredConstraints = new Set(summary.constraints_covered || []);
		const coveredPermissions = new Set(summary.permissions_covered || []);
		const coveredTransitions = new Set(summary.transitions_covered || []);
		const coveredRisks = new Set(summary.risks_covered || []);
		return {
			highRisks: (analysis.risk_signals || []).filter((r) => r.severity === "High" && !coveredRisks.has(r.id)).map((r) => r.title),
			rules: (analysis.business_rules || []).filter((r) => !coveredRules.has(r.id)).map((r) => r.title),
			constraints: (analysis.field_constraints || []).filter((c) => !coveredConstraints.has(c.id)).map((c) => c.field_name),
			permissions: (analysis.role_permissions || []).filter((p) => !coveredPermissions.has(p.id)).map((p) => `${p.role}: ${p.action}`),
			transitions: (analysis.state_transitions || []).filter((t) => !coveredTransitions.has(t.id)).map((t) => `${t.from_state} → ${t.to_state}`),
		};
	};

	const coveredScenarioTotal = coveragePlan.reduce((sum, plan) => sum + (getRequirementScenarioSummary(plan.requirement_id)?.covered_scenarios || 0), 0);
	const plannedScenarioTotal = coveragePlan.reduce((sum, plan) => sum + (plan.scenarios?.length || 0), 0);
	const mustHaveScenarioTotal = coveragePlan.reduce((sum, plan) => sum + (plan.scenarios?.filter((s) => s.must_have).length || 0), 0);
	const mustHaveCoveredScenarioTotal = coveragePlan.reduce((sum, plan) => {
		const missing = new Set(getRequirementScenarioSummary(plan.requirement_id)?.missing_scenario_types || []);
		return sum + (plan.scenarios?.filter((s) => s.must_have && !missing.has(s.scenario_type)).length || 0);
	}, 0);
	const missingScenarioCount = coveragePlan.reduce((sum, plan) => sum + (getRequirementScenarioSummary(plan.requirement_id)?.missing_scenario_types?.length || 0), 0);
	const requirementAnalysisGapCount = requirementAnalysis.reduce((sum, analysis) => {
		const gaps = getRequirementAnalysisGaps(analysis.requirement_id);
		return sum + Object.values(gaps).reduce((s, arr) => s + arr.length, 0);
	}, 0);
	const requirementTraceabilityRows = approvedRequirements.map((requirement) => {
		const linkedTestCases = testCases.filter((testCase) => getTestCaseLinkedRequirementIds(testCase).includes(requirement.id));
		const scenarioSummary = getRequirementScenarioSummary(requirement.id);
		const linkedScenarioTypes = [...new Set(
			linkedTestCases.flatMap((testCase) => normalizeStringArray(testCase.tags))
				.filter((tag) => tag.startsWith("scenario:"))
				.map((tag) => tag.replace("scenario:", "").replace(/-/g, " "))
		)];
		return {
			requirement,
			linkedTestCases,
			scenarioSummary,
			linkedScenarioTypes,
		};
	});
	const tracedRequirementCount = requirementTraceabilityRows.filter((row) => row.linkedTestCases.length > 0).length;
	const traceabilityGapCount = Math.max(0, approvedRequirements.length - tracedRequirementCount);
	const diagnosticsWarningCount = (testCaseWorkflowDiagnostics?.warnings?.length || 0) + (testCaseWorkflowDiagnostics?.parser_failures?.length || 0);
	const diagnosticsNeedsAttention = Boolean(
		testCaseWorkflowDiagnostics?.failure_reason
		|| testCaseWorkflowDiagnostics?.timed_out
		|| testCaseWorkflowDiagnostics?.stalled
		|| (testCaseWorkflowDiagnostics?.status && testCaseWorkflowDiagnostics.status !== "completed")
		|| diagnosticsWarningCount > 0
	);
	const hasGenerateResults = Boolean(
		testCases.length
		|| coveragePlan.length
		|| requirementAnalysis.length
		|| testCaseWorkflowDiagnostics
		|| appliedTestCaseWorkflowSettings
		|| testCaseReview
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
			id: "coverage",
			label: "Scenario Coverage",
			badge: plannedScenarioTotal ? `${coveredScenarioTotal}/${plannedScenarioTotal}` : "—",
			variant: missingScenarioCount > 0 ? "warning" : plannedScenarioTotal ? "success" : "muted",
		},
		{
			id: "analysis",
			label: "Requirement Analysis",
			badge: requirementAnalysisGapCount > 0 ? `${requirementAnalysisGapCount} gaps` : requirementAnalysis.length || "—",
			variant: requirementAnalysisGapCount > 0 ? "warning" : requirementAnalysis.length ? "success" : "muted",
		},
		{
			id: "diagnostics",
			label: "Diagnostics",
			badge: diagnosticsWarningCount || testCaseWorkflowDiagnostics?.status || (appliedTestCaseWorkflowSettings ? "settings" : "—"),
			variant: diagnosticsNeedsAttention ? "warning" : testCaseWorkflowDiagnostics || appliedTestCaseWorkflowSettings ? "muted" : "muted",
		},
	];

	const analyzeContext = async () => {
		setIsAnalyzingContext(true);
		setStatus("Analyzing context artifacts...");
		try {
			const res = await apiRequest("/requirements/enrich", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(buildContextPayload())
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to analyze context");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setEnrichedContext(data);
			setSelectedArtifactSourceIds((data.grounded_context?.artifact_sources || []).map((source) => source.id));
			setStatus("Context analyzed.");
		} catch (error) {
			setStatus(`Context analysis failed: ${error.message}`);
			resetContextAnalysis();
		} finally {
			setIsAnalyzingContext(false);
		}
	};

	const tabs = [
		{ id: 0, label: "Upload", title: "Upload Requirements" },
		{ id: 1, label: "Context", title: "Context Inputs" },
		{ id: 2, label: "Template", title: "Template Setup" },
		{ id: 3, label: "Generate", title: "Generate Test Cases" },
		{ id: 4, label: "Automation", title: "Automation" },
		{ id: 5, label: "Export", title: "Export Test Cases" }
	];

	const goNext = () => setActiveTab((prev) => Math.min(prev + 1, tabs.length - 1));
	const goPrev = () => setActiveTab((prev) => Math.max(prev - 1, 0));
	const {
		billingContactEmail,
		billingStatusItems,
		statusUsageItems,
		pilotAlert,
	} = useBillingStatus(billingEntitlements, usageSummary);
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

	return (
		<div className="page">
			<AppHeader
				status={status}
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
			/>

			{!isAuthenticated && !isVerifyingSession && (
				<div className="auth-warning-banner">
					🔐 Sign in to parse requirements, generate test cases, and export artifacts.
				</div>
			)}


			<BillingBanner
				isAuthenticated={isAuthenticated}
				pilotAlert={pilotAlert}
				billingContactEmail={billingContactEmail}
			/>

			<SignInDialog
				isOpen={isSignInDialogOpen}
				onOverlayClick={handleSignInDialogOverlayClick}
				onClose={closeSignInDialog}
				isAuthenticating={isAuthenticating}
				providers={visibleFirebaseAuthProviders}
				onProviderSignIn={handleProviderSignIn}
			/>

			<SettingsDialog
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

			<WorkflowTabs tabs={tabs} activeTab={activeTab} onTabChange={setActiveTab} />

			<div className="tab-content">
				{activeTab === 0 && (
					<section className="panel">
						<h2 className="panel-title">Upload Requirements</h2>
						<p className="panel-description">
							Choose a source for requirements, extract them into the review loop, and optionally push approved updates back to JIRA or Azure DevOps.
						</p>
						<div className="choice-group source-choice-group" role="radiogroup" aria-label="Requirement source selector">
							{REQUIREMENT_SOURCE_OPTIONS.map((option) => (
								<label key={option.value} className={`choice-card ${requirementSourceMode === option.value ? "selected" : ""}`}>
									<input
										type="radio"
										name="requirement-source"
										value={option.value}
										checked={requirementSourceMode === option.value}
										onChange={() => setRequirementSourceMode(option.value)}
									/>
									<span className="choice-card-copy">
										<span className="choice-card-title">{option.label}</span>
									</span>
								</label>
							))}
						</div>

						{requirementSourceMode === "file" ? (
							<div className="panel-form">
								<div className="form-group">
									<label>Requirements file</label>
									<input
										type="file"
										accept=".md,.docx,.xlsx"
										onChange={(e) => setFile(e.target.files?.[0] || null)}
									/>
								</div>
								<button onClick={() => parseRequirements(false)} disabled={!file || isParsing || requirementActionDisabled}>
									{isParsing ? "⏳ Parsing..." : "Parse Requirements"}
								</button>
							</div>
						) : requirementSourceMode === "jira" ? (
							<div className="jira-workflow-panel">
								<div className="jira-card">
									<div className="jira-card-header">
										<div>
											<h3>Import from JIRA</h3>
											<p>Pick a project, search for an epic, and import the epic plus child issues into the requirement parser.</p>
										</div>
									</div>
									<div className="panel-form two-cols jira-search-grid">
										<div className="form-group">
											<label>Project search</label>
											<input
												placeholder="Search JIRA projects"
												value={jiraProjectQuery}
												onChange={(event) => setJiraProjectQuery(event.target.value)}
												disabled={!jiraConnected}
											/>
										</div>
										<div className="form-group jira-inline-action">
											<label>Project</label>
											<div className="jira-inline-controls">
												<select value={selectedJiraProjectKey} onChange={(event) => setSelectedJiraProjectKey(event.target.value)} disabled={!jiraConnected || !jiraProjects.length}>
													<option value="">Select a JIRA project</option>
													{jiraProjects.map((project) => (
														<option key={project.project_id || project.key} value={project.key}>{project.key} — {project.name}</option>
													))}
												</select>
												<button className="secondary" onClick={() => loadJiraProjects(jiraProjectQuery)} disabled={!jiraConnected || isLoadingJiraProjects}>
													{isLoadingJiraProjects ? "⏳" : "Load"}
												</button>
											</div>
										</div>
										<div className="form-group">
											<label>Issue type</label>
											<select value={jiraIssueType} onChange={(event) => setJiraIssueType(event.target.value)} disabled={!jiraConnected || isLoadingJiraIssueTypes}>
												<option value="">Any issue type</option>
												{jiraIssueTypeOptions.map((issueTypeName) => (
													<option key={issueTypeName} value={issueTypeName}>{issueTypeName}</option>
												))}
											</select>
										</div>
										<div className="form-group jira-inline-action">
											<label>Issue search</label>
											<div className="jira-inline-controls">
												<input
													placeholder={`Search ${(jiraIssueType || "issue").toLowerCase()} summaries`}
													value={jiraIssueQuery}
													onChange={(event) => setJiraIssueQuery(event.target.value)}
													disabled={!jiraConnected}
												/>
												<button className="secondary" onClick={searchJiraIssues} disabled={!jiraConnected || !selectedJiraProjectKey || isSearchingJiraIssues}>
													{isSearchingJiraIssues ? "⏳" : "Search"}
												</button>
											</div>
										</div>
									</div>

									{jiraIssueResults.length > 0 ? (
										<div className="selection-table-wrapper">
											<table className="selection-table">
												<thead>
													<tr>
														<th>Select</th>
														<th>Issue</th>
														<th>Summary</th>
														<th>Type</th>
														<th>Status</th>
														<th>Parent</th>
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
																	<input
																		type="radio"
																		name="jira-issue-selection"
																		checked={selected}
																		onChange={() => setSelectedJiraIssueKey(issue.key)}
																		aria-label={`Select JIRA issue ${issue.key}`}
																	/>
																</td>
																<td><strong>{issue.key}</strong></td>
																<td>{issue.summary}</td>
																<td>{issue.issue_type || "—"}</td>
																<td>{issue.status || "—"}</td>
																<td>{issue.parent_key || "—"}</td>
															</tr>
														);
													})}
												</tbody>
											</table>
										</div>
									) : (
										<span className="helper-text">Search visible issues in the selected project to choose an import source.</span>
									)}

									<div className="panel-form button-row jira-import-actions">
										<button onClick={importRequirementsFromJira} disabled={!selectedJiraIssueKey || isImportingFromJira || requirementActionDisabled}>
											{isImportingFromJira ? "⏳ Importing..." : `Import ${selectedJiraIssue?.key || jiraIssueType || "issue"}`}
										</button>
										{selectedJiraIssue ? <span className="helper-text">Selected source: {selectedJiraIssue.key} — {selectedJiraIssue.summary}</span> : null}
									</div>
								</div>
							</div>
						) : (
							<div className="jira-workflow-panel">
								<div className="jira-card">
									<div className="jira-card-header">
										<div>
											<h3>Import from Azure DevOps</h3>
											<p>Pick a project, search work items, and import the selected item plus children into the requirement parser.</p>
										</div>
									</div>
									<div className="panel-form two-cols jira-search-grid">
										<div className="form-group">
											<label>Project search</label>
											<input
												placeholder="Search Azure DevOps projects"
												value={azureDevOpsProjectQuery}
												onChange={(event) => setAzureDevOpsProjectQuery(event.target.value)}
												disabled={!azureDevOpsConnected}
											/>
										</div>
										<div className="form-group jira-inline-action">
											<label>Project</label>
											<div className="jira-inline-controls">
												<select value={selectedAzureDevOpsProject} onChange={(event) => setSelectedAzureDevOpsProject(event.target.value)} disabled={!azureDevOpsConnected || !azureDevOpsProjects.length}>
													<option value="">Select an Azure DevOps project</option>
													{azureDevOpsProjects.map((project) => (
														<option key={project.project_id || project.name} value={project.name}>{project.name}</option>
													))}
												</select>
												<button className="secondary" onClick={() => loadAzureDevOpsProjects(azureDevOpsProjectQuery)} disabled={!azureDevOpsConnected || isLoadingAzureDevOpsProjects}>
													{isLoadingAzureDevOpsProjects ? "⏳" : "Load"}
												</button>
											</div>
										</div>
										<div className="form-group">
											<label>Work item type</label>
											<select value={azureDevOpsWorkItemType} onChange={(event) => setAzureDevOpsWorkItemType(event.target.value)} disabled={!azureDevOpsConnected || isLoadingAzureDevOpsWorkItemTypes}>
												<option value="">Any work item type</option>
												{azureDevOpsWorkItemTypeOptions.map((workItemTypeName) => (
													<option key={workItemTypeName} value={workItemTypeName}>{workItemTypeName}</option>
												))}
											</select>
										</div>
										<div className="form-group jira-inline-action">
											<label>Work item search</label>
											<div className="jira-inline-controls">
												<input
													placeholder={`Search ${(azureDevOpsWorkItemType || "work item").toLowerCase()} titles/descriptions`}
													value={azureDevOpsWorkItemQuery}
													onChange={(event) => setAzureDevOpsWorkItemQuery(event.target.value)}
													disabled={!azureDevOpsConnected}
												/>
												<button className="secondary" onClick={searchAzureDevOpsWorkItems} disabled={!azureDevOpsConnected || !selectedAzureDevOpsProject || isSearchingAzureDevOpsWorkItems}>
													{isSearchingAzureDevOpsWorkItems ? "⏳" : "Search"}
												</button>
											</div>
										</div>
									</div>

									{azureDevOpsWorkItemResults.length > 0 ? (
										<div className="selection-table-wrapper">
											<table className="selection-table">
												<thead>
													<tr>
														<th>Select</th>
														<th>Work item</th>
														<th>Title</th>
														<th>Type</th>
														<th>State</th>
														<th>Parent</th>
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
																	<input
																		type="radio"
																		name="azure-devops-work-item-selection"
																		checked={selected}
																		onChange={() => setSelectedAzureDevOpsWorkItemId(`${workItem.work_item_id}`)}
																		aria-label={`Select Azure DevOps work item ${workItem.work_item_id}`}
																	/>
																</td>
																<td><strong>#{workItem.work_item_id}</strong></td>
																<td>{workItem.title}</td>
																<td>{workItem.work_item_type || "—"}</td>
																<td>{workItem.state || "—"}</td>
																<td>{workItem.parent_id ? `#${workItem.parent_id}` : "—"}</td>
															</tr>
														);
													})}
												</tbody>
											</table>
										</div>
									) : (
										<span className="helper-text">Search visible work items in the selected project to choose an import source.</span>
									)}

									<div className="panel-form button-row jira-import-actions">
										<button onClick={importRequirementsFromAzureDevOps} disabled={!selectedAzureDevOpsWorkItemId || isImportingFromAzureDevOps || requirementActionDisabled}>
											{isImportingFromAzureDevOps ? "⏳ Importing..." : `Import ${selectedAzureDevOpsWorkItem ? `#${selectedAzureDevOpsWorkItem.work_item_id}` : azureDevOpsWorkItemType || "work item"}`}
										</button>
										{selectedAzureDevOpsWorkItem ? <span className="helper-text">Selected source: #{selectedAzureDevOpsWorkItem.work_item_id} — {selectedAzureDevOpsWorkItem.title}</span> : null}
									</div>
								</div>
							</div>
						)}


						{rawText && (
							<div className="result-section compact-result-section">
								<details className="collapsible-panel raw-text-panel">
									<summary className="collapsible-panel-summary">
										<span className="collapsible-panel-copy">
											<span className="collapsible-panel-title">Raw extracted text</span>
											<span className="collapsible-panel-description">Open only when you need to inspect parser input.</span>
										</span>
										<span className="collapsible-panel-meta">
											<span className="analysis-summary-pill">{rawText.length.toLocaleString()} chars</span>
											<span className="collapsible-panel-icon" aria-hidden="true">⏄</span>
										</span>
									</summary>
									<div className="collapsible-panel-body">
										<pre className="raw-text-pre">{rawText}</pre>
									</div>
								</details>
							</div>
						)}

						<RequirementReviewWorkbench
							requirements={requirements}
							approvedRequirementCount={approvedRequirementCount}
							reviewPendingRequirementCount={reviewPendingRequirementCount}
							rejectedRequirementCount={rejectedRequirementCount}
							onApproveNonRejected={() => bulkUpdateRequirementReviewStatus("Approved", (requirement) => getRequirementReviewStatus(requirement) !== "Rejected")}
							onMarkAllNeedsReview={() => bulkUpdateRequirementReviewStatus("Needs Review")}
							onReviewStatusChange={updateRequirementReviewStatus}
							onQualityFlagToggle={toggleRequirementQualityFlag}
						/>

						{hasJiraRequirements && (
							<div className="jira-sync-panel">
								<div className="jira-card-header">
									<div>
										<h3>JIRA Sync Preview</h3>
										<p>Preview the managed requirement block that will be written back to JIRA and then push ready updates explicitly.</p>
									</div>
									<div className="jira-connection-summary compact">
										{jiraImportedIssueKeys.map((issueKey) => (
											<span key={issueKey} className="jira-summary-pill">{issueKey}</span>
										))}
									</div>
								</div>
								<div className="panel-form two-cols jira-sync-controls">
									<div className="form-group">
										<label>Managed section title</label>
										<input
											value={jiraManagedSectionTitle}
											onChange={(event) => setJiraManagedSectionTitle(event.target.value)}
											placeholder={DEFAULT_JIRA_SYNC_SECTION_TITLE}
										/>
									</div>
									<div className="feedback-actions jira-sync-actions">
										<button className="secondary" onClick={() => previewJiraSync()} disabled={authActionDisabled || isPreviewingJiraSync || isApplyingJiraSync}>
											{isPreviewingJiraSync ? "⏳ Previewing..." : "Preview JIRA Update"}
										</button>
										<button onClick={applyJiraSync} disabled={authActionDisabled || isApplyingJiraSync || !jiraSyncPreview || !jiraPreviewHasReadyIssue}>
											{isApplyingJiraSync ? "⏳ Syncing..." : "Push Ready Updates"}
										</button>
									</div>
								</div>

								{jiraSyncPreview && (
									<div className="jira-sync-results">
										<div className="workflow-diagnostics-pills">
											<span className="workflow-diagnostics-pill">Ready {jiraSyncPreview.ready_issue_count || 0}</span>
											<span className="workflow-diagnostics-pill">Conflicts {jiraSyncPreview.conflict_count || 0}</span>
											<span className="workflow-diagnostics-pill">Skipped {(jiraSyncPreview.skipped_requirement_ids || []).length}</span>
										</div>
										<div className="jira-sync-preview-list">
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
														{issue.issue_url ? <a href={issue.issue_url} target="_blank" rel="noreferrer">Open in JIRA ↗</a> : null}
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
										</div>
										{jiraSyncPreview.warnings?.length > 0 && (
											<ul className="jira-sync-warning-list">
												{jiraSyncPreview.warnings.map((warning) => <li key={warning}>{warning}</li>)}
											</ul>
										)}
									</div>
								)}

								{jiraSyncResults && (
									<div className="jira-sync-results-summary">
										<h4>Last sync result</h4>
										<ul className="jira-sync-apply-list">
											{jiraSyncResults.results?.map((result) => (
												<li key={`${result.issue_key}-${result.status}`}>
													<strong>{result.issue_key}</strong> — {result.status}{result.message ? `: ${result.message}` : ""}
												</li>
											))}
										</ul>
									</div>
								)}
							</div>
						)}

						{hasAzureDevOpsRequirements && (
							<div className="jira-sync-panel">
								<div className="jira-card-header">
									<div>
										<h3>Azure DevOps Sync Preview</h3>
										<p>Preview the managed requirements block that will be written back to Azure DevOps work item descriptions.</p>
									</div>
									<div className="jira-connection-summary compact">
										{azureDevOpsImportedWorkItemIds.map((workItemId) => (
											<span key={workItemId} className="jira-summary-pill">#{workItemId}</span>
										))}
									</div>
								</div>
								<div className="panel-form two-cols jira-sync-controls">
									<div className="form-group">
										<label>Managed section title</label>
										<input
											value={azureDevOpsManagedSectionTitle}
											onChange={(event) => setAzureDevOpsManagedSectionTitle(event.target.value)}
											placeholder={DEFAULT_AZURE_DEVOPS_SYNC_SECTION_TITLE}
										/>
									</div>
									<div className="feedback-actions jira-sync-actions">
										<button className="secondary" onClick={() => previewAzureDevOpsSync()} disabled={authActionDisabled || isPreviewingAzureDevOpsSync || isApplyingAzureDevOpsSync}>
											{isPreviewingAzureDevOpsSync ? "⏳ Previewing..." : "Preview Azure DevOps Update"}
										</button>
										<button onClick={applyAzureDevOpsSync} disabled={authActionDisabled || isApplyingAzureDevOpsSync || !azureDevOpsSyncPreview || !azureDevOpsPreviewHasReadyWorkItem}>
											{isApplyingAzureDevOpsSync ? "⏳ Syncing..." : "Push Ready Updates"}
										</button>
									</div>
								</div>

								{azureDevOpsSyncPreview && (
									<div className="jira-sync-results">
										<div className="workflow-diagnostics-pills">
											<span className="workflow-diagnostics-pill">Ready {azureDevOpsSyncPreview.ready_work_item_count || 0}</span>
											<span className="workflow-diagnostics-pill">Conflicts {azureDevOpsSyncPreview.conflict_count || 0}</span>
											<span className="workflow-diagnostics-pill">Skipped {(azureDevOpsSyncPreview.skipped_requirement_ids || []).length}</span>
										</div>
										<div className="jira-sync-preview-list">
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
														{workItem.work_item_url ? <a href={workItem.work_item_url} target="_blank" rel="noreferrer">Open in Azure DevOps ↗</a> : null}
													</div>
													{workItem.conflict_reason ? <p className="jira-sync-preview-warning">{workItem.conflict_reason}</p> : null}
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
										</div>
										{azureDevOpsSyncPreview.warnings?.length > 0 && (
											<ul className="jira-sync-warning-list">
												{azureDevOpsSyncPreview.warnings.map((warning) => <li key={warning}>{warning}</li>)}
											</ul>
										)}
									</div>
								)}

								{azureDevOpsSyncResults && (
									<div className="jira-sync-results-summary">
										<h4>Last sync result</h4>
										<ul className="jira-sync-apply-list">
											{azureDevOpsSyncResults.results?.map((result) => (
												<li key={`${result.work_item_id}-${result.status}`}>
													<strong>#{result.work_item_id}</strong> — {result.status}{result.message ? `: ${result.message}` : ""}
												</li>
											))}
										</ul>
									</div>
								)}
							</div>
						)}

						{renderRequirementReviewReport()}

						{requirements.length > 0 && (
							<div className="feedback-section">
								<h3>Human Feedback</h3>
								<p className="feedback-description">
									Provide feedback on the extracted requirements. The AI will refine them based on your input.
								</p>
								<textarea
									className="feedback-textarea"
									placeholder="Enter your feedback here... e.g., 'Merge REQ-003 and REQ-004 into one', 'Split REQ-001 into multiple requirements', 'REQ-005 is too vague, make it more specific', 'Add a requirement for error handling', etc."
									value={reqFeedback}
									onChange={(e) => setReqFeedback(e.target.value)}
									rows={4}
								/>
								<div className="feedback-actions">
									<button 
										onClick={() => parseRequirements(true)} 
										disabled={!reqFeedback.trim() || isParsing || requirementActionDisabled}
										className="feedback-button"
									>
										{isParsing ? "⏳ Refining Requirements..." : "🔄 Implement Changes"}
									</button>
								</div>
							</div>
						)}

						<div className="panel-nav">
							<button onClick={goNext} className="secondary">
								Next
							</button>
						</div>
					</section>
				)}

				{activeTab === 1 && (
					<ContextInputsPanel
						appLink={appLink}
						setAppLink={setAppLink}
						prototypeLink={prototypeLink}
						setPrototypeLink={setPrototypeLink}
						diagramLinks={diagramLinks}
						setDiagramLinks={setDiagramLinks}
						imageLinks={imageLinks}
						setImageLinks={setImageLinks}
						hasContextInputs={hasContextInputs}
						analyzeContext={analyzeContext}
						isAnalyzingContext={isAnalyzingContext}
						authActionDisabled={authActionDisabled}
						enrichedContext={enrichedContext}
						resetContextAnalysis={resetContextAnalysis}
						selectedArtifactSourceIds={selectedArtifactSourceIds}
						setSelectedArtifactSourceIds={setSelectedArtifactSourceIds}
						goPrev={goPrev}
						goNext={goNext}
					/>
				)}

				{activeTab === 2 && (
					<TemplateSetupPanel
						templateName={templateName}
						setTemplateName={setTemplateName}
						templateFormat={templateFormat}
						setTemplateFormat={setTemplateFormat}
						goPrev={goPrev}
						goNext={goNext}
					/>
				)}

				{activeTab === 3 && (
					<section className="panel">
						<h2 className="panel-title">Generate Test Cases</h2>
						<p className="panel-description">
							Generate structured test cases from approved requirements and context.
						</p>
						{requirements.length > 0 && (
							<div className={`generation-gate-card ${canGenerateFromApprovedRequirements ? "ready" : "blocked"}`}>
								<div>
									<strong>{canGenerateFromApprovedRequirements ? "Ready for approved-requirement generation" : "Approval required before generation"}</strong>
									<p>{approvedRequirementCount} approved • {reviewPendingRequirementCount} pending review • {rejectedRequirementCount} rejected. Only approved requirements are sent to the test-case agents.</p>
								</div>
								{!canGenerateFromApprovedRequirements && (
									<button type="button" className="secondary small" onClick={() => setActiveTab(0)}>Review requirements</button>
								)}
							</div>
						)}
						<div className="panel-form button-row">
							<button onClick={() => generateTestCases(false)} disabled={!canGenerateFromApprovedRequirements || isGenerating || testCaseActionDisabled}>
								{isGenerating ? "⏳ Generating..." : `Generate from ${approvedRequirementCount || 0} Approved`}
							</button>
						</div>

						{testCaseReview && (
							<div className={`review-banner ${testCaseReview.approved ? "review-approved" : "review-needs-work"}`}>
								<div className="review-banner-header">
									<strong>{testCaseReview.approved ? "Approved for export" : "Needs refinement"}</strong>
									<div className="review-banner-metrics">
										<span className="review-metric-pill review-metric-pill-strong">{testCaseReviewMeta.scoreLabel}</span>
										{testCaseReviewMeta.thresholdLabel && (
											<span className="review-metric-pill">{testCaseReviewMeta.thresholdLabel}</span>
										)}
									</div>
								</div>
								<p>{testCaseReview.summary || "The review loop completed without a summary."}</p>
								{!testCaseReview.approved && testCaseReview.blocking_issues?.length > 0 && (
									<ul className="review-issues">
										{testCaseReview.blocking_issues.slice(0, 3).map((issue) => (
											<li key={issue}>{issue}</li>
										))}
									</ul>
								)}
							</div>
						)}

						{hasGenerateResults ? (
							<div className="generate-results-workspace">
								<div className="generate-results-header">
									<div>
										<h3>Generation Results</h3>
										<p>Review the generated cases, traceability, coverage, analysis, and workflow diagnostics without scrolling through a wall of artifacts.</p>
									</div>
									<span className="generate-results-summary-pill">{testCases.length} test case{testCases.length === 1 ? "" : "s"}</span>
								</div>
								<div className="generate-results-tabs" role="tablist" aria-label="Generation result sections">
									{generateResultTabs.map((tab) => (
										<button
											type="button"
											key={tab.id}
											className={`generate-result-tab ${activeGenerateResultTab === tab.id ? "active" : ""} ${tab.variant ? `generate-result-tab-${tab.variant}` : ""}`}
											onClick={() => setActiveGenerateResultTab(tab.id)}
											role="tab"
											aria-selected={activeGenerateResultTab === tab.id}
											aria-controls="generate-result-panel"
										>
											<span>{tab.label}</span>
											<span className={`generate-result-tab-badge ${tab.variant ? `generate-result-tab-badge-${tab.variant}` : ""}`}>{tab.badge}</span>
										</button>
									))}
								</div>
								<div
									id="generate-result-panel"
									className="generate-result-panel"
									role="tabpanel"
									aria-label={generateResultTabs.find((tab) => tab.id === activeGenerateResultTab)?.label || "Generation result"}
								>
									{activeGenerateResultTab === "diagnostics" && (
										renderWorkflowDiagnostics(
											"Test-case workflow diagnostics",
											testCaseWorkflowDiagnostics,
											appliedTestCaseWorkflowSettings,
											testCaseIterationHistory,
										) || (
											<div className="generate-result-empty">
												<h3>Diagnostics</h3>
												<p>No workflow diagnostics are available for this run.</p>
											</div>
										)
									)}

									{activeGenerateResultTab === "coverage" && (
										<ScenarioCoveragePanel
											coveragePlan={coveragePlan}
											coveredScenarioTotal={coveredScenarioTotal}
											plannedScenarioTotal={plannedScenarioTotal}
											mustHaveCoveredScenarioTotal={mustHaveCoveredScenarioTotal}
											mustHaveScenarioTotal={mustHaveScenarioTotal}
											missingScenarioCount={missingScenarioCount}
											getRequirementScenarioSummary={getRequirementScenarioSummary}
										/>
									)}

									{activeGenerateResultTab === "analysis" && (
										<RequirementAnalysisPanel
											requirementAnalysis={requirementAnalysis}
											coverageMetrics={coverageMetrics}
											requirementAnalysisGapCount={requirementAnalysisGapCount}
											getRequirementAnalysisSummary={getRequirementAnalysisSummary}
											getRequirementAnalysisGaps={getRequirementAnalysisGaps}
										/>
									)}

									{activeGenerateResultTab === "traceability" && (
										<TraceabilityMatrixPanel
											approvedRequirements={approvedRequirements}
											requirementTraceabilityRows={requirementTraceabilityRows}
											tracedRequirementCount={tracedRequirementCount}
											coverageMetrics={coverageMetrics}
											testCases={testCases}
										/>
									)}

									{activeGenerateResultTab === "test-cases" && (
										<GeneratedTestCasesView
											testCases={testCases}
											templateFormat={templateFormat}
											expandedRows={expandedRows}
											onToggleRowExpansion={toggleRowExpansion}
											feedback={feedback}
											onFeedbackChange={setFeedback}
											onRefineTestCases={() => generateTestCases(true)}
											isGenerating={isGenerating}
											testCaseActionDisabled={testCaseActionDisabled}
										/>
									)}
								</div>
							</div>
						) : (
							<div className="result-section">
								<h3>Generated Test Cases</h3>
								<span className="helper-text">No generation run yet. Generate from approved requirements to view test cases, traceability, coverage, analysis, and diagnostics.</span>
							</div>
						)}

						<div className="panel-nav">
							<button onClick={goPrev} className="secondary">Back</button>
							<button onClick={goNext} disabled={testCases.length === 0}>Next</button>
						</div>
					</section>
				)}

				{activeTab === 4 && (
					<AutomationPanel
						testCases={testCases}
						executionTargetBaseUrl={executionTargetBaseUrl}
						setExecutionTargetBaseUrl={setExecutionTargetBaseUrl}
						executionPreview={executionPreview}
						executionRunResult={executionRunResult}
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
						goPrev={goPrev}
					/>
				)}
			</div>
		</div>
	);
}
