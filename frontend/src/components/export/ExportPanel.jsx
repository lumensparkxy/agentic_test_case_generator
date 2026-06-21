const EXPORT_FORMATS = [
	{ format: "csv", className: "csv", icon: "📄", label: "CSV", description: "Excel compatible" },
	{ format: "excel", className: "excel", icon: "📊", label: "Excel", description: "Formatted .xlsx" },
	{ format: "json", className: "json", icon: "🧾", label: "JSON", description: "API/Import ready" },
];

const REPORT_PATH_LIMIT = 8;

function latestExecutionReport(executionRunResult, currentProject) {
	if (executionRunResult?.run_id) {
		return executionRunResult;
	}
	const executionSnapshotPayload = currentProject?.current_snapshots?.execution?.payload;
	if (executionSnapshotPayload?.run_id) {
		return executionSnapshotPayload;
	}
	const projectRun = currentProject?.execution_runs?.[0];
	if (projectRun?.run_id) {
		return projectRun;
	}
	const reportSnapshotPayload = currentProject?.current_snapshots?.reports?.payload;
	return reportSnapshotPayload?.run_id ? reportSnapshotPayload : null;
}

function normalizeReportPaths(report) {
	const directPaths = Array.isArray(report?.playwright_report_paths) ? report.playwright_report_paths : [];
	const resultPaths = Array.isArray(report?.results) ? report.results.map((result) => result?.playwright_report_path).filter(Boolean) : [];
	return [...new Set([...directPaths, ...resultPaths])];
}

function renderSummaryPill(label, value, tone = "") {
	return (
		<span className={`workflow-diagnostics-pill ${tone}`.trim()}>
			{label} {value || 0}
		</span>
	);
}

function PlaywrightExecutionReport({ report }) {
	const summary = report?.summary || {};
	const reportPaths = normalizeReportPaths(report);
	const visibleReportPaths = reportPaths.slice(0, REPORT_PATH_LIMIT);
	const hiddenReportPathCount = Math.max(0, reportPaths.length - visibleReportPaths.length);

	return (
		<div className="export-section playwright-report-section">
			<h3 className="section-subtitle">Playwright Execution Report</h3>
			{report ? (
				<div className="playwright-report-card">
					<div className="playwright-report-header">
						<div>
							<strong>Run {report.run_id}</strong>
							<p>
								{report.target_environment || "default"} {report.target_base_url ? `• ${report.target_base_url}` : ""}
							</p>
						</div>
						<span className={`review-banner ${report.status === "passed" ? "review-approved" : "review-needs-work"}`}>
							{report.status || "recorded"}
						</span>
					</div>
					<div className="workflow-diagnostics-pills">
						{renderSummaryPill("Passed", summary.passed, "success")}
						{renderSummaryPill("Failed", summary.failed, "warning")}
						{renderSummaryPill("Invalid", summary.invalid, "warning")}
						{renderSummaryPill("Skipped", summary.skipped)}
					</div>
					{report.artifacts_root && (
						<p className="helper-text">
							Artifacts root: <code>{report.artifacts_root}</code>
						</p>
					)}
					{visibleReportPaths.length ? (
						<>
							<p className="helper-text">Consolidated report path:</p>
							<ul className="playwright-report-paths">
								{visibleReportPaths.map((path) => (
									<li key={path}>
										<code>{path}</code>
									</li>
								))}
							</ul>
						</>
					) : (
						<p className="helper-text">No Playwright report path was returned for this execution run.</p>
					)}
					{hiddenReportPathCount > 0 && <p className="helper-text">+ {hiddenReportPathCount} additional report paths</p>}
				</div>
			) : (
				<div className="playwright-report-card empty">
					<p>No Playwright execution report has been recorded yet.</p>
				</div>
			)}
		</div>
	);
}

export default function ExportPanel({
	testCases,
	testCaseReview,
	executionRunResult,
	currentProject,
	exportReviewApproved,
	exportRequiresOverride,
	exportGateLocked,
	draftExportOverrideRequested,
	setDraftExportOverrideRequested,
	draftExportOverrideReason,
	setDraftExportOverrideReason,
	isExporting,
	authActionDisabled,
	exportToFormat,
	exportMessage,
	goPrev,
}) {
	const exportDisabled = testCases.length === 0 || isExporting || authActionDisabled || exportGateLocked;
	const executionReport = latestExecutionReport(executionRunResult, currentProject);

	return (
		<section className="panel">
			<h2 className="panel-title">Export Test Cases</h2>
			<p className="panel-description">Download your generated test cases as CSV, Excel, or JSON.</p>
			{testCases.length > 0 && (
				<div className={`export-readiness-card ${exportRequiresOverride ? "locked" : exportReviewApproved ? "approved" : ""}`}>
					<div>
						<strong>
							{exportRequiresOverride ? "Export locked by review gate" : exportReviewApproved ? "Approved for export" : "Ready to export"}
						</strong>
						<p>
							{exportRequiresOverride
								? testCaseReview?.summary || "The latest generated test cases need review before export."
								: exportReviewApproved
									? testCaseReview?.summary || "The latest generated test cases passed the review gate."
									: "No review decision is available for this export."}
						</p>
					</div>
					{exportRequiresOverride && (
						<div className="draft-export-override">
							<label className="draft-export-toggle">
								<input
									type="checkbox"
									checked={draftExportOverrideRequested}
									onChange={(event) => setDraftExportOverrideRequested(event.target.checked)}
								/>
								<span>Export draft anyway</span>
							</label>
							{draftExportOverrideRequested && (
								<textarea
									className="draft-export-reason"
									placeholder="Reason for exporting this draft"
									aria-label="Reason for exporting this draft"
									value={draftExportOverrideReason}
									onChange={(event) => setDraftExportOverrideReason(event.target.value)}
								/>
							)}
						</div>
					)}
				</div>
			)}
			<PlaywrightExecutionReport report={executionReport} />
			<div className="export-section">
				<h3 className="section-subtitle">📥 Quick Export</h3>
				<p className="helper-text">Download test cases directly to your computer.</p>
				{exportMessage && (
					<div className="workflow-result-notice success" role="status">
						<p>{exportMessage}</p>
					</div>
				)}
				<div className="export-buttons">
					{EXPORT_FORMATS.map((item) => (
						<button
							key={item.format}
							className={`export-btn ${item.className}`}
							onClick={() => exportToFormat(item.format)}
							disabled={exportDisabled}
						>
							<span className="export-icon">{item.icon}</span>
							<span className="export-label">{item.label}</span>
							<span className="export-desc">{item.description}</span>
						</button>
					))}
				</div>
			</div>
			<div className="panel-nav">
				<button onClick={goPrev} className="secondary">
					Back
				</button>
			</div>
		</section>
	);
}
