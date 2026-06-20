import { expect, test } from "@playwright/test";

import { seedAuthenticatedSession } from "./support/auth.js";

function jsonResponse(route, payload, status = 200) {
	return route.fulfill({
		status,
		contentType: "application/json",
		body: JSON.stringify(payload),
	});
}

function testCase(id) {
	return {
		id,
		title: `${id} checkout coverage`,
		description: `Coverage for ${id}`,
		priority: "High",
		type: "Regression",
		status: "Ready",
		preconditions: "A signed-in user exists.",
		steps: [{ step: 1, action: `Exercise ${id}`, expected: "Behavior is correct", test_data: null }],
		expected_result: "The behavior satisfies the requirement.",
		automation_status: "Automated",
		linked_requirement_ids: ["REQ-001"],
		scenario_refs: ["REQ-001-SCN-01"],
	};
}

function projectDetail(executionRuns = []) {
	return {
		project_id: "project-1",
		name: "Environment QA",
		description: null,
		status: "active",
		owner_user_id: "playwright-e2e-user",
		current_revision: 4 + executionRuns.length,
		created_at: "2026-06-12T00:00:00Z",
		updated_at: "2026-06-12T00:00:00Z",
		stage_state: {
			requirements: { current_snapshot_id: "snap-req-v1", version: 1, approved: true, stale: false, metadata: {} },
			test_cases: { current_snapshot_id: "snap-test-v1", version: 1, approved: true, stale: false, metadata: { test_case_count: 1 } },
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
				project_id: "project-1",
				stage: "requirements",
				version: 1,
				project_revision: 1,
				operation: "requirements.parse",
				approved: true,
				payload: { requirements: [{ id: "REQ-001", text: "REQ-001 checkout works", review_status: "Approved" }] },
				metadata: {},
				created_at: "2026-06-12T00:00:00Z",
			},
			test_cases: {
				snapshot_id: "snap-test-v1",
				project_id: "project-1",
				stage: "test_cases",
				version: 1,
				project_revision: 4,
				operation: "testcases.generate",
				approved: true,
				payload: {
					test_cases: [testCase("TC-001")],
					review: { approved: true, score: 100, threshold: 0, summary: "Approved.", blocking_issues: [] },
				},
				metadata: { test_case_count: 1 },
				created_at: "2026-06-12T00:00:00Z",
			},
		},
		timeline: executionRuns.map((run) => ({
			event_id: `event-${run.target_environment}`,
			project_id: "project-1",
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

function runRecord(environment, status, index) {
	const failed = status === "failed" ? 1 : 0;
	const passed = status === "passed" ? 1 : 0;
	return {
		run_record_id: `record-${environment}`,
		project_id: "project-1",
		run_id: `run-${environment}`,
		target_environment: environment,
		target_base_url: `https://${environment}.example.test/app`,
		project_revision: 4 + index,
		test_case_count: 1,
		status,
		summary: { passed, failed, invalid: 0, skipped: 0 },
		snapshot_id: `snap-exec-${environment}`,
		source_snapshot_id: "snap-test-v1",
		selected_test_case_ids: ["TC-001"],
		request_id: `req-${environment}`,
		created_at: `2026-06-12T00:0${index}:00Z`,
	};
}

function statusPayload() {
	return {
		project_id: "project-1",
		project_revision: 4,
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
		generated_at: "2026-06-12T00:00:00Z",
	};
}

test.describe("Multi-environment execution", () => {
	test("named environment runs are preserved separately in project history", async ({ page }) => {
		const executionRuns = [];
		let currentProject = projectDetail(executionRuns);

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
		await page.route("**/projects/project-1/orchestrator/status", async (route) => jsonResponse(route, statusPayload()));
		await page.route("**/projects/project-1/orchestrator/runs", async (route) =>
			jsonResponse(route, { runs: [], events: [], checkpoints: [] })
		);
		await page.route("**/projects/project-1", async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname !== "/projects/project-1") {
				return route.fallback();
			}
			return jsonResponse(route, currentProject);
		});
		await page.route("**/projects", async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname !== "/projects") {
				return route.fallback();
			}
			return jsonResponse(route, { projects: [projectSummary(currentProject)] });
		});
		await page.route("**/automation/execution/preview", async (route) => {
			const body = JSON.parse(route.request().postData() || "{}");
			return jsonResponse(route, {
				executable: [
					{
						id: `candidate-${body.target_environment}`,
						source_test_case_id: "TC-001",
						title: "TC-001 checkout coverage",
						status: "executable",
						spec: { steps: [{ action: "Open checkout" }] },
						traceability_ids: ["REQ-001"],
					},
				],
				manual: [],
				unsupported: [],
				invalid: [],
				warnings: [],
				summary: { executable: 1, manual: 0, unsupported: 0, invalid: 0 },
			});
		});
		await page.route("**/automation/execution/run", async (route) => {
			const body = JSON.parse(route.request().postData() || "{}");
			const environment = body.target_environment;
			const status = environment === "staging" ? "failed" : "passed";
			const record = runRecord(environment, status, executionRuns.length + 1);
			executionRuns.unshift(record);
			currentProject = projectDetail(executionRuns);
			return jsonResponse(route, {
				status,
				run_id: record.run_id,
				results: [{ id: `result-${environment}`, source_test_case_id: "TC-001", title: "TC-001 checkout coverage", status }],
				preview: {
					executable: [],
					manual: [],
					unsupported: [],
					invalid: [],
					warnings: [],
					summary: { executable: 0, manual: 0, unsupported: 0, invalid: 0 },
				},
				warnings: [],
				summary: record.summary,
			});
		});

		await seedAuthenticatedSession(page);
		await page.goto("/");
		await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible({ timeout: 30_000 });
		await page.getByLabel("Open QA project").selectOption("project-1");
		await expect(page.getByLabel("Workflow workspace").getByText(/Environment QA · revision 4/)).toBeVisible();

		await page
			.getByRole("navigation", { name: "Workflow navigation" })
			.getByRole("button", { name: /^Automation,/i })
			.click();
		await page.getByPlaceholder("staging, dev, customer-a").fill("staging");
		await page.getByPlaceholder("Use backend default").fill("https://staging.example.test/app");
		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		await expect(page.getByRole("button", { name: /^Run 1 Candidate$/ })).toBeEnabled();
		await page.getByRole("button", { name: /^Run 1 Candidate$/ }).click();
		await expect(page.getByText(/Execution failed: 0 passed, 1 failed/i)).toBeVisible();
		const stagingRun = page.locator(".project-run-row", { hasText: "staging" });
		await expect(stagingRun).toBeVisible();
		await expect(stagingRun).toContainText("failed");

		await page.getByPlaceholder("staging, dev, customer-a").fill("production-like");
		await page.getByPlaceholder("Use backend default").fill("https://production-like.example.test/app");
		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		await expect(page.getByRole("button", { name: /^Run 1 Candidate$/ })).toBeEnabled();
		await page.getByRole("button", { name: /^Run 1 Candidate$/ }).click();
		await expect(page.getByText(/Execution passed: 1 passed, 0 failed/i)).toBeVisible();
		const productionLikeRun = page.locator(".project-run-row", { hasText: "production-like" });
		await expect(productionLikeRun).toBeVisible();
		await expect(productionLikeRun).toContainText("passed");
		await expect(stagingRun).toBeVisible();
		await expect(stagingRun).toContainText("0 passed / 1 failed");
		await expect(productionLikeRun).toContainText("1 passed / 0 failed");
	});
});
