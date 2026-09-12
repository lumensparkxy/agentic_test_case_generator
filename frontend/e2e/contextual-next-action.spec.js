import { expect, test } from "@playwright/test";

import { buildProjectPath } from "../src/app/workflowRoutes.js";
import { seedAuthenticatedSession } from "./support/auth.js";

const PROJECT_ID = "contextual-task-project";
const OLD_CASE_TITLE = "Preserved checkout baseline";
const NEW_CASE_TITLE = "Regenerated checkout coverage";

const oldTestCase = {
	id: "TC-OLD",
	title: OLD_CASE_TITLE,
	description: "The existing suite must remain visible while a replacement is pending.",
	priority: "High",
	type: "Regression",
	status: "Ready",
	preconditions: "A checkout baseline exists.",
	steps: [{ step: 1, action: "Run the existing checkout flow", expected: "The baseline passes", test_data: null }],
	expected_result: "The existing checkout baseline remains covered.",
	test_data: null,
	estimated_time: "5 mins",
	automation_status: "Manual",
	component: "Checkout",
	tags: ["baseline"],
	linked_requirement_ids: ["REQ-001"],
	scenario_refs: ["REQ-001-SCN-01"],
};

const newTestCase = {
	...oldTestCase,
	id: "TC-NEW",
	title: NEW_CASE_TITLE,
	description: "Replacement coverage returned by a successful durable generation request.",
	tags: ["regenerated"],
};

function projectFixture() {
	return {
		project_id: PROJECT_ID,
		name: "Contextual Task QA",
		description: null,
		status: "active",
		owner_user_id: "playwright-e2e-user",
		current_revision: 8,
		created_at: "2026-07-17T08:00:00Z",
		updated_at: "2026-07-17T08:00:00Z",
		stage_state: {
			requirements: { current_snapshot_id: "snap-req-v2", version: 2, approved: true, stale: false, metadata: {} },
			use_cases: { current_snapshot_id: "snap-use-v1", version: 1, approved: true, stale: false, metadata: {} },
			test_cases: {
				current_snapshot_id: "snap-test-v1",
				version: 1,
				approved: true,
				stale: true,
				stale_reason: "requirements changed",
				metadata: {},
			},
		},
		current_snapshots: {
			requirements: {
				snapshot_id: "snap-req-v2",
				project_id: PROJECT_ID,
				stage: "requirements",
				version: 2,
				project_revision: 8,
				operation: "requirements.refine",
				approved: true,
				payload: {
					requirements: [{ id: "REQ-001", text: "Customers can complete checkout.", review_status: "Approved" }],
					review: { approved: true, score: 100, blocking_issues: [] },
				},
				metadata: {},
				created_at: "2026-07-17T08:00:00Z",
			},
			use_cases: {
				snapshot_id: "snap-use-v1",
				project_id: PROJECT_ID,
				stage: "use_cases",
				version: 1,
				project_revision: 7,
				operation: "testcases.generate.use_cases",
				approved: true,
				payload: {
					requirement_analysis: [],
					coverage_plan: [
						{
							requirement_id: "REQ-001",
							requirement_text: "Customers can complete checkout.",
							scenarios: [
								{
									id: "REQ-001-SCN-01",
									requirement_id: "REQ-001",
									scenario_type: "Happy Path",
									title: "Complete checkout",
									objective: "Validate checkout completion.",
									priority: "High",
									must_have: true,
								},
							],
						},
					],
				},
				metadata: {},
				created_at: "2026-07-17T08:00:00Z",
			},
			test_cases: {
				snapshot_id: "snap-test-v1",
				project_id: PROJECT_ID,
				stage: "test_cases",
				version: 1,
				project_revision: 7,
				operation: "testcases.generate",
				approved: true,
				payload: {
					test_cases: [oldTestCase],
					requirement_analysis: [],
					coverage_plan: [],
					coverage_metrics: null,
					review: { approved: true, score: 100, threshold: 85, summary: "Approved.", blocking_issues: [] },
					iteration_history: [],
				},
				metadata: { source_requirements_snapshot_id: "snap-req-v2", source_use_case_snapshot_id: "snap-use-v1" },
				created_at: "2026-07-17T08:00:00Z",
			},
		},
		timeline: [],
		execution_runs: [],
	};
}

