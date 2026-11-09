import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import { isDeepStrictEqual } from "node:util";
import { expect, test } from "@playwright/test";

const existing = "B-EXISTING";
const originalSlot = "2026-10-01 10:00–11:00 Europe/Zurich";
const freeSlot = "2026-10-01 11:00–12:00 Europe/Zurich";
const validationError = "Enter a whole number from 1 to 8";
const conflict = "Room is already booked";

const bookingPost = (request) => request.method() === "POST" && new URL(request.url()).pathname === "/api/bookings";
const observe = (name, actual, expected) => ({ name, actual, expected, matched: isDeepStrictEqual(actual, expected) });
const checksum = (bytes) => createHash("sha256").update(bytes).digest("hex");
const result = (page) => page.getByRole("status", { name: "Booking submission", exact: true });
const confirmation = (page) => page.getByRole("region", { name: "Booking confirmation", exact: true });
const bookingRows = (page) => page.getByRole("table", { name: "My bookings", exact: true }).locator("tbody").getByRole("row");

async function signIn(page, origin, employee = "Alice") {
	const target = new URL(origin);
	expect(target.protocol).toBe("http:");
	expect(target.hostname).toBe("127.0.0.1");
	await page.goto(target.href);
	await page.getByRole("button", { name: `Sign in as ${employee}`, exact: true }).click();
	await expect(page.getByText(`Signed in as ${employee}`, { exact: true })).toBeVisible();
	await expect(page.getByRole("heading", { name: "My bookings", exact: true })).toBeVisible();
	await expect(bookingRows(page)).toHaveCount(employee === "Alice" ? 1 : 0);
}

async function fillBooking(page, { room = "Birch", slot = originalSlot, attendees = "4" } = {}) {
	await page.getByLabel("Room", { exact: true }).selectOption({ label: room });
	await page.getByLabel("Time slot", { exact: true }).selectOption({ label: slot });
	await page.getByLabel("Attendees", { exact: true }).fill(attendees);
}

async function submission(page, action = "Book") {
	const [response] = await Promise.all([
		page.waitForResponse((candidate) => bookingPost(candidate.request())),
		page.getByRole("button", { name: action, exact: true }).click(),
	]);
	const body = await response.json();
	if (body.field === "attendees") {
		await expect(page.getByRole("alert")).toBeVisible();
	} else if (body.error) {
		await expect(result(page)).toHaveText(body.error);
	} else {
		await expect(confirmation(page).getByText(body.id, { exact: true })).toBeVisible();
	}
	return { body, status: response.status(), request: response.request().postDataBuffer() };
}

async function rowsAfterReload(page) {
	await page.reload();
	await expect(page.getByRole("heading", { name: "My bookings", exact: true })).toBeVisible();
	const rows = await bookingRows(page).all();
	return Promise.all(rows.map(async (row) => (await row.getByRole("cell").allTextContents()).slice(0, 5)));
}

async function invalidAttendees(page, origin, value) {
	await signIn(page, origin);
	await fillBooking(page, { attendees: value });
	const { status } = await submission(page);
	const inlineAlert = page.getByRole("alert");
	const inlineError = (await inlineAlert.isVisible()) ? await inlineAlert.textContent() : "";
	const rows = await rowsAfterReload(page);
	const label = value || "empty";
	return [
		observe(`reject ${label}`, status, 422),
		observe(`show inline error for ${label}`, inlineError, validationError),
		observe(
			`retain only original booking after ${label}`,
			rows.map(([id]) => id),
			[existing]
		),
	];
}

