import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installWorkspaceApi, projectDetailFixture, workspaceProjectFixture, workspaceSummaryFixture } from "./support/workspace.js";

const ID = "requirement-import-review";
const when = "2026-09-12T06:00:00Z";
const originals = Array.from({ length: 8 }, (_, i) => ({
	id: `REQ-${String(i + 1).padStart(3, "0")}`,
	requirement_uid: `original-${i + 1}`,
	text: `The system shall preserve task behavior ${i + 1}.`,
	review_status: "Approved",
	source_system: "file",
	source_path: "audit-requirements.md",
	content_version: 1,
	lifecycle_status: "active",
	quality_flags: [],
	sources: [
		{
			source_id: "document",
			source_version: "v1",
			import_id: "original",
			label: "audit-requirements.md",
			excerpt: `Task behavior ${i + 1}`,
		},
	],
}));
const incoming = ["The agricultural portal displays PDF resources.", "The reader presents hierarchical category navigation."].map(
	(text, i) => ({
		candidate_id: `incoming-${i + 1}`,
		requirement: { id: `REQ-00${i + 1}`, text, source_system: "jira", source_issue_key: "TX-6" },
		classification: "new",
		target_requirement_uid: null,
		reason: "No matching requirement found; add to the project.",
		suggestions: [],
	})
);

