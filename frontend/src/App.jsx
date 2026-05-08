import React, { useEffect, useState } from "react";
import { onAuthStateChanged, signInWithPopup, signOut } from "firebase/auth";
import {
	createFirebaseAuthProvider,
	firebaseAuth,
	firebaseAuthHandlerUrl,
	hasFirebaseAuthConfig,
	visibleFirebaseAuthProviders,
} from "./firebase";
import "./App.css";

const API_BASE = (() => {
	const configuredApiBase = (import.meta.env.VITE_API_BASE || "").trim();
	if (!configuredApiBase) {
		return "http://127.0.0.1:8000";
	}
	return configuredApiBase === "http://localhost:8000" ? "http://127.0.0.1:8000" : configuredApiBase;
})();
const STORAGE_AUTH_TOKEN = "tcg.auth.token";
const STORAGE_AUTH_USER = "tcg.auth.user";
const AUTH_REQUIRED_MESSAGE = "Sign in to continue.";
const EMPTY_WORKFLOW_SETTINGS = {
	approval_threshold: "",
	max_iterations: "",
	timeout_seconds: "",
	stall_iteration_limit: "",
	retry_attempts: "",
};
const WORKFLOW_SETTING_FIELDS = [
	{ key: "approval_threshold", label: "Approval threshold", min: 0, max: 100 },
	{ key: "max_iterations", label: "Max iterations", min: 1, max: 20 },
	{ key: "timeout_seconds", label: "Timeout (seconds)", min: 1, max: 900 },
	{ key: "stall_iteration_limit", label: "Stall limit", min: 1, max: 20 },
	{ key: "retry_attempts", label: "Retry attempts", min: 0, max: 5 },
];
const USAGE_STATUS_ITEMS = [
	{ key: "requirementsGeneratedCount", label: "Req +" },
	{ key: "requirementsModifiedCount", label: "Req Δ" },
	{ key: "testCasesGeneratedCount", label: "TC +" },
	{ key: "testCasesModifiedCount", label: "TC Δ" },
];
const PILOT_WARNING_THRESHOLD = 20;
const EMPTY_JIRA_CONNECTION_STATUS = {
	connected: false,
	connection: null,
};
const EMPTY_JIRA_CONNECTION_FORM = {
	baseUrl: "",
	email: "",
	apiToken: "",
};
const EMPTY_AZURE_DEVOPS_CONNECTION_STATUS = {
	connected: false,
	connection: null,
};
const EMPTY_AZURE_DEVOPS_CONNECTION_FORM = {
	organizationUrl: "",
	accountEmail: "",
	personalAccessToken: "",
};
const DEFAULT_JIRA_ISSUE_TYPE_OPTIONS = ["Epic", "Story", "Task", "Bug"];
const DEFAULT_AZURE_DEVOPS_WORK_ITEM_TYPE_OPTIONS = ["Epic", "Feature", "User Story", "Task", "Bug"];
const DEFAULT_SYNC_SECTION_TITLE = "Agentic Requirements";
const DEFAULT_JIRA_SYNC_SECTION_TITLE = DEFAULT_SYNC_SECTION_TITLE;
const DEFAULT_AZURE_DEVOPS_SYNC_SECTION_TITLE = DEFAULT_SYNC_SECTION_TITLE;
const REQUIREMENT_REVIEW_STATUSES = ["Draft", "Needs Review", "Approved", "Rejected"];
const REQUIREMENT_QUALITY_FLAG_OPTIONS = [
	"Ambiguous",
	"Duplicate",
	"Untestable",
	"Missing actor",
	"Missing expected result",
	"Needs split",
	"Needs merge",
	"Out of scope",
];
const JIRA_SOURCE_FIELDS = [
	"source_system",
	"source_issue_key",
	"source_issue_type",
	"source_parent_key",
	"source_parent_title",
	"source_issue_url",
	"source_issue_updated_at",
	"source_path",
	"source_section",
	"source_excerpt",
	"source_hierarchy",
	"parent_requirement_id",
	"review_status",
	"quality_flags",
	"sync_target_issue_key",
	"artifact_set_id",
	"artifact_item_id",
	"artifact_version_id",
	"artifact_version_number",
];

const buildJiraConnectionForm = (connection, user) => ({
	baseUrl: connection?.base_url || "",
	email: connection?.email || user?.email || "",
	apiToken: "",
});

const buildAzureDevOpsConnectionForm = (connection, user) => ({
	organizationUrl: connection?.organization_url || "",
	accountEmail: connection?.account_email || user?.email || "",
	personalAccessToken: "",
});

const isJiraLinkedRequirement = (requirement) => Boolean(
	requirement?.source_system === "jira"
	|| (!requirement?.source_system && (requirement?.source_issue_key || requirement?.sync_target_issue_key || requirement?.artifact_item_id))
);

const isAzureDevOpsLinkedRequirement = (requirement) => Boolean(requirement?.source_system === "azure_devops");

const getRequirementSourceLabel = (requirement) => {
	if (requirement?.source_system === "azure_devops") {
		return "Azure DevOps";
	}
	if (requirement?.source_system === "file") {
		return "File";
	}
	if (requirement?.source_system === "jira" || requirement?.source_issue_key || requirement?.sync_target_issue_key) {
		return "JIRA";
	}
	return "Source";
};

const normalizeStringArray = (value) => {
	if (Array.isArray(value)) {
		return [...new Set(value.map((item) => `${item || ""}`.trim()).filter(Boolean))];
	}
	const normalized = `${value || ""}`.trim();
	return normalized ? [normalized] : [];
};

const getRequirementReviewStatus = (requirement) => {
	const status = `${requirement?.review_status || "Draft"}`.trim();
	return REQUIREMENT_REVIEW_STATUSES.includes(status) ? status : "Draft";
};

const getRequirementContextPath = (requirement) => {
	const hierarchy = normalizeStringArray(requirement?.source_hierarchy);
	if (hierarchy.length) {
		return hierarchy.join(" › ");
	}
	if (requirement?.source_path) {
		return requirement.source_path;
	}
	const sourceKey = requirement?.source_issue_key || requirement?.sync_target_issue_key;
	if (sourceKey) {
		return [requirement?.source_parent_key, sourceKey, requirement?.source_section].filter(Boolean).join(" › ");
	}
	return "Imported requirements";
};

const groupRequirementsByContext = (items = []) => {
	const groups = new Map();
	items.forEach((requirement, index) => {
		const contextPath = getRequirementContextPath(requirement);
		if (!groups.has(contextPath)) {
			groups.set(contextPath, {
				id: contextPath || `group-${groups.size + 1}`,
				label: contextPath || "Imported requirements",
				sourceLabel: getRequirementSourceLabel(requirement),
				requirements: [],
			});
		}
		groups.get(contextPath).requirements.push({ ...requirement, __index: index });
	});
	return [...groups.values()];
};

const getTestCaseLinkedRequirementIds = (testCase) => {
	const explicit = normalizeStringArray(testCase?.linked_requirement_ids);
	const tagLinks = normalizeStringArray(testCase?.tags).filter((tag) => /^REQ-[A-Za-z0-9_-]+$/i.test(tag));
	return [...new Set([...explicit, ...tagLinks])];
};

const mergeRequirementMetadata = (nextRequirements = [], previousRequirements = []) => {
	const previousList = Array.isArray(previousRequirements) ? previousRequirements : [];
	const nextList = Array.isArray(nextRequirements) ? nextRequirements : [];
	if (!previousList.length || !nextList.length) {
		return nextList;
	}

	const previousByArtifactId = new Map();
	const previousById = new Map();
	const resolvedTargets = previousList
		.map((requirement) => requirement?.sync_target_issue_key || requirement?.source_issue_key || "")
		.filter(Boolean);
	const uniqueResolvedTargets = [...new Set(resolvedTargets)];
	const defaultSyncTargetKey = uniqueResolvedTargets.length === 1 ? uniqueResolvedTargets[0] : null;

	previousList.forEach((requirement) => {
		if (requirement?.artifact_item_id) {
			previousByArtifactId.set(requirement.artifact_item_id, requirement);
		}
		if (requirement?.id) {
			previousById.set(requirement.id, requirement);
		}
	});

	return nextList.map((requirement, index) => {
		const matchedRequirement = (
			(requirement?.artifact_item_id && previousByArtifactId.get(requirement.artifact_item_id))
			|| previousById.get(requirement?.id)
			|| (nextList.length === previousList.length ? previousList[index] : null)
		);

		const metadata = JIRA_SOURCE_FIELDS.reduce((acc, field) => {
			if ((requirement?.[field] == null || requirement?.[field] === "") && matchedRequirement?.[field] != null && matchedRequirement?.[field] !== "") {
				acc[field] = matchedRequirement[field];
			}
			return acc;
		}, {});

		if (!requirement?.sync_target_issue_key && !metadata.sync_target_issue_key && defaultSyncTargetKey && !matchedRequirement) {
			metadata.sync_target_issue_key = defaultSyncTargetKey;
		}

		return Object.keys(metadata).length ? { ...requirement, ...metadata } : requirement;
	});
};

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

