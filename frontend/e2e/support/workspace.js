export const STORAGE_CURRENT_PROJECT_ID = "tcg.current.project_id";
export const TEST_USER_ID = "playwright-e2e-user";
export const WORKSPACE_GENERATED_AT = "2026-07-17T12:00:00Z";

const DEFAULT_TIMESTAMP = "2026-07-17T10:00:00Z";

export function workspaceProjectFixture(overrides = {}) {
	return {
		project_id: "workspace-project",
		name: "Workspace Project",
		project_revision: 3,
		project_status: "active",
		current_stage: "requirements",
		current_status: "ready",
		current_snapshot_id: null,
		completed_stage_count: 0,
		total_stage_count: 9,
		reason: "Requirements are ready for review.",
		updated_at: DEFAULT_TIMESTAMP,
		...overrides,
	};
}

export function workspaceWorkItemFixture(overrides = {}) {
	return {
		work_item_id: "work_workspace_project_requirements",
		kind: "action",
		project_id: "workspace-project",
		project_name: "Workspace Project",
		project_revision: 3,
		stage: "requirements",
		status: "ready",
		action: "refine",
		enabled: true,
		primary: true,
		count: 4,
		reason: "Requirements are ready for review.",
		current_snapshot_id: null,
		updated_at: DEFAULT_TIMESTAMP,
		...overrides,
	};
}

export function workspaceRunFixture(overrides = {}) {
	return {
		run_record_id: "run-record-workspace-project",
		run_id: "run-workspace-project",
		project_id: "workspace-project",
		project_name: "Workspace Project",
		project_revision: 3,
		stage: "execution",
		status: "passed",
		target_environment: "staging",
		selected_count: 4,
		executed_count: 4,
		passed_count: 4,
		failed_count: 0,
		invalid_count: 0,
		skipped_count: 0,
		snapshot_id: null,
		source_snapshot_id: null,
		updated_at: DEFAULT_TIMESTAMP,
		...overrides,
	};
}

export function workspaceReportFixture(overrides = {}) {
	return {
		report_id: "report-workspace-project",
		project_id: "workspace-project",
		project_name: "Workspace Project",
		project_revision: 3,
		stage: "reports",
		status: "approved",
		report_type: "export",
		format: "json",
		operation: "export.json",
		approved: true,
		stale: false,
		count: 4,
		source_snapshot_id: null,
		execution_run_ids: [],
		updated_at: DEFAULT_TIMESTAMP,
		...overrides,
	};
}

export function workspaceSummaryFixture(overrides = {}) {
	return {
		continue_working: null,
		projects: [],
		work_items: [],
		recent_runs: [],
		recent_reports: [],
		generated_at: WORKSPACE_GENERATED_AT,
		...overrides,
	};
}

export function projectDetailFixture(project, overrides = {}) {
	return {
		project_id: project.project_id,
		name: project.name,
		description: null,
		status: project.project_status || "active",
		owner_user_id: TEST_USER_ID,
		current_revision: project.project_revision ?? 0,
		created_at: "2026-07-16T10:00:00Z",
		updated_at: project.updated_at || DEFAULT_TIMESTAMP,
		stage_state: {},
		current_snapshots: {},
		timeline: [],
		execution_runs: [],
		...overrides,
	};
}

export function createDeferred() {
	let resolve;
	let reject;
	const promise = new Promise((resolvePromise, rejectPromise) => {
		resolve = resolvePromise;
		reject = rejectPromise;
	});
	return { promise, resolve, reject };
}

export async function seedStoredProject(page, projectId) {
	await page.addInitScript(
		({ storageKey, storedProjectId }) => {
			window.localStorage.setItem(storageKey, storedProjectId);
		},
		{ storageKey: STORAGE_CURRENT_PROJECT_ID, storedProjectId: projectId }
	);
}

function jsonResponse(route, payload, status = 200) {
	return route.fulfill({
		status,
		contentType: "application/json",
		body: JSON.stringify(payload),
	});
}

