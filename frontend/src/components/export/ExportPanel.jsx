const EXPORT_FORMATS = [
	{ format: "csv", className: "csv", icon: "📄", label: "CSV", description: "Excel compatible" },
	{ format: "excel", className: "excel", icon: "📊", label: "Excel", description: "Formatted .xlsx" },
	{ format: "json", className: "json", icon: "🧾", label: "JSON", description: "API/Import ready" },
];

export default function ExportPanel({
	testCases,
	testCaseReview,
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
	goPrev,
}) {
	const exportDisabled = testCases.length === 0 || isExporting || authActionDisabled || exportGateLocked;

	return (
		<section className="panel">
			<h2 className="panel-title">Export Test Cases</h2>
			<p className="panel-description">
				Download your generated test cases as CSV, Excel, or JSON.
			</p>
			{testCases.length > 0 && (
				<div className={`export-readiness-card ${exportRequiresOverride ? "locked" : exportReviewApproved ? "approved" : ""}`}>
					<div>
						<strong>{exportRequiresOverride ? "Export locked by review gate" : exportReviewApproved ? "Approved for export" : "Ready to export"}</strong>
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
			<div className="export-section">
				<h3 className="section-subtitle">📥 Quick Export</h3>
				<p className="helper-text">Download test cases directly to your computer.</p>
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
				<button onClick={goPrev} className="secondary">Back</button>
			</div>
		</section>
	);
}
