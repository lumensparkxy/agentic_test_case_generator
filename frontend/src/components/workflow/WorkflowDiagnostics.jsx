const SOURCE_LABELS = {
	model: "Model-authored",
	model_recovered: "Recovered model",
	parallel_retry: "Parallel retry",
	deterministic_coverage_completion: "Deterministic completion",
	deterministic_full_fallback: "Deterministic full fallback",
};

const SOURCE_ORDER = ["model", "model_recovered", "parallel_retry", "deterministic_coverage_completion", "deterministic_full_fallback"];

function formatLabel(value) {
	return String(value || "")
		.replace(/_/g, " ")
		.replace(/\b\w/g, (char) => char.toUpperCase());
}

function numericValue(value) {
	const parsed = Number(value || 0);
	return Number.isFinite(parsed) ? parsed : 0;
}

function sourceCountEntries(counts) {
	return Object.entries(counts || {})
		.map(([source, count]) => ({ source, count: numericValue(count) }))
		.filter((entry) => entry.count > 0)
		.sort((left, right) => {
			const leftIndex = SOURCE_ORDER.indexOf(left.source);
			const rightIndex = SOURCE_ORDER.indexOf(right.source);
			const normalizedLeft = leftIndex === -1 ? SOURCE_ORDER.length : leftIndex;
			const normalizedRight = rightIndex === -1 ? SOURCE_ORDER.length : rightIndex;
			return normalizedLeft - normalizedRight || left.source.localeCompare(right.source);
		});
}

function isStructuredCompletionWarning(warning, diagnostics) {
	if (diagnostics?.completion_source !== "coverage_completion") {
		return false;
	}
	return /deterministic coverage completion|deterministic coverage case|optional deterministic case|must-have deterministic case/i.test(
		String(warning || "")
	);
}

export default function WorkflowDiagnostics({ title, diagnostics, appliedSettings, iterationHistory }) {
	if (!diagnostics && !appliedSettings) {
		return null;
	}

	const warnings = diagnostics?.warnings || [];
	const actionableWarnings = warnings.filter((warning) => !isStructuredCompletionWarning(warning, diagnostics));
	const parserRecoveries = diagnostics?.parser_recoveries || [];
	const parserFailures = diagnostics?.parser_failures || [];
	const sourceCounts = sourceCountEntries(diagnostics?.generation_source_counts);
	const deterministicStats = [
		{ label: "Must-have gaps", value: numericValue(diagnostics?.missing_must_have_scenario_count) },
		{ label: "Optional/planned gaps", value: numericValue(diagnostics?.missing_optional_scenario_count) },
		{ label: "Missing requirements", value: numericValue(diagnostics?.missing_requirements_count) },
		{ label: "Must-have additions", value: numericValue(diagnostics?.deterministic_must_have_additions) },
		{ label: "Optional additions", value: numericValue(diagnostics?.deterministic_optional_additions) },
		{ label: "Total additions", value: numericValue(diagnostics?.deterministic_total_additions) },
	].filter((entry) => entry.value > 0);
	const hasCompletionSummary = diagnostics?.completion_source || deterministicStats.length > 0;
	const pillEntries = [
		appliedSettings?.approval_threshold != null ? `Threshold ${appliedSettings.approval_threshold}` : null,
		appliedSettings?.max_iterations != null ? `Max iter ${appliedSettings.max_iterations}` : null,
		diagnostics?.status ? `Status ${diagnostics.status}` : null,
		diagnostics?.generation_route ? `Route ${formatLabel(diagnostics.generation_route)}` : null,
		diagnostics?.shard_count ? `Shards ${diagnostics.shard_count}` : null,
		diagnostics?.worker_count ? `Workers ${diagnostics.worker_count}` : null,
		iterationHistory?.length ? `Iterations ${iterationHistory.length}` : null,
		diagnostics?.best_iteration ? `Best iter ${diagnostics.best_iteration}` : null,
		diagnostics?.timed_out ? "Timed out" : null,
		diagnostics?.stalled ? "Stalled" : null,
		diagnostics?.used_fallback ? "Fallback used" : null,
	].filter(Boolean);

	return (
		<div className="workflow-diagnostics-panel">
			<div className="workflow-diagnostics-header">
				<h3>{title}</h3>
				{diagnostics?.failure_reason && <span className="workflow-diagnostics-reason">Reason: {diagnostics.failure_reason}</span>}
			</div>
			{pillEntries.length > 0 && (
				<div className="workflow-diagnostics-pills">
					{pillEntries.map((entry) => (
						<span className="workflow-diagnostics-pill" key={entry}>
							{entry}
						</span>
					))}
				</div>
			)}
			{sourceCounts.length > 0 && (
				<div className="workflow-diagnostics-block source">
					<strong>Generation sources</strong>
					<div className="workflow-diagnostics-stat-grid">
						{sourceCounts.map((entry) => (
							<div className="workflow-diagnostics-stat" key={entry.source}>
								<span>{SOURCE_LABELS[entry.source] || formatLabel(entry.source)}</span>
								<strong>{entry.count}</strong>
							</div>
						))}
					</div>
				</div>
			)}
			{hasCompletionSummary && (
				<div className="workflow-diagnostics-block info">
					<strong>Deterministic completion</strong>
					<div className="workflow-diagnostics-stat-grid">
						{diagnostics?.completion_source && (
							<div className="workflow-diagnostics-stat">
								<span>Completion source</span>
								<strong>{formatLabel(diagnostics.completion_source)}</strong>
							</div>
						)}
						{deterministicStats.map((entry) => (
							<div className="workflow-diagnostics-stat" key={entry.label}>
								<span>{entry.label}</span>
								<strong>{entry.value}</strong>
							</div>
						))}
					</div>
				</div>
			)}
			{actionableWarnings.length > 0 && (
				<div className="workflow-diagnostics-block warning">
					<strong>Warnings</strong>
					<ul>
						{actionableWarnings.map((warning) => (
							<li key={warning}>{warning}</li>
						))}
					</ul>
				</div>
			)}
			{parserRecoveries.length > 0 && (
				<div className="workflow-diagnostics-block info">
					<strong>Parser recoveries</strong>
					<ul>
						{parserRecoveries.map((recovery) => (
							<li key={recovery}>{recovery}</li>
						))}
					</ul>
				</div>
			)}
			{parserFailures.length > 0 && (
				<div className="workflow-diagnostics-block alert">
					<strong>Parser issues</strong>
					<ul>
						{parserFailures.map((failure) => (
							<li key={failure}>{failure}</li>
						))}
					</ul>
				</div>
			)}
		</div>
	);
}
