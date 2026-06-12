export default function ContextInputsPanel({
	appLink,
	setAppLink,
	prototypeLink,
	setPrototypeLink,
	diagramLinks,
	setDiagramLinks,
	imageLinks,
	setImageLinks,
	hasContextInputs,
	analyzeContext,
	isAnalyzingContext,
	authActionDisabled,
	enrichedContext,
	resetContextAnalysis,
	selectedArtifactSourceIds,
	setSelectedArtifactSourceIds,
	goPrev,
	goNext,
}) {
	return (
		<section className="panel">
			<h2 className="panel-title">Context Inputs</h2>
			<p className="panel-description">Add links and references to enrich the test case generation context.</p>
			<div className="panel-form two-cols">
				<div className="form-group">
					<label>Application link</label>
					<input placeholder="https://your-app" value={appLink} onChange={(e) => setAppLink(e.target.value)} />
				</div>
				<div className="form-group">
					<label>Prototype link</label>
					<input placeholder="https://prototype" value={prototypeLink} onChange={(e) => setPrototypeLink(e.target.value)} />
				</div>
				<div className="form-group">
					<label>Diagram links</label>
					<input placeholder="Link1; Link2" value={diagramLinks} onChange={(e) => setDiagramLinks(e.target.value)} />
				</div>
				<div className="form-group">
					<label>Image links</label>
					<input placeholder="Link1; Link2" value={imageLinks} onChange={(e) => setImageLinks(e.target.value)} />
				</div>
			</div>
			{hasContextInputs && (
				<div className="panel-form button-row">
					<button onClick={analyzeContext} disabled={isAnalyzingContext || authActionDisabled}>
						{isAnalyzingContext ? "⏳ Analyzing..." : "Analyze Context"}
					</button>
					{enrichedContext && (
						<button className="secondary" onClick={resetContextAnalysis}>
							Clear Analysis
						</button>
					)}
				</div>
			)}
			{enrichedContext?.grounded_context && (
				<div className="result-section">
					<h3>Grounded Context</h3>
					{(enrichedContext.grounded_context.artifact_sources || []).length > 0 && (
						<div className="artifact-sources">
							<h4>Artifact Sources</h4>
							<ul className="artifact-source-list">
								{enrichedContext.grounded_context.artifact_sources.map((source) => (
									<li key={source.id} className="artifact-source-item">
										<label>
											<input
												type="checkbox"
												checked={selectedArtifactSourceIds.includes(source.id)}
												onChange={(e) => {
													setSelectedArtifactSourceIds((prev) =>
														e.target.checked ? [...prev, source.id] : prev.filter((id) => id !== source.id)
													);
												}}
											/>
											<span>{source.url || source.id}</span>
											{source.type && <span className="artifact-type">{source.type}</span>}
										</label>
									</li>
								))}
							</ul>
						</div>
					)}
					<div className="analysis-detail-grid">
						{(enrichedContext.grounded_context.ui_elements || []).length > 0 && (
							<div className="analysis-detail-block">
								<h4>UI Elements</h4>
								<ul className="analysis-detail-list">
									{enrichedContext.grounded_context.ui_elements.slice(0, 6).map((el) => (
										<li key={el.id}>
											{el.element_type}: {el.label || el.id}
										</li>
									))}
								</ul>
							</div>
						)}
						{(enrichedContext.grounded_context.workflows || []).length > 0 && (
							<div className="analysis-detail-block">
								<h4>Workflows</h4>
								<ul className="analysis-detail-list">
									{enrichedContext.grounded_context.workflows.slice(0, 4).map((workflow) => (
										<li key={workflow.id}>
											{workflow.name}: {(workflow.transitions || []).join(", ") || workflow.description}
										</li>
									))}
								</ul>
							</div>
						)}
					</div>
				</div>
			)}
			<div className="panel-nav">
				<button onClick={goPrev} className="secondary">
					Back
				</button>
				<button onClick={goNext}>Next</button>
			</div>
		</section>
	);
}
