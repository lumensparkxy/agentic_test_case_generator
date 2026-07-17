import { expect, test } from "@playwright/test";

import {
	buildGlobalPath,
	buildProjectPath,
	getDestinationForLegacyTab,
	getDestinationForStage,
	getLegacyTabForDestination,
	parseWorkflowRoute,
	resolveOrchestratorDestination,
} from "../src/app/workflowRoutes.js";
import { selectContextualTask } from "../src/components/projects/contextualTask.js";
import { sampleRequirementsFile, seedAuthenticatedSession } from "./support/auth.js";

const STORAGE_CURRENT_PROJECT_ID = "tcg.current.project_id";
const TEST_USER_ID = "playwright-e2e-user";

function projectFixture(projectId, name, overrides = {}) {
	return {
		project_id: projectId,
		name,
		description: null,
		status: "active",
		owner_user_id: TEST_USER_ID,
		current_revision: 1,
		created_at: "2026-07-17T08:00:00Z",
		updated_at: "2026-07-17T08:00:00Z",
		stage_state: {},
		current_snapshots: {},
		timeline: [],
		execution_runs: [],
		...overrides,
	};
}

const PROJECT_A = projectFixture("route-project-a", "Route Project Alpha");
const PROJECT_B = projectFixture("route-project-b", "Route Project Beta", { current_revision: 2 });

function projectSummary(project) {
	return {
		project_id: project.project_id,
		name: project.name,
		description: project.description,
		status: project.status,
		owner_user_id: project.owner_user_id,
		current_revision: project.current_revision,
		created_at: project.created_at,
		updated_at: project.updated_at,
		stage_state: project.stage_state,
	};
}

function orchestratorStatus(project) {
	return {
		project_id: project.project_id,
		project_revision: project.current_revision,
		current_stage: "requirements",
		stages: {},
		next_actions: [],
		blockers: [],
		has_baseline_test_suite: false,
		upstream_changed: false,
		changed_upstream_stages: [],
		generated_at: "2026-07-17T08:00:00Z",
	};
}

function requirementsParseFixture(id, text) {
	return {
		source_name: `${id.toLowerCase()}.md`,
		raw_text: text,
		requirements: [
			{
				id,
				text,
				priority: "High",
				review_status: "Approved",
			},
		],
		review: { approved: true, score: 1, issues: [] },
		coverage_metrics: null,
		workflow_diagnostics: null,
		workflow_settings: null,
		iteration_history: [],
	};
}

function jsonResponse(route, payload, status = 200) {
	return route.fulfill({
		status,
		contentType: "application/json",
		body: JSON.stringify(payload),
	});
}

