import fs from "node:fs/promises";
import { stripVTControlCharacters } from "node:util";
import { expect, test } from "@playwright/test";
import {
	applyImport,
	approveRequirements,
	approveScenarios,
	assertSourceMapping,
	importSource,
	liveFixture,
	uiResponse,
} from "./support/live-workflow.js";

const faults = {
	"allow-nine": ["ROOM-101", ["Enter a whole number from 1 to 8"]],
	"allow-room-conflict": ["ROOM-205", ["Room is already booked"]],
	"share-room-availability": ["ROOM-205", ["Booking confirmed", "B-001"]],
	"deny-slot-release": ["ROOM-310", ["Booking confirmed", "B-001"]],
	"allow-non-owner-cancel": ["ROOM-310", ["Only the creator can cancel this booking", "Confirmed"]],
};

function loopbackUrl(raw) {
	const url = new URL(raw);
	expect(url.protocol).toBe("http:");
	expect(url.hostname).toBe("127.0.0.1");
	return url.href;
}

function matchedAssertions(item, observations) {
	if (item.status !== "failed") return [];
	const messages = (item.issues || [])
		.filter((issue) => issue.code === "playwright.error")
		.map((issue) => stripVTControlCharacters(issue.message))
		.filter((message) => /expect\([^\n]*\)\.(?:not\.)?to(?:BeVisible|HaveText|HaveCount)/.test(message));
	return observations.filter((text) => messages.some((message) => message.includes(`"${text}"`) || message.includes(`'${text}'`)));
}

test.use({ trace: "off", video: "off" });

