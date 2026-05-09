const EXPORT_FORMATS = [
	{ format: "csv", className: "csv", icon: "📄", label: "CSV", description: "Excel compatible" },
	{ format: "excel", className: "excel", icon: "📊", label: "Excel", description: "Formatted .xlsx" },
	{ format: "json", className: "json", icon: "🧾", label: "JSON", description: "API/Import ready" },
];

export default function ExportPanel({
	testCases,
	isExporting,
	authActionDisabled,
	exportToFormat,
	goPrev,
}) {
	return (
		<section className="panel">
			<h2 className="panel-title">Export Test Cases</h2>
			<p className="panel-description">
				Download your generated test cases as CSV, Excel, or JSON.
			</p>
			<div className="export-section">
				<h3 className="section-subtitle">📥 Quick Export</h3>
				<p className="helper-text">Download test cases directly to your computer.</p>
				<div className="export-buttons">
					{EXPORT_FORMATS.map((item) => (
						<button
							key={item.format}
							className={`export-btn ${item.className}`}
							onClick={() => exportToFormat(item.format)}
							disabled={testCases.length === 0 || isExporting || authActionDisabled}
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