async function installRouteShellApi(page, options = {}) {
	const projects = options.projects || [PROJECT_A, PROJECT_B];
	const projectsById = new Map(projects.map((project) => [project.project_id, project]));
	const projectErrors = options.projectErrors || {};
	const projectDetailGates = options.projectDetailGates || {};
	const requirementsParseGate = options.requirementsParseGate || null;
	const requirementsParseResponse = options.requirementsParseResponse || null;
	const requirementsParseScenarios = options.requirementsParseScenarios || [];
	const detailProjectIds = [];
	const projectListRequests = [];
	const requirementsParseRequests = [];

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
		if (pathname === "/workspace/summary") {
			return jsonResponse(route, {
				continue_working: null,
				projects: [],
				work_items: [],
				recent_runs: [],
				recent_reports: [],
				generated_at: "2026-07-17T08:00:00Z",
			});
		}
		if (pathname.startsWith("/integrations/")) {
			return jsonResponse(route, { connected: false, connection: null });
		}
		if (pathname === "/projects" && method === "GET") {
			projectListRequests.push(pathname);
			return jsonResponse(route, { projects: projects.map(projectSummary) });
		}
		if (pathname === "/requirements/parse" && method === "POST" && (requirementsParseResponse || requirementsParseScenarios.length)) {
			const scenario = requirementsParseScenarios[requirementsParseRequests.length] || null;
			requirementsParseRequests.push(pathname);
			const responseGate = scenario?.gate || requirementsParseGate;
			if (responseGate) {
				await responseGate;
			}
			return jsonResponse(route, scenario?.response || requirementsParseResponse);
		}

		const statusMatch = pathname.match(/^\/projects\/([^/]+)\/orchestrator\/status$/);
		if (statusMatch) {
			const project = projectsById.get(decodeURIComponent(statusMatch[1]));
			return project ? jsonResponse(route, orchestratorStatus(project)) : jsonResponse(route, { detail: "Project not found" }, 404);
		}

		const runsMatch = pathname.match(/^\/projects\/([^/]+)\/orchestrator\/runs$/);
		if (runsMatch) {
			const project = projectsById.get(decodeURIComponent(runsMatch[1]));
			return project
				? jsonResponse(route, { runs: [], events: [], checkpoints: [] })
				: jsonResponse(route, { detail: "Project not found" }, 404);
		}

		const detailMatch = pathname.match(/^\/projects\/([^/]+)$/);
		if (detailMatch && method === "GET") {
			const projectId = decodeURIComponent(detailMatch[1]);
			detailProjectIds.push(projectId);
			if (projectDetailGates[projectId]) {
				await projectDetailGates[projectId];
			}
			if (projectErrors[projectId]) {
				const status = projectErrors[projectId];
				return jsonResponse(route, { detail: status === 403 ? "Project access denied" : "Project not found" }, status);
			}
			const project = projectsById.get(projectId);
			return project ? jsonResponse(route, project) : jsonResponse(route, { detail: "Project not found" }, 404);
		}

		return route.fallback();
	});

	return { detailProjectIds, projectListRequests, requirementsParseRequests };
}

async function seedStoredProject(page, projectId) {
	await page.addInitScript(({ storageKey, storedProjectId }) => window.localStorage.setItem(storageKey, storedProjectId), {
		storageKey: STORAGE_CURRENT_PROJECT_ID,
		storedProjectId: projectId,
	});
}

async function expectSingleCurrent(navigation, label) {
	const currentItems = navigation.locator('[aria-current="page"]');
	await expect(currentItems).toHaveCount(1);
	await expect(currentItems.first()).toHaveAccessibleName(new RegExp(`^${label}(?:,.*)?$`, "i"));
	await expect(currentItems.first()).toHaveClass(/active/);
}

async function expectAtMostOneCurrent(navigation) {
	await expect.poll(async () => navigation.locator('[aria-current="page"]').count()).toBeLessThanOrEqual(1);
}

async function settleBrowserEffects(page) {
	await page.evaluate(
		() =>
			new Promise((resolve) => {
				window.requestAnimationFrame(() => window.requestAnimationFrame(resolve));
			})
	);
}

