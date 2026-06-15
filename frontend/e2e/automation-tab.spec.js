import { expect, test } from "@playwright/test";

import { seedAuthenticatedSession } from "./support/auth.js";

const PROJECT_ID = "project-automation";
const PROJECT_NAME = "Automation QA";

function jsonResponse(route, payload, status = 200) {
	return route.fulfill({
		status,
		contentType: "application/json",
		body: JSON.stringify(payload),
	});
}

function testCase(id, title, automationStatus = "Automated") {
	return {
		id,
		title,
		description: `Coverage for ${title}.`,
		priority: "High",
		type: "Regression",
		status: "Ready",
		preconditions: "A signed-in shopper can open checkout.",
		steps: [{ step: 1, action: `Exercise ${title}`, expected: "The expected checkout behavior is visible.", test_data: null }],
		expected_result: "The checkout behavior satisfies the approved requirement.",
		automation_status: automationStatus,
		linked_requirement_ids: ["REQ-001"],
		scenario_refs: ["REQ-001-SCN-01"],
	};
}

const testCases = [
	testCase("TC-001", "Checkout happy path"),
	testCase("TC-002", "Checkout declined payment"),
	testCase("TC-003", "Manual reconciliation", "Manual"),
	testCase("TC-004", "Unsupported browser dialog", "To Be Automated"),
	testCase("TC-005", "Invalid missing selector", "To Be Automated"),
];

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

function projectDetail({ executionRuns = [] } = {}) {
	return {
		project_id: PROJECT_ID,
		name: PROJECT_NAME,
		description: null,
		status: "active",
		owner_user_id: "playwright-e2e-user",
		current_revision: 7 + executionRuns.length,
		created_at: "2026-06-15T08:00:00Z",
		updated_at: "2026-06-15T08:00:00Z",
		stage_state: {
			requirements: { current_snapshot_id: "snap-req-v1", version: 1, approved: true, stale: false, metadata: {} },
			test_cases: {
				current_snapshot_id: "snap-test-v1",
				version: 1,
				approved: true,
				stale: false,
				metadata: { test_case_count: testCases.length },
			},
			automation: { current_snapshot_id: null, version: 0, approved: false, stale: false, metadata: {} },
			execution: executionRuns.length
				? {
						current_snapshot_id: executionRuns[0].snapshot_id,
						version: executionRuns.length,
						approved: executionRuns[0].status === "passed",
						stale: false,
						metadata: { status: executionRuns[0].status, target_environment: executionRuns[0].target_environment },
					}
				: undefined,
		},
		current_snapshots: {
			requirements: {
				snapshot_id: "snap-req-v1",
				project_id: PROJECT_ID,
				stage: "requirements",
				version: 1,
				project_revision: 1,
				operation: "requirements.parse",
				approved: true,
				payload: { requirements: [{ id: "REQ-001", text: "REQ-001 checkout must be validated.", review_status: "Approved" }] },
				metadata: {},
				created_at: "2026-06-15T08:00:00Z",
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
					test_cases: testCases,
					review: { approved: true, score: 100, threshold: 90, summary: "Approved.", blocking_issues: [] },
				},
				metadata: { test_case_count: testCases.length },
				created_at: "2026-06-15T08:00:00Z",
			},
		},
		timeline: executionRuns.map((run) => ({
			event_id: `event-${run.run_id}`,
			project_id: PROJECT_ID,
			event_type: "execution.run_recorded",
			stage: "execution",
			summary: `${run.target_environment} execution ${run.status}`,
			project_revision: run.project_revision,
			snapshot_id: run.snapshot_id,
			run_id: run.run_id,
			metadata: { summary: run.summary },
			occurred_at: run.created_at,
		})),
		execution_runs: executionRuns,
	};
}

function statusPayload(project) {
	return {
		project_id: PROJECT_ID,
		project_revision: project.current_revision,
		current_stage: "automation",
		stages: {
			requirements: { stage: "requirements", status: "completed", version: 1, approved: true, stale: false, summary: {}, blockers: [] },
			test_cases: { stage: "test_cases", status: "completed", version: 1, approved: true, stale: false, summary: {}, blockers: [] },
			automation: { stage: "automation", status: "ready", version: 0, approved: false, stale: false, summary: {}, blockers: [] },
		},
		next_actions: [
			{
				action: "automate",
				label: "Preview Automation",
				stage: "automation",
				enabled: true,
				primary: true,
				secondary: false,
				reason: "Approved test cases are ready for automation preview.",
				blockers: [],
			},
		],
		blockers: [],
		has_baseline_test_suite: true,
		upstream_changed: false,
		changed_upstream_stages: [],
		generated_at: "2026-06-15T08:00:00Z",
	};
}

