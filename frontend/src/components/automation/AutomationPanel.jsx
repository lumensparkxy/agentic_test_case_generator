import { TableScroll, Table, List, ListItem } from "../ui/collections";
import { Checkbox, Field, Input, Button } from "../ui/controls";
import { Surface, Alert } from "../ui/surfaces";
const renderBucketCount = (label, value, tone = "") => (
	<span className={`workflow-diagnostics-pill ${tone}`.trim()}>
		{label} {value || 0}
	</span>
);

const renderCandidateTable = ({ candidates, selectedCandidateIds, setSelectedCandidateIds, selectionDisabled }) => {
	if (!candidates.length) {
		return <span className="helper-text">Preview completed with zero executable candidates.</span>;
	}
	const selectedIds = new Set(selectedCandidateIds);
	const selectedCount = candidates.filter((candidate) => selectedIds.has(candidate.id)).length;
	const allSelected = selectedCount === candidates.length;
	const selectionIsMixed = selectedCount > 0 && !allSelected;
	const sourceIdCounts = candidates.reduce((counts, candidate) => {
		counts.set(candidate.source_test_case_id, (counts.get(candidate.source_test_case_id) || 0) + 1);
		return counts;
	}, new Map());
	const updateCandidateSelection = (candidateId, checked) => {
		setSelectedCandidateIds((currentIds) => {
			const nextIds = new Set(currentIds);
			if (checked) {
				nextIds.add(candidateId);
			} else {
				nextIds.delete(candidateId);
			}
			return [...nextIds];
		});
	};

	return (
		<TableScroll className="selection-table-wrapper" role="region" aria-label="Executable automation candidates table" tabIndex={0}>
			<Table className="selection-table">
				<thead>
					<tr>
						<th scope="col">
							<Checkbox
								ref={(input) => {
									if (input) input.indeterminate = selectionIsMixed;
								}}
								type="checkbox"
								aria-label="Select all executable candidates"
								aria-checked={selectionIsMixed ? "mixed" : allSelected}
								checked={allSelected}
								disabled={selectionDisabled}
								onChange={(event) => setSelectedCandidateIds(event.target.checked ? candidates.map((candidate) => candidate.id) : [])}
							/>
						</th>
						<th scope="col">Case</th>
						<th scope="col">Title</th>
						<th scope="col">Spec</th>
						<th scope="col">Traceability</th>
					</tr>
				</thead>
				<tbody>
					{candidates.map((candidate) => {
						const selectionLabel =
							sourceIdCounts.get(candidate.source_test_case_id) > 1
								? `Select ${candidate.source_test_case_id} candidate ${candidate.id} for execution`
								: `Select ${candidate.source_test_case_id} for execution`;
						return (
							<tr key={candidate.id}>
								<td>
									<Checkbox
										type="checkbox"
										aria-label={selectionLabel}
										checked={selectedIds.has(candidate.id)}
										disabled={selectionDisabled}
										onChange={(event) => updateCandidateSelection(candidate.id, event.target.checked)}
									/>
								</td>
								<td>
									<strong>{candidate.source_test_case_id}</strong>
								</td>
								<td>{candidate.title}</td>
								<td>
									{candidate.spec?.steps?.length || 0} step{(candidate.spec?.steps?.length || 0) === 1 ? "" : "s"}
								</td>
								<td>{candidate.traceability_ids?.join(", ") || "None"}</td>
							</tr>
						);
					})}
				</tbody>
			</Table>
		</TableScroll>
	);
};

const renderManualList = (candidates) => {
	if (!candidates.length) {
		return <span className="helper-text">No manual cases in the current preview.</span>;
	}

	return (
		<List className="jira-sync-apply-list">
			{candidates.map((candidate) => (
				<ListItem key={candidate.id}>
					<strong>{candidate.source_test_case_id}</strong> - {candidate.title}
					{candidate.review_reasons?.length ? `: ${candidate.review_reasons[0]}` : ""}
				</ListItem>
			))}
		</List>
	);
};

const renderUnsupportedList = (candidates, emptyMessage = "No unsupported cases in the current preview.") => {
	if (!candidates.length) {
		return <span className="helper-text">{emptyMessage}</span>;
	}

	return (
		<List as="div" variant="grouped" className="jira-sync-preview-list">
			{candidates.map((candidate) => (
				<div key={candidate.id} className="jira-sync-preview-card conflict">
					<div className="jira-sync-preview-header">
						<div>
							<strong>{candidate.source_test_case_id}</strong>
							<span>{candidate.title}</span>
						</div>
						<span className="jira-status-badge conflict">{candidate.status}</span>
					</div>
					<List className="jira-sync-warning-list">
						{candidate.unsupported_steps?.map((step) => (
							<ListItem key={`${candidate.id}-${step.step}-${step.reason_code}`}>
								Step {step.step}: {step.reason_code}. {step.suggested_next_action}
							</ListItem>
						))}
					</List>
				</div>
			))}
		</List>
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
		<Surface as="div" className="result-section">
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
				<TableScroll className="selection-table-wrapper" role="region" aria-label="Execution results table" tabIndex={0}>
					<Table className="selection-table">
						<thead>
							<tr>
								<th scope="col">Case</th>
								<th scope="col">Status</th>
								<th scope="col">Generated spec</th>
								<th scope="col">Artifacts</th>
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
					</Table>
				</TableScroll>
			)}
		</Surface>
	);
};

