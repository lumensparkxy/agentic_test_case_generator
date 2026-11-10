import fs from "node:fs/promises";
import path from "node:path";
import { stripVTControlCharacters } from "node:util";

const ready = 'exact heading "My bookings" should be visible';
const submit = 'I click exact button "Book"';
const savedCount = /^css "#bookings tr" count should be (\d+)$/;

function rows(value) {
	return Array.isArray(value) ? value : [];
}

function reportTests(suites) {
	return rows(suites).flatMap((suite) => [...rows(suite?.specs).flatMap((spec) => rows(spec?.tests)), ...reportTests(suite?.suites)]);
}

function command(step) {
	return typeof step?.title === "string" ? step.title.match(/^(?:Given|When|Then|And) (.+)$/)?.[1] : undefined;
}

export function matchesPostRejectionCountFailure(report, specId, capacity = 8) {
	if (!specId || rows(report?.errors).length) return false;
	const matching = reportTests(report?.suites)
		.map((test) => {
			const terminal = rows(test?.results).at(-1);
			const ids = [...rows(test?.annotations), ...rows(terminal?.annotations)]
				.filter((annotation) => annotation?.type === "caseId")
				.map((annotation) => annotation.description);
			return { terminal, ids };
		})
		.filter(({ ids }) => ids.includes(specId));
	if (matching.length !== 1 || matching[0].ids.some((id) => id !== specId)) return false;
	const { terminal } = matching[0];
	if (terminal?.status !== "failed") return false;
	const steps = rows(terminal.steps);
	const failures = steps.filter((step) => step?.error);
	if (failures.length !== 1) return false;

	const failure = failures[0];
	const failureIndex = steps.indexOf(failure);
	if (steps.slice(failureIndex + 1).some(command)) return false;
	const check = command(failure);
	const expected = check?.match(savedCount)?.[1];
	if (expected === undefined || typeof failure.error.message !== "string") return false;
	const message = stripVTControlCharacters(failure.error.message);
	if (
		!message.startsWith("Error: expect(locator).toHaveCount(") ||
		!/^Locator:[ \t]+locator\(['"]#bookings tr['"]\)$/m.test(message) ||
		message.match(/^Expected:[ \t]+(\d+)$/m)?.[1] !== expected ||
		message.match(/^Received:[ \t]+(\d+)$/m)?.[1] !== String(Number(expected) + 1)
	) {
		return false;
	}

	// The same assertion occurs twice. Only the one after reopening can
	// show that a rejected request still wrote an extra row.
	const commands = steps.slice(0, failureIndex).map(command);
	const book = commands.indexOf(submit);
	const actors = new Set(commands.filter((step) => /^I click exact button "Sign in as [^"]+"$/.test(step || "")));
	if (book < 0 || book !== commands.lastIndexOf(submit) || actors.size !== 1) return false;
	const before = commands.slice(0, book);
	const entered = before.findLastIndex((step) => /^I enter .* into field "Attendees"$/.test(step || ""));
	const opened = before.findLastIndex((step) => step?.startsWith('I open "'));
	if (opened < 0 || entered <= opened || before[entered] !== `I enter "${capacity + 1}" into field "Attendees"`) return false;
	const starting = before.slice(opened + 1, entered);
	const readyBefore = starting.lastIndexOf(ready);
	const actorBefore = before.indexOf([...actors][0]);
	if (readyBefore < 0 || actorBefore < 0 || actorBefore >= opened + 1 + readyBefore || !starting.slice(readyBefore + 1).includes(check))
		return false;

	const after = commands.slice(book + 1);
	const rejected = after.indexOf(`exact text "Enter a whole number from 1 to ${capacity}" should be visible`);
	const reopened = after.findLastIndex((step) => step?.startsWith('I open "'));
	return rejected >= 0 && reopened > rejected && after[reopened] === before[opened] && after.slice(reopened + 1).includes(ready);
}

export async function matchesSavedPostRejectionCountFailure(item, capacity = 8) {
	if (item.status !== "failed" || !item.artifacts_dir || !item.report_json_path) return false;
	const reportPath = path.resolve(item.artifacts_dir, "results.json");
	if (path.resolve(item.report_json_path) !== reportPath) return false;
	try {
		const report = JSON.parse(await fs.readFile(reportPath, "utf8"));
		return matchesPostRejectionCountFailure(report, item.spec_id, capacity);
	} catch {
		return false;
	}
}
