import { expect, test } from "@playwright/test";

import { sampleRequirementsFile, seedAuthenticatedSession } from "./support/auth.js";

const viewportCases = [
	{ label: "desktop", size: { width: 1280, height: 900 } },
	{ label: "mobile", size: { width: 390, height: 844 } },
];

function jsonResponse(route, payload, status = 200, headers = {}) {
	return route.fulfill({
		status,
		contentType: "application/json",
		headers,
		body: JSON.stringify(payload),
	});
}

async function installMockRoutes(page) {
	await page.route("**/auth/me", async (route) =>
		jsonResponse(route, {
			sub: "playwright-css-smoke-user",
			email: "playwright-css-smoke@example.com",
			name: "Playwright CSS Smoke",
			picture: null,
		})
	);
	await page.route("**/reports/usage/me", async (route) => jsonResponse(route, { groups: [] }));
	await page.route("**/entitlements/me", async (route) =>
		jsonResponse(route, {
			account: { plan_tier: "premium", support_contact_email: "support@example.test" },
			requirements: { remaining: 500, exhausted: false },
			test_cases: { remaining: 500, exhausted: false },
			wallet: { balance_units: 5000, balance_token_display: "5000" },
			shadow_mode: false,
		})
	);
	await page.route("**/integrations/jira/connection", async (route) =>
		jsonResponse(route, {
			connected: false,
			connection: null,
		})
	);
	await page.route("**/integrations/azure-devops/connection", async (route) =>
		jsonResponse(route, {
			connected: false,
			connection: null,
		})
	);
	await page.route("**/requirements/parse", async (route) =>
		jsonResponse(route, {
			source_name: "sample-requirements.md",
			raw_text: "The system shall allow users to export reports. The system shall block invalid exports.",
			requirements: [
				{
					id: "REQ-001",
					text: "The system shall allow users to export reports.",
					review_status: "Approved",
					source_system: "file",
					source_path: "sample-requirements.md",
				},
				{
					id: "REQ-002",
					text: "The system shall block invalid exports.",
					review_status: "Approved",
					source_system: "file",
					source_path: "sample-requirements.md",
				},
			],
			review: { approved: true, score: 95, threshold: 85, summary: "Requirements approved.", blocking_issues: [] },
			coverage_metrics: {
				total_requirements: 2,
				unique_requirements: 2,
				duplicate_requirements: 0,
				shall_format_count: 2,
				requirements_per_document: 2,
			},
			workflow_diagnostics: { status: "completed", warnings: [], parser_failures: [] },
			iteration_history: [],
		})
	);
	await page.route("**/requirements/enrich", async (route) => {
		const payload = route.request().postDataJSON();
		return jsonResponse(route, {
			...payload,
			grounded_context: {
				artifact_sources: [
					{ id: "ART-APP-01", source_type: "app", label: "Export workspace", url: "https://example.test/app", status: "Analyzed" },
				],
				ui_elements: [
					{
						id: "ART-APP-01-UI-01",
						source_id: "ART-APP-01",
						label: "Export",
						element_type: "Button",
						description: "Starts report export.",
					},
				],
				workflows: [{ id: "ART-APP-01-WF-01", source_id: "ART-APP-01", name: "Export report", steps: ["Open report", "Choose export"] }],
			},
		});
	});
	await page.route("**/testcases/generate", async (route) =>
		jsonResponse(route, {
			test_cases: [
				{
					id: "TC-001",
					title: "Export report",
					description: "Verify an approved report can be exported.",
					priority: "High",
					type: "Functional",
					status: "Ready",
					preconditions: "A report exists.",
					steps: [
						{ step: 1, action: "Open the report", expected: "Report details are visible", test_data: null },
						{ step: 2, action: "Export the report", expected: "The export downloads", test_data: null },
					],
					expected_result: "The report export completes.",
					test_data: null,
					estimated_time: "5 mins",
					automation_status: "To Be Automated",
					component: "Reports",
					tags: ["REQ-001"],
				},
			],
			review: { approved: true, score: 96, threshold: 90, summary: "Generated cases approved.", blocking_issues: [] },
			approved: true,
			coverage_plan: [],
			requirement_analysis: [],
			coverage_metrics: { total_test_cases: 1, requirements_total: 2, requirements_covered: 1 },
			workflow_diagnostics: { status: "completed", warnings: [], parser_failures: [] },
			iteration_history: [],
		})
	);
}

