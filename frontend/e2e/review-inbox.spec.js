import { expect, test } from "@playwright/test";

import { buildProjectPath } from "../src/app/workflowRoutes.js";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture } from "./support/use-case-review.js";
import {
	createDeferred,
	installWorkspaceApi,
	workspaceProjectFixture,
	workspaceSummaryFixture,
	workspaceWorkItemFixture,
} from "./support/workspace.js";

const USE_CASE_PROJECT = workspaceProjectFixture({
	project_id: "project-inbox-use-cases",
	name: "Mercury Checkout",
	project_revision: 7,
	current_stage: "use_cases",
	current_status: "attention_required",
	current_snapshot_id: "snapshot-use-cases-mercury",
	reason: "Three generated scenarios need human approval.",
	updated_at: "2026-07-17T12:00:00Z",
});

const REQUIREMENTS_PROJECT = workspaceProjectFixture({
	project_id: "project-inbox-requirements",
	name: "Atlas Accounts",
	project_revision: 4,
	current_stage: "requirements",
	current_status: "ready",
	current_snapshot_id: "snapshot-requirements-atlas",
	reason: "Requirements are ready for review.",
	updated_at: "2026-07-17T11:50:00Z",
});

const TEST_CASE_PROJECT = workspaceProjectFixture({
	project_id: "project-inbox-test-cases",
	name: "Nova Billing",
	project_revision: 9,
	current_stage: "review",
	current_status: "attention_required",
	current_snapshot_id: "snapshot-test-cases-nova",
	reason: "Five test cases need review.",
	updated_at: "2026-07-17T11:40:00Z",
});

const INFO_PROJECT = workspaceProjectFixture({
	project_id: "project-inbox-information",
	name: "Orbit Evidence",
	project_revision: 5,
	current_stage: "execution",
	current_status: "failed",
	current_snapshot_id: "snapshot-execution-orbit",
	reason: "Two checks failed in staging.",
	updated_at: "2026-07-17T11:30:00Z",
});

const USE_CASE_ITEM = workspaceWorkItemFixture({
	work_item_id: "work-mercury-use-cases",
	kind: "review",
	project_id: USE_CASE_PROJECT.project_id,
	project_name: USE_CASE_PROJECT.name,
	project_revision: USE_CASE_PROJECT.project_revision,
	stage: "use_cases",
	status: "attention_required",
	action: "approve",
	enabled: true,
	primary: true,
	count: 3,
	reason: USE_CASE_PROJECT.reason,
	current_snapshot_id: USE_CASE_PROJECT.current_snapshot_id,
	updated_at: USE_CASE_PROJECT.updated_at,
});

const REQUIREMENTS_ITEM = workspaceWorkItemFixture({
	work_item_id: "work-atlas-requirements",
	kind: "review",
	project_id: REQUIREMENTS_PROJECT.project_id,
	project_name: REQUIREMENTS_PROJECT.name,
	project_revision: REQUIREMENTS_PROJECT.project_revision,
	stage: "requirements",
	status: "ready",
	action: "approve",
	enabled: true,
	primary: true,
	count: 6,
	reason: REQUIREMENTS_PROJECT.reason,
	current_snapshot_id: REQUIREMENTS_PROJECT.current_snapshot_id,
	updated_at: REQUIREMENTS_PROJECT.updated_at,
});

const TEST_CASE_ITEM = workspaceWorkItemFixture({
	work_item_id: "work-nova-review",
	kind: "review",
	project_id: TEST_CASE_PROJECT.project_id,
	project_name: TEST_CASE_PROJECT.name,
	project_revision: TEST_CASE_PROJECT.project_revision,
	stage: "review",
	status: "attention_required",
	action: "review",
	enabled: true,
	primary: true,
	count: 5,
	reason: TEST_CASE_PROJECT.reason,
	current_snapshot_id: TEST_CASE_PROJECT.current_snapshot_id,
	updated_at: TEST_CASE_PROJECT.updated_at,
});

