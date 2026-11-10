import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectNoDocumentOverflow } from "./support/layout.js";
import { installUseCaseReviewApi, USE_CASE_PROJECT_ID, useCaseProjectFixture } from "./support/use-case-review.js";

const root = `/projects/${USE_CASE_PROJECT_ID}`;
const rows = [
	{ id: "REQ-101", text: "Customers can complete express checkout with a saved payment method." },
	{ id: "REQ-202", text: "High-value purchases require approval from a checkout supervisor." },
].map((requirement, index) => ({
	...requirement,
	requirement_uid: `checkout-${index + 1}`,
	review_status: "Needs Review",
	content_version: 1,
	lifecycle_status: "active",
	source_system: "file",
	sources: [
		{
			source_id: "checkout-doc",
			source_version: "v1",
			label: "pasted-requirements.md",
			original_requirement_ids: [`CHECKOUT-${index + 1}`],
			excerpt: requirement.text,
			excerpt_verified: true,
		},
	],
}));

const source = rows.map(({ text }) => text).join("\n");
const uploadName = "checkout-policy.md";
const uploadOriginalId = "PAYMENT-POLICY-07";
const uploadText = `## ${uploadOriginalId}\n${rows[0].text}\n`;
const uploadedRequirement = {
	...rows[0],
	id: "REQ-007",
	requirement_uid: "incoming-policy-7",
	sources: [
		{
			source_id: "checkout-policy",
			source_version: "policy-v1",
			label: uploadName,
			original_requirement_ids: [uploadOriginalId],
			source_section: "Lines 2-2",
			excerpt: rows[0].text,
			excerpt_verified: true,
		},
	],
};
const notes = "  Use a synthetic saved card.\nKeep the checkout account isolated.  ";
const target = "https://checkout.example.test";
const objective = "Prioritize confirmation behavior after a saved-card retry.";
const question = "What purchase amount requires supervisor approval?";
const reason = "The policy owner needs to confirm the threshold.";
const workspace = (page, name) => page.getByRole("navigation", { name: "Project workspaces" }).getByRole("link", { name, exact: true });
const heading = (page, name) => page.getByRole("heading", { name, exact: true });
const button = (page, name) => page.getByRole("button", { name, exact: true });
const notesInput = (page) => page.getByRole("textbox", { name: "Context notes", exact: true });
const comparison = (page) => page.getByRole("region", { name: "Compare incoming requirements", exact: true });

function saveSnapshot(project, stage, payload, { approved = false, stale = false } = {}) {
	const version = (project.current_snapshots[stage]?.version || 0) + 1;
	const snapshotId = `checkout-${stage}-${version}`;
	project.current_snapshots[stage] = { snapshot_id: snapshotId, stage, version, payload, approved };
	project.stage_state[stage] = { current_snapshot_id: snapshotId, version, approved, stale };
	return snapshotId;
}

