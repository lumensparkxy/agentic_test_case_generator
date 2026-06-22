import { expect, test } from "@playwright/test";

import { sampleRequirementsFile, seedAuthenticatedSession } from "./support/auth.js";

function jsonResponse(route, payload, status = 200, headers = {}) {
	return route.fulfill({
		status,
		contentType: "application/json",
		headers,
		body: JSON.stringify(payload),
	});
}

test.describe("Export approval gate", () => {
	test("draft test cases require an explicit override reason before export", async ({ page }) => {
		let exportPayload = null;

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
		await page.route("**/requirements/parse", async (route) =>
			jsonResponse(route, {
				source_name: "sample-requirements.md",
				raw_text: "The system shall allow users to export reports.",
				requirements: [{ id: "REQ-001", text: "The system shall allow users to export reports.", review_status: "Approved" }],
				review: { approved: true, score: 95, threshold: 85, summary: "Requirements approved.", blocking_issues: [] },
				coverage_metrics: {
					total_requirements: 1,
					unique_requirements: 1,
					duplicate_requirements: 0,
					shall_format_count: 1,
					requirements_per_document: 1,
				},
				workflow_diagnostics: { status: "completed", warnings: [], parser_failures: [] },
				iteration_history: [],
			})
		);
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
		await page.route("**/automation/execution/run", async (route) =>
			jsonResponse(route, {
				status: "passed",
				run_id: "exec_export_report",
				artifacts_root: "/tmp/agentic-tcg/exec_export_report",
				playwright_report_paths: ["/tmp/agentic-tcg/exec_export_report/artifacts/playwright/tc_001/html-report"],
				results: [
					{
						id: "tc_001",
						source_test_case_id: "TC-001",
						title: "Draft export report test",
						status: "passed",
						generated_spec_path: "/tmp/agentic-tcg/exec_export_report/generated/playwright/tc_001.spec.ts",
						artifacts_dir: "/tmp/agentic-tcg/exec_export_report/artifacts/playwright/tc_001",
						report_json_path: "/tmp/agentic-tcg/exec_export_report/artifacts/playwright/tc_001/results.json",
						playwright_report_path: "/tmp/agentic-tcg/exec_export_report/artifacts/playwright/tc_001/html-report",
						returncode: 0,
						stdout: "1 passed",
						stderr: "",
						issues: [],
					},
				],
				preview: {
					executable: [],
					manual: [],
					unsupported: [],
					invalid: [],
					warnings: [],
					summary: { executable: 1, manual: 0, unsupported: 0, invalid: 0 },
				},
				warnings: [],
				summary: { passed: 1, failed: 0, invalid: 0, skipped: 0, unsupported: 0, manual: 0 },
			})
		);
		await page.route("**/export/json", async (route) => {
			exportPayload = route.request().postDataJSON();
			return jsonResponse(route, { test_cases: exportPayload.test_cases || [] }, 200, {
				"content-disposition": "attachment; filename=test_cases.json",
			});
		});

		await seedAuthenticatedSession(page);
		await page.goto("/");
		await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible({ timeout: 30_000 });

		await page.locator('input[type="file"]').setInputFiles(sampleRequirementsFile);
		await page.getByRole("button", { name: /parse requirements/i }).click();
		await expect(page.locator(".requirement-review-table tbody tr")).toHaveCount(1);

		await page.getByRole("button", { name: /^Next$/ }).click();
		await page.getByRole("button", { name: /^Next$/ }).click();
		await page.getByRole("button", { name: /^Next$/ }).click();
		await page.getByRole("button", { name: /generate from \d+ approved/i }).click();
		await expect(page.getByText(/Needs additional negative coverage/i)).toBeVisible();
		await page.getByRole("tab", { name: /diagnostics/i }).click();
		await expect(page.getByText("Route Direct Parallel")).toBeVisible();
		await expect(page.getByText("Generation sources")).toBeVisible();
		await expect(page.locator(".workflow-diagnostics-stat", { hasText: "Model-authored" }).getByText("1")).toBeVisible();
		await expect(page.locator(".workflow-diagnostics-stat", { hasText: "Deterministic completion" }).getByText("1")).toBeVisible();
		await expect(page.getByText("Completion source")).toBeVisible();
		await expect(page.getByText("Coverage Completion")).toBeVisible();
		await expect(page.locator(".workflow-diagnostics-stat", { hasText: "Must-have gaps" }).getByText("1")).toBeVisible();
		await expect(page.locator(".workflow-diagnostics-stat", { hasText: "Optional/planned gaps" }).getByText("1")).toBeVisible();
		await expect(page.locator(".workflow-diagnostics-stat", { hasText: "Total additions" }).getByText("2")).toBeVisible();
		await expect(page.getByText("Parser recoveries")).toBeVisible();
		await expect(page.getByText(/recovered 1 complete test_cases entry/i)).toBeVisible();
		await expect(page.locator(".workflow-diagnostics-block.warning")).toHaveCount(0);
		await page.getByRole("button", { name: /^Next$/ }).click();
		await expect(page.getByRole("heading", { name: /^Automation$/i })).toBeVisible();
		await page.getByRole("button", { name: /preview execution/i }).click();
		await expect(page.getByRole("button", { name: /run 1 candidate/i })).toBeVisible();
		await page.getByRole("button", { name: /run 1 candidate/i }).click();
		await expect(page.getByText(/Execution passed: 1 passed/i)).toBeVisible();
		await page.getByRole("button", { name: /^Next$/ }).click();
		await expect(page.getByRole("heading", { name: /^Playwright Execution Report$/i })).toBeVisible();
		const reportCard = page.locator(".playwright-report-card").first();
		await expect(reportCard.getByText("Run exec_export_report")).toBeVisible();
		await expect(reportCard.getByText(/Artifacts root/i)).toBeVisible();
		await expect(reportCard.getByText("/tmp/agentic-tcg/exec_export_report/artifacts/playwright/tc_001/html-report")).toBeVisible();

		const jsonButton = page.getByRole("button", { name: /json/i }).first();
		await expect(page.getByText(/Export locked by review gate/i)).toBeVisible();
		await expect(jsonButton).toBeDisabled();

		await page.getByLabel(/export draft anyway/i).check();
		await expect(jsonButton).toBeDisabled();
		await page.getByPlaceholder(/Reason for exporting this draft/i).fill("Stakeholder review requested before final QA approval.");
		await expect(jsonButton).toBeEnabled();

		const download = await Promise.all([page.waitForEvent("download"), jsonButton.click()]).then(([item]) => item);

		expect(download.suggestedFilename()).toBe("test_cases.json");
		expect(exportPayload.draft_override_requested).toBe(true);
		expect(exportPayload.draft_override_reason).toContain("Stakeholder review requested");
		expect(exportPayload.approved).toBe(false);
	});
});
