import fs from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";

const contextPath = `/projects/${USE_CASE_PROJECT_ID}/context`;
test.setTimeout(45_000);
const otherProject = useCaseProjectFixture({ project_id: "context-other-project", name: "Other context project" });
const notesInput = (page) => page.getByRole("textbox", { name: "Context notes", exact: true });
const appLinkInput = (page) => page.getByRole("textbox", { name: "Application link", exact: true });
const analyzeButton = (page) => page.getByRole("button", { name: "Analyze Context", exact: true });

async function openContext(page, { failOn = 0, dropReplies = 0, savedContext = null } = {}) {
	// Never fall through to a live service, including optional guidance queries.
	await page.route("**/*", (route) => {
		if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.fallback();
		if (new URL(route.request().url()).pathname.endsWith("/knowledge")) {
			return route.fulfill({ json: { entries: [], skills: [], features: {}, revision: "0" } });
		}
		return route.fulfill({ status: 404, json: { detail: "No fixture for this optional endpoint" } });
	});
	const api = await installUseCaseReviewApi(page);
	const inputs = [];
	const requestIds = [];
	const receipts = new Map();
	if (savedContext) {
		const project = api.getProject();
		project.current_snapshots.context = { snapshot_id: "existing-context", payload: savedContext };
		project.stage_state.context = { approved: true, stale: false, current_snapshot_id: "existing-context" };
		api.setProject(project);
	}
	await page.route(`**/projects/${otherProject.project_id}`, (route) => route.fulfill({ json: otherProject }));
	await page.route("**/projects", (route) => route.fulfill({ json: { projects: [api.getProject(), otherProject] } }));
	await page.route("**/requirements/enrich", async (route) => {
		const input = route.request().postDataJSON();
		const requestId = route.request().headers()["x-request-id"];
		inputs.push(input);
		requestIds.push(requestId);
		if (inputs.length === failOn) {
			return route.fulfill({ status: 503, json: { detail: "Context service temporarily unavailable" } });
		}
		const respond = (payload) => (inputs.length <= dropReplies ? route.abort("failed") : route.fulfill({ json: payload }));
		if (receipts.has(requestId)) return respond(receipts.get(requestId));
		const project = api.getProject();
		if (input.project_id !== project.project_id || input.base_project_revision !== project.current_revision) {
			return route.fulfill({ status: 409, json: { detail: "Stale or wrong project" } });
		}
		project.current_revision += 1;
		const groundedContext = input.grounded_context || {
			artifact_sources: input.app_link ? [{ id: "SRC-app", url: input.app_link, source_type: "app" }] : [],
			ui_elements: input.app_link ? [{ id: "CTRL-1", name: "Verify checkout", element_type: "Button" }] : [],
			workflows: [],
		};
		const payload = { ...input, grounded_context: groundedContext };
		project.current_snapshots.context = { snapshot_id: `context-${project.current_revision}`, payload };
		project.stage_state.context = { approved: true, stale: false, current_snapshot_id: `context-${project.current_revision}` };
		api.setProject(project);
		receipts.set(requestId, payload);
		return respond(payload);
	});
	await seedAuthenticatedSession(page);
	await page.goto(contextPath);
	await expect(notesInput(page)).toBeVisible();
	return { ...api, inputs, requestIds };
}

async function saveContext(page) {
	const response = page.waitForResponse((response) => new URL(response.url()).pathname === "/requirements/enrich");
	await analyzeButton(page).click();
	expect((await response).ok()).toBe(true);
	await expect(analyzeButton(page)).toBeEnabled();
	await expect(page.getByRole("heading", { name: "Grounded Context", exact: true })).toBeVisible();
}

test("saves notes alone verbatim, preserves requirements and restores notes after reload @p1", async ({ page }) => {
	const api = await openContext(page);
	const before = api.getProject();
	await expect(analyzeButton(page)).toHaveCount(0);
	await notesInput(page).fill(" \n \t ");
	await expect(analyzeButton(page)).toHaveCount(0);
	const notes = `  ${(await fs.readFile(new URL("./fixtures/room-booking-context.txt", import.meta.url), "utf8")).trim()}\n\n`;
	await notesInput(page).fill(notes);
	await expect(notesInput(page)).toHaveAttribute("aria-describedby", /hint$/);
	await saveContext(page);
	expect(api.inputs).toHaveLength(1);
	expect(api.inputs[0]).toMatchObject({ notes, app_link: null, prototype_link: null, diagram_links: null, image_links: null });
	expect(api.getProject().current_snapshots.requirements).toEqual(before.current_snapshots.requirements);
	expect(api.getProject().stage_state.requirements).toEqual(before.stage_state.requirements);
	await page.reload();
	await expect(notesInput(page)).toHaveValue(notes);
	await expect(page.getByRole("link", { name: "Context, Complete", exact: true })).toBeVisible();
});

