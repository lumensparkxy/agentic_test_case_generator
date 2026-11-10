import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectNoDocumentOverflow } from "./support/layout.js";
import { USE_CASE_PROJECT_ID, useCaseOrchestratorFixture, useCaseProjectFixture } from "./support/use-case-review.js";
import { installWorkspaceApi, workspaceProjectFixture, workspaceSummaryFixture } from "./support/workspace.js";

const v2 = process.env.E2E_PROJECT_WORKSPACES_V2 === "true";
const projectRoot = `/projects/${USE_CASE_PROJECT_ID}`;
const projectPath = (suffix) => `${projectRoot}/${suffix}`;
const historicalSuite = "suite + original";
const historicalCase = {
	id: "TC + / Ω",
	title: "Original checkout",
	preconditions: "Use the original order form with a guest session.",
	steps: [{ step: 1, action: "Place the original order", expected: "Original confirmation appears" }],
};
const sourceExcerpt = "Customers can complete express checkout.";
const draftText = "Investigate checkout confirmation when a retry succeeds.";
const primary = (page) => page.getByRole("navigation", { name: "Project workspaces", exact: true });
const sections = (page) => page.getByRole("navigation", { name: "Workspace sections", exact: true });
const workspace = (page, name) => primary(page).getByRole("link", { name: new RegExp(`^${name}(?:,|$)`) });
const section = (page, name) => sections(page).getByRole("link", { name, exact: true });
const heading = (page, name) => page.getByRole("heading", { name, exact: true });

async function expectDeliverSection(page, name, { focused = false } = {}) {
	const title = `Deliver · ${name}`;
	await expect(workspace(page, "Deliver")).toHaveAttribute("aria-current", "location");
	await expect(section(page, name)).toHaveAttribute("aria-current", "page");
	await expect(page.getByRole("heading", { level: 1, name: title, exact: true })).toBeVisible();
	const main = page.getByRole("main", { name: `Workflow workspace: ${title}`, exact: true });
	await expect(main).toBeVisible();
	if (focused) await expect(main).toBeFocused();
}

async function setup(page, count = 50, { reviewApproved = true, scenarioReviewState = "pending" } = {}) {
	const project = useCaseProjectFixture({ reviewState: scenarioReviewState });
	project.current_snapshots.requirements.payload.requirements[0].sources = [
		{
			source_id: "checkout",
			source_version: "v1",
			label: "checkout.md",
			original_requirement_ids: ["CHECKOUT-1"],
			excerpt: sourceExcerpt,
			excerpt_verified: true,
		},
	];
	const snapshotId = "current-checkout-suite";
	project.current_snapshots.test_cases = {
		snapshot_id: snapshotId,
		stage: "test_cases",
		version: 2,
		approved: reviewApproved,
		payload: {
			review: { approved: reviewApproved, score: reviewApproved ? 100 : 85, threshold: 90 },
			test_cases: Array.from({ length: count }, (_, index) => ({
				id: `TC-${index + 1}`,
				title: `Checkout ${index + 1}`,
				priority: index % 2 ? "High" : "Medium",
				linked_requirement_ids: ["REQ-101"],
				steps: [{ step: 1, action: "Submit order", expected: `Receipt ${index + 1} appears` }],
			})),
		},
	};
	project.stage_state.test_cases = { current_snapshot_id: snapshotId, version: 2, stale: false, approved: reviewApproved };
	const other = useCaseProjectFixture({ project_id: "alternate-workspace-project", name: "Other checkout" });
	other.current_snapshots.requirements.payload.requirements[0].text = "The other checkout uses independent source evidence.";
	const projects = [project, other].map((entry) =>
		workspaceProjectFixture({
			project_id: entry.project_id,
			name: entry.name,
			project_revision: entry.current_revision,
		})
	);

	const writes = [];
	const errors = [];
	const lookups = [];
	page.on("request", (request) => {
		if (["fetch", "xhr"].includes(request.resourceType()) && !["GET", "OPTIONS", "HEAD"].includes(request.method())) {
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
		}
	});
	page.on("pageerror", (error) => errors.push(error.message));
	await page.route("**/*", (route) => {
		const request = route.request();
		if (!["fetch", "xhr"].includes(request.resourceType())) return route.fallback();
		const pathname = new URL(request.url()).pathname;
		if (pathname.endsWith("/knowledge")) {
			return route.fulfill({ json: { entries: [], skills: [], features: {}, revision: "0" } });
		}
		if (pathname.endsWith("/quality-findings")) {
			return route.fulfill({ json: { findings: [], project_revision: project.current_revision } });
		}
		return route.fulfill({ status: 404, json: { detail: "No fixture for this optional endpoint." } });
	});
	await installWorkspaceApi(page, {
		summary: workspaceSummaryFixture({ projects }),
		projectDetails: { [project.project_id]: project, [other.project_id]: other },
		orchestratorStatuses: { [project.project_id]: useCaseOrchestratorFixture(project) },
	});
	await page.route(`**${projectRoot}/test-case-evidence?*`, (route) => {
		const identity = Object.fromEntries(new URL(route.request().url()).searchParams);
		lookups.push(identity);
		return route.fulfill({
			json: {
				project_id: project.project_id,
				snapshot_id: historicalSuite,
				suite_version: 1,
				project_revision: 4,
				historical: true,
				test_case: historicalCase,
				requirements: [{ id: "REQ-101", text: "Original checkout requirement." }],
				obligations: [],
			},
		});
	});
	await seedAuthenticatedSession(page);
	return { writes, errors, lookups, project, other };
}

