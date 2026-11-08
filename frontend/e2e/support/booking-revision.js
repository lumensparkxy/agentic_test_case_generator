import fs from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { approveRequirements, approveScenarios, importSource, roomSource, uiResponse } from "./live-workflow.js";

const originalError = "Enter a whole number from 1 to 8";
const revisedError = "Enter a whole number from 1 to 6";
const revisedClauses = ["1 to 6 inclusive", "0, 7, or 1.5", "no greater than 6"];
const normalize = (text) => text.trim().replace(/\s+/g, " ");
const reviseSource = (text) =>
	text
		.replace("1 to 8 inclusive", "1 to 6 inclusive")
		.replace("0, 9, or 1.5", "0, 7, or 1.5")
		.replace("With a valid count and an available room", "With a valid count no greater than 6 and an available room");

const attendeeSources = (row) =>
	row.sources?.filter((source) => source.excerpt_verified && source.original_requirement_ids.includes("ROOM-101")) ?? [];

function attendeeMatches(current, preview) {
	const revisions = current.flatMap((row) =>
		attendeeSources(row)
			.filter((source) => reviseSource(source.excerpt) !== source.excerpt)
			.map((source) => ({ row, sourceId: source.source_id, revisedQuote: normalize(reviseSource(source.excerpt)) }))
	);
	const changed = new Set(revisions.map(({ row }) => row.requirement_uid));
	const choices = new Map();
	for (const candidate of preview.candidates) {
		const matches = revisions.filter(({ sourceId, revisedQuote }) =>
			attendeeSources(candidate.requirement).some((source) => {
				const quote = normalize(source.excerpt);
				return (
					source.source_id === sourceId &&
					revisedClauses.some((clause) => quote.includes(clause)) &&
					(revisedQuote.includes(quote) || quote.includes(revisedQuote))
				);
			})
		);
		const rows = new Set(matches.map(({ row }) => row));
		expect(rows.size, `A revised excerpt must identify a single existing requirement for ${candidate.candidate_id}`).toBeLessThanOrEqual(1);
		if (rows.size) choices.set(candidate.candidate_id, [...rows][0]);
	}
	expect([...choices.values()].map((row) => row.requirement_uid).sort()).toEqual([...changed].sort());
	const selectedQuotes = preview.candidates
		.filter((candidate) => choices.has(candidate.candidate_id))
		.flatMap((candidate) => attendeeSources(candidate.requirement).map((source) => normalize(source.excerpt)));
	for (const clause of revisedClauses) {
		expect(
			selectedQuotes.some((quote) => quote.includes(clause)),
			`The selected revised source must verify ${clause}`
		).toBe(true);
	}
	return choices;
}