function listProjectShape(project) {
	return {
		project_id: project.project_id,
		name: project.name,
		description: project.description || null,
		status: project.status || "active",
		owner_user_id: project.owner_user_id || TEST_USER_ID,
		current_revision: project.current_revision ?? 0,
		created_at: project.created_at || "2026-07-16T10:00:00Z",
		updated_at: project.updated_at || DEFAULT_TIMESTAMP,
		stage_state: project.stage_state || {},
	};
}

function defaultOrchestratorStatus(project, workspaceProject = null) {
	const currentStage = workspaceProject?.current_stage || "requirements";
	const currentStatus = workspaceProject?.current_status || "ready";
	return {
		project_id: project.project_id,
		project_revision: project.current_revision,
		current_stage: currentStage,
		stages: {
			[currentStage]: {
				stage: currentStage,
				status: currentStatus,
				current_snapshot_id: workspaceProject?.current_snapshot_id || null,
				version: workspaceProject?.current_snapshot_id ? 1 : 0,
				approved: currentStatus === "completed",
				stale: currentStatus === "stale",
				summary: {},
				blockers: [],
			},
		},
		next_actions: [],
		blockers: [],
		has_baseline_test_suite: false,
		upstream_changed: false,
		changed_upstream_stages: [],
		generated_at: WORKSPACE_GENERATED_AT,
	};
}

function workspaceProjectFromDetail(project, overrides = {}) {
	return workspaceProjectFixture({
		project_id: project.project_id,
		name: project.name,
		project_revision: project.current_revision,
		project_status: project.status,
		updated_at: project.updated_at,
		...overrides,
	});
}

