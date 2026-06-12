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
						automation_status: "Manual",
						component: "Reports",
						tags: ["REQ-001", "scenario:happy-path"],
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
				workflow_diagnostics: { status: "partial", warnings: [], parser_failures: [] },
				iteration_history: [],
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
		await page.getByRole("button", { name: /^Next$/ }).click();
		await expect(page.getByRole("heading", { name: /^Automation$/i })).toBeVisible();
		await page.getByRole("button", { name: /^Next$/ }).click();

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