function recommendation(action, overrides = {}) {
	const defaults = {
		action,
		label: action,
		stage: "test_cases",
		enabled: true,
		primary: false,
		secondary: true,
		reason: "Optional workflow action.",
		blockers: [],
		agent_kind: "test_cases",
		agent_contract_version: "1.0",
		agent_implementation: "local",
	};
	return { ...defaults, ...overrides };
}

function statusFixture(actions) {
	return {
		project_id: PROJECT_ID,
		project_revision: 8,
		current_stage: "impact_analysis",
		stages: {
			requirements: {
				stage: "requirements",
				status: "completed",
				current_snapshot_id: "snap-req-v2",
				version: 2,
				approved: true,
				stale: false,
				summary: {},
				blockers: [],
			},
			use_cases: {
				stage: "use_cases",
				status: "completed",
				current_snapshot_id: "snap-use-v1",
				version: 1,
				approved: true,
				stale: false,
				summary: {},
				blockers: [],
			},
			impact_analysis: {
				stage: "impact_analysis",
				status: "ready",
				current_snapshot_id: null,
				version: 0,
				approved: false,
				stale: false,
				summary: { changed_item_count: 2 },
				blockers: [],
			},
			test_cases: {
				stage: "test_cases",
				status: "stale",
				current_snapshot_id: "snap-test-v1",
				version: 1,
				approved: true,
				stale: true,
				stale_reason: "requirements changed",
				summary: { test_case_count: 1 },
				blockers: [],
			},
		},
		next_actions: actions,
		blockers: [],
		has_baseline_test_suite: true,
		upstream_changed: true,
		changed_upstream_stages: ["requirements"],
		generated_at: "2026-07-17T08:00:00Z",
	};
}

function staleActions() {
	return [
		recommendation("analyze_impact", {
			label: "Analyze Impact",
			stage: "impact_analysis",
			primary: true,
			secondary: false,
			reason: "Changed requirements should be compared with the current suite.",
			agent_kind: "impact",
		}),
		recommendation("full_regenerate", {
			label: "Full Regenerate",
			reason: "Rebuild the suite only when incremental impact analysis is not sufficient.",
		}),
		recommendation("review", {
			label: "Review Existing Evidence",
			stage: "review",
			reason: "Inspect current evidence before choosing a replacement path.",
		}),
	];
}

function generationResponse() {
	return {
		test_cases: [newTestCase],
		requirement_analysis: [],
		coverage_plan: [],
		coverage_metrics: null,
		review: { approved: true, score: 100, threshold: 85, summary: "Approved.", blocking_issues: [] },
		workflow_diagnostics: null,
		workflow_settings: null,
		iteration_history: [],
	};
}

function jsonResponse(route, payload, status = 200) {
	return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(payload) });
}

async function installApi(page, scenario) {
	const requests = { generation: 0 };

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
				sub: "playwright-e2e-user",
				email: "playwright-e2e@example.com",
				name: "Playwright E2E",
				picture: null,
			});
		}
		if (pathname === "/reports/usage/me") return jsonResponse(route, { groups: [] });
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
		if (pathname.startsWith("/integrations/")) return jsonResponse(route, { connected: false, connection: null });
		if (pathname === "/projects" && method === "GET") {
			return jsonResponse(route, {
				projects: [
					{
						project_id: scenario.project.project_id,
						name: scenario.project.name,
						description: scenario.project.description,
						status: scenario.project.status,
						owner_user_id: scenario.project.owner_user_id,
						current_revision: scenario.project.current_revision,
						created_at: scenario.project.created_at,
						updated_at: scenario.project.updated_at,
						stage_state: scenario.project.stage_state,
					},
				],
			});
		}
		if (pathname === `/projects/${PROJECT_ID}` && method === "GET") return jsonResponse(route, scenario.project);
		if (pathname === `/projects/${PROJECT_ID}/orchestrator/status`) return jsonResponse(route, scenario.status);
		if (pathname === `/projects/${PROJECT_ID}/orchestrator/runs`) {
			return jsonResponse(route, { runs: [], events: [], checkpoints: [] });
		}
		if (pathname === "/testcases/generate" && method === "POST") {
			requests.generation += 1;
			if (scenario.generationGate) await scenario.generationGate;
			if (scenario.generationFailure) return jsonResponse(route, { detail: "Synthetic regeneration failure" }, 500);
			if (scenario.statusAfterGeneration) scenario.status = scenario.statusAfterGeneration;
			return jsonResponse(route, generationResponse());
		}
		if (pathname === "/automation/execution/preview" && method === "POST") {
			return jsonResponse(route, {
				executable: [],
				manual: [],
				unsupported: [],
				invalid: [],
				warnings: [],
				summary: { executable: 0, manual: 0, unsupported: 0, invalid: 0 },
			});
		}
		return jsonResponse(route, { detail: `Unhandled test route: ${method} ${pathname}` }, 404);
	});

	return requests;
}

