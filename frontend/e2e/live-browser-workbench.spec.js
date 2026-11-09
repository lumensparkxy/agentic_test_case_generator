import fs from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { applyImport, approveRequirements, approveScenarios, importSource, liveFixture, uiResponse } from "./support/live-workflow.js";

const source = {
	name: "employee-directory.md",
	mimeType: "text/markdown",
	buffer: Buffer.from(`# Synthetic employee directory

## DIR-100 — Find the seeded employee
When a guest enters "Alex Rivera" in the "Employee name" field and presses "Search", show the exact text "Alex Rivera".

## DIR-200 — Report no matching employees
When a guest enters "NoMatch Person" in the "Employee name" field and presses "Search", show the exact text "No employees found".
`),
};
const notes =
	'This is a public synthetic directory. The observed heading "Employee directory", field "Employee name", and button "Search" are exact accessible names. ' +
	'Only "Alex Rivera" is seeded. "NoMatch Person" is absent. No sign-in, additional records, or setup is required.';
const originalEmptyCopy = "No employees found";

function directoryRequirement(project, originalId) {
	const rows = project.current_snapshots.requirements.payload.requirements.filter((row) =>
		row.sources?.some((entry) => entry.excerpt_verified && entry.original_requirement_ids?.includes(originalId))
	);
	expect(rows, `Verified Directory source ${originalId}`).toHaveLength(1);
	return rows[0];
}

