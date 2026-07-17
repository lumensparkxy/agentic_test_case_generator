export const GLOBAL_DESTINATIONS = Object.freeze({
	HOME: "home",
	PROJECTS: "projects",
	REVIEWS: "reviews",
	RUNS: "runs",
	REPORTS: "reports",
});

export const PROJECT_DESTINATIONS = Object.freeze({
	OVERVIEW: "overview",
	REQUIREMENTS: "requirements",
	CONTEXT: "context",
	USE_CASES: "use-cases",
	TEST_CASES: "test-cases",
	AUTOMATION: "automation",
	REPORTS: "reports",
});

const GLOBAL_PATH_BY_DESTINATION = Object.freeze({
	[GLOBAL_DESTINATIONS.HOME]: "/",
	[GLOBAL_DESTINATIONS.PROJECTS]: "/projects",
	[GLOBAL_DESTINATIONS.REVIEWS]: "/reviews",
	[GLOBAL_DESTINATIONS.RUNS]: "/runs",
	[GLOBAL_DESTINATIONS.REPORTS]: "/reports",
});

const PROJECT_SEGMENT_BY_DESTINATION = Object.freeze({
	[PROJECT_DESTINATIONS.OVERVIEW]: "",
	[PROJECT_DESTINATIONS.REQUIREMENTS]: "requirements",
	[PROJECT_DESTINATIONS.CONTEXT]: "context",
	[PROJECT_DESTINATIONS.USE_CASES]: "use-cases",
	[PROJECT_DESTINATIONS.TEST_CASES]: "test-cases",
	[PROJECT_DESTINATIONS.AUTOMATION]: "automation",
	[PROJECT_DESTINATIONS.REPORTS]: "reports",
});

export const GLOBAL_NAV_ITEMS = Object.freeze(
	[
		{ id: GLOBAL_DESTINATIONS.HOME, label: "Home", path: GLOBAL_PATH_BY_DESTINATION[GLOBAL_DESTINATIONS.HOME] },
		{
			id: GLOBAL_DESTINATIONS.PROJECTS,
			label: "Projects",
			path: GLOBAL_PATH_BY_DESTINATION[GLOBAL_DESTINATIONS.PROJECTS],
		},
		{
			id: GLOBAL_DESTINATIONS.REVIEWS,
			label: "Reviews",
			path: GLOBAL_PATH_BY_DESTINATION[GLOBAL_DESTINATIONS.REVIEWS],
		},
		{
			id: GLOBAL_DESTINATIONS.RUNS,
			label: "Runs",
			path: GLOBAL_PATH_BY_DESTINATION[GLOBAL_DESTINATIONS.RUNS],
		},
		{
			id: GLOBAL_DESTINATIONS.REPORTS,
			label: "Reports",
			path: GLOBAL_PATH_BY_DESTINATION[GLOBAL_DESTINATIONS.REPORTS],
		},
	].map(Object.freeze)
);

export const PROJECT_NAV_ITEMS = Object.freeze(
	[
		{
			id: PROJECT_DESTINATIONS.OVERVIEW,
			label: "Overview",
			title: "Project status and recovery",
			legacyTabIds: [7],
		},
		{
			id: PROJECT_DESTINATIONS.REQUIREMENTS,
			label: "Requirements",
			title: "Upload and review requirements",
			legacyTabIds: [0],
		},
		{
			id: PROJECT_DESTINATIONS.CONTEXT,
			label: "Context",
			title: "Add grounded product context",
			legacyTabIds: [1],
		},
		{
			id: PROJECT_DESTINATIONS.USE_CASES,
			label: "Use Cases",
			title: "Review generated use cases",
			legacyTabIds: [6],
		},
		{
			id: PROJECT_DESTINATIONS.TEST_CASES,
			label: "Test Cases",
			title: "Set up, generate, and review test cases",
			legacyTabIds: [2, 3],
		},
		{
			id: PROJECT_DESTINATIONS.AUTOMATION,
			label: "Automation",
			title: "Preview and run automation",
			legacyTabIds: [4],
		},
		{
			id: PROJECT_DESTINATIONS.REPORTS,
			label: "Reports",
			title: "Export test evidence",
			legacyTabIds: [5],
		},
	].map((item) => Object.freeze({ ...item, legacyTabIds: Object.freeze(item.legacyTabIds) }))
);

const LEGACY_TAB_DESTINATION = Object.freeze(
	PROJECT_NAV_ITEMS.reduce((mapping, item) => {
		for (const tabId of item.legacyTabIds) {
			mapping[tabId] = item.id;
		}
		return mapping;
	}, {})
);

const CANONICAL_LEGACY_TAB = Object.freeze({
	[PROJECT_DESTINATIONS.OVERVIEW]: 7,
	[PROJECT_DESTINATIONS.REQUIREMENTS]: 0,
	[PROJECT_DESTINATIONS.CONTEXT]: 1,
	[PROJECT_DESTINATIONS.USE_CASES]: 6,
	[PROJECT_DESTINATIONS.TEST_CASES]: 3,
	[PROJECT_DESTINATIONS.AUTOMATION]: 4,
	[PROJECT_DESTINATIONS.REPORTS]: 5,
});

