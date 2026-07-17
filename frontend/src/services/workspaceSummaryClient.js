import { parseApiError } from "./apiClient";

export const WORKSPACE_SUMMARY_LIMITS = Object.freeze({
	projects: 20,
	workItems: 50,
	runs: 20,
	reports: 20,
});

const normalizeList = (value) => (Array.isArray(value) ? value.filter((item) => item && typeof item === "object") : []);

export const EMPTY_WORKSPACE_SUMMARY = Object.freeze({
	continue_working: null,
	projects: Object.freeze([]),
	work_items: Object.freeze([]),
	recent_runs: Object.freeze([]),
	recent_reports: Object.freeze([]),
	generated_at: null,
});

export function buildWorkspaceSummaryPath(limits = WORKSPACE_SUMMARY_LIMITS) {
	const params = new URLSearchParams({
		include_archived: "false",
		projects_limit: `${limits.projects}`,
		work_items_limit: `${limits.workItems}`,
		runs_limit: `${limits.runs}`,
		reports_limit: `${limits.reports}`,
	});
	return `/workspace/summary?${params.toString()}`;
}

export function normalizeWorkspaceSummary(payload) {
	const data = payload && typeof payload === "object" ? payload : {};
	return {
		continue_working: data.continue_working && typeof data.continue_working === "object" ? data.continue_working : null,
		projects: normalizeList(data.projects),
		work_items: normalizeList(data.work_items),
		recent_runs: normalizeList(data.recent_runs),
		recent_reports: normalizeList(data.recent_reports),
		generated_at: typeof data.generated_at === "string" ? data.generated_at : null,
	};
}

export async function fetchWorkspaceSummary(request, { signal, limits = WORKSPACE_SUMMARY_LIMITS } = {}) {
	if (typeof request !== "function") {
		throw new TypeError("An authenticated request function is required to load the workspace summary.");
	}

	const response = await request(buildWorkspaceSummaryPath(limits), {
		method: "GET",
		signal,
	});
	if (!response?.ok) {
		const message = response
			? await parseApiError(response, "Workspace summary is unavailable. Please try again.")
			: "Workspace summary is unavailable. Please try again.";
		throw new Error(message);
	}

	return normalizeWorkspaceSummary(await response.json());
}