const FAILED_ITEM = workspaceWorkItemFixture({
	work_item_id: "work-orbit-execution",
	kind: "information",
	project_id: INFO_PROJECT.project_id,
	project_name: INFO_PROJECT.name,
	project_revision: INFO_PROJECT.project_revision,
	stage: "execution",
	status: "failed",
	action: null,
	enabled: false,
	primary: false,
	count: 2,
	reason: INFO_PROJECT.reason,
	current_snapshot_id: INFO_PROJECT.current_snapshot_id,
	updated_at: INFO_PROJECT.updated_at,
});

const BLOCKED_ITEM = workspaceWorkItemFixture({
	work_item_id: "work-orbit-requirements",
	kind: "information",
	project_id: INFO_PROJECT.project_id,
	project_name: INFO_PROJECT.name,
	project_revision: INFO_PROJECT.project_revision,
	stage: "requirements",
	status: "blocked",
	action: null,
	enabled: false,
	primary: false,
	count: 4,
	reason: "Requirements approval is missing.",
	current_snapshot_id: "snapshot-requirements-orbit",
	updated_at: "2026-07-17T11:20:00Z",
});

const COMPLETED_ITEM = workspaceWorkItemFixture({
	work_item_id: "work-orbit-reports-complete",
	kind: "information",
	project_id: INFO_PROJECT.project_id,
	project_name: INFO_PROJECT.name,
	project_revision: INFO_PROJECT.project_revision,
	stage: "reports",
	status: "completed",
	action: null,
	enabled: false,
	primary: false,
	count: 12,
	reason: "The evidence report is complete.",
	current_snapshot_id: "snapshot-reports-orbit",
	updated_at: "2026-07-17T11:10:00Z",
});

const DUPLICATE_USE_CASE_ITEM = {
	...USE_CASE_ITEM,
	work_item_id: "duplicate-work-item-id-must-not-render",
	reason: "Duplicate backend row must not render.",
};

const POPULATED_SUMMARY = workspaceSummaryFixture({
	continue_working: USE_CASE_ITEM,
	projects: [USE_CASE_PROJECT, REQUIREMENTS_PROJECT, TEST_CASE_PROJECT, INFO_PROJECT],
	work_items: [USE_CASE_ITEM, REQUIREMENTS_ITEM, TEST_CASE_ITEM, DUPLICATE_USE_CASE_ITEM, FAILED_ITEM, BLOCKED_ITEM, COMPLETED_ITEM],
});

const REQUIREMENTS_ONLY_SUMMARY = workspaceSummaryFixture({
	continue_working: REQUIREMENTS_ITEM,
	projects: [REQUIREMENTS_PROJECT],
	work_items: [REQUIREMENTS_ITEM],
});

const LONG_PROJECT_NAME = "Mercury Checkout and International Settlement Operations for Enterprise Customers";
const RESPONSIVE_SUMMARY = workspaceSummaryFixture({
	...POPULATED_SUMMARY,
	projects: POPULATED_SUMMARY.projects.map((project) =>
		project.project_id === USE_CASE_PROJECT.project_id ? { ...project, name: LONG_PROJECT_NAME } : project
	),
	work_items: POPULATED_SUMMARY.work_items.map((item) =>
		item.project_id === USE_CASE_PROJECT.project_id ? { ...item, project_name: LONG_PROJECT_NAME } : item
	),
});

function inbox(page) {
	return page.getByRole("main", { name: /^Review Inbox$/i });
}

function inboxList(page) {
	return inbox(page).getByRole("list");
}

function inboxRow(page, projectName) {
	return inboxList(page).getByRole("listitem").filter({ hasText: projectName });
}

async function openInbox(page, options = {}) {
	const api = await installWorkspaceApi(page, options);
	await seedAuthenticatedSession(page);
	await page.goto("/reviews");
	await expect(page.getByRole("heading", { name: /^Review Inbox$/i, level: 1 })).toBeVisible({ timeout: 30_000 });
	return api;
}

