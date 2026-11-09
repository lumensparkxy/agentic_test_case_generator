import { expect, test } from "@playwright/test";

import { buildProjectPath } from "../src/app/workflowRoutes.js";
import { expectNoSeriousOrCriticalViolations } from "./support/accessibility.js";
import { seedAuthenticatedSession } from "./support/auth.js";
import { settleLayout } from "./support/layout.js";
import { USE_CASE_PROJECT_ID, installUseCaseReviewApi, useCaseProjectFixture } from "./support/use-case-review.js";
import { createDeferred, installWorkspaceApi, seedStoredProject, workspaceSummaryFixture } from "./support/workspace.js";

const surfaceRoutes = {
	"empty Home": "/",
	"populated Home": "/",
	"project Overview": buildProjectPath(USE_CASE_PROJECT_ID),
	"Use Cases": buildProjectPath(USE_CASE_PROJECT_ID, "use-cases"),
	Automation: buildProjectPath(USE_CASE_PROJECT_ID, "automation"),
};

const surfaceHeadings = {
	"empty Home": "Home",
	"populated Home": "Home",
	"project Overview": "Overview",
	"Use Cases": "Use Cases",
	Automation: "Automation",
};

async function openSurface(page, surface, width) {
	await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
	await page.emulateMedia({ reducedMotion: "reduce" });
	if (surface === "empty Home") {
		await installWorkspaceApi(page, { summary: workspaceSummaryFixture() });
	} else {
		await installUseCaseReviewApi(page, { initialProject: useCaseProjectFixture() });
	}
	await seedAuthenticatedSession(page);
	await page.goto(surfaceRoutes[surface]);
	await expect(page.getByRole("heading", { name: surfaceHeadings[surface], level: 1 })).toBeVisible({
		timeout: 30_000,
	});
	if (surface === "empty Home") {
		await expect(page.getByRole("heading", { name: /^Create your first QA project$/i })).toBeVisible();
	}
	if (surface === "populated Home") {
		await expect(page.locator(".workspace-home-content")).toBeVisible();
	}
}

for (const width of [390, 1440]) {
	for (const surface of Object.keys(surfaceRoutes)) {
		test(
			`${surface} has no serious or critical WCAG A/AA violations at ${width}px`,
			{ tag: surface === "Use Cases" && width === 1440 ? "@p1" : [] },
			async ({ page }) => {
				await openSurface(page, surface, width);
				await expectNoSeriousOrCriticalViolations(page, `${surface} at ${width}px`);
			}
		);
	}
}

test("starts at the global skip link and never hydrates stale project workflow on an empty Home", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await installWorkspaceApi(page, { summary: workspaceSummaryFixture() });
	await seedAuthenticatedSession(page);
	await seedStoredProject(page, "removed-project");
	await page.goto("/");
	await expect(page.getByRole("heading", { name: /^Home$/i, level: 1 })).toBeVisible({ timeout: 30_000 });

	const skipLink = page.getByRole("link", { name: /^Skip to main content$/i });
	await expect.poll(() => page.evaluate(() => document.activeElement === document.body)).toBe(true);
	await page.keyboard.press("Tab");
	await expect(skipLink).toBeFocused();
	await expect(skipLink).toBeVisible();
	await expect(skipLink).toHaveAttribute("href", "#main-content");
	await page.keyboard.press("Enter");
	await expect(page.locator("#main-content")).toBeFocused();

	await expect(page.getByRole("navigation", { name: /^Project navigation$/i })).toHaveCount(0);
	await expect(page.getByRole("main", { name: /^Workflow workspace$/i })).toHaveCount(0);
	await expect(page.getByText(/Export locked by review gate/i)).toHaveCount(0);
	await expect(page.getByText(/^Needs review$/i)).toHaveCount(0);
});

