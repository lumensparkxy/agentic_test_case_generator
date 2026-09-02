import { expect, test } from "@playwright/test";

import { buildProjectPath } from "../src/app/workflowRoutes.js";
import { STORAGE_AUTH_TOKEN, STORAGE_AUTH_USER, seedAuthenticatedSession } from "./support/auth.js";
import {
	STORAGE_CURRENT_PROJECT_ID,
	createDeferred,
	installWorkspaceApi,
	projectDetailFixture,
	seedStoredProject,
	workspaceProjectFixture,
	workspaceReportFixture,
	workspaceRunFixture,
	workspaceSummaryFixture,
	workspaceWorkItemFixture,
} from "./support/workspace.js";

const REVIEW_PROJECT = workspaceProjectFixture({
	project_id: "project-mercury",
	name: "Mercury Checkout",
	project_revision: 7,
	current_stage: "use_cases",
	current_status: "attention_required",
	current_snapshot_id: "snapshot-use-cases-mercury",
	completed_stage_count: 2,
	reason: "Three generated use cases need approval.",
	updated_at: "2026-07-17T11:45:00Z",
});

const BLOCKED_PROJECT = workspaceProjectFixture({
	project_id: "project-orbit",
	name: "Orbit Billing",
	project_revision: 4,
	current_stage: "requirements",
	current_status: "blocked",
	completed_stage_count: 0,
	reason: "Requirements approval is missing.",
	updated_at: "2026-07-17T11:55:00Z",
});

const READY_PROJECT = workspaceProjectFixture({
	project_id: "project-atlas",
	name: "Atlas Reports",
	project_revision: 9,
	current_stage: "reports",
	current_status: "ready",
	completed_stage_count: 8,
	reason: "Evidence is ready to report.",
	updated_at: "2026-07-17T11:30:00Z",
});

const SECOND_REVIEW_PROJECT = workspaceProjectFixture({
	project_id: "project-nova",
	name: "Nova Accounts",
	project_revision: 5,
	current_stage: "review",
	current_status: "attention_required",
	completed_stage_count: 6,
	reason: "Test-case evidence needs review.",
	updated_at: "2026-07-17T11:40:00Z",
});

const REVIEW_ITEM = workspaceWorkItemFixture({
	work_item_id: "work-mercury-use-cases",
	kind: "review",
	project_id: REVIEW_PROJECT.project_id,
	project_name: REVIEW_PROJECT.name,
	project_revision: REVIEW_PROJECT.project_revision,
	stage: "use_cases",
	status: "attention_required",
	action: "approve",
	enabled: true,
	primary: true,
	count: 3,
	reason: REVIEW_PROJECT.reason,
	current_snapshot_id: REVIEW_PROJECT.current_snapshot_id,
	updated_at: REVIEW_PROJECT.updated_at,
});

const SECOND_REVIEW_ITEM = workspaceWorkItemFixture({
	work_item_id: "work-nova-review",
	kind: "review",
	project_id: SECOND_REVIEW_PROJECT.project_id,
	project_name: SECOND_REVIEW_PROJECT.name,
	project_revision: SECOND_REVIEW_PROJECT.project_revision,
	stage: "review",
	status: "attention_required",
	action: "review",
	enabled: true,
	primary: true,
	count: 5,
	reason: SECOND_REVIEW_PROJECT.reason,
	current_snapshot_id: "snapshot-test-cases-nova",
	updated_at: SECOND_REVIEW_PROJECT.updated_at,
});

const BLOCKED_ITEM = workspaceWorkItemFixture({
	work_item_id: "work-orbit-requirements",
	kind: "information",
	project_id: BLOCKED_PROJECT.project_id,
	project_name: BLOCKED_PROJECT.name,
	project_revision: BLOCKED_PROJECT.project_revision,
	stage: "requirements",
	status: "blocked",
	action: null,
	enabled: false,
	primary: false,
	count: 6,
	reason: BLOCKED_PROJECT.reason,
	current_snapshot_id: "snapshot-requirements-orbit",
	updated_at: BLOCKED_PROJECT.updated_at,
});

const FAILED_ITEM = workspaceWorkItemFixture({
	work_item_id: "work-orbit-execution",
	kind: "information",
	project_id: BLOCKED_PROJECT.project_id,
	project_name: BLOCKED_PROJECT.name,
	project_revision: BLOCKED_PROJECT.project_revision,
	stage: "execution",
	status: "failed",
	action: null,
	enabled: false,
	primary: false,
	count: 2,
	reason: "Two browser checks failed in staging.",
	current_snapshot_id: "snapshot-execution-orbit",
	updated_at: "2026-07-17T11:35:00Z",
});

