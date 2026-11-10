import ExecutionReport from "./ExecutionReport";
import SavedFileReport from "./SavedFileReport";
import RouteLink from "../../app/RouteLink";
import { buildSavedFileReportPath, getSavedFileReportId } from "../../app/savedFileReportLinks";
import ExecutionSetupSummary from "../generation/ExecutionSetupSummary";
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
	currentSuiteSnapshotId,
	suiteQualitySummary,
	declaredSetup,
	reviewedDesignAvailable,
	currentProject,
	accountId,
	savedFileReportLink,
	location,
	navigate,
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
	backToTestsPath,
}) {
	const projectId = currentProject?.project_id;
	if (savedFileReportLink && projectId) {
		return (
			<SavedFileReport
				key={JSON.stringify([accountId, projectId, savedFileReportLink.reportId])}
				accountId={accountId}
				projectId={projectId}
				reportId={savedFileReportLink.reportId}
				location={location}
				request={request}
				navigate={navigate}
			/>
		);
	}
	const runIds = new URLSearchParams(location.search).getAll("run");
	const runRecordId = runIds.length === 1 ? runIds[0] : "";
	const exportDisabled = testCases.length === 0 || isExporting || authActionDisabled || exportGateLocked;
	const executionReport = (
		<ExecutionReport
			currentProject={currentProject}
			accountId={accountId}
			executionRunResult={executionRunResult}
			currentSuiteSnapshotId={currentSuiteSnapshotId}
			runRecordId={runRecordId}
			request={request}
			location={location}
			navigate={navigate}
		/>
	);

	if (runIds.length > 0) {
		return (
			<Surface as="section" className="panel">
				<h2 className="panel-title">Saved execution</h2>
				{runRecordId ? executionReport : <p role="status">This saved execution is unavailable.</p>}
			</Surface>
		);
	}
	const reportSnapshot = currentProject?.current_snapshots?.reports;
	const latestFileReportId = getSavedFileReportId(reportSnapshot?.operation, reportSnapshot?.snapshot_id);

	return (
		<Surface as="section" className="panel">
			<h2 className="panel-title">Export Test Cases</h2>
			{projectId && latestFileReportId && (
				<p>
					<RouteLink to={buildSavedFileReportPath(projectId, latestFileReportId)} navigate={navigate}>
						Open latest saved file report
					</RouteLink>
				</p>
			)}
			{suiteQualitySummary}
			{testCases.length > 0 && (
				<div className={`export-readiness-card ${exportRequiresOverride ? "locked" : exportReviewApproved ? "approved" : ""}`}>
					<div>
						{reviewedDesignAvailable ? (
							<h3 className="section-subtitle">Reviewed design ready to export</h3>
						) : (
							<strong>
								{exportRequiresOverride ? "Export locked by review gate" : exportReviewApproved ? "Approved for export" : "Ready to export"}
							</strong>
						)}
						<p>
							{reviewedDesignAvailable
								? "Download the saved suite with its design assessment, source evidence, and separate browser setup status."
								: exportRequiresOverride
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
			<div className="export-section">
				<h3 className="section-subtitle">{reviewedDesignAvailable ? "Export reviewed design" : "📥 Quick Export"}</h3>
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
							variant="secondary"
							className={`export-btn ${item.className}`}
							onClick={() => exportToFormat(item.format)}
							disabled={exportDisabled}
						>
							<span className="export-icon" aria-hidden="true">
								{item.icon}
							</span>
							<span className="export-label">{reviewedDesignAvailable ? `Export reviewed design as ${item.label}` : item.label}</span>
							<span className="export-desc">{item.description}</span>
						</Button>
					))}
				</div>
				<p className="helper-text">Filenames include the project name and export time in UTC.</p>
			</div>
			{executionReport}
			{declaredSetup && <ExecutionSetupSummary declaredSetup={declaredSetup} projectId={projectId} navigate={navigate} />}
			<div className="panel-nav">
				<Button onClick={backToTestsPath ? () => navigate(backToTestsPath) : goPrev} className="secondary">
					{backToTestsPath ? "Back to tests" : "Back"}
				</Button>
			</div>
		</Surface>
	);
}