async function repairDirectorySource(page, fixture, suite, { target, faultMarker, revisedCopy }) {
	const current = await fixture.read();
	const previous = directoryRequirement(current, "DIR-200");
	const unrelated = directoryRequirement(current, "DIR-100");
	const oldCase = suite.payload.test_cases.find((row) => row.linked_requirement_ids.includes(previous.id));
	const keptCase = suite.payload.test_cases.find((row) => row.linked_requirement_ids.includes(unrelated.id));
	expect(oldCase?.id).toBeTruthy();
	expect(keptCase?.id).toBeTruthy();
	expect(oldCase.id).not.toBe(keptCase.id);
	const preview = await importSource(page, fixture, {
		...source,
		buffer: Buffer.from(source.buffer.toString("utf8").replace(originalEmptyCopy, revisedCopy)),
	});
	await fixture.save("changed-source-preview", preview);
	const findIncoming = (sourceId) => {
		const candidates = preview.candidates.filter((item) =>
			item.requirement.sources?.some((entry) => entry.excerpt_verified && entry.original_requirement_ids?.includes(sourceId))
		);
		expect(candidates, `Verified incoming source ${sourceId}`).toHaveLength(1);
		return candidates[0];
	};
	const changedCandidate = findIncoming("DIR-200");
	const unchangedCandidate = findIncoming("DIR-100");
	expect(preview.candidates).toHaveLength(2);
	expect(changedCandidate.requirement.text).toContain(revisedCopy);
	expect(changedCandidate).toMatchObject({ classification: "needs_decision", target_requirement_uid: null });
	expect(changedCandidate.suggestions[0]).toMatchObject({ requirement_uid: previous.requirement_uid, verified_original_ids: ["DIR-200"] });
	const openDecision = async (candidate) => {
		await page.getByRole("button", { name: `Review incoming requirement: ${candidate.requirement.text}`, exact: true }).click();
		return page.getByLabel(`Decision for ${candidate.candidate_id}`, { exact: true });
	};
	await (await openDecision(unchangedCandidate)).selectOption("skip");
	const decision = await openDecision(changedCandidate);
	await expect(decision).toHaveValue("");
	await expect(page.getByRole("heading", { name: `Suggested current requirement · ${previous.id}`, exact: true })).toBeVisible();
	await expect(page.getByText("Verified original IDs: DIR-200", { exact: true })).toBeVisible();
	await expect(decision.locator('optgroup[label="Verified source matches"] option')).toHaveAttribute("value", previous.requirement_uid);
	await decision.selectOption(previous.requirement_uid);
	await expect(page.getByText("0 added · 1 updated · 0 retired · 2 active requirements", { exact: true })).toBeVisible();
	const importResponse = await uiResponse(page, `/requirement-imports/${preview.import_id}/apply`, () =>
		page.getByRole("button", { name: "Apply reviewed changes", exact: true }).click()
	);
	expect(importResponse.request().postDataJSON().decisions).toEqual(
		expect.arrayContaining([
			expect.objectContaining({
				candidate_id: changedCandidate.candidate_id,
				action: "update",
				target_requirement_uid: previous.requirement_uid,
			}),
			expect.objectContaining({ candidate_id: unchangedCandidate.candidate_id, action: "skip" }),
		])
	);
	const imported = await importResponse.json();
	const revised = directoryRequirement(imported, "DIR-200");
	expect(revised).toMatchObject({
		id: previous.id,
		requirement_uid: previous.requirement_uid,
		content_version: previous.content_version + 1,
	});
	expect(directoryRequirement(imported, "DIR-100").content_version).toBe(unrelated.content_version);
	expect(imported.stage_state.test_cases).toMatchObject({ current_snapshot_id: suite.snapshot_id, stale: true });
	await approveRequirements(page, fixture);
	await fs.writeFile(faultMarker, "revised-empty\n");
	await page.goto(`${fixture.prefix}/context`);
	await page.getByPlaceholder("https://your-app").fill(target);
	await page.getByRole("textbox", { name: "Context notes", exact: true }).fill(notes);
	await uiResponse(page, "/requirements/enrich", () => page.getByRole("button", { name: "Analyze Context", exact: true }).click());
	await page.goto(`${fixture.prefix}/use-cases`);
	await uiResponse(page, "/use-cases/generate", () => page.getByRole("button", { name: "Regenerate Use Cases", exact: true }).click(), {
		timeout: 240_000,
	});
	const reviewed = await approveScenarios(page, fixture);
	const analyzed = await fixture.checked("POST", `${fixture.prefix}/impact-analysis`, { base_project_revision: reviewed.current_revision });
	const impact = analyzed.current_snapshots.impact_analysis.payload;
	await fixture.save("changed-source-impact", impact);
	expect(impact.changed_items).toEqual(
		expect.arrayContaining([expect.objectContaining({ kind: "requirement", change_type: "modified", requirement_id: previous.id })])
	);
	const selected = impact.recommendations.find((item) => item.action === "update" && item.test_case_id === oldCase.id);
	expect(selected?.recommendation_id, "The changed source requires a targeted update for the existing case").toBeTruthy();
	await page.goto(`${fixture.prefix}/test-cases`);
	for (const item of impact.recommendations) {
		await page.getByTestId(`impact-accept-${item.recommendation_id}`).setChecked(item === selected);
	}
	const applied = await uiResponse(
		page,
		"/impact-update/apply",
		() => page.getByRole("button", { name: "Apply 1 recommendation", exact: true }).click(),
		{ timeout: 360_000 }
	);
	expect(applied.request().postDataJSON().accepted_recommendation_ids).toEqual([selected.recommendation_id]);
	const updated = await applied.json();
	const draft = updated.current_snapshots.test_cases;
	await fixture.save("changed-source-repaired-suite", draft);
	expect(draft.payload.test_cases).toHaveLength(suite.payload.test_cases.length);
	expect(draft.payload.test_cases.find((row) => row.id === keptCase.id)).toEqual(keptCase);
	const repaired = draft.payload.test_cases.find((row) => row.id === oldCase.id);
	expect(repaired).toMatchObject({
		artifact_item_id: oldCase.artifact_item_id,
		artifact_version_number: oldCase.artifact_version_number + 1,
	});
	expect(repaired.artifact_version_id).not.toBe(oldCase.artifact_version_id);
	expect(repaired.steps.some((step) => step.expected.includes(revisedCopy))).toBe(true);
	expect(repaired.steps.every((step) => !step.expected.includes(originalEmptyCopy))).toBe(true);
	expect(draft.metadata.targeted_generation_runs).toHaveLength(1);
	return { updated, repaired, keptCase };
}

