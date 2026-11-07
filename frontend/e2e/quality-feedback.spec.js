import { test, expect } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";

test("@p1 capture missing login, backfill references and keep verification pending", async ({ page }) => {
	await installUseCaseReviewApi(page);
	await seedAuthenticatedSession(page);
	let findings = [];
	const actions = [];
	const requestIds = [];
	let failedApply = false;
	await page.route(`**/projects/${USE_CASE_PROJECT_ID}/knowledge`, (route) =>
		route.fulfill({ json: { entries: [], skills: [], features: {}, revision: "0" } })
	);
	await page.route(`**/projects/${USE_CASE_PROJECT_ID}/quality-findings**`, async (route) => {
		if (route.request().method() === "GET") return route.fulfill({ json: { project_revision: 7, findings } });
		const body = route.request().postDataJSON();
		actions.push(body);
		requestIds.push(route.request().headers()["x-request-id"]);
		if (body.action === "apply" && !failedApply) {
			failedApply = true;
			return route.fulfill({ status: 503, json: { detail: "Temporary feedback failure" } });
		}
		if (!body.action) findings = [{ ...body, id: "finding-1", status: "open" }];
		if (body.action === "apply")
			findings[0] = { ...findings[0], prerequisites: body.prerequisites, status: "applied_pending_verification" };
		if (body.action === "verify") return route.fulfill({ status: 409, json: { detail: "Current execution evidence is required" } });
		return route.fulfill({ json: { finding: findings[0], project_revision: 8 } });
	});
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/context`);
	const panel = page.getByRole("region", { name: "Quality feedback" });
	await expect(panel).toBeVisible();
	await panel.getByText("Record a quality finding", { exact: true }).click();
	await panel.getByLabel("Observed problem").fill("Supervisor login is missing");
	await panel.getByLabel("Expected behavior").fill("Supervisor approves the expense");
	await panel.getByLabel("Affected test case IDs").fill("TC-1");
	await panel.getByLabel("Missing prerequisite name").fill("supervisor_login");
	await panel.getByLabel("Prerequisite kind").selectOption("credential");
	await panel.getByLabel("Prerequisite description").fill("Account with Supervisor role");
	await panel.getByRole("button", { name: "Record finding", exact: true }).click();
	await expect(panel.getByRole("listitem").filter({ hasText: "Supervisor login is missing" })).toBeVisible();
	await panel.getByRole("button", { name: "Review finding", exact: true }).click();
	await panel.getByLabel("supervisor_login reference").fill("env:PETF_TEST_SUPERVISOR_LOGIN");
	await panel.getByRole("button", { name: "Apply prerequisite correction" }).click();
	await expect(panel.getByRole("alert")).toContainText("Temporary feedback failure");
	await panel.getByRole("button", { name: "Apply prerequisite correction" }).click();
	await expect(panel).toContainText("applied pending verification");
	await panel.getByRole("button", { name: "Review finding", exact: true }).click();
	await panel.getByLabel("Reusable lesson").fill("Approval tests require a provisioned actor with the specified role.");
	await panel.getByRole("button", { name: "Propose lesson" }).click();
	await expect.poll(() => actions.length).toBe(4);
	expect(requestIds[1]).toBe(requestIds[2]);
	expect(actions[1].prerequisites[0].reference).toBe("env:PETF_TEST_SUPERVISOR_LOGIN");
	expect(actions[3].lesson.stages).toEqual(["requirements", "use_cases", "test_cases", "automation"]);
	await expect(panel).toContainText("1 findings awaiting resolution or verification");
	await page.screenshot({ path: "/tmp/quality-feedback-desktop.png", fullPage: true });
	await page.setViewportSize({ width: 390, height: 844 });
	await expect(panel.getByRole("button", { name: "Record finding", exact: true })).toBeVisible();
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
	await page.screenshot({ path: "/tmp/quality-feedback-mobile.png", fullPage: true });
});