const controls = {
	async "capacity-boundaries"(page, origin, limit = 8) {
		await signIn(page, origin);
		await fillBooking(page, { room: "Atlas", slot: freeSlot, attendees: "6" });
		const accepted = await submission(page);
		await fillBooking(page, { room: "Birch", slot: freeSlot, attendees: "7" });
		const upper = await submission(page);
		const inlineAlert = page.getByRole("alert");
		const inlineError = (await inlineAlert.isVisible()) ? await inlineAlert.textContent() : "";
		const rows = await rowsAfterReload(page);
		return [
			observe("accept 6 attendees", accepted.status, 201),
			observe(
				"save the confirmed booking at 6",
				rows.find(([id]) => id === "B-001"),
				["B-001", "Atlas", freeSlot, "6", "Confirmed"]
			),
			observe("submit 7 attendees", upper.status, limit === 6 ? 422 : 201),
			observe("show the capacity error for rejected 7", inlineError, limit === 6 ? "Enter a whole number from 1 to 6" : ""),
			observe(
				"preserve the exact booking IDs after refresh",
				rows.map(([id]) => id),
				limit === 6 ? [existing, "B-001"] : [existing, "B-001", "B-002"]
			),
		];
	},

	async "attendee-boundaries"(page, origin) {
		await signIn(page, origin);
		const checks = [];
		for (const [attendees, room] of [
			["1", "Atlas"],
			["8", "Birch"],
		]) {
			await fillBooking(page, { room, slot: freeSlot, attendees });
			const { status } = await submission(page);
			checks.push(observe(`accept ${attendees} attendees`, status, 201));
		}
		const rows = await rowsAfterReload(page);
		return [
			...checks,
			observe("show selected room on the first saved record", rows.find(([id]) => id === "B-001")?.[1], "Atlas"),
			observe("show submitted attendees on the first saved record", rows.find(([id]) => id === "B-001")?.[3], "1"),
			observe(
				"show both confirmed boundary bookings",
				rows.slice(1).map((row) => row.slice(1)),
				[
					["Atlas", freeSlot, "1", "Confirmed"],
					["Birch", freeSlot, "8", "Confirmed"],
				]
			),
		];
	},

	"invalid-empty": (page, origin) => invalidAttendees(page, origin, ""),
	"invalid-zero": (page, origin) => invalidAttendees(page, origin, "0"),
	"invalid-nine": (page, origin) => invalidAttendees(page, origin, "9"),
	"invalid-fraction": (page, origin) => invalidAttendees(page, origin, "1.5"),

	async "occupied-slot"(page, origin) {
		await signIn(page, origin, "Bob");
		await fillBooking(page, { room: "Atlas" });
		const { status } = await submission(page);
		const message = await result(page).textContent();
		const rows = await rowsAfterReload(page);
		await page.goto(`${origin}/bookings/${existing}`);
		const detail = page.getByRole("region", { name: "Booking details", exact: true });
		await expect(detail.getByText(existing, { exact: true })).toBeVisible();
		return [
			observe("reject occupied Atlas slot", status, 409),
			observe("show exact room conflict", message, conflict),
			observe("create no booking for Bob", rows.length, 0),
			observe("keep Alice's seeded Atlas booking confirmed", await detail.getByText("Confirmed", { exact: true }).count(), 1),
		];
	},

	async "independent-room"(page, origin) {
		await signIn(page, origin);
		await fillBooking(page);
		const { status } = await submission(page);
		const rows = await rowsAfterReload(page);
		return [
			observe("allow Birch in the same slot", status, 201),
			observe(
				"keep both rooms confirmed",
				rows.map((row) => [row[1], row[4]]),
				[
					["Atlas", "Confirmed"],
					["Birch", "Confirmed"],
				]
			),
		];
	},

	async "cancel-release"(page, origin) {
		await signIn(page, origin);
		await bookingRows(page).getByRole("button", { name: "Cancel booking", exact: true }).click();
		await expect(result(page)).toHaveText("Booking cancelled");
		await expect(bookingRows(page).getByRole("cell", { name: "Cancelled", exact: true })).toBeVisible();
		await fillBooking(page, { room: "Atlas" });
		const { status } = await submission(page);
		const rows = await rowsAfterReload(page);
		return [
			observe("accept a new booking in the cancelled slot", status, 201),
			observe(
				"preserve cancelled original beside new confirmation",
				rows.map((row) => [row[0], row[4]]),
				[
					[existing, "Cancelled"],
					["B-001", "Confirmed"],
				]
			),
		];
	},

	async "non-owner-cancel"(page, origin) {
		await signIn(page, origin, "Bob");
		await page.goto(`${origin}/bookings/${existing}`);
		const detail = page.getByRole("region", { name: "Booking details", exact: true });
		await expect(detail.getByText("Confirmed", { exact: true })).toBeVisible();
		const [response] = await Promise.all([
			page.waitForResponse((candidate) => candidate.request().method() === "POST" && new URL(candidate.url()).pathname.endsWith("/cancel")),
			detail.getByRole("button", { name: "Cancel booking", exact: true }).click(),
		]);
		const message = page.getByRole("status", { name: "Cancellation result", exact: true });
		await expect(message).not.toHaveText("");
		const text = await message.textContent();
		await page.reload();
		await expect(detail.getByText(existing, { exact: true })).toBeVisible();
		const status = await detail.getByText(/^(Confirmed|Cancelled)$/).textContent();
		return [
			observe("deny Bob's direct cancellation", response.status(), 403),
			observe("explain cancellation ownership", text, "Only the creator can cancel this booking"),
			observe("retain Alice's confirmed booking after reload", status, "Confirmed"),
		];
	},

	async "double-click"(page, origin) {
		await signIn(page, origin);
		await fillBooking(page);
		const responses = [];
		page.on("response", (response) => {
			if (bookingPost(response.request())) responses.push(response);
		});
		await page.getByRole("button", { name: "Book", exact: true }).dblclick();
		await expect.poll(() => responses.length).toBe(2);
		const payloads = await Promise.all(responses.map((response) => response.json()));
		const ids = payloads.map((payload) => payload.id).sort();
		await expect(confirmation(page).getByRole("heading", { name: "Booking confirmed", exact: true })).toBeVisible();
		const confirmationCount = await confirmation(page).count();
		const visibleId = await confirmation(page).locator("p").textContent();
		const rows = await rowsAfterReload(page);
		return [
			observe("send identical bodies on both clicks", responses[0].request().postData() === responses[1].request().postData(), true),
			observe("create once and replay once", responses.map((response) => response.status()).sort(), [200, 201]),
			observe("return the original identity on both clicks", ids, ["B-001", "B-001"]),
			observe("show one confirmation with the original ID", [confirmationCount, visibleId], [1, "B-001"]),
			observe(
				"show exactly one new booking after refresh",
				rows.map(([id]) => id),
				[existing, "B-001"]
			),
		];
	},

	async "response-loss"(page, origin, evidenceFile) {
		await signIn(page, origin);
		await fillBooking(page);
		const [lost] = await Promise.all([
			page.waitForEvent("requestfailed", { predicate: bookingPost }),
			page.getByRole("button", { name: "Book", exact: true }).click(),
		]);
		await expect(result(page)).toHaveText("Booking response was lost");
		const evidence = JSON.parse(await fs.readFile(evidenceFile, "utf8"));
		const retry = await submission(page, "Retry pending booking");
		const rows = await rowsAfterReload(page);
		return [
			observe(
				"lose a complete successful response",
				[evidence.event, evidence.upstream_status],
				["complete_upstream_response_before_downstream_loss", 201]
			),
			observe(
				"retry the identical committed request",
				[checksum(lost.postDataBuffer()), checksum(retry.request)],
				[evidence.request_sha256, evidence.request_sha256]
			),
			observe("return the recorded original ID on retry", retry.body.id, evidence.original_id),
			observe(
				"show one booking with the recorded ID after refresh",
				rows.map(([id]) => id),
				[existing, evidence.original_id]
			),
		];
	},
};