test("moves focus to each SPA destination through links, Back, and Forward", async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 1000 });
	await installUseCaseReviewApi(page, { initialProject: useCaseProjectFixture() });
	await seedAuthenticatedSession(page);
	await page.goto("/");
	await expect(page.getByRole("heading", { name: /^Home$/i, level: 1 })).toBeVisible({ timeout: 30_000 });

	await page
		.getByRole("navigation", { name: /^Global navigation$/i })
		.getByRole("link", { name: /^Projects$/i })
		.click();
	await expect(page).toHaveURL(/\/projects\/?$/);
	await expect(page.locator("#main-content")).toBeFocused();

	await page
		.locator("#main-content")
		.getByRole("link", { name: /^Open$/i })
		.click();
	await expect(page).toHaveURL(buildProjectPath(USE_CASE_PROJECT_ID, "use-cases"));
	await expect(page.locator("#main-content")).toBeFocused();

	const projectNavigation = page.getByRole("navigation", { name: /^Project navigation$/i });
	await projectNavigation.getByRole("link", { name: /^Overview$/i }).click();
	await expect(page).toHaveURL(buildProjectPath(USE_CASE_PROJECT_ID));
	await expect(page.locator("#main-content")).toBeFocused();

	await projectNavigation.getByRole("link", { name: /^Use Cases,/i }).click();
	await expect(page).toHaveURL(buildProjectPath(USE_CASE_PROJECT_ID, "use-cases"));
	await expect(page.locator("#main-content")).toBeFocused();

	await projectNavigation.getByRole("link", { name: /^Automation,/i }).click();
	await expect(page).toHaveURL(buildProjectPath(USE_CASE_PROJECT_ID, "automation"));
	await expect(page.locator("#main-content")).toBeFocused();
	await expect(page.locator("#main-content")).toHaveAttribute("aria-label", "Workflow workspace: Automation");

	await page.goBack();
	await expect(page).toHaveURL(buildProjectPath(USE_CASE_PROJECT_ID, "use-cases"));
	await expect(page.locator("#main-content")).toBeFocused();
	await page.goForward();
	await expect(page).toHaveURL(buildProjectPath(USE_CASE_PROJECT_ID, "automation"));
	await expect(page.locator("#main-content")).toBeFocused();

	await projectNavigation.getByRole("link", { name: /^Reports,/i }).click();
	await expect(page).toHaveURL(buildProjectPath(USE_CASE_PROJECT_ID, "reports"));
	await expect(page.locator("#main-content")).toBeFocused();
	await expect(page.locator("#main-content")).toHaveAttribute("aria-label", "Workflow workspace: Reports");

	await page.goBack();
	await expect(page).toHaveURL(buildProjectPath(USE_CASE_PROJECT_ID, "automation"));
	await expect(page.locator("#main-content")).toBeFocused();
	await expect(page.locator("#main-content")).toHaveAttribute("aria-label", "Workflow workspace: Automation");
	await page.goForward();
	await expect(page).toHaveURL(buildProjectPath(USE_CASE_PROJECT_ID, "reports"));
	await expect(page.locator("#main-content")).toBeFocused();
	await expect(page.locator("#main-content")).toHaveAttribute("aria-label", "Workflow workspace: Reports");
});

test("opens a deferred direct link with parallel project data and preserves skip-link focus", { tag: "@p1" }, async ({ page }) => {
	await installUseCaseReviewApi(page, { initialProject: useCaseProjectFixture() });
	await seedAuthenticatedSession(page);
	const data = createDeferred();
	const module = createDeferred();
	let requestedData = false;
	let requestedModule = false;
	await page.route(
		(url) => url.pathname === `/projects/${USE_CASE_PROJECT_ID}`,
		async (route) => {
			requestedData = true;
			await data.promise;
			await route.fallback();
		}
	);
	await page.route("**/src/pages/UseCaseReviewPage.jsx*", async (route) => {
		requestedModule = true;
		await module.promise;
		await route.continue();
	});
	try {
		await page.goto(buildProjectPath(USE_CASE_PROJECT_ID, "use-cases"), { waitUntil: "domcontentloaded" });
		await expect.poll(() => [requestedData, requestedModule]).toEqual([true, true]);
		await expect(page.getByRole("navigation", { name: "Global navigation" })).toBeVisible();
		data.resolve();
		const main = page.locator("#main-content");
		await expect(main.getByRole("status")).toHaveText("Loading Use Cases…");
		await expect(page.getByRole("navigation", { name: "Project navigation" })).toBeVisible();
		await page.getByRole("link", { name: "Skip to main content" }).focus();
		await page.keyboard.press("Enter");
		await expect(main).toBeFocused();
		module.resolve();
		await expect(main.getByRole("heading", { name: "Use Cases", exact: true, level: 1 })).toBeVisible();
		await expect(main).toBeFocused();
	} finally {
		data.resolve();
		module.resolve();
	}
});

test("keeps navigation available after a workspace module fails and reloads only on request", { tag: "@p1" }, async ({ page }) => {
	await installUseCaseReviewApi(page, { initialProject: useCaseProjectFixture() });
	await seedAuthenticatedSession(page);
	const reportsModule = "**/src/pages/ReportsPage.jsx*";
	await page.route(reportsModule, (route) => route.abort("failed"));
	let documentLoads = 0;
	page.on("request", (request) => {
		if (request.resourceType() === "document") documentLoads++;
	});
	await page.goto("/");
	const nav = page.getByRole("navigation", { name: "Global navigation" });
	const main = page.locator("#main-content");
	const alert = main.getByRole("alert");
	await nav.getByRole("link", { name: "Reports", exact: true }).click();
	await expect(alert).toContainText("Couldn’t load Reports.");
	await expect(main).toBeFocused();
	await nav.getByRole("link", { name: "Home", exact: true }).click();
	await expect(main.getByRole("heading", { name: "Home", exact: true })).toBeVisible();
	await nav.getByRole("link", { name: "Reports", exact: true }).click();
	await expect(alert).toBeVisible();
	expect(documentLoads).toBe(1);
	await page.unroute(reportsModule);
	await alert.getByRole("button", { name: "Reload page", exact: true }).click();
	await expect(main.getByRole("heading", { name: "Reports", exact: true, level: 1 })).toBeVisible();
	expect(documentLoads).toBe(2);
});

