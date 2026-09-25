import fs from "node:fs/promises";
import { expect, test } from "@playwright/test";
import {
	liveFixture,
	roomSource,
	roomContext,
	uiResponse,
	importSource,
	applyImport,
	approveRequirements,
	approveScenarios,
	assertSourceMapping,
	caseQuality,
} from "./support/live-workflow.js";

test.use({ trace: "off", video: "off" });

test("anonymous portal opens provider chooser from sign-in", async ({ page }) => {
	await page.goto("/");
	await page.getByRole("button", { name: /^sign in$/i }).click();
	await expect(page.getByRole("dialog", { name: /choose a sign-in method/i })).toBeVisible();
	for (const provider of ["google", "microsoft", "apple"])
		await expect(page.getByRole("button", { name: new RegExp(provider, "i") })).toBeVisible();
});

test.describe("Live project workbench acceptance", () => {
	test.describe.configure({ retries: 0, timeout: 12 * 60 * 1000 });
	test.skip(
		process.env.E2E_LIVE !== "1",
		"Live model/Firestore checks require E2E_LIVE=1; mocked UI tests do not establish business acceptance."
	);

	test("isolated Room Booking project preserves source, review, generation and inspected export gates", async ({ page }, testInfo) => {
		// UI actions must fail promptly; model calls retain their explicit longer waits.
		page.setDefaultTimeout(30_000);
		const fixture = await liveFixture(page, testInfo);
		try {
			await test.step("Import and explicitly review source in the project workbench", async () => {
				const preview = await importSource(page, fixture);
				await fixture.save("source-import", preview);
				const applied = await applyImport(page, fixture, preview);
				assertSourceMapping(applied.current_snapshots.requirements.payload.requirements);
				await approveRequirements(page, fixture);
				await page.reload();
				assertSourceMapping((await fixture.read()).current_snapshots.requirements.payload.requirements);
			});

			await test.step("Compare and cancel the controlled 8 to 6 source change without replacing baseline", async () => {
				const before = await fixture.read();
				const v2 = (await fs.readFile(roomSource, "utf8"))
					.replace("1 to 8 inclusive", "1 to 6 inclusive")
					.replace("0, 9, or 1.5", "0, 7, or 1.5");
				expect(v2).not.toBe(await fs.readFile(roomSource, "utf8"));
				const preview = await importSource(page, fixture, {
					name: "room-booking-v2.md",
					mimeType: "text/markdown",
					buffer: Buffer.from(v2),
				});
				await fixture.save("cancelled-comparison", preview);
				await uiResponse(
					page,
					`/requirement-imports/${preview.import_id}`,
					() => page.getByRole("button", { name: "Cancel import", exact: true }).click(),
					{ status: 204 }
				);
				const after = await fixture.read();
				expect(after.current_revision).toBe(before.current_revision);
				expect(after.current_snapshots.requirements.snapshot_id).toBe(before.current_snapshots.requirements.snapshot_id);
			});

			await test.step("Save controlled context without inventing an application URL", async () => {
				const current = await fixture.read();
				const context = (await fs.readFile(roomContext, "utf8")).trim();
				await fixture.checked("POST", "/requirements/enrich", {
					requirements: current.current_snapshots.requirements.payload.requirements,
					notes: context,
					project_id: fixture.project.project_id,
					base_project_revision: current.current_revision,
				});
				await page.goto(`${fixture.prefix}/context`);
				const saved = await fixture.read();
				expect(saved.current_snapshots.context.payload.notes).toBe(context);
				expect(saved.stage_state.context.approved).toBe(true);
			});

			await test.step("Generate Use Cases and stop on failed quality before synthetic reviewer approval", async () => {
				await page.goto(`${fixture.prefix}/use-cases`);
				const response = await uiResponse(
					page,
					"/use-cases/generate",
					() => page.getByRole("button", { name: "Generate Use Cases", exact: true }).click(),
					{ timeout: 240_000 }
				);
				const result = await response.json();
				await fixture.save("use-case-generation", result);
				expect(result.current_snapshots.use_cases.metadata.guidance).toBeTruthy();
				expect(result.stage_state.use_cases.approved).toBe(false);
				await approveScenarios(page, fixture);
				await page.reload();
				expect((await fixture.read()).stage_state.use_cases.approved).toBe(true);
			});

			let generated;
			await test.step("Generate real cases and retain exact source, guidance and quality evidence", async () => {
				await page.goto(`${fixture.prefix}/test-cases`);
				const response = await uiResponse(
					page,
					"/testcases/generate",
					() => page.getByRole("button", { name: "Start generation", exact: true }).click(),
					{ timeout: 360_000 }
				);
				generated = await response.json();
				await fixture.save("generation", generated);
				const input = response.request().postDataJSON();
				expect(input.context.notes, "Saved source context must be sent unchanged to generation").toBe(
					(await fs.readFile(roomContext, "utf8")).trim()
				);
				expect(generated.guidance).toBeTruthy();
				const quality = caseQuality(generated.test_cases || []);
				await fixture.save("quality", quality);
				expect(quality.total, "Live generation returned no concrete cases; inspect generation diagnostics").toBeGreaterThan(0);
				expect(quality.withDescriptions).toBe(quality.total);
				expect(quality.withExpectedResults).toBe(quality.total);
				expect(quality.withRequirementLinks).toBe(quality.total);
				expect(quality.withTwoOrMoreSteps).toBeGreaterThanOrEqual(Math.ceil(quality.total * 0.8));
				for (const key of ["charFragmentCases", "invalidPriorities", "invalidTypes", "untitledCases"])
					expect(quality[key], key).toEqual([]);
				const current = await fixture.read();
				expect(current.current_snapshots.test_cases.payload.test_cases.map((c) => c.id)).toEqual(generated.test_cases.map((c) => c.id));
				await page.reload();
				await expect(page.getByRole("tab", { name: /Generated Test Cases/ })).toBeVisible();
			});

			await test.step("Inspect the real export, keeping draft and approved claims distinct", async () => {
				const current = await fixture.read();
				const snapshot = current.current_snapshots.test_cases;
				if (!generated.approved) {
					const ordinary = await fixture.api("POST", "/export/json", {
						test_cases: snapshot.payload.test_cases,
						project_id: current.project_id,
						base_project_revision: current.current_revision,
						source_snapshot_id: snapshot.snapshot_id,
					});
					expect(ordinary.status(), "Unapproved output must not export without an explicit draft reason").toBe(422);
				}
				await page.goto(`${fixture.prefix}/reports`);
				const toggle = page.getByLabel(/Export draft anyway/i);
				if (!generated.approved) {
					await expect(toggle).toBeVisible();
					await toggle.check();
					await page
						.getByLabel(/Reason for exporting this draft/i)
						.fill("Synthetic live QA evidence. Incomplete or unapproved output is retained as a draft, not accepted for release.");
				}
				const exportButton = page.getByRole("button", { name: /JSON API\/Import ready/i });
				await expect(exportButton).toBeEnabled();
				const [download] = await Promise.all([page.waitForEvent("download"), exportButton.click()]);
				const destination = testInfo.outputPath("inspected-export.json");
				await download.saveAs(destination);
				const exported = JSON.parse(await fs.readFile(destination, "utf8"));
				expect(exported.test_cases.map((c) => c.id)).toEqual(snapshot.payload.test_cases.map((c) => c.id));
				expect(exported.audit_metadata.snapshot_id).toBe(snapshot.snapshot_id);
				expect(exported.audit_metadata.project_revision).toBe(current.current_revision);
				await fixture.save("export-inspection", exported);
			});

			await test.step("Business acceptance stays blocked until substantive coverage and outstanding work pass", async () => {
				expect(generated.generation_tasks, "Unresolved source or planned-scenario work remains; draft export is not acceptance").toEqual(
					[]
				);
				expect(generated.substantive_assessment?.status, "Business coverage is incomplete or unverified").toBe("assessed_complete");
				expect(generated.approved, "The delivered suite has not passed review").toBe(true);
				testInfo.annotations.push({
					type: "business-review-limit",
					description: "Automated synthetic design gates passed; independent human review and real target execution are still separate.",
				});
			});
		} catch (error) {
			fixture.evidence.blocking_gates.push(error.message);
			await page.screenshot({ path: testInfo.outputPath("blocked-live-journey.png"), fullPage: true });
			throw error;
		} finally {
			await fixture.close();
		}
	});
});