const READY_ITEM = workspaceWorkItemFixture({
	work_item_id: "work-atlas-reports",
	kind: "action",
	project_id: READY_PROJECT.project_id,
	project_name: READY_PROJECT.name,
	project_revision: READY_PROJECT.project_revision,
	stage: "reports",
	status: "ready",
	action: "report",
	enabled: true,
	primary: true,
	count: 18,
	reason: READY_PROJECT.reason,
	current_snapshot_id: "snapshot-reports-atlas",
	updated_at: READY_PROJECT.updated_at,
});

const MANY_PROJECT_SUMMARY = workspaceSummaryFixture({
	continue_working: REVIEW_ITEM,
	projects: [BLOCKED_PROJECT, REVIEW_PROJECT, READY_PROJECT, SECOND_REVIEW_PROJECT],
	work_items: [READY_ITEM, BLOCKED_ITEM, REVIEW_ITEM, FAILED_ITEM, SECOND_REVIEW_ITEM],
	recent_runs: [
		workspaceRunFixture({
			run_record_id: "run-record-orbit-failed",
			run_id: "internal-run-id-must-not-render",
			project_id: BLOCKED_PROJECT.project_id,
			project_name: BLOCKED_PROJECT.name,
			project_revision: BLOCKED_PROJECT.project_revision,
			status: "failed",
			passed_count: 7,
			failed_count: 2,
			updated_at: "2026-07-17T11:25:00Z",
		}),
	],
	recent_reports: [
		workspaceReportFixture({
			report_id: "internal-report-id-must-not-render",
			project_id: READY_PROJECT.project_id,
			project_name: READY_PROJECT.name,
			project_revision: READY_PROJECT.project_revision,
			status: "stale",
			approved: true,
			stale: true,
			updated_at: "2026-07-17T11:20:00Z",
		}),
	],
});

const LONG_RUN_STATUS = "completedwithanintentionallylongproviderstatusvaluethatmustremaincontained";
const RESPONSIVE_PROJECT_SUMMARY = workspaceSummaryFixture({
	...MANY_PROJECT_SUMMARY,
	recent_runs: [
		workspaceRunFixture({
			run_record_id: "run-record-orbit-long-status",
			project_id: BLOCKED_PROJECT.project_id,
			project_name: BLOCKED_PROJECT.name,
			project_revision: BLOCKED_PROJECT.project_revision,
			status: LONG_RUN_STATUS,
			updated_at: "2026-07-17T11:25:00Z",
		}),
	],
});

function homeRegion(page, name) {
	return page.getByRole("region", { name: new RegExp(`^${name}$`, "i") });
}

function regionItem(region, projectName) {
	return region.getByRole("listitem").filter({ hasText: projectName });
}

async function expectProjectLink(region, projectName, expectedPath) {
	const item = regionItem(region, projectName);
	await expect(item).toHaveCount(1);
	const link = item.getByRole("link");
	await expect(link).toHaveCount(1);
	await expect(link).toHaveAttribute("href", expectedPath);
	return link;
}

async function expectNoProjectShell(page) {
	await expect(page.getByRole("navigation", { name: "Project navigation" })).toHaveCount(0);
	await expect(page.getByLabel("Project information rail")).toHaveCount(0);
	await expect(page.getByLabel("Contextual task")).toHaveCount(0);
}

async function expectWithinInitialViewport(locator, viewportHeight) {
	const box = await locator.boundingBox();
	expect(box, "Expected the element to have a visible bounding box").not.toBeNull();
	expect(box.y).toBeGreaterThanOrEqual(0);
	expect(box.y + box.height).toBeLessThanOrEqual(viewportHeight);
}

async function expectContainedBy(locator, container) {
	const [box, containerBox] = await Promise.all([locator.boundingBox(), container.boundingBox()]);
	expect(box, "Expected the contained element to have a visible bounding box").not.toBeNull();
	expect(containerBox, "Expected the container to have a visible bounding box").not.toBeNull();
	expect(box.x).toBeGreaterThanOrEqual(containerBox.x - 1);
	expect(box.x + box.width).toBeLessThanOrEqual(containerBox.x + containerBox.width + 1);
}

async function expectProjectOrder(region, projectNames) {
	const items = region.getByRole("listitem");
	await expect(items).toHaveCount(projectNames.length);
	for (const [index, projectName] of projectNames.entries()) {
		await expect(items.nth(index)).toContainText(projectName);
	}
}

async function openCreateProjectForm(page) {
	const nameInput = page.getByRole("textbox", { name: /^Project name$/i });
	if (!(await nameInput.isVisible().catch(() => false))) {
		await page.getByRole("button", { name: /^Create project$/i }).click();
	}
	await expect(nameInput).toBeVisible();
	const form = page.getByRole("form", { name: /^Create project$/i });
	await expect(form).toBeVisible();
	return { form, nameInput };
}