async function expectNoHorizontalOverflow(page, screenName) {
	const metrics = await page.evaluate(() => {
		const viewportWidth = window.innerWidth;
		const hasHorizontalScrollAncestor = (element) => {
			let current = element.parentElement;
			while (current && current !== document.body) {
				const style = window.getComputedStyle(current);
				if (current.scrollWidth > current.clientWidth + 1 && ["auto", "scroll"].includes(style.overflowX)) {
					return true;
				}
				current = current.parentElement;
			}
			return false;
		};
		const overflowing = Array.from(document.querySelectorAll("body *"))
			.filter((element) => {
				const rect = element.getBoundingClientRect();
				if (rect.width === 0 || rect.height === 0) {
					return false;
				}
				if (hasHorizontalScrollAncestor(element)) {
					return false;
				}
				return rect.left < -1 || rect.right > viewportWidth + 1;
			})
			.slice(0, 5)
			.map((element) => ({
				tag: element.tagName.toLowerCase(),
				className: String(element.className || ""),
				text: element.textContent?.trim().replace(/\s+/g, " ").slice(0, 80),
			}));

		return {
			viewportWidth,
			scrollWidth: document.documentElement.scrollWidth,
			overflowing,
		};
	});

	const message = `${screenName} overflow metrics: ${JSON.stringify(metrics)}`;
	expect(metrics.scrollWidth, message).toBeLessThanOrEqual(metrics.viewportWidth + 2);
	expect(metrics.overflowing, message).toEqual([]);
}

test.describe("Frontend CSS smoke", () => {
	for (const { label, size } of viewportCases) {
		test(`renders auth and settings surfaces without overflow on ${label}`, async ({ page }) => {
			await page.setViewportSize(size);
			await page.goto("/");
			await expect(page.getByRole("heading", { name: /agentic test case generator/i })).toBeVisible();
			await expectNoHorizontalOverflow(page, `${label} auth`);

			await page.getByRole("button", { name: /settings/i }).click();
			await expect(page.getByRole("dialog", { name: /settings/i })).toBeVisible();
			await expectNoHorizontalOverflow(page, `${label} workflow settings`);

			await page.getByRole("button", { name: /integrations/i }).click();
			await expect(page.getByRole("heading", { name: /integration connections/i })).toBeVisible();
			await expect(page.getByRole("heading", { name: /jira cloud/i })).toBeVisible();
			await expect(page.getByRole("heading", { name: /azure devops/i })).toBeVisible();
			await expectNoHorizontalOverflow(page, `${label} integration settings`);
		});

		test(`renders primary workflow screens without overflow on ${label}`, async ({ page }) => {
			await page.setViewportSize(size);
			await installMockRoutes(page);
			await seedAuthenticatedSession(page);

			await page.goto("/");
			await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible({ timeout: 30_000 });
			await expectNoHorizontalOverflow(page, `${label} requirements`);

			await page
				.getByRole("navigation", { name: "Workflow navigation" })
				.getByRole("button", { name: /^Collapse workflow navigation$/i })
				.click();
			await page
				.getByLabel("Project information rail")
				.getByRole("button", { name: /^Collapse project information$/i })
				.click();
			await expect(
				page.getByRole("navigation", { name: "Workflow navigation" }).getByRole("button", { name: /^Expand workflow navigation$/i })
			).toBeVisible();
			await expect(
				page.getByLabel("Project information rail").getByRole("button", { name: /^Expand project information$/i })
			).toBeVisible();
			await expectNoHorizontalOverflow(page, `${label} collapsed shell`);

			await page.locator('input[type="file"]').setInputFiles(sampleRequirementsFile);
			await page.getByRole("button", { name: /parse requirements/i }).click();
			await expect(page.locator(".requirement-review-table tbody tr")).toHaveCount(2);
			await expectNoHorizontalOverflow(page, `${label} requirement review`);

			await page.getByRole("button", { name: /^Next$/ }).click();
			await expect(page.getByRole("heading", { name: /context inputs/i })).toBeVisible();
			await page.locator('input[placeholder="https://your-app"]').fill("https://example.test/app");
			await page.getByRole("button", { name: /analyze context/i }).click();
			await expect(page.getByRole("heading", { name: /grounded context/i })).toBeVisible();
			await expectNoHorizontalOverflow(page, `${label} context`);

			await page.getByRole("button", { name: /^Next$/ }).click();
			await expect(page.getByRole("heading", { name: /template setup/i })).toBeVisible();
			await expectNoHorizontalOverflow(page, `${label} template`);

			await page.getByRole("button", { name: /^Next$/ }).click();
			await expect(page.getByRole("heading", { name: /generate test cases/i })).toBeVisible();
			await page.getByRole("button", { name: /generate from \d+ approved/i }).click();
			await expect(page.locator(".test-cases-table tbody tr").or(page.locator(".case-card")).first()).toBeVisible();
			await expectNoHorizontalOverflow(page, `${label} generate`);

			await page.getByRole("button", { name: /^Next$/ }).click();
			await expect(page.getByRole("heading", { name: /^automation$/i })).toBeVisible();
			await expectNoHorizontalOverflow(page, `${label} automation`);

			await page.getByRole("button", { name: /^Next$/ }).click();
			await expect(page.getByRole("heading", { name: /export test cases/i })).toBeVisible();
			await expectNoHorizontalOverflow(page, `${label} export`);
		});
	}
});