const faults = [
	["allow-nine", "invalid-nine", "retain only original booking after 9", [existing, "B-001"]],
	["reject-but-save", "invalid-nine", "retain only original booking after 9", [existing, "B-001"]],
	["allow-room-conflict", "occupied-slot", "create no booking for Bob", 1],
	["share-room-availability", "independent-room", "keep both rooms confirmed", [["Atlas", "Confirmed"]]],
	["deny-slot-release", "cancel-release", "preserve cancelled original beside new confirmation", [[existing, "Cancelled"]]],
	["allow-non-owner-cancel", "non-owner-cancel", "retain Alice's confirmed booking after reload", "Cancelled"],
	["duplicate-submission", "double-click", "show exactly one new booking after refresh", [existing, "B-001", "B-002"]],
	["duplicate-submission", "response-loss", "return the recorded original ID on retry", "B-002"],
	["wrong-saved-room", "attendee-boundaries", "show selected room on the first saved record", "Birch"],
	["wrong-saved-attendees", "attendee-boundaries", "show submitted attendees on the first saved record", "8"],
];

test("Room Booking controls distinguish healthy behavior from seeded faults", async ({ browser }, testInfo) => {
	test.skip(!process.env.E2E_ROOM_BOOKING_TARGETS, "Run with scripts/evaluate_room_booking.py");
	test.setTimeout(120_000);
	const targets = JSON.parse(process.env.E2E_ROOM_BOOKING_TARGETS);
	const measure = async (control, origin, input) => {
		const context = await browser.newContext();
		try {
			const page = await context.newPage();
			page.setDefaultTimeout(5000);
			return await controls[control](page, origin, input);
		} finally {
			await context.close();
		}
	};
	const report = { healthy: {}, defects: [] };
	for (const control of Object.keys(controls)) {
		await test.step(`Healthy ${control}`, async () => {
			const { origin, evidence } = control === "response-loss" ? targets.loss.healthy : { origin: targets.healthy };
			report.healthy[control] = await measure(control, origin, evidence);
		});
	}
	report.revisedCapacity = await test.step("Revised capacity accepts six and rejects seven", () =>
		measure("capacity-boundaries", targets.revised, 6));
	for (const [defect, control, assertion, faultValue] of faults) {
		await test.step(`Detect ${defect} using ${control}`, async () => {
			const { origin, evidence } = control === "response-loss" ? targets.loss[defect] : { origin: targets.defects[defect] };
			const checks = await measure(control, origin, evidence);
			if (defect === "reject-but-save") {
				expect(checks.find((check) => check.name === "reject 9")?.matched).toBe(true);
				expect(checks.find((check) => check.name === "show inline error for 9")?.matched).toBe(true);
			}
			const targeted = checks.find((check) => check.name === assertion);
			report.defects.push({
				defect,
				control,
				assertion,
				checks,
				detected:
					report.healthy[control].every((check) => check.matched) &&
					targeted?.matched === false &&
					isDeepStrictEqual(targeted.actual, faultValue),
			});
		});
	}
	const path = testInfo.outputPath("room-booking-matrix.json");
	await fs.writeFile(path, JSON.stringify(report, null, 2) + "\n");
	await testInfo.attach("Room Booking fault matrix", { path, contentType: "application/json" });
	for (const [control, checks] of Object.entries(report.healthy)) {
		expect(
			checks.filter((check) => !check.matched),
			control
		).toEqual([]);
	}
	expect(report.revisedCapacity.filter((check) => !check.matched)).toEqual([]);
	for (const sample of report.defects) {
		expect(sample.detected, JSON.stringify(sample)).toBe(true);
	}
});
