import { test, expect } from "@playwright/test";
import {
	liveFixture,
	importSource,
	applyImport,
	approveRequirements,
	approveScenarios,
	uiResponse,
	caseQuality,
} from "./support/live-workflow.js";

test.use({ trace: "off", video: "off", viewport: { width: 1440, height: 1000 } });

const source = `# Synthetic draft-order requirements
This is a controlled design fixture, not an instruction to operate a real service.

## QTY-101 — Quantity validation
On the Draft order form, Quantity must be a whole number from 1 to 8 inclusive. Pressing Validate with an empty value, 0, 9, or 1.5 displays "Enter a whole number from 1 to 8" beside Quantity. Pressing Validate with a valid quantity displays "Quantity accepted" beside Quantity.

## NOTE-202 — Clear note
On the Draft order form, pressing Clear note empties the Note text field. Clearing an already empty Note leaves it empty.
`;
const context =
	"Synthetic Draft order form. Available controls: Quantity text field, Validate button, Note text field and Clear note button. The Note field accepts ordinary text such as Team lunch. Reset both fields to empty before each test. Quantity validation and clearing Note are independent. No login, network operation, order persistence, payment, stock check or navigation is specified. Test design only; no application target is provisioned.";
const decision =
	'Quantity now accepts whole numbers from 1 to 6 inclusive; 7 and above are rejected. The error reads "Enter a whole number from 1 to 6". Clear note behavior is unchanged.';
const corrected = (text) => text.replace(/\b8\b/g, "6").replace(/\b9\b/g, "7");