test("retains the draft after an analysis failure and saves it on retry", async ({ page }) => {
	const api = await openContext(page, { failOn: 1 });
	const notes = "Alice owns B-EXISTING. Bob cannot cancel it.\nReset the fixtures before each test.";
	await notesInput(page).fill(notes);
	await analyzeButton(page).click();
	await expect(page.getByText("Context analysis failed: Context service temporarily unavailable", { exact: true })).toBeVisible();
	await expect(notesInput(page)).toHaveValue(notes);
	expect(api.getProject().current_snapshots.context).toBeUndefined();
	await saveContext(page);
	expect(api.inputs.map((input) => input.notes)).toEqual([notes, notes]);
	await expect(page.getByRole("alert")).toHaveCount(0);
	await page.reload();
	await expect(notesInput(page)).toHaveValue(notes);
});

test("uses fresh grounding for edited inputs and keeps the last evidence after a failed save @p1", async ({ page }) => {
	const oldLink = "https://booking.example.test/";
	const newLink = "https://booking.example.test/checkout";
	const prerequisite = { name: "employee", kind: "test_data", reference: "fixture:employee", status: "configured" };
	const api = await openContext(page, {
		failOn: 2,
		savedContext: {
			app_link: oldLink,
			execution_prerequisites: [prerequisite],
			grounded_context: { artifact_sources: [{ id: "SRC-app", url: oldLink, source_type: "app" }] },
		},
	});
	await expect(page.getByRole("checkbox", { name: oldLink, exact: true })).toBeChecked();
	await appLinkInput(page).fill("https://booking.example.test");
	await saveContext(page);
	expect(api.inputs[0].grounded_context.artifact_sources).toHaveLength(1);
	await appLinkInput(page).fill(newLink);
	await analyzeButton(page).click();
	await expect(page.getByRole("alert")).toContainText("Context service temporarily unavailable");
	await expect(page.getByRole("checkbox", { name: oldLink, exact: true })).toBeChecked();
	await expect(appLinkInput(page)).toHaveValue(newLink);
	expect(api.inputs[1]).toMatchObject({ grounded_context: null, execution_prerequisites: [prerequisite] });
	await saveContext(page);
	await expect(page.getByRole("checkbox", { name: newLink, exact: true })).toBeChecked();
	await expect(page.getByText("Button: Verify checkout", { exact: true })).toBeVisible();
	expect(api.requestIds[2]).not.toBe(api.requestIds[1]);
	await notesInput(page).fill("Check the employee fixture");
	await saveContext(page);
	expect(api.inputs[3]).toMatchObject({ grounded_context: null, execution_prerequisites: [prerequisite] });
});

test("recovers a saved Context with the original request after two lost responses @p1", async ({ page }) => {
	const api = await openContext(page, { dropReplies: 2 });
	const before = api.getProject().current_revision;
	await notesInput(page).fill("Reserve only one room per request.");
	await analyzeButton(page).click();
	await expect(page.getByRole("alert")).toContainText("Context analysis could not be confirmed. Retry with the same inputs.");
	await saveContext(page);
	expect(api.inputs).toHaveLength(3);
	expect(api.inputs).toEqual(Array(3).fill(api.inputs[0]));
	expect(api.requestIds[0]).toBeTruthy();
	expect(new Set(api.requestIds).size).toBe(1);
	expect(api.getProject().current_revision).toBe(before + 1);
});

test("keeps notes alongside links, isolates projects and allows clearing notes in a linked context", async ({ page }) => {
	const api = await openContext(page);
	const notes = "Atlas and Birch are separate rooms.\nKeep the booking ID after retry.";
	await notesInput(page).fill(notes);
	await page.getByRole("textbox", { name: "Application link", exact: true }).fill("https://booking.example.test");
	await page.getByRole("textbox", { name: "Diagram links", exact: true }).fill("https://booking.example.test/flow.svg");
	await saveContext(page);
	expect(api.inputs[0]).toMatchObject({
		notes,
		app_link: "https://booking.example.test",
		diagram_links: ["https://booking.example.test/flow.svg"],
	});
	await page.getByRole("button", { name: "Open QA project menu", exact: true }).click();
	await page.getByRole("button", { name: "Open QA project Other context project", exact: true }).click();
	await page.getByRole("link", { name: /^Context,/ }).click();
	await expect(notesInput(page)).toHaveValue("");
	await expect(analyzeButton(page)).toHaveCount(0);
	await page.getByRole("button", { name: "Open QA project menu", exact: true }).click();
	await page.getByRole("button", { name: "Open QA project Mercury Checkout", exact: true }).click();
	await page.getByRole("link", { name: /^Context,/ }).click();
	await expect(notesInput(page)).toHaveValue(notes);
	await page.getByRole("button", { name: "Clear Analysis", exact: true }).click();
	await expect(notesInput(page)).toHaveValue(notes);
	await notesInput(page).fill("");
	await saveContext(page);
	expect(api.inputs.at(-1)).toMatchObject({ notes: null, app_link: "https://booking.example.test" });
	await page.reload();
	await expect(notesInput(page)).toHaveValue("");
	await expect(page.getByRole("textbox", { name: "Application link", exact: true })).toHaveValue("https://booking.example.test");
});

for (const width of [390, 1440]) {
	test(`context notes are usable without horizontal overflow at ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		await openContext(page);
		await notesInput(page).fill("Long fixture identifier: " + "B".repeat(200));
		await expect(analyzeButton(page)).toBeVisible();
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBe(true);
	});
}