async function openTestCases(page) {
	await seedAuthenticatedSession(page);
	await page.goto(buildProjectPath(PROJECT_ID, "test-cases"));
	await expect(page.getByRole("button", { name: /open account menu/i })).toBeVisible({ timeout: 30_000 });
}

test.describe("Contextual next task", () => {
	test("shows compact suite controls without the Next task pane or technical details", async ({ page }) => {
		const scenario = { project: projectFixture(), status: statusFixture(staleActions()) };
		await installApi(page, scenario);
		await openTestCases(page);

		const task = page.getByLabel("Test suite actions");
		const reason = "Changed requirements should be compared with the current suite.";
		await expect(task.getByRole("button", { name: /^Start analysis$/i })).toBeVisible();
		await expect(page.getByText("Next task", { exact: true })).toHaveCount(0);
		await expect(task.getByText(reason, { exact: true })).toHaveCount(0);
		await expect(task.locator(".contextual-task-controls > button")).toHaveCount(1);
		await expect(task.getByText(/contract 1\.0/i)).not.toBeVisible();
		await expect(task.getByRole("button", { name: /^Full Regenerate$/i })).toHaveCount(0);

		await task
			.locator("summary")
			.filter({ hasText: /^More actions$/i })
			.click();
		await expect(task.getByText(/Based on Requirements v2 and Use Cases v1/i)).toHaveCount(0);
		await expect(task.getByText(/Requirements: snap-req-v2/i)).toHaveCount(0);
		await expect(task.getByText(/Use Cases: snap-use-v1/i)).toHaveCount(0);
		await expect(task.getByText(/Impact · contract 1\.0 · local/i)).toHaveCount(0);
		await expect(task.getByRole("button", { name: /^Full Regenerate$/i })).toBeVisible();
		await expect(task.getByRole("button", { name: /^Review Existing Evidence$/i })).toBeVisible();
		await page.setViewportSize({ width: 390, height: 844 });
		await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
		await expect(task.getByRole("button", { name: /^Start analysis$/i })).toBeVisible();
		await page.setViewportSize({ width: 1488, height: 900 });
		await expect(page.getByRole("button", { name: /Full Regenerate from/i })).toHaveCount(0);

		await page.goto(buildProjectPath(PROJECT_ID, "overview"));
		await expect(page.getByLabel("Contextual task")).toBeVisible();
		await expect(page.getByText("Next task", { exact: true })).toBeVisible();
		for (const destination of ["context", "automation", "reports"]) {
			await page.goto(buildProjectPath(PROJECT_ID, destination));
			await expect(page.getByLabel("Test suite actions")).toHaveCount(0);
		}

		await page.goto(buildProjectPath(PROJECT_ID, "test-cases"));
		await page.getByRole("button", { name: /^Template setup$/i }).click();
		await expect(page.getByLabel("Test suite actions")).toHaveCount(0);

		scenario.status = statusFixture([]);
		await page.getByRole("button", { name: /^Generate and review$/i }).click();
		await page.reload();
		await expect(page.getByLabel("Test suite actions")).toHaveCount(0);
		await expect(page.getByRole("button", { name: /Analyze Impact for/i })).toBeVisible();

		scenario.status = statusFixture([
			recommendation("full_regenerate", { label: "Full Regenerate", primary: true, secondary: false }),
			recommendation("analyze_impact", {
				label: "Analyze Impact",
				stage: "impact_analysis",
				primary: false,
				secondary: true,
				reason: "Use the incremental path before replacing the suite.",
			}),
		]);
		await page.reload();
		const safeTask = page.getByLabel("Test suite actions");
		await expect(safeTask.getByRole("button", { name: /^Start analysis$/i })).toBeVisible();
		await safeTask
			.locator("summary")
			.filter({ hasText: /^More actions$/i })
			.click();
		await expect(safeTask.getByRole("button", { name: /^Full Regenerate$/i })).toBeVisible();

		scenario.project.stage_state.test_cases.stale = false;
		scenario.project.stage_state.test_cases.stale_reason = null;
		scenario.status = statusFixture([]);
		scenario.status.current_stage = "automation";
		scenario.status.upstream_changed = false;
		scenario.status.changed_upstream_stages = [];
		scenario.status.stages.test_cases.status = "completed";
		scenario.status.stages.test_cases.stale = false;
		await page.reload();
		await expect(page.getByLabel("Test suite actions")).toHaveCount(0);
		await expect(page.getByRole("button", { name: /Generate from \d+ Approved/i })).toHaveCount(0);
	});

	test("renders one blocker, disables a blocked task, and emits no mutation", async ({ page }) => {
		const blocker = "Approve the current Use Cases snapshot before generation.";
		const blockedAction = recommendation("generate", {
			label: "Generate Test Cases",
			primary: true,
			secondary: false,
			enabled: false,
			reason: "Generation is waiting for approval.",
			blockers: [{ code: "missing_approval", message: blocker, stage: "use_cases", action: "generate", severity: "blocking" }],
		});
		const scenario = { project: projectFixture(), status: statusFixture([blockedAction]) };
		const requests = await installApi(page, scenario);
		await openTestCases(page);

		const task = page.getByLabel("Test suite actions");
		const actionButton = task.getByRole("button", { name: /^Start generation$/i });
		await expect(actionButton).toBeDisabled();
		await expect(task.getByText(blocker, { exact: true })).toHaveCount(1);
		await actionButton.evaluate((button) => button.click());
		expect(requests.generation).toBe(0);
	});

	test("does not expose legacy generation while Use Cases approval is the server-ranked task", async ({ page }) => {
		const project = projectFixture();
		project.stage_state.use_cases.approved = true;
		project.stage_state.use_cases.metadata = {};
		project.stage_state.test_cases = {
			current_snapshot_id: null,
			version: 0,
			approved: false,
			stale: false,
			metadata: {},
		};
		delete project.current_snapshots.test_cases;
		const approveUseCases = recommendation("approve", {
			label: "Approve Use Cases",
			stage: "use_cases",
			primary: true,
			secondary: false,
			reason: "A human must approve the current Use Cases snapshot before generation.",
		});
		const status = statusFixture([approveUseCases]);
		status.current_stage = "use_cases";
		status.has_baseline_test_suite = false;
		status.upstream_changed = false;
		status.changed_upstream_stages = [];
		status.stages.use_cases.status = "attention_required";
		status.stages.use_cases.approved = false;
		status.stages.test_cases = {
			...status.stages.test_cases,
			status: "blocked",
			current_snapshot_id: null,
			version: 0,
			approved: false,
			stale: false,
		};
		status.next_actions.push(
			recommendation("full_regenerate", {
				label: "Full Regenerate",
				reason: "Use only the explicit replacement path while approval remains pending.",
			})
		);
		const scenario = { project, status };
		const requests = await installApi(page, scenario);
		await openTestCases(page);

		const optionalTask = page.getByLabel("Test suite actions");
		await page.getByText("More actions", { exact: true }).first().click();
		await expect(optionalTask.getByRole("heading")).toHaveCount(0);
		await expect(optionalTask.locator(".contextual-task-controls > button")).toHaveCount(0);
		await optionalTask
			.locator("summary")
			.filter({ hasText: /^More actions$/i })
			.click();
		await expect(optionalTask.getByRole("button", { name: /^Full Regenerate$/i })).toBeVisible();
		await expect(page.getByRole("button", { name: /Generate from \d+ Approved/i })).toHaveCount(0);
		expect(requests.generation).toBe(0);

		scenario.project = projectFixture();
		scenario.project.stage_state.use_cases.metadata = {};
		scenario.status.has_baseline_test_suite = true;
		await page.reload();
		await expect(page.getByRole("button", { name: new RegExp(OLD_CASE_TITLE, "i") })).toBeVisible();
		await expect(page.getByRole("button", { name: /Implement Changes/i })).toHaveCount(0);
		expect(requests.generation).toBe(0);
	});

	test("requires cancel or confirm for full regeneration, prevents double-submit, and preserves the old suite until success", async ({
		page,
	}) => {
		let releaseGeneration;
		const generationGate = new Promise((resolve) => {
			releaseGeneration = resolve;
		});
		const statusAfterGeneration = statusFixture([
			recommendation("automate", {
				label: "Create Automation Preview",
				stage: "automation",
				primary: true,
				secondary: false,
			}),
		]);
		const scenario = { project: projectFixture(), status: statusFixture(staleActions()), generationGate, statusAfterGeneration };
		const requests = await installApi(page, scenario);
		await openTestCases(page);
		const oldCaseRow = page.getByRole("button", { name: new RegExp(OLD_CASE_TITLE, "i") });
		const newCaseRow = page.getByRole("button", { name: new RegExp(NEW_CASE_TITLE, "i") });
		await expect(oldCaseRow).toBeVisible();

		const task = page.getByLabel("Test suite actions");
		await task
			.locator("summary")
			.filter({ hasText: /^More actions$/i })
			.click();
		const regenerate = task.getByRole("button", { name: /^Full Regenerate$/i });
		await regenerate.click();
		let dialog = page.getByRole("dialog", { name: /Regenerate the entire test suite/i });
		await expect(dialog).toContainText(/current suite stays visible and unchanged/i);
		await expect(dialog).toContainText(/existing cases or coverage may be replaced/i);
		await expect(dialog.getByRole("button", { name: /^Confirm regeneration$/i })).toBeFocused();
		await page.keyboard.press("Tab");
		await expect(dialog.getByRole("button", { name: /^Cancel$/i })).toBeFocused();
		await page.keyboard.press("Shift+Tab");
		await expect(dialog.getByRole("button", { name: /^Confirm regeneration$/i })).toBeFocused();
		await dialog.getByRole("button", { name: /^Cancel$/i }).click();
		await expect(dialog).toHaveCount(0);
		await expect(regenerate).toBeFocused();
		expect(requests.generation).toBe(0);

		await regenerate.click();
		dialog = page.getByRole("dialog", { name: /Regenerate the entire test suite/i });
		const confirm = dialog.getByRole("button", { name: /^Confirm regeneration$/i });
		await confirm.evaluate((button) => {
			button.click();
			button.click();
		});
		await expect.poll(() => requests.generation).toBe(1);
		await expect(dialog.getByRole("button", { name: /^Regenerating…$/i })).toBeDisabled();
		await expect(dialog).toBeFocused();
		await page.keyboard.press("Tab");
		await expect(dialog).toBeFocused();
		await expect(oldCaseRow).toBeVisible();
		await expect(newCaseRow).toHaveCount(0);

		releaseGeneration();
		await expect(dialog).toHaveCount(0);
		await expect(page.getByLabel("Test suite actions")).toHaveCount(0);
		await expect(page.getByRole("main", { name: "Workflow workspace: Test Cases" })).toBeFocused();
		await expect(newCaseRow).toBeVisible();
		await expect(oldCaseRow).toHaveCount(0);
		expect(requests.generation).toBe(1);
	});

	test("keeps the existing suite and offers retry when full regeneration fails", async ({ page }) => {
		const scenario = {
			project: projectFixture(),
			status: statusFixture(staleActions()),
			generationFailure: true,
		};
		const requests = await installApi(page, scenario);
		await openTestCases(page);

		const task = page.getByLabel("Test suite actions");
		await task
			.locator("summary")
			.filter({ hasText: /^More actions$/i })
			.click();
		await task.getByRole("button", { name: /^Full Regenerate$/i }).click();
		const dialog = page.getByRole("dialog", { name: /Regenerate the entire test suite/i });
		await dialog.getByRole("button", { name: /^Confirm regeneration$/i }).click();

		await expect(dialog.getByRole("alert")).toContainText(/current suite was preserved/i);
		await expect(dialog.getByRole("button", { name: /^Confirm regeneration$/i })).toBeEnabled();
		await expect(dialog.getByRole("button", { name: /^Confirm regeneration$/i })).toBeFocused();
		await expect(page.getByRole("button", { name: new RegExp(OLD_CASE_TITLE, "i") })).toBeVisible();
		await expect(page.getByRole("button", { name: new RegExp(NEW_CASE_TITLE, "i") })).toHaveCount(0);
		expect(requests.generation).toBe(1);
	});
});

