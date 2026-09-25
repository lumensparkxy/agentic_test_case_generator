import { expect, test } from "@playwright/test";

import { buildTestCaseExportFilename } from "../src/services/exportFilename.js";
import { buildProjectPath } from "../src/app/workflowRoutes.js";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectNoSeriousOrCriticalViolations } from "./support/accessibility.js";

const PROJECT_ID = "project-export-gate";
const PROJECT = {
	project_id: PROJECT_ID,
	name: 'Export / Gate: "Zürich" QA?',
	description: null,
	status: "active",
	owner_user_id: "playwright-e2e-user",
	current_revision: 0,
	created_at: "2026-07-17T08:00:00Z",
	updated_at: "2026-07-17T08:00:00Z",
	stage_state: { requirements: { current_snapshot_id: "approved", approved: true, stale: false, metadata: {} } },
	current_snapshots: {
		requirements: {
			snapshot_id: "approved",
			payload: {
				requirements: [
					{
						id: "REQ-001",
						requirement_uid: "export-requirement",
						text: "The system shall allow users to export reports.",
						review_status: "Approved",
					},
				],
				review: { approved: true },
			},
		},
	},
	timeline: [],
	execution_runs: [],
};

function jsonResponse(route, payload, status = 200, headers = {}) {
	return route.fulfill({
		status,
		contentType: "application/json",
		headers,
		body: JSON.stringify(payload),
	});
}

function apiJsonResponse(route, payload, status = 200, headers = {}) {
	if (!["fetch", "xhr"].includes(route.request().resourceType())) {
		return route.fallback();
	}
	return jsonResponse(route, payload, status, headers);
}

function projectSummary() {
	return {
		project_id: PROJECT.project_id,
		name: PROJECT.name,
		description: PROJECT.description,
		status: PROJECT.status,
		owner_user_id: PROJECT.owner_user_id,
		current_revision: PROJECT.current_revision,
		created_at: PROJECT.created_at,
		updated_at: PROJECT.updated_at,
		stage_state: PROJECT.stage_state,
	};
}