const STAGE_DESTINATION = Object.freeze({
	requirements: PROJECT_DESTINATIONS.REQUIREMENTS,
	context: PROJECT_DESTINATIONS.CONTEXT,
	use_cases: PROJECT_DESTINATIONS.USE_CASES,
	impact_analysis: PROJECT_DESTINATIONS.TEST_CASES,
	test_cases: PROJECT_DESTINATIONS.TEST_CASES,
	review: PROJECT_DESTINATIONS.TEST_CASES,
	automation: PROJECT_DESTINATIONS.AUTOMATION,
	execution: PROJECT_DESTINATIONS.AUTOMATION,
	reports: PROJECT_DESTINATIONS.REPORTS,
});

const ACTION_DESTINATION = Object.freeze({
	generate: PROJECT_DESTINATIONS.TEST_CASES,
	analyze_impact: PROJECT_DESTINATIONS.TEST_CASES,
	apply_update: PROJECT_DESTINATIONS.TEST_CASES,
	full_regenerate: PROJECT_DESTINATIONS.TEST_CASES,
	automate: PROJECT_DESTINATIONS.AUTOMATION,
	execute: PROJECT_DESTINATIONS.AUTOMATION,
	review: PROJECT_DESTINATIONS.TEST_CASES,
	report: PROJECT_DESTINATIONS.REPORTS,
});

const normalizeKey = (value) => `${value ?? ""}`.trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");

const normalizePathname = (pathname) => {
	let value = `${pathname || "/"}`.trim();
	value = value.split("#", 1)[0].split("?", 1)[0] || "/";
	if (!value.startsWith("/")) {
		value = `/${value}`;
	}
	if (value.length > 1) {
		value = value.replace(/\/+$/, "");
	}
	return value || "/";
};

const decodePathSegment = (segment) => {
	try {
		return decodeURIComponent(segment);
	} catch {
		return null;
	}
};

export function buildGlobalPath(destination) {
	const path = GLOBAL_PATH_BY_DESTINATION[destination];
	if (!path) {
		throw new RangeError(`Unknown global destination: ${destination}`);
	}
	return path;
}

export function buildProjectPath(projectId, destination = PROJECT_DESTINATIONS.OVERVIEW) {
	const normalizedProjectId = `${projectId ?? ""}`.trim();
	if (!normalizedProjectId) {
		throw new TypeError("A project ID is required to build a project path.");
	}
	if (!Object.prototype.hasOwnProperty.call(PROJECT_SEGMENT_BY_DESTINATION, destination)) {
		throw new RangeError(`Unknown project destination: ${destination}`);
	}

	const projectRoot = `/projects/${encodeURIComponent(normalizedProjectId)}`;
	const segment = PROJECT_SEGMENT_BY_DESTINATION[destination];
	return segment ? `${projectRoot}/${segment}` : projectRoot;
}

export function parseWorkflowRoute(pathname) {
	const normalizedPathname = normalizePathname(pathname);
	const globalDestination = Object.entries(GLOBAL_PATH_BY_DESTINATION).find(([, path]) => path === normalizedPathname)?.[0];
	if (globalDestination) {
		return {
			kind: "global",
			destination: globalDestination,
			projectId: null,
			pathname: normalizedPathname,
		};
	}

	const segments = normalizedPathname.slice(1).split("/");
	if (segments[0] !== "projects" || segments.length < 2 || !segments[1]) {
		return { kind: "not-found", destination: null, projectId: null, pathname: normalizedPathname };
	}

	const projectId = decodePathSegment(segments[1]);
	if (!projectId) {
		return { kind: "not-found", destination: null, projectId: null, pathname: normalizedPathname };
	}

	if (segments.length === 2) {
		return {
			kind: "project",
			destination: PROJECT_DESTINATIONS.OVERVIEW,
			projectId,
			pathname: normalizedPathname,
		};
	}

	const destination = Object.entries(PROJECT_SEGMENT_BY_DESTINATION).find(
		([candidate, segment]) => candidate !== PROJECT_DESTINATIONS.OVERVIEW && segment === segments[2]
	)?.[0];
	if (segments.length === 3 && destination) {
		return { kind: "project", destination, projectId, pathname: normalizedPathname };
	}

	return {
		kind: "not-found",
		destination: null,
		projectId,
		pathname: normalizedPathname,
		reason: "invalid-project-destination",
	};
}

export function getDestinationForLegacyTab(tabId) {
	const normalizedTabId = Number(tabId);
	return Number.isInteger(normalizedTabId) ? LEGACY_TAB_DESTINATION[normalizedTabId] || null : null;
}

export function getLegacyTabForDestination(destination) {
	return Object.prototype.hasOwnProperty.call(CANONICAL_LEGACY_TAB, destination) ? CANONICAL_LEGACY_TAB[destination] : null;
}

export function getDestinationForStage(stage) {
	return STAGE_DESTINATION[normalizeKey(stage)] || null;
}

export function resolveOrchestratorDestination(recommendation) {
	if (!recommendation || typeof recommendation !== "object") {
		return null;
	}

	const action = normalizeKey(recommendation.action);
	if (action === "approve" || action === "refine") {
		return getDestinationForStage(recommendation.stage) || null;
	}
	return ACTION_DESTINATION[action] || getDestinationForStage(recommendation.stage) || null;
}
