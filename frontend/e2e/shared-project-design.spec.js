import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";

const caseFixture = (id, title) => ({
	id,
	title,
	description: `Objective for ${title}`,
	priority: "High",
	type: "Functional",
	status: "Draft",
	preconditions: "Signed in",
	expected_result: `Result for ${id}`,
	test_data: "Fixture test data",
	estimated_time: "3 minutes",
	automation_status: "Manual",
	component: "Checkout",
	tags: ["REQ-101"],
	steps: [1, 2, 3].map((n) => ({
		step: n,
		action: `${id} action ${n}`,
		expected: `${id} expectation ${n}`,
		test_data: n === 3 ? "Step-specific data" : "",
	})),
});

async function openCases(page, testCases = [caseFixture("TC-001", "Valid checkout"), caseFixture("TC-002", "Declined card")]) {
	const project = useCaseProjectFixture();
	project.current_snapshots.test_cases = {
		snapshot_id: "cases-v1",
		project_id: USE_CASE_PROJECT_ID,
		stage: "test_cases",
		version: 1,
		project_revision: 7,
		payload: {
			test_cases: testCases,
			review: {
				approved: false,
				score: 65,
				threshold: 90,
				blocking_issues: ["TC-001 requires a grounded button label."],
				summary: "Grounded UI details required.",
			},
		},
	};
	project.stage_state.test_cases = { current_snapshot_id: "cases-v1", approved: false, stale: false, version: 1 };
	await installUseCaseReviewApi(page, { initialProject: project });
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/test-cases`);
	await expect(page.getByRole("heading", { name: "Test Cases", exact: true, level: 1 })).toBeVisible();
	return project;
}

test("selects cases, expands steps and preserves all case metadata while searching", async ({ page }) => {
	await openCases(page);
	const detail = page.getByRole("region", { name: "Selected test case" });
	await expect(detail.getByRole("heading", { name: "Valid checkout" })).toBeVisible();
	await expect(detail).not.toContainText("TC-001 action 3");
	await detail.getByRole("button", { name: "Show all 3 steps" }).click();
	await expect(detail).toContainText("TC-001 action 3");
	await expect(detail).toContainText("Step-specific data");
	await detail.getByText("Test data and metadata", { exact: true }).click();
	await expect(detail).toContainText("Fixture test data");
	await expect(detail).toContainText("Manual");
	await page.getByRole("button", { name: /TC-002 Declined card/ }).click();
	await expect(detail.getByRole("heading", { name: "Declined card" })).toBeVisible();
	await expect(detail).toContainText("Result for TC-002");
	await expect(detail).not.toContainText("TC-001 action");
	await page.getByRole("searchbox", { name: "Search test cases" }).fill("Valid");
	await expect(detail.getByRole("heading", { name: "Valid checkout" })).toBeVisible();
	await page.getByRole("searchbox", { name: "Search test cases" }).fill("unmatched");
	await expect(page.getByText("No test cases match your search.")).toBeVisible();
	await expect(detail).toContainText("Select a test case");
	await page.getByRole("searchbox", { name: "Search test cases" }).fill("");
	await expect(detail.getByRole("heading", { name: "Declined card" })).toBeVisible();
	await page.getByRole("region", { name: "Test case quality" }).getByText("View findings", { exact: true }).click();
	await expect(page.getByRole("region", { name: "Test case quality" })).toContainText("TC-001 requires a grounded button label.");
	await page.getByRole("button", { name: "Template setup", exact: true }).click();
	await page.getByRole("button", { name: "Generate and review", exact: true }).click();
	await expect(page.getByRole("heading", { name: "Generated Test Cases", exact: true })).toBeVisible();
});

test("requires an explicit human decision and keeps navigation status separate", async ({ page }) => {
	const api = await installUseCaseReviewApi(page);
	await seedAuthenticatedSession(page);
	await page.goto(`/projects/${USE_CASE_PROJECT_ID}/use-cases`);
	const form = page.getByRole("form", { name: "Human review decision" });
	await expect(form.getByRole("radio", { name: /^Approve/ })).not.toBeChecked();
	await expect(form.getByRole("radio", { name: /^Request changes/ })).not.toBeChecked();
	await expect(form.getByRole("button", { name: "Choose a decision" })).toBeDisabled();
	const nav = page.getByRole("navigation", { name: "Project navigation", exact: true });
	await expect(nav.getByRole("link", { name: "Use Cases, Awaiting review" })).toHaveAttribute("aria-current", "page");
	await form.getByRole("radio", { name: /^Request changes/ }).check();
	await form.getByRole("button", { name: "Request changes", exact: true }).click();
	await expect(form.getByRole("alert")).toContainText("Comment required");
	expect(api.requests.review).toHaveLength(0);
});

for (const width of [390, 901, 1488]) {
	test(`test-case review reflows without document overflow at ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 1056 });
		await openCases(page);
		await expect(page.getByRole("region", { name: "Selected test case" })).toBeVisible();
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
		const input = page.getByRole("searchbox", { name: "Search test cases" });
		await input.fill("Declined");
		await expect(page.getByRole("region", { name: "Selected test case" })).toContainText("Declined card");
	});
}