function reviewScenario() {
	const project = projectFixture();
	project.stage_state.requirements.approved = false;
	project.current_snapshots.requirements.payload.requirements = [1, 2].map((n) => ({
		id: `REQ-00${n}`,
		requirement_uid: `requirement-${n}`,
		text: `Review incoming behavior ${n}.`,
		review_status: "Needs Review",
		quality_flags: [],
	}));
	const action = recommendation("approve", {
		stage: "requirements",
		label: "Review Requirements",
		primary: true,
		secondary: false,
		reason: "Review the two incoming requirements before applying changes.",
	});
	const status = statusFixture([
		action,
		...staleActions()
			.slice(0, 2)
			.map((a) => ({ ...a, primary: false, secondary: true })),
	]);
	status.current_stage = "requirements";
	status.stages.requirements.approved = false;
	status.stages.requirements.status = "attention_required";
	return { project, status };
}

function workspaceForScenario(scenario) {
	const primary = scenario.status.next_actions.find((a) => a.primary);
	const item = {
		...primary,
		work_item_id: "next-review",
		project_id: PROJECT_ID,
		project_name: scenario.project.name,
		project_revision: scenario.project.current_revision,
		kind: ["approve", "review"].includes(primary.action) ? "review" : "action",
		status: "attention_required",
		current_snapshot_id: "current",
		updated_at: scenario.project.updated_at,
	};
	return {
		continue_working: item,
		projects: [{ ...scenario.project, project_revision: scenario.project.current_revision, current_stage: primary.stage }],
		work_items: [item],
		recent_runs: [],
		recent_reports: [],
	};
}

