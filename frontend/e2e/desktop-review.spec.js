import { test, expect } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";

test("shows saved step numbers and exact test data across case selection and reload", { tag: "@p1" }, async ({ page }) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	const project = useCaseProjectFixture();
	const literal = '  =SUM(1,2)\r\n"Quoted"\u00a0🧪\t\n';
	project.current_snapshots.test_cases = {
		snapshot_id: "exact-step-review",
		stage: "test_cases",
		version: 1,
		payload: {
			test_cases: [
				{
					id: "TC-EXACT",
					title: "Inspect saved editor data",
					linked_requirement_ids: ["REQ-101"],
					steps: [
						{ step: 4, action: "Clear the editor", expected: "The field is empty", test_data: "" },
						{ step: 7, action: "Read the label", expected: "Label is visible" },
						{ step: 9, action: "Submit the payload", expected: "Exact data is saved", test_data: literal },
					],
				},
				{
					id: "TC-OLDER",
					title: "Inspect an older case",
					steps: [{ action: "Open the earlier form", expected: "The earlier form appears", test_data: null }],
				},
			],
			review: { approved: false, score: 0, threshold: 90 },
		},
	};
	project.stage_state.test_cases = { current_snapshot_id: "exact-step-review", approved: false, stale: false };
	await installUseCaseReviewApi(page, { initialProject: project });
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/test-cases`);
	const casesTab = page.getByRole("tab", { name: /Generated Test Cases/ });
	await casesTab.click();

	const detail = page.getByRole("region", { name: "Selected test case" });
	const exactCase = page.getByRole("button", { name: "TC-EXACT Inspect saved editor data" });
	const checkExactData = async () => {
		await expect(detail.getByRole("rowheader")).toHaveText(["4", "7", "9"]);
		await expect(detail.getByRole("row", { name: /Clear the editor/ })).toContainText("Test data is an empty string.");
		await expect(detail.getByRole("row", { name: /Read the label/ }).getByText(/Test data/)).toHaveCount(0);
		const payload = detail.getByRole("row", { name: /Submit the payload/ });
		await payload.getByText("Test data", { exact: true }).press("Enter");
		const raw = payload.locator("p.test-step-data-value");
		await expect(raw).toBeVisible();
		expect(await raw.textContent()).toBe(literal);
		await payload.getByText("Escaped JSON", { exact: true }).press("Enter");
		const escaped = payload.locator("pre");
		await expect(escaped).toBeVisible();
		const encoded = await escaped.textContent();
		expect(encoded).toContain("\\r\\n");
		expect(encoded).toContain("\\u00a0\\ud83e\\uddea\\t\\n");
		expect(JSON.parse(encoded)).toBe(literal);
	};
	await exactCase.click();
	await checkExactData();

	await page.getByRole("button", { name: "TC-OLDER Inspect an older case" }).click();
	await expect(detail.getByRole("rowheader")).toHaveText(["1"]);
	await expect(detail.getByRole("row", { name: /Open the earlier form/ }).getByText(/Test data/)).toHaveCount(0);
	await expect(detail.getByText("Escaped JSON")).toHaveCount(0);
	await exactCase.click();
	await page.reload();
	await casesTab.click();
	await expect(exactCase).toHaveAttribute("aria-pressed", "true");

	await page.setViewportSize({ width: 390, height: 844 });
	await checkExactData();
	await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

for (const hasMetrics of [true, false]) {
	test(`desktop review owns its evidence after reload (metrics ${hasMetrics})`, { tag: "@p1" }, async ({ page }) => {
		await page.setViewportSize({ width: hasMetrics ? 1366 : 1920, height: 900 });
		const project = useCaseProjectFixture();
		const stepData = '{\n  "total": 9,\n  "memo": "  Keep spaces  "\n}';
		project.current_snapshots.use_cases.payload.coverage_metrics = {
			cases_with_traceability: 30,
			covered_planned_scenarios: 32,
			planned_scenarios_total: 32,
		};
		project.current_snapshots.test_cases = {
			snapshot_id: "desktop-tests",
			stage: "test_cases",
			version: 1,
			payload: {
				test_cases: [
					{
						id: "TC-DESKTOP",
						title: "Submit payment",
						linked_requirement_ids: ["REQ-101"],
						steps: [{ step: 1, action: "Submit the order", expected: "Unique receipt identifier appears", test_data: stepData }],
					},
				],
				review: { approved: false, score: 0, threshold: 90 },
				substantive_assessment: hasMetrics
					? {
							status: "incomplete",
							obligations: [
								{
									requirement_id: "REQ-101",
									obligation: "Order receipt",
									status: "covered",
									source_quote: "Each order receives a unique receipt.",
									reason: "Step 1 checks the receipt.",
									evidence: [
										{ test_case_id: "TC-DESKTOP", step: 1, action: "Submit the order", expected: "Unique receipt identifier appears" },
									],
								},
							],
						}
					: null,
				...(hasMetrics
					? { coverage_metrics: { cases_with_traceability: 1, covered_planned_scenarios: 1, planned_scenarios_total: 4 } }
					: {}),
			},
		};
		project.stage_state.test_cases = { current_snapshot_id: "desktop-tests", approved: false, stale: false };
		await installUseCaseReviewApi(page, { initialProject: project });
		await seedAuthenticatedSession(page);
		await page.goto(`/projects/${USE_CASE_PROJECT_ID}/test-cases`);
		for (let visit = 0; visit < 2; visit++) {
			await page.getByRole("tab", { name: /Traceability Matrix/ }).click();
			await expect(page.getByText("Cases with traceability 1/1", { exact: true })).toBeVisible();
			await expect(page.getByText(hasMetrics ? "Linked scenarios 1/4" : "Linked scenarios Unavailable", { exact: true })).toBeVisible();
			await expect(page.getByText("Tests linked", { exact: true })).toBeVisible();
			await expect(page.getByText(/30\/1|32\/32/)).toHaveCount(0);
			await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
			await page.getByRole("searchbox", { name: "Search test cases" }).fill("Unique receipt");
			await expect(page.getByRole("button", { name: /TC-DESKTOP/ })).toBeVisible();
			await page
				.getByRole("row", { name: /Submit the order/ })
				.getByText("Test data", { exact: true })
				.click();
			const data = page.locator(".test-step-data p");
			await expect(data).toBeVisible();
			await expect(data).toHaveCSS("white-space", "break-spaces");
			expect(await data.textContent()).toBe(stepData);
			await page.getByText("Why this test? Source and assessment", { exact: true }).click();
			await expect(
				page.getByText(hasMetrics ? "Each order receives a unique receipt." : "No source-to-step assessment is available", {
					exact: hasMetrics,
				})
			).toBeVisible();
			if (!visit) await page.reload();
		}
		await page.screenshot({ path: test.info().outputPath("desktop-review.png"), fullPage: true });
	});
}