test.describe("Generated Room Booking execution", () => {
	test.describe.configure({ retries: 0, timeout: 20 * 60 * 1000 });
	test.skip(process.env.E2E_ROOM_BOOKING_WORKBENCH !== "1", "Run via scripts/e2e_workbench_local.py --fixture room-booking");

	test("measures the exact generated suite against healthy and faulted booking targets", async ({ page }, testInfo) => {
		page.setDefaultTimeout(30_000);
		expect(process.env.E2E_DEMO_PROJECT).toBe("demo-tcg-workbench");
		const target = loopbackUrl(process.env.E2E_BOOKING_TARGET);
		const lossTarget = loopbackUrl(process.env.E2E_BOOKING_LOSS_TARGET);
		const variants = JSON.parse(process.env.E2E_BOOKING_DEFECTS);
		const notes = (await fs.readFile(new URL("./fixtures/room-booking-browser-context.txt", import.meta.url), "utf8"))
			.replaceAll("{{target}}", target)
			.replaceAll("{{lossTarget}}", lossTarget)
			.trim();
		const fixture = await liveFixture(page, testInfo, { namePrefix: "Synthetic generated Room Booking execution" });
		try {
			const { requirements: sourceRequirements, prerequisite } = await test.step("Save source and observed fixture context", async () => {
				const preview = await importSource(page, fixture);
				await applyImport(page, fixture, preview);
				const approved = await approveRequirements(page, fixture);
				const requirements = approved.current_snapshots.requirements.payload.requirements;
				assertSourceMapping(requirements);
				const retryRequirements = requirements.filter(
					(row) =>
						/\bretr(?:y|ies|ying)\b/i.test(row.text) &&
						row.sources.some(
							(source) =>
								source.excerpt_verified && source.original_requirement_ids.includes("ROOM-440") && /response/i.test(source.excerpt)
						)
				);
				expect(retryRequirements.length, "Verified ROOM-440 response-loss retry requirement").toBeGreaterThan(0);
				const proxyPage = await page.request.get(lossTarget, { timeout: 5000 });
				expect(proxyPage.ok(), "Response-loss proxy is provisioned").toBe(true);
				expect(await proxyPage.text()).toContain("Meeting Room Booking");
				const prerequisite = {
					name: "responseLossProxyService",
					kind: "environment",
					description: `Provisioned manual response-loss proxy at ${lossTarget}, bound to POST /api/bookings.`,
					reference: "fixture:room-booking-response-loss",
					requirement_ids: retryRequirements.map((row) => row.id),
					status: "configured",
				};
				const context = { app_link: target, notes, execution_prerequisites: [prerequisite] };
				await fixture.checked("POST", "/requirements/enrich", {
					...context,
					project_id: approved.project_id,
					base_project_revision: approved.current_revision,
					requirements,
				});
				expect((await fixture.read()).current_snapshots.context.payload).toMatchObject(context);
				await fixture.save("target-context", { target, notes, requirements, prerequisite });
				return { requirements, prerequisite };
			});

			await test.step("Generate and review Use Cases through their normal gate", async () => {
				await page.goto(`${fixture.prefix}/use-cases`);
				const planned = await uiResponse(
					page,
					"/use-cases/generate",
					() => page.getByRole("button", { name: "Generate Use Cases", exact: true }).click(),
					{
						timeout: 300_000,
					}
				);
				await fixture.save("generated-use-cases", await planned.json());
				await approveScenarios(page, fixture);
			});

			const suite = await test.step("Save and inspect the exact generated suite", async () => {
				await page.goto(`${fixture.prefix}/test-cases`);
				const response = await uiResponse(
					page,
					"/testcases/generate",
					() => page.getByRole("button", { name: "Start generation", exact: true }).click(),
					{
						timeout: 420_000,
					}
				);
				const generated = await response.json();
				await fixture.save("generated-suite", generated);
				const saved = await fixture.read();
				const snapshot = saved.current_snapshots.test_cases;
				expect(snapshot.payload.test_cases.map((item) => item.id)).toEqual(generated.test_cases.map((item) => item.id));
				expect(snapshot.metadata.source_use_case_snapshot_id).toBe(saved.stage_state.use_cases.current_snapshot_id);
				return snapshot;
			});

			await page.goto(`${fixture.prefix}/automation`);
			await page.getByPlaceholder("staging, dev, customer-a").fill("booking-healthy");
			await page.getByRole("textbox", { name: "Target base URL", exact: true }).fill(target);
			const previewResponse = await uiResponse(page, "/automation/execution/preview", () =>
				page.getByRole("button", { name: "Preview Execution", exact: true }).click()
			);
			const preview = await previewResponse.json();
			await fixture.save("healthy-preview", preview);
			expect(preview.readiness.source_snapshot_id).toBe(suite.snapshot_id);
			const assessment = suite.payload.substantive_assessment || {};
			const candidates = preview.executable.map((item) => item.id);
			const matrix = {
				snapshot_id: suite.snapshot_id,
				design_status: assessment.design_status,
				combined_status: assessment.status,
				execution_setup: assessment.execution_status,
				execution_blockers: assessment.execution_blockers,
				generation_tasks: suite.payload.generation_tasks || [],
				preview: preview.summary,
				admission: preview.readiness,
				healthy: null,
				faults: [],
				manual_probe: "Double-click and response loss are covered by authored controls on this fixture.",
			};
			try {
				const retryCases = suite.payload.test_cases.filter((row) =>
					row.linked_requirement_ids.some((id) => prerequisite.requirement_ids.includes(id))
				);
				expect(retryCases.length, "Generated coverage for the response-loss retry").toBeGreaterThan(0);
				for (const row of retryCases) {
					expect(row.execution_prerequisites).toContainEqual(prerequisite);
					expect(row.automation_status).toBe("Manual");
					expect(preview.manual.map((item) => item.source_test_case_id)).toContain(row.id);
				}
				expect(preview.readiness.run_allowed, JSON.stringify(preview.readiness.blockers)).toBe(true);
				expect(candidates.length, "No generated case compiled for this supplied target").toBeGreaterThan(0);
				const healthyResponse = await uiResponse(
					page,
					"/automation/execution/run",
					() => page.getByRole("button", { name: new RegExp(`^Run ${candidates.length} Candidates?$`) }).click(),
					{ timeout: 240_000 }
				);
				const healthy = await healthyResponse.json();
				matrix.healthy = { run_id: healthy.run_id, status: healthy.status, summary: healthy.summary, results: healthy.results };
				await fixture.save("healthy-run", healthy);
				expect(healthy.results.map((item) => item.id)).toEqual(candidates);

				const requirementIds = new Map(
					sourceRequirements.map((row) => [row.id, row.sources.flatMap((item) => item.original_requirement_ids || [])])
				);
				const sourceIdsByCase = new Map(
					suite.payload.test_cases.map((row) => [row.id, new Set(row.linked_requirement_ids.flatMap((id) => requirementIds.get(id) || []))])
				);
				const baseInput = previewResponse.request().postDataJSON();
				for (const [defect, [sourceId, observations]] of Object.entries(faults)) {
					const eligible = preview.executable.filter((item) => sourceIdsByCase.get(item.source_test_case_id)?.has(sourceId));
					const passing = eligible.filter((item) => healthy.results.some((result) => result.id === item.id && result.status === "passed"));
					const record = { defect, sourceId, eligible: eligible.map((item) => item.id), tested: passing.map((item) => item.id) };
					matrix.faults.push(record);
					if (!passing.length) {
						record.status = eligible.length ? "no_healthy_pass" : "no_executable_case";
						continue;
					}
					const current = await fixture.read();
					const run = await fixture.checked("POST", "/automation/execution/run", {
						...baseInput,
						base_project_revision: current.current_revision,
						target_environment: `booking-${defect}`,
						target_base_url: loopbackUrl(variants[defect]),
						selected_test_case_ids: record.tested,
					});
					await fixture.save(`fault-${defect}`, run);
					expect(run.results.map((item) => item.id)).toEqual(record.tested);
					record.run_id = run.run_id;
					record.matches = run.results.flatMap((item) =>
						matchedAssertions(item, observations).map((observation) => ({ id: item.id, observation }))
					);
					record.status = record.matches.length
						? "detected"
						: run.results.every((item) => item.status === "passed")
							? "survived"
							: "unmatched_failure";
				}

				await page.goto(`${fixture.prefix}/reports`);
				await page.reload();
				await expect(page.getByRole("heading", { name: "Export Test Cases" })).toBeVisible();
				const persisted = (await fixture.read()).execution_runs;
				const runIds = [healthy.run_id, ...matrix.faults.flatMap((row) => (row.run_id ? [row.run_id] : []))];
				expect(persisted.map((item) => item.run_id)).toEqual(runIds.reverse());
				expect(persisted.every((item) => item.source_snapshot_id === suite.snapshot_id)).toBe(true);
				expect(healthy.summary.passed, "At least one generated browser case must pass on the healthy fixture").toBeGreaterThan(0);
				expect(
					matrix.faults.some((row) => row.status === "detected"),
					"No generated case detected a matched seeded fault"
				).toBe(true);
			} finally {
				await fixture.save("booking-matrix", matrix);
			}
		} catch (error) {
			fixture.evidence.blocking_gates.push(error.message);
			throw error;
		} finally {
			await fixture.close();
		}
	});
});
