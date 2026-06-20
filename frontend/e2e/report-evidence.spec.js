import { expect, test } from "@playwright/test";

import { seedAuthenticatedSession } from "./support/auth.js";

function jsonResponse(route, payload, status = 200) {
	return route.fulfill({
		status,
		contentType: "application/json",
		body: JSON.stringify(payload),
	});
}

function testCase() {
	return {
		id: "TC-001",
		title: "Checkout evidence coverage",
		description: "Coverage for checkout reporting.",
		priority: "High",
		type: "Regression",
		status: "Ready",
		preconditions: "A signed-in user exists.",
		steps: [{ step: 1, action: "Open checkout", expected: "Checkout is visible", test_data: null }],
		expected_result: "Checkout evidence is ready.",
		automation_status: "Automated",
		linked_requirement_ids: ["REQ-001"],
		scenario_refs: ["REQ-001-SCN-01"],
	};
}

function projectDetail() {
	return {
		project_id: "project-1",
		name: "Report Evidence QA",
		description: null,
		status: "active",
		owner_user_id: "playwright-e2e-user",
		current_revision: 11,
		created_at: "2026-06-12T00:00:00Z",
		updated_at: "2026-06-12T00:00:00Z",
		stage_state: {
			requirements: { current_snapshot_id: "snap-req-v2", version: 2, approved: true, stale: false, metadata: {} },
			test_cases: { current_snapshot_id: "snap-test-v2", version: 2, approved: true, stale: false, metadata: { test_case_count: 1 } },
			execution: {
				current_snapshot_id: "snap-exec-v1",
				version: 1,
				approved: true,
				stale: false,
				metadata: { status: "passed", target_environment: "staging" },
			},
			reports: {
				current_snapshot_id: "snap-report-v1",
				version: 1,
				approved: true,
				stale: true,
				stale_reason: "test_cases changed after report generation",
				metadata: { format: "json" },
			},
		},
		current_snapshots: {
			requirements: {
				snapshot_id: "snap-req-v2",
				project_id: "project-1",
				stage: "requirements",
				version: 2,
				project_revision: 8,
				operation: "requirements.refine",
				approved: true,
				payload: { requirements: [{ id: "REQ-001", text: "REQ-001 checkout evidence is reportable", review_status: "Approved" }] },
				metadata: {},
				created_at: "2026-06-12T00:00:00Z",
			},
			test_cases: {
				snapshot_id: "snap-test-v2",
				project_id: "project-1",
				stage: "test_cases",
				version: 2,
				project_revision: 10,
				operation: "testcases.generate",
				approved: true,
				payload: {
					test_cases: [testCase()],
					review: { approved: true, score: 100, threshold: 90, summary: "Approved.", blocking_issues: [] },
				},
				metadata: { test_case_count: 1 },
				created_at: "2026-06-12T00:00:00Z",
			},
			execution: {
				snapshot_id: "snap-exec-v1",
				project_id: "project-1",
				stage: "execution",
				version: 1,
				project_revision: 9,
				operation: "automation.execution.run",
				approved: true,
				payload: { run_id: "run-staging", status: "passed", target_environment: "staging" },
				metadata: { status: "passed", run_id: "run-staging", target_environment: "staging" },
				created_at: "2026-06-12T00:00:00Z",
			},
			reports: {
				snapshot_id: "snap-report-v1",
				project_id: "project-1",
				stage: "reports",
				version: 1,
				project_revision: 9,
				operation: "export.json",
				approved: true,
				payload: {
					format: "json",
					evidence: {
						source_snapshot_ids: {
							requirements: "snap-req-v2",
							test_cases: "snap-test-v2",
							execution: "snap-exec-v1",
						},
						execution_run_ids: ["run-staging"],
					},
				},
				metadata: { format: "json" },
				created_at: "2026-06-12T00:00:00Z",
			},
		},
		timeline: [],
		execution_runs: [
			{
				run_record_id: "record-staging",
				project_id: "project-1",
				run_id: "run-staging",
				target_environment: "staging",
				target_base_url: "https://staging.example.test/app",
				project_revision: 9,
				test_case_count: 1,
				status: "passed",
				summary: { passed: 1, failed: 0, invalid: 0, skipped: 0 },
				snapshot_id: "snap-exec-v1",
				source_snapshot_id: "snap-test-v2",
				selected_test_case_ids: ["TC-001"],
				request_id: "req-staging",
				created_at: "2026-06-12T00:01:00Z",
			},
		],
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

function statusPayload() {
	return {
		project_id: "project-1",
		project_revision: 11,
		current_stage: "reports",
		stages: {
			reports: {
				stage: "reports",
				status: "stale",
				current_snapshot_id: "snap-report-v1",
				version: 1,
				approved: true,
				stale: true,
				stale_reason: "test_cases changed after report generation",
				summary: {
					source_snapshot_ids: { test_cases: "snap-test-v2", execution: "snap-exec-v1" },
					execution_run_ids: ["run-staging"],
				},
				blockers: [],
			},
		},
		next_actions: [
			{
				action: "report",
				label: "Regenerate Evidence Report",
				stage: "reports",
				enabled: true,
				primary: true,
				secondary: false,
				reason: "The latest report is stale because upstream project evidence changed.",
				blockers: [],
				agent_kind: "report",
				agent_contract_version: "2026-06-13.v1",
				agent_implementation: "local",
			},
		],
		blockers: [],
		has_baseline_test_suite: true,
		upstream_changed: false,
		changed_upstream_stages: [],
		generated_at: "2026-06-12T00:00:00Z",
	};
}

test.describe("Report evidence", () => {
	test("stale reports show regeneration action and evidence links", async ({ page }) => {
		const project = projectDetail();

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
			return jsonResponse(route, project);
		});
		await page.route("**/projects", async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname !== "/projects") {
				return route.fallback();
			}
			return jsonResponse(route, { projects: [projectSummary(project)] });
		});

		await seedAuthenticatedSession(page);
		await page.goto("/");
		await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible({ timeout: 30_000 });
		await page.getByLabel("Open QA project").selectOption("project-1");

		const cockpit = page.getByLabel("Orchestrator Cockpit");
		await expect(cockpit.getByRole("button", { name: /^Regenerate Evidence Report$/ })).toBeVisible();

		const reportBlock = page.getByLabel("Project information rail").locator(".project-history-block", { hasText: "Latest Report" });
		await expect(reportBlock).toBeVisible();
		await expect(reportBlock).toContainText("Stale");
		await expect(reportBlock).toContainText("snap-report-v1");
		await expect(reportBlock).toContainText("snap-test-v2");
		await expect(reportBlock).toContainText("snap-exec-v1");
		await expect(reportBlock).toContainText("run-staging");
	});
});
