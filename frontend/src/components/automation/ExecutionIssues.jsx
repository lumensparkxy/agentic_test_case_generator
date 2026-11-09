import { List, ListItem } from "../ui/collections";
import { Disclosure } from "../ui/surfaces";

// Playwright saves terminal colors with its assertion messages.
// eslint-disable-next-line no-control-regex
const TERMINAL_COLOR = /\x1b\[[0-9;]*m/g;

export function ExecutionIssues({ issues = [], label = "Recorded issues" }) {
	if (!issues.length) return null;
	// Playwright's singular error aliases the first entry in its errors array.
	const playwrightPaths = new Set(issues.filter((issue) => issue.code === "playwright.error").map((issue) => issue.path));
	const recorded = issues.filter((issue) => issue.code !== "playwright.error" || !playwrightPaths.has(`${issue.path}s[0]`));
	return (
		<List className="execution-recorded-issues" role="list" aria-label={label}>
			{recorded.map((issue, index) => (
				<ListItem key={`${issue.path}:${index}`}>
					<pre>{issue.message.replace(TERMINAL_COLOR, "")}</pre>
				</ListItem>
			))}
		</List>
	);
}

export function RunnerIssues({ issues }) {
	if (!Array.isArray(issues) || !issues.length) return null;
	return (
		<Disclosure className="execution-case-outcomes" open>
			<summary>Browser runner errors</summary>
			<ExecutionIssues issues={issues} label="Browser runner errors" />
		</Disclosure>
	);
}