async function fixture(page, { existing = false } = {}) {
	const initialProject = useCaseProjectFixture({ current_revision: 0, current_snapshots: {}, stage_state: {} });
	if (existing) {
		saveSnapshot(
			initialProject,
			"requirements",
			{ requirements: rows.map((row) => ({ ...row, review_status: "Approved" })) },
			{ approved: true }
		);
		initialProject.current_revision++;
	}
	const writes = [];
	const errors = [];
	const contextInputs = [];
	let pending = null;
	let finding = null;
	let imports = 0;
	page.on("pageerror", (error) => errors.push(error.message));
	page.on("console", (message) => {
		if (message.type() === "error" && /same key|unique.*key/i.test(message.text())) errors.push(message.text());
	});
	page.on("request", (request) => {
		if (["fetch", "xhr"].includes(request.resourceType()) && !["GET", "OPTIONS", "HEAD"].includes(request.method()))
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
	});
	await page.route("**/*", (route) => {
		if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.fallback();
		if (new URL(route.request().url()).pathname.endsWith("/knowledge")) {
			return route.fulfill({ json: { entries: [], skills: [], features: {}, revision: "0" } });
		}
		return route.fulfill({ status: 404, json: { detail: "No fixture for this optional endpoint." } });
	});
	const api = await installUseCaseReviewApi(page, { initialProject });
	const update = (change) => {
		const project = api.getProject();
		project.current_revision++;
		change(project);
		api.setProject(project);
		return project;
	};
	await page.route(`**${root}/requirement-imports`, (route) => route.fulfill({ json: pending ? [pending] : [] }));
	const stageComparison = (sourceName, incomingRows) => {
		const project = api.getProject();
		const baseline = project.current_snapshots.requirements?.payload.requirements || [];
		return {
			import_id: `import-${++imports}`,
			project_id: project.project_id,
			base_project_revision: project.current_revision,
			source_name: sourceName,
			operation: "requirements.parse",
			status: "pending",
			current_requirements: baseline,
			candidates: incomingRows.map((requirement, index) => {
				const unchanged = baseline.find(({ text }) => text === requirement.text);
				return {
					candidate_id: `candidate-${index + 1}`,
					requirement,
					classification: unchanged ? "unchanged" : "new",
					target_requirement_uid: unchanged?.requirement_uid || null,
					suggestions: [],
					reason: unchanged ? "Exact content match." : "New requirement.",
				};
			}),
			recovery_snapshot_ids: [],
			suggested_update_scope: [],
			counts: {},
			warnings: [],
		};
	};
	await page.route("**/requirements/parse", (route) => {
		const body = route.request().postData();
		const uploaded = body.includes(`filename="${uploadName}"`);
		const name = uploaded ? uploadName : "pasted-requirements.md";
		expect(body).toContain(`filename="${name}"`);
		expect(body).toContain('name="import_mode"\r\n\r\nreview');
		expect(body).toContain(uploaded ? uploadText : source);
		pending = stageComparison(name, uploaded ? [uploadedRequirement] : rows);
		return route.fulfill({ json: pending });
	});
	await page.route(`**${root}/requirement-imports/*`, (route) => {
		expect(route.request().method()).toBe("DELETE");
		expect(new URL(route.request().url()).pathname).toBe(`${root}/requirement-imports/${pending.import_id}`);
		pending = null;
		return route.fulfill({ status: 204 });
	});
	await page.route(`**${root}/requirement-imports/*/compare`, (route) => {
		expect(route.request().method()).toBe("POST");
		expect(new URL(route.request().url()).pathname).toBe(`${root}/requirement-imports/${pending.import_id}/compare`);
		pending = stageComparison(
			pending.source_name,
			pending.candidates.map(({ requirement }) => requirement)
		);
		return route.fulfill({ json: pending });
	});
	await page.route(`**${root}/requirement-imports/*/apply`, (route) => {
		const request = route.request().postDataJSON();
		const existingRows = pending.current_requirements;
		expect(new URL(route.request().url()).pathname).toBe(`${root}/requirement-imports/${pending.import_id}/apply`);
		expect(pending.base_project_revision).toBe(api.getProject().current_revision);
		expect(request).toMatchObject({
			base_project_revision: pending.base_project_revision,
			decisions: pending.candidates.map(({ candidate_id, target_requirement_uid }) => ({
				candidate_id,
				target_requirement_uid,
				action: target_requirement_uid ? "keep" : "add",
			})),
			update_scope: [],
			confirm_retirements: false,
		});
		expect(request.idempotency_key).toBeTruthy();
		const added = pending.candidates.filter(({ target_requirement_uid }) => !target_requirement_uid).map(({ requirement }) => requirement);
		const kept = existingRows.map((row) => {
			const match = pending.candidates.find(({ target_requirement_uid }) => target_requirement_uid === row.requirement_uid);
			const additional = (match?.requirement.sources || []).filter(
				(source) =>
					!row.sources.some(({ source_id, source_version }) => source_id === source.source_id && source_version === source.source_version)
			);
			return { ...row, sources: [...row.sources, ...additional] };
		});
		const requirements = [...kept, ...added];
		const project = update((current) => {
			saveSnapshot(current, "requirements", {
				requirements,
				import_changes: { added: added.length, updated: 0, retired: 0, active: requirements.length },
			});
			if (current.stage_state.context) current.stage_state.context.stale = true;
		});
		pending = null;
		return route.fulfill({ json: project });
	});
	await page.route("**/requirements/enrich", (route) => {
		const input = route.request().postDataJSON();
		contextInputs.push(input);
		expect(input).toMatchObject({
			project_id: USE_CASE_PROJECT_ID,
			base_project_revision: api.getProject().current_revision,
			app_link: target,
			notes,
			grounded_context: null,
		});
		if (contextInputs.length === 1) {
			return route.fulfill({ status: 503, json: { detail: { code: "context_unavailable", message: "Context service unavailable." } } });
		}
		const artifact = { id: "ART-APP-01", source_type: "app", label: "Application link", url: target, status: "Unavailable" };
		const payload = { ...input, grounded_context: { artifact_sources: [artifact], ui_elements: [], workflows: [] } };
		update((current) => saveSnapshot(current, "context", payload, { approved: true }));
		return route.fulfill({ json: payload });
	});
	await page.route(`**${root}/requirements/reviews`, (route) => {
		const { base_project_revision: revision, reviews } = route.request().postDataJSON();
		expect(revision).toBe(api.getProject().current_revision);
		expect(reviews).toEqual([{ requirement_uid: rows[0].requirement_uid, review_status: "Approved", quality_flags: [] }]);
		const project = update((current) => {
			const prior = current.current_snapshots.requirements;
			saveSnapshot(current, "requirements", {
				...prior.payload,
				requirements: prior.payload.requirements.map((row) =>
					row.requirement_uid === rows[0].requirement_uid ? { ...row, ...reviews[0] } : row
				),
			});
		});
		return route.fulfill({ json: project });
	});
	await page.route(`**${root}`, (route) => {
		if (route.request().method() !== "PATCH") return route.fallback();
		const input = route.request().postDataJSON();
		expect(input).toEqual({
			base_project_revision: api.getProject().current_revision,
			testing_intent: { objective, risk_emphasis: "edge_cases" },
		});
		return route.fulfill({
			json: update((current) => {
				current.testing_intent = input.testing_intent;
			}),
		});
	});
	const markScope = (current, status) => {
		const prior = current.current_snapshots.requirements;
		return saveSnapshot(current, "requirements", {
			...prior.payload,
			requirements: prior.payload.requirements.map((row) =>
				row.id === rows[1].id
					? { ...row, clarification_status: status, review_status: status === "excluded" ? "Rejected" : "Needs Review" }
					: row
			),
		});
	};
	await page.route(`**${root}/quality-findings`, (route) => {
		if (route.request().method() === "GET") {
			return route.fulfill({ json: { findings: finding ? [finding] : [], project_revision: api.getProject().current_revision } });
		}
		const input = route.request().postDataJSON();
		const original = api.getProject().current_snapshots.requirements.snapshot_id;
		expect(input).toMatchObject({
			base_project_revision: api.getProject().current_revision,
			source_snapshot_id: original,
			category: "ambiguous_requirement",
			summary: question,
			expected_behavior: "Unresolved: a source-backed business decision is required.",
			requirement_ids: [rows[1].id],
		});
		const project = update((current) => {
			finding = {
				...input,
				id: "threshold-question",
				status: "open",
				original_source_snapshot_id: original,
				source_snapshot_id: markScope(current, "open"),
			};
		});
		return route.fulfill({ json: { finding, project_revision: project.current_revision } });
	});
	await page.route(`**${root}/quality-findings/threshold-question`, (route) => {
		const input = route.request().postDataJSON();
		expect(input.base_project_revision).toBe(api.getProject().current_revision);
		expect(input.reason).toBe(reason);
		expect(["defer", "exclude"]).toContain(input.action);
		const status = input.action === "defer" ? "deferred" : "excluded";
		const project = update((current) => {
			finding = { ...finding, status, disposition_reason: reason, source_snapshot_id: markScope(current, status) };
		});
		return route.fulfill({ json: { finding, project_revision: project.current_revision } });
	});
	await seedAuthenticatedSession(page);
	return { ...api, writes, errors, contextInputs };
}