export async function installWorkspaceApi(page, options = {}) {
	let currentSummary = options.summary || workspaceSummaryFixture();
	const summaryScenarios = options.summaryScenarios || [];
	const projectListScenarios = options.projectListScenarios || [];
	const projectDetails = new Map();
	const orchestratorStatuses = new Map(Object.entries(options.orchestratorStatuses || {}));
	const createErrorsByName = options.createErrorsByName || {};

	for (const workspaceProject of currentSummary.projects || []) {
		const detail = options.projectDetails?.[workspaceProject.project_id] || projectDetailFixture(workspaceProject);
		projectDetails.set(workspaceProject.project_id, detail);
	}
	for (const [projectId, detail] of Object.entries(options.projectDetails || {})) {
		projectDetails.set(projectId, detail);
	}

	const requests = {
		workspaceSummary: [],
		projectList: [],
		projectCreate: [],
		projectDetail: [],
	};

	await page.route("**/*", async (route) => {
		const request = route.request();
		if (!["fetch", "xhr"].includes(request.resourceType())) {
			return route.fallback();
		}

		const url = new URL(request.url());
		const { pathname } = url;
		const method = request.method();

		if (pathname === "/auth/me") {
			return jsonResponse(route, {
				sub: TEST_USER_ID,
				email: "playwright-e2e@example.com",
				name: "Playwright E2E",
				picture: null,
			});
		}
		if (pathname === "/reports/usage/me") {
			return jsonResponse(route, { groups: [] });
		}
		if (pathname === "/entitlements/me") {
			return jsonResponse(route, {
				account: { plan_tier: "premium", support_contact_email: "support@example.test" },
				requirements: { remaining: 500, exhausted: false },
				test_cases: { remaining: 500, exhausted: false },
				wallet: { balance_units: 5000, balance_token_display: "5000" },
				shadow_mode: false,
			});
		}
		if (pathname.startsWith("/integrations/")) {
			return jsonResponse(route, { connected: false, connection: null });
		}
		if (pathname === "/workspace/summary" && method === "GET") {
			const requestIndex = requests.workspaceSummary.length;
			requests.workspaceSummary.push({ method, url: url.href });
			const scenario = summaryScenarios[Math.min(requestIndex, Math.max(0, summaryScenarios.length - 1))] || null;
			if (scenario?.gate) {
				await scenario.gate;
			}
			const status = scenario?.status || 200;
			const payload = typeof scenario?.payload === "function" ? scenario.payload() : scenario?.payload || currentSummary;
			return jsonResponse(route, payload, status);
		}
		if (pathname === "/projects" && method === "GET") {
			const requestIndex = requests.projectList.length;
			requests.projectList.push({ method, url: url.href });
			const scenario = projectListScenarios[requestIndex] || null;
			const responseProjects =
				typeof scenario?.projects === "function"
					? scenario.projects(Array.from(projectDetails.values()))
					: scenario?.projects || Array.from(projectDetails.values()).map(listProjectShape);
			if (scenario?.gate || options.projectListGate) {
				await (scenario?.gate || options.projectListGate);
			}
			return jsonResponse(route, { projects: responseProjects }, scenario?.status || 200);
		}
		if (pathname === "/projects" && method === "POST") {
			const payload = request.postDataJSON();
			requests.projectCreate.push({ method, url: url.href, payload });
			if (options.createGate) {
				await options.createGate;
			}
			const configuredError = createErrorsByName[payload?.name];
			if (configuredError) {
				return jsonResponse(route, { detail: configuredError.detail || "Project validation failed" }, configuredError.status || 422);
			}

			const createdProject =
				typeof options.createProject === "function"
					? options.createProject(payload, currentSummary)
					: options.createProject ||
						projectDetailFixture(
							workspaceProjectFixture({
								project_id: "created-project",
								name: payload?.name || "Created Project",
								project_revision: 0,
								updated_at: "2026-07-17T12:30:00Z",
							})
						);
			projectDetails.set(createdProject.project_id, createdProject);
			const workspaceProject = workspaceProjectFromDetail(createdProject);
			const createdWorkItem = workspaceWorkItemFixture({
				work_item_id: `work_${createdProject.project_id}_requirements`,
				project_id: createdProject.project_id,
				project_name: createdProject.name,
				project_revision: createdProject.current_revision,
			});
			currentSummary = workspaceSummaryFixture({
				...currentSummary,
				continue_working: createdWorkItem,
				projects: [workspaceProject, ...(currentSummary.projects || []).filter((item) => item.project_id !== createdProject.project_id)],
				work_items: [createdWorkItem, ...(currentSummary.work_items || []).filter((item) => item.project_id !== createdProject.project_id)],
			});
			return jsonResponse(route, createdProject, 201);
		}

		const statusMatch = pathname.match(/^\/projects\/([^/]+)\/orchestrator\/status$/);
		if (statusMatch && method === "GET") {
			const projectId = decodeURIComponent(statusMatch[1]);
			const project = projectDetails.get(projectId);
			if (!project) {
				return jsonResponse(route, { detail: "Project not found" }, 404);
			}
			const workspaceProject = (currentSummary.projects || []).find((item) => item.project_id === projectId) || null;
			return jsonResponse(route, orchestratorStatuses.get(projectId) || defaultOrchestratorStatus(project, workspaceProject));
		}

		const runsMatch = pathname.match(/^\/projects\/([^/]+)\/orchestrator\/runs$/);
		if (runsMatch && method === "GET") {
			const projectId = decodeURIComponent(runsMatch[1]);
			return projectDetails.has(projectId)
				? jsonResponse(route, { runs: [], events: [], checkpoints: [] })
				: jsonResponse(route, { detail: "Project not found" }, 404);
		}

		const detailMatch = pathname.match(/^\/projects\/([^/]+)$/);
		if (detailMatch && method === "GET") {
			const projectId = decodeURIComponent(detailMatch[1]);
			requests.projectDetail.push({ method, url: url.href, projectId });
			const project = projectDetails.get(projectId);
			return project ? jsonResponse(route, project) : jsonResponse(route, { detail: "Project not found" }, 404);
		}

		return route.fallback();
	});

	return {
		requests,
		getSummary: () => currentSummary,
		setSummary: (nextSummary) => {
			currentSummary = nextSummary;
		},
	};
}
