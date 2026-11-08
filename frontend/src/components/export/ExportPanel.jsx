import ExecutionReport from "./ExecutionReport";
import { Surface } from "../ui/surfaces";
import { Checkbox, Textarea, Button } from "../ui/controls";
const EXPORT_FORMATS = [
	{ format: "csv", className: "csv", icon: "📄", label: "CSV", description: "Excel compatible" },
	{ format: "excel", className: "excel", icon: "📊", label: "Excel", description: "Formatted .xlsx" },
	{ format: "json", className: "json", icon: "🧾", label: "JSON", description: "API/Import ready" },
];

export default function ExportPanel({
	testCases,
	testCaseReview,
	executionRunResult,
	currentProject,
	runRecordId,
	request,
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
	exportError,
	goPrev,
}) {
	const exportDisabled = testCases.length === 0 || isExporting || authActionDisabled || exportGateLocked;
	const executionReport = (
		<ExecutionReport currentProject={currentProject} executionRunResult={executionRunResult} runRecordId={runRecordId} request={request} />
	);

	if (runRecordId) {
		return (
			<Surface as="section" className="panel">
				<h2 className="panel-title">Saved execution</h2>
				{executionReport}
			</Surface>
		);
	}

	return (
		<Surface as="section" className="panel">
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
								? testCaseReview?.approved
									? "The suite is incomplete or its saved approval is unavailable. Provide a reason to export this draft."
									: testCaseReview?.summary || "No review decision is available. Provide a reason to export this draft."
								: exportReviewApproved
									? testCaseReview?.summary || "The latest generated test cases passed the review gate."
									: "No review decision is available for this export."}
						</p>
					</div>
					{exportRequiresOverride && (
						<div className="draft-export-override">
							<label className="draft-export-toggle">
								<Checkbox
									type="checkbox"
									checked={draftExportOverrideRequested}
									onChange={(event) => setDraftExportOverrideRequested(event.target.checked)}
								/>
								<span>Export draft anyway</span>
							</label>
							{draftExportOverrideRequested && (
								<Textarea
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
			{executionReport}
			<div className="export-section">
				<h3 className="section-subtitle">📥 Quick Export</h3>
				<p className="helper-text">
					Download test cases directly to your computer. Filenames include the project name and export date/time in UTC.
				</p>
				{exportError && (
					<div className="workflow-result-notice warning" role="alert">
						<p>{exportError}</p>
					</div>
				)}
				{exportMessage && (
					<div className="workflow-result-notice success" role="status">
						<p>{exportMessage}</p>
					</div>
				)}
				<div className="export-buttons">
					{EXPORT_FORMATS.map((item) => (
						<Button
							key={item.format}
							className={`export-btn ${item.className}`}
							onClick={() => exportToFormat(item.format)}
							disabled={exportDisabled}
						>
							<span className="export-icon">{item.icon}</span>
							<span className="export-label">{item.label}</span>
							<span className="export-desc">{item.description}</span>
						</Button>
					))}
				</div>
			</div>
			<div className="panel-nav">
				<Button onClick={goPrev} className="secondary">
					Back
				</Button>
			</div>
		</Surface>
	);
}
