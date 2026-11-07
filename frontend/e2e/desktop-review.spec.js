import { test, expect } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";

for (const hasMetrics of [true, false]) {
	test(`desktop review owns its evidence after reload (metrics ${hasMetrics})`, { tag: "@p1" }, async ({ page }) => {
		await page.setViewportSize({ width: hasMetrics ? 1366 : 1920, height: 900 });
		const project = useCaseProjectFixture();
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
						steps: [{ step: 1, action: "Submit the order", expected: "Unique receipt identifier appears" }],
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
