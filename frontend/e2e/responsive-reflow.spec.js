import { expect, test } from "@playwright/test";

import { buildProjectPath } from "../src/app/workflowRoutes.js";
import { STORAGE_WORKFLOW_NAV_COLLAPSED } from "../src/constants/workflow.js";
import { seedAuthenticatedSession } from "./support/auth.js";
import {
	expectExactlyOneCurrent,
	expectNoDocumentOverflow,
	expectVisuallyContained,
	expectWithinInitialViewport,
	readCenterWidth,
	readHorizontalScrollRegions,
	settleLayout,
} from "./support/layout.js";
import {
	installWorkspaceApi,
	projectDetailFixture,
	workspaceProjectFixture,
	workspaceSummaryFixture,
	workspaceWorkItemFixture,
} from "./support/workspace.js";

const PROJECT_ID = "responsive-shell-project";
const PROJECT_NAME = "International Quality Assurance Workspace for the Alpine Commerce Platform";
const REQUIREMENTS_SNAPSHOT_ID = "responsive-requirements-v2";
const USE_CASES_SNAPSHOT_ID = "responsive-use-cases-v1";
const LONG_RAW_TEXT = `Responsive parser evidence ${"UNBROKEN".repeat(80)}`;

const viewports = [
	{ width: 320, height: 900, label: "400% reflow proxy" },
	{ width: 390, height: 844, label: "mobile" },
	{ width: 640, height: 900, label: "200% reflow proxy" },
	{ width: 760, height: 900, label: "tablet" },
	{ width: 900, height: 900, label: "compact boundary" },
	{ width: 1280, height: 900, label: "laptop" },
	{ width: 1440, height: 900, label: "desktop" },
	{ width: 1920, height: 1080, label: "wide desktop" },
];

function stageState(status, overrides = {}) {
	return {
		stage: overrides.stage,
		status,
		current_snapshot_id: null,
		version: 0,
		approved: false,
		stale: status === "stale",
		summary: {},
		blockers: [],
		...overrides,
	};
}