async function expectHistoricalCase(page, lookups) {
	const reader = page.getByRole("region", { name: "Saved test case", exact: true });
	await expect(reader.getByRole("heading", { name: historicalCase.title, exact: true })).toBeVisible();
	await expect(reader).toContainText("Historical saved suite · Version 1 · Project revision 4");
	await expect(reader).toContainText("Original confirmation appears");
	await expect(reader.getByRole("region", { name: "Preconditions", exact: true })).toContainText(historicalCase.preconditions);
	await expect(reader.getByText(historicalCase.preconditions, { exact: true })).toBeVisible();
	await expect(reader.getByRole("button", { name: "Report a problem", exact: true })).toHaveCount(0);
	expect(lookups.length).toBeGreaterThan(0);
	expect(lookups).toEqual(Array(lookups.length).fill({ snapshot_id: historicalSuite, case_id: historicalCase.id }));
}

test.describe("Opt-in project workspaces", () => {
	test.skip(!v2, "Requires VITE_PROJECT_WORKSPACES_V2=true on the frontend");

	test("collapses the app menu while preserving the selected artifact and keyboard navigation", async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const { writes, errors } = await setup(page, 200);
		await page.goto(projectPath("review/tests"));
		const menu = page.getByRole("button", { name: "Show app menu", exact: true });
		const navigation = page.getByRole("navigation", { name: "Global navigation", exact: true });
		const home = navigation.getByRole("link", { name: "Home", exact: true });
		const projects = navigation.getByRole("link", { name: "Projects", exact: true });
		const reader = page.getByRole("region", { name: "Selected test case", exact: true });
		const search = page.getByRole("searchbox", { name: "Search test cases", exact: true });
		await expect(menu).toHaveAttribute("aria-expanded", "false");
		await expect(navigation).toBeHidden();
		await search.fill("Checkout 200");
		await page.getByRole("button", { name: "TC-200 Checkout 200", exact: true }).press("Enter");
		await expect(reader).toContainText("Receipt 200 appears");
		await page.evaluate(async () => {
			await document.fonts.ready;
			window.scrollTo(0, 0);
		});
		const collapsedTop = (await reader.boundingBox()).y;
		await page.screenshot({ path: testInfo.outputPath("review-app-menu-collapsed.png") });
		await menu.press("Enter");
		const hideMenu = page.getByRole("button", { name: "Hide app menu", exact: true });
		await expect(hideMenu).toHaveAttribute("aria-expanded", "true");
		await expect(home).toBeFocused({ timeout: 3000 });
		await page.keyboard.press("Tab");
		await expect(projects).toBeFocused();
		await expect(page.getByRole("button", { name: "Open QA project menu", exact: true })).toBeVisible();
		await expect(reader).toContainText("Receipt 200 appears");
		await expect(search).toHaveValue("Checkout 200");
		expect((await reader.boundingBox()).y - collapsedTop).toBeGreaterThanOrEqual(76);
		await page.screenshot({ path: testInfo.outputPath("review-app-menu-expanded.png") });
		await page.keyboard.press("Escape");
		await expect(menu).toBeFocused();
		await expect(navigation).toBeHidden();
		await menu.press("Enter");
		await section(page, "Scenarios").press("Enter");
		await expect(hideMenu).toHaveAttribute("aria-expanded", "true");
		await hideMenu.press("Enter");
		await expect(menu).toBeFocused();
		await expect(navigation).toBeHidden();
		await section(page, "Tests").press("Enter");
		await expect(reader).toContainText("Receipt 200 appears");
		await expect(search).toHaveValue("Checkout 200");
		await expect(menu).toHaveAttribute("aria-expanded", "false");
		await expectNoDocumentOverflow(page);

		await menu.press("Enter");
		await expect(home).toBeFocused();
		await page.keyboard.press("Enter");
		await expect(page).toHaveURL("/");
		await expect(navigation).toBeVisible();
		await expect(menu).toHaveCount(0);
		expect(writes).toEqual([]);
		expect(errors).toEqual([]);
	});

	test("dismisses nested app controls in order on a narrow Review screen", async ({ page }) => {
		await page.setViewportSize({ width: 390, height: 844 });
		const { writes, errors } = await setup(page);
		await page.goto(projectPath("review/tests"));
		const menu = page.getByRole("button", { name: "Show app menu", exact: true });
		const controls = page.getByRole("button", { name: "Open workspace controls", exact: true });
		const projectMenu = page.getByRole("button", { name: "Open QA project menu", exact: true });
		const picker = page.getByRole("dialog", { name: "Projects", exact: true });

		await menu.press("Enter");
		await expect(
			page.getByRole("navigation", { name: "Global navigation" }).getByRole("link", { name: "Home", exact: true })
		).toBeFocused();
		await controls.press("Enter");
		await projectMenu.press("Enter");
		await expect(picker).toBeVisible();
		await expect(picker.locator('[aria-current="true"]')).toBeFocused();
		await page.keyboard.press("Escape");
		await expect(picker).toBeHidden();
		await expect(projectMenu).toBeFocused();
		await page.keyboard.press("Escape");
		await expect(controls).toBeFocused();
		await expect(projectMenu).toBeHidden();
		await page.keyboard.press("Escape");
		await expect(menu).toBeFocused();
		await expect(controls).toBeHidden();
		await expectNoDocumentOverflow(page);
		expect(writes).toEqual([]);
		expect(errors).toEqual([]);
	});

	for (const [width, initial, current, expected] of [
		[1366, "inputs", "Inputs", "Context Inputs"],
		[1440, "review/tests", "Review", "Test Cases"],
		[1920, "deliver/browser-tests", "Deliver", "Execution setup"],
	]) {
		test(`renders three workspaces without horizontal overflow at ${width}px`, async ({ page }, testInfo) => {
			await page.setViewportSize({ width, height: 900 });
			const count = width === 1440 ? 200 : 50;
			const { writes, errors } = await setup(page, count);
			await page.goto(projectPath(initial));
			await expect(heading(page, expected)).toBeVisible();
			if (current === "Review") {
				await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
				await expect(page.getByRole("region", { name: "Selected test case" })).toContainText("Receipt 1 appears");
			}
			await expect(primary(page).getByRole("link")).toHaveCount(3);
			for (const [name, suffix] of [
				["Inputs", "inputs"],
				["Review", "review/scenarios"],
				["Deliver", "deliver"],
			]) {
				await expect(workspace(page, name)).toHaveAttribute("href", projectPath(suffix));
			}
			await expect(primary(page).locator('[aria-current="location"]')).toHaveCount(1);
			await expect(workspace(page, current)).toHaveAttribute("aria-current", "location");
			await expect(page.getByText(`2 requirements · 4 scenarios · ${count} tests`, { exact: true })).toBeVisible();
			await expectNoDocumentOverflow(page, current);
			await expect(page.getByText(/Loading project recommendations/)).toHaveCount(0);
			await page.screenshot({ path: testInfo.outputPath(`workspace-${width}.png`) });

			if (width === 1440) {
				const scan = await new AxeBuilder({ page })
					.include('nav[aria-label="Project workspaces"]')
					.include('nav[aria-label="Workspace sections"]')
					.withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
					.analyze();
				expect(scan.violations).toEqual([]);
				await page.evaluate(() => {
					document.documentElement.style.zoom = "2";
				});
				await expectNoDocumentOverflow(page, "Review at 200% CSS zoom");
				await workspace(page, "Review").focus();
				await page.keyboard.press("Tab");
				await expect(workspace(page, "Deliver")).toBeFocused();
				await page.keyboard.press("Enter");
				await expect(heading(page, "Export Test Cases")).toBeVisible();
				await expect(page.getByText(`2 requirements · 4 scenarios · ${count} tests`, { exact: true })).toBeVisible();
				await expect(page.locator("#main-content")).toBeFocused();
				await expectNoDocumentOverflow(page, "Deliver at 200% CSS zoom");
				await page.screenshot({ path: testInfo.outputPath("workspace-1440-zoom-200.png") });
			}
			expect(writes).toEqual([]);
			expect(errors).toEqual([]);
		});
	}

	test("reveals the destination heading after keyboard navigation from a scrolled case reader", async ({ page }) => {
		await page.setViewportSize({ width: 720, height: 456 });
		const { writes, errors } = await setup(page, 200);
		await page.goto(projectPath("review/tests"));
		await page.getByRole("tab", { name: /Generated Test Cases/ }).press("Enter");
		const search = page.getByRole("searchbox", { name: "Search test cases", exact: true });
		const reader = page.getByRole("region", { name: "Selected test case" });
		await search.fill("Checkout 200");
		await page.getByRole("button", { name: "TC-200 Checkout 200", exact: true }).press("Enter");
		await reader.getByRole("heading", { name: "Checkout 200", exact: true }).scrollIntoViewIfNeeded();
		await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);

		await workspace(page, "Review").focus();
		await page.keyboard.press("Tab");
		await expect(workspace(page, "Deliver")).toBeFocused();
		await page.keyboard.press("End");
		await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(400);
		await page.keyboard.press("Enter");
		await expect(page.locator("#main-content")).toBeFocused();
		const exportHeading = heading(page, "Export Test Cases");
		await expect(exportHeading).toBeVisible();
		const waitForPageScroll = () =>
			page.evaluate(
				() =>
					new Promise((resolve) => {
						let timer;
						const waitForScrollToSettle = () => {
							window.clearTimeout(timer);
							timer = window.setTimeout(() => {
								window.removeEventListener("scroll", waitForScrollToSettle);
								resolve();
							}, 150);
						};
						window.addEventListener("scroll", waitForScrollToSettle);
						waitForScrollToSettle();
					})
			);
		await waitForPageScroll();
		await expect(exportHeading).toBeInViewport({ ratio: 1, timeout: 5000 });

		for (let lateScroll = 0; lateScroll < 2; lateScroll++) {
			await page.evaluate(() => window.scrollBy({ top: window.innerHeight, behavior: "instant" }));
			await expect(exportHeading).toBeInViewport({ ratio: 1, timeout: 5000 });
		}

		const entryScroll = await page.evaluate(() => window.scrollY);
		await page.keyboard.press("End");
		await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(entryScroll + 100);
		await waitForPageScroll();
		expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(entryScroll + 100);

		await page.goBack();
		await page.getByRole("tab", { name: /Generated Test Cases/ }).press("Enter");
		await expect(search).toHaveValue("Checkout 200");
		await expect(reader).toContainText("Receipt 200 appears");
		expect(writes).toEqual([]);
		expect(errors).toEqual([]);
	});

	test("section links and browser history restore the correct workbenches and local decisions", async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const { writes, errors, other } = await setup(page);
		await page.goto(projectPath("requirements"));
		await expect(workspace(page, "Inputs")).toHaveAttribute("aria-current", "location");
		await expect(section(page, "Sources")).toHaveAttribute("aria-current", "page");
		await page.getByRole("button", { name: "Import requirements", exact: true }).click();
		const paste = page.getByRole("radio", { name: "Paste text", exact: true });
		await paste.check();
		const requirementsText = page.getByLabel("Requirements text");
		await requirementsText.fill(draftText);

		await section(page, "Context").press("Enter");
		await expect(page).toHaveURL(projectPath("inputs/context"));
		await expect(heading(page, "Context Inputs")).toBeVisible();
		await expect(page.locator("#main-content")).toBeFocused();

		await workspace(page, "Review").press("Enter");
		await expect(page).toHaveURL(projectPath("review/scenarios"), { timeout: 5000 });
		await expect(page.getByRole("region", { name: "Use Cases review actions", exact: true })).toContainText("Pending human decision");
		await expect(page.locator("#main-content")).toBeFocused();
		await section(page, "Scope").press("Enter");
		await expect(page).toHaveURL(projectPath("review"));
		await expect(heading(page, "Requirement Review Workbench")).toBeVisible();
		await page.getByRole("button", { name: /^Review REQ-101:/ }).click();
		await page.getByText("Source evidence (1)", { exact: true }).click();
		await expect(page.locator("blockquote").filter({ hasText: sourceExcerpt })).toBeVisible();

		await section(page, "Scenarios").press("Enter");
		await expect(page).toHaveURL(projectPath("review/scenarios"));
		const scenarioStatus = page.getByRole("combobox", { name: "Review status for REQ-101-SCN-01", exact: true });
		await scenarioStatus.selectOption("approved");
		await section(page, "Tests").press("Enter");
		await expect(page).toHaveURL(projectPath("review/tests"));
		await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
		const search = page.getByRole("searchbox", { name: "Search test cases" });
		await search.fill("Checkout");
		await page.getByRole("button", { name: "Filters", exact: true }).click();
		const priority = page.getByRole("combobox", { name: "Priority", exact: true });
		await priority.selectOption("High");
		const chosen = page.getByRole("button", { name: "TC-40 Checkout 40", exact: true });
		await chosen.click();
		await expect(page.getByRole("region", { name: "Selected test case" })).toContainText("Receipt 40 appears");

		await workspace(page, "Deliver").press("Enter");
		await expect(page).toHaveURL(projectPath("deliver"));
		await expect(heading(page, "Export Test Cases")).toBeVisible();
		await expectDeliverSection(page, "Export", { focused: true });
		await page.goBack();
		await expect(page).toHaveURL(projectPath("review/tests"));
		await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
		await expect(search).toHaveValue("Checkout");
		await expect(chosen).toHaveAttribute("aria-pressed", "true");
		await page.getByRole("button", { name: "Filters (1)", exact: true }).click();
		await expect(priority).toHaveValue("High");

		await page.goBack();
		await expect(page).toHaveURL(projectPath("review/scenarios"));
		await expect(scenarioStatus).toHaveValue("approved");
		await page.goForward();
		await expect(page).toHaveURL(projectPath("review/tests"));
		await page.goForward();
		await expect(page).toHaveURL(projectPath("deliver"));
		await expect(heading(page, "Export Test Cases")).toBeVisible();
		await expect(page.locator("#main-content")).toBeFocused();
		await section(page, "Browser tests").press("Enter");
		await expect(page).toHaveURL(projectPath("deliver/browser-tests"));
		await expect(heading(page, "Execution setup")).toBeVisible();
		await expectDeliverSection(page, "Browser tests", { focused: true });

		for (const [alias, name] of [
			["reports", "Export"],
			["automation", "Browser tests"],
		]) {
			await page.goto(projectPath(alias));
			await expect(page).toHaveURL(projectPath(alias));
			await expectDeliverSection(page, name);
		}
		expect(writes).toEqual([]);

		const projectStatus = page.getByRole("link", { name: "Project status", exact: true });
		await projectStatus.press("Enter");
		await expect(page).toHaveURL(projectRoot);
		await expect(heading(page, "Overview")).toBeVisible();
		await expect(projectStatus).toHaveAttribute("aria-current", "page");
		await expect(page.locator("#main-content")).toBeFocused();

		const globalNav = page.getByRole("navigation", { name: "Global navigation", exact: true });
		for (const name of ["Home", "Projects", "Reviews", "Runs", "Reports"]) {
			await expect(globalNav.getByRole("link", { name, exact: true })).toBeVisible();
		}
		await globalNav.getByRole("link", { name: "Projects", exact: true }).press("Enter");
		await expect(primary(page)).toHaveCount(0);
		const otherRow = page.getByRole("region", { name: "Projects", exact: true }).getByRole("listitem").filter({ hasText: other.name });
		await otherRow.getByRole("link").press("Enter");
		await expect(workspace(page, "Inputs")).toHaveAttribute("href", `/projects/${other.project_id}/inputs`);
		await workspace(page, "Inputs").press("Enter");
		await page.getByRole("button", { name: "Import requirements", exact: true }).click();
		await paste.check();
		await expect(requirementsText).toBeEmpty();

		await page.goto(projectPath("inputs"));
		await page.getByRole("button", { name: "Import requirements", exact: true }).click();
		await paste.check();
		await expect(requirementsText).toHaveValue(draftText);
		expect(writes).toEqual([]);
		expect(errors).toEqual([]);
	});

	test("keeps the Review entry on Scope while an approved plan has stale Context", async ({ page }) => {
		const { writes, errors, project } = await setup(page, 50, { scenarioReviewState: "approved" });
		project.stage_state.context = { current_snapshot_id: "earlier-checkout-context", stale: true };
		await page.goto(projectPath("deliver"));
		await expect(workspace(page, "Review")).toHaveAttribute("href", projectPath("review"));
		await workspace(page, "Review").press("Enter");
		await expect(page).toHaveURL(projectPath("review"));
		await expect(heading(page, "Review scope")).toBeVisible();
		await expect(page.getByRole("status").filter({ hasText: "Saved context is stale." })).toBeVisible();
		expect(writes).toEqual([]);
		expect(errors).toEqual([]);
	});

	test("resumes the chosen Review Tests view, filters and case from Deliver and on reload", async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const { writes, errors, project } = await setup(page, 200, { reviewApproved: false, scenarioReviewState: "approved" });
		const improve = page.getByRole("tab", { name: /^Improve tests/ });
		const cases = page.getByRole("tab", { name: /^Generated Test Cases/ });
		const search = page.getByRole("searchbox", { name: "Search test cases", exact: true });
		const reader = page.getByRole("region", { name: "Selected test case" });
		const chosen = page.getByRole("button", { name: "TC-198 Checkout 198", exact: true });
		const priority = page.getByRole("combobox", { name: "Priority", exact: true });
		await page.goto(projectPath("deliver"));
		await workspace(page, "Review").press("Enter");
		await expect(page).toHaveURL(projectPath("review/tests"));
		await expect(page.locator("#main-content")).toBeFocused();
		await expect(improve).toHaveAttribute("aria-selected", "true");
		await cases.press("Enter");
		await search.fill("Checkout 19");
		await page.getByRole("button", { name: "Filters", exact: true }).press("Enter");
		await priority.selectOption("High");
		await chosen.press("Enter");
		await expect(reader).toContainText("Receipt 198 appears");

		await workspace(page, "Deliver").press("Enter");
		await expect(page).toHaveURL(projectPath("deliver"));
		await workspace(page, "Review").press("Enter");
		await expect(page).toHaveURL(projectPath("review/tests"));
		await expect(reader).toContainText("Receipt 198 appears");
		await page.reload();
		await expect(cases).toHaveAttribute("aria-selected", "true", { timeout: 5000 });
		await expect(search).toHaveValue("Checkout 19");
		await expect(chosen).toHaveAttribute("aria-pressed", "true");
		await expect(reader).toContainText("Receipt 198 appears");
		await page.getByRole("button", { name: "Filters (1)", exact: true }).press("Enter");
		await expect(priority).toHaveValue("High");
		await reader.getByRole("heading", { name: "Checkout 198", exact: true }).scrollIntoViewIfNeeded();
		await page.screenshot({ path: testInfo.outputPath("resumed-review-case.png") });

		const traceability = page.getByRole("tab", { name: /^Traceability Matrix/ });
		await traceability.press("Enter");
		await page.reload();
		await expect(traceability).toHaveAttribute("aria-selected", "true");

		const suite = project.current_snapshots.test_cases;
		suite.snapshot_id = "replacement-checkout-suite";
		suite.version += 1;
		project.stage_state.test_cases.current_snapshot_id = suite.snapshot_id;
		project.stage_state.test_cases.version = suite.version;
		project.current_revision += 1;
		await page.reload();
		await expect(improve).toHaveAttribute("aria-selected", "true");
		await cases.press("Enter");
		await expect(search).toBeEmpty();
		await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeVisible();
		await expect(chosen).toHaveAttribute("aria-pressed", "false");
		await expect(reader).toContainText("Receipt 1 appears");
		expect(writes).toEqual([]);
		expect(errors).toEqual([]);
	});
});

