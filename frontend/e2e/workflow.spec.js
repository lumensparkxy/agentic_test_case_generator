import fs from "node:fs/promises";
import { devices, expect, test } from "@playwright/test";
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
import { installWorkspaceApi } from "./support/workspace.js";

test.use({ trace: "off", video: "off" });

test("anonymous portal opens provider chooser from sign-in", async ({ page }) => {
	await page.goto("/");
	const signIn = page.getByRole("button", { name: /^sign in$/i });
	await expect(signIn).toBeVisible();
	await signIn.click();
	await expect(page.getByRole("dialog", { name: /choose a sign-in method/i })).toBeVisible();
	for (const provider of ["google", "microsoft", "apple"])
		await expect(page.getByRole("button", { name: new RegExp(provider, "i") })).toBeVisible();
});

test("restored Firebase user stays signed out after reload", async ({ page }) => {
	const apiKey = process.env.E2E_FIREBASE_FIXTURE_API_KEY;
	test.skip(!apiKey, "Requires a synthetic Vite Firebase configuration with the matching fixture key.");

	const user = { sub: "firebase-fixture-user", name: "Firebase Fixture", email: "firebase-fixture@example.test" };
	const expires = Math.floor(Date.now() / 1000) + 3600;
	const payload = Buffer.from(JSON.stringify({ sub: user.sub, user_id: user.sub, exp: expires })).toString("base64url");
	const token = `eyJhbGciOiJSUzI1NiJ9.${payload}.synthetic`;
	await page.addInitScript(
		({ key, firebaseUser }) => {
			if (sessionStorage.getItem("firebase-fixture-seeded")) return;
			localStorage.setItem(key, JSON.stringify(firebaseUser));
			sessionStorage.setItem("firebase-fixture-seeded", "1");
		},
		{
			key: `firebase:authUser:${apiKey}:[DEFAULT]`,
			firebaseUser: {
				uid: user.sub,
				displayName: user.name,
				email: user.email,
				emailVerified: true,
				isAnonymous: false,
				providerData: [{ providerId: "google.com", uid: user.sub, email: user.email, displayName: user.name }],
				stsTokenManager: { refreshToken: "synthetic-refresh", accessToken: token, expirationTime: expires * 1000 },
				apiKey,
				appName: "[DEFAULT]",
			},
		}
	);
	await installWorkspaceApi(page);
	await page.route("https://identitytoolkit.googleapis.com/v1/accounts:lookup*", (route) =>
		route.fulfill({
			headers: { "Access-Control-Allow-Origin": "*" },
			json: { users: [{ localId: user.sub, email: user.email, emailVerified: true, displayName: user.name }] },
		})
	);
	let verifications = 0;
	await page.route("**/auth/me", (route) => {
		verifications += 1;
		expect(route.request().headers().authorization).toBe(`Bearer ${token}`);
		return route.fulfill({ json: user });
	});

	const account = page.getByRole("button", { name: `Open account menu for ${user.name}` });
	const signIn = page.getByRole("button", { name: "Sign In", exact: true });
	await page.goto("/");
	await expect(account).toBeVisible();
	await page.reload();
	await expect(account).toBeVisible();
	expect(verifications).toBe(2);
	await account.click();
	await page.getByRole("menuitem", { name: "Sign Out" }).click();
	await expect(signIn).toBeEnabled();
	expect(await page.evaluate(() => localStorage.getItem("tcg.auth.token"))).toBeNull();
	await page.reload();
	await expect(page.getByText("Sign in to parse requirements, generate test cases, and export artifacts.")).toBeVisible();
	await expect(signIn).toBeEnabled();
	expect(verifications).toBe(2);
});

test.describe("Mobile Firebase startup", () => {
	const { viewport, userAgent, isMobile, hasTouch, deviceScaleFactor } = devices["iPhone 13"];
	test.use({ viewport, userAgent, isMobile, hasTouch, deviceScaleFactor });

	test("loads the popup resolver on provider selection and recovers from failure", async ({ page }) => {
		let resolverRequests = 0;
		await page.route("https://apis.google.com/js/api.js*", async (route) => {
			resolverRequests += 1;
			await route.abort("failed");
		});
		await page.goto("/");
		await expect(page.getByText("Sign in to parse requirements, generate test cases, and export artifacts.")).toBeVisible();
		await page.getByRole("button", { name: "Open workspace controls" }).click();
		const signIn = page.getByRole("button", { name: /^sign in$/i });
		const chooser = page.getByRole("dialog", { name: "Choose a sign-in method" });
		const google = chooser.getByRole("button", { name: "Sign in with Google" });
		const status = page.getByRole("alert").filter({ hasText: "Sign-in with Google failed" });

		await signIn.click();
		await expect(chooser).toBeVisible();
		expect(resolverRequests).toBe(0);
		await google.click();
		await expect.poll(() => resolverRequests).toBe(1);
		await expect(status).toBeVisible();
		await expect(signIn).toBeEnabled();

		await signIn.click();
		await expect(status).toHaveCount(0);
		await google.click();
		await expect.poll(() => resolverRequests).toBe(2);
		await expect(status).toBeVisible();
		await expect(signIn).toBeEnabled();
	});
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
				await page.goto(`${fixture.prefix}/context`);
				await page.getByRole("textbox", { name: "Context notes", exact: true }).fill(context);
				await uiResponse(page, "/requirements/enrich", () => page.getByRole("button", { name: "Analyze Context", exact: true }).click());
				await page.reload();
				await expect(page.getByRole("textbox", { name: "Context notes", exact: true })).toHaveValue(context);
				const saved = await fixture.read();
				expect(saved.current_snapshots.context.payload.notes).toBe(context);
				expect(saved.stage_state.context.approved).toBe(true);
				expect(saved.current_snapshots.requirements.snapshot_id).toBe(current.current_snapshots.requirements.snapshot_id);
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
