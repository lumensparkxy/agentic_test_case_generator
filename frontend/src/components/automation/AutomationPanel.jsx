const renderBucketCount = (label, value, tone = "") => (
	<span className={`workflow-diagnostics-pill ${tone}`.trim()}>
		{label} {value || 0}
	</span>
);

const renderCandidateTable = (candidates) => {
	if (!candidates.length) {
		return <span className="helper-text">No executable candidates in the current preview.</span>;
	}

	return (
		<div className="selection-table-wrapper">
			<table className="selection-table">
				<thead>
					<tr>
						<th>Case</th>
						<th>Title</th>
						<th>Spec</th>
						<th>Traceability</th>
					</tr>
				</thead>
				<tbody>
					{candidates.map((candidate) => (
						<tr key={candidate.id}>
							<td>
								<strong>{candidate.source_test_case_id}</strong>
							</td>
							<td>{candidate.title}</td>
							<td>
								{candidate.spec?.steps?.length || 0} step{(candidate.spec?.steps?.length || 0) === 1 ? "" : "s"}
							</td>
							<td>{candidate.traceability_ids?.join(", ") || "None"}</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
};

const renderManualList = (candidates) => {
	if (!candidates.length) {
		return <span className="helper-text">No manual cases in the current preview.</span>;
	}

	return (
		<ul className="jira-sync-apply-list">
			{candidates.map((candidate) => (
				<li key={candidate.id}>
					<strong>{candidate.source_test_case_id}</strong> - {candidate.title}
					{candidate.review_reasons?.length ? `: ${candidate.review_reasons[0]}` : ""}
				</li>
			))}
		</ul>
	);
};

const renderUnsupportedList = (candidates) => {
	if (!candidates.length) {
		return <span className="helper-text">No unsupported cases in the current preview.</span>;
	}

	return (
		<div className="jira-sync-preview-list">
			{candidates.map((candidate) => (
				<div key={candidate.id} className="jira-sync-preview-card conflict">
					<div className="jira-sync-preview-header">
						<div>
							<strong>{candidate.source_test_case_id}</strong>
							<span>{candidate.title}</span>
						</div>
						<span className="jira-status-badge conflict">{candidate.status}</span>
					</div>
					<ul className="jira-sync-warning-list">
						{candidate.unsupported_steps?.map((step) => (
							<li key={`${candidate.id}-${step.step}-${step.reason_code}`}>
								Step {step.step}: {step.reason_code}. {step.suggested_next_action}
							</li>
						))}
					</ul>
				</div>
			))}
		</div>
	);
};

const renderRunResults = (runResult) => {
	if (!runResult) {
		return null;
	}
	const summary = runResult.summary || {};
	const resultMessage = `Execution ${runResult.status || "finished"}: ${summary.passed || 0} passed, ${summary.failed || 0} failed, ${summary.invalid || 0} invalid.`;
	const directReportPaths = Array.isArray(runResult.playwright_report_paths) ? runResult.playwright_report_paths : [];
	const resultReportPaths = Array.isArray(runResult.results)
		? runResult.results.map((result) => result?.playwright_report_path).filter(Boolean)
		: [];
	const reportPaths = [...new Set([...directReportPaths, ...resultReportPaths])];

	return (
		<div className="result-section">
			<div className="generate-results-header">
				<div>
					<h3>Execution Results</h3>
					<p>Run {runResult.run_id}</p>
				</div>
				<span className={`review-banner ${runResult.status === "passed" ? "review-approved" : "review-needs-work"}`}>
					{runResult.status}
				</span>
			</div>
			<div className={`workflow-result-notice ${runResult.status === "passed" ? "success" : "warning"}`} role="status">
				<p>{resultMessage}</p>
			</div>
			<div className="workflow-diagnostics-pills">
				{renderBucketCount("Passed", summary.passed, "success")}
				{renderBucketCount("Failed", summary.failed, "warning")}
				{renderBucketCount("Invalid", summary.invalid, "warning")}
				{renderBucketCount("Skipped", summary.skipped)}
			</div>
			{runResult.artifacts_root && (
				<p className="helper-text">
					Artifacts root: <code>{runResult.artifacts_root}</code>
				</p>
			)}
			{reportPaths.length > 0 && (
				<p className="helper-text">
					Consolidated report: <code>{reportPaths[0]}</code>
					{reportPaths.length > 1 ? ` (+ ${reportPaths.length - 1} more)` : ""}
				</p>
			)}
			{runResult.results?.length > 0 && (
				<div className="selection-table-wrapper">
					<table className="selection-table">
						<thead>
							<tr>
								<th>Case</th>
								<th>Status</th>
								<th>Generated spec</th>
								<th>Artifacts</th>
							</tr>
						</thead>
						<tbody>
							{runResult.results.map((result) => (
								<tr key={result.id}>
									<td>
										<strong>{result.source_test_case_id}</strong>
									</td>
									<td>{result.status}</td>
									<td>{result.generated_spec_path ? <code>{result.generated_spec_path}</code> : "None"}</td>
									<td>{result.artifacts_dir ? <code>{result.artifacts_dir}</code> : "None"}</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}
		</div>
	);
};

export default function AutomationPanel({
	testCases,
	executionTargetBaseUrl,
	setExecutionTargetBaseUrl,
	executionTargetEnvironment,
	setExecutionTargetEnvironment,
	executionPreview,
	executionRunResult,
	isPreviewingExecution,
	isRunningExecution,
	authActionDisabled,
	previewExecution,
	runApprovedExecution,
	goPrev,
	goNext,
}) {
	const previewSummary = executionPreview?.summary || {};
	const executableCount = previewSummary.executable || 0;
	const previewDisabled = !testCases.length || isPreviewingExecution || isRunningExecution || authActionDisabled;
	const runDisabled = previewDisabled || executableCount === 0;

	return (
		<section className="panel">
			<h2 className="panel-title">Automation</h2>
			<p className="panel-description">Review executable candidates and run approved browser cases through Playwright.</p>
			<div className="panel-form two-cols">
				<div className="form-group">
					<label>Target environment</label>
					<input
						value={executionTargetEnvironment}
						onChange={(event) => setExecutionTargetEnvironment(event.target.value)}
						placeholder="staging, dev, customer-a"
					/>
				</div>
				<div className="form-group">
					<label>Target base URL</label>
					<input
						value={executionTargetBaseUrl}
						onChange={(event) => setExecutionTargetBaseUrl(event.target.value)}
						placeholder="Use backend default"
					/>
				</div>
				<div className="feedback-actions">
					<button className="secondary" onClick={() => previewExecution()} disabled={previewDisabled}>
						{isPreviewingExecution ? "Previewing..." : "Preview Execution"}
					</button>
					<button onClick={runApprovedExecution} disabled={runDisabled}>
						{isRunningExecution ? "Running..." : `Run ${executableCount || 0} Candidate${executableCount === 1 ? "" : "s"}`}
					</button>
				</div>
			</div>

			{executionPreview ? (
				<div className="generate-results-workspace">
					<div className="generate-results-header">
						<div>
							<h3>Execution Preview</h3>
							<p>
								{testCases.length} generated test case{testCases.length === 1 ? "" : "s"} reviewed for execution.
							</p>
						</div>
					</div>
					<div className="workflow-diagnostics-pills">
						{renderBucketCount("Executable", previewSummary.executable, "success")}
						{renderBucketCount("Manual", previewSummary.manual)}
						{renderBucketCount("Unsupported", previewSummary.unsupported, "warning")}
						{renderBucketCount("Invalid", previewSummary.invalid, "warning")}
					</div>

					<div className="result-section">
						<h3>Executable</h3>
						{renderCandidateTable(executionPreview.executable || [])}
					</div>

					<div className="result-section">
						<h3>Manual</h3>
						{renderManualList(executionPreview.manual || [])}
					</div>

					<div className="result-section">
						<h3>Unsupported</h3>
						{renderUnsupportedList([...(executionPreview.unsupported || []), ...(executionPreview.invalid || [])])}
					</div>

					{executionPreview.warnings?.length > 0 && (
						<ul className="jira-sync-warning-list">
							{executionPreview.warnings.map((warning) => (
								<li key={warning}>{warning}</li>
							))}
						</ul>
					)}
				</div>
			) : (
				<div className="result-section">
					<h3>Execution Preview</h3>
					<span className="helper-text">Generate test cases to preview automation readiness.</span>
				</div>
			)}

			{renderRunResults(executionRunResult)}

			<div className="panel-nav">
				<button onClick={goPrev} className="secondary">
					Back
				</button>
				<button onClick={goNext} disabled={testCases.length === 0}>
					Next
				</button>
			</div>
		</section>
	);
}