for (const entry of ["overview", "home", "reviews"]) {
	test(`${entry} opens the action's workbench without executing it`, async ({ page }) => {
		const scenario = reviewScenario();
		await installApi(page, scenario);
		await page.route("**/workspace/summary?*", (route) => route.fulfill({ json: workspaceForScenario(scenario) }));
		await seedAuthenticatedSession(page);
		const mutations = [];
		page.on("request", (request) => {
			if (["POST", "PATCH", "DELETE"].includes(request.method()) && /\/(projects|testcases|automation)\//.test(request.url()))
				mutations.push(request.url());
		});
		for (const [action, stage, destination, label] of [
			["approve", "requirements", "requirements", "Requirements"],
			["refine", "requirements", "requirements", "Requirements"],
			["approve", "use_cases", "use-cases", "Use Cases"],
			["review", "requirements", "requirements", "Requirements"],
			["review", "use_cases", "use-cases", "Use Cases"],
			["analyze_impact", "impact_analysis", "test-cases", "Test Cases"],
			["apply_update", "test_cases", "test-cases", "Test Cases"],
			["generate", "test_cases", "test-cases", "Test Cases"],
			["review", "review", "test-cases", "Test Cases"],
			["automate", "automation", "automation", "Automation"],
			["execute", "execution", "automation", "Automation"],
			["report", "reports", "reports", "Reports"],
		]) {
			scenario.status.next_actions = [recommendation(action, { stage, primary: true, secondary: false })];
			scenario.status.current_stage = stage;
			await page.goto(entry === "overview" ? buildProjectPath(PROJECT_ID) : entry === "home" ? "/" : "/reviews");
			const container =
				entry === "overview"
					? page.getByLabel("Contextual task")
					: entry === "home"
						? page.getByRole("region", { name: "Continue working" })
						: page.getByRole("main");
			const link = container.getByRole(entry === "overview" ? "button" : "link", {
				name: entry === "reviews" ? /^Open .* for / : `Open ${label}`,
				exact: entry !== "reviews",
			});
			await expect(link).toHaveText(`Open ${label}`);
			await link.focus();
			await page.keyboard.press("Enter");
			await expect(page).toHaveURL(new RegExp(`/projects/${PROJECT_ID}/${destination}$`));
			await expect(page.locator("#main-content")).toBeFocused();
		}
		expect(mutations).toEqual([]);
	});
}

test("reviewing requirements advances Overview and Home without reload and keeps optional analysis", async ({ page }) => {
	const scenario = reviewScenario();
	await installApi(page, scenario);
	const summaryRevisions = [];
	await page.route("**/workspace/summary?*", (route) => {
		summaryRevisions.push(scenario.project.current_revision);
		return route.fulfill({ json: workspaceForScenario(scenario) });
	});
	await page.route(`**/projects/${PROJECT_ID}/requirements/reviews`, (route) => {
		const changes = route.request().postDataJSON().reviews;
		for (const change of changes)
			Object.assign(
				scenario.project.current_snapshots.requirements.payload.requirements.find((r) => r.requirement_uid === change.requirement_uid),
				change
			);
		scenario.project.current_revision++;
		scenario.status.project_revision = scenario.project.current_revision;
		const approved = scenario.project.current_snapshots.requirements.payload.requirements.every((r) => r.review_status === "Approved");
		scenario.project.stage_state.requirements.approved = approved;
		if (approved) {
			scenario.status.next_actions = staleActions();
			scenario.status.current_stage = "impact_analysis";
		}
		return route.fulfill({ json: scenario.project });
	});
	await seedAuthenticatedSession(page);
	await page.goto(buildProjectPath(PROJECT_ID));
	const task = page.getByLabel("Contextual task");
	await expect(task.getByRole("heading", { name: "Review Requirements" })).toBeVisible();
	await task.locator("summary").click();
	await expect(task.getByRole("button", { name: "Analyze Impact", exact: true })).toBeEnabled();
	await task.getByRole("button", { name: "Open Requirements", exact: true }).click();
	for (const id of ["REQ-001", "REQ-002"]) {
		await page.getByLabel(`Review status for ${id}`, { exact: true }).selectOption("Approved");
		await expect(page.getByLabel(`Review status for ${id}`, { exact: true })).toBeEnabled();
	}
	await expect.poll(() => summaryRevisions.includes(10)).toBe(true);
	await page.getByRole("link", { name: "Overview", exact: true }).click();
	await expect(task.getByRole("heading", { name: "Analyze Impact" })).toBeVisible();
	await expect(task.getByRole("button", { name: "Open Test Cases" })).toBeEnabled();
	await page.getByRole("link", { name: "Home", exact: true }).click();
	await expect(page.getByRole("region", { name: "Continue working" }).getByRole("link", { name: "Open Test Cases" })).toBeVisible();
	await page.goto(buildProjectPath(PROJECT_ID));
	await page.reload();
	await expect(task.getByRole("heading", { name: "Analyze Impact" })).toBeVisible();
});

function approveScenarioRequirements(scenario) {
	for (const row of scenario.project.current_snapshots.requirements.payload.requirements) row.review_status = "Approved";
	scenario.project.stage_state.requirements.approved = true;
	scenario.project.current_revision = 9;
	scenario.status = { ...scenario.status, project_revision: 9, current_stage: "impact_analysis", next_actions: staleActions() };
}

test("late pre-review guidance cannot replace newer recommendations after leaving and reopening the project", async ({ page }) => {
	const scenario = reviewScenario();
	const oldStatus = structuredClone(scenario.status);
	const oldSummary = workspaceForScenario(scenario);
	await installApi(page, scenario);
	let releaseStatus;
	let releaseSummary;
	const statusGate = new Promise((resolve) => {
		releaseStatus = resolve;
	});
	const summaryGate = new Promise((resolve) => {
		releaseSummary = resolve;
	});
	let heldStatus = false;
	let heldSummary = false;
	await page.route(`**/projects/${PROJECT_ID}/orchestrator/status`, async (route) => {
		if (scenario.project.current_revision === 9 && !heldStatus) {
			heldStatus = true;
			await statusGate;
			return route.fulfill({ json: oldStatus });
		}
		return route.fulfill({ json: scenario.status });
	});
	await page.route("**/workspace/summary?*", async (route) => {
		if (scenario.project.current_revision === 9 && !heldSummary) {
			heldSummary = true;
			await summaryGate;
			return route.fulfill({ json: oldSummary }).catch(() => {});
		}
		return route.fulfill({ json: workspaceForScenario(scenario) });
	});
	await page.route(`**/projects/${PROJECT_ID}/requirements/reviews`, (route) => {
		expect(route.request().postDataJSON().reviews).toHaveLength(2);
		approveScenarioRequirements(scenario);
		return route.fulfill({ json: scenario.project });
	});
	await seedAuthenticatedSession(page);
	await page.goto(buildProjectPath(PROJECT_ID, "requirements"));
	await page.getByRole("button", { name: "Approve non-rejected", exact: true }).click();
	await expect.poll(() => heldStatus && heldSummary).toBe(true);
	await page.getByRole("link", { name: "Home", exact: true }).click();
	await page.getByRole("region", { name: "Continue working" }).getByRole("link", { name: "Open Test Cases", exact: true }).click();
	await expect(page.getByRole("button", { name: "Start analysis", exact: true })).toBeVisible();
	const oldResponse = page.waitForResponse((response) => response.url().endsWith("/orchestrator/status"));
	releaseStatus();
	releaseSummary();
	await oldResponse;
	await page.getByRole("link", { name: "Overview", exact: true }).click();
	await expect(page.getByLabel("Contextual task").getByRole("heading", { name: "Analyze Impact" })).toBeVisible();
	await expect(page.getByRole("heading", { name: "Review Requirements", exact: true })).toHaveCount(0);
});

test("older revision responses fail visibly while committed requirement approvals survive", async ({ page }) => {
	const scenario = reviewScenario();
	const oldStatus = structuredClone(scenario.status);
	const oldSummary = workspaceForScenario(scenario);
	let stale = true;
	await installApi(page, scenario);
	await page.route(`**/projects/${PROJECT_ID}/orchestrator/status`, (route) =>
		route.fulfill({ json: stale ? oldStatus : scenario.status })
	);
	await page.route("**/workspace/summary?*", (route) => route.fulfill({ json: stale ? oldSummary : workspaceForScenario(scenario) }));
	await page.route(`**/projects/${PROJECT_ID}/requirements/reviews`, (route) => {
		approveScenarioRequirements(scenario);
		return route.fulfill({ json: scenario.project });
	});
	await seedAuthenticatedSession(page);
	await page.goto(buildProjectPath(PROJECT_ID, "requirements"));
	await page.getByRole("button", { name: "Approve non-rejected", exact: true }).click();
	await expect(page.getByLabel("Review status for REQ-001", { exact: true })).toHaveValue("Approved");
	await page.getByRole("link", { name: "Overview", exact: true }).click();
	await expect(
		page.getByText("Workflow guidance is out of date. Reload the latest project before continuing.", { exact: true })
	).toBeVisible();
	await expect(page.getByLabel("Contextual task").getByRole("button", { name: "Open Requirements" })).toHaveCount(0);
	stale = false;
	await page.getByRole("link", { name: "Home", exact: true }).click();
	await expect(page.getByRole("region", { name: "Continue working" }).getByRole("link", { name: "Open Test Cases" })).toBeVisible();
});