const AuthProviderIcon = ({ providerId }) => {
	if (providerId === "google") {
		return (
			<svg viewBox="0 0 18 18" aria-hidden="true" focusable="false">
				<path fill="#4285F4" d="M17.64 9.2045c0-.6382-.0573-1.2518-.1636-1.8409H9v3.4818h4.8436c-.2086 1.125-.8427 2.0782-1.796 2.7164v2.2582h2.9087c1.7018-1.5663 2.6837-3.874 2.6837-6.6155z" />
				<path fill="#34A853" d="M9 18c2.43 0 4.4673-.8064 5.9564-2.1818l-2.9087-2.2582c-.8063.54-1.8372.8591-3.0477.8591-2.3427 0-4.3241-1.5818-5.0327-3.7091H.96v2.3318C2.4409 15.9836 5.4818 18 9 18z" />
				<path fill="#FBBC05" d="M3.9673 10.7091c-.18-.54-.2836-1.1164-.2836-1.7091s.1036-1.1691.2836-1.7091V4.9591H.96C.3477 6.1791 0 7.5509 0 9s.3477 2.8209.96 4.0409l3.0073-2.3318z" />
				<path fill="#EA4335" d="M9 3.5809c1.3214 0 2.5077.4541 3.44 1.3455l2.5818-2.5818C13.4636.8909 11.43 0 9 0 5.4818 0 2.4409 2.0164.96 4.9591l3.0073 2.3318C4.6759 5.1627 6.6573 3.5809 9 3.5809z" />
			</svg>
		);
	}

	if (providerId === "microsoft") {
		return (
			<svg viewBox="0 0 18 18" aria-hidden="true" focusable="false">
				<rect x="1" y="1" width="7" height="7" fill="#F25022" />
				<rect x="10" y="1" width="7" height="7" fill="#7FBA00" />
				<rect x="1" y="10" width="7" height="7" fill="#00A4EF" />
				<rect x="10" y="10" width="7" height="7" fill="#FFB900" />
			</svg>
		);
	}

	if (providerId === "apple") {
		return (
			<svg viewBox="0 0 384 512" aria-hidden="true" focusable="false">
				<path fill="currentColor" d="M318.7 268.7c-.2-38.2 31.2-56.5 32.6-57.4-17.8-26-45.4-29.6-55.3-30-23.5-2.4-45.8 13.8-57.7 13.8-11.8 0-30-13.4-49.3-13-25.3.4-48.7 14.7-61.7 37.5-26.3 45.6-6.7 113 18.7 149.8 12.4 17.9 27.1 38.1 46.5 37.3 18.7-.8 25.7-12 48.5-12 22.7 0 29 12 48.8 11.6 20.2-.4 33-18.1 45.3-36.1 14.2-20.8 20.1-41 20.3-42-.4-.2-36.8-14.1-37.2-56.5zm-40.6-100.1c10.3-12.5 17.2-29.8 15.3-47.1-14.7.6-32.8 9.8-43.4 22.3-9.5 10.9-17.8 28.4-15.6 45.1 16.5 1.3 33.4-8.4 43.7-20.3z" />
			</svg>
		);
	}

	return null;
};

const normalizeUsageMetric = (value) => {
	const parsed = Number.parseInt(`${value ?? 0}`, 10);
	return Number.isFinite(parsed) ? parsed : 0;
};

