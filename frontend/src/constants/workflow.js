export const STORAGE_AUTH_TOKEN = "tcg.auth.token";
export const STORAGE_AUTH_USER = "tcg.auth.user";
export const STORAGE_CURRENT_PROJECT_ID = "tcg.current.project_id";
export const STORAGE_WORKFLOW_NAV_COLLAPSED = "tcg.shell.workflowNavCollapsed";
export const AUTH_REQUIRED_MESSAGE = "Sign in to continue.";

export const EMPTY_WORKFLOW_SETTINGS = {
	approval_threshold: "",
	max_iterations: "",
	timeout_seconds: "",
	stall_iteration_limit: "",
	retry_attempts: "",
};

export const WORKFLOW_SETTING_FIELDS = [
	{ key: "approval_threshold", label: "Approval threshold", min: 0, max: 100 },
	{ key: "max_iterations", label: "Max iterations", min: 1, max: 20 },
	{ key: "timeout_seconds", label: "Timeout (seconds)", min: 1, max: 900 },
	{ key: "stall_iteration_limit", label: "Stall limit", min: 1, max: 20 },
	{ key: "retry_attempts", label: "Retry attempts", min: 0, max: 5 },
];

export const USAGE_STATUS_ITEMS = [
	{ key: "requirementsGeneratedCount", label: "Req +" },
	{ key: "requirementsModifiedCount", label: "Req Δ" },
	{ key: "testCasesGeneratedCount", label: "TC +" },
	{ key: "testCasesModifiedCount", label: "TC Δ" },
];

export const PILOT_WARNING_THRESHOLD = 20;

export const EMPTY_JIRA_CONNECTION_STATUS = {
	connected: false,
	connection: null,
};

export const EMPTY_JIRA_CONNECTION_FORM = {
	baseUrl: "",
	email: "",
	apiToken: "",
};

export const EMPTY_AZURE_DEVOPS_CONNECTION_STATUS = {
	connected: false,
	connection: null,
};

export const EMPTY_AZURE_DEVOPS_CONNECTION_FORM = {
	organizationUrl: "",
	accountEmail: "",
	personalAccessToken: "",
};

export const DEFAULT_JIRA_ISSUE_TYPE_OPTIONS = ["Epic", "Story", "Task", "Bug"];
export const DEFAULT_AZURE_DEVOPS_WORK_ITEM_TYPE_OPTIONS = ["Epic", "Feature", "User Story", "Task", "Bug"];
export const DEFAULT_SYNC_SECTION_TITLE = "Agentic Requirements";
export const DEFAULT_JIRA_SYNC_SECTION_TITLE = DEFAULT_SYNC_SECTION_TITLE;
export const DEFAULT_AZURE_DEVOPS_SYNC_SECTION_TITLE = DEFAULT_SYNC_SECTION_TITLE;

export const REQUIREMENT_SOURCE_OPTIONS = [
	{ value: "file", label: "File upload" },
	{ value: "jira", label: "JIRA Cloud" },
	{ value: "azure_devops", label: "Azure DevOps" },
];

export const TEMPLATE_FORMAT_OPTIONS = [
	{ value: "table", label: "Table", description: "Best for review, QA handoff, and exports" },
	{ value: "cards", label: "Cards", description: "Best for compact narrative review" },
];

export const REQUIREMENT_REVIEW_STATUSES = ["Draft", "Needs Review", "Approved", "Rejected"];

export const REQUIREMENT_QUALITY_FLAG_OPTIONS = [
	"Ambiguous",
	"Duplicate",
	"Untestable",
	"Missing actor",
	"Missing expected result",
	"Needs split",
	"Needs merge",
	"Out of scope",
];

export const JIRA_SOURCE_FIELDS = [
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