test.describe("Workflow route contract", () => {
	test("centralizes global, project, legacy-tab, stage, and orchestrator action resolution", () => {
		expect(buildGlobalPath("home")).toBe("/");
		expect(buildGlobalPath("projects")).toBe("/projects");
		expect(buildGlobalPath("reviews")).toBe("/reviews");
		expect(buildGlobalPath("runs")).toBe("/runs");
		expect(buildGlobalPath("reports")).toBe("/reports");

		expect(buildProjectPath("project-a")).toBe("/projects/project-a");
		expect(buildProjectPath("project-a", "requirements")).toBe("/projects/project-a/requirements");
		expect(buildProjectPath("project-a", "use-cases")).toBe("/projects/project-a/use-cases");
		expect(buildProjectPath("project-a", "test-cases")).toBe("/projects/project-a/test-cases");
		expect(buildProjectPath("project with spaces", "context")).toBe("/projects/project%20with%20spaces/context");

		expect(parseWorkflowRoute("/")).toMatchObject({ kind: "global", destination: "home" });
		expect(parseWorkflowRoute("/projects/project-a/context")).toMatchObject({
			kind: "project",
			projectId: "project-a",
			destination: "context",
		});
		expect(parseWorkflowRoute("/not-a-route")).toMatchObject({ kind: "not-found" });

		expect([0, 1, 2, 3, 4, 5, 6, 7].map(getDestinationForLegacyTab)).toEqual([
			"requirements",
			"context",
			"test-cases",
			"test-cases",
			"automation",
			"reports",
			"use-cases",
			"overview",
		]);
		expect(getLegacyTabForDestination("requirements")).toBe(0);
		expect(getLegacyTabForDestination("test-cases")).toBe(3);
		expect(getLegacyTabForDestination("use-cases")).toBe(6);
		expect(getLegacyTabForDestination("overview")).toBe(7);

		const stageCases = {
			requirements: "requirements",
			context: "context",
			use_cases: "use-cases",
			impact_analysis: "test-cases",
			test_cases: "test-cases",
			review: "test-cases",
			automation: "automation",
			execution: "automation",
			reports: "reports",
		};
		for (const [stage, destination] of Object.entries(stageCases)) {
			expect(getDestinationForStage(stage)).toBe(destination);
		}

		const actionCases = [
			[{ action: "approve", stage: "requirements" }, "requirements"],
			[{ action: "approve", stage: "use_cases" }, "use-cases"],
			[{ action: "refine", stage: "requirements" }, "requirements"],
			[{ action: "refine", stage: "context" }, "context"],
			[{ action: "approve", stage: "test_cases" }, "test-cases"],
			[{ action: "generate", stage: "test_cases" }, "test-cases"],
			[{ action: "full_regenerate", stage: "test_cases" }, "test-cases"],
			[{ action: "analyze_impact", stage: "impact_analysis" }, "test-cases"],
			[{ action: "apply_update", stage: "impact_analysis" }, "test-cases"],
			[{ action: "review", stage: "review" }, "test-cases"],
			[{ action: "automate", stage: "automation" }, "automation"],
			[{ action: "execute", stage: "execution" }, "automation"],
			[{ action: "report", stage: "reports" }, "reports"],
		];
		for (const [recommendation, destination] of actionCases) {
			expect(resolveOrchestratorDestination(recommendation)).toBe(destination);
		}
		expect(resolveOrchestratorDestination({ action: "unknown", stage: "unknown" })).toBeNull();

		const rankedActions = actionCases.slice(0, 3).map(([action], index) => ({
			...action,
			enabled: true,
			primary: index === 0,
			secondary: index > 0,
		}));
		expect(selectContextualTask({ next_actions: rankedActions }, { destination: "requirements" }).primaryAction).toEqual(rankedActions[0]);
		expect(selectContextualTask({ next_actions: rankedActions }, { destination: "context" }).primaryAction).toBeNull();
		expect(selectContextualTask({ next_actions: [] }, { destination: "requirements" }).primaryAction).toBeNull();

		const misrankedRegeneration = [
			{ action: "full_regenerate", stage: "test_cases", primary: true, secondary: false },
			{ action: "analyze_impact", stage: "impact_analysis", primary: false, secondary: true },
		];
		const safeRegenerationTask = selectContextualTask({ next_actions: misrankedRegeneration }, { destination: "test-cases" });
		expect(safeRegenerationTask.primaryAction).toEqual(misrankedRegeneration[1]);
		expect(safeRegenerationTask.secondaryActions).toContain(misrankedRegeneration[0]);
	});
});