test("keeps compact workspace controls open when Escape closes a nested control", { tag: "@p1" }, async ({ page }) => {
	await openSurface(page, "project Overview", 390);

	const workspaceToggle = page.getByRole("button", { name: /^Open workspace controls$/i });
	await workspaceToggle.focus();
	await page.keyboard.press("Enter");
	await expect(page.getByRole("button", { name: /^Close workspace controls$/i })).toHaveAttribute("aria-expanded", "true");

	const projectTrigger = page.getByRole("button", { name: /^Open QA project menu$/i });
	await projectTrigger.focus();
	await page.keyboard.press("Space");
	const projectDialog = page.getByRole("dialog", { name: /^Projects$/i });
	await expect(projectDialog).toBeVisible();
	await expect(projectDialog.getByRole("button", { name: /^Open QA project Mercury Checkout$/i })).toBeFocused();

	await page.keyboard.press("Escape");
	await expect(projectDialog).toHaveCount(0);
	await expect(projectTrigger).toBeFocused();
	await expect(page.getByRole("button", { name: /^Close workspace controls$/i })).toHaveAttribute("aria-expanded", "true");

	const healthTrigger = page.getByRole("button", { name: /^(Open|Close) system health details$/i });
	await healthTrigger.focus();
	await page.keyboard.press("Enter");
	await expect(healthTrigger).toHaveAttribute("aria-expanded", "true");
	await expect(healthTrigger).toBeFocused();

	const accountTrigger = page.getByRole("button", { name: /^Open account menu/i });
	await accountTrigger.evaluate((account) => {
		document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
		account.focus();
	});
	await settleLayout(page);
	await expect(healthTrigger).toHaveAttribute("aria-expanded", "false");
	await expect(accountTrigger).toBeFocused({ timeout: 1500 });
	await expect(page.getByRole("button", { name: /^Close workspace controls$/i })).toHaveAttribute("aria-expanded", "true");
	await page.keyboard.press("Enter");
	const accountMenu = page.getByRole("menu", { name: /^Account menu$/i });
	await expect(accountMenu.getByRole("menuitem", { name: /^Settings$/i })).toBeFocused();
	await page.keyboard.press("ArrowDown");
	await expect(accountMenu.getByRole("menuitem", { name: /^Sign Out$/i })).toBeFocused();
	await page.keyboard.press("Escape");
	await expect(accountTrigger).toBeFocused();
	await expect(page.getByRole("button", { name: /^Close workspace controls$/i })).toHaveAttribute("aria-expanded", "true");
});

test("opens Settings from both compact desktop entry points", async ({ page }) => {
	await openSurface(page, "project Overview", 1440);

	await page.getByRole("button", { name: /^Open settings$/i }).click();
	await expect(page.getByRole("dialog", { name: /^Settings$/i })).toBeVisible();
	await page.getByRole("button", { name: /^Close settings dialog$/i }).click();

	const accountTrigger = page.getByRole("button", { name: /^Open account menu/i });
	await accountTrigger.click();
	await page
		.getByRole("menu", { name: /^Account menu$/i })
		.getByRole("menuitem", { name: /^Settings$/i })
		.click();
	await expect(page.getByRole("dialog", { name: /^Settings$/i })).toBeVisible();
});

test("publishes async status politely and exposes blocking workspace failures as alerts", async ({ page }) => {
	await installWorkspaceApi(page, { summary: workspaceSummaryFixture() });
	await seedAuthenticatedSession(page);
	await page.goto("/projects");
	await expect(page.getByRole("heading", { name: /^Projects$/i, level: 1 })).toBeVisible({ timeout: 30_000 });

	const liveStatus = page.getByTestId("application-live-status");
	await expect(liveStatus).toHaveAttribute("role", "status");
	await expect(liveStatus).toHaveAttribute("aria-live", "polite");
	await expect(liveStatus).toHaveAttribute("aria-atomic", "true");
	const projectTrigger = page.getByRole("button", { name: /^Open QA project menu$/i });
	await expect(projectTrigger).toBeEnabled();
	await projectTrigger.click();
	await page
		.getByRole("dialog", { name: /^Projects$/i })
		.getByRole("button", { name: /^Refresh projects$/i })
		.click();
	await expect(liveStatus).toHaveText("Projects refreshed: 0 available.");

	const failurePage = await page.context().newPage();
	await installWorkspaceApi(failurePage, {
		summary: workspaceSummaryFixture(),
		summaryScenarios: [{ status: 503, payload: { detail: "Workspace summary is unavailable" } }],
	});
	await seedAuthenticatedSession(failurePage);
	await failurePage.goto("/");
	await expect(failurePage.getByRole("alert")).toContainText("Workspace summary is unavailable", { timeout: 30_000 });
	await failurePage.close();
});