const createRequestId = () => {
	if (globalThis.crypto?.randomUUID) {
		return globalThis.crypto.randomUUID();
	}
	return `tcg-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const formatPlanLabel = (planTier) => {
	const normalized = `${planTier || "pilot"}`.trim().toLowerCase();
	if (!normalized) {
		return "Pilot";
	}
	return normalized.charAt(0).toUpperCase() + normalized.slice(1);
};

const buildEmptyUsageSummary = (user) => ({
	scopeType: "individual",
	scopeKey: user?.sub ? `user:${user.sub}` : "user:current",
	displayName: user?.name || user?.email || user?.sub || "Current user",
	totalEvents: 0,
	requirementsGeneratedCount: 0,
	requirementsModifiedCount: 0,
	testCasesGeneratedCount: 0,
	testCasesModifiedCount: 0,
	hasData: false,
});

const buildUsageSummaryFromSource = (source, user, group, hasData = true) => ({
	scopeType: group?.scope_type || "individual",
	scopeKey: group?.scope_key || (user?.sub ? `user:${user.sub}` : "user:current"),
	displayName: source?.name || source?.email || group?.display_name || user?.name || user?.email || user?.sub || "Current user",
	totalEvents: normalizeUsageMetric(source?.total_events),
	requirementsGeneratedCount: normalizeUsageMetric(source?.requirements_generated_count),
	requirementsModifiedCount: normalizeUsageMetric(source?.requirements_modified_count),
	testCasesGeneratedCount: normalizeUsageMetric(source?.test_cases_generated_count),
	testCasesModifiedCount: normalizeUsageMetric(source?.test_cases_modified_count),
	hasData,
});

const getCurrentUserUsageSummary = (report, user) => {
	if (!user) {
		return null;
	}

	const fallback = buildEmptyUsageSummary(user);
	const subject = `${user.sub || ""}`.trim();
	const email = `${user.email || ""}`.trim().toLowerCase();
	const groups = Array.isArray(report?.groups) ? report.groups : [];
	const matchesUser = (candidate) => {
		const candidateUserId = `${candidate?.user_id || ""}`.trim();
		const candidateEmail = `${candidate?.email || ""}`.trim().toLowerCase();
		return (subject && candidateUserId === subject) || (email && candidateEmail === email);
	};

	for (const group of groups) {
		const users = Array.isArray(group?.users) ? group.users : [];
		const matchedUser = users.find(matchesUser);
		if (matchedUser) {
			return buildUsageSummaryFromSource(matchedUser, user, group, true);
		}

		if (group?.scope_type === "individual") {
			const scopeKey = `${group?.scope_key || ""}`.trim();
			const displayName = `${group?.display_name || ""}`.trim().toLowerCase();
			if ((subject && scopeKey === `user:${subject}`) || (email && displayName === email)) {
				return buildUsageSummaryFromSource(group, user, group, true);
			}
		}
	}

	return fallback;
};

const buildWorkflowSettingsPayload = (settings) => {
	const payload = Object.entries(settings || {}).reduce((acc, [key, value]) => {
		const normalized = `${value ?? ""}`.trim();
		if (!normalized) {
			return acc;
		}
		const parsed = Number.parseInt(normalized, 10);
		if (Number.isFinite(parsed)) {
			acc[key] = parsed;
		}
		return acc;
	}, {});

	return Object.keys(payload).length ? payload : null;
};

export default function App() {
	const [file, setFile] = useState(null);
	const [rawText, setRawText] = useState("");
	const [requirements, setRequirements] = useState([]);
	const [requirementReview, setRequirementReview] = useState(null);
	const [requirementCoverageMetrics, setRequirementCoverageMetrics] = useState(null);
	const [requirementWorkflowDiagnostics, setRequirementWorkflowDiagnostics] = useState(null);
	const [appliedRequirementWorkflowSettings, setAppliedRequirementWorkflowSettings] = useState(null);
	const [requirementIterationHistory, setRequirementIterationHistory] = useState([]);
	const [activeTab, setActiveTab] = useState(0);
	const [appLink, setAppLink] = useState("");
	const [prototypeLink, setPrototypeLink] = useState("");
	const [diagramLinks, setDiagramLinks] = useState("");
	const [imageLinks, setImageLinks] = useState("");
	const [templateName, setTemplateName] = useState("default");
	const [templateFormat, setTemplateFormat] = useState("table");
	const [testCases, setTestCases] = useState([]);
	const [requirementAnalysis, setRequirementAnalysis] = useState([]);
	const [coveragePlan, setCoveragePlan] = useState([]);
	const [coverageMetrics, setCoverageMetrics] = useState(null);
	const [testCaseReview, setTestCaseReview] = useState(null);
	const [testCaseWorkflowDiagnostics, setTestCaseWorkflowDiagnostics] = useState(null);
	const [appliedTestCaseWorkflowSettings, setAppliedTestCaseWorkflowSettings] = useState(null);
	const [testCaseIterationHistory, setTestCaseIterationHistory] = useState([]);
	const [enrichedContext, setEnrichedContext] = useState(null);
	const [selectedArtifactSourceIds, setSelectedArtifactSourceIds] = useState([]);
	const [status, setStatus] = useState("");
	const [feedback, setFeedback] = useState("");
	const [reqFeedback, setReqFeedback] = useState("");
	const [requirementWorkflowSettings, setRequirementWorkflowSettings] = useState(EMPTY_WORKFLOW_SETTINGS);
	const [testCaseWorkflowSettings, setTestCaseWorkflowSettings] = useState(EMPTY_WORKFLOW_SETTINGS);
	const [expandedRows, setExpandedRows] = useState({});
	const [activeGenerateResultTab, setActiveGenerateResultTab] = useState("test-cases");
	const [isGenerating, setIsGenerating] = useState(false);
	const [isParsing, setIsParsing] = useState(false);
	const [isAnalyzingContext, setIsAnalyzingContext] = useState(false);
	const [isExporting, setIsExporting] = useState(false);
	const [authToken, setAuthToken] = useState("");
	const [currentUser, setCurrentUser] = useState(null);
	const [isAuthenticating, setIsAuthenticating] = useState(false);
	const [activeAuthProvider, setActiveAuthProvider] = useState("");
	const [isSignInDialogOpen, setIsSignInDialogOpen] = useState(false);
	const [isSettingsDialogOpen, setIsSettingsDialogOpen] = useState(false);
	const [settingsSection, setSettingsSection] = useState("workflow");
	const [isVerifyingSession, setIsVerifyingSession] = useState(true);
	const [usageSummary, setUsageSummary] = useState(null);
	const [isUsageLoading, setIsUsageLoading] = useState(false);
	const [billingEntitlements, setBillingEntitlements] = useState(null);
	const [isBillingLoading, setIsBillingLoading] = useState(false);
	const [requirementSourceMode, setRequirementSourceMode] = useState("file");
	const [jiraConnectionStatus, setJiraConnectionStatus] = useState(EMPTY_JIRA_CONNECTION_STATUS);
	const [jiraConnectionForm, setJiraConnectionForm] = useState(EMPTY_JIRA_CONNECTION_FORM);
	const [isJiraConnectionLoading, setIsJiraConnectionLoading] = useState(false);
	const [isSavingJiraConnection, setIsSavingJiraConnection] = useState(false);
	const [isDeletingJiraConnection, setIsDeletingJiraConnection] = useState(false);
	const [jiraProjectQuery, setJiraProjectQuery] = useState("");
	const [jiraProjects, setJiraProjects] = useState([]);
	const [selectedJiraProjectKey, setSelectedJiraProjectKey] = useState("");
	const [isLoadingJiraProjects, setIsLoadingJiraProjects] = useState(false);
	const [jiraProjectIssueTypes, setJiraProjectIssueTypes] = useState([]);
	const [isLoadingJiraIssueTypes, setIsLoadingJiraIssueTypes] = useState(false);
	const [jiraIssueType, setJiraIssueType] = useState("");
	const [jiraIssueQuery, setJiraIssueQuery] = useState("");
	const [jiraIssueResults, setJiraIssueResults] = useState([]);
	const [selectedJiraIssueKey, setSelectedJiraIssueKey] = useState("");
	const [isSearchingJiraIssues, setIsSearchingJiraIssues] = useState(false);
	const [isImportingFromJira, setIsImportingFromJira] = useState(false);
	const [jiraSyncPreview, setJiraSyncPreview] = useState(null);
	const [jiraSyncResults, setJiraSyncResults] = useState(null);
	const [jiraManagedSectionTitle, setJiraManagedSectionTitle] = useState(DEFAULT_JIRA_SYNC_SECTION_TITLE);
	const [isPreviewingJiraSync, setIsPreviewingJiraSync] = useState(false);
	const [isApplyingJiraSync, setIsApplyingJiraSync] = useState(false);
	const [azureDevOpsConnectionStatus, setAzureDevOpsConnectionStatus] = useState(EMPTY_AZURE_DEVOPS_CONNECTION_STATUS);
	const [azureDevOpsConnectionForm, setAzureDevOpsConnectionForm] = useState(EMPTY_AZURE_DEVOPS_CONNECTION_FORM);
	const [isAzureDevOpsConnectionLoading, setIsAzureDevOpsConnectionLoading] = useState(false);
	const [isSavingAzureDevOpsConnection, setIsSavingAzureDevOpsConnection] = useState(false);
	const [isDeletingAzureDevOpsConnection, setIsDeletingAzureDevOpsConnection] = useState(false);
	const [azureDevOpsProjectQuery, setAzureDevOpsProjectQuery] = useState("");
	const [azureDevOpsProjects, setAzureDevOpsProjects] = useState([]);
	const [selectedAzureDevOpsProject, setSelectedAzureDevOpsProject] = useState("");
	const [isLoadingAzureDevOpsProjects, setIsLoadingAzureDevOpsProjects] = useState(false);
	const [azureDevOpsWorkItemTypes, setAzureDevOpsWorkItemTypes] = useState([]);
	const [isLoadingAzureDevOpsWorkItemTypes, setIsLoadingAzureDevOpsWorkItemTypes] = useState(false);
	const [azureDevOpsWorkItemType, setAzureDevOpsWorkItemType] = useState("");
	const [azureDevOpsWorkItemQuery, setAzureDevOpsWorkItemQuery] = useState("");
	const [azureDevOpsWorkItemResults, setAzureDevOpsWorkItemResults] = useState([]);
	const [selectedAzureDevOpsWorkItemId, setSelectedAzureDevOpsWorkItemId] = useState("");
	const [isSearchingAzureDevOpsWorkItems, setIsSearchingAzureDevOpsWorkItems] = useState(false);
	const [isImportingFromAzureDevOps, setIsImportingFromAzureDevOps] = useState(false);
	const [azureDevOpsSyncPreview, setAzureDevOpsSyncPreview] = useState(null);
	const [azureDevOpsSyncResults, setAzureDevOpsSyncResults] = useState(null);
	const [azureDevOpsManagedSectionTitle, setAzureDevOpsManagedSectionTitle] = useState(DEFAULT_AZURE_DEVOPS_SYNC_SECTION_TITLE);
	const [isPreviewingAzureDevOpsSync, setIsPreviewingAzureDevOpsSync] = useState(false);
	const [isApplyingAzureDevOpsSync, setIsApplyingAzureDevOpsSync] = useState(false);

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
		setTestCases([]);
		setRequirementAnalysis([]);
		setCoveragePlan([]);
		setCoverageMetrics(null);
		setTestCaseReview(null);
		setTestCaseWorkflowDiagnostics(null);
		setAppliedTestCaseWorkflowSettings(null);
		setTestCaseIterationHistory([]);
		setExpandedRows({});
		setActiveGenerateResultTab("test-cases");
		setFeedback("");
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

	const resetContextAnalysis = () => {
		setEnrichedContext(null);
		setSelectedArtifactSourceIds([]);
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

	const parseApiError = async (res, fallbackMessage) => {
		const text = await res.text();
		if (!text) return fallbackMessage;
		try {
			const parsed = JSON.parse(text);
			if (typeof parsed?.detail === "string") {
				return parsed.detail;
			}
			if (parsed?.detail?.message) {
				const contactEmail = parsed?.detail?.contact_email;
				return contactEmail ? `${parsed.detail.message} Contact ${contactEmail}.` : parsed.detail.message;
			}
			return parsed?.message || fallbackMessage;
		} catch {
			return text;
		}
	};

	const updateWorkflowSetting = (setter, key) => (event) => {
		setter((prev) => ({ ...prev, [key]: event.target.value }));
	};

	const renderWorkflowSettingsPanel = (title, description, settings, setSettings) => (
		<div className="workflow-settings-panel">
			<div className="workflow-settings-header">
				<div>
					<h3>{title}</h3>
					<p>{description}</p>
				</div>
				<span className="workflow-settings-badge">Optional</span>
			</div>
			<div className="workflow-settings-grid">
				{WORKFLOW_SETTING_FIELDS.map((field) => (
					<div className="form-group" key={field.key}>
						<label>{field.label}</label>
						<input
							type="number"
							min={field.min}
							max={field.max}
							placeholder="Use backend default"
							value={settings[field.key]}
							onChange={updateWorkflowSetting(setSettings, field.key)}
						/>
					</div>
				))}
			</div>
			<p className="workflow-settings-help">Leave any field blank to use the backend default for that workflow.</p>
		</div>
	);

	const renderWorkflowDiagnostics = (title, diagnostics, appliedSettings, iterationHistory) => {
		if (!diagnostics && !appliedSettings) {
			return null;
		}

		const warnings = diagnostics?.warnings || [];
		const parserFailures = diagnostics?.parser_failures || [];
		const pillEntries = [
			appliedSettings?.approval_threshold != null ? `Threshold ${appliedSettings.approval_threshold}` : null,
			appliedSettings?.max_iterations != null ? `Max iter ${appliedSettings.max_iterations}` : null,
			diagnostics?.status ? `Status ${diagnostics.status}` : null,
			iterationHistory?.length ? `Iterations ${iterationHistory.length}` : null,
			diagnostics?.best_iteration ? `Best iter ${diagnostics.best_iteration}` : null,
			diagnostics?.timed_out ? "Timed out" : null,
			diagnostics?.stalled ? "Stalled" : null,
			diagnostics?.used_fallback ? "Fallback used" : null,
		].filter(Boolean);

		return (
			<div className="workflow-diagnostics-panel">
				<div className="workflow-diagnostics-header">
					<h3>{title}</h3>
					{diagnostics?.failure_reason && <span className="workflow-diagnostics-reason">Reason: {diagnostics.failure_reason}</span>}
				</div>
				{pillEntries.length > 0 && (
					<div className="workflow-diagnostics-pills">
						{pillEntries.map((entry) => (
							<span className="workflow-diagnostics-pill" key={entry}>{entry}</span>
						))}
					</div>
				)}
				{warnings.length > 0 && (
					<div className="workflow-diagnostics-block warning">
						<strong>Warnings</strong>
						<ul>
							{warnings.map((warning) => <li key={warning}>{warning}</li>)}
						</ul>
					</div>
				)}
				{parserFailures.length > 0 && (
					<div className="workflow-diagnostics-block alert">
						<strong>Parser issues</strong>
						<ul>
							{parserFailures.map((failure) => <li key={failure}>{failure}</li>)}
						</ul>
					</div>
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

	useEffect(() => {
		if (!isSignInDialogOpen) {
			return undefined;
		}

		const handleKeyDown = (event) => {
			if (event.key === "Escape") {
				closeSignInDialog();
			}
		};

		window.addEventListener("keydown", handleKeyDown);
		return () => window.removeEventListener("keydown", handleKeyDown);
	}, [isSignInDialogOpen, isAuthenticating]);

	useEffect(() => {
		if (!isSettingsDialogOpen) {
			return undefined;
		}

		const handleKeyDown = (event) => {
			if (event.key === "Escape") {
				closeSettingsDialog();
			}
		};

		window.addEventListener("keydown", handleKeyDown);
		return () => window.removeEventListener("keydown", handleKeyDown);
	}, [isSettingsDialogOpen]);

	const apiRequest = async (path, options = {}, authRequired = true) => {
		const headers = { ...(options.headers || {}) };

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

	const resetJiraSyncState = () => {
		setJiraSyncPreview(null);
		setJiraSyncResults(null);
	};

	const resetAzureDevOpsSyncState = () => {
		setAzureDevOpsSyncPreview(null);
		setAzureDevOpsSyncResults(null);
	};

	const resetIntegrationSyncState = () => {
		resetJiraSyncState();
		resetAzureDevOpsSyncState();
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

	const renderJiraConnectionSettings = () => (
		<div className="jira-card settings-integration-card">
			<div className="jira-card-header">
				<div>
					<h3>JIRA Cloud</h3>
					<p>Store a per-user JIRA Cloud connection so imports and managed requirement sync can use it later.</p>
				</div>
				{jiraConnected ? <span className="jira-status-badge connected">Connected</span> : <span className="jira-status-badge">Not connected</span>}
			</div>
			{jiraConnected && jiraConnection ? (
				<div className="jira-connection-summary">
					<span className="jira-summary-pill">{jiraConnection.display_name || jiraConnection.email}</span>
					<span className="jira-summary-pill">{jiraConnection.base_url}</span>
					{jiraConnection.api_token_hint && <span className="jira-summary-pill">Token {jiraConnection.api_token_hint}</span>}
				</div>
			) : null}
			{!jiraConnected ? (
				<div className="panel-form two-cols jira-connection-form">
					<div className="form-group">
						<label>JIRA base URL</label>
						<input
							placeholder="https://your-team.atlassian.net"
							value={jiraConnectionForm.baseUrl}
							onChange={(event) => setJiraConnectionForm((prev) => ({ ...prev, baseUrl: event.target.value }))}
						/>
					</div>
					<div className="form-group">
						<label>JIRA email</label>
						<input
							type="email"
							placeholder="qa@company.com"
							value={jiraConnectionForm.email}
							onChange={(event) => setJiraConnectionForm((prev) => ({ ...prev, email: event.target.value }))}
						/>
					</div>
					<div className="form-group jira-connection-token-group">
						<label>JIRA API token</label>
						<input
							type="password"
							placeholder="Paste your Atlassian API token"
							value={jiraConnectionForm.apiToken}
							onChange={(event) => setJiraConnectionForm((prev) => ({ ...prev, apiToken: event.target.value }))}
						/>
					</div>
					<div className="panel-form button-row jira-connection-actions">
						<button onClick={saveJiraConnection} disabled={authActionDisabled || isSavingJiraConnection || isJiraConnectionLoading}>
							{isSavingJiraConnection ? "⏳ Connecting..." : "Connect JIRA"}
						</button>
						{isJiraConnectionLoading && <span className="helper-text">Refreshing JIRA connection…</span>}
					</div>
				</div>
			) : (
				<div className="jira-connected-actions">
					<button className="secondary" onClick={() => refreshJiraConnectionStatus(currentUser)} disabled={authActionDisabled || isJiraConnectionLoading}>
						{isJiraConnectionLoading ? "⏳ Refreshing status..." : "Refresh Status"}
					</button>
					<button className="secondary" onClick={deleteStoredJiraConnection} disabled={authActionDisabled || isDeletingJiraConnection}>
						{isDeletingJiraConnection ? "⏳ Disconnecting..." : "Disconnect"}
					</button>
				</div>
			)}
		</div>
	);

	const renderAzureDevOpsConnectionSettings = () => (
		<div className="jira-card settings-integration-card">
			<div className="jira-card-header">
				<div>
					<h3>Azure DevOps</h3>
					<p>Store a per-user Azure DevOps connection so imports and managed requirement sync can use it later.</p>
				</div>
				{azureDevOpsConnected ? <span className="jira-status-badge connected">Connected</span> : <span className="jira-status-badge">Not connected</span>}
			</div>
			{azureDevOpsConnected && azureDevOpsConnection ? (
				<div className="jira-connection-summary">
					<span className="jira-summary-pill">{azureDevOpsConnection.display_name || azureDevOpsConnection.organization}</span>
					<span className="jira-summary-pill">{azureDevOpsConnection.organization_url}</span>
					{azureDevOpsConnection.default_project && <span className="jira-summary-pill">Default project {azureDevOpsConnection.default_project}</span>}
					{azureDevOpsConnection.token_hint && <span className="jira-summary-pill">PAT {azureDevOpsConnection.token_hint}</span>}
				</div>
			) : null}
			{!azureDevOpsConnected ? (
				<div className="panel-form two-cols jira-connection-form">
					<div className="form-group">
						<label>Azure DevOps organization or project URL</label>
						<input
							placeholder="https://dev.azure.com/{organization}/{project}"
							value={azureDevOpsConnectionForm.organizationUrl}
							onChange={(event) => setAzureDevOpsConnectionForm((prev) => ({ ...prev, organizationUrl: event.target.value }))}
						/>
					</div>
					<div className="form-group">
						<label>Account email (optional)</label>
						<input
							type="email"
							placeholder="you@company.com or personal@example.com"
							value={azureDevOpsConnectionForm.accountEmail}
							onChange={(event) => setAzureDevOpsConnectionForm((prev) => ({ ...prev, accountEmail: event.target.value }))}
						/>
					</div>
					<div className="form-group jira-connection-token-group">
						<label>Azure DevOps PAT</label>
						<input
							type="password"
							placeholder="Paste your Azure DevOps Personal Access Token"
							value={azureDevOpsConnectionForm.personalAccessToken}
							onChange={(event) => setAzureDevOpsConnectionForm((prev) => ({ ...prev, personalAccessToken: event.target.value }))}
						/>
						<span className="helper-text">Use a minimal PAT with Project/team read and Work Items read/write scopes. Microsoft app sign-in is separate from Azure DevOps API access.</span>
					</div>
					<div className="panel-form button-row jira-connection-actions">
						<button onClick={saveAzureDevOpsConnection} disabled={authActionDisabled || isSavingAzureDevOpsConnection || isAzureDevOpsConnectionLoading}>
							{isSavingAzureDevOpsConnection ? "⏳ Connecting..." : "Connect Azure DevOps"}
						</button>
						{isAzureDevOpsConnectionLoading && <span className="helper-text">Refreshing Azure DevOps connection…</span>}
					</div>
				</div>
			) : (
				<div className="jira-connected-actions">
					<button className="secondary" onClick={() => refreshAzureDevOpsConnectionStatus(currentUser)} disabled={authActionDisabled || isAzureDevOpsConnectionLoading}>
						{isAzureDevOpsConnectionLoading ? "⏳ Refreshing status..." : "Refresh Status"}
					</button>
					<button className="secondary" onClick={deleteStoredAzureDevOpsConnection} disabled={authActionDisabled || isDeletingAzureDevOpsConnection}>
						{isDeletingAzureDevOpsConnection ? "⏳ Disconnecting..." : "Disconnect"}
					</button>
				</div>
			)}
		</div>
	);

	const renderSettingsDialog = () => {
		if (!isSettingsDialogOpen) {
			return null;
		}

		return (
			<div className="auth-dialog-overlay settings-dialog-overlay" onClick={handleSettingsDialogOverlayClick}>
				<div
					className="settings-dialog"
					role="dialog"
					aria-modal="true"
					aria-labelledby="settings-dialog-title"
					onClick={(event) => event.stopPropagation()}
				>
					<div className="settings-dialog-header">
						<div>
							<h2 id="settings-dialog-title">Settings</h2>
							<p>Manage one-time connections and advanced workflow tuning without crowding the main pipeline.</p>
						</div>
						<button
							type="button"
							className="auth-dialog-close"
							onClick={closeSettingsDialog}
							aria-label="Close settings dialog"
						>
							×
						</button>
					</div>
					<div className="settings-dialog-nav" role="tablist" aria-label="Settings sections">
						<button
							type="button"
							className={`settings-nav-btn ${settingsSection === "workflow" ? "active" : ""}`}
							onClick={() => setSettingsSection("workflow")}
						>
							Workflow tuning
						</button>
						<button
							type="button"
							className={`settings-nav-btn ${settingsSection === "integrations" ? "active" : ""}`}
							onClick={() => setSettingsSection("integrations")}
						>
							Integrations
						</button>
					</div>
					<div className="settings-dialog-body">
						{settingsSection === "workflow" ? (
							<>
								<div className="settings-section-intro">
									<h3>Advanced workflow tuning</h3>
									<p>Leave fields blank for backend defaults. Adjust these only when you need stricter review gates or shorter AI loops.</p>
								</div>
								{renderWorkflowSettingsPanel(
									"Requirements workflow settings",
									"Tune the requirement review loop when you want stricter gates or shorter runs.",
									requirementWorkflowSettings,
									setRequirementWorkflowSettings,
								)}
								{renderWorkflowSettingsPanel(
									"Test-case workflow settings",
									"Control validation strictness, loop length, and timeout behavior for generation and refinement.",
									testCaseWorkflowSettings,
									setTestCaseWorkflowSettings,
								)}
							</>
						) : (
							<>
								<div className="settings-section-intro">
									<h3>Integration connections</h3>
									<p>Set these up once per user. Import/search/sync actions stay in the Upload workflow where they are used.</p>
								</div>
								{!isAuthenticated && (
									<div className="settings-auth-note">
										🔐 Sign in to create or manage JIRA and Azure DevOps connections.
									</div>
								)}
								<div className="settings-integration-grid">
									{renderJiraConnectionSettings()}
									{renderAzureDevOpsConnectionSettings()}
								</div>
							</>
						)}
					</div>
				</div>
			</div>
		);
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
			setStatus(withFeedback ? "Requirements refined." : "Parsed.");
			await Promise.all([refreshUsageSummary(), refreshBillingEntitlements()]);
			if (withFeedback) setReqFeedback("");
		} catch (error) {
			setStatus(`Parse failed: ${error.message}`);
		} finally {
			setIsParsing(false);
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
			const generatedCount = Array.isArray(data.test_cases) ? data.test_cases.length : 0;
			const reviewStatus = data.review
				? ` Review ${data.review.approved ? "approved" : "needs refinement"}.`
				: "";
			setStatus(
				`${withFeedback ? "Test cases refined" : "Generated"}${generatedCount ? ` ${generatedCount} test case${generatedCount === 1 ? "" : "s"}` : ""} from ${requirementsForGeneration.length} approved requirement${requirementsForGeneration.length === 1 ? "" : "s"}.${reviewStatus}`.trim()
			);
			await Promise.all([refreshUsageSummary(), refreshBillingEntitlements()]);
			if (withFeedback) setFeedback("");
		} catch (error) {
			setStatus(`Generation failed: ${error.message}`);
		} finally {
			setIsGenerating(false);
		}
	};

	const exportToFormat = async (format) => {
		setIsExporting(true);
		setStatus(`Exporting to ${format.toUpperCase()}...`);
		try {
			const payload = { test_cases: testCases };
			const res = await apiRequest(`/export/${format}`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(payload)
			});
			
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Export failed");
				throw new Error(errorMessage);
			}
			
			// Download the file
			const blob = await res.blob();
			const url = window.URL.createObjectURL(blob);
			const a = document.createElement("a");
			a.href = url;
			const extensions = { csv: "csv", excel: "xlsx", json: "json" };
			a.download = `test_cases.${extensions[format] || format}`;
			document.body.appendChild(a);
			a.click();
			a.remove();
			window.URL.revokeObjectURL(url);
			setStatus(`✓ Exported to ${format.toUpperCase()} successfully`);
		} catch (error) {
			setStatus(`Export failed: ${error.message}`);
		} finally {
			setIsExporting(false);
		}
	};

	const getPriorityClass = (priority) => {
		const map = { Critical: "priority-critical", High: "priority-high", Medium: "priority-medium", Low: "priority-low" };
		return map[priority] || "";
	};

	const getStatusClass = (status) => {
		const map = { Draft: "status-draft", Ready: "status-ready", "In Review": "status-review", Approved: "status-approved" };
		return map[status] || "";
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
		{ id: 4, label: "Export", title: "Export Test Cases" }
	];

	const goNext = () => setActiveTab((prev) => Math.min(prev + 1, tabs.length - 1));
	const goPrev = () => setActiveTab((prev) => Math.max(prev - 1, 0));
	const billingContactEmail = billingEntitlements?.account?.support_contact_email || "hello@spica-digital.eu";
	const billingStatusItems = billingEntitlements
		? [
			{ key: "plan", label: "Plan", value: formatPlanLabel(billingEntitlements.account?.plan_tier), variant: "neutral" },
			...(billingEntitlements.account?.plan_tier === "pilot"
				? [
					{ key: "requirementsRemaining", label: "Req left", value: normalizeUsageMetric(billingEntitlements.requirements?.remaining), variant: billingEntitlements.requirements?.exhausted ? "alert" : "default" },
					{ key: "testCasesRemaining", label: "TC left", value: normalizeUsageMetric(billingEntitlements.test_cases?.remaining), variant: billingEntitlements.test_cases?.exhausted ? "alert" : "default" },
				]
				: []),
			...(billingEntitlements.account?.plan_tier !== "pilot"
				? [{ key: "walletBalance", label: billingEntitlements.account?.plan_tier === "enterprise" ? "Allocation" : "Credits", value: billingEntitlements.wallet?.balance_token_display || "0", variant: normalizeUsageMetric(billingEntitlements.wallet?.balance_units) > 0 ? "default" : "alert" }]
				: []),
		]
		: [];
	const statusUsageItems = usageSummary
		? USAGE_STATUS_ITEMS.map((item) => ({
			...item,
			value: normalizeUsageMetric(usageSummary[item.key]),
			variant: "default",
		}))
		: [];
	const currentAuthProviderLabel = activeAuthProvider ? getAuthProviderLabel(activeAuthProvider) : "";
	const pilotAlert = (() => {
		if (!billingEntitlements || billingEntitlements.account?.plan_tier !== "pilot") {
			return null;
		}

		const requirementsRemaining = normalizeUsageMetric(billingEntitlements.requirements?.remaining);
		const testCasesRemaining = normalizeUsageMetric(billingEntitlements.test_cases?.remaining);
		const exhaustedFamilies = [];
		const lowFamilies = [];

		if (billingEntitlements.requirements?.exhausted) {
			exhaustedFamilies.push("requirements");
		} else if (requirementsRemaining <= PILOT_WARNING_THRESHOLD) {
			lowFamilies.push(`${requirementsRemaining} requirement actions left`);
		}

		if (billingEntitlements.test_cases?.exhausted) {
			exhaustedFamilies.push("test cases");
		} else if (testCasesRemaining <= PILOT_WARNING_THRESHOLD) {
			lowFamilies.push(`${testCasesRemaining} test-case actions left`);
		}

		if (!exhaustedFamilies.length && !lowFamilies.length && !billingEntitlements.shadow_mode) {
			return null;
		}

		if (billingEntitlements.shadow_mode) {
			return {
				variant: "preview",
				title: "Billing preview is active",
				message: exhaustedFamilies.length
					? `Pilot limits would block ${exhaustedFamilies.join(" and ")} once enforcement is enabled.`
					: lowFamilies.length
						? `Pilot balances are informational for now: ${lowFamilies.join(" • ")}.`
						: "Pilot balances are being calculated in shadow mode before hard enforcement is switched on.",
			};
		}

		if (exhaustedFamilies.length) {
			return {
				variant: "locked",
				title: `Pilot limit reached for ${exhaustedFamilies.join(" and ")}`,
				message: "Upgrade to premium or contact support to keep processing those workflows.",
			};
		}

		if (lowFamilies.length) {
			return {
				variant: "warning",
				title: "Pilot quota running low",
				message: lowFamilies.join(" • "),
			};
		}

		return null;
	})();

	return (
		<div className="page">
			<header className="header">
				<div>
					<h1 className="title">Agentic Test Case Generator</h1>
					<p className="subtitle">
						A guided pipeline to parse requirements, enrich context, generate test cases,
						and export polished artifacts.
					</p>
				</div>
				<div className="header-right">
					<div className={`status ${isAuthenticated ? "status-authenticated" : ""}`}>
						<strong>Status:</strong>
						<span className="status-message">{status || "Idle"}</span>
						{isAuthenticated && (
							<div className="status-usage" aria-label="Current user usage summary">
								{billingStatusItems.length > 0 ? (
									billingStatusItems.map((item) => (
										<span className={`status-usage-pill ${item.variant ? `status-usage-pill-${item.variant}` : ""}`} key={item.key}>
											<span className="status-usage-pill-label">{item.label}</span>
											<span className="status-usage-pill-value">{item.value}</span>
										</span>
									))
								) : null}
								{statusUsageItems.length > 0 ? (
									statusUsageItems.map((item) => (
										<span className={`status-usage-pill ${item.variant ? `status-usage-pill-${item.variant}` : ""}`} key={item.key}>
											<span className="status-usage-pill-label">{item.label}</span>
											<span className="status-usage-pill-value">{item.value}</span>
										</span>
									))
								) : null}
								{isUsageLoading || isBillingLoading ? (
									<span className="status-usage-loading">Loading usage…</span>
								) : null}
							</div>
						)}
					</div>
					<button
						type="button"
						className="settings-open-btn"
						data-testid="settings-open-button"
						onClick={() => openSettingsDialog("workflow")}
						aria-label="Open settings"
					>
						<span aria-hidden="true">⚙</span>
						Settings
					</button>
					<div className="auth-panel">
						{isVerifyingSession ? (
							<span className="auth-message">Checking session...</span>
						) : isAuthenticated ? (
							<div className="auth-user">
								{currentUser?.picture && (
									<img src={currentUser.picture} alt={currentUser.name} className="auth-avatar" />
								)}
								<div className="auth-user-meta">
									<strong>{currentUser?.name}</strong>
									<span>{currentUser?.email || getAuthProviderLabel(currentUser?.provider) || currentUser?.sub}</span>
								</div>
								<button
									type="button"
									onClick={handleLogout}
									className="secondary auth-logout-btn"
									disabled={isAuthenticating}
								>
									{isAuthenticating ? "Signing out..." : "Sign Out"}
								</button>
							</div>
						) : hasFirebaseAuthConfig && hasVisibleAuthProviders ? (
							<div className="auth-login">
								<button type="button" onClick={openSignInDialog} disabled={isAuthenticating}>
									{isAuthenticating && currentAuthProviderLabel
										? `Signing in with ${currentAuthProviderLabel}...`
										: isAuthenticating
											? "Signing in..."
											: "Sign In"}
								</button>
							</div>
						) : hasFirebaseAuthConfig ? (
							<span className="auth-message auth-config-missing">
								No Firebase sign-in providers are currently available.
							</span>
						) : (
							<span className="auth-message auth-config-missing">
								Set the VITE_FIREBASE_* variables to enable Firebase sign-in.
							</span>
						)}
					</div>
				</div>
			</header>

			{!isAuthenticated && !isVerifyingSession && (
				<div className="auth-warning-banner">
					🔐 Sign in to parse requirements, generate test cases, and export artifacts.
				</div>
			)}

			{isAuthenticated && pilotAlert && (
				<div className={`billing-banner billing-banner-${pilotAlert.variant}`}>
					<div>
						<strong>{pilotAlert.title}</strong>
						<span>{pilotAlert.message}</span>
					</div>
					<a href={`mailto:${billingContactEmail}`} className="billing-banner-link">Contact {billingContactEmail}</a>
				</div>
			)}

			{isSignInDialogOpen && (
				<div className="auth-dialog-overlay" onClick={handleSignInDialogOverlayClick}>
					<div
						className="auth-dialog"
						role="dialog"
						aria-modal="true"
						aria-labelledby="auth-dialog-title"
						onClick={(event) => event.stopPropagation()}
					>
						<div className="auth-dialog-header">
							<div>
								<h2 id="auth-dialog-title">Choose a sign-in method</h2>
								<p>Select one provider to continue into the workspace.</p>
							</div>
							<button
								type="button"
								className="auth-dialog-close"
								onClick={closeSignInDialog}
								disabled={isAuthenticating}
								aria-label="Close sign-in dialog"
							>
								×
							</button>
						</div>
						<div className="auth-provider-list">
							{visibleFirebaseAuthProviders.map((provider) => (
								<button
									key={provider.id}
									type="button"
									className={`auth-provider-option auth-provider-option--${provider.buttonVariant || provider.id}`}
									onClick={() => handleProviderSignIn(provider.id)}
									disabled={isAuthenticating}
								>
									<span className="auth-provider-option-icon" aria-hidden="true">
										<AuthProviderIcon providerId={provider.id} />
									</span>
									<span className="auth-provider-option-label">
										{provider.buttonText || `Sign in with ${provider.label}`}
									</span>
								</button>
							))}
						</div>
					</div>
				</div>
			)}

			{renderSettingsDialog()}

			<div className="tabs">
				{tabs.map((tab) => (
					<button
						key={tab.id}
						className={`tab ${activeTab === tab.id ? "active" : ""}`}
						onClick={() => setActiveTab(tab.id)}
					>
						<span className="tab-number">{tab.id + 1}</span>
						<span className="tab-label">{tab.label}</span>
					</button>
				))}
			</div>

			<div className="tab-content">
				{activeTab === 0 && (
					<section className="panel">
						<h2 className="panel-title">Upload Requirements</h2>
						<p className="panel-description">
							Choose a source for requirements, extract them into the review loop, and optionally push approved updates back to JIRA or Azure DevOps.
						</p>
						<div className="source-toggle" role="tablist" aria-label="Requirement source selector">
							<button
								type="button"
								className={`source-toggle-btn ${requirementSourceMode === "file" ? "active" : ""}`}
								onClick={() => setRequirementSourceMode("file")}
							>
								<span className="source-toggle-title">File upload</span>
								<span className="source-toggle-copy">Markdown, Word, or Excel requirements</span>
							</button>
							<button
								type="button"
								className={`source-toggle-btn ${requirementSourceMode === "jira" ? "active" : ""}`}
								onClick={() => setRequirementSourceMode("jira")}
							>
								<span className="source-toggle-title">JIRA Cloud</span>
								<span className="source-toggle-copy">Import epics and child issues, then sync updates back</span>
							</button>
							<button
								type="button"
								className={`source-toggle-btn ${requirementSourceMode === "azure_devops" ? "active" : ""}`}
								onClick={() => setRequirementSourceMode("azure_devops")}
							>
								<span className="source-toggle-title">Azure DevOps</span>
								<span className="source-toggle-copy">Import work items and sync managed requirement updates</span>
							</button>
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
										<div className="jira-issue-results">
											{jiraIssueResults.map((issue) => {
												const selected = selectedJiraIssueKey === issue.key;
												return (
													<button
														type="button"
														key={issue.issue_id || issue.key}
														className={`jira-issue-card ${selected ? "selected" : ""}`}
														onClick={() => setSelectedJiraIssueKey(issue.key)}
													>
														<div className="jira-issue-card-header">
															<strong>{issue.key}</strong>
															<span>{issue.issue_type}</span>
														</div>
														<div className="jira-issue-card-title">{issue.summary}</div>
														<div className="jira-issue-card-meta">
															{issue.parent_key ? <span>Parent {issue.parent_key}</span> : null}
															{issue.status ? <span>{issue.status}</span> : null}
														</div>
													</button>
												);
											})}
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
										<div className="jira-issue-results">
											{azureDevOpsWorkItemResults.map((workItem) => {
												const selected = `${selectedAzureDevOpsWorkItemId}` === `${workItem.work_item_id}`;
												return (
													<button
														type="button"
														key={workItem.work_item_id}
														className={`jira-issue-card ${selected ? "selected" : ""}`}
														onClick={() => setSelectedAzureDevOpsWorkItemId(`${workItem.work_item_id}`)}
													>
														<div className="jira-issue-card-header">
															<strong>#{workItem.work_item_id}</strong>
															<span>{workItem.work_item_type}</span>
														</div>
														<div className="jira-issue-card-title">{workItem.title}</div>
														<div className="jira-issue-card-meta">
															{workItem.parent_id ? <span>Parent #{workItem.parent_id}</span> : null}
															{workItem.state ? <span>{workItem.state}</span> : null}
														</div>
													</button>
												);
											})}
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

						<div className="result-section">
							<h3>Raw Text</h3>
							<pre>{rawText || "No content yet"}</pre>
						</div>

						<div className="result-section">
							<h3>Requirement Review Workbench</h3>
							{requirements.length === 0 ? (
								<span className="helper-text">No requirements extracted yet.</span>
							) : (
								<div className="requirement-review-workbench">
									<div className="requirement-review-summary">
										<div>
											<strong>{approvedRequirementCount}/{requirements.length} approved for test generation</strong>
											<p>{reviewPendingRequirementCount} pending review • {rejectedRequirementCount} rejected/out of scope</p>
										</div>
										<div className="requirement-review-bulk-actions">
											<button type="button" className="secondary small" onClick={() => bulkUpdateRequirementReviewStatus("Approved", (requirement) => getRequirementReviewStatus(requirement) !== "Rejected")}>Approve non-rejected</button>
											<button type="button" className="secondary small" onClick={() => bulkUpdateRequirementReviewStatus("Needs Review")}>Mark all needs review</button>
										</div>
									</div>
									{groupRequirementsByContext(requirements).map((group) => (
										<div key={group.id} className="requirement-context-group">
											<div className="requirement-context-header">
												<div>
													<span className="requirement-source-badge subtle">{group.sourceLabel}</span>
													<h4>{group.label}</h4>
												</div>
												<span className="analysis-summary-pill">{group.requirements.length} requirement{group.requirements.length === 1 ? "" : "s"}</span>
											</div>
											<ul className="requirements-list contextual">
												{group.requirements.map((req) => {
													const reviewStatus = getRequirementReviewStatus(req);
													const qualityFlags = normalizeStringArray(req.quality_flags);
													return (
														<li key={req.id || req.text || req.__index}>
															<div className="requirement-review-card-header">
																<div className="requirement-item-copy">
																	<strong>{req.id || `REQ-${req.__index + 1}`}:</strong> {req.text || req.title || ""}
																</div>
																<span className={`requirement-source-badge status-${reviewStatus.toLowerCase().replace(/\s/g, "-")}`}>{reviewStatus}</span>
															</div>
															<div className="requirement-source-meta">
																{req.source_issue_key ? <span className="requirement-source-badge">{getRequirementSourceLabel(req)} {req.source_system === "azure_devops" ? `#${req.source_issue_key}` : req.source_issue_key}</span> : null}
																{req.source_issue_type ? <span className="requirement-source-badge subtle">{req.source_issue_type}</span> : null}
																{req.source_section ? <span className="requirement-source-badge subtle">{req.source_section}</span> : null}
																{req.sync_target_issue_key && req.sync_target_issue_key !== req.source_issue_key ? <span className="requirement-source-badge warning">Sync target {req.source_system === "azure_devops" ? `#${req.sync_target_issue_key}` : req.sync_target_issue_key}</span> : null}
															</div>
															<div className="requirement-review-actions">
																{REQUIREMENT_REVIEW_STATUSES.filter((statusOption) => statusOption !== "Draft").map((statusOption) => (
																	<button
																		type="button"
																		key={`${req.id}-${statusOption}`}
																		className={`requirement-review-action ${reviewStatus === statusOption ? "active" : ""}`}
																		onClick={() => updateRequirementReviewStatus(req.id, statusOption)}
																	>
																		{statusOption}
																	</button>
																))}
															</div>
															<div className="requirement-quality-flags">
																<span>Quality flags:</span>
																{REQUIREMENT_QUALITY_FLAG_OPTIONS.map((flag) => (
																	<button
																		type="button"
																		key={`${req.id}-${flag}`}
																		className={`quality-flag-chip ${qualityFlags.includes(flag) ? "active" : ""}`}
																		onClick={() => toggleRequirementQualityFlag(req.id, flag)}
																	>
																		{flag}
																	</button>
																))}
															</div>
															{req.source_excerpt ? (
																<details className="requirement-evidence">
																	<summary>Source evidence</summary>
																	<p>{req.source_excerpt}</p>
																	{req.source_issue_url ? <a href={req.source_issue_url} target="_blank" rel="noreferrer">Open source ↗</a> : null}
																</details>
															) : req.source_issue_url ? (
																<a className="requirement-source-link" href={req.source_issue_url} target="_blank" rel="noreferrer">Open source ↗</a>
															) : null}
														</li>
													);
												})}
											</ul>
										</div>
									))}
								</div>
							)}
						</div>

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

						{requirementReview && (
							<div className={`review-banner ${requirementReview.approved ? "review-approved" : "review-needs-work"}`}>
								<div className="review-banner-header">
									<strong>{requirementReview.approved ? "Requirements approved" : "Requirements need refinement"}</strong>
									<span>Score {requirementReview.score}/{requirementReview.threshold}</span>
								</div>
								<p>{requirementReview.summary || "The review loop completed without a summary."}</p>
								{!requirementReview.approved && requirementReview.blocking_issues?.length > 0 && (
									<ul className="review-issues">
										{requirementReview.blocking_issues.slice(0, 3).map((issue) => (
											<li key={issue}>{issue}</li>
										))}
									</ul>
								)}
							</div>
						)}

						{requirementCoverageMetrics && (
							<div className="workflow-metrics-panel">
								<h3>Requirement coverage snapshot</h3>
								<div className="workflow-diagnostics-pills">
									<span className="workflow-diagnostics-pill">Total {requirementCoverageMetrics.total_requirements ?? 0}</span>
									<span className="workflow-diagnostics-pill">Unique {requirementCoverageMetrics.unique_requirements ?? 0}</span>
									<span className="workflow-diagnostics-pill">Duplicates {requirementCoverageMetrics.duplicate_requirements ?? 0}</span>
									<span className="workflow-diagnostics-pill">Shall format {requirementCoverageMetrics.shall_format_count ?? 0}</span>
									<span className="workflow-diagnostics-pill">Per doc {requirementCoverageMetrics.requirements_per_document ?? 0}</span>
								</div>
							</div>
						)}

						{renderWorkflowDiagnostics(
							"Requirement workflow diagnostics",
							requirementWorkflowDiagnostics,
							appliedRequirementWorkflowSettings,
							requirementIterationHistory,
						)}

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
					<section className="panel">
						<h2 className="panel-title">Context Inputs</h2>
						<p className="panel-description">
							Add links and references to enrich the test case generation context.
						</p>
						<div className="panel-form two-cols">
							<div className="form-group">
								<label>Application link</label>
								<input
									placeholder="https://your-app"
									value={appLink}
									onChange={(e) => setAppLink(e.target.value)}
								/>
							</div>
							<div className="form-group">
								<label>Prototype link</label>
								<input
									placeholder="https://prototype"
									value={prototypeLink}
									onChange={(e) => setPrototypeLink(e.target.value)}
								/>
							</div>
							<div className="form-group">
								<label>Diagram links</label>
								<input
									placeholder="Link1; Link2"
									value={diagramLinks}
									onChange={(e) => setDiagramLinks(e.target.value)}
								/>
							</div>
							<div className="form-group">
								<label>Image links</label>
								<input
									placeholder="Link1; Link2"
									value={imageLinks}
									onChange={(e) => setImageLinks(e.target.value)}
								/>
							</div>
						</div>
						{hasContextInputs && (
							<div className="panel-form button-row">
								<button
									onClick={analyzeContext}
									disabled={isAnalyzingContext || authActionDisabled}
								>
									{isAnalyzingContext ? "⏳ Analyzing..." : "Analyze Context"}
								</button>
								{enrichedContext && (
									<button
										className="secondary"
										onClick={resetContextAnalysis}
									>
										Clear Analysis
									</button>
								)}
							</div>
						)}
						{enrichedContext?.grounded_context && (
							<div className="result-section">
								<h3>Grounded Context</h3>
								{(enrichedContext.grounded_context.artifact_sources || []).length > 0 && (
									<div className="artifact-sources">
										<h4>Artifact Sources</h4>
										<ul className="artifact-source-list">
											{enrichedContext.grounded_context.artifact_sources.map((source) => (
												<li key={source.id} className="artifact-source-item">
													<label>
														<input
															type="checkbox"
															checked={selectedArtifactSourceIds.includes(source.id)}
															onChange={(e) => {
																setSelectedArtifactSourceIds((prev) =>
																	e.target.checked
																		? [...prev, source.id]
																		: prev.filter((id) => id !== source.id)
																);
															}}
														/>
														<span>{source.url || source.id}</span>
														{source.type && <span className="artifact-type">{source.type}</span>}
													</label>
												</li>
											))}
										</ul>
									</div>
								)}
								<div className="analysis-detail-grid">
									{(enrichedContext.grounded_context.ui_elements || []).length > 0 && (
										<div className="analysis-detail-block">
											<h4>UI Elements</h4>
											<ul className="analysis-detail-list">
												{enrichedContext.grounded_context.ui_elements.slice(0, 6).map((el) => (
													<li key={el.id}>{el.element_type}: {el.label || el.id}</li>
												))}
											</ul>
										</div>
									)}
									{(enrichedContext.grounded_context.workflows || []).length > 0 && (
										<div className="analysis-detail-block">
											<h4>Workflows</h4>
											<ul className="analysis-detail-list">
												{enrichedContext.grounded_context.workflows.slice(0, 4).map((workflow) => (
													<li key={workflow.id}>{workflow.name}: {(workflow.transitions || []).join(", ") || workflow.description}</li>
												))}
											</ul>
										</div>
									)}
								</div>
							</div>
						)}
						<div className="panel-nav">
							<button onClick={goPrev} className="secondary">Back</button>
							<button onClick={goNext}>Next</button>
						</div>
					</section>
				)}

				{activeTab === 2 && (
					<section className="panel">
						<h2 className="panel-title">Template Setup</h2>
						<p className="panel-description">
							Configure the template name and output format for generated test cases.
						</p>
						<div className="panel-form">
							<div className="form-group">
								<label>Template name</label>
								<input
									placeholder="default"
									value={templateName}
									onChange={(e) => setTemplateName(e.target.value)}
								/>
							</div>
							<div className="form-group">
								<label>Template format</label>
								<input
									placeholder="table"
									value={templateFormat}
									onChange={(e) => setTemplateFormat(e.target.value)}
								/>
							</div>
						</div>
						<span className="helper-text">
							Fields used: id, title, description, priority, type, status, preconditions, steps, expected result, test data, estimated time, automation status, component, linked requirement IDs, scenario refs, source refs, and tags.
						</span>
						<div className="panel-nav">
							<button onClick={goPrev} className="secondary">Back</button>
							<button onClick={goNext}>Next</button>
						</div>
					</section>
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
									<span>Score {testCaseReview.score}/{testCaseReview.threshold}</span>
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

									{activeGenerateResultTab === "coverage" && (coveragePlan.length > 0 ? (
							<div className="result-section">
										<details className="collapsible-panel" open>
									<summary className="collapsible-panel-summary">
										<span className="collapsible-panel-copy">
											<span className="collapsible-panel-title">Scenario Coverage Plan</span>
											<span className="collapsible-panel-description">
												Planned scenario intent per requirement, available on demand instead of taking over the page.
											</span>
										</span>
										<span className="collapsible-panel-meta">
											<span className="analysis-summary-pill">{coveragePlan.length} requirements</span>
											<span className="analysis-summary-pill">Scenarios {coveredScenarioTotal}/{plannedScenarioTotal}</span>
											<span className="analysis-summary-pill">Must-have {mustHaveCoveredScenarioTotal}/{mustHaveScenarioTotal}</span>
											{missingScenarioCount > 0 && (
												<span className="analysis-summary-pill collapsible-pill-alert">Missing {missingScenarioCount}</span>
											)}
											<span className="collapsible-panel-icon" aria-hidden="true">⏄</span>
										</span>
									</summary>
									<div className="collapsible-panel-body">
										<div className="coverage-plan-list">
											{coveragePlan.map((plan) => {
												const summary = getRequirementScenarioSummary(plan.requirement_id);
												const missingScenarioTypes = new Set(summary?.missing_scenario_types || []);
												return (
													<div key={plan.requirement_id} className="coverage-plan-card">
														<div className="coverage-plan-header">
															<div>
																<div className="coverage-plan-id">{plan.requirement_id}</div>
																<div className="coverage-plan-text">{plan.requirement_text}</div>
															</div>
															{summary && (
																<span className="coverage-plan-summary">
																	{summary.covered_scenarios}/{summary.planned_scenarios} planned scenarios covered
																</span>
															)}
														</div>
														<div className="coverage-chip-row">
															{plan.scenarios?.map((scenario) => {
																const isMissing = missingScenarioTypes.has(scenario.scenario_type);
																return (
																	<span
																		key={scenario.id}
																		className={`coverage-chip ${scenario.must_have ? "required" : "recommended"} ${isMissing ? "missing" : "covered"}`}
																		title={scenario.objective}
																	>
																		{scenario.scenario_type}
																	</span>
																);
															})}
														</div>
													</div>
												);
											})}
										</div>
									</div>
								</details>
							</div>
									) : (
										<div className="generate-result-empty">
											<h3>Scenario Coverage Plan</h3>
											<p>No scenario coverage plan is available for this run.</p>
										</div>
									))}

									{activeGenerateResultTab === "analysis" && (requirementAnalysis.length > 0 ? (
							<div className="result-section">
										<details className="collapsible-panel" open>
									<summary className="collapsible-panel-summary">
										<span className="collapsible-panel-copy">
											<span className="collapsible-panel-title">Requirement Analysis</span>
											<span className="collapsible-panel-description">
												Rules, constraints, permissions, transitions, and risks extracted before scenario planning.
											</span>
										</span>
										<span className="collapsible-panel-meta">
											<span className="analysis-summary-pill">{requirementAnalysis.length} requirements</span>
											{coverageMetrics && (
												<>
													<span className="analysis-summary-pill">Rules {coverageMetrics.business_rules_covered || 0}/{coverageMetrics.business_rules_total || 0}</span>
													<span className="analysis-summary-pill">Constraints {coverageMetrics.field_constraints_covered || 0}/{coverageMetrics.field_constraints_total || 0}</span>
												</>
											)}
											{requirementAnalysisGapCount > 0 && (
												<span className="analysis-summary-pill collapsible-pill-alert">Gaps {requirementAnalysisGapCount}</span>
											)}
											<span className="collapsible-panel-icon" aria-hidden="true">⏄</span>
										</span>
									</summary>
									<div className="collapsible-panel-body">
										{coverageMetrics && (
											<div className="analysis-overview-row">
												<span className="analysis-summary-pill">Rules {coverageMetrics.business_rules_covered || 0}/{coverageMetrics.business_rules_total || 0}</span>
												<span className="analysis-summary-pill">Constraints {coverageMetrics.field_constraints_covered || 0}/{coverageMetrics.field_constraints_total || 0}</span>
												<span className="analysis-summary-pill">Permissions {coverageMetrics.role_permissions_covered || 0}/{coverageMetrics.role_permissions_total || 0}</span>
												<span className="analysis-summary-pill">Transitions {coverageMetrics.state_transitions_covered || 0}/{coverageMetrics.state_transitions_total || 0}</span>
												<span className="analysis-summary-pill">Risks {coverageMetrics.risk_signals_covered || 0}/{coverageMetrics.risk_signals_total || 0}</span>
											</div>
										)}
										<div className="analysis-card-list">
											{requirementAnalysis.map((analysis) => {
												const summary = getRequirementAnalysisSummary(analysis.requirement_id);
												const gaps = getRequirementAnalysisGaps(analysis.requirement_id);
												const hasGaps = Object.values(gaps).some((items) => items.length > 0);
												return (
													<div key={analysis.requirement_id} className="analysis-card">
														<div className="analysis-card-header">
															<div>
																<div className="coverage-plan-id">{analysis.requirement_id}</div>
																<div className="coverage-plan-text">{analysis.requirement_text}</div>
															</div>
															{summary && (
																<span className="coverage-plan-summary">
																	{summary.business_rules_covered}/{summary.business_rules_total} rules • {summary.field_constraints_covered}/{summary.field_constraints_total} constraints
																</span>
															)}
														</div>
														<div className="analysis-summary-row">
															<span className="analysis-summary-pill">Rules {analysis.business_rules?.length || 0}</span>
															<span className="analysis-summary-pill">Constraints {analysis.field_constraints?.length || 0}</span>
															<span className="analysis-summary-pill">Permissions {analysis.role_permissions?.length || 0}</span>
															<span className="analysis-summary-pill">Transitions {analysis.state_transitions?.length || 0}</span>
															<span className="analysis-summary-pill">Risks {analysis.risk_signals?.length || 0}</span>
														</div>
														{analysis.suggested_scenarios?.length > 0 && (
															<div className="analysis-chip-row">
																{analysis.suggested_scenarios.map((scenario) => (
																	<span key={`${analysis.requirement_id}-${scenario}`} className="analysis-chip">
																		{scenario}
																	</span>
																))}
															</div>
														)}
														<div className="analysis-detail-grid">
															<div className="analysis-detail-block">
																<h4>Business rules</h4>
																<ul className="analysis-detail-list">
																	{(analysis.business_rules || []).slice(0, 2).map((rule) => (
																		<li key={rule.id}>{rule.title}</li>
																	))}
																</ul>
															</div>
															<div className="analysis-detail-block">
																<h4>Constraints</h4>
																<ul className="analysis-detail-list">
																	{(analysis.field_constraints || []).slice(0, 2).map((constraint) => (
																		<li key={constraint.id}>{constraint.field_name}: {constraint.description}</li>
																	))}
																</ul>
															</div>
															<div className="analysis-detail-block">
																<h4>Permissions</h4>
																<ul className="analysis-detail-list">
																	{(analysis.role_permissions || []).slice(0, 2).map((permission) => (
																		<li key={permission.id}>{permission.role}: {permission.action}</li>
																	))}
																</ul>
															</div>
															<div className="analysis-detail-block">
																<h4>Transitions</h4>
																<ul className="analysis-detail-list">
																	{(analysis.state_transitions || []).slice(0, 2).map((transition) => (
																		<li key={transition.id}>{transition.from_state} → {transition.to_state}</li>
																	))}
																</ul>
															</div>
															<div className="analysis-detail-block">
																<h4>Risks</h4>
																<ul className="analysis-detail-list">
																	{(analysis.risk_signals || []).slice(0, 2).map((risk) => (
																		<li key={risk.id}>{risk.severity}: {risk.title}</li>
																	))}
																</ul>
															</div>
														</div>
														{hasGaps && (
															<div className="analysis-gap-block">
																<strong>Coverage gaps</strong>
																<ul className="analysis-gap-list">
																	{gaps.highRisks.slice(0, 2).map((item) => <li key={item}>{item}</li>)}
																	{gaps.rules.slice(0, 2).map((item) => <li key={item}>{item}</li>)}
																	{gaps.constraints.slice(0, 2).map((item) => <li key={item}>{item}</li>)}
																	{gaps.permissions.slice(0, 2).map((item) => <li key={item}>{item}</li>)}
																	{gaps.transitions.slice(0, 2).map((item) => <li key={item}>{item}</li>)}
																</ul>
															</div>
														)}
													</div>
												);
											})}
										</div>
									</div>
								</details>
							</div>
									) : (
										<div className="generate-result-empty">
											<h3>Requirement Analysis</h3>
											<p>No requirement analysis is available for this run.</p>
										</div>
									))}

									{activeGenerateResultTab === "traceability" && (approvedRequirements.length > 0 ? (
							<div className="result-section">
								<h3>Traceability Matrix</h3>
								<div className="workflow-diagnostics-pills">
									<span className="workflow-diagnostics-pill">Approved requirements covered {tracedRequirementCount}/{approvedRequirements.length}</span>
									<span className="workflow-diagnostics-pill">Cases with traceability {coverageMetrics?.cases_with_traceability ?? 0}/{testCases.length}</span>
									<span className="workflow-diagnostics-pill">Scenario coverage {coverageMetrics?.covered_planned_scenarios ?? 0}/{coverageMetrics?.planned_scenarios_total ?? 0}</span>
								</div>
								<div className="traceability-table-wrapper">
									<table className="traceability-table">
										<thead>
											<tr>
												<th>Requirement</th>
												<th>Story / source path</th>
												<th>Linked test cases</th>
												<th>Scenario coverage</th>
												<th>Status</th>
											</tr>
										</thead>
										<tbody>
											{requirementTraceabilityRows.map(({ requirement, linkedTestCases, scenarioSummary, linkedScenarioTypes }) => {
												const isCovered = linkedTestCases.length > 0;
												return (
													<tr key={requirement.id} className={isCovered ? "covered" : "missing"}>
														<td>
															<strong>{requirement.id}</strong>
															<span>{requirement.text}</span>
														</td>
														<td>{getRequirementContextPath(requirement)}</td>
														<td>
															{linkedTestCases.length ? linkedTestCases.map((testCase) => (
																<span key={testCase.id} className="tag traceability-case-tag">{testCase.id}</span>
															)) : <span className="traceability-missing-text">No linked tests</span>}
														</td>
														<td>
															{scenarioSummary ? `${scenarioSummary.covered_scenarios}/${scenarioSummary.planned_scenarios}` : "—"}
															{linkedScenarioTypes.length > 0 && (
																<div className="traceability-scenario-tags">
																	{linkedScenarioTypes.slice(0, 4).map((scenario) => <span key={`${requirement.id}-${scenario}`}>{scenario}</span>)}
																</div>
															)}
														</td>
														<td><span className={`traceability-status ${isCovered ? "covered" : "missing"}`}>{isCovered ? "Covered" : "Gap"}</span></td>
													</tr>
												);
											})}
										</tbody>
									</table>
								</div>
							</div>
									) : (
										<div className="generate-result-empty">
											<h3>Traceability Matrix</h3>
											<p>No approved requirements are available to trace for this run.</p>
										</div>
									))}

									{activeGenerateResultTab === "test-cases" && (
										<>
											<div className="result-section generate-result-section">
							<h3>Generated Test Cases</h3>
							{testCases.length === 0 ? (
								<span className="helper-text">No test cases generated yet.</span>
							) : templateFormat === "table" ? (
								<div className="test-cases-table-wrapper">
									<table className="test-cases-table">
										<thead>
											<tr>
												<th className="col-id">ID</th>
												<th className="col-title">Title</th>
												<th className="col-priority">Priority</th>
												<th className="col-type">Type</th>
												<th className="col-status">Status</th>
												<th className="col-preconditions">Preconditions</th>
												<th className="col-steps">Steps</th>
												<th className="col-expected">Expected Result</th>
												<th className="col-testdata">Test Data</th>
												<th className="col-time">Est. Time</th>
												<th className="col-automation">Automation</th>
												<th className="col-component">Component</th>
												<th className="col-tags">Linked Reqs</th>
												<th className="col-tags">Tags</th>
											</tr>
										</thead>
										<tbody>
											{testCases.map((tc) => (
												<React.Fragment key={tc.id}>
													<tr className={expandedRows[tc.id] ? "expanded" : ""} onClick={() => toggleRowExpansion(tc.id)}>
														<td className="tc-id">{tc.id}</td>
														<td className="tc-title">
															<div className="title-cell">
																<span className="expand-icon">{expandedRows[tc.id] ? "▼" : "▶"}</span>
																{tc.title}
															</div>
															{tc.description && <div className="tc-description">{tc.description}</div>}
														</td>
														<td className="tc-priority">
															<span className={`priority-badge ${getPriorityClass(tc.priority)}`}>{tc.priority || "Medium"}</span>
														</td>
														<td className="tc-type">{tc.type || "Functional"}</td>
														<td className="tc-status">
															<span className={`status-badge ${getStatusClass(tc.status)}`}>{tc.status || "Draft"}</span>
														</td>
														<td className="tc-preconditions">{tc.preconditions || "-"}</td>
														<td className="tc-steps">
															<ol>
																{tc.steps?.slice(0, expandedRows[tc.id] ? undefined : 2).map((step, index) => (
																	<li key={`${tc.id}-step-${step.step || index + 1}`}>
																		<strong>{step.action}</strong>
																		<span className="step-expected">→ {step.expected}</span>
																		{step.test_data && <span className="step-data">📋 {step.test_data}</span>}
																	</li>
																))}
																{!expandedRows[tc.id] && tc.steps?.length > 2 && (
																	<li className="more-steps">+{tc.steps.length - 2} more steps...</li>
																)}
															</ol>
														</td>
														<td className="tc-expected-result">{tc.expected_result || "-"}</td>
														<td className="tc-testdata">{tc.test_data || "-"}</td>
														<td className="tc-time">{tc.estimated_time || "-"}</td>
														<td className="tc-automation">
															<span className={`automation-badge ${tc.automation_status?.replace(/\s/g, "-").toLowerCase() || "manual"}`}>
																{tc.automation_status || "Manual"}
															</span>
														</td>
														<td className="tc-component">{tc.component || "-"}</td>
														<td className="tc-tags">
															{getTestCaseLinkedRequirementIds(tc).map((requirementId) => (
																<span key={`${tc.id}-${requirementId}`} className="tag traceability-case-tag">{requirementId}</span>
															))}
														</td>
														<td className="tc-tags">
															{tc.tags?.map((tag) => (
																<span key={tag} className="tag">{tag}</span>
															))}
														</td>
													</tr>
												</React.Fragment>
											))}
										</tbody>
									</table>
								</div>
							) : (
								<div className="test-cases-grid">
									{testCases.map((tc) => (
										<div key={tc.id} className="case-card">
											<div className="case-header">
												<span className="case-id">{tc.id}</span>
												<span className="case-title">{tc.title}</span>
												<span className={`priority-badge ${getPriorityClass(tc.priority)}`}>{tc.priority}</span>
											</div>
											{tc.description && <div className="case-description">{tc.description}</div>}
											<div className="case-meta">
												<span className="meta-item"><strong>Type:</strong> {tc.type}</span>
												<span className={`status-badge ${getStatusClass(tc.status)}`}>{tc.status}</span>
												<span className="meta-item"><strong>Est:</strong> {tc.estimated_time}</span>
											</div>
											{getTestCaseLinkedRequirementIds(tc).length > 0 && (
												<div className="case-tags traceability-links">
													{getTestCaseLinkedRequirementIds(tc).map((requirementId) => (
														<span key={`${tc.id}-linked-${requirementId}`} className="tag traceability-case-tag">{requirementId}</span>
													))}
												</div>
											)}
											{tc.preconditions && (
												<div className="case-preconditions">{tc.preconditions}</div>
											)}
											<div className="case-steps">
												<strong>Steps</strong>
												<ol>
													{tc.steps?.map((step, index) => (
														<li key={`${tc.id}-card-step-${step.step || index + 1}`}>
															<span className="step-action">{step.step || index + 1}. {step.action}</span>
															<span className="step-expected">→ {step.expected}</span>
															{step.test_data && <span className="step-data">📋 {step.test_data}</span>}
														</li>
													))}
												</ol>
											</div>
											{tc.expected_result && (
												<div className="case-expected"><strong>Expected Result:</strong> {tc.expected_result}</div>
											)}
											{tc.tags && tc.tags.length > 0 && (
												<div className="case-tags">
													{tc.tags.map((tag) => (
														<span key={tag} className="tag">{tag}</span>
													))}
												</div>
											)}
										</div>
									))}
								</div>
							)}
						</div>

						{testCases.length > 0 && (
							<div className="feedback-section">
								<h3>Human Feedback</h3>
								<p className="feedback-description">
									Provide feedback on the generated test cases. The AI will refine them based on your input.
								</p>
								<textarea
									className="feedback-textarea"
									placeholder="Enter your feedback here... e.g., 'Add more negative test cases for upload feature', 'TC-003 needs more detailed steps', 'Include security test cases', etc."
									value={feedback}
									onChange={(e) => setFeedback(e.target.value)}
									rows={4}
								/>
								<div className="feedback-actions">
									<button 
										onClick={() => generateTestCases(true)} 
										disabled={!feedback.trim() || isGenerating || testCaseActionDisabled}
										className="feedback-button"
									>
										{isGenerating ? "⏳ Updating Test Cases..." : "🔄 Implement Changes"}
									</button>
								</div>
							</div>
						)}
										</>
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
					<section className="panel">
						<h2 className="panel-title">Export Test Cases</h2>
						<p className="panel-description">
							Download your generated test cases as CSV, Excel, or JSON.
						</p>
						<div className="export-section">
							<h3 className="section-subtitle">📥 Quick Export</h3>
							<p className="helper-text">Download test cases directly to your computer.</p>
							<div className="export-buttons">
								<button 
									className="export-btn csv" 
									onClick={() => exportToFormat("csv")} 
									disabled={testCases.length === 0 || isExporting || authActionDisabled}
								>
									<span className="export-icon">📄</span>
									<span className="export-label">CSV</span>
									<span className="export-desc">Excel compatible</span>
								</button>
								<button 
									className="export-btn excel" 
									onClick={() => exportToFormat("excel")} 
									disabled={testCases.length === 0 || isExporting || authActionDisabled}
								>
									<span className="export-icon">📊</span>
									<span className="export-label">Excel</span>
									<span className="export-desc">Formatted .xlsx</span>
								</button>
								<button 
									className="export-btn json" 
									onClick={() => exportToFormat("json")} 
									disabled={testCases.length === 0 || isExporting || authActionDisabled}
								>
									<span className="export-icon">🧾</span>
									<span className="export-label">JSON</span>
									<span className="export-desc">API/Import ready</span>
								</button>
							</div>
						</div>
						<div className="panel-nav">
							<button onClick={goPrev} className="secondary">Back</button>
						</div>
					</section>
				)}
			</div>
		</div>
	);
}
