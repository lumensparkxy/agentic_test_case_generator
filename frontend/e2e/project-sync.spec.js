import { test, expect } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";

for (const width of [1366, 1920]) {
	test(`project correction refresh is reviewable and resumes at ${width}px`, { tag: width === 1366 ? "@p1" : [] }, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		const project = useCaseProjectFixture();
		const findings = [
			{
				id: "finding-1",
				status: "open",
				category: "ambiguous_requirement",
				summary: "Confirm supervisor threshold",
				expected_behavior: "Threshold is 600",
				requirement_ids: ["REQ-202"],
				test_case_ids: [],
				source_snapshot_id: project.current_snapshots.requirements.snapshot_id,
			},
		];
		await installUseCaseReviewApi(page, { initialProject: project });
		await page.route(`**/projects/${USE_CASE_PROJECT_ID}`, (route) => route.fulfill({ json: project }));
		await page.route(`**/projects/${USE_CASE_PROJECT_ID}/knowledge`, (route) =>
			route.fulfill({ json: { entries: [], skills: [], features: {} } })
		);
		await page.route(`**/projects/${USE_CASE_PROJECT_ID}/quality-findings`, (route) =>
			route.fulfill({ json: { findings, project_revision: project.current_revision } })
		);
		const calls = [];
		await page.route(`**/projects/${USE_CASE_PROJECT_ID}/changes/accept`, async (route) => {
			calls.push(route.request().postDataJSON());
			findings[0].status = "accepted";
			project.current_revision++;
			project.project_sync = {
				id: "change-1",
				status: "partial",
				stages: { requirements: "draft", context: "draft", use_cases: "draft", test_cases: "pending", automation: "not_requested" },
				error: "Model unavailable. Previous tests are preserved.",
			};
			await route.fulfill({ json: project });
		});
		let resumes = 0;
		await page.route(`**/projects/${USE_CASE_PROJECT_ID}/changes/resume`, async (route) => {
			resumes++;
			project.current_revision++;
			project.project_sync = {
				...project.project_sync,
				status: "ready_for_review",
				error: null,
				stages: { ...project.project_sync.stages, test_cases: "draft" },
				review: { changed: 1, added: 0, preserved: 49 },
			};
			await route.fulfill({ json: project });
		});
		await seedAuthenticatedSession(page);
		await page.goto(`/projects/${USE_CASE_PROJECT_ID}/context`);
		const panel = page.getByRole("region", { name: "Shared project corrections" });
		await panel.getByLabel("Finding to resolve").selectOption("finding-1");
		await panel.getByLabel("Accepted business decision").fill("Purchases above 600 require supervisor approval.");
		await panel.getByLabel("Corrected source · REQ-202").fill("Purchases above 600 require supervisor approval.");
		await panel.getByRole("button", { name: "Accept correction and refresh drafts" }).click();
		const status = page.getByRole("region", { name: "Project update status" });
		await expect(status).toContainText("Previous tests are preserved");
		expect(calls[0].corrections[0].requirement_id).toBe("REQ-202");
		await page.reload();
		await status.getByRole("button", { name: "Resume remaining stages" }).click();
		await expect(status).toContainText("Updated drafts are ready for review");
		await expect(status).toContainText("1 changed · 0 added · 49 preserved");
		expect(resumes).toBe(1);
		await page.screenshot({ path: test.info().outputPath(`project-sync-${width}.png`), fullPage: true });
	});
}