async function setup(
	page,
	{
		candidates = incoming,
		revision = 7,
		failedApply = false,
		extractionInterruption,
		connector,
		pendingOnLoad = false,
		emptyBaseline = false,
		sourceName = "TX-6",
		sourceEvidenceFindings = [],
	} = {}
) {
	await seedAuthenticatedSession(page);
	const baseline = emptyBaseline ? [] : originals;
	const summary = workspaceProjectFixture({ project_id: ID, name: "Audit project", project_revision: revision });
	let project = projectDetailFixture(summary, {
		current_revision: revision,
		stage_state: {
			requirements: { version: 1, approved: true, current_snapshot_id: "original" },
			test_cases: { version: 1, approved: true, current_snapshot_id: "tests", stale: false },
		},
		current_snapshots: {
			requirements: { snapshot_id: "original", payload: { requirements: structuredClone(baseline) } },
			test_cases: {
				snapshot_id: "tests",
				payload: { test_cases: [{ id: "TC-001", title: "Existing task test", linked_requirement_ids: ["REQ-001"], steps: [] }] },
			},
		},
	});
	const preview = {
		import_id: "import-1",
		project_id: ID,
		base_project_revision: 7,
		source_name: sourceName,
		operation: connector === "azure-devops" ? "requirements.import.azure_devops" : "requirements.import.jira",
		status: "pending",
		created_at: when,
		current_requirements: structuredClone(baseline),
		candidates: structuredClone(candidates),
		suggested_update_scope: [],
		counts: {},
		warnings: [],
		source_evidence_findings: sourceEvidenceFindings,
		recovery_snapshot_ids: [],
	};
	let pending = pendingOnLoad ? [preview] : [];
	const applies = [];
	const guidanceReads = [];
	const summaryReads = [];
	await installWorkspaceApi(page, { summary: workspaceSummaryFixture({ projects: [summary] }), projectDetails: { [ID]: project } });
	await page.route(`**/projects/${ID}/orchestrator/status`, (route) => {
		guidanceReads.push(project.current_revision);
		const pending = project.current_snapshots.requirements.payload.requirements.some((r) => r.review_status !== "Approved");
		return route.fulfill({
			json: {
				project_id: ID,
				project_revision: project.current_revision,
				stages: {},
				next_actions: pending
					? [
							{
								action: "approve",
								stage: "requirements",
								label: "Review Requirements",
								primary: true,
								enabled: true,
								reason: "Review incoming requirements before applying changes.",
							},
						]
					: [],
			},
		});
	});
	await page.route("**/workspace/summary?*", (route) => {
		summaryReads.push(project.current_revision);
		return route.fulfill({ json: workspaceSummaryFixture({ projects: [{ ...summary, project_revision: project.current_revision }] }) });
	});

	await page.route(`**/projects/${ID}`, (route) => route.fulfill({ json: project }));
	await page.route(`**/projects/${ID}/requirement-imports`, (route) => route.fulfill({ json: pending }));
	const extractions = [];
	const finishExtraction = (route, input) => {
		const request = route.request();
		extractions.push({ id: request.headers()["x-request-id"], body: input });
		if (extractions.length === 1 && extractionInterruption === "failed")
			return route.fulfill({ status: 503, json: { detail: "Project persistence is unavailable." } });
		pending = [preview];
		if (extractions.length === 1 && extractionInterruption === "transport") return route.abort("failed");
		if (extractions.length <= 2 && extractionInterruption === "repeated-transport") return route.abort("failed");
		if (extractions.length === 1 && extractionInterruption === "unconfirmed")
			return route.fulfill({ status: 503, json: { detail: { code: "requirement_extraction_unconfirmed" } } });
		if (extractions.length === 1 && extractionInterruption === "gateway")
			return route.fulfill({ status: 504, contentType: "text/html", body: "<h1>Gateway timeout</h1>" });
		return route.fulfill({ json: preview });
	};
	await page.route("**/requirements/parse", (route) => {
		const request = route.request();
		const body = request.postData();
		expect(body).toContain('name="import_mode"');
		expect(body).toContain("review");
		const boundary = request.headers()["content-type"].split("boundary=")[1];
		return finishExtraction(route, body.replaceAll(boundary, "boundary"));
	});
	if (connector) {
		const isJira = connector === "jira";
		const endpoints = isJira
			? {
					connection: { connected: true, connection: { site_url: "https://jira.example.test" } },
					"projects?*": { projects: [{ project_id: "123", key: "QA", name: "QA Project" }] },
					"projects/*/issue-types": { issue_types: [{ name: "Epic" }] },
					"issues/search?*": { issues: [{ key: "QA-416", issue_type: "Epic", summary: "Synthetic Jira source" }] },
				}
			: {
					connection: { connected: true, connection: { default_project: "QA Project" } },
					"projects?*": { projects: [{ project_id: "123", name: "QA Project" }] },
					"projects/*/work-item-types": { work_item_types: [{ name: "Feature" }] },
					"work-items/search?*": { work_items: [{ work_item_id: 416, work_item_type: "Feature", title: "Synthetic Azure DevOps source" }] },
				};
		for (const [endpoint, payload] of Object.entries(endpoints)) {
			await page.route(`**/integrations/${connector}/${endpoint}`, (route) => route.fulfill({ json: payload }));
		}
		await page.route(`**/integrations/${connector}/import`, (route) => finishExtraction(route, route.request().postDataJSON()));
	}
	await page.route(`**/projects/${ID}/requirement-imports/import-1`, (route) => {
		pending = [];
		return route.fulfill({ status: 204 });
	});
	await page.route(`**/projects/${ID}/requirement-imports/import-1/apply`, (route) => {
		const payload = route.request().postDataJSON();
		applies.push(payload);
		if (failedApply && applies.length === 1)
			return route.fulfill({ status: 503, json: { detail: "Temporary apply failure. Retry the same reviewed changes." } });
		let rows = structuredClone(baseline);
		const targeted = new Set();
		let number = 9;
		const counts = { added: 0, updated: 0, retired: 0 };
		for (const choice of payload.decisions) {
			const c = candidates.find((item) => item.candidate_id === choice.candidate_id);
			if (choice.action === "add") {
				rows.push({
					...c.requirement,
					id: `REQ-${String(number++).padStart(3, "0")}`,
					requirement_uid: c.candidate_id,
					review_status: "Needs Review",
					content_version: 1,
				});
				counts.added++;
			}
			if (["update", "keep"].includes(choice.action)) {
				targeted.add(choice.target_requirement_uid);
				rows = rows.map((r) =>
					r.requirement_uid === choice.target_requirement_uid
						? {
								...r,
								text: c.requirement.text,
								review_status: choice.action === "update" ? "Needs Review" : r.review_status,
								content_version: choice.action === "update" ? 2 : 1,
								sources: [...r.sources, { source_id: "jira", source_version: "v1", label: "TX-6", excerpt: c.requirement.text }],
							}
						: r
				);
				if (choice.action === "update") counts.updated++;
			}
		}
		const retired = rows
			.filter((r) => payload.update_scope.includes(r.requirement_uid) && !targeted.has(r.requirement_uid))
			.map((r) => ({ ...r, lifecycle_status: "retired" }));
		rows = rows.filter((r) => !retired.some((old) => old.requirement_uid === r.requirement_uid));
		counts.retired = retired.length;
		counts.active = rows.length;
		project = {
			...project,
			current_revision: 8,
			stage_state: {
				...project.stage_state,
				requirements: {
					...project.stage_state.requirements,
					current_snapshot_id: "combined",
					approved: rows.every((r) => r.review_status === "Approved"),
				},
				test_cases: { ...project.stage_state.test_cases, stale: true },
			},
			current_snapshots: {
				...project.current_snapshots,
				requirements: { snapshot_id: "combined", payload: { requirements: rows, retired_requirements: retired, import_changes: counts } },
			},
		};
		pending = [];
		return route.fulfill({ json: project });
	});
	await page.goto(`/projects/${ID}/requirements`);
	await expect(page.getByRole("heading", { name: "Requirement Review Workbench" })).toBeVisible();
	return { applies, extractions, preview, guidanceReads, summaryReads, getProject: () => project };
}