test.describe("Isolated browser workbench", () => {
	test.describe.configure({ retries: 0, timeout: 12 * 60 * 1000 });
	test.skip(process.env.E2E_LOCAL_WORKBENCH !== "1", "Run via scripts/e2e_workbench_local.py with its isolated Firestore emulator.");

	test("an approved generated suite retains healthy and broken browser run evidence after reload", async ({ page }, testInfo) => {
		page.setDefaultTimeout(30_000);
		expect(process.env.E2E_DEMO_PROJECT).toBe("demo-tcg-workbench");
		const target = process.env.E2E_DIRECTORY_TARGET;
		const faultMarker = process.env.E2E_DIRECTORY_FAULT;
		const revisedCopy = process.env.E2E_DIRECTORY_REVISED_EMPTY;
		expect(new URL(target).hostname).toBe("127.0.0.1");
		expect(faultMarker).toBeTruthy();
		const fixture = await liveFixture(page, testInfo, { namePrefix: "Synthetic directory execution" });
		try {
			await test.step("Import and review the real source and context", async () => {
				const parsed = await importSource(page, fixture, source);
				const project = await applyImport(page, fixture, parsed);
				const requirements = project.current_snapshots.requirements.payload.requirements;
				const sourceIds = requirements.flatMap((item) =>
					(item.sources || []).flatMap((evidence) => evidence.original_requirement_ids || [])
				);
				expect(sourceIds).toEqual(expect.arrayContaining(["DIR-100", "DIR-200"]));
				expect(requirements.every((item) => item.sources.some((evidence) => evidence.excerpt_verified))).toBe(true);
				await approveRequirements(page, fixture);
				await page.goto(`${fixture.prefix}/context`);
				await page.getByPlaceholder("https://your-app").fill(target);
				await page.getByRole("textbox", { name: "Context notes", exact: true }).fill(notes);
				await uiResponse(page, "/requirements/enrich", () => page.getByRole("button", { name: "Analyze Context", exact: true }).click());
				const saved = (await fixture.read()).current_snapshots.context.payload;
				expect(saved).toMatchObject({ app_link: target, notes });
			});

			await test.step("Generate Use Cases and record the synthetic review decision", async () => {
				await page.goto(`${fixture.prefix}/use-cases`);
				await uiResponse(page, "/use-cases/generate", () => page.getByRole("button", { name: "Generate Use Cases", exact: true }).click(), {
					timeout: 240_000,
				});
				const reviewed = await approveScenarios(page, fixture);
				const state = reviewed.stage_state.use_cases;
				expect(state.metadata.latest_human_review).toMatchObject({ decision: "approve", snapshot_id: state.current_snapshot_id });
			});

			const suite = await test.step("Generate the exact saved executable suite", async () => {
				await page.goto(`${fixture.prefix}/test-cases`);
				const generated = await (
					await uiResponse(page, "/testcases/generate", () => page.getByRole("button", { name: "Start generation", exact: true }).click(), {
						timeout: 360_000,
					})
				).json();
				await fixture.save("generated-suite", generated);
				expect(generated.generation_tasks).toEqual([]);
				expect(generated.approved, JSON.stringify(generated.review)).toBe(true);
				const saved = await fixture.read();
				const snapshot = saved.current_snapshots.test_cases;
				expect(snapshot.payload.test_cases.map((item) => item.id)).toEqual(generated.test_cases.map((item) => item.id));
				expect(snapshot.metadata.source_use_case_snapshot_id).toBe(saved.stage_state.use_cases.current_snapshot_id);
				return snapshot;
			});

			const runTarget = async (
				environment,
				expectedStatus,
				{ verifyAdmission = false, loseResponse = false, sourceSuite = suite, emptyCopy = originalEmptyCopy } = {}
			) => {
				await page.goto(`${fixture.prefix}/automation`);
				await page.getByPlaceholder("staging, dev, customer-a").fill(environment);
				await page.getByRole("textbox", { name: "Target base URL", exact: true }).fill(target);
				const previewResponse = await uiResponse(page, "/automation/execution/preview", () =>
					page.getByRole("button", { name: "Preview Execution", exact: true }).click()
				);
				const preview = await previewResponse.json();
				expect(preview.readiness).toMatchObject({
					run_allowed: true,
					source_snapshot_id: sourceSuite.snapshot_id,
					target_base_url: target,
				});
				const candidates = preview.executable.map((item) => item.id);
				const caseIds = sourceSuite.payload.test_cases.map((item) => item.id);
				expect(preview.executable.every((item) => caseIds.includes(item.source_test_case_id))).toBe(true);
				for (const outcome of ['exact text "Alex Rivera" should be visible', `exact text "${emptyCopy}" should be visible`]) {
					expect(
						preview.executable.some((item) => item.spec.steps.some((step) => step.endsWith(outcome))),
						outcome
					).toBe(true);
				}
				if (verifyAdmission) {
					const input = previewResponse.request().postDataJSON();
					const altered = input.test_cases.map((item, index) => (index === 0 ? { ...item, title: "Altered unsaved case" } : item));
					const denied = await fixture.api("POST", "/automation/execution/run", {
						...input,
						base_project_revision: preview.readiness.project_revision,
						selected_test_case_ids: candidates,
						test_cases: altered,
					});
					expect(denied.status()).toBe(409);
					expect(JSON.stringify(await denied.json())).toContain("case_snapshot_mismatch");
					expect((await fixture.read()).execution_runs).toEqual([]);
				}
				const button = page.getByRole("button", { name: new RegExp(`^Run ${candidates.length} Candidate(s)?$`) });
				let run;
				let runInput;
				if (loseResponse) {
					const match = "**/automation/execution/run";
					let requestId;
					const dropResponse = async (route) => {
						if (route.request().method() !== "POST") return route.fallback();
						const response = await route.fetch({ timeout: 120_000 });
						run = await response.json();
						runInput = route.request().postDataJSON();
						requestId = route.request().headers()["x-request-id"];
						await route.abort("failed");
					};
					await page.route(match, dropResponse);
					await button.click();
					await expect(page.getByRole("button", { name: "Check saved run" })).toBeVisible({ timeout: 120_000 });
					await page.unroute(match, dropResponse);
					expect(requestId).toBeTruthy();
					await page.reload();
					await page.getByRole("button", { name: "Check saved run" }).click();
					const receipt = await fixture.checked("GET", `${fixture.prefix}/execution-requests/${requestId}`);
					expect(receipt).toMatchObject({ status: "completed", run_id: run.run_id, source_snapshot_id: sourceSuite.snapshot_id });
					await expect(page.getByRole("link", { name: `Open saved run ${run.run_id}` })).toHaveAttribute(
						"href",
						`${fixture.prefix}/reports?run=${receipt.run_record_id}`
					);
					const duplicate = await fixture.api("POST", "/automation/execution/run", runInput, requestId);
					expect(duplicate.status()).toBe(409);
					expect((await duplicate.json()).detail).toMatchObject({ code: "execution_request_exists", receipt });
					expect((await fixture.read()).execution_runs.map((item) => item.run_id)).toEqual([run.run_id]);
					await fixture.save("recovered-execution-receipt", receipt);
				} else {
					const response = await uiResponse(page, "/automation/execution/run", () => button.click());
					run = await response.json();
					runInput = response.request().postDataJSON();
				}
				expect(runInput).toMatchObject({
					target_base_url: target,
					target_environment: environment,
					selected_test_case_ids: candidates,
					base_project_revision: preview.readiness.project_revision,
				});
				expect(run).toMatchObject({ status: expectedStatus, summary: { invalid: 0, skipped: 0 } });
				expect(run.results.map((item) => item.id)).toEqual(candidates);
				await fixture.save(`run-${environment}`, run);
				return run;
			};

			const healthy = await test.step("Run the current approved suite on healthy data", () =>
				runTarget("synthetic-healthy", "passed", { verifyAdmission: true, loseResponse: true }));
			const broken = await test.step("Run the same saved suite with a broken empty result", async () => {
				await fs.writeFile(faultMarker, "wrong-empty\n");
				const run = await runTarget("synthetic-wrong-empty", "failed");
				expect(run.summary.passed).toBeGreaterThan(0);
				expect(
					run.results.some((item) => item.status === "failed" && item.issues.some((issue) => issue.message.includes("No employees found")))
				).toBe(true);
				expect(run.run_id).not.toBe(healthy.run_id);
				return run;
			});

			await test.step("Inspect persisted report and approved export after a reload", async () => {
				await page.goto(`${fixture.prefix}/reports`);
				await page.reload();
				await expect(page.getByRole("heading", { name: "Export Test Cases" })).toBeVisible();
				await expect(page.getByText("Approved for export", { exact: true })).toBeVisible();
				await expect(page.getByText(`Run ${broken.run_id}`, { exact: true })).toBeVisible();
				await expect(page.getByText(`Failed ${broken.summary.failed}`, { exact: true })).toBeVisible();
				await expect(page.getByText(`synthetic-wrong-empty • ${target}`, { exact: true })).toBeVisible();
				expect(broken.artifacts_root).toBeTruthy();
				await expect(page.getByText(broken.artifacts_root, { exact: true })).toBeVisible();
				const persisted = await fixture.read();
				const history = persisted.execution_runs;
				expect(history.map((item) => item.run_id)).toEqual([broken.run_id, healthy.run_id]);
				expect(history).toMatchObject([
					{ status: "failed", target_base_url: target, source_snapshot_id: suite.snapshot_id, target_environment: "synthetic-wrong-empty" },
					{ status: "passed", target_base_url: target, source_snapshot_id: suite.snapshot_id, target_environment: "synthetic-healthy" },
				]);
				expect(persisted.current_snapshots.reports).toMatchObject({
					source_snapshot_id: history[0].snapshot_id,
					payload: { run_id: broken.run_id, status: "failed" },
				});
				await page.screenshot({ path: testInfo.outputPath("report-after-reload.png"), fullPage: true });
				const [download] = await Promise.all([
					page.waitForEvent("download"),
					page.getByRole("button", { name: /JSON API\/Import ready/i }).click(),
				]);
				const path = testInfo.outputPath("inspected-export.json");
				await download.saveAs(path);
				const exported = JSON.parse(await fs.readFile(path, "utf8"));
				expect(exported.test_cases.map((item) => item.id)).toEqual(suite.payload.test_cases.map((item) => item.id));
				expect(exported.audit_metadata).toMatchObject({
					snapshot_id: suite.snapshot_id,
					human_review_status: "unavailable",
					machine_review_status: "approved",
					is_draft: false,
					execution_status: "failed",
					execution_run_ids: [broken.run_id, healthy.run_id],
					source_snapshot_ids: {
						use_cases: persisted.stage_state.use_cases.metadata.latest_human_review.snapshot_id,
						test_cases: suite.snapshot_id,
						execution: history[0].snapshot_id,
					},
				});
				await fixture.save("persisted-run-and-report-evidence", {
					history,
					report: persisted.current_snapshots.reports,
					export: exported.audit_metadata,
				});
			});

			await test.step("Open each exact saved run and read its case outcomes", async () => {
				const { execution_runs: history } = await fixture.read();
				for (const expected of [healthy, broken]) {
					const record = history.find((item) => item.run_id === expected.run_id);
					const savedRun = await fixture.checked("GET", `${fixture.prefix}/execution-runs/${record.run_record_id}`);
					expect(savedRun).toMatchObject({
						run_id: expected.run_id,
						source_snapshot_id: suite.snapshot_id,
						results: expected.results.map(({ id, source_test_case_id, status }) => ({ id, source_test_case_id, status })),
					});
					await page.goto("/runs");
					await page.getByRole("link", { name: `Open evidence for run ${expected.run_id}` }).click();
					await expect(page).toHaveURL(new RegExp(`\\?run=${record.run_record_id}$`));
					await page.reload();
					const report = page.locator(".playwright-report-card");
					await expect(report.getByText(`Run ${expected.run_id}`, { exact: true })).toBeVisible();
					await expect(report.getByText(suite.snapshot_id, { exact: true })).toBeVisible();
					await report.getByText(`Case outcomes (${expected.results.length})`, { exact: true }).click();
					for (const result of expected.results) {
						await expect(report.getByText(result.source_test_case_id, { exact: true })).toBeVisible();
					}
					if (expected === broken) {
						const assertion = report.locator("pre").filter({ hasText: "No employees found" });
						await expect(assertion).toBeVisible();
						expect(await assertion.textContent()).not.toContain(String.fromCharCode(27));
						await page.screenshot({ path: testInfo.outputPath("saved-failure-details.png"), fullPage: true });
					}
				}
			});

			const sourceChange = revisedCopy
				? await test.step("Reimport and repair the changed empty-result requirement", () =>
						repairDirectorySource(page, fixture, suite, { target, faultMarker, revisedCopy }))
				: null;
			const reviewedSuite = await test.step("Review the exact saved suite after applying impact recommendations", async () => {
				const current = await fixture.read();
				let updated = sourceChange?.updated;
				if (!updated) {
					const analyzed = await fixture.checked("POST", `${fixture.prefix}/impact-analysis`, {
						base_project_revision: current.current_revision,
					});
					const impact = analyzed.current_snapshots.impact_analysis;
					const keep = impact.payload.recommendations.filter((item) => item.action === "keep");
					expect(keep.length).toBeGreaterThan(0);
					updated = await fixture.checked("POST", `${fixture.prefix}/impact-update/apply`, {
						analysis_snapshot_id: impact.snapshot_id,
						base_project_revision: analyzed.current_revision,
						accepted_recommendation_ids: keep.map((item) => item.recommendation_id),
					});
					expect(updated.current_snapshots.test_cases.payload.test_cases).toEqual(suite.payload.test_cases);
				}
				const draft = updated.current_snapshots.test_cases;
				expect(draft).toMatchObject({ approved: false, payload: { substantive_assessment: { status: "unknown" } } });
				const previewSaved = (project) =>
					fixture.checked("POST", "/automation/execution/preview", {
						project_id: project.project_id,
						base_project_revision: project.current_revision,
						test_cases: project.current_snapshots.test_cases.payload.test_cases,
						target_base_url: target,
						target_environment: "synthetic-post-change-review",
					});
				const before = await previewSaved(updated);
				expect(before.readiness).toMatchObject({ run_allowed: false, source_snapshot_id: draft.snapshot_id });
				expect(before.readiness.blockers.map((item) => item.code)).toContain("unapproved_test_cases");
				const pending = await fixture.read();
				await page.goto(`${fixture.prefix}/test-cases`);
				await expect(page.getByText("Export blocked", { exact: true })).toBeVisible();
				const response = await uiResponse(
					page,
					"/test-cases/review",
					() => page.getByRole("button", { name: "Review saved suite", exact: true }).click(),
					{ timeout: 210_000 }
				);
				const reviewed = await response.json();
				const saved = reviewed.current_snapshots.test_cases;
				await fixture.save("saved-suite-review", { draft, reviewed: saved, before: before.readiness });
				expect(saved.payload.test_cases).toEqual(draft.payload.test_cases);
				expect(saved).toMatchObject({ source_snapshot_id: draft.snapshot_id, approved: true, operation: "test_cases.review" });
				expect(reviewed.execution_runs).toEqual(current.execution_runs);
				const replay = await fixture.checked("POST", `${fixture.prefix}/test-cases/review`, {
					snapshot_id: draft.snapshot_id,
					base_project_revision: pending.current_revision,
				});
				expect(replay.current_snapshots.test_cases.snapshot_id).toBe(saved.snapshot_id);
				await page.reload();
				await expect(page.getByRole("button", { name: "Review saved suite", exact: true })).toHaveCount(0);
				await page.goto(`${fixture.prefix}/reports`);
				await expect(page.getByText("Approved for export", { exact: true })).toBeVisible();
				const after = await previewSaved(reviewed);
				await fixture.save("saved-suite-execution-admission", { before: before.readiness, after: after.readiness });
				expect(after.readiness).toMatchObject({ run_allowed: true, source_snapshot_id: saved.snapshot_id, blockers: [] });
				return saved;
			});

			if (sourceChange) {
				await test.step("Run the repaired suite against both versions of the Directory", async () => {
					const options = { sourceSuite: reviewedSuite, emptyCopy: revisedCopy };
					const revised = await runTarget("synthetic-revised-empty", "passed", options);
					expect(revised.results).toHaveLength(reviewedSuite.payload.test_cases.length);
					await fs.rm(faultMarker, { force: true });
					const old = await runTarget("synthetic-old-empty-copy", "failed", options);
					expect(old.results.find((row) => row.source_test_case_id === sourceChange.keptCase.id)?.status).toBe("passed");
					const failed = old.results.find((row) => row.source_test_case_id === sourceChange.repaired.id);
					expect(failed?.status).toBe("failed");
					expect(
						failed.issues.some(
							(item) => item.code === "playwright.error" && item.message.includes(revisedCopy) && item.message.includes("toBeVisible")
						)
					).toBe(true);
					const { execution_runs: history } = await fixture.read();
					expect(history.map((row) => [row.run_id, row.source_snapshot_id])).toEqual([
						[old.run_id, reviewedSuite.snapshot_id],
						[revised.run_id, reviewedSuite.snapshot_id],
						[broken.run_id, suite.snapshot_id],
						[healthy.run_id, suite.snapshot_id],
					]);
					await page.goto(`${fixture.prefix}/reports`);
					await page.reload();
					await expect(page.getByText("Approved for export", { exact: true })).toBeVisible();
					const [download] = await Promise.all([
						page.waitForEvent("download"),
						page.getByRole("button", { name: /JSON API\/Import ready/i }).click(),
					]);
					const exportPath = testInfo.outputPath("changed-source-export.json");
					await download.saveAs(exportPath);
					const exported = JSON.parse(await fs.readFile(exportPath, "utf8"));
					expect(exported.audit_metadata).toMatchObject({
						snapshot_id: reviewedSuite.snapshot_id,
						is_draft: false,
						execution_run_ids: [old.run_id, revised.run_id],
					});
					expect(exported.test_cases.find((row) => row.id === sourceChange.repaired.id).steps).toEqual(
						sourceChange.repaired.steps.map((step) => ({ ...step, test_data: step.test_data ?? null }))
					);
					await fixture.save("changed-source-run-evidence", { history, export: exported.audit_metadata });
				});
			}
		} finally {
			await fs.rm(faultMarker, { force: true });
			await fixture.close();
			const archived = await fixture.read();
			expect(archived).toMatchObject({ project_id: fixture.project.project_id, status: "archived" });
			await fixture.save("archive-readback", {
				project_id: archived.project_id,
				status: archived.status,
				revision: archived.current_revision,
			});
		}
	});
});