export default function AutomationPanel({
	testCases,
	executionTargetBaseUrl,
	setExecutionTargetBaseUrl,
	executionTargetEnvironment,
	setExecutionTargetEnvironment,
	executionPreview,
	selectedExecutionCandidateIds,
	setSelectedExecutionCandidateIds,
	executionRunResult,
	executionError,
	isPreviewingExecution,
	isRunningExecution,
	authActionDisabled,
	previewExecution,
	runApprovedExecution,
	goPrev,
	goNext,
}) {
	const previewSummary = executionPreview?.summary || {};
	const executableCandidates = executionPreview?.executable || [];
	const actualCandidateIds = new Set(executableCandidates.map((candidate) => candidate.id));
	const selectedExecutableCount = selectedExecutionCandidateIds.filter((candidateId) => actualCandidateIds.has(candidateId)).length;
	const previewIsActionable = executionPreview?.isConsistent === true && !executionPreview?.requiresRefresh;
	const previewDisabled = !testCases.length || isPreviewingExecution || isRunningExecution || authActionDisabled;
	const runDisabled = previewDisabled || !previewIsActionable || selectedExecutableCount === 0;
	const inputsDisabled = isPreviewingExecution || isRunningExecution || authActionDisabled;

	return (
		<Surface as="section" className="panel" aria-busy={isPreviewingExecution || isRunningExecution || undefined}>
			<h2 className="panel-title">Execution setup</h2>
			<p className="panel-description">Review executable candidates and run approved browser cases through Playwright.</p>
			<div className="panel-form two-cols">
				<Field className="form-group">
					<label htmlFor="automation-target-environment">Target environment</label>
					<Input
						id="automation-target-environment"
						value={executionTargetEnvironment}
						onChange={(event) => setExecutionTargetEnvironment(event.target.value)}
						placeholder="staging, dev, customer-a"
						disabled={inputsDisabled}
					/>
				</Field>
				<Field className="form-group">
					<label htmlFor="automation-target-base-url">Target base URL</label>
					<Input
						id="automation-target-base-url"
						value={executionTargetBaseUrl}
						onChange={(event) => setExecutionTargetBaseUrl(event.target.value)}
						placeholder="Use backend default"
						disabled={inputsDisabled}
					/>
				</Field>
				<div className="feedback-actions">
					<Button className="secondary" onClick={() => previewExecution()} disabled={previewDisabled}>
						{isPreviewingExecution ? "Previewing..." : "Preview Execution"}
					</Button>
					<Button onClick={runApprovedExecution} disabled={runDisabled}>
						{isRunningExecution ? "Running..." : `Run ${selectedExecutableCount} Candidate${selectedExecutableCount === 1 ? "" : "s"}`}
					</Button>
				</div>
			</div>
			{executionError ? (
				<Alert as="div" tone="warning" className="workflow-result-notice warning" role="alert">
					<p>{executionError}</p>
				</Alert>
			) : null}

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
					{!previewIsActionable && (
						<Alert as="div" tone="warning" className="workflow-result-notice warning" role="alert">
							<p>{executionPreview.consistencyMessage || "This stored preview must be refreshed before execution."}</p>
						</Alert>
					)}

					<Surface as="div" className="result-section">
						<h3>Executable</h3>
						{renderCandidateTable({
							candidates: executableCandidates,
							selectedCandidateIds: selectedExecutionCandidateIds,
							setSelectedCandidateIds: setSelectedExecutionCandidateIds,
							selectionDisabled: !previewIsActionable || isPreviewingExecution || isRunningExecution || authActionDisabled,
						})}
					</Surface>

					<Surface as="div" className="result-section">
						<h3>Manual</h3>
						{renderManualList(executionPreview.manual || [])}
					</Surface>

					<Surface as="div" className="result-section">
						<h3>Unsupported</h3>
						{renderUnsupportedList(executionPreview.unsupported || [])}
					</Surface>

					<Surface as="div" className="result-section">
						<h3>Invalid</h3>
						{renderUnsupportedList(executionPreview.invalid || [], "No invalid cases in the current preview.")}
					</Surface>

					{executionPreview.warnings?.length > 0 && (
						<List className="jira-sync-warning-list">
							{executionPreview.warnings.map((warning) => (
								<ListItem key={warning}>{warning}</ListItem>
							))}
						</List>
					)}
				</div>
			) : (
				<Surface as="div" className="result-section">
					<h3>Execution Preview</h3>
					<span className="helper-text">
						{testCases.length
							? "No preview yet. Preview execution readiness for the current test cases."
							: "Generate test cases to preview automation readiness."}
					</span>
				</Surface>
			)}

			{renderRunResults(executionRunResult)}

			<div className="panel-nav">
				<Button onClick={goPrev} className="secondary">
					Back
				</Button>
				<Button onClick={goNext} disabled={testCases.length === 0}>
					Next
				</Button>
			</div>
		</Surface>
	);
}