async function startExtraction(page) {
	const open = page.getByRole("button", { name: "Import requirements", exact: true });
	if (await open.isVisible()) await open.click();
	await page
		.locator('input[type="file"]')
		.setInputFiles({ name: "new.md", mimeType: "text/markdown", buffer: Buffer.from("Incoming requirements") });
	await page.getByRole("button", { name: "Parse Requirements", exact: true }).click();
}
async function stage(page) {
	await startExtraction(page);
	await expect(page.getByRole("heading", { name: "Compare incoming requirements" })).toBeFocused();
	await expect(page.getByRole("heading", { name: "Upload Requirements", exact: true })).toBeHidden();
}
const workbench = (page) => page.getByRole("region", { name: "Project requirements table", exact: true });
const openRequirement = (page, id) =>
	workbench(page)
		.getByRole("button", { name: new RegExp(`^Review ${id}:`) })
		.click();
const openIncoming = (page, text = incoming[0].requirement.text) =>
	page.getByRole("button", { name: `Review incoming requirement: ${text}`, exact: true }).click();

for (const interruption of ["transport", "unconfirmed", "gateway"]) {
	test(
		`recovers a ${interruption} extraction response with the same upload and request ID`,
		{ tag: interruption === "transport" ? "@p1" : [] },
		async ({ page }) => {
			const state = await setup(page, { extractionInterruption: interruption });
			await stage(page);
			expect(state.extractions).toHaveLength(2);
			expect(state.extractions[0].id).toBeTruthy();
			expect(state.extractions[1]).toEqual(state.extractions[0]);
			expect(state.applies).toHaveLength(0);
			expect(state.getProject().current_revision).toBe(7);
			expect(state.getProject().current_snapshots.requirements.payload.requirements).toHaveLength(8);
		}
	);
}

