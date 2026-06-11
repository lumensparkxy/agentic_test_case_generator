export default function WorkflowDiagnostics({
	title,
	diagnostics,
	appliedSettings,
	iterationHistory,
}) {
	if (!diagnostics && !appliedSettings) {
		return null;
	}

	const warnings = diagnostics?.warnings || [];
	const parserFailures = diagnostics?.parser_failures || [];
	const pillEntries = [
		appliedSettings?.approval_threshold != null ? `Threshold ${appliedSettings.approval_threshold}` : null,
		appliedSettings?.max_iterations != null ? `Max iter ${appliedSettings.max_iterations}` : null,
		diagnostics?.status ? `Status ${diagnostics.status}` : null,
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
						<span className="workflow-diagnostics-pill" key={entry}>{entry}</span>
					))}
				</div>
			)}
			{warnings.length > 0 && (
				<div className="workflow-diagnostics-block warning">
					<strong>Warnings</strong>
					<ul>
						{warnings.map((warning) => <li key={warning}>{warning}</li>)}
					</ul>
				</div>
			)}
			{parserFailures.length > 0 && (
				<div className="workflow-diagnostics-block alert">
					<strong>Parser issues</strong>
					<ul>
						{parserFailures.map((failure) => <li key={failure}>{failure}</li>)}
					</ul>
				</div>
			)}
		</div>
	);
}