function createResponsiveScenario({ currentStatus = "attention_required" } = {}) {
	const workspaceProject = workspaceProjectFixture({
		project_id: PROJECT_ID,
		name: PROJECT_NAME,
		project_revision: 12,
		current_stage: "use_cases",
		current_status: currentStatus,
		current_snapshot_id: USE_CASES_SNAPSHOT_ID,
		completed_stage_count: 1,
	});
	const requirementsSnapshot = {
		snapshot_id: REQUIREMENTS_SNAPSHOT_ID,
		project_id: PROJECT_ID,
		stage: "requirements",
		version: 2,
		project_revision: 11,
		operation: "requirements.refine",
		approved: true,
		payload: {
			requirements: [
				{
					id: "REQ-RESP-001",
					text: "Customers can complete checkout in every supported market.",
					review_status: "Approved",
					source_system: "file",
					source_path: "responsive-requirements.md",
				},
			],
			review: { approved: true, score: 100, threshold: 85, summary: "Requirements approved.", blocking_issues: [] },
			coverage_metrics: { total_requirements: 1, unique_requirements: 1 },
		},
		metadata: {},
		created_at: "2026-07-17T09:00:00Z",
	};
	const useCasesSnapshot = {
		snapshot_id: USE_CASES_SNAPSHOT_ID,
		project_id: PROJECT_ID,
		stage: "use_cases",
		version: 1,
		project_revision: 12,
		operation: "testcases.generate.use_cases",
		approved: true,
		source_snapshot_id: REQUIREMENTS_SNAPSHOT_ID,
		payload: {
			requirement_analysis: [],
			coverage_plan: [
				{
					requirement_id: "REQ-RESP-001",
					requirement_text: "Customers can complete checkout in every supported market.",
					scenarios: [],
				},
			],
		},
		metadata: {},
		created_at: "2026-07-17T09:30:00Z",
	};
	const project = projectDetailFixture(workspaceProject, {
		current_revision: 12,
		stage_state: {
			requirements: {
				current_snapshot_id: REQUIREMENTS_SNAPSHOT_ID,
				version: 2,
				approved: true,
				stale: false,
				metadata: {},
			},
			use_cases: {
				current_snapshot_id: USE_CASES_SNAPSHOT_ID,
				version: 1,
				approved: true,
				stale: false,
				metadata: {},
			},
		},
		current_snapshots: {
			requirements: requirementsSnapshot,
			use_cases: useCasesSnapshot,
		},
	});
	const status = {
		project_id: PROJECT_ID,
		project_revision: 12,
		current_stage: "use_cases",
		stages: {
			requirements: stageState("completed", {
				stage: "requirements",
				current_snapshot_id: REQUIREMENTS_SNAPSHOT_ID,
				version: 2,
				approved: true,
			}),
			context: stageState("not_started", { stage: "context" }),
			use_cases: stageState(currentStatus, {
				stage: "use_cases",
				current_snapshot_id: USE_CASES_SNAPSHOT_ID,
				version: 1,
			}),
			impact_analysis: stageState("not_started", { stage: "impact_analysis" }),
			test_cases: stageState("blocked", { stage: "test_cases" }),
			automation: stageState("blocked", { stage: "automation" }),
			execution: stageState("blocked", { stage: "execution" }),
			review: stageState("blocked", { stage: "review" }),
			reports: stageState("blocked", { stage: "reports" }),
		},
		next_actions: [
			{
				action: "approve",
				label: "Approve Use Cases",
				stage: "use_cases",
				enabled: true,
				primary: true,
				secondary: false,
				reason: "Use Cases need a matching human review before generation.",
				blockers: [],
			},
			{
				action: "full_regenerate",
				label: "Full Regenerate",
				stage: "test_cases",
				enabled: true,
				primary: false,
				secondary: true,
				reason: "Use the explicit replacement path only when necessary.",
				blockers: [],
			},
		],
		blockers: [],
		has_baseline_test_suite: false,
		upstream_changed: false,
		changed_upstream_stages: [],
		generated_at: "2026-07-17T10:00:00Z",
	};
	const workItem = workspaceWorkItemFixture({
		project_id: PROJECT_ID,
		project_name: PROJECT_NAME,
		project_revision: 12,
		stage: "use_cases",
		status: "attention_required",
		action: "approve",
		reason: "Use Cases need review.",
		current_snapshot_id: USE_CASES_SNAPSHOT_ID,
	});
	const summary = workspaceSummaryFixture({
		continue_working: workItem,
		projects: [workspaceProject],
		work_items: [workItem],
	});
	return { project, status, summary };
}

async function openResponsiveProject(page, viewport, { currentStatus = "attention_required", destination = "" } = {}) {
	await page.setViewportSize(viewport);
	const scenario = createResponsiveScenario({ currentStatus });
	await installWorkspaceApi(page, {
		summary: scenario.summary,
		projectDetails: { [PROJECT_ID]: scenario.project },
		orchestratorStatuses: { [PROJECT_ID]: scenario.status },
	});
	await seedAuthenticatedSession(page);
	const projectPath = destination ? buildProjectPath(PROJECT_ID, destination) : buildProjectPath(PROJECT_ID);
	await page.goto(projectPath);
	await expect(page.getByRole("navigation", { name: "Project navigation" })).toBeVisible({ timeout: 30_000 });
	return scenario;
}