test.describe("Authenticated Home workspace", () => {
	test("uses the Devpost project name in browser metadata", async ({ page }) => {
		await installWorkspaceApi(page, { summary: workspaceSummaryFixture() });
		await seedAuthenticatedSession(page);

		await page.goto("/");

		await expect(page).toHaveTitle("Test Engineer Agent");
	});

	for (const viewport of [
		{ width: 320, height: 900 },
		{ width: 390, height: 844 },
		{ width: 640, height: 900 },
		{ width: 760, height: 900 },
	]) {
		test(`keeps the zero-project heading and Create Project CTA above the fold at ${viewport.width}px`, async ({ page }) => {
			await page.setViewportSize(viewport);
			const api = await installWorkspaceApi(page, { summary: workspaceSummaryFixture() });
			await seedAuthenticatedSession(page);

			await page.goto("/");

			const heading = page.getByRole("heading", { name: /^Home$/i });
			const createProjectCta = page
				.getByRole("button", { name: /^Create project$/i })
				.or(page.getByRole("link", { name: /^Create project$/i }))
				.first();
			await expect(heading).toBeVisible({ timeout: 30_000 });
			await expect(page.getByRole("heading", { name: /^Create your first QA project$/i })).toBeVisible();
			await expect(createProjectCta).toBeVisible();
			await expectWithinInitialViewport(heading, viewport.height);
			await expectWithinInitialViewport(createProjectCta, viewport.height);
			await expectNoProjectShell(page);
			await expect.poll(() => api.requests.workspaceSummary.length).toBe(1);

			const overflow = await page.evaluate(() => ({
				clientWidth: document.documentElement.clientWidth,
				scrollWidth: document.documentElement.scrollWidth,
			}));
			expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 2);
		});
	}

	for (const viewport of [
		{ width: 320, height: 900 },
		{ width: 390, height: 844 },
		{ width: 640, height: 900 },
		{ width: 760, height: 900 },
		{ width: 900, height: 900 },
		{ width: 1280, height: 900 },
		{ width: 1440, height: 900 },
		{ width: 1920, height: 1080 },
	]) {
		test(`keeps populated Home task regions and free-form statuses contained at ${viewport.width}px`, async ({ page }) => {
			await page.setViewportSize(viewport);
			await installWorkspaceApi(page, { summary: RESPONSIVE_PROJECT_SUMMARY });
			await seedAuthenticatedSession(page);

			await page.goto("/");

			await expect(page.getByRole("heading", { name: /^Home$/i })).toBeVisible({ timeout: 30_000 });
			await expect(homeRegion(page, "Continue working")).toBeVisible();
			await expect(homeRegion(page, "My work")).toBeVisible();
			await expect(homeRegion(page, "Projects")).toBeVisible();
			const recentActivity = homeRegion(page, "Recent activity");
			await expect(recentActivity).toBeVisible();
			const runItem = recentActivity.getByRole("listitem").filter({ hasText: BLOCKED_PROJECT.name });
			const longStatus = runItem.locator(".workspace-status");
			await expect(longStatus).toContainText(LONG_RUN_STATUS, { ignoreCase: true });
			await expectContainedBy(longStatus, runItem);
			await expectNoProjectShell(page);

			const overflow = await page.evaluate(() => ({
				clientWidth: document.documentElement.clientWidth,
				scrollWidth: document.documentElement.scrollWidth,
			}));
			expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 2);
		});
	}

	test("uses the server-ranked Continue item for a one-project returning user and returns Home through browser history", async ({
		page,
	}) => {
		const summary = workspaceSummaryFixture({
			continue_working: REVIEW_ITEM,
			projects: [REVIEW_PROJECT],
			work_items: [REVIEW_ITEM],
		});
		await installWorkspaceApi(page, { summary });
		await seedAuthenticatedSession(page);
		await seedStoredProject(page, REVIEW_PROJECT.project_id);

		await page.goto("/");

		await expect(page).toHaveURL(/\/$/);
		await expect(page.getByRole("heading", { name: /^Home$/i })).toBeVisible({ timeout: 30_000 });
		const continueRegion = homeRegion(page, "Continue working");
		await expect(continueRegion).toContainText(REVIEW_PROJECT.name);
		await expect(continueRegion).toContainText("3");
		await expect(continueRegion).toContainText(REVIEW_ITEM.reason);
		const continueLink = continueRegion.getByRole("link");
		await expect(continueLink).toHaveCount(1);
		await expect(continueLink).toHaveAttribute("href", buildProjectPath(REVIEW_PROJECT.project_id, "use-cases"));
		await expectNoProjectShell(page);

		await continueLink.click();
		await expect(page).toHaveURL(buildProjectPath(REVIEW_PROJECT.project_id, "use-cases"));
		await expect(page.getByRole("heading", { name: /^Use Cases$/i })).toBeVisible({ timeout: 30_000 });

		await page.goBack();
		await expect(page).toHaveURL(/\/$/);
		await expect(page.getByRole("heading", { name: /^Home$/i })).toBeVisible();
		await expectNoProjectShell(page);
	});

	test("refreshes the authoritative workspace ranking when returning from project work", async ({ page }) => {
		const updatedSummary = workspaceSummaryFixture({
			...MANY_PROJECT_SUMMARY,
			continue_working: READY_ITEM,
		});
		const api = await installWorkspaceApi(page, {
			summary: MANY_PROJECT_SUMMARY,
			summaryScenarios: [{ payload: MANY_PROJECT_SUMMARY }, { payload: updatedSummary }],
		});
		await seedAuthenticatedSession(page);

		await page.goto("/");
		await expect(homeRegion(page, "Continue working")).toContainText(REVIEW_PROJECT.name, { timeout: 30_000 });
		await homeRegion(page, "Continue working").getByRole("link").click();
		await expect(page).toHaveURL(buildProjectPath(REVIEW_PROJECT.project_id, "use-cases"));
		await page
			.getByRole("navigation", { name: "Global navigation" })
			.getByRole("link", { name: /^Home$/i })
			.click();

		await expect(page).toHaveURL(/\/$/);
		await expect.poll(() => api.requests.workspaceSummary.length).toBe(2);
		await expect(homeRegion(page, "Continue working")).toContainText(READY_PROJECT.name);
		await expect(homeRegion(page, "Continue working")).not.toContainText(REVIEW_PROJECT.name);
	});

	test("preserves server ordering, stable My work groups, precise links, and bounded activity for many projects", async ({ page }) => {
		const api = await installWorkspaceApi(page, { summary: MANY_PROJECT_SUMMARY });
		await seedAuthenticatedSession(page);
		await seedStoredProject(page, BLOCKED_PROJECT.project_id);

		await page.goto("/");

		await expect(page.getByRole("heading", { name: /^Home$/i })).toBeVisible({ timeout: 30_000 });
		const continueRegion = homeRegion(page, "Continue working");
		await expect(continueRegion).toContainText(REVIEW_PROJECT.name);
		await expect(continueRegion).not.toContainText(BLOCKED_PROJECT.name);
		await expect(continueRegion.getByRole("link")).toHaveAttribute("href", buildProjectPath(REVIEW_PROJECT.project_id, "use-cases"));

		const myWork = homeRegion(page, "My work");
		const groups = myWork.getByRole("region");
		await expect(groups).toHaveCount(3);
		await expect(groups.nth(0)).toHaveAccessibleName(/^Needs review$/i);
		await expect(groups.nth(1)).toHaveAccessibleName(/^Needs attention$/i);
		await expect(groups.nth(2)).toHaveAccessibleName(/^Ready next$/i);

		const reviewGroup = myWork.getByRole("region", { name: /^Needs review$/i });
		await expectProjectOrder(reviewGroup, [REVIEW_PROJECT.name, SECOND_REVIEW_PROJECT.name]);
		await expectProjectLink(reviewGroup, REVIEW_PROJECT.name, buildProjectPath(REVIEW_PROJECT.project_id, "use-cases"));
		await expectProjectLink(reviewGroup, SECOND_REVIEW_PROJECT.name, buildProjectPath(SECOND_REVIEW_PROJECT.project_id, "test-cases"));

		const attentionGroup = myWork.getByRole("region", { name: /^Needs attention$/i });
		await expectProjectOrder(attentionGroup, [BLOCKED_PROJECT.name, BLOCKED_PROJECT.name]);
		const attentionItems = attentionGroup.getByRole("listitem");
		await expect(attentionItems.nth(0).getByRole("link")).toHaveAttribute(
			"href",
			buildProjectPath(BLOCKED_PROJECT.project_id, "requirements")
		);
		await expect(attentionItems.nth(1).getByRole("link")).toHaveAttribute(
			"href",
			buildProjectPath(BLOCKED_PROJECT.project_id, "automation")
		);

		const readyGroup = myWork.getByRole("region", { name: /^Ready next$/i });
		await expectProjectLink(readyGroup, READY_PROJECT.name, buildProjectPath(READY_PROJECT.project_id, "reports"));

		const projectsRegion = homeRegion(page, "Projects");
		await expectProjectOrder(projectsRegion, [BLOCKED_PROJECT.name, REVIEW_PROJECT.name, READY_PROJECT.name, SECOND_REVIEW_PROJECT.name]);
		const recentActivity = homeRegion(page, "Recent activity");
		await expect(recentActivity).toContainText(BLOCKED_PROJECT.name);
		await expect(recentActivity).toContainText(READY_PROJECT.name);
		await expect(page.getByText("internal-run-id-must-not-render", { exact: true })).toHaveCount(0);
		await expect(page.getByText("internal-report-id-must-not-render", { exact: true })).toHaveCount(0);
		await expect(page.getByText(REVIEW_PROJECT.current_snapshot_id, { exact: true })).toHaveCount(0);

		await expect.poll(() => api.requests.workspaceSummary.length).toBe(1);
		const workspaceRequest = new URL(api.requests.workspaceSummary[0].url);
		expect(Object.fromEntries(workspaceRequest.searchParams)).toMatchObject({
			include_archived: "false",
			projects_limit: "20",
			work_items_limit: "50",
			runs_limit: "20",
			reports_limit: "20",
		});
	});

	test("removes a missing stored project without hydrating stale project state", async ({ page }) => {
		const api = await installWorkspaceApi(page, {
			summary: workspaceSummaryFixture({
				continue_working: REVIEW_ITEM,
				projects: [REVIEW_PROJECT],
				work_items: [REVIEW_ITEM],
			}),
		});
		await seedAuthenticatedSession(page);
		await seedStoredProject(page, "project-no-longer-present");

		await page.goto("/");

		await expect(page.getByRole("heading", { name: /^Home$/i })).toBeVisible({ timeout: 30_000 });
		await expect.poll(() => page.evaluate((key) => window.localStorage.getItem(key), STORAGE_CURRENT_PROJECT_ID)).toBeNull();
		await expect(homeRegion(page, "Continue working")).toContainText(REVIEW_PROJECT.name);
		await expectNoProjectShell(page);
		expect(api.requests.projectDetail).toEqual([]);
	});

	test("preserves the Home information architecture while the summary is loading", async ({ page }) => {
		const deferred = createDeferred();
		await installWorkspaceApi(page, {
			summary: MANY_PROJECT_SUMMARY,
			summaryScenarios: [{ gate: deferred.promise, payload: MANY_PROJECT_SUMMARY }],
		});
		await seedAuthenticatedSession(page);

		await page.goto("/");

		await expect(page.getByRole("heading", { name: /^Home$/i })).toBeVisible({ timeout: 30_000 });
		const loadingStatus = page.getByRole("status", { name: /^Loading workspace$/i });
		await expect(loadingStatus).toBeVisible();
		await expect(loadingStatus).toHaveAttribute("aria-live", "polite");
		await expect(loadingStatus).toHaveAttribute("aria-busy", "true");
		await expectNoProjectShell(page);

		deferred.resolve();
		await expect(loadingStatus).toHaveCount(0);
		await expect(homeRegion(page, "Continue working")).toContainText(REVIEW_PROJECT.name);
		await expect(homeRegion(page, "My work")).toBeVisible();
		await expect(homeRegion(page, "Projects")).toBeVisible();
		await expect(homeRegion(page, "Recent activity")).toBeVisible();
	});

	test("recovers from a workspace summary failure through Retry", async ({ page }) => {
		const api = await installWorkspaceApi(page, {
			summary: MANY_PROJECT_SUMMARY,
			summaryScenarios: [
				{ status: 503, payload: { detail: "Workspace summary is unavailable" } },
				{ status: 200, payload: MANY_PROJECT_SUMMARY },
			],
		});
		await seedAuthenticatedSession(page);

		await page.goto("/");

		await expect(page.getByRole("heading", { name: /^Home$/i })).toBeVisible({ timeout: 30_000 });
		const alert = page.getByRole("alert");
		await expect(alert).toContainText("Workspace summary is unavailable");
		await alert.getByRole("button", { name: /^Retry$/i }).click();
		await expect.poll(() => api.requests.workspaceSummary.length).toBe(2);
		await expect(alert).toHaveCount(0);
		await expect(homeRegion(page, "Continue working")).toContainText(REVIEW_PROJECT.name);
	});

	test("removes the authenticated subject's workspace summary on logout", async ({ page }) => {
		await installWorkspaceApi(page, { summary: MANY_PROJECT_SUMMARY });
		await seedAuthenticatedSession(page);

		await page.goto("/");
		await expect(homeRegion(page, "Continue working")).toContainText(REVIEW_PROJECT.name, { timeout: 30_000 });
		await page.getByRole("button", { name: /^Open account menu/i }).click();
		await page
			.getByRole("menu", { name: /^Account menu$/i })
			.getByRole("menuitem", { name: /^Sign Out$/i })
			.click();

		await expect(page.locator(".auth-warning-banner")).toBeVisible();
		await expect(page.getByText(REVIEW_PROJECT.name, { exact: true })).toHaveCount(0);
		await expect(page.getByText(BLOCKED_PROJECT.name, { exact: true })).toHaveCount(0);
	});

	test("ignores a delayed prior-user project list after logout", async ({ page }) => {
		const projectListGate = createDeferred();
		const api = await installWorkspaceApi(page, {
			summary: MANY_PROJECT_SUMMARY,
			projectListGate: projectListGate.promise,
		});
		await seedAuthenticatedSession(page);

		await page.goto("/");
		await expect(homeRegion(page, "Continue working")).toContainText(REVIEW_PROJECT.name, { timeout: 30_000 });
		await expect.poll(() => api.requests.projectList.length).toBe(1);
		await page.getByRole("button", { name: /^Open QA project menu$/i }).click();
		const projectDialog = page.getByRole("dialog", { name: /^Projects$/i });
		await expect(projectDialog).toContainText("No projects yet");
		await page.getByRole("button", { name: /^Open account menu/i }).evaluate((button) => button.click());
		await page
			.getByRole("menu", { name: /^Account menu$/i })
			.getByRole("menuitem", { name: /^Sign Out$/i })
			.evaluate((button) => button.click());
		await expect(page.locator(".auth-warning-banner")).toBeVisible();

		const projectListResponse = page.waitForResponse(
			(response) => new URL(response.url()).pathname === "/projects" && response.request().method() === "GET"
		);
		projectListGate.resolve();
		await projectListResponse;
		await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

		await expect(projectDialog).toBeVisible();
		await expect(projectDialog).toContainText("No projects yet");
		await expect(projectDialog.getByText(REVIEW_PROJECT.name, { exact: true })).toHaveCount(0);
		await expect(projectDialog.getByText(BLOCKED_PROJECT.name, { exact: true })).toHaveCount(0);
	});
});