test("a rejected extraction shows an error and keeps the file available to retry", { tag: "@p1" }, async ({ page }) => {
	const state = await setup(page, { extractionInterruption: "failed" });
	await startExtraction(page);
	await expect(page.getByRole("alert").filter({ hasText: "Project persistence is unavailable" })).toBeVisible();
	expect(state.extractions).toHaveLength(1);
	expect(state.applies).toHaveLength(0);
	await page.getByRole("button", { name: "Parse Requirements", exact: true }).click();
	await expect(page.getByRole("heading", { name: "Compare incoming requirements" })).toBeFocused();
	expect(state.extractions).toHaveLength(2);
	expect(state.extractions[1].id).not.toBe(state.extractions[0].id);
});

test("an uncertain extraction keeps the request ID when the user retries the same file", { tag: "@p1" }, async ({ page }) => {
	const state = await setup(page, { extractionInterruption: "repeated-transport" });
	await startExtraction(page);
	await expect(page.getByRole("alert").filter({ hasText: "The requirements preview could not be confirmed" })).toBeVisible();
	expect(state.extractions).toHaveLength(2);
	await page.getByRole("button", { name: "Parse Requirements", exact: true }).click();
	await expect(page.getByRole("heading", { name: "Compare incoming requirements" })).toBeFocused();
	expect(state.extractions).toEqual(Array(3).fill(state.extractions[0]));
	expect(state.applies).toHaveLength(0);
});

for (const [connector, sourceLabel, buttonLabel, selector] of [
	["jira", "JIRA Cloud", "Import QA-416", { epic_key: "QA-416" }],
	["azure-devops", "Azure DevOps", "Import #416", { project: "QA Project", work_item_id: 416 }],
]) {
	test(`${sourceLabel} retries an uncertain reviewed import with the same selection and request ID`, { tag: "@p1" }, async ({ page }) => {
		const state = await setup(page, { connector, extractionInterruption: "repeated-transport" });
		await page.getByRole("button", { name: "Import requirements", exact: true }).click();
		await page.getByRole("radio", { name: sourceLabel, exact: true }).check();
		await page.locator("#requirement-import-setup").getByRole("button", { name: "Search", exact: true }).click();
		const importButton = page.getByRole("button", { name: buttonLabel, exact: true });
		await importButton.click();
		await expect(page.getByRole("alert").filter({ hasText: "The requirements preview could not be confirmed" })).toBeVisible();
		expect(state.extractions).toHaveLength(2);
		await importButton.click();
		await expect(page.getByRole("heading", { name: "Compare incoming requirements" })).toBeFocused();
		expect(state.extractions[0].id).toBeTruthy();
		expect(state.extractions[0].body).toMatchObject({
			...selector,
			include_children: true,
			import_mode: "review",
			base_project_revision: 7,
		});
		expect(state.extractions).toEqual(Array(3).fill(state.extractions[0]));
		expect(state.applies).toHaveLength(0);
		expect(state.getProject().current_snapshots.requirements.payload.requirements).toHaveLength(8);
	});
}

test("comparison displays verified original IDs and quotes before applying", async ({ page }) => {
	await setup(page, {
		candidates: [
			{
				...incoming[0],
				requirement: {
					...incoming[0].requirement,
					sources: [
						{
							source_id: "room-document",
							source_version: "room-v1",
							label: "rooms.md",
							original_requirement_ids: ["ROOM-101"],
							source_section: "Lines 6-6",
							excerpt: "Attendees must be a whole number from 1 to 8.",
							excerpt_verified: true,
						},
					],
				},
			},
		],
	});
	await stage(page);
	await openIncoming(page);
	await page.getByText("Source evidence and suggested matches", { exact: true }).click();
	await expect(page.getByText("Original IDs: ROOM-101", { exact: true })).toBeVisible();
	await expect(page.locator("blockquote").filter({ hasText: "Attendees must be a whole number from 1 to 8." })).toBeVisible();
	await expect(workbench(page)).toBeHidden();
});

