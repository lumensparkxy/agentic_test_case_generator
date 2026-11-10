import { expect, test } from "@playwright/test";
import { matchesPostRejectionCountFailure } from "./support/booking-fault-evidence.js";

const specId = "tc_003";
const target = "http://127.0.0.1:53283/";
const ready = 'And exact heading "My bookings" should be visible';
const book = 'And I click exact button "Book"';
const reopen = (url) => `And I open "${url}"`;
const rejected = (capacity) => `And exact text "Enter a whole number from 1 to ${capacity}" should be visible`;
const enter = (attendees) => `And I enter "${attendees}" into field "Attendees"`;
const count = (rows) => `And css "#bookings tr" count should be ${rows}`;
const countError = (expected, received, locator = "#bookings tr") => ({
	message: [
		"Error: \x1b[2mexpect(\x1b[22m\x1b[31mlocator\x1b[39m\x1b[2m).\x1b[22mtoHaveCount\x1b[2m(\x1b[22m\x1b[32mexpected\x1b[39m\x1b[2m)\x1b[22m failed",
		"",
		`Locator:  locator('${locator}')`,
		`Expected: \x1b[32m${expected}\x1b[39m`,
		`Received: \x1b[31m${received}\x1b[39m`,
		"Timeout:  5000ms",
	].join("\n"),
});

function rejectionSteps(capacity, baselineCount) {
	const actor = baselineCount ? "Alice" : "Bob";
	return [
		`Given I open "${target}"`,
		`When I click exact button "Sign in as ${actor}"`,
		`Then exact text "Signed in as ${actor}" should be visible`,
		ready,
		count(baselineCount),
		'And I select exact option "Birch" in field "Room"',
		'And I select exact option "2026-10-01 10:00–11:00 Europe/Zurich" in field "Time slot"',
		enter(capacity + 1),
		`And field "Attendees" value should equal "${capacity + 1}"`,
		book,
		rejected(capacity),
		reopen(target),
		ready,
		count(baselineCount),
	].map((title) => ({ title, duration: 10 }));
}

function reportFor(steps, { error = countError(1, 2), status = "failed" } = {}) {
	const annotations = [
		{ type: "specId", description: specId },
		{ type: "caseId", description: specId },
	];
	const result = {
		retry: 0,
		status,
		annotations,
		error,
		errors: [error],
		steps: [...steps.slice(0, -1), { ...steps.at(-1), error }],
	};
	const run = { expectedStatus: "passed", status: "unexpected", annotations, results: [result] };
	const suite = { specs: [{ title: "Submit booking above capacity", ok: false, tests: [run] }] };
	return { report: { errors: [], suites: [{ title: `${specId}.spec.ts`, specs: [], suites: [suite] }] }, run, result, suite };
}

test.describe("Booking fault evidence", { tag: "@p1" }, () => {
	test("credits only the exact post-rejection count increase", () => {
		const { report } = reportFor(rejectionSteps(6, 1));
		expect(matchesPostRejectionCountFailure(report, specId, 6)).toBe(true);
		expect(matchesPostRejectionCountFailure(report, specId, 8)).toBe(false);
		expect(matchesPostRejectionCountFailure(report, "tc_006", 6)).toBe(false);
		const original = reportFor(rejectionSteps(8, 0), { error: countError(0, 1) });
		expect(matchesPostRejectionCountFailure(original.report, specId)).toBe(true);
	});

	test("does not credit the identical count failure before submitting", () => {
		const steps = rejectionSteps(6, 1);
		const firstCount = steps.findIndex(({ title }) => title === count(1));
		const early = reportFor(steps.slice(0, firstCount + 1));
		expect(matchesPostRejectionCountFailure(early.report, specId, 6)).toBe(false);

		const ambiguous = reportFor(steps);
		ambiguous.result.steps[firstCount].error = countError(1, 2);
		expect(matchesPostRejectionCountFailure(ambiguous.report, specId, 6)).toBe(false);
	});

	test("requires one actor, the latest attendee input, the exact Book rejection, and the same ready page", () => {
		const original = rejectionSteps(6, 1);
		const replace = (steps, previous, next) => {
			steps.find(({ title }) => title === previous).title = next;
		};
		const insertBefore = (steps, before, title) =>
			steps.splice(
				steps.findIndex((step) => step.title === before),
				0,
				{ title, duration: 10 }
			);
		for (const [label, change] of [
			["different ready count before submit", (steps) => replace(steps, count(1), count(0))],
			["later valid value before submission", (steps) => insertBefore(steps, book, enter(6))],
			["different trigger", (steps) => replace(steps, book, 'And I click exact button "Cancel booking"')],
			["second Book trigger", (steps) => insertBefore(steps, rejected(6), book)],
			["different rejection", (steps) => replace(steps, rejected(6), 'And exact text "Room is already booked" should be visible')],
			["different page on same origin", (steps) => replace(steps, reopen(target), reopen(`${target}bookings/B-EXISTING`))],
			[
				"missing loaded heading after reopening",
				(steps) =>
					steps.splice(
						steps.findLastIndex(({ title }) => title === ready),
						1
					),
			],
			["different actor after reopening", (steps) => insertBefore(steps, reopen(target), 'And I click exact button "Sign in as Bob"')],
		]) {
			const steps = structuredClone(original);
			change(steps);
			expect(matchesPostRejectionCountFailure(reportFor(steps).report, specId, 6), label).toBe(false);
		}
	});

	test("does not credit a timeout or an unrelated count mismatch", () => {
		const steps = rejectionSteps(6, 1);
		for (const [label, options] of [
			["test timeout", { status: "timedOut", error: { message: "Test timeout of 30000ms exceeded." } }],
			["timed-out attempt with matching count text", { status: "timedOut" }],
			["wrong locator", { error: countError(1, 2, "#unrelated tr") }],
			["more than one extra row", { error: countError(1, 3) }],
		]) {
			expect(matchesPostRejectionCountFailure(reportFor(steps, options).report, specId, 6), label).toBe(false);
		}
	});

	test("ignores matching evidence from an earlier Playwright attempt", () => {
		const { report, run, result } = reportFor(rejectionSteps(6, 1));
		const later = reportFor(rejectionSteps(6, 1).slice(0, 5));
		run.results = [result, { ...later.result, retry: 1 }];
		expect(matchesPostRejectionCountFailure(report, specId, 6)).toBe(false);
	});

	test("rejects additional case steps after the claimed terminal count failure", () => {
		const { report, result } = reportFor(rejectionSteps(6, 1));
		result.steps.push({ title: 'And exact heading "My bookings" should be visible' });
		expect(matchesPostRejectionCountFailure(report, specId, 6)).toBe(false);
	});

	test("rejects a duplicate case identity anywhere in the report", () => {
		const { report, result, suite } = reportFor(rejectionSteps(6, 1));
		const conflicting = reportFor(rejectionSteps(6, 1));
		conflicting.result.annotations = [...result.annotations, { type: "caseId", description: "tc_006" }];
		expect(matchesPostRejectionCountFailure(conflicting.report, specId, 6)).toBe(false);
		report.suites.push({ title: "duplicate.spec.ts", specs: [], suites: [structuredClone(suite)] });
		expect(matchesPostRejectionCountFailure(report, specId, 6)).toBe(false);
	});
});