async function importCapacityChange(page, fixture, suite) {
	const before = await fixture.read();
	const requirements = before.current_snapshots.requirements;
	const baseline = requirements.payload.requirements;
	const original = await fs.readFile(roomSource, "utf8");
	const revised = reviseSource(original);
	for (const clause of revisedClauses) expect(revised).toContain(clause);
	const source = { name: "room-booking-requirements.md", mimeType: "text/markdown", buffer: Buffer.from(revised) };
	const cancelled = await importSource(page, fixture, source);
	await fixture.save("booking-revision-cancelled-preview", cancelled);
	await uiResponse(
		page,
		`/requirement-imports/${cancelled.import_id}`,
		() => page.getByRole("button", { name: "Cancel import", exact: true }).click(),
		{
			status: 204,
		}
	);
	const afterCancel = await fixture.read();
	expect(afterCancel.current_revision).toBe(before.current_revision);
	expect(afterCancel.current_snapshots.requirements).toEqual(requirements);
	const preview = await importSource(page, fixture, source);
	await fixture.save("booking-revision-preview", preview);
	const choices = attendeeMatches(baseline, preview);
	for (const candidate of preview.candidates) {
		const target = choices.get(candidate.candidate_id);
		if (target) {
			expect(candidate.suggestions).toEqual(
				expect.arrayContaining([expect.objectContaining({ requirement_uid: target.requirement_uid, verified_original_ids: ["ROOM-101"] })])
			);
		}
		await page.getByRole("button", { name: `Review incoming requirement: ${candidate.requirement.text}`, exact: true }).click();
		await page.getByLabel(`Decision for ${candidate.candidate_id}`, { exact: true }).selectOption(target?.requirement_uid || "skip");
	}
	await expect(
		page.getByText(`0 added · ${choices.size} updated · 0 retired · ${baseline.length} active requirements`, { exact: true })
	).toBeVisible();
	const response = await uiResponse(page, `/requirement-imports/${preview.import_id}/apply`, () =>
		page.getByRole("button", { name: "Apply reviewed changes", exact: true }).click()
	);
	expect(response.request().postDataJSON().decisions).toEqual(
		preview.candidates.map(({ candidate_id }) => {
			const target = choices.get(candidate_id);
			return { candidate_id, action: target ? "update" : "skip", target_requirement_uid: target?.requirement_uid ?? null };
		})
	);
	const applied = await response.json();
	const current = applied.current_snapshots.requirements.payload.requirements;
	const affected = new Set([...choices.values()].map((row) => row.id));
	expect(current.map((row) => [row.id, row.requirement_uid])).toEqual(baseline.map((row) => [row.id, row.requirement_uid]));
	for (const old of baseline) {
		const row = current.find((item) => item.id === old.id);
		if (affected.has(old.id)) {
			expect(row).toMatchObject({ content_version: old.content_version + 1, review_status: "Needs Review" });
		} else {
			expect(row).toEqual(old);
		}
	}
	expect(applied.stage_state.test_cases).toMatchObject({ stale: true, current_snapshot_id: suite.snapshot_id });
	const staleExport = await fixture.api("POST", "/export/json", {
		project_id: applied.project_id,
		base_project_revision: applied.current_revision,
		source_snapshot_id: suite.snapshot_id,
		test_cases: suite.payload.test_cases,
	});
	expect(staleExport.status()).toBe(409);
	expect((await staleExport.json()).detail).toContain("stale");
	return affected;
}