test("shows claimed and omitted ranges with their source group before a reviewer decides", { tag: "@p1" }, async ({ page }) => {
	const source = { source_label: "rooms.md", original_requirement_ids: ["ROOM-101"], source_section: "Lines 6-6" };
	const text = "The system shall confirm a booking with 1 to 6 attendees.";
	const state = await setup(page, {
		candidates: [{ ...incoming[0], requirement: { ...incoming[0].requirement, text }, classification: "needs_decision" }],
		sourceName: "rooms.md",
		sourceEvidenceFindings: [
			{ ...source, kind: "unverified_range", requirement_id: "REQ-001", excerpt: "between 1 and 6 inclusive" },
			{ ...source, kind: "uncovered_range", excerpt: "Attendees must be a whole number from 1 to 6 inclusive." },
		],
	});
	await page.setViewportSize({ width: 390, height: 844 });
	await stage(page);
	const findings = page.getByRole("region", { name: "Numeric source evidence needs review" });
	await expect(findings.getByRole("listitem").filter({ hasText: "Range absent from the incoming quotation" })).toContainText(
		"Incoming REQ-001 · rooms.md · ROOM-101"
	);
	await expect(findings.getByRole("listitem").filter({ hasText: "Source range missing from verified extraction" })).toContainText(
		"Attendees must be a whole number from 1 to 6 inclusive."
	);
	await openIncoming(page, text);
	await expect(page.getByLabel("Decision for incoming-1")).toHaveValue("");
	await expect(page.getByRole("button", { name: "Apply reviewed changes" })).toBeDisabled();
	expect(state.applies).toHaveLength(0);
});

test("a verified source suggestion previews the prior requirement and still needs an explicit choice", { tag: "@p1" }, async ({ page }) => {
	const prior = originals[1];
	const revisedText = "A task can be archived by its owner after completion.";
	const state = await setup(page, {
		candidates: [
			{
				...incoming[0],
				requirement: { ...incoming[0].requirement, text: revisedText, source_system: "file" },
				classification: "needs_decision",
				reason: "Verified original IDs suggest a match. Compare the requirements and choose a decision.",
				suggestions: [
					{ requirement_uid: prior.requirement_uid, reason: "Verified source reference", verified_original_ids: ["TASK-20"] },
					{ requirement_uid: originals[0].requirement_uid, reason: "Similar wording" },
				],
			},
		],
	});
	await stage(page);
	await openIncoming(page, revisedText);
	const apply = page.getByRole("button", { name: "Apply reviewed changes", exact: true });
	const decision = page.getByLabel("Decision for incoming-1", { exact: true });
	const sourceOptions = decision.locator('optgroup[label="Verified source matches"] option');
	await expect(page.getByRole("heading", { name: `Suggested current requirement · ${prior.id}`, exact: true })).toBeVisible();
	await expect(page.getByText(prior.text, { exact: true })).toBeVisible();
	await expect(page.getByText("Verified original IDs: TASK-20", { exact: true })).toBeVisible();
	await expect(sourceOptions).toHaveCount(1);
	await expect(sourceOptions).toHaveAttribute("value", prior.requirement_uid);
	await expect(decision.locator(`:scope > option[value="${originals[0].requirement_uid}"]`)).toHaveCount(1);
	await expect(decision).toHaveValue("");
	await expect(apply).toBeDisabled();
	await decision.selectOption(prior.requirement_uid);
	await expect(page.getByRole("heading", { name: `Current requirement · ${prior.id}`, exact: true })).toBeVisible();
	await apply.click();
	await expect(workbench(page)).toBeVisible();
	expect(state.applies[0].decisions).toEqual([
		{ candidate_id: "incoming-1", action: "update", target_requirement_uid: prior.requirement_uid },
	]);
	expect(state.getProject().current_snapshots.requirements.payload.requirements.find((item) => item.id === prior.id)).toMatchObject({
		requirement_uid: prior.requirement_uid,
		text: revisedText,
		content_version: 2,
		review_status: "Needs Review",
	});
});