test.describe("Live shared corrections", () => {
	test.describe.configure({ retries: 0, timeout: 20 * 60 * 1000 });
	test.skip(process.env.E2E_LIVE !== "1", "Requires the configured live Gemini provider and Firestore; no model mocks.");
	test("requirements, planners and generated tests share an accepted source correction", async ({ page }, testInfo) => {
		page.setDefaultTimeout(30_000);
		const fixture = await liveFixture(page, testInfo, { namePrefix: "Live Shared Corrections" });
		const errors = [];
		page.on("pageerror", (e) => errors.push(e.message));
		try {
			await fixture.save("controlled-source", { source, context, decision });
			const preview = await importSource(page, fixture, { name: "draft-order.md", mimeType: "text/markdown", buffer: Buffer.from(source) });
			await applyImport(page, fixture, preview);
			await approveRequirements(page, fixture);
			await page.goto(`${fixture.prefix}/context`);
			await page.getByRole("textbox", { name: "Context notes", exact: true }).fill(context);
			await uiResponse(page, "/requirements/enrich", () => page.getByRole("button", { name: "Analyze Context", exact: true }).click());
			await page.goto(`${fixture.prefix}/use-cases`);
			const planning = await uiResponse(
				page,
				"/use-cases/generate",
				() => page.getByRole("button", { name: "Generate Use Cases", exact: true }).click(),
				{ timeout: 300_000 }
			);
			await fixture.save("baseline-planning", await planning.json());
			await approveScenarios(page, fixture);
			await page.goto(`${fixture.prefix}/test-cases`);
			const generation = await uiResponse(
				page,
				"/testcases/generate",
				() => page.getByRole("button", { name: "Start generation", exact: true }).click(),
				{ timeout: 360_000 }
			);
			const generated = await generation.json();
			await fixture.save("baseline-generation", generated);
			expect(generated.generation_tasks).toEqual([]);
			expect(generated.workflow_diagnostics.used_fallback).toBe(false);
			expect(generated.substantive_assessment.status).toBe("assessed_complete");
			expect(generated.approved).toBe(true);
			expect(caseQuality(generated.test_cases).withRequirementLinks).toBe(generated.test_cases.length);
			const baseline = await fixture.read();
			const reqs = baseline.current_snapshots.requirements.payload.requirements;
			const affected = reqs.filter((r) => r.sources?.some((s) => s.original_requirement_ids?.includes("QTY-101")));
			expect(affected.length).toBeGreaterThan(0);
			expect(affected.some((r) => corrected(r.text) !== r.text)).toBe(true);
			const affectedIds = affected.map((r) => r.id);
			const preserved = baseline.current_snapshots.test_cases.payload.test_cases.filter(
				(c) => !c.linked_requirement_ids.some((id) => affectedIds.includes(id))
			);
			expect(preserved.length, "An independent case is needed to verify preservation").toBeGreaterThan(0);
			const finding = await fixture.checked("POST", `${fixture.prefix}/quality-findings`, {
				base_project_revision: baseline.current_revision,
				source_snapshot_id: baseline.current_snapshots.requirements.snapshot_id,
				category: "ambiguous_requirement",
				summary: "The quantity maximum has changed from eight to six. Confirm the revised rule and error text.",
				expected_behavior: decision,
				requirement_ids: affectedIds,
			});
			await page.goto(`${fixture.prefix}/context`);
			const panel = page.getByRole("region", { name: "Shared project corrections" });
			await panel.getByRole("combobox", { name: "Finding to resolve", exact: true }).selectOption(finding.finding.id);
			await panel.getByRole("textbox", { name: "Accepted business decision", exact: true }).fill(decision);
			for (const r of affected)
				await panel.getByRole("textbox", { name: `Corrected source · ${r.id}`, exact: true }).fill(corrected(r.text));
			const response = await uiResponse(
				page,
				"/changes/accept",
				() => panel.getByRole("button", { name: "Accept correction and refresh drafts", exact: true }).click(),
				{ timeout: 600_000 }
			);
			const synced = await response.json();
			await fixture.save("corrected-project", synced);
			expect(synced.project_sync.status, JSON.stringify(synced.project_sync)).toBe("ready_for_review");
			for (const stage of ["requirements", "context", "use_cases", "test_cases"]) {
				expect(synced.project_sync.stages[stage]).toBe("draft");
				expect(synced.stage_state[stage].approved).toBe(false);
			}
			for (const stage of ["use_cases", "test_cases"]) {
				const snap = synced.current_snapshots[stage];
				expect(snap.metadata.guidance.project_inputs).toEqual(synced.project_sync.input_manifest);
				expect(snap.metadata.guidance.project_inputs.decisions.at(-1).text).toBe(decision);
				expect(snap.metadata.guidance.behaviour_version).toBe(synced.project_sync.behaviour_version);
			}
			const cases = synced.current_snapshots.test_cases.payload.test_cases;
			const refreshed = synced.current_snapshots.test_cases.payload;
			expect(refreshed.requirement_analysis).toEqual(synced.current_snapshots.use_cases.payload.requirement_analysis);
			expect(refreshed.iteration_history).toEqual([]);
			expect(refreshed.generation_evidence.request_id).toBe(`${synced.project_sync.id}:test_cases:1`);
			for (const c of preserved) expect(cases.find((row) => row.id === c.id)).toEqual(c);
			for (const r of affected)
				expect(synced.current_snapshots.requirements.payload.requirements.find((row) => row.id === r.id).text).toBe(corrected(r.text));
			const changedCases = cases.filter((c) => c.linked_requirement_ids.some((id) => affectedIds.includes(id)));
			expect(changedCases.length).toBeGreaterThan(0);
			expect(JSON.stringify(changedCases)).toContain("1 to 6");
			expect(JSON.stringify(changedCases)).not.toContain("1 to 8");
			await fixture.save("changed-cases", changedCases);
			const stale = await fixture.api("POST", `${fixture.prefix}/changes/accept`, {
				...response.request().postDataJSON(),
				decision: "A stale correction must not replace the accepted rule.",
			});
			expect(stale.status()).toBe(409);
			const replay = await fixture.api(
				"POST",
				`${fixture.prefix}/changes/accept`,
				response.request().postDataJSON(),
				response.request().headers()["x-request-id"]
			);
			expect(replay.status()).toBe(200);
			expect((await replay.json()).current_revision).toBe(synced.current_revision);
			await page.reload();
			await expect(page.getByRole("region", { name: "Project update status" })).toContainText("Updated drafts are ready for review");
			expect((await fixture.read()).current_snapshots.test_cases.snapshot_id).toBe(synced.current_snapshots.test_cases.snapshot_id);
			await page.screenshot({ path: testInfo.outputPath("corrected-desktop.png"), fullPage: true });
			expect(errors).toEqual([]);
		} catch (error) {
			fixture.evidence.blocking_gates.push(error.message);
			await page.screenshot({ path: testInfo.outputPath("blocked-live-sync.png"), fullPage: true });
			throw error;
		} finally {
			await fixture.close();
		}
	});
});