function previewResponse() {
	return {
		executable: [
			{
				id: "candidate-1",
				source_test_case_id: "TC-001",
				title: "Checkout happy path",
				status: "executable",
				spec: { steps: [{ action: "Open checkout" }, { action: "Confirm order" }] },
				traceability_ids: ["REQ-001"],
			},
			{
				id: "candidate-2",
				source_test_case_id: "TC-002",
				title: "Checkout declined payment",
				status: "executable",
				spec: { steps: [{ action: "Open checkout" }, { action: "Submit declined card" }] },
				traceability_ids: ["REQ-001"],
			},
		],
		manual: [
			{
				id: "candidate-3",
				source_test_case_id: "TC-003",
				title: "Manual reconciliation",
				status: "manual",
				review_reasons: ["Requires finance back-office confirmation."],
				traceability_ids: ["REQ-001"],
			},
		],
		unsupported: [
			{
				id: "candidate-4",
				source_test_case_id: "TC-004",
				title: "Unsupported browser dialog",
				status: "unsupported",
				unsupported_steps: [{ step: 2, reason_code: "browser.dialog", suggested_next_action: "Keep this step manual." }],
				traceability_ids: ["REQ-001"],
			},
		],
		invalid: [
			{
				id: "candidate-5",
				source_test_case_id: "TC-005",
				title: "Invalid missing selector",
				status: "invalid",
				unsupported_steps: [{ step: 1, reason_code: "missing.selector", suggested_next_action: "Add selector evidence." }],
				traceability_ids: ["REQ-001"],
			},
		],
		warnings: ["2 candidates need manual QA review before execution."],
		summary: { executable: 2, manual: 1, unsupported: 1, invalid: 1 },
	};
}

function runRecord() {
	return {
		run_record_id: "record-automation-1",
		project_id: PROJECT_ID,
		run_id: "run-automation-1",
		target_environment: "staging",
		target_base_url: "https://staging.example.test/app",
		project_revision: 8,
		test_case_count: 2,
		status: "failed",
		summary: { passed: 1, failed: 1, invalid: 0, skipped: 0 },
		snapshot_id: "snap-exec-v1",
		source_snapshot_id: "snap-test-v1",
		selected_test_case_ids: ["TC-001", "TC-002"],
		request_id: "req-automation-1",
		created_at: "2026-06-15T08:05:00Z",
	};
}

async function routeShellData(page, { projects = [] } = {}) {
	await page.route("**/auth/me", async (route) =>
		jsonResponse(route, {
			sub: "playwright-e2e-user",
			email: "playwright-e2e@example.com",
			name: "Playwright E2E",
			picture: null,
		})
	);
	await page.route("**/reports/usage/me", async (route) => jsonResponse(route, { groups: [] }));
	await page.route("**/entitlements/me", async (route) =>
		jsonResponse(route, {
			account: { plan_tier: "premium", support_contact_email: "hello@spica-digital.eu" },
			requirements: { remaining: 500, exhausted: false },
			test_cases: { remaining: 500, exhausted: false },
			wallet: { balance_units: 5000, balance_token_display: "5000" },
			shadow_mode: false,
		})
	);
	await page.route("**/integrations/**", async (route) => {
		const url = new URL(route.request().url());
		if (!url.pathname.startsWith("/integrations/")) {
			return route.fallback();
		}
		return jsonResponse(route, { connected: false, connection: null });
	});
	await page.route("**/projects", async (route) => {
		const url = new URL(route.request().url());
		if (url.pathname !== "/projects") {
			return route.fallback();
		}
		return jsonResponse(route, { projects });
	});
}