test.describe("Route-driven application shell", () => {
	test("keeps Home isolated from a valid stored project", async ({ page }) => {
		const api = await installRouteShellApi(page);
		await seedAuthenticatedSession(page);
		await seedStoredProject(page, PROJECT_A.project_id);

		await page.goto("/");

		await expect(page).toHaveURL(/\/$/);
		await expect(page.getByRole("heading", { name: /^Home$/i })).toBeVisible({ timeout: 30_000 });
		await expectSingleCurrent(page.getByRole("navigation", { name: "Global navigation" }), "Home");
		await expect(page.getByRole("navigation", { name: "Project navigation" })).toHaveCount(0);
		await expect(page.getByRole("heading", { name: /^Upload Requirements$/i })).toHaveCount(0);
		await expect(page.getByLabel("Contextual task")).toHaveCount(0);
		await expect(page.getByLabel("Project information rail")).toHaveCount(0);
		await expect.poll(() => api.projectListRequests.length).toBeGreaterThan(0);
		await settleBrowserEffects(page);
		expect(api.detailProjectIds).toEqual([]);
	});

	test("renders every global destination with one active navigation item", async ({ page }) => {
		await installRouteShellApi(page);
		await seedAuthenticatedSession(page);

		const destinations = [
			{ path: "/", heading: "Home", active: "Home" },
			{ path: "/projects", heading: "Projects", active: "Projects" },
			{ path: "/reviews", heading: "Review Inbox", active: "Reviews" },
			{ path: "/runs", heading: "Runs", active: "Runs" },
			{ path: "/reports", heading: "Reports", active: "Reports" },
		];

		for (const destination of destinations) {
			await page.goto(destination.path);
			await expect(page).toHaveURL(new RegExp(`${destination.path === "/" ? "/" : destination.path}/?$`));
			await expect(page.getByRole("heading", { name: new RegExp(`^${destination.heading}$`, "i"), level: 1 })).toBeVisible({
				timeout: 30_000,
			});
			const globalNavigation = page.getByRole("navigation", { name: "Global navigation" });
			await expectSingleCurrent(globalNavigation, destination.active);
			await expect(page.getByRole("navigation", { name: "Project navigation" })).toHaveCount(0);
		}
	});

	test("hydrates every direct project workbench with separate active navigation", async ({ page }) => {
		await installRouteShellApi(page);
		await seedAuthenticatedSession(page);

		const destinations = [
			{ destination: "overview", heading: PROJECT_A.name, active: "Overview" },
			{ destination: "requirements", heading: "Upload Requirements", active: "Requirements" },
			{ destination: "context", heading: "Context Inputs", active: "Context" },
			{ destination: "use-cases", heading: "Use Cases", active: "Use Cases" },
			{ destination: "test-cases", heading: "Generate Test Cases", active: "Test Cases" },
			{ destination: "automation", heading: "Automation", active: "Automation" },
			{ destination: "reports", heading: "Export Test Cases", active: "Reports" },
		];

		for (const destination of destinations) {
			const path = buildProjectPath(PROJECT_A.project_id, destination.destination);
			await page.goto(path);
			await expect(page).toHaveURL(new RegExp(`${path}/?$`));
			await expect(page.getByRole("heading", { name: new RegExp(`^${destination.heading}$`, "i") })).toBeVisible({
				timeout: 30_000,
			});
			await expect(page.getByLabel("Project information rail")).toContainText(PROJECT_A.name);
			await expectSingleCurrent(page.getByRole("navigation", { name: "Global navigation" }), "Projects");
			await expectSingleCurrent(page.getByRole("navigation", { name: "Project navigation" }), destination.active);
		}
	});

	test("restores direct destinations and project selection through reload, Back, and Forward", async ({ page }) => {
		await installRouteShellApi(page);
		await seedAuthenticatedSession(page);

		await page.goto(buildProjectPath(PROJECT_A.project_id, "requirements"));
		const projectNavigation = page.getByRole("navigation", { name: "Project navigation" });
		await expect(projectNavigation).toBeVisible({ timeout: 30_000 });
		await projectNavigation.getByRole("link", { name: /^Context(?:,|$)/i }).click();
		await expect(page).toHaveURL(buildProjectPath(PROJECT_A.project_id, "context"));
		await projectNavigation.getByRole("link", { name: /^Test Cases(?:,|$)/i }).click();
		await expect(page).toHaveURL(buildProjectPath(PROJECT_A.project_id, "test-cases"));

		await page.reload();
		await expect(page).toHaveURL(buildProjectPath(PROJECT_A.project_id, "test-cases"));
		await expect(page.getByRole("heading", { name: /^Generate Test Cases$/i })).toBeVisible({ timeout: 30_000 });
		await expectSingleCurrent(page.getByRole("navigation", { name: "Project navigation" }), "Test Cases");

		await page.goBack();
		await expect(page).toHaveURL(buildProjectPath(PROJECT_A.project_id, "context"));
		await expect(page.getByRole("heading", { name: /^Context Inputs$/i })).toBeVisible();
		await expectSingleCurrent(page.getByRole("navigation", { name: "Project navigation" }), "Context");

		await page.goForward();
		await expect(page).toHaveURL(buildProjectPath(PROJECT_A.project_id, "test-cases"));
		await expectSingleCurrent(page.getByRole("navigation", { name: "Project navigation" }), "Test Cases");

		await page.goto(buildProjectPath(PROJECT_B.project_id, "automation"));
		await expect(page.getByLabel("Project information rail")).toContainText(PROJECT_B.name);
		await page.goBack();
		await expect(page).toHaveURL(buildProjectPath(PROJECT_A.project_id, "test-cases"));
		await expect(page.getByLabel("Project information rail")).toContainText(PROJECT_A.name);
		await page.goForward();
		await expect(page).toHaveURL(buildProjectPath(PROJECT_B.project_id, "automation"));
		await expect(page.getByLabel("Project information rail")).toContainText(PROJECT_B.name);
	});

	test("makes the URL project ID authoritative over a conflicting stored project", async ({ page }) => {
		const api = await installRouteShellApi(page);
		await seedAuthenticatedSession(page);
		await seedStoredProject(page, PROJECT_B.project_id);

		await page.goto(buildProjectPath(PROJECT_A.project_id, "context"));

		await expect(page.getByRole("heading", { name: /^Context Inputs$/i })).toBeVisible({ timeout: 30_000 });
		await expect(page.getByLabel("Project information rail")).toContainText(PROJECT_A.name);
		await expect(page.getByLabel("Project information rail")).not.toContainText(PROJECT_B.name);
		await expect
			.poll(() => page.evaluate((storageKey) => window.localStorage.getItem(storageKey), STORAGE_CURRENT_PROJECT_ID))
			.toBe(PROJECT_A.project_id);
		await expect.poll(() => api.projectListRequests.length).toBeGreaterThan(0);
		await settleBrowserEffects(page);
		expect(api.detailProjectIds).not.toContain(PROJECT_B.project_id);
	});

	test("removes an invalid stored project without uncaught errors or stale content", async ({ page }) => {
		const pageErrors = [];
		page.on("pageerror", (error) => pageErrors.push(error.message));
		const api = await installRouteShellApi(page);
		await seedAuthenticatedSession(page);
		await seedStoredProject(page, "project-does-not-exist");

		await page.goto("/");

		await expect(page.getByRole("heading", { name: /^Home$/i })).toBeVisible({ timeout: 30_000 });
		await expect.poll(() => page.evaluate((storageKey) => window.localStorage.getItem(storageKey), STORAGE_CURRENT_PROJECT_ID)).toBeNull();
		await expect(page.getByLabel("Project information rail")).toHaveCount(0);
		expect(api.detailProjectIds).toEqual([]);
		expect(pageErrors).toEqual([]);
	});

	test("provides recovery actions for unknown global and project destinations", async ({ page }) => {
		await installRouteShellApi(page);
		await seedAuthenticatedSession(page);

		for (const path of ["/not-a-real-route", `/projects/${PROJECT_A.project_id}/not-a-stage`]) {
			await page.goto(path);
			const main = page.getByRole("main");
			await expect(main.getByRole("heading", { name: /not found|unknown (?:project )?destination|destination unavailable/i })).toBeVisible({
				timeout: 30_000,
			});
			await expect(main.getByRole("link", { name: /home/i })).toBeVisible();
			await expect(main.getByRole("link", { name: /projects/i })).toBeVisible();
			await expectAtMostOneCurrent(page.getByRole("navigation", { name: "Global navigation" }));
		}
	});

	for (const status of [403, 404]) {
		test(`renders project recovery instead of stored content when project loading returns ${status}`, async ({ page }) => {
			const unavailableProjectId = `unavailable-${status}`;
			const api = await installRouteShellApi(page, { projectErrors: { [unavailableProjectId]: status } });
			await seedAuthenticatedSession(page);
			await seedStoredProject(page, PROJECT_B.project_id);

			await page.goto(buildProjectPath(unavailableProjectId, "requirements"));

			const main = page.getByRole("main");
			await expect(main.getByRole("heading", { name: /project unavailable|unable to open project|project not found/i })).toBeVisible({
				timeout: 30_000,
			});
			await expect(main.getByRole("link", { name: /home/i })).toBeVisible();
			await expect(main.getByRole("link", { name: /projects/i })).toBeVisible();
			await expect(page.getByLabel("Project information rail")).toHaveCount(0);
			expect(api.detailProjectIds).toEqual([unavailableProjectId]);
			expect(api.detailProjectIds).not.toContain(PROJECT_B.project_id);
		});
	}

	test("shows a stable loading state while a direct project route hydrates", async ({ page }) => {
		let releaseProject;
		const projectGate = new Promise((resolve) => {
			releaseProject = resolve;
		});
		await installRouteShellApi(page, { projectDetailGates: { [PROJECT_A.project_id]: projectGate } });
		await seedAuthenticatedSession(page);

		await page.goto(buildProjectPath(PROJECT_A.project_id, "requirements"));
		await expect(page.getByRole("heading", { name: /opening project/i })).toBeVisible({ timeout: 30_000 });
		await expect(page.getByLabel("Project information rail")).toHaveCount(0);

		releaseProject();
		await expect(page.getByRole("heading", { name: /^Upload Requirements$/i })).toBeVisible({ timeout: 30_000 });
		await expect(page.getByLabel("Project information rail")).toContainText(PROJECT_A.name);
	});

	test("ignores a delayed project action after the user switches projects", async ({ page }) => {
		let releaseParse;
		const parseGate = new Promise((resolve) => {
			releaseParse = resolve;
		});
		const lateRequirementText = "Late Project Alpha requirement must never appear in Project Beta";
		const api = await installRouteShellApi(page, {
			requirementsParseGate: parseGate,
			requirementsParseResponse: requirementsParseFixture("REQ-A-LATE", lateRequirementText),
		});
		await seedAuthenticatedSession(page);

		await page.goto(buildProjectPath(PROJECT_A.project_id, "requirements"));
		await expect(page.getByRole("heading", { name: /^Upload Requirements$/i })).toBeVisible({ timeout: 30_000 });
		await page.locator('input[type="file"]').setInputFiles(sampleRequirementsFile);
		await page.getByRole("button", { name: /^Parse Requirements$/i }).click();
		await expect.poll(() => api.requirementsParseRequests.length).toBe(1);

		await page.getByRole("button", { name: "Open QA project menu" }).click();
		await page.getByRole("button", { name: `Open QA project ${PROJECT_B.name}` }).click();
		await expect(page).toHaveURL(buildProjectPath(PROJECT_B.project_id));
		await expect(page.getByRole("heading", { name: new RegExp(`^${PROJECT_B.name}$`, "i") })).toBeVisible({ timeout: 30_000 });
		await expect(page.getByLabel("Project information rail")).toContainText(PROJECT_B.name);

		const parseResponse = page.waitForResponse(
			(response) => new URL(response.url()).pathname === "/requirements/parse" && response.request().method() === "POST"
		);
		releaseParse();
		await parseResponse;
		await settleBrowserEffects(page);

		await expect(page).toHaveURL(buildProjectPath(PROJECT_B.project_id));
		await expect(page.getByLabel("Project information rail")).toContainText(PROJECT_B.name);
		await expect(page.getByText(lateRequirementText, { exact: true })).toHaveCount(0);
		await expect
			.poll(() => page.evaluate((storageKey) => window.localStorage.getItem(storageKey), STORAGE_CURRENT_PROJECT_ID))
			.toBe(PROJECT_B.project_id);
		expect(api.detailProjectIds).toEqual([PROJECT_A.project_id, PROJECT_B.project_id]);
	});

	test("keeps an older action stale across an A to B to A route round-trip", async ({ page }) => {
		let releaseOldParse;
		let releaseNewParse;
		const oldParseGate = new Promise((resolve) => {
			releaseOldParse = resolve;
		});
		const newParseGate = new Promise((resolve) => {
			releaseNewParse = resolve;
		});
		const oldRequirementText = "Old Project Alpha response must remain stale after a route round-trip";
		const newRequirementText = "Current Project Alpha response wins after returning from Project Beta";
		const api = await installRouteShellApi(page, {
			requirementsParseScenarios: [
				{ gate: oldParseGate, response: requirementsParseFixture("REQ-A-OLD", oldRequirementText) },
				{ gate: newParseGate, response: requirementsParseFixture("REQ-A-NEW", newRequirementText) },
			],
		});
		await seedAuthenticatedSession(page);

		await page.goto(buildProjectPath(PROJECT_A.project_id, "requirements"));
		await expect(page.getByRole("heading", { name: /^Upload Requirements$/i })).toBeVisible({ timeout: 30_000 });
		await page.locator('input[type="file"]').setInputFiles(sampleRequirementsFile);
		await page.getByRole("button", { name: /^Parse Requirements$/i }).click();
		await expect.poll(() => api.requirementsParseRequests.length).toBe(1);

		await page.getByRole("button", { name: "Open QA project menu" }).click();
		await page.getByRole("button", { name: `Open QA project ${PROJECT_B.name}` }).click();
		await expect(page.getByRole("heading", { name: new RegExp(`^${PROJECT_B.name}$`, "i") })).toBeVisible({ timeout: 30_000 });
		await page.getByRole("button", { name: "Open QA project menu" }).click();
		await page.getByRole("button", { name: `Open QA project ${PROJECT_A.name}` }).click();
		await expect(page.getByRole("heading", { name: new RegExp(`^${PROJECT_A.name}$`, "i") })).toBeVisible({ timeout: 30_000 });
		await page
			.getByRole("navigation", { name: "Project navigation" })
			.getByRole("link", { name: /^Requirements(?:,|$)/i })
			.click();
		await expect(page.getByRole("heading", { name: /^Upload Requirements$/i })).toBeVisible();

		await page.locator('input[type="file"]').setInputFiles(sampleRequirementsFile);
		await page.getByRole("button", { name: /^Parse Requirements$/i }).click();
		await expect.poll(() => api.requirementsParseRequests.length).toBe(2);
		await expect(page.getByRole("button", { name: /Parsing/i })).toBeDisabled();

		const oldParseResponse = page.waitForResponse(
			(response) => new URL(response.url()).pathname === "/requirements/parse" && response.request().method() === "POST"
		);
		releaseOldParse();
		await oldParseResponse;
		await settleBrowserEffects(page);

		await expect(page.getByRole("button", { name: /Parsing/i })).toBeDisabled();
		await expect(page.getByText(oldRequirementText, { exact: true })).toHaveCount(0);
		expect(api.detailProjectIds).toEqual([PROJECT_A.project_id, PROJECT_B.project_id, PROJECT_A.project_id]);

		const newParseResponse = page.waitForResponse(
			(response) => new URL(response.url()).pathname === "/requirements/parse" && response.request().method() === "POST"
		);
		releaseNewParse();
		await newParseResponse;
		await expect(page.getByText(newRequirementText, { exact: true }).last()).toBeVisible();
		await expect(page.getByRole("button", { name: /^Parse Requirements$/i })).toBeVisible();
		await expect(page.getByText(oldRequirementText, { exact: true })).toHaveCount(0);
		await expect.poll(() => api.detailProjectIds.length).toBe(4);
		expect(api.detailProjectIds.at(-1)).toBe(PROJECT_A.project_id);
	});
});