test("historical case links preserve exact query and hash with the workspace shell or rollback @p1", async ({ page }, testInfo) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	const { writes, errors, lookups } = await setup(page);
	const query = new URLSearchParams({ suite: historicalSuite, case: historicalCase.id });
	const legacy = `${projectPath("test-cases")}?${query}#recorded`;
	const alias = `${projectPath("review/tests")}?${query}#recorded`;
	await page.goto(legacy);
	await expect(page).toHaveURL(legacy);
	await expectHistoricalCase(page, lookups);
	await expect(heading(page, "Saved test case")).toBeFocused();
	await page.goto(alias);
	await expect(page).toHaveURL(alias);
	await expectHistoricalCase(page, lookups);
	if (v2) {
		await expect(workspace(page, "Review")).toHaveAttribute("aria-current", "location");
		await expect(section(page, "Tests")).toHaveAttribute("aria-current", "page");
	} else {
		await expect(primary(page)).toHaveCount(0);
		await expect(page.getByRole("navigation", { name: "Project navigation", exact: true }).getByRole("link")).toHaveCount(7);
	}

	await page.screenshot({ path: testInfo.outputPath(v2 ? "historical-workspace.png" : "historical-rollback.png") });
	await page.getByRole("link", { name: "Open current suite", exact: true }).press("Enter");
	await expect(page.getByRole("tab", { name: /Generated Test Cases/ })).toBeVisible();
	await page.goBack();
	await expect(page).toHaveURL(alias);
	await expectHistoricalCase(page, lookups);
	await page.goForward();
	await expect(page.getByRole("tab", { name: /Generated Test Cases/ })).toBeVisible();

	if (!v2) {
		await page.goto(projectPath("inputs/context"));
		await expect(heading(page, "Context Inputs")).toBeVisible();
		await page.goto(projectPath("deliver"));
		await expect(heading(page, "Export Test Cases")).toBeVisible();
	}
	expect(writes).toEqual([]);
	expect(errors).toEqual([]);
});