test.describe("Automation tab", () => {
	test("keeps preview and run actions disabled without generated test cases", async ({ page }) => {
		await routeShellData(page);
		await seedAuthenticatedSession(page);

		await page.goto("/");
		await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible({ timeout: 30_000 });
		await page.locator(".tab", { hasText: "Automation" }).click();

		await expect(page.getByRole("heading", { name: /^Automation$/ })).toBeVisible();
		await expect(page.getByText("Generate test cases to preview automation readiness.")).toBeVisible();
		await expect(page.getByRole("button", { name: /^Preview Execution$/ })).toBeDisabled();
		await expect(page.getByRole("button", { name: /^Run 0 Candidates$/ })).toBeDisabled();
		await expect(page.locator(".panel-nav").getByRole("button", { name: /^Next$/ })).toBeDisabled();
	});

	test("previews mixed automation candidates and renders execution results", async ({ page }) => {
		let currentProject = projectDetail();
		let previewPayload = null;
		let runPayload = null;
		const preview = previewResponse();
		const executionRun = runRecord();

		await routeShellData(page, { projects: [projectSummary(currentProject)] });
		await page.route(`**/projects/${PROJECT_ID}/orchestrator/status`, async (route) => jsonResponse(route, statusPayload(currentProject)));
		await page.route(`**/projects/${PROJECT_ID}/orchestrator/runs`, async (route) =>
			jsonResponse(route, { runs: [], events: [], checkpoints: [] })
		);
		await page.route(`**/projects/${PROJECT_ID}`, async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname !== `/projects/${PROJECT_ID}`) {
				return route.fallback();
			}
			return jsonResponse(route, currentProject);
		});
		await page.route("**/automation/execution/preview", async (route) => {
			previewPayload = route.request().postDataJSON();
			return jsonResponse(route, preview);
		});
		await page.route("**/automation/execution/run", async (route) => {
			runPayload = route.request().postDataJSON();
			currentProject = projectDetail({ executionRuns: [executionRun] });
			return jsonResponse(route, {
				status: "failed",
				run_id: "run-automation-1",
				results: [
					{
						id: "result-1",
						source_test_case_id: "TC-001",
						title: "Checkout happy path",
						status: "passed",
						generated_spec_path: "generated/TC-001.spec.js",
						artifacts_dir: "artifacts/TC-001",
					},
					{
						id: "result-2",
						source_test_case_id: "TC-002",
						title: "Checkout declined payment",
						status: "failed",
						generated_spec_path: "generated/TC-002.spec.js",
						artifacts_dir: "artifacts/TC-002",
					},
				],
				preview,
				warnings: [],
				summary: executionRun.summary,
				artifacts_root: "artifacts/run-automation-1",
			});
		});
		await seedAuthenticatedSession(page);

		await page.goto("/");
		await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible({ timeout: 30_000 });
		await page.getByLabel("Open QA project").selectOption(PROJECT_ID);
		await expect(page.getByText(`${PROJECT_NAME} · revision 7`)).toBeVisible();

		await page.locator(".tab", { hasText: "Automation" }).click();
		await page.getByPlaceholder("staging, dev, customer-a").fill("staging");
		await page.getByPlaceholder("Use backend default").fill("https://staging.example.test/app");
		await expect(page.getByRole("button", { name: /^Preview Execution$/ })).toBeEnabled();
		await expect(page.getByRole("button", { name: /^Run 0 Candidates$/ })).toBeDisabled();

		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		await expect(page.getByText(/Execution preview ready: 2 executable, 1 manual, 1 unsupported/i)).toBeVisible();
		expect(previewPayload).toMatchObject({
			target_environment: "staging",
			target_base_url: "https://staging.example.test/app",
			project_id: PROJECT_ID,
			base_project_revision: 7,
		});
		expect(previewPayload.test_cases.map((item) => item.id)).toEqual(["TC-001", "TC-002", "TC-003", "TC-004", "TC-005"]);

		await expect(page.locator(".workflow-diagnostics-pill", { hasText: /^Executable 2$/ })).toBeVisible();
		await expect(page.locator(".workflow-diagnostics-pill", { hasText: /^Manual 1$/ })).toBeVisible();
		await expect(page.locator(".workflow-diagnostics-pill", { hasText: /^Unsupported 1$/ })).toBeVisible();
		await expect(page.locator(".workflow-diagnostics-pill", { hasText: /^Invalid 1$/ })).toBeVisible();
		await expect(page.getByText("Checkout happy path")).toBeVisible();
		await expect(page.getByText("Checkout declined payment")).toBeVisible();
		await expect(page.getByText(/TC-003 - Manual reconciliation: Requires finance back-office confirmation/i)).toBeVisible();
		await expect(page.getByText(/Step 2: browser\.dialog\. Keep this step manual\./i)).toBeVisible();
		await expect(page.getByText(/Step 1: missing\.selector\. Add selector evidence\./i)).toBeVisible();
		await expect(page.getByText("2 candidates need manual QA review before execution.")).toBeVisible();

		await expect(page.getByRole("button", { name: /^Run 2 Candidates$/ })).toBeEnabled();
		await page.getByRole("button", { name: /^Run 2 Candidates$/ }).click();
		await expect(page.getByText(/Execution failed: 1 passed, 1 failed, 0 invalid/i)).toBeVisible();
		expect(runPayload).toMatchObject({
			target_environment: "staging",
			target_base_url: "https://staging.example.test/app",
			project_id: PROJECT_ID,
			base_project_revision: 7,
			selected_test_case_ids: ["TC-001", "TC-002"],
		});

		const executionResults = page.locator(".result-section", { has: page.getByRole("heading", { name: "Execution Results" }) });
		await expect(executionResults).toContainText("run-automation-1");
		await expect(executionResults.locator(".workflow-diagnostics-pill", { hasText: /^Passed 1$/ })).toBeVisible();
		await expect(executionResults.locator(".workflow-diagnostics-pill", { hasText: /^Failed 1$/ })).toBeVisible();
		await expect(executionResults.locator("tbody tr")).toHaveCount(2);
		await expect(executionResults.locator("tbody tr", { hasText: "TC-001" })).toContainText("passed");
		await expect(executionResults.locator("tbody tr", { hasText: "TC-002" })).toContainText("failed");
		await expect(executionResults).toContainText("artifacts/run-automation-1");
	});
});
