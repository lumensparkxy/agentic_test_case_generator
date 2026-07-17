import { PROJECT_DESTINATIONS, buildProjectPath, getDestinationForStage, resolveOrchestratorDestination } from "../../app/workflowRoutes";

const STATUS_LABELS = Object.freeze({
	not_started: "Not started",
	ready: "Ready",
	blocked: "Blocked",
	completed: "Complete",
	stale: "Needs refresh",
	failed: "Failed",
	attention_required: "Needs attention",
	active: "Active",
	archived: "Archived",
	approved: "Approved",
	draft: "Draft",
	passed: "Passed",
	partial: "Completed with issues",
});

const ACTION_LABELS = Object.freeze({
	refine: "Refine requirements",
	approve: "Review approvals",
	generate: "Generate test cases",
	analyze_impact: "Analyze changes",
	apply_update: "Apply accepted updates",
	full_regenerate: "Regenerate test cases",
	automate: "Preview automation",
	execute: "Run automation",
	review: "Review test cases",
	report: "Open reports",
});

const GROUP_LABELS = Object.freeze({
	review: "Needs review",
	attention: "Needs attention",
	ready: "Ready next",
});
const GROUP_ORDER = Object.freeze(["review", "attention", "ready"]);

const ATTENTION_STATUSES = new Set(["blocked", "stale", "failed", "attention_required"]);

export const normalizeWorkspaceList = (value) => (Array.isArray(value) ? value : []);

export const formatWorkspaceLabel = (value, fallback = "") => {
	const normalized = `${value ?? ""}`.trim();
	if (!normalized) return fallback;
	return normalized
		.split(/[_\s-]+/)
		.filter(Boolean)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(" ");
};

export const formatWorkspaceStatus = (status) => STATUS_LABELS[status] || formatWorkspaceLabel(status, "Unknown");

export const formatWorkspaceDate = (value) => {
	const date = new Date(value || "");
	if (Number.isNaN(date.getTime())) return "";
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "numeric",
		year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
		hour: "numeric",
		minute: "2-digit",
	}).format(date);
};

export const getWorkspaceStatusTone = (status) => {
	if (["failed", "attention_required"].includes(status)) return "danger";
	if (["blocked", "stale", "draft", "partial"].includes(status)) return "warning";
	if (["completed", "approved", "passed"].includes(status)) return "success";
	if (["ready", "active"].includes(status)) return "info";
	return "neutral";
};

export const getWorkItemDestination = (item) =>
	resolveOrchestratorDestination(item) || getDestinationForStage(item?.stage) || PROJECT_DESTINATIONS.OVERVIEW;

export const getProjectDestination = (project) => getDestinationForStage(project?.current_stage) || PROJECT_DESTINATIONS.OVERVIEW;

export const getWorkItemTitle = (item) => {
	if (item?.action && ACTION_LABELS[item.action]) return ACTION_LABELS[item.action];
	if (item?.kind === "review") return `Review ${formatWorkspaceLabel(item.stage, "project work").toLowerCase()}`;
	return `${formatWorkspaceLabel(item?.stage, "Project work")} needs attention`;
};

const getWorkItemGroupId = (item) => {
	if (item?.kind === "review" || ["approve", "review"].includes(item?.action)) return "review";
	if (ATTENTION_STATUSES.has(item?.status)) return "attention";
	return "ready";
};

export function groupWorkspaceItems(items) {
	const grouped = new Map(GROUP_ORDER.map((id) => [id, { id, label: GROUP_LABELS[id], items: [] }]));
	for (const item of normalizeWorkspaceList(items)) {
		const id = getWorkItemGroupId(item);
		grouped.get(id).items.push(item);
	}
	return GROUP_ORDER.map((id) => grouped.get(id)).filter((group) => group.items.length > 0);
}

export const matchesWorkspaceQuery = (query, ...values) => {
	const needle = `${query || ""}`.trim().toLocaleLowerCase();
	if (!needle) return true;
	return values.some((value) => `${value ?? ""}`.toLocaleLowerCase().includes(needle));
};

export function getProjectPath(projectId, destination = PROJECT_DESTINATIONS.OVERVIEW) {
	return buildProjectPath(projectId, destination);
}