async function expectNoProjectShell(page) {
	await expect(page.getByRole("navigation", { name: /^Project navigation$/i })).toHaveCount(0);
	await expect(page.getByLabel("Project information rail")).toHaveCount(0);
	await expect(page.getByLabel("Contextual task")).toHaveCount(0);
}

test.describe("Global Review Inbox", () => {
	test("preserves server order, deduplicates snapshot work, and opens every canonical review workbench", async ({ page }) => {
		const api = await openInbox(page, { summary: POPULATED_SUMMARY });
		await expect(page.getByRole("navigation", { name: /^Global navigation$/i }).getByRole("link", { name: /^Reviews$/i })).toHaveAttribute(
			"aria-current",
			"page"
		);
		await expectNoProjectShell(page);

		const rows = inboxList(page).getByRole("listitem");
		await expect(rows).toHaveCount(3);
		await expect(rows.nth(0)).toContainText(USE_CASE_PROJECT.name);
		await expect(rows.nth(1)).toContainText(REQUIREMENTS_PROJECT.name);
		await expect(rows.nth(2)).toContainText(TEST_CASE_PROJECT.name);
		await expect(inbox(page)).not.toContainText("Duplicate backend row must not render");
		await expect(inbox(page)).not.toContainText(INFO_PROJECT.name);

		const useCaseRow = inboxRow(page, USE_CASE_PROJECT.name);
		await expect(useCaseRow).toContainText("3 scenarios");
		await expect(useCaseRow.locator(".review-inbox-kind .sr-only")).toHaveText("Task type:");
		await expect(useCaseRow).toContainText("Needs attention");
		await expect(
			useCaseRow.getByRole("heading", {
				level: 3,
				name: new RegExp(`${USE_CASE_PROJECT.name}.*Use Cases.*Review approvals`, "i"),
			})
		).toBeVisible();
		await expect(useCaseRow.getByRole("link", { name: new RegExp(`Open .* for ${USE_CASE_PROJECT.name}`, "i") })).toHaveAttribute(
			"href",
			buildProjectPath(USE_CASE_PROJECT.project_id, "use-cases")
		);
		await expect(inboxRow(page, REQUIREMENTS_PROJECT.name).getByRole("link")).toHaveAttribute(
			"href",
			buildProjectPath(REQUIREMENTS_PROJECT.project_id, "requirements")
		);
		await expect(inboxRow(page, TEST_CASE_PROJECT.name).getByRole("link")).toHaveAttribute(
			"href",
			buildProjectPath(TEST_CASE_PROJECT.project_id, "test-cases")
		);
		expect(api.requests.workspaceSummary).toHaveLength(1);
	});

	test("filters locally by stage and durable status while keeping secondary states out of the default queue", async ({ page }) => {
		const api = await openInbox(page, { summary: POPULATED_SUMMARY });
		await expect(inboxList(page).getByRole("listitem")).toHaveCount(3);
		await expect.poll(() => api.requests.workspaceSummary.length).toBe(1);
		const initialRequestCount = api.requests.workspaceSummary.length;

		await inbox(page)
			.getByRole("combobox", { name: /^Stage$/i })
			.selectOption("use_cases");
		await expect(inboxList(page).getByRole("listitem")).toHaveCount(1);
		await expect(inbox(page)).toContainText(USE_CASE_PROJECT.name);

		await inbox(page)
			.getByRole("combobox", { name: /^Status$/i })
			.selectOption("ready");
		await expect(page.getByRole("heading", { name: /^No items match these filters$/i })).toBeVisible();
		await inbox(page)
			.getByRole("button", { name: /^Clear filters$/i })
			.first()
			.click();
		await expect(inbox(page).getByRole("combobox", { name: /^Stage$/i })).toBeFocused();
		await expect(inboxList(page).getByRole("listitem")).toHaveCount(3);

		await inbox(page)
			.getByRole("button", { name: /^Informational & completed$/i })
			.click();
		const secondaryRows = inboxList(page).getByRole("listitem");
		await expect(secondaryRows).toHaveCount(3);
		await expect(secondaryRows.nth(0)).toContainText("Failed");
		await expect(secondaryRows.nth(1)).toContainText("Blocked");
		await expect(secondaryRows.nth(2)).toContainText("Complete");

		await inbox(page)
			.getByRole("combobox", { name: /^Stage$/i })
			.selectOption("execution");
		await expect(inboxList(page).getByRole("listitem")).toHaveCount(1);
		await expect(inbox(page)).toContainText("2 checks");
		expect(api.requests.workspaceSummary).toHaveLength(initialRequestCount);
	});

	test("shows a distinct caught-up state when only informational work remains", async ({ page }) => {
		await openInbox(page, {
			summary: workspaceSummaryFixture({ projects: [INFO_PROJECT], work_items: [FAILED_ITEM, COMPLETED_ITEM] }),
		});

		await expect(page.getByRole("heading", { name: /^No actionable reviews$/i })).toBeVisible();
		await inbox(page)
			.getByRole("button", { name: /^View informational items$/i })
			.click();
		await expect(inbox(page).getByRole("heading", { name: /^Informational & completed$/i, level: 2 })).toBeFocused();
		await expect(inboxList(page).getByRole("listitem")).toHaveCount(2);
		await expect(inbox(page)).toContainText(INFO_PROJECT.name);
	});

	test("renders loading failure, retries the bounded summary, and recovers into the queue", async ({ page }) => {
		const api = await openInbox(page, {
			summary: POPULATED_SUMMARY,
			summaryScenarios: [{ status: 503, payload: { detail: "Review summary is temporarily unavailable" } }, { payload: POPULATED_SUMMARY }],
		});

		const alert = page.getByRole("alert");
		await expect(alert).toContainText("Review summary is temporarily unavailable");
		await alert.getByRole("button", { name: /^Retry$/i }).click();
		await expect.poll(() => api.requests.workspaceSummary.length).toBe(2);
		await expect(inboxList(page).getByRole("listitem")).toHaveCount(3);
		await expect(alert).toHaveCount(0);
		await expect(page.getByRole("heading", { name: /^Review Inbox$/i, level: 1 })).toBeFocused();
	});

	test("labels cached rows as stale, normalizes vanished filters after retry, and never hydrates projects", async ({ page }) => {
		const api = await openInbox(page, {
			summary: POPULATED_SUMMARY,
			summaryScenarios: [
				{ payload: POPULATED_SUMMARY },
				{ status: 503, payload: { detail: "Review refresh is temporarily unavailable" } },
				{ payload: REQUIREMENTS_ONLY_SUMMARY },
			],
		});
		await expect(inboxList(page).getByRole("listitem")).toHaveCount(3);
		await inbox(page)
			.getByRole("combobox", { name: /^Stage$/i })
			.selectOption("use_cases");
		await inbox(page)
			.getByRole("button", { name: /^Refresh reviews$/i })
			.click();

		const alert = page.getByRole("alert");
		await expect(alert).toContainText("Review refresh is temporarily unavailable");
		await expect(alert).toContainText(/showing the last available review queue/i);
		await expect(inboxRow(page, USE_CASE_PROJECT.name)).toHaveCount(1);
		await expect(inbox(page).getByRole("button", { name: /^Refresh reviews$/i })).toBeFocused();
		expect(api.requests.projectDetail).toHaveLength(0);

		await alert.getByRole("button", { name: /^Retry$/i }).click();
		await expect.poll(() => api.requests.workspaceSummary.length).toBe(3);
		await expect(alert).toHaveCount(0);
		await expect(inbox(page).getByRole("combobox", { name: /^Stage$/i })).toHaveValue("all");
		await expect(inboxList(page).getByRole("listitem")).toHaveCount(1);
		await expect(inbox(page)).toContainText(REQUIREMENTS_PROJECT.name);
		await expect(page.getByRole("heading", { name: /^Review Inbox$/i, level: 1 })).toBeFocused();
		expect(api.requests.projectDetail).toHaveLength(0);
	});

	test("announces the bounded loading state before review items arrive", async ({ page }) => {
		const summaryGate = createDeferred();
		const api = await installWorkspaceApi(page, {
			summary: POPULATED_SUMMARY,
			summaryScenarios: [{ gate: summaryGate.promise, payload: POPULATED_SUMMARY }],
		});
		await seedAuthenticatedSession(page);
		await page.goto("/reviews");

		await expect(page.getByRole("status", { name: /^Loading workspace$/i })).toBeVisible({ timeout: 30_000 });
		await expect.poll(() => api.requests.workspaceSummary.length).toBe(1);
		summaryGate.resolve();
		await expect(inboxList(page).getByRole("listitem")).toHaveCount(3);
		await expect(page.getByRole("status", { name: /^Loading workspace$/i })).toHaveCount(0);
	});

	test("renders the authoritative empty queue without project workflow chrome", async ({ page }) => {
		await openInbox(page, { summary: workspaceSummaryFixture() });

		await expect(page.getByRole("heading", { name: /^Review queue is clear$/i })).toBeVisible();
		await expect(inbox(page).getByRole("list")).toHaveCount(0);
		await expectNoProjectShell(page);
	});

	test("keeps complete keyboard order and the populated queue contained across responsive breakpoints", async ({ page }) => {
		await page.setViewportSize({ width: 390, height: 844 });
		await openInbox(page, { summary: RESPONSIVE_SUMMARY });

		await expect(inboxList(page).getByRole("listitem")).toHaveCount(3);
		await page.keyboard.press("Tab");
		await expect(page.getByRole("link", { name: /^Skip to main content$/i })).toBeFocused();
		await page.keyboard.press("Enter");
		await expect(inbox(page)).toBeFocused();
		await page.keyboard.press("Tab");
		await expect(inbox(page).getByRole("button", { name: /^Refresh reviews$/i })).toBeFocused();
		await page.keyboard.press("Tab");
		await expect(inbox(page).getByRole("button", { name: /^Actionable$/i })).toBeFocused();
		await page.keyboard.press("Tab");
		await expect(inbox(page).getByRole("button", { name: /^Informational & completed$/i })).toBeFocused();
		await page.keyboard.press("Tab");
		await expect(inbox(page).getByRole("combobox", { name: /^Stage$/i })).toBeFocused();
		await page.keyboard.press("Tab");
		await expect(inbox(page).getByRole("combobox", { name: /^Status$/i })).toBeFocused();
		await page.keyboard.press("Tab");
		await expect(inboxRow(page, LONG_PROJECT_NAME).getByRole("link")).toBeFocused();

		for (const width of [390, 639, 640, 899, 900, 1280, 1920]) {
			await page.setViewportSize({ width, height: width < 900 ? 900 : 1080 });
			const overflow = await page.evaluate(() => ({
				clientWidth: document.documentElement.clientWidth,
				scrollWidth: document.documentElement.scrollWidth,
			}));
			expect(overflow.scrollWidth, `document overflow at ${width}px`).toBeLessThanOrEqual(overflow.clientWidth + 2);
		}
		const longProjectName = inbox(page).getByText(LONG_PROJECT_NAME, { exact: true });
		await page.setViewportSize({ width: 390, height: 900 });
		const projectNameSizing = await longProjectName.evaluate((element) => ({
			clientWidth: element.clientWidth,
			scrollWidth: element.scrollWidth,
		}));
		expect(projectNameSizing.scrollWidth).toBeLessThanOrEqual(projectNameSizing.clientWidth + 1);
		await expect(inboxRow(page, LONG_PROJECT_NAME).getByRole("link")).toHaveAccessibleName(
			new RegExp(`Open .* for ${LONG_PROJECT_NAME}`, "i")
		);
	});

	test("keeps distinct snapshots separate while collapsing exact identity duplicates", async ({ page }) => {
		const newerSnapshotItem = {
			...USE_CASE_ITEM,
			work_item_id: "work-mercury-use-cases-newer-snapshot",
			current_snapshot_id: "snapshot-use-cases-mercury-v2",
			reason: "A second snapshot identity remains a separate queue item.",
		};
		await openInbox(page, {
			summary: workspaceSummaryFixture({
				projects: [USE_CASE_PROJECT],
				work_items: [USE_CASE_ITEM, DUPLICATE_USE_CASE_ITEM, newerSnapshotItem],
			}),
		});

		await expect(inboxList(page).getByRole("listitem")).toHaveCount(2);
		await expect(inbox(page)).toContainText(USE_CASE_ITEM.reason);
		await expect(inbox(page)).toContainText(newerSnapshotItem.reason);
	});

	test("navigates Requirement and Test Cases rows into their rendered workbenches", async ({ page }) => {
		await openInbox(page, {
			summary: workspaceSummaryFixture({
				projects: [REQUIREMENTS_PROJECT, TEST_CASE_PROJECT],
				work_items: [REQUIREMENTS_ITEM, TEST_CASE_ITEM],
			}),
		});

		await inboxRow(page, REQUIREMENTS_PROJECT.name).getByRole("link").click();
		await expect(page).toHaveURL(buildProjectPath(REQUIREMENTS_PROJECT.project_id, "requirements"));
		await expect(page.getByRole("heading", { name: /^Upload Requirements$/i })).toBeVisible({ timeout: 30_000 });
		await page
			.getByRole("navigation", { name: /^Global navigation$/i })
			.getByRole("link", { name: /^Reviews$/i })
			.click();
		await expect(inboxRow(page, TEST_CASE_PROJECT.name)).toHaveCount(1);
		await inboxRow(page, TEST_CASE_PROJECT.name).getByRole("link").click();
		await expect(page).toHaveURL(buildProjectPath(TEST_CASE_PROJECT.project_id, "test-cases"));
		await expect(page.getByRole("heading", { name: /^Generate Test Cases$/i })).toBeVisible({ timeout: 30_000 });
	});

	test("removes a completed Use Cases decision after the shared summary refresh", async ({ page }) => {
		const project = useCaseProjectFixture();
		const api = await installUseCaseReviewApi(page, { initialProject: project });
		await seedAuthenticatedSession(page);
		await page.goto("/reviews");
		await expect(page.getByRole("heading", { name: /^Review Inbox$/i, level: 1 })).toBeVisible({ timeout: 30_000 });
		const reviewRow = inboxRow(page, project.name);
		await expect(reviewRow).toHaveCount(1);
		await reviewRow.getByRole("link").click();

		await expect(page).toHaveURL(buildProjectPath(project.project_id, "use-cases"));
		await expect(page.getByRole("heading", { name: /^Use Cases$/i, level: 1 })).toBeVisible({ timeout: 30_000 });
		await page.getByRole("button", { name: /^Approve Use Cases$/i }).click();
		await expect(page.getByRole("form", { name: /^Human review decision$/i }).getByRole("status")).toContainText(/approved/i);

		await page
			.getByRole("navigation", { name: /^Global navigation$/i })
			.getByRole("link", { name: /^Reviews$/i })
			.click();
		await expect(page).toHaveURL(/\/reviews\/?$/);
		await expect(page.getByRole("heading", { name: /^Review queue is clear$/i })).toBeVisible();
		await expect(inbox(page)).not.toContainText(project.name);
		await expect.poll(() => api.requests.workspaceSummary.length).toBeGreaterThanOrEqual(3);
		expect(api.requests.review).toHaveLength(1);
	});
});