function orchestratorStatus() {
	return {
		project_id: PROJECT_ID,
		project_revision: 0,
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

test.describe("Export approval gate", () => {
	test("draft test cases require an explicit override reason before export", { tag: "@p1" }, async ({ page }) => {
		let exportPayload = null;
		let runRequests = 0;

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
		await page.route("**/projects", async (route) => apiJsonResponse(route, { projects: [projectSummary()] }));
		await page.route(`**/projects/${PROJECT_ID}/orchestrator/status`, async (route) => apiJsonResponse(route, orchestratorStatus()));
		await page.route(`**/projects/${PROJECT_ID}/orchestrator/runs`, async (route) =>
			apiJsonResponse(route, { runs: [], events: [], checkpoints: [] })
		);
		await page.route(`**/projects/${PROJECT_ID}`, async (route) => apiJsonResponse(route, PROJECT));
		await page.route(`**/projects/${PROJECT_ID}/requirement-imports`, (route) => jsonResponse(route, []));
		await page.route("**/testcases/generate", async (route) =>
			jsonResponse(route, {
				test_cases: [
					{
						id: "TC-001",
						title: "Draft export report test",
						description: "Verify reports can be exported.",
						priority: "High",
						type: "Functional",
						status: "Draft",
						preconditions: "A report exists.",
						steps: [
							{ step: 1, action: "Open reports", expected: "Reports page is visible", test_data: null },
							{ step: 2, action: "Export the report", expected: "The export is downloaded", test_data: null },
						],
						expected_result: "The report export completes.",
						test_data: null,
						estimated_time: "5 mins",
						automation_status: "Automated",
						component: "Reports",
						tags: ["REQ-001", "scenario:happy-path"],
						generation_source: "model",
					},
					{
						id: "TC-002",
						title: "Deterministic export failure coverage",
						description: "Verify export failures are represented after coverage completion.",
						priority: "Medium",
						type: "Functional",
						status: "Draft",
						preconditions: "A report exists.",
						steps: [
							{ step: 1, action: "Open reports", expected: "Reports page is visible", test_data: null },
							{ step: 2, action: "Simulate an export failure", expected: "The failure is shown", test_data: null },
						],
						expected_result: "The user can understand why export failed.",
						test_data: null,
						estimated_time: "5 mins",
						automation_status: "To Be Automated",
						component: "Reports",
						tags: ["REQ-001", "scenario:negative"],
						generation_source: "deterministic_coverage_completion",
						coverage_completion_reason: "coverage_augmentation",
					},
				],
				review: {
					approved: false,
					score: 72,
					threshold: 90,
					summary: "Needs additional negative coverage.",
					blocking_issues: ["Missing negative export failure coverage."],
				},
				approved: false,
				coverage_plan: [],
				requirement_analysis: [],
				coverage_metrics: {},
				workflow_diagnostics: {
					status: "partial",
					generation_route: "direct_parallel",
					generation_source_counts: { model: 1, deterministic_coverage_completion: 1 },
					completion_source: "coverage_completion",
					missing_requirements_count: 0,
					missing_must_have_scenario_count: 1,
					missing_optional_scenario_count: 1,
					deterministic_must_have_additions: 1,
					deterministic_optional_additions: 1,
					deterministic_total_additions: 2,
					shard_count: 2,
					worker_count: 2,
					used_fallback: false,
					warnings: [
						"Model output needed deterministic coverage completion because 1 must-have scenario and 1 optional/planned scenario remained uncovered; added 1 must-have deterministic case and 1 optional deterministic case (2 total deterministic coverage cases).",
					],
					parser_recoveries: ["TestCaseGeneratorAgent: recovered 1 complete test_cases entry from truncated JSON"],
					parser_failures: [],
				},
				iteration_history: [],
			})
		);
		await page.route("**/automation/execution/preview", async (route) =>
			jsonResponse(route, {
				executable: [
					{
						id: "tc_001",
						source_test_case_id: "TC-001",
						title: "Draft export report test",
						status: "executable",
						spec: {
							schemaVersion: "1.0",
							id: "tc_001",
							title: "Draft export report test",
							steps: ['Given I open "https://playwright.dev"', 'Then "Playwright" should be visible'],
						},
						metadata: {},
						unsupported_steps: [],
						review_reasons: [],
						traceability_ids: ["REQ-001"],
					},
				],
				manual: [],
				unsupported: [],
				invalid: [],
				warnings: [],
				summary: { executable: 1, manual: 0, unsupported: 0, invalid: 0 },
			})
		);
		await page.route("**/automation/execution/run", (route) => {
			runRequests += 1;
			return jsonResponse(route, { detail: "Unapproved suite must not execute" }, 409);
		});

		await page.route("**/export/{json,csv,excel}", async (route) => {
			exportPayload = route.request().postDataJSON();
			return jsonResponse(route, { test_cases: exportPayload.test_cases || [] }, 200, {
				"content-disposition": "attachment; filename=test_cases.json",
			});
		});

		await seedAuthenticatedSession(page);
		const requirementsPath = buildProjectPath(PROJECT_ID, "requirements");
		const testCasesPath = buildProjectPath(PROJECT_ID, "test-cases");
		const automationPath = buildProjectPath(PROJECT_ID, "automation");
		const reportsPath = buildProjectPath(PROJECT_ID, "reports");
		await page.goto(requirementsPath);
		await expect(page).toHaveURL(requirementsPath);
		await expect(page.getByRole("button", { name: /open account menu/i })).toBeVisible({ timeout: 30_000 });

		await expect(page.locator(".requirement-review-table tbody tr")).toHaveCount(1);

		await page.getByRole("button", { name: /^Next$/ }).click();
		await page.getByRole("button", { name: /^Next$/ }).click();
		await page.getByRole("button", { name: /^Next$/ }).click();
		await expect(page).toHaveURL(testCasesPath);
		await page.getByRole("button", { name: /generate from \d+ approved/i }).click();
		await page.getByRole("tab", { name: /^Improve tests/ }).click();
		await expect(page.getByText(/Needs additional negative coverage/i)).toBeVisible();
		await page.getByRole("tab", { name: /test cases/i }).click();
		await expect(page.getByRole("region", { name: "Generated test cases", exact: true })).toBeVisible();
		await expect(page.getByRole("region", { name: "Selected test case", exact: true })).toBeVisible();
		await page.getByRole("tab", { name: /traceability/i }).click();
		await expect(page.getByRole("region", { name: "Requirement traceability table" })).toHaveAttribute("tabindex", "0");
		await expect(page.getByRole("tab", { name: /diagnostics|generation summary/i })).toHaveCount(0);

		await page.getByRole("button", { name: /^Next$/ }).click();
		await expect(page).toHaveURL(automationPath);
		await expect(page.getByRole("heading", { name: /^Automation$/i })).toBeVisible();
		await page.getByRole("button", { name: /preview execution/i }).click();
		await expect(page.getByRole("button", { name: /run 1 candidate/i })).toBeDisabled();
		expect(runRequests).toBe(0);
		await page.getByRole("button", { name: /^Next$/ }).click();
		await expect(page).toHaveURL(reportsPath);
		await expect(page.getByText("No Playwright execution report has been recorded yet.", { exact: true })).toBeVisible();

		const jsonButton = page.getByRole("button", { name: /json/i }).first();
		await expect(page.getByText(/Export locked by review gate/i)).toBeVisible();
		await expect(jsonButton).toBeDisabled();

		await page.getByLabel(/export draft anyway/i).check();
		await expect(jsonButton).toBeDisabled();
		await page.getByPlaceholder(/Reason for exporting this draft/i).fill("Stakeholder review requested before final QA approval.");
		await expect(jsonButton).toBeEnabled();

		await page.clock.setFixedTime(new Date("2026-09-24T14:30:52.123Z"));
		for (const [label, extension] of [
			["JSON", "json"],
			["CSV", "csv"],
			["Excel Formatted", "xlsx"],
		]) {
			const button = page.getByRole("button", { name: new RegExp(label, "i") }).first();
			const download = await Promise.all([page.waitForEvent("download"), button.click()]).then(([item]) => item);
			expect(download.suggestedFilename()).toBe(`export-gate-zürich-qa_test-cases_2026-09-24T14-30-52-123Z.${extension}`);
			await expect(button).toBeEnabled();
		}
		expect(exportPayload.draft_override_requested).toBe(true);
		expect(exportPayload.draft_override_reason).toContain("Stakeholder review requested");
		expect(exportPayload.approved).toBe(false);
	});
});

test("export filenames handle missing and long international project names", () => {
	const exportedAt = new Date("2026-09-24T14:30:52.123Z");
	expect(buildTestCaseExportFilename({ name: " /:*? ", project_id: "project-123" }, "excel", exportedAt)).toBe(
		"project-123_test-cases_2026-09-24T14-30-52-123Z.xlsx"
	);
	expect(buildTestCaseExportFilename(null, "csv", exportedAt)).toBe("project_test-cases_2026-09-24T14-30-52-123Z.csv");
	const filename = buildTestCaseExportFilename({ name: "測試專案".repeat(100) }, "json", exportedAt);
	expect(filename).toMatch(/^測試專案.*_test-cases_2026-09-24T14-30-52-123Z\.json$/);
	expect(Buffer.byteLength(filename, "utf8")).toBeLessThan(255);
	expect(buildTestCaseExportFilename({ name: "QA" }, "json", new Date("2026-09-24T14:30:52.124Z"))).not.toBe(
		buildTestCaseExportFilename({ name: "QA" }, "json", exportedAt)
	);
});

for (const reviewState of ["approved", "unavailable", "incomplete", "server-rejected"]) {
	test(`saved ${reviewState} suite exports with snapshot identity`, { tag: [] }, async ({ page }, testInfo) => {
		const cases = [
			{
				id: "TC-SAVED",
				title: "Observe checkout",
				status: "Ready",
				steps: [{ step: 1, action: "Open checkout", expected: "Checkout is visible" }],
			},
		];
		const approved = ["approved", "server-rejected"].includes(reviewState);
		const project = structuredClone(PROJECT);
		project.current_revision = 7;
		project.stage_state.test_cases = { current_snapshot_id: "tests-saved", approved: reviewState !== "unavailable", stale: false };
		project.current_snapshots.test_cases = {
			snapshot_id: "tests-saved",
			stage: "test_cases",
			version: 2,
			approved,
			payload: {
				test_cases: cases,
				generation_tasks: reviewState === "incomplete" ? [{ reason: "Missing cancellation" }] : [],
				...(reviewState !== "unavailable" ? { review: { approved: true, score: 100, threshold: 90 } } : {}),
			},
		};
		let exported;
		await page.route("**/*", async (route) => {
			if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.fallback();
			const pathname = new URL(route.request().url()).pathname;
			if (pathname === "/auth/me") return jsonResponse(route, { sub: "playwright-e2e-user", name: "QA" });
			if (pathname === "/projects") return jsonResponse(route, { projects: [project] });
			if (pathname === `/projects/${PROJECT_ID}`) return jsonResponse(route, project);
			if (pathname.endsWith("/orchestrator/status")) return jsonResponse(route, { ...orchestratorStatus(), project_revision: 7 });
			if (pathname === "/export/json") {
				exported = route.request().postDataJSON();
				if (reviewState === "server-rejected" && !exported.draft_override_requested) {
					return jsonResponse(route, { detail: { code: "draft_override_required", message: "Saved coverage is incomplete." } }, 422);
				}
				return jsonResponse(
					route,
					{
						export_format: "test_cases_v1",
						total_count: 1,
						test_cases: cases,
						audit_metadata: { schema_version: 1, snapshot_id: exported.source_snapshot_id, is_draft: !approved },
					},
					200,
					{ "content-disposition": "attachment; filename=test_cases.json" }
				);
			}
			return jsonResponse(route, {});
		});
		await seedAuthenticatedSession(page);
		await page.goto(buildProjectPath(PROJECT_ID, "reports"));
		const downloadButton = page.getByRole("button", { name: /JSON/ });
		if (reviewState === "server-rejected") {
			await downloadButton.click();
			await expect(page.getByText("Export failed: Saved coverage is incomplete.", { exact: false }).first()).toBeVisible();
			for (const width of [1488, 390]) {
				await page.setViewportSize({ width, height: 900 });
				await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
				await expectNoSeriousOrCriticalViolations(page, `Export rejection at ${width}px`);
				await page.screenshot({ path: testInfo.outputPath(`export-recovery-${width}.png`), fullPage: true });
			}
		}
		if (!approved || reviewState === "server-rejected") {
			await expect(downloadButton).toBeDisabled();
			await page.getByLabel(/export draft anyway/i).check();
			await expect(downloadButton).toBeDisabled();
			await page.getByPlaceholder(/Reason for exporting this draft/i).fill("Independent QA review requested.");
		}
		await expect(downloadButton).toBeEnabled();
		const downloadPromise = page.waitForEvent("download");
		await downloadButton.click();
		const download = await downloadPromise;
		const stream = await download.createReadStream();
		let content = "";
		for await (const chunk of stream) content += chunk.toString();
		const document = JSON.parse(content);
		expect(document.audit_metadata.snapshot_id).toBe("tests-saved");
		expect(document.test_cases[0].id).toBe("TC-SAVED");
		expect(exported.base_project_revision).toBe(7);
		expect(exported.draft_override_requested).toBe(!approved || reviewState === "server-rejected");
	});
}
