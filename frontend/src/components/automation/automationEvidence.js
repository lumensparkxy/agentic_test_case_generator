export function formatExecutionRunSummary(run) {
	const summary = run?.summary || {};
	const flaky = summary.flaky ? `, ${summary.flaky} flaky` : "";
	const runnerFailure = run?.issues?.length ? " The browser runner reported an error." : "";
	return `Execution ${run?.status || "finished"}: ${summary.passed || 0} passed, ${summary.failed || 0} failed${flaky}, ${summary.invalid || 0} invalid.${runnerFailure}`;
}

export const formatExecutionCaseStatus = (status) => (status === "flaky" ? "Flaky (passed on retry)" : status);

export function getAutomationEvidenceStatus(preview, run, { stale = false } = {}) {
	if (run?.run_id && typeof run.run_id === "string") {
		if (stale) return { status: "attention", label: "Past run; inputs changed" };
		if (run.status === "disabled") return { status: "execution_disabled", label: "Execution disabled" };
		const results = Array.isArray(run.results) ? run.results : [];
		const flaky = run.summary?.flaky > 0 || results.some((result) => result.status === "flaky");
		const executed = flaky || results.some((result) => ["passed", "failed"].includes(result.status));
		if (run.issues?.length) return { status: "execution_failed", label: "Browser runner failed" };
		if (run.status === "failed")
			return { status: "execution_failed", label: executed ? "Execution failed" : "Run failed before execution" };
		if (flaky && ["flaky", "passed"].includes(run.status)) return { status: "execution_flaky", label: "Flaky run" };
		if (executed && run.status === "passed") return { status: "execution_passed", label: "Execution passed" };
		return { status: "unavailable", label: "Execution evidence unavailable" };
	}
	if (preview?.hasPreview) {
		if (stale || !preview.isConsistent || preview.requiresRefresh) return { status: "preview_refresh", label: "Preview needs refresh" };
		return { status: "preview_ready", label: "Preview ready; not executed" };
	}
	return { status: "pending", label: "Not executed" };
}