async function repairBookingCases(page, fixture, { suite, affected, target, notes, prerequisite, revisionMarker }) {
	await approveRequirements(page, fixture);
	await fs.writeFile(revisionMarker, "6\n");
	const revisedNotes = notes.replace(originalError, revisedError);
	expect(revisedNotes).not.toBe(notes);
	await page.goto(`${fixture.prefix}/context`);
	await page.getByRole("textbox", { name: "Context notes", exact: true }).fill(revisedNotes);
	await uiResponse(page, "/requirements/enrich", () => page.getByRole("button", { name: "Analyze Context", exact: true }).click());
	expect((await fixture.read()).current_snapshots.context.payload).toMatchObject({
		app_link: target,
		notes: revisedNotes,
		execution_prerequisites: [prerequisite],
	});
	await page.goto(`${fixture.prefix}/use-cases`);
	await uiResponse(page, "/use-cases/generate", () => page.getByRole("button", { name: "Regenerate Use Cases", exact: true }).click(), {
		timeout: 300_000,
	});
	const planned = await approveScenarios(page, fixture);
	const analyzed = await fixture.checked("POST", `${fixture.prefix}/impact-analysis`, { base_project_revision: planned.current_revision });
	const impact = analyzed.current_snapshots.impact_analysis.payload;
	await fixture.save("booking-revision-impact", impact);
	const actualChanges = impact.changed_items.filter(({ kind }) => kind === "requirement");
	expect(actualChanges.map((row) => [row.requirement_id, row.change_type]).sort()).toEqual(
		[...affected].sort().map((id) => [id, "modified"])
	);
	const baselineCases = suite.payload.test_cases;
	const affectedCases = baselineCases.filter((row) => row.linked_requirement_ids.some((id) => affected.has(id)));
	const affectedIds = new Set(affectedCases.map((row) => row.id));
	expect(affectedIds.size).toBeGreaterThan(0);
	const selected = impact.recommendations.filter((item) => item.action === "update" && affectedIds.has(item.test_case_id));
	expect(new Set(selected.map((item) => item.test_case_id))).toEqual(affectedIds);
	await page.goto(`${fixture.prefix}/test-cases`);
	const selectedIds = selected.map((item) => item.recommendation_id).sort();
	for (const item of impact.recommendations) {
		await page
			.getByRole("checkbox", { name: `Accept ${item.title}`, exact: true })
			.setChecked(selectedIds.includes(item.recommendation_id));
	}
	const applied = await uiResponse(
		page,
		"/impact-update/apply",
		() =>
			page.getByRole("button", { name: `Apply ${selected.length} recommendation${selected.length === 1 ? "" : "s"}`, exact: true }).click(),
		{
			timeout: 600_000,
		}
	);
	expect(applied.request().postDataJSON().accepted_recommendation_ids.toSorted()).toEqual(selectedIds);
	const draft = (await applied.json()).current_snapshots.test_cases;
	await fixture.save("booking-revision-draft", draft);
	expect(draft.approved).toBe(false);
	expect(draft.metadata.targeted_generation_runs).toHaveLength(affectedIds.size);
	const repaired = draft.payload.test_cases;
	const oldIds = new Set(baselineCases.map((row) => row.id));
	for (const old of baselineCases) {
		const row = repaired.find((item) => item.id === old.id);
		if (affectedIds.has(old.id)) {
			expect(row).toMatchObject({ artifact_item_id: old.artifact_item_id, artifact_version_number: old.artifact_version_number + 1 });
			expect(row.artifact_version_id).not.toBe(old.artifact_version_id);
		} else {
			expect(row).toEqual(old);
		}
	}
	for (const added of repaired.filter((row) => !oldIds.has(row.id))) {
		expect(added.linked_requirement_ids.some((id) => affected.has(id))).toBe(true);
	}
	await expect(page.getByText("Export blocked", { exact: true })).toBeVisible();
	const reviewed = await uiResponse(
		page,
		"/test-cases/review",
		() => page.getByRole("button", { name: "Review saved suite", exact: true }).click(),
		{
			timeout: 300_000,
		}
	);
	const saved = (await reviewed.json()).current_snapshots.test_cases;
	await fixture.save("booking-revision-review", saved);
	expect(saved).toMatchObject({ approved: true, source_snapshot_id: draft.snapshot_id, operation: "test_cases.review" });
	expect(saved.payload.test_cases).toEqual(repaired);
	return { saved, affectedIds };
}

export async function previewBookingVersion(page, fixture, target, environment) {
	await page.goto(`${fixture.prefix}/automation`);
	await page.getByPlaceholder("staging, dev, customer-a").fill(environment);
	await page.getByRole("textbox", { name: "Target base URL", exact: true }).fill(target);
	const response = await uiResponse(page, "/automation/execution/preview", () =>
		page.getByRole("button", { name: "Preview Execution", exact: true }).click()
	);
	return { response, preview: await response.json() };
}

async function runBookingVersion(page, fixture, target, suite, environment) {
	const { preview } = await previewBookingVersion(page, fixture, target, environment);
	await fixture.save(`${environment}-preview`, preview);
	expect(preview.readiness).toMatchObject({ source_snapshot_id: suite.snapshot_id, run_allowed: true, blockers: [] });
	const ids = preview.executable.map((row) => row.id);
	expect(ids.length).toBeGreaterThan(0);
	const runResponse = await uiResponse(
		page,
		"/automation/execution/run",
		() => page.getByRole("button", { name: new RegExp(`^Run ${ids.length} Candidates?$`) }).click(),
		{
			timeout: 240_000,
		}
	);
	const run = await runResponse.json();
	await fixture.save(`${environment}-run`, run);
	expect(run.results.map((row) => row.id)).toEqual(ids);
	return { run, preview };
}