test("stages eight plus two, applies ten, and retains IDs and approvals after reload", { tag: "@p1" }, async ({ page }) => {
	const state = await setup(page);
	await stage(page);
	await expect(workbench(page)).toBeHidden();
	expect(state.getProject().current_snapshots.requirements.payload.requirements).toHaveLength(8);
	await expect(page.getByText("2 added · 0 updated · 0 retired · 10 active requirements", { exact: true })).toBeVisible();
	await page.getByRole("button", { name: "Apply reviewed changes" }).click();
	await expect(workbench(page).getByRole("row")).toHaveCount(11);
	await expect(page.getByRole("heading", { name: "Requirement Review Workbench" })).toBeFocused();
	await expect(page.getByRole("button", { name: "Import requirements", exact: true })).toHaveAttribute("aria-expanded", "false");
	await openRequirement(page, "REQ-001");
	await expect(page.getByLabel("Review status for REQ-001", { exact: true })).toHaveValue("Approved");
	await openRequirement(page, "REQ-009");
	await expect(page.getByLabel("Review status for REQ-009", { exact: true })).toHaveValue("Needs Review");
	expect(state.applies[0].update_scope).toEqual([]);
	expect(state.getProject().stage_state.test_cases.stale).toBeTruthy();
	expect(state.getProject().current_snapshots.test_cases.payload.test_cases).toHaveLength(1);
	await expect.poll(() => state.guidanceReads.includes(8) && state.summaryReads.includes(8)).toBe(true);
	await page.getByRole("link", { name: "Overview", exact: true }).click();
	await expect(page.getByLabel("Contextual task").getByRole("heading", { name: "Review Requirements" })).toBeVisible();
	await page.getByRole("button", { name: "Open Requirements", exact: true }).click();

	await page.reload();
	await expect(workbench(page).getByRole("row")).toHaveCount(11);
});

test("cross-source update requires resolving ambiguity and retains requirement ID", async ({ page }) => {
	const candidates = [
		{
			...incoming[0],
			classification: "needs_decision",
			reason: "Possible cross-source revision",
			suggestions: [{ requirement_uid: "original-1", reason: "Same behavior with a changed constraint" }],
		},
	];
	await setup(page, { candidates });
	await stage(page);
	await expect(page.getByRole("button", { name: "Apply reviewed changes" })).toBeDisabled();
	await openIncoming(page);
	await page.getByLabel("Decision for incoming-1").selectOption("original-1");
	await expect(page.getByText("0 added · 1 updated · 0 retired · 8 active requirements", { exact: true })).toBeVisible();
	await page.getByRole("button", { name: "Apply reviewed changes" }).click();
	await expect(workbench(page).getByRole("row")).toHaveCount(9);
	await openRequirement(page, "REQ-001");
	await expect(page.getByLabel("Review status for REQ-001", { exact: true })).toHaveValue("Needs Review");
});

test("retirement is confined to selected scope and requires confirming the list", async ({ page }) => {
	const state = await setup(page);
	await stage(page);
	await page.getByText("Replacement scope (0 existing requirements)", { exact: true }).click();
	const scope = page.locator(".import-scope");
	await scope.getByRole("checkbox").nth(0).check();
	await scope.getByRole("checkbox").nth(1).check();
	await expect(page.getByRole("heading", { name: "Retiring (2)" })).toBeVisible();
	await expect(page.getByRole("button", { name: "Apply reviewed changes" })).toBeDisabled();
	await page.getByRole("checkbox", { name: /Retire these 2 requirements/ }).check();
	await page.getByRole("button", { name: "Apply reviewed changes" }).click();
	await expect(page.getByText("Retired requirements (2)", { exact: true })).toBeVisible();
	await openRequirement(page, "REQ-003");
	await expect(page.getByLabel("Review status for REQ-003", { exact: true })).toHaveValue("Approved");
	expect(state.applies[0].update_scope).toEqual(["original-1", "original-2"]);
});