test.describe("Authenticated Projects experience", () => {
	test("filters the bounded server list client-side and focuses search with Ctrl+K and Cmd+K", async ({ page }) => {
		const api = await installWorkspaceApi(page, { summary: MANY_PROJECT_SUMMARY });
		await seedAuthenticatedSession(page);

		await page.goto("/projects");

		await expect(page.getByRole("heading", { name: /^Projects$/i, level: 1 })).toBeVisible({ timeout: 30_000 });
		const search = page.getByRole("searchbox", { name: /^Search projects$/i });
		await expect(search).toBeVisible();
		const projectsRegion = page.getByRole("region", { name: /^Projects$/i });
		await expectProjectOrder(projectsRegion, [BLOCKED_PROJECT.name, REVIEW_PROJECT.name, READY_PROJECT.name, SECOND_REVIEW_PROJECT.name]);
		await expect.poll(() => api.requests.workspaceSummary.length).toBe(1);

		await search.fill("Mercury");
		await expectProjectOrder(projectsRegion, [REVIEW_PROJECT.name]);
		await expect.poll(() => api.requests.workspaceSummary.length).toBe(1);

		for (const shortcut of ["Control+K", "Meta+K"]) {
			await page.getByRole("heading", { name: /^Projects$/i, level: 1 }).click();
			await page.keyboard.press(shortcut);
			await expect(search).toBeFocused();
		}
	});

	test("opens an existing project and persists its selection", async ({ page }) => {
		await installWorkspaceApi(page, { summary: MANY_PROJECT_SUMMARY });
		await seedAuthenticatedSession(page);

		await page.goto("/projects");
		await expect(page.getByRole("heading", { name: /^Projects$/i, level: 1 })).toBeVisible({ timeout: 30_000 });
		const projectsRegion = page.getByRole("region", { name: /^Projects$/i });
		const openLink = await expectProjectLink(projectsRegion, READY_PROJECT.name, buildProjectPath(READY_PROJECT.project_id, "reports"));

		await openLink.click();
		await expect(page).toHaveURL(buildProjectPath(READY_PROJECT.project_id, "reports"));
		await expect(page.getByRole("heading", { name: /^Export Test Cases$/i })).toBeVisible({ timeout: 30_000 });
		await expect
			.poll(() => page.evaluate((key) => window.localStorage.getItem(key), STORAGE_CURRENT_PROJECT_ID))
			.toBe(READY_PROJECT.project_id);
	});

	test("clears the selected project back to Home without leaving project state visible", async ({ page }) => {
		await installWorkspaceApi(page, { summary: MANY_PROJECT_SUMMARY });
		await seedAuthenticatedSession(page);

		await page.goto(buildProjectPath(READY_PROJECT.project_id, "reports"));
		await expect(page.getByRole("heading", { name: /^Export Test Cases$/i })).toBeVisible({ timeout: 30_000 });
		await expect
			.poll(() => page.evaluate((key) => window.localStorage.getItem(key), STORAGE_CURRENT_PROJECT_ID))
			.toBe(READY_PROJECT.project_id);

		await page.getByRole("button", { name: /^Open QA project menu$/i }).click();
		const projectDialog = page.getByRole("dialog", { name: /^Projects$/i });
		await projectDialog.getByRole("button", { name: /^Clear selection$/i }).click();

		await expect(page).toHaveURL(/\/$/);
		await expect(page.getByRole("heading", { name: /^Home$/i })).toBeVisible();
		await expect.poll(() => page.evaluate((key) => window.localStorage.getItem(key), STORAGE_CURRENT_PROJECT_ID)).toBeNull();
		await expect(page.getByRole("heading", { name: /^Export Test Cases$/i })).toHaveCount(0);
		await expectNoProjectShell(page);
	});

	test("creates a project, persists it, and keeps Home as the landing route", async ({ page }) => {
		const createdProject = projectDetailFixture(
			workspaceProjectFixture({
				project_id: "project-created-home",
				name: "Created from Home",
				project_revision: 0,
				updated_at: "2026-07-17T12:30:00Z",
			})
		);
		const api = await installWorkspaceApi(page, {
			summary: workspaceSummaryFixture(),
			createProject: createdProject,
		});
		await seedAuthenticatedSession(page);

		await page.goto("/projects");
		await expect(page.getByRole("heading", { name: /^Projects$/i, level: 1 })).toBeVisible({ timeout: 30_000 });
		const { form, nameInput } = await openCreateProjectForm(page);
		await nameInput.fill(createdProject.name);
		await form.getByRole("button", { name: /^Create project$/i }).click();

		await expect.poll(() => api.requests.projectCreate.length).toBe(1);
		expect(api.requests.projectCreate[0].payload).toEqual({ name: createdProject.name });
		await expect(page).toHaveURL(buildProjectPath(createdProject.project_id));
		await expect(page.getByRole("heading", { name: new RegExp(`^${createdProject.name}$`, "i") })).toBeVisible({ timeout: 30_000 });
		await expect
			.poll(() => page.evaluate((key) => window.localStorage.getItem(key), STORAGE_CURRENT_PROJECT_ID))
			.toBe(createdProject.project_id);

		await page
			.getByRole("navigation", { name: "Global navigation" })
			.getByRole("link", { name: /^Home$/i })
			.click();
		await expect(page).toHaveURL(/\/$/);
		await expect(page.getByRole("heading", { name: /^Home$/i })).toBeVisible();
		await expect(homeRegion(page, "Continue working")).toContainText(createdProject.name);
		await expectNoProjectShell(page);
	});

	test("keeps a newly created project when the initial project list resolves last", async ({ page }) => {
		const initialProjectListGate = createDeferred();
		const createdProject = projectDetailFixture(
			workspaceProjectFixture({
				project_id: "project-latest-list",
				name: "Latest Project List",
				project_revision: 0,
				updated_at: "2026-07-17T12:35:00Z",
			})
		);
		const api = await installWorkspaceApi(page, {
			summary: workspaceSummaryFixture(),
			createProject: createdProject,
			projectListScenarios: [{ gate: initialProjectListGate.promise, projects: [] }],
		});
		await seedAuthenticatedSession(page);

		await page.goto("/projects");
		await expect(page.getByRole("heading", { name: /^Projects$/i, level: 1 })).toBeVisible({ timeout: 30_000 });
		await expect.poll(() => api.requests.projectList.length).toBe(1);
		const { form, nameInput } = await openCreateProjectForm(page);
		await nameInput.fill(createdProject.name);
		await form.getByRole("button", { name: /^Create project$/i }).click();

		await expect.poll(() => api.requests.projectList.length).toBe(2);
		await expect(page).toHaveURL(buildProjectPath(createdProject.project_id));
		await expect
			.poll(() => page.evaluate((key) => window.localStorage.getItem(key), STORAGE_CURRENT_PROJECT_ID))
			.toBe(createdProject.project_id);

		const delayedListResponse = page.waitForResponse(
			(response) => new URL(response.url()).pathname === "/projects" && response.request().method() === "GET"
		);
		initialProjectListGate.resolve();
		await delayedListResponse;

		await page.getByRole("button", { name: /^Open QA project menu$/i }).click();
		const projectDialog = page.getByRole("dialog", { name: /^Projects$/i });
		await expect(projectDialog).toContainText(createdProject.name);
		await expect
			.poll(() => page.evaluate((key) => window.localStorage.getItem(key), STORAGE_CURRENT_PROJECT_ID))
			.toBe(createdProject.project_id);
	});

	test("reconciles a delayed successful create after the user returns Home without redirecting", async ({ page }) => {
		const createGate = createDeferred();
		const createdProject = projectDetailFixture(
			workspaceProjectFixture({ project_id: "project-stale-route", name: "Stale Route Project", project_revision: 0 })
		);
		const api = await installWorkspaceApi(page, {
			summary: workspaceSummaryFixture(),
			createProject: createdProject,
			createGate: createGate.promise,
		});
		await seedAuthenticatedSession(page);

		await page.goto("/projects");
		const { form, nameInput } = await openCreateProjectForm(page);
		await nameInput.fill(createdProject.name);
		await form.getByRole("button", { name: /^Create project$/i }).click();
		await expect.poll(() => api.requests.projectCreate.length).toBe(1);
		await page
			.getByRole("navigation", { name: "Global navigation" })
			.getByRole("link", { name: /^Home$/i })
			.click();

		const createResponse = page.waitForResponse(
			(response) => new URL(response.url()).pathname === "/projects" && response.request().method() === "POST"
		);
		createGate.resolve();
		await createResponse;
		await expect.poll(() => api.requests.workspaceSummary.length).toBe(2);

		await expect(page).toHaveURL(/\/$/);
		await expect(page.getByRole("heading", { name: /^Home$/i })).toBeVisible();
		await expect(homeRegion(page, "Continue working")).toContainText(createdProject.name);
		await expect(page.getByRole("heading", { name: /^Create your first QA project$/i })).toHaveCount(0);
		await expect.poll(() => page.evaluate((key) => window.localStorage.getItem(key), STORAGE_CURRENT_PROJECT_ID)).toBeNull();
		await expectNoProjectShell(page);
	});

	test("ignores a delayed create response after logout", async ({ page }) => {
		const createGate = createDeferred();
		const createdProject = projectDetailFixture(
			workspaceProjectFixture({ project_id: "project-stale-user", name: "Stale User Project", project_revision: 0 })
		);
		const api = await installWorkspaceApi(page, {
			summary: workspaceSummaryFixture(),
			createProject: createdProject,
			createGate: createGate.promise,
		});
		await seedAuthenticatedSession(page);

		await page.goto("/projects");
		const { form, nameInput } = await openCreateProjectForm(page);
		await nameInput.fill(createdProject.name);
		await form.getByRole("button", { name: /^Create project$/i }).click();
		await expect.poll(() => api.requests.projectCreate.length).toBe(1);
		await page.getByRole("button", { name: /^Open account menu/i }).click();
		await page
			.getByRole("menu", { name: /^Account menu$/i })
			.getByRole("menuitem", { name: /^Sign Out$/i })
			.click();
		await expect(page.locator(".auth-warning-banner")).toBeVisible();

		const createResponse = page.waitForResponse(
			(response) => new URL(response.url()).pathname === "/projects" && response.request().method() === "POST"
		);
		createGate.resolve();
		await createResponse;
		await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

		await expect(page.getByRole("navigation", { name: "Project navigation" })).toHaveCount(0);
		await expect
			.poll(() =>
				page.evaluate(
					({ projectKey, tokenKey, userKey }) => ({
						project: window.localStorage.getItem(projectKey),
						token: window.localStorage.getItem(tokenKey),
						user: window.localStorage.getItem(userKey),
					}),
					{ projectKey: STORAGE_CURRENT_PROJECT_ID, tokenKey: STORAGE_AUTH_TOKEN, userKey: STORAGE_AUTH_USER }
				)
			)
			.toEqual({ project: null, token: null, user: null });
	});

	test("keeps create-project validation errors recoverable", async ({ page }) => {
		const duplicateName = "Mercury Checkout";
		const api = await installWorkspaceApi(page, {
			summary: MANY_PROJECT_SUMMARY,
			createErrorsByName: {
				[duplicateName]: { status: 422, detail: "A project with this name already exists." },
			},
		});
		await seedAuthenticatedSession(page);

		await page.goto("/projects");
		await expect(page.getByRole("heading", { name: /^Projects$/i, level: 1 })).toBeVisible({ timeout: 30_000 });
		const { form, nameInput } = await openCreateProjectForm(page);
		await nameInput.fill(duplicateName);
		await form.getByRole("button", { name: /^Create project$/i }).click();

		await expect.poll(() => api.requests.projectCreate.length).toBe(1);
		await expect(page).toHaveURL(/\/projects\/?$/);
		await expect(page.getByRole("alert")).toContainText("A project with this name already exists.");
		await expect(nameInput).toHaveValue(duplicateName);
		await expect.poll(() => page.evaluate((key) => window.localStorage.getItem(key), STORAGE_CURRENT_PROJECT_ID)).toBeNull();
	});
});
