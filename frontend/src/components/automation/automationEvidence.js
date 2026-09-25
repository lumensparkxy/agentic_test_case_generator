export function getAutomationEvidenceStatus(preview, run, { stale = false } = {}) {
	if (run?.run_id && typeof run.run_id === "string") {
		if (stale) return { status: "attention", label: "Past run; inputs changed" };
		if (run.status === "disabled") return { status: "execution_disabled", label: "Execution disabled" };
		const executed = Array.isArray(run.results) && run.results.some((result) => ["passed", "failed"].includes(result.status));
		if (executed && run.status === "passed") return { status: "execution_passed", label: "Execution passed" };
		if (run.status === "failed")
			return { status: "execution_failed", label: executed ? "Execution failed" : "Run failed before execution" };
		return { status: "unavailable", label: "Execution evidence unavailable" };
	}
	if (preview?.hasPreview) {
		if (stale || !preview.isConsistent || preview.requiresRefresh) return { status: "preview_refresh", label: "Preview needs refresh" };
		return { status: "preview_ready", label: "Preview ready; not executed" };
	}
	return { status: "pending", label: "Not executed" };
}