test("cancel preserves baseline and removes pending import", async ({ page }) => {
	const state = await setup(page);
	await stage(page);
	await page.getByRole("button", { name: "Cancel import" }).click();
	await expect(page.getByRole("heading", { name: "Compare incoming requirements" })).not.toBeVisible();
	await expect(workbench(page).getByRole("row")).toHaveCount(9);
	await expect(page.getByRole("button", { name: "Import requirements", exact: true })).toBeFocused();
	expect(state.applies).toHaveLength(0);
	await page.reload();
	await expect(page.getByRole("button", { name: /^Review import:/ })).toHaveCount(0);
});

test("failed apply keeps comparison and reuses retry identity", async ({ page }) => {
	const state = await setup(page, { failedApply: true });
	await stage(page);
	await openIncoming(page);
	await page.getByLabel("Decision for incoming-1").selectOption("skip");
	await page.getByRole("button", { name: "Apply reviewed changes" }).click();
	await expect(page.getByRole("alert")).toContainText("Temporary apply failure");
	await expect(page.getByLabel("Decision for incoming-1")).toHaveValue("skip");
	expect(state.getProject().current_snapshots.requirements.payload.requirements).toHaveLength(8);
	await page.getByRole("button", { name: "Apply reviewed changes" }).click();
	await expect(workbench(page).getByRole("row")).toHaveCount(10);
	expect(state.applies[0].idempotency_key).toEqual(state.applies[1].idempotency_key);
});

test("stale saved previews cannot apply", { tag: "@p1" }, async ({ page }) => {
	await setup(page, { revision: 8, pendingOnLoad: true });
	await page.getByRole("button", { name: "Review import: TX-6" }).click();
	await expect(page.getByRole("alert")).toContainText("Project changed");
	await expect(page.getByRole("button", { name: "Apply reviewed changes" })).toBeDisabled();
});

test("renewed comparison refreshes the baseline revision after a conflict", async ({ page }) => {
	const state = await setup(page);
	await stage(page);
	await page.route(`**/projects/${ID}/requirement-imports/import-1/compare`, (route) => {
		state.getProject().current_revision = 8;
		return route.fulfill({ json: { ...state.preview, import_id: "import-2", base_project_revision: 8 } });
	});
	await page.getByRole("button", { name: "Compare again" }).click();
	await expect(page.getByText("TX-6 · Revision 8", { exact: true })).toBeVisible();
	await expect(page.getByRole("button", { name: "Apply reviewed changes" })).toBeEnabled();
	await expect(page.getByText("Project changed after this comparison. Compare again before applying.", { exact: true })).toHaveCount(0);
});

test("comparison works on narrow screens and passes focused accessibility checks", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await setup(page, {
		candidates: [
			incoming[0],
			{
				...incoming[1],
				classification: "needs_decision",
				suggestions: [{ requirement_uid: "original-2", reason: "Verified source reference", verified_original_ids: ["TASK-20"] }],
			},
		],
	});
	await stage(page);
	const comparison = page.getByRole("region", { name: "Compare incoming requirements" });
	const filter = page.getByLabel("Filter import changes");
	await filter.selectOption("new");
	await expect(comparison.getByRole("row")).toHaveCount(2);
	await filter.selectOption("needs_decision");
	await openIncoming(page, incoming[1].requirement.text);
	await expect(page.getByText("Verified original IDs: TASK-20", { exact: true })).toBeVisible();
	const box = await comparison.boundingBox();
	expect(box.width).toBeLessThanOrEqual(390);
	const results = await new AxeBuilder({ page }).include(".requirement-import-review").analyze();
	expect(results.violations).toEqual([]);
});