test.describe("Responsive project shell", () => {
	for (const width of [901, 1280, 1440, 1920]) {
		test(`keeps the desktop app bar compact at ${width}px`, async ({ page }) => {
			await openResponsiveProject(page, { width, height: 1000 });

			const appBar = page.locator(".global-app-shell-header");
			const appBarBox = await appBar.boundingBox();
			const projectMenuTrigger = page.getByRole("button", { name: /^Open QA project menu$/i });
			const main = page.getByRole("main");
			const mainBox = await main.boundingBox();

			expect(appBarBox).not.toBeNull();
			expect(mainBox).not.toBeNull();
			expect(appBarBox.height).toBeGreaterThanOrEqual(68);
			expect(appBarBox.height).toBeLessThanOrEqual(76);
			expect(Math.round(mainBox.y - (appBarBox.y + appBarBox.height))).toBe(16);
			await expect(projectMenuTrigger).toContainText(PROJECT_NAME);
			await expect(projectMenuTrigger).toContainText("revision 12");
			await expect(page.getByRole("button", { name: /^Open system health details$/i })).toBeVisible();
			await expect(page.getByRole("button", { name: /^Open settings$/i })).toBeVisible();
			await expect(page.getByRole("button", { name: /^Open account menu/i })).toBeVisible();
			await expect(page.getByRole("menuitem", { name: /^Sign Out$/i })).toHaveCount(0);
			await expectNoDocumentOverflow(page, `${width}px compact desktop app bar`);
		});
	}

	for (const viewport of viewports) {
		test(`contains project content at ${viewport.width}px (${viewport.label})`, async ({ page }) => {
			await openResponsiveProject(page, viewport);
			const heading = page.locator(".route-page-header h1");
			const routePage = page.locator(".route-page");
			const globalNavigation = page.getByRole("navigation", { name: "Global navigation" });
			const projectNavigation = page.getByRole("navigation", { name: "Project navigation" });

			await expect(heading).toHaveText(PROJECT_NAME);
			await expect(page.getByLabel("Contextual task").getByRole("button", { name: /^Open workbench$/i })).toBeVisible();
			await expect(page.getByLabel("Project information rail")).toHaveCount(0);
			await expectExactlyOneCurrent(globalNavigation);
			await expectExactlyOneCurrent(projectNavigation);
			await expectVisuallyContained(heading, routePage);

			if (viewport.width <= 900) {
				await expect(page.getByRole("button", { name: /^Open workspace controls$/i })).toHaveAttribute("aria-expanded", "false");
				await expect(projectNavigation.getByRole("button", { name: /^Open project navigation$/i })).toHaveAttribute(
					"aria-expanded",
					"false"
				);
				await expect(projectNavigation.locator(".workflow-navigation-list")).toBeHidden();
			} else {
				await expect(page.getByRole("button", { name: /^Open workspace controls$/i })).toHaveCount(0);
				await expect(projectNavigation.getByRole("link", { name: /^Overview, Current$/i })).toBeVisible();
			}

			if ([390, 760].includes(viewport.width)) {
				await expectWithinInitialViewport(heading, page);
				await expectWithinInitialViewport(page.getByLabel("Contextual task").getByRole("button", { name: /^Open workbench$/i }), page);
			}

			await expectNoDocumentOverflow(page, `${viewport.width}px project overview`);

			await page.goto(buildProjectPath(PROJECT_ID, "automation"));
			await expect(page.getByRole("heading", { name: /^Automation$/i })).toBeVisible();
			await expectNoDocumentOverflow(page, `${viewport.width}px project Automation`);

			await page.goto(buildProjectPath(PROJECT_ID, "reports"));
			await expect(page.getByRole("heading", { name: /^Export Test Cases$/i })).toBeVisible();
			await expectNoDocumentOverflow(page, `${viewport.width}px project Reports`);
		});
	}

	for (const width of [760, 900]) {
		test(`operates compact disclosures from the keyboard at ${width}px`, async ({ page }) => {
			await openResponsiveProject(page, { width, height: 900 });

			const workspaceToggle = page.getByRole("button", { name: /^Open workspace controls$/i });
			const workspaceControlsId = await workspaceToggle.getAttribute("aria-controls");
			expect(workspaceControlsId).toBeTruthy();
			await expect(page.locator(`#${workspaceControlsId}`)).toBeHidden();
			await workspaceToggle.focus();
			await page.keyboard.press(width === 760 ? "Enter" : "Space");
			await expect(page.getByRole("button", { name: /^Close workspace controls$/i })).toHaveAttribute("aria-expanded", "true");
			await page.keyboard.press("Tab");
			await page.keyboard.press("Escape");
			await expect(workspaceToggle).toBeVisible();
			await expect(workspaceToggle).toBeFocused();

			const projectNavigation = page.getByRole("navigation", { name: "Project navigation" });
			const projectToggle = projectNavigation.getByRole("button", { name: /^Open project navigation$/i });
			await projectToggle.focus();
			await page.keyboard.press(width === 760 ? "Space" : "Enter");
			await expect(projectNavigation.locator(".workflow-navigation-list")).toBeVisible();
			await expect(projectNavigation.getByRole("button", { name: /^Close project navigation$/i })).toHaveAttribute("aria-expanded", "true");
			await page.keyboard.press("Tab");
			await expect(projectNavigation.getByRole("link").first()).toBeFocused();
			await page.keyboard.press("Escape");
			await expect(projectNavigation.locator(".workflow-navigation-list")).toBeHidden();
			await expect(projectToggle).toBeFocused();
			await expectNoDocumentOverflow(page, `${width}px compact disclosure interactions`);
		});
	}

	test("reserves active treatment for the current route and exposes non-color workflow states", async ({ page }) => {
		await openResponsiveProject(page, { width: 1280, height: 900 });
		const globalNavigation = page.getByRole("navigation", { name: "Global navigation" });
		const projectNavigation = page.getByRole("navigation", { name: "Project navigation" });
		await expectExactlyOneCurrent(globalNavigation);
		await expectExactlyOneCurrent(projectNavigation);
		await expect(globalNavigation.locator('[aria-current="page"]')).toContainText("Projects");

		const expectedStates = [
			{ name: "Overview, Current", tone: "active" },
			{ name: "Requirements, Complete", tone: "complete" },
			{ name: "Context, Pending", tone: "pending" },
			{ name: "Use Cases, Needs attention", tone: "attention" },
			{ name: "Automation, Blocked", tone: "blocked" },
		];
		for (const expectedState of expectedStates) {
			const item = projectNavigation.getByRole("link", { name: expectedState.name });
			const badge = item.locator(".workflow-navigation-state");
			await expect(item).toBeVisible();
			await expect(badge).toHaveAttribute("data-status-tone", expectedState.tone);
			await expect(badge.locator("svg")).toBeVisible();
		}

		await projectNavigation.getByRole("button", { name: /^Collapse project navigation$/i }).click();
		await expectExactlyOneCurrent(projectNavigation);
		for (const expectedState of expectedStates) {
			const item = projectNavigation.getByRole("link", { name: expectedState.name });
			const badge = item.locator(".workflow-navigation-state");
			await expect(badge).toBeVisible();
			await expect(badge).toHaveClass(/compact/);
			await expect(badge).toHaveAttribute("data-status-tone", expectedState.tone);
		}
		await expectNoDocumentOverflow(page, "collapsed desktop workflow states");
	});

	test("contains long project and localized navigation labels without a status rail", async ({ page }) => {
		await openResponsiveProject(page, { width: 1920, height: 1080 });
		const projectNavigation = page.getByRole("navigation", { name: "Project navigation" });
		const globalNavigation = page.getByRole("navigation", { name: "Global navigation" });
		const workflowLabel = projectNavigation.getByRole("link", { name: /^Use Cases,/i }).locator(".workflow-navigation-copy strong");
		const globalLabel = globalNavigation.locator(".global-navigation-link.active");

		await workflowLabel.evaluate((element) => {
			element.textContent = "Anwendungsfallüberprüfung für internationale Qualitätsfreigaben";
		});
		await globalLabel.evaluate((element) => {
			element.textContent = "Internationale Projektarbeitsbereiche";
		});
		await settleLayout(page);
		await expect(page.getByLabel("Project information rail")).toHaveCount(0);
		await expectVisuallyContained(workflowLabel, workflowLabel.locator("xpath=.."));
		await expectVisuallyContained(globalLabel, globalNavigation);
		expect(await workflowLabel.evaluate((element) => getComputedStyle(element).overflowWrap)).not.toBe("anywhere");
		expect(await workflowLabel.evaluate((element) => getComputedStyle(element).textOverflow)).toBe("ellipsis");
		await expectNoDocumentOverflow(page, "long expanded shell labels");

		await page.setViewportSize({ width: 320, height: 900 });
		const heading = page.locator(".route-page-header h1");
		await heading.evaluate((element) => {
			element.textContent = "InternationalizedQualityAssuranceWorkspaceWithoutNaturalBreakpoints";
		});
		await settleLayout(page);
		await expectVisuallyContained(heading, page.locator(".route-page"));
		await expectNoDocumentOverflow(page, "320px unbroken project name");
	});

	test("preserves the desktop navigation preference across compact transitions and reload", async ({ page }) => {
		await openResponsiveProject(page, { width: 1280, height: 900 });
		const projectNavigation = page.getByRole("navigation", { name: "Project navigation" });

		await projectNavigation.getByRole("button", { name: /^Collapse project navigation$/i }).click();
		await expect(projectNavigation).toHaveClass(/collapsed/);
		await expect.poll(() => page.evaluate((key) => window.localStorage.getItem(key), STORAGE_WORKFLOW_NAV_COLLAPSED)).toBe("true");

		await page.setViewportSize({ width: 760, height: 900 });
		await expect(projectNavigation.getByRole("button", { name: /^Open project navigation$/i })).toHaveAttribute("aria-expanded", "false");
		await projectNavigation.getByRole("button", { name: /^Open project navigation$/i }).click();
		await expect(projectNavigation.locator(".workflow-navigation-list")).toBeVisible();

		await page.setViewportSize({ width: 1280, height: 900 });
		await expect(projectNavigation.getByRole("button", { name: /^Expand project navigation$/i })).toBeVisible();
		await expect.poll(() => page.evaluate((key) => window.localStorage.getItem(key), STORAGE_WORKFLOW_NAV_COLLAPSED)).toBe("true");

		await page.reload();
		await expect(page.getByRole("navigation", { name: "Project navigation" })).toHaveClass(/collapsed/);
		await page
			.getByRole("navigation", { name: "Project navigation" })
			.getByRole("button", { name: /^Expand project navigation$/i })
			.click();
		await expect.poll(() => page.evaluate((key) => window.localStorage.getItem(key), STORAGE_WORKFLOW_NAV_COLLAPSED)).toBe("false");
	});

	test("keeps the center workspace monotonic across the desktop transition", async ({ page }) => {
		await openResponsiveProject(page, { width: 1280, height: 900 });
		const width1280 = await readCenterWidth(page);
		await page.setViewportSize({ width: 1440, height: 900 });
		const width1440 = await readCenterWidth(page);
		await page.setViewportSize({ width: 1727, height: 1000 });
		const width1727 = await readCenterWidth(page);
		await page.setViewportSize({ width: 1728, height: 1000 });
		const width1728 = await readCenterWidth(page);

		expect(width1440, "1440px center must not be narrower than 1280px").toBeGreaterThanOrEqual(width1280 - 2);
		expect(width1727, "two-column center should continue growing before the transition").toBeGreaterThanOrEqual(width1440 - 2);
		expect(width1728, "three-column transition must preserve the 1280px usable center baseline").toBeGreaterThanOrEqual(width1280 - 2);
	});

	test("limits horizontal scrolling to named, focusable intrinsic table regions", async ({ page }) => {
		await openResponsiveProject(page, { width: 320, height: 900 }, { destination: "requirements" });
		await expect(page.getByRole("region", { name: /requirements table/i })).toBeVisible();
		await page.route("**/requirements/parse", async (route) =>
			route.fulfill({
				status: 200,
				contentType: "application/json",
				body: JSON.stringify({
					source_name: "responsive-requirements.md",
					raw_text: LONG_RAW_TEXT,
					requirements: [
						{
							id: "REQ-RESP-001",
							text: "Customers can complete checkout in every supported market.",
							review_status: "Approved",
						},
					],
					review: { approved: true, score: 100, threshold: 85, summary: "Requirements approved.", blocking_issues: [] },
					coverage_metrics: { total_requirements: 1, unique_requirements: 1 },
					workflow_diagnostics: { status: "completed", warnings: [], parser_failures: [] },
					iteration_history: [],
				}),
			})
		);
		await page.locator('input[type="file"]').setInputFiles({
			name: "responsive-requirements.md",
			mimeType: "text/markdown",
			buffer: Buffer.from("Customers can complete checkout in every supported market."),
		});
		await page.getByRole("button", { name: /parse requirements/i }).click();
		await page.getByText("Raw extracted text", { exact: true }).click();
		const rawTextRegion = page.getByRole("region", { name: "Raw extracted requirements text" });
		await expect(rawTextRegion).toBeVisible();
		await expect(rawTextRegion).toHaveAttribute("tabindex", "0");
		const regions = await readHorizontalScrollRegions(page);
		expect(regions.length, `Expected intrinsic table overflow at 320px: ${JSON.stringify(regions)}`).toBeGreaterThan(0);
		for (const region of regions) {
			expect(region.role, JSON.stringify(region)).toBe("region");
			expect(Boolean(region.label || region.labelledBy), JSON.stringify(region)).toBe(true);
			expect(region.hasIntrinsicContent, JSON.stringify(region)).toBe(true);
			expect(region.tabIndex, JSON.stringify(region)).toBeGreaterThanOrEqual(0);
		}
		await expectNoDocumentOverflow(page, "320px named table scroll regions");
	});
});