async function comparePastedSource(page) {
	const importToggle = button(page, "Import requirements");
	if (await importToggle.isVisible()) await importToggle.click();
	await page.getByRole("radio", { name: "Paste text", exact: true }).check();
	await page.getByLabel("Requirements text", { exact: true }).fill(source);
	await button(page, "Parse Requirements").click();
	await expect(comparison(page)).toBeVisible();
}

async function compareUploadedSource(page) {
	const importToggle = button(page, "Import requirements");
	if (await importToggle.isVisible()) await importToggle.click();
	await page.getByRole("radio", { name: "File upload", exact: true }).check();
	await page.getByLabel("Requirements file", { exact: true }).setInputFiles({
		name: uploadName,
		mimeType: "text/markdown",
		buffer: Buffer.from(uploadText),
	});
	await button(page, "Parse Requirements").click();
	await expect(heading(page, "Compare incoming requirements")).toBeFocused();
}

test.describe("Intake and scope in the 2.0 workspaces", () => {
	test.skip(process.env.E2E_PROJECT_WORKSPACES_V2 !== "true", "Requires VITE_PROJECT_WORKSPACES_V2=true on the frontend");

	test("pastes, compares and prepares context together, then records an explicit scope decision", async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const { getProject, writes, errors, contextInputs } = await fixture(page);
		await page.goto(`${root}/inputs`);
		await expect(heading(page, "Context Inputs")).toBeVisible();
		expect(writes).toEqual([]);

		await comparePastedSource(page);
		await expect(comparison(page)).toContainText("2 added");
		await workspace(page, "Review").press("Enter");
		const pendingNotice = page.getByRole("status").filter({ hasText: "A source comparison is open" });
		await expect(pendingNotice).toBeVisible();
		await expect(heading(page, "Requirement Review Workbench")).toHaveCount(0);
		await pendingNotice.getByRole("link", { name: "Inputs", exact: true }).press("Enter");
		await expect(comparison(page)).toBeVisible();
		expect(getProject().current_snapshots.requirements).toBeUndefined();
		await button(page, "Apply reviewed changes").click();
		await expect(comparison(page)).toHaveCount(0);
		await expect(button(page, "Review scope")).toBeFocused();
		await expect(page).toHaveURL(`${root}/inputs`);
		await expect(heading(page, "Requirement Review Workbench")).toHaveCount(0);

		await page.getByRole("textbox", { name: "Application link", exact: true }).fill(target);
		await notesInput(page).fill(notes);
		await page.reload();
		await expect(notesInput(page)).toHaveValue(notes);
		await expect(page.getByRole("status").filter({ hasText: "Unsaved context on this device." })).toBeVisible();
		await expect(page.getByRole("textbox", { name: "Application link", exact: true })).toHaveValue(target);
		expect(contextInputs).toHaveLength(0);
		await button(page, "Analyze Context").click();
		await expect(page.getByRole("alert").filter({ hasText: "Context service unavailable." })).toBeVisible();
		await expect(notesInput(page)).toHaveValue(notes);
		expect(getProject().current_snapshots.context).toBeUndefined();
		await button(page, "Analyze Context").click();
		await expect(heading(page, "Saved context evidence")).toBeVisible();
		expect(contextInputs).toHaveLength(2);
		expect(getProject().current_snapshots.context.payload).toMatchObject({ notes, grounded_context: { ui_elements: [] } });
		await expect(page.getByText(/app · Unavailable$/)).toBeVisible();
		await expect(page.getByRole("status").filter({ hasText: "Unsaved context on this device." })).toHaveCount(0);

		const intent = page.getByRole("region", { name: "Testing intent", exact: true });
		const objectiveInput = intent.getByRole("textbox", { name: "Testing objective", exact: true });
		const risk = intent.getByRole("combobox", { name: "Risk emphasis", exact: true });
		await objectiveInput.fill(objective);
		await risk.selectOption("edge_cases");
		await expect(intent).toContainText("Unsaved changes on this device.");
		await page.reload();
		await expect(objectiveInput).toHaveValue(objective);
		await expect(risk).toHaveValue("edge_cases");
		expect(getProject().testing_intent).toBeUndefined();
		await button(intent, "Save testing intent").click();
		await expect(intent).toContainText("Testing intent saved to the project.");
		const savedContext = getProject().current_snapshots.context;
		const savedContextState = getProject().stage_state.context;
		await expectNoDocumentOverflow(page, "Inputs");
		await page.screenshot({ path: testInfo.outputPath("inputs-context-and-intent.png"), fullPage: true });

		await button(page, "Review scope").press("Enter");
		await expect(page).toHaveURL(`${root}/review`);
		await expect(heading(page, "Requirement Review Workbench")).toBeVisible();
		await expect(notesInput(page)).toHaveCount(0);
		await expect(intent).toHaveCount(0);
		await expect(button(page, "Import requirements")).toHaveCount(0);
		expect(getProject().current_snapshots.requirements.payload.requirements.map(({ review_status }) => review_status)).toEqual([
			"Needs Review",
			"Needs Review",
		]);
		await page.getByRole("button", { name: /^Review REQ-101:/ }).click();
		await page.getByText("Source evidence (1)", { exact: true }).click();
		await expect(page.locator("blockquote").filter({ hasText: rows[0].text })).toBeVisible();
		await page.getByRole("combobox", { name: "Review status for REQ-101", exact: true }).selectOption("Approved");
		await expect(page.getByText(/1\/2 approved/)).toBeVisible();
		expect(getProject().current_snapshots.context).toEqual(savedContext);
		expect(getProject().stage_state.context).toEqual(savedContextState);

		await workspace(page, "Inputs").press("Enter");
		await expect(notesInput(page)).toHaveValue(notes);
		await comparePastedSource(page);
		await expect(comparison(page)).toContainText("Unchanged (2)");
		await button(page, "Apply reviewed changes").click();
		await expect(comparison(page)).toHaveCount(0);
		await workspace(page, "Review").press("Enter");
		await expect(page.getByText(/1\/2 approved/)).toBeVisible();
		await page.getByRole("button", { name: /^Review REQ-101:/ }).click();
		await page.getByText("Source evidence (1)", { exact: true }).click();
		await expect(page.getByText("Original IDs: CHECKOUT-1", { exact: true })).toBeVisible();
		await expect(page.locator("blockquote").filter({ hasText: rows[0].text })).toBeVisible();
		expect(writes).toEqual([
			"POST /requirements/parse",
			`POST ${root}/requirement-imports/import-1/apply`,
			"POST /requirements/enrich",
			"POST /requirements/enrich",
			`PATCH ${root}`,
			`PATCH ${root}/requirements/reviews`,
			"POST /requirements/parse",
			`POST ${root}/requirement-imports/import-2/apply`,
		]);
		expect(errors).toEqual([]);
	});

	test("keeps both sources and the approved identity after cancellation and stale comparison recovery", async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const { getProject, setProject, writes, errors } = await fixture(page);
		await page.goto("/projects");
		const project = page
			.getByRole("region", { name: "Projects", exact: true })
			.getByRole("listitem")
			.filter({ hasText: getProject().name });
		await project.getByRole("link", { name: "Open", exact: true }).press("Enter");
		await workspace(page, "Inputs").press("Enter");
		await expect(page).toHaveURL(`${root}/inputs`);
		await expect(heading(page, "Context Inputs")).toBeVisible();
		expect(writes).toEqual([]);

		await comparePastedSource(page);
		await button(comparison(page), "Apply reviewed changes").click();
		await button(page, "Review scope").press("Enter");
		const firstRequirement = page.getByRole("button", { name: /^Review REQ-101:/ });
		const reviewStatus = page.getByRole("combobox", { name: "Review status for REQ-101", exact: true });
		await firstRequirement.click();
		await reviewStatus.selectOption("Approved");
		await expect(page.getByText(/1\/2 approved in scope/)).toBeVisible();
		const approvedBaseline = getProject();

		await workspace(page, "Inputs").press("Enter");
		await compareUploadedSource(page);
		await expect(comparison(page)).toContainText("Unchanged (1)");
		await button(comparison(page), `Review incoming requirement: ${uploadedRequirement.text}`).click();
		await comparison(page).getByText("Source evidence and suggested matches", { exact: true }).click();
		await expect(comparison(page).getByText(`Original IDs: ${uploadOriginalId}`, { exact: true })).toBeVisible();
		await expect(comparison(page).locator("blockquote")).toHaveText(uploadedRequirement.text);
		expect(getProject()).toEqual(approvedBaseline);
		await button(comparison(page), "Cancel import").click();
		await expect(comparison(page)).toHaveCount(0);
		await page.reload();
		await expect(heading(page, "Context Inputs")).toBeVisible();
		await expect(button(page, `Review import: ${uploadName}`)).toHaveCount(0);
		expect(getProject()).toEqual(approvedBaseline);
		expect(writes.slice(-2)).toEqual(["POST /requirements/parse", `DELETE ${root}/requirement-imports/import-2`]);

		await compareUploadedSource(page);
		const revisedProject = { ...approvedBaseline, current_revision: approvedBaseline.current_revision + 1 };
		setProject(revisedProject);
		await page.reload();
		await button(page, `Review import: ${uploadName}`).press("Enter");
		await expect(comparison(page).getByRole("alert")).toHaveText("Project changed after this comparison. Compare again before applying.");
		const apply = button(comparison(page), "Apply reviewed changes");
		await expect(apply).toBeDisabled();
		expect(getProject()).toEqual(revisedProject);
		expect(writes.at(-1)).toBe("POST /requirements/parse");
		await button(comparison(page), "Compare again").click();
		await expect(comparison(page).getByText(`${uploadName} · Revision ${revisedProject.current_revision}`, { exact: true })).toBeVisible();
		await expect(apply).toBeEnabled();
		await apply.click();
		await expect(comparison(page)).toHaveCount(0);
		await button(page, "Review scope").press("Enter");
		await page.reload();
		await expect(page).toHaveURL(`${root}/review`);
		await expect(page.getByText(/1\/2 approved in scope/)).toBeVisible();
		await firstRequirement.click();
		await expect(reviewStatus).toHaveValue("Approved");
		const evidence = page.getByText("Source evidence (2)", { exact: true });
		await evidence.click();
		const citations = page.locator("details").filter({ has: evidence });
		await expect(citations.getByText("pasted-requirements.md", { exact: true })).toBeVisible();
		await expect(citations.getByText("Original IDs: CHECKOUT-1", { exact: true })).toBeVisible();
		await expect(citations.getByText(uploadName, { exact: true })).toBeVisible();
		await expect(citations.getByText(`Original IDs: ${uploadOriginalId}`, { exact: true })).toBeVisible();
		await expect(citations.locator("blockquote")).toHaveText([rows[0].text, rows[0].text]);
		const saved = getProject().current_snapshots.requirements.payload.requirements;
		expect(saved).toHaveLength(2);
		expect(saved[0]).toMatchObject({
			id: rows[0].id,
			requirement_uid: rows[0].requirement_uid,
			content_version: 1,
			review_status: "Approved",
		});
		expect(saved[0].sources).toEqual([...rows[0].sources, ...uploadedRequirement.sources]);
		expect(getProject().current_revision).toBe(revisedProject.current_revision + 1);
		expect(writes).toEqual([
			"POST /requirements/parse",
			`POST ${root}/requirement-imports/import-1/apply`,
			`PATCH ${root}/requirements/reviews`,
			"POST /requirements/parse",
			`DELETE ${root}/requirement-imports/import-2`,
			"POST /requirements/parse",
			`POST ${root}/requirement-imports/import-3/compare`,
			`POST ${root}/requirement-imports/import-4/apply`,
		]);
		expect(errors).toEqual([]);
	});

	test("a source-linked clarification removes only its affected requirement from eligible scope", async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const { writes, errors } = await fixture(page, { existing: true });
		await page.goto(`${root}/review`);
		await expect(heading(page, "Requirement Review Workbench")).toBeVisible();
		const queue = page.getByRole("region", { name: "Business clarifications", exact: true });
		await expect(queue).toBeVisible();
		expect(writes).toEqual([]);
		await queue.getByText("Ask about a missing business rule", { exact: true }).click();
		await queue.getByLabel("Requirement needing clarification").selectOption(rows[1].id);
		await queue.getByLabel("What needs clarification?").fill(question);
		await button(queue, "Add clarification").click();
		await expect(queue).toContainText("REQ-202 · open");
		await queue.getByText("Source and decision history", { exact: true }).click();
		await expect(queue.locator("blockquote").filter({ hasText: rows[1].text })).toBeVisible();
		await expect(
			page.getByRole("status").filter({ hasText: /^1\/2 approved in scope · 1 open · 0 deferred · 0 excluded · 0 rejected$/ })
		).toHaveCount(1);
		await expect(button(page, "Review scenarios")).toBeDisabled();
		await page.getByRole("button", { name: /^Review REQ-202:/ }).click();
		await expect(
			page.getByRole("combobox", { name: "Review status for REQ-202", exact: true }).getByRole("option", { name: "Approved", exact: true })
		).toBeDisabled();
		const panel = page.getByRole("region", { name: "Shared project corrections", exact: true });
		for (const [action, status] of [
			["Defer", "deferred"],
			["Exclude", "excluded"],
		]) {
			await button(queue, "Answer REQ-202").click();
			await panel.getByText("Defer or exclude this scope", { exact: true }).click();
			await panel.getByLabel("Reason for the scope decision").fill(reason);
			await button(panel, `${action} affected scope`).click();
			await expect(queue).toContainText(`REQ-202 · ${status}`);
			const disposition = status === "deferred" ? "1 deferred · 0 excluded · 0 rejected" : "0 deferred · 1 excluded · 1 rejected";
			await expect(page.getByRole("status").filter({ hasText: `1/1 approved in scope · 0 open · ${disposition}` })).toHaveCount(1);
			await expect(button(page, "Review scenarios")).toBeEnabled();
		}
		await page.reload();
		await expect(queue).toContainText("REQ-202 · excluded");
		await expect(
			page.getByRole("status").filter({ hasText: "1/1 approved in scope · 0 open · 0 deferred · 1 excluded · 1 rejected" })
		).toHaveCount(1);
		await expect(page.getByText(/Loading project recommendations/)).toHaveCount(0);
		await queue.getByText("Source and decision history", { exact: true }).click();
		await expect(queue.getByText(reason, { exact: true })).toBeVisible();
		await expectNoDocumentOverflow(page, "Review scope and clarification");
		await page.screenshot({ path: testInfo.outputPath("review-scope-clarification.png"), fullPage: true });
		expect(writes).toEqual([
			`POST ${root}/quality-findings`,
			`POST ${root}/quality-findings/threshold-question`,
			`POST ${root}/quality-findings/threshold-question`,
		]);
		expect(errors).toEqual([]);
	});

	test("earlier context and testing intent drafts are visible for comparison and isolated by account", async ({ page }) => {
		const { getProject, setProject, writes, errors } = await fixture(page, { existing: true });
		const project = getProject();
		saveSnapshot(project, "context", { notes: "Original server context." }, { approved: true });
		setProject(project);
		await page.goto(`${root}/inputs`);
		await expect(notesInput(page)).toHaveValue("Original server context.");
		await notesInput(page).fill(notes);
		const intent = page.getByRole("region", { name: "Testing intent", exact: true });
		const objectiveInput = intent.getByRole("textbox", { name: "Testing objective", exact: true });
		await objectiveInput.fill(objective);
		await expect(page.getByRole("status").filter({ hasText: "Unsaved context on this device." })).toBeVisible();
		await expect(intent).toContainText("Unsaved changes on this device.");

		project.current_revision++;
		project.testing_intent = { objective: "Latest server objective.", risk_emphasis: "regression" };
		saveSnapshot(project, "context", { notes: "Latest server context." }, { approved: true });
		setProject(project);
		await page.reload();
		await expect(notesInput(page)).toHaveValue("Latest server context.");
		await expect(objectiveInput).toHaveValue("Latest server objective.");
		const earlierContext = page.getByText("Context draft from an earlier snapshot", { exact: true });
		const earlierIntent = intent.getByText("Draft from an earlier revision", { exact: true });
		await earlierContext.click();
		await expect(page.getByText(notes, { exact: true })).toBeVisible();
		await earlierIntent.click();
		await expect(intent.getByText(objective, { exact: true })).toBeVisible();

		const other = { sub: "another-intake-reviewer", name: "Another intake reviewer", email: "intake-reviewer@example.test" };
		await seedAuthenticatedSession(page, other);
		await page.route("**/auth/me", (route) => route.fulfill({ json: other }));
		await page.reload();
		await expect(notesInput(page)).toHaveValue("Latest server context.");
		await expect(objectiveInput).toHaveValue("Latest server objective.");
		await expect(earlierContext).toHaveCount(0);
		await expect(earlierIntent).toHaveCount(0);
		expect(writes).toEqual([]);
		expect(errors).toEqual([]);
	});
});