export async function verifyBookingRevision(page, fixture, testInfo, { suite, target, notes, prerequisite, revisionMarker, baselineRun }) {
	const affected = await test.step("Compare, cancel and explicitly apply the attendee revision", () =>
		importCapacityChange(page, fixture, suite));
	const { saved, affectedIds } = await test.step("Repair and review the exact affected saved cases", () =>
		repairBookingCases(page, fixture, { suite, affected, target, notes, prerequisite, revisionMarker }));
	await test.step("Compare generated boundary checks against both versions and inspect saved evidence", async () => {
		const revised = await runBookingVersion(page, fixture, target, saved, "booking-capacity-six");
		expect(revised.run).toMatchObject({ status: "passed", summary: { failed: 0, invalid: 0, skipped: 0 } });
		const atSix = revised.preview.executable.filter((row) => {
			const steps = row.spec.steps;
			return (
				steps.some((step) => step.endsWith('I enter "6" into field "Attendees"')) &&
				steps.some((step) => step.includes('td:nth-child(4)" text should equal "6"'))
			);
		});
		const atSeven = revised.preview.executable.filter((row) => {
			const steps = row.spec.steps;
			return steps.some((step) => step.endsWith('I enter "7" into field "Attendees"')) && steps.some((step) => step.includes(revisedError));
		});
		expect(atSix.length, "No generated check verifies the saved attendee count at 6").toBeGreaterThan(0);
		expect(atSeven.length, "No generated check rejects 7 at the revised capacity").toBeGreaterThan(0);
		await fs.rm(revisionMarker, { force: true });
		const original = await runBookingVersion(page, fixture, target, saved, "booking-capacity-eight");
		expect(original.run).toMatchObject({ status: "failed", summary: { invalid: 0, skipped: 0 } });
		const originalById = new Map(original.run.results.map((row) => [row.id, row]));
		expect(atSix.some(({ id }) => originalById.get(id)?.status === "passed")).toBe(true);
		expect(
			atSeven.some(({ id }) => {
				const result = originalById.get(id);
				return (
					result?.status === "failed" &&
					result.issues.some(
						(item) => item.code === "playwright.error" && item.message.includes(revisedError) && item.message.includes("toBeVisible")
					)
				);
			})
		).toBe(true);
		const baselinePassed = new Set(baselineRun.results.filter((row) => row.status === "passed").map((row) => row.source_test_case_id));
		const unaffected = [...baselinePassed].filter((id) => !affectedIds.has(id));
		expect(unaffected.length).toBeGreaterThan(0);
		for (const result of [revised.run, original.run]) {
			const passed = result.results.filter((row) => row.status === "passed").map((row) => row.source_test_case_id);
			expect(passed).toEqual(expect.arrayContaining(unaffected));
		}
		const { execution_runs: history } = await fixture.read();
		expect(history.map((row) => [row.run_id, row.source_snapshot_id])).toEqual([
			[original.run.run_id, saved.snapshot_id],
			[revised.run.run_id, saved.snapshot_id],
			[baselineRun.run_id, suite.snapshot_id],
		]);
		await page.goto(`${fixture.prefix}/reports`);
		await page.reload();
		await expect(page.getByText("Approved for export", { exact: true })).toBeVisible();
		const [download] = await Promise.all([
			page.waitForEvent("download"),
			page.getByRole("button", { name: /JSON API\/Import ready/i }).click(),
		]);
		const path = testInfo.outputPath("booking-revision-export.json");
		await download.saveAs(path);
		const exported = JSON.parse(await fs.readFile(path, "utf8"));
		expect(exported.audit_metadata).toMatchObject({
			snapshot_id: saved.snapshot_id,
			is_draft: false,
			execution_run_ids: [original.run.run_id, revised.run.run_id],
		});
		expect(exported.test_cases.map((row) => row.id)).toEqual(saved.payload.test_cases.map((row) => row.id));
		await fixture.save("booking-revision-evidence", {
			history,
			export: exported.audit_metadata,
			atSix: atSix.map((row) => row.id),
			atSeven: atSeven.map((row) => row.id),
		});
	});
}