test("four Low Stock changes and Apply fit on a laptop", { tag: "@p1" }, async ({ page }) => {
	await page.setViewportSize({ width: 1366, height: 768 });
	const texts = [
		"The system shall display a Low stock badge on the inventory dashboard for any item whose available quantity is strictly below the reorder threshold of 10 units.",
		"The system shall not display a Low stock badge on the inventory dashboard when an item's available quantity is equal to 10 units.",
		"The system shall filter the inventory dashboard to show only low stock items and hide items with available quantity at or above the threshold when the user selects the Low stock filter.",
		"Clearing the Low stock filter shall restore all inventory items.",
	];
	await setup(page, {
		emptyBaseline: true,
		sourceName: "pasted-requirements.md",
		candidates: texts.map((text, index) => ({
			...incoming[0],
			candidate_id: `stock-${index + 1}`,
			requirement: { id: `STOCK-${index + 1}`, text },
		})),
	});
	await expect(page.getByRole("button", { name: "Hide import setup" })).toHaveAttribute("aria-expanded", "true");
	await stage(page);
	const rows = page.locator(".import-candidate-row");
	await expect(rows).toHaveCount(4);
	await expect.poll(async () => (await rows.first().boundingBox()).y).toBeLessThanOrEqual(240);
	for (const row of await rows.all()) await expect(row).toBeInViewport({ ratio: 1 });
	const apply = page.getByRole("button", { name: "Apply reviewed changes" });
	await expect(apply).toBeInViewport({ ratio: 1 });
	await expect(apply).toBeEnabled();
	await page.getByRole("button", { name: "Cancel import" }).click();
	await expect(page.getByRole("button", { name: "Hide import setup" })).toBeFocused();
	await expect(page.getByRole("heading", { name: "Upload Requirements" })).toBeVisible();
});

test("a server-detected stale import requires a new comparison", async ({ page }) => {
	const state = await setup(page);
	await stage(page);
	let attempts = 0;
	await page.route(`**/projects/${ID}/requirement-imports/import-1/apply`, (route) => {
		attempts++;
		return route.fulfill({
			status: 409,
			json: { detail: { code: "stale_import", message: "Project changed. Compare the import again before applying." } },
		});
	});
	const apply = page.getByRole("button", { name: "Apply reviewed changes" });
	await apply.click();
	await expect(page.getByRole("alert")).toContainText("Project changed");
	await expect(apply).toBeDisabled();
	expect(attempts).toBe(1);
	await page.route(`**/projects/${ID}/requirement-imports/import-1/compare`, (route) => {
		state.getProject().current_revision = 8;
		return route.fulfill({ json: { ...state.preview, import_id: "import-2", base_project_revision: 8 } });
	});
	await page.getByRole("button", { name: "Compare again" }).click();
	await expect(apply).toBeEnabled();
});

test("sticky import controls leave the keyboard-focused candidate visible", async ({ page }) => {
	await page.setViewportSize({ width: 1366, height: 768 });
	const candidates = Array.from({ length: 24 }, (_, index) => ({
		...incoming[0],
		candidate_id: `incoming-${index + 1}`,
		requirement: { id: `STOCK-${index + 1}`, text: `Update the inventory threshold for location ${index + 1}.` },
	}));
	await setup(page, { candidates });
	await stage(page);
	const apply = page.getByRole("button", { name: "Apply reviewed changes" });
	await expect(apply).toBeInViewport({ ratio: 1 });
	const candidatesTable = page.getByRole("region", { name: "Incoming requirement changes" });
	await candidatesTable.focus();
	for (let index = 0; index < 17; index++) await page.keyboard.press("Tab");
	const target = candidatesTable.getByRole("button").nth(16);
	await expect(target).toBeFocused();
	await expect(apply).toBeInViewport({ ratio: 1 });
	const actions = page.locator(".import-apply-bar");
	await expect
		.poll(async () => {
			const [row, bar] = await Promise.all([target.boundingBox(), actions.boundingBox()]);
			return row.y + row.height <= bar.y;
		})
		.toBe(true);
});
